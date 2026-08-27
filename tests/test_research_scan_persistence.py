"""Mission sections 4, 5, 7 and 10: the optimization must not weaken the
append-only/dedup contract, and the one-load-per-run property must be
ASSERTED rather than assumed.

The headline test here is `test_history_file_is_opened_exactly_once`: it
counts real `open()` calls on the observations file during a scan. That
is deliberately a behavioural count rather than a timing check -- a timing
threshold would be flaky in CI, but "the file was read 4,578 times"
either happened or it did not.
"""

from __future__ import annotations

import builtins
import json
import sys
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
from cfb_edge_finder.research import health, persistence  # noqa: E402
from cfb_edge_finder.research.scan_telemetry import ScanTelemetry  # noqa: E402
from cfb_edge_finder.schemas.provenance import ModelVersion  # noqa: E402

MODEL_VERSION = ModelVersion(model_version="persistence-test-1.0", pricing_engine_version="0.1.0")


def _run_scan(repo_dir: Path, monkeypatch, *, n_games: int = 4, run_id: str = "run-1"):
    games, classification = make_games(n_games)
    markets = make_markets(games)
    install_fake_market_feed(monkeypatch, markets)
    telemetry = ScanTelemetry()
    report = health.CaptureHealthReport()
    result = scanner._apply_scan(
        repo_dir,
        season=SEASON,
        games=games,
        classification_by_game_id=classification,
        fcs_school_names=frozenset(),
        cache=GameProjectionCache(make_history_lines(games)),
        kalshi_client=None,
        model_version=MODEL_VERSION,
        training_cutoff_fn=lambda r: f"strictly before season={r.as_of_season} week={r.as_of_week}",
        n_simulations=300,
        seed=0,
        now=NOW,
        schedule_source_timestamp=NOW,
        run_id=run_id,
        report=report,
        telemetry=telemetry,
    )
    return result, telemetry, report


def _obs_path(repo_dir: Path) -> Path:
    return persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)


def _rows(repo_dir: Path) -> list[dict]:
    path = _obs_path(repo_dir)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _seed(repo_dir: Path, rows: list[dict]) -> None:
    path = _obs_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


# --- The core scaling invariant -----------------------------------------


def test_history_file_is_opened_exactly_once_for_reading(tmp_path, monkeypatch):
    """THE regression guard. Before this work the observations file was
    re-opened and fully re-parsed once per market ticker (plus once more
    per row written); with a 1,724-row corpus and 4,578 live tickers that
    was ~4,578 full reads per run and it grew with the corpus. Exactly one
    read is allowed now, no matter how many tickers exist."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    # Seed a real history so the read is not trivially skipped.
    first_result, _, _ = _run_scan(repo_dir, monkeypatch, run_id="seed")
    assert first_result.written > 0
    seeded_rows = _rows(repo_dir)
    assert len(seeded_rows) > 0

    target = _obs_path(repo_dir).resolve()
    read_opens: list[str] = []
    real_open = builtins.open
    real_path_open = Path.open

    def _count(mode: str) -> None:
        if "r" in mode and "+" not in mode:
            read_opens.append(mode)

    def _patched_builtin_open(file, mode="r", *args, **kwargs):
        try:
            if Path(file).resolve() == target:
                _count(mode)
        except (TypeError, OSError, ValueError):
            pass
        return real_open(file, mode, *args, **kwargs)

    def _patched_path_open(self, mode="r", *args, **kwargs):
        try:
            if self.resolve() == target:
                _count(mode)
        except (OSError, ValueError):
            pass
        return real_path_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _patched_builtin_open)
    monkeypatch.setattr(Path, "open", _patched_path_open)

    _, telemetry, report = _run_scan(repo_dir, monkeypatch, run_id="counted")

    assert report.markets_scanned > 50, "too few tickers for this test to be meaningful"
    assert len(read_opens) == 1, (
        f"observations file opened for reading {len(read_opens)} times across "
        f"{report.markets_scanned} tickers -- the per-ticker re-read has regressed"
    )
    assert telemetry.history_load_count == 1
    assert telemetry.history_row_count == len(seeded_rows)


def test_history_load_count_stays_one_as_ticker_count_grows(tmp_path, monkeypatch):
    """The scaling shape, stated as a behavioural invariant rather than a
    timing assertion: 10x the tickers, still one history read."""
    for n_games in (2, 20):
        repo_dir = tmp_path / f"repo{n_games}"
        repo_dir.mkdir()
        _run_scan(repo_dir, monkeypatch, n_games=n_games, run_id="seed")
        _, telemetry, report = _run_scan(repo_dir, monkeypatch, n_games=n_games, run_id="second")
        assert telemetry.history_load_count == 1, f"n_games={n_games} loaded history {telemetry.history_load_count}x"
        assert report.markets_scanned > 0


def test_scanner_never_calls_the_full_pydantic_row_reader(tmp_path, monkeypatch):
    """`read_observation_rows` re-validates every historical row against
    the CURRENT schema. That is fine for reporting tools, but a scanner
    that calls it per ticker is the exact bug this milestone fixed -- and
    it would also make a run fail outright on a single legacy-schema row."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _run_scan(repo_dir, monkeypatch, run_id="seed")

    calls: list[Path] = []
    real = persistence.read_observation_rows
    monkeypatch.setattr(
        persistence, "read_observation_rows", lambda path: (calls.append(path), real(path))[1]
    )
    _run_scan(repo_dir, monkeypatch, run_id="second")
    assert calls == [], f"scanner still calls read_observation_rows: {calls}"


# --- Append-only / dedup contract ---------------------------------------


def test_empty_history_writes_every_row_once(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    assert not _obs_path(repo_dir).exists()
    result, telemetry, _ = _run_scan(repo_dir, monkeypatch)
    rows = _rows(repo_dir)
    assert result.written == len(rows) > 0
    assert result.skipped_duplicate == 0
    assert telemetry.history_row_count == 0
    keys = [r["observation_key"] for r in rows]
    assert len(keys) == len(set(keys))


def test_rerunning_the_same_scan_appends_nothing_and_rewrites_nothing(tmp_path, monkeypatch):
    """Append-only + idempotence: byte-for-byte identical file after a
    second identical run, and zero overwrites."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _run_scan(repo_dir, monkeypatch, run_id="first")
    after_first = _obs_path(repo_dir).read_bytes()

    result, _, _ = _run_scan(repo_dir, monkeypatch, run_id="second")
    after_second = _obs_path(repo_dir).read_bytes()

    assert result.written == 0, "an identical re-scan wrote new rows"
    assert after_second == after_first, "existing corpus bytes changed -- not append-only"


def test_existing_rows_are_immutable_and_never_reordered(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _run_scan(repo_dir, monkeypatch, n_games=2, run_id="first")
    original = _obs_path(repo_dir).read_text(encoding="utf-8").splitlines()

    # A larger slate: strictly more markets, so genuinely new rows append.
    _run_scan(repo_dir, monkeypatch, n_games=6, run_id="second")
    after = _obs_path(repo_dir).read_text(encoding="utf-8").splitlines()

    assert len(after) > len(original), "second scan appended nothing -- test is vacuous"
    assert after[: len(original)] == original, "pre-existing lines were modified, reordered, or re-serialized"


def test_duplicate_insertions_are_rejected_by_canonical_key(tmp_path, monkeypatch):
    """The append-level defence-in-depth path (the one the git retry loop
    relies on): rows whose canonical key is already on disk are rejected,
    whether the index is supplied or not."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _run_scan(repo_dir, monkeypatch, run_id="first")
    path = _obs_path(repo_dir)
    rows = _rows(repo_dir)

    from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow

    typed = [ResearchCorpusRow.model_validate(r) for r in rows]

    # Without an index (re-reads from disk, the historical behaviour).
    plain = persistence.append_observation_rows(path, typed)
    assert plain.written == 0 and plain.skipped_duplicate == len(typed)

    # With a freshly loaded index -- must reach the identical verdict.
    index = persistence.load_observation_index(path)
    indexed = persistence.append_observation_rows(path, typed, index=index)
    assert indexed.written == 0 and indexed.skipped_duplicate == len(typed)
    assert _rows(repo_dir) == rows, "a duplicate-rejecting append still mutated the file"


def test_duplicate_heavy_batch_writes_each_key_exactly_once(tmp_path, monkeypatch):
    """A single batch that is mostly duplicates OF ITSELF -- in-batch
    dedup must still write each canonical key exactly once, and the
    supplied index must absorb exactly what was written."""
    from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    _run_scan(source_dir, monkeypatch, n_games=2, run_id="source")
    real_rows = _rows(source_dir)
    assert len(real_rows) >= 5

    distinct = [ResearchCorpusRow.model_validate(r) for r in real_rows[:5]]
    batch = [row for row in distinct for _ in range(4)]  # each key 4x

    target = tmp_path / "target"
    path = persistence.canonical_path(target / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    path.parent.mkdir(parents=True, exist_ok=True)

    index = persistence.load_observation_index(path)
    result = persistence.append_observation_rows(path, batch, index=index)

    assert result.written == 5
    assert result.skipped_duplicate == 15
    written_keys = [json.loads(line)["observation_key"] for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(written_keys) == len(set(written_keys)) == 5
    assert index.keys == set(written_keys), "index did not absorb exactly the rows it wrote"
    assert index.row_count == 5


@pytest.mark.parametrize("history_size", [0, 1, 250, 5000])
def test_index_matches_a_full_read_at_every_history_size(tmp_path, history_size):
    """`load_observation_index` must derive EXACTLY the key set the
    canonical reader derives -- at empty, tiny, and large corpus sizes."""
    path = persistence.canonical_path(tmp_path / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    path.parent.mkdir(parents=True, exist_ok=True)
    expected_labels: dict[str, set[str]] = {}
    with path.open("w", encoding="utf-8") as handle:
        for i in range(history_size):
            row = json.loads(_SAMPLE_ROW)
            row["observation_key"] = f"key-{i}"
            ticker = f"TICK-{i % 37}"
            label = ["EARLY_OPEN", "T_24H", "T_6H"][i % 3]
            row["observation"]["kalshi_market_ticker"] = ticker
            row["observation"]["snapshot_timing"]["label"] = label
            expected_labels.setdefault(ticker, set()).add(label)
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    index = persistence.load_observation_index(path)
    assert index.keys == persistence.read_observation_keys(path)
    assert index.row_count == history_size
    assert index.load_count == 1
    assert index.malformed_rows == 0
    assert index.labels_by_ticker == expected_labels
    for ticker, labels in expected_labels.items():
        assert index.captured_labels_for(ticker) == labels
    assert index.captured_labels_for("NO-SUCH-TICKER") == set()


def test_index_tolerates_and_counts_malformed_lines(tmp_path):
    """A corpus row this run cannot decode must never break dedup for the
    rows it CAN decode -- it is counted and reported instead."""
    path = persistence.canonical_path(tmp_path / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    path.parent.mkdir(parents=True, exist_ok=True)
    good = json.loads(_SAMPLE_ROW)
    good["observation_key"] = "good-key"
    path.write_text(
        json.dumps(good, sort_keys=True) + "\n" + "{not json at all\n" + "\n",
        encoding="utf-8",
    )
    index = persistence.load_observation_index(path)
    assert index.keys == {"good-key"}
    assert index.row_count == 1
    assert index.malformed_rows == 1


def test_index_ignores_rows_missing_scheduling_fields(tmp_path):
    """A row from an older/partial schema still contributes its dedup key
    even if the scheduler cannot read a ticker/label out of it."""
    path = persistence.canonical_path(tmp_path / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"observation_key": "legacy-key"}) + "\n", encoding="utf-8")
    index = persistence.load_observation_index(path)
    assert index.keys == {"legacy-key"}
    assert index.labels_by_ticker == {}
    assert index.row_count == 1
    assert index.malformed_rows == 0


def test_multiple_snapshots_of_one_ticker_accumulate_labels(tmp_path, monkeypatch):
    """Two runs at different times against the same ticker must produce
    two distinct, both-retained snapshot rows."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    games, classification = make_games(2)
    markets = make_markets(games)
    history = make_history_lines(games)

    labels_seen = set()
    for offset_hours, run in ((24.0, "r1"), (6.0, "r2")):
        games_at, classification_at = make_games(2, kickoff_hours_ahead=offset_hours)
        install_fake_market_feed(monkeypatch, make_markets(games_at))
        scanner._apply_scan(
            repo_dir,
            season=SEASON,
            games=games_at,
            classification_by_game_id=classification_at,
            fcs_school_names=frozenset(),
            cache=GameProjectionCache(history),
            kalshi_client=None,
            model_version=MODEL_VERSION,
            training_cutoff_fn=lambda r: "cutoff",
            n_simulations=300,
            seed=0,
            now=NOW,
            schedule_source_timestamp=NOW,
            run_id=run,
            report=health.CaptureHealthReport(),
            telemetry=ScanTelemetry(),
        )
    assert classification is not None and markets is not None and games is not None
    rows = _rows(repo_dir)
    for row in rows:
        labels_seen.add(row["observation"]["snapshot_timing"]["label"])
    assert len(labels_seen) >= 2, f"expected multiple timing labels across runs, got {labels_seen}"
    keys = [r["observation_key"] for r in rows]
    assert len(keys) == len(set(keys)), "distinct snapshots collided on one canonical key"


def test_prior_proof_corpus_loads_unchanged(tmp_path, monkeypatch):
    """Compatibility with a corpus written BEFORE this change: seed rows
    produced by the legacy writer, then scan. Nothing pre-existing may be
    touched."""
    from reference.legacy_apply_scan import _apply_scan as legacy_apply_scan

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    games, classification = make_games(3)
    install_fake_market_feed(monkeypatch, make_markets(games))
    legacy_apply_scan(
        repo_dir,
        season=SEASON,
        games=games,
        classification_by_game_id=classification,
        fcs_school_names=frozenset(),
        cache=GameProjectionCache(make_history_lines(games)),
        kalshi_client=None,
        model_version=MODEL_VERSION,
        training_cutoff_fn=lambda r: "cutoff",
        n_simulations=300,
        seed=0,
        now=NOW,
        schedule_source_timestamp=NOW,
        run_id="legacy",
        report=health.CaptureHealthReport(),
    )
    legacy_bytes = _obs_path(repo_dir).read_bytes()
    assert len(legacy_bytes) > 0

    index = persistence.load_observation_index(_obs_path(repo_dir))
    assert index.keys == persistence.read_observation_keys(_obs_path(repo_dir))

    result, telemetry, _ = _run_scan(repo_dir, monkeypatch, n_games=3, run_id="optimized")
    assert result.written == 0, "optimized scan re-wrote rows the legacy writer already stored"
    assert _obs_path(repo_dir).read_bytes() == legacy_bytes
    assert telemetry.history_load_count == 1


_SAMPLE_ROW = json.dumps(
    {
        "capture_mode": "PROSPECTIVE",
        "capture_window_version": "capture_window_v1",
        "observation_key": "placeholder",
        "schema_version": "research_corpus_v1",
        "season": SEASON,
        "observation": {
            "kalshi_market_ticker": "TICK-0",
            "snapshot_timing": {"label": "EARLY_OPEN", "hours_before_kickoff": 100.0},
        },
    }
)
