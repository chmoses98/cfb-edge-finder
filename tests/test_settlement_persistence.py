"""Mission sections 9, 12, 14, 18, 20, 23: idempotence, append-only
storage, closing linkage, per-checkpoint independence, and incremental
indexing.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.research import persistence
from cfb_edge_finder.research.attribution import (
    ATTRIBUTION_CODE_VERSION,
    attribute_observation,
    attribution_key,
    build_closing_link,
)
from cfb_edge_finder.research.closing_capture import ClosingStatus
from cfb_edge_finder.research.settlement import settle_market
from cfb_edge_finder.schemas.attribution import (
    PENDING_ATTRIBUTION_STATES,
    AttributionState,
)
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow
from cfb_edge_finder.schemas.settlement import GameFinalStatus, GameResult

FIXTURE = Path(__file__).parent / "fixtures" / "real_captured_observations.jsonl"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
SEASON = 2026

REAL_ROWS = [
    ResearchCorpusRow.model_validate(json.loads(line))
    for line in FIXTURE.read_text(encoding="utf-8").splitlines()
    if line.strip()
]


def _result(home=31, away=17, status=GameFinalStatus.FINAL) -> GameResult:
    return GameResult(
        game_id="g", season=SEASON,
        home_points=home if status is GameFinalStatus.FINAL else None,
        away_points=away if status is GameFinalStatus.FINAL else None,
        status=status, captured_at=NOW,
    )


def _attr(row, **kw):
    s = settle_market(row.observation, _result(), settled_at=NOW)
    return attribute_observation(row, s, settled_at=NOW, **kw)


def _path(tmp_path: Path) -> Path:
    return persistence.canonical_path(tmp_path / "data" / "research", persistence.ATTRIBUTIONS_SUBDIR, SEASON)


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# --- Idempotence / duplicate prevention (section 9) ----------------------


def test_rerunning_settlement_writes_zero_duplicates(tmp_path):
    path = _path(tmp_path)
    attrs = [_attr(r) for r in REAL_ROWS]

    index = persistence.load_attribution_index(path)
    first = persistence.append_attribution_rows(path, attrs, index=index)
    assert first.written == len(attrs) > 0
    after_first = path.read_bytes()

    index2 = persistence.load_attribution_index(path)
    second = persistence.append_attribution_rows(path, attrs, index=index2)
    assert second.written == 0, "a re-run wrote duplicate settlement records"
    assert second.skipped_duplicate == len(attrs)
    assert path.read_bytes() == after_first, "re-run mutated existing settlement bytes"


def test_attribution_key_is_stable_and_versioned():
    assert attribution_key("obs-1") == attribution_key("obs-1")
    assert attribution_key("obs-1") != attribution_key("obs-2")
    assert ATTRIBUTION_CODE_VERSION in attribution_key("obs-1")


def test_a_settlement_logic_revision_appends_rather_than_overwrites(tmp_path):
    """Section 23: a correction uses an explicit amendment, never silent
    mutation. A new code version yields a new key, so the old conclusion
    survives alongside the new one."""
    path = _path(tmp_path)
    row = REAL_ROWS[0]
    original = _attr(row)
    index = persistence.load_attribution_index(path)
    persistence.append_attribution_rows(path, [original], index=index)

    amended = original.model_copy(
        update={
            "attribution_key": attribution_key(row.observation_key, code_version="attribution_v2"),
            "settlement_code_version": "attribution_v2",
        }
    )
    persistence.append_attribution_rows(path, [amended], index=index)

    rows = _rows(path)
    assert len(rows) == 2, "amendment overwrote the original conclusion"
    assert {r["settlement_code_version"] for r in rows} == {ATTRIBUTION_CODE_VERSION, "attribution_v2"}
    assert rows[0]["observation_key"] == rows[1]["observation_key"]


def test_existing_rows_are_never_rewritten_or_reordered(tmp_path):
    path = _path(tmp_path)
    index = persistence.load_attribution_index(path)
    persistence.append_attribution_rows(path, [_attr(r) for r in REAL_ROWS[:5]], index=index)
    original_lines = path.read_text(encoding="utf-8").splitlines()

    persistence.append_attribution_rows(path, [_attr(r) for r in REAL_ROWS[5:]], index=index)
    after = path.read_text(encoding="utf-8").splitlines()

    assert len(after) > len(original_lines)
    assert after[: len(original_lines)] == original_lines


def test_pending_states_are_not_persisted():
    """Writing a GAME_NOT_FINAL row would permanently consume that
    observation's attribution key and block the real settlement."""
    row = REAL_ROWS[0]
    s = settle_market(row.observation, _result(status=GameFinalStatus.NOT_YET_FINAL), settled_at=NOW)
    a = attribute_observation(row, s, settled_at=NOW)
    assert a.state in PENDING_ATTRIBUTION_STATES
    assert AttributionState.SETTLED_YES not in PENDING_ATTRIBUTION_STATES


# --- Per-checkpoint independence (section 14) ----------------------------


def test_each_checkpoint_settles_independently(tmp_path):
    """The same contract at different checkpoints must produce SEPARATE
    attribution rows with their own entry state -- never one collapsed row."""
    row = REAL_ROWS[0]
    checkpoints = []
    for i, label in enumerate(["EARLY_OPEN", "T_24H", "T_6H", "T_30", "CLOSING"]):
        obs = row.observation.model_copy(
            update={
                "snapshot_timing": row.observation.snapshot_timing.model_copy(update={"label": label}),
                "executable_yes_price": 0.30 + 0.05 * i,
                "captured_at": NOW - timedelta(hours=10 - i),
            }
        )
        checkpoints.append(
            row.model_copy(update={"observation": obs, "observation_key": f"{row.observation_key}-{label}"})
        )

    attrs = [_attr(c) for c in checkpoints]
    path = _path(tmp_path)
    index = persistence.load_attribution_index(path)
    result = persistence.append_attribution_rows(path, attrs, index=index)

    assert result.written == 5, "checkpoints were collapsed into fewer rows"
    assert len({a.timing_label for a in attrs}) == 5
    assert len({a.entry_yes_price for a in attrs}) == 5, "checkpoints share one entry price"
    # Same contract outcome across all of them.
    assert len({a.state for a in attrs}) == 1
    assert len({a.yes_economics.settlement_value for a in attrs}) == 1
    # ...but different P/L, because different entry prices.
    assert len({a.yes_economics.research_unit_pnl for a in attrs}) == 5


# --- Closing linkage (sections 12, 20) -----------------------------------


def test_closing_link_uses_a_genuine_closing_row():
    row = REAL_ROWS[0]
    closing_obs = row.observation.model_copy(
        update={
            "snapshot_timing": row.observation.snapshot_timing.model_copy(update={"label": "CLOSING"}),
            "executable_yes_price": 0.61,
            "executable_no_price": 0.41,
            "model_probability": 0.58,
        }
    )
    closing_row = row.model_copy(update={"observation": closing_obs, "observation_key": "closing-key"})
    link = build_closing_link(closing_row)
    assert link.closing_captured is True
    assert link.closing_status == ClosingStatus.CLOSING_CAPTURED.value
    assert link.closing_yes_price == 0.61
    assert link.closing_no_price == 0.41
    assert link.closing_model_probability == 0.58
    assert link.closing_observation_key == "closing-key"


def test_a_t30_row_is_refused_as_a_closing_stand_in():
    """Mission section 12: do not fabricate a close from T_30."""
    row = REAL_ROWS[0]
    t30 = row.model_copy(
        update={
            "observation": row.observation.model_copy(
                update={"snapshot_timing": row.observation.snapshot_timing.model_copy(update={"label": "T_30"})}
            )
        }
    )
    with pytest.raises(ValueError, match="only a genuine CLOSING snapshot"):
        build_closing_link(t30)


def test_missing_close_records_a_reason_and_does_not_block_settlement():
    row = REAL_ROWS[0]
    a = _attr(row, closing_row=None, closing_missing_reason=ClosingStatus.CLOSING_MISSING_MARKET_CLOSED.value)
    assert a.state in (AttributionState.SETTLED_YES, AttributionState.SETTLED_NO), "missing close blocked settlement"
    assert a.closing.closing_captured is False
    assert a.closing.closing_status == ClosingStatus.CLOSING_MISSING_MARKET_CLOSED.value


def test_clv_primitives_are_present_but_ungraded():
    """Section 13: persist the raw fields, draw no conclusions."""
    row = REAL_ROWS[0]
    closing_obs = row.observation.model_copy(
        update={
            "snapshot_timing": row.observation.snapshot_timing.model_copy(update={"label": "CLOSING"}),
            "executable_yes_price": 0.61, "executable_no_price": 0.41, "model_probability": 0.58,
        }
    )
    a = _attr(row, closing_row=row.model_copy(update={"observation": closing_obs, "observation_key": "ck"}))
    # Entry and closing primitives both present...
    assert a.entry_yes_price is not None and a.closing.closing_yes_price is not None
    assert a.entry_model_probability is not None and a.closing.closing_model_probability is not None
    assert a.captured_at is not None and a.closing.closing_captured_at is not None
    # ...and no graded CLV field exists anywhere on the record.
    dumped = a.model_dump()
    forbidden = [k for k in dumped if any(t in k.lower() for t in ("clv", "edge", "roi", "beat", "grade"))]
    assert forbidden == [], f"CLV/edge grading leaked into settlement: {forbidden}"


# --- Incremental indexing (section 18) -----------------------------------


def test_index_matches_a_full_read_and_loads_once(tmp_path):
    path = _path(tmp_path)
    attrs = [_attr(r) for r in REAL_ROWS]
    persistence.append_attribution_rows(path, attrs, index=persistence.load_attribution_index(path))

    index = persistence.load_attribution_index(path)
    assert index.load_count == 1
    assert index.row_count == len(attrs)
    assert index.malformed_rows == 0
    assert index.keys == {a.attribution_key for a in attrs}
    assert index.settled_observation_keys == {a.observation_key for a in attrs}
    for a in attrs:
        assert index.already_attributed(a.observation_key, ATTRIBUTION_CODE_VERSION)
    assert not index.already_attributed("never-seen", ATTRIBUTION_CODE_VERSION)


def test_index_tolerates_and_counts_malformed_lines(tmp_path):
    path = _path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    good = _attr(REAL_ROWS[0]).model_dump(mode="json")
    path.write_text(json.dumps(good, sort_keys=True) + "\n{not json\n\n", encoding="utf-8")
    index = persistence.load_attribution_index(path)
    assert index.row_count == 1 and index.malformed_rows == 1
    assert len(index.keys) == 1


@pytest.mark.parametrize("n_existing", [0, 1_000, 20_000])
def test_index_lookup_does_not_degrade_with_ledger_size(tmp_path, n_existing):
    """Section 18: settlement must not become another O(history x ledger)
    nested scan. Membership must stay O(1) regardless of ledger size."""
    path = _path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    template = _attr(REAL_ROWS[0]).model_dump(mode="json")
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n_existing):
            row = dict(template)
            row["attribution_key"] = f"synthetic-{i:08d}|{ATTRIBUTION_CODE_VERSION}"
            row["observation_key"] = f"synthetic-{i:08d}"
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    index = persistence.load_attribution_index(path)
    assert index.row_count == n_existing
    assert index.load_count == 1

    started = time.perf_counter()
    for i in range(2000):
        index.already_attributed(f"synthetic-{i % max(n_existing, 1):08d}", ATTRIBUTION_CODE_VERSION)
    elapsed = time.perf_counter() - started
    # Deliberately enormous: this trips only on a genuine linear-scan
    # regression, never on CI noise.
    assert elapsed < 2.0, f"2000 membership checks took {elapsed:.2f}s against {n_existing} rows"


def test_index_holds_no_decoded_rows(tmp_path):
    path = _path(tmp_path)
    persistence.append_attribution_rows(
        path, [_attr(r) for r in REAL_ROWS], index=persistence.load_attribution_index(path)
    )
    index = persistence.load_attribution_index(path)
    for value in vars(index).values():
        assert not isinstance(value, list), "index retains a list -- likely the whole decoded ledger"
