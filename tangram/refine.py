"""Local search: move 1-2 pieces by up to +-2 ticks (overlap-free) to minimise stroke-render pixel error."""
import itertools
import json
import sys

import numpy as np
from shapely.geometry import Polygon

import check_state as c
import stroke_render as s


def overlap_free(st):
    P = [Polygon(p) for p in c.polygons(st)]
    return all(a.intersection(b).area < 1e-3 for a, b in itertools.combinations(P, 2))


def err(st):
    return min(float(np.abs(s.render(st, 0.5, True, dx, dy) - s.TG).sum())
               for dx in np.arange(-1.5, 1.51, 0.5) for dy in np.arange(-1.5, 1.51, 0.5))


state = json.load(open(sys.argv[1]))[0] if sys.argv[1].endswith(".json") else json.loads(sys.argv[1])
cur = err(state)
print("start", round(cur, 1), json.dumps(state), flush=True)
moves = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3) if (dx, dy) != (0, 0)]
for rnd in range(6):
    improved = False
    for i in range(7):
        for dx, dy in moves:
            st = [list(p) for p in state]
            st[i][0] += dx
            st[i][1] += dy
            if overlap_free(st):
                e = err(st)
                if e < cur - 1e-6:
                    cur, state, improved = e, st, True
                    print("  1-move", round(cur, 1), json.dumps(state), flush=True)
    if not improved:
        for i, j in itertools.combinations(range(7), 2):
            for (dx, dy), (ex, ey) in itertools.product(moves, repeat=2):
                if abs(dx) + abs(dy) + abs(ex) + abs(ey) > 3:
                    continue
                st = [list(p) for p in state]
                st[i][0] += dx; st[i][1] += dy; st[j][0] += ex; st[j][1] += ey
                if overlap_free(st):
                    e = err(st)
                    if e < cur - 1e-6:
                        cur, state, improved = e, st, True
                        print("  2-move", round(cur, 1), json.dumps(state), flush=True)
    print("round", rnd, round(cur, 1), json.dumps(state), flush=True)
    if not improved:
        break
json.dump(state, open("refined.json", "w"))
