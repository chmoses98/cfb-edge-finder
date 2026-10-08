#!/usr/bin/env python3
"""Render docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE1_TABLES.md from the frozen evaluation report.

Every number in the CFB Wave 1 results document is taken from these generated tables.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave1"
OUT = ROOT / "docs" / "research" / "FOOTBALL_SIGNAL_DISCOVERY_WAVE1_TABLES.md"


def f(v, nd=3, pct=False, sign=True):
    if v is None:
        return "—"
    if pct:
        return f"{100 * v:.1f}%"
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


def main() -> int:
    rep = json.loads((BASE / "evaluation_report.json").read_text())
    scr = json.loads((BASE / "stage_a_screen.json").read_text())
    L = [
        "# Football Signal Discovery Wave 1 (CFB) — generated tables",
        "",
        f"Generated from `evaluation_report.json` (code `{rep['code_sha'][:9]}`, set 1 `{rep['set1_sha256'][:12]}`, "
        f"set 2 `{rep['set2_sha256'][:12]}`, run {rep['generated_at']}). Do not edit by hand: "
        "`python3 scripts/signal_discovery_cfb_tables.py`.",
        "",
        f"Rows: {rep['rows']} eligible games 2014–2025; with spread {rep['rows_with_spread']}, total {rep['rows_with_total']}, "
        f"moneyline {rep['rows_with_ml']} (2021+). Exclusions: {rep['exclusions']}.",
        "",
        f"Hypotheses in the FDR families: football {rep['hypotheses_tested_football']}, market {rep['hypotheses_tested_market']}.",
        "",
        "## 1. Every hypothesis (all seasons; set-2 rows are discovery-contaminated on block A — see §2)",
        "",
        "| Id | Status | Football effect | q (BH) | Market effect | q (BH) | Holm p (market) | Name |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in rep["summary"]:
        fdr = rep["results"][s["id"]].get("fdr", {})
        L.append(
            f"| {s['id']} | {s['status']} | {f(s['football_effect'])} | {f(s['football_q'], sign=False)} | {f(s['market_effect'])} | "
            f"{f(s['market_q'], sign=False)} | {f(fdr.get('market_p_holm'), sign=False)} | {s['name']} |"
        )
    L += [
        "",
        "Effects: SIDE = win-rate lift (football) and mean ATS residual in points or (win − no-vig implied) for ML; "
        "TOTAL = points vs season mean / total residual (direction-signed); SLOPE = points per feature SD.",
        "",
        "## 2. Set 2 on block B only (the decisive sample)",
        "",
        "| Id | Status | Market effect | q (BH, within set 2) | n |",
        "|---|---|---|---|---|",
    ]
    for s in rep["set2_block_b"]["summary"]:
        r = rep["set2_block_b"]["results"][s["id"]]
        n = (r.get("market") or r.get("ATS") or {}).get("n")
        L.append(f"| {s['id']} | {s['status']} | {f(s['market_effect'])} | {f(s['market_q'], sign=False)} | {n} |")
    L += [
        "",
        "## 3. Slope hypotheses: market residual detail",
        "",
        "| Id | n | β/SD (resid) | SE | p | Block A β (p) | Block B β (p) | seasons expected sign | LOSO range | Follow |z|≥1: n, cover, ROI@−110 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for sid, r in rep["results"].items():
        m = r.get("market") or {}
        if "beta_per_sd" not in m:
            continue
        b = m["blocks"]
        fr = m["follow_rule_abs_z_ge_1"]
        L.append(
            f"| {sid} | {m['n']} | {f(m['beta_per_sd'])} | {f(m['se'], sign=False)} | {f(m['p'], 4, sign=False)} | "
            f"{f(b['A'].get('beta_per_sd'))} ({f(b['A'].get('p'), sign=False)}) | {f(b['B'].get('beta_per_sd'))} ({f(b['B'].get('p'), sign=False)}) | "
            f"{f(m['share_seasons_expected_sign'], pct=True)} | {f(m['loso_min'])}…{f(m['loso_max'])} | "
            f"{fr['n']}, {f(fr.get('cover_rate'), pct=True)}, {f(fr.get('roi_assumed_110'), pct=True)} |"
        )
    L += [
        "",
        "## 4. Side hypotheses: football, ATS and moneyline",
        "",
        "| Id | n | Win | Lift | ATS n | W-L-P | Cover [95%] | Mean resid (p) | ROI@−110 | ML n | Win − implied (p) | ML ROI [95%] |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for sid, r in rep["results"].items():
        if "ATS" not in r:
            continue
        fb, a, m = r["football"], r["ATS"], r["ML"]
        if not a.get("n"):
            ats = "— | — | — | — | —"
        else:
            ats = (
                f"{a['n']} | {a['covers']}-{a['losses']}-{a['pushes']} | {f(a['cover_rate'], pct=True)} "
                f"[{f(a['cover_ci'][0], pct=True)}, {f(a['cover_ci'][1], pct=True)}] | {f(a['mean_resid'], 2)} ({f(a['resid_p'], sign=False)}) | "
                f"{f(a['roi_assumed_110'], pct=True)}"
            )
        ml = (
            f"{m.get('n', 0)} | {f(m.get('mean_resid_win_minus_implied'))} ({f(m.get('resid_p'), sign=False)}) | "
            f"{f(m.get('roi_provider_odds'), pct=True)} [{f((m.get('roi_ci_boot') or [None])[0], pct=True)}, {f((m.get('roi_ci_boot') or [None, None])[1], pct=True)}]"
        )
        L.append(f"| {sid} | {fb['n']} | {f(fb['win_rate'], pct=True)} | {f(fb['lift_mean'])} | {ats} | {ml} |")
    L += [
        "",
        "## 5. Total hypotheses",
        "",
        "| Id | n | Football lift (pts) | Market n | O/U hit (dir) | Mean resid (p) | ROI@−110 |",
        "|---|---|---|---|---|---|---|",
    ]
    for sid, r in rep["results"].items():
        if "TOTAL" not in r:
            continue
        fb, t = r["football"], r["TOTAL"]
        L.append(
            f"| {sid} | {fb['n']} | {f(fb['lift_mean'], 2)} | {t.get('n')} | {f(t.get('cover_rate'), pct=True)} | {f(t.get('mean_resid'), 2)} "
            f"({f(t.get('resid_p'), sign=False)}) | {f(t.get('roi_assumed_110'), pct=True)} |"
        )
    L += ["", "## 6. CONTROL tiers vs spread and moneyline", ""]
    ct = rep["deep_dive"]["control_tiers"]
    L += [
        "| Tier | n | W | Win | ATS W-L-P | Cover [95%] | Mean / median resid | p | ROI@−110 | Mean spread (side) | vs opener 2021+ (n, cover, ROI) | ML n | Win − implied | ML ROI [95%] |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for k, v in ct.items():
        if not isinstance(v, dict) or "ATS" not in v:
            continue
        a, fb, m = v["ATS"], v["football"], v["ML"]
        op = a.get("vs_opening_2021plus") or {}
        L.append(
            f"| {k} | {fb['n']} | {fb['wins']} | {f(fb['win_rate'], pct=True)} | {a['covers']}-{a['losses']}-{a['pushes']} | "
            f"{f(a['cover_rate'], pct=True)} [{f(a['cover_ci'][0], pct=True)}, {f(a['cover_ci'][1], pct=True)}] | "
            f"{f(a['mean_resid'], 2)} / {f(a['median_resid'], 1)} | {f(a['resid_p'], sign=False)} | {f(a['roi_assumed_110'], pct=True)} | "
            f"{f(a['mean_spread_s'], 1)} | {op.get('n', '—')}, {f(op.get('cover_rate'), pct=True)}, {f(op.get('roi_assumed_110'), pct=True)} | "
            f"{m.get('n')} | {f(m.get('mean_resid_win_minus_implied'))} | {f(m.get('roi_provider_odds'), pct=True)} "
            f"[{f((m.get('roi_ci_boot') or [None])[0], pct=True)}, {f((m.get('roi_ci_boot') or [None, None])[1], pct=True)}] |"
        )
    d = ct.get("moderate_minus_strong_mean_resid")
    if d:
        L += [
            "",
            f"MODERATE − STRONG mean ATS residual: {f(d['estimate'], 2)} pts, bootstrap 95% [{f(d['ci_boot'][0], 2)}, {f(d['ci_boot'][1], 2)}].",
        ]
    L += ["", "### 6a. CONTROL tiers by season (ATS: n, cover, mean residual)", ""]
    seasons = sorted(
        {s for v in ct.values() if isinstance(v, dict) and "ATS" in v for s in v["ATS"].get("by_season", {})}
    )
    L += ["| Tier | " + " | ".join(seasons) + " |", "|---|" + "---|" * len(seasons)]
    for k, v in ct.items():
        if not isinstance(v, dict) or "ATS" not in v:
            continue
        bs = v["ATS"]["by_season"]
        L.append(
            f"| {k} | "
            + " | ".join(
                f"{bs[s]['n']}, {100 * bs[s]['cover_rate']:.0f}%, {bs[s]['mean_resid']:+.1f}" if s in bs else "—"
                for s in seasons
            )
            + " |"
        )
    L += ["", "### 6b. CONTROL tiers by closing-spread bucket (side perspective: n, cover)", ""]
    for k, v in ct.items():
        if isinstance(v, dict) and "ATS" in v:
            L.append(
                f"* {k}: "
                + "; ".join(
                    f"{b}: {x['n']}, {f(x['cover_rate'], pct=True)}" for b, x in v["ATS"]["by_spread_bucket"].items()
                )
            )
    L += ["", "## 7. Market disagreement regimes", ""]
    md = rep["deep_dive"]["market_disagreement"]
    L += [
        "| Football | Market regime | n | Win | ATS cover [95%] | Mean resid (p) | ROI@−110 | ML n | Implied | ML win | ML ROI |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for tier in ("CONTROL_STRONG", "CONTROL_MODERATE"):
        for reg, x in md[tier].items():
            a, m = x["ATS"], x["ML"]
            L.append(
                f"| {tier} | {reg} | {x['n']} | {f(x['win_rate'], pct=True)} | {f(a.get('cover_rate'), pct=True)} [{f(a['cover_ci'][0], pct=True)}, {f(a['cover_ci'][1], pct=True)}] | "
                f"{f(a.get('mean_resid'), 2)} ({f(a.get('resid_p'), sign=False)}) | {f(a.get('roi_assumed_110'), pct=True)} | {m.get('n')} | "
                f"{f(m.get('mean_novig_implied'), pct=True)} | {f(m.get('win_rate'), pct=True)} | {f(m.get('roi_provider_odds'), pct=True)} |"
            )
    bd = md.get("baseline_vs_market_favourite_disagree")
    if bd:
        L += [
            "",
            f"Football scoring baseline and closing spread disagree on the favourite in {bd['n']} games: the football side won "
            f"{f(bd['football_side_win_rate'], pct=True)} (market favourite {f(bd['market_favourite_win_rate'], pct=True)}); football side ATS cover "
            f"{f(bd['football_side_ATS']['cover_rate'], pct=True)} (mean resid {f(bd['football_side_ATS']['mean_resid'], 2)}, p {f(bd['football_side_ATS']['resid_p'], sign=False)}). "
            f"2021+ (n {bd['n_2021plus']}): football side implied {f(bd['football_side_mean_novig_implied_2021plus'], pct=True)}, won {f(bd['football_side_win_rate_2021plus'], pct=True)}.",
        ]
    L += ["", "CONTROL side win rate by moneyline no-vig implied bucket (2021+):", ""]
    for b, x in rep["deep_dive"]["control_calibration_vs_ml"]["control_side"].items():
        L.append(f"* {b}: n {x['n']}, implied {f(x['mean_implied'], pct=True)}, won {f(x['win_rate'], pct=True)}")
    L += ["", "## 8. Walk-forward (pre-registered models)", ""]
    for k, v in rep["walk_forward"].items():
        a = v["aggregate"]
        L.append(
            f"* **{k}**: {json.dumps({kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in a.items() if not isinstance(vv, dict)})}"
        )
        if "picks_abs_pred_ge_threshold" in a:
            p = a["picks_abs_pred_ge_threshold"]
            L.append(
                f"  * picks: n {p.get('n')}, cover {f(p.get('cover_rate'), pct=True)}, ROI@−110 {f(p.get('roi_assumed_110'), pct=True)}"
            )
        L.append(
            "  * folds: "
            + "; ".join(
                f"{x['test_season']}: "
                + (
                    f"r {x['oos_corr']:+.3f}"
                    if "oos_corr" in x
                    else f"Δlogloss {x['logloss_market_plus_football'] - x['logloss_market_raw']:+.4f}"
                )
                for x in v["folds"]
            )
        )
    L += [
        "",
        "## 9. Stage A screen (block A 2014–2019 only; exploration, not evidence)",
        "",
        f"{scr['associations_screened']} associations ({scr['market_associations']} market); market q<0.05: {scr['market_q_lt_0_05']}; q<0.10: {scr['market_q_lt_0_10']}.",
        "",
        "| Scope | Feature | Target | n | β/SD | z | q (whole screen) | seasons same sign |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in scr["top"]["market"][:25]:
        L.append(
            f"| {e['scope']} | {e['feature']} | {e['target']} | {e['n']} | {e['beta_per_sd']:+.3f} | {e['z']:+.2f} | {e['q_bh_whole_screen']:.3f} | "
            f"{e.get('seasons_same_sign', '—')}/{e.get('seasons', '—')} |"
        )
    k26 = json.loads((BASE / "control_2026_kalshi_implied_spread.json").read_text())
    L += [
        "",
        "## 10. 2026 CONTROL vs Kalshi spread ladder (descriptive; last catalog snapshot before kickoff)",
        "",
        "| Tier | Game | Minutes before kickoff | Implied control margin (ladder 50%) | Actual control margin | Margin − implied |",
        "|---|---|---|---|---|---|",
    ]
    for x in k26:
        if x.get("implied_control_margin") is None:
            continue
        am = (x.get("settlement") or {}).get("control_margin")
        if "MODERATE" in x["tier"]:
            L.append(
                f"| {x['tier']} | {x['game']} | {x['snapshot_min_before']} | {x['implied_control_margin']:+.1f} | {am:+.0f} | {am - x['implied_control_margin']:+.1f} |"
            )
    strong = [
        x
        for x in k26
        if x.get("implied_control_margin") is not None and "STRONG" in x["tier"] and x["snapshot_min_before"] <= 1440
    ]
    res = [(x["settlement"]["control_margin"] - x["implied_control_margin"]) for x in strong]
    if res:
        L.append(
            f"| STRONG (all) | {len(res)} games | ≤ 1440 | — | — | {sum(r > 0 for r in res)} above / {sum(r < 0 for r in res)} below, mean {sum(res) / len(res):+.1f} |"
        )
    miss = {}
    for x in k26:
        if x.get("status"):
            miss[x["status"]] = miss.get(x["status"], 0) + 1
    L.append(f"\nNot priced: {miss} (catalog history starts 2026-09-18).")
    OUT.write_text("\n".join(L) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
