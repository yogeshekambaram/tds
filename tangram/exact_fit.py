"""Pixel-centre rasterizer + search over anchors AND orientation variants, no overlaps allowed."""
import itertools
import json
import sys

import numpy as np
from matplotlib.path import Path
from PIL import Image

import check_state as c

# Board y points up (svg y = height - y), so fit against the vertically flipped target
gray = np.array(Image.open(r"D:\projects\TDS\tangram\target_flipped.png").convert("L"))
target = gray < 128
H, W = target.shape
# Only evaluate near the shape to keep it fast
ys, xs = np.nonzero(target)
X0, X1, Y0, Y1 = xs.min() - 20, xs.max() + 20, ys.min() - 20, ys.max() + 20
gx, gy = np.meshgrid(np.arange(X0, X1) + 0.5, np.arange(Y0, Y1) + 0.5)
CENTRES = np.column_stack([gx.ravel(), gy.ravel()])
TGT = target[Y0:Y1, X0:X1].ravel()
EPS = 1e-7
DILATE = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0

_cache = {}


def piece_mask(pid, pose, sx, sy):
    key = (pid, tuple(pose), sx, sy)
    if key not in _cache:
        x, y, o, r = pose
        pts = [c.rotated(vx, vy, o, bool(r)) for vx, vy in c.SHAPES[pid]]
        poly = [((x + px) * 2 + sx, (y + py) * 2 + sy) for px, py in pts]
        area = sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))
        _cache[key] = Path(poly).contains_points(CENTRES, radius=DILATE if area > 0 else -DILATE)
    return _cache[key]


def mismatch(state, sx=0, sy=0):
    m = np.zeros(len(CENTRES), bool)
    for pid, pose in zip(c.IDS, state):
        m |= piece_mask(pid, pose, sx, sy)
    return int((m ^ TGT).sum())


def overlaps(p, q):
    for poly in (p, q):
        n = len(poly)
        for i in range(n):
            (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
            ax, ay = y2 - y1, x1 - x2
            pp = [x * ax + y * ay for x, y in p]
            qq = [x * ax + y * ay for x, y in q]
            if min(pp) >= max(qq) - EPS or min(qq) >= max(pp) - EPS:
                return False
    return True


def n_overlaps(state):
    P = c.polygons(state)
    return sum(overlaps(P[i], P[j]) for i in range(7) for j in range(i + 1, 7))


def variants(pid, pose):
    """Same footprint, different (orientation, reflected, anchor): re-anchor to keep centroid."""
    x, y, o, r = pose
    base = [c.rotated(vx, vy, o, bool(r)) for vx, vy in c.SHAPES[pid]]
    cx = x + sum(p[0] for p in base) / len(base)
    cy = y + sum(p[1] for p in base) / len(base)
    out = []
    for o2, r2 in itertools.product(range(8), (0, 1)):
        if (o2 % 2) != (o % 2):
            continue
        loc = [c.rotated(vx, vy, o2, r2) for vx, vy in c.SHAPES[pid]]
        ax = cx - sum(p[0] for p in loc) / len(loc)
        ay = cy - sum(p[1] for p in loc) / len(loc)
        out.append([round(ax), round(ay), o2, r2])
    return out


def cost(state):
    return mismatch(state) + 100000 * n_overlaps(state)


s = json.loads(open(sys.argv[1]).read())
cur = cost(s)
print("start", cur, "overlaps", n_overlaps(s), flush=True)
R = 3
moves = [(dx, dy) for dx in range(-R, R + 1) for dy in range(-R, R + 1)]
for rnd in range(10):
    improved = False
    # single-piece moves including orientation variants
    for i, pid in enumerate(c.IDS):
        best = (cur, s)
        for v in variants(pid, s[i]) + [s[i]]:
            for dx, dy in moves:
                t = [p[:] for p in s]
                t[i] = [v[0] + dx, v[1] + dy, v[2], v[3]]
                sc = cost(t)
                if sc < best[0]:
                    best = (sc, t)
        if best[0] < cur:
            cur, s, improved = best[0], best[1], True
    # pair moves (positions only) to escape overlap deadlocks
    if not improved:
        small = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3)]
        best = (cur, s)
        for i, j in itertools.combinations(range(7), 2):
            for (a, b), (e, f) in itertools.product(small, small):
                t = [p[:] for p in s]
                t[i][0] += a; t[i][1] += b
                t[j][0] += e; t[j][1] += f
                sc = cost(t)
                if sc < best[0]:
                    best = (sc, t)
        if best[0] < cur:
            cur, s, improved = best[0], best[1], True
    print("round", rnd, cur, json.dumps(s), flush=True)
    if not improved:
        break

open(sys.argv[2], "w").write(json.dumps(s))
print("final mismatch", mismatch(s), "overlaps", n_overlaps(s), json.dumps(s))
