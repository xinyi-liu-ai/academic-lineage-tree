"""把教授表 + 自动补全结果合成一张导师关系图，输出 docs/data/graph.json。

用法: python scripts/build_graph.py
输入:
  data/raw/professors.csv                    人工整理的 5080 位教授
  data/raw/universities.csv                 学校全称/简称
  data/enrich/*.jsonl                       自动补全的关系（Wikidata / MGP）
"""
import datetime
import glob
import json
import os
import re
import unicodedata
import sys
from collections import defaultdict

import csv

sys.path.insert(0, os.path.dirname(__file__))
from names import display_name, key, split_advisors, wildcard_regex, fold  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 占位图 / 签名失效的照片链接（RIT claws 签名链接现在一律返回默认老虎图）
PHOTO_PLACEHOLDER = re.compile(r'(?i)claws\.rit\.edu/photos/getphotoid\.php|fallback-[\w-]*-portrait|no-?profile(?:-?pic)?|noprofilepic|'
                               r'/generic\.(?:jpe?g|png)|faculty-no-profile-pic')


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


def load_new_faculty(profs, g):
    """把 new_faculty.json 的老师加成教授节点。返回 ([nf id], {nf id: 职称 / 照片 / 招生 / 入职 / 导师 / 来源})。"""
    path = os.path.join(ROOT, 'data/enrich/new_faculty.json')
    if not os.path.exists(path):
        return [], {}
    parent = {}
    for p in profs:
        for a, b in parse_domains(p['domain']):
            parent[a] = b
    ids, extra = [], {}
    for r in json.load(open(path, encoding='utf-8')):
        nid = 'nf:' + key(r['name'])
        if nid in g.nodes:
            continue
        dom = ' '.join(f"{a} ({parent.get(a, 'interdisciplinary')}-area)" for a in r.get('areas') or [])
        g.nodes[nid] = dict(id=nid, name=r['name'], dblp=r['name'], uni=r['uni'], homepage=r.get('homepage') or '', domain=dom,
                            scholar=r.get('scholar') or '', cv='', phd_raw='', post_raw='', status='current', now_at='',
                            nf=r.get('source_url') or '')
        g.by_key[key(r['name'])].append(nid)
        g.prof_folded[fold(r['name'])] = nid
        ids.append(nid)
        extra[nid] = r
    return ids, extra


def parse_domains(d):
    # 'ml (ai-area) theory (theory-area)' -> [('ml','ai'), ('theory','theory')]
    return [(a, b) for a, b in re.findall(r'([\w-]+)\s*\(([\w-]+)-area\)', d)]


def uni_tokens(u):
    u = u.replace('Univ.', 'University').replace(' - ', ' ')
    return {t for t in re.findall(r'[a-z]{4,}', u.lower()) if t not in {'university', 'state', 'college', 'institute', 'technology'}}


# 按名字对上教授的 Wikidata 条目（wikidata_match_method.json 里不是 scholar_id 的），描述的是不是同名的另一个人
WD_CS = re.compile(r'comput|informat|software|programm|machine learning|artificial intelligence|robot|data|electric|'
                   r'electronic|cryptograph|engineer|network|cyber|algorithm|human.computer|vision|natural language', re.I)
WD_OTHER = re.compile(r'(bio)?statistic\w*|econom\w*|biochem\w*|biolog\w*|chemist\w*|physician|medic\w*|cancer|clinic\w*|'
                      r'psycholog\w*|neuroscien\w*|ecolog\w*|environment\w*|marine|genetic\w*|epidemiolog\w*|pharma\w*|'
                      r'physicist|sociolog\w*|anthropolog\w*|philosoph\w*|historian|industrial engineer|nurs\w*|'
                      r'business\w*|financ\w*|lawyer|jurist|athlete|football\w*|actor|actress|musician|composer|artist|'
                      r'photographer|politician|journalist|priest|geolog\w*|agricult\w*|veterinar\w*|mathemat\w*', re.I)
WD_PHD_YEAR = re.compile(r'(?:ph\.?\s?d\.?|doctoral|doctorate)\D{0,80}?\b((?:19|20)\d\d)\b', re.I)


def ilast(name):
    """(名首字母, 姓)，比较不同来源写法的同一个人用（"Prof. Manoj Franklin" ~ "Manoj Franklin"）。"""
    t = [x for x in re.split(r'[\s.,-]+', fold(display_name(name))) if x and x not in {'prof', 'professor', 'dr', 'jr', 'sr', 'ii', 'iii'}]
    return (t[0][:1], t[-1]) if len(t) > 1 else ('', t[0] if t else '')


def wd_misfit(p, w, start, wd_adv=(), sheet_adv=()):
    """w: wikidata_match_info.json 里的条目信息（co = [和教授在 dblp 上合写过的 Wikidata 导师/学生数, 关系总数]），
    start: 教授的 career_start；wd_adv / sheet_adv: 条目上 / 人工表里这位教授的博士导师（ilast）。
    条目的导师 / 学生没有一个和教授合写过论文、和人工表的导师也对不上、且条目本身对不上时返回理由：
      scholar — 条目有另一个 Google Scholar ID，且任职单位对不上或领域不像 CS（如 UIUC 的统计学家 Bo Li）；
      field   — 描述 / 职业 / 任职单位是别的领域（统计、经济、生物、数学……）且描述 / 职业没有 CS 字样，任职单位也对不上；
      sheet   — 人工表有导师、条目的导师和它完全不同，且任职单位对不上（如 Seyoung Kim：表里 Eric Xing，条目是经济学博士）；
      phd_year — 描述里的博士年份（多是 Mathematics Genealogy 导入的 "Ph.D. <学校> <年份>"）比教授 dblp 入行早 12 年以上
                 或晚 12 年以上，且没有任何 CS 字样、任职单位对不上。
    有合写佐证、没有可核对的关系或 dblp 上没有教授的论文时返回 None（不判断）。"""
    co = w.get('co')
    if not co or not co[1] or co[0] or set(wd_adv) & set(sheet_adv):
        return None
    text = ' '.join([w.get('d') or ''] + (w.get('occ') or []))
    emp = '|'.join(w.get('emp') or [])
    emp_ok = bool(uni_tokens(p['uni']) & uni_tokens(emp))
    m = re.search(r'user=([\w-]{12})', p.get('scholar') or '')
    gs_conflict = bool(m and w.get('gs') and m.group(1) not in w['gs'])
    other = bool(WD_OTHER.search(text + ' ' + emp)) and not WD_CS.search(WD_OTHER.sub(' ', text))
    if gs_conflict and (not emp_ok or other):
        return 'scholar'
    if emp_ok:
        return None
    if other:
        return 'field'
    if wd_adv and sheet_adv:
        return 'sheet'
    y = WD_PHD_YEAR.search(w.get('d') or '')
    if y and start and not WD_CS.search(text) and not start - 12 <= int(y.group(1)) <= start + 12:
        return 'phd_year'
    return None


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
        self.prof_data = {}

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

    def add_edge(self, adv, stu, typ, src, url='', ext=None, named=None):
        """ext: 外部人员在这条来源里的强标识（Wikidata QID / 维基百科词条），拆分同名节点时用。
        named: 这条来源里哪一端（'a' 导师 / 's' 学生）只是按名字认成名单内教授的；None 表示两端都可靠
        （人工表、本人的 CV / 主页、QID 对上教授的 Wikidata）。见 detach_implausible。"""
        if adv == stu:
            return
        e = self.edges.get((adv, stu, typ))
        if e:
            if src not in e['src']:
                e['src'].append(src)
            if url and not e.get('url'):
                e['url'] = url
        else:
            e = self.edges[(adv, stu, typ)] = dict(a=adv, s=stu, t=typ, src=[src], url=url)
        if url:
            e.setdefault('pages', set()).add(url)  # 各来源的出处页面（只供拆分时判断主页链接归属）
            e.setdefault('su', {}).setdefault(src, url)  # 每个来源自己的出处链接
        if ext and ext.split(':')[0] in ('wd', 'wp'):
            e.setdefault('ids', set()).add(ext)
        if named:
            e.setdefault('named', set()).add(named)
        else:
            e['firm'] = True
        return e

    def move_edge(self, e, old, new):
        del self.edges[(e['a'], e['s'], e['t'])]
        a, s = (new if e['a'] == old else e['a']), (new if e['s'] == old else e['s'])
        prev = self.edges.get((a, s, e['t']))
        if prev:
            prev['src'] += [x for x in e['src'] if x not in prev['src']]
            for k in ('ids', 'pages', 'named'):
                prev.setdefault(k, set()).update(e.get(k, ()))
            for k, v in (e.get('su') or {}).items():
                prev.setdefault('su', {}).setdefault(k, v)
            prev['firm'] = prev.get('firm') or e.get('firm')
        else:
            e['a'], e['s'] = a, s
            self.edges[(a, s, e['t'])] = e

    # 人工核对过、确实成立的教授→教授边，不受 detach_implausible 影响（入行年份被同名者 / 拆开的 dblp 档案带偏）
    KEEP_PROF_EDGES = {
        ('p935', 'p1416'),   # Aaron Sidford → Kevin Tian（Stanford）：Kevin Tian 的 dblp 年份混进了同名者
        ('p5557', 'p2950'),  # Alex X. Liu（MSU）→ Zubair Shafiq：dblp 上 "Alex Liu" 档案只从 2011 年算起
        ('p949', 'p1214'),   # Gregory Valiant → Hongyang R. Zhang（Stanford，与 Ashish Goel 共同指导）：合写论文在读博后期
    }
    # 另：Yao Xie / Kun Sun 主页上的学生 "Jie Wang" 都不是名单里的 Jie Wang 0002（UMass Lowell，1989 年起发表，
    # 和两人都没有合写过论文），两条边都按规则摘下，不放行

    def _detach(self, e, why, moved):
        a, s = e['a'], e['s']
        if 's' in e['named']:
            old, new = s, f"hs:{a}:{key(self.nodes[s]['name'])}"
        else:
            old, new = a, f"x:{s}:{key(self.nodes[a]['name'])}"
        nn = self.nodes.setdefault(new, dict(id=new, name=self.nodes[old]['name'], ext=True))
        for k, v in (e.get('listed') or {}).items():  # 学生名单上写的去向 / 主页 / 在读标记
            nn.setdefault(k, v)
        moved.append((dict(a=a, s=s, src=list(e['src']), why=why), new))
        self.move_edge(e, old, new)

    def detach_implausible(self, start, titles, ap_since, early=None, slack=3, max_adv=2):
        """教授→教授的博士边，若只靠按名字认人的来源（导师主页学生名单、按名字对上的 Wikidata / 维基百科、
        dblp 推断），且时间上说不通，就不挂到这位教授身上：
          - 导师入行（career_start）比学生晚 slack 年以上；
          - 导师 AP 入职（ap_since）不早于学生 AP 入职前 slack 年；
          - 导师是 Assistant Professor，学生已是 Associate / Full / Distinguished Professor。
        按名字认错的是学生一端时改成导师名下的外部学生 hs:<导师>:<名字>（和没对上名单的学生一样），
        是导师一端时改成 x:<学生>:<名字>。
        第二轮：PhD 导师超过 max_adv 位的教授，只靠按名字认人的边若没有 dblp 佐证（2b 的合著推断一致，
        或 early_coauthors 里学生读博那几年和这位导师合写过论文），也照此摘下（why='crowded'）。
        返回 [(原边信息, 新端点)]。"""
        junior = re.compile(r'assistant professor', re.I)
        senior = re.compile(r'(associate |distinguished )?professor( emeritus)?', re.I)
        ap = {k: v['y'] for k, v in ap_since.items() if v.get('y') and not v.get('incoming')}
        moved = []
        for e in list(self.edges.values()):
            a, s = e['a'], e['s']
            if e['t'] != 'phd' or e.get('firm') or not e.get('named') or \
                    self.nodes[a].get('ext') or self.nodes[s].get('ext'):
                continue
            if (a, s) in self.KEEP_PROF_EDGES:
                continue
            why = [w for w, bad in (
                ('start', a in start and s in start and start[a] > start[s] + slack),
                ('ap', a in ap and s in ap and ap[a] > ap[s] - slack),
                ('title', bool(junior.fullmatch(titles.get(a) or '')) and bool(senior.fullmatch(titles.get(s) or '')))) if bad]
            if why:
                self._detach(e, why, moved)
        if not early:  # 没有 early_coauthors.json 就无从判断，不做第二轮
            return moved
        into = defaultdict(list)
        for e in self.edges.values():
            if e['t'] == 'phd' and not self.nodes[e['s']].get('ext'):
                into[e['s']].append(e)
        for s, es in into.items():
            if len(es) <= max_adv:
                continue
            for e in es:
                a = e['a']
                if e.get('firm') or not e.get('named') or 'dblp' in e['src'] or self.nodes[a].get('ext') or                         (a, s) in self.KEEP_PROF_EDGES or early.get(s, {}).get(a):
                    continue
                self._detach(e, ['crowded'], moved)
        return moved

    def split_homonyms(self, start, min_gap=8):
        """外部人员只按名字合并，同名的两个人会被并成一个节点（如 Heer 的在读学生 Yang Liu 和
        Fei Liu 的博士导师 Yang Liu）。做法：把节点的边按来源标识分组——带同一个 Wikidata QID / 维基百科
        词条的边是同一人，一组；没有标识的边（人工表、CV、主页、dblp 推断）合成一组，维持按名字合并。
        以节点 id 所属的组为准（id 是 QID 时是那一组，按名字建的 x: 节点是无标识那组），其他组若和它
        构成「A 的博士生 X 又是 P 的博士导师」且 P 入行比 A 晚不到 min_gap 年，就是另一个人，挪到新节点
        （有 QID 用 QID，否则 x:<另一端 id>:<名字>）。时间线说得通的组照旧合并，比如国外教授既是
        某教授的学生、又带出几位美国教授。返回 [(原 id, 新 id, 挪走那组的页面)]。"""
        touch = defaultdict(list)
        for e in self.edges.values():
            for nid in (e['a'], e['s']):
                if self.nodes[nid].get('ext'):
                    touch[nid].append(e)

        def clash(g1, g2, nid):  # g1 里有 A→X、g2 里有 X→P（或反过来），且时间上排不下
            for x, y in ((g1, g2), (g2, g1)):
                for e1 in x:
                    for e2 in y:
                        if e1['s'] == nid and e2['a'] == nid and e1['t'] == e2['t'] == 'phd' and \
                                start.get(e1['a']) and start.get(e2['s']) and start[e2['s']] - start[e1['a']] < min_gap:
                            return True
            return False

        splits = []
        for nid, es in touch.items():
            if not any(e['s'] == nid for e in es) or not any(e['a'] == nid for e in es):
                continue  # 只当学生或只当导师：没有可检查的链条
            groups, plain = [], []  # [(标识集合, 边)]、无标识的边
            for e in es:
                ids = e.get('ids')
                if not ids:
                    plain.append(e)
                    continue
                hit = [g for g in groups if g[0] & ids]
                merged = (set(ids).union(*(g[0] for g in hit)), [e] + [x for g in hit for x in g[1]])
                groups = [g for g in groups if not any(g is h for h in hit)] + [merged]
            if plain:
                groups.append((set(), plain))
            anchor = next((g for g in groups if nid in g[0]), None) or (groups[-1] if plain else max(groups, key=lambda g: len(g[1])))
            n = self.nodes[nid]
            for ids, move in groups:
                if move is anchor[1] or not clash(anchor[1], move, nid):
                    continue
                other = lambda e: e['s'] if e['a'] == nid else e['a']
                strong = sorted(ids, key=lambda i: (not i.startswith('wd:'), i))
                new = strong[0] if strong else f"x:{other(move[0])}:{key(n['name'])}"
                nn = self.nodes.setdefault(new, dict(id=new, name=n['name'], ext=True, wd=new if strong else None))
                pages = set().union(*(e.get('pages', set()) for e in move))
                for e in move:
                    o = self.nodes[other(e)]
                    pages.update(x for x in (o.get('homepage'), o.get('cv')) if x)
                    self.move_edge(e, nid, new)
                # 导师主页学生名单（2a）带来的去向 / 主页 / 在读标记，跟着名单所在的那条边走
                listed = lambda g: any(e['s'] == nid or e['s'] == new for e in g if {'homepage', 'sheet'} & set(e['src']))
                if listed(move) and not listed(anchor[1]):
                    for k in ('af', 'h', 'cur'):
                        if k in n:
                            nn.setdefault(k, n.pop(k))
                if n.get('wd') in ids:
                    n['wd'] = None
                if n.get('url') in pages:
                    nn.setdefault('url', n['url'])
                    kept = [e for e in anchor[1] if e.get('url') and e['url'] not in pages]
                    n['url'] = kept[0]['url'] if kept else ''
                splits.append((nid, new, pages))
        return splits


_TEXTS = {}
POSTDOC_W = r'(?:post-?doc\w*|post-?doctoral|research\s+(?:scientist|associate|fellow|staff))'


def postdoc_with(pid, adv_name):
    """教授 pid 自己的主页 / CV 里，博后 / 研究员字样和导师的姓出现在同一句里（100 字以内）。"""
    if pid not in _TEXTS:
        t = ''
        for kind in ('home', 'cv'):
            fn = os.path.join(ROOT, 'data/cache/pages', f'{pid}_{kind}.txt')
            if os.path.exists(fn):
                t += open(fn, encoding='utf-8', errors='replace').read() + '\n'
        _TEXTS[pid] = ''.join(c for c in unicodedata.normalize('NFKD', t) if not unicodedata.combining(c))
    t = _TEXTS[pid]
    toks = fold(display_name(adv_name)).split()
    if not t or len(toks) < 2 or len(toks[-1]) < 3:
        return False
    sur = re.escape(toks[-1])
    return re.search(rf'(?is){POSTDOC_W}[^.\n]{{0,100}}\b{sur}\b|\b{sur}\b[^.\n]{{0,60}}{POSTDOC_W}', t) is not None


MERGE_REASONS = ('same_homepage', 'same_scholar', 'same_name_shared_advisor', 'professor_same_advisor', 'same_linkedin')


def merge_nodes(g, merge_list, alias):
    """把 drop（外部人员）并到 keep 上：边挪过去，keep 缺的 url / af / h 用 drop 的。已不在图里的跳过（可再调用一次）。"""
    n = 0
    for m in merge_list:
        keep, drop = m['keep'], m['drop']
        if m.get('reason') not in MERGE_REASONS:
            continue
        while keep in alias:
            keep = alias[keep]
        if keep == drop or drop not in g.nodes or keep not in g.nodes or not g.nodes[drop].get('ext'):
            continue
        alias[drop] = keep
        n += 1
        for k in ('url', 'af', 'h', 'li', 'g', 'ph_lab'):
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
                for k, v in (e.get('su') or {}).items():
                    old.setdefault('su', {}).setdefault(k, v)
            else:
                e['a'], e['s'] = a2, s2
                g.edges[(a2, s2, t)] = e
        del g.nodes[drop]
    return n


def merge_professors(g, pm, stats):
    """data/enrich/professor_merges.json：professors.csv 里重复的同一位教授。节点 id 用较早的那个（收藏夹按 id 存），
    姓名 / 主页 / 学校等用 data 那一行；另一行的边挪过来。返回 {被去掉的 id: 保留的 id}。"""
    gone = {}
    for m in pm.get('merge', []):
        a, b = m['data'], m['other']
        if a not in g.nodes or b not in g.nodes:
            continue
        keep, drop = sorted((a, b), key=lambda x: int(x[1:]))
        data = g.nodes[a]
        g.nodes[keep] = dict(data, id=keep)
        for (x, y, t), e in list(g.edges.items()):
            if drop in (x, y):
                g.move_edge(e, drop, keep)
        for k3 in [k3 for k3 in g.edges if k3[0] == k3[1]]:
            del g.edges[k3]
        del g.nodes[drop]
        gone[drop] = keep
        g.prof_data[keep] = a  # 按教授 id 存的职称 / 照片 / 招生等，用 data 那一行的
        stats['professor_duplicates_merged'] += 1
    for pid, nm in pm.get('rename', {}).items():
        if pid in g.nodes:
            g.nodes[pid]['name'] = nm
    return gone


def apply_overrides(g, ov, stats):
    """data/enrich/edge_blocklist.json：人工核对的边修正（格式见文件里的 _doc）。外部导师（x:/dblp:/hs:/wd:）的节点 id
    会随合并 / 拆分 / 摘边变化，所以 block 时也按名字匹配：节点还在就用它的名字，否则用 id 里最后一个冒号之后的部分。
    返回 {节点 id: 输出时去掉的字段}。"""
    def ends(a_id, s_id):
        if a_id.startswith(('x:', 'dblp:', 'hs:', 'wd:')):
            nm = key(g.nodes[a_id]['name'] if a_id in g.nodes else a_id.rsplit(':', 1)[1])
            hit = lambda a: a == a_id or (g.nodes[a].get('ext') and key(g.nodes[a]['name']) == nm)
        else:
            hit = lambda a: a == a_id
        return [e for e in g.edges.values() if e['s'] == s_id and hit(e['a'])]
    for a, s, why in ov.get('block', []):
        for e in ends(a, s):
            if 'homepage' in e['src'] and not g.nodes[s].get('ext'):
                # 导师主页名单上确有这个学生，只是不是名单里的这位教授：留作导师名下的外部学生
                new = f"hs:{e['a']}:{key(g.nodes[s]['name'])}"
                g.nodes.setdefault(new, dict(id=new, name=g.nodes[s]['name'], ext=True, **(e.get('listed') or {})))
                g.move_edge(e, s, new)
            else:
                del g.edges[(e['a'], e['s'], e['t'])]
            stats['manual_blocked'] += 1
    for a, s, old, new, why in ov.get('retype', []):
        for e in [e for e in ends(a, s) if e['t'] == old]:
            del g.edges[(e['a'], e['s'], old)]
            prev = g.edges.get((e['a'], e['s'], new))
            if prev:
                prev['src'] += [x for x in e['src'] if x not in prev['src']]
            else:
                e['t'] = new
                g.edges[(e['a'], e['s'], new)] = e
            stats['manual_retyped'] += 1
    for a, s, typ, name, why in ov.get('add', []):
        if s not in g.nodes:
            continue
        g.nodes.setdefault(a, dict(id=a, name=name, ext=True, wd=a if a.startswith('wd:') else None))
        g.add_edge(a, s, typ, 'sheet')
        stats['manual_added'] += 1
    return {nid: fields for nid, fields, why in ov.get('strip', [])}  # 输出节点时去掉的字段


def main():
    profs = load_profs()
    unis = load_unis()
    g = Graph(profs)
    # 0) 还没进 CSRankings 的新入职老师（data/enrich/new_faculty.json，prepare_new_faculty.py 生成、人工核对）：
    #    id 用 "nf:<名字键>"，以后 CSRankings 收录了同名同校的人也保留这个 id（收藏夹按 id 存），见 2c'
    new_fac, nf_extra = load_new_faculty(profs, g)
    prof_ids_csv = {p['id'] for p in profs}
    profs = profs + [g.nodes[i] for i in new_fac]
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

    for nid, r in nf_extra.items():
        for adv in r.get('advisors') or []:
            if not adv.get('name') or key(adv['name']) == key(r['name']):
                continue
            src = 'cv' if re.search(r'(?i)\bcv\b|vita|resume|\.pdf(?:$|\?)', adv.get('url') or '') else 'homepage'
            g.add_edge(g.resolve(adv['name']), nid, adv.get('type') or 'phd', src, adv.get('url') or '')
            stats['new_faculty_advisor_edges'] += 1
    stats['new_faculty'] = len(new_fac)

    # 2) 自动补全的关系（dblp 推断最后处理，见 2b）
    def opt(name):
        f = os.path.join(ROOT, 'data/enrich', name)
        return json.load(open(f, encoding='utf-8')) if os.path.exists(f) else {}
    career_start = opt('career_start.json')  # dblp 入行年份（career_start.py）
    # QID 只是按名字对上教授的：enrich_wikidata.py 记下的匹配方式是 name_desc（只有姓名 + 描述像学者，单位对不上），
    # 或 match_wikidata_employer.py 按姓名 + 任职学校对上的（employer_search）：这些教授一端算按名字认人，见
    # detach_implausible。name_employer（姓名一致且 Wikidata 任职单位就是他的学校）仍算可靠——试过一并当按名字认人，
    # crowded 规则摘掉的多是对的边（Galil→Katz、Raz→Lovett、Cassell→Bickmore）。维基百科条目也是从这个 QID 找到的，一样处理
    wd_method = opt('wikidata_match_method.json')
    name_matched = set(opt('wikidata_employer_match.json')) | {pid for pid, m in wd_method.items() if m == 'name_desc'}
    # 不是靠 Scholar ID 对上的条目里，描述的明显是同名的另一个人（wd_misfit）：教授自己的 Wikidata / 维基百科导师学生不要；
    # 别人条目里指向他的那端改成这个 QID 的外部人员
    wd_adv, sheet_adv = defaultdict(set), defaultdict(set)
    wd_path = os.path.join(ROOT, 'data/enrich/wikidata.jsonl')
    for line in (open(wd_path, encoding='utf-8') if os.path.exists(wd_path) else ()):
        r = json.loads(line)
        if r['rel'] == 'advisor' and r.get('type', 'phd') == 'phd':
            wd_adv[r['prof']].add(ilast(r['name']))
    for e in g.edges.values():  # 这时图里只有人工表的边（第 1 步）
        if e['t'] == 'phd':
            sheet_adv[e['s']].add(ilast(g.nodes[e['a']]['name']))
    wd_misfits = {pid: why for pid, w in opt('wikidata_match_info.json').items()
                  if wd_method.get(pid, 'scholar_id') != 'scholar_id' and pid in g.nodes and
                  (why := wd_misfit(g.nodes[pid], w, career_start.get(pid), wd_adv[pid], sheet_adv[pid]))}
    wd_info = opt('wikidata_match_info.json')
    ov = opt('edge_blocklist.json')
    for pid, q, why in ov.get('wd_reject', []):  # 人工判定（QID 变了就不再适用）
        if wd_info.get(pid, {}).get('q') == q:
            wd_misfits[pid] = 'manual'
    for pid, q, why in ov.get('wd_accept', []):
        if wd_info.get(pid, {}).get('q') == q:
            wd_misfits.pop(pid, None)
    stats['wd_name_matched'] = len(name_matched)
    stats['wd_misfit_profs'] = len(wd_misfits)
    for pid, why in sorted(wd_misfits.items()):
        print('wikidata misfit', pid, g.nodes[pid]['name'], why, file=sys.stderr)
    inferred, listed = [], []
    for f in sorted(glob.glob(os.path.join(ROOT, 'data/enrich/*.jsonl'))):
        recs = [json.loads(line) for line in open(f, encoding='utf-8')]
        if os.path.basename(f) == 'dblp.jsonl':
            inferred = recs
            continue
        if os.path.basename(f) == 'students_homepage.jsonl':
            held = [r for r in recs if r.get('conf') == 'low']
            stats['homepage_student_held_low_conf'] = len(held)
            recs = [r for r in recs if r.get('conf') != 'low']  # 召回那一轮新增、可信度不够的：先不用
            listed = recs + listed  # 导师主页上的学生名单，见 2a
            continue
        if os.path.basename(f) == 'students_lab.jsonl':
            listed = listed + recs  # 实验室网站上的（排在主页名单后面：同一人已在主页名单上时只补主页 / 照片）
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
            if r['source'] in ('wikidata', 'wikipedia') and r['prof'] in wd_misfits:
                stats['wd_misfit_dropped'] += 1
                continue
            ext_id = r.get('ext_id')
            if not ext_id and r['source'] == 'wikipedia' and '/wiki/' in r.get('url', ''):
                ext_id = 'wp:' + r['url'].rsplit('/wiki/', 1)[1]
            if r.get('other_prof') in wd_misfits:  # url 是对面那人的条目；不按名字 resolve，否则又落回这位教授
                ext_id = 'wd:' + r['url'].rsplit('/', 1)[1]
                r = {k: v for k, v in r.items() if k != 'other_prof'}
                g.nodes.setdefault(ext_id, dict(id=ext_id, name=r['name'], ext=True, wd=ext_id))
                g.alias[ext_id] = ext_id
                stats['wd_misfit_to_external'] += 1
            if not r.get('other_prof') and not ext_id and re.search(r'(?i)\s(?:de|del|della|van|von|der|den|da|di|du|le|la|dos|das)$', r['name'].strip()):
                pre = fold(r['name']) + ' '
                hits = {nid for nid, n in g.nodes.items() if fold(n['name']).startswith(pre)}
                if len(hits) == 1:
                    r = dict(r, other_prof=hits.pop())  # 名字被截在姓的前缀上（"Chris De"）
                    stats['particle_names_fixed'] += 1
            other = r.get('other_prof') or g.resolve(r['name'], ext_id)
            if g.nodes[other].get('ext') and not g.nodes[other].get('url'):
                g.nodes[other]['url'] = r.get('url', '')
            hint = None if r.get('other_prof') else ext_id
            # 哪一端只是按名字认成教授的：Wikidata / 维基百科里没有 QID→教授的对应、靠 resolve 按名字找到的那端；
            # 或者 QID 只是按名字对上教授的（name_matched，见上）。本人 CV / 主页写的导师算可靠
            end = 'a' if r['rel'] == 'advisor' else 's'
            named = None
            if r['source'] in ('wikidata', 'wikipedia'):
                if not r.get('other_prof') and not g.nodes[other].get('ext'):
                    named = end
                elif r.get('other_prof') in name_matched:
                    named = end
                elif r['prof'] in name_matched:
                    named = 's' if end == 'a' else 'a'
            if r['rel'] == 'advisor':
                g.add_edge(other, r['prof'], r.get('type', 'phd'), r['source'], r.get('url', ''), hint, named)
            else:
                g.add_edge(r['prof'], other, r.get('type', 'phd'), r['source'], r.get('url', ''), hint, named)
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
    lab_photo_ok = opt('student_photos_lab.json')  # 实验室页上学生照片里 https 下能打开、尺寸够的（fetch_lab_pages.py --photos）
    for r in listed:
        k = key(r['name'])
        other = r.get('other_prof') or mine[r['prof']].get(k)
        lab = r.get('via') == 'lab'
        if not other:
            other = f"hs:{r['prof']}:{k}"
            g.nodes.setdefault(other, dict(id=other, name=r['name'].strip(), ext=True))
            mine[r['prof']][k] = other
        if other == r['prof']:
            continue
        if r.get('af') and g.nodes[other].get('ext') and not g.nodes[other].get('af'):
            g.nodes[other]['af'] = r['af']  # 导师主页上写的去向，如 "Amazon"、"postdoc, Harvard"
        if r.get('h') and g.nodes[other].get('ext') and not (lab and g.nodes[other].get('h')):
            g.nodes[other]['h'] = r['h']
        if lab and g.nodes[other].get('ext'):
            for fld in ('li', 'g'):
                if r.get(fld):
                    g.nodes[other].setdefault(fld, r[fld])
            if r.get('ph') and lab_photo_ok.get(r['ph']):
                g.nodes[other].setdefault('ph_lab', lab_photo_ok[r['ph']])
                stats['lab_student_photos'] += 1
        if r.get('cur') and g.nodes[other].get('ext'):
            g.nodes[other]['cur'] = True  # 导师主页"当前学生"名单里的人
        manual = r.get('source') == 'sheet'  # 人工补充的可靠；导师主页名单上的名字只是按名字（+合著）认成教授
        if r.get('type', 'phd') == 'phd' and not manual and not g.nodes[other].get('ext'):
            firm_adv = [e2['a'] for e2 in g.edges.values() if e2['s'] == other and e2['t'] == 'phd' and e2['a'] != r['prof']
                        and set(e2['src']) & {'sheet', 'cv', 'wikidata', 'wikipedia', 'homepage'}]
            adv_unis = {g.nodes[a2].get('uni') for a2 in firm_adv if not g.nodes[a2].get('ext')}
            if firm_adv and adv_unis and None not in adv_unis and g.nodes[r['prof']].get('uni') not in adv_unis:
                r = dict(r, type='postdoc')  # 例：Heer 名单上的 Leilani Battle（MIT 博士）、Zhicheng Liu（Georgia Tech 博士）
                stats['listed_phd_downgraded_to_postdoc'] += 1
        if lab and (r['prof'], other, r.get('type', 'phd')) not in g.edges:
            stats['lab_new_edges'] += 1
        e = g.add_edge(r['prof'], other, r.get('type', 'phd'), r.get('source', 'homepage'), r.get('url', ''),
                       named=None if manual else 's')
        if e and not g.nodes[other].get('ext'):
            e.setdefault('listed', {}).update({k: (True if k == 'cur' else r[k]) for k in ('af', 'h', 'cur') if r.get(k)})
        stats[('lab' if lab else r.get('source', 'homepage')) + '_student'] += 1

    # 2b) dblp 合作论文推断：与已有导师一致时作为佐证；只有其他来源都没有 PhD 导师时才新增
    has_phd = defaultdict(set)
    for e in g.edges.values():
        if e['t'] == 'phd':
            has_phd[e['s']].add(e['a'])
    # 推断窗口（读博那几年）在学生 dblp 入行 6 年以后、AP 入职之后、或还没结束：多半是博士后导师或同名者，不用
    ap_y = {k: v['y'] for k, v in opt('ap_since.json').items() if v.get('y') and not v.get('incoming')}
    this_year = datetime.date.today().year
    for r in inferred:
        stu = r['prof']
        w = re.search(r'(\d{4})\D(\d{4})', r.get('note', ''))
        if w and ((stu in career_start and int(w.group(1)) >= career_start[stu] + 6) or
                  (stu in ap_y and int(w.group(1)) > ap_y[stu]) or int(w.group(2)) > this_year):
            stats['dblp_late_window_skipped'] += 1
            continue
        if has_phd.get(stu):
            # 教授优先、再按 id 排，结果不随集合遍历顺序变
            match = sorted((a for a in has_phd[stu] if key(g.nodes[a]['name']) == key(r['name'])),
                           key=lambda a: (bool(g.nodes[a].get('ext')), a))
            if match:
                a, op = match[0], r.get('other_prof')
                xs = [m for m in match if m.startswith('x:')]
                if op and op != stu and xs and all(m == op or m.startswith('x:') for m in match) and \
                        all(key(g.nodes[m]['name']) == key(g.nodes[op]['name']) for m in xs):
                    # 名单里有同名教授（或写法不全）时人工表 / 主页的导师只能按名字落到 x: 节点；
                    # dblp 已认出是名单内哪位教授，就把这条边改指向他（他已是导师时并进那条边）
                    for m in xs:
                        g.move_edge(g.edges[(m, stu, 'phd')], m, op)
                        stats['dblp_repointed'] += 1
                    a = op
                e = g.edges[(a, stu, 'phd')]
                if 'dblp' not in e['src']:
                    e['src'].append('dblp')
                stats['dblp_confirms'] += 1
            continue
        # 学生本人主页 / CV 上写着跟这位（或他所在学校）做博后 / 研究员：合著是博后期间的，不是博士导师
        if postdoc_with(stu, r['name']):
            stats['dblp_skipped_postdoc_mention'] += 1
            continue
        other = r.get('other_prof') or g.resolve(r['name'], r.get('ext_id'))
        if not r.get('other_prof') and not g.nodes[other].get('ext'):
            # infer_dblp 只给名单外的 dblp 作者写 ext_id（名单内的写 other_prof），按名字落到同名教授身上必是认错了人
            # （如 "Qing Yang 0001" 不是名单里的 Qing Yang）：改用这个 dblp 作者自己的外部节点
            other = r['ext_id'] if r.get('ext_id') else f"x:{stu}:{key(r['name'])}"
            g.nodes.setdefault(other, dict(id=other, name=r['name'], ext=True, wd=r.get('ext_id')))
            stats['dblp_not_listed_prof'] += 1
        if g.nodes[other].get('ext') and not g.nodes[other].get('url'):
            g.nodes[other]['url'] = r['url']
        g.add_edge(other, stu, 'phd', 'dblp', r['url'], named='a')  # 合著推断的导师，不算可靠来源
        g.edges[(other, stu, 'phd')]['note'] = r.get('note', '')
        stats['dblp_advisor'] += 1

    # 2b') 拆开被名字合并的同名外部人员（时间线说不通，见 Graph.split_homonyms；入行年份来自 career_start.py）
    splits = g.split_homonyms(career_start)
    stats['homonyms_split'] = len(splits)

    # 2c) 合并同一个人的重复节点（find_duplicates.py / 人工查重：同主页 / 同 Scholar / 同名且同导师 / 同领英 / 名单内教授且同导师）
    ov = opt('edge_blocklist.json')
    for a, s_, why in ov.get('pre_block', []):  # 合并前先摘的错边（否则合并会把它带到别人身上）
        for k3 in [k3 for k3 in g.edges if k3[0] == a and k3[1] == s_]:
            del g.edges[k3]
            stats['manual_pre_blocked'] += 1
    alias = {}
    mc_path = os.path.join(ROOT, 'data/enrich/merge_candidates.json')
    merge_list = json.load(open(mc_path, encoding='utf-8')) if os.path.exists(mc_path) else []
    merge_nodes(g, merge_list, alias)
    stats['merged_duplicates'] = len(alias)

    # 2c') professors.csv 里重复的同一位教授
    prof_gone = merge_professors(g, opt('professor_merges.json'), stats)
    for nid in new_fac:
        same = [q for q in g.by_key.get(key(g.nodes[nid]['name']), []) if q in prof_ids_csv and q in g.nodes
                and g.nodes[q]['uni'] == g.nodes[nid]['uni']]
        for q in same:  # 名单上有了这个人：边并到 nf id，主页 / Scholar 用 CSRankings 的，保留新老师来源链接
            for (x, y, t), e in list(g.edges.items()):
                if q in (x, y):
                    g.move_edge(e, q, nid)
            for fld in ('homepage', 'scholar', 'cv', 'domain', 'dblp'):
                if g.nodes[q].get(fld):
                    g.nodes[nid][fld] = g.nodes[q][fld]
            del g.nodes[q]
            prof_gone[q] = nid
            g.prof_data[nid] = q
            stats['new_faculty_now_in_csrankings'] += 1
    for drop, keep in prof_gone.items():
        if drop in career_start and keep not in career_start:
            career_start[keep] = career_start[drop]

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
    # 主页上的职称 / 照片地址（extract_titles.py、fetch_photos.py、fetch_people_photos.py）
    titles, photos, people_photos = opt('titles.json'), opt('photos.json'), opt('people_photos.json')
    home_photos = dict(photos)  # 主页上选的（院系名录的照片被 blocklist 去掉时退回用它）
    # 院系名录（fetch_directories.py）比个人主页可靠，优先；Wikimedia Commons 照片（fetch_wikidata_photos.py）兜底
    # 但名录里兼职 / 访问 / 研究 / 教学类职称（多半是在别的系的挂名）不覆盖主页上的正式职称
    SIDE = re.compile(r'adjunct|visiting|courtesy|affiliat|research|instruction|teaching|lecturer', re.I)
    DIR_STALE = {'p6054'}  # 名录过时：UCSB 的 Zheng Zhang 名录仍写 Assistant Professor
    dir_photo = {}
    for pid, r in opt('directory.json').items():
        if r.get('ti') and pid not in DIR_STALE:
            if titles.get(pid) and SIDE.search(r['ti']) and not SIDE.search(titles[pid]):
                continue
            # 名录和主页职级一样、主页多了 Distinguished（"Distinguished Professor"）：用主页的
            rank = lambda t: re.sub(r'(?i)distinguished\s+|\s+emerit\w*', '', t or '').strip().lower()
            if titles.get(pid) and re.search(r'(?i)distinguished', titles[pid]) and not re.search(r'(?i)distinguished', r['ti']) \
                    and rank(titles[pid]).endswith(rank(r['ti'])):
                continue
            titles[pid] = r['ti']
        if r.get('ph'):
            photos[pid] = r['ph']
            dir_photo[pid] = r['ph']
    link_updates, dead_links, recruiting = opt('link_updates.json'), opt('dead_links.json'), opt('recruiting.json')
    # 人工核对后不采用的招生判断：组里的统一表态、条件句、"可能招零到一个"这类说不准的
    for nid in ('p6487', 'p1552', 'p566', 'p1209'):
        recruiting.pop(nid, None)
    ap_since = opt('ap_since.json')  # AP 入职年份（extract_ap_since.py）：{pid: {y, q, src, incoming}}
    status_fixes, title_over = opt('status_fixes.json'), opt('title_overrides.json')
    for nid, r in nf_extra.items():  # 新入职老师自带的职称 / 照片 / 招生 / 入职年份
        if r.get('title'):
            titles.setdefault(nid, r['title'])
        if r.get('photo') and not PHOTO_PLACEHOLDER.search(r['photo']):
            photos.setdefault(nid, r['photo'])
        if r.get('recruiting'):
            recruiting.setdefault(nid, r['recruiting'])
        ym = re.search(r'(20\d\d)', r.get('start') or '')
        if ym:
            y = int(ym.group(1))
            mo = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
                  'spr': 1, 'sum': 6, 'fal': 9, 'aut': 9, 'win': 1}.get((r.get('start') or '')[:3].lower(), 8)
            ap_since.setdefault(nid, dict(y=y, incoming=(y, mo) > (datetime.date.today().year, datetime.date.today().month),
                                          q=f"Joined {r['uni']} ({r['start']})", src=r.get('source_url') or ''))
    for keep, src in g.prof_data.items():  # 合并掉的重复教授：按 id 存的数据改用 data 那一行的（没有时用另一行的）
        other = next(d for d, k in prof_gone.items() if k == keep) if keep == src else src
        for dct in (titles, photos, home_photos, recruiting, ap_since, link_updates, dead_links, career_start):
            v = dct.get(src) if src in dct else dct.get(other)
            if v is not None:
                dct[keep] = v
    for pid, fx in status_fixes.items():  # 已调走 / 离开学术界：和不在名单上的教授一样，作为外部人员显示现在的去向
        if isinstance(fx, dict) and pid in g.nodes and re.search(r'(?i)^moved|left\s+academia', fx.get('note', '')):
            g.nodes[pid]['status'] = 'former'
            m_ = re.match(r'(?i)moved:\s*(?:now\s+)?(.*)', fx['note'])
            g.nodes[pid]['now_at'] = (m_.group(1) if m_ else 'Left academia ' + re.sub(r'(?i)^left academia\s*', '', fx['note'])).split(';')[0].strip()[:120]
            stats['status_fixed'] += 1
    for pid in status_fixes.get('drop_as', []):
        ap_since.pop(pid, None)
    # 写着 Assistant Professor、入职已 >= 8 年的：抽查 5 位都已升职或离开；院系名录也写 Assistant Professor 才保留
    this_y = datetime.date.today().year
    dir_all = opt('directory.json')
    for pid in list(ap_since):
        y = ap_since[pid].get('y')
        if y and not ap_since[pid].get('incoming') and y <= this_y - 8 and \
                not re.search(r'(?i)^assistant\s+professor', (dir_all.get(pid) or {}).get('ti') or ''):
            del ap_since[pid]
            if re.search(r'(?i)\bassistant\s+professor', titles.get(pid) or ''):
                titles.pop(pid, None)
            stats['stale_ap_dropped'] += 1
    # 3b) 只靠按名字认人的教授→教授边，时间线 / 职称说不通的不挂到教授身上（要用上面的职称，所以放在这里）
    detached = g.detach_implausible(career_start, titles, ap_since, opt('early_coauthors.json'))
    stats['implausible_prof_edges_detached'] = len(detached)
    for e, new in detached:
        print('detached', e['a'], g.nodes[e['a']]['name'], '->', e['s'], g.nodes[e['s']]['name'],
              '+'.join(e['src']), ','.join(e['why']), '=>', new, file=sys.stderr)
    strip = apply_overrides(g, ov, stats)  # 人工核对的边修正
    # 有些重复节点（hs:<导师>:<名字>）是 3b 摘边之后才生成的：再合并一次
    stats['merged_duplicates_after_detach'] = merge_nodes(g, merge_list, alias)
    trusted = {e['s'] for e in g.edges.values() if e['t'] == 'phd' and set(e['src']) & {'wikidata', 'wikipedia'}}
    for e in g.edges.values():  # 让人工表导师"存疑"的那条 Wikidata / 维基百科边被摘掉了，就不再存疑
        if e.get('disputed') and e['s'] not in trusted:
            e['disputed'] = False
            stats['sheet_phd_disputed'] -= 1
    used = {e['a'] for e in g.edges.values()} | {e['s'] for e in g.edges.values()}
    bad_urls = opt('photo_https_bad.json')  # https 下打不开的照片地址（verify_photos.py），换下一个来源
    for src in (photos, people_photos):
        for nid in [k for k, u in src.items() if u in bad_urls]:
            del src[nid]
    # 院系个人页 / About 页 / sitemap 里补到的照片（fetch_photos_extra.py，逐张人工核对过），排在 Wikimedia 之前兜底
    blocked = set(opt('photo_blocklist.json'))
    for nid, u in opt('photos_extra.json').items():
        if u not in bad_urls and nid not in blocked:
            photos.setdefault(nid, u)
    for nid, u in opt('wikidata_photos.json').items():
        if u not in bad_urls and nid not in wd_misfits:  # 同名另一个人的照片不要
            photos.setdefault(nid, u)
            people_photos.setdefault(nid, u)
    # 人工核对出的选错 / 占位的照片（photo_blocklist.json），在所有来源合并之后再去掉：
    # 理由里注明 [source: directory.json] / [source: photos.json] 的只去掉那个来源的那张，退回用下一个来源
    for nid, why in opt('photo_blocklist.json').items():
        src_m = re.search(r'\[source:\s*([\w.]+)\]', str(why))
        if src_m and src_m.group(1) == 'directory.json' and dir_photo.get(nid) and photos.get(nid) == dir_photo[nid]:
            alt = home_photos.get(nid)
            if alt and alt != dir_photo[nid] and alt not in bad_urls and not PHOTO_PLACEHOLDER.search(alt):
                photos[nid] = alt
            else:
                photos.pop(nid, None)
            stats['photo_blocked_directory'] += 1
            continue
        if src_m and src_m.group(1) == 'photos.json' and photos.get(nid) and photos.get(nid) != home_photos.get(nid):
            continue  # 被挡的是主页那张，现在用的是别的来源
        if photos.pop(nid, None):
            stats['photo_blocked'] += 1
        people_photos.pop(nid, None)
    for src in (photos, people_photos):  # 占位图 / 签名失效的链接
        for nid in [k for k, u in src.items() if PHOTO_PLACEHOLDER.search(u)]:
            del src[nid]
            stats['photo_placeholder_dropped'] += 1
    prof_photo_urls = {u for k, u in photos.items() if k in g.nodes and not g.nodes[k].get('ext')}
    for old, new, pages in splits:  # 拆分前在原节点上找到的个人主页若来自挪走那一侧的页面，跟着挪到新节点
        if links.get(old, {}).get('via') in pages and new not in links:
            links[new] = links.pop(old)
            if not people.get(old, {}).get('h'):  # 照片和失效标记是按这个主页找的
                for src in (people_photos, dead_links):
                    if old in src:
                        src.setdefault(new, src.pop(old))
    for nid, n in g.nodes.items():  # 新补进名单的教授（faculty_manual.csv）：名单外时找到的照片
        if not n.get('ext') and n.get('status') == 'manual' and not photos.get(nid):
            suf = ':' + key(n['name'])
            drops = [d_ for d_, k_ in alias.items() if k_ == nid]
            cands = [people_photos.get('x:' + key(n['name']))] + [people_photos.get(d_) for d_ in drops] +                 [u for k_, u in people_photos.items() if k_.endswith(suf)]
            u = next((u for u in cands if u and u not in bad_urls), None)
            if u:
                photos[nid] = u
    aliases_of = defaultdict(list)
    for drop, keep in alias.items():
        while keep in alias:
            keep = alias[keep]
        aliases_of[keep].append(drop)
        if keep in g.nodes and not g.nodes[keep].get('ext') and not photos.get(keep) and people_photos.get(drop) \
                and people_photos[drop] not in bad_urls:
            photos[keep] = people_photos[drop]  # 名单外时找到的照片（如 x:jure leskovec → 补进名单的教授）
    out_nodes = []
    for nid, n in g.nodes.items():
        if n.get('ext'):
            if nid not in used:
                continue
            d = dict(id=nid, n=n['name'], x=1, **({'af': n['af']} if n.get('af') else {}), **({'h': n['h']} if n.get('h') else {}))
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
            elif n.get('ph_lab') and not PHOTO_PLACEHOLDER.search(n['ph_lab']):
                d['ph'] = n['ph_lab']  # 导师实验室网站 People 页上这个学生卡片里的照片
            if d.get('ph') in prof_photo_urls:
                del d['ph']  # 学生页上抓到的是导师的照片
                stats['student_photo_is_professor_photo'] += 1
            for fld in ('li', 'g'):
                if n.get(fld) and not d.get(fld):
                    d[fld] = n[fld]
            if n.get('cur') and not d.get('af'):
                d['st'] = 1  # 在读学生（导师主页的当前学生名单）
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
            if recruiting.get(nid):  # 主页上的招生声明（extract_recruiting.py）：[yes/no, 年份, 抓取年月, 原句, 出处]
                r = recruiting[nid]
                d['rc'] = [r['s'], r.get('y'), r.get('at', ''), r.get('q', ''), r.get('src', ''),
                           {k: r[k] for k in ('phd', 'old', 'inst', 'yf') if k in r}]
            if ap_since.get(nid) and ap_since[nid].get('y'):  # [入职年份, 是否尚未入职, 原句, 出处]
                r = ap_since[nid]
                d['as'] = [r['y'], 1 if r.get('incoming') else 0, r.get('q', ''), r.get('src', '')]
            if n.get('status') == 'former':
                # 已不在 CSRankings 的美国学校名单上（调去国外、退休、转业界……）：网站只收在美国高校任教的人，
                # 不再当作教授列出；作为别人的导师 / 学生仍留在树上，显示现在的去向或原学校
                if nid not in used:
                    continue
                stats['former_as_outside'] += 1
                x = dict(id=nid, n=n['name'], x=1, af=n.get('now_at') or f"Formerly {n['uni']}")
                x.update({k: d[k] for k in ('h', 'g', 'ph') if d.get(k)})
                if x.get('h') and nid in dead_links:
                    del x['h']  # 已失效的旧主页（check_links.py）
                    stats['former_dead_homepage_dropped'] += 1
                out_nodes.append(x)
                continue
            if n.get('nf'):
                d['nf'] = n['nf']  # 新入职、还没进 CSRankings：前端标 "New faculty · not yet in CSRankings" 并链到来源
            d['d'] = [a for a, _ in parse_domains(n['domain'])]
            d['a'] = sorted({b for _, b in parse_domains(n['domain'])})
            out_nodes.append(d)
    for d in out_nodes:  # edge_blocklist.json 的 strip：属于同名另一个人的主页 / 单位 / 照片
        for k in strip.get(d['id'], ()):
            d.pop(k, None)
    # 人工核对的职称（title_overrides.json）最后覆盖；status_fixes.json：已调走 / 离开学术界的按外部人员显示去向
    for d in out_nodes:
        if d['id'] in title_over and not d.get('x'):
            d['ti'] = title_over[d['id']]
        fx = status_fixes.get(d['id'])
        if isinstance(fx, dict) and re.search(r'(?i)hijack|spam', fx.get('note', '')):
            d.pop('h', None)  # 主页被劫持
    # 名单外的人：去向写着名单里的美国大学（且不是博后 / 学生 / 研究员 / 工程师 / 曾任）时标 "ua":1（前端原来在 load() 里逐个比对）
    uni_lc = [u.lower() for u in uni_list if len(u) > 6]
    uni_alt = set()
    for u in uni_list:
        v = u.lower().replace('univ. of', 'university of').replace('univ.', 'university')
        uni_alt |= {v, v.replace(' - ', ', '), v.replace(' - ', ' '), v.replace(' - ', ' at ')}
        m_uc = re.match(r'university of california - (.+)', v)
        if m_uc:
            uni_alt |= {'uc ' + m_uc.group(1), 'uc-' + m_uc.group(1)}
    uni_alt = {v for v in uni_alt if len(v) > 6}
    for d in out_nodes:
        if d.get('x') and d.get('af') and not re.search(r'(?i)postdoc|post-doc|student|ph\.?d|fellow|intern|alum|formerly|scientist|engineer', d['af']):
            a = d['af'].lower()
            if any(u in a for u in uni_lc):
                d['ua'] = 1
        if d.get('x') and not d.get('ua') and d.get('af') and re.search(r'(?i)\bprofessor\b', d['af']) \
                and not re.search(r'(?i)formerly|emerit|visiting|adjunct|research\s+professor', d['af']):
            a = d['af'].lower()
            if any(u in a for u in uni_alt):
                d['ua'] = 1  # "Assistant Professor, UC Berkeley"：在名单内的美国学校任教，但不在 CSRankings 名单上
    for d in out_nodes:  # 抓来的链接只放行 http(s)，防止 javascript: 之类进到网页
        for k in ('h', 'li', 'g', 'c', 'ph', 'w'):
            if k in d and not re.match(r'https?://', str(d[k]), re.I):
                del d[k]
    # 边: [导师, 学生, 类型, 来源, 出处链接, 是否存疑(, 推断依据)]
    def edge_url(e):  # 各来源的出处链接不同时用 " | " 连接，顺序同来源
        su = e.get('su') or {}
        urls = [su.get(x, '') for x in e['src']]
        if len({u for u in urls if u}) > 1:
            return ' | '.join(urls)
        return e.get('url', '') or next((u for u in urls if u), '')
    edges = [[e['a'], e['s'], e['t'], '+'.join(e['src']), edge_url(e), int(bool(e.get('disputed')))]
             + ([e['note']] if e.get('note') else []) for e in g.edges.values()]

    prof_ids = {p['id'] for p in profs}
    with_adv = {e['s'] for e in g.edges.values() if e['s'] in prof_ids and not e.get('disputed')}
    with_stu = {e['a'] for e in g.edges.values() if e['a'] in prof_ids}
    prof_prof = sum(1 for e in g.edges.values() if e['a'] in prof_ids and e['s'] in prof_ids)
    profs = [p for p in profs if p['id'] not in prof_gone]
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
