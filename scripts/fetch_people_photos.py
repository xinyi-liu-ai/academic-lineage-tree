"""给教授名单以外的人（学生、外部导师，graph.json 里 "x":1 的节点）从个人主页找本人照片。
选图逻辑和 fetch_photos.py 相同（复用 crawl / candidates / image_ok）；只抓真正的个人主页，
LinkedIn、Google Scholar、Twitter、github.com 仓库页等跳过。

遵守 robots.txt，同一主机两次请求至少间隔 2 秒（复用 fetch_pages.py）；可中断，重跑跳过已抓的。
输出: data/cache/people_photo_pages/{safe_id}.json（页面上的图片列表和选中结果，不提交）
      data/enrich/people_photos.json  {node_id: url}
用法: python scripts/fetch_people_photos.py              抓取 + 选图
      python scripts/fetch_people_photos.py --limit 20   只抓前 20 人（测试）
      python scripts/fetch_people_photos.py --pick       只用缓存重新选图（不联网）
"""
import concurrent.futures as cf
import hashlib
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from itertools import zip_longest
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import ROOT  # noqa: E402
from fetch_photos import crawl  # noqa: E402
from names import display_name, fold  # noqa: E402

OUT = os.path.join(ROOT, 'data/cache/people_photo_pages')
GRAPH = os.path.join(ROOT, 'docs/data/graph.json')
RESULT = os.path.join(ROOT, 'data/enrich/people_photos.json')
# 不是个人主页的站点（社交网络、论文库、学术档案等）
SKIP_HOST = re.compile(r'(^|\.)(linkedin\.com|twitter\.com|x\.com|facebook\.com|instagram\.com|youtube\.com|'
                       r'researchgate\.net|orcid\.org|dblp\.org|dblp\.uni-trier\.de|semanticscholar\.org|'
                       r'wikipedia\.org|wikidata\.org|openreview\.net|arxiv\.org|medium\.com|crunchbase\.com|'
                       r'acm\.org|ieee\.org|google\.com|github\.com)$', re.I)
KEEP_HOST = re.compile(r'(^|\.)(sites\.google\.com|research\.google\.com)$', re.I)


def safe_id(nid):
    """节点 id（如 "hs:p1241:xinyi liu"、"w:Q123"）转成可作文件名的形式，附短哈希防撞名。"""
    s = re.sub(r'[^A-Za-z0-9._-]+', '_', nid).strip('_')[:80]
    return f'{s}_{hashlib.md5(nid.encode("utf-8")).hexdigest()[:8]}'


def is_homepage(url):
    if not url or not url.startswith('http'):
        return False
    host = urlparse(url).netloc.lower().split(':')[0]
    if host.startswith('www.'):
        host = host[4:]
    return bool(KEEP_HOST.search(host)) or not SKIP_HOST.search(host)


def load_people():
    g = json.load(open(GRAPH, encoding='utf-8'))
    return [dict(id=n['id'], name=n['n'], homepage=n['h']) for n in g['nodes']
            if n.get('x') == 1 and is_homepage(n.get('h'))]


def cache_path(p):
    return os.path.join(OUT, safe_id(p['id']) + '.json')


def write_out(people):
    photos = {}
    for p in people:
        f = cache_path(p)
        if os.path.exists(f):
            try:
                rec = json.load(open(f, encoding='utf-8'))
            except ValueError:
                continue
            if rec.get('pick'):
                photos[p['id']] = rec['pick']
    # 同一张图被不同姓氏的人选中，多半是机构占位图或合影，去掉（同姓的是重复节点，保留）
    surnames = defaultdict(set)
    name = {p['id']: p['name'] for p in people}
    for k, u in photos.items():
        t = fold(display_name(name[k])).split()
        surnames[u].add(t[-1] if t else '')
    photos = {k: u for k, u in photos.items() if len(surnames[u]) == 1}
    json.dump(photos, open(RESULT, 'w', encoding='utf-8'), indent=0, ensure_ascii=False)
    print('photos', len(photos), 'of', len(people))


def main():
    os.makedirs(OUT, exist_ok=True)
    people = load_people()
    if '--pick' in sys.argv:
        return write_out(people)
    random.Random(3).shuffle(people)
    todo = [p for p in people if not os.path.exists(cache_path(p))]
    if '--limit' in sys.argv:
        todo = todo[:int(sys.argv[sys.argv.index('--limit') + 1])]
    # 按主机轮流排队：同一网站上的请求要间隔 2 秒，别让线程都堵在一个主机上
    by_host = defaultdict(list)
    for p in todo:
        by_host[urlparse(p['homepage']).netloc].append(p)
    todo = [p for grp in zip_longest(*sorted(by_host.values(), key=len, reverse=True)) for p in grp if p]
    print('people', len(people), 'remaining', len(todo), flush=True)
    t0, done, found = time.time(), 0, 0

    def work(p):
        try:
            rec = crawl(p)
        except Exception as e:
            rec = dict(url=p['homepage'], imgs=[], err=type(e).__name__)
        rec['id'] = p['id']
        with open(cache_path(p), 'w', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False)
        return bool(rec.get('pick'))

    with cf.ThreadPoolExecutor(max_workers=64) as ex:
        for ok in ex.map(work, todo):
            done += 1
            found += ok
            if done % 300 == 0:
                print(f'{done}/{len(todo)} found {found} {time.time() - t0:.0f}s', flush=True)
    print('done', done, 'found', found, f'{time.time() - t0:.0f}s')
    write_out(people)


if __name__ == '__main__':
    main()
