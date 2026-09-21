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

import hashlib
import json
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
    DEFAULT_MIN_NET_EDGE,
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


def handicap_fingerprint(handicap: HandicapPayload | None) -> str | None:
    """A stable digest of the OPINION, so a later study can group by it.

    Over the numbers a handicap actually asserts -- the per-period
    distributions, their uncertainty regions, the explicit probabilities and
    their ranges -- and nothing else. Not the prose, which can be reworded
    without changing a single price, and not the confidence, which is recorded
    beside it in its own field.

    Two recommendations sharing this digest came from the same stated view of
    the game. That is what makes "was this handicapper calibrated?" a question
    with an answer rather than a feeling.
    """
    if handicap is None:
        return None
    material: list[Any] = []
    for period in sorted(handicap.period_distributions):
        dist = handicap.period_distributions[period]
        unc = getattr(dist, "uncertainty", None)
        material.append(
            (
                period,
                round(dist.home_mean, 6),
                round(dist.away_mean, 6),
                round(dist.home_sd, 6),
                round(dist.away_sd, 6),
                round(dist.correlation, 6),
                None
                if unc is None
                else (
                    round(unc.margin_points, 6),
                    round(unc.total_points, 6),
                    round(unc.sd_scale_low, 6),
                    round(unc.sd_scale_high, 6),
                    bool(unc.stated),
                ),
            )
        )
    material.append(
        sorted((k, round(float(v), 6)) for k, v in (handicap.explicit_probabilities or {}).items())
    )
    material.append(
        sorted(
            (k, [round(float(x), 6) for x in v])
            for k, v in (handicap.explicit_probability_ranges or {}).items()
        )
    )
    blob = json.dumps(material, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def recommendation_id(
    *, batch: str | None, shard: str, game_key: str, ticker: str, side: str,
    packet_hash: str | None, handicap_hash: str | None,
) -> str:
    """A stable join key for ONE recommendation.

    Deterministic: regenerating the same batch from the same packet and the
    same handicap yields the same id, so a re-run is recognisable as the same
    recommendation rather than a new one. It changes when the quote universe
    changes (packet_hash) or when the opinion changes (handicap_hash), because
    those genuinely are different recommendations about the same contract.

    It says nothing about whether the bet was taken. Execution lives in the
    accounting ledger and is linked afterwards, never merged in.
    """
    blob = "|".join(
        str(part) for part in
        (batch or shard, game_key, ticker, side, packet_hash or "", handicap_hash or "")
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:20]


def min_net_edge_provenance(min_net_edge: float) -> dict[str, object]:
    """Where the required edge came from, and what it is NOT.

    `"min_net_edge": 0.02` in a machine-readable artifact reads like a finding.
    It never was one: this repository has never validated a bar at which a CFB
    Kalshi edge is real, and any reader -- human or model -- who treats that
    number as calibrated is being misled by the file rather than by anything
    anybody wrote. So the number travels with its own provenance.
    """
    operator_supplied = abs(min_net_edge - DEFAULT_MIN_NET_EDGE) > 1e-12
    return {
        "source": "operator" if operator_supplied else "repository_default",
        "is_validated_threshold": False,
        "note": (
            "An operator preference, not a calibrated bar. This repository has never "
            "established the edge at which a CFB Kalshi contract is profitable, and "
            "robustness measures sensitivity to the handicap's stated uncertainty -- "
            "NOT whether the fair probability itself is calibrated."
        ),
    }


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
    max_alternatives: int | None = None,
    batch: str | None = None,
    shard: str | None = None,
) -> dict[str, Any]:
    row = candidate.row
    game_key = str(row.get("game_key"))
    packet = packets.get(game_key) or {}
    side = str(row.get("best_side"))
    sensitivity = row.get("sensitivity") or {}
    ticker = str(row.get("ticker"))
    alternatives = (reduction.alternatives.get(ticker, []) if reduction else [])
    # The NEAREST alternatives by fee-adjusted edge, not the first few by
    # ticker: a reader deciding whether to take a different rung wants the
    # rungs that nearly won, and the rest are in the reduction ledger with
    # every one of their reasons.
    shown = (
        sorted(alternatives, key=lambda a: -float(a.get("fee_adjusted_edge") or 0.0))[
            :max_alternatives
        ]
        if max_alternatives is not None
        else alternatives
    )
    return {
        # The join key a later calibration study groups on. Deterministic, so
        # regenerating an unchanged batch is recognisable as the SAME
        # recommendation; sensitive to the quote universe and to the opinion,
        # because a different price or a different handicap is a different
        # recommendation about the same contract.
        "recommendation_id": recommendation_id(
            batch=batch,
            shard=shard or "",
            game_key=game_key,
            ticker=ticker,
            side=side.upper(),
            packet_hash=packet.get("packet_hash"),
            handicap_hash=handicap_fingerprint(handicaps.get(game_key)),
        ),
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
        # ---- where the numbers came from --------------------------------
        # The per-GAME facts -- thesis, opposing case, confidence, factual data
        # quality, the full market-disagreement reading -- are hoisted into the
        # artifact's `games` block and joined on `game_key`. They are identical
        # on every candidate from one game, and repeating a twelve-domain
        # coverage map on twenty-four rows is a third of the file for no
        # information. What stays inline is the DISAGREEMENT LEVEL, because it
        # is one word and it is the one a reader acts on without scrolling.
        "market_disagreement_level": ((disagreements or {}).get(game_key) or {}).get("level"),
        # ---- exposure and reduction -------------------------------------
        "correlation_group": f"{candidate.driver} / {candidate.direction}",
        "related_alternatives": shown,
        "related_alternatives_total": len(alternatives),
        "why_this_expression_survived": (
            f"it is the best expression of the {candidate.driver} / {candidate.direction} view in "
            f"this game: it beat {len(alternatives)} other contract(s) on fee-adjusted edge, entry "
            "fee, robustness and worst-case edge, in that order"
            if alternatives
            else "it is the only surviving expression of this view in this game"
        ),
        "stake_placeholder": None,
        "why_this_market_expresses_the_thesis": _why_this_market(row),
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


#: Conventions repeated on every candidate, stated ONCE.
#:
#: Same compaction the analysis artifact already applies to the fee model and
#: the price conventions: a sentence identical on 550 rows is not information,
#: it is a third of the file. What is NOT hoisted is anything whose value
#: differs per candidate -- the robustness reason, the worst scenario, the
#: opposing case -- because those are the lines that decide whether a row is
#: read correctly.
CANDIDATE_CONVENTIONS = {
    "bet_up_to_price": (
        "the highest price at which this side still clears the operator's required edge, computed "
        "at the quoted fee. Kalshi's trade fee rises with price, so the true ceiling is slightly "
        "below the figure shown."
    ),
    "stake_placeholder": (
        "the operator sets this. Nothing in this repository sizes a bet, and the exposure block "
        "below exists so that sizing can see which candidates are one opinion."
    ),
    "handicap_confidence": (
        "this repository has no validated confidence-grade convention, so the handicapper's own "
        "stated confidence is reported verbatim rather than translated into a grade. "
        "`handicap_confidence_effective` is that word after the factual data quality caps it."
    ),
    "related_alternatives": (
        "the nearest rungs of the same view by fee-adjusted edge. The COMPLETE list, with every "
        "removal's deterministic reason, is in the reduction ledger written beside this file."
    ),
    "fee_adjusted_edge_range": (
        "the edge at the best and worst corner of the handicap's own stated uncertainty region. "
        "`sensitivity_bound: exact_corner_extremum` means the worst corner IS the worst case; "
        "`grid_extremum` means it is the worst of the corners tested; `not_tested` means the "
        "handicap stated no region, and the candidate cannot be robust."
    ),
}

#: How many alternative rungs a candidate carries inline.
#:
#: Five rather than all of them. A ladder can have twenty-nine rungs and a
#: reader choosing between them is choosing among the few that nearly won; the
#: other twenty-four are audit, and audit belongs in the ledger file.
DEFAULT_MAX_INLINE_ALTERNATIVES = 5


def game_block(
    game_key: str,
    packets: dict[str, dict[str, Any]],
    handicaps: dict[str, HandicapPayload],
    disagreements: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Everything a candidate would otherwise repeat, stated once per game.

    The same compaction the analysis artifact applies to the fee model: a
    twelve-domain coverage map and a two-paragraph thesis are identical on
    every candidate from one game, and repeating them is file size rather than
    information. Candidates join on `game_key`, and the artifact says so.
    """
    packet = packets.get(game_key) or {}
    handicap = handicaps.get(game_key)
    context = packet.get("factual_context") or {}
    return {
        "game": packet.get("title") or game_key,
        "kickoff": packet.get("kickoff"),
        "thesis": (
            (handicap.thesis or handicap.assumptions or "not supplied by the handicap")
            if handicap
            else "not supplied by the handicap"
        ),
        "strongest_opposing_case": (
            handicap.opposing_case
            if handicap and handicap.opposing_case
            else "not supplied by the handicap"
        ),
        "handicap_confidence": handicap.confidence if handicap else "unstated",
        "handicap_confidence_effective": handicap.effective_confidence if handicap else "unstated",
        "handicap_confidence_capped_by_data": (
            handicap.confidence_was_capped if handicap else False
        ),
        "handicap_schema_version": handicap.schema_version_supplied if handicap else None,
        "uncertainty_stated": (not handicap.is_legacy_point_estimate) if handicap else False,
        "factual_data_quality": {
            "confidence_ceiling": (context.get("data_quality") or {}).get("confidence_ceiling"),
            "missing_domains": context.get("missing_domains") or [],
            "coverage": context.get("coverage") or {},
        },
        "market_disagreement": disagreements.get(game_key),
        # PROVENANCE, for a calibration study nobody can run yet.
        #
        # Whether a fair probability was well calibrated can only be answered
        # later, against outcomes, and only if the inputs it came from can be
        # reconstructed exactly. A recommendation that cannot be tied back to
        # the packet and the facts it was formed from is an anecdote.
        "provenance": {
            "packet_hash": packet.get("packet_hash"),
            "market_universe_hash": packet.get("market_universe_hash"),
            "context_hash": context.get("context_hash"),
            "material_context_hash": context.get("material_context_hash"),
            "handicap_hash": handicap_fingerprint(handicap),
            "context_collected_at": context.get("collected_at"),
        },
    }


def build_reduction_ledger(
    shard: str | None,
    batch: str | None,
    reduction: Reduction,
) -> dict[str, Any]:
    """The complete audit of what the reduction removed and why.

    A SIBLING FILE, not a section. It is the larger half of the output by some
    margin -- 139 KB against 40 KB of candidates on the retained slate -- and
    an artifact built for fast review should not carry its own audit trail
    inline. Nothing is lost: every removed contract is here, with the survivor
    it lost to and the deterministic reason, and the candidate artifact points
    at this file by name.
    """
    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "kind": "reduction_ledger",
        "shard": shard,
        "batch": batch,
        "generated_at": datetime.now(UTC).isoformat(),
        "removed": len(reduction.removed),
        "removed_by_reason": reduction.counts,
        "survivors": [e.ticker for e in reduction.survivors],
        "entries": reduction.removed,
        "note": (
            "Every entry lost to the NAMED survivor in `lost_to` for the deterministic reason in "
            "`reason`. There is no top-N in the reduction: a contract is here because another "
            "contract expressing the same view was a better buy, never because a list was full."
        ),
    }


def build_candidate_artifact(
    shard: str | None,
    evaluations: list[GameEvaluation],
    packets: dict[str, dict[str, Any]],
    handicaps: dict[str, HandicapPayload],
    *,
    min_net_edge: float,
    batch: str | None = None,
    top_n: int | None = None,
    max_inline_alternatives: int | None = DEFAULT_MAX_INLINE_ALTERNATIVES,
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
        "min_net_edge_provenance": min_net_edge_provenance(min_net_edge),
        "conventions": CANDIDATE_CONVENTIONS,
        "reduction_ledger_file": f"{batch or shard}.reduction.json",
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
            "A candidate's per-game facts -- thesis, opposing case, confidence, factual data "
            "quality, the full disagreement reading -- are in `games[game_key]`, stated once. "
            "They are identical on every candidate from that game.",
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
        "games": {
            evaluation.game_key: game_block(
                evaluation.game_key, packets, handicaps, disagreements
            )
            for evaluation in evaluations
        },
        "exposure": exposure_groups(list(survivors)),
        "candidates": [
            candidate_record(
                expression,
                handicaps,
                packets,
                reduction=reduction,
                disagreements=disagreements,
                max_alternatives=max_inline_alternatives,
                batch=batch,
                shard=shard,
            )
            for expression in shown
        ],
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
        "min_net_edge_provenance": min_net_edge_provenance(min_net_edge),
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
        "games_context": {
            e.game_key: game_block(e.game_key, packets, handicaps, disagreements)
            for e in evaluations
        },
        "market_disagreement_by_game": [disagreements[key] for key in sorted(disagreements)],
        "final_bets": [
            candidate_record(
                c, handicaps, packets, reduction=reduction,
                disagreements=disagreements, shard=shard,
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
    games = report.get("games_context") or {}
    for index, bet in enumerate(report["final_bets"], start=1):
        game = games.get(bet["game_key"], {})
        lines.extend(
            [
                f"{index}. {bet['game']} -- {bet['market']}",
                f"   side {bet['side']}: {bet['side_means']}",
                f"   price {bet['kalshi_executable_price']} (implied {bet['implied_probability']}) "
                f"vs fair {bet['fair_probability']}",
                f"   raw edge {bet['raw_edge']} | fee {bet['fee']} | fee-adjusted {bet['fee_adjusted_edge']}",
                f"   robustness: {bet['robustness']} (edge range {bet['fee_adjusted_edge_range']})",
                f"   bet up to: {bet['bet_up_to_price']}",
                f"   handicap confidence: {game.get('handicap_confidence', 'unstated')} | "
                f"stake: {bet['stake_placeholder']}",
                f"   thesis: {game.get('thesis', 'not supplied by the handicap')}",
                f"   why this market: {bet['why_this_market_expresses_the_thesis']}",
                f"   strongest opposing case: "
                f"{game.get('strongest_opposing_case', 'not supplied by the handicap')}",
                "",
            ]
        )
    lines.append(report["disclaimer"])
    return "\n".join(lines)
