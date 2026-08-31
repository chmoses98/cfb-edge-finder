"""Live, read-only probe of CFBD's account/quota surface as a RECOVERY
signal: does the deployed API actually serve the `GET /info` endpoint the
public server source (github.com/CFBD/cfb-api-v2) defines, is it really
unmetered, and what do a quota-429 and its headers actually look like?

*** WHY (2026-08-29+ CFBD quota outage) ***
The capture loop currently attempts a full football-state build every
5 minutes while locked out, burning ~1,150 pointless 429'd requests/day.
The server source shows `/info` and `/info/usage` in the quota
middleware's `ignoredPaths` (never metered) and `/info` returning
{monthlyLimit, remainingCalls, usedCalls, resetAt(=first of next month
00:00 UTC), tierName} -- the perfect zero-cost recovery probe. This
script verifies that against the REAL deployed API before the recovery
monitor is built on it.

READ-ONLY: prints diagnostics; writes nothing; never prints the API key
or any Authorization header. It spends AT MOST ONE metered call (the
/games probe, which while quota-locked is rejected un-metered anyway).
"""

from __future__ import annotations

import os
import sys

import requests

BASE = "https://api.collegefootballdata.com"

SAFE_HEADERS = (
    "X-CallLimit-Remaining",
    "Retry-After",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
    "RateLimit-Limit",
    "RateLimit-Remaining",
    "RateLimit-Reset",
    "Content-Type",
    "Date",
)


def _show(label: str, response: requests.Response, body_chars: int = 1200) -> None:
    print(f"\n{'=' * 72}\n{label}: HTTP {response.status_code}")
    for header in SAFE_HEADERS:
        if header in response.headers:
            print(f"  {header}: {response.headers[header]}")
    text = response.text or ""
    print(f"  body ({min(len(text), body_chars)}/{len(text)} chars): {text[:body_chars]}")


def main() -> int:
    api_key = os.environ.get("CFBD_API_KEY")
    if not api_key:
        print("ERROR: CFBD_API_KEY not set.", file=sys.stderr)
        return 2
    auth = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

    # 1. Unauthenticated /info -- expected null/401-ish; proves gateway up
    #    and that the endpoint exists at all, without spending anything.
    r = requests.get(f"{BASE}/info", timeout=30)
    _show("GET /info (unauthenticated)", r)

    # 2. Authenticated /info -- THE candidate probe. Per server source:
    #    unmetered; body carries monthlyLimit/remainingCalls/usedCalls/
    #    resetAt/tierName even while the quota is exhausted.
    r_info = requests.get(f"{BASE}/info", headers=auth, timeout=30)
    _show("GET /info (authenticated)", r_info)

    # 3. Call it AGAIN to prove it is unmetered: remainingCalls (and the
    #    X-CallLimit-Remaining header) must not decrease between two
    #    consecutive /info calls.
    r_info2 = requests.get(f"{BASE}/info", headers=auth, timeout=30)
    _show("GET /info (authenticated, repeat -- unmetered check)", r_info2)
    try:
        a = r_info.json() or {}
        b = r_info2.json() or {}
        unchanged = a.get("remainingCalls") == b.get("remainingCalls")
        verdict = "UNCHANGED: /info is unmetered" if unchanged else "DECREASED: /info IS metered"
        print(
            f"\nunmetered check: remainingCalls {a.get('remainingCalls')} -> {b.get('remainingCalls')} "
            f"({verdict})"
        )
    except ValueError:
        print("\nunmetered check: /info body not JSON -- cannot compare")

    # 4. Authenticated /info/usage -- recent-usage telemetry shape.
    r = requests.get(f"{BASE}/info/usage", headers=auth, params={"days": 7, "limit": 10}, timeout=30)
    _show("GET /info/usage?days=7&limit=10 (authenticated)", r, body_chars=2000)

    # 5. One METERED endpoint, to record what the real quota-429 (or a
    #    real success, if quota has reset) looks like: status, body,
    #    headers. /games with a week filter keeps a success response
    #    small. While exhausted this request is rejected BEFORE metering
    #    (server source: checkCallQuotas), so it costs nothing.
    r = requests.get(
        f"{BASE}/games", headers=auth, params={"year": 2026, "week": 1, "division": "fbs"}, timeout=30
    )
    _show("GET /games?year=2026&week=1 (authenticated, METERED probe)", r, body_chars=400)

    print("\nSTATUS: READ-ONLY CFBD quota probe. Nothing captured or written; key never printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
