"""从 Wikidata 补导师 / 学生关系。

1. 用 Google Scholar ID (P1960) 精确匹配教授；没有 Scholar ID 或匹配不到的，用英文全名 + 学者身份兜底
2. 查每位匹配上的教授的博士导师 (P184) 和博士学生 (P185 及反向 P184)
输出: data/enrich/wikidata.jsonl, data/cache/wikidata_match.json,
      data/enrich/wikidata_match_method.json {pid: scholar_id | name_employer | name_desc | employer_search}
      data/enrich/wikidata_match_info.json（不是靠 Scholar ID 对上的条目信息，build_graph 核对同名误配用）
用法: python scripts/enrich_wikidata.py            重新匹配并查关系
      python scripts/enrich_wikidata.py --methods  只按缓存的匹配结果反推匹配方式
"""
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT, uni_tokens  # noqa: E402

API = 'https://query.wikidata.org/sparql'
HEAD = {'Accept': 'application/sparql-results+json', 'User-Agent': 'lineage-tree-research-script/0.1'}


def sparql(q, tries=4):
    for k in range(tries):
        try:
            r = requests.post(API, data={'query': q}, headers=HEAD, timeout=90)
            if r.status_code == 200:
                return r.json()['results']['bindings']
        except (requests.RequestException, ValueError):
            pass
        time.sleep(5 * (k + 1))
    raise RuntimeError('SPARQL query failed after retries')


def too_old(qids):
    """出生早于 1930 或 2005 年前去世的条目——不可能是现任教授，用于排除同名误配。"""
    bad, qids = set(), list(qids)
    for i in range(0, len(qids), 300):
        vals = ' '.join('wd:' + q for q in qids[i:i + 300])
        q = f'''SELECT ?i ?b ?d WHERE {{ VALUES ?i {{ {vals} }}
          OPTIONAL {{ ?i wdt:P569 ?b }} OPTIONAL {{ ?i wdt:P570 ?d }} }}'''
        for b in sparql(q):
            by = b.get('b', {}).get('value', '')[:4]
            dy = b.get('d', {}).get('value', '')[:4]
            if (by.isdigit() and int(by) < 1930) or (dy.isdigit() and int(dy) < 2005):
                bad.add(qid(b['i']['value']))
        time.sleep(1)
    return bad


def qid(uri):
    return uri.rsplit('/', 1)[-1]


def esc(s):
    return s.replace('\\', '\\\\').replace('"', '\\"')


def item_info(qids, full=None):
    """Wikidata 条目的基本信息，供核对匹配方式 / 同名误配：
    {qid: dict(l=英文标签, d=英文描述, gs=[Scholar ID], by=出生年, dy=去世年, emp=[任职单位], occ=[职业], edu=[毕业学校])}
    full: 只给这些 QID 查 emp / occ / edu（其余只查前几项）。"""
    out, qids = {}, sorted(set(qids))
    full = set(qids if full is None else full)

    def concat(var, label):
        return f'(GROUP_CONCAT(DISTINCT ?{label}; separator="|") AS ?{var})'

    for i in range(0, len(qids), 300):
        vals = ' '.join('wd:' + q for q in qids[i:i + 300])
        q = f'''SELECT ?i (SAMPLE(?l) AS ?lab) (SAMPLE(?d) AS ?desc) {concat('gss', 'gs')}
                  (MIN(?b) AS ?born) (MIN(?x) AS ?died) WHERE {{ VALUES ?i {{ {vals} }}
          OPTIONAL {{ ?i rdfs:label ?l FILTER(lang(?l)="en") }}
          OPTIONAL {{ ?i schema:description ?d FILTER(lang(?d)="en") }}
          OPTIONAL {{ ?i wdt:P1960 ?gs }} OPTIONAL {{ ?i wdt:P569 ?b }} OPTIONAL {{ ?i wdt:P570 ?x }}
        }} GROUP BY ?i'''
        for b in sparql(q):
            v = lambda k: b.get(k, {}).get('value', '')
            yr = lambda k: int(v(k)[:4]) if v(k)[:4].isdigit() else None
            out[qid(v('i'))] = dict(l=v('lab'), d=v('desc'), gs=[x for x in v('gss').split('|') if x],
                                    by=yr('born'), dy=yr('died'))
        time.sleep(1)
    rest = [q for q in qids if q in full]
    for prop, field in (('P108', 'emp'), ('P106', 'occ'), ('P69', 'edu')):
        for i in range(0, len(rest), 300):
            vals = ' '.join('wd:' + q for q in rest[i:i + 300])
            q = f'''SELECT ?i {concat('vs', 'vL')} WHERE {{ VALUES ?i {{ {vals} }}
              ?i wdt:{prop} ?v . ?v rdfs:label ?vL FILTER(lang(?vL)="en") }} GROUP BY ?i'''
            for b in sparql(q):
                out.setdefault(qid(b['i']['value']), {})[field] = sorted(b['vs']['value'].split('|'))
            time.sleep(1)
    return out


def scholar_id(p):
    m = re.search(r'user=([\w-]{12})', p['scholar'])
    return m.group(1) if m else None


def dblp_support(profs, pids):
    """Wikidata 上这位教授的导师 / 学生（data/enrich/wikidata.jsonl）里，有几位在 dblp 上和他合写过论文
    （名首字母 + 姓相同即算）。真导师 / 学生多半合写过；一个都没有，又是按名字对上的条目，就可能是同名的另一个人。
    返回 {pid: [合写过的, 关系总数]}；没有 dblp 索引（infer_dblp.py 生成）或 dblp 上没有他的论文就不给。"""
    import pickle
    from infer_dblp import CACHE, nkey
    path = os.path.join(ROOT, 'data/enrich/wikidata.jsonl')
    if not os.path.exists(CACHE) or not os.path.exists(path):
        return {}
    pubs = pickle.load(open(CACHE, 'rb'))['pubs']
    by_id = {p['id']: p for p in profs}
    rel = {pid: [] for pid in pids}
    for line in open(path, encoding='utf-8'):
        r = json.loads(line)
        if r['prof'] in rel:
            rel[r['prof']].append(r['name'])
        if r.get('other_prof') in rel:
            rel[r['other_prof']].append(by_id[r['prof']]['name'])
    out = {}
    for pid, names in rel.items():
        mine = pubs.get(by_id[pid]['dblp'])
        if mine:
            co = {nkey(a) for _, _, authors, _ in mine for a in authors}
            out[pid] = [sum(nkey(n) in co for n in names), len(names)]
    return out


def write_method_files(profs, match, method, info):
    """data/enrich/wikidata_match_method.json  {pid: 匹配方式}
    data/enrich/wikidata_match_info.json    {pid: Wikidata 条目信息 + co=dblp_support}（只收不是靠 Scholar ID 对上的，
                                            build_graph 核对同名误配用）"""
    by_id = {p['id']: p for p in profs}
    co = dblp_support(profs, [pid for pid, m in method.items() if m != 'scholar_id'])
    info = {pid: dict(info.get(match[pid], {}), **({'co': co[pid]} if pid in co else {})) for pid in method}
    with open(os.path.join(ROOT, 'data/enrich/wikidata_match_method.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(sorted(method.items(), key=lambda kv: int(kv[0][1:]))), f, indent=0)
    named = {pid: dict(q=match[pid], m=m, n=by_id[pid]['name'], **info[pid])
             for pid, m in method.items() if m != 'scholar_id'}
    with open(os.path.join(ROOT, 'data/enrich/wikidata_match_info.json'), 'w', encoding='utf-8') as f:
        json.dump(dict(sorted(named.items(), key=lambda kv: int(kv[0][1:]))), f, ensure_ascii=False, indent=0)


def reconstruct_methods():
    """匹配方式是后来才记录的：按缓存的匹配结果（data/cache/wikidata_match.json）反推每位教授是怎么对上的，
    写 wikidata_match_method.json / wikidata_match_info.json，不重新匹配。
    判断依据：条目的 P1960 含教授的 Scholar ID → scholar_id；否则在 wikidata_employer_match.json 里且 QID 相同
    → employer_search（1c）；否则是 1b 的姓名兜底，任职单位和教授学校对得上记 name_employer，否则 name_desc。"""
    profs = load_profs()
    by_id = {p['id']: p for p in profs}
    match = json.load(open(os.path.join(ROOT, 'data/cache/wikidata_match.json'), encoding='utf-8'))
    emp_path = os.path.join(ROOT, 'data/enrich/wikidata_employer_match.json')
    emp = json.load(open(emp_path, encoding='utf-8')) if os.path.exists(emp_path) else {}
    base = item_info(match.values(), full=[])
    method = {}
    for pid, q in match.items():
        if scholar_id(by_id[pid]) in base.get(q, {}).get('gs', []):
            method[pid] = 'scholar_id'
        elif emp.get(pid) == q:
            method[pid] = 'employer_search'
        else:
            method[pid] = 'name'
    info = item_info([match[pid] for pid, m in method.items() if m != 'scholar_id'])
    for pid, m in method.items():
        if m == 'name':
            method[pid] = 'name_employer' if uni_tokens(by_id[pid]['uni']) & uni_tokens('|'.join(info.get(match[pid], {}).get('emp', []))) \
                else 'name_desc'
    write_method_files(profs, match, method, info)
    from collections import Counter
    print(Counter(method.values()))


def main():
    profs = load_profs()
    os.makedirs(os.path.join(ROOT, 'data/enrich'), exist_ok=True)
    os.makedirs(os.path.join(ROOT, 'data/cache'), exist_ok=True)

    # --- 1a. Google Scholar ID 匹配
    gs = {}
    for p in profs:
        if scholar_id(p):
            gs[scholar_id(p)] = p['id']
    match = {}  # prof id -> qid
    method = {}  # prof id -> 匹配方式（见 write_method_files；build_graph 把非 scholar_id 的当作按名字认人）
    ids = list(gs)
    for i in range(0, len(ids), 250):
        vals = ' '.join(f'"{esc(x)}"' for x in ids[i:i + 250])
        for b in sparql(f'SELECT ?item ?gs WHERE {{ VALUES ?gs {{ {vals} }} ?item wdt:P1960 ?gs }}'):
            match.setdefault(gs[b['gs']['value']], qid(b['item']['value']))
            method.setdefault(gs[b['gs']['value']], 'scholar_id')
        time.sleep(1)
    print('scholar-id matches', len(match))

    # --- 1b. 姓名兜底：英文标签完全一致、是人、且有学术相关属性，且只有唯一候选
    rest = [p for p in profs if p['id'] not in match]
    by_name = {}
    for p in rest:
        by_name.setdefault(p['name'], []).append(p['id'])
    names = [n for n, v in by_name.items() if len(v) == 1]
    found = {}
    for i in range(0, len(names), 150):
        vals = ' '.join(f'"{esc(n)}"@en' for n in names[i:i + 150])
        q = f'''SELECT DISTINCT ?item ?name WHERE {{
          VALUES ?name {{ {vals} }}
          ?item rdfs:label ?name ; wdt:P31 wd:Q5 .
          {{ ?item wdt:P184 [] }} UNION {{ ?item wdt:P185 [] }} UNION {{ [] wdt:P184 ?item }}
          UNION {{ ?item wdt:P2456 [] }} UNION {{ ?item wdt:P1960 [] }}
        }}'''
        for b in sparql(q):
            found.setdefault(b['name']['value'], set()).add(qid(b['item']['value']))
        time.sleep(1)
    uniq = {by_name[n][0]: next(iter(qs)) for n, qs in found.items() if len(qs) == 1}
    # 同名误配过滤：描述要像 CS/理工学者，或任职单位和表里学校对得上
    info = {}
    items = list(set(uniq.values()))
    for i in range(0, len(items), 300):
        vals = ' '.join('wd:' + q for q in items[i:i + 300])
        q = f'''SELECT ?i ?d (GROUP_CONCAT(DISTINCT ?eL; separator="|") AS ?emp) WHERE {{
          VALUES ?i {{ {vals} }}
          OPTIONAL {{ ?i schema:description ?d FILTER(lang(?d)="en") }}
          OPTIONAL {{ ?i wdt:P108 ?e . ?e rdfs:label ?eL FILTER(lang(?eL)="en") }}
        }} GROUP BY ?i ?d'''
        for b in sparql(q):
            info[qid(b['i']['value'])] = (b.get('d', {}).get('value', ''), b.get('emp', {}).get('value', ''))
        time.sleep(1)
    profs_by_id = {p['id']: p for p in profs}
    good_desc = re.compile(r'ph\.?d|computer|comput|informat|engineer|scien|robot|mathemat|statistic|professor|'
                           r'researcher|data|software|electrical|machine learning', re.I)
    bad_desc = re.compile(r'health|medic|nurs|politic|actor|actress|singer|footballer|athlete|artist|musician|'
                          r'painter|writer|novelist|business|lawyer|journalist|priest|histor', re.I)

    n_name = rejected = 0
    old = too_old(uniq.values())
    for pid, q in uniq.items():
        if q in old:
            rejected += 1
            continue
        d, emp = info.get(q, ('', ''))
        emp_ok = bool(uni_tokens(profs_by_id[pid]['uni']) & uni_tokens(emp))
        if emp_ok or (good_desc.search(d) and not bad_desc.search(d)):
            match[pid] = q
            method[pid] = 'name_employer' if emp_ok else 'name_desc'
            n_name += 1
        else:
            rejected += 1
    print('name matches', n_name, 'rejected', rejected, 'total', len(match))

    # 1c. 按任职学校匹配的结果（scripts/match_wikidata_employer.py 生成）
    emp_path = os.path.join(ROOT, 'data/enrich/wikidata_employer_match.json')
    if os.path.exists(emp_path):
        extra = {k: v for k, v in json.load(open(emp_path)).items() if k not in match}
        match.update(extra)
        method.update({k: 'employer_search' for k in extra})
        print('employer matches', len(extra), 'total', len(match))

    # --- 2. 查导师和学生
    q2p = {}
    for pid, q in match.items():
        q2p.setdefault(q, pid)
    qs = list(q2p)
    recs, seen = [], set()
    for i in range(0, len(qs), 200):
        vals = ' '.join('wd:' + q for q in qs[i:i + 200])
        q = f'''SELECT ?p ?rel ?o ?oLabel WHERE {{
          VALUES ?p {{ {vals} }}
          {{ ?p wdt:P184 ?o . BIND("advisor" AS ?rel) }}
          UNION {{ ?p wdt:P185 ?o . BIND("student" AS ?rel) }}
          UNION {{ ?o wdt:P184 ?p . BIND("student" AS ?rel) }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,mul,de,fr,es,it,zh". }}
        }}'''
        for b in sparql(q):
            p, o, rel = qid(b['p']['value']), qid(b['o']['value']), b['rel']['value']
            if (p, o, rel) in seen:
                continue
            seen.add((p, o, rel))
            name = b.get('oLabel', {}).get('value', o)
            if re.fullmatch(r'Q\d+', name):
                continue  # 没有可读姓名
            rec = dict(prof=q2p[p], rel=rel, name=name, type='phd', source='wikidata',
                       url=f'https://www.wikidata.org/wiki/{o}')
            if o in q2p:
                rec['other_prof'] = q2p[o]
            else:
                rec['ext_id'] = 'wd:' + o
            recs.append(rec)
        time.sleep(1)

    with open(os.path.join(ROOT, 'data/enrich/wikidata.jsonl'), 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    with open(os.path.join(ROOT, 'data/cache/wikidata_match.json'), 'w', encoding='utf-8') as f:
        json.dump(match, f)
    write_method_files(profs, match, method, item_info([match[k] for k, m in method.items() if m != 'scholar_id']))
    print('relations', len(recs), 'advisor', sum(r['rel'] == 'advisor' for r in recs),
          'student', sum(r['rel'] == 'student' for r in recs))


if __name__ == '__main__':
    if '--methods' in sys.argv:  # 只按缓存反推匹配方式，不重新匹配
        reconstruct_methods()
    else:
        main()
