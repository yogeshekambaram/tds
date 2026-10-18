"""Fit integer anchors so the exact outline's edge lines match the lines measured in the target image.

Measured lines (ticks, from the 0.5-coverage iso-contour; quantized by the target renderer to ~0.5 tick):
families h: y, v: x, d1: x - y, d2: x + y. A global translation (gx, gy) is fitted, since the grader's
canonical outline is translation-invariant.
"""
import itertools
import json
import math
import sys

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

import check_state as c

MEASURED = {"h": [108.5, 116.5, 131.0], "v": [80.0, 80.5, 125.5, 193.5], "d1": [17.0, 63.0, 64.0],
            "d2": [131.5, 141.5, 278.5]}


def outline_lines(state):
    polys = [Polygon(p) for p in c.polygons(state)]
    overlap = sum(a.intersection(b).area for a, b in itertools.combinations(polys, 2))
    u = unary_union(polys).buffer(0.6, join_style=2, mitre_limit=10).buffer(-0.6, join_style=2, mitre_limit=10)
    rings = [g.exterior for g in getattr(u, "geoms", [u])]
    lines = {"h": [], "v": [], "d1": [], "d2": []}
    segs = [seg for ring in rings for seg in zip(list(ring.coords), list(ring.coords)[1:])]
    for (x1, y1), (x2, y2) in segs:
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 1e-6:
            continue
        dx, dy = (x2 - x1) / L, (y2 - y1) / L
        if abs(dy) < 1e-6:
            lines["h"].append((y1, L))
        elif abs(dx) < 1e-6:
            lines["v"].append((x1, L))
        elif abs(dx - dy) < 1e-6 or abs(dx + dy) < 1e-6 and (dx * dy > 0):
            lines["d1"].append((x1 - y1, L))
        else:
            lines["d2"].append((x1 + y1, L))
    # merge collinear segments with the same invariant
    out = {}
    for k, v in lines.items():
        vals = {}
        for inv, L in v:
            key = round(inv, 4)
            vals[key] = vals.get(key, 0) + L
        out[k] = vals
    return out, overlap


def score(state):
    lines, overlap = outline_lines(state)
    if lines is None or overlap > 1e-3:
        return 1e9, None
    best = None
    for gx in np.arange(-1, 1.01, 0.125):
        for gy in np.arange(-1, 1.01, 0.125):
            shift = {"h": gy, "v": gx, "d1": gx - gy, "d2": gx + gy}
            s = 0.0
            for fam, meas in MEASURED.items():
                cand = [inv + shift[fam] for inv, L in lines[fam].items() if L > 3.0]
                for m in meas:  # every measured line needs a candidate line
                    s += min((abs(m - x) for x in cand), default=5) ** 2
                for x in cand:  # every candidate line needs a measured line
                    s += min(abs(m - x) for m in meas) ** 2
            if best is None or s < best[0]:
                best = (s, (gx, gy))
    return best


if __name__ == "__main__":
    state = json.load(open(sys.argv[1]))
    cur, g = score(state)
    print("start", round(cur, 3), g, flush=True)
    for rnd in range(12):
        improved = False
        for i in range(7):
            for dx, dy in itertools.product(range(-3, 4), repeat=2):
                if dx == dy == 0:
                    continue
                st = [list(p) for p in state]
                st[i][0] += dx
                st[i][1] += dy
                s, g2 = score(st)
                if s < cur - 1e-9:
                    cur, g, state, improved = s, g2, st, True
        print("round", rnd, round(cur, 4), g, json.dumps(state), flush=True)
        if not improved:
            break
    json.dump(state, open("line_fit.json", "w"))
    lines, _ = outline_lines(state)
    for fam in lines:
        print(fam, sorted((round(k, 3), round(L, 1)) for k, L in lines[fam].items()))
