"""Key-number-exact discrete score distributions with opponent-adjusted pace and scoring efficiency.

*** RESEARCH-ONLY. NOT A VALIDATED BETTING MODEL. NOTHING HERE IS WIRED INTO RUN CFB. ***
Like ``projections/distribution.py`` this module returns probabilities, not recommendations.
Promoting it into the live path requires the held-out comparison in
``docs/reports/DISCRETE_SCORING_EVAL_2026-11-02.md`` to be reviewed and an explicit owner decision.

Why not Normal?  A Normal margin has no mass AT 3, 7, 10, 14, so P(cover -2.5) and P(cover -3.5) differ
only by a smooth density, while real football puts ~9% of margins exactly on 3 and ~6% on 7. A team total
or 1Q total is even worse: scores are sums of 7s, 3s and 6s, not a bell curve. Here each team's score is
an EXACT compound-Poisson law over scoring events (TD worth 6/7/8, FG worth 3, safety worth 2) computed
by Panjer recursion, so the pmf has the correct key-number lumps by construction, and ``P(margin > k)``,
``P(margin == k)`` (push) and ``P(total >= n)`` are exact sums over the pmf.

Components (what each input does):

* pace         ``plays = LEAGUE_PLAYS * pace[team] * pace[opp]`` (multiplicative, opponent-adjusted)
* efficiency   ``points/play = LEAGUE_PPP * off[team] * def[opp] * site`` (scoring efficiency: includes
               finishing drives, field-goal range and explosives because it is realised points)
* schedule     indices are fitted by iterating ``off``/``def`` jointly, so a team that fattened up on
               weak defences is credited for the schedule it actually played; ``schedule_strength`` reports it
* variance     shared game-environment volatility ``sigma_common`` (both teams' rates scale together:
               tempo, weather, game script) and per-team ``sigma_team``; both are FITTED on a calibration
               split, never assumed
* key numbers  ``fg_share`` (fraction of expected points scored as field goals) is FITTED on the same split

Deliberately absent, because the repository's data cannot support it (see the evaluation report):
* EPA/play: only drive summaries and success-rate counts exist in ``team_games.jsonl`` -- no play-level EPA.
* special teams, injury effect sizes: no calibrated magnitudes exist. Injuries are NOT used to move the
  mean; ``PERIOD``/injury adjustments must be supplied explicitly or the call refuses.
* quarter/half scoring shares: ``team_games.jsonl`` has no per-period points. 1Q/1H pmfs require an
  explicit, evidenced ``period_share`` and raise ``MissingPeriodCalibration`` otherwise.
* havoc metrics: excluded by owner instruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

MAX_POINTS = 90  # per-team truncation; mass beyond is < 1e-6 for any realistic rate and is renormalised away

#: TD value pmf after extra point / two-point attempts. Assumed football-rules priors (XP ~95%, ~4% of TDs go for
#: two at ~48%), expectation ~6.95. Documented constants, not fitted.
TD_VALUE_PMF = {6: 0.0688, 7: 0.9120, 8: 0.0192}
SAFETY_RATE = 0.04  # safeties per team-game; negligible, kept so a 2-point score is not impossible
FG_POINTS = 3
SAFETY_POINTS = 2

_E_TD_POINTS = sum(k * v for k, v in TD_VALUE_PMF.items())

LEAGUE_FLOOR_PLAYS = 40.0

#: CFB has no final ties: a regulation tie goes to overtime and the contract settles on the FINAL score.
#: (winner_ot_points, loser_ot_points, probability). ASSUMED prior over typical overtime endings (no overtime
#: scoring data exists in this repository to fit it). It moves <~3% of total mass, only into margins
#: {3, 7} and the totals just above the regulation tie, and the home/away winner is split 50/50.
#: Owner-adjustable; ``build_distribution(resolve_overtime=False)`` leaves the raw regulation pmf.
OT_OUTCOMES = ((7, 0, 0.38), (3, 0, 0.20), (10, 7, 0.12), (14, 7, 0.15), (10, 3, 0.15))


class MissingPeriodCalibration(ValueError):
    """A 1Q/1H pmf was requested without an evidenced period share. We refuse rather than guess."""


KEY_MARGINS = (3, 7, 10, 14)


@dataclass(frozen=True)
class ShapeParams:
    fg_share: float
    sigma_common: float
    sigma_team: float
    #: Calibration of the margin law (2026-11-02, PR #249). BOTH default to 1.0 = the uncalibrated baseline,
    #: bit-for-bit. They are FITTED on a calibration split only (``research/discrete_scoring_eval``):
    #:
    #: ``margin_shrink`` (kappa in (0, 1]) scales the predicted home-minus-away mean difference toward zero while
    #:     preserving the predicted total. Opponent-adjusted ratings from 3-6 games per team are noisy, so their
    #:     raw spreads are over-dispersed; kappa is the regression-to-the-mean factor.
    #: ``key_lift`` (>= 1) multiplies the joint mass of every score pair whose |margin| is a key number
    #:     (3, 7, 10, 14), then renormalises. Compound-Poisson scoring puts correct LUMPS at key numbers but
    #:     still too little TOTAL mass there versus what was observed; this is the one-parameter recalibration.
    margin_shrink: float = 1.0
    key_lift: float = 1.0
    #: Per-margin lifts ``((3, l3), (7, l7), (10, lx), (14, lx))``: same mechanism as ``key_lift`` but each key
    #: number gets its OWN weight (> 0; may be < 1). Empty = none. Applied multiplicatively with ``key_lift``.
    key_lifts: tuple[tuple[int, float], ...] = ()


@dataclass(frozen=True)
class TeamRatings:
    league_plays: float
    league_ppp: float
    home_edge: float  # multiplicative efficiency edge for the home team (away gets 1/home_edge)
    pace: dict[str, float]
    off: dict[str, float]
    defense: dict[str, float]
    games_played: dict[str, int]
    schedule_strength: dict[str, float]  # mean opponent defence index faced (>1 = faced weak defences)


@dataclass(frozen=True)
class GameRow:
    team_id: str
    opp_id: str
    site: str  # home | away | neutral
    plays: float
    points_for: float


def fit_ratings(rows: list[GameRow], *, prior_games: float = 4.0, iterations: int = 25) -> TeamRatings:
    """Opponent-adjusted multiplicative indices with shrinkage toward 1.0 (``prior_games`` pseudo-games)."""
    if not rows:
        raise ValueError("no games to fit")
    plays_mean = float(np.mean([r.plays for r in rows]))
    ppp_mean = float(sum(r.points_for for r in rows) / sum(r.plays for r in rows))
    home_pts = sum(r.points_for for r in rows if r.site == "home")
    away_pts = sum(r.points_for for r in rows if r.site == "away")
    home_edge = float(np.sqrt(home_pts / away_pts)) if home_pts > 0 and away_pts > 0 else 1.0
    site_f = {"home": home_edge, "away": 1.0 / home_edge, "neutral": 1.0}
    teams = sorted({r.team_id for r in rows} | {r.opp_id for r in rows})
    pace = dict.fromkeys(teams, 1.0)
    off = dict.fromkeys(teams, 1.0)
    dfn = dict.fromkeys(teams, 1.0)
    for _ in range(iterations):
        # pace: plays_tg ~ L * pace[t] * pace[o]
        num = dict.fromkeys(teams, prior_games * plays_mean)
        den = dict.fromkeys(teams, prior_games * plays_mean)
        for r in rows:
            num[r.team_id] += r.plays
            den[r.team_id] += plays_mean * pace[r.opp_id]
        pace = {t: num[t] / den[t] for t in teams}
        # offence: ppp_tg ~ L * off[t] * def[o] * site
        nod = dict.fromkeys(teams, prior_games * ppp_mean)
        nonum = {t: prior_games * ppp_mean for t in teams}
        for r in rows:
            ppp = r.points_for / r.plays
            nonum[r.team_id] += ppp
            nod[r.team_id] += ppp_mean * dfn[r.opp_id] * site_f[r.site]
        off = {t: nonum[t] / nod[t] for t in teams}
        # defence: points allowed per play by o ~ L * off[t] * def[o] * site
        dnum = {t: prior_games * ppp_mean for t in teams}
        dden = {t: prior_games * ppp_mean for t in teams}
        for r in rows:
            ppp = r.points_for / r.plays
            dnum[r.opp_id] += ppp
            dden[r.opp_id] += ppp_mean * off[r.team_id] * site_f[r.site]
        dfn = {t: dnum[t] / dden[t] for t in teams}
    count = dict.fromkeys(teams, 0)
    sos_sum = dict.fromkeys(teams, 0.0)
    for r in rows:
        count[r.team_id] += 1
        sos_sum[r.team_id] += dfn[r.opp_id]
    sos = {t: (sos_sum[t] / count[t]) if count[t] else 1.0 for t in teams}
    return TeamRatings(plays_mean, ppp_mean, home_edge, pace, off, dfn, count, sos)


def expected_points(ratings: TeamRatings, team: str, opp: str, site: str) -> float | None:
    """None if either team has no rated history: an unrated team is unpriced, not average."""
    if ratings.games_played.get(team, 0) == 0 or ratings.games_played.get(opp, 0) == 0:
        return None
    site_f = {"home": ratings.home_edge, "away": 1.0 / ratings.home_edge, "neutral": 1.0}[site]
    plays = ratings.league_plays * ratings.pace[team] * ratings.pace[opp]
    ppp = ratings.league_ppp * ratings.off[team] * ratings.defense[opp] * site_f
    return float(plays * ppp)


def _panjer_batch(
    lam_td: np.ndarray, lam_fg: np.ndarray, lam_saf: float | np.ndarray, n: int = MAX_POINTS + 1
) -> np.ndarray:
    """Exact compound-Poisson pmfs, one row per rate triple. Jumps: 2 (safety), 3 (FG), TD values."""
    lam_td = np.asarray(lam_td, dtype=float)
    lam_fg = np.asarray(lam_fg, dtype=float)
    lam_saf = np.broadcast_to(np.asarray(lam_saf, dtype=float), lam_td.shape)
    lam = lam_td + lam_fg + lam_saf
    # jump rates a_j = lambda * g_j  (intensity of a jump of size j)
    rate = {2: lam_saf, 3: lam_fg}
    for pts, p in TD_VALUE_PMF.items():
        rate[pts] = rate.get(pts, 0.0) + lam_td * p
    pmf = np.zeros((lam.shape[0], n))
    pmf[:, 0] = np.exp(-lam)
    for k in range(1, n):
        acc = np.zeros(lam.shape[0])
        for j, a in rate.items():
            if k - j >= 0:
                acc += j * a * pmf[:, k - j]
        pmf[:, k] = acc / k
    return pmf


def team_rates(mean_points: float, fg_share: float) -> tuple[float, float]:
    """(TD rate, FG rate) so that E[points] == mean_points given the safety allowance."""
    base = mean_points - SAFETY_RATE * SAFETY_POINTS
    base = max(base, 0.01)
    lam_td = base * (1.0 - fg_share) / _E_TD_POINTS
    lam_fg = base * fg_share / FG_POINTS
    return lam_td, lam_fg


_GH_COMMON = np.polynomial.hermite_e.hermegauss(7)
_GH_TEAM = np.polynomial.hermite_e.hermegauss(5)


def _nodes(sigma: float, spec) -> tuple[np.ndarray, np.ndarray]:
    z, w = spec
    w = w / w.sum()
    if sigma <= 0:
        return np.array([1.0]), np.array([1.0])
    return np.exp(sigma * z - 0.5 * sigma * sigma), w


@dataclass
class ScoreDistribution:
    """Joint pmf over (home_score, away_score), 0..MAX_POINTS each."""

    joint: np.ndarray = field(repr=False)
    home_mean: float
    away_mean: float

    def __post_init__(self) -> None:
        self.joint = self.joint / self.joint.sum()
        n = self.joint.shape[0]
        h = np.arange(n)[:, None]
        a = np.arange(n)[None, :]
        self._margin = np.bincount((h - a + n - 1).ravel(), weights=self.joint.ravel(), minlength=2 * n - 1)
        self._total = np.bincount((h + a).ravel(), weights=self.joint.ravel(), minlength=2 * n - 1)
        self._n = n

    # ---- marginals -------------------------------------------------------------------------------------
    def home_pmf(self) -> np.ndarray:
        return self.joint.sum(axis=1)

    def away_pmf(self) -> np.ndarray:
        return self.joint.sum(axis=0)

    def margin_prob(self, k: int) -> float:
        """P(home - away == k)."""
        idx = k + self._n - 1
        return float(self._margin[idx]) if 0 <= idx < len(self._margin) else 0.0

    def total_prob(self, k: int) -> float:
        return float(self._total[k]) if 0 <= k < len(self._total) else 0.0

    # ---- exact survival probabilities (strike may be x.5 or an integer; integers expose the push) --------
    def p_margin_gt(self, strike: float) -> float:
        k0 = int(np.floor(strike)) + 1  # smallest integer margin strictly greater than strike
        return float(self._margin[max(0, k0 + self._n - 1) :].sum())

    def p_margin_lt(self, strike: float) -> float:
        k1 = int(np.ceil(strike)) - 1
        hi = k1 + self._n - 1
        return float(self._margin[: max(0, hi + 1)].sum())

    def p_total_gt(self, strike: float) -> float:
        return float(self._total[int(np.floor(strike)) + 1 :].sum())

    def p_total_lt(self, strike: float) -> float:
        return float(self._total[: max(0, int(np.ceil(strike)))].sum())

    def p_team_gt(self, home: bool, strike: float) -> float:
        pmf = self.home_pmf() if home else self.away_pmf()
        return float(pmf[int(np.floor(strike)) + 1 :].sum())

    def p_team_lt(self, home: bool, strike: float) -> float:
        pmf = self.home_pmf() if home else self.away_pmf()
        return float(pmf[: max(0, int(np.ceil(strike)))].sum())


def key_lift_weights(n: int, lift: float) -> np.ndarray:
    """Cell weights: ``lift`` where |home - away| is a key number, else 1. (n x n)."""
    idx = np.arange(n)
    margin = np.abs(idx[:, None] - idx[None, :])
    return np.where(np.isin(margin, KEY_MARGINS), lift, 1.0)


def per_key_lift_weights(n: int, lifts: tuple[tuple[int, float], ...]) -> np.ndarray:
    """Cell weights where |home - away| == m gets its own weight; every other cell is 1. (n x n)."""
    idx = np.arange(n)
    margin = np.abs(idx[:, None] - idx[None, :])
    w = np.ones((n, n))
    for m, lift in lifts:
        if lift <= 0:
            raise ValueError("per-margin lifts must be > 0")
        w[margin == m] = lift
    return w


def _resolve_overtime(joint: np.ndarray) -> np.ndarray:
    out = joint.copy()
    n = joint.shape[0]
    for sc in range(n):
        mass = out[sc, sc]
        if mass <= 0:
            continue
        out[sc, sc] = 0.0
        for w, lo, p in OT_OUTCOMES:
            hi_i, lo_i = min(sc + w, n - 1), min(sc + lo, n - 1)
            out[hi_i, lo_i] += 0.5 * mass * p
            out[lo_i, hi_i] += 0.5 * mass * p
    return out


def build_distribution(
    home_mean: float, away_mean: float, shape: ShapeParams, *, resolve_overtime: bool = True
) -> ScoreDistribution:
    """Joint score pmf: independent compound-Poisson scores conditional on a shared environment factor
    and team-specific volatility factors, both integrated out exactly (Gauss-Hermite)."""
    if not (0.0 < shape.margin_shrink <= 1.0) or shape.key_lift < 1.0:
        raise ValueError("margin_shrink must be in (0, 1] and key_lift >= 1")
    if shape.margin_shrink != 1.0:
        total, diff = home_mean + away_mean, home_mean - away_mean
        home_mean = (total + shape.margin_shrink * diff) / 2.0
        away_mean = (total - shape.margin_shrink * diff) / 2.0
    cm, cw = _nodes(shape.sigma_common, _GH_COMMON)
    tm, tw = _nodes(shape.sigma_team, _GH_TEAM)
    n = MAX_POINTS + 1

    def team_pmf_given_common(mean: float) -> np.ndarray:
        lam_td0, lam_fg0 = team_rates(mean, shape.fg_share)
        # batch over (common node, team node)
        mult = (cm[:, None] * tm[None, :]).ravel()
        pm = _panjer_batch(lam_td0 * mult, lam_fg0 * mult, SAFETY_RATE, n)
        pm = pm.reshape(len(cm), len(tm), n)
        return (pm * tw[None, :, None]).sum(axis=1)  # (common, n)

    ph = team_pmf_given_common(home_mean)
    pa = team_pmf_given_common(away_mean)
    joint = np.zeros((n, n))
    for c in range(len(cm)):
        joint += cw[c] * np.outer(ph[c], pa[c])
    if resolve_overtime:
        joint = _resolve_overtime(joint)
    if shape.key_lift != 1.0:
        joint = joint * key_lift_weights(n, shape.key_lift)
    if shape.key_lifts:
        joint = joint * per_key_lift_weights(n, shape.key_lifts)
    return ScoreDistribution(joint, home_mean, away_mean)


def build_period_distribution(
    home_mean: float,
    away_mean: float,
    shape: ShapeParams,
    *,
    period_share: float | None,
    period_fg_share: float | None = None,
    resolve_overtime: bool = False,
) -> ScoreDistribution:
    """1Q / 1H scoring law. REQUIRES an evidenced ``period_share`` (fraction of full-game expected points
    scored in the period, measured from per-period scoring data). None -> ``MissingPeriodCalibration``."""
    if period_share is None or not (0.0 < period_share < 1.0):
        raise MissingPeriodCalibration(
            "period_share is required and must come from per-period scoring data; "
            "team_games.jsonl has none, so 1Q/1H distributions are not priced"
        )
    s = shape if period_fg_share is None else replace(shape, fg_share=period_fg_share)
    return build_distribution(home_mean * period_share, away_mean * period_share, s, resolve_overtime=resolve_overtime)
