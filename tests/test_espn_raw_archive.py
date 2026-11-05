"""Raw ESPN archive: extraction against REAL captured summaries, reconciliation, availability and idempotency. Offline."""

import json
import tempfile
from pathlib import Path

import pytest

from cfb_edge_finder.ladder_archive import espn_raw as er
from cfb_edge_finder.ladder_archive import espn_store as es

FIX = Path(__file__).parent / "fixtures" / "espn"


def raw(eid):
    return (FIX / f"summary_{eid}.json").read_bytes()


def ok(eid, at="2026-11-05T06:00:00Z"):
    return {"ok": True, "status": 200, "uri": f"u?event={eid}", "body": raw(eid), "fetched_at": at, "error": None}


def test_real_summary_fg_distances_and_box_reconciliation():
    r = er.index_row("401856705", ok("401856705"), {"date": "2026-10-03T16:00Z", "state": "post"}, "r")
    fg = r["fg_attempts"]
    assert [(f["distance_yards"], f["result"]) for f in fg] == [(58, "BLOCKED"), (39, "GOOD")]
    assert r["reconciliation"]["fg_plays_vs_box_attempts"] == {"plays": 2, "box": 2, "match": True}
    assert r["reconciliation"]["last_play_score_vs_final"]["match"] is True
    assert r["missingness"] == [] and r["n_drives"] == 19 and r["first_drive_by_team"]["238"]["result"] == "PUNT"


def test_scoring_phrase_fg_is_captured_not_lost():
    """401858249 has an FG recorded as '29 Yd Field Goal' (no 'attempt from'); missing it made box and plays disagree."""
    r = er.index_row("401858249", ok("401858249"), None, "r")
    dist = sorted((f["distance_yards"], f["distance_source"]) for f in r["fg_attempts"])
    assert (29, "play_text_scoring_phrase") in dist and len(dist) == 4
    assert r["reconciliation"]["fg_plays_vs_box_attempts"]["match"] is True


def test_postgame_records_are_never_pregame_and_available_only_from_our_fetch_time():
    r = er.index_row("401856705", ok("401856705", "2026-11-05T06:00:00Z"), {"date": "2026-10-03T16:00Z"}, "r")
    assert r["pregame_evidence"] is False and r["available_from"] == "2026-11-05T06:00:00Z" == r["fetched_at"]
    assert "postgame" in r["usage_rule"]


def test_missingness_is_explicit_never_zero_filled():
    s = json.loads(raw("401856705"))
    s["drives"] = {"previous": []}
    s["boxscore"]["players"] = []
    r = er.index_row(
        "1",
        {"ok": True, "status": 200, "uri": "u", "body": json.dumps(s).encode(), "fetched_at": "t", "error": None},
        None,
        "r",
    )
    assert (
        "NO_DRIVES" in r["missingness"]
        and "NO_KICKING_CATEGORY" in r["missingness"]
        and r["fg_attempts"] == []
        and r["kicking_box"] == {}
    )
    bad = er.index_row(
        "2", {"ok": False, "status": 503, "uri": "u", "body": None, "fetched_at": "t", "error": "HTTP 503"}, None, "r"
    )
    assert bad["missingness"] == ["FETCH_FAILED"] and bad["fetch_ok"] is False and bad["sha256"] is None
    nj = er.index_row(
        "3", {"ok": True, "status": 200, "uri": "u", "body": b"<html>", "fetched_at": "t", "error": None}, None, "r"
    )
    assert "PAYLOAD_NOT_JSON" in nj["missingness"]


def test_fg_without_distance_keeps_none_and_reason_and_duplicates_are_flagged():
    s = json.loads(raw("401856705"))
    for d in s["drives"]["previous"]:
        for p in list(d["plays"]):
            if p.get("type", {}).get("text") == "Field Goal Good":
                p["text"] = "J. Kicker field goal GOOD"
                dup = json.loads(json.dumps(p))
                d["plays"].append(dup)
    fgs, flags = er.fg_attempts(s)
    nodist = [f for f in fgs if f["result"] == "GOOD"][0]
    assert nodist["distance_yards"] is None and nodist["distance_missing_reason"] == "NO_DISTANCE_IN_PLAY_TEXT"
    assert (
        any(f.startswith("DUPLICATE_FG_PLAY_ID") for f in flags) and len([f for f in fgs if f["result"] == "GOOD"]) == 1
    )


def test_box_disagreement_is_surfaced():
    s = json.loads(raw("401856705"))
    for blk in s["boxscore"]["players"]:
        for cat in blk["statistics"]:
            if cat["name"] == "kicking":
                cat["totals"][0] = "5/9"
    r = er.index_row(
        "1",
        {"ok": True, "status": 200, "uri": "u", "body": json.dumps(s).encode(), "fetched_at": "t", "error": None},
        None,
        "r",
    )
    assert "FG_PLAYS_DISAGREE_WITH_BOX_ATTEMPTS" in r["missingness"]


def test_archive_roundtrip_idempotency_and_tamper():
    with tempfile.TemporaryDirectory() as d:
        a = Path(d)
        row = er.index_row("401856705", ok("401856705"), {"date": "x", "state": "post"}, "r1")
        man = {"run_id": "r1", "finished_at": "2026-11-05T06:00:01Z", "started_at": "x"}
        es.write_run(a, man, [row], {"401856705": raw("401856705")})
        assert es.verify(a)["ok"]
        h = es.history(a)
        assert h["401856705"]["completed_ok"] and es.should_fetch(h["401856705"]) == (
            False,
            "ALREADY_ARCHIVED_COMPLETE",
        )
        assert es.should_fetch(None) == (True, "NEW")
        with pytest.raises(FileExistsError):
            es.write_run(a, man, [dict(row)], {"401856705": raw("401856705")})
        f = next((a / "raw").glob("*/*.json.gz"))
        f.write_bytes(__import__("gzip").compress(b'{"x":2}', mtime=0))
        assert not es.verify(a)["ok"]


def test_failures_are_retried_then_given_up_and_never_become_rows_of_zeros():
    h = {"archived_ok": False, "completed_ok": False, "failed_attempts": 2}
    assert es.should_fetch(h)[0] is True
    h["failed_attempts"] = es.MAX_FAILED_ATTEMPTS
    assert es.should_fetch(h) == (False, "GAVE_UP_AFTER_FAILURES")
    # an event archived once as incomplete that keeps failing is also given up (no starvation of newer games)
    assert es.should_fetch({"archived_ok": True, "completed_ok": False, "failed_attempts": 9}) == (
        False,
        "GAVE_UP_AFTER_FAILURES",
    )
    assert (
        es.should_fetch({"archived_ok": True, "completed_ok": False, "failed_attempts": 0, "incomplete_attempts": 1})[0]
        is True
    )


def test_fetch_raw_retries_transient_and_hashes_original_bytes():
    class R:
        def __init__(s, c, code):
            s.content, s.status_code = c, code

    seq = [R(b"", 503), R(b'{"a":1}', 200)]
    calls = []

    def g(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return seq.pop(0)

    out = er.fetch_raw("1", http_get=g, sleep=lambda s: None)
    assert out["ok"] and out["body"] == b'{"a":1}' and len(calls) == 2
    out = er.fetch_raw("1", http_get=lambda *a, **k: R(b"", 404), sleep=lambda s: None)
    assert not out["ok"] and out["status"] == 404 and out["body"] is None


def test_fg_type_whitelist_and_word_boundary_result():
    plays = [
        {
            "id": "1",
            "type": {"text": "Field Goal Good"},
            "text": "(10:00) J.Goodman field goal attempt from 41 yards GOOD",
            "period": {"number": 1},
        },
        {
            "id": "2",
            "type": {"text": "Field Goal Missed"},
            "text": "(09:00) J.Gooden field goal attempt from 52 yards NO GOOD",
            "period": {"number": 1},
        },
        {
            "id": "3",
            "type": {"text": "Missed Field Goal Return"},
            "text": "returned field goal attempt",
            "period": {"number": 1},
        },
        {
            "id": "4",
            "type": {"text": "Penalty"},
            "text": "false start, field goal attempt from 40 yards",
            "period": {"number": 1},
        },
        {"id": "5", "type": {"text": "Field Goal"}, "text": "Goodman 33 Yd Field Goal MISSED", "period": {"number": 2}},
    ]
    fgs, _ = er.fg_attempts({"drives": {"previous": [{"team": {"id": "1"}, "plays": plays}]}})
    got = {f["play_id"]: (f["result"], f["distance_yards"]) for f in fgs}
    assert got["1"] == ("GOOD", 41) and got["2"] == ("NO_GOOD", 52) and set(got) == {"1", "2", "5"}
    assert got["5"][0] == "NO_GOOD" and got["5"][1] == 33  # "Goodman" must not turn a MISSED kick into GOOD


def test_made_count_period_scores_and_incomplete_box_are_reconciled():
    s = json.loads(raw("401856705"))
    for blk in s["boxscore"]["players"]:
        for cat in blk["statistics"]:
            if cat["name"] == "kicking":
                cat["totals"][0] = "1/2" if blk["team"]["id"] == "61" else cat["totals"][0]
    r = er.index_row(
        "1",
        {"ok": True, "status": 200, "uri": "u", "body": json.dumps(s).encode(), "fetched_at": "t", "error": None},
        None,
        "r",
    )
    assert "FG_MADE_DISAGREES_WITH_BOX" in r["missingness"] or "FG_PLAYS_DISAGREE_WITH_BOX_ATTEMPTS" in r["missingness"]
    s2 = json.loads(raw("401856705"))
    s2["header"]["competitions"][0]["competitors"][0]["score"] = "99"
    r2 = er.index_row(
        "2",
        {"ok": True, "status": 200, "uri": "u", "body": json.dumps(s2).encode(), "fetched_at": "t", "error": None},
        None,
        "r",
    )
    assert "PERIOD_SCORES_DISAGREE_WITH_FINAL" in r2["missingness"]


def test_payloads_that_never_complete_are_not_re_archived_forever():
    h = {
        "archived_ok": True,
        "completed_ok": False,
        "failed_attempts": 0,
        "incomplete_attempts": es.MAX_FAILED_ATTEMPTS,
    }
    assert es.should_fetch(h) == (False, "GAVE_UP_PAYLOAD_NEVER_COMPLETED")


def test_empty_kicking_category_is_a_genuine_zero_but_an_absent_one_is_missing():
    s = json.loads(raw("401858250"))
    for blk in s["boxscore"]["players"]:
        for cat in blk["statistics"]:
            if cat["name"] == "kicking" and blk["team"]["id"] != "87":
                cat["totals"], cat["athletes"] = [], []
    r = er.index_row(
        "1",
        {"ok": True, "status": 200, "uri": "u", "body": json.dumps(s).encode(), "fetched_at": "t", "error": None},
        None,
        "r",
    )
    assert (
        "KICKING_BOX_INCOMPLETE" not in r["missingness"]
        and r["reconciliation"]["fg_plays_vs_box_attempts"]["match"] is True
    )
    for blk in s["boxscore"]["players"]:
        blk["statistics"] = [c for c in blk["statistics"] if not (c["name"] == "kicking" and blk["team"]["id"] != "87")]
    r2 = er.index_row(
        "2",
        {"ok": True, "status": 200, "uri": "u", "body": json.dumps(s).encode(), "fetched_at": "t", "error": None},
        None,
        "r",
    )
    assert (
        r2["reconciliation"]["fg_plays_vs_box_attempts"]["match"] is True
        or r2["reconciliation"]["fg_plays_vs_box_attempts"]["match"] is None
    )


def test_non_dict_or_non_json_200_never_crashes_or_blocks_publishing():
    for body in (b"[]", b"null", b"<html>", b'"x"'):
        r = er.index_row(
            "1", {"ok": True, "status": 200, "uri": "u", "body": body, "fetched_at": "t", "error": None}, None, "r"
        )
        assert r["missingness"] == ["PAYLOAD_NOT_JSON"]

    class R:
        def __init__(s, c, code):
            s.content, s.status_code = c, code

    out = er.fetch_raw("1", http_get=lambda *a, **k: R(b"<html>maintenance</html>", 200), sleep=lambda s: None)
    assert not out["ok"] and out["body"] is None and "non-JSON" in out["error"]


def test_archived_but_incomplete_events_that_keep_failing_are_given_up():
    h = {
        "archived_ok": True,
        "completed_ok": False,
        "failed_attempts": es.MAX_FAILED_ATTEMPTS,
        "incomplete_attempts": 1,
    }
    assert es.should_fetch(h) == (False, "GAVE_UP_AFTER_FAILURES")
