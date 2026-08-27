#!/usr/bin/env python3
"""Milestone E, Part B: the single scheduled scanner.

Scans the whole not-started CFB slate ONCE per run, decides which timing
buckets are due for each already-discovered Kalshi market, captures
exactly those, and persists the result durably (append-only, deterministic
dedup -- see research/persistence.py + research/git_durable_store.py).
Reuses Milestone D's live-fetch/parse/price wiring from
capture_kalshi_cfb_snapshot.py verbatim (imported, not duplicated) --
this script's own new logic is scheduling (research/timing.py), identity
(research/scan_logic.py), and durable persistence.

    python scripts/research_scan_and_capture.py --schedule-season 2026 \\
        --history-seasons 2022 2023 2024 2025 --data-repo-dir /path/to/checkout

Exits non-zero (mission section 19: "fail loud on high-severity data-
integrity issues") when research.health.should_fail_run(...) is True.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Reuse Milestone D's live-fetch/parse wiring verbatim rather than
# duplicating it -- these are the same helpers scripts/capture_kalshi_cfb_
# snapshot.py itself calls; this script only changes SCHEDULING (which
# ticker/label to capture this run) and PERSISTENCE (durable, not printed).
import capture_kalshi_cfb_snapshot as milestone_d  # noqa: E402

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.data.kalshi_client import KalshiClient  # noqa: E402
from cfb_edge_finder.kalshi.game_mapping import KalshiGameMappingResult, map_kalshi_event_to_game  # noqa: E402
from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest  # noqa: E402
from cfb_edge_finder.kalshi.ladder_pricing import price_one_market  # noqa: E402
from cfb_edge_finder.research import (  # noqa: E402
    closing_capture,
    git_durable_store,
    health,
    persistence,
    scan_logic,
    timing,
)
from cfb_edge_finder.research.scan_logic import StaleScheduleGuardError  # noqa: E402
from cfb_edge_finder.research.scan_telemetry import ScanTelemetry  # noqa: E402
from cfb_edge_finder.schemas.capture_state import CaptureState, CaptureStateRecord  # noqa: E402
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow  # noqa: E402
from cfb_edge_finder.schemas.data_versions import DataVersionManifest  # noqa: E402
from cfb_edge_finder.schemas.game import GameRecord  # noqa: E402
from cfb_edge_finder.schemas.kalshi_observation import SnapshotTiming  # noqa: E402
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion  # noqa: E402

FEATURE_VERSION = "features_v1_c2_ratings"
MAPPING_VERSION = "kalshi_game_mapping_v1"


def _build_data_versions(model_version: ModelVersion, captured_at: datetime) -> DataVersionManifest:
    return DataVersionManifest(
        model_version=model_version.model_version,
        feature_version=FEATURE_VERSION,
        cfbd_capture_timestamp=captured_at,
        kalshi_capture_timestamp=captured_at,
        mapping_version=MAPPING_VERSION,
        fee_schedule_version=None,
        settlement_version=None,
        snapshot_schema_version="research_corpus_v1",
    )


def _apply_scan(
    repo_dir: Path,
    *,
    season: int,
    games: list[GameRecord],
    classification_by_game_id: dict[str, tuple[str | None, str | None]],
    fcs_school_names: frozenset[str],
    cache: GameProjectionCache,
    kalshi_client: KalshiClient,
    model_version: ModelVersion,
    training_cutoff_fn,
    n_simulations: int,
    seed: int,
    now: datetime,
    schedule_source_timestamp: datetime,
    run_id: str | None,
    report: health.CaptureHealthReport,
    telemetry: ScanTelemetry,
) -> persistence.AppendResult:
    """The per-attempt unit of work `commit_and_push_with_retry` calls --
    reads the CURRENT on-disk canonical file fresh each retry, so
    re-running this after a reset to the fresh remote tip correctly
    re-detects rows the other run already wrote.

    *** ONE HISTORY LOAD PER ATTEMPT (performance hardening) ***
    That "fresh each retry" requirement is why `load_observation_index`
    is called HERE, at the top of the per-attempt function, and not once
    in `main()`: a retry runs against a hard-reset working tree whose
    observations file has genuinely changed underneath us, so an index
    hoisted out of this function would make the retry dedup against stale
    content -- silently reintroducing exactly the duplicate-row race
    research/git_durable_store.py exists to prevent. Once per ATTEMPT is
    the correct scope; the bug being fixed was once per TICKER.

    Every lookup the per-ticker loop used to derive by re-reading the
    whole file (a full pydantic re-validation of the entire corpus, then
    another full JSON pass inside every append) now comes off this one
    index, and rows are BUFFERED and written in a single appending,
    fsync'd batch at the end -- see the write-strategy note there."""
    base_dir = repo_dir / "data" / "research"
    provenance = DataProvenance(schedule_source="cfbd", data_timestamp=now)
    data_versions = _build_data_versions(model_version, now)

    obs_path = persistence.canonical_path(base_dir, persistence.OBSERVATIONS_SUBDIR, season)
    index = persistence.load_observation_index(obs_path)
    telemetry.history_load_count += index.load_count
    telemetry.history_load_seconds += index.load_seconds
    telemetry.history_row_count = index.row_count
    telemetry.malformed_row_count = index.malformed_rows

    # `games` is scanned by game_id once per mapped event; at ~3.5k
    # scheduled games and ~1.5k events that linear `next(...)` search was
    # millions of comparisons per run for a lookup a dict does in O(1).
    games_by_id = {g.game_id: g for g in games}

    pending_rows: list[ResearchCorpusRow] = []
    capture_state_rows: list[CaptureStateRecord] = []
    # Distinct games this run actually PROJECTED, not the size of the
    # schedule -- the denominator for "contracts priced per projection"
    # (mission section 7) only means anything if it counts games that
    # genuinely reached the model.
    projected_game_ids: set[str] = set()

    for series_ticker, family in milestone_d.CORE_V1_SERIES_TO_FAMILY.items():
        with telemetry.phase("market_discovery_seconds"):
            markets = milestone_d._fetch_active_markets_safe(kalshi_client, series_ticker)  # noqa: SLF001
        report.markets_scanned += len(markets)
        telemetry.discovered_market_count += len(markets)
        markets_by_event: dict[str, list[dict]] = {}
        for market in markets:
            markets_by_event.setdefault(str(market.get("event_ticker", "")), []).append(market)

        for event_ticker, event_markets in markets_by_event.items():
            probe_market = event_markets[0]
            evidence = milestone_d._evidence_from_market(probe_market, event_ticker)  # noqa: SLF001
            with telemetry.phase("game_mapping_seconds"):
                mapping: KalshiGameMappingResult = map_kalshi_event_to_game(
                    evidence, games, fcs_school_names=fcs_school_names
                )
            # See research.scan_logic.is_genuine_mapping_failure's own
            # docstring: a live rehearsal caught a cruder
            # `mapping.reason is not None` check here also counting
            # FCS_VS_FCS (a correctly-classified, understood population)
            # as a failure, making a routine ~45% FCS-involved early-
            # season slate look like a 72% mapping failure rate.
            if scan_logic.is_genuine_mapping_failure(mapping.reason):
                report.mapping_failures += len(event_markets)

            matched_game = games_by_id.get(mapping.game_id) if mapping.game_id else None
            home_cls = away_cls = None
            projection_request: GameProjectionRequest | None = None
            if matched_game is not None:
                home_cls, away_cls = classification_by_game_id.get(matched_game.game_id, (None, None))
                if home_cls == "fbs" and away_cls == "fbs":
                    projection_request = GameProjectionRequest(
                        game_id=matched_game.game_id,
                        home_id=matched_game.home_team_id,
                        away_id=matched_game.away_team_id,
                        home_classification=home_cls,
                        away_classification=away_cls,
                        is_neutral_site=matched_game.neutral_site,
                        as_of_season=matched_game.season,
                        as_of_week=matched_game.week_number or 0,
                        n_simulations=n_simulations,
                        seed=seed,
                    )

            # *** LAZY PROJECTION (prospective-collection milestone) ***
            # The football model used to run for EVERY mapped event on
            # EVERY scan, before anything checked whether a checkpoint was
            # actually due -- ~102 projections and a full multi-season
            # CFBD history fetch per run even when the run went on to
            # write nothing at all. That was affordable hourly; at the
            # 10-minute cadence the closing window requires it is not, and
            # it is pure waste besides.
            #
            # Deferring is output-NEUTRAL: `cached_projection` is only
            # ever consumed by `price_one_market`, which is only reached
            # when `due_labels` is non-empty. Projection inputs do not
            # depend on due-ness, so a row priced from a lazily-built
            # projection is byte-identical to one priced from an eagerly-
            # built one. GameProjectionCache still memoises per game, so
            # a ladder of 30 contracts on one game still costs exactly one
            # projection (see tests/test_research_scan_projection_reuse).
            event_projection: list = []  # memo cell: [] = not attempted yet

            def _projection_for_event(
                request: GameProjectionRequest | None = projection_request,
                memo: list = event_projection,
                game: GameRecord | None = matched_game,
            ) -> tuple[object | None, str | None]:
                if memo:
                    return memo[0]
                if request is None:
                    memo.append((None, None))
                    return memo[0]
                try:
                    with telemetry.phase("projection_seconds"):
                        built = cache.get_or_build(request)
                    resolved = (built, training_cutoff_fn(request))
                    if game is not None:
                        projected_game_ids.add(game.game_id)
                except ValueError:
                    resolved = (None, None)
                memo.append(resolved)
                return resolved

            game_started = matched_game is not None and matched_game.status != "scheduled"
            kickoff = matched_game.kickoff_utc if matched_game is not None else None

            try:
                if matched_game is not None:
                    scan_logic.guard_capture_allowed(
                        game_status=matched_game.status,
                        schedule_source_timestamp=schedule_source_timestamp,
                        now=now,
                    )
            except StaleScheduleGuardError:
                report.stale_schedule_failures += len(event_markets)
                continue

            for market in event_markets:
                ticker = str(market.get("ticker", ""))
                # WAS: a full `read_observation_rows(obs_path)` (pydantic-
                # validating EVERY historical row) right here, once per
                # ticker, to compute this one small set. The index built
                # once above already holds it, and stays live as rows are
                # buffered, so this is the same set the disk read returned
                # -- including for a ticker visited twice in one run.
                already_captured_for_ticker = set(index.captured_labels_for(ticker))

                due_labels = timing.resolve_due_labels(
                    kickoff_utc=kickoff,
                    now=now,
                    already_captured_labels=already_captured_for_ticker,
                    game_started=game_started,
                )
                report.captures_due += len(due_labels)
                if timing.CLOSING in due_labels:
                    report.closing_due += 1
                    telemetry.closing_due_count += 1
                if not due_labels:
                    continue

                # Only now -- with at least one genuinely due checkpoint --
                # is the expensive football model allowed to run.
                cached_projection, training_cutoff_str = _projection_for_event()

                for label in due_labels:
                    elapsed = timing.hours_before_kickoff(kickoff, now) if kickoff is not None else None
                    snapshot_timing = SnapshotTiming(label=label, hours_before_kickoff=elapsed)
                    with telemetry.phase("contract_pricing_seconds"):
                        observation = price_one_market(
                            market,
                            family_hint=family,
                            event_ticker=event_ticker,
                            series_ticker=series_ticker,
                            mapping=mapping,
                            home_classification=home_cls,
                            away_classification=away_cls,
                            cached_projection=cached_projection,
                            captured_at=now,
                            snapshot_id=str(uuid.uuid4()),
                            snapshot_timing=snapshot_timing,
                            model_version=model_version,
                            training_cutoff=training_cutoff_str,
                            provenance=provenance,
                        )
                    telemetry.observation_count += 1
                    if label == timing.CLOSING:
                        report.closing_captured += 1
                        telemetry.closing_captured_count += 1
                    if observation.pricing_status == "model_priced":
                        report.supported_markets += 1
                        telemetry.priced_contract_count += 1
                    else:
                        telemetry.unresolved_count += 1

                    row = scan_logic.build_corpus_row(
                        observation=observation,
                        season=season,
                        kickoff_utc_at_capture=kickoff,
                        game_status_at_capture=matched_game.status if matched_game is not None else "unknown",
                        schedule_source_timestamp=schedule_source_timestamp,
                        data_versions=data_versions,
                        run_id=run_id,
                    )
                    # WAS: `append_observation_rows(obs_path, [row])` per
                    # row -- each call re-read the whole file for dedup
                    # keys and re-opened/fsync'd the file for one line.
                    # Buffered instead and written in ONE appending,
                    # fsync'd batch below; `register_pending` keeps the
                    # index's scheduling view current in the meantime, so
                    # nothing downstream can tell the difference.
                    pending_rows.append(row)
                    index.register_pending(row.model_dump(mode="json"))

                    capture_state_rows.append(
                        CaptureStateRecord(
                            game_id=observation.game_id or "unmapped",
                            kalshi_market_ticker=ticker,
                            timing_label=label,
                            state=CaptureState.CAPTURED,
                            observed_at=now,
                            detail=f"captured via run {run_id or 'local'}",
                            run_id=run_id,
                        )
                    )

                # *** CLOSING COMPLETENESS ACCOUNTING (mission section 18) ***
                # Whenever a market is inside (or has just passed) its
                # closing window, record exactly one explicit closing
                # outcome. Silence is never an option: a market that
                # reaches kickoff without a CLOSING row must say WHY in
                # the capture-state log, so a missing closing is always
                # attributable rather than merely absent.
                if kickoff is not None and matched_game is not None:
                    minutes_out = timing.minutes_before_kickoff(kickoff, now)
                    in_or_past_window = minutes_out <= timing.CLOSING_WINDOW_MINUTES
                    already_closed = timing.CLOSING in already_captured_for_ticker
                    if in_or_past_window and not already_closed and timing.CLOSING not in due_labels:
                        eligibility = closing_capture.evaluate_closing_eligibility(
                            market_status=str(market.get("status") or "") or None,
                            executable_yes_price=None,
                            executable_no_price=None,
                            mapping_failed=mapping.game_id is None,
                            is_supported_population=(home_cls == "fbs" and away_cls == "fbs"),
                            minutes_before_kickoff=minutes_out,
                        )
                        report.closing_missing += 1
                        capture_state_rows.append(
                            CaptureStateRecord(
                                game_id=mapping.game_id or "unmapped",
                                kalshi_market_ticker=ticker,
                                timing_label=timing.CLOSING,
                                state=CaptureState.OTHER_EXPLICIT_REASON,
                                observed_at=now,
                                detail=f"{eligibility.status.value}: {eligibility.detail}",
                                run_id=run_id,
                            )
                        )

                if kickoff is not None:
                    seen_labels = already_captured_for_ticker | set(due_labels)
                    states = timing.resolve_all_bucket_states(
                        kickoff_utc=kickoff, now=now, already_captured_labels=seen_labels, game_started=game_started
                    )
                    for label, state in states.items():
                        if state == CaptureState.MISSED_WINDOW:
                            report.missed_windows += 1
                            capture_state_rows.append(
                                CaptureStateRecord(
                                    game_id=mapping.game_id or "unmapped",
                                    kalshi_market_ticker=ticker,
                                    timing_label=label,
                                    state=CaptureState.MISSED_WINDOW,
                                    observed_at=now,
                                    detail="window closed before a capture occurred",
                                    run_id=run_id,
                                )
                            )

    # *** WRITE STRATEGY ***
    # ONE append per file per attempt, still strictly append-only and
    # still fsync'd (research/persistence.py's `append_json_rows` is
    # unchanged in that respect): existing lines are never rewritten,
    # never reordered, and never re-serialized -- the corpus is not
    # rewritten wholesale at any point, before or after this change.
    # Batching only removes the repeated open/read/fsync per row; the
    # durability boundary that actually matters is still the git commit
    # in research/git_durable_store.py, which is all-or-nothing per
    # attempt, so a crash mid-scan leaves the durable store exactly as
    # untouched as it did before.
    with telemetry.phase("persistence_write_seconds"):
        result = persistence.append_observation_rows(obs_path, pending_rows, index=index)
        if capture_state_rows:
            state_path = persistence.canonical_path(base_dir, persistence.CAPTURE_STATE_SUBDIR, season)
            persistence.append_capture_state_rows(state_path, capture_state_rows)

    telemetry.distinct_games = len(projected_game_ids)
    telemetry.duplicate_count += result.skipped_duplicate
    report.captures_written += result.written
    report.captures_skipped_already_present += result.skipped_duplicate
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schedule-season", type=int, default=2026)
    parser.add_argument("--history-seasons", type=int, nargs="+", default=[2022, 2023, 2024, 2025])
    parser.add_argument("--n-simulations", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--data-repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-branch", default="research-data")
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--no-push", action="store_true", help="Write locally only; skip the git commit/push step (rehearsal mode)."
    )
    parser.add_argument(
        "--trigger-type",
        default="local",
        help=(
            "How this run was triggered ('schedule', 'workflow_dispatch', 'local'). Recorded in telemetry "
            "for provenance ONLY -- it never changes due-label resolution, duplicate protection, or what "
            "gets written, so a manual run and a scheduled run produce compatible research artifacts."
        ),
    )
    parser.add_argument(
        "--telemetry-json",
        type=Path,
        default=None,
        help="Also write this run's performance telemetry to a JSON file (it is always printed).",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.cfbd_api_key:
        print("ERROR: CFBD_API_KEY not set.", file=sys.stderr)
        return 2

    now = datetime.now(UTC)
    cfbd_client = CFBDClient(api_key=settings.cfbd_api_key)
    report = health.CaptureHealthReport()
    telemetry = ScanTelemetry(trigger_type=args.trigger_type)

    try:
        games, classification_by_game_id = milestone_d._fetch_candidate_games(args.schedule_season, cfbd_client, now)  # noqa: SLF001
        fcs_school_names = milestone_d._fetch_fcs_school_names(cfbd_client, args.schedule_season)  # noqa: SLF001
    except CFBDAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    def _fetch_history_lines():
        """Deferred to first projection. At the 10-minute collection
        cadence most scans have nothing due (checkpoint windows are
        narrow), and this is the single most expensive call the scanner
        makes -- four seasons of CFBD team-game lines. Paying it only when
        a checkpoint is genuinely due is what keeps a frequent cadence
        cheap; see docs/PROSPECTIVE_COLLECTION.md's cadence analysis."""
        with telemetry.phase("history_fetch_seconds"):
            return milestone_d._fetch_history_lines(args.history_seasons, cfbd_client, now)  # noqa: SLF001

    not_started_games = [g for g in games if g.status == "scheduled"]
    report.games_scanned = len(not_started_games)

    cache = GameProjectionCache(lines_provider=_fetch_history_lines)
    kalshi_client = KalshiClient()
    model_version = ModelVersion(
        model_version=milestone_d.MODEL_VERSION,
        ratings_component_version=milestone_d._ratings_component_version(),  # noqa: SLF001
        pricing_engine_version="0.1.0",
    )

    def training_cutoff_fn(request: GameProjectionRequest) -> str:
        return f"strictly before season={request.as_of_season} week={request.as_of_week}"

    if not args.no_push:
        git_durable_store.ensure_branch_checked_out(args.data_repo_dir, args.data_branch)

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        return _apply_scan(
            repo_dir,
            season=args.schedule_season,
            games=not_started_games,
            classification_by_game_id=classification_by_game_id,
            fcs_school_names=fcs_school_names,
            cache=cache,
            kalshi_client=kalshi_client,
            model_version=model_version,
            training_cutoff_fn=training_cutoff_fn,
            n_simulations=args.n_simulations,
            seed=args.seed,
            now=now,
            schedule_source_timestamp=now,
            run_id=args.run_id,
            report=report,
            telemetry=telemetry,
        )

    if args.no_push:
        apply_fn(args.data_repo_dir)
    else:
        push_result = git_durable_store.commit_and_push_with_retry(
            args.data_repo_dir,
            args.data_branch,
            apply_fn,
            commit_message=(
                f"research capture: season={args.schedule_season} run={args.run_id or 'local'} at={now.isoformat()}"
            ),
        )
        print(f"Pushed after {push_result.attempts} attempt(s).")

    telemetry.game_projection_count = cache.projection_builds
    telemetry.ratings_fit_count = cache.ratings_fits
    telemetry.history_fetch_count = cache.lines_fetch_count
    telemetry.finish()

    diagnostics = health.evaluate_collapse(report, baseline_supported_markets=None)
    report_dict = {
        "games_scanned": report.games_scanned,
        "markets_scanned": report.markets_scanned,
        "supported_markets": report.supported_markets,
        "captures_due": report.captures_due,
        "captures_written": report.captures_written,
        "captures_skipped_already_present": report.captures_skipped_already_present,
        "missed_windows": report.missed_windows,
        "mapping_failures": report.mapping_failures,
        "stale_schedule_failures": report.stale_schedule_failures,
        "diagnostics": [{"severity": d.severity.value, "code": d.code, "detail": d.detail} for d in diagnostics],
    }
    print(json.dumps(report_dict, indent=2))
    # One compact run-level record -- deliberately not per-ticker (see
    # research/scan_telemetry.py). `history_load_count` must read 1 per
    # scan attempt; anything higher means the per-ticker re-read
    # regressed back in.
    print("\nPERF " + json.dumps(telemetry.as_dict(), sort_keys=True))
    if args.telemetry_json is not None:
        args.telemetry_json.parent.mkdir(parents=True, exist_ok=True)
        args.telemetry_json.write_text(json.dumps(telemetry.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
    print("\nSTATUS: RESEARCH-ONLY. No bet recommendation, stake sizing, or trading action anywhere in this output.")

    return 1 if health.should_fail_run(diagnostics) else 0


if __name__ == "__main__":
    raise SystemExit(main())
