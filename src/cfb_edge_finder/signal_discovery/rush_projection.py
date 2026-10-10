"""Wave 2D Track A: CFB rush projection integration (RETROSPECTIVE_MODEL_INTEGRATION). Pure helpers, no I/O.

Pre-registered in docs/research/CFB_RUSH_PROJECTION_INTEGRATION_PROTOCOL.md (committed alone at PROTOCOL_COMMIT).
The candidate correction is football-only: it reads the exact Wave-2C pregame rush features and is fit against the
CURRENT production model's own margin error, walk-forward by season, without an intercept. Nothing here reads a
market field, and nothing here is imported by production code.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from statistics import NormalDist
from typing import Any

import numpy as np

from cfb_edge_finder.signal_discovery import mechanisms as M

VERSION = "cfb_rush_projection_integration/1.0.0"
PROTOCOL_PATH = "docs/research/CFB_RUSH_PROJECTION_INTEGRATION_PROTOCOL.md"
PROTOCOL_COMMIT = "9d70580d2e7c03290b58692f3ba1671b8fdcc142"
PROTOCOL_SHA256 = "29d4bfc0e5a6b3a7d1329b916da7018870f345eb2aab1dcd8925d8a467098f62"
STARTING_MAIN = "1e40ecbcbbc714a0b2a96aec7a8a79c1e4388352"
LABEL = "RETROSPECTIVE_MODEL_INTEGRATION"

SEED = 20261010
N_BOOT = 2000

#: Wave-2C residualisers (mechanism_report.json "residualisers") and standardisation (protocol section 3).
RUSH_RESIDUALISER = {"a": 0.020161006542193327, "b": 0.8259469722808191, "sd": 1.0375720528416614}
PASS_RESIDUALISER = {"a": -0.01729990928929165, "b": 0.8726116667853815, "sd": 0.9220036335623869}
RUSH_OFF_MEAN = 0.1341409189374074
RUSH_OFF_SD = 1.3944072421206335

CANDIDATES: dict[str, tuple[str, ...]] = {"P1": ("x_rd",), "P2": ("x_rr",), "P3": ("x_rd", "x_ro")}
CONTROLS = ("live", "inseason")

PRIMARY_FOLDS = tuple(range(2018, 2026))
BURN_IN_FOLDS = (2016, 2017)
FIRST_TRAIN_SEASON = 2015
TALENT_SEASONS = (2019, 2021, 2022, 2023, 2024, 2025, 2026)
THRESHOLDS = (-21, -14, -10, -7, -3, 0, 3, 7, 10, 14, 21)
MARGIN_BINS = ((0.0, 7.0), (7.0, 14.0), (14.0, 21.0), (21.0, math.inf))
ERAS = {
    "2014-2019": range(2014, 2020),
    "2020-2022": range(2020, 2023),
    "2023": (2023,),
    "2024": (2024,),
    "2025": (2025,),
}
WEEK_BLOCKS = {"weeks_1_2": (0, 2), "weeks_3_5": (3, 5), "weeks_6_plus": (6, 99)}

#: Gates (protocol section 7).
G2_RMSE_TOL = 0.02
G3_FOLD_TOL = 0.02
G3_MIN_NOT_WORSE = 6
G3_MIN_BETTER = 5
G4_SINGLE_TOL = 0.05
G5_BRIER_TOL = 0.0005
G5_LOGLOSS_TOL = 0.0015
G6_BIAS_TOL = 0.25
G6_MIN_N = 200
G7_TOTAL_TOL = 1e-9
G10_TOL = 0.50
G11_PRACTICAL = -0.10

MARKET_PREFIXES = ("m.", "k.", "dk.", "market", "spread", "moneyline", "kalshi", "close", "open")
OUTCOME_PREFIXES = ("o.", "g.", "actual", "home_points", "away_points")


class IntegrationError(RuntimeError):
    """Raised when an integrity condition of the protocol is violated."""


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def assert_football_only(names: Iterable[str]) -> None:
    """Candidate inputs may never be a market or outcome field (protocol section 10)."""
    for n in names:
        low = n.lower()
        if low.startswith(MARKET_PREFIXES) or low.startswith(OUTCOME_PREFIXES):
            raise IntegrationError(f"non-football field offered to the projection candidate: {n}")


def rush_features(row: dict[str, Any]) -> dict[str, float | None]:
    """The exact Wave-2C constructs (protocol section 3), from pregame fields only."""
    fits = {"rushing": RUSH_RESIDUALISER, "passing": PASS_RESIDUALISER}
    c = M.construct(M.pregame_only(row), fits)
    ro = c.get("rush_off_diff")
    return {
        "x_rd": c.get("frozen_z"),
        "x_rr": c.get("rush_res_z"),
        "x_ro": None if ro is None else (ro - RUSH_OFF_MEAN) / RUSH_OFF_SD,
    }


def fit_beta(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """No-intercept OLS: y ~ x @ beta (x is n x k)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    if len(y) < x.shape[1] + 1:
        raise IntegrationError("too few rows to fit the rush correction")
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    return beta


def delta(beta: Sequence[float], xs: Sequence[float | None], *, fbs_vs_fbs: bool) -> float:
    """Home-margin correction. Exactly 0 for FCS-involved games or when any feature is missing (fail closed)."""
    if not fbs_vs_fbs or any(v is None for v in xs):
        return 0.0
    return float(sum(b * float(v) for b, v in zip(beta, xs, strict=True)))


def shifted_scores(home: float, away: float, d: float) -> tuple[float, float]:
    """Zero-sum shift of the expected scores: margin moves by d, total exactly unchanged."""
    return home + d / 2.0, away - d / 2.0


def margin_sigma(var_h: float, var_a: float, cov: float, s_h: float, s_a: float) -> float:
    return math.sqrt(max(var_h * s_h**2 + var_a * s_a**2 - 2.0 * cov * s_h * s_a, 1e-9))


def p_margin_gt(mu: float, sigma: float, t: float) -> float:
    """Production continuity-corrected Normal P(margin > t) (projections.distribution.prob_greater_than)."""
    return min(1.0, max(0.0, 1.0 - NormalDist(mu, sigma).cdf(t + 0.5)))


def interval_hit(mu: float, sigma: float, actual: float, level: float) -> bool:
    z = NormalDist().inv_cdf(0.5 + level / 2.0)
    return abs(actual - mu) <= z * sigma


def log_loss(p: float, y: int) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def game_metrics(mu: float, sigma: float, actual: float) -> dict[str, float]:
    """Per-game scores; the caller averages them. Pushes (actual == t) are excluded per threshold."""
    err = actual - mu
    out = {"abs": abs(err), "sq": err * err, "err": err}
    if actual != 0:
        p = p_margin_gt(mu, sigma, 0.0)
        y = 1 if actual > 0 else 0
        out["brier"] = (p - y) ** 2
        out["logloss"] = log_loss(p, y)
    sb = [(p_margin_gt(mu, sigma, t) - (1.0 if actual > t else 0.0)) ** 2 for t in THRESHOLDS if actual != t]
    out["spread_brier"] = float(np.mean(sb)) if sb else float("nan")
    for lv in (0.5, 0.8, 0.9):
        out[f"cov{int(lv * 100)}"] = 1.0 if interval_hit(mu, sigma, actual, lv) else 0.0
    return out


def boot_mean_ci(values: np.ndarray, clusters: Sequence[Any], *, seed: int = SEED, n_boot: int = N_BOOT) -> list[float]:
    """Percentile CI of a mean of paired per-game differences, resampling clusters (season-week)."""
    values = np.asarray(values, dtype=float)
    keys = sorted(set(clusters))
    idx = {k: i for i, k in enumerate(keys)}
    cid = np.array([idx[c] for c in clusters])
    sums = np.bincount(cid, weights=values, minlength=len(keys))
    cnts = np.bincount(cid, minlength=len(keys)).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    stats = sums[draws].sum(axis=1) / np.maximum(cnts[draws].sum(axis=1), 1.0)
    return [float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))]


def rmse(sq: Iterable[float]) -> float:
    s = list(sq)
    return math.sqrt(sum(s) / len(s)) if s else float("nan")


def corr(a: Sequence[float], b: Sequence[float]) -> float | None:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def partial_corr(a: Sequence[float], b: Sequence[float], controls: np.ndarray) -> float | None:
    """corr(a, b | controls) via residuals from OLS with intercept."""
    z = np.column_stack([np.ones(len(a)), np.asarray(controls, dtype=float)])
    ra = np.asarray(a, dtype=float) - z @ np.linalg.lstsq(z, np.asarray(a, dtype=float), rcond=None)[0]
    rb = np.asarray(b, dtype=float) - z @ np.linalg.lstsq(z, np.asarray(b, dtype=float), rcond=None)[0]
    return corr(ra, rb)


def incremental_r2(y: Sequence[float], base: np.ndarray, extra: np.ndarray) -> float:
    """R^2(base + extra) - R^2(base), both with intercept."""

    def r2(x: np.ndarray) -> float:
        z = np.column_stack([np.ones(len(y)), x])
        yy = np.asarray(y, dtype=float)
        fit = z @ np.linalg.lstsq(z, yy, rcond=None)[0]
        return 1.0 - float(((yy - fit) ** 2).sum() / ((yy - yy.mean()) ** 2).sum())

    base = np.asarray(base, dtype=float)
    if base.ndim == 1:
        base = base[:, None]
    extra = np.asarray(extra, dtype=float)
    if extra.ndim == 1:
        extra = extra[:, None]
    return r2(np.column_stack([base, extra])) - r2(base)


def week_block(week: int) -> str:
    for name, (lo, hi) in WEEK_BLOCKS.items():
        if lo <= week <= hi:
            return name
    return "weeks_6_plus"
