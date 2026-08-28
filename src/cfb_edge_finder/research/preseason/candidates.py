"""Candidate preseason priors, as post-hoc margin adjustments to CONTROL.

*** WHY AN ADJUSTMENT RATHER THAN A MODIFIED MODEL ***

A candidate is expressed as

    candidate_margin = control_margin + beta * (home_feature - away_feature)

and NOT by editing `project_game`. Three reasons, in order of
importance:

1. Production is untouched. The control path runs exactly as it does in
   production, and the candidate is arithmetic applied afterwards.
2. The comparison is genuinely paired. Both arms share one ratings fit,
   one residual pool and one seed, so the ONLY difference is the
   candidate term. Refitting the model per candidate would let fit noise
   masquerade as candidate effect.
3. `beta` is one interpretable number per candidate. Part 15 asks for a
   simple, interpretable prior rather than an overfit preseason
   simulator, and a single slope is about as interpretable as it gets.

*** BETA IS FIT ON DEVELOPMENT SEASONS ONLY ***

`fit_beta` refuses any season outside the declared development set. The
fitted value is then applied UNCHANGED to selection and confirmation. A
beta refit on the season it is evaluated on would be measuring in-sample
fit, which is the one thing this research is not interested in.

*** THE WINNER PROBABILITY SHIFTS WITH THE MARGIN ***

Shifting the simulated margin distribution by a constant and re-reading
P(margin > 0) keeps winner and margin consistent with one another. Moving
the margin while leaving the probability alone would produce an arm that
contradicts itself.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

import numpy as np

from cfb_edge_finder.modeling.leakage import AsOf
from cfb_edge_finder.research.preseason.corpus import HistoricalGame
from cfb_edge_finder.research.preseason.evaluation import GamePrediction
from cfb_edge_finder.research.preseason.features import FeatureTable


@dataclass(frozen=True)
class CandidateSpec:
    """One declared candidate. Simple by construction."""

    name: str
    feature_name: str
    description: str

    def differential(
        self, game: HistoricalGame, table: FeatureTable, target: AsOf
    ) -> float | None:
        """home feature minus away feature, or None if either is missing.

        Missing is NOT imputed to zero: a zero differential asserts the
        two teams are equal on this feature, which is a claim, not an
        absence."""
        home = table.get(game.home_team, self.feature_name, target=target)
        away = table.get(game.away_team, self.feature_name, target=target)
        if home is None or away is None:
            return None
        if home.value is None or away.value is None:
            return None
        return float(home.value) - float(away.value)


CANDIDATES = (
    CandidateSpec(
        "returning_production_total",
        "returning_percentPPA",
        "Overall returning production share (Candidate A). The control uses only the "
        "passing split, and only for uncertainty.",
    ),
    CandidateSpec(
        "qb_continuity_passing",
        "returning_percentPassingPPA",
        "Returning passing production (Candidate B). The strongest leakage-safe QB "
        "continuity proxy available; QB identity is not historically reconstructable.",
    ),
    CandidateSpec(
        "returning_rushing",
        "returning_percentRushingPPA",
        "Returning rushing production (Candidate A component).",
    ),
    CandidateSpec(
        "returning_receiving",
        "returning_percentReceivingPPA",
        "Returning receiving production (Candidate A component).",
    ),
    CandidateSpec(
        "talent_composite",
        "talent_composite",
        "Recruiting talent composite (Candidate C).",
    ),
    CandidateSpec(
        "coaching_change",
        "head_coach_changed",
        "New head coach indicator (Candidate D). Tested without assuming a sign.",
    ),
)


class DevelopmentOnlyError(RuntimeError):
    """A beta fit was attempted outside the development seasons."""


@dataclass(frozen=True)
class FittedCandidate:
    spec: CandidateSpec
    beta: float
    n_games: int
    development_seasons: tuple[int, ...]
    mean_abs_differential: float

    @property
    def implied_points_per_unit(self) -> float:
        return self.beta


def fit_beta(
    spec: CandidateSpec,
    rows: list[tuple[GamePrediction, float]],
    *,
    development_seasons: tuple[int, ...],
) -> FittedCandidate | None:
    """Least-squares slope of (actual - control) on the feature
    differential, over DEVELOPMENT games only.

    No intercept: the control already carries the overall level, and an
    intercept here would silently re-fit the model's global bias under
    the guise of a preseason feature."""
    offenders = {p.season for p, _ in rows} - set(development_seasons)
    if offenders:
        raise DevelopmentOnlyError(
            f"beta for {spec.name} would be fit on non-development season(s) {sorted(offenders)}; "
            f"a beta fit on the data it is evaluated on measures in-sample fit"
        )
    usable = [(p, d) for p, d in rows if d is not None]
    if len(usable) < 30:
        return None
    x = np.array([d for _, d in usable], dtype=float)
    y = np.array([p.actual_home_margin - p.projected_margin for p, _ in usable], dtype=float)
    denom = float(np.dot(x, x))
    if denom <= 1e-9:
        return None
    beta = float(np.dot(x, y) / denom)
    return FittedCandidate(
        spec=spec,
        beta=beta,
        n_games=len(usable),
        development_seasons=tuple(sorted(development_seasons)),
        mean_abs_differential=statistics.fmean(abs(d) for _, d in usable),
    )


def apply_candidate(
    prediction: GamePrediction,
    differential: float | None,
    fitted: FittedCandidate,
    margin_samples: np.ndarray,
) -> GamePrediction:
    """Shift the control's margin by beta * differential.

    `margin_samples` is the control's own simulated margin distribution;
    shifting it and re-reading P(margin > 0) keeps the winner probability
    consistent with the shifted margin, rather than leaving an arm that
    contradicts itself. A zero margin resolves to AWAY, matching
    settlement."""
    if differential is None:
        return prediction
    delta = fitted.beta * differential
    shifted = margin_samples + delta
    return GamePrediction(
        game_id=prediction.game_id,
        season=prediction.season,
        week=prediction.week,
        home_win_probability=float(np.mean(shifted > 0)),
        projected_margin=prediction.projected_margin + delta,
        projected_total=prediction.projected_total,
        actual_home_margin=prediction.actual_home_margin,
        actual_total=prediction.actual_total,
        is_neutral_site=prediction.is_neutral_site,
        both_fbs=prediction.both_fbs,
    )
