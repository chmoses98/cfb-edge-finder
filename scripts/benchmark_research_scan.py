#!/usr/bin/env python3
"""Mission section 11: reproducible synthetic scale benchmarks for the
research scanner, across corpus sizes and market-universe sizes.

Run manually; this is NOT a CI gate. CI asserts the INSTRUMENTATION
invariants instead (tests/test_research_scan_scale.py) because wall-clock
thresholds on shared runners are flaky, while "the history file was read
once" is a fact that either holds or does not.

    python scripts/benchmark_research_scan.py --quick
    python scripts/benchmark_research_scan.py --json out.json

*** WHAT THE DEFAULT MODE MEASURES, AND WHY ***
The regression being characterized (a full corpus re-read per market
ticker) happened for EVERY discovered ticker, before any mapping or
pricing decision -- so the history/scheduling path is isolated by default
with synthetic unmapped tickers. That lets the ticker axis reach the
5,000 the mission asks for, which a real-team slate cannot (the registry
holds 138 FBS teams = 69 disjoint matchups). `--full-slate` additionally
runs the complete mapped-and-priced path on a real 69-game slate.

`--legacy` also runs the pre-optimization algorithm
(tests/reference/legacy_apply_scan.py) at the same sizes, so the speedup
is measured rather than asserted. It is off by default because at the
larger sizes it takes hours -- exactly the point.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_ROOT / "tests"))

import research_scan_and_capture as scanner  # noqa: E402
from scan_harness import (  # noqa: E402
    MAX_GAMES,
    NOW,
    SEASON,
    install_fake_market_feed,
    make_games,
    make_history_lines,
    make_markets,
)

from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache  # noqa: E402
from cfb_edge_finder.research import health, persistence  # noqa: E402
from cfb_edge_finder.research.scan_telemetry import ScanTelemetry  # noqa: E402
from cfb_edge_finder.schemas.provenance import ModelVersion  # noqa: E402

MODEL_VERSION = ModelVersion(model_version="benchmark-1.0", pricing_engine_version="0.1.0")

CORPUS_SIZES = (1_000, 10_000, 50_000, 100_000)
TICKER_COUNTS = (500, 2_000, 5_000)


class _FakeMonkeypatch:
    """Minimal setattr/undo shim so this script can reuse the pytest-shaped
    harness helper without importing pytest."""

    def __init__(self) -> None:
        self._undo: list[tuple[object, str, object]] = []

    def setattr(self, target, name, value):  # noqa: A002
        self._undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self) -> None:
        for target, name, old in reversed(self._undo):
            setattr(target, name, old)
        self._undo.clear()


_ROW_TEMPLATE: dict | None = None


def _row_template() -> dict:
    """A FULLY SCHEMA-VALID corpus row, produced by actually running the
    real scanner once on a one-game slate.

    Using a hand-written compact stub here would quietly bias the whole
    benchmark: `load_observation_index` reads the decoded dict, but the
    LEGACY path being compared against calls `read_observation_rows`,
    which pydantic-validates every row -- a stub row would make the legacy
    side crash rather than be measured, and would also understate row size
    (real rows are ~2.4 KB, roughly 15x a stub). Deriving the template
    from real output keeps both sides honest."""
    global _ROW_TEMPLATE
    if _ROW_TEMPLATE is not None:
        return _ROW_TEMPLATE
    mp = _FakeMonkeypatch()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            games, classification = make_games(1)
            install_fake_market_feed(mp, make_markets(games))
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
                n_simulations=200,
                seed=0,
                now=NOW,
                schedule_source_timestamp=NOW,
                run_id="template",
                report=health.CaptureHealthReport(),
                telemetry=ScanTelemetry(),
            )
            obs = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
            _ROW_TEMPLATE = json.loads(obs.read_text(encoding="utf-8").splitlines()[0])
    finally:
        mp.undo()
    return _ROW_TEMPLATE


def _write_synthetic_corpus(path: Path, n_rows: int) -> None:
    """`n_rows` schema-valid rows cloned from a real one, with distinct
    canonical keys and tickers so dedup and the ticker index are both
    exercised at full width."""
    path.parent.mkdir(parents=True, exist_ok=True)
    template = _row_template()
    labels = ("EARLY_OPEN", "T_7D", "T_3D", "T_24H", "T_6H")
    with path.open("w", encoding="utf-8") as handle:
        for i in range(n_rows):
            row = json.loads(json.dumps(template))
            row["observation_key"] = f"synthetic-key-{i:08d}"
            row["observation"]["kalshi_market_ticker"] = f"SYNTH-TICKER-{i % 4000:05d}"
            row["observation"]["snapshot_timing"]["label"] = labels[i % len(labels)]
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _synthetic_markets(n_tickers: int) -> dict[str, list[dict]]:
    """Unmapped tickers: enough to drive the discovery/scheduling loop at
    scale without needing a team registry entry per matchup."""
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


def _peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _run_once(*, corpus_rows: int, markets_by_series, games, classification, history, legacy: bool):
    mp = _FakeMonkeypatch()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp)
            obs = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
            _write_synthetic_corpus(obs, corpus_rows)
            install_fake_market_feed(mp, markets_by_series)
            report = health.CaptureHealthReport()
            telemetry = ScanTelemetry()
            kwargs = dict(
                season=SEASON,
                games=games,
                classification_by_game_id=classification,
                fcs_school_names=frozenset(),
                cache=GameProjectionCache(history),
                kalshi_client=None,
                model_version=MODEL_VERSION,
                training_cutoff_fn=lambda r: "cutoff",
                n_simulations=300,
                seed=0,
                now=NOW,
                schedule_source_timestamp=NOW,
                run_id="benchmark",
                report=report,
            )
            started = time.perf_counter()
            if legacy:
                from reference.legacy_apply_scan import _apply_scan as legacy_scan

                legacy_scan(repo_dir, report=report, **{k: v for k, v in kwargs.items() if k != "report"})
            else:
                scanner._apply_scan(
                    repo_dir, telemetry=telemetry, **{k: v for k, v in kwargs.items() if k != "report"}, report=report
                )
            elapsed = time.perf_counter() - started
            return {
                "seconds": round(elapsed, 3),
                "markets_scanned": report.markets_scanned,
                "captures_written": report.captures_written,
                "history_load_count": None if legacy else telemetry.history_load_count,
                "history_load_seconds": None if legacy else round(telemetry.history_load_seconds, 4),
                "peak_rss_mb": round(_peak_rss_mb(), 1),
            }
    finally:
        mp.undo()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quick", action="store_true", help="Smaller grid for a fast sanity pass.")
    parser.add_argument("--legacy", action="store_true", help="Also run the pre-optimization algorithm (SLOW).")
    parser.add_argument("--full-slate", action="store_true", help="Also run the fully mapped/priced 69-game slate.")
    parser.add_argument("--json", type=Path, default=None, help="Write results to this JSON file.")
    args = parser.parse_args()

    corpus_sizes = (1_000, 10_000) if args.quick else CORPUS_SIZES
    ticker_counts = (500, 2_000) if args.quick else TICKER_COUNTS

    results = []
    print(f"{'mode':<10} {'corpus':>8} {'tickers':>8} {'seconds':>9} {'loads':>6} {'load_s':>8} {'rss_mb':>8}")
    print("-" * 64)
    for corpus_rows in corpus_sizes:
        for n_tickers in ticker_counts:
            markets = _synthetic_markets(n_tickers)
            row = _run_once(
                corpus_rows=corpus_rows,
                markets_by_series=markets,
                games=[],
                classification={},
                history=[],
                legacy=False,
            )
            row.update(mode="optimized", corpus_rows=corpus_rows, tickers=n_tickers)
            results.append(row)
            print(
                f"{'optimized':<10} {corpus_rows:>8} {n_tickers:>8} {row['seconds']:>9} "
                f"{row['history_load_count']:>6} {row['history_load_seconds']:>8} {row['peak_rss_mb']:>8}"
            )

            if args.legacy:
                legacy_row = _run_once(
                    corpus_rows=corpus_rows,
                    markets_by_series=markets,
                    games=[],
                    classification={},
                    history=[],
                    legacy=True,
                )
                legacy_row.update(mode="legacy", corpus_rows=corpus_rows, tickers=n_tickers)
                results.append(legacy_row)
                speedup = legacy_row["seconds"] / max(row["seconds"], 1e-9)
                print(
                    f"{'legacy':<10} {corpus_rows:>8} {n_tickers:>8} {legacy_row['seconds']:>9} "
                    f"{'n/a':>6} {'n/a':>8} {legacy_row['peak_rss_mb']:>8}   ({speedup:.0f}x slower)"
                )

    if args.full_slate:
        print("\nFull mapped+priced slate (real registry teams):")
        games, classification = make_games(MAX_GAMES)
        markets = make_markets(games)
        history = make_history_lines(games)
        for corpus_rows in corpus_sizes:
            row = _run_once(
                corpus_rows=corpus_rows,
                markets_by_series=markets,
                games=games,
                classification=classification,
                history=history,
                legacy=False,
            )
            row.update(mode="optimized-full-slate", corpus_rows=corpus_rows, tickers=row["markets_scanned"])
            results.append(row)
            print(
                f"{'full':<10} {corpus_rows:>8} {row['markets_scanned']:>8} {row['seconds']:>9} "
                f"{row['history_load_count']:>6} {row['history_load_seconds']:>8} {row['peak_rss_mb']:>8}"
            )

    if args.json is not None:
        args.json.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
