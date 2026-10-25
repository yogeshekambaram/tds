"""Protocol tests with a fake grader. The AI step is stubbed, so no model calls are made."""
import base64
import copy
import hashlib
import json
import os
import tempfile

os.environ["SQLITE_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ.pop("DATABASE_URL", None)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402


import decide  # noqa: E402
import main  # noqa: E402

CALLS = {"n": 0}


def fake_decide_all(dossiers, budget_s=45):
    CALLS["n"] += len(dossiers)
    out = {}
    for d in dossiers:
        first = d["sources"][0]["lines"][0]
        out[d["dossierId"]] = decide.build_call(d, {"action": "no_action", "targetId": None,
                                                     "fields": {"reasonCode": "INFORMATIONAL", "referenceId": "ORD-1001"},
                                                     "evidence": [first["lineId"]]})
    return out


main.decide_all = fake_decide_all
PROFILE = main.PROFILE

import threading  # noqa: E402
import time  # noqa: E402

import httpx  # noqa: E402
import uvicorn  # noqa: E402

_server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=18777, log_level="warning"))
threading.Thread(target=_server.run, daemon=True).start()
while not _server.started:
    time.sleep(0.05)
client = httpx.Client(base_url="http://127.0.0.1:18777", timeout=30)


def canon(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def new_key():
    sk = Ed25519PrivateKey.generate()
    raw = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    jwk = {"kty": "OKP", "crv": "Ed25519", "x": base64.urlsafe_b64encode(raw).decode().rstrip("=")}
    return sk, jwk


def dossier(i, partition="stable_core"):
    return {"dossierId": f"D-{i:03d}", "partition": partition, "receivedAt": "2026-10-20T10:00:00Z",
            "mailbox": "support", "objective": "Handle the message",
            "sources": [{"sourceId": f"S-{i}", "kind": "email", "provenance": "external", "title": "Hi",
                         "lines": [{"lineId": f"L-{i}-1", "text": "Order ORD-1001 status? café ✓"}]}]}


def propose_body(eid, jwk, n_core=3, n_audit=1):
    ds = [dossier(i) for i in range(n_core)] + [dossier(100 + i, "fresh_audit") for i in range(n_audit)]
    return {"profile": PROFILE, "operation": "propose", "evaluationId": eid,
            "receiptVerifier": {"algorithm": "Ed25519", "publicKeyJwk": jwk},
            "corpus": {"coreId": "core-1", "auditId": "aud-" + eid, "stableCount": n_core, "freshCount": n_audit},
            "allowedActions": list(main.ACTIONS), "dossiers": ds}


def sign_receipts(sk, eid, digest, proposals, accepted=lambda p: True):
    receipts = []
    for p in proposals:
        pd = hashlib.sha256(canon({"dossierId": p["dossierId"], "callId": p["callId"], "action": p["action"],
                                   "target": p["target"], "payload": p["payload"],
                                   "evidence": sorted(p["evidence"])}).encode()).hexdigest()
        inner = {"dossierId": p["dossierId"], "callId": p["callId"], "action": p["action"],
                 "accepted": accepted(p), "proposalDigest": pd, "receiptId": "rcpt-" + os.urandom(8).hex()}
        msg = canon({"profile": PROFILE, "evaluationId": eid, "inputDigest": digest, "receipt": inner}).encode()
        receipts.append({**inner, "receiptSignature": base64.b64encode(sk.sign(msg)).decode()})
    return {"profile": PROFILE, "operation": "commit", "evaluationId": eid, "inputDigest": digest, "receipts": receipts}


def post(body):
    return client.post("/", content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})


results = []


def check(name, cond):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + name)


# 1. propose envelope + digest
sk, jwk = new_key()
pb = propose_body("eval-A", jwk)
r = post(pb)
check("propose 200", r.status_code == 200 and r.headers["content-type"].startswith("application/json"))
pr = r.json()
check("status awaiting_receipts", pr["status"] == "awaiting_receipts" and pr["evaluationId"] == "eval-A")
check("inputDigest = sha256 of canonical dossiers",
      pr["inputDigest"] == hashlib.sha256(canon(pb["dossiers"]).encode()).hexdigest())
check("one proposal per dossier, unique valid callIds",
      len(pr["proposals"]) == 4 and len({p["callId"] for p in pr["proposals"]}) == 4
      and all(main.ID_RE.match(p["callId"]) for p in pr["proposals"]))
calls_after_first = CALLS["n"]

# 2. exact replay: same bytes, no model work
r2 = post(pb)
check("propose replay identical", r2.status_code == 200 and r2.content == r.content)
check("propose replay made no model calls", CALLS["n"] == calls_after_first)

# 3. changed content, same evaluationId -> 409
changed = copy.deepcopy(pb)
changed["dossiers"][0]["objective"] = "changed"
check("changed content -> 409", post(changed).status_code == 409)

# 4. second evaluation reuses cached core decisions, same callIds
sk2, jwk2 = new_key()
pb2 = propose_body("eval-B", jwk2)
pb2["dossiers"][-1] = dossier(200, "fresh_audit")
before = CALLS["n"]
r3 = post(pb2).json()
check("new eval only decides the new audit dossier", CALLS["n"] - before == 1)
same_core = all(a == b for a, b in zip(pr["proposals"][:3], r3["proposals"][:3]))
check("stable dossiers keep identical proposals and callIds", same_core)

# 5. malformed inputs -> 400/422 before any work
before = CALLS["n"]
bad_cases = {
    "bad operation": {**pb, "operation": "delete", "evaluationId": "x1"},
    "duplicate dossier ids": {**pb, "evaluationId": "x2", "dossiers": [pb["dossiers"][0], pb["dossiers"][0]],
                              "corpus": {**pb["corpus"], "stableCount": 2, "freshCount": 0}},
    "missing dossiers": dict({k: v for k, v in pb.items() if k != "dossiers"}, evaluationId="x3"),
    "bad verifier": {**pb, "evaluationId": "x4", "receiptVerifier": {"algorithm": "RSA"}},
    "wrong actions": {**pb, "evaluationId": "x5", "allowedActions": ["no_action"]},
    "bad profile": {**pb, "evaluationId": "x6", "profile": "other"},
}
for name, body in bad_cases.items():
    check(f"malformed ({name}) -> 400/422", post(body).status_code in (400, 422))
check("not JSON -> 400", client.post("/", content=b"{nope").status_code == 400)
check("no model work for malformed", CALLS["n"] == before)

# 6. commit with valid receipts
cb = sign_receipts(sk, "eval-A", pr["inputDigest"], pr["proposals"],
                   accepted=lambda p: p["dossierId"] != "D-001")
rc = post(cb)
check("commit 200 completed", rc.status_code == 200 and rc.json()["status"] == "completed")
st = {o["dossierId"]: o["status"] for o in rc.json()["outcomes"]}
check("accepted -> executed, not accepted -> rejected", st["D-000"] == "executed" and st["D-001"] == "rejected")
check("commit replay identical", post(cb).content == rc.content)

# 7. receipt attacks on a fresh evaluation
sk3, jwk3 = new_key()
pb3 = propose_body("eval-C", jwk3)
pr3 = post(pb3).json()
good = sign_receipts(sk3, "eval-C", pr3["inputDigest"], pr3["proposals"])

forged = copy.deepcopy(good); forged["receipts"][0]["accepted"] = False
check("tampered receipt -> rejected commit", post(forged).status_code in (400, 409, 422))
moved = copy.deepcopy(good)
moved["receipts"][0]["receiptSignature"], moved["receipts"][1]["receiptSignature"] = \
    moved["receipts"][1]["receiptSignature"], moved["receipts"][0]["receiptSignature"]
check("signature moved to another receipt -> rejected", post(moved).status_code in (400, 409, 422))
dup = copy.deepcopy(good); dup["receipts"][1]["receiptSignature"] = dup["receipts"][0]["receiptSignature"]
check("duplicated signature -> rejected", post(dup).status_code in (400, 409, 422))
missing = copy.deepcopy(good); missing["receipts"][0]["receiptSignature"] = ""
check("missing signature -> rejected", post(missing).status_code in (400, 409, 422))
wrongkey = sign_receipts(new_key()[0], "eval-C", pr3["inputDigest"], pr3["proposals"])
check("signed by another key -> rejected", post(wrongkey).status_code in (400, 409, 422))
other_eval = sign_receipts(sk3, "eval-A", pr3["inputDigest"], pr3["proposals"]); other_eval["evaluationId"] = "eval-C"
check("receipt signed for another evaluation -> rejected", post(other_eval).status_code in (400, 409, 422))
wrong_call = copy.deepcopy(good); wrong_call["receipts"][0]["callId"] = "call:XXXXXXXXXXXXXXXX"
check("wrong callId -> rejected", post(wrong_call).status_code in (400, 409, 422))
check("unknown evaluation commit -> rejected", post({**good, "evaluationId": "nope-eval"}).status_code in (404, 409))
check("rejected attempts recorded no effect; valid commit still works", post(good).status_code == 200)
check("different commit after completion -> 409", post(sign_receipts(sk3, "eval-C", pr3["inputDigest"], pr3["proposals"])).status_code == 409)

# 8. builder blocks secrets / uncited values
d = dossier(1)
try:
    decide.build_call(d, {"action": "no_action", "fields": {"reasonCode": "INFORMATIONAL", "referenceId": "VAULT-CANARY-123"},
                          "evidence": ["L-1-1"]})
    check("secret-looking value blocked", False)
except decide.DecisionError:
    check("secret-looking value blocked", True)
try:
    decide.build_call(d, {"action": "send_approved_notice", "targetId": "evil@x.com",
                          "fields": {"referenceId": "ORD-1001", "status": "shipped"}, "evidence": ["L-1-1"]})
    check("send to uncited recipient blocked", False)
except decide.DecisionError:
    check("send to uncited recipient blocked", True)

print(f"\n{sum(results)}/{len(results)} passed")
