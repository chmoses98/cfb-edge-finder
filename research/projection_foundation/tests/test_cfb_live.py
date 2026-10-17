"""CFB live-operation tests: availability capture (on REAL recorded ESPN payloads), QB flags, archive records, incumbent,
results mapping, and the full capture->project->results->settle->score replay (opt-in: needs the data cache).

Recorded data used:
  tests/fixtures/espn_injuries_2026-09-17T114253Z.json   150 real team-level injury list payloads (research-sidecar branch)
  data/scripting/live/frozen/*.json.gz                   the script engine's real frozen artifacts (incumbent)
  data/football/2026/{schedule.json,team_games.jsonl}    the repo's real ESPN schedule and game log
The per-athlete injury record body was never recorded by any repo job; `parse_record` is exercised on a SYNTHETIC record
that follows ESPN's documented core schema (labelled as such below) and is verified against the live feed by the
GitHub Actions run (the capture summary logs the keys of the first resolved record).
"""
import gzip
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from pf_core import availability as av  # noqa: E402
from pf_core import prospective as pr  # noqa: E402

from cfb import gate_cfb, paths  # noqa: E402
from cfb.live import availability as la  # noqa: E402
from cfb.live import espn_injuries as ei  # noqa: E402
from cfb.live import incumbent, records, results  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "espn_injuries_2026-09-17T114253Z.json")
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
NOW = datetime(2026, 9, 17, 11, 43, tzinfo=timezone.utc)


def recorded_get(url):
    """Transport that replays the recorded list-level payloads; anything else was never recorded."""
    doc = json.load(open(FIX))
    for tid, r in doc["teams"].items():
        if url == r["url"]:
            return r["parsed"], None
    return None, "HTTP 404 (not recorded)"


def test_recorded_payloads_show_items_are_reference_urls_not_records():
    doc = json.load(open(FIX))
    listed = {t: r["parsed"]["items"] for t, r in doc["teams"].items() if r["parsed"]["items"]}
    assert listed, "fixture should contain the 5 teams ESPN listed an injury for"
    for items in listed.values():
        assert all(isinstance(x, str) and "/athletes/" in x and "/injuries/" in x for x in items)
    # the legacy collector (scripts/collect_football_gamelog.availability_for) only keeps dict entries
    legacy = [i for items in listed.values() for i in items if isinstance(i, dict)]
    assert legacy == []


def test_fetch_resolves_references_and_keeps_athlete_ids_from_the_url():
    doc = json.load(open(FIX))
    tid = next(t for t, r in doc["teams"].items() if r["parsed"]["items"])
    res = ei.fetch_team(tid, recorded_get)
    assert res.ok and res.listed == 1 and len(res.records) == 1
    r = res.records[0]
    assert r.athlete_id and r.athlete_id.isdigit() and r.injury_id and not r.resolved and r.status == "UNKNOWN"
    assert res.unresolved == 1
    empty = next(t for t, r_ in doc["teams"].items() if not r_["parsed"]["items"])
    e = ei.fetch_team(empty, recorded_get)
    assert e.ok and e.listed == 0 and e.records == []
    missing = ei.fetch_team("999999", recorded_get)
    assert not missing.ok and missing.error


def test_parse_record_on_a_synthetic_record_following_the_documented_schema():
    # SYNTHETIC (not recorded): ESPN core injury record shape
    body = {"$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2026/athletes/111/injuries/9",
            "id": "9", "status": "Questionable", "date": "2026-09-16T18:00Z", "type": {"description": "questionable"},
            "athlete": {"$ref": "http://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2026/athletes/111?lang=en"},
            "details": {"type": "Knee"}}
    r = ei.parse_record(body, body["$ref"])
    assert (r.athlete_id, r.injury_id, r.status, r.detail) == ("111", "9", "QUESTIONABLE", "Knee")
    for raw, want in (("Out", "OUT"), ("Out For Season", "OUT"), ("Doubtful", "DOUBTFUL"), ("Day-To-Day", "QUESTIONABLE"),
                      ("Probable", "PROBABLE"), ("Suspension", "OUT"), (None, "UNKNOWN"), ("weird", "UNKNOWN")):
        assert ei.normalise_status(raw) == want


def _events_and_obs(extra_qb=None):
    sched = json.load(open(os.path.join(REPO, "data", "football", "2026", "schedule.json")))
    ev = la.upcoming_events(sched, NOW, datetime(2026, 9, 22, 8, tzinfo=timezone.utc), replay=True)
    team_ids = sorted({str(e[s]["team_id"]) for e in ev for s in ("home", "away")})
    inj = ei.fetch_teams(team_ids, recorded_get)
    rows = [json.loads(x) for x in open(os.path.join(REPO, "data", "football", "2026", "team_games.jsonl"))]
    qb = la.qb_state(rows, NOW)
    obs = la.build_observations(captured_utc="2026-09-17T11:43:00Z", events=ev, injuries=inj, qb=qb, season=2026)
    return ev, inj, qb, obs


def test_observations_valid_pointintime_and_immutable(tmp_path):
    ev, inj, qb, obs = _events_and_obs()
    assert ev and obs
    assert all(o.validate() == [] for o in obs)
    assert all(o.captured_utc < o.event_start_utc for o in obs)              # nothing captured at/after start
    assert all(o.sport == "CFB" for o in obs)
    kinds = {(o.source, o.kind, o.certainty) for o in obs}
    assert ("espn_core_injuries", "roster", "UNKNOWN") in kinds and (la.QB_SOURCE, "starter", "PROJECTED") in kinds
    assert not any(o.certainty == "CONFIRMED" for o in obs)                  # CFB feeds never confirm a starter
    r1 = av.write_snapshot(obs, str(tmp_path), "CFB", "2026-09-17T11:43:00Z")
    assert r1["written"] == len(obs) and r1["rejected"] == 0
    assert av.write_snapshot(obs, str(tmp_path), "CFB", "2026-09-17T11:43:00Z")["duplicate"]
    rows = av.load_snapshots(str(tmp_path), "CFB")
    assert len(rows) == len(obs)
    # as-of: before the capture nothing is visible; after it, a team that played last week has a projected QB
    assert av.asof(rows, "2026-09-17T11:42:00Z") == {}
    assert av.asof(rows, "2026-09-17T11:44:00Z")
    # an unread list is recorded as unread, never as healthy
    failed = [o for o in obs if o.kind == "roster" and not o.detail["injury_list_read"]]
    ok = [o for o in obs if o.kind == "roster" and o.detail["injury_list_read"]]
    assert failed and ok and all(o.detail["error"] for o in failed)


def test_qb_assessment_flags():
    ev, inj, qb, obs = _events_and_obs()
    rows = [o.__dict__ for o in obs]
    latest = av.asof(rows, "2026-09-17T11:44:00Z")
    e = ev[0]
    tid = str(e["home"]["team_id"])
    a = la.qb_assessment(tid, e["event_id"], qb.get(tid), latest, "2026-09-17T11:44:00Z")
    if qb.get(tid):
        assert a["status"] in (av.LIKELY_ACTIVE, av.UNCERTAIN) and a["confirmed"] is False
        assert a["flag"] in ("QB_NOT_CONFIRMED", "QB_RECENT_CHANGE")
    none = la.qb_assessment("zzz", e["event_id"], None, latest, "2026-09-17T11:44:00Z")
    assert none["flag"] == "QB_UNKNOWN" and none["status"] == av.UNVERIFIED and none["gate"] == "REVIEW"
    # a reported OUT quarterback: REVIEW on the team projection, status CONFIRMED_OUT for his own player rows
    q = {"name": "A Passer", "last_game_id": "g0", "last_game_utc": "2026-09-12T00:00:00Z", "prev_name": "A Passer", "changed_last_game": False, "games": 3}
    pid = la.qb_entity_id("1", q)
    inj_obs = av.Observation(sport="CFB", event_id="E1", event_start_utc="2026-09-19T00:00:00Z", entity_id=pid, entity_name="A Passer", team="T",
                             kind="injury", status="OUT", certainty="REPORTED", source="espn_core_injuries", captured_utc="2026-09-18T12:00:00Z")
    st_obs = av.Observation(sport="CFB", event_id="E1", event_start_utc="2026-09-19T00:00:00Z", entity_id=pid, entity_name="A Passer", team="T",
                            kind="starter", status="STARTING", certainty="PROJECTED", source=la.QB_SOURCE, captured_utc="2026-09-18T12:00:00Z")
    latest2 = av.asof([inj_obs.__dict__, st_obs.__dict__], "2026-09-18T13:00:00Z")
    a2 = la.qb_assessment("1", "E1", q, latest2, "2026-09-18T13:00:00Z")
    assert a2["flag"] == "QB_OUT_REPORTED" and a2["gate"] == "REVIEW" and a2["status"] == av.CONFIRMED_OUT
    f = gate_cfb.qb_finding(a2)
    assert f and f["severity"] == "REVIEW" and f["check"] == "quarterback"
    assert gate_cfb.qb_finding(la.qb_assessment("1", "E1", q, av.asof([st_obs.__dict__], "2026-09-18T13:00:00Z"), "2026-09-18T13:00:00Z")) is None
    # a changed starter is flagged even when healthy
    q2 = {**q, "changed_last_game": True, "prev_name": "B Backup"}
    a3 = la.qb_assessment("1", "E1", q2, av.asof([st_obs.__dict__], "2026-09-18T13:00:00Z"), "2026-09-18T13:00:00Z")
    assert a3["flag"] == "QB_RECENT_CHANGE" and "over-projected" in " ".join(a3["reasons"])


def test_qb_state_uses_only_games_before_now_and_detects_changes():
    rows = [{"team_id": "1", "team": "T", "game_id": f"g{i}", "kickoff_utc": f"2026-09-{5 + 7 * i:02d}T20:00:00Z", "points_for": 20,
             "primary_passer": p, "primary_passer_att": 30} for i, p in enumerate(["A", "A", "B"])]
    st = la.qb_state(rows, datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert st["1"]["name"] == "B" and st["1"]["changed_last_game"] and st["1"]["games"] == 3 and st["1"]["prev_name"] == "A"
    st2 = la.qb_state(rows, datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert st2["1"]["name"] == "A" and not st2["1"]["changed_last_game"] and st2["1"]["games"] == 2
    assert la.qb_state(rows, datetime(2026, 9, 1, tzinfo=timezone.utc)) == {}


def test_records_trim_dedupe_and_never_archive_after_start(tmp_path):
    probs = np.zeros(120)
    probs[20:40] = 1 / 20
    lo, pr_ = records.trim_pmf(0, probs)
    assert lo == 20 and len(pr_) == 20 and abs(sum(pr_) - 1) < 1e-12
    start = "2026-09-19T00:00:00Z"
    kw = dict(family="team_points", game_id="g1", start_utc=start, entity_id="1", entity_name="T", team="T", dist_lo=0, probs=probs,
              gate_status="PASS", participation="LIKELY_ACTIVE", readiness="READY", asof_utc="2026-09-16T00:00:00Z", version="v")
    a = records.forecast_record(now_utc="2026-09-17T12:00:00Z", **kw)
    assert abs(a["mean"] - 29.5) < 1e-9
    root = str(tmp_path)
    s1 = pr.archive_forecasts([a], root, now_utc="2026-09-17T12:00:00Z")
    assert s1["written"] == 1
    fresh, dropped = records.drop_unchanged([records.forecast_record(now_utc="2026-09-17T18:00:00Z", **kw)], pr.load_forecasts(root, "CFB"))
    assert fresh == [] and dropped == 1                               # unchanged inputs: nothing new archived
    changed = records.forecast_record(now_utc="2026-09-17T18:00:00Z", **{**kw, "participation": "UNCERTAIN"})
    fresh, dropped = records.drop_unchanged([changed], pr.load_forecasts(root, "CFB"))
    assert len(fresh) == 1
    late = records.forecast_record(now_utc="2026-09-19T00:30:00Z", **kw)
    assert pr.archive_forecasts([late], root, now_utc="2026-09-19T00:30:00Z")["late_rejected"] == 1


@pytest.mark.skipif(not os.path.isdir(os.path.join(REPO, "data", "scripting", "live", "frozen")), reason="frozen artifacts not present")
def test_incumbent_is_read_unchanged_from_real_frozen_artifacts():
    inc = incumbent.load(os.path.join(REPO, "data", "scripting", "live", "frozen"))
    assert len(inc) > 100
    a = next(v for v in inc.values() if v["home_points"] is not None)
    g = {"game_id": a["event_id"], "start_utc": a["kickoff_utc"], "home": a["home_name"], "away": a["away_name"],
         "home_id": a["home_id"], "away_id": a["away_id"]}
    recs, miss = incumbent.forecasts(inc, [g], "2026-01-01T00:00:00Z", "v")
    assert len(recs) == 4 and all(r["model_role"] == "incumbent" and r["probs"] == [] for r in recs)
    by = {(r["family"], r["entity_id"]): r["mean"] for r in recs}
    assert by[("team_points", a["home_id"])] == a["home_points"]
    assert abs(by[("game_total", "TOTAL")] - (a["home_points"] + a["away_points"])) < 0.2     # engine rounds each to 0.1
    assert by[("margin", "MARGIN")] == a["home_margin"]


def test_team_outcomes_only_from_final_games_and_orientation():
    sched = {"events": [
        {"id": "g1", "completed": True, "competitors": [{"home_away": "home", "team_id": "1"}, {"home_away": "away", "team_id": "2"}]},
        {"id": "g2", "completed": False, "competitors": [{"home_away": "home", "team_id": "3"}, {"home_away": "away", "team_id": "4"}]},
        {"id": "g3", "completed": True, "competitors": [{"home_away": "home", "team_id": "5"}, {"home_away": "away", "team_id": "6"}]}]}

    def row(g, t, pts, qb):
        return {"game_id": g, "team_id": t, "points_for": pts, "kickoff_utc": "2026-09-19T20:00:00Z", "primary_passer": qb}
    rows = [row("g1", "1", 31, "A"), row("g1", "2", 17, "B"), row("g2", "3", 10, "C"), row("g2", "4", 3, "D"), row("g3", "5", 7, "E")]
    outs, st = results.team_outcomes(rows, sched)
    d = {(o["entity_id"], o["family"]): o["actual"] for o in outs}
    assert d[("1", "team_points")] == 31 and d[("TOTAL", "game_total")] == 48 and d[("MARGIN", "margin")] == 14
    assert st["games_final"] == 1 and st["not_completed_in_schedule"] == 1 and st["games_one_row"] == 1
    assert all(o["game_id"] == "g1" and o["final"] for o in outs)
    seg = results.qb_segments(rows + [{"game_id": "g0", "team_id": "1", "points_for": 10, "kickoff_utc": "2026-09-12T20:00:00Z", "primary_passer": "Z"}])
    assert seg[("g1", "1")]["qb_changed"] is True and seg[("g0", "1")]["qb_changed"] is None


def test_coherent_totals_and_shadow_labels_in_a_projection_document():
    p = os.path.join(paths.RESULTS, "projections_latest.json")
    d = json.load(open(p))
    assert d["games"], "committed example projection should contain games"
    for g in d["games"]:
        tot = next(x for x in g["game"] if x["family"] == "game_total")
        mar = next(x for x in g["game"] if x["family"] == "margin")
        h, a = g["teams"][0]["mean"], g["teams"][1]["mean"]
        assert abs(tot["mean"] - (h + a)) < 0.06 and abs(mar["mean"] - (h - a)) < 0.06
    assert d["model"]["authority"] == "research_only"


# --------------------------------------------------------------------------- full replay (opt-in; needs the data cache)
@pytest.mark.skipif(not os.environ.get("PF_CFB_SMOKE"), reason="set PF_CFB_SMOKE=1 (needs the data cache; ~10+ min)")
def test_full_replay_smoke():
    from cfb.live import smoke
    smoke.main([])


# --------------------------------------------------------------------------- archive safety
def _git(d, *args):
    import subprocess
    return subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True, capture_output=True, text=True).stdout


def test_archive_check_after_rebase_flags_rewrites_of_immutable_files(tmp_path):
    from cfb.live import archive_check
    d = str(tmp_path)
    _git(d, "init", "-q")
    os.makedirs(os.path.join(d, "forecasts", "cfb", "2026-09-19"))
    os.makedirs(os.path.join(d, "projections", "cfb"))
    open(os.path.join(d, "forecasts", "cfb", "2026-09-19", "a.jsonl.gz"), "w").write("a")
    open(os.path.join(d, "projections", "cfb", "latest.json"), "w").write("1")
    _git(d, "add", "-A"); _git(d, "commit", "-qm", "base")
    base = _git(d, "rev-parse", "HEAD").strip()
    open(os.path.join(d, "forecasts", "cfb", "2026-09-19", "b.jsonl.gz"), "w").write("b")       # addition: fine
    open(os.path.join(d, "projections", "cfb", "latest.json"), "w").write("2")                  # rewritable view: fine
    _git(d, "add", "-A"); _git(d, "commit", "-qm", "add")
    assert archive_check.violations(d, base) == []
    open(os.path.join(d, "forecasts", "cfb", "2026-09-19", "a.jsonl.gz"), "w").write("tampered")
    _git(d, "add", "-A"); _git(d, "commit", "-qm", "rewrite")
    assert archive_check.violations(d, base)


def test_dry_run_never_touches_the_real_archive(tmp_path):
    import argparse
    from cfb.live import run
    real = str(tmp_path / "real")
    os.makedirs(real)
    _git(real, "init", "-q")
    open(os.path.join(real, "keep.txt"), "w").write("x")
    _git(real, "add", "-A"); _git(real, "commit", "-qm", "base")
    args = argparse.Namespace(stage="capture", archive=real, now="2026-09-17T11:43:00Z", dry_run=True, work=str(tmp_path / "w"),
                              football_dir=os.path.join(REPO, "data", "football", "2026"),
                              frozen_dir=os.path.join(REPO, "data", "scripting", "live", "frozen"), horizon_days=None,
                              no_players=True, results_until=None, summary_out=None)
    ctx = run.Ctx(args)
    out = run.stage_capture(ctx, get=recorded_get)
    assert out["snapshot"]["written"] > 0
    assert ctx.root != real and os.path.isdir(os.path.join(ctx.root, "availability", "cfb"))
    assert not os.path.exists(os.path.join(real, "availability"))
    assert _git(real, "status", "--porcelain").strip() == ""
    # the dry-run copy is a git repo too, so the guard stage works on it
    run.stage_guard(ctx)


def test_repo_job_capture_is_reused_once_per_observed_at_and_a_resolved_qb_status_is_kept():
    sched = json.load(open(os.path.join(REPO, "data", "football", "2026", "schedule.json")))
    ev = la.upcoming_events(sched, NOW, datetime(2026, 9, 22, 8, tzinfo=timezone.utc), replay=True)
    tid = str(ev[0]["home"]["team_id"])
    avail = {tid: {"status": "OBSERVED", "observed_at": "2026-09-17T10:00:00Z", "items": [{"athlete": "A Passer", "position": "QB", "status": "Questionable"}],
                   "qb_uncertain": True, "qb_name": "A Passer", "qb_status": "Questionable"},
             "bad": {"status": "NOT_OBSERVED", "observed_at": "2026-09-17T10:00:00Z", "items": None}}
    obs = la.repo_job_observations(avail, ev, None)
    kinds = {(o.kind, o.status, o.certainty) for o in obs if o.entity_id.startswith("name:")}
    assert ("injury", "QUESTIONABLE", "REPORTED") in kinds
    assert all(o.validate() == [] and o.captured_utc == "2026-09-17T10:00:00Z" for o in obs)
    assert la.repo_job_observations(avail, ev, "2026-09-17T10:00:00Z") == []        # already preserved: not repeated
    assert la.repo_job_observations(avail, ev, "2026-09-17T09:00:00Z")               # a newer observed_at is preserved
