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
    alias_path = os.path.join(ROOT, 'data/raw/faculty_aliases.csv')  # 同一人的其他写法
    if os.path.exists(alias_path):
        pid_of = {p['dblp']: p['id'] for p in profs}
        with open(alias_path, encoding='utf-8', newline='') as f:
            for r in list(csv.reader(f))[1:]:
                pid = pid_of.get(r[0])
                k = key(display_name(r[1]))
                if pid and pid not in g.by_key[k]:
                    g.by_key[k].append(pid)
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
    inferred, listed = [], []
    for f in sorted(glob.glob(os.path.join(ROOT, 'data/enrich/*.jsonl'))):
        recs = [json.loads(line) for line in open(f, encoding='utf-8')]
        if os.path.basename(f) == 'dblp.jsonl':
            inferred = recs
            continue
        if os.path.basename(f) == 'students_homepage.jsonl':
            listed = recs  # 导师主页上的学生名单，见 2a
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

    # 2a) 导师主页上的学生名单：学生也是名单内教授时用 other_prof（extract_students 已用合著核实）；
    #     否则找这位导师已有的同名学生；都没有就新建节点，不和别处的同名外部人员合并（常见名字容易张冠李戴）
    manual_path = os.path.join(ROOT, 'data/raw/manual_students.csv')  # 人工补充的师生关系（如学生本人提供）
    if os.path.exists(manual_path):
        pid_of = {p['dblp']: p['id'] for p in profs}
        with open(manual_path, encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                if row['advisor'] in pid_of and row['student'].strip():
                    listed.append(dict(prof=pid_of[row['advisor']], rel='student', name=row['student'].strip(),
                                       type=row.get('type') or 'phd', source='sheet', url='',
                                       h=row.get('homepage', '').strip(), af=row.get('affiliation', '').strip()))
    mine = defaultdict(dict)
    for e in g.edges.values():
        mine[e['a']].setdefault(key(g.nodes[e['s']]['name']), e['s'])
    for r in listed:
        k = key(r['name'])
        other = r.get('other_prof') or mine[r['prof']].get(k)
        if not other:
            other = f"hs:{r['prof']}:{k}"
            g.nodes.setdefault(other, dict(id=other, name=r['name'].strip(), ext=True))
            mine[r['prof']][k] = other
        if other == r['prof']:
            continue
        if r.get('af') and g.nodes[other].get('ext') and not g.nodes[other].get('af'):
            g.nodes[other]['af'] = r['af']  # 导师主页上写的去向，如 "Amazon"、"postdoc, Harvard"
        if r.get('h') and g.nodes[other].get('ext'):
            g.nodes[other]['h'] = r['h']
        g.add_edge(r['prof'], other, r.get('type', 'phd'), r.get('source', 'homepage'), r.get('url', ''))
        stats[r.get('source', 'homepage') + '_student'] += 1

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

    # 2c) 合并同一个人的重复节点（find_duplicates.py：同主页 / 同 Scholar / 同名且同导师）
    alias = {}
    mc_path = os.path.join(ROOT, 'data/enrich/merge_candidates.json')
    for m in (json.load(open(mc_path, encoding='utf-8')) if os.path.exists(mc_path) else []):
        keep, drop = m['keep'], m['drop']
        if m.get('reason') not in ('same_homepage', 'same_scholar', 'same_name_shared_advisor'):
            continue
        while keep in alias:
            keep = alias[keep]
        if keep == drop or drop not in g.nodes or keep not in g.nodes or not g.nodes[drop].get('ext'):
            continue
        alias[drop] = keep
        for k in ('url', 'af', 'h'):
            if g.nodes[keep].get('ext') and g.nodes[drop].get(k) and not g.nodes[keep].get(k):
                g.nodes[keep][k] = g.nodes[drop][k]
        for (a, s, t), e in list(g.edges.items()):
            if drop not in (a, s):
                continue
            del g.edges[(a, s, t)]
            a2, s2 = (keep if a == drop else a), (keep if s == drop else s)
            if a2 == s2:
                continue
            if (a2, s2, t) in g.edges:
                old = g.edges[(a2, s2, t)]
                old['src'] += [x for x in e['src'] if x not in old['src']]
            else:
                e['a'], e['s'] = a2, s2
                g.edges[(a2, s2, t)] = e
        del g.nodes[drop]
    stats['merged_duplicates'] = len(alias)

    # 2d) 两人互为导师（A→B 且 B→A）：保留来源更可靠的那条，一样可靠就都去掉
    rank = lambda e: max({'sheet': 3, 'wikidata': 3, 'wikipedia': 3, 'cv': 2, 'homepage': 1, 'dblp': 0}.get(x, 1) for x in e['src'])
    mutual = 0
    for (a, s, t), e in list(g.edges.items()):
        rev = g.edges.get((s, a, 'phd')) if t == 'phd' else None
        if rev is None or (a, s, t) not in g.edges:
            continue
        ra, rb = rank(e), rank(rev)
        if ra >= rb:
            del g.edges[(s, a, 'phd')]
        if rb >= ra:
            del g.edges[(a, s, t)]
        mutual += 1
    stats['mutual_advisor_pairs_fixed'] = mutual

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
    people_path = os.path.join(ROOT, 'data/enrich/people.json')  # 名单外的人：主页 / 领英 / Scholar / 现单位
    people = json.load(open(people_path, encoding='utf-8')) if os.path.exists(people_path) else {}
    links_path = os.path.join(ROOT, 'data/enrich/student_links.json')  # 导师/学生主页上链过去的个人主页
    links = json.load(open(links_path, encoding='utf-8')) if os.path.exists(links_path) else {}
    def opt(name):
        f = os.path.join(ROOT, 'data/enrich', name)
        return json.load(open(f, encoding='utf-8')) if os.path.exists(f) else {}
    # 主页上的职称 / 照片地址（extract_titles.py、fetch_photos.py、fetch_people_photos.py）
    titles, photos, people_photos = opt('titles.json'), opt('photos.json'), opt('people_photos.json')
    for nid in opt('photo_blocklist.json'):  # 人工核对出的选错的主页照片，或本人要求删除
        photos.pop(nid, None)
        people_photos.pop(nid, None)
    # 院系名录（fetch_directories.py）比个人主页可靠，优先；Wikimedia Commons 照片（fetch_wikidata_photos.py）兜底
    for pid, r in opt('directory.json').items():
        if r.get('ti'):
            titles[pid] = r['ti']
        if r.get('ph'):
            photos[pid] = r['ph']
    link_updates, dead_links = opt('link_updates.json'), opt('dead_links.json')
    bad_urls = opt('photo_https_bad.json')  # https 下打不开的照片地址（verify_photos.py），换下一个来源
    for src in (photos, people_photos):
        for nid in [k for k, u in src.items() if u in bad_urls]:
            del src[nid]
    for nid, u in opt('wikidata_photos.json').items():
        if u not in bad_urls:
            photos.setdefault(nid, u)
            people_photos.setdefault(nid, u)
    aliases_of = defaultdict(list)
    for drop, keep in alias.items():
        while keep in alias:
            keep = alias[keep]
        aliases_of[keep].append(drop)
    out_nodes = []
    for nid, n in g.nodes.items():
        if n.get('ext'):
            if nid not in used:
                continue
            d = dict(id=nid, n=n['name'], x=1, **({'w': n['url']} if n.get('url') else {}), **({'af': n['af']} if n.get('af') else {}), **({'h': n['h']} if n.get('h') else {}))
            d.update(people.get(nid, {}))
            for dup in aliases_of.get(nid, ()):  # 合并掉的重复节点带来的主页 / 领英 / 单位
                for k, v in {**people.get(dup, {}), **{k: v for k, v in links.get(dup, {}).items() if k in ('h', 'li', 'g')}}.items():
                    d.setdefault(k, v)
            for k, v in links.get(nid, {}).items():
                if k in ('h', 'li', 'g') and not d.get(k):
                    d[k] = v
                    if k == 'h':
                        d['hv'] = links[nid]['via']
            if people_photos.get(nid):
                d['ph'] = people_photos[nid]
            if d.get('h') and nid in dead_links:  # 主页已失效（check_links.py）：名字改链到领英 / 搜索
                del d['h']
                d.pop('hv', None)
            out_nodes.append(d)
        else:
            d = dict(id=nid, n=n['name'], u=uni_idx[n['uni']], h=n['homepage'])
            lu = link_updates.get(nid, {})  # 旧主页失效时用 CSRankings 现在的主页；补 Scholar（refresh_links.py）
            if lu.get('homepage'):
                d['h'] = lu['homepage']
            if n['scholar'] or lu.get('scholar'):
                d['g'] = n['scholar'] or lu['scholar']
            if n['cv']:
                d['c'] = n['cv']
            if titles.get(nid):
                d['ti'] = titles[nid]
            if photos.get(nid):
                d['ph'] = photos[nid]
            if n.get('status') == 'former':
                # 已不在 CSRankings 的美国学校名单上（调去国外、退休、转业界……）：网站只收在美国高校任教的人，
                # 不再当作教授列出；作为别人的导师 / 学生仍留在树上，显示现在的去向或原学校
                if nid not in used:
                    continue
                stats['former_as_outside'] += 1
                x = dict(id=nid, n=n['name'], x=1, af=n.get('now_at') or f"Formerly {n['uni']}")
                x.update({k: d[k] for k in ('h', 'g', 'ph') if d.get(k)})
                out_nodes.append(x)
                continue
            d['d'] = [a for a, _ in parse_domains(n['domain'])]
            d['a'] = sorted({b for _, b in parse_domains(n['domain'])})
            out_nodes.append(d)
    for d in out_nodes:  # 抓来的链接只放行 http(s)，防止 javascript: 之类进到网页
        for k in ('h', 'li', 'g', 'c', 'ph', 'w'):
            if k in d and not re.match(r'https?://', str(d[k]), re.I):
                del d[k]
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
