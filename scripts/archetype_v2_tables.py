# ruff: noqa: E501  -- markdown table rows are long f-strings by nature
"""Render docs/ARCHETYPE_V2_HOLDOUT_TABLES.md from archetype_v2_holdout_report.json. GENERATED tables only."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TIERS = ("HOME_CONTROL_MODERATE", "HOME_CONTROL_STRONG", "AWAY_CONTROL_MODERATE", "AWAY_CONTROL_STRONG")


def pct(x: float | None, places: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{places}f}%"


def ci(c: list[float] | None, as_pct: bool = True) -> str:
    if not c:
        return "—"
    return f"{100 * c[0]:.0f}–{100 * c[1]:.0f}%" if as_pct else f"{c[0]:.3f}–{c[1]:.3f}"


def rt(b: dict[str, Any] | None) -> str:
    if not b or not b.get("n"):
        return "—"
    return f"{pct(b['rate'])} ({b['hits']}/{b['n']}; {ci(b['ci95'])})"


def control(ev: dict[str, Any], label: str) -> list[str]:
    out = [
        f"**{label}** — base side win rate: home {pct(ev['control']['base_side_win_rate']['home']['rate'])}, away {pct(ev['control']['base_side_win_rate']['away']['rate'])}",
        "",
        "| Tier | n | Win rate (95% CI) | Lift | Mean | p10 | p25 | p50 | p75 | p90 | >0 | ≥3 | ≥7 | ≥10 | ≥14 | ≥17 | ≥21 | ≥24 | ≥28 | V1 7–24 cov | V1 17–45 cov |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in TIERS:
        e = ev["control"]["tiers"][t]
        p = e["pooled"]
        if not p["n"]:
            out.append(f"| {t} | 0 |" + " |" * 19)
            continue
        out.append(
            f"| {t} | {p['n']} | {rt(p['win_rate'])} | {e.get('win_rate_lift')} | {p['mean']} | {p['p10']} | {p['p25']} | {p['p50']} | {p['p75']} | {p['p90']} | "
            + " | ".join(pct(p[k], 0) for k in ("p_margin_gt_0", "p_margin_ge_3", "p_margin_ge_7", "p_margin_ge_10", "p_margin_ge_14", "p_margin_ge_17", "p_margin_ge_21", "p_margin_ge_24", "p_margin_ge_28"))
            + f" | {pct(e['v1_control_band_7_24_coverage']['rate'])} | {pct(e['v1_pulls_away_band_17_45_coverage']['rate'])} |"
        )
    seasons = sorted(ev["control"]["tiers"][TIERS[0]]["by_season"])
    out += ["", "By season (win rate / base / median, n):", "", "| Tier | " + " | ".join(seasons) + " | χ² p |", "|---|" + "---|" * (len(seasons) + 1)]
    for t in TIERS:
        e = ev["control"]["tiers"][t]
        cells = []
        for s in seasons:
            v = e["by_season"][s]
            cells.append("—" if not v["n"] else f"{pct(v['win_rate'], 0)} / {pct(v['base_side_win_rate'], 0)} / {v['median_margin']} (n {v['n']})")
        out.append(f"| {t} | " + " | ".join(cells) + f" | {e['win_rate_season_homogeneity']['p']} |")
    out += ["", "Ordering (STRONG − MODERATE):", ""]
    for side, o in ev["control"]["ordering"].items():
        out.append(
            f"* {side}: win rate {o['strong_minus_moderate_win_rate']} (95% CI {o['win_rate_difference_ci95']}); median {o['strong_minus_moderate_median']}; "
            f"seasons STRONG higher {o['season_ordering']['strong_higher_seasons']} of evaluable {o['season_ordering']['evaluable_seasons']}"
        )
    return out


def intervals(rep: dict[str, Any]) -> list[str]:
    ev = rep["evaluation"]
    out = [
        "| Tier | Frozen central-50 (dev p25–p75) | Frozen central-80 (dev p10–p90) | Dev in-sample 50 / 80 | Holdout 50 coverage | Holdout 80 coverage | Calibrated |",
        "|---|---|---|---|---|---|---|",
    ]
    dev = rep["development_reference"]["evaluation"]["control"]["tiers"]
    crit = rep["criteria"]["CONTROL_INTERVALS"]["tiers"]
    for t in TIERS:
        iv = rep["frozen_control_intervals"][t]
        cov = ev["control"]["tiers"][t].get("interval_coverage") or {}
        dcov = dev[t].get("interval_coverage") or {}
        out.append(
            f"| {t} | {iv['central_50']} | {iv['central_80']} | {pct((dcov.get('central_50') or {}).get('rate'))} / {pct((dcov.get('central_80') or {}).get('rate'))} | "
            f"{rt(cov.get('central_50'))} | {rt(cov.get('central_80'))} | {crit[t].get('calibrated')} |"
        )
    seasons = sorted(ev["control"]["tiers"][TIERS[0]]["by_season"])
    out += ["", "Holdout coverage by season (50 / 80):", "", "| Tier | " + " | ".join(seasons) + " |", "|---|" + "---|" * len(seasons)]
    for t in TIERS:
        cells = []
        for s in seasons:
            c = ev["control"]["tiers"][t]["by_season"][s].get("coverage")
            cells.append("—" if not c else f"{pct(c['central_50'], 0)} / {pct(c['central_80'], 0)}")
        out.append(f"| {t} | " + " | ".join(cells) + " |")
    return out


def closeness(ev: dict[str, Any], label: str) -> list[str]:
    c = ev["closeness"]
    u = c["unconditional"]
    out = [
        f"**{label}** — unconditional: ≤3 {pct(u['le_3'])}, ≤7 {pct(u['le_7'])}, ≤8 {pct(u['le_8'])}, median |margin| {u['median_abs_margin']}",
        "",
        "| Claim | n | ≤8 (95% CI) | ≤7 | ≤3 | Median abs margin | Lift vs base |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, v in c["variants"].items():
        if not v["n"]:
            continue
        out.append(f"| {k} | {v['n']} | {rt(v['one_score_le_8'])} | {pct(v['le_7']['rate'])} | {pct(v['le_3']['rate'])} | {v['median_abs_margin']} | {v['lift_vs_base']} |")
    seasons = sorted(c["variants"]["CLOSENESS"]["by_season"])
    out += ["", "| Claim | " + " | ".join(seasons) + " |", "|---|" + "---|" * len(seasons)]
    for k in ("CLOSENESS", "CLOSENESS_EVEN", "CLOSENESS_NARROW"):
        v = c["variants"][k]
        out.append(f"| {k} lift (n) | " + " | ".join(f"{v['by_season'][s]['lift']} ({v['by_season'][s]['n']})" for s in seasons) + " |")
    return out


def environments(ev: dict[str, Any], label: str) -> list[str]:
    e = ev["environments"]
    out = [
        f"**{label}**",
        "",
        "| Claim | n | Mean total pct (95% CI) | Extreme third (lift) | Extreme quartile | Actual − baseline | Baseline-matched diff (95% CI) | Mean plays pct (95% CI) | Realized label (base, lift) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for k, v in e.items():
        if k.startswith("_") or not v.get("n"):
            continue
        lab = v.get("realized_label")
        lab_s = "—" if not lab else f"{rt(lab)} (base {pct(lab['base'])}, lift {lab['lift']})"
        bm = v["baseline_matched"]
        out.append(
            f"| {k} ({v['direction']}) | {v['n']} | {v['mean_total_percentile']} ({ci(v['mean_total_percentile_ci95'], False)}) | {rt(v['extreme_third'])} ({v['extreme_third_lift']}) | "
            f"{rt(v['extreme_quartile'])} | {v['mean_actual_minus_baseline']} | {bm['difference']} ({ci(bm['ci95'], False)}) | "
            f"{v['mean_total_plays_percentile']} ({ci(v['mean_total_plays_percentile_ci95'], False)}) | {lab_s} |"
        )
    seasons = sorted(e["ELEVATED_SCORING_ENVIRONMENT"]["by_season"]) if e["ELEVATED_SCORING_ENVIRONMENT"].get("n") else []
    out += ["", "Mean total percentile by season (plays percentile for PACE), n:", "", "| Claim | " + " | ".join(seasons) + " |", "|---|" + "---|" * len(seasons)]
    for k in ("ELEVATED_SCORING_ENVIRONMENT", "SUPPRESSED_SCORING_ENVIRONMENT", "PACE_HIGH", "PACE_LOW", "DEFENSIVE_SUPPRESSION"):
        v = e[k]
        if not v.get("n"):
            continue
        key = "mean_plays_percentile" if k.startswith("PACE") else "mean_total_percentile"
        out.append(f"| {k} | " + " | ".join(f"{v['by_season'][s][key]} ({v['by_season'][s]['n']})" for s in seasons) + " |")
    out.append(f"\nMixed scoring-environment games (no claim): {e['_mixed_scoring_environment_games']}; realized-label bases {e['_realized_label_base']}")
    return out


def disruption(ev: dict[str, Any], label: str) -> list[str]:
    d = ev["disruption"]
    out = [
        f"**{label}** — per game-side base: TD label {pct(d['base']['td_label_per_side'])}, mechanism {pct(d['base']['mechanism_per_side'])}",
        "",
        "| Claim | n | Win rate (95% CI) | Expected win (side mix) | TD label (lift) | Mechanism (sacks+TFL edge) | Median margin | p25–p75 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for k, v in d.items():
        if not isinstance(v, dict) or "n" not in v or not v["n"]:
            continue
        m = v["margin"]
        out.append(
            f"| {k} | {v['n']} | {rt(v['win_rate'])} | {pct(v['expected_win_rate_from_side_mix'])} | {rt(v['turnover_disruption_label'])} ({v['td_label_lift']}) | "
            f"{rt(v['mechanism_sacks_tfl_edge'])} | {m['p50']} | {m['p25']}–{m['p75']} |"
        )
    vi = d.get("volatility_increment")
    if vi:
        out.append(f"\nVolatility increment: TD label {vi['td_label_difference']} (95% CI {vi['td_label_difference_ci95']}); win rate {vi['win_rate_difference']} (95% CI {vi['win_rate_difference_ci95']})")
    seasons = sorted(d["DISRUPTION_EDGE"]["by_season"])
    out += ["", "| Season | " + " | ".join(seasons) + " |", "|---|" + "---|" * len(seasons)]
    v = d["DISRUPTION_EDGE"]["by_season"]
    out.append("| TD lift (n) | " + " | ".join(f"{v[s]['td_lift']} ({v[s]['n']})" for s in seasons) + " |")
    out.append("| Win rate | " + " | ".join(pct(v[s]["win_rate"], 0) for s in seasons) + " |")
    return out


def criteria(crit: dict[str, Any]) -> list[str]:
    out = ["| Family | Passes | Detail |", "|---|---|---|"]
    for fam, ok in crit["summary"]["families_passed"].items():
        detail = {k: v for k, v in crit[fam].items() if k not in ("passes", "tiers")}
        out.append(f"| {fam} | **{ok}** | `{json.dumps(detail, sort_keys=True)[:400]}` |")
    for tier, v in crit["CONTROL"]["tiers"].items():
        out.append(f"| CONTROL / {tier} | {v['passes']} | C1 {v['C1_win_rate_lower_bound_above_base']}; seasons passing {v['C2_seasons']['passing_seasons']} of {v['C2_seasons']['evaluable_seasons']} |")
    s = crit["summary"]
    out += ["", f"**Verdict: {s['verdict']}** — non-control families passed {s['non_control_families_passed']}/5; recommendation: **{s['recommendation']}**"]
    return out


def misc(ev: dict[str, Any], label: str) -> list[str]:
    nc = ev["no_claim"]
    out = [f"**{label}**", "", "| Group | Share | n | |margin| bucket entropy (bits) | Median abs margin | One-score | Total SD |", "|---|---|---|---|---|---|---|"]
    for k, v in nc.items():
        share = v.get("share")
        out.append(
            f"| {k} | {pct((share or {}).get('rate'))} | {v['n']} | {(v.get('abs_margin_bucket_entropy') or {}).get('entropy_bits')} | "
            f"{(v.get('abs_margin') or {}).get('median')} | {rt(v.get('one_score'))} | {(v.get('total_points') or {}).get('sd')} |"
        )
    dq = ev["data_quality"]
    out += ["", "CONTROL win rate by DATA-QUALITY confidence vs OUTCOME STRENGTH:", ""]
    for key in ("by_strength", "by_confidence", "by_prior_games", "by_adjustment_stability"):
        out.append(f"* {key}: " + "; ".join(f"{k} {rt(v)} median {v['median_margin']}" for k, v in dq[key].items()))
    out.append("* by side × strength × confidence: " + "; ".join(f"{k} {pct(v['rate'], 0)} (n {v['n']})" for k, v in dq["by_side_strength_confidence"].items()))
    iw = ev["inefficient_win_exploratory"]
    out += ["", f"Inefficient-win family (EXPLORATORY): base {rt(iw['family_rate'])}; within finishing-advantage-without-efficiency-edge {rt(iw['within_finishing_without_efficiency_edge'])}; lift {iw['lift']}"]
    return out


def compare(c: dict[str, Any], label: str) -> list[str]:
    out = [f"**{label}** (n {c['games']})", ""]
    for section in ("redundancy", "coverage", "directional_accuracy", "closeness", "margin_description", "abstention", "stable_lift"):
        out.append(f"* **{section}**: `{json.dumps(c[section], sort_keys=True)[:900]}`")
    out += ["", "| V1 archetype | Generated | PPV | Lift (95% CI) |", "|---|---|---|---|"]
    for a, v in c["v1_identification_lift"].items():
        out.append(f"| {a} | {v['generated']} | {pct(v['ppv'])} | {v['lift']} ({v['lift_ci95']}) |")
    out += ["", "| Scoring claim | n | Mean total pct | Baseline-matched |", "|---|---|---|---|"]
    for side in ("v1", "v2"):
        for a, v in c["scoring_environment"][side].items():
            out.append(f"| {side}: {a} | {v['n']} | {v['mean_total_percentile']} | {v['baseline_matched']} |")
    return out


def render(rep: dict[str, Any]) -> str:
    dev = rep["development_reference"]["evaluation"]
    ev = rep["evaluation"]
    lines = [
        "# Archetype V2 — sealed holdout tables",
        "",
        "GENERATED by `scripts/run_archetype_v2_study.py score-holdout`. Interpretation: `docs/ARCHETYPE_V2_HOLDOUT_RESULTS.md`, `docs/ARCHETYPE_V1_V2_COMPARISON.md`.",
        "",
        f"* V2 {rep['v2_version']}; methodology {rep['methodology_version']}; protocol {rep['protocol']} @ {rep['protocol_commit']}",
        f"* frozen parameters sha256 {rep['frozen_parameters_sha256']} (commit {rep['frozen_parameters_commit']}); manifest commit {rep['manifest_commit']}",
        f"* base main {rep['base_main_sha']}; code at run {rep['code_sha_at_run']}; generated {rep['generated_at']}",
        f"* DEVELOPMENT {rep['development_seasons']} ({dev['n_games']} games) — SEALED HOLDOUT {rep['holdout_seasons']} ({ev['n_games']} games). Never pooled for model selection.",
        "",
        "## Coverage (holdout)",
        "",
        "| Season | Scheduled | Completed | Box both | Success-rate data | Drives | Explosive counts | Targets | Eligible | Excluded | Reasons | Leakage checks |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s, c in sorted(rep["coverage"].items()):
        reasons = ", ".join(f"{k} {v}" for k, v in c["exclusion_reasons"].items())
        lines.append(
            f"| {s} | {c['scheduled_fbs_involved']} | {c['completed']} | {c['with_box_score_both_teams']} | {c['with_success_rate_play_data_both_teams']} | "
            f"{c['with_drive_data_both_teams']} | {c['with_explosive_play_counts_both_teams']} | {c['identity_resolved_targets']} | {c['eligible_with_prior_history']} | "
            f"{c['excluded']} | {reasons} | {c['leakage_checks_passed']}/{c['identity_resolved_targets']} |"
        )
    lines += ["", "File SHA-256 (first 16):", ""]
    for s, c in sorted(rep["coverage"].items()):
        lines.append(f"* {s}: " + ", ".join(f"`{k}` {v[:16]}" for k, v in sorted(c["file_sha256"].items())) + f"; rows {c['rows_fingerprint'][:16]}")
    lines += ["", "## Pre-registered criteria (holdout)", "", *criteria(rep["criteria"])]
    lines += ["", "## CONTROL", "", *control(dev, "DEVELOPMENT 2021-2025"), "", *control(ev, "SEALED HOLDOUT 2014-2020")]
    lines += ["", "## Empirical intervals", "", *intervals(rep)]
    lines += ["", "## CLOSENESS", "", *closeness(dev, "DEVELOPMENT"), "", *closeness(ev, "SEALED HOLDOUT")]
    lines += ["", "## Scoring environment, pace, defensive suppression", "", *environments(dev, "DEVELOPMENT"), "", *environments(ev, "SEALED HOLDOUT")]
    lines += ["", "## Disruption", "", *disruption(dev, "DEVELOPMENT"), "", *disruption(ev, "SEALED HOLDOUT")]
    lines += ["", "## No-claim, data quality, exploratory", "", *misc(dev, "DEVELOPMENT"), "", *misc(ev, "SEALED HOLDOUT")]
    lines += ["", "## V1 vs V2", "", *compare(rep["development_reference"]["v1_vs_v2_in_sample"], "DEVELOPMENT (in-sample)"), "", *compare(rep["v1_vs_v2"], "SEALED HOLDOUT")]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    Path(sys.argv[2]).write_text(render(json.loads(Path(sys.argv[1]).read_text())))
