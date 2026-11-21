#!/usr/bin/env python3
"""Sanity checks for docs/data/graph.json. Exit 1 on hard errors."""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "docs" / "data" / "graph.json"
MAX = 10

g = json.loads(PATH.read_text(encoding="utf-8"))
nodes = {n["id"]: n for n in g["nodes"]}
edges = g["edges"]
unis = g.get("unis", [])
summary = g.get("summary", {})

hard = 0


def is_prof(i):
    return re.fullmatch(r"p\d+", i) is not None or i.startswith("nf:")  # nf:<名字> 是还没进 CSRankings 的新老师


def report(title, items, hard_err=False, total=None):
    global hard
    n = len(items) if total is None else total
    tag = "ERROR" if (hard_err and n) else ("WARN " if n else "ok   ")
    print(f"[{tag}] {title}: {n}")
    for it in items[:MAX]:
        print("        ", it)
    if hard_err and n:
        hard += 1


def name(i):
    return nodes[i]["n"] if i in nodes else i


print(f"nodes={len(nodes)} (list {len(g['nodes'])}) edges={len(edges)} unis={len(unis)}")

# endpoints
missing = [e for e in edges if e[0] not in nodes or e[1] not in nodes]
report("edge endpoints missing", [f"{e[0]} -> {e[1]}" for e in missing], True)

# bad u index
badu = [n["id"] for n in nodes.values() if "u" in n and n["u"] is not None and not (isinstance(n["u"], int) and 0 <= n["u"] < len(unis))]
report("node u index out of range", badu, True)

report("duplicate node ids", [k for k, v in Counter(n["id"] for n in g["nodes"]).items() if v > 1], True)

# self-loops
report("self-loops", [f"{name(e[0])} ({e[0]})" for e in edges if e[0] == e[1]])

# duplicates
c = Counter((e[0], e[1], e[2]) for e in edges)
dups = [f"{name(k[0])} -> {name(k[1])} [{k[2]}] x{v}" for k, v in c.items() if v > 1]
report("duplicate edges (same advisor/student/type)", dups)
c2 = Counter((e[0], e[1]) for e in edges)
dups2 = [f"{name(k[0])} -> {name(k[1])} x{v}" for k, v in c2.items() if v > 1]
report("pairs with multiple edges (any type)", dups2)

# cycles (advisor -> student), iterative DFS, non-disputed or all edges
adj = defaultdict(set)
for a, s, *_ in edges:
    if a != s and a in nodes and s in nodes:
        adj[a].add(s)
color = {}
cycles = []
for root in list(adj):
    if root in color:
        continue
    stack = [(root, iter(adj[root]))]
    color[root] = 1
    path = [root]
    while stack:
        v, it = stack[-1]
        adv = False
        for w in it:
            if color.get(w, 0) == 0:
                color[w] = 1
                path.append(w)
                stack.append((w, iter(adj.get(w, ()))))
                adv = True
                break
            elif color[w] == 1:
                cycles.append(path[path.index(w):] + [w])
        if not adv:
            color[v] = 2
            path.pop()
            stack.pop()
report("advisor-chain cycles", [" > ".join(name(x) for x in cy) for cy in cycles])

# >3 non-disputed phd advisors
advs = defaultdict(set)
for e in edges:
    if e[2] == "phd" and not e[5]:
        advs[e[1]].add(e[0])
many = sorted(((len(v), k) for k, v in advs.items() if len(v) > 3), reverse=True)
report("people with >3 non-disputed PhD advisors", [f"{name(k)} ({k}): {n} advisors" for n, k in many])

# professors with >120 students
studs = defaultdict(set)
for e in edges:
    studs[e[0]].add(e[1])
big = sorted(((len(v), k) for k, v in studs.items() if len(v) > 120), reverse=True)
report("advisors with >120 students", [f"{name(k)} ({k}): {n}" for n, k in big])

# names
BAD_WORDS = re.compile(r"\b(Guide|Course|Courses|Lab|Laboratory|Center|Centre|Department|University|Program|News|Institute|School|College|Group|Seminar|Publications?|Research|Home|Contact|Faculty|Students?|Alumni|People)\b", re.I)
qq, fffd, dig, words = [], [], [], []
for n in nodes.values():
    nm = n.get("n") or ""
    lab = f"{nm!r} ({n['id']})"
    if "??" in nm:
        qq.append(lab)
    if "�" in nm:
        fffd.append(lab)
    if re.search(r"\d", nm):
        dig.append(lab)
    if BAD_WORDS.search(nm) or not nm.strip() or len(nm) > 60:
        words.append(lab)
report("names containing '??'", qq)
report("names containing U+FFFD", fffd)
report("names containing digits", dig)
report("names looking like non-names (Guide/Lab/Center/...)", words)

# URLs
URL_FIELDS = ("h", "li", "g", "ph", "c")
badurl = []
for n in nodes.values():
    for f in URL_FIELDS:
        v = n.get(f)
        if v and not re.match(r"https?://", str(v), re.I):
            badurl.append(f"{n['id']} {n.get('n')!r} {f}={str(v)[:60]!r}")
for e in edges:
    # 各来源的出处链接用 " | " 连接（顺序同来源，某个来源没有链接时那一段为空）
    if len(e) > 4 and e[4] and not all(re.match(r"https?://", u, re.I) for u in str(e[4]).split(" | ") if u.strip()):
        badurl.append(f"edge {e[0]}->{e[1]} url={str(e[4])[:60]!r}")
report("non-http(s) URLs", badurl, True)

hp = defaultdict(set)
for n in nodes.values():
    h = n.get("h")
    if h:
        hp[h.rstrip("/").lower()].add(n["id"])
shared = sorted(((len(v), k) for k, v in hp.items() if len(v) > 3), reverse=True)
report("homepage URL shared by >3 people", [f"{k}: {n} people" for n, k in shared])

# summary consistency
print("--- summary consistency")
issues = []
if "edges" in summary and summary["edges"] != len(edges):
    issues.append(f"summary.edges={summary['edges']} vs actual {len(edges)}")
if "professors" in summary:
    prof_ids = {n["id"] for n in nodes.values() if is_prof(n["id"])}
    if summary["professors"] != len(prof_ids):
        issues.append(f"summary.professors={summary['professors']} vs non-x: nodes {len(prof_ids)}")
if "external_people" in summary:
    ext = sum(1 for k in nodes if not is_prof(k))
    if summary["external_people"] != ext:
        issues.append(f"summary.external_people={summary['external_people']} vs x: nodes {ext}")
pp = sum(1 for e in edges if is_prof(e[0]) and is_prof(e[1]))
if "prof_to_prof_edges" in summary and summary["prof_to_prof_edges"] != pp:
    issues.append(f"summary.prof_to_prof_edges={summary['prof_to_prof_edges']} vs actual {pp}")
if "with_student" in summary:
    ws = len({e[0] for e in edges if is_prof(e[0])})
    if summary["with_student"] != ws:
        issues.append(f"summary.with_student={summary['with_student']} vs professors with a student edge {ws}")
if "with_advisor" in summary:
    wa = len({e[1] for e in edges if is_prof(e[1])})
    if summary["with_advisor"] != wa:
        issues.append(f"summary.with_advisor={summary['with_advisor']} vs professors with an advisor edge {wa}")
# by_source: only comparable when sources field is a plain tag
srcs = Counter()
for e in edges:
    for s in str(e[3]).split("+"):
        srcs[s] += 1
print("   edge-source tag counts:", dict(srcs.most_common(15)))
print("   summary.by_source sum:", sum(summary.get("by_source", {}).values()), "(tags may overlap; informational)")
report("summary vs actual mismatches", issues)

print()
print("RESULT:", "FAIL (hard errors)" if hard else "PASS (warnings only)")
sys.exit(1 if hard else 0)
