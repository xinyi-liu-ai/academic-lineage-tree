"""用最新的 CSRankings 数据更新 data/raw/professors.csv（只限原有的 189 所学校）。

- 旧行顺序不变（网页链接 #/p/p{行号} 保持稳定），人工整理的导师列原样保留
- 仍在原校: status=current；换到名单内另一所学校: 更新学校, status=moved
- 已不在名单内任何学校: status=former（保留其师承关系，网页上标注）
- 新老师（在 CSRankings 收录的会议上 2020 年后有论文）追加到末尾, status=new
研究方向: 2016 年后在 CSRankings 各子领域会议上的论文数，取最多的一个（及占比 >= 30% 的其他方向）
用法: python scripts/update_faculty.py [--download]
"""
import csv
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import ROOT  # noqa: E402
from names import display_name, fold  # noqa: E402

SRC = 'https://raw.githubusercontent.com/emeryberger/CSrankings/gh-pages/'
DIR = os.path.join(ROOT, 'data/cache/csrankings')
FILES = ('csrankings.csv', 'generated-author-info.csv', 'dblp-aliases.csv')
# CSRankings 的机构写法 -> 本项目沿用的写法
INST = {'Air Force Inst. of Technology': 'Air Force Institute of Technology',
        'California Inst. of Technology': 'California Institute of Technology',
        'Massachusetts Inst. of Technology': 'Massachusetts Institute of Technology',
        'Missouri S&T': 'Missouri University of Technology',
        'Rochester Inst. of Technology': 'Rochester Institute of Technology',
        'Univ. of Louisiana - Lafayette': 'University of Louisiana - Lafayette',
        'Univ. of Maryland - College Park': 'University of Maryland - College Park',
        'Univ. of Massachusetts Amherst': 'University of Massachusetts Amherst',
        'Univ. of Missouri - Kansas City': 'University of Missouri - Kansas City',
        'Univ. of Wisconsin - Milwaukee': 'University of Wisconsin - Milwaukee'}
VENUES = dict(
    ai=('ai', 'aaai ijcai'), vision=('ai', 'cvpr eccv iccv'), ml=('ai', 'icml iclr kdd nips'), nlp=('ai', 'acl emnlp naacl'),
    ir=('ai', 'sigir www'), arch=('systems', 'asplos isca micro hpca'), networks=('systems', 'sigcomm nsdi'),
    security=('systems', 'ccs oakland usenixsec ndss pets'), db=('systems', 'sigmod vldb icde pods pacmmod'),
    eda=('systems', 'dac iccad'), embedded=('systems', 'emsoft rtas rtss'), hpc=('systems', 'hpdc ics sc'),
    mobile=('systems', 'mobicom mobisys sensys'), metrics=('systems', 'imc sigmetrics'),
    os=('systems', 'sosp osdi eurosys fast usenixatc'), pl=('systems', 'popl pldi icfp oopsla pacmpl'),
    se=('systems', 'icse fse ase issta'), theory=('theory', 'focs soda stoc'), crypto=('theory', 'crypto eurocrypt'),
    logic=('theory', 'cav lics'), bio=('interdisciplinary', 'ismb recomb'),
    graphics=('interdisciplinary', 'siggraph siggraph-asia eurographics'), csed=('interdisciplinary', 'sigcse'),
    ecom=('interdisciplinary', 'ec wine'), hci=('interdisciplinary', 'chiconf ubicomp uist'),
    robotics=('interdisciplinary', 'icra iros rss'), visualization=('interdisciplinary', 'vis vr'))
V2A = {v: a for a, (_, vs) in VENUES.items() for v in vs.split()}
HEADER = ['University Name', 'name', 'homepage', 'domain', 'google scholar', 'cvlink', 'PhD advisor', 'postdoc advisor',
          'status', 'now at']


def read(name):
    return list(csv.DictReader(open(os.path.join(DIR, name), encoding='utf-8')))


def main():
    os.makedirs(DIR, exist_ok=True)
    if '--download' in sys.argv or not all(os.path.exists(os.path.join(DIR, f)) for f in FILES):
        for f in FILES:
            urllib.request.urlretrieve(SRC + f, os.path.join(DIR, f))
    path = os.path.join(ROOT, 'data/raw/professors.csv')
    with open(path, encoding='utf-8', newline='') as f:
        old = list(csv.reader(f))[1:]
    ours = {r[0] for r in old}
    canon = {r['alias']: r['name'] for r in read('dblp-aliases.csv')}
    C = lambda n: canon.get(n, n)  # noqa: E731

    cur = defaultdict(set)  # name -> 名单内学校
    info = {}
    anywhere = defaultdict(set)
    for r in read('csrankings.csv'):
        u, n = INST.get(r['affiliation'], r['affiliation']), C(r['name'])
        anywhere[n].add(u)
        if u in ours:
            cur[n].add(u)
            info[(u, n)] = r
    pubs = defaultdict(lambda: defaultdict(Counter))  # (uni, name) -> year -> area -> count
    for r in read('generated-author-info.csv'):
        a = V2A.get(r['area'])
        if a:
            pubs[(INST.get(r['dept'], r['dept']), C(r['name']))][int(r['year'])][a] += float(r['count'])

    st = Counter()
    seen = set()
    rows = []
    by_disp = defaultdict(set)  # 去掉 dblp 编号后的名字 -> 当前名单里的写法（dblp 后来给同名的人加了编号）
    for n in cur:
        by_disp[fold(display_name(n))].add(n)

    def find(n, uni):
        if n in cur:
            return n
        if '?' in n:  # 旧表里的乱码名字：在同校当前名单里按通配符找回
            rx = re.compile(re.sub(r'(\\\?)+', '.{1,3}', re.escape(n)) + '$')
            hit = [m for (u, m) in info if u == uni and rx.match(m)]
            return hit[0] if len(hit) == 1 else n
        hit = by_disp.get(fold(display_name(n)), set())
        same_uni = [m for m in hit if uni in cur[m]]
        if len(same_uni) == 1:
            return same_uni[0]
        return next(iter(hit)) if len(hit) == 1 else n

    for r in old:
        r = (r + [''] * 10)[:10]
        was = r[8] if r[8] in ('new', 'moved') else 'current'
        seen.add(C(r[1]))
        n = find(C(r[1]), r[0])
        seen.add(n)
        if r[0] in cur.get(n, ()):
            r[8], r[9] = was, ''
        elif cur.get(n):
            r[0], r[8], r[9] = sorted(cur[n])[0], 'moved', ''
        else:
            r[8], r[9] = 'former', '; '.join(sorted(anywhere.get(n, ())))
        st[r[8]] += 1
        rows.append(r)

    def domain(k):
        c = Counter()
        for y, ac in pubs[k].items():
            if y >= 2016:
                c.update(ac)
        if not c:
            for ac in pubs[k].values():
                c.update(ac)
        top = max(c, key=c.get)
        tot = sum(c.values())
        keep = [top] + [a for a, v in c.most_common() if a != top and v / tot >= 0.3]
        return ' '.join(f'{a} ({VENUES[a][0]}-area)' for a in keep)

    for (u, n), r in sorted(info.items()):
        if n in seen or not any(y >= 2020 for y in pubs.get((u, n), {})):
            continue
        seen.add(n)
        sid = r['scholarid']
        scholar = f'https://scholar.google.com/citations?user={sid}' if sid and sid != 'NOSCHOLARPAGE' else ''
        rows.append([u, n, r['homepage'], domain((u, n)), scholar, '', '', '', 'new', ''])
        st['new'] += 1
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)
    # 同一人的其他写法（dblp 别名），供 build_graph 合并同人
    inv = defaultdict(set)
    for a, n in canon.items():
        inv[n].add(a)
    with open(os.path.join(ROOT, 'data/raw/faculty_aliases.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['name', 'alias'])
        for r in rows:
            c = C(r[1])
            for a in sorted((inv[c] | {c}) - {r[1]}):
                w.writerow([r[1], a])
    print(dict(st), 'total', len(rows))


if __name__ == '__main__':
    main()
