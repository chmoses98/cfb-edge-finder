#!/usr/bin/env python3
"""Write the Phase 15 Predictive Confidence Matrix rows and the market-readiness file for CFB from results_p15.json.

    python make_outputs.py MATRIX_JSON READINESS_JSON
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, os.environ.get("P15EVAL_DIR", "/home/user/sift-alpha/research/phase15"))
import p15eval as PE  # noqa: E402

R = json.loads((HERE / "results_p15.json").read_text())
KA = R["kalshi_actual_markets"]["by_series"]
SEGNAME = {"FG": "FG", "h1": "1H", "h2": "2H", "q1": "Q1", "q2": "Q2", "q3": "Q3", "q4": "Q4"}


def kseries(seg, fam):
    q = {"FG": "", "h1": "1H", "h2": "2H", "q1": "1Q", "q2": "2Q", "q3": "3Q", "q4": "4Q"}[seg]
    if fam in ("winner", "tie"):
        return "KXNCAAFGAME" if seg == "FG" else f"KXNCAAF{q}"
    return f"KXNCAAF{q}{ {'spread': 'SPREAD', 'total': 'TOTAL', 'team_total': 'TEAMTOTAL'}[fam] }"


def verdict(sc, actual, p_holm=0.0):
    cal = 0.85 <= sc["slope"] <= 1.15 and abs(sc["bias"]) <= 0.02
    gain = sc["d_logloss"][1][1] < 0 and p_holm < 0.05
    mild = 0.75 <= sc["slope"] <= 1.25 and abs(sc["bias"]) <= 0.03
    seasons_ok = all(v[0] <= 0 or v[1][0] <= 0 for v in sc.get("by_season", {}).values())
    v = "RELIABLE" if (cal and gain and seasons_ok) else ("USABLE" if (cal or (gain and mild)) else "ABSTAIN")
    notes = []
    if actual is not None and v != "ABSTAIN":
        if not (0.8 <= actual["slope"] <= 1.25) or abs(actual["bias"]) > 0.03:
            v = "USABLE" if v == "RELIABLE" else "ABSTAIN"
            notes.append(f"downgraded: at every Kalshi-listed strike slope {actual['slope']}, bias {actual['bias']}")
    return v, notes


def main(mpath, rpath):
    rows = []
    fg = R["fg_point"]["all"]
    for fam in ("winner", "spread", "total", "team_total"):
        sc = R["fg_ladders"][f"{fam}_vs_prod"]
        act = KA.get(kseries("FG", fam))
        v, notes = verdict(sc, act)
        m = {k: sc[k] for k in ("n_rows", "brier", "brier_base", "d_brier", "logloss", "logloss_base", "d_logloss", "slope",
                                "slope_base", "bias", "bias_base", "by_season")}
        m["vs_pr307_d_logloss"] = R["fg_ladders"][f"{fam}_vs_v307"]["d_logloss"]
        m["margin_mae"], m["total_mae"] = fg["v2"]["margin_ae"], fg["v2"]["total_ae"]
        m["margin_crps"], m["total_crps"] = fg["v2"]["margin_crps"], fg["v2"]["total_crps"]
        m["d_margin_crps_vs_prod"], m["d_total_crps_vs_prod"] = fg["v2-prod"]["margin_crps"], fg["v2-prod"]["total_crps"]
        m["kalshi_actual_strikes"] = act
        drivers = ["#307 opponent-adjusted efficiency ratings refit before every game date", "talent/returning-production prior"]
        if fam == "spread":
            notes.append("abstain where model p < 0.10 or > 0.90 (fat margin tails: observed 0.13 at mean p 0.06)")
        if fam in ("total", "team_total"):
            notes.append("over-probabilities too high near the mean (total residual right-skewed, median -0.7; 2025-26 "
                         "scoring 0.9 below the model); beats production but miscalibrated")
        rows.append(PE.matrix_row("CFB", fam, "FG", "pr307_challenger (frozen 0.5.0 replaced in research only)",
                                  "production 0.5.0-early-season-talent-prior (replay)", sc["n_games"], m, v, drivers,
                                  "; ".join(notes)))
    holm = PE.holm({f"{s}_{f}": v["p_logloss"] for s, o in R["periods"].items() for f, v in o.items()
                    if isinstance(v, dict) and "p_logloss" in v})       # 25 pre-declared period comparisons
    for seg, o in R["periods"].items():
        for fam in ("winner", "tie", "spread", "total", "team_total"):
            if fam not in o:
                continue
            sc = o[fam]
            act = KA.get(kseries(seg, fam)) if fam not in ("winner", "tie") else None
            v, notes = verdict(sc, act, holm[f"{seg}_{fam}"])
            if fam == "tie" and not (0.5 <= sc["slope"] <= 1.5):
                notes.append("tie probability barely varies across games; slope not informative, judged on log loss/bias")
                v = "USABLE" if abs(sc["bias"]) <= 0.02 and sc["d_logloss"][1][1] < 0 and holm[f"{seg}_{fam}"] < 0.05 \
                    else "ABSTAIN"
            if seg == "h2":
                notes.append("Kalshi settled 2H markets INCLUDING overtime in 10/10 OT games despite rules text; model "
                             "targets regulation 2H")
            m = {k: sc[k] for k in ("n_rows", "brier", "brier_base", "d_brier", "logloss", "logloss_base", "d_logloss",
                                    "p_logloss", "slope", "slope_base", "bias", "bias_base", "by_season",
                                    "vs_alt_shape_d_logloss")}
            m["p_logloss_holm25"] = round(holm[f"{seg}_{fam}"], 4)
            m["crps_margin_delta_vs_naive"] = o["crps_margin_delta_vs_naive"]
            m["crps_total_delta_vs_naive"] = o["crps_total_delta_vs_naive"]
            m["kalshi_actual_strikes"] = act
            rows.append(PE.matrix_row(
                "CFB", fam, SEGNAME[seg], f"period layer fitted to {SEGNAME[seg]} outcomes + empirical neighbour PMF",
                "naive proportional split of the #307 full-game forecast (discretised normal)", sc["n_games"], m, v,
                ["#307 full-game team-points forecast", f"opponent-adjusted {SEGNAME[seg]} scoring ratings (walk-forward)",
                 "empirical lumpy period-score PMF (2016-2024)"], "; ".join(notes)))
    PE.write_rows(rows, mpath)
    readiness(rpath)
    for r in rows:
        print(r["segment"], r["family"], r["verdict"], r["notes"][:90])


def readiness(path):
    base_in = ["ESPN postgame summaries 2014-2026 (vault espn_summary_postgame, 10,374 + 751 games)",
               "CFBD cache 2014-2026 (games, talent, returning production)", "repo ESPN game log data/football/2026"]
    prod_in = ["CFBD games/advanced stats lines 2022-2025 (GameProjectionCache)", "247 talent composite (preseason)"]
    unused = ["ESPN play-by-play drives / success rate (used by #307 research, not production)",
              "CFBD returning production", "ESPN linescores (period scores; derived this phase)"]
    missing = [{"input": "QB/injury availability", "best_free_source": "none structured historically",
                "blocker": "no as-of snapshots"},
               {"input": "pregame weather forecast", "best_free_source": "NWS/Open-Meteo forecast archive",
                "blocker": "only realised conditions retrievable for history"}]
    fams = [("game winner", ["KXNCAAFGAME"], "FG", "READY", "yes: #307 forecasts from schedule + prior games"),
            ("full-game spread", ["KXNCAAFSPREAD"], "FG", "READY", "yes"),
            ("full-game total", ["KXNCAAFTOTAL"], "FG", "PARTIAL", "yes, but total probabilities miscalibrated (bias +0.06)"),
            ("full-game team total", ["KXNCAAFTEAMTOTAL"], "FG", "PARTIAL", "yes, bias +0.02-0.05"),
            ("1H winner (3-way)", ["KXNCAAF1H", "KXNCAAF1HWINNER"], "1H", "READY", "yes"),
            ("1H spread", ["KXNCAAF1HSPREAD"], "1H", "READY", "yes"),
            ("1H total", ["KXNCAAF1HTOTAL"], "1H", "READY", "yes"),
            ("1H team total", ["KXNCAAF1HTEAMTOTAL"], "1H", "READY", "yes"),
            ("2H winner/spread/total", ["KXNCAAF2H", "KXNCAAF2HSPREAD", "KXNCAAF2HTOTAL"], "2H", "PARTIAL",
             "yes for regulation; settlement includes OT in practice"),
            ("quarter winner (3-way)", ["KXNCAAF1Q", "KXNCAAF2Q", "KXNCAAF3Q", "KXNCAAF4Q"], "Q", "READY", "yes"),
            ("quarter spread", ["KXNCAAF1QSPREAD", "KXNCAAF2QSPREAD", "KXNCAAF3QSPREAD", "KXNCAAF4QSPREAD"], "Q", "READY", "yes"),
            ("quarter total", ["KXNCAAF1QTOTAL", "KXNCAAF2QTOTAL", "KXNCAAF3QTOTAL", "KXNCAAF4QTOTAL"], "Q", "READY", "yes"),
            ("1H/FG double result", ["KXNCAAF1HFT"], "combo", "BLOCKED", "no model for the joint 1H x FG outcome yet")]
    agree = R["kalshi_actual_markets"]["settlement_agreement"]
    out = []
    for name, sers, seg, status, pre in fams:
        ag = {s: agree.get(s) for s in sers if s in agree}
        settle = "yes: ESPN linescores reproduce Kalshi results " + json.dumps(ag)
        if seg == "2H":
            settle = "partial: Kalshi settled 2H incl. overtime (10/10 OT games) though rules say excluding OT " + json.dumps(ag)
        if seg == "combo":
            settle = "yes (derivable from linescores + finals)"
        out.append({"sport": "CFB", "family": name, "kalshi_series": sers, "inputs_available": base_in,
                    "inputs_used_in_production": prod_in if seg == "FG" else ["none (production has no period model)"],
                    "inputs_collected_unused": unused, "missing_inputs": missing,
                    "historical_depth": ("FBS-vs-FBS 2014-2026 (~770/season); outcomes 11,121 valid linescores"
                                         if seg != "FG" else "2014-2026 games; production replay 2014-2025 + 2026"),
                    "pregame_ready": pre, "settlement_ready": settle,
                    "acquired_this_phase": ["derived cfb_period_scores_2014_2026.parquet (11,124 games; 11,121 valid)"]
                    if seg != "FG" else [], "status": status})
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
