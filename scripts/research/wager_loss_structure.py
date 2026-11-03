#!/usr/bin/env python3
"""Where did the realised CFB P&L go? Fees, sizing, correlation and market family, from the accounted-wager export.

    python3 scripts/research/wager_loss_structure.py --wagers app/latest/wagers.json [--unit 75]

Reads ONLY settled-wager arithmetic (price, contracts, stake, fees, profit_loss, game key). It uses no model
probability, because none is recorded with these wagers (model_run_id / recommendation_id are null), so it can NOT
separate handicapping error from pricing error and does not pretend to. What it can show:

  * the fee drag (ROI before vs after fees),
  * what fixed structural rules (5U per bet, 5U per game, no undecodable combos, one position per game) would have
    changed,
  * ROI by market family / side / entry price, each with a GAME-CLUSTERED bootstrap interval.

Rules are fixed in code before any result is read; none of them is chosen from the numbers. Cells are examined after
the fact, so an interval that excludes zero is a LEAD for a prospective test, never a validated edge. Output is
aggregate only: no ticker, order id or fill id is printed.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict

COMBO_PREFIX = "KXMVECROSSCATEGORY"
FAMILY_PREFIXES = (  # longest / most specific first
    ("KXNCAAFTEAMTOTAL", "TEAM_TOTAL"),
    ("KXNCAAF1QSPREAD", "1Q_SPREAD"),
    ("KXNCAAF1HSPREAD", "1H_SPREAD"),
    ("KXNCAAF1QTOTAL", "1Q_TOTAL"),
    ("KXNCAAF1HTOTAL", "1H_TOTAL"),
    ("KXNCAAFSPREAD", "GAME_SPREAD"),
    ("KXNCAAFTOTAL", "GAME_TOTAL"),
    ("KXNCAAFGAME", "GAME_ML"),
    (COMBO_PREFIX, "COMBO"),
)


def family(ticker: str) -> str:
    for prefix, name in FAMILY_PREFIXES:
        if ticker.startswith(prefix):
            return name
    return "OTHER"


def load(path: str) -> list[dict]:
    rows = []
    for w in json.load(open(path))["items"]:
        if w.get("settlement_status") != "SETTLED":
            continue
        t = w["kalshi_ticker"]
        rows.append(
            {
                "family": family(t),
                "combo": t.startswith(COMBO_PREFIX),
                "side": w["selection"],
                "price": w["average_price"],
                "contracts": w["contracts"],
                "stake": w["stake"],
                "fee": w["fees"],
                "pl": w["profit_loss"],
                "game": (w.get("source_ids") or {}).get("kalshi_game_key") or t,
                "placed": w["placed_at"],
            }
        )
    return rows


def scaled(r: dict, k: float) -> dict:
    return {**r, "stake": r["stake"] * k, "fee": r["fee"] * k, "pl": r["pl"] * k, "contracts": r["contracts"] * k}


def roi(rows: list[dict]) -> float:
    stake = sum(r["stake"] for r in rows)
    return sum(r["pl"] for r in rows) / stake if stake else 0.0


def cluster_ci(rows: list[dict], reps: int = 4000, seed: int = 11) -> tuple[float, float]:
    """95% interval on ROI, resampling whole games (positions in one game are one outcome, not independent bets)."""
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_game[r["game"]].append(r)
    games = list(by_game.values())
    if not games:
        return 0.0, 0.0
    rnd = random.Random(seed)
    draws = sorted(roi([x for g in (rnd.choice(games) for _ in games) for x in g]) for _ in range(reps))
    return draws[int(0.025 * reps)], draws[int(0.975 * reps) - 1]


def scenarios(rows: list[dict], unit: float) -> list[tuple[str, list[dict]]]:
    cap = 5 * unit
    per_bet = [scaled(r, min(1.0, cap / r["stake"])) if r["stake"] > 0 else r for r in rows]
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_game[r["game"]].append(r)
    per_game = []
    for g in by_game.values():
        total = sum(r["stake"] for r in g)
        k = min(1.0, cap / total) if total > 0 else 1.0
        per_game += [scaled(r, k) for r in g]
    structural = []
    for g in by_game.values():
        kept = [scaled(r, min(1.0, cap / r["stake"])) for r in g if not r["combo"] and r["stake"] > 0]
        total = sum(r["stake"] for r in kept)
        k = min(1.0, cap / total) if total > 0 else 1.0
        structural += [scaled(r, k) for r in kept]
    first: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: r["placed"]):
        first.setdefault(r["game"], r)
    return [
        ("actual", rows),
        ("actual, BEFORE fees (isolates the fee drag)", [{**r, "pl": r["pl"] + r["fee"]} for r in rows]),
        ("cap every bet at 5U", per_bet),
        ("cap total outlay per game at 5U", per_game),
        ("no combo / undecodable contracts", [r for r in rows if not r["combo"]]),
        ("no contracts priced under 30c", [r for r in rows if r["price"] >= 0.30]),
        ("one position per game (earliest placed)", list(first.values())),
        ("5U per bet + 5U per game + no combos", structural),
    ]


def time_split(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Chronological halves by placement time. The split is by count and fixed, not tuned: the first half is the
    development sample, the second the held-out sample, and no rule here was chosen by looking at either."""
    ordered = sorted(rows, key=lambda r: r["placed"])
    half = len(ordered) // 2
    return [("first half (development)", ordered[:half]), ("second half (held out)", ordered[half:])]


def stake_size_rank_correlation(rows: list[dict], reps: int = 5000, seed: int = 1) -> tuple[float, float]:
    """Spearman correlation between bet size and per-bet return, with a permutation p-value. Positive would mean larger
    bets did better, i.e. that sizing carried information."""

    def ranks(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        for rank, i in enumerate(order):
            out[i] = float(rank)
        return out

    def corr(a: list[float], b: list[float]) -> float:
        n = len(a)
        ma, mb = sum(a) / n, sum(b) / n
        va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
        return sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True)) / ((va * vb) ** 0.5) if va and vb else 0.0

    sized = [r for r in rows if r["stake"] > 0]
    if len(sized) < 3:
        return 0.0, 1.0
    a = ranks([r["stake"] for r in sized])
    b = ranks([r["pl"] / r["stake"] for r in sized])
    observed = corr(a, b)
    rnd = random.Random(seed)
    hits = 0
    for _ in range(reps):
        shuffled = b[:]
        rnd.shuffle(shuffled)
        if abs(corr(a, shuffled)) >= abs(observed):
            hits += 1
    return observed, hits / reps


def band(price: float) -> str:
    return "<30c" if price < 0.30 else "30-50c" if price < 0.50 else "50-70c" if price < 0.70 else "70c+"


def render(rows: list[dict], unit: float) -> str:
    out = []
    out.append("| scenario (rule fixed a priori) | bets | outlay $ | net $ | ROI | 95% game-cluster CI |")
    out.append("|---|---|---|---|---|---|")
    for name, rs in scenarios(rows, unit):
        lo, hi = cluster_ci(rs)
        outlay, net = sum(r["stake"] for r in rs), sum(r["pl"] for r in rs)
        out.append(f"| {name} | {len(rs)} | {outlay:,.0f} | {net:+,.0f} | {roi(rs):+.1%} | [{lo:+.0%}, {hi:+.0%}] |")
    for title, key in (
        ("family", lambda r: r["family"]),
        ("side", lambda r: r["side"]),
        ("entry price", lambda r: band(r["price"])),
    ):
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[key(r)].append(r)
        out += [
            "",
            f"| {title} | bets | games | outlay $ | net $ | ROI | 95% CI | fee % of outlay |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for k in sorted(groups, key=lambda k: -len(groups[k])):
            rs = groups[k]
            lo, hi = cluster_ci(rs)
            stake, net, fees = sum(r["stake"] for r in rs), sum(r["pl"] for r in rs), sum(r["fee"] for r in rs)
            games = len({r["game"] for r in rs})
            out.append(
                f"| {k} | {len(rs)} | {games} | {stake:,.0f} | {net:+,.0f} | {roi(rs):+.1%} | "
                f"[{lo:+.0%}, {hi:+.0%}] | {fees / stake:.1%} |"
            )
    out += ["", "| chronological split | bets | outlay $ | mean bet $ | ROI | 95% CI |", "|---|---|---|---|---|---|"]
    for name, rs in time_split(rows):
        if not rs:
            continue
        lo, hi = cluster_ci(rs)
        stake = sum(r["stake"] for r in rs)
        out.append(
            f"| {name} | {len(rs)} | {stake:,.0f} | {stake / len(rs):,.0f} | {roi(rs):+.1%} | [{lo:+.0%}, {hi:+.0%}] |"
        )
    rho, p = stake_size_rank_correlation(rows)
    out += ["", f"Spearman(bet size, per-bet return) = {rho:+.3f}, permutation p = {p:.3f}"]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wagers", required=True)
    ap.add_argument("--unit", type=float, default=75.0, help="dollars per unit (owner-set: 1U = $75)")
    a = ap.parse_args(argv)
    rows = load(a.wagers)
    print(f"settled wagers: {len(rows)}; games: {len({r['game'] for r in rows})}\n")
    print(render(rows, a.unit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
