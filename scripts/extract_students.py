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
    s = l.strip(' \t:•·*#▪►|').strip()
    s = re.sub(r'\s*\(\d+\)$', '', s)  # "PhD Students (12)"
    # "Supervised Graduate Students at Yale" -> "Graduate Students"
    s = re.sub(r'^(?:supervised|advised)\s+(?=\S+\s+students?\b|students?\b|ph\.?\s?d|post)', '', s, flags=re.I)
    s = re.sub(r'(\b(?i:students?|scholars?|researchers?))\s+(?:at|@)\s+[A-Z][\w .&\'’-]{0,30}$', r'\1', s)
    if not s:
        return None, False
    if CONTACT_END.match(s):
        return 'end', False
    if re.fullmatch(r'(?:current(?:ly)?|present|former|past|previous|completed|graduated)\s*:?', s, re.I):
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
        if allow_post and re.match(r'[\s,;:(\[|–—-]*' + L_POST.pattern, t, re.I):
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


def entries(lines, coauthors=frozenset()):
    """栏目里的每一项 -> (名字, 备注, 原文, 标注)。coauthors：这位教授 dblp 合作者的匹配键，合作者不受下面的严格过滤。
    标注见 entry_label()：本行（去向之前）写的 PhD / Postdoc / MS / BSc / REU / Visiting…，本行没写时看下一行的说明。"""
    out = []
    lines = [l.strip(' \t•·*-–—▪►') for l in lines]
    for li, line in enumerate(lines):
        if not line or re.match(r'(?:joint(?:ly)?\s+)?with\s', line, re.I):
            continue  # "With Yi Zhang, Dan Roth · VLDB"：合作者，不是学生
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
            if re.match(r'\s*(?:University|Institute|College|School|Laboratory|Lab|Center|Centre|Hospital|Inc\b|Corporation)', rest):
                continue  # 机构名的前半截："Prince Sultan University"
            if re.search(r'(?<!co-)(?<!co)\b(?i:advisor|supervisor)\s*[:,]\s*(?:Prof\.?\s*|Dr\.?\s*)?[A-Z]', rest):
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
            if k in ('phd', 'postdoc', 'amb', 'mixed', 'skip'):
                area, n_area = n_area, n_area + 1
                cur, head, sub, since = ('skip' if mentor else k), l, None, 0
            continue
        if k == 'end' or (k is None and since >= 60):
            flush()
            area = None
            mentor = False
            continue
        if k in ('phd', 'postdoc', 'amb', 'mixed', 'skip'):
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
            since += 1
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
    all_texts = {p['id']: load_texts(p['id'], p['homepage'], shared) for p in profs}
    build_common(t for ts in all_texts.values() for _, t in ts)
    build_vocab(idx, profs)
    n_coauthors = {}
    recs = []
    dropped = {}
    for p in profs:
        seen = set()
        co_ids = {a for pub in idx.get(p['dblp'], []) for a in pub[2]}  # dblp 作者标识（"Bo Li 0026"）
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
            for area, sec_kind, head, sub, ents in parsed:
                if per_area[area] > 80:
                    continue  # 太长，多半不是学生名单
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
                    elif sec_kind == 'mixed':
                        continue  # "PhD and MS Students" 栏目里没写学位的项
                    elif sec_kind == 'phd':
                        kind = 'phd'
                    elif page_nonphd:
                        continue  # 只写 Students / Alumni、页面上另有硕士 / 本科等栏目：分不清是不是博士生
                    else:
                        kind = 'phd'
                    if k == me_key[p['id']] or (k, kind) in seen:
                        continue
                    # PDF 版 CV 排版乱、常混进论文题目：要么和这位教授合写过论文，要么备注里写着 PhD/student 等
                    if strict and k not in coauthors and not STUDENTISH.search(rest):
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
                    if note and (alumni or kind == 'postdoc') and not YEARISH.fullmatch(note):
                        r['af'] = note
                    # 列在"当前学生"里、备注也没写去向或毕业年份：网站上标为在读学生
                    if (kind == 'phd' and not alumni
                            and not re.search(r'\bnow\b|graduat|alum|former|\b(?:19[89]\d|20[01]\d|202[0-3])\b', f'{note} {rest}', re.I)):
                        r['cur'] = 1
                    r['_co'] = k in coauthors or bool(STUDENTISH.search(rest))  # rest：备注清洗前的原文（"Ph.D." 会被 clean_note 去掉）
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
    for r in recs:
        r.pop('_co')
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
