"""从各校 CS 系官方教师名录（faculty directory）抓职称和照片，比个人主页更可靠。

每个学校一小段配置（名录网址；有分页的自动翻页）。解析用通用方法：在页面里找和教授名单
（同一学校）对得上的人名元素，往上找到这个人的"名片"块，取块里第一行像职称的短文字和第一张照片。
人名匹配不分大小写/重音，中间名、首字母可有可无，支持 "姓, 名" 写法。
职称规整沿用 extract_titles.py（保留 Teaching / Research / of the Practice 区别和 Emeritus，
去掉系名、Chair 等后缀）。需要 JS 才能显示的名录跳过。
遵守 robots.txt，同一主机两次请求至少间隔 2 秒；页面缓存在 data/cache/directories/，重跑不再联网。
输出: data/enrich/directory.json  {pid: {"ti": 职称, "ph": 照片绝对地址, "src": 名录网址}}
用法: python scripts/fetch_directories.py             抓取（用缓存）+ 解析 + 和 titles.json 对比
      python scripts/fetch_directories.py --refresh   忽略缓存重新抓
      python scripts/fetch_directories.py --only cmu  只处理某个学校（配置里的 tag）
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from urllib.parse import urljoin, urlparse

import lxml.html
import requests

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from fetch_pages import allowed, UA, HOST_GAP  # noqa: E402
from names import fold, key  # noqa: E402
from extract_titles import TITLE, norm  # noqa: E402

CACHE = os.path.join(ROOT, 'data/cache/directories')
RESULT = os.path.join(ROOT, 'data/enrich/directory.json')
TITLES = os.path.join(ROOT, 'data/enrich/titles.json')
MAX_PAGES = 60

# 每校配置：tag、学校名（和 professors.csv 一致）、名录网址（按优先级；同一人以先出现的为准）
SITES = [
    ('neu', 'Northeastern University', [
        'https://www.khoury.northeastern.edu/people/?filters%5B%5D=people_roles%7C931',   # 终身/终身轨
        'https://www.khoury.northeastern.edu/people/?filters%5B%5D=people_roles%7C928',   # 教学岗
        'https://www.khoury.northeastern.edu/people/?filters%5B%5D=people_roles%7C926']),  # 研究岗
    ('cmu', 'Carnegie Mellon University', [
        'https://csd.cmu.edu/people/faculty',
        'https://lti.cmu.edu/people/faculty/index.html',
        'https://hcii.cmu.edu/people/faculty',
        'https://www.ri.cmu.edu/people/faculty/']),
    ('mit', 'Massachusetts Institute of Technology', ['https://www.eecs.mit.edu/role/faculty/']),
    ('stanford', 'Stanford University', ['https://www.cs.stanford.edu/people-cs/faculty-name']),
    ('berkeley', 'Univ. of California - Berkeley', ['https://www2.eecs.berkeley.edu/Faculty/Lists/faculty.html']),
    ('uiuc', 'Univ. of Illinois at Urbana-Champaign', ['https://siebelschool.illinois.edu/about/people/all-faculty']),
    ('uw', 'University of Washington', [
        'https://www.cs.washington.edu/people/faculty-members/',
        'https://www.cs.washington.edu/people/emeritus/',
        'https://www.cs.washington.edu/people/adjunct/',
        'https://www.cs.washington.edu/people/affiliate/']),
    ('gatech', 'Georgia Institute of Technology', ['https://www.cc.gatech.edu/people/faculty']),
    ('cornell', 'Cornell University', ['https://www.cs.cornell.edu/directory']),
    ('ucsd', 'Univ. of California - San Diego', ['https://cse.ucsd.edu/people/faculty-profiles/faculty']),  # 只有照片
    ('umd', 'University of Maryland - College Park', ['https://www.cs.umd.edu/people/faculty']),
    ('utexas', 'University of Texas at Austin', ['https://www.cs.utexas.edu/people']),
    ('princeton', 'Princeton University', ['https://www.cs.princeton.edu/people/faculty']),
    ('columbia', 'Columbia University', ['https://www.cs.columbia.edu/people/faculty/']),
    ('nyu', 'New York University', ['https://cs.nyu.edu/dynamic/people/faculty/']),
    ('usc', 'University of Southern California', ['https://www.cs.usc.edu/directory/faculty/']),
    ('umass', 'University of Massachusetts Amherst', [
        'https://www.cics.umass.edu/about/directory?s=&field_person__profile_type_ref_target_id%5B351%5D=351']),
    ('purdue', 'Purdue University', ['https://www.cs.purdue.edu/people/faculty/index.html']),
    ('harvard', 'Harvard University', ['https://seas.harvard.edu/computer-science/people']),
    ('jhu', 'Johns Hopkins University', ['https://www.cs.jhu.edu/faculty/']),
    ('upenn', 'University of Pennsylvania', ['https://directory.seas.upenn.edu/computer-and-information-science/']),
    ('northwestern', 'Northwestern University', [
        'https://www.mccormick.northwestern.edu/computer-science/people/faculty/']),
    ('uci', 'Univ. of California - Irvine', ['https://cs.ics.uci.edu/faculty/']),
    ('utah', 'University of Utah', ['https://www.cs.utah.edu/people/faculty/']),
    ('ucsc', 'Univ. of California - Santa Cruz', ['https://engineering.ucsc.edu/people/faculty/']),  # 全工学院
    # 跳过：U Michigan（cse.engin.umich.edu 对脚本返回 403）、UW-Madison（名单由 JS 加载）、
    #       UCLA（cs.ucla.edu 名录跳到 samueli.ucla.edu 的 JS 搜索页）、Virginia Tech（名单由 JS 加载）
]

_last, _host_locks, _glock = {}, {}, threading.Lock()


def get(url, refresh=False):
    """带缓存的礼貌抓取：robots.txt + 同主机间隔 2 秒。返回 (html, 最终网址) 或 (None, 原因)。"""
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest()[:16] + '.json')
    if not refresh and os.path.exists(fn):
        d = json.load(open(fn, encoding='utf-8'))
        return d['html'], d['url']
    if not allowed(url):
        return None, 'robots'
    host = urlparse(url).netloc
    with _glock:
        lock = _host_locks.setdefault(host, threading.Lock())
    for attempt in range(3):
        with lock:
            wait = _last.get(host, 0) + HOST_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                r = requests.get(url, headers={'User-Agent': UA}, timeout=45)
            except requests.RequestException as e:
                err = type(e).__name__
                continue
            finally:
                _last[host] = time.time()
        if r.status_code != 200:
            return None, str(r.status_code)
        html = r.content.decode(r.encoding or 'utf-8', errors='replace') if r.encoding else r.text
        json.dump({'url': r.url, 'html': html}, open(fn, 'w', encoding='utf-8'), ensure_ascii=False)
        return html, r.url
    return None, err


PAGE_RX = re.compile(r'([?&]page=(?:%2C|,)?)(\d+)|(/page/)(\d+)(/)')


def page_urls(doc, url):
    """找分页链接（Drupal ?page=N 或 WordPress /page/N/），返回其余各页网址。"""
    base = urlparse(url)
    base_path = re.sub(r'/page/\d+/?', '/', base.path)
    found = {}
    for a in doc.iter('a'):
        h = a.get('href')
        if not h:
            continue
        h = urljoin(url, h)
        p = urlparse(h)
        if p.netloc != base.netloc or re.sub(r'/page/\d+/?', '/', p.path) != base_path:
            continue
        m = PAGE_RX.search(h)
        if m:
            found[int(m.group(2) or m.group(4))] = h
    if not found:
        return []
    zero = 0 in found
    tmpl = found[max(found)]
    out = []
    for n in range(1 if zero else 2, min(max(found), MAX_PAGES) + 1):
        out.append(PAGE_RX.sub(lambda m: (m.group(1) + str(n)) if m.group(1) else (m.group(3) + str(n) + m.group(5)),
                               tmpl, count=1))
    return out


# ---------- 人名匹配 ----------
STRIP_NAME = re.compile(r'\((?:[^)]*)\)|\b(?:Dr|Prof|Professor|Ph\.?\s?D|PhD|MBA|M\.?D)\b\.?|[»«›‹|•]', re.I)
NAMEISH = re.compile(r"^[A-Z][\w'’.\-]*(?:,?\s+[A-Za-z][\w'’.\-]*){1,5}$")
TITLE_WORDS = re.compile(r'(?i)\b(professor|lecturer|instructor|faculty|scientist|chair|dean|director|emerit)')


def clean_name(t):
    t = STRIP_NAME.sub(' ', t)
    t = re.sub(r'\s+', ' ', t).strip(' ,;:-')
    parts = [x.strip() for x in t.split(',')]
    if len(parts) == 2 and parts[1] and not re.fullmatch(r'(?i)jr\.?|sr\.?|i+|iv', parts[1]):
        t = parts[1] + ' ' + parts[0]  # "Acar, Umut" → "Umut Acar"
    elif len(parts) > 1:
        t = parts[0]
    return t


class Matcher:
    def __init__(self, profs):
        self.by_key = defaultdict(set)
        self.by_last_init = defaultdict(set)
        for p in profs:
            k = key(p['name'])
            self.by_key[k].add(p['id'])
            toks = k.split()
            if len(toks) == 2:
                self.by_last_init[(toks[1], toks[0][0])].add(p['id'])
            # 多段姓（"Berg-Kirkpatrick"、"De la Torre"）：最后两段也作为姓
            ft = [t for t in fold(p['name']).split() if len(t) > 1]
            if len(ft) >= 3:
                self.by_key[ft[0] + ' ' + ft[-1]].add(p['id'])

    def match(self, text):
        t = clean_name(text)
        if not t or len(t) > 60 or not NAMEISH.match(t) or TITLE_WORDS.search(t):
            return None
        k = key(t)
        hit = self.by_key.get(k)
        if hit and len(hit) == 1:
            return next(iter(hit))
        toks = k.split()
        if len(toks) == 2:
            hit = self.by_last_init.get((toks[1], toks[0][0]))
            if hit and len(hit) == 1:
                return next(iter(hit))
        return None


# ---------- 名片块解析 ----------
BLOCK_TAGS = {'p', 'div', 'br', 'li', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article',
              'dd', 'dt', 'figcaption', 'span', 'a', 'strong', 'em', 'ul', 'header', 'footer'}
BAD_IMG = re.compile(r'(?i)(logo|icon|sprite|placeholder|default[-_.]|blank|spacer|silhouette|no[-_]?photo|no[-_]?image|'
                     r'avatar|missing|wordmark|seal|banner|shims?/|1x1|\.svg(\?|$)|^data:)')


def lines_of(el):
    """块里的文字按行切开（块级标签、换行处断开）。"""
    out, cur = [], []

    def walk(e):
        if not isinstance(e.tag, str):
            if e.tail:
                cur.append(e.tail)
            return
        if e.tag in BLOCK_TAGS:
            out.append(''.join(cur)); cur.clear()
        if e.text:
            cur.append(e.text)
        for c in e:
            walk(c)
        if e.tag in BLOCK_TAGS:
            out.append(''.join(cur)); cur.clear()
        if e.tail:
            cur.append(e.tail)
    walk(el)
    out.append(''.join(cur))
    return [re.sub(r'\s+', ' ', x).strip() for x in out if x and x.strip()]


SENTENCE = re.compile(r'(?i)(is|was|are|has|have|her|his|their|research(?:es)?|works?|joined|received)')


def title_from(lines):
    """块里第一行像职称的短文字。同一行有多个时（MIT："ITT Career Development Professor ...; Associate
    Professor"），优先取单独成段的职称，冠名讲席（段首不是职称）靠后；Career Development 讲席不代表职级，跳过。"""
    for ln in lines:
        # 太长的一般是简介句子；冠名讲席的长职称（Stanford）允许到 260 字
        if len(ln) > 260 or (len(ln) > 120 and SENTENCE.search(ln)):
            continue
        if re.search(r'(?i)security\s+of\s+employment', ln):
            return 'Lecturer with Security of Employment'
        clean, named = [], []
        for m in TITLE.finditer(ln):
            if not m.group(2)[0].isupper():
                continue
            seg_start = max(ln.rfind(c, 0, m.start()) for c in ';,|/') + 1
            pre = ln[seg_start:m.start()].strip(' -–—:')
            if re.search(r'(?i)(former|previously|ph\.?d|student|postdoc|advis)', ln[:m.start()]):
                continue
            t = norm(m.group(1), m.group(2), m.group(3))
            if not t:
                continue
            if re.search(r'(?i)emerit', ln) and 'Emeritus' not in t:
                t += ' Emeritus'
            if not pre or re.fullmatch(r'(?i)(and|&|the|an?)', pre):
                clean.append(t)
            elif not re.search(r'(?i)career\s+development', pre):
                named.append(t)
        if clean or named:
            return (clean or named)[0]
    return None


def img_src(img):
    for a in ('data-src', 'data-lazy-src', 'data-original', 'src'):
        v = (img.get(a) or '').strip()
        if v and not v.startswith('data:'):
            return v
    ss = img.get('srcset') or img.get('data-srcset') or ''
    return ss.split(',')[0].split()[0] if ss.strip() else ''


def photo_from(card, page_url):
    for e in card.iter():
        if not isinstance(e.tag, str):
            continue
        src = ''
        if e.tag == 'img':
            src = img_src(e)
        elif e.get('data-bgset') or e.get('data-bg') or e.get('data-background-image'):
            v = e.get('data-bgset') or e.get('data-bg') or e.get('data-background-image')
            src = v.split(',')[0].split()[0]  # data-bgset 取第一个（通常最大）
        elif 'background-image' in (e.get('style') or ''):
            m = re.search(r'url\([\'"]?([^\'")]+)', e.get('style'))
            src = m.group(1) if m else ''
        if src and not BAD_IMG.search(src):
            return urljoin(page_url, src)
    return None


def sig(e):
    return (e.tag, (e.get('class') or '').strip())


def rel_path(e, top):
    # 叶子和父元素比较标签+class，再往上只比较标签（同一行左右两栏的 class 常不同）
    out = []
    while e is not None and e is not top:
        out.append(sig(e) if len(out) < 2 else e.tag)
        e = e.getparent()
    return tuple(out)


FUNC_WORDS = re.compile(r'(?i)(?<![\w])(and|of|the|for|in|on|with|to|&|\+)(?![\w])')


def name_like(e):
    t = re.sub(r'\s+', ' ', e.text_content()).strip()
    return 3 <= len(t) <= 60 and NAMEISH.match(clean_name(t) or 'x') and not TITLE_WORDS.search(t) \
        and not FUNC_WORDS.search(t) and '@' not in t and not re.search(r'\d', t)


def parse(html, page_url, matcher):
    doc = lxml.html.fromstring(html)
    for bad in doc.xpath('//script|//style|//noscript|//nav|//header[@role="banner"]'):
        bad.drop_tree()
    hits = []  # (元素, pid)
    for e in doc.iter():
        if not isinstance(e.tag, str) or e.tag in ('html', 'body'):
            continue
        t = e.text_content()
        if len(t) > 120:
            continue
        pid = matcher.match(re.sub(r'\s+', ' ', t).strip())
        if pid:
            hits.append((e, pid))
    # 只留最深的那个元素（外层包着同样文字的不要）
    hit_ids = {(id(e), pid) for e, pid in hits}
    deep = []
    for e, pid in hits:
        if any((id(c), pid) in hit_ids for c in e.iterdescendants()):
            continue
        deep.append((e, pid))
    name_els = {id(e): pid for e, pid in deep}
    out = {}
    for e, pid in deep:
        s = sig(e)
        card, a = e, e
        for _ in range(8):
            a = a.getparent()
            if a is None or a.tag in ('body', 'html'):
                break
            if len(a.text_content()) > 2500:
                break
            others = {name_els[id(d)] for d in a.iterdescendants() if id(d) in name_els} - {pid}
            # 同一位置结构（从 a 到名字元素的标签路径）出现第二个像人名的元素 → a 已经包住了好几张名片
            path = rel_path(e, a)
            same = sum(1 for d in a.iterdescendants() if isinstance(d.tag, str) and sig(d) == s
                       and rel_path(d, a) == path and name_like(d))
            if others or same > 1:
                break
            card = a
        own = re.sub(r'\s+', ' ', e.text_content()).strip()
        lines = [ln for ln in lines_of(card) if ln != own]
        ti, ph = title_from(lines), photo_from(card, page_url)
        prev = out.get(pid)
        if prev is None:
            out[pid] = {'tis': set(), 'ph': None}
        if ti:
            out[pid]['tis'].add(ti)
        out[pid]['ph'] = out[pid]['ph'] or ph
    return doc, out


def run_site(site, profs_by_uni, refresh):
    tag, uni, urls = site
    matcher = Matcher(profs_by_uni.get(uni, []))
    res, log = {}, []
    for u in urls:
        html, final = get(u, refresh)
        if html is None:
            log.append(f'{u} -> {final}')
            continue
        found = parse(html, final, matcher)[1]
        pages = [(final, found)]
        for pu in page_urls(lxml.html.fromstring(html), final):
            h2, f2 = get(pu, refresh)
            if h2 is None:
                log.append(f'{pu} -> {f2}')
                continue
            pages.append((f2, parse(h2, f2, matcher)[1]))
        # 同一名录里一个人对上好几种不同职称 → 多半是重名的另一个人，不用这份名录的职称
        tis, ph = defaultdict(set), {}
        for src, found in pages:
            for pid, v in found.items():
                tis[pid] |= v['tis']
                ph[pid] = ph.get(pid) or v['ph']
        amb = [pid for pid, t in tis.items() if len(t) > 1]
        for pid in tis:
            ti = next(iter(tis[pid])) if len(tis[pid]) == 1 else None
            cur = res.setdefault(pid, {})
            if not cur.get('ti') and ti:
                cur['ti'], cur['src'] = ti, u
            if not cur.get('ph') and ph[pid]:
                cur['ph'] = ph[pid]
                cur.setdefault('src', u)
            cur.setdefault('src', u)
        log.append(f'{u}: {len(pages)} 页' + (f'，职称有冲突 {len(amb)} 人' if amb else ''))
    # 同一张图给了好几个人 → 多半是占位图，去掉
    cnt = Counter(v.get('ph') for v in res.values() if v.get('ph'))
    for v in res.values():
        if v.get('ph') and cnt[v['ph']] > 1:
            del v['ph']
    return tag, uni, res, log


RANK = [('Emeritus', 'emeritus'), ('Lecturer', 'lecturer'), ('Teaching', 'teaching'), ('of Teaching', 'teaching'),
        ('Practice', 'practice'), ('Research', 'research'), ('Adjunct', 'adjunct'), ('Visiting', 'visiting'),
        ('Assistant', 'assistant'), ('Associate', 'associate')]


def rank(t):
    """粗粒度职级，用来和 titles.json 对比（忽略 Distinguished 之类的荣誉修饰）。"""
    if not t:
        return None
    for w, r in RANK:
        if w in t:
            sub = [r2 for w2, r2 in RANK[RANK.index((w, r)) + 1:] if w2 in t and r2 in ('assistant', 'associate')]
            return r + ('-' + sub[0] if sub and r in ('teaching', 'research', 'practice') else '')
    return 'scientist' if 'Scientist' in t else 'full'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--only')
    args = ap.parse_args()
    profs = load_profs()
    by_uni = defaultdict(list)
    for p in profs:
        by_uni[p['uni']].append(p)
    sites = [s for s in SITES if not args.only or s[0] == args.only]
    old = json.load(open(RESULT, encoding='utf-8')) if args.only and os.path.exists(RESULT) else {}
    result, stats = dict(old), []
    with cf.ThreadPoolExecutor(12) as ex:
        for tag, uni, res, log in ex.map(lambda s: run_site(s, by_uni, args.refresh), sites):
            for line in log:
                print(f'  [{tag}] {line}')
            for pid in [p['id'] for p in by_uni[uni]]:
                result.pop(pid, None)
            result.update(res)
            stats.append((tag, len(by_uni[uni]), len(res), sum(1 for v in res.values() if v.get('ti')),
                          sum(1 for v in res.values() if v.get('ph'))))
    result = dict(sorted(result.items(), key=lambda kv: int(kv[0][1:])))
    with open(RESULT, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=0)

    print(f'\n{"学校":12s} {"名单":>5s} {"匹配":>5s} {"职称":>5s} {"照片":>5s}')
    for tag, n, m, t, ph in stats:
        print(f'{tag:12s} {n:5d} {m:5d} {t:5d} {ph:5d}')
    tot = [sum(x[i] for x in stats) for i in range(1, 5)]
    print(f'{"合计":12s} {tot[0]:5d} {tot[1]:5d} {tot[2]:5d} {tot[3]:5d}')
    print('\n职称分布:')
    for t, c in Counter(v['ti'] for v in result.values() if v.get('ti')).most_common():
        print(f'  {c:5d}  {t}')

    if os.path.exists(TITLES):
        hp = json.load(open(TITLES, encoding='utf-8'))
        both = [pid for pid, v in result.items() if v.get('ti') and pid in hp]
        same = sum(1 for pid in both if hp[pid] == result[pid]['ti'])
        rsame = sum(1 for pid in both if rank(hp[pid]) == rank(result[pid]['ti']))
        print(f'\n和 titles.json（主页提取）对比：两边都有 {len(both)} 人，完全一致 {same}'
              f' ({same / max(len(both), 1):.1%})，职级一致 {rsame} ({rsame / max(len(both), 1):.1%})')
        diff = Counter((hp[pid], result[pid]['ti']) for pid in both if rank(hp[pid]) != rank(result[pid]['ti']))
        print('职级不一致最多的组合（主页 → 名录）:')
        for (a, b), c in diff.most_common(15):
            print(f'  {c:4d}  {a}  →  {b}')
        new = sum(1 for pid, v in result.items() if v.get('ti') and pid not in hp)
        print(f'名录新增（主页没提取到职称）: {new} 人')


if __name__ == '__main__':
    main()
