"""The card review: turning a shortlist into a card without mistaking
correlated contracts for independent ideas.

*** WHY THIS EXISTS ***
The 2026-09-26 slate went 20-12 and still lost money on one game whose thesis
was RIGHT. The favourite won -- the moneyline cashed -- but two more rungs of
the same margin ladder were on the card beside it, each needing a bigger win
than the last, and together they carried several times the moneyline's risk.
Every contract was positive-EV on its own; nothing on the card said that the
second and third rungs only ever paid in outcomes where the first already had.

Elsewhere on the same slate an underdog moneyline and the same underdog's
points were both on the card, and that pair was fine: a close loss cashed the
points without the moneyline. Correlated, but not redundant.

The reducer (`candidates.py`) already keeps one best expression per VIEW and
groups survivors by game and THESIS. What it did not do is tell the final
reviewer, in words a machine can check, what promoting a second expression
DOES to the card. This module does that.

*** WHAT IT SAYS ***
  CORE EXPRESSION            the reducer's survivor for a view.
  INCREMENTAL CANDIDATE      every other rung of that view. On the card it is
                             not an alternative any more: it is more exposure
                             to the same opinion, and it must earn it.
  CASH PATH                  derived from the contract's own terms, never from
                             a result: can this position pay when the core
                             does not (an INDEPENDENT CASH PATH), or does it
                             only ever pay when the core already has (a NESTED
                             TAIL EXTENSION)?

*** WHAT IT NEVER SAYS ***
Correlation is not a veto. It is an exposure fact. Nothing here rejects,
promotes, ranks or truncates anything, and nothing here sizes anything: every
exposure figure is a null the operator's own final review fills in after
stakes are chosen OUTSIDE this repository. The vocabulary is generic (an
outcome variable, an interval of integer outcomes) so it ports to any sport
whose markets settle on integer counts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only; candidates imports this module
    from cfb_edge_finder.execution.candidates import Expression, Reduction

CARD_REVIEW_VERSION = "cfb_card_review/1.0.0"


class CardRole(StrEnum):
    CORE_EXPRESSION = "core_expression"
    """The reducer's survivor for one view: the best buy of that opinion."""

    INCREMENTAL_EXPRESSION_CANDIDATE = "incremental_expression_candidate"
    """Another rung of a view whose core survived. Placing it on the card
    beside the core adds exposure to the same opinion."""

    DUPLICATE_LISTING = "duplicate_listing"
    """The same outcome on the same side as a kept row. Placing it beside the
    kept row is simply more of the same bet."""


class CashPathRelation(StrEnum):
    """How one position's winning outcomes relate to another's.

    Derived from contract terms alone. A relation never depends on how the
    game actually finished."""

    EQUIVALENT_OUTCOME_SET = "equivalent_outcome_set"
    """Wins in exactly the same outcomes. A second position is the same bet."""

    NESTED_TAIL_EXTENSION = "nested_tail_extension"
    """Wins only in a strict subset of the reference's winning outcomes: it
    needs a STRONGER version of the same game script, and can never cash when
    the reference does not."""

    CORRELATED_INDEPENDENT_CASH_PATH = "correlated_independent_cash_path"
    """Shares the opinion, but has winning outcomes in which the reference
    loses -- the underdog that loses a close game still covers the points."""

    SAME_THESIS_DIFFERENT_MECHANISM = "same_thesis_different_mechanism"
    """Same game-level opinion, settled on a different quantity (another
    period, another scoring measure). Positively related, and able to cash
    separately; how strongly they move together is not derivable here."""

    DIFFERENT_MECHANISM = "different_mechanism"
    """Different quantity and not the same thesis."""

    MUTUALLY_EXCLUSIVE = "mutually_exclusive"
    """No outcome pays both: opposing positions with no middle."""

    OPPOSING_MIDDLE = "opposing_middle"
    """Opposing directions whose winning outcomes overlap: a middle."""

    UNDETERMINED = "undetermined"
    """The contract terms do not reduce to one interval of one outcome
    variable (a NO on a band, an explicit-probability contract), so the
    relation is left to the reviewer rather than guessed."""


class CardDecision(StrEnum):
    """The reviewer's verdict on each position. Never set by this repository."""

    CORE_EXPRESSION = "CORE_EXPRESSION"
    INCREMENTAL_EXPOSURE_JUSTIFIED = "INCREMENTAL_EXPOSURE_JUSTIFIED"
    REDUNDANT_CORRELATED_EXPRESSION = "REDUNDANT_CORRELATED_EXPRESSION"
    TAIL_EXTENSION_NOT_JUSTIFIED = "TAIL_EXTENSION_NOT_JUSTIFIED"
    CONCENTRATION_TOO_HIGH = "CONCENTRATION_TOO_HIGH"
    OPPOSING_HEDGE_WITH_PURPOSE = "OPPOSING_HEDGE_WITH_PURPOSE"
    OPPOSING_POSITION_CONTRADICTS_THESIS = "OPPOSING_POSITION_CONTRADICTS_THESIS"


# ------------------------------------------------------------ win sets

_INF = math.inf


@dataclass(frozen=True)
class WinSet:
    """The integer outcomes in which a position pays, on one outcome variable.

    `variable` is a period-qualified quantity -- `full_game:home_margin`,
    `first_half:total_points`, `full_game:away_points`. Every margin is stated
    as HOME minus AWAY, so an away-team contract is the same variable
    reflected, and two contracts quoted from opposite ends compare directly.
    """

    variable: str
    low: float
    high: float

    @property
    def empty(self) -> bool:
        return self.low > self.high

    def as_dict(self) -> dict[str, Any]:
        return {
            "variable": self.variable,
            "at_least": None if self.low == -_INF else int(self.low),
            "at_most": None if self.high == _INF else int(self.high),
            "description": describe(self.variable, [(self.low, self.high)]),
        }


def describe(variable: str, intervals: list[tuple[float, float]]) -> str:
    label = variable.replace(":", " ").replace("_", " ")
    if not intervals:
        return "no outcome"
    parts = []
    for low, high in intervals:
        if low == -_INF and high == _INF:
            parts.append(f"any {label}")
        elif low == -_INF:
            parts.append(f"{label} <= {int(high)}")
        elif high == _INF:
            parts.append(f"{label} >= {int(low)}")
        elif low == high:
            parts.append(f"{label} == {int(low)}")
        else:
            parts.append(f"{label} between {int(low)} and {int(high)}")
    return " or ".join(parts)


def _yes_interval(line: Any, comparator: str) -> tuple[float, float] | None:
    """The integer outcomes a YES pays on, for a one-sided strike.

    Mirrors the evaluator's `_cutpoint`: "over 7.5" and "over 7" both mean
    at least 8; "7 or more" means at least 7."""
    if not isinstance(line, (int, float)) or isinstance(line, bool):
        return None
    value = float(line)
    if comparator == "greater":
        return math.floor(value) + 1, _INF
    if comparator == "greater_or_equal":
        return math.ceil(value), _INF
    if comparator == "less":
        return -_INF, math.ceil(value) - 1
    if comparator == "less_or_equal":
        return -_INF, math.floor(value)
    return None


def _complement(interval: tuple[float, float]) -> tuple[float, float] | None:
    """Of a half-line, the other half-line. Of anything else, None: the NO of
    a bounded band is two pieces, which is not one interval."""
    low, high = interval
    if low == -_INF and high == _INF:
        return None
    if low == -_INF:
        return high + 1, _INF
    if high == _INF:
        return -_INF, low - 1
    return None


def win_set(row: dict[str, Any]) -> WinSet | None:
    """What a priced row actually pays on, or None when that is not one interval."""
    kind = str(row.get("kind") or "")
    period = str(row.get("period") or "")
    team = str(row.get("team") or "")
    side = str(row.get("best_side") or "yes")
    comparator = str(row.get("comparator") or "greater")
    line = row.get("line")

    if kind in ("moneyline", "spread", "margin_band"):
        if team not in ("home", "away"):
            return None
        if kind == "moneyline":
            yes: tuple[float, float] | None = (1, _INF)
        elif kind == "spread":
            yes = _yes_interval(line, comparator)
        else:
            # A band's upper bound is part of its terms. A row that does not
            # carry the key (a ledger written before rows did) is not treated
            # as open-ended: that would overstate what the band pays on.
            if "cap" not in row:
                return None
            yes = _yes_interval(line, comparator)
            cap = row.get("cap")
            if yes is not None and isinstance(cap, (int, float)) and not isinstance(cap, bool):
                yes = (yes[0], math.floor(float(cap)))
        if yes is None:
            return None
        if team == "away":
            yes = (-yes[1], -yes[0])
        variable = f"{period}:home_margin"
    elif kind == "total":
        yes = _yes_interval(line, comparator)
        variable = f"{period}:total_points"
    elif kind == "team_total":
        if team not in ("home", "away"):
            return None
        yes = _yes_interval(line, comparator)
        variable = f"{period}:{team}_points"
    else:
        return None

    if yes is None:
        return None
    interval = yes if side == "yes" else _complement(yes)
    if interval is None:
        return None
    result = WinSet(variable=variable, low=interval[0], high=interval[1])
    return None if result.empty else result


def _union_gaps(a: WinSet, b: WinSet) -> list[tuple[float, float]]:
    """The integer outcomes in which NEITHER pays, as intervals."""
    pieces = sorted([(a.low, a.high), (b.low, b.high)])
    merged: list[list[float]] = []
    for low, high in pieces:
        if merged and low <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    gaps: list[tuple[float, float]] = []
    cursor = -_INF  # the lowest outcome not yet covered
    for low, high in merged:
        if low > cursor:
            gaps.append((cursor, low - 1))
        cursor = max(cursor, high + 1)
    if cursor < _INF:
        gaps.append((cursor, _INF))
    return [(low, high) for low, high in gaps if low <= high]


def _extension_points(inner: WinSet, outer: WinSet) -> int | None:
    """How much further into the tail `inner` reaches than `outer`."""
    total = 0
    finite = False
    if inner.low != -_INF and outer.low != -_INF:
        total += int(inner.low - outer.low)
        finite = True
    if inner.high != _INF and outer.high != _INF:
        total += int(outer.high - inner.high)
        finite = True
    return total if finite else None


def relate(
    candidate: dict[str, Any],
    reference: dict[str, Any],
    *,
    same_thesis: bool = True,
    opposing: bool = False,
) -> dict[str, Any]:
    """The cash-path relation of `candidate` to `reference`, from terms alone.

    `same_thesis` and `opposing` are what the caller already knows from the
    grouping keys; the outcome sets decide everything else."""
    a, b = win_set(candidate), win_set(reference)
    out: dict[str, Any] = {
        "relation": CashPathRelation.UNDETERMINED.value,
        "relative_to": reference.get("ticker"),
        "this_wins_when": a.as_dict() if a else None,
        "reference_wins_when": b.as_dict() if b else None,
        "cashes_when_reference_fails": None,
        "reference_cashes_when_this_fails": None,
        "both_can_cash": None,
        "both_lose_when": None,
        "tail_extension": False,
        "extension_points": None,
    }
    if a is None or b is None:
        return out
    if a.variable != b.variable:
        out["relation"] = (
            CashPathRelation.SAME_THESIS_DIFFERENT_MECHANISM.value
            if same_thesis
            else CashPathRelation.DIFFERENT_MECHANISM.value
        )
        return out

    overlap = max(a.low, b.low) <= min(a.high, b.high)
    a_in_b = b.low <= a.low and a.high <= b.high
    b_in_a = a.low <= b.low and b.high <= a.high
    out["cashes_when_reference_fails"] = not a_in_b
    out["reference_cashes_when_this_fails"] = not b_in_a
    out["both_can_cash"] = overlap
    out["both_lose_when"] = describe(a.variable, _union_gaps(a, b))

    if opposing:
        out["relation"] = (
            CashPathRelation.OPPOSING_MIDDLE.value
            if overlap
            else CashPathRelation.MUTUALLY_EXCLUSIVE.value
        )
    elif a_in_b and b_in_a:
        out["relation"] = CashPathRelation.EQUIVALENT_OUTCOME_SET.value
    elif a_in_b:
        out["relation"] = CashPathRelation.NESTED_TAIL_EXTENSION.value
        out["tail_extension"] = True
        out["extension_points"] = _extension_points(a, b)
    elif not overlap:
        out["relation"] = CashPathRelation.MUTUALLY_EXCLUSIVE.value
    else:
        out["relation"] = CashPathRelation.CORRELATED_INDEPENDENT_CASH_PATH.value
    return out


def incremental_fields(relation: dict[str, Any], *, duplicate: bool = False) -> dict[str, Any]:
    """What a removed row carries so promoting it can never look free.

    `relation` is `relate(row, core)`. Only the part a reviewer acts on is
    kept: an alternative is repeated on every candidate that carries it, and
    the core's own outcome set is already on the core."""
    wins = relation.get("this_wins_when") or {}
    return {
        "card_role": (
            CardRole.DUPLICATE_LISTING.value
            if duplicate
            else CardRole.INCREMENTAL_EXPRESSION_CANDIDATE.value
        ),
        "requires_incremental_justification": True,
        "justification_standard": _justification_standard(relation["relation"]),
        "cash_path": {
            "relation": relation["relation"],
            "wins_when": wins.get("description"),
            "cashes_when_core_fails": relation["cashes_when_reference_fails"],
            "tail_extension": relation["tail_extension"],
            "extension_points": relation["extension_points"],
        },
    }


def _justification_standard(relation: str) -> str:
    if relation in (
        CashPathRelation.NESTED_TAIL_EXTENSION.value,
        CashPathRelation.EQUIVALENT_OUTCOME_SET.value,
    ):
        return "heightened"
    return "standard"


# ------------------------------------------------------------ the contract

PRINCIPLES = (
    "Correlation is not a veto. It is an exposure fact.",
    "Multiple tickers do not automatically mean multiple independent bets.",
    "Every additional correlated position must earn incremental exposure.",
    "An aggressive ladder rung is a tail extension, not a free second edge.",
)

PIPELINE = (
    "game_thesis",
    "candidate_markets",
    "correlation_thesis_map",
    "core_expression",
    "incremental_expression_test",
    "game_thesis_exposure_review",
    "slate_concentration_review",
    "final_card",
)

RULES = (
    {
        "id": "one_core_per_funded_thesis",
        "rule": "Choose exactly one CORE expression for every thesis that is funded.",
    },
    {
        "id": "additional_expression_needs_justification",
        "rule": (
            "Any additional correlated expression of a funded thesis requires an explicit, completed "
            "incremental_justification."
        ),
    },
    {
        "id": "alternate_rungs_are_not_independent",
        "rule": "Alternate ladder rungs are not independent bets; placing one beside its core is incremental exposure.",
    },
    {
        "id": "review_game_exposure_after_sizing",
        "rule": (
            "After stakes are assigned outside this repository, sum and examine the combined exposure "
            "of each game."
        ),
    },
    {
        "id": "review_thesis_exposure_after_sizing",
        "rule": (
            "After stakes are assigned outside this repository, sum and examine the combined exposure "
            "of each thesis."
        ),
    },
    {
        "id": "state_common_failure_mode",
        "rule": "Name the outcome in which correlated positions fail together.",
    },
    {
        "id": "independent_cash_path_may_survive",
        "rule": (
            "An additional expression may stay on the card despite correlation when it has a materially "
            "different cash path."
        ),
    },
    {
        "id": "correlation_is_not_a_rejection_rule",
        "rule": "Correlation alone is never a reason to reject an otherwise good bet. It is an exposure fact.",
    },
    {
        "id": "slate_concentration_review",
        "rule": "The final slate receives a slate-level concentration review before it is returned.",
    },
)

FINAL_REVIEW_CHECKLIST = (
    ("build_game_thesis", "Build the game thesis first."),
    ("choose_core_expression", "Choose the best/core expression of it."),
    ("inspect_related_alternatives", "Inspect every related alternative."),
    (
        "justify_incremental_expression",
        "If selecting another correlated expression, state why it is incremental rather than redundant.",
    ),
    ("state_shared_failure_mode", "State the shared failure mode."),
    ("state_independent_cash_path", "State whether it has an independent cash path."),
    ("identify_tail_extension", "If it is a more aggressive rung, identify it as a tail extension."),
    (
        "sum_exposure_after_sizing",
        "After assigning stakes externally, sum exposure by bet, by thesis, by game and by slate.",
    ),
    ("flag_concentrated_games", "Flag concentrated games."),
    (
        "no_exposure_from_ticker_count",
        "Do not increase total game exposure simply because multiple attractive tickers exist.",
    ),
    (
        "do_not_reject_for_correlation_alone",
        "Do not reject a genuinely valuable second expression merely because it is correlated.",
    ),
)

INCREMENTAL_JUSTIFICATION_TEMPLATE = {
    "shared_thesis": None,
    "shared_failure_mode": None,
    "independent_cash_path": None,
    "incremental_edge_case": None,
    "why_not_redundant": None,
    "tail_extension": None,
    "concentration_effect": None,
    "decision": None,
}

CASH_PATH_VOCABULARY = {
    relation.value: " ".join((relation.__doc__ or "").split()) for relation in CashPathRelation
}

DECISIONS = {
    CardDecision.CORE_EXPRESSION.value: "the one expression chosen to carry a funded thesis",
    CardDecision.INCREMENTAL_EXPOSURE_JUSTIFIED.value: (
        "an additional correlated expression whose completed justification shows it adds expected "
        "profit worth its extra downside when the shared thesis fails"
    ),
    CardDecision.REDUNDANT_CORRELATED_EXPRESSION.value: (
        "an additional expression that pays in the same outcomes as one already on the card"
    ),
    CardDecision.TAIL_EXTENSION_NOT_JUSTIFIED.value: (
        "a more aggressive rung whose only path to pay is a stronger version of a script the card "
        "already backs, without a case strong enough to fund it"
    ),
    CardDecision.CONCENTRATION_TOO_HIGH.value: (
        "a justified expression left off because the game's or thesis's combined exposure is already "
        "too large a share of the card"
    ),
    CardDecision.OPPOSING_HEDGE_WITH_PURPOSE.value: (
        "a position against the card's own thesis in the same game, held for a stated reason (a middle, "
        "a priced hedge)"
    ),
    CardDecision.OPPOSING_POSITION_CONTRADICTS_THESIS.value: (
        "a position against the card's own thesis in the same game with no stated purpose"
    ),
}


def _exposure_placeholders(*names: str) -> dict[str, Any]:
    return {name: None for name in names} | {"supplied_by": "operator_final_review"}


# ------------------------------------------------------------ the review

_OPPOSITES = {"home": "away", "away": "home", "over": "under", "under": "over"}


def opposite_thesis(thesis: str) -> str | None:
    """`side:home` <-> `side:away`, `total:over` <-> `total:under`, and the
    same for a team-scoring thesis. Standalone contracts have no opposite."""
    head, _, direction = thesis.rpartition(":")
    if not head or direction not in _OPPOSITES or head.startswith("standalone"):
        return None
    return f"{head}:{_OPPOSITES[direction]}"


def thesis_index(survivors: list[Expression]) -> dict[tuple[str, str], list[Expression]]:
    index: dict[tuple[str, str], list[Expression]] = {}
    for expression in survivors:
        index.setdefault(expression.thesis_key, []).append(expression)
    for members in index.values():
        members.sort(key=lambda e: e.ticker)
    return index


def core_fields(
    expression: Expression, index: dict[tuple[str, str], list[Expression]]
) -> dict[str, Any]:
    """What a surviving candidate carries about its place on a card."""
    peers = [e.ticker for e in index.get(expression.thesis_key, []) if e.ticker != expression.ticker]
    wins = win_set(expression.row)
    return {
        "card_role": CardRole.CORE_EXPRESSION.value,
        "card_role_scope": "view",
        "thesis_group": f"{expression.thesis_key[0]}:{expression.thesis_key[1]}",
        "wins_when": wins.as_dict() if wins else None,
        "thesis_core_expression_count": len(peers) + 1,
        "thesis_peers": peers,
        "funding_beside_thesis_peers_requires_incremental_justification": bool(peers),
    }


def build_card_review(reduction: Reduction) -> dict[str, Any]:
    """The machine-readable final-review contract and the structure it fills.

    Built from the WHOLE reduction -- every survivor and every alternative --
    never from a display-truncated list. It counts and classifies; it does not
    select, reject, rank, or size.
    """
    index = thesis_index(list(reduction.survivors))
    by_game: dict[str, dict[str, list[Expression]]] = {}
    for (game, thesis), members in index.items():
        by_game.setdefault(game, {})[thesis] = members

    games: dict[str, Any] = {}
    slate_tail = 0
    slate_alternatives = 0
    for game in sorted(by_game):
        theses = by_game[game]
        thesis_blocks: dict[str, Any] = {}
        game_alternatives = 0
        game_tails: list[str] = []
        game_independent: list[str] = []
        for thesis in sorted(theses):
            cores = theses[thesis]
            by_relation: dict[str, list[str]] = {}
            tails: list[dict[str, Any]] = []
            alternative_count = 0
            for core in cores:
                for alternative in reduction.alternatives.get(core.ticker, []):
                    alternative_count += 1
                    relation = (alternative.get("cash_path") or {}).get(
                        "relation", CashPathRelation.UNDETERMINED.value
                    )
                    by_relation.setdefault(relation, []).append(str(alternative["ticker"]))
                    if relation == CashPathRelation.NESTED_TAIL_EXTENSION.value:
                        tails.append(
                            {
                                "ticker": alternative["ticker"],
                                "core": core.ticker,
                                "extension_points": alternative["cash_path"].get("extension_points"),
                                "wins_when": alternative["cash_path"].get("wins_when"),
                            }
                        )
                    elif relation == CashPathRelation.CORRELATED_INDEPENDENT_CASH_PATH.value:
                        game_independent.append(str(alternative["ticker"]))
            tails.sort(
                key=lambda t: (
                    t["core"],
                    t["extension_points"] if t["extension_points"] is not None else -1,
                    t["ticker"],
                )
            )
            game_alternatives += alternative_count
            game_tails.extend(t["ticker"] for t in tails)
            core_pairs = [
                {"a": a.ticker, "b": b.ticker, **_pair(a, b, same_thesis=True, opposing=False)}
                for i, a in enumerate(cores)
                for b in cores[i + 1 :]
            ]
            thesis_blocks[thesis] = {
                "thesis_key": f"{game}:{thesis}",
                "core_expressions": [c.ticker for c in cores],
                "multiple_core_expressions": len(cores) > 1,
                "core_expression_relations": core_pairs,
                "related_incremental_candidates": {k: sorted(v) for k, v in sorted(by_relation.items())},
                "related_incremental_candidate_count": alternative_count,
                "nested_tail_extensions": tails,
                # The most aggressive rung beside each core: the one whose only
                # path to pay is the strongest version of the script.
                "deepest_tail_extension_by_core": {
                    t["core"]: t["ticker"] for t in tails if t["extension_points"] is not None
                },
                "core_loses_when": {c.ticker: _loses_when(c.row) for c in cores},
                "common_failure_mode": None,
                "exposure_after_sizing": _exposure_placeholders(
                    "thesis_exposure", "percent_of_card_risk", "concentration_flag"
                ),
            }

        opposing = []
        for thesis in sorted(theses):
            other = opposite_thesis(thesis)
            if other is None or other not in theses or other < thesis:
                continue
            for a in theses[thesis]:
                for b in theses[other]:
                    opposing.append(
                        {"a": a.ticker, "b": b.ticker, **_pair(a, b, same_thesis=False, opposing=True)}
                    )

        core_count = sum(len(c) for c in theses.values())
        extra_cores = core_count - len(theses)
        slate_tail += len(game_tails)
        slate_alternatives += game_alternatives
        games[game] = {
            "core_candidate_count": core_count,
            "core_expressions": sorted(e.ticker for c in theses.values() for e in c),
            "thesis_groups": sorted(theses),
            "thesis_group_count": len(theses),
            "multiple_thesis_groups": len(theses) > 1,
            "related_alternative_count": game_alternatives,
            "possible_incremental_expression_count": game_alternatives + extra_cores,
            "has_nested_tail_extension": bool(game_tails),
            "nested_tail_extension_candidates": sorted(game_tails),
            "independent_cash_path_candidates": sorted(game_independent),
            "opposing_positions": opposing,
            "exposure_warning": (
                "Every position placed on this game shares its outcome. Total game exposure must be "
                "summed across all of them AFTER stakes are assigned; more attractive tickers are "
                "not a reason for more game exposure."
            ),
            "exposure_after_sizing": _exposure_placeholders(
                "total_game_exposure", "percent_of_card_risk", "concentration_flag"
            ),
            "theses": thesis_blocks,
        }

    return {
        "contract_version": CARD_REVIEW_VERSION,
        "required_before_final_card": True,
        "sizing_authority": "operator",
        "repository_sizes_positions": False,
        "principles": list(PRINCIPLES),
        "pipeline": list(PIPELINE),
        "rules": [dict(rule) for rule in RULES],
        "final_review_checklist": [
            {"step": number, "id": key, "instruction": text}
            for number, (key, text) in enumerate(FINAL_REVIEW_CHECKLIST, start=1)
        ],
        "card_roles": {role.value: " ".join((role.__doc__ or "").split()) for role in CardRole},
        "cash_path_vocabulary": dict(CASH_PATH_VOCABULARY),
        "decisions": dict(DECISIONS),
        "incremental_justification_template": dict(INCREMENTAL_JUSTIFICATION_TEMPLATE),
        "incremental_justification_required_for": (
            "every position on the final card beyond the first core expression of its thesis: a "
            "promoted related alternative, a duplicate listing, or a second view's core in the same thesis"
        ),
        "games": games,
        "slate": {
            "games_with_candidates": len(games),
            "thesis_groups": sum(g["thesis_group_count"] for g in games.values()),
            "core_expressions": sum(g["core_candidate_count"] for g in games.values()),
            "related_alternatives": slate_alternatives,
            "nested_tail_extension_candidates": slate_tail,
            "games_with_multiple_thesis_groups": sorted(
                game for game, block in games.items() if block["multiple_thesis_groups"]
            ),
            "games_with_opposing_positions": sorted(
                game for game, block in games.items() if block["opposing_positions"]
            ),
            "exposure_after_sizing": _exposure_placeholders(
                "by_bet", "by_thesis", "by_game", "total_card_risk", "concentrated_games"
            ),
            "review_status": "pending_final_review",
        },
    }


def _pair(a: Expression, b: Expression, *, same_thesis: bool, opposing: bool) -> dict[str, Any]:
    full = relate(a.row, b.row, same_thesis=same_thesis, opposing=opposing)
    return {
        "relation": full["relation"],
        "both_can_cash": full["both_can_cash"],
        "both_lose_when": full["both_lose_when"],
    }


def _loses_when(row: dict[str, Any]) -> str | None:
    wins = win_set(row)
    if wins is None:
        return None
    gaps = []
    if wins.low != -_INF:
        gaps.append((-_INF, wins.low - 1))
    if wins.high != _INF:
        gaps.append((wins.high + 1, _INF))
    return describe(wins.variable, gaps)
