# ruff: noqa: E501  -- markdown table rows are long f-strings by nature
"""Render docs/ARCHETYPE_HISTORICAL_TABLES.md from archetype_historical_report.json. GENERATED tables only."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ORDER = (
    "HOME_CONTROL",
    "AWAY_CONTROL",
    "FAVORITE_PULLS_AWAY",
    "UNDERDOG_HANGS_AROUND",
    "COMPETITIVE_SHOOTOUT",
    "COMPETITIVE_GRIND",
    "COMPETITIVE_TOSSUP",
    "PACE_DRIVEN_OVER",
    "DEFENSIVE_SUPPRESSION",
    "EXPLOSIVE_UPSET",
    "TURNOVER_DISRUPTION",
)


def pct(x: float | None, places: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{places}f}%"


def ci(c: list[float] | None) -> str:
    return "—" if not c else f"{100 * c[0]:.0f}–{100 * c[1]:.0f}%"


def rt(block: dict[str, Any] | None) -> str:
    if not block or not block.get("n"):
        return "—"
    return f"{pct(block['rate'])} ({block['hits']}/{block['n']}; {ci(block['ci95'])})"


def num(x: Any) -> str:
    return "—" if x is None else str(x)


def coverage_table(rep: dict[str, Any]) -> list[str]:
    out = [
        "| Season | Scheduled FBS-involved | Completed | Box both teams | Success-rate play data | Drive data | "
        "Explosive counts | Identity-resolved targets | Eligible (prior history) | Excluded | Exclusion reasons | "
        "Leakage checks passed |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for season, c in sorted(rep["coverage"].items()):
        reasons = ", ".join(f"{k} {v}" for k, v in c["exclusion_reasons"].items())
        out.append(
            f"| {season} | {c['scheduled_fbs_involved']} | {c['completed']} | {c['with_box_score_both_teams']} | "
            f"{c['with_success_rate_play_data_both_teams']} | {c['with_drive_data_both_teams']} | "
            f"{c['with_explosive_play_counts_both_teams']} | {c['identity_resolved_targets']} | "
            f"{c['eligible_with_prior_history']} | {c['excluded']} | {reasons} | "
            f"{c['leakage_checks_passed']}/{c['identity_resolved_targets']} |"
        )
    out += ["", "File fingerprints (SHA-256, first 16):", ""]
    for season, c in sorted(rep["coverage"].items()):
        digests = ", ".join(f"`{k}` {v[:16]}" for k, v in sorted(c["file_sha256"].items()))
        out.append(f"* {season}: {digests}; rows fingerprint {c['rows_fingerprint'][:16]}")
    return out


def taxonomy(block: dict[str, Any]) -> list[str]:
    tq = block["taxonomy_quality"]
    out = [f"Eligible games: {tq['n_games']}", ""]
    out += ["| Coverage | production labels only | + environment descriptors |", "|---|---|---|"]
    for k in ("at_least_one", "exactly_one", "multiple", "none_ambiguous"):
        out.append(f"| {k} | {rt(tq['production_labels_only'][k])} | {rt(tq['with_environment_descriptors'][k])} |")
    out += [
        "",
        "| Realized label | Base rate (95% CI) | "
        + " | ".join(sorted(next(iter(tq["base_rates"].values()))["by_season"]))
        + " | season χ² p |",
        "|---|---|" + "---|" * (len(next(iter(tq["base_rates"].values()))["by_season"]) + 1),
    ]
    for label, b in tq["base_rates"].items():
        if label == "AMBIGUOUS":
            continue
        seasons = " | ".join(pct(v["rate"]) for _, v in sorted(b["by_season"].items()))
        out.append(f"| {label} | {rt(b)} | {seasons} | {num(b['season_homogeneity']['p'])} |")
    out.append(f"| AMBIGUOUS | {rt(tq['base_rates']['AMBIGUOUS'])} | | |")
    out += [
        "",
        "Realized primary (exclusive) distribution: "
        + ", ".join(f"{k} {v}" for k, v in tq["primary_distribution"].items()),
    ]
    out += [
        "",
        "Largest pairwise overlaps (Jaccard):",
        "",
        "| Pair | Both | Jaccard | P(B given A) | P(A given B) |",
        "|---|---|---|---|---|",
    ]
    pairs = sorted(tq["pairwise_overlap"].items(), key=lambda kv: -kv[1]["jaccard"])[:12]
    for k, v in pairs:
        out.append(f"| {k} | {v['both']} | {v['jaccard']} | {v['p_b_given_a']} | {v['p_a_given_b']} |")
    s = tq["sensitivity_baseline_lead"]
    out += [
        "",
        f"Sensitivity (lead = scoring-baseline margin sign): AMBIGUOUS {rt(s['ambiguous'])}; "
        f"lead agrees with the efficiency reference lead in {rt(s['lead_agreement_with_reference'])}.",
        f"Explosive-play data coverage: {rt(tq['explosive_data_coverage'])}.",
    ]
    return out


def identification(block: dict[str, Any]) -> list[str]:
    ident = block["identification"]
    out = [
        "| Archetype | Generated (any role) | Base rate | LABEL_MATCH PPV (95% CI) | Lift (bootstrap 95%) | Recall any / PRIMARY | "
        "Specificity | DEFINITION_CHECK | PRODUCTION_DESCRIBED | Stability |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in ORDER:
        b = ident[a]
        lift = "—" if b["lift"] is None else f"{b['lift']}"
        if b.get("lift_ci95_bootstrap"):
            lift += f" ({b['lift_ci95_bootstrap'][0]}–{b['lift_ci95_bootstrap'][1]})"
        out.append(
            f"| {a} | {b['generated']} | {pct(b['base_rate'])} | {rt(b['label_match'])} | {lift} | "
            f"{pct(b['recall_any_role']['rate'])} / {pct(b['recall_primary']['rate'])} | "
            f"{pct(b['specificity_any_role']['rate'])} | {rt(b['definition_check'])} | {rt(b['production_described'])} | "
            f"{b['stability']} |"
        )
    out += [
        "",
        "By role (LABEL_MATCH PPV, n):",
        "",
        "| Archetype | PRIMARY | SECONDARY | ALTERNATE | DANGER |",
        "|---|---|---|---|---|",
    ]
    for a in ORDER:
        r = ident[a]["by_role"]
        out.append(
            "| "
            + a
            + " | "
            + " | ".join(
                f"{pct(r[x]['label_match']['rate'])} (n {r[x]['generated']})"
                for x in ("PRIMARY", "SECONDARY", "ALTERNATE", "DANGER")
            )
            + " |"
        )
    out += [
        "",
        "By evidence score (LABEL_MATCH PPV, n):",
        "",
        "| Archetype | ≤1 | 2 | 3 | ≥4 |",
        "|---|---|---|---|---|",
    ]
    for a in ORDER:
        r = ident[a]["by_evidence_bucket"]
        out.append(
            "| "
            + a
            + " | "
            + " | ".join((f"{pct(r[k]['rate'])} (n {r[k]['n']})" if k in r else "—") for k in ("<=1", "2", "3", ">=4"))
            + " |"
        )
    out += [
        "",
        "By reconstructed confidence tier (LABEL_MATCH PPV, n):",
        "",
        "| Archetype | HIGH | MEDIUM | LOW |",
        "|---|---|---|---|",
    ]
    for a in ORDER:
        r = ident[a]["by_confidence"]
        out.append(
            "| "
            + a
            + " | "
            + " | ".join((f"{pct(r[k]['rate'])} (n {r[k]['n']})" if k in r else "—") for k in ("HIGH", "MEDIUM", "LOW"))
            + " |"
        )
    seasons = sorted(next(iter(ident.values()))["by_season"]) if ORDER[0] in ident else []
    out += [
        "",
        "By season (PPV / base / lift, n):",
        "",
        "| Archetype | " + " | ".join(seasons) + " | PPV χ² p |",
        "|---|" + "---|" * (len(seasons) + 1),
    ]
    for a in ORDER:
        r = ident[a]["by_season"]
        cells = []
        for s in seasons:
            v = r[s]
            cells.append(
                f"{pct(v['label_match']['rate'], 0)} / {pct(v['base_rate'], 0)} / {num(v['lift'])} (n {v['generated']})"
            )
        out.append(f"| {a} | " + " | ".join(cells) + f" | {num(ident[a]['ppv_season_homogeneity']['p'])} |")
    sc = block["overall_scorecard"]
    out += ["", "Game-level scorecard (scripted games):", ""]
    for k, v in sc.items():
        out.append(
            f"* {k}: PRIMARY {rt(v['primary'])}; PRIMARY or SECONDARY {rt(v['primary_or_secondary'])}; any {rt(v['any_script'])}"
        )
    st = ident["_status"]
    out += ["", f"Script status counts: {st['status_counts']}"]
    return out


def confusion(block: dict[str, Any]) -> list[str]:
    out = []
    for a, v in block["confusion"].items():
        flag = f" — {v['sample_flag']}" if v["sample_flag"] else ""
        out.append(f"**PRIMARY {a}** (n {v['n']}{flag})")
        out.append("")
        out.append("* realized primary: " + ", ".join(f"{k} {pct(x)}" for k, x in v["realized_primary"].items()))
        out.append("* label incidence: " + ", ".join(f"{k} {pct(x)}" for k, x in v["label_incidence"].items()))
        if "supported_side_margin_bucket" in v:
            out.append(
                "* supported side: " + ", ".join(f"{k} {pct(x)}" for k, x in v["supported_side_margin_bucket"].items())
            )
        out.append("")
    return out


def no_script(block: dict[str, Any]) -> list[str]:
    ns = block["no_script"]
    out = [f"NO_SCRIPT rate: {rt(ns['rate'])}", ""]
    out.append("By season: " + ", ".join(f"{k} {rt(v)}" for k, v in ns["by_season"].items()))
    out.append("")
    out.append(
        "By confidence tier (share abstaining): " + ", ".join(f"{k} {rt(v)}" for k, v in ns["by_confidence"].items())
    )
    out += ["", "| | NO_SCRIPT | Scripted |", "|---|---|---|"]
    a, b = ns["no_script"], ns["scripted"]
    for key, label in (("n", "games"),):
        out.append(f"| {label} | {a[key]} | {b[key]} |")
    out.append(
        f"| realized-primary entropy (bits) | {a['concentration'].get('entropy_bits')} | {b['concentration'].get('entropy_bits')} |"
    )
    out.append(
        f"| largest realized-primary share | {pct(a['concentration'].get('max_share'))} | {pct(b['concentration'].get('max_share'))} |"
    )
    out.append(f"| AMBIGUOUS | {rt(a['ambiguous'])} | {rt(b['ambiguous'])} |")
    out.append(f"| one-score games | {rt(a['one_score'])} | {rt(b['one_score'])} |")
    out.append(f"| reference favourite won | {rt(a['reference_favorite_won'])} | {rt(b['reference_favorite_won'])} |")
    out.append(
        f"| abs margin median (p10–p90) | {a['abs_margin'].get('median')} ({a['abs_margin'].get('p10')}–{a['abs_margin'].get('p90')}) | {b['abs_margin'].get('median')} ({b['abs_margin'].get('p10')}–{b['abs_margin'].get('p90')}) |"
    )
    out.append(f"| abs margin SD | {a['abs_margin'].get('sd')} | {b['abs_margin'].get('sd')} |")
    out.append(
        f"| total median (SD) | {a['total_points'].get('median')} ({a['total_points'].get('sd')}) | {b['total_points'].get('median')} ({b['total_points'].get('sd')}) |"
    )
    out += ["", "Realized primary, NO_SCRIPT: " + ", ".join(f"{k} {v}" for k, v in a["realized_primary"].items())]
    out += ["", "Realized primary, scripted: " + ", ".join(f"{k} {v}" for k, v in b["realized_primary"].items())]
    out += ["", "Within confidence tier (entropy bits / AMBIGUOUS):", ""]
    for t, v in ns["within_tier"].items():
        out.append(
            f"* {t}: NO_SCRIPT n {v['no_script']['n']}, {v['no_script']['concentration'].get('entropy_bits')} bits, "
            f"AMBIGUOUS {pct((v['no_script']['ambiguous'] or {}).get('rate'))}; scripted n {v['scripted']['n']}, "
            f"{v['scripted']['concentration'].get('entropy_bits')} bits, AMBIGUOUS {pct((v['scripted']['ambiguous'] or {}).get('rate'))}"
        )
    br = ns["best_rejected_candidate"]
    out += [
        "",
        f"Best rejected candidate in NO_SCRIPT games ({br['games_with_a_rejected_candidate']} games): "
        + ", ".join(f"{k} {rt(v)}" for k, v in br["by_archetype"].items()),
    ]
    return out


def ablation(block: dict[str, Any]) -> list[str]:
    out = []
    for a, rows in block["ablation"].items():
        out.append(f"**{a}**")
        out.append("")
        if rows.get("status") == "DATA_UNAVAILABLE":
            out.append(f"DATA_UNAVAILABLE: {rows['reason']} (candidates generated: {rows['candidates_generated']})")
            out.append("")
            continue
        out += ["| Condition | LABEL_MATCH rate (n; 95% CI) | Lift vs base |", "|---|---|---|"]
        for k, v in rows.items():
            if k == "components":
                continue
            out.append(f"| {k} | {rt(v)} | {num(v.get('lift_vs_base'))} |")
        if "components" in rows:
            comp = rows["components"]
            out.append("")
            out.append(
                "Components: "
                + "; ".join(
                    f"{k}: all games {rt(v['all_games'])}, published {rt(v['published_any_role'])}"
                    for k, v in comp.items()
                )
            )
        out.append("")
    return out


def margins(block: dict[str, Any]) -> list[str]:
    out = [
        "| Archetype | Set | n | Win rate | Mean | p10 | p25 | Median | p75 | p90 | ≥1 | ≥7 | ≥14 | ≥21 | Band | Band coverage |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in ORDER:
        for key in ("pregame_primary", "pregame_any_role", "realized_label"):
            v = block["margins"][a][key]
            if not v.get("n"):
                out.append(f"| {a} | {key} | 0 | | | | | | | | | | | | | |")
                continue
            out.append(
                f"| {a} | {key} | {v['n']} | {pct(v['win_rate']['rate'])} | {v['mean']} | {v['p10']} | {v['p25']} | "
                f"{v['median']} | {v['p75']} | {v['p90']} | {pct(v['p_margin_ge_1'])} | {pct(v['p_margin_ge_7'])} | "
                f"{pct(v['p_margin_ge_14'])} | {pct(v['p_margin_ge_21'])} | {v.get('band', '—')} | {rt(v.get('band_coverage'))} |"
            )
    out += [
        "",
        "POST-HOC EXPLORATORY (deviation D1): by required-edge strength / co-published PULLS_AWAY",
        "",
        "| Split | n | Win rate | Median | p10 | p90 | ≥7 | ≥21 | Band coverage |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for k, v in block["margins"]["EXPLORATORY_by_required_strength"].items():
        if v.get("n"):
            out.append(
                f"| {k} | {v['n']} | {pct(v['win_rate']['rate'])} | {v['median']} | {v['p10']} | {v['p90']} | "
                f"{pct(v['p_margin_ge_7'])} | {pct(v['p_margin_ge_21'])} | {rt(v.get('band_coverage'))} |"
            )
    v = block["margins"]["reference_favorite_all_games"]
    out.append(
        f"| (reference favourite, all games) | — | {v['n']} | {pct(v['win_rate']['rate'])} | {v['mean']} | {v['p10']} | "
        f"{v['p25']} | {v['median']} | {v['p75']} | {v['p90']} | {pct(v['p_margin_ge_1'])} | {pct(v['p_margin_ge_7'])} | "
        f"{pct(v['p_margin_ge_14'])} | {pct(v['p_margin_ge_21'])} | — | — |"
    )
    out += [
        "",
        "Margins are the supported side's: control/pulls-away = lead; hangs-around/upset = underdog; disruption = "
        "disruption side; competitive and environment scripts = |home margin| (band coverage against 0–8).",
    ]
    return out


def scoring(block: dict[str, Any]) -> list[str]:
    se = block["scoring_environments"]
    out = [
        "| Archetype | Role | n | Mean total pct | Extreme third | Extreme quartile | Actual − season mean | "
        "Baseline − season mean | Actual − baseline | Baseline-matched pct diff | Uncalibrated band coverage |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in ("COMPETITIVE_SHOOTOUT", "PACE_DRIVEN_OVER", "COMPETITIVE_GRIND", "DEFENSIVE_SUPPRESSION"):
        for role in ("any_role", "primary"):
            v = se[a][role]
            if not v.get("n"):
                continue
            out.append(
                f"| {a} ({se[a]['direction']}) | {role} | {v['n']} | {v['mean_total_percentile']} | {rt(v['in_extreme_third'])} | "
                f"{rt(v['in_extreme_quartile'])} | {v['mean_actual_minus_season_mean']} | {v['mean_baseline_minus_season_mean']} | "
                f"{v['mean_actual_minus_baseline']} | {num(v['baseline_matched_percentile_difference'])} | "
                f"{rt(v['uncalibrated_total_band_coverage'])} |"
            )
    ref = se["_reference"]
    out += [
        "",
        f"Uninformative reference: extreme third 33.3%, quartile 25%; corr(baseline total, actual total) = {ref['baseline_total_vs_actual_corr']}.",
    ]
    return out


def ambiguous(block: dict[str, Any]) -> list[str]:
    a = block["ambiguous_anatomy"]
    out = [f"AMBIGUOUS games: {a['n_ambiguous']} ({pct(a['share_of_games'])} of eligible)", ""]
    for k, v in a["families"].items():
        out.append(f"* {k}: {v['n']} ({pct(v['share_of_ambiguous'])})")
    out.append(
        f"* |margin| median {a['abs_margin'].get('median')}; winner turnover edge median {a['winner_turnover_edge'].get('median')}"
    )
    return out


def shapes(block: dict[str, Any]) -> list[str]:
    g = block["game_shapes"]
    return [
        f"* |margin|: median {g['abs_margin']['median']} (p10 {g['abs_margin']['p10']}, p90 {g['abs_margin']['p90']}); one-score games {rt(g['one_score_games'])}",
        f"* total points: median {g['total_points']['median']} (SD {g['total_points']['sd']}); total plays median {g['total_plays']['median']}",
        f"* winner also won efficiency: {rt(g['winner_won_efficiency'])}; won success rate: {rt(g['winner_won_success_rate'])}",
        f"* reference (efficiency) favourite won: {rt(g['reference_favorite_won'])}",
        f"* efficiency basis: {g['efficiency_basis']}",
    ]


def render(rep: dict[str, Any]) -> str:
    lines = [
        "# Historical Archetype Validation — generated tables",
        "",
        "GENERATED by `scripts/run_archetype_historical_study.py` from "
        "`data/scripting/validation/archetype_historical_report.json`. Do not edit by hand; the interpretation is in "
        "`docs/ARCHETYPE_HISTORICAL_VALIDATION.md`.",
        "",
        f"* research version {rep['research_version']}; methodology {rep['methodology_version']}; protocol "
        f"{rep['protocol']} @ {rep['protocol_commit']}",
        f"* starting main {rep['starting_main_sha']}; code at run {rep['code_sha_at_run']}; generated {rep['generated_at']}",
        f"* study window {rep['study_window']}; season roles {rep['season_roles']}",
        "",
        "## 1. Coverage",
        "",
        *coverage_table(rep),
    ]
    for name, block in [("POOLED 2021-2025", rep["pooled"])] + [(k, v) for k, v in rep["blocks"].items()]:
        lines += [
            "",
            f"## {name}",
            "",
            "### Game shapes",
            "",
            *shapes(block),
            "",
            "### A. Realized taxonomy",
            "",
            *taxonomy(block),
            "",
            "### B. Pregame identification",
            "",
            *identification(block),
            "",
            "### Confusion (PRIMARY → realized)",
            "",
            *confusion(block),
            "### NO_SCRIPT",
            "",
            *no_script(block),
            "",
            "### Evidence-gate ablation",
            "",
            *ablation(block),
            "### Margin distributions",
            "",
            *margins(block),
            "",
            "### Scoring environments",
            "",
            *scoring(block),
            "",
            "### AMBIGUOUS anatomy",
            "",
            *ambiguous(block),
        ]
    lines += [
        "",
        "## Rolling origin",
        "",
        "| Split | Archetype | History PPV (95% CI) | History lift | Test PPV | Test n | Test lift | Inside CI | Lift same side of 1 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for split, rows in rep["rolling_origin"].items():
        for a, v in rows.items():
            lines.append(
                f"| {split} | {a} | {pct(v['history_ppv'])} ({ci(v['history_ci95'])}) | {num(v['history_lift'])} | "
                f"{pct(v['test_ppv'])} | {v['test_n']} | {num(v['test_lift'])} | {v['test_ppv_inside_history_ci']} | {v['test_lift_same_side_of_1']} |"
            )
    ec = rep["exploratory_clusters"]
    lines += [
        "",
        "## Exploratory clusters (EXPLORATORY ONLY)",
        "",
        f"n {ec['n_games']}; k = {ec['k_selected_by_second_difference']}; inertia {ec['inertia_by_k']}",
        "",
    ]
    lines += ["| Cluster | n | Share | AMBIGUOUS | Centroid | Realized primary |", "|---|---|---|---|---|---|"]
    for c in ec["clusters"]:
        cen = ", ".join(f"{k} {v}" for k, v in c["centroid_raw_units"].items())
        prim = ", ".join(f"{k} {pct(v, 0)}" for k, v in list(c["realized_primary"].items())[:4])
        lines.append(f"| {c['cluster']} | {c['n']} | {pct(c['share'])} | {pct(c['ambiguous'])} | {cen} | {prim} |")
    if "source_sensitivity_2026_espn_pregame_only" in rep:
        s = rep["source_sensitivity_2026_espn_pregame_only"]
        lines += ["", "## Source sensitivity: 2026 ESPN log, pregame generation only (no 2026 outcome read)", ""]
        for key in ("espn_2026", "cfbd_2025_weeks_1_6_for_comparison"):
            v = s[key]
            lines.append(
                f"* {key}: eligible {v['eligible_games']}; status {v['status']}; PRIMARY {v['primary']}; any role {v['any_role']}; "
                f"games with an explosive finding {v['games_with_any_explosive_finding']}; EXPLOSIVE_UPSET candidates "
                f"{v['explosive_upset_candidates']}; confidence {v['confidence']}"
            )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    report = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(render(report))
