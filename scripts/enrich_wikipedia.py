"""从英文维基百科人物信息框补导师 / 学生（doctoral_advisor / doctoral_students / other_academic_advisors）。

只处理已在 Wikidata 匹配上、且有英文维基条目的教授。走 MediaWiki 官方 API。
输出: data/enrich/wikipedia.jsonl
"""
import json
import os
import re
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import ROOT  # noqa: E402
from enrich_wikidata import sparql, qid  # noqa: E402

HEAD = {'User-Agent': 'lineage-tree-research-script/0.1'}
FIELDS = {'doctoral_advisor': ('advisor', 'phd'), 'doctoral_advisors': ('advisor', 'phd'),
          'doctoral_students': ('student', 'phd'), 'other_academic_advisors': ('advisor', 'postdoc')}


def parse_people(val):
    """从信息框字段值里取出人名：优先 [[链接]]，否则按换行 / <br> / 逗号分隔。"""
    val = re.sub(r'<ref[^>]*/>|<ref.*?</ref>|<!--.*?-->', '', val, flags=re.S)
    links = re.findall(r'\[\[([^\]|#]+)(?:\|([^\]]+))?\]\]', val)
    if links:
        out = []
        for page, label in links:
            nm = re.sub(r'\s*\(.*?\)$', '', page).strip()
            out.append((nm, page.strip()))
        return out
    val = re.sub(r'\{\{(?:plain ?list|ubl|unbulleted list|flatlist)\s*\|', '', val, flags=re.I)
    parts = re.split(r'<br\s*/?>|\n\*|\||,', val)
    return [(p.strip(' *}{\n'), None) for p in parts if len(p.strip(' *}{\n').split()) >= 2]


def infobox_fields(wikitext):
    res = {}
    for f in FIELDS:
        m = re.search(r'\|\s*' + f + r'\s*=(.*?)(?=\n\s*\|\s*\w+\s*=|\n\}\})', wikitext, re.S)
        if m and m.group(1).strip():
            res[f] = m.group(1)
    return res


def main():
    match = json.load(open(os.path.join(ROOT, 'data/cache/wikidata_match.json')))
    q2p = {}
    for pid, q in match.items():
        q2p.setdefault(q, pid)
    # 取英文维基条目标题
    titles = {}
    qs = list(q2p)
    for i in range(0, len(qs), 300):
        vals = ' '.join('wd:' + q for q in qs[i:i + 300])
        for b in sparql(f'''SELECT ?i ?t WHERE {{ VALUES ?i {{ {vals} }}
              ?a schema:about ?i ; schema:isPartOf <https://en.wikipedia.org/> ; schema:name ?t }}'''):
            titles[b['t']['value']] = q2p[qid(b['i']['value'])]
        time.sleep(1)
    print('enwiki articles', len(titles))

    recs = []
    tl = list(titles)
    for i in range(0, len(tl), 20):
        batch = tl[i:i + 20]
        for _ in range(8):
            r = requests.get('https://en.wikipedia.org/w/api.php', headers=HEAD, timeout=60, params=dict(
                action='query', prop='revisions', rvprop='content', rvslots='main', format='json', formatversion=2,
                redirects=1, maxlag=5, titles='|'.join(batch)))
            if r.status_code == 200 and r.text.startswith('{') and 'query' in r.json():
                break
            time.sleep(int(r.headers.get('retry-after', 10)) + 2)
        else:
            print('skip batch', i)
            continue
        data = r.json()['query']
        norm = {n['to']: n['from'] for n in data.get('normalized', [])}
        redir = {n['to']: n['from'] for n in data.get('redirects', [])}
        for page in data.get('pages', []):
            if 'revisions' not in page:
                continue
            t = page['title']
            t = redir.get(t, t)
            t = norm.get(t, t)
            pid = titles.get(t)
            if not pid:
                continue
            text = page['revisions'][0]['slots']['main']['content']
            for f, val in infobox_fields(text).items():
                rel, typ = FIELDS[f]
                for nm, link in parse_people(val):
                    if not nm or len(nm) > 60 or re.search(r'universit|institute|college', nm, re.I):
                        continue
                    recs.append(dict(prof=pid, rel=rel, name=nm, type=typ, source='wikipedia',
                                     url='https://en.wikipedia.org/wiki/' + (link or t).replace(' ', '_')))
        time.sleep(3)

    with open(os.path.join(ROOT, 'data/enrich/wikipedia.jsonl'), 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print('relations', len(recs), 'advisor', sum(r['rel'] == 'advisor' for r in recs),
          'student', sum(r['rel'] == 'student' for r in recs))


if __name__ == '__main__':
    main()
