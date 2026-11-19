"""从已抓的教授主页 / CV 文字里提取当前职称（Assistant Professor、Associate Teaching Professor、Research Professor、
Professor of the Practice、Senior Lecturer、Research Scientist……）。

规则（按可信度从高到低，每个来源依次尝试）：
  1. 紧跟在本人完整名字后面的职称（"Jiang Hu\\nProfessor\\n..."、"Xin He, Professor"、院系目录页里本人那一条；
     名字去重音、允许中间名缩写 / 连字符 / 名字前缀 "Alex"≈"Alexander"；中间隔着别人的名字行就不算）
  2. 现在时陈述："I am an Associate Professor"、"Smith is a Teaching Professor"、"I have been a professor"
  3. 单独的姓后面（前一个词得像本人的名，"Saksham Agarwal" 这种同姓学生不算）
  4. 履历式任职行里带 "–present / since 2019" 的那条（CV 的 Appointments）
  5. 主页开头（前 2500 字）的第一个职称——页面上没有本人名字、院系目录页、带年份的行都不用；
     几位教授拿到一模一样的上下文（同一个院系模板页）就整组丢掉（drop_shared）
跳过：formerly / was / previously / until 2023 / PhD student advised by Professor X / now / → / will join（学生去向）/
"we are hiring assistant professors" / "joined X as an Assistant Professor in 2015"（太早，多半已升职）/
已结束的年份区间 "(2015-2020)" / 论文标题、奖项名里的 / 导航菜单里的 "Teaching\\nAssociate Professor"；
"Professor Smith" 这种称呼不算；Courtesy / Affiliate / Junior Professor 不算；
冠名教职统一成职级（"Fredkin Professor" → Professor，"Berry Family Distinguished Associate Professor" → Associate Professor），
名字后先写 Adjunct / Visiting 再写正职的用正职。Research Scientist 系列只认带 Research 的。找不到就不写，网站上显示 "Faculty"。
来源顺序：主页 data/cache/people_pages/{pid}.json 的 pages[0]（缺的用 data/cache/pages/{pid}_home.txt）→
CV data/cache/pages/{pid}_cv.txt → people_pages 里的其他子页（只认规则 1，且要完整名字）。
输出: data/enrich/titles.json  {pid: title}
用法: python scripts/extract_titles.py [--sample N] [--debug]   （--debug 打印每条结果的来源和上下文）
"""
import json
import os
import random
import re
import sys
import unicodedata
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402

THIS_YEAR = 2026
MODS = (r'Alumni(?=[ \t]+Distinguished)|Distinguished|University|Full|Associate|Assistant|Assoc\.?|Asst\.?|Adjunct|Visiting|Research|Teaching|'
        r'Clinical|Senior|Principal|Staff|Junior|Courtesy|Affiliate|Affiliated|Tenure[- ]Track|Tenured|Endowed|'
        r'Chaired|Collegiate|Named|Regents\'?|Institute|Presidential|Emerit(?:us|a)')
RANK = re.compile(r'(?i)distinguished|full|associate|assistant|assoc|asst|adjunct|visiting|clinical|senior|principal')
# 修饰词之间：空格 / 逗号 / 连字符 / PDF 里粘连的 "AssistantProfessor"，最多一个换行
SEP = r'(?:[ \t,\-]*\n[ \t]*|[ \t,\-]*)'
TITLE = re.compile(rf'(?<![A-Za-z])((?:(?:{MODS}){SEP}){{0,4}})(Professor|Lecturer|Instructor|Scientist)'
                   r'(?![a-z])((?:[ \t]+of[ \t]+(?:the[ \t]+)?(?:Practice|Teaching|Instruction)(?![a-z]))'
                   r'|(?:,?\s+Emerit(?:us|a)(?![a-z])))?', re.I)
ORG = re.compile(r'[ \t]+(School|Department|Dept|Computer|Electrical|College|Institute|Center|Centre|Faculty|Division|'
                 r'Program|Information|Engineering|Mathematics|Data|Emerit|Director|Chair|Head|Associate|Dean|EECS|CS|'
                 r'Robotics|Machine|Statistics|Electronics|Biomedical|Mechanical|Industrial|Civil|Physics|Software|'
                 r'Management|Operations|Applied|University|Univ|Office)(?![a-z])')
# "joined X as an Assistant Professor in 2015"：入职年份太早的不算当前职称（多半已经升职）
JOINED = re.compile(r'(?<![a-z])joined(?![a-z])[^.\n]{0,80}$', re.I)
OLD_YEAR = re.compile(rf'^[^.\n]{{0,50}}?(?<!\d)(19\d\d|20[01]\d|20[2-9]\d)(?!\d)')
BAD_WORDS = (r'former(?:ly)?|previous(?:ly)?|was|were|been\s+(?:an?\s+)?(?:visiting|former)|prior|before|until|'
             r'postdoc\w*|student|students|alum\w*|graduate[ds]?|advis\w*|supervis\w*|mentor\w*|worked|under|with|by|'
             r'thank\w*|colleague|hiring|hire|hired|recruit\w*|positions?\s+(?:for|as|of|open\w*|available)|'
             r'seeking|opening|search|apply|applications?|candidates?|dear|'
             r'(?<![-–—])(?<![-–—] )(?<!I\'m )(?<!I’m )(?<!I am )now|became|become|becoming|next|later|then|will|start\w*|took|accepted|offer|first\s+job|placement|congrat\w*|like|other|'
             r'served|serving|award|awards|lecture|named\s+after|in\s+memoriam|late')
BEFORE_BAD = re.compile(rf'(?<![a-z])(?:{BAD_WORDS})(?![a-z])[^\w\n]*(?:an?\s+|the\s+)?((?:[\w\'’]+[ ,-]+){{0,3}})\Z', re.I)
# 同一句话里前面出现过去时："Previously, I was an Assistant Professor at X, a Research Assistant Professor at Y"
PAST_SENT = re.compile(r'(?<![a-z])(was|were|previously|formerly|prior\s+to|had\s+been|used\s+to|before\s+(?:that|'
                       r'joining|coming|moving))(?![a-z])', re.I)
ARROW = re.compile(r'(→|->|=>|⟶|➝)\s*$')
# 第一人称 / 第三人称现在时："I am (currently) an"、"I'm a"、"I have been a"、"<名字> is an"、"He is the"
PRESENT = re.compile(r'(?:(?<![A-Za-z])I\s+am|(?<![A-Za-z])I[\'’]m|(?<![A-Za-z])I\s+have\s+been|'
                     r'(?<![A-Za-z])I\s+currently\s+am|(?<![A-Za-z])I\s+serve\s+as|(?<![A-Za-z])I\s+work\s+as|'
                     r'currently\s+(?:serves?|works?)\s+as|(?<![A-Za-z])(?:is|am))\s+'
                     r'(?:currently\s+|also\s+|presently\s+|now\s+|still\s+|both\s+|proud\s+to\s+be\s+)?'
                     r'(?:an?\s+|the\s+)?(?:[\w.\'’&-]+\s+){0,4}$', re.I)
# 小写的裸 "professor" 只认最直接的说法："I am a professor in ..."（"I'm just a humble professor" 不算）
PRESENT_STRICT = re.compile(r'(?:(?<![A-Za-z])I\s+am|(?<![A-Za-z])I[\'’]m|(?<![A-Za-z])I\s+have\s+been|'
                            r'(?<![A-Za-z])is)\s+(?:currently\s+|also\s+|now\s+|still\s+)?(?:an?|the)\s+'
                            r'(?:full\s+|tenured\s+)?$', re.I)
YEAR_RANGE = re.compile(r'((?:19|20)\d\d)\s*(?:[-–—]|to)\s*(?:[A-Za-z]{3,9}\.?\s+)?((?:19|20)?\d\d)(?!\d)')
OPEN_RANGE = re.compile(r'(?:(?:19|20)\d\d|[\'’]\d\d|since)\s*(?:[-–—]+|to|until)?\s*(?:present|now|current|today|'
                        r'date)(?![a-z])|(?:19|20)\d\d\s*[-–—]+[ \t]*(?:$|\)|;)', re.I | re.M)
SINCE = re.compile(r'\b(?:since|from)\s+(?:\w+\s+)?(?:19|20)\d\d\b(?!\s*(?:[-–—]|to)\s*(?:19|20)?\d\d)', re.I)
PAREN = re.compile(r'\s*\(\s*((?:Assistant|Associate|Full)\s+Professor)\s*\)', re.I)
KEEP = ['Distinguished', 'Associate', 'Assistant', 'Adjunct', 'Visiting', 'Research', 'Teaching', 'Clinical',
        'Senior', 'Principal', 'Staff', 'Collegiate']
ORDER = ['Distinguished', 'Senior', 'Principal', 'Staff', 'Collegiate', 'Visiting', 'Adjunct', 'Clinical', 'Associate',
         'Assistant', 'Research', 'Teaching']


def past_range(s):
    """有已经结束的年份区间（2015-2020；"on leave 2026-2027" 这种还没结束的不算）。"""
    for a, b in YEAR_RANGE.findall(s):
        end = int(b) if len(b) == 4 else int(a[:2] + b)
        if int(a) <= end < THIS_YEAR:
            return True
    return False


def norm(mods, base, tail):
    ms = [m.rstrip('.').capitalize() for m in re.findall(MODS, mods, re.I)]
    ms = [{'Asst': 'Assistant', 'Assoc': 'Associate'}.get(m, m) for m in ms]
    low = {m.lower() for m in ms}
    if low & {'courtesy', 'affiliate', 'affiliated', 'junior'}:
        return None
    base = base.capitalize()
    kept = [m for m in KEEP if m.lower() in low]
    if base == 'Scientist':
        # 只认 Research Scientist 系列（Senior / Principal / Staff / Associate / Assistant Research Scientist）
        if 'Research' not in kept or low & {'distinguished', 'visiting', 'adjunct', 'teaching', 'clinical'}:
            return None
    elif 'Staff' in kept:
        return None
    if 'Associate' in kept and 'Assistant' in kept:
        return None
    if 'Distinguished' in kept and ('Associate' in kept or 'Assistant' in kept):
        kept.remove('Distinguished')  # "Berry Family Distinguished Associate Professor" 是冠名，职级是 Associate
    kept.sort(key=ORDER.index)
    t = ' '.join(kept + [base])
    tail = re.sub(r'[\s,]+', ' ', tail or '').strip()
    if tail and not tail.lower().startswith('emerit'):
        t += ' of ' + ('the ' if ' the ' in tail.lower() else '') + tail.split()[-1].capitalize()
    if 'emeritus' in low or 'emerita' in low or tail.lower().startswith('emerit'):
        t += ' Emeritus'
    return t


def fold(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))


def fold_text(text):
    """逐字去重音（长度不变，位置和原文一一对应），名字匹配都在这上面做。"""
    return ''.join((fold(c) or c)[:1] for c in text)


def letter_re(ch):
    b = (fold(ch) or ch)[:1]
    alts = [b] + ([b + 'e'] if ch.lower() in 'äöü' else [])  # Müller 也写成 Mueller
    return re.escape(b) if len(alts) == 1 else '(?:' + '|'.join(map(re.escape, alts)) + ')'


def word_re(w):
    return r'[\s-]?'.join(''.join(letter_re(c) for c in part) for part in w.split('-'))


def first_re(f):
    """名：带连字符的后半可省（"Wu-chun" / "Wu"），长名字可以只写前 3 个字母开头（Alex / Alexander，Sotiris / Sotirios）。"""
    if '-' in f:
        a, b = f.split('-', 1)
        return word_re(a) + rf'(?:[\s-]?{word_re(b)})?'
    if len(f) >= 4 and '.' not in f:
        return word_re(f[:3]) + r'\w*'
    return word_re(f)


def name_patterns(name):
    """完整名字（中间名/缩写可有可无，不分大小写）和单独的姓（区分大小写，至少 4 个字母）。都匹配去重音后的文字。"""
    toks = [t for t in re.split(r'\s+', name.strip()) if t]
    toks = [t for t in toks if not re.fullmatch(r'(?i)(jr|sr|ii|iii|iv|phd|ph\.d)\.?,?', t)]
    if not toks:
        return None, None, set()
    real = [t for t in toks if len(t.rstrip('.')) > 1]
    first, last = (real[0], real[-1]) if len(real) >= 2 else (toks[0], toks[-1])
    lasts = [last] + ([real[1]] if len(real) >= 3 else [])  # "Parinaz Naghizadeh Ardabili" 常写成 "Parinaz Naghizadeh"
    pats = []
    for ln in lasts:
        pats.append(rf'(?<![\w]){first_re(first)}(?:[\s.\-]+[\w.()"“”\'-]+){{0,2}}?[\s.\-]+{word_re(ln)}(?![\w])')
    pats.append(rf'(?<![\w]){word_re(last)},\s*{first_re(first)}(?![\w])')
    fullp = re.compile('|'.join(pats), re.I)
    lf = fold(last)
    lastp = re.compile(rf'(?<![\w]){word_re(last)}(?![\w])') if len(lf) >= 4 and lf[0].isupper() else None
    # 名和中间名的首字母（"Robert M. Kirby" 也叫 "Mike Kirby"）
    return fullp, lastp, {fold(t)[:1].lower() for t in toks[:-1]} or {fold(first)[:1].lower()}


def candidates(text):
    """页面里所有像职称的片段（已去掉明显不是本人当前职称的）。
    返回 [(start, end, title, present, before, line)]，present = 前面是 "I am a / X is a" 这类现在时陈述。"""
    out = []
    for m in TITLE.finditer(text):
        s = m.start()
        mods = m.group(1) or ''
        if '\n' in mods and not RANK.search(mods[:mods.rfind('\n')]):
            # "Teaching\nAssociate Professor"：换行前只是导航菜单里的一项，不算修饰词
            s += mods.rfind('\n') + 1
            while text[s] in ' \t':
                s += 1
            mods = text[s:m.start(2)]
        base = m.group(2)
        before = text[max(0, s - 70):s]
        after = text[m.end():m.end() + 80]
        line_start = text.rfind('\n', 0, s) + 1
        line_end = text.find('\n', m.end())
        line_end = len(text) if line_end < 0 else line_end
        line = text[max(line_start, s - 60):min(line_end, m.end() + 60)]
        if BEFORE_BAD.search(before) or ARROW.search(before) or re.search(r'(?i)(?<![a-z])(new|incoming)\s+$', before):
            continue
        sent = re.split(r'[.;!?]\s|\n\s*\n', text[max(line_start, s - 200):s])[-1]
        if PAST_SENT.search(sent):
            continue
        if re.search(r'(?i)\bwill\s+(?:soon\s+)?(?:join|start|move|become|begin|be\s+joining|be\s+starting)\b', sent):
            continue  # "Dr. X will join SUNY Albany as a Visiting Assistant Professor"（说的是学生去向 / 将来）
        um = re.search(r'(?i)\buntil\s+(?:\w+\s+)?((?:19|20)\d\d)\b', sent)
        if um and int(um.group(1)) < THIS_YEAR:  # "From May 2000 until September 2023 he is Full Professor"
            continue
        if sent.count('“') > sent.count('”') or sent.count('"') % 2:  # 论文 / 演讲标题里："Meet the Professor"
            continue
        jm = JOINED.search(text[max(0, s - 110):s])
        if jm and re.search(r'(?i)assistant|associate|asst|assoc', mods) and (
                re.search(r'(?<!\d)(19\d\d|20[01]\d|2020)(?!\d)', sent + ' ' + jm.group(0))
                or (OLD_YEAR.search(after) and int(OLD_YEAR.search(after).group(1)) <= THIS_YEAR - 6)):
            continue
        # 'Assistant Professors' / 'Professorship' 已被 (?![a-z]) 排除；"Professor Smith" 是称呼
        if (not mods.strip() and base.lower() == 'professor' and re.match(r'[ \t]+(?:[A-Z][a-z]|[A-Z]\.)', after)
                and not ORG.match(after) and not re.match(r'[ \t]+(?:of|in|at|and|&)\b', after)):
            continue
        # 新闻体小写 "assistant professor Han Zhao"：说的是别人
        if base[0].islower() and re.match(r'[ \t]+[A-Z][a-z]+[ \t]+[A-Z][a-z]+', after) and not ORG.match(after):
            continue
        present = bool(PRESENT.search(before))
        # 正文里的小写裸 "professor"：只认 "I am a professor" 或单独一行（"charless c. fowlkes\nprofessor\n..."）
        if (base[0].islower() and not mods.strip() and not PRESENT_STRICT.search(before)
                and not re.fullmatch(r'\s*', text[line_start:s] + text[m.end():line_end])):
            continue
        # 这一行（或紧跟的 40 字）里有已结束的年份区间、且不是 "–present"：过去的职位
        if (past_range(line) or past_range(after[:40])) and not (
                OPEN_RANGE.search(line) or OPEN_RANGE.search(after.split('\n')[0][:60])):
            continue
        # 'Senior Scientist Award' / 'Distinguished Lecturer of IEEE' / 'Professor of the Year Award'
        if re.match(r'[ \t]*(?:[A-Z]\w+[ \t]+){0,2}(?:of[ \t]+the[ \t]+Year|Award|Prize|Fellowship|Program|Series|Lecture|Talk|Position|'
                    r'Search|Job|Opening|Candidate)s?\b', after, re.I):
            continue
        if base.lower() in ('lecturer', 'instructor') and (
                re.search(r'(?i)distinguished|named|memorial|keynote|invited|guest|plenary|summer', mods + line)
                or re.search(r'(?:19|20)\d\d', line) and not OPEN_RANGE.search(line)):
            continue
        if base.lower() == 'instructor' and not re.search(r'(?i)clinical|senior|teaching|principal', mods):
            continue  # "instructor: Prof. X"、课程页里的 instructor
        pm = PAREN.match(after) if re.search(r'(?i)junior', mods) else None
        t = norm(pm.group(1), 'Professor', '') if pm else norm(mods, base, m.group(3))
        if not t:
            continue
        out.append((s, m.end(), t, present, before, line))
    return out


def is_directory(cands, text):
    """院系目录 / 实验室成员页：很多行单独写着职称（每个人一条）。"""
    solo = 0
    for s, e, t, *_ in cands:
        ls = text.rfind('\n', 0, s) + 1
        le = text.find('\n', e)
        le = len(text) if le < 0 else le
        if len(text[ls:le].strip()) <= len(text[s:e].strip()) + 45:
            solo += 1
    return solo >= 4


def ctx(text, s, e):
    return re.sub(r'\s+', ' ', text[max(0, s - 70):e + 50]).strip()


def upgrade(c, cands, text, name_ends):
    """选中的是 "Professor"，但紧挨着（80 字内）、或本人名字后面、或第一人称里写了 "Professor Emeritus" /
    "Distinguished Professor"：用更具体的（"RICH KORF, Professor Emeritus"）。"""
    t = c[2]
    for d in cands:
        if d is c:
            continue
        # 紧挨着、且中间没隔着另一个单位（"Professor, TTIC\nProfessor Emeritus, UCSB" 不算）
        close = 0 <= d[0] - c[1] < 80 and not re.search(r'(?i)universit|institute|college|school', text[c[1]:d[0]])
        if d[2] == t + ' Emeritus' and (close or d[3] or any(0 <= d[0] - e < 40 for e in name_ends)):
            return d[2]
        if t == 'Professor' and d[2] == 'Distinguished Professor' and close:
            return d[2]
        # 冠名教职后面紧跟着真实职级："McKnight Land-Grant Professor\nAssistant Professor"
        named = text[text.rfind('\n', 0, c[0]) + 1:c[0]].strip()
        if (t == 'Professor' and d[2] in ('Assistant Professor', 'Associate Professor') and close and named
                and text[c[1]:d[0]].count('\n') <= 1 and len(text[c[1]:d[0]].strip(' ,;()\n\t')) == 0):
            return d[2]
    return t


def find_title(text, name, kind='home'):
    """返回 (title, rule, context) 或 None。kind: home / cv / sub"""
    if not text:
        return None
    text = text[:40000]
    fullp, lastp, initial = name_patterns(name)
    if not fullp:
        return None
    cands = candidates(text)
    if not cands:
        return None
    directory = is_directory(cands, text)
    ft = fold_text(text)
    # 名字出现的位置（姓不分大小写，"RICH KORF, Professor Emeritus"）
    name_ends = [m.end() for m in fullp.finditer(ft)]
    if lastp:
        name_ends += [m.end() for m in re.finditer(lastp.pattern, ft, re.I)]

    def hit(c, rule):
        return upgrade(c, cands, text, name_ends), rule, ctx(text, c[0], c[1])

    def after_name(occs, win):
        for o in occs:
            pre = text[max(0, o.start() - 40):o.start()]
            # 名字是作为导师 / 合作者出现的："advisor: Sean Hallgren"、"with Professor X"
            if re.search(r'(?i)(advis\w*|student|with|by|under|thanks?)\W*(?:prof\w*\.?\s*|dr\.?\s*)?$', pre):
                continue
            nxt = [c for c in cands if 0 <= c[0] - o.end() < win]
            if not nxt:
                continue
            c = nxt[0]
            # 名字后面先写兼职（"Adjunct Professor, UT Dallas\nProfessor of Computer Science, ..."）：用后面的正职
            if re.search(r'Adjunct|Visiting', c[2]):
                c = next((d for d in cands if 0 < d[0] - c[0] < 250 and not re.search(r'Adjunct|Visiting', d[2])), c)
            between = text[o.end():c[0]]
            if re.search(r'(?i)student|advis|postdoc|alumni|graduated|former|previous', between):
                continue
            if directory and between.count('\n') > 4:
                continue
            # 中间隔着另一个人名行（实验室成员页："Saeed Hassanpour\nPrincipal Investigator\n...\nNaofumi Tomita\nResearch Scientist"）
            if any(NAME_LINE.fullmatch(ln.strip()) and not NOT_NAME.search(ln) and not fullp.search(fold(ln))
                   for ln in between.split('\n')[1:-1]):
                continue
            return c
        return None

    # 1. 紧跟在本人完整名字后面（目录页里窗口更小）
    c = after_name(list(fullp.finditer(ft)), 160 if directory else 300)
    if c:
        return hit(c, 'name')
    if kind == 'sub':
        return None

    # 2. 现在时陈述：主语要是 I / 本人名字 / 他她（他她只在页面前部）
    for c in cands:
        if not c[3]:
            continue
        b = c[4]
        pm = PRESENT.search(b)
        # "I am also a research scientist at Google"：兼职，不当作主职称
        if c[2].endswith('Scientist') and re.search(r'(?i)\balso\b|\bpart[- ]time\b', pm.group(0)):
            continue
        if re.match(r'I(?![a-z])', pm.group(0)):
            return hit(c, 'I am')
        sm = re.search(r'(\S+)\s+$', b[:pm.start()])
        subj = sm.group(1) if sm else ''
        fb = ft[c[0] - len(b):c[0]]
        if subj and (fullp.search(fb[max(0, pm.start() - 40):pm.start() + 1])
                     or (lastp and lastp.fullmatch(fold(subj).strip(',')))):
            return hit(c, 'X is')
        if subj.lower() in ('he', 'she', 'they') and c[0] < 5000 and not directory:
            return hit(c, 'he/she is')

    # 3. 单独的姓后面（前一个词要么不是大写单词，要么和名字首字母相同："Ben Fuller" 可以，"Saksham Agarwal" 不行）
    if lastp:
        occs = []
        for o in lastp.finditer(ft):
            pw = re.search(r'([A-Za-z][\w\'’-]*)[ \t]+$', ft[max(0, o.start() - 30):o.start()])
            if pw and pw.group(1)[0].isupper() and fold(pw.group(1))[:1].lower() not in initial \
                    and pw.group(1).lower() not in ('dr', 'prof', 'professor', 'mr', 'ms', 'mrs'):
                continue
            occs.append(o)
        c = after_name(occs, 200)
        if c:
            return hit(c, 'surname')

    # 4. 履历式任职行：带 "–present" / "since 2019"（取第一条正职；Adjunct/Visiting 只在没有正职时用）
    cur = []
    for i, c in enumerate(cands):
        prev_end = cands[i - 1][1] if i else 0
        pre = text[max(prev_end, c[0] - 40):c[0]]  # 年份在上一行："2014- present\nProfessor, ..."
        nxt = text[c[1]:c[1] + 200].split('\n')
        # "Assistant Professor\nJuly 2015 - present"；如果日期写在职称前一行（"2002–2019\nAssociate Professor"），下一行属于下一条
        date_first = re.search(r'(?:19|20)\d\d', text[text.rfind('\n', 0, text.rfind('\n', 0, c[0])) + 1:c[0]])
        post = nxt[0] + ('\n' + nxt[1] if len(nxt) > 1 and len(nxt[1].strip()) < 40 and not date_first else '')
        if (OPEN_RANGE.search(c[5]) or SINCE.search(c[5]) or OPEN_RANGE.search(pre) or OPEN_RANGE.search(post)
                or re.search(r'(?:19|20)\d\d\s*[-–—]+\s*\Z', pre)):
            cur.append(c)
    main = [c for c in cur if not re.search(r'Adjunct|Visiting', c[2])] or cur
    if main:
        return hit(main[0], 'present')
    if kind == 'cv':
        return None

    # 5. 主页开头（不是目录页；带年份的行不算，多半是履历 / 获奖列表）
    if not directory and cands[0][0] < 2500:
        c = cands[0]
        # 页面里有好几处 "别人的名字\n职称"（院系教师列表 "Ioannis Koutis\nProfessor, Computer Science"）就不用这条
        others = sum(1 for d in cands[:30] if other_name_before(d, ft, fullp, lastp))
        # 页面上根本没有本人的名字（院系 / 学院首页之类），不用这条
        if not name_ends:
            return None
        if (not re.search(r'(?:19|20)\d\d', c[5]) or OPEN_RANGE.search(c[5])) and not re.search(
                r'Visiting|Adjunct', c[2]) and not re.search(r'(?i)appointments?\s+include', c[4]) \
                and others < 2 and not other_name_before(c, ft, fullp, lastp):
            return hit(c, 'early')
    return None


NAME_LINE = re.compile(r"(?:Dr\.?\s+)?[A-Z][a-z][\w'’.-]*(?:\s+(?:[A-Z]\.|[A-Z][a-z][\w'’.-]*)){1,3}"
                       r"(?:,?\s*Ph\.?\s?D\.?)?")
NOT_NAME = re.compile(r'(?i)\b(home|about|me|research|teaching|publications?|people|faculty|staff|news|contact|'
                      r'department|school|college|university|institute|center|lab|group|family|chair|the|of|and|'
                      r'engineering|science|sciences|computer|computing|menu|search|directory|profile|bio|'
                      r'principal|investigator|director|read|view|full|phd|candidate|student|email|contact|office|'
                      r'phone|room|hall|building|associate|assistant|professor|lecturer|scientist|chief|founder|'
                      r'ceo|dean|head|member|members|team|current|past|alumni|google|scholar|curriculum|vitae|cv)\b')


def other_name_before(c, text, fullp, lastp):
    """职称单独起一行，上一行是一个（不是本人的）人名。"""
    ls = text.rfind('\n', 0, c[0])
    if text[ls + 1:c[0]].strip():
        return False
    prev = text[text.rfind('\n', 0, max(ls, 0)) + 1:ls].strip() if ls > 0 else ''
    if not NAME_LINE.fullmatch(prev) or NOT_NAME.search(prev):
        return False
    return not (fullp.search(prev) or (lastp and re.search(lastp.pattern, prev, re.I)))


def drop_shared(info):
    """不靠名字定位的结果（页面开头 / 他她 is），如果好几位教授拿到的是一模一样的上下文，
    多半是同一个院系模板页 / 菜单里别人的职称（如 UB 目录页上的 "Assistant Professor of Teaching"），丢掉。"""
    by_ctx = Counter(r[2] for r in info.values() if r[1] in ('early', 'he/she is'))
    return {k: r for k, r in info.items() if not (r[1] in ('early', 'he/she is') and by_ctx[r[2]] > 1)}




def sources(pid):
    """[(kind, text)]：主页、CV、其他子页。"""
    out = []
    pages = []
    f = os.path.join(ROOT, 'data/cache/people_pages', pid + '.json')
    if os.path.exists(f):
        pages = json.load(open(f, encoding='utf-8')).get('pages') or []
    home = pages[0].get('text') if pages and pages[0].get('text') else ''
    f = os.path.join(ROOT, 'data/cache/pages', pid + '_home.txt')
    if not home and os.path.exists(f):
        home = open(f, encoding='utf-8', errors='replace').read()
    out.append(('home', home))
    f = os.path.join(ROOT, 'data/cache/pages', pid + '_cv.txt')
    if os.path.exists(f):
        cv = open(f, encoding='utf-8', errors='replace').read()
        if cv.startswith('#URL'):
            cv = cv.split('\n', 1)[1] if '\n' in cv else ''
        if cv.strip() and cv.strip()[:300] != home.strip()[:300]:
            out.append(('cv', cv))
    for pg in pages[1:]:
        if pg.get('text'):
            out.append(('sub', pg['text']))
    return out


def title_of(srcs, name):
    for kind, text in srcs:
        r = find_title(text, name, kind)
        if r:
            return r + (kind,)
    return None


def main():
    profs = load_profs()
    info = {}
    for p in profs:
        r = title_of(sources(p['id']), p['name'])
        if r:
            info[p['id']] = r
    info = drop_shared(info)
    out = {p['id']: info[p['id']][0] for p in profs if p['id'] in info}
    json.dump(out, open(os.path.join(ROOT, 'data/enrich/titles.json'), 'w', encoding='utf-8'), indent=0)
    print('titles', len(out), 'of', len(profs))
    print(Counter(out.values()).most_common(30))
    print(Counter(r[3] + ':' + r[1] for r in info.values()).most_common())
    if '--sample' in sys.argv:
        n = int(sys.argv[sys.argv.index('--sample') + 1])
        for p in random.Random(1).sample(profs, n):
            r = info.get(p['id'])
            print(p['name'], '|', p['uni'], '|', out.get(p['id']),
                  ('| ' + r[3] + '/' + r[1] + ' | ' + r[2]) if r and '--debug' in sys.argv else '')


if __name__ == '__main__':
    main()
