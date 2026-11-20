"""给 graph.json 里还没有照片的在职教授补照片（主页 / 院系个人页 / About 页上的本人照片）。

来源（按顺序，找到一张就停）：
  1. 已缓存的主页图片列表（fetch_photos.py 的 data/cache/photo_pages/）用放宽的规则再挑一次；
  2. 主页地址已更新（link_updates）或上次网络出错的，重新抓现在的主页；
  3. 主页上指向本人 About / Bio / 院系个人页（路径里带本人姓氏的 /people/、/faculty/、/directory/ 页）的链接；
  4. 院系网站 sitemap.xml 里路径带本人姓名的个人页（SITEMAPS 里配置的学校）。
选图：文件名 / alt 里有本人姓氏，或写着 headshot / portrait；本人个人页（网址或标题里有姓名）上唯一一张
内容图片；个人主页上排在最前面的那一张。alt 写着别人名字的、logo / 图标 / 合影类的不要；同一张图（地址或内容）
被几个人选中的全部不要。图片要能用 https 打开、短边 >= 80 px、宽高比 0.5–1.7。
全部候选做成照片墙人工核对，核对出的错图记在 REVIEW_REJECT 里。

遵守 robots.txt，同一主机两次请求至少间隔 2 秒（复用 fetch_pages.py 的 polite_get，按主机加锁）；不碰 dblp.org。
缓存: data/cache/photo_extra/（不提交）
输出: data/enrich/photos_extra.json  {pid: url}
用法: python scripts/fetch_photos_extra.py            抓取 + 选图 + 验图 + 写结果
      python scripts/fetch_photos_extra.py --sheet DIR 另外在 DIR 生成照片墙（HTML + PNG）
"""
import concurrent.futures as cf
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from itertools import zip_longest
from urllib.parse import urljoin, urlparse, unquote

import lxml.html

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from fetch_pages import allowed, polite_get  # noqa: E402
from names import display_name, fold  # noqa: E402

CACHE = os.path.join(ROOT, 'data/cache/photo_extra')
OUT = os.path.join(ROOT, 'data/enrich/photos_extra.json')
NEVER = re.compile(r'(^|\.)(dblp\.org|dblp\.uni-trier\.de|linkedin\.com|scholar\.google\.[a-z.]+|twitter\.com|x\.com|'
                   r'facebook\.com|github\.com|researchgate\.net|orcid\.org|youtube\.com|instagram\.com)$', re.I)

BAD = re.compile(r'logo|icon|banner|header|footer|seal|wordmark|sprite|button|arrow|badge|social|twitter|github|'
                 r'linkedin|scholar|orcid|email|mail|rss|spinner|loading|background|\bbg\b|\bmap\b|qr|flag|feed|'
                 r'cover|hero|slide|poster|figure|fig\d|teaser|youtube|award|sponsor|nsf|darpa|campus|building|'
                 r'group|team|students|lab.?photo|pixel|blank|spacer|transparent|placeholder|default[-_.]|'
                 r'silhouette|no.?photo|no.?image|missing|generic|not.?available|cartoon|anime|comic|plush|mascot|'
                 r'book|overview|facilities|drawn|drawing|sketch|crest|crown|statcounter|counter|tracking|'
                 r'favicon|avatar[-_]default|thumbnail-default|share|chevron|caret|close|menu|search|'
                 r'listing-thumbnail|landing-page|header-image|wallpaper|screenshot|demo|robot|award|news', re.I)
GOOD = re.compile(r'headshot|portrait|head.?shot|profile.?(photo|pic|image)|photo of|picture of|faculty.?photo|'
                  r'\bme\b|mugshot|bio.?photo', re.I)
OTHER_NAME = re.compile(r'\b[A-Z][a-z]+ (?:[A-Z]\.? )?[A-Z][a-z]+\b')
ORG_WORDS = re.compile(r'(?i)universit|college|school|department|institute|\blab\b|center|centre|engineering|science')
PROFILE_LINK = re.compile(r'(?i)^\s*(about(\s+me)?|bio(graphy)?|short bio|profile|faculty profile|(official|university|'
                          r'department(al)?|dept\.?|school|college|institutional)\s+(page|profile|homepage|web\s?page)|'
                          r'directory (page|profile|listing)|home page at .*|people page)\s*$')
PROFILE_PATH = re.compile(r'(?i)/(people|person|faculty|directory|profiles?|staff|team|bio|our-people|expert|experts)/')


def sha(s):
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def jload(f, default=None):
    try:
        return json.load(open(f, encoding='utf-8'))
    except (OSError, ValueError):
        return default


def https(u):
    return 'https://' + u[7:] if u.startswith('http://') else u


def name_parts(name):
    t = [x for x in fold(display_name(name)).split() if len(x) > 1 and x not in {'jr', 'sr', 'ii', 'iii', 'iv'}]
    if len(t) < 2:
        return t[0] if t else '', t[0] if t else '', set(t)
    return t[0], t[-1], set(t)


# ---------- 页面抓取（带缓存） ----------
def fetch_page(url):
    """抓一个 HTML 页面，返回 {url, err, imgs, og, title, h1, links}；结果缓存。"""
    f = os.path.join(CACHE, 'pages', sha(url) + '.json')
    rec = jload(f)
    if rec is not None:
        return rec
    rec = dict(url=url, err='', imgs=[], og=None, title='', h1='', links=[])
    host = urlparse(url).netloc.lower()
    if NEVER.search(host.split(':')[0]):
        rec['err'] = 'skip'
    elif not allowed(url):
        rec['err'] = 'robots'
    else:
        try:
            r, body = polite_get(url)
            if r.status_code != 200:
                rec['err'] = str(r.status_code)
            elif 'html' not in r.headers.get('content-type', '').lower():
                rec['err'] = 'not html'
            else:
                rec.update(parse_html(body.decode(r.encoding or 'utf-8', errors='replace'), r.url))
        except Exception as e:  # noqa: BLE001
            rec['err'] = type(e).__name__
    os.makedirs(os.path.dirname(f), exist_ok=True)
    json.dump(rec, open(f, 'w', encoding='utf-8'), ensure_ascii=False)
    return rec


def parse_html(html, url):
    try:
        doc = lxml.html.fromstring(re.sub(r'^\s*<\?xml[^>]*\?>', '', html))
    except Exception:  # noqa: BLE001
        return dict(url=url)
    for bad in doc.xpath('//script|//style|//noscript'):
        bad.drop_tree()
    imgs = []
    for el in doc.iter('img', 'source'):
        src = el.get('src') or el.get('data-src') or el.get('data-lazy-src') or ''
        if not src and el.get('srcset'):
            src = el.get('srcset').split(',')[0].strip().split(' ')[0]
        if not src or el.tag == 'source':
            continue
        # 祖先里是否有 header / nav / footer（站点外框里的图片不是本人照片）
        chrome = any(a.tag in ('header', 'nav', 'footer') or re.search(r'(?i)header|footer|navbar|masthead|sidebar-logo',
                     (a.get('class') or '') + ' ' + (a.get('id') or '')) for a in el.iterancestors())
        imgs.append(dict(src=urljoin(url, src.strip()), alt=(el.get('alt') or el.get('title') or '').strip()[:200],
                         cls=((el.get('class') or '') + ' ' + (el.get('id') or '')).strip()[:100],
                         w=el.get('width') or '', h=el.get('height') or '', chrome=chrome))
    og = None
    for m in doc.iter('meta'):
        if (m.get('property') or m.get('name') or '').lower() in ('og:image', 'twitter:image') and m.get('content'):
            og = urljoin(url, m.get('content').strip())
            break
    title = ' '.join((doc.findtext('.//title') or '').split())[:200]
    h1 = ' '.join(' '.join(x.text_content().split()) for x in doc.iter('h1'))[:200]
    links = []
    for a in doc.iter('a'):
        h = a.get('href')
        if h and not h.startswith(('mailto:', 'javascript:', '#')):
            links.append([urljoin(url, h.strip()), ' '.join(a.text_content().split())[:80]])
    body = doc.find('body')
    head = ' '.join((body.text_content() if body is not None else '').split())[:500]
    return dict(url=url, imgs=imgs[:80], og=og, title=' '.join(title.split()), h1=h1, links=links[:400], text_head=head)


# ---------- 选图 ----------
def num(x):
    m = re.match(r'\s*(\d+)', x or '')
    return int(m.group(1)) if m else None


def hit_name(blob, first, last):
    words = set(re.split(r'[^a-z]+', blob))
    def hit(x):
        return bool(x) and (x in words or (len(x) >= 4 and x in blob)
                            or (len(x) == 3 and any(w.endswith(x) and len(w) > 5 for w in words)))
    return hit(last), hit(first)


def page_is_person(page, first, last):
    """页面本身是不是这个人的页面：网址路径、<title> 或 <h1> 里有姓（+名或名首字母）。"""
    path = fold(unquote(urlparse(page['url']).path).replace('/', ' ').replace('_', ' ').replace('.', ' '))
    words = set(path.split())
    if last in words or (len(last) >= 4 and last in path.replace(' ', '')):
        return True
    for t in (page.get('title', ''), page.get('h1', '')):
        w = set(fold(t).split())
        if last in w and (first in w or len(w) <= 8):
            return True
    return False


GENERIC = set('headshot head shot photo photos portrait profile pic picture image img me my crop cropped square small '
              'large medium web new final thumb thumbnail jpg jpeg png webp scaled bio faculty official edited copy '
              'color colour bw hi res hires lowres cut sq dr prof professor of the and resized compressed min original '
              'circle round face front dsc img_ pxl'.split())


def foreign_tokens(fname, allp):
    """文件名里有不是本人姓名、也不是常见词的字母串（"PMandal-Headshot.jpeg"）：多半是别人的照片。"""
    toks = [t for t in re.split(r'[^a-z]+', fold(re.sub(r'([a-z])([A-Z])', r' ', fname.rsplit('.', 1)[0])))
            if len(t) >= 3]
    return [t for t in toks if t not in GENERIC and t not in allp and not any(t in a or a in t for a in allp if len(a) >= 3)]


_SURNAMES = None


def other_surname(fname, allp):
    """文件名里出现名单上别的教授的姓（"Murield-Medard-preferred.jpg" 出现在 Shulaker 的页面上）。"""
    global _SURNAMES
    if _SURNAMES is None:
        _SURNAMES = {name_parts(p['name'])[1] for p in load_profs()} - GENERIC
        _SURNAMES = {x for x in _SURNAMES if len(x) >= 4}
    toks = re.split(r'[^a-z]+', fold(re.sub(r'([a-z])([A-Z])', r' ', fname.rsplit('.', 1)[0])))
    return [t for t in toks if t in _SURNAMES and t not in allp]


def pick_from_page(page, name, kind, bad_urls=()):
    """返回 [(tier, url, why)]，tier 越小越可信。kind: home（个人主页）/ profile（院系个人页、About 页）。"""
    first, last, allp = name_parts(name)
    if not last:
        return []
    # 本人的页面：网址 / 标题里有姓名，或页面文字开头（前 400 字）就是本人姓名（个人主页）
    head = fold(page.get('text_head', '') + ' ' + page.get('title', '') + ' ' + page.get('h1', '')).split()
    own = page_is_person(page, first, last) or (last in head and (first in head or kind == 'home'))
    content, out = [], []
    for i, img in enumerate(page.get('imgs', [])):
        src = img['src']
        if not src.startswith('http') or src.lower().split('?')[0].endswith(('.svg', '.gif', '.ico')) or src in bad_urls:
            continue
        fname = unquote(src.rsplit('/', 1)[-1].split('?')[0])
        path = unquote(urlparse(src).path)
        blob = fold(f'{fname} {img["alt"]} {img["cls"]}')
        hl, hf = hit_name(blob, first, last)
        w, h = num(img['w']), num(img['h'])
        if (w and w < 70) or (h and h < 70) or (w and h and not 0.45 < w / h < 1.8):
            continue
        if img.get('chrome') and not hl:
            continue
        bad_txt = BAD.search(f'{fname} {img["cls"]} {img["alt"]}')
        if bad_txt or re.search(r'(?i)/(logos?|icons?|themes?/[^/]+/images|assets/img/social)/', path):
            continue
        # alt 写着别人的名字
        alt_names = [m for m in OTHER_NAME.findall(img['alt']) if not ORG_WORDS.search(m)]
        if alt_names and not hl and not any(set(fold(m).split()) & allp for m in alt_names):
            continue
        if other_surname(fname, allp):
            continue
        content.append(i)
        if hl and (hf or len(last) >= 4 or GOOD.search(img['alt'] + ' ' + fname)):
            out.append((0, src, f'name in file/alt [{fname[:40]} | {img["alt"][:40]}]'))
        elif GOOD.search(img['alt'] + ' ' + fname + ' ' + img['cls']) and own and not alt_names                 and not foreign_tokens(fname, allp):
            out.append((1, src, f'portrait keyword on own page [{fname[:40]} | {img["alt"][:40]}]'))
        elif hl and own and not foreign_tokens(fname, allp):
            out.append((1, src, f'surname in file/alt on own page [{fname[:40]} | {img["alt"][:40]}]'))
    if not out and own and content:
        first_i = content[0]
        img = page['imgs'][first_i]
        fname = unquote(img['src'].rsplit('/', 1)[-1].split('?')[0])
        if len(content) == 1:
            out.append((2, img['src'], f'only content image on {kind} page [{fname[:40]} | {img["alt"][:40]}]'))
        elif kind == 'home' and first_i < 3 and len(content) <= 4:
            out.append((3, img['src'], f'first of {len(content)} images on homepage [{fname[:40]} | {img["alt"][:40]}]'))
    og = page.get('og')
    if og and og.startswith('http') and og not in bad_urls:
        fname = unquote(og.rsplit('/', 1)[-1].split('?')[0])
        hl, hf = hit_name(fold(fname), first, last)
        if hl and not BAD.search(fname):
            out.append((1, og, f'og:image with surname [{fname[:40]}]'))
    seen, res = set(), []
    for t in sorted(out, key=lambda x: x[0]):
        if t[1] not in seen:
            seen.add(t[1])
            res.append(t)
    return res[:3]


# ---------- 验图 ----------
def check_image(url):
    """用 https 取图：200、image/*、无 CORP 限制、短边 >= 80、宽高比 0.5–1.7。缓存结果和缩略图。"""
    u = https(url)
    f = os.path.join(CACHE, 'img', sha(u) + '.json')
    rec = jload(f)
    if rec is not None:
        return rec
    rec = dict(url=u, ok=False, err='')
    host = urlparse(u).netloc.lower()
    if NEVER.search(host.split(':')[0]):
        rec['err'] = 'skip'
    elif not allowed(u):
        rec['err'] = 'robots'
    else:
        try:
            r, body = polite_get(u)
            ct = r.headers.get('content-type', '').split(';')[0].strip().lower()
            cp = r.headers.get('cross-origin-resource-policy', '').strip().lower()
            if r.status_code != 200:
                rec['err'] = f'status {r.status_code}'
            elif not ct.startswith('image/'):
                rec['err'] = f'not image {ct}'
            elif cp in ('same-origin', 'same-site'):
                rec['err'] = f'CORP {cp}'
            else:
                from PIL import Image
                im = Image.open(io.BytesIO(body))
                w, h = im.size
                rec.update(w=w, h=h, final=r.url, md5=hashlib.md5(body).hexdigest())
                if min(w, h) < 80:
                    rec['err'] = f'too small {w}x{h}'
                elif not 0.5 <= w / h <= 1.7:
                    rec['err'] = f'aspect {w}x{h}'
                else:
                    rec['ok'] = True
                    im = im.convert('RGB')
                    im.thumbnail((240, 300))
                    im.save(os.path.join(CACHE, 'img', sha(u) + '.jpg'), quality=85)
        except Exception as e:  # noqa: BLE001
            rec['err'] = 'tls error' if 'SSL' in type(e).__name__ else type(e).__name__
    os.makedirs(os.path.dirname(f), exist_ok=True)
    json.dump(rec, open(f, 'w', encoding='utf-8'))
    return rec


# ---------- 院系 sitemap ----------
# 学校 → 院系网站 sitemap（2026-11 从各站 robots.txt / 常见位置确认能公开读取的）。
# 没列的：对脚本返回 403（UNC、UVA、UIC、Ohio State、U Michigan CSE、JHU 工学院、CUNY、Rice profiles）、
# TLS 证书链不全（MSU cse、Rutgers cs、UMass Lowell cs、Kentucky 工学院）、个人页网址不含姓名（UC Riverside profiles）
SITEMAPS = {
    'Vanderbilt University': ['https://engineering.vanderbilt.edu/sitemap.xml'],
    'Yale University': ['https://engineering.yale.edu/sitemap.xml'],
    'University of Colorado Boulder': [f'https://www.colorado.edu/{d}/sitemap.xml'
                                       for d in ('cs', 'ecee', 'cmci', 'atlas', 'engineering', 'infoscience', 'mechanical')],
    'George Mason University': ['https://cs.gmu.edu/sitemap.xml'],
    'Johns Hopkins University': ['https://www.cs.jhu.edu/sitemap.xml'],
    'Oregon State University': ['https://engineering.oregonstate.edu/sitemap.xml'],
    'University of Texas at Dallas': ['https://cs.utdallas.edu/wp-sitemap.xml'],
    'Univ. of California - Los Angeles': ['https://samueli.ucla.edu/wp-sitemap.xml'],
    'Univ. of California - San Diego': ['https://cse.ucsd.edu/sitemap.xml'],
    'University of Massachusetts Lowell': ['https://www.uml.edu/sitemap.xml'],
    'Drexel University': ['https://drexel.edu/sitemap.xml'],
    'Univ. of California - Irvine': ['https://ics.uci.edu/sitemap.xml'],
    'Case Western Reserve University': ['https://engineering.case.edu/sitemap.xml'],
    'Binghamton University': ['https://www.binghamton.edu/sitemap-root.xml'],
    'Rochester Institute of Technology': ['https://www.rit.edu/sitemap.xml'],
    'University of Central Florida': ['https://www.cs.ucf.edu/sitemap_index.xml'],
    'Michigan State University': ['https://engineering.msu.edu/sitemap.xml'],
    'Univ. of California - Davis': ['https://cs.ucdavis.edu/sitemap.xml', 'https://engineering.ucdavis.edu/sitemap.xml'],
    'University of Michigan': ['https://www.si.umich.edu/sitemap.xml'],
    'Univ. of Maryland - Baltimore County': ['https://www.csee.umbc.edu/wp-sitemap.xml'],
    'Northeastern University': ['https://www.khoury.northeastern.edu/sitemap.xml'],
    'Syracuse University': ['https://ecs.syr.edu/sitemap.xml'],
    'University of Maryland - College Park': ['https://www.cs.umd.edu/sitemap.xml'],
    'University of Delaware': ['https://www.cis.udel.edu/sitemap_index.xml'],
    'Iowa State University': ['https://www.engineering.iastate.edu/sitemap.xml'],
    'Augusta University': ['https://www.augusta.edu/sitemap.xml'],
    'Tulane University': ['https://sse.tulane.edu/sitemap.xml'],
    'University of Iowa': ['https://cs.uiowa.edu/sitemap.xml'],
    'UCCS': ['https://www.uccs.edu/sitemap.xml'],
    'Georgia State University': ['https://csds.gsu.edu/sitemap_index.xml'],
}


def sitemap_urls(sm, depth=0):
    f = os.path.join(CACHE, 'sitemaps', sha(sm) + '.json')
    urls = jload(f)
    if urls is not None:
        return urls
    urls = []
    if allowed(sm):
        try:
            r, body = polite_get(sm)
            if r.status_code == 200:
                locs = re.findall(rb'<loc>\s*([^<\s]+)\s*</loc>', body)
                locs = [x.decode('utf-8', 'replace').replace('&amp;', '&') for x in locs]
                # Pantheon 托管的站点 sitemap 里写的是 *.pantheonsite.io 内部域名，换回正式域名
                real = urlparse(sm).netloc
                locs = [re.sub(r'^https?://[^/]+\.pantheonsite\.io', 'https://' + real, x) for x in locs]
                if b'<sitemapindex' in body[:2000] and depth < 2:
                    # 新闻 / 活动 / 分类之类的子 sitemap 不用看
                    skip = re.compile(r'posts-post-|/post-sitemap|news|event|tribe|attachment|categor|tag|taxonom|'
                                      r'users|story|stories|blog|product')
                    for sub in [x for x in locs if not skip.search(x)][:60]:
                        urls += sitemap_urls(sub, depth + 1)
                else:
                    urls = locs
        except Exception:  # noqa: BLE001
            pass
    os.makedirs(os.path.dirname(f), exist_ok=True)
    json.dump(urls, open(f, 'w', encoding='utf-8'))
    return urls


def sitemap_profile(name, urls):
    """sitemap 里路径最后一段是本人姓名（名-姓 / 姓-名 / 名-中-姓）的个人页。"""
    first, last, allp = name_parts(name)
    out = []
    for u in urls:
        p = urlparse(u).path.rstrip('/')
        seg = fold(unquote(p.rsplit('/', 1)[-1]).replace('_', ' ').replace('.', ' ').replace('html', '').replace('aspx', ''))
        toks = seg.split()
        if len(toks) >= 2 and last in toks and first in toks and set(toks) <= allp | {'dr', 'phd', 'prof'}:
            if PROFILE_PATH.search(p + '/') or len(toks) >= 2:
                out.append(u)
    return out[:2]


# ---------- 主流程 ----------
def missing_profs():
    g = json.load(open(os.path.join(ROOT, 'docs/data/graph.json'), encoding='utf-8'))
    miss = {n['id']: n for n in g['nodes'] if 'x' not in n and not n.get('ph')}
    profs = {p['id']: p for p in load_profs()}
    return [dict(profs[pid], h=n.get('h', ''), u=g['unis'][n['u']][0]) for pid, n in miss.items() if pid in profs]


def norm_url(u):
    u = re.sub(r'^https?://(www\.)?', '', (u or '').lower()).split('#')[0]
    return re.sub(r'/(index|default|home)\.(s?html?|php|aspx?)$', '', u).rstrip('/')


def gather(p, bad_urls):
    """一个人：依次看各来源的页面，返回候选 [(tier, url, why, page_url, source)]。"""
    name = p['name']
    first, last, _ = name_parts(name)
    cands, pages = [], []
    old = jload(os.path.join(ROOT, 'data/cache/photo_pages', p['id'] + '.json'), {})
    if old.get('imgs') and not old.get('err'):
        pg = dict(url=old['url'], imgs=[dict(i, src=urljoin(old['url'], i['src'])) for i in old['imgs']], og=old.get('og'))
        txt = os.path.join(ROOT, 'data/cache/pages', p['id'] + '_home.txt')
        if os.path.exists(txt):
            pg['text_head'] = open(txt, encoding='utf-8', errors='replace').read(500)
        pages.append(('home-cache', 'home', pg))
    h = p['h'] if p['h'].startswith('http') else ''
    refetch = h and (not old or old.get('err') not in (None, '', 'robots') or norm_url(h) != norm_url(old.get('url')))
    home = None
    if h and (refetch or not old.get('imgs')):
        home = fetch_page(h)
        if not home.get('err'):
            pages.append(('home-new', 'home', home))
    # 主页上的链接：先用 people_pages 缓存，没有就用刚抓的主页
    links = []
    pp = jload(os.path.join(ROOT, 'data/cache/people_pages', p['id'] + '.json'), {})
    for pg in (pp.get('pages') or [])[:1]:
        links += pg.get('links') or []
    if home and not home.get('err'):
        links += home.get('links') or []
    elif h and not links and old.get('imgs'):
        hp = fetch_page(h)
        links += hp.get('links') or []
    prof_links = []
    hhost = urlparse(h).netloc.lower()
    for href, text in links:
        if not href.startswith('http') or href.lower().endswith(('.pdf', '.jpg', '.png', '.ppt', '.pptx', '.zip')):
            continue
        hp_ = urlparse(href)
        if NEVER.search(hp_.netloc.lower().split(':')[0]) or norm_url(href) == norm_url(h):
            continue
        path_f = fold(unquote(hp_.path).replace('/', ' ').replace('_', ' ').replace('.', ' '))
        if PROFILE_LINK.match(text or '') and (hp_.netloc.lower() == hhost or '.edu' in hp_.netloc):
            prof_links.append(href)
        elif '.edu' in hp_.netloc.lower() and PROFILE_PATH.search(hp_.path + '/') and last in path_f.split() \
                and (first in path_f.split() or first[:1] in path_f.split() or len(last) >= 5):
            prof_links.append(href)
    seen = set()
    for u in prof_links:
        if norm_url(u) in seen or len(seen) >= 3:
            continue
        seen.add(norm_url(u))
        pg = fetch_page(u)
        if not pg.get('err'):
            pages.append(('profile-link', 'profile', pg))
    for u in sitemap_profile(name, SITEMAP_INDEX.get(p['uni'], [])):
        if norm_url(u) in seen:
            continue
        seen.add(norm_url(u))
        pg = fetch_page(u)
        if not pg.get('err'):
            pages.append(('sitemap', 'profile', pg))
    for src, kind, pg in pages:
        for tier, u, why in pick_from_page(pg, name, kind, bad_urls):
            cands.append((tier, u, why, pg['url'], src))
    # 院系个人页的照片优先于主页上靠位置猜的
    cands.sort(key=lambda c: (c[0], 0 if c[4] in ('profile-link', 'sitemap') else 1))
    return cands


SITEMAP_INDEX = {}

# 照片墙人工核对出的错图（pid → 原因）
# 2026-11 对 429 张候选逐张看照片墙的结果
REVIEW_REJECT = {
    'p3226': 'placeholder silhouette',
    'p4641': 'person tiny in street scene',
    'p5255': 'person small at desk',
    'p4786': 'RIT tiger logo',
    'p6259': 'award photo, two people',
    'p4475': 'diagram (Rubik cube)',
    'p2513': 'diagram',
    'p6643': 'event photo, two people',
    'p6647': 'lab scene, several people',
    'p5092': 'ASU logo',
    'p5093': 'collage with logos',
    'p4636': 'Augusta University logo',
    'p4643': 'Augusta University logo',
    'p4227': 'lecturing at blackboard, person small',
    'p49': 'photo with infant, two subjects',
    'p3892': 'grid of small face images',
    'p4096': 'blank image',
    'p4520': 'person small in front of wall',
    'p513': 'cartoon avatar',
    'p5518': 'posterized drawing',
    'p4843': 'blank image',
    'p3436': 'circuit board image',
    'p953': 'person tiny in mountain landscape',
    'p5965': 'person small inside sculpture',
    'p6114': 'person tiny in mountain landscape',
    'p6128': 'cartoon',
    'p4878': 'drawing of clock tower',
    'p6204': 'tiger statue dominates frame',
    'p4464': 'family photo, several people',
    'p840': 'caricature drawing',
    'p841': 'graphic, no clear portrait',
    'p3744': 'person tiny at desk',
    'p6651': 'black image',
    'p6705': 'Yale logo',
    'p4251': 'back of head at computer',
    'p4516': 'NSA seal',
    'p2947': 'face not clearly visible',
    'p6013': 'lab scene, two people',
    'p2943': 'group photo',
    'p4881': 'book cover',
}


def main():
    sheet_dir = sys.argv[sys.argv.index('--sheet') + 1] if '--sheet' in sys.argv else None
    os.makedirs(CACHE, exist_ok=True)
    profs = missing_profs()
    if '--limit' in sys.argv:
        profs = profs[:int(sys.argv[sys.argv.index('--limit') + 1])]
    print('current professors without photo', len(profs), flush=True)
    for uni, sms in SITEMAPS.items():
        for sm in sms:
            SITEMAP_INDEX.setdefault(uni, [])
            SITEMAP_INDEX[uni] += [u for u in sitemap_urls(sm) if not u.lower().endswith(('.xml', '.pdf', '.jpg', '.png'))]
        print('sitemap', uni, len(SITEMAP_INDEX.get(uni, [])), flush=True)
    bad = set(jload(os.path.join(ROOT, 'data/enrich/photo_https_bad.json'), {}))
    photos = jload(os.path.join(ROOT, 'data/enrich/photos.json'), {})
    block = jload(os.path.join(ROOT, 'data/enrich/photo_blocklist.json'), {})
    bad |= {photos[k] for k in block if k in photos}  # 人工核对时判错的那张不再选

    by_host = defaultdict(list)
    for p in profs:
        by_host[urlparse(p['h']).netloc].append(p)
    order = [p for grp in zip_longest(*sorted(by_host.values(), key=len, reverse=True)) for p in grp if p]
    lock = threading.Lock()
    results, n = {}, [0]

    def work(p):
        try:
            cands = gather(p, bad)
        except Exception as e:  # noqa: BLE001
            print('error', p['id'], type(e).__name__, e, flush=True)
            cands = []
        chosen = None
        for c in cands[:3]:
            chk = check_image(c[1])
            if chk.get('ok'):
                chosen = dict(url=https(c[1]), tier=c[0], why=c[2], page=c[3], src=c[4], w=chk['w'], h=chk['h'],
                              md5=chk['md5'], thumb=sha(https(c[1])) + '.jpg')
                break
        with lock:
            results[p['id']] = dict(name=p['name'], uni=p['uni'], cands=[list(c) for c in cands], pick=chosen)
            n[0] += 1
            if n[0] % 100 == 0:
                print(n[0], 'done', sum(1 for r in results.values() if r['pick']), 'found', flush=True)

    with cf.ThreadPoolExecutor(48) as ex:
        list(ex.map(work, order))
    json.dump(results, open(os.path.join(CACHE, 'results.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)

    picks = {pid: r['pick'] for pid, r in results.items() if r['pick']}
    # 同一张图（地址或内容）被姓不同的几个人选中：院系页上的占位图或别人，都不要
    by_u, by_md5 = defaultdict(set), defaultdict(set)
    for pid, k in picks.items():
        ln = name_parts(results[pid]['name'])[1]
        by_u[k['url']].add(ln)
        by_md5[k['md5']].add(ln)
    existing = set(jload(os.path.join(ROOT, 'data/enrich/photos.json'), {}).values())
    existing |= {r.get('ph') for r in jload(os.path.join(ROOT, 'data/enrich/directory.json'), {}).values()}
    existing = {https(u) for u in existing if u}
    drop = Counter()
    for pid in list(picks):
        k = picks[pid]
        if len(by_u[k['url']]) > 1 or len(by_md5[k['md5']]) > 1:
            drop['shared'] += 1
            del picks[pid]
        elif k['url'] in existing:
            drop['already someone else\'s photo'] += 1
            del picks[pid]
        elif pid in REVIEW_REJECT:
            drop['review reject'] += 1
            del picks[pid]
    print('dropped', dict(drop))
    print('by tier', Counter(k['tier'] for k in picks.values()), 'by source', Counter(k['src'] for k in picks.values()))
    json.dump({pid: picks[pid]['url'] for pid in sorted(picks, key=lambda x: int(x[1:]))},
              open(OUT, 'w', encoding='utf-8'), indent=0)
    print('photos_extra', len(picks))
    if sheet_dir:
        make_sheet(picks, results, sheet_dir)


def make_sheet(picks, results, d):
    from PIL import Image, ImageDraw, ImageFont
    os.makedirs(d, exist_ok=True)
    items = sorted(picks.items(), key=lambda kv: (kv[1]['tier'], results[kv[0]]['uni'], kv[0]))
    try:
        font = ImageFont.truetype('arial.ttf', 12)
    except OSError:
        font = ImageFont.load_default()
    W, H, COLS, PER = 170, 230, 8, 48
    for s in range(0, len(items), PER):
        chunk = items[s:s + PER]
        rows = (len(chunk) + COLS - 1) // COLS
        sheet = Image.new('RGB', (COLS * W, rows * H), 'white')
        dr = ImageDraw.Draw(sheet)
        for j, (pid, k) in enumerate(chunk):
            x, y = (j % COLS) * W, (j // COLS) * H
            try:
                im = Image.open(os.path.join(CACHE, 'img', k['thumb']))
                im.thumbnail((W - 10, H - 50))
                sheet.paste(im, (x + 5, y + 5))
            except OSError:
                pass
            nm = results[pid]['name']
            dr.text((x + 5, y + H - 44), f'{s + j} {pid} t{k["tier"]}', fill='red', font=font)
            dr.text((x + 5, y + H - 30), nm[:24], fill='black', font=font)
            dr.text((x + 5, y + H - 16), results[pid]['uni'][:26], fill='gray', font=font)
        sheet.save(os.path.join(d, f'sheet_{s // PER:02d}.png'))
    rows = ''.join(
        f'<figure><img src="{k["url"]}" loading="lazy"><figcaption><b>{i} {pid}</b> {results[pid]["name"]}<br>'
        f'{results[pid]["uni"]}<br><small>t{k["tier"]} {k["src"]}: {k["why"]}<br><a href="{k["page"]}">page</a> '
        f'{k["w"]}x{k["h"]}</small></figcaption></figure>' for i, (pid, k) in enumerate(items))
    html = ('<!doctype html><meta charset="utf-8"><title>photos_extra contact sheet</title><style>body{font:12px sans-serif}'
            'figure{display:inline-block;width:180px;vertical-align:top;margin:4px}img{width:170px;height:200px;'
            f'object-fit:cover;background:#eee}}</style><h1>{len(items)} new photos</h1>{rows}')
    open(os.path.join(d, 'contact_sheet.html'), 'w', encoding='utf-8').write(html)
    print('sheet', d)


if __name__ == '__main__':
    main()
