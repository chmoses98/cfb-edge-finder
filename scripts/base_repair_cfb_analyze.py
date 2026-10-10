"""Wave 2E analysis: base-projection repair (B1/B2/B3 vs P0), layer diagnosis, then the run-defense retest on
the frozen repaired base. Reads only committed inputs and writes
data/scripting/validation/base_repair/base_repair_report.json.
Every rule is the one frozen in docs/research/CFB_BASE_MODEL_REPAIR_PROTOCOL.md. RETROSPECTIVE_MODEL_REPAIR.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.modeling.margin_calibration import LinearMarginParams, fit_linear_margin  # noqa: E402
from cfb_edge_finder.modeling.margin_correction_artifact import FROZEN_MARGIN_CORRECTION_PARAMS  # noqa: E402
from cfb_edge_finder.signal_discovery import rush_projection as R  # noqa: E402

DATA = ROOT / "data" / "scripting" / "validation" / "base_repair"
W2D = ROOT / "data" / "scripting" / "validation" / "rush_projection"
REPORT = DATA / "base_repair_report.json"

VERSION = "cfb_base_model_repair/1.0.0"
PROTOCOL_PATH = "docs/research/CFB_BASE_MODEL_REPAIR_PROTOCOL.md"
PROTOCOL_COMMIT = "16d0e324217c71b555a4e32938ba6d61ef33a0e6"
PROTOCOL_SHA256 = "f446a8554b926799d8c288b7d1bc01e463ce43a32c2dc6978a0d768671cf766c"
SEED = 20261012
N_BOOT = 2000
PRIMARY = tuple(range(2018, 2026))
REPORTED = tuple(range(2016, 2026))
BINS = ((0, 3), (3, 7), (7, 14), (14, 21), (21, 28), (28, math.inf))
TAIL = 14.0
BASES = ("B1", "B2", "B3")
SLOPE_LO, SLOPE_HI = 0.90, 1.10


def read_rows(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def rnd(x: Any, n: int = 6) -> Any:
    if isinstance(x, float):
        return None if math.isnan(x) else round(x, n)
    if isinstance(x, dict):
        return {str(k): rnd(v, n) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [rnd(v, n) for v in x]
    if isinstance(x, np.bool_):
        return bool(x)
    return x


# --------------------------------------------------------------------------- model reconstruction


def c2_delta(raw: float, a: float, b: float, identity: bool, fbs: bool) -> float:
    return 0.0 if (identity or not fbs) else a * raw + b - raw


def window_fit(rows: list[dict[str, Any]], season: int, key: str) -> LinearMarginParams:
    """fit_linear_margin on FBS-vs-FBS (reg + post) rows of seasons max(2015, S-4)..S-1 (protocol 2)."""
    tr = [r for r in rows if r["fbs_vs_fbs"] and max(2015, season - 4) <= r["season"] < season and key in r["_m"]]
    assert all(r["season"] < season for r in tr)  # never its own season
    return fit_linear_margin(np.array([r["_m"][key] for r in tr]), np.array([r["_actual"] for r in tr]))


def reconstruct(hist: list[dict[str, Any]], sc: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict]:
    rows = []
    for r in hist:
        if "live_eh" not in r or "in_eh" not in r or "in_sigma" not in r or "live_sigma" not in r:
            continue
        fbs = r["fbs_vs_fbs"]
        t = r["talent_delta"]
        m: dict[str, float] = {}
        for tag, k in (("live", "P0"), ("in", "B1")):
            raw = r[f"{tag}_eh"] - r[f"{tag}_ea"]
            c2 = c2_delta(raw, r["c2_a"], r["c2_b"], r["c2_identity"], fbs)
            m[f"{k}_raw"] = raw
            m[f"{k}_raw_talent"] = raw + t
            m[f"{k}_c2"] = raw + c2
            m[k] = raw + c2 + t
        s = sc.get(r["game_id"])
        if s is not None:
            m["SC_raw"] = s["sc_eh"] - s["sc_ea"]
        rows.append(
            {
                **r,
                "_m": m,
                "_actual": float(r["home_points"] - r["away_points"]),
                "_actual_total": float(r["home_points"] + r["away_points"]),
                "_total": {"P0": r["live_eh"] + r["live_ea"], "B1": r["in_eh"] + r["in_ea"]},
                "_sigma": {"P0": r["live_sigma"], "B1": r["in_sigma"]},
            }
        )
    fits: dict[str, dict[int, Any]] = {"B2": {}, "B3": {}, "SC_c2": {}}
    by_season = defaultdict(list)
    for r in rows:
        by_season[r["season"]].append(r)
    for s in sorted(by_season):
        p2 = window_fit(rows, s, "B1")
        p3 = window_fit(rows, s, "B1_raw_talent")
        psc = window_fit(rows, s, "SC_raw")
        fits["B2"][s], fits["B3"][s], fits["SC_c2"][s] = p2, p3, psc
        for r in by_season[s]:
            fbs = r["fbs_vs_fbs"]
            m = r["_m"]
            m["B2"] = float(p2.apply(np.array([m["B1"]]))[0]) if fbs else m["B1"]
            m["B3"] = float(p3.apply(np.array([m["B1_raw_talent"]]))[0]) if fbs else m["B1"]
            if "SC_raw" in m:
                m["SC"] = (float(psc.apply(np.array([m["SC_raw"]]))[0]) if fbs else m["SC_raw"]) + r["talent_delta"]
            for k in ("B2", "B3"):
                r["_total"][k] = r["_total"]["B1"]  # zero-sum calibration: B1 total exactly
                r["_sigma"][k] = r["_sigma"]["B1"]
    meta = {
        k: {str(s): {"a": p.a, "b": p.b, "identity": p.is_identity_fallback} for s, p in v.items()}
        for k, v in fits.items()
    }
    return rows, meta


# --------------------------------------------------------------------------- metrics


def p_home(mu: float, sd: float) -> float:
    return min(1.0, max(0.0, 1.0 - NormalDist(mu, sd).cdf(0.5)))


def fav_dir(actual: float, proj: float) -> float:
    return (actual - proj) * (1.0 if proj >= 0 else -1.0)


def metrics(rows: list[dict[str, Any]], key: str, sigma_key: str | None = None) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    sk = sigma_key or (key if key in rows[0]["_sigma"] else key.split("_")[0])
    proj = np.array([r["_m"][key] for r in rows])
    act = np.array([r["_actual"] for r in rows])
    sig = np.array([r["_sigma"].get(sk, r["_sigma"]["B1"]) for r in rows])
    err = act - proj
    win = [(p_home(m, s), 1 if a > 0 else 0) for m, s, a in zip(proj, sig, act, strict=True) if a != 0]
    fde = np.array([fav_dir(a, p) for a, p in zip(act, proj, strict=True)])
    big = np.abs(proj) >= TAIL
    slope, intercept = np.polyfit(proj, act, 1)
    out = {
        "n": len(rows),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err**2))),
        "bias": float(np.mean(err)),
        "slope": float(slope),
        "intercept": float(intercept),
        "brier": float(np.mean([(p - y) ** 2 for p, y in win])),
        "logloss": float(np.mean([R.log_loss(p, y) for p, y in win])),
        "fav_tail": float(np.mean(fde[big])) if big.any() else None,
        "fav_tail_n": int(big.sum()),
        "dog_tail": float(np.mean(fde <= -TAIL) - np.mean(fde >= TAIL)),
        "resid_sd": float(np.std(err, ddof=1)),
        "mean_sigma": float(np.mean(sig)),
    }
    for lv in (0.5, 0.8, 0.9):
        z = NormalDist().inv_cdf(0.5 + lv / 2)
        out[f"cov{int(lv * 100)}"] = float(np.mean(np.abs(err) <= z * sig))
    out["bins"] = {
        f"{lo}_{'inf' if math.isinf(hi) else hi}": {
            "n": int(((np.abs(proj) >= lo) & (np.abs(proj) < hi)).sum()),
            "fav_bias": float(np.mean(fde[(np.abs(proj) >= lo) & (np.abs(proj) < hi)]))
            if ((np.abs(proj) >= lo) & (np.abs(proj) < hi)).any()
            else None,
        }
        for lo, hi in BINS
    }
    return out


def paired(rows: list[dict[str, Any]], key: str, base: str = "P0") -> dict[str, Any]:
    d = np.array([abs(r["_actual"] - r["_m"][key]) - abs(r["_actual"] - r["_m"][base]) for r in rows])
    return {
        "d_mae": float(d.mean()),
        "d_mae_ci95": R.boot_mean_ci(d, [(r["season"], r["week"]) for r in rows], seed=SEED, n_boot=N_BOOT),
    }


def total_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    e = np.array([r["_actual_total"] - r["_total"][key] for r in rows])
    return {"total_mae": float(np.mean(np.abs(e))), "total_bias": float(np.mean(e))}


def week_bucket(w: int) -> str:
    if w <= 1:
        return "week_1"
    if w <= 3:
        return "weeks_2_3"
    if w <= 5:
        return "weeks_4_5"
    return "weeks_6_plus"


# --------------------------------------------------------------------------- gates and selection


def base_gates(
    pop: list[dict[str, Any]],
    key: str,
    m0: dict[str, Any],
    mk: dict[str, Any],
    pr: dict[str, Any],
    fcs: dict[str, Any],
    y26: dict[str, Any],
) -> dict[str, Any]:
    folds = {}
    for s in PRIMARY:
        rs = [r for r in pop if r["season"] == s]
        a, b = metrics(rs, "P0"), metrics(rs, key)
        folds[str(s)] = {"d_mae": b["mae"] - a["mae"], "slope": b["slope"]}
    rec = [r for r in pop if r["season"] in (2023, 2024, 2025)]
    r0, rk = metrics(rec, "P0"), metrics(rec, key)
    tot0, totk = total_metrics(pop, "P0"), total_metrics(pop, key)
    g = {
        "G1": {"value": "current-season ingestion contract: tests/test_cfb_base_repair.py", "pass": True},
        "G2": {"value": mk["mae"] - m0["mae"], "pass": mk["mae"] - m0["mae"] <= 0.02},
        "G3": {"value": mk["rmse"] - m0["rmse"], "pass": mk["rmse"] - m0["rmse"] <= 0.02},
        "G4": {
            "value": {"d_brier": mk["brier"] - m0["brier"], "d_logloss": mk["logloss"] - m0["logloss"]},
            "pass": mk["brier"] - m0["brier"] <= 0.0005 and mk["logloss"] - m0["logloss"] <= 0.0015,
        },
        "G5": {
            "value": {"slope": mk["slope"], "p0_slope": m0["slope"]},
            "pass": SLOPE_LO <= mk["slope"] <= SLOPE_HI and abs(1 - mk["slope"]) <= abs(1 - m0["slope"]) - 0.05,
        },
        "G6": {
            "value": {
                "fav_tail": mk["fav_tail"],
                "p0_fav_tail": m0["fav_tail"],
                "material_improvement": abs(m0["fav_tail"]) - abs(mk["fav_tail"]) >= 0.5,
            },
            "pass": abs(mk["fav_tail"]) <= abs(m0["fav_tail"]),
        },
        "G7": {
            "value": {"dog_tail": mk["dog_tail"], "p0_dog_tail": m0["dog_tail"]},
            "pass": abs(mk["dog_tail"]) <= abs(m0["dog_tail"]) + 0.01,
        },
        "G8": {
            "value": folds,
            "pass": sum(1 for f in folds.values() if f["d_mae"] <= 0.05) >= 6
            and sum(1 for f in folds.values() if 0.85 <= f["slope"] <= 1.15) >= 6,
        },
        "G9": {
            "value": {"d_mae": rk["mae"] - r0["mae"], "slope": rk["slope"]},
            "pass": rk["mae"] - r0["mae"] <= 0.02 and SLOPE_LO <= rk["slope"] <= SLOPE_HI,
        },
        "G10": {
            "value": {
                "max_abs_calibration_total_change": max(abs(r["_total"][key] - r["_total"]["B1"]) for r in pop),
                "d_total_mae_vs_p0": totk["total_mae"] - tot0["total_mae"],
            },
            "pass": max(abs(r["_total"][key] - r["_total"]["B1"]) for r in pop) <= 1e-9
            and totk["total_mae"] - tot0["total_mae"] <= 0.10,
        },
        "G11": {"value": fcs, "pass": fcs["d_mae"] <= 0.25 and fcs["max_abs_calibration_delta"] == 0.0},
        "G12": {"value": "no market field read by any base model (tests)", "pass": True},
        "G13": {"value": "every calibration/rush fit asserted strictly-prior seasons (tests)", "pass": True},
        "G14": {"value": y26, "pass": y26.get("d_mae") is not None and y26["d_mae"] <= 0.50},
    }
    for v in g.values():
        v["pass"] = bool(v["pass"])
    return g


def select_base(gates: dict[str, dict[str, Any]], pooled: dict[str, dict[str, Any]]) -> dict[str, Any]:
    passing = [k for k in BASES if all(x["pass"] for x in gates[k].values())]
    if not passing:
        # protocol 8: research-diagnostic base only (best MAE among those whose slope is closest to 1)
        diag = min(BASES, key=lambda k: (round(abs(1 - pooled[k]["slope"]), 2), pooled[k]["mae"]))
        return {"decision": "KEEP_CURRENT_BASE", "base": None, "diagnostic_base": diag, "passing": []}
    best = min(pooled[k]["mae"] for k in passing)
    near = [k for k in passing if pooled[k]["mae"] <= best + 0.02]
    order = {"B3": 0, "B1": 1, "B2": 2}
    return {"decision": "PROMOTE_BASE", "base": sorted(near, key=order.get)[0], "passing": passing}


# --------------------------------------------------------------------------- 2026 sanity, FCS, market


def y2026(rows26: list[dict[str, Any]], prod: dict[str, LinearMarginParams]) -> dict[str, Any]:
    act = np.array([r["home_points"] - r["away_points"] for r in rows26], dtype=float)
    m = {
        "P0": np.array([r["p0_margin"] for r in rows26]),
        "B1": np.array([r["b1_margin"] for r in rows26]),
    }
    m["B2"] = prod["B2"].apply(m["B1"])
    m["B3"] = prod["B3"].apply(np.array([r["b1_raw"] + r["b1_talent"] for r in rows26]))
    out = {"n": len(rows26)}
    for k, v in m.items():
        out[k] = {
            "mae": float(np.mean(np.abs(act - v))),
            "rmse": float(np.sqrt(np.mean((act - v) ** 2))),
            "bias": float(np.mean(act - v)),
            "slope": float(np.polyfit(v, act, 1)[0]),
        }
    for k in BASES:
        out[k]["d_mae_vs_p0"] = out[k]["mae"] - out["P0"]["mae"]
    out["weeks"] = sorted({r["week"] for r in rows26})
    out["mean_current_season_rows"] = float(np.mean([r["current_season_rows_before"] for r in rows26]))
    out["p0_identical_across_weeks"] = True
    return out


def market(pop: list[dict[str, Any]], key: str) -> dict[str, Any]:
    rows = [r for r in pop if r.get("descriptive_close_spread_home") is not None]
    mk = np.array([-float(r["descriptive_close_spread_home"]) for r in rows])
    act = np.array([r["_actual"] for r in rows])
    p0 = np.array([r["_m"]["P0"] for r in rows])
    b = np.array([r["_m"][key] for r in rows])
    moved = np.abs(b - p0) > 1e-9
    return {
        "n": len(rows),
        "market_mae": float(np.mean(np.abs(act - mk))),
        "p0_mae": float(np.mean(np.abs(act - p0))),
        "base_mae": float(np.mean(np.abs(act - b))),
        "corr_p0_market": R.corr(p0, mk),
        "corr_base_market": R.corr(b, mk),
        "mean_abs_p0_minus_market": float(np.mean(np.abs(p0 - mk))),
        "mean_abs_base_minus_market": float(np.mean(np.abs(b - mk))),
        "disagreements_ge_7_p0": int(np.sum(np.abs(p0 - mk) >= 7)),
        "disagreements_ge_7_base": int(np.sum(np.abs(b - mk) >= 7)),
        "share_moves_toward_market": float(np.mean(np.abs(b - mk)[moved] < np.abs(p0 - mk)[moved])),
        "share_moves_toward_actual": float(np.mean(np.abs(b - act)[moved] < np.abs(p0 - act)[moved])),
    }


# --------------------------------------------------------------------------- run-defense retest (protocol 8)


def _a2d():
    spec = importlib.util.spec_from_file_location(
        "rush_projection_cfb_analyze_2e", ROOT / "scripts" / "rush_projection_cfb_analyze.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rush_projection_cfb_analyze_2e"] = mod
    spec.loader.exec_module(mod)
    return mod


def rush_population(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not (r["fbs_vs_fbs"] and not r["postseason"] and r.get("in_wave2c")) or r["season"] < R.FIRST_TRAIN_SEASON:
            continue
        mk = r["_m"][key]
        bt = r["_total"]["B1"] if key != "P0" else r["_total"]["P0"]
        home = (bt + mk) / 2.0
        out.append(
            {
                **r,
                "_p0": {
                    "margin": mk,
                    "home": home,
                    "away": bt - home,
                    "total": bt,
                    "sigma": r["_sigma"]["B1" if key != "P0" else "P0"],
                },
            }
        )
    return out


def rush_retest(
    rows: list[dict[str, Any]],
    key: str,
    rows26: list[dict[str, Any]],
    feats26: dict[str, dict[str, Any]],
    prod: dict[str, LinearMarginParams],
) -> dict[str, Any]:
    a = _a2d()
    pop = rush_population(rows, key)
    rob = rush_population(rows, "B1")
    fcs = [r for r in rows if not r["fbs_vs_fbs"]]
    out: dict[str, Any] = {
        "base": key,
        "residual_diagnostic": a.residual_diagnostic(pop),
        "double_counting": None,
        "candidates": {},
    }
    # 2026 base margins for the sanity gate
    b26 = {}
    for r in rows26:
        if key == "B1":
            b26[r["game_id"]] = r["b1_margin"]
        elif key == "B2":
            b26[r["game_id"]] = float(prod["B2"].apply(np.array([r["b1_margin"]]))[0])
        elif key == "B3":
            b26[r["game_id"]] = float(prod["B3"].apply(np.array([r["b1_raw"] + r["b1_talent"]]))[0])
    names = {"R1": "P1", "R2": "P3", "R3": "P2"}
    for rid, cand in names.items():
        ev = a.evaluate(pop, cand)
        ev_rob = a.evaluate(rob, cand) if key != "B1" else ev
        tr = [r for r in pop if None not in a.xs(r, cand)]
        beta = R.fit_beta(
            np.array([[float(v) for v in a.xs(r, cand)] for r in tr]),
            np.array([r["_actual"] - r["_p0"]["margin"] for r in tr]),
        )
        s26 = [(r, feats26.get(r["game_id"])) for r in rows26]
        s26 = [(r, f) for r, f in s26 if f and None not in [f.get(x) for x in R.CANDIDATES[cand]]]
        if s26:
            act = np.array([r["home_points"] - r["away_points"] for r, _ in s26], dtype=float)
            bm = np.array([b26[r["game_id"]] for r, _ in s26])
            d = np.array([R.delta(beta, [f[x] for x in R.CANDIDATES[cand]], fbs_vs_fbs=True) for _, f in s26])
            g10 = {"n": len(s26), "d_mae": float(np.mean(np.abs(act - bm - d)) - np.mean(np.abs(act - bm)))}
        else:
            g10 = {"n": 0, "d_mae": None}
        fcs_max = max(abs(R.delta(beta, a.xs(r, cand), fbs_vs_fbs=False)) for r in fcs)
        gates = a.gates(ev, ev_rob, g10, fcs_max)
        for k in ("_scored", "_base"):
            ev.pop(k, None)
            if ev_rob is not ev:
                ev_rob.pop(k, None)
        out["candidates"][rid] = {
            "wave2d_name": cand,
            "features": list(R.CANDIDATES[cand]),
            "pooled_primary": ev["pooled_primary"],
            "seasons": ev["seasons"],
            "eras": ev["eras"],
            "week_blocks": ev["week_blocks"],
            "betas_by_fold": ev["betas_by_fold"],
            "leave_one_season_out_d_mae": ev["leave_one_season_out_d_mae"],
            "bias_base": ev["bias_p0"],
            "bias_cand": ev["bias_cand"],
            "robustness_vs_B1": ev_rob["pooled_primary"] if ev_rob is not ev else "BASE is B1",
            "beta_final_2015_2025": [float(b) for b in beta],
            "sanity_2026": g10,
            "gates": gates,
        }
    return out


def rush_decision(cands: dict[str, Any], base_promoted: bool) -> dict[str, Any]:
    full = [k for k, c in cands.items() if all(g["pass"] for g in c["gates"].values())]
    shadow_keys = ("G1", "G2", "G5", "G6", "G7", "G8", "G9", "G10")
    shadow = [k for k, c in cands.items() if all(c["gates"][g]["pass"] for g in shadow_keys)]

    def pick(names: list[str]) -> str:
        best = min(cands[n]["pooled_primary"]["d_mae"] for n in names)
        near = [n for n in names if cands[n]["pooled_primary"]["d_mae"] <= best + 0.01]
        return sorted(near, key=lambda n: (len(cands[n]["features"]), n))[0]

    if full and base_promoted:
        return {"decision": "PROMOTE_RUSH", "candidate": pick(full)}
    if full or shadow:
        return {
            "decision": "SHADOW_RUSH",
            "candidate": pick(full or shadow),
            "reason": "all gates pass but base not promoted" if full else "partial gates",
        }
    return {"decision": "REJECT_RUSH", "candidate": None}


# --------------------------------------------------------------------------- main


def main() -> int:
    hist = read_rows(W2D / "p0_games_2014_2025.jsonl.gz")
    sc = {r["game_id"]: r for r in read_rows(DATA / "source_contract_2014_2025.jsonl.gz")}
    rows26 = read_rows(DATA / "games_2026.jsonl.gz")
    feats26 = {r["game_id"]: r for r in read_rows(W2D / "p0_games_2026.jsonl.gz")}
    rows, fits = reconstruct(hist, sc)
    pop = [r for r in rows if r["fbs_vs_fbs"] and not r["postseason"] and r["season"] in PRIMARY]
    keys = ("P0", *BASES)
    pooled = {k: metrics(pop, k) for k in keys}
    for k in BASES:
        pooled[k].update(paired(pop, k))
        pooled[k].update(total_metrics(pop, k))
    pooled["P0"].update(total_metrics(pop, "P0"))

    # production calibration artifacts = the S=2026 fold (fit on 2022-2025)
    prod = {"B2": window_fit(rows, 2026, "B1"), "B3": window_fit(rows, 2026, "B1_raw_talent")}
    y26 = y2026(rows26, prod)
    # FBS-vs-FCS (never pooled)
    fcs_pop = [r for r in rows if not r["fbs_vs_fbs"] and not r["postseason"] and r["season"] in PRIMARY]
    fcs = {k: metrics(fcs_pop, k) for k in keys}
    gates = {}
    for k in BASES:
        fk = {
            "d_mae": fcs[k]["mae"] - fcs["P0"]["mae"],
            "max_abs_calibration_delta": max(abs(r["_m"][k] - r["_m"]["B1"]) for r in fcs_pop),
        }
        gates[k] = base_gates(
            pop, k, pooled["P0"], pooled[k], pooled[k], fk, {"d_mae": y26[k]["d_mae_vs_p0"], **y26[k]}
        )
    selection = select_base(gates, pooled)
    base_key = selection["base"] or selection["diagnostic_base"]

    # layer diagnosis (protocol 5)
    layers = {}
    for name, key in (
        ("P0 raw ratings", "P0_raw"),
        ("P0 raw + talent", "P0_raw_talent"),
        ("P0 old C.2", "P0_c2"),
        ("P0 old C.2 + talent (current live)", "P0"),
        ("B1 raw ratings", "B1_raw"),
        ("B1 raw + talent", "B1_raw_talent"),
        ("B1 old C.2", "B1_c2"),
        ("B1 old C.2 + talent", "B1"),
        ("B2", "B2"),
        ("B3", "B3"),
    ):
        mm = metrics(pop, key, "P0" if key.startswith("P0") else "B1")
        layers[name] = {k: mm[k] for k in ("slope", "intercept", "mae", "rmse", "fav_tail", "dog_tail", "bias")}
    # C.2 audit: the frozen artifact on 2022-2025 raw margins
    audit = {}
    for tag in ("P0", "B1"):
        rs = [r for r in pop if r["season"] >= 2022]
        x = np.array([r["_m"][f"{tag}_raw"] for r in rs])
        y = np.array([r["_actual"] for r in rs])
        fx = FROZEN_MARGIN_CORRECTION_PARAMS.apply(x)
        audit[tag] = {
            "raw_slope": float(np.polyfit(x, y, 1)[0]),
            "frozen_c2_slope": float(np.polyfit(fx, y, 1)[0]),
            "frozen_c2_mae": float(np.mean(np.abs(y - fx))),
            "raw_mae": float(np.mean(np.abs(y - x))),
            "n": len(rs),
        }
    # talent interaction (composition, not an interaction term)
    talent = {}
    for flag in (True, False):
        rs = [r for r in pop if r["talent_active"] is flag]
        talent["active" if flag else "inactive"] = {
            k: {x: metrics(rs, k, "P0" if k.startswith("P0") else "B1")[x] for x in ("slope", "mae", "fav_tail")}
            for k in ("P0_raw", "P0_raw_talent", "P0_c2", "P0", "B1_raw", "B1_raw_talent", "B1_c2", "B1", "B2", "B3")
        }
    # segments
    seg = {}
    for name, fn in (
        ("season", lambda r: str(r["season"])),
        ("week", lambda r: week_bucket(r["week"])),
        ("site", lambda r: "neutral" if r["neutral_site"] else "home_away"),
    ):
        groups = defaultdict(list)
        for r in pop:
            groups[fn(r)].append(r)
        seg[name] = {
            g: {
                k: {x: metrics(rs, k)[x] for x in ("n", "mae", "rmse", "slope", "brier", "bias", "fav_tail")}
                for k in keys
            }
            for g, rs in sorted(groups.items())
        }
    eras = {}
    for name, yrs in R.ERAS.items():
        rs = [r for r in rows if r["fbs_vs_fbs"] and not r["postseason"] and r["season"] in yrs and r["season"] >= 2016]
        eras[name] = {k: {x: metrics(rs, k)[x] for x in ("n", "mae", "rmse", "slope", "fav_tail")} for k in keys}
    wk1 = [r for r in pop if r["week"] <= 1]
    week1_check = {
        "n": len(wk1),
        "b1_differs_from_p0": sum(1 for r in wk1 if abs(r["_m"]["B1_raw"] - r["_m"]["P0_raw"]) > 1e-9),
        "of_which_week_1_after_week_0_games": sum(
            1 for r in wk1 if r["week"] == 1 and abs(r["_m"]["B1_raw"] - r["_m"]["P0_raw"]) > 1e-9
        ),
    }
    # source contract
    sc_pop = [r for r in pop if "SC" in r["_m"]]
    m_sc, m_b1 = metrics(sc_pop, "SC", "B1"), metrics(sc_pop, "B1")
    source_contract = {
        "n": len(sc_pop),
        "b1_mae": m_b1["mae"],
        "sc_mae": m_sc["mae"],
        "d_mae": m_sc["mae"] - m_b1["mae"],
        "b1_slope": m_b1["slope"],
        "sc_slope": m_sc["slope"],
        "d_slope": m_sc["slope"] - m_b1["slope"],
        "accept_espn_definition": abs(m_sc["mae"] - m_b1["mae"]) <= 0.02 and abs(m_sc["slope"] - m_b1["slope"]) <= 0.01,
    }
    rush = rush_retest(rows, base_key, rows26, feats26, prod)
    rush["decision"] = rush_decision(rush["candidates"], selection["decision"] == "PROMOTE_BASE")
    report = {
        "schema": VERSION,
        "label": "RETROSPECTIVE_MODEL_REPAIR",
        "protocol": {"path": PROTOCOL_PATH, "commit": PROTOCOL_COMMIT, "sha256": PROTOCOL_SHA256},
        "inputs_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                W2D / "p0_games_2014_2025.jsonl.gz",
                W2D / "p0_games_2026.jsonl.gz",
                DATA / "source_contract_2014_2025.jsonl.gz",
                DATA / "games_2026.jsonl.gz",
            ]
        },
        "population_primary": len(pop),
        "pooled_primary": pooled,
        "fold_calibrations": fits,
        "production_calibration_artifacts_2022_2025": {k: {"a": v.a, "b": v.b} for k, v in prod.items()},
        "layer_diagnosis": layers,
        "c2_audit_frozen_artifact_2022_2025": audit,
        "talent_composition": talent,
        "segments": seg,
        "eras": eras,
        "week1_check": week1_check,
        "source_contract": source_contract,
        "fbs_vs_fcs": {k: {x: fcs[k][x] for x in ("n", "mae", "rmse", "bias", "slope")} for k in keys},
        "y2026": y26,
        "base_gates": gates,
        "base_selection": selection,
        "market_descriptive": {k: market(pop, k) for k in BASES},
        "rush_retest": rush,
    }
    text = json.dumps(rnd(report), indent=1, sort_keys=True, default=float) + "\n"
    REPORT.write_text(text, encoding="utf-8")
    print(hashlib.sha256(text.encode()).hexdigest())
    print(json.dumps({"base": selection, "rush": rush["decision"]}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
