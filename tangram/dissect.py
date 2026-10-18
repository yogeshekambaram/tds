"""Search for a Tangram dissection of target.png.

At every step the topmost-leftmost uncovered target point must be a vertex of the piece covering it,
so we try every unused piece / orientation / reflection / vertex there, snap the anchor to integer
ticks (with a small jitter), and keep placements that stay inside the uncovered region.
Board ticks map to image pixels as px = 2*x, py = 576 - 2*y (board y points up).
"""
import json
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_erosion

import check_state as c

TARGET = np.array(Image.open(r"D:\projects\TDS\tangram\ORIGINAL.png").convert("L"), float) / 255 < 0.5
H, W = TARGET.shape
KINDS = {"large": ["large-a", "large-b"], "small": ["small-a", "small-b"]}
JIT = range(-3, 4)


def local(pid, o, refl):
    return [c.rotated(vx, vy, o, refl) for vx, vy in c.SHAPES[pid]]  # ticks, board frame


def to_px(ax, ay, pts):
    return [(2 * (ax + px), H - 2 * (ay + py)) for px, py in pts]


def raster(poly):
    img = Image.new("1", (W, H), 0)
    ImageDraw.Draw(img).polygon(poly, fill=1)
    return np.array(img)


def first_point(remaining):
    core = binary_erosion(remaining, iterations=2)
    if core.sum() < 30:
        return None
    ys, xs = np.nonzero(core)
    y = ys.min()
    x = xs[ys == y].min()
    return x, y - 2  # undo the erosion offset (approximately)


ORIENTS = {pid: {(o, r): local(pid, o, r) for o in range(8) for r in (0, 1)} for pid in c.IDS}
# Dedupe congruent orientations per piece (e.g. square has only 2 distinct shapes)
for pid, d in ORIENTS.items():
    seen, keep = set(), {}
    for k, pts in d.items():
        key = tuple(sorted((round(x - min(p[0] for p in pts), 3), round(y - min(p[1] for p in pts), 3)) for x, y in pts))
        if key not in seen:
            seen.add(key)
            keep[k] = pts
    ORIENTS[pid] = keep


def candidates(remaining, pid):
    pt = first_point(remaining)
    if pt is None:
        return []
    px, py = pt
    out = []
    for (o, r), pts in ORIENTS[pid].items():
        for vx, vy in pts:
            bx, by = px / 2 - vx, (H - py) / 2 - vy  # anchor (ticks) putting this vertex on the point
            for jx in JIT:
                for jy in JIT:
                    ax, ay = int(round(bx)) + jx, int(round(by)) + jy
                    m = raster(to_px(ax, ay, pts))
                    area = m.sum()
                    outside = (m & ~remaining).sum()
                    if outside < 0.04 * area:
                        out.append((outside / area, ax, ay, o, r, m))
    out.sort(key=lambda t: t[0])
    # keep distinct placements only
    uniq, seen = [], set()
    for t in out:
        k = (t[1] // 2, t[2] // 2, t[3], t[4])
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq[:6]


best = {"left": 1e9}


def solve(remaining, used, poses, depth=0):
    left = int(binary_erosion(remaining, iterations=2).sum())
    if left < best["left"]:
        best["left"] = left
        print(f"depth {depth} uncovered core {left}: {poses}", flush=True)
    if len(used) == 7:
        return poses if left < 30 else None
    tried_kinds = set()
    for pid in c.IDS:
        if pid in used:
            continue
        kind = pid.split("-")[0]
        if kind in tried_kinds:  # identical pieces are interchangeable
            continue
        tried_kinds.add(kind)
        for score, ax, ay, o, r, m in candidates(remaining, pid):
            res = solve(remaining & ~m, used | {pid}, {**poses, pid: [ax, ay, o, r]}, depth + 1)
            if res:
                return res
    return None


if __name__ == "__main__":
    sol = solve(TARGET.copy(), set(), {})
    if sol:
        state = [sol[pid] for pid in c.IDS]
        print("SOLUTION", json.dumps(state))
    else:
        print("no solution found", file=sys.stderr)
