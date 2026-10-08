"""Stage A exploration screen (block A seasons only). RESEARCH ONLY.

Ranks football features and a CURATED list of football-logical interactions
by their association with the closing-line residuals (home ATS residual,
total residual) and with the football outcomes (home margin, total points).
Every association is reported with n, slope per SD, z, p and a BH q-value
across the WHOLE screen, plus the share of block-A seasons with the same sign.

Nothing here is evidence. The screen's only output is a ranked list from
which candidate rules are written down, frozen and then evaluated once on
block B (2020-2025), whose lines this module never opens (the runner
enforces the season restriction).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from cfb_edge_finder.signal_discovery import stats

UNIT_METRICS = (
    "success_rate",
    "early_down_success_rate",
    "yards_per_play",
    "first_down_rate",
    "third_down_rate",
    "rush_success_rate",
    "yards_per_designed_rush",
    "yards_per_rush",
    "pass_success_rate",
    "net_yards_per_dropback",
    "yards_per_pass_attempt",
    "interception_rate",
    "sack_rate_allowed",
    "havoc_allowed",
    "turnovers_per_game",
    "points_per_opportunity",
    "points_per_drive",
    "points_per_game",
    "plays_per_game",
    "seconds_per_play",
)

DIMS = ("sustained_efficiency", "scoring", "finishing", "rushing", "passing", "disruption")


def side_features(r: dict) -> dict[str, float | None]:
    """Home-perspective features for the ATS / margin targets."""
    out: dict[str, float | None] = {}
    for d in DIMS:
        out[f"net.{d}"] = r.get(f"net.{d}")
    for m in UNIT_METRICS:
        ho, hd, ao, ad = (
            r.get(f"{s}_{u}_q.{m}") for s, u in (("home", "off"), ("home", "def"), ("away", "off"), ("away", "def"))
        )
        if None not in (ho, hd, ao, ad):
            out[f"netq.{m}"] = (ho - ad) - (ao - hd)
            out[f"offdiff.{m}"] = ho - ao
            out[f"defdiff.{m}"] = hd - ad
    for k in ("gap.margin", "baseline.home_margin", "raw.ypp_net"):
        out[k] = r.get(k)
    hp, ap = r.get("home_pass_rate_raw"), r.get("away_pass_rate_raw")
    out["pass_rate_diff_raw"] = hp - ap if None not in (hp, ap) else None
    hv, av = r.get("home_ypp_resid_sd_pct"), r.get("away_ypp_resid_sd_pct")
    out["volatility_diff"] = hv - av if None not in (hv, av) else None
    hg, ag = r.get("home_giveaways_z_raw"), r.get("away_giveaways_z_raw")
    ht, at = r.get("home_takeaways_z_raw"), r.get("away_takeaways_z_raw")
    out["turnover_margin_z_raw"] = (ht - hg) - (at - ag) if None not in (hg, ag, ht, at) else None
    out["prior_games_diff"] = r["prior_games_home"] - r["prior_games_away"]
    sp = r.get("m.spread_home")
    out["market.spread_home(reference)"] = sp
    return out


def game_features(r: dict) -> dict[str, float | None]:
    """Game-level features for the total targets."""
    out: dict[str, float | None] = {}
    for k in (
        "possession_environment",
        "tempo_environment",
        "gap.total",
        "baseline.total_points",
        "def_quality_sum",
        "off_quality_sum",
    ):
        out[k] = r.get(k)
    for m in UNIT_METRICS:
        q = [r.get(f"{s}_{u}_q.{m}") for s in ("home", "away") for u in ("off", "def")]
        if None not in q:
            out[f"offsum.{m}"] = q[0] + q[2]
            out[f"defsum.{m}"] = q[1] + q[3]
            out[f"envsum.{m}"] = (q[0] - q[3]) + (q[2] - q[1])  # both offenses vs opposing defenses
    out["abs_net.sustained_efficiency"] = (
        abs(r["net.sustained_efficiency"]) if r.get("net.sustained_efficiency") is not None else None
    )
    hp, ap = r.get("home_pass_rate_raw"), r.get("away_pass_rate_raw")
    out["pass_rate_sum_raw"] = hp + ap if None not in (hp, ap) else None
    hv, av = r.get("home_ypp_resid_sd_pct"), r.get("away_ypp_resid_sd_pct")
    out["volatility_sum"] = hv + av if None not in (hv, av) else None
    out["market.total(reference)"] = r.get("m.total")
    return out


#: Curated, football-logical interactions (product of standardised terms), home perspective.
SIDE_INTERACTIONS = (
    ("net.sustained_efficiency", "possession_environment", "CONTROL x PACE"),
    ("net.passing", "possession_environment", "PASSING x PACE"),
    ("net.rushing", "net.sustained_efficiency", "RUSH x CONTROL"),
    ("net.disruption", "net.passing", "DISRUPTION x PASSING"),
    ("net.sustained_efficiency", "net.finishing", "EFFICIENCY x FINISHING"),
    ("net.sustained_efficiency", "volatility_sum", "CONTROL x VOLATILITY"),
    ("gap.margin", "possession_environment", "DISAGREEMENT x PACE"),
    ("net.sustained_efficiency", "prior_games_min", "CONTROL x SAMPLE SIZE"),
)


def _assoc(xs: list[float], ys: list[float], seasons: list[int]) -> dict[str, Any] | None:
    x, y = np.asarray(xs, float), np.asarray(ys, float)
    if len(x) < 100 or float(x.std()) == 0:
        return None
    xz = (x - x.mean()) / x.std(ddof=1)
    res = stats.ols(y, np.column_stack([np.ones(len(x)), xz]))
    by = defaultdict(list)
    for s, a, b in zip(seasons, xz, y, strict=False):
        by[s].append((a, b))
    signs = []
    for pairs in by.values():
        if len(pairs) >= 50:
            a = np.asarray([p[0] for p in pairs])
            b = np.asarray([p[1] for p in pairs])
            if a.std() > 0:
                signs.append(np.sign(np.cov(a, b)[0, 1]))
    beta = res["beta"][1]
    return {
        "n": len(x),
        "beta_per_sd": beta,
        "z": res["z"][1],
        "p": res["p"][1],
        "corr": float(np.corrcoef(x, y)[0, 1]),
        "seasons_same_sign": int(sum(1 for s in signs if s == np.sign(beta))),
        "seasons": len(signs),
    }


def screen(rows: list[dict]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    side_targets = {"home_ats_resid": "MARKET", "o.home_margin": "FOOTBALL"}
    total_targets = {"total_resid": "MARKET", "o.total_points": "FOOTBALL"}
    sf = [side_features(r) for r in rows]
    gf = [game_features(r) for r in rows]
    for r, f, g in zip(rows, sf, gf, strict=False):
        f["possession_environment"] = r.get("possession_environment")
        f["volatility_sum"] = g.get("volatility_sum")
        f["prior_games_min"] = min(r["prior_games_home"], r["prior_games_away"])
    # univariate
    for feats, targets, scope in ((sf, side_targets, "SIDE"), (gf, total_targets, "TOTAL")):
        names = sorted({k for f in feats for k in f})
        for name in names:
            for tgt, layer in targets.items():
                xs, ys, ss = [], [], []
                for r, f in zip(rows, feats, strict=False):
                    x, y = f.get(name), r.get(tgt)
                    if x is not None and y is not None:
                        xs.append(x)
                        ys.append(y)
                        ss.append(r["season"])
                a = _assoc(xs, ys, ss)
                if a:
                    entries.append(
                        {"scope": scope, "feature": name, "target": tgt, "layer": layer, "kind": "UNIVARIATE", **a}
                    )
    # curated interactions (product of z-scores), home perspective
    for a_name, b_name, label in SIDE_INTERACTIONS:
        pairs = [(f.get(a_name), f.get(b_name), r) for r, f in zip(rows, sf, strict=False)]
        pairs = [(a, b, r) for a, b, r in pairs if a is not None and b is not None]
        if len(pairs) < 100:
            continue
        A = np.asarray([p[0] for p in pairs])
        B = np.asarray([p[1] for p in pairs])
        A, B = (A - A.mean()) / A.std(), (B - B.mean()) / B.std()
        for tgt, layer in side_targets.items():
            sub = [
                (a * b, a, b, p[2][tgt], p[2]["season"])
                for a, b, p in zip(A, B, pairs, strict=False)
                if p[2].get(tgt) is not None
            ]
            y = np.asarray([s[3] for s in sub])
            X = np.column_stack([np.ones(len(sub)), [s[1] for s in sub], [s[2] for s in sub], [s[0] for s in sub]])
            res = stats.ols(y, X)
            entries.append(
                {
                    "scope": "SIDE",
                    "feature": f"{a_name} * {b_name}",
                    "label": label,
                    "target": tgt,
                    "layer": layer,
                    "kind": "INTERACTION",
                    "n": len(sub),
                    "beta_per_sd": res["beta"][3],
                    "z": res["z"][3],
                    "p": res["p"][3],
                }
            )
    # conditional screens inside CONTROL tiers: control-side ATS residual vs each side feature
    # (oriented to the control side)
    for tier in ("MODERATE", "STRONG"):
        sub = [
            (r, f)
            for r, f in zip(rows, sf, strict=False)
            if r["control_strength"] == tier and r["home_ats_resid"] is not None
        ]
        names = sorted({k for _, f in sub for k in f})
        for name in names:
            xs, ys, ss = [], [], []
            for r, f in sub:
                sg = 1.0 if r["control_side"] == "home" else -1.0
                x = f.get(name)
                if x is None:
                    continue
                orient = sg if name not in ("possession_environment", "volatility_sum", "prior_games_min") else 1.0
                xs.append(orient * x)
                ys.append(sg * r["home_ats_resid"])
                ss.append(r["season"])
            a = _assoc(xs, ys, ss)
            if a:
                entries.append(
                    {
                        "scope": f"WITHIN_{tier}_CONTROL",
                        "feature": name,
                        "target": "control_side_ats_resid",
                        "layer": "MARKET",
                        "kind": "CONDITIONAL",
                        **a,
                    }
                )
    q = stats.benjamini_hochberg([e["p"] for e in entries])
    for e, qq in zip(entries, q, strict=False):
        e["q_bh_whole_screen"] = qq
    market = sorted([e for e in entries if e["layer"] == "MARKET"], key=lambda e: e["p"])
    football = sorted([e for e in entries if e["layer"] == "FOOTBALL"], key=lambda e: e["p"])
    return {
        "associations_screened": len(entries),
        "market_associations": len(market),
        "football_associations": len(football),
        "market_q_lt_0_10": sum(1 for e in market if e["q_bh_whole_screen"] < 0.10),
        "market_q_lt_0_05": sum(1 for e in market if e["q_bh_whole_screen"] < 0.05),
        "top": {"market": market[:40], "football": football[:25]},
        "all": entries,
    }
