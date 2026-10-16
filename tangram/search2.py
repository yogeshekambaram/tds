"""Joint 2-piece search for a valid (no-overlap) state that best matches target.png."""
import itertools
import json
import sys

import numpy as np
from PIL import Image, ImageDraw

import check_state as c

target = np.array(Image.open(r"D:\projects\TDS\tangram\target.png").convert("L")) < 128
EPS = 1e-7


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


def valid(state):
    polys = c.polygons(state)
    return not any(overlaps(polys[i], polys[j]) for i in range(7) for j in range(i + 1, 7))


def render(state, dx, dy):
    img = Image.new("1", (768, 576), 0)
    d = ImageDraw.Draw(img)
    for poly in c.polygons(state):
        d.polygon([(x * 2 + dx, y * 2 + dy) for x, y in poly], fill=1)
    return np.array(img)


def score(state, shifts):
    return min(int((render(state, dx, dy) ^ target).sum()) for dx, dy in shifts)


s = json.loads(open(sys.argv[1]).read())
ALL = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3)]
R = 2
moves = [(dx, dy) for dx in range(-R, R + 1) for dy in range(-R, R + 1)]
cur = score(s, ALL) if valid(s) else 10**9
print("start", cur)
for rnd in range(4):
    best = (cur, s)
    for i, j in itertools.combinations(range(7), 2):
        for (a, b), (e, f) in itertools.product(moves, moves):
            t = [p[:] for p in s]
            t[i][0] += a; t[i][1] += b
            t[j][0] += e; t[j][1] += f
            if not valid(t):
                continue
            sc = score(t, ALL)
            if sc < best[0]:
                best = (sc, t)
    if best[0] >= cur:
        break
    cur, s = best
    print("round", rnd, cur, json.dumps(s), flush=True)
open(sys.argv[2], "w").write(json.dumps(s))
print("final", cur, f"{cur / target.sum() * 100:.2f}%", "valid", valid(s), json.dumps(s))
