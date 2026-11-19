"""按任职学校在 Wikidata 里匹配教授（补充 Scholar ID / 全名匹配没找到的人）。

1. 把 189 所学校对应到 Wikidata 条目（wbsearchentities，取第一个是大学/机构的结果）
2. 取这些学校里有导师/学生关系的人（P108 任职 或 P69 毕业 + 有 P184/P185）
3. 同校内按「名+姓」或「姓+名首字母」唯一匹配（含别名）
输出: data/enrich/wikidata_employer_match.json  {prof_id: qid}
"""
import json
import os
import re
import sys
import time
from collections import defaultdict

import requests

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from enrich_wikidata import sparql, qid, HEAD, too_old  # noqa: E402
from names import fold, display_name  # noqa: E402

MANUAL = {  # 简称/特殊写法
    'Univ. of Illinois at Urbana-Champaign': 'University of Illinois Urbana-Champaign',
    'Georgia Institute of Technology': 'Georgia Institute of Technology',
    'Massachusetts Institute of Technology': 'Massachusetts Institute of Technology',
    'Univ. of Maryland - College Park': 'University of Maryland, College Park',
    'University of Michigan': 'University of Michigan',
    'University of Wisconsin - Madison': 'University of Wisconsin–Madison',
    'Univ. of California - San Diego': 'University of California, San Diego',
    'Univ. of California - Berkeley': 'University of California, Berkeley',
}


# 名称对不上的学校，人工查好的 Wikidata 条目
MANUAL_QID = {
    'Missouri University of Technology': 'Q4380853',
    'TTI Chicago': 'Q7830888',
    'University of Louisiana - Lafayette': 'Q116485',
    'University of Wisconsin - Milwaukee': 'Q1755318',
}


def uni_query_name(u):
    if u in MANUAL:
        return MANUAL[u]
    u = u.replace('Univ. of', 'University of').replace('Univ.', 'University')
    u = re.sub(r'\s+-\s+', ', ', u)
    return u


def find_uni(name):
    r = requests.get('https://www.wikidata.org/w/api.php', headers=HEAD, timeout=30, params=dict(
        action='wbsearchentities', search=name, language='en', type='item', limit=5, format='json'))
    for hit in r.json().get('search', []):
        d = hit.get('description', '').lower()
        if any(w in d for w in ('university', 'college', 'institute', 'school', 'research')):
            return hit['id']
    hits = r.json().get('search', [])
    return hits[0]['id'] if hits else None


def keys(name):
    toks = [t for t in fold(display_name(name)).split() if t not in {'jr', 'sr', 'ii', 'iii'}]
    if len(toks) < 2:
        return set()
    first, last = toks[0], toks[-1]
    out = {f'{first} {last}'}
    if len(toks) > 2 and len(toks[0]) == 1:  # "J. Zico Kolter" -> "zico kolter"
        out.add(f'{toks[1]} {last}')
    return out


def initial_key(name):
    toks = [t for t in fold(display_name(name)).split() if t not in {'jr', 'sr', 'ii', 'iii'}]
    return f'{toks[0][0]} {toks[-1]}' if len(toks) >= 2 else ''


def main():
    profs = load_profs()
    done = set(json.load(open(os.path.join(ROOT, 'data/cache/wikidata_match.json'))))
    todo = [p for p in profs if p['id'] not in done]
    print('unmatched professors', len(todo))

    uni_q = {}
    unis = sorted({p['uni'] for p in todo})
    want = {}
    for u in unis:
        n = uni_query_name(u)
        for v in {n, n.replace(', ', ' '), n.replace(' - ', ' ')}:
            want[v] = u
    names = list(want)
    for i in range(0, len(names), 40):
        vals = ' '.join('"%s"@en' % n.replace('"', '') for n in names[i:i + 40])
        q = f'''SELECT ?n ?u (COUNT(?e) AS ?c) WHERE {{
          VALUES ?n {{ {vals} }}
          {{ ?u rdfs:label ?n }} UNION {{ ?u skos:altLabel ?n }}
          ?u wdt:P31/wdt:P279* wd:Q38723 .
          OPTIONAL {{ ?e wdt:P108 ?u }}
        }} GROUP BY ?n ?u'''
        best = {}
        for b in sparql(q):
            n, c = b['n']['value'], int(b['c']['value'])
            if n not in best or c > best[n][1]:
                best[n] = (qid(b['u']['value']), c)
        for n, (qq, _) in best.items():
            uni_q.setdefault(want[n], qq)
        time.sleep(1)
    for u, qq in MANUAL_QID.items():
        uni_q.setdefault(u, qq)
    missing = [u for u in unis if u not in uni_q]
    print('unresolved:', missing[:40])
    print('universities resolved', sum(1 for v in uni_q.values() if v), '/', len(uni_q))

    q2uni = defaultdict(set)
    for u, q in uni_q.items():
        if q:
            q2uni[q].add(u)
    people = defaultdict(lambda: defaultdict(set))  # uni -> key -> {qid}
    people_init = defaultdict(lambda: defaultdict(set))
    qs = list(q2uni)
    for i in range(0, len(qs), 20):
        vals = ' '.join('wd:' + q for q in qs[i:i + 20])
        q = f'''SELECT DISTINCT ?u ?p ?l WHERE {{
          VALUES ?u {{ {vals} }}
          ?p wdt:P108 ?u .
          {{ ?p wdt:P184 [] }} UNION {{ ?p wdt:P185 [] }} UNION {{ [] wdt:P184 ?p }}
          {{ ?p rdfs:label ?l FILTER(lang(?l)="en") }} UNION {{ ?p skos:altLabel ?l FILTER(lang(?l)="en") }}
        }}'''
        for b in sparql(q):
            for u in q2uni[qid(b['u']['value'])]:
                for k in keys(b['l']['value']):
                    people[u][k].add(qid(b['p']['value']))
                people_init[u][initial_key(b['l']['value'])].add(qid(b['p']['value']))
        time.sleep(1)

    found = {}
    for p in todo:
        cands = set()
        for k in keys(p['name']):
            cands |= people[p['uni']].get(k, set())
        # 只有表里名字本身是缩写（如 "J. Smith"）才按首字母匹配
        if not cands and len(fold(p['name']).split()[0]) == 1:
            cands = people_init[p['uni']].get(initial_key(p['name']), set())
        if len(cands) == 1:
            found[p['id']] = next(iter(cands))
    # 同一个 qid 只能给一位教授
    cnt = defaultdict(int)
    for q in found.values():
        cnt[q] += 1
    found = {k: v for k, v in found.items() if cnt[v] == 1}
    old = too_old(found.values())
    print('dropped (born <1930 / died <2005)', sum(1 for v in found.values() if v in old))
    found = {k: v for k, v in found.items() if v not in old}
    with open(os.path.join(ROOT, 'data/enrich/wikidata_employer_match.json'), 'w') as f:
        json.dump(found, f, indent=0)
    print('employer matches', len(found))
    for pid in list(found)[:15]:
        p = next(x for x in profs if x['id'] == pid)
        print(' ', p['name'], '|', p['uni'], '->', found[pid])


if __name__ == '__main__':
    main()
