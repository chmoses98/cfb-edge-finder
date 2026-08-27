"""Redundant triggering must be SAFE.

The whole trigger design deliberately runs the collector more often than
strictly necessary: a conductor chain, a `*/10` cron underneath it, and a
human able to press Run at any moment. That is only a good trade if an
extra invocation genuinely costs nothing but a few seconds of runner
time. These tests hold that line against the REAL collector path --
`research_scan_and_capture._apply_scan`, the same function both workflows
call -- rather than against a model of it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import research_scan_and_capture as collector  # noqa: E402
from scan_harness import (  # noqa: E402
    install_failing_market_feed,
    install_fake_market_feed,
    make_games,
    make_history_lines,
    make_markets,
)

from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache  # noqa: E402
from cfb_edge_finder.research import health, persistence  # noqa: E402
from cfb_edge_finder.research.scan_telemetry import ScanTelemetry  # noqa: E402
from cfb_edge_finder.schemas.provenance import ModelVersion  # noqa: E402

SEASON = 2026
NOW = None  # set per-test from the harness


def _model_version():
    return ModelVersion(
        model_version="trigger-test-1.0",
        ratings_component_version="ratings-1",
        pricing_engine_version="0.1.0",
    )


def _run_once(repo_dir: Path, monkeypatch, games, classification, markets, history, *, run_id: str, now):
    """One collector invocation, exactly as either workflow performs it."""
    install_fake_market_feed(monkeypatch, markets)
    report = health.CaptureHealthReport()
    telemetry = ScanTelemetry()
    result = collector._apply_scan(  # noqa: SLF001
        repo_dir,
        season=SEASON,
        games=games,
        classification_by_game_id=classification,
        fcs_school_names=frozenset(),
        cache=GameProjectionCache(history),
        kalshi_client=None,
        model_version=_model_version(),
        training_cutoff_fn=lambda request: f"strictly before season={request.as_of_season} week={request.as_of_week}",
        n_simulations=200,
        seed=0,
        now=now,
        schedule_source_timestamp=now,
        run_id=run_id,
        report=report,
        telemetry=telemetry,
    )
    return result, report, telemetry


def _rows(repo_dir: Path) -> list[dict]:
    path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def slate(tmp_path):
    games, classification = make_games(4)
    markets = make_markets(games)
    history = make_history_lines(games)
    now = min(g.kickoff_utc for g in games if g.kickoff_utc) - __import__("datetime").timedelta(hours=24)
    return games, classification, markets, history, now


def test_three_triggers_in_a_row_write_one_row_each(tmp_path, monkeypatch, slate):
    """GitHub cron, then the conductor seconds later, then a human. The
    canonical key is what dedups, so all three converge on one snapshot."""
    games, classification, markets, history, now = slate
    repo = tmp_path / "repo"
    repo.mkdir()

    first, report1, _ = _run_once(repo, monkeypatch, games, classification, markets, history,
                                  run_id="github-schedule", now=now)
    after_first = _rows(repo)
    assert after_first, "the harness must actually write rows or this proves nothing"
    assert report1.captures_written > 0

    for run_id in ("conductor-dispatch", "human-manual"):
        _, report, _ = _run_once(repo, monkeypatch, games, classification, markets, history,
                                 run_id=run_id, now=now)
        assert report.captures_written == 0, f"{run_id} rewrote rows that already existed"
        # Dedup short-circuits EARLIER than persistence: the capture-state
        # ledger makes the label not-due, so the redundant run never
        # prices anything. That is why an extra trigger is cheap enough
        # for the whole redundant design to be worth it.
        assert report.captures_due == 0
        assert report.supported_markets == 0, "a redundant trigger re-priced the slate"

    final = _rows(repo)
    assert len(final) == len(after_first), "a redundant trigger added rows"
    keys = [r["observation_key"] for r in final]
    assert len(keys) == len(set(keys)), "duplicate observation_key persisted"


def test_redundant_runs_do_not_alter_existing_rows(tmp_path, monkeypatch, slate):
    """Append-only means the first capture wins permanently -- a later
    trigger must not refresh a quote in place."""
    games, classification, markets, history, now = slate
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_once(repo, monkeypatch, games, classification, markets, history, run_id="a", now=now)
    before = _rows(repo)
    _run_once(repo, monkeypatch, games, classification, markets, history, run_id="b", now=now)
    assert _rows(repo) == before


def test_run_id_does_not_leak_into_the_dedup_key(tmp_path, monkeypatch, slate):
    """If run_id were part of the canonical key, every redundant trigger
    would silently double the corpus."""
    games, classification, markets, history, now = slate
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_once(repo, monkeypatch, games, classification, markets, history, run_id="run-AAA", now=now)
    keys_a = {r["observation_key"] for r in _rows(repo)}
    _run_once(repo, monkeypatch, games, classification, markets, history, run_id="run-ZZZ", now=now)
    assert {r["observation_key"] for r in _rows(repo)} == keys_a


def test_a_delayed_trigger_still_dedups(tmp_path, monkeypatch, slate):
    """A dispatch that arrives late -- the exact case the conductor exists
    to cover -- must not produce a second row for the same checkpoint."""
    import datetime as dt

    games, classification, markets, history, now = slate
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_once(repo, monkeypatch, games, classification, markets, history, run_id="on-time", now=now)
    count_before = len(_rows(repo))
    # Still inside T_24H's window (18h-30h), so the label is due again.
    _run_once(repo, monkeypatch, games, classification, markets, history,
              run_id="late", now=now + dt.timedelta(minutes=45))
    assert len(_rows(repo)) == count_before


def test_collector_failure_writes_nothing_and_leaves_corpus_intact(tmp_path, monkeypatch, slate):
    """A failing market feed must not half-write or corrupt the corpus."""
    games, classification, markets, history, now = slate
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_once(repo, monkeypatch, games, classification, markets, history, run_id="good", now=now)
    good = _rows(repo)

    install_failing_market_feed(monkeypatch, failing_series=set(markets))
    report = health.CaptureHealthReport()
    collector._apply_scan(  # noqa: SLF001
        repo, season=SEASON, games=games, classification_by_game_id=classification,
        fcs_school_names=frozenset(), cache=GameProjectionCache(history), kalshi_client=None,
        model_version=_model_version(),
        training_cutoff_fn=lambda r: "cutoff", n_simulations=200, seed=0, now=now,
        schedule_source_timestamp=now, run_id="failing", report=report, telemetry=ScanTelemetry(),
    )
    assert _rows(repo) == good, "a failed run mutated the corpus"


def test_no_trigger_path_can_produce_an_actionable_candidate(tmp_path, monkeypatch, slate):
    """Section 21: no operational failure or redundancy may yield an
    actionable candidate."""
    from cfb_edge_finder.expression.corpus import load_contract_snapshots
    from cfb_edge_finder.recommendation.eligibility import EligibilityConfig
    from cfb_edge_finder.recommendation.pipeline import run_pipeline

    games, classification, markets, history, now = slate
    repo = tmp_path / "repo"
    repo.mkdir()
    for run_id in ("cron", "conductor", "manual"):
        _run_once(repo, monkeypatch, games, classification, markets, history, run_id=run_id, now=now)

    path = persistence.canonical_path(repo / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    loaded = load_contract_snapshots(path)
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=now)
    assert result.card.actionable_count == 0
    assert result.card.entries == ()


def test_simultaneous_triggers_racing_past_due_resolution_still_dedup(tmp_path, monkeypatch, slate):
    """The genuine race the retry loop exists for.

    Two runs in flight at once BOTH resolve the label as due, because
    neither has seen the other's capture state yet. Due-label
    short-circuiting cannot help here, so the canonical-key check at the
    persistence layer is the last line of defence. Modelled by scanning
    into two independent repos from identical empty state and then
    replaying one's rows into the other, which is exactly what the
    push/reset/re-apply loop does after a non-fast-forward rejection."""
    games, classification, markets, history, now = slate
    repo_a, repo_b = tmp_path / "a", tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()

    _run_once(repo_a, monkeypatch, games, classification, markets, history, run_id="cron", now=now)
    _run_once(repo_b, monkeypatch, games, classification, markets, history, run_id="conductor", now=now)

    rows_a, rows_b = _rows(repo_a), _rows(repo_b)
    assert rows_a and rows_b
    assert {r["observation_key"] for r in rows_a} == {r["observation_key"] for r in rows_b}, (
        "two concurrent runs of the same slate must derive the same canonical keys"
    )

    path_a = persistence.canonical_path(repo_a / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    before = len(rows_a)
    result = persistence.append_json_rows(path_a, rows_b, lambda r: r["observation_key"])
    after = _rows(repo_a)

    assert len(after) == before, "the losing run's rows were appended a second time"
    assert result.written == 0
    assert result.skipped_duplicate == len(rows_b)
    keys = [r["observation_key"] for r in after]
    assert len(keys) == len(set(keys))
