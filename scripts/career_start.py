"""每位名单内教授的「入行年份」：dblp 上第一个 4 年内至少 3 篇论文的年份。

只取第一篇论文的年份容易被同名的人早年的一两篇论文带偏（如某人 1991 年一篇、2004 年起才连续发表），
所以要求连续发表。build_graph.py 用它检查「A 的学生 X 又是 P 的导师」这种链条在时间上是否说得通，
说不通就把名字相同但不是同一人的外部节点拆开。
数据: data/cache/dblp/index.pkl（infer_dblp.py 生成，不提交）
另外记下每位教授读博那几年（入行起 7 年）里和哪些名单内教授合写过论文，供 build_graph.py 判断
「导师主页名单上按名字认出的学生」是否真有师生合作。
输出: data/enrich/career_start.json  {pid: 年份}
      data/enrich/early_coauthors.json  {学生 pid: {合作教授 pid: 合写篇数}}
用法: python scripts/career_start.py
"""
import json
import os
import pickle
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402

INDEX = os.path.join(ROOT, 'data/cache/dblp/index.pkl')


def start_year(pubs):
    c = Counter(p[1] for p in pubs if p[1])
    return next((y for y in sorted(c) if sum(c[z] for z in range(y, y + 4)) >= 3), None)


def main():
    data = pickle.load(open(INDEX, 'rb'))
    profs = load_profs()
    pid_of = {p['dblp']: p['id'] for p in profs}
    out, early = {}, {}
    for p in profs:
        pubs = data['pubs'].get(p['dblp'], [])
        y = start_year(pubs)
        if not y:
            continue
        out[p['id']] = y
        co = Counter(pid_of[a] for t, yr, auth, _ in pubs if yr and y <= yr <= y + 6 and t != 'phdthesis'
                     for a in set(auth) if a in pid_of and a != p['dblp'])
        if co:
            early[p['id']] = dict(sorted(co.items()))
    for name, obj in (('career_start.json', out), ('early_coauthors.json', early)):
        with open(os.path.join(ROOT, 'data/enrich', name), 'w', encoding='utf-8') as f:
            json.dump(obj, f, separators=(',', ':'), sort_keys=True)
    print('professors with a start year:', len(out), 'with early co-authors:', len(early))


if __name__ == '__main__':
    main()
