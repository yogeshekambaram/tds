"""Protocol tests against a running server (MOCK_LLM=1). Usage: python test_protocol.py http://127.0.0.1:8071"""
import concurrent.futures as cf
import json
import sys
import uuid

import httpx

BASE = sys.argv[1].rstrip("/")
A = BASE + "/a2a/"
H = {"Authorization": "Bearer owner-token-1", "A2A-Version": "1.0", "Content-Type": "application/a2a+json"}
OUT = {"Authorization": "Bearer outsider-token-2", "A2A-Version": "1.0", "Content-Type": "application/a2a+json"}
fails = []


def check(name, cond, info=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -> {info}"))
    if not cond:
        fails.append(name)


def batch_msg(mid=None, n=3):
    pkgs = [{"packageId": f"pkg-{i}", "documents": [{"title": "Cover", "text": f"Cover sheet [COV-{i}]"},
            {"text": f"Invoice INV-{i} from Acme. Payment hold pending bank callback [EV-{i}-A] [EV-{i}-B] [EV-{i}-C]."}]}
            for i in range(n)]
    return {"messageId": mid or str(uuid.uuid4()), "role": "ROLE_USER",
            "parts": [{"mediaType": "application/vnd.ga5.invoice-claim-batch+json",
                       "data": {"batchId": "b-" + uuid.uuid4().hex[:6], "policyRevision": "r1", "packages": pkgs}}]}


def send(msg, headers=H, ri=False):
    return httpx.post(A + "message:send", headers=headers, timeout=60,
                      json={"message": msg, "configuration": {"returnImmediately": ri, "historyLength": 20,
                            "acceptedOutputModes": ["application/vnd.ga5.invoice-action-proposals+json",
                                                    "application/vnd.ga5.invoice-action-receipts+json"]}})


def results_msg(task, outcomes=None, **over):
    props = task["artifacts"][0]["parts"][0]["data"]
    res = [{"packageId": p["packageId"], "actionId": p["actionId"], "action": p["action"],
            "outcome": (outcomes or {}).get(p["packageId"], "ACCEPTED"), "receiptNonce": "nonce-" + uuid.uuid4().hex}
           for p in props["proposals"]]
    m = {"messageId": str(uuid.uuid4()), "taskId": task["id"], "contextId": task["contextId"], "role": "ROLE_USER",
         "parts": [{"mediaType": "application/vnd.ga5.invoice-action-results+json",
                    "data": {"batchId": props["batchId"], "results": res}}]}
    m.update(over)
    return m


# card
r = httpx.get(BASE + "/.well-known/agent-card.json")
card = r.json()
check("card 200 json", r.status_code == 200 and "json" in r.headers["content-type"])
check("card interface url", card["supportedInterfaces"][0]["url"].endswith("/a2a/"), card["supportedInterfaces"])
check("card skill", card["skills"][0]["id"] == "invoice_action_agent")
# auth / version / media
check("no auth 401", httpx.get(A + "tasks", headers={"A2A-Version": "1.0"}).status_code in (401, 403))
check("bad scheme 401", httpx.get(A + "tasks", headers={"Authorization": "Basic x", "A2A-Version": "1.0"}).status_code in (401, 403))
check("bad version 400", httpx.get(A + "tasks", headers={**H, "A2A-Version": "0.3"}).status_code == 400)
check("missing version 400", httpx.get(A + "tasks", headers={"Authorization": H["Authorization"]}).status_code == 400)
check("bad media 415", httpx.post(A + "message:send", headers={**H, "Content-Type": "application/json"}, json={}).status_code in (400, 415))
# create
msg = batch_msg()
r = send(msg)
t = r.json()["task"]
check("send 200 a2a media", r.status_code == 200 and r.headers["content-type"].startswith("application/a2a+json"))
check("input required", t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED")
check("history has initial", t["history"][0]["messageId"] == msg["messageId"])
check("one proposals artifact", len(t["artifacts"]) == 1)
props = t["artifacts"][0]["parts"][0]["data"]["proposals"]
check("3 refs, no cover", all(p["evidenceRefs"] == [f"EV-{i}-A", f"EV-{i}-B", f"EV-{i}-C"] for i, p in enumerate(props)),
      [p["evidenceRefs"] for p in props])
check("actionId len", all(len(p["actionId"]) >= 12 for p in props))
check("rationale len", all(60 <= len(p["rationale"]) <= 1500 for p in props))
# idempotency: reordered keys + changed returnImmediately
reordered = json.loads(json.dumps(msg, sort_keys=True))
r2 = send(dict(reversed(list(reordered.items()))), ri=True)
check("replay same task", r2.status_code == 200 and r2.json()["task"] == t, r2.text[:200])
changed = json.loads(json.dumps(msg)); changed["parts"][0]["data"]["policyRevision"] = "r2"
r3 = send(changed)
check("conflict 409", r3.status_code == 409 and "IDEMPOTENCY_CONFLICT" in r3.text, r3.text[:200])
# concurrency
m2 = batch_msg()
with cf.ThreadPoolExecutor(6) as ex:
    rs = list(ex.map(lambda _: send(m2), range(6)))
ids = {x.json()["task"]["id"] for x in rs if x.status_code == 200}
check("concurrent dedupe", len(ids) == 1 and all(x.status_code == 200 for x in rs), [x.status_code for x in rs])
# isolation
check("outsider get 404", httpx.get(A + "tasks/" + t["id"], headers=OUT).status_code in (403, 404))
check("outsider list empty", httpx.get(A + "tasks", headers=OUT).json()["tasks"] == [])
ro = httpx.post(A + f"tasks/{t['id']}:cancel", headers=OUT)
check("outsider cancel 404, no echo", ro.status_code in (403, 404) and t["id"] not in ro.text, ro.text)
rc = send(results_msg(t), headers=OUT)
check("outsider continue rejected", rc.status_code in (403, 404) and t["id"] not in rc.text, rc.text)
check("owner list has task", t["id"] in [x["id"] for x in httpx.get(A + "tasks", headers=H).json()["tasks"]])
# mismatched continuations
check("wrong context", send(results_msg(t, contextId="nope")).status_code == 400)
bad = results_msg(t); bad["parts"][0]["data"]["results"][0]["action"] = "settle_invoice"
check("wrong action", send(bad).status_code == 400)
bad = results_msg(t); bad["parts"][0]["data"]["results"][0]["actionId"] = "act-wrong-000000"
check("wrong actionId", send(bad).status_code == 400)
bad = results_msg(t); bad["parts"][0]["data"]["batchId"] = "other"
check("wrong batch", send(bad).status_code == 400)
check("still input required", httpx.get(A + "tasks/" + t["id"], headers=H).json()["status"]["state"] == "TASK_STATE_INPUT_REQUIRED")
# complete with one rejected
rm = results_msg(t, {"pkg-1": "REJECTED"})
rf = send(rm)
ft = rf.json()["task"]
ex = ft["artifacts"][1]["parts"][0]["data"]["executions"]
check("completed", ft["status"]["state"] == "TASK_STATE_COMPLETED")
check("executions accepted only", [e["packageId"] for e in ex] == ["pkg-0", "pkg-2"], ex)
check("nonce bound", ex[0]["receiptNonce"] == rm["parts"][0]["data"]["results"][0]["receiptNonce"])
check("history 2", len(ft["history"]) == 2)
check("result replay same", send(rm).json()["task"] == ft)
check("terminal cancel 409", httpx.post(A + f"tasks/{t['id']}:cancel", headers=H).status_code == 409)
check("new result after terminal 409", send(results_msg(t)).status_code == 409)
check("terminal unchanged", httpx.get(A + "tasks/" + t["id"], headers=H).json() == ft)
# cancel vs receipt race
for _ in range(5):
    tt = send(batch_msg()).json()["task"]
    rmsg = results_msg(tt)
    with cf.ThreadPoolExecutor(2) as ex2:
        f1 = ex2.submit(lambda: send(rmsg))
        f2 = ex2.submit(lambda: httpx.post(A + f"tasks/{tt['id']}:cancel", headers=H))
        codes = sorted([f1.result().status_code, f2.result().status_code])
    final = httpx.get(A + "tasks/" + tt["id"], headers=H).json()
    ok = codes == [200, 409] and ((final["status"]["state"] == "TASK_STATE_COMPLETED" and len(final["artifacts"]) == 2) or
                                  (final["status"]["state"] == "TASK_STATE_CANCELED" and len(final["artifacts"]) == 1))
    check("race exactly one wins", ok, (codes, final["status"]["state"], len(final["artifacts"])))
print("\nFAILED:", fails if fails else "none")
