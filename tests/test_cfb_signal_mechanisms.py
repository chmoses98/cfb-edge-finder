"""Football Signal Discovery Lab, Wave 2C (CFB): the signal-mechanism study is explanatory and leaves Wave 2 untouched.

Proves:
* Wave-2 / 2A code, candidates and artifacts are unchanged;
* CONTROL and the PROS-001 constants are imported, not restated;
* no future game enters a feature, and no final score enters a market model;
* the spread is never a football input;
* opener / close and home / away orientation are correct;
* source-parity joins are stable, and FBS / FCS identity is correct;
* outcomes never move membership;
* nothing writes a prospective store;
* the analysis reruns deterministically.

Offline: synthetic rows and the committed study inputs under data/scripting/validation/signal_mechanisms/.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from cfb_edge_finder.signal_discovery import mechanisms as M
from cfb_edge_finder.signal_discovery import wave2 as W

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "scripting" / "validation" / "signal_mechanisms"
WAVE1 = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave1"
WAVE2A = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave2a"
HIST, Y26, REPORT, BUILD = (
    OUT / "games_2014_2025.jsonl.gz",
    OUT / "games_2026.jsonl.gz",
    OUT / "mechanism_report.json",
    OUT / "build_manifest.json",
)
HAVE = HIST.exists() and Y26.exists() and REPORT.exists()
need_outputs = pytest.mark.skipif(not HAVE, reason="study outputs not built")

#: SHA-256 of the frozen Wave-2 / Wave-2A files at the study's source main (e1310b00). Never edit them.
FROZEN_FILES = {
    "src/cfb_edge_finder/signal_discovery/wave2.py": "f8f235b28e4cd0e3ed7ba8155e9e9a7e978acbb909eef309386353954c7218ea",
    "src/cfb_edge_finder/signal_discovery/wave2_cycle.py": (
        "b729facf0a3d9112132a6419e0ff80656e75976117365ab74da75f13bbb8462e"
    ),
    "src/cfb_edge_finder/signal_discovery/wave2a/replay.py": (
        "dcb590c6178e1c8f8d465991a46eaf9f1b4f38c5d021f994ceee9c6e9b49462a"
    ),
    "src/cfb_edge_finder/signal_discovery/wave2a/history.py": (
        "35006dfe2fb2d3afe7ed301c46327e39a5486a2032c51c6d062463971273da46"
    ),
    "scripts/signal_lab_wave2a_cfb.py": "87321d378b0a7a10a9b301b78ec1e07c7dca0cf0772f128cf60c83dc2af9fe71",
    "docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE2_PROTOCOL.md": (
        "79f748db16f9478b37bdbd591a9fef31cd20ac3fe48f07afbe89f7db6c4063c1"
    ),
    "docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE2A_PROTOCOL.md": (
        "e204e2f7e957ab520fc77f67b38d4b2319c2013820ba88d07c7012aab4652080"
    ),
    "data/scripting/validation/signal_discovery_wave2/candidates.json": (
        "dbc97b65289e3b7a5a35f6bb44a006b5d65e1f793f463dcc60c02099860bea80"
    ),
}
WAVE2A_ARTIFACTS = {
    "extract_manifest.json": "4ce8d89b76da1f5b700e77f93c6eb6fbf68a23bf423fca6f10cf7126d1cfa10d",
    "history_report.json": "5550d3c95a0b4321f6f4866846ecb97b8f769ff66279c5fd80d2147242a9f14c",
    "replay_membership_2026.jsonl.gz": "ee46fa79743c83c5215233e468f93aaa7feed4923f012545c7032ca16eb8a040",
    "replay_report_2026.json": "e70cb71d313c6400dbdd86e2b96fa5a8cea71133e9e385df964a86d13cdbe6fe",
    "replay_rows_2026.jsonl.gz": "c60e90ea6b3893b17faee9888f804950d27f52f0de4cd9d48fc78b0f295f7bea",
    "spread_captures_2026.jsonl.gz": "dde615731d091b8626276276278e20b36fee64655e6703976f79d2a9087ac6b2",
    "winner_quotes_supplement_2026.jsonl.gz": "c89dcd68f1552c955ecfd775d2b80d2ca878fe0675eb6aebcc7d6cec863b7619",
}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _gz(p: Path) -> list[dict]:
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return [json.loads(x) for x in fh]


def _ts(s: str) -> datetime:
    t = str(s).replace("Z", "+00:00")
    if len(t) == 22:  # 2026-08-29T16:00+00:00 (no seconds)
        t = t[:16] + ":00" + t[16:]
    return datetime.fromisoformat(t)


def _analysis():
    spec = importlib.util.spec_from_file_location("cfb_mech_an", ROOT / "scripts" / "signal_mechanisms_cfb_analyze.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cfb_mech_an"] = mod
    spec.loader.exec_module(mod)
    return mod


def _row(**kw) -> dict:
    base = {
        "game_id": "1",
        "season": 2024,
        "week": 5,
        "home": "H",
        "away": "A",
        "neutral_site": False,
        "net.sustained_efficiency": 1.0,
        "net.rushing": 1.5,
        "net.passing": 0.2,
        W.DSC001_FEATURE: 2.0,
        "control_side": "home",
        "control_strength": "MODERATE",
        "m.spread_home": -7.0,
        "m.spread_open_home": -6.0,
        "m.total": 55.0,
        "o.home_margin": 10.0,
        "o.total_points": 50.0,
        "o.home_points": 30.0,
        "o.away_points": 20.0,
        "g.h1": 3.0,
        "g.h2": 7.0,
        "g.q1": 0.0,
        "g.q2": 3.0,
        "g.q3": 7.0,
        "g.q4": 0.0,
        "g.thru_q3": 10.0,
        "g.ot": 0.0,
    }
    base.update(kw)
    return base


FITS = {"rushing": {"a": 0.0, "b": 0.8, "sd": 1.0, "n": 1}, "passing": {"a": 0.0, "b": 0.9, "sd": 1.0, "n": 1}}


# --------------------------------------------------------------------------- Wave 2 / 2A untouched


@pytest.mark.parametrize("rel,sha", sorted(FROZEN_FILES.items()))
def test_wave2_code_and_candidates_unchanged(rel, sha):
    assert _sha(ROOT / rel) == sha, f"{rel} changed"


def test_wave2_candidate_hash_still_registered():
    doc = W.load_candidates(ROOT)  # raises CandidatesMismatch on any change
    assert W.sha256(doc) == W.CANDIDATES_SHA256 == "bf19c972bd230ff505c50ccd3a631dbc027d41b4900ad76c97248a4cfc6e1ce5"


@pytest.mark.parametrize("name,sha", sorted(WAVE2A_ARTIFACTS.items()))
def test_wave2a_replay_artifacts_untouched(name, sha):
    assert _sha(WAVE2A / name) == sha


def test_protocol_hash_is_the_registered_one():
    assert _sha(ROOT / "docs" / "research" / "CFB_SIGNAL_MECHANISM_PROTOCOL.md") == M.PROTOCOL_SHA256


@pytest.mark.parametrize(
    "path",
    [
        "/x/research-signals/wave2/ledger.jsonl",
        "data/scripting/validation/signal_discovery_wave2/candidates.json",
        "data/scripting/validation/signal_discovery_wave2a/replay_rows_2026.jsonl.gz",
        "/repo/data/research/wave2/x.json",
    ],
)
def test_no_prospective_or_wave2_write(path):
    with pytest.raises(M.MechanismIntegrityError):
        M.assert_not_prospective(path)
    M.assert_not_prospective(str(OUT / "mechanism_report.json"))  # the study's own directory is allowed


# --------------------------------------------------------------------------- imported, never restated


def test_rush_constants_imported_and_match_registration():
    doc = W.load_candidates(ROOT)
    s1 = doc["streams"][W.PROS_001]["standardisation"]
    assert (s1["mean"], s1["sd"]) == (W.DSC001_MEAN, W.DSC001_SD)
    r = _row(**{W.DSC001_FEATURE: W.DSC001_MEAN + W.DSC001_SD * 1.0})
    assert M.frozen_z(r) == pytest.approx(1.0) and M.frozen_side(r) == "home"
    assert M.frozen_side(_row(**{W.DSC001_FEATURE: W.DSC001_MEAN + 0.99 * W.DSC001_SD})) is None
    assert M.frozen_side(_row(**{W.DSC001_FEATURE: W.DSC001_MEAN - 1.2 * W.DSC001_SD})) == "away"


def test_control_comes_from_the_production_claim():
    c = M.construct(_row(control_side="away", **{"net.sustained_efficiency": -2.0}), FITS)
    assert c["control_mag"] == pytest.approx(2.0)  # signed toward the claimed side, no threshold of our own
    assert M.construct(_row(control_side=None), FITS)["control_mag"] is None
    src = (ROOT / "src" / "cfb_edge_finder" / "signal_discovery" / "mechanisms.py").read_text()
    assert "STRONG" not in src.split("def construct")[1].split("def ")[0]


@need_outputs
def test_study_control_equals_wave1_feature_tables():
    w1 = {}
    for s in (2016, 2024):
        for r in _gz(WAVE1 / "features" / f"features_{s}.jsonl.gz"):
            w1[r["game_id"]] = (r.get("control_side"), r.get("control_strength"))
    rows = [r for r in _gz(HIST) if r["game_id"] in w1]
    assert rows and all((r.get("control_side"), r.get("control_strength")) == w1[r["game_id"]] for r in rows)


# --------------------------------------------------------------------------- walls


def test_market_and_outcomes_never_enter_constructs():
    a = M.construct(_row(), FITS)
    b = M.construct(_row(**{"m.spread_home": 30.0, "m.total": 10.0, "o.home_margin": -50.0, "g.h2": -40.0}), FITS)
    assert a == b
    assert not any(k.startswith(("m.", "o.", "g.")) for k in M.pregame_only(_row()))


def test_membership_does_not_move_with_outcomes():
    an = _analysis()
    rows = [_row(game_id=str(i), **{W.DSC001_FEATURE: (i - 5) * 0.6, "net.rushing": (i - 5) * 0.4}) for i in range(11)]
    doctored = copy.deepcopy(rows)
    for r in doctored:
        r["o.home_margin"] = -r["o.home_margin"] * 3
        r["g.h1"], r["g.h2"] = 50.0, -60.0
    p1, sds = an.prepare(rows, FITS)
    p2, _ = an.prepare(doctored, FITS, sds)
    for key in ("frs", "rrs", "prs", "control_side"):
        before = [(s["game_id"], s["side"]) for s in an.sides(p1, key)]
        after = [(s["game_id"], s["side"]) for s in an.sides(p2, key)]
        assert before == after


def test_no_final_score_enters_the_market_model():
    an = _analysis()
    rows = []
    for s in range(2014, 2020):
        for i in range(40):
            eff = (i % 9) - 4.0
            rows.append(
                _row(
                    game_id=f"{s}-{i}",
                    season=s,
                    **{
                        "net.sustained_efficiency": eff,
                        "net.rushing": eff * 0.8 + ((i % 5) - 2) * 0.3,
                        "net.passing": eff * 0.9 - ((i % 7) - 3) * 0.2,
                        "m.spread_home": -3 * eff - (i % 3),
                        "o.home_margin": 4 * eff + (i % 11) - 5,
                    },
                )
            )
    H, sds = an.prepare(rows, FITS)
    a = an.market_decomposition(H)
    doctored = copy.deepcopy(rows)
    for r in doctored:
        r["o.home_margin"] = 99.0 - r["o.home_margin"]
    H2, _ = an.prepare(doctored, FITS, sds)
    b = an.market_decomposition(H2)
    fitted = [m for m in a if "exp_margin_coef" in a[m]]
    assert len(fitted) >= 3
    for model in fitted:
        assert a[model]["exp_margin_coef"] == b[model]["exp_margin_coef"]
        assert a[model]["exp_margin_wf_oos_r2"] == b[model]["exp_margin_wf_oos_r2"]
        assert a[model]["margin_coef"] != b[model]["margin_coef"]  # the doctoring is real: only the football fit moves


# --------------------------------------------------------------------------- orientation


def test_home_away_and_opener_close_orientation():
    an = _analysis()
    H, _ = an.prepare([_row(), _row(game_id="2", control_side="away")], FITS)
    home = an.sides([H[0]], "control_side")[0]
    away = an.sides([H[1]], "control_side")[0]
    assert (home["side_margin"], home["side_spread"], home["side_open"]) == (10.0, -7.0, -6.0)
    assert (away["side_margin"], away["side_spread"], away["side_open"]) == (-10.0, 7.0, 6.0)
    assert home["side_resid"] == pytest.approx(3.0) and away["side_resid"] == pytest.approx(-3.0)
    assert home["h2"] == 7.0 and away["h2"] == -7.0
    # line moved from -6 (open) to -7 (close): toward the home side by +1
    assert home["side_open"] - home["side_spread"] == pytest.approx(1.0)


def test_drive_and_quarter_helpers():
    d = M.drive_outcomes(
        [
            {"driveResult": "TD", "plays": 8, "startYardsToGoal": 75, "endYardsToGoal": 0},
            {"driveResult": "PUNT", "plays": 3, "startYardsToGoal": 80, "endYardsToGoal": 72},
            {"driveResult": "FG", "plays": 11, "startYardsToGoal": 60, "endYardsToGoal": 12},
            {"driveResult": "INT", "plays": 4, "startYardsToGoal": 70, "endYardsToGoal": 50},
        ]
    )
    assert d["points"] == 10 and d["empty"] == 2 and d["three_out"] == 1 and d["rz_trips"] == 2 and d["rz_td"] == 1
    assert d["turnover_drives"] == 1 and d["long_drives"] == 1
    q = M.quarter_margins([7, 3, 0, 10, 3], [0, 7, 7, 6, 0])
    assert q["h1"] == 3 and q["h2"] == -3 and q["ot"] == 3 and q["thru_q3"] == -4 and q["q4"] == 4
    assert M.quarter_margins([7, 3, 0], [0, 7, 7]) is None


def test_visible_proxies_use_prior_games_only(tmp_path):
    spec = importlib.util.spec_from_file_location("cfb_mech_build", ROOT / "scripts" / "signal_mechanisms_cfb.py")
    B = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(B)
    games = [
        {"id": 1, "startDate": "2024-09-01T00:00:00.000Z", "week": 1, "seasonType": "regular", "completed": True,
         "homeId": 10, "awayId": 20, "homePoints": 30, "awayPoints": 10},
        {"id": 2, "startDate": "2024-09-08T00:00:00.000Z", "week": 2, "seasonType": "regular", "completed": True,
         "homeId": 10, "awayId": 30, "homePoints": 0, "awayPoints": 50},
    ]  # fmt: skip
    with gzip.open(tmp_path / "games.json.gz", "wt") as fh:
        json.dump(games, fh)
    v = B.visible_proxies(tmp_path, 2024)
    assert v["1"]["games_home"] == 0 and v["1"]["margin_td_home"] is None  # nothing before the first game
    assert v["2"]["games_home"] == 1 and v["2"]["margin_td_home"] == 20.0  # game 2's own -50 is never seen


# --------------------------------------------------------------------------- committed inputs


@need_outputs
def test_no_future_game_enters_a_feature_row():
    for p in (HIST, Y26):
        for r in _gz(p):
            if r.get("history_latest_kickoff"):
                assert _ts(r["history_latest_kickoff"]) < _ts(r["kickoff_utc"]), r["game_id"]


@need_outputs
def test_anchor_and_2026_feature_parity_recorded():
    b = json.loads(BUILD.read_text())
    a = b["historical"]["anchor"]
    assert (a["n"], a["w"], a["l"], a["p"]) == (2713, 1408, 1260, 45) and a["mean"] == pytest.approx(1.4003870254331)
    fc = b["rows_2026"]["feature_check"]
    assert fc["compared"] == 397 and fc["mismatch"] == 0
    assert b["rows_2026"]["control_check_wave2a"]["disagreements"] == []


@need_outputs
def test_2026_frozen_feature_equals_wave2a_membership():
    mem = {
        r["game_id"]: (r.get("signal") or {}).get("value")
        for r in _gz(WAVE2A / "replay_membership_2026.jsonl.gz")
        if r["signal_id"] == W.PROS_001 and r["record"] == "OBSERVATION"
    }
    for r in _gz(Y26):
        v, w = r.get(W.DSC001_FEATURE), mem[r["game_id"]]
        assert (v is None) == (w is None) and (v is None or v == pytest.approx(w, abs=1e-12))


@need_outputs
def test_source_parity_join_is_stable():
    p = json.loads(BUILD.read_text())["cfbd_espn_parity_2026"]
    assert p["overlap_ids"] == p["orientation_match"] == p["neutral_match"] > 0
    assert p["completed_both"] == p["score_match"] == len(p["rows"]) and all(r["match"] for r in p["rows"])


@need_outputs
def test_fbs_fcs_identity():
    for r in _gz(HIST):
        divs = (r.get("home_division"), r.get("away_division"))
        assert bool(r.get("fcs_involved")) == ("fcs" in divs), r["game_id"]
    rows26 = [r for r in _gz(Y26) if r.get(W.DSC001_FEATURE) is not None]
    assert all(r.get("home_division") in ("fbs", "fcs") and r.get("away_division") in ("fbs", "fcs") for r in rows26)


@need_outputs
def test_market_spread_never_a_football_input():
    rows = _gz(HIST)
    feats = [k for k in rows[0] if not k.startswith(("m.", "o.", "g.", "v."))]
    assert not any("spread" in k or "total_open" in k or "ml_" in k for k in feats)


@need_outputs
def test_deterministic_rerun_of_formal_tests():
    an = _analysis()
    hist = _gz(HIST)
    H, _ = an.prepare(hist, M.fit_residualisers(hist))
    got = an.formal_tests(H)
    rep = json.loads(REPORT.read_text())["formal_tests"]
    for k, v in got.items():
        assert v["est"] == pytest.approx(rep[k]["est"], abs=1e-12), k
        assert v["ci95"] == pytest.approx(rep[k]["ci95"], abs=1e-12), k
