"""Refine poses: best pixel match against target.png with no piece overlaps."""
import json
import sys

import numpy as np
from PIL import Image, ImageDraw

import check_state as c

target = np.array(Image.open(r"D:\projects\TDS\tangram\target.png").convert("L")) < 128
EPS = 1e-7


def overlaps(p, q):
    # Separating axis theorem; touching (zero-depth) contact is allowed
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


def n_bad(state):
    polys = c.polygons(state)
    bad = sum(1 for poly in polys
              if any(x < -EPS or x > 384 + EPS or y < -EPS or y > 288 + EPS for x, y in poly))
    return bad + sum(overlaps(polys[i], polys[j]) for i in range(7) for j in range(i + 1, 7))


def valid(state):
    return n_bad(state) == 0


def render(state, dx, dy):
    img = Image.new("1", (768, 576), 0)
    d = ImageDraw.Draw(img)
    for poly in c.polygons(state):
        d.polygon([(x * 2 + dx, y * 2 + dy) for x, y in poly], fill=1)
    return np.array(img)


def score(state):
    return min(int((render(state, dx, dy) ^ target).sum()) for dx in range(-2, 3) for dy in range(-2, 3))


s = json.loads(open(sys.argv[1]).read())
cost = lambda st: score(st) + 100000 * n_bad(st)
cur = cost(s)
print("start", cur, valid(s))
R = 6
improved = True
while improved:
    improved = False
    for i in range(7):
        best = (cur, s)
        for ddx in range(-R, R + 1):
            for ddy in range(-R, R + 1):
                if ddx == ddy == 0:
                    continue
                t = [p[:] for p in s]
                t[i][0] += ddx
                t[i][1] += ddy
                sc = cost(t)
                if sc < best[0]:
                    best = (sc, t)
        if best[0] < cur:
            cur, s = best
            improved = True
    print("pass", cur, json.dumps(s))
open(sys.argv[2], "w").write(json.dumps(s))
print("final", cur, f"{cur / target.sum() * 100:.2f}%", "valid", valid(s), json.dumps(s))
