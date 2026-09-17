#!/usr/bin/env python3
"""Live, read-only investigation of Kalshi's CURRENT college-football
market surface, run from a GitHub Actions runner.

    python scripts/investigate_kalshi_milestones_live.py

*** WHY THIS EXISTS ***
The market-discovery pivot rests on one empirical question that no amount
of code reading can answer: is Kalshi's `/milestones` endpoint the
strongest canonical way to identify one physical college-football game and
every Kalshi event attached to it? This script asks Kalshi directly and
prints exactly what comes back, so the discovery architecture is designed
against observed behavior rather than assumption.

*** WHY A RUNNER AND NOT THE DEV ENVIRONMENT ***
This repository's dev environment has no network egress to Kalshi's API
hosts (organization policy: the agent proxy answers 403 to CONNECT for
api.elections.kalshi.com, trading-api.kalshi.com and api.kalshi.com alike).
That is the same constraint validate-kalshi-cfb-live.yml already works
around, and this script follows that established pattern.

*** READ-ONLY, CREDENTIAL-FREE ***
Every call is an unauthenticated GET against Kalshi's public market-data
endpoints. No Authorization header is ever sent, no order/portfolio
endpoint is ever touched, and there is no secret to leak.

What it answers, in order:
  1. Which /milestones filter combinations work, and what a milestone
     actually contains (full raw JSON for a real one).
  2. Whether related_event_tickers resolve to real fetchable events, and
     what an event's raw JSON contains.
  3. Whether an event's inline market list is complete, or whether
     GET /markets?event_ticker=... returns more (the pagination question).
  4. The complete raw field inventory of a real market, so the catalog
     schema preserves everything that exists.
  5. THE COMPLETENESS QUESTION: does milestone-driven discovery find every
     market that a brute-force series sweep finds? Any market reachable by
     series sweep but attached to no milestone is a hole in the
     milestone-only architecture, and this script reports it explicitly.
  6. Whether novel/unknown CFB series exist beyond the known KXNCAAF* set.
  7. What Kalshi's multivariate/combo event surface exposes for CFB.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
TIMEOUT_SECONDS = 25.0
MAX_PAGES = 60

# Known CFB series -- used HERE only as a brute-force control group to
# audit milestone-driven discovery against. This is emphatically not a
# discovery allowlist; the whole point is to measure what it would miss.
CONTROL_SERIES_TICKERS = (
    "KXNCAAFGAME",
    "KXNCAAFSPREAD",
    "KXNCAAFTOTAL",
    "KXNCAAF1HWINNER",
    "KXNCAAF1HSPREAD",
    "KXNCAAF1HTOTAL",
    "KXNCAAFMARGIN",
    "KXNCAAFTEAMTOTAL",
)


def _get(path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    """Bounded-retry GET. Returns (status_code, parsed_body_or_None).
    status_code -1 means a transport failure that survived the retries."""
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    for attempt in range(4):
        try:
            resp = requests.get(
                f"{BASE_URL}{path}",
                params=clean,
                headers={"Accept": "application/json"},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            if attempt == 3:
                print(f"    TRANSPORT_ERROR {path} {clean}: {exc}")
                return -1, None
            time.sleep(min(1.0 * (2**attempt), 8.0))
            continue
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt == 3:
                return resp.status_code, None
            retry_after = resp.headers.get("Retry-After")
            delay = min(float(retry_after), 8.0) if retry_after and retry_after.isdigit() else min(1.0 * (2**attempt), 8.0)
            time.sleep(delay)
            continue
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, None
    return -1, None


def _paginate(path: str, params: dict[str, Any], list_key: str) -> tuple[list[dict], bool, int]:
    """Returns (items, complete, pages_fetched). `complete` is False if any
    page failed or the cursor never terminated -- the distinction this whole
    mission turns on: a failure must never look like an empty result."""
    items: list[dict] = []
    cursor: str | None = None
    for page in range(MAX_PAGES):
        page_params = dict(params, limit=1000)
        if cursor:
            page_params["cursor"] = cursor
        status, body = _get(path, page_params)
        if status != 200 or not isinstance(body, dict):
            print(f"    PAGE_FAILURE {path} page={page} status={status}")
            return items, False, page
        page_items = body.get(list_key) or []
        items.extend(page_items)
        cursor = body.get("cursor") or None
        if not cursor or not page_items:
            return items, True, page + 1
    print(f"    PAGINATION_CAP_HIT {path} after {MAX_PAGES} pages")
    return items, False, MAX_PAGES


def _hdr(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def section_1_milestones() -> list[dict]:
    _hdr("1. /milestones ENDPOINT PROBE")

    status, body = _get("/exchange/status")
    print(f"GET /exchange/status -> HTTP {status}  body={json.dumps(body)[:200]}")

    # Does the endpoint exist at all, and which filter spellings work?
    probes = [
        ("bare", {}),
        ("category only", {"category": "Sports"}),
        ("documented triple", {"category": "Sports", "competition": "College Football", "type": "football_game"}),
        ("type only", {"type": "football_game"}),
        ("competition only", {"competition": "College Football"}),
        ("underscore competition", {"category": "Sports", "competition": "college_football", "type": "football_game"}),
        ("with limit", {"category": "Sports", "competition": "College Football", "type": "football_game", "limit": 10}),
    ]
    for label, params in probes:
        status, body = _get("/milestones", params)
        count = len(body.get("milestones") or []) if isinstance(body, dict) else 0
        keys = sorted(body.keys()) if isinstance(body, dict) else None
        print(f"  [{label:24}] {params}\n      -> HTTP {status} milestones={count} top_level_keys={keys}")

    # Date-window parameter spellings (a slate needs "upcoming only").
    now = datetime.now(UTC)
    window = {
        "min_start_date": now.isoformat().replace("+00:00", "Z"),
        "max_start_date": (now + timedelta(days=14)).isoformat().replace("+00:00", "Z"),
    }
    for label, extra in [
        ("min/max_start_date", window),
        ("min_date/max_date", {"min_date": window["min_start_date"], "max_date": window["max_start_date"]}),
        ("start_date_after", {"start_date_after": window["min_start_date"]}),
    ]:
        params = {"category": "Sports", "competition": "College Football", "type": "football_game", **extra}
        status, body = _get("/milestones", params)
        count = len(body.get("milestones") or []) if isinstance(body, dict) else 0
        print(f"  [date filter {label:20}] -> HTTP {status} milestones={count}")

    # Full sweep with the documented triple.
    _hdr("1b. FULL CFB MILESTONE SWEEP (documented filter triple, paginated)")
    milestones, complete, pages = _paginate(
        "/milestones",
        {"category": "Sports", "competition": "College Football", "type": "football_game"},
        "milestones",
    )
    print(f"TOTAL CFB milestones: {len(milestones)}  pagination_complete={complete}  pages={pages}")

    if not milestones:
        print("!! NO MILESTONES RETURNED -- milestone-driven discovery is NOT viable as specified.")
        return []

    print("\n--- FULL RAW JSON of first milestone (complete field inventory) ---")
    print(json.dumps(milestones[0], indent=2, sort_keys=True))

    all_keys: Counter[str] = Counter()
    for m in milestones:
        all_keys.update(m.keys())
    print(f"\n--- Key frequency across all {len(milestones)} milestones ---")
    for key, n in sorted(all_keys.items()):
        print(f"  {key:30} present on {n}/{len(milestones)}")

    # How many events does a milestone actually link?
    rel_counts = Counter(len(m.get("related_event_tickers") or []) for m in milestones)
    prim_counts = Counter(len(m.get("primary_event_tickers") or []) for m in milestones)
    print(f"\nrelated_event_tickers count distribution: {dict(sorted(rel_counts.items()))}")
    print(f"primary_event_tickers count distribution: {dict(sorted(prim_counts.items()))}")

    # Do primary ever fall outside related? (Determines whether we must union.)
    outside = 0
    for m in milestones:
        rel = set(m.get("related_event_tickers") or [])
        prim = set(m.get("primary_event_tickers") or [])
        if prim - rel:
            outside += 1
    print(f"milestones whose primary_event_tickers are NOT all inside related_event_tickers: {outside}")
    print("  (non-zero => discovery MUST union primary+related, not trust related alone)")

    # Time span, and what the titles look like.
    print("\n--- Sample milestone titles / start_dates (first 25) ---")
    for m in milestones[:25]:
        rel_n = len(m.get("related_event_tickers") or [])
        print(f"  {str(m.get('start_date'))[:16]}  events={rel_n:3}  {str(m.get('title'))[:70]}")

    return milestones


def section_2_events_and_markets(milestones: list[dict]) -> tuple[set[str], dict[str, dict]]:
    """Walk milestone -> events -> markets. Returns (all market tickers,
    ticker -> market dict) discovered the milestone way."""
    _hdr("2. MILESTONE -> EVENT -> MARKET WALK")

    # Pick a cross-section: the milestone with the most related events, the
    # fewest, and a few in between -- marquee vs thin game coverage.
    ordered = sorted(milestones, key=lambda m: -len(m.get("related_event_tickers") or []))
    sample = [m for m in (ordered[0], ordered[len(ordered) // 2], ordered[-1]) if m]
    seen_ids = set()
    unique_sample = []
    for m in sample:
        if id(m) not in seen_ids:
            seen_ids.add(id(m))
            unique_sample.append(m)

    for idx, m in enumerate(unique_sample):
        rel = list(dict.fromkeys((m.get("primary_event_tickers") or []) + (m.get("related_event_tickers") or [])))
        print(f"\n--- SAMPLE MILESTONE {idx + 1}: {str(m.get('title'))[:60]!r} ({len(rel)} events) ---")
        for ev_ticker in rel[:6]:
            status, body = _get(f"/events/{ev_ticker}")
            if status != 200 or not isinstance(body, dict):
                print(f"  event {ev_ticker}: HTTP {status}  <<< FETCH FAILED")
                continue
            event = body.get("event") or {}
            inline = body.get("markets") or event.get("markets") or []
            listed, complete, pages = _paginate("/markets", {"event_ticker": ev_ticker}, "markets")
            print(
                f"  event {ev_ticker:42} series={event.get('series_ticker')!s:18} "
                f"inline_markets={len(inline):4} /markets?event_ticker={len(listed):4} "
                f"(pages={pages} complete={complete})"
            )
            if len(inline) != len(listed):
                print(
                    f"      *** INLINE LIST IS NOT SUFFICIENT: inline={len(inline)} vs listed={len(listed)} "
                    f"-- discovery must use GET /markets?event_ticker with pagination"
                )
            if idx == 0 and ev_ticker == rel[0]:
                print("\n  --- FULL RAW JSON of this event (field inventory) ---")
                printable = {k: v for k, v in event.items() if k != "markets"}
                print(json.dumps(printable, indent=2, sort_keys=True)[:3000])

    _hdr("2b. FULL MILESTONE-DRIVEN SWEEP (every CFB milestone, every event, every market)")
    market_by_ticker: dict[str, dict] = {}
    event_fetch_failures: list[str] = []
    pagination_failures: list[str] = []
    events_seen: set[str] = set()
    milestone_event_map: dict[str, list[str]] = {}

    for m in milestones:
        mid = str(m.get("id") or m.get("title"))
        rel = list(dict.fromkeys((m.get("primary_event_tickers") or []) + (m.get("related_event_tickers") or [])))
        milestone_event_map[mid] = rel
        for ev_ticker in rel:
            if ev_ticker in events_seen:
                continue
            events_seen.add(ev_ticker)
            markets, complete, _pages = _paginate("/markets", {"event_ticker": ev_ticker}, "markets")
            if not complete:
                pagination_failures.append(ev_ticker)
            if not markets and not complete:
                event_fetch_failures.append(ev_ticker)
            for mk in markets:
                t = mk.get("ticker")
                if t:
                    market_by_ticker[t] = mk

    print(f"milestones swept:            {len(milestones)}")
    print(f"distinct events referenced:  {len(events_seen)}")
    print(f"event/market fetch failures: {len(event_fetch_failures)} {event_fetch_failures[:10]}")
    print(f"pagination failures:         {len(pagination_failures)} {pagination_failures[:10]}")
    print(f"distinct markets discovered: {len(market_by_ticker)}")

    series_dist = Counter(mk.get("series_ticker") or "<none>" for mk in market_by_ticker.values())
    print("\n--- SERIES TICKER DISTRIBUTION of milestone-discovered markets ---")
    for s, n in series_dist.most_common():
        print(f"  {s:28} {n:6}")

    status_dist = Counter(mk.get("status") or "<none>" for mk in market_by_ticker.values())
    print(f"\n--- STATUS DISTRIBUTION --- {dict(status_dist)}")

    type_dist = Counter(mk.get("market_type") or "<none>" for mk in market_by_ticker.values())
    strike_dist = Counter(mk.get("strike_type") or "<none>" for mk in market_by_ticker.values())
    print(f"--- market_type DISTRIBUTION --- {dict(type_dist)}")
    print(f"--- strike_type DISTRIBUTION --- {dict(strike_dist)}")

    return set(market_by_ticker), market_by_ticker


def section_3_market_fields(market_by_ticker: dict[str, dict]) -> None:
    _hdr("3. RAW MARKET FIELD INVENTORY")
    if not market_by_ticker:
        print("no markets to inspect")
        return

    all_keys: Counter[str] = Counter()
    for mk in market_by_ticker.values():
        all_keys.update(mk.keys())
    total = len(market_by_ticker)
    print(f"--- Key frequency across all {total} markets ---")
    for key, n in sorted(all_keys.items()):
        print(f"  {key:34} {n:6}/{total}")

    # Print one full market per series so every contract grammar is visible.
    by_series: dict[str, dict] = {}
    for mk in market_by_ticker.values():
        s = mk.get("series_ticker") or "<none>"
        if s not in by_series:
            by_series[s] = mk
    for s, mk in sorted(by_series.items()):
        print(f"\n--- FULL RAW MARKET JSON, series={s} ---")
        print(json.dumps(mk, indent=2, sort_keys=True))

    # Is there anything on the detail endpoint the list endpoint lacks?
    sample_ticker = next(iter(market_by_ticker))
    status, body = _get(f"/markets/{sample_ticker}")
    if status == 200 and isinstance(body, dict):
        detail = body.get("market") or body
        list_keys = set(market_by_ticker[sample_ticker].keys())
        detail_keys = set(detail.keys())
        print(f"\n--- DETAIL vs LIST endpoint for {sample_ticker} ---")
        print(f"  only in detail: {sorted(detail_keys - list_keys)}")
        print(f"  only in list:   {sorted(list_keys - detail_keys)}")

    # Orderbook: is quoted SIZE available anywhere?
    status, body = _get(f"/markets/{sample_ticker}/orderbook")
    print(f"\n--- GET /markets/{sample_ticker}/orderbook -> HTTP {status} ---")
    if status == 200:
        print(json.dumps(body, indent=2, sort_keys=True)[:1500])


def section_4_completeness_audit(milestone_tickers: set[str]) -> None:
    """The load-bearing question: can milestone-driven discovery miss a
    market that exists? Brute-force the known series as a control."""
    _hdr("4. COMPLETENESS AUDIT: milestone-driven vs brute-force series sweep")

    control_tickers: set[str] = set()
    control_by_series: dict[str, int] = {}
    for series in CONTROL_SERIES_TICKERS:
        status, body = _get(f"/series/{series}")
        if status != 200:
            print(f"  series {series:22} -> HTTP {status} (does not exist / unavailable)")
            continue
        markets, complete, pages = _paginate("/markets", {"series_ticker": series}, "markets")
        tickers = {mk["ticker"] for mk in markets if mk.get("ticker")}
        control_tickers |= tickers
        control_by_series[series] = len(tickers)
        print(f"  series {series:22} -> {len(tickers):6} markets (pages={pages} complete={complete})")

    print(f"\ncontrol (series sweep) total distinct markets: {len(control_tickers)}")
    print(f"milestone-driven total distinct markets:      {len(milestone_tickers)}")

    missed = control_tickers - milestone_tickers
    extra = milestone_tickers - control_tickers
    print(f"\n*** IN SERIES SWEEP BUT NOT FOUND VIA MILESTONES: {len(missed)}")
    for t in sorted(missed)[:40]:
        print(f"      MISSED {t}")
    print(f"\n*** FOUND VIA MILESTONES BUT NOT IN CONTROL SERIES: {len(extra)}")
    for t in sorted(extra)[:40]:
        print(f"      EXTRA  {t}")
    print(
        "\nINTERPRETATION: 'MISSED' entries are markets the milestone-only architecture would\n"
        "not publish. A large MISSED count means milestones are insufficient alone. 'EXTRA'\n"
        "entries are markets a series allowlist would have dropped -- i.e. the exact failure\n"
        "mode the allowlist ban exists to prevent."
    )


def section_5_unknown_series() -> None:
    _hdr("5. UNKNOWN / NOVEL CFB SERIES DISCOVERY (is the known set complete?)")
    series, complete, pages = _paginate("/series", {"category": "Sports"}, "series")
    print(f"GET /series?category=Sports -> {len(series)} series (pages={pages} complete={complete})")
    if not series:
        # Some deployments require tags rather than category.
        series, complete, pages = _paginate("/series", {}, "series")
        print(f"  fallback bare /series -> {len(series)} series (complete={complete})")

    cfb_like = []
    for s in series:
        blob = json.dumps(s).upper()
        if "NCAAF" in blob or "COLLEGE FOOTBALL" in blob:
            cfb_like.append(s)
    print(f"\nCFB-looking series found: {len(cfb_like)}")
    for s in cfb_like:
        ticker = s.get("ticker")
        known = "KNOWN  " if ticker in CONTROL_SERIES_TICKERS else "NOVEL >>"
        print(f"  {known} {ticker!s:26} {str(s.get('title'))[:55]!r} category={s.get('category')!r}")
    if cfb_like:
        print("\n--- FULL RAW JSON of one series ---")
        print(json.dumps(cfb_like[0], indent=2, sort_keys=True))


def section_6_multivariate() -> None:
    _hdr("6. MULTIVARIATE / COMBO EVENT SURFACE")
    for path, params in [
        ("/multivariate_event_collections", {}),
        ("/multivariate_event_collections", {"status": "open"}),
        ("/multivariate_event_collections", {"series_ticker": "KXNCAAFGAME"}),
    ]:
        status, body = _get(path, params)
        items = body.get("multivariate_contracts") or body.get("collections") or [] if isinstance(body, dict) else []
        keys = sorted(body.keys()) if isinstance(body, dict) else None
        print(f"  GET {path} {params} -> HTTP {status} items={len(items)} keys={keys}")
        if status == 200 and isinstance(body, dict):
            print(f"      body sample: {json.dumps(body)[:1200]}")

    collections, complete, _ = _paginate("/multivariate_event_collections", {}, "multivariate_contracts")
    if not collections:
        collections, complete, _ = _paginate("/multivariate_event_collections", {}, "collections")
    print(f"\ntotal multivariate collections listed: {len(collections)} complete={complete}")
    cfb = [c for c in collections if "NCAAF" in json.dumps(c).upper() or "COLLEGE FOOTBALL" in json.dumps(c).upper()]
    print(f"CFB-related multivariate collections: {len(cfb)}")
    for c in cfb[:10]:
        print(f"  {json.dumps(c)[:400]}")
    if cfb:
        print("\n--- FULL RAW JSON of one CFB multivariate collection ---")
        print(json.dumps(cfb[0], indent=2, sort_keys=True))
        ticker = cfb[0].get("collection_ticker") or cfb[0].get("ticker")
        if ticker:
            status, body = _get(f"/multivariate_event_collections/{ticker}")
            print(f"\nGET /multivariate_event_collections/{ticker} -> HTTP {status}")
            print(json.dumps(body, indent=2, sort_keys=True)[:2000] if status == 200 else "")


def main() -> int:
    print(f"Kalshi CFB market-discovery investigation @ {datetime.now(UTC).isoformat()}")
    print(f"BASE_URL={BASE_URL}  (unauthenticated, GET-only)")

    milestones = section_1_milestones()
    if milestones:
        milestone_tickers, market_by_ticker = section_2_events_and_markets(milestones)
        section_3_market_fields(market_by_ticker)
        section_4_completeness_audit(milestone_tickers)
    else:
        section_4_completeness_audit(set())
    section_5_unknown_series()
    section_6_multivariate()

    _hdr("INVESTIGATION COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
