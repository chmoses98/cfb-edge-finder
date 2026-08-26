"""Milestone D: one football-model projection per game, reused to price an
entire contract ladder cheaply.

*** WHY THIS EXISTS (mission section 17's scale architecture requirement) ***
The expensive part of pricing a Kalshi contract is the football model
itself: fitting ratings, building an out-of-sample residual pool, and
running a Monte Carlo simulation (`score_model.project_game`). Kalshi can
list 30+ distinct contracts for ONE game (a full spread ladder, a full
total ladder). Rerunning the football model once per CONTRACT (instead of
once per GAME) would make repricing 50-80 games x many contracts each
prohibitively expensive, and -- more importantly -- WRONG: two spread
contracts for the same game must be priced from the exact same underlying
distribution, or their probabilities would not even be mutually
consistent with each other.

This module is a thin, explicit cache keyed by (game_id, as_of, method
parameters): `GameProjectionCache.get_or_build()` runs the full C.2
pipeline (identical to `scripts/build_cfb_baseline.py`'s own call
sequence -- see that script for why each piece is reused verbatim, not
reimplemented) AT MOST ONCE per game, and returns the same
`CorrectedGameProjection` object for every subsequent contract in that
game's ladder. Repricing a market whose PRICE changed (but whose game
inputs did not) never touches this cache at all -- see
`kalshi/contract_semantics.py` + `projections.distribution.price_market`
for the cheap, closed-form repricing path that consumes this cache's
output.

*** RATINGS/RESIDUAL-POOL SHARING ACROSS GAMES (real fix, live-evidenced) ***
`fit_fbs_efficiency_ratings` and `build_expanding_residual_pool` depend
ONLY on `(history, as_of)` -- never on which specific game is being
priced. An earlier version of this cache re-ran both, a real ridge
regression over the full multi-season corpus, once per DISTINCT
game_id, even when many games shared the identical `as_of`. A live
snapshot capture that (after fixing game-mapping evidence extraction,
see `kalshi/contract_semantics.py`) started mapping ~300+ real games
against a single as_of exposed this as a genuine scale problem: the
capture ran for 15+ minutes and had to be cancelled rather than
completing. `_ratings_and_pool_for_as_of()` now caches the
(ratings, residual_pool) pair by `as_of` alone, so every game sharing a
week's as_of reuses one fit instead of re-deriving it -- the per-game
Monte Carlo simulation in `project_game` still runs once per game (that
part IS genuinely game-specific), only the shared upstream fitting is
deduplicated.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf
from cfb_edge_finder.modeling.margin_correction_artifact import (
    FROZEN_MARGIN_CORRECTION_PARAMS,
    MARGIN_CORRECTION_ARTIFACT_VERSION,
    MARGIN_CORRECTION_METHOD,
    MARGIN_CORRECTION_TRAINING_CUTOFF,
)
from cfb_edge_finder.modeling.ratings import DEFAULT_RIDGE_LAMBDA, RatingsSnapshot, fit_fbs_efficiency_ratings
from cfb_edge_finder.modeling.score_model import (
    DEFAULT_RESIDUAL_SCALE,
    CorrectedGameProjection,
    apply_margin_correction,
    build_expanding_residual_pool,
    project_game,
)


@dataclass(frozen=True)
class GameProjectionRequest:
    """Everything needed to build one game's projection -- the cache key.
    Two requests with equal fields are the SAME projection and must reuse
    the cached result; this dataclass is frozen+hashable specifically so
    it can be a dict key without a separate hand-rolled cache-key string."""

    game_id: str
    home_id: str
    away_id: str
    home_classification: str
    away_classification: str
    is_neutral_site: bool
    as_of_season: int
    as_of_week: int
    n_simulations: int
    seed: int


@dataclass(frozen=True)
class CachedGameProjection:
    request: GameProjectionRequest
    projection: CorrectedGameProjection
    is_fbs_vs_fbs: bool
    training_rows: int
    teams_with_data: int


class GameProjectionCache:
    """Not thread-safe (this codebase has no concurrent pricing path yet)
    -- a plain dict-backed cache, deliberately as simple as the job needs.
    `lines` (the full leakage-safe TeamGameLine corpus) is supplied once
    at construction, exactly mirroring how build_cfb_baseline.py's `lines`
    variable is fetched once and passed into every downstream call."""

    def __init__(self, lines: list[TeamGameLine]) -> None:
        self._lines = lines
        self._cache: dict[GameProjectionRequest, CachedGameProjection] = {}
        self._ratings_and_pool_cache: dict[AsOf, tuple[RatingsSnapshot, np.ndarray]] = {}

    def __len__(self) -> int:
        return len(self._cache)

    def _ratings_and_pool_for_as_of(self, as_of: AsOf) -> tuple[RatingsSnapshot, np.ndarray]:
        """`fit_fbs_efficiency_ratings`/`build_expanding_residual_pool`
        depend only on `(history, as_of)`, and `history` is itself
        entirely determined by `as_of` (via `self._lines`) -- so every
        game sharing an `as_of` shares one fit here, run at most once,
        regardless of how many distinct games are priced against it.
        See this module's docstring for the live run that made this a
        real (not hypothetical) fix rather than a premature one."""
        cached = self._ratings_and_pool_cache.get(as_of)
        if cached is not None:
            return cached
        history = [ln for ln in self._lines if ln.as_of.is_strictly_before(as_of)]
        if not history:
            raise ValueError(f"no leakage-safe history strictly before {as_of!r}")
        ratings = fit_fbs_efficiency_ratings(history, as_of, ridge_lambda=DEFAULT_RIDGE_LAMBDA)
        residual_pool = build_expanding_residual_pool(history, as_of)
        self._ratings_and_pool_cache[as_of] = (ratings, residual_pool)
        return ratings, residual_pool

    def get_or_build(self, request: GameProjectionRequest) -> CachedGameProjection:
        cached = self._cache.get(request)
        if cached is not None:
            return cached

        as_of = AsOf(season=request.as_of_season, week=request.as_of_week)
        ratings, residual_pool = self._ratings_and_pool_for_as_of(as_of)

        raw_projection = project_game(
            home_id=request.home_id,
            away_id=request.away_id,
            home_classification=request.home_classification,
            away_classification=request.away_classification,
            is_neutral_site=request.is_neutral_site,
            ratings=ratings,
            prior_season_ratings=None,
            residual_pool=residual_pool,
            home_percent_passing_ppa=None,
            away_percent_passing_ppa=None,
            n_simulations=request.n_simulations,
            seed=request.seed,
            residual_scale=DEFAULT_RESIDUAL_SCALE,
        )

        is_fbs_vs_fbs = request.home_classification == "fbs" and request.away_classification == "fbs"
        corrected = apply_margin_correction(
            raw_projection,
            is_fbs_vs_fbs=is_fbs_vs_fbs,
            method=MARGIN_CORRECTION_METHOD,
            correction_model=FROZEN_MARGIN_CORRECTION_PARAMS,
            artifact_version=MARGIN_CORRECTION_ARTIFACT_VERSION,
            as_of=as_of,
            training_cutoff=MARGIN_CORRECTION_TRAINING_CUTOFF,
        )

        result = CachedGameProjection(
            request=request,
            projection=corrected,
            is_fbs_vs_fbs=is_fbs_vs_fbs,
            training_rows=ratings.n_training_rows,
            teams_with_data=ratings.n_teams_with_data,
        )
        self._cache[request] = result
        return result
