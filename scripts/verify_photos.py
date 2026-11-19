"""验证头像 URL 在 https 下能否被网页嵌入（TLS、状态码、类型、CORP、尺寸）。

读取 photos / wikidata_photos / people_photos 三个 json，逐个请求 https 版本，
结果缓存到 data/cache/photo_verify.jsonl（可续跑），坏的写入 data/enrich/photo_https_bad.json（{原始URL: 原因}）。
"""
import io, json, sys, threading, time, itertools, collections
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse
import requests
from PIL import Image
sys.path.insert(0, 'scripts')
from fetch_pages import allowed

ROOT = Path(__file__).resolve().parent.parent
SRC = ['photos', 'wikidata_photos', 'people_photos']
CACHE = ROOT / 'data/cache/photo_verify.jsonl'
OUT = ROOT / 'data/enrich/photo_https_bad.json'
UA = 'lineage-tree-research-script/0.1'
_hl, _next, _g = {}, {}, threading.Lock()


def https(u):
    return 'https://' + u[7:] if u.startswith('http://') else u


def wait_host(h):
    with _g:
        lock = _hl.setdefault(h, threading.Lock())
    with lock:  # 每个主机串行预约发送时间，间隔 >= 1s
        t = _next.get(h, 0) - time.time()
        if t > 0:
            time.sleep(t)
        _next[h] = time.time() + 1.0


def check(url):
    u = https(url)
    try:
        if not allowed(u):
            return 'robots'
        wait_host(urlparse(u).netloc)
        with requests.get(u, headers={'User-Agent': UA}, stream=True, timeout=15) as r:
            if r.status_code != 200:
                return f'status {r.status_code}'
            ct = r.headers.get('content-type', '').split(';')[0].strip().lower()
            if not ct.startswith('image/'):
                return f'not image ({ct or "none"})'
            cp = r.headers.get('cross-origin-resource-policy', '').strip().lower()
            if cp in ('same-origin', 'same-site'):
                return f'CORP {cp}'
            data = r.raw.read(65536, decode_content=True)
        try:
            w, h = Image.open(io.BytesIO(data)).size
        except Exception:
            return 'ok'  # SVG 或被截断，无法量尺寸，视为可用
        return f'too small {w}x{h}' if min(w, h) < 60 else 'ok'
    except requests.exceptions.SSLError:
        return 'tls error'
    except requests.exceptions.RequestException as e:
        return 'conn error ' + type(e).__name__


def main():
    srcs = {s: json.loads((ROOT / f'data/enrich/{s}.json').read_text('utf8')) for s in SRC}
    urls = sorted({https(u) for d in srcs.values() for u in d.values()})
    cache = {}
    if CACHE.exists():
        for l in CACHE.read_text('utf8').splitlines():
            try:
                o = json.loads(l); cache[o['url']] = o['r']
            except Exception:
                pass
    todo = [u for u in urls if u not in cache]
    byh = collections.defaultdict(list)
    for u in todo:
        byh[urlparse(u).netloc].append(u)
    order = [u for grp in itertools.zip_longest(*byh.values()) for u in grp if u]  # 按主机轮转
    print(len(urls), 'urls,', len(todo), 'to check', flush=True)
    fl = threading.Lock(); n = [0]
    with open(CACHE, 'a', encoding='utf8') as f:
        def work(u):
            r = check(u)
            with fl:
                cache[u] = r
                f.write(json.dumps({'url': u, 'r': r}) + '\n'); f.flush()
                n[0] += 1
                if n[0] % 100 == 0:
                    print(n[0], flush=True)
        with ThreadPoolExecutor(48) as ex:
            list(ex.map(work, order))
    bad, stats = {}, {}
    for s, d in srcs.items():
        c = collections.Counter()
        for k, u in d.items():
            r = cache.get(https(u), 'ok')
            c[r.split(' ')[0] + (' ' + r.split(' ')[1] if r.startswith(('status', 'CORP')) else '')] += 1
            if r not in ('ok', 'robots'):
                bad[u] = r
        stats[s] = dict(c)
    OUT.write_text(json.dumps(bad, ensure_ascii=False, indent=1), 'utf8')
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print('bad', len(bad))


if __name__ == '__main__':
    main()
