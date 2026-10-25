"""Safe AI mailroom agent: POST {operation: propose|commit} on / (also /mailroom).

Protocol, storage and AI are separate: request validation and hashing happen before any model work;
state lives in SQLite; mailroom_ai.decide() is only called for dossiers not already cached.
"""
import base64
import concurrent.futures as cf
import hashlib
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, Request
from fastapi.responses import Response

import mailroom_ai

PROFILE = "ga5-mailroom-action-gate/v2"
MAX_BODY = 8 * 1024 * 1024
CALL_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{12,128}$")
HERE = Path(__file__).resolve().parent
LOG = HERE / "requests.jsonl"

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
db = sqlite3.connect(str(HERE / "mailroom_state.db"), check_same_thread=False, isolation_level=None)
db.execute("PRAGMA journal_mode=WAL")
db.executescript("""
CREATE TABLE IF NOT EXISTS evaluations (id TEXT PRIMARY KEY, req_hash TEXT, input_digest TEXT, verifier TEXT,
    propose_resp TEXT, commit_hash TEXT, commit_resp TEXT, receipts TEXT);
CREATE TABLE IF NOT EXISTS decisions (key TEXT PRIMARY KEY, body TEXT);
""")
db_lock = threading.RLock()
_eval_locks, _guard = {}, threading.Lock()


class Fail(Exception):
    def __init__(self, code, reason, message):
        self.code, self.reason, self.message = code, reason, message


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(obj):
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def reply(obj, code=200):
    return Response(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                    status_code=code, media_type="application/json")


def eval_lock(eid):
    with _guard:
        return _eval_locks.setdefault(eid, threading.Lock())


def q(sql, args=()):
    with db_lock:
        return db.execute(sql, args).fetchone()


def ex(sql, args=()):
    with db_lock:
        db.execute(sql, args)


# ------------------------------------------------------------------------------------- validation
def need(cond, msg):
    if not cond:
        raise Fail(422, "SCHEMA_INVALID", msg)


def is_str(v, empty=False):
    return isinstance(v, str) and (empty or v != "")


def validate_propose(req):
    need(is_str(req.get("evaluationId")), "evaluationId must be a non-empty string")
    rv = req.get("receiptVerifier")
    need(isinstance(rv, dict) and rv.get("algorithm") == "Ed25519", "receiptVerifier.algorithm must be Ed25519")
    jwk = rv.get("publicKeyJwk")
    need(isinstance(jwk, dict) and jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519" and is_str(jwk.get("x")),
         "receiptVerifier.publicKeyJwk must be an OKP Ed25519 JWK")
    try:
        need(len(b64url(jwk["x"])) == 32, "publicKeyJwk.x must encode 32 bytes")
    except (ValueError, TypeError):
        raise Fail(422, "SCHEMA_INVALID", "publicKeyJwk.x is not base64url")
    need(isinstance(req.get("corpus"), dict), "corpus must be an object")
    allowed = req.get("allowedActions")
    need(isinstance(allowed, list) and allowed and all(a in mailroom_ai.ACTIONS for a in allowed)
         and len(set(allowed)) == len(allowed), "allowedActions must list known actions")
    dossiers = req.get("dossiers")
    need(isinstance(dossiers, list) and dossiers, "dossiers must be a non-empty array")
    seen = set()
    for d in dossiers:
        need(isinstance(d, dict) and is_str(d.get("dossierId")), "each dossier needs a dossierId")
        need(d["dossierId"] not in seen, "duplicate dossierId")
        seen.add(d["dossierId"])
        need(d.get("partition") in ("stable_core", "fresh_audit"), "invalid dossier partition")
        for k in ("receivedAt", "mailbox", "objective"):
            need(is_str(d.get(k), empty=True), f"dossier.{k} must be a string")
        need(isinstance(d.get("sources"), list) and d["sources"], "dossier.sources must be a non-empty array")
        line_ids = set()
        for s in d["sources"]:
            need(isinstance(s, dict) and is_str(s.get("sourceId")), "each source needs a sourceId")
            for k in ("kind", "provenance", "title"):
                need(is_str(s.get(k), empty=True), f"source.{k} must be a string")
            need(isinstance(s.get("lines"), list), "source.lines must be an array")
            for ln in s["lines"]:
                need(isinstance(ln, dict) and is_str(ln.get("lineId")) and is_str(ln.get("text"), empty=True),
                     "each line needs lineId and text")
                need(ln["lineId"] not in line_ids, "duplicate lineId in dossier")
                line_ids.add(ln["lineId"])


RECEIPT_FIELDS = {"dossierId": str, "callId": str, "action": str, "accepted": bool, "proposalDigest": str,
                  "receiptId": str, "receiptSignature": str}


def validate_commit(req):
    need(is_str(req.get("evaluationId")), "evaluationId must be a non-empty string")
    need(isinstance(req.get("inputDigest"), str) and re.fullmatch(r"[0-9a-f]{64}", req["inputDigest"]),
         "inputDigest must be lowercase SHA-256 hex")
    rs = req.get("receipts")
    need(isinstance(rs, list) and rs, "receipts must be a non-empty array")
    for r in rs:
        need(isinstance(r, dict), "each receipt must be an object")
        for k, t in RECEIPT_FIELDS.items():
            need(isinstance(r.get(k), t) and (t is bool or r[k] != ""), f"receipt.{k} is missing or invalid")


def b64url(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64any(s):
    s = s.strip()
    try:
        return base64.b64decode(s + "=" * (-len(s) % 4), validate=True)
    except (ValueError, TypeError):
        return b64url(s)


# ------------------------------------------------------------------------------------- operations
def proposal_digest(p):
    view = {"dossierId": p["dossierId"], "callId": p["callId"], "action": p["action"], "target": p.get("target"),
            "payload": p["payload"], "evidence": sorted(p["evidence"])}
    return sha(view)


DECISION_VERSION = "v3"
DECIDE_BUDGET_S = 38  # bump when decision logic changes; callIds stay tied to dossier content only


def dossier_key(d):
    return hashlib.sha256((d["dossierId"] + "\n" + canonical(d)).encode("utf-8")).hexdigest()


def decide_all(dossiers):
    out, todo = {}, []
    for d in dossiers:
        k = dossier_key(d)
        row = q("SELECT body FROM decisions WHERE key=?", (DECISION_VERSION + ":" + k,))
        if row:
            out[d["dossierId"]] = json.loads(row[0])
        else:
            todo.append((d, k))
    if todo:
        pool = cf.ThreadPoolExecutor(max_workers=min(12, len(todo)))
        futs = {pool.submit(mailroom_ai.decide, d): (d, k) for d, k in todo}
        done, pending = cf.wait(futs, timeout=DECIDE_BUDGET_S)
        for f in list(done) + list(pending):
            d, k = futs[f]
            ok = f in done and f.exception() is None
            decision = f.result() if ok else mailroom_ai.fallback(d)
            decision["callId"] = "mr." + k[:40]
            # cached either way: stable dossiers must keep the same proposal across evaluations
            ex("INSERT OR REPLACE INTO decisions VALUES (?,?)", (DECISION_VERSION + ":" + k, json.dumps(decision)))
            out[d["dossierId"]] = decision
        pool.shutdown(wait=False)
    return out


def propose(req):
    validate_propose(req)
    eid, req_hash = req["evaluationId"], sha(req)
    with eval_lock(eid):
        row = q("SELECT req_hash, propose_resp FROM evaluations WHERE id=?", (eid,))
        if row:
            if row[0] != req_hash:
                raise Fail(409, "EVALUATION_CONFLICT", "evaluationId was already used with different content")
            return json.loads(row[1])
        try:
            decisions = decide_all(req["dossiers"])
        except Exception:
            raise Fail(503, "MODEL_UNAVAILABLE", "decision service temporarily unavailable")
        allowed = set(req["allowedActions"])
        proposals = []
        for d in req["dossiers"]:
            dec = decisions[d["dossierId"]]
            p = {"dossierId": d["dossierId"], "callId": dec["callId"], "action": dec["action"],
                 "target": dec["target"], "payload": dec["payload"], "evidence": dec["evidence"]}
            if p["action"] not in allowed:  # never propose a tool the grader did not allow
                raise Fail(503, "MODEL_UNAVAILABLE", "decision service produced a disallowed action")
            proposals.append(p)
        resp = {"profile": PROFILE, "evaluationId": eid, "status": "awaiting_receipts",
                "inputDigest": sha(req["dossiers"]), "proposals": proposals}
        ex("INSERT INTO evaluations (id, req_hash, input_digest, verifier, propose_resp) VALUES (?,?,?,?,?)",
           (eid, req_hash, resp["inputDigest"], json.dumps(req["receiptVerifier"]), json.dumps(resp)))
        return resp


def commit(req):
    validate_commit(req)
    eid = req["evaluationId"]
    with eval_lock(eid):
        row = q("SELECT input_digest, verifier, propose_resp, commit_hash, commit_resp FROM evaluations WHERE id=?", (eid,))
        if not row:
            raise Fail(422, "UNKNOWN_EVALUATION", "no proposals exist for this evaluation")
        input_digest, verifier, propose_resp, commit_hash, commit_resp = row
        req_hash = sha(req)
        if commit_hash:
            if commit_hash != req_hash:
                raise Fail(409, "EVALUATION_CONFLICT", "this evaluation was already committed with different receipts")
            return json.loads(commit_resp)
        if req["inputDigest"] != input_digest:
            raise Fail(409, "DIGEST_MISMATCH", "inputDigest does not match the proposed evaluation")
        proposals = {p["dossierId"]: p for p in json.loads(propose_resp)["proposals"]}
        receipts = req["receipts"]
        if len(receipts) != len(proposals) or {r["dossierId"] for r in receipts} != set(proposals):
            raise Fail(409, "RECEIPT_SET_MISMATCH", "exactly one receipt is required for every proposal")
        if len({r["receiptId"] for r in receipts}) != len(receipts) or \
                len({r["receiptSignature"] for r in receipts}) != len(receipts):
            raise Fail(422, "RECEIPT_INVALID", "duplicated receipt or signature")
        for r in receipts:
            p = proposals[r["dossierId"]]
            if r["callId"] != p["callId"] or r["action"] != p["action"] or r["proposalDigest"] != proposal_digest(p):
                raise Fail(409, "PROPOSAL_MISMATCH", "receipt does not match the persisted proposal")
        key = Ed25519PublicKey.from_public_bytes(b64url(json.loads(verifier)["publicKeyJwk"]["x"]))
        for r in receipts:  # verify ALL signatures before recording any effect
            inner = {k: v for k, v in r.items() if k != "receiptSignature"}
            msg = canonical({"profile": PROFILE, "evaluationId": eid, "inputDigest": req["inputDigest"], "receipt": inner})
            try:
                key.verify(b64any(r["receiptSignature"]), msg.encode("utf-8"))
            except (InvalidSignature, ValueError, TypeError):
                raise Fail(422, "RECEIPT_SIGNATURE_INVALID", "a receipt signature failed verification")
        by_id = {r["dossierId"]: r for r in receipts}
        outcomes = []
        for did, p in proposals.items():
            r = by_id[did]
            outcomes.append({"dossierId": did, "callId": p["callId"], "action": p["action"],
                             "proposalDigest": r["proposalDigest"], "receiptId": r["receiptId"],
                             "status": "executed" if r["accepted"] is True else "rejected"})
        resp = {"profile": PROFILE, "evaluationId": eid, "status": "completed", "inputDigest": input_digest,
                "outcomes": outcomes}
        ex("UPDATE evaluations SET commit_hash=?, commit_resp=?, receipts=? WHERE id=?",
           (req_hash, json.dumps(resp), json.dumps(receipts), eid))
        return resp


def handle(raw):
    if len(raw) > MAX_BODY:
        raise Fail(413, "BODY_TOO_LARGE", "request body too large")
    try:
        req = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Fail(400, "MALFORMED_JSON", "request body must be JSON")
    if not isinstance(req, dict):
        raise Fail(400, "MALFORMED_REQUEST", "request body must be a JSON object")
    if req.get("profile") != PROFILE:
        raise Fail(400, "UNSUPPORTED_PROFILE", "profile must be " + PROFILE)
    op = req.get("operation")
    if op == "propose":
        return propose(req)
    if op == "commit":
        return commit(req)
    raise Fail(400, "INVALID_OPERATION", "operation must be propose or commit")


async def endpoint(request: Request):
    raw = await request.body()
    import anyio
    try:
        resp = reply(await anyio.to_thread.run_sync(handle, raw))
    except Fail as f:
        resp = reply({"profile": PROFILE, "status": "error", "error": {"code": f.reason, "message": f.message}}, f.code)
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(), "path": request.url.path,
                                 "status": resp.status_code, "body": raw[:3_000_000].decode("utf-8", "replace"),
                                 "response": resp.body[:300000].decode("utf-8", "replace")}) + "\n")
    except Exception:
        pass
    return resp


app.add_api_route("/", endpoint, methods=["POST"])
app.add_api_route("/mailroom", endpoint, methods=["POST"])
app.add_api_route("/actions", endpoint, methods=["POST"])
app.add_api_route("/mailroom/actions", endpoint, methods=["POST"])


@app.get("/")
@app.get("/mailroom")
@app.get("/actions")
@app.get("/mailroom/actions")
def info():
    return reply({"profile": PROFILE, "status": "ready", "operations": ["propose", "commit"]})
