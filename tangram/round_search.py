"""Enumerate floor/ceil roundings of the ideal dissection's anchors; keep valid ones; score vs measured lines."""
import itertools
import json
import math

import line_fit as lf

IDEAL = {  # pid: (orientation, reflected, x_ticks, y_ticks) relative to large-b fixed at (80,116)
    "large-a": (1, 1, 16 + 6.828427 * 16, 116 - 3.328427 * 16),
    "large-b": (2, 1, 80, 116),
    "medium": (7, 0, 16 + 9.656854 * 16, 116 - 0.5 * 16),
    "small-a": (5, 1, 16 + 5.414214 * 16, 116 - 4.742641 * 16),
    "small-b": (1, 0, 16 + 6.828427 * 16, 116 - 3.328427 * 16),
    "square": (1, 0, 16 + 8.242641 * 16, 116 - 1.914214 * 16),
    "parallelogram": (3, 0, 16 + 8.242641 * 16, 116 - 1.914214 * 16),
}
IDS = ["large-a", "large-b", "medium", "small-a", "small-b", "square", "parallelogram"]
choices = []
for pid in IDS:
    o, r, x, y = IDEAL[pid]
    xs = sorted({math.floor(x), math.ceil(x)})
    ys = sorted({math.floor(y), math.ceil(y)})
    choices.append([[ax, ay, o, r] for ax in xs for ay in ys])
results = []
for combo in itertools.product(*choices):
    st = [list(p) for p in combo]
    s, g = lf.score(st)
    if s < 1e8:
        results.append((s, g, st))
results.sort(key=lambda t: t[0])
print(len(results), "valid roundings")
for s, g, st in results[:8]:
    print(round(s, 4), g, json.dumps(st))
json.dump([st for _, _, st in results[:20]], open("round_candidates.json", "w"))
