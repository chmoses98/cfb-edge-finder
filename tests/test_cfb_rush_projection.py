"""Wave 2D Track A (CFB): rush projection integration is football-only, leakage-safe and leaves production untouched.

Proves:
* P0 is the current production arithmetic (live object reproduced; historical reconstruction == production code);
* the candidate features are the exact Wave-2C constructs, pregame-only, and never market or outcome fields;
* coefficients are fit walk-forward on strictly earlier seasons, without an intercept, against P0 error;
* FCS-involved games and missing features get exactly zero correction; totals and widths never change;
* home / away orientation is antisymmetric (neutral sites carry no home term);
* the analysis is deterministic and the decision follows the frozen gates;
* the model version and every production / Wave-2 file are unchanged (decision: not promoted).
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

from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf
from cfb_edge_finder.modeling.margin_correction_artifact import FROZEN_MARGIN_CORRECTION_PARAMS
from cfb_edge_finder.modeling.ratings import fit_fbs_efficiency_ratings
from cfb_edge_finder.modeling.talent_prior import talent_margin_delta
from cfb_edge_finder.signal_discovery import mechanisms as M
from cfb_edge_finder.signal_discovery import rush_projection as R
from cfb_edge_finder.signal_discovery import wave2 as W

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "scripting" / "validation" / "rush_projection"
WAVE2C = ROOT / "data" / "scripting" / "validation" / "signal_mechanisms"
HIST, Y26, REPORT, BUILD = (
    OUT / "p0_games_2014_2025.jsonl.gz",
    OUT / "p0_games_2026.jsonl.gz",
    OUT / "integration_report.json",
    OUT / "build_manifest.json",
)
HAVE = HIST.exists() and Y26.exists() and REPORT.exists()
need_outputs = pytest.mark.skipif(not HAVE, reason="study outputs not built")

#: Production model files at the Wave-2D starting main (1e40ecbc). The decision is REJECT, so none may change.
PRODUCTION_FILES = {
    "src/cfb_edge_finder/modeling/score_model.py": "7c7e440781430cd577cd4b72d2bf5264ddfd619fbe996e5fd3084bb4a4f32722",
    "src/cfb_edge_finder/modeling/ratings.py": "21f96a299406c16be4c4af5115905e4809b9c041be5f509e7162865aabdc1deb",
    "src/cfb_edge_finder/modeling/talent_prior.py": "d2b12433b676f35451661a31e199b6a6e39c9f98f271a8f4c4a80389ab1ec0dc",
    "src/cfb_edge_finder/modeling/margin_correction_artifact.py": (
        "37c22f24a27ee927939dba669ea821c78ec25310f97c95c0ad9072e691ccb45e"
    ),
    "src/cfb_edge_finder/kalshi/game_projection_cache.py": (
        "ebfca09d9beed87203d058908986a59da6f4152f856877ecf79630c368ea4d38"
    ),
    "src/cfb_edge_finder/modeling/corpus.py": "0b1f182fa2c4f58401e46f84ae05893702b71f63b1748d3c42711806e830f32c",
    "src/cfb_edge_finder/modeling/margin_calibration.py": (
        "fe55e63c8f51ce50a463ab102d33ecbccefd5dc92c7d600207c4f40a9feef819"
    ),
    "scripts/research_scan_and_capture.py": "846c04e4d6925d63731d86e7290e5c22d5778788ec0ff13a6844f63cee776a92",
    "scripts/capture_kalshi_cfb_snapshot.py": "325bb87512f7552ace94fa1f794908fad1b0d372e34e123c3f2fe3d16a35e0b1",
}
#: Wave-2 / Wave-2C files and artifacts (never edited by Wave 2D).
FROZEN_RESEARCH = {
    "src/cfb_edge_finder/signal_discovery/wave2.py": "f8f235b28e4cd0e3ed7ba8155e9e9a7e978acbb909eef309386353954c7218ea",
    "src/cfb_edge_finder/signal_discovery/wave2_cycle.py": (
        "b729facf0a3d9112132a6419e0ff80656e75976117365ab74da75f13bbb8462e"
    ),
    "src/cfb_edge_finder/signal_discovery/mechanisms.py": (
        "03d824618750d31be06b1fa9926d1706080f4fd9552cacb046e677d7d2191dfe"
    ),
    "data/scripting/validation/signal_discovery_wave2/candidates.json": (
        "dbc97b65289e3b7a5a35f6bb44a006b5d65e1f793f463dcc60c02099860bea80"
    ),
    "data/scripting/validation/signal_mechanisms/games_2014_2025.jsonl.gz": (
        "e0443bb1c1cc4e0e2ae1433cbcdcc1e3fd2c597cfe07c3960f9f4073c35b8626"
    ),
    "data/scripting/validation/signal_mechanisms/games_2026.jsonl.gz": (
        "7cc71635053c4760702d1a433bcd52acd35f7c25f6f8cab7e3f95061276c1607"
    ),
    "data/scripting/validation/signal_mechanisms/mechanism_report.json": (
        "57167ae56543303b53eebb5a88ef43fd583aed6d8fc134e64f2683821df06ab9"
    ),
}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def analyze():
    return _load("rush_projection_cfb_analyze_t", "scripts/rush_projection_cfb_analyze.py")


@pytest.fixture(scope="module")
def build():
    return _load("rush_projection_cfb_t", "scripts/rush_projection_cfb.py")


# --------------------------------------------------------------------------- provenance and frozen files


def test_protocol_committed_and_hash_pinned():
    assert _sha(ROOT / R.PROTOCOL_PATH) == R.PROTOCOL_SHA256
    assert len(R.PROTOCOL_COMMIT) == 40 and R.LABEL == "RETROSPECTIVE_MODEL_INTEGRATION"


@pytest.mark.parametrize("rel,digest", sorted({**PRODUCTION_FILES, **FROZEN_RESEARCH}.items()))
def test_production_and_frozen_research_files_unchanged(rel, digest):
    assert _sha(ROOT / rel) == digest


def test_model_version_unchanged_when_not_promoted():
    sys.path.insert(0, str(ROOT / "scripts"))
    import capture_kalshi_cfb_snapshot as live

    assert live.MODEL_VERSION == "0.4.0-milestone-c2-live-margin-correction"
    assert live.TALENT_PRIOR_MODEL_VERSION == "0.5.0-early-season-talent-prior"
    assert live.resolve_model_version(True) == "0.5.0-early-season-talent-prior"
    # no promoted correction artifact anywhere in production code
    for p in (ROOT / "src" / "cfb_edge_finder").rglob("*.py"):
        if "signal_discovery" in p.parts:
            continue
        text = p.read_text(encoding="utf-8")
        assert "cfb_rush_margin_correction" not in text
        assert "rush_projection" not in text, f"production module imports the research candidate: {p}"


# --------------------------------------------------------------------------- P0 is the production arithmetic


def _line(season, week, team, opp, pts, opp_pts, plays, home, gid):
    return TeamGameLine(
        source_game_id=gid,
        season=season,
        week=week,
        is_postseason=False,
        team_id=team,
        opponent_id=opp,
        team_classification="fbs",
        opponent_classification="fbs",
        is_home=home,
        is_neutral_site=False,
        team_points=pts,
        opponent_points=opp_pts,
        team_plays=plays,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _synthetic_lines():
    rng = np.random.default_rng(7)
    teams = ["a", "b", "c", "d", "e", "f"]
    lines, gid = [], 0
    for season in (2024, 2025):
        for week in range(1, 9):
            order = list(rng.permutation(teams))
            for i in range(0, 6, 2):
                h, a = order[i], order[i + 1]
                hp, ap = int(rng.integers(7, 50)), int(rng.integers(7, 50))
                hpl, apl = int(rng.integers(55, 85)), int(rng.integers(55, 85))
                gid += 1
                lines.append(_line(season, week, h, a, hp, ap, hpl, True, str(gid)))
                lines.append(_line(season, week, a, h, ap, hp, apl, False, str(gid)))
    return lines


def test_p0_reconstruction_equals_live_object(build, analyze):
    """The historical builder (production project_game + C.2 + talent) equals GameProjectionCache exactly."""
    from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest

    lines = _synthetic_lines()
    talent = {"a": 600.0, "b": 450.0}
    cache = GameProjectionCache(lines, talent_by_team=talent)
    live = cache.get_or_build(
        GameProjectionRequest("g", "a", "b", "fbs", "fbs", False, 2026, 3, n_simulations=200, seed=0)
    ).projection
    target = _line(2026, 3, "a", "b", 0, 0, 70, True, "g")
    det = build.deterministic(fit_fbs_efficiency_ratings(lines, AsOf(2026, 0)), target)
    row = {
        "live_eh": det["eh"],
        "live_ea": det["ea"],
        "live_sigma": 1.0,
        "fbs_vs_fbs": True,
        "c2_identity": False,
        "c2_a": FROZEN_MARGIN_CORRECTION_PARAMS.a,
        "c2_b": FROZEN_MARGIN_CORRECTION_PARAMS.b,
        "talent_delta": talent_margin_delta(600.0, 450.0),
    }
    p0 = analyze.p0(row, "live")
    assert det["eh"] == live.raw.expected_home_points and det["ea"] == live.raw.expected_away_points
    assert p0["margin"] == pytest.approx(live.expected_margin, abs=1e-12)
    assert p0["total"] == pytest.approx(live.expected_total, abs=1e-12)


@need_outputs
def test_live_object_reproduced_on_real_observations():
    rep = json.loads(BUILD.read_text())["live_reproduction"]
    assert rep["exact_to_1e-12"] is True and rep["moneyline_rows_checked"] >= 400 and rep["distinct_games"] >= 50


@need_outputs
def test_2026_p0_rows_are_the_live_composition():
    for r in _rows(Y26):
        raw = r["p0_live_raw_margin"]
        c2 = FROZEN_MARGIN_CORRECTION_PARAMS.a * raw + FROZEN_MARGIN_CORRECTION_PARAMS.b - raw
        assert r["c2_delta"] == pytest.approx(c2, abs=1e-9)
        assert r["p0_live_margin"] == pytest.approx(raw + r["c2_delta"] + r["talent_delta"], abs=1e-9)
        assert r["fbs_vs_fbs"] is True


@need_outputs
def test_p0_live_has_no_in_season_rows_and_inseason_does(analyze):
    """P0-LIVE ratings for a season are fit once (identical expected points for a matchup all season)."""
    rows = [r for r in _rows(HIST) if r["season"] == 2023 and "live_eh" in r and "in_eh" in r]
    key = {}
    for r in rows:
        key.setdefault((r["home_id"], r["away_id"], r["neutral_site"]), set()).add(round(r["live_eh"], 12))
    assert all(len(v) == 1 for v in key.values())
    assert any(abs(r["live_eh"] - r["in_eh"]) > 1e-6 for r in rows)


# --------------------------------------------------------------------------- features: exact, pregame, football-only


def test_feature_constants_are_wave2c():
    rep = json.loads((WAVE2C / "mechanism_report.json").read_text())
    for k in ("a", "b", "sd"):
        assert R.RUSH_RESIDUALISER[k] == rep["residualisers"]["rushing"][k]
        assert R.PASS_RESIDUALISER[k] == rep["residualisers"]["passing"][k]
    assert R.RUSH_OFF_MEAN == rep["standardisation"]["rush_off_diff"]["mean"]
    assert R.RUSH_OFF_SD == rep["standardisation"]["rush_off_diff"]["sd"]
    assert rep["standardisation"]["rush_def_diff"] == {"mean": W.DSC001_MEAN, "sd": W.DSC001_SD}


@need_outputs
def test_x_rd_is_the_pros001_z_and_x_rr_the_wave2c_construct():
    rows = _rows(WAVE2C / "games_2014_2025.jsonl.gz")[:400]
    fits = {"rushing": R.RUSH_RESIDUALISER, "passing": R.PASS_RESIDUALISER}
    for w in rows:
        f = R.rush_features(w)
        v = w.get(W.DSC001_FEATURE)
        if v is not None:
            assert f["x_rd"] == pytest.approx((v - W.DSC001_MEAN) / W.DSC001_SD, abs=1e-12)
        assert f["x_rr"] == M.construct(w, fits)["rush_res_z"]


def test_features_ignore_market_and_outcome_fields():
    w = json.loads(gzip.open(WAVE2C / "games_2014_2025.jsonl.gz", "rt").readline())
    doctored = copy.deepcopy(w)
    for k in list(doctored):
        if k.startswith(("o.", "m.", "g.", "k.")):
            doctored[k] = 999.0
    assert R.rush_features(doctored) == R.rush_features(w)


def test_market_and_outcome_fields_rejected():
    for bad in ("m.spread_home", "k.implied_margin", "o.home_points", "spread", "close_line", "kalshi_center"):
        with pytest.raises(R.IntegrationError):
            R.assert_football_only([bad])
    R.assert_football_only([f for feats in R.CANDIDATES.values() for f in feats])


# --------------------------------------------------------------------------- correction mechanics


def test_fcs_and_missing_features_fail_closed():
    assert R.delta([3.0], [1.5], fbs_vs_fbs=False) == 0.0
    assert R.delta([3.0], [None], fbs_vs_fbs=True) == 0.0
    assert R.delta([3.0, 2.0], [1.0, None], fbs_vs_fbs=True) == 0.0


def test_orientation_antisymmetric_no_home_term():
    beta = [3.2, 1.1]
    assert R.delta(beta, [0.7, -0.4], fbs_vs_fbs=True) == -R.delta(beta, [-0.7, 0.4], fbs_vs_fbs=True)
    assert R.delta(beta, [0.0, 0.0], fbs_vs_fbs=True) == 0.0  # no intercept: a neutral matchup moves nothing


def test_total_exactly_preserved_and_width_unchanged(analyze):
    for h, a, d in ((31.2, 17.9, 3.3), (10.0, 44.5, -7.25), (0.4, 0.2, 1.0)):
        nh, na = R.shifted_scores(h, a, d)
        assert nh + na == pytest.approx(h + a, abs=1e-12) and (nh - na) - (h - a) == pytest.approx(d, abs=1e-12)
    row = {
        "game_id": "g",
        "season": 2020,
        "week": 3,
        "neutral_site": False,
        "talent_active": False,
        "_actual": 7.0,
        "_p0": {"margin": 3.0, "home": 30.0, "away": 27.0, "total": 57.0, "sigma": 16.0},
    }
    base = analyze.score([row], None)[0]
    cand = analyze.score([row], {"g": 2.0})[0]
    assert cand["total"] == base["total"] == 57.0
    # same sigma: a pure location shift of the Normal
    assert cand["mu"] - base["mu"] == 2.0


def test_fit_beta_has_no_intercept():
    x = np.array([[1.0], [2.0], [3.0], [4.0]])
    y = 2.0 * x[:, 0] + 5.0
    beta = R.fit_beta(x, y)
    assert beta.shape == (1,)
    assert beta[0] == pytest.approx(float((x[:, 0] @ y) / (x[:, 0] @ x[:, 0])))


@need_outputs
def test_walk_forward_never_uses_the_future(analyze):
    hist = _rows(HIST)
    pop = analyze.population(hist, "live")
    betas, _ = analyze.walk_forward(pop, "P1")
    doctored = copy.deepcopy(hist)
    for r in doctored:
        if r["season"] >= 2021:
            r["home_points"], r["away_points"] = r["away_points"], r["home_points"]
    betas2, _ = analyze.walk_forward(analyze.population(doctored, "live"), "P1")
    for s in range(2016, 2022):
        assert betas[s] == betas2[s], s
    assert betas[2023] != betas2[2023]


@need_outputs
def test_population_is_fbs_regular_season_and_fcs_rows_get_zero(analyze):
    hist = _rows(HIST)
    pop = analyze.population(hist, "live")
    assert all(r["fbs_vs_fbs"] and not r["postseason"] for r in pop)
    beta = json.loads(REPORT.read_text())["candidates"]["P1"]["beta_final_2015_2025_live"]
    fcs = [r for r in hist if not r["fbs_vs_fbs"]]
    assert fcs and all(R.delta(beta, [r.get("x_rd")], fbs_vs_fbs=False) == 0.0 for r in fcs)


# --------------------------------------------------------------------------- report, gates, decision


@need_outputs
def test_report_gates_and_decision_follow_the_protocol(analyze):
    rep = json.loads(REPORT.read_text())
    assert rep["protocol"]["commit"] == R.PROTOCOL_COMMIT and rep["protocol"]["sha256"] == R.PROTOCOL_SHA256
    for c in rep["candidates"].values():
        assert c["gates"]["G7"]["value"] == 0.0 and c["gates"]["G9"]["value"] == 0.0
        assert set(c["gates"]) == {f"G{i}" for i in range(1, 13)}
    assert analyze.decide(rep["candidates"]) == rep["decision"]
    assert rep["decision"]["decision"] in ("PROMOTE", "SHADOW_ONLY", "REJECT")


@need_outputs
def test_analysis_is_deterministic(analyze, tmp_path, monkeypatch):
    target = tmp_path / "integration_report.json"
    monkeypatch.setattr(analyze, "REPORT", target)
    analyze.main()
    assert _sha(target) == _sha(REPORT)


@need_outputs
def test_inputs_hashes_recorded():
    rep = json.loads(REPORT.read_text())
    assert rep["inputs_sha256"]["p0_games_2014_2025.jsonl.gz"] == _sha(HIST)
    assert rep["inputs_sha256"]["p0_games_2026.jsonl.gz"] == _sha(Y26)
    man = json.loads(BUILD.read_text())
    assert man["wave2c_join"]["orientation_mismatch"] == 0
    assert man["outputs_sha256"]["p0_games_2014_2025.jsonl.gz"] == _sha(HIST)
