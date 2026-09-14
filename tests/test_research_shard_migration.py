"""Migration equivalence: the season monolith becomes date shards with
provably identical content.

The migration relocates rows the collector already captured. It is not
allowed to regenerate, re-serialise, re-order, re-validate, semantically
rewrite, backfill, sample, compact, or drop anything -- so the proof that
matters is BYTE equality of the line multiset, plus equality of the
dedup-key multiset (zero missing, zero duplicate), plus within-shard
order preservation.

These tests exercise the real `scripts/migrate_research_shards.py`, not a
reimplementation of it, and they check both directions: a good migration
must be accepted AND a corrupted one must be rejected with the monolith
left exactly where it was.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import migrate_research_shards as migrate  # noqa: E402

from cfb_edge_finder.research import persistence, shards  # noqa: E402

SEASON = 2026


def _obs(key: str, captured_at: str) -> dict:
    return {
        "observation_key": key,
        "season": SEASON,
        "schema_version": "research_corpus_row_v2",
        "observation": {
            "captured_at": captured_at,
            "game_id": f"game-{key}",
            "kalshi_market_ticker": f"MKT-{key}",
            "snapshot_timing": {"label": "T_60"},
        },
    }


def _write_monolith(base: Path, subdir: str, rows: list[dict]) -> Path:
    path = shards.legacy_monolith_path(base, subdir, SEASON)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return path


def _spread_rows(n: int, days: int = 5) -> list[dict]:
    return [_obs(f"k{i:05d}", f"2026-09-{10 + (i % days):02d}T{i % 24:02d}:30:00+00:00") for i in range(n)]


# --- the equivalence proof ------------------------------------------------


def test_every_row_survives_byte_for_byte(tmp_path):
    base = tmp_path / "data" / "research"
    rows = _spread_rows(400)
    legacy = _write_monolith(base, shards.OBSERVATIONS_SUBDIR, rows)
    before_lines = legacy.read_text(encoding="utf-8").splitlines()

    report = migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)

    assert report["status"] == "MIGRATED"
    assert report["rows_in"] == report["rows_out"] == len(rows)
    assert report["duplicate_keys"] == 0
    assert report["missing_keys"] == 0
    assert report["gained_keys"] == 0
    assert report["byte_identical"] is True

    after_lines = [
        line
        for _p, line in shards.iter_raw_lines(
            shards.source_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON)
        )
    ]
    assert sorted(after_lines) == sorted(before_lines), "a line changed bytes during migration"


def test_keys_are_neither_lost_nor_duplicated(tmp_path):
    base = tmp_path / "data" / "research"
    rows = _spread_rows(250)
    _write_monolith(base, shards.OBSERVATIONS_SUBDIR, rows)

    migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)

    keys = [
        json.loads(line)["observation_key"]
        for _p, line in shards.iter_raw_lines(shards.source_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON))
    ]
    assert len(keys) == len(rows)
    assert len(set(keys)) == len(rows), "a key gained a second row"
    assert set(keys) == {r["observation_key"] for r in rows}, "a key went missing"


def test_rows_land_in_the_shard_their_own_timestamp_names(tmp_path):
    base = tmp_path / "data" / "research"
    rows = _spread_rows(100, days=4)
    _write_monolith(base, shards.OBSERVATIONS_SUBDIR, rows)
    migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)

    for path in shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON):
        date, _part = shards.parse_shard_name(path.name)
        for _p, line in shards.iter_raw_lines(path):
            row = json.loads(line)
            assert shards.shard_date_for(row, shards.OBSERVATIONS_SUBDIR) == date


def test_original_order_is_preserved_within_each_shard(tmp_path):
    base = tmp_path / "data" / "research"
    rows = _spread_rows(200)
    legacy = _write_monolith(base, shards.OBSERVATIONS_SUBDIR, rows)
    original = legacy.read_text(encoding="utf-8").splitlines()
    position = {line: i for i, line in enumerate(original)}

    report = migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    assert report["order_violations"] == 0

    for path in shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON):
        seen = [position[line] for _p, line in shards.iter_raw_lines(path)]
        assert seen == sorted(seen), f"{path.name} reordered rows"


def test_the_monolith_is_removed_only_after_the_proof_passes(tmp_path):
    base = tmp_path / "data" / "research"
    legacy = _write_monolith(base, shards.OBSERVATIONS_SUBDIR, _spread_rows(30))
    assert legacy.is_file()
    migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    assert not legacy.exists()
    assert shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON)


def test_a_failed_proof_leaves_the_monolith_untouched(tmp_path, monkeypatch):
    """The safety property: if equivalence cannot be shown, nothing is
    deleted and the operator still has the original file."""
    base = tmp_path / "data" / "research"
    legacy = _write_monolith(base, shards.OBSERVATIONS_SUBDIR, _spread_rows(20))
    before = legacy.read_bytes()

    real_append = shards.append_lines

    def lossy(base_dir, subdir, season, dated_lines, **kwargs):
        return real_append(base_dir, subdir, season, list(dated_lines)[:-1], **kwargs)

    monkeypatch.setattr(shards, "append_lines", lossy)
    with pytest.raises(migrate.MigrationError) as exc:
        migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)

    assert "row count" in str(exc.value)
    assert legacy.read_bytes() == before, "a failed migration deleted the monolith"


def test_migration_refuses_to_run_into_an_already_populated_shard_set(tmp_path):
    """Merging a monolith into existing shards could duplicate rows, so
    it is refused rather than guessed at."""
    base = tmp_path / "data" / "research"
    _write_monolith(base, shards.OBSERVATIONS_SUBDIR, _spread_rows(5))
    shards.append_lines(
        base, shards.OBSERVATIONS_SUBDIR, SEASON, [("2026-09-10", json.dumps(_obs("x", "2026-09-10T00:00:00+00:00")))]
    )
    with pytest.raises(migrate.MigrationError, match="already exist"):
        migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)


def test_migrating_a_missing_family_is_a_no_op(tmp_path):
    base = tmp_path / "data" / "research"
    base.mkdir(parents=True)
    report = migrate.migrate_family(base, shards.SETTLEMENTS_SUBDIR, SEASON)
    assert report["status"] == "NOTHING_TO_MIGRATE"
    assert report["rows_in"] == 0


def test_an_undatable_row_is_carried_not_dropped(tmp_path):
    base = tmp_path / "data" / "research"
    rows = [_obs("good", "2026-09-12T10:00:00+00:00")]
    legacy = _write_monolith(base, shards.OBSERVATIONS_SUBDIR, rows)
    with legacy.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"observation_key": "undated"}, sort_keys=True) + "\n")

    report = migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    assert report["status"] == "MIGRATED"
    assert report["rows_out"] == 2
    names = [p.name for p in shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON)]
    assert shards.shard_name(shards.UNDATED_SHARD_DATE, 1) in names


def test_a_malformed_line_is_carried_not_dropped(tmp_path):
    """A line this code cannot decode is still somebody's evidence."""
    base = tmp_path / "data" / "research"
    legacy = _write_monolith(base, shards.OBSERVATIONS_SUBDIR, [_obs("good", "2026-09-12T10:00:00+00:00")])
    with legacy.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all\n")

    report = migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    assert report["status"] == "MIGRATED"
    assert report["rows_out"] == 2
    assert report["malformed_rows"] == 1
    body = [line for _p, line in shards.iter_raw_lines(shards.source_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON))]
    assert "{not json at all" in body


def test_no_shard_exceeds_the_target_and_rollover_still_preserves_everything(tmp_path):
    base = tmp_path / "data" / "research"
    # All on ONE date, so the only way to stay under the target is a rollover.
    rows = [_obs(f"k{i:05d}", "2026-09-12T10:00:00+00:00") for i in range(500)]
    _write_monolith(base, shards.OBSERVATIONS_SUBDIR, rows)

    report = migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON, target_bytes=20_000)
    assert report["status"] == "MIGRATED"
    assert report["shards_written"] > 1, "expected a rollover"
    assert report["largest_shard_bytes"] <= 20_000
    assert report["rows_out"] == 500
    assert report["duplicate_keys"] == 0


# --- after migration, the corpus behaves exactly as before ---------------


def test_a_migrated_corpus_still_dedups_new_captures(tmp_path):
    """The point of the whole exercise: prospective dedup must be
    unchanged by where the rows now live."""
    base = tmp_path / "data" / "research"
    rows = _spread_rows(60)
    _write_monolith(base, shards.OBSERVATIONS_SUBDIR, rows)
    migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)

    replay = persistence.append_sharded_json_rows(
        base, shards.OBSERVATIONS_SUBDIR, SEASON, rows, persistence.observation_key_of
    )
    assert replay.written == 0
    assert replay.skipped_duplicate == len(rows)

    genuinely_new = [_obs("brand-new", "2026-09-14T10:00:00+00:00")]
    fresh = persistence.append_sharded_json_rows(
        base, shards.OBSERVATIONS_SUBDIR, SEASON, genuinely_new, persistence.observation_key_of
    )
    assert fresh.written == 1


def test_a_migrated_corpus_loads_with_the_same_row_and_key_count(tmp_path):
    base = tmp_path / "data" / "research"
    rows = _spread_rows(120)
    _write_monolith(base, shards.OBSERVATIONS_SUBDIR, rows)
    before = persistence.load_observation_index_for(base, SEASON)

    migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    after = persistence.load_observation_index_for(base, SEASON)

    assert after.row_count == before.row_count == len(rows)
    assert after.keys == before.keys
    assert after.labels_by_ticker == before.labels_by_ticker
    assert after.malformed_rows == before.malformed_rows == 0


# --- every family, end to end --------------------------------------------


FAMILY_FIXTURES = {
    shards.OBSERVATIONS_SUBDIR: lambda i: _obs(f"k{i}", f"2026-09-1{i % 3}T10:00:00+00:00"),
    shards.ATTRIBUTIONS_SUBDIR: lambda i: {
        "attribution_key": f"a{i}",
        "observation_key": f"k{i}",
        "captured_at": f"2026-09-1{i % 3}T10:00:00+00:00",
    },
    shards.SHADOW_SUBDIR: lambda i: {
        "shadow_key": f"s{i}",
        "captured_at": f"2026-09-1{i % 3}T10:00:00+00:00",
    },
    shards.V2_SHADOW_SUBDIR: lambda i: {
        "observation_key": f"k{i}",
        "v2_model_version": "0.6.0-v2-shadow",
        "captured_at": f"2026-09-1{i % 3}T10:00:00+00:00",
    },
    shards.CAPTURE_STATE_SUBDIR: lambda i: {
        "game_id": f"g{i}",
        "kalshi_market_ticker": f"MKT-{i}",
        "timing_label": "T_60",
        "state": "CAPTURED",
        "observed_at": f"2026-09-1{i % 3}T10:00:00+00:00",
    },
    shards.SETTLEMENTS_SUBDIR: lambda i: {
        "game_id": f"g{i}",
        "kalshi_market_ticker": f"MKT-{i}",
        "status": "settled",
        "derived_contract_settlement": "YES",
        "official_kalshi_settlement": "YES",
        "settled_at": f"2026-09-1{i % 3}T10:00:00+00:00",
    },
    shards.HEARTBEATS_SUBDIR: lambda i: {
        "run_id": f"r{i}",
        "invoked_at": f"2026-09-1{i % 3}T10:00:00+00:00",
        "succeeded": True,
    },
}


@pytest.mark.parametrize("family", sorted(FAMILY_FIXTURES))
def test_each_family_migrates_with_full_equivalence(tmp_path, family):
    base = tmp_path / "data" / "research"
    rows = [FAMILY_FIXTURES[family](i) for i in range(60)]
    legacy = _write_monolith(base, family, rows)
    before = legacy.read_text(encoding="utf-8").splitlines()

    report = migrate.migrate_family(base, family, SEASON)

    assert report["status"] == "MIGRATED", report.get("failures")
    assert report["rows_in"] == report["rows_out"] == len(rows)
    assert report["duplicate_keys"] == 0
    assert report["missing_keys"] == 0
    after = [line for _p, line in shards.iter_raw_lines(shards.source_paths(base, family, SEASON))]
    assert sorted(after) == sorted(before)


def test_the_migration_dedup_key_is_the_one_production_enforces():
    """If these two ever drift, the proof checks a different notion of
    'duplicate' than the append path actually prevents."""
    for family in migrate.KEYED_FAMILIES:
        assert persistence.dedup_key_fn_for(family) is not None


def test_a_settlement_with_a_null_outcome_is_still_keyed():
    """The bug the first dry run against the real corpus exposed.

    Production's settlement fingerprint STRINGIFIES the outcome fields,
    so a row whose `official_kalshi_settlement` is null still has a key.
    An earlier migration draft restated the rule and required every field
    to be non-null, which made all 9,544 real settlement rows 'keyless'
    and the duplicate/missing check vacuous for that whole family."""
    settlement = {
        "game_id": "g1",
        "kalshi_market_ticker": "MKT-1",
        "status": "pending_not_final",
        "derived_contract_settlement": None,
        "official_kalshi_settlement": None,
    }
    key = migrate.dedup_key_of(settlement, shards.SETTLEMENTS_SUBDIR)
    assert key is not None
    assert key == persistence.settlement_fact_key(settlement)


def test_a_family_of_null_outcome_settlements_is_proved_not_skipped(tmp_path):
    """End to end: the migration report must count these rows as keyed,
    which is what makes its zero-duplicate/zero-missing claim mean
    something for settlements."""
    base = tmp_path / "data" / "research"
    rows = [
        {
            "game_id": f"g{i}",
            "kalshi_market_ticker": f"MKT-{i}",
            "status": "pending_not_final",
            "derived_contract_settlement": None,
            "official_kalshi_settlement": None,
            "settled_at": f"2026-09-1{i % 3}T10:00:00+00:00",
        }
        for i in range(30)
    ]
    _write_monolith(base, shards.SETTLEMENTS_SUBDIR, rows)
    report = migrate.migrate_family(base, shards.SETTLEMENTS_SUBDIR, SEASON)

    assert report["status"] == "MIGRATED"
    assert report["keyless_rows"] == 0, "settlement rows were not keyed by the proof"
    assert report["distinct_keys_out"] == len(rows)
    assert report["duplicate_keys"] == 0
    assert report["missing_keys"] == 0


def test_heartbeats_have_no_dedup_key_and_are_proved_by_count_and_bytes():
    """Deliberate: every invocation is its own row, so a key-based check
    would be meaningless there."""
    assert shards.HEARTBEATS_SUBDIR not in migrate.KEYED_FAMILIES
    assert migrate.dedup_key_of({"run_id": "r1"}, shards.HEARTBEATS_SUBDIR) is None


# --- the CLI --------------------------------------------------------------


def test_dry_run_changes_nothing_on_disk(tmp_path, capsys):
    repo = tmp_path / "repo"
    base = repo / "data" / "research"
    legacy = _write_monolith(base, shards.OBSERVATIONS_SUBDIR, _spread_rows(50))
    before = legacy.read_bytes()
    report_path = tmp_path / "report.json"

    exit_code = migrate.main_with_args(
        [
            "--data-repo-dir", str(repo),
            "--season", str(SEASON),
            "--families", shards.OBSERVATIONS_SUBDIR,
            "--dry-run",
            "--report", str(report_path),
        ]
    )
    assert exit_code == 0
    assert legacy.read_bytes() == before, "a dry run touched the real corpus"
    assert shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON) == []

    summary = json.loads(report_path.read_text())
    assert summary["mode"] == "dry-run"
    assert summary["totals"]["rows_in"] == summary["totals"]["rows_out"] == 50
    assert summary["totals"]["duplicate_keys"] == 0
    assert summary["totals"]["missing_keys"] == 0


def test_apply_migrates_every_family_and_reports(tmp_path):
    repo = tmp_path / "repo"
    base = repo / "data" / "research"
    for family, make in FAMILY_FIXTURES.items():
        _write_monolith(base, family, [make(i) for i in range(20)])
    report_path = tmp_path / "report.json"

    exit_code = migrate.main_with_args(
        ["--data-repo-dir", str(repo), "--season", str(SEASON), "--report", str(report_path)]
    )
    assert exit_code == 0

    summary = json.loads(report_path.read_text())
    assert summary["mode"] == "apply"
    assert summary["totals"]["rows_in"] == summary["totals"]["rows_out"] == 20 * len(FAMILY_FIXTURES)
    assert summary["totals"]["duplicate_keys"] == 0
    assert summary["totals"]["missing_keys"] == 0
    for family in FAMILY_FIXTURES:
        assert not shards.legacy_monolith_path(base, family, SEASON).exists()
        assert shards.shard_paths(base, family, SEASON)


def test_re_running_a_completed_migration_is_a_no_op(tmp_path):
    repo = tmp_path / "repo"
    base = repo / "data" / "research"
    _write_monolith(base, shards.OBSERVATIONS_SUBDIR, _spread_rows(40))
    args = ["--data-repo-dir", str(repo), "--season", str(SEASON), "--families", shards.OBSERVATIONS_SUBDIR]
    assert migrate.main_with_args(args) == 0

    snapshot = {
        p.name: p.read_bytes() for p in shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    }
    assert migrate.main_with_args(args) == 0
    after = {p.name: p.read_bytes() for p in shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON)}
    assert after == snapshot
