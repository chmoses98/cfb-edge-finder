"""EXPERIMENTAL, DISABLED: a venue-only home effect for the scoring baseline. PURE, football only.

*** WHY THIS EXISTS (root cause, docs/HOME_EFFECT_INVESTIGATION.md) ***
The incumbent fits `points_per_game` on the CURRENT season only:

    y_r = mu + h * x_r + o_t + d_d + (non-FBS offsets) + e_r

with every team effect shrunk toward league average (prior = 4 games) and the
home coefficient `h` almost unpenalised (prior = 1 game, shared by every row).
Early in a season each team has one or two games and the schedule is not
random: strong programmes HOST weaker ones. With one game per team, "home
team was better" and "home teams score more" are not separable in the data;
the ridge prior decides, and because one cheap shared parameter can explain
the whole home/away gap while each team effect is expensive, `h` absorbs the
strength gap. The arithmetic is right (margin = +2h, h = half the gap, no
double count); the parameter is not identified. 2h after week 1 was 7.2
(2024), 11.3 (2025) and 19.8 (2026), and falls to ~4-6 by mid-season as
team effects become identified.

*** THE CANDIDATE ***
Two pre-game sources of information, both from seasons that ENDED before the
target season began, enter the same ridge as prior MEANS (not new data):

1. `h` is shrunk toward the previous season's end-of-season home effect
   `h0`, with weight `home_prior_games`.
2. Each team's offence/defence effect is shrunk toward `carryover` times its
   previous end-of-season effect (0 = the incumbent's league-average prior),
   so a strong team hosting a weak one is explained by team strength rather
   than by the venue.

    beta = (X'WX + Lambda)^-1 (X'Wy + Lambda m)        m = prior means

With `carryover = 0`, `home_prior_games = 1` and `h0 = 0` this is EXACTLY the
incumbent's `adjust.fit_metric` for `points_per_game`
(tests/test_home_effect_experimental.py pins it).

*** DISABLED ***
`ENABLED` is False. No production module imports this file; the research
harness passes `enabled=True` explicitly. Nothing here is a projection that
is published, priced or bet.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from cfb_edge_finder.scripting.adjust import (
    DEFAULT_CONFIG,
    AdjustConfig,
    _divisions,
    fit_metric,
    observations,
)
from cfb_edge_finder.scripting.gamelog import TeamGame, before, iso_utc, parse_utc
from cfb_edge_finder.scripting.metrics import BY_ID

ENABLED = False
VERSION = "cfb-home-effect-experimental/0.1.0"
METRIC = "points_per_game"


class DisabledError(RuntimeError):
    """Raised when the experimental pathway is called without an explicit opt-in."""


@dataclass(frozen=True)
class SeasonPrior:
    """End-of-season state of a season that finished before the target season started."""

    source_season: int
    source_last_kickoff: str
    mu: float
    home_effect: float
    offense: dict[str, float]  # team effect o_t (deviation from mu, incl. non-FBS offset)
    defense: dict[str, float]


@dataclass(frozen=True)
class HomeEffectConfig:
    home_prior_games: float = 1.0
    carryover: float = 0.0
    adjust: AdjustConfig = DEFAULT_CONFIG

    def describe(self) -> dict[str, Any]:
        return {"version": VERSION, "home_prior_games": self.home_prior_games, "carryover": self.carryover, **self.adjust.describe()}


#: Frozen candidate (chosen on 2015-2023 only; see docs/HOME_EFFECT_INVESTIGATION.md). Not active anywhere.
FROZEN_CANDIDATE = HomeEffectConfig(home_prior_games=300.0, carryover=0.7)


@dataclass(frozen=True)
class PointsFit:
    cutoff: str
    observations: int
    mu: float
    home_effect: float
    adjusted_offense: dict[str, float]
    adjusted_defense: dict[str, float]
    games: dict[tuple[str, str], int]


def _require(enabled: bool) -> None:
    if not (enabled or ENABLED):
        raise DisabledError("experimental home effect is disabled; research callers must pass enabled=True")


def season_prior(prev_rows: list[TeamGame], target_season_start: datetime | str, config: AdjustConfig = DEFAULT_CONFIG) -> SeasonPrior:
    """The previous season's end-of-season incumbent fit. Every row must have kicked off before the target season."""
    start = parse_utc(target_season_start)
    if not prev_rows:
        raise ValueError("no previous-season rows")
    last = max(r.kickoff() for r in prev_rows)
    if last >= start:
        raise ValueError(f"prior rows reach {iso_utc(last)}, not before target season start {iso_utc(start)}")
    fit = fit_metric(prev_rows, METRIC, start, config)
    if not fit.adjusted or fit.mu is None:
        raise ValueError("previous-season fit is not adjusted")
    off = {t: e.adjusted - fit.mu for (t, u), e in fit.estimates.items() if u == "offense" and e.adjusted is not None}
    dfn = {t: e.adjusted - fit.mu for (t, u), e in fit.estimates.items() if u == "defense" and e.adjusted is not None}
    return SeasonPrior(
        source_season=int(prev_rows[0].season),
        source_last_kickoff=iso_utc(last),
        mu=float(fit.mu),
        home_effect=float(fit.home_effect or 0.0),
        offense=off,
        defense=dfn,
    )


def fit_points(
    rows: list[TeamGame],
    cutoff: datetime | str,
    prior: SeasonPrior | None,
    config: HomeEffectConfig,
    *,
    enabled: bool = False,
) -> PointsFit | None:
    """`points_per_game` ridge with prior means. Uses only games finished before `cutoff`."""
    _require(enabled)
    metric = BY_ID[METRIC]
    history = before(rows, cutoff)
    if prior is not None and parse_utc(prior.source_last_kickoff) >= min((r.kickoff() for r in history), default=parse_utc(cutoff)):
        raise ValueError("season prior overlaps the target season")
    divisions = _divisions(history)
    obs = observations(history, metric)
    if len(obs) < 8:
        return None
    cfg = config.adjust
    teams = sorted({o.team for o in obs} | {o.opponent for o in obs})
    t_index = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    p = 4 + 2 * n
    off0, def0 = 4, 4 + n
    X = np.zeros((len(obs), p))
    y = np.zeros(len(obs))
    for i, o in enumerate(obs):
        X[i, 0] = 1.0
        X[i, 1] = 1.0 if o.site == "home" else (-1.0 if o.site == "away" else 0.0)
        X[i, 2] = 1.0 if divisions.get(o.team) != "fbs" else 0.0
        X[i, 3] = 1.0 if divisions.get(o.opponent) != "fbs" else 0.0
        X[i, off0 + t_index[o.team]] = 1.0
        X[i, def0 + t_index[o.opponent]] = 1.0
        y[i] = o.value
    w = np.ones(len(obs))  # points_per_game is not denominator-weighted (metrics.py)
    penalty = np.zeros(p)
    mean = np.zeros(p)
    penalty[0] = 1e-9
    penalty[1] = config.home_prior_games
    penalty[2] = penalty[3] = cfg.group_prior_games
    for team, i in t_index.items():
        fbs = divisions.get(team) == "fbs"
        lam = cfg.prior_games if fbs else cfg.nonfbs_prior_games
        penalty[off0 + i] = penalty[def0 + i] = lam
        if prior is not None and fbs:
            mean[off0 + i] = config.carryover * prior.offense.get(team, 0.0)
            mean[def0 + i] = config.carryover * prior.defense.get(team, 0.0)
    if prior is not None:
        mean[1] = prior.home_effect
    XtW = X.T * w
    A = XtW @ X + np.diag(penalty)
    beta = np.linalg.solve(A, XtW @ y + penalty * mean)
    mu = float(beta[0])
    games: dict[tuple[str, str], int] = {}
    for o in obs:
        games[(o.team, "offense")] = games.get((o.team, "offense"), 0) + 1
        games[(o.opponent, "defense")] = games.get((o.opponent, "defense"), 0) + 1
    adj_o, adj_d = {}, {}
    for team, i in t_index.items():
        g = [2] if divisions.get(team) != "fbs" else []
        gd = [3] if divisions.get(team) != "fbs" else []
        if games.get((team, "offense"), 0) > 0:
            adj_o[team] = mu + float(beta[off0 + i]) + sum(float(beta[j]) for j in g)
        if games.get((team, "defense"), 0) > 0:
            adj_d[team] = mu + float(beta[def0 + i]) + sum(float(beta[j]) for j in gd)
    return PointsFit(iso_utc(parse_utc(cutoff)), len(obs), mu, float(beta[1]), adj_o, adj_d, games)


def scoring_margin(fit: PointsFit | None, home_id: str, away_id: str, neutral_site: bool) -> dict[str, float | None]:
    """Same formula and clamping as `matchup.scoring_baseline`, on the experimental fit."""
    out: dict[str, float | None] = {"home_points": None, "away_points": None, "home_margin": None, "total_points": None, "home_effect_applied": None}
    if fit is None:
        return out
    try:
        ho, ad = fit.adjusted_offense[home_id], fit.adjusted_defense[away_id]
        ao, hd = fit.adjusted_offense[away_id], fit.adjusted_defense[home_id]
    except KeyError:
        return out
    x = 0.0 if neutral_site else 1.0
    home = max(ho + ad - fit.mu + fit.home_effect * x, 0.0)
    away = max(ao + hd - fit.mu - fit.home_effect * x, 0.0)
    if math.isnan(home) or math.isnan(away):
        return out
    out.update(home_points=home, away_points=away, home_margin=home - away, total_points=home + away, home_effect_applied=fit.home_effect * x)
    return out
