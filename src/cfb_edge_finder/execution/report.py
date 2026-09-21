"""The candidate artifact: what an operator actually reads.

*** IT IS A VIEW OF THE LEDGER, NEVER A SEPARATE DATASET ***
Everything here is derived from the evaluation ledger, which already holds
every eligible contract's terminal disposition. The shard gate runs first and
refuses outright on an INCOMPLETE game, so the shortlist cannot be a view of a
partial scan.

*** WHAT IS IN IT ***
Enough to act on and nothing else. A candidate carries the market and what YES
and NO actually mean, the executable price and its fee, the fee-adjusted
breakeven, the base fair probability and what happens to it across the
handicap's own uncertainty region, the robustness verdict, the price to stop
buying at, the handicapper's thesis and their strongest opposing case, the
factual data quality behind it, the market-disagreement reading, the
correlation group it belongs to, the alternative contracts it beat, and why it
beat them.

*** WHAT IS NOT IN IT ***
The other 14,000 contracts. They are all in the ledger, all counted, and all
readable -- and re-uploading them for a final review is the latency problem
this whole redesign exists to remove.

*** AND NO CAP ***
There is no `[:N]` in the reduction. `--top` still exists on the CLI for
reading a long list on a terminal; it is a display truncation, it says so in
the artifact when it is used, and the reduction ledger still carries every
contract that lost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cfb_edge_finder.execution import CANDIDATE_SCHEMA_VERSION, REPORT_SCHEMA_VERSION
from cfb_edge_finder.execution.candidates import (
    Expression,
    Reduction,
    exposure_groups,
    reduce_candidates,
)
from cfb_edge_finder.execution.disagreement import measure
from cfb_edge_finder.execution.evaluator import (
    CANDIDATE_STATUSES,
    EvaluationStatus,
    GameEvaluation,
    require_complete,
)
from cfb_edge_finder.execution.handicap import HandicapPayload


class ShardGateError(RuntimeError):
    """The shard is not fully scanned, so no shortlist may be produced."""


@dataclass(frozen=True)
class Candidate:
    """Kept for callers that predate the reduction engine.

    `driver` and `direction` are the correlation-group coordinates; the engine
    in `candidates.py` owns the comparison logic now."""

    row: dict[str, Any]
    driver: str
    direction: str

    @property
    def net_edge(self) -> float:
        return float(self.row.get("net_edge") or 0.0)


def correlation_review(rows: list[dict[str, Any]]) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """The schema-1 signature, backed by the full reduction engine.

    Returns (survivors, removed). The removed entries now carry `lost_to` and
    a deterministic reason from `candidates.ReductionReason` rather than a
    single generic sentence."""
    reduction = reduce_candidates(rows)
    survivors = [
        Candidate(row=e.row, driver=e.driver, direction=e.direction)
        for e in reduction.survivors
    ]
    return survivors, reduction.removed


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
    candidate: Candidate | Expression,
    handicaps: dict[str, HandicapPayload],
    packets: dict[str, dict[str, Any]],
    *,
    reduction: Reduction | None = None,
    disagreements: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    row = candidate.row
    game_key = str(row.get("game_key"))
    handicap = handicaps.get(game_key)
    packet = packets.get(game_key) or {}
    side = str(row.get("best_side"))
    sensitivity = row.get("sensitivity") or {}
    context = packet.get("factual_context") or {}
    ticker = str(row.get("ticker"))
    alternatives = (reduction.alternatives.get(ticker, []) if reduction else [])
    return {
        "game": packet.get("title") or game_key,
        "game_key": game_key,
        "kickoff": packet.get("kickoff"),
        "market": ticker,
        "market_family": row.get("family"),
        "period": row.get("period"),
        "side": side.upper(),
        "side_means": row.get("yes_means") if side == "yes" else row.get("no_means"),
        "kalshi_executable_price": row.get("executable_entry"),
        "fee": row.get("fee"),
        "fee_adjusted_breakeven": sensitivity.get("fee_adjusted_breakeven"),
        "implied_probability": row.get("implied_probability"),
        "fair_probability": row.get("fair_probability"),
        "raw_edge": row.get("raw_edge"),
        "fee_adjusted_edge": row.get("net_edge"),
        "expected_value_per_contract": row.get("ev_per_contract"),
        # ---- the part a point estimate cannot produce -------------------
        "robustness": sensitivity.get("robustness") or row.get("status"),
        "robustness_reason": sensitivity.get("robustness_reason"),
        "fair_probability_range": [
            sensitivity.get("fair_probability_low"),
            sensitivity.get("fair_probability_high"),
        ],
        "fee_adjusted_edge_range": [
            sensitivity.get("net_edge_low"),
            sensitivity.get("net_edge_high"),
        ],
        "worst_scenario": sensitivity.get("worst_scenario"),
        "sensitivity_bound": sensitivity.get("sensitivity_bound"),
        "scenarios_tested": sensitivity.get("scenarios_tested"),
        "bet_up_to_price": row.get("bet_up_to_price"),
        "bet_up_to_note": (
            "the highest price at which this side still clears the operator's required edge, "
            "computed at the quoted fee. Kalshi's trade fee rises with price, so the true ceiling "
            "is slightly below this figure."
        ),
        # ---- where the numbers came from --------------------------------
        "handicap_confidence": handicap.confidence if handicap else "unstated",
        "handicap_confidence_effective": handicap.effective_confidence if handicap else "unstated",
        "handicap_confidence_capped_by_data": (
            handicap.confidence_was_capped if handicap else False
        ),
        "confidence_grade_note": (
            "this repository has no validated confidence-grade convention, so the handicapper's own "
            "stated confidence is reported verbatim rather than translated into a grade"
        ),
        "factual_data_quality": {
            "confidence_ceiling": (context.get("data_quality") or {}).get("confidence_ceiling"),
            "missing_domains": context.get("missing_domains") or [],
            "coverage": context.get("coverage") or {},
        },
        "market_disagreement": (disagreements or {}).get(game_key),
        # ---- exposure and reduction -------------------------------------
        "correlation_group": f"{candidate.driver} / {candidate.direction}",
        "related_alternatives": alternatives,
        "why_this_expression_survived": (
            f"it is the best expression of the {candidate.driver} / {candidate.direction} view in "
            f"this game: it beat {len(alternatives)} other contract(s) on fee-adjusted edge, entry "
            "fee, robustness and worst-case edge, in that order"
            if alternatives
            else "it is the only surviving expression of this view in this game"
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
    }


def _awaiting_judgement(evaluations: list[GameEvaluation]) -> list[dict[str, Any]]:
    """Unusual markets the handicap could not price, surfaced rather than buried.

    Capped per GAME rather than overall, and the cap is stated in the artifact.
    A game with two hundred unpriceable player props would otherwise drown the
    file it was written to shrink -- and a family that large is a signal in
    itself, which is why the family counts are reported alongside.
    """
    out: list[dict[str, Any]] = []
    for evaluation in evaluations:
        families: dict[str, int] = {}
        samples: dict[str, dict[str, Any]] = {}
        for row in evaluation.rows:
            if row.get("status") not in (
                EvaluationStatus.UNPRICEABLE_FROM_HANDICAP.value,
                EvaluationStatus.UNPRICEABLE_INSUFFICIENT_DATA.value,
            ):
                continue
            family = str(row.get("family") or "unknown")
            families[family] = families.get(family, 0) + 1
            samples.setdefault(
                family,
                {
                    "example_ticker": row.get("ticker"),
                    "yes_means": row.get("yes_means"),
                    "reason": row.get("reason"),
                },
            )
        for family, count in sorted(families.items()):
            out.append(
                {
                    "game_key": evaluation.game_key,
                    "family": family,
                    "contracts": count,
                    **samples[family],
                }
            )
    return out


def build_candidate_artifact(
    shard: str | None,
    evaluations: list[GameEvaluation],
    packets: dict[str, dict[str, Any]],
    handicaps: dict[str, HandicapPayload],
    *,
    min_net_edge: float,
    batch: str | None = None,
    top_n: int | None = None,
) -> dict[str, Any]:
    """The small file a final review reads.

    Raises `ShardGateError` on any INCOMPLETE game, for the same reason the
    full report does: a shortlist drawn from a partial scan is worse than no
    shortlist, because it looks exactly like a complete one.
    """
    incomplete = [e for e in evaluations if not e.complete]
    if incomplete:
        raise ShardGateError(
            f"{len(incomplete)} INCOMPLETE game(s) in {batch or shard!r}; no candidate artifact "
            "may be produced until every game passes its completion gate: "
            + "; ".join(
                f"{e.game_key} unaccounted={e.unaccounted} missing={e.missing_tickers or '-'}"
                for e in incomplete
            )
        )

    rows = [
        row
        for evaluation in evaluations
        for row in evaluation.rows
        if row.get("status") in CANDIDATE_STATUSES
    ]
    reduction = reduce_candidates(rows)

    disagreements = {
        evaluation.game_key: measure(
            evaluation.game_key,
            [r for r in evaluation.rows if r.get("fair_probability_yes") is not None],
        ).as_dict()
        for evaluation in evaluations
    }

    survivors = reduction.survivors
    shown = survivors if top_n is None else survivors[:top_n]

    status_counts: dict[str, int] = {}
    for evaluation in evaluations:
        for status, count in evaluation.status_counts.items():
            status_counts[status] = status_counts.get(status, 0) + count

    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "shard": shard,
        "batch": batch,
        "generated_at": datetime.now(UTC).isoformat(),
        "min_net_edge": min_net_edge,
        "how_to_read": [
            "Every contract in the batch was priced or explicitly marked unpriceable before this "
            "file existed. The reconciliation below is the proof.",
            "robust_positive_ev means the fee-adjusted edge clears the bar at EVERY corner of the "
            "handicap's own stated uncertainty region. sensitive_positive_ev means it does not, "
            "or that no region was stated -- read robustness_reason for which.",
            "Contracts in one correlation_group are the same opinion at different prices. Two "
            "different tickers are not diversification.",
            "market_disagreement EXTREME is a prompt to re-check home/away orientation, period and "
            "units before betting. It is never a reason to discard a handicap.",
        ],
        "reconciliation": {
            "games": len(evaluations),
            "eligible_contracts": sum(e.eligible for e in evaluations),
            "evaluated_contracts": sum(e.evaluated for e in evaluations),
            "explicitly_unpriceable_contracts": sum(e.unpriceable for e in evaluations),
            "unaccounted_contracts": sum(e.unaccounted for e in evaluations),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "reduction": {
            "candidate_rows_before_reduction": len(rows),
            "surviving_candidates": len(survivors),
            "removed": len(reduction.removed),
            "removed_by_reason": reduction.counts,
            "display_truncated_to": top_n,
            "no_cap_note": (
                "Reduction removes only contracts that LOST TO A NAMED SURVIVOR for a "
                "deterministic reason; every removal is in reduction_ledger below. There is no "
                "top-N inside it. `display_truncated_to` is a terminal-reading convenience and is "
                "null unless --top was passed."
            ),
        },
        "exposure": exposure_groups(list(survivors)),
        "candidates": [
            candidate_record(
                expression, handicaps, packets, reduction=reduction, disagreements=disagreements
            )
            for expression in shown
        ],
        "reduction_ledger": reduction.removed,
        "market_disagreement_by_game": [
            disagreements[key] for key in sorted(disagreements)
        ],
        "awaiting_explicit_judgement": _awaiting_judgement(evaluations),
        "disclaimer": (
            "No edge is claimed. Every fair probability here came from a handicap supplied from "
            "outside this repository; the code checked arithmetic, coverage and sensitivity, "
            "nothing else. No order can be placed from this artifact."
        ),
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
        if row.get("status") in CANDIDATE_STATUSES
    ]
    reduction = reduce_candidates(positive)
    survivors = reduction.survivors
    final = survivors if top_n is None else survivors[:top_n]

    disagreements = {
        evaluation.game_key: measure(
            evaluation.game_key,
            [r for r in evaluation.rows if r.get("fair_probability_yes") is not None],
        ).as_dict()
        for evaluation in evaluations
    }

    status_counts: dict[str, int] = {}
    for evaluation in evaluations:
        for status, count in evaluation.status_counts.items():
            status_counts[status] = status_counts.get(status, 0) + count

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
        "robust_positive_ev_contracts": status_counts.get(
            EvaluationStatus.ROBUST_POSITIVE_EV.value, 0
        ),
        "sensitive_positive_ev_contracts": status_counts.get(
            EvaluationStatus.SENSITIVE_POSITIVE_EV.value, 0
        ),
        "not_robust_contracts": status_counts.get(EvaluationStatus.NOT_ROBUST.value, 0),
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
        "status_counts": dict(sorted(status_counts.items())),
        "provenance": (
            f"The {len(final)} final bet(s) are survivors of {totals['eligible_contracts']} "
            f"exhaustive contract dispositions, not markets selected for evaluation."
        ),
        "disclaimer": (
            "No edge is claimed. Every fair probability here came from a handicap supplied from "
            "outside this repository; the code checked arithmetic, coverage and sensitivity, "
            "nothing else. No order can be placed from this artifact."
        ),
        "games": [e.summary() for e in evaluations],
        "market_disagreement_by_game": [disagreements[key] for key in sorted(disagreements)],
        "final_bets": [
            candidate_record(
                c, handicaps, packets, reduction=reduction, disagreements=disagreements
            )
            for c in final
        ],
        "dominated_duplicates": reduction.removed,
        "reduction_by_reason": reduction.counts,
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
        f"Robust positive-EV:         {totals['robust_positive_ev_contracts']}",
        f"Sensitive positive-EV:      {totals['sensitive_positive_ev_contracts']}",
        f"Not robust (excluded):      {totals['not_robust_contracts']}",
        f"Candidate contracts:        {totals['positive_ev_contracts']}",
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
                f"   robustness: {bet['robustness']} (edge range {bet['fee_adjusted_edge_range']})",
                f"   bet up to: {bet['bet_up_to_price']}",
                f"   handicap confidence: {bet['handicap_confidence']} | stake: {bet['stake_placeholder']}",
                f"   thesis: {bet['game_thesis']}",
                f"   why this market: {bet['why_this_market_expresses_the_thesis']}",
                f"   strongest opposing case: {bet['strongest_opposing_case']}",
                "",
            ]
        )
    lines.append(report["disclaimer"])
    return "\n".join(lines)
