"""从 Wikidata 补导师 / 学生关系。

1. 用 Google Scholar ID (P1960) 精确匹配教授；没有 Scholar ID 或匹配不到的，用英文全名 + 学者身份兜底
2. 查每位匹配上的教授的博士导师 (P184) 和博士学生 (P185 及反向 P184)
输出: data/enrich/wikidata.jsonl, data/cache/wikidata_match.json
"""
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402

API = 'https://query.wikidata.org/sparql'
HEAD = {'Accept': 'application/sparql-results+json', 'User-Agent': 'lineage-tree-research-script/0.1'}


def sparql(q, tries=4):
    for k in range(tries):
        r = requests.post(API, data={'query': q}, headers=HEAD, timeout=90)
        if r.status_code == 200:
            return r.json()['results']['bindings']
        time.sleep(5 * (k + 1))
    r.raise_for_status()


def qid(uri):
    return uri.rsplit('/', 1)[-1]


def esc(s):
    return s.replace('\\', '\\\\').replace('"', '\\"')


def main():
    profs = load_profs()
    os.makedirs(os.path.join(ROOT, 'data/enrich'), exist_ok=True)
    os.makedirs(os.path.join(ROOT, 'data/cache'), exist_ok=True)

    # --- 1a. Google Scholar ID 匹配
    gs = {}
    for p in profs:
        m = re.search(r'user=([\w-]{12})', p['scholar'])
        if m:
            gs[m.group(1)] = p['id']
    match = {}  # prof id -> qid
    ids = list(gs)
    for i in range(0, len(ids), 250):
        vals = ' '.join(f'"{esc(x)}"' for x in ids[i:i + 250])
        for b in sparql(f'SELECT ?item ?gs WHERE {{ VALUES ?gs {{ {vals} }} ?item wdt:P1960 ?gs }}'):
            match.setdefault(gs[b['gs']['value']], qid(b['item']['value']))
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

    def uni_tokens(u):
        u = u.replace('Univ.', 'University').replace(' - ', ' ')
        return {t for t in re.findall(r'[a-z]{4,}', u.lower()) if t not in {'university', 'state', 'college', 'institute', 'technology'}}

    n_name = rejected = 0
    for pid, q in uniq.items():
        d, emp = info.get(q, ('', ''))
        emp_ok = bool(uni_tokens(profs_by_id[pid]['uni']) & uni_tokens(emp))
        if emp_ok or (good_desc.search(d) and not bad_desc.search(d)):
            match[pid] = q
            n_name += 1
        else:
            rejected += 1
    print('name matches', n_name, 'rejected', rejected, 'total', len(match))

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
    print('relations', len(recs), 'advisor', sum(r['rel'] == 'advisor' for r in recs),
          'student', sum(r['rel'] == 'student' for r in recs))


if __name__ == '__main__':
    main()
