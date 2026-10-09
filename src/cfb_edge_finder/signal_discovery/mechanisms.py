"""Football Signal Discovery Lab, Wave 2C (CFB): signal-mechanism study. RESEARCH ONLY. PURE.

docs/research/CFB_SIGNAL_MECHANISM_PROTOCOL.md (pre-registered at 202c9e91). Explanatory diagnostics of why the Wave-1
CFB signals behave as they do against the market. Nothing here is a rule, filter, threshold, stake, price or
recommendation. The frozen PROS-001 constants are imported from `wave2`, never restated; CONTROL is the production
claim already on every feature row.

Wall: `pregame_*` / `construct_*` functions take feature rows only (no `o.*`, no `m.*`); market and outcome fields
enter only the analysis functions.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import numpy as np

from cfb_edge_finder.signal_discovery import wave2 as W

VERSION = "cfb_signal_mechanisms/1.0.0"
PROTOCOL_COMMIT = "202c9e91cff9b7b2d3be9efe03e0c512d07afc5e"
PROTOCOL_SHA256 = "0106a1fb363430c14121d208d4a7cbf0ec932779a2f52b427281ce34e4822c90"
SEED = 20261011
N_BOOT = 2000
#: integrity anchor (protocol section 1): the frozen PROS-001 rule over 2014-2025
ANCHOR = {"n": 2713, "mean": 1.4003870254331, "w": 1408, "l": 1260, "p": 45}

OUTCOME_PREFIXES = ("o.", "g.")
MARKET_PREFIXES = ("m.",)

EMPTY_RESULTS = frozenset(
    {
        "PUNT",
        "INT",
        "FUMBLE",
        "INT TD",
        "FUMBLE RETURN TD",
        "FUMBLE TD",
        "DOWNS",
        "DOWNS TD",
        "MISSED FG",
        "MISSED FG TD",
        "BLOCKED PUNT",
        "BLOCKED FG",
        "PUNT TD",
        "PUNT RETURN TD",
        "SF",
        "END OF HALF",
    }
)
TURNOVER_RESULTS = frozenset({"INT", "FUMBLE", "INT TD", "FUMBLE RETURN TD", "FUMBLE TD"})


class MechanismIntegrityError(RuntimeError):
    """A frozen invariant does not hold, or an output would touch the prospective store."""


def assert_not_prospective(path: str) -> None:
    text = str(path).replace("\\", "/")
    if "research-signals" in text or "/wave2/" in text.replace("signal_discovery_wave2/", "") or "ledger" in text:
        raise MechanismIntegrityError(f"mechanism outputs may not be written to a prospective store: {text}")
    if "signal_discovery_wave2" in text and "signal_mechanisms" not in text:
        raise MechanismIntegrityError(f"Wave-2 / 2A artifacts are read-only: {text}")


def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


# --------------------------------------------------------------------------- frozen rule (imported)


def frozen_z(row: dict[str, Any]) -> float | None:
    x = _f(row.get(W.DSC001_FEATURE))
    return None if x is None else (x - W.DSC001_MEAN) / W.DSC001_SD


def frozen_side(row: dict[str, Any]) -> str | None:
    z = frozen_z(row)
    if z is None or abs(z) < W.Z_THRESHOLD:
        return None
    return "home" if z > 0 else "away"


def sign(side: str) -> float:
    return 1.0 if side == "home" else -1.0


# --------------------------------------------------------------------------- constructs (pregame only)


def pregame_only(row: dict[str, Any]) -> dict[str, Any]:
    """The row without outcome or market fields (what membership / constructs may see)."""
    return {k: v for k, v in row.items() if not k.startswith(OUTCOME_PREFIXES + MARKET_PREFIXES)}


def fit_residualisers(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """OLS slope of net.<dim> on net.sustained_efficiency and the SD of the residual (feature-on-feature)."""
    out = {}
    rows = list(rows)
    for dim in ("rushing", "passing"):
        pts = [(_f(r.get("net.sustained_efficiency")), _f(r.get(f"net.{dim}"))) for r in rows]
        pts = [(x, y) for x, y in pts if x is not None and y is not None]
        x = np.array([p[0] for p in pts])
        y = np.array([p[1] for p in pts])
        b = float(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1))
        a = float(y.mean() - b * x.mean())
        res = y - (a + b * x)
        out[dim] = {"a": a, "b": b, "sd": float(res.std(ddof=1)), "n": len(pts)}
    return out


def construct(row: dict[str, Any], fits: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Protocol section 3 constructs from pregame fields only."""
    p = pregame_only(row)
    eff = _f(p.get("net.sustained_efficiency"))
    out: dict[str, Any] = {"frozen_z": frozen_z(p), "frs": frozen_side(p)}
    for dim, key in (("rushing", "rush_res_z"), ("passing", "pass_res_z")):
        v = _f(p.get(f"net.{dim}"))
        f = fits[dim]
        out[key] = None if v is None or eff is None else (v - (f["a"] + f["b"] * eff)) / f["sd"]
    out["rrs"] = (
        None
        if out["rush_res_z"] is None or abs(out["rush_res_z"]) < 1
        else ("home" if out["rush_res_z"] > 0 else "away")
    )
    out["prs"] = (
        None
        if out["pass_res_z"] is None or abs(out["pass_res_z"]) < 1
        else ("home" if out["pass_res_z"] > 0 else "away")
    )
    for m, k in (("rush_success_rate", "rush"), ("pass_success_rate", "pass")):
        ho, ao = _f(p.get(f"home_off_q.{m}")), _f(p.get(f"away_off_q.{m}"))
        hd, ad = _f(p.get(f"home_def_q.{m}")), _f(p.get(f"away_def_q.{m}"))
        out[f"{k}_off_diff"] = None if None in (ho, ao) else ho - ao
        out[f"{k}_def_diff"] = None if None in (hd, ad) else hd - ad
        # matchup interaction: own offense x opponent defensive weakness, home minus away
        out[f"{k}_matchup"] = None if None in (ho, ao, hd, ad) else ho * (-ad) - ao * (-hd)
    cs = p.get("control_side")
    out["control_mag"] = None if cs is None or eff is None else sign(cs) * eff
    return out


# --------------------------------------------------------------------------- game-level outcomes (post-game)


def drive_outcomes(drives: Iterable[dict[str, Any]]) -> dict[str, float]:
    """One offense's drives in one game -> counts (protocol section 3)."""
    c = defaultdict(float)
    for d in drives:
        res = str(d.get("driveResult") or "").upper()
        plays = float(d.get("plays") or 0)
        c["drives"] += 1
        c["points"] += 7.0 if res == "TD" else (3.0 if res == "FG" else 0.0)
        c["empty"] += 1 if res in EMPTY_RESULTS else 0
        c["three_out"] += 1 if (res == "PUNT" and plays <= 3) else 0
        c["turnover_drives"] += 1 if res in TURNOVER_RESULTS else 0
        reached = min(float(d.get("startYardsToGoal") or 100), float(d.get("endYardsToGoal") or 100))
        rz = (0 <= reached <= 20) or res == "TD"
        c["rz_trips"] += 1 if rz else 0
        c["rz_td"] += 1 if (rz and res == "TD") else 0
        c["long_drives"] += 1 if plays >= 10 else 0
        c["drive_plays"] += plays
    return dict(c)


def quarter_margins(home_ls: Sequence[Any] | None, away_ls: Sequence[Any] | None) -> dict[str, float] | None:
    """Home-minus-away quarter margins from CFBD line scores; None unless four regulation quarters exist."""
    if not home_ls or not away_ls or len(home_ls) < 4 or len(away_ls) < 4:
        return None
    if any(x is None for x in list(home_ls[:4]) + list(away_ls[:4])):
        return None
    q = [float(home_ls[i]) - float(away_ls[i]) for i in range(4)]
    ot = float(sum(home_ls[4:])) - float(sum(away_ls[4:])) if len(home_ls) > 4 else 0.0
    return {
        "q1": q[0],
        "q2": q[1],
        "q3": q[2],
        "q4": q[3],
        "h1": q[0] + q[1],
        "h2": q[2] + q[3],
        "thru_q3": q[0] + q[1] + q[2],
        "ot": ot,
    }


# --------------------------------------------------------------------------- statistics


def ols(y: Sequence[float], X: Sequence[Sequence[float]]) -> np.ndarray | None:
    Y, M = np.asarray(y, float), np.asarray(X, float)
    if len(Y) == 0 or M.ndim != 2 or len(Y) < M.shape[1] + 3:
        return None
    M1 = np.column_stack([np.ones(len(Y)), M])
    beta, *_ = np.linalg.lstsq(M1, Y, rcond=None)
    return beta


def r2(y: Sequence[float], X: Sequence[Sequence[float]]) -> float | None:
    Y, M = np.asarray(y, float), np.asarray(X, float)
    beta = ols(Y, M)
    if beta is None:
        return None
    pred = np.column_stack([np.ones(len(Y)), M]) @ beta
    return float(1 - ((Y - pred) ** 2).sum() / ((Y - Y.mean()) ** 2).sum())


def season_boot(
    rows: list[dict[str, Any]],
    stat: Callable[[list[dict[str, Any]]], float | None],
    *,
    cluster: str = "season",
    n_boot: int = N_BOOT,
    seed: int = SEED,
) -> dict[str, Any]:
    """Cluster bootstrap (whole seasons by default); percentile 95 % CI, two-sided p for 0, per-cluster signs."""
    est = stat(rows)
    by: dict[Any, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by[r.get(cluster)].append(i)
    keys = sorted(by, key=str)
    out: dict[str, Any] = {"est": est, "n": len(rows), "clusters": len(keys)}
    if est is None or len(keys) < 3:
        return {**out, "ci95": None, "p": None}
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(keys), len(keys))
        sample = [rows[i] for k in pick for i in by[keys[k]]]
        v = stat(sample)
        if v is not None and np.isfinite(v):
            vals.append(v)
    a = np.asarray(vals)
    p = float(min(1.0, 2 * min((a <= 0).mean(), (a >= 0).mean()))) if len(a) else None
    out.update({"ci95": [float(np.quantile(a, 0.025)), float(np.quantile(a, 0.975))] if len(a) else None, "p": p})
    if cluster == "season":
        signs = []
        for k in keys:
            v = stat([rows[i] for i in by[k]])
            if v is not None:
                signs.append(1 if v > 0 else (-1 if v < 0 else 0))
        out["seasons_positive"] = sum(1 for s in signs if s > 0)
        out["seasons_negative"] = sum(1 for s in signs if s < 0)
    return out


def coef(rows: list[dict[str, Any]], y: str, xs: Sequence[str], which: str) -> float | None:
    rs = [r for r in rows if r.get(y) is not None and all(r.get(x) is not None for x in xs)]
    beta = ols([r[y] for r in rs], [[r[x] for x in xs] for r in rs])
    return None if beta is None else float(beta[1 + list(xs).index(which)])


def bh(pvals: dict[str, float | None]) -> dict[str, float | None]:
    items = sorted(((k, p) for k, p in pvals.items() if p is not None), key=lambda x: x[1])
    m = len(items)
    q: dict[str, float | None] = {k: None for k in pvals}
    prev = 1.0
    for rank in range(m, 0, -1):
        k, p = items[rank - 1]
        prev = min(prev, p * m / rank)
        q[k] = prev
    return q


def describe(values: Iterable[float]) -> dict[str, Any]:
    v = np.asarray([x for x in values if x is not None], float)
    if len(v) < 3:
        return {"n": int(len(v))}
    m, s = float(v.mean()), float(v.std(ddof=1))
    z = (v - m) / s if s > 0 else v * 0
    return {
        "n": int(len(v)),
        "mean": m,
        "median": float(np.median(v)),
        "sd": s,
        "p10": float(np.quantile(v, 0.10)),
        "p25": float(np.quantile(v, 0.25)),
        "p75": float(np.quantile(v, 0.75)),
        "p90": float(np.quantile(v, 0.90)),
        "skew": float((z**3).mean()),
        "excess_kurtosis": float((z**4).mean() - 3),
    }


def ats_record(resid: Iterable[float]) -> dict[str, Any]:
    v = [x for x in resid if x is not None]
    w, lost = sum(1 for x in v if x > 0), sum(1 for x in v if x < 0)
    return {
        "w": w,
        "l": lost,
        "p": len(v) - w - lost,
        "cover": w / (w + lost) if w + lost else None,
        "mean": float(np.mean(v)) if v else None,
        "n": len(v),
    }


def bins(
    rows: list[dict[str, Any]],
    key: Callable[[dict[str, Any]], Any],
    fields: dict[str, Callable[[dict[str, Any]], float | None]],
) -> dict[str, Any]:
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        k = key(r)
        if k is not None:
            groups[str(k)].append(r)
    out = {}
    for k in sorted(groups):
        g = groups[k]
        cell: dict[str, Any] = {"n": len(g)}
        for name, f in fields.items():
            vals = [f(r) for r in g]
            vals = [v for v in vals if v is not None]
            cell[name] = float(np.mean(vals)) if vals else None
        out[k] = cell
    return out


def quantile_edges(values: Iterable[float], q: int) -> list[float]:
    v = np.asarray([x for x in values if x is not None], float)
    return [float(x) for x in np.quantile(v, np.linspace(0, 1, q + 1))]


def bucket(v: float | None, edges: list[float]) -> int | None:
    if v is None:
        return None
    for i in range(len(edges) - 1):
        if v <= edges[i + 1] or i == len(edges) - 2:
            return i
    return None


def auc(y: Sequence[int], score: Sequence[float]) -> float | None:
    y, s = np.asarray(y), np.asarray(score, float)
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    # average ranks for ties
    allv = np.concatenate([pos, neg])
    for val in np.unique(allv):
        idx = np.where(allv == val)[0]
        if len(idx) > 1:
            ranks[idx] = ranks[idx].mean()
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def logistic(y: Sequence[int], X: Sequence[Sequence[float]], iters: int = 60) -> np.ndarray | None:
    Y, M = np.asarray(y, float), np.column_stack([np.ones(len(y)), np.asarray(X, float)])
    if len(Y) < M.shape[1] + 5:
        return None
    b = np.zeros(M.shape[1])
    for _ in range(iters):
        mu = 1 / (1 + np.exp(-np.clip(M @ b, -30, 30)))
        w = mu * (1 - mu)
        H = M.T @ (M * w[:, None]) + 1e-6 * np.eye(M.shape[1])
        step = np.linalg.solve(H, M.T @ (Y - mu))
        b = b + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return b


def spearman_brown(r: float | None) -> float | None:
    return None if r is None else 2 * r / (1 + r)


def corr(a: Sequence[float], b: Sequence[float]) -> float | None:
    x, y = np.asarray(a, float), np.asarray(b, float)
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def partial_corr(rows: list[dict[str, Any]], a: str, b: str, controls: Sequence[str]) -> float | None:
    rs = [r for r in rows if all(r.get(k) is not None for k in (a, b, *controls))]
    if len(rs) < 10:
        return None
    C = np.column_stack([np.ones(len(rs))] + [[r[c] for r in rs] for c in controls])

    def resid(k):
        y = np.array([r[k] for r in rs], float)
        beta, *_ = np.linalg.lstsq(C, y, rcond=None)
        return y - C @ beta

    return corr(resid(a), resid(b))


def counts(values: Iterable[Any]) -> dict[str, int]:
    return {str(k): v for k, v in sorted(Counter(values).items(), key=lambda kv: str(kv[0]))}


def season_boot_ols(
    rows: list[dict[str, Any]],
    targets: Sequence[str],
    xs: Sequence[str],
    stat: Callable[[dict[str, np.ndarray]], float | None],
    *,
    n_boot: int = N_BOOT,
    seed: int = SEED,
    cluster: str = "season",
) -> dict[str, Any]:
    """`season_boot` for statistics of OLS coefficients, from per-cluster sufficient statistics (X'X, X'y).

    The same estimator as refitting on the resampled rows (OLS is additive in X'X / X'y). `stat` receives
    {target: beta}, beta[0] the intercept and beta[1 + i] the coefficient of xs[i]. Rows must be complete."""
    rs = [r for r in rows if all(r.get(k) is not None for k in (*targets, *xs))]
    by: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in rs:
        by[r.get(cluster)].append(r)
    keys = sorted(by, key=str)
    k = len(xs) + 1
    XtX = np.zeros((len(keys), k, k))
    Xty = np.zeros((len(keys), len(targets), k))
    for i, key in enumerate(keys):
        g = by[key]
        X = np.column_stack([np.ones(len(g))] + [[float(r[x]) for r in g] for x in xs])
        XtX[i] = X.T @ X
        for j, t in enumerate(targets):
            Xty[i, j] = X.T @ np.array([float(r[t]) for r in g])

    def solve(idx):
        try:
            betas = np.linalg.solve(XtX[idx].sum(axis=0), Xty[idx].sum(axis=0).T).T
        except np.linalg.LinAlgError:
            return None
        return stat({t: betas[j] for j, t in enumerate(targets)})

    est = solve(np.arange(len(keys)))
    out: dict[str, Any] = {"est": est, "n": len(rs), "clusters": len(keys)}
    if est is None or len(keys) < 3:
        return {**out, "ci95": None, "p": None}
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        v = solve(rng.integers(0, len(keys), len(keys)))
        if v is not None and np.isfinite(v):
            vals.append(v)
    a = np.asarray(vals)
    out["ci95"] = [float(np.quantile(a, 0.025)), float(np.quantile(a, 0.975))]
    out["p"] = float(min(1.0, 2 * min((a <= 0).mean(), (a >= 0).mean())))
    if cluster == "season":
        signs = [solve(np.array([i])) for i in range(len(keys))]
        out["seasons_positive"] = sum(1 for s in signs if s is not None and s > 0)
        out["seasons_negative"] = sum(1 for s in signs if s is not None and s < 0)
    return out
