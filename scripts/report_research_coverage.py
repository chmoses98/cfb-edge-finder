#!/usr/bin/env python3
"""Coverage report over the committed research packets: what each game actually carries, and what it does not.

    python scripts/report_research_coverage.py                    # whole index
    python scripts/report_research_coverage.py --date 2026-11-07  # one slate
    python scripts/report_research_coverage.py --game 26NOV06UNMNEV --game 26NOV06TCUARIZ --detail

Read-only. It reads the same files a connector session reads (index -> slate -> game files) and prints counts, so a
claim such as "every game has the same depth" is a number that can be checked, not a sentence.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load(rel: str | Path) -> Any:
    return json.loads((ROOT / rel).read_text())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date")
    ap.add_argument("--game", action="append")
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args(argv)
    index = load("data/research_packets/index.json")
    rows: list[dict[str, Any]] = []
    for slate in index["slates"]:
        if args.date and slate["slate_date"] != args.date:
            continue
        rows += load(slate["path"])["games"]
    if args.game:
        rows = [r for r in rows if r["game_key"] in set(args.game)]
    if not rows:
        print("no games selected")
        return 1

    flags: Counter[str] = Counter()
    sections: dict[str, Counter[str]] = {}
    families: Counter[str] = Counter()
    coverage: Counter[str] = Counter()
    contracts = 0
    no_side = both_priced = with_ref = 0
    for r in rows:
        flags.update(r["flag_codes"])
        for k, v in r["sections"].items():
            sections.setdefault(k, Counter())[v] += 1
        contracts += r["contract_count"]
        for part_path in r["files"]["markets"]:
            part = load(part_path)
            if part.get("families"):
                for fam, info in part["families"].items():
                    families[fam] += info["contracts"]
                    coverage[info["coverage"]] += info["contracts"]
            for c in part["contracts"]:
                if (
                    c["yes"]["ask"] is not None
                    and c["no"]["ask"] is not None
                    and c["yes"]["all_in"]
                    and c["no"]["all_in"]
                ):
                    both_priced += 1
                else:
                    no_side += 1
                if "ref_freq" in c:
                    with_ref += 1
    print(f"games: {len(rows)}   contracts: {contracts}")
    print(
        f"contracts with both buy sides priced and an all-in cost: {both_priced}   one side unpriced "
            f"(empty/one-sided book): {no_side}"
    )
    print(f"contracts with a historical reference-class frequency: {with_ref}")
    print("\nsections (how many games in each state):")
    for k, c in sorted(sections.items()):
        print(f"  {k:20s} {dict(c)}")
    print("\nflags (games carrying each):")
    for k, n in flags.most_common():
        print(f"  {k:36s} {n}")
    print("\nmarket coverage by contract count:", dict(coverage))
    print("families:", dict(families.most_common()))
    if args.detail:
        for r in rows:
            print(
                f"\n{r['game_key']} {r['title']} kickoff {r['kickoff_et']}  "
                    f"confidence={r['football_data_confidence']} contracts={r['contract_count']}"
            )
            print("  flags:", ", ".join(r["flag_codes"]))
            print("  sections:", r["sections"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
