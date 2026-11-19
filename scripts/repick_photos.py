"""选图规则改了以后，对缓存里原来没选到照片、现在有候选的记录补取图片（联网，只下载候选图片本身）。
用法: python scripts/repick_photos.py   之后再跑 fetch_photos.py --pick 和 fetch_people_photos.py --pick
"""
import concurrent.futures as cf
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import ROOT, load_profs  # noqa: E402
import fetch_photos as F  # noqa: E402

names = {p['id']: p['name'] for p in load_profs()}
g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8'))
ext = {}
for n in g['nodes']:
    if n.get('x'):
        ext[n['id']] = n['n']


def todo():
    for f in glob.glob(os.path.join(ROOT, 'data/cache/photo_pages/*.json')):
        yield f, names.get(os.path.basename(f)[:-5]), False
    for f in glob.glob(os.path.join(ROOT, 'data/cache/people_photo_pages/*.json')):
        yield f, None, True


def work(item):
    f, name, strict = item
    try:
        rec = json.load(open(f, encoding='utf-8'))
    except ValueError:
        return 0
    if rec.get('pick') or not rec.get('imgs'):
        return 0
    name = name or ext.get(rec.get('id'))
    if not name:
        return 0
    for c in F.candidates(rec, name, strict):
        ok = F.image_ok(c)
        if ok:
            rec['pick'], rec['size'] = c, ok[:2]
            json.dump(rec, open(f, 'w', encoding='utf-8'), ensure_ascii=False)
            return 1
    return 0


if __name__ == '__main__':
    items = list(todo())
    with cf.ThreadPoolExecutor(max_workers=48) as ex:
        print('new picks', sum(ex.map(work, items)), 'of', len(items))
