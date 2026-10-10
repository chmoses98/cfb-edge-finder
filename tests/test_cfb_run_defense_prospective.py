"""Wave 2D Track B (CFB): run-defense prospective streams are append-only, pregame-frozen and isolated.

Proves (protocol docs/research/CFB_RUN_DEFENSE_PROSPECTIVE_PROTOCOL.md, prompt section 44):
* no historical backfill: nothing before activation is ever written;
* observations are frozen strictly before kickoff and carry P0 and the candidate but no outcome;
* settlement is append-only, idempotent, and never mutates the frozen observation;
* CFB-PROS-003 uses the exact CFB-PROS-001 run-defense definition through the Wave-2 builder;
* missing stats stay missing and no market price is inferred when absent;
* FCS-involved games are a separate research population; a missing P0 fails closed;
* the streams' counts are separate from Wave 2, and a failure never stops the conductor or reaches SIFT.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.control_prospective import capture as cap
from cfb_edge_finder.scripting.gamelog import TeamGame
from cfb_edge_finder.signal_discovery import run_defense_cycle as RD
from cfb_edge_finder.signal_discovery import wave2 as W
from cfb_edge_finder.signal_discovery import wave2_cycle as W2

ROOT = Path(__file__).resolve().parent.parent
KICKOFF = "2026-10-17T19:30:00Z"
GID = "900000001"


def at(minutes_before: float, kickoff: str = KICKOFF) -> str:
    return cap.iso(cap.parse_utc(kickoff) - timedelta(minutes=minutes_before))


def event(gid=GID, kickoff=KICKOFF, home="10", away="20", neutral=False):
    return {
        "id": gid,
        "date": kickoff.replace(":00Z", "Z"),
        "neutral_site": neutral,
        "competitors": [
            {"home_away": "home", "team_id": home, "location": f"Team{home}", "names": [f"Team{home}"]},
            {"home_away": "away", "team_id": away, "location": f"Team{away}", "names": [f"Team{away}"]},
        ],
    }


def tg(
    team,
    opp,
    div="fbs",
    odiv="fbs",
    gid="1",
    kickoff="2026-10-03T19:30:00Z",
    pf=None,
    pa=None,
    box=None,
    pbp=None,
    site="home",
):
    return TeamGame.from_dict(
        {
            "game_id": gid,
            "season": 2026,
            "week": 6,
            "kickoff_utc": kickoff,
            "team_id": team,
            "team": f"Team{team}",
            "opponent_id": opp,
            "opponent": f"Team{opp}",
            "team_division": div,
            "opponent_division": odiv,
            "site": site,
            "points_for": pf,
            "points_against": pa,
            "box": box or {},
            "pbp": pbp,
        }
    )


HISTORY = [tg("10", "30"), tg("20", "40"), tg("50", "60", odiv="fcs")]


def feature(z=1.5, eff=0.8, games=4):
    return {
        W.DSC001_FEATURE: W.DSC001_MEAN + z * W.DSC001_SD,
        "net.sustained_efficiency": eff,
        "net.rushing": 0.2,
        "net.passing": 0.1,
        "home_off_q.rush_success_rate": 0.5,
        "away_off_q.rush_success_rate": 0.1,
        "prior_games_home": games,
        "prior_games_away": games + 1,
        "history_rows": 900,
        "eligibility": None,
        "football_data_cutoff": "2026-10-17T08:00:00Z",
        "freshness": {"status": "FRESH"},
    }


def p0(gid):
    return {
        "status": "OK",
        "model_version": "0.5.0-early-season-talent-prior",
        "margin": 6.5,
        "total": 55.0,
        "raw_margin": 4.0,
        "c2_delta": 1.5,
        "talent_delta": 1.0,
        "sd": 17.0,
    }


def run(store, now, *, schedule=None, rows=HISTORY, provider=p0, fn=None, games=None):
    return RD.run(
        store_dir=store,
        season=2026,
        now=now,
        schedule=schedule if schedule is not None else [event()],
        team_rows=list(rows),
        p0_provider=provider,
        games=games,
        feature_fn=fn or (lambda *a, **k: feature()),
        code_sha="test",
    )


def ledger(store):
    path = store / "run_defense" / "2026" / "ledger.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def finals(home_pts=31, away_pts=17, box_h=None, box_a=None, pbp=None):
    return [
        tg(
            "10",
            "20",
            gid=GID,
            kickoff=KICKOFF,
            pf=home_pts,
            pa=away_pts,
            site="home",
            box=box_h if box_h is not None else {"possession_seconds": 1900, "pass_att": 25, "ints_thrown": 0},
            pbp=pbp,
        ),
        tg(
            "20",
            "10",
            gid=GID,
            kickoff=KICKOFF,
            pf=away_pts,
            pa=home_pts,
            site="away",
            box=box_a if box_a is not None else {"possession_seconds": 1700, "pass_att": 38, "ints_thrown": 2},
            pbp=pbp,
        ),
    ]


# --------------------------------------------------------------------------- registration


def test_protocol_hash_constants():
    assert hashlib.sha256((ROOT / RD.PROTOCOL_PATH).read_bytes()).hexdigest() == RD.PROTOCOL_SHA256
    assert len(RD.PROTOCOL_COMMIT) == 40
    assert RD.ACTIVATION_UTC == "2026-10-13T12:00:00Z" and RD.OBSERVE_FROM_MIN == 360.0
    rep = json.loads((ROOT / "data/scripting/validation/rush_projection/integration_report.json").read_text())
    assert round(RD.P1_BETA, 6) == rep["candidates"]["P1"]["beta_final_2015_2025_live"][0]
    assert rep["decision"]["decision"] == "REJECT"  # P1 is tracked as research only, never as production


def test_default_feature_is_the_wave2_builder_and_x_rd_is_pros001_z():
    assert RD.run.__kwdefaults__["feature_fn"] is None  # resolved to wave2_cycle.feature_for inside run()
    x = RD.frozen_features(feature(z=1.25))
    assert x["x_rd"] == pytest.approx(1.25, abs=1e-12)
    assert x[W.DSC001_FEATURE] == pytest.approx(W.DSC001_MEAN + 1.25 * W.DSC001_SD)
    assert W2.feature_for.__module__ == "cfb_edge_finder.signal_discovery.wave2_cycle"


def test_no_edge_confirmed_state_and_status_ladder():
    text = (ROOT / "src/cfb_edge_finder/signal_discovery/run_defense_cycle.py").read_text()
    assert "EDGE_CONFIRMED" not in text
    assert [RD.status_for(n, None) for n in (0, 49, 50, 99, 100, 399)] == [
        "PROSPECTIVE_TRACKING",
        "PROSPECTIVE_TRACKING",
        "EARLY_READ",
        "EARLY_READ",
        "INTERIM",
        "INTERIM",
    ]
    assert RD.status_for(400, True) == "REJECTED" and RD.status_for(400, False) == "REVIEW_REQUIRED"
    assert RD.next_review(0) == 50 and RD.next_review(120) == 200 and RD.next_review(400) is None


# --------------------------------------------------------------------------- pregame freeze, no backfill


def test_no_backfill_before_activation(tmp_path):
    old = "2026-10-11T19:30:00Z"
    for now in (at(120, old), at(-300, old)):
        run(tmp_path, now, schedule=[event(kickoff=old)])
    assert ledger(tmp_path) == []


def test_observation_only_inside_the_pregame_window(tmp_path):
    run(tmp_path, at(400))
    assert ledger(tmp_path) == []  # too early
    run(tmp_path, at(300))
    rows = ledger(tmp_path)
    assert [r["record"] for r in rows] == ["OBSERVATION"] and rows[0]["minutes_before"] == 300.0
    run(tmp_path, at(200))
    assert len(ledger(tmp_path)) == 1  # frozen once


def test_unobserved_game_is_missed_never_observed_after_kickoff(tmp_path):
    run(tmp_path, at(-10))
    rows = ledger(tmp_path)
    assert [r["record"] for r in rows] == ["MISSED"] and rows[0]["reason"] == "NOT_OBSERVED_BEFORE_KICKOFF"
    run(tmp_path, at(-20), rows=HISTORY + finals())
    assert [r["record"] for r in ledger(tmp_path)] == ["MISSED"]  # never settled, never re-observed


def test_observation_holds_p0_and_candidate_and_no_outcome(tmp_path):
    run(tmp_path, at(150))
    obs = ledger(tmp_path)[0]
    assert obs["p0"]["margin"] == 6.5 and obs["p0_status"] == "OK"
    assert obs["p1"]["delta"] == pytest.approx(RD.P1_BETA * 1.5)
    assert obs["p1"]["total"] == obs["p0"]["total"] and obs["p1"]["sd"] == obs["p0"]["sd"]
    text = json.dumps(obs)
    for banned in ("actual", "points_for", "home_points", "market_implied", "o.home"):
        assert banned not in text


# --------------------------------------------------------------------------- settlement


def test_settlement_append_only_idempotent_and_never_mutates_the_observation(tmp_path):
    run(tmp_path, at(150))
    path = tmp_path / "run_defense" / "2026" / "ledger.jsonl"
    before = path.read_text()
    run(tmp_path, at(-60))  # after kickoff but no final in the log yet
    assert path.read_text() == before
    run(tmp_path, at(-300), rows=HISTORY + finals())
    after = path.read_text()
    assert after.startswith(before)
    rows = ledger(tmp_path)
    assert [r["record"] for r in rows] == ["OBSERVATION", "SETTLEMENT"]
    s = rows[1]
    assert s["actual_margin_home"] == 14.0 and s["mechanism"]["opponent_pass_att_diff"] == 13.0
    assert s["mechanism"]["opponent_ints_diff"] == 2.0 and s["mechanism"]["possession_diff_home"] == 200.0
    run(tmp_path, at(-400), rows=HISTORY + finals(home_pts=0, away_pts=50))
    assert path.read_text() == after  # repeated settlement writes nothing, even if the log changes


def test_missing_stats_stay_missing_and_no_market_is_inferred(tmp_path):
    run(tmp_path, at(150))
    run(tmp_path, at(-300), rows=HISTORY + finals(box_h={"pass_att": 20}, box_a={}))
    s = ledger(tmp_path)[1]
    assert s["mechanism"]["possession_diff_home"] is None and s["mechanism"]["opponent_ints_diff"] is None
    assert s["mechanism"]["drives_diff"] is None
    assert s["market"]["market_implied_margin_home"] is None and s["market"]["status"] == "NO_KALSHI_GAME"


def test_market_centre_absent_when_wave2_has_no_capture(tmp_path):
    game = cap.SlateGame(
        game_key="26OCT17XY", kickoff_utc=KICKOFF, kickoff_source="espn_schedule", title="t", espn_event_id=GID
    )
    run(tmp_path, at(150), games=[game])
    run(tmp_path, at(-300), rows=HISTORY + finals(), games=[game])
    m = ledger(tmp_path)[1]["market"]
    assert m["market_implied_margin_home"] is None and m["status"] != "OK"


# --------------------------------------------------------------------------- populations and fail-closed


def test_fcs_games_are_a_separate_research_population(tmp_path):
    sched = [event(gid="900000002", home="50", away="60")]
    run(tmp_path, at(150), schedule=sched)
    obs = ledger(tmp_path)[0]
    assert obs["population"] == RD.FCS_RESEARCH and obs["p1"]["delta"] == 0.0 and not obs["p1"]["applied"]
    rep = RD.status_report(ledger(tmp_path), now=at(0), season=2026)
    assert all(s["eligible"] == 0 for s in rep["streams"].values())
    assert rep["fcs_research_c2c_f4"]["observed"] == 1 and rep["fcs_research_c2c_f4"]["pooled_with_primary"] is False


def test_fcs_vs_fcs_is_never_recorded(tmp_path):
    rows = [tg("70", "80", div="fcs", odiv="fcs")]
    run(tmp_path, at(150), schedule=[event(gid="900000003", home="70", away="80")], rows=rows)
    assert ledger(tmp_path) == []


def test_missing_p0_fails_closed(tmp_path):
    run(tmp_path, at(150), provider=None)
    obs = ledger(tmp_path)[0]
    assert obs["p0_status"] == "UNAVAILABLE" and obs["p1"] is None
    run(tmp_path, at(-300), rows=HISTORY + finals(), provider=None)
    rep = RD.status_report(ledger(tmp_path), now=at(-300), season=2026)
    assert rep["streams"][RD.MODEL_PROS_001]["eligible"] == 0 and rep["streams"][RD.PROS_003]["eligible"] == 0
    assert rep["streams"][RD.MECH_PROS_001]["settled"] == 1  # the mechanism stream needs only the feature


def test_provider_exception_fails_closed(tmp_path):
    def boom(gid):
        raise RuntimeError("no state")

    run(tmp_path, at(150), provider=boom)
    assert ledger(tmp_path)[0]["p0_status"] == "UNAVAILABLE"


def test_missing_feature_gives_no_correction(tmp_path):
    run(tmp_path, at(150), fn=lambda *a, **k: {**feature(), W.DSC001_FEATURE: None})
    obs = ledger(tmp_path)[0]
    assert obs["features"]["x_rd"] is None and obs["p1"]["delta"] == 0.0 and obs["p1"]["margin"] == 6.5


def test_c2c_f3_shadow_is_recorded_and_inert(tmp_path):
    run(tmp_path, at(150))
    obs = ledger(tmp_path)[0]
    assert obs["features"]["x_rd_unc_c2c_f3"] == pytest.approx(1.5 * 4 / 7)
    assert obs["p1"]["delta"] == pytest.approx(RD.P1_BETA * 1.5)  # the shadow never changes the candidate


def test_status_report_metrics_after_settlement(tmp_path):
    run(tmp_path, at(150))
    run(tmp_path, at(-300), rows=HISTORY + finals())
    rep = json.loads((tmp_path / "run_defense/reports/2026/run_defense_status.json").read_text())
    m = rep["streams"][RD.MODEL_PROS_001]
    p1 = 6.5 + RD.P1_BETA * 1.5
    assert m["settled"] == 1 and m["primary_metric"]["value"] == pytest.approx(abs(6.5 - 14) - abs(p1 - 14))
    assert m["status"] == "PROSPECTIVE_TRACKING" and m["next_review"] == 50
    assert set(rep["streams"]) == set(RD.STREAMS)


def test_status_report_carries_no_betting_language(tmp_path):
    from cfb_edge_finder.control_prospective import BANNED_WORDS

    run(tmp_path, at(150))
    run(tmp_path, at(-300), rows=HISTORY + finals())
    text = (tmp_path / "run_defense/reports/2026/run_defense_status.json").read_text().lower()
    text = text.replace("no bet, no stake, no badge", "")
    for word in BANNED_WORDS:
        assert not re.search(rf"(?<![a-z0-9_+]){re.escape(word)}(?![a-z0-9_])", text), word


# --------------------------------------------------------------------------- isolation


def test_counts_separate_and_wave2_store_untouched(tmp_path):
    w2 = tmp_path / "wave2" / "2026"
    w2.mkdir(parents=True)
    (w2 / "ledger.jsonl").write_text('{"row_id":"x"}\n')
    (w2 / "spread_attempts.jsonl").write_text("")
    snap = {p: p.read_bytes() for p in w2.iterdir()}
    run(tmp_path, at(150))
    run(tmp_path, at(-300), rows=HISTORY + finals())
    assert {p: p.read_bytes() for p in w2.iterdir()} == snap
    written = {p.relative_to(tmp_path).parts[0] for p in tmp_path.rglob("*") if p.is_file()}
    assert written == {"wave2", "run_defense"}


def test_no_production_module_imports_the_streams():
    for p in (ROOT / "src" / "cfb_edge_finder").rglob("*.py"):
        if "signal_discovery" in p.parts:
            continue
        assert "run_defense_cycle" not in p.read_text(encoding="utf-8"), p
    text = (ROOT / "src/cfb_edge_finder/signal_discovery/run_defense_cycle.py").read_text()
    for banned in ("sizing", "recommend", "router", "bankroll", "betting_card", "sift_export"):
        assert f"import {banned}" not in text and f".{banned} import" not in text


def _conductor():
    spec = importlib.util.spec_from_file_location(
        "cfb_research_conductor_rd", ROOT / "scripts" / "cfb_research_conductor.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cfb_research_conductor_rd"] = mod
    spec.loader.exec_module(mod)
    return mod


def _main(tmp_path):
    main = tmp_path / "main"
    for sub in (
        "data/live",
        "data/football/2026",
        "data/scripting/live/sift",
        "data/scripting/live/frozen_v2",
        "app/latest",
    ):
        (main / sub).mkdir(parents=True)
    (main / "data/live/cfb_market_catalog.json").write_text(json.dumps({"capture": {}, "games": []}))
    (main / "data/football/2026/schedule.json").write_text(json.dumps({"events": []}))
    (main / "app/latest/events.json").write_text(json.dumps({"items": []}))
    (tmp_path / "ledger" / "2026").mkdir(parents=True)
    return main


def test_conductor_survives_a_run_defense_failure(tmp_path, monkeypatch):
    conductor = _conductor()

    def boom(**kwargs):
        raise RuntimeError("run-defense exploded")

    monkeypatch.setattr(conductor.run_defense_cycle, "run", boom)
    out = conductor.run_cycle(
        main_dir=_main(tmp_path),
        ledger_dir=tmp_path / "ledger",
        store_dir=tmp_path / "store",
        now=at(120),
        fetch=lambda: [],
    )
    assert out["run_defense"]["status"] == "SYSTEM_FAILURE" and "exploded" in out["run_defense"]["error"]
    assert "status" not in out["wave2"] or out["wave2"]["status"] != "SYSTEM_FAILURE"
    sift = (tmp_path / "store" / "signals" / "cfb_research_signals.json").read_text()
    assert "PROS-003" not in sift and "run_defense" not in sift and "MODEL-PROS" not in sift


def test_conductor_without_p0_inputs_runs_and_fails_closed(tmp_path):
    conductor = _conductor()
    assert conductor.build_p0_provider(None, 2026) is None
    assert conductor.build_p0_provider(tmp_path / "missing", 2026) is None
    out = conductor.run_cycle(
        main_dir=_main(tmp_path),
        ledger_dir=tmp_path / "ledger",
        store_dir=tmp_path / "store",
        now=at(120),
        fetch=lambda: [],
        p0_dir=tmp_path / "missing",
    )
    assert out["run_defense"]["errors"] == [] and out["run_defense"]["ledger_rows"] == 0


def test_live_p0_unknown_game_is_unavailable():
    live = RD.LiveP0(lines_loader=list, talent_by_team={}, schedule_games=[], model_version="m", provenance={})
    assert live("123")["status"] == "UNAVAILABLE"
