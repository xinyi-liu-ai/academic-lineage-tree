"""给图里还没有可信 PhD 导师的在职教授，补抓主页上链接的 CV / Bio / About 页面（以及院系页上的个人主页），
供 extract_advisors_extra.py 离线抽取导师。

- 目标：graph.json 里没有「非存疑、type=phd」入边的教授，或者唯一的导师边只来自 dblp 合著推断
- 复用 fetch_pages.py 的 robots.txt 检查和按主机加锁（同一主机两次请求至少间隔 2 秒），多线程跨主机并行
- 超过 8 MB 的文件不要；不碰 dblp.org、Google Scholar、LinkedIn、ResearchGate 这类站点
- 两轮：第 1 轮抓主页上的 CV / Bio / About / 个人主页链接；第 2 轮从新抓到的个人主页上再找 CV / Bio
输出: data/cache/advisor_pages/{pid}.json（不提交）
用法: python scripts/fetch_advisor_pages.py [--targets-only]
"""
import concurrent.futures as cf
import io
import json
import os
import random
import re
import sys
import threading
import time
from collections import defaultdict
from urllib.parse import urljoin, urlparse

import requests

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from fetch_pages import allowed, TextAndLinks, UA, HOST_GAP, MAX_BYTES  # noqa: E402

OUT = os.path.join(ROOT, 'data/cache/advisor_pages')
PAGES = os.path.join(ROOT, 'data/cache/pages')
PEOPLE = os.path.join(ROOT, 'data/cache/people_pages')
MAX_PER_PROF = 5

SKIP_HOST = re.compile(r'(^|\.)(dblp\.org|dblp\.uni-trier\.de|scholar\.google\.[a-z.]+|linkedin\.com|researchgate\.net|'
                       r'facebook\.com|twitter\.com|x\.com|instagram\.com|youtube\.com|orcid\.org|github\.com|'
                       r'semanticscholar\.org|academia\.edu|docs\.google\.com|box\.com|onedrive\.live\.com|'
                       r'sharepoint\.com|arxiv\.org|doi\.org|acm\.org|ieee\.org|wikipedia\.org)$', re.I)
CV_TEXT = re.compile(r'^\W*(cv|c\.v\.|vita|vitae|curriculum vitae|résumé|resume|full cv|short cv|my cv|cv \(pdf\)|'
                     r'curriculum vitae \(pdf\)|cv/resume|resume/cv|download cv|\[cv\]|\(cv\))\W*$', re.I)
CV_HREF = re.compile(r'(^|[/_-])(cv|vita|vitae|resume|curriculum)[^/]*\.pdf(\?|$)', re.I)
BIO_TEXT = re.compile(r'^\W*(bio|biography|short bio|about|about me|bio(graphy)? & cv|cv & bio|background|'
                      r'brief bio|full bio|biosketch|bio sketch|profile)\W*$', re.I)
HOME_TEXT = re.compile(r'^\W*(website|personal website|personal web ?page|personal homepage|personal home page|'
                       r'homepage|home page|my homepage|my website|personal site|personal|visit website|'
                       r'personal webpage)\W*$', re.I)
BAD_EXT = re.compile(r'\.(ps|gz|zip|pptx?|docx?|jpe?g|png|gif|mp4|bib|tex)(\?|$)|^mailto:|^javascript:', re.I)

_last, _host_locks, _hl = {}, {}, threading.Lock()
NAV = set()  # 好几位教授的主页上都有的同一个链接（院系导航栏里的「CV」「About」），不抓
_log = threading.Lock()


def host_lock(host):
    with _hl:
        return _host_locks.setdefault(host, threading.Lock())


def to_text(ctype, body, enc):
    if 'pdf' in ctype or body[:5] == b'%PDF-':
        try:
            import pymupdf
            doc = pymupdf.open(stream=body, filetype='pdf')
            return '\n'.join(doc[i].get_text() for i in range(min(len(doc), 15))), [], 'pdf'
        except Exception:
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(body))
                return '\n'.join((pg.extract_text() or '') for pg in reader.pages[:15]), [], 'pdf'
            except Exception:
                return '', [], 'pdf-error'
    if 'html' not in ctype and 'text' not in ctype:
        return '', [], 'ctype:' + ctype[:30]
    p = TextAndLinks()
    try:
        p.feed(body.decode(enc or 'utf-8', errors='replace'))
    except Exception:
        pass
    text = re.sub(r'[ \t\r\f\v]+', ' ', ''.join(p.out))
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text, p.links, ''


def fetch(url):
    """返回 (最终 URL, 文字, 链接, 错误)。"""
    host = urlparse(url).netloc
    if SKIP_HOST.search(host.split(':')[0]):
        return url, '', [], 'skip-host'
    if not allowed(url):
        return url, '', [], 'robots'
    lock = host_lock(host)
    with lock:
        wait = _last.get(host, 0) + HOST_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=(10, 25), stream=True, allow_redirects=True)
            try:
                if r.status_code != 200:
                    return r.url, '', [], str(r.status_code)
                cl = r.headers.get('content-length')
                if cl and cl.isdigit() and int(cl) > MAX_BYTES:
                    return r.url, '', [], 'too-big'
                body = r.raw.read(MAX_BYTES + 1, decode_content=True)
            finally:
                r.close()
        except Exception as e:
            return url, '', [], type(e).__name__
        finally:
            _last[host] = time.time()
    if len(body) > MAX_BYTES:
        return r.url, '', [], 'too-big'
    final = r.url
    if SKIP_HOST.search(urlparse(final).netloc.split(':')[0]):
        return final, '', [], 'skip-host'
    text, links, err = to_text(r.headers.get('content-type', '').lower(), body, r.encoding)
    return final, text, [(urljoin(final, h), t) for h, t in links if h], err


def drive_direct(h):
    """Google Drive / Dropbox 的公开分享链接换成直接下载地址。"""
    m = re.match(r'https?://drive\.google\.com/(?:file/d/|open\?id=)([\w-]{20,})', h)
    if m:
        return f'https://drive.google.com/uc?export=download&id={m.group(1)}'
    if re.match(r'https?://(www\.)?dropbox\.com/', h):
        return h.replace('dl=0', 'dl=1') if 'dl=0' in h else h + ('&' if '?' in h else '?') + 'dl=1'
    return h


def norm(h):
    u = urlparse(h)
    path = re.sub(r'/+', '/', u.path)
    path = re.sub(r'/(index\.(html?|php))?$', '', path)
    host = u.netloc.lower().removeprefix('www.')
    return (host + path).replace('%7e', '~').replace('%7E', '~') + ('?' + u.query if u.query else '')


def pick_links(base_url, links, want_home=True):
    """从一页的链接里挑 CV / Bio / 个人主页；返回 [(kind, url)]。"""
    bp = urlparse(base_url)
    base_dir = bp.path if bp.path.endswith('/') else bp.path.rsplit('/', 1)[0] + '/'
    cvs, bios, homes = [], [], []
    for h, t in links:
        h = (h or '').split('#')[0].strip()
        t = re.sub(r'\s+', ' ', (t or '')).strip()
        if not h.startswith('http') or BAD_EXT.search(h) or norm(h) == norm(base_url) or h in NAV:
            continue
        u = urlparse(h)
        if CV_TEXT.match(t) or CV_HREF.search(u.path):
            cvs.append(('cv', drive_direct(h)))
        elif BIO_TEXT.match(t) and u.netloc == bp.netloc and u.path.startswith(base_dir) and not h.endswith('.pdf'):
            bios.append(('bio', h))  # 只要本人站点内的 About / Bio，不要院系导航栏里的「About us」
        elif want_home and HOME_TEXT.match(t) and not h.lower().endswith('.pdf') and (
                u.netloc != bp.netloc or (u.path.strip('/') and not u.path.startswith(base_dir))):
            homes.append(('home', h))  # 院系简介页上的「Personal website」
    out, seen = [], set()
    for k, h in cvs[:2] + bios[:2] + homes[:1]:
        if norm(h) not in seen:
            seen.add(norm(h))
            out.append((k, h))
    return out


def targets():
    g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8'))
    # 本脚本自己产出的边（homepage_extra.jsonl）不算：否则重建图之后再跑，目标就把自己排除掉了
    extra = os.path.join(ROOT, 'data/enrich/homepage_extra.jsonl')
    own = {(r['prof'], r['url']) for r in map(json.loads, open(extra, encoding='utf-8'))} if os.path.exists(extra) else set()
    inc = defaultdict(list)
    for e in g['edges']:
        src = set(e[3].split('+'))
        if e[2] == 'phd' and not e[5]:
            if (e[1], e[4]) in own and src <= {'cv', 'homepage', 'dblp'}:
                continue
            inc[e[1]].append(src)
    ids = {n['id'] for n in g['nodes']}
    out = []
    for p in load_profs():
        if p['status'] == 'former' or p['id'] not in ids:
            continue
        srcs = inc.get(p['id'], [])
        if not srcs or all(s == {'dblp'} for s in srcs):
            out.append(p)
    return out


def cached_cv_url(pid):
    f = os.path.join(PAGES, pid + '_cv.txt')
    if not os.path.exists(f):
        return ''
    with open(f, encoding='utf-8', errors='replace') as fh:
        first = fh.readline()
        ok = len(fh.read(400)) > 200
    return first.replace('#URL ', '').strip() if ok else ''


def crawl(p):
    pp = os.path.join(PEOPLE, p['id'] + '.json')
    home_links, home_url = [], p['homepage']
    if os.path.exists(pp):
        d = json.load(open(pp, encoding='utf-8'))
        if d['pages']:
            home_links, home_url = d['pages'][0]['links'], d['pages'][0]['url']
    have = {norm(cached_cv_url(p['id'])), norm(home_url), norm(p['homepage'])}
    todo = []
    if p['cv'].startswith('http'):
        todo.append(('cv', drive_direct(p['cv'])))
    todo += pick_links(home_url, home_links)
    pages, seen = [], set(have)
    q = [(k, h) for k, h in todo]
    while q and len(pages) < MAX_PER_PROF:
        kind, h = q.pop(0)
        if norm(h) in seen:
            continue
        seen.add(norm(h))
        final, text, links, err = fetch(h)
        pages.append(dict(kind=kind, url=h, final=final, text=text, err=err,
                          links=links if kind == 'home' else []))
        if kind == 'home' and text:  # 第 2 轮：个人主页上的 CV / Bio
            q += [x for x in pick_links(final, links, want_home=False) if norm(x[1]) not in seen]
    return pages


def build_nav(profs):
    cnt = defaultdict(int)
    for p in profs:
        pp = os.path.join(PEOPLE, p['id'] + '.json')
        if os.path.exists(pp):
            d = json.load(open(pp, encoding='utf-8'))
            for h in {h for h, _ in (d['pages'][0]['links'] if d['pages'] else [])}:
                cnt[h] += 1
    NAV.update(h for h, n in cnt.items() if n >= 3)


def main():
    os.makedirs(OUT, exist_ok=True)
    T = targets()
    build_nav(load_profs())
    print('targets', len(T), flush=True)
    if '--targets-only' in sys.argv:
        return
    random.Random(3).shuffle(T)
    todo = [p for p in T if not os.path.exists(os.path.join(OUT, p['id'] + '.json'))]
    print('remaining', len(todo), flush=True)
    t0, done, npages = time.time(), 0, 0

    def work(p):
        try:
            pages = crawl(p)
        except Exception as e:
            pages = [dict(kind='error', url='', final='', text='', err=type(e).__name__, links=[])]
        with open(os.path.join(OUT, p['id'] + '.json'), 'w', encoding='utf-8', errors='replace') as f:
            json.dump(dict(pid=p['id'], name=p['name'], pages=pages), f, ensure_ascii=False)
        return len(pages)

    with cf.ThreadPoolExecutor(max_workers=24) as ex:
        for n in ex.map(work, todo):
            done += 1
            npages += n
            if done % 100 == 0:
                print(f'{done}/{len(todo)} pages={npages} {time.time() - t0:.0f}s', flush=True)
    print('done', done, 'pages', npages, f'{time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
