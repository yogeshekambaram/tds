"""Tolerant exact-cover search: like exact_dissect.py, but allows ~1-tick slop so the measured
(slightly imprecise, tick-jogged) target outline can be tiled. Prints every distinct dissection found.
"""
import math
import sys

from shapely.geometry import Polygon

import check_state as c
import exact_dissect as e

SLOP = 0.08      # units^2 of a piece allowed outside the uncovered region
CLEAN = 0.07     # slivers thinner than ~2*CLEAN units are dropped after each placement


def clean(region):
    return region.buffer(-CLEAN, join_style=2).buffer(CLEAN, join_style=2)


def vertices(region):
    pts = []
    for g in getattr(region, "geoms", [region]):
        if g.is_empty:
            continue
        pts += list(g.simplify(0.03).exterior.coords)
    return pts


def solve(region, used, placed, out):
    if len(used) == 7:
        if region.area < 0.3:
            out.append(placed)
        return
    pts = vertices(region)
    if not pts:
        return
    vx, vy = min(pts, key=lambda p: (round(p[1], 1), p[0]))
    kinds = set()
    for pid in c.IDS:
        kind = pid.split("-")[0]
        if pid in used or kind in kinds:
            continue
        kinds.add(kind)
        for o, refl, ppts in e.POSES[pid]:
            for px, py in ppts:
                dx, dy = vx - px, vy - py
                piece = Polygon([(x + dx, y + dy) for x, y in ppts])
                if piece.difference(region).area > SLOP:
                    continue
                solve(clean(region.difference(piece)), used | {pid}, placed + [(pid, o, refl, dx, dy)], out)
                if len(out) >= 20:
                    return


if __name__ == "__main__":
    measured = Polygon(e.TARGET.exterior.coords)
    found = []
    solve(measured, set(), [], found)
    print(len(found), "dissections")
    seen = set()
    for sol in found:
        key = tuple(sorted((p, o, r) for p, o, r, _, _ in sol))
        if key in seen:
            continue
        seen.add(key)
        print("---")
        for s in sorted(sol):
            print("  %-13s o=%d refl=%d  at (%.3f, %.3f)" % s)
    sys.exit(0 if found else 1)
