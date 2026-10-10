"""Wave 2E (CFB): base-projection repair study is leakage-safe and leaves production untouched.

Current-season ingestion (protocol 6-7, prompt 43): prior seasons load; current-season completed games load through
the ESPN bridge with CFBD identity / orientation / neutral site / classification; the target and future games are
excluded; a later week sees earlier completed games; training counts rise; ratings and projections update; an
earlier snapshot never sees the future; FCS handling is the existing pooled treatment.

Calibration (prompt 44): fold coefficients use strictly earlier seasons only; no market field enters; totals and
variance are invariant; the frozen C.2 artifact reproduces; metrics are deterministic.

Rush retest (prompt 45): exact Wave-2D features; refit against the repaired base's error; FBS-vs-FBS only; G6 is
enforced unchanged; nothing promotes automatically; prospective streams untouched.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest
from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.margin_correction_artifact import FROZEN_MARGIN_CORRECTION_PARAMS
from cfb_edge_finder.scripting.gamelog import TeamGame
from cfb_edge_finder.signal_discovery import rush_projection as R

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "scripting" / "validation" / "base_repair"
W2D = ROOT / "data" / "scripting" / "validation" / "rush_projection"
REPORT = OUT / "base_repair_report.json"
HAVE = REPORT.exists() and (W2D / "p0_games_2014_2025.jsonl.gz").exists()
need_outputs = pytest.mark.skipif(not HAVE, reason="study outputs not built")

#: Files Wave 2E must not change (decision KEEP_CURRENT_BASE / REJECT_RUSH): production model + prospective streams.
PINNED = {
    "src/cfb_edge_finder/modeling/score_model.py": "7c7e440781430cd577cd4b72d2bf5264ddfd619fbe996e5fd3084bb4a4f32722",
    "src/cfb_edge_finder/modeling/ratings.py": "21f96a299406c16be4c4af5115905e4809b9c041be5f509e7162865aabdc1deb",
    "src/cfb_edge_finder/modeling/talent_prior.py": "d2b12433b676f35451661a31e199b6a6e39c9f98f271a8f4c4a80389ab1ec0dc",
    "src/cfb_edge_finder/modeling/margin_correction_artifact.py": (
        "37c22f24a27ee927939dba669ea821c78ec25310f97c95c0ad9072e691ccb45e"
    ),
    "src/cfb_edge_finder/kalshi/game_projection_cache.py": (
        "ebfca09d9beed87203d058908986a59da6f4152f856877ecf79630c368ea4d38"
    ),
    "scripts/research_scan_and_capture.py": "846c04e4d6925d63731d86e7290e5c22d5778788ec0ff13a6844f63cee776a92",
    "scripts/capture_kalshi_cfb_snapshot.py": "325bb87512f7552ace94fa1f794908fad1b0d372e34e123c3f2fe3d16a35e0b1",
    "data/scripting/validation/rush_projection/integration_report.json": (
        "0effb3871949aece832011771a4d4b1e8a1eb54cbf9a0e900f66fc9204cc32fb"
    ),
    "data/scripting/validation/rush_projection/p0_games_2014_2025.jsonl.gz": (
        "c0c40840f1b4b4299657d5c1a0d6eeb2a6f31b1421d18ddeec9d1ec6e17928da"
    ),
    "docs/research/CFB_RUN_DEFENSE_PROSPECTIVE_PROTOCOL.md": (
        "576eb022d6bbfea0022aa0f1153dc87eaeda251cf33233783bc09b49bf198239"
    ),
}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def build():
    return _load("base_repair_cfb_t", "scripts/base_repair_cfb.py")


@pytest.fixture(scope="module")
def analyze():
    return _load("base_repair_cfb_analyze_t", "scripts/base_repair_cfb_analyze.py")


# --------------------------------------------------------------------------- synthetic data


def _line(season, week, team, opp, pts, opp_pts, plays, home, gid, *, tc="fbs", oc="fbs", neutral=False):
    return TeamGameLine(
        source_game_id=gid,
        season=season,
        week=week,
        is_postseason=False,
        team_id=team,
        opponent_id=opp,
        team_classification=tc,
        opponent_classification=oc,
        is_home=home,
        is_neutral_site=neutral,
        team_points=pts,
        opponent_points=opp_pts,
        team_plays=plays,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


TEAMS = ["alabama", "auburn", "georgia", "florida", "texas", "utsa"]


def _prior(seed=3):
    rng = np.random.default_rng(seed)
    lines, gid = [], 0
    for season in (2024, 2025):
        for week in range(1, 9):
            order = list(rng.permutation(TEAMS))
            for i in range(0, 6, 2):
                h, a = order[i], order[i + 1]
                hp, ap = int(rng.integers(10, 45)), int(rng.integers(10, 45))
                gid += 1
                lines.append(_line(season, week, h, a, hp, ap, 70, True, f"p{gid}"))
                lines.append(_line(season, week, a, h, ap, hp, 70, False, f"p{gid}"))
    return lines


def _current(texas_dominant=True):
    """Weeks 1-5 of 2026: texas blows everyone out, utsa loses big (information the prior lacks)."""
    lines, gid = [], 0
    sched = [
        ("texas", "alabama"),
        ("utsa", "georgia"),
        ("texas", "auburn"),
        ("utsa", "florida"),
        ("texas", "georgia"),
        ("utsa", "alabama"),
        ("texas", "florida"),
        ("utsa", "auburn"),
        ("texas", "auburn"),
        ("utsa", "georgia"),
    ]
    for i, (a, b) in enumerate(sched):
        week = i // 2 + 1
        gid += 1
        ap, bp = (52, 3) if a == "texas" and texas_dominant else (3, 49)
        lines.append(_line(2026, week, a, b, ap, bp, 70, True, f"c{gid}"))
        lines.append(_line(2026, week, b, a, bp, ap, 70, False, f"c{gid}"))
    return lines


def _req(week, home="texas", away="utsa"):
    return GameProjectionRequest("g", home, away, "fbs", "fbs", False, 2026, week, n_simulations=200, seed=0)


# --------------------------------------------------------------------------- current-season ingestion (43)


def test_week6_projection_uses_completed_weeks_1_to_5_and_earlier_snapshot_does_not():
    prior, cur = _prior(), _current()
    stale = GameProjectionCache(prior).get_or_build(_req(6))
    live = GameProjectionCache(prior + cur)
    w1, w6 = live.get_or_build(_req(1)), live.get_or_build(_req(6))
    assert w1.training_rows == stale.training_rows  # week 1: nothing from 2026 is admissible yet
    assert w1.projection.raw_expected_margin == stale.projection.raw_expected_margin
    assert w6.training_rows == stale.training_rows + 20  # weeks 1-5: ten games, two rows each
    assert w6.projection.raw_expected_margin > stale.projection.raw_expected_margin + 1.0  # evidence moved it


def test_stale_behaviour_is_week_invariant_and_fixed_behaviour_is_not():
    prior, cur = _prior(), _current()
    stale = GameProjectionCache(prior)
    assert (
        stale.get_or_build(_req(3)).projection.expected_margin == stale.get_or_build(_req(7)).projection.expected_margin
    )
    live = GameProjectionCache(prior + cur)
    assert (
        live.get_or_build(_req(3)).projection.expected_margin != live.get_or_build(_req(7)).projection.expected_margin
    )


def test_future_and_target_games_never_enter_an_earlier_snapshot():
    prior, cur = _prior(), _current()
    base = GameProjectionCache(prior + cur).get_or_build(_req(3)).projection.expected_margin
    future = [_line(2026, w, "utsa", "texas", 70, 0, 70, True, f"f{w}") for w in (3, 4, 9)]  # own week + later
    future += [_line(2026, w, "texas", "utsa", 0, 70, 70, False, f"f{w}") for w in (3, 4, 9)]
    doctored = GameProjectionCache(prior + cur + future).get_or_build(_req(3)).projection.expected_margin
    assert doctored == base


def test_same_data_reuses_and_changed_history_rebuilds():
    prior, cur = _prior(), _current()
    c = GameProjectionCache(prior + cur)
    c.get_or_build(_req(4))
    c.get_or_build(_req(4, home="alabama", away="auburn"))
    assert c.ratings_fits == 1  # same as-of + same data -> one fit reused
    c.get_or_build(_req(5))
    assert c.ratings_fits == 2  # later as-of -> rebuilt
    other = GameProjectionCache(prior + _current(texas_dominant=False))
    assert other.get_or_build(_req(4)).projection.expected_margin != c.get_or_build(_req(4)).projection.expected_margin


def _tg(gid, team, opp, pf, pa, site, plays=68, div="fbs", odiv="fbs"):
    return TeamGame.from_dict(
        {
            "game_id": gid,
            "season": 2026,
            "week": 2,
            "kickoff_utc": "2026-09-11T23:30:00Z",
            "team_id": team,
            "team": team,
            "opponent_id": opp,
            "opponent": opp,
            "team_division": div,
            "opponent_division": odiv,
            "site": site,
            "points_for": pf,
            "points_against": pa,
            "box": {"plays": plays, "rush_att": plays - 30, "pass_att": 30},
            "pbp": None,
        }
    )


def _cfbd(gid, home, away, hc="fbs", ac="fbs", neutral=False, week=2):
    return {
        "id": int(gid),
        "season": 2026,
        "week": week,
        "seasonType": "regular",
        "startDate": "2026-09-11T23:30:00.000Z",
        "neutralSite": neutral,
        "homeTeam": home,
        "awayTeam": away,
        "homeClassification": hc,
        "awayClassification": ac,
        "completed": False,
        "homePoints": None,
        "awayPoints": None,
    }


def _event(gid, home_id, away_id):
    return {
        "id": gid,
        "date": "2026-09-11T23:30Z",
        "competitors": [{"home_away": "home", "team_id": home_id}, {"home_away": "away", "team_id": away_id}],
    }


def test_espn_bridge_identity_orientation_neutral_and_plays(build):
    rows = [
        _tg("401858214", "103", "164", 38, 10, "home"),
        _tg("401858214", "164", "103", 10, 38, "away"),
        _tg("401900001", "251", "333", 21, 24, "neutral", plays=71),
        _tg("401900001", "333", "251", 24, 21, "neutral", plays=62),
    ]
    events = [_event("401858214", "103", "164"), _event("401900001", "251", "333")]
    cfbd = [_cfbd("401858214", "Boston College", "Rutgers"), _cfbd("401900001", "Texas", "Alabama", neutral=True)]
    lines, stats = build.espn_current_season_lines(rows, events, cfbd)
    by = {(ln.source_game_id, ln.team_id): ln for ln in lines}
    bc = by[("401858214", "boston-college")]
    assert bc.is_home and bc.opponent_id == "rutgers" and bc.team_points == 38 and bc.opponent_points == 10
    assert bc.week == 2 and bc.season == 2026 and bc.source == build.CURRENT_SOURCE
    tx = by[("401900001", "texas")]
    assert tx.is_neutral_site and tx.is_home and tx.team_plays == 71  # CFBD home designation kept at neutral site
    assert by[("401900001", "alabama")].team_plays == 62 and not by[("401900001", "alabama")].is_home
    assert stats["games"] == 2


def test_espn_bridge_skips_unfinished_unidentified_and_keeps_fcs_pooled(build):
    rows = [
        _tg("1", "10", "20", None, None, "home"),
        _tg("1", "20", "10", None, None, "away"),
        _tg("2", "10", "30", 49, 7, "home", odiv="fcs"),
        _tg("2", "30", "10", 7, 49, "away", div="fcs"),
        _tg("3", "10", "40", 20, 10, "home"),
    ]
    events = [_event("1", "10", "20"), _event("2", "10", "30"), _event("3", "10", "40")]
    cfbd = [_cfbd("1", "Texas", "Alabama"), _cfbd("2", "Texas", "Idaho State", ac="fcs")]
    lines, stats = build.espn_current_season_lines(rows, events, cfbd)
    assert stats["no_final"] == 1 and stats["no_cfbd_identity"] == 1
    fcs = [ln for ln in lines if ln.source_game_id == "2"]
    assert {ln.team_classification for ln in fcs} == {"fbs", "fcs"}  # FCS row kept; the ratings fit pools it


@need_outputs
def test_source_contract_and_week1_check_recorded():
    rep = json.loads(REPORT.read_text())
    sc = rep["source_contract"]
    assert sc["accept_espn_definition"] is True and abs(sc["d_mae"]) <= 0.02 and abs(sc["d_slope"]) <= 0.01
    assert rep["week1_check"]["b1_differs_from_p0"] == 0  # no same-season evidence exists in week 1
    assert rep["y2026"]["mean_current_season_rows"] > 0


# --------------------------------------------------------------------------- calibration (44)


@need_outputs
def test_fold_calibrations_use_only_earlier_seasons(analyze):
    hist = [json.loads(x) for x in gzip.open(W2D / "p0_games_2014_2025.jsonl.gz", "rt")]
    sc = {r["game_id"]: r for r in (json.loads(x) for x in gzip.open(OUT / "source_contract_2014_2025.jsonl.gz", "rt"))}
    _, fits = analyze.reconstruct(hist, sc)
    doctored = copy.deepcopy(hist)
    for r in doctored:
        if r["season"] >= 2022:
            r["home_points"], r["away_points"] = r["away_points"], r["home_points"]
    _, fits2 = analyze.reconstruct(doctored, sc)
    for k in ("B2", "B3"):
        for s in range(2016, 2023):
            assert fits[k][str(s)] == fits2[k][str(s)], (k, s)
        assert fits[k]["2024"] != fits2[k]["2024"]


@need_outputs
def test_no_market_field_enters_any_base_model(analyze):
    hist = [json.loads(x) for x in gzip.open(W2D / "p0_games_2014_2025.jsonl.gz", "rt")][:3000]
    rows, _ = analyze.reconstruct(hist, {})
    doctored = copy.deepcopy(hist)
    for r in doctored:
        r["descriptive_close_spread_home"] = 99.0
    rows2, _ = analyze.reconstruct(doctored, {})
    assert [r["_m"] for r in rows] == [r["_m"] for r in rows2]


@need_outputs
def test_calibration_preserves_total_and_variance(analyze):
    hist = [json.loads(x) for x in gzip.open(W2D / "p0_games_2014_2025.jsonl.gz", "rt")]
    rows, _ = analyze.reconstruct(hist, {})
    for r in rows:
        assert r["_total"]["B2"] == r["_total"]["B3"] == r["_total"]["B1"]
        assert r["_sigma"]["B2"] == r["_sigma"]["B3"] == r["_sigma"]["B1"]
        if not r["fbs_vs_fbs"]:
            assert r["_m"]["B2"] == r["_m"]["B3"] == r["_m"]["B1"]  # FCS: no calibration


def test_old_c2_artifact_reproducible():
    assert (FROZEN_MARGIN_CORRECTION_PARAMS.a, FROZEN_MARGIN_CORRECTION_PARAMS.b) == (
        1.3413121461524347,
        0.8117267938452581,
    )
    rows = (
        [json.loads(x) for x in gzip.open(OUT / "games_2026.jsonl.gz", "rt")]
        if (OUT / "games_2026.jsonl.gz").exists()
        else []
    )
    for r in rows:
        for tag in ("p0", "b1"):
            raw = r[f"{tag}_raw"]
            c2 = FROZEN_MARGIN_CORRECTION_PARAMS.a * raw + FROZEN_MARGIN_CORRECTION_PARAMS.b - raw
            assert r[f"{tag}_c2"] == pytest.approx(c2, abs=1e-9)
            assert r[f"{tag}_margin"] == pytest.approx(raw + r[f"{tag}_c2"] + r[f"{tag}_talent"], abs=1e-9)


def test_metrics_deterministic_and_tail_definitions(analyze):
    rows = [
        {"_m": {"X": p}, "_actual": a, "_sigma": {"X": 16.0, "B1": 16.0}, "season": 2020, "week": 5}
        for p, a in ((20.0, 3.0), (-18.0, -40.0), (2.0, 1.0), (-1.0, 4.0), (15.0, 15.0))
    ]
    m1, m2 = analyze.metrics(rows, "X"), analyze.metrics(rows, "X")
    assert m1 == m2
    # favourite-direction errors among |proj| >= 14: (3-20)*1 = -17, (-40+18)*-1 = +22, 0 -> mean 5/3
    assert m1["fav_tail"] == pytest.approx(5 / 3) and m1["fav_tail_n"] == 3
    assert m1["dog_tail"] == pytest.approx(1 / 5 - 1 / 5)


@need_outputs
def test_analysis_is_deterministic(analyze, tmp_path, monkeypatch):
    target = tmp_path / "r.json"
    monkeypatch.setattr(analyze, "REPORT", target)
    analyze.main()
    assert _sha(target) == _sha(REPORT)


@need_outputs
def test_decision_follows_the_frozen_gates(analyze):
    rep = json.loads(REPORT.read_text())
    sel = analyze.select_base(rep["base_gates"], rep["pooled_primary"])
    assert sel == rep["base_selection"]
    for k, gates in rep["base_gates"].items():
        assert set(gates) == {f"G{i}" for i in range(1, 15)}, k
    assert rep["protocol"]["sha256"] == _sha(ROOT / rep["protocol"]["path"])


# --------------------------------------------------------------------------- rush retest (45)


@need_outputs
def test_rush_features_are_the_wave2d_definitions_and_fit_against_base_error(analyze):
    hist = [json.loads(x) for x in gzip.open(W2D / "p0_games_2014_2025.jsonl.gz", "rt")]
    rows, _ = analyze.reconstruct(hist, {})
    rep = json.loads(REPORT.read_text())
    key = rep["rush_retest"]["base"]
    pop = analyze.rush_population(rows, key)
    for r in pop[:500]:
        assert r["_p0"]["margin"] == r["_m"][key]
        assert r["_p0"]["home"] - r["_p0"]["away"] == pytest.approx(r["_m"][key])
        assert r["_p0"]["home"] + r["_p0"]["away"] == pytest.approx(r["_total"]["B1"])
        assert r["fbs_vs_fbs"] and not r["postseason"]
    assert R.CANDIDATES == {"P1": ("x_rd",), "P2": ("x_rr",), "P3": ("x_rd", "x_ro")}
    assert R.RUSH_RESIDUALISER["b"] == 0.8259469722808191 and R.RUSH_OFF_SD == 1.3944072421206335


def test_g6_is_enforced_and_nothing_promotes_without_a_promoted_base(analyze):
    def cand(g6, d=-0.3, feats=1):
        gates = {f"G{i}": {"pass": True} for i in range(1, 13)}
        gates["G6"]["pass"] = g6
        return {"gates": gates, "pooled_primary": {"d_mae": d}, "features": ["x"] * feats}

    assert analyze.rush_decision({"R1": cand(False)}, True)["decision"] == "REJECT_RUSH"
    assert analyze.rush_decision({"R1": cand(True)}, False)["decision"] == "SHADOW_RUSH"
    assert analyze.rush_decision({"R1": cand(True)}, True)["decision"] == "PROMOTE_RUSH"
    assert R.delta([3.0], [1.0], fbs_vs_fbs=False) == 0.0


@pytest.mark.parametrize("rel,digest", sorted(PINNED.items()))
def test_production_and_prospective_files_unchanged(rel, digest):
    assert _sha(ROOT / rel) == digest


def test_run_defense_p0_still_the_frozen_prior_season_object():
    text = (ROOT / "src/cfb_edge_finder/signal_discovery/run_defense_cycle.py").read_text()
    assert "P1_BETA = 3.592270771756417" in text and 'ACTIVATION_UTC = "2026-10-13T12:00:00Z"' in text
    conductor = (ROOT / "scripts/cfb_research_conductor.py").read_text()
    assert "state.to_scan_inputs(datetime.now(UTC))" in conductor  # history seasons only, unchanged


def test_model_version_unchanged():
    sys.path.insert(0, str(ROOT / "scripts"))
    import capture_kalshi_cfb_snapshot as live

    assert live.MODEL_VERSION == "0.4.0-milestone-c2-live-margin-correction"
    assert live.TALENT_PRIOR_MODEL_VERSION == "0.5.0-early-season-talent-prior"
    for p in (ROOT / "src" / "cfb_edge_finder").rglob("*.py"):
        assert "0.6.0-in-season" not in p.read_text(encoding="utf-8"), p
