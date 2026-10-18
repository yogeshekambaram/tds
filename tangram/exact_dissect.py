"""Exact-cover search for a Tangram dissection of the measured target outline.

Coordinates are board units, relative to the target's left tip. The lowest-leftmost vertex of the
uncovered region must be a vertex of the piece covering it, so we try every piece pose there.
"""
import math

from shapely.geometry import Polygon
from shapely.ops import unary_union

import check_state as c

R = math.sqrt(2)
TARGET = Polygon([
    (0, 0), (4, 0), (4, -0.5), (4 + 2 * R, -0.5), (4 + 3 * R, -0.5 + R), (4 + 5 * R, -0.5 + R),
    (4 + 5 * R, -0.5 - R), (4 + 4 * R, -0.5), (4 + 2 * R, -0.5 - 2 * R), (4 + 2 * R, -0.5 - 4 * R),
    (4 + R, -0.5 - 3 * R), (4, -0.5 - 4 * R), (4, -4),
])
EPS = 1e-7


def poses(pid):
    seen = set()
    for o in range(8):
        for refl in (0, 1):
            pts = [(x / 16, y / 16) for x, y in (c.rotated(vx, vy, o, refl) for vx, vy in c.SHAPES[pid])]
            key = frozenset((round(x - min(p[0] for p in pts), 6), round(y - min(p[1] for p in pts), 6)) for x, y in pts)
            if key not in seen:
                seen.add(key)
                yield o, refl, pts


POSES = {pid: list(poses(pid)) for pid in c.IDS}


def anchor_vertex(region):
    pts = []
    for g in getattr(region, "geoms", [region]):
        pts += list(g.exterior.coords)
    return min(pts, key=lambda p: (round(p[1], 6), round(p[0], 6)))


def solve(region, used, placed):
    if len(used) == 7:
        return placed if region.area < 1e-5 else None
    vx, vy = anchor_vertex(region)
    kinds = set()
    for pid in c.IDS:
        kind = pid.split("-")[0]
        if pid in used or kind in kinds:
            continue
        kinds.add(kind)
        for o, refl, pts in POSES[pid]:
            for px, py in pts:
                dx, dy = vx - px, vy - py
                piece = Polygon([(x + dx, y + dy) for x, y in pts])
                if piece.difference(region).area > EPS:
                    continue
                rest = region.difference(piece).buffer(-1e-9).buffer(1e-9)
                res = solve(rest, used | {pid}, placed + [(pid, o, refl, dx, dy)])
                if res:
                    return res
    return None


if __name__ == "__main__":
    print("target area", round(TARGET.area, 6))
    sol = solve(TARGET, set(), [])
    for s in sol or []:
        print(s)
    print("solved" if sol else "no solution")
