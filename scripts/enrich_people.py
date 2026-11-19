"""给名单外的人（学生、外校导师）找个人主页、领英、Google Scholar 和现在的单位。

来源（按优先级）:
  1. CSRankings 全球教师名单（含国外学校）: 主页、单位、Scholar
  2. Wikidata: 官网 P856、领英 P2035、Scholar P1960
  3. dblp 个人档案 (homepages/ 记录): 主页、领英、Scholar 链接和单位备注
同名消歧: 优先选「和他在图里相连的教授合写过论文」的那个 dblp 作者；否则 dblp 里只有一个同名的人才采用。
输出: data/enrich/people.json  {node_id: {h, li, g, af}}，由 build_graph.py 合并进节点
用法: python scripts/enrich_people.py   （先跑过 build_graph.py 和 infer_dblp.py）
"""
import csv
import gzip
import html
import json
import os
import pickle
import re
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from enrich_wikidata import sparql, qid  # noqa: E402
from names import display_name, fold, key  # noqa: E402

DUMP = os.path.join(ROOT, 'data/cache/dblp/dblp.xml.gz')
INDEX = os.path.join(ROOT, 'data/cache/dblp/index.pkl')
CSR = os.path.join(ROOT, 'data/cache/csrankings/csrankings.csv')
SPLIT = re.compile(r'(?=<www\s)')
AUTH = re.compile(r'<author[^>]*>(.*?)</author>')
URL = re.compile(r'<url[^>]*>(.*?)</url>')
AFF = re.compile(r'<note[^>]*type="affiliation"[^>]*>(.*?)</note>')
TAG = re.compile(r'<[^>]+>')
SKIP_URL = re.compile(r'orcid\.org|wikidata|wikipedia|viaf\.org|id\.loc\.gov|isni|dl\.acm\.org|ieeexplore|scopus|zbmath|'
                      r'mathgenealogy|genealogy\.ams|researchgate|semanticscholar|dblp\.|openreview|twitter\.com|x\.com/|'
                      r'cienciavitae|d-nb\.info|catalogue\.bnf|github\.com|facebook|youtube|mathscinet|arxiv\.org|'
                      r'publons|webofscience|researcherid|loop\.frontiersin|academic\.microsoft|crossref|idref|isni|'
                      r'scholar\.google', re.I)


def scan_homepages(want):
    """dblp 个人档案：只保留名字（去编号后）在 want 里的人。"""
    out, base_count = {}, defaultdict(set)
    cur = None
    with gzip.open(DUMP, 'rt', encoding='utf-8', errors='replace') as f:
        for raw in f:
            for line in SPLIT.split(raw):
                if cur is None:
                    if not line.startswith('<www') or 'key="homepages/' not in line[:120]:
                        continue
                    cur = [[], [], []]
                cur[0] += [html.unescape(TAG.sub('', a)) for a in AUTH.findall(line)]
                cur[1] += [html.unescape(u) for u in URL.findall(line)]
                cur[2] += [html.unescape(TAG.sub('', a)) for a in AFF.findall(line)]
                if '</www>' in line:
                    names, urls, affs = cur
                    cur = None
                    if not names:
                        continue
                    hit = {fold(display_name(n)) for n in names} & want
                    if not hit:
                        continue
                    rec = dict(name=names[0], urls=urls, aff=affs)
                    for n in names:
                        out[n] = rec
                    for b in hit:
                        base_count[b].add(names[0])
    return out, base_count


def classify(urls):
    h = li = g = ''
    for u in urls:
        if 'linkedin.com/in' in u:
            li = li or u
        elif 'scholar.google' in u:
            g = g or u
        elif not SKIP_URL.search(u) and u.startswith('http'):
            h = h or u
    return h, li, g


def main():
    t0 = time.time()
    g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8'))
    nodes = {n['id']: n for n in g['nodes']}
    profs = {p['id']: p for p in load_profs()}
    ext = {i: n for i, n in nodes.items() if n.get('x')}
    linked = defaultdict(set)  # 外部节点 -> 相连的名单内教授
    for a, s, *_ in g['edges']:
        if a in ext and s in profs:
            linked[a].add(s)
        if s in ext and a in profs:
            linked[s].add(a)

    # 1) 通过合著消歧：相连教授的合作者里，和外部节点同名的 dblp 作者
    index = pickle.load(open(INDEX, 'rb'))['pubs']
    chosen = {}
    for i, n in ext.items():
        k = key(n['n'])
        cands = set()
        for pid in linked[i]:
            for pub in index.get(profs[pid]['dblp'], []):
                for au in pub[2]:
                    if key(display_name(au)) == k:
                        cands.add(au)
        if len(cands) == 1:
            chosen[i] = next(iter(cands))
    print('matched via co-authorship', len(chosen), '/', len(ext), f'{time.time() - t0:.0f}s', flush=True)

    want = {fold(display_name(n['n'])) for n in ext.values()} | {fold(display_name(c)) for c in chosen.values()}
    hp, base_count = scan_homepages(want)
    print('dblp homepage records', len({id(v) for v in hp.values()}), f'{time.time() - t0:.0f}s', flush=True)

    csr = defaultdict(list)
    for r in csv.DictReader(open(CSR, encoding='utf-8')):
        csr[r['name']].append(r)
        csr['~' + fold(display_name(r['name']))].append(r)

    # 2) Wikidata 官网 / 领英 / Scholar
    wd = {i: i[3:] for i in ext if i.startswith('wd:')}
    wdinfo = defaultdict(dict)
    qs = list(wd.values())
    for j in range(0, len(qs), 300):
        vals = ' '.join('wd:' + q for q in qs[j:j + 300])
        q = f'''SELECT ?i ?site ?li ?gs WHERE {{ VALUES ?i {{ {vals} }}
          OPTIONAL {{ ?i wdt:P856 ?site }} OPTIONAL {{ ?i wdt:P2035 ?li }} OPTIONAL {{ ?i wdt:P1960 ?gs }} }}'''
        for b in sparql(q):
            d = wdinfo[qid(b['i']['value'])]
            if 'site' in b:
                d.setdefault('h', b['site']['value'])
            if 'li' in b:
                d.setdefault('li', 'https://www.linkedin.com/in/' + b['li']['value'])
            if 'gs' in b:
                d.setdefault('g', 'https://scholar.google.com/citations?user=' + b['gs']['value'])
        time.sleep(1)
    print('wikidata items with links', sum(1 for v in wdinfo.values() if v), f'{time.time() - t0:.0f}s', flush=True)

    out, how = {}, defaultdict(int)
    for i, n in ext.items():
        base = fold(display_name(n['n']))
        dname = chosen.get(i)
        if not dname and len(base_count.get(base, ())) == 1:
            dname = next(iter(base_count[base]))  # dblp 里只有一个同名的人
        rec = hp.get(dname) if dname else None
        info = {}
        rows = csr.get(dname, []) if dname else []
        if not rows and not dname and len(csr.get('~' + base, [])) == 1:
            rows = csr['~' + base]
        if len(rows) == 1:
            r = rows[0]
            info.update(h=r['homepage'], af=r['affiliation'])
            if r['scholarid'] and r['scholarid'] != 'NOSCHOLARPAGE':
                info['g'] = 'https://scholar.google.com/citations?user=' + r['scholarid']
            how['csrankings'] += 1
        w = wdinfo.get(wd.get(i, ''), {})
        for k2 in ('h', 'li', 'g'):
            if w.get(k2) and not info.get(k2):
                info[k2] = w[k2]
        if rec:
            h, li, gs = classify(rec['urls'])
            for k2, v in (('h', h), ('li', li), ('g', gs)):
                if v and not info.get(k2):
                    info[k2] = v
            if rec['aff'] and not info.get('af'):
                info['af'] = rec['aff'][0]
        if dname and not rec and not info.get('h'):
            pass
        info = {k2: v for k2, v in info.items() if v}
        if info:
            out[i] = info
            for k2 in info:
                how[k2] += 1
    with open(os.path.join(ROOT, 'data/enrich/people.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    print('external people', len(ext), 'with any info', len(out), dict(how), f'{time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
