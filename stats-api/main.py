"""GET /stats?values=1,2,3: descriptive statistics with a strict single-origin CORS policy."""
import time
import uuid

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

EMAIL = "24ds3000035@ds.study.iitm.ac.in"
ALLOWED_ORIGIN = "https://dash-5mao0o.example.com"

app = FastAPI(title="Stats service")
app.add_middleware(CORSMiddleware, allow_origins=[ALLOWED_ORIGIN], allow_methods=["GET", "OPTIONS"],
                   allow_headers=["*"], expose_headers=["X-Request-ID", "X-Process-Time"])


# Registered after CORS, so it wraps it: preflight responses get these headers too.
@app.middleware("http")
async def request_headers(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = str(uuid.uuid4())
    response.headers["X-Process-Time"] = f"{time.perf_counter() - start:.6f}"
    return response


@app.get("/stats")
def stats(values: str = Query(..., description="Comma-separated integers")):
    try:
        nums = [int(v) for v in values.replace(" ", "").split(",") if v != ""]
    except ValueError:
        raise HTTPException(422, "values must be comma-separated integers")
    if not nums:
        raise HTTPException(422, "values must contain at least one integer")
    return {"email": EMAIL, "count": len(nums), "sum": sum(nums), "min": min(nums), "max": max(nums),
            "mean": sum(nums) / len(nums)}


@app.get("/")
def root():
    return {"status": "ok", "endpoint": "/stats?values=1,2,3"}
