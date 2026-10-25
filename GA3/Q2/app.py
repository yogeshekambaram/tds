"""A2A 1.0 HTTP+JSON invoice action agent (protocol layer).

Routes: GET /.well-known/agent-card.json (public) and, under BASE_PATH, POST message:send,
GET tasks/{id}, GET tasks, POST tasks/{id}:cancel. Storage lives in store.py, AI in decide.py.
"""
import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

import decide
from store import Store

BASE_PATH = os.environ.get("BASE_PATH", "/a2a/")
A2A_MEDIA = "application/a2a+json"
BATCH_MEDIA = "application/vnd.ga5.invoice-claim-batch+json"
RESULTS_MEDIA = "application/vnd.ga5.invoice-action-results+json"
PROPOSALS_MEDIA = "application/vnd.ga5.invoice-action-proposals+json"
RECEIPTS_MEDIA = "application/vnd.ga5.invoice-action-receipts+json"
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_CANCELED", "TASK_STATE_FAILED", "TASK_STATE_REJECTED"}
MAX_BYTES = 512 * 1024
LOG = Path(__file__).with_name("requests.jsonl")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
store = Store()
_key_locks, _key_guard = {}, threading.Lock()


class ApiError(Exception):
    def __init__(self, code, status, message, reason=None):
        self.code, self.status, self.message, self.reason = code, status, message, reason or status


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def respond(payload, code=200):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return Response(body, status_code=code, media_type=A2A_MEDIA)


def error(e: ApiError):
    payload = {"error": {"code": e.code, "status": e.status, "message": e.message, "details": [
        {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": e.reason, "domain": "a2a-protocol.org"}]}}
    resp = respond(payload, e.code)
    if e.code == 401:
        resp.headers["WWW-Authenticate"] = 'Bearer realm="a2a"'
    return resp


def key_lock(key):
    with _key_guard:
        return _key_locks.setdefault(key, threading.Lock())


def log(request, body, status):
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": now(), "method": request.method, "path": request.url.path,
                                "query": str(request.url.query), "status": status,
                                "headers": {k: v for k, v in request.headers.items() if k.lower() != "authorization"},
                                "auth_present": "authorization" in request.headers,
                                "body": body[:200000]}, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------------------- checks
def principal_of(request):
    auth = request.headers.get("authorization", "")
    m = re.fullmatch(r"Bearer ([^\s]+)", auth)
    if not m:
        raise ApiError(401, "UNAUTHENTICATED", "Missing or invalid bearer token")
    return hashlib.sha256(m.group(1).encode()).hexdigest()


def check_common(request, needs_body):
    principal = principal_of(request)
    if request.headers.get("a2a-version", "").strip() != "1.0":
        raise ApiError(400, "VERSION_NOT_SUPPORTED", "A2A-Version 1.0 is required", "VERSION_NOT_SUPPORTED")
    if needs_body:
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != A2A_MEDIA:
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must be application/a2a+json",
                           "CONTENT_TYPE_NOT_SUPPORTED")
    return principal


def base_url(request):
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    return f"https://{host}{BASE_PATH}"


def digest(message):
    return hashlib.sha256(decide.canonical(message).encode()).hexdigest()


def view(task, history_length=None, include_artifacts=True):
    t = copy.deepcopy(task)
    if history_length is not None:
        t["history"] = t["history"][-history_length:] if history_length > 0 else []
    if not include_artifacts:
        t.pop("artifacts", None)
    return t


def int_param(value, default=None):
    if value is None or value == "":
        return default
    try:
        v = int(value)
    except ValueError:
        raise ApiError(400, "INVALID_ARGUMENT", "Invalid integer parameter")
    if v < 0:
        raise ApiError(400, "INVALID_ARGUMENT", "Invalid integer parameter")
    return v


def find_part(message, media):
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ApiError(400, "INVALID_ARGUMENT", "Message parts are required")
    hits = [p for p in parts if isinstance(p, dict) and p.get("mediaType") == media and isinstance(p.get("data"), dict)]
    if len(hits) != 1:
        raise ApiError(400, "INVALID_ARGUMENT", "Expected exactly one data part of the required media type")
    return hits[0]["data"]


# ---------------------------------------------------------------------------------------- routes
@app.get("/.well-known/agent-card.json")
def agent_card(request: Request):
    in_modes = [BATCH_MEDIA, RESULTS_MEDIA]
    out_modes = [PROPOSALS_MEDIA, RECEIPTS_MEDIA]
    card = {
        "name": "GA5 Invoice Action Agent",
        "description": "Reads invoice claim packages, proposes one evidence-backed business action per invoice, "
                       "and executes only grader-accepted proposals with durable, replay-safe receipts.",
        "version": "1.0.0",
        "supportedInterfaces": [{"url": base_url(request), "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}],
        "provider": {"organization": "TDS GA5", "url": base_url(request)},
        "capabilities": {"streaming": False, "pushNotifications": False, "extendedAgentCard": False},
        "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}},
        "securityRequirements": [{"schemes": {"bearer": {"list": []}}}],
        "defaultInputModes": in_modes,
        "defaultOutputModes": out_modes,
        "skills": [{
            "id": "invoice_action_agent",
            "name": "Invoice action agent",
            "description": "Chooses settle_invoice, request_approval, hold_invoice, reject_duplicate or open_exception "
                           "for each invoice package, citing the decisive evidence references.",
            "tags": ["invoice", "accounts-payable", "approval", "duplicate-payment", "a2a"],
            "inputModes": in_modes,
            "outputModes": out_modes,
        }],
    }
    return JSONResponse(card, media_type="application/json")


@app.post(BASE_PATH + "message:send")
async def message_send(request: Request):
    raw = (await request.body()).decode("utf-8", "replace")
    try:
        principal = check_common(request, needs_body=True)
        try:
            body = json.loads(raw)
        except ValueError:
            raise ApiError(400, "INVALID_ARGUMENT", "Request body must be JSON")
        resp = await _run(lambda: send(principal, body))
    except ApiError as e:
        resp = error(e)
    log(request, raw, resp.status_code)
    return resp


async def _run(fn):
    import anyio
    return await anyio.to_thread.run_sync(fn)


def send(principal, body):
    if not isinstance(body, dict) or not isinstance(body.get("message"), dict):
        raise ApiError(400, "INVALID_ARGUMENT", "A message object is required")
    message = body["message"]
    mid = message.get("messageId")
    if not isinstance(mid, str) or not mid:
        raise ApiError(400, "INVALID_ARGUMENT", "messageId is required")
    if message.get("role") != "ROLE_USER":
        raise ApiError(400, "INVALID_ARGUMENT", "role must be ROLE_USER")
    config = body.get("configuration") if isinstance(body.get("configuration"), dict) else {}
    hl = config.get("historyLength")
    hl = hl if isinstance(hl, int) and hl >= 0 else None
    h = digest(message)
    with key_lock((principal, mid)):
        rec = store.get_message(principal, mid)
        if rec:
            if rec[0] != h:
                raise ApiError(409, "ALREADY_EXISTS", "Message ID was already used with different content",
                               "IDEMPOTENCY_CONFLICT")
            task = store.get_task(principal, rec[1])
            return respond({"task": view(task, hl)})
        if message.get("taskId"):
            task = continue_task(principal, message, h)
        else:
            task = create_task(principal, message, h)
    return respond({"task": view(task, hl)})


def create_task(principal, message, h):
    data = find_part(message, BATCH_MEDIA)
    batch_id, packages = data.get("batchId"), data.get("packages")
    if not isinstance(batch_id, str) or not batch_id or not isinstance(packages, list) or not packages:
        raise ApiError(400, "INVALID_ARGUMENT", "batchId and packages are required")
    ids = [p.get("packageId") if isinstance(p, dict) else None for p in packages]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ApiError(400, "INVALID_ARGUMENT", "Each package needs a unique packageId")
    try:
        decisions = decide.decide_batch(packages, data.get("policyRevision"), store)
    except Exception:
        raise ApiError(503, "UNAVAILABLE", "Decision service temporarily unavailable")
    task_id = str(uuid.uuid4())
    context_id = message.get("contextId") or str(uuid.uuid4())
    proposals = []
    for pid in ids:
        d = decisions[pid]
        proposals.append({
            "packageId": pid,
            "actionId": "act-" + hashlib.sha256(f"{task_id}:{pid}".encode()).hexdigest()[:24],
            "action": d["action"],
            "facts": d["facts"],
            "evidenceRefs": d["evidenceRefs"],
            "rationale": decide.rationale(d),
        })
    stamp = now()
    task = {
        "id": task_id,
        "contextId": context_id,
        "status": {"state": "TASK_STATE_INPUT_REQUIRED", "timestamp": stamp, "message": {
            "messageId": "status-" + task_id, "role": "ROLE_AGENT", "taskId": task_id, "contextId": context_id,
            "parts": [{"text": "Proposals ready. Send invoice-action-results to execute accepted actions."}]}},
        "artifacts": [{"artifactId": "proposals-" + task_id, "name": "invoice-action-proposals",
                       "parts": [{"mediaType": PROPOSALS_MEDIA, "data": {"batchId": batch_id, "proposals": proposals}}]}],
        "history": [dict(message, taskId=task_id, contextId=context_id)],
        "metadata": {},
    }
    with store.transaction():
        store.put_task(principal, task, time.time())
        store.put_message(principal, message["messageId"], h, task_id)
    return task


def continue_task(principal, message, h):
    task = store.get_task(principal, message["taskId"])
    if task is None:
        raise ApiError(404, "NOT_FOUND", "Task not found", "TASK_NOT_FOUND")
    if message.get("contextId") != task["contextId"]:
        raise ApiError(400, "INVALID_ARGUMENT", "Continuation does not match the task context")
    data = find_part(message, RESULTS_MEDIA)
    proposal_data = task["artifacts"][0]["parts"][0]["data"]
    if data.get("batchId") != proposal_data["batchId"]:
        raise ApiError(400, "INVALID_ARGUMENT", "Continuation does not match the task batch")
    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise ApiError(400, "INVALID_ARGUMENT", "results are required")
    by_pkg = {p["packageId"]: p for p in proposal_data["proposals"]}
    seen = set()
    for r in results:
        if not isinstance(r, dict):
            raise ApiError(400, "INVALID_ARGUMENT", "Invalid result entry")
        p = by_pkg.get(r.get("packageId"))
        if p is None or r["packageId"] in seen or r.get("actionId") != p["actionId"] or r.get("action") != p["action"]:
            raise ApiError(400, "INVALID_ARGUMENT", "Result does not match a stored proposal")
        if r.get("outcome") not in ("ACCEPTED", "REJECTED"):
            raise ApiError(400, "INVALID_ARGUMENT", "Invalid outcome")
        if r["outcome"] == "ACCEPTED" and (not isinstance(r.get("receiptNonce"), str) or not r["receiptNonce"]):
            raise ApiError(400, "INVALID_ARGUMENT", "Accepted results need a receipt nonce")
        seen.add(r["packageId"])
    if seen != set(by_pkg):
        raise ApiError(400, "INVALID_ARGUMENT", "Results must cover every proposal exactly once")
    with store.transaction():
        task = store.get_task(principal, message["taskId"])  # re-read under the lock (cancel race)
        if task["status"]["state"] in TERMINAL:
            raise ApiError(409, "FAILED_PRECONDITION", "Task is already in a terminal state", "TASK_TERMINAL")
        executions = []
        for r in results:
            if r["outcome"] != "ACCEPTED":
                continue
            p = by_pkg[r["packageId"]]
            executions.append({"packageId": p["packageId"], "actionId": p["actionId"], "action": p["action"],
                               "receiptNonce": r["receiptNonce"], "facts": p["facts"], "evidenceRefs": p["evidenceRefs"]})
        task["history"].append(dict(message))
        task["artifacts"].append({"artifactId": "receipts-" + task["id"], "name": "invoice-action-receipts",
                                  "parts": [{"mediaType": RECEIPTS_MEDIA,
                                             "data": {"batchId": proposal_data["batchId"], "executions": executions}}]})
        task["status"] = {"state": "TASK_STATE_COMPLETED", "timestamp": now()}
        store.put_task(principal, task, store.task_created(task["id"]) or time.time())
        store.put_message(principal, message["messageId"], h, task["id"])
    return task


@app.get(BASE_PATH + "tasks")
@app.get(BASE_PATH + "tasks/")
def list_tasks(request: Request):
    try:
        principal = check_common(request, needs_body=False)
        q = request.query_params
        hl = int_param(q.get("historyLength"), 0)
        include = q.get("includeArtifacts", "true").lower() != "false"
        page_size = min(int_param(q.get("pageSize"), 50) or 50, 100)
        offset = int_param(q.get("pageToken"), 0)
        tasks = store.list_tasks(principal)
        if q.get("contextId"):
            tasks = [t for t in tasks if t["contextId"] == q.get("contextId")]
        if q.get("status"):
            tasks = [t for t in tasks if t["status"]["state"] == q.get("status")]
        page = [view(t, hl, include) for t in tasks[offset:offset + page_size]]
        while page and len(json.dumps(page)) > MAX_BYTES - 4096:
            page.pop()
        nxt = offset + len(page)
        resp = respond({"tasks": page, "nextPageToken": str(nxt) if nxt < len(tasks) else "",
                        "pageSize": len(page), "totalSize": len(tasks)})
    except ApiError as e:
        resp = error(e)
    log(request, "", resp.status_code)
    return resp


@app.get(BASE_PATH + "tasks/{ref:path}")
def get_task(request: Request, ref: str):
    try:
        principal = check_common(request, needs_body=False)
        task = store.get_task(principal, ref)
        if task is None:
            raise ApiError(404, "NOT_FOUND", "Task not found", "TASK_NOT_FOUND")
        resp = respond(view(task, int_param(request.query_params.get("historyLength"))))
    except ApiError as e:
        resp = error(e)
    log(request, "", resp.status_code)
    return resp


@app.post(BASE_PATH + "tasks/{ref:path}")
async def cancel_task(request: Request, ref: str):
    raw = (await request.body()).decode("utf-8", "replace")
    try:
        if not ref.endswith(":cancel"):
            raise ApiError(404, "NOT_FOUND", "Not found")
        principal = check_common(request, needs_body=bool(raw.strip()))
        resp = await _run(lambda: cancel(principal, ref[: -len(":cancel")]))
    except ApiError as e:
        resp = error(e)
    log(request, raw, resp.status_code)
    return resp


def cancel(principal, task_id):
    with store.transaction():
        task = store.get_task(principal, task_id)
        if task is None:
            raise ApiError(404, "NOT_FOUND", "Task not found", "TASK_NOT_FOUND")
        if task["status"]["state"] in TERMINAL:
            raise ApiError(409, "FAILED_PRECONDITION", "Task cannot be canceled", "TASK_NOT_CANCELABLE")
        task["status"] = {"state": "TASK_STATE_CANCELED", "timestamp": now()}
        store.put_task(principal, task, store.task_created(task_id) or time.time())
    return respond(task)


@app.exception_handler(404)
async def not_found(request, exc):
    return error(ApiError(404, "NOT_FOUND", "Not found"))


@app.exception_handler(405)
async def not_allowed(request, exc):
    return error(ApiError(405, "METHOD_NOT_ALLOWED", "Method not allowed"))
