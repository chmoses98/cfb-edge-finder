"""The UTC-date shard layout that replaced the season monolith.

Guards the four properties the GH001 repair rests on:

  1. a row's shard is a PURE FUNCTION of the row (determinism),
  2. dedup stays GLOBAL across shards (sharding must not be able to
     duplicate an observation),
  3. no shard can grow past the target size (rollover),
  4. a legacy `{season}.jsonl` is still readable during the transition.
"""

from __future__ import annotations

import json
from pathlib import Path

import corpus_helpers
import pytest
from research_factories import make_corpus_row, make_observation

from cfb_edge_finder.research import persistence, shards

SEASON = 2026


def _obs_row(key: str, captured_at: str) -> dict:
    return {
        "observation_key": key,
        "season": SEASON,
        "observation": {
            "captured_at": captured_at,
            "game_id": "g1",
            "kalshi_market_ticker": "MKT-1",
            "snapshot_timing": {"label": "T_60"},
        },
    }


# --- 1. determinism -------------------------------------------------------


def test_shard_date_is_a_pure_function_of_the_row():
    row = _obs_row("k", "2026-09-12T23:45:00+00:00")
    first = shards.shard_date_for(row, shards.OBSERVATIONS_SUBDIR)
    assert first == "2026-09-12"
    # Same row, any number of times, any order -- always the same answer.
    assert all(shards.shard_date_for(dict(row), shards.OBSERVATIONS_SUBDIR) == first for _ in range(5))


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("2026-09-12T23:45:00+00:00", "2026-09-12"),
        ("2026-09-12T23:45:00Z", "2026-09-12"),
        ("2026-09-12T23:45:00", "2026-09-12"),  # naive is read as UTC, never as local
        ("2026-09-13T01:30:00+04:00", "2026-09-12"),  # converted to UTC, not truncated
        ("2026-09-12T20:30:00-05:00", "2026-09-13"),
    ],
)
def test_the_date_is_the_utc_date_not_the_string_prefix(stamp, expected):
    """A non-UTC offset must be CONVERTED. Truncating the string would
    put two rows captured at the same instant in different shards
    depending on how their producer formatted the timestamp."""
    assert shards.utc_date_of(stamp) == expected


def test_a_row_with_no_usable_timestamp_still_gets_a_home():
    """Never 'nowhere': an undatable row is evidence too, and migration
    must be able to carry it."""
    assert shards.shard_date_for({"observation": {}}, shards.OBSERVATIONS_SUBDIR) == shards.UNDATED_SHARD_DATE
    assert shards.shard_date_for({}, shards.SETTLEMENTS_SUBDIR) == shards.UNDATED_SHARD_DATE
    assert shards.utc_date_of("not a timestamp") is None


def test_every_sharded_family_declares_a_date_field():
    for family in shards.SHARDED_FAMILIES:
        assert shards.FAMILY_DATE_FIELDS[family], f"{family} has no date field"


# --- 2. global dedup ------------------------------------------------------


def test_the_same_key_captured_on_a_later_date_is_still_a_duplicate(tmp_path):
    """THE property sharding could most easily have broken.
    `observation_key` carries no timestamp, so the same logical
    observation re-derived tomorrow keys identically but dates
    differently. Per-shard dedup would write it twice."""
    base = tmp_path / "data" / "research"
    first = _obs_row("same-key", "2026-09-12T23:59:00+00:00")
    later = _obs_row("same-key", "2026-09-13T00:01:00+00:00")

    r1 = persistence.append_sharded_json_rows(
        base, shards.OBSERVATIONS_SUBDIR, SEASON, [first], persistence.observation_key_of
    )
    r2 = persistence.append_sharded_json_rows(
        base, shards.OBSERVATIONS_SUBDIR, SEASON, [later], persistence.observation_key_of
    )

    assert r1.written == 1
    assert r2.written == 0 and r2.skipped_duplicate == 1
    assert len(corpus_helpers.rows(base, shards.OBSERVATIONS_SUBDIR, SEASON)) == 1


def test_rows_land_in_their_own_date_shard(tmp_path):
    base = tmp_path / "data" / "research"
    rows = [
        _obs_row("a", "2026-09-12T10:00:00+00:00"),
        _obs_row("b", "2026-09-13T10:00:00+00:00"),
        _obs_row("c", "2026-09-12T22:00:00+00:00"),
    ]
    persistence.append_sharded_json_rows(
        base, shards.OBSERVATIONS_SUBDIR, SEASON, rows, persistence.observation_key_of
    )
    names = [p.name for p in shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON)]
    assert names == ["2026-09-12.part001.jsonl", "2026-09-13.part001.jsonl"]
    assert len(corpus_helpers.rows(base, shards.OBSERVATIONS_SUBDIR, SEASON)) == 3


def test_capture_state_dedup_also_spans_shards(tmp_path):
    """Its key is (game, ticker, label, state) with no date either, so a
    checkpoint that reached CAPTURED last week must not re-append today
    merely because today is a different shard."""
    from datetime import UTC, datetime

    from cfb_edge_finder.schemas.capture_state import CaptureState, CaptureStateRecord

    base = tmp_path / "data" / "research"
    monday = CaptureStateRecord(
        game_id="g1", kalshi_market_ticker="MKT-1", timing_label="T_60",
        state=CaptureState.CAPTURED, observed_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
    )
    tuesday = CaptureStateRecord(
        game_id="g1", kalshi_market_ticker="MKT-1", timing_label="T_60",
        state=CaptureState.CAPTURED, observed_at=datetime(2026, 9, 13, 12, tzinfo=UTC),
    )
    assert persistence.append_capture_state_rows(base, SEASON, [monday]).written == 1
    second = persistence.append_capture_state_rows(base, SEASON, [tuesday])
    assert second.written == 0 and second.skipped_duplicate == 1


def test_the_index_sees_rows_from_every_shard(tmp_path):
    base = tmp_path / "data" / "research"
    rows = [_obs_row(f"k{i}", f"2026-09-{10 + i:02d}T10:00:00+00:00") for i in range(4)]
    persistence.append_sharded_json_rows(
        base, shards.OBSERVATIONS_SUBDIR, SEASON, rows, persistence.observation_key_of
    )
    index = persistence.load_observation_index_for(base, SEASON)
    assert index.row_count == 4
    assert index.keys == {"k0", "k1", "k2", "k3"}
    assert index.load_count == 1, "history must still be derived once per run"


# --- 3. rollover ----------------------------------------------------------


def test_a_shard_rolls_over_before_passing_the_target_size(tmp_path):
    base = tmp_path / "data" / "research"
    line = "x" * 1000
    target = 4096
    shards.append_lines(
        base,
        shards.OBSERVATIONS_SUBDIR,
        SEASON,
        [("2026-09-12", line) for _ in range(12)],
        target_bytes=target,
    )
    written = shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    assert len(written) > 1, "no rollover happened"
    assert all(p.stat().st_size <= target for p in written)
    assert [p.name for p in written] == sorted(p.name for p in written), "parts out of order"
    # Nothing lost.
    assert sum(1 for _p, _l in shards.iter_raw_lines(written)) == 12


def test_rollover_never_reopens_an_earlier_part(tmp_path):
    """An already-pushed shard must stay byte-stable: a later append
    starts a new part rather than topping up a full one."""
    base = tmp_path / "data" / "research"
    target = 4096
    for _ in range(12):
        shards.append_lines(
            base, shards.OBSERVATIONS_SUBDIR, SEASON, [("2026-09-12", "x" * 1000)], target_bytes=target
        )
    parts = shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    first_part = parts[0]
    before = first_part.read_bytes()
    shards.append_lines(
        base, shards.OBSERVATIONS_SUBDIR, SEASON, [("2026-09-12", "x" * 1000)], target_bytes=target
    )
    assert first_part.read_bytes() == before


def test_a_single_oversize_row_is_written_rather_than_dropped(tmp_path):
    """Losing a row to a size policy would be far worse than one
    oversize shard -- the guard reports it instead."""
    base = tmp_path / "data" / "research"
    huge = "y" * 5000
    shards.append_lines(
        base, shards.OBSERVATIONS_SUBDIR, SEASON, [("2026-09-12", huge)], target_bytes=1000
    )
    assert corpus_helpers.lines(base, shards.OBSERVATIONS_SUBDIR, SEASON) == [huge]


def test_the_production_target_is_comfortably_under_githubs_limits():
    """45 MB is below GitHub's 50 MB advisory warning; the push refusal
    is below its 100 MiB (104,857,600 B) hard limit."""
    assert shards.SHARD_TARGET_BYTES <= 50_000_000
    assert shards.PUSH_REFUSAL_BYTES < 100 * 1024 * 1024
    assert shards.SHARD_TARGET_BYTES < shards.PUSH_REFUSAL_BYTES


# --- the size guard -------------------------------------------------------


def test_oversize_blobs_reports_offenders_largest_first(tmp_path):
    small = tmp_path / "small.jsonl"
    big = tmp_path / "big.jsonl"
    bigger = tmp_path / "bigger.jsonl"
    small.write_bytes(b"a" * 10)
    big.write_bytes(b"a" * 500)
    bigger.write_bytes(b"a" * 900)
    found = shards.oversize_blobs(tmp_path, limit_bytes=100)
    assert [p.name for p, _ in found] == ["bigger.jsonl", "big.jsonl"]


def test_oversize_blobs_can_be_restricted_to_specific_paths(tmp_path):
    """What the durable store needs: an UNCHANGED oversize blob already
    on the remote creates no new object and must not block a push."""
    untouched = tmp_path / "untouched.jsonl"
    changed = tmp_path / "changed.jsonl"
    untouched.write_bytes(b"a" * 900)
    changed.write_bytes(b"a" * 50)
    assert shards.oversize_blobs(tmp_path, limit_bytes=100, only=[changed]) == []
    assert len(shards.oversize_blobs(tmp_path, limit_bytes=100)) == 1


# --- 4. legacy monolith reads --------------------------------------------


def test_a_legacy_monolith_is_still_read(tmp_path):
    """The transition guarantee: code on this branch must read a corpus
    that has not been migrated yet, with no flag and no second path."""
    base = tmp_path / "data" / "research"
    legacy = shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(_obs_row("old", "2026-09-01T10:00:00+00:00")) + "\n")

    index = persistence.load_observation_index_for(base, SEASON)
    assert index.keys == {"old"}


def test_the_legacy_monolith_is_read_before_the_shards(tmp_path):
    base = tmp_path / "data" / "research"
    legacy = shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(_obs_row("old", "2026-09-01T10:00:00+00:00")) + "\n")
    persistence.append_sharded_json_rows(
        base,
        shards.OBSERVATIONS_SUBDIR,
        SEASON,
        [_obs_row("new", "2026-09-12T10:00:00+00:00")],
        persistence.observation_key_of,
    )
    keys = [r["observation_key"] for r in corpus_helpers.rows(base, shards.OBSERVATIONS_SUBDIR, SEASON)]
    assert keys == ["old", "new"], "oldest rows must still come first"


def test_a_key_already_in_the_legacy_monolith_is_not_rewritten_into_a_shard(tmp_path):
    """Dedup must span the legacy file too, or the first post-migration
    run would duplicate everything the monolith already holds."""
    base = tmp_path / "data" / "research"
    row = _obs_row("dupe", "2026-09-01T10:00:00+00:00")
    legacy = shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(row) + "\n")

    result = persistence.append_sharded_json_rows(
        base, shards.OBSERVATIONS_SUBDIR, SEASON, [row], persistence.observation_key_of
    )
    assert result.written == 0 and result.skipped_duplicate == 1
    assert shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON) == []


def test_writes_never_go_to_the_legacy_monolith(tmp_path):
    base = tmp_path / "data" / "research"
    legacy = shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(_obs_row("old", "2026-09-01T10:00:00+00:00")) + "\n")
    before = legacy.read_bytes()

    persistence.append_observation_rows(
        base, SEASON, [make_corpus_row(observation=make_observation(kalshi_market_ticker="MKT-NEW"))]
    )
    assert legacy.read_bytes() == before, "the legacy monolith was written to"
    assert shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON), "no shard was written"


# --- path shapes ----------------------------------------------------------


def test_shard_and_monolith_paths_cannot_collide():
    base = Path("/tmp/base")
    monolith = shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    directory = shards.shard_dir(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    assert monolith != directory
    assert monolith.parent == directory.parent


def test_shard_names_round_trip():
    for part in (1, 2, 37, 999, 1000):
        name = shards.shard_name("2026-09-12", part)
        assert shards.parse_shard_name(name) == ("2026-09-12", part)
    assert shards.parse_shard_name("2026.jsonl") is None
    assert shards.parse_shard_name("2026-09-12.jsonl") is None
    assert shards.parse_shard_name("notes.txt") is None


def test_as_source_paths_accepts_a_file_a_directory_or_a_list(tmp_path):
    base = tmp_path / "data" / "research"
    persistence.append_sharded_json_rows(
        base,
        shards.OBSERVATIONS_SUBDIR,
        SEASON,
        [_obs_row("a", "2026-09-12T10:00:00+00:00")],
        persistence.observation_key_of,
    )
    directory = shards.shard_dir(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    only = shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON)[0]

    assert shards.as_source_paths(directory) == [only]
    assert shards.as_source_paths(only) == [only]
    assert shards.as_source_paths([only, only]) == [only], "duplicates must collapse"
    assert shards.as_source_paths(tmp_path / "missing") == []
    assert shards.as_source_paths(None) == []
