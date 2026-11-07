"""The per-game Kalshi menu, enriched for an independent handicap: meanings, ladders, all-in costs, research inputs.

Source of truth is the execution layer the catalog workflow already publishes (``quotes.json`` for prices and sizes,
``events/*.json`` for settlement text, fee model and close times). Nothing here is re-fetched from Kalshi and nothing is
re-priced from a model: prices are the CAPTURED quotes, labelled as such, and every cost figure is arithmetic on them.

The per-family guide below answers, for each of the market families Kalshi lists, WHICH football evidence bears on it
and WHAT the analyst must estimate, and it names the coverage gaps explicitly: a family the repository cannot inform
is listed as a gap, never silently skipped and never filled with a league average.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from cfb_edge_finder.research_packet import history as hist
from cfb_edge_finder.research_packet.pricing import UNIT_DOLLARS, view_side

SCHEMA_VERSION = "cfb_research_markets/1.0.0"

FULL, PARTIAL, GAP = "SUPPORTED", "PARTIAL", "GAP"

#: How a ticker's period is encoded: KXNCAAF1Q..., KXNCAAF2QSPREAD, KXNCAAF1HTOTAL, KXNCAAF2HSPREAD ...
_PERIOD_RE = re.compile(r"^KXNCAAF(?P<n>[1-4])(?P<kind>Q|H)")
_SUFFIX_RE = re.compile(r"^(?P<code>[A-Z]*)(?P<num>\d*(?:\.\d+)?)$")

FAMILY_GUIDE: dict[str, dict[str, Any]] = {
    "game_moneyline": {
        "coverage": FULL,
        "estimate": "P(team wins the game)",
        "inputs": [
            "football.matchup",
            "football.baseline",
            "distributions.comparables",
            "availability",
            "environment",
            "scripts",
        ],
        "note": "likely winner, realistic upset pathways: use the dominant / competitive / upset script set in "
        "`game_script`.",
    },
    "game_spread": {
        "coverage": FULL,
        "estimate": "P(team wins by more than the line)",
        "inputs": [
            "football.matchup",
            "football.baseline",
            "distributions.comparables.p_home_margin_greater_than",
            "football.pace",
            "game_script",
        ],
        "note": "expected margin, control, mismatch and backdoor-cover risk (the margin distribution's width is the "
        "backdoor risk). Spread rungs are one ladder: P is monotone in the line.",
    },
    "game_total": {
        "coverage": FULL,
        "estimate": "P(total points > line)",
        "inputs": [
            "football.pace",
            "football.matchup",
            "football.baseline",
            "distributions.comparables.p_total_greater_than",
            "environment",
        ],
        "note": "projected possessions x scoring efficiency, explosive plays, defensive matchups; weather and tempo "
        "shift "
        "the whole ladder.",
    },
    "team_total": {
        "coverage": FULL,
        "estimate": "P(team points > line)",
        "inputs": [
            "football.matchup (that team's offense vs the actual opposing defense)",
            "football.baseline",
            "distributions.comparables.p_*_points_greater_than",
            "availability.qb",
        ],
        "note": "price a team total from THAT offense versus THIS defense, not from the game total split evenly.",
    },
    "first_half_moneyline": {
        "coverage": PARTIAL,
        "estimate": "P(team leads at halftime) / P(tie at halftime)",
        "inputs": [
            "distributions.comparables.first_half",
            "football.matchup (early-down success, opening scripts)",
            "football.pace",
        ],
        "note": "first-half history is conditional on the full-game baseline; team first-half scoring splits are raw "
        "(unadjusted) and opening scripts are not captured, so early-game tendencies come from those splits plus "
        "early-down success rate and neutral pass rate.",
        "gaps": [
            "team first-half splits are raw and unadjusted (football.period_splits_raw)",
            "opening-drive tendencies are not captured",
        ],
    },
    "first_half_spread": {
        "coverage": PARTIAL,
        "estimate": "P(team leads by more than the line at halftime)",
        "inputs": [
            "distributions.comparables.first_half.p_home_margin_greater_than",
            "football.matchup",
            "football.baseline",
        ],
        "gaps": [
            "team first-half splits are raw and unadjusted (football.period_splits_raw); opening-drive tendencies "
            "are not captured"
        ],
    },
    "first_half_total": {
        "coverage": PARTIAL,
        "estimate": "P(1H total points > line)",
        "inputs": ["distributions.comparables.first_half.p_total_greater_than", "football.pace", "football.baseline"],
        "gaps": [
            "team first-half splits are raw and unadjusted (football.period_splits_raw); opening-drive tendencies "
            "are not captured"
        ],
    },
    "first_half_team_total": {
        "coverage": PARTIAL,
        "estimate": "P(team 1H points > line)",
        "inputs": ["distributions.comparables.first_half", "football.matchup", "availability.qb"],
        "gaps": [
            "team first-half splits are raw and unadjusted (football.period_splits_raw); opening-drive tendencies "
            "are not captured"
        ],
    },
    "second_half_moneyline": {
        "coverage": PARTIAL,
        "estimate": "P(team wins the 2nd half)",
        "inputs": ["distributions.comparables", "football.matchup"],
        "gaps": ["second-half outcomes derive from regulation Q3+Q4 linescores only"],
    },
    "second_half_spread": {
        "coverage": PARTIAL,
        "estimate": "P(team wins the 2H by more than the line)",
        "inputs": ["distributions.comparables", "football.matchup"],
        "gaps": ["second-half outcomes derive from regulation Q3+Q4 linescores only"],
    },
    "second_half_total": {
        "coverage": PARTIAL,
        "estimate": "P(2H total > line)",
        "inputs": ["distributions.comparables.second_half", "football.pace"],
        "gaps": ["second-half outcomes derive from regulation Q3+Q4 linescores only"],
    },
    "quarter_moneyline": {
        "coverage": PARTIAL,
        "estimate": "P(team outscores the other in that quarter) / P(quarter tie)",
        "inputs": ["distributions.comparables.first_quarter", "football.pace"],
        "gaps": [
            "opening-drive tendencies are not captured; team quarter splits are raw and unadjusted",
            "quarter outcomes are high-variance: treat the reference class as shape only",
        ],
    },
    "quarter_spread": {
        "coverage": PARTIAL,
        "estimate": "P(team wins the quarter by more than the line)",
        "inputs": ["distributions.comparables.first_quarter", "football.pace"],
        "gaps": ["opening-drive tendencies are not captured"],
    },
    "quarter_total": {
        "coverage": PARTIAL,
        "estimate": "P(quarter total points > line)",
        "inputs": [
            "distributions.comparables.first_quarter.p_total_greater_than",
            "football.pace (seconds per play, drives per game)",
            "football.matchup",
        ],
        "gaps": [
            "opening-drive tendencies are not captured",
            "no scripted-opening-drive indicator exists for either team",
        ],
    },
    "winning_margin": {
        "coverage": FULL,
        "estimate": "P(winner and margin band)",
        "inputs": ["distributions.comparables.p_home_margin_greater_than", "football.matchup", "game_script"],
        "note": "a band is the difference of two spread-ladder probabilities; price it from the margin distribution, "
        "then "
        "check the bands sum to the winner probability.",
    },
    "overtime": {
        "coverage": PARTIAL,
        "estimate": "P(game goes to overtime)",
        "inputs": ["distributions.comparables.actual_home_margin (mass near zero)", "football.baseline"],
        "gaps": [
            "overtime frequency among comparables is small-n; regulation-tie probability is not modelled by the "
            "repository"
        ],
    },
    "touchdown_scorer": {
        "coverage": PARTIAL,
        "estimate": "P(team scores the first TD) / P(no TD)",
        "inputs": ["football.pace", "football.matchup", "distributions.comparables.first_quarter"],
        "gaps": [
            "opening-drive and first-score tendencies are not captured; coin-toss/receive decisions are unknown pregame"
        ],
    },
    "team_stat_prop": {
        "coverage": PARTIAL,
        "estimate": "P(team stat exceeds the threshold) e.g. receiving touchdowns",
        "inputs": ["football.matchup (passing / rushing / finishing)", "football.baseline", "availability.qb"],
        "gaps": [
            "team stat counts beyond points are in the log as box totals only; no historical distribution of that "
            "stat is conditioned on the baseline"
        ],
    },
    "game_stat_prop": {
        "coverage": GAP,
        "estimate": "P(stat event occurs) e.g. a defensive or special-teams touchdown",
        "inputs": ["football.matchup (disruption)", "special teams counts in schedule rows"],
        "gaps": ["no historical rate of this event is published; derive a base rate from outside evidence and say so"],
    },
    "player_prop": {
        "coverage": GAP,
        "estimate": "P(player stat exceeds the threshold)",
        "inputs": ["availability", "football.qb", "roster"],
        "gaps": [
            "the repository has no player-level usage, target share, carry share or matchup data; "
            "individual-player markets need external research"
        ],
    },
    "unknown": {
        "coverage": GAP,
        "estimate": "read rules_primary: the contract was captured in full but not labelled",
        "inputs": ["event rules text"],
        "gaps": [
            "unlabelled family: classify from rules_primary before pricing (e.g. half/full-game parlays are "
            "conjunctions: price the joint outcome, not the legs independently)"
        ],
    },
}
DEFAULT_GUIDE = {
    "coverage": GAP,
    "estimate": "see rules_primary",
    "inputs": [],
    "gaps": ["family has no handicapping guide yet"],
}

#: Same underlying quantity expressed in different families, so a thesis is compared across every expression.
THESIS_GROUPS: dict[str, list[str]] = {
    "full_game_winner_and_margin": ["game_moneyline", "game_spread", "winning_margin"],
    "full_game_scoring": ["game_total", "team_total"],
    "first_half": ["first_half_moneyline", "first_half_spread", "first_half_total", "first_half_team_total"],
    "second_half": ["second_half_moneyline", "second_half_spread", "second_half_total"],
    "quarters": ["quarter_moneyline", "quarter_spread", "quarter_total"],
    "other": ["touchdown_scorer", "team_stat_prop", "game_stat_prop", "overtime", "player_prop", "unknown"],
}


def period_of(series: str, family: str, event_period: str | None) -> str:
    m = _PERIOD_RE.match(series)
    if m:
        n, kind = m.group("n"), m.group("kind")
        return f"{'q' if kind == 'Q' else 'h'}{n}" if not (kind == "H" and n == "1") else "h1"
    if family.startswith("first_half"):
        return "h1"
    if family.startswith("second_half"):
        return "h2"
    return event_period or "game"


def split_suffix(market_ticker: str) -> tuple[str, str]:
    tail = market_ticker.rsplit("-", 1)[-1]
    m = _SUFFIX_RE.match(tail)
    return (m.group("code"), m.group("num")) if m else ("", "")


def side_codes(game_key: str, tickers: list[str]) -> dict[str, str | None]:
    """Away/home ticker codes. Kalshi game keys are DATE + AWAYCODE + HOMECODE; tickers' codes resolve the split."""
    tail = game_key[7:]
    codes = {split_suffix(t)[0] for t in tickers} - {"", "TIE", "NONE", "NOTD", "OT"}
    for away in sorted(codes, key=len, reverse=True):
        rest = tail[len(away) :] if tail.startswith(away) else None
        if rest and rest in codes:
            return {"away": away, "home": rest}
    return {"away": None, "home": None}


def quantity_for(
    family: str, period: str, code: str, num: str, floor_strike: float | None, codes: dict[str, str | None]
) -> dict[str, Any]:
    """The structured thing the analyst must estimate for this contract's YES outcome (home/away oriented)."""
    side = "home" if code and code == codes.get("home") else "away" if code and code == codes.get("away") else None
    hist_period = {"h1": "h1", "h2": "h2", "q1": "q1", "q2": "q2", "q3": "q3", "q4": "q4"}.get(period, "game")
    if family.endswith("moneyline") and family != "unknown":
        if code in ("TIE",):
            return {"type": "win", "period": hist_period, "outcome": "tie"}
        return {"type": "win", "period": hist_period, "side": side, "team_code": code or None}
    if family.endswith("spread"):
        return {
            "type": "margin_over",
            "period": hist_period,
            "side": side,
            "team_code": code or None,
            "threshold": floor_strike,
        }
    if family.endswith("team_total"):
        return {
            "type": "team_points_over",
            "period": hist_period,
            "side": side,
            "team_code": code or None,
            "threshold": floor_strike,
        }
    if family.endswith("total"):
        return {"type": "total_over", "period": hist_period, "threshold": floor_strike}
    return {"type": "see_title"}


def _ladder_key(family: str, event_ticker: str, code: str) -> str:
    return f"{event_ticker}|{code}" if family.endswith(("spread", "team_total")) else event_ticker


def build_markets(
    *,
    game_key: str,
    quote_files: list[dict[str, Any]],
    event_files: dict[str, dict[str, Any]],
    capture: dict[str, Any],
    reference_games: list[dict[str, Any]],
    unit_dollars: float = UNIT_DOLLARS,
    orientation_swapped: bool = False,
    team_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The enriched menu for one game. ``quote_files`` are the parsed ``quotes*.json`` parts, ``event_files`` the parsed
    event shards by event ticker; ``reference_games`` is the comparables reference class (may be empty)."""
    quotes = [q for part in quote_files for q in part["quotes"]]
    fingerprint = quote_files[0].get("game_fingerprint") if quote_files else None
    codes = side_codes(game_key, [q["market_ticker"] for q in quotes])
    if orientation_swapped:
        # Kalshi's key order is AWAYHOME in Kalshi's own orientation. When identity resolution found the football game
        # lists
        # the teams the other way round, `home`/`away` in every quantity must follow the FOOTBALL game, because the
        # reference
        # class and the matchup tables are oriented that way. Never let a contract's side disagree with the packet's.
        codes = {"away": codes["home"], "home": codes["away"]}

    # Contract-level facts that live only in the event shards (settlement text, fee model, close times).
    detail: dict[str, dict[str, Any]] = {}
    events: dict[str, dict[str, Any]] = {}
    for ev_ticker, shard in event_files.items():
        for m in shard.get("markets") or []:
            detail[m["market_ticker"]] = m
        first = (shard.get("markets") or [None])[0]
        if first:
            events[ev_ticker] = {
                "family": first.get("family"),
                "period": first.get("period"),
                "series_ticker": first.get("series_ticker"),
                "rules_primary_example": first.get("rules_primary"),
                "rules_secondary": (first.get("rules_secondary") or "").split("\n\n")[0],
                "settlement_sources": [s.get("name") for s in first.get("settlement_sources") or []],
                "fee_model": first.get("fee_type"),
                "fee_multiplier": first.get("fee_multiplier"),
                "close_time": first.get("close_time"),
                "expected_expiration_time": first.get("expected_expiration_time"),
                "occurrence_datetime": first.get("occurrence_datetime"),
                "settlement_timer_seconds": first.get("settlement_timer_seconds"),
                "can_close_early": first.get("can_close_early"),
            }

    contracts: list[dict[str, Any]] = []
    ladders: dict[str, dict[str, Any]] = defaultdict(lambda: {"tickers": [], "lines": []})
    families: dict[str, int] = defaultdict(int)
    for q in sorted(quotes, key=lambda r: (r["event_ticker"], r["market_ticker"])):
        family = q["family"]
        families[family] += 1
        series = q["market_ticker"].split("-", 1)[0]
        d = detail.get(q["market_ticker"], {})
        code, num = split_suffix(q["market_ticker"])
        period = period_of(series, family, d.get("period"))
        qty = quantity_for(family, period, code, num, q.get("floor_strike"), codes)
        yes = view_side("YES", q.get("yes_ask"), series, dollars=unit_dollars)
        no = view_side("NO", q.get("no_ask"), series, dollars=unit_dollars)

        def side_block(view, ask, size, bid, executable):  # noqa: ANN001
            # break-even probability == all_in_per_contract (the probability at which the side has zero EV after fees)
            return {
                "ask": ask,
                "ask_size": size,
                "bid": bid,
                "exec": executable,
                "contracts_1u": view.contracts,
                "order_fee_1u": view.order_fee,
                "all_in": view.all_in_per_contract,
            }

        record: dict[str, Any] = {
            "ticker": q["market_ticker"],
            "family": family,
            "period": period,
            "yes_means": d.get("yes_sub_title") or q["title"],
            "line": q.get("floor_strike"),
            "kind": qty.get("type"),
            "team": qty.get("team_code"),
            "side": qty.get("side"),
            "book": q["book_state"],
            "yes": side_block(
                yes, q.get("yes_ask"), q.get("yes_ask_size"), q.get("yes_bid"), q.get("buy_yes_executable")
            ),
            "no": side_block(no, q.get("no_ask"), q.get("yes_bid_size"), q.get("no_bid"), q.get("buy_no_executable")),
            "upd": q.get("updated_time"),
            "ladder": _ladder_key(family, q["event_ticker"], code),
        }
        record = {k: v for k, v in record.items() if v is not None}
        if qty.get("outcome"):
            record["outcome"] = qty["outcome"]
        if q["status"] != "active":
            record["status"] = q["status"]
        if q.get("is_alternate_line"):
            record["alt"] = True
        if reference_games:
            freq = hist.frequency_for(reference_games, qty)
            if freq is not None:
                record["ref_freq"] = {"n": freq["n"], "yes": freq["yes_frequency"]}
        if family.endswith(("spread", "total", "team_total")) and q.get("floor_strike") is not None:
            lad = ladders[record["ladder"]]
            lad["tickers"].append(q["market_ticker"])
            lad["lines"].append(q["floor_strike"])
            lad["family"] = family
            lad["period"] = period
            lad["subject"] = code or None
        contracts.append(record)

    for lad in ladders.values():
        order = sorted(range(len(lad["lines"])), key=lambda i: lad["lines"][i])
        lad["lines"] = [lad["lines"][i] for i in order]
        lad["tickers"] = [lad["tickers"][i] for i in order]
        lad["monotone_rule"] = (
            "YES probability is non-increasing as the line rises; a price that violates it is a mispricing or a "
            "stale quote"
        )

    guide = {f: {**FAMILY_GUIDE.get(f, DEFAULT_GUIDE), "contracts": n} for f, n in sorted(families.items())}
    return {
        "schema_version": SCHEMA_VERSION,
        "game_key": game_key,
        "contains_model_probabilities": False,
        "price_provenance": {
            "kind": "CAPTURED_NOT_LIVE_VERIFIED",
            "captured_at": capture.get("captured_at"),
            "captured_at_meaning": capture.get("captured_at_meaning"),
            "game_fingerprint": fingerprint,
            "rule": (
                "every price below is a capture from the catalog collector, not a live order-book quote. Compare "
                "`game_fingerprint` with `data/live/execution/games/<game_key>/quotes.json`: if they differ, the "
                "quotes file is "
                "newer and wins. Confirm any price on Kalshi before acting."
            ),
        },
        "pricing_basis": {
            "unit_dollars": unit_dollars,
            "buy_YES": "pay yes_ask; size is yes.ask_size",
            "buy_NO": "pay no_ask (= 1 - yes_bid, NEVER 1 - yes_ask); size is no.ask_size (the YES bid book)",
            "all_in_per_contract": "ask + the ORDER's taker fee / contracts, for a one-unit order; fee = "
            "ceil_to_cent(multiplier x 0.07 x "
            "contracts x P x (1-P))",
            "fee_excludes": ["rounding_fee", "rebate", "fee_accumulator", "user_balance_precision"],
            "break_even_probability": "equals all_in: the probability that the side you BUY wins at which expected "
            "value is zero",
            "contract_fields": {
                "yes/no.ask": "price to BUY that side (NO uses the NO ask)",
                "yes/no.ask_size": "contracts quoted at that ask at capture (NO size is the YES bid book)",
                "yes/no.exec": "positive quoted size on that side at capture (NOT live-verified)",
                "yes/no.all_in": "ask + one-unit order fee spread over its contracts = break-even probability for "
                "that side",
                "ref_freq": "share of n comparable past games in which the YES outcome occurred: descriptive "
                "reference class, "
                "NOT a fair probability",
                "upd": "when Kalshi last changed the contract; a thin ladder rung can be days old",
            },
        },
        "side_codes": codes,
        "side_teams": {
            "home": {"ticker_code": codes.get("home"), "team": (team_names or {}).get("home")},
            "away": {"ticker_code": codes.get("away"), "team": (team_names or {}).get("away")},
            "orientation_swapped_vs_kalshi_key": orientation_swapped,
            "rule": "`side` on a contract is the FOOTBALL game's home/away (matches football.* in packet.json); "
            "verify the "
            "code-to-team pairing here",
        },
        "families": guide,
        "thesis_groups": THESIS_GROUPS,
        "events": events,
        "ladders": dict(sorted(ladders.items())),
        "contract_count": len(contracts),
        "contracts": contracts,
    }
