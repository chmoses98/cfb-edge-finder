"""The PRE-OPTIMIZATION scanner inner loop, extracted verbatim from
`scripts/research_scan_and_capture.py` at main@6015276 (the commit this
performance work branched from).

This file is a frozen REFERENCE, never production code and never imported
by anything but tests. It is what
`tests/test_research_scan_equivalence.py` diffs the optimized scanner
against, so "the output did not change" is proven by re-running the old
algorithm on the same inputs rather than asserted from review.

Do not "fix", optimize, or reformat the body below: its per-ticker
`read_observation_rows` and per-row `append_observation_rows` calls ARE
the behaviour under test. If a legitimate research-semantics change ever
makes this diverge from the optimized scanner, that divergence must be
reviewed deliberately and this file re-extracted from the new baseline --
not silently patched to agree.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import capture_kalshi_cfb_snapshot as milestone_d  # noqa: E402

from cfb_edge_finder.data.kalshi_client import KalshiClient  # noqa: E402
from cfb_edge_finder.kalshi.fee_schedule import KALSHI_FEE_SCHEDULE_2026_07_07_TAKER  # noqa: E402
from cfb_edge_finder.kalshi.game_mapping import KalshiGameMappingResult, map_kalshi_event_to_game  # noqa: E402
from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest  # noqa: E402
from cfb_edge_finder.kalshi.ladder_pricing import price_one_market  # noqa: E402
from cfb_edge_finder.research import health, persistence, scan_logic, timing  # noqa: E402
from cfb_edge_finder.research.scan_logic import StaleScheduleGuardError  # noqa: E402
from cfb_edge_finder.schemas.capture_state import CaptureState, CaptureStateRecord  # noqa: E402
from cfb_edge_finder.schemas.corpus_row import CORPUS_SCHEMA_VERSION  # noqa: E402
from cfb_edge_finder.schemas.data_versions import DataVersionManifest  # noqa: E402
from cfb_edge_finder.schemas.game import GameRecord  # noqa: E402
from cfb_edge_finder.schemas.kalshi_observation import SnapshotTiming  # noqa: E402
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion  # noqa: E402

FEATURE_VERSION = "features_v1_c2_ratings"
MAPPING_VERSION = "kalshi_game_mapping_v1"


def _build_data_versions(model_version: ModelVersion, captured_at: datetime) -> DataVersionManifest:
    """Provenance stamp. Deliberately kept in step with production.

    What this reference freezes is the SCANNING ALGORITHM -- the thing the
    Mission 1 performance work rewrote, and the thing the equivalence
    tests exist to prove unchanged. The manifest below is a constant
    stamped identically onto every row by both implementations; it is an
    input to the algorithm, not an output of it. Updating it in step with
    a deliberate schema change therefore preserves what the comparison
    tests, rather than weakening it.

    What would NOT be legitimate: editing the loop, the dedup rule, the
    due-label resolution, or the row-ordering below to match production
    after a behavioural change. Those are the algorithm. If one of them
    ever disagrees, the answer is to fix production or justify the
    difference -- never to reshape this file until the test goes green.
    """
    return DataVersionManifest(
        model_version=model_version.model_version,
        feature_version=FEATURE_VERSION,
        cfbd_capture_timestamp=captured_at,
        kalshi_capture_timestamp=captured_at,
        mapping_version=MAPPING_VERSION,
        fee_schedule_version=KALSHI_FEE_SCHEDULE_2026_07_07_TAKER.version_label,
        settlement_version=None,
        snapshot_schema_version=CORPUS_SCHEMA_VERSION,
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
) -> persistence.AppendResult:
    """The per-attempt unit of work `commit_and_push_with_retry` calls --
    reads the CURRENT on-disk canonical file fresh each retry (via
    persistence.read_observation_keys), so re-running this after a reset
    to the fresh remote tip correctly re-detects rows the other run
    already wrote."""
    base_dir = repo_dir / "data" / "research"
    provenance = DataProvenance(schedule_source="cfbd", data_timestamp=now)
    data_versions = _build_data_versions(model_version, now)

    total_written = 0
    total_skipped = 0
    keys_written: list[str] = []
    capture_state_rows: list[CaptureStateRecord] = []

    for series_ticker, family in milestone_d.CORE_V1_SERIES_TO_FAMILY.items():
        markets = milestone_d._fetch_active_markets_safe(kalshi_client, series_ticker)  # noqa: SLF001
        report.markets_scanned += len(markets)
        markets_by_event: dict[str, list[dict]] = {}
        for market in markets:
            markets_by_event.setdefault(str(market.get("event_ticker", "")), []).append(market)

        for event_ticker, event_markets in markets_by_event.items():
            probe_market = event_markets[0]
            evidence = milestone_d._evidence_from_market(probe_market, event_ticker)  # noqa: SLF001
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

            matched_game = (
                next((g for g in games if g.game_id == mapping.game_id), None) if mapping.game_id else None
            )
            cached_projection = None
            home_cls = away_cls = None
            training_cutoff_str = None
            if matched_game is not None:
                home_cls, away_cls = classification_by_game_id.get(matched_game.game_id, (None, None))
                if home_cls == "fbs" and away_cls == "fbs":
                    request = GameProjectionRequest(
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
                    try:
                        cached_projection = cache.get_or_build(request)
                        training_cutoff_str = training_cutoff_fn(request)
                    except ValueError:
                        pass

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
                obs_path = persistence.canonical_path(base_dir, persistence.OBSERVATIONS_SUBDIR, season)
                existing_rows = persistence.read_observation_rows(obs_path) if obs_path.exists() else []
                already_captured_for_ticker = {
                    r.observation.snapshot_timing.label
                    for r in existing_rows
                    if r.observation.kalshi_market_ticker == ticker
                }

                due_labels = timing.resolve_due_labels(
                    kickoff_utc=kickoff,
                    now=now,
                    already_captured_labels=already_captured_for_ticker,
                    game_started=game_started,
                )
                report.captures_due += len(due_labels)
                if not due_labels:
                    continue

                for label in due_labels:
                    elapsed = timing.hours_before_kickoff(kickoff, now) if kickoff is not None else None
                    snapshot_timing = SnapshotTiming(label=label, hours_before_kickoff=elapsed)
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
                    if observation.pricing_status == "model_priced":
                        report.supported_markets += 1

                    row = scan_logic.build_corpus_row(
                        observation=observation,
                        season=season,
                        kickoff_utc_at_capture=kickoff,
                        game_status_at_capture=matched_game.status if matched_game is not None else "unknown",
                        schedule_source_timestamp=schedule_source_timestamp,
                        data_versions=data_versions,
                        run_id=run_id,
                    )
                    result = persistence.append_observation_rows(obs_path, [row])
                    total_written += result.written
                    total_skipped += result.skipped_duplicate
                    keys_written.extend(result.keys_written)

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

    if capture_state_rows:
        state_path = persistence.canonical_path(base_dir, persistence.CAPTURE_STATE_SUBDIR, season)
        persistence.append_capture_state_rows(state_path, capture_state_rows)

    report.captures_written += total_written
    report.captures_skipped_already_present += total_skipped
    return persistence.AppendResult(
        written=total_written, skipped_duplicate=total_skipped, keys_written=tuple(keys_written)
    )

