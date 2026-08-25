#!/usr/bin/env python3
"""Milestone D, mission section 12: attempts to verify Kalshi's CURRENT
published fee schedule from genuine documentation/API sources -- read
only, no credentials.

    python scripts/validate_kalshi_fees_live.py

`kalshi/executable_price.py`'s fee-drag helpers have never had a
verified `fee_rate` wired in: this dev environment's own network egress
to docs.kalshi.com is blocked by the agent proxy (attempted twice
already, per that module's docstring). This script re-attempts the same
question from a GitHub-hosted runner (unrestricted egress), trying a
small set of plausible public, read-only fee-schedule URLs and reporting
exactly what each one returns -- HTTP status and a short excerpt only,
never assuming success. If every candidate fails or returns unusable
content, that is itself the honest answer this script exists to produce:
fees stay UNVERIFIED, and this script's own output is the evidence for
why, not a reason to guess.
"""

from __future__ import annotations

import re

import requests

TIMEOUT_SECONDS = 20.0

CANDIDATE_URLS = (
    "https://kalshi.com/docs/fee-schedule",
    "https://docs.kalshi.com",
    "https://docs.kalshi.com/reference/fees",
    "https://docs.kalshi.com/getting-started/fees",
    "https://trading-api.kalshi.com/trade-api/v2/exchange/schedule",
    "https://api.elections.kalshi.com/trade-api/v2/exchange/schedule",
)

_FEE_KEYWORD_RE = re.compile(r"fee", re.IGNORECASE)


def main() -> int:
    print("=== Kalshi fee-schedule verification attempt (live, read-only, no credentials) ===")
    any_usable = False
    for url in CANDIDATE_URLS:
        print(f"\n--- GET {url} ---")
        try:
            resp = requests.get(url, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            print(f"REQUEST FAILED: {exc.__class__.__name__}: {exc}")
            continue
        print(f"HTTP {resp.status_code}, content-type={resp.headers.get('content-type', '?')!r}")
        if resp.status_code != 200:
            print("Not usable (non-200).")
            continue
        body = resp.text
        fee_mentions = len(_FEE_KEYWORD_RE.findall(body))
        print(f"Body length: {len(body)} chars, 'fee'-keyword mentions: {fee_mentions}")
        if fee_mentions > 0:
            any_usable = True
            # Print a short, bounded excerpt around the first mention only.
            match = _FEE_KEYWORD_RE.search(body)
            if match is not None:
                start = max(0, match.start() - 200)
                end = min(len(body), match.start() + 400)
                print(f"Excerpt around first 'fee' mention:\n{body[start:end]!r}")

    print("\n=== SUMMARY ===")
    if any_usable:
        print(
            "At least one candidate URL returned HTTP 200 with fee-related text. A human must "
            "read the excerpt(s) above and manually confirm the exact current fee formula before "
            "kalshi/executable_price.py's fee_rate is ever set to anything but "
            "UNVERIFIED_PLACEHOLDER_FEE_RATE -- this script does not parse or trust a formula "
            "automatically."
        )
        return 0
    print(
        "No candidate URL returned usable fee-schedule content from this runner either. "
        "Kalshi's current CFB fee schedule remains genuinely UNVERIFIED -- do not set a real "
        "fee_rate anywhere in this codebase based on assumption or the MLB repo's fee constants."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
