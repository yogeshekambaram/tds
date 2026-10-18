import json, itertools, numpy as np
from shapely.geometry import Polygon
import check_state as c, stroke_render as s
base = json.load(open("variant_candidates.json"))[0]
IDX = {p: i for i, p in enumerate(c.IDS)}
polys0 = c.polygons(base)
def near(poly, ref, tol):
    return max(min(np.hypot(px - qx, py - qy) for qx, qy in ref) for px, py in poly) <= tol
def opts(pid, tol, allow_variants):
    i = IDX[pid]; o0, r0 = base[i][2], base[i][3]; out = []
    for o in range(8):
        for r in (0, 1):
            if not allow_variants and (o, r) != (o0, r0): continue
            for ax in range(base[i][0] - 3, base[i][0] + 4):
                for ay in range(base[i][1] - 3, base[i][1] + 4):
                    st = [list(p) for p in base]; st[i] = [ax, ay, o, r]
                    poly = c.polygons(st)[i]
                    if len(poly) == len(polys0[i]) and near(poly, polys0[i], tol):
                        out.append([ax, ay, o, r])
    # also anchor-variant placements far from base anchor (parallelogram other acute corner)
    return out
def par_opts():
    i = IDX["parallelogram"]; out = []
    for o in range(8):
        for r in (0, 1):
            for ax in range(70, 160):
                for ay in range(75, 120):
                    st = [list(p) for p in base]; st[i] = [ax, ay, o, r]
                    poly = c.polygons(st)[i]
                    if near(poly, polys0[i], 1.5): out.append([ax, ay, o, r])
    return out
O = {"parallelogram": par_opts(), "small-b": opts("small-b", 1.5, False), "large-a": opts("large-a", 1.5, False),
     "square": opts("square", 1.5, True)}
print({k: len(v) for k, v in O.items()}, flush=True)
def ok(st):
    P = [Polygon(p) for p in c.polygons(st)]
    return all(a.intersection(b).area < 1e-3 for a, b in itertools.combinations(P, 2))
valid = []
for combo in itertools.product(*O.values()):
    st = [list(p) for p in base]
    for pid, pose in zip(O, combo): st[IDX[pid]] = list(pose)
    if ok(st): valid.append(st)
print(len(valid), "valid joint placements", flush=True)
def err(st):
    return min(float(np.abs(s.render(st, 0.5, True, dx, dy) - s.TG).sum()) for dx in np.arange(-1, 1.01, 0.5) for dy in np.arange(-1, 1.01, 0.5))
scored = sorted(((err(st), st) for st in valid), key=lambda t: t[0])
for e, st in scored[:8]: print(round(e, 1), json.dumps(st), flush=True)
json.dump([st for _, st in scored[:10]], open("joint_best.json", "w"))
