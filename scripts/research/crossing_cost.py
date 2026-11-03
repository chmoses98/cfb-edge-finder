#!/usr/bin/env python3
"""What does it cost to BUY a CFB Kalshi contract by crossing the book, by market family?

    python3 scripts/research/crossing_cost.py [--games data/live/execution/games] [--lo 0.30] [--hi 0.70]

For every active, two-sided contract side in the captured quotes, with the ask inside [lo, hi]:
    half_spread = (ask - bid) / 2      what a taker gives up against the mid
    taker_fee   = the exchange's documented trade fee at the ask (buy_yes_fee / buy_no_fee)
    cost        = half_spread + taker_fee   the edge over MID a taker needs just to break even

This is arithmetic on quoted prices only. It uses no model probability. The books are whatever was captured (typically
well before kickoff), not the books at the moment of any fill, so it describes the market's structure, not realised
slippage. Wide books may be stale or illiquid, which is why the median is printed beside the mean.
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics as st
import sys
from collections import defaultdict


def sides(quote_file: str, lo: float, hi: float):
    q = json.load(open(quote_file))
    for m in q.get("quotes", []):
        if m.get("status") != "active" or m.get("book_state") != "two_sided":
            continue
        for side in ("yes", "no"):
            ask, bid, fee = m.get(f"{side}_ask"), m.get(f"{side}_bid"), m.get(f"buy_{side}_fee")
            if ask is None or bid is None or fee is None or not (lo <= ask <= hi) or bid > ask:
                continue
            yield {"family": m.get("family") or "unknown", "half_spread": (ask - bid) / 2, "fee": fee}


def summarise(rows: list[dict]) -> dict[str, dict]:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r["family"]].append(r)
        by["ALL"].append(r)
    out = {}
    for fam, rs in by.items():
        cost = [r["half_spread"] + r["fee"] for r in rs]
        out[fam] = {
            "n": len(rs),
            "mean_half_spread_c": 100 * st.mean(r["half_spread"] for r in rs),
            "mean_fee_c": 100 * st.mean(r["fee"] for r in rs),
            "mean_cost_c": 100 * st.mean(cost),
            "median_cost_c": 100 * st.median(cost),
        }
    return out


def render(summary: dict[str, dict]) -> str:
    lines = [
        "| family | contract sides | mean half-spread | mean taker fee | mean cost to cross | median cost to cross |",
        "|---|---|---|---|---|---|",
    ]
    for fam in sorted(summary, key=lambda f: summary[f]["median_cost_c"]):
        s = summary[fam]
        lines.append(
            f"| {fam} | {s['n']} | {s['mean_half_spread_c']:.1f}c | {s['mean_fee_c']:.2f}c | "
            f"{s['mean_cost_c']:.1f}c | {s['median_cost_c']:.1f}c |"
        )
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", default="data/live/execution/games")
    ap.add_argument("--lo", type=float, default=0.30)
    ap.add_argument("--hi", type=float, default=0.70)
    a = ap.parse_args(argv)
    rows = [r for f in glob.glob(f"{a.games}/*/quotes.json") for r in sides(f, a.lo, a.hi)]
    if not rows:
        print("no two-sided quotes found", file=sys.stderr)
        return 1
    print(render(summarise(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
