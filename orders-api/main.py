import base64
import math
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

TOTAL_ORDERS = 51       # fixed catalog: order IDs 1..T
RATE_LIMIT = 17         # requests per client per window
WINDOW_S = 10.0
EMAIL = "24ds3000035@ds.study.iitm.ac.in"

app = FastAPI()

LOCK = threading.Lock()
CATALOG = [{"id": i, "item": f"Item {i}", "amount": round(10 + i * 1.5, 2)} for i in range(1, TOTAL_ORDERS + 1)]
ORDERS_BY_KEY = {}                 # Idempotency-Key -> created order
HITS = defaultdict(deque)          # X-Client-Id -> timestamps of recent requests


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    # Only requests that identify a client are bucketed; each client ID has its own window
    client = request.headers.get("x-client-id")
    if client and request.method != "OPTIONS":
        now = time.monotonic()
        with LOCK:
            hits = HITS[client]
            while hits and now - hits[0] >= WINDOW_S:
                hits.popleft()
            if len(hits) >= RATE_LIMIT:
                retry = max(1, math.ceil(WINDOW_S - (now - hits[0])))
                return JSONResponse(
                    {"detail": "Rate limit exceeded", "retry_after_s": retry},
                    status_code=429,
                    headers={"Retry-After": str(retry), "X-RateLimit-Limit": str(RATE_LIMIT),
                             "X-RateLimit-Remaining": "0"},
                )
            hits.append(now)
            remaining = RATE_LIMIT - len(hits)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
    return await call_next(request)


# Added after the rate limiter so it is the outermost layer: 429s also get CORS headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)


@app.post("/orders")
async def create_order(request: Request, idempotency_key: str = Header(None)):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    with LOCK:
        if idempotency_key and idempotency_key in ORDERS_BY_KEY:
            # Replay: same order, never a duplicate
            return JSONResponse(ORDERS_BY_KEY[idempotency_key], status_code=200,
                                headers={"Idempotent-Replayed": "true"})
        order = {"id": str(uuid.uuid4()), "status": "created", "email": EMAIL,
                 "idempotency_key": idempotency_key,
                 "data": payload if isinstance(payload, dict) else {}}
        if idempotency_key:
            ORDERS_BY_KEY[idempotency_key] = order
    return JSONResponse(order, status_code=201)


def encode_cursor(next_id):
    return base64.urlsafe_b64encode(f"after:{next_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor):
    padded = cursor + "=" * (-len(cursor) % 4)
    text = base64.urlsafe_b64decode(padded.encode()).decode()
    if not text.startswith("after:"):
        raise ValueError("bad cursor")
    return int(text[len("after:"):])


@app.get("/orders")
def list_orders(limit: int = Query(10, ge=1, le=100), cursor: str = Query(None)):
    start = 0  # index into CATALOG
    if cursor:
        try:
            start = decode_cursor(cursor)
        except Exception:
            return JSONResponse({"detail": "Invalid cursor"}, status_code=400)
        if start < 0 or start > TOTAL_ORDERS:
            return JSONResponse({"detail": "Invalid cursor"}, status_code=400)
    items = CATALOG[start:start + limit]
    end = start + len(items)
    next_cursor = encode_cursor(end) if end < TOTAL_ORDERS else None
    return {"items": items, "next_cursor": next_cursor, "total": TOTAL_ORDERS}


@app.get("/health")
def health():
    return {"status": "ok"}
