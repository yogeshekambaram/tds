# AI Pipe usage check for the 2-8 November 2026 prediction market.
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File .\check_usage.ps1
# Saves a timestamped snapshot of the feed next to this script, prints every day in the feed,
# then the running total of completed target days (2-8 Nov, UTC).

$feed = Invoke-RestMethod https://stats.aipipe.org/usage.json
$stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHHmm") + "Z"
$feed | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $PSScriptRoot "usage_$stamp.json")

"Published: $($feed.published_at)"
foreach ($x in $feed.days) {
    "{0}  complete={1}  {2:N2}  iitm_users={3}" -f $x.date, $x.complete, $x.total.spend_usd, $x.iitm_users
}

$t = 0; $n = 0
foreach ($x in $feed.days) {
    if ($x.date -ge '2026-11-02' -and $x.date -le '2026-11-08' -and $x.complete) { $t += $x.total.spend_usd; $n++ }
}
"Target week (2-8 Nov): {0} of 7 completed days in this feed, total so far {1:N2} USD" -f $n, $t
"Note: the feed keeps only 7 days; keep your earlier snapshots so no completed target day is lost."
