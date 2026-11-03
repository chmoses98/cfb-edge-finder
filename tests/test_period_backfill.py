"""Period backfill: provenance, validation, duplicates, quarantine, idempotency."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from cfb_edge_finder.projections.period_data import coverage
from cfb_edge_finder.research import period_backfill as pb
from cfb_edge_finder.scripting.gamelog import TeamGame

_spec = importlib.util.spec_from_file_location(
    "backfill_period_scores", Path(__file__).resolve().parents[1] / "scripts" / "backfill_period_scores.py"
)
backfill_script = importlib.util.module_from_spec(_spec)
sys.modules["backfill_period_scores"] = backfill_script
_spec.loader.exec_module(backfill_script)  # type: ignore[union-attr]


def summary(
    gid="401", home=("111", [7, 10, 3, 7]), away=("222", [0, 14, 7, 3]), *, completed=True, date="2026-10-17T19:00Z"
):
    def comp(side, tid, periods):
        return {
            "homeAway": side,
            "team": {"id": tid},
            "score": str(sum(periods)),
            "linescores": [{"displayValue": str(p)} for p in periods],
        }

    return {
        "header": {
            "id": gid,
            "season": {"year": 2026},
            "competitions": [
                {
                    "date": date,
                    "status": {"type": {"completed": completed}},
                    "competitors": [comp("home", *home), comp("away", *away)],
                }
            ],
        }
    }


T = "2026-11-03T00:00:00Z"


def test_valid_record_has_full_provenance():
    rec = pb.record_from_summary(summary(), fetched_at=T, expected_teams=("111", "222"))
    assert rec is not None and rec.valid
    assert rec.home_periods == (7, 10, 3, 7) and rec.away_periods == (0, 14, 7, 3)
    assert rec.source == {"name": pb.SOURCE_NAME, "endpoint": pb.ENDPOINT, "event_id": "401"}
    assert len(rec.payload_sha256) == 64 and len(rec.header_sha256) == 64 and rec.fetched_at == T
    assert rec.kickoff_utc == "2026-10-17T19:00:00Z"


def test_incomplete_game_yields_no_record():
    assert pb.record_from_summary(summary(completed=False), fetched_at=T) is None


@pytest.mark.parametrize(
    "home,away,issue",
    [
        (("111", [7, 10, 3]), ("222", [0, 14, 7]), "FEWER_THAN_4_PERIODS"),
        (("111", [7, 10, 3, 7]), ("222", [0, 14, 7, 3, 0]), "PERIOD_COUNT_DIFFERS_HOME_AWAY"),
        (("111", [7, 10, 3, 60]), ("222", [0, 14, 7, 3]), "IMPLAUSIBLE_POINTS"),
    ],
)
def test_validation_issues(home, away, issue):
    rec = pb.record_from_summary(summary(home=home, away=away), fetched_at=T)
    assert rec is not None and issue in rec.issues and not rec.valid


def test_period_sum_must_equal_final():
    s = summary()
    s["header"]["competitions"][0]["competitors"][0]["score"] = "30"
    rec = pb.record_from_summary(s, fetched_at=T)
    assert "HOME_PERIOD_SUM_MISMATCH" in rec.issues


def test_missing_and_malformed_linescores():
    s = summary()
    s["header"]["competitions"][0]["competitors"][0]["linescores"] = None
    assert "MISSING_LINESCORES" in pb.record_from_summary(s, fetched_at=T).issues
    s = summary()
    s["header"]["competitions"][0]["competitors"][1]["linescores"][0]["displayValue"] = "x"
    assert "NON_NUMERIC_PERIOD" in pb.record_from_summary(s, fetched_at=T).issues
    s = summary()
    s["header"]["competitions"][0]["competitors"][1]["linescores"][0]["displayValue"] = "-3"
    assert "NEGATIVE_OR_NONINTEGER_PERIOD" in pb.record_from_summary(s, fetched_at=T).issues


def test_team_mapping_mismatch_is_quarantined_not_swapped(tmp_path):
    rec = pb.record_from_summary(summary(), fetched_at=T, expected_teams=("222", "111"))
    assert "TEAM_MAPPING_MISMATCH" in rec.issues
    store = pb.PeriodStore(tmp_path)
    assert store.ingest(rec) == pb.INVALID_QUARANTINED
    assert store.valid_records() == [] and (tmp_path / "period_scores_quarantine.jsonl").exists()


def test_new_then_duplicate_is_idempotent_byte_for_byte(tmp_path):
    store = pb.PeriodStore(tmp_path)
    rec = pb.record_from_summary(summary(), fetched_at=T)
    assert store.ingest(rec) == pb.NEW
    before = (tmp_path / "period_scores.jsonl").read_bytes()
    again = pb.record_from_summary(summary(), fetched_at="2026-11-04T00:00:00Z")
    assert store.ingest(again) == pb.DUPLICATE_NOOP
    assert pb.PeriodStore(tmp_path).ingest(again) == pb.DUPLICATE_NOOP  # also from a cold start
    assert (tmp_path / "period_scores.jsonl").read_bytes() == before
    assert not (tmp_path / "period_scores_quarantine.jsonl").exists()


def test_conflicting_numbers_are_quarantined_and_never_overwrite(tmp_path):
    store = pb.PeriodStore(tmp_path)
    assert store.ingest(pb.record_from_summary(summary(), fetched_at=T)) == pb.NEW
    revised = summary(home=("111", [7, 10, 6, 7]))  # a changed (still internally consistent) linescore
    out = store.ingest(pb.record_from_summary(revised, fetched_at=T))
    assert out == pb.CONFLICT_QUARANTINED
    kept = store.get("401")
    assert kept["home_periods"] == [7, 10, 3, 7]
    q = [json.loads(x) for x in (tmp_path / "period_scores_quarantine.jsonl").read_text().splitlines()]
    assert len(q) == 1 and q[0]["quarantine_reason"] == "SAME_GAME_DIFFERENT_NUMBERS" and "conflicts_with" in q[0]
    # re-ingesting the same conflicting payload does not append a second quarantine row
    store.ingest(pb.record_from_summary(revised, fetched_at=T))
    assert len((tmp_path / "period_scores_quarantine.jsonl").read_text().splitlines()) == 1


def test_same_matchup_under_two_game_ids(tmp_path):
    store = pb.PeriodStore(tmp_path)
    store.ingest(pb.record_from_summary(summary(gid="401"), fetched_at=T))
    assert store.ingest(pb.record_from_summary(summary(gid="999"), fetched_at=T)) == pb.DUPLICATE_NOOP
    other = summary(gid="998", home=("111", [7, 10, 6, 7]))
    assert store.ingest(pb.record_from_summary(other, fetched_at=T)) == pb.CONFLICT_QUARANTINED
    assert len(store.valid_records()) == 1


def _tg(game_id, team_id, opp_id, site, pts):
    return TeamGame(
        game_id=game_id,
        season=2026,
        week=7,
        kickoff_utc="2026-10-17T19:00:00Z",
        team_id=team_id,
        team=team_id,
        opponent_id=opp_id,
        opponent=opp_id,
        team_division="fbs",
        opponent_division="fbs",
        site=site,
        points_for=pts[0],
        points_against=pts[1],
        box={},
        pbp=None,
    )


def test_attach_periods_matches_orientation_and_updates_coverage(tmp_path):
    store = pb.PeriodStore(tmp_path)
    store.ingest(pb.record_from_summary(summary(), fetched_at=T))
    rows = [
        _tg("401", "111", "222", "home", (27, 24)),
        _tg("401", "222", "111", "away", (24, 27)),
        _tg("500", "333", "444", "home", (10, 3)),
        _tg("500", "444", "333", "away", (3, 10)),
    ]
    out, stats = pb.attach_periods(rows, store)
    assert stats == {"attached": 2, "already_had": 0, "no_record": 2, "mapping_mismatch": 0}
    assert out[0].period_points == (7, 10, 3, 7) and out[1].period_points == (0, 14, 7, 3)
    assert out[2].period_points is None  # never filled in
    cov = coverage(out)
    assert cov["games"] == 2 and cov["games_with_periods"] == 1
    bad = [_tg("401", "222", "111", "home", (27, 24)), _tg("401", "111", "222", "away", (24, 27))]
    _, stats2 = pb.attach_periods(bad, store)
    assert stats2["attached"] == 2  # orientation follows team ids, not the row's site label
    wrong = [_tg("401", "999", "888", "home", (1, 2))]
    assert pb.attach_periods(wrong, store)[1]["mapping_mismatch"] == 1


def test_script_offline_and_dry_run(tmp_path, capsys):
    tg = tmp_path / "team_games.jsonl"
    rows = [_tg("401", "111", "222", "home", (27, 24)), _tg("401", "222", "111", "away", (24, 27))]
    tg.write_text("\n".join(json.dumps(r.as_dict()) for r in rows) + "\n")
    sdir = tmp_path / "summaries"
    sdir.mkdir()
    (sdir / "401.json").write_text(json.dumps(summary()))
    store_dir = tmp_path / "store"
    assert (
        backfill_script.main(
            ["--team-games", str(tg), "--store-dir", str(store_dir), "--summaries-dir", str(sdir), "--dry-run"]
        )
        == 0
    )
    assert not store_dir.exists()
    assert json.loads(capsys.readouterr().out)["VALID_WOULD_WRITE"] == 1
    assert (
        backfill_script.main(["--team-games", str(tg), "--store-dir", str(store_dir), "--summaries-dir", str(sdir)])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["NEW"] == 1
    assert (
        backfill_script.main(["--team-games", str(tg), "--store-dir", str(store_dir), "--summaries-dir", str(sdir)])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["DUPLICATE_NOOP"] == 1
    assert backfill_script.main(["--team-games", str(tmp_path / "nope.jsonl"), "--store-dir", str(store_dir)]) == 2
