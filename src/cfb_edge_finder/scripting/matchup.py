"""Team profiles and the market-blind matchup vector. PURE.

*** WHAT A MATCHUP EDGE IS ***
For one metric and one direction (say, the home offense against the away
defense), standardise each unit's ADJUSTED value against the FBS universe
and add them, signed so that positive always favours the offense:

    offense_z = s * (adjusted_offense - mean_offense) / sd_offense
    defense_z = s * (adjusted_defense_allowed - mean_defense) / sd_defense
    edge      = offense_z + defense_z              s = +1 if more is better for an offense
    uncertainty = sqrt((se_offense / sd_offense)^2 + (se_defense / sd_defense)^2)

An edge of +2 is, for example, a one-SD-above-average offense meeting a
one-SD-below-average defense. It is a DESCRIPTION of the two units; it is
not a forecast of the next game's stat line, and nothing downstream turns it
into a probability.

A dimension's edge is the mean of its primary metrics' edges. Secondary
evidence (third downs), regression-prone metrics (turnovers) and descriptive
tendencies (pass rate) are shown but never enter a dimension edge.

*** NOTHING HERE KNOWS A PRICE ***
This module's inputs are a `LeagueFit`, the rows it was fitted on, and the
two team ids. There is no parameter through which a market could enter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from cfb_edge_finder.scripting.adjust import (
    DEFENSE,
    OFFENSE,
    Q_NOT_ADJUSTED,
    LeagueFit,
    MetricFit,
    rank_in_universe,
)
from cfb_edge_finder.scripting.gamelog import TeamGame, before
from cfb_edge_finder.scripting.metrics import BY_ID, ENHANCED, METRICS, MetricDef

#: Which metrics form each dimension's edge. Order is presentation order.
DIMENSION_EDGE_METRICS: dict[str, tuple[str, ...]] = {
    "scoring": ("points_per_game",),
    "sustained_efficiency": ("success_rate", "early_down_success_rate", "yards_per_play", "first_down_rate"),
    "rushing": ("rush_success_rate", "yards_per_designed_rush", "yards_per_rush"),
    "passing": ("pass_success_rate", "net_yards_per_dropback", "yards_per_pass_attempt"),
    "explosiveness": ("explosive_play_rate", "explosive_rush_rate", "explosive_pass_rate"),
    "disruption": ("sack_rate_allowed", "havoc_allowed"),
    "finishing": ("points_per_opportunity", "points_per_drive"),
}

#: Shown alongside a dimension but never averaged into its edge.
DIMENSION_CONTEXT_METRICS: dict[str, tuple[str, ...]] = {
    "sustained_efficiency": ("third_down_rate",),
    "passing": ("interception_rate",),
    "disruption": ("turnovers_per_game",),
    "pace": ("plays_per_game", "seconds_per_play", "pass_rate", "neutral_pass_rate"),
    "volatility": ("explosive_yard_share",),
}

SIDES = ("home", "away")


def _r(value: float | None, places: int = 4) -> float | None:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    return round(float(value), places)


def metric_key(side: str, unit: str, metric_id: str) -> str:
    return f"{side}.{unit}.{metric_id}"


@dataclass(frozen=True)
class GameIdentity:
    """Who is playing where. Football facts only."""

    event_id: str
    home_id: str
    away_id: str
    home_name: str
    away_name: str
    kickoff_utc: str
    neutral_site: bool
    season: int

    def team(self, side: str) -> tuple[str, str]:
        return (self.home_id, self.home_name) if side == "home" else (self.away_id, self.away_name)


def _sources_for(rows: list[TeamGame], team_id: str, metric: MetricDef) -> tuple[str | None, str | None]:
    mine = [r for r in rows if r.team_id == team_id or r.opponent_id == team_id]
    if not mine:
        return None, None
    sources = sorted({(r.pbp_source if metric.tier == ENHANCED else r.source) or "" for r in mine} - {""})
    observed = max((r.observed_at or "") for r in mine) or None
    return (",".join(sources) or None), observed


def unit_metric(
    fit: MetricFit,
    metric: MetricDef,
    team_id: str,
    unit: str,
    rows: list[TeamGame],
    season: int,
    cutoff: str,
) -> dict[str, Any]:
    """One published metric for one team-unit: every field the contract requires."""
    est = fit.get(team_id, unit)
    higher = metric.offense_higher_is_better if unit == OFFENSE else not metric.offense_higher_is_better
    rank, universe = rank_in_universe(fit, team_id, unit, higher)
    source, observed = _sources_for(rows, team_id, metric)
    adjusted = est.adjusted if est is not None else None
    if not metric.adjust:
        reason = "descriptive tendency: opponent adjustment does not apply to this metric (V1)"
    elif est is None or est.games == 0:
        reason = "no qualifying games for this team before the cutoff"
    elif adjusted is None:
        reason = "the league fit for this metric could not be computed (too few observations)"
    else:
        reason = None
    return {
        "metric_id": metric.metric_id,
        "name": metric.name,
        "dimension": metric.dimension,
        "unit": unit,
        "measure": metric.unit,
        "tier": metric.tier,
        "raw": _r(est.raw if est else None),
        "adjusted": _r(adjusted),
        "adjusted_available": adjusted is not None,
        "adjusted_unavailable_reason": reason,
        "standard_error": _r(est.se if est else None),
        "rank": rank,
        "universe_size": universe,
        "rank_basis": "adjusted" if fit.adjusted else "raw",
        "direction": "higher_is_better" if higher else "lower_is_better",
        "sample": {
            "games": est.games if est else 0,
            "effective_n": _r(est.effective_n if est else 0.0, 2),
            "prior_weight": _r(est.prior_weight if est else None, 3),
        },
        "source": source,
        "observed_at": observed,
        "season": season,
        "window": {"type": "season_to_date", "through_exclusive": cutoff},
        "quality": (est.quality if est else ("UNAVAILABLE" if metric.adjust else Q_NOT_ADJUSTED)),
        "secondary_evidence": metric.secondary,
        "regression_prone": metric.regression_prone,
    }


def team_profile(
    league: LeagueFit,
    rows: list[TeamGame],
    identity: GameIdentity,
    side: str,
) -> dict[str, Any]:
    team_id, name = identity.team(side)
    history = before(rows, league.cutoff)
    games = [r for r in history if r.team_id == team_id]
    wins = sum(1 for r in games if (r.points_for or 0) > (r.points_against or 0))
    metrics: dict[str, dict[str, Any]] = {}
    for metric in METRICS:
        fit = league.fits.get(metric.metric_id)
        if fit is None:
            continue
        for unit in (OFFENSE, DEFENSE):
            metrics[f"{unit}.{metric.metric_id}"] = unit_metric(
                fit, metric, team_id, unit, history, identity.season, league.cutoff
            )
    passers = [(r.kickoff_utc, r.primary_passer, r.primary_passer_att) for r in games if r.primary_passer]
    return {
        "team_id": team_id,
        "name": name,
        "division": league.divisions.get(team_id, "unknown"),
        "games_observed": len(games),
        "games_with_play_log": sum(1 for r in games if r.pbp is not None),
        "record_in_window": {"wins": wins, "losses": len(games) - wins},
        "residual_sd": {
            "offense_yards_per_play": _r(_resid(league, "yards_per_play", team_id, OFFENSE)),
            "defense_yards_per_play": _r(_resid(league, "yards_per_play", team_id, DEFENSE)),
            "offense_points": _r(_resid(league, "points_per_game", team_id, OFFENSE)),
            "defense_points": _r(_resid(league, "points_per_game", team_id, DEFENSE)),
        },
        "quarterback": _passer_continuity(passers),
        "metrics": metrics,
    }


def _resid(league: LeagueFit, metric_id: str, team_id: str, unit: str) -> float | None:
    fit = league.fits.get(metric_id)
    est = fit.get(team_id, unit) if fit else None
    return est.residual_sd if est else None


def _passer_continuity(passers: list[tuple[str, str | None, float | None]]) -> dict[str, Any]:
    """Did the team's leading passer start its most recent game? A box-score fact.

    This is the one availability signal the box score itself carries, and it
    is reported as what it is: who threw the most passes, game by game."""
    if not passers:
        return {"season_primary": None, "last_game_primary": None, "changed": None, "games": 0}
    passers = sorted(passers)
    totals: dict[str, float] = {}
    for _, name, att in passers:
        if name:
            totals[name] = totals.get(name, 0.0) + float(att or 0.0)
    season_primary = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if totals else None
    last = passers[-1][1]
    return {
        "season_primary": season_primary,
        "last_game_primary": last,
        "changed": bool(season_primary and last and season_primary != last),
        "games": len(passers),
    }


# ------------------------------------------------------------- edges


def _z(fit: MetricFit, unit: str, value: float | None) -> tuple[float | None, float | None]:
    dist = fit.distribution(unit)
    if value is None or dist is None or dist[1] <= 0:
        return None, None
    mean, sd, _ = dist
    return (value - mean) / sd, sd


def metric_edge(
    league: LeagueFit, metric_id: str, offense_team: str, defense_team: str, offense_side: str
) -> dict[str, Any] | None:
    fit = league.fits.get(metric_id)
    metric = BY_ID[metric_id]
    if fit is None or not fit.adjusted:
        return None
    off, dfn = fit.get(offense_team, OFFENSE), fit.get(defense_team, DEFENSE)
    if off is None or dfn is None or off.adjusted is None or dfn.adjusted is None:
        return None
    sign = 1.0 if metric.offense_higher_is_better else -1.0
    oz, osd = _z(fit, OFFENSE, off.adjusted)
    dz, dsd = _z(fit, DEFENSE, dfn.adjusted)
    if oz is None or dz is None:
        return None
    unc = math.sqrt(((off.se or 0.0) / osd) ** 2 + ((dfn.se or 0.0) / dsd) ** 2)
    defense_side = "away" if offense_side == "home" else "home"
    return {
        "metric_id": metric_id,
        "tier": metric.tier,
        "offense_z": _r(sign * oz, 3),
        "defense_z": _r(sign * dz, 3),
        "edge": _r(sign * (oz + dz), 3),
        "uncertainty": _r(unc, 3),
        "offense_ref": metric_key(offense_side, OFFENSE, metric_id),
        "defense_ref": metric_key(defense_side, DEFENSE, metric_id),
    }


def _direction(league: LeagueFit, dimension: str, identity: GameIdentity, offense_side: str) -> dict[str, Any]:
    off_team = identity.home_id if offense_side == "home" else identity.away_id
    def_team = identity.away_id if offense_side == "home" else identity.home_id
    components = [
        edge
        for metric_id in DIMENSION_EDGE_METRICS.get(dimension, ())
        if (edge := metric_edge(league, metric_id, off_team, def_team, offense_side)) is not None
    ]
    if not components:
        return {"edge": None, "uncertainty": None, "components": [], "enhanced_components": 0}
    edge = sum(c["edge"] for c in components) / len(components)
    unc = sum(c["uncertainty"] for c in components) / len(components)
    return {
        "edge": _r(edge, 3),
        "uncertainty": _r(unc, 3),
        "components": components,
        "enhanced_components": sum(1 for c in components if c["tier"] == ENHANCED),
    }


def _unit_z(league: LeagueFit, metric_id: str, team_id: str, unit: str) -> float | None:
    fit = league.fits.get(metric_id)
    if fit is None:
        return None
    est = fit.get(team_id, unit)
    value = None if est is None else (est.adjusted if fit.adjusted else est.raw)
    z, _ = _z(fit, unit, value)
    return z


def _pace(league: LeagueFit, identity: GameIdentity) -> dict[str, Any]:
    h, a = identity.home_id, identity.away_id
    plays = [
        _unit_z(league, "plays_per_game", h, OFFENSE),
        _unit_z(league, "plays_per_game", a, DEFENSE),
        _unit_z(league, "plays_per_game", a, OFFENSE),
        _unit_z(league, "plays_per_game", h, DEFENSE),
    ]
    tempo = [
        _unit_z(league, "seconds_per_play", h, OFFENSE),
        _unit_z(league, "seconds_per_play", a, OFFENSE),
    ]
    env = sum(plays) / 2 if all(z is not None for z in plays) else None
    tempo_env = -sum(tempo) / 2 if all(z is not None for z in tempo) else None
    return {
        "possession_environment": _r(env, 3),
        "tempo_environment": _r(tempo_env, 3),
        "definition": (
            "possession_environment = (z of home offense plays + z of away defense plays allowed + z of away "
            "offense plays + z of home defense plays allowed) / 2, on adjusted values. Positive = more snaps "
            "than an average FBS game. tempo_environment = -(z of each offense's possession seconds per play) / 2"
        ),
        "refs": [
            metric_key("home", OFFENSE, "plays_per_game"),
            metric_key("away", DEFENSE, "plays_per_game"),
            metric_key("away", OFFENSE, "plays_per_game"),
            metric_key("home", DEFENSE, "plays_per_game"),
            metric_key("home", OFFENSE, "seconds_per_play"),
            metric_key("away", OFFENSE, "seconds_per_play"),
        ],
    }


def _volatility(league: LeagueFit, identity: GameIdentity, profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    fit = league.fits.get("yards_per_play")
    residuals = []
    if fit is not None:
        residuals = sorted(
            est.residual_sd
            for (team, unit), est in fit.estimates.items()
            if unit == OFFENSE and team in fit.fbs_teams and est.residual_sd is not None
        )

    def percentile(value: float | None) -> float | None:
        if value is None or not residuals:
            return None
        below = sum(1 for v in residuals if v < value)
        return round(below / len(residuals), 3)

    out: dict[str, Any] = {}
    for side in SIDES:
        team_id = identity.home_id if side == "home" else identity.away_id
        prof = profiles[side]
        share = prof["metrics"].get("offense.explosive_yard_share", {}).get("raw")
        out[side] = {
            "offense_ypp_residual_sd": prof["residual_sd"]["offense_yards_per_play"],
            "offense_ypp_residual_sd_percentile": percentile(prof["residual_sd"]["offense_yards_per_play"]),
            "explosive_yard_share": share,
            "explosive_yard_share_z": _r(_unit_z(league, "explosive_yard_share", team_id, OFFENSE), 3),
            "success_rate_z": _r(_unit_z(league, "success_rate", team_id, OFFENSE), 3),
            "takeaways_per_game_z": _r(_unit_z(league, "turnovers_per_game", team_id, DEFENSE), 3),
            "giveaways_per_game_z": _r(_unit_z(league, "turnovers_per_game", team_id, OFFENSE), 3),
            "defense_ypp_allowed_z": _r(_unit_z(league, "yards_per_play", team_id, DEFENSE), 3),
            "refs": [
                metric_key(side, OFFENSE, "explosive_yard_share"),
                metric_key(side, OFFENSE, "success_rate"),
                metric_key(side, DEFENSE, "turnovers_per_game"),
                metric_key(side, OFFENSE, "turnovers_per_game"),
                metric_key(side, DEFENSE, "yards_per_play"),
            ],
        }
    out["definition"] = (
        "offense_ypp_residual_sd = game-to-game spread of the offense's yards per play around what the fit "
        "expected against each opponent; its percentile is within the FBS universe. explosive_yard_share_z "
        "is the raw share standardised across FBS (not opponent-adjusted). takeaways_per_game_z and "
        "giveaways_per_game_z are RAW per-game turnover counts standardised across FBS: turnovers are "
        "regression-prone and are deliberately not opponent-adjusted."
    )
    return out


def scoring_baseline(league: LeagueFit, identity: GameIdentity) -> dict[str, Any]:
    """The opponent-adjusted scoring baseline that script bands are drawn around.

    home = adj_off(home) + adj_def_allowed(away) - mu + h * x;  away likewise with -h * x,
    x = 1 at a true home site and 0 at a neutral site. DESCRIPTIVE: what these
    offenses have produced, adjusted for the defenses they met, meeting these
    defenses. It is not a projection, it is not calibrated, and it is used
    for one thing only: drawing the scoring bands a script's shape is stated in."""
    fit = league.fits.get("points_per_game")
    out: dict[str, Any] = {
        "home_points": None,
        "away_points": None,
        "total_points": None,
        "home_margin": None,
        "uncertainty_points": None,
        "label": "opponent-adjusted scoring baseline -- descriptive, uncalibrated, NOT a projection",
        "refs": [
            metric_key("home", OFFENSE, "points_per_game"),
            metric_key("away", DEFENSE, "points_per_game"),
            metric_key("away", OFFENSE, "points_per_game"),
            metric_key("home", DEFENSE, "points_per_game"),
        ],
    }
    if fit is None or not fit.adjusted or fit.mu is None:
        return out
    ho, ad = fit.get(identity.home_id, OFFENSE), fit.get(identity.away_id, DEFENSE)
    ao, hd = fit.get(identity.away_id, OFFENSE), fit.get(identity.home_id, DEFENSE)
    if any(e is None or e.adjusted is None for e in (ho, ad, ao, hd)):
        return out
    x = 0.0 if identity.neutral_site else 1.0
    h = fit.home_effect or 0.0
    home = ho.adjusted + ad.adjusted - fit.mu + h * x
    away = ao.adjusted + hd.adjusted - fit.mu - h * x
    unc = math.sqrt(sum((e.se or 0.0) ** 2 for e in (ho, ad, ao, hd)))
    out.update(
        {
            "home_points": _r(max(home, 0.0), 1),
            "away_points": _r(max(away, 0.0), 1),
            "total_points": _r(max(home, 0.0) + max(away, 0.0), 1),
            "home_margin": _r(home - away, 1),
            "uncertainty_points": _r(unc, 1),
            "home_effect_applied": _r(h * x, 2),
        }
    )
    return out


def matchup_vector(league: LeagueFit, rows: list[TeamGame], identity: GameIdentity) -> dict[str, Any]:
    profiles = {side: team_profile(league, rows, identity, side) for side in SIDES}
    dimensions: dict[str, Any] = {}
    for dimension in DIMENSION_EDGE_METRICS:
        home_dir = _direction(league, dimension, identity, "home")
        away_dir = _direction(league, dimension, identity, "away")
        net = (
            _r(home_dir["edge"] - away_dir["edge"], 3)
            if home_dir["edge"] is not None and away_dir["edge"] is not None
            else None
        )
        dimensions[dimension] = {
            "edge_metrics": list(DIMENSION_EDGE_METRICS[dimension]),
            "context_metrics": list(DIMENSION_CONTEXT_METRICS.get(dimension, ())),
            "home_offense_vs_away_defense": home_dir,
            "away_offense_vs_home_defense": away_dir,
            "net_home_advantage": net,
        }
    dimensions["pace"] = {
        **_pace(league, identity),
        "context_metrics": list(DIMENSION_CONTEXT_METRICS["pace"]),
    }
    dimensions["volatility"] = {
        **_volatility(league, identity, profiles),
        "context_metrics": list(DIMENSION_CONTEXT_METRICS["volatility"]),
    }
    return {
        "teams": profiles,
        "dimensions": dimensions,
        "scoring_baseline": scoring_baseline(league, identity),
        "adjustment": {
            "cutoff_exclusive": league.cutoff,
            "rows_fingerprint": league.rows_fingerprint,
            "config": league.config.describe(),
            **league.stability(),
            "league_baselines": {
                mid: {"mu": _r(f.mu), "home_effect": _r(f.home_effect), "residual_sigma": _r(f.sigma)}
                for mid, f in sorted(league.fits.items())
                if f.adjusted
            },
        },
    }
