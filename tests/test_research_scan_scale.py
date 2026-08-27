"""Mission section 11: scale behaviour, asserted the way it can be
asserted reliably in CI.

*** WHY THESE ARE INSTRUMENTATION ASSERTIONS, NOT TIMING THRESHOLDS ***
The property that actually matters is a COMPLEXITY one: runtime must not
grow multiplicatively with (history size x ticker count). A wall-clock
threshold for that is flaky on shared CI runners -- a noisy neighbour
turns a real pass into a red build, and everyone learns to re-run the job
instead of reading it. The counted invariants below ("the history file was
loaded once", "the index equals a full read at 100k rows") are exact:
they cannot flake, and they fail for exactly the reason the timing test
was meant to catch.

Measured wall-clock scaling is reported separately and deliberately, by
scripts/benchmark_research_scan.py -- run by hand, never gating a merge.
The one timing assertion here is a deliberately enormous sanity bound
(see its own comment) that only trips on a genuine algorithmic regression.
"""

from __future__ import annotations

import json
import sys
import time
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
)

from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache  # noqa: E402
from cfb_edge_finder.research import health, persistence  # noqa: E402
from cfb_edge_finder.research.scan_telemetry import ScanTelemetry  # noqa: E402
from cfb_edge_finder.schemas.provenance import ModelVersion  # noqa: E402

MODEL_VERSION = ModelVersion(model_version="scale-test-1.0", pricing_engine_version="0.1.0")


def _write_corpus(path: Path, n_rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    labels = ("EARLY_OPEN", "T_7D", "T_3D", "T_24H", "T_6H")
    with path.open("w", encoding="utf-8") as handle:
        for i in range(n_rows):
            handle.write(
                json.dumps(
                    {
                        "observation_key": f"synthetic-key-{i:08d}",
                        "schema_version": "research_corpus_v1",
                        "season": SEASON,
                        "observation": {
                            "kalshi_market_ticker": f"SYNTH-TICKER-{i % 4000:05d}",
                            # `i // 4000`, not `i % 5`: the ticker index is
                            # `i % 4000` and 5 divides 4000, so `i % 5` would
                            # pin every row of a given ticker to ONE label and
                            # never exercise multi-label tickers at all.
                            "snapshot_timing": {
                                "label": labels[(i // 4_000) % len(labels)],
                                "hours_before_kickoff": 100.0,
                            },
                        },
                    },
                    sort_keys=True,
                )
                + "\n"
            )


def _synthetic_markets(n_tickers: int) -> dict[str, list[dict]]:
    markets = []
    for i in range(n_tickers):
        event = f"KXNCAAFGAME-SYNTH{i // 10:05d}"
        markets.append(
            {
                "ticker": f"{event}-C{i:05d}",
                "event_ticker": event,
                "title": "Synthetic contract",
                "status": "active",
                "yes_bid_dollars": "0.40",
                "yes_ask_dollars": "0.55",
                "no_bid_dollars": "0.45",
                "no_ask_dollars": "0.60",
            }
        )
    return {"KXNCAAFGAME": markets, "KXNCAAFSPREAD": [], "KXNCAAFTOTAL": []}


def _scan_at_scale(tmp_path: Path, monkeypatch, *, corpus_rows: int, n_tickers: int):
    repo_dir = tmp_path / f"c{corpus_rows}_t{n_tickers}"
    repo_dir.mkdir(parents=True, exist_ok=True)
    obs = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    _write_corpus(obs, corpus_rows)
    install_fake_market_feed(monkeypatch, _synthetic_markets(n_tickers))
    telemetry = ScanTelemetry()
    report = health.CaptureHealthReport()
    started = time.perf_counter()
    scanner._apply_scan(
        repo_dir,
        season=SEASON,
        games=[],
        classification_by_game_id={},
        fcs_school_names=frozenset(),
        cache=GameProjectionCache([]),
        kalshi_client=None,
        model_version=MODEL_VERSION,
        training_cutoff_fn=lambda r: "cutoff",
        n_simulations=100,
        seed=0,
        now=NOW,
        schedule_source_timestamp=NOW,
        run_id="scale",
        report=report,
        telemetry=telemetry,
    )
    return time.perf_counter() - started, telemetry, report


@pytest.mark.parametrize("corpus_rows", [1_000, 10_000, 50_000])
@pytest.mark.parametrize("n_tickers", [500, 2_000])
def test_history_is_read_once_at_every_scale(tmp_path, monkeypatch, corpus_rows, n_tickers):
    """The load-bearing assertion of this whole milestone, across the
    mission's corpus/market grid."""
    _, telemetry, report = _scan_at_scale(tmp_path, monkeypatch, corpus_rows=corpus_rows, n_tickers=n_tickers)
    assert telemetry.history_load_count == 1
    assert telemetry.history_row_count == corpus_rows
    assert report.markets_scanned == n_tickers


def test_index_is_exact_at_one_hundred_thousand_rows(tmp_path):
    """The largest corpus size the mission asks about. Checks CORRECTNESS
    at scale (the index must still equal a full canonical read), not
    speed."""
    path = persistence.canonical_path(tmp_path / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    _write_corpus(path, 100_000)
    index = persistence.load_observation_index(path)
    assert index.row_count == 100_000
    assert index.load_count == 1
    assert index.malformed_rows == 0
    assert len(index.keys) == 100_000
    assert index.keys == persistence.read_observation_keys(path)
    assert len(index.labels_by_ticker) == 4_000
    assert sum(len(v) for v in index.labels_by_ticker.values()) == 5 * 4_000


def test_runtime_does_not_grow_multiplicatively_with_history_times_tickers(tmp_path, monkeypatch):
    """The complexity claim, expressed so it cannot flake.

    Legacy did O(tickers x history): holding the corpus fixed and
    quadrupling the ticker count multiplied history-reading work by four.
    Optimized reads history once, so ticker count moves runtime only
    through genuine per-ticker work.

    The bound is deliberately absurd (4x the tickers may cost up to 10x
    the wall clock) because the point is to catch a REINTRODUCED
    per-ticker full read -- which at these sizes costs orders of
    magnitude, not tens of percent -- while never tripping on CI noise."""
    corpus_rows = 20_000
    small_time, small_tel, _ = _scan_at_scale(tmp_path, monkeypatch, corpus_rows=corpus_rows, n_tickers=500)
    large_time, large_tel, _ = _scan_at_scale(tmp_path, monkeypatch, corpus_rows=corpus_rows, n_tickers=2_000)

    assert small_tel.history_load_count == large_tel.history_load_count == 1

    floor = 0.05  # seconds; below this, timer noise dominates entirely
    ratio = max(large_time, floor) / max(small_time, floor)
    assert ratio < 10.0, (
        f"4x the tickers cost {ratio:.1f}x the runtime at a fixed {corpus_rows}-row corpus "
        "-- history work looks like it scales with ticker count again"
    )


def test_growing_the_corpus_does_not_change_ticker_work(tmp_path, monkeypatch):
    """The other axis: holding tickers fixed, a 10x larger corpus must add
    ONE larger read, not 10x work per ticker."""
    small_time, small_tel, _ = _scan_at_scale(tmp_path, monkeypatch, corpus_rows=5_000, n_tickers=1_000)
    large_time, large_tel, _ = _scan_at_scale(tmp_path, monkeypatch, corpus_rows=50_000, n_tickers=1_000)

    assert small_tel.history_load_count == large_tel.history_load_count == 1
    assert large_tel.history_row_count == 50_000

    # All the extra time must be attributable to the single larger load.
    extra_total = max(large_time - small_time, 0.0)
    extra_load = large_tel.history_load_seconds - small_tel.history_load_seconds
    assert extra_load >= 0
    assert extra_total < max(extra_load * 20.0, 5.0), (
        f"10x corpus added {extra_total:.3f}s total but only {extra_load:.3f}s of history loading "
        "-- the corpus is being re-read somewhere else"
    )


def test_memory_stays_proportional_to_the_index_not_the_corpus_text(tmp_path):
    """The index keeps keys and per-ticker label sets, never the decoded
    rows themselves -- so a large corpus must not be held in memory."""
    path = persistence.canonical_path(tmp_path / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    _write_corpus(path, 50_000)
    index = persistence.load_observation_index(path)
    # No attribute anywhere on the index holds decoded rows.
    for value in vars(index).values():
        assert not isinstance(value, list), "index retains a list -- likely the whole decoded corpus"
    assert len(index.keys) == 50_000
    assert len(index.labels_by_ticker) == 4_000
