"""Re-decide every logged grader dossier with the current logic and compare with the grader's verdicts."""
import concurrent.futures as cf
import json
import sys

import mailroom_ai

sys.stdout.reconfigure(encoding="utf-8")
rows = [json.loads(l) for l in open("requests.jsonl", encoding="utf-8")]


def P(s):
    try:
        return json.loads(s)
    except Exception:
        return None


props, acc, dossiers = {}, {}, {}
for r in rows:
    b = P(r["body"]) or {}
    if not str(b.get("evaluationId", "")).startswith("eval_") or r["status"] != 200:
        continue
    if b.get("operation") == "propose":
        for d in b["dossiers"]:
            dossiers[d["dossierId"]] = d
        for p in P(r["response"])["proposals"]:
            props[p["dossierId"]] = p
    elif b.get("operation") == "commit":
        for x in b["receipts"]:
            acc[x["dossierId"]] = x["accepted"]

with cf.ThreadPoolExecutor(16) as ex:
    new = dict(zip(dossiers, ex.map(mailroom_ai.decide, dossiers.values())))

same_ok, regress, changed = 0, [], []
for did, n in new.items():
    o = props[did]
    core_same = (o["action"], o["target"], o["payload"]) == (n["action"], n["target"], n["payload"])
    if acc[did]:
        if core_same:
            same_ok += 1
        else:
            regress.append(did)
    else:
        changed.append(did)
    tag = "ACC" if acc[did] else "REJ"
    print(f"{tag} {dossiers[did]['partition'][:6]} {did} {o['action']:22s} -> {n['action']:22s} "
          f"{'' if core_same else 'ARGS/ACTION CHANGED'} ev {len(o['evidence'])}->{len(n['evidence'])}")
    if not acc[did] or not core_same:
        print("      new:", json.dumps({k: n[k] for k in ("target", "payload", "evidence")}))
print(f"\naccepted kept identical: {same_ok}; regressions: {regress}; previously rejected re-decided: {len(changed)}")
json.dump(new, open("offline_new.json", "w"), indent=1)
