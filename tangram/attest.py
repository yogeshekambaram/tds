"""Get a signed Tangram completion token straight from the attestation API, without the board UI.

Usage: python attest.py '<state json>'
The state is the board's "State data" format: 7 x [x, y, orientation, reflected] in the order
large-a, large-b, medium, small-a, small-b, square, parallelogram.
"""
import base64
import json
import sys
from datetime import datetime, timezone

import requests

API = "https://tds-network-games.sanand.workers.dev/api/visual/attest"
EMAIL = "24ds3000035@ds.study.iitm.ac.in"

state = json.loads(sys.argv[1])
# Same bytes the board sends: compact JSON of the pose tuples, base64url without padding
state_bytes = json.dumps(state, separators=(",", ":")).encode()
state_b64 = base64.urlsafe_b64encode(state_bytes).decode().rstrip("=")

now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
# Honest client report: one event saying the state was imported by a script
events = [{"seq": 1, "client_at": now, "elapsed_ms": 0, "source": "script",
           "action": "import_state", "revision": 1, "ok": True}]
client_report = {"schema_version": 1, "started_at": now, "total_events": 1, "included_events": 1,
                 "omitted_events": 0, "truncated": False, "events": events}

resp = requests.post(API, json={
    "email": EMAIL,
    "game": "tangram",
    "game_version": 1,
    "config_id": "standard-seven-v1",
    "state_version": 1,
    "equivalence_version": 2,
    "state": state_b64,
    "client_report": client_report,
}, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)

data = resp.json()
print("HTTP", resp.status_code)
print("invalid:", data.get("invalid"), "errors:", data.get("errors"))
print("match_key:", data.get("match_key"))
receipt = data.get("receipt")
print("\nTOKEN (paste into Completion Token):\n" + (receipt if isinstance(receipt, str) else json.dumps(receipt)))
