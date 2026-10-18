"""Render candidates like the board SVG (fill + 1.5px miter stroke) and compare with the target pixels."""
import json
import sys

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon
from shapely.ops import unary_union

import check_state as c

T = 1 - np.array(Image.open(r"D:\projects\TDS\tangram\ORIGINAL.png").convert("L"), float) / 255
H, W = T.shape
Y0, Y1, X0, X1 = 300, 560, 20, 400
TG = T[Y0:Y1, X0:X1]
SS = 8


def px_polys(state, rnd):
    out = []
    for poly in c.polygons(state):
        pts = [(2 * x, H - 2 * y) for x, y in poly]
        if rnd:
            pts = [(round(x), round(y)) for x, y in pts]
        out.append(Polygon(pts))
    return out


def render(state, stroke=0.75, rnd=False, dx=0.0, dy=0.0):
    shapes = [p.buffer(stroke, join_style=2, mitre_limit=4) if stroke else p for p in px_polys(state, rnd)]
    img = Image.new("L", ((X1 - X0) * SS, (Y1 - Y0) * SS), 0)
    d = ImageDraw.Draw(img)
    for s in shapes:
        for g in getattr(s, "geoms", [s]):
            d.polygon([((x + dx - X0) * SS, (y + dy - Y0) * SS) for x, y in g.exterior.coords], fill=255)
    return np.asarray(img.resize((X1 - X0, Y1 - Y0), Image.BOX), float) / 255


def best_err(state, stroke, rnd):
    best = None
    for dx in np.arange(-1.5, 1.51, 0.5):
        for dy in np.arange(-1.5, 1.51, 0.5):
            e = float(np.abs(render(state, stroke, rnd, dx, dy) - TG).sum())
            if best is None or e < best[0]:
                best = (e, dx, dy)
    return best


if __name__ == "__main__":
    cands = json.load(open(sys.argv[1]))
    if cands and isinstance(cands[0][0], int):
        cands = [cands]
    for i, st in enumerate(cands[: int(sys.argv[2]) if len(sys.argv) > 2 else 10]):
        r = {f"stroke{s}{'_round' if rr else ''}": best_err(st, s, rr) for s in (0.75,) for rr in (False, True)}
        print(i, json.dumps(st), {k: (round(v[0], 1), v[1], v[2]) for k, v in r.items()}, flush=True)
