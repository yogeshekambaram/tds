"""Compare every outline edge of a candidate state with the target image, in ticks.

For each boundary edge of the union, sample the image along the outward normal at a few points
on the edge and find where coverage crosses 0.5. Reports (measured - candidate) offset per edge.
"""
import json
import math
import sys

import numpy as np
from PIL import Image
from shapely.geometry import Polygon
from shapely.ops import unary_union

import check_state as c

T = 1 - np.array(Image.open(r"D:\projects\TDS\tangram\ORIGINAL.png").convert("L"), float) / 255
H, W = T.shape


def sample(px, py):
    x0, y0 = int(math.floor(px)), int(math.floor(py))
    fx, fy = px - x0, py - y0
    v = T[y0:y0 + 2, x0:x0 + 2]
    return (v[0, 0] * (1 - fx) * (1 - fy) + v[0, 1] * fx * (1 - fy) + v[1, 0] * (1 - fx) * fy + v[1, 1] * fx * fy)


def to_px(x, y, off):
    return 2 * x + off[0], H - 2 * y - off[1]


def edge_offsets(state, off=(1.0, 1.0)):
    union = unary_union([Polygon(p) for p in c.polygons(state)]).buffer(0.6, join_style=2).buffer(-0.6, join_style=2).simplify(0.05)
    coords = list(union.exterior.coords)
    if not union.exterior.is_ccw:
        coords = coords[::-1]
    rows = []
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        if L < 3:
            rows.append(((x1, y1), (x2, y2), L, None))
            continue
        nx, ny = (y2 - y1) / L, -(x2 - x1) / L  # outward normal for a CCW polygon (board frame)
        found = []
        for t in (0.3, 0.5, 0.7):
            mx, my = x1 + t * (x2 - x1), y1 + t * (y2 - y1)
            # walk from 3 ticks inside to 3 ticks outside; find the 0.5 crossing
            prev = None
            for k in np.linspace(-3, 3, 241):
                px, py = to_px(mx + k * nx, my + k * ny, off)
                v = sample(px, py)
                if prev is not None and prev[1] >= 0.5 > v:
                    found.append(prev[0] + (prev[1] - 0.5) / (prev[1] - v) * (k - prev[0]))
                    break
                prev = (k, v)
        rows.append(((x1, y1), (x2, y2), L, float(np.median(found)) if found else None))
    return rows


if __name__ == "__main__":
    state = json.loads(sys.argv[1]) if sys.argv[1].startswith("[") else json.load(open(sys.argv[1]))
    for a, b, L, d in edge_offsets(state):
        flag = "" if d is None else ("   <-- off" if abs(d) > 0.45 else "")
        print(f"({a[0]:7.2f},{a[1]:7.2f}) -> ({b[0]:7.2f},{b[1]:7.2f})  len {L:6.2f}  offset {'   n/a' if d is None else f'{d:+.2f}'} ticks{flag}")
