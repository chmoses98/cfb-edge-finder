"""Owner rules for RUN CFB (2026-11-02): a per-bet size ceiling, an Auburn exclusion, and a havoc ban.

*** WHAT THESE ARE ***
Three explicit, visible, RESTRICTION-ONLY rules the owner set. None of them ever adds, re-ranks or
edits a candidate; each can only mark a candidate not actionable or lower a proposed stake, and each
says so by name in the artifact. Nothing here is hidden inside a probability or a rating.

1. ``MAX_BET_UNITS`` -- no single bet above 5U (1U = $75, so $375). A hard clamp on the *proposed*
   stake, with the reason disclosed (``PER_BET_CAP_5U_APPLIED``). It is a per-bet ceiling, not a
   game or day cap: combined exposure above 5U/game or 15U/day stays a FLAG (exposure_aggregation).

2. ``OWNER_EXCLUSION_AUBURN`` -- never recommend betting against Auburn. A position is "against
   Auburn" when its payoff needs Auburn to lose or fail to cover:
     * moneyline (full game or 1H): Auburn NO, or the opponent YES;
     * spread (full game, 1H, 1Q): Auburn NO (does not cover), or the opponent YES (covers);
     * team total: Auburn NO (the Under on Auburn's own score) -- an INTERPRETATION, see below.
   Not affected: game totals, 1Q/1H totals, the opponent's team total, Auburn YES, opponent NO,
   and any market in a game Auburn is not part of. Combos (``KXMVECROSSCATEGORY``) have no
   recoverable legs, so they cannot be attributed to a team at all; they are not touched here
   and stay ``COMBO_UNDECODED`` (reported separately by the ontology).
   FAIL CLOSED: if the game involves Auburn but the direction of the position cannot be
   determined, the candidate is blocked ``OWNER_EXCLUSION_AUBURN_UNDETERMINED``.
   The rule does not change any probability. It is an owner preference applied after pricing,
   shown on every candidate it touches.

3. ``HAVOC_FORBIDDEN_TOKEN`` -- a havoc metric is never a handicapping input. ``assert_no_havoc``
   scans feature/input names and raises; a test runs it over every model-input field in the repo.

*** AN INTERPRETATION TO CONFIRM ***
"Betting against Auburn" is read as: a position that wins when Auburn loses, fails to cover, or
scores under its own team total. If the owner means only win/cover markets, delete the TEAM_TOTAL
branch in ``auburn_exclusion`` (one clause) and its test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from cfb_edge_finder.expression.ticker_ontology import Family, parse_ticker

STANDARD_UNIT_DOLLARS = 75.0
MAX_BET_UNITS = 5.0
MAX_BET_DOLLARS = MAX_BET_UNITS * STANDARD_UNIT_DOLLARS  # 375.0 at the standard unit

OWNER_EXCLUSION_AUBURN = "OWNER_EXCLUSION_AUBURN"
OWNER_EXCLUSION_AUBURN_UNDETERMINED = "OWNER_EXCLUSION_AUBURN_UNDETERMINED"
PER_BET_CAP_APPLIED = "PER_BET_CAP_5U_APPLIED"

#: The Kalshi team token for Auburn, as it appears in game keys such as ``26OCT17AUBUGA``. Matching is
#: exact on the token, never a substring: ``AUB`` inside some other six-letter key is not Auburn.
AUBURN_TICKER_TOKEN = "AUB"
_AUBURN_NAME = re.compile(r"\bauburn\b", re.IGNORECASE)
# "Auburn Montgomery" and similar are not the SEC school. FBS slates never contain them; the guard
# exists so a stray FCS/D2 title cannot trigger the exclusion by accident.
_AUBURN_OTHER = re.compile(r"\bauburn\s+(montgomery|university\s+at\s+montgomery)\b", re.IGNORECASE)

HAVOC_PATTERN = re.compile(r"havoc", re.IGNORECASE)


@dataclass(frozen=True)
class OwnerVerdict:
    blocked: bool
    reason: str | None
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"blocked": self.blocked, "reason": self.reason, "detail": self.detail}


def _title_has_auburn(title: str | None) -> bool:
    if not title or _AUBURN_OTHER.search(title):
        return False
    return bool(_AUBURN_NAME.search(title))


def _title_sides(title: str | None) -> tuple[str | None, str | None]:
    """``("Away", "Home")`` from "Away at Home" / "Away vs Home" / "Away @ Home"; ``(None, None)`` if unclear."""
    if not title:
        return None, None
    parts = re.split(r"\s+(?:at|vs\.?|@)\s+", title.strip(), maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return None, None
    return parts[0].strip(), parts[1].strip()


def auburn_exclusion(ticker: str, side: str, *, game_title: str | None = None) -> OwnerVerdict:
    """Is buying ``side`` of ``ticker`` a position against Auburn?

    ``side`` is the side BOUGHT (``YES``/``NO``, any case). ``game_title`` ("Arkansas at Auburn") is
    optional corroboration; it can only make the verdict stricter (it detects an Auburn game whose
    ticker could not be attributed), never looser.
    """
    bought = str(side).strip().upper()
    info = parse_ticker(ticker)
    title_auburn = _title_has_auburn(game_title)

    if not info.decoded or info.family in (Family.COMBO_UNDECODED, Family.UNKNOWN):
        if title_auburn:
            return OwnerVerdict(True, OWNER_EXCLUSION_AUBURN_UNDETERMINED,
                                "Auburn game, contract not decodable: direction cannot be shown not to be against Auburn")
        return OwnerVerdict(False, None, "not an attributable single-game contract")

    if info.family is Family.TOTAL:
        return OwnerVerdict(False, None, "game/period total: no team direction")

    team, opponent = info.team, info.opponent
    auburn_is_team = team == AUBURN_TICKER_TOKEN
    auburn_is_opponent = opponent == AUBURN_TICKER_TOKEN
    game_has_auburn = auburn_is_team or auburn_is_opponent or title_auburn

    if not game_has_auburn:
        return OwnerVerdict(False, None, "Auburn is not in this game")
    if bought not in ("YES", "NO"):
        return OwnerVerdict(True, OWNER_EXCLUSION_AUBURN_UNDETERMINED, f"side {side!r} is not YES/NO in an Auburn game")

    if not (auburn_is_team or auburn_is_opponent):
        # Auburn is in the game (by title) but the ticker's team token is neither AUB nor derivable
        # as AUB's opponent: cannot tell which team the contract names.
        return OwnerVerdict(True, OWNER_EXCLUSION_AUBURN_UNDETERMINED,
                            "Auburn game by title, but the contract's team token is not attributable to Auburn or its opponent")

    # From here the contract names Auburn (as `team`) or Auburn's opponent (as `team`).
    if info.family in (Family.MONEYLINE, Family.SPREAD):
        against = (auburn_is_team and bought == "NO") or (auburn_is_opponent and bought == "YES")
        if against:
            return OwnerVerdict(True, OWNER_EXCLUSION_AUBURN,
                                f"{info.family.value}: {bought} on {team} requires Auburn to lose or fail to cover")
        return OwnerVerdict(False, None, f"{info.family.value}: {bought} on {team} is not against Auburn")

    if info.family is Family.TEAM_TOTAL:
        # Auburn's own team total, Under (NO on "scores at least N"): a bet on Auburn underperforming.
        if auburn_is_team and bought == "NO":
            return OwnerVerdict(True, OWNER_EXCLUSION_AUBURN, "TEAM_TOTAL: Under on Auburn's own score")
        return OwnerVerdict(False, None, "TEAM_TOTAL: not an Under on Auburn's own score")

    return OwnerVerdict(True, OWNER_EXCLUSION_AUBURN_UNDETERMINED, f"unhandled family {info.family.value} in an Auburn game")


@dataclass(frozen=True)
class StakeClamp:
    proposed_dollars: float | None
    effective_dollars: float | None
    clamped: bool
    max_dollars: float
    reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "max_single_bet_units": MAX_BET_UNITS,
            "max_single_bet_dollars": self.max_dollars,
            "proposed_dollars": self.proposed_dollars,
            "effective_dollars": self.effective_dollars,
            "clamped": self.clamped,
            "reason": self.reason,
            "scope": "PER_BET_CEILING_ONLY: game (>5U) and day (>15U) exposure remain flags, never caps",
        }


def clamp_stake(proposed_dollars: float | None, *, unit_dollars: float = STANDARD_UNIT_DOLLARS) -> StakeClamp:
    """Hard ceiling of 5U on ONE bet. A missing proposal stays missing (it is never assumed)."""
    ceiling = MAX_BET_UNITS * unit_dollars
    if proposed_dollars is None:
        return StakeClamp(None, None, False, ceiling, None)
    if proposed_dollars < 0:
        raise ValueError("a stake cannot be negative")
    if proposed_dollars > ceiling + 1e-9:
        return StakeClamp(proposed_dollars, ceiling, True, ceiling, PER_BET_CAP_APPLIED)
    return StakeClamp(proposed_dollars, proposed_dollars, False, ceiling, None)


def assert_no_havoc(names: list[str] | tuple[str, ...], *, where: str = "model inputs") -> None:
    """Raise if any feature/input name contains 'havoc' (case-insensitive). Havoc is never a handicapping input."""
    bad = sorted({n for n in names if HAVOC_PATTERN.search(str(n))})
    if bad:
        raise ValueError(f"havoc is forbidden as a handicapping input; found in {where}: {bad}")


__all__ = [
    "AUBURN_TICKER_TOKEN",
    "MAX_BET_DOLLARS",
    "MAX_BET_UNITS",
    "OWNER_EXCLUSION_AUBURN",
    "OWNER_EXCLUSION_AUBURN_UNDETERMINED",
    "OwnerVerdict",
    "PER_BET_CAP_APPLIED",
    "StakeClamp",
    "assert_no_havoc",
    "auburn_exclusion",
    "clamp_stake",
]
