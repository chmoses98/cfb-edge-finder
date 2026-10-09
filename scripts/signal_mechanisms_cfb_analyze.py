#!/usr/bin/env python3
"""Wave 2C (CFB): signal-mechanism study -- analysis. RESEARCH ONLY. EXPLANATORY ONLY.

Reads only the committed study inputs in data/scripting/validation/signal_mechanisms/ and writes the versioned
artifact `mechanism_report.json` (cfb_signal_mechanisms/1.0.0). Every analysis follows
docs/research/CFB_SIGNAL_MECHANISM_PROTOCOL.md (202c9e91); anything not pre-registered is labelled POST_HOC. No
rule, filter, threshold, stake or recommendation is produced.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from cfb_edge_finder.signal_discovery import mechanisms as M  # noqa: E402
from cfb_edge_finder.signal_discovery import wave2 as W  # noqa: E402

OUT = ROOT / "data" / "scripting" / "validation" / "signal_mechanisms"
X_CORE = ["eff_z", "pass_res_z", "rush_res_z", "neutral"]


def read_gz(p: Path) -> list[dict]:
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return [json.loads(x) for x in fh]


def _default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return str(o)


def clean(o):
    if isinstance(o, dict):
        return {("None" if k is None else str(k)): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return o


def mean(v):
    v = [x for x in v if x is not None]
    return float(np.mean(v)) if v else None


# --------------------------------------------------------------------------- preparation


def prepare(hist: list[dict], fits: dict, sds: dict | None = None) -> tuple[list[dict], dict]:
    """Attach constructs (pregame) and derived market / outcome quantities. Standardisation uses 2014-2025 SDs."""
    rows = []
    for r in hist:
        for unit, key in (("def", "def_quality_sum"), ("off", "off_quality_sum")):
            if r.get(key) is None:  # the Wave-1 evaluator's formula (evaluate._derive), for rows built outside it
                q = [r.get(f"{s}_{unit}_q.points_per_game") for s in ("home", "away")]
                r[key] = sum(q) if None not in q else None
        c = M.construct(r, fits)
        d = dict(r)
        d.update(c)
        d["neutral"] = 1.0 if r.get("neutral_site") else 0.0
        d["eff"] = M._f(r.get("net.sustained_efficiency"))
        sp, tot = M._f(r.get("m.spread_home")), M._f(r.get("m.total"))
        mg = M._f(r.get("o.home_margin"))
        d["margin"] = mg
        d["exp_margin"] = None if sp is None else -sp
        d["ats"] = None if sp is None or mg is None else mg + sp
        d["total_resid"] = None if tot is None or r.get("o.total_points") is None else r["o.total_points"] - tot
        d["fcs"] = bool(r.get("fcs_involved")) or "fcs" in (r.get("home_division"), r.get("away_division"))
        rows.append(d)
    if sds is None:
        sds = {}
        for k in (
            "eff",
            "rush_off_diff",
            "rush_def_diff",
            "pass_off_diff",
            "pass_def_diff",
            "rush_matchup",
            "pass_matchup",
            "possession_environment",
            "def_quality_sum",
            "off_quality_sum",
            "net.finishing",
            "net.disruption",
            "control_mag",
        ):
            v = [x[k] for x in rows if x.get(k) is not None]
            sd = float(np.std(v, ddof=1)) if len(v) > 1 else 0.0
            sds[k] = {"mean": float(np.mean(v)) if v else 0.0, "sd": sd if sd > 0 else 1.0}
    for d in rows:
        for k, z in (
            ("eff", "eff_z"),
            ("rush_off_diff", "rush_off_z"),
            ("rush_def_diff", "rush_def_z"),
            ("pass_off_diff", "pass_off_z"),
            ("pass_def_diff", "pass_def_z"),
            ("rush_matchup", "rush_matchup_z"),
            ("pass_matchup", "pass_matchup_z"),
            ("possession_environment", "pace_z"),
            ("def_quality_sum", "defsupp_z"),
            ("off_quality_sum", "offenv_z"),
            ("net.finishing", "finishing_z"),
            ("net.disruption", "disruption_z"),
        ):
            v = M._f(d.get(k))
            d[z] = None if v is None else (v - sds[k]["mean"]) / sds[k]["sd"]
    return rows, sds


def sides(rows: list[dict], key: str) -> list[dict]:
    """One side-oriented record per game where `key` names a side (home/away)."""
    out = []
    for r in rows:
        s = r.get(key)
        if s not in ("home", "away"):
            continue
        g = M.sign(s)
        rec = {
            "season": r["season"],
            "game_id": r["game_id"],
            "side": s,
            "week": r.get("week"),
            "row": r,
            "side_margin": None if r["margin"] is None else g * r["margin"],
            "side_spread": None if r["m.spread_home"] is None else g * r["m.spread_home"],
            "side_resid": None if r["ats"] is None else g * r["ats"],
            "side_open": None if r.get("m.spread_open_home") is None else g * r["m.spread_open_home"],
        }
        for q in ("q1", "q2", "q3", "q4", "h1", "h2", "thru_q3", "ot"):
            v = r.get(f"g.{q}")
            rec[q] = None if v is None else g * v
        out.append(rec)
    return out


def fit_coef(rows, y, xs, which):
    return M.coef(rows, y, xs, which)


def boot_coef(rows, y, xs, which, **kw):
    i = 1 + list(xs).index(which)
    return M.season_boot_ols(rows, [y], xs, lambda b: float(b[y][i]), **kw)


# --------------------------------------------------------------------------- formal tests (protocol section 4)


def formal_tests(H: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None]
    xs = ["rush_res_z", "pass_res_z", "eff", "exp_margin", "neutral"]
    late = [
        r
        for r in sp
        if r.get("g.h1") is not None and r.get("g.h2") is not None and all(r.get(x) is not None for x in xs)
    ]

    T = {}
    T["T1_late_separation"] = M.season_boot_ols(
        late, ["g.h2", "g.h1"], xs, lambda b: float(b["g.h2"][1] - b["g.h1"][1])
    )
    T["T2_drive_sustainability"] = boot_coef(sp, "g.empty_rate_diff", xs, "rush_res_z")
    x3 = ["rush_def_z", "rush_off_z", "eff"]
    r3 = [r for r in sp if all(r.get(x) is not None for x in x3)]

    T["T3_rush_defense_vs_offense"] = M.season_boot_ols(r3, ["ats"], x3, lambda b: float(b["ats"][1] - b["ats"][2]))
    xw = X_CORE
    rw = [r for r in sp if all(r.get(x) is not None for x in xw)]

    def wdiff(feature):
        i = 1 + xw.index(feature)
        return lambda b: float(b["exp_margin"][i] - b["margin"][i])

    T["T4_market_overweights_passing"] = M.season_boot_ols(rw, ["exp_margin", "margin"], xw, wdiff("pass_res_z"))
    T["T5_market_underweights_rushing"] = M.season_boot_ols(rw, ["exp_margin", "margin"], xw, wdiff("rush_res_z"))
    prs = [s for s in sides(sp, "prs") if s["side_resid"] is not None]
    rrs = [s for s in sides(sp, "rrs") if s["side_resid"] is not None]
    both = [{**s, "_g": "P"} for s in prs] + [{**s, "_g": "R"} for s in rrs]

    def t6(s):
        a = [x["side_resid"] for x in s if x["_g"] == "P"]
        b = [x["side_resid"] for x in s if x["_g"] == "R"]
        return float(np.std(a, ddof=1) - np.std(b, ddof=1)) if len(a) > 2 and len(b) > 2 else None

    T["T6_passing_more_volatile"] = M.season_boot(both, t6)
    ctrl = [
        s for s in sides(sp, "control_side") if s["side_resid"] is not None and s["row"].get("control_mag") is not None
    ]
    for s in ctrl:
        s["control_mag"] = s["row"]["control_mag"]
    T["T7_control_magnitude_priced"] = M.season_boot_ols(
        ctrl, ["side_resid"], ["control_mag"], lambda b: float(b["side_resid"][1])
    )
    T["T7_reference_margin_slope"] = M.season_boot_ols(
        ctrl, ["side_margin"], ["control_mag"], lambda b: float(b["side_margin"][1])
    )
    tot = [
        r
        for r in H
        if r["total_resid"] is not None and all(r.get(x) is not None for x in ("pace_z", "defsupp_z", "offenv_z"))
    ]
    xt = ["pace_z", "defsupp_z", "offenv_z"]
    T["T8_pace_absorbed"] = boot_coef(tot, "total_resid", xt, "pace_z")
    T["T9_defensive_suppression_absorbed"] = boot_coef(tot, "total_resid", xt, "defsupp_z")
    env = [r for r in H if r["total_resid"] is not None and r.get("scoring_env_claim") in ("ELEVATED", "SUPPRESSED")]

    def t10(s):
        a = [r["total_resid"] for r in s if r["scoring_env_claim"] == "ELEVATED"]
        b = [r["total_resid"] for r in s if r["scoring_env_claim"] == "SUPPRESSED"]
        return float(np.mean(a) - np.mean(b)) if a and b else None

    T["T10_scoring_environment_absorbed"] = M.season_boot(env, t10)
    frs = [s for s in sides(sp, "frs") if s["side_open"] is not None and s["side_margin"] is not None]
    for s in frs:
        s["d_open_close"] = (s["side_margin"] + s["side_open"]) - (s["side_margin"] + s["side_spread"])
    T["T11_close_absorbs_rush_signal"] = M.season_boot(frs, lambda s: mean([x["d_open_close"] for x in s]))
    pv = {k: v.get("p") for k, v in T.items() if not k.startswith("T7_reference")}
    q = M.bh(pv)
    for k in pv:
        T[k]["q_bh"] = q[k]
    return T


# --------------------------------------------------------------------------- M1 rushing


def rushing_mechanism(H: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None]
    xs = ["rush_res_z", "pass_res_z", "eff", "exp_margin", "neutral"]
    channels = [
        "g.q1",
        "g.q2",
        "g.q3",
        "g.q4",
        "g.h1",
        "g.h2",
        "g.ot",
        "margin",
        "g.empty_rate_diff",
        "g.ppd_diff",
        "g.first_downs_diff",
        "g.poss_sec_diff",
        "g.plays_diff",
        "g.three_out_diff",
        "g.rz_trips_diff",
        "g.long_drives_diff",
        "g.turnovers_diff",
        "g.third_rate_diff",
        "g.rush_sr_diff",
        "g.pass_sr_diff",
        "g.sr_diff",
        "g.scoring_opps_diff",
        "g.rush_att_diff",
    ]
    coefs = {}
    for y in channels:
        coefs[y] = {
            "rush_res_z": M.coef(sp, y, xs, "rush_res_z"),
            "pass_res_z": M.coef(sp, y, xs, "pass_res_z"),
            "n": sum(1 for r in sp if r.get(y) is not None),
        }
    for r in sp:
        h, a = r.get("g.home_rz_td_rate"), r.get("g.away_rz_td_rate")
        r["g.rz_td_rate_diff"] = None if h is None or a is None else h - a
    coefs["g.rz_td_rate_diff"] = {
        "rush_res_z": M.coef(sp, "g.rz_td_rate_diff", xs, "rush_res_z"),
        "pass_res_z": M.coef(sp, "g.rz_td_rate_diff", xs, "pass_res_z"),
    }
    # mediation (descriptive): how much of the ATS coefficient on rush_res_z do post-game channels absorb?
    base = ["rush_res_z", "pass_res_z", "eff", "neutral"]
    meds = {
        "possession": ["g.poss_sec_diff", "g.plays_diff"],
        "drive_quality": ["g.empty_rate_diff", "g.ppd_diff"],
        "turnovers": ["g.turnovers_diff"],
        "second_half": ["g.h2"],
        "first_half": ["g.h1"],
    }
    med_rows = [r for r in sp if all(r.get(x) is not None for x in base + sum(meds.values(), []))]
    mediation = {"n": len(med_rows), "ats_coef_base": M.coef(med_rows, "ats", base, "rush_res_z")}
    for name, m in meds.items():
        mediation[f"ats_coef_with_{name}"] = M.coef(med_rows, "ats", base + m, "rush_res_z")
    # score timing: FRS / RRS sides vs matched controls (same side-spread bin and home/away, |frozen z| < 0.5)
    timing = {}
    for key in ("frs", "rrs", "prs"):
        timing[key] = matched_timing(sp, key)
    th = {}
    for y in ("g.turnovers_diff", "g.ints_diff", "g.fumbles_diff", "g.pass_att_diff", "g.rush_att_diff"):
        th[y] = {"rush_res_z": M.coef(sp, y, xs, "rush_res_z"), "pass_res_z": M.coef(sp, y, xs, "pass_res_z")}
    x3 = ["rush_def_z", "rush_off_z", "eff", "exp_margin", "neutral"]
    for y in (
        "g.turnovers_diff",
        "g.ints_diff",
        "g.fumbles_diff",
        "g.pass_att_diff",
        "g.rush_att_diff",
        "g.empty_rate_diff",
        "g.poss_sec_diff",
        "margin",
    ):
        th[f"{y}|rush_def_z"] = M.coef(sp, y, x3, "rush_def_z")
        th[f"{y}|rush_off_z"] = M.coef(sp, y, x3, "rush_off_z")
    mrows = [
        r
        for r in sp
        if all(
            r.get(x) is not None
            for x in ["rush_def_z", "rush_off_z", "eff", "neutral", "g.turnovers_diff", "g.ints_diff"]
        )
    ]
    th["ats_rush_def_coef_base"] = M.coef(mrows, "ats", ["rush_def_z", "rush_off_z", "eff", "neutral"], "rush_def_z")
    th["ats_rush_def_coef_with_turnovers"] = M.coef(
        mrows, "ats", ["rush_def_z", "rush_off_z", "eff", "neutral", "g.turnovers_diff"], "rush_def_z"
    )
    th["label"] = "POST_HOC decomposition of the turnover channel (not pre-registered)"
    return {
        "channel_coefficients": coefs,
        "mediation": mediation,
        "turnover_decomposition_POST_HOC": th,
        "score_timing_matched": timing,
        "lead_preservation": lead_preservation(sp),
        "margin_distributions": margin_distributions(sp),
        "matched_strata": matched_strata(sp),
    }


def spread_bin(v: float | None) -> int | None:
    if v is None:
        return None
    edges = [-60, -24, -17, -13.5, -10, -7, -4.5, -2.5, 0, 2.5, 4.5, 7, 10, 14, 21, 60]
    for i in range(len(edges) - 1):
        if v <= edges[i + 1]:
            return i
    return len(edges) - 2


def matched_timing(sp: list[dict], key: str) -> dict[str, Any]:
    treated = [s for s in sides(sp, key) if s["q1"] is not None]
    pool = defaultdict(list)
    for r in sp:
        z = r.get("frozen_z") if key == "frs" else r.get("rush_res_z" if key == "rrs" else "pass_res_z")
        if z is None or abs(z) >= 0.5 or r.get("g.q1") is None:
            continue
        for s in sides([{**r, "_both": "home"}], "_both") + sides([{**r, "_both": "away"}], "_both"):
            pool[(spread_bin(s["side_spread"]), s["side"])].append(s)
    fields = ("q1", "q2", "q3", "q4", "h1", "h2", "thru_q3", "side_margin", "side_resid")
    diffs = {f: [] for f in fields}
    used = 0
    for t in treated:
        ctrl = pool.get((spread_bin(t["side_spread"]), t["side"]))
        if not ctrl:
            continue
        used += 1
        for f in fields:
            cv = [c[f] for c in ctrl if c[f] is not None]
            if t[f] is not None and cv:
                diffs[f].append(t[f] - float(np.mean(cv)))
    return {
        "treated": len(treated),
        "matched": used,
        "treated_mean": {f: mean([t[f] for t in treated]) for f in fields},
        "minus_matched_controls": {f: mean(diffs[f]) for f in fields},
        "minus_matched_ci95": {f: _ci([{"v": v} for v in diffs[f]]) for f in ("h1", "h2", "q4", "side_resid")},
    }


def _ci(rows):
    if len(rows) < 10:
        return None
    v = np.array([r["v"] for r in rows])
    rng = np.random.default_rng(M.SEED)
    b = [v[rng.integers(0, len(v), len(v))].mean() for _ in range(1000)]
    return [float(np.quantile(b, 0.025)), float(np.quantile(b, 0.975))]


def lead_preservation(sp: list[dict]) -> dict[str, Any]:
    out = {}
    for key in ("frs", "rrs", "prs"):
        recs = [s for s in sides(sp, key) if s["h1"] is not None and s["side_margin"] is not None]
        pool = []
        for r in sp:
            z = r.get("frozen_z") if key == "frs" else r.get("rush_res_z" if key == "rrs" else "pass_res_z")
            if z is None or abs(z) >= 0.5 or r.get("g.h1") is None:
                continue
            pool += [s for s in sides([{**r, "_s": "home"}], "_s") + sides([{**r, "_s": "away"}], "_s")]
        res = {}
        for state, cond in (
            ("leading", lambda s: s["h1"] > 0),
            ("tied", lambda s: s["h1"] == 0),
            ("trailing", lambda s: s["h1"] < 0),
        ):
            t = [s for s in recs if cond(s)]
            c = [s for s in pool if cond(s) and s["side_margin"] is not None]
            res[state] = {
                "treated": summarize_state(t),
                "controls_abs_z_lt_0.5": summarize_state(c),
                "treated_minus_matched": matched_state(t, c),
            }
        out[key] = res
    return out


def matched_state(t: list[dict], c: list[dict]) -> dict[str, Any]:
    """Treated minus controls in the same side-spread bin, home/away and halftime-margin bin (POST_HOC refinement)."""
    hb = lambda v: (  # noqa: E731
        None
        if v is None
        else (0 if v <= -14 else 1 if v < -7 else 2 if v < 0 else 3 if v == 0 else 4 if v <= 7 else 5 if v <= 14 else 6)
    )  # noqa: E731
    pool = defaultdict(list)
    for s in c:
        pool[(spread_bin(s["side_spread"]), s["side"], hb(s["h1"]))].append(s)
    fields = {
        "win": lambda s: 1.0 if s["side_margin"] > 0 else 0.0,
        "second_half_margin": lambda s: s["h2"],
        "lead_increased": lambda s: 1.0 if (s["h2"] or 0) > 0 else 0.0,
        "comeback_against": lambda s: 1.0 if s["h1"] > 0 and s["side_margin"] < 0 else 0.0,
    }
    d = {k: [] for k in fields}
    used = 0
    for x in t:
        cc = pool.get((spread_bin(x["side_spread"]), x["side"], hb(x["h1"])))
        if not cc:
            continue
        used += 1
        for k, f in fields.items():
            v = [f(y) for y in cc if f(y) is not None]
            if f(x) is not None and v:
                d[k].append(f(x) - float(np.mean(v)))
    return {"matched": used, **{k: mean(v) for k, v in d.items()}}


def summarize_state(rs: list[dict]) -> dict[str, Any]:
    if not rs:
        return {"n": 0}
    return {
        "n": len(rs),
        "win": mean([1.0 if s["side_margin"] > 0 else 0.0 for s in rs]),
        "second_half_margin": mean([s["h2"] for s in rs]),
        "lead_increased": mean([1.0 if (s["h2"] or 0) > 0 else 0.0 for s in rs]),
        "comeback_against": mean([1.0 if s["h1"] > 0 and s["side_margin"] < 0 else 0.0 for s in rs]),
        "mean_side_spread": mean([s["side_spread"] for s in rs]),
    }


def margin_distributions(sp: list[dict]) -> dict[str, Any]:
    out = {}
    groups = {
        "FRS_rush_edge_frozen": sides(sp, "frs"),
        "RRS_rush_residual": sides(sp, "rrs"),
        "PRS_pass_residual": sides(sp, "prs"),
        "CONTROL": sides(sp, "control_side"),
        "all_home_sides": sides([{**r, "_s": "home"} for r in sp], "_s"),
    }
    for name, ss in groups.items():
        res = [s["side_resid"] for s in ss if s["side_resid"] is not None]
        mg = [s["side_margin"] for s in ss if s["side_margin"] is not None]
        d = M.describe(res)
        d["P_resid_ge_14"] = mean([1.0 if x >= 14 else 0.0 for x in res])
        d["P_resid_le_minus14"] = mean([1.0 if x <= -14 else 0.0 for x in res])
        d["P_margin_ge_21"] = mean([1.0 if x >= 21 else 0.0 for x in mg])
        d["ats"] = M.ats_record(res)
        out[name] = d
    return out


def matched_strata(sp: list[dict]) -> dict[str, Any]:
    """Efficiency quintile x side-spread bin x home/away (home perspective strata); top vs bottom rush_res tercile."""
    rs = [r for r in sp if r.get("rush_res_z") is not None and r.get("eff") is not None]
    e_edges = M.quantile_edges([r["eff"] for r in rs], 5)
    t_edges = M.quantile_edges([r["rush_res_z"] for r in rs], 3)
    strata = defaultdict(lambda: {"top": [], "bot": []})
    for r in rs:
        key = (M.bucket(r["eff"], e_edges), spread_bin(r["m.spread_home"]), r["neutral"])
        t = M.bucket(r["rush_res_z"], t_edges)
        if t == 2:
            strata[key]["top"].append(r["ats"])
        elif t == 0:
            strata[key]["bot"].append(r["ats"])
    diffs, w = [], []
    for v in strata.values():
        if v["top"] and v["bot"]:
            diffs.append(np.mean(v["top"]) - np.mean(v["bot"]))
            w.append(min(len(v["top"]), len(v["bot"])))
    pass_rs = [r for r in sp if r.get("pass_res_z") is not None and r.get("eff") is not None]
    p_edges = M.quantile_edges([r["pass_res_z"] for r in pass_rs], 3)
    pstrata = defaultdict(lambda: {"top": [], "bot": []})
    for r in pass_rs:
        key = (M.bucket(r["eff"], e_edges), spread_bin(r["m.spread_home"]), r["neutral"])
        t = M.bucket(r["pass_res_z"], p_edges)
        if t == 2:
            pstrata[key]["top"].append(r["ats"])
        elif t == 0:
            pstrata[key]["bot"].append(r["ats"])
    pd_, pw = [], []
    for v in pstrata.values():
        if v["top"] and v["bot"]:
            pd_.append(np.mean(v["top"]) - np.mean(v["bot"]))
            pw.append(min(len(v["top"]), len(v["bot"])))
    return {
        "rush_top_minus_bottom_tercile_home_ats": float(np.average(diffs, weights=w)) if diffs else None,
        "rush_strata": len(diffs),
        "pass_top_minus_bottom_tercile_home_ats": float(np.average(pd_, weights=pw)) if pd_ else None,
        "pass_strata": len(pd_),
        "note": (
            "within-stratum (efficiency quintile x spread bin x neutral) weighted mean difference of home ATS residual"
        ),
    }


# --------------------------------------------------------------------------- M2 passing vs rushing


def information_overlap(H: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None]
    feats = [
        "eff_z",
        "pass_res_z",
        "rush_res_z",
        "pass_def_z",
        "rush_def_z",
        "pass_off_z",
        "rush_off_z",
        "net.passing",
        "net.rushing",
    ]
    for r in sp:
        cs = r.get("control_side")
        r["ctrl_home_mag"] = 0.0 if cs is None else r.get("control_mag", 0.0) * M.sign(cs)
    feats.append("ctrl_home_mag")
    rs = [r for r in sp if all(r.get(f) is not None for f in feats)]
    out = {"n": len(rs), "corr": {}, "partial_given_eff": {}, "incremental_r2": {}}
    for a in feats + ["exp_margin", "margin"]:
        out["corr"][a] = {b: M.corr([r[a] for r in rs], [r[b] for r in rs]) for b in feats + ["exp_margin", "margin"]}
    for f in feats:
        if f == "eff_z":
            continue
        out["partial_given_eff"][f] = {
            "spread": M.partial_corr(rs, f, "exp_margin", ["eff_z", "neutral"]),
            "margin": M.partial_corr(rs, f, "margin", ["eff_z", "neutral"]),
        }
    base = ["eff_z", "neutral"]
    for tgt in ("exp_margin", "margin"):
        r0 = M.r2([r[tgt] for r in rs], [[r[x] for x in base] for r in rs])
        inc = {"base_r2_eff_neutral": r0}
        for f in ("pass_res_z", "rush_res_z", "pass_def_z", "rush_def_z", "ctrl_home_mag"):
            inc[f] = M.r2([r[tgt] for r in rs], [[r[x] for x in base + [f]] for r in rs]) - r0
        out["incremental_r2"][tgt] = inc
    return out


def market_decomposition(H: list[dict]) -> dict[str, Any]:
    """Models A-D for the closing spread (market expected margin) and for the actual margin; walk-forward by season."""
    sp = [r for r in H if r["ats"] is not None]
    models = {
        "A_eff": ["eff_z", "neutral"],
        "B_eff_pass": ["eff_z", "pass_res_z", "neutral"],
        "C_eff_rush": ["eff_z", "rush_res_z", "neutral"],
        "D_full": ["eff_z", "pass_res_z", "rush_res_z", "pace_z", "defsupp_z", "offenv_z", "neutral"],
    }
    out = {}
    seasons = sorted({r["season"] for r in sp})
    for name, xs in models.items():
        rs = [r for r in sp if all(r.get(x) is not None for x in xs)]
        res = {"features": xs, "n": len(rs)}
        if len(rs) < 50:
            out[name] = res
            continue
        for tgt in ("exp_margin", "margin"):
            beta = M.ols([r[tgt] for r in rs], [[r[x] for x in xs] for r in rs])
            res[f"{tgt}_coef"] = dict(zip(["intercept"] + xs, [float(b) for b in beta], strict=True))
            res[f"{tgt}_r2_in_sample"] = M.r2([r[tgt] for r in rs], [[r[x] for x in xs] for r in rs])
            # walk-forward: fit seasons < s, predict s (first two seasons excluded)
            pred, act = [], []
            fold_coefs = []
            for s in seasons[2:]:
                tr = [r for r in rs if r["season"] < s]
                te = [r for r in rs if r["season"] == s]
                b = M.ols([r[tgt] for r in tr], [[r[x] for x in xs] for r in tr])
                fold_coefs.append(dict(zip(["intercept"] + xs, [float(x) for x in b], strict=True)))
                for r in te:
                    pred.append(b[0] + sum(b[1 + i] * r[x] for i, x in enumerate(xs)))
                    act.append(r[tgt])
            p, a = np.array(pred), np.array(act)
            res[f"{tgt}_wf_oos_r2"] = float(1 - ((a - p) ** 2).sum() / ((a - a.mean()) ** 2).sum())
            res[f"{tgt}_wf_coef_range"] = {
                x: [min(f[x] for f in fold_coefs), max(f[x] for f in fold_coefs)] for x in xs
            }
        out[name] = res
    return out


def weight_table(H: list[dict]) -> dict[str, Any]:
    """Football weight (actual) vs market weight (embedded in the line) per standardised feature, with CIs."""
    sp = [r for r in H if r["ats"] is not None]
    spread_x = ["eff_z", "pass_res_z", "rush_res_z", "rush_def_z", "neutral"]
    rs = [r for r in sp if all(r.get(x) is not None for x in spread_x)]
    spread_panel = {}
    for f in ("eff_z", "pass_res_z", "rush_res_z", "rush_def_z"):
        spread_panel[f] = {
            f"{lab}_weight": boot_coef(rs, tgt, spread_x, f)
            for lab, tgt in (("football", "margin"), ("market", "exp_margin"), ("residual", "ats"))
        }
    tot = [
        r
        for r in H
        if r["total_resid"] is not None and all(r.get(x) is not None for x in ("pace_z", "defsupp_z", "offenv_z"))
    ]
    tx = ["pace_z", "defsupp_z", "offenv_z"]
    for r in tot:
        r["_total"] = r["o.total_points"]
        r["_mtotal"] = r["m.total"]
    total_panel = {}
    for f in tx:
        total_panel[f] = {
            f"{lab}_weight": boot_coef(tot, tgt, tx, f)
            for lab, tgt in (("football", "_total"), ("market", "_mtotal"), ("residual", "total_resid"))
        }
    return {
        "spread_panel": {"n": len(rs), "features": spread_x, "cells": spread_panel},
        "total_panel": {"n": len(tot), "features": tx, "cells": total_panel},
        "units": (
            "points per 1 SD of the feature (2014-2025 SD); football = actual margin/total, market = closing line, "
            "residual = difference"
        ),
    }


def passing_volatility(H: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None]
    out = {}
    for key in ("prs", "rrs"):
        ss = [s for s in sides(sp, key) if s["side_resid"] is not None]
        fav = [s for s in ss if s["side_spread"] < 0]
        out[key] = {
            "n": len(ss),
            "resid": M.describe([s["side_resid"] for s in ss]),
            "margin": M.describe([s["side_margin"] for s in ss]),
            "upset_rate_as_favourite": mean([1.0 if s["side_margin"] < 0 else 0.0 for s in fav]),
            "n_favourite": len(fav),
            "mean_side_spread": mean([s["side_spread"] for s in ss]),
            "P_resid_ge_21": mean([1.0 if s["side_resid"] >= 21 else 0.0 for s in ss]),
            "P_resid_le_minus21": mean([1.0 if s["side_resid"] <= -21 else 0.0 for s in ss]),
        }
        tov = [
            (s["side_resid"], M.sign(s["side"]) * s["row"]["g.turnovers_diff"])
            for s in ss
            if s["row"].get("g.turnovers_diff") is not None
        ]
        out[key]["corr_resid_with_side_turnover_diff"] = M.corr([a for a, _ in tov], [b for _, b in tov])
        out[key]["resid_sd_excluding_turnover_diff"] = None
        if tov:
            a = np.array([x for x, _ in tov])
            b = np.array([y for _, y in tov])
            beta = np.polyfit(b, a, 1)
            out[key]["resid_sd_excluding_turnover_diff"] = float(np.std(a - np.polyval(beta, b), ddof=1))
    # matched on efficiency: pass-edge vs rush-edge sides in the same efficiency quintile (side-oriented)
    return out


def visibility(H: list[dict]) -> dict[str, Any]:
    """Proxies the public can see: AP rank, scoring average, prior margin, record, pregame Elo."""
    sp = [r for r in H if r["ats"] is not None]
    for r in sp:
        eh, ea = r.get("v.elo_home"), r.get("v.elo_away")
        r["elo_diff"] = None if eh is None or ea is None else (eh - ea) / 100.0
        r["ppg_td_diff"] = (
            None
            if r.get("v.ppg_td_home") is None or r.get("v.ppg_td_away") is None
            else r["v.ppg_td_home"] - r["v.ppg_td_away"]
        )
        r["margin_td_diff"] = (
            None
            if r.get("v.margin_td_home") is None or r.get("v.margin_td_away") is None
            else r["v.margin_td_home"] - r["v.margin_td_away"]
        )
        r["last_margin_diff"] = (
            None
            if r.get("v.last_margin_home") is None or r.get("v.last_margin_away") is None
            else r["v.last_margin_home"] - r["v.last_margin_away"]
        )
        gh, ga = r.get("v.games_home") or 0, r.get("v.games_away") or 0
        r["winpct_diff"] = None if not gh or not ga else r["v.wins_home"] / gh - r["v.wins_away"] / ga
        r["ap_diff"] = (26 - (r.get("v.ap_home") or 26)) - (26 - (r.get("v.ap_away") or 26))
    proxies = ["elo_diff", "ppg_td_diff", "margin_td_diff", "last_margin_diff", "winpct_diff", "ap_diff"]
    rs = [r for r in sp if all(r.get(x) is not None for x in proxies + ["pass_res_z", "rush_res_z", "eff_z"])]
    out = {"n": len(rs), "corr_with_proxies": {}}
    for f in ("pass_res_z", "rush_res_z", "pass_off_z", "rush_off_z", "pass_def_z", "rush_def_z", "eff_z"):
        out["corr_with_proxies"][f] = {
            p: M.corr([r[f] for r in rs if r.get(f) is not None], [r[p] for r in rs if r.get(f) is not None])
            for p in proxies
        }
    base = ["eff_z", "pass_res_z", "rush_res_z", "neutral"]
    out["spread_weight_without_proxies"] = {f: M.coef(rs, "exp_margin", base, f) for f in ("pass_res_z", "rush_res_z")}
    out["spread_weight_with_proxies"] = {
        f: M.coef(rs, "exp_margin", base + proxies, f) for f in ("pass_res_z", "rush_res_z")
    }
    out["margin_weight_without_proxies"] = {f: M.coef(rs, "margin", base, f) for f in ("pass_res_z", "rush_res_z")}
    out["margin_weight_with_proxies"] = {
        f: M.coef(rs, "margin", base + proxies, f) for f in ("pass_res_z", "rush_res_z")
    }
    out["residual_weight_without_proxies"] = {f: M.coef(rs, "ats", base, f) for f in ("pass_res_z", "rush_res_z")}
    out["residual_weight_with_proxies"] = {
        f: M.coef(rs, "ats", base + proxies, f) for f in ("pass_res_z", "rush_res_z")
    }
    out["proxy_weights_in_spread"] = {p: M.coef(rs, "exp_margin", base + proxies, p) for p in proxies}
    out["proxy_weights_in_residual"] = {p: M.coef(rs, "ats", base + proxies, p) for p in proxies}
    out["note"] = (
        "AP diff = (26 - rank) difference, unranked = 26; elo per 100 points; to-date values use prior games only"
    )
    return out


# --------------------------------------------------------------------------- M3 CONTROL


TIER = {
    ("home", "MODERATE"): "HOME MODERATE",
    ("away", "MODERATE"): "AWAY MODERATE",
    ("home", "STRONG"): "HOME STRONG",
    ("away", "STRONG"): "AWAY STRONG",
}


def control_mechanism(H: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None]
    cs = sides(sp, "control_side")
    for s in cs:
        r = s["row"]
        s["tier"] = TIER.get((s["side"], r.get("control_strength")), f"{s['side']} {r.get('control_strength')}")
        s["mag"] = r.get("control_mag")
        p = r.get("m.ml_home_novig")
        s["ml_p"] = None if p is None else (p if s["side"] == "home" else 1 - p)
    tiers = {}
    for t in ("HOME MODERATE", "HOME STRONG", "AWAY MODERATE", "AWAY STRONG"):
        g = [s for s in cs if s["tier"] == t]
        tiers[t] = {
            "n": len(g),
            "win": mean([1.0 if s["side_margin"] > 0 else 0.0 for s in g]),
            "mean_side_spread": mean([s["side_spread"] for s in g]),
            "mean_margin": mean([s["side_margin"] for s in g]),
            "resid": M.describe([s["side_resid"] for s in g]),
            "ats": M.ats_record([s["side_resid"] for s in g]),
            "ml_implied_win_2021plus": mean([s["ml_p"] for s in g]),
            "ml_n": sum(1 for s in g if s["ml_p"] is not None),
            "ml_actual_win_2021plus": mean([1.0 if s["side_margin"] > 0 else 0.0 for s in g if s["ml_p"] is not None]),
        }
    # saturation: magnitude deciles -> win prob, market expected margin, actual margin, residual
    ms = [s for s in cs if s["mag"] is not None]
    edges = M.quantile_edges([s["mag"] for s in ms], 10)
    sat = M.bins(
        ms,
        lambda s: f"D{M.bucket(s['mag'], edges) + 1:02d}",
        {
            "mag": lambda s: s["mag"],
            "win": lambda s: 1.0 if s["side_margin"] > 0 else 0.0,
            "market_expected_margin": lambda s: -s["side_spread"],
            "actual_margin": lambda s: s["side_margin"],
            "resid": lambda s: s["side_resid"],
            "ml_implied": lambda s: s["ml_p"],
        },
    )
    for s in ms:
        s["_win"] = 1.0 if s["side_margin"] > 0 else 0.0
    slopes = {
        "win_per_mag": M.coef(ms, "_win", ["mag"], "mag"),
        "market_margin_per_mag": M.coef([{**s, "_e": -s["side_spread"]} for s in ms], "_e", ["mag"], "mag"),
        "actual_margin_per_mag": M.coef(ms, "side_margin", ["mag"], "mag"),
        "resid_per_mag": M.coef(ms, "side_resid", ["mag"], "mag"),
        "corr_mag_market_margin": M.corr([s["mag"] for s in ms], [-s["side_spread"] for s in ms]),
    }

    # loss modes and cover-failure modes (post-game channels, side-oriented)
    def chan(s):
        r, g = s["row"], M.sign(s["side"])

        def d(k):
            v = r.get(f"g.{k}_diff")
            return None if v is None else g * v

        own_ppd = r.get(f"g.{s['side']}_ppd")
        opp_ppd = r.get(f"g.{'away' if s['side'] == 'home' else 'home'}_ppd")
        opp_q4 = r.get(f"g.{'away' if s['side'] == 'home' else 'home'}_q4_pts")
        return {
            "turnover_margin": None if d("turnovers") is None else -d("turnovers"),
            "ypp_diff": d("ypp"),
            "sr_diff": d("sr"),
            "plays_total": r.get("g.plays_total"),
            "poss_diff": d("poss_sec"),
            "empty_diff": d("empty_rate"),
            "own_ppd": own_ppd,
            "opp_ppd": opp_ppd,
            "h1_margin": s["h1"],
            "h2_margin": s["h2"],
            "q4_margin": s["q4"],
            "opp_q4_points": opp_q4,
            "thru_q3_margin": s["thru_q3"],
            "side_spread": s["side_spread"],
        }

    groups = {
        "losses": [s for s in cs if s["side_margin"] < 0],
        "wins_failed_cover": [s for s in cs if s["side_margin"] > 0 and s["side_resid"] < 0],
        "wins_covered": [s for s in cs if s["side_margin"] > 0 and s["side_resid"] > 0],
    }
    modes = {}
    for name, g in groups.items():
        cs_ = [chan(s) for s in g]
        modes[name] = {"n": len(g), **{k: mean([c[k] for c in cs_]) for k in cs_[0]}} if cs_ else {"n": 0}
    # cover-failure decomposition: of wins that failed to cover, how many led by > spread through Q3 then gave back?
    wf = groups["wins_failed_cover"]
    modes["wins_failed_cover_paths"] = {
        "covered_through_q3_then_gave_back_in_q4": mean(
            [
                1.0 if (s["thru_q3"] is not None and s["thru_q3"] + s["side_spread"] > 0) else 0.0
                for s in wf
                if s["thru_q3"] is not None
            ]
        ),
        "never_separated_led_by_less_than_spread_at_half": mean(
            [1.0 if s["h1"] is not None and s["h1"] < -s["side_spread"] / 2 else 0.0 for s in wf if s["h1"] is not None]
        ),
        "mean_side_spread": mean([s["side_spread"] for s in wf]),
        "mean_margin": mean([s["side_margin"] for s in wf]),
    }
    return {"tiers": tiers, "saturation_deciles": sat, "slopes": slopes, "modes": modes}


def _record(r: dict, side: str) -> str:
    w, g = r.get(f"v.wins_{side}") or 0, r.get(f"v.games_{side}") or 0
    return f"{w}-{g - w}"


def strong_recognition(H: list[dict], R26: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None]
    for r in sp:
        r["is_strong"] = 1 if r.get("control_strength") == "STRONG" else 0
        eh, ea = r.get("v.elo_home"), r.get("v.elo_away")
        r["abs_elo"] = None if eh is None or ea is None else abs(eh - ea) / 100.0
        r["abs_margin_td"] = None if r.get("margin_td_diff") is None else abs(r["margin_td_diff"])
        r["abs_winpct"] = None if r.get("winpct_diff") is None else abs(r["winpct_diff"])
        r["n_ranked"] = (1 if r.get("v.ap_home") else 0) + (1 if r.get("v.ap_away") else 0)
        r["abs_spread"] = abs(r["m.spread_home"])
    prox = ["abs_elo", "abs_margin_td", "abs_winpct", "n_ranked"]
    rs = [r for r in sp if all(r.get(p) is not None for p in prox)]
    seasons = sorted({r["season"] for r in rs})
    ys, ps, ys2, ps2 = [], [], [], []
    for s in seasons[1:]:
        tr = [r for r in rs if r["season"] < s]
        te = [r for r in rs if r["season"] == s]
        b = M.logistic([r["is_strong"] for r in tr], [[r[p] for p in prox] for r in tr])
        b2 = M.logistic([r["is_strong"] for r in tr], [[r["abs_spread"]] for r in tr])
        for r in te:
            ys.append(r["is_strong"])
            ps.append(float(b[0] + sum(b[1 + i] * r[p] for i, p in enumerate(prox))))
            ys2.append(r["is_strong"])
            ps2.append(float(b2[0] + b2[1] * r["abs_spread"]))
    compare = {}
    for k in ("abs_elo", "abs_margin_td", "abs_winpct", "n_ranked", "abs_spread"):
        compare[k] = {
            "strong": mean([r[k] for r in rs if r["is_strong"]]),
            "control_moderate": mean([r[k] for r in rs if r.get("control_strength") == "MODERATE"]),
            "no_control": mean([r[k] for r in rs if r.get("control_side") is None]),
        }
    st = [s for s in sides(sp, "control_side") if s["row"].get("control_strength") == "STRONG"]
    skeptical = [s for s in st if -s["side_spread"] < W.SKEPTIC_BELOW_POINTS]
    cases = []
    for s in sorted(skeptical, key=lambda s: (s["season"], s["game_id"])):
        r = s["row"]
        o = "away" if s["side"] == "home" else "home"
        elo_s = (
            None
            if r.get("v.elo_home") is None or r.get("v.elo_away") is None
            else M.sign(s["side"]) * (r["v.elo_home"] - r["v.elo_away"])
        )
        cases.append(
            {
                "season": s["season"],
                "week": r.get("week"),
                "game_id": s["game_id"],
                "side": s["side"],
                "control_team": r[s["side"]],
                "opponent": r[o],
                "neutral": r.get("neutral_site"),
                "fcs": r.get("fcs"),
                "control_mag": r.get("control_mag"),
                "side_spread": s["side_spread"],
                "elo_diff_side": elo_s,
                "ap_side": r.get(f"v.ap_{s['side']}"),
                "ap_opp": r.get(f"v.ap_{o}"),
                "record_side": _record(r, s["side"]),
                "prior_games_side": r.get(f"prior_games_{s['side']}"),
                "margin": s["side_margin"],
                "resid": s["side_resid"],
            }
        )
    sk_stats = {
        "n": len(skeptical),
        "ats": M.ats_record([s["side_resid"] for s in skeptical]),
        "share_away": mean([1.0 if s["side"] == "away" else 0.0 for s in skeptical]),
        "share_elo_disagrees": mean(
            [1.0 if c["elo_diff_side"] is not None and c["elo_diff_side"] < 0 else 0.0 for c in cases]
        ),
        "median_prior_games_side": float(
            np.median([c["prior_games_side"] for c in cases if c["prior_games_side"] is not None])
        )
        if cases
        else None,
        "median_week": float(np.median([c["week"] for c in cases if c["week"] is not None])) if cases else None,
        "all_strong_median_week": float(np.median([s["row"]["week"] for s in st if s["row"].get("week") is not None])),
        "all_strong_share_away": mean([1.0 if s["side"] == "away" else 0.0 for s in st]),
        "all_strong_share_elo_disagrees": mean(
            [
                1.0 if M.sign(s["side"]) * (s["row"]["v.elo_home"] - s["row"]["v.elo_away"]) < 0 else 0.0
                for s in st
                if s["row"].get("v.elo_home") is not None and s["row"].get("v.elo_away") is not None
            ]
        ),
    }
    k26 = []
    for r in R26:
        im = r.get("k.pros002_implied_margin")
        if im is None:
            continue
        cs_ = r.get("control_side")
        mg = None if r.get("o.home_margin") is None or cs_ is None else M.sign(cs_) * r["o.home_margin"]
        k26.append(
            {
                "game_id": r["game_id"],
                "home": r.get("home"),
                "away": r.get("away"),
                "control_side": cs_,
                "implied_margin": im,
                "skeptical": im < W.SKEPTIC_BELOW_POINTS,
                "margin": mg,
                "resid": None if mg is None else mg - im,
                "control_mag": r.get("control_mag"),
            }
        )
    return {
        "walk_forward_auc_visible_proxies": M.auc(ys, ps),
        "walk_forward_auc_abs_spread": M.auc(ys2, ps2),
        "n": len(rs),
        "profile": compare,
        "corr_strong_mag_vs_market_margin": M.corr(
            [s["row"]["control_mag"] for s in st], [-s["side_spread"] for s in st]
        ),
        "strong_share_market_margin_ge_3": mean(
            [1.0 if -s["side_spread"] >= W.SKEPTIC_BELOW_POINTS else 0.0 for s in st]
        ),
        "strong_median_market_margin": float(np.median([-s["side_spread"] for s in st])),
        "n_strong": len(st),
        "skeptical_historical": sk_stats,
        "skeptical_cases_historical": cases,
        "kalshi_2026": {
            "priced": len(k26),
            "skeptical": sum(1 for x in k26 if x["skeptical"]),
            "median_implied": float(np.median([x["implied_margin"] for x in k26])) if k26 else None,
            "corr_mag_implied": M.corr(
                [x["control_mag"] for x in k26 if x["control_mag"] is not None],
                [x["implied_margin"] for x in k26 if x["control_mag"] is not None],
            ),
            "skeptical_rows": [x for x in k26 if x["skeptical"]],
        },
    }


# --------------------------------------------------------------------------- M4 2026 shift


def shift_2026(H: list[dict], R26: list[dict], build: dict) -> dict[str, Any]:
    def stats(rows):
        x = [r["defdiff.rush_success_rate"] for r in rows if r.get("defdiff.rush_success_rate") is not None]
        if len(x) < 5:
            return {"n": len(x)}
        z = [(v - W.DSC001_MEAN) / W.DSC001_SD for v in x]
        return {
            "n": len(x),
            "mean": float(np.mean(x)),
            "sd": float(np.std(x, ddof=1)),
            "share_abs_z_ge_1": mean([1.0 if abs(v) >= 1 else 0.0 for v in z]),
            "mean_abs_z": mean([abs(v) for v in z]),
        }

    weeks26 = sorted(
        {r["week"] for r in R26 if r.get("defdiff.rush_success_rate") is not None and r.get("week") is not None}
    )
    lo, hi = min(weeks26), max(weeks26)
    for r in R26:
        r["fcs"] = "fcs" in (r.get("home_division"), r.get("away_division")) or bool(r.get("fcs_involved"))
    s26 = {
        "all": stats(R26),
        "fbs_only": stats([r for r in R26 if not r["fcs"]]),
        "fcs_involved": stats([r for r in R26 if r["fcs"]]),
        "by_week": {str(w): stats([r for r in R26 if r.get("week") == w]) for w in weeks26},
        "share_fcs_rows": mean(
            [1.0 if r["fcs"] else 0.0 for r in R26 if r.get("defdiff.rush_success_rate") is not None]
        ),
    }
    hist = {}
    for s in sorted({r["season"] for r in H}):
        win = [
            r
            for r in H
            if r["season"] == s
            and r.get("season_type") == "regular"
            and r.get("week") is not None
            and lo <= r["week"] <= hi
        ]
        hist[str(s)] = {
            "window": stats(win),
            "fbs_only": stats([r for r in win if not r["fcs"]]),
            "fcs_involved": stats([r for r in win if r["fcs"]]),
            "share_fcs_rows": mean(
                [1.0 if r["fcs"] else 0.0 for r in win if r.get("defdiff.rush_success_rate") is not None]
            ),
            "full_season": stats([r for r in H if r["season"] == s]),
        }
    wsd = [h["window"]["sd"] for h in hist.values() if h["window"].get("sd") is not None]
    wmean = [h["window"]["mean"] for h in hist.values() if h["window"].get("mean") is not None]
    fbs_mean = [h["fbs_only"]["mean"] for h in hist.values() if h["fbs_only"].get("mean") is not None]
    fbs_sd = [h["fbs_only"]["sd"] for h in hist.values() if h["fbs_only"].get("sd") is not None]
    rule = {
        "hist_window_sd_range": [min(wsd), max(wsd)],
        "hist_window_mean_range": [min(wmean), max(wmean)],
        "hist_fbs_only_mean_range": [min(fbs_mean), max(fbs_mean)],
        "hist_fbs_only_sd_range": [min(fbs_sd), max(fbs_sd)],
        "2026_sd": s26["all"]["sd"],
        "2026_mean": s26["all"]["mean"],
        "2026_fbs_only_mean": s26["fbs_only"].get("mean"),
        "2026_fbs_only_sd": s26["fbs_only"].get("sd"),
    }
    sd_ok = s26["all"]["sd"] <= max(wsd) * 1.10
    mean_ok = s26["fbs_only"].get("mean") is not None and min(fbs_mean) <= s26["fbs_only"]["mean"] <= max(fbs_mean)
    rule["sd_within_rule"] = sd_ok
    rule["fbs_mean_within_range"] = mean_ok
    rule["T12_verdict"] = (
        "CONSISTENT_WITH_EARLY_SEASON_COMPOSITION" if (sd_ok and mean_ok) else "UNEXPLAINED_BY_EARLY_SEASON"
    )

    # uncertainty: |z| vs prior games (historical window and 2026)
    def unc(rows):
        out = {}
        for lo_, hi_, lab in ((0, 2, "<=2"), (3, 4, "3-4"), (5, 6, "5-6"), (7, 99, ">=7")):
            g = [
                r
                for r in rows
                if r.get("defdiff.rush_success_rate") is not None
                and r.get("prior_games_home") is not None
                and lo_ <= min(r["prior_games_home"], r["prior_games_away"] or 0) <= hi_
            ]
            out[lab] = {
                "n": len(g),
                "mean_abs_z": mean([abs((r["defdiff.rush_success_rate"] - W.DSC001_MEAN) / W.DSC001_SD) for r in g]),
                "mean_def_se_sr": mean(
                    [
                        ((r.get("home_def_se.success_rate") or 0) + (r.get("away_def_se.success_rate") or 0)) / 2
                        for r in g
                    ]
                ),
            }
        return out

    # source parity: league raw per-game rush success rates (team-game level), same weeks, FBS-vs-FBS
    def raw_rates(rows):
        v = []
        for r in rows:
            if r["fcs"]:
                continue
            for s in ("home", "away"):
                x = r.get(f"g.{s}_rush_sr")
                if x is not None:
                    v.append(x)
        return M.describe(v)

    raw = {"2026_espn": raw_rates([r for r in R26 if r.get("week") is not None and lo <= r["week"] <= hi])}
    for s in (2023, 2024, 2025):
        raw[f"{s}_cfbd"] = raw_rates(
            [r for r in H if r["season"] == s and r.get("week") is not None and lo <= r["week"] <= hi]
        )
    return {
        "weeks_2026": [lo, hi],
        "2026": s26,
        "historical_matched_window": hist,
        "decision_rule_T12": rule,
        "uncertainty_hist_window": unc([r for r in H if r.get("week") is not None and lo <= r["week"] <= hi]),
        "uncertainty_2026": unc(R26),
        "source_parity_raw_rush_success": raw,
        "source_parity_identity": build.get("cfbd_espn_parity_2026"),
        "source_definitions": {
            "historical": (
                "CFBD /stats/game/advanced successRate x plays, garbage time excluded (advanced_regular_nogarbage)"
            ),
            "2026": "production espn.play_log_counts _successful() on ESPN summary drives (garbage plays tracked)",
        },
    }


def signal_timing(H: list[dict]) -> dict[str, Any]:
    """Within-season stability: team pregame value at week k vs the team's last regular-season pregame value."""
    metrics = {
        "rush_def": "def_q.rush_success_rate",
        "rush_off": "off_q.rush_success_rate",
        "pass_def": "def_q.pass_success_rate",
        "pass_off": "off_q.pass_success_rate",
        "eff_off": "off_q.success_rate",
        "pace": "off_q.plays_per_game",
        "def_supp": "def_q.points_per_game",
    }
    team_vals = defaultdict(list)  # (season, team_id) -> [(week, {metric: v})]
    for r in H:
        if r.get("season_type") != "regular" or r.get("week") is None:
            continue
        for s in ("home", "away"):
            team_vals[(r["season"], r[f"{s}_id"])].append(
                (r["week"], {k: r.get(f"{s}_{v}") for k, v in metrics.items()})
            )
    out = {}
    for k in metrics:
        by_week = defaultdict(lambda: ([], []))
        for vals in team_vals.values():
            vals.sort()
            last = vals[-1][1].get(k)
            if last is None:
                continue
            for wk, d in vals[:-1]:
                if d.get(k) is not None and wk <= 10:
                    by_week[wk][0].append(d[k])
                    by_week[wk][1].append(last)
        out[k] = {str(w): {"n": len(a), "corr_with_last": M.corr(a, b)} for w, (a, b) in sorted(by_week.items())}
    # qualification turnover by week (frozen rule and CONTROL)
    qual = {}
    for wk in range(1, 15):
        g = [
            r for r in H if r.get("week") == wk and r.get("season_type") == "regular" and r.get("frozen_z") is not None
        ]
        if g:
            qual[str(wk)] = {
                "n": len(g),
                "frs_rate": mean([1.0 if r["frs"] else 0.0 for r in g]),
                "control_rate": mean([1.0 if r.get("control_side") else 0.0 for r in g]),
                "mean_abs_frozen_z": mean([abs(r["frozen_z"]) for r in g]),
            }
    return {"corr_week_k_vs_final_pregame": out, "qualification_by_week": qual}


def reliability(H: list[dict]) -> dict[str, Any]:
    """Season-to-season autocorrelation of final pregame team values; odd/even split-half of raw per-game rates."""
    metrics = {
        "rush_off": "off_q.rush_success_rate",
        "rush_def": "def_q.rush_success_rate",
        "pass_off": "off_q.pass_success_rate",
        "pass_def": "def_q.pass_success_rate",
        "eff_off": "off_q.success_rate",
        "eff_def": "def_q.success_rate",
        "pace": "off_q.plays_per_game",
        "def_supp": "def_q.points_per_game",
    }
    last = {}
    for r in sorted(H, key=lambda r: (r["season"], r.get("week") or 0)):
        if r.get("season_type") != "regular":
            continue
        for s in ("home", "away"):
            last[(r["season"], r[f"{s}_id"])] = {k: r.get(f"{s}_{v}") for k, v in metrics.items()}
    auto = {}
    for k in metrics:
        a, b = [], []
        for (season, team), d in last.items():
            nxt = last.get((season + 1, team))
            if nxt and d.get(k) is not None and nxt.get(k) is not None:
                a.append(d[k])
                b.append(nxt[k])
        auto[k] = {"n": len(a), "year_to_year_corr": M.corr(a, b)}
    # split-half on raw per-game outcomes (offense own rate; defense = opponent's rate)
    per = defaultdict(list)
    for r in sorted(H, key=lambda r: (r["season"], r.get("kickoff_utc") or "")):
        for s, o in (("home", "away"), ("away", "home")):
            per[(r["season"], r[f"{s}_id"])].append(
                {
                    "rush_off": r.get(f"g.{s}_rush_sr"),
                    "rush_def": r.get(f"g.{o}_rush_sr"),
                    "pass_off": r.get(f"g.{s}_pass_sr"),
                    "pass_def": r.get(f"g.{o}_pass_sr"),
                    "eff_off": r.get(f"g.{s}_sr"),
                    "eff_def": r.get(f"g.{o}_sr"),
                    "pace": r.get(f"g.{s}_plays"),
                    "def_supp": r.get(f"g.{o}_ppd"),
                }
            )
    split = {}
    for k in metrics:
        a, b = [], []
        for games in per.values():
            vals = [g[k] for g in games if g.get(k) is not None]
            if len(vals) < 8:
                continue
            a.append(float(np.mean(vals[0::2])))
            b.append(float(np.mean(vals[1::2])))
        c = M.corr(a, b)
        split[k] = {"team_seasons": len(a), "split_half_corr": c, "spearman_brown": M.spearman_brown(c)}
    return {
        "year_to_year": auto,
        "split_half_raw": split,
        "note": (
            "year-to-year uses each team's last regular-season pregame (opponent-adjusted) value; "
            "split-half uses raw per-game rates"
        ),
    }


# --------------------------------------------------------------------------- totals (M6-M8)


def totals_mechanism(H: list[dict]) -> dict[str, Any]:
    tot = [
        r
        for r in H
        if r["total_resid"] is not None and all(r.get(x) is not None for x in ("pace_z", "defsupp_z", "offenv_z"))
    ]
    xs = ["pace_z", "defsupp_z", "offenv_z"]
    for r in tot:
        r["_total"] = r["o.total_points"]
        r["_mtotal"] = r["m.total"]
        hp, ap = r.get("g.home_drives"), r.get("g.away_drives")
        r["_drives"] = None if hp is None or ap is None else hp + ap
        pts = r["o.total_points"]
        r["_ppd"] = None if not r["_drives"] else pts / r["_drives"]
        pl = r.get("g.plays_total")
        r["_ppp"] = None if not pl else pts / pl
        so = [r.get("g.home_scoring_opps"), r.get("g.away_scoring_opps")]
        r["_opps"] = None if None in so else sum(so)
        r["_sr"] = (
            mean([r.get("g.home_sr"), r.get("g.away_sr")])
            if r.get("g.home_sr") is not None and r.get("g.away_sr") is not None
            else None
        )
        rz = [r.get("g.home_rz_td_rate"), r.get("g.away_rz_td_rate")]
        r["_rz_td"] = mean(rz) if None not in rz else None
        ypp = [r.get("g.home_ypp"), r.get("g.away_ypp")]
        r["_ypp"] = mean(ypp) if None not in ypp else None
    channels = {
        "actual_total": "_total",
        "market_total": "_mtotal",
        "total_resid": "total_resid",
        "plays": "g.plays_total",
        "drives": "_drives",
        "points_per_drive": "_ppd",
        "points_per_play": "_ppp",
        "scoring_opps": "_opps",
        "success_rate": "_sr",
        "rz_td_rate": "_rz_td",
        "yards_per_play": "_ypp",
    }
    coefs = {f: {name: M.coef(tot, y, xs, f) for name, y in channels.items()} for f in xs}
    # binned: pace / suppression deciles -> market total, actual, residual
    out = {"n": len(tot), "coefficients_per_SD": coefs}
    for f in xs:
        e = M.quantile_edges([r[f] for r in tot], 5)
        out[f"{f}_quintiles"] = M.bins(
            tot,
            lambda r, f=f, e=e: f"Q{M.bucket(r[f], e) + 1}",
            {
                "feature": lambda r, f=f: r[f],
                "market_total": lambda r: r["_mtotal"],
                "actual_total": lambda r: r["_total"],
                "total_resid": lambda r: r["total_resid"],
                "plays": lambda r: r.get("g.plays_total"),
                "ppd": lambda r: r["_ppd"],
            },
        )
    claims = {}
    for name, key, vals in (
        ("pace_claim", "pace_claim", ("HIGH", "LOW")),
        ("scoring_env_claim", "scoring_env_claim", ("ELEVATED", "SUPPRESSED")),
    ):
        for v in vals:
            g = [r for r in tot if r.get(key) == v]
            claims[f"{name}={v}"] = {
                "n": len(g),
                "market_total": mean([r["_mtotal"] for r in g]),
                "actual_total": mean([r["_total"] for r in g]),
                "total_resid": mean([r["total_resid"] for r in g]),
                "plays": mean([r.get("g.plays_total") for r in g]),
            }
    g = [r for r in tot if r.get("defensive_suppression_claim")]
    claims["defensive_suppression_claim"] = {
        "n": len(g),
        "market_total": mean([r["_mtotal"] for r in g]),
        "actual_total": mean([r["_total"] for r in g]),
        "total_resid": mean([r["total_resid"] for r in g]),
        "plays": mean([r.get("g.plays_total") for r in g]),
        "ppd": mean([r["_ppd"] for r in g]),
        "rz_td": mean([r["_rz_td"] for r in g]),
        "sr": mean([r["_sr"] for r in g]),
    }
    claims["all_games"] = {
        "n": len(tot),
        "market_total": mean([r["_mtotal"] for r in tot]),
        "actual_total": mean([r["_total"] for r in tot]),
        "plays": mean([r.get("g.plays_total") for r in tot]),
        "ppd": mean([r["_ppd"] for r in tot]),
        "rz_td": mean([r["_rz_td"] for r in tot]),
        "sr": mean([r["_sr"] for r in tot]),
    }
    out["claims"] = claims
    return out


# --------------------------------------------------------------------------- market timing / geometry / benchmark


def open_close(H: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None and r.get("m.spread_open_home") is not None]
    out = {}
    for key in ("frs", "rrs", "prs", "control_side"):
        ss = [s for s in sides(sp, key) if s["side_open"] is not None and s["side_margin"] is not None]
        rv_open = [s["side_margin"] + s["side_open"] for s in ss]
        rv_close = [s["side_margin"] + s["side_spread"] for s in ss]
        move = [s["side_open"] - s["side_spread"] for s in ss]  # > 0: the line moved toward the side
        out[key] = {
            "n": len(ss),
            "resid_vs_open": M.ats_record(rv_open),
            "resid_vs_close": M.ats_record(rv_close),
            "mean_move_toward_side": mean(move),
            "share_toward": mean([1.0 if m > 0 else 0.0 for m in move]),
            "share_away": mean([1.0 if m < 0 else 0.0 for m in move]),
        }
    # continuous: does the line move with rush_res_z / pass_res_z (home perspective, close - open)?
    rs = [r for r in sp if all(r.get(x) is not None for x in X_CORE)]
    for r in rs:
        r["_move_home"] = r["m.spread_open_home"] - r["m.spread_home"]  # > 0: moved toward home
        r["_resid_open"] = r["margin"] + r["m.spread_open_home"]
    out["move_coef_home"] = {f: M.coef(rs, "_move_home", X_CORE, f) for f in ("eff_z", "pass_res_z", "rush_res_z")}
    out["resid_open_coef"] = {f: M.coef(rs, "_resid_open", X_CORE, f) for f in ("eff_z", "pass_res_z", "rush_res_z")}
    out["resid_close_coef"] = {f: M.coef(rs, "ats", X_CORE, f) for f in ("eff_z", "pass_res_z", "rush_res_z")}
    tt = [
        r
        for r in H
        if r.get("m.total_open") is not None and r["total_resid"] is not None and r.get("pace_z") is not None
    ]
    for r in tt:
        r["_tmove"] = r["m.total"] - r["m.total_open"]
        r["_tres_open"] = r["o.total_points"] - r["m.total_open"]
    out["pace_totals"] = {
        "n": len(tt),
        "move_per_pace_sd": M.coef(tt, "_tmove", ["pace_z"], "pace_z"),
        "resid_open_per_pace_sd": M.coef(tt, "_tres_open", ["pace_z"], "pace_z"),
        "resid_close_per_pace_sd": M.coef(tt, "total_resid", ["pace_z"], "pace_z"),
    }
    return out


def geometry(H: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None]
    cs = [s for s in sides(sp, "control_side") if s["row"].get("control_mag") is not None]
    e = M.quantile_edges([s["row"]["control_mag"] for s in cs], 4)
    sb = lambda v: (  # noqa: E731
        "dog"
        if v > 0
        else ("fav_0_3" if v > -3 else ("fav_3_10" if v > -10 else ("fav_10_20" if v > -20 else "fav_20plus")))
    )  # noqa: E731
    ctrl = M.bins(
        cs,
        lambda s: f"magQ{M.bucket(s['row']['control_mag'], e) + 1}|{sb(s['side_spread'])}|{s['side']}",
        {"resid": lambda s: s["side_resid"], "win": lambda s: 1.0 if s["side_margin"] > 0 else 0.0},
    )
    fr = sides(sp, "frs")
    rush = M.bins(
        fr,
        lambda s: f"absz_{_zbin(s['row']['frozen_z'])}|{sb(s['side_spread'])}|{s['side']}",
        {"resid": lambda s: s["side_resid"]},
    )
    return {
        "control_mag_quartile_x_side_spread_x_side": ctrl,
        "frozen_rush_absz_x_side_spread_x_side": rush,
        "control_market_skeptical_share_by_side": {
            s: mean([1.0 if x["side_spread"] > -3 else 0.0 for x in cs if x["side"] == s]) for s in ("home", "away")
        },
        "frs_side_underdog_share": mean([1.0 if s["side_spread"] > 0 else 0.0 for s in fr]),
    }


def _zbin(z: float) -> str:
    return "1-1.5" if abs(z) < 1.5 else ("1.5-2" if abs(z) < 2 else "2+")


def benchmark(H: list[dict]) -> dict[str, Any]:
    """Walk-forward OOS: market-only vs market + one signal, for the margin and the total."""
    sp = [r for r in H if r["ats"] is not None]
    seasons = sorted({r["season"] for r in sp})
    out = {}

    def wf(rows, tgt, xs):
        pred, act = [], []
        for s in seasons[2:]:
            tr = [r for r in rows if r["season"] < s]
            te = [r for r in rows if r["season"] == s]
            b = M.ols([r[tgt] for r in tr], [[r[x] for x in xs] for r in tr]) if len(tr) > 50 else None
            if b is None:
                continue
            for r in te:
                pred.append(b[0] + sum(b[1 + i] * r[x] for i, x in enumerate(xs)))
                act.append(r[tgt])
        p, a = np.array(pred), np.array(act)
        return float(np.sqrt(((a - p) ** 2).mean())) if len(a) else None

    for r in sp:
        cs = r.get("control_side")
        r["ctrl_home_mag"] = 0.0 if cs is None else (r.get("control_mag") or 0.0) * M.sign(cs)
    for name, f in (
        ("eff", "eff_z"),
        ("pass_res", "pass_res_z"),
        ("rush_res", "rush_res_z"),
        ("rush_def_frozen", "rush_def_z"),
        ("control", "ctrl_home_mag"),
        ("finishing_negctrl", "finishing_z"),
        ("disruption_negctrl", "disruption_z"),
    ):
        rs = [r for r in sp if r.get(f) is not None]
        fo = wf(rs, "margin", [f, "neutral"])
        mo = wf(rs, "margin", ["exp_margin"])
        fm = wf(rs, "margin", ["exp_margin", f, "neutral"])
        out[f"margin|{name}"] = {
            "n": len(rs),
            "seasons": sorted({r["season"] for r in rs}),
            "rmse_football_only": fo,
            "rmse_market_only": mo,
            "rmse_market_plus_signal": fm,
            "rmse_gain_over_market": None if mo is None or fm is None else mo - fm,
        }
    tot = [r for r in H if r["total_resid"] is not None]
    for r in tot:
        r["_total"] = r["o.total_points"]
        r["_mtotal"] = r["m.total"]
    for name, f in (("pace", "pace_z"), ("defsupp", "defsupp_z"), ("offenv", "offenv_z")):
        rs = [r for r in tot if r.get(f) is not None]
        out[f"total|{name}"] = {
            "n": len(rs),
            "rmse_football_only": wf(rs, "_total", [f]),
            "rmse_market_only": wf(rs, "_total", ["_mtotal"]),
            "rmse_market_plus_signal": wf(rs, "_total", ["_mtotal", f]),
        }
        out[f"total|{name}"]["rmse_gain_over_market"] = (
            out[f"total|{name}"]["rmse_market_only"] - out[f"total|{name}"]["rmse_market_plus_signal"]
        )
    return out


def matchup_decomposition(H: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None]
    out = {}
    for k in ("rush", "pass"):
        xs = [f"{k}_off_z", f"{k}_def_z", f"{k}_matchup_z", "eff_z", "neutral"]
        rs = [r for r in sp if all(r.get(x) is not None for x in xs)]
        out[k] = {"n": len(rs)}
        for tgt in ("margin", "exp_margin", "ats"):
            out[k][tgt] = {x: M.coef(rs, tgt, xs, x) for x in xs[:3]}
        out[k]["ats_boot"] = {x: boot_coef(rs, "ats", xs, x) for x in xs[:3]}
    return out


# --------------------------------------------------------------------------- negative controls, 2026, cases, matrix


def negative_controls(H: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None and all(r.get(x) is not None for x in X_CORE)]
    rng = np.random.default_rng(M.SEED)
    flip = rng.choice([-1.0, 1.0], len(sp))
    for r, f in zip(sp, flip, strict=True):
        r["_rush_flip"] = r["rush_res_z"] * f
    by = defaultdict(list)
    for r in sp:
        by[r["season"]].append(r)
    for rs in by.values():
        vals = [r["rush_res_z"] for r in rs]
        perm = rng.permutation(len(vals))
        for r, i in zip(rs, perm, strict=True):
            r["_rush_shuf"] = vals[i]
    xs_flip = ["eff_z", "pass_res_z", "_rush_flip", "neutral"]
    xs_shuf = ["eff_z", "pass_res_z", "_rush_shuf", "neutral"]
    out = {
        "NC1_sign_flipped_rush": boot_coef(sp, "ats", xs_flip, "_rush_flip"),
        "NC2_within_season_shuffle": boot_coef(sp, "ats", xs_shuf, "_rush_shuf"),
    }
    for f in ("finishing_z", "disruption_z"):
        out[f"NC3_{f}"] = boot_coef(sp, "ats", ["eff_z", f, "neutral"], f)
    xs = ["rush_res_z", "pass_res_z", "eff", "exp_margin", "neutral"]
    out["NC4_q1_timing_placebo_rush"] = M.coef(sp, "g.q1", xs, "rush_res_z")
    out["reference_T5_rush_res"] = M.coef(sp, "ats", X_CORE, "rush_res_z")
    return out


def consistency_2026(R26: list[dict], fits: dict, sds: dict) -> dict[str, Any]:
    rows, _ = prepare(R26, fits, sds)
    out = {}
    dk = [r for r in rows if r.get("m.dk_spread_home") is not None and r.get("margin") is not None]
    for r in dk:
        r["dk_ats"] = r["margin"] + r["m.dk_spread_home"]
        r["dk_exp"] = -r["m.dk_spread_home"]
    for key in ("frs", "rrs", "prs", "control_side"):
        ss = []
        for r in dk:
            s = r.get(key)
            if s in ("home", "away"):
                ss.append(M.sign(s) * r["dk_ats"])
        out[f"dk_spread|{key}"] = M.ats_record(ss)
    rs = [r for r in dk if all(r.get(x) is not None for x in X_CORE)]
    out["dk_weights"] = {
        "n": len(rs),
        **{
            f: {
                "football": M.coef([{**r, "y": r["margin"]} for r in rs], "y", X_CORE, f),
                "market": M.coef([{**r, "y": r["dk_exp"]} for r in rs], "y", X_CORE, f),
                "residual": M.coef([{**r, "y": r["dk_ats"]} for r in rs], "y", X_CORE, f),
            }
            for f in ("eff_z", "pass_res_z", "rush_res_z")
        },
    }
    tt = [
        r
        for r in rows
        if r.get("m.dk_total") is not None and r.get("o.total_points") is not None and r.get("pace_z") is not None
    ]
    for r in tt:
        r["dk_tres"] = r["o.total_points"] - r["m.dk_total"]
    out["dk_total"] = {
        "n": len(tt),
        **{
            f: {
                "football": M.coef([{**r, "y": r["o.total_points"]} for r in tt if r.get(f) is not None], "y", [f], f),
                "market": M.coef([{**r, "y": r["m.dk_total"]} for r in tt if r.get(f) is not None], "y", [f], f),
                "residual": M.coef([{**r, "y": r["dk_tres"]} for r in tt if r.get(f) is not None], "y", [f], f),
            }
            for f in ("pace_z", "defsupp_z", "offenv_z")
        },
    }
    ctrl = [r for r in rows if r.get("control_side") and r.get("margin") is not None]
    out["control_2026"] = {
        "n": len(ctrl),
        "win": mean([1.0 if M.sign(r["control_side"]) * r["margin"] > 0 else 0.0 for r in ctrl]),
        "strong_n": sum(1 for r in ctrl if r.get("control_strength") == "STRONG"),
    }
    k1 = [r for r in rows if r.get("k.pros001_implied_margin") is not None]
    out["kalshi_pros001"] = {
        "n": len(k1),
        "note": "Wave-2A replay residuals; see FOOTBALL_SIGNAL_DISCOVERY_WAVE2A_RESULTS.md",
    }
    return out


def case_studies(H: list[dict]) -> dict[str, Any]:
    sp = [r for r in H if r["ats"] is not None]

    def card(s):
        r = s["row"]
        o = "away" if s["side"] == "home" else "home"
        return {
            "season": s["season"],
            "week": r.get("week"),
            "game_id": s["game_id"],
            "side_team": r[s["side"]],
            "opponent": r[o],
            "side": s["side"],
            "frozen_z": r.get("frozen_z"),
            "rush_res_z": r.get("rush_res_z"),
            "pass_res_z": r.get("pass_res_z"),
            "eff": r.get("eff"),
            "control": f"{r.get('control_side')}/{r.get('control_strength')}",
            "side_spread": s["side_spread"],
            "side_margin": s["side_margin"],
            "resid": s["side_resid"],
            "h1": s["h1"],
            "h2": s["h2"],
            "turnover_margin": None
            if r.get("g.turnovers_diff") is None
            else -M.sign(s["side"]) * r["g.turnovers_diff"],
            "ppd_diff": None if r.get("g.ppd_diff") is None else M.sign(s["side"]) * r["g.ppd_diff"],
            "plays_total": r.get("g.plays_total"),
            "sr_diff": None if r.get("g.sr_diff") is None else M.sign(s["side"]) * r["g.sr_diff"],
        }

    frs = [s for s in sides(sp, "frs") if s["side_resid"] is not None]
    prs = [s for s in sides(sp, "prs") if s["side_resid"] is not None]
    ctrl_wins = [s for s in sides(sp, "control_side") if s["side_margin"] is not None and s["side_margin"] > 0]
    key = lambda s: (s["side_resid"], s["game_id"])  # noqa: E731
    return {
        "rule": "deterministic ranking by side ATS residual after membership was fixed (ties by game id)",
        "A_frs_largest_positive": [card(s) for s in sorted(frs, key=key, reverse=True)[:5]],
        "B_frs_largest_negative": [card(s) for s in sorted(frs, key=key)[:5]],
        "C_prs_most_negative": [card(s) for s in sorted(prs, key=key)[:5]],
        "E_control_wins_worst_cover": [card(s) for s in sorted(ctrl_wins, key=key)[:5]],
    }


def absorption_matrix(rep: dict[str, Any]) -> list[dict[str, Any]]:
    wt = rep["weight_table"]["spread_panel"]["cells"]
    tp = rep["weight_table"]["total_panel"]["cells"]
    ft = rep["formal_tests"]
    c26 = rep["consistency_2026"]

    def cell(c):
        return {
            "football": c["football_weight"]["est"],
            "market": c["market_weight"]["est"],
            "residual": c["residual_weight"]["est"],
            "residual_ci95": c["residual_weight"]["ci95"],
            "seasons_pos_neg": [
                c["residual_weight"].get("seasons_positive"),
                c["residual_weight"].get("seasons_negative"),
            ],
        }

    tiers = rep["control"]["tiers"]
    return [
        {
            "signal": "CONTROL",
            "unit": "tier",
            "football": {t: tiers[t]["win"] for t in tiers},
            "market": {t: tiers[t]["mean_side_spread"] for t in tiers},
            "residual": {t: tiers[t]["resid"].get("mean") for t in tiers},
            "T7": ft["T7_control_magnitude_priced"],
            "2026": c26.get("dk_spread|control_side"),
        },
        {
            "signal": "rush edge (rush_res_z)",
            "unit": "pts per SD",
            **cell(wt["rush_res_z"]),
            "2026_dk": c26["dk_weights"].get("rush_res_z"),
        },
        {
            "signal": "rush defense (frozen feature)",
            "unit": "pts per SD",
            **cell(wt["rush_def_z"]),
            "2026_dk_frs": c26.get("dk_spread|frs"),
        },
        {
            "signal": "pass edge (pass_res_z)",
            "unit": "pts per SD",
            **cell(wt["pass_res_z"]),
            "2026_dk": c26["dk_weights"].get("pass_res_z"),
        },
        {
            "signal": "overall efficiency",
            "unit": "pts per SD",
            **cell(wt["eff_z"]),
            "2026_dk": c26["dk_weights"].get("eff_z"),
        },
        {"signal": "pace", "unit": "total pts per SD", **cell(tp["pace_z"]), "2026_dk": c26["dk_total"].get("pace_z")},
        {
            "signal": "defensive suppression",
            "unit": "total pts per SD (higher = better defenses)",
            **cell(tp["defsupp_z"]),
            "2026_dk": c26["dk_total"].get("defsupp_z"),
        },
        {
            "signal": "scoring environment",
            "unit": "total pts per SD (offense quality sum)",
            **cell(tp["offenv_z"]),
            "2026_dk": c26["dk_total"].get("offenv_z"),
        },
        {
            "signal": "disruption (negative control)",
            "unit": "pts per SD",
            "residual": rep["negative_controls"]["NC3_disruption_z"],
        },
        {
            "signal": "closeness",
            "unit": "claim",
            "note": "Wave-1 CFB-SIG-006: underdog ATS 51.2 %, +0.16 (REJECTED, priced); not re-estimated",
        },
    ]


def main() -> int:
    build = json.loads((OUT / "build_manifest.json").read_text())
    hist = read_gz(OUT / "games_2014_2025.jsonl.gz")
    r26 = read_gz(OUT / "games_2026.jsonl.gz")
    fits = M.fit_residualisers(hist)
    H, sds = prepare(hist, fits)
    anchor = M.ats_record(
        [M.sign(s["side"]) * s["row"]["ats"] for s in sides([r for r in H if r["ats"] is not None], "frs")]
    )
    if anchor["n"] != M.ANCHOR["n"] or abs(anchor["mean"] - M.ANCHOR["mean"]) > 1e-9:
        raise M.MechanismIntegrityError(f"anchor not reproduced: {anchor}")
    R26, _ = prepare(r26, fits, sds)
    rep: dict[str, Any] = {
        "schema": M.VERSION,
        "protocol": {
            "commit": M.PROTOCOL_COMMIT,
            "sha256": M.PROTOCOL_SHA256,
            "sha256_now": hashlib.sha256(
                (ROOT / "docs/research/CFB_SIGNAL_MECHANISM_PROTOCOL.md").read_bytes()
            ).hexdigest(),
        },
        "build": {
            k: build.get(k)
            for k in (
                "code_sha",
                "log_pin",
                "log_sha256",
                "schedule_sha256",
                "sidecar_odds_sha256",
                "hashes",
                "n_hist",
                "n_2026",
                "historical",
                "rows_2026",
                "cfbd_digests",
            )
        },
        "wave2_pins": {
            "candidates_sha256": W.CANDIDATES_SHA256,
            "dsc001": [W.DSC001_FEATURE, W.DSC001_MEAN, W.DSC001_SD, W.Z_THRESHOLD],
            "skeptic_below": W.SKEPTIC_BELOW_POINTS,
        },
        "anchor": anchor,
        "residualisers": fits,
        "standardisation": sds,
        "populations": {
            "historical_rows": len(H),
            "with_spread": sum(1 for r in H if r["ats"] is not None),
            "with_total": sum(1 for r in H if r["total_resid"] is not None),
            "with_opener": sum(1 for r in H if r.get("m.spread_open_home") is not None and r["ats"] is not None),
            "frs_sides": sum(1 for r in H if r["frs"] and r["ats"] is not None),
            "rrs_sides": sum(1 for r in H if r["rrs"] and r["ats"] is not None),
            "prs_sides": sum(1 for r in H if r["prs"] and r["ats"] is not None),
            "control_sides": sum(1 for r in H if r.get("control_side") and r["ats"] is not None),
            "rows_2026": len(R26),
            "rows_2026_feature": sum(1 for r in R26 if r.get("defdiff.rush_success_rate") is not None),
            "rows_2026_dk": sum(1 for r in R26 if r.get("m.dk_spread_home") is not None),
        },
    }
    rep["formal_tests"] = formal_tests(H)
    rep["rushing"] = rushing_mechanism(H)
    rep["information_overlap"] = information_overlap(H)
    rep["market_decomposition"] = market_decomposition(H)
    rep["weight_table"] = weight_table(H)
    rep["passing_volatility"] = passing_volatility(H)
    rep["visibility"] = visibility(H)
    rep["control"] = control_mechanism(H)
    rep["strong_recognition"] = strong_recognition(H, R26)
    rep["shift_2026"] = shift_2026(H, R26, build)
    rep["signal_timing"] = signal_timing(H)
    rep["reliability"] = reliability(H)
    rep["totals"] = totals_mechanism(H)
    rep["open_close"] = open_close(H)
    rep["geometry"] = geometry(H)
    rep["benchmark"] = benchmark(H)
    rep["matchup"] = matchup_decomposition(H)
    rep["negative_controls"] = negative_controls(H)
    rep["consistency_2026"] = consistency_2026(r26, fits, sds)
    rep["case_studies"] = case_studies(H)
    rep["absorption_matrix"] = absorption_matrix(rep)
    payload = json.dumps(clean(rep), indent=1, sort_keys=True, default=_default) + "\n"
    M.assert_not_prospective(str(OUT / "mechanism_report.json"))
    (OUT / "mechanism_report.json").write_text(payload)
    print("mechanism_report.json sha256", hashlib.sha256(payload.encode()).hexdigest())
    print(
        json.dumps(
            clean(
                {
                    k: {
                        kk: vv
                        for kk, vv in v.items()
                        if kk in ("est", "ci95", "p", "q_bh", "seasons_positive", "seasons_negative")
                    }
                    for k, v in rep["formal_tests"].items()
                }
            ),
            indent=1,
            default=_default,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
