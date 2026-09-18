"""The RUN CFB consumer contract.

*** THE RULE THIS MODULE EXISTS TO ENFORCE ***

    A GAME MAY NOT BE USED FOR HANDICAPPING OR BET SELECTION UNLESS
    game.completeness.native_game_markets_complete == true

The catalog deliberately publishes an INCOMPLETE capture rather than
suppressing every game it *did* capture successfully. That is the right
trade -- a handicapper is better served by "here are 238 of 239 games, and
here is the one that failed" than by silence. It is only safe if the
consumer cannot accidentally treat the failed one as complete, and a
convention in a document is not a mechanism. This is the mechanism.

*** WHY GAME-LEVEL COMPLETENESS IS AUTHORITATIVE, NOT SLATE-LEVEL ***
`capture_complete` is a slate-wide AND: it is false if ANY game failed.
Gating on it would throw away 238 perfectly complete game menus because
one unrelated game's event fetch got a 429. So:

  capture_complete == true   -> the whole captured slate passed
  capture_complete == false  -> does NOT invalidate games whose own
                                native_game_markets_complete is true

Each game's own completeness is the authority for whether THAT game may be
analyzed. The slate flag is a health signal, not a gate.

*** WHY `unknown` DOES NOT BLOCK A GAME ***
An unclassified contract is a LABELLING gap, not a DISCOVERY gap -- the
contract was captured in full, with its rules and prices, and a human or
an external handicapper can read it perfectly well. Blocking on it would
punish the catalog for being honest about what it could not name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class GameUsability(StrEnum):
    USABLE = "usable"
    """Market discovery for this game completed. Its menu may be used for
    handicapping and bet selection."""

    UNAVAILABLE_INCOMPLETE_DISCOVERY = "unavailable_incomplete_discovery"
    """Market discovery for this game did NOT complete. Its menu is
    partial, and a partial menu cannot be distinguished from a thin one by
    looking at it. Do not select bets from this game."""

    UNAVAILABLE_NOT_IN_CATALOG = "unavailable_not_in_catalog"
    """No such game in this capture. Not the same as "no markets": the
    catalog simply does not describe it."""


@dataclass(frozen=True)
class GameUsabilityVerdict:
    """Why a game may or may not be handicapped from this catalog."""

    game_key: str
    usability: GameUsability
    reason: str
    market_count: int = 0
    events_reported: int = 0
    events_fetched: int = 0
    failed_event_tickers: tuple[str, ...] = ()
    pagination_failed_event_tickers: tuple[str, ...] = ()
    api_failures: int = 0
    markets_file: str | None = None
    title: str | None = None

    @property
    def may_handicap(self) -> bool:
        """The single question a consumer must ask before analyzing."""
        return self.usability is GameUsability.USABLE

    def explain(self) -> str:
        """A line fit to show a human or paste into a session, naming the
        diagnostics rather than just refusing."""
        if self.may_handicap:
            return (
                f"{self.game_key} ({self.title or 'unknown matchup'}): USABLE -- "
                f"{self.market_count} contracts, {self.events_fetched}/{self.events_reported} events captured."
            )
        if self.usability is GameUsability.UNAVAILABLE_NOT_IN_CATALOG:
            return f"{self.game_key}: UNAVAILABLE -- not present in this capture."
        detail = []
        if self.failed_event_tickers:
            detail.append(f"events that could not be fetched: {', '.join(self.failed_event_tickers)}")
        if self.pagination_failed_event_tickers:
            detail.append(
                f"events whose market list was truncated: {', '.join(self.pagination_failed_event_tickers)}"
            )
        if self.events_fetched < self.events_reported:
            detail.append(f"only {self.events_fetched} of {self.events_reported} events captured")
        if self.api_failures:
            detail.append(f"{self.api_failures} API failure(s)")
        return (
            f"{self.game_key} ({self.title or 'unknown matchup'}): UNAVAILABLE -- market discovery "
            f"incomplete. {'; '.join(detail) or 'no diagnostics recorded'}. "
            f"The {self.market_count} contract(s) captured are a PARTIAL menu and must not be used "
            f"for bet selection."
        )


def _completeness(entry: dict[str, Any]) -> dict[str, Any]:
    return entry.get("completeness") or {}


def assess_game(entry: dict[str, Any] | None, game_key: str | None = None) -> GameUsabilityVerdict:
    """Decide whether one catalog game entry may be handicapped.

    Accepts either an index entry or a per-game detail document -- both
    carry the same `completeness` block, so a consumer that has only opened
    the detail file gets the same answer."""
    if entry is None:
        return GameUsabilityVerdict(
            game_key=game_key or "<unknown>",
            usability=GameUsability.UNAVAILABLE_NOT_IN_CATALOG,
            reason="no entry for this game in the catalog",
        )

    key = str(entry.get("game_key") or game_key or "<unknown>")
    completeness = _completeness(entry)
    failed = tuple(completeness.get("failed_event_tickers") or ())
    truncated = tuple(completeness.get("pagination_failed_event_tickers") or ())

    # Absent means NOT PROVEN COMPLETE. A missing flag must never read as
    # permission -- that is the same "a missing field means everything is
    # fine" assumption the discovery layer refuses to make.
    complete = completeness.get("native_game_markets_complete")

    verdict_fields = {
        "game_key": key,
        "market_count": int(entry.get("market_count") or completeness.get("markets_discovered") or 0),
        "events_reported": int(completeness.get("related_event_tickers_reported") or 0),
        "events_fetched": int(completeness.get("events_fetched") or 0),
        "failed_event_tickers": failed,
        "pagination_failed_event_tickers": truncated,
        "api_failures": int(completeness.get("api_failures") or 0),
        "markets_file": entry.get("markets_file"),
        "title": entry.get("title"),
    }

    if complete is True:
        return GameUsabilityVerdict(
            usability=GameUsability.USABLE,
            reason="native_game_markets_complete is true",
            **verdict_fields,
        )
    if complete is None:
        return GameUsabilityVerdict(
            usability=GameUsability.UNAVAILABLE_INCOMPLETE_DISCOVERY,
            reason="native_game_markets_complete is absent; completeness is unproven",
            **verdict_fields,
        )
    return GameUsabilityVerdict(
        usability=GameUsability.UNAVAILABLE_INCOMPLETE_DISCOVERY,
        reason="native_game_markets_complete is false",
        **verdict_fields,
    )


@dataclass(frozen=True)
class SlateAssessment:
    """The whole slate, split into what may and may not be analyzed."""

    capture_complete: bool
    captured_at: str | None
    usable: list[GameUsabilityVerdict] = field(default_factory=list)
    unavailable: list[GameUsabilityVerdict] = field(default_factory=list)

    @property
    def usable_game_keys(self) -> list[str]:
        return [v.game_key for v in self.usable]

    def verdict_for(self, game_key: str) -> GameUsabilityVerdict:
        for verdict in (*self.usable, *self.unavailable):
            if verdict.game_key == game_key:
                return verdict
        return GameUsabilityVerdict(
            game_key=game_key,
            usability=GameUsability.UNAVAILABLE_NOT_IN_CATALOG,
            reason="no entry for this game in the catalog",
        )

    def summary(self) -> str:
        line = (
            f"slate captured {self.captured_at}: {len(self.usable)} game(s) usable, "
            f"{len(self.unavailable)} unavailable (capture_complete={self.capture_complete})"
        )
        if self.unavailable:
            line += "\n" + "\n".join(f"  {v.explain()}" for v in self.unavailable)
        return line


def assess_slate(catalog: dict[str, Any]) -> SlateAssessment:
    """Split a catalog index into usable and unavailable games.

    Note what this does NOT do: it does not refuse the whole slate when
    `capture_complete` is false. A slate-wide failure flag is a health
    signal; each game's own completeness decides that game."""
    usable: list[GameUsabilityVerdict] = []
    unavailable: list[GameUsabilityVerdict] = []
    for entry in catalog.get("games") or []:
        verdict = assess_game(entry)
        (usable if verdict.may_handicap else unavailable).append(verdict)
    return SlateAssessment(
        capture_complete=bool((catalog.get("completeness") or {}).get("capture_complete")),
        captured_at=(catalog.get("capture") or {}).get("captured_at"),
        usable=usable,
        unavailable=unavailable,
    )


class IncompleteGameError(RuntimeError):
    """Raised when a consumer tries to analyze a game it may not analyze.

    Exists so the guard can be a hard stop in code, not a comment someone
    has to remember to read."""

    def __init__(self, verdict: GameUsabilityVerdict) -> None:
        super().__init__(verdict.explain())
        self.verdict = verdict


def require_usable_game(entry: dict[str, Any] | None, game_key: str | None = None) -> GameUsabilityVerdict:
    """Gate a game before handicapping it. Raises `IncompleteGameError`
    with the diagnostics when the game may not be used."""
    verdict = assess_game(entry, game_key=game_key)
    if not verdict.may_handicap:
        raise IncompleteGameError(verdict)
    return verdict
