"""Contract-oriented probabilities for the talent shadow arm.

*** THE DEFECT THIS MODULE EXISTS TO FIX ***

The first live prospective capture computed one number per game:

    shadow_probability = mean(control_margin_samples + delta > 0)

which is P(HOME wins). It was then written onto EVERY contract on that
game. Measured on real captured rows, the same value appeared on the
home-side and away-side winner rows of the same matchup (Boise State at
Oregon: control 0.1185 / 0.8699, shadow 0.9315 / 0.9315), so the
away-side `shadow_minus_control_probability` compared P(home) against
P(away) -- not a paired delta at all.

A reproduction across families showed the blast radius was larger than
the moneyline where it was first spotted: a SPREAD contract and a TOTAL
contract received that same winner probability, because nothing in the
old path looked at the contract's proposition at all.

*** THE FIX: PRICE THE SHADOW THROUGH THE CANONICAL PRICER ***

The canonical arm prices a contract analytically:

    distribution = cached_projection.projection.to_game_distribution()
    price_parsed_contract(parsed, distribution, named_team_side=side)

`price_parsed_contract` already encodes every proposition this
repository supports -- P(named team wins), P(named team wins by strictly
more than T) with the spread sign derivation, P(total over/under T) --
and applies the same continuity correction to each.

So the shadow does not re-implement any of it. It builds the SAME
`GameDistribution` with the talent delta applied, and calls the SAME
function with the SAME parsed contract and the SAME resolved team side.
Orientation, tie handling, threshold semantics and market inputs are then
identical by construction rather than by inspection: the only difference
between the two arms is the frozen talent shift.

*** HOW THE DELTA IS APPLIED ***

Exactly the way this repository already applies a margin delta to a
distribution -- `CorrectedGameProjection.to_game_distribution` (the C.2
margin correction):

    home_mean += delta / 2
    away_mean -= delta / 2
    home_sd, away_sd, correlation unchanged

which shifts the mean margin by exactly `delta`, leaves the total
unchanged, and preserves variance. Reusing the C.2 convention rather than
inventing a second one keeps a single answer to "what does it mean to
move a projected margin", and it matches the frozen candidate's
documented behaviour (`shadow_transform.TOTAL_CHANNEL_UNCHANGED`).

Consequence worth stating because it is a RESULT, not an oversight: a
TOTAL contract's shadow probability equals its basis probability
exactly. The frozen candidate moves margin and not total, so it makes no
prediction about totals. That is the mathematically expected outcome and
is asserted by test.

*** WHAT THIS MODULE DELIBERATELY DOES NOT DO ***

It does not touch TALENT_BETA, the shadow model version, the talent
margin adjustment, variance, or the preregistered primary hypothesis.
This is a measurement repair on the experimental arm.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfb_edge_finder.kalshi.contract_semantics import ParsedContract
from cfb_edge_finder.kalshi.market_pricing import price_parsed_contract
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.projection import GameDistribution

PROBABILITY_SEMANTICS_VERSION = "shadow_probability_contract_oriented_v2"
"""Instrumentation version, NOT a model version.

`shadow-preseason-talent-v1` is unchanged and stays unchanged: same beta,
same talent transformation, same margin prediction. What changed is how
the arm's probability is READ OFF that prediction for a given contract."""

COMPARISON_BASIS = "basis_control_same_pricer_same_orientation"
"""The clean experimental comparison is BASIS-vs-SHADOW.

Basis control is the control distribution priced through the identical
pricer, contract and side as the shadow, so the two differ only by the
talent shift. The canonical probability is recorded alongside it for
audit against the live production model, never as the experimental
counterfactual."""


class ShadowContractPricingError(ValueError):
    """Raised when a contract cannot be priced for BOTH arms identically."""


@dataclass(frozen=True)
class ContractProbabilities:
    """One contract, priced under both arms.

    `basis` and `shadow` come from the same function, the same parsed
    contract and the same resolved side; only the distribution differs."""

    basis: float | None
    shadow: float | None
    detail: str
    semantics_version: str = PROBABILITY_SEMANTICS_VERSION
    comparison_basis: str = COMPARISON_BASIS

    @property
    def shadow_minus_basis(self) -> float | None:
        if self.basis is None or self.shadow is None:
            return None
        return self.shadow - self.basis


def shadow_game_distribution(control: GameDistribution, delta: float) -> GameDistribution:
    """The control distribution with the talent margin delta applied.

    Mirrors `CorrectedGameProjection.to_game_distribution` exactly,
    including its 0.0 floor on either mean, so a talent shift and a C.2
    correction mean the same thing to the rest of the system."""
    return GameDistribution(
        home_mean=max(control.home_mean + delta / 2, 0.0),
        away_mean=max(control.away_mean - delta / 2, 0.0),
        home_sd=control.home_sd,
        away_sd=control.away_sd,
        correlation=control.correlation,
    )


def _parsed_for(
    *, family: MarketFamily, side: Side | None, threshold: float | None
) -> ParsedContract:
    """Rebuild the contract spec the canonical pricer consumes.

    Fields come from what the canonical observation itself recorded, so
    the shadow prices the proposition the CONTROL actually priced rather
    than a re-derivation that could drift from it."""
    return ParsedContract(
        reason=None,
        detail="reconstructed from the canonical observation for shadow pricing",
        market_family=family,
        side=side,
        line=threshold,
        semantics_confidence="confirmed_live",
    )


def price_contract_both_arms(
    *,
    control_distribution: GameDistribution,
    delta: float,
    family: MarketFamily | None,
    side: Side | None,
    threshold: float | None,
    named_team_side: Side | None,
) -> ContractProbabilities:
    """Price one contract under the basis control and the shadow.

    Returns both probabilities or both None -- never one arm priced and
    the other not, which would produce a delta against nothing."""
    if family is None:
        return ContractProbabilities(
            basis=None, shadow=None, detail="no market family on the canonical observation"
        )

    parsed = _parsed_for(family=family, side=side, threshold=threshold)
    shadow_distribution = shadow_game_distribution(control_distribution, delta)

    basis_result = price_parsed_contract(
        parsed, control_distribution, named_team_side=named_team_side
    )
    shadow_result = price_parsed_contract(
        parsed, shadow_distribution, named_team_side=named_team_side
    )

    if basis_result.model_probability is None or shadow_result.model_probability is None:
        # One-armed pricing is worse than none: it invites a delta
        # against a missing counterfactual.
        return ContractProbabilities(
            basis=None,
            shadow=None,
            detail=f"unpriceable for both arms: {basis_result.error or shadow_result.error}",
        )

    return ContractProbabilities(
        basis=float(basis_result.model_probability),
        shadow=float(shadow_result.model_probability),
        detail=shadow_result.detail,
    )
