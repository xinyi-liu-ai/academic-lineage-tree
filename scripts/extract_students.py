"""从教授自己的主页 / CV 的学生栏目（Students、PhD Students、Alumni、Former Students、Postdocs…）提取学生名单。

- 栏目标题必须单独成行；栏目到下一个标题（Teaching、Publications…）或 60 行为止
- 每条取开头的人名（2–4 个首字母大写的词），括号或后面的文字当作去向（如 "Rui Dong (Amazon)"）
- 记下每个名字所在的栏目、子栏目（"PhD" / "Postdocs" / "Masters" / "Undergraduates" / "Visitors" / "Collaborators"…）
  和这一项自己的标注（"(PhD '13)"、"MS 2007"、"DREU 2015"、"Postdoc 24"、"CS BSc"；本行没写看下一行说明）：
  只留博士生 / 博士毕业生；博士后记为 postdoc；硕士、本科、REU、实习、访问、合作者、论文评审一律不要；
  只写 Students / Alumni 的栏目里没标注的项，页面上另有本科 / 访问 / 实习等栏目时也不要
  （例外：页面没写明 PhD 栏目、却把硕士单列时，按对比当作博士生）
- 名单上的名字也是名单内的教授时，按 dblp 作者标识认定是同一人（"Bo Li 0020" ≠ 教授 "Bo Li 0026"）
- 三四个词的名字取全（"Bao Tran Truong"），粘在一起的词拆开（"ZhangPh.D."）
- 没和教授合写过论文的名字再过一道过滤：导航 / 栏目 / 题目用词黑名单，页面里常以小写出现、dblp 作者名里又没出现过的词
检验（--eval）：抽出的名字有多少和这位教授在 dblp 上合写过论文、与 Wikidata 已知学生的重合情况
- 实验室网站（fetch_lab_pages.py 抓的 HTML，别的域名上的 People / Team 页）按卡片找人，另记个人主页链接和照片，见 lab_entries()
输出: data/enrich/students_homepage.jsonl、data/enrich/students_lab.jsonl（实验室网站上的；多了 h / li / g / ph / via 字段）
用法: python scripts/extract_students.py [--eval]
"""
import json
import os
import pickle
import re
import sys
from collections import Counter, defaultdict
from urllib.parse import urljoin, urlparse

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
                       r'summer|reu|rotation|bachelor|honou?rs\s+(?:students?|theses|thesis)|ms/ma)\b.{0,40}$', re.I)
END_HEAD = re.compile(r'^(?:selected\s+|recent\s+)?(?:teaching|publications?|papers|research|courses?|news|contact|service|projects?|awards?|honors|talks|'
                      r'software|bio|biography|funding|sponsors|links|misc|education|employment|experience|positions?|grants|'
                      r'professional|activities|outreach|collaborators|recent news|announcements|openings|prospective students|'
                      r'for prospective|teaching assistants|committees?|editorial|invited|patents|books|preprints|datasets|code)\b.{0,40}$', re.I)
TOK = r"(?:[A-Z][a-zA-Z'’\-]+|[A-Z]\.(?:-?[A-Z]\.)?)"
PART = r'(?:de|del|della|van|von|der|den|da|di|la|le|du|dos|das|bin|al|el|ibn)'
NAME = re.compile(rf"^({TOK}(?:\s+(?:{TOK}|{PART}))*?\s+[A-Z][a-zA-Z'’\-]+)(?=$|[\s,;:(\[–—\-|.])")
# 同样的规则但取最长：NAME 是非贪婪的，三四个词的名字（"Bao Tran Truong"）会被截成前两个词
NAME_RUN = re.compile(rf"^({TOK}(?:\s+(?:{TOK}|{PART}))*\s+[A-Z][a-zA-Z'’\-]+)(?=$|[\s,;:(\[–—\-|.])")
STOP = {w.lower() for w in '''University Institute College Lab Laboratory Research Google Microsoft Amazon Apple Meta Facebook IBM Intel
    Professor Assistant Associate PhD Ph.D. Student Students Postdoc Postdocs Now Current Former Fall Spring Summer Winter Department
    Computer Science Sciences Engineering Center School The Co Advised Thesis Dissertation Graduated Joint With Inc Corp Group Team
    Members Alumni January February March April May June July August September October November December Visiting Scholar Fellow
    Faculty Lecturer Scientist Engineer Senior Staff Principal Director Manager Startup Company CEO CTO Founder Postdoctoral
    Co-advised Coadvised Master Masters Undergraduate Undergrad Intern National State Technology Data Machine Learning Systems
    AI Artificial Intelligence NLP Vision Robotics Security Networks Lab's Labs Home Page Homepage Website CV Email Office Phone
    New York Los Angeles San Francisco Boston Seattle Chicago Hong Kong China USA US Canada Inria Max Planck MIT CMU UCLA
    Stanford Berkeley Harvard Princeton Yale Cornell Columbia Toronto Oxford Cambridge Tsinghua Peking Zhejiang
    Award Best Paper Prize Recipient Medal Our We I My His Her Their Department's Program Programs Project Projects
    Ph PhD's Yahoo Nvidia NVIDIA Adobe Baidu Alibaba Tencent Huawei Samsung Uber Netflix Twitter LinkedIn Oracle Cisco
    Qualcomm Bloomberg Waymo Tesla OpenAI DeepMind Salesforce ByteDance VMware'''.split()}
YEARISH = re.compile(r'\b(19|20)\d{2}\b')
END_ANY = re.compile(r'courses taught|teaching|publications', re.I)  # 短行里出现即视为栏目结束
STUDENTISH = re.compile(r'ph\.?\s?d|student|postdoc|dissertation|thesis|graduated|defended|alum', re.I)

# ---- 栏目标题和每一项的身份标注（博士 / 博士后 / 硕士本科等）----
# 子栏目标题：学生栏目里常再分 "PhD" / "Postdocs" / "Masters" / "Undergraduates" / "Visitors" / "Collaborators"
PHD_W = r'ph\.?\s?d\.?s?|d\.?\s?phil\.?|doctoral|doctorate'  # "Alumni PhDs"
POST_W = r'post-?\s?docs?|post-?doctoral(?:\s+(?:researchers?|fellows?|scholars?|associates?))?'
NONPHD_W = (r"masters?|master['’]s|m\.?\s?s\.?\s?e\.?|m\.?\s?s\.?|m\.?\s?sc\.?|m\.?\s?eng\.?|m\.?\s?phil\.?|m\.?\s?tech|m\.?\s?a\.?|s\.?m\.?|mba|"
            r"undergrad\w*|bachelor['’]?s?|b\.?\s?s\.?|b\.?\s?sc\.?|b\.?\s?a\.?|b\.?\s?tech|b\.?\s?eng|reu|dreu|surf|urop|"
            r"high[-\s]school|summer|interns?|internships?|visiting|visitors?|rotation|rotating|honou?rs|exchange|"
            r"affiliates?|affiliated|collaborators?|frequent collaborators|external|alumni\s+of\s+other\s+groups")
DEG_ANY = rf'(?:{PHD_W}|{POST_W}|{NONPHD_W})'
# "PhD Dissertation" 单独出现是教授本人的博士论文，要有 "supervised" 之类才是指导的学生
H_TAIL = (r'(?:students?|student researchers|researchers?|scholars?|fellows?|alumni|alumnae|advisees|graduates|'
          r'(?:theses|dissertations?)\s+(?:supervised|advised|directed|chaired|completed)|'
          r'thesis\s+(?:students?|supervision|advising)|supervision|advising|mentees|associates|members|'
          r'interns|projects|research(?:ers)?|candidates|programs?|programmes?)')
H_TAIL2 = r'(?:\s+(?:advised|supervised|graduated|mentored|hosted|co-?advised))?'
DEG_HEAD = re.compile(rf'^(?:{Q}\s+|and\s+)*(?:(?P<pre>[A-Z][\w&.\-]*)\s+)?(?P<d1>{DEG_ANY})(?:\s*(?:and|&|/|,)\s*(?P<d2>{DEG_ANY}))?'
                      rf'(?P<tail>\s+(?:(?:and|&)\s+)?{H_TAIL}{H_TAIL2})?(?:\s+(?:and|&)\s+(?:alumni|former students))?'
                      rf'(?:\s*,?\s*\(.*\))?\s*:?$', re.I)
# 学生栏目里只写"现在 / 以前"、不写学位的子标题
AMB_SUB = re.compile(rf'^(?:{Q}|(?:current|former|past)\s+(?:group\s+|lab\s+)?members|(?:current\s+|former\s+)?students?\s*(?:&|and)\s*researchers|'
                     r'(?:graduate\s+|grad\s+)?researchers|members|others?|alumni\s+hall\s+of\s+fame)\s*:?$|'
                     r'^.{0,25}\((?:i\.e\.,?\s*)?(?:past|former)\s+(?:group\s+|lab\s+)?members\)$', re.I)  # "Dearly Departed (i.e., Past Group Members)"
# 指导别校学生 / 本科生的 "Mentees" 栏目：里面再分 "PhD Students" 也不是自己的博士生
MENTOR_HEAD = re.compile(r'mentee|mentor(?:ed|ing)|students?\s+mentored', re.I)
# 短行：论文评审、合作者、访问学者、非正式指导（"Ad hoc Ph.D. mentoring"：别校学生）
SKIP_ANY = re.compile(r'committee|examiner|readers?\b|jury|collaborat|affiliates|visitors|visiting|ad[\s-]hoc|informal|unofficial', re.I)
CONTACT_END = re.compile(r'^(?:to reach me|reach me|mailing address|'
                         r'contact(?: me| info(?:rmation)?)?\s*:?$|a few sayings|quotes?\s*:?$)', re.I)
# 每一项后面的标注
L_DROP = re.compile(r'\b(?:visiting|visitor|intern(?:ship)?s?|reu|dreu|surf|urop|high[-\s]school|summer\s+(?:student|intern|research\w*)|'
                    r'rotation|rotating|exchange\s+student|affiliate|collaborator|thesis\s+reader|reader|committee\s+member|'
                    r'honou?rs\s+thesis|senior\s+thesis|capstone)\b', re.I)
L_NONPHD_W = re.compile(r"\b(?:masters?|master['’]s|undergrad\w*|bachelor\w*|b-?tech|m-?tech|mtech|btech)\b", re.I)
# 缩写大小写敏感（"MS" 是硕士，"ms" 不是）；后面可以紧接大写字母（排版粘在一起的 "BScDing"）
L_NONPHD_A = re.compile(r"(?<![A-Za-z])(?:M\.?S\.?E\.?|M\.?S\.?|M\.?Sc\.?|MEng|M\.Eng\.?|MPhil|M\.Phil\.?|MBA|MFin|S\.?M\.?|"
                        r"B\.?S\.?|B\.?Sc\.?|B\.?A\.?|BEng|B\.Eng\.?|BT|B\.?Tech|M\.?Tech)(?![a-z])")
L_PHD = re.compile(r'(?<![A-Za-z])(?:ph\.?\s?d|d\.?\s?phil|doctoral|doctorate)', re.I)
L_POST = re.compile(r'\bpost-?\s?docs?\b|\bpost-?doctoral\b', re.I)
L_CLASSYEAR = re.compile(r"(?:^|\s)[’']\d{2}\b")  # "Jane Doe '19"：美国本科生的届别写法
# 去向标记：之后的文字是毕业去向（"MS -> Ph.D. Cornell"、"First position: PhD student at Toronto"），不算身份
DEST = re.compile(r'\b(?:now|currently|then|later|subsequently|first\s+(?:position|job|employment)|current\s+position|'
                  r'next(?:\s+position)?|employer|job|went\s+on|moved\s+to|joined|afterwards?|placement|position)\b|→|⇒|->|=>|➔|»', re.I)
# 跨页面一票否决：访问学者 / 合作者。论文评审委员会不算（常有人先请教授当硕士论文评委、后来跟他读博）
VISITOR = re.compile(r'visit|collaborat|affiliat', re.I)
# 论文题目里常见、人名里不会有的词尾（"Polarized Substructural"）
TITLE_SUFFIX = re.compile(r'(?:ized|ised|izing|ising|ation|ations|tion|tions|sion|sions|ural|ality|ness|ment|ments|ology|ologies|'
                          r'ical|ics|ings|ive|ives|able|ible)$', re.I)


def head_kind(l):
    """短行是不是栏目标题。返回 (类别, 是否写明学位)：'phd' / 'postdoc' / 'amb'（只说学生）/ 'skip'（硕士、本科、
    访问、实习、合作者、论文评审…）/ 'end'；不是标题返回 (None, False)。"""
    if len(l) > 70:
        return None, False
    s = l.strip(' \t:•·*#▪►|↪→⇒➔»>–—-').strip()
    s = re.sub(r'\s*\(\d+\)$|\s+\d{1,3}$', '', s)  # "PhD Students (12)"、"PhD Students 4"
    # "Supervised Graduate Students at Yale" -> "Graduate Students"
    s = re.sub(r'^(?:supervised|advised)\s+(?=\S+\s+students?\b|students?\b|ph\.?\s?d|post)', '', s, flags=re.I)
    s = re.sub(r'(\b(?i:students?|scholars?|researchers?))\s+(?:at|@)\s+[A-Z][\w .&\'’-]{0,30}$', r'\1', s)
    if not s:
        return None, False
    if re.match(r'(?i)research\s+(?:interests?|areas?|topics?|focus)\s*:\s*\S', s):
        return None, False  # 卡片里的 "Research Interests: ..."，不是栏目标题
    if re.fullmatch(r'(?i)ph\.?\s?d\.?\s+(?:thesis\s+|dissertation\s+)?(?:advisor|supervisor)\s+(?:of|for|to)\s*:?', s):
        return 'phd', True
    if re.fullmatch(r'(?i)(?:(?:current|our|lab|group|team|research)\s+)?(?:members|people|team|research\s+team|lab\s+members|group\s+members|team\s+members|the\s+team)\s*:?', s):
        return 'gen', False  # 没写身份的人员栏目：只收本行 / 下一行写着 PhD student / Postdoc 的
    # 只认像标题的写法（复数 / 带 researchers 等名词）；"Postdoc, UCF"、"Postdoc at MIT" 是上一个人的去向，不是标题
    if len(s) <= 70 and re.fullmatch(r'(?i)(?:current\s+|former\s+|past\s+|previous\s+)?(?:post-?\s?docs|post-?\s?doc(?:toral)?\s+(?:researchers?|scholars?|fellows?|associates?|advisees|alumni|students|members))'
                                     r'(?:\s+(?:and|&)\s+[a-z ]{3,40})?(?:\s+(?:supervised|hosted|advised|mentored))?\s*:?', s) \
            and not re.search(PHD_W, s, re.I):
        return 'postdoc', True  # "Postdoctoral Researcher and Visiting Project Scientist"、"Post-doctoral scholars supervised"
    if len(s) <= 70 and re.search(r'(?i)\bcommittee\b|\bexaminer\b|\breader\b', s) and re.search(r'(?i)ph\.?\s?d|thesis|dissertation|doctoral', s):
        return 'skip', True  # "PhD Thesis Committee Member For:"
    if CONTACT_END.match(s):
        return 'end', False
    if re.fullmatch(r'(?:current(?:ly)?|present|former|past|previous|completed|graduated|(?:group|lab|team|research\s+group)\s+alumn(?:i|ae))\s*:?', s, re.I):
        return 'sub_inherit', False  # 光秃秃的 "Current" / "Former"：沿用上一级的学位（栏目外出现时当作没写学位的学生栏目）
    m = DEG_HEAD.match(s)
    if m:
        if any(d and re.fullmatch(r'[A-Z][a-z]', d) for d in (m.group('d1'), m.group('d2'))):
            return None, False  # "Justin Ma (Spring 2010)"：人名里的 "Ma"、"Ms"，不是 MA / MS
        kinds = set()
        for d in (m.group('d1'), m.group('d2')):
            if not d:
                continue
            kinds.add('phd' if re.fullmatch(PHD_W, d, re.I) else 'postdoc' if re.fullmatch(POST_W, d, re.I) else 'skip')
        bare = not m.group('tail')
        if m.group('pre') and re.fullmatch(NONPHD_W, m.group('pre'), re.I):
            kinds = {'skip'}  # "Visiting PhD Students"、"Summer PhD Interns"
        if 'skip' in kinds and 'phd' in kinds:
            k = 'mixed'  # "PhD and Masters Students"：没标注的项分不清
        elif 'skip' in kinds:
            k = 'skip'
        elif 'phd' in kinds:
            k = 'phd'
        else:
            k = 'postdoc'
        if bare and k != 'postdoc':
            return ('sub_' + k), True  # 光秃秃的 "PhD" / "Masters"：只在学生栏目里算子标题
        return k, True
    if len(s) <= 60 and STU_HEAD.match(s):
        return ('phd' if re.search(PHD_W, s, re.I) else 'amb'), bool(re.search(PHD_W, s, re.I))
    if len(s) <= 60 and POST_HEAD.match(s):
        return 'postdoc', True
    if len(s) <= 60 and re.match(r'(?i)(?:hosted|visiting|guest|exchange)\s+(?:ph\.?\s?d\.?\s+|graduate\s+|doctoral\s+)?(?:students?|researchers?|scholars?)\b', s):
        return 'skip', True
    if len(s) <= 60 and re.match(r'(?i)(?:co-?advising|co-?advisees|mentorship|mentoring|mentees|students?\s+mentored|research\s+mentoring|'
                                 r'other\s+(?:alumni|students|mentees)|collaborators|research\s+collaborators|other\s+advising)\b.{0,40}$', s):
        return 'skip', True
    if len(s) <= 60 and SKIP_HEAD.match(s):
        return 'skip', True
    if len(s) <= 60 and END_HEAD.match(s):
        return ('skip' if re.match(r'(?:frequent\s+)?collaborators', s, re.I) else 'end'), False
    if len(s) <= 50 and AMB_SUB.match(s):
        return 'sub_amb', False
    # "Graduate Thesis Committees , Virginia Tech" 首字母大写像人名，复数的职责词说明是标题
    if len(s) <= 60 and SKIP_ANY.search(s) and (not NAME.match(s) or re.search(r'committees|examiners|readers|collaborators|visitors', s, re.I)):
        return 'sub_skip', False
    if len(s) <= 40 and END_ANY.search(s):
        return 'end', False
    return None, False


def entry_label(rest, nxt=''):
    """这一项写的身份：'drop'（访问、实习、REU、合作者…）/ 'phd' / 'postdoc' / 'nonphd'（硕士、本科）/ 'class'（'19 届）/ None。
    只看去向标记之前的文字；本行没写时看下一行的说明（下一行只认博士 / 硕士本科 / 访问实习，不认博士后——常是去向）。"""
    def lab(t, allow_post=True):
        t = DEST.split(t, 1)[0]
        if L_DROP.search(t):
            return 'drop'
        ph = L_PHD.search(t)
        nm = L_NONPHD_W.search(t) or L_NONPHD_A.search(t)
        # 硕士写在前面、后面的 PhD 是在别处读的："(MS), PhD student at UT Austin"、"2020 MS, with PhD in Physics"；
        # 本科学历写在前面（"B.S. Tsinghua, Ph.D. student"）、"M.S. 2010, Ph.D. 2015" 仍算博士
        if ph and nm and nm.start() < ph.start():
            if re.match(r'\.?\s*(?:student|candidate)?\s*(?:at|in|@|from)\b', t[ph.end():], re.I):
                return 'nonphd'
            # 身份词写在前面（"Undergraduate, MIT PhD student"、"Master's student; PhD at ..."）也是硕士本科
            st = re.search(r"\b(?:undergrad\w*|(?:masters?|master['’]s|m\.?s\.?|meng)\s+student)", t, re.I)
            if st and st.start() < ph.start():
                return 'nonphd'
        if ph:
            return 'phd'
        # 博士后要写在最前面（"Postdoc 24, ..."、"(PostDoc)"、"Postdoc → NJIT"）；
        # "2023 · Postdoctoral researcher, UMD" 是毕业去向
        if allow_post and re.match(r'[\s,;:(\[|–—-]*(?-i:[A-Z]{2,6}\s+){0,2}' + L_POST.pattern, t, re.I):
            return 'postdoc'
        if nm:
            return 'nonphd'
        if L_CLASSYEAR.search(t):
            return 'class'
        return None
    return lab(rest) or (lab(nxt, allow_post=False) if nxt else None)
COMMON = set()  # 常用英文词（由 build_common() 从所有页面文字统计）
COMMON_LO = set()  # 较常见的词：名字里每个词都属于这一类就不要
WORDISH = set()  # 页面里以小写形式出现过若干次的词（英文单词）
WORDISH_MIN = 5
NAME_VOCAB = Counter()  # dblp 作者名里出现过的词（小写），由 build_vocab() 统计；既是英文单词又从没当过名字的词不算人名
FIRST_VOCAB, LAST_VOCAB = Counter(), Counter()
LOWER = Counter()  # 各词在页面里以小写出现的次数
NAME_LAST = re.compile(r"[A-Z][a-zA-Z'’\-]+")
SURNAME_PREFIX = {'mc', 'mac', 'de', 'di', 'da', 'du', 'le', 'la', 'el', 'al', 'van', 'von', 'der', 'del', 'della', 'st', 'fitz',
                  'ben', 'abu', 'bin', 'o', 'ka', 'na'}
CAMEL = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]*)+\b")


def unglue(part):
    """排版时粘在一起的词拆开："ZhangPh.D." / "BlackSenior" / "XiaoUAlberta" / "SamantaM"；"McKay"、"DeLorey" 不动。"""
    def fix(m):
        segs = re.findall(r'[A-Z][a-z]*', m.group(0))
        for k in range(1, len(segs)):
            s, lo = segs[k], segs[k].lower()
            if k == 1 and segs[0].lower() in SURNAME_PREFIX:
                continue  # "ElFar"、"DeLong"、"MacLean" 是姓
            if (lo in STOP or lo in JUNK_WORDS or lo in COMMON or lo in {'ph', 'phd', 'now', 'currently'}
                    or all(len(x) == 1 for x in segs[k:])  # 末尾粘了缩写："SamantaM"、"BenvenutiCS"
                    or (s == 'U' and len(segs[k + 1:][0] if k + 1 < len(segs) else '') > 1)):  # "XiaoUAlberta"
                return ''.join(segs[:k]) + ' ' + ''.join(segs[k:])
        return m.group(0)
    return CAMEL.sub(fix, part)


# 不是人名的词：网站导航、栏目名、课程 / 机构 / 地名、论文题目里常见的词（只用于没合写过论文的名字）
JUNK_WORDS = {w.lower() for w in '''
    Guide Guides Transfer Credit Credits Course Courses Lab Labs Center Centre Centers Research Researchers News Newsletter
    Seminar Seminars Program Programs Office Offices Student Students University Universities Department Departments
    Home Contact Contacts Career Careers Services Service Applicants Applicant Applications Application Admission Admissions
    Opportunities Opportunity Photo Photos Gallery Video Videos Media Coverage Statement Statements Diversity Inclusion
    Equity Accessibility Web Website Log Login Logout Sign Jump Skip Menu Search Find People Directory Type Select Download
    Downloads Connected Connect Areas Area Tobacco Free Next Steps Step Recent Latest Related Talk Talks Lecture Lectures
    Industry Allies Alliance Partners Life Prose Strategic Framework Frameworks Algorithmic Aspects Technical Reports Report
    Brief Bio Biography Fusion Prospective Virtual Outgoing Incoming Server Play Watch Listen Read More Learn Explore Visit
    Publication Publications Paper Papers Preprint Preprints Journal Conference Workshop Workshops Proceedings Book Books
    Chapter Study Studies Survey Analysis Towards Toward Improved Lower Upper Bound Bounds Approach Approaches Method Methods
    Learning Deep Neural Network Networks Graph Graphs Model Models Modeling Algorithm Algorithms Optimization Efficient
    Robust Scalable Adaptive Dynamic Dynamics Theory Theoretical Applied Application Using Based Via Beyond Understanding
    Visual Language Languages Computing Computational Computation Quantum Secure Privacy Policy Policies Terms Rights
    Copyright Reserved Sitemap Accessibility Feedback Calendar Events Event Announcements Resources Resource Facilities
    Support Giving Donate Alumni Alumnus Undergraduate Undergraduates Graduate Graduates Postgraduate Faculty Staff
    Sophomore Freshman Junior Senior Seniors Honors Scholars Scholarship Scholarships Fellowship Fellowships Awards Award
    First Second Third Purdue Johns Loyola Marymount Commonwealth Rentals Thermo Engineers Tech Polytechnic Institute Institutes College Colleges School Schools Academy Campus Campuses Valley Silicon Hall Building
    County City Bay Coast North South East West Northern Southern Eastern Western Central Santa San Los Las Saint
    Infectious Diseases Disease Health Medicine Medical Clinical Hospital Biology Chemistry Physics Mathematics Statistics
    Engineering Science Sciences Humanities Arts Business Management Economics Education Law Society Societies Association
    Committee Council Board Initiative Initiatives Project Projects Group Groups Team Teams Members Member Join Joining
    Openings Positions Position Jobs Job Hiring Apply Overview Introduction Welcome About Mission Vision History Facts
    Highlights Spotlight Stories Story Press Release Releases Blog Blogs Podcast Social Twitter Facebook Instagram Youtube
    Forum Symposium Colloquium Tutorial Tutorials Lab's Teaching Class Classes Curriculum Syllabus Schedule Hours Map
    Directions Parking Location Locations Address Phone Fax Email Mail Office Contact Inc Corp Corporation Company Ltd LLC
    Foundation Fund Funding Grant Grants Sponsor Sponsors Donor Donors Information Info Quick Links Link Resources Tools Tool
    Software Hardware Code Dataset Datasets Data Benchmark Demo Demos Slides Poster Posters Thesis Theses Dissertation
    Recipient Winner Winners Finalist Outstanding Distinguished Excellence Achievement Achievements Best Top Most Annual
    Interdisciplinary Collaborative Collaboration Collaborations Laboratory Laboratories Systems System Studio Workshop
    Currently Previously Formerly Graduated Expected Joined Moved Former Current Past Present Future Upcoming
    To If Of On At By Or Is It Us We Be As The And For With From Into Our Your Their This That These Those All New
    '''.split()}
FUNCTION_2 = {'to', 'if', 'of', 'on', 'at', 'by', 'or', 'is', 'it', 'us', 'we', 'be', 'as'}


def build_vocab(idx, profs):
    """dblp 作者名里出现过的名字词；另记作为名（第一个词）、作为姓（最后一个词）出现的次数。"""
    for a in {a for pubs in idx.values() for pub in pubs for a in pub[2]} | {p['name'] for p in profs}:
        toks = fold(display_name(a)).split()
        for t in set(toks):
            NAME_VOCAB[t] += 1
        if len(toks) >= 2:
            FIRST_VOCAB[toks[0]] += 1
            LAST_VOCAB[toks[-1]] += 1


def junk_name(toks):
    """名字里有明显不是人名的词：栏目 / 导航 / 题目用词，或页面里常以小写出现、dblp 作者名里又几乎没出现过的词。"""
    wordish = []
    for i, t in enumerate(toks):
        lo = t.lower()
        f = fold(t)
        if lo in JUNK_WORDS and NAME_VOCAB[f] < 20:
            return True
        if lo in FUNCTION_2 and not (i == len(toks) - 1 and len(toks[0]) > 2 and FIRST_VOCAB[fold(toks[0])] >= 5
                                         and fold(toks[0]) not in WORDISH):
            return True  # "Jump To"、"A. If"；但 "Khoa To" 是真名
        if t.endswith(("s'", 's’', '’')) or re.match(r'[A-Z]{2,}[a-z]', t):  # "Students' Study"、"VisHikers’ Guide"、"ENGenious"
            return True
        if len(f) > 2 and f in WORDISH and NAME_VOCAB[f] < 2:
            wordish.append((i, f))
    if not wordish:
        return False
    if len(wordish) > 1:
        return True
    # 只有一个这样的词、它在页面里小写出现得不多、另一头是常见的名 / 姓：多半是少见的真名（"Dan Knights"、"Turbo Majumdar"）
    i, f = wordish[0]
    first, last = fold(toks[0]), fold(toks[-1])
    strong_first = len(first) > 2 and FIRST_VOCAB[first] >= 30
    if i == len(toks) - 1:
        return not (LOWER[f] < 100 and strong_first)
    if i == 0:  # "Turbo Majumdar"、"Tural Hagverdiyev"
        return not (LOWER[f] < 30 and (LAST_VOCAB[last] >= 30 or (last not in WORDISH and NAME_VOCAB[last] < 2)))
    return not (LOWER[f] < 100 and strong_first and LAST_VOCAB[last] >= 30)


def name_like(t):
    """名字后面接着的词是否可能也是名字的一部分（用来补全三四个词的名字）。"""
    lo = t.lower()
    return not (lo.strip('.') in STOP or lo in COMMON or lo in COMMON_LO or lo in JUNK_WORDS or t.endswith(("'s", '’s'))
                or (t.isupper() and len(t) > 1) or re.match(r'[A-Z]{2,}[a-z]', t)  # BSc、MSc
                or (fold(t) in WORDISH and NAME_VOCAB[fold(t)] < 5))


def build_common(texts):
    """在页面里以小写形式大量出现的词是普通单词，不会是人名的组成部分。"""
    low, cap = Counter(), Counter()
    for t in texts:
        for w in re.findall(r"\b[A-Za-z][a-z]{2,}\b", t):
            (low if w[0].islower() else cap)[w.lower()] += 1
    COMMON.update(w for w, c in low.items() if c >= 100 and c >= 2 * cap[w])
    COMMON_LO.update(w for w, c in low.items() if c >= 25 and c >= cap[w])
    WORDISH.update(w for w, c in low.items() if c >= WORDISH_MIN)
    LOWER.update(low)
    COMMON.update({'carnegie', 'mellon', 'campus', 'quick', 'submit', 'steering', 'advisory', 'graduate', 'outcomes', 'degrees', 'rights', 'reserved', 'sign', 'ons', 'frequent', 'searches', 'newsletter', 'recruiting', 'partners', 'gear', 'family', 'early', 'about', 'all', 'past', 'outstanding', 'distinguished', 'our', 'home', 'contact', 'technologies', 'chase', 'jpmorgan', 'addison', 'plain', 'privacy', 'policy', 'employment', 'researcher', 'scholars', 'certificate', 'system', 'network', 'designing', 'youtube', 'delft', 'turing'})


def clean_note(s):
    s = s.strip(' \t,;:.-–—()[]')
    s = re.sub(r'^(?:now|currently|then)\s+(?:at|with|an?|the)?\s*', '', s, flags=re.I)
    s = re.sub(r'^(?:ph\.?\s?d\.?|graduated|grad\.?)\s*(?:\d{4})?\s*[,;:–—-]?\s*', '', s, flags=re.I)
    s = re.sub(r'\s+', ' ', s).strip(' ,;:.-–—()')
    return s[:70]


def plausible(toks, coauthors_hit):
    """名字本身像不像人名：2–4 个首字母大写的词；不是合作者时，不能带论文题目式的词尾（"Polarized Substructural"），
    也不能每个词都从没在 dblp 作者名里出现过、又在页面里当普通单词用（"Ancient Greek Proverb"）。"""
    if not 2 <= len(toks) <= 4 or not all(t[0].isupper() for t in toks):
        return False
    if coauthors_hit:
        return True
    for t in toks:
        f = fold(t)
        if len(f) >= 6 and TITLE_SUFFIX.search(f) and NAME_VOCAB[f] < 3:
            return False
    if all(NAME_VOCAB[fold(t)] == 0 and (fold(t) in WORDISH or LOWER[fold(t)] > 0) for t in toks if len(t.strip('.')) > 1):
        return False
    return True


# 引用格式："Khan, S., Iqbal, M., ..."、"[J.35]"、"J-33."、"(2016)."、"with C. Ferry, T. Yuki, and S. Derrien"
PUB_CITE = re.compile(r'\b[A-Z][a-z]+,\s+[A-Z]\.(?:\s*[A-Z]\.)?,|\[[CJWB]\.?\s?\d+\]|^\s*\*?[CJW]-?\d+\.|\((?:19|20)\d\d\)\.|'
                      r'\b[A-Z]\.\s+[A-Z][a-z]+,\s+[A-Z]\.\s+[A-Z][a-z]+,|\b[Ww]ith\s+[A-Z]\.\s+[A-Z][a-z]+,')
PUB_LINE = re.compile(r'(?i)\(co-?pi\)|\(pi\)|\$\s?\d[\d,]{3,}|\bgrant\b|\bpp\.\s*\d|\bvol\.\s*\d|\bno\.\s*\d|proceedings|\bconference\b|\bjournal\b|transactions\s+on|\barxiv\b|'
                      r'\bin\s+(?:acm|ieee|usenix)\b|[“”]|acceptance\s+rate|\[pdf\]|\bdoi\b|\beprint\b|under\s+review|to\s+appear|'
                      r'\b(?:icml|neurips|iclr|cvpr|iccv|eccv|acl|emnlp|naacl|kdd|sigmod|vldb|chi|uist|osdi|sosp|nsdi|sigcomm|ccs|stoc|focs|soda)\s*[\'’]?\d{2}')


def entries(lines, coauthors=frozenset()):
    """栏目里的每一项 -> (名字, 备注, 原文, 标注)。coauthors：这位教授 dblp 合作者的匹配键，合作者不受下面的严格过滤。
    标注见 entry_label()：本行（去向之前）写的 PhD / Postdoc / MS / BSc / REU / Visiting…，本行没写时看下一行的说明。"""
    out = []
    lines = [l.strip(' \t•·*-–—▪►') for l in lines]
    for li, line in enumerate(lines):
        if not line or re.match(r'(?:joint(?:ly)?\s+)?with\s', line, re.I):
            continue  # "With Yi Zhang, Dan Roth · VLDB"：合作者，不是学生
        if PUB_LINE.search(line) or PUB_CITE.search(line):
            continue  # 论文列表里的作者行（"Vol. 8, pp. 48-55"、"Proceedings of ..."、带引号的题目）
        if li and (re.search(r'\b(?:with|and|by|of|the|from|to|for|called|named|thank|thanks|via|including|e\.g\.,?)$', lines[li - 1])
                   or re.search(r'(?i)\b(?:co-?advisors?|advisors?|supervisors?|advised\s+by|committee\s+chair)\s*:?$', lines[li - 1])):
            continue  # 上一句没写完：正文里提到的人（合著者、合作者）
        nxt = lines[li + 1] if li + 1 < len(lines) else ''
        if nxt and NAME.match(unglue(nxt)):
            nxt = ''  # 下一行是另一个人
        prev = lines[li - 1] if li else ''
        if re.fullmatch(r'\(\s*[A-Za-z.\s/]{2,12}\s*\)', prev):
            nxt = prev  # 学位写在名字上一行："(MS)\nMeer Shah."
        flat = re.sub(r'\([^)]*\)', '', line)
        parts = [line]
        if line.count(',') >= 1 and '(' not in line:
            cand = [x.strip(' .') for x in re.split(r',\s*(?:and\s+)?|\s+and\s+', flat) if x.strip(' .')]
            # 每一段都得像人名："David Park, Undergrad Student" 不是两个人（否则身份标注就丢了）
            if len(cand) >= 2 and all(NAME.match(c) and len(c.split()) <= 4 and entry_label(c) is None
                                      and not any(t.lower().strip('.') in STOP or t.lower() in JUNK_WORDS for t in c.split())
                                      for c in cand):
                parts = cand
                nxt = ''
        for part in parts:
            part = re.sub(r'^and\s+', '', part.strip())
            part = re.sub(r'^(?:Dr|Prof)\.?\s+(?=[A-Z])', '', part)
            part = re.sub(r"^([A-Z][\w'’-]+)\s+[(“\"‘']([A-Z][A-Za-z]{1,12})[)”\"’']\s+(?=[A-Z])", r'\1 ', part)  # 括号 / 引号里的昵称
            part = unglue(part)  # "Zhenyu ZhangPh.D." / "Sam BlackSenior Data Scientist"
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
            end = m.end()
            # 三四个词的名字：和教授合写过论文的写法优先；否则整串都像名字时用整串
            run = NAME_RUN.match(part)
            if run and run.end() > end:
                rtoks = run.group(1).split()
                longer = [n for n in range(len(rtoks), len(toks), -1)
                          if n <= 4 and NAME_LAST.fullmatch(rtoks[n - 1])]
                hit = next((n for n in longer if key(' '.join(rtoks[:n])) in coauthors), None)
                if hit is None and key(name) not in coauthors and len(rtoks) <= 4 and longer and longer[0] == len(rtoks)                         and all(name_like(t) for t in rtoks[len(toks):]) and not junk_name(rtoks):
                    hit = len(rtoks)
                if hit:
                    name = ' '.join(rtoks[:hit])
                    toks = rtoks[:hit]
                    end = len(re.match(r'\s+'.join(map(re.escape, toks)), part).group(0))
            co = key(name) in coauthors
            if not co and junk_name(toks):
                continue  # 不是合作者时再严一些：导航栏、栏目名、论文题目、地名
            if not plausible(toks, co):
                continue
            rest = part[end:]
            if re.match(r'\s+(?:and\s+I|called|who|which|has|have|had|is|was|were|wrote|and\s+(?:i|we)\b|for\s+(?:his|her|their)|on\s+(?:his|her|the))\b', rest):
                continue  # 正文："Larry Snyder called Principles of ..."
            if re.match(r'\s*(?:University|Institute|College|School|Laboratory|Lab|Center|Centre|Hospital|Inc\b|Corporation)', rest):
                continue  # 机构名的前半截："Prince Sultan University"
            if re.search(r'(?<!co-)(?<!co)\b(?i:advisor|supervisor)\s*[:,]\s*(?:Prof\.?\s*|Dr\.?\s*)?[A-Z]', rest) or \
                    re.match(r'\s*\(\s*(?i:advisor|adviser|supervisor|advised\s+by)\s+(?:Prof\.?\s*|Dr\.?\s*)?[A-Z]', rest):
                continue  # "Advisor, James Allan"：导师另有其人（论文评审名单）
            if not co and re.match(r'\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\s*,?$', rest) and any(
                    TITLE_SUFFIX.search(fold(t)) or fold(t) in WORDISH for t in rest.split()):
                continue  # 整行是题目式的大写短语："Polarized Substructural Session Types,"
            out.append((name, clean_note(rest), rest, entry_label(rest, nxt)))
    return out


def sections(text):
    """按行扫一遍页面，记下每一行所在的学生栏目及其子栏目。
    返回 (栏目列表, 页面是否有写明本科 / 访问 / 实习等的栏目而又没把硕士单列)；栏目 = (编号, 类别, 栏目标题, 子标题, 各行)。
    类别：'phd' / 'postdoc' / 'amb'（只写 Students / Alumni，没写学位）/ 'skip'。"""
    # 零宽空格、不换行空格：Google Sites 等页面的标题常带（"​PhD students"），不去掉就认不出标题
    lines = [re.sub(r'[​‌‍﻿]', '', l).replace('\xa0', ' ').strip() for l in text.split('\n')]
    lines = [l for l in lines if l]
    out = []
    page_nonphd = page_masters = page_phd = False
    area = None  # 当前学生栏目的编号
    n_area = 0
    cur = head = sub = None
    body = []
    since = 0

    def flush():
        if area is not None and body:
            out.append((area, cur, head, sub, list(body)))
        body.clear()

    mentor = False  # 当前栏目是 "Mentees / Students Mentored"（且没写 advis）：整块跳过，含其中的 "PhD Students" 子标题
    for l in lines:
        k, deg = head_kind(l)
        if deg and k in ('skip', 'sub_skip', 'mixed', 'sub_mixed') and not re.search(r'collaborat|affiliat|external|committee', l, re.I):
            page_nonphd = True
            if k in ('skip', 'sub_skip') and re.search(r"(?i:master)|(?<![A-Za-z])(?:M\.?S\.?|M\.?Sc|MEng|M\.Eng|S\.?M\.?)(?![a-z])", l):
                page_masters = True  # 硕士生单列：其余只写 Students / Alumni 的名单按对比当作博士生
        if k in ('phd', 'sub_phd') and not MENTOR_HEAD.search(l):
            page_phd = True
        if k and k != 'end' and len(l) <= 60:
            if MENTOR_HEAD.search(l) and not re.search(r'advis|supervis', l, re.I):
                mentor = True
            elif k in ('phd', 'postdoc', 'amb', 'mixed') and re.search(r'advis|supervis', l, re.I):
                mentor = False
        if area is None:
            if k in ('phd', 'postdoc', 'amb', 'mixed', 'skip', 'gen'):
                area, n_area = n_area, n_area + 1
                cur, head, sub, since = ('skip' if mentor else k), l, None, 0
            continue
        if k == 'end' or (k is None and since >= 60):
            flush()
            area = None
            mentor = False
            continue
        if k in ('phd', 'postdoc', 'amb', 'mixed', 'skip', 'gen'):
            flush()
            cur, head, sub, since = ('skip' if mentor else k), l, None, 0
        elif k and k.startswith('sub_'):
            flush()
            if mentor:
                cur = 'skip'
            elif k != 'sub_inherit':  # "Current" / "Former"：沿用上一级的学位
                cur = k[4:]
            sub, since = l, 0
        else:
            lb = l.lstrip(' \t•·*-–—▪►')
            if (cur == 'amb' and since <= 1 and len(lb) > 60 and not NAME.match(lb)
                    and re.search(r'undergrad|master|high school|intern', lb, re.I) and not re.search(PHD_W, lb, re.I)):
                cur = 'skip'  # 栏目开头的说明："I am mentoring students in their undergraduate/master research"
            body.append(l)
            since = 0 if NAME.match(unglue(lb)) else since + 1  # 名单很长时不在第 60 行处截断
    flush()
    # 对比规则只在页面没有写明 PhD 的栏目时用：有 "Ph.D program" 又单列 "M.S program" 时，"Alumni" 照样分不清
    return out, (page_nonphd and not (page_masters and not page_phd))


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


def extra_pages(pid, name=''):
    """别的爬虫缓存里本人的页面（个人主页、CV、Prospective / Join 页）：recruit_pages（要出现本人的姓）、recruit_follow、advisor_pages。"""
    out = []
    sur = (fold(name).split() or [''])[-1]
    for sub, need_name in (('recruit_pages', True), ('recruit_follow', False), ('advisor_pages', False)):
        fn = os.path.join(ROOT, 'data/cache', sub, pid + '.json')
        if not os.path.exists(fn):
            continue
        try:
            d = json.load(open(fn, encoding='utf-8'))
        except Exception:
            continue
        for pg in d.get('pages') or []:
            t = pg.get('text') or ''
            if not t.strip() or pg.get('kind') == 'lab':
                continue  # 实验室网站由 fetch_lab_pages.py / lab_entries 处理
            if need_name and not (sur and sur in fold(t[:200000]).split()):
                continue
            out.append((pg.get('final') or pg.get('url') or '', t))
    return out


def load_texts(pid, homepage='', shared=frozenset(), name=''):
    texts = []
    for pg in load_pages(pid):
        # 主页是院系名录页时顺着去的个人主页（fetch_lab_pages.py --refetch-home，via=personal*）也算本人的
        if pg.get('text') and (personal(pg['url'], homepage or pg['url'], shared) or str(pg.get('via', '')).startswith('personal')):
            texts.append((pg['url'], pg['text']))
    for url, t in extra_pages(pid, name):
        if not any(t[:2000] == x[:2000] for _, x in texts):
            texts.append((url, t))
    for kind in ('home', 'cv'):
        f = os.path.join(PAGES, f'{pid}_{kind}.txt')
        if os.path.exists(f):
            first, _, body = open(f, encoding='utf-8', errors='replace').read().partition('\n')
            if body.strip() and not any(body[:2000] == t[:2000] for _, t in texts):
                texts.append((first.replace('#URL ', '').strip(), body))
    return texts


# ---------------------------------------------------------------- 实验室网站（fetch_lab_pages.py 抓的 HTML）
# 实验室 People / Team 页多是"卡片"：照片 + 名字（链到个人主页）+ 身份（"PhD Student"）。按 HTML 结构找每个名字所在的卡片，
# 身份看卡片里名字后面的短行（entry_label）和卡片所在的栏目标题（head_kind，h1–h6 及卡片外的短标题），
# 个人主页取名字上的链接 / 卡片里写着 Homepage、带房子 / 地球图标的链接，照片取卡片里的 <img>。
LAB = os.path.join(ROOT, 'data/cache/lab_pages')
BLOCK_TAGS = {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'td', 'th', 'dd', 'dt', 'article',
              'figcaption', 'figure', 'header', 'ul', 'ol', 'table', 'blockquote', 'aside', 'main'}
CAND_TAGS = {'a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'td', 'th', 'strong', 'b', 'span', 'div', 'dt', 'dd',
             'figcaption', 'em', 'label', 'font', 'u', 'i', 'article', 'section'}
HEAD_TAGS = {'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4, 'h5': 5, 'h6': 6}
# 实验室网站上 PI / 教职 / 职员的卡片（在读 / 毕业学生栏目以外）
STAFF = re.compile(r'^\W*(?:principal\s+investigator|\bp\.?i\.?\b|(?:lab\s+|group\s+|research\s+|program\s+)?(?:director|manager|coordinator|administrator)|'
                   r'co-?director|founder|(?:distinguished\s+|full\s+|adjunct\s+|research\s+|visiting\s+|clinical\s+)?(?:assistant\s+|associate\s+)?professor|'
                   r'faculty|staff|lecturer|instructor|research\s+(?:scientist|engineer|associate|staff|programmer|fellow)|'
                   r'(?:senior\s+|staff\s+)?(?:software\s+|research\s+|hardware\s+)?(?:engineer|scientist|developer)|technician|'
                   r'collaborator|alumni\s+faculty|affiliate)', re.I)
ADVISED = re.compile(r"(?i)(?<!co-)(?<!co)\b(?:advis(?:ed|or|ors)|supervis(?:ed|or))\b\s*(?:by|:|-|with)?\s*(?:prof\.?\s*|dr\.?\s*)?"
                     r"([A-Z][\w.'’-]+(?:\s+[A-Z][\w'’-]+){0,3})")
SOCIAL = re.compile(r'(?:^|\.)(?:twitter\.com|x\.com|facebook\.com|instagram\.com|youtube\.com|youtu\.be|github\.com|gitlab\.com|orcid\.org|'
                    r'researchgate\.net|dblp\.org|dblp\.uni-trier\.de|semanticscholar\.org|openreview\.net|arxiv\.org|doi\.org|acm\.org|'
                    r'ieee\.org|bsky\.app|mastodon\.\w+|medium\.com|substack\.com|threads\.net|tiktok\.com|zoom\.us|calendly\.com|'
                    r'wikipedia\.org|google\.com|goo\.gl|bit\.ly|weibo\.com|zhihu\.com|huggingface\.co|kaggle\.com|t\.me|wa\.me|'
                    r'scholar\.archive\.org|amazon\.\w+|apple\.com|spotify\.com|flickr\.com|vimeo\.com|discord\.\w+|slack\.com|'
                    r'notion\.site|linktr\.ee|cdn-cgi)$', re.I)
HOME_HINT = re.compile(r'(?i)home\s*-?\s*page|\bhome\b|website|web\s*site|\bweb\b|personal|\bsite\b|\bpage\b|globe|fa-link|\blink\b|'
                       r'homepage|bi-house|fa-house|house|\bwww\b')
BAD_IMG = re.compile(r'(?i)logo|icon|banner|placeholder|default|blank|spacer|no[-_ ]?(?:photo|image|img|pic)|silhouette|anonymous|'
                     r'unknown|generic|dummy|missing|empty|question|avatar[-_]?(?:default|placeholder)|user[-_]?(?:default|placeholder)|'
                     r'person[-_]?(?:placeholder|icon)|profile[-_]?(?:placeholder|default)|sprite|loading|spinner|pixel|transparent|'
                     r'favicon|emoji|badge|award|flag|qr|wordmark|seal|header|footer|background|\bbg\b|cover|hero|group[-_]?photo|'
                     r'team[-_]?photo|lab[-_]?photo|\bmap\b|scholar|linkedin|github|twitter|mail|orcid|\bcv\b')
BAD_IMG_CLS = re.compile(r'(?i)logo|icon|placeholder|emoji|badge|flag|sprite')  # class 里常有 "object-cover" 之类，只看这几个词
ALUM = re.compile(r'(?i)alum|former|past\b|graduated|previous|completed|emerit')
PERSONALISH = re.compile(r'(?i)\.github\.io|sites\.google\.com|/~|%7e|/(?:people|person|profile|faculty|users?|homes?|staff)/|\.(?:me|io|site|page|xyz)$')
# 新闻报道、数据中介、论文：不是个人主页
NOT_PERSONAL = re.compile(r'(?i)rocketreach|zoominfo|crunchbase|signalhire|contactout|/news/|/article|/story|/stories/|/blog/|/press|'
                          r'/item/|\.pdf|/papers?/|/pubs?/|/publications?/|/abs/|/doi/')


def _norm_ws(s):
    return re.sub(r'\s+', ' ', (s or '').replace('\xa0', ' ').replace('​', '')).strip()


def el_lines(el):
    """元素里的文字，按块级元素分行。"""
    out = []

    def rec(e):
        tag = e.tag if isinstance(e.tag, str) else ''
        if tag in ('script', 'style', 'noscript', 'svg', 'template'):
            return
        sep = '\n' if tag in BLOCK_TAGS else ' '
        out.append(sep)
        if e.text and tag:
            out.append(e.text)
        for c in e:
            rec(c)
            if c.tail:
                out.append(c.tail)
        out.append(sep)
    rec(el)
    return [x for x in (_norm_ws(l) for l in ''.join(out).split('\n')) if x]


def _img_src(img, base):
    """<img> 的图片地址：data-src / src，没有时取 srcset 里最大的一张。"""
    for a in ('data-src', 'data-lazy-src', 'data-original', 'src'):
        v = (img.get(a) or '').strip()
        if v and not v.startswith('data:'):
            return urljoin(base, v)
    ss = img.get('srcset') or img.get('data-srcset') or ''
    best, bw = None, -1
    for part in ss.split(','):
        bits = part.strip().split()
        if not bits or bits[0].startswith('data:'):
            continue
        m = re.match(r'(\d+)', bits[1]) if len(bits) > 1 else None
        w = int(m.group(1)) if m else 1
        if w > bw:
            best, bw = bits[0], w
    return urljoin(base, best) if best else None


def lab_cards(html, base, coauthors, me_key):
    """实验室页面上的人（按文档顺序）：[{name, k, same, rest, heads, links, imgs, card_text}]。"""
    import lxml.html
    try:
        doc = lxml.html.fromstring(html)
    except Exception:
        return []
    for bad in doc.xpath('//script|//style|//noscript|//svg|//nav|//footer|//form|//template|//iframe|//select'):
        bad.drop_tree()
    for cm in doc.xpath('//comment()|//processing-instruction()'):
        if cm.getparent() is not None:
            cm.drop_tree()
    elems =[e for e in doc.iter() if isinstance(e.tag, str)]
    order = {e: i for i, e in enumerate(elems)}
    text_of = {}

    def txt(e):
        if e not in text_of:
            text_of[e] = _norm_ws(' '.join(e.itertext()))  # 相邻的 <span> 之间补空格（"Chris Cox" + "Assistant Professor"）
        return text_of[e]
    # 1) 名字元素：整个元素的文字以一个人名开头、不超过 150 字
    cand = {}
    for e in elems:
        if e.tag not in CAND_TAGS:
            continue
        t = txt(e)
        if not t or len(t) > 150 or not t[0].isupper():
            continue
        ents = entries([t], coauthors)
        if len(ents) != 1:
            continue
        name = ents[0][0]
        if not t.startswith(name.split()[0]):
            continue
        # "(co-advised with <a>Vincent Sitzmann</a>)"、"Advisor: <a>...</a>"：是导师 / 合作者，不是这一项的人
        prev = e.getprevious()
        before = (prev.tail if prev is not None else e.getparent().text if e.getparent() is not None else '') or ''
        if re.search(r'(?i)(?:advis\w*|supervis\w*|mentor\w*|\bwith|\bby|\band|\bPIs?|host\w*)\s*[:(,]?\s*(?:prof\.?|dr\.?)?\s*$', before[-40:]):
            continue
        cand[e] = (name, key(name))
    # 只留最里层（<li><a>Alice Smith</a> (PhD 2019)</li> 里的 <a>）
    for e in list(cand):
        if e not in cand:
            continue
        k, nm = cand[e][1], cand[e][0]
        for p in e.iterancestors():
            if p in cand and (cand[p][1] == k or cand[p][0].startswith(nm) or txt(p).startswith(nm)):
                del cand[p]
    cand = {e: v for e, v in cand.items() if v[1] != me_key}
    # 2) 卡片：从名字元素往上走，直到上一层里还有别的名字或文字太长
    anc_keys = defaultdict(set)
    for e, (_, k) in cand.items():
        anc_keys[e].add(k)
        for p in e.iterancestors():
            anc_keys[p].add(k)
    cards = {}
    for e, (name, k) in sorted(cand.items(), key=lambda x: order[x[0]]):
        c = e
        while True:
            p = c.getparent()
            if p is None or p.tag in ('body', 'html') or len(anc_keys[p]) > 1 or len(txt(p)) > 1500:
                break
            c = p
        cards.setdefault((c, k), (order[c], c, e, name, k))  # 照片 alt + 标题里同一个名字只算一次
    cards = sorted(cards.values(), key=lambda x: x[0])
    in_card = set()
    for _, c, _, _, _ in cards:
        in_card.update(c.iter())
    # 3) 栏目标题：文档顺序扫一遍，h1–h6 以及卡片外、不含人名的短行（"PhD Students"、"Alumni"）
    stack, heads_at = [], {}
    by_start = defaultdict(list)
    for i, c, _, _, _ in cards:
        by_start[i].append(c)
    for i, e in enumerate(elems):
        for c in by_start.get(i, ()):
            heads_at[c] = list(stack)
        if e in in_card:
            continue
        lvl = HEAD_TAGS.get(e.tag)
        t = None
        if lvl is None:
            if e.tag not in ('p', 'div', 'span', 'strong', 'b', 'dt', 'th', 'summary', 'button', 'li', 'em', 'u', 'font', 'td', 'label'):
                continue
            if len(e) > 3 or any(x.tag in BLOCK_TAGS for x in e if isinstance(x.tag, str)):
                continue
            t = txt(e)
            if not t or len(t) > 60 or head_kind(t)[0] is None:
                continue
            lvl = 7
        t = t or txt(e)
        if not t or len(t) > 80:
            continue
        while stack and stack[-1][0] >= lvl:
            stack.pop()
        stack.append((lvl, t))
    links_count, img_count = Counter(), Counter()
    out = []
    for _, c, e, name, k in cards:
        lines = el_lines(c)
        # 名字所在那一行：名字后面同一行的文字（"Alice Smith (PhD 2019, now at Google)"）
        same, rest_lines, found = '', [], False
        for ln in lines:
            if not found and ln.startswith(name):
                same, found = ln[len(name):], True
            elif found:
                rest_lines.append(ln)
        if not found:
            rest_lines = [ln for ln in lines if name not in ln]
        if c is e and e.tag in ('a', 'span', 'b', 'strong', 'em', 'i', 'u', 'font'):
            # <br> 分隔的名单：卡片就是名字本身，同一行后面的文字（"[CS] → Postdoc at UMD"）到下一个 <br> / 下一个人为止
            extra = [e.tail or '']
            for sib in e.itersiblings():
                if not isinstance(sib.tag, str):
                    extra.append(sib.tail or '')
                    continue
                if sib.tag == 'br' or sib in cand or any(x in cand for x in sib.iterdescendants()):
                    break
                extra.append(' '.join(sib.itertext()) + ' ' + (sib.tail or ''))
            ex = _norm_ws(' '.join(extra))
            if ex and not same.strip():
                same = ' ' + ex[:200]
        name_set = set(e.iter())
        hs = []
        for a in c.iter('a'):
            href = (a.get('href') or '').strip()
            if not href or href.startswith(('#', 'mailto:', 'javascript:', 'tel:')):
                continue
            hint = ' '.join([a.text_content(), a.get('title') or '', a.get('aria-label') or '', a.get('class') or ''] +
                            [(x.get('class') or '') + ' ' + (x.get('alt') or '') + ' ' + (x.get('title') or '') + ' ' + (x.get('src') or '')
                             for x in a.iter() if x is not a and isinstance(x.tag, str)])
            on_name = (a is e or a in set(e.iterancestors()) or a in name_set
                       or fold(_norm_ws(a.text_content())) == fold(name)
                       or re.search(r'(?i)\b' + re.escape(name) + r"(?:['’]s)?\s+(?:home\s*page|website)", a.get('aria-label') or '') is not None)
            u = urljoin(base, href)
            hs.append(dict(href=u, hint=_norm_ws(hint)[:200], on_name=on_name))
        imgs = []
        for img in c.iter('img'):
            src = _img_src(img, base)
            if src:
                imgs.append(dict(src=src, alt=img.get('alt') or '', w=img.get('width') or '', h=img.get('height') or '',
                                 cls=img.get('class') or ''))
        links_count.update({x['href'] for x in hs})  # 按卡片数算：同一张卡片里照片和名字链到同一主页只算一次
        img_count.update({x['src'] for x in imgs})
        out.append(dict(name=name, k=k, same=same, rest=rest_lines[:8], heads=heads_at.get(c, []), links=hs, imgs=imgs,
                        card_text=' '.join(lines)[:1500]))
    # 页面上多张卡片共用的链接 / 图片：实验室公共链接、占位图
    for r in out:
        r['links'] = [x for x in r['links'] if links_count[x['href']] <= 1]
        r['imgs'] = [x for x in r['imgs'] if img_count[x['src']] <= 1]
    return out


def section_kind(heads):
    """卡片所在栏目的类别（从最内层往外看）和是不是毕业生栏目。"""
    alumni = any(ALUM.search(t) for _, t in heads)
    if any(MENTOR_HEAD.search(t) and not re.search(r'advis|supervis', t, re.I) for _, t in heads):
        return 'skip', alumni
    for _, t in reversed(heads):
        k, _ = head_kind(t)
        if k is None and len(t) <= 80 and re.search(r'(?i)\bpost-?\s?docs?\b|postdoctoral', t) and not re.search(PHD_W, t, re.I):
            k = 'postdoc'  # "Former research scientists / post-docs:"
        if k is None or k == 'sub_inherit':
            continue
        if k == 'end':
            return 'end', alumni
        return (k[4:] if k.startswith('sub_') else k), alumni
    return None, alumni


def card_label(card):
    """卡片里写的身份：同一行名字后面的文字，再看下面几行短行。返回 (label, 那一行)。"""
    cands = ([card['same']] if card['same'].strip(' ,;:-–—|') else []) + [ln for ln in card['rest'] if len(ln) <= 100][:3]
    for ln in cands:
        head = DEST.split(ln, 1)[0]
        if re.match(r'(?i)\W*post-?\s?doc|\W*post-?doctoral', head):
            return 'postdoc', ln  # "Postdoc · PhD in Machine Learning, CU Boulder"：PhD 是读博的学校
        if re.search(r'(?<![A-Za-z])(?:UG|Pre-PhD|Pre-doc\w*|Predoc\w*)(?![A-Za-z])', head):
            return 'nonphd', ln  # "UG CS 2026"、"Pre-PhD student"
        lb = entry_label(ln)
        if lb:
            return lb, ln
        if STAFF.match(head):
            return 'staff', ln
    return None, ''


def pick_homepage(card, name, lab_host, prof_prefix, shared_hosts):
    """卡片里的个人主页 'h'（不是实验室网站、导师主页、社交网站）、领英 'li'、Scholar 'g'。"""
    from fetch_recruit_pages import under
    toks = [t for t in fold(name).split() if len(t) >= 3]
    out, personal = {}, []
    for x in card['links']:
        h = x['href'].split('#')[0]
        if not h.startswith('http') or NOT_PERSONAL.search(h):
            continue
        pu = urlparse(h)
        host = pu.netloc.lower().split(':')[0].removeprefix('www.')
        if 'linkedin.com' in host and '/in/' in pu.path:
            out.setdefault('li', h)
            continue
        if host == 'scholar.google.com' and 'user=' in pu.query:
            out.setdefault('g', h)
            continue
        if host != 'sites.google.com' and (SOCIAL.search(host) or 'dblp' in host):
            continue
        if re.search(r'\.(?:pdf|ps|gz|zip|pptx?|docx?|jpe?g|png|gif|svg|bib|mp4)(?:\?|$)', pu.path, re.I) or '/cdn-cgi/' in pu.path:
            continue
        if host == lab_host or under(h, prof_prefix):
            continue  # 实验室网站本身、导师自己的主页
        if host in shared_hosts and not (PERSONAL_PATH.search(pu.path) or any(t in fold(pu.path) for t in toks)):
            continue  # 院系网站上不是本人目录的页面
        if not pu.path.strip('/') and host.endswith('.edu') and host.count('.') <= 2:
            continue  # 学校 / 院系首页
        personal.append((x, h, host, pu))
    pick = next((h for x, h, _, _ in personal if x['on_name']), None) \
        or next((h for x, h, _, _ in personal if HOME_HINT.search(x['hint'])), None)
    if not pick:
        named = {h for x, h, host, pu in personal if any(t in fold(host + ' ' + pu.path) for t in toks)}
        if len(named) == 1:
            pick = named.pop()
        elif len({h for _, h, _, _ in personal}) == 1 and PERSONALISH.search(personal[0][2] + personal[0][3].path):
            pick = personal[0][1]  # 卡片里唯一的外链，且像个人主页（github.io、sites.google.com、~user）
    if pick:
        out['h'] = pick
    return out


def pick_photo(card, name):
    for im in card['imgs']:
        src = im['src']
        if not src.startswith('http'):
            continue
        fname = urlparse(src).path.rsplit('/', 1)[-1]
        if re.search(r'\.svg$', fname, re.I) or not fname or BAD_IMG.search(fname) or BAD_IMG_CLS.search(im['cls']):
            continue
        dims = [int(m.group(1)) for v in (im['w'], im['h']) for m in [re.match(r'\s*(\d+)\s*(?:px)?\s*$', v or '')] if m]
        if dims and min(dims) < 80:
            continue
        if im['alt'] and BAD_IMG.search(im['alt']) and not set(fold(im['alt']).split()) & set(fold(name).split()):
            continue
        return src
    return None


def load_lab(pid):
    import gzip
    f = os.path.join(LAB, pid + '.json.gz')
    if not os.path.exists(f):
        return None
    with gzip.open(f, 'rt', encoding='utf-8') as fh:
        return json.load(fh)


def lab_page_texts(lab):
    """实验室 People 页的纯文字（给卡片法没认出的名字走主页的栏目规则）。"""
    from fetch_pages import TextAndLinks
    res = []
    for pg in lab['pages']:
        if not pg.get('html') or pg.get('kind') == 'join':
            continue
        tp = TextAndLinks()
        try:
            tp.feed(pg['html'])
        except Exception:
            continue
        text = re.sub(r'[ \t\r\f\v]+', ' ', ''.join(tp.out))
        res.append((pg.get('final') or pg['url'], re.sub(r'\n\s*\n+', '\n', text)))
    return res


COLLAB_HEAD = re.compile(r'(?i)collaborat|affiliat|visit|mentee|mentor(?:ed|ing)|\bguests?\b|friends|external|interns?\b')


def person_like(name):
    """名字里至少有一个词在 dblp 作者名里出现过（"Urban Forager" 这种不要）。"""
    return any(NAME_VOCAB[fold(t)] > 0 for t in name.split() if len(t.strip('.')) > 1)


def lab_entries(p, lab, coauthors, me_key, shared_hosts, multi_pi, uni_names=None, own_lab=True):
    """一位教授实验室网站上的博士生 / 博士后：[dict(name, kind, alumni, note, rest, url, links, ph)]，以及卡片法见过的名字。"""
    from fetch_recruit_pages import home_prefix
    prof_prefix = home_prefix(p['homepage'])
    sur = fold(p['name']).split()[-1] if p['name'] else ''
    out, decided = [], set()
    for pg in lab['pages']:
        if not pg.get('html') or pg.get('kind') == 'join':
            continue
        base = pg.get('final') or pg['url']
        lab_host = urlparse(base).netloc.lower().split(':')[0].removeprefix('www.')
        cards = lab_cards(pg['html'], base, coauthors, me_key)
        if len(cards) > 120:
            continue  # 太多人：多半是研究所 / 院系名录
        labs = [card_label(c) for c in cards]
        secs = [section_kind(c['heads']) for c in cards]
        page_nonphd = any(lb in ('nonphd', 'class') for lb, _ in labs) or any(s in ('skip', 'mixed') for s, _ in secs)
        # 页面上除了本人还有 >=2 位教授（研究组 / 院系方向页）：学生要和本人合写过论文或卡片里写着本人
        n_fac = sum(1 for c, (lb, ln) in zip(cards, labs) if lb == 'staff' and re.search(r'(?i)professor|faculty', ln))
        multi_page = multi_pi or n_fac >= 2
        # 严格标准（只用于主页名单上没有的新人）：列着 >=3 位教授的研究组 / 院系页、或不像是本人自己实验室的，要合写过论文
        group_page = multi_pi or n_fac >= 3 or (uni_names is not None and many_faculty(lab_page_text(pg['html']), p, uni_names, 3))
        contrast = any(lb in ('nonphd', 'class') for lb, _ in labs) or any(s in ('skip',) for s, _ in secs)
        for c, (lb, lline), (sec, alumni) in zip(cards, labs, secs):
            k = c['k']
            decided.add(k)
            if sec == 'end' or lb in ('drop', 'nonphd', 'class') or (lb == 'staff' and not alumni):
                continue
            if lb == 'postdoc' or (sec == 'postdoc' and lb != 'phd'):
                kind = 'postdoc'
            elif lb == 'phd':
                kind = 'phd'
            elif sec in ('skip', 'mixed'):
                continue
            elif sec == 'phd' or (sec == 'amb' and not page_nonphd):
                kind = 'phd'
            else:
                continue  # "Team" / "People" 下没写身份的卡片：分不清
            text_all = c['card_text'] + ' ' + c['same']
            # 写着导师是别人："Advisor: Prof. Smith"，卡片里没有本人的姓
            am = ADVISED.search(text_all)
            if am and sur and sur not in fold(text_all).split():
                continue
            if multi_page and k not in coauthors and not (sur and sur in fold(text_all).split()):
                continue  # 几位教授共用的实验室网站：要和本人合写过论文
            note = ''
            if c['same'].strip(' ,;:-–—|'):
                note = clean_note(re.split(r'\b(?:Former|Previous(?:ly)?|Past)\b', c['same'])[0])
            elif alumni:
                for ln in c['rest'][:3]:
                    parts = DEST.split(ln, 1)
                    if len(parts) > 1 and parts[-1].strip(' :,'):
                        note = clean_note(parts[-1])
                        break
                    if lb == 'staff' and ln == lline:
                        note = clean_note(ln)
                        break
            links = pick_homepage(c, c['name'], lab_host, prof_prefix, shared_hosts)
            ph = pick_photo(c, c['name'])
            explicit = (lb == 'phd' and kind == 'phd') or (lb == 'postdoc' and kind == 'postdoc') or \
                       (lb is None and sec == 'phd' and kind == 'phd') or (lb is None and sec == 'postdoc' and kind == 'postdoc')
            amb_ok = lb is None and sec == 'amb' and not alumni and contrast and kind == 'phd'
            co = k in coauthors
            strict = ((explicit or amb_ok)
                      and not any(COLLAB_HEAD.search(t) for _, t in c['heads'])
                      and person_like(c['name'])
                      and (co or not (group_page or not own_lab))
                      and (co or explicit or bool(links.get('h')) or bool(ph)))
            out.append(dict(name=c['name'], kind=kind, alumni=alumni, note=note, rest=' '.join([c['same']] + c['rest'][:3]),
                            url=base, links=links, ph=ph, sec=sec, lb=lb, strict=strict))
    return out, decided

AF_DEST = re.compile(r'(?i)\b(?:now|currently|first\s+(?:position|job|employment)|current(?:ly)?\s+(?:position|at)|next(?:\s+position)?|'
                     r'placement|went\s+on\s+to|moved\s+to|then)\b\s*[:,-]?\s*(?:(?:at|with|an?|the)\b\s*)?|→|⇒|->|=>|➔|»|>>')
AF_DEG = re.compile(r"(?i)(?<![A-Za-z])(?:ph\.?\s?d|d\.?phil|doctora\w*|m\.?s\.?c?|m\.?eng|b\.?s\.?c?|b\.?a|master\w*|bachelor\w*|graduat\w*|defended|thesis|dissertation)(?![a-z])")
AF_YEAR = re.compile(r"(?i)\b(?:fall|spring|summer|winter|autumn)\b\s*['’]?\s*(?:\d{2,4})?|\b(?:19|20)\d{2}\b(?:\s*[-–]\s*(?:(?:19|20)?\d{2}|present|now))?|['’]\d{2}\b")
AF_ORG = re.compile(r'(?i)universit|univ\.|institut|college|school|\blabs?\b|laborator|research|\binc\b|corp|\bllc\b|\bltd\b|google|microsoft|amazon|'
                    r'\bmeta\b|facebook|apple|\bibm\b|intel|nvidia|adobe|baidu|alibaba|tencent|huawei|samsung|uber|netflix|linkedin|oracle|cisco|'
                    r'qualcomm|bloomberg|waymo|tesla|openai|deepmind|salesforce|bytedance|vmware|professor|faculty|lecturer|scientist|engineer|'
                    r'post-?doc|fellow|founder|ceo|cto|startup|\bat\b|@|\bai\b|tech\b|center|centre|hospital|bank|capital|systems|software|'
                    r'consult|analyst|developer|manager|director|government|national|academy|foundation|\bco\.?\b|group|ventures|associates')


def clean_af(note, rest):
    """导师主页名单上写的去向，只留像现在单位 / 职位的一段：
    有 now / currently / → / First position 时取它后面的；没有时，学位或年份写在机构前面（"PhD 2019, Google"）取后面的，
    机构写在年份 / 学位前面（"Georgia Tech (2025)"、"PhD MIT"）是读博的学校，不要；去掉年份、学期、学位词、"co-advised with…"、
    不配对的括号；被 70 字截断的、读起来像论文题目（"Structured Gradient Boosting"）的不要。"""
    raw = re.sub(r'\s+', ' ', rest or note or '').strip()
    m = AF_DEST.search(raw)
    if m:
        t = raw[m.end():]
    else:
        t = raw
        hit = AF_DEG.search(t) or AF_YEAR.search(t)
        if hit:
            before = t[:hit.start()].strip(' ,;:([-–—)')
            if AF_DEG.search(t) and AF_DEG.search(t).start() == hit.start() and not re.match(r"[\s.,:'’(]{0,3}(?:(?:19|20)\d{2}|['’]\d{2})", t[hit.end():]):
                return None  # "PhD MIT"、"(MS)"：学位信息
            if re.search(r'[A-Za-z]{3,}', before):
                return None  # "Georgia Tech (2025"：读博的学校 + 年份
            t = t[hit.end():]
    t = re.split(r'(?i)\(?\s*\b(?:co-?advis\w*|(?:jointly\s+)?(?:advised|supervised|mentored)\s+(?:with|by)|primary\s+advisor|advisors?:)', t)[0]
    t = re.sub(r"\(?\s*\bwith\s+(?:prof\.?\s+|dr\.?\s+)?[A-Z][\w.'’-]*(?:\s+[A-Z][\w.'’-]*){0,3}\s*\)?", ' ', t)
    t = AF_YEAR.sub(' ', t)
    t = AF_DEG.sub(' ', t)
    t = re.sub(r'(?i)\b(?:student|candidate|expected|alumn\w*|in\s+progress|current)\b', ' ', t)
    t = re.sub(r'\(\s*\)|\[\s*\]', ' ', t)
    if t.count('(') != t.count(')') or re.fullmatch(r'\s*\(([^()]*)\)\s*', t):
        t = t.replace('(', ' ').replace(')', ' ')
    t = re.sub(r'\s+', ' ', t).strip(' \t,;:.-–—/|')
    t = re.sub(r'^(?:at|in|with|from|to|and|an?|the)\s+', '', t, flags=re.I).strip(' ,;:.-–—')
    if len(t) >= 70 or len(t) < 2 or not re.search(r'[A-Z]', t):
        return None
    if not AF_ORG.search(t):
        toks = [x for x in re.findall(r"[A-Za-z][\w'’-]*", t)]
        if not toks or len(toks) > 4:
            return None
        commonish = [x for x in toks if len(x) > 2 and fold(x) in WORDISH and not x.isupper()]
        if commonish and len(commonish) * 2 >= len(toks) or (len(toks) > 1 and any(TITLE_SUFFIX.search(fold(x)) and len(x) >= 7 for x in toks)):
            return None  # 读起来像论文题目 / 普通短语
    return t


def many_faculty(text, p, uni_names, n=2):
    """页面上出现了同校至少 n 位别的教授的全名。"""
    t = ' ' + fold(text[:200000]) + ' '
    hits = 0
    for q, nm in uni_names.get(p['uni'], ()):
        if q != p['id'] and (' ' + nm + ' ') in t:
            hits += 1
            if hits >= n:
                return True
    return False


CUR_MAX_FIRST = 2026 - 7  # 与教授最早合写论文早于这一年的，不再标为在读


def first_year(first_co, k):
    """最早合写年份；名字写法不同（"Matt Conlen" / "Matthew Conlen"）时按同姓、名字前 3 个字母相同找。"""
    if k in first_co:
        return first_co[k]
    t = k.split()
    if len(t) < 2 or len(t[0]) < 3:
        return None
    ys = [y for kk, y in first_co.items() if kk.split()[-1] == t[-1] and (kk.split()[0][:3] == t[0][:3])]
    return min(ys) if ys else None


def first_coauthor_years(pubs):
    """教授的论文里，每个合作者（匹配键）最早一起发表的年份。"""
    out = {}
    for pub in pubs:
        y = pub[1]
        if not isinstance(y, int):
            continue
        for a in pub[2]:
            k = key(display_name(a))
            if k not in out or y < out[k]:
                out[k] = y
    return out


def self_abbrev(name, prof_name):
    """名单上的名字其实是教授本人的缩写（"T. Bretl" = Timothy Bretl）。"""
    a, b = fold(name).split(), fold(prof_name).split()
    return bool(a and b) and a[-1] == b[-1] and a[0][:1] == b[0][:1] and len(a[0]) <= 2


# 不是人名的大写词组（奖项 / 基金 / 机构名）
NOT_PERSON = re.compile(r'(?i)\b(?:clare\s+boothe|sloan\s+kettering|grace\s+hopper|ann\s+arbor|palo\s+alto|la\s+jolla|new\s+haven|'
                        r'santa\s+barbara|santa\s+cruz|los\s+alamos|salt\s+lake|college\s+park|chapel\s+hill|stony\s+brook|'
                        r'ada\s+lovelace|alan\s+turing|marie\s+curie|boothe\s+luce|kettering|fellowship|scholarship|foundation|'
                        r'institute|university|laborator|award|prize|center|centre|hospital|college)\b')


def compact(name):
    return fold(display_name(name)).replace(' ', '')


def lab_page_text(html):
    return re.sub(r'<[^>]+>', ' ', re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', html))


def extract_lab(profs, hp_recs, idx, dropped, by_key, dblp_of, co_of, me_key, uni_names):
    """实验室网站上的学生（fetch_lab_pages.py 的缓存）。和主页名单是同一人时（同名，或 "Li-Hao Wang" / "Lihao Wang"
    这种写法不同）改用主页上的写法，build_graph 按名字并到同一个节点上，只补主页 / 照片，不新增人。"""
    import fetch_recruit_pages as frp
    labs = {}
    for p in profs:
        if p.get('status') == 'former' or not p['homepage'].startswith('http'):
            continue
        lab = load_lab(p['id'])
        if lab and lab.get('lab') and lab['lab'].get('has_name'):
            labs[p['id']] = lab
    lab_freq = Counter(frp.norm(lab['lab']['url']) for lab in labs.values())
    shared_hosts = {h.lower().removeprefix('www.') for h, c in SHARED_HOSTS.items() if c >= 3}
    hp_names = defaultdict(dict)
    for r in hp_recs:
        hp_names[r['prof']][compact(r['name'])] = r['name']
        t = fold(display_name(r['name'])).split()
        if len(t) >= 2:
            hp_names[r['prof']][''.join(t[-1:] + t[:-1])] = r['name']  # "Wang Lihao"
    pmap = {p['id']: p for p in profs}
    cs_path = os.path.join(ROOT, 'data/enrich/career_start.json')
    career = json.load(open(cs_path, encoding='utf-8')) if os.path.exists(cs_path) else {}  # 教授 dblp 入行年份
    out = []
    stats = Counter()
    for pid, lab in labs.items():
        p = pmap[pid]
        my_sur_ = (fold(p['name']).split() or [''])[-1]
        co_ids = {a for pub in idx.get(p['dblp'], []) for a in pub[2]}
        co_raw = defaultdict(set)
        for a in co_ids:
            co_raw[key(display_name(a))].add(a)
        coauthors = set(co_raw)
        first_co = first_coauthor_years(idx.get(p['dblp'], []))
        # 列着同校 >=5 位教授的：院系 / 方向的大组页面（"Stanford ML Group"、"Theory Group"），不是本人的实验室
        if any(pg.get('html') and pg.get('kind') == 'people' and many_faculty(lab_page_text(pg['html']), p, uni_names, 5)
               for pg in lab['pages']):
            stats['labs_area_group_pages'] += 1
            continue
        multi = lab_freq[frp.norm(lab['lab']['url'])] > 1 or any(
            pg.get('html') and pg.get('kind') == 'people' and many_faculty(lab_page_text(pg['html']), p, uni_names) for pg in lab['pages'])
        title0 = ' '.join(m.group(1) for pg in lab['pages'][:1] if pg.get('html')
                          for m in [re.search(r'(?is)<title[^>]*>(.*?)</title>', pg['html'])] if m)
        own_lab = lab['lab'].get('prio') in (4, 6) or (my_sur_ and my_sur_ in fold(lab['lab'].get('via', '') + ' ' + title0 + ' ' + lab['lab']['url']))
        ents, decided = lab_entries(p, lab, coauthors, me_key[pid], shared_hosts, multi, uni_names, own_lab)
        # 以别人命名的实验室（"Langmead Lab"，页面上又有 Ben Langmead）：是同事的实验室，本人只是合作者
        title = lab['lab'].get('via', '') + ' ' + ' '.join(
            m.group(1) for pg in lab['pages'][:1] if pg.get('html')
            for m in [re.search(r'(?is)<title[^>]*>(.*?)</title>', pg['html'])] if m)
        own = set(fold(p['name']).split())
        lasts = {k.split()[-1] for k in decided if ' ' in k}
        other = [w for w in re.findall(r"\b([A-Z][a-z]{2,})(?:['’]s)?\s+(?:Lab|Laboratory|Group|Research\s+Group)\b", title)
                 if fold(w) not in own and fold(w) in lasts]
        if other:
            stats['labs_named_after_someone_else'] += 1
            continue
        # 卡片法没见过的名字：页面文字按主页的栏目规则
        for url, text in lab_page_texts(lab):
            secs, page_nonphd = sections(text)
            grp_t = many_faculty(text, p, uni_names, 3)
            for area, sec_kind, head, sub, body in secs:
                if sec_kind == 'skip':
                    continue
                ents_t = entries(body, coauthors)
                if len(ents_t) > 80:
                    continue
                alumni = bool(ALUM.search(f'{head} {sub or ""}'))
                for name, note, rest, label in ents_t:
                    if key(name) in decided or label in ('drop', 'nonphd', 'class'):
                        continue
                    if sec_kind == 'postdoc' or label == 'postdoc':
                        kind = 'postdoc'
                    elif label == 'phd' or sec_kind == 'phd' or (sec_kind == 'amb' and not page_nonphd):
                        kind = 'phd'
                    else:
                        continue
                    if multi and key(name) not in coauthors:
                        continue
                    explicit = label in ('phd', 'postdoc') or sec_kind in ('phd', 'postdoc')
                    co = key(name) in coauthors
                    strict = explicit and person_like(name) and not COLLAB_HEAD.search(f'{head} {sub or ""}') and (co or own_lab) \
                        and (co or not grp_t)
                    ents.append(dict(name=name, kind=kind, alumni=alumni, note=note if alumni or kind == 'postdoc' else '',
                                     rest=rest, url=url, links={}, ph=None, sec=sec_kind, lb=label, text=True, strict=strict))
        seen = {}
        recs = []
        hp_keys_p = {key(n) for n in hp_names[pid].values()}
        for e in ents:
            name = e['name']
            k = key(name)
            if k == me_key[pid] or (pid, k) in dropped:
                continue
            if k not in hp_keys_p and compact(name) in hp_names[pid]:
                name = hp_names[pid][compact(name)]
                k = key(name)
                stats['renamed_to_homepage_form'] += 1
            if k not in hp_keys_p and not e.get('strict'):
                stats['new_people_failed_strict'] += 1
                continue  # 主页名单上没有的新人：要明确写着 PhD / 博后，且有合著、主页或照片之一，见 lab_entries
            if (k, e['kind']) in seen:
                r = seen[(k, e['kind'])]
                for f2, v in list(e['links'].items()) + [('ph', e['ph'])]:
                    if v and not r.get(f2):
                        r[f2] = v
                continue
            r = dict(prof=pid, rel='student', name=name, type=e['kind'], source='homepage', url=e['url'], via='lab')

            def same_person(q):
                if dblp_of[q] in co_ids or p['dblp'] in co_of(q):
                    return True
                raw = co_raw.get(k, ())
                return bool(raw) and not all(re.search(r'\s\d{4}$', a) for a in raw)
            fac = [q for q in by_key.get(k, []) if q != pid and same_person(q)]
            if len(fac) == 1:
                r['other_prof'] = fac[0]
            note = e['note']
            af = clean_af(note, note) if note and (e['alumni'] or e['kind'] == 'postdoc') else None
            if af:
                r['af'] = af
            if (e['kind'] == 'phd' and not e['alumni'] and not ((first_year(first_co, k) or 9999) < CUR_MAX_FIRST) and not re.search(r'\b(?:19|20)\d\d\s*[-–]\s*(?:19|20)\d\d\b', e['rest'])
                    and not re.search(r'\bnow\b|graduat|alum|former|\b(?:19[89]\d|20[01]\d|202[0-3])\b', f"{note} {e['rest']}", re.I)):
                r['cur'] = 1
            for f2, v in e['links'].items():
                r[f2] = v
            if e['ph']:
                r['ph'] = e['ph']
            # 毕业 / 入组年份早于本人入行：院系或大组的历届名单（"Tim Davis (PhD 1989)"），不是本人的学生
            yrs = [int(y) for y in re.findall(r'\b(19[5-9]\d|20[0-3]\d)\b', e['rest'])]
            if yrs and career.get(pid) and min(yrs) < career[pid] - 1:
                stats['before_advisor_career'] += 1
                recs_skip = True
            else:
                recs_skip = False
            if recs_skip:
                continue
            r['_co'] = k in coauthors or bool(STUDENTISH.search(e['rest'])) or e['sec'] in ('phd', 'postdoc')
            r['_dbg'] = (e['sec'], e['lb'], bool(e.get('text')))
            seen[(k, e['kind'])] = r
            recs.append(r)
        if len(recs) > 40 and sum(r['_co'] for r in recs) / len(recs) < 0.25 and len(coauthors) >= 20:
            recs = [r for r in recs if r['_co']]
        for r in recs:
            r.pop('_co')
        out.extend(recs)
    hp_keys = {(r['prof'], key(r['name'])) for r in hp_recs}
    stats['labs_used'] = len(labs)
    stats['records'] = len(out)
    stats['new_people'] = sum((r['prof'], key(r['name'])) not in hp_keys for r in out)
    stats['with_h'] = sum(bool(r.get('h')) for r in out)
    stats['with_ph'] = sum(bool(r.get('ph')) for r in out)
    stats['from_text_rules'] = sum(r['_dbg'][2] for r in out)
    print('lab sites', dict(stats), Counter(r['type'] for r in out))
    if '--lab-debug' in sys.argv:
        json.dump(out, open(os.path.join(ROOT, 'data/cache/students_lab_debug.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    for r in out:
        r.pop('_dbg')
    return out


def main():
    profs = load_profs()
    me_key = {p['id']: key(p['name']) for p in profs}
    dblp_of = {p['id']: p.get('dblp') for p in profs}
    _co_of = {}

    def co_of(q):  # 教授 q 在 dblp 上的合作者标识
        if q not in _co_of:
            _co_of[q] = {a for pub in idx.get(dblp_of[q], []) for a in pub[2]}
        return _co_of[q]
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
    all_texts = {p['id']: load_texts(p['id'], p['homepage'], shared, p['name']) for p in profs}
    from fetch_recruit_pages import home_prefix, under
    uni_names = defaultdict(list)  # 同校教授的全名（小写），找"列着好几位教授"的公共页面
    for q in profs:
        if len(fold(q['name']).split()) >= 2:
            uni_names[q['uni']].append((q['id'], fold(q['name'])))
    my_sur = {q['id']: (fold(q['name']).split() or [''])[-1] for q in profs}
    n_shared_skip = 0
    build_common(t for ts in all_texts.values() for _, t in ts)
    build_vocab(idx, profs)
    n_coauthors = {}
    recs = []
    dropped = {}
    for p in profs:
        seen = set()
        co_ids = {a for pub in idx.get(p['dblp'], []) for a in pub[2]}  # dblp 作者标识（"Bo Li 0026"）
        first_co = first_coauthor_years(idx.get(p['dblp'], []))
        co_raw = defaultdict(set)  # 匹配键 -> dblp 作者标识
        for a in co_ids:
            co_raw[key(display_name(a))].add(a)
        coauthors = set(co_raw)
        n_coauthors[p['id']] = len(coauthors)
        for url, text in all_texts[p['id']]:
            is_pdf = url.lower().split('?')[0].endswith('.pdf') or text.lstrip().startswith('%PDF')
            # 系里统一模板的个人页（同一主机上有很多教授、路径不带 ~）导航菜单里常有 Students 栏目，按严格规则处理
            strict = is_pdf or (SHARED_HOSTS[urlparse(url).netloc] > 2
                                and not PERSONAL_PATH.search(urlparse(url).path))
            secs, page_nonphd = sections(text)
            n_names = sum(len(body) for _, k_, _, _, body in secs if k_ != 'skip')
            directory = not is_pdf and n_names > 60 and (
                (SHARED_HOSTS[urlparse(url).netloc] > 2 and not PERSONAL_PATH.search(urlparse(url).path))
                or not under(url, home_prefix(p['homepage'])))
            # 院系 / 方向的公共 People 页（列着同校好几位教授，如 hci.nd.edu/people/）：这一项里要写着本人的姓
            shared_pg = (url.rstrip('/') != p['homepage'].rstrip('/') and not under(url, home_prefix(p['homepage']))
                         and many_faculty(text, p, uni_names))
            per_area = Counter()
            parsed = []
            for area, sec_kind, head, sub, body in secs:
                if sec_kind == 'skip':
                    # 硕士 / 本科 / REU / 访问 / 实习 / 合作者 / 论文评审栏目。访问学者 / 合作者栏目里的人，
                    # 别的页面上写成 PhD 也不算（"Visiting PhD student" 在主页上常简写成 "(PhD)"）；
                    # 实习 / REU / 本科 / 硕士不在此列：常有人先在组里实习或读硕士、后来读博
                    if VISITOR.search(f'{head} {sub or ""}'):
                        for e in entries(body, coauthors):
                            dropped[(p['id'], key(e[0]))] = True
                    continue
                ents = entries(body, coauthors)
                per_area[area] += len(ents)
                parsed.append((area, sec_kind, head, sub, ents))
            is_cv = is_pdf or bool(re.search(r'(?i)(?:^|[/_.-])(?:cv|vita|vitae|resume)(?:[/_.-]|$)', urlparse(url).path))
            sub_seen = Counter()
            for area, sec_kind, head, sub, ents in parsed:
                if per_area[area] > 80:
                    continue  # 太长，多半不是学生名单
                if is_cv:
                    sub_seen[(area, (sub or '').strip().lower())] += 1
                    if sub and sub_seen[(area, sub.strip().lower())] > 1:
                        continue
                    ents = [e for e in ents if person_like(e[0])]
                alumni = bool(re.search(r'alum|former|past|graduated|previous|completed', f'{head} {sub or ""}', re.I))
                for name, note, rest, label in ents:
                    k = key(name)
                    if label == 'drop':
                        if VISITOR.search(DEST.split(rest, 1)[0]):
                            dropped[(p['id'], k)] = True  # 写着访问学者 / 合作者 / 论文评审：别的页面上也不算
                        continue
                    if sec_kind == 'postdoc' and label in ('nonphd', 'class'):
                        continue  # 博士后栏目里混着的 "MEng Student"
                    if sec_kind == 'postdoc':
                        kind = 'postdoc'  # 博士后栏目里的 "PhD MIT 2010" 是他们读博的地方
                    elif label == 'postdoc':
                        kind = 'postdoc'
                    elif label == 'phd':
                        kind = 'phd'
                    elif label in ('nonphd', 'class') and (label == 'nonphd' or sec_kind in ('amb', 'mixed')):
                        continue  # 硕士 / 本科
                    elif sec_kind in ('mixed', 'gen'):
                        continue  # "PhD and MS Students" / "Members" 栏目里没写学位的项
                    elif sec_kind == 'phd':
                        kind = 'phd'
                    elif page_nonphd:
                        continue  # 只写 Students / Alumni、页面上另有硕士 / 本科等栏目：分不清是不是博士生
                    else:
                        kind = 'phd'
                    if k == me_key[p['id']] or (k, kind) in seen or self_abbrev(name, p['name']):
                        continue
                    # PDF 版 CV 排版乱、常混进论文题目：要么和这位教授合写过论文，要么备注里写着 PhD/student 等
                    if directory and k not in coauthors:
                        continue  # 院系名录页（几十上百人）：只认合写过论文的
                    if strict and sec_kind != 'phd' and k not in coauthors and not STUDENTISH.search(rest):
                        continue
                    if shared_pg and not (my_sur[p['id']] and my_sur[p['id']] in fold(rest).split()):
                        n_shared_skip += 1
                        continue
                    seen.add((k, kind))
                    r = dict(prof=p['id'], rel='student', name=name, type=kind, source='homepage', url=url)
                    # 学生后来也当了教授：合写过论文才认定是同一人。dblp 标识对得上（任一方向）就认；
                    # 否则按名字认，但 dblp 已用编号把同名的人分开、合写的又不是这位教授时不认——
                    # "Bo Li" 这类常见名字，dblp 里 "Bo Li 0020" 和名单里的教授 "Bo Li 0026" 是两个人；
                    # 不带编号的别名照样按名字认（"Martha Mercaldi Kim" = 教授 "Martha A. Kim"）
                    def same_person(q):
                        if dblp_of[q] in co_ids or p['dblp'] in co_of(q):
                            return True
                        raw = co_raw.get(k, ())
                        return bool(raw) and not all(re.search(r'\s\d{4}$', a) for a in raw)
                    fac = [q for q in by_key.get(k, []) if q != p['id'] and same_person(q)]
                    if len(fac) == 1:
                        r['other_prof'] = fac[0]
                    af = clean_af(note, rest) if note and (alumni or kind == 'postdoc') else None
                    if af:
                        r['af'] = af
                    # 列在"当前学生"里、备注也没写去向或毕业年份：网站上标为在读学生
                    if (kind == 'phd' and not alumni
                            and not re.search(r'\bnow\b|graduat|alum|former|\b(?:19[89]\d|20[01]\d|202[0-3])\b', f'{note} {rest}', re.I)
                            and not ((first_year(first_co, k) or 9999) < CUR_MAX_FIRST)):
                        r['cur'] = 1  # 和教授第一篇合写论文已是 7 年多以前：多半早毕业了，页面没更新
                    own_label = entry_label(rest)
                    r['_sig'] = dict(
                        explicit=sec_kind in ('phd', 'postdoc') or own_label in ('phd', 'postdoc'),
                        next_only=label in ('phd', 'postdoc') and own_label is None and sec_kind not in ('phd', 'postdoc'),
                        co=k in coauthors,
                        own=(under(url, home_prefix(p['homepage'])) and not is_pdf) or is_cv,
                        contrast=page_nonphd or any(k_ in ('skip', 'postdoc') for _, k_, _, _, _ in secs),
                        cv=is_cv)
                    r['_co'] = k in coauthors or bool(STUDENTISH.search(rest)) or sec_kind == 'phd'  # rest：备注清洗前的原文（"Ph.D." 会被 clean_note 去掉）；写明 PhD 的栏目不按合著比例删
                    recs.append(r)
    recs = [r for r in recs if (r['prof'], key(r['name'])) not in dropped]
    # 同一人在一个页面记为博士后、另一页面记为博士生：两条都留（有人在同一导师处读博后又做博士后）
    # 名单过长、和教授合写过论文的比例又很低：多半抓到了院系名录或课程名单，只保留合写过 / 注明是学生的
    by_prof = defaultdict(list)
    for r in recs:
        by_prof[r['prof']].append(r)
    keep = []
    for pid, rs in by_prof.items():
        co_rate = sum(r['_co'] for r in rs) / len(rs)
        # 没有 dblp 合作者数据（或很少）的教授没法这样判断，不能把大组整组丢掉
        if len(rs) > 40 and co_rate < 0.25 and n_coauthors.get(pid, 0) >= 20:
            rs = [r for r in rs if r['_co']]
        keep.extend(rs)
    recs = keep
    # 召回那一轮（2026-11）新增的记录（不在 students_homepage_baseline.json 里的）：只有高可信的直接用，其余标 "conf":"low"，
    # build_graph 跳过、留着以后再看。高可信 = 写明 PhD / 博后（栏目或本行，不是只靠下一行）+ 至少一个旁证（和教授合写过论文，
    # 或在本人网址 / CV 里且页面另有硕士本科 / 博后栏目可对照）+ 名字的名和姓都在 dblp 作者名里出现过、不在奖项 / 机构名单里
    base_path = os.path.join(ROOT, 'data/enrich/students_homepage_baseline.json')
    baseline = {tuple(x) for x in json.load(open(base_path, encoding='utf-8'))} if os.path.exists(base_path) else None
    n_low = 0
    for r in recs:
        sig = r.pop('_sig', None)
        if baseline is None or sig is None or (r['prof'], key(r['name']), r['type']) in baseline:
            continue
        toks = [t for t in fold(r['name']).split() if len(t) > 1]
        name_ok = len(toks) >= 2 and NAME_VOCAB[toks[0]] > 0 and NAME_VOCAB[toks[-1]] > 0 and not NOT_PERSON.search(r['name'])
        high = (sig['explicit'] and not sig['next_only'] and name_ok
                and (sig['co'] or (sig['own'] and sig['contrast'])))
        if not high:
            r['conf'] = 'low'
            n_low += 1
    print('recall-round records held back (conf=low)', n_low)
    for r in recs:
        r.pop('_co')
    print('skipped on shared faculty pages', n_shared_skip)
    print('records', len(recs), 'professors', len({r['prof'] for r in recs}),
          Counter(r['type'] for r in recs))
    lab_recs = extract_lab(profs, recs, idx, dropped, by_key, dblp_of, co_of, me_key, uni_names)

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
    with open(os.path.join(ROOT, 'data/enrich/students_lab.jsonl'), 'w', encoding='utf-8') as f:
        for r in lab_recs:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
