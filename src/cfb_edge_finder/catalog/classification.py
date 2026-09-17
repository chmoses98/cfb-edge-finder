"""Best-effort market classification, applied AFTER discovery.

*** THE ONE RULE THIS MODULE EXISTS TO ENFORCE ***
Classification is a LABEL, never a FILTER. Every market that discovery
found appears in the catalog with a family attached; a market this module
cannot confidently label gets `MarketFamilyLabel.UNKNOWN` and is kept in
full, with its raw payload intact. UNKNOWN NEVER MEANS DROP.

That is not a stylistic preference. The repository's previous live path
gated on a three-family `CORE_V1` set (moneyline/spread/total) derived
from a hand-written registry of what Kalshi had been *observed* to offer.
A live sweep run during this pivot found 149 college-football series on
the exchange -- quarter winners, quarter spreads and totals, second-half
lines, both-teams-to-score per quarter, a dozen team-stat families, first
touchdown scorer, overtime, and more -- of which that registry knew four.
Any architecture that decides what to keep from a pre-agreed family list
is therefore structurally guaranteed to miss most of the real market
surface, and to miss it silently.

*** WHY THE SERIES TICKER IS THE PRIMARY SIGNAL, AND WHY IT IS NOT TRUSTED
ALONE ***
Kalshi's series tickers are strongly systematic (KXNCAAFSPREAD,
KXNCAAF1HTOTAL, KXNCAAF3QSPREAD, KXNCAAFTEAMTOTAL...), so a structural
read of the ticker generalizes to series that do not exist yet: a
KXNCAAF2HSPREAD created tomorrow classifies as a second-half spread
without a code change, because the period prefix and the family suffix are
parsed rather than looked up. Where the ticker is ambiguous or unfamiliar,
the title/rules text is used as corroboration only. Nothing here infers
contract semantics from "what a sportsbook would normally offer" -- the
raw Kalshi contract is authoritative, which is exactly why the raw payload
travels with every classified record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class MarketFamilyLabel(StrEnum):
    """Classification labels. Deliberately open-ended in spirit: adding a
    label here changes only how a market is DESCRIBED, never whether it is
    captured. `UNKNOWN` is a first-class, fully-retained outcome."""

    GAME_MONEYLINE = "game_moneyline"
    GAME_SPREAD = "game_spread"
    GAME_TOTAL = "game_total"
    TEAM_TOTAL = "team_total"

    FIRST_HALF_MONEYLINE = "first_half_moneyline"
    FIRST_HALF_SPREAD = "first_half_spread"
    FIRST_HALF_TOTAL = "first_half_total"
    FIRST_HALF_TEAM_TOTAL = "first_half_team_total"
    SECOND_HALF_MONEYLINE = "second_half_moneyline"
    SECOND_HALF_SPREAD = "second_half_spread"
    SECOND_HALF_TOTAL = "second_half_total"

    QUARTER_MONEYLINE = "quarter_moneyline"
    QUARTER_SPREAD = "quarter_spread"
    QUARTER_TOTAL = "quarter_total"

    WINNING_MARGIN = "winning_margin"
    BOTH_TEAMS_TO_SCORE = "both_teams_to_score"
    FIRST_SCORE = "first_score"
    TOUCHDOWN_SCORER = "touchdown_scorer"
    PLAYER_PROP = "player_prop"
    TEAM_STAT_PROP = "team_stat_prop"
    GAME_STAT_PROP = "game_stat_prop"
    OVERTIME = "overtime"
    MULTIVARIATE_COMBO = "multivariate_combo"
    SEASON_FUTURES = "season_futures"
    OTHER = "other"
    UNKNOWN = "unknown"
    """Retained in full. A market is UNKNOWN when nothing in its ticker,
    title or rules resolves to a family with reasonable confidence -- which
    is precisely the case a handicapper most needs to see verbatim."""


class ClassificationConfidence(StrEnum):
    STRUCTURAL = "structural"
    """The series ticker parsed cleanly into period + family."""
    TEXTUAL = "textual"
    """The ticker did not resolve; the title/rules text did."""
    UNRESOLVED = "unresolved"
    """Neither did. Family is UNKNOWN and the market is kept as-is."""


class GamePeriod(StrEnum):
    FULL_GAME = "full_game"
    FIRST_HALF = "first_half"
    SECOND_HALF = "second_half"
    FIRST_QUARTER = "first_quarter"
    SECOND_QUARTER = "second_quarter"
    THIRD_QUARTER = "third_quarter"
    FOURTH_QUARTER = "fourth_quarter"
    OVERTIME = "overtime"
    SEASON = "season"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MarketClassification:
    family: MarketFamilyLabel
    period: GamePeriod
    confidence: ClassificationConfidence
    is_alternate_line: bool = False
    """True when this contract is one rung of a multi-rung ladder for the
    same period and family (an alternate spread/total). Determined by the
    caller from siblings within the same event -- a single market in
    isolation cannot know whether it is the 'main' line, and pretending
    otherwise is how a real alternate ladder gets mislabelled."""
    rationale: str = ""
    """Why this label was chosen, so a surprising classification is
    debuggable from the artifact alone rather than by re-deriving it."""


# --- Period prefixes as they appear inside a Kalshi series ticker --------
# Parsed structurally so an unseen series composed of the same parts
# classifies correctly without being enumerated anywhere.
_PERIOD_TOKENS: tuple[tuple[str, GamePeriod], ...] = (
    ("1H", GamePeriod.FIRST_HALF),
    ("2H", GamePeriod.SECOND_HALF),
    ("1Q", GamePeriod.FIRST_QUARTER),
    ("2Q", GamePeriod.SECOND_QUARTER),
    ("3Q", GamePeriod.THIRD_QUARTER),
    ("4Q", GamePeriod.FOURTH_QUARTER),
)

# --- Family suffixes, longest-first so TEAMTOTAL wins over TOTAL --------
_FAMILY_SUFFIXES: tuple[tuple[str, MarketFamilyLabel], ...] = (
    ("TEAMTOTAL", MarketFamilyLabel.TEAM_TOTAL),
    ("BTTS", MarketFamilyLabel.BOTH_TEAMS_TO_SCORE),
    ("FIRSTTDTEAM", MarketFamilyLabel.TOUCHDOWN_SCORER),
    ("FIRSTTD", MarketFamilyLabel.TOUCHDOWN_SCORER),
    ("FTTS", MarketFamilyLabel.FIRST_SCORE),
    ("SPREAD", MarketFamilyLabel.GAME_SPREAD),
    ("TOTAL", MarketFamilyLabel.GAME_TOTAL),
    ("MARGIN", MarketFamilyLabel.WINNING_MARGIN),
    ("GAME", MarketFamilyLabel.GAME_MONEYLINE),
    ("OT", MarketFamilyLabel.OVERTIME),
)

# Team-stat families: the suffix names a countable team statistic. These
# are real, tradeable CFB markets (sacks, turnovers, field goals, receiving
# yards...) that no sportsbook-shaped family enum has a slot for.
_TEAM_STAT_SUFFIXES = (
    "TEAMSACK",
    "TEAMTO",
    "TEAMFG",
    "TEAMINT",
    "TEAMTD",
    "TEAMREC",
    "TEAMRECYDS",
    "TEAMRECTD",
    "TEAMRSHTD",
    "TEAMRSHATT",
    "TEAMRSHYDS",
    "TEAMPASSYDS",
    "TEAMPASSTD",
)

_GAME_STAT_SUFFIXES = ("TOTALFG", "TOTALTD", "2PT", "DSTTD", "MOSTOT", "QHIGHSCORE", "HIGHSCORE")

# Season-long / futures markets. Classified (not dropped) so the catalog
# can separate "what can I bet on THIS GAME" from season-level inventory
# that happens to share the college-football tag.
_FUTURES_MARKERS = (
    "CHAMPION",
    "WINS",
    "PLAYOFF",
    "SEED",
    "APRANK",
    "APANY",
    "APCHURN",
    "CFPPOLL",
    "TOPAPRANK",
    "AWARD",
    "COTY",
    "HEISMAN",
    "COACH",
    "CONF",
    "QUAL",
    "UNDEFEATED",
    "BOWLGAME",
    "JOINCONF",
    "LEADER",
    "REGTOP",
    "STADIUM",
    "ATTENDANCE",
    "H2HWINS",
    "TFUT",
    "UPSET",
)

_PLAYER_PROP_MARKERS = ("PASSYDS", "RECYDS", "RSHYDS", "PASSTDS", "PASSINT", "REC", "RUSHYDS", "ANYTIMETD")

_MULTIVARIATE_MARKERS = ("MVE", "PREPACK", "SGP", "MULTI", "PARLAY")


@dataclass(frozen=True)
class _TickerParse:
    period: GamePeriod
    remainder: str


def _strip_cfb_prefix(series_ticker: str) -> str | None:
    """Return the part of a series ticker after its college-football
    prefix, or None if it does not look like a CFB series at all."""
    t = series_ticker.upper()
    for prefix in ("KXNCAAFCS", "KXNCAAF", "KXNCAA", "KXCFB", "KXCFP", "NCAAF"):
        if t.startswith(prefix):
            return t[len(prefix) :]
    return None


def _parse_period(remainder: str) -> _TickerParse:
    for token, period in _PERIOD_TOKENS:
        if remainder.startswith(token):
            return _TickerParse(period=period, remainder=remainder[len(token) :])
    return _TickerParse(period=GamePeriod.FULL_GAME, remainder=remainder)


def _period_family(period: GamePeriod, base: MarketFamilyLabel) -> MarketFamilyLabel:
    """Project a full-game family onto the period the ticker named. Kept as
    an explicit table rather than string-building so an unmapped
    combination degrades to the base label instead of inventing a member
    that does not exist."""
    if period == GamePeriod.FULL_GAME:
        return base
    halves = {
        GamePeriod.FIRST_HALF: {
            MarketFamilyLabel.GAME_MONEYLINE: MarketFamilyLabel.FIRST_HALF_MONEYLINE,
            MarketFamilyLabel.GAME_SPREAD: MarketFamilyLabel.FIRST_HALF_SPREAD,
            MarketFamilyLabel.GAME_TOTAL: MarketFamilyLabel.FIRST_HALF_TOTAL,
            MarketFamilyLabel.TEAM_TOTAL: MarketFamilyLabel.FIRST_HALF_TEAM_TOTAL,
        },
        GamePeriod.SECOND_HALF: {
            MarketFamilyLabel.GAME_MONEYLINE: MarketFamilyLabel.SECOND_HALF_MONEYLINE,
            MarketFamilyLabel.GAME_SPREAD: MarketFamilyLabel.SECOND_HALF_SPREAD,
            MarketFamilyLabel.GAME_TOTAL: MarketFamilyLabel.SECOND_HALF_TOTAL,
        },
    }
    if period in halves:
        return halves[period].get(base, base)
    quarters = {
        MarketFamilyLabel.GAME_MONEYLINE: MarketFamilyLabel.QUARTER_MONEYLINE,
        MarketFamilyLabel.GAME_SPREAD: MarketFamilyLabel.QUARTER_SPREAD,
        MarketFamilyLabel.GAME_TOTAL: MarketFamilyLabel.QUARTER_TOTAL,
    }
    if period in (
        GamePeriod.FIRST_QUARTER,
        GamePeriod.SECOND_QUARTER,
        GamePeriod.THIRD_QUARTER,
        GamePeriod.FOURTH_QUARTER,
    ):
        return quarters.get(base, base)
    return base


def classify_from_series_ticker(series_ticker: str | None) -> MarketClassification | None:
    """Structural classification from the series ticker alone. Returns None
    when the ticker yields nothing, so the caller can fall back to text."""
    if not series_ticker:
        return None
    remainder_or_none = _strip_cfb_prefix(series_ticker)
    if remainder_or_none is None:
        return None
    upper = series_ticker.upper()

    if any(marker in upper for marker in _MULTIVARIATE_MARKERS):
        return MarketClassification(
            family=MarketFamilyLabel.MULTIVARIATE_COMBO,
            period=GamePeriod.UNKNOWN,
            confidence=ClassificationConfidence.STRUCTURAL,
            rationale=f"series ticker {series_ticker!r} carries a multivariate/pre-pack marker",
        )

    parsed = _parse_period(remainder_or_none)
    rem = parsed.remainder

    # A bare period token with no family suffix is that period's winner
    # (KXNCAAF1H = 1st Half Winner, KXNCAAF3Q = 3rd Quarter Winner).
    if rem == "" and parsed.period != GamePeriod.FULL_GAME:
        return MarketClassification(
            family=_period_family(parsed.period, MarketFamilyLabel.GAME_MONEYLINE),
            period=parsed.period,
            confidence=ClassificationConfidence.STRUCTURAL,
            rationale=f"series ticker {series_ticker!r} is a bare period token => {parsed.period} winner",
        )

    # Futures before game-level: KXNCAAFCONFMATCHUP must not match "GAME".
    if any(marker in rem for marker in _FUTURES_MARKERS):
        return MarketClassification(
            family=MarketFamilyLabel.SEASON_FUTURES,
            period=GamePeriod.SEASON,
            confidence=ClassificationConfidence.STRUCTURAL,
            rationale=f"series ticker {series_ticker!r} matches a season/futures marker",
        )

    for suffix in _TEAM_STAT_SUFFIXES:
        if rem == suffix:
            return MarketClassification(
                family=MarketFamilyLabel.TEAM_STAT_PROP,
                period=parsed.period,
                confidence=ClassificationConfidence.STRUCTURAL,
                rationale=f"series ticker {series_ticker!r} names team statistic {suffix!r}",
            )
    for suffix in _GAME_STAT_SUFFIXES:
        if rem == suffix:
            family = MarketFamilyLabel.OVERTIME if "OT" in suffix else MarketFamilyLabel.GAME_STAT_PROP
            return MarketClassification(
                family=family,
                period=parsed.period,
                confidence=ClassificationConfidence.STRUCTURAL,
                rationale=f"series ticker {series_ticker!r} names game statistic {suffix!r}",
            )

    for suffix, base in _FAMILY_SUFFIXES:
        if rem == suffix or rem.endswith(suffix):
            return MarketClassification(
                family=_period_family(parsed.period, base),
                period=parsed.period,
                confidence=ClassificationConfidence.STRUCTURAL,
                rationale=f"series ticker {series_ticker!r} resolves to {suffix!r} for {parsed.period}",
            )

    if any(marker in rem for marker in _PLAYER_PROP_MARKERS):
        return MarketClassification(
            family=MarketFamilyLabel.PLAYER_PROP,
            period=parsed.period,
            confidence=ClassificationConfidence.STRUCTURAL,
            rationale=f"series ticker {series_ticker!r} matches a player-stat marker",
        )
    return None


_TEXT_TOTAL = re.compile(r"\b(total|combined|over/under|o/u)\b", re.I)
_TEXT_SPREAD = re.compile(r"\bwin by|\bspread\b|\bmargin\b|\bcover\b", re.I)
_TEXT_WINNER = re.compile(r"\bwho will win\b|\bwin the game\b|\bto win\b|\bwinner\b", re.I)
_TEXT_FIRST_HALF = re.compile(r"\b(1st|first) half\b", re.I)
_TEXT_SECOND_HALF = re.compile(r"\b(2nd|second) half\b", re.I)
_TEXT_QUARTER = re.compile(r"\b(1st|2nd|3rd|4th|first|second|third|fourth) quarter\b", re.I)
_QUARTER_WORD_TO_PERIOD = {
    "1st": GamePeriod.FIRST_QUARTER,
    "first": GamePeriod.FIRST_QUARTER,
    "2nd": GamePeriod.SECOND_QUARTER,
    "second": GamePeriod.SECOND_QUARTER,
    "3rd": GamePeriod.THIRD_QUARTER,
    "third": GamePeriod.THIRD_QUARTER,
    "4th": GamePeriod.FOURTH_QUARTER,
    "fourth": GamePeriod.FOURTH_QUARTER,
}


def _period_from_text(text: str) -> GamePeriod:
    quarter = _TEXT_QUARTER.search(text)
    if quarter:
        return _QUARTER_WORD_TO_PERIOD.get(quarter.group(1).lower(), GamePeriod.UNKNOWN)
    if _TEXT_FIRST_HALF.search(text):
        return GamePeriod.FIRST_HALF
    if _TEXT_SECOND_HALF.search(text):
        return GamePeriod.SECOND_HALF
    return GamePeriod.FULL_GAME


def classify_from_text(title: str | None, rules_primary: str | None = None) -> MarketClassification | None:
    """Corroboration only, used when the ticker resolved nothing. Reads
    Kalshi's own contract text -- never an assumption about what a
    sportsbook offers."""
    text = " ".join(part for part in (title, rules_primary) if part).strip()
    if not text:
        return None
    period = _period_from_text(text)
    if _TEXT_TOTAL.search(text):
        base = MarketFamilyLabel.GAME_TOTAL
    elif _TEXT_SPREAD.search(text):
        base = MarketFamilyLabel.GAME_SPREAD
    elif _TEXT_WINNER.search(text):
        base = MarketFamilyLabel.GAME_MONEYLINE
    else:
        return None
    return MarketClassification(
        family=_period_family(period, base),
        period=period,
        confidence=ClassificationConfidence.TEXTUAL,
        rationale=f"ticker unresolved; contract text matched {base} for {period}",
    )


def classify_market(
    series_ticker: str | None,
    title: str | None = None,
    rules_primary: str | None = None,
) -> MarketClassification:
    """Classify one market. ALWAYS returns a classification -- an
    unresolvable market is labelled UNKNOWN and kept, never dropped."""
    structural = classify_from_series_ticker(series_ticker)
    if structural is not None:
        return structural
    textual = classify_from_text(title, rules_primary)
    if textual is not None:
        return textual
    return MarketClassification(
        family=MarketFamilyLabel.UNKNOWN,
        period=GamePeriod.UNKNOWN,
        confidence=ClassificationConfidence.UNRESOLVED,
        rationale=(
            f"neither series ticker {series_ticker!r} nor contract text resolved to a known family; "
            f"retained verbatim for external interpretation"
        ),
    )


LADDER_FAMILIES = frozenset(
    {
        MarketFamilyLabel.GAME_SPREAD,
        MarketFamilyLabel.GAME_TOTAL,
        MarketFamilyLabel.TEAM_TOTAL,
        MarketFamilyLabel.FIRST_HALF_SPREAD,
        MarketFamilyLabel.FIRST_HALF_TOTAL,
        MarketFamilyLabel.FIRST_HALF_TEAM_TOTAL,
        MarketFamilyLabel.SECOND_HALF_SPREAD,
        MarketFamilyLabel.SECOND_HALF_TOTAL,
        MarketFamilyLabel.QUARTER_SPREAD,
        MarketFamilyLabel.QUARTER_TOTAL,
        MarketFamilyLabel.TEAM_STAT_PROP,
        MarketFamilyLabel.GAME_STAT_PROP,
        MarketFamilyLabel.PLAYER_PROP,
    }
)
"""Families that legitimately list many threshold rungs for one game. Used
only to decide whether `is_alternate_line` is meaningful -- a moneyline
with two contracts is two sides, not two rungs."""
