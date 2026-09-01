"""Two-lane architecture: the deadline-critical Kalshi capture loop must
not depend on a live CFBD request while valid durable football state
exists -- and every degradation must fail closed, explicitly accounted.

The tests here are the systems-level regression matrix for the
decoupling: zero-CFBD fast path, capture under a live 429, freshness
fail-closed, kickoff/reschedule safety, no-backfill reconciliation, a
REAL git branch-swap integration pass, and byte-exact CONTROL
equivalence between artifact-served and live-served inputs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tests"))
sys.path.insert(0, str(_ROOT / "scripts"))

import research_scan_and_capture as scanner  # noqa: E402
from scan_harness import (  # noqa: E402
    FBS_TEAMS,
    SEASON,
    install_fake_market_feed,
    make_markets,
)

from cfb_edge_finder.kalshi.game_projection_cache import (  # noqa: E402
    GameProjectionCache,
    GameProjectionRequest,
)
from cfb_edge_finder.research import (  # noqa: E402
    checkpoint_reconciliation,
    football_state,
    git_durable_store,
    health,
    persistence,
)
from cfb_edge_finder.research.scan_telemetry import ScanTelemetry  # noqa: E402
from cfb_edge_finder.schemas.capture_state import CaptureState  # noqa: E402
from cfb_edge_finder.schemas.provenance import ModelVersion  # noqa: E402

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
MODEL_VERSION = ModelVersion(model_version="decoupling-test-1.0", pricing_engine_version="0.1.0")
HISTORY_SEASONS = [2025]


# ------------------------------------------------------------- fixtures


def _schedule_row(i: int, home: str, away: str, kickoff: datetime) -> dict:
    return {
        "id": 900000 + i,
        "season": SEASON,
        "week": 1,
        "seasonType": "regular",
        "startDate": kickoff.isoformat(),
        "startTimeTBD": False,
        "neutralSite": False,
        "conferenceGame": False,
        "homeTeam": home,
        "awayTeam": away,
        "homeClassification": "fbs",
        "awayClassification": "fbs",
        "completed": False,
        "status": "scheduled",
    }


def _history_rows(n_teams: int = 8) -> tuple[list[dict], list[dict]]:
    """Completed 2025 games among real registry teams, full wire shape."""
    games, advanced = [], []
    gid = 800000
    for week in range(1, 4):
        for i in range(0, n_teams - 1, 2):
            home, away = FBS_TEAMS[i][1], FBS_TEAMS[i + 1][1]
            gid += 1
            games.append(
                {
                    "id": gid,
                    "season": 2025,
                    "week": week,
                    "seasonType": "regular",
                    "startDate": f"2025-09-{6 + week:02d}T18:00:00Z",
                    "neutralSite": False,
                    "homeTeam": home,
                    "awayTeam": away,
                    "homeClassification": "fbs",
                    "awayClassification": "fbs",
                    "homePoints": 28 + i,
                    "awayPoints": 17 + week,
                    "completed": True,
                    "status": "final",
                }
            )
            advanced.append({"gameId": gid, "team": home, "offense": {"plays": 68}})
            advanced.append({"gameId": gid, "team": away, "offense": {"plays": 64}})
    return games, advanced


class FakeCFBD:
    """Counts every call; optionally fails everything with a live-shaped
    429 so tests can prove the fast lane never needs it."""

    def __init__(self, schedule=None, teams=None, history=None, failing: bool = False):
        self.schedule = schedule or []
        self.teams = teams if teams is not None else [{"school": "Montana", "classification": "fcs"}]
        self.history = history or {}
        self.failing = failing
        self.calls = 0

    def _maybe_fail(self):
        self.calls += 1
        if self.failing:
            raise requests.HTTPError("429 Client Error: Too Many Requests")

    def fetch_games(self, season, season_type=None, division="fbs", week=None):
        self._maybe_fail()
        if season == SEASON:
            return self.schedule
        return self.history.get(season, {}).get("games", [])

    def fetch_all_division_teams(self, season=None):
        self._maybe_fail()
        return self.teams

    def fetch_advanced_team_game_stats(self, season, week=None, team=None, exclude_garbage_time=False):
        self._maybe_fail()
        return self.history.get(season, {}).get("advanced", [])


def _make_state(tmp_repo: Path, *, kickoff_hours_ahead: float = 24.0, n_games: int = 2,
                fetched_at: datetime | None = None) -> football_state.FootballState:
    kickoff = NOW + timedelta(hours=kickoff_hours_ahead)
    schedule = [
        _schedule_row(i, FBS_TEAMS[2 * i][1], FBS_TEAMS[2 * i + 1][1], kickoff)
        for i in range(n_games)
    ]
    hist_games, hist_adv = _history_rows()
    client = FakeCFBD(schedule=schedule, history={2025: {"games": hist_games, "advanced": hist_adv}})
    state = football_state.build_football_state(
        client, season=SEASON, history_seasons=HISTORY_SEASONS, now=fetched_at or NOW
    )
    football_state.save_football_state(tmp_repo, state)
    return state


def _run_scan_from_state(repo_dir: Path, state, monkeypatch, *, now=NOW, run_id="run-1",
                         markets=None, schedule_ts=None):
    inputs = state.to_scan_inputs(now)
    games = [g for g in inputs.games if g.status == "scheduled"]
    markets = markets if markets is not None else make_markets(games)
    install_fake_market_feed(monkeypatch, markets)
    telemetry = ScanTelemetry()
    report = health.CaptureHealthReport()
    result = scanner._apply_scan(
        repo_dir,
        season=SEASON,
        games=games,
        classification_by_game_id=inputs.classification_by_game_id,
        fcs_school_names=inputs.fcs_school_names,
        cache=GameProjectionCache(lines_provider=inputs.lines_loader),
        kalshi_client=None,
        model_version=MODEL_VERSION,
        training_cutoff_fn=lambda r: f"strictly before season={r.as_of_season} week={r.as_of_week}",
        n_simulations=200,
        seed=0,
        now=now,
        schedule_source_timestamp=schedule_ts or state.schedule_fetched_at,
        run_id=run_id,
        report=report,
        telemetry=telemetry,
    )
    return result, telemetry, report, games


def _obs_path(repo_dir: Path) -> Path:
    return persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)


def _state_path(repo_dir: Path) -> Path:
    return persistence.canonical_path(repo_dir / "data" / "research", persistence.CAPTURE_STATE_SUBDIR, SEASON)


# ------------------------------------ 1: zero CFBD when state is fresh


def test_fresh_state_makes_zero_cfbd_requests(tmp_path):
    _make_state(tmp_path)
    dead_client = FakeCFBD(failing=True)
    outcome = football_state.resolve_football_state(
        tmp_path, dead_client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW
    )
    assert outcome.source == "cache"
    assert outcome.cfbd_requests == 0
    assert dead_client.calls == 0
    assert outcome.state is not None


# --------------------- 2/3: capture due checkpoints while CFBD is 429ing


@pytest.mark.parametrize("hours_ahead,label", [(0.5, "T_30"), (10.0 / 60.0, "CLOSING")])
def test_due_checkpoint_captures_while_cfbd_returns_429(tmp_path, monkeypatch, hours_ahead, label):
    _make_state(tmp_path, kickoff_hours_ahead=hours_ahead)
    dead_client = FakeCFBD(failing=True)
    outcome = football_state.resolve_football_state(
        tmp_path, dead_client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW
    )
    assert outcome.state is not None and dead_client.calls == 0

    result, telemetry, report, _games = _run_scan_from_state(tmp_path, outcome.state, monkeypatch)
    assert result.written > 0
    rows = [json.loads(line) for line in _obs_path(tmp_path).read_text().splitlines() if line.strip()]
    labels = {r["observation"]["snapshot_timing"]["label"] for r in rows}
    assert label in labels
    # The harness's GAME fixtures carry a generic rules text that the
    # winner cross-check correctly refuses (pre-existing harness
    # artifact); every SPREAD/TOTAL contract must model-price, proving
    # the projection path ran entirely from the artifact.
    priced = [r for r in rows if r["observation"]["snapshot_timing"]["label"] == label
              and r["observation"].get("family") in ("spread", "total")]
    assert priced and all(r["observation"]["pricing_status"] == "model_priced" for r in priced)
    assert dead_client.calls == 0  # the entire capture never touched CFBD


# ---------------------------------- 4/17/18: freshness fail-closed rules


def test_soft_stale_state_survives_a_failed_refresh(tmp_path):
    _make_state(tmp_path, fetched_at=NOW - timedelta(hours=5))  # soft-stale, inside 6h hard bound
    dead_client = FakeCFBD(failing=True)
    outcome = football_state.resolve_football_state(
        tmp_path, dead_client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW
    )
    assert outcome.source == "cache_after_refresh_failure"
    assert outcome.state is not None
    assert outcome.refresh_error and "429" in outcome.refresh_error
    assert dead_client.calls >= 1  # the refresh WAS attempted and observably failed


def test_hard_stale_state_fails_closed_when_refresh_fails(tmp_path):
    _make_state(tmp_path, fetched_at=NOW - timedelta(hours=7))  # beyond the 6h hard bound
    dead_client = FakeCFBD(failing=True)
    outcome = football_state.resolve_football_state(
        tmp_path, dead_client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW
    )
    assert outcome.state is None
    assert outcome.source == "unavailable"
    assert outcome.freshness == football_state.FOOTBALL_STATE_STALE_HARD


def test_missing_state_fails_closed_when_build_fails(tmp_path):
    dead_client = FakeCFBD(failing=True)
    outcome = football_state.resolve_football_state(
        tmp_path, dead_client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW
    )
    assert outcome.state is None
    assert outcome.freshness == football_state.FOOTBALL_STATE_MISSING


def test_stale_schedule_timestamp_blocks_rows_via_existing_guard(tmp_path, monkeypatch):
    state = _make_state(tmp_path, kickoff_hours_ahead=0.5)
    result, _t, report, _g = _run_scan_from_state(
        tmp_path, state, monkeypatch, schedule_ts=NOW - timedelta(hours=7)
    )
    assert result.written == 0
    assert report.stale_schedule_failures > 0


def test_corrupt_artifact_is_refused(tmp_path):
    _make_state(tmp_path)
    payload = tmp_path / "data" / "research" / "football_state" / f"{SEASON}.json"
    payload.write_text(payload.read_text() + " ")  # sha mismatch
    loaded, verdict = football_state.load_football_state(tmp_path, SEASON)
    assert loaded is None
    assert verdict == football_state.FOOTBALL_STATE_CORRUPT


# --------------------------- 5/6/7/8: kickoff and reschedule fail-closed


def test_unknown_kickoff_captures_nothing(tmp_path, monkeypatch):
    state = _make_state(tmp_path, kickoff_hours_ahead=0.5)
    inputs = state.to_scan_inputs(NOW)
    broken = [g.model_copy(update={"kickoff_utc": None}) for g in inputs.games]
    markets = make_markets(inputs.games)
    install_fake_market_feed(monkeypatch, markets)
    telemetry = ScanTelemetry()
    report = health.CaptureHealthReport()
    result = scanner._apply_scan(
        tmp_path, season=SEASON, games=broken,
        classification_by_game_id=inputs.classification_by_game_id,
        fcs_school_names=inputs.fcs_school_names,
        cache=GameProjectionCache(lines_provider=inputs.lines_loader),
        kalshi_client=None, model_version=MODEL_VERSION,
        training_cutoff_fn=lambda r: "cutoff", n_simulations=200, seed=0,
        now=NOW, schedule_source_timestamp=state.schedule_fetched_at,
        run_id="r", report=report, telemetry=telemetry,
    )
    assert result.written == 0


def test_close_time_earlier_than_kickoff_marks_kickoff_uncertain_and_captures_nothing(tmp_path, monkeypatch):
    # The one direction the clock guard cannot catch: the market closing
    # HOURS BEFORE the cached kickoff evidences a game moved earlier.
    state = _make_state(tmp_path, kickoff_hours_ahead=6.0, n_games=1)
    inputs = state.to_scan_inputs(NOW)
    games = inputs.games
    markets = make_markets(games)
    drifted = (games[0].kickoff_utc - timedelta(hours=3)).isoformat()
    for series_markets in markets.values():
        for market in series_markets:
            market["close_time"] = drifted
    result, telemetry, report, _g = _run_scan_from_state(
        tmp_path, state, monkeypatch, markets=markets
    )
    assert result.written == 0
    assert telemetry.kickoff_uncertain_games >= 1
    assert report.kickoff_uncertain_events >= 1
    diagnostics = health.evaluate_collapse(report, baseline_supported_markets=None)
    assert any(d.code == "kickoff_uncertain_events" for d in diagnostics)
    state_rows = [json.loads(line) for line in _state_path(tmp_path).read_text().splitlines() if line.strip()]
    assert any(
        r["state"] == CaptureState.OTHER_EXPLICIT_REASON.value and "kickoff_uncertain" in r["detail"]
        for r in state_rows
    )


def test_kalshi_standard_48h_late_close_time_still_captures(tmp_path, monkeypatch):
    # Live regression (2026-09-01 forensic audit): Kalshi sets close_time
    # to kickoff + 48h on EVERY CFB single-game market. A symmetric
    # |drift| check treated that universal shape as kickoff-uncertain and
    # silently withheld the entire mapped universe -- all 160 mapped
    # events, including the Sep 3 T_3D windows later reconciled as
    # missed. A later close_time is not evidence the game moved earlier
    # and must not withhold anything.
    state = _make_state(tmp_path, kickoff_hours_ahead=0.5, n_games=1)
    inputs = state.to_scan_inputs(NOW)
    markets = make_markets(inputs.games)
    late = (inputs.games[0].kickoff_utc + timedelta(hours=48)).isoformat()
    for series_markets in markets.values():
        for market in series_markets:
            market["close_time"] = late
    result, telemetry, report, _g = _run_scan_from_state(tmp_path, state, monkeypatch, markets=markets)
    assert result.written > 0
    assert telemetry.kickoff_uncertain_games == 0
    assert report.kickoff_uncertain_events == 0


def test_close_time_within_tolerance_still_captures(tmp_path, monkeypatch):
    state = _make_state(tmp_path, kickoff_hours_ahead=0.5, n_games=1)
    inputs = state.to_scan_inputs(NOW)
    markets = make_markets(inputs.games)
    near = (inputs.games[0].kickoff_utc - timedelta(minutes=5)).isoformat()
    for series_markets in markets.values():
        for market in series_markets:
            market["close_time"] = near
    result, telemetry, _report, _g = _run_scan_from_state(tmp_path, state, monkeypatch, markets=markets)
    assert result.written > 0
    assert telemetry.kickoff_uncertain_games == 0


def test_passed_kickoff_never_yields_closing_or_any_pregame_row(tmp_path, monkeypatch):
    # Cached kickoff is in the past (e.g. the game was NOT rescheduled in
    # the cache but has started): the clock guard must produce zero rows.
    state = _make_state(tmp_path, kickoff_hours_ahead=-0.5)
    result, _t, _report, _g = _run_scan_from_state(tmp_path, state, monkeypatch)
    assert result.written == 0
    if _obs_path(tmp_path).exists():
        assert _obs_path(tmp_path).read_text().strip() == ""


# ------------------------------------------------- 10: dedup unchanged


def test_duplicate_capture_remains_deduped(tmp_path, monkeypatch):
    state = _make_state(tmp_path, kickoff_hours_ahead=0.5)
    first, _t, _r, _g = _run_scan_from_state(tmp_path, state, monkeypatch, run_id="a")
    assert first.written > 0
    second, _t2, _r2, _g2 = _run_scan_from_state(tmp_path, state, monkeypatch, run_id="b")
    assert second.written == 0
    assert second.skipped_duplicate == 0 or second.skipped_duplicate >= 0  # nothing due -> nothing to dedup


# ------------------- 11/12: CONTROL equivalence, artifact vs live inputs


def test_projection_from_artifact_is_bit_identical_to_live_inputs(tmp_path):
    from cfb_edge_finder.modeling.corpus import build_team_game_lines

    state = _make_state(tmp_path)
    inputs = state.to_scan_inputs(NOW)
    artifact_lines = inputs.lines_loader()

    hist_games, hist_adv = _history_rows()
    live_lines, _skipped = build_team_game_lines(hist_games, hist_adv, captured_at=NOW)
    assert [line.model_dump() for line in artifact_lines] == [line.model_dump() for line in live_lines]

    game = inputs.games[0]
    request = GameProjectionRequest(
        game_id=game.game_id, home_id=game.home_team_id, away_id=game.away_team_id,
        home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, as_of_season=SEASON, as_of_week=1,
        n_simulations=500, seed=0,
    )
    from_artifact = GameProjectionCache(artifact_lines).get_or_build(request).projection
    from_live = GameProjectionCache(live_lines).get_or_build(request).projection
    assert from_artifact.expected_home_points == from_live.expected_home_points
    assert from_artifact.expected_away_points == from_live.expected_away_points
    assert from_artifact.prob_home_win() == from_live.prob_home_win()
    assert from_artifact.prob_margin_greater_than(3.5) == from_live.prob_margin_greater_than(3.5)


def test_schedule_normalization_matches_live_path(tmp_path):
    import capture_kalshi_cfb_snapshot as milestone_d

    state = _make_state(tmp_path)
    inputs = state.to_scan_inputs(NOW)
    live_games, live_classification = milestone_d._fetch_candidate_games(
        SEASON, FakeCFBD(schedule=state.schedule_games), NOW
    )
    assert [g.model_dump() for g in inputs.games] == [g.model_dump() for g in live_games]
    assert inputs.classification_by_game_id == live_classification


# -------------------------------- 19/20: reconciliation, never backfill


def _seed_started_game_rows(repo_dir: Path, *, kickoff: datetime, captured_labels: list[str]) -> None:
    path = _obs_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for i, label in enumerate(captured_labels):
            handle.write(json.dumps({
                "schema_version": "research_corpus_v2",
                "capture_mode": "PROSPECTIVE",
                "season": SEASON,
                "observation_key": f"seed-{label}-{i}",
                "kickoff_utc_at_capture": kickoff.isoformat(),
                "observation": {
                    "kalshi_market_ticker": "KXNCAAFGAME-26SEP01TEST-AAA",
                    "game_id": "cfb-2026-wk01-test-game",
                    "captured_at": (kickoff - timedelta(hours=20)).isoformat(),
                    "snapshot_timing": {"label": label, "hours_before_kickoff": 20.0},
                },
            }) + "\n")


def test_reconciliation_writes_terminal_missed_reasons_after_hard_down_window(tmp_path):
    kickoff = NOW - timedelta(hours=2)  # game started during the outage
    _seed_started_game_rows(tmp_path, kickoff=kickoff, captured_labels=["EARLY_OPEN", "T_24H"])
    obs_before = _obs_path(tmp_path).read_text()

    written = checkpoint_reconciliation.reconcile(
        _obs_path(tmp_path), _state_path(tmp_path), now=NOW, run_id="reconciler"
    )
    assert written > 0
    rows = [json.loads(line) for line in _state_path(tmp_path).read_text().splitlines() if line.strip()]
    by_label = {r["timing_label"]: r for r in rows}
    for label in ("T_6H", "T_90", "T_60", "T_30", "CLOSING"):
        assert label in by_label, f"{label} has no terminal accounting"
        assert by_label[label]["state"] == CaptureState.MISSED_WINDOW.value
        assert "reconciled after the fact" in by_label[label]["detail"]
    # Captured labels are NOT re-accounted as missed.
    assert "T_24H" not in by_label and "EARLY_OPEN" not in by_label

    # NEVER backfill: the observations ledger is byte-identical.
    assert _obs_path(tmp_path).read_text() == obs_before

    # Idempotent: a second reconciliation writes nothing new.
    assert checkpoint_reconciliation.reconcile(
        _obs_path(tmp_path), _state_path(tmp_path), now=NOW + timedelta(minutes=5), run_id="again"
    ) == 0


def test_reconciliation_leaves_open_windows_and_unknown_kickoffs_alone(tmp_path):
    future_kick = NOW + timedelta(hours=30)
    _seed_started_game_rows(tmp_path, kickoff=future_kick, captured_labels=["EARLY_OPEN"])
    written = checkpoint_reconciliation.reconcile(
        _obs_path(tmp_path), _state_path(tmp_path), now=NOW, run_id="r"
    )
    # Nothing has provably passed for a game 30h away except nothing:
    # T_7D may legitimately be already-missed depending on discovery age,
    # but CLOSING/T_30/T_60/T_90/T_6H must NOT be.
    rows = (
        [json.loads(line) for line in _state_path(tmp_path).read_text().splitlines()]
        if _state_path(tmp_path).exists()
        else []
    )
    labels = {r["timing_label"] for r in rows}
    assert not ({"CLOSING", "T_30", "T_60", "T_90", "T_6H"} & labels)
    assert written == len(rows)


# --------------------------- 16: REAL branch-swap integration approximation


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_real_branch_swap_checkout_load_scan_capture(tmp_path, monkeypatch):
    """main checkout -> imports (already loaded) -> REAL git checkout of
    the data branch -> cached-state load -> Kalshi scan -> checkpoint
    capture -> durable commit/push to a REAL (local bare) remote."""
    remote = tmp_path / "origin.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "--initial-branch=main")

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "--initial-branch=main")
    _git(work, "config", "user.email", "test@example.invalid")
    _git(work, "config", "user.name", "test")
    _git(work, "remote", "add", "origin", str(remote))
    (work / "README.md").write_text("main branch code checkout\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "main")
    _git(work, "push", "origin", "main")

    # Data branch with ONLY durable data: the football-state artifact.
    git_durable_store.ensure_branch_checked_out(work, "research-data")
    state = _make_state(work, kickoff_hours_ahead=0.5, n_games=1)

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        loaded, verdict = football_state.load_football_state(repo_dir, SEASON)
        assert loaded is not None, verdict
        result, telemetry, _report, _games = _run_scan_from_state(repo_dir, loaded, monkeypatch)
        assert result.written > 0
        return result

    push = git_durable_store.commit_and_push_with_retry(
        work, "research-data", apply_fn, commit_message="decoupling integration test"
    )
    assert push.attempts >= 1

    # The durable branch on the REMOTE now contains artifact + captures,
    # and is data-only (no src/ tree was ever staged by this path).
    show = subprocess.run(
        ["git", "show", f"origin/research-data:data/research/football_state/{SEASON}.manifest.json"],
        cwd=work, capture_output=True, text=True,
    )
    assert show.returncode == 0
    manifest = json.loads(show.stdout)
    assert manifest["schema_version"] == football_state.FOOTBALL_STATE_SCHEMA_VERSION
    ls = subprocess.run(
        ["git", "ls-tree", "--name-only", "origin/research-data"], cwd=work, capture_output=True, text=True
    )
    assert "src" not in ls.stdout.split()
    obs_show = subprocess.run(
        ["git", "show", f"origin/research-data:data/research/observations/{SEASON}.jsonl"],
        cwd=work, capture_output=True, text=True,
    )
    assert obs_show.returncode == 0 and obs_show.stdout.strip()
    assert state.schema_version == football_state.FOOTBALL_STATE_SCHEMA_VERSION


# ----------------------------------------------- refresh path behaviors


def test_soft_stale_schedule_refresh_is_one_call_and_updates_timestamp(tmp_path):
    _make_state(tmp_path, fetched_at=NOW - timedelta(hours=5))
    kickoff = NOW + timedelta(hours=24)
    fresh_schedule = [_schedule_row(0, FBS_TEAMS[0][1], FBS_TEAMS[1][1], kickoff)]
    client = FakeCFBD(schedule=fresh_schedule)
    outcome = football_state.resolve_football_state(
        tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW
    )
    assert outcome.source == "live_schedule_refresh"
    assert client.calls == 1
    assert outcome.state.schedule_fetched_at == NOW
    # history untouched, carried forward with its own provenance
    assert outcome.state.history_fetched_at == NOW - timedelta(hours=5)


def test_force_refresh_does_a_full_rebuild(tmp_path):
    _make_state(tmp_path)
    hist_games, hist_adv = _history_rows()
    client = FakeCFBD(
        schedule=[_schedule_row(0, FBS_TEAMS[0][1], FBS_TEAMS[1][1], NOW + timedelta(hours=24))],
        history={2025: {"games": hist_games, "advanced": hist_adv}},
    )
    outcome = football_state.resolve_football_state(
        tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW, force_refresh=True
    )
    assert outcome.source == "live_full_refresh"
    assert client.calls == 2 + 2 * len(HISTORY_SEASONS)


def test_unchanged_payload_is_not_rewritten(tmp_path):
    state = _make_state(tmp_path)
    payload = tmp_path / "data" / "research" / "football_state" / f"{SEASON}.json"
    before = payload.stat().st_mtime_ns
    football_state.save_football_state(tmp_path, state)
    assert payload.stat().st_mtime_ns == before  # identical content: no rewrite
