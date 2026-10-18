"""DFS over integer anchors within ~1.3 ticks of the ideal dissection; prune overlaps; score vs measured lines."""
import json
import math

from shapely.geometry import Polygon

import check_state as c
import line_fit as lf

exec(open("round_search.py").read().split("IDS =")[0])  # IDEAL
ORDER = ["large-b", "large-a", "small-b", "parallelogram", "square", "medium", "small-a"]
TOL = 1e-3


def options(pid):
    o, r, x, y = IDEAL[pid]
    out = []
    for ax in range(math.floor(x) - 1, math.ceil(x) + 2):
        for ay in range(math.floor(y) - 1, math.ceil(y) + 2):
            if math.hypot(ax - x, ay - y) <= 1.3:
                out.append((ax, ay, o, r))
    return out


def poly(pid, pose):
    st = {p: [0, 0, 0, 0] for p in c.IDS}
    st[pid] = list(pose)
    return Polygon(c.polygons([st[p] for p in c.IDS])[c.IDS.index(pid)])


results, count = [], [0]


def dfs(i, placed):
    if i == len(ORDER):
        count[0] += 1
        st = [list(placed[p][0]) for p in c.IDS]
        s, g = lf.score(st)
        results.append((s, g, st))
        return
    pid = ORDER[i]
    for pose in options(pid):
        P = poly(pid, pose)
        if any(P.intersection(Q).area > TOL for _, Q in placed.values()):
            continue
        placed[pid] = (pose, P)
        dfs(i + 1, placed)
        del placed[pid]


dfs(0, {})
results.sort(key=lambda t: t[0])
print(count[0], "overlap-free roundings")
for s, g, st in results[:10]:
    print(round(s, 4), g, json.dumps(st))
json.dump([st for _, _, st in results[:30]], open("round_candidates.json", "w"))
