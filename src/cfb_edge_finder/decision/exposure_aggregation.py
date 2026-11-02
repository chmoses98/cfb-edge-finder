"""Aggregate game-level and daily exposure, in units, across correlated expressions of one thesis.

*** FLAGS, NOT CAPS ***
Crossing a threshold produces a flag a human reads. Nothing here removes, shrinks or refuses a
position -- the owner's standing policy is "no arbitrary flat slate caps" -- and no correlation
coefficient is invented (see ``decision/portfolio.py`` for why). What this adds over
``portfolio.py`` is the missing arithmetic: how many UNITS sit behind one game thesis, expressed
through spreads, alternates, 1Q/1H spreads, moneylines, totals and team totals together.

Three numbers per game, each honest about what it is:

* ``gross_units``       every position in the game (hedges counted as risk, not netted)
* ``cluster_units``     units behind each *exact* directional thesis: ``margin:<team>`` (ML, spread,
                        alt-spread, 1Q/1H spread leaning to that team; a NO leans to the opponent),
                        ``total:OVER|UNDER`` (any period), ``teamscore:<team>:HIGH|LOW``
* ``view_units``        conservative union views, where a team total shares a football story with a
                        margin or total position ('X dominates' = margin:X + teamscore:X:HIGH +
                        teamscore:opp:LOW; 'shootout' = total:OVER + both teamscore HIGH; 'slog' the
                        mirror). Membership is by direction only -- NOT a statistical dependence claim.

Cross-category combos cannot be assigned to a game; they are reported separately, never silently
dropped and never guessed into a game.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from cfb_edge_finder.expression.ticker_ontology import Family, parse_ticker

#: Owner-adjustable policy defaults (flags only).
GAME_FLAG_UNITS = 5.0
DAY_FLAG_UNITS = 15.0


@dataclass(frozen=True)
class Position:
    ticker: str
    side: str  # "YES" | "NO"
    stake_dollars: float
    placed_date: str | None = None  # used only for combos, which have no ticker date


@dataclass
class GameExposure:
    game_key: str
    game_date: str | None
    gross_units: float = 0.0
    cluster_units: dict[str, float] = field(default_factory=dict)
    view_units: dict[str, float] = field(default_factory=dict)
    positions: int = 0
    flags: list[str] = field(default_factory=list)

    @property
    def max_view_units(self) -> float:
        return max(self.view_units.values(), default=0.0)


@dataclass
class ExposureReport:
    unit_dollars: float
    games: dict[str, GameExposure]
    daily_gross_units: dict[str, float]
    undecoded_combo_units: float
    undecoded_combo_units_by_day: dict[str, float]
    undecodable_units: float  # non-combo tickers the ontology could not decode
    flags: list[str]


def _cluster(info, side: str) -> tuple[str, list[str]] | None:
    """(primary cluster tag, union views it belongs to)."""
    yes = side.upper() == "YES"
    if info.family in (Family.MONEYLINE, Family.SPREAD):
        leaned = info.team if yes else info.opponent
        return f"margin:{leaned}", [f"dominates:{leaned}"] if leaned else []
    if info.family is Family.TOTAL:
        d = "OVER" if yes else "UNDER"
        return f"total:{d}", ["shootout" if yes else "slog"]
    if info.family is Family.TEAM_TOTAL:
        d = "HIGH" if yes else "LOW"
        views = []
        if yes:
            views = [f"dominates:{info.team}", "shootout"]
        else:
            views = [f"dominates:{info.opponent}", "slog"]
        return f"teamscore:{info.team}:{d}", views
    return None


def aggregate_exposure(
    positions: list[Position],
    *,
    unit_dollars: float,
    game_flag_units: float = GAME_FLAG_UNITS,
    day_flag_units: float = DAY_FLAG_UNITS,
) -> ExposureReport:
    if not unit_dollars or unit_dollars <= 0:
        raise ValueError("unit_dollars must be a positive, explicitly supplied value; no default unit exists")
    games: dict[str, GameExposure] = {}
    daily: dict[str, float] = defaultdict(float)
    combo = 0.0
    combo_by_day: dict[str, float] = defaultdict(float)
    undecodable = 0.0
    for pos in positions:
        units = pos.stake_dollars / unit_dollars
        info = parse_ticker(pos.ticker)
        if info.family is Family.COMBO_UNDECODED:
            combo += units
            combo_by_day[pos.placed_date or "UNKNOWN_DAY"] += units
            continue
        if not info.decoded or info.game_key is None:
            undecodable += units
            continue
        g = games.setdefault(info.game_key, GameExposure(info.game_key, info.game_date))
        g.gross_units += units
        g.positions += 1
        daily[info.game_date or "UNKNOWN_DAY"] += units
        tagged = _cluster(info, pos.side)
        if tagged:
            primary, views = tagged
            g.cluster_units[primary] = g.cluster_units.get(primary, 0.0) + units
            for v in views:
                g.view_units[v] = g.view_units.get(v, 0.0) + units
    flags: list[str] = []
    for key, g in sorted(games.items()):
        if g.gross_units > game_flag_units:
            g.flags.append(f"GAME_GROSS_OVER_{game_flag_units:g}U")
        if g.max_view_units > game_flag_units:
            g.flags.append(f"CORRELATED_VIEW_OVER_{game_flag_units:g}U")
        flags += [f"{key}:{f}" for f in g.flags]
    for day, u in sorted(daily.items()):
        if u > day_flag_units:
            flags.append(f"{day}:DAY_GROSS_OVER_{day_flag_units:g}U")
    if combo:
        flags.append("UNDECODED_COMBO_EXPOSURE_PRESENT")
    if undecodable:
        flags.append("UNDECODABLE_TICKER_EXPOSURE_PRESENT")
    return ExposureReport(
        unit_dollars=unit_dollars,
        games=games,
        daily_gross_units=dict(daily),
        undecoded_combo_units=combo,
        undecoded_combo_units_by_day=dict(combo_by_day),
        undecodable_units=undecodable,
        flags=flags,
    )
