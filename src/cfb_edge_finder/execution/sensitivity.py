"""What happens to an edge when the handicap is wrong.

*** THE RULE THIS MODULE ENFORCES ***
A recommendation may never be called ROBUST because one point-estimate
probability beat one fee-adjusted breakeven. Robust means the edge is still
there at every corner of the region the handicapper stated -- and a handicap
that stated no region cannot make the claim at all, however large its base-case
edge looks.

*** THE ARITHMETIC ***
On a Kalshi binary the fee is charged on entry only, so

    EV per contract = fair - entry - fee

and the fee-adjusted breakeven probability is `entry + fee`. An edge is
therefore `fair - breakeven`, and a sensitivity analysis is nothing more than
recomputing `fair` at each corner and reporting the worst one. `entry`, `fee`
and the breakeven do NOT move across the region: they are the market's, not the
handicap's, and pretending the price improves in the scenarios where we are
wrong would be flattering the analysis at exactly the wrong moment.

*** WHAT THE BOUND ACTUALLY IS ***
Three honest labels, never one confident one:

  `exact_corner_extremum`  the family is monotone in every axis, so the corner
                           minimum IS the region minimum. Moneyline, spread,
                           alternate spread, total, alternate total and team
                           total are all in this class.
  `grid_extremum`          the family is not monotone (a winning-margin BAND is
                           a difference of two tails), so the corners are a
                           sample. The minimum reported is a minimum over the
                           enumerated corners and says so.
  `stated_range`           the probability was typed by a human and the range
                           was typed by the same human. There is no model to
                           perturb; the bound is the one they stated.
  `not_tested`             no region exists. This is the one that can never be
                           robust.

*** IT DOES NOT DECIDE WHETHER TO BET ***
It classifies. `min_required_edge` is the operator's number, printed back, and
this repository has still never validated a bar at which a CFB Kalshi edge is
real.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Robustness(StrEnum):
    ROBUST_POSITIVE_EV = "robust_positive_ev"
    """Positive, and CLEARS THE OPERATOR'S BAR, at every corner of the region."""

    SENSITIVE_POSITIVE_EV = "sensitive_positive_ev"
    """Base case clears the bar; somewhere in the region it does not, but the
    edge never goes negative. Also the ceiling for any handicap with no
    region at all -- a point estimate cannot earn more than this."""

    NOT_ROBUST = "not_robust"
    """Base case clears the bar and part of the reasonable region crosses
    BELOW BREAKEVEN. The bet is a bet on the handicap being close to exactly
    right."""

    BELOW_REQUIRED_EDGE = "below_required_edge"
    NEGATIVE_EV = "negative_ev"
    ZERO_OR_NEGLIGIBLE_EDGE = "zero_or_negligible_edge"
    NOT_APPLICABLE = "not_applicable"
    """The contract was never priced, so there is nothing to be robust about."""


#: The classifications a recommendation may be built from. `not_robust` is
#: deliberately NOT here: it is published, counted and readable in the ledger,
#: and it does not reach the candidate artifact unless the operator asks for it
#: explicitly.
RECOMMENDABLE = frozenset(
    {Robustness.ROBUST_POSITIVE_EV.value, Robustness.SENSITIVE_POSITIVE_EV.value}
)


class SensitivityBound(StrEnum):
    EXACT_CORNER_EXTREMUM = "exact_corner_extremum"
    GRID_EXTREMUM = "grid_extremum"
    STATED_RANGE = "stated_range"
    NOT_TESTED = "not_tested"


#: Contract kinds whose fair probability is monotone in every axis of the
#: region, so the corner extremum is the true region extremum. Kept as an
#: explicit allowlist: a kind added later gets the honest `grid_extremum`
#: label until somebody proves monotonicity for it.
MONOTONE_KINDS = frozenset({"moneyline", "spread", "total", "team_total"})

NEGLIGIBLE_EDGE = 1e-4


@dataclass(frozen=True)
class ScenarioEdge:
    """One corner's answer."""

    label: str
    fair_probability: float
    net_edge: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.label,
            "fair_probability": round(self.fair_probability, 6),
            "net_edge": round(self.net_edge, 6),
        }


@dataclass(frozen=True)
class Sensitivity:
    """The full answer for one side of one contract."""

    base_fair_probability: float
    breakeven_probability: float
    base_net_edge: float
    min_fair_probability: float
    max_fair_probability: float
    min_net_edge: float
    max_net_edge: float
    bound: str
    scenarios_tested: int
    robustness: str
    reason: str
    worst_scenario: str | None = None
    scenario_edges: tuple[ScenarioEdge, ...] = ()

    def as_dict(self, *, include_scenarios: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {
            "base_fair_probability": round(self.base_fair_probability, 6),
            "fee_adjusted_breakeven": round(self.breakeven_probability, 6),
            "base_net_edge": round(self.base_net_edge, 6),
            "fair_probability_low": round(self.min_fair_probability, 6),
            "fair_probability_high": round(self.max_fair_probability, 6),
            "net_edge_low": round(self.min_net_edge, 6),
            "net_edge_high": round(self.max_net_edge, 6),
            "sensitivity_bound": self.bound,
            "scenarios_tested": self.scenarios_tested,
            "robustness": self.robustness,
            "robustness_reason": self.reason,
            "worst_scenario": self.worst_scenario,
        }
        if include_scenarios:
            out["scenario_edges"] = [s.as_dict() for s in self.scenario_edges]
        return out


def bound_for(kind: str, *, tested: bool, from_stated_range: bool = False) -> str:
    if from_stated_range:
        return SensitivityBound.STATED_RANGE.value
    if not tested:
        return SensitivityBound.NOT_TESTED.value
    if kind in MONOTONE_KINDS:
        return SensitivityBound.EXACT_CORNER_EXTREMUM.value
    return SensitivityBound.GRID_EXTREMUM.value


def classify(
    *,
    base_net_edge: float,
    min_net_edge: float,
    min_required_edge: float,
    bound: str,
) -> tuple[str, str]:
    """(robustness, reason). The single place the taxonomy is decided.

    Order matters, and the first branch is the whole point of the module: a
    region that was never tested cannot produce a robust verdict, whatever the
    numbers say. `min_net_edge` equals `base_net_edge` in that case -- which is
    exactly the arithmetic that would otherwise mislabel it.
    """
    if base_net_edge <= -NEGLIGIBLE_EDGE:
        return (
            Robustness.NEGATIVE_EV.value,
            f"base fee-adjusted edge {base_net_edge:.4f} is negative",
        )
    if abs(base_net_edge) <= NEGLIGIBLE_EDGE:
        return (
            Robustness.ZERO_OR_NEGLIGIBLE_EDGE.value,
            f"base fee-adjusted edge {base_net_edge:.6f} is inside the negligible band",
        )
    if base_net_edge < min_required_edge:
        return (
            Robustness.BELOW_REQUIRED_EDGE.value,
            f"base fee-adjusted edge {base_net_edge:.4f} is positive but below the "
            f"{min_required_edge:.4f} bar",
        )

    if bound == SensitivityBound.NOT_TESTED.value:
        return (
            Robustness.SENSITIVE_POSITIVE_EV.value,
            "the handicap stated no uncertainty region, so this edge was never tested against "
            "one; a point estimate cannot support a robustness claim however large its base edge",
        )

    if min_net_edge >= min_required_edge:
        return (
            Robustness.ROBUST_POSITIVE_EV.value,
            f"the fee-adjusted edge stays at or above the {min_required_edge:.4f} bar across the "
            f"whole stated uncertainty region (worst {min_net_edge:.4f})",
        )
    if min_net_edge > 0:
        return (
            Robustness.SENSITIVE_POSITIVE_EV.value,
            f"the edge stays positive across the region but falls to {min_net_edge:.4f}, below the "
            f"{min_required_edge:.4f} bar, in part of it",
        )
    return (
        Robustness.NOT_ROBUST.value,
        f"part of the stated uncertainty region takes the fee-adjusted edge to {min_net_edge:.4f}, "
        "at or below breakeven",
    )


def evaluate_side(
    *,
    kind: str,
    entry: float,
    fee: float,
    fair_by_scenario: list[tuple[str, float]],
    min_required_edge: float,
    tested: bool,
    from_stated_range: bool = False,
) -> Sensitivity:
    """Sensitivity for one executable side.

    `fair_by_scenario` must have the BASE CASE FIRST. That is not a
    convenience: the base fair value is the number every downstream artifact
    quotes, and deriving it from `min`/`max` after the fact would make the
    published fair value depend on which corner happened to be extreme.
    """
    if not fair_by_scenario:
        raise ValueError("a sensitivity needs at least the base scenario")

    breakeven = float(entry) + float(fee)
    edges = [
        ScenarioEdge(label=label, fair_probability=fair, net_edge=fair - breakeven)
        for label, fair in fair_by_scenario
    ]
    base = edges[0]
    worst = min(edges, key=lambda e: e.net_edge)
    best = max(edges, key=lambda e: e.net_edge)
    bound = bound_for(kind, tested=tested, from_stated_range=from_stated_range)
    robustness, reason = classify(
        base_net_edge=base.net_edge,
        min_net_edge=worst.net_edge,
        min_required_edge=min_required_edge,
        bound=bound,
    )
    return Sensitivity(
        base_fair_probability=base.fair_probability,
        breakeven_probability=breakeven,
        base_net_edge=base.net_edge,
        min_fair_probability=min(e.fair_probability for e in edges),
        max_fair_probability=max(e.fair_probability for e in edges),
        min_net_edge=worst.net_edge,
        max_net_edge=best.net_edge,
        bound=bound,
        scenarios_tested=len(edges),
        robustness=robustness,
        reason=reason,
        worst_scenario=worst.label,
        scenario_edges=tuple(edges),
    )


def bet_up_to(fair_probability: float, fee: float, min_required_edge: float) -> float:
    """The highest price at which this side still clears the operator's bar.

    Stated in PRICE, not in probability, because the operator is looking at an
    order ticket. The fee is treated as fixed at the quoted level rather than
    recomputed at the higher price: Kalshi's trade fee rises with price, so the
    true ceiling is slightly below this, and a number that errs toward paying
    LESS is the right direction to err in.
    """
    return max(0.0, fair_probability - fee - min_required_edge)
