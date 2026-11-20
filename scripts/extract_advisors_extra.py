"""给图里还没有可信 PhD 导师的在职教授，从主页、CV、Bio / About 页面里高精度抽取本人的 PhD / postdoc 导师。

和 extract_advisors.py 的区别：
- 输入多了 fetch_advisor_pages.py 新抓的 CV / Bio / 个人主页（data/cache/advisor_pages/），以及院系页缓存
- 只认两种写法：
  A. 简历「学位条目 + 标签」：Ph.D. …（Advisor(s) / Supervisor / Thesis advisor / Committee chair: X）
     标签归属于它前面最近的学位 / 职位条目；落在学生、委员会、指导名单这类小节里的一律不要
  B. 自述句子：I received my Ph.D. … advised by / under the supervision of / working with X；
     I was a postdoc … hosted by X（记为 postdoc）
- 名字沿用 extract_advisors.py 的姓名正则和清洗规则
输出: data/enrich/homepage_extra.jsonl（与 homepage.jsonl 同格式），
      data/cache/homepage_extra_snippets.jsonl（带原文片段，供人工核对）
用法: python scripts/extract_advisors_extra.py [--pid p123] [--dry]
"""
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from names import key, fold  # noqa: E402
from extract_advisors import DEGREE_RX, BAD_TOK, TRAIL, load_graph  # noqa: E402
from fetch_advisor_pages import targets  # noqa: E402

PAGES = os.path.join(ROOT, 'data/cache/pages')
PEOPLE = os.path.join(ROOT, 'data/cache/people_pages')
ADV = os.path.join(ROOT, 'data/cache/advisor_pages')

PRE = r"(?:(?:Prof(?:essor)?s?\.?|Drs?\.?)\s+)?"
# 姓名正则：沿用 extract_advisors.py 的写法，另外允许 ChengXiang 这种中间大写的词
_U = "A-Z\u00C0-\u00DE"
_L = "a-z\u00DF-\u024F'’"
_WORD = rf"(?:Mc|Mac|De|Di|La|Le|O['’])?[{_U}][{_L}]+(?:[{_U}][{_L}]+)?(?:-[{_U}]?[{_L}]+)*"
_TOKEN = rf"(?:{_WORD}|[{_U}]\.(?:\s?-?[{_U}]\.)?)"
_PARTICLE = r"(?:de|van|von|der|den|da|del|di|la|le|du|dos|das)"
_NAME = rf"(?:(?:Prof(?:essor)?\.?|Dr\.?)\s+)?{_TOKEN}(?:\s+(?:{_PARTICLE}\s+)*{_TOKEN}){{1,3}}"
NAMES = rf"({_NAME}(?:\s*(?:,|\band\b|&|/)\s*(?:(?:Prof(?:essor)?\.?|Dr\.?)\s+)?{_NAME}){{0,2}})"
SUPERVISION = (r"(?:supervision|supervison|guidance|guidence|direction|advisement|advisorship|advisory|advising|advise|"
               r"advice|mentorship|mentoring|tutelage|supervisorship)")
# A. 标签式
LABEL = re.compile(
    r"(?i:\b(?:(?:ph\.?\s?d\.?|doctoral|thesis|dissertation|research)\s+)?(?:co-?)?(?:advisors?|advisers?|supervisors?|"
    r"promoters?|directors?|mentors?)\b(?:\s*\(s\))?|\b(?:committee\s+)?chairs?\b|\bco-?chairs?\b|"
    r"\b(?:co-?)?(?:advised|supervised|directed)\s+by\b|\bunder\s+(?:the\s+)?(?:joint\s+)?" + SUPERVISION + r"\s+of\b)"
    r"[ \t]*[:–—-]?[ \t]*(?:(?i:(?:was|is|were|are)\s+))?" + PRE + NAMES)
# 标签后必须紧跟名字：「Advisor: X」「(advisor X)」「Advisors Prof. X, Prof. Y」
# B. 自述句
SELF = r"(?:\bI\b|\b[Hh]e\b|\b[Ss]he\b|\b[Tt]hey\b|\bmy\b|\bhis\b|\bher\b)"
PHD = r"(?:ph\.?\s?d\.?|doctorate|doctoral\s+degree|d\.?\s?phil\.?|doctor\s+of\s+philosophy|doctoral\s+studies)"
GOT = (r"(?:received|earned|obtained|completed|got|did|finished|defended|holds?|has|have|was\s+awarded|graduated\s+with|"
       r"pursued|conducted|is\s+a\s+graduate\s+of)")
LINK = (r"(?:(?:co-?)?advised\s+by|(?:co-?)?supervised\s+by|mentored\s+by|"
        r"under\s+(?:the\s+)?(?:joint\s+|co-?)?" + SUPERVISION + r"\s+of|"
        r"under\s+(?=(?:Prof|Professor|Dr)\b)|"
        r"(?:where|in\s+which)\s+(?:I|he|she|they)\s+(?:was|were)\s+(?:co-?)?(?:advised|supervised)\s+by|"
        r"(?:where|in\s+which)\s+(?:I|he|she|they)\s+(?:worked|studied)\s+(?:with|under)|"
        r"working\s+(?:with|under)|worked\s+(?:with|under)|studying\s+(?:with|under)|"
        r"with\s+(?:(?:my\s+)?(?:ph\.?\s?d\.?\s+)?(?:advisors?|advisers?|supervisors?)\s+)?(?=(?:Prof|Professor|Dr)\.?\s)|"
        r"(?:and|whose)\s+(?:my|his|her|their)\s+(?:ph\.?\s?d\.?\s+|doctoral\s+)?(?:advisors?|advisers?|supervisors?)\s+(?:was|were|is)|"
        r"(?:advisors?|advisers?|supervisors?)\s+(?:was|were)|"
        r"in\s+the\s+(?:group|lab|laboratory)\s+of)")
PROSE_PHD = re.compile(
    rf"(?:{SELF}|\b[A-Z][a-z]+|\band)\s+(?:(?:also|then|later|subsequently)\s+)?{GOT}\s+(?:(?:my|his|her|their|a|the|an)\s+)?"
    rf"(?:[\w.,'’()-]+\s+){{0,3}}?(?i:{PHD})"
    rf"(?P<mid>[^.;!?]{{0,220}}?)(?:,\s*)?(?P<link>{LINK})\s*,?\s*" + PRE + NAMES)
PROSE_STUDENT = re.compile(
    rf"{SELF}\s+(?:was|were)\s+(?:a\s+)?(?i:{PHD})\s+(?:student|candidate|researcher)"
    rf"(?P<mid>[^.;!?]{{0,200}}?)(?:,\s*)?(?P<link>{LINK})\s*,?\s*" + PRE + NAMES)
PROSE_MY = re.compile(
    rf"(?i:\bmy\s+(?:ph\.?\s?d\.?|doctoral|thesis|dissertation)\s+(?:co-?)?(?:advisors?|advisers?|supervisors?)"
    r"\s*(?:,\s*)?(?:(?:was|is|were|are)\s+)?)" + PRE + NAMES)
# 紧跟在「I received my PhD …」后面的一句：「I was advised by X」「My adviser was X」「My dissertation was supervised by X」
PROSE_NEXT = re.compile(
    r"(?:\bI\s+was\s+(?:(?:very\s+)?(?:fortunate|lucky|privileged)\s+to\s+be\s+)?(?:co-?)?(?:advised|supervised)\s+by|\b(?:My|my)\s+(?:(?:ph\.?\s?d\.?|doctoral|thesis)\s+)?"
    r"(?:advisors?|advisers?|supervisors?)\s+(?:was|were)|\b(?:My|my)\s+(?:dissertation|thesis)\s+was\s+"
    r"(?:supervised|advised|directed)\s+by)\s*,?\s*" + PRE + NAMES)
PREV_PHD = re.compile(rf"(?:{SELF}|\b[A-Z][a-z]+)\s+(?:{GOT}\s+(?:(?:my|his|her|their|a|the|an)\s+)?(?:[\w.,'’()-]+\s+){{0,5}}?"
                      rf"(?i:{PHD})|(?:was|were)\s+a\s+(?i:{PHD})\s+(?:student|candidate))")
POSTDOC = r"(?:post-?doc(?:toral)?(?:\s+(?:researcher|fellow|scholar|associate|scientist|research\s+(?:associate|fellow|scientist)))?)"
PROSE_POST = re.compile(
    rf"{SELF}\s+(?:was|were|worked\s+as|served\s+as|spent\s+[\w\s]{{1,20}}?as|then\s+(?:was|became))\s+(?:an?\s+)?"
    rf"(?:[A-Z][\w&-]*\s+){{0,6}}(?i:{POSTDOC})"
    rf"(?P<mid>[^.;!?]{{0,160}}?)(?:,\s*)?(?P<link>(?:hosted|advised|supervised|mentored)\s+by|working\s+with|worked\s+with|"
    rf"(?:where|in\s+which)\s+(?:I|he|she|they)\s+worked\s+with|"
    rf"with|under(?:\s+the\s+(?:supervision|guidance|mentorship|direction)\s+of)?|in\s+the\s+(?:group|lab)\s+of)\s*,?\s*"
    + PRE + NAMES)

# 落在这些小节里的「Advisor: X」说的是学生 / 被评审人的导师
STUDENT_HEAD = re.compile(
    r'(?i)^\W*(?:(?:current|former|past|graduated|doctoral|ph\.?\s?d\.?|graduate|undergraduate|master\'?s?|post-?doc(?:toral)?|'
    r'research|student|thesis|dissertation|my|lab|group|selected)\s+){0,3}'
    r'(?:students?|advisees?|mentees?|supervision|advising|mentoring|mentorship|alumni|members?|people|team|'
    r'(?:thesis|dissertation|doctoral|ph\.?\s?d\.?|exam(?:ination)?|qualifying|qual|defense|oral)?\s*committees?(?:\s+(?:service|membership|member))?|'
    r'committee\s+(?:work|service|membership)|(?:ph\.?\s?d\.?|doctoral|graduate)\s+(?:theses|dissertations)\s*(?:supervised|advised)?|'
    r'(?:theses|dissertations)\s+(?:supervised|advised)|researchers?\s+supervised|postdocs?|scholars?|'
    r'advised|supervised|graduated|group\s+members|external\s+examiner|examiner)\b[\w\s,&()/-]{0,40}$')
GOOD_HEAD = re.compile(r'(?i)^\W*(?:education|educational\s+background|degrees?|academic\s+(?:background|degrees|history|training)|'
                       r'bio(?:graphy)?|short\s+bio|about(?:\s+me)?|background|training|(?:academic\s+|professional\s+)?'
                       r'(?:employment|appointments|positions|experience)(?:\s+history)?)\W*$')
STUDENT_LIST = re.compile(r'(?i)\b(?:co-?advised|jointly\s+advised|co-?supervised)\s+with\b|\bmyself\b|'
                          r'\bstudents?\s+of\b|\bmy\s+(?:former\s+)?(?:ph\.?\s?d\.?\s+)?students?\b')


ACCENT_RX = re.compile(r'\s?[' + ''.join(map(chr, (0xB4, 0x2CA, 0x2CB, 0x300, 0x301, 0xA8, 0x308, 0x2C7, 0x30C, 0x2DC,
                                                    0x303, 0x2C6, 0x60))) + r'](?=[A-Za-z])')


def normalize(t):
    t = unicodedata.normalize('NFKC', t)
    t = re.sub(r'(\w)-\n(\w)', r'\1\2', t)  # PDF 断行连字符
    t = ACCENT_RX.sub('', t)  # PDF 里单独的重音符：「Jovanovi ́c」
    t = re.sub(r'[\u00a0\u2009\u202f]', ' ', t)
    t = re.sub(r'[“”"]\s*[A-Z][a-z]+\s*[“”"]\s+', '', t)  # Alex “Sandy” Pentland
    t = re.sub(r'\b([A-Z][a-z]+)\s+\(([A-Z][a-z]+)\)\s+(?=[A-Z])', r'\1 ', t)  # Zhenhui (Jessie) Li
    t = re.sub(r'[ \t\r\f\v]+', ' ', t)
    return t


def headings(t):
    """[(位置, 'student'|'good'|'other')]：短行里认出来的小节标题；导航栏里连成一串的不算。"""
    out = []
    lines = t.split('\n')
    pos = 0
    kinds = []
    for ln in lines:
        s = ln.strip()
        k = None
        if 0 < len(s) <= 70 and len(s.split()) <= 7 and not re.search(r'\d{4}|[.:;]\s*\S', s):
            if STUDENT_HEAD.match(s):
                k = 'student'
            elif GOOD_HEAD.match(s):
                k = 'good'
            elif re.match(r'(?i)^\W*(?:publications?|teaching|awards?|honou?rs|grants?|funding|service|talks?|'
                          r'research|patents?|software|news|courses?|contact|professional\s+activities|'
                          r'selected\s+publications|invited\s+talks|presentations)\b[\w\s&,/-]{0,30}$', s):
                k = 'other'
        kinds.append((pos, k))
        pos += len(ln) + 1
    for i, (p, k) in enumerate(kinds):
        if not k:
            continue
        nxt = next((kk for _, kk in kinds[i + 1:i + 3] if kk), None)
        prv = next((kk for _, kk in reversed(kinds[max(0, i - 2):i]) if kk), None)
        if nxt or prv:  # 导航栏：好几个标题挨着
            continue
        out.append((p, k))
    return out


def section_at(heads, pos):
    k = None
    for p, kk in heads:
        if p > pos:
            break
        k = kk
    return k


def nearest_degree(t, pos, back=260):
    """标签前面最近的学位 / 经历词。"""
    before = t[max(0, pos - back):pos]
    last = None
    for m in DEGREE_RX.finditer(before):
        if not m.group(0).lower().startswith('minor'):  # 「Ph.D. …, minor in X」
            last = m
    if not last:
        return None, None
    kind = 'phd' if last.group('phd') else 'post' if last.group('post') else 'other'
    return kind, before[last.start():]


INST = re.compile(r"(?i)\b(?:universit\w*|institut\w*|college|tech|polytechnic|laborator\w*|labs?|school|center|centre|"
                  r"state|research|inc|corp\w*|google|microsoft|ibm|amazon|meta|nvidia|intel|department|dept|mit|cmu|"
                  r"ucla|usc|nyu|eth|epfl|kaist|stanford|berkeley|princeton|harvard|cornell|yale|columbia)\b")


PLACE = set('Carnegie Mellon Penn Pennsylvania Georgia Virginia Ohio Texas Purdue Rice Duke Brown Johns Hopkins Notre Dame '
            'Northwestern Northeastern Stony Brook Arizona Michigan Indiana Iowa Florida Washington Oregon Utah Illinois '
            'California Southern Northern Western Eastern Central New York Boston Rutgers Tufts Emory Vanderbilt Cornell '
            'Massachusetts Maryland Minnesota Wisconsin Colorado Kansas Missouri Kentucky Tennessee Alabama Louisiana '
            'Carolina Dakota Nebraska Oklahoma Arkansas Mississippi Nevada Idaho Montana Wyoming Delaware Connecticut '
            'Vermont Maine Hampshire Jersey Rhode Island Alaska Hawaii Tsinghua Peking Zhejiang Fudan Nanjing Tokyo Seoul '
            'National Technical Federal Royal Imperial King Queen Simon Fraser McGill Toronto Waterloo Alberta British '
            'Columbia Hong Kong Singapore Delft Aarhus Weizmann Technion Hebrew Tel Aviv Indian Max Planck Allen Toyota '
            'Bell Xerox Adobe Yahoo Apple Bosch Siemens Huawei Alibaba Baidu Tencent Samsung Sandia Los Alamos Argonne '
            'Oak Ridge Lawrence Livermore Berkeley Stanford Harvard Princeton Yale MIT Caltech Georgetown Drexel Temple '
            'Clemson Auburn Baylor Lehigh Syracuse Rochester Dartmouth Brandeis Wesleyan Swarthmore Riverside Irvine Davis '
            'Diego Santa Barbara Cruz Merced Angeles Francisco Chapel Hill Urbana Champaign Amherst Austin Dallas Houston '
            'Arlington Antonio Paso Tech'.split())


PARTICLES = {'de', 'van', 'von', 'der', 'den', 'da', 'del', 'di', 'la', 'le', 'du', 'dos', 'das', 'De', 'La', 'Van',
             'Von', 'Di', 'Da', 'Del', 'Le', 'Du'}
INITIAL = re.compile(r"[A-Z]\.(?:-?[A-Z]\.)?")


def clean_part(part, known, boundary_ok):
    """一段「名字」→ 规范姓名或 None。boundary_ok：名字后面紧跟标点 / 小写词（不是下一个大写词），可以放心多取一个词。"""
    part = re.sub(r'^(?:(?:Prof\w*\.?|Drs?\.?)\s+)+', '', part.strip())
    part = re.sub(r"['’]s\b.*$", '', part)  # Dawson Engler's group
    toks = part.split()
    had_inst = False
    for i, t in enumerate(toks):  # 「Minh N. Do University of Illinois」截掉单位
        if INST.fullmatch(t.strip('.,')) or re.search(r'[a-z](?:Universit|Institut|College)', t):
            toks, boundary_ok, had_inst = toks[:i], False, True
            break
    # 末尾的学位缩写（M.A.）、常见尾词；截过单位时再去掉地名（「Robert Collins Penn State」）
    while len(toks) > 2 and (toks[-1].lower().strip('.,') in TRAIL or re.fullmatch(r'(?:[A-Z]\.){2,}', toks[-1])
                             or had_inst and toks[-1] in PLACE):
        toks.pop()
        boundary_ok = False
    if len(toks) < 2 or had_inst and (toks[-1] in PLACE or all(t in PLACE for t in toks)):
        return None  # 「Carnegie Mellon University」「Virginia Tech」整段是单位
    for n in range(min(len(toks), 4), 1, -1):
        if key(' '.join(toks[:n])) in known:
            toks = toks[:n]
            break
    else:
        if len(toks) >= 3 and (INITIAL.fullmatch(toks[0]) or INITIAL.fullmatch(toks[1])):
            toks = toks[:3]  # X. Sharon Hu / Jack H. Lutz
        elif len(toks) >= 3 and toks[1] in PARTICLES:
            j = 1
            while j < len(toks) and toks[j] in PARTICLES:
                j += 1
            toks = toks[:j + 1]  # Fernando De la Torre
        elif len(toks) == 3 and boundary_ok:
            pass  # Clyde Lee Giles from …
        else:
            toks = toks[:2]
    if any(t.lower().strip('.') in BAD_TOK for t in toks):
        return None
    if len(toks[-1].strip('.')) <= 1 or re.search(r'\w\.\w', toks[-1]) or INITIAL.fullmatch(toks[-1]):
        return None
    return ' '.join(toks)


def names_from(m, known, self_k, flat=None):
    s = m.group(m.lastindex)
    cut = re.search(r'\s+\S+:', s)
    s = s[:cut.start()] if cut else s
    tail = flat[m.end():m.end() + 12] if flat is not None and not cut else ''
    end_ok = bool(re.match(r"\s*(?:[.,;:()\[\]]|[a-z]|$)", tail)) if flat is not None and not cut else False
    parts = re.split(r'\s*(?:,|\band\b|&|/)\s*', s)
    out = []
    for i, part in enumerate(parts):
        n = clean_part(part, known, boundary_ok=(i < len(parts) - 1) or end_ok)
        if n and key(n) != self_k:
            out.append(n)
    return out


def mentions_self(text, self_name):
    f = ' ' + fold(text) + ' '
    toks = [t for t in fold(self_name).split() if len(t) > 1]
    return bool(toks) and (' ' + toks[-1] + ' ' in f or ' ' + toks[0] + ' ' in f and len(toks[0]) > 3)


def sentence_start(t, pos):
    i = max(t.rfind('. ', 0, pos), t.rfind('\n\n', 0, pos), t.rfind('; ', 0, pos))
    return i + 1 if i >= 0 else 0


def subject_ok(t, m, self_toks):
    """自述句的主语得是本人：I / he / she / 本人名字。"""
    head = t[sentence_start(t, m.start()):m.start() + 40]
    first = re.match(r"\s*(?:\W*\s*)?(?:(?:Prof(?:essor)?\.?|Dr\.?)\s+)?([A-Z][\w'’-]*)", head)
    subj = re.match(r"\s*([A-Za-z][\w'’-]*)", t[m.start():m.start() + 30])
    w = subj.group(1) if subj else ''
    if w in {'I', 'He', 'he', 'She', 'she', 'They', 'they', 'my', 'his', 'her', 'My', 'His', 'Her'}:
        return True
    if w == 'and':  # 「He entered … and later earned his Ph.D.」：看句首主语
        return bool(first and (first.group(1) in {'I', 'He', 'She', 'They'} or first.group(1) in self_toks))
    return w in self_toks or bool(first and first.group(1) in self_toks and w[:1].islower())


def extract_doc(raw, self_name, known, kind):
    t = normalize(raw)
    heads = headings(t)
    flat = t.replace('\n', ' ')
    self_k = key(self_name)
    self_toks = set(re.findall(r"[A-Za-z][\w'’-]+", self_name))
    found = []

    # A. 标签式
    label_hits = []
    for m in LABEL.finditer(flat):
        lab = m.group(0)[:m.start(m.lastindex) - m.start()].lower()
        if 'chair' in lab and not re.search(r'committee|dissertation|thesis|chaired', lab + flat[max(0, m.start() - 60):m.start()].lower()):
            continue  # 单独的「Chair: X」多是会议 / 系里职务
        if re.search(r'(?i)\b(?:research|director|mentor)', lab) and not re.search(r'(?i)ph\.?\s?d|doctoral|thesis|dissertation', lab):
            if not re.search(r'(?i)post-?doc', flat[max(0, m.start() - 200):m.start()]):
                continue  # 「Research advisor / Director / Mentor」只在 postdoc 条目里认
        sec = section_at(heads, m.start())
        if sec == 'student':
            continue
        byform = bool(re.search(r'(?:advised|supervised|directed)\s+by|under\b', lab))
        if byform:
            # 「(advised by X)」「under the supervision of X」这类句式标签：前面得是本人（I / my / he / she / 本人名字），
            # 不能是名单里的别人（「Jane Doe (co-advised by X)」「Noam … under the supervision of X」）
            near = flat[max(0, m.start() - 200):m.start()]
            if re.search(r'co-?(?:advised|supervised)', lab) and not re.search(r'\bI\b|\bmy\b', near):
                _dk, _seg = nearest_degree(flat, m.start(), back=120)
                if _dk != 'phd' or sec == 'student':
                    continue  # 简历学位条目里的「PhD, ECE  Co-advised by X and Y」可以；名单里的不行
            if re.search(r'(?i)\bnext\s*:|\bjoining\b|\bnow\s+at\b|\bcurrently\s+at\b|\bphd\s+forum\b|\bpresents?\b|'
                         r'\bwill\s+be\s+starting\b|\bcongratulat|\bwelcome\b|\bjoins\b', near[-150:]):
                continue  # 新闻 / 学生去向：「X will be starting as a postdoc … under the supervision of Y」
        before = flat[max(0, m.start() - 300):m.start()]
        if STUDENT_LIST.search(before[-200:]) or STUDENT_LIST.search(flat[m.end():m.end() + 60]):
            continue
        if re.search(r'(?i)\bcommittee\b', flat[max(0, m.start() - 400):m.start()]) and 'committee' not in lab:
            continue  # 答辩委员会名单里的「(advisor: X)」
        if re.search(r'(?i)\bstudents?\b|\badvisees?\b|\bmentees?\b|\balumni\b|\bgraduates\b|graduated\s+ph|\bpostdocs\b|'
                     r'different\s+ph|\badvising\b|\bmentoring\b|\bsupervising\b', before[-250:]):
            continue  # 「Advising: Postdoctoral Advisor X」是自己带的人
        dk, seg = nearest_degree(flat, m.start())
        after = flat[m.end():m.end() + 30]
        am = re.match(r'\s*[,;)]?\s*(?:ph\.?\s?d\.?\b)', after, re.I)
        if lab.strip().startswith(('ph', 'doctoral', 'dissertation')) or re.match(r'(?i)thesis', lab) and dk == 'phd':
            typ = 'post' if dk == 'post' else 'phd'
            if re.match(r'(?i)thesis', lab) and dk != 'phd':
                continue
        elif am:
            typ = 'phd'
        elif dk in ('phd', 'post'):
            typ = dk
        else:
            continue
        if byform and seg is not None and re.search(
                r'[.;]\s+[^.;]{0,60}?\b(?:I|[Hh]e|[Ss]he)\s+(?:graduated|received|obtained|earned|completed|did|am|joined|'
                r'spent|worked|moved)\b', seg):
            continue  # 学位词之后又开了一句讲别的经历（「… postdoc …. Before that, I graduated from X, under …」）
        if seg is not None and re.search(r'(?i)research\s+assistant|graduate\s+(?:research\s+)?assistant|teaching\s+assistant|'
                                         r'\bintern(?:ship)?\b|\bvisiting\b|\bvisit\b', seg):
            continue  # 学位词之后又开了个 RA / 实习 / 访问条目，标签属于后者
        if re.search(r'(?i)discontinued|transferred|withdrew|left\s+the\s+program', flat[m.end():m.end() + 80]):
            continue
        if re.search(r'(?i)during\s+(?:my|his|her|their)\s+visit|while\s+visiting|as\s+a\s+visiting', flat[m.end():m.end() + 60]):
            continue  # 访问期间的导师
        if seg is not None and re.search(r'\(\s*$', flat[max(0, m.start() - len(seg) - 3):m.start() - len(seg)]):
            continue  # 「Jane Doe (PhD, graduated 2013) – advisor: X」：括号里的学位是名单条目
        if dk == 'phd' and seg is not None:
            # 学位词和标签之间又出现了别人名字 + 学位（学生名单：「Jane Doe, Ph.D. 2019, Advisor: X」）
            pre = flat[max(0, m.start() - 260):m.start()][-len(seg) - 30:][:40]
            if re.search(r'\b[A-Z][a-z]+\s+[A-Z][a-z]+\s*,?\s*\(?\s*(?i:ph\.?\s?d|m\.?s\.?)\b', pre) and \
                    not re.search(r'(?i)\b(?:my|his|her|their)\s+ph', pre):
                if sec != 'good':
                    continue
        # 这份文本得是本人的：简历开头或标签前 1500 字里出现本人名字（院系名单页上链到的可能是别人的 CV）
        if not (mentions_self(flat[:2500], self_name) or mentions_self(flat[max(0, m.start() - 1500):m.start()], self_name)):
            continue
        for n in names_from(m, known, self_k, flat):
            label_hits.append((n, 'phd' if typ == 'phd' else 'postdoc', m.start(), flat[max(0, m.start() - 160):m.end() + 30]))
    # 一页里标签式命中太多：多半是名单（委员会 / 学生），只信最前面的一个 PhD 和一个 postdoc
    if len({h[0] for h in label_hits}) > 4:
        firsts = {}
        for h in label_hits:
            firsts.setdefault(h[1], h)
        label_hits = list(firsts.values())
    found += [(n, typ, snip, 'label') for n, typ, _, snip in label_hits]

    # B. 自述句
    for rx, typ in ((PROSE_PHD, 'phd'), (PROSE_STUDENT, 'phd'), (PROSE_MY, 'phd'), (PROSE_NEXT, 'phd'),
                    (PROSE_POST, 'postdoc')):
        for m in rx.finditer(flat):
            mid = m.groupdict().get('mid') or ''
            link = (m.groupdict().get('link') or '').lower()
            if rx not in (PROSE_MY, PROSE_NEXT) and not subject_ok(flat, m, self_toks):
                continue
            if rx is PROSE_NEXT:  # 「I received my PhD from X. I was advised by Y.」：前一句得是本人读 PhD
                end = sentence_start(flat, m.start())
                prev = flat[max(0, end - 300):end]
                same = flat[end:m.start()]  # 同一句里前半句就是「I received my PhD from X, where …」
                prev = prev[prev.rfind('. ', 0, len(prev) - 2) + 1:] if prev.rfind('. ', 0, len(prev) - 2) >= 0 else prev
                if PREV_PHD.search(same) and not re.search(r'(?i)post-?doc|master|bachelor|undergrad|intern', same):
                    pass
                elif not (PREV_PHD.search(prev) and not re.search(r'(?i)post-?doc|master|bachelor|undergrad|intern', prev)):
                    continue
            if rx is PROSE_MY and section_at(heads, m.start()) == 'student':
                continue
            mid2 = re.sub(r'(?i)\b(?:m\.?s\.?|master\'?s?)\s+and\s+ph\.?\s?d\.?|ph\.?\s?d\.?\s+and\s+(?:m\.?s\.?|master\'?s?)\b', '', mid)
            if re.search(r'(?i)\b(?:also|closely|collaborat|intern|visit|summer\s+(?:intern|research|school|student)|postdoc|post-doctoral|master|m\.s\.|'
                         r'undergrad|bachelor|b\.s\.|b\.a\.)', mid2) and typ == 'phd':
                continue  # 「PhD … where I also worked closely with X」「Master's … advised by」
            if 'with' in link or 'under' in link or 'group' in link or 'lab' in link:
                if re.search(r'(?i)\b(?:also|closely|collaborat)', mid + link):
                    continue
            if typ == 'postdoc' and re.search(r'(?i)\bph\.?\s?d\b', mid):
                continue
            third = not re.match(r'\s*(?:I|my|My)(?![A-Za-z])', flat[m.start():m.start() + 4])
            if third and not (mentions_self(flat[max(0, m.start() - 1500):m.start() + 20], self_name)
                              or mentions_self(flat[:400], self_name)):
                continue  # 第三人称「He received …」：页首或前文得提到本人名字（院系名单页上会有别人的简介）
            if re.search(r'(?i)during\s+(?:my|his|her|their)\s+visit|while\s+visiting|as\s+a\s+visiting',
                         flat[m.end():m.end() + 60]):
                continue  # 「supervised by X during my visit to …」是访问期间的导师
            weak = bool(re.search(r'with|under$|group|lab', link.strip()))
            for n in names_from(m, known, self_k, flat):
                found.append((n, typ, flat[max(0, m.start() - 60):m.end() + 30], 'prose-with' if weak else 'prose'))
    out, seen = [], set()
    for n, typ, snip, how in found:
        if (key(n), typ) not in seen:
            seen.add((key(n), typ))
            out.append((n, typ, snip, how))
    return out


def docs_for(p):
    """[(来源 cv/homepage, URL, 文本)]"""
    out = []
    for kind in ('home', 'cv'):
        f = os.path.join(PAGES, f'{p["id"]}_{kind}.txt')
        if os.path.exists(f):
            txt = open(f, encoding='utf-8', errors='replace').read()
            first, _, body = txt.partition('\n')
            if body.strip():
                out.append(('cv' if kind == 'cv' else 'homepage', first.replace('#URL ', '').strip(), body))
    f = os.path.join(PEOPLE, p['id'] + '.json')
    if os.path.exists(f):
        d = json.load(open(f, encoding='utf-8'))
        if d['pages'] and d['pages'][0]['text'].strip():
            out.append(('homepage', d['pages'][0]['url'], d['pages'][0]['text']))
    f = os.path.join(ADV, p['id'] + '.json')
    if os.path.exists(f):
        for pg in json.load(open(f, encoding='utf-8'))['pages']:
            if pg['text'].strip():
                src = 'cv' if pg['kind'] == 'cv' else 'homepage'
                out.append((src, pg.get('final') or pg['url'], pg['text']))
    seen, uniq = set(), []
    for d in out:
        h = hash(d[2][:5000])
        if h not in seen:
            seen.add(h)
            uniq.append(d)
    return uniq


def main():
    only = sys.argv[sys.argv.index('--pid') + 1] if '--pid' in sys.argv else None
    known, _ = load_graph()
    T = targets()
    recs = []
    for p in T:
        if only and p['id'] != only:
            continue
        hits = {}
        for src, url, text in docs_for(p):
            for n, typ, snip, how in extract_doc(text, p['name'], known, src):
                k = (key(n), typ)
                if k not in hits or (hits[k]['how'] != 'label' and how == 'label') or (hits[k]['source'] != 'cv' and src == 'cv' and how == hits[k]['how']):
                    hits[k] = dict(prof=p['id'], rel='advisor', name=n, type=typ, source=src, url=url,
                                   snippet=re.sub(r'\s+', ' ', snip), how=how, prof_name=p['name'])
        # 简历里写明了「Advisor: X」时，句子里「worked with Y」这类弱说法的 Y 不要（多是合作者）
        lab = {k for k, h in hits.items() if h['how'] == 'label'}
        for t in ('phd', 'postdoc'):
            if any(k[1] == t for k in lab):
                hits = {k: h for k, h in hits.items() if not (k[1] == t and h['how'] == 'prose-with')}
        phd = [h for h in hits.values() if h['type'] == 'phd']
        if len(phd) > 3:  # 抽出四个以上 PhD 导师：多半混进了名单，整位教授都不要
            hits = {k: h for k, h in hits.items() if h['type'] != 'phd'}
        post = [h for h in hits.values() if h['type'] == 'postdoc']
        if len(post) > 3:
            hits = {k: h for k, h in hits.items() if h['type'] != 'postdoc'}
        # 同一个人既被认成 PhD 导师又被认成 postdoc 导师：保留 PhD
        phd_keys = {k[0] for k in hits if k[1] == 'phd'}
        recs += [h for k, h in hits.items() if not (k[1] == 'postdoc' and k[0] in phd_keys)]
    if only:
        for r in recs:
            print(r)
        return
    print('targets', len(T), 'records', len(recs), 'professors', len({r['prof'] for r in recs}),
          'with phd', len({r['prof'] for r in recs if r['type'] == 'phd'}),
          'phd', sum(r['type'] == 'phd' for r in recs), 'postdoc', sum(r['type'] == 'postdoc' for r in recs))
    if '--dry' in sys.argv:  # 只写片段文件到临时目录，不动 data/enrich
        with open(os.path.join(os.environ.get('TEMP', '/tmp'), 'homepage_extra_dry.jsonl'), 'w', encoding='utf-8') as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        return
    with open(os.path.join(ROOT, 'data/enrich/homepage_extra.jsonl'), 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps({k: r[k] for k in ('prof', 'rel', 'name', 'type', 'source', 'url')}, ensure_ascii=False) + '\n')
    with open(os.path.join(ROOT, 'data/cache/homepage_extra_snippets.jsonl'), 'w', encoding='utf-8') as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
