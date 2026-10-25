"""Mailroom action gate (profile ga5-mailroom-action-gate/v2).

POST / with operation "propose" or "commit". The model only suggests decisions; code validates,
builds the frozen tool calls, persists state and verifies Ed25519 receipts.
"""
import base64
import hashlib
import json
import re
import threading
from concurrent.futures import Future

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from decide import ACTIONS, decide_all
from store import Store

PROFILE = "ga5-mailroom-action-gate/v2"
MAX_BODY = 6 * 1024 * 1024
MAX_RESPONSE = 512 * 1024
ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{12,128}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

app = FastAPI()
store = Store()
eval_locks, eval_locks_guard = {}, threading.Lock()
inflight, inflight_guard = {}, threading.Lock()


class Bad(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def reply(status, body):
    return JSONResponse(body, status_code=status, media_type="application/json")


def err(status, message):
    return reply(status, {"profile": PROFILE, "error": message})


def lock_for(evaluation_id):
    with eval_locks_guard:
        return eval_locks.setdefault(evaluation_id, threading.Lock())


# ---------- validation (all before any AI or tool work) ----------

def req_str(obj, key, where, max_len=4096, allow_empty=False):
    v = obj.get(key)
    if not isinstance(v, str) or (not allow_empty and not v) or len(v) > max_len:
        raise Bad(422, f"{where}.{key} must be a non-empty string")
    return v


def validate_propose(body):
    req_str(body, "evaluationId", "request", 256)
    rv = body.get("receiptVerifier")
    if not isinstance(rv, dict) or rv.get("algorithm") != "Ed25519":
        raise Bad(422, "receiptVerifier.algorithm must be Ed25519")
    jwk = rv.get("publicKeyJwk")
    if not isinstance(jwk, dict) or jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or not isinstance(jwk.get("x"), str):
        raise Bad(422, "receiptVerifier.publicKeyJwk must be an OKP Ed25519 JWK")
    try:
        public_key(jwk)
    except Exception:
        raise Bad(422, "receiptVerifier.publicKeyJwk.x is not a valid Ed25519 key")
    corpus = body.get("corpus")
    if not isinstance(corpus, dict):
        raise Bad(422, "corpus must be an object")
    for k in ("coreId", "auditId"):
        req_str(corpus, k, "corpus", 256)
    for k in ("stableCount", "freshCount"):
        if not isinstance(corpus.get(k), int) or isinstance(corpus.get(k), bool) or corpus[k] < 0:
            raise Bad(422, f"corpus.{k} must be a non-negative integer")
    allowed = body.get("allowedActions")
    if not isinstance(allowed, list) or sorted(allowed) != sorted(ACTIONS) or len(set(allowed)) != len(allowed):
        raise Bad(422, "allowedActions must list exactly the six permitted actions")
    dossiers = body.get("dossiers")
    if not isinstance(dossiers, list) or not dossiers or len(dossiers) > 500:
        raise Bad(422, "dossiers must be a non-empty array")
    seen, counts = set(), {"stable_core": 0, "fresh_audit": 0}
    for i, d in enumerate(dossiers):
        w = f"dossiers[{i}]"
        if not isinstance(d, dict):
            raise Bad(422, f"{w} must be an object")
        did = req_str(d, "dossierId", w, 256)
        if did in seen:
            raise Bad(422, f"duplicate dossierId {did}")
        seen.add(did)
        if d.get("partition") not in counts:
            raise Bad(422, f"{w}.partition must be stable_core or fresh_audit")
        counts[d["partition"]] += 1
        for k in ("receivedAt", "mailbox", "objective"):
            req_str(d, k, w, 20000)
        sources = d.get("sources")
        if not isinstance(sources, list) or not sources:
            raise Bad(422, f"{w}.sources must be a non-empty array")
        line_ids, source_ids = set(), set()
        for j, s in enumerate(sources):
            ws = f"{w}.sources[{j}]"
            if not isinstance(s, dict):
                raise Bad(422, f"{ws} must be an object")
            sid = req_str(s, "sourceId", ws, 256)
            if sid in source_ids:
                raise Bad(422, f"{ws} duplicate sourceId")
            source_ids.add(sid)
            for k in ("kind", "provenance", "title"):
                req_str(s, k, ws, 20000, allow_empty=(k == "title"))
            lines = s.get("lines")
            if not isinstance(lines, list) or not lines:
                raise Bad(422, f"{ws}.lines must be a non-empty array")
            for k_, ln in enumerate(lines):
                wl = f"{ws}.lines[{k_}]"
                if not isinstance(ln, dict):
                    raise Bad(422, f"{wl} must be an object")
                lid = req_str(ln, "lineId", wl, 256)
                if lid in line_ids:
                    raise Bad(422, f"{wl} duplicate lineId {lid}")
                line_ids.add(lid)
                if not isinstance(ln.get("text"), str) or len(ln["text"]) > 50000:
                    raise Bad(422, f"{wl}.text must be a string")
    if counts["stable_core"] != corpus["stableCount"] or counts["fresh_audit"] != corpus["freshCount"]:
        raise Bad(422, "corpus counts do not match the dossier partitions")


def validate_commit(body):
    req_str(body, "evaluationId", "request", 256)
    if not isinstance(body.get("inputDigest"), str) or not HEX64.match(body["inputDigest"]):
        raise Bad(422, "inputDigest must be lowercase SHA-256 hex")
    receipts = body.get("receipts")
    if not isinstance(receipts, list) or not receipts:
        raise Bad(422, "receipts must be a non-empty array")
    for i, r in enumerate(receipts):
        w = f"receipts[{i}]"
        if not isinstance(r, dict):
            raise Bad(422, f"{w} must be an object")
        for k in ("dossierId", "callId", "action", "proposalDigest", "receiptId", "receiptSignature"):
            req_str(r, k, w, 4096)
        if not isinstance(r.get("accepted"), bool):
            raise Bad(422, f"{w}.accepted must be boolean")


# ---------- crypto ----------

def b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64_decode_any(s):
    s = s.strip()
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4), validate=True)
    except Exception:
        return b64url_decode(s)


def public_key(jwk):
    raw = b64url_decode(jwk["x"])
    if len(raw) != 32:
        raise ValueError("bad key length")
    return Ed25519PublicKey.from_public_bytes(raw)


def proposal_digest(p):
    view = {"dossierId": p["dossierId"], "callId": p["callId"], "action": p["action"],
            "target": p.get("target"), "payload": p["payload"], "evidence": sorted(p["evidence"])}
    return sha256_hex(view)


# ---------- propose ----------

def decide_with_cache(dossiers):
    """Return {dossierId: decision}; shared stable dossiers are decided once, even across concurrent evaluations."""
    keys = {d["dossierId"]: "dec:" + sha256_hex(d) for d in dossiers}
    results, mine, waits = {}, [], []
    for d in dossiers:
        cached = store.get(keys[d["dossierId"]])
        if cached:
            results[d["dossierId"]] = cached
            continue
        with inflight_guard:
            fut = inflight.get(keys[d["dossierId"]])
            if fut is None:
                fut = Future()
                inflight[keys[d["dossierId"]]] = fut
                mine.append((d, fut))
            else:
                waits.append((d, fut))
    if mine:
        try:
            decided = decide_all([d for d, _ in mine])
            to_store = {keys[d["dossierId"]]: decided[d["dossierId"]]
                        for d, _ in mine if not decided[d["dossierId"]].get("fallback")}
            store.put_many(to_store)
            for d, fut in mine:
                results[d["dossierId"]] = decided[d["dossierId"]]
                fut.set_result(decided[d["dossierId"]])
        except Exception as e:
            for _, fut in mine:
                if not fut.done():
                    fut.set_exception(e)
            raise
        finally:
            with inflight_guard:
                for d, _ in mine:
                    inflight.pop(keys[d["dossierId"]], None)
    for d, fut in waits:
        results[d["dossierId"]] = fut.result(timeout=50)
    return results, keys


def handle_propose(body):
    validate_propose(body)
    eid = body["evaluationId"]
    request_hash = sha256_hex(body)
    with lock_for(eid):
        existing = store.get("eval:" + eid)
        if existing:
            if existing["requestHash"] == request_hash:
                return 200, existing["proposeResponse"]  # exact replay: no model work
            raise Bad(409, "evaluationId already used with different content")

        dossiers = body["dossiers"]
        decisions, keys = decide_with_cache(dossiers)
        proposals = []
        for d in dossiers:
            dec = decisions[d["dossierId"]]
            call_id = "call:" + keys[d["dossierId"]][4:44]  # stable per dossier content
            proposals.append({"dossierId": d["dossierId"], "callId": call_id, "action": dec["action"],
                              "target": dec["target"], "payload": dec["payload"], "evidence": dec["evidence"]})
        response = {"profile": PROFILE, "evaluationId": eid, "status": "awaiting_receipts",
                    "inputDigest": sha256_hex(dossiers), "proposals": proposals}
        store.put("eval:" + eid, {
            "requestHash": request_hash, "inputDigest": response["inputDigest"],
            "verifierJwk": body["receiptVerifier"]["publicKeyJwk"],
            "proposals": {p["dossierId"]: {**p, "proposalDigest": proposal_digest(p)} for p in proposals},
            "order": [p["dossierId"] for p in proposals],
            "proposeResponse": response, "commit": None})
        return 200, response


# ---------- commit ----------

def handle_commit(body):
    validate_commit(body)
    eid = body["evaluationId"]
    with lock_for(eid):
        ev = store.get("eval:" + eid)
        if not ev:
            raise Bad(404, "unknown evaluationId")
        commit_hash = sha256_hex(body)
        if ev.get("commit"):
            if ev["commit"]["requestHash"] == commit_hash:
                return 200, ev["commit"]["response"]  # exact replay: no new effect
            raise Bad(409, "evaluation already committed with different receipts")
        if body["inputDigest"] != ev["inputDigest"]:
            raise Bad(409, "inputDigest does not match the proposal")

        receipts = body["receipts"]
        by_dossier = {}
        for r in receipts:
            if r["dossierId"] in by_dossier:
                raise Bad(409, f"duplicate receipt for {r['dossierId']}")
            by_dossier[r["dossierId"]] = r
        if set(by_dossier) != set(ev["proposals"]):
            raise Bad(409, "receipts must cover exactly the proposed dossiers")
        if len({r["receiptId"] for r in receipts}) != len(receipts):
            raise Bad(409, "duplicate receiptId")
        if len({r["receiptSignature"] for r in receipts}) != len(receipts):
            raise Bad(409, "duplicate receiptSignature")

        key = public_key(ev["verifierJwk"])
        for r in receipts:
            p = ev["proposals"][r["dossierId"]]
            if r["callId"] != p["callId"] or r["action"] != p["action"] or r["proposalDigest"] != p["proposalDigest"]:
                raise Bad(409, f"receipt for {r['dossierId']} does not match the persisted proposal")
            inner = {k: v for k, v in r.items() if k != "receiptSignature"}
            message = canonical({"profile": PROFILE, "evaluationId": eid, "inputDigest": body["inputDigest"],
                                 "receipt": inner}).encode("utf-8")
            try:
                sig = b64_decode_any(r["receiptSignature"])
                if len(sig) != 64:
                    raise ValueError
                key.verify(sig, message)
            except (InvalidSignature, ValueError, Exception):
                raise Bad(422, f"invalid receipt signature for {r['dossierId']}")

        outcomes = []
        for did in ev["order"]:
            r, p = by_dossier[did], ev["proposals"][did]
            outcomes.append({"dossierId": did, "callId": p["callId"], "action": p["action"],
                             "proposalDigest": p["proposalDigest"], "receiptId": r["receiptId"],
                             "status": "executed" if r["accepted"] is True else "rejected"})
        response = {"profile": PROFILE, "evaluationId": eid, "status": "completed",
                    "inputDigest": ev["inputDigest"], "outcomes": outcomes}
        ev["commit"] = {"requestHash": commit_hash, "receipts": receipts, "response": response}
        store.put("eval:" + eid, ev)  # persist receipts before replying
        return 200, response


# ---------- HTTP ----------

@app.post("/")
async def endpoint(request: Request):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY:
        return err(413, "request body too large")
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return err(413, "request body too large")
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        return err(400, "body must be valid JSON")
    if not isinstance(body, dict):
        return err(400, "body must be a JSON object")
    if body.get("profile") != PROFILE:
        return err(400, f"profile must be {PROFILE}")
    op = body.get("operation")
    try:
        if op == "propose":
            import anyio
            status, payload = await anyio.to_thread.run_sync(handle_propose, body)
        elif op == "commit":
            import anyio
            status, payload = await anyio.to_thread.run_sync(handle_commit, body)
        else:
            return err(400, "operation must be propose or commit")
    except Bad as b:
        return err(b.status, b.message)
    if len(canonical(payload).encode("utf-8")) > MAX_RESPONSE:
        return err(500, "response would exceed 512 KiB")
    return reply(status, payload)


@app.get("/")
def health():
    return reply(200, {"profile": PROFILE, "status": "ok"})
