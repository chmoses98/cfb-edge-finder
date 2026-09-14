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


# =========================================================================
# THE CUTOVER DEADLOCK
#
# merge -> a scheduled capture runs -> it writes the first shard ->
# monolith and shards coexist -> migration refuses -> stuck.
#
# `research/maintenance.py` is what stops that state forming.
# `--allow-existing-shards` is what makes it survivable if it forms
# anyway (a pre-merge run still in flight, a workflow re-enabled early,
# a raw push). The CEO's requirement was "this must not be possible" --
# so it is prevented AND recoverable, not one or the other.
# =========================================================================


def _cutover_row(key: str, *, day: int = 12, extra: str = "x") -> dict:
    return {
        "observation_key": key,
        "observation": {"captured_at": f"2026-09-{day:02d}T10:00:00+00:00", "note": extra},
    }


def _cutover_monolith(base: Path, rows: list[dict]) -> Path:
    legacy = shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, 2026)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8"
    )
    return legacy


def _cutover_shard_rows(base: Path, rows: list[dict]) -> None:
    """Place rows in the shards the way a post-merge writer's append
    lands them -- same shard rule, same serialisation -- but WITHOUT the
    dedup pass.

    Deliberately not `append_sharded_json_rows`: that dedups against the
    legacy monolith as well (which is the transition guarantee, and is
    asserted directly below), so it is incapable of producing a row that
    sits in both layouts. The migration's behaviour when a row DOES sit
    in both is the thing under test, so the fixture has to construct it.
    """
    shards.append_lines(
        base,
        shards.OBSERVATIONS_SUBDIR,
        2026,
        [
            (shards.shard_date_for(row, shards.OBSERVATIONS_SUBDIR),
             json.dumps(row, sort_keys=True))
            for row in rows
        ],
    )


def test_a_post_merge_writer_cannot_create_the_duplicate_in_the_first_place(tmp_path):
    """Why the coexistence case is a narrow repair rather than a merge:
    during the transition the writer READS the legacy monolith, so it
    dedups against it and can only ever add genuinely NEW keys to the
    shards. Two rows claiming one key is therefore already impossible on
    the live path -- the migration handles it anyway, but this is the
    reason it should never have to."""
    base = tmp_path / "data" / "research"
    _cutover_monolith(base, [_cutover_row("a"), _cutover_row("b")])

    result = persistence.append_sharded_json_rows(
        base,
        shards.OBSERVATIONS_SUBDIR,
        2026,
        [_cutover_row("a"), _cutover_row("new")],
        persistence.observation_key_of,
    )

    assert result.written == 1 and result.skipped_duplicate == 1
    shard_keys = [json.loads(line)["observation_key"] for line in _cutover_lines(base)]
    assert shard_keys == ["new"], "the writer re-emitted a row that was already in the monolith"


def _cutover_lines(base: Path) -> list[str]:
    lines: list[str] = []
    for path in shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, 2026):
        lines.extend(x for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
    return lines


def test_coexistence_still_refuses_by_default(tmp_path):
    """The default must stay conservative: migrating blindly into a
    populated shard set is how rows get duplicated."""
    base = tmp_path / "data" / "research"
    _cutover_monolith(base, [_cutover_row("a"), _cutover_row("b")])
    _cutover_shard_rows(base, [_cutover_row("c")])

    with pytest.raises(migrate.MigrationError) as exc:
        migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, 2026)

    message = str(exc.value)
    assert "already exist" in message
    # ...but it must no longer read as a dead end.
    assert "--allow-existing-shards" in message
    assert shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, 2026).is_file()


def test_the_deadlock_is_recoverable_with_allow_existing_shards(tmp_path):
    """THE test for BLOCKER 2's recovery half: a writer landed a shard
    between merge and migration, and the cutover still completes with
    every row present exactly once."""
    base = tmp_path / "data" / "research"
    monolith_rows = [_cutover_row("a"), _cutover_row("b")]
    _cutover_monolith(base, monolith_rows)
    _cutover_shard_rows(base, [_cutover_row("c", day=13)])  # the writer that landed in the gap

    report = migrate.migrate_family(
        base, shards.OBSERVATIONS_SUBDIR, 2026, allow_existing_shards=True
    )

    assert report["status"] == "MIGRATED"
    assert report["preexisting_shard_rows"] == 1
    assert report["rows_appended"] == 2
    assert report["rows_out"] == 3
    assert report["duplicate_keys"] == 0
    assert report["missing_keys"] == 0
    assert report["preexisting_rows_lost"] == 0
    assert not shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, 2026).exists()

    keys = {json.loads(line)["observation_key"] for line in _cutover_lines(base)}
    assert keys == {"a", "b", "c"}
    assert len(_cutover_lines(base)) == 3, "a row was duplicated or lost"


def test_a_row_the_writer_already_captured_is_not_appended_twice(tmp_path):
    """The writer reads the legacy monolith during the transition, so it
    dedups against it and should never re-emit a monolith row. If it does
    anyway, the migration must not turn that into a duplicate."""
    base = tmp_path / "data" / "research"
    shared = _cutover_row("a")
    _cutover_monolith(base, [shared, _cutover_row("b")])
    _cutover_shard_rows(base, [shared])

    report = migrate.migrate_family(
        base, shards.OBSERVATIONS_SUBDIR, 2026, allow_existing_shards=True
    )

    assert report["rows_already_present"] == 1
    assert report["rows_appended"] == 1
    assert report["duplicate_keys"] == 0
    assert report["missing_keys"] == 0
    lines = _cutover_lines(base)
    assert len(lines) == 2
    assert sorted(json.loads(line)["observation_key"] for line in lines) == ["a", "b"]


def test_same_key_different_bytes_refuses_rather_than_choosing(tmp_path):
    """Two versions of one observation is a question about what the data
    SAYS. Neither copy may be discarded automatically, so this fails with
    both lines named and the monolith left exactly where it was."""
    base = tmp_path / "data" / "research"
    _cutover_monolith(base, [_cutover_row("a", extra="from-the-monolith")])
    _cutover_shard_rows(base, [_cutover_row("a", extra="from-the-shard")])

    with pytest.raises(migrate.MigrationError) as exc:
        migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, 2026, allow_existing_shards=True)

    message = str(exc.value)
    assert "same dedup key with different content" in message
    assert "from-the-monolith" in message and "from-the-shard" in message
    assert shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, 2026).is_file()
    assert len(_cutover_lines(base)) == 1, "the shard set was modified by a refused migration"


def test_allow_existing_shards_is_a_no_op_when_no_shards_exist(tmp_path):
    """The flag must not change the normal cutover's behaviour at all --
    otherwise the production run would be rehearsing a different path
    from the one the equivalence proof covers."""
    base_plain = tmp_path / "plain" / "data" / "research"
    base_flag = tmp_path / "flag" / "data" / "research"
    rows = [_cutover_row("a"), _cutover_row("b", day=13), _cutover_row("c")]
    _cutover_monolith(base_plain, rows)
    _cutover_monolith(base_flag, rows)

    plain = migrate.migrate_family(base_plain, shards.OBSERVATIONS_SUBDIR, 2026)
    flagged = migrate.migrate_family(
        base_flag, shards.OBSERVATIONS_SUBDIR, 2026, allow_existing_shards=True
    )

    assert plain["status"] == flagged["status"] == "MIGRATED"
    for field in ("rows_in", "rows_out", "shards_written", "duplicate_keys", "missing_keys",
                  "order_violations", "byte_identical"):
        assert plain[field] == flagged[field], field
    assert _cutover_lines(base_plain) == _cutover_lines(base_flag)


def test_pre_existing_shard_rows_survive_byte_for_byte(tmp_path):
    """Nothing the writer wrote may be disturbed by the repair."""
    base = tmp_path / "data" / "research"
    _cutover_monolith(base, [_cutover_row("a"), _cutover_row("b", day=13)])
    _cutover_shard_rows(base, [_cutover_row("c"), _cutover_row("d", day=13)])
    before = sorted(_cutover_lines(base))

    migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, 2026, allow_existing_shards=True)

    after = _cutover_lines(base)
    for line in before:
        assert line in after, "a pre-existing shard line was altered or dropped"


def test_migration_is_idempotent_after_the_deadlock_recovery(tmp_path):
    """Re-running the recovery must be a no-op, so an operator who is
    unsure whether it completed can simply run it again."""
    base = tmp_path / "data" / "research"
    _cutover_monolith(base, [_cutover_row("a"), _cutover_row("b")])
    _cutover_shard_rows(base, [_cutover_row("c")])
    migrate.migrate_family(base, shards.OBSERVATIONS_SUBDIR, 2026, allow_existing_shards=True)
    first = _cutover_lines(base)

    again = migrate.migrate_family(
        base, shards.OBSERVATIONS_SUBDIR, 2026, allow_existing_shards=True
    )
    assert again["status"] == "NOTHING_TO_MIGRATE"
    assert _cutover_lines(base) == first


def test_the_cli_exposes_the_recovery_flag(tmp_path):
    base = tmp_path / "data" / "research"
    _cutover_monolith(base, [_cutover_row("a")])
    _cutover_shard_rows(base, [_cutover_row("c", day=13)])

    assert migrate.main_with_args(
        ["--data-repo-dir", str(tmp_path), "--season", "2026",
         "--families", shards.OBSERVATIONS_SUBDIR, "--allow-existing-shards"]
    ) == 0
    assert not shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, 2026).exists()


def test_a_dry_run_of_the_recovery_rehearses_against_the_real_shards(tmp_path):
    """A dry run that copied only the monolith would rehearse against an
    empty shard set -- i.e. prove something other than what the real run
    is about to do -- and would wrongly report success on a conflict."""
    base = tmp_path / "data" / "research"
    _cutover_monolith(base, [_cutover_row("a", extra="from-the-monolith")])
    _cutover_shard_rows(base, [_cutover_row("a", extra="from-the-shard")])

    assert migrate.main_with_args(
        ["--data-repo-dir", str(tmp_path), "--season", "2026",
         "--families", shards.OBSERVATIONS_SUBDIR, "--allow-existing-shards", "--dry-run"]
    ) == 1
    # And the real tree is untouched by the rehearsal, as always.
    assert shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, 2026).is_file()
    assert len(_cutover_lines(base)) == 1
