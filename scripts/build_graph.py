"""把教授表 + 自动补全结果合成一张导师关系图，输出 docs/data/graph.json。

用法: python scripts/build_graph.py
输入:
  data/raw/professors.csv                    人工整理的 5080 位教授
  data/raw/universities.csv                 学校全称/简称
  data/enrich/*.jsonl                       自动补全的关系（Wikidata / MGP）
"""
import glob
import json
import os
import re
import sys
from collections import defaultdict

import csv

sys.path.insert(0, os.path.dirname(__file__))
from names import display_name, key, split_advisors, wildcard_regex, fold  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def s(v):
    return '' if v is None else str(v).strip()


def load_profs():
    with open(os.path.join(ROOT, 'data/raw/professors.csv'), encoding='utf-8', newline='') as f:
        rows = list(csv.reader(f))[1:]
    profs = []
    for i, r in enumerate(rows):
        profs.append(dict(id=f'p{i}', name=display_name(s(r[1])), dblp=s(r[1]), uni=s(r[0]), homepage=s(r[2]),
                          domain=s(r[3]), scholar=s(r[4]), cv=s(r[5]), phd_raw=s(r[6]), post_raw=s(r[7]),
                          status=s(r[8]) if len(r) > 8 else 'current', now_at=s(r[9]) if len(r) > 9 else ''))
    return profs


def load_unis():
    p = os.path.join(ROOT, 'data/raw/universities.csv')
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8', newline='') as f:
        return {s(r[0]): s(r[1]) for r in list(csv.reader(f))[1:] if r and r[0]}


def parse_domains(d):
    # 'ml (ai-area) theory (theory-area)' -> [('ml','ai'), ('theory','theory')]
    return [(a, b) for a, b in re.findall(r'([\w-]+)\s*\(([\w-]+)-area\)', d)]


class Graph:
    def __init__(self, profs):
        self.nodes = {}
        self.by_key = defaultdict(list)
        self.edges = {}
        self.alias = {}
        for p in profs:
            self.nodes[p['id']] = p
            self.by_key[key(p['name'])].append(p['id'])
        self.prof_folded = {fold(p['name']): p['id'] for p in profs}

    def resolve(self, name, ext_hint=None):
        """把一个姓名对应到图里的节点；找不到就新建外部节点。"""
        k = key(name)
        if '?' in name:  # 乱码名字：在教授名单里按通配符找回
            rx = wildcard_regex(name)
            hits = [pid for f, pid in self.prof_folded.items() if rx.match(f)]
            if len(hits) == 1:
                return hits[0]
        if ext_hint and ext_hint in self.alias:
            return self.alias[ext_hint]
        cands = self.by_key.get(k, [])
        profs = [c for c in cands if not self.nodes[c].get('ext')]
        if len(profs) == 1:
            if ext_hint:
                self.alias[ext_hint] = profs[0]
            return profs[0]
        exts = [c for c in cands if self.nodes[c].get('ext')]
        if ext_hint:
            # 同名外部人员合并，除非两边是不同的 Wikidata 条目
            def compatible(c):
                w = self.nodes[c].get('wd') or ''
                return not (w.startswith('wd:') and ext_hint.startswith('wd:') and w != ext_hint)
            ok = [c for c in exts if compatible(c)]
            if len(ok) == 1:
                w = self.nodes[ok[0]].get('wd') or ''
                if not w.startswith('wd:'):
                    self.nodes[ok[0]]['wd'] = ext_hint
                self.alias[ext_hint] = ok[0]
                return ok[0]
            xid = ext_hint
        else:
            if len(exts) == 1:
                return exts[0]
            xid = 'x:' + k
        if xid not in self.nodes:
            self.nodes[xid] = dict(id=xid, name=name.strip(), ext=True, wd=ext_hint)
            self.by_key[k].append(xid)
        if ext_hint:
            self.alias[ext_hint] = xid
        return xid

    def add_edge(self, adv, stu, typ, src, url=''):
        if adv == stu:
            return
        e = self.edges.get((adv, stu, typ))
        if e:
            if src not in e['src']:
                e['src'].append(src)
            if url and not e.get('url'):
                e['url'] = url
        else:
            self.edges[(adv, stu, typ)] = dict(a=adv, s=stu, t=typ, src=[src], url=url)


def main():
    profs = load_profs()
    unis = load_unis()
    g = Graph(profs)
    stats = defaultdict(int)

    # 1) 人工整理的导师
    for p in profs:
        for raw, typ in ((p['phd_raw'], 'phd'), (p['post_raw'], 'postdoc')):
            for nm in split_advisors(raw):
                if key(nm) == key(p['name']):
                    continue  # 把自己填成导师的错误数据
                g.add_edge(g.resolve(nm), p['id'], typ, 'sheet')
                stats['sheet_' + typ] += 1

    # 2) 自动补全的关系（dblp 推断最后处理，见 2b）
    inferred = []
    for f in sorted(glob.glob(os.path.join(ROOT, 'data/enrich/*.jsonl'))):
        recs = [json.loads(line) for line in open(f, encoding='utf-8')]
        if os.path.basename(f) == 'dblp.jsonl':
            inferred = recs
            continue
        cleaned = []
        for r in recs:
            if r['source'] == 'wikipedia':  # 信息框纯文本字段里可能有 "A and B"、<br>、年份
                nm = re.sub(r'<[^>]+>|\(\d{4}\)', '', r['name']).strip()
                for part in re.split(r'\s+and\s+|\s*&\s*', nm):
                    if len(part.split()) >= 2:
                        cleaned.append(dict(r, name=part.strip()))
            else:
                cleaned.append(r)
        for r in cleaned:
            ext_id = r.get('ext_id')
            if not ext_id and r['source'] == 'wikipedia' and '/wiki/' in r.get('url', ''):
                ext_id = 'wp:' + r['url'].rsplit('/wiki/', 1)[1]
            other = r.get('other_prof') or g.resolve(r['name'], ext_id)
            if g.nodes[other].get('ext') and not g.nodes[other].get('url'):
                g.nodes[other]['url'] = r.get('url', '')
            if r['rel'] == 'advisor':
                g.add_edge(other, r['prof'], r.get('type', 'phd'), r['source'], r.get('url', ''))
            else:
                g.add_edge(r['prof'], other, r.get('type', 'phd'), r['source'], r.get('url', ''))
            stats[r['source'] + '_' + r['rel']] += 1

    # 2b) dblp 合作论文推断：与已有导师一致时作为佐证；只有其他来源都没有 PhD 导师时才新增
    has_phd = defaultdict(set)
    for e in g.edges.values():
        if e['t'] == 'phd':
            has_phd[e['s']].add(e['a'])
    for r in inferred:
        stu = r['prof']
        if has_phd.get(stu):
            match = [a for a in has_phd[stu] if key(g.nodes[a]['name']) == key(r['name'])]
            if match:
                g.edges[(match[0], stu, 'phd')]['src'].append('dblp')
                stats['dblp_confirms'] += 1
            continue
        other = r.get('other_prof') or g.resolve(r['name'], r.get('ext_id'))
        if g.nodes[other].get('ext') and not g.nodes[other].get('url'):
            g.nodes[other]['url'] = r['url']
        g.add_edge(other, stu, 'phd', 'dblp', r['url'])
        g.edges[(other, stu, 'phd')]['note'] = r.get('note', '')
        stats['dblp_advisor'] += 1

    # 3) 人工表的 PhD 导师若与 Wikidata / 维基百科不一致，标为存疑
    trusted = defaultdict(set)
    for e in g.edges.values():
        if e['t'] == 'phd' and set(e['src']) & {'wikidata', 'wikipedia'}:
            trusted[e['s']].add(e['a'])
    disputed = 0
    for e in g.edges.values():
        if e['t'] == 'phd' and e['src'] == ['sheet'] and trusted.get(e['s']):
            e['disputed'] = True
            disputed += 1
    stats['sheet_phd_disputed'] = disputed

    # 4) 输出（紧凑格式，供网页使用）
    uni_list = sorted({p['uni'] for p in profs})
    uni_idx = {u: i for i, u in enumerate(uni_list)}
    used = {e['a'] for e in g.edges.values()} | {e['s'] for e in g.edges.values()}
    out_nodes = []
    for nid, n in g.nodes.items():
        if n.get('ext'):
            if nid not in used:
                continue
            out_nodes.append(dict(id=nid, n=n['name'], x=1, **({'w': n['url']} if n.get('url') else {})))
        else:
            d = dict(id=nid, n=n['name'], u=uni_idx[n['uni']], h=n['homepage'])
            if n['scholar']:
                d['g'] = n['scholar']
            if n['cv']:
                d['c'] = n['cv']
            if n.get('status') == 'former':  # 已不在 CSRankings 名单内的学校（调走、退休、荣休）
                d['o'] = 1
                if n.get('now_at'):
                    d['now'] = n['now_at']
            d['d'] = [a for a, _ in parse_domains(n['domain'])]
            d['a'] = sorted({b for _, b in parse_domains(n['domain'])})
            out_nodes.append(d)
    # 边: [导师, 学生, 类型, 来源, 出处链接, 是否存疑(, 推断依据)]
    edges = [[e['a'], e['s'], e['t'], '+'.join(e['src']), e.get('url', ''), int(bool(e.get('disputed')))]
             + ([e['note']] if e.get('note') else []) for e in g.edges.values()]

    prof_ids = {p['id'] for p in profs}
    with_adv = {e['s'] for e in g.edges.values() if e['s'] in prof_ids and not e.get('disputed')}
    with_stu = {e['a'] for e in g.edges.values() if e['a'] in prof_ids}
    prof_prof = sum(1 for e in g.edges.values() if e['a'] in prof_ids and e['s'] in prof_ids)
    summary = dict(professors=len(profs), current_faculty=sum(1 for p in profs if p.get('status') != 'former'),
                   current_with_advisor=sum(1 for p in profs if p.get('status') != 'former' and p['id'] in with_adv), with_advisor=len(with_adv), with_student=len(with_stu),
                   external_people=sum(1 for n in out_nodes if n.get('x')), edges=len(edges),
                   prof_to_prof_edges=prof_prof, by_source=dict(stats))

    os.makedirs(os.path.join(ROOT, 'docs/data'), exist_ok=True)
    with open(os.path.join(ROOT, 'docs/data/graph.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(unis=[[u, unis.get(u, '')] for u in uni_list], nodes=out_nodes, edges=edges, summary=summary),
                  f, ensure_ascii=False, separators=(',', ':'))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
