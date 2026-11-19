"""从缓存的主页 / CV 文本里抽取 PhD / postdoc 导师，并用 Wikidata / Wikipedia 已知导师评估准确率。

用法: python scripts/extract_advisors.py [--eval]
输出: data/enrich/homepage.jsonl（仅写入还没有可信导师的教授）
"""
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from names import key, fold  # noqa: E402

CACHE = os.path.join(ROOT, 'data/cache/pages')
U = r"A-Z\u00C0-\u00DE"
L = r"a-z\u00DF-\u024F'’"
WORD = rf"(?:Mc|Mac|De|Di|La|Le|O['’])?[{U}][{L}]+(?:-[{U}]?[{L}]+)*"
TOKEN = rf"(?:{WORD}|[{U}]\.(?:\s?-?[{U}]\.)?)"
PARTICLE = r"(?:de|van|von|der|den|da|del|di|la|le|du|dos|das)"
NAME = rf"(?:(?:Prof(?:essor)?\.?|Dr\.?)\s+)?{TOKEN}(?:\s+(?:{PARTICLE}\s+)*{TOKEN}){{1,3}}"
NAMES = rf"({NAME}(?:\s*(?:,|\band\b|&|/)\s*(?:(?:Prof(?:essor)?\.?|Dr\.?)\s+)?{NAME}){{0,2}})"
SEP = (r"[ \t]*[:\-–—,]?[ \t]*(?:(?:was|is|were|are)\s+)?(?:(?:[A-Z][a-z]+\s+)?Distinguished\s+)?"
       r"(?:(?i:professors?|prof\.|dr\.)\s+)?")

# (正则, 关键词本身是否已说明是 PhD/postdoc)
PATTERNS = [
    (rf"(?i:(?<![a-z])(?:ph\.?\s?d\.?|doctoral|thesis|dissertation)\s+(?:co-?)?(?:advisor|adviser|supervisor)s?){SEP}{NAMES}", 'phd'),
    (rf"(?i:(?:post-?doc(?:toral)?)\s+(?:co-?)?(?:advisor|adviser|supervisor|mentor|host)s?){SEP}{NAMES}", 'postdoc'),
    (rf"(?i:\b(?:advisor|adviser|supervisor)s?)[ \t]*[:–—][ \t]*(?:(?i:professors?|prof\.|dr\.)\s+)?{NAMES}", None),
    (rf"(?i:\b(?:advised|supervised|mentored)\s+by){SEP}{NAMES}", None),
    (rf"(?i:under\s+the\s+(?:joint\s+)?(?:supervision|guidance|mentorship|advisement)\s+of){SEP}{NAMES}", None),
    (rf"(?i:\bmy\s+(?:ph\.?\s?d\.?\s+|doctoral\s+)(?:advisor|adviser|supervisor)s?)\s*,?{SEP}{NAMES}", 'phd'),
]
RX = [(re.compile(p), t) for p, t in PATTERNS]

BAD_TOK = {'university', 'department', 'dept', 'lab', 'labs', 'laboratory', 'school', 'institute', 'science',
           'sciences', 'engineering', 'computer', 'computing', 'center', 'centre', 'group', 'research', 'the', 'of',
           'college', 'ph', 'phd', 'thesis', 'dissertation', 'committee', 'student', 'students', 'program',
           'faculty', 'award', 'fellowship', 'national', 'foundation', 'professor', 'assistant', 'associate',
           'chair', 'electrical', 'information', 'systems', 'data', 'machine', 'learning', 'with', 'in', 'at',
           'my', 'our', 'his', 'her', 'their', 'microsoft', 'google', 'ibm', 'amazon', 'intel', 'facebook', 'meta',
           'nvidia', 'technology', 'mathematics', 'physics', 'statistics', 'biology', 'chemistry', 'artificial',
           'intelligence', 'vision', 'robotics', 'security', 'network', 'networks', 'software', 'theory', 'new',
           'york', 'california', 'texas', 'and', 'visiting', 'positions', 'position', 'employment', 'israel'}
# 名字后面常被一起抓进来的词：截掉
TRAIL = {'received', 'master', 'masters', 'other', 'integrated', 'best', 'bachelor', 'bachelors', 'area', 'thesis',
         'research', 'brown', 'stanford', 'berkeley', 'mit', 'cmu', 'continue', 'reading', 'b', 'm', 'ms', 'bs',
         'ma', 'ba', 'msc', 'bsc', 'phd', 'ph', 'dr', 'prof', 'professor', 'previously', 'before', 'after',
         'currently', 'since', 'from', 'then', 'he', 'she', 'i', 'we', 'they', 'my', 'his', 'her', 'during',
         'while', 'in', 'at', 'on', 'for', 'the', 'a', 'an', 'research', 'interests', 'interest', 'awards',
         'honors', 'education', 'experience', 'employment', 'publications', 'teaching', 'department', 'school',
         'university', 'college', 'institute', 'dissertation', 'title', 'committee', 'members', 'member',
         'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec', 'january',
         'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november',
         'december', 'spring', 'fall', 'summer', 'winter', 'bio', 'cv', 'email', 'office', 'phone', 'contact',
         'his', 'postdoc', 'postdoctoral', 'graduate', 'undergraduate', 'visiting', 'senior', 'junior',
         'next', 'position', 'current', 'now', 'study', 'graduated', 'co-advisor', 'coadvisor', 'shanghai',
         'beijing', 'china', 'usa', 'india', 'korea', 'japan', 'canada', 'israel', 'germany', 'france', 'uk',
         'professional', 'distinguished', 'academic', 'positions', 'industry', 'note'}
STUDENT_CTX = re.compile(r'(?i)\bstudents\b|\badvisees\b|\bmentees\b|\balumni\b|graduated\s+(ph\.?d|students)|'
                         r'student\s+(advising|supervision)|\bpostdocs\b')
DEGREE_RX = re.compile(r'(?i)(?P<phd>\bph\.?\s?d\b|\bdoctor\s+of\s+philosophy\b|\bdoctor(?:al|ate)\b|\bd\.?phil\b|\bdr\.?\s?rer\b)|'
                       r'(?P<post>\bpost-?doc(?:toral)?\b)|'
                       r'(?P<other>\bm\.?\s?s\.?c?\b(?=[\s,.(])|\bm\.?\s?eng\b|\bmaster|\bb\.?\s?s\.?c?\b(?=[\s,.(])|'
                       r'\bb\.?\s?a\.?\b(?=[\s,.(])|\ba\.\s?b\.|\bdipl\b|\bs\.\s?b\.|\bs\.\s?m\.|\bbachelor|\bundergrad|\bdiplom|\blicenci|\bm\.?\s?math|'
                       r'\bm\.\s?e\.|\bm\.\s?a\.|\bm\.\s?phil|\bintern(?:ship)?\b|\bsummer\b|\bcorporation\b|\binc\.?\b|\bresearch\s+(?:associate|engineer|scientist|staff)|'
                       r'\bsoftware\s+engineer|\bminor(?:ed)?\b)')


def trim(tokens, known):
    """去掉名字末尾的多余词；优先取能对上已知学者的最长前缀。"""
    toks = [t for t in tokens if t]
    while toks and toks[-1].lower().strip('.,') in TRAIL:
        toks.pop()
    for n in range(min(len(toks), 4), 1, -1):
        cand = ' '.join(toks[:n])
        if key(cand) in known:
            return cand
    if len(toks) < 2:
        return None
    # 不认识的名字：默认取「名 姓」；中间是缩写（如 Jack H. Lutz）时取三个词
    if len(toks) >= 3 and re.fullmatch(r"[A-Z]\.(?:-?[A-Z]\.)?", toks[1]):
        return ' '.join(toks[:3])
    return ' '.join(toks[:2])


def clean_name(n, known):
    n = re.sub(r'^(?:Prof(?:essor)?\.?|Dr\.?)\s+', '', n.strip())
    n = re.sub(r'\s+', ' ', n).strip(' ,.;')
    n = trim(n.split(' '), known)
    if not n:
        return None
    toks = n.split()
    if not 2 <= len(toks) <= 4 or any(t.lower().strip('.') in BAD_TOK for t in toks):
        return None
    if len(toks[-1].strip('.')) <= 1 or re.search(r'\w\.\w', toks[-1]):  # 姓不能是缩写或 "B.A" 这类
        return None
    return n


def split_names(s, known):
    s = re.split(r'\s+\S+:', s)[0]
    parts = re.split(r'\s*(?:,|\band\b|&|/)\s*', s)
    return [x for x in (clean_name(p, known) for p in parts) if x]


def degree_context(before):
    """离关键词最近的学位 / 经历词：phd、post 或 other。"""
    last = None
    for m in DEGREE_RX.finditer(before):
        last = m
    if not last:
        return None
    return 'phd' if last.group('phd') else 'post' if last.group('post') else 'other'


NARRATOR_RX = re.compile(r"\b([A-Z][a-z]+)\s+(?:received|was|is|has|got|earned|obtained|completed|defended)\b")
PRONOUNS = {'He', 'She', 'They', 'I', 'We', 'Professor', 'Prof', 'Dr', 'Congrats', 'The', 'This', 'It'}


def extract(text, self_name, known):
    import unicodedata
    text = unicodedata.normalize('NFKC', text)  # 连字 ﬀ/ﬁ、全角字符等还原
    found = []
    self_k = key(self_name)
    self_toks = set(re.findall(r'[A-Za-z]+', self_name))
    for rx, kw_type in RX:
        for m in rx.finditer(text):
            before = text[max(0, m.start() - 300):m.start()]
            if STUDENT_CTX.search(before):
                continue  # 学生名单里的「advisor / co-advised by」说的是学生的导师
            if re.search(r'(?i)committee', text[max(0, m.start() - 600):m.start()]):
                continue  # 答辩委员会名单里的「(advisor: X)」是别人的导师
            if re.search(r'\([^()]{0,70}$', before):
                continue  # 括号里的「(…, Advisor: X)」多是名单条目
            if re.search(r'(?i)\bmyself\b|\bme\b', text[m.end():m.end() + 80]):
                continue  # 「co-supervised by X and myself」：说的是自己的学生
            subj = [w for w in NARRATOR_RX.findall(before[-160:]) if w not in PRONOUNS]
            if subj and subj[-1] not in self_toks:
                continue  # 在讲别人：「Alex received his PhD … advised by」
            near = before[-140:]
            if (re.search(r'(?i)\bstudent\b|graduated|next position|\bprimary\b|mentee', near)
                    and not re.search(r'(?i)\b(i|he|she)\s+(was|am|is|were)\b|\bmy\b', near)):
                continue
            ctx = degree_context(before[-220:])
            after = re.match(r'\s*(?:[,•|;]\s*)?(.{0,25})', text[m.end():m.end() + 40], re.S).group(1)
            am = DEGREE_RX.match(after)
            if am:
                ctx = 'phd' if am.group('phd') else 'post' if am.group('post') else 'other'
            if kw_type == 'postdoc':
                typ = 'postdoc'
            elif kw_type == 'phd':
                if ctx == 'post':
                    typ = 'postdoc'
                else:
                    typ = 'phd'
            else:
                if ctx == 'phd':
                    typ = 'phd'
                elif ctx == 'post':
                    typ = 'postdoc'
                else:
                    continue  # 没有 PhD/postdoc 上下文（或是硕士、实习、公司），不收
            if kw_type == 'phd' and ctx == 'other' and re.search(r'(?i)thesis', m.group(0)[:20]):
                continue  # 「Thesis advisor」紧跟在硕士学位后面
            for n in split_names(m.group(1), known):
                if key(n) == self_k:
                    continue
                snippet = text[max(0, m.start() - 80):m.end() + 20].replace('\n', ' ')
                found.append((n, typ, snippet))
    seen, out = set(), []
    for n, t, s in found:
        if (key(n), t) not in seen:
            seen.add((key(n), t))
            out.append((n, t, s))
    return out


def read_page(path):
    if not os.path.exists(path):
        return '', ''
    txt = open(path, encoding='utf-8', errors='replace').read()
    first, _, body = txt.partition('\n')
    return first.replace('#URL ', '').strip(), body


def load_graph():
    g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8'))
    names = {n['id']: n['n'] for n in g['nodes']}
    known = {key(n) for n in names.values()}
    trusted = defaultdict(set)
    for a, s, typ, src, *_ in g["edges"]:
        if typ == 'phd' and set(src.split('+')) & {'wikidata', 'wikipedia'}:
            trusted[s].add(key(names[a]))
            trusted[s].add('last:' + fold(names[a]).split()[-1])
    return known, trusted


def main():
    evaluate = '--eval' in sys.argv
    profs = load_profs()
    known, trusted = load_graph()
    recs, ev, bad = [], defaultdict(int), []
    for p in profs:
        hits = []
        for kind in ('home', 'cv'):
            url, body = read_page(os.path.join(CACHE, f'{p["id"]}_{kind}.txt'))
            if body:
                page = extract(body, p['name'], known)
                if len(page) > 4:
                    continue  # 一页抽出太多「导师」，多半是学生/委员会名单
                for n, t, snip in page:
                    hits.append((n, t, snip, 'cv' if kind == 'cv' else 'homepage', url))
        if not hits:
            continue
        ev['profs_with_hits'] += 1
        tr = trusted.get(p['id'])
        if tr:
            for n, t, snip, src, url in hits:
                if t != 'phd':
                    continue
                ok = key(n) in tr or ('last:' + fold(n).split()[-1]) in tr
                ev['eval_total'] += 1
                ev['eval_ok'] += ok
                if not ok:
                    bad.append((p['name'], n, snip[:150]))
        else:
            for n, t, snip, src, url in hits:
                recs.append(dict(prof=p['id'], rel='advisor', name=n, type=t, source=src, url=url, snippet=snip))
    if ev['eval_total']:
        ev['precision'] = round(ev['eval_ok'] / ev['eval_total'], 3)
    print(dict(ev))
    print('new records', len(recs), 'professors', len({r['prof'] for r in recs}),
          'phd', sum(r['type'] == 'phd' for r in recs), 'postdoc', sum(r['type'] == 'postdoc' for r in recs))
    if evaluate:
        for b in bad[:60]:
            print('  x', b)
        return
    with open(os.path.join(ROOT, 'data/enrich/homepage.jsonl'), 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps({k: v for k, v in r.items() if k != 'snippet'}, ensure_ascii=False) + '\n')
    with open(os.path.join(ROOT, 'data/cache/homepage_snippets.jsonl'), 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
