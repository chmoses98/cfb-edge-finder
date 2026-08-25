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
_WINNER_TITLE_RE = re.compile(r"^(?P<team>.+?) wins$", re.IGNORECASE)
_WINNER_RULES_TEAM_RE = re.compile(r"^If (?P<team>.+?) wins the ", re.IGNORECASE)
"""Milestone D hardening pass, mission item 7: the confirmed winner/
moneyline grammar, from the SAME live rules_primary evidence
`_MATCHUP_IN_RULES_RE` already relies on (job 97711133675): "If Cornell
wins the Cornell vs Colgate college football game originally scheduled
for Sep 19, 2026, then the market resolves to Yes." The market's own
TITLE for this family is the short "<TEAM> wins" form (mirroring
SPREAD's "<TEAM> wins by over X points" and TOTAL's "Over X points
scored" -- each family's title names only what that contract itself
settles on). `parse_winner_market` requires the title to match this
grammar (previously it accepted ANY non-empty string as a team name --
no grammar was ever enforced), and, when `rules_primary` is also
supplied, cross-checks that the SAME team name is stated as the winner
there too -- a genuine, deterministic title/team/event correspondence
check, not a confidence upgrade taken on faith. A game going to overtime
changes nothing about this grammar or the check: Kalshi settles the
SAME "<TEAM> wins" contract on the final score regardless of how many
periods it took, so there is no separate overtime state for this parser
to special-case."""

_MATCHUP_IN_RULES_RE = re.compile(r"\bthe (?P<matchup>[A-Z].+? vs .+?) college football game")
"""Extracts the two-team matchup embedded in a market's own
`rules_primary` prose -- e.g. "...in the Southern Utah vs Montana college
football game originally scheduled..." -> "Southern Utah vs Montana".

*** CONFIRMED ACROSS ALL THREE CORE_V1 FAMILIES, NOT JUST SPREAD/TOTAL ***
The word immediately before "the <TEAM> vs <TEAM> college football game"
differs by family -- SPREAD/TOTAL use "...points **in** the <matchup>
college football game..." while WINNER/moneyline uses "<TEAM> wins
**the** <matchup> college football game..." (no "in"). A first version
of this regex required literal "in the ", which matched SPREAD/TOTAL but
left every live KXNCAAFGAME market PARSE_UNRESOLVED (job 97710429233:
256/3995 observations model-priced, but zero of the 368 live KXNCAAFGAME
markets among them). A follow-up live probe (job 97711133675) confirmed
the real winner-market text: "If Cornell wins the Cornell vs Colgate
college football game originally scheduled for Sep 19, 2026, then the
market resolves to Yes." -- matching on `\\bthe ` instead of `in the `
covers both phrasings.

*** CASE-SENSITIVE ON PURPOSE, REQUIRES AN UPPERCASE MATCHUP START ***
The TOTAL family's real text is "If **the** teams collectively score
more than 80.5 points in **the** Southern Utah vs Montana college
football game..." -- TWO "the"s appear before "college football game".
Matching `\\bthe ` case-insensitively anchors on the FIRST one ("the
teams collectively ... Montana"), producing a wrong, over-long matchup.
Requiring `[A-Z]` immediately after "the " (real team names are always
capitalized proper nouns, unlike "teams") skips that generic occurrence
and lands on the real matchup every time. This regex therefore has NO
`re.IGNORECASE` flag, unlike the title-grammar regexes above.

*** WHY THIS EXISTS: A REAL, LIVE-CONFIRMED STRUCTURAL GAP ***
A live `GET /events/{event_ticker}` response (job 97709841758, this
branch) confirmed the EVENT object itself carries no title/subtitle/
matchup field at all -- only `available_on_brokers`, `category`,
`collateral_return_type`, `event_ticker`, `exchange_index`,
`last_updated_ts`, and a nested `markets` array. Each individual
MARKET's own `title` is single-team/single-line ("Southern Utah wins by
over 4.5 points", "Over 80.5 points scored") and can never be split into
two team names by `game_mapping._split_title`. The ONLY place a genuine
two-team matchup string appears anywhere in this API's real responses is
embedded in prose inside `rules_primary`, consistently phrased "...in
the <TEAM> vs <TEAM> college football game...". This function extracts
exactly that, nothing more -- a first live snapshot capture that instead
passed a market's own single-team title as mapping evidence saw 100% of
2,278 discovered game-level markets land in TICKER_UNRESOLVED for
exactly this reason (see docs/MILESTONE_D.md section 15)."""


def extract_matchup_from_rules_primary(rules_primary: str | None) -> str | None:
    """Returns a `"<TEAM> vs <TEAM>"` string suitable as
    `KalshiGameEvidence.title` (whose separator list already includes
    `" vs "`), or None if `rules_primary` is absent or doesn't match the
    confirmed phrasing -- callers should treat None as PARSE_UNRESOLVED-
    worthy evidence, never guess a matchup from anywhere else."""
    if not rules_primary:
        return None
    match = _MATCHUP_IN_RULES_RE.search(rules_primary)
    if match is None:
        return None
    return match.group("matchup").strip()

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


def parse_winner_market(title: str, rules_primary: str | None = None) -> ParsedContract:
    """Architecturally supported (mirrors Milestone B.5's historical
    audit: one binary YES/NO contract per team, cent price = implied win
    probability directly, no separate threshold). Hardened (mission item
    7) to require the confirmed "<TEAM> wins" title grammar -- a title
    that doesn't match it is PARSE_UNRESOLVED, never accepted as a raw
    team name on faith.

    `semantics_confidence` starts at "unconfirmed" and is raised to
    "confirmed_live" ONLY when `rules_primary` is also supplied AND its
    own "If <TEAM> wins the ..." text names the EXACT SAME team as the
    title -- a deterministic title/team/event correspondence, never a
    confidence bump taken on faith (mission: "Do not lower semantics
    confidence unless the title/team/event correspondence is
    deterministically verified" -- read together with this module's
    existing spread/total precedent of rejecting inconsistent evidence
    rather than guessing, the same applies in reverse: never RAISE
    confidence without that same verification either). If `rules_primary`
    names a DIFFERENT team than the title, that is a genuine anomaly
    (inconsistent payload) and this returns PARSE_UNRESOLVED, exactly
    like a spread/total title-vs-floor_strike mismatch. If
    `rules_primary` is absent, or present but doesn't match this
    module's own confirmed rules-text grammar, confidence simply stays
    "unconfirmed" -- never guessed either way."""
    stripped = title.strip()
    match = _WINNER_TITLE_RE.match(stripped)
    if match is None:
        return ParsedContract(
            reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED,
            detail=f"winner title {title!r} did not match the confirmed '<TEAM> wins' grammar",
        )
    team = match.group("team").strip()
    if not team:
        return ParsedContract(reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED, detail="empty team name in winner title")

    confidence = "unconfirmed"
    detail = f"parsed: {team!r} as a moneyline/winner contract (title grammar confirmed, no rules_primary cross-check)"
    if rules_primary:
        rules_match = _WINNER_RULES_TEAM_RE.match(rules_primary.strip())
        if rules_match is not None:
            rules_team = rules_match.group("team").strip()
            if rules_team != team:
                return ParsedContract(
                    reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED,
                    detail=(
                        f"title names {team!r} but rules_primary names {rules_team!r} as the winning team "
                        f"-- inconsistent payload, never guessed"
                    ),
                )
            confidence = "confirmed_live"
            detail = (
                f"parsed: {team!r} confirmed by BOTH the title's '<TEAM> wins' grammar AND rules_primary's "
                f"'If {team} wins the ...' text -- deterministic title/team/event correspondence"
            )

    return ParsedContract(
        reason=None,
        detail=detail,
        market_family=MarketFamily.MONEYLINE,
        raw_team_name=team,
        semantics_confidence=confidence,
    )
