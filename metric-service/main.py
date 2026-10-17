import json
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

EMAIL = "24ds3000035@ds.study.iitm.ac.in"
START = time.monotonic()

# No labels, so /metrics shows a single "http_requests_total <value>" line
REQUESTS = Counter("http_requests", "Total HTTP requests to any endpoint")

LOGS = deque(maxlen=1000)  # recent structured log entries for /logs/tail

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def log(level, request_id, path, **fields):
    entry = {
        "level": level,
        "ts": datetime.now(timezone.utc).isoformat(),
        "path": path,
        "request_id": request_id,
        **fields,
    }
    LOGS.append(entry)
    print(json.dumps(entry), file=sys.stdout, flush=True)  # JSON logs on stdout too


@app.middleware("http")
async def count_and_log(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    REQUESTS.inc()
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as e:
        log("error", request_id, request.url.path, method=request.method, error=str(e))
        raise
    log("info", request_id, request.url.path,
        method=request.method,
        status=response.status_code,
        duration_ms=round((time.perf_counter() - started) * 1000, 2))
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/work")
def work(n: int = Query(1, ge=0, le=1_000_000)):
    total = 0
    for i in range(n):  # K units of work
        total += i * i
    return {"email": EMAIL, "done": n}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "uptime_s": round(time.monotonic() - START, 3)}


@app.get("/logs/tail")
def logs_tail(limit: int = Query(50, ge=1, le=1000)):
    return list(LOGS)[-limit:]
