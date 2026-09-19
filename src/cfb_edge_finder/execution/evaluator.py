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
from cfb_edge_finder.execution.semantics import ContractKind, PricingRequirement, TeamSlot
from cfb_edge_finder.projections.distribution import (
    margin_distribution,
    team_total_distribution,
    total_distribution,
)
from cfb_edge_finder.schemas.common import Side
from cfb_edge_finder.schemas.projection import GameDistribution


class EvaluationStatus(StrEnum):
    POSITIVE_EV = "positive_ev"
    NEGATIVE_EV = "negative_ev"
    ZERO_OR_NEGLIGIBLE_EDGE = "zero_or_negligible_edge"
    BELOW_REQUIRED_EDGE = "below_required_edge"
    TOO_UNCERTAIN = "too_uncertain"
    UNPRICEABLE_FROM_HANDICAP = "unpriceable_from_handicap"
    NON_EXECUTABLE = "non_executable"
    INVALID_SEMANTICS = "invalid_semantics"


PRICED_STATUSES = frozenset(
    {
        EvaluationStatus.POSITIVE_EV.value,
        EvaluationStatus.NEGATIVE_EV.value,
        EvaluationStatus.ZERO_OR_NEGLIGIBLE_EDGE.value,
        EvaluationStatus.BELOW_REQUIRED_EDGE.value,
        EvaluationStatus.TOO_UNCERTAIN.value,
    }
)
UNPRICEABLE_STATUSES = frozenset(
    {
        EvaluationStatus.UNPRICEABLE_FROM_HANDICAP.value,
        EvaluationStatus.NON_EXECUTABLE.value,
        EvaluationStatus.INVALID_SEMANTICS.value,
    }
)

DEFAULT_MIN_NET_EDGE = 0.02
"""The operator's required fee-adjusted edge, in probability units.

*** THIS IS NOT A VALIDATED THRESHOLD ***
This repository has never established a bar at which a CFB Kalshi edge is
real, and this constant does not claim one. It is a knob the operator sets
on the command line; 0.02 is the default because a visible default that
the CLI prints back is safer than an invisible 0.0 that makes every
rounding artefact a 'positive EV' bet."""

NEGLIGIBLE_EDGE = 1e-4


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

    return FairProbability(
        None, "none", f"no pricing route for contract kind {kind!r}"
    )


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

    def as_dict(self) -> dict[str, Any]:
        return {
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


def _side_evaluation(side: str, entry: float | None, fee: float | None, fair: float) -> SideEvaluation | None:
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

    def positive_ev_rows(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["status"] == EvaluationStatus.POSITIVE_EV.value]

    def summary(self) -> dict[str, Any]:
        return {
            "game_key": self.game_key,
            "packet_hash": self.packet_hash,
            "market_universe_hash": self.market_universe_hash,
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
        eligible=len(contracts),
        evaluated_at=moment.isoformat(),
    )

    low_confidence = set(handicap.low_confidence_families)

    for record in contracts:
        ticker = str(record.get("ticker"))
        semantics = dict(record.get("semantics") or {})
        semantics["family"] = record.get("family")
        executable = record.get("executable") or {}
        yes_block = executable.get("yes") or {}
        no_block = executable.get("no") or {}

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
            evaluation.rows.append(row)
            evaluation.status_counts[row["status"]] = evaluation.status_counts.get(row["status"], 0) + 1
            continue

        fair = fair_probability(semantics, ticker, handicap)
        if fair.value is None:
            row.update(
                status=EvaluationStatus.UNPRICEABLE_FROM_HANDICAP.value,
                reason=fair.reason or "the supplied handicap does not determine this probability",
                pricing_method=fair.method,
            )
            evaluation.rows.append(row)
            evaluation.status_counts[row["status"]] = evaluation.status_counts.get(row["status"], 0) + 1
            continue

        yes_eval = _side_evaluation("yes", yes_block.get("entry"), yes_block.get("fee"), fair.value)
        no_eval = _side_evaluation("no", no_block.get("entry"), no_block.get("fee"), 1.0 - fair.value)
        sides = [s for s in (yes_eval, no_eval) if s is not None]
        best = max(sides, key=lambda s: s.net_edge)

        row["fair_probability_yes"] = round(fair.value, 6)
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

        family = str(record.get("family") or "")
        if family in low_confidence:
            row.update(
                status=EvaluationStatus.TOO_UNCERTAIN.value,
                reason=f"handicap flagged family {family!r} as low confidence",
            )
        elif -NEGLIGIBLE_EDGE <= best.net_edge <= NEGLIGIBLE_EDGE:
            row.update(
                status=EvaluationStatus.ZERO_OR_NEGLIGIBLE_EDGE.value,
                reason=f"net edge {best.net_edge:.6f} is inside the negligible band",
            )
        elif best.net_edge >= min_net_edge:
            row.update(
                status=EvaluationStatus.POSITIVE_EV.value,
                reason=f"net edge {best.net_edge:.4f} clears the operator's {min_net_edge:.4f} bar",
            )
        elif best.net_edge > 0:
            row.update(
                status=EvaluationStatus.BELOW_REQUIRED_EDGE.value,
                reason=f"net edge {best.net_edge:.4f} is positive but below the {min_net_edge:.4f} bar",
            )
        else:
            row.update(
                status=EvaluationStatus.NEGATIVE_EV.value,
                reason=f"net edge {best.net_edge:.4f} after fees",
            )

        evaluation.rows.append(row)
        evaluation.status_counts[row["status"]] = evaluation.status_counts.get(row["status"], 0) + 1

    return reconcile(packet, evaluation)


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
        "totals": totals,
        "status_counts": dict(sorted(status_counts.items())),
        "games": [e.summary() for e in evaluations],
        "contracts": [row for e in evaluations for row in e.rows],
    }
