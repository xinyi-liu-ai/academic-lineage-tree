"""给主页上没写招生表态的教授，再抓几页"可能写了是否招 PhD"的子页面，供 extract_recruiting.py 离线使用。

对象: 有主页、且 data/enrich/recruiting.json 里还没有 yes / no 的教授。
链接来源: data/cache/people_pages/{pid}.json 里已抓的主页（及 People / Lab 子页）上的链接。
挑哪些链接:
  - 本人那一块网站（/~user/ 之下、个人域名整站、github.io 项目页、院系网站上 /people/name/ 之下）：
    链接文字或 URL 命中 prospective / opening / join / apply / advising / position / FAQ / contact / recruit /
    hiring / "for students" 等；
  - 本人的实验室网站（别的主机，链接文字像 "XYZ Lab"、"my research group"、"lab website"）：先抓实验室首页，
    再从实验室首页上找 prospective / join / openings 链接抓一页；别的主机上文字就是 "Join us" 一类的也直接抓；
  - 去掉院系导航：同一个 URL 出现在 >=3 位教授的主页上（实验室首页 >=6）就当成院系公共链接；挂着 >=3 位教授主页的
    主机算院系网站，上面不属于本人那一块的页面不抓；本科 / 教职招聘 / 校友 / 捐赠 / 访问 / 学费 一类的链接文字不抓。
  - 每人最多抓 MAX_FETCH 页（优先级：prospective/join/openings > position/apply/students > FAQ/advising > contact > 实验室首页）。
礼貌: 复用 fetch_pages.py 的 robots.txt 检查和"同一主机两次请求至少间隔 2 秒"（每主机一把锁，多线程跨主机并发）；
      超时 20 秒；通用 User-Agent；永不访问 dblp.org；PDF 超过 3 MB 直接跳过（先看 Content-Length，没有则读到 3 MB 截断丢弃）。
可中断，重跑跳过已抓的人。
输出: data/cache/recruit_pages/{pid}.json  {"pid":..., "pages":[{"url","text","links","err","via"}]}（不提交）
用法: python scripts/fetch_recruit_pages.py [--dry N]   --dry N 只打印前 N 个人选中的链接，不联网
"""
import concurrent.futures as cf
import json
import os
import random
import re
import sys
import threading
import time
from collections import Counter
from urllib.parse import urlparse, urljoin

import requests

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
import fetch_pages as fp  # noqa: E402

OUT = os.path.join(ROOT, 'data/cache/recruit_pages')
PP = os.path.join(ROOT, 'data/cache/people_pages')
MAX_FETCH = 3
MAX_PDF = 3 * 1024 * 1024
MAX_HTML = 4 * 1024 * 1024

# 优先级从高到低；文字和 URL 都看
STRONG = re.compile(r'prospective|\bopenings?\b|open\s+positions?|\bjoin(?:ing)?\b|recruit|\bhiring\b|vacanc|'
                    r'(?:for|to|info\w*\s+for|note\s+to|notes\s+for|message\s+to)\s+(?:\w+\s+)?(?:students|applicants)|'
                    r'future\s+(?:ph\.?d\.?\s+)?students|potential\s+students|want\s+to\s+work|work(?:ing)?\s+with\s+me|'
                    r'(?:ph\.?d\.?|graduate)\s+(?:students?\s+)?(?:applicants|admissions?|positions?)', re.I)
STRONG_URL = re.compile(r'(?<![a-z])(?:prospective|openings?|join(?:[-_]?us)?|recruit\w*|hiring|vacanc\w*|for[-_]?students|apply|positions?|'
                        r'future[-_]?students|work[-_]?with[-_]?me)(?![a-z])', re.I)
# 别的主机上的链接要更像"本人 / 实验室在招人"
EXT_STRONG = re.compile(r'^(?:join(?:\s+(?:us|me|my|our|the)(?:\s+(?:\w+\s+)?(?:lab|group|team))?)?|openings?|open\s+positions?|positions|'
                        r"(?:we\s*(?:are|'re)\s+)?hiring|vacancies|recruit(?:ing|ment)?|"
                        r'(?:information\s+for\s+|notes?\s+(?:for|to)\s+|for\s+)?prospective\s+(?:ph\.?d\.?\s+|graduate\s+)?(?:students|applicants)|'
                        r'(?:info\w*\s+)?for\s+(?:ph\.?d\.?\s+)?applicants|work(?:ing)?\s+with\s+(?:me|us))\s*[!.:>→↗]*$', re.I)
EXT_BAD = re.compile(r'admission|forms?\b|\blists?\b|listserv|network|society|e-?mail|newsletter|\bjobs\b|careers|apply|grad(?:school|uate)?\.|'
                     r'peoplegrove|applyweb|office\.com|forms\.gle', re.I)
# 别的主机上的院系路径（不当成实验室网站的 Join 页）
DEPT_PATH = re.compile(r'^/(?:[\w-]+/)?(?:admissions?|academics?|graduate|grad|programs?|apply|students|prospective[-_]students|'
                       r'future[-_]students|education|degrees?|phd)(?:/|$|\.)', re.I)
MID = re.compile(r'\bpositions?\b|\bopportunit|\bapply\b|\bapplicants?\b|\badmissions?\b|^(?:my\s+|our\s+|ph\.?d\.?\s+|graduate\s+)?students$', re.I)
# 实验室链接里不是本人实验室的：国家实验室、公司、工作组、奖项
NOT_LAB = re.compile(r'national\s+lab|working\s+group|\baward|\bnsf\b|\bplan\b|\binc\b|yahoo|microsoft|google|ibm|hp\s+labs|'
                     r'bell\s+labs|nokia|intel|adobe|amazon|facebook|meta\s+ai|nvidia|vmware|w3c|ietf|director\s+of|lead\b|labs$|'
                     r'ph\.?\s?d|now\s+at|\b(?:19|20)\d\d\b|meetings?|week\b|\bcs\s*\d', re.I)
# 院系简介页上指向本人个人主页的链接
HOME_TEXT = re.compile(r'^(?:(?:visit\s+)?(?:my|personal|faculty|professional|academic|research)\s+)?(?:web\s*-?\s*site|home\s*-?\s*page|web\s*-?\s*page|page|site)'
                       r'(?:\s*[:>→↗»]\s*(?:link)?)?$|^(?:personal|my)\s+(?:web|home)', re.I)
NOT_LAB_HOST = re.compile(r'\.gov$|goo\.gl$|\.mil$|nsf\.\w+$', re.I)
MYLAB = re.compile(r'\b(?:my|our)\b|website|web\s*site|home\s*page|\bpage\b|\bsite\b|^(?:lab|group|research\s+group|research\s+lab)$', re.I)
LOW = re.compile(r'\bfaqs?\b|frequently\s+asked|\badvis(?:ing|ees?|or)\b|\bmentoring\b', re.I)
LOW_URL = re.compile(r'(?:^|[/_.-])(?:faqs?|advising|advisees|mentoring)(?:[/_.-]|$)', re.I)
CONTACT = re.compile(r'\bcontacts?\b', re.I)
CONTACT_URL = re.compile(r'(?:^|[/_.-])contact(?:[/_.-]|$)', re.I)
LAB = re.compile(r'\b(?:lab|labs|laboratory|group|research\s+group|lab\s+website|group\s+website|lab\s+page|group\s+page)\b', re.I)
# 不是讲 PhD 招生的
NOT_TEXT = re.compile(r'undergrad|faculty|staff|postdoc|alumni|giving|donat|visit|tuition|financial|aid\b|board|'
                      r'transfer|first-year|freshm|high\s+school|k-12|career\s+fair|employer|recruiter|industry|corporate|partner|'
                      r'press|emergency|directions|map\b|parking|it\s+help|help\s+desk|webmaster|accessib|'
                      r'publications?|papers?|courses?|teaching|talks?|news|calendar|events?|seminar|colloquium|'
                      r'reading\s+group|code\b|software|github|datasets?\b|slides|video|\bcv\b|vita|resume|bio\b|'
                      r'masters?|m\.?s\.?\s|meng|online|certificate|summer|intern|reu\b|request\s+info|check\s+application|status|'
                      r'current|organization|involvement|club|'
                      r'chair|dean|council|committee|policy|policies|title\s+ix|equal\s+opportunity|diversity\s+statement', re.I)
NOT_URL = re.compile(r'\.(?:ps|gz|zip|tar|pptx?|docx?|xlsx?|jpe?g|png|gif|svg|mp4|mov|bib|tex)(?:\?|$)|mailto:|javascript:|'
                     r'/(?:undergrad\w*|faculty-?(?:positions|hiring|openings|jobs)|jobs?|careers?|staff|alumni|giving|admissions?/undergrad)\b', re.I)
NEVER_HOST = re.compile(r'(?:^|\.)(?:dblp\.org|dblp\.uni-trier\.de|scholar\.google\.\w+|google\.com|github\.com|'
                        r'linkedin\.com|twitter\.com|x\.com|facebook\.com|instagram\.com|youtube\.com|youtu\.be|bsky\.app|'
                        r'mastodon\.\w+|arxiv\.org|semanticscholar\.org|researchgate\.net|orcid\.org|acm\.org|ieee\.org|'
                        r'wikipedia\.org|medium\.com|substack\.com|zoom\.us|calendly\.com|doi\.org|amazon\.\w+|'
                        r'openreview\.net|aclanthology\.org|springer\.com|nature\.com|sciencedirect\.com|tiktok\.com|threads\.net)$', re.I)


def site(host):
    """xxx.cs.uni.edu -> uni.edu；用来判断"同一所学校的网站"。"""
    parts = host.lower().split(':')[0].split('.')
    if parts[-1] in ('edu', 'org', 'com', 'net', 'io', 'ai', 'me') and len(parts) >= 2:
        return '.'.join(parts[-2:])
    return '.'.join(parts[-3:])


def bare(host):
    return host.lower().split(':')[0].removeprefix('www.')


def norm(u):
    u = u.split('#')[0]
    p = urlparse(u)
    return (bare(p.netloc) + p.path.rstrip('/') + ('?' + p.query if p.query else '')).lower()


def home_prefix(url):
    """主页"本人的那一块"：(host, 路径前缀)。"""
    p = urlparse(url)
    host, path = bare(p.netloc), p.path
    segs = [x for x in path.split('/') if x]
    for i, x in enumerate(segs):
        if x.startswith('~') or x.lower().startswith('%7e'):
            return host, '/' + '/'.join(segs[:i + 1])
    if host.endswith('.github.io'):
        return host, ('/' + segs[0]) if segs and '.' not in segs[0] else '/'
    inst = host.endswith('.edu') or '.ac.' in host or host == 'sites.google.com'
    if segs and re.search(r'\.(?:html?|php|aspx?|cfm|jsp)$', segs[-1], re.I):
        segs = segs[:-1]
        if inst and not segs:
            return host, path          # faculty.uci.edu/profile.cfm?id=... 下面没有本人的子页
    if not inst:
        return host, '/'               # 个人域名：整站都是本人的
    return host, '/' + '/'.join(segs)


def under(href, prefix):
    host, pre = prefix
    p = urlparse(href)
    h = bare(p.netloc)
    if pre == '/':
        return h == host
    if h != host and site(h) != site(host):   # 主页跳转到同校别的主机（www.cs.x.edu -> sites.cs.x.edu）
        return False
    path = p.path.rstrip('/')
    pre = pre.rstrip('/')
    return path == pre or path.startswith(pre + '/') or path.startswith(pre + '.')


def score(href, text, prefix, shared_hosts=(), schools=(), names=()):
    """返回 (优先级, 类型)；None 表示不要。优先级越小越先抓。"""
    t = re.sub(r'\s+', ' ', text or '').strip()
    pu = urlparse(href)
    host = bare(pu.netloc)
    if not href.startswith('http') or not host or 'dblp' in host or NOT_URL.search(href) or len(t) > 60:
        return None
    if NEVER_HOST.search(host) or (t and NOT_TEXT.search(t)):
        return None
    path = pu.path + ('?' + pu.query if pu.query else '')
    is_pdf = pu.path.lower().endswith('.pdf')
    if under(href, prefix):
        rel = path[len(prefix[1].rstrip('/')):] if path.startswith(prefix[1].rstrip('/')) else path
        if re.sub(r'(?i)/?(?:index|home|default)\.\w+$', '', rel).strip('/') == '':
            return None                # 就是主页本身（跳转后的另一个主机名 / index.html）
        if (STRONG.search(t) and not is_pdf) or (is_pdf and EXT_STRONG.search(t)) or (not is_pdf and STRONG_URL.search(rel)):
            return (0, 'sub')
        if MID.search(t) and not is_pdf:
            return (1, 'sub')
        if LOW.search(t) or (not is_pdf and LOW_URL.search(rel)):
            return (2, 'sub')
        if CONTACT.search(t) or (not is_pdf and CONTACT_URL.search(rel)):
            return (3, 'sub')
        return None
    if host == prefix[0] or host in shared_hosts or '.' not in host.removesuffix('.edu') or is_pdf:
        return None                    # 院系网站上不属于本人的页面
    if re.search(r'\.(?!(?:io|ai|me|co|us|tv|cc|ly|sh|so|fm|gg|app|dev|org|com|net|edu|info|science|lab|group|xyz|site|page)$)[a-z]{2,3}$', host):
        return None                    # 外国学校 / 机构（.ch、.ca、.de、.kr……）
    if (host.endswith('.edu') or '.ac.' in host) and schools and site(host) not in schools:
        return None                    # 别的学校的网站：多半是以前待过的实验室
    if t and EXT_STRONG.search(t) and not EXT_BAD.search(t + ' ' + host) and not DEPT_PATH.search(pu.path):
        return (0, 'sub')              # 实验室网站上的 "Join us" 等
    pm = re.search(r"(?:prof\.?|professor|dr\.?)\s+([A-Z][\w-]+)|([A-Z][\w-]+)['’]s\s+(?:\w+\s+)?(?:lab|group|laboratory)", t)
    if pm and names and (pm.group(1) or pm.group(2)).lower() not in names:
        return None                    # "Prof. Mark Riedl's lab"：别人的实验室
    if t and LAB.search(t) and not NOT_LAB.search(t) and not NOT_LAB_HOST.search(host):
        # 同校的、或文字是 "my lab" / "lab website" 的排前面
        return (4 if site(host) in schools or MYLAB.search(t) else 5, 'lab')
    return None


def link_freq():
    """每个 URL / 每个主机出现在多少位教授的主页上。"""
    c, ch = Counter(), Counter()
    for fn in os.listdir(PP):
        try:
            d = json.load(open(os.path.join(PP, fn), encoding='utf-8'))
        except Exception:
            continue
        pages = d.get('pages') or []
        if pages:
            urls = {norm(h) for h, _ in pages[0].get('links') or [] if h}
            c.update(urls)
            ch.update({u.split('/')[0] for u in urls})
    return c, ch


def shared_host_set():
    """主页挂在同一主机上的教授 >=3 位：这是院系网站，不是本人 / 实验室网站。"""
    c = Counter(bare(urlparse(p['homepage']).netloc) for p in load_profs() if p['homepage'].startswith('http'))
    return {h for h, n in c.items() if n >= 3}


def school_sites():
    """每所学校的网站域名（uni.edu）：同校教授主页里出现 >=3 次或占 >=20% 的 .edu 域名。"""
    from collections import defaultdict
    d = defaultdict(Counter)
    for p in load_profs():
        h = bare(urlparse(p['homepage']).netloc)
        if h.endswith('.edu'):
            d[p['uni']][site(h)] += 1
    return {u: {s for s, n in c.items() if n >= 3 or n >= 0.2 * sum(c.values())} for u, c in d.items()}


def candidates(d, freq, seen, shared_hosts, schools=frozenset(), names=frozenset()):
    pages = d.get('pages') or []
    if not pages or not pages[0].get('links'):
        return []
    prefix = home_prefix(pages[0]['url'])
    if prefix[0].endswith('.edu'):
        schools = set(schools) | {site(prefix[0])}
    if prefix[1] == '/' and (prefix[0] in shared_hosts or prefix[0] == site(prefix[0])):
        prefix = (prefix[0], '/\0')     # 主页填的是学校 / 院系首页：本人子页无从谈起，只看实验室链接
    # 主页是院系网站上的简介页：找 "Personal website" 链接
    dept_profile = prefix[0] in shared_hosts and '~' not in prefix[1]
    found = {}
    for pi, pg in enumerate(pages):
        for h, t in pg.get('links') or []:
            if not h:
                continue
            h = urljoin(pg['url'], h).split('#')[0]
            n = norm(h)
            if n in seen:
                continue
            sc = score(h, t, prefix, shared_hosts, schools, names)
            if dept_profile and pi == 0 and HOME_TEXT.search(re.sub(r'\s+', ' ', t or '').strip()):
                hh = bare(urlparse(h).netloc)
                if h.startswith('http') and not NEVER_HOST.search(hh) and not NOT_URL.search(h) and not h.lower().endswith('.pdf') \
                        and (hh != prefix[0] or '~' in h) and hh not in shared_hosts - {prefix[0]}:
                    sc = (1, 'home')
            if sc is None or freq[0].get(n, 0) >= (3 if sc[1] == 'sub' else 6):
                continue
            if not under(h, prefix) and freq[1].get(n.split('/')[0], 0) >= 8:
                continue        # 别的主机被 >=8 位教授链接：研究生院 / 表单 / 新闻站，不是本人实验室
            if pi > 0 and sc[0] > 0:
                continue        # 子页（People / Lab 页）上的链接只要强匹配的
            if n not in found or sc < found[n][0]:
                found[n] = (sc, h, re.sub(r'\s+', ' ', t or '').strip())
    return sorted(found.values(), key=lambda x: x[0])


_locks_lock = threading.Lock()


def polite_get(url):
    host = urlparse(url).netloc
    with _locks_lock:
        lock = fp._host_locks.setdefault(host, threading.Lock())
    with lock:
        wait = fp._last.get(host, 0) + fp.HOST_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, headers={'User-Agent': fp.UA}, timeout=20, stream=True, allow_redirects=True)
            try:
                ctype = r.headers.get('content-type', '').lower()
                try:
                    clen = int(r.headers.get('content-length') or 0)
                except ValueError:
                    clen = 0
                is_pdf = 'pdf' in ctype or urlparse(r.url).path.lower().endswith('.pdf')
                if is_pdf and clen > MAX_PDF:
                    return r, None
                body = r.raw.read((MAX_PDF if is_pdf else MAX_HTML) + 1, decode_content=True)
                if is_pdf and len(body) > MAX_PDF:
                    return r, None
            finally:
                r.close()
        finally:
            fp._last[host] = time.time()
    return r, body


def fetch(url):
    if 'dblp' in urlparse(url).netloc.lower():
        return '', [], 'never'
    if not fp.allowed(url):
        return '', [], 'robots'
    try:
        r, body = polite_get(url)
    except Exception as e:  # 网络错误 / urllib3 解压出错
        return '', [], type(e).__name__
    if body is None:
        return '', [], 'pdf-too-big'
    if r.status_code != 200:
        return '', [], str(r.status_code)
    if 'dblp' in urlparse(r.url).netloc.lower():
        return '', [], 'never'
    text, links = fp.to_text(r, body)
    return text, [(urljoin(r.url, h), t) for h, t in links if h], ''


def name_tokens(name):
    return {w.lower() for w in re.findall(r'[^\W\d_][\w-]+', name)}


def crawl(d, freq, shared, schools=frozenset(), names=frozenset()):
    pages = d.get('pages') or []
    seen = {norm(p['url']) for p in pages if p.get('url')}
    out, n_fetch, lab_hosts = [], 0, set()
    for (prio, kind), h, t in candidates(d, freq, seen, shared, schools, names):
        if n_fetch >= MAX_FETCH:
            break
        n = norm(h)
        if n in seen:
            continue
        lh = bare(urlparse(h).netloc)
        if kind in ('lab', 'home'):
            if lh in lab_hosts:
                continue
            lab_hosts.add(lh)
        seen.add(n)
        text, links, err = fetch(h)
        n_fetch += 1
        out.append(dict(url=h, text=text[:400000], links=links if kind != 'sub' else [], err=err, via=t, kind=kind))
        if kind != 'sub' and links and n_fetch < MAX_FETCH:
            # 实验室首页 / 个人主页上的 Join / Prospective / Openings
            pre2 = (lh, '/') if kind == 'lab' else home_prefix(h)
            best = None
            for h2, t2 in links:
                h2 = h2.split('#')[0]
                if norm(h2) in seen or not under(h2, pre2):
                    continue
                sc = score(h2, t2, pre2)
                if sc and sc[1] == 'sub' and sc[0] <= 1 and (best is None or sc < best[0]):
                    best = (sc, h2, re.sub(r'\s+', ' ', t2 or '').strip())
            if best:
                seen.add(norm(best[1]))
                t3, _, e3 = fetch(best[1])
                n_fetch += 1
                out.append(dict(url=best[1], text=t3[:400000], links=[], err=e3, via=kind + ': ' + best[2], kind='sub2'))
    return out


def todo_list():
    rec = json.load(open(os.path.join(ROOT, 'data/enrich/recruiting.json'), encoding='utf-8'))
    profs = [p for p in load_profs() if p['homepage'].startswith('http') and rec.get(p['id'], {}).get('s') not in ('yes', 'no')]
    random.Random(3).shuffle(profs)   # 打散同一主机
    return profs


def main():
    args = sys.argv[1:]
    dry = int(args[args.index('--dry') + 1]) if '--dry' in args else 0
    freq = link_freq()
    shared = shared_host_set()
    sch = school_sites()
    profs = [p for p in todo_list() if os.path.exists(os.path.join(PP, p['id'] + '.json'))]
    if dry:
        nc = Counter()
        for p in profs:
            d = json.load(open(os.path.join(PP, p['id'] + '.json'), encoding='utf-8'))
            seen = {norm(x['url']) for x in d.get('pages') or [] if x.get('url')}
            c = candidates(d, freq, seen, shared, sch.get(p['uni'], set()), name_tokens(p['name']))
            nc[min(len(c), 5)] += 1
            if dry > 0 and c:
                dry -= 1
                print(p['id'], p['homepage'])
                for (pr, k), h, t in c[:6]:
                    print(f'   {pr} {k:3} {t[:40]!r:44} {h}')
        print('candidate count distribution', sorted(nc.items()))
        return
    os.makedirs(OUT, exist_ok=True)
    todo = [p for p in profs if not os.path.exists(os.path.join(OUT, p['id'] + '.json'))]
    print('professors', len(profs), 'remaining', len(todo), flush=True)
    t0, done, npages = time.time(), 0, 0

    def work(p):
        try:
            d = json.load(open(os.path.join(PP, p['id'] + '.json'), encoding='utf-8'))
            pages = crawl(d, freq, shared, sch.get(p['uni'], set()), name_tokens(p['name']))
        except Exception as e:  # 单人出错不影响整体
            pages = [dict(url='', text='', links=[], err='crawl-' + type(e).__name__, via='')]
        with open(os.path.join(OUT, p['id'] + '.json'), 'w', encoding='utf-8', errors='replace') as f:
            json.dump(dict(pid=p['id'], pages=pages), f, ensure_ascii=False)
        return len(pages)

    with cf.ThreadPoolExecutor(max_workers=24) as ex:
        for n in ex.map(work, todo):
            done += 1
            npages += n
            if done % 200 == 0:
                print(f'{done}/{len(todo)} pages={npages} {time.time() - t0:.0f}s', flush=True)
    print('done', done, 'pages', npages, f'{time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
