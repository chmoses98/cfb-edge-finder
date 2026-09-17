#!/usr/bin/env python3
"""Empirical completeness audit of the Kalshi CFB market catalog.

    python scripts/audit_kalshi_catalog_completeness.py

Read-only, credential-free, runs from an Actions runner (this dev
environment has no egress to Kalshi).

*** WHAT "COMPLETENESS" CAN AND CANNOT MEAN HERE ***
The mission asks for VISIBLE KALSHI MARKETS vs DISCOVERED API MARKETS.
Kalshi's own web and mobile clients render from the same public REST
endpoints this catalog reads -- there is no separate "website-only" market
feed -- so the honest operationalization is not a screenshot diff but an
INDEPENDENT REDISCOVERY: reach the same market universe by a different
route and diff the two.

This audit therefore builds a THIRD path, deliberately unlike the
catalog's two:

    catalog path 1:  /milestones -> event tickers -> /markets?event_ticker
    catalog path 2:  /series -> /events?series_ticker -> /markets?event_ticker
    THIS AUDIT:      /series -> /markets?series_ticker  (no milestones,
                                no /events call at all)

Path 3 touches neither milestones nor the events endpoint, so a defect in
either -- a milestone omitting an event, an event sweep missing a series,
a status filter hiding live inventory -- shows up as a market present in
path 3 and absent from the catalog. That is the finding that matters, and
it is reported per game rather than only in aggregate.

*** THE CROSS-SECTION ***
Per the mission, the audit spans marquee/top-tier games, ordinary
power-conference games, G5 games, and FBS-vs-FCS / low-market-count games.
Kalshi's milestones hand us exactly the fields needed to stratify:
`details.tier`, `details.division` and `details.conference`.

Exit code is 0 when every audited game reconciles, 2 when any audited game
is missing markets the independent path found. A non-zero exit here means
the catalog is NOT ready to be the live source.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from cfb_edge_finder.catalog.artifacts import build_catalog, build_flat_index, write_json
from cfb_edge_finder.catalog.discovery import MarketDiscovery
from cfb_edge_finder.catalog.identity import parse_event_ticker
from cfb_edge_finder.catalog.pagination import SweepStats, paginate
from cfb_edge_finder.data.kalshi_client import KalshiClient

MARQUEE_TIERS = frozenset({"A_PLUS", "A", "A_MINUS"})
POWER_CONFERENCES = frozenset({"SEC", "Big Ten", "Big 12", "ACC", "Pac-12", "Big-12", "BIG TEN"})


def _hdr(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def independent_series_market_sweep(getter, stats: SweepStats) -> tuple[dict[str, set[str]], list[str]]:
    """PATH 3: every CFB series -> every market, straight from
    /markets?series_ticker, bucketed by physical game key.

    No milestone is consulted and /events is never called, so this is a
    genuinely independent measurement rather than a re-run of the
    catalog's own logic. Status is deliberately UNFILTERED: asking for
    open-only here would hide exactly the kind of live-but-unexpected
    inventory the audit is looking for."""
    series_sweep = paginate(getter, "/series", {"category": "Sports"}, "series")
    if not series_sweep.complete:
        print(f"  WARNING: series sweep incomplete: {series_sweep.failure_reason}")

    cfb_series: list[str] = []
    for series in series_sweep.items:
        ticker = str(series.get("ticker") or "")
        title = str(series.get("title") or "").upper()
        tags = [str(t).upper() for t in (series.get("tags") or [])]
        if (
            ticker.startswith(("KXNCAAF", "KXCFB", "KXCFP"))
            or "COLLEGE FOOTBALL" in title
            or ("FOOTBALL" in tags and "NCAA" in ticker)
        ):
            cfb_series.append(ticker)

    print(f"  independent path: {len(cfb_series)} CFB series to sweep")
    by_game: dict[str, set[str]] = defaultdict(set)
    failures: list[str] = []
    for index, ticker in enumerate(sorted(cfb_series), start=1):
        sweep = paginate(getter, "/markets", {"series_ticker": ticker}, "markets")
        stats.record_sweep(f"/markets series={ticker}", sweep)
        if not sweep.complete:
            failures.append(f"{ticker}: {sweep.failure_reason}")
        for market in sweep.items:
            event_ticker = str(market.get("event_ticker") or "")
            parts = parse_event_ticker(event_ticker)
            market_ticker = market.get("ticker")
            if parts and market_ticker:
                by_game[parts.game_key].add(str(market_ticker))
        if index % 25 == 0:
            print(f"    ...{index}/{len(cfb_series)} series swept")
    print(f"  independent path: {sum(len(v) for v in by_game.values())} markets across {len(by_game)} game keys")
    return by_game, failures


def _stratum(game: dict) -> str:
    identity = game.get("identity") or {}
    tier = str(identity.get("tier") or "")
    division = str(identity.get("division") or "")
    conference = str(identity.get("conference") or "")
    if division and division.upper() != "FBS":
        return "fbs_vs_fcs_or_lower"
    if tier in MARQUEE_TIERS:
        return "marquee"
    if conference in POWER_CONFERENCES:
        return "power_conference"
    if game.get("market_count", 0) <= 6:
        return "low_market_count"
    return "group_of_five_or_other"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon-days", type=float, default=10.0)
    parser.add_argument("--per-stratum", type=int, default=4, help="Games to audit per stratum")
    parser.add_argument("--audit-all", action="store_true", help="Audit every discovered game, not a sample")
    parser.add_argument(
        "--write-catalog",
        default=None,
        help=(
            "Directory to write the catalog this audit already built. Without it the caller has to "
            "rebuild the catalog from scratch to look at it, which doubles a ten-minute run for no "
            "new information."
        ),
    )
    args = parser.parse_args(argv)

    started = datetime.now(UTC)
    client = KalshiClient()

    _hdr("STEP 1: build the catalog exactly as production does")
    run = MarketDiscovery(client.get_json).run(as_of=started, horizon_days=args.horizon_days)
    catalog = build_catalog(run)
    totals = catalog["totals"]
    print(
        f"catalog: {totals['physical_games']} games / {totals['events']} events / "
        f"{totals['markets']} markets / {totals['unknown_family_markets']} unknown-family"
    )
    print(f"capture_complete={catalog['completeness']['capture_complete']}")
    print(f"requests={catalog['discovery']['requests_made']} errors={len(catalog['discovery']['errors'])}")
    for error in catalog["discovery"]["errors"][:10]:
        print(f"  error: {error}")

    print("\n--- family distribution (whole slate) ---")
    for family, count in totals["family_distribution"].items():
        print(f"  {family:28} {count:6}")
    print("\n--- status distribution (whole slate) ---")
    for status, count in totals["status_distribution"].items():
        print(f"  {status:28} {count:6}")

    if args.write_catalog:
        out_dir = Path(args.write_catalog)
        write_json(out_dir / "cfb_market_catalog.json", catalog)
        write_json(out_dir / "cfb_markets_flat.json", build_flat_index(run))
        print(f"wrote the audited catalog to {out_dir}")

    _hdr("STEP 2: independent rediscovery (series -> markets, no milestones, no /events)")
    audit_stats = SweepStats()
    independent, independent_failures = independent_series_market_sweep(client.get_json, audit_stats)
    if independent_failures:
        print(f"  independent-path failures ({len(independent_failures)}):")
        for failure in independent_failures[:10]:
            print(f"    {failure}")

    _hdr("STEP 3: stratify the audited cross-section")
    catalog_by_game: dict[str, set[str]] = {
        game["game_key"]: {m["market_ticker"] for m in game["markets"]} for game in catalog["games"]
    }
    strata: dict[str, list[dict]] = defaultdict(list)
    for game in catalog["games"]:
        strata[_stratum(game)].append(game)
    for name, games in sorted(strata.items()):
        print(f"  {name:28} {len(games):4} games")

    audited: list[dict] = []
    if args.audit_all:
        audited = list(catalog["games"])
    else:
        for name in sorted(strata):
            ranked = sorted(strata[name], key=lambda g: -g.get("market_count", 0))
            picks = ranked[: args.per_stratum]
            # Also take the THINNEST game in the stratum: a low-market-count
            # game is where a discovery gap is least likely to be noticed.
            if len(ranked) > args.per_stratum:
                picks.append(ranked[-1])
            audited.extend(picks)

    _hdr(f"STEP 4: per-game reconciliation ({len(audited)} games audited)")
    discrepancies: list[tuple[str, set[str], set[str]]] = []
    header = (
        f"{'game_key':22} {'stratum':24} {'tier':9} {'div':5} "
        f"{'catalog':>8} {'indep':>7} {'missing':>8} {'extra':>7}"
    )
    print(header)
    print("-" * len(header))
    for game in sorted(audited, key=lambda g: g["game_key"]):
        key = game["game_key"]
        identity = game.get("identity") or {}
        catalog_markets = catalog_by_game.get(key, set())
        independent_markets = independent.get(key, set())
        missing = independent_markets - catalog_markets
        extra = catalog_markets - independent_markets
        print(
            f"{key:22} {_stratum(game):24} {str(identity.get('tier') or '-'):9} "
            f"{str(identity.get('division') or '-'):5} {len(catalog_markets):8} "
            f"{len(independent_markets):7} {len(missing):8} {len(extra):7}"
        )
        if missing:
            discrepancies.append((key, missing, extra))

    _hdr("STEP 5: investigate every discrepancy")
    if not discrepancies:
        print("NO DISCREPANCIES: every audited game's catalog menu contains every market the")
        print("independent series->markets path found for that game.")
    for key, missing, extra in discrepancies:
        print(f"\ngame {key}: {len(missing)} market(s) in the independent path but NOT in the catalog")
        for ticker in sorted(missing)[:15]:
            status, body = _probe_market(client, ticker)
            print(f"    MISSING {ticker}  (detail HTTP {status}, status={body})")
        if extra:
            print(f"  and {len(extra)} in the catalog but not the independent path (sample):")
            for ticker in sorted(extra)[:5]:
                print(f"    EXTRA   {ticker}")

    _hdr("STEP 6: whole-slate aggregate")
    catalog_all = {t for tickers in catalog_by_game.values() for t in tickers}
    audited_keys = {g["game_key"] for g in audited}
    # Restrict the aggregate to games the catalog publishes: the independent
    # path sweeps unfiltered status and every game key on the exchange
    # (including finished games and those beyond the horizon), which the
    # catalog deliberately does not publish. Comparing those would measure
    # the horizon, not completeness.
    independent_in_scope = {t for key in catalog_by_game for t in independent.get(key, set())}
    print(f"catalog markets (published games):            {len(catalog_all)}")
    print(f"independent markets (same games):             {len(independent_in_scope)}")
    print(f"in independent but NOT catalog (same games):  {len(independent_in_scope - catalog_all)}")
    print(f"in catalog but NOT independent (same games):  {len(catalog_all - independent_in_scope)}")
    print(f"game keys only the independent path saw:      {len(set(independent) - set(catalog_by_game))}")
    print(f"  (expected: finished games and games beyond the {args.horizon_days}-day horizon)")

    slate_missing = independent_in_scope - catalog_all
    if slate_missing:
        print(f"\nsample of the {len(slate_missing)} markets missing slate-wide:")
        for ticker in sorted(slate_missing)[:25]:
            print(f"    {ticker}")
        series_counts = Counter(t.split("-")[0] for t in slate_missing)
        print(f"\nmissing markets by series: {dict(series_counts.most_common(20))}")

    # The verdict repeats the headline numbers rather than only pointing
    # back at STEP 1. A CI log is read from the END -- the log API returns
    # a bounded tail -- so a verdict that references figures printed ten
    # thousand lines earlier is a verdict nobody can actually check.
    _hdr("VERDICT")
    print("--- what the catalog captured ---")
    print(f"  physical games            {totals['physical_games']}")
    print(f"  events                    {totals['events']}")
    print(f"  markets                   {totals['markets']}")
    print(f"  season-level events       {totals['season_level_events']}")
    print(f"  unknown-family markets    {totals['unknown_family_markets']}")
    print(f"  capture_complete          {catalog['completeness']['capture_complete']}")
    print(f"  games incomplete          {catalog['completeness']['games_incomplete_count']}")
    disc = catalog["discovery"]
    print(f"  milestones considered     {disc['milestones_considered']}")
    print(f"  milestones selected       {disc['milestones_selected']}")
    print(f"  CFB series discovered     {disc['series_discovered']}")
    print(f"  series with open events   {disc['series_with_open_events']}")
    print(f"  events only in series sweep {disc['events_only_in_series_sweep_count']}")
    print(f"  events from prefetch      {disc['events_served_from_prefetch']}")
    print(f"  events fetched one-by-one {disc['events_fetched_individually']}")
    print("\n--- family distribution ---")
    for family, count in totals["family_distribution"].items():
        print(f"  {family:28} {count:6}")
    print("\n--- market status distribution ---")
    for status_value, count in totals["status_distribution"].items():
        print(f"  {status_value:28} {count:6}")
    print()

    audited_missing = sum(len(m) for _k, m, _e in discrepancies)
    print(f"audited games:            {len(audited)} ({len(audited_keys)} distinct)")
    print(f"audited games with gaps:  {len(discrepancies)}")
    print(f"audited markets missing:  {audited_missing}")
    print(f"slate-wide missing:       {len(slate_missing)}")
    print(f"catalog capture_complete: {catalog['completeness']['capture_complete']}")
    print(f"elapsed:                  {round((datetime.now(UTC) - started).total_seconds(), 1)}s")
    print(f"requests (catalog):       {catalog['discovery']['requests_made']}")
    print(f"requests (audit path):    {audit_stats.requests_made}")

    if audited_missing or slate_missing:
        print("\nRESULT: NOT COMPLETE -- the independent path found markets the catalog did not publish.")
        return 2
    print("\nRESULT: COMPLETE -- independent rediscovery found nothing the catalog missed.")
    return 0


def _probe_market(client: KalshiClient, ticker: str) -> tuple[int, str]:
    """Fetch one market so a discrepancy is explained, not just counted."""
    try:
        body = client.get_json(f"/markets/{ticker}", None)
    except Exception as exc:  # noqa: BLE001
        return -1, f"fetch failed: {type(exc).__name__}"
    market = (body or {}).get("market") or body or {}
    return 200, json.dumps(
        {k: market.get(k) for k in ("status", "event_ticker", "close_time", "title")}, default=str
    )[:200]


if __name__ == "__main__":
    sys.exit(main())
