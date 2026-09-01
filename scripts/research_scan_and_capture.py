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
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# *** EVERY cfb_edge_finder IMPORT BELOW MUST STAY AT MODULE LEVEL ***
# `main` calls ensure_branch_checked_out BEFORE _apply_scan, and that
# runs `git checkout -B research-data`, replacing the working tree --
# this editable install's `src/` included -- with the research-data
# branch's own stray `src/` snapshot, which predates
# `research/preseason/` entirely. Modules already in sys.modules survive
# that swap; a function-local import does not. A deferred
# `research.preseason.corpus` import here is what silently disabled the
# shadow sidecar on a live run (see _build_shadow_sidecar).

# Reuse Milestone D's live-fetch/parse wiring verbatim rather than
# duplicating it -- these are the same helpers scripts/capture_kalshi_cfb_
# snapshot.py itself calls; this script only changes SCHEDULING (which
# ticker/label to capture this run) and PERSISTENCE (durable, not printed).
import capture_kalshi_cfb_snapshot as milestone_d  # noqa: E402

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDClient  # noqa: E402
from cfb_edge_finder.data.kalshi_client import KalshiClient  # noqa: E402
from cfb_edge_finder.kalshi.fee_schedule import KALSHI_FEE_SCHEDULE_2026_07_07_TAKER  # noqa: E402
from cfb_edge_finder.kalshi.game_mapping import KalshiGameMappingResult, map_kalshi_event_to_game  # noqa: E402
from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest  # noqa: E402
from cfb_edge_finder.kalshi.ladder_pricing import price_one_market  # noqa: E402
from cfb_edge_finder.modeling.leakage import AsOf  # noqa: E402
from cfb_edge_finder.research import (  # noqa: E402
    cfbd_access,
    checkpoint_reconciliation,
    closing_capture,
    football_state,
    git_durable_store,
    health,
    persistence,
    scan_logic,
    timing,
)
from cfb_edge_finder.research import heartbeat as heartbeat_mod  # noqa: E402
from cfb_edge_finder.research.identity import observation_key  # noqa: E402
from cfb_edge_finder.research.preseason.corpus import (  # noqa: E402
    build_feature_tables,
    load_cache,
)
from cfb_edge_finder.research.preseason.shadow_sidecar import (  # noqa: E402
    ShadowSidecar,
    shadow_key,
)
from cfb_edge_finder.research.preseason.shadow_spec import (  # noqa: E402
    CONTROL_SPEC_SHA256,
    SHADOW_SPEC_SHA256,
    assert_specs_frozen,
)
from cfb_edge_finder.research.scan_logic import StaleScheduleGuardError  # noqa: E402
from cfb_edge_finder.research.scan_telemetry import ScanTelemetry  # noqa: E402
from cfb_edge_finder.research.trigger import (  # noqa: E402
    CLOSING_GUARD_LEAD_MINUTES,
    SchedulePlanningState,
    classify_trigger,
)
from cfb_edge_finder.schemas.capture_state import CaptureState, CaptureStateRecord  # noqa: E402
from cfb_edge_finder.schemas.corpus_row import CORPUS_SCHEMA_VERSION, ResearchCorpusRow  # noqa: E402
from cfb_edge_finder.schemas.data_versions import DataVersionManifest  # noqa: E402
from cfb_edge_finder.schemas.game import GameRecord  # noqa: E402
from cfb_edge_finder.schemas.kalshi_observation import SnapshotTiming  # noqa: E402
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion  # noqa: E402

FEATURE_VERSION = "features_v1_c2_ratings"
MAPPING_VERSION = "kalshi_game_mapping_v1"


def _emit_shadow_record(
    *,
    sidecar,
    observation,
    cached_projection,
    mapping,
    matched_game,
    kickoff,
    season: int,
    now: datetime,
    home_cls,
    away_cls,
    pending: list[dict],
    seen: set[str],
) -> None:
    """Append one linked shadow record for a canonical observation.

    Dedupes on observation_key|shadow_model_version, so a retry writes
    zero duplicate rows and a future candidate version can coexist rather
    than overwriting this one's evidence. Never raises: the canonical row
    is already built and must not be endangered by research code."""
    try:
        projection = cached_projection.projection
        raw = projection.raw
        corrected_margins = (raw.home_scores - raw.away_scores) + projection.margin_delta

        key = observation_key(
            season=season,
            game_id=observation.game_id or "unmapped",
            market_ticker=observation.kalshi_market_ticker,
            timing_label=observation.snapshot_timing.label,
            model_version=(
                observation.model_version.model_version if observation.model_version else "unpriced"
            ),
        )
        dedup = shadow_key(key)
        if dedup in seen:
            return

        record = sidecar.for_contract(
            observation_key=key,
            game_id=observation.game_id or "unmapped",
            timing_label=observation.snapshot_timing.label,
            captured_at=observation.captured_at,
            kickoff_utc=kickoff,
            market_ticker=observation.kalshi_market_ticker,
            market_family=observation.family.value if observation.family else None,
            executable_yes_price=observation.executable_yes_price,
            executable_no_price=observation.executable_no_price,
            control_model_version=(
                observation.model_version.model_version if observation.model_version else None
            ),
            control_probability=observation.model_probability,
            # The canonical observation already recorded the contract's
            # proposition (family, threshold, over/under side, and the
            # RESOLVED named team side). Reusing those fields is what
            # makes the shadow price the same proposition as the control
            # instead of one probability per game.
            control_distribution=cached_projection.projection.to_game_distribution(),
            contract_family=observation.family,
            contract_side=observation.side,
            contract_threshold=observation.threshold,
            named_team_side=observation.team,
            projection_snapshot_id=cached_projection.projection_snapshot_id,
            home_team_id=(matched_game.home_team_id if matched_game else ""),
            away_team_id=(matched_game.away_team_id if matched_game else ""),
            corrected_margin_samples=corrected_margins,
            control_margin_corrected=projection.expected_margin,
            control_expected_home=projection.expected_home_points,
            control_expected_away=projection.expected_away_points,
            both_fbs=(home_cls == "fbs" and away_cls == "fbs"),
            capture_mode="PROSPECTIVE",
        )
        if record is None:
            return
        payload = record.to_dict()
        payload["shadow_key"] = dedup
        payload["projection_snapshot_id"] = cached_projection.projection_snapshot_id
        payload["talent_season"] = sidecar.talent_season
        payload["talent_fetched_at"] = sidecar.talent_fetched_at
        payload["shadow_capture_started_at"] = sidecar.shadow_capture_started_at
        seen.add(dedup)
        pending.append(payload)
    except Exception:  # noqa: BLE001
        # Counted, not silent -- but never fatal to canonical capture.
        try:
            sidecar.telemetry.shadow_failures += 1
        except Exception:  # noqa: BLE001
            pass


def _build_shadow_sidecar(
    repo_dir: Path, season: int, now: datetime
) -> tuple[ShadowSidecar | None, str]:
    """Load the frozen talent inputs for the shadow sidecar.

    Returns `(sidecar, state)`. The sidecar is None on ANY problem --
    missing cache, drifted spec, unparsable file. Canonical capture then
    proceeds exactly as it did before this module existed: a research
    side effect must never be able to cost a prospective checkpoint,
    least of all a CLOSING one, which cannot be recovered.

    *** WHY THE STATE STRING EXISTS ***
    Returning a bare None made "the sidecar could not be built" and "the
    sidecar ran and nothing was eligible" produce byte-identical
    telemetry -- every shadow counter reads 0 under both. A live run on
    main did exactly that: 158 canonical captures, 0 shadow rows, 0
    failures, and no way to tell from the log which of the two had
    happened. The state is the difference.

    *** WHY THE IMPORTS THIS NEEDS ARE AT MODULE LEVEL ***
    They used to be function-local, which looked harmless and was not.
    `main` calls `ensure_branch_checked_out` BEFORE `_apply_scan`, and
    that runs `git checkout -B research-data`, replacing the working
    tree -- including the editable install's `src/` -- with the
    research-data branch's own stray `src/` snapshot, a fossil of the
    old stray-source-tree incident that predates
    `research/preseason/` entirely. Modules already in `sys.modules`
    survive; a deferred import does not. So this function's
    `research.preseason.corpus` import raised ModuleNotFoundError on
    every real run, the broad `except` below swallowed it, and the
    sidecar was silently None in production while every test passed --
    because no test checks out the data branch mid-run. Keep them at
    module level.
    """
    try:
        # Refuse to capture against a drifted candidate: a shadow row
        # written under a changed beta would silently contaminate the
        # prospective evidence it exists to create.
        assert_specs_frozen(control_sha256=CONTROL_SPEC_SHA256, shadow_sha256=SHADOW_SPEC_SHA256)

        cache_dir = repo_dir / "data" / "research_cache" / "preseason"
        seasons = load_cache(cache_dir)
        if season not in seasons:
            return None, f"UNAVAILABLE_NO_CACHE_FOR_SEASON_{season}"
        table = build_feature_tables(seasons)[season]
        target = AsOf(season=season, week=1)
        # Every value goes through table.get(), which calls
        # PreseasonFeature.validate_for() and RAISES on a season
        # misalignment. Reading the index directly would be faster and
        # would silently skip the leakage guard -- the one check that
        # stops a talent row being applied to the season it was derived
        # from.
        teams = {team for (team, name) in table._by_key if name == "talent_composite"}
        talent: dict[str, float] = {}
        for team in sorted(teams):
            feature = table.get(team, "talent_composite", target=target)
            if feature is not None and feature.value is not None:
                talent[team] = float(feature.value)
        if not talent:
            return None, "UNAVAILABLE_NO_TALENT_VALUES"
        return (
            ShadowSidecar(
                talent_by_team=talent,
                talent_season=season,
                talent_source_version="preseason_research_cache_v1",
                talent_fetched_at=now.isoformat(),
                code_sha=_code_sha(),
                shadow_capture_started_at=now.isoformat(),
            ),
            "ACTIVE",
        )
    except Exception as exc:  # noqa: BLE001
        # The exception TYPE, never a bare count: "12 failures" is not a
        # diagnosis, "12 ModuleNotFoundError" is.
        return None, f"UNAVAILABLE_{type(exc).__name__}"


def _code_sha() -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _build_data_versions(model_version: ModelVersion, captured_at: datetime) -> DataVersionManifest:
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
    non_fbs_school_names: frozenset[str] = frozenset(),
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
    # RESEARCH SIDECAR. Built once per attempt, never per ticker. If it
    # cannot be constructed at all the canonical capture proceeds exactly
    # as before -- the shadow is a side effect, never a dependency.
    shadow_sidecar, shadow_sidecar_state = _build_shadow_sidecar(repo_dir, season, now)
    telemetry.shadow_sidecar_state = shadow_sidecar_state
    pending_shadow_rows: list[dict] = []
    seen_shadow_keys: set[str] = set()
    # Distinct games this run actually PROJECTED, not the size of the
    # schedule -- the denominator for "contracts priced per projection"
    # (mission section 7) only means anything if it counts games that
    # genuinely reached the model.
    projected_game_ids: set[str] = set()

    for series_ticker, family in milestone_d.CORE_V1_SERIES_TO_FAMILY.items():
        with telemetry.phase("market_discovery_seconds"):
            markets, fetch_failed = milestone_d._fetch_active_markets_with_status(  # noqa: SLF001
                kalshi_client, series_ticker
            )
        if fetch_failed:
            # A failed series is NOT an empty series. Counting it makes the
            # run fail loudly rather than under-reporting the market
            # universe while looking healthy (see that helper's docstring
            # for the live 429 that proved this).
            report.api_failures += 1
            telemetry.api_failure_count += 1
        report.markets_scanned += len(markets)
        telemetry.discovered_market_count += len(markets)
        markets_by_event: dict[str, list[dict]] = {}
        for market in markets:
            markets_by_event.setdefault(str(market.get("event_ticker", "")), []).append(market)

        for event_ticker, event_markets in markets_by_event.items():
            report.events_scanned += 1
            probe_market = event_markets[0]
            evidence = milestone_d._evidence_from_market(probe_market, event_ticker)  # noqa: SLF001
            with telemetry.phase("game_mapping_seconds"):
                mapping: KalshiGameMappingResult = map_kalshi_event_to_game(
                    evidence, games, fcs_school_names=fcs_school_names,
                    non_fbs_school_names=non_fbs_school_names,
                )
            # See research.scan_logic.is_genuine_mapping_failure's own
            # docstring: a live rehearsal caught a cruder
            # `mapping.reason is not None` check here also counting
            # FCS_VS_FCS (a correctly-classified, understood population)
            # as a failure, making a routine ~45% FCS-involved early-
            # season slate look like a 72% mapping failure rate. The
            # 2026-09-01 forensic audit found the same shape one level
            # up -- FBS-vs-FCS and other non-FBS fixtures landing in
            # AMBIGUOUS_TEAM_MAPPING -- hence NON_FBS_PARTICIPANT and
            # the explicit unsupported-population accounting here.
            if scan_logic.is_genuine_mapping_failure(mapping.reason):
                report.events_mapping_failed += 1
                report.mapping_failures += len(event_markets)
            elif scan_logic.is_unsupported_population(mapping.reason):
                report.markets_unsupported_population += len(event_markets)

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

            # *** KICKOFF SANITY CROSS-CHECK (two-lane decoupling) ***
            # With the schedule served from the durable artifact (up to
            # 6h old under the existing staleness guard), a game moved
            # EARLIER is the one drift direction the clock guard cannot
            # catch on its own. Kalshi's own close_time for a mapped
            # market is an independent, already-captured signal of the
            # real deadline: a disagreement beyond tolerance marks the
            # game KICKOFF-UNCERTAIN and captures NOTHING for it this
            # run -- fail closed, explicitly accounted. An absent
            # close_time skips the check (the freshness bound, the clock
            # guard, and Kalshi's active-status requirement remain in
            # force).
            kickoff_uncertain = False
            if kickoff is not None and matched_game is not None and evidence.reference_timestamp is not None:
                drift_minutes = abs((evidence.reference_timestamp - kickoff).total_seconds()) / 60.0
                if drift_minutes > football_state.KICKOFF_SANITY_TOLERANCE_MINUTES:
                    kickoff_uncertain = True
                    telemetry.kickoff_uncertain_games += 1
                    capture_state_rows.append(
                        CaptureStateRecord(
                            game_id=matched_game.game_id,
                            kalshi_market_ticker=str(probe_market.get("ticker", "")),
                            timing_label="ALL_PREGAME",
                            state=CaptureState.OTHER_EXPLICIT_REASON,
                            observed_at=now,
                            detail=(
                                f"kickoff_uncertain: cached kickoff {kickoff.isoformat()} vs Kalshi "
                                f"close_time {evidence.reference_timestamp.isoformat()} "
                                f"({drift_minutes:.0f} min apart) -- captures withheld fail-closed"
                            ),
                            run_id=run_id,
                        )
                    )
            if kickoff_uncertain:
                continue

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
                    # *** CLOSING EXECUTABILITY GATE (defense in depth) ***
                    # Market discovery already filters to status ==
                    # "active", so a suspended/closed market normally
                    # never reaches this point at all. That filter is the
                    # PRIMARY protection; this gate is the secondary one,
                    # and it exists because CLOSING is the one checkpoint
                    # that cannot be recovered if we get it wrong. If a
                    # market's status is anything but executable, or it
                    # produced no executable quote, we record WHY rather
                    # than writing a closing row that looks tradeable.
                    if label == timing.CLOSING:
                        eligibility = closing_capture.evaluate_closing_eligibility(
                            market_status=observation.market_status,
                            executable_yes_price=observation.executable_yes_price,
                            executable_no_price=observation.executable_no_price,
                            mapping_failed=observation.game_id is None,
                            is_supported_population=(home_cls == "fbs" and away_cls == "fbs"),
                            minutes_before_kickoff=(
                                timing.minutes_before_kickoff(kickoff, now) if kickoff is not None else None
                            ),
                        )
                        if not eligibility.eligible:
                            report.closing_missing += 1
                            capture_state_rows.append(
                                CaptureStateRecord(
                                    game_id=observation.game_id or "unmapped",
                                    kalshi_market_ticker=ticker,
                                    timing_label=timing.CLOSING,
                                    state=CaptureState.OTHER_EXPLICIT_REASON,
                                    observed_at=now,
                                    detail=f"{eligibility.status.value}: {eligibility.detail}",
                                    run_id=run_id,
                                )
                            )
                            continue

                    telemetry.observation_count += 1
                    if label == timing.CLOSING:
                        report.closing_captured += 1
                        telemetry.closing_captured_count += 1
                    if observation.pricing_status == "model_priced":
                        report.supported_markets += 1
                        telemetry.priced_contract_count += 1
                    else:
                        telemetry.unresolved_count += 1

                    # ------------------------------------------------
                    # RESEARCH SIDECAR: linked talent-shadow record.
                    # Runs AFTER the canonical observation exists and can
                    # only add a row; it never edits, blocks or replaces
                    # one. Anything unexpected inside returns None.
                    # ------------------------------------------------
                    if shadow_sidecar is not None and cached_projection is not None:
                        _emit_shadow_record(
                            sidecar=shadow_sidecar,
                            observation=observation,
                            cached_projection=cached_projection,
                            mapping=mapping,
                            matched_game=matched_game,
                            kickoff=kickoff,
                            season=season,
                            now=now,
                            home_cls=home_cls,
                            away_cls=away_cls,
                            pending=pending_shadow_rows,
                            seen=seen_shadow_keys,
                        )

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
        state_path = persistence.canonical_path(base_dir, persistence.CAPTURE_STATE_SUBDIR, season)
        if capture_state_rows:
            persistence.append_capture_state_rows(state_path, capture_state_rows)
        # After-the-fact reconciliation: durable-data-only accounting for
        # windows that provably passed uncaptured (e.g. during a
        # collector or dependency outage). Idempotent via the existing
        # capture-state dedup key; never creates an observation.
        telemetry.reconciled_missed_checkpoints = checkpoint_reconciliation.reconcile(
            obs_path, state_path, now=now, run_id=run_id, index=index
        )
        # Shadow rows are written AFTER the canonical observations and in
        # their own file. Ordering matters: if this raised, the canonical
        # rows are already durable. It is wrapped anyway, because a
        # research side effect must never fail a prospective capture.
        if pending_shadow_rows:
            try:
                shadow_path = persistence.canonical_path(
                    base_dir, persistence.SHADOW_SUBDIR, season
                )
                shadow_result = persistence.append_json_rows(
                    shadow_path,
                    pending_shadow_rows,
                    key_fn=lambda row: row.get("shadow_key"),
                )
                telemetry.shadow_rows_written = shadow_result.written
                telemetry.shadow_rows_duplicate = shadow_result.skipped_duplicate
            except Exception:  # noqa: BLE001
                telemetry.shadow_rows_written = 0

    if shadow_sidecar is not None:
        telemetry.shadow_contracts_priced = shadow_sidecar.telemetry.shadow_contracts_priced
        telemetry.shadow_game_transforms = shadow_sidecar.telemetry.shadow_game_transforms
        telemetry.shadow_games_offered = shadow_sidecar.telemetry.games_offered
        telemetry.shadow_failures = shadow_sidecar.telemetry.shadow_failures
        telemetry.shadow_failure_types = dict(shadow_sidecar.telemetry.failure_types)
        telemetry.shadow_unavailable_reasons = dict(shadow_sidecar.telemetry.unavailable_reasons)

    telemetry.distinct_games = len(projected_game_ids)
    telemetry.duplicate_count += result.skipped_duplicate
    report.captures_written += result.written
    report.captures_skipped_already_present += result.skipped_duplicate
    return result


def _actions_step_summary_path():
    """GitHub's per-job markdown summary file, when running in Actions.
    None locally. Section-I visibility only -- never load-bearing."""
    import os

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    return Path(path) if path else None


def _append_actions_step_summary(access, access_record: dict, outcome) -> None:
    """Make quota state and recovery visually obvious in the Actions UI
    (job summary panel) without any new notification infrastructure."""
    path = _actions_step_summary_path()
    if path is None:
        return
    lines: list[str] = []
    state = access_record.get("access_state")
    if access.recovery_detected:
        lines.append("## :zap: CFBD_RECOVERED")
        lines.append("Quota restored -- football-state bootstrap ran this very run via the normal slow lane.")
    if state == cfbd_access.CFBD_QUOTA_EXHAUSTED:
        lines.append("## :no_entry: CFBD_QUOTA_EXHAUSTED")
        lines.append(f"- `next_probe_at` = `{access_record.get('cfbd_next_probe_at')}` (unmetered /info probe)")
        if access_record.get("cfbd_quota_resets_at"):
            resets = access_record.get("cfbd_quota_resets_at")
            lines.append(f"- `quota_resets_at` = `{resets}` (authoritative, from CFBD /info)")
        lines.append("- metered CFBD calls gated off; capture stays fail-closed unless a valid cached artifact serves")
    if getattr(outcome, "source", "") == "live_full_refresh":
        lines.append("## :white_check_mark: FOOTBALL_STATE_READY")
        lines.append("- full `football_state_v1` artifact rebuilt and durably saved this run")
        lines.append("- subsequent captures use `source=cache` with `cfbd_requests=0`")
    if not lines:
        return
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass  # summary is cosmetic; never fail a run over it


def _fail_closed_no_football_state(
    args, outcome, resolved_trigger, now: datetime, telemetry, access_record: dict
) -> int:
    """No provable football state and no way to refresh it: capture
    NOTHING, but leave durable evidence that the run happened and why it
    stopped -- a failed heartbeat plus the no-network reconciliation pass
    -- so an outage can never again be a silent gap. Before this path
    existed, the equivalent condition was an unhandled exception: the
    process died before any accounting and the durable store showed
    nothing at all (the exact shape of the 2026-08-29 CFBD-429 outage)."""
    season = args.schedule_season

    def account_only(repo_dir: Path) -> persistence.AppendResult:
        base_dir = repo_dir / "data" / "research"
        obs_path = persistence.canonical_path(base_dir, persistence.OBSERVATIONS_SUBDIR, season)
        state_path = persistence.canonical_path(base_dir, persistence.CAPTURE_STATE_SUBDIR, season)
        cfbd_access.save_state(repo_dir, access_record)
        telemetry.reconciled_missed_checkpoints = checkpoint_reconciliation.reconcile(
            obs_path, state_path, now=now, run_id=args.run_id
        )
        gate_note = ""
        if access_record.get("access_state") == cfbd_access.CFBD_QUOTA_EXHAUSTED:
            gate_note = (
                f" | CFBD_QUOTA_EXHAUSTED next_probe_at={access_record.get('cfbd_next_probe_at')}"
                f" quota_resets_at={access_record.get('cfbd_quota_resets_at')}"
            )
        heartbeat_mod.append_heartbeat(
            repo_dir,
            season,
            heartbeat_mod.Heartbeat(
                schema_version=heartbeat_mod.HEARTBEAT_SCHEMA_VERSION,
                run_id=args.run_id,
                trigger_type=resolved_trigger.value,
                invoked_at=now.isoformat(),
                started_at=now.isoformat(),
                finished_at=datetime.now(UTC).isoformat(),
                succeeded=False,
                schedule_fetch_success=False,
                schedule_state=SchedulePlanningState.FETCH_FAILED.value,
                cfbd_access_state=access_record.get("access_state"),
                cfbd_quota_limit=access_record.get("cfbd_quota_limit"),
                cfbd_quota_remaining=access_record.get("cfbd_quota_remaining"),
                cfbd_quota_resets_at=access_record.get("cfbd_quota_resets_at"),
                cfbd_next_probe_at=access_record.get("cfbd_next_probe_at"),
                detail=(
                    f"FAIL-CLOSED: football state {outcome.freshness} and refresh "
                    f"{'gated' if outcome.cfbd_requests == 0 else 'failed'} "
                    f"({outcome.refresh_error or 'no error detail'}); no captures attempted; "
                    f"reconciled_missed_checkpoints={telemetry.reconciled_missed_checkpoints}{gate_note}"
                ),
            ),
        )
        return persistence.AppendResult(written=0, skipped_duplicate=0)

    if args.no_push:
        account_only(args.data_repo_dir)
    else:
        git_durable_store.commit_and_push_with_retry(
            args.data_repo_dir,
            args.data_branch,
            account_only,
            commit_message=(
                f"research capture FAIL-CLOSED (football state unavailable): season={season} "
                f"run={args.run_id or 'local'} at={now.isoformat()}"
            ),
        )
    telemetry.finish()
    print(json.dumps({
        "fail_closed": True,
        "football_state_freshness": outcome.freshness,
        "football_state_source": outcome.source,
        "refresh_error": outcome.refresh_error,
        "cfbd_access_state": access_record.get("access_state"),
        "cfbd_quota_remaining": access_record.get("cfbd_quota_remaining"),
        "cfbd_quota_resets_at": access_record.get("cfbd_quota_resets_at"),
        "cfbd_next_probe_at": access_record.get("cfbd_next_probe_at"),
        "captures_due": 0,
        "captures_written": 0,
        "reconciled_missed_checkpoints": telemetry.reconciled_missed_checkpoints,
    }, indent=2))
    print("\nPERF " + json.dumps(telemetry.as_dict(), sort_keys=True))
    print("\nSTATUS: RESEARCH-ONLY. Fail-closed: no football state, no captures, accounting only.")
    return 1


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
        "--trigger-actor",
        default=None,
        help=(
            "GitHub actor that started the run. Only used to tell a conductor dispatch "
            "(github-actions) apart from a human pressing Run workflow, both of which arrive "
            "as workflow_dispatch. Provenance ONLY."
        ),
    )
    parser.add_argument(
        "--trigger-source",
        default=None,
        help=(
            "Self-declared trigger provenance from the caller (EXTERNAL_SCHEDULE | MANUAL). "
            "Used only to label an external scheduler's dispatch, which is otherwise "
            "indistinguishable from a human one. Provenance ONLY -- it never affects "
            "due-label resolution, duplicate protection, or what gets written."
        ),
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
        "--refresh-football-state",
        action="store_true",
        help=(
            "Force a full live CFBD refresh of the durable football-state artifact this run, "
            "even if the cached state is fresh. Never required for correctness -- the slow lane "
            "refreshes automatically on its freshness schedule."
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
    resolved_trigger = classify_trigger(args.trigger_type, args.trigger_actor, args.trigger_source)

    # *** TWO-LANE ARCHITECTURE (football-state decoupling) ***
    # The durable football-state artifact lives on the data branch, so
    # the checkout that used to happen just before _apply_scan now
    # happens FIRST. Module-level imports already guard against the
    # branch-swap working-tree replacement (see _build_shadow_sidecar's
    # docstring for the live incident that established that rule).
    git_durable_store.ensure_branch_checked_out(args.data_repo_dir, args.data_branch)

    # *** CFBD QUOTA GATE (research/cfbd_access.py) ***
    # Decided BEFORE any metered CFBD request. While the durable state
    # says the quota is exhausted, this run makes at most ONE unmetered
    # /info probe (and usually zero) instead of the doomed full-build
    # attempt that used to burn ~1,150 429'd requests/day. The first
    # probe showing remainingCalls > 0 un-gates this very run, so the
    # existing slow lane bootstraps the artifact immediately.
    # --refresh-football-state stays an explicit operator override.
    access = cfbd_access.assess(
        args.data_repo_dir, cfbd_client, now=now, force_allow=args.refresh_football_state
    )
    telemetry.cfbd_access_state = access.access_state
    for line in cfbd_access.summary_lines(access):
        print(f"CFBD-ACCESS {line}")

    outcome = football_state.resolve_football_state(
        args.data_repo_dir,
        cfbd_client,
        season=args.schedule_season,
        history_seasons=list(args.history_seasons),
        now=now,
        force_refresh=args.refresh_football_state,
        allow_cfbd=access.allow_cfbd,
    )
    telemetry.football_state_source = outcome.source
    telemetry.football_state_freshness = outcome.freshness
    telemetry.cfbd_requests = outcome.cfbd_requests
    if outcome.refresh_error:
        print(f"NOTE: football-state refresh failed ({outcome.refresh_error}); "
              f"source={outcome.source} freshness={outcome.freshness}")

    # The durable state this run leaves behind (written inside the apply
    # functions below so the push-retry loop persists it atomically with
    # everything else). Also drives heartbeat fields and the Actions
    # step summary.
    access_record = cfbd_access.record_outcome(access, outcome, cfbd_client, now=now)
    telemetry.cfbd_access_state = access_record.get("access_state", access.access_state)
    _append_actions_step_summary(access, access_record, outcome)

    if outcome.state is None:
        # FAIL CLOSED: no captures without provable football state. But
        # the outage itself must still leave durable evidence -- a
        # heartbeat that says the run happened and failed, and the
        # no-network reconciliation pass so windows that died in silence
        # get their explicit terminal accounting.
        return _fail_closed_no_football_state(args, outcome, resolved_trigger, now, telemetry, access_record)

    state = outcome.state
    telemetry.football_state_schedule_age_minutes = round(state.schedule_age_hours(now) * 60.0, 1)
    inputs = state.to_scan_inputs(now)
    games = inputs.games
    classification_by_game_id = inputs.classification_by_game_id
    fcs_school_names = inputs.fcs_school_names

    def _load_history_lines():
        """Deferred to first projection, exactly as the live fetch was --
        but now a LOCAL rebuild from the durable artifact: zero network,
        so a 429 during a closing window can no longer cost a
        projection."""
        with telemetry.phase("history_fetch_seconds"):
            return inputs.lines_loader()

    not_started_games = [g for g in games if g.status == "scheduled"]
    report.games_scanned = len(not_started_games)

    cache = GameProjectionCache(lines_provider=_load_history_lines)
    kalshi_client = KalshiClient()
    model_version = ModelVersion(
        model_version=milestone_d.MODEL_VERSION,
        ratings_component_version=milestone_d._ratings_component_version(),  # noqa: SLF001
        pricing_engine_version="0.1.0",
    )

    def training_cutoff_fn(request: GameProjectionRequest) -> str:
        return f"strictly before season={request.as_of_season} week={request.as_of_week}"

    # (The rehearsal-vs-real corpus note that used to sit here still
    # applies: ensure_branch_checked_out ran above, before football-state
    # resolution, so --no-push rehearsals continue to scan the REAL
    # corpus rather than an empty ledger.)

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        return _apply_scan(
            repo_dir,
            season=args.schedule_season,
            games=not_started_games,
            classification_by_game_id=classification_by_game_id,
            fcs_school_names=fcs_school_names,
            non_fbs_school_names=inputs.non_fbs_school_names,
            cache=cache,
            kalshi_client=kalshi_client,
            model_version=model_version,
            training_cutoff_fn=training_cutoff_fn,
            n_simulations=args.n_simulations,
            seed=args.seed,
            now=now,
            # The artifact's own fetch time -- guard_capture_allowed
            # enforces the 6h staleness bound against exactly this value,
            # so schedule freshness is proven per captured row.
            schedule_source_timestamp=state.schedule_fetched_at,
            run_id=args.run_id,
            report=report,
            telemetry=telemetry,
        )

    invoked_at = now

    def apply_and_beat(repo_dir: Path) -> persistence.AppendResult:
        """Scan, then record one heartbeat.

        Both inside the same apply so the retry loop treats them as one
        unit: on a push conflict the local commit is discarded by the hard
        reset and this runs again, so a run leaves exactly one heartbeat
        rather than one per attempt."""
        cfbd_access.save_state(repo_dir, access_record)
        result = apply_fn(repo_dir)
        supported_kickoffs = sorted(
            g.kickoff_utc
            for g in not_started_games
            if g.kickoff_utc is not None
            and g.kickoff_utc > now
            and classification_by_game_id.get(g.game_id, (None, None)) == ("fbs", "fbs")
        )
        next_kickoff = supported_kickoffs[0] if supported_kickoffs else None
        heartbeat_mod.append_heartbeat(
            repo_dir,
            args.schedule_season,
            heartbeat_mod.Heartbeat(
                schema_version=heartbeat_mod.HEARTBEAT_SCHEMA_VERSION,
                run_id=args.run_id,
                trigger_type=resolved_trigger.value,
                invoked_at=invoked_at.isoformat(),
                started_at=invoked_at.isoformat(),
                finished_at=datetime.now(UTC).isoformat(),
                succeeded=True,
                markets_discovered=report.markets_scanned,
                labels_due=report.captures_due,
                labels_captured=report.captures_written,
                closing_labels_due=report.closing_due,
                closing_labels_captured=report.closing_captured,
                duplicates_skipped=report.captures_skipped_already_present,
                malformed_rows=telemetry.malformed_row_count,
                api_failures=telemetry.api_failure_count,
                cfbd_healthy=report.games_scanned > 0,
                kalshi_healthy=report.markets_scanned > 0 and telemetry.api_failure_count == 0,
                cfbd_access_state=access_record.get("access_state"),
                cfbd_quota_limit=access_record.get("cfbd_quota_limit"),
                cfbd_quota_remaining=access_record.get("cfbd_quota_remaining"),
                cfbd_quota_resets_at=access_record.get("cfbd_quota_resets_at"),
                cfbd_next_probe_at=access_record.get("cfbd_next_probe_at"),
                schedule_fetch_success=outcome.source in ("cache", "live_full_refresh", "live_schedule_refresh"),
                schedule_state=(
                    SchedulePlanningState.FETCH_SUCCESS_GUARDABLE_GAME_PRESENT.value
                    if supported_kickoffs
                    else SchedulePlanningState.FETCH_SUCCESS_NO_SUPPORTED_GAMES.value
                ),
                total_schedule_games=len(games),
                supported_upcoming_games=len(supported_kickoffs),
                next_supported_kickoff=next_kickoff.isoformat() if next_kickoff else None,
                next_critical_checkpoint="CLOSING" if next_kickoff else None,
                next_critical_checkpoint_at=(
                    (next_kickoff - timedelta(minutes=CLOSING_GUARD_LEAD_MINUTES)).isoformat()
                    if next_kickoff
                    else None
                ),
                detail=(
                    f"trigger={resolved_trigger.value} raw_event={args.trigger_type!r} "
                    f"declared={args.trigger_source or 'none'} "
                    f"football_state={outcome.source} cfbd_requests={outcome.cfbd_requests}"
                ),
            ),
        )
        return result

    if args.no_push:
        apply_and_beat(args.data_repo_dir)
    else:
        push_result = git_durable_store.commit_and_push_with_retry(
            args.data_repo_dir,
            args.data_branch,
            apply_and_beat,
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
        "events_scanned": report.events_scanned,
        "events_mapping_failed": report.events_mapping_failed,
        "markets_unsupported_population": report.markets_unsupported_population,
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
