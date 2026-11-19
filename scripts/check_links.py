"""检查站点上个人主页链接是否已失效。
教授：复用 data/cache/people_pages 的抓取结果；外部人物：礼貌 GET（遵守 robots、同站 2 秒间隔）。
404/410、DNS 失败、跳转到域名停放页或根主页视为失效；超时/5xx/403 视为未知。
缓存：data/cache/link_check/results.jsonl（可续跑）；输出：data/enrich/dead_links.json {node_id: reason}。"""
import collections
import concurrent.futures as cf
import json
import os
import sys
import threading
from urllib.parse import urlparse

sys.path.insert(0, 'scripts')
import fetch_pages  # noqa: E402
from fetch_pages import allowed, polite_get  # noqa: E402
import requests  # noqa: E402

fetch_pages.MAX_BYTES = 4096  # 只读开头几个字节
CACHE_DIR = 'data/cache/link_check'
CACHE = os.path.join(CACHE_DIR, 'results.jsonl')
OUT = 'data/enrich/dead_links.json'
PARK = ('sedoparking', 'sedo.com', 'godaddy.com', 'hugedomains', 'dan.com', 'afternic', 'parkingcrew',
        'bodis', 'namecheap.com', 'domainmarket', 'undeveloped', 'above.com', 'domainsponsor', 'squadhelp')
lock = threading.Lock()


def classify(url):
    try:
        if not allowed(url):
            return 'unknown', 'robots'
        r, _ = polite_get(url)
    except requests.exceptions.ConnectionError as e:
        s = str(e)
        if any(k in s for k in ('getaddrinfo', 'Name or service', 'NameResolution', 'nodename')):
            return 'dead', 'dns'
        return 'unknown', 'connerr'
    except requests.RequestException as e:
        return 'unknown', type(e).__name__
    except Exception as e:
        return 'unknown', type(e).__name__
    c = r.status_code
    if c in (404, 410):
        return 'dead', str(c)
    if c >= 400:
        return 'unknown', str(c)
    o, f = urlparse(url), urlparse(r.url)
    if any(k in f.netloc.lower() for k in PARK):
        return 'dead', 'parked'
    if r.history:
        oh, fh = o.netloc.lower().removeprefix('www.'), f.netloc.lower().removeprefix('www.')
        orig_deep = len(o.path.strip('/')) > 0
        if f.path.strip('/') == '' and not f.query and orig_deep:
            return 'dead', 'redirect-root'
    return 'ok', str(c)


def main():
    g = json.load(open('docs/data/graph.json', encoding='utf8'))
    dead = {}
    stat = collections.Counter()
    for n in g['nodes']:  # 教授
        if n.get('h') and not n.get('x'):
            try:
                err = json.load(open(f'data/cache/people_pages/{n["id"]}.json', encoding='utf8'))['pages'][0]['err']
            except Exception:
                continue
            stat['prof_' + (err or 'ok')] += 1
            if err in ('404', '410', 'ConnectionError'):
                dead[n['id']] = 'prof-' + err
    os.makedirs(CACHE_DIR, exist_ok=True)
    done = {}
    if os.path.exists(CACHE):
        for line in open(CACHE, encoding='utf8'):
            try:
                d = json.loads(line)
                done[d['u']] = d
            except Exception:
                pass
    ext = [(n['id'], n['h']) for n in g['nodes'] if n.get('x') and n.get('h')]
    byhost = collections.defaultdict(list)
    for nid, h in ext:
        if h not in done:
            byhost[urlparse(h).netloc].append((nid, h))
    order, queues = [], list(byhost.values())
    while queues:  # 按站点轮流交错
        nxt = []
        for q in queues:
            order.append(q.pop(0))
            if q:
                nxt.append(q)
        queues = nxt
    print('external', len(ext), 'todo', len(order), flush=True)
    fh = open(CACHE, 'a', encoding='utf8')
    cnt = [0]

    def job(item):
        nid, h = item
        st, why = classify(h)
        with lock:
            done[h] = {'u': h, 's': st, 'r': why}
            fh.write(json.dumps(done[h]) + '\n')
            fh.flush()
            cnt[0] += 1
            if cnt[0] % 500 == 0:
                print(cnt[0], flush=True)
    with cf.ThreadPoolExecutor(64) as ex:
        list(ex.map(job, order))
    for nid, h in ext:
        d = done.get(h)
        stat['ext_' + (d['s'] if d else 'none')] += 1
        if d and d['s'] == 'dead':
            dead[nid] = d['r']
    json.dump(dead, open(OUT, 'w', encoding='utf8'), ensure_ascii=False, indent=0)
    print(dict(stat), 'dead', len(dead), collections.Counter(dead.values()))


if __name__ == '__main__':
    main()
