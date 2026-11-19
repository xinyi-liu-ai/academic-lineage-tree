"""从教授自己的主页 / CV 的学生栏目（Students、PhD Students、Alumni、Former Students、Postdocs…）提取学生名单。

- 栏目标题必须单独成行；栏目到下一个标题（Teaching、Publications…）或 60 行为止
- 每条取开头的人名（2–4 个首字母大写的词），括号或后面的文字当作去向（如 "Rui Dong (Amazon)"）
- 本科生、硕士、访问学生、实习生栏目跳过；Postdocs 栏目记为博士后
检验（--eval）：抽出的名字有多少和这位教授在 dblp 上合写过论文、与 Wikidata 已知学生的重合情况
输出: data/enrich/students_homepage.jsonl
用法: python scripts/extract_students.py [--eval]
"""
import json
import os
import pickle
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from names import display_name, fold, key  # noqa: E402

PAGES = os.path.join(ROOT, 'data/cache/pages')
PEOPLE = os.path.join(ROOT, 'data/cache/people_pages')
INDEX = os.path.join(ROOT, 'data/cache/dblp/index.pkl')

Q = r'(?:current|present|my|former|past|graduated|previous|alumni|completed)'
DEG = r'(?:ph\.?\s?d\.?|doctoral|graduate|grad)'
STU_HEAD = re.compile(rf'^(?:{Q}\s+)?(?:{DEG}\s+)?(?:students?|advisees|alumni|alumnae)(?:\s+(?:and|&)\s+(?:alumni|postdocs?|former students))?'
                      rf'(?:\s+(?:advised|supervised|graduated|mentored))?(?:\s*\(.*\))?\s*:?$|^{DEG}\s+(?:students?\s+)?alumni\s*:?$|'
                      rf'^(?:{Q}\s+)?{DEG}\s+(?:thesis|dissertation)\s+(?:students?|supervision|advising)\s*:?$|^(?:doctoral|ph\.?\s?d\.?)\s+(?:advising|supervision)\s*:?$', re.I)
POST_HEAD = re.compile(rf'^(?:{Q}\s+)?(?:post-?docs?|postdoctoral\s+(?:researchers?|fellows?|scholars?|associates?))(?:\s+(?:hosted|supervised|mentored))?\s*:?$', re.I)
SKIP_HEAD = re.compile(r'^(?:current\s+|former\s+|past\s+)?(?:undergrad|undergraduate|master|m\.?s\.?|meng|visiting|visitors?|interns?|high school|'
                       r'summer|reu|rotation|bachelor|honors?|ms/ma)\b.{0,40}$', re.I)
END_HEAD = re.compile(r'^(?:selected\s+|recent\s+)?(?:teaching|publications?|papers|research|courses?|news|contact|service|projects?|awards?|honors|talks|'
                      r'software|bio|biography|funding|sponsors|links|misc|education|employment|experience|positions?|grants|'
                      r'professional|activities|outreach|collaborators|recent news|announcements|openings|prospective students|'
                      r'for prospective|teaching assistants|committees?|editorial|invited|patents|books|preprints|datasets|code)\b.{0,40}$', re.I)
TOK = r"(?:[A-Z][a-zA-Z'’\-]+|[A-Z]\.(?:-?[A-Z]\.)?)"
PART = r'(?:de|del|della|van|von|der|den|da|di|la|le|du|dos|das|bin|al|el|ibn)'
NAME = re.compile(rf"^({TOK}(?:\s+(?:{TOK}|{PART}))*?\s+[A-Z][a-zA-Z'’\-]+)(?=$|[\s,;:(\[–—\-|.])")
STOP = {w.lower() for w in '''University Institute College Lab Laboratory Research Google Microsoft Amazon Apple Meta Facebook IBM Intel
    Professor Assistant Associate PhD Ph.D. Student Students Postdoc Postdocs Now Current Former Fall Spring Summer Winter Department
    Computer Science Sciences Engineering Center School The Co Advised Thesis Dissertation Graduated Joint With Inc Corp Group Team
    Members Alumni January February March April May June July August September October November December Visiting Scholar Fellow
    Faculty Lecturer Scientist Engineer Senior Staff Principal Director Manager Startup Company CEO CTO Founder Postdoctoral
    Co-advised Coadvised Master Masters Undergraduate Undergrad Intern National State Technology Data Machine Learning Systems
    AI Artificial Intelligence NLP Vision Robotics Security Networks Lab's Labs Home Page Homepage Website CV Email Office Phone
    New York Los Angeles San Francisco Boston Seattle Chicago Hong Kong China USA US Canada Inria Max Planck MIT CMU UCLA
    Stanford Berkeley Harvard Princeton Yale Cornell Columbia Toronto Oxford Cambridge Tsinghua Peking Zhejiang
    Award Best Paper Prize Recipient Medal Our We I My His Her Their Department's Program Programs Project Projects'''.split()}
YEARISH = re.compile(r'\b(19|20)\d{2}\b')
END_ANY = re.compile(r'committee|examiner|reader|jury|courses taught|teaching|publications', re.I)  # 短行里出现即视为栏目结束
STUDENTISH = re.compile(r'ph\.?\s?d|student|postdoc|dissertation|thesis|graduated|defended|alum', re.I)
NOT_PHD = re.compile(r"[’']\d{2}\b|\b(m\.?s\.?|m\.?eng|masters?|b\.?s\.?|b\.?a\.?|undergrad\w*|bachelor|high school|intern|visiting)\b", re.I)
COMMON = set()  # 常用英文词（由 build_common() 从所有页面文字统计）
COMMON_LO = set()  # 较常见的词：名字里每个词都属于这一类就不要


def build_common(texts):
    """在页面里以小写形式大量出现的词是普通单词，不会是人名的组成部分。"""
    low, cap = Counter(), Counter()
    for t in texts:
        for w in re.findall(r"\b[A-Za-z][a-z]{2,}\b", t):
            (low if w[0].islower() else cap)[w.lower()] += 1
    COMMON.update(w for w, c in low.items() if c >= 100 and c >= 2 * cap[w])
    COMMON_LO.update(w for w, c in low.items() if c >= 25 and c >= cap[w])
    COMMON.update({'carnegie', 'mellon', 'campus', 'quick', 'submit', 'steering', 'advisory', 'graduate', 'outcomes', 'degrees', 'rights', 'reserved', 'sign', 'ons', 'frequent', 'searches', 'newsletter', 'recruiting', 'partners', 'gear', 'family', 'early', 'about', 'all', 'past', 'outstanding', 'distinguished', 'our', 'home', 'contact', 'technologies', 'chase', 'jpmorgan', 'addison', 'plain', 'privacy', 'policy', 'employment', 'researcher', 'scholars', 'certificate', 'system', 'network', 'designing', 'youtube', 'delft', 'turing'})


def clean_note(s):
    s = s.strip(' \t,;:.-–—()[]')
    s = re.sub(r'^(?:now|currently|then)\s+(?:at|with|an?|the)?\s*', '', s, flags=re.I)
    s = re.sub(r'^(?:ph\.?\s?d\.?|graduated|grad\.?)\s*(?:\d{4})?\s*[,;:–—-]?\s*', '', s, flags=re.I)
    s = re.sub(r'\s+', ' ', s).strip(' ,;:.-–—()')
    return s[:70]


def entries(lines):
    """栏目里的每一项 -> (名字, 备注)。"""
    out = []
    for line in lines:
        line = line.strip(' \t•·*-–—▪►')
        if not line:
            continue
        flat = re.sub(r'\([^)]*\)', '', line)
        parts = [line]
        if line.count(',') >= 1 and '(' not in line:
            cand = [x.strip(' .') for x in re.split(r',\s*(?:and\s+)?|\s+and\s+', flat) if x.strip(' .')]
            if len(cand) >= 2 and all(NAME.match(c) and len(c.split()) <= 4 for c in cand):
                parts = cand
        for part in parts:
            part = re.sub(r'^and\s+', '', part.strip())
            m = NAME.match(part)
            if not m:
                continue
            name = m.group(1)
            toks = name.split()
            if not 2 <= len(toks) <= 4 or any(t.lower().strip('.') in STOP or t.lower() in STOP for t in toks):
                continue
            if (any(t.lower() in COMMON or t.endswith(("'s", '’s')) or (t.isupper() and len(t) > 1 and '.' not in t) for t in toks)
                    or all(t.lower() in COMMON_LO for t in toks)):  # 普通单词、所有格、全大写缩写（VCU、TU）都不是人名
                continue
            note = clean_note(part[m.end():])
            if NOT_PHD.search(note):
                continue  # 硕士 / 本科 / 实习
            out.append((name, note))
    return out


def sections(text):
    lines = [l.strip() for l in text.split('\n')]
    lines = [l for l in lines if l]
    out = []
    i = 0
    while i < len(lines):
        h = lines[i]
        kind = 'phd' if len(h) <= 60 and STU_HEAD.match(h) else 'postdoc' if len(h) <= 60 and POST_HEAD.match(h) else None
        if not kind:
            i += 1
            continue
        body = []
        j = i + 1
        while j < len(lines) and j - i <= 60:
            l = lines[j]
            if len(l) <= 60 and (STU_HEAD.match(l) or POST_HEAD.match(l) or SKIP_HEAD.match(l) or END_HEAD.match(l)
                                 or (len(l) <= 40 and END_ANY.search(l))):
                break
            body.append(l)
            j += 1
        out.append((kind, h, body))
        i = j
    return out


def load_pages(pid):
    pp = os.path.join(PEOPLE, pid + '.json')
    return json.load(open(pp, encoding='utf-8')).get('pages', []) if os.path.exists(pp) else []


def personal(url, homepage, shared):
    """只认教授自己的页面：主页本身、主页目录下的子页，或个人域名；被多位教授共用的页面（系里名录）不算。"""
    if url.rstrip('/') == homepage.rstrip('/'):
        return True
    if url in shared:
        return False
    hu, uu = urlparse(homepage), urlparse(url)
    if hu.netloc != uu.netloc:
        return False
    base = hu.path.rsplit('/', 1)[0] if '.' in hu.path.rsplit('/', 1)[-1] else hu.path.rstrip('/')
    return (bool(base) and uu.path.startswith(base + '/')) or SHARED_HOSTS.get(uu.netloc, 0) <= 1


SHARED_HOSTS = Counter()
PERSONAL_PATH = re.compile(r'/(~|home/|homes/|u/|users?/|web/)', re.I)  # 个人目录式网址


def load_texts(pid, homepage='', shared=frozenset()):
    texts = []
    for pg in load_pages(pid):
        if pg.get('text') and personal(pg['url'], homepage or pg['url'], shared):
            texts.append((pg['url'], pg['text']))
    for kind in ('home', 'cv'):
        f = os.path.join(PAGES, f'{pid}_{kind}.txt')
        if os.path.exists(f):
            first, _, body = open(f, encoding='utf-8', errors='replace').read().partition('\n')
            if body.strip() and not any(body[:2000] == t[:2000] for _, t in texts):
                texts.append((first.replace('#URL ', '').strip(), body))
    return texts


def main():
    profs = load_profs()
    me_key = {p['id']: key(p['name']) for p in profs}
    by_key = defaultdict(list)
    for p in profs:
        by_key[key(p['name'])].append(p['id'])
    idx = pickle.load(open(INDEX, 'rb'))['pubs']
    url_count = Counter()
    for p in profs:
        SHARED_HOSTS[urlparse(p['homepage']).netloc] += 1
        for pg in load_pages(p['id'])[1:]:
            url_count[pg['url'].rstrip('/')] += 1
    shared = {u for u, c in url_count.items() if c > 1}
    shared |= {u + '/' for u in shared}
    all_texts = {p['id']: load_texts(p['id'], p['homepage'], shared) for p in profs}
    build_common(t for ts in all_texts.values() for _, t in ts)
    recs = []
    for p in profs:
        seen = set()
        coauthors = {key(display_name(a)) for pub in idx.get(p['dblp'], []) for a in pub[2]}
        for url, text in all_texts[p['id']]:
            is_pdf = url.lower().split('?')[0].endswith('.pdf') or text.lstrip().startswith('%PDF')
            # 系里统一模板的个人页（同一主机上有很多教授、路径不带 ~）导航菜单里常有 Students 栏目，按严格规则处理
            strict = is_pdf or (SHARED_HOSTS[urlparse(url).netloc] > 2
                                and not PERSONAL_PATH.search(urlparse(url).path))
            for kind, head, body in sections(text):
                ents = entries(body)
                if len(ents) > 80:
                    continue  # 太长，多半不是学生名单
                alumni = bool(re.search(r'alum|former|past|graduated|previous|completed', head, re.I))
                for name, note in ents:
                    k = key(name)
                    if k == me_key[p['id']] or (k, kind) in seen:
                        continue
                    # PDF 版 CV 排版乱、常混进论文题目：要么和这位教授合写过论文，要么备注里写着 PhD/student 等
                    if strict and k not in coauthors and not STUDENTISH.search(note):
                        continue
                    seen.add((k, kind))
                    r = dict(prof=p['id'], rel='student', name=name, type=kind, source='homepage', url=url)
                    fac = [q for q in by_key.get(k, []) if q != p['id']]
                    if len(fac) == 1 and k in coauthors:  # 学生后来也当了教授：合写过论文才认定是同一人
                        r['other_prof'] = fac[0]
                    if note and (alumni or kind == 'postdoc') and not YEARISH.fullmatch(note):
                        r['af'] = note
                    recs.append(r)
    print('records', len(recs), 'professors', len({r['prof'] for r in recs}),
          Counter(r['type'] for r in recs))

    if '--eval' in sys.argv:
        dblp = {p['id']: p['dblp'] for p in profs}
        co = {}
        for pid in {r['prof'] for r in recs}:
            co[pid] = {key(display_name(a)) for pub in idx.get(dblp[pid], []) for a in pub[2]}
        hit = sum(1 for r in recs if key(r['name']) in co[r['prof']])
        print(f'coauthored with the professor in dblp: {hit}/{len(recs)} = {hit / len(recs):.2f}')
        g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8'))
        nodes = {n['id']: n for n in g['nodes']}
        known = defaultdict(set)
        for a, s, t, src, *_ in g['edges']:
            if t == 'phd' and set(src.split('+')) & {'wikidata', 'wikipedia'}:
                known[a].add(key(nodes[s]['n']))
        both = [r for r in recs if known.get(r['prof'])]
        ov = sum(1 for r in both if key(r['name']) in known[r['prof']])
        print(f'professors with known students: extracted {len(both)}, of which already known {ov}')
        bad = [r for r in recs if key(r['name']) not in co[r['prof']]]
        import random
        random.Random(4).shuffle(bad)
        for r in bad[:40]:
            print('  ?', r['name'], '|', r.get('af', ''), '|', r['url'][:60])
        return
    with open(os.path.join(ROOT, 'data/enrich/students_homepage.jsonl'), 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
