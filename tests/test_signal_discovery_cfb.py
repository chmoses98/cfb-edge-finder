"""Football Signal Discovery Lab, Wave 1 (CFB): integrity / leakage tests.

Proves, on synthetic seasons, that:
  * no final score / later game reaches a pregame feature row (target and later games doctored);
  * appending future games never changes an earlier feature row;
  * the feature module cannot reach a market line or the outcome module (import graph + source);
  * the CFBD lines loader drops every score field;
  * opponent adjustment and the CONTROL tier come from the unmodified production path;
  * the evaluator's arithmetic (ATS residual, push, -110 ROI, BH) is right on hand-made rows;
  * the runner refuses to evaluate when the hypothesis file is not named in the protocol.
"""

from __future__ import annotations

import ast
import dataclasses
import gzip
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from script_engine_fakes import synthetic_season

from cfb_edge_finder.archetype_research.replay import history_for, schedule_targets
from cfb_edge_finder.scripting.claims import derive_claims
from cfb_edge_finder.scripting.football import LeagueFitCache
from cfb_edge_finder.scripting.gamelog import FINAL_AFTER, parse_utc
from cfb_edge_finder.signal_discovery import evaluate as E
from cfb_edge_finder.signal_discovery import stats
from cfb_edge_finder.signal_discovery.features import freeze_table, pregame_features
from cfb_edge_finder.signal_discovery.market_lines import consensus, load_lines

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "cfb_edge_finder" / "signal_discovery"


def _games(rows):
    out = {}
    for r in rows:
        g = out.setdefault(
            r.game_id,
            {
                "id": r.game_id,
                "season": r.season,
                "week": r.week,
                "seasonType": "regular",
                "startDate": r.kickoff_utc,
                "completed": True,
                "neutralSite": False,
                "conferenceGame": None,
            },
        )
        g["homeId" if r.site == "home" else "awayId"] = r.team_id
    return list(out.values())


@pytest.fixture(scope="module")
def season():
    rows = synthetic_season()
    targets, excluded = schedule_targets(_games(rows), rows)
    assert not excluded
    return tuple(rows), targets


def _t(targets, week, i=0):
    return [t for t in targets if t.week == week][i]


# ------------------------------------------------------------------ leakage


def test_no_final_score_or_later_game_enters_pregame_features(season):
    rows, targets = season
    target = _t(targets, 5)
    base = pregame_features(rows, target, LeagueFitCache())
    cutoff = parse_utc(base["football_data_cutoff"])
    doctored = tuple(
        dataclasses.replace(
            r,
            points_for=(r.points_for or 0) + 60,
            points_against=0.0,
            box={k: (v * 3 if isinstance(v, float) else v) for k, v in r.box.items()},
        )
        if r.kickoff() + FINAL_AFTER >= cutoff
        else r
        for r in rows
    )
    again = pregame_features(doctored, target, LeagueFitCache())
    assert again == base
    # non-vacuous: a game played after the doctored results IS changed
    later = _t(targets, 8)
    assert pregame_features(doctored, later, LeagueFitCache()) != pregame_features(rows, later, LeagueFitCache())


def test_appending_future_games_never_changes_an_earlier_feature_row(season):
    rows, targets = season
    early = [_t(targets, w) for w in (2, 4, 6)]
    first = [pregame_features(rows, t, LeagueFitCache()) for t in early]
    future = tuple(
        dataclasses.replace(r, game_id=f"F{r.game_id}", kickoff_utc=r.kickoff_utc.replace("2026-", "2027-"))
        for r in rows
    )
    again = [pregame_features(rows + future, t, LeagueFitCache()) for t in early]
    assert first == again


def test_rolling_metrics_and_opponent_adjustment_stop_at_cutoff(season):
    rows, targets = season
    target = _t(targets, 6)
    cutoff, history = history_for(rows, target)
    row = pregame_features(rows, target, LeagueFitCache())
    assert row["history_latest_kickoff"] < target.kickoff_utc
    assert all(r.kickoff() + FINAL_AFTER < parse_utc(cutoff) for r in history)
    # games counted in a team's adjusted profile are exactly its pre-cutoff games
    assert row["home_off_games.points_per_game"] == sum(1 for r in history if r.team_id == target.home_id)
    # no current-season full-season average: a team's game count grows week to week
    nxt = [t for t in targets if t.week == 9 and target.home_id in (t.home_id, t.away_id)]
    if nxt:
        side = "home" if nxt[0].home_id == target.home_id else "away"
        later = pregame_features(rows, nxt[0], LeagueFitCache())
        assert later[f"{side}_off_games.points_per_game"] > row["home_off_games.points_per_game"]


def test_control_tier_is_the_unmodified_production_claim(season):
    rows, targets = season
    from cfb_edge_finder.archetype_research.replay import build_pregame_content

    for t in targets[40:70]:
        cutoff, history = history_for(rows, t)
        content = build_pregame_content(history, t, LeagueFitCache())
        claims, _ = derive_claims(content["matchup_findings"], {"home": t.home_name, "away": t.away_name}, None, [])
        row = pregame_features(rows, t, LeagueFitCache())
        c = claims["control"]
        assert (row["control_side"], row["control_strength"]) == ((c["side"], c["strength"]) if c else (None, None))
        assert (
            row["net.sustained_efficiency"]
            == content["matchup_profile"]["dimensions"]["sustained_efficiency"]["net_home_advantage"]
        )


def test_feature_table_hash_is_deterministic(season):
    rows, targets = season
    a = [pregame_features(rows, t, LeagueFitCache()) for t in targets[30:45]]
    b = [pregame_features(rows, t, LeagueFitCache()) for t in targets[30:45]]
    assert freeze_table(a) == freeze_table(b)


# ------------------------------------------------------------------ market / outcome walls


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_feature_module_cannot_reach_lines_or_outcomes():
    imports = _imports(PKG / "features.py")
    assert not any("market_lines" in m or "outcomes" in m or "evaluate" in m or "kalshi" in m for m in imports)
    tree = ast.parse((PKG / "features.py").read_text())
    docs = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)
    }
    code_strings = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    ]
    assert not any(s in c for c in code_strings for s in ("lines_", "homeScore", "homePoints", "spread", "moneyline"))
    # runtime: importing the feature module loads neither side of the wall
    code = (
        "import sys; import cfb_edge_finder.signal_discovery.features; "
        "walls=('signal_discovery.market_lines','signal_discovery.outcomes','signal_discovery.evaluate'); "
        "bad=[m for m in sys.modules if m.endswith(walls)]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True).returncode == 0


def test_closing_market_never_enters_the_football_builder():
    # the production builder is market-blind by construction; the research layer adds no market input
    from cfb_edge_finder.scripting.football import FootballPacket

    names = {f.name for f in dataclasses.fields(FootballPacket)}
    assert not any(k in names for k in ("spread", "line", "price", "total", "moneyline", "odds"))


def test_lines_loader_drops_scores(tmp_path):
    game = {
        "id": 1,
        "season": 2020,
        "week": 1,
        "seasonType": "regular",
        "startDate": "2020-09-05T16:00:00Z",
        "homeTeam": "A",
        "awayTeam": "B",
        "homeTeamId": 10,
        "awayTeamId": 20,
        "homeScore": 41,
        "awayScore": 3,
        "lines": [
            {
                "provider": "consensus",
                "spread": -7.5,
                "overUnder": 55,
                "spreadOpen": None,
                "overUnderOpen": None,
                "homeMoneyline": None,
                "awayMoneyline": None,
                "formattedSpread": "A -7.5",
            },
            {
                "provider": "Draft Kings",
                "spread": -6.5,
                "overUnder": 54,
                "spreadOpen": -7,
                "overUnderOpen": 53,
                "homeMoneyline": -250,
                "awayMoneyline": 200,
                "formattedSpread": "A -6.5",
            },
            {
                "provider": "DraftKings",
                "spread": -9.0,
                "overUnder": 99,
                "spreadOpen": None,
                "overUnderOpen": None,
                "homeMoneyline": None,
                "awayMoneyline": None,
                "formattedSpread": "A -9",
            },
        ],
    }
    row = consensus(game)
    flat = json.dumps(row)
    assert "41" not in flat.replace("41.", "") and "Score" not in flat
    assert row["spread_home"] == -7.0  # median of the two DISTINCT providers (alias collapsed, first kept)
    assert row["ml_exec"]["provider"] == "DraftKings"
    p = tmp_path / "lines_regular.json.gz"
    with gzip.open(p, "wt") as fh:
        json.dump([game], fh)
    assert load_lines(tmp_path)["1"]["total"] == 54.5


# ------------------------------------------------------------------ evaluator arithmetic


def _row(gid, season, home_margin, spread, total_pts=50.0, total_line=50.0, control=None, strength=None):
    return {
        "game_id": gid,
        "season": season,
        "week": 3,
        "season_type": "regular",
        "home": f"H{gid}",
        "away": f"A{gid}",
        "home_id": "1",
        "away_id": "2",
        "neutral_site": False,
        "conference_game": True,
        "fcs_involved": False,
        "control_side": control,
        "control_strength": strength,
        "closeness": False,
        "pace_claim": None,
        "scoring_env_claim": None,
        "defensive_suppression_claim": False,
        "disruption_sides": [],
        "finding_codes": [],
        "prior_games_home": 3,
        "prior_games_away": 3,
        "o.home_margin": home_margin,
        "o.home_points": 25 + home_margin / 2,
        "o.away_points": 25 - home_margin / 2,
        "o.total_points": total_pts,
        "o.home_1h": None,
        "o.away_1h": None,
        "early_season": True,
        "m.spread_home": spread,
        "m.spread_open_home": None,
        "m.total": total_line,
        "m.ml_home_novig": None,
        "m.ml_exec": None,
        "m.dispersion_flag": False,
        "home_ats_resid": home_margin + spread,
        "total_resid": total_pts - total_line,
        "home_win": 1.0 if home_margin > 0 else 0.0,
    }


def test_ats_residual_push_and_roi_arithmetic():
    s = E.ats_summary([3.0, -1.0, 0.0, 2.5])
    assert (s["covers"], s["losses"], s["pushes"]) == (2, 1, 1)
    assert s["cover_rate"] == pytest.approx(2 / 3)
    assert s["roi_assumed_110"] == pytest.approx((2 * 100 / 110 - 1) / 3)
    assert s["break_even"] == pytest.approx(110 / 210)


def test_side_orientation_of_spread_and_margin():
    rows = [_row("1", 2015, home_margin=-10.0, spread=3.0, control="away", strength="MODERATE")]
    norms = E.season_norms(rows)
    srows = E.side_rows(rows, {"population": {"rule": "control", "strength": "MODERATE"}, "side": "control"}, norms)
    x = srows[0]
    assert x["side"] == "away" and x["margin"] == 10.0 and x["spread_s"] == -3.0 and x["ats_resid"] == 7.0


def test_benjamini_hochberg_matches_reference():
    q = stats.benjamini_hochberg([0.01, 0.04, 0.03, None, 0.5])
    # m = 4: raw p*m/rank = 0.04, 0.06, 0.0533, 0.5 -> step-up minimum
    assert q[0] == pytest.approx(0.04)
    assert q[1] == pytest.approx(0.04 * 4 / 3) and q[2] == pytest.approx(0.04 * 4 / 3)
    assert q[3] is None and q[4] == pytest.approx(0.5)


def test_evaluate_all_runs_on_synthetic_rows_and_assigns_status():
    rng = np.random.default_rng(1)
    rows = []
    for i in range(1200):
        season = 2014 + i % 12
        ctrl = "home" if i % 3 == 0 else None
        m = float(rng.normal(7 if ctrl else 0, 15))
        rows.append(
            _row(
                str(i),
                season,
                m if m != 0 else 1.0,
                float(-rng.normal(7 if ctrl else 0, 3)),
                control=ctrl,
                strength="STRONG" if ctrl else None,
            )
        )
    from cfb_edge_finder.signal_discovery.hypotheses import SET1, STATUS_RULE

    specs = [h for h in SET1 if h["id"] in ("CFB-SIG-003", "CFB-BASE-001", "CFB-SIG-018")]
    out = E.evaluate_all(rows, specs, STATUS_RULE)
    st = {s["id"]: s["status"] for s in out["summary"]}
    assert st["CFB-SIG-018"] == "DATA_UNAVAILABLE" and st["CFB-BASE-001"] == "BASELINE_REFERENCE"
    assert st["CFB-SIG-003"] in {"APPROX_EFFICIENT", "FOOTBALL_VALIDATED", "MARKET_WATCH", "REJECTED", "OVERPRICED"}


def test_runner_refuses_unregistered_hypotheses(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_signal_discovery_cfb as R

    bogus = tmp_path / "h.json"
    bogus.write_text(json.dumps({"hypotheses": [], "tampered": True}))
    with pytest.raises(SystemExit):
        R._check_frozen(bogus)


def test_registered_set1_file_matches_code():
    """The frozen hypothesis file in git equals the specs in hypotheses.py (no silent edit after freezing)."""
    path = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave1" / "hypotheses_set1.json"
    if not path.exists():
        pytest.skip("set 1 not frozen yet")
    from cfb_edge_finder.signal_discovery.hypotheses import SET1, STATUS_RULE, WALK_FORWARD

    frozen = json.loads(path.read_text())
    assert frozen["hypotheses"] == json.loads(json.dumps(SET1))
    assert frozen["walk_forward"] == json.loads(json.dumps(WALK_FORWARD))
    assert frozen["status_rule"] == json.loads(json.dumps(STATUS_RULE))
