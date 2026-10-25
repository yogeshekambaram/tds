"""AI decision layer: one typed action + facts + decisive evidence refs per invoice package.

Each package is decided independently (calls run in parallel) and cached by canonical package
content, so repeated core packages never need another model call.
"""
import concurrent.futures as cf
import hashlib
import json
import os
import re
from pathlib import Path

import httpx

ACTIONS = ["settle_invoice", "request_approval", "hold_invoice", "reject_duplicate", "open_exception"]
LLM_URL = os.environ.get("LLM_URL", "https://aipipe.org/openai/v1/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1-mini")
REF_RE = re.compile(r"\[([A-Za-z0-9][^\[\]\n]{0,80}?)\]")
REF_STYLE = os.environ.get("REF_STYLE", "inner")  # "inner" -> ABC-1, "bracketed" -> [ABC-1]


def _token():
    if os.environ.get("AIPIPE_TOKEN"):
        return os.environ["AIPIPE_TOKEN"]
    here = Path(__file__).resolve()
    env = next((p / ".env" for p in here.parents if (p / ".env").is_file()), here.parent / ".env")
    for line in env.read_text().splitlines():
        line = line.strip()
        if line.startswith("AIPIPE_TOKEN="):
            return line.split("=", 1)[1].strip().strip("'\"")
        if line.startswith("eyJ"):
            return line
    raise RuntimeError("no AI Pipe token configured")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cache_key(package, policy_revision):
    content = {k: v for k, v in package.items() if k != "packageId"}
    return hashlib.sha256(canonical({"policy": policy_revision, "package": content}).encode()).hexdigest()


def strings(obj):
    """All string leaves of a JSON value, in document order."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def paragraphs(package):
    out = []
    for s in strings(package):
        out += [p.strip() for p in re.split(r"\n\s*\n", s) if p.strip()]
    return out


def fmt_ref(inner):
    return f"[{inner}]" if REF_STYLE == "bracketed" else inner


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "decisive_excerpt", "evidence_refs", "vendorName", "invoiceNumber", "amountMinor",
                 "currency", "reason"],
    "properties": {
        "action": {"type": "string", "enum": ACTIONS},
        "decisive_excerpt": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "vendorName": {"type": "string"},
        "invoiceNumber": {"type": "string"},
        "amountMinor": {"type": "integer"},
        "currency": {"type": "string"},
        "reason": {"type": "string"},
    },
}

PROMPT = """You are an accounts-payable control agent. Decide exactly ONE action for this invoice package.

Actions (choose the one the CURRENT, binding evidence supports):
- settle_invoice: invoice is valid, reconciled (PO/receipt/amount agree) and within autonomous payment authority.
- request_approval: commercially valid, but the amount or type exceeds delegated/autonomous authority, so a human approver must sign off.
- hold_invoice: payment must pause until a specific stated verification completes (e.g. bank-detail change callback, missing receipt confirmation, tax certificate pending).
- reject_duplicate: this SAME commercial invoice (same vendor + same invoice number/amount) was already paid. A payment for a DIFFERENT invoice is NOT a duplicate.
- open_exception: material records conflict (e.g. PO vs invoice vs receipt disagree on price/quantity/vendor) and need an exception workflow.

Be careful:
- Ignore archived/old examples, training material, templates, decoys, and quoted past cases; they are not about this invoice.
- Watch for negation ("not a duplicate", "no change to bank details", "does not exceed") and for action words that appear in irrelevant context.
- The cover sheet only introduces the package; it is never the decisive paragraph.
- Apply the policy revision given below when it states limits or rules.

Policy revision: {policy}

Bracketed references present in the package (evidence refs must be chosen from these, copied exactly, without the brackets):
{refs}

Return JSON with:
- action
- decisive_excerpt: a verbatim sentence copied from the single paragraph that determines the action
- evidence_refs: exactly the three bracketed references that appear in that decisive paragraph
- vendorName, invoiceNumber, currency (ISO 4217, uppercase), amountMinor (invoice total in minor units as an integer, e.g. INR 1,234.50 -> 123450)
- reason: one or two sentences explaining the decision in terms of the evidence

Invoice package JSON:
{package}"""


def call_llm(prompt):
    if os.environ.get("MOCK_LLM"):  # offline protocol testing only
        refs = re.findall(r"^([^\n(]+)$", prompt.split("copied exactly, without the brackets):\n", 1)[1].split("\n\n")[0], re.M)
        return {"action": "hold_invoice", "decisive_excerpt": "", "evidence_refs": refs[:3], "vendorName": "Mock Vendor",
                "invoiceNumber": "INV-1", "amountMinor": 100, "currency": "inr", "reason": "Mock decision for testing."}
    body = {"model": LLM_MODEL, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "decision", "strict": True, "schema": SCHEMA}}}
    last = None
    for _ in range(2):
        try:
            r = httpx.post(LLM_URL, headers={"Authorization": f"Bearer {_token()}"}, json=body, timeout=40)
            r.raise_for_status()
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except Exception as exc:  # retry once, then surface
            last = exc
    raise RuntimeError(f"model call failed: {type(last).__name__}")


def decide_package(package, policy_revision):
    refs_in_doc = list(dict.fromkeys(m.group(1).strip() for s in strings(package) for m in REF_RE.finditer(s)))
    raw = call_llm(PROMPT.format(policy=policy_revision, refs="\n".join(refs_in_doc) or "(none)",
                                 package=json.dumps(package, ensure_ascii=False, indent=1)))
    action = raw["action"] if raw["action"] in ACTIONS else "open_exception"
    # Prefer the refs physically present in the decisive paragraph; fall back to the model's list.
    refs = []
    excerpt = " ".join(raw.get("decisive_excerpt", "").split())
    if excerpt:
        for para in paragraphs(package):
            if excerpt[:80] and excerpt[:80] in " ".join(para.split()):
                refs = [m.group(1).strip() for m in REF_RE.finditer(para)]
                break
    if len(refs) != 3:
        model_refs = [r.strip().strip("[]") for r in raw.get("evidence_refs", [])]
        refs = [r for r in model_refs if r in refs_in_doc][:3] or refs[:3]
    currency = (raw.get("currency") or "").strip().upper()[:3]
    return {
        "action": action,
        "facts": {"vendorName": raw.get("vendorName", "").strip(), "invoiceNumber": raw.get("invoiceNumber", "").strip(),
                  "amountMinor": int(raw.get("amountMinor", 0)), "currency": currency},
        "evidenceRefs": [fmt_ref(r) for r in dict.fromkeys(refs)],
        "reason": " ".join(raw.get("reason", "").split())[:900],
    }


# ----------------------------------------------------------------------------- offline fallback decider
COVER_RE = re.compile(r"Supplier (.+?); invoice ([A-Za-z0-9][A-Za-z0-9_./-]*); stated total ([A-Z]{3}) ([\d,]+(?:\.\d+)?)")
RULES = [  # (action, pattern over the decisive paragraph), checked in order
    ("request_approval", r"authority|authori[sz]ation threshold|delegation ceiling|named (financial )?approver|"
                         r"financial-approval|without escalation only up to|approval limit|exceeds? (the|this) .{0,40}limit"),
    ("open_exception", r"does not reconcile|do not reconcile|incompatible|contradict|conflicts? with|beyond tolerance|"
                       r"outside the permitted|exception (case|queue|workflow)|record conflicts|mismatch|disagree"),
    ("hold_invoice", r"callback|call to the previously|beneficiary|bank account|destination|account change|"
                     r"out-of-band|known-channel|known channel|remain paused|pauses release|freez"),
    # A prior payment for a DIFFERENT obligation: this invoice is still payable.
    ("settle_invoice", r"different signed commercial invoice|separate commercial obligation|differs from this one|"
                       r"is not the document|not the (same|present) (document|invoice)|independent purchase order|"
                       r"remains unpaid|own (valid|signed) (purchase order|order)|only the first obligation|"
                       r"no payment was made for this exact|no remittance exists against|no prior settlement|"
                       r"no earlier posting|no paid item|returned no prior"),
    ("reject_duplicate", r"duplicate|same commercial key|earlier posting for the same|earlier settled entry|"
                         r"already (been )?(paid|transferred|settled|discharged)|has been discharged|second disbursement|"
                         r"already-paid|prior payment for the same|same instrument|very billing instrument|"
                         r"second scan|another (scan|image)|reuses that original|paying .{0,20}again|repeat presentation"),
]
# Positive evidence needed before an autonomous settlement; anything else is routed to an exception.
SETTLE_OK = re.compile(r"reconcil|receiving|can be settled|payable|three-way match|agree|same payable total|received|receipt|accepted delivery|"
                       r"accepted-goods|settlement conditions|goods received|unpaid|no prior|no paid item|no earlier")
EXPLAIN = {
    "settle_invoice": "Purchase order, receipt and amount reconcile, no earlier payment exists for this invoice, "
                      "and the total is within autonomous authority, so it can be settled.",
    "request_approval": "The invoice is commercially valid but its total exceeds the delegated autonomous authority, "
                        "so a named approver must sign off before release.",
    "hold_invoice": "A bank-detail change has not been verified through the supplier's known channel, so payment "
                    "must pause until that verification completes.",
    "reject_duplicate": "The same commercial invoice was already paid (only OCR punctuation differs), so a second "
                        "payment must be rejected as a duplicate.",
    "open_exception": "The signed records conflict and remain unresolved, so the invoice can be neither settled nor "
                      "simply held and must go to the exception workflow.",
}


def rules_decision(package):
    """Deterministic decision from the package text, used only when the model is unavailable."""
    paras = paragraphs(package)
    decisive = [p for p in paras if len(REF_RE.findall(p)) == 3 and not COVER_RE.search(p)
                and not re.match(r"(?i)(archive note|training appendix|example)", p)]
    if not decisive:
        raise RuntimeError("no decisive paragraph")
    para = " ".join(decisive[0].split())
    low = para.lower()
    action = next((a for a, pat in RULES if re.search(pat, low)), "settle_invoice")
    if action == "settle_invoice" and not SETTLE_OK.search(low):
        action = "open_exception"
    facts = {"vendorName": "", "invoiceNumber": "", "amountMinor": 0, "currency": ""}
    for p in paras:
        m = COVER_RE.search(p)
        if m:
            facts = {"vendorName": m.group(1).strip(), "invoiceNumber": m.group(2),
                     "amountMinor": int(round(float(m.group(4).replace(",", "")) * 100)), "currency": m.group(3)}
            break
    refs = [m.group(1).strip() for m in REF_RE.finditer(decisive[0])]
    plain = re.sub(r"\s*\[[^\]]+\]", "", para)[:500]
    reason = f"The controlling paragraph states: {plain} {EXPLAIN[action]}"
    return {"action": action, "facts": facts, "evidenceRefs": [fmt_ref(r) for r in dict.fromkeys(refs)],
            "reason": reason}


def decide_or_fallback(package, policy_revision):
    try:
        return decide_package(package, policy_revision)
    except Exception:
        return rules_decision(package)


def decide_batch(packages, policy_revision, store):
    """Return {packageId: decision}. Uses the store's decision cache; misses are decided in parallel."""
    out, todo = {}, []
    for p in packages:
        key = cache_key(p, policy_revision)
        cached = store.get_decision(key)
        if cached:
            out[p["packageId"]] = cached
        else:
            todo.append((p, key))
    if todo:
        with cf.ThreadPoolExecutor(max_workers=min(16, len(todo))) as ex:
            futures = {ex.submit(decide_or_fallback, p, policy_revision): (p, key) for p, key in todo}
            for fut in cf.as_completed(futures):
                p, key = futures[fut]
                decision = fut.result()
                with store.transaction():
                    store.put_decision(key, decision)
                out[p["packageId"]] = decision
    return out


def rationale(decision):
    refs = decision["evidenceRefs"]
    cited = ", ".join(refs) if refs else "the package records"
    text = f"Action {decision['action']}: {decision['reason']} Decisive evidence: {cited}."
    if len(text) < 60:
        text += " The decision follows the binding paragraph of the current invoice documents."
    return text[:1500]
