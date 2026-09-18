#!/usr/bin/env python3
"""Live, read-only investigation of Kalshi's CURRENT college-football
market surface, run from a GitHub Actions runner.

    python scripts/investigate_kalshi_milestones_live.py

*** WHY THIS EXISTS ***
The market-discovery pivot rests on empirical questions that no amount of
code reading can answer. This script asks Kalshi directly and prints
exactly what comes back, so the discovery architecture is designed against
observed behavior rather than assumption.

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

===========================================================================
FINDINGS SO FAR (revisions 1-2; transcripts under docs/evidence/)
===========================================================================
R1: `limit` is MANDATORY on /milestones -- omitting it returns HTTP 400
    {"msg":"Query argument limit is required, but not found"}. R1 also
    requested limit=1000 on every paginated sweep, above the accepted
    maximum, so every sweep failed instantly and reported an empty market
    universe. 200 works everywhere.

R2: The mission's documented filter triple
    (category=Sports & competition="College Football" & type=football_game)
    returns HTTP 200 with ZERO milestones. The endpoint answers; it simply
    yields nothing for that combination.

R2: A brute-force sweep of the 8 previously-"known" CFB series returned
    30,129 markets, and /series?category=Sports exposes 149 CFB-looking
    series -- of which only FOUR were in that known set. A series allowlist
    would therefore drop the overwhelming majority of Kalshi's real CFB
    market surface (quarter winners/spreads/totals, halves, both-teams-to-
    score, a dozen team-stat families, first-TD, overtime, and more).

===========================================================================
WHAT THIS REVISION (3) MUST SETTLE
===========================================================================
  A. Why /milestones is empty for CFB: what does it return UNFILTERED, and
     which category/competition/type values actually exist in the data? If
     a CFB milestone exists under a different vocabulary, milestone-driven
     discovery is viable and we should use it.
  B. Whether an EVENT carries a milestone reference of its own -- the
     reverse lookup, which would establish game identity without the
     milestone list endpoint at all.
  C. The complete raw field inventory of a real CFB EVENT and real CFB
     MARKETS across many families. Revisions 1-2 never got this (the walk
     was skipped once milestones came back empty), and the catalog schema
     cannot be designed without it.
  D. Whether an event's inline market list is sufficient, or
     GET /markets?event_ticker=... with pagination returns more.
  E. Whether the event-ticker suffix is a reliable Kalshi-native physical
     game key: KXNCAAF1HSPREAD-26AUG29HAWSTAN and
     KXNCAAFSPREAD-26AUG29HAWSTAN share the suffix 26AUG29HAWSTAN. If that
     holds, one physical game is identifiable with no external schedule
     provider and no CFBD key at all.
  F. Which discovery sweep is affordable on a schedule: what filters do
     /events and /markets accept (status, date windows) so a refresh need
     not re-read 30,000 markets.
  G. What the multivariate/combo surface exposes for CFB, so the catalog's
     completeness claims stay honest.
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
MAX_PAGES = 120
PAGE_LIMIT = 200
"""200, not 1000: above an endpoint's own maximum the request is rejected
outright, and revision 1 turned that into a silent "empty universe"."""


def _get(path: str, params: dict[str, Any] | None = None, quiet: bool = False) -> tuple[int, Any]:
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    for attempt in range(4):
        try:
            resp = requests.get(
                f"{BASE_URL}{path}", params=clean, headers={"Accept": "application/json"}, timeout=TIMEOUT_SECONDS
            )
        except requests.RequestException as exc:
            if attempt == 3:
                print(f"    TRANSPORT_ERROR {path} {clean}: {exc}")
                return -1, None
            time.sleep(min(1.0 * (2**attempt), 8.0))
            continue
        if resp.status_code >= 400 and resp.status_code != 429 and not (500 <= resp.status_code < 600):
            if not quiet:
                print(f"    HTTP_{resp.status_code} {path} params={clean} body={resp.text[:250]!r}")
            try:
                return resp.status_code, resp.json()
            except ValueError:
                return resp.status_code, None
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt == 3:
                return resp.status_code, None
            ra = resp.headers.get("Retry-After")
            time.sleep(min(float(ra), 8.0) if ra and ra.isdigit() else min(1.0 * (2**attempt), 8.0))
            continue
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, None
    return -1, None


def _paginate(path: str, params: dict[str, Any], list_key: str, max_pages: int = MAX_PAGES) -> tuple[list[dict], bool]:
    items: list[dict] = []
    cursor: str | None = None
    seen: set[str] = set()
    for page in range(max_pages):
        page_params = dict(params, limit=PAGE_LIMIT)
        if cursor:
            page_params["cursor"] = cursor
        status, body = _get(path, page_params)
        if status != 200 or not isinstance(body, dict):
            print(f"    PAGE_FAILURE {path} page={page} status={status}")
            return items, False
        items.extend(i for i in (body.get(list_key) or []) if isinstance(i, dict))
        cursor = body.get("cursor") or None
        if not cursor:
            return items, True
        if cursor in seen:
            print(f"    CURSOR_LOOP {path}")
            return items, False
        seen.add(cursor)
    print(f"    PAGINATION_CAP_HIT {path}")
    return items, False


def _hdr(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def _key_frequency(label: str, rows: list[dict]) -> None:
    counts: Counter[str] = Counter()
    for r in rows:
        counts.update(r.keys())
    print(f"\n--- {label}: key frequency across {len(rows)} rows ---")
    for k, n in sorted(counts.items()):
        example = next((repr(r[k])[:70] for r in rows if r.get(k) not in (None, "", [], {})), "<always empty>")
        print(f"  {k:32} {n:6}/{len(rows)}  e.g. {example}")


# --------------------------------------------------------------------------
# A. Why is /milestones empty for CFB?
# --------------------------------------------------------------------------
def section_a_milestone_vocabulary() -> list[dict]:
    _hdr("A. /milestones -- WHAT VOCABULARY DOES THE DATA ACTUALLY USE?")

    status, _body = _get("/exchange/status")
    print(f"GET /exchange/status -> HTTP {status}")

    status, body = _get("/milestones", {"limit": 200})
    print(f"\nGET /milestones?limit=200 (UNFILTERED) -> HTTP {status}")
    if isinstance(body, dict):
        print(f"  top-level keys: {sorted(body.keys())}")
    first_page = (body.get("milestones") or []) if isinstance(body, dict) else []
    print(f"  milestones on first page: {len(first_page)}")

    all_milestones, complete = _paginate("/milestones", {}, "milestones")
    print(f"\nFULL unfiltered /milestones sweep: {len(all_milestones)} milestones (complete={complete})")

    if not all_milestones:
        print("!! /milestones is EMPTY EXCHANGE-WIDE, not merely for CFB.")
        return []

    print("\n--- FULL RAW JSON of first milestone ---")
    print(json.dumps(all_milestones[0], indent=2, sort_keys=True))
    _key_frequency("milestones", all_milestones)

    for field_name in ("category", "competition", "type", "sport", "league", "milestone_type"):
        values = Counter(str(m.get(field_name)) for m in all_milestones if m.get(field_name) is not None)
        if values:
            print(f"\ndistinct {field_name!r} values ({len(values)}): {dict(values.most_common(40))}")

    football = [m for m in all_milestones if "FOOTBALL" in json.dumps(m).upper()]
    print(f"\nmilestones mentioning FOOTBALL anywhere: {len(football)}")
    for m in football[:10]:
        print(f"  {json.dumps(m)[:300]}")
    ncaaf = [m for m in all_milestones if "NCAAF" in json.dumps(m).upper()]
    print(f"\nmilestones mentioning NCAAF anywhere: {len(ncaaf)}")
    for m in ncaaf[:10]:
        print(f"  {json.dumps(m)[:300]}")

    comps = sorted({str(m.get("competition")) for m in all_milestones if m.get("competition")})
    types = sorted({str(m.get("type")) for m in all_milestones if m.get("type")})
    print(f"\nretrying filters using OBSERVED values -- competitions={comps[:25]} types={types[:25]}")
    for comp in [c for c in comps if "FOOTBALL" in c.upper() or "NCAA" in c.upper() or "COLLEGE" in c.upper()][:5]:
        status, body = _get("/milestones", {"competition": comp, "limit": 200})
        n = len(body.get("milestones") or []) if isinstance(body, dict) else 0
        print(f"  competition={comp!r} -> HTTP {status} milestones={n}")
    return all_milestones


# --------------------------------------------------------------------------
# B/C/D/E/F. The real CFB game surface: series -> events -> games -> markets
# --------------------------------------------------------------------------
GAME_KEY_RE = re.compile(r"^(?P<series>[A-Z0-9]+)-(?P<game>\d{2}[A-Z]{3}\d{2}[A-Z0-9]+)$")
"""An event ticker looks like KXNCAAFSPREAD-26AUG29HAWSTAN: a series
ticker, then a per-game suffix of YY MON DD + team codes. If that suffix is
shared across series for one physical game, it IS Kalshi's own physical
game key and no external schedule provider is needed for identity."""


def _discover_cfb_series() -> list[dict]:
    """Dynamically discover CFB series from Kalshi itself. This is NOT a
    discovery allowlist: no family is hardcoded, so a series Kalshi creates
    tomorrow is picked up on the next run."""
    series, complete = _paginate("/series", {"category": "Sports"}, "series")
    print(f"GET /series?category=Sports -> {len(series)} series (complete={complete})")
    cfb = []
    for s in series:
        title = str(s.get("title") or "").upper()
        ticker = str(s.get("ticker") or "")
        tags = [str(t).upper() for t in (s.get("tags") or [])]
        looks_college = (
            "COLLEGE FOOTBALL" in title
            or ticker.startswith("KXNCAAF")
            or ticker.startswith("KXCFB")
            or ticker.startswith("KXCFP")
        )
        if looks_college or ("FOOTBALL" in tags and "NCAA" in ticker):
            cfb.append(s)
    print(f"CFB-looking series: {len(cfb)}")
    return cfb


def section_bcdef_game_surface() -> None:
    _hdr("B-F. REAL CFB GAME SURFACE: series -> events -> physical games -> markets")

    cfb_series = _discover_cfb_series()

    print("\n--- /events filter probe (decides the shape of the production sweep) ---")
    probe_series = "KXNCAAFSPREAD"
    for label, params in [
        ("bare", {}),
        ("status=open", {"status": "open"}),
        ("status=unopened", {"status": "unopened"}),
        ("with_nested_markets", {"with_nested_markets": "true"}),
        ("status=open,unopened", {"status": "open,unopened"}),
    ]:
        status, body = _get("/events", {"series_ticker": probe_series, "limit": 5, **params})
        n = len(body.get("events") or []) if isinstance(body, dict) else 0
        keys = sorted(body.keys()) if isinstance(body, dict) else None
        print(f"  /events?series_ticker={probe_series} {label:22} -> HTTP {status} events={n} keys={keys}")

    print("\n--- EVENT SWEEP across all discovered CFB series (status=open) ---")
    events_by_game: dict[str, list[dict]] = defaultdict(list)
    all_events: list[dict] = []
    series_event_counts: dict[str, int] = {}
    unparsed_tickers: list[str] = []

    for s in cfb_series:
        ticker = str(s.get("ticker"))
        events, complete = _paginate("/events", {"series_ticker": ticker, "status": "open"}, "events")
        series_event_counts[ticker] = len(events)
        if not complete:
            print(f"  !! incomplete event sweep for {ticker}")
        for ev in events:
            all_events.append(ev)
            ev_ticker = str(ev.get("event_ticker") or ev.get("ticker") or "")
            m = GAME_KEY_RE.match(ev_ticker)
            if m:
                events_by_game[m.group("game")].append(ev)
            else:
                unparsed_tickers.append(ev_ticker)

    with_events = {k: v for k, v in series_event_counts.items() if v}
    print(f"\nseries with >=1 OPEN event: {len(with_events)} of {len(cfb_series)}")
    for t, n in sorted(with_events.items(), key=lambda kv: -kv[1])[:70]:
        print(f"  {t:28} {n:5} open events")
    print(f"\ntotal open CFB events: {len(all_events)}")
    print(f"distinct physical game keys (event-ticker suffix): {len(events_by_game)}")
    print(f"event tickers NOT matching the game-key pattern: {len(unparsed_tickers)}")
    for t in unparsed_tickers[:25]:
        print(f"      UNPARSED {t}")

    _key_frequency("events", all_events[:400])

    if not events_by_game:
        print("!! no physical games grouped; cannot continue")
        return

    fam_counts = Counter(len(v) for v in events_by_game.values())
    print(f"\nevents-per-physical-game distribution: {dict(sorted(fam_counts.items()))}")
    richest = sorted(events_by_game.items(), key=lambda kv: -len(kv[1]))
    print("\n--- richest physical games by event count ---")
    for game_key, evs in richest[:10]:
        series_list = sorted({str(e.get("series_ticker") or "?") for e in evs})
        print(f"  {game_key:22} {len(evs):3} events  series={series_list[:14]}")
    print("\n--- thinnest physical games by event count ---")
    for game_key, evs in richest[-10:]:
        series_list = sorted({str(e.get("series_ticker") or "?") for e in evs})
        print(f"  {game_key:22} {len(evs):3} events  series={series_list}")

    print("\n--- B. milestone back-reference on an event ---")
    sample_event = richest[0][1][0]
    ev_ticker = str(sample_event.get("event_ticker") or sample_event.get("ticker"))
    status, body = _get(f"/events/{ev_ticker}")
    event_detail = (body.get("event") or {}) if isinstance(body, dict) else {}
    print(f"GET /events/{ev_ticker} -> HTTP {status}, keys={sorted(event_detail.keys())}")
    milestone_fields = {k: v for k, v in event_detail.items() if "milestone" in k.lower()}
    print(f"milestone-ish fields on the event: {milestone_fields or 'NONE'}")
    for mid_field in ("milestone_id", "milestone_ticker"):
        mid = event_detail.get(mid_field)
        if mid:
            st, mb = _get(f"/milestones/{mid}")
            print(f"  GET /milestones/{mid} -> HTTP {st} body={json.dumps(mb)[:800]}")

    print("\n--- FULL RAW JSON of one CFB EVENT (markets elided) ---")
    printable = {k: v for k, v in event_detail.items() if k != "markets"}
    print(json.dumps(printable, indent=2, sort_keys=True)[:2500])

    _hdr("D. IS AN EVENT'S INLINE MARKET LIST SUFFICIENT?")
    game_key, evs = richest[0]
    print(f"auditing physical game {game_key} ({len(evs)} events)")
    total_inline = 0
    total_listed = 0
    for ev in evs[:12]:
        t = str(ev.get("event_ticker") or ev.get("ticker"))
        _st, nb = _get("/events", {"event_ticker": t, "with_nested_markets": "true", "limit": 1}, quiet=True)
        nested = 0
        if isinstance(nb, dict):
            for e in nb.get("events") or []:
                nested += len(e.get("markets") or [])
        _st2, db = _get(f"/events/{t}", quiet=True)
        inline = 0
        if isinstance(db, dict):
            inline = len(db.get("markets") or (db.get("event") or {}).get("markets") or [])
        listed, complete = _paginate("/markets", {"event_ticker": t}, "markets")
        total_inline += inline
        total_listed += len(listed)
        flag = "  <<< MISMATCH" if inline != len(listed) else ""
        print(f"  {t:44} inline={inline:4} nested={nested:4} /markets={len(listed):4} complete={complete}{flag}")
    print(f"\nTOTALS for this game: inline={total_inline} /markets={total_listed}")
    if total_inline != total_listed:
        print("*** CONCLUSION: the inline list is NOT sufficient -- discovery must page /markets.")
    else:
        print("*** Inline and paginated agree for this game (still page /markets: one game is not proof).")

    _hdr("C. RAW MARKET FIELD INVENTORY (across all families of one game)")
    all_markets: list[dict] = []
    per_series_example: dict[str, dict] = {}
    for ev in evs:
        t = str(ev.get("event_ticker") or ev.get("ticker"))
        mk, _c = _paginate("/markets", {"event_ticker": t}, "markets")
        all_markets.extend(mk)
        for m in mk:
            s = str(m.get("series_ticker") or (m.get("event_ticker") or "").split("-")[0])
            per_series_example.setdefault(s, m)
    print(f"markets for physical game {game_key}: {len(all_markets)} across {len(per_series_example)} series")
    _key_frequency("markets", all_markets)

    print(f"\n--- status / type distributions for game {game_key} ---")
    for f in ("status", "market_type", "strike_type", "result", "can_close_early", "response_price_units"):
        print(f"  {f:24} {dict(Counter(str(m.get(f)) for m in all_markets).most_common(12))}")

    print("\n--- ONE FULL RAW MARKET PER SERIES (contract grammar per family) ---")
    for s, m in sorted(per_series_example.items())[:16]:
        print(f"\n===== series {s} =====")
        print(json.dumps(m, indent=2, sort_keys=True))

    sample_ticker = str(all_markets[0].get("ticker")) if all_markets else None
    if sample_ticker:
        st, ob = _get(f"/markets/{sample_ticker}/orderbook")
        print(f"\n--- GET /markets/{sample_ticker}/orderbook -> HTTP {st} ---")
        print(json.dumps(ob, indent=2, sort_keys=True)[:1200])

    _hdr("F. FILTERS FOR AN AFFORDABLE SCHEDULED REFRESH")
    now = int(datetime.now(UTC).timestamp())
    probes: list[tuple[str, dict[str, Any]]] = [
        ("status=open", {"series_ticker": "KXNCAAFSPREAD", "status": "open"}),
        ("min_close_ts", {"series_ticker": "KXNCAAFSPREAD", "min_close_ts": now}),
        ("max_close_ts", {"series_ticker": "KXNCAAFSPREAD", "max_close_ts": now + 7 * 86400}),
        ("both close_ts", {"series_ticker": "KXNCAAFSPREAD", "min_close_ts": now, "max_close_ts": now + 7 * 86400}),
    ]
    if sample_ticker:
        probes.append(("tickers=", {"tickers": sample_ticker}))
    for label, params in probes:
        st, body = _get("/markets", {**params, "limit": 200})
        n = len(body.get("markets") or []) if isinstance(body, dict) else 0
        print(f"  /markets {label:16} {params} -> HTTP {st} markets={n}")


def section_g_multivariate() -> None:
    _hdr("G. MULTIVARIATE / COMBO SURFACE FOR CFB")
    collections, complete = _paginate("/multivariate_event_collections", {}, "multivariate_contracts")
    print(f"total multivariate collections: {len(collections)} (complete={complete})")

    cfb = []
    for c in collections:
        assoc = [t for t in (c.get("associated_event_tickers") or []) if isinstance(t, str)]
        ncaaf = [t for t in assoc if "NCAAF" in t.upper()]
        meta_cfb = "NCAAF" in json.dumps({k: v for k, v in c.items() if "associated" not in k}).upper()
        if ncaaf or meta_cfb:
            cfb.append((c, ncaaf))
    print(f"collections referencing an NCAAF event: {len(cfb)}")
    for c, ncaaf in cfb[:20]:
        print(
            f"  {str(c.get('collection_ticker')):46} series={str(c.get('series_ticker')):30} "
            f"ncaaf_events={len(ncaaf):5} total={len(c.get('associated_event_tickers') or []):5} "
            f"single_market_per_event={c.get('is_single_market_per_event')} size_min={c.get('size_min')}"
        )
    if cfb:
        c, ncaaf = cfb[0]
        meta = {k: v for k, v in c.items() if not k.startswith("associated")}
        print("\n--- FULL RAW JSON of one CFB-referencing collection (assoc elided) ---")
        print(json.dumps(meta, indent=2, sort_keys=True)[:2000])
        print(f"\nsample NCAAF event tickers referenced: {ncaaf[:15]}")
        cs = c.get("series_ticker")
        st, body = _get("/markets", {"series_ticker": cs, "limit": 5})
        n = len(body.get("markets") or []) if isinstance(body, dict) else 0
        print(f"\nGET /markets?series_ticker={cs} -> HTTP {st} markets={n}")
        if n:
            print(json.dumps((body.get("markets") or [])[0], indent=2, sort_keys=True)[:1200])
        st, body = _get("/events", {"series_ticker": cs, "limit": 5})
        n2 = len(body.get("events") or []) if isinstance(body, dict) else 0
        print(f"GET /events?series_ticker={cs} -> HTTP {st} events={n2}")


def main() -> int:
    print(f"Kalshi CFB market-discovery investigation R3 @ {datetime.now(UTC).isoformat()}")
    print(f"BASE_URL={BASE_URL}  (unauthenticated, GET-only)")
    section_a_milestone_vocabulary()
    section_bcdef_game_surface()
    section_g_multivariate()
    _hdr("INVESTIGATION COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
