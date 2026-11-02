#!/usr/bin/env python3
"""Reconcile every executed contract in a window and attribute the losses -- or say the evidence is missing.

    python scripts/cfb_loss_attribution.py --wagers app/latest/wagers.json \
        --settlements app/latest/settlements.json --start 2026-10-22 --end 2026-11-01 \
        --json-out docs/reports/loss_attribution_2026-10-22_2026-11-01.json \
        --md-out   docs/reports/LOSS_ATTRIBUTION_DASHBOARD_2026-11-02.md \
        [--closing-quotes closes.json] [--unit-dollars 70] [--records-out reconciliation.jsonl]

``--closing-quotes`` is {ticker: {"yes_bid":..,"yes_ask":..,"captured_at":..}}, the LAST quote captured before
kickoff. Without it CLV is reported as MISSING for every contract -- never estimated.
``--unit-dollars`` is never inferred; without it, exposure is reported in dollars only.
Read-only on the ledger: writes only the report files and (append-only) ``--records-out``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.decision.exposure_aggregation import Position, aggregate_exposure  # noqa: E402
from cfb_edge_finder.postmortem.contract_reconciliation import append_records, build_reconciliation  # noqa: E402
from cfb_edge_finder.postmortem.loss_attribution import classify_loss, family_label, family_performance  # noqa: E402


def build_report(wagers, settlements, start, end, closes, unit_dollars):
    sett = {s["wager_id"]: s for s in settlements}
    window = [w for w in wagers if start <= w["placed_at"][:10] <= end]
    records = [
        build_reconciliation(w, sett.get(w["wager_id"]), close_quote=(closes or {}).get(w["kalshi_ticker"]))
        for w in window
    ]
    settled = [r for r in records if r["settlement"] and r["settlement"]["result"] in ("WON", "LOST")]
    losses = [r for r in settled if r["settlement"]["result"] == "LOST"]
    verdicts = [(r, classify_loss(r)) for r in losses]
    cause_counts = Counter(v["cause"] for _, v in verdicts)
    missing_counts = Counter(m for r in records for m in r["missing"])
    loss_missing = Counter(m for _, v in verdicts for m in v["missing"])
    stake = sum(r["execution"]["stake"] for r in settled)
    net = sum(r["settlement"]["net_pnl"] for r in settled)
    fees = sum(r["execution"]["fees_paid"] or 0 for r in settled)
    positions = [
        Position(r["ticker"], r["side"], r["execution"]["stake"], (r["execution"]["placed_at"] or "")[:10] or None)
        for r in settled
    ]
    game_dollars = defaultdict(float)
    game_net = defaultdict(float)
    for r in settled:
        g = r["ontology"]["game_key"] or "COMBO_UNDECODED"
        game_dollars[g] += r["execution"]["stake"]
        game_net[g] += r["settlement"]["net_pnl"]
    top_games = sorted(game_dollars, key=game_dollars.get, reverse=True)[:10]
    exposure = None
    if unit_dollars:
        ex = aggregate_exposure(positions, unit_dollars=unit_dollars)
        exposure = {
            "unit_dollars": unit_dollars,
            "flags": ex.flags,
            "games_over_5U_gross": sorted(k for k, g in ex.games.items() if g.gross_units > 5),
            "games_over_5U_correlated_view": sorted(k for k, g in ex.games.items() if g.max_view_units > 5),
            "days_over_15U": {d: round(u, 2) for d, u in sorted(ex.daily_gross_units.items()) if u > 15},
            "undecoded_combo_units": round(ex.undecoded_combo_units, 2),
        }
    fee_mismatch = [r["ticker"] for r in records if r["execution"]["fee_matches_schedule"] is False]
    return {
        "window": [start, end],
        "ledger_totals": {
            "settled": len(settled),
            "losses": len(losses),
            "stake": round(stake, 2),
            "net_fee_inclusive": round(net, 2),
            "fees_included_in_stake": round(fees, 2),
            "note": "net = gross - stake; fees are already inside stake and are not subtracted again",
        },
        "reconciliation": {
            "contracts": len(records),
            "economics_issues": sum(len(r["economics_issues"]) for r in records),
            "fee_recompute_mismatches": fee_mismatch,
            "missing_evidence_counts": dict(sorted(missing_counts.items())),
        },
        "loss_attribution": {
            "cause_counts": dict(cause_counts),
            "missing_evidence_on_losses": dict(sorted(loss_missing.items())),
            "per_loss": [
                {
                    "ticker": r["ticker"],
                    "side": r["side"],
                    "family": family_label(r["ticker"]),
                    "net_pnl": r["settlement"]["net_pnl"],
                    **v,
                }
                for r, v in verdicts
            ],
        },
        "family_performance": family_performance(settled),
        "largest_game_concentrations": [
            {"game": g, "stake": round(game_dollars[g], 2), "net": round(game_net[g], 2)} for g in top_games
        ],
        "exposure": exposure,
    }, records


def render_md(rep):
    t, la, rc = rep["ledger_totals"], rep["loss_attribution"], rep["reconciliation"]
    L = [
        f"# CFB loss attribution — {rep['window'][0]} to {rep['window'][1]}",
        "",
        f"**{t['settled']} settled, {t['losses']} losses, stake ${t['stake']:,.2f}, net ${t['net_fee_inclusive']:,.2f} "
        f"(fee-inclusive; ${t['fees_included_in_stake']:,.2f} fees are inside stake).**",
        "",
        "## Completed",
        "",
        f"- Reconciled {rc['contracts']} contracts; stake/fee/net identities violated: {rc['economics_issues']}; "
        f"per-order fee recompute mismatches: {len(rc['fee_recompute_mismatches'])}.",
        "- Loss causes: " + ", ".join(f"{k}={v}" for k, v in sorted(la["cause_counts"].items())),
        "",
        "## Unresolved (evidence missing — not inferred)",
        "",
    ]
    for k, v in la["missing_evidence_on_losses"].items():
        L.append(f"- `{k}` on {v} losing contracts")
    L += [
        "",
        "## Family performance (game-cluster bootstrap 95% CI on ROI)",
        "",
        "| family | bets | games | stake | net | ROI | 95% CI | excludes 0 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for fam, p in sorted(rep["family_performance"].items(), key=lambda kv: kv[1]["net"]):
        L.append(
            f"| {fam} | {p['n_bets']} | {p['n_games']} | {p['stake']:,.0f} | {p['net']:,.0f} | {p['roi']:+.1%} | "
            f"[{p['roi_ci95_game_cluster_bootstrap'][0]:+.1%}, {p['roi_ci95_game_cluster_bootstrap'][1]:+.1%}] | "
            f"{('yes' if p['ci_excludes_zero'] else 'no') if p['ci_reliable'] else 'n/a (<10 games)'} |"
        )
    L += [
        "",
        "Families whose CI includes 0 are not distinguishable from break-even/variance on this sample. "
        "Families were examined AFTER seeing the losses (worst-of-9 selection), so an interval that excludes 0 "
        "is a lead to test prospectively, not a confirmed defect; and a positive family (team totals) is "
        "not shown to be profitable by a hot sample whose interval includes 0.",
        "",
    ]
    if rep["exposure"]:
        e = rep["exposure"]
        L += [
            f"## Exposure at unit=${e['unit_dollars']:g} (flags only, no caps)",
            "",
            "The unit is a supplied parameter, never inferred; $70 is the illustrative value in docs/POSTMORTEM.md "
            "and must be confirmed by the owner before these flags are read as policy.",
            f"- Games over 5U gross: {len(e['games_over_5U_gross'])}; "
            f"over 5U correlated view: {len(e['games_over_5U_correlated_view'])}",
            f"- Days over 15U gross: {e['days_over_15U']}",
            f"- Undecoded combo units: {e['undecoded_combo_units']}",
            "",
        ]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wagers", type=Path, required=True)
    ap.add_argument("--settlements", type=Path, required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--closing-quotes", type=Path)
    ap.add_argument("--unit-dollars", type=float)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--md-out", type=Path)
    ap.add_argument("--records-out", type=Path)
    a = ap.parse_args()
    wagers = json.loads(a.wagers.read_text())["items"]
    setts = json.loads(a.settlements.read_text())["items"]
    closes = json.loads(a.closing_quotes.read_text()) if a.closing_quotes else None
    rep, records = build_report(wagers, setts, a.start, a.end, closes, a.unit_dollars)
    a.json_out.parent.mkdir(parents=True, exist_ok=True)
    a.json_out.write_text(json.dumps(rep, indent=2, sort_keys=True) + "\n")
    if a.md_out:
        a.md_out.write_text(render_md(rep))
    if a.records_out:
        print("appended", append_records(a.records_out, records), "new reconciliation records")
    print(
        json.dumps({"ledger_totals": rep["ledger_totals"], "causes": rep["loss_attribution"]["cause_counts"]}, indent=1)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
