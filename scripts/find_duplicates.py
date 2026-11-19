"""Find high-confidence duplicate nodes in docs/data/graph.json.

Writes data/enrich/merge_candidates.json: [{keep, drop, reason}].
Reasons: same_scholar, same_homepage, same_name_shared_advisor,
         professor_same_advisor, professor_no_advisor.
Keep priority: professor ids > w:/wd: ids > others. Two professors are never merged.
"""
import json, re, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from names import fold, key

ROOT = Path(__file__).resolve().parent.parent
g = json.load(open(ROOT / 'docs/data/graph.json', encoding='utf8'))
nodes = {n['id']: n for n in g['nodes']}
is_prof = lambda i: not nodes[i].get('x')
advisors = collections.defaultdict(set)  # student -> advisors
for e in g['edges']:
    advisors[e[1]].add(e[0])

SUFFIX = {'jr', 'sr', 'ii', 'iii', 'iv'}
def toks(n):
    return [t for t in fold(n['n']).split() if t not in SUFFIX]

def compatible(a, b):
    """Same first & last token (after folding); middle tokens must not conflict."""
    ta, tb = toks(a), toks(b)
    if len(ta) < 2 or len(tb) < 2 or ta[0] != tb[0] or ta[-1] != tb[-1]:
        return False
    ma, mb = ta[1:-1], tb[1:-1]
    for x in ma:
        for y in mb:
            if x[0] != y[0]:
                return False
    return True

def strict(a, b):
    f = lambda n: [t for t in toks(n) if len(t) > 1]
    return f(a) == f(b)

def norm_url(u):
    u = re.sub(r'^https?://(www\.)?', '', u.strip().lower())
    return re.sub(r'(index\.(html?|php))?/*$', '', u.split('#')[0].split('?')[0])

def scholar_id(u):
    m = re.search(r'user=([\w-]{8,})', u or '')
    return m.group(1) if m else None

def rank(i):
    if is_prof(i): return 0
    if i.startswith(('w:', 'wd:')): return 1
    return 2

pairs = []  # (a, b, reason)
# Homepage / scholar
for field, fn, reason in (('h', norm_url, 'same_homepage'), ('g', scholar_id, 'same_scholar')):
    grp = collections.defaultdict(list)
    for i, n in nodes.items():
        if n.get(field):
            k = fn(n[field])
            if k and (field == 'g' or '/' in k):  # skip bare-domain homepages (generic hosts)
                grp[k].append(i)
    for k, ids in grp.items():
        if len(ids) < 2 or len(ids) > 6:
            continue
        for a in ids[1:]:
            for b in ids:
                if b != a and compatible(nodes[a], nodes[b]):
                    pairs.append((a, b, reason)); break

# Same key + shared advisor (external-external)
byk = collections.defaultdict(list)
for i, n in nodes.items():
    k = key(n['n'])
    if k: byk[k].append(i)
for k, ids in byk.items():
    ex = [i for i in ids if not is_prof(i)]
    for ai in range(len(ex)):
        for bi in range(ai + 1, len(ex)):
            a, b = ex[ai], ex[bi]
            if advisors[a] & advisors[b] and compatible(nodes[a], nodes[b]):
                pairs.append((a, b, 'same_name_shared_advisor'))
    # external vs professor
    profs = [i for i in ids if is_prof(i)]
    if len(profs) != 1:
        continue
    p = profs[0]
    for x in ex:
        if not compatible(nodes[x], nodes[p]):
            continue
        if advisors[x] & advisors[p]:
            pairs.append((x, p, 'professor_same_advisor'))
        elif not advisors[p] and advisors[x] and strict(nodes[x], nodes[p]) and '-' not in nodes[x]['n'] + nodes[p]['n']:
            pairs.append((x, p, 'professor_no_advisor'))

# Union with constraint: at most one professor per cluster
par = {}
def find(x):
    while par.setdefault(x, x) != x:
        par[x] = par[par[x]]; x = par[x]
    return x
has_prof = {}
reasons = {}
for a, b, r in pairs:
    ra, rb = find(a), find(b)
    if ra == rb: continue
    if has_prof.get(ra, is_prof(ra) and False) and has_prof.get(rb, False): continue
    pa = any(is_prof(m) for m in [a]) or has_prof.get(ra, False)
    pb = any(is_prof(m) for m in [b]) or has_prof.get(rb, False)
    if pa and pb: continue
    par[ra] = rb
    has_prof[rb] = pa or pb
    reasons[(a, b)] = r
clusters = collections.defaultdict(list)
for i in par: clusters[find(i)].append(i)
# reason per drop: first pair involving it
why = {}
for (a, b), r in reasons.items():
    why.setdefault(a, r); why.setdefault(b, r)
out = []
for members in clusters.values():
    keep = min(members, key=lambda i: (rank(i), i))
    for d in members:
        if d != keep:
            out.append({'keep': keep, 'drop': d, 'reason': why.get(d, 'transitive')})
out.sort(key=lambda o: (o['reason'], o['keep']))
# 累积：构建时已合并掉的重复节点不会再出现在图里，保留上一轮的结果（drop 已不存在的条目构建时自动跳过）
_prev_path = ROOT / 'data/enrich/merge_candidates.json'
if _prev_path.exists():
    _seen = {m['drop'] for m in out}
    out += [m for m in json.loads(_prev_path.read_text(encoding='utf8')) if m['drop'] not in _seen]
(ROOT / 'data/enrich/merge_candidates.json').write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf8')
cnt = collections.Counter(o['reason'] for o in out)
print(len(out), dict(cnt))
import random
random.seed(1)
for r in cnt:
    sub = [o for o in out if o['reason'] == r]
    for o in random.sample(sub, min(3, len(sub))):
        a, b = nodes[o['keep']], nodes[o['drop']]
        print(r, '|', o['keep'], a['n'], '<-', o['drop'], b['n'], '|', a.get('h') or a.get('g'), '|', b.get('h') or b.get('g'), '|', [nodes[x]['n'] for x in (advisors[o['drop']])][:2])
