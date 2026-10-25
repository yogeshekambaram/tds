# PR1 Q1: Predict AI Pipe usage, 2–8 November 2026 (1 mark)

## The question

Predict the total AI Pipe cost (US dollars) for **2–8 November 2026 (UTC dates)**: the sum of the seven daily
`total.spend_usd` values from the public usage feed. Choose one of 17 cost ranges in the prediction market,
update it as new reports arrive, and explain the reasons.

- **Ranges:** 0–1¢, 1–2¢, 2–5¢, 5–10¢, 10–20¢, 20–50¢, 50¢–$1, $1–$2, $2–$5, $5–$10, $10–$20, $20–$50, $50–$100,
  $100–$200, $200–$500, $500–$1,000, $1,000+. Each range includes its lower limit and excludes its upper limit
  (exactly $2 is in $2–$5). The total is not rounded.
- **What counts:** each day's `total.spend_usd` once (do not add provider subtotals again). Only completed UTC days
  count; today's incomplete row is excluded. A missing day is not zero. Free models, personal keys and unmetered
  requests are not in the feed.
- **Timing:** the feed is published every 6 hours. The market checks hourly and keeps completed days after they leave
  the 7-day feed. Trading pauses if the latest report is more than 12 hours old.
- **Trading closes:** 9 November 2026, 00:00 UTC (05:30 IST). The teacher sets the result during 9–16 November.
- **Marks:** depend on how long each prediction is held and how new evidence is used, so start early and update
  promptly. Trading credits are optional practice points.
- **To submit:** after settlement (9–16 Nov), copy the **signed result token** from the market and paste it into
  the exam answer box. The sign-in code is not the result token.
- **Sign-in:** reload the exam page for a fresh personal sign-in code (it expires after 20 minutes). Keep it private.
  Anyone with it can trade as you. It is deliberately not stored here.

## Data source

Public feed: <https://stats.aipipe.org/usage.json> (documented in the
[AI Pipe README](https://github.com/sanand0/aipipe#public-usage-feed)). Published at 00:00, 06:00, 12:00 and
18:00 UTC. It holds the last 7 days. Collection started on 2026-10-15.

Each day has `date`, `complete`, `available`, `total.spend_usd`, token counts, per-provider subtotals and `iitm_users`.

## Snapshot: report published 2026-10-24 00:00 UTC

Saved as `usage_2026-10-24.json`.

| UTC day | total.spend_usd | IITM users |
|---|---|---|
| 2026-10-18 | 2.95 | 489 |
| 2026-10-19 | 0.23 | 2 |
| 2026-10-20 | 1.37 | 3 |
| 2026-10-21 | 3.27 | 22 |
| 2026-10-22 | 11.01 | 70 |
| 2026-10-23 | 13.80 | 85 |
| 2026-10-24 | (incomplete, not counted) | |

Six complete days total about **$32.63**, almost all OpenAI.

## Current prediction: $20–$50

Reasoning:
- Spending follows assignment activity. Quiet days cost $0.20–$1.40; days with heavy deadline work cost $11–$14.
- At this activity level a normal week falls somewhere around $5–$40, so **$20–$50** is the centre.
- **$10–$20** is the main downside risk (a quiet week with no deadline).
- **$50–$100** needs a large deadline inside the week, pushing several days above $10–$15. A plausible ceiling is
  about 7 × $14 ≈ $100, so that range is a long shot.

## Update plan

1. **Before 2 Nov:** check the course calendar for any assignment or exam deadline between 2 and 8 Nov.
   A deadline in that week makes $20–$50 or $50–$100 more likely; no deadline favours $10–$20.
2. **Every day or two:** run the check script. If spending stays at $10+ per day, consider moving up. If it drops
   back to under $1–$2 per day, consider $10–$20.
3. **During 2–8 Nov:** add the completed days, then estimate the rest from the recent daily rate.
   - If 2–4 Nov already exceed about $25, move to **$50–$100**.
   - If 2–4 Nov are under about $3, move to **$10–$20**.
4. Move as soon as the evidence clearly shifts, because marks depend on how long each prediction is held.
5. Record every check and decision in the log below.

## How to check

PowerShell (also saves a timestamped snapshot of the feed in this folder):

```powershell
powershell -ExecutionPolicy Bypass -File D:\projects\TDS\PR1\Q1\check_usage.ps1
```

One-line PowerShell alternative (recent days):

```powershell
foreach ($x in (Invoke-RestMethod https://stats.aipipe.org/usage.json).days) { "{0}  complete={1}  {2:N2}" -f $x.date, $x.complete, $x.total.spend_usd }
```

One-line PowerShell (running total of the target week):

```powershell
$d = (Invoke-RestMethod https://stats.aipipe.org/usage.json).days; $t = 0; foreach ($x in $d) { if ($x.date -ge '2026-11-02' -and $x.date -le '2026-11-08' -and $x.complete) { "{0}  {1:N2}" -f $x.date, $x.total.spend_usd; $t += $x.total.spend_usd } }; "total so far: {0:N2}" -f $t
```

Git Bash:

```bash
curl -s https://stats.aipipe.org/usage.json | python -c "import json,sys; d=json.load(sys.stdin)['days']; w=[x for x in d if '2026-11-02'<=x['date']<='2026-11-08' and x['complete']]; [print(x['date'], round(x['total']['spend_usd'],2)) for x in w]; print('total so far', round(sum(x['total']['spend_usd'] for x in w),2))"
```

Note: in PowerShell, `curl` is a shortcut for `Invoke-WebRequest`, so use the PowerShell commands there.
Paste multi-line commands carefully; if the line breaks get lost, use the one-line versions.

## Decision log

| Date (UTC) | Feed published | Evidence | Range held | Reason |
|---|---|---|---|---|
| 2026-10-24 | 2026-10-24 00:00 | 18–23 Oct: $32.63 over 6 days; $11–$14/day on 22–23 Oct | $20–$50 | Typical week at current activity centres on $20–$50 |

## Result

- Result token (after 9–16 Nov settlement): _not available yet_
