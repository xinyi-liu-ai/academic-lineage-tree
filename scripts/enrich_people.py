"""给名单外的人（学生、外校导师）找个人主页、领英、Google Scholar 和现在的单位。

来源（按优先级）:
  1. CSRankings 全球教师名单（含国外学校）: 主页、单位、Scholar
  2. Wikidata: 官网 P856、领英 P2035、Scholar P1960
  3. dblp 个人档案 (homepages/ 记录): 主页、领英、Scholar 链接和单位备注
同名消歧:
  - 优先选「和他在图里相连的教授合写过论文」的那个 dblp 作者（合著认人，可靠）。
  - 否则只按名字配上（dblp / CSRankings 里只有一个同名的人）的，要和相连导师 / 学生的情况说得通才用：
    入行年份说得通（学生不早于导师入行 3 年以上、导师不晚于学生入行 8 年以上，按 dblp 档案所有别名的论文算），
    并且单位对得上（相连教授的学校，或导师页面上写的去向），或用别名和相连教授合写过 / 有共同合作者，
    或这个名字在 dblp 里没有同名的人（没被拆成带编号的几个作者）；相连教授在 dblp 索引里没有论文时只看年代。
  - 导师 / 学生页面上直接链了此人（student_links.json 或导师页面学生名单里的 h / li），而按名字配的数据
    和它对不上（不是同一地址、主页用户名不同，导师页面链的那个页面里也没提到这个单位 / 没链到这个主页或
    Scholar；领英页 robots 不让抓，只能比地址）：按名字配的主页 / 单位 / Scholar / 领英整个不要。
单位（dblp 备注）: 取现在的那条（没有 label 或 "since"），没有现在的就取最近一条过去的、写成 "Formerly X"
  （节点自己已有导师页面写的去向时不写）；等于他读博的学校（dblp "PhD" 备注或博士导师所在学校）的不当现单位、不写。
  有博士后边、dblp 只有一条单位备注且近 10 年才开始发表、单位又不是博士后所在学校的，也当作读博学校不写。
主页: 名录 / 团队页（/people /team /students /members 结尾且标题不是本人名字）不算；同一个主页对应几个不同的人不用。
领英统一成 https://www.linkedin.com/in/<slug>。check_links.py 修过的地址（link_updates.json 的 was -> homepage）沿用。
输出: data/enrich/people.json  {node_id: {h, li, g, af}}，由 build_graph.py 合并进节点
用法: python scripts/enrich_people.py [--rescan]
     （先跑过 build_graph.py、infer_dblp.py、career_start.py 和 match_people_links.py；dblp 扫描结果缓存在
      data/cache/dblp/enrich_scan.pkl，--rescan 重扫）
"""
import csv
import datetime
import gzip
import html
import json
import os
import pickle
import re
import sys
import time
from collections import Counter, defaultdict
from urllib.parse import unquote, urlparse

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT, uni_tokens  # noqa: E402
from enrich_wikidata import sparql, qid  # noqa: E402
from names import display_name, fold, key  # noqa: E402

DUMP = os.path.join(ROOT, 'data/cache/dblp/dblp.xml.gz')
INDEX = os.path.join(ROOT, 'data/cache/dblp/index.pkl')
SCAN = os.path.join(ROOT, 'data/cache/dblp/enrich_scan.pkl')
CSR = os.path.join(ROOT, 'data/cache/csrankings/csrankings.csv')
TYPES = ('article', 'inproceedings', 'incollection', 'phdthesis', 'book', 'proceedings', 'mastersthesis', 'www')
START = re.compile(r'<(%s)\s' % '|'.join(TYPES))
SPLIT = re.compile(r'(?=<(?:%s)\s)' % '|'.join(TYPES))
END = re.compile(r'</(%s)>' % '|'.join(TYPES))
AUTH = re.compile(r'<author[^>]*>(.*?)</author>')
YEAR = re.compile(r'<year>(\d{4})</year>')
URL = re.compile(r'<url[^>]*>(.*?)</url>')
AFF = re.compile(r'<note([^>]*)type="affiliation"([^>]*)>(.*?)</note>')
LABEL = re.compile(r'label="([^"]*)"')
TAG = re.compile(r'<[^>]+>')
SKIP_URL = re.compile(r'orcid\.org|wikidata|wikipedia|viaf\.org|id\.loc\.gov|isni|dl\.acm\.org|ieeexplore|scopus|zbmath|'
                      r'mathgenealogy|genealogy\.ams|researchgate|semanticscholar|dblp\.|openreview|twitter\.com|x\.com/|'
                      r'cienciavitae|d-nb\.info|catalogue\.bnf|github\.com|facebook|youtube|mathscinet|arxiv\.org|'
                      r'publons|webofscience|researcherid|loop\.frontiersin|academic\.microsoft|crossref|idref|isni|'
                      r'scholar\.google', re.I)
PHD_LABEL = re.compile(r'^\s*(?:former,?\s*)?(?:ph\.?\s?d|pdh|doctor)', re.I)
CUR_LABEL = re.compile(r'^\s*since\b', re.I)
THIS_YEAR = datetime.date.today().year


def advisor_page_fields(g):
    """导师 / 实验室页面学生名单（students_homepage.jsonl、students_lab.jsonl、人工表）上写的此人主页、领英、去向、
    是否在读，按 (导师, 名字键) 对到图里和这位导师相连的外部节点上。返回 {nid: {h, li, af, cur}}。"""
    nodes = {n['id']: n for n in g['nodes']}
    by = defaultdict(dict)
    for a, s, *_ in g['edges']:
        for p, o in ((a, s), (s, a)):
            if o in nodes and nodes[o].get('x'):
                by[p].setdefault(key(nodes[o]['n']), o)
    recs = []
    for f in ('students_homepage.jsonl', 'students_lab.jsonl'):
        p = os.path.join(ROOT, 'data/enrich', f)
        if os.path.exists(p):
            recs += [json.loads(line) for line in open(p, encoding='utf-8')]
    mp = os.path.join(ROOT, 'data/raw/manual_students.csv')
    if os.path.exists(mp):
        pid_of = {p['dblp']: p['id'] for p in load_profs()}
        for row in csv.DictReader(open(mp, encoding='utf-8', newline='')):
            if row['advisor'] in pid_of and row['student'].strip():
                recs.append(dict(prof=pid_of[row['advisor']], rel='student', name=row['student'].strip(),
                                 h=row.get('homepage', '').strip(), af=row.get('affiliation', '').strip()))
    out = defaultdict(dict)
    for r in recs:
        if r.get('rel') != 'student' or r.get('other_prof'):
            continue
        nid = by[r['prof']].get(key(r['name']))
        if not nid:
            continue
        for k in ('h', 'li', 'af'):
            if r.get(k):
                out[nid].setdefault(k, r[k])
        if r.get('cur'):
            out[nid]['cur'] = True
    return out


def start_year(years):
    c = Counter(years)
    y = next((y for y in sorted(c) if sum(c[z] for z in range(y, y + 4)) >= 3), None)
    return y or (min(c) if c else None)


def scan_dblp(want):
    """一遍扫 dump：名字（去编号后）在 want 里的 dblp 个人档案（主页 / 领英 / Scholar / 单位备注带 label），
    以及这些名字的论文年份和合作者（判断按名字配上的人年代、领域对不对得上）。"""
    surnames = {b.split()[-1] for b in want if b}
    memo = {}

    def base(a):
        b = memo.get(a)
        if b is None:
            s = display_name(a)
            last = re.split(r'[\s-]', s)[-1].lower()
            b = '' if (last.isascii() and last.isalpha() and last not in surnames) else fold(s)
            if b:
                memo[a] = b
        return b

    hp, base_count = {}, defaultdict(set)
    stats = {}
    cur = None
    with gzip.open(DUMP, 'rt', encoding='utf-8', errors='replace') as f:
        for line in (part for raw in f for part in SPLIT.split(raw)):
            if cur is None:
                m = START.match(line)
                if not m:
                    continue
                cur = [m.group(1), [], None, [], [], 'key="homepages/' in line[:200]]
            cur[1] += [html.unescape(TAG.sub('', a)) for a in AUTH.findall(line)]
            m = YEAR.search(line)
            if m:
                cur[2] = int(m.group(1))
            if cur[0] == 'www':
                cur[3] += [html.unescape(u) for u in URL.findall(line)]
                for a1, a2, txt in AFF.findall(line):
                    lab = LABEL.search(a1 + a2)
                    cur[4].append((lab.group(1) if lab else '', html.unescape(TAG.sub('', txt))))
            if not END.search(line):
                continue
            t, auth, y, urls, affs, home = cur
            cur = None
            if t == 'www':
                if not home or not auth:
                    continue
                hit = {fold(display_name(n)) for n in auth} & want
                if hit:
                    rec = dict(name=auth[0], names=auth, urls=urls, aff=affs)
                    for n in auth:
                        hp[n] = rec
                    for b in hit:
                        base_count[b].add(auth[0])
                continue
            mine = [a for a in auth if base(a) in want]
            for a in mine:
                st = stats.get(a)
                if st is None:
                    st = stats[a] = [Counter(), set()]
                if y:
                    st[0][y] += 1
                st[1].update(x for x in auth if x != a)
    return hp, dict(base_count), stats


def scan_stats(names):
    """补扫：这些确切的 dblp 名字（个人档案里的别名、CSRankings 名字）的论文年份和合作者。"""
    stats, cur = {}, None
    with gzip.open(DUMP, 'rt', encoding='utf-8', errors='replace') as f:
        for line in (part for raw in f for part in SPLIT.split(raw)):
            if cur is None:
                m = START.match(line)
                if not m or m.group(1) == 'www':
                    continue
                cur = [[], None]
            cur[0] += [html.unescape(TAG.sub('', a)) for a in AUTH.findall(line)]
            m = YEAR.search(line)
            if m:
                cur[1] = int(m.group(1))
            if not END.search(line):
                continue
            auth, y = cur
            cur = None
            for a in auth:
                if a in names:
                    st = stats.get(a)
                    if st is None:
                        st = stats[a] = [Counter(), set()]
                    if y:
                        st[0][y] += 1
                    st[1].update(x for x in auth if x != a)
    return stats


def classify(urls):
    from check_links import norm_li
    h = li = g = ''
    for u in urls:
        if 'linkedin.com/in' in u:
            li = li or norm_li(u)
        elif 'scholar.google' in u:
            g = g or u
        elif not SKIP_URL.search(u) and u.startswith('http'):
            h = h or u
    return h, li, g


def pick_aff(notes):
    """dblp 单位备注 [(label, 文字)] -> (现单位, 是否只是过去的单位)。没有 label / "since" 的是现在的；
    "former"、"2012-2015"、"until 2019" 是过去的（取年份最近的一条）；"PhD 2016" 是读博学校，不当单位。"""
    cur = [t for lab, t in notes if not lab.strip() or CUR_LABEL.match(lab)]
    if cur:
        return cur[0], False
    past = []
    for i, (lab, t) in enumerate(notes):
        if PHD_LABEL.match(lab) or re.match(r'^\s*(?:bs|ms|master|bachelor)', lab, re.I):
            continue
        ys = [int(y) for y in re.findall(r'(?:19|20)\d\d', lab)]
        past.append((max(ys) if ys else 0, -i, t))
    if past:
        return max(past)[2], True
    return '', False


def inst_match(af, uni):
    """单位文字 af（dblp 备注 / CSRankings / 导师页面写的去向）是不是学校 uni。按逗号分段，某段的关键词
    和学校的一致即可（多出的只能是 UCLA 这类缩写）。"""
    ut = uni_tokens(uni)
    if not ut or not af:
        return False
    parts = [x for x in re.split(r'[,;]', af) if x.strip()]
    for seg in parts + [a + ' ' + b for a, b in zip(parts, parts[1:])]:  # 也试相邻两段（"University of California, San Diego"）
        st = uni_tokens(seg.replace('University of California at', 'University of California'))
        extra = st - ut
        if ut <= st and all(re.search(r'\b' + x.upper() + r'\b', seg) for x in extra):
            return True
    return False


def main():
    t0 = time.time()
    from check_links import GENERIC_SEG, distinct_people, norm_li, norm_url, not_personal, page_text
    g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8'))
    nodes = {n['id']: n for n in g['nodes']}
    profs = {p['id']: p for p in load_profs()}
    ext = {i: n for i, n in nodes.items() if n.get('x') and i not in profs}
    linked = defaultdict(set)  # 外部节点 -> 相连的名单内教授
    stu_of = defaultdict(list)  # 外部节点作为学生：[(教授, 类型)]
    adv_of = defaultdict(set)  # 外部节点作为导师：他带的教授
    for a, s, t, *_ in g['edges']:
        if a in ext and s in profs:
            linked[a].add(s)
            adv_of[a].add(s)
        if s in ext and a in profs:
            linked[s].add(a)
            stu_of[s].append((a, t))
    own = advisor_page_fields(g)
    links_path = os.path.join(ROOT, 'data/enrich/student_links.json')
    slinks = json.load(open(links_path, encoding='utf-8')) if os.path.exists(links_path) else {}
    lu_path = os.path.join(ROOT, 'data/enrich/link_updates.json')
    lu = json.load(open(lu_path, encoding='utf-8')) if os.path.exists(lu_path) else {}
    fixes = {v['was']: v['homepage'] for v in lu.values() if isinstance(v, dict) and v.get('was') and v.get('homepage')}
    cstart = json.load(open(os.path.join(ROOT, 'data/enrich/career_start.json'), encoding='utf-8'))

    # 1) 通过合著消歧：相连教授的合作者里，和外部节点同名的 dblp 作者
    index = pickle.load(open(INDEX, 'rb'))['pubs']
    chosen = {}
    for i, n in ext.items():
        k = key(n['n'])
        cands = set()
        for pid in linked[i]:
            for pub in index.get(profs[pid]['dblp'], []):
                for au in pub[2]:
                    if key(display_name(au)) == k:
                        cands.add(au)
        if len(cands) == 1:
            chosen[i] = next(iter(cands))
    print('matched via co-authorship', len(chosen), '/', len(ext), f'{time.time() - t0:.0f}s', flush=True)

    csr = defaultdict(list)
    for r in csv.DictReader(open(CSR, encoding='utf-8')):
        csr[r['name']].append(r)
        csr['~' + fold(display_name(r['name']))].append(r)
    want = {fold(display_name(n['n'])) for n in ext.values()} | {fold(display_name(c)) for c in chosen.values()}
    data = pickle.load(open(SCAN, 'rb')) if os.path.exists(SCAN) and '--rescan' not in sys.argv else None
    if not data or not want <= data['want']:
        hp, base_count, stats = scan_dblp(want)
        data = dict(want=want, hp=hp, base_count=base_count, stats=stats)
        pickle.dump(data, open(SCAN, 'wb'))
    hp, base_count, stats = data['hp'], data['base_count'], data['stats']
    # 个人档案里的别名（C. A. R. Hoare / Tony Hoare）和 CSRankings 名字的论文不一定在上面扫到：补扫，否则入行年份会算错
    extra = ({a for rec in hp.values() for a in rec['names']} |
             {r['name'] for k, rs in csr.items() if k.startswith('~') and k[1:] in want for r in rs}) - set(stats)
    if not extra <= data.get('extra', set()):
        data['extra'] = extra | data.get('extra', set())
        data['stats_extra'] = scan_stats(data['extra'])
        pickle.dump(data, open(SCAN, 'wb'))
    stats = {**data.get('stats_extra', {}), **stats}
    homonyms = Counter(fold(display_name(a)) for a in stats)  # 去编号后同名的 dblp 作者有几个
    print('dblp homepage records', len({id(v) for v in hp.values()}), 'authors with stats', len(stats),
          f'{time.time() - t0:.0f}s', flush=True)

    # 2) Wikidata 官网 / 领英 / Scholar
    wd = {i: i[3:] for i in ext if i.startswith('wd:')}
    wdinfo = defaultdict(dict)
    qs = list(wd.values())
    for j in range(0, len(qs), 300):
        vals = ' '.join('wd:' + q for q in qs[j:j + 300])
        q = f'''SELECT ?i ?site ?li ?gs WHERE {{ VALUES ?i {{ {vals} }}
          OPTIONAL {{ ?i wdt:P856 ?site }} OPTIONAL {{ ?i wdt:P2035 ?li }} OPTIONAL {{ ?i wdt:P1960 ?gs }} }}'''
        for b in sparql(q):
            d = wdinfo[qid(b['i']['value'])]
            if 'site' in b:
                d.setdefault('h', b['site']['value'])
            if 'li' in b:
                d.setdefault('li', 'https://www.linkedin.com/in/' + b['li']['value'])
            if 'gs' in b:
                d.setdefault('g', 'https://scholar.google.com/citations?user=' + b['gs']['value'])
        time.sleep(1)
    print('wikidata items with links', sum(1 for v in wdinfo.values() if v), f'{time.time() - t0:.0f}s', flush=True)

    adv_co = {}

    def coauthors_of(pid):
        if pid not in adv_co:
            s = set()
            for pub in index.get(profs[pid]['dblp'], []):
                s.update(pub[2])
            adv_co[pid] = s
        return adv_co[pid]

    def consistent(i, dnames, affs, from_csr):
        """按名字配上的 dblp / CSRankings 人和这个节点的导师 / 学生情况是否说得通。返回 (是否, 原因)。"""
        ys, co = Counter(), set()
        for d in dnames:
            st = stats.get(d)
            if st:
                ys.update(st[0])
                co |= st[1]
        sy = start_year(ys)
        o = own.get(i, {})
        # （导师页面上的"在读"标记常是旧页面留下的，不拿来判断年代）
        for p, _ in stu_of[i]:
            if sy and cstart.get(p) and sy < cstart[p] - 3:
                return False, f'era:student-starts-{sy}-before-advisor-{cstart[p]}'
        for p in adv_of[i]:  # 外系 / 老一辈导师的论文 dblp 常收得晚，放宽到 8 年
            if sy and cstart.get(p) and sy > cstart[p] + 8:
                return False, f'era:advisor-starts-{sy}-after-student-{cstart[p]}'
        unis = [profs[p]['uni'] for p in linked[i]]
        if any(inst_match(a, u) for a in affs for u in unis):
            return True, 'institution'
        if o.get('af') and any(set(uni_tokens(a)) and uni_tokens(a) <= uni_tokens(o['af']) for a in affs if a):
            return True, 'institution:advisor-page'
        own_names = set(dnames)
        if any(own_names & coauthors_of(p) for p in linked[i]):  # 用别名和相连教授合写过（名字键不同没被合著认出）
            return True, 'field:coauthor-under-alias'
        if any((coauthors_of(p) - own_names) & co for p in linked[i]):
            return True, 'field:shared-coauthor'
        if sy and not any(coauthors_of(p) for p in linked[i]):  # 相连教授在 dblp 索引里没有论文：只能看年代
            return True, 'era-only:no-advisor-pubs'
        # 名字在 dblp 里没有被拆成几个同名作者（没有 "Chi Zhang 0003" 这类编号），年代又说得通：同名撞车的风险低
        bases = {fold(display_name(d)) for d in dnames}
        if sy and all(homonyms.get(b, 0) <= 1 for b in bases):
            return True, 'era+unique-name-in-dblp'
        return False, 'no-institution-or-coauthor-overlap'

    def user_tokens(u):
        p = urlparse(u)
        segs = [x.lower().lstrip('~') for x in unquote(p.path).split('/') if x]
        segs += [v.lower() for v in re.findall(r'=([A-Za-z][\w.-]{3,})', p.query)]
        if not segs:
            segs = [p.netloc.lower().removeprefix('www.').split('.')[0]]
        return {re.sub(r'\.(?:s?html?|php)$', '', x) for x in segs if len(x) >= 4} - GENERIC_SEG

    def corroborated(info, src, adv):
        """按名字配上的数据和导师页面上的链接是不是同一个人：同一地址、主页用户名相同，
        或导师页面链的那个页面里提到了这个单位 / 链到了这个主页或 Scholar。返回依据或 ''。"""
        mine = {k2: v for k2, v in info.items() if src.get(k2) in ('csr', 'dblp')}
        for k2, v in mine.items():
            if k2 in adv and (norm_li(v) == norm_li(adv[k2]) if k2 == 'li' else norm_url(v) == norm_url(adv[k2])):
                return 'same-url'
        if mine.get('h') and adv.get('h') and user_tokens(mine['h']) & user_tokens(adv['h']):
            return 'same-username'
        for k2 in ('h', 'g'):  # 领英抓不了（robots），只看主页 / Scholar 以外的页面
            if not adv.get(k2) or 'scholar.google' in adv[k2]:
                continue
            txt = page_text(adv[k2])
            if not txt:
                continue
            low = unquote(txt.lower())
            flat = low.replace('https://', '').replace('http://', '').replace('www.', '')
            if mine.get('h') and norm_url(mine['h']).lower() in flat:
                return 'advisor-link-page-links-it'
            m = re.search(r'user=([\w-]+)', mine.get('g', ''))
            if m and m.group(1).lower() in low:
                return 'advisor-link-page-links-it'
            if mine.get('af') and uni_tokens(mine['af']) and uni_tokens(mine['af']) <= set(re.findall(r'[a-z]{4,}', low)):
                return 'advisor-link-page-mentions-affiliation'
        return ''

    out, how, stat = {}, Counter(), Counter()
    log = defaultdict(list)
    for i, n in ext.items():
        b = fold(display_name(n['n']))
        dname = chosen.get(i)
        name_only = False
        if not dname and len(base_count.get(b, ())) == 1:
            dname = next(iter(base_count[b]))  # dblp 里只有一个同名的人
            name_only = True
        rec = hp.get(dname) if dname else None
        rows = csr.get(dname, []) if dname else []
        if not rows and not dname and len(csr.get('~' + b, [])) == 1:
            rows = csr['~' + b]
            name_only = True
        if len(rows) != 1:
            rows = []
        src, info = {}, {}
        if name_only and (rec or rows):
            dn = set(rec['names'] if rec else []) | ({rows[0]['name']} if rows else set()) | ({dname} if dname else set())
            affs = ([rows[0]['affiliation']] if rows else []) + ([t for _, t in rec['aff']] if rec else [])
            ok, why = consistent(i, dn, affs, bool(rows) and not rec)
            stat['name_only_checked'] += 1
            if not ok:
                stat['rule1_name_only_rejected'] += 1
                log['rule1_rejected'].append((i, n['n'], why, rows[0]['affiliation'] if rows else (rec['aff'][:1] if rec else '')))
                rec, rows = None, []
        if rows:
            r = rows[0]
            for k2, v in (('h', r['homepage']), ('af', r['affiliation'])):
                if v:
                    info[k2], src[k2] = v, 'csr'
            if r['scholarid'] and r['scholarid'] != 'NOSCHOLARPAGE':
                info['g'], src['g'] = 'https://scholar.google.com/citations?user=' + r['scholarid'], 'csr'
            how['csrankings'] += 1
        w = wdinfo.get(wd.get(i, ''), {})
        for k2 in ('h', 'li', 'g'):
            if w.get(k2) and not info.get(k2):
                info[k2], src[k2] = w[k2], 'wd'
        if rec:
            h, li, gs = classify(rec['urls'])
            for k2, v in (('h', h), ('li', li), ('g', gs)):
                if v and not info.get(k2):
                    info[k2], src[k2] = v, 'dblp'
            if rec['aff'] and not info.get('af'):
                old_first = rec['aff'][0][1]
                af, former = pick_aff(rec['aff'])
                phd = [t for lab, t in rec['aff'] if PHD_LABEL.match(lab)]
                phd += [profs[p]['uni'] for p, t in stu_of[i] if t == 'phd']
                is_phd = bool(af) and any(inst_match(af, u) or fold(af) == fold(u) for u in phd if u)
                # 有博士后边、dblp 只有一条没更新过的单位备注、又是近 10 年才开始发表的：这条多半是读博的学校，
                # 不是博士后所在学校就不当现单位（如 USC 博士、现 UC Berkeley 博士后）
                post = [profs[p]['uni'] for p, t in stu_of[i] if t == 'postdoc']
                ys = Counter()
                for nm in rec['names']:
                    ys.update(stats.get(nm, [Counter()])[0])
                sy = start_year(ys)
                if af and not is_phd and not former and post and len(rec['aff']) == 1 and sy and sy >= THIS_YEAR - 10 \
                        and not any(inst_match(af, u) or fold(af) == fold(u) for u in post):
                    is_phd = True
                    stat['rule2_af_postdoc_earlier_school'] += 1
                if is_phd:
                    stat['rule2_af_is_phd_school_dropped'] += 1
                    log['rule2_phd'].append((i, n['n'], af))
                    af = ''
                elif former:
                    if own.get(i, {}).get('af'):  # 导师页面写了去向：不用过去的单位盖掉它
                        af = ''
                    else:
                        af = 'Formerly ' + af
                    stat['rule2_af_only_past_formerly' if af else 'rule2_af_only_past_dropped'] += 1
                    log['rule2_former'].append((i, n['n'], af or '(dropped)'))
                if af != old_first and not is_phd and not former:
                    k3 = 'rule2_af_latest_instead_of_first' if af else 'rule2_af_only_phd_note_dropped'
                    stat[k3] += 1
                    log[k3].append((i, n['n'], old_first, '->', af))
                if af:
                    info['af'], src['af'] = af, 'dblp'
        # 导师 / 学生页面上直接链了此人：按名字配的数据对不上就整个不要
        adv = {k2: v for k2, v in (slinks.get(i, {}) | own.get(i, {})).items()
               if k2 in ('h', 'li', 'g') and v and not re.search(r'urldefense\.|safelinks\.protection', v)}
        if name_only and adv and any(s in ('csr', 'dblp') for s in src.values()):
            why = corroborated(info, src, adv)
            if why:
                stat['rule1_advisor_link_corroborated_' + why] += 1
            else:
                dropped = {k2: info.pop(k2) for k2 in list(info) if src.get(k2) in ('csr', 'dblp')}
                stat['rule1_dropped_conflicts_with_advisor_page_link'] += 1
                log['rule1_advisor_link'].append((i, n['n'], adv, dropped))
        if info.get('h'):
            info['h'] = fixes.get(info['h'], info['h'])
            if not_personal(info['h'], n['n']):
                stat['rule4_directory_h_dropped' + ('_wikidata' if src.get('h') == 'wd' else '')] += 1
                log['rule4_dir'].append((i, n['n'], info['h']))
                del info['h']
        if info.get('li'):
            li = norm_li(info['li'])
            if li != info['li']:
                stat['rule5_li_normalised'] += 1
            info['li'] = li
        info = {k2: v for k2, v in info.items() if v}
        if info:
            out[i] = info

    # 同一个主页对应好几个不同的人（名首字母不同或姓明显不同）：不用
    owners = defaultdict(set)
    for i, n in ext.items():
        for u in {out.get(i, {}).get('h'), own.get(i, {}).get('h'), slinks.get(i, {}).get('h')} - {None, ''}:
            owners[norm_url(u)].add(n['n'])
    for i in list(out):
        h = out[i].get('h')
        if h and distinct_people(owners[norm_url(h)]):
            stat['rule4_shared_h_dropped'] += 1
            log['rule4_shared'].append((i, ext[i]['n'], h))
            del out[i]['h']
            if not out[i]:
                del out[i]
    for i, o in own.items():  # 导师页面上的主页被 check_links.py 换成了能打开的地址（TLS 变体）
        if o.get('h') in fixes and i in ext and not out.get(i, {}).get('h'):
            out.setdefault(i, {})['h'] = fixes[o['h']]
    for info in out.values():
        for k2 in info:
            how[k2] += 1
    with open(os.path.join(ROOT, 'data/enrich/people.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=0)
    with open(os.path.join(ROOT, 'data/cache/dblp/enrich_people_log.json'), 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, indent=0)
    print('external people', len(ext), 'with any info', len(out), dict(how), f'{time.time() - t0:.0f}s')
    print(json.dumps(dict(stat), indent=1))


if __name__ == '__main__':
    main()
