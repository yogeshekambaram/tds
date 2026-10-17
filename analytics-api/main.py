import os
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

API_KEY = os.environ["API_KEY"]
EMAIL = "24ds3000035@ds.study.iitm.ac.in"

app = FastAPI()
# CORS runs before the auth check, so browser preflight (OPTIONS) requests succeed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/analytics")
async def analytics(request: Request):
    if request.headers.get("x-api-key") != API_KEY:
        return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)

    body = await request.json()
    events = body.get("events", [])

    users = set()
    positive_by_user = defaultdict(float)
    revenue = 0.0
    for e in events:
        user = e.get("user")
        amount = float(e.get("amount", 0))
        users.add(user)
        if amount > 0:
            revenue += amount
            positive_by_user[user] += amount

    top_user = max(positive_by_user, key=positive_by_user.get) if positive_by_user else None

    return {
        "email": EMAIL,
        "total_events": len(events),
        "unique_users": len(users),
        "revenue": round(revenue, 2),
        "top_user": top_user,
    }


@app.get("/health")
def health():
    return {"status": "ok"}
