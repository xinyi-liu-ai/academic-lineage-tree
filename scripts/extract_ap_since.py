"""给职称是 Assistant Professor 的教授找"在现单位当助理教授的起始年份"（网站上显示 "Assistant Prof. since 2024"），
以及还没入职、主页上写着 "I will join X as an Assistant Professor in Fall 2026" 的 incoming 教授。

谁算 AP：data/enrich/titles.json 的职称，被 data/enrich/directory.json 的 "ti" 覆盖后，
匹配 /^Assistant (Research |Teaching )?Professor/。

来源（全是缓存，不联网）：
  data/cache/people_pages/{pid}.json 的 pages[0]（主页）和其他子页、data/cache/pages/{pid}_home.txt、
  data/cache/pages/{pid}_cv.txt、data/cache/recruit_pages/{pid}*（有就用）。
  文本按行切，再按句子切；新闻行开头的日期（"08/2025 Joined ..."、"Aug 2024: Started ..."、"[2023.09] ..."）记下来，
  只有月份的（"Aug: Joined ..."）往上找最近的年份小标题。

规则（按可信度从高到低，每条都要求句子里或同一行里有 "Assistant Professor"，P4 例外）：
  P1 入职陈述：joined / join / joining / started / starting / began / moved to / became / appointed / arrived ...
     + Assistant Professor，年份取离动词和职称最近的 "in (Fall) 2023" / "on July 1, 2025" / "since 2022" /
     "from Jan. 2020" / "Fall 2024"，句子里没有年份就用新闻行日期。
     例："I joined the University of X as an assistant professor in 2023"、"Aug 2024: Started as Assistant Professor at Y"、
     "Starting in January 2025, I will be an Assistant Professor at Z"
  P2 since 陈述："Since Fall 2024, I am an Assistant Professor"、"has been an assistant professor at X since 2021"
  P3 履历式开放区间：同一行（或紧挨着的短行）里 "Assistant Professor, Univ X, 2021–present" / "(2022 - )" /
     "2021-Present Tenure-Track Assistant Professor ..."；闭区间 "2017–2022" 不算
  P4 不带职称的入职年：本人（I / He / She / 名字 / 新闻行无主语）"joined <现单位 | the department | the faculty |
     the School of ...> in 2023"、"Before joining <现单位> in 2023, I was a postdoc"、
     "Starting in Winter 2025, I will be joining the ECE Department at UW-Madison"、Purdue 院系页的 "Joined department: Fall 2025"；
     句子里出现 postdoc / research scientist / lecturer / student / visiting 等其他身份就不算（可能是先做别的再转 AP），
     后文是 Associate Chair / Dean 这类资深职务的也不算
  年份必须在 2010–2027 之间；不能只凭博士毕业年份猜。

单位核对：句子里离职称（P4 是离 join）最近的大学名（"University of X" / "X University" / "X Institute of Technology" /
  已知缩写 CMU、UCR、UIUC ...）必须是现单位（用 professors.csv 的 University Name 生成别名；"UC Riverside"、
  "University of Maryland" ≈ "University of Maryland - College Park"）；不是现单位就丢掉（以前在别校当 AP、学生去别校当 AP）。
  句子里没写单位就当作现单位。页面挂在另一所大学的 .edu 域名下（旧单位主页 / CV 没更新）时，必须明确写出现单位才收。

排除：past tense（was / were / previously / formerly / prior to that / served as）描述的 AP；Visiting / Adjunct / Courtesy /
  Affiliate / Clinical / by courtesy AP；职称种类对不上（现职是 Assistant Professor，句子说的是 Research Assistant Professor）；
  教学经历行（"Assistant Professor, CISC474 (Fall 2023)"）；"on leave"；闭区间的后一半年份；
  "assistant professor positions"、招聘广告、"we are hiring"；congrat / 我的学生 / alumni / mentee
  这类说别人的句子；主语是别人名字（不是 I / He / She / They / 本人名字）的句子；页面上本人写了
  "promoted to Associate Professor" / "I am (now) an Associate Professor" 的整个人跳过（职称过时）。

多个候选：P1/P2/P3 一起比，都没有才用 P4；过去时 / 现在时陈述优先于 "will join" 将来时（除非将来时的年份更晚且 >= 2026）；
  取最早的年份（后来才加的冠名教职 "X Family Assistant Professor 2026-" 不是起点），只出现一次且比多数票早 3 年以上的当噪声；
  同年份里主页 > 子页 > CV。

incoming：起始时间晚于 2026 年 11 月（年份 ≥ 2027，或 "2026 年 12 月"），或者用 "will join / will be joining /
  am joining / incoming" 这种将来时说法、年份是 2026 且月份不明或在 8 月以后（"Fall 2026"）。
  "will join in Spring 2026" 现在（2026-11）早就入职了，记 incoming=false；年份早于 2026 的将来时说法
  （"I will join UCR in Jan 2020"，页面没更新）也记作已入职。新闻行日期不早于入职时间的（"2026-08: I am joining ..."）不算将来时。
  没写入职年份的将来时（"August 2021: I defended ... and will be joining UMD"、"I am joining Rice as an AP"）不收录。

输出: data/enrich/ap_since.json  {pid: {"y": 2024, "q": 原句(<=200 字), "src": 页面 URL, "incoming": bool}}
用法: python scripts/extract_ap_since.py [--debug] [--sample N] [--pid p1,p2]
  （--debug / --sample 打印每条结果的规则层 T1-T3 和冲突年份；--pid 只跑这几个人、不写文件）
"""
import csv
import glob
import json
import os
import random
import re
import sys
import unicodedata
from collections import Counter
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW_Y, NOW_M = 2026, 11
YMIN, YMAX = 2010, 2027

MONTHS = {m: i + 1 for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'])}
MON = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?'
SEASON = r'(?:Fall|Spring|Summer|Winter|Autumn)'
YEAR = r'(20[12]\d)(?!\d)'

AP = re.compile(r'(?:(?<![A-Za-z])|(?<=[a-z])(?=A))((?:tenure[- ]track\s+)?(?:assistant|asst\.?)\s+(?:research\s+|teaching\s+)?prof(?:es+or|\.)?'
                r'(?:\s+of\s+teaching)?)(?-i:(?![a-z]))(?!\s*(?:positions|openings?|search|candidates?|level))', re.I)
AP_BAD_PREFIX = re.compile(r'(?i)(?:visiting|adjunct|courtesy|affiliate[d]?|a\W{0,2}liated|a[ﬀﬃ]liated|clinical|honorary|'
                           r'associate|full|joint|secondary|affiliate|and|or|/)[\s,-]*$')
AP_BAD_SUFFIX = re.compile(r'(?i)\s*[,(\[]?\s*(?:\(?\s*by\s+courtesy|courtesy|\(?\s*affiliate|\(?\s*adjunct|\(?\s*secondary|'
                           r'\(?\s*joint\s+appointment|\(?\s*visiting|emerit|positions?|role\s+at\s+(?!my)|search|job)')
COURSE = re.compile(r'(?<![A-Za-z])[A-Z]{2,6}\s?-?(?!(?:19|20)\d\d(?!\d))[\dX]{2,4}[A-Z]?(?![\d])(?!\s*(?:[-–—]|to))|on\s+leave|(?i:(?<![a-z])(?:course|taught|teaching|lecture|instructor\s+of\s+record))')
STATIVE = re.compile(r"(?i)(?:have\s+been|has\s+been|i\s+am|i'm|i’m|is\s+an?|am\s+now|will\s+be)$")
JOIN = re.compile(r'(?<![A-Za-z])(joined|joins|join|joining|started|starts|start|starting|began|begin|beginning|'
                  r'moved\s+to|moving\s+to|arrived|became|become|becoming|appointed|commenced|took\s+up|'
                  r'accepted\s+an?\s+(?:offer|position)|will\s+be|have\s+been|has\s+been|i\s+am|i\'m|i’m|is\s+an?|'
                  r'am\s+now|joined\s+as)(?![a-z])', re.I)
FUTURE = re.compile(r'(?i)(?<![a-z])(will|shall|going\s+to|am\s+joining|is\s+joining|are\s+joining|be\s+joining|'
                    r'incoming|upcoming|plan\s+to|i\'ll|i’ll|this\s+coming)(?![a-z])')
STRONG_FUTURE = re.compile(r'(?i)(?<![a-z])(will|shall|going\s+to|i\'ll|i’ll|incoming|upcoming)(?![a-z])')
PAST_AP = re.compile(r'(?i)(?<![a-z])(was|were|previously|formerly|prior\s+to\s+that|before\s+that|had\s+been|'
                     r'served\s+as|used\s+to\s+be|left)(?![a-z])')
OTHER_PERSON = re.compile(r'(?i)congrat|(?:my|our|his|her|their)\s+(?:former\s+|first\s+|second\s+|third\s+)?'
                          r'(?:ph\.?d\.?\s+|graduate\s+|master\'?s\s+)?(?:students?|advisees?|mentees?|postdocs?|alumn\w*)|'
                          r'(?<![a-z])(?:alumni|alumnus|alumna|mentee|advisee|lab\s+alum)|'
                          r'(?:ph\.?d\.?|former|graduate)\s+students?\s+(?-i:[A-Z])|welcome[sd]?\s+(?:new|our)|'
                          r'new\s+faculty\s+(?:members?|hires?)|hiring|we\s+are\s+recruiting|job\s+talk|'
                          r'(?:has|have)\s+accepted\s+(?:a|an)\s+(?:tenure|faculty|assistant)')
OTHER_ROLE = re.compile(r'(?i)(?<![a-z])(post-?docs?|postdoctoral|research\s+(?:scientist|associate|fellow|engineer|staff)|'
                        r'lecturer|instructor|students?|visiting|intern|engineer|scientist|fellow|staff|director|'
                        r'researcher|ph\.?d\.?\s+(?:student|candidate)|undergrad\w*|graduate\s+student|teaching\s+assistant)(?![a-z])')
PROMOTED = re.compile(r'(?i)(?:promoted\s+to\s+(?:tenured\s+)?(?:associate|full)\s+professor|'
                      r'(?:i\s+am|i\'m|i’m)\s+(?:now\s+|currently\s+)?an?\s+(?:tenured\s+)?associate\s+professor|'
                      r'received\s+tenure|was\s+granted\s+tenure|got\s+tenure)')
EXCLUDE_SENT = re.compile(r'(?i)(?:applications?\s+for|apply|job\s+(?:posting|ad)|faculty\s+(?:search|position|opening)s?|'
                          r'tenure[- ]track\s+(?:faculty\s+)?(?:positions?|openings?)|award|prize|grant(?![a-z])|'
                          r'career\s+award|nsf\s+career)')

# 年份短语（带引导词），组 1 = 年份
YEAR_PHRASE = re.compile(
    r'(?i)(?<![a-z])(?:in|since|from|starting|start|starts|beginning|begin|as\s+of|effective|on|by|until|during|this|'
    r'joined|started|of|at|after|before)?\s*'
    r'(?:the\s+)?(?:(?:early|late|mid-?|beginning\s+of|end\s+of|summer\s+of|fall\s+of)\s+)?'
    r'(?:' + SEASON + r'\s+(?:of\s+|semester\s+of\s+|quarter\s+of\s+)?|' + MON + r'\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?(?:of\s+)?|'
    r'\d{1,2}/(?:\d{1,2}/)?)?' + YEAR)
DEGREE_NEAR = re.compile(r'(?i)(?<![a-z])(ph\.?\s?d\.?|doctora\w*|degrees?|graduat\w*|received|earned|postdoc\w*|post-doc\w*|'
                         r'b\.\s?s\.?c?|m\.\s?s\.?c?|bsc|msc|b\.\s?a\.|m\.\s?eng|dissertation|thesis|awards?|awarded|prize|'
                         r'papers?|best|interns?|internship|fellows?|fellowship|visiting|research\s+scientist|lecturer|'
                         r'engineers?|was\s+(?:a|an)|worked|working|studied|students?)(?![a-z])[^.;]{0,40}$')
# 开放区间 "2021–present" / "Aug. 2021 - now" / "(2018-)" / "2022.8 --)" / "9/2025- Assistant Professor" / "07/2024 {"
# （LaTeX CV 转出来的 en dash 有时变成 "{"）
DASH = r'(?:[-–—~{‐‑−]+|to(?![a-z]))'
PRESENT = r'(?:present|now|current(?:ly)?|today|date|till\s+date|curr\.?|pres\.?|tbd|ongoing)'
OPEN_RANGE = re.compile(r'(?i)(?<![\d/.])(?:' + MON + r'\s*|' + SEASON + r'\s+|\d{1,2}/)?' + YEAR + r'(?:\.\d{1,2})?'
                        r'(?:\s*(?:[-–—~{‐‑−]+\s*' + PRESENT + r'|to\s+' + PRESENT + r'(?!\s*:))(?-i:(?![a-z]))'
                        r'|\s*[-–—~{‐‑−]+\s*(?:\)|\]|$|;|\||,|(?=(?:tenure[- ]track\s+)?assistant))|\s*[-–—~]*\s*onwards?)')
OPEN_RANGE2 = re.compile(r'(?i)(?<![\d/.])(?:' + MON + r'\s*|' + SEASON + r'\s+|\d{1,2}/)?' + YEAR + r'(?:\.\d{1,2})?'
                         r'\s*(?:[-–—~{‐‑−]+\s*' + PRESENT + r'|to\s+' + PRESENT + r'(?!\s*:))(?-i:(?![a-z]))')
CLOSED_RANGE = re.compile(r'(?i)(?<!\d)' + YEAR + r'\s*(?:[-–—~]+|to)\s*(?:' + MON + r'\s+|' + SEASON + r'\s+)?(?:20)?([12]\d)(?!\d)')
SINCE = re.compile(r'(?i)(?:(?<![A-Za-z])|(?<=[a-z])(?=[A-Z]))(?:since|from|as\s+of|effective)\s+(?:the\s+)?(?:' + SEASON + r'\s+(?:of\s+)?|' + MON +
                   r'\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?)?' + YEAR + r'(?!\s*(?:[-–—~]|to)\s*\d)')

# 新闻行日期
NEWS_DATE = re.compile(
    r'^\s*[\[(【]?\s*(?:'
    r'(?P<y1>20[12]\d)\s*[./-]\s*(?P<m1>\d{1,2})(?:\s*[./-]\s*\d{1,2})?'            # 2024/08, 2024.08.01, 2024-08
    r'|(?P<m2>\d{1,2})\s*[./-]\s*(?:\d{1,2}\s*[./-]\s*)?(?P<y2>20[12]\d)'           # 08/2025, 8/1/2025
    r'|(?P<mn3>' + MON + r')\s*(?:\d{1,2}(?:st|nd|rd|th)?,?\s*)?(?P<y3>20[12]\d|[\'’][12]\d)(?!\d)'  # Aug 2024, Aug 01, 2022, Aug '24
    r'|(?P<y4>20[12]\d)\s*[,.]?\s*(?P<mn4>' + MON + r')(?:\s*\d{1,2})?'                # 2022 Sep
    r'|(?P<s5>' + SEASON + r')\s+(?P<y5>20[12]\d)'                                     # Fall 2024
    r'|(?P<y6>20[12]\d)'                                                               # 2024:
    r')\s*[\])】]?\s*(?:[:：\-–—|•·]+|\s)\s*(?=\S)(?!(?:present|now|current|today|till|\d))', re.I)
MONTH_ONLY = re.compile(r'^\s*[\[(]?\s*(?P<mn>' + MON + r')\s*(?:\d{1,2})?\s*[\])]?\s*[:：\-–—]\s*', re.I)
YEAR_HEADER = re.compile(r'^\s*(?:news\s*)?[\[(]?(20[12]\d)[\])]?\s*:?\s*$', re.I)

NOISE_TOK = {'jan', 'january', 'feb', 'february', 'mar', 'march', 'apr', 'april', 'may', 'jun', 'june', 'jul', 'july',
             'aug', 'august', 'sep', 'sept', 'september', 'oct', 'october', 'nov', 'november', 'dec', 'december',
             'fall', 'spring', 'summer', 'winter', 'autumn', 'present', 'now', 'current', 'usa', 'us', 'since', 'from',
             'department', 'dept', 'computer', 'science', 'sciences', 'engineering', 'assistant', 'associate',
             'professor', 'tenure', 'track', 'tenure-track', 'research', 'teaching', 'appointments', 'employment',
             'positions', 'position', 'academic', 'experience'}
# 多校区系统名：逗号后面的校区不能省（"University of California, Riverside" ≠ UCLA）
AMBIG_CORE = [{'california'}, {'texas'}, {'massachusetts'}, {'colorado'}, {'maryland'}, {'illinois'}, {'michigan'},
              {'nebraska'}, {'missouri'}, {'alabama'}, {'wisconsin'}, {'north', 'carolina'}, {'nevada'}, {'louisiana'},
              {'new', 'york'}, {'state', 'new', 'york'}, {'washington'}, {'indiana'}, {'hawaii'}, {'minnesota'}]
STOP = {'university', 'univ', 'of', 'the', 'at', 'in', 'institute', 'and', 'school', 'college', 'campus', 'main'}
TOKEN_NORM = {'tech': 'technology', 'penn': 'pennsylvania', 'uc': 'california', 'cal': 'california', 'st': 'saint',
              'st.': 'saint', 'umass': 'massachusetts', 'mass': 'massachusetts', 'polytechnic': 'polytechnic'}
# 现单位额外的别名 token（mention 的 token 是这个集合的子集就算现单位）
EXTRA_TOKENS = {
    'University of North Carolina': {'chapel', 'hill'},
    'Univ. of Illinois at Urbana-Champaign': {'urbana', 'champaign'},
    'University of Maryland - College Park': {'college', 'park'},
    'University of Wisconsin - Madison': {'madison'},
    'University of Washington': {'seattle', 'allen'},
    'Univ. of California - San Diego': {'la', 'jolla'},
    'Univ. of California - Los Angeles': {'los', 'angeles'},
    'University of Minnesota': {'twin', 'cities'},
    'University of Massachusetts Amherst': {'amherst'},
    'University of Hawaii at Manoa': {'manoa', 'hawai\'i', 'hawaiʻi'},
    'University of Nebraska': {'lincoln'},
    'University of Nevada': {'reno'},
    'University of Colorado Boulder': {'boulder'},
    'Pennsylvania State University': {'park', 'university'},
    'Washington University in St. Louis': {'saint', 'louis', 'washu'},
    'CUNY': {'city', 'new', 'york', 'cuny', 'graduate', 'center', 'hunter', 'queens', 'brooklyn', 'baruch'},
    'University at Albany - SUNY': {'albany', 'suny', 'state', 'new', 'york'},
    'University at Buffalo': {'buffalo', 'suny', 'state', 'new', 'york'},
    'Stony Brook University': {'stony', 'brook', 'suny', 'state', 'new', 'york'},
    'Binghamton University': {'binghamton', 'suny', 'state', 'new', 'york'},
    'TTI Chicago': {'toyota', 'technological', 'chicago'},
    'Missouri S&T': {'missouri', 'science', 'technology', 's&t'},
    'Missouri University of Technology': {'missouri', 'science', 'technology', 's&t'},
    'NJIT': {'new', 'jersey', 'technology'},
    'Rutgers University': {'new', 'brunswick', 'state', 'jersey'},
    'UNC - Charlotte': {'north', 'carolina', 'charlotte'},
    'Ohio State University': {'the'},
    'Georgia Institute of Technology': {'georgia', 'technology'},
    'Virginia Tech': {'virginia', 'polytechnic', 'state', 'technology'},
    'Indiana University': {'bloomington', 'indianapolis'},
    'University of Michigan': {'ann', 'arbor'},
    'University of Texas at Austin': {'texas', 'austin'},
    'University of Alabama - Birmingham': {'alabama', 'birmingham'},
    'University of Tennessee': {'knoxville'},
    'University of Kentucky': {'lexington'},
    'University of Missouri': {'columbia'},
    'University of Colorado - Denver': {'colorado', 'denver'},
    'University of Louisiana - Lafayette': {'louisiana', 'lafayette'},
    'University of Massachusetts Boston': {'massachusetts', 'boston'},
    'University of Massachusetts Lowell': {'massachusetts', 'lowell'},
    'Michigan Technological University': {'michigan', 'technological', 'technology'},
    'Rochester Institute of Technology': {'rochester', 'technology'},
    'California Institute of Technology': {'caltech'},
    'College of William and Mary': {'william', 'mary', 'w&m'},
    'Naval Postgraduate School': {'naval', 'postgraduate'},
}
# 已知缩写 -> 大学名（多个都算）
ACRONYMS = {
    'CMU': ['Carnegie Mellon University'], 'MIT': ['Massachusetts Institute of Technology'],
    'UIUC': ['Univ. of Illinois at Urbana-Champaign'], 'UCSD': ['Univ. of California - San Diego'],
    'UCB': ['Univ. of California - Berkeley'], 'UCLA': ['Univ. of California - Los Angeles'],
    'UCI': ['Univ. of California - Irvine'], 'UCSB': ['Univ. of California - Santa Barbara'],
    'UCR': ['Univ. of California - Riverside'], 'UCSC': ['Univ. of California - Santa Cruz'],
    'UCD': ['Univ. of California - Davis'], 'UC Davis': ['Univ. of California - Davis'],
    'UC San Diego': ['Univ. of California - San Diego'], 'UC Irvine': ['Univ. of California - Irvine'],
    'UC Riverside': ['Univ. of California - Riverside'], 'UC Santa Barbara': ['Univ. of California - Santa Barbara'],
    'UC Santa Cruz': ['Univ. of California - Santa Cruz'], 'UC Los Angeles': ['Univ. of California - Los Angeles'],
    'UC Merced': ['Univ. of California - Merced'], 'UCM': ['Univ. of California - Merced'],
    'Gatech': ['Georgia Institute of Technology'], 'GT': ['Georgia Institute of Technology'],
    'Georgia Tech': ['Georgia Institute of Technology'], 'UMich': ['University of Michigan'],
    'UW': ['University of Washington', 'University of Wisconsin - Madison'], 'UMD': ['University of Maryland - College Park'],
    'NEU': ['Northeastern University'], 'UT': ['University of Texas at Austin'],
    'UPenn': ['University of Pennsylvania'], 'Penn': ['University of Pennsylvania'], 'NYU': ['New York University'],
    'UMass': ['University of Massachusetts Amherst', 'University of Massachusetts Boston', 'University of Massachusetts Lowell'],
    'USC': ['University of Southern California', 'University of South Carolina'], 'SBU': ['Stony Brook University'],
    'PSU': ['Pennsylvania State University', 'Portland State University'], 'Penn State': ['Pennsylvania State University'],
    'NWU': ['Northwestern University'], 'GMU': ['George Mason University'], 'TAMU': ['Texas A&M University'],
    'NCSU': ['North Carolina State University'], 'NC State': ['North Carolina State University'],
    'OSU': ['Ohio State University', 'Oregon State University'], 'UVA': ['University of Virginia'],
    'BU': ['Boston University'], 'UIC': ['University of Illinois at Chicago'], 'UMN': ['University of Minnesota'],
    'UNC': ['University of North Carolina', 'UNC - Charlotte'], 'ASU': ['Arizona State University'],
    'VT': ['Virginia Tech'], 'JHU': ['Johns Hopkins University'], 'UR': ['University of Rochester'],
    'UTD': ['University of Texas at Dallas'], 'UT Dallas': ['University of Texas at Dallas'],
    'WashU': ['Washington University in St. Louis'], 'WUSTL': ['Washington University in St. Louis'],
    'MSU': ['Michigan State University'], 'RIT': ['Rochester Institute of Technology'],
    'UF': ['University of Florida'], 'UFL': ['University of Florida'], 'UCF': ['University of Central Florida'],
    'USF': ['University of South Florida'], 'FIU': ['Florida International University'],
    'FSU': ['Florida State University'], 'FAU': ['Florida Atlantic University'], 'WPI': ['Worcester Polytechnic Institute'],
    'RPI': ['Rensselaer Polytechnic Institute'], 'Caltech': ['California Institute of Technology'],
    'NJIT': ['NJIT'], 'GWU': ['George Washington University'], 'GW': ['George Washington University'],
    'VCU': ['Virginia Commonwealth University'], 'ODU': ['Old Dominion University'], 'UVM': ['University of Vermont'],
    'UNT': ['University of North Texas'], 'UNM': ['University of New Mexico'], 'NMSU': ['New Mexico State University'],
    'UConn': ['University of Connecticut'], 'UMKC': ['University of Missouri - Kansas City'],
    'UAB': ['University of Alabama - Birmingham'], 'UNLV': ['University of Nevada Las Vegas'],
    'UNR': ['University of Nevada'], 'KU': ['University of Kansas'], 'UK': ['University of Kentucky'],
    'CWRU': ['Case Western Reserve University'], 'BYU': ['Brigham Young University'], 'LSU': ['Louisiana State University'],
    'KSU': ['Kansas State University'], 'K-State': ['Kansas State University'], 'WSU': ['Washington State University', 'Wayne State University', 'Wichita State University'],
    'TTIC': ['TTI Chicago'], 'TTI-Chicago': ['TTI Chicago'], 'CUNY': ['CUNY'], 'UTEP': ['University of Texas - El Paso'],
    'UTSA': ['University of Texas at San Antonio'], 'UTA': ['University of Texas at Arlington'],
    'UT Austin': ['University of Texas at Austin'], 'UT-Austin': ['University of Texas at Austin'],
    'UMBC': ['Univ. of Maryland - Baltimore County'], 'UNCC': ['UNC - Charlotte'], 'UNC Charlotte': ['UNC - Charlotte'],
    'UMass Amherst': ['University of Massachusetts Amherst'], 'UMass Lowell': ['University of Massachusetts Lowell'],
    'UMass Boston': ['University of Massachusetts Boston'], 'UML': ['University of Massachusetts Lowell'],
    'UGA': ['University of Georgia'], 'GSU': ['Georgia State University'], 'ISU': ['Iowa State University'],
    'UH': ['University of Houston'], 'UNH': ['University of New Hampshire'], 'URI': ['University of Rhode Island'],
    'UO': ['University of Oregon'], 'OU': ['University of Oklahoma', 'Oakland University'], 'UTK': ['University of Tennessee'],
    'SLU': ['Saint Louis University'], 'UC': ['University of Cincinnati'], 'UCD Denver': ['University of Colorado - Denver'],
    'CU Boulder': ['University of Colorado Boulder'], 'CU Denver': ['University of Colorado - Denver'],
    'UNL': ['University of Nebraska'], 'UNO': ['University of Nebraska - Omaha', 'University of New Orleans'],
    'USU': ['Utah State University'], 'BSU': ['Boise State University'], 'NPS': ['Naval Postgraduate School'],
    'IIT': ['Illinois Institute of Technology'], 'Mines': ['Colorado School of Mines'], 'CSU': ['Colorado State University', 'Cleveland State University'],
    'ULL': ['University of Louisiana - Lafayette'], 'UL Lafayette': ['University of Louisiana - Lafayette'],
    'UM': ['University of Michigan', 'University of Miami', 'University of Memphis'], 'UMD-Dearborn': ['University of Michigan-Dearborn'],
    'UM-Dearborn': ['University of Michigan-Dearborn'], 'W&M': ['College of William and Mary'],
    'Texas Tech': ['Texas Tech University'], 'TTU': ['Texas Tech University'], 'Virginia Tech': ['Virginia Tech'],
    'Michigan Tech': ['Michigan Technological University'], 'MTU': ['Michigan Technological University'],
    'Pitt': ['University of Pittsburgh'], 'Stevens': ['Stevens Institute of Technology'], 'Rutgers': ['Rutgers University'],
    'Purdue': ['Purdue University'], 'Stanford': ['Stanford University'], 'Harvard': ['Harvard University'],
    'Princeton': ['Princeton University'], 'Yale': ['Yale University'], 'Cornell': ['Cornell University'],
    'Columbia': ['Columbia University'], 'Duke': ['Duke University'], 'Brown': ['Brown University'], 'Rice': ['Rice University'],
    'Dartmouth': ['Dartmouth College'], 'Northwestern': ['Northwestern University'], 'Northeastern': ['Northeastern University'],
    'Berkeley': ['Univ. of California - Berkeley'], 'UC Berkeley': ['Univ. of California - Berkeley'],
    'Johns Hopkins': ['Johns Hopkins University'], 'Carnegie Mellon': ['Carnegie Mellon University'],
    'Georgetown': ['Georgetown University'], 'Vanderbilt': ['Vanderbilt University'], 'Emory': ['Emory University'],
    'Tufts': ['Tufts University'], 'Tulane': ['Tulane University'], 'Lehigh': ['Lehigh University'], 'Drexel': ['Drexel University'],
    'Clemson': ['Clemson University'], 'Auburn': ['Auburn University'], 'Brandeis': ['Brandeis University'],
    'Syracuse': ['Syracuse University'], 'Temple': ['Temple University'], 'Binghamton': ['Binghamton University'],
    'Notre Dame': ['University of Notre Dame'], 'Wisconsin': ['University of Wisconsin - Madison'],
    'UW-Madison': ['University of Wisconsin - Madison'], 'UW Madison': ['University of Wisconsin - Madison'],
    'Illinois': ['Univ. of Illinois at Urbana-Champaign'], 'Michigan': ['University of Michigan'],
    'Maryland': ['University of Maryland - College Park'],
}
ACR_RE = re.compile(r'(?<![\w&-])(' + '|'.join(sorted((re.escape(k) for k in ACRONYMS), key=len, reverse=True)) +
                    r')(?![\w&]|-(?!Austin|Madison|Dearborn|Chicago)\w)')
# 大学名：University of X / X University / X Institute of Technology / X College / X Tech
CAPW = r'(?:[A-Z][\w&\'’.-]*|of|at|in|the|and|-|–)'
ORG_RE = re.compile(
    r'(?:(?:The\s+)?(?:University|Univ\.)\s+of\s+(?:[A-Z][\w&\'’.-]*)(?:[\s,]+(?:at\s+|in\s+|-\s*|–\s*)?(?:[A-Z][\w&\'’.-]*))*'
    r'|(?:[A-Z][\w&\'’.-]*\s+){1,4}(?:University|Institute\s+of\s+Technology|Polytechnic\s+Institute|Tech)(?![a-z])'
    r'(?:[\s,]+(?:at\s+|in\s+|-\s*)?(?:[A-Z][\w&\'’.-]*)(?:\s+[A-Z][\w&\'’.-]*)?)?'
    r'|(?:[A-Z][\w&\'’.-]*\s+){1,2}College(?![a-z])(?!\s+of))')
ORG_GENERIC = re.compile(r'(?i)^(?:the\s+)?(?:computer|computing|engineering|data|information|science|electrical|'
                         r'department|dept|school|graduate|state|research|technology|national|new|our|this|my|'
                         r'interactive|ai|ml|robotics|allen|vector|simons)(?:\s|$)')


def norm(s):
    s = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in s if not unicodedata.combining(c))


def tokens(name):
    name = norm(name).lower().replace('univ.', 'university')
    toks = re.findall(r"[a-z&'’]+(?:\.)?", name)
    out = set()
    for t in toks:
        t = t.rstrip('.')
        t = TOKEN_NORM.get(t, t)
        if t not in STOP and t not in NOISE_TOK and not re.fullmatch(r'[a-z]{1,2}', t):
            out.add(t)
    return out


def _all_uni_tokens():
    out = set()
    for f in ('data/raw/universities.csv', 'data/raw/professors.csv'):
        fp = os.path.join(ROOT, f)
        if os.path.exists(fp):
            with open(fp, encoding='utf-8', newline='') as fh:
                for r in list(csv.reader(fh))[1:]:
                    if r:
                        out |= tokens(r[0])
    for v in EXTRA_TOKENS.values():
        out |= v
    return out


def uni_tokens(uni):
    return tokens(uni) | EXTRA_TOKENS.get(uni, set())


def org_mentions(s):
    """[(start, end, text, is_current_fn_input)]：句子里的大学名。"""
    out = []
    for m in ORG_RE.finditer(s):
        txt = m.group(0).strip(' ,-–')
        if ORG_GENERIC.match(txt) and not re.search(r'(?i)state\s+university|national\s+university|new\s+(?:york|jersey|mexico|hampshire|orleans)', txt):
            # "Computer Science Institute"、"Department of ..."：不是大学
            continue
        out.append((m.start(), m.end(), txt, 'name'))
    for m in ACR_RE.finditer(s):
        if any(a <= m.start() < b for a, b, _, _ in out):
            continue
        out.append((m.start(), m.end(), m.group(1), 'acr'))
    return sorted(out)


def org_is_current(txt, kind, uni):
    if kind == 'acr':
        return uni in ACRONYMS.get(txt, [])
    # 全名里含缩写（"UC Riverside"、"UT Austin"）
    for m in ACR_RE.finditer(txt):
        if uni in ACRONYMS.get(m.group(1), []) and len(m.group(1)) >= 4:
            return True
    t = tokens(txt)
    t.discard('the')
    if not t:
        return True
    cur = uni_tokens(uni)
    if t <= cur:
        return True
    # "Arizona State University, Tempe"、"University of Virginia, Charlottesville"：逗号前的主体对上、又不是多校区系统名
    core = tokens(re.split(r'[,(|]', txt)[0])
    if bool(core) and core <= cur and core not in AMBIG_CORE and \
            re.search(r'(?i)univ|institute|college|tech', re.split(r'[,(|]', txt)[0]):
        return True
    # "University of North Texas Denton"、"University of Washington, Seattle"：多出来的一个词不是任何大学名里的词（城市名）
    extra = t - cur
    return len(extra) == 1 and not (extra & ALL_UNI_TOKENS) and len(t & cur) >= 1 and (t & cur) not in AMBIG_CORE


ALL_UNI_TOKENS = set()


# ---------------------------------------------------------------- 页面

def load_pages(pid):
    """[(url, text, kind)]，kind = home / sub / cv / recruit。"""
    out, seen = [], set()

    def add(url, txt, kind):
        if not txt or not txt.strip() or txt[:3000].count('\x00') > 50:
            return
        key = re.sub(r'\s+', ' ', txt[:5000])
        if key in seen:
            return
        seen.add(key)
        out.append((url, txt[:400000], kind))

    pp = os.path.join(ROOT, 'data/cache/people_pages', pid + '.json')
    if os.path.exists(pp):
        try:
            d = json.load(open(pp, encoding='utf-8'))
        except Exception:
            d = {}
        for i, p in enumerate(d.get('pages') or []):
            add(p.get('url') or '', p.get('text') or '', 'home' if i == 0 else 'sub')
    for name, kind in ((pid + '_home.txt', 'home'), (pid + '_cv.txt', 'cv')):
        hp = os.path.join(ROOT, 'data/cache/pages', name)
        if os.path.exists(hp):
            txt = open(hp, encoding='utf-8', errors='replace').read()
            url = ''
            if txt.startswith('#URL'):
                first, _, txt = txt.partition('\n')
                url = first[4:].strip()
            add(url, txt, kind)
    rdir = os.path.join(ROOT, 'data/cache/recruit_pages')
    if os.path.isdir(rdir):
        for f in sorted(glob.glob(os.path.join(rdir, pid + '.*')) + glob.glob(os.path.join(rdir, pid + '_*'))):
            try:
                if f.endswith('.json'):
                    d = json.load(open(f, encoding='utf-8'))
                    pages = d.get('pages') if isinstance(d, dict) else d
                    if isinstance(d, dict) and 'text' in d:
                        pages = [d]
                    for p in pages or []:
                        if isinstance(p, dict):
                            add(p.get('url') or '', p.get('text') or '', 'recruit')
                else:
                    txt = open(f, encoding='utf-8', errors='replace').read()
                    url = ''
                    if txt.startswith('#URL'):
                        first, _, txt = txt.partition('\n')
                        url = first[4:].strip()
                    add(url, txt, 'recruit')
            except Exception:
                pass
    return out


SENT_SPLIT = re.compile(r'(?<!\bDr\.)(?<!\bMr\.)(?<!\bMs\.)(?<!\bSt\.)(?<!\bProf\.)(?<!\bMrs\.)(?<!\bUniv\.)(?<!\bDept\.)'
                        r'(?<![A-Z]\.[A-Z]\.)(?<=[a-z0-9)\]”"][.!?])\s+(?=[A-Z"“(\[])|(?<=[.!?])\s{2,}|\s+•\s+')


def news_year(line, prev_year):
    """新闻行开头的日期 -> (year, month, 去掉日期后的文字起点)。"""
    m = NEWS_DATE.match(line)
    if m:
        g = m.groupdict()
        y = g['y1'] or g['y2'] or g['y3'] or g['y4'] or g['y5'] or g['y6']
        y = y.lstrip('\'’')
        if len(y) == 2:
            y = '20' + y
        mon = None
        if g['m1']:
            mon = int(g['m1'])
        elif g['m2']:
            mon = int(g['m2'])
        elif g['mn3'] or g['mn4']:
            mon = MONTHS.get((g['mn3'] or g['mn4'])[:3].lower())
        elif g['s5']:
            mon = {'fal': 9, 'aut': 9, 'spr': 1, 'sum': 6, 'win': 1}[g['s5'][:3].lower()]
        if mon is not None and not 1 <= mon <= 12:
            return None
        date_end = max(m.end(k) for k in ('y1', 'm1', 'y2', 'y3', 'y4', 'mn4', 'y5', 'y6') if g[k])
        if re.match(r'(?i)\s*[\])]?\s*[-–—~{‐‑−]+\s*(?:' + PRESENT + r'|(?:\S+\s+)?20[12]\d|\d{1,2}/|\(|\||,|$|'
                    r'(?:tenure[- ]track\s+)?assistant)', line[date_end:]):
            return None         # "2027- Assistant Professor"、"July 2024 – current" 是开放区间，不是新闻日期
        return int(y), mon, m.end()
    m = MONTH_ONLY.match(line)
    if m and prev_year:
        return prev_year, MONTHS.get(m.group('mn')[:3].lower()), m.end()
    return None


def clean(s):
    return re.sub(r'\s+', ' ', s).strip()


def short_q(s, a, b, lim=200):
    s2 = clean(s)
    if len(s2) <= lim:
        return s2
    # 以匹配处为中心截取
    pre = clean(s[:a])
    mid = clean(s[a:b])
    room = max(0, lim - len(mid))
    left = pre[-(room // 2):] if room // 2 else ''
    right = clean(s[b:])[:room - len(left)]
    return (left + (' ' if left else '') + mid + ' ' + right).strip()[:lim]


def year_month(txt):
    """'Fall 2024' / 'August 2024' / 'July 1, 2025' -> 月份（可能 None）。"""
    m = re.search(r'(?i)(' + SEASON + r')', txt)
    if m:
        return {'fal': 9, 'aut': 9, 'spr': 1, 'sum': 6, 'win': 1}[m.group(1)[:3].lower()]
    m = re.search(r'(?i)(' + MON + r')', txt)
    if m:
        return MONTHS.get(m.group(1)[:3].lower())
    m = re.search(r'(?<!\d)(\d{1,2})/(?:\d{1,2}/)?20[12]\d', txt)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(1))
    return None


def is_incoming(y, mon, future):
    """起始时间晚于 2026-11，或将来时说法且 2026 年下半年及以后（"will join in Spring 2026" 早就入职了，不算）。"""
    if y > NOW_Y or (y == NOW_Y and mon is not None and mon > NOW_M):
        return True
    return bool(future) and y == NOW_Y and (mon is None or mon >= 8)


# ---------------------------------------------------------------- 候选

def edu_domain(url):
    h = (urlparse(url).hostname or '').lower()
    return '.'.join(h.split('.')[-2:]) if h.endswith('.edu') else ''


def domain_owners():
    """.edu 域名 -> 大学（professors.csv 里主页在这个域名下的人 >=3 个且 >=60% 属于同一所）。"""
    with open(os.path.join(ROOT, 'data/raw/professors.csv'), encoding='utf-8', newline='') as f:
        rows = list(csv.reader(f))[1:]
    cnt = {}
    for r in rows:
        d = edu_domain(r[2] if '://' in r[2] else 'http://' + r[2])
        if d:
            cnt.setdefault(d, Counter())[r[0].strip()] += 1
    out = {}
    for d, c in cnt.items():
        u, n = c.most_common(1)[0]
        if n >= 3 and n / sum(c.values()) >= 0.6:
            out[d] = u
    return out


DOMAIN_OWNER = None


class Ctx:
    def __init__(self, pid, prof, title):
        self.pid = pid
        self.uni = prof['uni']
        self.title = title
        nm = norm(re.sub(r'\s+\d{4}$', '', prof['name']))
        parts = [p for p in re.split(r'[\s.-]+', nm) if len(p) >= 2]
        self.name_toks = {p.lower() for p in parts}
        self.foreign = False

    def nick(self, w):
        """昵称 / 简写：前 3 个字母和名字里某个词一样（Andy≈Andrew、Matt≈Matthew）。"""
        w = norm(w.lower())
        return len(w) >= 3 and any(t[:3] == w[:3] and (t.startswith(w[:-1]) or w.startswith(t[:3])) for t in self.name_toks
                                   if len(t) >= 3)

    def set_page(self, url):
        """页面挂在另一所大学的域名下（旧单位的主页 / CV 没更新）。共享 token 的不算（UM-Dearborn 用 umich.edu）。"""
        owner = DOMAIN_OWNER.get(edu_domain(url))
        self.foreign = bool(owner) and owner != self.uni and not (uni_tokens(owner) & uni_tokens(self.uni))


def subject_ok(s, vpos, ctx):
    """动词前面的主语是不是本人（I / He / She / They / 名字 / 无主语的新闻行）。"""
    pre = s[:vpos]
    # 同一分句里
    pre = re.split(r'[.;:!?]\s|,\s+(?:and|but|while|where)\s|\s(?:and|but)\s(?=[A-Z])', pre)[-1]
    words = re.findall(r"[A-Za-z][\w'’-]*", pre)
    if not words:
        return True
    if re.search(r'(?i)(?<![a-z])(?:professors|faculty\s+members|students|people|hires|two|three|four|several|'
                 r'new\s+faculty)(?![a-z])', pre):
        return False
    caps = [w for w in words[-6:] if w[0].isupper()]
    for w in reversed(words[-4:]):
        lw = w.lower()
        if lw in ('i', 'he', 'she', 'they', 'we', 'me', 'myself', 'dr', 'prof', 'professor'):
            return True
        if norm(lw) in ctx.name_toks or ctx.nick(lw):
            return True
    # 别人的名字做主语："Shamim will start as an AP ..."、"Dr. Wang joined ..."
    for w in caps:
        lw = norm(w.lower())
        if lw in ctx.name_toks or ctx.nick(lw) or lw in MONTHS or lw[:3] in MONTHS or re.match(SEASON, w) or lw in (
                'i', 'he', 'she', 'they', 'we', 'in', 'since', 'starting', 'from', 'after', 'before', 'as', 'on',
                'this', 'then', 'now', 'recently', 'currently', 'today', 'news', 'update', 'finally', 'also',
                'prior', 'following', 'upon', 'last', 'next', 'officially', 'excited', 'happy', 'thrilled',
                'beginning', 'effective', 'at', 'the', 'and', 'bio', 'biography', 'about', 'short', 'note',
                'personal', 'me', 'my', 'joined', 'started', 'starting', 'start', 'will', 'am', 'dr', 'prof',
                'professor', 'assistant', 'fall', 'spring', 'summer', 'winter', 'autumn', 'early', 'late',
                'mid', 'until', 'during', 'by', 'in', 'of', 'when', 'later', 'hi', 'hello', 'welcome'):
            continue
        if any(lw == t for t in uni_tokens(ctx.uni)) or ACR_RE.fullmatch(w) or len(w) <= 2 or lw in STOP or                 lw in NOISE_TOK or lw in ('institute', 'technology', 'state', 'school', 'college', 'center', 'lab'):
            continue
        return False
    return True


def pick_year(s, anchor_a, anchor_b, news_y):
    """离锚点（动词/职称）最近、不是学位/博士后年份的那个年份 -> (year, month, a, b)；没有用新闻日期。"""
    best = None
    for m in YEAR_PHRASE.finditer(s):
        y = int(m.group(1))
        a, b = m.start(1), m.end(1)
        # 年份前同一分句里有 PhD / postdoc / received 之类 -> 这是学位年份
        pre = s[max(0, m.start() - 60):m.start()]
        pre = re.split(r'[.;]\s|,\s*(?:and\s+)?(?:then\s+|since\s+|in\s+|where\s+)?(?=I\s|he\s|she\s|joined|started|became)|\sand\s(?=I\s|he\s|she\s|joined|started|became|then)', pre)[-1]
        if DEGREE_NEAR.search(pre):
            continue
        # 闭区间的后一半（"postdoc ... from 2023-2026"）不是起始年份
        if re.search(r'(?i)(?:19|20)\d\d\s*(?:[-–—~]+|to)\s*$', s[max(0, a - 12):a]):
            continue
        post = s[b:b + 25]
        if re.match(r'\s*(?:[-–—~]|to)\s*(?:20)?\d\d', post):     # 闭区间
            continue
        if re.match(r'[^,.;]{0,6}(?:ph\.?d|paper|conference|workshop|award|best|neurips|icml|cvpr|acl|iclr|'
                    r'sigmod|vldb|chi|uist|kdd|aaai|ijcai|emnlp|naacl|icse|fse|ccs|usenix|ndss|oakland|osdi|sosp|'
                    r'nsdi|sigcomm|mobicom|stoc|focs|soda)', post, re.I):
            continue
        d = a - anchor_b if a >= anchor_b else anchor_a - b
        d = max(d, 0)
        if a < anchor_a:
            d += 15           # 年份在后面更常见（"joined X as AP in 2023"），前面的稍微降权
        if best is None or d < best[0]:
            best = (d, y, m.group(0), a, b)
    if best and best[0] <= 160:
        return best[1], year_month(best[2]), best[3], best[4]
    if news_y:
        return news_y[0], news_y[1], None, None
    return None


def nearest_org(s, a, b, window=140):
    """离 [a,b) 最近的大学名 -> (text, kind) 或 None。"""
    best = None
    for oa, ob, txt, kind in org_mentions(s):
        d = oa - b if oa >= b else a - ob
        d = max(d, 0)
        if oa < a:
            # 前面的单位：中间要是隔着 from / PhD / received 之类就不算
            between = s[ob:a]
            if re.search(r'(?i)ph\.?d|postdoc|received|graduat|degree|before|prior|after|previously|was\s+an?', between):
                continue
            d += 10
        if d <= window and (best is None or d < best[0]):
            best = (d, txt, kind)
    return (best[1], best[2]) if best else None


DEPT_ACR = {'CS', 'CSE', 'EECS', 'ECE', 'EE', 'IST', 'SCAI', 'CSEE', 'SCI', 'SOC', 'ISE', 'ME', 'CIS', 'CDS', 'CEE',
            'BME', 'IE', 'IOE', 'ORIE', 'CISE', 'CSC', 'CISC', 'SECS', 'SCS', 'HCI', 'HCII', 'AI', 'ML', 'NLP', 'ECSE',
            'ESE', 'MAE', 'ISR', 'LTI', 'RI', 'CDM', 'SIS', 'SCIS', 'CSAIL', 'EPIC', 'IDS', 'DS', 'SEAS', 'CEAS',
            'CECS', 'CCIS', 'CCI', 'ICS', 'BCS', 'IU', 'ICE', 'PI', 'USA', 'US', 'UCSF', 'NSF', 'CMS', 'EPS', 'SIST',
            'ECE', 'GSE', 'TT', 'II', 'III', 'IV', 'PHD', 'DC', 'NY', 'NYC', 'CA', 'TX', 'MA', 'PA', 'IL', 'FL', 'OH',
            'GA', 'VA', 'NC', 'NJ', 'MD', 'WA', 'CO', 'AZ', 'UT', 'MI', 'MN', 'WI', 'IN', 'IA', 'KS', 'KY', 'LA', 'MO',
            'NE', 'NV', 'NH', 'NM', 'OK', 'OR', 'RI', 'SC', 'TN', 'VT', 'CT', 'DE', 'HI', 'AL', 'MS', 'AR', 'ID', 'MT',
            'ND', 'SD', 'WV', 'WY', 'AK', 'ECEE', 'CSIS', 'SOIC', 'SICE', 'KSoC', 'SoC', 'COE', 'CoE', 'CCS', 'ITM'}


def object_conflict(seg, ctx):
    """join / at 后面紧跟的单位不是现单位："at Hong Kong Unverisity ..."、"joining SIU School of Computing"。"""
    for m in re.finditer(r'(?<![A-Za-z])(?:at|joining|joined|join|joins|to)\s+(?:the\s+)?((?:[A-Z][\w&\'’.-]*(?:\s+|$)){1,6})', seg):
        phrase = m.group(1).strip()
        words = phrase.split()
        # 第一个词是缩写
        w0 = re.sub(r'[^\w&-]', '', words[0])
        if re.fullmatch(r'[A-Z][A-Z&-]{1,6}', w0) and w0 not in DEPT_ACR and w0.upper() not in DEPT_ACR:
            if w0 in ACRONYMS:
                if ctx.uni not in ACRONYMS[w0]:
                    return True
            elif not org_is_current(w0, 'name', ctx.uni) and w0.lower() not in uni_tokens(ctx.uni):
                return True
        if re.search(r'(?i)un\w*sity|univ|institute|college(?!\s+of)', phrase):
            if not org_is_current(phrase, 'name', ctx.uni):
                # "College of Computing" 这种院系名不是大学名
                if not re.match(r'(?i)(?:the\s+)?(?:college|school|institute|department|dept)\s+(?:of|for)\s', phrase):
                    return True
    return False


def cands_in_sentence(s, ctx, news_y, line, kind):
    """一个句子里的候选 -> [(tier, year, month, future, q_span, explicit_year)]"""
    out = []
    if OTHER_PERSON.search(s) or EXCLUDE_SENT.search(s) or re.search(r'(?i)on\s+leave|until\s+\w+\s+20[12]\d', line):
        return out
    for m in AP.finditer(s):
        a, b = m.start(1), m.end(1)
        if re.search(r'(?i)professors(?![a-z])', s[a:b + 1]):
            continue
        if AP_BAD_PREFIX.search(s[max(0, a - 30):a]) or AP_BAD_SUFFIX.match(s[b:b + 40]):
            continue
        mt = m.group(1).lower()
        # 职称和句子里的 AP 种类要对上（主页说 "Research Assistant Professor" 而现在是 Assistant Professor -> 不是同一份工作）
        is_res = 'research' in mt or re.search(r'(?i)research\s*$', s[max(0, a - 12):a])
        is_tea = 'teaching' in mt or re.search(r'(?i)teaching\s*$', s[max(0, a - 12):a])
        if bool(is_res) != ('Research' in ctx.title) or bool(is_tea) != ('Teaching' in ctx.title):
            continue
        if COURSE.search(s[max(0, a - 80):b + 80]):
            continue
        pre = s[max(0, a - 120):a]
        pre_clause = re.split(r'[.;:]\s|,\s+(?:and|but|where|while)\s|(?<=\d)\)', pre)[-1]
        org = nearest_org(s, a, b)
        if org and not org_is_current(org[0], org[1], ctx.uni):
            continue
        if ctx.foreign and not (org and org_is_current(org[0], org[1], ctx.uni)):
            continue        # 页面挂在别的大学域名下：必须明确写现单位
        if object_conflict(s[max(0, a - 150):b + 120], ctx):
            continue
        future = bool(FUTURE.search(s))
        # 过去时描述的 AP（"Previously, I was an Assistant Professor at ..."）
        past = PAST_AP.search(pre_clause)
        jm = None
        for j in JOIN.finditer(s):
            if j.end() <= a and a - j.end() <= 140 or j.start() >= b and j.start() - b <= 60 or \
                    j.start() >= b and re.match(r'(?i)joined|joining', j.group(1)) and \
                    re.search(r'(?i)(?:which|where)\s+(?:I|he|she)\s+$|,\s*$', s[max(b, j.start() - 15):j.start()]):
                # 入职动词优先于 "I am / has been" 这类状态动词，其次看离职称的距离
                key = (bool(STATIVE.match(j.group(1))), abs(j.start() - a))
                if jm is None or key < jkey:
                    jm, jkey = j, key
        # P2 since
        sm = None
        for x in SINCE.finditer(s):
            if 0 <= x.start() - b <= 120 or 0 <= a - x.end() <= 120:
                if sm is None or abs(x.start() - a) < abs(sm.start() - a):
                    sm = x
        # P3 开放区间：离职称最近的那个（80 字以内）
        rm, rd = None, None
        for x in list(OPEN_RANGE2.finditer(line)) + (list(OPEN_RANGE.finditer(s)) if kind in ('cv', 'sub', 'home') else []):
            if line is not s and x.re is OPEN_RANGE2:
                d = 0           # 区间独占一行，和职称行拼在一起
            else:
                gap = s[b:x.start()] if x.start() >= b else s[x.end():a]
                d = len(clean(gap)) if (x.start() >= b or x.end() <= a) else -1
            if 0 <= d <= 120 and (rd is None or d < rd):
                # 区间和职称之间不能隔着别的区间 / 别的职位
                between = s[min(x.end(), b):max(x.start(), a)] if line is s else ''
                if re.search(r'20[12]\d', between) or re.search(OTHER_ROLE.pattern.replace('director|', ''), between):
                    continue
                rm, rd = x, d
        verb = jm.group(1).lower() if jm else ''
        stative = verb in ('have been', 'has been', 'i am', "i'm", 'i’m', 'is an', 'is a', 'am now', 'will be') or \
            re.match(r'(?:is|i)\s', verb)
        if past and not (jm and not stative and jm.start() > past.start()):
            continue
        if jm and not stative and subject_ok(s, jm.start(), ctx):
            yr = pick_year(s, min(jm.start(), a), max(jm.end(), b), news_y)
            # 将来时却没写年份（"August 2021: I defended ... and will be joining UMD"）：新闻日期不是入职时间
            if yr and not (yr[2] is None and STRONG_FUTURE.search(s)):
                out.append((1, yr[0], yr[1], future and yr[2] is not None, (min(jm.start(), a), b)))
                continue
        if sm and subject_ok(s, min(sm.start(), a), ctx):
            y = int(sm.group(1))
            out.append((1, y, year_month(sm.group(0)), future, (min(sm.start(), a), max(sm.end(), b))))
            continue
        if rm:
            y = int(rm.group(1))
            out.append((2, y, year_month(rm.group(0)), False, (a, b)))
            continue
        if jm and verb == 'will be' and subject_ok(s, jm.start(), ctx):
            yr = pick_year(s, min(jm.start(), a), b, None)
            if yr:
                out.append((1, yr[0], yr[1], True, (jm.start(), b)))
                continue
        # 无动词的新闻行："08/2025 Assistant Professor at Clemson"
        if news_y and news_y[1] and len(clean(s)) < 120 and re.match(r'(?i)\s*(?:tenure[- ]track\s+)?(?:assistant|asst)', s) and \
                not OTHER_ROLE.search(s) and not future:
            out.append((2, news_y[0], news_y[1], False, (a, b)))
    return out


def cands_noap(s, ctx, news_y):
    """P4：不带职称的入职年份（"Lombardi joined the Princeton faculty in September 2023"、"Before joining UVA in 2024, ..."）。"""
    out = []
    if OTHER_PERSON.search(s) or EXCLUDE_SENT.search(s) or AP.search(s):
        return out
    # 院系主页的结构化字段："Joined department: Fall 2025"（Purdue）
    m = re.match(r'(?i)\s*joined\s+(?:the\s+)?department\s*:\s*((?:' + SEASON + r'\s+|' + MON + r'\s+)?)' + YEAR + r'\s*$', s)
    if m and not ctx.foreign:
        return [(2, int(m.group(2)), year_month(m.group(1)), False, (0, len(s)))]
    for m in re.finditer(r'(?i)(?<![a-z])(?:before|prior\s+to)\s+joining|(?<![a-z])(?:joined|moved\s+to|came\s+to|'
                         r'arrived\s+at|(?:will\s+(?:be\s+)?|am\s+|is\s+|be\s+)join(?:ing)?)(?![a-z])', s):
        before_join = bool(re.match(r'(?i)before|prior', m.group(0)))
        will_join = bool(re.match(r'(?i)will|am|is|be', m.group(0)))
        rest = s[m.end():m.end() + 160]
        ym = re.match(r'\s+(?:the\s+)?(?P<obj>[^.;]{2,90}?)\s+(?:in|since|from|on)\s+(?:the\s+)?'
                      r'(?P<when>(?:' + SEASON + r'\s+(?:of\s+)?|' + MON + r'\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?|\d{1,2}/)?)'
                      + YEAR + r'(?P<after>[^.;]{0,80})', rest)
        # 年份在前："Starting in Winter 2025, I will be joining the ECE Department at UW-Madison"
        lead = re.search(r'(?i)(?:^|[.;]\s)\s*(?:starting|beginning|from|in|since|as\s+of|effective)\s+(?:in\s+)?(?:the\s+)?'
                         r'(?P<when>(?:' + SEASON + r'\s+(?:of\s+)?|' + MON + r'\s+(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?)?)'
                         + YEAR + r'\s*,?\s*(?:I|he|she|[A-Z][a-z]+)\s+(?:have\s+|has\s+)?$', s[:m.start()])
        if lead and not before_join:
            ym3 = re.match(r'\s+(?:the\s+)?(?P<obj>[^.;!]{2,120}?)\s*(?:[.;!]|$)', rest)
            if not ym3:
                continue
            obj, y = ym3.group('obj'), int(lead.group(2))
            mon = year_month(lead.group('when') or '')
            after = ''
        elif ym:
            obj, y = ym.group('obj'), int(ym.group(3))
            mon = year_month(ym.group('when') or '')
            after = ym.group('after')
        else:
            # 新闻行 "Sept. 2023: Joined Dam Nation! (Started at OSU)"、"2024 Moved to Georgia Tech!"
            ym2 = re.match(r'\s+(?:the\s+)?(?P<obj>[^.;!]{2,90}?)\s*(?:[.;!]|$)', rest)
            if not (news_y and ym2 and (m.start() <= 3 or m.start() <= 25 and subject_ok(s, m.start(), ctx))) or \
                    before_join or (will_join and not re.search(r'(?i)this\s+(?:fall|spring|summer|winter|semester)', rest)):
                continue
            obj, y, mon, after = ym2.group('obj'), news_y[0], news_y[1], ''
            obj = re.sub(r'(?i)\s+this\s+(?:fall|spring|summer|winter|semester).*$', '', obj)
        if re.search(r'(?i)(?<![a-z])(?:and\s+(?:will|is|am|has|have|was|then|teach\w*|lead\w*|direct\w*|start\w*|join\w*|became|i|he|she)|but|will|'
                     r'to\s+(?:work|teach|lead|start)|where|teach\w*|after|for|i|he|she|'
                     r'they|we|my|his|her|their|obtained|received|earned|ph\.?d|degree|graduat\w*)(?![a-z])', obj):
            continue
        bad_at = False
        for am in re.finditer(r'(?<![A-Za-z])at\s+(?:the\s+)?((?:[A-Z][\w&\'’.-]*\s*)+)', obj):
            ph = am.group(1).strip()
            if ph.split()[0] in ACRONYMS:
                bad_at |= ctx.uni not in ACRONYMS[ph.split()[0]]
            elif not org_is_current(ph, 'name', ctx.uni):
                bad_at = True
        if bad_at:
            continue
        if will_join and not re.search(r'(?i)(?<![a-z])(?:i|he|she|' + '|'.join(re.escape(t) for t in ctx.name_toks) +
                                       r')\s+(?:will|am|is|shall)', s[max(0, m.start() - 40):m.end()]):
            continue
        # "He joined Georgia Tech in 2014, and is currently the Associate Chair ..."：后文是资深职务，不是新 AP
        if re.search(r'(?i)(?<![a-z])(?:associate|chair|dean|full\s+professor|tenured|emerit\w*)(?![a-z])', after):
            continue
        if OTHER_ROLE.search(obj) or (OTHER_ROLE.search(after[:40]) and not before_join):
            continue
        if not before_join and OTHER_ROLE.search(s[:m.start()][-80:]):
            continue
        if re.search(r'(?i)(?<![a-z])as\s+an?\s+(?!faculty|tenure|member\s+of\s+the\s+faculty)', obj + ' ' + after[:30]):
            continue
        if re.search(r'(?i)(?<![a-z])(lab|group|team|company|google|microsoft|meta|amazon|ibm|nvidia|apple|startup|board|'
                     r'committee|editorial|program\s+committee|panel|workshop|conference|society|ieee|acm|network|'
                     r'consortium|initiative|alliance|center\s+for|institute\s+for)(?![a-z])', obj):
            continue
        if object_conflict('at ' + obj, ctx):
            continue
        orgs = org_mentions(obj)
        cur_named = any(org_is_current(t, k, ctx.uni) for _, _, t, k in orgs)
        if orgs and not cur_named:
            continue
        if not orgs:
            toks = [t for t in tokens(ctx.uni) if len(t) > 3]
            pat = r'(?i)(?<![a-z])(?:faculty|department|dept|school|college|division'
            pat += ''.join('|' + re.escape(t) for t in toks) + r')(?![a-z])'
            if not re.search(pat, obj):
                continue
        if ctx.foreign and not cur_named:
            continue
        if not subject_ok(s, m.start(), ctx):
            continue
        out.append((3, y, mon, bool(FUTURE.search(s)), (m.start(), min(len(s), m.end() + len(rest)))))
    return out


def find(pid, ctx):
    cands = []
    pages = load_pages(pid)
    for pi, (url, text, kind) in enumerate(pages):
        if kind == 'home' and PROMOTED.search(text[:20000]):
            return None, 'promoted'
        ctx.set_page(url)
        lines = text.split('\n')
        prev_year = None
        for li, line in enumerate(lines):
            if not line.strip():
                continue
            hy = YEAR_HEADER.match(line)
            if hy:
                prev_year = int(hy.group(1))
                continue
            nd = news_year(line, prev_year)
            body_start = 0
            news_y = None
            if nd:
                news_y = (nd[0], nd[1])
                body_start = nd[2]
                if re.match(r'\s*[\[(]?\s*20[12]\d', line):
                    prev_year = nd[0]
            # CV：日期区间独占一行（或和单位名一行），相邻行是职称
            ctx_line = line
            if AP.search(line) and not re.search(r'20[12]\d', line):
                for nb in (lines[li - 1] if li > 0 else '', lines[li + 1] if li + 1 < len(lines) else ''):
                    if len(nb.strip()) < 100 and OPEN_RANGE2.search(nb) and not AP.search(nb) and \
                            not OTHER_ROLE.search(nb):
                        ctx_line = nb + ' ' + line
                        break
            body = line[body_start:]
            sents = SENT_SPLIT.split(body) if len(body) > 250 else [body]     # 短行（CV 条目）不切句
            for si, s in enumerate(sents):
                if not s.strip():
                    continue
                ny = news_y if si == 0 else None
                lc = ctx_line if (ctx_line is not line and AP.search(s)) else s
                for tier, y, mon, fut, (qa, qb) in cands_in_sentence(s, ctx, ny, lc, kind) + cands_noap(s, ctx, ny):
                    if not YMIN <= y <= YMAX:
                        continue
                    # 新闻是过去发的、日期不早于所说的入职时间：那时的 "am joining" 现在已经不是将来
                    if fut and ny and (ny[0], ny[1] or 0) >= (y, mon or 0) and (ny[0], ny[1] or 0) <= (NOW_Y, NOW_M):
                        fut = False
                    q = short_q(s, qa, qb)
                    if ny and not re.search(r'20[12]\d', q):
                        q = clean(line[:body_start]) + ' ' + q
                    if lc is not s:
                        q = clean(lc)
                    cands.append(dict(tier=tier, y=y, mon=mon, fut=fut, q=q[:200], src=url, kind=kind, pi=pi))
    if not cands:
        return None, 'none'
    # 选：P1/P2/P3（tier 1、2）合在一起，没有才用 P4（tier 3）；过去时 / 现在时优先于将来时
    best_tier = min(c['tier'] for c in cands)
    pool = [c for c in cands if c['tier'] <= 2] if best_tier <= 2 else [c for c in cands if c['tier'] == 3]
    nonfut = [c for c in pool if not c['fut']]
    fut = [c for c in pool if c['fut']]
    if nonfut and fut:
        # 旧的 "will join in 2021" 和后来的 "joined in 2021" 并存很常见；将来时年份更晚而且 >= 今年才信将来时
        pool = fut if max(c['y'] for c in fut) > max(c['y'] for c in nonfut) and max(c['y'] for c in fut) >= NOW_Y \
            else nonfut
    votes = Counter(c['y'] for c in pool)
    if all(c['fut'] for c in pool):
        y = max(votes)          # 多个将来时说法取最新的
    else:
        # AP 起始年份取最早的（后来加的冠名教职 "Hamelsky Family Assistant Professor 2026-" 不是起点）；
        # 但只出现一次、比多数票早 3 年以上的当噪声
        y = min(votes)
        top_y, top_n = votes.most_common(1)[0]
        if votes[y] == 1 and top_n >= 2 and top_y - y >= 3:
            y = top_y
    pool2 = [c for c in pool if c['y'] == y]
    pool2.sort(key=lambda c: (c['tier'], {'home': 0, 'sub': 1, 'cv': 2, 'recruit': 3}[c['kind']], c['pi']))
    c = pool2[0]
    return dict(y=c['y'], q=c['q'], src=c['src'], incoming=is_incoming(c['y'], c['mon'], c['fut']),
                _tier=c['tier'], _conflict=sorted(votes.items()) if len(votes) > 1 else None), 'ok'


def load_profs():
    with open(os.path.join(ROOT, 'data/raw/professors.csv'), encoding='utf-8', newline='') as f:
        rows = list(csv.reader(f))[1:]
    return {f'p{i}': dict(uni=r[0].strip(), name=r[1].strip()) for i, r in enumerate(rows)}


def ap_titles():
    """{pid: title}，titles.json 被 directory.json 的 "ti" 覆盖后，只留 Assistant (Research|Teaching) Professor。"""
    t = json.load(open(os.path.join(ROOT, 'data/enrich/titles.json'), encoding='utf-8'))
    d = json.load(open(os.path.join(ROOT, 'data/enrich/directory.json'), encoding='utf-8'))
    ti = dict(t)
    for k, v in d.items():
        if isinstance(v, dict) and v.get('ti'):
            ti[k] = v['ti']
    return {k: v for k, v in sorted(ti.items(), key=lambda kv: int(kv[0][1:]))
            if re.match(r'^Assistant (Research |Teaching )?Professor', v)}


def main():
    global DOMAIN_OWNER
    debug = '--debug' in sys.argv
    sample = None
    if '--sample' in sys.argv:
        sample = int(sys.argv[sys.argv.index('--sample') + 1])
    only = None
    if '--pid' in sys.argv:
        only = sys.argv[sys.argv.index('--pid') + 1].split(',')
    DOMAIN_OWNER = domain_owners()
    ALL_UNI_TOKENS.update(_all_uni_tokens())
    profs = load_profs()
    titles = ap_titles()
    out, stat = {}, Counter()
    for pid, title in titles.items():
        if only and pid not in only:
            continue
        r, why = find(pid, Ctx(pid, profs[pid], title))
        stat[why] += 1
        if r:
            out[pid] = r
    res = {p: {k: v for k, v in r.items() if not k.startswith('_')} for p, r in out.items()}
    if not only:
        with open(os.path.join(ROOT, 'data/enrich/ap_since.json'), 'w', encoding='utf-8') as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    n = sum(stat.values())
    print(f'AP 教授 {n} 人，找到年份 {len(res)} 人（{len(res) / max(1, n):.0%}），'
          f'incoming {sum(r["incoming"] for r in res.values())} 人；{dict(stat)}')
    print('按年份:', dict(sorted(Counter(r['y'] for r in res.values()).items())))
    print('按规则层:', dict(sorted(Counter(r['_tier'] for r in out.values()).items())))
    if debug or sample or only:
        items = list(out.items())
        if sample:
            random.seed(int(os.environ.get('SEED', '7')))
            items = random.sample(items, min(sample, len(items)))
        for p, r in items:
            print(f"{p}\t{profs[p]['name']} | {profs[p]['uni']}\t{r['y']}{' INCOMING' if r['incoming'] else ''}"
                  f"\tT{r['_tier']}{' CONFLICT ' + str(r['_conflict']) if r['_conflict'] else ''}\n    {r['q']}\n    {r['src']}")


if __name__ == '__main__':
    main()
