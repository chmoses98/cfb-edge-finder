"""Point-in-time review of the candidate artifact: the RUN CFB wiring of the 2026-11-02 postmortem fixes.

*** WHERE THIS RUNS ***
``python -m cfb_edge_finder.execution candidates`` (and ``report``) is the step that turns a completed
evaluation into the artifact a RUN CFB session reads before it returns a card (RUN_CFB_CONTRACT.md,
"ChatGPT full-slate handoff"). ``prepare-live`` already checks capture age and kickoff when the slate is
BUILT, but candidates are emitted hours later, after handicapping. This module re-checks every
candidate at the instant the artifact is produced (``--as-of``, default the wall clock) and attaches, per
candidate, additive fields:

* ``pit_review``     point-in-time guard verdict (projection / availability / quote / provenance / kickoff)
* ``fee_inclusive``  break-even probability for a ONE-UNIT order, using the real per-ORDER Kalshi fee
* ``fair_value``     the handicap's stated probability bounds, their calibration status, and a
                     conservative bet-up-to computed from the LOWER bound -- or ``None`` with a reason
* ``exposure_context`` units already held on this game / day (from the ledger), in $75 units

*** WHAT IT CAN AND CANNOT DO ***
RESTRICTION-ONLY. It can mark a candidate ``actionable: false`` with named reasons. It never adds a
candidate, never re-ranks, never sizes, and never edits a legacy field: ``bet_up_to_price`` is left exactly
as the evaluator produced it, with its basis stated beside it, so history and the old artifact reader are
unchanged. Exposure over $375/game or $1,125/day is a FLAG for a human; nothing is capped.

*** NOTHING IS INVENTED ***
No handicap probability is "calibrated" here: the repository holds no settled prospective reliability
table. ``qualified_positive_ev`` is therefore ``False`` unless the caller supplies a reliability table,
and ``conservative_bet_up_to`` is ``None`` whenever the handicap stated no uncertainty region.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cfb_edge_finder.decision.exposure_aggregation import (
    DAY_FLAG_UNITS,
    GAME_FLAG_UNITS,
    STANDARD_UNIT_DOLLARS,
    Position,
    aggregate_exposure,
    flag_thresholds_dollars,
)
from cfb_edge_finder.decision.owner_rules import auburn_exclusion, clamp_stake
from cfb_edge_finder.decision.pit_guards import (
    DEFAULT_KICKOFF_BUFFER,
    DEFAULT_MAX_PROJECTION_AGE,
    PitInputs,
    PitPolicy,
    PriceProvenance,
    evaluate_pit_guards,
)
from cfb_edge_finder.execution.context import FRESHNESS_BUDGET_SECONDS
from cfb_edge_finder.execution.disposition import DEFAULT_MAX_CAPTURE_AGE_MINUTES
from cfb_edge_finder.expression.fair_value import (
    CalibrationStatus,
    ProbabilityEstimate,
    ReliabilityBin,
    bet_up_to,
    calibrated_probability,
    fee_inclusive_break_even,
    order_fee_dollars,
)

PIT_REVIEW_VERSION = "cfb_pit_review/1.1.0"

#: Basis string stamped beside the legacy ``bet_up_to_price`` so a reader can see what it is and is not.
LEGACY_BET_UP_TO_BASIS = (
    "BASE_CASE_HANDICAP_PROBABILITY_AT_QUOTED_PRICE_FEE_UNCALIBRATED: fair probability minus the fee at the "
    "QUOTED price minus the required edge. Not a lower bound, not calibrated, and the fee is not "
    "re-evaluated at the ceiling price. Kept unchanged for history; use fair_value.conservative_bet_up_to."
)


def cfb_policy(
    *, max_quote_age: timedelta | None = None, max_projection_age: timedelta | None = None
) -> PitPolicy:
    """CFB's guard policy, taken from budgets this repository ALREADY defines (no new magic numbers).

    * quote: the slate's capture-age budget (``DEFAULT_MAX_CAPTURE_AGE_MINUTES``) -- the same bar
      ``prepare-live`` applied when the slate was built, now re-applied at decision time;
    * availability: the context layer's own ``FRESHNESS_BUDGET_SECONDS['availability']``;
    * projection: the PIT default (12h); kickoff buffer: the PIT default.
    CFB has no confirmed-QB source, so confirmation is a disclosed note, not a block.
    """
    return PitPolicy(
        max_projection_age=max_projection_age or DEFAULT_MAX_PROJECTION_AGE,
        max_availability_age=timedelta(seconds=FRESHNESS_BUDGET_SECONDS["availability"]),
        max_quote_age=max_quote_age or timedelta(minutes=DEFAULT_MAX_CAPTURE_AGE_MINUTES),
        kickoff_buffer=DEFAULT_KICKOFF_BUFFER,
        require_qb_confirmation=False,
    )


def parse_dt(value: Any) -> datetime | None:
    """Timezone-aware UTC, or None. A naive timestamp is treated as missing, not guessed to be UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(UTC)


def load_reliability_bins(path: Path | None) -> list[ReliabilityBin] | None:
    """Settled out-of-sample reliability bins ``[{lo, hi, observations, hits}, ...]``; None if no file."""
    if path is None:
        return None
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw.get("bins") if isinstance(raw, dict) else raw
    return [ReliabilityBin(float(r["lo"]), float(r["hi"]), int(r["observations"]), int(r["hits"])) for r in rows]


def load_ledger_positions(path: Path | None, *, dates: Iterable[str] | None = None) -> list[Position]:
    """Positions already held, from an ``app/latest/wagers.json``-shaped export.

    ``dates`` (YYYY-MM-DD) keeps only wagers placed on those US/Eastern-agnostic UTC dates OR whose ticker
    game date matches; the exposure module groups by ticker game date, so we keep everything when None.
    """
    if path is None or not Path(path).exists():
        return []
    items = json.loads(Path(path).read_text(encoding="utf-8")).get("items") or []
    wanted = set(dates) if dates else None
    out: list[Position] = []
    for w in items:
        ticker, side, stake = w.get("kalshi_ticker"), w.get("selection"), w.get("stake")
        if not ticker or side not in ("YES", "NO") or not isinstance(stake, (int, float)):
            continue
        placed = str(w.get("placed_at") or "")[:10] or None
        if wanted is not None and placed not in wanted:
            continue
        out.append(Position(ticker=ticker, side=side, stake_dollars=float(stake), placed_date=placed))
    return out


@dataclass(frozen=True)
class ReviewContext:
    decision_time: datetime
    quote_captured_at: datetime | None
    packets: dict[str, dict[str, Any]]
    handicaps: dict[str, Any]
    unit_dollars: float = STANDARD_UNIT_DOLLARS
    policy: PitPolicy = field(default_factory=cfb_policy)
    reference_units: float = 1.0
    existing_positions: tuple[Position, ...] = ()
    proposed_stakes: dict[str, float] = field(default_factory=dict)
    """recommendation_id (or market ticker) -> proposed dollars. Absent = exposure of the proposal is
    NOT_ASSESSED; it is never assumed."""
    reliability_bins: tuple[ReliabilityBin, ...] | None = None
    min_net_edge: float = 0.0


def _series(ticker: str) -> str:
    return ticker.split("-", 1)[0]


def _availability_observed_at(packet: dict[str, Any]) -> datetime | None:
    domains = ((packet.get("factual_context") or {}).get("domains") or {})
    field_ = domains.get("availability") or {}
    if field_.get("quality") == "missing":
        return None
    return parse_dt(field_.get("observed_at"))


def _estimate(candidate: dict[str, Any], ctx: ReviewContext) -> tuple[ProbabilityEstimate, dict[str, Any]]:
    """The handicap's stated bounds -> a calibration-honest estimate.

    CALIBRATED only through a reliability table built from settled outcomes. Otherwise the point/lower/upper
    are reported as the handicap's own STATED values, never as calibrated ones.
    """
    raw = candidate.get("fair_probability")
    stated = candidate.get("fair_probability_range") or [None, None]
    low, high = (stated + [None, None])[:2]
    bins = list(ctx.reliability_bins) if ctx.reliability_bins else None
    est = calibrated_probability(raw, bins)
    info = {
        "handicap_point": raw,
        "handicap_stated_low": low,
        "handicap_stated_high": high,
        "uncertainty_region_stated": bool(candidate.get("scenarios_tested") and candidate.get("scenarios_tested") > 1),
        "calibration_status": est.status.value,
        "calibrated_low": est.lower,
        "calibrated_high": est.upper,
        "calibration_bin_observations": est.bin_observations,
    }
    return est, info


def _stated_lower_bet_up_to(
    low: float | None, contracts: float, series: str, required_margin: float
) -> float | None:
    """Highest whole-cent price where the STATED lower bound still clears per-order break-even + margin."""
    if low is None or contracts <= 0:
        return None
    best: float | None = None
    for cents in range(1, 100):
        price = cents / 100
        be = fee_inclusive_break_even(price, contracts, series)
        if be is not None and low - be >= required_margin:
            best = price
    return best


def review_candidate(candidate: dict[str, Any], ctx: ReviewContext) -> dict[str, Any]:
    """Additive review fields for ONE candidate. Pure; never mutates ``candidate``."""
    game_key = str(candidate.get("game_key"))
    packet = ctx.packets.get(game_key) or {}
    handicap = ctx.handicaps.get(game_key)
    entry = candidate.get("kalshi_executable_price")
    ticker = str(candidate.get("market"))
    series = _series(ticker)

    # ---- point-in-time guards ------------------------------------------------------------------------
    provenance = PriceProvenance.EXECUTABLE_ASK if isinstance(entry, (int, float)) else PriceProvenance.UNKNOWN
    guard = evaluate_pit_guards(
        PitInputs(
            decision_time=ctx.decision_time,
            kickoff=parse_dt(candidate.get("kickoff") or packet.get("kickoff")),
            projection_as_of=parse_dt(getattr(handicap, "handicapped_at", None)),
            availability_observed_at=_availability_observed_at(packet),
            qb_confirmed=None,
            quote_captured_at=ctx.quote_captured_at,
            price_provenance=provenance,
        ),
        ctx.policy,
    )
    blocking = [g.value for g in guard.blocking]
    # ---- owner rules (restriction-only, named, visible): never against Auburn ------------------------
    owner = auburn_exclusion(ticker, str(candidate.get("side") or ""), game_title=packet.get("title"))
    if owner.blocked and owner.reason:
        blocking.append(owner.reason)
    quote_age = (
        round((ctx.decision_time - ctx.quote_captured_at).total_seconds())
        if ctx.quote_captured_at is not None
        else None
    )

    # ---- fee-inclusive break-even at ONE UNIT --------------------------------------------------------
    reference_dollars = ctx.unit_dollars * ctx.reference_units
    contracts = None
    order_fee = None
    break_even = None
    if isinstance(entry, (int, float)) and 0.0 < entry < 1.0:
        contracts = round(reference_dollars / entry, 2)
        order_fee = order_fee_dollars(entry, contracts, series)
        break_even = fee_inclusive_break_even(entry, contracts, series)
    fee_block = {
        "reference_order_dollars": reference_dollars,
        "reference_contracts": contracts,
        "executable_price": entry,
        "order_fee_dollars": order_fee,
        "break_even_probability": None if break_even is None else round(break_even, 6),
        "fee_basis": "Kalshi taker fee rounded UP once per ORDER (expression/fair_value.order_fee_dollars)",
        "price_provenance": "CAPTURED_EXECUTABLE_ASK: the ask for the side bought at the catalog capture time, "
        "not a live quote",
    }

    # ---- fair-value bounds + conservative bet-up-to --------------------------------------------------
    est, info = _estimate(candidate, ctx)
    low = candidate.get("fair_probability_range", [None, None])[0] if info["uncertainty_region_stated"] else None
    conservative: float | None = None
    reason: str | None = None
    basis: str | None = None
    if est.usable:
        conservative = bet_up_to(est, contracts or 1.0, series, required_margin=ctx.min_net_edge)
        basis = "CALIBRATED_LOWER_BOUND"
        if conservative is None:
            reason = "NO_PRICE_CLEARS_BREAK_EVEN_AT_CALIBRATED_LOWER_BOUND"
    elif low is not None and contracts:
        conservative = _stated_lower_bet_up_to(float(low), contracts, series, ctx.min_net_edge)
        basis = "HANDICAP_STATED_LOWER_BOUND_UNCALIBRATED"
        if conservative is None:
            reason = "NO_PRICE_CLEARS_BREAK_EVEN_AT_STATED_LOWER_BOUND"
    else:
        reason = "NO_STATED_UNCERTAINTY_REGION" if candidate.get("fair_probability") is not None else (
            "NO_PROBABILITY"
        )
    legacy = candidate.get("bet_up_to_price")
    fair_block = {
        **info,
        "conservative_bet_up_to": conservative,
        "conservative_bet_up_to_basis": basis,
        "conservative_bet_up_to_missing_reason": reason,
        "legacy_bet_up_to_price": legacy,
        "legacy_bet_up_to_basis": LEGACY_BET_UP_TO_BASIS,
        "legacy_exceeds_conservative": (
            None if legacy is None or conservative is None else bool(legacy > conservative)
        ),
        "within_conservative_bet_up_to": (
            None if conservative is None or not isinstance(entry, (int, float)) else bool(entry <= conservative)
        ),
        "qualified_positive_ev": bool(
            est.usable
            and break_even is not None
            and est.lower is not None
            and est.lower - break_even >= ctx.min_net_edge
        ),
        "qualification_note": (
            "True only when the probability is CALIBRATED from settled outcomes and its lower bound clears "
            "the fee-inclusive break-even. Without a reliability table this is False by construction: a "
            "handicap's stated range is an opinion about uncertainty, not a measured one."
        ),
    }

    proposed = ctx.proposed_stakes.get(str(candidate.get("recommendation_id"))) or ctx.proposed_stakes.get(ticker)
    sizing = clamp_stake(float(proposed) if proposed else None, unit_dollars=ctx.unit_dollars).as_dict()
    return {
        "owner_rules": {"auburn": owner.as_dict(), "sizing": sizing},
        "pit_review": {
            "version": PIT_REVIEW_VERSION,
            "actionable": bool(guard.actionable and not owner.blocked),
            "blocking_guards": blocking,
            "notes": list(guard.notes),
            "decision_time": ctx.decision_time.isoformat(),
            "quote_captured_at": None if ctx.quote_captured_at is None else ctx.quote_captured_at.isoformat(),
            "quote_age_seconds": quote_age,
            "kickoff": candidate.get("kickoff") or packet.get("kickoff"),
        },
        "fee_inclusive": fee_block,
        "fair_value": fair_block,
    }


def exposure_review(ctx: ReviewContext, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate exposure in $75 units: what is already held, plus any proposal the caller supplied."""
    positions = list(ctx.existing_positions)
    proposed: list[Position] = []
    for cand in candidates:
        stake = ctx.proposed_stakes.get(str(cand.get("recommendation_id"))) or ctx.proposed_stakes.get(
            str(cand.get("market"))
        )
        if stake:
            effective = clamp_stake(float(stake), unit_dollars=ctx.unit_dollars).effective_dollars
            proposed.append(
                Position(
                    str(cand["market"]),
                    str(cand["side"]).upper(),
                    float(effective),
                    ctx.decision_time.date().isoformat(),
                )
            )
    held = aggregate_exposure(positions, unit_dollars=ctx.unit_dollars)
    combined = aggregate_exposure(positions + proposed, unit_dollars=ctx.unit_dollars)
    new_flags = sorted(set(combined.flags) - set(held.flags))
    return {
        "unit_dollars": ctx.unit_dollars,
        "flag_thresholds_dollars": flag_thresholds_dollars(ctx.unit_dollars),
        "flag_thresholds_units": {"per_game": GAME_FLAG_UNITS, "per_day": DAY_FLAG_UNITS},
        "policy": "FLAGS_ONLY_NO_CAPS",
        "positions_already_held": len(positions),
        "proposed_positions": len(proposed),
        "proposal_assessed": bool(proposed),
        "proposal_note": None
        if proposed
        else "NOT_ASSESSED: no --proposed-stakes supplied; the repository never assumes a stake",
        "flags_already_held": held.flags,
        "flags_with_proposal": combined.flags,
        "flags_introduced_by_proposal": new_flags,
        "daily_gross_units": {k: round(v, 3) for k, v in sorted(combined.daily_gross_units.items())},
        "games": {
            k: {
                "gross_units": round(g.gross_units, 3),
                "max_correlated_view_units": round(g.max_view_units, 3),
                "flags": g.flags,
            }
            for k, g in sorted(combined.games.items())
        },
        "undecoded_combo_units": round(combined.undecoded_combo_units, 3),
    }


def annotate_candidate_artifact(
    artifact: dict[str, Any], ctx: ReviewContext, *, key: str = "candidates"
) -> dict[str, Any]:
    """Return ``artifact`` with additive review fields. Restriction-only: no candidate is added, removed or reordered.

    ``key`` names the list of candidate records: ``candidates`` (candidate artifact) or ``final_bets`` (report)."""
    candidates = artifact.get(key) or []
    actionable = 0
    blocked_by: dict[str, int] = {}
    for cand in candidates:
        fields = review_candidate(cand, ctx)
        cand.update(fields)
        if fields["pit_review"]["actionable"]:
            actionable += 1
        for g in fields["pit_review"]["blocking_guards"]:
            blocked_by[g] = blocked_by.get(g, 0) + 1
    exposure = exposure_review(ctx, candidates)
    for cand in candidates:
        held_units = (exposure["games"].get(_game_of(cand)) or {}).get("gross_units")
        cand["exposure_context"] = {
            "game_units_held_or_proposed": held_units,
            "unit_dollars": ctx.unit_dollars,
        }
    artifact["pit_review"] = {
        "version": PIT_REVIEW_VERSION,
        "restriction_only": True,
        "candidates": len(candidates),
        "actionable": actionable,
        "not_actionable": len(candidates) - actionable,
        "blocked_by": dict(sorted(blocked_by.items())),
        "decision_time": ctx.decision_time.isoformat(),
        "policy": {
            "max_quote_age_seconds": ctx.policy.max_quote_age.total_seconds(),
            "max_availability_age_seconds": ctx.policy.max_availability_age.total_seconds(),
            "max_projection_age_seconds": ctx.policy.max_projection_age.total_seconds(),
            "kickoff_buffer_seconds": ctx.policy.kickoff_buffer.total_seconds(),
            "require_qb_confirmation": ctx.policy.require_qb_confirmation,
            "note": "owner-adjustable defaults reused from prepare-live / context freshness budgets",
        },
        "exposure": exposure,
        "reading": (
            "A candidate with actionable=false must NOT be presented as a bet: the named guard(s) say an "
            "input was stale, missing, from the future, not an executable ask, or too close to kickoff. "
            "conservative_bet_up_to replaces bet_up_to_price as the price ceiling to quote; the legacy "
            "field is retained unchanged for history. Exposure flags are for a human to read; nothing is capped."
        ),
    }
    artifact.setdefault("how_to_read", []).append(
        "POINT-IN-TIME REVIEW (`pit_review`): re-checked at artifact time. Quote only candidates with "
        "`pit_review.actionable == true`; quote `fair_value.conservative_bet_up_to` (None = no defensible "
        "ceiling) rather than `bet_up_to_price`; `fair_value.qualified_positive_ev` is False unless the "
        "probability is calibrated from settled outcomes. Exposure >$375/game or >$1,125/day (5U/15U at "
        "1U=$75) is a flag to disclose, not a cap."
    )
    return artifact


def _game_of(cand: dict[str, Any]) -> str | None:
    from cfb_edge_finder.expression.ticker_ontology import parse_ticker

    info = parse_ticker(str(cand.get("market")))
    return info.game_key if info.decoded else None


def context_from_slate(
    *,
    slate: dict[str, Any],
    packets: dict[str, dict[str, Any]],
    handicaps: dict[str, Any],
    decision_time: datetime,
    unit_dollars: float = STANDARD_UNIT_DOLLARS,
    max_quote_age_minutes: float | None = None,
    existing_positions: Iterable[Position] = (),
    proposed_stakes: dict[str, float] | None = None,
    reliability_bins: list[ReliabilityBin] | None = None,
    min_net_edge: float = 0.0,
) -> ReviewContext:
    """Build the review context from the artifacts the workflow already wrote (slate + packets + handicaps)."""
    source = slate.get("source") or {}
    cfg = slate.get("config") or {}
    quote_minutes = max_quote_age_minutes or cfg.get("max_capture_age_minutes")
    policy = cfb_policy(max_quote_age=timedelta(minutes=quote_minutes) if quote_minutes else None)
    return ReviewContext(
        decision_time=decision_time,
        quote_captured_at=parse_dt(source.get("captured_at")),
        packets=packets,
        handicaps=handicaps,
        unit_dollars=unit_dollars,
        policy=policy,
        existing_positions=tuple(existing_positions),
        proposed_stakes=dict(proposed_stakes or {}),
        reliability_bins=tuple(reliability_bins) if reliability_bins else None,
        min_net_edge=min_net_edge,
    )


__all__ = [
    "CalibrationStatus",
    "ReviewContext",
    "annotate_candidate_artifact",
    "context_from_slate",
    "cfb_policy",
    "exposure_review",
    "load_ledger_positions",
    "load_reliability_bins",
    "review_candidate",
]
