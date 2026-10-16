"""Render a Tangram board state (same math as adapter.js) and compare with target.png."""
import json
import math
import sys

import numpy as np
from PIL import Image, ImageDraw

R2 = math.sqrt(2)
IDS = ["large-a", "large-b", "medium", "small-a", "small-b", "square", "parallelogram"]
SHAPES = {
    "large-a": [(0, 0), (4, 0), (0, 4)],
    "large-b": [(0, 0), (4, 0), (0, 4)],
    "medium": [(-2, 0), (2, 0), (0, 2)],
    "small-a": [(0, 0), (2, 0), (0, 2)],
    "small-b": [(0, 0), (2, 0), (0, 2)],
    "square": [(0, 0), (2, 0), (2, 2), (0, 2)],
    "parallelogram": [(0, 0), (2, 0), (4, 2), (2, 2)],
}


def rotated(x, y, o, refl):
    if refl:
        x = -x
    s = 8 * R2
    return {
        0: (16 * x, 16 * y),
        1: (s * (x - y), s * (x + y)),
        2: (-16 * y, 16 * x),
        3: (-s * (x + y), s * (x - y)),
        4: (-16 * x, -16 * y),
        5: (s * (y - x), -s * (x + y)),
        6: (16 * y, -16 * x),
        7: (s * (x + y), s * (y - x)),
    }[o]


def polygons(state):
    out = []
    for pid, (x, y, o, refl) in zip(IDS, state):
        pts = [rotated(vx, vy, o, bool(refl)) for vx, vy in SHAPES[pid]]
        out.append([(x + px, y + py) for px, py in pts])  # in ticks
    return out


def render(state, dx=0.0, dy=0.0, size=(768, 576)):
    img = Image.new("1", size, 0)
    d = ImageDraw.Draw(img)
    for poly in polygons(state):
        d.polygon([(px * 2 + dx, py * 2 + dy) for px, py in poly], fill=1)  # 16 ticks = 32 px
    return np.array(img)


def best_match(state, target):
    # Translation doesn't matter: align bounding boxes, then fine-tune by a few px
    m = render(state)
    ty, tx = np.nonzero(target)
    my, mx = np.nonzero(m)
    bx, by = tx.min() - mx.min(), ty.min() - my.min()
    best = None
    for dx in range(bx - 3, bx + 4):
        for dy in range(by - 3, by + 4):
            e = (render(state, dx, dy) ^ target).sum()
            if best is None or e < best[0]:
                best = (e, dx, dy)
    return best


if __name__ == "__main__":
    target = np.array(Image.open(r"D:\projects\TDS\tangram\target.png").convert("L")) < 128
    state = json.loads(sys.argv[1])
    e, dx, dy = best_match(state, target)
    print(f"mismatch {e} px ({e / target.sum() * 100:.2f}%) at shift ({dx},{dy})")
