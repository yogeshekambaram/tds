"""Check ledger-agent's run_plan against an independent pandas computation."""
import sys

import pandas as pd

sys.path.insert(0, r"D:\projects\TDS\ledger-agent")
import main  # noqa: E402

main.load_data()

raw = pd.read_json(r"D:\projects\TDS\ledger_explore\export.ndjson", lines=True, dtype=False)
raw["u"] = pd.to_datetime(raw["updated_at"], utc=True)
cur = raw.sort_values("u").groupby("id").tail(1).copy()
cur["ist"] = pd.to_datetime(cur["created_at"], utc=True).dt.tz_convert("Asia/Kolkata")
cur["m"] = cur["ist"].dt.strftime("%Y-%m")
cur["usd"] = cur["amount"] * cur["currency"].map({"USD": 1, "EUR": 1.04, "INR": 0.01237})
paid = cur[cur.status == "paid"]

checks = [
    ({"metric": "revenue", "filters": {"region": ["north"], "date_from": "2026-03-01", "date_to": "2026-03-31"}},
     round(paid[(paid.region == "North") & (paid.m == "2026-03")].usd.sum(), 2)),
    ({"metric": "revenue", "filters": {"product": ["Kettle"], "region": ["West"], "date_from": "2026-01-01", "date_to": "2026-03-31"}},
     round(paid[(paid["product"] == "Kettle") & (paid.region == "West") & (paid.m <= "2026-03")].usd.sum(), 2)),
    ({"metric": "revenue", "group_by": "product", "answer": "name", "filters": {"date_from": "2026-02-01", "date_to": "2026-02-29"}},
     paid[paid.m == "2026-02"].groupby("product").usd.sum().idxmax()),
    ({"metric": "revenue", "group_by": "customer", "answer": "name"},
     paid.groupby("customer").usd.sum().idxmax()),
    ({"metric": "refund_amount", "filters": {"region": ["South"], "date_from": "2026-04-01", "date_to": "2026-04-30"}},
     round(cur[(cur.status == "refunded") & (cur.region == "South") & (cur.m == "2026-04")].usd.sum(), 2)),
    ({"metric": "units", "filters": {"product": ["air fryer"], "date_from": "2026-05-01", "date_to": "2026-05-31"}},
     int(paid[(paid["product"] == "Air Fryer") & (paid.m == "2026-05")].qty.sum())),
    ({"metric": "order_count", "status": "refunded", "filters": {"date_from": "2026-06-01", "date_to": "2026-06-30"}},
     int(((cur.status == "refunded") & (cur.m == "2026-06")).sum())),
    ({"metric": "revenue", "group_by": "region", "order": "asc", "answer": "name", "filters": {"date_from": "2026-01-01", "date_to": "2026-01-31"}},
     paid[paid.m == "2026-01"].groupby("region").usd.sum().idxmin()),
    ({"metric": "units", "group_by": "product", "answer": "names", "limit": 3},
     list(paid.groupby("product").qty.sum().sort_values(ascending=False).index[:3])),
    ({"metric": "avg_order_value", "filters": {"region": ["East"]}},
     round(paid[paid.region == "East"].usd.mean(), 2)),
]

ok = 0
for plan, truth in checks:
    got = main.run_plan(plan)
    good = got == truth or (isinstance(truth, float) and abs(got - truth) < 0.005)
    ok += good
    print("OK " if good else "BAD", got, "| truth", truth)
print(ok, "/", len(checks))
