"""Incident agent: POST /v2/incidents, POST /v2/incidents/{runId}/receipts, GET /v2/incidents/{runId}.

Layers: request validation + persistence (this file), planning (incident_ai), trace construction
(incident_trace). Receipts drive a stored state machine; replays only read stored responses.
"""
import copy
import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response

import incident_ai
import incident_trace

PROFILE = "ga5-incident-agent/v2"
HERE = Path(__file__).resolve().parent
LOG = HERE / "requests.jsonl"
TP_RE = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
db = sqlite3.connect(str(HERE / "incident_state.db"), check_same_thread=False, isolation_level=None)
db.execute("PRAGMA journal_mode=WAL")
db.executescript("""
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, req_hash TEXT, state TEXT);
CREATE TABLE IF NOT EXISTS receipts (run_id TEXT, receipt_id TEXT, hash TEXT, response TEXT, PRIMARY KEY (run_id, receipt_id));
CREATE TABLE IF NOT EXISTS plans (key TEXT PRIMARY KEY, plan TEXT);
""")
db_lock = threading.RLock()
_locks, _guard = {}, threading.Lock()


class Fail(Exception):
    def __init__(self, code, reason, message):
        self.code, self.reason, self.message = code, reason, message


def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(o):
    return hashlib.sha256(canonical(o).encode("utf-8")).hexdigest()


def hexid(n):
    while True:
        v = secrets.token_hex(n)
        if int(v, 16):
            return v


def now_ns():
    return time.time_ns()


def run_lock(run_id):
    with _guard:
        return _locks.setdefault(run_id, threading.Lock())


def q(sql, args=()):
    with db_lock:
        return db.execute(sql, args).fetchone()


def ex(sql, args=()):
    with db_lock:
        db.execute(sql, args)


def save(state):
    ex("INSERT OR REPLACE INTO runs VALUES (?,?,?)", (state["runId"], state["reqHash"], json.dumps(state)))


def load(run_id):
    row = q("SELECT state FROM runs WHERE run_id=?", (run_id,))
    return json.loads(row[0]) if row else None


# ------------------------------------------------------------------------------------- redaction
def secret_values(state_or_body):
    vals = set()

    def walk(o):
        if isinstance(o, str):
            if len(o) >= 4:
                vals.add(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(state_or_body.get("sensitive", {}))
    for v in (state_or_body.get("policy") or {}).get("doNotExport", []) or []:
        if isinstance(v, str) and len(v) >= 6 and not re.fullmatch(r"[a-zA-Z_.]+", v):
            vals.add(v)  # literal secret strings (field names are not values)
    return vals


def scrub(obj, secrets_):
    if not secrets_:
        return obj
    if isinstance(obj, str):
        for s in secrets_:
            if s in obj:
                obj = obj.replace(s, "[REDACTED]")
        return obj
    if isinstance(obj, dict):
        return {k: scrub(v, secrets_) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v, secrets_) for v in obj]
    return obj


# ------------------------------------------------------------------------------------- validation
def need(cond, msg):
    if not cond:
        raise Fail(422, "INVALID_REQUEST", msg)


def validate_create(b):
    if b.get("profile") != PROFILE:
        raise Fail(400, "UNSUPPORTED_PROFILE", "profile must be " + PROFILE)
    for k in ("runId", "agentName", "publicMarker"):
        need(isinstance(b.get(k), str) and b[k] != "", f"{k} must be a non-empty string")
    need(isinstance(b.get("sensitive", {}), dict), "sensitive must be an object")
    inc = b.get("incident")
    need(isinstance(inc, dict), "incident must be an object")
    need(isinstance(inc.get("transcript"), str) and inc["transcript"].strip(), "incident.transcript is required")
    need(isinstance(inc.get("allowedRootCauses"), list) and inc["allowedRootCauses"]
         and all(isinstance(c, str) for c in inc["allowedRootCauses"]), "incident.allowedRootCauses is required")
    cat = b.get("toolCatalog")
    need(isinstance(cat, list) and cat, "toolCatalog must be a non-empty array")
    names = [t.get("name") if isinstance(t, dict) else None for t in cat]
    need(all(isinstance(n, str) and n for n in names) and len(set(names)) == len(names), "toolCatalog names must be unique")
    need(all(isinstance(t.get("inputSchema", {}), dict) for t in cat), "inputSchema must be an object")
    pol = b.get("policy")
    need(isinstance(pol, dict), "policy must be an object")
    need(isinstance(pol.get("effectTools", []), list), "policy.effectTools must be an array")
    need(len(incident_ai.evidence_ids(inc["transcript"])) >= 2, "transcript has no evidence IDs")


# ------------------------------------------------------------------------------------- state machine
def new_dispatch(state, action):
    att = {"attempt": len(action["attempts"]) + 1, "clientSpanId": hexid(8), "start": now_ns(), "end": None,
           "receiptId": None, "nonce": None, "status": None, "errorType": None, "resultClass": None}
    action["attempts"].append(att)
    action["state"] = "pending"
    if action.get("startNs") is None:
        action["startNs"] = att["start"]
    d = {"actionId": action["actionId"], "callId": action["callId"], "phase": action["phase"],
         "toolName": action["toolName"], "arguments": action["arguments"], "evidence": action["evidence"],
         "attempt": att["attempt"], "traceparent": f"00-{state['traceId']}-{att['clientSpanId']}-01"}
    if state.get("tracestate"):
        d["tracestate"] = state["tracestate"]
    if action.get("approvalId"):
        d["approvalId"] = action["approvalId"]
        d["approvalNonce"] = action["approvalNonce"]
    state["actionLog"].append(d)
    if action["phase"] == "effect":
        state["chosenEffect"] = action["toolName"]
    return d


def pending_approvals(state):
    a = state.get("approval")
    if a and a.get("decision") is None:
        return [{"approvalId": a["approvalId"], "actionId": a["actionId"], "toolName": a["toolName"],
                 "argumentsDigest": a["argumentsDigest"]}]
    return []


def advance(state):
    """After diagnostics/approvals change, decide what (if anything) to dispatch next. Returns new dispatches."""
    diags = [a for a in state["actions"] if a["phase"] == "diagnostic"]
    eff = next(a for a in state["actions"] if a["phase"] == "effect")
    out = []
    if any(a["state"] == "pending" for a in diags):
        return out
    if state.get("joinSpan") and state["joinSpan"]["end"] is None:
        state["joinSpan"]["end"] = now_ns()
    if any(a["state"] == "failed" for a in diags):
        if eff["state"] == "planned":
            eff["state"] = "suppressed"
            state["suppressed"].append({"actionId": eff["actionId"], "callId": eff["callId"], "toolName": eff["toolName"],
                                        "reason": "prerequisite_diagnostic_failed"})
        finish(state, "failed")
        return out
    if eff["state"] == "planned":
        if eff["toolName"] in state["approvalRequiredFor"]:
            if state.get("approval") is None:
                state["approval"] = {"approvalId": "apr-" + hexid(8), "actionId": eff["actionId"], "toolName": eff["toolName"],
                                     "argumentsDigest": sha(eff["arguments"]), "gateSpanId": hexid(8), "start": now_ns(),
                                     "end": None, "decision": None, "nonce": None, "receiptId": None}
            elif state["approval"]["decision"] == "approved":
                eff["approvalId"] = state["approval"]["approvalId"]
                eff["approvalNonce"] = state["approval"]["nonce"]
                out.append(new_dispatch(state, eff))
            elif state["approval"]["decision"] is not None:
                eff["state"] = "suppressed"
                state["suppressed"].append({"actionId": eff["actionId"], "callId": eff["callId"], "toolName": eff["toolName"],
                                            "reason": "approval_" + str(state["approval"]["decision"])})
                finish(state, "failed")
        else:
            out.append(new_dispatch(state, eff))
    elif eff["state"] == "ok":
        finish(state, "completed")
    elif eff["state"] == "failed":
        finish(state, "failed")
    return out


def finish(state, status):
    if state["status"] in ("completed", "failed"):
        return
    state["status"] = status
    state["agentEnd"] = now_ns()


def view(state, dispatches=None):
    # Every response carries the full stored state (actionLog/receiptLog/otlp are mandatory arrays/objects);
    # dispatches/approvals hold only the work issued in this response and are empty once terminal.
    terminal = state["status"] in ("completed", "failed")
    if not terminal:  # waiting: exactly the documented proposal shape; the full record appears once final
        base = {"runId": state["runId"], "status": state["status"], "diagnosis": state["diagnosis"],
                "dispatches": dispatches or [], "approvals": pending_approvals(state)}
        return scrub(base, set(state["secrets"]))
    base = {"runId": state["runId"], "status": state["status"], "diagnosis": state["diagnosis"],
            "dispatches": [] if terminal else (dispatches or []),
            "approvals": [] if terminal else pending_approvals(state),
            "chosenEffect": state["chosenEffect"], "suppressed": state["suppressed"],
            "actionLog": state["actionLog"], "receiptLog": state["receiptLog"],
            "otlp": incident_trace.build(state)}
    return scrub(base, set(state["secrets"]))


def create(body, headers):
    validate_create(body)
    run_id, h = body["runId"], sha(body)
    with run_lock(run_id):
        row = q("SELECT req_hash, state FROM runs WHERE run_id=?", (run_id,))
        if row:
            if row[0] != h:
                raise Fail(409, "RUN_CONFLICT", "runId already exists with different content")
            return json.loads(row[1])["initialResponse"]
        inc, cat, pol = body["incident"], body["toolCatalog"], body["policy"]
        server_start = now_ns()
        tp = TP_RE.match((headers.get("traceparent") or "").strip().lower())
        valid_tp = tp and tp.group(1) != "ff" and int(tp.group(2), 16) and int(tp.group(3), 16)
        trace_id = tp.group(2) if valid_tp else hexid(16)
        parent = tp.group(3) if valid_tp else None
        tracestate = (headers.get("tracestate") or "").strip() if valid_tp else ""
        key = sha({"incident": inc, "toolCatalog": cat, "policy": pol})
        row = q("SELECT plan FROM plans WHERE key=?", (key,))
        cached = json.loads(row[0]) if row else None
        chat_start = now_ns()
        try:
            plan, model_used = incident_ai.plan_with_fallback(inc, cat, pol, cached)
        except Exception:
            raise Fail(503, "PLANNER_UNAVAILABLE", "planning service temporarily unavailable")
        chat_end = now_ns()
        if not cached:
            ex("INSERT OR REPLACE INTO plans VALUES (?,?)", (key, json.dumps(plan)))
        plan = incident_ai.with_required_evidence(plan, inc["transcript"])
        base = hashlib.sha256(run_id.encode()).hexdigest()
        actions = []
        for i, step in enumerate(plan["diagnostics"] + [plan["effect"]]):
            phase = "effect" if i == len(plan["diagnostics"]) else "diagnostic"
            aid = f"act-{base[:10]}-{i + 1:02d}"
            actions.append({"actionId": aid, "callId": aid.replace("act-", "call-"), "phase": phase,
                            "toolName": step["toolName"], "arguments": step["arguments"], "evidence": step["evidence"],
                            "toolSpanId": hexid(8), "state": "planned", "attempts": [], "startNs": None, "endNs": None})
        state = {"runId": run_id, "reqHash": h, "agentName": body["agentName"], "publicMarker": body["publicMarker"],
                 "secrets": sorted(secret_values(body)), "approvalRequiredFor": pol.get("approvalRequiredFor", []) or [],
                 "traceId": trace_id, "parentSpanId": parent, "tracestate": tracestate, "serverSpanId": hexid(8),
                 "agentSpanId": hexid(8), "chatSpanId": hexid(8), "model": model_used,
                 "serverStart": server_start, "chatStart": chat_start, "chatEnd": chat_end, "agentEnd": None,
                 "diagnosis": {"rootCause": plan["rootCause"], "evidence": plan["evidence"]},
                 "chosenEffect": None, "suppressed": [], "status": "waiting",
                 "actions": actions, "approval": None, "joinSpan": None, "actionLog": [], "receiptLog": []}
        diags = [a for a in actions if a["phase"] == "diagnostic"]
        dispatches = [new_dispatch(state, a) for a in diags]
        if len(diags) > 1:
            state["joinSpan"] = {"spanId": hexid(8), "start": now_ns(), "end": None}
        state["serverEnd"] = now_ns()
        resp = view(state, dispatches)
        state["initialResponse"] = resp
        state["lastResponse"] = resp
        save(state)
        return resp


def receipt(run_id, body):
    if not isinstance(body, dict) or not isinstance(body.get("receiptId"), str) or not body["receiptId"]:
        raise Fail(422, "INVALID_RECEIPT", "receiptId is required")
    rid, h = body["receiptId"], sha(body)
    with run_lock(run_id):
        state = load(run_id)
        if state is None:
            raise Fail(404, "UNKNOWN_RUN", "run not found")
        row = q("SELECT hash, response FROM receipts WHERE run_id=? AND receipt_id=?", (run_id, rid))
        if row:
            if row[0] != h:
                raise Fail(409, "RECEIPT_CONFLICT", "receiptId already used with different content")
            return json.loads(row[1])
        if state["status"] in ("completed", "failed"):
            raise Fail(422, "RUN_TERMINAL", "run is already terminal")
        outcomes, approvals = body.get("outcomes") or [], body.get("approvals") or []
        need(isinstance(outcomes, list) and isinstance(approvals, list) and (outcomes or approvals),
             "receipt needs outcomes or approvals")
        work = copy.deepcopy(state)
        by_id = {a["actionId"]: a for a in work["actions"]}
        seen = set()
        for o in outcomes:  # validate everything before changing anything
            need(isinstance(o, dict), "invalid outcome")
            a = by_id.get(o.get("actionId"))
            need(a is not None and a["state"] == "pending" and o.get("callId") == a["callId"], "outcome is not for a pending call")
            need(isinstance(o.get("attempt"), int) and o["attempt"] == a["attempts"][-1]["attempt"], "outcome attempt mismatch")
            need(isinstance(o.get("status"), int) and not isinstance(o.get("status"), bool), "outcome status must be an integer")
            need(isinstance(o.get("nonce"), str) and o["nonce"], "outcome nonce is required")
            need(a["actionId"] not in seen, "duplicate outcome")
            seen.add(a["actionId"])
        for ap in approvals:
            need(isinstance(ap, dict) and work.get("approval") and ap.get("approvalId") == work["approval"]["approvalId"]
                 and work["approval"]["decision"] is None, "approval is not pending")
            need(ap.get("decision") in ("approved", "rejected", "denied"), "invalid approval decision")
            need(isinstance(ap.get("nonce"), str) and ap["nonce"], "approval nonce is required")
        need(len(approvals) <= 1, "only one approval can be pending")
        dispatches = []
        for o in outcomes:
            a = by_id[o["actionId"]]
            att = a["attempts"][-1]
            att.update(end=now_ns(), receiptId=rid, nonce=o["nonce"], status=o["status"],
                       errorType=o.get("errorType"), resultClass=o.get("resultClass"))
            work["receiptLog"].append(dict({"receiptId": rid}, **o))
            if 200 <= o["status"] < 300:
                a["state"], a["endNs"] = "ok", att["end"]
            elif o["status"] == 503 and att["attempt"] == 1:
                dispatches.append(new_dispatch(work, a))  # exactly one retry, same action/call IDs
            else:
                a["state"], a["endNs"] = "failed", att["end"]
        for ap in approvals:
            work["approval"].update(decision=ap["decision"], nonce=ap["nonce"], receiptId=rid, end=now_ns())
            work["receiptLog"].append({"receiptId": rid, "approvalId": ap["approvalId"], "decision": ap["decision"],
                                       "nonce": ap["nonce"]})
        eff = next(a for a in work["actions"] if a["phase"] == "effect")
        if eff["state"] in ("ok", "failed"):
            finish(work, "completed" if eff["state"] == "ok" else "failed")
        else:
            dispatches += advance(work)
        resp = view(work, dispatches)
        work["lastResponse"] = resp
        save(work)
        ex("INSERT INTO receipts VALUES (?,?,?,?)", (run_id, rid, h, json.dumps(resp)))
        return resp


def current(run_id):
    state = load(run_id)
    if state is None:
        raise Fail(404, "UNKNOWN_RUN", "run not found")
    if state["status"] in ("completed", "failed"):
        return view(state)
    return state["lastResponse"]


# ------------------------------------------------------------------------------------- HTTP
def reply(obj, code=200):
    return Response(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                    status_code=code, media_type="application/json")


def log(request, raw, resp):
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(), "method": request.method,
                                "path": request.url.path, "status": resp.status_code,
                                "headers": {k: v for k, v in request.headers.items() if k.lower() not in ("authorization", "cookie")},
                                "body": raw.decode("utf-8", "replace")[:2_000_000],
                                "response": resp.body.decode("utf-8", "replace")[:800_000]}) + "\n")
    except Exception:
        pass


async def run(request, fn):
    import anyio
    raw = await request.body()
    try:
        resp = reply(await anyio.to_thread.run_sync(fn, raw))
    except Fail as f:
        resp = reply({"error": {"code": f.reason, "message": f.message}}, f.code)
    log(request, raw, resp)
    return resp


def parse(raw):
    try:
        b = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Fail(400, "MALFORMED_JSON", "request body must be JSON")
    if not isinstance(b, dict):
        raise Fail(400, "MALFORMED_JSON", "request body must be a JSON object")
    return b


@app.post("/v2/incidents")
async def post_incident(request: Request):
    headers = dict(request.headers)
    return await run(request, lambda raw: create(parse(raw), headers))


@app.post("/v2/incidents/{run_id}/receipts")
async def post_receipt(request: Request, run_id: str):
    return await run(request, lambda raw: receipt(run_id, parse(raw)))


@app.get("/v2/incidents/{run_id}")
async def get_incident(request: Request, run_id: str):
    return await run(request, lambda raw: current(run_id))


@app.get("/")
def root():
    return reply({"ok": True, "profile": PROFILE, "routes": ["POST /v2/incidents", "POST /v2/incidents/{runId}/receipts",
                                                            "GET /v2/incidents/{runId}"]})
