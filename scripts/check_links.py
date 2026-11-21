"""检查站点上个人主页链接是否已失效。
教授：复用 data/cache/people_pages 的抓取结果；外部人物：礼貌 GET（遵守 robots、同站 2 秒间隔、普通 UA、不绕过拦截）。

外部人物的主页按 build_graph.py 合并后的结果预测（people.json > 导师主页名单上的 h > student_links.json），
每个候选地址都查，dead_links.json 只记最终会显示的那个已失效的节点。
判为失效（dead）：404/410、403（非 Cloudflare 质询）、5xx（重试一次仍是）、DNS 失败、域名停放 / 出售页、
  跳转到根目录或 /people /faculty /directory 之类的名录首页、跳转后的地址里不再有原地址里的用户名 / 人名、
  meta refresh 指向的页面失效、学院 / 系里"个人页已停用"的通知页；
  名录 / 团队页（路径以 /people /team /students /members 结尾且标题不是本人名字）也记入（原因 not-personal），
  build_graph 会把它当失效处理、不再当个人主页显示。
需人工看（review，不算失效）：Cloudflare 质询页。
TLS 出错时依次试 https://www. / http:// / http://www. 变体，能打开就把可用地址写进 people.json / student_links.json，
  并在 link_updates.json 里记 {节点: {homepage: 新地址, was: 旧地址}}（enrich_people / match_people_links 重跑时沿用）。
领英：robots.txt 允许才查，只有明确 404 才算失效（999 等不算）。
缓存：data/cache/link_check/results_v2.jsonl（可续跑；旧版 results.jsonl 不含跳转目标和标题，不再使用）；
      data/cache/link_check/needs_review.json；data/cache/link_check/titles.json（page_title 的缓存）。
输出：data/enrich/dead_links.json {node_id: reason}；data/enrich/link_updates.json（见上）。
用法: python scripts/check_links.py [--recheck]   （--recheck 忽略缓存重新抓）
"""
import collections
import concurrent.futures as cf
import html
import json
import os
import re
import sys
import threading
import time
import warnings
from urllib.parse import unquote, urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_pages  # noqa: E402
from fetch_pages import allowed, polite_get  # noqa: E402
from names import display_name, fold  # noqa: E402
import requests  # noqa: E402
import urllib3  # noqa: E402

warnings.simplefilter('ignore', urllib3.exceptions.InsecureRequestWarning)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fetch_pages.MAX_BYTES = 64 * 1024  # 只读开头一段：够看标题、停放页文字和 meta refresh
CACHE_DIR = os.path.join(ROOT, 'data/cache/link_check')
CACHE = os.path.join(CACHE_DIR, 'results_v2.jsonl')
REVIEW = os.path.join(CACHE_DIR, 'needs_review.json')
TITLES = os.path.join(CACHE_DIR, 'titles.json')
OUT = os.path.join(ROOT, 'data/enrich/dead_links.json')
UPDATES = os.path.join(ROOT, 'data/enrich/link_updates.json')
PARK = ('sedoparking', 'sedo.com', 'godaddy.com', 'hugedomains', 'dan.com', 'afternic', 'parkingcrew',
        'bodis', 'namecheap.com', 'domainmarket', 'undeveloped', 'above.com', 'domainsponsor', 'squadhelp',
        'parklogic', 'domainnamesales', 'atom.com', 'spaceship.com')
PARK_HOST = re.compile(r'^ww\d+\.')  # 停放商常用的跳转子域名 ww1. / ww38.
PARK_TEXT = re.compile(r'domain\s+(?:name\s+)?(?:is\s+|may\s+be\s+|might\s+be\s+)?for\s+sale|buy\s+this\s+domain|'
                       r'this\s+domain\s+(?:is|has\s+been)\s+(?:parked|registered|for\s+sale)|parked\s+(?:free|domain|by)|'
                       r'domain\s+(?:has\s+)?expired|is\s+parked\s+free|make\s+an\s+offer\s+on\s+this\s+domain|'
                       r'the\s+domain\s+name\s+\S+\s+is\s+for\s+sale|inquire\s+about\s+this\s+domain', re.I)
GONE_TEXT = re.compile(r'(?:pages?|homepages?|site|web\s*space)\s+(?:are|is)\s+no\s+longer\s+(?:available|hosted)|'
                       r'no\s+longer\s+(?:available|exists?)\s+(?:on|at)\s+this\s+(?:server|site)|'
                       r'personal\s+(?:web\s*)?pages?\s+(?:have|has)\s+been\s+(?:removed|discontinued|retired)|'
                       r'this\s+(?:page|account|user)\s+(?:has\s+been|was)\s+(?:removed|deleted|deactivated|disabled)', re.I)
CF_TEXT = re.compile(r'<title>\s*(?:just a moment|attention required|checking your browser)|challenge-platform|cf-chl-|'
                     r'cf_chl_opt|enable javascript and cookies to continue', re.I)
# 名录 / 团队 / 学生列表页（规则 4）：路径最后一段是这些词（可带 .html / .php / index.html）
DIR_PATH = re.compile(r'/(?:people|team|our-team|the-team|students|members|lab-members|group-members|directory|faculty|'
                      r'staff|personnel|alumni)(?:\.s?html?|\.php|\.aspx?)?/?(?:index\.s?html?)?$', re.I)
# 跳转落到这些"名录首页"上算失效（原地址更深时）
LANDING = re.compile(r'^/?(?:[a-z]{2}/)?(?:people|faculty|directory|faculty-directory|staff|persons?|team|members|'
                     r'about/people|about/faculty|people/faculty|faculty-and-staff|faculty-staff|our-people|'
                     r'404|not-found|error)?(?:\.s?html?|\.php|\.aspx?)?/?$', re.I)
GENERIC_SEG = {'index', 'home', 'homepage', 'people', 'faculty', 'staff', 'users', 'user', 'homes', 'members', 'en', 'www',
               'about', 'pages', 'page', 'profile', 'profiles', 'person', 'directory', 'site', 'sites', 'view', 'research',
               'cs', 'ece', 'eecs', 'html', 'php', 'web', 'public', 'main', 'default', 'info', 'facultyinfo', 'aspx', 'htm'}
# 标题像名录 / 列表页（且不含本人姓）：不是个人主页
LISTING_TITLE = re.compile(r'\b(?:directory|people|faculty\s*(?:&|and)\s*staff|our\s+team|team\s+members|lab\s+members|'
                           r'group\s+members|students|members|personnel|staff\s+list|faculty\s+list)\b', re.I)
TITLE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
TAG_RE = re.compile(r'<script.*?</script>|<style.*?</style>|<[^>]+>', re.I | re.S)
META_REFRESH = re.compile(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\']?\s*\d*\s*;?\s*url\s*=\s*([^"\'>\s]+)', re.I)
lock = threading.Lock()


def norm_li(u):
    """领英统一成 https://www.linkedin.com/in/<slug>（去掉国家子域名、?originalSubdomain= 等参数、结尾斜杠）。"""
    if not u:
        return ''
    p = urlparse(u.strip())
    if p.netloc.lower().endswith('linkedin.com'):
        m = re.match(r'/in/([^/?#]+)', p.path)
    else:  # 导师页面上漏了 https:// 的相对链接：https://x.github.io/www.linkedin.com/in/slug
        m = re.search(r'linkedin\.com/in/([^/?#]+)', p.path)
    if not m:
        return u.strip()
    return 'https://www.linkedin.com/in/' + m.group(1)


def norm_url(u):
    """比较用：去协议、www.、结尾斜杠、index.html，主机小写。"""
    u = unquote((u or '').strip())
    p = urlparse(u if '://' in u else 'http://' + u)
    host = p.netloc.lower().removeprefix('www.')
    path = re.sub(r'/(?:index|default|home)\.(?:s?html?|php|aspx?)$', '/', p.path).rstrip('/')
    return host + path + ('?' + p.query if p.query else '')


def name_tokens(name):
    t = [x for x in fold(display_name(name or '')).split() if len(x) > 1 and x not in {'jr', 'sr', 'ii', 'iii'}]
    return (t[0], t[-1]) if len(t) >= 2 else (tuple(t) if t else ())


def title_is_person(title, name):
    """标题就是此人的名字：名和姓都出现，且标题不长（不是 "People | X Lab" 这类列表页）。"""
    toks = name_tokens(name)
    if not title or len(toks) < 2:
        return False
    words = set(fold(html.unescape(title)).split())
    return toks[0] in words and toks[-1] in words


def is_dir_path(url):
    p = urlparse(url.strip())
    return not p.query and bool(DIR_PATH.search(p.path or ''))  # 带查询参数的（?user=xx、?id=）多半是个人页


def same_person(a, b):
    """两个名字是不是同一个人（只用来判断"一个主页对应几个不同的人"，宁可当成同一人）：
    有共同的姓 / 中间名（长度 >= 3，且不只是两人的名相同），或一个是另一个的前缀（Fayaz / Fayazbakhsh）、
    拼写几乎一样（Satyanarayan / Satyanaryan）。"""
    import difflib
    ta = [x for x in fold(display_name(a)).split() if x not in {'jr', 'sr', 'ii', 'iii', 'vii'}]
    tb = [x for x in fold(display_name(b)).split() if x not in {'jr', 'sr', 'ii', 'iii', 'vii'}]
    if not ta or not tb:
        return fold(a) == fold(b)
    for x in ta:
        for y in tb:
            if len(x) < 3 or len(y) < 3 or (x == ta[0] and y == tb[0]):
                continue
            pre = x.startswith(y) or y.startswith(x)
            if x == y or (pre and (min(len(x), len(y)) >= 5 or abs(len(x) - len(y)) <= 1)) or \
                    (min(len(x), len(y)) >= 5 and difflib.SequenceMatcher(None, x, y).ratio() >= 0.8):
                return True
    return False


def distinct_people(names):
    """一组名字里是否有不同的人。"""
    names = list(names)
    return any(not same_person(names[0], x) for x in names[1:])


_titles, _titles_lock = None, threading.Lock()


def page_title(url):
    """礼貌抓取网页标题（遵守 robots、同站 2 秒），结果缓存。抓不到返回 None。"""
    global _titles
    with _titles_lock:
        if _titles is None:
            _titles = json.load(open(TITLES, encoding='utf-8')) if os.path.exists(TITLES) else {}
        if url in _titles:
            return _titles[url]
    t = None
    try:
        if allowed(url):
            r, body = polite_get(url)
            if r.status_code < 400:
                m = TITLE.search(body.decode(r.encoding or 'utf-8', errors='replace'))
                t = re.sub(r'\s+', ' ', html.unescape(m.group(1))).strip() if m else ''
    except Exception:
        t = None
    with _titles_lock:
        _titles[url] = t
        os.makedirs(CACHE_DIR, exist_ok=True)
        json.dump(_titles, open(TITLES, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    return t


_texts, _texts_lock = None, threading.Lock()
TEXTS = os.path.join(CACHE_DIR, 'page_text.json')


def page_text(url):
    """礼貌抓网页开头一段的纯文字和链接（遵守 robots、同站 2 秒），结果缓存。抓不到返回 ''。"""
    global _texts
    with _texts_lock:
        if _texts is None:
            _texts = json.load(open(TEXTS, encoding='utf-8')) if os.path.exists(TEXTS) else {}
        if url in _texts:
            return _texts[url]
    t = ''
    try:
        if allowed(url):
            r, body = polite_get(url)
            if r.status_code < 400:
                raw = body.decode(r.encoding or 'utf-8', errors='replace')
                hrefs = ' '.join(re.findall(r'''href=["']([^"']+)''', raw))
                t = (re.sub(r'\s+', ' ', html.unescape(TAG_RE.sub(' ', raw))) + ' ' + hrefs)[:40000]
    except Exception:
        t = ''
    with _texts_lock:
        _texts[url] = t
        os.makedirs(CACHE_DIR, exist_ok=True)
        json.dump(_texts, open(TEXTS, 'w', encoding='utf-8'), ensure_ascii=False)
    return t


def not_personal(url, name):
    """规则 4：名录 / 团队页路径，且标题不是本人名字 -> 不是个人主页。"""
    return is_dir_path(url) and not title_is_person(page_title(url) or '', name)


def ident_tokens(url, name):
    """原地址里能代表"这个人"的片段：人名（名 / 姓，出现在原地址里的）、~用户名、根路径时的主机名第一段。"""
    p = urlparse(url)
    low = unquote(url).lower()
    toks = {t for t in name_tokens(name) if len(t) > 2 and t in low}
    for seg in unquote(p.path).split('/'):
        s = seg.lower()
        if s.startswith('~') and len(s) > 2:
            toks.add(s[1:])
        elif s.startswith('%7e') and len(s) > 4:
            toks.add(s[3:])
    if not p.path.strip('/'):
        lab = p.netloc.lower().removeprefix('www.').split('.')[0]
        if len(lab) > 2:
            toks.add(lab)
    return {t for t in toks if t not in GENERIC_SEG}


def _get(url, verify=True, follow=True):
    if verify and follow:
        r, body = polite_get(url)
    else:  # 证书过期 / 不匹配时只读内容判断是不是停放页，不采用这个地址；同样遵守同站 2 秒间隔
        host = urlparse(url).netloc
        lk = fetch_pages._host_locks.setdefault(host, threading.Lock())
        with lk:
            wait = fetch_pages._last.get(host, 0) + fetch_pages.HOST_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                r = requests.get(url, headers={'User-Agent': fetch_pages.UA}, timeout=20, stream=True, verify=verify,
                                 allow_redirects=follow)
                try:
                    body = r.raw.read(fetch_pages.MAX_BYTES, decode_content=True)
                finally:
                    r.close()
            finally:
                fetch_pages._last[host] = time.time()
    return r, body.decode(r.encoding or 'utf-8', errors='replace') if body else ''


def parked_host(netloc):
    """停放商的跳转主机（如 ww38.example.me）或已知停放服务。"""
    n = netloc.lower()
    n = n.split(':')[0]
    return bool(PARK_HOST.match(n)) or any(n == k or n.endswith('.' + k) for k in PARK)


def _parked_insecure(url):
    """TLS 各变体都打不开：不验证证书读一次（https 和 http 各一次），只用来认停放 / 出售页，不采用这个地址。"""
    p = urlparse(url)
    for u in (url, 'http://' + url.split('://', 1)[1]) if p.scheme == 'https' else (url,):
        r = None
        for _ in range(5):  # 手动跟跳转：停放商的跳转目标（ww38.）自己的 TLS 也常是坏的
            try:
                r, text = _get(u, verify=False, follow=False)
            except Exception:
                r = None
                break
            loc = r.headers.get('location')
            if r.status_code in (301, 302, 303, 307, 308) and loc:
                u = urljoin(u, loc)
                if parked_host(urlparse(u).netloc):
                    return True
                continue
            break
        if r is None:
            continue
        f = urlparse(u)
        m = TITLE.search(text)
        title = m.group(1) if m else ''
        if parked_host(f.netloc) or PARK_TEXT.search(title) or PARK_TEXT.search(text[:30000]):
            return True
    return False


def classify(url, name, is_li=False, _depth=0):
    """返回 (状态, 原因, 最终地址, 标题)。状态: ok / dead / unknown / review / fixed（TLS 变体可用，原因里是新地址）。"""
    try:
        if not allowed(url):
            return 'unknown', 'robots', '', ''
        r, text = _get(url)
    except requests.exceptions.SSLError:
        if is_li or _depth:
            return 'unknown', 'tls', '', ''
        p = urlparse(url)
        host = p.netloc.removeprefix('www.')
        rest = url.split(p.netloc, 1)[1]
        for v in (f'https://www.{host}{rest}', f'http://{host}{rest}', f'http://www.{host}{rest}'):
            if v.rstrip('/') == url.rstrip('/'):
                continue
            st, why, fin, ti = classify(v, name, _depth=1)
            if st == 'ok':
                return 'fixed', v, fin, ti
            if st == 'dead':
                return st, why, fin, ti
        if _parked_insecure(url):
            return 'dead', 'parked', '', ''
        return 'unknown', 'tls', '', ''
    except requests.exceptions.ConnectionError as e:
        s = str(e)
        if any(k in s for k in ('getaddrinfo', 'Name or service', 'NameResolution', 'nodename')):
            return 'dead', 'dns', '', ''
        return 'unknown', 'connerr', '', ''
    except requests.RequestException as e:
        return 'unknown', type(e).__name__, '', ''
    except Exception as e:
        return 'unknown', type(e).__name__, '', ''
    c = r.status_code
    m = TITLE.search(text)
    title = re.sub(r'\s+', ' ', html.unescape(m.group(1))).strip()[:200] if m else ''
    if is_li:  # 领英：只认明确的 404
        return ('dead', '404', r.url, '') if c == 404 else ('ok' if c < 400 else 'unknown', str(c), r.url, '')
    if urlparse(r.url).netloc.lower().endswith('linkedin.com'):  # 自己的域名转到领英：算能用（领英 robots 不让查，999 不算失效）
        return 'ok', 'redirect-linkedin', r.url, title
    if r.history and not allowed(r.url):  # 跳转目标的 robots 不允许：不判断
        return 'unknown', 'robots-redirect', r.url, title
    if c == 999:
        return 'unknown', '999', r.url, title
    cf_challenge = r.headers.get('cf-mitigated', '').lower() == 'challenge' or bool(CF_TEXT.search(text[:20000]))
    if c in (404, 410):
        return 'dead', str(c), r.url, title
    if c in (403, 429, 503) and cf_challenge:
        return 'review', 'cloudflare-challenge', r.url, title
    if c == 403:
        return 'dead', '403', r.url, title
    if c >= 500:
        if _depth == 0:  # 偶发的服务器错误：重试一次
            st = classify(url, name, _depth=1)
            if st[0] != 'dead' or not st[1].startswith('5'):
                return st
        return 'dead', str(c), r.url, title
    if c >= 400:
        return 'unknown', str(c), r.url, title
    if cf_challenge:
        return 'review', 'cloudflare-challenge', r.url, title
    o, f = urlparse(url), urlparse(r.url)
    if parked_host(f.netloc):
        return 'dead', 'parked', r.url, title
    head = text[:30000]
    if PARK_TEXT.search(title) or (len(head) < 30000 and PARK_TEXT.search(head)):
        return 'dead', 'parked', r.url, title
    if GONE_TEXT.search(head) and not title_is_person(title, name):
        return 'dead', 'gone-notice', r.url, title
    mr = META_REFRESH.search(head)
    if mr and _depth < 2:
        nxt = urljoin(r.url, html.unescape(mr.group(1).strip('\'"')))
        if norm_url(nxt) != norm_url(r.url):
            st, why, fin, ti = classify(nxt, name, _depth=_depth + 1)
            if st == 'dead':
                return 'dead', 'meta-refresh-' + why, fin, ti
            if st in ('ok', 'review'):
                return st, why, fin, ti or title
    if norm_url(r.url) != norm_url(url):  # 跳转了
        orig_deep = len(o.path.strip('/')) > 0 or bool(o.query)
        if orig_deep and not f.query and LANDING.match(f.path or '/'):
            return 'dead', 'redirect-root' if not f.path.strip('/') else 'redirect-directory', r.url, title
        toks = ident_tokens(url, name)
        fin_low = unquote(r.url).lower()
        if toks and not any(t in fin_low for t in toks) and not title_is_person(title, name):
            return 'dead', 'redirect-lost-name', r.url, title
    if is_dir_path(r.url) and not title_is_person(title, name):
        return 'dead', 'not-personal', r.url, title
    toks = name_tokens(name)
    if title and toks and LISTING_TITLE.search(title) and toks[-1] not in fold(title).split() and \
            toks[-1] not in fold(TAG_RE.sub(' ', text)).split():  # 名录详情页（标题泛泛、正文有本人）不算
        return 'dead', 'not-personal', r.url, title
    return 'ok', str(c), r.url, title


def predicted_homepages(g, people, links):
    """build_graph 合并后外部节点会显示哪个主页：people.json > 导师主页名单上的 h > student_links.json。
    返回 {nid: (最终主页, 来源, 名字)} 和 {nid: [所有候选 (地址, 来源)]}。"""
    from enrich_people import advisor_page_fields
    own = advisor_page_fields(g)
    lu = json.load(open(UPDATES, encoding='utf-8')) if os.path.exists(UPDATES) else {}
    final, cands = {}, collections.defaultdict(list)
    for n in g['nodes']:
        if not n.get('x'):
            continue
        nid = n['id']
        if re.fullmatch(r'p\d+', nid):  # 已不在名单上的教授（former）：主页来自教授表 / link_updates
            if n.get('h'):
                cands[nid].append((n['h'], 'prof'))
                final[nid] = (n['h'], 'prof', n['n'])
            continue
        for src, u in (('people', people.get(nid, {}).get('h')), ('own', own.get(nid, {}).get('h')),
                       ('links', links.get(nid, {}).get('h'))):
            if u and u not in [c[0] for c in cands[nid]]:
                cands[nid].append((u, src))
        if cands[nid]:
            final[nid] = (*cands[nid][0], n['n'])
    return final, cands, lu


def main():
    recheck = '--recheck' in sys.argv
    g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf8'))
    people_path = os.path.join(ROOT, 'data/enrich/people.json')
    links_path = os.path.join(ROOT, 'data/enrich/student_links.json')
    people = json.load(open(people_path, encoding='utf8'))
    links = json.load(open(links_path, encoding='utf8'))
    dead = {}
    stat = collections.Counter()
    for n in g['nodes']:  # 教授
        if n.get('h') and not n.get('x'):
            try:
                err = json.load(open(os.path.join(ROOT, f'data/cache/people_pages/{n["id"]}.json'), encoding='utf8'))['pages'][0]['err']
            except Exception:
                continue
            stat['prof_' + (err or 'ok')] += 1
            if err in ('404', '410', 'ConnectionError'):
                dead[n['id']] = 'prof-' + err
    os.makedirs(CACHE_DIR, exist_ok=True)
    done = {}
    if os.path.exists(CACHE) and not recheck:
        for line in open(CACHE, encoding='utf8'):
            try:
                d = json.loads(line)
                done[d['u']] = d
            except Exception:
                pass
    final, cands, lu = predicted_homepages(g, people, links)
    names = {n['id']: n['n'] for n in g['nodes']}
    todo = {}
    for nid, cs in cands.items():
        for u, _ in cs:
            if u not in done and u not in todo:
                todo[u] = (names[nid], False)
    li_urls = {}
    for n in g['nodes']:
        if n.get('x'):
            li = norm_li(people.get(n['id'], {}).get('li') or links.get(n['id'], {}).get('li') or n.get('li') or '')
            if li:
                li_urls[n['id']] = li
                if li not in done and li not in todo:
                    todo[li] = (n['n'], True)
    byhost = collections.defaultdict(list)
    for u, v in todo.items():
        byhost[urlparse(u).netloc].append((u, v))
    order, queues = [], list(byhost.values())
    while queues:  # 按站点轮流交错
        nxt = []
        for q in queues:
            order.append(q.pop(0))
            if q:
                nxt.append(q)
        queues = nxt
    print('external homepages', len(final), 'candidate urls', sum(len(v) for v in cands.values()),
          'linkedin', len(li_urls), 'todo', len(order), flush=True)
    fh = open(CACHE, 'a', encoding='utf8')
    cnt = [0]

    def job(item):
        u, (name, is_li) = item
        st, why, fin, ti = classify(u, name, is_li)
        with lock:
            done[u] = {'u': u, 's': st, 'r': why, 'f': fin, 't': ti}
            fh.write(json.dumps(done[u], ensure_ascii=False) + '\n')
            fh.flush()
            cnt[0] += 1
            if cnt[0] % 500 == 0:
                print(cnt[0], flush=True)
    with cf.ThreadPoolExecutor(64) as ex:
        list(ex.map(job, order))
    fh.close()

    # TLS 变体可用：写回来源文件，并记到 link_updates.json
    fixes = 0
    for nid, cs in cands.items():
        for u, src in cs:
            d = done.get(u)
            if not d or d['s'] != 'fixed':
                continue
            new = d['r']
            if src == 'people' or src == 'own':
                people.setdefault(nid, {})['h'] = new
            elif src == 'links':
                links[nid]['h'] = new
            if src == 'prof':
                lu.setdefault(nid, {})['homepage'] = new
            else:
                lu[nid] = {'homepage': new, 'was': u}
            fixes += 1
    json.dump(lu, open(UPDATES, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    review = {}
    for nid, (u, src, _) in final.items():
        d = done.get(u)
        st = d['s'] if d else 'none'
        if st == 'dead':  # 排在前面的主页失效、后面的候选能打开：去掉失效的那个，让能打开的显示出来
            alive = next((c for c in cands[nid][1:] if done.get(c[0], {}).get('s') in ('ok', 'fixed')), None)
            if alive and src in ('people', 'own'):
                if src == 'people' and alive[1] == 'links':
                    del people[nid]['h']  # student_links 的主页会补上（带出处）
                else:
                    people.setdefault(nid, {})['h'] = done[alive[0]]['r'] if done[alive[0]]['s'] == 'fixed' else alive[0]
                fixes += 1
                stat['ext_dead_replaced_by_other_candidate'] += 1
                continue
        stat['ext_' + st] += 1
        if st == 'dead':
            dead[nid] = d['r']
        elif st == 'review':
            review[nid] = {'u': u, 'r': d['r']}
    li_dead = {nid: u for nid, u in li_urls.items() if done.get(u, {}).get('s') == 'dead'}
    for nid in li_dead:  # 领英明确 404：从来源文件里去掉
        for src in (people, links):
            if norm_li(src.get(nid, {}).get('li', '')) == li_urls[nid]:
                del src[nid]['li']
                fixes += 1
    li_stat = collections.Counter((done.get(u, {}).get('s', 'none'), done.get(u, {}).get('r', '')) for u in li_urls.values())
    if fixes:
        json.dump(people, open(people_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
        json.dump(links, open(links_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    json.dump(dead, open(OUT, 'w', encoding='utf8'), ensure_ascii=False, indent=0)
    json.dump(dict(homepage=review, linkedin_dead=li_dead), open(REVIEW, 'w', encoding='utf8'), ensure_ascii=False, indent=0)
    print(dict(stat), 'dead', len(dead), collections.Counter(dead.values()))
    print('tls fixes', fixes, 'needs review', len(review), 'linkedin', dict(li_stat), 'linkedin dead (not removed)', len(li_dead))


if __name__ == '__main__':
    main()
