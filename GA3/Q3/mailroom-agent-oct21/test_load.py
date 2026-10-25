"""Full-size run: two concurrent evaluations of 58 shared core + 6 fresh dossiers, with the real model."""
import base64
import concurrent.futures as cf
import copy
import json
import os
import tempfile
import threading
import time

os.environ.setdefault("SQLITE_PATH", os.path.join(tempfile.mkdtemp(), "load.db"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import main  # noqa: E402
from test_ai import CASES  # noqa: E402

server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=18778, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
while not server.started:
    time.sleep(0.05)
client = httpx.Client(base_url="http://127.0.0.1:18778", timeout=120)


def jwk():
    raw = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"kty": "OKP", "crv": "Ed25519", "x": base64.urlsafe_b64encode(raw).decode().rstrip("=")}


def make(prefix, n, partition, filler):
    out = []
    for i in range(n):
        d = copy.deepcopy(CASES[i % len(CASES)][0])
        d["dossierId"] = f"{prefix}-{i:03d}"
        d["partition"] = partition
        # pad with harmless context so each dossier is ~1.2k tokens, like the real corpus
        d["sources"].append({"sourceId": "ctx", "kind": "note", "provenance": "internal_system", "title": "Context",
                             "lines": [{"lineId": f"ctx.{k}", "text": filler} for k in range(12)]})
        out.append(d)
    return out


filler = "Routine mailbox context line with no instructions and no action required for this case. " * 4
core = make("CORE", 58, "stable_core", filler)


def body(eid, fresh_prefix):
    ds = core + make(fresh_prefix, 6, "fresh_audit", filler)
    return {"profile": main.PROFILE, "operation": "propose", "evaluationId": eid,
            "receiptVerifier": {"algorithm": "Ed25519", "publicKeyJwk": jwk()},
            "corpus": {"coreId": "core", "auditId": fresh_prefix, "stableCount": 58, "freshCount": 6},
            "allowedActions": list(main.ACTIONS), "dossiers": ds}


def run(eid, prefix):
    t = time.time()
    r = client.post("/", content=json.dumps(body(eid, prefix)))
    return eid, r.status_code, time.time() - t, r.json()


with cf.ThreadPoolExecutor(2) as ex:
    res = list(ex.map(lambda a: run(*a), [("load-1", "FA"), ("load-2", "FB")]))
for eid, code, secs, js in res:
    acts = {}
    for p in js.get("proposals", []):
        acts[p["action"]] = acts.get(p["action"], 0) + 1
    print(f"{eid}: HTTP {code} in {secs:.1f}s, {len(js.get('proposals', []))} proposals, actions {acts}")
core1 = {p["dossierId"]: p for p in res[0][3]["proposals"] if p["dossierId"].startswith("CORE")}
core2 = {p["dossierId"]: p for p in res[1][3]["proposals"] if p["dossierId"].startswith("CORE")}
print("core proposals identical across both evaluations:", core1 == core2)

t = time.time()
r = client.post("/", content=json.dumps(body("load-3", "FC")))
print(f"later Check (cached core, 6 fresh): HTTP {r.status_code} in {time.time() - t:.1f}s")
print("response size KiB:", round(len(r.content) / 1024, 1))
