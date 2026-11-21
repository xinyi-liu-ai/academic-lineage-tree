"""从已抓的教授主页文字里判断：本人是否在主页上写了"正在招 PhD 学生"。

输入（全部是缓存，不联网）:
  data/cache/people_pages/{pid}.json   pages[0] 是主页，其余是 People / Students / Join 子页
  data/cache/pages/{pid}_home.txt       people_pages 全空时的备用主页（第一行 "#URL ..."）
  data/cache/recruit_pages/{pid}.json  fetch_recruit_pages.py 另抓的 Prospective / Join / Openings / FAQ / Contact /
                                       实验室网站 / 个人主页（排在上面两类之后；实验室页上 "Prof. X is recruiting" 要求 X 是本人）
  data/cache/lab_pages/{pid}.json.gz   fetch_lab_pages.py 抓的本人实验室网站（首页 / People / Join，HTML）
  data/cache/recruit_follow/{pid}.json fetch_lab_pages.py --follow：主页上只有不带年份的 "yes" 时，顺着 "THIS" / "details" /
                                       "Join" / "Openings" / "Positions" / "Prospective" 链接再抓的一层
输出: data/enrich/recruiting.json
  {pid: {"s": "yes"|"no", "q": 原句(<=220 字), "y": 目标入学年份或 null, "src": 页面 URL, "at": "YYYY-MM",
         "phd": 句子或所在栏目标题是否写明 PhD / doctoral / graduate, "old": 不带年份的 yes 而页面上最新的日期早于 2026,
         "inst": 同一段里提到要去 / 在别的学校招（"moving to HKUST"、"Oxford DPhil"）, "yf": 年份从哪来}}
  "at" 用缓存文件的修改时间，表示"什么时候看到的"。
  yf: "heading"（栏目标题 "Prospective Ph.D. students starting in Fall 2027:"）、"para"（同一段别的句子）、
      "stamp"（旁边的 "Updated on 05/02/2025"：取那一年、8 月以后写的算下一年）、"footer"（页脚 "Last updated"，页面上没有更新的日期时）、
      "older"（主页的 yes 不带年份，本人实验室 / Join / 链接页上有往年的 PhD 招生：用往年那句，网站上显示为可能过时）、
      "ambig"（"2026-2027 school year" 没写 start / join：页面最新日期在起始年 5 月以后时当作这一年里的申请季，取后一年）

规则:
  yes: 第一人称 / 本组明确在招 PhD（"I am looking for motivated PhD students"、"recruiting PhD students for Fall 2027"、
       "my group has openings for PhD students"、"I will be taking new students"、"fully funded PhD positions"、
       "apply to the PhD program and mention my name"）。泛指 "students" 也算，除非句子里只提本科 / 硕士 / 实习 / 博后。
       宾语是合作者、赞助方、受试者、助教的不算。
  no : 明确不招（"I am not taking new students"、"not accepting students for Fall 2027"、"no openings"、"my group is full"、
       "on sabbatical and not recruiting"）。"I do not accept students who haven't taken my class"、
       "I don't accept students every year" 这类附条件的不算。
  跳过: 目标年份早于 2026（"Fall 2019"、"2021-2022"；但可用来说明不带年份的 yes 已过时，见 yf="older"）；
        不带年份、在 (入学年 − 1) 年 8 月以前的新闻条目里（"Sep 2025: I am recruiting..."）；
        只是招有薪 RA / 按学期的科研助理 / 实习（"paid research work"、"at least one semester"）；
        院系 / 研究组 / 中心的公共页面上没点本人名字的句子（实验室网站被 >=2 位教授共用时也一样）；
        院系套话（"Prospective students should apply through graduate admissions"）；
        条件句 / 疑问句 / 讲别人的（"if you are looking for PhD students"、"students looking for advisors"）。
  年份: "Fall 2027" / "Fall '27" → 2027；"2027-2028 academic year" → 2027；"2026-27 application/admissions cycle" → 2027
        （申请季跨两年，入学是后一年）；"for 2027" → 2027。只在命中位置附近找。
  一个人有多句时：年份最新的优先；同年份时 no 优先于 yes（不招一般更具体），再按强度（带 PhD 字样 > 泛指 students）、
  主页优先于子页。
用法: python scripts/extract_recruiting.py [--sample N] [--debug]
  --sample N  随机打印 N 条 yes 和最多 N 条 no 供人工核对
  --debug     打印每条命中的所有候选句
"""
import json
import os
import random
import re
import sys
import time
import unicodedata
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from fetch_recruit_pages import home_prefix, under  # noqa: E402

THIS_YEAR = 2026
MAXQ = 220

# ---------------------------------------------------------------- 分句

TERM = re.compile(r'[.!?:;)"\]]\s*$')
# 菜单 / 标题行："Home"、"Notes for Prospective Students"、"Join Us"——不和上下文拼接
MENU_LINE = re.compile(r"^[A-Z\d][\w&'’-]*\.?(?:\s+(?:[A-Z\d][\w&'’-]*\.?|for|of|and|the|to|with|&|at|in|a|on|us)){0,5}$")
FUNC_END = re.compile(r'\b(?:a|an|the|of|for|in|to|and|or|with|at|my|our|on|by|from|as|is|are|am|be|who|that|students?)$', re.I)


def fix_mojibake(s):
    """UTF-8 被当成 Latin-1 解码的乱码（"youâ\\x80\\x99re"、"ð\\x9f\\x93\\x8c"）还原回去。"""
    if not re.search('[Â-ô][\u0080-¿]', s):
        return s
    for enc in ('latin-1', 'cp1252'):
        try:
            return s.encode(enc).decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return s


def norm_line(s):
    s = fix_mojibake(s)
    s = s.replace('Â\xa0', ' ').replace('\xa0', ' ').replace('Â ', ' ').replace('​', '')
    return re.sub(r'\s+', ' ', s).strip()


def paragraphs(text):
    """把网页 innerText 的行拼回段落：HTML 自动换行会把一句话拆成好几行，但导航菜单每行一个短词组。"""
    out, cur = [], ''
    for raw in text.split('\n'):
        line = norm_line(raw)
        if not line:
            if cur:
                out.append(cur)
            cur = ''
            continue
        if not cur:
            cur = line
            continue
        join = False
        tail = cur[-120:]           # 只看末尾，长段落上反复整段匹配会变成平方复杂度
        cur_menu = len(cur) < 80 and MENU_LINE.match(cur)
        if not TERM.search(tail):
            if line[0].islower() or line[0] in ',;)':
                join = True
            elif tail[-1] in ',(-–':
                join = True
            elif FUNC_END.search(tail) and not cur_menu:
                join = True
            elif not cur_menu and not (len(line) < 80 and MENU_LINE.match(line)) and (len(cur) > 60 or len(cur.split()) >= 5):
                # 上一行是没写完的长句，这一行也不是菜单 / 标题 -> HTML 自动换行
                join = True
        if join:
            cur = cur + ' ' + line
        else:
            out.append(cur)
            cur = line
    if cur:
        out.append(cur)
    return out


SENT_SPLIT = re.compile(r'(?<=[a-z0-9)\]][.!?])\s+(?=[A-Z"(\[“*•])|(?<=[!?])\s+|\s+[•|·]\s+')


def sentences(text):
    for para in paragraphs(text):
        prev = ''
        for s in SENT_SPLIT.split(para):
            s = s.strip(' -*•')
            if s:
                yield s, prev
                prev = s


# ---------------------------------------------------------------- 年份

SEASON_YEAR = re.compile(r"(?i)\b(?:fall|spring|winter|summer|autumn)(?:\s+(?:of|semester|quarter|term))?\s*['’]?\s*((?:20)?[0-3]\d)\b"
                         r"|\b((?:20)?[0-3]\d)\s+(?:fall|spring)\b")   # 后一种是 "26 Spring/Fall"
ACAD_YEAR = re.compile(r"(?:\b(20[0-3]\d)|['’]([0-3]\d))\s*[-–/]\s*['’]?((?:20)?[0-3]\d)\b(\s*(?:\w+\s+)?(?:application|admissions?|recruiting|hiring|applicant)s?\s+(?:cycle|season|round))?", re.I)
FOR_YEAR = re.compile(r'(?i)\b(?:for|in|starting|start(?:ing)? in|from|admission|admissions|cycle|class of|entering in)\s+(20[0-3]\d)\b(?![-–/]\d)')
ANY_YEAR = re.compile(r'\b(20[0-3]\d)\b')
DATE_STAMP = re.compile(r'(?i)(?:\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2},?\s+)?(20[0-3]\d)\b'
                        r'|\b(20[0-3]\d)[./-]\d{1,2}(?:[./-]\d{1,2})?\b|\bupdated?:?\s*(20[0-3]\d)|\b(?:as of|last updated)\s+(20[0-3]\d)'
                        r'|\b\d{1,2}/\d{1,2}/(\d{2}|20[0-3]\d)\b)')


def norm_year(y):
    y = int(y)
    return y + 2000 if y < 100 else y


ADMIT = THIS_YEAR + 1          # 现在（2026 年 11 月）招的是 2027 年入学
FRESH_CUT = (ADMIT - 1, 8)     # 早于这个年月的新闻 / "Updated on" 说的是往年的招生季
START_W = re.compile(r'(?i)\b(?:start\w*|begin\w*|join\w*|enter\w*|matriculat\w*|incoming|admit\w*\s+in)\b')


def target_year(s, newest=None):
    """入学年份：Fall 2027 / 2027-2028 学年 / 2026-27 申请季 / for 2027。返回 (年份, 是否是没写 start 的学年区间)。
    "2026-2027 school year" 没写 start / join：可能是 2026 年入学，也可能是 2026-27 学年里的申请季（2027 入学）；
    页面上最新日期在起始年 5 月以后时取后一年。"""
    ys = [norm_year(m.group(1) or m.group(2)) for m in SEASON_YEAR.finditer(s)]
    amb = False
    for m in ACAD_YEAR.finditer(s):
        a, b = norm_year(m.group(1) or m.group(2)), norm_year(m.group(3))
        if b - a != 1:
            continue
        if m.group(4):
            ys.append(b)
        elif START_W.search(s[max(0, m.start() - 40):m.end() + 25]) or re.search(r'(?i)admission\w*\s+(?:for|in)\s*$', s[max(0, m.start() - 20):m.start()]):
            ys.append(a)
        else:
            amb = True
            ys.append(b if newest and newest >= (a, 5) else a)
    ys += [int(m.group(1)) for m in FOR_YEAR.finditer(s)]
    ys = [y for y in ys if 2000 <= y <= 2035]
    return (max(ys) if ys else None), amb


MONTHS = {m: i + 1 for i, m in enumerate('jan feb mar apr may jun jul aug sep oct nov dec'.split())}
YM_PATTERNS = [
    (re.compile(r'(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?(20[0-3]\d)\b'),
     lambda m: (int(m.group(2)), MONTHS[m.group(1).lower()[:3]])),
    (re.compile(r'\b(20[0-3]\d)[./-](\d{1,2})(?:[./-]\d{1,2})?\b'), lambda m: (int(m.group(1)), int(m.group(2)))),
    (re.compile(r'\b(\d{1,2})/\d{1,2}/(20[0-3]\d|\d{2})\b'), lambda m: (norm_year(m.group(2)), int(m.group(1)))),
    (re.compile(r'\b(\d{1,2})/(20[0-3]\d)\b'), lambda m: (int(m.group(2)), int(m.group(1)))),
    (re.compile(r'(?i)\b(?:spring|summer|fall|autumn|winter)\s+(20[0-3]\d)\b'), lambda m: (int(m.group(1)), {'spr': 2, 'sum': 6, 'fal': 9, 'aut': 9, 'win': 1}[m.group(0)[:3].lower()])),
]
UPD = re.compile(r'(?i)\b(?:last\s+)?(?:updated?|modified|revised)\b\s*(?:on|at|in)?\s*:?\s*|\bas\s+of\s+')
NOW_YM = (2026, 11)


def stamp_ym(s, seasons=True):
    """文字里的年月（"Oct 2026"、"2026/06"、"05/02/2025"、"10/2024"、"Fall 2026"）。"""
    out = []
    for i, (p, fn) in enumerate(YM_PATTERNS):
        if i == 4 and not seasons:
            continue
        for m in p.finditer(s):
            try:
                y, mo = fn(m)
            except (KeyError, ValueError):
                continue
            if 2000 <= y <= 2035 and 1 <= mo <= 12 and (y, mo) <= NOW_YM:
                out.append((y, mo))
    return out


def upd_stamps(s):
    """"Updated on 05/02/2025"、"Last updated: Feb 2026"、"as of March 2025" 里的年月。"""
    out = []
    for m in UPD.finditer(s):
        out += stamp_ym(s[m.end():m.end() + 30], seasons=False)[:1]
        if not out:
            y = re.match(r'\s*(20[0-3]\d)\b', s[m.end():m.end() + 8])
            if y and int(y.group(1)) <= NOW_YM[0]:
                out.append((int(y.group(1)), 1))
    return out


def stamp_years(s):
    """新闻日期戳（"Sep 2020:"、"2021/09"、"Update 2019"、"3/11/25"）——表示这句话写于哪年。"""
    out = []
    for m in DATE_STAMP.finditer(s):
        out += [norm_year(g) for g in m.groups() if g]
    return out


# ---------------------------------------------------------------- 句型

PHD = r'(?<![\w-])(?:ph\.?\s?d\.?|doctoral|graduate|grad)'
STU = r'(?:students?|researchers?|candidates?|applicants?|advisees?|members?)'
OBJ_PHD = rf'(?:{PHD}(?:\s+(?:and|or|&|/)\s+\w+)?(?:[\s-]+level)?\s+{STU}|{PHD}\s+(?:positions?|researchers?)|(?<![\w-])ph\.?\s?d\.?s\b|(?<![\w-])phd\b|(?<![\w-])ph\.\s?d\.(?=[\s,/]))'
ADJ = (r'(?:new|motivated|talented|strong|bright|excellent|enthusiastic|passionate|self-motivated|highly[- ]motivated|creative|curious|'
       r'exceptional|hard-working|smart|capable|dedicated|outstanding|great|good|interested|prospective|incoming|additional|more|any)')
OBJ_ANY = rf'(?:{OBJ_PHD}|(?:{ADJ}[\s,]+(?:and\s+)?)*students\b)'

FIRST = (r"(?:\bi\b|\bi'm\b|\bi’m\b|\bwe\b|\bwe're\b|\bwe’re\b|\bmy\s+(?:\w+\s+)?(?:group|lab|team)\b|\bour\s+(?:\w+\s+)?(?:group|lab|team)\b|"
         r"\bthe\s+(?:group|lab|team)\b|\b(?:prof\.?|professor|dr\.?)\s+[A-Z]\w+|"
         # 主页上的第三人称："She is currently recruiting PhD students"、"Wan is always looking for Ph.D. students"
         r"(?:\b(?:he|she)|(?-i:\b[A-Z][a-z]+))\s+(?=is\s+(?:\w+ly\s+|always\s+|also\s+){0,2}(?:recruiting|looking\s+for|seeking|hiring|accepting)))")
RECRUIT_V = (r"(?:(?:am|are|is|'m|’m|'re|’re)\s+(?:\w+ly\s+|also\s+|still\s+|always\s+|now\s+){0,3}(?:looking\s+for|seeking|recruiting|hiring|accepting|taking(?:\s+on)?|admitting|searching\s+for|looking\s+to\s+(?:recruit|hire|add|take(?:\s+on)?))"
             r"|(?:will|plan\s+to|hope\s+to|expect\s+to|anticipate|do\s+anticipate|intend\s+to|would\s+like\s+to|want\s+to|aim\s+to|"
             r"(?:am|are|'m|’m|'re|’re)\s+(?:\w+ly\s+)?(?:planning|going|hoping|excited|happy)\s+to)\s+(?:be\s+)?(?:\w+ly\s+)?(?:recruit(?:ing)?(?:\s+for)?|hir(?:e|ing)|accept(?:ing)?|tak(?:e|ing)(?:\s+on)?|admit(?:ting)?|look(?:ing)?\s+for|seek(?:ing)?|supervis(?:e|ing)|advis(?:e|ing)(?:\s+and\s+recruiting\s+for)?)"
             r"|(?<![\w-])(?:\w+ly\s+)?(?:recruit|seek|welcome)\b"
             r"|(?:have|has)\s+(?:\w+\s+){0,2}(?:openings?|positions?|spots?|slots?|vacanc(?:y|ies)|room|funding|space)\s+(?:available\s+)?for"
             r"|(?:am|are|'m|’m)\s+(?:open\s+to\s+(?:taking|accepting|advising|supervising|recruiting)|happy\s+to\s+(?:take|accept|work\s+with|supervise|advise))\s+(?:\w+\s+)?)")
QTY = r"(?:\w+['’]s|one|two|three|four|several|multiple|a\s+few|some|1|2|3|4|\d\s*[-–]\s*\d|a|an|at\s+most|up\s+to|roughly|about|\w+ly|\w+-\w+|\w+)"
YES_SVO = re.compile(rf"(?i){FIRST}\s*(?:\w+\s+){{0,2}}?{RECRUIT_V}\s*(?:{QTY}[\s,-]+){{0,5}}?{OBJ_ANY}")
YES_PHRASE = [
    re.compile(rf"(?i)\b(?:openings?|positions?|vacanc(?:y|ies)|spots?)\s+(?:are\s+|is\s+)?(?:available\s+|open\s+)?for\s+(?:\w+\s+){{0,3}}{OBJ_PHD}"),
    re.compile(rf"(?i)\b(?:fully[- ]funded|funded|open|multiple|several|available)\s+(?:\w+\s+){{0,2}}?{PHD}\s+(?:student\s+|research\s+|assistant\s+)?(?:positions?|openings?|assistantships?|spots?)"),
    re.compile(rf"(?i){PHD}\s+(?:student\s+|research\s+)?(?:positions?|openings?|assistantships?)\s+(?:are\s+|is\s+)?(?:\w+\s+)?(?:available|open)\b"),
    # 标题式 "Recruiting PhD students for Fall 2027!"、"Hiring: ..."、"The XYZ Lab is looking for ..."
    re.compile(rf"(?i)(?:^[^A-Za-z]*|:\s*|^(?:now|currently)\s+|\b(?:lab|group|team|center|centre)\s+(?:is|are)\s+(?:\w+ly\s+|also\s+|always\s+){{0,2}}|\b(?:\w+@\w+)\s+(?:is|are)\s+(?:\w+ly\s+|also\s+|always\s+){{0,2}})"
               rf"(?<![\w-])(?:recruiting|seeking|looking\s+for|hiring|accepting)\s*:?\s+(?:\w+[\s-]+){{0,4}}?{OBJ_PHD}"),
    re.compile(r"(?i)\b(?:apply|applying)\s+(?:to|for|through)\s+(?:the\s+)?(?:\w+\s+){0,6}?(?:ph\.?\s?d\.?|doctoral|graduate)\s+program\b.{0,80}?\b(?:mention|list|name|indicate|select|tag)\s+(?:me\b|my\s+name|my\s+lab|my\s+group)"),
    re.compile(r"(?i)\b(?:mention|list|indicate|tag)\s+(?:me|my\s+name)\b.{0,40}?\b(?:ph\.?\s?d\.?\s+)?(?:application|statement|system)"),
    re.compile(rf"(?i)\b(?:join|joining)\s+(?:my|our)\s+(?:group|lab|team|research group)\b.{{0,40}}?\bas\s+an?\s+(?:{PHD}\s+student|ph\.?\s?d\b(?!\s+(?:holder|graduate)))"),
]

NEG = r"(?:not|no\s+longer|unable\s+to|n['’]t|never)"
NO_VERB = (r"(?:recruit(?:ing)?|tak(?:e|ing)(?:\s+on)?|accept(?:ing)?|admit(?:ting)?|hir(?:e|ing)|look(?:ing)?\s+for|seek(?:ing)?|supervis(?:e|ing)|advis(?:e|ing)|"
           r"consider(?:ing)?|have\s+(?:\w+\s+)?(?:openings?|funding|positions?|space|room|capacity)\s+(?:available\s+)?(?:for|to\s+(?:hire|take|support|fund)))")
NO_OBJ = rf"(?:(?:any|new|additional|more|further|incoming|prospective|external|outside|a|an|on|direct|my|own|other)\s+){{0,3}}?(?:\w+\s+(?:or|and)\s+)?(?:{OBJ_ANY}|(?<![\w-])ph\.?\s?d\.?s?(?![a-z])|advisees|students?)"
NO_PATTERNS = [
    re.compile(rf"(?i){FIRST}\s+(?:\w+\s+){{0,2}}?(?:am|are|is|will|'m|’m|'re|’re|do|does|can|have|has|plan|currently)?\s*{NEG}\s+(?:be\s+|currently\s+|actively\s+|planning\s+to\s+|plan\s+to\s+|anticipate\s+|expect\s+to\s+|intend\s+to\s+|going\s+to\s+|able\s+to\s+|looking\s+to\s+|\w+ly\s+|taking\s+on\s+){{0,3}}"
               rf"{NO_VERB}\s+{NO_OBJ}"),
    re.compile(rf"(?i)\b(?:no|not\s+have\s+any|don['’]t\s+have\s+any|do\s+not\s+have\s+any)\s+(?:\w+\s+){{0,2}}?(?:openings?|positions?|spots?|vacanc(?:y|ies)|funding|space|room)\s+(?:available\s+)?(?:for\s+|to\s+(?:hire|take|support|fund)\s+)?(?:\w+\s+){{0,3}}?(?:{OBJ_ANY}|ph\.?d)"),
    re.compile(rf"(?i)(?:^|\b(?:currently|presently|now|time)[,:]?\s+|[(:]\s*|\bi\s+am\s+|\band\s+)(?:not|no\s+longer)\s+(?:currently\s+|actively\s+)?(?:recruiting|taking|accepting|admitting|looking\s+for|seeking)\s+{NO_OBJ}"),
    re.compile(r"(?i)\b(?:my|our|the)\s+(?:\w+\s+)?(?:group|lab|team)\s+is\s+(?:currently\s+)?(?:full|at\s+(?:full\s+)?capacity)\b|\b(?:i\s+am|i['’]m)\s+(?:currently\s+)?at\s+(?:full\s+)?capacity\s+(?:for\s+(?:hiring|taking|accepting|recruiting|new|advising|supervising)|and\s+(?:not|cannot|can['’]t|won['’]t|will\s+not))"),
    re.compile(rf"(?i)\bsabbatical\b.{{0,80}}\b{NEG}\s+(?:\w+\s+){{0,2}}(?:recruit|tak|accept|admit|look)"),
    re.compile(rf"(?i)\bno\s+(?:new\s+)?(?:{PHD}\s+)?students\s+(?:will\s+be\s+)?(?:accepted|admitted|taken|recruited)"),
    # 没有宾语的："New students: I am unfortunately not recruiting at the moment."（classify 里要求句子提到 student / PhD）
    re.compile(r"(?i)\b(?:i\s+am|i['’]m|we\s+are|we['’]re)\s+(?:\w+ly\s+)?(?:not|no\s+longer)\s+(?:\w+ly\s+)?recruiting"
               r"(?=\s*(?:at\s+(?:the|this)\s+(?:moment|time)|this\s+(?:year|cycle)|right\s+now|now|currently|for\s+(?:fall|spring|the)|in\s+(?:fall|spring|20)|[.!;,)]|$))"),
]
# "I do not accept students who haven't taken my class"、"I don't accept students every year"、"outside of this annual cycle"
NO_COND = re.compile(r"(?i)\b(?:always|typically|usually|generally|normally|gifts?|respond|reply|answer|visitors?|who\s+are\s+not)\b")
NO_COND_AFTER = re.compile(r"(?i)\s*,?\s*(?:who|that|unless|outside|except|without|every\s+year|each\s+year|via\s+email|by\s+email|directly|"
                           r"based\s+on|through\s+email|before|from\s+(?:my\s+)?(?:class|course|outside)|in\s+the\s+cs\s+program|not\s+at\b|"
                           r"as\s+(?:graders?|tas?|teaching)|for\s+(?:summer\s+)?(?:internships?|research\s+credit|directed|independent)|to\s+(?:my|our)\s+(?:class|course)|"
                           r"in\s+(?:those|these|that|this|such|the\s+following|other)\s+(?:areas?|topics?|fields?)|on\s+(?:topics?|projects?|this|that|these)|"
                           r"in\s+(?:areas?|topics?)\s+(?:of|outside|unrelated)|to\s+work\s+on|working\s+on|who\s+work|"
                           # "I cannot admit students on my own"、"I don't admit students, the University does"、
                           # "not recruiting students wishing to pursue a Ph.D. in Seismology"
                           # "we are not actively recruiting students through these programs"（但通过别的项目在招）
                           r"on\s+my\s+own|myself|to\s+(?:the|our)\s+(?:\w+\s+)?program|wishing|wanting|interested\s+in|"
                           r"through\s+(?:these|those|this|that|other|the\s+following)\b|"
                           r"(?:the|our)\s+(?:university|department|school|admissions?|committee)\b)")
# "If you are a NYU student looking for research: I am not taking on direct advisees" -> 只针对本校学生，不算
NO_SCOPE_HEAD = re.compile(r"(?i)\bif\s+you\s+are\s+(?:a|an)\s+(?:current\s+)?[\w@.-]+\s+(?:undergrad\w*|student|master'?s)")

# 宾语只是本科 / 硕士 / 实习 / 博后 / 访问学者，不算 PhD
NON_PHD = re.compile(r"(?i)\b(?:undergrad\w*|hourly|research\s+credits?|for\s+credit|independent\s+stud\w+|master['’]?s|honou?rs\s+(?:year|thesis|projects?|program)|masters|m\.?s\.?\s+students|ms\s+students|meng|reu|interns?\b|internships?|"
                     r"high[- ]school|summer\s+(?:students|programs?|camps?|schools?)|visiting\s+students|post-?docs?\w*|post-?doctoral|research\s+(?:scientists?|engineers?|assistants?|staff)|"
                     r"rotation\s+students|capstone|honou?rs?\s+students|bs\s+students|b\.?s\.?\s+students)")
NON_PHD_STU = re.compile(r"(?i)\b(?:undergrad\w*|hourly|research\s+credits?|for\s+credit|independent\s+stud\w+|master['’]?s|honou?rs\s+(?:year|thesis|projects?|program)|masters|m\.?s\.?\s+students|ms\s+students|meng|reu|"
                         r"interns?\b|internships?|high[- ]school|summer\s+(?:students|programs?|camps?|schools?)|rotation\s+students|capstone|honou?rs?\s+students|bs\s+students|b\.?s\.?\s+students)")
# 宾语不是要招的学生
BAD_OBJ = re.compile(r"(?i)\b(?:participants?|subjects|volunteers|collaborators?|collaborations?|sponsors?|partners?|reviewers?|teaching assistants?|tas\b|graders?|"
                     r"faculty\s+(?:candidates?|members?|positions?)|tenure|job|jobs|position\s+as|internship\s+opportunit|a\s+postdoc\s+position|"
                     r"an?\s+(?:faculty|academic|industry)\s+position|weekly\s+meetings?|seminars?|reading\s+group|talks?\b|events?\b)")
# 条件 / 疑问 / 讲别人："if you are looking for"、"students who are looking for advisors"、"Are you looking for"
OTHER = re.compile(r"(?i)\b(?:if\s+you|you\s+are|you're|you’re|are\s+you|students?\s+(?:who\s+are|that\s+are)\s+looking|for\s+students\s+looking|they\s+are|"
                   r"looking\s+for\s+(?:an?\s+)?(?:advisors?|supervisors?|mentors?)|"
                   r"department\s+is|university\s+is|school\s+is|college\s+is|program\s+is|institute\s+is)")
APPLY_BOILER = re.compile(r"(?i)(?:should|must|need\s+to)\s+apply\s+(?:directly\s+)?(?:through|to|via)\s+(?:the\s+)?(?:\w+\s+){0,4}(?:admissions?|program)")


PAID_RA = re.compile(r"(?i)\bpaid\b|\bhourly\b|\bper\s+semester\b|\bat\s+least\s+(?:one|a)\s+(?:full\s+)?semester|\bfor\s+(?:paid\s+)?research\s+work\b|"
                     r"\bresearch\s+assistant(?:s|ships?)?\b(?!.{0,30}\bph\.?\s?d)|\bRAs?\b(?![^.]{0,40}ph\.?\s?d)|\binternships?\b|\binterns\b")
# 明确对 PhD 申请人说的（有这句时 RA / 实习字样不影响）
PHD_APPLICANT = re.compile(r"(?i)\b(?:recruit\w*|hir\w*|looking\s+for|seeking|accept\w*|admit\w*|tak\w+)\s+(?:\w+\s+){0,4}?(?:new\s+)?(?:ph\.?\s?d\.?|doctoral)\s+(?:students?|applicants?|candidates?)"
                           r"|(?:ph\.?\s?d\.?|doctoral)\s+(?:applicants?|positions?|admissions?|program)|prospective\s+(?:ph\.?\s?d|doctoral)|fully[- ]funded")
PHD_WORD = re.compile(r'(?i)(?<![\w-])(?:ph\.?\s?d|doctoral|doctorate|graduate|grad\s+students?|d\.?\s?phil)')
RECRUITISH = re.compile(r'(?i)prospective|students?|ph\.?\s?d|recruit|opening|position|join|admission|hiring|apply|doctoral|graduate|applicants?')
# 同一段里讲去别的学校 / 在别的学校招
INST_MOVE = re.compile(r"(?:\b(?i:moving|move|joining|join(?:ed)?|relocat\w+|transition\w*|heading)\s+to\s+|\b(?i:new\s+(?:position|role)\s+at)\s+)(?:the\s+)?"
                       r"((?:[A-Z][\w&.'’-]*|of|at|and)(?:\s+(?:[A-Z][\w&.'’-]*|of|and)){0,6})")
INST_DPHIL = re.compile(r'\b(Oxford)\b[^.]{0,60}\bD\.?Phil|\bD\.?Phil\b[^.]{0,60}\b(Oxford)\b')
INST_STOP = {'university', 'of', 'the', 'at', 'and', 'institute', 'technology', 'college', 'school', 'state', 'department', 'dept', 'cs', 'cse',
             'computer', 'science', 'engineering', 'in', 'january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
             'september', 'october', 'november', 'december', 'fall', 'spring', 'summer', 'winter'}


def other_inst(para, uni):
    """段落里提到的、不是本人学校的学校名（"moving to HKUST CSE in January 2027"）。"""
    own = {w for w in re.findall(r'[a-z]+', deaccent(uni)) if w not in INST_STOP}
    for m in INST_DPHIL.finditer(para):
        if 'oxford' not in own:
            return 'Oxford'
    for m in INST_MOVE.finditer(para):
        name = re.sub(r'\s+(?:of|and|at|in)$', '', m.group(1).strip(" .,'’"))
        name = re.split(r'\s+(?:in|on|this|next|starting|from)\b', name)[0]
        words = {w for w in re.findall(r'[a-z]+', deaccent(name)) if w not in INST_STOP}
        if words and not words & own and len(name) >= 3 and not re.match(r'(?:My|Our|The|This|I|We)\b', name):
            return name[:60]
    return None


# 先用便宜的关键词过一遍，绝大多数句子不用跑上面的大正则
PREFILTER = re.compile(r'(?i)student|ph\.?\s?d|doctoral|advisee|full|capacity|sabbatical|mention')


def is_phd_obj(s):
    return re.search(rf'(?i){OBJ_PHD}', s) is not None


def classify(sent):
    """返回 (status, strength, match) 或 None。"""
    s = sent
    if len(s) < 15 or len(s) > 1500 or not PREFILTER.search(s):
        return None
    # ---- no
    for p in NO_PATTERNS:
        for m in p.finditer(s):
            seg = m.group(0)
            after = s[m.end():m.end() + 60]
            if NON_PHD.search(seg + ' ' + after[:25]) and not is_phd_obj(seg):
                continue
            if NO_COND.search(seg) or NO_COND_AFTER.match(after):
                continue
            if BAD_OBJ.search(seg + ' ' + after[:30]):
                continue
            if re.search(r'(?i)\b(?:if|whether|unless)\s+(?:\w+\s+){0,4}$', s[max(0, m.start() - 40):m.start()]):
                continue
            if NO_SCOPE_HEAD.search(s[:m.start()]):
                continue
            if p is NO_PATTERNS[-1] and not re.search(r'(?i)student|ph\.?\s?d|doctoral', s):
                continue
            return ('no', 3 if is_phd_obj(seg) else 2, m)
    # ---- yes
    cands = []
    for p in [YES_SVO] + YES_PHRASE:
        cands += list(p.finditer(s))
    for m in sorted(cands, key=lambda m: m.start()):
        seg = m.group(0)
        head = s[max(0, m.start() - 40):m.start()]
        tail = s[m.start():min(len(s), m.end() + 80)]
        # 只招自带经费 / 有个人 fellowship 的："I am only accepting self-funded students"、"can only recruit students who hold a fellowship"
        if re.search(r'(?i)\bonly\b', s[max(0, m.start() - 15):m.end()]) or \
                re.search(r'(?i)self[- ]funded|own\s+funding|bring\s+(?:their|your)\s+own|who\s+(?:hold|have|bring)\s+(?:an?\s+)?(?:\w+\s+)?fellowships?', tail):
            continue
        # "Welcome Amir to join our team as a PhD student" 是新生入组的新闻
        if re.search(r'(?i)\bwelcom(?:e|ing)\s+[A-Z]', s[:m.start() + 1]) and not re.search(r'(?i)\bwelcome\s+(?:all|any|new|motivated|applications)', s):
            continue
        # yes 里不能带否定："I do not plan to hire"、"I do not anticipate taking"
        # "I'm excited to advise the following talented PhD students:" 是在列现有学生
        if re.match(r'(?i)\s*(?:in\s+my\s+(?:group|lab)\s*)?:\s*$', s[m.end():]) or re.search(r'(?i)\bthe\s+following\b', seg):
            continue
        if re.search(r"(?i)\b(?:not|no\s+longer|never|unable|n['’]t)\b", seg):
            continue
        # "We welcome our incoming PhD cohort"（新闻）、"We welcome visiting doctoral researchers"（访问学者）
        if re.search(r'(?i)\bwelcom(?:e|ing)\s+(?:our|the)\b|\bvisiting\s+(?:\w+\s+)?(?:doctoral|ph\.?\s?d|graduate)', seg):
            continue
        # "it is unlikely ... but not impossible that I will accept"；"I prefer to recruit students from my classes"、"I try to recruit PhD students who..."
        if re.search(r'(?i)\b(?:unlikely|not\s+impossible|doubtful)\b', s[:m.end() + 40]) or re.search(r'(?i)\b(?:prefer|try|tend|attempt)\s+to\b', seg):
            continue
        # "Based on how funding is going, I may recruit fewer students or more students"
        if re.search(r'(?i)\b(?:may|might)\s+(?:\w+\s+)?(?:recruit|admit|take|accept|hire)\s+(?:fewer|more)\b', seg):
            continue
        # "We're always looking for Stanford students who are interested in research"、"recruiting UF students"：本校学生做科研
        um = re.search(r'\b([A-Z][\w&-]*)\s+(?:students|undergraduates)$', seg)
        if um and not is_phd_obj(seg) and not re.fullmatch(ADJ, um.group(1).lower()):
            continue
        # 宾语就是 "master’s students"（后面不是 "..., and PhD students" 这种列举）
        if re.search(r"(?i)\b(?:undergrad\w*|master['’]?s|masters|m\.?s\.?|b\.?s\.?|meng|honou?rs)\s+students?$", seg) and not is_phd_obj(seg) and \
                not re.match(r"(?i)\s*(?:,|and|or|&|/)\s*(?:\w+\s+){0,3}?(?:ph\.?\s?d|doctoral)", s[m.end():m.end() + 40]):
            continue
        if re.search(r'(?i)\bwelcome\b', seg) and re.search(r'(?i)\b(?:questions?|e-?mails?|inquir\w*|enquir\w*|from|visitors?|comments?|feedback)\b', seg):
            continue
        if OTHER.search(seg) or re.search(r'(?i)\b(?:if|whether|when|whom?|which|unless)\s+(?:\w+\s+){0,3}$', head):
            continue
        if re.search(r'(?i)\b(?:are|do)\s+you\s+(?:\w+\s+){0,2}$', head) or re.match(r'[^.!?]{0,30}\?', s[m.end():]):
            continue
        # 列举式宾语 "undergraduates, Master's students, Ph.D. students, and post-docs"：往后多看一点
        if NON_PHD.search(seg) and not is_phd_obj(re.split(r'[;!]\s|(?<=[a-z]{2})\.\s', s[m.start():m.end() + 30])[0]):
            continue
        if BAD_OBJ.search(tail):
            continue
        if not is_phd_obj(seg):
            # 泛指 "students"：句子里有本科 / 硕士 / 实习限定而没有 PhD 字样 -> 不算
            # （"students and postdocs" 里的 students 多半就是 PhD，所以这里不看博后）
            if NON_PHD_STU.search(s) and not is_phd_obj(s):
                continue
            if re.search(r'(?i)\bwelcome\b', seg) and not re.match(r'(?i)[^.]{0,40}?\bto\s+(?:join\s+(?:my|our)\s+(?:\w+\s+)?(?:lab|group|team)\b|work\s+with\s+(?:me|us)|apply)', s[m.end():]):
                continue
        if APPLY_BOILER.search(s) and not re.search(r"(?i)\b(?:i|we|my|our|i'm)\b", seg):
            continue
        # 招有薪 RA / 按学期的科研助理（"seeking students at all levels ... for paid research work ... at least one semester"）
        if PAID_RA.search(s) and not PHD_APPLICANT.search(s):
            continue
        return ('yes', 3 if is_phd_obj(s) else 2, m)
    return None


# ---------------------------------------------------------------- 主流程

def deaccent(s):
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower()


def surname(name):
    toks = [t for t in re.findall(r'[^\W\d_][\w-]*', name) if t.lower().strip('.') not in ('jr', 'sr', 'ii', 'iii', 'iv')]
    return deaccent(toks[-1]) if toks else ''


_LAB_FREQ = None


def lab_url_freq():
    """实验室网站 / 实验室页被几位教授的缓存共用（>=2：多位 PI 的研究组、院系中心页）。"""
    global _LAB_FREQ
    if _LAB_FREQ is None:
        import glob
        import gzip
        from fetch_recruit_pages import norm
        c = Counter()
        for fn in glob.glob(os.path.join(ROOT, 'data/cache/lab_pages', '*.json.gz')):
            try:
                d = json.load(gzip.open(fn, 'rt', encoding='utf-8'))
            except Exception:
                continue
            if d.get('lab'):
                c[norm(d['lab']['url'])] += 1
        for fn in glob.glob(os.path.join(ROOT, 'data/cache/recruit_pages', '*.json')):
            try:
                d = json.load(open(fn, encoding='utf-8'))
            except Exception:
                continue
            for p in d.get('pages') or []:
                if p.get('kind') == 'lab' and p.get('url'):
                    c[norm(p['url'])] += 1
        _LAB_FREQ = c
    return _LAB_FREQ


def html_text(html):
    from fetch_pages import TextAndLinks
    tp = TextAndLinks()
    try:
        tp.feed(html)
    except Exception:
        pass
    text = re.sub(r'[ \t\r\f\v]+', ' ', ''.join(tp.out))
    return re.sub(r'\n\s*\n+', '\n', text)


def load_pages(pid, name='', homepage=''):
    """返回 ([(url, text, mtime, meta)], 主页及子页的个数)。meta: extra（另抓的页面）、shared（多人共用的页面）。"""
    from fetch_recruit_pages import norm
    out = []
    sur = surname(name)
    has_name = lambda t: bool(sur) and re.search(r'\b' + re.escape(sur) + r'\b', deaccent(t)) is not None  # noqa: E731
    pp = os.path.join(ROOT, 'data/cache/people_pages', pid + '.json')
    if os.path.exists(pp):
        mt = os.path.getmtime(pp)
        try:
            d = json.load(open(pp, encoding='utf-8'))
        except Exception:
            d = {'pages': []}
        seen = set()
        pages = d.get('pages') or []
        prefixes = [home_prefix(u) for u in {homepage, pages[0].get('url') if pages else ''} if u]
        for i, p in enumerate(pages):
            t = p.get('text') or ''
            if not t.strip() or t[:3000].count('\x00') > 50 or t in seen:
                continue        # 空页 / 没解码的 UTF-16 垃圾页 / http 和 https 重复的同一页
            # 子页要在本人那一块网站下面：院系网站上的 /theory/ 研究组页、中心页不算本人的
            if i > 0 and prefixes and not any(under(p.get('url') or '', pre) for pre in prefixes):
                continue
            seen.add(t)
            out.append((p.get('url') or '', t[:400000], mt, {}))
    if not out:
        hp = os.path.join(ROOT, 'data/cache/pages', pid + '_home.txt')
        if os.path.exists(hp):
            txt = open(hp, encoding='utf-8', errors='replace').read()
            url = ''
            if txt.startswith('#URL'):
                first, _, txt = txt.partition('\n')
                url = first[4:].strip()
            if txt.strip():
                out.append((url, txt, os.path.getmtime(hp), {}))
    n_home = len(out)
    freq = lab_url_freq()
    # fetch_recruit_pages.py 另抓的 Prospective / Join / Openings / FAQ / Contact / 实验室 / 个人主页，排在主页和子页后面
    rp = os.path.join(ROOT, 'data/cache/recruit_pages', pid + '.json')
    if os.path.exists(rp):
        mt = os.path.getmtime(rp)
        try:
            d = json.load(open(rp, encoding='utf-8'))
        except Exception:
            d = {'pages': []}
        seen = {t for _, t, _, _ in out}
        prefix = home_prefix(out[0][0]) if out and out[0][0] else None
        parent_ok, parent_shared = False, False
        for p in d.get('pages') or []:
            t = p.get('text') or ''
            kind, url = p.get('kind') or 'sub', p.get('url') or ''
            # 是不是本人的页面：本人主页那一块下面的子页直接算；实验室 / 个人主页 / 别的主机上的页面要求页面里出现本人的姓
            # （院系简介页旁边列着别人的 "Personal Website"；实验室链接可能是同事的实验室）
            if kind == 'sub2':
                ok, shared = parent_ok or has_name(t), parent_shared
            elif kind == 'sub' and prefix and under(url, prefix):
                ok, shared = True, False
            else:
                ok, shared = has_name(t), kind == 'lab' and freq[norm(url)] >= 2
            if kind != 'sub2':
                parent_ok, parent_shared = ok, shared
            if not ok or not t.strip() or t[:3000].count('\x00') > 50 or t in seen:
                continue
            seen.add(t)
            out.append((url, t[:400000], mt, {'extra': True, 'shared': shared}))
    # fetch_lab_pages.py 抓的本人实验室网站（首页 / People / Join）
    lp = os.path.join(ROOT, 'data/cache/lab_pages', pid + '.json.gz')
    if os.path.exists(lp):
        import gzip
        try:
            d = json.load(gzip.open(lp, 'rt', encoding='utf-8'))
        except Exception:
            d = {}
        if d.get('lab') and d['lab'].get('has_name'):
            mt = os.path.getmtime(lp)
            shared = freq[norm(d['lab']['url'])] >= 2
            seen = {t[:3000] for _, t, _, _ in out}
            for p in d.get('pages') or []:
                if not p.get('html'):
                    continue
                t = html_text(p['html'])
                if t.strip() and t[:3000] not in seen:
                    seen.add(t[:3000])
                    out.append((p.get('final') or p['url'], t[:400000], mt, {'extra': True, 'shared': shared, 'lab': True}))
    # fetch_lab_pages.py --follow：主页上 "THIS" / "details" / "Join" / "Positions" 链接再抓的一层
    fp_ = os.path.join(ROOT, 'data/cache/recruit_follow', pid + '.json')
    if os.path.exists(fp_):
        mt = os.path.getmtime(fp_)
        try:
            d = json.load(open(fp_, encoding='utf-8'))
        except Exception:
            d = {'pages': []}
        prefix = home_prefix(out[0][0]) if out and out[0][0] else None
        seen = {t[:3000] for _, t, _, _ in out}
        for p in d.get('pages') or []:
            t, url = p.get('text') or '', p.get('final') or p.get('url') or ''
            if not t.strip() or t[:3000] in seen or not ((prefix and under(url, prefix)) or p.get('own') or has_name(t)):
                continue
            seen.add(t[:3000])
            out.append((url, t[:400000], mt, {'extra': True, 'shared': False, 'follow': True}))
    return out, n_home


def trim_prefix(s, start):
    """句子开头粘着导航菜单 / 标题（"Home Publications Prospective Students I am looking..."）时去掉，返回新的起点。"""
    pre = s[:start]
    ends = [m.end() for m in re.finditer(r'(?<=[a-z0-9)\]][.!?])\s+(?=[A-Z"(\[“])', pre)]   # 不在 "Ph.D. " 处断
    base = ends[-1] if ends else 0
    seg = pre[base:]

    def capsy(ws):
        return [w for w in ws if w[:1].isupper() or not w[:1].isalpha()]

    k = seg.rfind(':')
    if k >= 0 and len(seg) - k <= 4:
        # 紧挨着的 "Prospective students:" 标签保留，标签前面是菜单就去掉
        head = seg[:k].split()
        label = ' '.join(head[-4:])
        ls = seg.rfind(label, 0, k + 1) if head else 0
        noise = seg[:ls].split()
        if len(noise) >= 2 and len(capsy(noise)) / len(noise) >= 0.6:
            return base + ls
        return base
    words = seg.split()
    if len(words) >= 3 and s[start:start + 1].isupper() and len(capsy(words[-2:])) == 2 and not seg.rstrip().endswith(',') \
            and len(capsy(words)) / len(words) >= 0.6:
        return start
    return base


def clip(s, m):
    st0 = trim_prefix(s, m.start())
    s2 = s[st0:]
    a, b = m.start() - st0, m.end() - st0
    s2 = s2.rstrip()
    if len(s2) <= MAXQ:
        return s2
    room = max(0, MAXQ - 2 - (b - a))
    st = max(0, a - room // 4)
    en = min(len(s2), st + MAXQ - 2)
    st = max(0, en - (MAXQ - 2))
    q = s2[st:en].strip()
    return (('…' if st > 0 else '') + q + ('…' if en < len(s2) else ''))[:MAXQ]


SUBJ_NAME = re.compile(r'(?i:prof\.?|professor|dr\.?)\s+([A-Z][\w-]+)'
                       r'|^(?!(?:The|This|That|Our|My|He|She|We|It|Lab|Group|Team|Now|Currently)\b)([A-Z][a-z]+)\s+(?:is|are)\b')


def para_sentences(para):
    prev = ''
    for x in SENT_SPLIT.split(para):
        x = x.strip(' -*•')
        if x:
            yield x, prev
            prev = x


def page_dates(paras):
    """页面上最新的日期（新闻、"Fall 2026" 课程等；不算版权年份），以及页脚式 "Last updated" 的日期。"""
    newest, upd = None, []
    for para in paras:
        if re.search(r'(?i)©|copyright|all\s+rights', para):
            continue
        u = upd_stamps(para)
        upd += u
        rest = stamp_ym(UPD.sub(' ', para)) if u else stamp_ym(para)
        if rest:
            newest = max(newest or (0, 0), max(rest))
    return newest, upd


def find(pid, name='', uni='', homepage=''):
    found = []
    pages, n_home = load_pages(pid, name, homepage)
    name_toks = {w.lower() for w in re.findall(r'[A-Za-z][\w-]+', name)}
    sur = surname(name)
    for pi, (url, text, mt, meta) in enumerate(pages):
        extra = pi >= n_home            # 另抓的页面（实验室网站可能有好几位 PI）
        paras = paragraphs(text)
        newest, page_upd = page_dates(paras)
        seen = set()
        for qi, para in enumerate(paras):
            head = ''
            for j in range(qi - 1, max(-1, qi - 4), -1):
                if len(paras[j]) <= 100:
                    head = paras[j]
                    break
            sents = list(para_sentences(para))
            for sent, prev in sents:
                if sent in seen:
                    continue
                seen.add(sent)
                r = classify(sent)
                if not r:
                    continue
                st, strength, m = r
                if extra:
                    # 实验室页上 "Prof. Smith is recruiting" 必须是本人
                    sm = SUBJ_NAME.match(m.group(0))
                    if sm and (sm.group(1) or sm.group(2)).lower() not in name_toks:
                        continue
                if meta.get('shared') and not (sur and re.search(r'\b' + re.escape(sur) + r'\b', deaccent(sent))):
                    continue        # 研究组 / 中心 / 多人共用的实验室页：句子里要点本人的名字
                win = sent[max(0, m.start() - 90):m.end() + 160]
                y, amb = target_year(win, newest)
                yf = 'ambig' if amb else None
                if y is None and head and RECRUITISH.search(head):
                    y, amb = target_year(head, newest)
                    yf = ('ambig' if amb else 'heading') if y else None
                if y is None:
                    for s2, _ in sents:
                        if s2 is not sent and RECRUITISH.search(s2) and not stamp_ym(s2, seasons=False):
                            y2, amb2 = target_year(s2, newest)
                            if y2:
                                y, yf = y2, ('ambig' if amb2 else 'para')
                                break
                ctx = (prev[-60:] + ' ' if len(prev) < 80 else '') + sent[:max(0, m.start()) + 20]
                stamps = [x for x in stamp_ym(ctx, seasons=False)]
                if y is None and stamps and max(stamps) < FRESH_CUT and not UPD.search(ctx):
                    continue        # (入学年 − 1) 年 8 月以前的新闻条目
                stale = False
                if y is not None and y < THIS_YEAR:
                    if st != 'yes' or y < THIS_YEAR - 4:
                        continue    # 目标年份已过
                    stale = True    # 往年的 PhD 招生：只用来说明不带年份的 yes 已过时
                if y is None:
                    # "recruiting for 2021" 一类没被识别成目标年份的旧年份
                    olds = [int(x) for x in ANY_YEAR.findall(win)]
                    if olds and max(olds) < THIS_YEAR - 1 and re.search(r'(?i)\b(?:for|in|starting|admission)\s+(?:the\s+)?20\d\d', win):
                        if st != 'yes' or max(olds) < THIS_YEAR - 4:
                            continue
                        y, yf, stale = max(olds), 'sentence', True
                old = False
                if y is None and st == 'yes':
                    # 旁边的 "Updated on 05/02/2025"；页脚的 "Last updated" 只在页面上没有更新的日期时算
                    near = upd_stamps(' '.join([head, para] + [paras[j] for j in (qi - 1, qi + 1) if 0 <= j < len(paras) and len(paras[j]) <= 120]))
                    up, how = (max(near), 'stamp') if near else (None, None)
                    if up is None and page_upd and (newest is None or newest <= max(page_upd)):
                        up, how = max(page_upd), 'footer'
                    if up and up < FRESH_CUT:
                        y, yf = (up[0] + 1 if up[1] >= 8 else up[0]), how
                    elif newest and newest < (THIS_YEAR, 1):
                        old = True  # 页面上最新的日期早于 2026：可能过时
                phd = bool(PHD_WORD.search(sent) or (head and PHD_WORD.search(head)))
                inst = other_inst(' '.join([head, para]), uni) if uni else None
                found.append(dict(s=st, strength=strength, y=y, yf=yf, sent=sent, m=m, src=url, page=pi, mt=mt, extra=extra,
                                  stale=stale, phd=phd, old=old, inst=inst))
    return found


def pick(found):
    def key(f):
        return ((f['y'] or 0), 1 if f['s'] == 'no' else 0, f['strength'], -f['page'])
    fresh = [f for f in found if not f['stale']]
    if not fresh:
        return None
    b = max(fresh, key=key)
    if b['s'] == 'yes' and b['y'] is None:
        # 主页上的 yes 不带年份，本人页面（实验室 / Join / 链接页）上有往年的 PhD 招生：用往年那句（网站上显示为可能过时）
        # 主页那句只泛指 students 时，往年那句也可以只写 openings / students（"multiple openings (fully funded) starting from Fall 2024"）
        older = [f for f in found if f['stale'] and f['s'] == 'yes' and (f['phd'] or not b['phd'])]
        if older:
            b = dict(max(older, key=lambda f: (f['y'], f['strength'], -f['page'])), yf='older')
    return b


def summarize(res):
    c = Counter()
    for v in res.values():
        if v['s'] != 'yes':
            c['no'] += 1
            continue
        if v.get('y'):
            c['yes_dated_current' if v['y'] >= ADMIT else 'yes_dated_stale'] += 1
        else:
            phd = v['phd'] if 'phd' in v else bool(PHD_WORD.search(v.get('q', '')))
            c['yes_undated_phd' if phd else 'yes_undated_generic'] += 1
    return dict(sorted(c.items()))


def main():
    args = sys.argv[1:]
    debug = '--debug' in args
    sample = int(args[args.index('--sample') + 1]) if '--sample' in args else 0
    profs = load_profs()
    res = {}
    out = os.path.join(ROOT, 'data/enrich/recruiting.json')
    before = json.load(open(out, encoding='utf-8')) if os.path.exists(out) else {}
    for p in profs:
        f = find(p['id'], p['name'], p['uni'], p['homepage'])
        if not f:
            continue
        b = pick(f)
        if b is None:
            continue
        res[p['id']] = dict(s=b['s'], q=clip(b['sent'], b['m']), y=b['y'], src=b['src'],
                            at=time.strftime('%Y-%m', time.localtime(b['mt'])), phd=b['phd'])
        for k in ('old', 'inst', 'yf'):
            if b.get(k):
                res[p['id']][k] = b[k]
        if debug:
            print(p['id'], p['name'], '|', b['s'], b['y'])
            for x in f:
                print('    ', x['s'], x['y'], '|', clip(x['sent'], x['m']))
    os.makedirs(os.path.join(ROOT, 'data/enrich'), exist_ok=True)
    print('之前:', summarize(before))
    print('之后:', summarize(res))
    print('年份来源:', dict(Counter(v.get('yf', 'sentence' if v.get('y') else '-') for v in res.values())),
          'old:', sum(1 for v in res.values() if v.get('old')), 'inst:', sum(1 for v in res.values() if v.get('inst')))
    with open(out, 'w', encoding='utf-8') as fo:
        json.dump(res, fo, ensure_ascii=False, indent=0, sort_keys=True)
    c = Counter(v['s'] for v in res.values())
    cy = Counter((v['s'], v['y']) for v in res.values())
    print(f'{len(profs)} 位教授，{len(res)} 位有招生表态：{dict(c)}')
    print('按年份:', sorted(cy.items(), key=lambda t: (t[0][0], t[0][1] or 0)))
    print('写入', out)
    if sample:
        names = {p['id']: p['name'] for p in profs}
        rnd = random.Random(int(os.environ.get('SEED', '1')))
        for st in ('yes', 'no'):
            ids = sorted(k for k, v in res.items() if v['s'] == st)
            rnd.shuffle(ids)
            print(f'\n===== {st} 抽样 {min(sample, len(ids))}/{len(ids)} =====')
            for k in ids[:sample]:
                v = res[k]
                print(f"{k:6} {names[k][:22]:22} y={v['y']} | {v['q']}")


if __name__ == '__main__':
    main()
