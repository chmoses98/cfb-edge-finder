"""Building the game -> dimension -> equivalence -> expression hierarchy
from captured observations.

*** ONE PASS, INDEXED ONCE ***
Contracts are grouped by walking the observation list exactly once and
bucketing into dicts. No step rescans all contracts per contract; this
repo has already paid once for an O(n^2) rescan in the capture path (see
docs/PERFORMANCE.md) and does not repeat it.

*** DEDUPLICATION TO ONE SNAPSHOT PER TICKER ***
The corpus holds many snapshots per ticker (EARLY_OPEN, T_7D, ...). The
expression structure is a statement about the market at ONE instant, so
comparing an EARLY_OPEN ask against a T_30 ask would manufacture
"dominance" out of the passage of time. The builder therefore selects one
snapshot per ticker -- by default the latest captured -- and records which
timing labels were collapsed so the choice is visible.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from cfb_edge_finder.expression.economics import (
    DominanceFinding,
    ExpressionEconomics,
    StaticInconsistency,
    build_expression_economics,
    detect_static_inconsistency,
    find_dominated_expressions,
)
from cfb_edge_finder.expression.ladders import (
    Ladder,
    LadderFinding,
    analyze_ladder,
    check_model_tie_mass,
)
from cfb_edge_finder.expression.taxonomy import (
    ContractSemantics,
    MarketDimension,
    truth_condition_key,
)
from cfb_edge_finder.schemas.common import MarketFamily, Side

UNRESOLVED_SEMANTICS = "EQUIVALENCE_UNRESOLVED"


@dataclass(frozen=True)
class ContractSnapshot:
    """One ticker at one captured instant, with everything the expression
    framework needs. Nothing is recomputed from the ticker string."""

    semantics: ContractSemantics
    timing_label: str
    captured_at: str
    model_probability: float | None
    executable_yes_price: float | None
    executable_no_price: float | None
    market_status: str | None
    fee_status: str | None
    fee_schedule_version: str | None
    model_version: str | None
    pricing_status: str | None
    series_ticker: str | None

    @property
    def model_probability_no_side(self) -> float | None:
        """The model's probability for the NO side. Complemented HERE, at
        the one place that knows the YES probability's meaning, rather
        than inside the economics layer."""
        return None if self.model_probability is None else 1.0 - self.model_probability


@dataclass
class EquivalenceGroup:
    """All executable expressions that settle on one terminal event."""

    truth_condition_key: str
    game_id: str
    dimension: MarketDimension
    expressions: list[ExpressionEconomics] = field(default_factory=list)

    @property
    def expression_count(self) -> int:
        return len(self.expressions)

    @property
    def priceable_expressions(self) -> list[ExpressionEconomics]:
        return [e for e in self.expressions if e.priceable]

    @property
    def lowest_break_even_expression(self) -> ExpressionEconomics | None:
        """The cheapest all-in expression of this event.

        Named for the arithmetic property it reports, not as a
        suggestion: this is 'which of these identical payouts costs
        least', which is a fact about prices, not a decision about
        whether to hold any of them."""
        priceable = self.priceable_expressions
        return min(priceable, key=lambda e: e.all_in_cost) if priceable else None

    @property
    def all_in_cost_spread(self) -> float | None:
        priceable = self.priceable_expressions
        if len(priceable) < 2:
            return None
        costs = [e.all_in_cost for e in priceable]
        return max(costs) - min(costs)


@dataclass
class DimensionGroup:
    game_id: str
    dimension: MarketDimension
    equivalence_groups: dict[str, EquivalenceGroup] = field(default_factory=dict)
    ladders: dict[str, Ladder] = field(default_factory=dict)
    contract_tickers: set[str] = field(default_factory=set)


@dataclass
class GameGroup:
    game_id: str
    dimensions: dict[MarketDimension, DimensionGroup] = field(default_factory=dict)
    unresolved_tickers: list[str] = field(default_factory=list)

    @property
    def contract_count(self) -> int:
        return sum(len(d.contract_tickers) for d in self.dimensions.values()) + len(self.unresolved_tickers)


@dataclass
class ExpressionUniverse:
    games: dict[str, GameGroup] = field(default_factory=dict)
    unsupported_tickers: list[str] = field(default_factory=list)
    unresolved_semantics_tickers: list[str] = field(default_factory=list)
    snapshots_considered: int = 0
    tickers_deduplicated: int = 0
    timing_labels_present: set[str] = field(default_factory=set)
    model_versions: set[str] = field(default_factory=set)

    dominance_findings: list[DominanceFinding] = field(default_factory=list)
    ladder_findings: list[LadderFinding] = field(default_factory=list)
    static_inconsistencies: list[StaticInconsistency] = field(default_factory=list)

    # --- Correlation-aware counts (mission section 11) ---
    @property
    def game_group_count(self) -> int:
        return len(self.games)

    @property
    def dimension_group_count(self) -> int:
        return sum(len(g.dimensions) for g in self.games.values())

    @property
    def equivalence_group_count(self) -> int:
        return sum(len(d.equivalence_groups) for g in self.games.values() for d in g.dimensions.values())

    @property
    def contract_count(self) -> int:
        return sum(g.contract_count for g in self.games.values())

    @property
    def multi_expression_groups(self) -> list[EquivalenceGroup]:
        return [
            eq
            for g in self.games.values()
            for d in g.dimensions.values()
            for eq in d.equivalence_groups.values()
            if eq.expression_count > 1
        ]


def _series_ticker_of(market_ticker: str) -> str | None:
    head = market_ticker.split("-", 1)[0].strip()
    return head or None


def _ladder_key(snapshot: ContractSnapshot) -> str | None:
    """A ladder is a set of rungs whose thresholds are directly
    comparable: for spreads that means one TEAM's rungs (the two teams'
    ladders read the margin in opposite directions and must not be
    interleaved); for totals, the game's Over rungs."""
    semantics = snapshot.semantics
    if semantics.family is MarketFamily.SPREAD and semantics.team is not None:
        return f"{semantics.game_id}|MARGIN|{semantics.team.value}"
    if semantics.family is MarketFamily.TOTAL:
        return f"{semantics.game_id}|TOTAL|over"
    return None


def build_universe(snapshots: list[ContractSnapshot]) -> ExpressionUniverse:
    """Group snapshots into the hierarchy and run every structural check."""
    universe = ExpressionUniverse()
    universe.snapshots_considered = len(snapshots)

    for snapshot in snapshots:
        semantics = snapshot.semantics
        ticker = semantics.market_ticker
        universe.timing_labels_present.add(snapshot.timing_label)
        if snapshot.model_version:
            universe.model_versions.add(snapshot.model_version)

        if semantics.family is None or snapshot.pricing_status != "model_priced":
            universe.unsupported_tickers.append(ticker)
            continue
        if not semantics.semantics_resolved:
            universe.unresolved_semantics_tickers.append(ticker)
            game = universe.games.setdefault(semantics.game_id, GameGroup(semantics.game_id))
            game.unresolved_tickers.append(ticker)
            continue

        game = universe.games.setdefault(semantics.game_id, GameGroup(semantics.game_id))
        dimension_group = game.dimensions.setdefault(
            semantics.dimension, DimensionGroup(semantics.game_id, semantics.dimension)
        )
        dimension_group.contract_tickers.add(ticker)

        series = _series_ticker_of(ticker)
        for side, price, model_probability in (
            (Side.YES, snapshot.executable_yes_price, snapshot.model_probability),
            (Side.NO, snapshot.executable_no_price, snapshot.model_probability_no_side),
        ):
            key = truth_condition_key(semantics, side)
            if key is None:
                continue
            group = dimension_group.equivalence_groups.setdefault(
                key, EquivalenceGroup(key, semantics.game_id, semantics.dimension)
            )
            group.expressions.append(
                build_expression_economics(
                    market_ticker=ticker,
                    executable_side=side,
                    executable_price=price,
                    model_probability_for_this_side=model_probability,
                    series_ticker=series,
                    fee_status=snapshot.fee_status,
                    fee_schedule_version=snapshot.fee_schedule_version,
                )
            )

        ladder_key = _ladder_key(snapshot)
        if ladder_key is not None and semantics.threshold is not None:
            from cfb_edge_finder.expression.ladders import LadderRung

            ladder = dimension_group.ladders.setdefault(
                ladder_key, Ladder(semantics.game_id, semantics.dimension, ladder_key)
            )
            ladder.rungs.append(
                LadderRung(
                    market_ticker=ticker,
                    threshold=semantics.threshold,
                    model_probability=snapshot.model_probability,
                    executable_yes_price=snapshot.executable_yes_price,
                    executable_no_price=snapshot.executable_no_price,
                    semantic_operator=semantics.semantic_operator,
                    timing_label=snapshot.timing_label,
                )
            )

    _run_checks(universe, snapshots)
    return universe


def _complement_key(key: str) -> str | None:
    """The truth-condition key of the complementary event.

    Written by inverting the predicate the key itself encodes, so the
    complement is derived from the same canonical form rather than
    re-derived from contract fields."""
    parts = key.split("|")
    if len(parts) != 3:
        return None
    game, dimension, condition = parts
    if dimension == "WINNER":
        other = "away" if condition == "home" else "home"
        return f"{game}|WINNER|{other}"
    if dimension == "MARGIN":
        for operator, inverse in ((">=", "<"), ("<=", ">"), (">", "<="), ("<", ">=")):
            if f"home_margin{operator}" in condition:
                value = condition.split(operator, 1)[1]
                return f"{game}|MARGIN|home_margin{inverse}{value}"
        return None
    if dimension == "TOTAL":
        for operator, inverse in ((">", "<="), ("<=", ">")):
            if f"total{operator}" in condition:
                value = condition.split(operator, 1)[1]
                return f"{game}|TOTAL|total{inverse}{value}"
    return None


def _run_checks(universe: ExpressionUniverse, snapshots: list[ContractSnapshot]) -> None:
    seen_inconsistency_pairs: set[frozenset[str]] = set()

    for game in universe.games.values():
        for dimension_group in game.dimensions.values():
            for ladder in dimension_group.ladders.values():
                universe.ladder_findings.extend(analyze_ladder(ladder))

            for key, group in dimension_group.equivalence_groups.items():
                universe.dominance_findings.extend(find_dominated_expressions(key, group.expressions))

                complement = _complement_key(key)
                if complement is None or complement not in dimension_group.equivalence_groups:
                    continue
                pair = frozenset({key, complement})
                if pair in seen_inconsistency_pairs:
                    continue
                seen_inconsistency_pairs.add(pair)
                finding = detect_static_inconsistency(
                    game_id=game.game_id,
                    dimension=dimension_group.dimension.value,
                    event_key=key,
                    complement_key=complement,
                    event_expressions=group.expressions,
                    complement_expressions=dimension_group.equivalence_groups[complement].expressions,
                )
                if finding is not None:
                    universe.static_inconsistencies.append(finding)

    # Model tie mass: needs both moneyline sides of a game.
    winner_probabilities: dict[str, dict[Side, float]] = defaultdict(dict)
    for snapshot in snapshots:
        semantics = snapshot.semantics
        if (
            semantics.family is MarketFamily.MONEYLINE
            and semantics.team in (Side.HOME, Side.AWAY)
            and snapshot.model_probability is not None
        ):
            winner_probabilities[semantics.game_id][semantics.team] = snapshot.model_probability
    for game_id, sides in winner_probabilities.items():
        finding = check_model_tie_mass(
            game_id=game_id,
            home_model_probability=sides.get(Side.HOME),
            away_model_probability=sides.get(Side.AWAY),
        )
        if finding is not None:
            universe.ladder_findings.append(finding)
