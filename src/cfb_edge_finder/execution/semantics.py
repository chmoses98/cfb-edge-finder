"""Contract semantics: what does YES mean, what does NO mean, and what
probability would price it.

*** WHY THIS IS SEPARATE FROM CLASSIFICATION ***
`catalog/classification.py` answers "what family is this?" -- a label.
This module answers "what would have to be true for this contract to
settle YES?" -- a claim about the physical game, expressed in a form the
evaluator can price from a supplied handicap. A family label alone cannot
do that: `game_spread` does not say which team, which direction, or which
rung, and pricing the wrong side of a rung is a silent, total error.

*** NOTHING HERE IS DROPPED ***
A contract whose semantics cannot be derived structurally is NOT removed.
It gets `kind = binary_event` and `requires = explicit_probability`, which
means: the handicap can still price it by naming its ticker, and if the
handicap does not, the evaluator marks it explicitly UNPRICEABLE. Silence
is never an outcome.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------- slots


class TeamSlot(StrEnum):
    HOME = "home"
    AWAY = "away"
    TIE = "tie"
    NEITHER = "neither"
    """A real outcome on some Kalshi three-way markets: "No team scores a
    TD". Not an error and not a missing team."""
    NONE = "none"
    """The contract is not team-scoped at all (a game total)."""
    UNRESOLVED = "unresolved"
    """The contract IS team-scoped but we could not say which team. This
    is a mapping failure and is reported as one -- never guessed."""


class ContractKind(StrEnum):
    MONEYLINE = "moneyline"
    TIE = "tie"
    SPREAD = "spread"
    TOTAL = "total"
    TEAM_TOTAL = "team_total"
    MARGIN_BAND = "margin_band"
    BINARY_EVENT = "binary_event"
    """Everything structurally real but not derivable from a score
    distribution: first-TD team, defensive TD, overtime, team stat props,
    1H/FT doubles, and any family Kalshi invents tomorrow."""


class PricingRequirement(StrEnum):
    PERIOD_DISTRIBUTION = "period_distribution"
    EXPLICIT_PROBABILITY = "explicit_probability"


class SemanticsStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED_TEAM = "unresolved_team"
    UNSTATEABLE = "unstateable"
    """We cannot even say in words what YES means: no title, no rules, no
    subtitle. Vanishingly rare, and the only semantics outcome that is a
    mechanical exclusion."""


# ------------------------------------------------------- period mapping

PERIODS = (
    "full_game",
    "first_half",
    "second_half",
    "first_quarter",
    "second_quarter",
    "third_quarter",
    "fourth_quarter",
    "overtime",
)

_SCORE_PERIODS = frozenset(PERIODS) - {"overtime"}
"""Periods for which a supplied score distribution is meaningful. Overtime
scoring is conditional on reaching overtime, which a marginal period
distribution does not express -- those contracts price from an explicit
probability instead."""

_MONEYLINE_FAMILIES = frozenset(
    {"game_moneyline", "first_half_moneyline", "second_half_moneyline", "quarter_moneyline"}
)
_SPREAD_FAMILIES = frozenset(
    {"game_spread", "first_half_spread", "second_half_spread", "quarter_spread"}
)
_TOTAL_FAMILIES = frozenset({"game_total", "first_half_total", "second_half_total", "quarter_total"})
_TEAM_TOTAL_FAMILIES = frozenset(
    {"team_total", "first_half_team_total", "second_half_team_total", "quarter_team_total"}
)
_MARGIN_FAMILIES = frozenset({"winning_margin"})

_TIE_TOKENS = ("TIE", "DRAW")
_NEITHER_TOKENS = ("NONE", "NEITHER", "NOTEAM")

_TRAILING_DIGITS = re.compile(r"\d+$")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(name: str | None) -> str:
    return _NON_ALNUM.sub("", str(name or "").lower())


# --------------------------------------------------------- game teams


@dataclass(frozen=True)
class GameTeams:
    """Who is playing, which one is home, and how confident we are.

    Home/away is NOT cosmetic here: every spread, moneyline and team-total
    probability the handicap supplies is keyed to one of these two slots,
    so a flip inverts the pricing of hundreds of contracts at once. The
    packet therefore publishes `home_away_source` and the handicap payload
    is required to echo both names back -- a silent flip becomes a loud
    rejection instead of a wrong bet.
    """

    home_name: str | None
    away_name: str | None
    home_away_source: str
    home_away_confidence: str
    code_to_slot: dict[str, str] = field(default_factory=dict)
    uuid_to_slot: dict[str, str] = field(default_factory=dict)
    name_to_slot: dict[str, str] = field(default_factory=dict)

    @property
    def known(self) -> bool:
        return bool(self.home_name and self.away_name)

    def slot_for_name(self, value: str | None) -> str | None:
        if not value:
            return None
        return self.name_to_slot.get(_norm(value))

    def slot_for_prefix(self, text: str | None) -> str | None:
        """Longest-name-first prefix match, so "Miami" never wins over
        "Miami (OH)" when both are in the same game."""
        if not text:
            return None
        lowered = str(text).lower()
        for name, slot in sorted(
            ((n, s) for n, s in self.raw_names.items()), key=lambda kv: -len(kv[0])
        ):
            if lowered.startswith(name.lower()):
                return slot
        return None

    @property
    def raw_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        if self.away_name:
            names[self.away_name] = TeamSlot.AWAY.value
        if self.home_name:
            names[self.home_name] = TeamSlot.HOME.value
        return names


def _split_matchup(title: str | None) -> tuple[str | None, str | None, str, str]:
    """(away, home, source, confidence) from a game title.

    " at " is Kalshi's own milestone phrasing and states home/away
    outright. " vs " does not, but Kalshi's CFB event titles and game keys
    list the away side first (verified on the live slate: milestone
    "LSU at Ole Miss" <-> event "LSU vs Ole Miss" <-> key 26SEP19LSUMISS).
    That ordering is used, and LABELLED AS A CONVENTION rather than as a
    fact, so a reader can overrule it.
    """
    if not title:
        return None, None, "absent", "none"
    text = str(title)
    for sep in (" at ", " @ "):
        if sep in text:
            left, _, right = text.partition(sep)
            return left.strip(), right.strip(), "milestone_title_at", "stated"
    for sep in (" vs. ", " vs ", " v. "):
        if sep in text:
            left, _, right = text.partition(sep)
            return left.strip(), right.strip(), "event_title_order", "convention"
    return None, None, "unparsed", "none"


def _ticker_suffix(market_ticker: str | None, event_ticker: str | None) -> str | None:
    if not market_ticker:
        return None
    ticker = str(market_ticker)
    if event_ticker and ticker.startswith(str(event_ticker) + "-"):
        return ticker[len(str(event_ticker)) + 1 :]
    parts = ticker.rsplit("-", 1)
    return parts[1] if len(parts) == 2 else None


def _suffix_code(suffix: str | None) -> str | None:
    """"LSU10" -> "LSU"; "MISS" -> "MISS"; "17" -> None."""
    if not suffix:
        return None
    code = _TRAILING_DIGITS.sub("", suffix)
    return code or None


def build_game_teams(game_title: str | None, markets: list[dict[str, Any]]) -> GameTeams:
    """Learn the game's two teams, and the code/uuid aliases Kalshi uses
    for them, from the contracts themselves."""
    away, home, source, confidence = _split_matchup(game_title)
    teams = GameTeams(
        home_name=home, away_name=away, home_away_source=source, home_away_confidence=confidence
    )
    name_to_slot: dict[str, str] = {}
    if away:
        name_to_slot[_norm(away)] = TeamSlot.AWAY.value
    if home:
        name_to_slot[_norm(home)] = TeamSlot.HOME.value

    code_to_slot: dict[str, str] = {}
    uuid_to_slot: dict[str, str] = {}

    for market in markets:
        suffix = _ticker_suffix(market.get("market_ticker"), market.get("event_ticker"))
        code = _suffix_code(suffix)
        label = market.get("yes_sub_title") or market.get("title")
        uuid = None
        custom = market.get("custom_strike")
        if isinstance(custom, dict):
            uuid = custom.get("football_team")

        slot = name_to_slot.get(_norm(label))
        if slot is None and label:
            slot = teams.slot_for_prefix(str(label))
        if slot is None:
            continue
        if code and code.upper() not in (*_TIE_TOKENS, *_NEITHER_TOKENS):
            code_to_slot.setdefault(code.upper(), slot)
        if uuid:
            uuid_to_slot.setdefault(str(uuid), slot)

    return GameTeams(
        home_name=home,
        away_name=away,
        home_away_source=source,
        home_away_confidence=confidence,
        code_to_slot=code_to_slot,
        uuid_to_slot=uuid_to_slot,
        name_to_slot=name_to_slot,
    )


# --------------------------------------------------------- the semantics


@dataclass(frozen=True)
class ContractSemantics:
    kind: str
    period: str
    team: str
    comparator: str | None
    threshold: float | None
    cap: float | None
    requires: str
    yes_meaning: str
    no_meaning: str
    status: str
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        out = {
            "kind": self.kind,
            "period": self.period,
            "team": self.team,
            "requires": self.requires,
            "yes_means": self.yes_meaning,
            "no_means": self.no_meaning,
        }
        if self.comparator is not None:
            out["comparator"] = self.comparator
        if self.threshold is not None:
            out["line"] = self.threshold
        if self.cap is not None:
            out["cap"] = self.cap
        return out


def _resolve_team(market: dict[str, Any], teams: GameTeams) -> tuple[str, str]:
    """(slot, rationale). Three independent routes, tried in order of how
    directly Kalshi states the answer."""
    suffix = _ticker_suffix(market.get("market_ticker"), market.get("event_ticker"))
    code = (_suffix_code(suffix) or "").upper()
    if code in _TIE_TOKENS:
        return TeamSlot.TIE.value, f"ticker suffix {suffix!r} is a tie outcome"
    if code in _NEITHER_TOKENS:
        return TeamSlot.NEITHER.value, f"ticker suffix {suffix!r} is a 'neither team' outcome"

    label = market.get("yes_sub_title") or market.get("title")
    text = str(label or "")
    if "tie" in text.lower() and "wins" not in text.lower():
        return TeamSlot.TIE.value, f"subtitle {text!r} names a tie"

    slot = teams.slot_for_name(label)
    if slot:
        return slot, f"subtitle {str(label)!r} matches a game team name"

    slot = teams.slot_for_prefix(text)
    if slot:
        return slot, f"title {text!r} begins with a game team name"

    if code and code in teams.code_to_slot:
        return teams.code_to_slot[code], f"ticker code {code!r} maps to a game team"

    custom = market.get("custom_strike")
    if isinstance(custom, dict):
        uuid = custom.get("football_team")
        if uuid and str(uuid) in teams.uuid_to_slot:
            return teams.uuid_to_slot[str(uuid)], f"custom_strike football_team {uuid!r} maps to a game team"

    return TeamSlot.UNRESOLVED.value, (
        f"no route resolved a team: suffix={suffix!r} subtitle={label!r} "
        f"custom_strike={custom!r}"
    )


def _period_of(market: dict[str, Any]) -> str:
    period = str(market.get("period") or "unknown")
    return period if period in PERIODS else period


def _comparator_and_threshold(market: dict[str, Any]) -> tuple[str | None, float | None, float | None]:
    floor_strike = market.get("floor_strike")
    cap_strike = market.get("cap_strike")
    strike_type = str(market.get("strike_type") or "").lower()
    floor_value = float(floor_strike) if isinstance(floor_strike, (int, float)) else None
    cap_value = float(cap_strike) if isinstance(cap_strike, (int, float)) else None

    if floor_value is not None and cap_value is not None:
        return "between", floor_value, cap_value
    if floor_value is not None:
        if strike_type in ("greater_or_equal", "greater_than_or_equal"):
            return "greater_or_equal", floor_value, None
        return "greater", floor_value, None
    if cap_value is not None:
        if strike_type in ("less_or_equal", "less_than_or_equal"):
            return "less_or_equal", cap_value, None
        return "less", cap_value, None
    return None, None, None


def _team_label(slot: str, teams: GameTeams) -> str:
    if slot == TeamSlot.HOME.value:
        return teams.home_name or "home"
    if slot == TeamSlot.AWAY.value:
        return teams.away_name or "away"
    return slot


def derive_semantics(market: dict[str, Any], teams: GameTeams) -> ContractSemantics:
    """One contract -> what YES means and what would price it.

    Deliberately total: every input returns a ContractSemantics. The
    difference between a family this module models structurally and one it
    has never seen is which `requires` value comes back, not whether an
    answer comes back at all.
    """
    family = str(market.get("family") or "unknown")
    period = _period_of(market)
    comparator, threshold, cap = _comparator_and_threshold(market)
    title = market.get("title") or market.get("yes_sub_title") or market.get("rules_primary")

    if not title:
        return ContractSemantics(
            kind=ContractKind.BINARY_EVENT.value,
            period=period,
            team=TeamSlot.NONE.value,
            comparator=comparator,
            threshold=threshold,
            cap=cap,
            requires=PricingRequirement.EXPLICIT_PROBABILITY.value,
            yes_meaning="",
            no_meaning="",
            status=SemanticsStatus.UNSTATEABLE.value,
            rationale="contract carries no title, subtitle or rules text: YES cannot be stated",
        )

    stated = str(title).strip()

    def _binary(rationale: str, team: str = TeamSlot.NONE.value) -> ContractSemantics:
        return ContractSemantics(
            kind=ContractKind.BINARY_EVENT.value,
            period=period,
            team=team,
            comparator=comparator,
            threshold=threshold,
            cap=cap,
            requires=PricingRequirement.EXPLICIT_PROBABILITY.value,
            yes_meaning=stated,
            no_meaning=f"NOT ({stated})",
            status=SemanticsStatus.RESOLVED.value,
            rationale=rationale,
        )

    # --- totals: no team, a threshold, and a period we can price --------
    if family in _TOTAL_FAMILIES:
        if threshold is None:
            return _binary(f"{family} carried no strike; cannot state a rung")
        if period not in _SCORE_PERIODS:
            return _binary(f"{family} has period {period!r}, which no score distribution expresses")
        return ContractSemantics(
            kind=ContractKind.TOTAL.value,
            period=period,
            team=TeamSlot.NONE.value,
            comparator=comparator or "greater",
            threshold=threshold,
            cap=cap,
            requires=PricingRequirement.PERIOD_DISTRIBUTION.value,
            yes_meaning=stated,
            no_meaning=f"combined {period} points do NOT clear {threshold}",
            status=SemanticsStatus.RESOLVED.value,
            rationale=f"{family} rung at {threshold} ({comparator})",
        )

    # --- everything below is team-scoped ---------------------------------
    slot, team_rationale = _resolve_team(market, teams)

    if family in _MONEYLINE_FAMILIES:
        if slot == TeamSlot.TIE.value:
            return ContractSemantics(
                kind=ContractKind.TIE.value,
                period=period,
                team=TeamSlot.TIE.value,
                comparator=None,
                threshold=None,
                cap=None,
                requires=PricingRequirement.EXPLICIT_PROBABILITY.value,
                yes_meaning=stated,
                no_meaning=f"NOT ({stated})",
                status=SemanticsStatus.RESOLVED.value,
                rationale=(
                    "an exact-tie point mass is not reliably derivable from a continuous margin "
                    "distribution (a normal approximation overstates a full-game tie by ~40x and "
                    "understates a 1Q tie by ~3x), so this rung prices only from an explicit "
                    "probability"
                ),
            )
        if slot in (TeamSlot.HOME.value, TeamSlot.AWAY.value) and period in _SCORE_PERIODS:
            return ContractSemantics(
                kind=ContractKind.MONEYLINE.value,
                period=period,
                team=slot,
                comparator="greater",
                threshold=None,
                cap=None,
                requires=PricingRequirement.PERIOD_DISTRIBUTION.value,
                yes_meaning=stated,
                no_meaning=f"{_team_label(slot, teams)} does NOT win the {period}",
                status=SemanticsStatus.RESOLVED.value,
                rationale=team_rationale,
            )
        if slot == TeamSlot.UNRESOLVED.value:
            return ContractSemantics(
                kind=ContractKind.BINARY_EVENT.value,
                period=period,
                team=slot,
                comparator=comparator,
                threshold=threshold,
                cap=cap,
                requires=PricingRequirement.EXPLICIT_PROBABILITY.value,
                yes_meaning=stated,
                no_meaning=f"NOT ({stated})",
                status=SemanticsStatus.UNRESOLVED_TEAM.value,
                rationale=team_rationale,
            )
        return _binary(f"{family} with period {period!r}", team=slot)

    if family in _SPREAD_FAMILIES:
        if slot not in (TeamSlot.HOME.value, TeamSlot.AWAY.value):
            status = (
                SemanticsStatus.UNRESOLVED_TEAM.value
                if slot == TeamSlot.UNRESOLVED.value
                else SemanticsStatus.RESOLVED.value
            )
            return ContractSemantics(
                kind=ContractKind.BINARY_EVENT.value,
                period=period,
                team=slot,
                comparator=comparator,
                threshold=threshold,
                cap=cap,
                requires=PricingRequirement.EXPLICIT_PROBABILITY.value,
                yes_meaning=stated,
                no_meaning=f"NOT ({stated})",
                status=status,
                rationale=team_rationale,
            )
        if threshold is None or period not in _SCORE_PERIODS:
            return _binary(f"{family} rung without a usable strike/period", team=slot)
        return ContractSemantics(
            kind=ContractKind.SPREAD.value,
            period=period,
            team=slot,
            comparator=comparator or "greater",
            threshold=threshold,
            cap=cap,
            requires=PricingRequirement.PERIOD_DISTRIBUTION.value,
            yes_meaning=stated,
            no_meaning=(
                f"{_team_label(slot, teams)} does NOT win the {period} by more than {threshold}"
            ),
            status=SemanticsStatus.RESOLVED.value,
            rationale=f"{team_rationale}; rung {threshold} ({comparator})",
        )

    if family in _TEAM_TOTAL_FAMILIES:
        if slot not in (TeamSlot.HOME.value, TeamSlot.AWAY.value):
            status = (
                SemanticsStatus.UNRESOLVED_TEAM.value
                if slot == TeamSlot.UNRESOLVED.value
                else SemanticsStatus.RESOLVED.value
            )
            return ContractSemantics(
                kind=ContractKind.BINARY_EVENT.value,
                period=period,
                team=slot,
                comparator=comparator,
                threshold=threshold,
                cap=cap,
                requires=PricingRequirement.EXPLICIT_PROBABILITY.value,
                yes_meaning=stated,
                no_meaning=f"NOT ({stated})",
                status=status,
                rationale=team_rationale,
            )
        if threshold is None or period not in _SCORE_PERIODS:
            return _binary(f"{family} rung without a usable strike/period", team=slot)
        return ContractSemantics(
            kind=ContractKind.TEAM_TOTAL.value,
            period=period,
            team=slot,
            comparator=comparator or "greater",
            threshold=threshold,
            cap=cap,
            requires=PricingRequirement.PERIOD_DISTRIBUTION.value,
            yes_meaning=stated,
            no_meaning=f"{_team_label(slot, teams)} does NOT clear {threshold} {period} points",
            status=SemanticsStatus.RESOLVED.value,
            rationale=f"{team_rationale}; rung {threshold} ({comparator})",
        )

    if family in _MARGIN_FAMILIES:
        if slot in (TeamSlot.HOME.value, TeamSlot.AWAY.value) and threshold is not None and period in _SCORE_PERIODS:
            return ContractSemantics(
                kind=ContractKind.MARGIN_BAND.value,
                period=period,
                team=slot,
                comparator=comparator or "greater",
                threshold=threshold,
                cap=cap,
                requires=PricingRequirement.PERIOD_DISTRIBUTION.value,
                yes_meaning=stated,
                no_meaning=f"NOT ({stated})",
                status=SemanticsStatus.RESOLVED.value,
                rationale=f"{team_rationale}; margin band [{threshold}, {cap}]",
            )
        return _binary("winning-margin contract without a resolvable team/band", team=slot)

    # --- every other family, named or not, keeps its place in the count --
    return _binary(
        f"family {family!r} is not derivable from a score distribution; prices from an explicit "
        f"probability keyed by ticker",
        team=slot if slot != TeamSlot.UNRESOLVED.value else TeamSlot.NONE.value,
    )
