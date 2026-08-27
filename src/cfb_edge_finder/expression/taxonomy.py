"""The grouping hierarchy and the structural correlation taxonomy.

*** THE HIERARCHY ***
    game_group        one CFB game
      dimension_group one latent football quantity (winner / margin / total)
        equivalence_group  one terminal truth condition
          expression       one executable side of one ticker

The levels are deliberately distinct. `Team A -3.5` and `Team A -7.5`
share a DIMENSION (the same final margin decides both) but are not the
same EVENT. `Team A wins YES` and `Team B wins NO` are the same EVENT
expressed two ways. Collapsing those two ideas is exactly the mistake
that makes four correlated positions look like four independent theses.

*** EQUIVALENCE IS PROVED FROM SETTLEMENT SEMANTICS, NEVER FROM TICKERS ***
Mission section 23. Two contracts are declared equivalent only when the
persisted contract semantics -- family, team/side, threshold, operator --
imply identical settlement truth conditions under
`research/settlement.py`'s actual rules. Anything short of that is
`EQUIVALENCE_UNRESOLVED` and is excluded from exact-equivalence
comparison rather than guessed at.

*** WHY THE MONEYLINE PAIR IS EXACT ***
`settle_market` computes `actual_winner = HOME if home_margin > 0 else
AWAY`, and a moneyline contract settles YES iff its own team is that
winner. Exactly one of HOME/AWAY is the winner for every possible final
score -- including a 0 margin, which that rule assigns to AWAY. The
sample space is therefore partitioned with no gap, so:

    (home ticker, YES)  ==  (away ticker, NO)      both mean "home wins"
    (away ticker, YES)  ==  (home ticker, NO)      both mean "away wins"

This is a property of the SETTLEMENT rule, not of the model. The model's
own two moneyline probabilities need not sum to 1 (see
`model_tie_mass` in ladders.py) -- that discrepancy is a reportable model
diagnostic and has no bearing on whether the CONTRACTS are equivalent.

*** WHY CROSS-TEAM SPREAD EQUIVALENCE IS NOT CLAIMED ***
"away margin > u" is "home margin < -u"; the complement of "home margin >
t" is "home margin <= t". For integer scores and half-point lines these
coincide only for specific (t, u) pairs that do not arise in the observed
ladder structure. Rather than encode a fragile arithmetic special case,
cross-team spread relationships are classified as nested-same-dimension,
never exact. Under-claiming equivalence is the safe failure mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cfb_edge_finder.schemas.common import MarketFamily, Side


class MarketDimension(StrEnum):
    """The latent football quantity a contract resolves against."""

    MARGIN = "MARGIN"
    """The final margin. BOTH the moneyline and every spread rung read
    this one number: a moneyline is simply the rung at threshold 0
    ("home_margin > 0"), which is why `Team A ML`, `Team A -3.5` and
    `Team A -7.5` belong to one margin thesis group rather than to
    separate dimensions. Treating the winner as its own dimension would
    hide that a moneyline and a spread on the same team move together."""

    WINNER = "WINNER"
    """Retained as a LABEL for winner-specific diagnostics (see
    `check_model_tie_mass`). Deliberately not a grouping dimension -- no
    market family maps to it, because the winner is not a separate latent
    quantity from the margin."""

    TOTAL = "TOTAL"
    """Combined final score."""

    UNKNOWN = "UNKNOWN"


class CorrelationClass(StrEnum):
    """Structural relationship between two expressions. Deterministic and
    logical -- deliberately NOT estimated from settled outcomes, of which
    this corpus currently has none."""

    EXACT_EQUIVALENT = "EXACT_EQUIVALENT"
    """Provably identical settlement truth conditions."""

    SAME_MARGIN_DIMENSION_NESTED = "SAME_MARGIN_DIMENSION_NESTED"
    """Both read the final margin, different thresholds/teams."""

    SAME_TOTAL_DIMENSION_NESTED = "SAME_TOTAL_DIMENSION_NESTED"
    """Both read the combined score, different thresholds."""

    SAME_GAME_DIFFERENT_DIMENSION = "SAME_GAME_DIFFERENT_DIMENSION"
    """Same game, different latent quantity (e.g. winner vs total).
    Correlated through the game, not through one number."""

    UNRELATED_GAME = "UNRELATED_GAME"

    EQUIVALENCE_UNRESOLVED = "EQUIVALENCE_UNRESOLVED"
    """Semantics too incomplete to classify. Never treated as equivalent."""


FAMILY_TO_DIMENSION = {
    # Moneyline and spread share the MARGIN dimension on purpose -- see
    # MarketDimension.MARGIN's docstring.
    MarketFamily.MONEYLINE: MarketDimension.MARGIN,
    MarketFamily.SPREAD: MarketDimension.MARGIN,
    MarketFamily.TOTAL: MarketDimension.TOTAL,
}

SUPPORTED_OPERATOR = ">"
"""The only settlement operator this repo has verified live. Anything
else is unresolved rather than assumed."""


@dataclass(frozen=True)
class ContractSemantics:
    """The persisted semantics of one contract, as captured. Nothing here
    is re-parsed from the ticker string at analysis time."""

    market_ticker: str
    game_id: str
    family: MarketFamily | None
    team: Side | None
    side: Side | None
    threshold: float | None
    semantic_operator: str | None
    parse_status: str | None = None

    @property
    def dimension(self) -> MarketDimension:
        if self.family is None:
            return MarketDimension.UNKNOWN
        return FAMILY_TO_DIMENSION.get(self.family, MarketDimension.UNKNOWN)

    @property
    def semantics_resolved(self) -> bool:
        """Whether the captured semantics are complete enough to reason
        about the truth condition at all."""
        if self.family is MarketFamily.MONEYLINE:
            return self.team in (Side.HOME, Side.AWAY)
        if self.family is MarketFamily.SPREAD:
            return (
                self.team in (Side.HOME, Side.AWAY)
                and self.threshold is not None
                and self.semantic_operator == SUPPORTED_OPERATOR
            )
        if self.family is MarketFamily.TOTAL:
            return (
                self.side is Side.OVER
                and self.threshold is not None
                and self.semantic_operator == SUPPORTED_OPERATOR
            )
        return False


def truth_condition_key(semantics: ContractSemantics, executable_side: Side) -> str | None:
    """A canonical string naming the EVENT this executable side pays out on.

    Two expressions share a key exactly when they settle together, always.
    Returns None when semantics are unresolved -- callers must then treat
    the contract as EQUIVALENCE_UNRESOLVED rather than grouping it.

    The keys are written as explicit predicates over the game's own
    quantities (`home_margin`, `total`) so that reading two keys side by
    side is enough to see why they match."""
    if executable_side not in (Side.YES, Side.NO):
        raise ValueError(f"executable_side must be YES or NO, got {executable_side!r}")
    if not semantics.semantics_resolved:
        return None

    game = semantics.game_id
    if semantics.family is MarketFamily.MONEYLINE:
        # Expressed in the SAME canonical margin language as the spread
        # rungs, because a moneyline is the rung at threshold 0.
        # Settlement sets actual_winner = HOME iff home_margin > 0, so:
        #   home wins  <=>  home_margin > 0
        #   away wins  <=>  home_margin <= 0        (a 0 margin is AWAY)
        # YES on the home ticket and NO on the away ticket therefore
        # produce the identical key, which is the exact-equivalence the
        # settlement rule guarantees.
        team_wins = semantics.team
        if executable_side is Side.NO:
            team_wins = Side.AWAY if team_wins is Side.HOME else Side.HOME
        condition = "home_margin>0" if team_wins is Side.HOME else "home_margin<=0"
        return f"{game}|MARGIN|{condition}"

    if semantics.family is MarketFamily.SPREAD:
        # Settlement: team_margin > threshold, where away_margin is
        # -home_margin. Normalizing everything onto home_margin makes two
        # differently-expressed-but-identical conditions collide.
        t = semantics.threshold
        if semantics.team is Side.HOME:
            yes_condition = f"home_margin>{t}"
            no_condition = f"home_margin<={t}"
        else:
            yes_condition = f"home_margin<{-t}"
            no_condition = f"home_margin>={-t}"
        return f"{game}|MARGIN|{yes_condition if executable_side is Side.YES else no_condition}"

    if semantics.family is MarketFamily.TOTAL:
        t = semantics.threshold
        condition = f"total>{t}" if executable_side is Side.YES else f"total<={t}"
        return f"{game}|TOTAL|{condition}"

    return None


def classify_pair(a: ContractSemantics, b: ContractSemantics) -> CorrelationClass:
    """Structural relationship between two CONTRACTS (not sides).

    Deliberately conservative: anything whose semantics are incomplete is
    EQUIVALENCE_UNRESOLVED, never optimistically related."""
    if a.game_id != b.game_id:
        return CorrelationClass.UNRELATED_GAME
    if not (a.semantics_resolved and b.semantics_resolved):
        return CorrelationClass.EQUIVALENCE_UNRESOLVED

    # Same event on the same executable side => exact.
    a_yes = truth_condition_key(a, Side.YES)
    b_yes = truth_condition_key(b, Side.YES)
    b_no = truth_condition_key(b, Side.NO)
    if a_yes is not None and (a_yes == b_yes or a_yes == b_no):
        return CorrelationClass.EXACT_EQUIVALENT

    if a.dimension is not b.dimension:
        return CorrelationClass.SAME_GAME_DIFFERENT_DIMENSION
    if a.dimension is MarketDimension.MARGIN:
        return CorrelationClass.SAME_MARGIN_DIMENSION_NESTED
    if a.dimension is MarketDimension.TOTAL:
        return CorrelationClass.SAME_TOTAL_DIMENSION_NESTED
    return CorrelationClass.EQUIVALENCE_UNRESOLVED
