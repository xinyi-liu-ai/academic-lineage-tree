"""从 Wikidata 的 P18 (图像) 取 Commons 自由授权头像，作为第二个照片来源。

教授: data/cache/wikidata_match.json (pid -> QID)；外部人物: 图中形如 "wd:Q123" 的节点。
批量 SPARQL (每批 200 个 QID)，缩略图用 Special:FilePath?width=200。
输出: data/enrich/wikidata_photos.json {节点 id: 缩略图 URL}
"""
import json
import os
import re
import time
from urllib.parse import quote, unquote

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = 'https://query.wikidata.org/sparql'
UA = 'AcademicLineageTree/1.0 (academic genealogy site; photo lookup via Wikidata P18)'


def main():
    with open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8') as f:
        graph = json.load(f)
    with open(os.path.join(ROOT, 'data/cache/wikidata_match.json'), encoding='utf-8') as f:
        match = json.load(f)
    ids = {n['id'] for n in graph['nodes']}
    q2nodes = {}  # QID -> [节点 id]
    for pid, q in match.items():
        if pid in ids:
            q2nodes.setdefault(q, []).append(pid)
    for i in ids:
        if i.startswith('wd:'):
            q2nodes.setdefault(i[3:], []).append(i)
    qids = sorted(q2nodes)
    print('QIDs:', len(qids))

    out = {}
    s = requests.Session()
    s.headers['User-Agent'] = UA
    for i in range(0, len(qids), 200):
        vals = ' '.join('wd:' + q for q in qids[i:i + 200])
        query = f'SELECT ?i ?img WHERE {{ VALUES ?i {{ {vals} }} ?i wdt:P18 ?img }}'
        for attempt in range(4):
            try:
                r = s.get(API, params={'query': query, 'format': 'json'}, timeout=90)
                r.raise_for_status()
                break
            except Exception as e:
                print('retry', e)
                time.sleep(5 * (attempt + 1))
        else:
            continue
        for b in r.json()['results']['bindings']:
            q = b['i']['value'].rsplit('/', 1)[-1]
            fn = unquote(b['img']['value'].rsplit('/', 1)[-1])
            url = 'https://commons.wikimedia.org/wiki/Special:FilePath/' + quote(fn.replace(' ', '_')) + '?width=200'
            for nid in q2nodes.get(q, []):
                out.setdefault(nid, url)
        print(i, len(out), flush=True)
        time.sleep(1)

    with open(os.path.join(ROOT, 'data/enrich/wikidata_photos.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    prof = sum(1 for k in out if re.fullmatch(r'p\d+', k))
    ext = sum(1 for k in out if k.startswith('wd:'))
    print('professors:', prof, 'external:', ext)
    for k in list(out)[:5]:
        print(k, out[k])


if __name__ == '__main__':
    main()
