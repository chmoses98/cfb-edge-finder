"""Walk-forward, time-split evaluation of the discrete score model against the Normal baseline.

Protocol (point-in-time by construction):

1. Games are FBS-vs-FBS only, ordered by kickoff. A game is *rated* only if both teams have
   ``MIN_PRIOR_GAMES`` earlier-DATE games. Ratings for a game use games with a strictly earlier kickoff DATE
   (same-day games are excluded, so no look-ahead through ordering ties).
2. Rated games split in time: the first ``cal_fraction`` are CALIBRATION (shape parameters and baseline
   dispersions are fitted here), the rest are HELD-OUT and are never used for any fitting.
3. Four systems are scored on identical held-out games and identical lines:
       naive_normal      naive shrunk team averages  + Normal (the repo's existing GameDistribution form)
       adjusted_normal   opponent-adjusted pace x efficiency means + Normal   (isolates the mean upgrade)
       naive_discrete    naive means + exact discrete law                     (isolates the shape upgrade)
       adjusted_discrete opponent-adjusted means + exact discrete law         (the proposed model)
   Normals get their dispersion fitted on the calibration split too (3 free parameters each), so the
   comparison is not rigged by an un-tuned baseline.
4. Metrics: log score of the realised margin, total and each team score; Brier and log loss on binary events
   (margin/total/team-total over strikes bracketing the mean, SAME strikes for every system); calibration
   (ECE); key-number mass (predicted vs observed P(|margin| in {3,7,10,14})). A paired bootstrap over
   held-out games gives a 95% interval on the per-game log-score difference.

No market prices exist in ``team_games.jsonl``, so this measures *distribution quality*, not betting
profit. Beating a Normal on log score is necessary evidence for better prices; it is not proof of edge.
"""

from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

from cfb_edge_finder.projections.discrete_scoring import (
    MAX_POINTS,
    GameRow,
    ScoreDistribution,
    ShapeParams,
    build_distribution,
    expected_points,
    fit_ratings,
)

MIN_PRIOR_GAMES = 2
EPS = 1e-4
KEY_MARGINS = (3, 7, 10, 14)
_STD = NormalDist()

FG_GRID = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35)
#: Calibration grids (fit on the calibration split ONLY). kappa=1 / lift=1 is the uncalibrated baseline.
KAPPA_GRID = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4)
LIFT_GRID = tuple(round(1.0 + 0.1 * i, 2) for i in range(16))  # 1.0 .. 2.5
L3_GRID = tuple(round(1.0 + 0.1 * i, 2) for i in range(26))  # 1.0 .. 3.5   (margin of exactly 3)
L7_GRID = tuple(round(1.0 + 0.1 * i, 2) for i in range(16))  # 1.0 .. 2.5   (margin of exactly 7)
LX_GRID = tuple(round(0.6 + 0.1 * i, 2) for i in range(11))  # 0.6 .. 1.6   (margins of 10 and 14)
SIGMA_COMMON_GRID = (0.0, 0.05, 0.12, 0.20)
SIGMA_TEAM_GRID = (0.0, 0.05, 0.10, 0.20)


# ----------------------------------------------------------------------------------------------- loading
def load_games(path: Path) -> list[dict[str, Any]]:
    by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            by_game[r["game_id"]].append(r)
    games = []
    for gid, rs in by_game.items():
        if len(rs) != 2 or any(r["team_division"] != "fbs" for r in rs):
            continue
        if any(r.get("points_for") is None or not (r.get("pbp") or {}).get("scrim_plays") for r in rs):
            continue
        rs = sorted(rs, key=lambda r: {"home": 0, "away": 1}.get(r["site"], 2))
        h, a = rs
        neutral = h["site"] == "neutral"
        games.append(
            {
                "game_id": gid,
                "kickoff": h["kickoff_utc"],
                "date": h["kickoff_utc"][:10],
                "home": h["team_id"],
                "away": a["team_id"],
                "neutral": neutral,
                "home_pts": int(h["points_for"]),
                "away_pts": int(a["points_for"]),
                "home_plays": float(h["pbp"]["scrim_plays"]),
                "away_plays": float(a["pbp"]["scrim_plays"]),
            }
        )
    games.sort(key=lambda g: (g["kickoff"], g["game_id"]))
    return games


def _rows_for(games: list[dict[str, Any]]) -> list[GameRow]:
    out: list[GameRow] = []
    for g in games:
        hs, as_ = ("neutral", "neutral") if g["neutral"] else ("home", "away")
        out.append(GameRow(g["home"], g["away"], hs, g["home_plays"], g["home_pts"]))
        out.append(GameRow(g["away"], g["home"], as_, g["away_plays"], g["away_pts"]))
    return out


def naive_means(prior: list[dict[str, Any]], g: dict[str, Any], pseudo: float = 4.0) -> tuple[float, float] | None:
    pf: dict[str, list[float]] = defaultdict(list)
    pa: dict[str, list[float]] = defaultdict(list)
    for p in prior:
        pf[p["home"]].append(p["home_pts"])
        pa[p["home"]].append(p["away_pts"])
        pf[p["away"]].append(p["away_pts"])
        pa[p["away"]].append(p["home_pts"])
    allp = [x for v in pf.values() for x in v]
    if not allp:
        return None
    lg = float(np.mean(allp))

    def shr(v: list[float]) -> float:
        return (sum(v) + pseudo * lg) / (len(v) + pseudo)

    h, a = g["home"], g["away"]
    return (shr(pf[h]) + shr(pa[a])) / 2.0, (shr(pf[a]) + shr(pa[h])) / 2.0


# ----------------------------------------------------------------------------------------------- normal
def _norm_pmf_vec(mu: float, sd: float, n: int, lo: int = 0) -> np.ndarray:
    ks = np.arange(lo, lo + n)
    cdf = np.array([_STD.cdf((k + 0.5 - mu) / sd) for k in ks])
    cdf_lo = np.array([_STD.cdf((k - 0.5 - mu) / sd) for k in ks])
    return np.maximum(cdf - cdf_lo, 1e-12)


class NormalSystem:
    """Discretised Normal marginals for margin, total, and each team score, dispersion fitted on calibration."""

    def __init__(self, sd_margin: float, sd_total: float, sd_team: float):
        self.sd_margin, self.sd_total, self.sd_team = sd_margin, sd_total, sd_team

    def logp_margin(self, mu_h: float, mu_a: float, m: int) -> float:
        return math.log(
            max(
                _STD.cdf((m + 0.5 - (mu_h - mu_a)) / self.sd_margin)
                - _STD.cdf((m - 0.5 - (mu_h - mu_a)) / self.sd_margin),
                1e-12,
            )
        )

    def logp_total(self, mu_h: float, mu_a: float, t: int) -> float:
        mu = mu_h + mu_a
        return math.log(max(_STD.cdf((t + 0.5 - mu) / self.sd_total) - _STD.cdf((t - 0.5 - mu) / self.sd_total), 1e-12))

    def logp_team(self, mu: float, x: int) -> float:
        return math.log(max(_STD.cdf((x + 0.5 - mu) / self.sd_team) - _STD.cdf((x - 0.5 - mu) / self.sd_team), 1e-12))

    # continuity-corrected survival at half-point strikes
    def p_margin_gt(self, mu_h, mu_a, s):
        return 1 - _STD.cdf((s - (mu_h - mu_a)) / self.sd_margin)

    def p_total_gt(self, mu_h, mu_a, s):
        return 1 - _STD.cdf((s - (mu_h + mu_a)) / self.sd_total)

    def p_team_gt(self, mu, s):
        return 1 - _STD.cdf((s - mu) / self.sd_team)

    def key_mass(self, mu_h, mu_a):
        sd = self.sd_margin
        out = 0.0
        for k in KEY_MARGINS:
            for sign in (1, -1):
                m = sign * k
                out += _STD.cdf((m + 0.5 - (mu_h - mu_a)) / sd) - _STD.cdf((m - 0.5 - (mu_h - mu_a)) / sd)
        return out


def fit_normal(cal: list[dict[str, Any]], means: list[tuple[float, float]]) -> NormalSystem:
    grid = [3.0 + 0.5 * i for i in range(0, 40)]

    def best(resid: list[float]) -> float:
        def ll(sd: float) -> float:
            return sum(math.log(max(_STD.cdf((r + 0.5) / sd) - _STD.cdf((r - 0.5) / sd), 1e-12)) for r in resid)

        return max(grid, key=ll)

    rm = [g["home_pts"] - g["away_pts"] - (mu[0] - mu[1]) for g, mu in zip(cal, means, strict=True)]
    rt = [g["home_pts"] + g["away_pts"] - (mu[0] + mu[1]) for g, mu in zip(cal, means, strict=True)]
    rs = [g["home_pts"] - mu[0] for g, mu in zip(cal, means, strict=True)] + [
        g["away_pts"] - mu[1] for g, mu in zip(cal, means, strict=True)
    ]
    return NormalSystem(best(rm), best(rt), best(rs))


# ----------------------------------------------------------------------------------------------- discrete
def fit_shape(cal: list[dict[str, Any]], means: list[tuple[float, float]]) -> tuple[ShapeParams, float]:
    best: tuple[float, ShapeParams] | None = None
    for fg, sc, st in itertools.product(FG_GRID, SIGMA_COMMON_GRID, SIGMA_TEAM_GRID):
        sp = ShapeParams(fg, sc, st)
        ll = 0.0
        for g, (mh, ma) in zip(cal, means, strict=True):
            d = build_distribution(mh, ma, sp)
            ll += math.log(max(d.joint[min(g["home_pts"], MAX_POINTS), min(g["away_pts"], MAX_POINTS)], 1e-12))
        if best is None or ll > best[0]:
            best = (ll, sp)
    assert best is not None
    return best[1], best[0] / len(cal)


def fit_calibration(
    cal: list[dict[str, Any]], means: list[tuple[float, float]], base: ShapeParams
) -> tuple[ShapeParams, float]:
    """Fit (fg_share, margin_shrink, key_lift) by joint log-likelihood on ``cal`` ONLY.

    The sigmas are carried from ``base`` (the baseline fit chose the grid edge). ``key_lift`` has a closed
    form once the pmf is built: a cell's lifted probability is ``q * w / (1 + (lift - 1) * M)`` where ``M`` is
    the game's key-number mass, so the whole lift grid is evaluated without rebuilding any distribution.
    This function never sees a held-out game; the signature is the guarantee.
    """
    best: tuple[float, ShapeParams] | None = None
    for fg, kappa in itertools.product(FG_GRID, KAPPA_GRID):
        sp = ShapeParams(fg, base.sigma_common, base.sigma_team, kappa, 1.0)
        q, hit, mass = [], [], []
        for g, (mh, ma) in zip(cal, means, strict=True):
            d = build_distribution(mh, ma, sp)
            h, a = min(g["home_pts"], MAX_POINTS), min(g["away_pts"], MAX_POINTS)
            q.append(d.joint[h, a])
            hit.append(abs(h - a) in KEY_MARGINS)
            mass.append(sum(d.margin_prob(sgn * k) for k in KEY_MARGINS for sgn in (1, -1)))
        qa, ha, ma_ = np.asarray(q), np.asarray(hit), np.asarray(mass)
        for lift in LIFT_GRID:
            ll = float(np.sum(np.log(np.maximum(qa * np.where(ha, lift, 1.0) / (1.0 + (lift - 1.0) * ma_), 1e-12))))
            if best is None or ll > best[0]:
                best = (ll, ShapeParams(fg, base.sigma_common, base.sigma_team, kappa, lift))
    assert best is not None
    return best[1], best[0] / len(cal)


def fit_calibration_per_key(
    cal: list[dict[str, Any]], means: list[tuple[float, float]], base: ShapeParams
) -> tuple[ShapeParams, float]:
    """Like ``fit_calibration`` but with separate lifts for margin==3, margin==7 and margins 10/14.

    Motivated by the CALIBRATION-split per-number gaps (the uniform lift fixed the aggregate but over-allocated
    10/14). Still fitted on ``cal`` only; closed-form in the lifts given the per-game group masses."""
    best: tuple[float, ShapeParams] | None = None
    l3, l7, lx = np.meshgrid(np.asarray(L3_GRID), np.asarray(L7_GRID), np.asarray(LX_GRID), indexing="ij")
    l3, l7, lx = l3.ravel(), l7.ravel(), lx.ravel()
    for fg, kappa in itertools.product(FG_GRID, KAPPA_GRID):
        sp = ShapeParams(fg, base.sigma_common, base.sigma_team, kappa)
        q, grp, m3, m7, mx = [], [], [], [], []
        for g, (mh, ma) in zip(cal, means, strict=True):
            d = build_distribution(mh, ma, sp)
            h, a = min(g["home_pts"], MAX_POINTS), min(g["away_pts"], MAX_POINTS)
            q.append(d.joint[h, a])
            mm = abs(h - a)
            grp.append(0 if mm == 3 else 1 if mm == 7 else 2 if mm in (10, 14) else -1)
            m3.append(d.margin_prob(3) + d.margin_prob(-3))
            m7.append(d.margin_prob(7) + d.margin_prob(-7))
            mx.append(sum(d.margin_prob(sg * k) for k in (10, 14) for sg in (1, -1)))
        qa, ga = np.asarray(q), np.asarray(grp)
        m3a, m7a, mxa = np.asarray(m3), np.asarray(m7), np.asarray(mx)
        # normaliser per (game, lift triple)
        z = (
            1.0
            + (l3[:, None] - 1.0) * m3a[None, :]
            + (l7[:, None] - 1.0) * m7a[None, :]
            + (lx[:, None] - 1.0) * mxa[None, :]
        )
        w = np.ones((len(l3), len(qa)))
        w = np.where(ga[None, :] == 0, l3[:, None], w)
        w = np.where(ga[None, :] == 1, l7[:, None], w)
        w = np.where(ga[None, :] == 2, lx[:, None], w)
        ll = np.log(np.maximum(qa[None, :] * w / z, 1e-12)).sum(axis=1)
        i = int(np.argmax(ll))
        if best is None or ll[i] > best[0]:
            lifts = ((3, float(l3[i])), (7, float(l7[i])), (10, float(lx[i])), (14, float(lx[i])))
            best = (float(ll[i]), ShapeParams(fg, base.sigma_common, base.sigma_team, kappa, 1.0, lifts))
    assert best is not None
    return best[1], best[0] / len(cal)


def _key_detail(dists_games: list[tuple[ScoreDistribution, dict[str, Any]]]) -> dict[str, Any]:
    """Predicted (mean over games) vs observed mass at each key number and in total."""
    n = len(dists_games)
    per = {
        str(k): {
            "predicted": float(np.mean([d.margin_prob(k) + d.margin_prob(-k) for d, _ in dists_games])),
            "observed": float(np.mean([abs(g["home_pts"] - g["away_pts"]) == k for _, g in dists_games])),
        }
        for k in KEY_MARGINS
    }
    pred = float(np.mean([sum(d.margin_prob(sg * k) for k in KEY_MARGINS for sg in (1, -1)) for d, _ in dists_games]))
    obs = float(np.mean([abs(g["home_pts"] - g["away_pts"]) in KEY_MARGINS for _, g in dists_games]))
    return {"games": n, "per_key_number": per, "all_key_numbers": {"predicted": pred, "observed": obs}}


def key_number_verdict(
    report_systems: dict[str, Any], paired: dict[str, Any], n_held: int, cand: str = "adjusted_discrete_cal"
) -> dict[str, Any]:
    """PRE-DECLARED rule (written before the held-out numbers were looked at). CALIBRATION_FIXED iff ALL hold:

    A. held-out predicted key-number mass is within 1.96 binomial standard errors of the observed rate;
    B. its absolute gap is smaller than the uncalibrated reference's gap;
    C. paired held-out log-score gain vs the uncalibrated reference is positive on margin and NOT
       significantly negative on total or team score (95% CI upper bound >= 0);
    D. held-out event Brier is no worse than the reference's by more than 0.001.
    """
    ref = "adjusted_discrete"
    k_c = report_systems[cand]["key_number_mass"]
    k_r = report_systems[ref]["key_number_mass"]
    obs = k_c["observed_rate_abs_margin_in_3_7_10_14"]
    se = math.sqrt(obs * (1 - obs) / n_held)
    gap_c, gap_r = abs(k_c["mean_predicted"] - obs), abs(k_r["mean_predicted"] - obs)
    pb = paired[cand]["vs_adjusted_discrete"]
    a = gap_c <= 1.96 * se
    b = gap_c < gap_r
    c = pb["margin"]["mean_diff_per_game"] > 0 and pb["total"]["ci95"][1] >= 0 and pb["team"]["ci95"][1] >= 0
    d = report_systems[cand]["event_brier"] <= report_systems[ref]["event_brier"] + 0.001
    return {
        "verdict": "CALIBRATION_FIXED" if (a and b and c and d) else "NOT_FIXED",
        "candidate": cand,
        "reference": ref,
        "observed_rate_heldout": obs,
        "standard_error_observed": se,
        "predicted_candidate": k_c["mean_predicted"],
        "predicted_reference": k_r["mean_predicted"],
        "criteria": {
            "A_within_1.96_se_of_observed": bool(a),
            "B_gap_smaller_than_reference": bool(b),
            "C_logscore_not_degraded_margin_improved": bool(c),
            "D_event_brier_not_worse": bool(d),
        },
        "promotion": "NOT_PROMOTED: single-season evidence (347 rated 2026 games); promotion is an owner decision",
    }


def _disc_logp(d: ScoreDistribution, g: dict[str, Any]) -> dict[str, float]:
    h, a = g["home_pts"], g["away_pts"]
    return {
        "margin": math.log(max(d.margin_prob(h - a), 1e-12)),
        "total": math.log(max(d.total_prob(h + a), 1e-12)),
        "team": 0.5 * (math.log(max(d.home_pmf()[h], 1e-12)) + math.log(max(d.away_pmf()[a], 1e-12))),
    }


def _strikes(center: float) -> list[float]:
    base = math.floor(center) + 0.5
    return [base + k for k in (-2, -1, 0, 1, 2)]


def _ece(preds: list[float], obs: list[int], bins: int = 5) -> float:
    if not preds:
        return float("nan")
    tot = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(preds) if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if idx:
            tot += len(idx) / len(preds) * abs(np.mean([preds[i] for i in idx]) - np.mean([obs[i] for i in idx]))
    return float(tot)


def _bootstrap(diff: list[float], seed: int = 20261102, reps: int = 2000) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    arr = np.asarray(diff)
    means = arr[rng.integers(0, len(arr), size=(reps, len(arr)))].mean(axis=1)
    return float(arr.mean()), float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


# ----------------------------------------------------------------------------------------------- driver
def run_evaluation(games: list[dict[str, Any]], *, cal_fraction: float = 0.6) -> dict[str, Any]:
    dates = sorted({g["date"] for g in games})
    rated: list[dict[str, Any]] = []
    feats: dict[str, dict[str, tuple[float, float]]] = {}
    for day in dates:
        prior = [g for g in games if g["date"] < day]
        todays = [g for g in games if g["date"] == day]
        if not prior:
            continue
        counts: dict[str, int] = defaultdict(int)
        for p in prior:
            counts[p["home"]] += 1
            counts[p["away"]] += 1
        ratings = fit_ratings(_rows_for(prior))
        for g in todays:
            if counts[g["home"]] < MIN_PRIOR_GAMES or counts[g["away"]] < MIN_PRIOR_GAMES:
                continue
            site_h, site_a = ("neutral", "neutral") if g["neutral"] else ("home", "away")
            adj_h = expected_points(ratings, g["home"], g["away"], site_h)
            adj_a = expected_points(ratings, g["away"], g["home"], site_a)
            nv = naive_means(prior, g)
            if adj_h is None or adj_a is None or nv is None:
                continue
            feats[g["game_id"]] = {"adjusted": (adj_h, adj_a), "naive": nv}
            rated.append(g)
    n_cal = int(len(rated) * cal_fraction)
    cal, held = rated[:n_cal], rated[n_cal:]
    if len(cal) < 30 or len(held) < 30:
        return {"status": "INSUFFICIENT_DATA", "rated_games": len(rated), "calibration": len(cal), "heldout": len(held)}

    systems: dict[str, tuple[str, str]] = {
        "naive_normal": ("naive", "normal"),
        "adjusted_normal": ("adjusted", "normal"),
        "naive_discrete": ("naive", "discrete"),
        "adjusted_discrete": ("adjusted", "discrete"),
        "naive_discrete_cal": ("naive", "discrete"),
        "adjusted_discrete_cal": ("adjusted", "discrete"),
        "naive_discrete_calk": ("naive", "discrete"),
        "adjusted_discrete_calk": ("adjusted", "discrete"),
    }
    fitted: dict[str, Any] = {}
    for mean_kind in ("naive", "adjusted"):
        cal_means = [feats[g["game_id"]][mean_kind] for g in cal]
        fitted[f"{mean_kind}_normal"] = fit_normal(cal, cal_means)
        sp, ll = fit_shape(cal, cal_means)
        fitted[f"{mean_kind}_discrete"] = sp
        fitted[f"{mean_kind}_discrete_cal_loglik_per_game"] = ll
        cal_sp, cal_ll = fit_calibration(cal, cal_means, sp)
        fitted[f"{mean_kind}_discrete_cal"] = cal_sp
        fitted[f"{mean_kind}_discrete_cal_cal_loglik_per_game"] = cal_ll
        calk_sp, calk_ll = fit_calibration_per_key(cal, cal_means, sp)
        fitted[f"{mean_kind}_discrete_calk"] = calk_sp
        fitted[f"{mean_kind}_discrete_calk_cal_loglik_per_game"] = calk_ll

    per_game: dict[str, dict[str, list[float]]] = {s: defaultdict(list) for s in systems}
    events: dict[str, tuple[list[float], list[int]]] = {s: ([], []) for s in systems}
    key_pred: dict[str, list[float]] = {s: [] for s in systems}
    key_obs: list[int] = []
    for g in held:
        h, a = g["home_pts"], g["away_pts"]
        key_obs.append(int(abs(h - a) in KEY_MARGINS))
        ref = feats[g["game_id"]]["naive"]  # identical strikes for every system
        mstrikes, tstrikes = _strikes(ref[0] - ref[1]), _strikes(ref[0] + ref[1])
        hstrikes, astrikes = _strikes(ref[0]), _strikes(ref[1])
        for name, (mean_kind, kind) in systems.items():
            mh, ma = feats[g["game_id"]][mean_kind]
            ev_p, ev_o = events[name]
            if kind == "normal":
                ns: NormalSystem = fitted[name]
                per_game[name]["margin"].append(ns.logp_margin(mh, ma, h - a))
                per_game[name]["total"].append(ns.logp_total(mh, ma, h + a))
                per_game[name]["team"].append(0.5 * (ns.logp_team(mh, h) + ns.logp_team(ma, a)))
                for s in mstrikes:
                    ev_p.append(ns.p_margin_gt(mh, ma, s))
                    ev_o.append(int(h - a > s))
                for s in tstrikes:
                    ev_p.append(ns.p_total_gt(mh, ma, s))
                    ev_o.append(int(h + a > s))
                for s in hstrikes:
                    ev_p.append(ns.p_team_gt(mh, s))
                    ev_o.append(int(h > s))
                for s in astrikes:
                    ev_p.append(ns.p_team_gt(ma, s))
                    ev_o.append(int(a > s))
                key_pred[name].append(ns.key_mass(mh, ma))
            else:
                d = build_distribution(mh, ma, fitted[name])
                for k, v in _disc_logp(d, g).items():
                    per_game[name][k].append(v)
                for s in mstrikes:
                    ev_p.append(d.p_margin_gt(s))
                    ev_o.append(int(h - a > s))
                for s in tstrikes:
                    ev_p.append(d.p_total_gt(s))
                    ev_o.append(int(h + a > s))
                for s in hstrikes:
                    ev_p.append(d.p_team_gt(True, s))
                    ev_o.append(int(h > s))
                for s in astrikes:
                    ev_p.append(d.p_team_gt(False, s))
                    ev_o.append(int(a > s))
                key_pred[name].append(sum(d.margin_prob(sg * k) for k in KEY_MARGINS for sg in (1, -1)))

    key_detail: dict[str, Any] = {}
    for name, (mean_kind, kind) in systems.items():
        if kind != "discrete":
            continue
        key_detail[name] = {}
        for split_name, split in (("calibration", cal), ("heldout", held)):
            pairs = [
                (build_distribution(*feats[g["game_id"]][mean_kind], fitted[name]), g) for g in split
            ]
            key_detail[name][split_name] = _key_detail(pairs)

    report: dict[str, Any] = {
        "status": "OK",
        "protocol": {
            "universe": "FBS-vs-FBS games in data/football/2026/team_games.jsonl",
            "games_total": len(games),
            "rated_games": len(rated),
            "calibration_games": len(cal),
            "heldout_games": len(held),
            "calibration_window": [cal[0]["date"], cal[-1]["date"]],
            "heldout_window": [held[0]["date"], held[-1]["date"]],
            "ratings": "refit per kickoff DATE using strictly earlier dates only",
            "min_prior_games_per_team": MIN_PRIOR_GAMES,
            "shared_strikes": "5 half-point strikes bracketing the NAIVE mean, identical for all systems",
        },
        "fitted": {k: (v.__dict__ if hasattr(v, "__dict__") else v) for k, v in fitted.items()},
        "systems": {},
        "key_number_detail": key_detail,
        "paired_bootstrap_logscore_vs_naive_normal": {},
        "unsupported": {
            "epa": "no play-level EPA in repo data (only drive summaries / success counts)",
            "special_teams": "no special-teams data",
            "injury_effect_sizes": "no calibrated injury magnitudes; injuries are not used to move means",
            "period_1q_1h": "no per-period scoring in team_games.jsonl; 1Q/1H distributions are refused, not guessed",
            "market_prices": "no historical market lines in repo data; measures distribution quality, not profit",
        },
    }
    key_rate = float(np.mean(key_obs))
    for name in systems:
        ev_p, ev_o = events[name]
        p = np.clip(np.asarray(ev_p), EPS, 1 - EPS)
        o = np.asarray(ev_o)
        report["systems"][name] = {
            "mean_logscore": {k: float(np.mean(v)) for k, v in per_game[name].items()},
            "event_brier": float(np.mean((p - o) ** 2)),
            "event_logloss": float(-np.mean(o * np.log(p) + (1 - o) * np.log(1 - p))),
            "event_ece": _ece(list(p), list(o)),
            "n_events": int(len(o)),
            "key_number_mass": {
                "observed_rate_abs_margin_in_3_7_10_14": key_rate,
                "mean_predicted": float(np.mean(key_pred[name])),
            },
        }
    for name in systems:
        if name == "naive_normal":
            continue
        report["paired_bootstrap_logscore_vs_naive_normal"][name] = {}
        for k in ("margin", "total", "team"):
            diff = [x - y for x, y in zip(per_game[name][k], per_game["naive_normal"][k], strict=True)]
            mean, lo, hi = _bootstrap(diff)
            report["paired_bootstrap_logscore_vs_naive_normal"][name][k] = {
                "mean_diff_per_game": mean,
                "ci95": [lo, hi],
                "ci_excludes_zero": bool(lo > 0 or hi < 0),
            }
    # Paired comparison of the CALIBRATED systems against their UNCALIBRATED counterparts (the calibration effect).
    report["paired_bootstrap_logscore_calibration_effect"] = {}
    for base_name in ("naive_discrete", "adjusted_discrete"):
        for suffix in ("cal", "calk"):
            cal_name = f"{base_name}_{suffix}"
            report["paired_bootstrap_logscore_calibration_effect"][cal_name] = {f"vs_{base_name}": {}}
            for k in ("margin", "total", "team"):
                diff = [x - y for x, y in zip(per_game[cal_name][k], per_game[base_name][k], strict=True)]
                mean, lo, hi = _bootstrap(diff)
                report["paired_bootstrap_logscore_calibration_effect"][cal_name][f"vs_{base_name}"][k] = {
                    "mean_diff_per_game": mean,
                    "ci95": [lo, hi],
                    "ci_excludes_zero": bool(lo > 0 or hi < 0),
                }
    paired = report["paired_bootstrap_logscore_calibration_effect"]
    report["key_number_calibration"] = {
        "uniform_lift_v1": key_number_verdict(report["systems"], paired, len(held), "adjusted_discrete_cal"),
        "per_key_lift_v2": key_number_verdict(report["systems"], paired, len(held), "adjusted_discrete_calk"),
        "disclosure": (
            "v2 was designed AFTER v1's held-out result was seen, motivated by the calibration-split per-number gaps "
            "(margin 3 under-predicted ~2.5x; 10/14 over-allocated by the uniform lift). Both are reported under the "
            "same pre-declared rule; v2's held-out numbers are therefore not fully clean evidence."
        ),
    }
    return report
