"""Reducing many positive-EV rows to the few worth acting on -- after
every one of them has already been evaluated.

*** REDUCTION IS NOT FILTERING ***
Nothing here removes a contract from consideration before it is priced. The
input to this module is the finished evaluation ledger: every eligible
contract in the shard has already been priced or explicitly marked
unpriceable, and the invariant has already closed. What this module does is
notice that twenty of the survivors are the same opinion twenty times, and say
which single expression of it is the best buy.

*** AND EVERY REMOVAL IS NAMED ***
There is no `[:N]` anywhere in this file and no notion of "top". A candidate
is removed only by LOSING TO A NAMED SURVIVOR for a deterministic reason, and
the pair is written into the reduction ledger. "Twelve candidates were
dropped" that cannot say which twelve, to whom, and why, is not an audit
trail.

*** WHY DIFFERENT TICKERS ARE NOT DIVERSIFICATION ***
    LSU -2.5   and   LSU -6.5
are two prices on one opinion. So are YES on "home by more than 6.5" and NO on
"away by more than 5.5" -- the same event, quoted from opposite ends. A
shortlist that shows those as three independent ideas invites three stakes on
one game, and the operator's exposure is then triple what the artifact implies.

Three nested groupings make that visible:

  EXPRESSION KEY   (game, period, driver, direction, line, effective side)
                   Two rows with this key are the SAME CONTRACT stated twice.
  VIEW KEY         (game, period, driver, direction)
                   One ladder, one direction: alternate rungs of one opinion.
  THESIS KEY       (game, direction-on-the-game)
                   Every market in the game that wins if the same side covers.

Only the best member of a VIEW survives. Members of a THESIS all survive --
they are genuinely different bets -- but they are grouped, counted, and their
aggregate exposure is reported, because the operator sizing them needs to see
that they move together.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from cfb_edge_finder.execution.evaluator import CANDIDATE_STATUSES
from cfb_edge_finder.execution.sensitivity import Robustness

#: Robustness ranked for tie-breaking. Higher is better, and it beats a
#: marginally larger edge: an extra 0.001 of base edge is not worth a bet that
#: stops working when the margin estimate moves a point.
_ROBUSTNESS_RANK = {
    Robustness.ROBUST_POSITIVE_EV.value: 2,
    Robustness.SENSITIVE_POSITIVE_EV.value: 1,
}

#: Two base edges within this of each other are treated as equal, and the
#: tie is broken on robustness and then on worst-case edge. Without it the
#: sixth decimal place of a normal CDF decides which of two bets an operator
#: sees, which is not a decision anybody made.
EDGE_TIE_BAND = 0.0025


class ReductionReason(StrEnum):
    EXACT_EQUIVALENT = "exact_equivalent"
    """Identical contract, identical side, identical price: the same row twice."""

    DOMINATED_DUPLICATE = "dominated_duplicate"
    """Same view, strictly smaller fee-adjusted edge."""

    FEE_DOMINATED = "fee_dominated"
    """Same view, edge within the tie band, and a strictly larger entry fee."""

    WORSE_ROBUSTNESS = "worse_robustness"
    """Same view, edge within the tie band, and a weaker robustness verdict."""

    INFERIOR_EXPRESSION = "inferior_expression"
    """Same view, edge within the tie band, same robustness, and a worse
    worst-case edge. The last deterministic tie-break before the ticker."""

    CORRELATION_GROUP_ALTERNATIVE = "correlation_group_alternative"
    """Same view, a different rung of the same ladder, and no other reason
    separates them. Kept as a named alternative on the survivor, not deleted."""


@dataclass(frozen=True)
class Expression:
    """One priced row, with the three keys that decide what it duplicates."""

    row: dict[str, Any]
    driver: str
    direction: str
    view_key: tuple[str, str, str]
    expression_key: tuple[str, str, str, Any, str]
    thesis_key: tuple[str, str]

    @property
    def ticker(self) -> str:
        return str(self.row.get("ticker"))

    @property
    def net_edge(self) -> float:
        return float(self.row.get("net_edge") or 0.0)

    @property
    def fee(self) -> float:
        return float(self.row.get("fee") or 0.0)

    @property
    def robustness(self) -> str:
        return str((self.row.get("sensitivity") or {}).get("robustness") or self.row.get("status"))

    @property
    def robustness_rank(self) -> int:
        return _ROBUSTNESS_RANK.get(self.robustness, 0)

    @property
    def worst_net_edge(self) -> float:
        sensitivity = self.row.get("sensitivity") or {}
        value = sensitivity.get("net_edge_low")
        return float(value) if value is not None else self.net_edge


def driver_and_direction(row: dict[str, Any]) -> tuple[str, str]:
    """Which underlying quantity the bet is really a view on, and which way.

    Two contracts that are the same view in different clothes -- YES on
    "home wins by over 6.5" and NO on "away wins by over 5.5" -- must not both
    appear in a shortlist as if they were independent ideas. Reducing every
    contract to (driver, direction) is what makes that visible."""
    kind = str(row.get("kind") or "")
    period = str(row.get("period") or "")
    team = str(row.get("team") or "none")
    side = str(row.get("best_side") or "yes")
    yes_side = side == "yes"

    if kind in ("moneyline", "spread", "margin_band"):
        home_view = (team == "home") == yes_side
        return f"{period}:margin", "home" if home_view else "away"
    if kind == "total":
        return f"{period}:total", "over" if yes_side else "under"
    if kind == "team_total":
        return f"{period}:team_total:{team}", "over" if yes_side else "under"
    return f"contract:{row.get('ticker')}", side


def thesis_direction(row: dict[str, Any], driver: str, direction: str) -> str:
    """The GAME-level opinion a row expresses, if it expresses one.

    A spread, a moneyline, a margin band and a team total on the favourite all
    win together when the favourite is better than the market thinks. A game
    total does not -- it is a separate opinion about the same game -- so it
    gets its own thesis rather than being folded into the side one.
    """
    if driver.endswith(":margin"):
        return f"side:{direction}"
    if driver.endswith(":total"):
        return f"total:{direction}"
    if ":team_total:" in driver:
        team = driver.rsplit(":", 1)[-1]
        return f"team_scoring:{team}:{direction}"
    return f"standalone:{row.get('ticker')}"


def build_expression(row: dict[str, Any]) -> Expression:
    driver, direction = driver_and_direction(row)
    game = str(row.get("game_key"))
    return Expression(
        row=row,
        driver=driver,
        direction=direction,
        view_key=(game, driver, direction),
        expression_key=(
            game,
            driver,
            direction,
            row.get("line"),
            str(row.get("best_side") or "yes"),
        ),
        thesis_key=(game, thesis_direction(row, driver, direction)),
    )


def _beats(challenger: Expression, incumbent: Expression) -> tuple[bool, str]:
    """(does the challenger replace the incumbent, why the loser lost).

    Every branch returns a REASON FOR THE LOSER, because that is what the
    reduction ledger records. The comparison is a total order on
    (edge band, robustness, worst-case edge, ticker), so the outcome does not
    depend on the order rows arrived in -- which is what makes a re-run of the
    same ledger produce the same shortlist.
    """
    delta = challenger.net_edge - incumbent.net_edge
    if delta > EDGE_TIE_BAND:
        return True, ReductionReason.DOMINATED_DUPLICATE.value
    if delta < -EDGE_TIE_BAND:
        return False, ReductionReason.DOMINATED_DUPLICATE.value

    # Inside the tie band. Fees first: two prices on one opinion where one
    # costs more to enter is the clearest possible dominance.
    if challenger.fee < incumbent.fee - 1e-9:
        return True, ReductionReason.FEE_DOMINATED.value
    if challenger.fee > incumbent.fee + 1e-9:
        return False, ReductionReason.FEE_DOMINATED.value

    if challenger.robustness_rank > incumbent.robustness_rank:
        return True, ReductionReason.WORSE_ROBUSTNESS.value
    if challenger.robustness_rank < incumbent.robustness_rank:
        return False, ReductionReason.WORSE_ROBUSTNESS.value

    if challenger.worst_net_edge > incumbent.worst_net_edge + 1e-9:
        return True, ReductionReason.INFERIOR_EXPRESSION.value
    if challenger.worst_net_edge < incumbent.worst_net_edge - 1e-9:
        return False, ReductionReason.INFERIOR_EXPRESSION.value

    # Nothing economic separates them. The ticker decides, so the result is
    # stable, and the reason says honestly that the two were alternatives
    # rather than one being worse.
    if challenger.ticker < incumbent.ticker:
        return True, ReductionReason.CORRELATION_GROUP_ALTERNATIVE.value
    return False, ReductionReason.CORRELATION_GROUP_ALTERNATIVE.value


@dataclass
class Reduction:
    survivors: list[Expression] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    alternatives: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    """survivor ticker -> the rows it beat, so the artifact can show the
    operator the rest of the ladder without re-listing it as a bet."""

    @property
    def counts(self) -> dict[str, int]:
        by_reason: dict[str, int] = {}
        for entry in self.removed:
            by_reason[entry["reason"]] = by_reason.get(entry["reason"], 0) + 1
        return dict(sorted(by_reason.items()))


def reduce_candidates(rows: list[dict[str, Any]]) -> Reduction:
    """Keep the best expression of each distinct view; account for the rest.

    `rows` must already be terminal candidate rows -- this does not re-filter
    on status, because deciding what counts as a candidate is the evaluator's
    job and doing it twice is how the two definitions drift apart.
    """
    expressions = [build_expression(row) for row in rows]

    # 1. Exact duplicates first, by expression key. Two rows that are the same
    #    contract on the same side are not an interesting economic comparison;
    #    they are a bug or a re-listing, and folding them here keeps them out
    #    of the ladder-level reasons below.
    by_expression: dict[tuple, Expression] = {}
    reduction = Reduction()
    for expression in expressions:
        incumbent = by_expression.get(expression.expression_key)
        if incumbent is None:
            by_expression[expression.expression_key] = expression
            continue
        winner, loser = (
            (expression, incumbent)
            if _beats(expression, incumbent)[0]
            else (incumbent, expression)
        )
        by_expression[expression.expression_key] = winner
        reduction.removed.append(
            _removal(loser, winner, ReductionReason.EXACT_EQUIVALENT.value)
        )

    # 2. Then one survivor per VIEW.
    by_view: dict[tuple[str, str, str], Expression] = {}
    losses: list[tuple[Expression, Expression, str]] = []
    for expression in by_expression.values():
        incumbent = by_view.get(expression.view_key)
        if incumbent is None:
            by_view[expression.view_key] = expression
            continue
        replaces, reason = _beats(expression, incumbent)
        if replaces:
            by_view[expression.view_key] = expression
            losses.append((incumbent, expression, reason))
        else:
            losses.append((expression, incumbent, reason))

    # The survivor recorded against a loss may itself have been beaten later,
    # so every loss is re-pointed at the view's FINAL survivor. Otherwise the
    # ledger would say a row lost to a contract that is not in the artifact.
    for loser, _provisional, reason in losses:
        winner = by_view[loser.view_key]
        reduction.removed.append(_removal(loser, winner, reason))
        reduction.alternatives.setdefault(winner.ticker, []).append(
            {
                "ticker": loser.ticker,
                "line": loser.row.get("line"),
                "side": loser.row.get("best_side"),
                "executable_price": loser.row.get("executable_entry"),
                "fee_adjusted_edge": loser.row.get("net_edge"),
                "robustness": loser.robustness,
                "removed_because": reason,
            }
        )

    reduction.survivors = sorted(
        by_view.values(),
        key=lambda e: (-e.robustness_rank, -e.net_edge, e.ticker),
    )
    for ticker in reduction.alternatives:
        reduction.alternatives[ticker].sort(key=lambda entry: str(entry["ticker"]))
    return reduction


def _removal(loser: Expression, winner: Expression, reason: str) -> dict[str, Any]:
    return {
        "ticker": loser.ticker,
        "game_key": loser.row.get("game_key"),
        "status": loser.row.get("status"),
        "net_edge": loser.row.get("net_edge"),
        "robustness": loser.robustness,
        "reason": reason,
        "lost_to": winner.ticker,
        "lost_to_net_edge": winner.row.get("net_edge"),
        "correlation_group": f"{loser.driver} / {loser.direction}",
        "explanation": _EXPLANATIONS[reason].format(
            winner=winner.ticker,
            group=f"{loser.driver} / {loser.direction}",
        ),
    }


_EXPLANATIONS = {
    ReductionReason.EXACT_EQUIVALENT.value: (
        "the same contract and side appeared twice; {winner} is the copy that was kept"
    ),
    ReductionReason.DOMINATED_DUPLICATE.value: (
        "{winner} expresses the same view ({group}) at a materially better fee-adjusted edge"
    ),
    ReductionReason.FEE_DOMINATED.value: (
        "{winner} expresses the same view ({group}) at the same edge for a smaller entry fee"
    ),
    ReductionReason.WORSE_ROBUSTNESS.value: (
        "{winner} expresses the same view ({group}) at a comparable edge and survives more of the "
        "handicap's stated uncertainty region"
    ),
    ReductionReason.INFERIOR_EXPRESSION.value: (
        "{winner} expresses the same view ({group}) at a comparable edge with a better worst-case "
        "edge across the uncertainty region"
    ),
    ReductionReason.CORRELATION_GROUP_ALTERNATIVE.value: (
        "another rung of the same ladder ({group}); nothing economic separated it from {winner}, "
        "which was kept by ticker order and carries this one as a named alternative"
    ),
}


# ---------------------------------------------------------------- exposure


@dataclass(frozen=True)
class ExposureGroup:
    """Contracts that win or lose together, counted so sizing can see them."""

    key: str
    scope: str
    members: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "group": self.key,
            "scope": self.scope,
            "contracts": list(self.members),
            "contract_count": len(self.members),
        }


def exposure_groups(survivors: list[Expression]) -> dict[str, Any]:
    """Game-level and thesis-level groupings of the surviving candidates.

    *** THIS IS NOT A STAKE ***
    Nothing here recommends an amount. It reports which candidates are the same
    bet wearing different tickers, so that an operator reading the artifact
    cannot mistake four expressions of one thesis for a diversified four-bet
    slate. The sizing decision, and the bankroll, stay entirely outside this
    repository.
    """
    by_game: dict[str, list[str]] = {}
    by_thesis: dict[tuple[str, str], list[str]] = {}
    for expression in survivors:
        by_game.setdefault(str(expression.row.get("game_key")), []).append(expression.ticker)
        by_thesis.setdefault(expression.thesis_key, []).append(expression.ticker)

    games = [
        ExposureGroup(key=game, scope="game", members=tuple(sorted(tickers)))
        for game, tickers in sorted(by_game.items())
    ]
    theses = [
        ExposureGroup(
            key=f"{game}:{thesis}", scope="thesis", members=tuple(sorted(tickers))
        )
        for (game, thesis), tickers in sorted(by_thesis.items())
    ]
    return {
        "note": (
            "Contracts inside one group are correlated expressions of one opinion, not independent "
            "bets. Two different tickers are not diversification. This repository sizes nothing; "
            "these groups exist so the operator's own sizing can see the correlation."
        ),
        "by_game": [g.as_dict() for g in games],
        "by_thesis": [t.as_dict() for t in theses],
        "largest_game_group": max((len(g.members) for g in games), default=0),
        "largest_thesis_group": max((len(t.members) for t in theses), default=0),
    }


def candidate_statuses() -> frozenset[str]:
    """Exported so a test can assert the reducer and the evaluator agree."""
    return CANDIDATE_STATUSES
