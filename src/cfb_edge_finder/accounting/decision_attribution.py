"""Attributing a filed wager to the decision record that preceded it.

*** WHAT THIS READS, AND WHAT IT NEVER WRITES ***
It reads the PRIVATE decision store (`cfb_edge_finder.decisions`) and the wager
rows, and returns a separate attribution record per wager. Not one field of a
wager row is changed, and nothing here can write a ledger row: this module is
one of the ATTRIBUTION_MODULES `tests/test_accounting_isolation.py` holds to
that rule.

*** THE IDENTITY ORDER ***
1. There is no explicit order-to-decision linkage: the operator places the
   order by hand and the router records it from the venue. So the strongest
   identity available is
2. the exact market ticker and side, the game the ticker names, and a decision
   record created BEFORE the order executed, within the match window; and when
   more than one run qualifies,
3. the run whose `created_at` is nearest before the placement. A later run of
   the same batch supersedes an earlier one in the operator's hands; the
   nearest earlier record is the one that was on the screen.

*** WHAT IS AMBIGUOUS, AND STAYS SO ***
Two runs created at the SAME instant that both carry the candidate, or one
run that carries the same ticker and side twice: there is no nearest, and the
wager is reported `ambiguous` with both record ids rather than assigned.

*** WHAT IS REJECTED ***
A candidate whose record was created AFTER the game's kickoff. A decision made
after the game began is not the pre-game decision the postmortem is asking
about, whatever the order's own timestamp says; it is named as rejected with
the reason, not silently skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cfb_edge_finder.accounting.recommendation_link import (
    DEFAULT_MATCH_WINDOW_SECONDS,
    DEFAULT_PRICE_TOLERANCE,
    Match,
    MatchResult,
    MatchState,
    Recommendation,
)

MATCHED = "matched"
UNMATCHED = "unmatched"
AMBIGUOUS = "ambiguous"


def _parse(moment: Any) -> datetime | None:
    if not moment:
        return None
    try:
        parsed = datetime.fromisoformat(str(moment).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _game_key_of(ticker: str | None) -> str | None:
    parts = str(ticker or "").split("-")
    return parts[1] if len(parts) >= 3 else None


@dataclass(frozen=True)
class DecisionCandidate:
    """One selected candidate from one decision record, with the record around it."""

    record_id: str
    record_created_at: str
    record_path: str | None
    slate_date: str | None
    batch: str | None
    kickoff_window: str | None
    candidate: dict[str, Any]
    game: dict[str, Any]
    siblings: tuple[dict[str, Any], ...]
    """Every other candidate the same record carried for the same game, and the
    evaluated-but-not-selected rows for that game: the alternatives that existed
    when this one was chosen."""

    @property
    def ticker(self) -> str:
        return str(self.candidate.get("market_ticker") or "")

    @property
    def side(self) -> str:
        return str(self.candidate.get("side") or "").upper()

    @property
    def recommendation_id(self) -> str:
        return f"{self.record_id[:12]}:{self.candidate.get('recommendation_id') or self.ticker + ':' + self.side}"

    def as_recommendation(self) -> Recommendation:
        """The subset the existing tier cuts read, in their vocabulary."""
        c = self.candidate
        return Recommendation(
            recommendation_id=self.recommendation_id,
            game_key=c.get("game_key"),
            ticker=self.ticker,
            side=self.side,
            quote_timestamp=self.record_created_at,
            quoted_entry=_number(c.get("observed_price")),
            bet_up_to=_number(c.get("bet_up_to")),
            fair_probability=_number(c.get("fair_probability")),
            fee_adjusted_edge=_number(c.get("edge")),
            robustness=c.get("tier"),
            confidence=c.get("confidence") or self.game.get("effective_confidence"),
            data_quality_ceiling=self.game.get("data_quality_ceiling"),
            correlation_group=c.get("correlation_group"),
            recommended_stake=_number(c.get("recommended_stake")),
            packet_hash=self.game.get("packet_hash"),
        )


def candidates_from_records(records: list[dict[str, Any]]) -> list[DecisionCandidate]:
    """Every selected candidate in every record, each carrying its record."""
    out: list[DecisionCandidate] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        games = record.get("games") or {}
        selected = [c for c in record.get("candidates") or [] if isinstance(c, dict)]
        not_selected = [
            e for e in record.get("evaluated_not_selected") or [] if isinstance(e, dict)
        ]
        for candidate in selected:
            game_key = str(candidate.get("game_key") or _game_key_of(candidate.get("market_ticker")) or "")
            siblings = tuple(
                {**other, "selected": True}
                for other in selected
                if other is not candidate and str(other.get("game_key")) == game_key
            ) + tuple(
                {**other, "selected": False}
                for other in not_selected
                if str(other.get("game_key")) == game_key
            )
            out.append(
                DecisionCandidate(
                    record_id=str(record.get("record_id") or ""),
                    record_created_at=str(record.get("created_at") or ""),
                    record_path=record.get("_path"),
                    slate_date=record.get("slate_date"),
                    batch=record.get("batch"),
                    kickoff_window=record.get("kickoff_window"),
                    candidate=dict(candidate),
                    game=dict(games.get(game_key) or {}),
                    siblings=siblings,
                )
            )
    return out


@dataclass
class DecisionAttribution:
    """Per-wager attribution plus the MatchResult the tier cuts consume."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    matches: MatchResult = field(default_factory=MatchResult)
    records_read: int = 0
    candidates_read: int = 0
    rejected_post_kickoff: int = 0

    @property
    def counts(self) -> dict[str, int]:
        out = {MATCHED: 0, UNMATCHED: 0, AMBIGUOUS: 0}
        for entry in self.entries:
            out[entry["state"]] = out.get(entry["state"], 0) + 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity_rule": (
                "exact market ticker and side, same game, decision record created before the "
                "order inside the match window; among several runs the nearest earlier record; "
                "a tie or a duplicate within one record is ambiguous, never guessed"
            ),
            "records_read": self.records_read,
            "candidates_read": self.candidates_read,
            "rejected_post_kickoff_candidates": self.rejected_post_kickoff,
            "counts": self.counts,
            "wagers": self.entries,
        }


def _entry(wager: dict[str, Any], state: str, **extra: Any) -> dict[str, Any]:
    return {
        "state": state,
        "source_bet_key": wager.get("source_bet_key"),
        "market_ticker": wager.get("market_ticker"),
        "side": str(wager.get("side") or "").upper(),
        "game_date": wager.get("game_date"),
        "executed_at": wager.get("executed_at"),
        "execution_price": _number(wager.get("execution_price")),
        "actual_stake": _number(wager.get("stake")),
        **extra,
    }


def attribute(
    wagers: list[dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    window_seconds: float = DEFAULT_MATCH_WINDOW_SECONDS,
    price_tolerance: float = DEFAULT_PRICE_TOLERANCE,
) -> DecisionAttribution:
    """Every wager, attributed to at most one decision candidate, or not.

    The wager rows are read and never written."""
    result = DecisionAttribution(records_read=len(records))
    candidates = candidates_from_records(records)
    result.candidates_read = len(candidates)

    by_market: dict[tuple[str, str], list[DecisionCandidate]] = {}
    for candidate in candidates:
        by_market.setdefault((candidate.ticker, candidate.side), []).append(candidate)

    used: set[str] = set()
    for wager in wagers:
        ticker = str(wager.get("market_ticker") or "")
        side = str(wager.get("side") or "").upper()
        executed_at = _parse(wager.get("executed_at"))
        price = _number(wager.get("execution_price"))
        stake = _number(wager.get("stake"))
        key = wager.get("source_bet_key")
        pool = by_market.get((ticker, side), [])

        eligible: list[DecisionCandidate] = []
        rejected: list[str] = []
        for candidate in pool:
            created = _parse(candidate.record_created_at)
            if created is None or executed_at is None:
                rejected.append(f"{candidate.record_id[:12]}: no usable clock")
                continue
            game_key = _game_key_of(ticker)
            if game_key and candidate.candidate.get("game_key") and candidate.candidate["game_key"] != game_key:
                rejected.append(f"{candidate.record_id[:12]}: names a different game")
                continue
            kickoff = _parse(candidate.game.get("kickoff") or candidate.candidate.get("kickoff"))
            if kickoff is not None and created > kickoff:
                result.rejected_post_kickoff += 1
                rejected.append(f"{candidate.record_id[:12]}: record created after kickoff")
                continue
            gap = (executed_at - created).total_seconds()
            if gap < 0:
                rejected.append(f"{candidate.record_id[:12]}: record newer than the order")
                continue
            if gap > window_seconds:
                rejected.append(f"{candidate.record_id[:12]}: outside the match window")
                continue
            eligible.append(candidate)

        if not eligible:
            result.entries.append(
                _entry(
                    wager, UNMATCHED,
                    reason=(
                        "no decision record carries this market and side before the order"
                        if not pool else "; ".join(rejected) or "no eligible decision record"
                    ),
                    candidates_considered=len(pool),
                )
            )
            result.matches.matches.append(
                Match(
                    state=MatchState.EXECUTED_NOT_RECOMMENDED.value,
                    source_bet_key=key, ticker=ticker, side=side,
                    executed_price=price, executed_stake=stake,
                    reason="no decision record for this market and side preceded the order",
                    candidates_considered=len(pool),
                )
            )
            continue

        # Nearest earlier RUN. Two candidates from one record, or two records
        # created at the same instant, have no nearest and stay ambiguous.
        by_record: dict[str, list[DecisionCandidate]] = {}
        for candidate in eligible:
            by_record.setdefault(candidate.record_id, []).append(candidate)
        latest_created = max(c.record_created_at for c in eligible)
        nearest = [c for c in eligible if c.record_created_at == latest_created]
        nearest_records = {c.record_id for c in nearest}
        if len(nearest) > 1 or any(len(v) > 1 for v in by_record.values() if v[0].record_id in nearest_records):
            result.entries.append(
                _entry(
                    wager, AMBIGUOUS,
                    reason=(
                        f"{len(nearest)} decision candidates for this market and side are equally "
                        "near before the order; choosing one would be a guess"
                    ),
                    record_ids=sorted(nearest_records),
                    candidates_considered=len(eligible),
                )
            )
            result.matches.matches.append(
                Match(
                    state=MatchState.AMBIGUOUS_MATCH.value,
                    source_bet_key=key, ticker=ticker, side=side,
                    executed_price=price, executed_stake=stake,
                    reason="more than one decision candidate is equally near before the order",
                    candidates_considered=len(eligible),
                )
            )
            continue

        chosen = nearest[0]
        used.add(chosen.recommendation_id)
        recommendation = chosen.as_recommendation()
        observed = recommendation.quoted_entry
        bet_up_to = recommendation.bet_up_to
        delta = None if price is None or observed is None else round(price - observed, 6)
        inside = None if bet_up_to is None or price is None else bool(price <= bet_up_to + 1e-9)

        state = MatchState.RECOMMENDED_AND_EXECUTED.value
        reason = "market, side, ordering and price agree"
        if inside is False:
            state = MatchState.EXECUTED_ABOVE_BET_UP_TO.value
            reason = f"filled at {price:.4f}, above the bet-up-to {bet_up_to:.4f}"
        elif delta is not None and abs(delta) > price_tolerance:
            state = MatchState.EXECUTED_ABOVE_BET_UP_TO.value
            reason = f"filled {delta:+.4f} from the observed decision-time price, outside tolerance"
        elif (
            recommendation.recommended_stake is not None
            and stake is not None
            and recommendation.recommended_stake > 0
            and abs(stake - recommendation.recommended_stake) / recommendation.recommended_stake > 0.25
        ):
            state = MatchState.EXECUTED_DIFFERENT_SIZE.value
            reason = f"staked {stake:.2f} against a recorded {recommendation.recommended_stake:.2f}"

        result.matches.matches.append(
            Match(
                state=state, source_bet_key=key, recommendation_id=chosen.recommendation_id,
                ticker=ticker, side=side, executed_price=price, recommended_price=observed,
                bet_up_to=bet_up_to, price_delta=delta, executed_stake=stake,
                recommended_stake=recommendation.recommended_stake, reason=reason,
                candidates_considered=len(eligible),
            )
        )
        result.entries.append(
            _entry(
                wager, MATCHED,
                record_id=chosen.record_id,
                record_created_at=chosen.record_created_at,
                record_path=chosen.record_path,
                slate_date=chosen.slate_date,
                batch=chosen.batch,
                kickoff_window=chosen.kickoff_window,
                recommendation_id=chosen.candidate.get("recommendation_id"),
                game=chosen.game.get("game"),
                kickoff=chosen.game.get("kickoff"),
                thesis=chosen.game.get("thesis"),
                opposing_case=chosen.game.get("opposing_case"),
                confidence=recommendation.confidence,
                tier=recommendation.robustness,
                data_quality_ceiling=recommendation.data_quality_ceiling,
                observed_price=observed,
                observed_at=chosen.candidate.get("observed_at"),
                bet_up_to=bet_up_to,
                price_delta=delta,
                inside_bet_up_to=inside,
                fair_probability=recommendation.fair_probability,
                edge=recommendation.fee_adjusted_edge,
                recommended_stake=recommendation.recommended_stake,
                match_state=state,
                match_reason=reason,
                runs_considered=len(by_record),
                alternatives=[
                    {
                        "market_ticker": s.get("market_ticker"),
                        "side": s.get("side"),
                        "edge": _number(s.get("edge")),
                        "observed_price": _number(s.get("observed_price")),
                        "bet_up_to": _number(s.get("bet_up_to")),
                        "selected": bool(s.get("selected")),
                        "removal_reason": s.get("removal_reason"),
                    }
                    for s in chosen.siblings
                ],
            )
        )

    for candidate in candidates:
        if candidate.recommendation_id in used:
            continue
        rec = candidate.as_recommendation()
        result.matches.matches.append(
            Match(
                state=MatchState.RECOMMENDED_NOT_EXECUTED.value,
                recommendation_id=candidate.recommendation_id, ticker=candidate.ticker,
                side=candidate.side, recommended_price=rec.quoted_entry, bet_up_to=rec.bet_up_to,
                recommended_stake=rec.recommended_stake,
                reason="no execution matched this decision candidate",
            )
        )
    return result


def render(attribution: DecisionAttribution) -> list[str]:
    """Lines for the local postmortem. Prints prices and stakes: LOCAL ONLY."""
    counts = attribution.counts
    lines = [
        "",
        "  decision-record attribution (private store):",
        f"    records read:              {attribution.records_read}",
        f"    candidates read:           {attribution.candidates_read}",
        f"    matched / unmatched / ambiguous: {counts[MATCHED]} / {counts[UNMATCHED]} / {counts[AMBIGUOUS]}",
        f"    post-kickoff candidates rejected: {attribution.rejected_post_kickoff}",
    ]
    for entry in attribution.entries:
        if entry["state"] != MATCHED:
            lines.append(
                f"    {entry['state']:9} {entry['market_ticker']} {entry['side']} -- {entry.get('reason')}"
            )
            continue
        inside = entry.get("inside_bet_up_to")
        inside_word = "n/a" if inside is None else ("inside" if inside else "ABOVE")
        lines.append(
            f"    matched   {entry['market_ticker']} {entry['side']} @{entry['execution_price']} "
            f"(decision {entry.get('observed_price')}, bet-up-to {entry.get('bet_up_to')}, {inside_word}) "
            f"tier={entry.get('tier')} confidence={entry.get('confidence')} "
            f"stake={entry.get('actual_stake')} run={str(entry.get('record_id'))[:12]} "
            f"alternatives={len(entry.get('alternatives') or [])}"
        )
        if entry.get("thesis"):
            lines.append(f"              thesis: {str(entry['thesis'])[:160]}")
    return lines
