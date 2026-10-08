"""Market-blind, outcome-blind pregame feature rows. RESEARCH ONLY.

*** WHAT THIS MODULE MAY READ ***
A season's `TeamGame` rows and the schedule metadata the archetype studies
already whitelist (`archetype_research.replay.GAME_META_FIELDS`). For each
target it hands ONLY `gamelog.before(rows, data_cutoff(kickoff))` to the
unmodified production builder (`scripting.football.build_content`), exactly
as the Wave 1/2 archetype studies did, then flattens the production matchup
vector into one row.

It never imports `market_lines` or `outcomes`, never reads a CFBD `lines_*`
file, and never reads the target game's own box score
(`tests/test_signal_discovery_cfb.py` pins all three).

*** OPPONENT ADJUSTMENT ***
Every adjusted number is the production `cfb-opponent-adjustment/1.0.0` fit
(ridge-style offense/defense regression with home effect and shrinkage
priors, in-season only). No new adjustment method is introduced. Each
feature records the RAW value beside the ADJUSTED value and the standard
error / games for the headline metrics so reliability is visible.

*** SIGN CONVENTION ***
`<side>_off_q.<metric>` : quality of that side's offense, in FBS SDs of the
                          adjusted value, positive = better offense.
`<side>_def_q.<metric>` : quality of that side's defense, positive = better
                          defense (allows less).
`dir.<dimension>.<side>`: production directional edge of that side's offense
                          against the other side's defense (positive favours
                          the offense), i.e. offense quality minus defense
                          quality on the same scale.
`net.<dimension>`       : production `net_home_advantage` (home direction
                          minus away direction).
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.archetype_research.replay import (
    OBSERVED_CLEAN,
    Target,
    build_pregame_content,
    canonical,
    history_for,
    sha256,
)
from cfb_edge_finder.scripting.adjust import DEFENSE, OFFENSE
from cfb_edge_finder.scripting.claims import derive_claims
from cfb_edge_finder.scripting.football import LeagueFitCache
from cfb_edge_finder.scripting.matchup import DIMENSION_EDGE_METRICS, _z
from cfb_edge_finder.scripting.metrics import METRICS

#: Metrics whose raw value, adjusted value, SE and games are copied for every team-unit,
#: so the RAW vs ADJUSTED vs RELIABILITY triple is on every row.
HEADLINE_METRICS = ("points_per_game", "yards_per_play", "success_rate", "plays_per_game")

DIMENSIONS = tuple(DIMENSION_EDGE_METRICS)

SIDES = ("home", "away")


def _r(value: float | None, places: int = 4) -> float | None:
    return None if value is None else round(float(value), places)


def _unit_quality(fit, team_id: str, unit: str, higher_better_for_offense: bool) -> float | None:
    """Standardised adjusted value, signed so positive = better unit (offense or defense)."""
    est = fit.get(team_id, unit)
    if est is None:
        return None
    value = est.adjusted if fit.adjusted else est.raw
    z, _ = _z(fit, unit, value)
    if z is None:
        return None
    sign = 1.0 if higher_better_for_offense else -1.0
    return sign * z if unit == OFFENSE else -sign * z


def feature_row(content: dict[str, Any], target: Target, cutoff: str, history: tuple, league) -> dict[str, Any]:
    """Flatten one production football artifact into a research feature row."""
    vector = content["matchup_profile"]
    dims = vector["dimensions"]
    findings = content["matchup_findings"]
    baseline = vector["scoring_baseline"]
    names = {"home": target.home_name, "away": target.away_name}
    claims, _ = derive_claims(findings, names, None, [])
    ids = {"home": target.home_id, "away": target.away_id}
    prior = {side: sum(1 for r in history if r.team_id == ids[side]) for side in SIDES}

    row: dict[str, Any] = {
        "game_id": target.game_id,
        "season": target.season,
        "week": target.week,
        "season_type": target.season_type,
        "kickoff_utc": target.kickoff_utc,
        "football_data_cutoff": cutoff,
        "home": target.home_name,
        "away": target.away_name,
        "home_id": target.home_id,
        "away_id": target.away_id,
        "neutral_site": target.neutral_site,
        "conference_game": target.conference_game,
        "home_division": target.home_division,
        "away_division": target.away_division,
        "fcs_involved": target.home_division != "fbs" or target.away_division != "fbs",
        "prior_games_home": prior["home"],
        "prior_games_away": prior["away"],
        "history_games": len({r.game_id for r in history}),
        "history_latest_kickoff": max((r.kickoff_utc for r in history), default=None),
        "adjustment_stable": bool(vector["adjustment"].get("stable")),
        "data_confidence": content["data_confidence"],
        "content_hash": sha256(content),
    }

    # --- production V2 claims (unchanged rule, read from this replay's own findings)
    control = claims["control"]
    row["control_side"] = control["side"] if control else None
    row["control_strength"] = control["strength"] if control else None
    row["closeness"] = claims["closeness"] is not None
    row["pace_claim"] = claims["pace"]["level"] if claims["pace"] else None
    se = claims["scoring_environment"]
    row["scoring_env_claim"] = se["level"] if se else None
    row["defensive_suppression_claim"] = claims["defensive_suppression"] is not None
    row["disruption_sides"] = sorted(d["side"] for d in claims["disruption"])
    row["finding_codes"] = sorted(f["code"] for f in findings)
    by_code = {f["code"]: f for f in findings}
    for side in SIDES:
        f = by_code.get(f"{side.upper()}_SUSTAINED_EFFICIENCY_ADVANTAGE")
        row[f"sus_finding_{side}_value"] = _r(f["value"], 3) if f else None
        row[f"sus_finding_{side}_uncertainty"] = _r(f["uncertainty"], 3) if f else None

    # --- dimension edges (production matchup vector)
    for dim in DIMENSIONS:
        d = dims.get(dim) or {}
        row[f"net.{dim}"] = d.get("net_home_advantage")
        h = d.get("home_offense_vs_away_defense") or {}
        a = d.get("away_offense_vs_home_defense") or {}
        row[f"dir.{dim}.home"] = h.get("edge")
        row[f"dir.{dim}.away"] = a.get("edge")
        unc = [u for u in (h.get("uncertainty"), a.get("uncertainty")) if u is not None]
        row[f"unc.{dim}"] = _r(sum(unc) / len(unc), 3) if len(unc) == 2 else None
    pace = dims.get("pace") or {}
    row["possession_environment"] = pace.get("possession_environment")
    row["tempo_environment"] = pace.get("tempo_environment")
    vol = dims.get("volatility") or {}
    for side in SIDES:
        v = vol.get(side) or {}
        row[f"{side}_ypp_resid_sd_pct"] = v.get("offense_ypp_residual_sd_percentile")
        row[f"{side}_takeaways_z_raw"] = v.get("takeaways_per_game_z")
        row[f"{side}_giveaways_z_raw"] = v.get("giveaways_per_game_z")

    # --- unit qualities for every adjusted metric, both teams, both units
    for metric in METRICS:
        fit = league.fits.get(metric.metric_id)
        if fit is None or not metric.adjust:
            continue
        for side in SIDES:
            row[f"{side}_off_q.{metric.metric_id}"] = _r(
                _unit_quality(fit, ids[side], OFFENSE, metric.offense_higher_is_better), 3
            )
            row[f"{side}_def_q.{metric.metric_id}"] = _r(
                _unit_quality(fit, ids[side], DEFENSE, metric.offense_higher_is_better), 3
            )

    # --- RAW vs ADJUSTED vs reliability for headline metrics
    for mid in HEADLINE_METRICS:
        fit = league.fits.get(mid)
        for side in SIDES:
            for unit, tag in ((OFFENSE, "off"), (DEFENSE, "def")):
                est = fit.get(ids[side], unit) if fit else None
                row[f"{side}_{tag}_raw.{mid}"] = _r(est.raw) if est else None
                row[f"{side}_{tag}_adj.{mid}"] = _r(est.adjusted) if est else None
                row[f"{side}_{tag}_se.{mid}"] = _r(est.se) if est else None
                row[f"{side}_{tag}_games.{mid}"] = est.games if est else 0
        if fit is not None:
            row[f"league_mu.{mid}"] = _r(fit.mu)
            row[f"league_home_effect.{mid}"] = _r(fit.home_effect)

    # --- pass-rate tendency (descriptive, not adjusted; production publishes raw)
    pr = league.fits.get("pass_rate")
    for side in SIDES:
        est = pr.get(ids[side], OFFENSE) if pr else None
        row[f"{side}_pass_rate_raw"] = _r(est.raw) if est else None

    # --- opponent-adjusted scoring baseline (production; descriptive)
    for k in ("home_points", "away_points", "total_points", "home_margin", "uncertainty_points"):
        row[f"baseline.{k}"] = baseline.get(k)

    row["eligibility"] = eligibility(row)
    return row


def eligibility(row: dict[str, Any]) -> str | None:
    """Same rule as the archetype studies: both teams need history; profile and baseline defined."""
    if row["prior_games_home"] == 0 and row["prior_games_away"] == 0:
        return "NO_PRIOR_HISTORY_BOTH"
    if row["prior_games_home"] == 0:
        return "NO_PRIOR_HISTORY_HOME"
    if row["prior_games_away"] == 0:
        return "NO_PRIOR_HISTORY_AWAY"
    if row["net.sustained_efficiency"] is None:
        return "PROFILE_UNDEFINED"
    if row["baseline.total_points"] is None:
        return "BASELINE_UNDEFINED"
    return None


def pregame_features(all_rows: tuple, target: Target, cache: LeagueFitCache) -> dict[str, Any]:
    """Replay the production builder for one target from strictly earlier games and flatten it."""
    cutoff, history = history_for(all_rows, target)
    content = build_pregame_content(history, target, cache)
    if content["football_data_cutoff"] != cutoff:
        raise RuntimeError("production cutoff disagrees with the replay cutoff")
    league = cache.get(history, cutoff)
    return feature_row(content, target, cutoff, history, league)


def freeze_table(rows: list[dict[str, Any]]) -> str:
    """SHA-256 of the canonical feature table (sorted by game id)."""
    return sha256([canonical(r) for r in sorted(rows, key=lambda r: r["game_id"])])


__all__ = ["OBSERVED_CLEAN", "feature_row", "freeze_table", "pregame_features"]
