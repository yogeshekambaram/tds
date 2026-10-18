"""Anchor fit using the target renderer model: corners rounded to whole pixels, then anti-aliased."""
import itertools
import json
import sys

import numpy as np
from PIL import Image, ImageDraw

import check_state as c
import fit_anchors as f

SS = 8


def render(state):
    img = Image.new("L", ((f.X1 - f.X0) * SS, (f.Y1 - f.Y0) * SS), 0)
    d = ImageDraw.Draw(img)
    for poly in c.polygons(state):
        d.polygon([((round(2 * x) - f.X0) * SS, (round(f.H - 2 * y) - f.Y0) * SS) for x, y in poly], fill=255)
    return np.asarray(img.resize((f.X1 - f.X0, f.Y1 - f.Y0), Image.BOX), float) / 255


def err(st):
    return float(np.abs(render(st) - f.TG).sum())


if __name__ == "__main__":
    state = json.loads(open(sys.argv[1]).read())
    cur = err(state)
    print("start", round(cur, 1), flush=True)
    for rnd in range(12):
        improved = False
        for i in range(7):
            for dx, dy in itertools.product(range(-4, 5), repeat=2):
                st = [list(p) for p in state]
                st[i][0] += dx
                st[i][1] += dy
                e2 = err(st)
                if e2 < cur - 1e-6:
                    cur, state, improved = e2, st, True
        # pairs of pieces moving together (shared edges)
        for i, j in itertools.combinations(range(7), 2):
            for dx, dy in itertools.product(range(-2, 3), repeat=2):
                st = [list(p) for p in state]
                for k in (i, j):
                    st[k][0] += dx
                    st[k][1] += dy
                e2 = err(st)
                if e2 < cur - 1e-6:
                    cur, state, improved = e2, st, True
        print("round", rnd, round(cur, 2), json.dumps(state), flush=True)
        if not improved:
            break
    json.dump(state, open("fit_round.json", "w"))
