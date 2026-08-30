#!/usr/bin/env python3
"""Research settlement + outcome attribution.

Resolves every captured research observation to its market's outcome, with
its own hypothetical research-unit economics and its own CLOSING linkage.

    python scripts/research_attribute.py --season 2026 --data-repo-dir /path/to/checkout

*** RESEARCH-ONLY ***
Produces measurement records. No bet, no stake, no recommendation, no
order. Kalshi access here is read-only market metadata.

*** INCREMENTAL BY CONSTRUCTION (mission section 18) ***
Both ledgers are loaded exactly ONCE per run:
  - the observation corpus (one pass),
  - the attribution ledger (one pass, via `load_attribution_index`).
"Has this observation already been attributed?" is then an O(1) set
lookup, so the run is O(observations + attributions + work) rather than
the nested O(observations x attributions) scan a naive implementation
would produce. This repo has already paid once for that mistake in the
capture path -- see docs/PERFORMANCE.md -- and is not repeating it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.data.kalshi_client import KalshiClient  # noqa: E402
from cfb_edge_finder.research import attribution as attribution_mod  # noqa: E402
from cfb_edge_finder.research import (  # noqa: E402
    git_durable_store,
    kalshi_settlement_check,
    persistence,
    result_provider,
    settlement_health,
)
from cfb_edge_finder.research.settlement import flag_mismatch, settle_market  # noqa: E402
from cfb_edge_finder.schemas.attribution import PENDING_ATTRIBUTION_STATES, AttributionState  # noqa: E402
from cfb_edge_finder.schemas.settlement import GameFinalStatus, GameResult  # noqa: E402

CLOSING_LABEL = "CLOSING"


def _series_ticker_of(market_ticker: str) -> str | None:
    """The Kalshi series a ticker belongs to, for fee-multiplier lookup.
    Tickers are `SERIES-EVENT-OUTCOME`, so the series is the first
    segment. Returns None rather than a guess if the shape is unexpected."""
    head = market_ticker.split("-", 1)[0].strip()
    return head or None


def _settleable_game_ids(repo_dir: Path, season: int) -> set[str]:
    """The games whose pending observations this run could attribute --
    what the fallback provider needs results for. Same settleable-
    population filter `_apply_attribution` uses."""
    obs_path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, season)
    if not obs_path.exists():
        return set()
    return {
        row.observation.game_id
        for row in persistence.read_observation_rows(obs_path)
        if row.observation.game_id and attribution_mod.is_settleable_population(row)
    }


def _apply_attribution(
    repo_dir: Path,
    *,
    season: int,
    results_by_game_id: dict[str, GameResult],
    now: datetime,
    report: settlement_health.SettlementHealthReport,
    kalshi_client: KalshiClient | None,
    run_id: str | None,
) -> persistence.AppendResult:
    """The per-attempt unit of work `commit_and_push_with_retry` calls.

    Both indexes are built HERE, once per attempt rather than once per
    process, for the same reason the capture path does: a retry runs
    against a hard-reset working tree whose ledgers have genuinely changed
    underneath us, and deduping against stale content would reintroduce
    duplicate rows."""
    base_dir = repo_dir / "data" / "research"
    obs_path = persistence.canonical_path(base_dir, persistence.OBSERVATIONS_SUBDIR, season)
    attr_path = persistence.canonical_path(base_dir, persistence.ATTRIBUTIONS_SUBDIR, season)

    rows = persistence.read_observation_rows(obs_path) if obs_path.exists() else []
    index = persistence.load_attribution_index(attr_path)
    report.observations_scanned = len(rows)

    # One pass to group by market and to find each market's CLOSING row.
    closing_by_ticker = {}
    rows_by_game: dict[str, list] = {}
    for row in rows:
        obs = row.observation
        if obs.snapshot_timing.label == CLOSING_LABEL:
            closing_by_ticker[obs.kalshi_market_ticker] = row
        if obs.game_id:
            rows_by_game.setdefault(obs.game_id, []).append(row)

    pending = [
        row
        for row in rows
        if not index.already_attributed(row.observation_key, attribution_mod.ATTRIBUTION_CODE_VERSION)
    ]
    report.unsettled_eligible = sum(1 for r in pending if attribution_mod.is_settleable_population(r))

    # Derive each GAME's result once, and each MARKET's settlement once --
    # then attribute it to every observation of that market. The expensive
    # derivation never repeats per checkpoint.
    result_cache: dict[str, object] = {}
    settlement_cache: dict[tuple[str, str], object] = {}
    kalshi_cache: dict[str, kalshi_settlement_check.KalshiMarketOutcome] = {}

    attributions = []
    for row in pending:
        obs = row.observation
        game_id = obs.game_id
        settlement = None
        market_is_final = None

        if game_id and attribution_mod.is_settleable_population(row):
            if game_id not in result_cache:
                # The provider already resolved every game this run has a canonical result for
                # (CFBD primary, or the strictly-validated ESPN fallback). A game it FAILED
                # CLOSED on is simply absent here -- its observations stay pending, exactly
                # like a game CFBD had no row for.
                result = results_by_game_id.get(game_id)
                report.games_checked += 1
                if result is not None and result.status is GameFinalStatus.FINAL:
                    report.games_newly_final += 1
                result_cache[game_id] = result
            result = result_cache[game_id]

            if result is not None:
                cache_key = (game_id, obs.kalshi_market_ticker)
                if cache_key not in settlement_cache:
                    settlement_cache[cache_key] = settle_market(obs, result, settled_at=now)
                settlement = settlement_cache[cache_key]

                # Cross-check against Kalshi's own finalized result, once
                # per market, only when the game is actually final.
                if kalshi_client is not None and result.status is GameFinalStatus.FINAL:
                    ticker = obs.kalshi_market_ticker
                    if ticker not in kalshi_cache:
                        kalshi_cache[ticker] = kalshi_settlement_check.fetch_market_outcome(kalshi_client, ticker)
                    outcome = kalshi_cache[ticker]
                    if outcome.fetch_failed:
                        report.api_failures += 1
                    elif outcome.official_settlement is not None:
                        settlement = flag_mismatch(settlement, outcome.official_settlement)
                        settlement_cache[cache_key] = settlement
                    market_is_final = outcome.is_finalized

        attribution = attribution_mod.attribute_observation(
            row,
            settlement,
            settled_at=now,
            closing_row=closing_by_ticker.get(obs.kalshi_market_ticker),
            series_ticker=_series_ticker_of(obs.kalshi_market_ticker),
            result_fetched_at=now,
            run_id=run_id,
            require_market_final=kalshi_client is not None,
            market_is_final=market_is_final,
        )
        attributions.append(attribution)

    for a in attributions:
        state = a.state
        if state is AttributionState.SETTLED_YES:
            report.settled_yes += 1
        elif state is AttributionState.SETTLED_NO:
            report.settled_no += 1
        elif state is AttributionState.GAME_NOT_FINAL:
            report.game_not_final += 1
        elif state is AttributionState.MARKET_NOT_FINAL:
            report.market_not_final += 1
        elif state is AttributionState.RESULT_UNAVAILABLE:
            report.result_unavailable += 1
        elif state is AttributionState.SEMANTICS_UNRESOLVED:
            report.semantics_unresolved += 1
        elif state is AttributionState.MAPPING_UNRESOLVED:
            report.mapping_unresolved += 1
        elif state is AttributionState.NOT_APPLICABLE_UNSUPPORTED_POPULATION:
            report.unsupported_population += 1
        elif state is AttributionState.SETTLEMENT_MISMATCH:
            report.settlement_mismatches += 1
        if state in (AttributionState.SETTLED_YES, AttributionState.SETTLED_NO):
            if a.closing is not None and a.closing.closing_captured:
                report.closing_captured += 1
            else:
                report.closing_missing += 1

    # Only TERMINAL states are persisted. A GAME_NOT_FINAL observation is
    # not a research fact -- it is "ask again later" -- and writing it
    # would permanently consume that observation's attribution key,
    # preventing the real settlement from ever being recorded.
    durable = [a for a in attributions if a.state not in PENDING_ATTRIBUTION_STATES]
    result = persistence.append_attribution_rows(attr_path, durable, index=index)
    report.attributions_written = result.written
    report.duplicate_attempts = result.skipped_duplicate
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--data-repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-branch", default="research-data")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--trigger-type", default="local")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument(
        "--skip-kalshi-crosscheck",
        action="store_true",
        help="Skip the read-only Kalshi settlement cross-check (offline/rehearsal use).",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.cfbd_api_key:
        print("ERROR: CFBD_API_KEY not set.", file=sys.stderr)
        return 2

    started = time.perf_counter()
    now = datetime.now(UTC)
    report = settlement_health.SettlementHealthReport()

    cfbd_client = CFBDClient(api_key=settings.cfbd_api_key)

    # Durable data first: the fallback provider needs the observation
    # ledger and the durable schedule identity BEFORE any network result
    # fetch. With --no-push the checkout is skipped, so --data-repo-dir
    # must already contain the durable data.
    if not args.no_push:
        git_durable_store.ensure_branch_checked_out(args.data_repo_dir, args.data_branch)

    needed_game_ids = _settleable_game_ids(args.data_repo_dir, args.season)

    try:
        provider_outcome = result_provider.resolve_game_results(
            season=args.season,
            now=now,
            cfbd_client=cfbd_client,
            repo_dir=args.data_repo_dir,
            needed_game_ids=needed_game_ids,
        )
    except CFBDAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except result_provider.ResultProviderUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    kalshi_client = None if args.skip_kalshi_crosscheck else KalshiClient()

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        return _apply_attribution(
            repo_dir,
            season=args.season,
            results_by_game_id=provider_outcome.results_by_game_id,
            now=now,
            report=report,
            kalshi_client=kalshi_client,
            run_id=args.run_id,
        )

    if args.no_push:
        apply_fn(args.data_repo_dir)
    else:
        push_result = git_durable_store.commit_and_push_with_retry(
            args.data_repo_dir,
            args.data_branch,
            apply_fn,
            commit_message=(
                f"research attribution: season={args.season} run={args.run_id or 'local'} at={now.isoformat()}"
            ),
        )
        print(f"Pushed after {push_result.attempts} attempt(s).")

    report.wall_clock_seconds = time.perf_counter() - started
    diagnostics = settlement_health.evaluate_settlement_health(report)

    summary = {
        "trigger_type": args.trigger_type,
        **provider_outcome.summary_dict(),
        "observations_scanned": report.observations_scanned,
        "unsettled_eligible": report.unsettled_eligible,
        "games_checked": report.games_checked,
        "games_newly_final": report.games_newly_final,
        "attributions_written": report.attributions_written,
        "duplicate_attempts": report.duplicate_attempts,
        "settled_yes": report.settled_yes,
        "settled_no": report.settled_no,
        "game_not_final": report.game_not_final,
        "market_not_final": report.market_not_final,
        "result_unavailable": report.result_unavailable,
        "semantics_unresolved": report.semantics_unresolved,
        "mapping_unresolved": report.mapping_unresolved,
        "unsupported_population": report.unsupported_population,
        "settlement_mismatches": report.settlement_mismatches,
        "closing_captured": report.closing_captured,
        "closing_missing": report.closing_missing,
        "api_failures": report.api_failures,
        "wall_clock_seconds": round(report.wall_clock_seconds, 3),
        "diagnostics": [{"severity": d.severity.value, "code": d.code, "detail": d.detail} for d in diagnostics],
    }
    print(json.dumps(summary, indent=2))
    print(
        "\nSTATUS: RESEARCH-ONLY settlement attribution. Hypothetical one-contract measurement only -- "
        "no bet, stake, bankroll, recommendation, or order anywhere in this output."
    )
    return 1 if settlement_health.should_fail_settlement_run(diagnostics) else 0


if __name__ == "__main__":
    raise SystemExit(main())
