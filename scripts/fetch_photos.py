"""从教授主页找本人照片：收集页面上的 <img> 和 og:image，按文件名/alt/class 打分选候选，
再取图片头部检查尺寸（太小、太扁的不要）。网站只引用原图地址，不保存或转发图片。

遵守 robots.txt，同一主机两次请求至少间隔 2 秒（复用 fetch_pages.py）；可中断，重跑跳过已抓的。
输出: data/cache/photo_pages/{pid}.json（页面上的图片列表和选中结果，不提交）
      data/enrich/photos.json  {pid: url}
用法: python scripts/fetch_photos.py            抓取 + 选图
      python scripts/fetch_photos.py --pick     只用缓存重新选图（不联网）
"""
import concurrent.futures as cf
import io
import json
import os
import random
import re
import sys
import time
from html.parser import HTMLParser
from urllib.parse import urljoin

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from fetch_pages import allowed, polite_get  # noqa: E402
from names import display_name, fold  # noqa: E402

OUT = os.path.join(ROOT, 'data/cache/photo_pages')
BAD = re.compile(r'logo|icon|banner|header|footer|seal|wordmark|sprite|button|arrow|badge|social|twitter|github|'
                 r'linkedin|scholar|orcid|email|mail|rss|spinner|loading|background|\bbg\b|map|qr|flag|feed|'
                 r'cover|hero|slide|poster|figure|fig\d|teaser|thumb_video|youtube|award|sponsor|nsf|darpa|'
                 r'university|college|department|dept(?!h)|campus|building|lab_?photo|group|team|students|'
                 r'pixel|blank|spacer|transparent|placeholder|default|data:', re.I)
# 只看文件名（alt / class 里常有 research、book 之类的普通词）
BAD_FILE = re.compile(r'nophoto|no-photo|generic|not.?available|cartoon|anime|ghibli|comic|plush|mascot|book|'
                      r'overview|facilities|elephant|tiger|drawn|drawing|sketch|crest|crown', re.I)
GOOD = re.compile(r'photo|portrait|headshot|profile|\bme\b|face|selfie|avatar|bio|mugshot|pic\b|picture|people', re.I)
IMG_EXT = re.compile(r'\.(jpe?g|png|webp|gif)(\?|$)', re.I)


class Imgs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.imgs, self.og = [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'img':
            src = a.get('src') or a.get('data-src') or ''
            if src:
                self.imgs.append(dict(src=src, alt=a.get('alt') or '', cls=(a.get('class') or '') + ' ' + (a.get('id') or ''),
                                      w=a.get('width') or '', h=a.get('height') or ''))
        elif tag == 'meta' and (a.get('property') or a.get('name') or '').lower() in ('og:image', 'twitter:image'):
            self.og = self.og or a.get('content')


def num(x):
    m = re.match(r'\s*(\d+)', x or '')
    return int(m.group(1)) if m else None


def score(img, i, first, last, strict=True):
    s = img['src']
    blob = fold(f"{s.rsplit('/', 1)[-1]} {img['alt']} {img['cls']}")
    raw = f"{s} {img['alt']} {img['cls']}"
    if BAD.search(f"{s.rsplit('/', 1)[-1]} {img['alt']} {img['cls']}") or BAD_FILE.search(s.rsplit('/', 1)[-1])             or s.startswith('data:') or s.lower().endswith('.svg'):
        return None
    w, h = num(img['w']), num(img['h'])
    if (w and w < 70) or (h and h < 70) or (w and h and not 0.5 < w / h < 1.6):
        return None
    sc = 0
    words = set(re.split(r'[^a-z]+', blob))
    # 两个字母的姓（Li、Wu）必须是完整的词；三个字母的（Zhu、Liu）可以在词尾，如 jerryzhu.jpg
    hit = lambda x: bool(x) and (x in words or (len(x) >= 4 and x in blob)
                                 or (len(x) == 3 and any(w.endswith(x) and len(w) > 5 for w in words)))
    # 以下规则来自对 2936 张选图的人工核对（错图约 3%）
    if strict and not hit(last) and not hit(first) and i >= 1:
        return None  # 没有名字线索时只信页面上的第一张图（院系列表页上第一张常是别人）
    if strict and i >= 10 and not hit(last):
        return None
    alt_words = set(fold(img['alt']).split())
    if strict and re.search(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', img['alt']) and not ({first, last} & alt_words) \
            and not re.search(r'(?i)university|college|school|department|institute|lab', img['alt']):
        return None  # alt 写着另一个人的名字
    if hit(last):
        sc += 3
    if hit(first):
        sc += 2 if not strict or hit(last) or i == 0 else 1  # 只命中名（David、Michael）不够
    if GOOD.search(raw):
        sc += 2
    if i < 3:
        sc += 1
    return sc


def candidates(rec, name, strict=True):
    t = [x for x in fold(display_name(name)).split() if len(x) > 1]
    first, last = (t[0], t[-1]) if len(t) >= 2 else ('', '')
    out = []
    for i, img in enumerate(rec.get('imgs', [])):
        sc = score(img, i, first, last, strict)
        if sc is not None and sc >= 3:
            out.append((sc, -i, urljoin(rec['url'], img['src'])))
    out.sort(reverse=True)
    return [u for _, _, u in out[:2]]


def image_ok(url):
    """取图片头部，用 Pillow 读尺寸：至少 90px、宽高比 0.55–1.5。"""
    if not url.startswith('http') or not allowed(url):
        return None
    try:
        r, body = polite_get(url)
    except Exception:
        return None
    if r.status_code != 200 or 'image' not in r.headers.get('content-type', ''):
        return None
    try:
        from PIL import Image
        w, h = Image.open(io.BytesIO(body)).size
    except Exception:
        return None
    if min(w, h) < 90 or not 0.55 < w / h < 1.5:
        return None
    return [w, h, r.url]


def crawl(p):
    url = p['homepage']
    rec = dict(url=url, imgs=[], og=None, err='')
    if not allowed(url):
        rec['err'] = 'robots'
        return rec
    try:
        r, body = polite_get(url)
    except Exception as e:
        rec['err'] = type(e).__name__
        return rec
    if r.status_code != 200 or 'html' not in r.headers.get('content-type', '').lower():
        rec['err'] = str(r.status_code)
        return rec
    rec['url'] = r.url
    parser = Imgs()
    try:
        parser.feed(body.decode(r.encoding or 'utf-8', errors='replace'))
    except Exception:
        pass
    rec['imgs'], rec['og'] = parser.imgs[:60], parser.og
    for c in candidates(rec, p['name']):
        ok = image_ok(c)
        if ok:
            rec['pick'] = c
            rec['size'] = ok[:2]
            break
    return rec


def write_out(profs):
    photos = {}
    for p in profs:
        f = os.path.join(OUT, p['id'] + '.json')
        if os.path.exists(f):
            try:
                rec = json.load(open(f, encoding='utf-8'))
            except ValueError:
                continue
            # 用当前的打分规则复核：选中的图已不算候选（比如只靠短姓子串命中）就不要了
            # 教授的选图已人工逐张核对过（2026-11，错图记在 photo_blocklist.json，构建时去掉），直接用；
            # 严格规则（score(strict=True)）用于没人工看过的学生照片
            if rec.get('pick'):
                photos[p['id']] = rec['pick']
    # 同一张图被姓名不同的几位教授选中：多半是院系列表页上的某一个人，全部不要（同名重复建档的除外）
    names = {p['id']: fold(display_name(p['name'])).split()[-1:] for p in profs}
    by_url = {}
    for pid, u in photos.items():
        by_url.setdefault(u, set()).add(tuple(names.get(pid, ())))
    shared = {u for u, ns in by_url.items() if len(ns) > 1}
    photos = {pid: u for pid, u in photos.items() if u not in shared}
    print('dropped shared pictures', len(shared))
    json.dump(photos, open(os.path.join(ROOT, 'data/enrich/photos.json'), 'w', encoding='utf-8'), indent=0)
    print('photos', len(photos), 'of', len(profs))


def main():
    os.makedirs(OUT, exist_ok=True)
    profs = [p for p in load_profs() if p['homepage'].startswith('http')]
    if '--pick' in sys.argv:
        return write_out(profs)
    random.Random(3).shuffle(profs)
    todo = [p for p in profs if not os.path.exists(os.path.join(OUT, p['id'] + '.json'))]
    # 按主机轮流排队：同一院系网站上的请求要间隔 2 秒，别让线程都堵在一个主机上
    from collections import defaultdict
    from itertools import zip_longest
    from urllib.parse import urlparse
    by_host = defaultdict(list)
    for p in todo:
        by_host[urlparse(p['homepage']).netloc].append(p)
    todo = [p for grp in zip_longest(*sorted(by_host.values(), key=len, reverse=True)) for p in grp if p]
    print('professors', len(profs), 'remaining', len(todo), flush=True)
    t0, done = time.time(), 0

    def work(p):
        try:
            rec = crawl(p)
        except Exception as e:
            rec = dict(url=p['homepage'], imgs=[], err=type(e).__name__)
        with open(os.path.join(OUT, p['id'] + '.json'), 'w', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False)
        return bool(rec.get('pick'))

    found = 0
    with cf.ThreadPoolExecutor(max_workers=64) as ex:
        for ok in ex.map(work, todo):
            done += 1
            found += ok
            if done % 300 == 0:
                print(f'{done}/{len(todo)} found {found} {time.time() - t0:.0f}s', flush=True)
    print('done', done, f'{time.time() - t0:.0f}s')
    write_out(profs)


if __name__ == '__main__':
    main()
