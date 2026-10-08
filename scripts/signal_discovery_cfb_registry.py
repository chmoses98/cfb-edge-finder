#!/usr/bin/env python3
"""Write the versioned CFB signal registry (football_signal_registry/1.0.0) from the frozen Wave 1 outputs.

Status is copied from the mechanical status rule; nothing is upgraded by hand. EDGE_CONFIRMED is never assigned.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave1"
PROTOCOL = ROOT / "docs" / "research" / "FOOTBALL_SIGNAL_DISCOVERY_WAVE1_PROTOCOL.md"

MARKET_COLUMNS = ("winner", "ATS", "total", "team_total", "1H", "1Q", "winning_margin")

LIMITATIONS = [
    "RETROSPECTIVE DISCOVERY / VALIDATION: every CFBD football season 2014-2025 had already been opened "
    "by the archetype studies",
    "CFBD lines are untimestamped closing-or-near-closing provider values; spread/total economics at an ASSUMED -110",
    "moneyline economics use one provider's untimestamped closing odds (2021-2025 only), not a Kalshi executable quote",
    "no explosive-play counts, QB identity, injuries, 1H/1Q/team-total/winning-margin lines in the historical source",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    rep = json.loads((BASE / "evaluation_report.json").read_text())
    set1 = json.loads((BASE / "hypotheses_set1.json").read_text())
    set2 = json.loads((BASE / "hypotheses_set2.json").read_text())
    specs = {h["id"]: h for h in set1["hypotheses"] + set2["hypotheses"]}
    block_b = {s["id"]: s for s in rep["set2_block_b"]["summary"]}
    code_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    signals = []
    for s in rep["summary"]:
        spec = specs[s["id"]]
        res = rep["results"].get(s["id"], {})
        status = s["status"]
        if s["id"] in block_b:  # set 2: the decisive status is the block-B one
            status = block_b[s["id"]]["status"]
        econ = {}
        if "ATS" in res and res["ATS"].get("n"):
            econ["ATS_assumed_-110"] = {
                k: res["ATS"].get(k)
                for k in ("n", "covers", "losses", "pushes", "cover_rate", "roi_assumed_110", "roi_ci_boot")
            }
        if "ML" in res and res["ML"].get("n"):
            econ["ML_provider_odds"] = {k: res["ML"].get(k) for k in ("econ_n", "roi_provider_odds", "roi_ci_boot")}
        if "TOTAL" in res and res["TOTAL"].get("n"):
            econ["TOTAL_assumed_-110"] = {
                k: res["TOTAL"].get(k) for k in ("n", "cover_rate", "roi_assumed_110", "roi_ci_boot")
            }
        if "market" in res and res["market"].get("follow_rule_abs_z_ge_1"):
            fr = res["market"]["follow_rule_abs_z_ge_1"]
            econ["follow_rule_abs_z_ge_1_assumed_-110"] = {
                k: fr.get(k) for k in ("n", "cover_rate", "roi_assumed_110", "roi_ci_boot")
            }
        signals.append(
            {
                "signal_id": s["id"],
                "name": spec["name"],
                "sport": "CFB",
                "level": "GAME",
                "family": spec["family"],
                "definition": {
                    k: spec.get(k)
                    for k in ("kind", "population", "side", "feature", "controls", "expected_sign", "direction")
                    if spec.get(k) is not None
                },
                "football_interpretation": spec["interpretation"],
                "status": status,
                "status_reasons": res.get("status_reasons"),
                "market_families": spec.get("markets", []),
                "primary_market": spec.get("primary_market"),
                "discovery_sample": {
                    "set": 2 if s["id"].startswith("CFB-DSC") else 1,
                    "discovered_on": "Stage A screen, block A 2014-2019"
                    if s["id"].startswith("CFB-DSC")
                    else "pre-registered from football logic (prompt section 38)",
                },
                "validation": {
                    "football_effect": s["football_effect"],
                    "football_q_bh": s["football_q"],
                    "market_effect": s["market_effect"],
                    "market_q_bh": s["market_q"],
                    "fdr": res.get("fdr"),
                    "block_b_only": block_b.get(s["id"]),
                },
                "economic_results": econ,
                "prospective": {
                    "status": "NOT_STARTED",
                    "note": "no Wave-1 signal is active; see RESULTS section Y for tracking candidates",
                },
                "limitations": LIMITATIONS + ([spec["reason"]] if spec.get("reason") else []),
                "protocol_sha": sha(PROTOCOL),
                "hypothesis_set_sha": rep["set2_sha256"] if s["id"].startswith("CFB-DSC") else rep["set1_sha256"],
                "evaluation_code_sha": rep["code_sha"],
                "code_sha": code_sha,
            }
        )
    out = {
        "schema": "football_signal_registry/1.0.0",
        "sport": "CFB",
        "wave": "football-signal-discovery-wave1",
        "generated_from": "data/scripting/validation/signal_discovery_wave1/evaluation_report.json",
        "status_vocabulary": [
            "DISCOVERY_ONLY",
            "FOOTBALL_VALIDATED",
            "MARKET_WATCH",
            "VALUE_WATCH",
            "APPROX_EFFICIENT",
            "OVERPRICED",
            "EDGE_CONFIRMED",
            "REJECTED",
            "DATA_UNAVAILABLE",
            "BASELINE_REFERENCE",
        ],
        "edge_confirmed_assigned": False,
        "signals": signals,
    }
    path = BASE / "football_signal_registry.json"
    path.write_text(json.dumps(out, indent=1, sort_keys=True, default=float))
    print(f"wrote {path} ({len(signals)} signals)")
    write_catalog(signals, rep)
    return 0


def _fmt(v, nd=2):
    return "—" if v is None else f"{v:+.{nd}f}"


def _stability(res: dict) -> str:
    if "market" in res and res["market"].get("blocks"):
        m = res["market"]
        b = m["blocks"]
        return (
            f"blocks A {_fmt(b['A'].get('beta_per_sd'))} / B {_fmt(b['B'].get('beta_per_sd'))}; "
            f"{round(100 * (m.get('share_seasons_expected_sign') or 0))}% seasons expected sign"
        )
    for key in ("ATS", "TOTAL"):
        st = (res.get(key) or {}).get("stability")
        if st:
            b = st["blocks"]
            return (
                f"blocks A {_fmt(b['A'].get('mean'))} / B {_fmt(b['B'].get('mean'))}; "
                f"{st.get('seasons_same_sign')}/{st.get('seasons_qualified')} seasons same sign; "
                f"top-5 teams {round(100 * st['top5_team_row_share'])}% of rows"
            )
    return "—"


def write_catalog(signals: list[dict], rep: dict) -> None:
    lines = [
        "# CFB signal catalog — Football Signal Discovery Wave 1",
        "",
        "Generated by `scripts/signal_discovery_cfb_registry.py` from the frozen evaluation report. Status is the mechanical "
        "status rule (set-2 candidates: block B only). Effects: SIDE = win-rate lift / mean ATS residual (points) or "
        "win − no-vig implied (ML); TOTAL = points; SLOPE = points per feature SD. Economics: spreads/totals at an ASSUMED "
        "−110, moneylines at provider closing odds (2021–25). RETROSPECTIVE DISCOVERY / VALIDATION throughout.",
        "",
        "| Signal | Definition | Football logic | n | Football effect (q) | Market effect (q) | Stability | Market family "
        "| Economic result | Status |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in signals:
        res = rep["results"].get(s["signal_id"], {})
        v = s["validation"]
        d = s["definition"]
        n = (res.get("football") or {}).get("n")
        econ = s["economic_results"]
        e = (
            "; ".join(
                f"{k}: n {x.get('n') or x.get('econ_n')}, ROI "
                f"{'—' if (x.get('roi_assumed_110') if 'roi_assumed_110' in x else x.get('roi_provider_odds')) is None else str(round(100 * (x.get('roi_assumed_110') if 'roi_assumed_110' in x else x.get('roi_provider_odds')), 1)) + '%'}"
                for k, x in econ.items()
            )
            or "—"
        )
        defin = ", ".join(f"{k}={json.dumps(x)}" for k, x in d.items())
        lines.append(
            f"| **{s['signal_id']}** {s['name']} | `{defin}` | {s['football_interpretation']} | {n} | "
            f"{_fmt(v['football_effect'], 3)} ({_fmt(v['football_q_bh'], 3)}) | {_fmt(v['market_effect'], 3)} "
            f"({_fmt(v['market_q_bh'], 3)}) | {_stability(res)} | {s['primary_market']} | {e} | **{s['status']}** |"
        )
    out = ROOT / "docs" / "research" / "CFB_SIGNAL_CATALOG.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    raise SystemExit(main())
