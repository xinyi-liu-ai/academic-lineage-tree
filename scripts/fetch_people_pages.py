"""抓教授主页及其 Students / People / Group / Alumni 子页面，保存文字和链接（含链接文字），供
extract_students.py（提取学生名单）和 match_people_links.py（给人配个人主页）离线使用。

遵守 robots.txt，同一主机两次请求至少间隔 2 秒（复用 fetch_pages.py）；可中断，重跑跳过已抓的。
输出: data/cache/people_pages/{pid}.json（不提交）
用法: python scripts/fetch_people_pages.py
"""
import concurrent.futures as cf
import json
import os
import random
import re
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(__file__))
from build_graph import load_profs, ROOT  # noqa: E402
from fetch_pages import fetch  # noqa: E402

OUT = os.path.join(ROOT, 'data/cache/people_pages')
SUBPAGE = re.compile(r'\b(students?|people|group|lab|team|members?|alumni|advisees|mentees|former students)\b', re.I)
NOT_SUB = re.compile(r'publication|paper|course|teaching|talk|news|calendar|event|seminar', re.I)
BAD = re.compile(r'\.(pdf|ps|gz|zip|pptx?|docx?|jpe?g|png)(\?|$)|mailto:', re.I)
MAX_SUB = 4


def crawl(p):
    pages, seen = [], set()
    text, links, err = fetch(p['homepage'])
    seen.add(p['homepage'].rstrip('/'))
    pages.append(dict(url=p['homepage'], text=text, links=links, err=err))
    host = urlparse(p['homepage']).netloc
    subs = []
    for h, t in links:
        h = h.split('#')[0]
        t = (t or '').strip()
        if (h.rstrip('/') not in seen and urlparse(h).netloc == host and len(t) < 40 and SUBPAGE.search(t)
                and not NOT_SUB.search(t) and not BAD.search(h) and h not in subs):
            subs.append(h)
    for h in subs[:MAX_SUB]:
        seen.add(h.rstrip('/'))
        t2, l2, e2 = fetch(h)
        pages.append(dict(url=h, text=t2, links=l2, err=e2))
    return pages


def main():
    os.makedirs(OUT, exist_ok=True)
    profs = [p for p in load_profs() if p['homepage'].startswith('http')]
    random.Random(2).shuffle(profs)
    todo = [p for p in profs if not os.path.exists(os.path.join(OUT, p['id'] + '.json'))]
    print('professors', len(profs), 'remaining', len(todo), flush=True)
    t0, done = time.time(), 0

    def work(p):
        try:
            pages = crawl(p)
        except Exception as e:  # 单个页面出错不影响整体
            pages = [dict(url=p['homepage'], text='', links=[], err=type(e).__name__)]
        with open(os.path.join(OUT, p['id'] + '.json'), 'w', encoding='utf-8', errors='replace') as f:
            json.dump(dict(pid=p['id'], pages=pages), f, ensure_ascii=False)
        return len(pages)

    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        for _ in ex.map(work, todo):
            done += 1
            if done % 300 == 0:
                print(f'{done}/{len(todo)} {time.time() - t0:.0f}s', flush=True)
    print('done', done, f'{time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
