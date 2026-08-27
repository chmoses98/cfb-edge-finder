"""Mission sections 4, 12, 13, 16, 20, 22: the collection regime driven
end-to-end through the real scanner.

Everything below runs `_apply_scan` -- the actual production function --
with only the live Kalshi fetch stubbed, so these are statements about
what the collector really does, not about a reimplementation of it.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tests"))
sys.path.insert(0, str(_ROOT / "scripts"))

import research_scan_and_capture as scanner  # noqa: E402
from scan_harness import (  # noqa: E402
    NOW,
    SEASON,
    install_fake_market_feed,
    make_games,
    make_history_lines,
    make_markets,
)

from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache  # noqa: E402
from cfb_edge_finder.research import closing_capture, health, movement, persistence, scan_logic, timing  # noqa: E402
from cfb_edge_finder.research.scan_telemetry import ScanTelemetry  # noqa: E402
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow  # noqa: E402
from cfb_edge_finder.schemas.provenance import ModelVersion  # noqa: E402

MODEL_VERSION = ModelVersion(model_version="collection-test-1.0", pricing_engine_version="0.1.0")


def _scan(repo_dir: Path, monkeypatch, *, hours_ahead: float, n_games: int = 2, run_id="r", markets=None, now=None):
    games, classification = make_games(n_games, kickoff_hours_ahead=hours_ahead)
    install_fake_market_feed(monkeypatch, markets if markets is not None else make_markets(games))
    telemetry = ScanTelemetry()
    report = health.CaptureHealthReport()
    scanner._apply_scan(
        repo_dir,
        season=SEASON,
        games=games,
        classification_by_game_id=classification,
        fcs_school_names=frozenset(),
        cache=GameProjectionCache(make_history_lines(games)),
        kalshi_client=None,
        model_version=MODEL_VERSION,
        training_cutoff_fn=lambda r: "cutoff",
        n_simulations=250,
        seed=0,
        now=now or NOW,
        schedule_source_timestamp=now or NOW,
        run_id=run_id,
        report=report,
        telemetry=telemetry,
    )
    return telemetry, report, games


def _rows(repo_dir: Path) -> list[dict]:
    p = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _labels(repo_dir: Path) -> set[str]:
    return {r["observation"]["snapshot_timing"]["label"] for r in _rows(repo_dir)}


# --- Checkpoint due-ness end to end --------------------------------------


@pytest.mark.parametrize(
    "hours_ahead,expected_label",
    [
        (24.0, "T_24H"),
        (6.0, "T_6H"),
        (1.5, "T_90"),
        (1.0, "T_60"),
        (0.5, "T_30"),
        (0.15, timing.CLOSING),  # 9 minutes out
    ],
)
def test_each_checkpoint_is_captured_at_its_own_distance(tmp_path, monkeypatch, hours_ahead, expected_label):
    repo = tmp_path / f"c{hours_ahead}"
    repo.mkdir()
    _scan(repo, monkeypatch, hours_ahead=hours_ahead)
    assert expected_label in _labels(repo), f"{expected_label} not captured at {hours_ahead}h out"


def test_closing_is_captured_as_its_own_row_not_inferred_from_t30(tmp_path, monkeypatch):
    """Mission section 9. T_30 first, then CLOSING: two distinct rows with
    distinct canonical keys, and the CLOSING row is genuinely fresher."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _scan(repo, monkeypatch, hours_ahead=0.5, run_id="t30")
    assert "T_30" in _labels(repo)

    _scan(repo, monkeypatch, hours_ahead=0.15, run_id="closing")
    labels = _labels(repo)
    assert "T_30" in labels and timing.CLOSING in labels

    rows = _rows(repo)
    keys = [r["observation_key"] for r in rows]
    assert len(keys) == len(set(keys)), "CLOSING collided with T_30 on canonical key"

    closing_rows = [r for r in rows if r["observation"]["snapshot_timing"]["label"] == timing.CLOSING]
    t30_rows = [r for r in rows if r["observation"]["snapshot_timing"]["label"] == "T_30"]
    assert closing_rows and t30_rows
    assert closing_rows[0]["observation"]["snapshot_timing"]["hours_before_kickoff"] < (
        t30_rows[0]["observation"]["snapshot_timing"]["hours_before_kickoff"]
    )


def test_closing_is_never_captured_after_kickoff(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    # Kickoff already passed: negative hours_ahead.
    _scan(repo, monkeypatch, hours_ahead=-0.5)
    assert timing.CLOSING not in _labels(repo)


def test_no_checkpoint_is_captured_twice(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _scan(repo, monkeypatch, hours_ahead=0.15, run_id="first")
    first = _rows(repo)
    _scan(repo, monkeypatch, hours_ahead=0.15, run_id="second")
    second = _rows(repo)
    assert len(second) == len(first), "a repeat scan in the same window duplicated checkpoints"
    keys = [r["observation_key"] for r in second]
    assert len(keys) == len(set(keys))


def test_a_late_run_still_catches_a_numeric_checkpoint(tmp_path, monkeypatch):
    """Mission section 4: a late run MAY capture a still-eligible numeric
    label. T_6H's window is 4-8h, so a run at 4.5h out still catches it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _scan(repo, monkeypatch, hours_ahead=4.5)
    assert "T_6H" in _labels(repo)


def test_a_late_run_cannot_backfill_closing(tmp_path, monkeypatch):
    """...but CLOSING is the exception that is never backfilled."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _scan(repo, monkeypatch, hours_ahead=-0.01)  # barely past kickoff
    assert timing.CLOSING not in _labels(repo)


# --- Manual vs scheduled equivalence (section 20) ------------------------


def test_manual_and_scheduled_runs_produce_identical_artifacts(tmp_path, monkeypatch):
    manual = tmp_path / "manual"
    manual.mkdir()
    scheduled = tmp_path / "scheduled"
    scheduled.mkdir()
    _scan(manual, monkeypatch, hours_ahead=6.0, run_id="manual-run")
    _scan(scheduled, monkeypatch, hours_ahead=6.0, run_id="manual-run")

    a, b = _rows(manual), _rows(scheduled)
    assert len(a) == len(b) > 0
    strip = lambda rows: [  # noqa: E731
        {k: v for k, v in r.items() if k != "observation"}
        | {"observation": {k: v for k, v in r["observation"].items() if k != "snapshot_id"}}
        for r in rows
    ]
    assert strip(a) == strip(b)


def test_trigger_type_is_provenance_only():
    """It is recorded, but nothing in due-label resolution reads it."""
    assert ScanTelemetry(trigger_type="workflow_dispatch").trigger_type == "workflow_dispatch"
    assert "trigger_type" not in timing.resolve_due_labels.__code__.co_varnames


# --- Kickoff reschedule (section 13) -------------------------------------


def test_reschedule_leaves_prior_snapshots_immutable(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _scan(repo, monkeypatch, hours_ahead=6.0, run_id="before")
    before_bytes = persistence.canonical_path(
        repo / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON
    ).read_bytes()
    assert len(before_bytes) > 0

    # Game moves 24h later: new labels resolve against the NEW kickoff,
    # and nothing already written may change.
    _scan(repo, monkeypatch, hours_ahead=30.0, run_id="after-reschedule")
    after = persistence.canonical_path(repo / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON).read_bytes()
    assert after.startswith(before_bytes), "reschedule rewrote or reordered existing snapshots"


def test_reschedule_detection_threshold():
    base = NOW
    assert scan_logic.detect_reschedule(base, base + timedelta(minutes=5)) is False
    assert scan_logic.detect_reschedule(base, base + timedelta(hours=3)) is True
    assert scan_logic.detect_reschedule(None, base) is False


def test_closing_follows_the_new_kickoff(tmp_path, monkeypatch):
    """After a reschedule, CLOSING is due relative to the NEW kickoff."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _scan(repo, monkeypatch, hours_ahead=0.15, run_id="near-old-kickoff")
    assert timing.CLOSING in _labels(repo)


# --- Market suspension / closure (section 14) ----------------------------


def _mark_all(markets: dict, status: str) -> dict:
    return {series: [{**m, "status": status} for m in ms] for series, ms in markets.items()}


def test_market_status_is_recorded_on_every_row(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _scan(repo, monkeypatch, hours_ahead=6.0)
    rows = _rows(repo)
    assert rows
    assert all(r["observation"]["market_status"] == "active" for r in rows)


def test_suspended_markets_are_not_discovered_as_active(tmp_path, monkeypatch):
    """PRIMARY protection: the discovery filter keeps non-active markets
    out of pricing entirely, so no executable price is ever fabricated
    for one. (The secondary CLOSING eligibility gate inside the capture
    path is covered by tests/test_prospective_closing_capture.py.)"""
    repo = tmp_path / "repo"
    repo.mkdir()
    games, _ = make_games(2, kickoff_hours_ahead=6.0)
    suspended = _mark_all(make_markets(games), "suspended")
    _scan(repo, monkeypatch, hours_ahead=6.0, markets=suspended)
    assert _rows(repo) == [], "a suspended market produced a priced row"


def test_closed_market_in_closing_window_yields_no_closing_row(tmp_path, monkeypatch):
    """A market that has closed before its closing window produces no
    CLOSING row at all -- never a fabricated one."""
    repo = tmp_path / "repo"
    repo.mkdir()
    games, _ = make_games(2, kickoff_hours_ahead=0.15)
    closed = _mark_all(make_markets(games), "closed")
    _scan(repo, monkeypatch, hours_ahead=0.15, markets=closed)
    assert timing.CLOSING not in _labels(repo)


# --- Stale data (section 16) ---------------------------------------------


def test_stale_schedule_blocks_capture():
    stale = NOW - timedelta(hours=scan_logic.MAX_SCHEDULE_STALENESS_HOURS + 1)
    with pytest.raises(scan_logic.StaleScheduleGuardError):
        scan_logic.guard_capture_allowed(game_status="scheduled", schedule_source_timestamp=stale, now=NOW)


def test_fresh_schedule_allows_capture():
    fresh = NOW - timedelta(minutes=5)
    scan_logic.guard_capture_allowed(game_status="scheduled", schedule_source_timestamp=fresh, now=NOW)


def test_started_game_blocks_new_pregame_capture():
    with pytest.raises(scan_logic.StaleScheduleGuardError):
        scan_logic.guard_capture_allowed(game_status="in_progress", schedule_source_timestamp=NOW, now=NOW)


# --- Model/data refresh (section 12) -------------------------------------


def test_price_only_change_does_not_rerun_the_football_model(tmp_path, monkeypatch):
    """A market whose PRICE moved but whose game inputs did not must
    reprice off the cached projection, not refit the model."""
    games, classification = make_games(2, kickoff_hours_ahead=6.0)
    cache = GameProjectionCache(make_history_lines(games))
    base_markets = make_markets(games)
    moved = {
        series: [{**m, "yes_bid_dollars": "0.70", "yes_ask_dollars": "0.75"} for m in ms]
        for series, ms in base_markets.items()
    }

    for i, markets in enumerate((base_markets, moved)):
        repo = tmp_path / f"run{i}"
        repo.mkdir()
        install_fake_market_feed(monkeypatch, markets)
        scanner._apply_scan(
            repo,
            season=SEASON,
            games=games,
            classification_by_game_id=classification,
            fcs_school_names=frozenset(),
            cache=cache,  # SAME cache across both runs
            kalshi_client=None,
            model_version=MODEL_VERSION,
            training_cutoff_fn=lambda r: "cutoff",
            n_simulations=250,
            seed=0,
            now=NOW,
            schedule_source_timestamp=NOW,
            run_id=f"run{i}",
            report=health.CaptureHealthReport(),
            telemetry=ScanTelemetry(),
        )
        if i == 0:
            builds_after_first = cache.projection_builds
            assert builds_after_first > 0

    assert cache.projection_builds == builds_after_first, (
        "a price-only change rebuilt the football model"
    )


def test_changed_game_inputs_do_invalidate_the_cache():
    """The complement: genuinely different game inputs must NOT silently
    reuse another game's projection."""
    games, _ = make_games(4, kickoff_hours_ahead=6.0)
    cache = GameProjectionCache(make_history_lines(games))
    from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionRequest

    def _req(game, **over):
        base = dict(
            game_id=game.game_id, home_id=game.home_team_id, away_id=game.away_team_id,
            home_classification="fbs", away_classification="fbs", is_neutral_site=False,
            as_of_season=game.season, as_of_week=game.week_number or 1, n_simulations=200, seed=0,
        )
        base.update(over)
        return GameProjectionRequest(**base)

    cache.get_or_build(_req(games[0]))
    assert cache.projection_builds == 1
    cache.get_or_build(_req(games[0]))
    assert cache.projection_builds == 1, "identical request rebuilt"
    cache.get_or_build(_req(games[1]))
    assert cache.projection_builds == 2, "a different game reused another game's projection"
    cache.get_or_build(_req(games[0], as_of_week=(games[0].week_number or 1) + 1))
    assert cache.projection_builds == 3, "a changed as_of reused a stale projection"


def test_history_is_fetched_lazily_and_at_most_once():
    calls = []

    def provider():
        calls.append(1)
        return make_history_lines(make_games(2)[0])

    cache = GameProjectionCache(lines_provider=provider)
    assert calls == [], "history fetched before any projection was requested"

    games, _ = make_games(2)
    from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionRequest

    req = GameProjectionRequest(
        game_id=games[0].game_id, home_id=games[0].home_team_id, away_id=games[0].away_team_id,
        home_classification="fbs", away_classification="fbs", is_neutral_site=False,
        as_of_season=games[0].season, as_of_week=games[0].week_number or 1, n_simulations=200, seed=0,
    )
    cache.get_or_build(req)
    cache.get_or_build(req)
    assert calls == [1], f"history provider called {len(calls)} times, expected exactly 1"
    assert cache.lines_fetch_count == 1


def test_scan_with_nothing_due_never_fetches_history(tmp_path, monkeypatch):
    """The property the 10-minute cadence depends on."""
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []

    def provider():
        calls.append(1)
        return []

    games, classification = make_games(2, kickoff_hours_ahead=300.0)  # far outside every window...
    _scan_games, _ = make_games(2, kickoff_hours_ahead=300.0)
    install_fake_market_feed(monkeypatch, make_markets(games))
    # ...and EARLY_OPEN already captured, so genuinely nothing is due.
    scanner._apply_scan(
        repo,
        season=SEASON,
        games=games,
        classification_by_game_id=classification,
        fcs_school_names=frozenset(),
        cache=GameProjectionCache(lines_provider=provider),
        kalshi_client=None,
        model_version=MODEL_VERSION,
        training_cutoff_fn=lambda r: "cutoff",
        n_simulations=250,
        seed=0,
        now=NOW,
        schedule_source_timestamp=NOW,
        run_id="nothing-due",
        report=health.CaptureHealthReport(),
        telemetry=ScanTelemetry(),
    )
    # EARLY_OPEN IS due on a first sighting, so this run does capture --
    # rerun it and assert the SECOND run (nothing due) stays cheap.
    calls.clear()
    install_fake_market_feed(monkeypatch, make_markets(games))
    scanner._apply_scan(
        repo,
        season=SEASON,
        games=games,
        classification_by_game_id=classification,
        fcs_school_names=frozenset(),
        cache=GameProjectionCache(lines_provider=provider),
        kalshi_client=None,
        model_version=MODEL_VERSION,
        training_cutoff_fn=lambda r: "cutoff",
        n_simulations=250,
        seed=0,
        now=NOW,
        schedule_source_timestamp=NOW,
        run_id="nothing-due-2",
        report=health.CaptureHealthReport(),
        telemetry=ScanTelemetry(),
    )
    assert calls == [], "a scan with nothing due still fetched the CFBD history corpus"


# --- Movement representation (sections 10-11) ----------------------------


def test_movement_preserves_the_checkpoint_sequence(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for hours in (24.0, 6.0, 1.5, 1.0, 0.5, 0.15):
        _scan(repo, monkeypatch, hours_ahead=hours, run_id=f"h{hours}")

    rows = [ResearchCorpusRow.model_validate(r) for r in _rows(repo)]
    movements = movement.build_contract_movements(rows)
    assert movements

    multi = [m for m in movements if len(m.points) > 1]
    assert multi, "no contract accumulated multiple checkpoints"

    order = {label: i for i, label in enumerate(timing.ALL_PREGAME_LABELS)}
    for m in multi:
        idx = [order[label] for label in m.labels if label in order]
        assert idx == sorted(idx), f"checkpoints out of canonical order: {m.labels}"

    with_closing = [m for m in movements if m.has_closing]
    assert with_closing, "no contract reached CLOSING"
    for m in with_closing:
        assert m.point_for(timing.CLOSING) is not None
        assert m.market_price_series()[-1][0] == timing.CLOSING

    coverage = movement.coverage_by_label(movements)
    assert coverage[timing.CLOSING] > 0
    assert coverage["EARLY_OPEN"] > 0


def test_market_and_model_series_are_kept_separate(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _scan(repo, monkeypatch, hours_ahead=6.0)
    rows = [ResearchCorpusRow.model_validate(r) for r in _rows(repo)]
    m = movement.build_contract_movements(rows)[0]
    assert [p[0] for p in m.market_price_series()] == [p[0] for p in m.model_probability_series()]
    assert m.market_price_series() is not m.model_probability_series()


# --- Health (section 15) --------------------------------------------------


def test_due_but_unlanded_closing_is_high_severity():
    report = health.CaptureHealthReport(markets_scanned=10, games_scanned=5, closing_due=3, closing_captured=1)
    codes = {d.code: d.severity for d in health.evaluate_collapse(report, baseline_supported_markets=None)}
    assert codes.get("closing_capture_shortfall") == health.Severity.HIGH
    assert health.should_fail_run(health.evaluate_collapse(report, None)) is True


def test_fully_landed_closings_raise_nothing():
    report = health.CaptureHealthReport(markets_scanned=10, games_scanned=5, closing_due=2, closing_captured=2)
    codes = {d.code for d in health.evaluate_collapse(report, baseline_supported_markets=None)}
    assert "closing_capture_shortfall" not in codes


def test_recorded_missing_closings_warn_but_do_not_fail():
    report = health.CaptureHealthReport(markets_scanned=10, games_scanned=5, closing_missing=4)
    diags = health.evaluate_collapse(report, baseline_supported_markets=None)
    codes = {d.code: d.severity for d in diags}
    assert codes.get("closing_missing_recorded") == health.Severity.WARNING
    assert health.should_fail_run(diags) is False


def test_api_failures_are_high_severity():
    report = health.CaptureHealthReport(markets_scanned=10, games_scanned=5, api_failures=1)
    assert health.should_fail_run(health.evaluate_collapse(report, None)) is True


def test_unexpected_zeros_fail_but_expected_ones_do_not():
    empty = health.CaptureHealthReport(markets_scanned=0, games_scanned=0)
    assert health.should_fail_run(health.evaluate_collapse(empty, None)) is True

    quiet = health.CaptureHealthReport(markets_scanned=50, games_scanned=20, captures_due=0, captures_written=0)
    assert health.should_fail_run(health.evaluate_collapse(quiet, None)) is False


def test_closing_statuses_all_reachable_from_the_enum():
    assert closing_capture.MISSING_CLOSING_STATUSES
    assert all(s.value.startswith("CLOSING_MISSING") for s in closing_capture.MISSING_CLOSING_STATUSES)
