#!/usr/bin/env python3
"""Probe R4: is the milestone path a usable SECOND discovery source?

    python scripts/probe_kalshi_milestone_reconciliation.py

Read-only, credential-free, run from an Actions runner (this dev
environment has no egress to Kalshi -- see
investigate_kalshi_milestones_live.py's docstring).

*** WHY A SECOND SOURCE MATTERS ***
Probe R3 established the series->events->markets path works and finds 271
physical games / 1,946 open events. But a single discovery path has a
silent-failure mode this mission explicitly forbids: if a series is missed
(new ticker, heuristic miss) its markets are absent from the catalog and
NOTHING in the output says so. Two independent paths whose union is the
menu, and whose DISAGREEMENT is reported, removes that blind spot.

R3 also found the milestone endpoint is usable after all, just not as
documented: there is no `competition` field at all (the mission's
`competition="College Football"` filter matched nothing because the key
does not exist), the league lives at `details.league == "NCAAFB"`, and
`type=football_game` narrows 24,000+ milestones to ~1,286 -- about 7
pages, i.e. cheap enough to run every cycle.

QUESTIONS THIS PROBE MUST SETTLE
  1. Do NCAAFB milestones for CURRENT/UPCOMING games carry
     related_event_tickers that actually name KXNCAAF* event tickers? (R3
     only saw truncated 2025-season samples.)
  2. Does the milestone path find any event the series sweep misses, and
     vice versa? That reconciliation is the completeness claim.
  3. What does the `milestones` key inside a /events response contain --
     a free per-event game linkage?
  4. What is the FULL status vocabulary of CFB events and markets? The
     catalog must count open/unopened/paused/closed/settled, and R3 only
     ever asked for status=open.
  5. What is the largest market count on a single event? Proof that
     /markets pagination is genuinely required, not theoretical.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
TIMEOUT_SECONDS = 25.0
PAGE_LIMIT = 200
MAX_PAGES = 200

GAME_KEY_RE = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<game>\d{2}[A-Z]{3}\d{2}[A-Z0-9]+)$")


def _get(path: str, params: dict[str, Any] | None = None, quiet: bool = False) -> tuple[int, Any]:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    for attempt in range(4):
        try:
            resp = requests.get(
                f"{BASE_URL}{path}", params=clean, headers={"Accept": "application/json"}, timeout=TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            if attempt == 3:
                print(f"    TRANSPORT_ERROR {path}: {exc}")
                return -1, None
            time.sleep(min(2**attempt, 8))
            continue
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt == 3:
                return resp.status_code, None
            time.sleep(min(2**attempt, 8))
            continue
        if resp.status_code >= 400 and not quiet:
            print(f"    HTTP_{resp.status_code} {path} params={clean} body={resp.text[:200]!r}")
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, None
    return -1, None


def _paginate(path: str, params: dict[str, Any], list_key: str) -> tuple[list[dict], bool]:
    items: list[dict] = []
    cursor: str | None = None
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        p = dict(params, limit=PAGE_LIMIT)
        if cursor:
            p["cursor"] = cursor
        status, body = _get(path, p)
        if status != 200 or not isinstance(body, dict):
            print(f"    PAGE_FAILURE {path} page={page} status={status}")
            return items, False
        items.extend(i for i in (body.get(list_key) or []) if isinstance(i, dict))
        cursor = body.get("cursor") or None
        if not cursor:
            return items, True
        if cursor in seen:
            return items, False
        seen.add(cursor)
    return items, False


def _hdr(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def q1_ncaaf_milestones() -> list[dict]:
    _hdr("1. NCAAFB MILESTONES VIA type=football_game")
    ms, complete = _paginate("/milestones", {"type": "football_game"}, "milestones")
    print(f"type=football_game -> {len(ms)} milestones (complete={complete})")

    leagues = Counter(str((m.get("details") or {}).get("league")) for m in ms)
    print(f"league distribution: {dict(leagues)}")

    ncaaf = [m for m in ms if str((m.get("details") or {}).get("league")) == "NCAAFB"]
    print(f"NCAAFB milestones: {len(ncaaf)}")

    statuses = Counter(str((m.get("details") or {}).get("status")) for m in ncaaf)
    print(f"NCAAFB milestone detail.status: {dict(statuses)}")
    seasons = Counter(
        f"{((m.get('details') or {}).get('season') or {}).get('year')}-"
        f"{((m.get('details') or {}).get('season') or {}).get('type')}"
        for m in ncaaf
    )
    print(f"NCAAFB milestone seasons: {dict(sorted(seasons.items()))}")

    now = datetime.now(UTC)
    upcoming = []
    for m in ncaaf:
        sd = str(m.get("start_date") or "")
        try:
            when = datetime.fromisoformat(sd.replace("Z", "+00:00"))
        except ValueError:
            continue
        if when >= now:
            upcoming.append((when, m))
    upcoming.sort(key=lambda kv: kv[0])
    print(f"\nNCAAFB milestones with start_date in the FUTURE: {len(upcoming)}")

    with_events = [m for _w, m in upcoming if (m.get("related_event_tickers") or [])]
    print(f"  ...of which carry related_event_tickers: {len(with_events)}")

    print("\n--- FULL RAW JSON of the next upcoming NCAAFB milestone ---")
    if upcoming:
        print(json.dumps(upcoming[0][1], indent=2, sort_keys=True)[:3000])

    print("\n--- related_event_tickers for the next 8 upcoming NCAAFB milestones ---")
    for when, m in upcoming[:8]:
        rel = m.get("related_event_tickers") or []
        prim = m.get("primary_event_tickers") or []
        print(f"\n  {when.isoformat()}  title={str(m.get('title'))[:60]!r}")
        print(f"    primary({len(prim)}): {prim[:6]}")
        print(f"    related({len(rel)}): {rel[:20]}")
        print(f"    details keys: {sorted((m.get('details') or {}).keys())}")
    return ncaaf


def q2_reconcile(ncaaf_milestones: list[dict]) -> None:
    _hdr("2. RECONCILIATION: milestone path vs series path")

    # Series path (same logic as the production sweep).
    series, _c = _paginate("/series", {"category": "Sports"}, "series")
    cfb_series = []
    for s in series:
        title = str(s.get("title") or "").upper()
        ticker = str(s.get("ticker") or "")
        tags = [str(t).upper() for t in (s.get("tags") or [])]
        if (
            "COLLEGE FOOTBALL" in title
            or ticker.startswith(("KXNCAAF", "KXCFB", "KXCFP"))
            or ("FOOTBALL" in tags and "NCAA" in ticker)
        ):
            cfb_series.append(ticker)

    series_events: set[str] = set()
    for t in cfb_series:
        evs, complete = _paginate("/events", {"series_ticker": t, "status": "open"}, "events")
        if not complete:
            print(f"  !! incomplete sweep for {t}")
        for e in evs:
            series_events.add(str(e.get("event_ticker") or e.get("ticker")))
    print(f"series path: {len(cfb_series)} CFB series -> {len(series_events)} open events")

    milestone_events: set[str] = set()
    for m in ncaaf_milestones:
        for t in (m.get("primary_event_tickers") or []) + (m.get("related_event_tickers") or []):
            if isinstance(t, str):
                milestone_events.add(t)
    print(f"milestone path: {len(ncaaf_milestones)} NCAAFB milestones -> {len(milestone_events)} referenced events")

    only_series = series_events - milestone_events
    only_milestone = milestone_events - series_events
    both = series_events & milestone_events
    print(f"\nin BOTH:                  {len(both)}")
    print(f"ONLY in series path:      {len(only_series)}")
    print(f"ONLY in milestone path:   {len(only_milestone)}")

    print("\n--- sample ONLY-in-milestone event tickers (would the series path miss them?) ---")
    for t in sorted(only_milestone)[:25]:
        print(f"      {t}")
    print("\n--- sample ONLY-in-series event tickers ---")
    for t in sorted(only_series)[:25]:
        print(f"      {t}")

    # Are only-in-milestone tickers actually still live? A closed event is
    # not a miss -- the series sweep asked for status=open on purpose.
    print("\n--- status of up to 12 ONLY-in-milestone events ---")
    for t in sorted(only_milestone)[:12]:
        st, body = _get(f"/events/{t}", quiet=True)
        ev = (body.get("event") or {}) if isinstance(body, dict) else {}
        mk, _c = _paginate("/markets", {"event_ticker": t}, "markets")
        statuses = Counter(str(m.get("status")) for m in mk)
        print(f"      {t:44} HTTP {st} series={ev.get('series_ticker')} markets={len(mk)} statuses={dict(statuses)}")


def q3_events_milestones_key() -> None:
    _hdr("3. THE `milestones` KEY INSIDE A /events RESPONSE")
    st, body = _get("/events", {"series_ticker": "KXNCAAFGAME", "status": "open", "limit": 3})
    if isinstance(body, dict):
        print(f"top-level keys: {sorted(body.keys())}")
        print("\n--- raw `milestones` value ---")
        print(json.dumps(body.get("milestones"), indent=2, sort_keys=True)[:2500])
        evs = body.get("events") or []
        if evs:
            print("\n--- one event from this list response (full) ---")
            print(json.dumps(evs[0], indent=2, sort_keys=True)[:1800])


def q4_status_vocabulary() -> None:
    _hdr("4. FULL STATUS VOCABULARY FOR CFB EVENTS AND MARKETS")
    for status in (None, "open", "unopened", "closed", "settled", "paused", "determined", "finalized"):
        st, body = _get("/events", {"series_ticker": "KXNCAAFGAME", "status": status, "limit": 200}, quiet=True)
        n = len(body.get("events") or []) if isinstance(body, dict) else 0
        print(f"  /events?series_ticker=KXNCAAFGAME&status={status!s:12} -> HTTP {st} events={n}")

    print()
    for status in (None, "open", "unopened", "closed", "settled", "paused", "active", "determined", "finalized"):
        st, body = _get("/markets", {"series_ticker": "KXNCAAFGAME", "status": status, "limit": 200}, quiet=True)
        n = len(body.get("markets") or []) if isinstance(body, dict) else 0
        print(f"  /markets?series_ticker=KXNCAAFGAME&status={status!s:12} -> HTTP {st} markets={n}")

    # Observed market-status values across an unfiltered series sweep.
    mk, complete = _paginate("/markets", {"series_ticker": "KXNCAAFGAME"}, "markets")
    print(f"\nunfiltered KXNCAAFGAME sweep: {len(mk)} markets (complete={complete})")
    print(f"observed market status values: {dict(Counter(str(m.get('status')) for m in mk))}")
    print(f"observed result values:       {dict(Counter(str(m.get('result')) for m in mk).most_common(10))}")

    # And for events.
    evs, complete = _paginate("/events", {"series_ticker": "KXNCAAFGAME"}, "events")
    print(f"\nunfiltered KXNCAAFGAME event sweep: {len(evs)} events (complete={complete})")
    comps = Counter(str((e.get("product_metadata") or {}).get("competition")) for e in evs)
    print(f"product_metadata.competition values: {dict(comps)}")
    scopes = Counter(str((e.get("product_metadata") or {}).get("competition_scope")) for e in evs)
    print(f"product_metadata.competition_scope values: {dict(scopes.most_common(20))}")


def q5_largest_event() -> None:
    _hdr("5. LARGEST MARKET COUNT ON ONE EVENT (is pagination genuinely required?)")
    biggest: list[tuple[int, str]] = []
    events_by_game: dict[str, list[str]] = defaultdict(list)
    for series in ("KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFTEAMTOTAL", "KXNCAAF1HSPREAD", "KXNCAAFGAME"):
        evs, _c = _paginate("/events", {"series_ticker": series, "status": "open"}, "events")
        for e in evs[:14]:
            t = str(e.get("event_ticker") or e.get("ticker"))
            m = GAME_KEY_RE.match(t)
            if m:
                events_by_game[m.group("game")].append(t)
            mk, complete = _paginate("/markets", {"event_ticker": t}, "markets")
            biggest.append((len(mk), f"{t} (complete={complete})"))
    biggest.sort(reverse=True)
    print("largest single-event market counts:")
    for n, t in biggest[:15]:
        flag = "  <<< EXCEEDS ONE PAGE (200)" if n > 200 else ""
        print(f"  {n:5}  {t}{flag}")
    over = [b for b in biggest if b[0] > 200]
    print(f"\nevents exceeding one 200-item page: {len(over)} of {len(biggest)} sampled")
    if not over:
        print("NOTE: no sampled event exceeded 200 markets, but per-event pagination stays mandatory --")
        print("      Kalshi adds ladder rungs as a game approaches, and an unpaged sweep would")
        print("      silently truncate the first event that crosses the boundary.")


def main() -> int:
    print(f"Kalshi milestone-reconciliation probe R4 @ {datetime.now(UTC).isoformat()}")
    ncaaf = q1_ncaaf_milestones()
    q3_events_milestones_key()
    q4_status_vocabulary()
    q5_largest_event()
    if ncaaf:
        q2_reconcile(ncaaf)
    _hdr("PROBE COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
