"""Render docs/CONTROL_2026_MARKET_TABLES.md from control_market_report.json (fixed layout, written pre-reveal)."""

from __future__ import annotations

from typing import Any

TIERS = ("HOME_CONTROL_MODERATE", "HOME_CONTROL_STRONG", "AWAY_CONTROL_MODERATE", "AWAY_CONTROL_STRONG")
GROUPS = (*TIERS, "ALL_MODERATE", "ALL_STRONG", "ALL_CONTROL")
SHORT = {
    "HOME_CONTROL_MODERATE": "HOME MOD",
    "HOME_CONTROL_STRONG": "HOME STRONG",
    "AWAY_CONTROL_MODERATE": "AWAY MOD",
    "AWAY_CONTROL_STRONG": "AWAY STRONG",
    "ALL_MODERATE": "ALL MOD",
    "ALL_STRONG": "ALL STRONG",
    "ALL_CONTROL": "ALL CONTROL",
}


def f(x: Any, pct: bool = False, k: int = 3) -> str:
    if x is None:
        return "—"
    if isinstance(x, list):
        return "[" + ", ".join(f(v, pct, k) for v in x) + "]"
    if pct:
        return f"{100 * x:.1f}%"
    if isinstance(x, float):
        return f"{x:.{k}f}"
    return str(x)


def table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def scope_tables(name: str, scope: dict[str, Any]) -> str:
    g = scope["groups"]
    parts = [f"## Scope: {name}", "", f"**Overall verdict (pre-registered rule):** {scope['overall_verdict']}", ""]

    parts += ["### Coverage", ""]
    rows = []
    for k in GROUPS:
        c = g[k]["coverage"]
        rows.append(
            [
                SHORT[k],
                f(c["eligible_control_games"]),
                f(c["with_game_winner_market"]),
                f(c["with_valid_entry_quote"]),
                f(c["missing_entry_quote"]),
                f(c["with_valid_settlement"]),
                f(c["primary_economics_n"]),
                ", ".join(f"{a} {b}" for a, b in c["exclusions"].items()) or "—",
            ]
        )
    parts += [
        table(
            ["Group", "Eligible", "ML market", "Entry quote", "Missing quote", "Settled", "Econ n", "Exclusions"], rows
        ),
        "",
    ]

    parts += ["### Football performance (all settled CONTROL games, market data not required)", ""]
    rows = []
    for k in GROUPS:
        fb = g[k]["football"]
        rows.append(
            [
                SHORT[k],
                f(fb["n"]),
                f(fb["wins"]),
                f(fb["losses"]),
                f(fb["win_rate"], True),
                f(fb["wilson95"], True),
                f(fb.get("historical_development_win_rate"), True),
                f(fb.get("historical_validation_win_rate"), True),
                f(fb["median_margin"]),
                fb.get("continuation", "—"),
            ]
        )
    parts += [
        table(
            [
                "Group",
                "n",
                "W",
                "L",
                "2026 win",
                "Wilson 95%",
                "Dev 21–25",
                "Val 14–20",
                "Median margin",
                "Continuation",
            ],
            rows,
        ),
        "",
    ]

    parts += ["### Primary economics (CONTROL team YES, PRIMARY_60_180, fee-adjusted, one contract)", ""]
    rows = []
    for k in GROUPS:
        e = g[k]["economics"]
        if not e["n"]:
            rows.append([SHORT[k], "0"] + ["—"] * 14)
            continue
        boot = e["roi_on_outlay_bootstrap"]
        rows.append(
            [
                SHORT[k],
                f(e["n"]),
                f(e["wins"]),
                f(e["win_rate"], True),
                f(e["entry_price_mean"]),
                f(e["entry_price_median"]),
                f(e["total_entry_cost"], k=2),
                f(e["total_fees"], k=2),
                f(e["gross_pnl"], k=2),
                f(e["fee_adjusted_pnl"], k=2),
                f(e["roi_on_outlay"], True),
                f(boot.get("ci95"), True),
                f(e["pnl_per_contract_mean"]),
                f(e["pnl_per_contract_median"]),
                f(e["break_even_win_rate"], True),
                f(e["realized_minus_break_even"], True),
            ]
        )
    parts += [
        table(
            [
                "Group",
                "n",
                "W",
                "Win",
                "Mean entry",
                "Median entry",
                "Entry cost",
                "Fees",
                "Gross P/L",
                "Fee-adj P/L",
                "ROI",
                "ROI 95% (boot)",
                "Mean P/L",
                "Median P/L",
                "Break-even",
                "Realized − BE",
            ],
            rows,
        ),
        "",
    ]

    parts += ["### Entry-price distribution and win-rate interval", ""]
    rows = []
    for k in GROUPS:
        e = g[k]["economics"]
        if not e["n"]:
            continue
        p = e["entry_price_pctl"]
        rows.append(
            [
                SHORT[k],
                f(p["p10"]),
                f(p["p25"]),
                f(p["p50"]),
                f(p["p75"]),
                f(p["p90"]),
                f(e["win_rate_wilson95"], True),
                f(e["win_rate_bootstrap95"], True),
                f(e["roi_on_entry"], True),
            ]
        )
    parts += [
        table(
            ["Group", "p10", "p25", "p50", "p75", "p90", "Win Wilson 95%", "Win boot 95%", "ROI on entry (secondary)"],
            rows,
        ),
        "",
    ]

    parts += ["### Market scoring on identical rows (descriptive; asks are not fair probabilities)", ""]
    rows = []
    for k in GROUPS:
        s = g[k]["market_scoring"]
        rows.append(
            [
                SHORT[k],
                f(s["n"]),
                f(s["brier_entry_ask"], k=4),
                f(s["log_loss_entry_ask"], k=4),
                f(s["brier_historical_tier_rate"], k=4),
                f(s["log_loss_historical_tier_rate"], k=4),
                f(s["mid_n"]),
                f(s["brier_entry_mid"], k=4),
            ]
        )
    parts += [
        table(
            ["Group", "n", "Brier ask", "LogLoss ask", "Brier hist rate", "LogLoss hist rate", "mid n", "Brier mid"],
            rows,
        ),
        "",
        "Paired model-vs-market: not computed — V2 CONTROL carries no probability by design.",
        "",
    ]

    parts += ["### CLV (same contract, same side; CLOSING = last valid quote in (k−60, k))", ""]
    rows = []
    for k in GROUPS:
        c = g[k]["clv"]
        rows.append(
            [
                SHORT[k],
                f(c["entry_n"]),
                f(c["clv_n"]),
                f(c["missing"]),
                f(c.get("mean_clv"), k=4),
                f(c.get("median_clv"), k=4),
                f(c.get("mean_clv_bootstrap95"), k=4),
                f(c.get("favorable_pct"), True),
                f(c.get("flat_pct"), True),
                f(c.get("unfavorable_pct"), True),
                f(c.get("mean_logit_movement"), k=4),
            ]
        )
    parts += [
        table(
            [
                "Group",
                "Entry n",
                "CLV n",
                "Missing close",
                "Mean CLV",
                "Median CLV",
                "Mean 95%",
                "Fav",
                "Flat",
                "Unfav",
                "Mean logit",
            ],
            rows,
        ),
        "",
    ]

    parts += ["### Open-to-close movement of the CONTROL team's YES ask (up = toward CONTROL)", ""]
    rows = []
    for k in GROUPS:
        m = g[k]["movement"]
        for leg in ("EARLY_OPEN->PRIMARY_60_180", "PRIMARY_60_180->CLOSING", "EARLY_OPEN->CLOSING"):
            v = m[leg]
            rows.append(
                [
                    SHORT[k],
                    leg,
                    f(v["n"]),
                    f(v["mean_move"], k=4),
                    f(v["median_move"], k=4),
                    f(v["mean_abs_move"], k=4),
                    f(v["toward_control_pct"], True),
                    f(v["away_from_control_pct"], True),
                    f(v["flat_pct"], True),
                ]
            )
    parts += [
        table(["Group", "Leg", "n", "Mean move", "Median move", "Mean |move|", "Toward CONTROL", "Away", "Flat"], rows),
        "",
    ]
    rows = [
        [SHORT[k]]
        + [
            f(g[k]["movement"][cp]["n"]) + " @ " + f(g[k]["movement"][cp]["mean_price"])
            for cp in ("EARLY_OPEN", "W_180_360", "PRIMARY_60_180", "W_15_60", "CLOSING")
        ]
        for k in GROUPS
    ]
    parts += [table(["Group", "EARLY_OPEN n @ mean", "W_180_360", "PRIMARY_60_180", "W_15_60", "CLOSING"], rows), ""]

    parts += ["### Price buckets (entry ask, whole cents; EXPLORATORY, fixed buckets, empty ones kept)", ""]
    rows = []
    for k in GROUPS:
        for label, b in g[k]["price_buckets"].items():
            rows.append(
                [
                    SHORT[k],
                    label,
                    f(b["n"]),
                    f(b["wins"]),
                    f(b["win_rate"], True),
                    f(b["entry_price_mean"]),
                    f(b["roi_on_outlay"], True),
                    f(b["roi_ci95"], True),
                    f(b["fee_adjusted_pnl"], k=2),
                    f(b["clv_n"]),
                    f(b["mean_clv"], k=4),
                    b["sample_label"],
                ]
            )
    parts += [
        table(
            [
                "Group",
                "Bucket ¢",
                "n",
                "W",
                "Win",
                "Mean entry",
                "ROI",
                "ROI 95%",
                "P/L",
                "CLV n",
                "Mean CLV",
                "Sample",
            ],
            rows,
        ),
        "",
    ]

    parts += ["### Secondary contract (opponent's market NO at its own ask; descriptive sensitivity)", ""]
    rows = []
    for k in GROUPS:
        e = g[k]["economics_secondary_opponent_no"]
        c = g[k]["clv_secondary_opponent_no"]
        rows.append(
            [
                SHORT[k],
                f(e["n"]),
                f(e.get("win_rate"), True),
                f(e.get("entry_price_mean")),
                f(e.get("fee_adjusted_pnl"), k=2),
                f(e.get("roi_on_outlay"), True),
                f((e.get("roi_on_outlay_bootstrap") or {}).get("ci95"), True),
                f(c["clv_n"]),
                f(c.get("mean_clv"), k=4),
            ]
        )
    parts += [
        table(["Group", "n", "Win", "Mean entry", "Fee-adj P/L", "ROI", "ROI 95%", "CLV n", "Mean CLV"], rows),
        "",
    ]

    parts += ["### Pre-registered verdicts", ""]
    rows = [[SHORT[t], g[t]["verdict"]["verdict"], g[t]["verdict"]["basis"]] for t in TIERS]
    parts += [table(["Tier", "Verdict", "Basis"], rows), ""]

    parts += ["### Descriptive slices (EXPLORATORY)", ""]
    for k in GROUPS:
        sl = scope["slices"][k]
        rows = []
        for dim, cells in sl.items():
            for label, c in cells.items():
                if not c["eligible"]:
                    continue
                rows.append(
                    [
                        dim,
                        label,
                        f(c["eligible"]),
                        f(c["n"]),
                        f(c["wins"]),
                        f(c["win_rate"], True),
                        f(c["entry_price_mean"]),
                        f(c["roi_on_outlay"], True),
                        f(c["roi_ci95"], True),
                        f(c["fee_adjusted_pnl"], k=2),
                    ]
                )
        parts += [
            f"#### {SHORT[k]}",
            "",
            table(["Slice", "Cell", "Eligible", "Econ n", "W", "Win", "Mean entry", "ROI", "ROI 95%", "P/L"], rows),
            "",
        ]
    return "\n".join(parts)


def render_tables(report: dict[str, Any]) -> str:
    head = [
        "# CONTROL × 2026 Kalshi game-winner market — tables",
        "",
        "Generated by `scripts/run_control_market_study.py reveal` from `control_market_report.json`.",
        "Protocol: `docs/CONTROL_2026_MARKET_PROTOCOL.md` (sha256 "
        + report["protocol_sha256"][:12]
        + "…, commit "
        + report["protocol_commit"][:8]
        + "). Freeze manifest sha256 "
        + report["manifest_sha256"][:12]
        + "…. Code "
        + report["source_code_sha"][:8]
        + ".",
        "",
        "**Research only.** Retrospective football replay with prospectively captured prices, except rows labelled "
        "PROSPECTIVE_V2_CONTROL. One research contract per game; no staking. Every tier × bucket, slice and block "
        "cell is EXPLORATORY (uncorrected multiplicity). Executable asks are not fair probabilities.",
        "",
    ]
    body = []
    for name, scope in report["results"].items():
        n = scope["groups"]["ALL_CONTROL"]["coverage"]["eligible_control_games"]
        if name != "POOLED" and n == 0:
            body += [f"## Scope: {name}", "", "No eligible rows in this population (reported, not substituted).", ""]
            continue
        body.append(scope_tables(name, scope))
    sens = report.get("sensitivities") or {}
    if sens:
        body += ["## Pre-declared sensitivities (descriptive; cannot change a verdict)", ""]
        rows = []
        for name, groups in sens.items():
            for k in GROUPS:
                e = groups[k]
                rows.append(
                    [
                        name,
                        SHORT[k],
                        f(e["n"]),
                        f(e.get("win_rate"), True),
                        f(e.get("entry_price_mean")),
                        f(e.get("fee_adjusted_pnl"), k=2),
                        f(e.get("roi_on_outlay"), True),
                        f((e.get("roi_on_outlay_bootstrap") or {}).get("ci95"), True),
                    ]
                )
        body += [table(["Sensitivity", "Group", "n", "Win", "Mean entry", "Fee-adj P/L", "ROI", "ROI 95%"], rows), ""]
    return "\n".join(head + body) + "\n"
