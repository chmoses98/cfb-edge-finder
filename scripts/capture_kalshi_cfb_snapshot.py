#!/usr/bin/env python3
"""Milestone D, mission sections 15/18: the first genuine prospective
research snapshot -- fetches LIVE CFBD schedule + LIVE Kalshi CFB
markets, maps every discovered game-level market to a canonical game,
classifies its coverage outcome, prices every MAPPED_SUPPORTED
FBS-vs-FBS contract with the real C.2 model (ONE game projection reused
across that game's whole contract ladder), and prints the resulting
research ledger. Read-only against both APIs; no order-placement
endpoint is ever called; no recommendation/staking output anywhere.

    python scripts/capture_kalshi_cfb_snapshot.py --schedule-season 2026 \\
        --history-seasons 2022 2023 2024 2025

Requires CFBD_API_KEY (live schedule + historical ratings corpus) --
Kalshi's own endpoints need no credential at all, per KalshiClient's own
design. Exits non-zero on a genuine setup failure (missing CFBD key);
individual market-level failures (parse/mapping/pricing) are recorded as
explicit coverage outcomes in the printed ledger, never silent drops and
never a script-level crash.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.data.kalshi_client import KalshiClient  # noqa: E402
from cfb_edge_finder.ingestion.game_normalization import (  # noqa: E402
    GameNormalizationError,
    away_classification,
    home_classification,
    normalize_cfbd_game,
)
from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason, to_coverage_outcome  # noqa: E402
from cfb_edge_finder.kalshi.contract_semantics import extract_matchup_from_rules_primary  # noqa: E402
from cfb_edge_finder.kalshi.game_mapping import KalshiGameEvidence, map_kalshi_event_to_game  # noqa: E402
from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest  # noqa: E402
from cfb_edge_finder.kalshi.ladder_pricing import price_one_market  # noqa: E402
from cfb_edge_finder.kalshi.research_ledger import ResearchLedger  # noqa: E402
from cfb_edge_finder.modeling.corpus import TeamGameLine, build_team_game_lines  # noqa: E402
from cfb_edge_finder.schemas.common import MarketFamily  # noqa: E402
from cfb_edge_finder.schemas.game import GameRecord  # noqa: E402
from cfb_edge_finder.schemas.kalshi_observation import KalshiResearchObservation, SnapshotTiming  # noqa: E402
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion  # noqa: E402
from cfb_edge_finder.teams.fcs_identity import build_fcs_school_name_set  # noqa: E402

MODEL_VERSION = "0.4.0-milestone-c2-live-margin-correction"
"""Must match scripts/build_cfb_baseline.py's own MODEL_VERSION exactly
-- mission section 9 forbids silently pricing under any other version."""

TRAINING_CUTOFF_LABEL = "strictly before the projected game's own as_of (season, week)"

CORE_V1_SERIES_TO_FAMILY: dict[str, MarketFamily] = {
    "KXNCAAFGAME": MarketFamily.MONEYLINE,
    "KXNCAAFSPREAD": MarketFamily.SPREAD,
    "KXNCAAFTOTAL": MarketFamily.TOTAL,
}
"""The three CORE_V1 series this snapshot actually parses/prices -- see
kalshi/cfb_market_family_registry.py. Any OTHER live CFB series
(futures, first-half, etc.) is discovered and counted below but never
routed through contract_semantics/market_pricing at all."""

FUTURES_SERIES_TICKERS = (
    "KXNCAAF",
    "KXNCAAFPLAYOFF",
    "KXHEISMAN",
    "KXNCAAFAPRANK",
    "KXNCAAFTOPAPRANK",
    "KXNCAAFCS",
    "KXNCAAFACC",
    "KXNCAAFSEC",
    "KXNCAAFCUSA",
    "KXNCAAFBIG12",
    "KXNCAAFBIGTEN",
    "KXNCAAFWINS",
)
"""Known futures/season-long series (mission section 21) -- discovered
and counted as NON_GAME_FUTURES, never priced by this single-game engine."""


def _ratings_component_version() -> str:
    return (
        "ridge_lambda=10;pace_mode=matchup;residual_scale=0.85;fcs_mode=pooled;calibration=platt;"
        "fcs_treatment=pooled-shrinkage-v2;margin_correction_method=linear"
    )


def _fetch_history_lines(seasons: list[int], client: CFBDClient, captured_at: datetime) -> list[TeamGameLine]:
    all_lines: list[TeamGameLine] = []
    for season in seasons:
        raw_games = client.fetch_games(season=season, season_type=None, division="fbs")
        raw_advanced = client.fetch_advanced_team_game_stats(season=season)
        lines, _skipped = build_team_game_lines(raw_games, raw_advanced, captured_at=captured_at)
        all_lines.extend(lines)
    return all_lines


def _fetch_candidate_games(
    season: int, client: CFBDClient, observed_at: datetime
) -> tuple[list[GameRecord], dict[str, tuple[str | None, str | None]]]:
    raw_games = client.fetch_games(season=season, season_type=None)
    games: list[GameRecord] = []
    classification_by_game_id: dict[str, tuple[str | None, str | None]] = {}
    for raw in raw_games:
        try:
            game = normalize_cfbd_game(raw, observed_at=observed_at)
        except GameNormalizationError:
            continue
        games.append(game)
        classification_by_game_id[game.game_id] = (home_classification(raw), away_classification(raw))
    return games, classification_by_game_id


def _fetch_fcs_school_names(client: CFBDClient, season: int) -> frozenset[str]:
    """Milestone D hardening pass: a minimal, deterministic identity
    lookup (NOT an FCS registry, NOT FCS modeling -- see
    teams/fcs_identity.py) so a genuine FCS-vs-FCS Kalshi market is
    classified as the distinct, understood FCS_VS_FCS coverage reason
    instead of an undifferentiated parse/ambiguity failure."""
    raw_teams = client.fetch_all_division_teams(season=season)
    return build_fcs_school_name_set(raw_teams)


def _fetch_active_markets_safe(client: KalshiClient, series_ticker: str) -> list[dict]:
    """Fetches every market for one series and filters to `status ==
    "active"` CLIENT-SIDE, deliberately never passing `status=` as a
    server-side query parameter.

    *** REAL EVIDENCE THIS IS BUILT FROM (two live runs on this branch) ***
    Run 1 (job 32816755586) passed `status="active"` server-side and got
    HTTP 400 for KXNCAAFGAME specifically, which looked at first like a
    quirk of that one truly-empty series. Run 2 (job 97708513504), after
    a first fix attempt still passed `status="active"` server-side for
    EVERY series -- and EVERY one of them returned 400 (KXNCAAFSPREAD/
    KXNCAAFTOTAL included, both confirmed via
    scripts/validate_kalshi_cfb_live.py to have ~200 real active markets
    each), except KXNCAAFWINS which returned 429 (rate-limited). That
    ruled out the "empty series" theory: the real cause is that Kalshi's
    `/markets` endpoint does not accept `status=active` as a query
    parameter value at all, even though `"active"` IS the real value it
    puts in each market's own `status` FIELD in the response body -- the
    query-parameter vocabulary and the response-field vocabulary are not
    the same, and `validate_kalshi_cfb_live.py` already worked around
    this correctly (see its own module docstring) by fetching markets
    UNFILTERED and checking `status` client-side. This function now does
    the same, rather than trusting a server-side filter that was never
    actually confirmed to work.

    A genuine per-series HTTPError (e.g. real rate-limiting) is still
    caught here and reported rather than crashing the whole capture."""
    try:
        markets = client.fetch_markets(series_ticker=series_ticker)
    except requests.HTTPError as exc:
        print(f"  NOTE: GET /markets?series_ticker={series_ticker} failed ({exc})")
        print(f"  Treating {series_ticker!r} as 0 markets and continuing.")
        return []
    return [m for m in markets if str(m.get("status", "")).lower() == "active"]


def _evidence_from_market(market: dict, event_ticker: str) -> KalshiGameEvidence:
    """Builds mapping evidence for one event, from ONE of its markets
    (`probe_market` at the call site). The matchup string comes from
    `extract_matchup_from_rules_primary(rules_primary)` -- NOT from this
    market's own `title` -- because a live event probe (job 97709841758)
    confirmed the event object itself has no title/matchup field, and an
    individual market's title is single-team/single-line, never a real
    "TEAM1 vs TEAM2" pairing (see that function's own docstring for the
    full evidence). If extraction fails (title/rules_primary absent, or
    the confirmed phrasing isn't matched), `title=None` is passed through
    honestly -- `map_kalshi_event_to_game` already treats that as
    PARSE_UNRESOLVED, never a guess."""
    close_time_raw = market.get("close_time")
    reference_timestamp = None
    if isinstance(close_time_raw, str):
        try:
            reference_timestamp = datetime.fromisoformat(close_time_raw.replace("Z", "+00:00"))
        except ValueError:
            reference_timestamp = None
    matchup = extract_matchup_from_rules_primary(market.get("rules_primary"))
    return KalshiGameEvidence(
        market_ticker=str(market.get("ticker", "")),
        event_ticker=event_ticker,
        title=matchup,
        subtitle=str(market.get("title", "") or "") or None,
        reference_timestamp=reference_timestamp,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schedule-season", type=int, default=2026)
    parser.add_argument("--history-seasons", type=int, nargs="+", default=[2022, 2023, 2024, 2025])
    parser.add_argument("--n-simulations", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.cfbd_api_key:
        print("ERROR: CFBD_API_KEY not set -- this capture requires a genuine live schedule fetch.", file=sys.stderr)
        return 2

    captured_at = datetime.now(UTC)
    snapshot_id = str(uuid.uuid4())
    snapshot_timing = SnapshotTiming(label="EARLY_OPEN", hours_before_kickoff=None)
    provenance = DataProvenance(schedule_source="cfbd", data_timestamp=captured_at)
    model_version = ModelVersion(
        model_version=MODEL_VERSION,
        ratings_component_version=_ratings_component_version(),
        pricing_engine_version="0.1.0",
    )

    cfbd_client = CFBDClient(api_key=settings.cfbd_api_key)
    try:
        candidate_games, classification_by_game_id = _fetch_candidate_games(
            args.schedule_season, cfbd_client, captured_at
        )
        history_lines = _fetch_history_lines(args.history_seasons, cfbd_client, captured_at)
        fcs_school_names = _fetch_fcs_school_names(cfbd_client, args.schedule_season)
    except CFBDAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Fetched {len(candidate_games)} candidate games for season {args.schedule_season} from live CFBD.")
    print(f"Fetched {len(history_lines)} historical TeamGameLine rows across seasons {args.history_seasons}.")
    print(f"Fetched {len(fcs_school_names)} known FCS school names from live CFBD (FCS-vs-FCS classification only).")

    cache = GameProjectionCache(history_lines)
    ledger = ResearchLedger()
    kalshi_client = KalshiClient()

    # --- CORE_V1 game-level series ---------------------------------
    for series_ticker, family in CORE_V1_SERIES_TO_FAMILY.items():
        markets = _fetch_active_markets_safe(kalshi_client, series_ticker)
        print(f"\n{series_ticker}: {len(markets)} active markets")
        markets_by_event: dict[str, list[dict]] = {}
        for market in markets:
            event_ticker = str(market.get("event_ticker", ""))
            markets_by_event.setdefault(event_ticker, []).append(market)

        for event_ticker, event_markets in markets_by_event.items():
            probe_market = event_markets[0]
            evidence = _evidence_from_market(probe_market, event_ticker)
            mapping = map_kalshi_event_to_game(evidence, candidate_games, fcs_school_names=fcs_school_names)

            cached_projection = None
            home_cls = away_cls = None
            training_cutoff_str = None
            if mapping.game_id is not None:
                home_cls, away_cls = classification_by_game_id.get(mapping.game_id, (None, None))
                matched_game = next((g for g in candidate_games if g.game_id == mapping.game_id), None)
                if matched_game is not None and home_cls == "fbs" and away_cls == "fbs":
                    request = GameProjectionRequest(
                        game_id=matched_game.game_id,
                        home_id=matched_game.home_team_id,
                        away_id=matched_game.away_team_id,
                        home_classification=home_cls,
                        away_classification=away_cls,
                        is_neutral_site=matched_game.neutral_site,
                        as_of_season=matched_game.season,
                        as_of_week=matched_game.week_number or 0,
                        n_simulations=args.n_simulations,
                        seed=args.seed,
                    )
                    try:
                        cached_projection = cache.get_or_build(request)
                        training_cutoff_str = (
                            f"strictly before season={request.as_of_season} week={request.as_of_week}"
                        )
                    except ValueError as exc:
                        print(f"  NOTE: could not build projection for {mapping.game_id!r}: {exc}")

            for market in event_markets:
                observation: KalshiResearchObservation = price_one_market(
                    market,
                    family_hint=family,
                    event_ticker=event_ticker,
                    mapping=mapping,
                    home_classification=home_cls,
                    away_classification=away_cls,
                    cached_projection=cached_projection,
                    captured_at=captured_at,
                    snapshot_id=snapshot_id,
                    snapshot_timing=snapshot_timing,
                    model_version=model_version,
                    training_cutoff=training_cutoff_str,
                    provenance=provenance,
                )
                ledger.append(observation)

    # --- Futures / season-long series -- discovered + isolated, never priced ---
    futures_count = 0
    for series_ticker in FUTURES_SERIES_TICKERS:
        markets = _fetch_active_markets_safe(kalshi_client, series_ticker)
        futures_count += len(markets)
        for market in markets:
            ledger.append(
                KalshiResearchObservation(
                    snapshot_id=snapshot_id,
                    captured_at=captured_at,
                    snapshot_timing=snapshot_timing,
                    game_id=None,
                    kalshi_event_ticker=str(market.get("event_ticker", "")),
                    kalshi_market_ticker=str(market.get("ticker", "")),
                    family=None,
                    fee_status="unverified",
                    coverage_outcome=to_coverage_outcome(KalshiCfbCoverageReason.NON_GAME_FUTURES),
                    coverage_reason=KalshiCfbCoverageReason.NON_GAME_FUTURES.value,
                    parse_status="not_applicable",
                    pricing_status="futures_separate_engine",
                    provenance=provenance,
                )
            )
    print(f"\nFutures/season-long series: {futures_count} active markets discovered, all NON_GAME_FUTURES.")

    # --- Summary -----------------------------------------------------
    print(f"\n=== SNAPSHOT {snapshot_id} ({snapshot_timing.label}) captured_at={captured_at.isoformat()} ===")
    print(f"Total observations: {len(ledger)}")
    print(f"Coverage outcome counts: {ledger.coverage_outcome_counts()}")
    print(f"Coverage sum check: {sum(ledger.coverage_outcome_counts().values())} == {len(ledger)}")
    reason_counts: dict[str, int] = {}
    for row in ledger.rows():
        key = row.coverage_reason or "none"
        reason_counts[key] = reason_counts.get(key, 0) + 1
    print(f"Coverage reason counts (specific, mission hardening breakdown): {reason_counts}")
    print(f"Reason sum check: {sum(reason_counts.values())} == {len(ledger)}")
    print(f"Research readiness counts: {ledger.readiness_counts()}")

    priced = [r for r in ledger.rows() if r.pricing_status == "model_priced"]
    print(f"\nModel-priced (RESEARCH_COMPARABLE-eligible) observations: {len(priced)}")
    for row in priced[:20]:
        print(
            f"  {row.kalshi_market_ticker} ({row.family.value if row.family else None}, "
            f"parse_status={row.parse_status}): "
            f"model_probability={row.model_probability:.4f} "
            f"executable_yes_price={row.executable_yes_price} "
            f"research_probability_gap={row.research_probability_gap} "
            f"research_fee_amount={row.research_fee_amount} "
            f"fee_schedule_version={row.fee_schedule_version} "
            f"fee_adjusted_research_gap={row.fee_adjusted_research_gap}"
        )

    print("\nSTATUS: RESEARCH-ONLY. No bet recommendation, stake sizing, or trading action anywhere in this output.")
    print(f"\nFull ledger (JSON): {json.dumps([r.model_dump(mode='json') for r in ledger.rows()], indent=2)[:20000]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
