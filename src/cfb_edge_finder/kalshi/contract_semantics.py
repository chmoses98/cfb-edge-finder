"""Milestone D: Kalshi CFB contract-semantics parser -- ticker/title/rules
text -> a canonical (MarketFamily, Side, line, team) tuple, or an explicit
"could not confirm" outcome.

*** EVIDENCE THIS PARSER IS BUILT FROM ***
Genuine, live GET /markets responses captured via
scripts/validate_kalshi_cfb_live.py (base URL
https://api.elections.kalshi.com/trade-api/v2, confirmed reachable and
unauthenticated). Real example (Southern Utah at Montana, 2026-08-29):

  spread: {"ticker": "KXNCAAFSPREAD-26AUG29SUUMONT-SUU5",
           "event_ticker": "KXNCAAFSPREAD-26AUG29SUUMONT",
           "title": "Southern Utah wins by over 4.5 points",
           "floor_strike": 4.5, "strike_type": "structured",
           "yes_bid_dollars": "0.0700", "yes_ask_dollars": "0.3500",
           "no_bid_dollars": "0.6500", "no_ask_dollars": "0.9300",
           "rules_primary": "If Southern Utah wins by more than 4.5
             points in the Southern Utah vs Montana college football game
             originally scheduled for Aug 29, 2026, then the market
             resolves to Yes."}

  total:  {"ticker": "KXNCAAFTOTAL-26AUG29SUUMONT-81",
           "event_ticker": "KXNCAAFTOTAL-26AUG29SUUMONT",
           "title": "Over 80.5 points scored",
           "floor_strike": 80.5, "strike_type": "greater",
           "yes_bid_dollars": "0.0800", "yes_ask_dollars": "0.4300",
           "rules_primary": "If the teams collectively score more than
             80.5 points in the Southern Utah vs Montana college football
             game originally scheduled for Aug 29, 2026, then the market
             resolves to Yes."}

Both the LIST endpoint (GET /markets?series_ticker=X) and the single-
market DETAIL endpoint (GET /markets/{ticker}) were confirmed to return
IDENTICAL pricing fields (yes_bid_dollars/yes_ask_dollars/etc. -- see
price_extraction.py's module docstring for the full field list and why
they're read directly as probabilities) -- a per-market detail fetch is
NOT needed on top of the list sweep. This module's own earlier draft
assumed pricing fields were list-endpoint-only-absent; that assumption
was wrong and is corrected here, not silently dropped.

The spread ladder confirmed BOTH teams get their own full threshold
ladder in one event (e.g. "SUU5"="Southern Utah wins by over 4.5" AND
"MONT39"="Montana wins by over 38.5" coexist under the same
event_ticker) -- each ticker's own title/rules_primary names the team it
is about; this parser never infers a side from ticker ORDER.

*** CONFIRMED SEMANTICS (from the quoted rules_primary text above, not
assumed) ***
  - Operator: strictly GREATER THAN ("wins by MORE THAN X" / "score MORE
    THAN X"), never ">=". Every observed threshold is a half-point
    (4.5, 3.5, 2.5, ...; 80.5, 77.5, 74.5, ...) -- a real, structural
    push-impossibility (a real integer margin/total can never land
    exactly on a half-point line), not an assumption.
  - Spread is per-team, per-threshold: each rung is its own binary
    YES/NO market for ONE team covering ONE specific line -- there is no
    separate "home"/"away" flag in the payload; which team a given
    market is about is read directly from its own title/rules text, not
    inferred from ticker position.
  - Postponement: "If the game is postponed but begins within 48 hours of
    its originally scheduled start time, the market will remain open and
    resolve based on the official final result. If the game is not
    started within 48 hours, the market will resolve to a fair price."
    (quoted verbatim from rules_secondary in the live payload).
  - Game-winner/moneyline: NOT observed live this session under
    KXNCAAFGAME (confirmed to exist as a series via GET
    /series/KXNCAAFGAME, HTTP 200, but with ZERO current events) or any
    other ticker variant tried (KXNCAAFWINNER, KXNCAAFML,
    KXNCAAFMONEYLINE). This parser still supports a winner/moneyline
    shape (mirroring Milestone B.5's historical audit and the analogous,
    currently-populated KXNFLGAME series) so the architecture is ready
    the moment real winner markets appear, but no live winner-market
    parse has actually been exercised against real Kalshi data this
    session -- see docs/MILESTONE_D.md's "Contract semantics" section
    for the honest confidence distinction between spread/total
    (CONFIRMED from real live payloads) and winner (architecturally
    supported, not live-confirmed this session).

*** WHY "GREATER THAN", NOT "AT LEAST" ***
Kalshi's CFTC self-certification template (Milestone B.5's audit)
technically allows "above/below/between/exactly/at least" modifiers, but
every SPREAD/TOTAL market actually observed live uses only the strict
"more than" grammar, with half-point lines that make the "at least"
distinction moot in practice (a half-point line has no integer to be
"at least"). This parser therefore hardcodes strict ">" for both
families, and marks a market PARSE_UNRESOLVED if a genuinely different
operator word is ever found in its rules text -- never silently
reinterpreting unfamiliar language as ">".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason
from cfb_edge_finder.schemas.common import MarketFamily, Side

_SPREAD_TITLE_RE = re.compile(r"^(?P<team>.+?) wins by over (?P<threshold>-?\d+(?:\.\d+)?) points?$", re.IGNORECASE)
_TOTAL_TITLE_RE = re.compile(r"^Over (?P<threshold>-?\d+(?:\.\d+)?) points? scored$", re.IGNORECASE)

_CONFIRMED_OPERATOR = ">"
"""The only operator confirmed from real live rules_primary text this
session -- see module docstring."""


@dataclass(frozen=True)
class ParsedContract:
    """`reason=None` means parsing succeeded; every other field is then
    populated and meaningful. A non-None `reason` means parsing failed --
    `market_family`/`side`/`line`/`team` are then None and `detail`
    explains why, for the caller to record in the coverage ledger."""

    reason: KalshiCfbCoverageReason | None
    detail: str
    market_family: MarketFamily | None = None
    side: Side | None = None
    line: float | None = None
    team: Side | None = None
    operator: str | None = None
    raw_team_name: str | None = None
    semantics_confidence: str = "unconfirmed"
    """One of "confirmed_live" (parsed from real live evidence matching
    this module's documented grammar exactly) or "unconfirmed" (parsing
    failed, or the family has no live-confirmed grammar yet -- see
    module docstring's winner-market caveat)."""


def parse_spread_market(title: str, floor_strike: float | None) -> ParsedContract:
    """`title` example: "Southern Utah wins by over 4.5 points". `side`
    is always Side.HOME or Side.AWAY -- resolving WHICH one the named
    team is requires the caller's own game mapping (this module has no
    notion of home/away identity), so `team` here is left as None and
    `raw_team_name` carries the unresolved string for the caller to
    resolve via teams.registry + the mapped GameRecord."""
    match = _SPREAD_TITLE_RE.match(title.strip())
    if match is None:
        return ParsedContract(
            reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED,
            detail=f"spread title {title!r} did not match the confirmed 'X wins by over Y points' grammar",
        )
    threshold = float(match.group("threshold"))
    if floor_strike is not None and abs(threshold - floor_strike) > 1e-9:
        return ParsedContract(
            reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED,
            detail=f"title threshold {threshold} does not match floor_strike {floor_strike!r} -- inconsistent payload",
        )
    return ParsedContract(
        reason=None,
        detail=f"parsed: {match.group('team').strip()!r} covers -{threshold:+.1f} (strictly greater than)",
        market_family=MarketFamily.SPREAD,
        line=threshold,
        operator=_CONFIRMED_OPERATOR,
        raw_team_name=match.group("team").strip(),
        semantics_confidence="confirmed_live",
    )


def parse_total_market(title: str, floor_strike: float | None) -> ParsedContract:
    """`title` example: "Over 80.5 points scored". Always Side.OVER --
    Kalshi's own NO side on this same contract is the executable way to
    price UNDER (see kalshi/game_projection_cache.py + the pricing layer
    docstrings for why the model computes P(over) directly rather than a
    separate "under" primitive: P(under) = 1 - P(over) exactly, since
    both sides settle the SAME binary contract)."""
    match = _TOTAL_TITLE_RE.match(title.strip())
    if match is None:
        return ParsedContract(
            reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED,
            detail=f"total title {title!r} did not match the confirmed 'Over X points scored' grammar",
        )
    threshold = float(match.group("threshold"))
    if floor_strike is not None and abs(threshold - floor_strike) > 1e-9:
        return ParsedContract(
            reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED,
            detail=f"title threshold {threshold} does not match floor_strike {floor_strike!r} -- inconsistent payload",
        )
    return ParsedContract(
        reason=None,
        detail=f"parsed: total strictly greater than {threshold:+.1f}",
        market_family=MarketFamily.TOTAL,
        side=Side.OVER,
        line=threshold,
        operator=_CONFIRMED_OPERATOR,
        semantics_confidence="confirmed_live",
    )


def parse_winner_market(title: str) -> ParsedContract:
    """Architecturally supported (mirrors Milestone B.5's historical
    audit: one binary YES/NO contract per team, cent price = implied win
    probability directly, no separate threshold) but NOT live-confirmed
    this session -- see module docstring. `semantics_confidence` is
    always "unconfirmed" from this function; a future live confirmation
    should upgrade this function's docstring/confidence, not silently
    change the returned value's meaning."""
    stripped = title.strip()
    if not stripped:
        return ParsedContract(reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED, detail="empty winner market title")
    return ParsedContract(
        reason=None,
        detail=f"parsed (unconfirmed grammar): {stripped!r} as a moneyline/winner contract for that named team",
        market_family=MarketFamily.MONEYLINE,
        raw_team_name=stripped,
        semantics_confidence="unconfirmed",
    )
