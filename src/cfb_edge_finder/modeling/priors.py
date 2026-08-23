"""Early-season/preseason prior carryover (mission spec section 9).

Week 1 of a season has zero current-season evidence for `ratings.py`'s
regression to work with -- every team's in-season offense/defense rating
is exactly 0.0 (league average) at that point, which is obviously wrong
for a defending playoff team facing a rebuilding one. This module blends
each team's CURRENT-SEASON rating with its PRIOR-SEASON ending rating,
with the blend weight shifting from "mostly prior" to "mostly current"
as current-season games accumulate -- the same shrinkage FORM used inside
ratings.py for both the ridge fit and the pace estimate, applied here
across a season boundary instead of within one.

*** WHAT A TEAM WITH NO PRIOR-SEASON DATA GETS ***
A team new to the corpus (first FBS season, or the first season CFBD data
is available for it -- e.g. one of the four FCS-to-FBS transitional
programs from Milestone B's team registry) has no prior rating to blend
from. It gets the league-average prior (0.0), NOT a fabricated
below-average "expect them to struggle" adjustment -- inventing a
transition penalty without real evidence for its size would be exactly
the kind of unverified-but-authoritative-looking number this project
refuses to produce (see kalshi/executable_price.py's fee-rate discipline
for the same principle). This is a real, documented limitation: such
teams' early-season projections are best-effort and carry inflated
uncertainty (see qb_continuity.py's uncertainty machinery, which this
module's `games_played` output also feeds).
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SEASON_SHRINKAGE_K = 4.0
"""Games of current-season evidence at which a team's rating is weighted
50/50 against its prior-season carryover. Same order of magnitude as
DEFAULT_PACE_SHRINKAGE_K in ratings.py -- both represent "how many games
until we mostly trust this season's own evidence" and there is no
principled reason for them to differ; kept as two separate constants
(not one shared one) so they can be tuned independently once backtest
evidence justifies it. See docs/MILESTONE_C.md "Early-season priors"."""


@dataclass(frozen=True)
class BlendedRating:
    offense: float
    defense: float
    weight_on_current_season: float = 0.0
    """0.0 = pure prior-season carryover (or pure league-average if no
    prior-season rating exists either), 1.0 = pure current-season fit.
    Exposed directly because it's exactly what
    UncertaintyProfile.early_season_prior_weight (schemas/projection.py)
    needs -- see score_model.py."""


def season_carryover_weight(games_played_this_season: int, *, k: float = DEFAULT_SEASON_SHRINKAGE_K) -> float:
    if games_played_this_season < 0:
        raise ValueError(f"games_played_this_season must be >= 0, got {games_played_this_season}")
    return games_played_this_season / (games_played_this_season + k)


def blend_team_rating(
    *,
    current_offense: float,
    current_defense: float,
    prior_season_offense: float | None,
    prior_season_defense: float | None,
    games_played_this_season: int,
    k: float = DEFAULT_SEASON_SHRINKAGE_K,
) -> BlendedRating:
    """Blends one team's current-season fitted rating with its
    prior-season ending rating. `prior_season_offense`/`prior_season_defense`
    are None when the team has no prior-season data in the corpus at all
    (see module docstring) -- treated as a league-average (0.0) prior in
    that case, not silently skipped.
    """
    weight = season_carryover_weight(games_played_this_season, k=k)
    prior_off = prior_season_offense if prior_season_offense is not None else 0.0
    prior_def = prior_season_defense if prior_season_defense is not None else 0.0
    return BlendedRating(
        offense=weight * current_offense + (1 - weight) * prior_off,
        defense=weight * current_defense + (1 - weight) * prior_def,
        weight_on_current_season=weight,
    )
