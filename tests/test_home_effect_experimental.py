"""Experimental venue-only home effect (scripting/home_effect_experimental.py). Offline, synthetic data only."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from script_engine_fakes import SEASON_START, synthetic_season

from cfb_edge_finder.scripting import FOOTBALL_MODULES, MARKET_MODULES
from cfb_edge_finder.scripting import home_effect_experimental as HE
from cfb_edge_finder.scripting.adjust import fit_metric
from cfb_edge_finder.scripting.gamelog import TeamGame

ROOT = Path(__file__).resolve().parents[1]
SCRIPTING = ROOT / "src" / "cfb_edge_finder" / "scripting"
CUTOFF = (SEASON_START + timedelta(weeks=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
INCUMBENT = HE.HomeEffectConfig(home_prior_games=1.0, carryover=0.0)


@pytest.fixture(scope="module")
def season() -> list[TeamGame]:
    return synthetic_season()


def _prev_season(rows: list[TeamGame], years: int = 1) -> list[TeamGame]:
    shift = timedelta(days=364 * years)

    def mv(r: TeamGame) -> TeamGame:
        k = (datetime.fromisoformat(r.kickoff_utc.replace("Z", "+00:00")) - shift).strftime("%Y-%m-%dT%H:%M:%SZ")
        return replace(r, kickoff_utc=k, season=r.season - years, game_id="p" + r.game_id)

    return [mv(r) for r in rows]


def test_disabled_by_default(season):
    assert HE.ENABLED is False
    with pytest.raises(HE.DisabledError):
        HE.fit_points(season, CUTOFF, None, INCUMBENT)


def test_no_production_module_imports_the_experiment():
    for mod in set(FOOTBALL_MODULES) | set(MARKET_MODULES):
        if mod == "home_effect_experimental":
            continue
        tree = ast.parse((SCRIPTING / f"{mod}.py").read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom) for a in n.names}
        names |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert not any("home_effect_experimental" in x for x in names), mod


def test_null_config_reproduces_the_incumbent_exactly(season):
    inc = fit_metric(season, "points_per_game", CUTOFF)
    exp = HE.fit_points(season, CUTOFF, None, INCUMBENT, enabled=True)
    assert exp.mu == pytest.approx(inc.mu, abs=1e-9)
    assert exp.home_effect == pytest.approx(inc.home_effect, abs=1e-9)
    for (team, unit), est in inc.estimates.items():
        got = (exp.adjusted_offense if unit == "offense" else exp.adjusted_defense).get(team)
        assert got == pytest.approx(est.adjusted, abs=1e-8), (team, unit)


def test_scoring_margin_matches_the_baseline_formula(season):
    from cfb_edge_finder.scripting.adjust import fit_league
    from cfb_edge_finder.scripting.matchup import GameIdentity, scoring_baseline

    exp = HE.fit_points(season, CUTOFF, None, INCUMBENT, enabled=True)
    league = fit_league(season, CUTOFF, metric_ids=("points_per_game",))
    for neutral in (False, True):
        ident = GameIdentity("e", "F00", "F01", "a", "b", CUTOFF, neutral, 2026)
        base = scoring_baseline(league, ident)
        m = HE.scoring_margin(exp, "F00", "F01", neutral)
        assert m["home_margin"] == pytest.approx(base["home_margin"], abs=0.051)
        assert m["home_effect_applied"] == pytest.approx(
            0.0 if neutral else league.fits["points_per_game"].home_effect, abs=1e-9
        )


def test_future_games_cannot_move_a_fit(season):
    prior = HE.season_prior(_prev_season(season), season[0].kickoff_utc)
    past = [r for r in season if r.kickoff_utc < CUTOFF]
    assert len(past) < len(season)
    early = HE.fit_points(past, CUTOFF, prior, HE.FROZEN_CANDIDATE, enabled=True)
    full = HE.fit_points(season, CUTOFF, prior, HE.FROZEN_CANDIDATE, enabled=True)
    assert early == full


def test_prior_must_come_from_a_finished_earlier_season(season):
    with pytest.raises(ValueError):
        HE.season_prior(season, season[0].kickoff_utc)  # rows overlap the target season
    bad = HE.SeasonPrior(2026, season[-1].kickoff_utc, 25.0, 1.0, {}, {})
    with pytest.raises(ValueError):
        HE.fit_points(season, CUTOFF, bad, HE.FROZEN_CANDIDATE, enabled=True)


def _confounded_week(true_h: float, seed: int = 3) -> tuple[list[TeamGame], dict[str, float]]:
    """One opening week in which every strong team HOSTS a weak one (buy games), plus the previous season."""
    rng = np.random.RandomState(seed)
    n = 40
    strength = {f"T{i:02d}": float(s) for i, s in enumerate(np.linspace(14, -14, n))}
    teams = list(strength)

    def game(gid: str, kick: datetime, home: str, away: str, season_: int, h: float) -> list[TeamGame]:
        hp = 28 + strength[home] / 2 + h + rng.normal(0, 3)
        ap = 28 + strength[away] / 2 - h + rng.normal(0, 3)

        def mk(t, o, site, pf, pa):
            k = kick.strftime("%Y-%m-%dT%H:%M:%SZ")
            return TeamGame(gid, season_, 1, k, t, t, o, o, "fbs", "fbs", site, pf, pa, {}, None)

        return [mk(home, away, "home", hp, ap), mk(away, home, "away", ap, hp)]

    start = datetime(2026, 8, 29, 19, tzinfo=UTC)
    rows: list[TeamGame] = []
    for i in range(n // 2):  # week 1: strong hosts weak
        rows += game(f"g{i}", start, teams[i], teams[n - 1 - i], 2026, true_h)
    prev: list[TeamGame] = []
    order = list(range(n))
    for wk in range(10):  # previous season: random home assignment, so h is identified there
        rng.shuffle(order)
        for j in range(0, n, 2):
            a, b = teams[order[j]], teams[order[j + 1]]
            prev += game(f"p{wk}_{j}", start - timedelta(days=364) + timedelta(weeks=wk), a, b, 2025, true_h)
    return rows + prev, strength


def test_buy_game_confounding_inflates_the_incumbent_and_not_the_candidate():
    """The root cause, reproduced: true 2h = 3, strong teams host weak teams in week 1."""
    rows, _ = _confounded_week(true_h=1.5)
    cur = [r for r in rows if r.season == 2026]
    prev = [r for r in rows if r.season == 2025]
    cutoff = "2026-09-04T08:00:00Z"
    inc = fit_metric(cur, "points_per_game", cutoff)
    prior = HE.season_prior(prev, cur[0].kickoff_utc)
    cand = HE.fit_points(cur, cutoff, prior, HE.FROZEN_CANDIDATE, enabled=True)
    assert 2 * inc.home_effect > 8.0  # true 2h is 3: the venue term swallows most of the strength gap
    assert abs(2 * prior.home_effect - 3.0) < 1.5  # identified when home assignment is random
    assert abs(2 * cand.home_effect - 3.0) < 1.5


def test_committed_study_report_is_consistent():
    rep = json.loads((ROOT / "data/scripting/validation/home_effect_experimental/report.json").read_text())
    assert rep["label"] == "RESEARCH_ONLY"
    assert rep["selected"]["config"]["home_prior_games"] == HE.FROZEN_CANDIDATE.home_prior_games
    assert rep["selected"]["config"]["carryover"] == HE.FROZEN_CANDIDATE.carryover
    for split in ("development", "validation", "test_2026"):
        p = rep["by_split"][split]["paired_selected_vs_incumbent"]["all"]
        assert p["mean_sq_err_diff"] + 2 * p["se"] < 0, split  # better than the incumbent beyond 2 SE on every split
