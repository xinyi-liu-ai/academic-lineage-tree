"""抓教授在别的网站上的实验室 / 课题组网站（主页上的 "Lab"、"Research group"、"lab website"、xxxlab.org、
某某组织的 github.io 等），保存 HTML，供 extract_students.py（学生名单、学生个人主页和照片）和
extract_recruiting.py（实验室 Join / Openings 页上的招生表态）离线使用。

对象: 现任教授（status 不是 former），且 data/cache/people_pages/{pid}.json 里有已抓的主页。
找实验室链接: 复用 fetch_recruit_pages.candidates() 的 'lab' 一类（链接文字像 "XYZ Lab" / "my research group" /
  "lab website"，排除院系 / 学校网站、别校网站、公司 / 国家实验室、社交网站）；另外链接文字就是网址本身或
  "here" / "website" 而主机名带 lab / group 的也算（"https://hopwinslab.com/"）。
  同一个链接出现在 >=3 位教授的主页上（院系公共链接、多位 PI 共用的大实验室）不要；主机被 >=8 位教授链接的不要。
每人只取第一个实验室网站；抓实验室首页，再从首页（及 People 页）的链接里挑同一网站上的
  People / Team / Members / Students / Alumni 页和 Join / Openings / Prospective / Positions 页，每人最多 MAX_PAGES 页。
  实验室首页和子页里都没出现教授的姓时记 has_name=false，提取时不用（多半不是他的实验室）。
礼貌: 复用 fetch_pages.allowed（robots.txt）和 fetch_recruit_pages.polite_get（同一主机两次请求至少间隔 2 秒，每主机一把锁，
  多线程跨主机并发）；超时 20 秒；通用 User-Agent；永不访问 dblp.org；不绕过任何反爬验证（403 / 验证页直接记下错误）。
可中断，重跑跳过已抓的人。
输出: data/cache/lab_pages/{pid}.json.gz  {"pid", "lab": {"url", "via"}, "pages": [{"url", "final", "kind", "err", "html"}]}（不提交）
用法: python scripts/fetch_lab_pages.py [--dry N]   --dry N 只打印前 N 个人找到的实验室链接，不联网
      python scripts/fetch_lab_pages.py --follow    recruiting.json 里不带年份的 yes：顺着主页上 "THIS" / "details" / "Join" /
                                                    "Positions" 等招生链接再抓一层 → data/cache/recruit_follow/{pid}.json
      python scripts/fetch_lab_pages.py --photos    检查 students_lab.jsonl 里的学生照片（https、图片、>=80px）
                                                    → data/enrich/student_photos_lab.json
      python scripts/fetch_lab_pages.py --refetch-home  重抓 people_pages 里主页没内容 / 地址已换 / 是院系名录页 / 有 Mentoring 子页
                                                    没抓的教授（跟随 meta refresh 和 JS 跳转、http→https、院系页 → 个人主页两跳）
      python scripts/fetch_lab_pages.py --refetch-cv    以前只读前 15 页、可能被截断的 PDF CV 重抓读全
      python scripts/fetch_lab_pages.py --refresh       people_pages 重抓后，实验室候选变了的重新抓（含 "<X> Lab/Group/Team"、个人主页上的实验室链接）
顺序: fetch_lab_pages.py --refetch-home → --refetch-cv → fetch_lab_pages.py --refresh → extract_students.py → fetch_lab_pages.py --photos → extract_recruiting.py →
      fetch_lab_pages.py --follow → extract_recruiting.py → build_graph.py
"""
import concurrent.futures as cf
import gzip
import json
import os
import random
import re
import sys
import time
from collections import Counter
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
import fetch_pages as fp  # noqa: E402
import fetch_recruit_pages as frp  # noqa: E402
from extract_recruiting import deaccent, surname  # noqa: E402  （纯文本处理，不联网）

OUT = os.path.join(ROOT, 'data/cache/lab_pages')
PP = os.path.join(ROOT, 'data/cache/people_pages')
MAX_PAGES = 5
MAX_HTML = 3 * 1024 * 1024

# 主机名像实验室网站（hopwinslab.com、xyz-lab.github.io、foogroup.org）
TEAM_T = re.compile(r"^(?:the\s+|my\s+|our\s+)?[A-Z][\w&.'’+-]*(?:\s+(?:[A-Z&][\w&.'’+-]*|of|for|and|in|on|&)){0,6}\s+(?:Lab|Labs|Laboratory|Group|Team|Research\s+Group|Research\s+Lab)(?:\s+website)?\s*[»>→↗]?$")
LAB_HOST = re.compile(r'(?:lab|labs|group|grp)(?:[.-]|$)|^(?:lab|group)s?[.-]', re.I)
GENERIC_TEXT = re.compile(r'^(?:https?://\S+|www\.\S+|here|website|web\s*site|link|this\s+link|\S+\.(?:com|org|net|io|ai|edu)\S*)$', re.I)
# 实验室网站上的子页
PEOPLE_T = re.compile(r'^(?:(?:our|the|lab|group|team|current|former|research)\s+)*(?:people|team|members?|students|personnel|researchers|'
                      r'alumni|alumnae|ph\.?\s?d\.?\s+students|group|meet\s+the\s+(?:team|group|lab)|who\s+we\s+are|our\s+team|lab\s+members|'
                      r'group\s+members|team\s+members|members\s+(?:&|and)\s+alumni|people\s+(?:&|and)\s+alumni)\s*[»>→]?$', re.I)
PEOPLE_U = re.compile(r'(?:^|/)(?:people|team|members?|students|personnel|alumni|lab-?members|group-?members|our-?team)(?:/|\.html?|\.php|$)', re.I)
JOIN_T = re.compile(r'\bjoin\b|\bopenings?\b|prospective|\bpositions?\b|opportunit|recruit|\bhiring\b|vacanc|\bapply\b|work\s+with\s+us', re.I)
JOIN_U = re.compile(r'(?:^|/)(?:join(?:-?us)?|openings?|prospective(?:-students)?|positions?|opportunities|recruit\w*|hiring|vacancies|apply)(?:/|\.html?|\.php|$)', re.I)
NOT_SUB = re.compile(r'\.(?:pdf|ps|gz|zip|pptx?|docx?|jpe?g|png|gif|svg|mp4|bib)(?:\?|$)|mailto:|javascript:', re.I)


def lab_candidates(d, freq, shared, schools, names, home_host):
    """主页上指向本人实验室网站的链接，按优先级排序：[(prio, url, 链接文字)]。"""
    pages = d.get('pages') or []
    if not pages:
        return []
    seen = {frp.norm(p['url']) for p in pages if p.get('url')}
    out = []
    # 主页是院系名录页时，--refetch-home 顺着去的个人主页（via=personal）也当主页看：主页 → 个人主页 → 实验室网站
    roots = [pages[0]] + [p for p in pages[1:] if str(p.get('via', '')) == 'personal']
    for root in roots:
        for (prio, kind), h, t in frp.candidates({'pages': [root]}, freq, seen, shared, schools, names):
            if kind == 'lab':
                out.append((prio, h, t))
    have = {frp.norm(h) for _, h, _ in out}
    root_links = [x for root in roots for x in (root.get('links') or [])]
    # 链接文字是 "<X> Lab" / "<X> Group" / "<X> Team"（frp 的规则不认 Team、也不认首页以外的个人主页）
    for h, t in root_links:
        if not h or not h.startswith('http'):
            continue
        h = h.split('#')[0]
        n = frp.norm(h)
        host = frp.bare(urlparse(h).netloc)
        t = re.sub(r'\s+', ' ', t or '').strip()
        if n in have or n in seen or host == home_host or host in shared or frp.NEVER_HOST.search(host) or 'dblp' in host:
            continue
        if not TEAM_T.match(t) or frp.NOT_LAB.search(t) or NOT_SUB.search(h):
            continue
        if (host.endswith('.edu') or '.ac.' in host) and schools and frp.site(host) not in schools:
            continue
        if freq[0].get(n, 0) >= 3 or freq[1].get(n.split('/')[0], 0) >= 8:
            continue
        out.append((5, h, t))
        have.add(n)
    # 链接文字就是网址 / "here"：主机名带 lab / group 才算
    for h, t in root_links:
        if not h or not h.startswith('http'):
            continue
        h = h.split('#')[0]
        n = frp.norm(h)
        host = frp.bare(urlparse(h).netloc)
        t = re.sub(r'\s+', ' ', t or '').strip()
        if n in have or n in seen or host == home_host or host in shared or frp.NEVER_HOST.search(host) or 'dblp' in host:
            continue
        if not GENERIC_TEXT.match(t) or not LAB_HOST.search(host.split('.')[0] if not host.endswith('.github.io') else host[:-10]):
            continue
        if (host.endswith('.edu') or '.ac.' in host) and schools and frp.site(host) not in schools:
            continue
        if NOT_SUB.search(h) or freq[0].get(n, 0) >= 3 or freq[1].get(n.split('/')[0], 0) >= 8:
            continue
        out.append((6, h, t))
        have.add(n)
    res = []
    for prio, h, t in out:
        if NOT_LAB_EXTRA.search(t) or NOT_LAB_EXTRA_HOST.search(frp.bare(urlparse(h).netloc)):
            continue
        h2, depth = lab_root(h)
        if prio == 6 and depth:
            continue  # 文字只是 "Website" / 网址的子页、项目页（xxx-lab.github.io/H-Flow/）
        # 同一个实验室链接出现在 >=3 位教授的主页上：院系 / 大实验室公共链接
        if freq[0].get(frp.norm(h), 0) >= 3 or freq[0].get(frp.norm(h2), 0) >= 3:
            continue
        res.append((prio, h2, t))
    return sorted(res, key=lambda c: c[0])


# 不是本人实验室的："Association for Laboratory Phonology"、"DOE's Jefferson Lab"、"ACM Special Interest Group"、课程页
NOT_LAB_EXTRA = re.compile(r'association|society|special\s+interest|\bsig\w*\b|fellowship|national|jefferson|lincoln|\bctf\b|course|'
                           r'overview|members\s+and|institute\s+for|consortium|alliance|blog\b|news', re.I)
NOT_LAB_EXTRA_HOST = re.compile(r'speakerdeck\.com|slideshare|wordpress\.com$|^(?:www\.)?nortonlifelock|\.gov$', re.I)
SUB_TAIL = re.compile(r'^(?:publications?|papers|projects?|people|team|members|news|research|index\.\w+|home|about|software|'
                      r'contact|join(?:-?us)?|openings?|group|lab|blog|en)$', re.I)


def lab_root(h):
    """实验室链接若指向某个子页（/publications/、/people/、/index.html）改成上一级。返回 (URL, 剩下的路径段数)。"""
    pu = urlparse(h)
    segs = [x for x in pu.path.split('/') if x]
    while segs and SUB_TAIL.match(segs[-1]):
        segs.pop()
    if frp.bare(pu.netloc).endswith('.github.io'):
        segs = segs[:1]
    if not segs:
        return f'{pu.scheme}://{pu.netloc}/', 0
    if len(segs) == len([x for x in pu.path.split('/') if x]) and not pu.query:
        return h, len(segs)
    return f'{pu.scheme}://{pu.netloc}/' + '/'.join(segs) + '/', len(segs)


def fetch_html(url):
    """返回 (最终 URL, HTML 文本, 错误)。只要 HTML。"""
    if 'dblp' in urlparse(url).netloc.lower():
        return url, '', 'never'
    if not fp.allowed(url):
        return url, '', 'robots'
    try:
        r, body = frp.polite_get(url)
    except Exception as e:
        return url, '', type(e).__name__
    if body is None:
        return url, '', 'too-big'
    if r.status_code != 200:
        return r.url, '', str(r.status_code)
    if 'dblp' in urlparse(r.url).netloc.lower():
        return r.url, '', 'never'
    ctype = r.headers.get('content-type', '').lower()
    if 'html' not in ctype and not body[:500].lstrip().lower().startswith((b'<!doctype html', b'<html')):
        return r.url, '', 'not-html'
    enc = r.encoding if r.encoding and r.encoding.lower() != 'iso-8859-1' else None
    m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4000], re.I)
    if m:
        enc = m.group(1).decode('ascii', 'ignore')
    try:
        html = body[:MAX_HTML].decode(enc or 'utf-8', errors='replace')
    except LookupError:
        html = body[:MAX_HTML].decode('utf-8', errors='replace')
    return r.url, html, ''


def page_links(html, base):
    """HTML 里的 (绝对 URL, 链接文字)。"""
    p = fp.TextAndLinks()
    try:
        p.feed(html)
    except Exception:
        pass
    text = re.sub(r'[ \t\r\f\v]+', ' ', ''.join(p.out))
    return [(urljoin(base, h), re.sub(r'\s+', ' ', t or '').strip()) for h, t in p.links if h], text


def same_site(h, lab_url):
    a, b = urlparse(h), urlparse(lab_url)
    ha, hb = frp.bare(a.netloc), frp.bare(b.netloc)
    if ha != hb:
        return False
    if hb.endswith('.github.io') or hb == 'sites.google.com':
        segs = [x for x in b.path.split('/') if x]
        if hb == 'sites.google.com' and len(segs) >= 2:
            return a.path.startswith('/' + '/'.join(segs[:2]))
    return True


def pick_subpages(links, lab_url, seen):
    people, join = [], []
    for h, t in links:
        h = h.split('#')[0]
        if not h.startswith('http') or NOT_SUB.search(h) or frp.norm(h) in seen or not same_site(h, lab_url):
            continue
        path = urlparse(h).path
        if path.strip('/') == urlparse(lab_url).path.strip('/'):
            continue
        if len(t) <= 40 and (PEOPLE_T.match(t) or (not t or GENERIC_TEXT.match(t)) and PEOPLE_U.search(path)) or (len(t) <= 25 and PEOPLE_U.search(path) and not JOIN_T.search(t)):
            if h not in people and not re.search(r'(?i)publication|news|project|research$|software|contact', t):
                people.append(h)
        elif len(t) <= 50 and (JOIN_T.search(t) or JOIN_U.search(path)) and not re.search(
                r'(?i)faculty\s+positions?|postdoc\w*\s+(?:only|positions?)$|undergrad|summer|intern', t):
            if h not in join:
                join.append(h)
    # alumni 放后面
    people.sort(key=lambda u: 1 if re.search(r'(?i)alum', u) else 0)
    return people, join


def crawl(p, d, freq, shared, schools):
    home_host = frp.bare(urlparse(p['homepage']).netloc)
    cands = lab_candidates(d, freq, shared, schools, frp.name_tokens(p['name']), home_host)
    if not cands:
        return None
    prio, lab_url, via = cands[0]
    rec = dict(pid=p['id'], lab=dict(url=lab_url, via=via, prio=prio), pages=[])
    final, html, err = fetch_html(lab_url)
    rec['pages'].append(dict(url=lab_url, final=final, kind='lab', err=err, html=html))
    if not html:
        return rec
    links, text = page_links(html, final)
    sur = surname(p['name'])
    has = lambda t: bool(sur) and re.search(r'\b' + re.escape(sur) + r'\b', deaccent(t)) is not None  # noqa: E731
    rec['lab']['has_name'] = has(text)
    seen = {frp.norm(lab_url), frp.norm(final)}
    people, join = pick_subpages(links, final, seen)
    # 名额先给第一个 People 页和第一个 Join 页，再给第二个
    order = [(u, 'people') for u in people[:1]] + [(u, 'join') for u in join[:1]] + \
            [(u, 'people') for u in people[1:2]] + [(u, 'join') for u in join[1:2]]
    for u, kind in order:
        if len(rec['pages']) >= MAX_PAGES:
            break
        if frp.norm(u) in seen:
            continue
        seen.add(frp.norm(u))
        f2, h2, e2 = fetch_html(u)
        seen.add(frp.norm(f2))
        rec['pages'].append(dict(url=u, final=f2, kind=kind, err=e2, html=h2))
        # People 页上再分 PhD / Alumni 子页：名额还有就跟一层
        if kind == 'people' and h2 and len(rec['pages']) < MAX_PAGES:
            l2, _ = page_links(h2, f2)
            p2, j2 = pick_subpages(l2, final, seen)
            for u2 in p2[:MAX_PAGES - len(rec['pages']) - (1 if join and frp.norm(join[0]) not in seen else 0)]:
                if re.search(r'(?i)alum|former|past|student|phd|member', u2) and frp.norm(u2) not in seen:
                    seen.add(frp.norm(u2))
                    f3, h3, e3 = fetch_html(u2)
                    rec['pages'].append(dict(url=u2, final=f3, kind='people', err=e3, html=h3))
    # 首页上没有本人的姓时看 People / Join 页（小实验室首页常只写实验室名）；都没有的提取时不用
    if not rec['lab']['has_name']:
        rec['lab']['has_name'] = any(pg['html'] and has(page_links(pg['html'], pg['final'])[1]) for pg in rec['pages'][1:])
    return rec


def path_of(pid):
    return os.path.join(OUT, pid + '.json.gz')


def load(pid):
    f = path_of(pid)
    if not os.path.exists(f):
        return None
    with gzip.open(f, 'rt', encoding='utf-8') as fh:
        return json.load(fh)


FOLLOW_OUT = os.path.join(ROOT, 'data/cache/recruit_follow')
# 招生链接：文字本身说明是招生页的（任何位置），和只有放在招生句子旁边才算的（"Please read THIS"、"details"）
FOLLOW_STRONG = re.compile(r'^(?:hiring|join(?:\s+(?:us|me|my|our)(?:\s+(?:\w+\s+)?(?:lab|group|team))?)?|openings?|open\s+positions?|positions?|'
                           r'(?:notes?\s+(?:to|for)\s+|info\w*\s+for\s+|for\s+)?prospective(?:\s+(?:ph\.?d\.?|graduate))?(?:\s+(?:students?|applicants?))?|'
                           r'recruit(?:ing|ment)?(?:\s+info\w*)?|vacancies|(?:ph\.?d\.?\s+)?(?:positions?|openings?|opportunities)|'
                           r'(?:for\s+)?(?:future|potential)\s+students|student\s+recruitment)\s*[!.:>»→]*$', re.I)
FOLLOW_WEAK = re.compile(r'^(?:this|here|details?|more(?:\s+(?:info\w*|details))?|link|this\s+(?:page|link)|read\s+this|click\s+here|information)$', re.I)


def follow_candidates(pid, d, freq, seen):
    """主页上只有不带年份的 yes 时再抓的链接（最多 2 个）。"""
    from extract_recruiting import paragraphs
    pages = d.get('pages') or []
    rec = json.load(open(os.path.join(ROOT, 'data/enrich/recruiting.json'), encoding='utf-8')).get(pid, {})
    q = re.sub(r'^…|…$', '', rec.get('q', ''))[:60]
    out = []
    for pg in pages:
        text = pg.get('text') or ''
        near = ''
        if q and q in re.sub(r'\s+', ' ', text):
            flat = re.sub(r'\s+', ' ', text)
            i = flat.index(q)
            near = flat[max(0, i - 300):i + 500]
        for h, t in pg.get('links') or []:
            h = (h or '').split('#')[0]
            t = re.sub(r'\s+', ' ', t or '').strip()
            if not h.startswith('http') or NOT_SUB.search(h) or frp.norm(h) in seen or frp.NEVER_HOST.search(frp.bare(urlparse(h).netloc)):
                continue
            if freq[0].get(frp.norm(h), 0) >= 3 or len(t) > 50:
                continue
            strong = bool(FOLLOW_STRONG.match(t))
            weak = bool(FOLLOW_WEAK.match(t)) and near and re.search(r'(?<![A-Za-z])' + re.escape(t) + r'(?![A-Za-z])', near)
            if (strong or weak) and h not in [x[0] for x in out]:
                out.append((h, t, 0 if weak else 1))
    return [x for x in sorted(out, key=lambda x: x[2])][:2]


def follow_main():
    """--follow：recruiting.json 里不带年份的 yes，顺着主页上的招生链接（"THIS"、"details"、"Join"、"Positions"…）再抓一层。"""
    freq = frp.link_freq()
    rc = json.load(open(os.path.join(ROOT, 'data/enrich/recruiting.json'), encoding='utf-8'))
    profs = [p for p in load_profs() if rc.get(p['id'], {}).get('s') == 'yes' and not rc[p['id']].get('y')
             and os.path.exists(os.path.join(PP, p['id'] + '.json'))]
    os.makedirs(FOLLOW_OUT, exist_ok=True)
    todo = [p for p in profs if not os.path.exists(os.path.join(FOLLOW_OUT, p['id'] + '.json'))]
    random.Random(6).shuffle(todo)
    print('undated yes', len(profs), 'remaining', len(todo), flush=True)

    def work(p):
        d = json.load(open(os.path.join(PP, p['id'] + '.json'), encoding='utf-8'))
        seen = {frp.norm(x['url']) for x in d.get('pages') or [] if x.get('url')}
        for extra in (os.path.join(ROOT, 'data/cache/recruit_pages', p['id'] + '.json'),):
            if os.path.exists(extra):
                seen |= {frp.norm(x['url']) for x in json.load(open(extra, encoding='utf-8')).get('pages') or [] if x.get('url')}
        lab = load(p['id'])
        if lab:
            seen |= {frp.norm(x['url']) for x in lab.get('pages') or []} | {frp.norm(x.get('final') or '') for x in lab.get('pages') or []}
        home = frp.home_prefix(p['homepage'])
        pages = []
        for h, t, _ in follow_candidates(p['id'], d, freq, seen):
            final, html, err = fetch_html(h)
            links, text = page_links(html, final) if html else ([], '')
            pages.append(dict(url=h, final=final, via=t, err=err, text=text[:400000], links=links[:300],
                              own=frp.under(final, home) or frp.under(h, home)))
        with open(os.path.join(FOLLOW_OUT, p['id'] + '.json'), 'w', encoding='utf-8') as f:
            json.dump(dict(pid=p['id'], pages=pages), f, ensure_ascii=False)
        return len(pages)

    n = 0
    with cf.ThreadPoolExecutor(max_workers=32) as ex:
        for k in ex.map(work, todo):
            n += k
    print('fetched pages', n)


PHOTO_CACHE = os.path.join(ROOT, 'data/cache/lab_photo_verify.jsonl')
PHOTO_OUT = os.path.join(ROOT, 'data/enrich/student_photos_lab.json')


def check_photo(url):
    """https 下能否打开、是不是图片、尺寸 >= 80px、宽高比 0.5–2、没有禁止跨站引用。返回 (https 地址, 原因)。"""
    import io
    import requests
    from PIL import Image
    u = 'https://' + url[7:] if url.startswith('http://') else url
    if not fp.allowed(u):
        return None, 'robots'
    host = urlparse(u).netloc
    with frp._locks_lock:
        lock = fp._host_locks.setdefault(host, __import__('threading').Lock())
    with lock:
        wait = fp._last.get(host, 0) + fp.HOST_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(u, headers={'User-Agent': fp.UA}, timeout=20, stream=True)
            try:
                body = r.raw.read(4 * 1024 * 1024, decode_content=True)
            finally:
                r.close()
        except Exception as e:
            return None, type(e).__name__
        finally:
            fp._last[host] = time.time()
    if r.status_code != 200:
        return None, f'status {r.status_code}'
    if not r.headers.get('content-type', '').lower().startswith('image/'):
        return None, 'not image'
    if r.headers.get('cross-origin-resource-policy', '').strip().lower() in ('same-origin', 'same-site'):
        return None, 'corp'
    try:
        w, h = Image.open(io.BytesIO(body)).size
    except Exception:
        return None, 'unreadable'
    if min(w, h) < 80 or not 0.5 <= w / h <= 2.0:
        return None, f'size {w}x{h}'
    return u, ''


def photos_main():
    """--photos：students_lab.jsonl 里实验室页上的学生照片逐张检查，好的写进 data/enrich/student_photos_lab.json {原地址: https 地址}。"""
    urls = sorted({json.loads(l).get('ph') for l in open(os.path.join(ROOT, 'data/enrich/students_lab.jsonl'), encoding='utf-8')} - {None})
    done = {}
    if os.path.exists(PHOTO_CACHE):
        for l in open(PHOTO_CACHE, encoding='utf-8'):
            r = json.loads(l)
            done[r['url']] = r
    todo = [u for u in urls if u not in done]
    random.Random(8).shuffle(todo)
    print('photos', len(urls), 'to check', len(todo), flush=True)
    lock = __import__('threading').Lock()

    def work(u):
        good, why = check_photo(u)
        r = dict(url=u, ok=good, why=why)
        with lock, open(PHOTO_CACHE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
        return r
    with cf.ThreadPoolExecutor(max_workers=32) as ex:
        for r in ex.map(work, todo):
            done[r['url']] = r
    out = {u: done[u]['ok'] for u in urls if done.get(u, {}).get('ok')}
    json.dump(out, open(PHOTO_OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    print('good', len(out), 'bad', Counter(done[u]['why'].split(' ')[0] for u in urls if not done[u].get('ok')).most_common(8))


SUB_T = re.compile(r'\b(?:students?|people|group|lab|team|members?|alumni|advisees|mentees|mentoring|advising|former students|'
                   r'research\s+team|my\s+group|phd\s+students)\b', re.I)
SUB_NOT = re.compile(r'publication|paper|course|teaching|talk|news|calendar|event|seminar|prospective|join|apply', re.I)
PERSONAL_T = re.compile(r'^(?:(?:visit\s+)?(?:my|personal|faculty|professional|academic|research|lab|group)\s+)?(?:web\s*-?\s*site|home\s*-?\s*page|'
                        r'web\s*page|page|site)(?:\s*[:>→↗»]\s*(?:link)?)?$|^(?:personal|my)\s+(?:web|home)|^website$|^homepage$', re.I)


def refetch_targets(profs):
    """people_pages 里主页没抓到内容的、主页地址已换（link_updates.json）的、主页是院系名录页的教授。返回 [(教授, 主页地址, 原因)]。"""
    lu_path = os.path.join(ROOT, 'data/enrich/link_updates.json')
    lu = json.load(open(lu_path, encoding='utf-8')) if os.path.exists(lu_path) else {}
    shared = frp.shared_host_set()
    out = []
    for p in profs:
        f = os.path.join(PP, p['id'] + '.json')
        home = (lu.get(p['id']) or {}).get('homepage') or p['homepage']
        if not os.path.exists(f):
            out.append((p, home, 'nocache'))
            continue
        d = json.load(open(f, encoding='utf-8'))
        if d.get('refetched'):
            continue
        pg = (d.get('pages') or [{}])[0]
        if home.rstrip('/') != (pg.get('url') or '').rstrip('/'):
            out.append((p, home, 'moved'))
        elif len((pg.get('text') or '').strip()) < 300:
            out.append((p, home, 'empty'))
        elif frp.bare(urlparse(home).netloc) in shared and not re.search(r'/~|%7e', home, re.I):
            out.append((p, home, 'dept'))
        else:
            # 同站的 Mentoring / Advising / Advisees 子页（fetch_people_pages.py 的子页规则不认这几个词）
            got = {frp.norm(x.get('url') or '') for x in d.get('pages') or []}
            host = frp.bare(urlparse(pg.get('url') or home).netloc)
            if any(re.search(r'(?i)\b(?:mentoring|mentees|advising|advisees)\b', t or '') and len(t or '') < 40
                   and frp.bare(urlparse(h).netloc) == host and frp.norm(h.split('#')[0]) not in got
                   for h, t in pg.get('links') or []):
                out.append((p, home, 'mentoring'))
    return out


def crawl_home(p, home, why, shared):
    """主页 + 同站的 Students / People / Mentoring 子页；主页是院系名录页时，再顺着第一个站外的个人主页链接抓它和它的子页。"""
    from fetch_people_pages import BAD
    pages, seen = [], set()
    sur = surname(p['name'])

    def add(url, via):
        t, l, e = fp.fetch(url)
        seen.add(frp.norm(url))
        pages.append(dict(url=url, text=t[:400000], links=l[:600], err=e, via=via))
        return t, l

    def subs(base, links, limit):
        host = frp.bare(urlparse(base).netloc)
        pre = frp.home_prefix(base)
        out = []
        for h, t in links:
            h = h.split('#')[0]
            t = (t or '').strip()
            if (frp.norm(h) not in seen and frp.bare(urlparse(h).netloc) == host and len(t) < 40 and SUB_T.search(t)
                    and not SUB_NOT.search(t) and not BAD.search(h) and h not in out and (pre[1] == '/' or frp.under(h, pre))):
                out.append(h)
        return out[:limit]
    text, links = add(home, why)
    for h in subs(home, links, 4):
        add(h, 'sub')
    host = frp.bare(urlparse(home).netloc)
    if why == 'dept' or host in shared:
        # 院系名录页：第一个站外的个人主页链接（"Personal website"、"Homepage"、或链接文字就是本人名字）
        name_t = {w.lower() for w in re.findall(r'[A-Za-z]{3,}', p['name'])}
        for h, t in links:
            t2 = re.sub(r'\s+', ' ', t or '').strip()
            hh = frp.bare(urlparse(h).netloc)
            if not h.startswith('http') or hh == host or hh in shared or frp.NEVER_HOST.search(hh) or BAD.search(h) or NOT_SUB.search(h):
                continue
            if PERSONAL_T.match(t2) or (t2 and set(w.lower() for w in re.findall(r'[A-Za-z]{3,}', t2)) >= name_t and len(t2) < 50):
                t3, l3 = add(h, 'personal')
                if t3 and sur and sur in deaccent(t3):
                    for h2 in subs(h, l3, 4):
                        add(h2, 'personal-sub')
                break
    return pages


def refetch_main():
    """--refetch-home：重抓 people_pages 里主页没内容 / 地址已换 / 是院系名录页的教授（跟随跳转、http→https、个人主页两跳）。"""
    profs = [p for p in load_profs() if p['homepage'].startswith('http') and p.get('status') != 'former']
    todo = refetch_targets(profs)
    print('refetch', len(todo), Counter(w for _, _, w in todo), flush=True)
    random.Random(9).shuffle(todo)
    shared = frp.shared_host_set()
    t0, done = time.time(), 0
    stats = Counter()

    def work(item):
        p, home, why = item
        f = os.path.join(PP, p['id'] + '.json')
        try:
            pages = crawl_home(p, home, why, shared)
        except Exception as e:
            return why, 'error ' + type(e).__name__
        old = json.load(open(f, encoding='utf-8')).get('pages', []) if os.path.exists(f) else []
        got = sum(len(x['text']) for x in pages)
        if got < sum(len(x.get('text') or '') for x in old) and why != 'moved':
            pages = old + [x for x in pages if x.get('via', '').startswith('personal')]  # 新抓的反而更少：保留原来的，只补个人主页
        with open(f, 'w', encoding='utf-8') as fh:
            json.dump(dict(pid=p['id'], pages=pages, refetched=why), fh, ensure_ascii=False)
        return why, 'ok' if got else 'empty'

    with cf.ThreadPoolExecutor(max_workers=48) as ex:
        for why, res in ex.map(work, todo):
            stats[(why, res)] += 1
            done += 1
            if done % 300 == 0:
                print(f'{done}/{len(todo)} {time.time() - t0:.0f}s', flush=True)
    print('done', dict(stats), f'{time.time() - t0:.0f}s')


def refetch_cv_main():
    """--refetch-cv：以前 PDF 只读前 15 页；文字已很长（可能被截断）的 CV 重抓一遍读全（fetch_pages 现在读到 80 页）。"""
    import glob
    todo = []
    for fn in glob.glob(os.path.join(ROOT, 'data/cache/pages', '*_cv.txt')):
        s = open(fn, encoding='utf-8', errors='replace').read()
        url = s.split('\n', 1)[0].replace('#URL ', '').strip()
        if url.startswith('http') and len(s) > 25000 and 'dblp' not in url:
            todo.append((fn, url, len(s)))
    random.Random(10).shuffle(todo)
    print('cv to refetch', len(todo), flush=True)
    st = Counter()

    def work(item):
        fn, url, n0 = item
        try:
            t, _, e = fp.fetch(url)
        except Exception as ex:
            return 'error ' + type(ex).__name__
        if len(t) > n0:
            with open(fn, 'w', encoding='utf-8', errors='replace') as fh:
                fh.write(f'#URL {url}\n' + t)
            return 'longer'
        return 'same' if t else (e or 'empty')
    with cf.ThreadPoolExecutor(max_workers=32) as ex:
        for r in ex.map(work, todo):
            st[r] += 1
    print('done', dict(st))


def main():
    args = sys.argv[1:]
    if '--follow' in args:
        return follow_main()
    if '--refetch-cv' in args:
        return refetch_cv_main()
    if '--refetch-home' in args:
        return refetch_main()
    if '--photos' in args:
        return photos_main()
    dry = int(args[args.index('--dry') + 1]) if '--dry' in args else 0
    freq = frp.link_freq()
    shared = frp.shared_host_set()
    sch = frp.school_sites()
    profs = [p for p in load_profs() if p['homepage'].startswith('http') and p.get('status') != 'former'
             and os.path.exists(os.path.join(PP, p['id'] + '.json'))]
    random.Random(5).shuffle(profs)
    if dry:
        n = 0
        prio = Counter()
        hosts = Counter()
        for p in profs:
            d = json.load(open(os.path.join(PP, p['id'] + '.json'), encoding='utf-8'))
            c = lab_candidates(d, freq, shared, sch.get(p['uni'], set()), frp.name_tokens(p['name']),
                               frp.bare(urlparse(p['homepage']).netloc))
            if c:
                n += 1
                prio[c[0][0]] += 1
                hosts[frp.bare(urlparse(c[0][1]).netloc)] += 1
                if dry > 0:
                    dry -= 1
                    print(p['id'], p['name'], p['homepage'])
                    for pr, h, t in c[:3]:
                        print(f'    {pr} {t[:40]!r:44} {h}')
        print('professors', len(profs), 'with lab link', n, 'prio', sorted(prio.items()))
        print('hosts used by >1', [h for h, c in hosts.most_common(30) if c > 1])
        return
    os.makedirs(OUT, exist_ok=True)
    if '--refresh' in args:
        # people_pages 重抓过（或规则变了）：实验室候选和缓存里的不一样（或以前没有）的重新抓
        for p in profs:
            rec = load(p['id'])
            d = json.load(open(os.path.join(PP, p['id'] + '.json'), encoding='utf-8'))
            c = lab_candidates(d, freq, shared, sch.get(p['uni'], set()), frp.name_tokens(p['name']),
                               frp.bare(urlparse(p['homepage']).netloc))
            if rec is None:
                continue
            old_url = (rec.get('lab') or {}).get('url')
            if c and c[0][1] != old_url:
                os.remove(path_of(p['id']))
    todo = [p for p in profs if not os.path.exists(path_of(p['id']))]
    print('professors', len(profs), 'remaining', len(todo), flush=True)
    t0, done, npages, nlab = time.time(), 0, 0, 0

    def work(p):
        try:
            d = json.load(open(os.path.join(PP, p['id'] + '.json'), encoding='utf-8'))
            rec = crawl(p, d, freq, shared, sch.get(p['uni'], set()))
        except Exception as e:  # 单人出错不影响整体
            rec = dict(pid=p['id'], lab=None, pages=[], err='crawl-' + type(e).__name__ + ': ' + str(e)[:80])
        rec = rec or dict(pid=p['id'], lab=None, pages=[])
        with gzip.open(path_of(p['id']), 'wt', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False)
        return len(rec['pages'])

    with cf.ThreadPoolExecutor(max_workers=32) as ex:
        for k in ex.map(work, todo):
            done += 1
            npages += k
            nlab += bool(k)
            if done % 200 == 0:
                print(f'{done}/{len(todo)} labs={nlab} pages={npages} {time.time() - t0:.0f}s', flush=True)
    print('done', done, 'labs', nlab, 'pages', npages, f'{time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
