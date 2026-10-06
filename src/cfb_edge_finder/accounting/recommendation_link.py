"""Linking what was recommended to what was actually executed.

*** THE RULE THAT CANNOT BEND ***
A recommendation is not a wager. The ONLY source of canonical execution is the
venue's own fill evidence, delivered by the router into the wager ledger, and
nothing in this module may change a single field of it. Every function here
takes execution rows and returns a SEPARATE match record; none of them returns
a modified wager, and `tests/test_recommendation_linkage.py` asserts that the
rows it was handed come back byte-identical.

That is not an abundance of caution. A matcher that "corrected" an execution
price to the recommended one would produce a ledger where every bet was filled
exactly where the analysis said it should be, and a postmortem computed from
it would be a description of the analysis rather than of the money.

*** WHAT A MATCH IS EVIDENCE OF ***
That a recommendation existed for this market and side before this order was
placed, at a price the recommendation would have permitted. It is not evidence
that the recommendation CAUSED the bet -- the operator may have had the same
idea, or a better one -- and the vocabulary is careful about that: the states
are `recommended_and_executed`, never `followed_the_recommendation`.

*** WHY MATCHING IS DELIBERATELY STRICT ***
Four facts must agree: the exact ticker, the exact side, the recommendation
being older than the execution, and the execution price being inside a stated
tolerance of the recommended one. Loosening any of them buys attribution for
bets nobody recommended, which corrupts the one number a postmortem exists to
produce -- how the operator did relative to their own process.

An execution that matches two recommendations, or a recommendation that
matches two executions, is `ambiguous_match`. Picking the nearer one by time
would be a guess, and a guess here is a bet attributed to an analysis that did
not produce it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

#: How far an execution price may sit from the recommended entry and still be
#: called the same bet, in probability units.
#:
#: NOT a claim about execution quality. Two cents is roughly one Kalshi tick
#: either side of a quoted ask, so a fill inside it is the same order reaching
#: the book a moment later. A fill outside it is a different decision about
#: price, which is exactly what `executed_above_bet_up_to` exists to name.
DEFAULT_PRICE_TOLERANCE = 0.02

#: How long after a recommendation an execution may still be attributed to it.
#:
#: Twelve hours covers a Saturday: a morning recommendation acted on before an
#: evening kickoff is the same decision. Past that the market has moved enough
#: that the recommendation's own prices no longer describe the bet, and the
#: match would be attribution by calendar rather than by evidence.
DEFAULT_MATCH_WINDOW_SECONDS = 12 * 3600.0


class MatchState(StrEnum):
    RECOMMENDED_AND_EXECUTED = "recommended_and_executed"
    """A recommendation existed first, for this exact market and side, at a
    price this fill is inside. Evidence of coincidence, not of causation."""

    RECOMMENDED_NOT_EXECUTED = "recommended_not_executed"
    """The analysis named it and no matching order was placed. The other half
    of a process postmortem, and the half a wager ledger cannot see."""

    EXECUTED_NOT_RECOMMENDED = "executed_not_recommended"
    """Real money on a market nothing in the artifact named. Entirely the
    operator's prerogative, and worth counting separately from the rest."""

    EXECUTED_ABOVE_BET_UP_TO = "executed_above_bet_up_to"
    """Recommended, executed, and filled ABOVE the price the recommendation
    said to stop at. Still the same idea; no longer the same bet."""

    EXECUTED_DIFFERENT_SIZE = "executed_different_size"
    """Matched on market, side and price, and the stake is materially away
    from what the artifact carried. Only reachable when a stake was recorded
    on the recommendation, which is not required."""

    AMBIGUOUS_MATCH = "ambiguous_match"
    """More than one candidate on either side. Reported, never resolved."""


#: States a postmortem may treat as "this bet came out of the process". Kept as
#: a set so a state added later has to be considered rather than inherited.
ATTRIBUTED_STATES = frozenset(
    {
        MatchState.RECOMMENDED_AND_EXECUTED.value,
        MatchState.EXECUTED_ABOVE_BET_UP_TO.value,
        MatchState.EXECUTED_DIFFERENT_SIZE.value,
    }
)


def _parse(moment: Any) -> datetime | None:
    if not moment:
        return None
    try:
        parsed = datetime.fromisoformat(str(moment).replace("Z", "+00:00"))
    except ValueError:
        return None
    from datetime import UTC

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass(frozen=True)
class Recommendation:
    """One candidate, as the artifact published it.

    Deliberately a SMALL subset of the candidate record. A matcher that read
    the whole artifact would be one refactor away from a postmortem computing
    money from the analysis instead of from the ledger."""

    recommendation_id: str
    game_key: str | None
    ticker: str
    side: str
    quote_timestamp: str | None
    quoted_entry: float | None
    bet_up_to: float | None
    fair_probability: float | None
    fee_adjusted_edge: float | None
    robustness: str | None
    confidence: str | None
    data_quality_ceiling: str | None
    correlation_group: str | None
    recommended_stake: float | None = None
    packet_hash: str | None = None
    handicap_hash: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "recommendation_id": self.recommendation_id,
            "game_key": self.game_key,
            "ticker": self.ticker,
            "side": self.side,
            "quote_timestamp": self.quote_timestamp,
            "quoted_entry": self.quoted_entry,
            "bet_up_to": self.bet_up_to,
            "fair_probability": self.fair_probability,
            "fee_adjusted_edge": self.fee_adjusted_edge,
            "robustness": self.robustness,
            "confidence": self.confidence,
            "data_quality_ceiling": self.data_quality_ceiling,
            "correlation_group": self.correlation_group,
            "recommended_stake": self.recommended_stake,
            "packet_hash": self.packet_hash,
            "handicap_hash": self.handicap_hash,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Recommendation:
        return cls(
            recommendation_id=str(payload.get("recommendation_id") or ""),
            game_key=payload.get("game_key"),
            ticker=str(payload.get("ticker") or ""),
            side=str(payload.get("side") or "").upper(),
            quote_timestamp=payload.get("quote_timestamp"),
            quoted_entry=_number(payload.get("quoted_entry")),
            bet_up_to=_number(payload.get("bet_up_to")),
            fair_probability=_number(payload.get("fair_probability")),
            fee_adjusted_edge=_number(payload.get("fee_adjusted_edge")),
            robustness=payload.get("robustness"),
            confidence=payload.get("confidence"),
            data_quality_ceiling=payload.get("data_quality_ceiling"),
            correlation_group=payload.get("correlation_group"),
            recommended_stake=_number(payload.get("recommended_stake")),
            packet_hash=payload.get("packet_hash"),
            handicap_hash=payload.get("handicap_hash"),
        )


def recommendations_from_artifact(artifact: dict[str, Any]) -> list[Recommendation]:
    """Read a candidate artifact into recommendation records.

    ONE DIRECTION ONLY. Nothing writes an artifact from the ledger, and this
    reads only the fields the match needs -- so the artifact can gain, lose or
    rename anything else without a postmortem quietly changing its numbers.
    """
    out: list[Recommendation] = []
    batch = str(artifact.get("batch") or artifact.get("shard") or "")
    generated = artifact.get("generated_at")
    games = artifact.get("games") or {}
    for index, candidate in enumerate(artifact.get("candidates") or []):
        ticker = str(candidate.get("market") or "")
        if not ticker:
            continue
        game_key = candidate.get("game_key")
        game = games.get(str(game_key)) or {}
        out.append(
            Recommendation(
                # Deterministic and readable: the same artifact read twice
                # yields the same ids, so re-running a postmortem is a no-op
                # rather than a second set of recommendations.
                recommendation_id=f"{batch or 'artifact'}:{index:04d}:{ticker}:{candidate.get('side')}",
                game_key=game_key,
                ticker=ticker,
                side=str(candidate.get("side") or "").upper(),
                quote_timestamp=generated,
                quoted_entry=_number(candidate.get("kalshi_executable_price")),
                bet_up_to=_number(candidate.get("bet_up_to_price")),
                fair_probability=_number(candidate.get("fair_probability")),
                fee_adjusted_edge=_number(candidate.get("fee_adjusted_edge")),
                robustness=candidate.get("robustness"),
                confidence=game.get("handicap_confidence_effective")
                or game.get("handicap_confidence"),
                data_quality_ceiling=(
                    (game.get("factual_data_quality") or {}).get("confidence_ceiling")
                ),
                correlation_group=candidate.get("correlation_group"),
                recommended_stake=_number(candidate.get("stake_placeholder")),
            )
        )
    return out


@dataclass(frozen=True)
class Match:
    """One classification. Carries no money of its own.

    `source_bet_key` points at the wager; every figure a postmortem reports
    about that wager is read from the LEDGER ROW, never from here. This record
    says only which recommendation, if any, the execution corresponds to and
    how the two differ."""

    state: str
    source_bet_key: str | None = None
    recommendation_id: str | None = None
    ticker: str | None = None
    side: str | None = None
    executed_price: float | None = None
    recommended_price: float | None = None
    bet_up_to: float | None = None
    price_delta: float | None = None
    executed_stake: float | None = None
    recommended_stake: float | None = None
    reason: str = ""
    candidates_considered: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "source_bet_key": self.source_bet_key,
            "recommendation_id": self.recommendation_id,
            "ticker": self.ticker,
            "side": self.side,
            "executed_price": self.executed_price,
            "recommended_price": self.recommended_price,
            "bet_up_to": self.bet_up_to,
            "price_delta": self.price_delta,
            "executed_stake": self.executed_stake,
            "recommended_stake": self.recommended_stake,
            "reason": self.reason,
            "candidates_considered": self.candidates_considered,
        }


@dataclass
class MatchResult:
    matches: list[Match] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for match in self.matches:
            out[match.state] = out.get(match.state, 0) + 1
        return dict(sorted(out.items()))

    def by_key(self) -> dict[str, Match]:
        return {m.source_bet_key: m for m in self.matches if m.source_bet_key}


def match_executions(
    wagers: list[dict[str, Any]],
    recommendations: list[Recommendation],
    *,
    price_tolerance: float = DEFAULT_PRICE_TOLERANCE,
    window_seconds: float = DEFAULT_MATCH_WINDOW_SECONDS,
    stake_tolerance: float = 0.25,
) -> MatchResult:
    """Classify every execution, and every recommendation nobody acted on.

    *** THE WAGER ROWS ARE READ AND NEVER WRITTEN ***
    Not one field of `wagers` is touched. The result is a separate list of
    `Match` records keyed by `source_bet_key`, so a caller that wants both has
    to join them deliberately rather than receive a wager that has quietly
    acquired an analysis's opinion of its price.
    """
    result = MatchResult()
    used: set[str] = set()

    by_market: dict[tuple[str, str], list[Recommendation]] = {}
    for recommendation in recommendations:
        by_market.setdefault((recommendation.ticker, recommendation.side), []).append(
            recommendation
        )

    for wager in wagers:
        ticker = str(wager.get("market_ticker") or "")
        side = str(wager.get("side") or "").upper()
        executed_at = _parse(wager.get("executed_at"))
        price = _number(wager.get("execution_price"))
        stake = _number(wager.get("stake"))
        key = wager.get("source_bet_key")

        pool = by_market.get((ticker, side), [])
        eligible = []
        for recommendation in pool:
            quoted_at = _parse(recommendation.quote_timestamp)
            if quoted_at is None or executed_at is None:
                # A recommendation or an execution with no clock cannot be
                # ordered, and "which came first" is the whole question.
                continue
            gap = (executed_at - quoted_at).total_seconds()
            if gap < 0:
                # The recommendation is NEWER than the bet. An analysis cannot
                # have informed an order that predates it, and counting it
                # would attribute every bet to whatever was published later.
                continue
            if gap > window_seconds:
                continue
            eligible.append(recommendation)

        if not eligible:
            result.matches.append(
                Match(
                    state=MatchState.EXECUTED_NOT_RECOMMENDED.value,
                    source_bet_key=key,
                    ticker=ticker,
                    side=side,
                    executed_price=price,
                    executed_stake=stake,
                    reason=(
                        "no recommendation for this exact market and side existed before this "
                        "order, inside the match window"
                    ),
                    candidates_considered=len(pool),
                )
            )
            continue

        if len(eligible) > 1:
            result.matches.append(
                Match(
                    state=MatchState.AMBIGUOUS_MATCH.value,
                    source_bet_key=key,
                    ticker=ticker,
                    side=side,
                    executed_price=price,
                    executed_stake=stake,
                    reason=(
                        f"{len(eligible)} recommendations for this market and side are eligible; "
                        "choosing the nearer one by time would be a guess, and a guess here "
                        "attributes a bet to an analysis that may not have produced it"
                    ),
                    candidates_considered=len(eligible),
                )
            )
            # Every candidate stays available: an ambiguity is not a
            # consumption, and marking them used would turn one unresolvable
            # case into several `recommended_not_executed` ones.
            continue

        recommendation = eligible[0]
        used.add(recommendation.recommendation_id)
        delta = (
            None
            if price is None or recommendation.quoted_entry is None
            else round(price - recommendation.quoted_entry, 6)
        )

        state = MatchState.RECOMMENDED_AND_EXECUTED.value
        reason = "the market, side, ordering and price all agree"
        if (
            recommendation.bet_up_to is not None
            and price is not None
            and price > recommendation.bet_up_to + 1e-9
        ):
            state = MatchState.EXECUTED_ABOVE_BET_UP_TO.value
            reason = (
                f"filled at {price:.4f}, above the {recommendation.bet_up_to:.4f} the "
                "recommendation said to stop at. Still the same idea; no longer the same bet"
            )
        elif delta is not None and abs(delta) > price_tolerance:
            state = MatchState.EXECUTED_ABOVE_BET_UP_TO.value
            reason = (
                f"filled {delta:+.4f} from the quoted entry, outside the "
                f"{price_tolerance:.4f} tolerance"
            )
        elif (
            recommendation.recommended_stake is not None
            and stake is not None
            and recommendation.recommended_stake > 0
            and abs(stake - recommendation.recommended_stake)
            / recommendation.recommended_stake
            > stake_tolerance
        ):
            state = MatchState.EXECUTED_DIFFERENT_SIZE.value
            reason = (
                f"staked {stake:.2f} against a recommended {recommendation.recommended_stake:.2f}"
            )

        result.matches.append(
            Match(
                state=state,
                source_bet_key=key,
                recommendation_id=recommendation.recommendation_id,
                ticker=ticker,
                side=side,
                executed_price=price,
                recommended_price=recommendation.quoted_entry,
                bet_up_to=recommendation.bet_up_to,
                price_delta=delta,
                executed_stake=stake,
                recommended_stake=recommendation.recommended_stake,
                reason=reason,
                candidates_considered=len(eligible),
            )
        )

    for recommendation in recommendations:
        if recommendation.recommendation_id in used:
            continue
        result.matches.append(
            Match(
                state=MatchState.RECOMMENDED_NOT_EXECUTED.value,
                recommendation_id=recommendation.recommendation_id,
                ticker=recommendation.ticker,
                side=recommendation.side,
                recommended_price=recommendation.quoted_entry,
                bet_up_to=recommendation.bet_up_to,
                recommended_stake=recommendation.recommended_stake,
                reason="no execution matched this recommendation",
            )
        )

    return result
