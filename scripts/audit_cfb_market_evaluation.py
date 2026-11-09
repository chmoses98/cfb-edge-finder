#!/usr/bin/env python3
"""Market-first coverage audit for RUN CFB (read-only, research-only).

    python scripts/audit_cfb_market_evaluation.py --slate-date 2026-11-07 --window early \
        --expect-games 42 --expect-contracts 3992 \
        [--evaluations analyst_evaluations.json] [--ledger-dir OUT] [--json OUT.json] [--md OUT.md] [--require-complete]

Reconciles EVERY listed contract and BOTH sides (2 x contracts) and reports how many were genuinely evaluated by an
analyst. With no ``--evaluations`` file nothing is evaluated: every priceable side is NOT_EVALUATED and the report says
"NOT A COMPLETE EVALUATION". It never produces a probability, a bet, a stake, an approval or an order.

Exit codes: 0 report written; 1 structural defect (a contract/side is missing, duplicated or miscounted);
2 ``--require-complete`` was given and the verdict is not COMPLETE. Evaluations schema: docs/RUN_CFB_MARKET_AUDIT.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.research_packet import derived_estimates as de  # noqa: E402
from cfb_edge_finder.research_packet import handicap_pricing as hp  # noqa: E402
from cfb_edge_finder.research_packet import history as hist  # noqa: E402
from cfb_edge_finder.research_packet import market_audit as ma  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--slate-date", required=True)
    ap.add_argument("--window", choices=["early", "afternoon", "evening"])
    ap.add_argument("--expect-games", type=int)
    ap.add_argument("--expect-contracts", type=int)
    ap.add_argument("--evaluations", help="analyst evaluations JSON (cfb_market_evaluations/1.0.0)")
    ap.add_argument("--derive", action="store_true", help="generate research-only historical estimates for every side")
    ap.add_argument("--derived", help="a saved cfb_derived_estimates/1.0.0 file to apply instead of generating")
    ap.add_argument("--derived-out", help="write the generated derived-estimates file here")
    ap.add_argument("--skill-json", default="data/research_packets/reference/derived_skill.json")
    ap.add_argument("--refresh-skill", action="store_true", help="recompute the walk-forward skill table")
    ap.add_argument("--handicaps", help="ANALYST game handicaps (cfb_game_handicaps/1.0.0); priced for every side")
    ap.add_argument("--seed-check", action="store_true", help="price historical-seed DRAFT handicaps as a cross-check")
    ap.add_argument("--seed-handicaps-out", help="write the seeded DRAFT handicaps (not analyst handicaps) here")
    ap.add_argument("--wagers", default="app/latest/wagers.json")
    ap.add_argument("--evidence-join", default=None, help="evidence_join report JSON (CLV by family)")
    ap.add_argument("--as-of", help="label only; recorded in the report")
    ap.add_argument("--ledger-dir", help="write one sides ledger per game here (regenerated, not committed)")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--md", dest="md_out")
    ap.add_argument("--require-complete", action="store_true")
    ap.add_argument("--verify-report", help="re-check a saved report JSON for consistency and exit")
    a = ap.parse_args(argv)

    if a.verify_report:
        problems = ma.verify_report(json.loads(Path(a.verify_report).read_text()))
        for p in problems:
            print("REPORT INCONSISTENT:", p, file=sys.stderr)
        print("report consistent" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    root = Path(a.root)
    inv = ma.load_inventory(root, a.slate_date, a.window)
    sides = ma.build_sides(inv)
    entries, load_defects = ma.load_evaluations(Path(a.evaluations) if a.evaluations else None)
    context = None
    derived_defects: list[ma.Defect] = []
    if a.derived:
        d_entries, derived_defects = ma.load_derived(Path(a.derived))
        derived_defects += ma.apply_derived(sides, d_entries)
    elif a.derive:
        games = hist.load_games(root / "data/research_packets/reference/history_games.jsonl.gz")
        skill_path = root / a.skill_json
        if skill_path.is_file() and not a.refresh_skill:
            skill = json.loads(skill_path.read_text())
        else:
            skill = de.family_skill(games)
            skill_path.parent.mkdir(parents=True, exist_ok=True)
            skill_path.write_text(json.dumps(skill, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        doc, context = de.generate(root, inv, games, skill)
        if a.derived_out:
            Path(a.derived_out).write_text(json.dumps(doc, separators=(",", ":")) + "\n", encoding="utf-8")
        derived_defects = ma.apply_derived(sides, doc["entries"])
    hctx: dict | None = None
    if (a.seed_check or a.seed_handicaps_out) and context is not None:
        games_by_key = {g["game_key"]: g for g in inv.games}
        hist_games = hist.load_games(root / "data/research_packets/reference/history_games.jsonl.gz")
        seeds, seeded_dists = {}, {}
        for info in context["games"]:
            g = games_by_key[info["game_key"]]
            pkt_rel = g.get("packet")
            pkt = json.loads((root / pkt_rel).read_text()) if pkt_rel and (root / pkt_rel).is_file() else None
            chosen, _w, base = de.reference_class_for(g, pkt, hist_games)
            qa = info.get("qb_assessment")
            if not chosen or not qa:
                continue
            seed = hp.seed_handicap(chosen, qa, float(qa["baseline_uncertainty_points"]))
            if seed:
                seeds[info["game_key"]] = seed
                seeded_dists[info["game_key"]] = hp.seeded_distributions(seed)
        if a.seed_handicaps_out:
            Path(a.seed_handicaps_out).write_text(
                json.dumps(
                    {
                        "schema_version": "cfb_game_handicap_seeds/1.0.0",
                        "authority": "DRAFT: reference-class statistics, NOT an analyst handicap. To use one, copy it to a "  # noqa: E501
                        "cfb_game_handicaps/1.0.0 file, edit it with your own view, and add analyst, handicapped_at, "
                        "evidence, thesis and opposing_case. Entries flagged draft are refused.",
                        "seeds": [
                            {"game_key": k, "draft": True, "payload": {"period_distributions": v}}
                            for k, v in sorted(seeds.items())
                        ],
                    },
                    indent=1,
                )
                + "\n",
                encoding="utf-8",
            )
        if a.seed_check:
            check_entries, info_s = hp.price_sides(sides, inv.side_codes, seeded_dists, source=hp.SRC_SEED)
            ma.apply_handicap_checks(sides, check_entries, hp.SRC_SEED)
            unp: dict[str, int] = {}
            for gaps in info_s["gaps"].values():
                for why, n in gaps.items():
                    unp[why] = unp.get(why, 0) + n
            hctx = {"seeded_games": len(seeds), "seeded_unpriced_by_reason": unp}
    analyst_handicap_defects: list[ma.Defect] = []
    if a.handicaps:
        h_dists, h_meta, h_rej = hp.parse_handicaps(json.loads(Path(a.handicaps).read_text()))
        h_entries, h_info = hp.price_sides(sides, inv.side_codes, h_dists, source=hp.SRC_ANALYST_HANDICAP)
        analyst_handicap_defects = ma.apply_evaluations(sides, h_entries)
        unp2: dict[str, int] = {}
        for gaps in h_info["gaps"].values():
            for why, n in gaps.items():
                unp2[why] = unp2.get(why, 0) + n
        hctx = (hctx or {}) | {"analyst_games": h_meta, "rejections": h_rej, "unpriced_by_reason": unp2}
    # analyst evaluations are applied LAST so an independent analyst judgment supersedes a generated estimate
    apply_defects = ma.apply_evaluations(sides, entries)
    defects = ma.verify(
        inv,
        sides,
        load_defects + derived_defects + analyst_handicap_defects + apply_defects,
        expect_games=a.expect_games,
        expect_contracts=a.expect_contracts,
    )
    evj = Path(a.evidence_join) if a.evidence_join else None
    if evj is None:
        found = sorted((root / "docs" / "reports").glob("evidence_join_*.json"))
        evj = found[-1] if found else None
    history = ma.family_history(root / a.wagers, evj)
    report = ma.build_report(
        inv, sides, defects, history=history, as_of=a.as_of, derived_context=context, handicap_context=hctx
    )
    problems = ma.verify_report(report)
    if problems:  # the audit's own arithmetic must hold before anything is written
        for p in problems:
            print("INTERNAL INCONSISTENCY:", p, file=sys.stderr)
        return 1
    md = ma.render_markdown(report)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if a.md_out:
        Path(a.md_out).write_text(md, encoding="utf-8")
    if a.ledger_dir:
        ma.write_ledger(sides, Path(a.ledger_dir))
    print(md if not (a.json_out or a.md_out) else md.split("\n## Coverage")[0])
    if report["verdict"]["structural_defects"]:
        return 1
    if a.require_complete and not report["verdict"]["evaluation_complete"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
