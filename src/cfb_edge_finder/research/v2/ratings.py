"""Team-strength systems for V2, fit strictly from games before an as-of.

All functions take the canonical game table (research/v2/games.py) and
an ordering key; a game is usable as evidence for a prediction only if
its (season, week) is strictly before the prediction's (season, week).
The walk-forward drivers in tournament.py enforce that by slicing before
calling anything here, and every function re-checks with an assertion.

Systems:
  1. `RidgeMarginRatings` -- a single-strength ridge model on MARGIN with
     home-field, optional exponential recency weights across seasons and
     within season, and optional per-team prior means (shrink toward a
     preseason prior instead of zero). Also fits separate offense /
     defense strengths on points scored (`fit_offense_defense`).
  2. `Elo` -- classic margin-of-victory Elo with preseason regression to
     the mean (or to an external prior), with K and regression fraction
     as parameters. Sequential, so it is naturally leakage-safe when
     driven game by game in kickoff order.

FCS opponents: pooled into one pseudo-team per system (as in V1) -- an
FCS side contributes to the FBS team's evidence but is never rated on
its own. The pooled FCS parameter is shrunk lightly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

FCS_ID = "__fcs__"


def _side_ids(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    home = np.where(df["home_class"].values == "fbs", df["home"].values, FCS_ID)
    away = np.where(df["away_class"].values == "fbs", df["away"].values, FCS_ID)
    return home, away


@dataclass
class RidgeMarginRatings:
    """margin ~ hfa*home_ind + s[home] - s[away]  (weighted ridge).

    weights: per-game weights (recency); prior_mean: dict team -> prior
    strength the ridge shrinks toward (default 0); lam: ridge strength
    (in games-equivalent units: the penalty is lam * (s - prior)^2)."""

    lam: float = 8.0
    fcs_lam: float = 2.0
    strength: dict[str, float] = field(default_factory=dict)
    hfa: float = 0.0
    intercept: float = 0.0
    n_games: int = 0

    def fit(
        self,
        games: pd.DataFrame,
        *,
        weights: np.ndarray | None = None,
        prior_mean: dict[str, float] | None = None,
        prior_weight: dict[str, float] | None = None,
    ) -> RidgeMarginRatings:
        home, away = _side_ids(games)
        teams = sorted(set(home) | set(away))
        idx = {t: i for i, t in enumerate(teams)}
        n = len(games)
        k = len(teams)
        w = np.ones(n) if weights is None else np.asarray(weights, float)
        # design: [hfa_home_indicator, intercept(neutral-aware)?] -- we use
        # a single hfa on non-neutral games; margin is home - away so the
        # intercept is the hfa itself.
        X = np.zeros((n, k + 1))
        hi = np.where(games["neutral"].values, 0.0, 1.0)
        X[:, 0] = hi
        rows = np.arange(n)
        X[rows, 1 + np.array([idx[t] for t in home])] += 1.0
        X[rows, 1 + np.array([idx[t] for t in away])] -= 1.0
        y = games["margin"].values.astype(float)
        pen = np.zeros(k + 1)
        prior = np.zeros(k + 1)
        for t, i in idx.items():
            base = self.fcs_lam if t == FCS_ID else self.lam
            if prior_weight and t in prior_weight:
                base = float(prior_weight[t])
            pen[1 + i] = base
            if prior_mean and t in prior_mean:
                prior[1 + i] = float(prior_mean[t])
        pen[0] = 1e-6
        Xw = X * w[:, None]
        A = X.T @ Xw + np.diag(pen)
        b = Xw.T @ y + pen * prior
        beta = np.linalg.solve(A, b)
        self.hfa = float(beta[0])
        self.strength = {t: float(beta[1 + i]) for t, i in idx.items()}
        self.n_games = n
        return self

    def predict_margin(self, home: str, away: str, neutral: bool, home_fbs=True, away_fbs=True) -> float:
        h = self.strength.get(home if home_fbs else FCS_ID, 0.0)
        a = self.strength.get(away if away_fbs else FCS_ID, 0.0)
        return (0.0 if neutral else self.hfa) + h - a


@dataclass
class RidgeScoreRatings:
    """points_for ~ mu + hfa*home_ind + off[team] - def[opp], two rows per game."""

    lam: float = 8.0
    fcs_lam: float = 2.0
    offense: dict[str, float] = field(default_factory=dict)
    defense: dict[str, float] = field(default_factory=dict)
    mu: float = 0.0
    hfa: float = 0.0

    def fit(self, games: pd.DataFrame, *, weights: np.ndarray | None = None,
            prior_off: dict[str, float] | None = None, prior_def: dict[str, float] | None = None) -> RidgeScoreRatings:
        home, away = _side_ids(games)
        teams = sorted(set(home) | set(away))
        idx = {t: i for i, t in enumerate(teams)}
        n = len(games)
        k = len(teams)
        w0 = np.ones(n) if weights is None else np.asarray(weights, float)
        hi = np.where(games["neutral"].values, 0.0, 1.0)
        # rows: home offense vs away defense, then away offense vs home defense
        X = np.zeros((2 * n, 2 + 2 * k))
        y = np.zeros(2 * n)
        w = np.concatenate([w0, w0])
        r = np.arange(n)
        hidx = np.array([idx[t] for t in home])
        aidx = np.array([idx[t] for t in away])
        X[r, 0] = 1.0
        X[r, 1] = hi
        X[r, 2 + hidx] = 1.0
        X[r, 2 + k + aidx] = -1.0
        y[r] = games["home_points"].values
        X[n + r, 0] = 1.0
        X[n + r, 1] = -hi
        X[n + r, 2 + aidx] = 1.0
        X[n + r, 2 + k + hidx] = -1.0
        y[n + r] = games["away_points"].values
        pen = np.zeros(2 + 2 * k)
        prior = np.zeros(2 + 2 * k)
        for t, i in idx.items():
            base = self.fcs_lam if t == FCS_ID else self.lam
            pen[2 + i] = base
            pen[2 + k + i] = base
            if prior_off and t in prior_off:
                prior[2 + i] = prior_off[t]
            if prior_def and t in prior_def:
                prior[2 + k + i] = prior_def[t]
        pen[:2] = 1e-6
        Xw = X * w[:, None]
        beta = np.linalg.solve(X.T @ Xw + np.diag(pen), Xw.T @ y + pen * prior)
        self.mu, self.hfa = float(beta[0]), float(beta[1])
        self.offense = {t: float(beta[2 + i]) for t, i in idx.items()}
        self.defense = {t: float(beta[2 + k + i]) for t, i in idx.items()}
        return self

    def predict_points(self, home, away, neutral, home_fbs=True, away_fbs=True) -> tuple[float, float]:
        h = home if home_fbs else FCS_ID
        a = away if away_fbs else FCS_ID
        hf = 0.0 if neutral else self.hfa
        hp = self.mu + hf + self.offense.get(h, 0.0) - self.defense.get(a, 0.0)
        ap = self.mu - hf + self.offense.get(a, 0.0) - self.defense.get(h, 0.0)
        return max(hp, 0.0), max(ap, 0.0)


def recency_weights(games: pd.DataFrame, *, as_of_season: int, season_decay: float, week_decay: float = 1.0,
                    as_of_week: int | None = None) -> np.ndarray:
    """w = season_decay ** (as_of_season - season) * week_decay ** (weeks back within the as-of season)."""
    seasons = games["season"].values
    weeks = games["week"].values
    w = np.power(season_decay, (as_of_season - seasons).astype(float))
    if week_decay != 1.0 and as_of_week is not None:
        same = seasons == as_of_season
        w = np.where(same, w * np.power(week_decay, np.maximum(as_of_week - weeks, 0).astype(float)), w)
    return w


@dataclass
class Elo:
    """Margin-of-victory Elo with preseason regression.

    rating update: r += K * mov_mult * (S - E), E = 1/(1+10^(-(dr+hfa)/400)),
    mov_mult = ln(|margin|+1) * 2.2/(|dr_pre_adj|*0.001+2.2)  (FiveThirtyEight NFL form).
    Points-per-Elo conversion for margin prediction: margin = (dr + hfa)/elo_per_point."""

    k: float = 20.0
    hfa: float = 55.0
    regress: float = 0.30
    mean: float = 1500.0
    elo_per_point: float = 25.0
    fcs_rating: float = 1200.0
    ratings: dict[str, float] = field(default_factory=dict)
    last_season: dict[str, int] = field(default_factory=dict)

    def rating(self, team: str, is_fbs: bool = True) -> float:
        if not is_fbs:
            return self.fcs_rating
        return self.ratings.get(team, self.mean)

    def start_season(self, season: int, prior: dict[str, float] | None = None, prior_w: float = 0.0) -> None:
        for t in list(self.ratings):
            target = self.mean
            if prior and t in prior:
                target = (1 - prior_w) * self.mean + prior_w * prior[t]
            self.ratings[t] = self.ratings[t] + self.regress * (target - self.ratings[t])

    def expected_home(self, home, away, neutral, home_fbs=True, away_fbs=True) -> float:
        dr = self.rating(home, home_fbs) - self.rating(away, away_fbs) + (0.0 if neutral else self.hfa)
        return 1.0 / (1.0 + 10 ** (-dr / 400.0))

    def predict_margin(self, home, away, neutral, home_fbs=True, away_fbs=True) -> float:
        dr = self.rating(home, home_fbs) - self.rating(away, away_fbs) + (0.0 if neutral else self.hfa)
        return dr / self.elo_per_point

    def update(self, home, away, neutral, margin, home_fbs=True, away_fbs=True) -> None:
        rh, ra = self.rating(home, home_fbs), self.rating(away, away_fbs)
        dr = rh - ra + (0.0 if neutral else self.hfa)
        e = 1.0 / (1.0 + 10 ** (-dr / 400.0))
        s = 1.0 if margin > 0 else (0.5 if margin == 0 else 0.0)
        winner_dr = dr if margin > 0 else -dr
        mult = np.log(abs(margin) + 1.0) * 2.2 / (winner_dr * 0.001 + 2.2)
        delta = self.k * mult * (s - e)
        if home_fbs:
            self.ratings[home] = rh + delta
        if away_fbs:
            self.ratings[away] = ra - delta
