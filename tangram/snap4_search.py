"""Exhaustive-ish search with anchors on the 4-tick human snap grid (board y-up frame)."""
import itertools, json, sys
sys.argv = ['x', sys.argv[1], '/dev/null', sys.argv[2]]
exec(open('exact_fit.py').read().split('s = json.loads')[0])
s = json.load(open(sys.argv[1]))
start = [[4 * round(x / 4), 4 * round(y / 4), o, r] for x, y, o, r in s]

def options(pid, pose):
    out = []
    for v in variants(pid, pose) + [pose]:
        bx, by = 4 * round(v[0] / 4), 4 * round(v[1] / 4)
        for dx in (-4, 0, 4):
            for dy in (-4, 0, 4):
                out.append([bx + dx, by + dy, v[2], v[3]])
    uniq = {json.dumps(o): o for o in out}
    return list(uniq.values())

def shifted_score(st):
    return min(mismatch(st, sx, sy) for sx in (0, 0.5, 1, 1.5) for sy in (0, 0.5, 1, 1.5))

cost = lambda st: shifted_score(st) + 100000 * n_overlaps(st)
cur, s = cost(start), start
print('start', cur, json.dumps(s), flush=True)
for rnd in range(10):
    improved = False
    for i, pid in enumerate(c.IDS):
        best = (cur, s)
        for v in options(pid, s[i]):
            t = [p[:] for p in s]; t[i] = v
            sc = cost(t)
            if sc < best[0]:
                best = (sc, t)
        if best[0] < cur:
            cur, s, improved = best[0], best[1], True
    if not improved:
        st = [(dx, dy) for dx in (-4, 0, 4) for dy in (-4, 0, 4)]
        best = (cur, s)
        for i, j in itertools.combinations(range(7), 2):
            for (a, b), (e, f) in itertools.product(st, st):
                t = [p[:] for p in s]; t[i][0] += a; t[i][1] += b; t[j][0] += e; t[j][1] += f
                sc = cost(t)
                if sc < best[0]:
                    best = (sc, t)
        if best[0] < cur:
            cur, s, improved = best[0], best[1], True
    print('round', rnd, cur, 'overlaps', n_overlaps(s), json.dumps(s), flush=True)
    if not improved:
        break
open('snap4_best.json', 'w').write(json.dumps(s))
