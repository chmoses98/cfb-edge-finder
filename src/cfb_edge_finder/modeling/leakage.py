"""The leakage contract -- what a pregame prediction is allowed to know.

*** THIS IS THE MOST IMPORTANT FILE IN MILESTONE C ***
See docs/MILESTONE_C.md's "Leakage policy" section for the full narrative
version of this contract. This module is its enforceable, testable form.

The contract, precisely: a prediction for game X, made "as of" some
`AsOf` timestamp, may only use:

  1. Games that KICKED OFF strictly before that AsOf timestamp (not games
     merely scheduled before it -- a game's own outcome/statistics do not
     exist as data until it has actually been played).
  2. Preseason/season-level information that was genuinely published
     before the season started (e.g. prior-season final ratings, a
     recruiting-talent composite computed from an offseason recruiting
     cycle) -- never a same-season aggregate that could include games
     after AsOf.
  3. Never: game X's own result, in-progress state, or postgame stats.
     Never: any later game. Never a "season total" field that CFBD (or
     any source) only finalizes once the season is over, if that field
     is being used to predict a game from earlier in that same season.

`AsOf` is deliberately WEEK-granular, not per-game: all games within one
week of one season are treated as simultaneous for rating-fitting purposes
(this is standard practice -- see docs/MILESTONE_C.md "Backtest
methodology" for why per-game refitting is both unnecessary and
expensive). This means two games in the same week can never leak into
each other's predictions (neither has "happened yet" relative to the
other from the model's perspective at fit time), but a team's rating used
to predict its week-5 game is fit from strictly weeks 0-4 (and prior
seasons), never week 5 itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfb_edge_finder.schemas.common import CFPRound, SeasonType


class LeakageError(ValueError):
    """Raised when a game's own data would be used to help predict itself,
    or when any row purporting to inform a prediction is not strictly
    prior to the AsOf point. This must never be silently allowed.
    """


@dataclass(frozen=True, order=True)
class AsOf:
    """The chronological cutoff a prediction is allowed to see. Comparable
    and orderable (season, week) -- deliberately NOT a raw datetime,
    because "week" is the actual unit games are batched by for rating
    fits (see module docstring). Postseason games are assigned
    week=REGULAR_SEASON_WEEK_CEILING + 1 (conference championships) or
    higher (bowls, then CFP rounds in bracket order) so postseason AsOf
    values sort strictly after every regular-season week of that season --
    see `postseason_week_rank()`.
    """

    season: int
    week: int

    def is_strictly_before(self, other: AsOf) -> bool:
        return (self.season, self.week) < (other.season, other.week)


# Regular-season weeks run 0-15 in practice (week 0 exists for the
# occasional Week 0 slate). Postseason phases get synthetic week numbers
# strictly above this ceiling so they always sort after every regular
# week of the same season, and in a defensible bracket order relative to
# each other (conference championships -> bowls -> CFP rounds, in bracket
# order). Reuses the SAME SeasonType/CFPRound vocabulary Milestone B
# already built and tested (week_labels.py) rather than inventing a
# parallel one.
REGULAR_SEASON_WEEK_CEILING = 15

_CFP_ROUND_OFFSET = {
    CFPRound.FIRST_ROUND: 0,
    CFPRound.QUARTERFINAL: 1,
    CFPRound.SEMIFINAL: 2,
    CFPRound.NATIONAL_CHAMPIONSHIP: 3,
}


def postseason_week_rank(season_type: SeasonType, cfp_round: CFPRound | None = None) -> int:
    """Maps a postseason SeasonType (+ CFPRound, when season_type is CFP)
    to a synthetic week number strictly greater than
    REGULAR_SEASON_WEEK_CEILING. Raises on anything unrecognized rather
    than silently guessing a sort position -- an unranked postseason phase
    sorting *before* a regular-season game would be a real leakage bug
    (or vice versa), not a cosmetic ordering issue.
    """
    if season_type == SeasonType.CONFERENCE_CHAMPIONSHIP:
        return REGULAR_SEASON_WEEK_CEILING + 1
    if season_type == SeasonType.BOWL:
        return REGULAR_SEASON_WEEK_CEILING + 2
    if season_type == SeasonType.CFP:
        if cfp_round not in _CFP_ROUND_OFFSET:
            raise LeakageError(
                f"CFP season_type requires a recognized cfp_round to compute its "
                f"postseason_week_rank, got {cfp_round!r}"
            )
        return REGULAR_SEASON_WEEK_CEILING + 3 + _CFP_ROUND_OFFSET[cfp_round]
    raise LeakageError(
        f"{season_type!r} has no defined postseason_week_rank -- add one rather than "
        f"guessing a sort position that could silently misorder the leakage cutoff"
    )


def assert_strictly_before(row_as_of: AsOf, cutoff: AsOf, *, context: str) -> None:
    """The single enforcement point every feature-construction function in
    this package must call before using a historical row. Raises
    LeakageError (never returns False for the caller to maybe-ignore) if
    `row_as_of` is not strictly before `cutoff`.
    """
    if not row_as_of.is_strictly_before(cutoff):
        raise LeakageError(
            f"{context}: row at {row_as_of!r} is not strictly before cutoff {cutoff!r} -- "
            f"using it would leak same-or-future information into this prediction"
        )
