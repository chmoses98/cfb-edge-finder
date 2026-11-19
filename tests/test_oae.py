"""OAE challenger integration: frozen provenance, research parity, pregame safety, flag semantics, additive block."""
from __future__ import annotations

import copy
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cfb_edge_finder import oae
from cfb_edge_finder.oae import block as B
from cfb_edge_finder.oae import forecast as fc
from cfb_edge_finder.oae import frozen as fz
from cfb_edge_finder.oae import ratings as rt

FIX = Path(__file__).parent / "fixtures" / "oae"
FBS = {"1", "4", "5", "8", "9", "12", "15", "17", "18", "37", "151"}


def _rows_2026():
    raw = gzip.decompress((FIX / "team_games_2026_research.jsonl.gz").read_bytes())
    return [json.loads(x) for x in raw.splitlines()]


def _day_start(date: str) -> datetime:
    return datetime.fromisoformat(date).replace(tzinfo=UTC) + rt.LOCAL_OFFSET


def test_flag_is_off_unless_exactly_one():
    assert oae.FLAG == "CFB_OAE_FORECASTS"
    assert not oae.enabled({})
    for v in ("", "0", "true", "yes", "on", " 2"):
        assert not oae.enabled({oae.FLAG: v})
    assert oae.enabled({oae.FLAG: "1"})


def test_frozen_assets_match_provenance_and_research_sources():
    prov = fz.provenance()
    assert prov["sources"]["p307"]["commit"].startswith("8742f2a9")
    assert prov["sources"]["p308"]["commit"].startswith("7ebddfc6")
    F = fz.load()                                   # verifies every sha256
    assert F.season == 2026
    assert F.p307["params"] == {"lam": 1.0, "w_prev": 0.1, "w_prev2": 0.1, "decay_k": 3.0, "layer_alpha": 1.0}
    assert F.p15["fg"]["layer"]["margin"]["b"] == pytest.approx(F.p307["layer"]["margin"]["b"], abs=1e-12)
    assert prov["files"]["period_nb.npz"]["source_sha256"] == F.p15["period_nb_sha256"]
    assert {r["season"] for r in F.prior_rows} == {2024, 2025}


def test_tampered_asset_fails_closed(tmp_path, monkeypatch):
    import shutil
    d = tmp_path / "frozen"
    shutil.copytree(fz.FROZEN_DIR, d)
    p = d / "preseason.json"
    p.write_text(p.read_text().replace("2026", "2027", 1))
    monkeypatch.setattr(fz, "FROZEN_DIR", d)
    fz.load.cache_clear()
    fz.provenance.cache_clear()
    try:
        with pytest.raises(fz.FrozenAssetError):
            fz.load()
    finally:
        fz.load.cache_clear()
        fz.provenance.cache_clear()


def test_parity_with_research_frozen_forecasts():
    """Reproduces the research code's forecasts (fixture written by research/oae_integration/build_frozen_assets.py)."""
    exp = json.loads((FIX / "parity_expected.json").read_text())["games"]
    assert len(exp) >= 12
    F = fz.load()
    rows = F.prior_rows + _rows_2026()
    p = F.p307["params"]
    worst = 0.0
    for e in exp:
        R, gp, _ = rt.rate_date(rows, 2026, e["date"], _day_start(e["date"]), p)
        f = rt.game_features(R, gp, F.preseason, e["home"], e["away"], True, True, e["neutral"])
        assert (f["n_h"], f["n_a"]) == (e["n_h"], e["n_a"])
        fg = fc.full_game(f, F.p15["fg"]["layer"], F.p15["fg"]["sigma"])
        got = {"margin": fg["margin"], "total": fg["total"], "sd_m": fg["sd_m"], "p_home_win": fg["p_home_win"]}
        J = fc.joint_from_margin_total(fg["margin"], fg["sd_m"], fg["total"], fg["sd_t"], fg["rho"], 200)
        pairs = [(got[k], e[k]) for k in got] + [(fc.p_margin_gt(J, float(k)), v) for k, v in e["fg_spread"].items()]
        for seg, pe in e["periods"].items():
            fp = F.p15["periods"][seg]
            mh, ma = fc.period_means(f, fg, seg, fp["coef"])
            Jp = fc.period_joint(B._nb(F, seg), mh, ma, fp["tie_scale"])
            pairs += [(mh, pe["mh"]), (ma, pe["ma"]), (fc.p_margin_gt(Jp, 0.5), pe["p_home_win"])]
            pairs += [(fc.p_margin_gt(Jp, float(k)), v) for k, v in pe["spread"].items()]
            pairs += [(fc.p_total_gt(Jp, float(k)), v) for k, v in pe["total"].items()]
        worst = max(worst, max(abs(a - b) for a, b in pairs))
    print("parity max abs diff", worst)
    assert worst < 1e-8, worst


def test_training_rows_are_strictly_pregame():
    rows = fz.load().prior_rows + _rows_2026()
    now = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
    tr = rt.training_rows(rows, 2026, "2026-10-10", now)
    assert tr and all(r["date"] < "2026-10-10" and rt.parse_utc(r["kickoff_utc"]) < now for r in tr)
    assert {r["season"] for r in tr} == {2024, 2025, 2026}
    # a forecast made on Tuesday for Saturday cannot see Thursday's games, even if the log already holds them
    early = rt.training_rows(rows, 2026, "2026-10-10", datetime(2026, 10, 6, 12, tzinfo=UTC))
    assert max(rt.parse_utc(r["kickoff_utc"]) for r in early) < datetime(2026, 10, 6, 12, tzinfo=UTC)
    assert len(early) < len(tr)


def test_future_rows_do_not_change_a_forecast():
    """Truncation test: deleting every team-game on/after the game date leaves the forecast unchanged."""
    F = fz.load()
    rows = F.prior_rows + _rows_2026()
    d, now = "2026-10-17", _day_start("2026-10-17")
    trunc = [r for r in rows if r["date"] < d]
    poisoned = rows + [dict(r, date="2026-10-16", kickoff_utc="2026-10-17T12:00:00Z", points_for=999.0)
                       for r in rows if r["season"] == 2026][:20]
    a = rt.rate_date(rows, 2026, d, now, F.p307["params"])
    b = rt.rate_date(trunc, 2026, d, now, F.p307["params"])
    c = rt.rate_date(poisoned, 2026, d, now, F.p307["params"])      # rows kicking off after `now` are dropped
    for m in ("pts", "pp_q1"):
        assert a[0][m]["mu"] == pytest.approx(b[0][m]["mu"], abs=1e-12) == pytest.approx(c[0][m]["mu"], abs=1e-12)
    assert a[2] == b[2] == c[2]


def test_compact_from_log_matches_research_rows_and_checks_periods():
    raw = {"game_id": "1", "season": 2026, "kickoff_utc": "2026-09-06T01:30:00Z", "team_id": "10", "opponent_id": "20",
           "site": "home", "team_division": "fbs", "opponent_division": "fbs", "points_for": 24, "points_against": 17,
           "box": {"plays": 70, "total_yards": 400}, "pbp": {"success_plays": 30, "scrim_plays": 68, "drives": 12,
                                                             "drive_points": 24}, "period_points": [7, 10, 0, 7]}
    opp = dict(raw, team_id="20", opponent_id="10", site="away", points_for=17, points_against=24,
               period_points=[0, 7, 3, 7])
    c = rt.load_log_rows([raw, opp])
    assert c[0]["date"] == "2026-09-05"                      # UTC - 6 h local game day
    assert [c[0][f"pp_q{i}"] for i in range(1, 5)] == [7, 10, 0, 7]
    bad = rt.load_log_rows([dict(raw, period_points=[7, 10, 0]), opp])
    assert all(r["pp_q1"] is None for r in bad)              # both sides dropped when either side is invalid


def _schedule_and_games(date="2026-11-21"):
    rows = _rows_2026()
    pairs = {}
    for r in rows:
        if r["team_division"] == "fbs" and r["opponent_division"] == "fbs" and r["site"] == "home":
            pairs.setdefault(r["team_id"], r["opponent_id"])
    teams = list(pairs.items())[:3]
    events, games = [], []
    for i, (h, a) in enumerate(teams):
        gid = f"9{i:08d}"
        events.append({"id": gid, "neutral_site": i == 2, "competitors": [
            {"home_away": "home", "team_id": h, "conference_id": "8"},
            {"home_away": "away", "team_id": a, "conference_id": "1" if i else "999"}]})
        games.append({"game_id": gid, "home_id": h, "away_id": a, "start_utc": f"{date}T17:00:00Z", "home": "H",
                      "away": "A", "teams": [], "game": []})
    return {"season": 2026, "events": events}, games, rows


def test_block_is_additive_versioned_and_not_activated():
    sched, games, rows = _schedule_and_games()
    doc = {"schema": "sift.projections.v1", "games": copy.deepcopy(games), "model": {"version": "pf-x"}}
    before = json.dumps(doc, sort_keys=True)
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)
    blk = B.build_block(doc["games"], sched, rows, now, FBS)
    assert json.dumps(doc, sort_keys=True) == before         # input artifact untouched
    assert blk["model_version"] == oae.MODEL_VERSION and blk["status"] == "not_activated" and blk["activated"] is False
    assert [s["reason"] for s in blk["skipped"]] == ["not FBS vs FBS (outside validation)"]
    assert len(blk["games"]) == 2 and blk["games"][1]["neutral"] is True
    g = blk["games"][0]
    assert set(g["fg"]) == {"expected_margin", "spread_home", "sigma_margin", "p_home_win", "p_away_win",
                            "spread_ladder"}
    assert "total" not in json.dumps(g["fg"]) and "team_total" not in json.dumps(g)
    assert set(g["periods"]) == {"q1", "q2", "q3", "q4", "h1"}
    assert "total_ladder" not in g["periods"]["q1"] and "total_ladder" not in g["periods"]["q3"]
    assert 0 < g["fg"]["p_home_win"] < 1 and g["n_training_team_games"] > 3000
    json.dumps(blk)                                           # serialisable


def test_block_fails_closed_outside_frozen_season():
    sched, games, rows = _schedule_and_games()
    blk = B.build_block(games, dict(sched, season=2027), rows, datetime(2026, 11, 20, tzinfo=UTC), FBS)
    assert blk["games"] == [] and all("no frozen inputs" in s["reason"] for s in blk["skipped"])
    assert blk["available"] is False and "season 2027 not covered" in blk["unavailable_reason"]


def test_started_games_are_skipped():
    sched, games, rows = _schedule_and_games()
    blk = B.build_block(games, sched, rows, datetime(2026, 11, 21, 17, tzinfo=UTC) + timedelta(seconds=1), FBS)
    assert blk["games"] == [] and {s["reason"] for s in blk["skipped"]} >= {"already started"}


def test_settlement_basis_and_tie_handling_match_the_validation():
    """Q1-Q4/1H are regulation-only (period_points[0..3]); FG includes OT; period winner is 3-way with no tie mass."""
    sched, games, rows = _schedule_and_games()
    blk = B.build_block(games, sched, rows, datetime(2026, 11, 20, 12, tzinfo=UTC), FBS)
    assert set(blk["settlement_basis"]) == set(blk["families"]) == {"fg", "q1", "q2", "q3", "q4", "h1"}
    assert "INCLUDING overtime" in blk["settlement_basis"]["fg"]
    assert all("overtime EXCLUDED" in blk["settlement_basis"][q] for q in ("q1", "q2", "q3", "q4"))
    assert "Q1 + Q2" in blk["settlement_basis"]["h1"] and "3-way" in blk["period_winner_basis"]
    g = blk["games"][0]
    assert g["fg"]["p_home_win"] + g["fg"]["p_away_win"] == pytest.approx(1.0, abs=2e-4)
    q1 = g["periods"]["q1"]
    assert 0.03 < 1 - q1["p_home_win"] - q1["p_away_win"] < 0.4          # tie mass left out (both sides settle NO)
    # an OT game: regulation quarters come from the first four periods only, the 5th (OT) never enters Q4
    raw = {"game_id": "9", "season": 2026, "kickoff_utc": "2026-10-03T16:00:00Z", "team_id": "1", "opponent_id": "2",
           "site": "home", "points_for": 31, "points_against": 24, "period_points": [7, 7, 3, 7, 7]}
    c = rt.compact_from_log(raw)
    assert (c["pp_q1"], c["pp_q2"], c["pp_q3"], c["pp_q4"]) == (7, 7, 3, 7) and c["points_for"] == 31


def test_preseason_snapshot_is_bound_to_the_frozen_season():
    """Talent/returning production is a pre-opener 2026 snapshot (research: identical to the 2026-08-28 fetch); the
    block refuses every other season, so the table can never be applied to a season it post-dates."""
    F = fz.load()
    assert F.season == 2026 and len(F.preseason) == fz.provenance()["files"]["preseason.json"]["teams"]
    assert max(r["season"] for r in F.prior_rows) < F.season


def test_forecast_at_kickoff_is_skipped_not_forecast():
    sched, games, rows = _schedule_and_games()
    g = games[1]                                                              # an FBS-vs-FBS game
    blk = B.build_block([g], sched, rows, rt.parse_utc(g["start_utc"]), FBS)   # now == kickoff
    assert blk["games"] == [] and blk["skipped"] == [{"game_id": g["game_id"], "reason": "already started"}]


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 2"])
def test_operating_key_is_off_unless_exactly_one(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(oae.OPERATING_FLAG, raising=False)
    else:
        monkeypatch.setenv(oae.OPERATING_FLAG, value)
    sched, games, rows = _schedule_and_games()
    blk = B.build_block(games, sched, rows, datetime(2026, 11, 20, 12, tzinfo=UTC), FBS)
    assert {k: blk[k] for k in oae.AUTHORITY} == oae.AUTHORITY
    assert oae.unavailable_block("x")["status"] == "not_activated"


def test_operating_key_declares_the_operating_forecast_without_bet_authority(monkeypatch):
    sched, games, rows = _schedule_and_games()
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)
    shadow = B.build_block(copy.deepcopy(games), sched, rows, now, FBS)
    monkeypatch.setenv(oae.OPERATING_FLAG, "1")
    blk = B.build_block(copy.deepcopy(games), sched, rows, now, FBS)
    assert blk["status"] == "activated" and blk["activated"] is True and blk["operating_forecast"] is True
    assert blk["role"] == "operating" and blk["bet_authority"] is False
    # the forecasts themselves are identical: only the declaration changes
    assert blk["games"] == shadow["games"] and blk["model_version"] == shadow["model_version"] == oae.MODEL_VERSION
    monkeypatch.delenv(oae.OPERATING_FLAG)  # rollback
    assert B.build_block(copy.deepcopy(games), sched, rows, now, FBS)["status"] == "not_activated"
