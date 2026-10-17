import os
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

EMAIL = "24ds3000035@ds.study.iitm.ac.in"
BUCKET = 9          # requests per client per window
WINDOW_S = 10.0

# Assigned origin, plus the exam page so the browser-based grader can reach /ping.
# Extra origins can be added without a code change: EXTRA_ORIGINS="https://a.com,https://b.com"
ALLOWED_ORIGINS = ["https://app-buruhp.example.com", "https://exam.sanand.workers.dev"]
ALLOWED_ORIGINS += [o.strip() for o in os.environ.get("EXTRA_ORIGINS", "").split(",") if o.strip()]

app = FastAPI()
LOCK = threading.Lock()
HITS = defaultdict(deque)  # X-Client-Id -> timestamps within the window


# Middleware order: Starlette runs the LAST-added middleware first (outermost).
# Defined here innermost -> outermost: rate limiter, request context, CORS.

@app.middleware("http")
async def rate_limiter(request: Request, call_next):
    client = request.headers.get("x-client-id")
    if client:
        now = time.monotonic()
        with LOCK:
            hits = HITS[client]
            while hits and now - hits[0] >= WINDOW_S:
                hits.popleft()
            if len(hits) >= BUCKET:
                retry = max(1, int(WINDOW_S - (now - hits[0])) + 1)
                return JSONResponse({"detail": "Too Many Requests", "request_id": request.state.request_id},
                                    status_code=429, headers={"Retry-After": str(retry)})
            hits.append(now)
    return await call_next(request)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


# Outermost: answers preflights before they reach the rate limiter, and adds CORS headers
# to every response (including 429s). Only the listed origins ever get ACAO; never "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["X-Request-ID", "X-Client-Id", "Content-Type"],
    expose_headers=["X-Request-ID", "Retry-After"],
)


@app.get("/ping")
def ping(request: Request):
    return {"email": EMAIL, "request_id": request.state.request_id}


@app.get("/health")
def health():
    return {"status": "ok"}
