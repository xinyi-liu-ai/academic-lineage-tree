"""用 fetch_people_pages.py 抓下来的教授页面里的链接，给和这位教授相连的人（学生、导师）配个人主页。

链接文字里同时出现此人的名和姓才算；同一个链接对应多个人（多半是实验室主页）不用；
链到教授本人主页、论文、PDF 的不算。
输出: data/enrich/student_links.json  {node_id: {h|li|g: url, via: 页面}}
用法: python scripts/match_people_links.py   （在 build_graph.py 之后跑，之后再跑一次 build_graph.py）
"""
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from names import display_name, fold  # noqa: E402

PEOPLE = os.path.join(ROOT, 'data/cache/people_pages')
BAD_HREF = re.compile(r'\.(pdf|ps|gz|zip|pptx?|docx?|jpe?g|png|bib)(\?|$)|arxiv\.org|doi\.org|dblp\.|acm\.org|ieee|mailto:|'
                      r'semanticscholar|openreview|youtube|twitter\.com|x\.com/|facebook|wikipedia|aclanthology|'
                      r'proceedings|neurips\.cc|github\.com/[^/]+/[^/]+|/pubs?/|/papers?/|/publications?', re.I)


def tokens(name):
    t = [x for x in fold(display_name(name)).split() if x not in {'jr', 'sr', 'ii', 'iii'} and len(x) > 1]
    return (t[0], t[-1]) if len(t) >= 2 else None


def main():
    g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8'))
    nodes = {n['id']: n for n in g['nodes']}
    profs = {p['id']: p for p in load_profs()}
    need = defaultdict(dict)
    for a, s, *_ in g['edges']:
        for me, other in ((a, s), (s, a)):
            o = nodes.get(other)
            # 主页来自上一轮匹配（带 hv）的也重新算，否则重跑时会丢掉这些结果
            if me in profs and o and o.get('x') and (not o.get('h') or o.get('hv')):
                t = tokens(o['n'])
                if t:
                    need[me][other] = t
    votes = defaultdict(lambda: defaultdict(set))
    for pid, people in need.items():
        f = os.path.join(PEOPLE, pid + '.json')
        if not os.path.exists(f):
            continue
        own = profs[pid]['homepage'].rstrip('/')
        for pg in json.load(open(f, encoding='utf-8')).get('pages', []):
            for href, anchor in pg.get('links', []):
                if not href.startswith('http') or BAD_HREF.search(href) or href.split('#')[0].rstrip('/') in (own, pg['url'].rstrip('/')):
                    continue
                a = fold(anchor or '')
                if not a or len(a) > 70:
                    continue
                words = set(a.split())
                hits = [x for x, (fi, la) in people.items() if fi in words and la in words]
                if len(hits) == 1:
                    votes[hits[0]][href.split('#')[0]].add(pg['url'])
    href_count = defaultdict(int)
    for hs in votes.values():
        for h in hs:
            href_count[h] += 1
    out = {}
    for person, hs in votes.items():
        ok = [h for h in hs if href_count[h] == 1]
        if len(ok) != 1:
            continue
        h = ok[0]
        kind = 'li' if 'linkedin.com/in' in h else 'g' if 'scholar.google' in h else 'h'
        out[person] = {kind: h, 'via': sorted(hs[h])[0]}
    out_path = os.path.join(ROOT, 'data/enrich/student_links.json')
    if os.path.exists(out_path):  # 累积：上一轮找到、这次没再找到的（页面变了或节点改名）保留
        for k, v in json.load(open(out_path, encoding='utf-8')).items():
            out.setdefault(k, v)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print('people needing a link', len({k for v in need.values() for k in v}), 'found', len(out))


if __name__ == '__main__':
    main()
