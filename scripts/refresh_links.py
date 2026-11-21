"""用最新 CSRankings 刷新教授链接，输出 data/enrich/link_updates.json。

(a) 我们的主页为空或已失效（people_pages 首页抓取报错），而 CSRankings 给出不同主页 -> 更新 homepage
(b) 我们没有 Google Scholar 链接，而 CSRankings 有 scholarid -> 补 scholar
匹配：dblp 名字（经别名规整）+ 同校优先；同名多校时若无法唯一确定则跳过。
"""
import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import ROOT, load_profs  # noqa: E402
from names import display_name, fold  # noqa: E402
from update_faculty import INST  # noqa: E402


def rd(p):
    with open(os.path.join(ROOT, p), encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def norm(u):
    return (u or '').strip().lower().rstrip('/').replace('https://', 'http://').replace('http://www.', 'http://')


def main():
    canon = {r['alias']: r['name'] for r in rd('data/cache/csrankings/dblp-aliases.csv')}
    for r in rd('data/raw/faculty_aliases.csv'):
        canon.setdefault(r['alias'], r['name'])
    idx = defaultdict(list)
    for r in rd('data/cache/csrankings/csrankings.csv'):
        r['aff'] = INST.get(r['affiliation'], r['affiliation'])
        idx[fold(display_name(canon.get(r['name'], r['name'])))].append(r)
    out = {}
    for p in load_profs():
        c = idx.get(fold(display_name(canon.get(p['dblp'], p['dblp']))), [])
        same = [r for r in c if r['aff'] == p['uni']]
        c = same or (c if len({r['aff'] for r in c}) == 1 else [])
        if not c:
            continue
        r = c[0]
        u = {}
        new_hp = r['homepage'].strip()
        if new_hp and norm(new_hp) != norm(p['homepage']):
            dead = not p['homepage']
            if not dead:
                try:
                    pg = json.load(open(os.path.join(ROOT, f"data/cache/people_pages/{p['id']}.json"), encoding='utf-8'))
                    dead = bool(pg['pages'][0].get('err'))
                except Exception:
                    pass
            if dead:
                u['homepage'] = new_hp
        sid = r['scholarid'].strip()
        if not p['scholar'] and sid and sid != 'NOSCHOLARPAGE':
            u['scholar'] = f'https://scholar.google.com/citations?user={sid}'
        if u:
            out[p['id']] = u
    path = os.path.join(ROOT, 'data/enrich/link_updates.json')
    old = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    for pid, u in out.items():  # 合并：这次算出的覆盖同名字段，其余（别的脚本 / 人工写的，如 TLS 修复）保留
        old.setdefault(pid, {}).update(u)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(old, f, ensure_ascii=False, indent=1)
    profs = {p['id']: p for p in load_profs()}
    print('total', len(out), 'homepage', sum('homepage' in v for v in out.values()),
          'scholar', sum('scholar' in v for v in out.values()))
    for k, v in list(out.items())[:10]:
        print(k, profs[k]['name'], '| old hp:', profs[k]['homepage'], '|', v)


main()
