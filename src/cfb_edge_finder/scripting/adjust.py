"""Opponent adjustment: a ridge offense/defense decomposition. PURE, deterministic.

*** THE MODEL (one fit per metric, per cutoff) ***

For every team-game row r (team t's offense against opponent d) with an
observed per-game value y_r = num_r / den_r:

    y_r  =  mu  +  h * x_r  +  o_t  +  g_o * [t not FBS]  +  d_d  +  g_d * [d not FBS]  +  e_r

    mu     league baseline (unpenalised)
    x_r    +1 home, -1 away, 0 neutral site; h is half the home/away gap
    o_t    team t's OFFENSIVE effect     (positive = produces more of the metric)
    d_d    team d's DEFENSIVE effect     (positive = ALLOWS more of the metric)
    g_o/g_d  shared offsets for non-FBS teams, so an FCS opponent is not
             treated as an average FBS team

Weighted ridge least squares. Each row is weighted by its denominator
relative to the mean denominator (a 90-play game says more about yards per
play than a 50-play game), capped to [0.25, 2.5]. The penalty on every team
effect is lambda, stated in GAMES: lambda = 4 means "a team's effect starts
as four average-weight games at exactly league average". With n games
against average opponents the estimate keeps roughly n / (n + lambda) of the
raw deviation, so a team two games in carries a 67% prior weight and cannot
look certain. Non-FBS teams get a stronger penalty (8 games) toward their
shared offset.

    beta  = (X'WX + Lambda)^-1 X'Wy
    resid = y - X beta
    s2    = sum(w * resid^2) / max(N - p_eff, 1),  p_eff = tr((X'WX + Lambda)^-1 X'WX)
    Var   = s2 * (X'WX + Lambda)^-1                (posterior-style uncertainty)

Published per team and unit:
    adjusted  = mu + o_t (+ g_o)     "this offense against an average defense, neutral site"
    adjusted  = mu + d_t (+ g_d)     "this defense against an average offense, neutral site"
    se        = sqrt(c' Var c) for the matching contrast c
    games, effective_n = sum of weights, prior_weight = lambda / (lambda + effective_n)

*** LEAKAGE ***
`fit_metric` takes rows through `gamelog.before(rows, cutoff)` and nothing
else. A fit is a pure function of the rows strictly before the cutoff, so a
later-season game can never move an earlier estimate:
`tests/test_script_engine_adjustment.py` fits the same cutoff on a truncated
season and on the full season and requires identical output.

*** NEVER A SILENT SUBSTITUTION ***
A metric whose team cannot be estimated publishes `adjusted: None` and a
quality of UNAVAILABLE. A descriptive metric (`adjust=False`) publishes
`adjusted: None` and NOT_ADJUSTED. Nothing ever copies `raw` into
`adjusted`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from cfb_edge_finder.scripting.gamelog import (
    TeamGame,
    before,
    iso_utc,
    opponent_row,
    pair_index,
    parse_utc,
    rows_fingerprint,
)
from cfb_edge_finder.scripting.metrics import BY_ID, METRICS, MetricDef

OFFENSE = "offense"
DEFENSE = "defense"

Q_OK = "OK"
Q_THIN = "THIN_SAMPLE"
Q_PRIOR_HEAVY = "PRIOR_HEAVY"
Q_UNAVAILABLE = "UNAVAILABLE"
Q_NOT_ADJUSTED = "NOT_ADJUSTED"

#: Fewer games than this and an estimate is THIN regardless of its prior weight.
MIN_GAMES_FOR_OK = 3


@dataclass(frozen=True)
class AdjustConfig:
    prior_games: float = 4.0
    nonfbs_prior_games: float = 8.0
    group_prior_games: float = 0.5
    home_prior_games: float = 1.0
    weight_floor: float = 0.25
    weight_cap: float = 2.5

    def describe(self) -> dict[str, float]:
        return {
            "prior_games_fbs": self.prior_games,
            "prior_games_non_fbs": self.nonfbs_prior_games,
            "prior_games_group_offset": self.group_prior_games,
            "prior_games_home_effect": self.home_prior_games,
            "weight_floor": self.weight_floor,
            "weight_cap": self.weight_cap,
        }


DEFAULT_CONFIG = AdjustConfig()


@dataclass(frozen=True)
class UnitEstimate:
    team_id: str
    unit: str
    raw: float | None
    adjusted: float | None
    se: float | None
    games: int
    effective_n: float
    prior_weight: float | None
    residual_sd: float | None
    quality: str


@dataclass(frozen=True)
class MetricFit:
    metric_id: str
    cutoff: str
    rows_fingerprint: str
    observations: int
    mu: float | None
    home_effect: float | None
    sigma: float | None
    estimates: dict[tuple[str, str], UnitEstimate]
    fbs_teams: tuple[str, ...]
    adjusted: bool
    _dist: dict[str, tuple[float, float, int] | None] = field(default_factory=dict, compare=False, repr=False)

    def get(self, team_id: str, unit: str) -> UnitEstimate | None:
        return self.estimates.get((team_id, unit))

    def distribution(self, unit: str) -> tuple[float, float, int] | None:
        """Mean, SD and count of the FBS universe's values for this unit.

        Adjusted values when the metric is adjusted, raw otherwise. Used for
        standardising matchup comparisons; never for ranking a team against
        a universe that does not contain it."""
        if unit in self._dist:
            return self._dist[unit]
        values = [
            est.adjusted if self.adjusted else est.raw
            for team in self.fbs_teams
            if (est := self.estimates.get((team, unit))) is not None
        ]
        values = [v for v in values if v is not None]
        if len(values) < 2:
            self._dist[unit] = None
            return None
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        self._dist[unit] = (mean, math.sqrt(var), len(values))
        return self._dist[unit]


@dataclass(frozen=True)
class _Obs:
    team: str
    opponent: str
    site: str
    num: float
    den: float
    value: float


def _divisions(rows: list[TeamGame]) -> dict[str, str]:
    """Each team's division, by majority over the rows that state it."""
    votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        votes[row.team_id][row.team_division] += 1
        votes[row.opponent_id][row.opponent_division] += 1
    out = {}
    for team, counts in votes.items():
        known = {k: v for k, v in counts.items() if k != "unknown"} or counts
        out[team] = sorted(known.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return out


def observations(rows: list[TeamGame], metric: MetricDef) -> list[_Obs]:
    index = pair_index(rows)
    out = []
    for row in rows:
        observed = metric.extract(row, opponent_row(row, index))
        if observed is None:
            continue
        num, den = observed
        if den <= 0:
            continue
        out.append(_Obs(row.team_id, row.opponent_id, row.site, num, den, num / den))
    return out


def fbs_components(rows: list[TeamGame], divisions: dict[str, str]) -> int:
    """Connected components of the FBS-vs-FBS schedule graph.

    One component means every FBS team's schedule links to every other's, so
    an adjustment can place them on one scale. Week 1, with a dozen isolated
    pairs, it cannot -- and the fit says so rather than pretending."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    fbs = sorted(t for t, d in divisions.items() if d == "fbs")
    for team in fbs:
        find(team)
    for row in rows:
        if divisions.get(row.team_id) == "fbs" and divisions.get(row.opponent_id) == "fbs":
            a, b = find(row.team_id), find(row.opponent_id)
            if a != b:
                parent[max(a, b)] = min(a, b)
    return len({find(t) for t in fbs})


def fit_metric(
    rows: list[TeamGame],
    metric: MetricDef | str,
    cutoff: datetime | str,
    config: AdjustConfig = DEFAULT_CONFIG,
) -> MetricFit:
    metric = BY_ID[metric] if isinstance(metric, str) else metric
    history = before(rows, cutoff)
    divisions = _divisions(history)
    obs = observations(history, metric)
    cutoff_iso = iso_utc(parse_utc(cutoff))
    fingerprint = rows_fingerprint(history)
    fbs_teams = tuple(sorted(t for t, d in divisions.items() if d == "fbs"))

    raw = _raw_rates(obs)
    games = _games(obs)

    if not metric.adjust or len(obs) < 8:
        estimates = {}
        for (team, unit), (num, den) in raw.items():
            n = games.get((team, unit), 0)
            estimates[(team, unit)] = UnitEstimate(
                team_id=team,
                unit=unit,
                raw=_safe_div(num, den),
                adjusted=None,
                se=None,
                games=n,
                effective_n=float(n),
                prior_weight=None,
                residual_sd=None,
                quality=Q_NOT_ADJUSTED if not metric.adjust else Q_UNAVAILABLE,
            )
        return MetricFit(
            metric_id=metric.metric_id,
            cutoff=cutoff_iso,
            rows_fingerprint=fingerprint,
            observations=len(obs),
            mu=None,
            home_effect=None,
            sigma=None,
            estimates=estimates,
            fbs_teams=fbs_teams,
            adjusted=False,
        )

    teams = sorted({o.team for o in obs} | {o.opponent for o in obs})
    t_index = {t: i for i, t in enumerate(teams)}
    n_teams = len(teams)
    # Parameter layout: mu, h, g_o, g_d, o_t..., d_t...
    p = 4 + 2 * n_teams
    off0, def0 = 4, 4 + n_teams

    mean_den = sum(o.den for o in obs) / len(obs)
    X = np.zeros((len(obs), p))
    y = np.zeros(len(obs))
    w = np.zeros(len(obs))
    for i, o in enumerate(obs):
        X[i, 0] = 1.0
        X[i, 1] = 1.0 if o.site == "home" else (-1.0 if o.site == "away" else 0.0)
        if divisions.get(o.team) != "fbs":
            X[i, 2] = 1.0
        if divisions.get(o.opponent) != "fbs":
            X[i, 3] = 1.0
        X[i, off0 + t_index[o.team]] = 1.0
        X[i, def0 + t_index[o.opponent]] = 1.0
        y[i] = o.value
        if metric.weight_by_denominator and mean_den > 0:
            w[i] = min(max(o.den / mean_den, config.weight_floor), config.weight_cap)
        else:
            w[i] = 1.0

    penalty = np.zeros(p)
    penalty[0] = 1e-9
    penalty[1] = config.home_prior_games
    penalty[2] = penalty[3] = config.group_prior_games
    for team, i in t_index.items():
        lam = config.prior_games if divisions.get(team) == "fbs" else config.nonfbs_prior_games
        penalty[off0 + i] = lam
        penalty[def0 + i] = lam

    XtW = X.T * w
    XtWX = XtW @ X
    A = XtWX + np.diag(penalty)
    A_inv = np.linalg.inv(A)
    beta = A_inv @ (XtW @ y)
    resid = y - X @ beta
    p_eff = float(np.trace(A_inv @ XtWX))
    dof = max(len(obs) - p_eff, 1.0)
    s2 = float(np.sum(w * resid**2) / dof)

    weight_sum: dict[tuple[str, str], float] = defaultdict(float)
    resid_sq: dict[tuple[str, str], list[float]] = defaultdict(list)
    for i, o in enumerate(obs):
        weight_sum[(o.team, "offense")] += w[i]
        weight_sum[(o.opponent, "defense")] += w[i]
        resid_sq[(o.team, "offense")].append(float(resid[i]))
        resid_sq[(o.opponent, "defense")].append(float(resid[i]))

    mu = float(beta[0])
    estimates: dict[tuple[str, str], UnitEstimate] = {}
    for team, i in t_index.items():
        non_fbs = divisions.get(team) != "fbs"
        lam = penalty[off0 + i]
        for unit, col, group in (("offense", off0 + i, 2), ("defense", def0 + i, 3)):
            idx = [0, col] + ([group] if non_fbs else [])
            value = float(sum(beta[j] for j in idx))
            # c' A^-1 c for a 0/1 contrast is the sum of the selected sub-block.
            quad = float(A_inv[np.ix_(idx, idx)].sum())
            se = math.sqrt(max(quad * s2, 0.0))
            eff = float(weight_sum.get((team, unit), 0.0))
            n = games.get((team, unit), 0)
            residuals = resid_sq.get((team, unit), [])
            num, den = raw.get((team, unit), (0.0, 0.0))
            prior_weight = lam / (lam + eff) if (lam + eff) > 0 else None
            estimates[(team, unit)] = UnitEstimate(
                team_id=team,
                unit=unit,
                raw=_safe_div(num, den),
                adjusted=value if n > 0 else None,
                se=se if n > 0 else None,
                games=n,
                effective_n=eff,
                prior_weight=prior_weight,
                residual_sd=(
                    math.sqrt(sum(r * r for r in residuals) / len(residuals)) if len(residuals) >= 2 else None
                ),
                quality=_quality(n, prior_weight),
            )

    return MetricFit(
        metric_id=metric.metric_id,
        cutoff=cutoff_iso,
        rows_fingerprint=fingerprint,
        observations=len(obs),
        mu=mu,
        home_effect=float(beta[1]),
        sigma=math.sqrt(s2),
        estimates=estimates,
        fbs_teams=fbs_teams,
        adjusted=True,
    )


def _quality(games: int, prior_weight: float | None) -> str:
    if games == 0:
        return Q_UNAVAILABLE
    if games < MIN_GAMES_FOR_OK:
        return Q_THIN
    if prior_weight is not None and prior_weight > 0.5:
        return Q_PRIOR_HEAVY
    return Q_OK


def _safe_div(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def _raw_rates(obs: list[_Obs]) -> dict[tuple[str, str], tuple[float, float]]:
    totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for o in obs:
        for key in ((o.team, OFFENSE), (o.opponent, DEFENSE)):
            totals[key][0] += o.num
            totals[key][1] += o.den
    return {k: (v[0], v[1]) for k, v in totals.items()}


def _games(obs: list[_Obs]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for o in obs:
        counts[(o.team, OFFENSE)] += 1
        counts[(o.opponent, DEFENSE)] += 1
    return dict(counts)


@dataclass(frozen=True)
class LeagueFit:
    """Every metric fitted at one cutoff, plus the schedule-graph diagnostics."""

    cutoff: str
    rows_fingerprint: str
    fits: dict[str, MetricFit]
    divisions: dict[str, str]
    fbs_components: int
    median_fbs_games: float
    games_in_window: int
    config: AdjustConfig

    @property
    def stable(self) -> bool:
        return self.fbs_components == 1 and self.median_fbs_games >= MIN_GAMES_FOR_OK

    def stability(self) -> dict[str, object]:
        return {
            "stable": self.stable,
            "fbs_schedule_components": self.fbs_components,
            "median_fbs_games": self.median_fbs_games,
            "games_in_window": self.games_in_window,
            "rule": (
                "stable when every FBS team is connected through FBS-vs-FBS games to every other (one component) "
                f"and the median FBS team has at least {MIN_GAMES_FOR_OK} games"
            ),
        }


def fit_league(
    rows: list[TeamGame],
    cutoff: datetime | str,
    config: AdjustConfig = DEFAULT_CONFIG,
    metric_ids: tuple[str, ...] | None = None,
) -> LeagueFit:
    history = before(rows, cutoff)
    divisions = _divisions(history)
    chosen = [m for m in METRICS if metric_ids is None or m.metric_id in metric_ids]
    fits = {m.metric_id: fit_metric(history, m, cutoff, config) for m in chosen}
    per_team: dict[str, int] = defaultdict(int)
    for row in history:
        per_team[row.team_id] += 1
    fbs_games = sorted(per_team.get(t, 0) for t, d in divisions.items() if d == "fbs")
    median = float(fbs_games[len(fbs_games) // 2]) if fbs_games else 0.0
    return LeagueFit(
        cutoff=iso_utc(parse_utc(cutoff)),
        rows_fingerprint=rows_fingerprint(history),
        fits=fits,
        divisions=divisions,
        fbs_components=fbs_components(history, divisions),
        median_fbs_games=median,
        games_in_window=len({row.game_id for row in history}),
        config=config,
    )


def rank_in_universe(fit: MetricFit, team_id: str, unit: str, higher_is_better: bool) -> tuple[int | None, int]:
    """Rank 1 = best among FBS teams with a value, and the universe size.

    Ties break on team id so the rank is deterministic. A non-FBS team has
    no rank in the FBS universe (None), rather than a rank it was never
    measured against."""
    scored = []
    for team in fit.fbs_teams:
        est = fit.estimates.get((team, unit))
        if est is None:
            continue
        value = est.adjusted if fit.adjusted else est.raw
        if value is not None:
            scored.append((team, value))
    scored.sort(key=lambda tv: ((-tv[1]) if higher_is_better else tv[1], tv[0]))
    order = [t for t, _ in scored]
    rank = order.index(team_id) + 1 if team_id in order else None
    return rank, len(order)
