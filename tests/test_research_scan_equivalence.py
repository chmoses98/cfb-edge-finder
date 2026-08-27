"""Mission section 9: the optimized scanner must produce research output
IDENTICAL to the pre-optimization scanner.

This is not an assertion about the diff -- it re-runs the actual old
algorithm (tests/reference/legacy_apply_scan.py, extracted verbatim from
main@6015276) and the optimized one against the SAME captured market
input, same games, same model inputs, same clock, and diffs the resulting
corpus files.

Only two fields are legitimately run-specific and normalized away before
comparison: `snapshot_id` (a fresh uuid4 per observation) and the
generated-at timestamps derived from the run clock. Everything else --
including every field mission section 9 enumerates -- is compared
byte-for-byte.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Both the shared harness and the production scanner live outside the
# package: `tests/` for the harness, `scripts/` for the scanner itself.
# These inserts must happen before those imports, hence the E402s below.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tests"))
sys.path.insert(0, str(_ROOT / "scripts"))

import research_scan_and_capture as optimized  # noqa: E402
from reference.legacy_apply_scan import _apply_scan as legacy_apply_scan  # noqa: E402
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

MODEL_VERSION = ModelVersion(
    model_version="equivalence-test-model-1.0",
    ratings_component_version="ridge_lambda=25",
    pricing_engine_version="0.1.0",
)

RUN_SPECIFIC_FIELDS = ("snapshot_id",)
"""Mission section 9's allowed-to-differ list. Deliberately as SHORT as
possible: every other field, including all timing/provenance/versioning
metadata, is pinned by passing the same `now` to both runs, so it must
match exactly rather than being normalized away."""


def _training_cutoff_fn(request) -> str:
    return f"strictly before season={request.as_of_season} week={request.as_of_week}"


def _normalize(row: dict) -> dict:
    """Strips ONLY the fields mission section 9 permits to be
    run-specific. Everything else is left exactly as written."""
    normalized = json.loads(json.dumps(row, sort_keys=True))
    for field in RUN_SPECIFIC_FIELDS:
        normalized.get("observation", {}).pop(field, None)
    return normalized


def _read_normalized(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [_normalize(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _common_kwargs(games, classification, cache):
    return dict(
        season=SEASON,
        games=games,
        classification_by_game_id=classification,
        fcs_school_names=frozenset(),
        cache=cache,
        kalshi_client=None,
        model_version=MODEL_VERSION,
        training_cutoff_fn=_training_cutoff_fn,
        n_simulations=400,
        seed=0,
        now=NOW,
        schedule_source_timestamp=NOW,
        run_id="equivalence-run",
    )


def _seed_history(repo_dir: Path, rows: list[dict]) -> None:
    path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _run_pair(tmp_path: Path, monkeypatch, *, n_games: int, seed_rows: list[dict] | None = None):
    """Runs legacy and optimized against byte-identical starting state and
    returns (legacy_rows, optimized_rows, legacy_result, optimized_result,
    telemetry). Each side gets its own repo dir seeded identically."""
    games, classification = make_games(n_games)
    markets = make_markets(games)
    history = make_history_lines(games)

    outputs = {}
    for name in ("legacy", "optimized"):
        repo_dir = tmp_path / name
        repo_dir.mkdir(parents=True, exist_ok=True)
        if seed_rows:
            _seed_history(repo_dir, seed_rows)
        install_fake_market_feed(monkeypatch, markets)
        # A FRESH cache per side: sharing one would let the first run warm
        # the second's projections and hide a real divergence.
        cache = GameProjectionCache(history)
        report = health.CaptureHealthReport()
        kwargs = _common_kwargs(games, classification, cache)
        if name == "legacy":
            result = legacy_apply_scan(repo_dir, report=report, **kwargs)
            telemetry = None
        else:
            telemetry = ScanTelemetry()
            result = optimized._apply_scan(repo_dir, report=report, telemetry=telemetry, **kwargs)
        obs = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
        state = persistence.canonical_path(repo_dir / "data" / "research", persistence.CAPTURE_STATE_SUBDIR, SEASON)
        outputs[name] = {
            "rows": _read_normalized(obs),
            "state": _read_normalized(state),
            "result": result,
            "report": report,
            "telemetry": telemetry,
        }
    return outputs


@pytest.fixture(scope="module")
def _sanity_games():
    return make_games(3)


def test_harness_actually_produces_priced_observations(tmp_path, monkeypatch):
    """Guards the whole equivalence suite: a fixture that silently priced
    NOTHING would make every comparison below pass vacuously."""
    out = _run_pair(tmp_path, monkeypatch, n_games=3)
    assert len(out["optimized"]["rows"]) > 0
    priced = [r for r in out["optimized"]["rows"] if r["observation"]["pricing_status"] == "model_priced"]
    assert len(priced) > 0, "fixture priced no contracts -- equivalence would be vacuous"


@pytest.mark.parametrize("n_games", [1, 3, 8])
def test_optimized_output_is_byte_identical_to_legacy(tmp_path, monkeypatch, n_games):
    out = _run_pair(tmp_path, monkeypatch, n_games=n_games)
    legacy, optimized_rows = out["legacy"]["rows"], out["optimized"]["rows"]

    assert len(legacy) == len(optimized_rows), "observation COUNT differs"
    assert [r["observation_key"] for r in legacy] == [r["observation_key"] for r in optimized_rows]
    assert legacy == optimized_rows, "normalized corpus rows are not byte-identical"


def test_every_mission_section_9_field_matches(tmp_path, monkeypatch):
    """Explicit, field-by-field version of the byte comparison above --
    so a failure names the field that diverged instead of dumping a diff
    of two large dicts."""
    out = _run_pair(tmp_path, monkeypatch, n_games=4)
    legacy, opt = out["legacy"]["rows"], out["optimized"]["rows"]
    assert len(legacy) == len(opt) > 0

    row_fields = ("observation_key", "season", "game_status_at_capture", "kickoff_utc_at_capture", "schema_version")
    obs_fields = (
        "game_id", "kalshi_market_ticker", "kalshi_event_ticker", "coverage_outcome", "coverage_reason",
        "parse_status", "pricing_status", "model_probability", "executable_yes_price", "executable_no_price",
        "research_probability_gap", "gross_probability_gap", "fee_adjusted_research_gap", "estimated_taker_fee",
        "fee_schedule_version", "fee_status", "fee_verification_status", "model_version", "training_cutoff",
        "snapshot_timing", "provenance", "family", "side", "team", "threshold", "market_midpoint", "uncertainty",
    )
    for i, (lrow, orow) in enumerate(zip(legacy, opt, strict=True)):
        for field in row_fields:
            assert lrow.get(field) == orow.get(field), f"row {i}: field {field!r} diverged"
        for field in obs_fields:
            assert lrow["observation"].get(field) == orow["observation"].get(field), (
                f"row {i} ({orow['observation'].get('kalshi_market_ticker')}): observation.{field} diverged"
            )
        assert lrow["data_versions"] == orow["data_versions"], f"row {i}: data_versions diverged"


def test_capture_state_log_is_identical(tmp_path, monkeypatch):
    out = _run_pair(tmp_path, monkeypatch, n_games=4)
    assert out["legacy"]["state"] == out["optimized"]["state"]


def test_append_result_counters_are_identical(tmp_path, monkeypatch):
    out = _run_pair(tmp_path, monkeypatch, n_games=4)
    legacy_result, opt_result = out["legacy"]["result"], out["optimized"]["result"]
    assert legacy_result.written == opt_result.written
    assert legacy_result.skipped_duplicate == opt_result.skipped_duplicate
    assert sorted(legacy_result.keys_written) == sorted(opt_result.keys_written)

    lr, orr = out["legacy"]["report"], out["optimized"]["report"]
    for field in (
        "markets_scanned", "supported_markets", "captures_due", "captures_written",
        "captures_skipped_already_present", "missed_windows", "mapping_failures", "stale_schedule_failures",
    ):
        assert getattr(lr, field) == getattr(orr, field), f"health report field {field!r} diverged"


def test_equivalence_holds_against_a_pre_existing_history(tmp_path, monkeypatch):
    """The interesting case, and the one this whole refactor is about: a
    corpus that ALREADY contains some of the rows this run would produce.

    Legacy learned "what has this ticker already captured?" by re-reading
    the entire file once per ticker; optimized learns it from the
    one-shot index. Both must therefore reach the same SCHEDULING
    decision -- the already-captured timing labels are suppressed by
    `timing.resolve_due_labels`, so those rows are never generated at all
    (which is why this shows up as fewer rows WRITTEN, not as
    append-level duplicates; duplicate REJECTION is defence-in-depth
    behind this and is covered directly in
    tests/test_research_scan_persistence.py)."""
    baseline = _run_pair(tmp_path / "seed", monkeypatch, n_games=4)
    seed_rows = [
        json.loads(line)
        for line in (
            (tmp_path / "seed" / "optimized" / "data" / "research" / "observations" / f"{SEASON}.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        if line.strip()
    ]
    assert len(seed_rows) > 0

    partial = seed_rows[: len(seed_rows) // 2]
    out = _run_pair(tmp_path / "rerun", monkeypatch, n_games=4, seed_rows=partial)

    # Same output, and the seeded history genuinely changed the decision.
    assert out["legacy"]["rows"] == out["optimized"]["rows"]
    assert out["legacy"]["result"].written == out["optimized"]["result"].written
    assert out["legacy"]["result"].skipped_duplicate == out["optimized"]["result"].skipped_duplicate
    assert out["optimized"]["result"].written < baseline["optimized"]["result"].written, (
        "seeding half the corpus did not suppress any capture -- the history was not consulted"
    )

    # And the pre-existing rows are still present, untouched, exactly once.
    final_keys = [r["observation_key"] for r in out["optimized"]["rows"]]
    assert len(final_keys) == len(set(final_keys)), "duplicate canonical key in the final corpus"
    for seeded in partial:
        assert seeded["observation_key"] in final_keys
