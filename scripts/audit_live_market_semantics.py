"""Week 1 readiness audit: live, read-only payload probe for the
persistently-unparsed market population.

*** WHY THIS EXISTS (evidence, not speculation) ***
The prospective corpus (research-data branch, 2026 observations) shows a
hard per-game split that no unit test reproduces:

  - EVERY market of EVERY game kicking off 2026-08-29/30 -- the imminent
    opening slate -- has pricing_status=not_priced with
    parse_status=unresolved and family=None, across ALL THREE families
    (KXNCAAFGAME / KXNCAAFSPREAD / KXNCAAFTOTAL), at every checkpoint
    ever attempted (EARLY_OPEN, T_3D, T_24H).
  - Games kicking off 2026-09-03+ price normally with the same code.
  - A handful of later marquee/neutral games (e.g. CLEMLSU, OSU@Texas)
    fail ONLY their KXNCAAFGAME (winner) contracts.

A total market's title ("Over 80.5 points scored") contains no team
names, so a per-game total-parse failure implies the PAYLOAD for those
events differs structurally (title grammar, floor_strike convention, or
missing fields) -- something only a live fetch can show. This dev
environment's egress to Kalshi is policy-blocked, so this script runs
from a GitHub Actions runner via workflow_dispatch, exactly like the
other validate_* scripts.

READ-ONLY: public unauthenticated GETs only; prints results; writes
nothing; no trading endpoints; no credentials.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from cfb_edge_finder.data.kalshi_client import KalshiClient
from cfb_edge_finder.kalshi.contract_semantics import (
    parse_spread_market,
    parse_total_market,
    parse_winner_market,
)
from cfb_edge_finder.schemas.common import MarketFamily

SERIES_TO_FAMILY = {
    "KXNCAAFGAME": MarketFamily.MONEYLINE,
    "KXNCAAFSPREAD": MarketFamily.SPREAD,
    "KXNCAAFTOTAL": MarketFamily.TOTAL,
}

# Persistently-unparsed events (imminent slate) + never-parsed winner
# events + a healthy Sep control group, from the genuine corpus.
FAILING_EVENT_SUFFIXES = {
    "26AUG29MEMUNLV",
    "26AUG29HAWSTAN",
    "26AUG29UNCTCU",
    "26AUG29SACEMU",
    "26AUG29JVSTNDSU",
    "26SEP05CLEMLSU",  # winner-only failure
}
CONTROL_EVENT_SUFFIXES = {
    "26SEP04SJSUEMU",
    "26SEP05LIBJMU",
}
INTERESTING = FAILING_EVENT_SUFFIXES | CONTROL_EVENT_SUFFIXES

FIELDS_OF_INTEREST = [
    "ticker",
    "title",
    "yes_sub_title",
    "no_sub_title",
    "subtitle",
    "floor_strike",
    "cap_strike",
    "strike_type",
    "status",
]


def _parse(family: MarketFamily, market: dict):
    title = str(market.get("title", "") or "")
    floor = market.get("floor_strike")
    floor_f = float(floor) if isinstance(floor, (int, float)) else None
    rules = market.get("rules_primary")
    if family == MarketFamily.SPREAD:
        return parse_spread_market(title, floor_f)
    if family == MarketFamily.TOTAL:
        return parse_total_market(title, floor_f)
    return parse_winner_market(title, rules)


def main() -> int:
    client = KalshiClient()
    exit_notes: list[str] = []
    for series, family in SERIES_TO_FAMILY.items():
        markets = client.fetch_markets(series_ticker=series)
        active = [m for m in markets if str(m.get("status", "")).lower() == "active"]
        print(f"\n{'=' * 78}\nSERIES {series}: {len(markets)} markets, {len(active)} active")

        outcome_by_event: dict[str, Counter] = {}
        missing_rules: Counter = Counter()
        for market in active:
            event = str(market.get("event_ticker", ""))
            suffix = event.split("-", 1)[1] if "-" in event else event
            parsed = _parse(family, market)
            outcome = "OK" if parsed.reason is None else parsed.reason.value
            outcome_by_event.setdefault(suffix, Counter())[outcome] += 1
            if not market.get("rules_primary"):
                missing_rules[suffix] += 1

        n_events_all_ok = sum(1 for c in outcome_by_event.values() if set(c) == {"OK"})
        n_events_any_fail = sum(1 for c in outcome_by_event.values() if set(c) != {"OK"})
        print(f"events fully parsing: {n_events_all_ok}; events with parse failures: {n_events_any_fail}")
        if missing_rules:
            print(f"events with markets MISSING rules_primary: {len(missing_rules)}")

        # Show every failing event's outcome mix (bounded output).
        shown = 0
        for suffix, counts in sorted(outcome_by_event.items()):
            if set(counts) != {"OK"} and shown < 40:
                print(f"  FAILS {suffix}: {dict(counts)}  missing_rules={missing_rules.get(suffix, 0)}")
                shown += 1

        # Full payload evidence for the interesting events: first two
        # markets each, all semantics-relevant fields verbatim.
        for market in active:
            event = str(market.get("event_ticker", ""))
            suffix = event.split("-", 1)[1] if "-" in event else event
            if suffix not in INTERESTING:
                continue
            key = f"{series}:{suffix}"
            if exit_notes.count(key) >= 2:
                continue
            exit_notes.append(key)
            payload = {k: market.get(k) for k in FIELDS_OF_INTEREST}
            payload["rules_primary"] = (str(market.get("rules_primary"))[:220]) if market.get("rules_primary") else None
            parsed = _parse(family, market)
            print(f"  EVIDENCE {json.dumps(payload, default=str)}")
            print(f"    -> parse: {'OK' if parsed.reason is None else parsed.reason.value} | {parsed.detail[:200]}")

    print("\nSTATUS: READ-ONLY live semantics audit. Nothing captured, priced, or written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
