"""POST /extract: pull vendor, amount, currency and due date out of free-form invoice text.

A local Ollama model does the extraction with a JSON-schema constrained reply; every field is then
checked against the source text and repaired with regex heuristics, so small models stay accurate.
"""
import json
import os
import re
from datetime import datetime

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("MODEL", "qwen2.5:0.5b")

app = FastAPI(title="Invoice extractor")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ExtractRequest(BaseModel):
    text: str


class Invoice(BaseModel):
    vendor: str
    amount: float
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    date: str


SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "amount": {"type": "number"},
        "currency": {"type": "string"},
        "date": {"type": "string"},
    },
    "required": ["vendor", "amount", "currency", "date"],
}

PROMPT = """Extract these fields from the invoice below and reply with JSON only.
- vendor: the company that issued the invoice (full name, including suffix like Ltd. or Inc.)
- amount: the TOTAL amount due as a plain number (no symbols or commas)
- currency: 3-letter ISO code, uppercase (USD, EUR, GBP, ...)
- date: the payment DUE date as YYYY-MM-DD

Invoice:
{text}"""

SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}
CODES = r"USD|EUR|GBP|JPY|INR|AUD|CAD|CHF|CNY|SGD"
MONTHS = "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%d/%m/%Y", "%Y/%m/%d"
NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
VENDOR_SUFFIX = r"(?:Ltd\.?|Limited|Inc\.?|LLC|LLP|PLC|GmbH|Corp\.?|Corporation|Co\.|Company|Industries|Group|S\.A\.)"


def ask_llm(text):
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/chat", timeout=60, json={
            "model": MODEL, "stream": False, "format": SCHEMA, "options": {"temperature": 0},
            "messages": [{"role": "user", "content": PROMPT.format(text=text[:6000])}]})
        r.raise_for_status()
        return json.loads(r.json()["message"]["content"])
    except Exception:
        return {}


def to_float(s):
    try:
        return float(str(s).replace(",", "").strip())
    except ValueError:
        return None


def near(text, pos, words, before=80, floor=0):
    """True if any of *words* occurs in the text just before *pos* (but not before *floor*)."""
    window = text[max(0, floor, pos - before):pos].lower()
    return any(w in window for w in words)


def with_floors(items):
    """Pair each (pos, value) with the position right after the previous item, so keyword windows don't overlap."""
    out, prev = [], 0
    for pos, val, end in items:
        out.append((pos, val, prev))
        prev = end
    return out


def find_dates(text):
    """All dates in the text as (position, YYYY-MM-DD, keyword-window floor)."""
    found = [(m.start(), m.group(), m.end()) for m in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b", text)]
    pattern = r"\b(?:\d{1,2} [A-Z][a-z]+\.? \d{4}|[A-Z][a-z]+\.? \d{1,2},? \d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}/\d{1,2}/\d{1,2})\b"
    for m in re.finditer(pattern, text):
        for fmt in MONTHS:
            try:
                found.append((m.start(), datetime.strptime(m.group().replace(".", ""), fmt).strftime("%Y-%m-%d"), m.end()))
                break
            except ValueError:
                pass
    return with_floors(sorted(found))


def pick_date(text, llm_date):
    dates = find_dates(text)
    due = [d for p, d, f in dates if near(text, p, ("due", "pay by", "payable", "deadline"), floor=f)]
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(llm_date or ""))
    if m and (not dates or m.group() in {d for _, d, _ in dates}) and (not due or m.group() in due):
        return m.group()
    return (due or [d for _, d, _ in dates] or [m.group() if m else ""])[0]


def pick_currency(text, llm_cur):
    cur = str(llm_cur or "").strip().upper()
    codes = re.findall(rf"\b({CODES})\b", text, re.I)
    if re.fullmatch(r"[A-Z]{3}", cur) and (not codes or cur in {c.upper() for c in codes}):
        return cur
    if codes:
        return codes[0].upper()
    for sym, code in SYMBOLS.items():
        if sym in text:
            return code
    return cur if re.fullmatch(r"[A-Z]{3}", cur) else "USD"


def pick_amount(text, llm_amount):
    amounts = []
    for m in re.finditer(rf"(?<![\w-])(?:[$€£¥₹]\s*)?({NUM})(?![\w-])", text):
        v = to_float(m.group(1))
        if v is None or re.fullmatch(r"(19|20)\d{2}", m.group(1)):
            continue
        amounts.append((m.start(), v, m.end()))
    amounts = with_floors(amounts)
    totals = [v for p, v, f in amounts if near(text, p, ("total", "amount due", "balance due", "amount payable", "due:"), 40, f)
              and not near(text, p, ("subtotal", "sub-total", "tax", "vat"), 25, f)]
    llm = to_float(llm_amount)
    if llm is not None and (not amounts or any(abs(llm - v) < 0.005 for _, v, _ in amounts)) and (not totals or llm in totals):
        return llm
    if totals:
        return totals[-1]
    money = [v for p, v, _ in amounts if re.search(rf"[$€£¥₹]|\b(?:{CODES})\b", text[max(0, p - 6):p + 30], re.I)]
    return max(money or [v for _, v, _ in amounts] or [llm or 0.0])


FIELD_LABELS = r"\s+(?:total|amount|subtotal|tax|vat|due|date|invoice|bill to|billed to|customer|ship to|payment|balance|currency)\b"


def trim_vendor(name):
    """Cut a captured vendor at its company suffix or at the next field label (single-line invoices)."""
    m = re.search(rf"^.*?\b{VENDOR_SUFFIX}", name)
    if m:
        return m.group(0).strip()
    return re.split(FIELD_LABELS, name, maxsplit=1, flags=re.I)[0].strip(" .")


def pick_vendor(text, llm_vendor):
    m = re.search(r"(?:vendor|from|supplier|seller|billed by|issued by|payee)\s*[:\-]\s*([^\n,;]+)", text, re.I)
    if m:
        return trim_vendor(m.group(1).strip())
    v = str(llm_vendor or "").strip()
    pos = text.lower().find(v.lower()) if len(v) > 2 else -1
    if pos >= 0 and not near(text, pos, ("bill to", "billed to", "customer", "ship to", "sold to", "client"), 25):
        return v
    m = re.search(rf"\b([A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*)*\s+{VENDOR_SUFFIX})", text)
    if m:
        return m.group(1)
    return v or "Unknown"


@app.post("/extract", response_model=Invoice)
def extract(req: ExtractRequest):
    text = req.text or ""
    llm = ask_llm(text) if text.strip() else {}
    return Invoice(
        vendor=pick_vendor(text, llm.get("vendor")),
        amount=pick_amount(text, llm.get("amount")),
        currency=pick_currency(text, llm.get("currency")),
        date=pick_date(text, llm.get("date")),
    )


@app.get("/")
def health():
    return {"status": "ok", "model": MODEL}
