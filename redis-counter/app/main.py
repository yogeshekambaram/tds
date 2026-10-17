"""Redis-backed hit counter: POST /hit/{key}, GET /count/{key}, GET /healthz."""
import os

import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

r = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), port=6379, decode_responses=True,
                socket_timeout=2, socket_connect_timeout=2)
PREFIX = "hits:"

app = FastAPI(title="Redis hit counter")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.post("/hit/{key}")
def hit(key: str):
    return {"key": key, "count": r.incr(PREFIX + key)}


@app.get("/count/{key}")
def count(key: str):
    return {"key": key, "count": int(r.get(PREFIX + key) or 0)}


@app.get("/healthz")
def healthz():
    try:
        r.ping()
    except redis.RedisError:
        return JSONResponse({"status": "error", "redis": "down"}, status_code=503)
    return {"status": "ok", "redis": "up"}
