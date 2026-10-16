"""Model the target renderer: corners rounded to whole pixels (image y-down), then 8x8 anti-aliasing."""
import json
import sys

import numpy as np
from matplotlib.path import Path
from PIL import Image

import check_state as c

SS = 8
T = 1 - np.array(Image.open(r"D:\projects\TDS\tangram\ORIGINAL.png").convert("L"), float) / 255
ys, xs = np.nonzero(T > 0)
X0, X1, Y0, Y1 = xs.min() - 8, xs.max() + 9, ys.min() - 8, ys.max() + 9
TG = T[Y0:Y1, X0:X1]
h, w = TG.shape
sub = (np.arange(SS) + 0.5) / SS
gx = (X0 + np.arange(w)[:, None] + sub[None, :]).ravel()
gy = (Y0 + np.arange(h)[:, None] + sub[None, :]).ravel()
PX, PY = np.meshgrid(gx, gy)
PTS = np.column_stack([PX.ravel(), PY.ravel()])


def image_polys(state, flip=True, dx=0, dy=0):
    out = []
    for poly in c.polygons(state):
        if flip:
            out.append([(round(x * 2) + dx, round(576 - y * 2) + dy) for x, y in poly])
        else:
            out.append([(round(x * 2) + dx, round(y * 2) + dy) for x, y in poly])
    return out


def render(state, flip=True, dx=0, dy=0):
    acc = np.zeros(len(PTS), bool)
    for poly in image_polys(state, flip, dx, dy):
        acc |= Path(poly).contains_points(PTS)
    return acc.reshape(h, SS, w, SS).mean(axis=(1, 3))


def err(state, flip=True):
    return min(float(np.abs(render(state, flip, dx, dy) - TG).sum())
               for dx in range(-2, 3) for dy in range(-2, 3))


if __name__ == "__main__":
    st = json.loads(sys.argv[1])
    print("error (flip):", round(err(st, True), 2), " error (no flip):", round(err(st, False), 2))
