"""The exhaustive contract evaluator.

*** THE REQUIREMENT THIS MODULE EXISTS FOR ***
Once a handicap exists for a game, EVERY mechanically eligible contract
mapped to that game is evaluated against it. Not the interesting ones, not
the liquid ones, not the six that look best -- every one. The loop does
not break early, cannot be short-circuited by finding a good bet, and the
completion gate below refuses to mark the game done unless the arithmetic
closes:

    eligible == evaluated + unpriceable      and      unaccounted == 0

*** WHY UNPRICEABLE IS A RESULT AND NOT A FAILURE ***
A first-TD market cannot be priced from a score distribution. Neither can
a 1H/FT double, a tie, or a receiving-touchdown prop. The honest outcome
is to say so, per contract, with a reason -- and to keep counting it. A
system that quietly drops what it cannot price reports 100% coverage of
whatever it happened to understand.

*** EVERY PRICED CONTRACT CARRIES ITS OWN SENSITIVITY ***
A fair probability is priced at the handicap's base case AND at every corner
of the uncertainty region the handicap stated. The row therefore carries the
worst fair value and the worst fee-adjusted edge alongside the base ones, and
its terminal status is a ROBUSTNESS verdict rather than a point-estimate
comparison. `sensitivity.py` owns that taxonomy and the rule that matters
most: a handicap with no stated region can never produce `robust_positive_ev`.

*** NO EDGE IS CLAIMED ANYWHERE ***
`net_edge` is arithmetic on a probability somebody else supplied. It says
what the supplied handicap implies about a quoted price, and nothing about
whether that handicap is any good. `min_net_edge` is an operator input
with no empirical backing in this repository -- see docs and the CLI help.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from statistics import NormalDist
from typing import Any

from cfb_edge_finder.execution import LEDGER_SCHEMA_VERSION
from cfb_edge_finder.execution.handicap import HandicapPayload, PeriodDistribution
from cfb_edge_finder.execution.quality import DataQuality
from cfb_edge_finder.execution.semantics import ContractKind, PricingRequirement, TeamSlot
from cfb_edge_finder.execution.sensitivity import (
    NEGLIGIBLE_EDGE,
    RECOMMENDABLE,
    Robustness,
    Sensitivity,
    bet_up_to,
    evaluate_side,
)
from cfb_edge_finder.execution.uncertainty import Scenario, scenarios
from cfb_edge_finder.projections.distribution import (
    margin_distribution,
    team_total_distribution,
    total_distribution,
)
from cfb_edge_finder.schemas.common import Side
from cfb_edge_finder.schemas.projection import GameDistribution


class EvaluationStatus(StrEnum):
    """One terminal bucket per eligible contract. There is no other outcome.

    The first three replace the single `positive_ev` of schema 1: a bet whose
    edge survives the handicap's own stated error and one that exists only at
    the exact base case are different findings, and collapsing them into one
    status is how a point estimate ends up looking like a conclusion.
    """

    ROBUST_POSITIVE_EV = "robust_positive_ev"
    SENSITIVE_POSITIVE_EV = "sensitive_positive_ev"
    NOT_ROBUST = "not_robust"
    NEGATIVE_EV = "negative_ev"
    ZERO_OR_NEGLIGIBLE_EDGE = "zero_or_negligible_edge"
    BELOW_REQUIRED_EDGE = "below_required_edge"
    TOO_UNCERTAIN = "too_uncertain"
    UNPRICEABLE_FROM_HANDICAP = "unpriceable_from_handicap"
    UNPRICEABLE_INSUFFICIENT_DATA = "unpriceable_insufficient_data"
    NON_EXECUTABLE = "non_executable"
    INVALID_SEMANTICS = "invalid_semantics"


PRICED_STATUSES = frozenset(
    {
        EvaluationStatus.ROBUST_POSITIVE_EV.value,
        EvaluationStatus.SENSITIVE_POSITIVE_EV.value,
        EvaluationStatus.NOT_ROBUST.value,
        EvaluationStatus.NEGATIVE_EV.value,
        EvaluationStatus.ZERO_OR_NEGLIGIBLE_EDGE.value,
        EvaluationStatus.BELOW_REQUIRED_EDGE.value,
        EvaluationStatus.TOO_UNCERTAIN.value,
    }
)
UNPRICEABLE_STATUSES = frozenset(
    {
        EvaluationStatus.UNPRICEABLE_FROM_HANDICAP.value,
        EvaluationStatus.UNPRICEABLE_INSUFFICIENT_DATA.value,
        EvaluationStatus.NON_EXECUTABLE.value,
        EvaluationStatus.INVALID_SEMANTICS.value,
    }
)

#: Statuses a candidate artifact may be built from. Everything else is in the
#: ledger and visible; nothing else is a recommendation.
CANDIDATE_STATUSES = frozenset(
    {
        EvaluationStatus.ROBUST_POSITIVE_EV.value,
        EvaluationStatus.SENSITIVE_POSITIVE_EV.value,
    }
)

#: The operator's required fee-adjusted edge when they do not state one.
#:
#: NEUTRAL BY DEFAULT, AND THAT IS THE POINT. This repository has never
#: validated a bar at which a CFB Kalshi edge is real, so a nonzero default
#: would be an unvalidated threshold applied silently -- and worse, written
#: into every candidate artifact as `min_net_edge`, where a reader could
#: reasonably take it for a finding. It was 0.02 for exactly that reason and
#: 0.02 was never anything but a guess.
#:
#: Zero does not mean "bet everything". A contract still has to clear
#: NEGLIGIBLE_EDGE to be distinguished from noise, and it still has to be
#: positive at EVERY corner of the handicap's uncertainty region to be robust.
#: Those are the filters that were ever doing the work.
DEFAULT_MIN_NET_EDGE = 0.0

#: What the default used to be. Kept so an operator who wants the old
#: behaviour can ask for it by name and see, in the artifact, that they chose
#: it. It is a round number somebody picked; no evidence supports it.
LEGACY_UNVALIDATED_MIN_NET_EDGE = 0.02
"""The operator's required fee-adjusted edge, in probability units.

*** THIS IS NOT A VALIDATED THRESHOLD ***
This repository has never established a bar at which a CFB Kalshi edge is
real, and this constant does not claim one. It is a knob the operator sets
on the command line; 0.02 is the default because a visible default that
the CLI prints back is safer than an invisible 0.0 that makes every
rounding artefact a 'positive EV' bet."""


# --------------------------------------------------------------- pricing


def _game_distribution(period: PeriodDistribution) -> GameDistribution:
    return GameDistribution(
        home_mean=period.home_mean,
        away_mean=period.away_mean,
        home_sd=period.home_sd,
        away_sd=period.away_sd,
        correlation=period.correlation,
    )


def _cutpoint(threshold: float, comparator: str) -> float:
    """Where to cut a continuous approximation of an integer score.

    Football scores are integers, so the probability a contract asks about
    is always P(X >= k) for some integer k. Getting from a strike to that
    k is the only place a half-point line matters:

        "over 16.5"  (greater, half-integer)  -> X >= 17 -> cut at 16.5
        "wins by over 4" (greater, integer)   -> X >= 5  -> cut at 4.5
        "1+ overtimes" (greater_or_equal, 1)  -> X >= 1  -> cut at 0.5

    Applying a blanket 0.5 continuity correction to a strike that is
    ALREADY a half-integer shifts every Kalshi rung by a full point, which
    is a systematic mispricing of the entire ladder in one direction."""
    is_integer = abs(threshold - round(threshold)) < 1e-9
    if comparator in ("greater_or_equal", "less"):
        return threshold - 0.5 if is_integer else threshold
    return threshold + 0.5 if is_integer else threshold


def _prob_at_least(dist: NormalDist, cut: float) -> float:
    return min(1.0, max(0.0, 1.0 - dist.cdf(cut)))


def _signed_margin(dist: NormalDist, team: str) -> NormalDist:
    """`margin_distribution` is home minus away. An away-team contract is
    the same distribution reflected, not a different calculation."""
    if team == TeamSlot.HOME.value:
        return dist
    return NormalDist(-dist.mean, dist.stdev)


@dataclass(frozen=True)
class FairProbability:
    value: float | None
    method: str
    reason: str | None = None


def fair_from_distribution(
    semantics: dict[str, Any],
    period: PeriodDistribution,
) -> FairProbability:
    """YES probability for one contract from ONE period distribution.

    Split out from `fair_probability` so the sensitivity engine can re-run it
    against a perturbed distribution without re-deciding which pricing route
    applies. That decision depends on the CONTRACT, which does not move when
    the handicap does; recomputing it per corner would be eight times the work
    and one more place for the corners to diverge from the base case.
    """
    kind = str(semantics.get("kind"))
    period_name = str(semantics.get("period"))
    distribution = _game_distribution(period)
    threshold = semantics.get("line")
    comparator = str(semantics.get("comparator") or "greater")
    team = str(semantics.get("team") or TeamSlot.NONE.value)

    if kind == ContractKind.MONEYLINE.value:
        margin = _signed_margin(margin_distribution(distribution), team)
        return FairProbability(_prob_at_least(margin, 0.5), f"{period_name}_margin_distribution")

    if kind == ContractKind.SPREAD.value:
        if threshold is None:
            return FairProbability(None, "none", "spread contract carries no rung")
        margin = _signed_margin(margin_distribution(distribution), team)
        return FairProbability(
            _prob_at_least(margin, _cutpoint(float(threshold), comparator)),
            f"{period_name}_margin_distribution",
        )

    if kind == ContractKind.TOTAL.value:
        if threshold is None:
            return FairProbability(None, "none", "total contract carries no rung")
        dist = total_distribution(distribution)
        cut = _cutpoint(float(threshold), comparator)
        if comparator in ("less", "less_or_equal"):
            return FairProbability(1.0 - _prob_at_least(dist, cut), f"{period_name}_total_distribution")
        return FairProbability(_prob_at_least(dist, cut), f"{period_name}_total_distribution")

    if kind == ContractKind.TEAM_TOTAL.value:
        if threshold is None:
            return FairProbability(None, "none", "team-total contract carries no rung")
        if team not in (TeamSlot.HOME.value, TeamSlot.AWAY.value):
            return FairProbability(None, "none", f"team-total contract has team slot {team!r}")
        side = Side.HOME if team == TeamSlot.HOME.value else Side.AWAY
        dist = team_total_distribution(distribution, side)
        cut = _cutpoint(float(threshold), comparator)
        if comparator in ("less", "less_or_equal"):
            return FairProbability(1.0 - _prob_at_least(dist, cut), f"{period_name}_team_total_distribution")
        return FairProbability(_prob_at_least(dist, cut), f"{period_name}_team_total_distribution")

    if kind == ContractKind.MARGIN_BAND.value:
        if threshold is None:
            return FairProbability(None, "none", "margin-band contract carries no band")
        margin = _signed_margin(margin_distribution(distribution), team)
        low = _prob_at_least(margin, _cutpoint(float(threshold), comparator))
        cap = semantics.get("cap")
        if cap is None:
            return FairProbability(low, f"{period_name}_margin_distribution")
        high = _prob_at_least(margin, float(cap) + 0.5)
        return FairProbability(max(0.0, low - high), f"{period_name}_margin_band")

    return FairProbability(None, "none", f"no pricing route for contract kind {kind!r}")


def fair_probability(
    semantics: dict[str, Any],
    ticker: str,
    handicap: HandicapPayload,
) -> FairProbability:
    """YES probability for one contract, or an explicit reason there is none."""
    explicit = handicap.explicit_probabilities.get(ticker)
    if explicit is not None:
        return FairProbability(float(explicit), "explicit_probability")

    kind = str(semantics.get("kind"))
    requires = str(semantics.get("requires"))
    period_name = str(semantics.get("period"))
    family = str(semantics.get("family") or "")

    if requires != PricingRequirement.PERIOD_DISTRIBUTION.value:
        return FairProbability(
            None,
            "none",
            (
                f"contract kind {kind!r} is not derivable from a score distribution and no explicit "
                f"probability was supplied for {ticker}"
            ),
        )

    if family and family in handicap.declared_unpriceable_families:
        return FairProbability(
            None, "none", f"handicap declared family {family!r} unpriceable"
        )

    period = handicap.period_distributions.get(period_name)
    if period is None:
        return FairProbability(
            None,
            "none",
            f"handicap supplied no score distribution for period {period_name!r}",
        )

    return fair_from_distribution(semantics, period)


def _fair_by_scenario(
    semantics: dict[str, Any],
    period: PeriodDistribution,
    grid: tuple[Scenario, ...],
) -> list[tuple[str, float]]:
    """(label, YES fair probability) at every corner, base case first."""
    out: list[tuple[str, float]] = []
    for scenario in grid:
        moved = (
            period
            if scenario.is_base
            else period.perturbed(
                margin_shift=scenario.margin_shift,
                total_shift=scenario.total_shift,
                sd_scale=scenario.sd_scale,
                correlation_shift=scenario.correlation_shift,
            )
        )
        priced = fair_from_distribution(semantics, moved)
        if priced.value is None:
            # Unreachable for a contract whose base case priced, because the
            # route depends only on the contract. Kept as a fail-closed guard:
            # dropping a corner silently would shrink the tested region and
            # inflate the robustness claim.
            return out[:1] if out else []
        out.append((scenario.label, float(priced.value)))
    return out


# ------------------------------------------------------------ evaluation


@dataclass(frozen=True)
class SideEvaluation:
    side: str
    entry: float
    fee: float
    fair_probability: float
    implied_probability: float
    raw_edge: float
    net_edge: float
    ev_per_contract: float
    ev_per_dollar_risked: float
    sensitivity: Sensitivity | None = None

    def as_dict(self) -> dict[str, Any]:
        out = {
            "side": self.side,
            "executable_entry": round(self.entry, 6),
            "fee": round(self.fee, 6),
            "implied_probability": round(self.implied_probability, 6),
            "fair_probability": round(self.fair_probability, 6),
            "raw_edge": round(self.raw_edge, 6),
            "net_edge": round(self.net_edge, 6),
            "ev_per_contract": round(self.ev_per_contract, 6),
            "ev_per_dollar_risked": round(self.ev_per_dollar_risked, 6),
        }
        if self.sensitivity is not None:
            out["sensitivity"] = self.sensitivity.as_dict()
        return out


def _side_evaluation(
    side: str,
    entry: float | None,
    fee: float | None,
    fair: float,
    sensitivity: Sensitivity | None = None,
) -> SideEvaluation | None:
    """EV on a Kalshi binary is as plain as it looks: you pay `entry` plus
    `fee` now and receive $1 if the side settles true.

        EV = fair*(1 - entry) - (1 - fair)*entry - fee = fair - entry - fee

    The fee is charged on entry only, so it comes straight off the edge."""
    if entry is None:
        return None
    fee_value = float(fee or 0.0)
    raw_edge = fair - entry
    net_edge = raw_edge - fee_value
    return SideEvaluation(
        side=side,
        entry=float(entry),
        fee=fee_value,
        fair_probability=fair,
        implied_probability=float(entry),
        raw_edge=raw_edge,
        net_edge=net_edge,
        ev_per_contract=net_edge,
        ev_per_dollar_risked=net_edge / (float(entry) + fee_value) if (entry + fee_value) > 0 else 0.0,
        sensitivity=sensitivity,
    )


@dataclass
class GameEvaluation:
    game_key: str
    packet_hash: str | None
    market_universe_hash: str | None
    eligible: int
    rows: list[dict[str, Any]] = field(default_factory=list)
    status_counts: dict[str, int] = field(default_factory=dict)
    evaluated_at: str = ""
    missing_tickers: list[str] = field(default_factory=list)
    """Eligible contracts that produced no row. Named, not merely counted:
    "3 unaccounted" that cannot be named is not a diagnosis."""
    duplicate_tickers: list[str] = field(default_factory=list)
    unexpected_tickers: list[str] = field(default_factory=list)
    context_hash: str | None = None
    handicap_schema_version: str | None = None
    uncertainty_stated: bool = False

    @property
    def evaluated(self) -> int:
        return sum(count for status, count in self.status_counts.items() if status in PRICED_STATUSES)

    @property
    def unpriceable(self) -> int:
        return sum(
            count for status, count in self.status_counts.items() if status in UNPRICEABLE_STATUSES
        )

    @property
    def accounted(self) -> int:
        return self.evaluated + self.unpriceable

    @property
    def unaccounted(self) -> int:
        return self.eligible - self.accounted

    @property
    def complete(self) -> bool:
        """COMPLETE means all four of these, not just the headline count:
        nothing missing, nothing duplicated, nothing foreign, and the
        arithmetic closes."""
        return (
            self.unaccounted == 0
            and self.eligible == self.accounted
            and not self.missing_tickers
            and not self.duplicate_tickers
            and not self.unexpected_tickers
        )

    def candidate_rows(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["status"] in CANDIDATE_STATUSES]

    def positive_ev_rows(self) -> list[dict[str, Any]]:
        """Kept under its schema-1 name, and it now means every row whose
        edge is real enough to reach a candidate artifact. `not_robust` is
        deliberately excluded: it is a bet on being exactly right."""
        return self.candidate_rows()

    def summary(self) -> dict[str, Any]:
        return {
            "game_key": self.game_key,
            "packet_hash": self.packet_hash,
            "context_hash": self.context_hash,
            "market_universe_hash": self.market_universe_hash,
            "handicap_schema_version": self.handicap_schema_version,
            "uncertainty_stated": self.uncertainty_stated,
            "eligible_contracts": self.eligible,
            "evaluated_contracts": self.evaluated,
            "unpriceable_contracts": self.unpriceable,
            "unaccounted_contracts": self.unaccounted,
            "status_counts": dict(sorted(self.status_counts.items())),
            "game_status": "COMPLETE" if self.complete else "INCOMPLETE",
            "missing_tickers": list(self.missing_tickers),
            "duplicate_tickers": list(self.duplicate_tickers),
            "unexpected_tickers": list(self.unexpected_tickers),
            "evaluated_at": self.evaluated_at,
        }


class CompletionGateError(RuntimeError):
    """A game could not be marked complete. Carries the missing tickers,
    because the mission's own worked example demands the exact three."""

    def __init__(self, game_key: str, missing: list[str], evaluation: GameEvaluation) -> None:
        self.game_key = game_key
        self.missing = missing
        self.evaluation = evaluation
        super().__init__(
            f"{game_key}: INCOMPLETE -- eligible={evaluation.eligible} "
            f"evaluated={evaluation.evaluated} unpriceable={evaluation.unpriceable} "
            f"unaccounted={evaluation.unaccounted}; missing: {', '.join(missing) or '(unnamed)'}"
        )


def _count(evaluation: GameEvaluation, row: dict[str, Any]) -> None:
    evaluation.rows.append(row)
    evaluation.status_counts[row["status"]] = evaluation.status_counts.get(row["status"], 0) + 1


def evaluate_game(
    packet: dict[str, Any],
    handicap: HandicapPayload,
    *,
    min_net_edge: float = DEFAULT_MIN_NET_EDGE,
    as_of: datetime | None = None,
) -> GameEvaluation:
    """Price EVERY eligible contract in `packet` against `handicap`.

    The loop is unconditional and the accounting is checked afterwards, so
    a bug that skipped a contract shows up as INCOMPLETE rather than as a
    shortlist built on a partial scan."""
    moment = as_of or datetime.now(UTC)
    contracts = list(packet.get("contracts") or [])
    evaluation = GameEvaluation(
        game_key=str(packet.get("game_key")),
        packet_hash=packet.get("packet_hash"),
        market_universe_hash=packet.get("market_universe_hash"),
        context_hash=(packet.get("factual_context") or {}).get("context_hash"),
        eligible=len(contracts),
        evaluated_at=moment.isoformat(),
        handicap_schema_version=handicap.schema_version_supplied,
        uncertainty_stated=not handicap.is_legacy_point_estimate,
    )

    # THE MEASUREMENT FLOORS THE CLAIM.
    #
    # The packet carries the data quality that was MEASURED when the slate was
    # built; the handicap carries what the handicapper claims. A payload that
    # dropped the block, or listed fewer gates than the measurement raised,
    # would otherwise silently re-open every family the facts cannot support.
    measured = DataQuality.parse((packet.get("factual_context") or {}).get("data_quality"))
    effective_quality = handicap.data_quality.merged_with_measurement(measured)

    low_confidence = set(handicap.low_confidence_families)
    gated = effective_quality.gated_families
    # One scenario grid per PERIOD, not per contract. The grid depends only on
    # the stated region, and 14,000 contracts rebuilding the same eight corners
    # is the kind of waste that turns a two-second evaluation into a minute.
    grids = {
        name: scenarios(dist.uncertainty)
        for name, dist in handicap.period_distributions.items()
    }

    for record in contracts:
        ticker = str(record.get("ticker"))
        semantics = dict(record.get("semantics") or {})
        semantics["family"] = record.get("family")
        executable = record.get("executable") or {}
        yes_block = executable.get("yes") or {}
        no_block = executable.get("no") or {}
        family = str(record.get("family") or "")

        row: dict[str, Any] = {
            "ticker": ticker,
            "game_key": evaluation.game_key,
            "family": record.get("family"),
            "period": semantics.get("period"),
            "kind": semantics.get("kind"),
            "team": semantics.get("team"),
            "line": semantics.get("line"),
            "yes_means": semantics.get("yes_means"),
            "no_means": semantics.get("no_means"),
            "yes_entry": yes_block.get("entry"),
            "no_entry": no_block.get("entry"),
        }

        if yes_block.get("entry") is None and no_block.get("entry") is None:
            row.update(
                status=EvaluationStatus.NON_EXECUTABLE.value,
                reason="neither side has an executable entry price at evaluation time",
            )
            _count(evaluation, row)
            continue

        # THE DATA-QUALITY GATE RUNS BEFORE PRICING, NOT AFTER.
        # A family this game's FACTS cannot support must not be priced and then
        # discarded: the number would exist, would be quotable, and somebody
        # would quote it. It becomes a counted terminal bucket instead.
        gate = effective_quality.gate_for(family) if family in gated else None
        if gate is not None:
            row.update(
                status=EvaluationStatus.UNPRICEABLE_INSUFFICIENT_DATA.value,
                reason=(
                    f"the factual evidence for this game does not support family {family!r}: "
                    f"{gate.reason} (measured: {gate.measured})"
                ),
                data_quality_gate=gate.as_dict(),
            )
            _count(evaluation, row)
            continue

        fair = fair_probability(semantics, ticker, handicap)
        if fair.value is None:
            row.update(
                status=EvaluationStatus.UNPRICEABLE_FROM_HANDICAP.value,
                reason=fair.reason or "the supplied handicap does not determine this probability",
                pricing_method=fair.method,
            )
            _count(evaluation, row)
            continue

        # The scenario ladder for THIS contract. Three sources, and which one
        # applies is the thing that decides whether robustness is claimable.
        from_stated_range = False
        if fair.method == "explicit_probability":
            stated = handicap.explicit_probability_ranges.get(ticker)
            if stated is None:
                yes_ladder = [("base", float(fair.value))]
                tested = False
            else:
                low, high = stated
                yes_ladder = [("base", float(fair.value)), ("stated_low", low), ("stated_high", high)]
                tested = True
                from_stated_range = True
        else:
            period_name = str(semantics.get("period"))
            grid = grids.get(period_name) or ()
            period_dist = handicap.period_distributions.get(period_name)
            if period_dist is None:  # pragma: no cover - fair.value implies it exists
                yes_ladder = [("base", float(fair.value))]
                tested = False
            else:
                yes_ladder = _fair_by_scenario(semantics, period_dist, grid)
                tested = period_dist.uncertainty.supports_robustness and len(yes_ladder) > 1
                if not yes_ladder:
                    yes_ladder = [("base", float(fair.value))]

        no_ladder = [(label, 1.0 - value) for label, value in yes_ladder]
        kind = str(semantics.get("kind") or "")

        sides: list[SideEvaluation] = []
        for side_name, block, ladder in (
            ("yes", yes_block, yes_ladder),
            ("no", no_block, no_ladder),
        ):
            entry = block.get("entry")
            if entry is None:
                continue
            fee = float(block.get("fee") or 0.0)
            sensitivity = evaluate_side(
                kind=kind,
                entry=float(entry),
                fee=fee,
                fair_by_scenario=ladder,
                min_required_edge=min_net_edge,
                tested=tested,
                from_stated_range=from_stated_range,
            )
            side_eval = _side_evaluation(side_name, entry, fee, ladder[0][1], sensitivity)
            if side_eval is not None:
                sides.append(side_eval)

        # The best side is chosen on the BASE edge, and then reported with its
        # own sensitivity. Choosing on the worst-case edge instead would quietly
        # switch the recommended side whenever the downside happened to favour
        # the other one, which is a different bet than the handicap implies.
        best = max(sides, key=lambda s: s.net_edge)
        best_sensitivity = best.sensitivity

        row["fair_probability_yes"] = round(float(yes_ladder[0][1]), 6)
        row["pricing_method"] = fair.method
        row["sides"] = [s.as_dict() for s in sides]
        row["best_side"] = best.side
        row["executable_entry"] = round(best.entry, 6)
        row["fee"] = round(best.fee, 6)
        row["implied_probability"] = round(best.implied_probability, 6)
        row["fair_probability"] = round(best.fair_probability, 6)
        row["raw_edge"] = round(best.raw_edge, 6)
        row["net_edge"] = round(best.net_edge, 6)
        row["ev_per_contract"] = round(best.ev_per_contract, 6)
        row["uncertainty_tested"] = bool(tested)
        if best_sensitivity is not None:
            row["sensitivity"] = best_sensitivity.as_dict()
            row["bet_up_to_price"] = round(
                bet_up_to(best.fair_probability, best.fee, min_net_edge), 6
            )

        if family in low_confidence:
            row.update(
                status=EvaluationStatus.TOO_UNCERTAIN.value,
                reason=f"handicap flagged family {family!r} as low confidence",
            )
        else:
            verdict = best_sensitivity.robustness if best_sensitivity else Robustness.NEGATIVE_EV.value
            reason = best_sensitivity.reason if best_sensitivity else "no sensitivity was computed"
            row.update(status=_STATUS_FOR_ROBUSTNESS[verdict], reason=reason)

        _count(evaluation, row)

    return reconcile(packet, evaluation)


#: Robustness verdict -> terminal ledger bucket. A total mapping over the
#: `Robustness` enum, so a verdict added there without a bucket here is an
#: immediate KeyError rather than an unaccounted contract.
_STATUS_FOR_ROBUSTNESS = {
    Robustness.ROBUST_POSITIVE_EV.value: EvaluationStatus.ROBUST_POSITIVE_EV.value,
    Robustness.SENSITIVE_POSITIVE_EV.value: EvaluationStatus.SENSITIVE_POSITIVE_EV.value,
    Robustness.NOT_ROBUST.value: EvaluationStatus.NOT_ROBUST.value,
    Robustness.BELOW_REQUIRED_EDGE.value: EvaluationStatus.BELOW_REQUIRED_EDGE.value,
    Robustness.NEGATIVE_EV.value: EvaluationStatus.NEGATIVE_EV.value,
    Robustness.ZERO_OR_NEGLIGIBLE_EDGE.value: EvaluationStatus.ZERO_OR_NEGLIGIBLE_EDGE.value,
    Robustness.NOT_APPLICABLE.value: EvaluationStatus.NEGATIVE_EV.value,
}


def reconcile(packet: dict[str, Any], evaluation: GameEvaluation) -> GameEvaluation:
    """The invariant, measured rather than assumed.

    Two independent things are checked: that the counts add up, and that
    the SET of tickers evaluated is exactly the set of eligible tickers.
    The second catches the failure the first cannot -- a contract
    evaluated twice while another was skipped leaves the totals correct.

    This RECORDS the discrepancy instead of raising it. An INCOMPLETE game
    must be representable, printable and resumable; only the report
    generator refuses to proceed on one (`require_complete`)."""
    eligible_tickers = {str(c.get("ticker")) for c in packet.get("contracts") or []}
    evaluated_tickers = [str(r["ticker"]) for r in evaluation.rows]
    seen = set(evaluated_tickers)

    counted: dict[str, int] = {}
    for ticker in evaluated_tickers:
        counted[ticker] = counted.get(ticker, 0) + 1
    evaluation.duplicate_tickers = sorted(t for t, n in counted.items() if n > 1)
    evaluation.missing_tickers = sorted(eligible_tickers - seen)
    evaluation.unexpected_tickers = sorted(seen - eligible_tickers)
    return evaluation


def require_complete(evaluation: GameEvaluation) -> None:
    """The per-game completion gate. Nothing downstream of this may treat
    the game as fully scanned unless it returns."""
    if evaluation.complete:
        return
    missing = (
        evaluation.missing_tickers + evaluation.duplicate_tickers + evaluation.unexpected_tickers
    )
    raise CompletionGateError(evaluation.game_key, missing, evaluation)


def ledger_document(
    evaluations: list[GameEvaluation],
    *,
    shard: str | None,
    min_net_edge: float,
    as_of: str,
) -> dict[str, Any]:
    """The machine-readable record of EVERY eligible contract's
    disposition -- winners and losers alike. The shortlist is a view of
    this file, never a separate dataset."""
    totals = {
        "eligible_contracts": sum(e.eligible for e in evaluations),
        "evaluated_contracts": sum(e.evaluated for e in evaluations),
        "unpriceable_contracts": sum(e.unpriceable for e in evaluations),
        "unaccounted_contracts": sum(e.unaccounted for e in evaluations),
    }
    status_counts: dict[str, int] = {}
    for evaluation in evaluations:
        for status, count in evaluation.status_counts.items():
            status_counts[status] = status_counts.get(status, 0) + count
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "shard": shard,
        "as_of": as_of,
        "min_net_edge": min_net_edge,
        "min_net_edge_note": (
            "operator-supplied required edge. This repository has never validated a bar at which a "
            "CFB Kalshi edge is real, and none is claimed here."
        ),
        "robustness_note": (
            "robust_positive_ev means the fee-adjusted edge clears the bar at EVERY corner of the "
            "uncertainty region the handicap stated. A handicap that stated no region cannot reach "
            "it -- its contracts top out at sensitive_positive_ev with the reason naming why."
        ),
        "totals": totals,
        "status_counts": dict(sorted(status_counts.items())),
        "games": [e.summary() for e in evaluations],
        "contracts": [row for e in evaluations for row in e.rows],
    }


__all__ = [
    "CANDIDATE_STATUSES",
    "DEFAULT_MIN_NET_EDGE",
    "NEGLIGIBLE_EDGE",
    "PRICED_STATUSES",
    "RECOMMENDABLE",
    "UNPRICEABLE_STATUSES",
    "CompletionGateError",
    "EvaluationStatus",
    "FairProbability",
    "GameEvaluation",
    "SideEvaluation",
    "evaluate_game",
    "fair_from_distribution",
    "fair_probability",
    "ledger_document",
    "reconcile",
    "require_complete",
]
