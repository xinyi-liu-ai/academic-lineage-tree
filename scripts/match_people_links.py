"""用 fetch_people_pages.py 抓下来的教授页面里的链接，给和这位教授相连的人（学生、导师）配个人主页。

链接文字里同时出现此人的名和姓才算；同一个链接对应多个人（多半是实验室主页）不用；
链到教授本人主页、论文、PDF 的不算。
名录 / 团队页（路径以 /people /team /students /members 等结尾）不算个人主页，除非网页标题就是此人名字（礼貌抓标题，有缓存）；
同一个主页对应好几个不同的人（名首字母不同或姓明显不同）也不用。
所有和教授相连的外部节点都找（不只没有主页的）：enrich_people.py 用"导师页面上链了谁"判断按名字配的数据是不是同名的人。
领英统一成 https://www.linkedin.com/in/<slug>；check_links.py 修过的地址（link_updates.json 里的 was -> homepage）沿用。
输出: data/enrich/student_links.json  {node_id: {h|li|g: url, via: 页面}}
用法: python scripts/match_people_links.py   （在 build_graph.py 之后、enrich_people.py 之前跑，之后再跑一次 build_graph.py）
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from names import display_name, fold  # noqa: E402
from check_links import distinct_people, norm_li, norm_url, not_personal  # noqa: E402

PEOPLE = os.path.join(ROOT, 'data/cache/people_pages')
BAD_HREF = re.compile(r'\.(pdf|ps|gz|zip|pptx?|docx?|jpe?g|png|bib)(\?|$)|arxiv\.org|doi\.org|dblp\.|acm\.org|ieee|mailto:|'
                      r'semanticscholar|openreview|youtube|twitter\.com|x\.com/|facebook|wikipedia|aclanthology|'
                      r'proceedings|neurips\.cc|urldefense\.|safelinks\.protection|github\.com/[^/]+/[^/]+|/pubs?/|/papers?/|/publications?', re.I)


def tokens(name):
    t = [x for x in fold(display_name(name)).split() if x not in {'jr', 'sr', 'ii', 'iii'} and len(x) > 1]
    return (t[0], t[-1]) if len(t) >= 2 else None


def url_fixes():
    p = os.path.join(ROOT, 'data/enrich/link_updates.json')
    lu = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
    return {v['was']: v['homepage'] for v in lu.values() if isinstance(v, dict) and v.get('was') and v.get('homepage')}


def clean(person, rec, names, stat):
    """领英规范化、名录页过滤；返回清理后的记录（没有可用链接时返回 None）。"""
    rec = dict(rec)
    if rec.get('li'):
        li = norm_li(rec['li'])
        if li != rec['li']:
            stat['li_normalised'] += 1
        rec['li'] = li
    if rec.get('h') and not_personal(rec['h'], names.get(person, '')):
        stat['h_directory_dropped'] += 1
        del rec['h']
    return rec if any(rec.get(k) for k in ('h', 'li', 'g')) else None


def main():
    g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8'))
    nodes = {n['id']: n for n in g['nodes']}
    names = {i: n['n'] for i, n in nodes.items()}
    profs = {p['id']: p for p in load_profs()}
    need = defaultdict(dict)
    for a, s, *_ in g['edges']:
        for me, other in ((a, s), (s, a)):
            o = nodes.get(other)
            if me in profs and o and o.get('x'):
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
    stat = Counter()
    fixes = url_fixes()
    out = {}
    for person, hs in votes.items():
        ok = [h for h in hs if href_count[h] == 1]
        if len(ok) != 1:
            continue
        h = ok[0]
        kind = 'li' if 'linkedin.com/in' in h else 'g' if 'scholar.google' in h else 'h'
        out[person] = {kind: fixes.get(h, h), 'via': sorted(hs[h])[0]}
    out_path = os.path.join(ROOT, 'data/enrich/student_links.json')
    if os.path.exists(out_path):  # 累积：上一轮找到、这次没再找到的（页面变了或节点改名）保留
        for k, v in json.load(open(out_path, encoding='utf-8')).items():
            if k not in out:
                out[k] = {kk: (fixes.get(vv, vv) if kk == 'h' else vv) for kk, vv in v.items()}
    for person in list(out):
        rec = clean(person, out[person], names, stat)
        if rec is None:
            del out[person]
        else:
            out[person] = rec
    # 同一个主页对应好几个不同的人：不用
    # （只看图里现有的节点；累积保留下来的旧节点 id 多半是同一个人改名前的，不算"另一个人"）
    owners = defaultdict(set)
    for person, rec in out.items():
        if rec.get('h') and person in names:
            owners[norm_url(rec['h'])].add(names[person])
    for person in list(out):
        h = out[person].get('h')
        if h and person in names and distinct_people(owners[norm_url(h)]):
            stat['h_shared_dropped'] += 1
            del out[person]['h']
            if not any(out[person].get(k) for k in ('li', 'g')):
                del out[person]
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print('people with an edge to a listed professor', len({k for v in need.values() for k in v}), 'found', len(out), dict(stat))


if __name__ == '__main__':
    main()
