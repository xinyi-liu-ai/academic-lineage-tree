"""抓取教授主页和 CV（HTML / PDF）转成纯文本，缓存到 data/cache/pages/（不公开）。

- 遵守各站 robots.txt；同一主机两次请求至少间隔 2 秒
- 主页上若有 CV / Curriculum Vitae 链接，顺带抓一份
用法: python scripts/fetch_pages.py [--only-missing]
"""
import concurrent.futures as cf
import io
import os
import re
import sys
import threading
import time
import urllib.robotparser
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402

UA = 'lineage-tree-research-script/0.1'
CACHE = os.path.join(ROOT, 'data/cache/pages')
MAX_BYTES = 8 * 1024 * 1024
HOST_GAP = 2.0

_robots, _robots_lock, _robots_locks = {}, threading.Lock(), {}
_last, _host_locks = {}, {}


class TextAndLinks(HTMLParser):
    SKIP = {'script', 'style', 'noscript', 'svg'}
    BLOCK = {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'td', 'dd', 'dt'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.links, self.skip, self._a = [], [], 0, None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append('\n')
        if tag == 'a':
            self._a = [dict(attrs).get('href', ''), '']

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == 'a' and self._a:
            self.links.append(tuple(self._a))
            self._a = None
        if tag in self.BLOCK:
            self.out.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)
            if self._a is not None:
                self._a[1] += data


def allowed(url):
    p = urlparse(url)
    base = f'{p.scheme}://{p.netloc}'
    with _robots_lock:  # 全局锁只用来取每个站点自己的锁，下载 robots.txt 时不挡别的站点
        lock = _robots_locks.setdefault(base, threading.Lock())
    with lock:
        rp = _robots.get(base)
        if rp is None:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = requests.get(base + '/robots.txt', headers={'User-Agent': UA}, timeout=10)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except requests.RequestException:
                rp.parse([])
            _robots[base] = rp
    return rp.can_fetch(UA, url)


def polite_get(url):
    host = urlparse(url).netloc
    lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        wait = _last.get(host, 0) + HOST_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=20, stream=True, allow_redirects=True)
            try:
                body = r.raw.read(MAX_BYTES, decode_content=True)
            finally:
                r.close()  # 多线程下不关会占着连接
        finally:
            _last[host] = time.time()
    return r, body


def to_text(r, body):
    ctype = r.headers.get('content-type', '').lower()
    if 'pdf' in ctype or body[:5] == b'%PDF-':
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(body))
            return '\n'.join((pg.extract_text() or '') for pg in reader.pages[:80]), []  # CV 的学生名单常在最后几页
        except Exception:
            return '', []
    if 'html' not in ctype and 'text' not in ctype:
        return '', []
    p = TextAndLinks()
    try:
        p.feed(body.decode(r.encoding or 'utf-8', errors='replace'))
    except Exception:
        pass
    text = re.sub(r'[ \t\r\f\v]+', ' ', ''.join(p.out))
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text, p.links


REFRESH = re.compile(rb"""<meta[^>]+http-equiv=["']?refresh["']?[^>]*content=["']?\s*\d*\s*;?\s*url\s*=\s*['"]?([^"'>\s]+)""", re.I)
JS_REDIRECT = re.compile(rb"""(?:window\.|document\.)?location(?:\.href)?\s*=\s*["']([^"']+)["']|location\.replace\(\s*["']([^"']+)["']\s*\)""")


def fetch(url, _hops=0):
    """抓一页转成文字 + 链接。页面几乎没内容时跟随 <meta refresh> 和简单的 JS 跳转；http 超时 / 连不上时改试 https。"""
    if not url or not url.startswith('http') or not allowed(url):
        return '', [], 'robots' if url else ''
    try:
        r, body = polite_get(url)
    except requests.RequestException as e:
        if url.startswith('http://') and _hops == 0:
            return fetch('https://' + url[7:], 1)
        return '', [], type(e).__name__
    if r.status_code != 200:
        return '', [], str(r.status_code)
    text, links = to_text(r, body)
    if _hops < 2 and len(text.strip()) < 1500 and 'html' in r.headers.get('content-type', '').lower():
        m = REFRESH.search(body[:20000]) or JS_REDIRECT.search(body[:20000])
        if m:
            raw = m.group(1) or (m.group(2) if m.re.groups >= 2 else None) or b''
            target = urljoin(r.url, raw.decode('utf-8', 'ignore'))
            if target.startswith('http') and target.split('#')[0].rstrip('/') != r.url.split('#')[0].rstrip('/'):
                return fetch(target, _hops + 1)
    return text, [(urljoin(r.url, h), t) for h, t in links if h], ''


def find_cv_link(links):
    for href, text in links:
        t = text.strip().lower()
        if t in {'cv', 'c.v.', 'vita', 'curriculum vitae', 'resume', 'résumé'} or re.search(r'\b(cv|vita|curriculum)\b', t):
            return href
    for href, _ in links:
        if re.search(r'(^|[/_-])(cv|vita)[^/]*\.pdf$', href, re.I):
            return href
    return None


def work(p):
    out = {}
    hp = os.path.join(CACHE, p['id'] + '_home.txt')
    cvp = os.path.join(CACHE, p['id'] + '_cv.txt')
    if os.path.exists(hp) and os.path.exists(cvp):
        return p['id'], 'cached'
    text, links, err = fetch(p['homepage'])
    open(hp, 'w', encoding='utf-8', errors='replace').write(f'#URL {p["homepage"]}\n' + text)
    cv_url = p['cv'] if p['cv'].startswith('http') else find_cv_link(links)
    cv_text, _, cv_err = fetch(cv_url) if cv_url and cv_url != p['homepage'] else ('', [], '')
    open(cvp, 'w', encoding='utf-8', errors='replace').write(f'#URL {cv_url or ""}\n' + cv_text)
    out = f'home={len(text)}{"/" + err if err else ""} cv={len(cv_text)}{"/" + cv_err if cv_err else ""}'
    return p['id'], out


def main():
    os.makedirs(CACHE, exist_ok=True)
    import random
    profs = load_profs()
    random.Random(0).shuffle(profs)  # 打散同一主机的请求，避免线程都卡在同一站点
    t0 = time.time()
    done = 0
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        def safe(p):
            try:
                return work(p)
            except Exception as e:  # 单个页面出错不影响整体
                return p['id'], 'error ' + type(e).__name__
        for pid, msg in ex.map(safe, profs):
            done += 1
            if done % 200 == 0:
                print(f'{done}/{len(profs)} {time.time() - t0:.0f}s', flush=True)
    print('done', done)


if __name__ == '__main__':
    main()
