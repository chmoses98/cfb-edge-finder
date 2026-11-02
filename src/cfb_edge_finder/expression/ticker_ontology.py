"""Decode a Kalshi CFB ticker into (family, period, game, team, threshold), or say it cannot.

Ledger rows carry only the ticker. Reporting and exposure need the structure, and one place
must own the decoding so a spread can never be bucketed as a team total in one report and a
spread in another.

Cross-category contracts (``KXMVECROSSCATEGORY-*``) are multi-leg combos whose legs are NOT
recoverable from the ticker. They decode to ``COMBO_UNDECODED`` -- reported separately and never
attributed to a single game or market family, because guessing a leg would invent exposure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Family(StrEnum):
    MONEYLINE = "MONEYLINE"
    SPREAD = "SPREAD"
    TOTAL = "TOTAL"
    TEAM_TOTAL = "TEAM_TOTAL"
    COMBO_UNDECODED = "COMBO_UNDECODED"
    UNKNOWN = "UNKNOWN"


class Period(StrEnum):
    GAME = "GAME"
    FIRST_QUARTER = "1Q"
    FIRST_HALF = "1H"


_SERIES = {
    "KXNCAAFGAME": (Family.MONEYLINE, Period.GAME),
    "KXNCAAFSPREAD": (Family.SPREAD, Period.GAME),
    "KXNCAAFTOTAL": (Family.TOTAL, Period.GAME),
    "KXNCAAFTEAMTOTAL": (Family.TEAM_TOTAL, Period.GAME),
    "KXNCAAF1QSPREAD": (Family.SPREAD, Period.FIRST_QUARTER),
    "KXNCAAF1QTOTAL": (Family.TOTAL, Period.FIRST_QUARTER),
    "KXNCAAF1HSPREAD": (Family.SPREAD, Period.FIRST_HALF),
    "KXNCAAF1HTOTAL": (Family.TOTAL, Period.FIRST_HALF),
    "KXNCAAF1H": (Family.MONEYLINE, Period.FIRST_HALF),
}
_COMBO_PREFIX = "KXMVECROSSCATEGORY"
_GAME_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z0-9]+)$")
_TEAM_THR_RE = re.compile(r"^([A-Z]+?)(\d+(?:\.\d+)?)$")


@dataclass(frozen=True)
class TickerInfo:
    ticker: str
    series: str
    family: Family
    period: Period | None
    game_key: str | None  # e.g. 26OCT22JMUAPP
    game_date: str | None  # 2026-10-22 (the ticker's event date, not necessarily kickoff date)
    team: str | None
    opponent: str | None  # derived from the game key when the team token is a prefix/suffix of it
    threshold: float | None
    decoded: bool

    @property
    def strike(self) -> float | None:
        """Kalshi's ``floor_strike`` for the 'greater' contracts: the ticker integer N means 'over N-0.5', so
        YES pays when the value is >= N. Verified against live catalog quotes (e.g. ...-BALL10 -> 9.5)."""
        return None if self.threshold is None else self.threshold - 0.5


_MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}


def parse_ticker(ticker: str) -> TickerInfo:
    parts = ticker.split("-")
    series = parts[0]
    if series.startswith(_COMBO_PREFIX):
        return TickerInfo(ticker, series, Family.COMBO_UNDECODED, None, None, None, None, None, None, False)
    fam_period = _SERIES.get(series)
    if fam_period is None or len(parts) < 3:
        return TickerInfo(ticker, series, Family.UNKNOWN, None, None, None, None, None, None, False)
    family, period = fam_period
    game_key = parts[1]
    m = _GAME_RE.match(game_key)
    date = None
    teams = None
    if m and m.group(2) in _MONTHS:
        date = f"20{m.group(1)}-{_MONTHS[m.group(2)]:02d}-{m.group(3)}"
        teams = m.group(4)
    tail = parts[2]
    team: str | None = None
    threshold: float | None = None
    if family is Family.TOTAL:
        try:
            threshold = float(tail)
        except ValueError:
            return TickerInfo(ticker, series, family, period, game_key, date, None, None, None, False)
    elif family is Family.MONEYLINE:
        team = tail
    else:
        tm = _TEAM_THR_RE.match(tail)
        if tm is None:
            return TickerInfo(ticker, series, family, period, game_key, date, None, None, None, False)
        team, threshold = tm.group(1), float(tm.group(2))
    opponent = None
    if team and teams:
        if teams.startswith(team) and len(teams) > len(team):
            opponent = teams[len(team) :]
        elif teams.endswith(team) and len(teams) > len(team):
            opponent = teams[: -len(team)]
    decoded = date is not None and (family is Family.TOTAL or (team is not None and opponent is not None))
    return TickerInfo(ticker, series, family, period, game_key, date, team, opponent, threshold, decoded)
