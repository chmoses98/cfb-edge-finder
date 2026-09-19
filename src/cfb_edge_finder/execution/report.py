"""The final bet report -- and the gate that stands in front of it.

*** THIS IS THE OPTIONAL PATH, NOT THE LIVE ONE ***
The live workflow is: generate `<window>.analysis.json`, upload it to
ChatGPT, get every bet back in the conversation. Nothing is written back
to this repository and this module is not on that path.

What it is for is arithmetic verification after the fact: given a
handicap somebody already produced, re-price every eligible contract and
prove the coverage closed. It is kept because that proof is worth having,
not because a shortlist has to come from here.

There is no bet cap anywhere in it: `top_n` defaults to None, which
returns every survivor. The CLI's `--top` is a display truncation for
debugging and never touches the ledger.

*** THE ORDER THAT MATTERS ***
    every game in the shard COMPLETE
        -> rank the positive-EV survivors
            -> apply the correlation review
                -> produce the shortlist

Not one step earlier. `build_report` calls `require_complete` on every
game before it looks at a single candidate, and raises if any game is
INCOMPLETE. A shortlist that exists is therefore a shortlist drawn from a
scan that closed its books.

*** WHAT A FINAL BET IS, AND IS NOT ***
A final bet is a SURVIVOR of every eligible contract in the shard, ranked
by the edge implied by a handicap this code did not produce. It is not a
recommendation, it carries no stake, and nothing in this repository claims
that the handicap behind it is any good. `stake_placeholder` is a blank
for the operator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cfb_edge_finder.execution import REPORT_SCHEMA_VERSION
from cfb_edge_finder.execution.evaluator import (
    EvaluationStatus,
    GameEvaluation,
    require_complete,
)
from cfb_edge_finder.execution.handicap import HandicapPayload


class ShardGateError(RuntimeError):
    """The shard is not fully scanned, so no shortlist may be produced."""


@dataclass(frozen=True)
class Candidate:
    row: dict[str, Any]
    driver: str
    direction: str

    @property
    def net_edge(self) -> float:
        return float(self.row.get("net_edge") or 0.0)


def _driver_and_direction(row: dict[str, Any]) -> tuple[str, str]:
    """Which underlying quantity the bet is really a view on, and which
    way.

    Two contracts that are the same view in different clothes -- YES on
    "home wins by over 6.5" and NO on "away wins by over 5.5" -- must not
    both appear in a shortlist as if they were independent ideas. Reducing
    every contract to (driver, direction) is what makes that visible."""
    kind = str(row.get("kind") or "")
    period = str(row.get("period") or "")
    team = str(row.get("team") or "none")
    side = str(row.get("best_side") or "yes")
    yes_side = side == "yes"

    if kind in ("moneyline", "spread", "margin_band"):
        home_view = (team == "home") == yes_side
        return f"{period}:margin", "home" if home_view else "away"
    if kind == "total":
        return f"{period}:total", "over" if yes_side else "under"
    if kind == "team_total":
        return f"{period}:team_total:{team}", "over" if yes_side else "under"
    return f"contract:{row.get('ticker')}", side


def correlation_review(rows: list[dict[str, Any]]) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Keep the best expression of each distinct view; report the rest.

    Deterministic and explainable: within one game, one period, one driver
    and one direction, the contract with the largest fee-adjusted edge
    survives and the others are recorded as dominated duplicates of it."""
    best: dict[tuple[str, str, str], Candidate] = {}
    ordered: list[Candidate] = []
    for row in rows:
        driver, direction = _driver_and_direction(row)
        candidate = Candidate(row=row, driver=driver, direction=direction)
        ordered.append(candidate)
        key = (str(row.get("game_key")), driver, direction)
        incumbent = best.get(key)
        if incumbent is None or candidate.net_edge > incumbent.net_edge:
            best[key] = candidate

    survivors = sorted(best.values(), key=lambda c: -c.net_edge)
    kept = {id(c) for c in survivors}
    dropped = [
        {
            "ticker": c.row.get("ticker"),
            "game_key": c.row.get("game_key"),
            "net_edge": c.row.get("net_edge"),
            "reason": (
                f"a higher-edge expression of the same view "
                f"({c.driver} / {c.direction}) survived the correlation review"
            ),
        }
        for c in ordered
        if id(c) not in kept
    ]
    return survivors, dropped


def _why_this_market(row: dict[str, Any]) -> str:
    kind = str(row.get("kind") or "")
    period = str(row.get("period") or "")
    driver = {
        "moneyline": f"the {period} margin distribution",
        "spread": f"the {period} margin distribution",
        "margin_band": f"the {period} margin distribution",
        "total": f"the {period} combined-score distribution",
        "team_total": f"the {period} team-score distribution",
    }.get(kind, "an explicit probability supplied by the handicap")
    side = str(row.get("best_side"))
    meaning = row.get("yes_means") if side == "yes" else row.get("no_means")
    return (
        f"Buying {side.upper()} expresses '{meaning}'. It prices directly off {driver}, so the "
        f"handicap's disagreement with the market shows up here without passing through any other "
        f"assumption. Fair {row.get('fair_probability')} vs implied {row.get('implied_probability')} "
        f"leaves {row.get('raw_edge')} before the {row.get('fee')} entry fee."
    )


def candidate_record(
    candidate: Candidate,
    handicaps: dict[str, HandicapPayload],
    packets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = candidate.row
    game_key = str(row.get("game_key"))
    handicap = handicaps.get(game_key)
    packet = packets.get(game_key) or {}
    side = str(row.get("best_side"))
    return {
        "game": packet.get("title") or game_key,
        "game_key": game_key,
        "kickoff": packet.get("kickoff"),
        "market": row.get("ticker"),
        "market_family": row.get("family"),
        "period": row.get("period"),
        "side": side.upper(),
        "side_means": row.get("yes_means") if side == "yes" else row.get("no_means"),
        "kalshi_executable_price": row.get("executable_entry"),
        "implied_probability": row.get("implied_probability"),
        "fair_probability": row.get("fair_probability"),
        "raw_edge": row.get("raw_edge"),
        "fee": row.get("fee"),
        "fee_adjusted_edge": row.get("net_edge"),
        "expected_value_per_contract": row.get("ev_per_contract"),
        "handicap_confidence": handicap.confidence if handicap else "unstated",
        "confidence_grade_note": (
            "this repository has no validated confidence-grade convention, so the handicapper's own "
            "stated confidence is reported verbatim rather than translated into a grade"
        ),
        "stake_placeholder": None,
        "stake_note": "the operator sets this; nothing in this repository sizes a bet",
        "game_thesis": (handicap.thesis or handicap.assumptions or "not supplied by the handicap")
        if handicap
        else "not supplied by the handicap",
        "why_this_market_expresses_the_thesis": _why_this_market(row),
        "strongest_opposing_case": (
            handicap.opposing_case if handicap and handicap.opposing_case else "not supplied by the handicap"
        ),
        "correlation_group": f"{candidate.driver} / {candidate.direction}",
    }


def build_report(
    shard: str | None,
    evaluations: list[GameEvaluation],
    packets: dict[str, dict[str, Any]],
    handicaps: dict[str, HandicapPayload],
    *,
    min_net_edge: float,
    games_discovered: int,
    contracts_discovered: int,
    mechanical_exclusions: int,
    top_n: int | None = None,
) -> dict[str, Any]:
    incomplete = [e for e in evaluations if not e.complete]
    if incomplete:
        details = "; ".join(
            f"{e.game_key}: eligible={e.eligible} evaluated={e.evaluated} "
            f"unpriceable={e.unpriceable} unaccounted={e.unaccounted} "
            f"missing={e.missing_tickers or '-'}"
            for e in incomplete
        )
        raise ShardGateError(
            f"shard {shard!r} has {len(incomplete)} INCOMPLETE game(s); no shortlist may be "
            f"produced until every game passes its completion gate. {details}"
        )
    for evaluation in evaluations:
        require_complete(evaluation)

    positive = [
        row
        for evaluation in evaluations
        for row in evaluation.rows
        if row.get("status") == EvaluationStatus.POSITIVE_EV.value
    ]
    survivors, dropped = correlation_review(positive)
    final = survivors if top_n is None else survivors[:top_n]

    totals = {
        "games_discovered": games_discovered,
        "games_handicapped": len(handicaps),
        "games_fully_reconciled": len(evaluations),
        "contracts_discovered": contracts_discovered,
        "mechanical_exclusions": mechanical_exclusions,
        "eligible_contracts": sum(e.eligible for e in evaluations),
        "priced_evaluated": sum(e.evaluated for e in evaluations),
        "explicitly_unpriceable": sum(e.unpriceable for e in evaluations),
        "unaccounted": sum(e.unaccounted for e in evaluations),
        "positive_ev_contracts": len(positive),
        "passed_correlation_review": len(survivors),
        "final_bets_selected": len(final),
    }

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "shard": shard,
        "generated_at": datetime.now(UTC).isoformat(),
        "min_net_edge": min_net_edge,
        "totals": totals,
        "provenance": (
            f"The {len(final)} final bet(s) are survivors of {totals['eligible_contracts']} "
            f"exhaustive contract dispositions, not markets selected for evaluation."
        ),
        "disclaimer": (
            "No edge is claimed. Every fair probability here came from a handicap supplied from "
            "outside this repository; the code checked arithmetic and coverage, nothing else. "
            "No order can be placed from this artifact."
        ),
        "games": [e.summary() for e in evaluations],
        "final_bets": [candidate_record(c, handicaps, packets) for c in final],
        "dominated_duplicates": dropped,
    }


def render_report(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        f"CFB EXECUTION REPORT -- shard {report.get('shard')}",
        "",
        f"Games discovered:        {totals['games_discovered']}",
        f"Games handicapped:       {totals['games_handicapped']}",
        f"Games fully reconciled:  {totals['games_fully_reconciled']}",
        "",
        f"Contracts discovered:    {totals['contracts_discovered']}",
        f"Mechanical exclusions:   {totals['mechanical_exclusions']}",
        f"Eligible contracts:      {totals['eligible_contracts']}",
        f"Priced/evaluated:        {totals['priced_evaluated']}",
        f"Explicitly unpriceable:  {totals['explicitly_unpriceable']}",
        f"Unaccounted:             {totals['unaccounted']}",
        "",
        f"Positive-EV contracts:      {totals['positive_ev_contracts']}",
        f"Passed correlation review:  {totals['passed_correlation_review']}",
        f"Final bets selected:        {totals['final_bets_selected']}",
        "",
        report["provenance"],
        "",
    ]
    for index, bet in enumerate(report["final_bets"], start=1):
        lines.extend(
            [
                f"{index}. {bet['game']} -- {bet['market']}",
                f"   side {bet['side']}: {bet['side_means']}",
                f"   price {bet['kalshi_executable_price']} (implied {bet['implied_probability']}) "
                f"vs fair {bet['fair_probability']}",
                f"   raw edge {bet['raw_edge']} | fee {bet['fee']} | fee-adjusted {bet['fee_adjusted_edge']}",
                f"   handicap confidence: {bet['handicap_confidence']} | stake: {bet['stake_placeholder']}",
                f"   thesis: {bet['game_thesis']}",
                f"   why this market: {bet['why_this_market_expresses_the_thesis']}",
                f"   strongest opposing case: {bet['strongest_opposing_case']}",
                "",
            ]
        )
    lines.append(report["disclaimer"])
    return "\n".join(lines)
