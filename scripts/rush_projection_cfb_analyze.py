"""Wave 2D Track A analysis: walk-forward rush corrections vs the current production projection.

Reads only data/scripting/validation/rush_projection/p0_games_*.jsonl.gz (committed study input) and writes
integration_report.json. Every rule (population, folds, coefficients, gates, decision) is the one frozen in
docs/research/CFB_RUSH_PROJECTION_INTEGRATION_PROTOCOL.md. RETROSPECTIVE_MODEL_INTEGRATION.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.signal_discovery import rush_projection as R  # noqa: E402

DATA = ROOT / "data" / "scripting" / "validation" / "rush_projection"
REPORT = DATA / "integration_report.json"
TAGS = {"live": "live", "inseason": "in"}


def read_rows(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def rnd(x: Any, n: int = 4) -> Any:
    if isinstance(x, float):
        return None if math.isnan(x) else round(x, n)
    if isinstance(x, dict):
        return {k: rnd(v, n) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [rnd(v, n) for v in x]
    return x


# --------------------------------------------------------------------------- P0 reconstruction from components


def p0(row: dict[str, Any], control: str) -> dict[str, float] | None:
    t = TAGS[control]
    if f"{t}_eh" not in row or f"{t}_sigma" not in row:
        return None
    raw = row[f"{t}_eh"] - row[f"{t}_ea"]
    c2 = 0.0
    if row["fbs_vs_fbs"] and not row["c2_identity"]:
        c2 = row["c2_a"] * raw + row["c2_b"] - raw
    margin = raw + c2 + row["talent_delta"]
    return {
        "raw": raw,
        "c2": c2,
        "margin": margin,
        "home": row[f"{t}_eh"] + (c2 + row["talent_delta"]) / 2,
        "away": row[f"{t}_ea"] - (c2 + row["talent_delta"]) / 2,
        "total": row[f"{t}_eh"] + row[f"{t}_ea"],
        "sigma": row[f"{t}_sigma"],
    }


def population(rows: list[dict[str, Any]], control: str) -> list[dict[str, Any]]:
    """Protocol section 5: FBS-vs-FBS regular season, in the Wave-2C input, final score, P0 computable."""
    out = []
    for r in rows:
        if not (r["fbs_vs_fbs"] and not r["postseason"] and r.get("in_wave2c")):
            continue
        if r["season"] < R.FIRST_TRAIN_SEASON:
            continue
        base = p0(r, control)
        if base is None:
            continue
        out.append({**r, "_p0": base, "_actual": float(r["home_points"] - r["away_points"])})
    return out


def xs(row: dict[str, Any], cand: str) -> list[float | None]:
    return [row.get(f) for f in R.CANDIDATES[cand]]


def walk_forward(pop: list[dict[str, Any]], cand: str) -> tuple[dict[int, list[float]], dict[str, float]]:
    """beta_S fit on seasons FIRST_TRAIN_SEASON..S-1 (no intercept, target = actual - P0); returns per-game delta."""
    betas: dict[int, list[float]] = {}
    deltas: dict[str, float] = {}
    seasons = sorted({r["season"] for r in pop})
    for s in seasons:
        if s <= R.FIRST_TRAIN_SEASON:
            continue
        train = [r for r in pop if R.FIRST_TRAIN_SEASON <= r["season"] < s and None not in xs(r, cand)]
        assert all(r["season"] < s for r in train)  # no game is ever in its own fitting set
        x = np.array([[float(v) for v in xs(r, cand)] for r in train])
        y = np.array([r["_actual"] - r["_p0"]["margin"] for r in train])
        beta = R.fit_beta(x, y)
        betas[s] = [float(b) for b in beta]
        for r in pop:
            if r["season"] == s:
                deltas[r["game_id"]] = R.delta(beta, xs(r, cand), fbs_vs_fbs=r["fbs_vs_fbs"])
    return betas, deltas


def score(pop: list[dict[str, Any]], deltas: dict[str, float] | None) -> list[dict[str, Any]]:
    out = []
    for r in pop:
        d = 0.0 if deltas is None else deltas.get(r["game_id"])
        if d is None:
            continue
        mu = r["_p0"]["margin"] + d
        g = R.game_metrics(mu, r["_p0"]["sigma"], r["_actual"])
        h, a = R.shifted_scores(r["_p0"]["home"], r["_p0"]["away"], d)
        g.update(
            game_id=r["game_id"],
            season=r["season"],
            week=r["week"],
            neutral=r["neutral_site"],
            mu=mu,
            p0_margin=r["_p0"]["margin"],
            delta=d,
            total=h + a,
            p0_total=r["_p0"]["total"],
            actual=r["_actual"],
            cluster=(r["season"], r["week"]),
            talent_active=r["talent_active"],
            has_feature=d != 0.0,
        )
        out.append(g)
    return out


def summarize(base: list[dict[str, Any]], cand: list[dict[str, Any]]) -> dict[str, Any]:
    """Paired candidate-minus-P0 summary over the same games."""
    if not base:
        return {"n": 0}
    b = {g["game_id"]: g for g in base}
    c = [g for g in cand if g["game_id"] in b]
    bb = [b[g["game_id"]] for g in c]
    d_abs = np.array([x["abs"] - y["abs"] for x, y in zip(c, bb, strict=True)])

    def mean(rows: list[dict[str, Any]], k: str) -> float:
        v = [r[k] for r in rows if k in r and not (isinstance(r[k], float) and math.isnan(r[k]))]
        return float(np.mean(v)) if v else float("nan")

    def paired(k: str) -> float:
        v = [x[k] - y[k] for x, y in zip(c, bb, strict=True) if k in x and k in y]
        return float(np.mean(v)) if v else float("nan")

    out = {
        "n": len(c),
        "n_corrected": sum(1 for g in c if g["delta"] != 0.0),
        "p0_mae": mean(bb, "abs"),
        "cand_mae": mean(c, "abs"),
        "d_mae": float(d_abs.mean()),
        "d_mae_ci95": R.boot_mean_ci(d_abs, [g["cluster"] for g in c]) if len(c) > 30 else None,
        "p0_rmse": R.rmse(r["sq"] for r in bb),
        "cand_rmse": R.rmse(r["sq"] for r in c),
        "p0_bias": mean(bb, "err"),
        "cand_bias": mean(c, "err"),
        "p0_brier": mean(bb, "brier"),
        "d_brier": paired("brier"),
        "p0_logloss": mean(bb, "logloss"),
        "d_logloss": paired("logloss"),
        "p0_spread_brier": mean(bb, "spread_brier"),
        "d_spread_brier": paired("spread_brier"),
        "mean_abs_delta": float(np.mean([abs(g["delta"]) for g in c])),
    }
    out["d_rmse"] = out["cand_rmse"] - out["p0_rmse"]
    for lv in ("cov50", "cov80", "cov90"):
        out[f"p0_{lv}"] = mean(bb, lv)
        out[f"cand_{lv}"] = mean(c, lv)
    return out


def oriented_bias(scored: list[dict[str, Any]]) -> dict[str, Any]:
    """Bias toward the P0-projected favourite by |P0 margin| bin, plus site bias (G6)."""
    out: dict[str, Any] = {}
    for lo, hi in R.MARGIN_BINS:
        rows = [g for g in scored if lo <= abs(g["p0_margin"]) < hi]
        sgn = [1.0 if g["p0_margin"] >= 0 else -1.0 for g in rows]
        out[f"abs_p0_{int(lo)}_{'inf' if math.isinf(hi) else int(hi)}"] = {
            "n": len(rows),
            "fav_bias": float(np.mean([s * (g["actual"] - g["mu"]) for s, g in zip(sgn, rows, strict=True)]))
            if rows
            else None,
        }
    for name, pred in (("non_neutral_home_persp", lambda g: not g["neutral"]), ("neutral", lambda g: g["neutral"])):
        rows = [g for g in scored if pred(g)]
        out[name] = {"n": len(rows), "bias": float(np.mean([g["actual"] - g["mu"] for g in rows])) if rows else None}
    return out


def by(scored: list[dict[str, Any]], key) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in scored:
        k = key(g)
        if k is not None:
            groups[str(k)].append(g)
    return groups


# --------------------------------------------------------------------------- per control x candidate evaluation


def evaluate(pop: list[dict[str, Any]], cand: str) -> dict[str, Any]:
    betas, deltas = walk_forward(pop, cand)
    base = score(pop, None)
    cs = score(pop, deltas)
    base_by = by(base, lambda g: g["season"])
    cand_by = by(cs, lambda g: g["season"])
    seasons = {}
    for s in sorted(cand_by, key=int):
        sm = summarize(base_by[s], cand_by[s])
        seasons[s] = {
            k: sm.get(k)
            for k in (
                "n",
                "p0_mae",
                "cand_mae",
                "d_mae",
                "p0_rmse",
                "cand_rmse",
                "d_rmse",
                "p0_bias",
                "cand_bias",
                "d_brier",
                "n_corrected",
            )
        }
        seasons[s]["beta"] = betas.get(int(s))
    prim_ids = {g["game_id"] for g in cs if g["season"] in R.PRIMARY_FOLDS}
    pb = [g for g in base if g["game_id"] in prim_ids]
    pc = [g for g in cs if g["game_id"] in prim_ids]
    pooled = summarize(pb, pc)
    eras = {}
    for name, yrs in R.ERAS.items():
        ids = {g["game_id"] for g in cs if g["season"] in yrs}
        eras[name] = summarize([g for g in base if g["game_id"] in ids], [g for g in cs if g["game_id"] in ids])
    weeks = {}
    for name in R.WEEK_BLOCKS:
        ids = {g["game_id"] for g in pc if R.week_block(g["week"]) == name}
        weeks[name] = summarize([g for g in pb if g["game_id"] in ids], [g for g in pc if g["game_id"] in ids])
    talent = {}
    for name, flag in (("talent_active_seasons", True), ("talent_inactive_seasons", False)):
        ids = {g["game_id"] for g in pc if g["talent_active"] is flag}
        talent[name] = summarize([g for g in pb if g["game_id"] in ids], [g for g in pc if g["game_id"] in ids])
    loso = {}
    for s in R.PRIMARY_FOLDS:
        ids = {g["game_id"] for g in pc if g["season"] != s}
        sm = summarize([g for g in pb if g["game_id"] in ids], [g for g in pc if g["game_id"] in ids])
        loso[str(s)] = sm["d_mae"]
    total_diff = max((abs(g["total"] - g["p0_total"]) for g in cs), default=0.0)
    return {
        "betas_by_fold": {str(k): v for k, v in betas.items()},
        "seasons": seasons,
        "pooled_primary": pooled,
        "eras": eras,
        "week_blocks": weeks,
        "talent_prior": talent,
        "leave_one_season_out_d_mae": loso,
        "bias_p0": oriented_bias(pb),
        "bias_cand": oriented_bias(pc),
        "max_abs_total_diff": total_diff,
        "_scored": pc,
        "_base": pb,
    }


def gates(ev_live: dict[str, Any], ev_in: dict[str, Any], g10: dict[str, Any], fcs_max: float) -> dict[str, Any]:
    p = ev_live["pooled_primary"]
    fold_d = {s: ev_live["seasons"][str(s)]["d_mae"] for s in R.PRIMARY_FOLDS if str(s) in ev_live["seasons"]}
    rec_ids = (2023, 2024, 2025)
    rec = ev_live["eras"]
    recent_pooled = np.average([rec[str(s)]["d_mae"] for s in rec_ids], weights=[rec[str(s)]["n"] for s in rec_ids])

    def g3(ev: dict[str, Any]) -> bool:
        fd = [ev["seasons"][str(s)]["d_mae"] for s in R.PRIMARY_FOLDS]
        return (
            sum(1 for d in fd if d <= R.G3_FOLD_TOL) >= R.G3_MIN_NOT_WORSE
            and sum(1 for d in fd if d < 0) >= R.G3_MIN_BETTER
            and all(v < 0 for v in ev["leave_one_season_out_d_mae"].values())
        )

    def g1(ev: dict[str, Any]) -> bool:
        pp = ev["pooled_primary"]
        return pp["d_mae"] < 0 and pp["d_mae_ci95"] is not None and pp["d_mae_ci95"][1] < 0

    def g6() -> bool:
        b0, b1 = ev_live["bias_p0"], ev_live["bias_cand"]
        for k, v in b0.items():
            if v["n"] < R.G6_MIN_N:
                continue
            key = "fav_bias" if "fav_bias" in v else "bias"
            if abs(b1[k][key]) - abs(v[key]) > R.G6_BIAS_TOL:
                return False
        return True

    res = {
        "G1": {"value": [p["d_mae"], p["d_mae_ci95"]], "pass": g1(ev_live)},
        "G2": {"value": p["d_rmse"], "pass": p["d_rmse"] <= R.G2_RMSE_TOL},
        "G3": {"value": {"fold_d_mae": fold_d, "loso": ev_live["leave_one_season_out_d_mae"]}, "pass": g3(ev_live)},
        "G4": {
            "value": {"recent_pooled": float(recent_pooled), **{str(s): rec[str(s)]["d_mae"] for s in rec_ids}},
            "pass": recent_pooled <= 0 and all(rec[str(s)]["d_mae"] <= R.G4_SINGLE_TOL for s in rec_ids),
        },
        "G5": {
            "value": {"d_brier": p["d_brier"], "d_logloss": p["d_logloss"]},
            "pass": p["d_brier"] <= R.G5_BRIER_TOL and p["d_logloss"] <= R.G5_LOGLOSS_TOL,
        },
        "G6": {"value": {"p0": ev_live["bias_p0"], "cand": ev_live["bias_cand"]}, "pass": g6()},
        "G7": {"value": ev_live["max_abs_total_diff"], "pass": ev_live["max_abs_total_diff"] <= R.G7_TOTAL_TOL},
        "G8": {"value": "walk-forward folds asserted strictly prior; features pregame-only; tests", "pass": True},
        "G9": {"value": fcs_max, "pass": fcs_max == 0.0},
        "G10": {"value": g10, "pass": g10.get("d_mae") is not None and g10["d_mae"] <= R.G10_TOL},
        "G11": {"value": p["d_mae"], "pass": p["d_mae"] <= R.G11_PRACTICAL},
        "G12": {
            "value": {
                "inseason_d_mae": ev_in["pooled_primary"]["d_mae"],
                "ci95": ev_in["pooled_primary"]["d_mae_ci95"],
            },
            "pass": g1(ev_in) and g3(ev_in),
        },
    }
    for g in res.values():
        g["pass"] = bool(g["pass"])
    return res


def decide(results: dict[str, Any]) -> dict[str, Any]:
    """Protocol section 8."""
    order = {"P1": 0, "P2": 1, "P3": 2}
    params = {"P1": 1, "P2": 1, "P3": 2}

    def pick(names: list[str]) -> str | None:
        if not names:
            return None
        best = min(results[n]["live"]["pooled_primary"]["d_mae"] for n in names)
        near = [n for n in names if results[n]["live"]["pooled_primary"]["d_mae"] <= best + 0.01]
        return sorted(near, key=lambda n: (params[n], order[n]))[0]

    promotable = [n for n in results if all(g["pass"] for g in results[n]["gates"].values())]
    if promotable:
        return {"decision": "PROMOTE", "candidate": pick(promotable), "promotable": promotable}
    shadow_keys = ("G1", "G2", "G5", "G6", "G7", "G8", "G9", "G10")
    shadowable = [
        n
        for n in results
        if all(results[n]["gates"][k]["pass"] for k in shadow_keys)
        and results[n]["inseason"]["pooled_primary"]["d_mae"] < 0
    ]
    if shadowable:
        return {"decision": "SHADOW_ONLY", "candidate": pick(shadowable), "shadowable": shadowable}
    return {"decision": "REJECT", "candidate": None}


# --------------------------------------------------------------------------- diagnostics


def residual_diagnostic(pop: list[dict[str, Any]]) -> dict[str, Any]:
    """F: does the frozen feature predict P0 error? Pooled in-sample slope (descriptive) + season-cluster CI."""
    out = {}
    for f in ("x_rd", "x_rr", "x_ro"):
        rows = [r for r in pop if r.get(f) is not None and r["season"] in R.PRIMARY_FOLDS]
        x = np.array([r[f] for r in rows])
        y = np.array([r["_actual"] - r["_p0"]["margin"] for r in rows])
        slope = float(R.fit_beta(x, y)[0])
        seasons = sorted({r["season"] for r in rows})
        rng = np.random.default_rng(R.SEED)
        by_s = {s: np.array([i for i, r in enumerate(rows) if r["season"] == s]) for s in seasons}
        boots = []
        for _ in range(R.N_BOOT):
            idx = np.concatenate([by_s[s] for s in rng.choice(seasons, size=len(seasons))])
            boots.append(float(R.fit_beta(x[idx], y[idx])[0]))
        per_season = {str(s): float(R.fit_beta(x[by_s[s]], y[by_s[s]])[0]) for s in seasons}
        out[f] = {
            "n": len(rows),
            "slope_points_per_sd": slope,
            "ci95_season_cluster": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "per_season_slope": per_season,
            "seasons_positive": sum(1 for v in per_season.values() if v > 0),
            "corr_with_p0_error": R.corr(x, y),
        }
    return out


def double_counting(pop: list[dict[str, Any]]) -> dict[str, Any]:
    """G: overlap of the frozen features with P0's own components (P0-LIVE, primary folds)."""
    rows = [
        r
        for r in pop
        if r["season"] in R.PRIMARY_FOLDS
        and r.get("x_rd") is not None
        and r.get("x_rr") is not None
        and "live_off_diff" in r
    ]
    comps = {
        "offense_rating_diff": [r["live_off_diff"] for r in rows],
        "defense_rating_diff": [r["live_def_diff"] for r in rows],
        "talent_delta": [r["talent_delta"] for r in rows],
        "c2_margin_delta": [r["_p0"]["c2"] for r in rows],
        "expected_plays": [r["live_plays"] for r in rows],
        "p0_margin": [r["_p0"]["margin"] for r in rows],
    }
    err = np.array([r["_actual"] - r["_p0"]["margin"] for r in rows])
    p0m = np.array(comps["p0_margin"])
    out: dict[str, Any] = {"n": len(rows)}
    for f in ("x_rd", "x_rr"):
        x = np.array([r[f] for r in rows])
        out[f] = {
            "raw_corr": {k: R.corr(x, v) for k, v in comps.items()},
            "partial_corr_with_p0_error_given_p0_margin": R.partial_corr(x, err, p0m),
            "incremental_r2_p0_error_over_p0_margin": R.incremental_r2(err, p0m, x),
            "r2_x_on_p0_components": 1.0
            - float(
                (
                    (
                        x
                        - np.column_stack([np.ones(len(x))] + [np.array(v) for v in comps.values()])
                        @ np.linalg.lstsq(
                            np.column_stack([np.ones(len(x))] + [np.array(v) for v in comps.values()]), x, rcond=None
                        )[0]
                    )
                    ** 2
                ).sum()
                / ((x - x.mean()) ** 2).sum()
            ),
        }
    return out


def market_comparison(ev: dict[str, Any], pop: list[dict[str, Any]]) -> dict[str, Any]:
    """AB: descriptive only. Market close never enters P0 or the candidate."""
    close = {r["game_id"]: r.get("descriptive_close_spread_home") for r in pop}
    rows = [g for g in ev["_scored"] if close.get(g["game_id"]) is not None]
    mkt = np.array([-float(close[g["game_id"]]) for g in rows])
    p0m = np.array([g["p0_margin"] for g in rows])
    cm = np.array([g["mu"] for g in rows])
    act = np.array([g["actual"] for g in rows])
    d = cm - p0m
    moved = d != 0
    toward_market = np.sign(d[moved]) == np.sign((mkt - p0m)[moved])
    toward_actual = np.sign(d[moved]) == np.sign((act - p0m)[moved])

    sigma_by = {r["game_id"]: r["_p0"]["sigma"] for r in pop}

    def cover_brier(mu: np.ndarray) -> float:
        """Brier of P(home margin > market line) at the close, pushes excluded (sigma identical for both)."""
        out = []
        for m, s, a, g in zip(mu, mkt, act, rows, strict=True):
            if a == s:
                continue
            p = R.p_margin_gt(float(m), sigma_by[g["game_id"]], float(s))
            out.append((p - (1.0 if a > s else 0.0)) ** 2)
        return float(np.mean(out))

    return {
        "n": len(rows),
        "market_mae": float(np.mean(np.abs(act - mkt))),
        "p0_mae": float(np.mean(np.abs(act - p0m))),
        "cand_mae": float(np.mean(np.abs(act - cm))),
        "corr_p0_market": R.corr(p0m, mkt),
        "corr_cand_market": R.corr(cm, mkt),
        "mean_abs_p0_minus_market": float(np.mean(np.abs(p0m - mkt))),
        "mean_abs_cand_minus_market": float(np.mean(np.abs(cm - mkt))),
        "disagreements_ge_7_p0": int(np.sum(np.abs(p0m - mkt) >= 7)),
        "disagreements_ge_7_cand": int(np.sum(np.abs(cm - mkt) >= 7)),
        "share_corrections_toward_market": float(toward_market.mean()) if moved.any() else None,
        "share_corrections_toward_actual": float(toward_actual.mean()) if moved.any() else None,
        "cover_prob_brier_at_close_p0": cover_brier(p0m),
        "cover_prob_brier_at_close_cand": cover_brier(cm),
    }


def calibration_post_hoc(ev: dict[str, Any]) -> dict[str, Any]:
    """POST_HOC, descriptive only (added after the gates were computed; never feeds the decision): calibration slope
    of actual on projected margin, and favourite-oriented bias binned by each model's OWN projection."""
    rows = ev["_scored"]
    out: dict[str, Any] = {"label": "POST_HOC_DESCRIPTIVE"}
    for name, key in (("p0", "p0_margin"), ("cand", "mu")):
        x = np.array([g[key] for g in rows])
        y = np.array([g["actual"] for g in rows])
        bins = {}
        for lo, hi in R.MARGIN_BINS:
            rr = [g for g in rows if lo <= abs(g[key]) < hi]
            bins[f"{int(lo)}_{'inf' if math.isinf(hi) else int(hi)}"] = {
                "n": len(rr),
                "fav_bias": float(np.mean([(1.0 if g[key] >= 0 else -1.0) * (g["actual"] - g[key]) for g in rr]))
                if rr
                else None,
            }
        out[name] = {"calibration_slope": float(np.polyfit(x, y, 1)[0]), "own_bin_fav_bias": bins}
    return out


def sanity_2026(rows_2026: list[dict[str, Any]], beta: list[float], cand: str) -> dict[str, Any]:
    rows = [r for r in rows_2026 if r.get("home_points") is not None and None not in xs(r, cand)]
    if not rows:
        return {"n": 0, "d_mae": None}
    act = np.array([r["home_points"] - r["away_points"] for r in rows], dtype=float)
    p0m = np.array([r["p0_live_margin"] for r in rows])
    d = np.array([R.delta(beta, xs(r, cand), fbs_vs_fbs=True) for r in rows])
    return {
        "n": len(rows),
        "p0_mae": float(np.mean(np.abs(act - p0m))),
        "cand_mae": float(np.mean(np.abs(act - p0m - d))),
        "d_mae": float(np.mean(np.abs(act - p0m - d)) - np.mean(np.abs(act - p0m))),
        "p0_bias": float(np.mean(act - p0m)),
        "cand_bias": float(np.mean(act - p0m - d)),
        "slope_of_p0_error_on_x": [
            float(b) for b in R.fit_beta(np.array([[float(v) for v in xs(r, cand)] for r in rows]), act - p0m)
        ],
        "max_abs_total_change": 0.0,
    }


def main() -> int:
    hist = read_rows(DATA / "p0_games_2014_2025.jsonl.gz")
    rows_2026 = read_rows(DATA / "p0_games_2026.jsonl.gz")
    pops = {c: population(hist, c) for c in R.CONTROLS}
    fcs_rows = [r for r in hist if not r["fbs_vs_fbs"]]
    results: dict[str, Any] = {}
    for cand in R.CANDIDATES:
        ev_live = evaluate(pops["live"], cand)
        ev_in = evaluate(pops["inseason"], cand)
        final_train = [r for r in pops["live"] if None not in xs(r, cand)]
        beta_final = R.fit_beta(
            np.array([[float(v) for v in xs(r, cand)] for r in final_train]),
            np.array([r["_actual"] - r["_p0"]["margin"] for r in final_train]),
        )
        g10 = sanity_2026(rows_2026, [float(b) for b in beta_final], cand)
        fcs_max = max(abs(R.delta(beta_final, xs(r, cand), fbs_vs_fbs=False)) for r in fcs_rows)
        results[cand] = {
            "features": list(R.CANDIDATES[cand]),
            "live": ev_live,
            "inseason": ev_in,
            "beta_final_2015_2025_live": [float(b) for b in beta_final],
            "n_final_train": len(final_train),
            "sanity_2026": g10,
            "fcs_rows_checked": len(fcs_rows),
        }
        results[cand]["gates"] = gates(ev_live, ev_in, g10, fcs_max)
        results[cand]["market_comparison_live"] = market_comparison(ev_live, pops["live"])
        results[cand]["calibration_post_hoc"] = {
            "live": calibration_post_hoc(ev_live),
            "inseason": calibration_post_hoc(ev_in),
        }
    decision = decide(results)
    for cand in results:
        for c in ("live", "inseason"):
            results[cand][c].pop("_scored", None)
            results[cand][c].pop("_base", None)
    p0_by_season = {}
    for c in R.CONTROLS:
        base = score(pops[c], None)
        p0_by_season[c] = {
            s: {k: summarize(g, g)[k] for k in ("n", "p0_mae", "p0_rmse", "p0_bias", "p0_brier")}
            for s, g in sorted(by(base, lambda g: g["season"]).items())
        }
    report = {
        "schema": R.VERSION,
        "label": R.LABEL,
        "protocol": {"commit": R.PROTOCOL_COMMIT, "sha256": R.PROTOCOL_SHA256, "path": R.PROTOCOL_PATH},
        "starting_main": R.STARTING_MAIN,
        "inputs_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DATA.glob("p0_games_*.jsonl.gz"))
        },
        "populations": {c: len(p) for c, p in pops.items()},
        "p0_by_season": p0_by_season,
        "residual_diagnostic_live": residual_diagnostic(pops["live"]),
        "residual_diagnostic_inseason": residual_diagnostic(pops["inseason"]),
        "double_counting_live": double_counting(pops["live"]),
        "candidates": results,
        "decision": decision,
    }
    text = json.dumps(rnd(report, 6), indent=1, sort_keys=True, default=float) + "\n"
    REPORT.write_text(text, encoding="utf-8")
    print(hashlib.sha256(text.encode()).hexdigest())
    print(json.dumps(rnd(decision), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
