"""Opponent-adjusted offense/defense ratings, pace, and home-field advantage.

*** METHOD, AND WHY (mission spec sections 6-9) ***

Team strength is fit as a ridge-regularized simultaneous least-squares
regression of POINTS-PER-PLAY (not raw points -- see "pace" below) on:
  points_per_play(team, game) ~= mu + offense[team] - defense[opponent] + hfa * home_indicator

This is the standard additive "Massey/SRS-style" opponent-adjustment
method, applied to a rate (points/play) instead of raw points specifically
so pace (plays run) and efficiency are estimated independently and later
recombined multiplicatively (expected_points = efficiency * expected_plays)
-- fitting directly on raw points would let a fast, mediocre team look
"efficient" purely because it ran more plays, double-counting pace inside
what should be a pure efficiency signal (mission section 7's explicit
warning).

Ridge (L2) regularization on the offense/defense parameters -- NOT on the
intercept or HFA term -- is used instead of plain least squares because
early-season/thin-schedule teams would otherwise get wildly unstable,
overfit ratings from a handful of games; ridge shrinks every team's rating
toward the league-average (0.0) in proportion to how little evidence
exists for it, which is exactly the "early-season uncertainty" behavior
mission section 9 asks for, applied at the rating-fit level (see
`priors.py` for the SEPARATE season-to-season carryover shrinkage on top
of this).

*** WHY FBS-VS-FCS OPPONENTS ARE NOT INDIVIDUALLY RATED ***
Mission section 4 explicitly warns against blindly mixing FBS-vs-FCS into
the main calibration population. Rather than giving each of the ~100+
FCS programs that occasionally appear on an FBS schedule their own
offense/defense parameter (mostly from a single observation -- far too
noisy to be meaningful, and it would roughly double the parameter count
for no real gain), every FCS opponent shares ONE pooled
"generic FCS opponent" offense/defense parameter pair. This still lets an
FBS team's blowout-or-struggle against an FCS opponent inform that FBS
team's OWN rating (real signal), while not pretending to know anything
FCS-team-specific about the opponent. FBS-vs-FCS games are still
projectable (see score_model.py) -- just with a coarser, documented,
higher-uncertainty opponent model, and evaluated as a SEPARATE backtest
segment (see backtest.py), never pooled into FBS-vs-FBS calibration
numbers.

*** MILESTONE C HARDENING: THE POOLED FCS PARAMETER WAS OVER-SHRUNK ***
The first live backtest found a large (~+14.8 point) FBS-vs-FCS margin
bias: the model was systematically under-predicting how badly FBS teams
beat FCS teams. Root cause, confirmed by inspection rather than assumed:
`fcs_offense`/`fcs_defense` were ridge-penalized with the SAME
`DEFAULT_RIDGE_LAMBDA` used for an individual FBS team that might have as
few as 1-2 games of evidence. But the pooled FCS parameter is fit from
EVERY FBS-vs-FCS game leaguewide in a season (typically several dozen to
~100+ rows, far more evidence than any single team gets) -- applying
individual-team-strength shrinkage to a parameter with that much pooled
evidence needlessly pulls it toward 0.0 (league-average FBS strength),
which is a genuinely wrong prior for "the typical FCS team that gets
scheduled by an FBS opponent." `DEFAULT_FCS_RIDGE_LAMBDA` is a separate,
smaller constant so the pooled FCS parameter is regularized in proportion
to its OWN (much larger) evidence volume instead of borrowing an
individual team's shrinkage strength. This is a standard shrinkage-vs-n
correction, not a hindsight-fit "FCS tier": it uses exactly the same
strictly-prior training rows as before, just penalizes them correctly.
See docs/MILESTONE_C.md "FBS-vs-FCS margin bias" for the before/after
backtest numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf, assert_strictly_before

FCS_PSEUDO_TEAM_ID = "__fcs__"

DEFAULT_RIDGE_LAMBDA = 10.0
"""Milestone C.2: lowered from Milestone C's 25.0 via a leakage-safe,
chronological DEVELOPMENT/CONFIRMATION selection procedure (see
docs/MILESTONE_C2.md "Leakage-safe model selection"), never by tuning on
and then re-presenting the same season as if it were untouched: candidates
were compared on 2022-2024 development data ONLY (10.0 was the clear
winner there on winner log loss/Brier/margin MAE/RMSE), and the selected
value was then, and only then, checked against the held-out 2025
confirmation season -- where the margin-accuracy gain replicates clearly,
while the winner-calibration gain is much smaller and within noise
(reported honestly, not oversold). No measurable change to margin bias,
FBS-vs-FCS bias, or total accuracy on either the development or
confirmation season. This is a genuine out-of-time accuracy improvement,
NOT a fix for the still-open favorite-tail margin-bias finding (see
docs/MILESTONE_C2.md) -- that bias was ablation-tested against this same
lambda sweep, on both development and confirmation data, and found
essentially unchanged by it. Still a documented, provisional constant,
not a cross-validated one."""

DEFAULT_PACE_SHRINKAGE_K = 4.0
"""Games of evidence at which a team's trailing pace is weighted 50/50
against the league-average pace -- same shrinkage FORM as the early-season
rating prior in priors.py, applied here to pace specifically."""

DEFAULT_FCS_RIDGE_LAMBDA = 4.0
"""Separate, much smaller ridge penalty for the POOLED fcs_offense/
fcs_defense parameters -- see module docstring's "MILESTONE C HARDENING"
note. Chosen to sit near DEFAULT_PACE_SHRINKAGE_K's order of magnitude
(a "trust the pooled evidence fairly quickly" strength) rather than
DEFAULT_RIDGE_LAMBDA's "distrust a single team's thin evidence" strength
-- still a documented, provisional constant (not cross-validated), but
now scaled to the right kind of parameter."""

FCS_TIER_MIN_GAMES = 2
"""An FCS opponent needs at least this many prior FBS matchups (within
the strictly-prior `lines` a rating snapshot is fit from) before it is
tiered individually -- fewer than this, it falls back to the "average"
tier bucket by default rather than being tiered on 0-1 noisy
observations. This mirrors DEFAULT_FCS_RIDGE_LAMBDA's underlying logic
(trust pooled evidence, distrust thin individual evidence) one level
down."""

FCS_TIER_WEAK_THRESHOLD = -35.0
FCS_TIER_STRONG_THRESHOLD = -20.0
"""Milestone C.2: fixed, documented, round-number thresholds on an FCS
opponent's own trailing average scoring margin against FBS opponents
(their points minus the FBS team's points, so a large negative number
means they get blown out) -- NOT adaptive percentile splits (which would
implicitly depend on the whole population, including future data, and
could shift discontinuously game to game). margin <= WEAK_THRESHOLD ->
"weak"; WEAK_THRESHOLD < margin <= STRONG_THRESHOLD -> "average"; margin
> STRONG_THRESHOLD -> "strong". Mechanically derived from each FCS
team's OWN prior-game history already present in the corpus (no new
CFBD endpoint, no manual reputation labels, no hindsight -- see
`_fcs_team_tiers`)."""

FCS_TIERS = ("weak", "average", "strong")
FCS_DEFAULT_TIER = "average"


@dataclass(frozen=True)
class RatingsSnapshot:
    """Every number a prediction for a game as-of `as_of` is allowed to
    use. Fit strictly from TeamGameLine rows with as_of < this snapshot's
    as_of (enforced inside `fit_fbs_efficiency_ratings`, not left to the
    caller to remember).
    """

    as_of: AsOf
    mu: float
    hfa: float
    offense: dict[str, float]
    defense: dict[str, float]
    fcs_offense: float
    fcs_defense: float
    games_played: dict[str, int]
    pace: dict[str, float]
    league_avg_pace: float
    n_training_rows: int
    n_teams_with_data: int
    fcs_mode: str = "pooled"
    """"pooled" (default/Milestone C behavior) or "tiered" (Milestone C.2
    candidate -- see `fit_fbs_efficiency_ratings`'s `fcs_mode` param)."""
    fcs_tier_offense: dict[str, float] = field(default_factory=dict)
    fcs_tier_defense: dict[str, float] = field(default_factory=dict)
    fcs_team_tier: dict[str, str] = field(default_factory=dict)
    """FCS team_id -> tier name, computed ONLY from strictly-prior games
    (the same `lines` this whole snapshot was fit from) -- an FCS
    opponent absent from this dict (never seen before, or fewer than
    FCS_TIER_MIN_GAMES prior matchups) is treated as FCS_DEFAULT_TIER by
    `opponent_*_rating` below, never as an error."""
    pace_mode: str = "symmetric"
    """"symmetric" (default/Milestone C behavior: both teams in a game get
    the SAME expected-plays value, `(team_pace(A) + team_pace(B)) / 2`) or
    "matchup" (Milestone C.2 candidate -- see `_estimate_defense_pace_allowed`
    and `expected_plays_for`): each team's OWN expected plays combines its
    own trailing offensive pace with the OPPONENT's trailing defensive
    "plays allowed" tendency, so the two teams in a game can genuinely
    differ in expected play volume instead of being forced to share one
    number."""
    defense_pace_allowed: dict[str, float] = field(default_factory=dict)
    """Trailing average plays/game a team's DEFENSE has allowed (i.e. the
    OPPONENT's plays in games where this team_id was on defense), shrunk
    toward the league average identically to `pace` -- only populated when
    `pace_mode == "matchup"`. See `_estimate_defense_pace_allowed`."""

    def offense_rating(self, team_id: str) -> float:
        return self.offense.get(team_id, 0.0)

    def defense_rating(self, team_id: str) -> float:
        return self.defense.get(team_id, 0.0)

    def fcs_offense_for(self, opponent_id: str) -> float:
        if self.fcs_mode != "tiered":
            return self.fcs_offense
        tier = self.fcs_team_tier.get(opponent_id, FCS_DEFAULT_TIER)
        return self.fcs_tier_offense.get(tier, self.fcs_offense)

    def fcs_defense_for(self, opponent_id: str) -> float:
        if self.fcs_mode != "tiered":
            return self.fcs_defense
        tier = self.fcs_team_tier.get(opponent_id, FCS_DEFAULT_TIER)
        return self.fcs_tier_defense.get(tier, self.fcs_defense)

    def opponent_offense_rating(self, opponent_id: str, opponent_classification: str | None) -> float:
        if opponent_classification == "fbs":
            return self.offense.get(opponent_id, 0.0)
        return self.fcs_offense_for(opponent_id)

    def opponent_defense_rating(self, opponent_id: str, opponent_classification: str | None) -> float:
        if opponent_classification == "fbs":
            return self.defense.get(opponent_id, 0.0)
        return self.fcs_defense_for(opponent_id)

    def team_pace(self, team_id: str) -> float:
        return self.pace.get(team_id, self.league_avg_pace)

    def defense_pace_allowed_for(self, team_id: str) -> float:
        return self.defense_pace_allowed.get(team_id, self.league_avg_pace)

    def expected_plays_for(self, team_id: str, opponent_id: str) -> float:
        """The expected-plays value `score_model.py` uses for `team_id`'s
        own scoring line in a game against `opponent_id`. "symmetric" mode
        (default) reproduces Milestone C's original shared-value behavior
        exactly (order-independent, both teams get the same number).
        "matchup" mode instead combines `team_id`'s own trailing offensive
        pace with `opponent_id`'s trailing defensive plays-allowed
        tendency -- see `pace_mode`'s docstring."""
        if self.pace_mode != "matchup":
            return (self.team_pace(team_id) + self.team_pace(opponent_id)) / 2
        return (self.team_pace(team_id) + self.defense_pace_allowed_for(opponent_id)) / 2

    def games_played_for(self, team_id: str) -> int:
        return self.games_played.get(team_id, 0)


def _home_indicator(line: TeamGameLine) -> float:
    if line.is_neutral_site:
        return 0.0
    return 1.0 if line.is_home else -1.0


def _fcs_team_tiers(training_rows: list[TeamGameLine]) -> dict[str, str]:
    """Milestone C.2 candidate: mechanically derives a weak/average/strong
    tier for each FCS opponent from its OWN trailing scoring margin
    against FBS teams, using ONLY rows already in `training_rows`
    (strictly prior to the snapshot's as_of by the caller's leakage
    check). No new data source, no manual team labels, no hindsight --
    every input is an FBS team's own row where `opponent_classification
    == "fcs"` (team_points is the FBS team's score, opponent_points is
    that FCS team's score). Deterministic: same input rows always
    produce the same tiering. An FCS opponent with fewer than
    FCS_TIER_MIN_GAMES such rows is simply absent from the returned dict
    -- callers must treat that as FCS_DEFAULT_TIER, never as an error.
    """
    fcs_margins: dict[str, list[float]] = {}
    for line in training_rows:
        if line.opponent_classification == "fcs":
            fcs_margins.setdefault(line.opponent_id, []).append(line.opponent_points - line.team_points)

    tiers: dict[str, str] = {}
    for fcs_team_id, margins in fcs_margins.items():
        if len(margins) < FCS_TIER_MIN_GAMES:
            continue
        avg_margin = sum(margins) / len(margins)
        if avg_margin <= FCS_TIER_WEAK_THRESHOLD:
            tiers[fcs_team_id] = "weak"
        elif avg_margin <= FCS_TIER_STRONG_THRESHOLD:
            tiers[fcs_team_id] = "average"
        else:
            tiers[fcs_team_id] = "strong"
    return tiers


def fit_fbs_efficiency_ratings(
    lines: list[TeamGameLine],
    as_of: AsOf,
    *,
    ridge_lambda: float = DEFAULT_RIDGE_LAMBDA,
    fcs_ridge_lambda: float = DEFAULT_FCS_RIDGE_LAMBDA,
    pace_shrinkage_k: float = DEFAULT_PACE_SHRINKAGE_K,
    fcs_mode: str = "pooled",
    pace_mode: str = "symmetric",
) -> RatingsSnapshot:
    """Fits offense/defense/HFA ratings and trailing pace from every
    TeamGameLine row with `team_classification == "fbs"` and
    `row.as_of` strictly before `as_of` (leakage-checked per row -- a row
    that is not strictly prior raises, it is never silently skipped by a
    range filter that could hide a caller's bug).

    `fcs_mode="tiered"` (Milestone C.2 candidate, see `_fcs_team_tiers`)
    fits THREE FCS pseudo-team offense/defense pairs (weak/average/strong,
    mechanically derived from each FCS opponent's own trailing scoring
    margin against FBS teams in `lines`) instead of one pooled pair --
    otherwise identical leakage/regularization discipline throughout.

    `pace_mode="matchup"` (Milestone C.2 totals candidate, see
    `_estimate_defense_pace_allowed` and `RatingsSnapshot.expected_plays_for`)
    additionally fits each team's trailing defensive plays-allowed, so the
    two teams in a game can get genuinely different expected-plays values
    instead of one shared symmetric average.
    """
    if fcs_mode not in ("pooled", "tiered"):
        raise ValueError(f"fcs_mode must be 'pooled' or 'tiered', got {fcs_mode!r}")
    if pace_mode not in ("symmetric", "matchup"):
        raise ValueError(f"pace_mode must be 'symmetric' or 'matchup', got {pace_mode!r}")

    training_rows = []
    for line in lines:
        if line.team_classification != "fbs":
            continue
        assert_strictly_before(line.as_of, as_of, context=f"fit_fbs_efficiency_ratings row for {line.team_id}")
        training_rows.append(line)

    team_ids = sorted({line.team_id for line in training_rows})
    team_index = {team_id: i for i, team_id in enumerate(team_ids)}
    n_teams = len(team_ids)

    games_played: dict[str, int] = {team_id: 0 for team_id in team_ids}
    for line in training_rows:
        games_played[line.team_id] += 1

    fcs_team_tier = _fcs_team_tiers(training_rows) if fcs_mode == "tiered" else {}
    fcs_groups = list(FCS_TIERS) if fcs_mode == "tiered" else ["pooled"]
    n_fcs_groups = len(fcs_groups)
    fcs_group_index = {name: i for i, name in enumerate(fcs_groups)}

    def _fcs_group_for(opponent_id: str) -> str:
        if fcs_mode == "pooled":
            return "pooled"
        return fcs_team_tier.get(opponent_id, FCS_DEFAULT_TIER)

    efficiency_rows = [line for line in training_rows if line.team_plays]
    # mu, hfa, offense[n_teams], defense[n_teams], fcs_offense/defense[groups]
    n_params = 2 + 2 * n_teams + 2 * n_fcs_groups
    if not efficiency_rows or n_teams == 0:
        # No leakage-safe evidence at all yet (e.g. the very start of the
        # very first season in the corpus) -- return a fully neutral
        # snapshot rather than dividing by zero. Every downstream consumer
        # must treat games_played == 0 as "pure prior" (see priors.py).
        return RatingsSnapshot(
            as_of=as_of,
            mu=0.0,
            hfa=0.0,
            offense=dict.fromkeys(team_ids, 0.0),
            defense=dict.fromkeys(team_ids, 0.0),
            fcs_offense=0.0,
            fcs_defense=0.0,
            games_played=games_played,
            pace=dict.fromkeys(team_ids, 0.0),
            league_avg_pace=70.0,
            n_training_rows=0,
            n_teams_with_data=0,
            fcs_mode=fcs_mode,
            fcs_tier_offense=dict.fromkeys(FCS_TIERS, 0.0) if fcs_mode == "tiered" else {},
            fcs_tier_defense=dict.fromkeys(FCS_TIERS, 0.0) if fcs_mode == "tiered" else {},
            fcs_team_tier=fcs_team_tier,
            pace_mode=pace_mode,
            defense_pace_allowed=dict.fromkeys(team_ids, 0.0) if pace_mode == "matchup" else {},
        )

    X = np.zeros((len(efficiency_rows), n_params))
    y = np.zeros(len(efficiency_rows))
    off_offset = 2
    def_offset = 2 + n_teams
    fcs_off_base = 2 + 2 * n_teams
    fcs_def_base = fcs_off_base + n_fcs_groups

    for row_i, line in enumerate(efficiency_rows):
        X[row_i, 0] = 1.0  # mu
        X[row_i, 1] = _home_indicator(line)
        X[row_i, off_offset + team_index[line.team_id]] = 1.0
        if line.opponent_classification == "fbs":
            X[row_i, def_offset + team_index[line.opponent_id]] = -1.0
        else:
            group_i = fcs_group_index[_fcs_group_for(line.opponent_id)]
            X[row_i, fcs_def_base + group_i] = -1.0
        y[row_i] = line.team_points / line.team_plays

    # mu (col 0) and hfa (col 1) get only a tiny numerical-stability
    # epsilon, not the real ridge_lambda -- they are meant to be
    # essentially unregularized (the league-wide intercept and
    # home-field edge should be driven by the data, not shrunk toward
    # zero the way an individual team's rating should be). The epsilon
    # exists purely so a degenerate design (e.g. every training row is
    # neutral-site, making the whole hfa column zero; or too few rows to
    # separate mu/hfa/offense/defense) never produces a genuinely singular
    # matrix -- without it such inputs would raise LinAlgError instead of
    # returning a legitimate (if data-starved) fit.
    NUMERICAL_STABILITY_EPSILON = 1e-6
    penalty = np.full(n_params, NUMERICAL_STABILITY_EPSILON)
    penalty[off_offset:] = ridge_lambda
    # The pooled/tiered FCS columns get their own, much smaller penalty --
    # see DEFAULT_FCS_RIDGE_LAMBDA's docstring: even a single tier is fit
    # from far more pooled evidence than an individual FBS team's columns,
    # so shrinking it at the individual-team strength is a bug, not a
    # feature.
    penalty[fcs_off_base : fcs_off_base + n_fcs_groups] = fcs_ridge_lambda
    penalty[fcs_def_base : fcs_def_base + n_fcs_groups] = fcs_ridge_lambda
    ridge_matrix = np.diag(penalty)

    XtX = X.T @ X
    beta = np.linalg.solve(XtX + ridge_matrix, X.T @ y)

    mu = float(beta[0])
    hfa = float(beta[1])
    offense = {team_id: float(beta[off_offset + i]) for team_id, i in team_index.items()}
    defense = {team_id: float(beta[def_offset + i]) for team_id, i in team_index.items()}
    fcs_tier_offense = {name: float(beta[fcs_off_base + i]) for name, i in fcs_group_index.items()}
    fcs_tier_defense = {name: float(beta[fcs_def_base + i]) for name, i in fcs_group_index.items()}
    # `fcs_offense`/`fcs_defense` (the pooled scalars) are always populated
    # too, even in "tiered" mode -- used as the documented fallback for an
    # FCS opponent whose tier bucket somehow has no fitted value (should
    # not happen given FCS_DEFAULT_TIER, but keeps the fallback real
    # rather than hardcoded to 0.0).
    if fcs_mode == "pooled":
        fcs_offense = fcs_tier_offense["pooled"]
        fcs_defense = fcs_tier_defense["pooled"]
    else:
        fcs_offense = fcs_tier_offense[FCS_DEFAULT_TIER]
        fcs_defense = fcs_tier_defense[FCS_DEFAULT_TIER]

    pace = _estimate_pace(training_rows, team_ids, shrinkage_k=pace_shrinkage_k)
    league_avg_pace = pace.pop("__league_average__")
    defense_pace_allowed = (
        _estimate_defense_pace_allowed(training_rows, team_ids, shrinkage_k=pace_shrinkage_k)
        if pace_mode == "matchup"
        else {}
    )

    return RatingsSnapshot(
        as_of=as_of,
        mu=mu,
        hfa=hfa,
        offense=offense,
        defense=defense,
        fcs_offense=fcs_offense,
        fcs_defense=fcs_defense,
        games_played=games_played,
        pace=pace,
        league_avg_pace=league_avg_pace,
        n_training_rows=len(efficiency_rows),
        n_teams_with_data=n_teams,
        fcs_mode=fcs_mode,
        fcs_tier_offense=fcs_tier_offense if fcs_mode == "tiered" else {},
        fcs_tier_defense=fcs_tier_defense if fcs_mode == "tiered" else {},
        fcs_team_tier=fcs_team_tier,
        pace_mode=pace_mode,
        defense_pace_allowed=defense_pace_allowed,
    )


def _estimate_pace(
    training_rows: list[TeamGameLine], team_ids: list[str], *, shrinkage_k: float
) -> dict[str, float]:
    """Trailing average plays/game per team, shrunk toward the league
    average in proportion to games played (same shrinkage FORM used for
    the season-carryover prior in priors.py, applied here within-season).
    Returns a dict with one extra key "__league_average__" carrying the
    league mean, popped by the caller.
    """
    plays_by_team: dict[str, list[int]] = {team_id: [] for team_id in team_ids}
    all_plays: list[int] = []
    for line in training_rows:
        if line.team_plays is None:
            continue
        plays_by_team.setdefault(line.team_id, []).append(line.team_plays)
        all_plays.append(line.team_plays)

    league_avg = float(np.mean(all_plays)) if all_plays else 70.0

    pace: dict[str, float] = {"__league_average__": league_avg}
    for team_id in team_ids:
        observed = plays_by_team.get(team_id, [])
        n = len(observed)
        if n == 0:
            pace[team_id] = league_avg
            continue
        team_avg = float(np.mean(observed))
        weight = n / (n + shrinkage_k)
        pace[team_id] = weight * team_avg + (1 - weight) * league_avg
    return pace


def _estimate_defense_pace_allowed(
    training_rows: list[TeamGameLine], team_ids: list[str], *, shrinkage_k: float
) -> dict[str, float]:
    """Milestone C.2 "matchup" pace_mode candidate: trailing average
    plays/game a team's DEFENSE has ALLOWED, mirroring `_estimate_pace`'s
    shrinkage form exactly. FBS-vs-FBS games only (same population
    `_paired_fbs_games`/the residual pool already restrict to -- mission
    section 4: FBS-vs-FCS is never blended into main FBS-vs-FBS
    calibration), so this needs `opponent_classification == "fbs"` on top
    of `training_rows`'s existing `team_classification == "fbs"` filter.
    For a row where team=A played opponent=B, A's own `team_plays` is
    exactly how many plays B's DEFENSE allowed that game -- no new data,
    just re-aggregating the SAME already-captured `team_plays` field from
    the opponent's (defense's) perspective instead of the offense's.
    """
    allowed_by_team: dict[str, list[int]] = {team_id: [] for team_id in team_ids}
    all_allowed: list[int] = []
    for line in training_rows:
        if line.team_plays is None or line.opponent_classification != "fbs":
            continue
        allowed_by_team.setdefault(line.opponent_id, []).append(line.team_plays)
        all_allowed.append(line.team_plays)

    league_avg = float(np.mean(all_allowed)) if all_allowed else 70.0

    allowed: dict[str, float] = {}
    for team_id in team_ids:
        observed = allowed_by_team.get(team_id, [])
        n = len(observed)
        if n == 0:
            allowed[team_id] = league_avg
            continue
        team_avg = float(np.mean(observed))
        weight = n / (n + shrinkage_k)
        allowed[team_id] = weight * team_avg + (1 - weight) * league_avg
    return allowed


def expected_points_per_play(
    ratings: RatingsSnapshot,
    team_id: str,
    opponent_id: str,
    opponent_classification: str | None,
    *,
    home_indicator: float,
) -> float:
    """home_indicator: +1.0 home, -1.0 away, 0.0 neutral -- same convention
    as the fit itself (`_home_indicator`), so a caller can never
    accidentally apply HFA to a neutral-site game by passing the wrong sign
    (0.0 always means "no HFA either way").
    """
    return (
        ratings.mu
        + ratings.offense_rating(team_id)
        - ratings.opponent_defense_rating(opponent_id, opponent_classification)
        + ratings.hfa * home_indicator
    )
