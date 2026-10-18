"""DFS over integer anchors near the ideal dissection, trying every (orientation, reflection) variant
that yields the same polygon (so the anchor can sit on any congruent corner). Scores by outline lines.
"""
import json
import math

from shapely.geometry import Polygon

import check_state as c
import line_fit as lf

exec(open("round_search.py").read().split("IDS =")[0])  # IDEAL
ORDER = ["large-b", "large-a", "small-b", "parallelogram", "square", "medium", "small-a"]
TOL = 1e-3


def verts(pid, o, r, ax, ay):
    return [(ax + x, ay + y) for x, y in (c.rotated(vx, vy, o, r) for vx, vy in c.SHAPES[pid])]


def key(pts):
    return frozenset((round(x * 4) / 4, round(y * 4) / 4) for x, y in pts)


def same(a, b):
    a = sorted(a); b = sorted(b)
    return len(a) == len(b) and all(abs(p[0] - q[0]) < 1e-3 and abs(p[1] - q[1]) < 1e-3 for p, q in zip(a, b))


def variants(pid):
    o0, r0, x0, y0 = IDEAL[pid]
    target = verts(pid, o0, r0, x0, y0)
    out = []
    for o in range(8):
        for r in (0, 1):
            local = [c.rotated(vx, vy, o, r) for vx, vy in c.SHAPES[pid]]
            # anchor such that the polygon coincides with the ideal one: try matching each vertex to vertex 0
            for tx, ty in target:
                ax, ay = tx - local[0][0], ty - local[0][1]
                if same([(ax + x, ay + y) for x, y in local], target):
                    out.append((o, r, ax, ay))
    uniq = {}
    for o, r, ax, ay in out:
        uniq[(o, r)] = (o, r, ax, ay)
    return list(uniq.values())


def options(pid):
    res = []
    seen = set()
    for o, r, x, y in variants(pid):
        for ax in range(math.floor(x) - 1, math.ceil(x) + 2):
            for ay in range(math.floor(y) - 1, math.ceil(y) + 2):
                if math.hypot(ax - x, ay - y) <= 1.3:
                    P = Polygon(verts(pid, o, r, ax, ay))
                    k = (o, r, ax, ay) if False else key(list(P.exterior.coords)[:-1])
                    if k not in seen:
                        seen.add(k)
                        res.append(((ax, ay, o, r), P))
    return res


OPTS = {pid: options(pid) for pid in ORDER}
OPTS["large-b"] = [o for o in OPTS["large-b"] if o[0][:2] == (80, 116)]  # fix translation
print({p: len(v) for p, v in OPTS.items()}, flush=True)

results = []


def dfs(i, placed):
    if i == len(ORDER):
        st = [list(placed[p][0]) for p in c.IDS]
        s, g = lf.score(st)
        results.append((s, g, st))
        return
    pid = ORDER[i]
    for pose, P in OPTS[pid]:
        if any(P.intersection(Q).area > TOL for _, Q in placed.values()):
            continue
        placed[pid] = (pose, P)
        dfs(i + 1, placed)
        del placed[pid]


dfs(0, {})
results.sort(key=lambda t: t[0])
print(len(results), "overlap-free placements")
for s, g, st in results[:10]:
    print(round(s, 4), g, json.dumps(st))
json.dump([st for _, _, st in results[:60]], open("variant_candidates.json", "w"))
