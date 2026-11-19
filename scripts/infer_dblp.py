"""从 dblp 合作论文推断博士导师：读博期间最常以「末位作者」出现、资历明显更高的合作者。

数据: dblp 全库 dump（CC0），放在 data/cache/dblp/dblp.xml.gz（不提交）
  curl -o data/cache/dblp/dblp.xml.gz https://dblp.org/xml/dblp.xml.gz
步骤:
  1. 扫描 dump：个人主页记录里的别名 -> 名单内教授的全部论文
  2. 每人确定读博窗口（dblp 收录博士论文则取论文年份前 5 年，否则取第一篇论文起 5 年）
  3. 候选人打分：末位作者次数（本人一作加权）、跨越年数、资历、是否公司研究员、是否在博士学校
  4. 只保留高置信度结果（得分 >= 3 且领先第二名足够多、非按字母排序、1995 年后入行）
在已知导师的 2177 人上检验，这一门槛下准确率约 83%（--eval 复现）。
输出: data/enrich/dblp.jsonl（build_graph 只在其他来源都没有导师时采用，一致时作为佐证）
用法: python scripts/infer_dblp.py [--rescan] [--eval]
"""
import gzip
import html
import json
import os
import pickle
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from names import display_name, fold  # noqa: E402

DUMP = os.path.join(ROOT, 'data/cache/dblp/dblp.xml.gz')
CACHE = os.path.join(ROOT, 'data/cache/dblp/index.pkl')
PAPER = {'article', 'inproceedings', 'incollection'}
TYPES = ('article', 'inproceedings', 'incollection', 'phdthesis', 'book', 'proceedings', 'mastersthesis', 'www')
START = re.compile(r'<(%s)\s' % '|'.join(TYPES))
SPLIT = re.compile(r'(?=<(?:%s)\s)' % '|'.join(TYPES))
END = re.compile(r'</(%s)>' % '|'.join(TYPES))
AUTH = re.compile(r'<author[^>]*>(.*?)</author>')
YEAR = re.compile(r'<year>(\d{4})</year>')
SCHOOL = re.compile(r'<school>(.*?)</school>')
AFF = re.compile(r'<note[^>]*type="affiliation"[^>]*>(.*?)</note>')
HOMEPAGE_KEY = re.compile(r'key="homepages/')
TAG = re.compile(r'<[^>]+>')
INDUSTRY = re.compile(r'microsoft|google|ibm|\bresearch\b|labs?\b|inc\b|corp|intel\b|nvidia|facebook|meta\b|amazon|'
                      r'adobe|yahoo|bell|at&t|xerox|hewlett|siemens|huawei|baidu|alibaba|tencent|deepmind|openai|apple',
                      re.I)
ACADEMIC = re.compile(r'universi|college|institut|école|ecole|school|eth\b|epfl|tu\b', re.I)
MIN_SCORE, MIN_MARGIN, MIN_FIRST_YEAR = 3, 0.8, 1995


def records():
    """逐行解析 dump，产出 (type, year, [authors], school, [affiliation notes], is_homepage)。"""
    cur = None
    with gzip.open(DUMP, 'rt', encoding='utf-8', errors='replace') as f:
        for line in (part for raw in f for part in SPLIT.split(raw)):  # 一行里可能有上一条的结尾和下一条的开头
            if cur is None:
                m = START.search(line)
                if not m:
                    continue
                cur = [m.group(1), None, [], None, [], bool(HOMEPAGE_KEY.search(line))]
            cur[2] += [html.unescape(TAG.sub('', a)) for a in AUTH.findall(line)]
            m = YEAR.search(line)
            if m:
                cur[1] = int(m.group(1))
            m = SCHOOL.search(line)
            if m:
                cur[3] = html.unescape(TAG.sub('', m.group(1)))
            cur[4] += [html.unescape(TAG.sub('', a)) for a in AFF.findall(line)]
            if END.search(line):
                yield tuple(cur)
                cur = None


def scan(profs):
    targets = {p['dblp'] for p in profs}
    alias = {}
    for t, _, auth, _, _, home in records():  # 第 1 遍：别名
        if t == 'www' and home:
            hit = [a for a in auth if a in targets]
            for a in auth if hit else ():
                alias.setdefault(a, hit[0])
    pubs = {n: [] for n in targets}
    for t, y, auth, school, _, _ in records():  # 第 2 遍：名单内教授的论文
        if t == 'www':
            continue
        auth = [alias.get(a, a) for a in auth]
        for a in set(auth) & targets:
            pubs[a].append((t, y, auth, school))
    cands = set()
    for p in profs:
        w = window(pubs.get(p['dblp'], []))
        for pub in (w[0] if w else ()):
            cands.update(pub[2])
    years, aff = defaultdict(Counter), defaultdict(list)
    for t, y, auth, _, notes, home in records():  # 第 3 遍：候选人的发表年份和机构
        if t == 'www':
            if home and notes:
                for a in auth:
                    if a in cands:
                        aff[a] += notes
        elif y:
            for a in set(auth) & cands:
                years[a][y] += 1
    print('aliases', sum(1 for a, t in alias.items() if a != t), 'professors with papers',
          sum(1 for v in pubs.values() if v), 'candidates', len(cands))
    return dict(pubs=pubs, years=dict(years), aff=dict(aff), targets=sorted(targets))


def surname(n):
    return display_name(n).split()[-1].lower()


def toks(s):
    stop = {'university', 'institute', 'technology', 'college', 'department', 'science', 'sciences', 'computer', 'school'}
    return {t for t in re.findall(r'[a-z]{4,}', fold(s or '')) if t not in stop}


def window(pubs):
    thesis = [p for p in pubs if p[0] == 'phdthesis' and p[1]]
    papers = [p for p in pubs if p[0] in PAPER and p[1] and len(p[2]) >= 2]
    if not papers:
        return None
    first = min(p[1] for p in papers)
    if thesis:
        y = min(p[1] for p in thesis)
        lo, hi, how = y - 5, y, 'thesis'
    else:
        lo, hi, how = first, first + 5, 'first-pub'
    return [p for p in papers if lo <= p[1] <= hi], (lo, hi), how, first, thesis[0][3] if thesis else None


def infer(me, data, uni_of):
    w = window(data['pubs'].get(me, []))
    if w is None:
        return None
    win, (lo, hi), how, first, school = w
    multi = [p for p in win if len(p[2]) >= 3]
    alpha = sum(1 for p in multi if [surname(a) for a in p[2]] == sorted(surname(a) for a in p[2]))
    is_alpha = len(multi) >= 3 and alpha / len(multi) > 0.6
    last, co, yrs = Counter(), Counter(), defaultdict(set)
    for p in win:
        for a in p[2]:
            if a != me:
                co[a] += 1
        L = p[2][-1]
        if L != me:
            last[L] += 1 + (p[2][0] == me)
            yrs[L].add(p[1])
    feats = {}
    for a in co:
        ys = data['years'].get(a, Counter())
        afirst = min(ys) if ys else None
        before = sum(v for y, v in ys.items() if y < lo)
        affs = ' | '.join(data['aff'].get(a, []))
        ind = bool(INDUSTRY.search(affs)) and not ACADEMIC.search(affs)
        at_school = bool(school) and (bool(toks(school) & toks(affs)) or
                                      any(toks(school) & toks(u) for u in uni_of.get(a, ())))
        s = co[a] * 0.5 if is_alpha else last[a] * min(len(yrs[a]), 3) / 3 + 0.25 * co[a]
        if afirst is not None and first - afirst < 3:
            s *= 0.3  # 同辈，不像导师
        if before < 5:
            s *= 0.5  # 读博开始前几乎没有论文
        if ind:
            s *= 0.4  # 公司研究员：多半是实习 mentor
        if at_school:
            s *= 1.5
        feats[a] = s
    ranked = sorted(feats, key=lambda a: -feats[a])[:3]
    if not ranked or feats[ranked[0]] <= 0:
        return None
    s1, s2 = feats[ranked[0]], (feats[ranked[1]] if len(ranked) > 1 else 0)
    return dict(advisor=ranked[0], score=s1, margin=s1 / (s1 + s2), alpha=is_alpha, first=first,
                window=(lo, hi), how=how, last=last[ranked[0]], top=ranked, coauthors=list(co))


def confident(r):
    return r and not r['alpha'] and r['first'] >= MIN_FIRST_YEAR and r['score'] >= MIN_SCORE and r['margin'] >= MIN_MARGIN


def nkey(n):
    t = [x for x in re.split(r'[\s.-]+', fold(display_name(n))) if x and x not in {'jr', 'sr', 'ii', 'iii'}]
    return (t[0][:1], t[-1]) if len(t) > 1 else ('', t[0] if t else '')


def same(a, truths):
    i, l = nkey(a)
    for t in truths:
        ti, tl = nkey(t)
        if ti == i and (tl == l or ('?' in tl and re.fullmatch(re.sub(r'(\\\?)+', '.{1,2}', re.escape(tl)), l))):
            return True
    return False


def evaluate(profs, data, uni_of):
    g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8'))
    nodes = {n['id']: n for n in g['nodes']}
    truth = defaultdict(set)
    for a, s, t, src, *_ in g['edges']:
        if t == 'phd' and s in nodes and not nodes[s].get('x') and set(src.split('+')) & {'wikidata', 'wikipedia'}:
            truth[s].add(nodes[a]['n'])
    rows = []
    for p in profs:
        if p['id'] in truth:
            r = infer(p['dblp'], data, uni_of)
            if r:
                rows.append((r, same(r['advisor'], truth[p['id']]), any(same(a, truth[p['id']]) for a in r['coauthors'])))
    n = len(rows)
    print(f'checked {n}: top1 {sum(x[1] for x in rows) / n:.3f}, advisor among coauthors {sum(x[2] for x in rows) / n:.3f}')
    C = [x for x in rows if confident(x[0])]
    print(f'confident: {len(C)} ({len(C) / n:.0%}), precision {sum(x[1] for x in C) / len(C):.3f}')


def main():
    profs = load_profs()
    if '--rescan' in sys.argv or not os.path.exists(CACHE) or \
            pickle.load(open(CACHE, 'rb'))['targets'] != sorted({p['dblp'] for p in profs}):
        data = scan(profs)
        pickle.dump(data, open(CACHE, 'wb'))
    else:
        data = pickle.load(open(CACHE, 'rb'))
    uni_of = defaultdict(set)
    dblp2id = {}
    for p in profs:
        uni_of[p['dblp']].add(p['uni'])
        dblp2id.setdefault(p['dblp'], p['id'])
    if '--eval' in sys.argv:
        evaluate(profs, data, uni_of)
    out = []
    for p in profs:
        r = infer(p['dblp'], data, uni_of)
        if not confident(r):
            continue
        adv = r['advisor']
        rec = dict(prof=p['id'], rel='advisor', name=display_name(adv), type='phd', source='dblp',
                   url='https://dblp.org/search?q=' + quote(display_name(adv)),
                   note=f'last author on {r["last"]} joint papers, {r["window"][0]}–{r["window"][1]}')
        if adv in dblp2id:
            rec['other_prof'] = dblp2id[adv]
        else:
            rec['ext_id'] = 'dblp:' + adv
        out.append(rec)
    with open(os.path.join(ROOT, 'data/enrich/dblp.jsonl'), 'w', encoding='utf-8') as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    print('inferred advisors', len(out))


if __name__ == '__main__':
    main()
