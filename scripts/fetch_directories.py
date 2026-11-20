"""从各校 CS 系官方教师名录（faculty directory）抓职称和照片，比个人主页更可靠。

每个学校一小段配置（名录网址；有分页的自动翻页）。解析用通用方法：在页面里找和教授名单
（同一学校）对得上的人名元素，往上找到这个人的"名片"块，取块里第一行像职称的短文字和第一张照片。
人名匹配不分大小写/重音，中间名、首字母可有可无，支持 "姓, 名" 写法。
职称规整沿用 extract_titles.py（保留 Teaching / Research / of the Practice 区别和 Emeritus，
去掉系名、Chair 等后缀）。需要 JS 才能显示的名录：能找到页面自己读的公开数据（WordPress REST、Drupal JSON:API、
页面脚本里的数组、静态 JSON 文件）就直接读（见 JSON_SOURCES），找不到就跳过；对脚本返回 403 / 反爬页的不绕过。
人名对不上时（"Bill" / "William"）再用链接地址对 CSRankings 主页（链接文字要像人名、且同姓）。
遵守 robots.txt，同一主机两次请求至少间隔 2 秒；页面缓存在 data/cache/directories/，重跑不再联网。
输出: data/enrich/directory.json  {pid: {"ti": 职称, "ph": 照片绝对地址, "src": 名录网址}}
      累加合并：已有条目不改不删，只补缺的职称 / 照片；名录比主页职称正好低一级的（名录没跟上升职）不写。
用法: python scripts/fetch_directories.py             抓取（用缓存）+ 解析 + 和 titles.json 对比
      python scripts/fetch_directories.py --refresh   忽略缓存重新抓
      python scripts/fetch_directories.py --only cmu,vt  只处理某几个学校（配置里的 tag）
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from urllib.parse import urljoin, urlparse

import lxml.html
import requests

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
import fetch_pages as fp  # noqa: E402
from fetch_pages import allowed, UA, HOST_GAP  # noqa: E402
from names import fold, key, display_name  # noqa: E402
from extract_titles import TITLE, norm  # noqa: E402

CACHE = os.path.join(ROOT, 'data/cache/directories')
RESULT = os.path.join(ROOT, 'data/enrich/directory.json')
TITLES = os.path.join(ROOT, 'data/enrich/titles.json')
MAX_PAGES = 60

# 每校配置：tag、学校名（和 professors.csv 一致）、名录网址（按优先级；同一人以先出现的为准）
SITES = [
    ('neu', 'Northeastern University', [
        'https://www.khoury.northeastern.edu/people/?filters%5B%5D=people_roles%7C931',   # 终身/终身轨
        'https://www.khoury.northeastern.edu/people/?filters%5B%5D=people_roles%7C928',   # 教学岗
        'https://www.khoury.northeastern.edu/people/?filters%5B%5D=people_roles%7C926']),  # 研究岗
    ('cmu', 'Carnegie Mellon University', [
        'https://csd.cmu.edu/people/faculty',
        'https://lti.cmu.edu/people/faculty/index.html',
        'https://hcii.cmu.edu/people/faculty',
        'https://www.ri.cmu.edu/people/faculty/',
        'https://www.ece.cmu.edu/directory/faculty.html']),
    ('mit', 'Massachusetts Institute of Technology', ['https://www.eecs.mit.edu/role/faculty/']),
    ('stanford', 'Stanford University', ['https://www.cs.stanford.edu/people-cs/faculty-name']),
    ('berkeley', 'Univ. of California - Berkeley', ['https://www2.eecs.berkeley.edu/Faculty/Lists/faculty.html']),
    ('uiuc', 'Univ. of Illinois at Urbana-Champaign', ['https://siebelschool.illinois.edu/about/people/all-faculty']),
    ('uw', 'University of Washington', [
        'https://www.cs.washington.edu/people/faculty-members/',
        'https://www.cs.washington.edu/people/emeritus/',
        'https://www.cs.washington.edu/people/adjunct/',
        'https://www.cs.washington.edu/people/affiliate/']),
    ('gatech', 'Georgia Institute of Technology', ['https://www.cc.gatech.edu/people/faculty']),
    ('cornell', 'Cornell University', ['https://www.cs.cornell.edu/directory']),
    ('ucsd', 'Univ. of California - San Diego', ['https://cse.ucsd.edu/people/faculty-profiles/faculty',  # 只有照片
                                                 'https://ece.ucsd.edu/people/faculty']),
    ('umd', 'University of Maryland - College Park', ['https://www.cs.umd.edu/people/faculty',
                                                      'https://www.umiacs.umd.edu/people/faculty']),
    ('utexas', 'University of Texas at Austin', ['https://www.cs.utexas.edu/people']),
    ('princeton', 'Princeton University', ['https://www.cs.princeton.edu/people/faculty']),
    ('columbia', 'Columbia University', ['https://www.cs.columbia.edu/people/faculty/']),
    ('nyu', 'New York University', ['https://cs.nyu.edu/dynamic/people/faculty/']),
    ('usc', 'University of Southern California', ['https://www.cs.usc.edu/directory/faculty/']),
    ('umass', 'University of Massachusetts Amherst', [
        'https://www.cics.umass.edu/about/directory?s=&field_person__profile_type_ref_target_id%5B351%5D=351']),
    ('purdue', 'Purdue University', ['https://www.cs.purdue.edu/people/faculty/index.html']),
    ('harvard', 'Harvard University', ['https://seas.harvard.edu/computer-science/people']),
    ('jhu', 'Johns Hopkins University', ['https://www.cs.jhu.edu/faculty/']),
    ('upenn', 'University of Pennsylvania', ['https://directory.seas.upenn.edu/computer-and-information-science/']),
    ('northwestern', 'Northwestern University', [
        'https://www.mccormick.northwestern.edu/computer-science/people/faculty/']),
    ('uci', 'Univ. of California - Irvine', ['https://cs.ics.uci.edu/faculty/']),
    ('utah', 'University of Utah', ['https://www.cs.utah.edu/people/faculty/']),
    ('ucsc', 'Univ. of California - Santa Cruz', ['https://engineering.ucsc.edu/people/faculty/']),  # 全工学院
    # ---- 2026-11 补充（按缺职称人数从多到少） ----
    ('vt', 'Virginia Tech', ['https://cs.vt.edu/people/faculty.html']),
    # U Michigan：CSE / ECE（*.engin.umich.edu）对脚本返回 403，只能用信息学院和机器人系的名录
    ('umich', 'University of Michigan', ['https://www.si.umich.edu/people/directory/faculty',
                                         'https://robotics.umich.edu/people/faculty/']),
    # UW–Madison 名单由 JS 从 WordPress REST 接口加载，直接读接口（uw_staff_type: 5 核心教师、18 教学岗、
    # 12 合聘、15 荣休）
    ('wisc', 'University of Wisconsin - Madison', [
        ('wp_uw', f'https://www.cs.wisc.edu/wp-json/wp/v2/uw_staff?uw_staff_type={t}&per_page=100')
        for t in (5, 18, 12, 15)]),
    ('stonybrook', 'Stony Brook University', ['https://www.cs.stonybrook.edu/people/faculty']),
    ('uchicago', 'University of Chicago', ['https://cs.uchicago.edu/people/']),
    ('gmu', 'George Mason University', ['https://cs.gmu.edu/people/faculty/']),
    ('tamu', 'Texas A&M University', [('tamu', 'https://engineering.tamu.edu/profile-data.json')]),  # 全工学院
    ('colorado', 'University of Colorado Boulder', [
        ('drupal_ucb', 'https://www.colorado.edu/cs/jsonapi/node/ucb_person?page%5Blimit%5D=50')]),
    ('bu', 'Boston University', ['https://www.bu.edu/cs/people/faculty/']),
    ('ncsu', 'North Carolina State University', ['https://www.csc.ncsu.edu/people/']),
    ('vanderbilt', 'Vanderbilt University', ['https://computing.vanderbilt.edu/faculty/']),
    ('ucf', 'University of Central Florida', ['https://www.cs.ucf.edu/people/faculty/',
                                              'https://www.ece.ucf.edu/people/faculty/']),
    ('rutgers', 'Rutgers University', ['https://www.cs.rutgers.edu/people/professors']),
    ('buffalo', 'University at Buffalo', [
        'https://engineering.buffalo.edu/computer-science-engineering/people/faculty-directory.html']),
    ('psu', 'Pennsylvania State University', ['https://www.eecs.psu.edu/departments/cse-faculty-list.aspx',
                                              'https://ist.psu.edu/directory/faculty']),
    ('ufl', 'University of Florida', ['https://www.cise.ufl.edu/people/faculty/', 'https://www.ece.ufl.edu/people/faculty/']),
    ('utd', 'University of Texas at Dallas', ['https://cs.utdallas.edu/people/faculty/']),
    ('nd', 'University of Notre Dame', ['https://cse.nd.edu/faculty/']),
    ('utsa', 'University of Texas at San Antonio', ['https://caicc.utsa.edu/computer-science/faculty/']),
    ('utk', 'University of Tennessee', ['https://eecs.utk.edu/people/faculty/']),
    ('usu', 'Utah State University', [('usu', 'https://engineering.usu.edu/cs/directory/faculty/')]),  # 名单在页面脚本里
    ('uga', 'University of Georgia', ['https://computing.uga.edu/directory/faculty']),
    ('brown', 'Brown University', ['https://cs.brown.edu/people/faculty/']),
    ('umn', 'University of Minnesota', ['https://cse.umn.edu/cs/faculty']),
    ('byu', 'Brigham Young University', ['https://cs.byu.edu/people/']),
    ('clemson', 'Clemson University', ['https://www.clemson.edu/cecas/departments/computing/people/index.html']),
    ('dartmouth', 'Dartmouth College', ['https://web.cs.dartmouth.edu/people']),
    ('pitt', 'University of Pittsburgh', ['https://www.cs.pitt.edu/people/faculty', 'https://www.sci.pitt.edu/people/faculty']),
    ('caltech', 'California Institute of Technology', ['https://www.cms.caltech.edu/people/faculty',
                                                        'https://www.eas.caltech.edu/people/faculty']),
    ('wpi', 'Worcester Polytechnic Institute', ['https://www.wpi.edu/academics/departments/computer-science/faculty-staff']),
    ('duke', 'Duke University', ['https://cs.duke.edu/people/faculty', 'https://ece.duke.edu/faculty']),
    ('ucsb', 'Univ. of California - Santa Barbara', ['https://cs.ucsb.edu/people/faculty']),
    ('uconn', 'University of Connecticut', ['https://www.cse.uconn.edu/people/faculty/']),
    ('fsu', 'Florida State University', ['https://www.cs.fsu.edu/people/faculty/']),
    ('wm', 'College of William and Mary', ['https://www.wm.edu/as/computerscience/people/index.php']),
    ('stevens', 'Stevens Institute of Technology', [
        'https://www.stevens.edu/school-engineering-science/departments/computer-science/faculty']),
    ('wustl', 'Washington University in St. Louis', ['https://engineering.wustl.edu/faculty/index.html']),  # 全工学院
    ('tufts', 'Tufts University', ['https://engineering.tufts.edu/cs/people/faculty', 'https://www.cs.tufts.edu/people/faculty']),
    ('gwu', 'George Washington University', ['https://cs.seas.gwu.edu/faculty']),
    ('uh', 'University of Houston', ['https://www.uh.edu/nsm/computer-science/people/faculty/']),
    ('gsu', 'Georgia State University', ['https://csds.gsu.edu/directory/']),
    ('uncc', 'UNC - Charlotte', ['https://cci.charlotte.edu/directory/faculty/']),
    ('rochester', 'University of Rochester', ['https://www.cs.rochester.edu/people/faculty/index.html']),
    ('udel', 'University of Delaware', ['https://www.cis.udel.edu/people/faculty/']),
    ('njit', 'NJIT', ['https://cs.njit.edu/faculty']),
    ('iastate', 'Iowa State University', ['https://www.cs.iastate.edu/people/faculty']),
    ('ou', 'University of Oklahoma', ['https://www.ou.edu/coe/cs/people/faculty']),
    ('wayne', 'Wayne State University', ['https://engineering.wayne.edu/computer-science/faculty']),
    ('rpi', 'Rensselaer Polytechnic Institute', ['https://science.rpi.edu/computer-science/faculty']),
    ('ku', 'University of Kansas', ['https://eecs.ku.edu/faculty']),
    ('ohio', 'Ohio University', ['https://www.ohio.edu/engineering/eecs/people']),
    ('mtu', 'Michigan Technological University', ['https://www.mtu.edu/cs/department/faculty/']),
    ('temple', 'Temple University', ['https://cis.temple.edu/people/faculty']),
    # 跳过（2026-11 再试过）：
    #   对脚本返回 403 / 401（反爬，不绕过）：U Michigan（cse / ece / eecs.engin.umich.edu）、UVA、UIC、Ohio State、
    #     CUNY Graduate Center、UNC、UC Davis、JHU ECE、Princeton ECE、UM-Dearborn、UC Merced、Hawaii、Tulane
    #   UC Irvine：www.ics.uci.edu/faculty/ 跳到 "Access Notice" 反爬页
    #   robots.txt 不允许：FIU（cis.fiu.edu）、RIT（cs.rit.edu）、Indiana（名单 JS 在 /_assets/ 下，被 robots 禁止）
    #   TLS 证书链不全（requests 校验失败，不关校验）：Michigan State（cse.msu.edu）、UT Arlington（cse.uta.edu）
    #   名单由 JS 加载、找不到公开数据接口：UCLA（samueli.ucla.edu 搜索页走 admin-ajax POST）、RIT、Yale、Rice、
    #     UMass Lowell、Arizona State、Oregon State、UC Riverside（profiles.ucr.edu 单页应用）、UNT、Drexel、
    #     DePaul、Washington State、Syracuse
]

_last, _host_locks, _glock = {}, {}, threading.Lock()


def get(url, refresh=False):
    """带缓存的礼貌抓取：robots.txt + 同主机间隔 2 秒。返回 (html, 最终网址) 或 (None, 原因)。"""
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest()[:16] + '.json')
    if not refresh and os.path.exists(fn):
        d = json.load(open(fn, encoding='utf-8'))
        return d['html'], d['url']
    host = urlparse(url).netloc
    with _glock:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:  # 取 robots.txt 也算一次请求，之后同样隔 2 秒
        fresh = f'{urlparse(url).scheme}://{host}' not in fp._robots
        ok = allowed(url)
        if fresh:
            _last[host] = time.time()
    if not ok:
        return None, 'robots'
    err = 'error'
    for attempt in range(3):
        with lock:
            wait = _last.get(host, 0) + HOST_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                r = requests.get(url, headers={'User-Agent': UA}, timeout=45)
            except requests.RequestException as e:
                err = type(e).__name__
                continue
            finally:
                _last[host] = time.time()
        if r.status_code != 200:
            return None, str(r.status_code)
        html = r.content.decode(r.encoding or 'utf-8', errors='replace') if r.encoding else r.text
        json.dump({'url': r.url, 'html': html}, open(fn, 'w', encoding='utf-8'), ensure_ascii=False)
        return html, r.url
    return None, err


PAGE_RX = re.compile(r'([?&]page=(?:%2C|,)?)(\d+)|(/page/)(\d+)(/)')


def page_urls(doc, url):
    """找分页链接（Drupal ?page=N 或 WordPress /page/N/），返回其余各页网址。"""
    base = urlparse(url)
    base_path = re.sub(r'/page/\d+/?', '/', base.path)
    found = {}
    for a in doc.iter('a'):
        h = a.get('href')
        if not h:
            continue
        h = urljoin(url, h)
        p = urlparse(h)
        if p.netloc != base.netloc or re.sub(r'/page/\d+/?', '/', p.path) != base_path:
            continue
        m = PAGE_RX.search(h)
        if m:
            found[int(m.group(2) or m.group(4))] = h
    if not found:
        return []
    zero = 0 in found
    tmpl = found[max(found)]
    out = []
    for n in range(1 if zero else 2, min(max(found), MAX_PAGES) + 1):
        out.append(PAGE_RX.sub(lambda m: (m.group(1) + str(n)) if m.group(1) else (m.group(3) + str(n) + m.group(5)),
                               tmpl, count=1))
    return out


def html_doc(html):
    # 带 <?xml encoding=...?> 声明的字符串 lxml 不收（RPI）
    return lxml.html.fromstring(re.sub(r'^\s*<\?xml[^>]*\?>', '', html))


# ---------- 人名匹配 ----------
STRIP_NAME = re.compile(r'\((?:[^)]*)\)|\b(?:Dr|Prof|Professor|Ph\.?\s?D|PhD|MBA|M\.?D)\b\.?|[»«›‹|•]', re.I)
NAMEISH = re.compile(r"^[A-Z][\w'’.\-]*(?:,?\s+[A-Za-z][\w'’.\-]*){1,5}$")
TITLE_WORDS = re.compile(r'(?i)\b(professor|lecturer|instructor|faculty|scientist|chair|dean|director|emerit)')


def clean_name(t):
    t = STRIP_NAME.sub(' ', t)
    t = re.sub(r'\s+', ' ', t).strip(' ,;:-')
    parts = [x.strip() for x in t.split(',')]
    if len(parts) == 2 and parts[1] and not re.fullmatch(r'(?i)jr\.?|sr\.?|i+|iv', parts[1]):
        t = parts[1] + ' ' + parts[0]  # "Acar, Umut" → "Umut Acar"
    elif len(parts) > 1:
        t = parts[0]
    return t


def url_key(u):
    """主页地址规整：去协议、www.、末尾 / 和 index.html，不分大小写。"""
    u = (u or '').strip().lower()
    u = re.sub(r'^https?://', '', u).split('#')[0]
    u = re.sub(r'^www\d?\.', '', u)
    u = re.sub(r'/(index|default|home)\.(s?html?|php|aspx?)$', '', u)
    return u.rstrip('/')


NICK = [{'tom', 'thomas'}, {'jim', 'james', 'jimmy'}, {'hal', 'harold'}, {'kostas', 'konstantinos'}, {'izzy', 'isaac'},
        {'misha', 'michael', 'mikhail', 'mike'}, {'kate', 'katie', 'kathy', 'katherine', 'kathryn', 'kaitlin', 'katharine'},
        {'bill', 'will', 'william', 'billy'}, {'bob', 'rob', 'robby', 'robert', 'bobby'}, {'rick', 'dick', 'richard', 'rich'},
        {'steve', 'stephen', 'stephan', 'steven'}, {'ted', 'edward', 'ed'}, {'chuck', 'charles', 'charlie'},
        {'jack', 'john', 'jon', 'jonathan'}, {'sasha', 'alexander', 'alex'}, {'dima', 'dmitry', 'dmitri'},
        {'nick', 'nicholas', 'nikolaos', 'nikos', 'nicolas'}, {'fredo', 'frederic', 'fred', 'frederick'},
        {'dave', 'david'}, {'andy', 'andrew', 'drew'}, {'tony', 'anthony', 'antonio'}, {'joe', 'joseph', 'joey'},
        {'peggy', 'margaret'}, {'jerry', 'gerald'}, {'larry', 'lawrence'}, {'ken', 'kenneth'}, {'pete', 'peter'}]


LINK_WORDS = re.compile(r"(?i)\b(website|web|page|profile|homepage|home|lab|research|group|site)\b|['’]s\b")


def middle_initials(name):
    """中间名首字母（只算单个字母的："Michael D. Jones" → {'d'}）。"""
    toks = [t.strip('.') for t in display_name(name).split()[1:-1]]
    return {fold(t).lower() for t in toks if len(t) == 1 and t.isalpha()}


def same_given(a, b):
    """两个（已去重音小写的）名是不是同一个人的写法：一样、互为前缀（至少 3 个字母）、或常见昵称。"""
    if a == b:
        return True
    if len(min(a, b, key=len)) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return any(a in g and b in g for g in NICK)


class Matcher:
    def __init__(self, profs):
        self.by_key = defaultdict(set)
        self.by_last_init = defaultdict(set)
        self.by_url = defaultdict(set)
        self.surnames, self.initials = {}, {}
        for p in profs:
            self.initials[p['id']] = middle_initials(p['name'])
            k = key(p['name'])
            self.by_key[k].add(p['id'])
            uk = url_key(p.get('homepage'))
            if uk.count('/') >= 1:  # 只用带路径的主页（整站域名可能是实验室 / 系主页）
                self.by_url[uk].add(p['id'])
            self.surnames[p['id']] = set(fold(p['name']).split()[1:])
            toks = k.split()
            if len(toks) == 2:
                given = [t for t in fold(p['name']).split()[:-1] if len(t) > 1]  # 名 + 中间名
                self.by_last_init[(toks[1], toks[0][0])].add((p['id'], toks[0], tuple(given[1:])))
            # 多段姓（"Berg-Kirkpatrick"、"De la Torre"）：最后两段也作为姓
            ft = [t for t in fold(p['name']).split() if len(t) > 1]
            if len(ft) >= 3:
                self.by_key[ft[0] + ' ' + ft[-1]].add(p['id'])

    def match(self, text):
        t = clean_name(text)
        if not t or len(t) > 60 or not NAMEISH.match(t) or TITLE_WORDS.search(t):
            return None
        k = key(t)
        hit = self.by_key.get(k)
        if hit and len(hit) == 1:
            pid = next(iter(hit))
            # 两边都写了中间名首字母、却不一样（BYU 的 "Mike P. Jones" 和表里的 "Michael D. Jones"）：不是同一个人
            mi, pi = middle_initials(t), self.initials[pid]
            return pid if not (mi and pi and not mi & pi) else None
        toks = k.split()
        if len(toks) == 2:
            # 同姓、名字首字母相同：还要名字对得上——互为前缀（"Dan"/"Daniel"）、常见昵称（"Tom"/"Thomas"），
            # 或名录写的是中间名（"Jessy Li" / "Junyi Jessy Li"）。否则名录里的别人（ECE 的 "Zhaowei Liu"、
            # UW 的 "Ruth Anderson"）会被当成表里的 "Zhijian Liu"、"Richard Anderson"
            hit = self.by_last_init.get((toks[1], toks[0][0]))
            if hit and len(hit) == 1:
                pid, first, middle = next(iter(hit))
                if same_given(first, toks[0]) or toks[0] in middle:
                    return pid
        return None

    def match_url(self, href, text):
        """链接指向某位教授的主页、链接文字本身像人名且和他同姓（名字写法不同："Bill" / "William"）。"""
        hit = self.by_url.get(url_key(href))
        if not hit or len(hit) != 1:
            return None
        pid = next(iter(hit))
        t = clean_name(text)
        if not t or len(t) > 60 or not NAMEISH.match(t) or TITLE_WORDS.search(t) or LINK_WORDS.search(t):
            return None
        return pid if set(fold(t).split()) & self.surnames[pid] else None


# ---------- 名片块解析 ----------
BLOCK_TAGS = {'p', 'div', 'br', 'li', 'tr', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article',
              'dd', 'dt', 'figcaption', 'span', 'a', 'strong', 'em', 'ul', 'header', 'footer'}
BAD_IMG = re.compile(r'(?i)(logo|icon|sprite|placeholder|default[-_.]|blank|spacer|silhouette|no[-_]?photo|no[-_]?image|'
                     r'avatar|missing|wordmark|seal|banner|shims?/|1x1|\.svg(\?|$)|^data:)')


def lines_of(el):
    """块里的文字按行切开（块级标签、换行处断开）。"""
    out, cur = [], []

    def walk(e):
        if not isinstance(e.tag, str):
            if e.tail:
                cur.append(e.tail)
            return
        if e.tag in BLOCK_TAGS:
            out.append(''.join(cur)); cur.clear()
        if e.text:
            cur.append(e.text)
        for c in e:
            walk(c)
        if e.tag in BLOCK_TAGS:
            out.append(''.join(cur)); cur.clear()
        if e.tail:
            cur.append(e.tail)
    walk(el)
    out.append(''.join(cur))
    return [re.sub(r'\s+', ' ', x).strip() for x in out if x and x.strip()]


SENTENCE = re.compile(r'(?i)(is|was|are|has|have|her|his|their|research(?:es)?|works?|joined|received)')


def title_from(lines):
    """块里第一行像职称的短文字。同一行有多个时（MIT："ITT Career Development Professor ...; Associate
    Professor"），优先取单独成段的职称，冠名讲席（段首不是职称）靠后；Career Development 讲席不代表职级，跳过。"""
    fallback, left = None, 0
    for ln in lines:
        if fallback:
            # 冠名讲席单独一行，真实职级常在下一行（Notre Dame："Frank M. Freimann Collegiate Professor of CSE"
            # → "Associate Professor, CSE"）：往后看两行，有不带冠名的职称就用它
            left -= 1
            if left < 0:
                return fallback
        # 太长的一般是简介句子；冠名讲席的长职称（Stanford）允许到 260 字
        if len(ln) > 260 or (len(ln) > 120 and SENTENCE.search(ln)):
            continue
        if re.search(r'(?i)security\s+of\s+employment', ln):
            return 'Lecturer with Security of Employment'
        clean, named = [], []
        for m in TITLE.finditer(ln):
            if not m.group(2)[0].isupper():
                continue
            seg_start = max(ln.rfind(c, 0, m.start()) for c in ';,|/') + 1
            pre = ln[seg_start:m.start()].strip(' -–—:')
            if re.search(r'(?i)(former|previously|ph\.?d|student|postdoc|advis)', ln[:m.start()]):
                continue
            t = norm(m.group(1), m.group(2), m.group(3))
            if not t:
                continue
            if re.search(r'(?i)emerit', ln) and 'Emeritus' not in t:
                t += ' Emeritus'
            if not pre or re.fullmatch(r'(?i)(and|&|the|an?)', pre):
                clean.append(t)
            elif not re.search(r'(?i)career\s+development', pre):
                # 冠名的 "Collegiate Professor" 是讲席名，不是 VT 那种教学岗 Collegiate 职称
                named.append(re.sub(r'^Collegiate ', '', t))
        if clean:
            return clean[0]
        if named and not fallback:
            fallback, left = named[0], 2
    return fallback


def img_src(img):
    for a in ('data-src', 'data-lazy-src', 'data-original', 'src'):
        v = (img.get(a) or '').strip()
        if v and not v.startswith('data:'):
            return v
    ss = img.get('srcset') or img.get('data-srcset') or ''
    return ss.split(',')[0].split()[0] if ss.strip() else ''


def photo_from(card, page_url):
    for e in card.iter():
        if not isinstance(e.tag, str):
            continue
        src = ''
        if e.tag == 'img':
            src = img_src(e)
        elif e.get('data-bgset') or e.get('data-bg') or e.get('data-background-image'):
            v = e.get('data-bgset') or e.get('data-bg') or e.get('data-background-image')
            src = v.split(',')[0].split()[0]  # data-bgset 取第一个（通常最大）
        elif 'background-image' in (e.get('style') or ''):
            m = re.search(r'url\([\'"]?([^\'")]+)', e.get('style'))
            src = m.group(1) if m else ''
        if src and not BAD_IMG.search(src):
            return urljoin(page_url, src)
    return None


def sig(e):
    return (e.tag, (e.get('class') or '').strip())


def rel_path(e, top):
    # 叶子和父元素比较标签+class，再往上只比较标签（同一行左右两栏的 class 常不同）
    out = []
    while e is not None and e is not top:
        out.append(sig(e) if len(out) < 2 else e.tag)
        e = e.getparent()
    return tuple(out)


FUNC_WORDS = re.compile(r'(?i)(?<![\w])(and|of|the|for|in|on|with|to|&|\+)(?![\w])')


def name_like(e):
    t = re.sub(r'\s+', ' ', e.text_content()).strip()
    return 3 <= len(t) <= 60 and NAMEISH.match(clean_name(t) or 'x') and not TITLE_WORDS.search(t) \
        and not FUNC_WORDS.search(t) and '@' not in t and not re.search(r'\d', t)


def parse(html, page_url, matcher):
    doc = html_doc(html)
    # 页眉里的菜单不要（UMN 把整页正文都包在 header[role=banner] 里，含 main 的不删）
    # （UGA 的 HTML 没闭合，正文落在 nav 里面；含 main 的 nav 也不删）
    for bad in doc.xpath('//script|//style|//noscript|//nav[not(.//main) and not(.//*[@role="main"])]|'
                         '//header[@role="banner"][not(.//main) and not(.//*[@role="main"])]'):
        bad.drop_tree()
    # 读屏用的隐藏短文字（VT："Ali Butt<span class=sr-only>, bio</span>" 会被当成 "姓, 名"）；
    # Clemson 把整页正文包在 sr-only 里等 JS 显示，长的不删
    for bad in doc.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " sr-only ") or '
                         'contains(concat(" ", normalize-space(@class), " "), " visually-hidden ") or '
                         'contains(concat(" ", normalize-space(@class), " "), " element-invisible ")]'):
        if len(bad.text_content()) < 200:
            bad.drop_tree()
    hits = []  # (元素, pid)
    for e in doc.iter():
        if not isinstance(e.tag, str) or e.tag in ('html', 'body'):
            continue
        t = e.text_content()
        if len(t) > 120:
            continue
        t = re.sub(r'\s+', ' ', t).strip()
        pid = matcher.match(t)
        if not pid and e.tag == 'a' and e.get('href'):
            pid = matcher.match_url(urljoin(page_url, e.get('href')), t)
        if pid:
            hits.append((e, pid))
    # 只留最深的那个元素（外层包着同样文字的不要）
    hit_ids = {(id(e), pid) for e, pid in hits}
    deep = []
    for e, pid in hits:
        if any((id(c), pid) in hit_ids for c in e.iterdescendants()):
            continue
        deep.append((e, pid))
    name_els = {id(e): pid for e, pid in deep}
    out = {}
    for e, pid in deep:
        s = sig(e)
        card, a = e, e
        for _ in range(8):
            a = a.getparent()
            if a is None or a.tag in ('body', 'html'):
                break
            if len(a.text_content()) > 2500:
                break
            others = {name_els[id(d)] for d in a.iterdescendants() if id(d) in name_els} - {pid}
            # 同一位置结构（从 a 到名字元素的标签路径）出现第二个像人名的元素 → a 已经包住了好几张名片
            path = rel_path(e, a)
            same = sum(1 for d in a.iterdescendants() if isinstance(d.tag, str) and sig(d) == s
                       and rel_path(d, a) == path and name_like(d))
            if others or same > 1:
                break
            card = a
        own = re.sub(r'\s+', ' ', e.text_content()).strip()
        lines = [ln for ln in lines_of(card) if ln != own]
        ti, ph = title_from(lines), photo_from(card, page_url)
        prev = out.get(pid)
        if prev is None:
            out[pid] = {'tis': set(), 'ph': None}
        if ti:
            out[pid]['tis'].add(ti)
        out[pid]['ph'] = out[pid]['ph'] or ph
    return doc, out


# ---------- 名单由 JS 加载的名录：直接读页面自己用的 JSON 数据 ----------
# 每个函数把一页原文（JSON / 含数据的 HTML）变成 ([{name, titles, ph, href}], 下一页网址或 None)
def _txt(h):
    return re.sub(r'\s+', ' ', lxml.html.fromstring(h).text_content()).strip() if h and h.strip() else ''


def json_wp_uw(text, url):
    """UW 主题 WordPress 的 /wp-json/wp/v2/uw_staff（UW–Madison CS）。"""
    j, out = json.loads(text), []
    for r in j:
        m = r.get('meta') or {}
        out.append(dict(name=f"{m.get('_uw_first_name', '')} {m.get('_uw_last_name', '')}".strip()
                        or _txt(r['title']['rendered']),
                        titles=[m.get('_uw_title') or ''], ph=m.get('_uw_people_image_url'), href=r.get('link')))
    nxt = None
    if len(j) >= 100:
        n = int(re.search(r'[?&]page=(\d+)', url).group(1)) if re.search(r'[?&]page=(\d+)', url) else 1
        nxt = re.sub(r'([?&])page=\d+', '', url) + f'&page={n + 1}'
    return out, nxt


def json_tamu(text, url):
    """Texas A&M 工学院 /profile-data.json（cfprofiles.js 用的全院名单）。"""
    j = json.loads(text)
    return [dict(name=r.get('name', ''), titles=r.get('titles') or [], ph=urljoin(url, r['image']) if r.get('image') else None,
                 href=urljoin(url, r.get('link') or '')) for r in j], None


def json_drupal_ucb(text, url):
    """CU Boulder Drupal 的 /jsonapi/node/ucb_person（JSON:API，分页走 links.next）。"""
    j, out = json.loads(text), []
    for r in j.get('data') or []:
        a = r.get('attributes') or {}
        ti = a.get('field_ucb_person_title') or []
        links = [x.get('uri') for x in a.get('field_ucb_person_links') or [] if x.get('uri')]
        out.append(dict(name=re.sub(r'\s+', ' ', a.get('title') or ''), titles=ti if isinstance(ti, list) else [ti],
                        ph=None, href=links[0] if links else None))
    nxt = ((j.get('links') or {}).get('next') or {}).get('href')
    return out, nxt


def js_usu(text, url):
    """Utah State：名单写在页面脚本的数组里 { firstName: "..", lastName: "..", link: "..", image: "..", title: ".." }
    （字段顺序、单双引号不固定；先去掉 /* */ 和 // 注释，注释掉的条目不算）。"""
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    text = re.sub(r'(?m)^\s*//.*$', ' ', text)
    out = []
    for obj in re.findall(r'\{([^{}]*\bfirstName\s*:[^{}]*)\}', text):
        f = {k: (a if a is not None and a != '' else b) for k, a, b in
             re.findall(r'(\w+)\s*:\s*(?:"([^"]*)"|\'([^\']*)\')', obj)}
        if f.get('firstName') and f.get('lastName'):
            out.append(dict(name=f"{f['firstName']} {f['lastName']}", titles=[f.get('title') or ''],
                            ph=urljoin(url, f['image']) if f.get('image') else None,
                            href=urljoin(url, f['link']) if f.get('link') else None))
    return out, None


JSON_SOURCES = {'wp_uw': json_wp_uw, 'tamu': json_tamu, 'drupal_ucb': json_drupal_ucb, 'usu': js_usu}


def parse_records(recs, matcher):
    """JSON 名单 → 和 parse() 一样的 {pid: {'tis', 'ph'}}。名字对不上的用个人主页地址再对一次。"""
    out = {}
    for r in recs:
        name = clean_name(r['name'])
        pid = matcher.match(name)
        if not pid and r.get('href'):
            pid = matcher.match_url(r['href'], name)
        if not pid:
            continue
        v = out.setdefault(pid, {'tis': set(), 'ph': None})
        ti = title_from([re.sub(r'\s+', ' ', _txt(t) if '<' in t else t).strip() for t in r['titles'] if t])
        if ti:
            v['tis'].add(ti)
        if r.get('ph') and not BAD_IMG.search(r['ph']):
            v['ph'] = v['ph'] or r['ph']
    return out


def fetch_pages(u, matcher, refresh, log):
    """一个名录入口 → [(网址, found)]。u 是网址（HTML 名录，自动翻页）或 (JSON 格式名, 网址)。"""
    if isinstance(u, tuple):
        kind, url = u
        pages = []
        while url and len(pages) < MAX_PAGES:
            text, final = get(url, refresh)
            if text is None:
                log.append(f'{url} -> {final}')
                break
            try:
                recs, url = JSON_SOURCES[kind](text, final)
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                log.append(f'{url} -> JSON 解析失败 {type(e).__name__}')
                break
            pages.append((final, parse_records(recs, matcher)))
        return pages
    html, final = get(u, refresh)
    if html is None:
        log.append(f'{u} -> {final}')
        return []
    pages = [(final, parse(html, final, matcher)[1])]
    for pu in page_urls(html_doc(html), final):
        h2, f2 = get(pu, refresh)
        if h2 is None:
            log.append(f'{pu} -> {f2}')
            continue
        pages.append((f2, parse(h2, f2, matcher)[1]))
    return pages


def run_site(site, profs_by_uni, refresh):
    tag, uni, urls = site
    matcher = Matcher(profs_by_uni.get(uni, []))
    res, log = {}, []
    for u in urls:
        pages = fetch_pages(u, matcher, refresh, log)
        if not pages:
            continue
        u = u[1] if isinstance(u, tuple) else u
        # 同一名录里一个人对上好几种不同职称 → 多半是重名的另一个人，不用这份名录的职称
        tis, ph = defaultdict(set), {}
        for src, found in pages:
            for pid, v in found.items():
                tis[pid] |= v['tis']
                ph[pid] = ph.get(pid) or v['ph']
        amb = [pid for pid, t in tis.items() if len(t) > 1]
        for pid in tis:
            ti = next(iter(tis[pid])) if len(tis[pid]) == 1 else None
            cur = res.setdefault(pid, {})
            if not cur.get('ti') and ti:
                cur['ti'], cur['src'] = ti, u
            if not cur.get('ph') and ph[pid]:
                cur['ph'] = ph[pid]
                cur.setdefault('src', u)
            cur.setdefault('src', u)
        log.append(f'{u}: {len(pages)} 页' + (f'，职称有冲突 {len(amb)} 人' if amb else ''))
    # 同一张图给了好几个人 → 多半是占位图，去掉
    cnt = Counter(v.get('ph') for v in res.values() if v.get('ph'))
    for v in res.values():
        if v.get('ph') and cnt[v['ph']] > 1:
            del v['ph']
    return tag, uni, res, log


RANK = [('Emeritus', 'emeritus'), ('Lecturer', 'lecturer'), ('Teaching', 'teaching'), ('of Teaching', 'teaching'),
        ('Practice', 'practice'), ('Research', 'research'), ('Adjunct', 'adjunct'), ('Visiting', 'visiting'),
        ('Assistant', 'assistant'), ('Associate', 'associate')]


def rank(t):
    """粗粒度职级，用来和 titles.json 对比（忽略 Distinguished 之类的荣誉修饰）。"""
    if not t:
        return None
    for w, r in RANK:
        if w in t:
            sub = [r2 for w2, r2 in RANK[RANK.index((w, r)) + 1:] if w2 in t and r2 in ('assistant', 'associate')]
            return r + ('-' + sub[0] if sub and r in ('teaching', 'research', 'practice') else '')
    return 'scientist' if 'Scientist' in t else 'full'


LADDER = ['assistant', 'associate', 'full']


def stale_demotion(home, directory):
    """名录职称比主页职称正好低一级（同一条线：Assistant → Associate → Professor，Teaching / Research 线同理）：
    多半是名录没跟上升职（CU Boulder、UW–Madison 都有），这种不写进 directory.json，网站用主页的。
    差两级（主页 "Professor"、名录 "Assistant Professor"）多半是主页提取错了，仍用名录的。"""
    def split(t):
        r = rank(t)
        if r in LADDER:
            return 'tt', LADDER.index(r)
        m = re.fullmatch(r'(teaching|research|practice)(?:-(assistant|associate))?', r or '')
        return (m.group(1), LADDER.index(m.group(2) or 'full')) if m else (None, None)
    if not home or not directory or 'Collegiate' in home:  # 主页的 "Collegiate Professor" 多半是冠名讲席（Notre Dame）
        return False
    (ta, la), (tb, lb) = split(home), split(directory)
    return ta is not None and ta == tb and lb == la - 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--only')
    args = ap.parse_args()
    profs = load_profs()
    by_uni = defaultdict(list)
    for p in profs:
        by_uni[p['uni']].append(p)
    sites = [s for s in SITES if not args.only or s[0] in args.only.split(',')]
    # 累加合并：已有条目一律保留不改，新结果只补缺的职称 / 照片（名录会下线、改版，旧结果不丢）
    old = json.load(open(RESULT, encoding='utf-8')) if os.path.exists(RESULT) else {}
    hp = json.load(open(TITLES, encoding='utf-8')) if os.path.exists(TITLES) else {}
    result, stats, changed, stale = {k: dict(v) for k, v in old.items()}, [], [], []
    with cf.ThreadPoolExecutor(12) as ex:
        for tag, uni, res, log in ex.map(lambda s: run_site(s, by_uni, args.refresh), sites):
            for line in log:
                print(f'  [{tag}] {line}')
            added = 0
            for pid, v in res.items():
                cur = result.get(pid, {})
                if v.get('ti') and cur.get('ti') and cur['ti'] != v['ti']:
                    changed.append((pid, cur['ti'], v['ti']))
                if v.get('ti') and not cur.get('ti'):
                    if stale_demotion(hp.get(pid), v['ti']):
                        stale.append((pid, hp[pid], v['ti']))
                    else:
                        cur['ti'], cur['src'] = v['ti'], v['src']
                        added += 1
                if v.get('ph') and not cur.get('ph'):
                    cur['ph'] = v['ph']
                    cur.setdefault('src', v['src'])
                if cur:
                    result[pid] = cur
            stats.append((tag, len(by_uni[uni]), len(res), sum(1 for v in res.values() if v.get('ti')),
                          sum(1 for v in res.values() if v.get('ph')), added))
    result = dict(sorted(result.items(), key=lambda kv: int(kv[0][1:])))
    with open(RESULT, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=0)

    print(f'\n{"学校":12s} {"名单":>5s} {"匹配":>5s} {"职称":>5s} {"照片":>5s} {"新增职称":>5s}')
    for tag, n, m, t, ph, ad in stats:
        print(f'{tag:12s} {n:5d} {m:5d} {t:5d} {ph:5d} {ad:5d}')
    tot = [sum(x[i] for x in stats) for i in range(1, 6)]
    print(f'{"合计":12s} {tot[0]:5d} {tot[1]:5d} {tot[2]:5d} {tot[3]:5d} {tot[4]:5d}')
    print(f'\n名录比主页正好低一级、多半是名录没跟上升职（不写入，用主页的）: {len(stale)} 人')
    for pid, a, b in stale:
        print(f'  {pid}  主页 {a}  名录 {b}')
    print(f'这次解析和 directory.json 已有职称不同（保留已有的，供人工核对）: {len(changed)} 人')
    for pid, a, b in changed:
        print(f'  {pid}  已有 {a}  这次 {b}')
    print('\n职称分布:')
    for t, c in Counter(v['ti'] for v in result.values() if v.get('ti')).most_common():
        print(f'  {c:5d}  {t}')

    if os.path.exists(TITLES):
        hp = json.load(open(TITLES, encoding='utf-8'))
        both = [pid for pid, v in result.items() if v.get('ti') and pid in hp]
        same = sum(1 for pid in both if hp[pid] == result[pid]['ti'])
        rsame = sum(1 for pid in both if rank(hp[pid]) == rank(result[pid]['ti']))
        print(f'\n和 titles.json（主页提取）对比：两边都有 {len(both)} 人，完全一致 {same}'
              f' ({same / max(len(both), 1):.1%})，职级一致 {rsame} ({rsame / max(len(both), 1):.1%})')
        diff = Counter((hp[pid], result[pid]['ti']) for pid in both if rank(hp[pid]) != rank(result[pid]['ti']))
        print('职级不一致最多的组合（主页 → 名录）:')
        for (a, b), c in diff.most_common(15):
            print(f'  {c:4d}  {a}  →  {b}')
        new = sum(1 for pid, v in result.items() if v.get('ti') and pid not in hp)
        print(f'名录新增（主页没提取到职称）: {new} 人')


if __name__ == '__main__':
    main()
