"""新入职（2025–2027）、CSRankings 还没收录的助理教授 → data/enrich/new_faculty.json。

输入: 人工搜集的候选表 CSV（列 name, university_csv, department, title, start_term, homepage, source_url,
      phd_institution, phd_advisor, csrankings_status, flags, notes, university），只用 flags 为空的行。
步骤:
  1. 去重: 和 data/raw/professors.csv、data/cache/csrankings/csrankings.csv 比对（names.key() 加昵称 / 中间名 /
     括号里的英文名 / 重音与 ł 等变体），已经在任何一张表里的人不再加（只在 CSRankings、被 professors.csv 过滤掉的
     也不加：网站上标 "not yet in CSRankings" 会是错的，单独报告）；学校必须和 professors.csv 里的写法完全一致。
  2. 联网（--fetch）: 抓主页、招聘公告、以及 CURATED 里列出的补充页面（CV、实验室页），缓存到
     data/cache/pages/new_faculty/（已在 .gitignore）。礼貌: 遵守 robots.txt；同一主机两次请求至少间隔 2 秒；
     通用 User-Agent；永不访问 dblp.org；不绕过任何反爬（403 / 验证页直接记错误）。
  3. --review FILE: 从缓存页面里自动找候选（职称句、PhD / advisor 句、招生句、Scholar 链接、头像图片），
     写成文本供人工核对。人工核对后的结果写在下面 CURATED 里（每一条导师引文、头像都人工看过）。
  4. 输出: 每人 {name, uni, title, start, areas, homepage, scholar, photo, phd_school,
     advisors:[{name, type, quote, url}], recruiting:{s, y, q, src, at} 或 null, source_url}。
     写出前校验: advisor / recruiting 的引文必须能在对应 URL 的缓存文字里找到（空白、引号归一后）；
     头像必须 https 可打开、是图片、短边 >= 80px、宽高比 0.5–2、没有 CORP 禁止跨站。校验不过的字段置空并打印。
用法:
  python scripts/prepare_new_faculty.py --input new_asst_profs_missing.csv --fetch      # 抓页面
  python scripts/prepare_new_faculty.py --input ... --review review.txt                  # 自动候选，供人工核对
  python scripts/prepare_new_faculty.py --input ...                                      # 写 new_faculty.json
"""
import argparse
import concurrent.futures as cf
import csv
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from names import key, fold, display_name  # noqa: E402

CACHE = os.path.join(ROOT, 'data/cache/pages/new_faculty')
OUT = os.path.join(ROOT, 'data/enrich/new_faculty.json')
UA = 'lineage-tree-research-script/0.1'
HOST_GAP = 2.0
MAX_BYTES = 6 * 1024 * 1024

AREAS = {'ai', 'vision', 'ml', 'nlp', 'ir', 'arch', 'networks', 'security', 'db', 'eda', 'embedded', 'hpc', 'mobile',
         'metrics', 'os', 'pl', 'se', 'theory', 'crypto', 'logic', 'bio', 'graphics', 'csed', 'ecom', 'hci', 'robotics',
         'visualization'}

# ---------------------------------------------------------------- 1. 去重

NICK = [('steve', 'steven', 'stephen'), ('tim', 'timothy'), ('joe', 'joseph'), ('alex', 'alexander', 'alexandru', 'aleksander'),
        ('sid', 'siddharth'), ('ben', 'benjamin'), ('dan', 'daniel'), ('mitch', 'mitchell'), ('will', 'william'),
        ('jen', 'jennifer', 'jenny'), ('gabe', 'gabriel'), ('ali',), ('paul',), ('lucy',), ('em', 'emma'),
        ('harry', 'hengyun'), ('tiffany', 'ge'), ('brandon', 'yushan'), ('jas', 'jasmine'), ('kate', 'kaitlyn', 'katie'),
        ('sasha', 'aleksander'), ('kirill',), ('chris', 'christopher'), ('david', 'dave'), ('xiang',)]
NICK_MAP = {}
for grp in NICK:
    for n in grp:
        NICK_MAP.setdefault(n, set()).update(grp)


def fold2(s):
    s = s.replace('ł', 'l').replace('Ł', 'L').replace('ø', 'o').replace('đ', 'd')
    return fold(s)


def name_keys(name):
    """一个名字的所有匹配键：名 + 姓，括号里的英文名、中间名、连字符姓的两半 / 合写、昵称。"""
    name = display_name(name)
    paren = re.findall(r'\(([^)]*)\)', name)
    base = re.sub(r'\([^)]*\)', ' ', name)
    toks = [t for t in fold2(base).split() if t not in {'jr', 'sr', 'ii', 'iii', 'iv'}]
    if not toks:
        return set()
    lasts = {toks[-1]}
    raw_last = re.sub(r'\([^)]*\)', ' ', name).split()[-1]
    if '-' in raw_last:  # Bruns-Smith → brunssmith / bruns smith（不单独用 smith，避免撞上 David A. Smith）
        lasts = {fold2(raw_last).replace(' ', '')}
    firsts = set(toks[:-1]) | {fold2(p).split()[0] for p in paren if fold2(p)}
    for f in list(firsts):
        firsts |= NICK_MAP.get(f, set())
    out = {f'{f} {L}' for f in firsts for L in lasts}
    if '-' not in raw_last:
        out.add(key(name))
    return out


def load_tables():
    with open(os.path.join(ROOT, 'data/raw/professors.csv'), encoding='utf-8', newline='') as f:
        profs = list(csv.DictReader(f))
    with open(os.path.join(ROOT, 'data/cache/csrankings/csrankings.csv'), encoding='utf-8', newline='') as f:
        csr = list(csv.DictReader(f))
    return profs, csr


def index(rows, name_col):
    idx = {}
    for r in rows:
        for k in name_keys(r[name_col]):
            idx.setdefault(k, []).append(r)
    return idx


def host_path(u):
    p = urlparse(u.strip().lower())
    q = re.sub(r'&?utm_[^&]*', '', p.query)  # 目录页用 ?q=<id> 区分人，不能丢
    return (p.netloc.removeprefix('www.') + p.path).rstrip('/') + ('?' + q if q else '')


def dedupe(cands):
    profs, csr = load_tables()
    unis = {p['University Name'] for p in profs}
    pidx, cidx = index(profs, 'name'), index(csr, 'name')
    homes = {host_path(p['homepage']): p for p in profs if p['homepage'].strip()}
    kept, dropped = [], []
    for c in cands:
        if c['university_csv'] not in unis:
            dropped.append((c, f"university {c['university_csv']!r} not in professors.csv"))
            continue
        keys = name_keys(c['name'])
        hit_p = {(p['name'], p['University Name'], p['status']) for k in keys for p in pidx.get(k, [])}
        hit_c = {(r['name'], r['affiliation']) for k in keys for r in cidx.get(k, [])}
        hp = CURATED.get(c['name'], {}).get('homepage') or c['homepage']
        if hp and host_path(hp) in homes:
            p = homes[host_path(hp)]
            hit_p.add((p['name'], p['University Name'], p['status']))
        # 同名不同人（人工核对过：研究方向、单位、dblp 编号都对不上）
        same_name_other = HOMONYMS.get(c['name'], set())
        hit_p = {h for h in hit_p if h[0] not in same_name_other}
        hit_c = {h for h in hit_c if h[0] not in same_name_other}
        if hit_p:
            dropped.append((c, f'already in professors.csv: {sorted(hit_p)}'))
        elif hit_c:
            dropped.append((c, f'in CSRankings (not in professors.csv): {sorted(hit_c)}'))
        else:
            kept.append(c)
    return kept, dropped


# CSRankings / professors.csv 里同名但不是同一个人（人工核对）
HOMONYMS = {
    'Ge (Tiffany) Wang': {"Ge Wang 0003"},                 # 西安交大
    'Jin Wang': {'Jin Wang 0009'},                         # 苏州大学
    'Hengyun (Harry) Zhou': {'H. Harry Zhou'},             # Towson University
    'Steven Xia': {'Stephen Xia'},                         # Northwestern，IoT / 移动计算；这位是 UIUC 软件工程 PhD
    'Xiang Cheng': {'Xiang Cheng 0003'},                   # 北邮，无线通信
}

# ---------------------------------------------------------------- 2. 礼貌抓取

_robots, _robots_lock, _last, _host_locks, _locks_lock = {}, threading.Lock(), {}, {}, threading.Lock()


def _host_lock(host):
    with _locks_lock:
        return _host_locks.setdefault(host, threading.Lock())


def _wait(host):
    w = _last.get(host, 0) + HOST_GAP - time.time()
    if w > 0:
        time.sleep(w)


def allowed(url):
    p = urlparse(url)
    base = f'{p.scheme}://{p.netloc}'
    with _host_lock(p.netloc):
        rp = _robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            _wait(p.netloc)
            try:
                r = requests.get(base + '/robots.txt', headers={'User-Agent': UA}, timeout=15)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            finally:
                _last[p.netloc] = time.time()
            _robots[base] = rp
    return rp.can_fetch(UA, url)


def polite_get(url, max_bytes=MAX_BYTES):
    host = urlparse(url).netloc
    with _host_lock(host):
        _wait(host)
        try:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=25, stream=True, allow_redirects=True)
            try:
                body = r.raw.read(max_bytes, decode_content=True)
            finally:
                r.close()
        finally:
            _last[host] = time.time()
    return r, body


def cache_path(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest()[:16] + '.json')


def pdf_text(body):
    from pypdf import PdfReader
    try:
        rd = PdfReader(io.BytesIO(body))
        return '\n'.join((pg.extract_text() or '') for pg in rd.pages[:30])
    except Exception:
        return ''


def fetch(url, refresh=False):
    """抓一个 URL，缓存 {url, final, status, ctype, html|text, err, at}。"""
    cp = cache_path(url)
    if os.path.exists(cp) and not refresh:
        with open(cp, encoding='utf-8') as f:
            d = json.load(f)
        if not d.get('err') or d['err'] in ('robots', 'never'):
            return d
    d = dict(url=url, final=url, status=0, ctype='', html='', text='', err='', at=time.strftime('%Y-%m'))
    if 'dblp' in urlparse(url).netloc.lower():
        d['err'] = 'never'
    elif not allowed(url):
        d['err'] = 'robots'
    else:
        try:
            r, body = polite_get(url)
            d.update(final=r.url, status=r.status_code, ctype=r.headers.get('content-type', '').lower())
            if r.status_code != 200:
                d['err'] = f'status {r.status_code}'
            elif 'pdf' in d['ctype'] or urlparse(r.url).path.lower().endswith('.pdf'):
                d['text'] = pdf_text(body)
            else:
                enc = r.encoding if r.encoding and r.encoding.lower() != 'iso-8859-1' else 'utf-8'
                d['html'] = body.decode(enc, errors='replace')
                if re.search(r'captcha|cf-challenge|Just a moment\.\.\.|Access denied', d['html'][:5000], re.I):
                    d['err'] = 'challenge'
        except Exception as e:
            d['err'] = type(e).__name__
    os.makedirs(CACHE, exist_ok=True)
    with open(cp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False)
    return d


def urls_for(c):
    cur = CURATED.get(c['name'], {})
    us = [cur.get('homepage') or c['homepage'], c['source_url'], *cur.get('extra', [])]
    us += [a['url'] for a in cur.get('advisors', [])]
    if cur.get('recruiting'):
        us.append(cur['recruiting']['src'])
    out = []
    for u in us:
        if u and u not in out:
            out.append(u)
    return out


def fetch_all(kept, refresh=False):
    todo = sorted({u for c in kept for u in urls_for(c)})
    with cf.ThreadPoolExecutor(12) as ex:
        for u, d in zip(todo, ex.map(lambda u: fetch(u, refresh), todo)):
            print(f"{d['err'] or d['status']:>12}  {u}")


# ---------------------------------------------------------------- 3. 解析 / 自动候选

class Page(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'svg', 'head'}
    BLOCK = {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'td', 'dd', 'dt', 'article',
             'header', 'footer', 'ul', 'ol', 'table', 'blockquote', 'figcaption'}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.links, self.imgs, self.skip, self._a = base, [], [], [], 0, None
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta' and a.get('content'):
            self.meta[(a.get('property') or a.get('name') or '').lower()] = a['content']
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append('\n')
        if tag == 'a':
            self._a = [urljoin(self.base, a.get('href') or ''), '']
        if tag == 'img':
            src = a.get('src') or a.get('data-src') or ''
            if src and not src.startswith('data:'):
                self.imgs.append(dict(src=urljoin(self.base, src), alt=a.get('alt') or '', w=a.get('width') or '',
                                      h=a.get('height') or '', cls=a.get('class') or ''))

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == 'a' and self._a:
            self.links.append(tuple(self._a))
            self._a = None
        if tag in self.BLOCK:
            self.out.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)
            if self._a is not None:
                self._a[1] += data


def parse(d):
    if d.get('text') and not d.get('html'):
        return d['text'], [], [], {}
    p = Page(d.get('final') or d['url'])
    try:
        p.feed(d.get('html') or '')
    except Exception:
        pass
    text = re.sub(r'[ \t\r\f\v\xa0]+', ' ', ''.join(p.out))
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text, p.links, p.imgs, p.meta


def norm(s):
    s = unicodedata.normalize('NFKC', s)
    s = s.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
    s = s.replace('–', '-').replace('—', '-').replace('­', '')
    return re.sub(r'\s+', ' ', s).strip().lower()


def page_text(url):
    p = cache_path(url)
    if not os.path.exists(p):
        return ''
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    return parse(d)[0] if not d.get('err') else ''


def sentences(text):
    for line in text.split('\n'):
        for s in re.split(r'(?<=[.!?])\s+(?=[A-Z])', line):
            s = s.strip()
            if 12 < len(s) < 500:
                yield s


PATS = {
    'title': re.compile(r'\b(assistant|associate)\s+(teaching\s+|research\s+)?professor\b', re.I),
    'start': re.compile(r'\b(join(ing|ed)?|start(ing|ed)?|begin(ning)?|will be)\b.{0,80}\b(20(2[4-8]))\b|\b(fall|spring|summer|january|july|august|september)\s+20(2[4-8])', re.I),
    'phd': re.compile(r'\b(ph\.?\s?d\.?|doctora[lt]e?|doctor of philosophy)\b', re.I),
    'advisor': re.compile(r'advis|supervis|mentor|working with|worked with|under the guidance|co-advis', re.I),
    'recruit': re.compile(r'\b(recruit|looking for|hiring|openings?|accepting|seeking|join (my|our)|prospective)\b.{0,120}\b(students?|ph\.?d)', re.I),
}


def review(kept, path):
    with open(path, 'w', encoding='utf-8') as out:
        for c in kept:
            out.write(f"\n{'=' * 100}\n{c['name']} | {c['university_csv']} | {c['title']} | {c['start_term']} | phd {c['phd_institution']} | adv {c['phd_advisor']}\n")
            for u in urls_for(c):
                cp = cache_path(u)
                if not os.path.exists(cp):
                    out.write(f'  [not fetched] {u}\n')
                    continue
                with open(cp, encoding='utf-8') as f:
                    d = json.load(f)
                out.write(f"--- {u} -> {d.get('final')} [{d.get('err') or d.get('status')}]\n")
                if d.get('err'):
                    continue
                text, links, imgs, meta = parse(d)
                last = c['name'].split()[-1].lower()
                first = re.sub(r'\(.*?\)', '', c['name']).split()[0].lower()
                near = norm(c['name']) in norm(text) or last in text.lower()
                out.write(f'  name on page: {near}; og:image {meta.get("og:image", "")}\n')
                for s in sentences(text):
                    tags = [k for k, p in PATS.items() if p.search(s)]
                    low = s.lower()
                    if tags and (last in low or first in low or re.search(r'\b(I|my|me|our)\b', s) or
                                 urlparse(u).netloc not in urlparse(c['source_url']).netloc):
                        out.write(f"  [{','.join(tags)}] {s[:400]}\n")
                for h, t in links:
                    if re.search(r'scholar\.google|/cv|cv\.pdf|vitae|resume|github\.io|people|faculty|homepage|lab', h + ' ' + t, re.I) \
                            and (last in (h + t).lower() or 'scholar' in h or re.search(r'\bcv\b|vitae', t, re.I)):
                        out.write(f'  link: {t.strip()[:60]!r} {h}\n')
                for im in imgs:
                    s = im['src'].lower()
                    if last in s or first in s or last in im['alt'].lower() or re.search(r'profile|headshot|portrait|photo|me\.|avatar', s):
                        out.write(f"  img: {im['src']} alt={im['alt'][:40]!r} {im['w']}x{im['h']}\n")
    print('review written to', path)


# ---------------------------------------------------------------- 4. 校验 + 输出

_photo_cache = {}


def check_photo(url):
    if not url:
        return None, 'empty'
    if url in _photo_cache:
        return _photo_cache[url]
    from PIL import Image
    u = 'https://' + url[7:] if url.startswith('http://') else url
    res = None, ''
    if not allowed(u):
        res = None, 'robots'
    else:
        try:
            r, body = polite_get(u, 8 * 1024 * 1024)
            if r.status_code != 200:
                res = None, f'status {r.status_code}'
            elif not r.headers.get('content-type', '').lower().startswith('image/'):
                res = None, 'not image: ' + r.headers.get('content-type', '')
            elif r.headers.get('cross-origin-resource-policy', '').strip().lower() in ('same-origin', 'same-site'):
                res = None, 'corp'
            else:
                w, h = Image.open(io.BytesIO(body)).size
                res = (u, '') if min(w, h) >= 80 and 0.5 <= w / h <= 2.0 else (None, f'size {w}x{h}')
        except Exception as e:
            res = None, type(e).__name__
    _photo_cache[url] = res
    return res


def fetched_at(url):
    """缓存里记录的抓取月份（"什么时候看到的"）。"""
    try:
        with open(cache_path(url), encoding='utf-8') as f:
            return json.load(f).get('at') or time.strftime('%Y-%m')
    except OSError:
        return time.strftime('%Y-%m')


def quote_ok(quote, url):
    t = norm(page_text(url))
    q = norm(quote)
    if q in t:
        return True
    # PDF 抽字常把换行处的连字符 / 空格弄丢：再比一次去掉所有非字母数字的版本
    sq = re.sub(r'[^a-z0-9]', '', q)
    return bool(sq) and sq in re.sub(r'[^a-z0-9]', '', t)


def build(kept):
    out, problems = [], []
    for c in kept:
        cur = CURATED.get(c['name'])
        if cur is None:
            problems.append(f"{c['name']}: not curated yet — skipped")
            continue
        if cur.get('skip'):
            problems.append(f"{c['name']}: skipped — {cur['skip']}")
            continue
        areas = cur.get('areas', [])
        bad = [a for a in areas if a not in AREAS]
        if bad or not 1 <= len(areas) <= 3:
            problems.append(f"{c['name']}: bad areas {areas}")
            areas = [a for a in areas if a in AREAS][:3]
        advisors = []
        for a in cur.get('advisors', []):
            if quote_ok(a['quote'], a['url']):
                advisors.append(dict(name=a['name'], type=a.get('type', 'phd'), quote=a['quote'], url=a['url']))
            else:
                problems.append(f"{c['name']}: advisor quote not found on page, dropped: {a['name']} @ {a['url']}")
        rec = cur.get('recruiting')
        if rec:
            if quote_ok(rec['q'], rec['src']):
                rec = dict(s=rec['s'], y=rec.get('y'), q=rec['q'], src=rec['src'], at=rec.get('at') or fetched_at(rec['src']))
            else:
                problems.append(f"{c['name']}: recruiting quote not found on page, dropped")
                rec = None
        photo = ''
        if cur.get('photo'):
            photo, why = check_photo(cur['photo'])
            if not photo:
                problems.append(f"{c['name']}: photo rejected ({why}): {cur['photo']}")
                photo = ''
        out.append(dict(name=cur.get('name', re.sub(r'\s+', ' ', c['name'])), uni=c['university_csv'],
                        title=cur.get('title') or c['title'], start=cur.get('start') or c['start_term'],
                        areas=areas, homepage=cur.get('homepage') or c['homepage'], scholar=cur.get('scholar', ''),
                        photo=photo, phd_school=cur.get('phd_school', c['phd_institution']), advisors=advisors,
                        recruiting=rec, source_url=cur.get('source_url') or c['source_url']))
    return out, problems


def load_candidates(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return [r for r in csv.DictReader(f) if not (r.get('flags') or '').strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--fetch', action='store_true')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--review')
    a = ap.parse_args()
    cands = load_candidates(a.input)
    kept, dropped = dedupe(cands)
    print(f'{len(cands)} candidates, {len(kept)} kept, {len(dropped)} dropped')
    for c, why in dropped:
        print(f"  DROP {c['name']} ({c['university_csv']}): {why}")
    if a.fetch:
        fetch_all(kept, a.refresh)
        return
    if a.review:
        review(kept, a.review)
        return
    out, problems = build(kept)
    for p in problems:
        print('  !', p)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write('\n')
    n = len(out)
    stat = {k: sum(1 for r in out if r[k]) for k in ('title', 'start', 'areas', 'homepage', 'scholar', 'photo',
                                                     'phd_school', 'advisors', 'recruiting')}
    print(f'wrote {n} rows to {os.path.relpath(OUT, ROOT)}; non-empty: {stat}')


# ---------------------------------------------------------------- 人工核对结果
# 每人: homepage / title / start / areas（1–3 个 CSRankings 子领域）/ scholar / photo / phd_school /
#       advisors[{name,type,quote,url}] / recruiting{s,y,q,src}（at 用运行月份）/ extra（额外要抓的页面，如 CV）/
#       skip（不收的原因）。导师 name 尽量写成 professors.csv 里的写法（Ed Felten → Edward Felten、
#       Dan Jurafsky → Daniel Jurafsky 等），quote 保留页面原文。每条导师引文、每张头像都人工看过。
def A(name, quote, url, type='phd'):
    return dict(name=name, type=type, quote=quote, url=url)


def R(s, y, q, src):
    return dict(s=s, y=y, q=q, src=src)


S = 'https://scholar.google.com/citations?user='

CURATED = {
    'Joseph Calandrino': dict(
        homepage='https://plaintextresponse.com/', title='Assistant Professor', start='2025', areas=['security'],
        photo='https://plaintextresponse.com/static/calandrino.png', phd_school='Princeton University',
        extra=['https://plaintextresponse.com', 'https://blog.citp.princeton.edu/author/calandrinogmail-com'],
        advisors=[A('Edward Felten', 'As long time readers will remember, he did his Ph.D. here, advised by Ed Felten.',
                    'https://blog.citp.princeton.edu/author/calandrinogmail-com')]),
    'Tim Dettmers': dict(
        title='Assistant Professor', start='2025', areas=['ml', 'nlp'], scholar=S + 'lHI3w5kAAAAJ',
        photo='https://timdettmers.com/wp-content/uploads/2025/12/profile_2026_400x400.png',
        phd_school='University of Washington',
        advisors=[A('Luke Zettlemoyer', 'I earn my PhD from University of Washington advised by Luke Zettlemoyer.',
                    'https://timdettmers.com/about/')]),
    'Akari Asai': dict(
        title='Assistant Professor', start='2026', areas=['nlp', 'ir'], scholar=S + 'gqB4u_wAAAAJ',
        photo='https://akariasai.github.io/assets/img/akari-asai-fall2026.jpg', phd_school='University of Washington',
        advisors=[A('Hannaneh Hajishirzi', 'Previously, I completed my Ph.D. at the University of Washington in 2025, advised by Hannaneh Hajishirzi, where I worked on retrieval-augmented language models and information-seeking systems.',
                    'https://akariasai.github.io/')],
        recruiting=R('yes', 2027, 'I am hiring PhD students in the upcoming application cycle for a Fall 2027 start.',
                     'https://akariasai.github.io/')),
    'Niloofar Mireshghallah': dict(
        homepage='https://mireshghallah.github.io/', title='Assistant Professor', start='2026',
        areas=['nlp', 'security'], scholar=S + 'WUCu45YAAAAJ',
        photo='https://mireshghallah.github.io/pics/pic4.jpg?v=20261102', phd_school='UC San Diego',
        extra=['https://mireshghallah.github.io/bio.html', 'https://www.lti.cs.cmu.edu/people/faculty/mireshghallah-niloofar.html',
               'https://mireshghallah.github.io/blog/why_i_joined_humansand.html'],
        advisors=[A('Taylor Berg-Kirkpatrick', 'I received my PhD from UC San Diego, advised by Taylor Berg-Kirkpatrick',
                    'https://mireshghallah.github.io/'),
                  A('Yejin Choi', 'Prior to that, I was a postdoctoral scholar at University of Washington, advised by Yejin Choi and Yulia Tsvetkov.',
                    'https://mireshghallah.github.io/', 'postdoc'),
                  A('Yulia Tsvetkov', 'Prior to that, I was a postdoctoral scholar at University of Washington, advised by Yejin Choi and Yulia Tsvetkov.',
                    'https://mireshghallah.github.io/', 'postdoc')]),
    'Steve Rathje': dict(
        title='Assistant Professor', start='Fall 2026', areas=['hci'], scholar=S + 'tw5jvawAAAAJ',
        photo='https://stevenrathje.com/authors/admin/avatar_hu7811619d72c09456a80ea32eaf6ede54_435760_270x270_fill_q75_lanczos_center.jpg',
        phd_school='University of Cambridge',
        extra=['https://stevenrathje.com/media/Steve%20Rathje%20CV%202026.pdf'],
        advisors=[A('Sander van der Linden', 'PhD in Psychology Advisor: Sander van der Linden',
                    'https://stevenrathje.com/media/Steve%20Rathje%20CV%202026.pdf'),
                  A('Jay Van Bavel', 'Postdoctoral Researcher New York University Advisor: Jay Van Bavel',
                    'https://stevenrathje.com/media/Steve%20Rathje%20CV%202026.pdf', 'postdoc')]),
    'Mengzhou Xia': dict(
        homepage='https://xiamengzhou.github.io/', title='Assistant Professor', start='2026', areas=['nlp', 'ml'],
        scholar=S + 'fNexhaUAAAAJ', photo='https://xiamengzhou.github.io/images/Mengzhou-small.jpg',
        phd_school='Princeton University',
        advisors=[A('Danqi Chen', 'I recently completed my Ph.D. in Computer Science at Princeton University as part of Princeton NLP and Princeton Language Intelligence (PLI), advised by Prof. Danqi Chen.',
                    'https://xiamengzhou.github.io/')]),
    'Steven Xia': dict(
        name='Chunqiu Steven Xia', homepage='https://steven.cs.illinois.edu/', title='Assistant Professor', start='2026',
        areas=['se'], scholar=S + '-PCjRp8AAAAJ',
        photo='https://scsdeans.cs.cmu.edu/new-faculty/resources/2026/Xia.Steven%20Chunqiu.jpg',
        phd_school='University of Illinois Urbana-Champaign',
        advisors=[A('Lingming Zhang', 'I am a final year Computer Science PhD student at UIUC advised by Lingming Zhang.',
                    'https://steven.cs.illinois.edu/')]),
    'Willow Ahrens': dict(
        homepage='https://willowahrens.net/', title='Assistant Professor', start='Aug 2025', areas=['pl', 'hpc'],
        scholar=S + '0QFbKQ4AAAAJ', photo='https://willowahrens.net/assets/images/bio-photo.jpeg', phd_school='MIT',
        extra=['https://krellinst.org/csgf/alum/ahrens2017'],
        advisors=[A('Saman Amarasinghe', 'Field of Study: Computer Science Advisor: Saman Amarasinghe',
                    'https://krellinst.org/csgf/alum/ahrens2017')]),
    'Francisco Romero': dict(
        homepage='https://www.franciscoaromero.com/home', title='Assistant Professor', start='Fall 2025',
        areas=['os', 'db'], scholar=S + 'mSw8KfIAAAAJ',
        photo='https://scs.gatech.edu/sites/default/files/styles/profile_150x180_/public/images/profiles/2025-08/faromero_headshot.jpg?itok=YFTw-fLx',
        phd_school='Stanford University', extra=['https://scs.gatech.edu/people/francisco-romero'],
        advisors=[A('Christoforos E. Kozyrakis', 'I received my MS and PhD in EE at Stanford University, advised by Christos Kozyrakis and supported by the Stanford DARE Fellowship.',
                    'https://www.franciscoaromero.com/home')]),
    'Siddharth Karamcheti': dict(
        homepage='https://siddkaramcheti.com/', title='Assistant Professor', start='Fall 2026', areas=['robotics', 'ml'],
        scholar=S + 'L5v2PHAAAAAJ', photo='https://www.siddkaramcheti.com/assets/img/sidd.jpg',
        phd_school='Stanford University',
        advisors=[A('Dorsa Sadigh', 'Timeline: I completed my PhD in Computer Science at Stanford University in 2025 where I was co-advised by Dorsa Sadigh and Percy Liang.',
                    'https://siddkaramcheti.com/'),
                  A('Percy Liang', 'Timeline: I completed my PhD in Computer Science at Stanford University in 2025 where I was co-advised by Dorsa Sadigh and Percy Liang.',
                    'https://siddkaramcheti.com/')]),
    'Veronica Rivera': dict(
        homepage='https://vrivera2017.github.io/', title='Assistant Professor', start='Fall 2026',
        areas=['security', 'hci'], photo='https://vrivera2017.github.io/images/rivera_headshot1.jpg',
        phd_school='UC Santa Cruz', extra=['https://vrivera2017.github.io/docs/Rivera_CV_June26.pdf'],
        advisors=[A('David Lee', 'I completed my PhD in Computational Media at the University of California, Santa Cruz, working with David Lee and was a visiting PhD student at the Max Planck Institute for Software Systems with Elissa Redmiles.',
                    'https://vrivera2017.github.io/'),
                  A('Zakir Durumeric', 'Embedded Ethics Postdoctoral Fellow, Computer Science, Institute for Human-Centered AI (HAI) & McCoy Family Center for Ethics in Society Advisor: Zakir Durumeric',
                    'https://vrivera2017.github.io/docs/Rivera_CV_June26.pdf', 'postdoc'),
                  A('Yixin Zou', 'Max Planck Institute for Security and Privacy, Postdoctoral Researcher Advisor: Yixin Zou',
                    'https://vrivera2017.github.io/docs/Rivera_CV_June26.pdf', 'postdoc')],
        recruiting=R('yes', 2027, 'I am recruiting PhD students to start in Fall 2027.', 'https://vrivera2017.github.io/')),
    'Alex Ozdemir': dict(
        title='Assistant Professor', start='Fall 2026', areas=['crypto', 'security', 'pl'],
        photo='https://faculty.cc.gatech.edu/~aozdemir/images/headshot-tux.png', phd_school='Stanford University',
        advisors=[A('Clark Barrett', 'Before that, I got my PhD at Stanford, advised by Clark Barrett and Dan Boneh.',
                    'https://faculty.cc.gatech.edu/~aozdemir/'),
                  A('Dan Boneh', 'Before that, I got my PhD at Stanford, advised by Clark Barrett and Dan Boneh.',
                    'https://faculty.cc.gatech.edu/~aozdemir/')],
        recruiting=R('yes', None, 'I am recruiting students and postdocs.', 'https://faculty.cc.gatech.edu/~aozdemir/')),
    'Ge (Tiffany) Wang': dict(
        homepage='https://tiffanygewang.github.io/', title='Assistant Professor', start='Aug 2025', areas=['hci'],
        scholar=S + 'DVV3PDIAAAAJ', photo='https://tiffanygewang.github.io/assets/img/prof_pic.jpg',
        phd_school='University of Oxford',
        recruiting=R('yes', 2027, 'I’m looking for motivated PhD students for Fall 2027 and always excited to collaborate with undergraduate and graduate students interested in Human-Computer Interaction and Human-Centered AI.',
                     'https://tiffanygewang.github.io/')),
    'Daniel Alabi': dict(
        homepage='https://alabidan.me/', title='Assistant Professor', start='2025', areas=['security', 'theory'],
        photo='https://alabidan.me/img/alabidan_me_photo_smaller.png', phd_school='Harvard University'),
    'Mitchell Gordon': dict(
        homepage='https://mitchellg.github.io/', title='Assistant Professor', start='2025', areas=['hci', 'ml'],
        scholar=S + 'pMkqt6sAAAAJ', photo='https://mitchellg.github.io/images/face_3.png',
        phd_school='Stanford University', extra=['https://mitchellg.github.io/assets/cv.pdf'],
        advisors=[A('Michael S. Bernstein', 'Ph.D., Computer Science Advisors: Michael Bernstein and James Landay',
                    'https://mitchellg.github.io/assets/cv.pdf'),
                  A('James A. Landay', 'Ph.D., Computer Science Advisors: Michael Bernstein and James Landay',
                    'https://mitchellg.github.io/assets/cv.pdf')]),
    'Bailey Flanigan': dict(
        homepage='https://baileyflanigan.com/', title='Miller (1922) Career Development Assistant Professor', start='2025',
        areas=['ecom'], scholar=S + 'h0PIjYMAAAAJ', phd_school='Carnegie Mellon University',
        advisors=[A('Ariel Procaccia', 'I completed my PhD in computer science at Carnegie Mellon University in 2024, where I was extremely lucky to have been advised by Ariel Procaccia.',
                    'https://baileyflanigan.com/')]),
    'Yunha Hwang': dict(
        homepage='https://yunhahwang.com/', title='Assistant Professor', start='2025', areas=['bio', 'ml'],
        scholar=S + 'Xu1L9HYAAAAJ',
        photo='https://images.squarespace-cdn.com/content/v1/64348e7d55843c42b422b711/10921677-8ff0-4586-a333-dd89aacc8c0d/Hwang-3.jpg',
        phd_school='Harvard University',
        recruiting=R('yes', None, 'I am looking for PhD students and postdocs to join my lab, please email me if you are interested.',
                     'https://yunhahwang.com/')),
    'Hengyun (Harry) Zhou': dict(
        homepage='https://zphy.github.io/', title='Assistant Professor', start='2026', areas=['arch'],
        photo='https://www.eecs.mit.edu/wp-content/uploads/2026/03/Zhou_Hengyun_Harry-scaled-e1778186757540-230x230.jpeg',
        phd_school='Harvard University', extra=['https://cqe.mit.edu/hengyun-harry-zhou/', 'https://www.eecs.mit.edu/people/harryzhou/'],
        advisors=[A('Mikhail Lukin', 'Since I really enjoyed that experience, I decided to do my PhD in Mikhail Lukin’s lab at Harvard—a group that intertwines theory and experiments.',
                    'https://cqe.mit.edu/hengyun-harry-zhou/')]),
    'Jas Brooks': dict(
        homepage='https://jasbrooks.net/', title='Assistant Professor', start='Sep 2026', areas=['hci'],
        scholar=S + 'WA6Hrt8AAAAJ', photo='https://jasbrooks.com/wp-content/uploads/2024/07/2024June21_423.png',
        phd_school='University of Chicago',
        advisors=[A('Pedro Lopes', 'They received a Ph.D. (2025) and B.S. (2016) in Computer Science from the University of Chicago, where they were advised by Professor Pedro Lopes.',
                    'https://jasbrooks.net/')],
        recruiting=R('yes', 2026, 'I am recruiting multiple PhD students and one Postdoc for Summer/Fall 2026!', 'https://jasbrooks.net/')),
    'Zana Buçinca': dict(
        homepage='https://zbucinca.github.io/', title='Assistant Professor', start='Sep 2026', areas=['hci', 'ai'],
        scholar=S + 'Q_RQYDgAAAAJ', photo='https://zbucinca.github.io/assets/img/zbucinca_updated.jpeg',
        phd_school='Harvard University',
        advisors=[A('Krzysztof Gajos', 'I received my PhD in Computer Science from Harvard, where I was fortunate to be advised by Professor Krzysztof Gajos.',
                    'https://zbucinca.github.io/')],
        recruiting=R('no', 2027, 'I am unlikely to accept new PhD students for Fall 2027, but if you have independent funding and would like to work together, please reach out.',
                     'https://zbucinca.github.io/')),
    'David Bruns-Smith': dict(
        homepage='https://brunssmith.com/', title='Assistant Professor', start='Sep 2026', areas=['ml'],
        scholar=S + 'TmmHmzAAAAAJ', photo='https://brunssmith.com/assets/images/website_shot_600.jpg',
        phd_school='UC Berkeley'),
    'Alexandru Damian': dict(
        homepage='https://alex-damian.github.io/', title='Assistant Professor', start='Sep 2026', areas=['ml', 'theory'],
        scholar=S + 'dymr3s8AAAAJ', photo='https://alex-damian.github.io/headshot.jpg', phd_school='Princeton University',
        advisors=[A('Jason D. Lee', 'I received my Ph.D. in Applied and Computational Mathematics at Princeton University under the supervision of Jason D. Lee',
                    'https://alex-damian.github.io/')],
        recruiting=R('yes', 2027, 'I am actively recruiting Ph.D. students to start in Fall 2027.', 'https://alex-damian.github.io/')),
    'Silviu Pitis': dict(
        homepage='https://silviupitis.com/', title='Assistant Professor', start='Fall 2026', areas=['ml', 'ai'],
        scholar=S + 'oYlo1ycAAAAJ', photo='https://silviupitis.com/assets/img/headshot.jpg', phd_school='University of Toronto',
        advisors=[A('Jimmy Ba', 'I received my PhD in computer science from the University of Toronto, advised by Jimmy Ba.',
                    'https://silviupitis.com/'),
                  A('Sheila McIlraith', 'Previously, I was a CIFAR AI Safety Postdoctoral Fellow at the Vector Institute, where I worked with Sheila McIlraith.',
                    'https://silviupitis.com/', 'postdoc')],
        recruiting=R('no', None, 'I am not recruiting PhD students this cycle.', 'https://silviupitis.com/')),
    'Gabriel Poesia': dict(
        homepage='https://gpoesia.com/', title='Assistant Professor', start='Fall 2026', areas=['ai', 'ml'],
        photo='https://gpoesia.com/img/me.jpg', phd_school='Stanford University',
        advisors=[A('Noah Goodman', 'PhD in Computer Science (2019-2025) at Stanford, advised by Noah Goodman.', 'https://gpoesia.com/')]),
    'Preston Culbertson': dict(
        homepage='https://pculbertson.github.io/', title='Assistant Professor', start='Fall 2025', areas=['robotics'],
        scholar=S + '8v2kg_0AAAAJ', photo='https://pculbertson.github.io/assets/img/preston_culbertson.jpg',
        phd_school='Stanford University',
        recruiting=R('yes', None, "Prospective students: I'm looking for motivated PhD students to join my lab!", 'https://pculbertson.github.io/')),
    'Angelina Wang': dict(
        homepage='https://angelina-wang.github.io/', title='Assistant Professor', start='Fall 2025', areas=['ml', 'hci'],
        scholar=S + 'cGemfcYAAAAJ', photo='https://angelina-wang.infosci.cornell.edu/website_files/headshot.jpeg',
        phd_school='Princeton University', extra=['https://angelina-wang.github.io/files/cv.pdf'],
        advisors=[A('Olga Russakovsky', 'earned my PhD in Computer Science at Princeton University advised by the wonderful Olga Russakovsky',
                    'https://angelina-wang.github.io/'),
                  A('Sanmi Koyejo', 'Postdoctoral Fellow, Stanford University Advisors: Sanmi Koyejo, Daniel E. Ho',
                    'https://angelina-wang.github.io/files/cv.pdf', 'postdoc'),
                  A('Daniel E. Ho', 'Postdoctoral Fellow, Stanford University Advisors: Sanmi Koyejo, Daniel E. Ho',
                    'https://angelina-wang.github.io/files/cv.pdf', 'postdoc')],
        recruiting=R('yes', None, 'I am currently recruiting both PhD students and postdocs.', 'https://angelina-wang.github.io/')),
    'Benjamin Shestakofsky': dict(
        homepage='https://infosci.cornell.edu/people/benjamin-shestakofsky', title='Assistant Professor', start='Fall 2025',
        areas=['hci'],
        photo='https://infosci.cornell.edu/sites/default/files/styles/profile_hero/public/images/people/Ben_Shestakofsky-24_final-2.jpg?h=57024e64&itok=9l8Q_Ns4',
        phd_school='UC Berkeley', extra=['https://infosci.cornell.edu/news-stories/meet-new-faculty-benjamin-shestakofsky']),
    'Kaitlyn Zhou': dict(
        homepage='https://katezhou.github.io/', title='Assistant Professor', start='2026', areas=['nlp', 'hci'],
        scholar=S + 'SQAK2mwAAAAJ', photo='https://katezhou.github.io/assets/profile-pics/profile_picture.png',
        phd_school='Stanford University', extra=['https://infosci.cornell.edu/people/kaitlyn-zhou'],
        advisors=[A('Daniel Jurafsky', 'I graduated with a Ph.D. in Computer Science from Stanford University, advised by Dan Jurafsky.',
                    'https://katezhou.github.io/')],
        recruiting=R('yes', None, "I'm recruiting PhD students in NLP and HCI in both computer science and information science!",
                     'https://katezhou.github.io/')),
    'Ayush Sekhari': dict(
        homepage='https://ayush.sekhari.com/', title='Assistant Professor', start='Nov 2026', areas=['ml', 'theory'],
        scholar=S + 'jH9i188AAAAJ', photo='https://ayush.sekhari.com/images/my_picture.png', phd_school='Cornell University',
        advisors=[A('Karthik Sridharan', 'He earned his Ph.D. in Computer Science from Cornell University, advised by Prof. Karthik Sridharan and Prof. Robert D. Kleinberg',
                    'https://ayush.sekhari.com/'),
                  A('Robert D. Kleinberg', 'He earned his Ph.D. in Computer Science from Cornell University, advised by Prof. Karthik Sridharan and Prof. Robert D. Kleinberg',
                    'https://ayush.sekhari.com/'),
                  A('Alexander Rakhlin', "He completed his postdoctoral research at MIT's Institute for Data, Systems, and Society (IDSS), where he worked with Prof. Alexander (Sasha) Rakhlin.",
                    'https://ayush.sekhari.com/', 'postdoc')],
        recruiting=R('yes', None, 'Prospective Mentees: I am recruiting new PhD students and postdocs at Cornell Tech.',
                     'https://ayush.sekhari.com/')),
    'Sagar Karandikar': dict(
        homepage='https://sagark.org/', title='Assistant Professor', start='Fall 2025', areas=['arch'],
        scholar=S + 'APtrU-cAAAAJ', photo='https://sagark.org/assets/images/SagarK_square_web.jpg', phd_school='UC Berkeley',
        extra=['https://www2.eecs.berkeley.edu/Pubs/TechRpts/2024/EECS-2024-222.html'],
        advisors=[A('Krste Asanović', 'Advisors: Krste Asanović', 'https://www2.eecs.berkeley.edu/Pubs/TechRpts/2024/EECS-2024-222.html')]),
    'Lucy Li': dict(homepage='https://lucy3.github.io/'),  # 已在 professors.csv（名字写作 Li Lucy），去重时会被丢掉
    'Jennifer Volk': dict(
        homepage='https://sites.google.com/view/jennifervolk', title='John D. Wiley Assistant Professor', start='Jan 2025',
        areas=['arch', 'eda'], phd_school='UC Santa Barbara',
        recruiting=R('yes', None, 'I have open positions for students—prospective graduate students are encouraged to apply here.',
                     'https://sites.google.com/view/jennifervolk')),
    'Wei Zhan': dict(
        homepage='https://cs.purdue.edu/homes/weizhan/', title='Assistant Professor', start='Fall 2025', areas=['theory'],
        scholar=S + 'eyWN04cAAAAJ', photo='https://cs.purdue.edu/homes/weizhan/images/profile.jpg',
        phd_school='Princeton University',
        advisors=[A('Ran Raz', 'I received my Ph.D. from Princeton University, where I was honored to be advised by Ran Raz.',
                    'https://cs.purdue.edu/homes/weizhan/')]),
    'Zongwei Zhou': dict(
        title='Assistant Professor', start='Jul 2026', areas=['vision', 'bio'], scholar=S + 'JVOeczAAAAAJ',
        photo='https://static.wixstatic.com/media/deaea1_e82c29aef6634c749bf673f13032e49a~mv2.jpg',
        phd_school='Arizona State University',
        advisors=[A('Jianming Liang', "Towards Annotation-Efficient Deep Learning for Computer-Aided Diagnosis AMIA Doctoral Dissertation Award President's Awards for Innovation Advised by Dr. Jianming Liang",
                    'https://www.zongweiz.com/')]),
    'Ali Vakilian': dict(homepage='https://www.mit.edu/~vakilian/',
                         skip='homepage now lists him at Rutgers Business School; Virginia Tech appears under "Previously"'),
    'Jiaqi Leng': dict(
        homepage='https://jiaqileng.github.io/', title='Assistant Professor', start='Fall 2026', areas=['theory'],
        scholar=S + 'Rr83RUsAAAAJ', photo='https://jiaqileng.github.io/assets/img/profile.jpg',
        phd_school='University of Maryland, College Park',
        advisors=[A('Xiaodi Wu', 'I earned my B.Sc. from the University of Hong Kong and my Ph.D. from the University of Maryland in 2024 under the supervision of Xiaodi Wu.',
                    'https://jiaqileng.github.io/')],
        recruiting=R('yes', 2027, 'I plan to recruit several Ph.D. students to start in fall 2027, so please reach out if you’re interested.',
                     'https://jiaqileng.github.io/')),
    'Paul Krogmeier': dict(
        homepage='https://www.colorado.edu/cs/paul-krogmeier', title='Assistant Professor', start='Fall 2026',
        areas=['pl', 'logic'],
        photo='https://www.colorado.edu/cs/sites/default/files/styles/large_image_style/public/2025-08/me-march-2025.jpg?itok=1UWExUAE',
        phd_school='University of Illinois Urbana-Champaign',
        advisors=[A('P. Madhusudan', 'Paul has a PhD in computer science from the University of Illinois where he was advised by Madhusudan Parthasarathy.',
                    'https://www.colorado.edu/cs/paul-krogmeier')]),
    'Siyuan Niu': dict(
        homepage='https://www.ece.ucf.edu/person/siyuan-niu/', title='Assistant Professor', start='Spring 2025',
        areas=['arch'], phd_school='University of Montpellier'),
    'Jongha Ryu': dict(
        homepage='https://jongharyu.github.io/', title='Wormley Family Early Career Assistant Professor', start='Fall 2026',
        areas=['ml'], scholar=S + '5ZYeWgcAAAAJ', photo='https://jongharyu.github.io/assets/img/headshot.jpg',
        phd_school='UC San Diego',
        advisors=[A('Young-Han Kim', 'I received my Ph.D. in Electrical Engineering from UC San Diego, advised by Young-Han Kim and Sanjoy Dasgupta, with support from the Kwanjeong Educational Foundation.',
                    'https://jongharyu.github.io/'),
                  A('Sanjoy Dasgupta', 'I received my Ph.D. in Electrical Engineering from UC San Diego, advised by Young-Han Kim and Sanjoy Dasgupta, with support from the Kwanjeong Educational Foundation.',
                    'https://jongharyu.github.io/')],
        recruiting=R('yes', 2027, 'To prospective students: I plan to recruit a PhD student to join my group in Fall 2027.',
                     'https://jongharyu.github.io/')),
    'Pei Wu': dict(homepage='https://www.eecs.psu.edu/departments/directory-detail-g.aspx?q=pkw5226',
                   skip='Penn State lists him among hires who started between Feb 1, 2024 and Mar 27, 2025; start not confirmed inside 2025–2027'),
    'Emma Dauterman': dict(
        homepage='https://cs.stanford.edu/~edauterman/', title='Assistant Professor', start='Fall 2025',
        areas=['security', 'os'], photo='https://cs.stanford.edu/~edauterman/emma_240.jpg', phd_school='UC Berkeley',
        extra=['https://cs.stanford.edu/~edauterman/bio.txt'],
        advisors=[A('Raluca A. Popa', 'Before that, I completed my Ph.D. in computer science at UC Berkeley where I was advised by Raluca Ada Popa and Ion Stoica.',
                    'https://cs.stanford.edu/~edauterman/'),
                  A('Ion Stoica', 'Before that, I completed my Ph.D. in computer science at UC Berkeley where I was advised by Raluca Ada Popa and Ion Stoica.',
                    'https://cs.stanford.edu/~edauterman/'),
                  A('Henry Corrigan-Gibbs', 'She was a postdoc at MIT with Henry Corrigan-Gibbs',
                    'https://cs.stanford.edu/~edauterman/bio.txt', 'postdoc')]),
    'Nirav Atre': dict(
        homepage='https://www.niravatre.com/', title='Assistant Professor', start='Fall 2026', areas=['networks', 'os'],
        scholar=S + 'Py55TXMAAAAJ', photo='https://www.niravatre.com/assets/avatar.png', phd_school='Carnegie Mellon University',
        extra=['https://niravatre.pages.dev/', 'https://profiles.stanford.edu/nirav-atre?tab=bio', 'https://niravatre.pages.dev/files/thesis2025-atre.pdf'],
        advisors=[A('Justine Sherry', 'Thesis Committee: Justine Sherry (Chair)', 'https://niravatre.pages.dev/files/thesis2025-atre.pdf')]),
    'Jiangpeng He': dict(
        homepage='https://jiangpenghe.github.io/', title='Assistant Professor', start='Fall 2025', areas=['vision', 'ml'],
        scholar=S + 'YEsCw1IAAAAJ', photo='https://jiangpenghe.github.io/assets/img/prof_pic.jpg', phd_school='Purdue University',
        advisors=[A('Fengqing Maggie Zhu', 'I earned my Ph.D. in Electrical and Computer Engineering from Purdue University, in the Video and Image Processing Lab (VIPER), where I had the privilege of being advised by Prof. Fengqing Maggie Zhu and Prof. Edward Delp.',
                    'https://jiangpenghe.github.io/'),
                  A('Edward Delp', 'I earned my Ph.D. in Electrical and Computer Engineering from Purdue University, in the Video and Image Processing Lab (VIPER), where I had the privilege of being advised by Prof. Fengqing Maggie Zhu and Prof. Edward Delp.',
                    'https://jiangpenghe.github.io/'),
                  A('Hermano Igo Krebs', 'Previously, I was a Postdoctoral Associate at MIT, working with Dr. Hermano Igo Krebs on Smart Rehabilitation Robotics.',
                    'https://jiangpenghe.github.io/', 'postdoc')],
        recruiting=R('yes', None, 'I am seeking highly motivated Ph.D students.', 'https://jiangpenghe.github.io/')),
    'Yiwei Zhao': dict(
        homepage='https://zhaoyw007.github.io/', title='Assistant Professor', start='Fall 2026', areas=['arch', 'hpc'],
        scholar=S + 'ESIzPeQAAAAJ', photo='https://zhaoyw007.github.io/assets/images/profile.JPEG',
        phd_school='Carnegie Mellon University',
        advisors=[A('Phillip B. Gibbons', 'I received my Ph.D. from Carnegie Mellon University, advised by Prof. Phillip B. Gibbons in the Parallel Data Lab and the CMU Parlay Lab.',
                    'https://zhaoyw007.github.io/')],
        recruiting=R('yes', None, "I am seeking highly motivated students at all levels, including Ph.D., master's, and undergraduate, as well as for other research assistant opportunities.",
                     'https://zhaoyw007.github.io/')),
    'Sneha Goenka': dict(
        homepage='https://snehagoenka.com/', title='Assistant Professor', start='Jan 2025', areas=['arch', 'bio'],
        scholar=S + 'VzbI1DoAAAAJ', phd_school='Stanford University',
        advisors=[A('Mark Horowitz', 'in the Electrical Engineering department at Stanford University where I was advised by Prof. Mark Horowitz',
                    'https://snehagoenka.com/'),
                  A('Euan Ashley', 'Thereafter, I was a Postdoctoral Research Scholar at Stanford Medicine with Prof. Euan Ashley.',
                    'https://snehagoenka.com/', 'postdoc')],
        recruiting=R('yes', None, 'I am actively looking for postdocs, graduate and undergraduate students to join my lab!',
                     'https://snehagoenka.com/')),
    'Ewin Tang': dict(
        homepage='https://ewintang.com/', title='Assistant Professor', start='Sep 2026', areas=['theory'],
        scholar=S + 'ytm_89IAAAAJ', phd_school='University of Washington',
        advisors=[A('James R. Lee', 'I received my PhD at the University of Washington, advised by James Lee.', 'https://ewintang.com/')]),
    'Brandon Yushan Feng': dict(
        homepage='https://brandonyfeng.github.io/', title='Assistant Professor', start='Fall 2025',
        areas=['vision', 'graphics'], scholar=S + 'VCeYRsYAAAAJ', photo='https://brandonyfeng.github.io/images/headshot.jpg',
        phd_school='University of Maryland, College Park',
        advisors=[A('Amitabh Varshney', 'I did my PhD in Computer Science at the University of Maryland, advised by Amitabh Varshney.',
                    'https://brandonyfeng.github.io/'),
                  A('William T. Freeman', 'I am a Postdoctoral Associate at MIT CSAIL, working with William T. Freeman.',
                    'https://brandonyfeng.github.io/', 'postdoc')]),
    'Karthik Mahadevan': dict(
        homepage='https://karthikmahadevan.ca/', title='Assistant Professor', start='Spring 2027', areas=['hci', 'robotics'],
        scholar=S + 'i-OiNnAAAAAJ',
        photo='https://karthikmahadevan.ca/author/karthik-mahadevan/avatar_huebbfad1edfa8e198fe68a27ac6a615ca_2526312_270x270_fill_q90_lanczos_center.jpg',
        phd_school='University of Toronto', extra=['https://karthikmahadevan.ca/files/cv.pdf'],
        advisors=[A('Tovi Grossman', 'Ph.D., Computer Science University of Toronto Advisor: Dr. Tovi Grossman',
                    'https://karthikmahadevan.ca/files/cv.pdf'),
                  A('Julie Shah', 'I have started my postdoctoral position at MIT working with Prof. Julie Shah and Dr. Ben Armstrong.',
                    'https://karthikmahadevan.ca/', 'postdoc')],
        recruiting=R('yes', 2027, 'I will be joining UT Austin as an Assistant Professor in Spring 2027 and recruiting PhD students to start in Fall 2027!',
                     'https://karthikmahadevan.ca/')),
    'Yudai Tanaka': dict(
        homepage='https://www.cs.utexas.edu/people/faculty-researchers/yudai-tanaka', title='Assistant Professor',
        start='Fall 2026', areas=['hci'],
        photo='https://www.cs.utexas.edu/sites/default/files/styles/faculty_photo/public/2026-09/YudaiTanaka-extended.jpg?itok=urv9dQ32',
        phd_school='University of Chicago', extra=['https://computerscience.uchicago.edu/people/yudai-tanaka/'],
        advisors=[A('Pedro Lopes', 'Yudai Tanaka is a Ph.D. candidate in the Department of Computer Science at University of Chicago advised by Prof. Pedro Lopes.',
                    'https://computerscience.uchicago.edu/people/yudai-tanaka/')]),
    'Chenfeng Xu': dict(
        homepage='https://www.chenfengx.com/', title='Assistant Professor', start='Fall 2026',
        areas=['ml', 'robotics', 'vision'], scholar=S + 'RpqvaTUAAAAJ',
        photo='https://cns.utexas.edu/sites/default/files/uploads/images/default/xu.jpg', phd_school='UC Berkeley',
        advisors=[A('Masayoshi Tomizuka', 'I obtained my Ph.D degree at UC Berkeley in 2025, fortunately advised by Prof. Masayoshi Tomizuka (ME department) and Prof. Kurt Keutzer (EECS department).',
                    'https://www.chenfengx.com/'),
                  A('Kurt Keutzer', 'I obtained my Ph.D degree at UC Berkeley in 2025, fortunately advised by Prof. Masayoshi Tomizuka (ME department) and Prof. Kurt Keutzer (EECS department).',
                    'https://www.chenfengx.com/')],
        recruiting=R('yes', None, 'I recruit 1-2 Ph.D. students this year.', 'https://www.chenfengx.com/')),
    'Kun Woo Cho': dict(
        homepage='https://kunwoocho.com/', title='Assistant Professor', start='Aug 2026', areas=['networks', 'mobile'],
        scholar=S + 'dUGl5SsAAAAJ',
        photo='https://kunwoocho.com/authors/admin/avatar_hu134d4e4e9831ccfc8de4af3f8bdc12b9_11249012_270x270_fill_lanczos_center_3.png',
        phd_school='Princeton University', extra=['https://kunwoocho.com/uploads/cv.pdf'],
        advisors=[A('Kyle Jamieson', 'Doctor of Philosophy (Ph.D.) in Computer Science, Advisor: Kyle Jamieson',
                    'https://kunwoocho.com/uploads/cv.pdf'),
                  A('Ashutosh Sabharwal', 'Before this, I was a Postdoctoral Associate at Rice University, where I was fortunate to be advised by Ashu Sabharwal and Ashok Veeraraghavan',
                    'https://kunwoocho.com/', 'postdoc'),
                  A('Ashok Veeraraghavan', 'Before this, I was a Postdoctoral Associate at Rice University, where I was fortunate to be advised by Ashu Sabharwal and Ashok Veeraraghavan',
                    'https://kunwoocho.com/', 'postdoc')],
        recruiting=R('yes', None, 'I am looking for self-motivated students and postdocs.', 'https://kunwoocho.com/')),
    'Guandao Yang': dict(
        homepage='https://www.guandaoyang.com/', title='Assistant Professor', start='Fall 2026', areas=['vision', 'graphics'],
        scholar=S + '_kElCmMAAAAJ', photo='https://www.guandaoyang.com/assets/img/prof_pic.JPEG',
        phd_school='Cornell University',
        advisors=[A('Serge Belongie', 'In 2023, I received my Ph.D. from Cornell Tech, advised by Serge Belongie and Bharath Hariharan.',
                    'https://www.guandaoyang.com/'),
                  A('Bharath Hariharan', 'In 2023, I received my Ph.D. from Cornell Tech, advised by Serge Belongie and Bharath Hariharan.',
                    'https://www.guandaoyang.com/'),
                  A('Leonidas Guibas', 'Before that, I spent two years as a Postdoctoral Scholar at Stanford University, working with Leonidas Guibas and Gordon Wetzstein.',
                    'https://www.guandaoyang.com/', 'postdoc'),
                  A('Gordon Wetzstein', 'Before that, I spent two years as a Postdoctoral Scholar at Stanford University, working with Leonidas Guibas and Gordon Wetzstein.',
                    'https://www.guandaoyang.com/', 'postdoc')],
        recruiting=R('yes', None, 'I am hiring Ph.D. students at UT Austin.', 'https://www.guandaoyang.com/')),
    'Jin Wang': dict(
        homepage='https://search.asu.edu/profile/5462842', title='Assistant Professor', start='Fall 2025', areas=['db', 'nlp'],
        scholar=S + '5zl9QR8AAAAJ', photo='https://news.engineering.asu.edu/wp-content/uploads/2025/08/JinWang-600x600-72.jpg',
        phd_school='UCLA',
        advisors=[A('Carlo Zaniolo', 'He obtained the Ph.D. degree from Computer Science Department, University of California, Los Angeles in July 2020 under the supervision of Professor Carlo Zaniolo.',
                    'https://search.asu.edu/profile/5462842')],
        recruiting=R('no', None, "I don't plan to take new PhD students now.", 'https://search.asu.edu/profile/5462842')),
    'Paria Rashidinejad': dict(
        homepage='https://pariard.github.io/', title='WiSE Gabilan Assistant Professor', start='Fall 2025', areas=['ml', 'ai'],
        scholar=S + 'BgQkdsYAAAAJ', photo='https://pariard.github.io/images/paria.jpg', phd_school='UC Berkeley',
        advisors=[A('Stuart Russell', 'I received my Ph.D. in Electrical Engineering and Computer Sciences (EECS) from the University of California, Berkeley, under the supervision of Stuart Russell and Jiantao Jiao.',
                    'https://pariard.github.io/'),
                  A('Jiantao Jiao', 'I received my Ph.D. in Electrical Engineering and Computer Sciences (EECS) from the University of California, Berkeley, under the supervision of Stuart Russell and Jiantao Jiao.',
                    'https://pariard.github.io/')]),
    'Aleksander Hołyński': dict(
        homepage='https://holynski.org/', title='Assistant Professor', start='Jul 2025', areas=['vision', 'graphics'],
        scholar=S + 'ypBMJMgAAAAJ', phd_school='University of Washington',
        advisors=[A('Steven M. Seitz', 'He completed his PhD studies at the University of Washington, where he was advised by Steve Seitz, Brian Curless, and Rick Szeliski.',
                    'https://www.cs.columbia.edu/2025/cs-welcomes-six-new-faculty'),
                  A('Brian Curless', 'He completed his PhD studies at the University of Washington, where he was advised by Steve Seitz, Brian Curless, and Rick Szeliski.',
                    'https://www.cs.columbia.edu/2025/cs-welcomes-six-new-faculty'),
                  A('Richard Szeliski', 'He completed his PhD studies at the University of Washington, where he was advised by Steve Seitz, Brian Curless, and Rick Szeliski.',
                    'https://www.cs.columbia.edu/2025/cs-welcomes-six-new-faculty'),
                  A('Alexei A. Efros', 'Previously, I was a postdoc at UC Berkeley with Alyosha Efros and Angjoo Kanazawa',
                    'https://holynski.org/', 'postdoc'),
                  A('Angjoo Kanazawa', 'Previously, I was a postdoc at UC Berkeley with Alyosha Efros and Angjoo Kanazawa',
                    'https://holynski.org/', 'postdoc')]),
    'Zishen Wan': dict(
        homepage='https://zishenwan.github.io/', title='Assistant Professor', start='Fall 2026', areas=['arch'],
        scholar=S + 'dt3ImqIAAAAJ', photo='https://zishenwan.github.io/images/Zishen_photo_4_square.jpg',
        phd_school='Georgia Institute of Technology',
        advisors=[A('Arijit Raychowdhury', 'received my Ph.D. from Georgia Tech in 2025, advised by Prof. Arijit Raychowdhury and Prof. Tushar Krishna.',
                    'https://zishenwan.github.io/'),
                  A('Tushar Krishna', 'received my Ph.D. from Georgia Tech in 2025, advised by Prof. Arijit Raychowdhury and Prof. Tushar Krishna.',
                    'https://zishenwan.github.io/'),
                  A('Vijay Janapa Reddi', 'a Postdoctoral Fellow at Harvard University, working with Prof. Vijay Janapa Reddi.',
                    'https://zishenwan.github.io/', 'postdoc')],
        recruiting=R('yes', None, 'I am recruiting Ph.D. students, postdoctoral researchers, and visiting students interested in computer architecture, systems, VLSI, physical & agentic AI, and AI for computing system design.',
                     'https://zishenwan.github.io/')),
    'Xiang Cheng': dict(homepage='https://ece.duke.edu/people/xiang-cheng/',
                        extra=['https://pratt.duke.edu/news/xiang-cheng-using-geometry-and-noise-to-push-ais-boundaries'],
                        skip='Duke Pratt news: started as assistant professor in Duke ECE on Aug 1, 2024 (visiting 2024–25) — before the 2025–2027 window'),
}


if __name__ == '__main__':
    main()
