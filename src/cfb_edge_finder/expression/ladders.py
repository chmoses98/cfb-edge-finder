"""Spread and total ladder structure, model monotonicity, and market
coherence checks.

*** THE ORDERING PROPERTY ***
Every rung of a spread ladder for one team reads the SAME number -- the
final margin -- at a different threshold. Because the settlement rule is
`team_margin > threshold`, the events are strictly nested:

    {margin > 27.5}  subset of  {margin > 13.5}  subset of  {margin > 1.5}

so as the threshold rises the event can only become less likely. A model
probability that RISES with the threshold is therefore not a judgement
call, it is a contradiction of the model's own distribution, and is
flagged. Totals behave identically for Over contracts.

*** MARKET INCOHERENCE IS FLAGGED, NEVER REPAIRED ***
The same nesting says a harder rung should not cost MORE than an easier
one. When the captured asks say otherwise, that is recorded and left
alone. Mission section 7 is explicit: do not silently repair. A quoted
ask can be stale, thin, or wide, and rewriting it would destroy the
evidence that the market looked like that at capture time.

Importantly, an incoherent pair of asks is NOT an arbitrage claim. Acting
on it would require buying one rung and selling (or buying the NO of)
another, at size, simultaneously, with fees on both legs -- none of which
is established by two top-of-book asks. See economics.py's
`StaticInconsistency` for the one construction this repo does consider
defensible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from cfb_edge_finder.expression.taxonomy import MarketDimension


class LadderAnomaly(StrEnum):
    MODEL_MONOTONICITY_VIOLATION = "MODEL_MONOTONICITY_VIOLATION"
    MARKET_LADDER_INCOHERENCE = "MARKET_LADDER_INCOHERENCE"
    DUPLICATE_THRESHOLD = "DUPLICATE_THRESHOLD"
    INCONSISTENT_SEMANTIC_OPERATOR = "INCONSISTENT_SEMANTIC_OPERATOR"
    IMPOSSIBLE_THRESHOLD = "IMPOSSIBLE_THRESHOLD"
    MODEL_TIE_MASS = "MODEL_TIE_MASS"


@dataclass(frozen=True)
class LadderRung:
    market_ticker: str
    threshold: float
    model_probability: float | None
    executable_yes_price: float | None
    executable_no_price: float | None
    semantic_operator: str | None
    timing_label: str


@dataclass(frozen=True)
class LadderFinding:
    anomaly: LadderAnomaly
    game_id: str
    dimension: MarketDimension
    ladder_key: str
    detail: str
    lower_ticker: str | None = None
    upper_ticker: str | None = None
    lower_threshold: float | None = None
    upper_threshold: float | None = None
    lower_value: float | None = None
    upper_value: float | None = None
    magnitude: float | None = None


@dataclass
class Ladder:
    """One ordered ladder: a (game, dimension, side-key) family of rungs."""

    game_id: str
    dimension: MarketDimension
    ladder_key: str
    rungs: list[LadderRung] = field(default_factory=list)

    def sorted_rungs(self) -> list[LadderRung]:
        return sorted(self.rungs, key=lambda r: r.threshold)

    @property
    def size(self) -> int:
        return len(self.rungs)


MONOTONICITY_EPSILON = 1e-9
"""Model probabilities come from a Monte Carlo, so exact ties between
adjacent rungs are ordinary. Only a genuine INCREASE is a violation."""

MARKET_INCOHERENCE_EPSILON = 0.0
"""Prices sit on a 1-cent tick, so any strict increase is real rather
than numerical noise. Kept explicit so the choice is visible."""


def check_model_monotonicity(ladder: Ladder) -> list[LadderFinding]:
    """Model probability must be non-increasing as the threshold rises."""
    findings: list[LadderFinding] = []
    rungs = [r for r in ladder.sorted_rungs() if r.model_probability is not None]
    for lower, upper in zip(rungs, rungs[1:], strict=False):
        if upper.model_probability > lower.model_probability + MONOTONICITY_EPSILON:
            findings.append(
                LadderFinding(
                    anomaly=LadderAnomaly.MODEL_MONOTONICITY_VIOLATION,
                    game_id=ladder.game_id,
                    dimension=ladder.dimension,
                    ladder_key=ladder.ladder_key,
                    detail=(
                        f"model probability rose from {lower.model_probability:.4f} at threshold "
                        f"{lower.threshold} to {upper.model_probability:.4f} at {upper.threshold}, but the "
                        f"harder event is a strict subset of the easier one"
                    ),
                    lower_ticker=lower.market_ticker,
                    upper_ticker=upper.market_ticker,
                    lower_threshold=lower.threshold,
                    upper_threshold=upper.threshold,
                    lower_value=lower.model_probability,
                    upper_value=upper.model_probability,
                    magnitude=upper.model_probability - lower.model_probability,
                )
            )
    return findings


def check_market_coherence(ladder: Ladder) -> list[LadderFinding]:
    """Executable YES ask should be non-increasing as the threshold rises.

    Recorded, never corrected -- and explicitly not an arbitrage claim
    (see module docstring)."""
    findings: list[LadderFinding] = []
    rungs = [r for r in ladder.sorted_rungs() if r.executable_yes_price is not None]
    for lower, upper in zip(rungs, rungs[1:], strict=False):
        if upper.executable_yes_price > lower.executable_yes_price + MARKET_INCOHERENCE_EPSILON:
            findings.append(
                LadderFinding(
                    anomaly=LadderAnomaly.MARKET_LADDER_INCOHERENCE,
                    game_id=ladder.game_id,
                    dimension=ladder.dimension,
                    ladder_key=ladder.ladder_key,
                    detail=(
                        f"executable YES ask rose from {lower.executable_yes_price:.2f} at threshold "
                        f"{lower.threshold} to {upper.executable_yes_price:.2f} at {upper.threshold}: the "
                        f"strictly harder event was quoted more expensively"
                    ),
                    lower_ticker=lower.market_ticker,
                    upper_ticker=upper.market_ticker,
                    lower_threshold=lower.threshold,
                    upper_threshold=upper.threshold,
                    lower_value=lower.executable_yes_price,
                    upper_value=upper.executable_yes_price,
                    magnitude=upper.executable_yes_price - lower.executable_yes_price,
                )
            )
    return findings


def check_structural_integrity(ladder: Ladder) -> list[LadderFinding]:
    """Duplicate thresholds, mixed operators, impossible thresholds."""
    findings: list[LadderFinding] = []

    seen: dict[float, str] = {}
    for rung in ladder.sorted_rungs():
        if rung.threshold in seen:
            findings.append(
                LadderFinding(
                    anomaly=LadderAnomaly.DUPLICATE_THRESHOLD,
                    game_id=ladder.game_id,
                    dimension=ladder.dimension,
                    ladder_key=ladder.ladder_key,
                    detail=f"threshold {rung.threshold} appears on both {seen[rung.threshold]} and "
                           f"{rung.market_ticker}",
                    lower_ticker=seen[rung.threshold],
                    upper_ticker=rung.market_ticker,
                    lower_threshold=rung.threshold,
                    upper_threshold=rung.threshold,
                )
            )
        else:
            seen[rung.threshold] = rung.market_ticker

    operators = {r.semantic_operator for r in ladder.rungs if r.semantic_operator is not None}
    if len(operators) > 1:
        findings.append(
            LadderFinding(
                anomaly=LadderAnomaly.INCONSISTENT_SEMANTIC_OPERATOR,
                game_id=ladder.game_id,
                dimension=ladder.dimension,
                ladder_key=ladder.ladder_key,
                detail=f"ladder mixes settlement operators {sorted(operators)}; rungs are not comparable",
            )
        )

    for rung in ladder.rungs:
        # A total can never be negative; a margin threshold beyond any
        # plausible football result signals a parser problem, not a market.
        impossible = (ladder.dimension is MarketDimension.TOTAL and rung.threshold < 0) or abs(
            rung.threshold
        ) > 200
        if impossible:
            findings.append(
                LadderFinding(
                    anomaly=LadderAnomaly.IMPOSSIBLE_THRESHOLD,
                    game_id=ladder.game_id,
                    dimension=ladder.dimension,
                    ladder_key=ladder.ladder_key,
                    detail=f"threshold {rung.threshold} on {rung.market_ticker} is outside any plausible "
                           f"football result and indicates a parse problem",
                    lower_ticker=rung.market_ticker,
                    lower_threshold=rung.threshold,
                )
            )
    return findings


def analyze_ladder(ladder: Ladder) -> list[LadderFinding]:
    return (
        check_structural_integrity(ladder)
        + check_model_monotonicity(ladder)
        + check_market_coherence(ladder)
    )


MODEL_TIE_MASS_EPSILON = 1e-4


def check_model_tie_mass(
    *, game_id: str, home_model_probability: float | None, away_model_probability: float | None
) -> LadderFinding | None:
    """Whether the model's two moneyline probabilities fail to sum to 1.

    *** WHY THIS MATTERS AND WHAT IT DOES NOT MEAN ***
    Settlement partitions every final score into exactly one winner (a 0
    margin resolves to AWAY), so the two winner events are exhaustive and
    their true probabilities must sum to 1. If the model's do not, the
    shortfall is simulated mass sitting on an exact tie -- a margin of 0
    that the model treats as neither team winning.

    This is a MODEL diagnostic. It does not weaken the contract-level
    equivalence in taxonomy.py, which is derived from the settlement rule
    rather than from the model, and it is reported rather than corrected."""
    if home_model_probability is None or away_model_probability is None:
        return None
    total = home_model_probability + away_model_probability
    gap = 1.0 - total
    if abs(gap) <= MODEL_TIE_MASS_EPSILON:
        return None
    return LadderFinding(
        anomaly=LadderAnomaly.MODEL_TIE_MASS,
        game_id=game_id,
        dimension=MarketDimension.WINNER,
        ladder_key=f"{game_id}|WINNER",
        detail=(
            f"model winner probabilities sum to {total:.4f}, leaving {gap:+.4f} unassigned; settlement "
            f"treats the winner events as exhaustive (a 0 margin resolves to AWAY), so this is simulated "
            f"tie mass in the model, not a contract ambiguity"
        ),
        lower_value=home_model_probability,
        upper_value=away_model_probability,
        magnitude=gap,
    )
