"""Fit board state to the target using anti-aliased coverage (8x8 supersampling), no outline.

Usage: python aa_fit.py <start.json> <out.json>
Works in the board's y-up frame (target is flipped vertically before fitting).
"""
import itertools
import json
import sys

import numpy as np
from matplotlib.path import Path
from PIL import Image, ImageOps

import check_state as c

SS = 8
img = ImageOps.flip(Image.open(r"D:\projects\TDS\tangram\ORIGINAL.png").convert("L"))
dark = 1.0 - np.array(img, dtype=float) / 255.0
ys, xs = np.nonzero(dark > 0)
X0, X1, Y0, Y1 = xs.min() - 6, xs.max() + 7, ys.min() - 6, ys.max() + 7
TGT = dark[Y0:Y1, X0:X1]
h, w = TGT.shape
sub = (np.arange(SS) + 0.5) / SS
gx = (X0 + np.arange(w)[:, None] + sub[None, :]).ravel()
gy = (Y0 + np.arange(h)[:, None] + sub[None, :]).ravel()
PX, PY = np.meshgrid(gx, gy)
PTS = np.column_stack([PX.ravel(), PY.ravel()])
EPS = 1e-7
_cache = {}


def piece_cov(pid, pose):
    key = (pid, tuple(pose))
    if key not in _cache:
        x, y, o, r = pose
        poly = [((x + px) * 2, (y + py) * 2) for px, py in
                (c.rotated(vx, vy, o, bool(r)) for vx, vy in c.SHAPES[pid])]
        _cache[key] = Path(poly).contains_points(PTS).reshape(h * SS, w * SS)
    return _cache[key]


def coverage(state):
    m = np.zeros((h * SS, w * SS), bool)
    for pid, pose in zip(c.IDS, state):
        m |= piece_cov(pid, pose)
    return m.reshape(h, SS, w, SS).mean(axis=(1, 3))


def err(state):
    return float(np.abs(coverage(state) - TGT).sum())


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
    x, y, o, r = pose
    base = [c.rotated(vx, vy, o, bool(r)) for vx, vy in c.SHAPES[pid]]
    cx = x + sum(p[0] for p in base) / len(base)
    cy = y + sum(p[1] for p in base) / len(base)
    out = []
    for o2, r2 in itertools.product(range(8), (0, 1)):
        if o2 % 2 != o % 2:
            continue
        loc = [c.rotated(vx, vy, o2, r2) for vx, vy in c.SHAPES[pid]]
        out.append([round(cx - sum(p[0] for p in loc) / len(loc)),
                    round(cy - sum(p[1] for p in loc) / len(loc)), o2, r2])
    return out


def cost(state):
    return err(state) + 1e5 * n_overlaps(state)


if __name__ == "__main__":
    s = json.load(open(sys.argv[1]))
    cur = cost(s)
    print("start", round(cur, 2), flush=True)
    R = 2
    moves = [(dx, dy) for dx in range(-R, R + 1) for dy in range(-R, R + 1)]
    for rnd in range(10):
        improved = False
        for i, pid in enumerate(c.IDS):
            best = (cur, s)
            for v in variants(pid, s[i]) + [s[i]]:
                for dx, dy in moves:
                    t = [p[:] for p in s]
                    t[i] = [v[0] + dx, v[1] + dy, v[2], v[3]]
                    sc = cost(t)
                    if sc < best[0] - 1e-9:
                        best = (sc, t)
            if best[0] < cur - 1e-9:
                cur, s, improved = best[0], best[1], True
        if not improved:
            small = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            best = (cur, s)
            for i, j in itertools.combinations(range(7), 2):
                for (a, b), (e, f) in itertools.product(small, small):
                    t = [p[:] for p in s]
                    t[i][0] += a; t[i][1] += b; t[j][0] += e; t[j][1] += f
                    sc = cost(t)
                    if sc < best[0] - 1e-9:
                        best = (sc, t)
            if best[0] < cur - 1e-9:
                cur, s, improved = best[0], best[1], True
        print("round", rnd, round(cur, 2), "overlaps", n_overlaps(s), json.dumps(s), flush=True)
        if not improved:
            break
    json.dump(s, open(sys.argv[2], "w"))
