"""Fit integer tick anchors for a known dissection by matching target.png pixel-for-pixel."""
import itertools
import json
import sys

import numpy as np
from PIL import Image, ImageDraw

import check_state as c

SS = 4
T = 1 - np.array(Image.open(r"D:\projects\TDS\tangram\ORIGINAL.png").convert("L"), float) / 255
H, W = T.shape
Y0, Y1, X0, X1 = 300, 560, 20, 400
TG = T[Y0:Y1, X0:X1]


def render(state, grow=0.0):
    img = Image.new("L", ((X1 - X0) * SS, (Y1 - Y0) * SS), 0)
    d = ImageDraw.Draw(img)
    for poly in c.polygons(state):
        pts = [((2 * x - X0) * SS, (H - 2 * y - Y0) * SS) for x, y in poly]
        d.polygon(pts, fill=255)
        if grow:
            d.line(pts + pts[:1], fill=255, width=max(1, int(round(2 * grow * SS))))
    return np.asarray(img.resize((X1 - X0, Y1 - Y0), Image.BOX), float) / 255


def err(state, grow):
    return float(np.abs(render(state, grow) - TG).sum())


if __name__ == "__main__":
    state = json.loads(sys.argv[1])
    grow = min((err(state, g), g) for g in (0, 0.25, 0.5, 0.75, 1.0))[1]
    cur = err(state, grow)
    print("grow", grow, "initial", round(cur, 1), flush=True)
    for rnd in range(10):
        improved = False
        for i in range(7):
            for dx, dy in itertools.product(range(-3, 4), repeat=2):
                st = [list(p) for p in state]
                st[i][0] += dx
                st[i][1] += dy
                e2 = err(st, grow)
                if e2 < cur - 1e-6:
                    cur, state, improved = e2, st, True
        print("round", rnd, round(cur, 2), json.dumps(state), flush=True)
        if not improved:
            break
    json.dump(state, open("fit_state.json", "w"))
