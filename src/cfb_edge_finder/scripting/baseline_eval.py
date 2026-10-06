"""Retrospective validation of the descriptive scoring baseline. PURE, football only.

*** WHAT IS UNDER TEST ***
`matchup.scoring_baseline` exactly as production computes it: for every
target game, `football.build_content` is run on the whole season's game log,
and -- as in production -- it sees only the games that finished before that
game's data cutoff (`gamelog.before`). Nothing here re-implements the
baseline, so what is validated is what is published.

*** THE TARGET IS THE FOOTBALL RESULT ***
Errors are predicted minus actual final score. No market total, sportsbook
line or price is read anywhere in this module (it is one of the football
modules the market-blindness test closes over).

The protocol, segments, candidate corrections and train/test splits were
pre-registered in docs/SCRIPT_ENGINE.md section 15.1 before any error was
computed. `fit_correction` is fitted on a training block only;
`apply_correction` is then scored on a later, untouched block.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np

from cfb_edge_finder.scripting.confidence import assess
from cfb_edge_finder.scripting.football import FootballPacket, LeagueFitCache, build_content
from cfb_edge_finder.scripting.gamelog import TeamGame, before, data_cutoff
from cfb_edge_finder.scripting.matchup import GameIdentity

OBSERVED_CLEAN = {
    "home": {"status": "OBSERVED", "qb_uncertain": False},
    "away": {"status": "OBSERVED", "qb_uncertain": False},
}
"""Historical injury lists do not exist. The evidence tier is reconstructed
as if availability had been observed with no quarterback doubt; the
as-published tier (availability NOT_OBSERVED) is recorded beside it."""


@dataclass(frozen=True)
class Target:
    game_id: str
    season: int
    week: int | None
    kickoff_utc: str
    home_id: str
    away_id: str
    home_name: str
    away_name: str
    neutral_site: bool
    conference_game: bool | None
    home_points: float
    away_points: float
    home_division: str | None
    away_division: str | None


def targets_from_rows(rows: Iterable[TeamGame], meta: dict[str, dict[str, Any]]) -> tuple[list[Target], dict[str, int]]:
    """Completed games with both teams in the log, oriented by `meta`.

    `meta[game_id]` = {"home_id", "away_id", "neutral_site", "conference_game"}
    from the same source as the rows (CFBD /games, or the ESPN schedule)."""
    by_game: dict[str, dict[str, TeamGame]] = defaultdict(dict)
    for row in rows:
        by_game[row.game_id][row.team_id] = row
    skipped = {"no_meta": 0, "missing_side": 0, "no_score": 0}
    out = []
    for game_id, sides in by_game.items():
        info = meta.get(game_id)
        if info is None:
            skipped["no_meta"] += 1
            continue
        home, away = sides.get(str(info["home_id"])), sides.get(str(info["away_id"]))
        if home is None or away is None:
            skipped["missing_side"] += 1
            continue
        if home.points_for is None or away.points_for is None:
            skipped["no_score"] += 1
            continue
        out.append(
            Target(
                game_id=game_id,
                season=home.season,
                week=home.week,
                kickoff_utc=home.kickoff_utc,
                home_id=home.team_id,
                away_id=away.team_id,
                home_name=home.team,
                away_name=away.team,
                neutral_site=bool(info.get("neutral_site")),
                conference_game=info.get("conference_game"),
                home_points=float(home.points_for),
                away_points=float(away.points_for),
                home_division=home.team_division,
                away_division=away.team_division,
            )
        )
    return sorted(out, key=lambda t: (t.kickoff_utc, t.game_id)), skipped


def _tercile(fit: Any, team_id: str, unit: str, higher_is_better: bool) -> str | None:
    """STRONG / MID / WEAK among FBS units of the same fit."""
    est = fit.get(team_id, unit)
    if est is None or est.adjusted is None:
        return None
    pool = sorted(
        e.adjusted for (t, u), e in fit.estimates.items() if u == unit and t in fit.fbs_teams and e.adjusted is not None
    )
    if len(pool) < 9:
        return None
    lo, hi = pool[len(pool) // 3], pool[(2 * len(pool)) // 3]
    v = est.adjusted
    if higher_is_better:
        return "STRONG" if v >= hi else "WEAK" if v < lo else "MID"
    return "STRONG" if v <= lo else "WEAK" if v > hi else "MID"


def evaluate_game(rows: tuple[TeamGame, ...], target: Target, cache: LeagueFitCache) -> dict[str, Any]:
    """One target game, rebuilt as production would have built it pregame."""
    ident = GameIdentity(
        event_id=f"retro_{target.game_id}",
        home_id=target.home_id,
        away_id=target.away_id,
        home_name=target.home_name,
        away_name=target.away_name,
        kickoff_utc=target.kickoff_utc,
        neutral_site=target.neutral_site,
        season=target.season,
    )
    packet = FootballPacket(
        identity=ident,
        game_key=None,
        rows=rows,
        availability=OBSERVED_CLEAN,
        identity_check={"status": "PASS", "orientation_swapped": False},
        freshness={"status": "FRESH"},
    )
    content = build_content(packet, cache)
    cutoff = content["football_data_cutoff"]
    names = {"home": target.home_name, "away": target.away_name}
    published = assess(
        content["matchup_profile"],
        names=names,
        availability={},
        identity_check=packet.identity_check,
        freshness=packet.freshness,
    )
    base = content["matchup_profile"]["scoring_baseline"]
    fit = cache.get(rows, cutoff).fits["points_per_game"]
    history = before(rows, cutoff)
    prior = {
        side: sum(1 for r in history if r.team_id == tid)
        for side, tid in (("home", target.home_id), ("away", target.away_id))
    }
    comps: dict[str, float | None] = {}
    for side, off_id, def_id in (("home", target.home_id, target.away_id), ("away", target.away_id, target.home_id)):
        o, d = fit.get(off_id, "offense"), fit.get(def_id, "defense")
        ok = fit.mu is not None and o is not None and d is not None and None not in (o.adjusted, d.adjusted)
        comps[f"{side}_o"] = round(o.adjusted - fit.mu, 4) if ok else None
        comps[f"{side}_d"] = round(d.adjusted - fit.mu, 4) if ok else None
    scripts = content["game_scripts"]["scripts"]
    primary = scripts[0] if scripts else None
    band = (primary or {}).get("outcome_shape", {}).get("bands", {}).get("total_points")
    actual_total = target.home_points + target.away_points
    hx = 0.0 if target.neutral_site else (fit.home_effect or 0.0)
    return {
        "game_id": target.game_id,
        "season": target.season,
        "week": target.week,
        "kickoff_utc": target.kickoff_utc,
        "cutoff": cutoff,
        "home": target.home_name,
        "away": target.away_name,
        "neutral_site": target.neutral_site,
        "conference_game": target.conference_game,
        "divisions": [target.home_division, target.away_division],
        "fcs_involved": target.home_division != "fbs" or target.away_division != "fbs",
        "prior_games": [prior["home"], prior["away"]],
        "pred_home": base.get("home_points"),
        "pred_away": base.get("away_points"),
        "pred_total": base.get("total_points"),
        "uncertainty_points": base.get("uncertainty_points"),
        "actual_home": target.home_points,
        "actual_away": target.away_points,
        "actual_total": actual_total,
        "mu": None if fit.mu is None else round(fit.mu, 4),
        "home_effect_applied": round(hx, 4),
        "naive_total": None if fit.mu is None else round(2 * fit.mu, 4),
        **comps,
        "units": {
            "home_off": _tercile(fit, target.home_id, "offense", True),
            "home_def": _tercile(fit, target.home_id, "defense", False),
            "away_off": _tercile(fit, target.away_id, "offense", True),
            "away_def": _tercile(fit, target.away_id, "defense", False),
        },
        "confidence_evidence": content["data_confidence"],
        "confidence_published": published["level"],
        "findings": sorted(f["code"] for f in content["matchup_findings"]),
        "primary": None if primary is None else primary["archetype"],
        "primary_total_environment": None if primary is None else primary["outcome_shape"]["total_environment"],
        "primary_total_band": band,
        "actual_in_primary_total_band": None if not band else band[0] <= actual_total <= band[1],
        "cutoff_check": all(r.kickoff_utc < target.kickoff_utc for r in history)
        and cutoff == data_cutoff(target.kickoff_utc),
    }


def evaluate(rows: Iterable[TeamGame], targets: list[Target], cache: LeagueFitCache | None = None) -> list[dict]:
    data = tuple(rows)
    cache = cache or LeagueFitCache()
    return [evaluate_game(data, t, cache) for t in targets]


# ---------------------------------------------------------------- statistics


def _q(values: list[float], q: float) -> float:
    return float(np.quantile(np.asarray(values), q)) if values else math.nan


def error_stats(records: list[dict[str, Any]], pred: str = "pred_total", actual: str = "actual_total") -> dict:
    errs = [r[pred] - r[actual] for r in records if r.get(pred) is not None]
    n = len(errs)
    if n == 0:
        return {"n": 0}
    a = np.asarray(errs, dtype=float)
    absa = np.abs(a)
    sd = float(a.std(ddof=1)) if n > 1 else math.nan
    half = 1.96 * sd / math.sqrt(n) if n > 1 else math.nan
    return {
        "n": n,
        "bias": round(float(a.mean()), 3),
        "bias_ci95": [round(float(a.mean()) - half, 3), round(float(a.mean()) + half, 3)],
        "mae": round(float(absa.mean()), 3),
        "rmse": round(float(math.sqrt((a**2).mean())), 3),
        "median_abs_error": round(float(np.median(absa)), 3),
        "abs_error_p50": round(_q(list(absa), 0.5), 3),
        "abs_error_p80": round(_q(list(absa), 0.8), 3),
        "abs_error_p90": round(_q(list(absa), 0.9), 3),
    }


def block_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [r for r in records if r.get("pred_total") is not None]
    home = error_stats(usable, "pred_home", "actual_home")
    away = error_stats(usable, "pred_away", "actual_away")
    return {
        "total": error_stats(usable),
        "home_points": {k: home.get(k) for k in ("n", "bias", "bias_ci95", "mae")},
        "away_points": {k: away.get(k) for k in ("n", "bias", "bias_ci95", "mae")},
        "naive_league_total": {
            k: v for k, v in error_stats(usable, "naive_total").items() if k in ("n", "bias", "mae", "rmse")
        },
        "excluded_no_baseline": len(records) - len(usable),
    }


def band_offset_diagnostics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the PRIMARY script's total band sits, versus where totals landed.

    For each archetype whose PRIMARY states a total band: the mean of
    (actual - baseline), the mean of (band centre - baseline), and how often
    the actual total fell inside the band. A band centred far from where
    totals actually land relative to the baseline is mis-placed, whatever
    the baseline's own accuracy."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("pred_total") is not None and r.get("primary_total_band"):
            by[r["primary"]].append(r)
    out = {}
    for arch, rs in sorted(by.items()):
        diff = np.array([r["actual_total"] - r["pred_total"] for r in rs])
        centre = np.array([(r["primary_total_band"][0] + r["primary_total_band"][1]) / 2 - r["pred_total"] for r in rs])
        inside = sum(1 for r in rs if r["actual_in_primary_total_band"])
        out[arch] = {
            "n": len(rs),
            "actual_minus_baseline_mean": round(float(diff.mean()), 2),
            "band_centre_minus_baseline_mean": round(float(centre.mean()), 2),
            "inside_band": inside,
            "inside_rate": round(inside / len(rs), 3),
        }
    return out


def residual_profile(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Distribution of actual - baseline (what a calibrated band would have to cover), and the home margin."""
    usable = [r for r in records if r.get("pred_total") is not None]
    if not usable:
        return {"n": 0}
    res = np.array([r["actual_total"] - r["pred_total"] for r in usable])
    site = [r for r in usable if not r["neutral_site"]]
    corr = float(np.corrcoef([r["pred_total"] for r in usable], [r["actual_total"] for r in usable])[0, 1])
    return {
        "n": len(usable),
        "actual_minus_baseline_sd": round(float(res.std(ddof=1)), 2),
        "actual_minus_baseline_p10_p90": [round(float(q), 1) for q in np.percentile(res, [10, 90])],
        "actual_minus_baseline_p25_p75": [round(float(q), 1) for q in np.percentile(res, [25, 75])],
        "corr_baseline_actual_total": round(corr, 3),
        "home_site_games": len(site),
        "home_effect_applied_mean": round(float(np.mean([r["home_effect_applied"] for r in site])), 2)
        if site
        else None,
        "predicted_home_margin_mean": round(float(np.mean([r["pred_home"] - r["pred_away"] for r in site])), 2)
        if site
        else None,
        "actual_home_margin_mean": round(float(np.mean([r["actual_home"] - r["actual_away"] for r in site])), 2)
        if site
        else None,
    }


def _bucket_prior(r: dict) -> str:
    g = min(r["prior_games"])
    return "0" if g == 0 else "1" if g == 1 else "2" if g == 2 else "3-4" if g <= 4 else "5-7" if g <= 7 else "8+"


def _bucket_total(r: dict) -> str:
    t = r["pred_total"]
    return "<45" if t < 45 else "45-55" if t < 55 else "55-65" if t < 65 else "65-75" if t < 75 else "75+"


def _units(r: dict, key: str) -> str | None:
    return r["units"].get(key)


SEGMENTS: dict[str, Callable[[dict], str | None]] = {
    "prior_games_min": _bucket_prior,
    "confidence_evidence": lambda r: r["confidence_evidence"],
    "confidence_published": lambda r: r["confidence_published"],
    "predicted_total": _bucket_total,
    "both_offenses_strong": lambda r: str(_units(r, "home_off") == "STRONG" and _units(r, "away_off") == "STRONG"),
    "both_defenses_strong": lambda r: str(_units(r, "home_def") == "STRONG" and _units(r, "away_def") == "STRONG"),
    "strong_off_vs_strong_def": lambda r: str(
        (_units(r, "home_off") == "STRONG" and _units(r, "away_def") == "STRONG")
        or (_units(r, "away_off") == "STRONG" and _units(r, "home_def") == "STRONG")
    ),
    "strong_off_vs_weak_def": lambda r: str(
        (_units(r, "home_off") == "STRONG" and _units(r, "away_def") == "WEAK")
        or (_units(r, "away_off") == "STRONG" and _units(r, "home_def") == "WEAK")
    ),
    "conference_game": lambda r: None if r["conference_game"] is None else str(bool(r["conference_game"])),
    "fcs_involved": lambda r: str(r["fcs_involved"]),
    "primary_total_environment": lambda r: r["primary_total_environment"] or "NO_SCRIPT",
}


def segment_stats(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    usable = [r for r in records if r.get("pred_total") is not None]
    out: dict[str, dict[str, Any]] = {}
    for name, key in SEGMENTS.items():
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in usable:
            k = key(r)
            if k is not None:
                groups[k].append(r)
        out[name] = {k: error_stats(v) for k, v in sorted(groups.items())}
    return out


def _team_rows(records: list[dict[str, Any]]) -> list[tuple[float, float, float, float, str]]:
    """Per team-game: (actual - mu - h*x, o, d, prediction residual, defense tercile of the opponent)."""
    out = []
    for r in records:
        if r.get("pred_total") is None or r.get("home_o") is None or r.get("away_o") is None:
            continue
        hx = r["home_effect_applied"]
        out.append(
            (
                r["actual_home"] - r["mu"] - hx,
                r["home_o"],
                r["home_d"],
                r["pred_home"] - r["actual_home"],
                r["units"]["away_def"] or "NA",
            )
        )
        out.append(
            (
                r["actual_away"] - r["mu"] + hx,
                r["away_o"],
                r["away_d"],
                r["pred_away"] - r["actual_away"],
                r["units"]["home_def"] or "NA",
            )
        )
    return out


def _ols(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ beta
    dof = max(len(y) - x.shape[1], 1)
    s2 = float(resid @ resid) / dof
    cov = s2 * np.linalg.inv(x.T @ x)
    return beta, np.sqrt(np.diag(cov))


def defense_diagnostics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Is defensive strength double-counted (over-credited) in the additive baseline?"""
    rows = _team_rows(records)
    if len(rows) < 20:
        return {"n_team_games": len(rows)}
    y = np.array([r[0] for r in rows])
    x = np.column_stack([np.ones(len(rows)), [r[1] for r in rows], [r[2] for r in rows]])
    beta, se = _ols(y, x)
    by_def: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_def[r[4]].append(r[3])
    return {
        "n_team_games": len(rows),
        "model": "actual - mu - h*x = c + b_o * (adj_off - mu) + b_d * (adj_def_allowed - mu)",
        "intercept": [round(float(beta[0]), 3), round(float(se[0]), 3)],
        "b_offense": [round(float(beta[1]), 3), round(float(se[1]), 3)],
        "b_defense": [round(float(beta[2]), 3), round(float(se[2]), 3)],
        "reading": "b = 1 means the baseline credits the unit exactly; b < 1 over-credits it (double counting), "
        "b > 1 under-credits it. Values are [estimate, standard error].",
        "team_points_bias_by_opponent_defense": {
            k: {"n": len(v), "bias": round(float(np.mean(v)), 3)} for k, v in sorted(by_def.items())
        },
    }


# ------------------------------------------------- pre-registered corrections

CORRECTIONS = ("linear_total", "components")


def fit_correction(method: str, train: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [r for r in train if r.get("pred_total") is not None]
    if method == "linear_total":
        y = np.array([r["actual_total"] for r in usable])
        x = np.column_stack([np.ones(len(usable)), [r["pred_total"] for r in usable]])
        beta, se = _ols(y, x)
        return {
            "method": method,
            "a": float(beta[0]),
            "b": float(beta[1]),
            "se": [float(s) for s in se],
            "n_train": len(usable),
        }
    if method == "components":
        rows = _team_rows(usable)
        y = np.array([r[0] for r in rows])
        x = np.column_stack([np.ones(len(rows)), [r[1] for r in rows], [r[2] for r in rows]])
        beta, se = _ols(y, x)
        return {
            "method": method,
            "c": float(beta[0]),
            "b_o": float(beta[1]),
            "b_d": float(beta[2]),
            "se": [float(s) for s in se],
            "n_train": len(rows),
        }
    raise ValueError(method)


def apply_correction(model: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in records:
        if r.get("pred_total") is None:
            continue
        if model["method"] == "linear_total":
            total = model["a"] + model["b"] * r["pred_total"]
            out.append({**r, "pred_total": total})
            continue
        if r.get("home_o") is None or r.get("away_o") is None:
            continue
        hx = r["home_effect_applied"]
        home = r["mu"] + hx + model["c"] + model["b_o"] * r["home_o"] + model["b_d"] * r["home_d"]
        away = r["mu"] - hx + model["c"] + model["b_o"] * r["away_o"] + model["b_d"] * r["away_d"]
        out.append(
            {
                **r,
                "pred_home": max(home, 0.0),
                "pred_away": max(away, 0.0),
                "pred_total": max(home, 0.0) + max(away, 0.0),
            }
        )
    return out


GATE = {
    "min_n": 300,
    "max_abs_total_bias": 1.5,
    "max_abs_side_bias": 1.0,
    "min_mae_improvement": 0.03,
    "segment_min_n": 100,
    "segment_max_abs_bias": 3.0,
}


def gate_checks(corrected: list[dict[str, Any]], raw: list[dict[str, Any]]) -> dict[str, Any]:
    """The retrospective half of the promotion gate (docs section 15.2, point 2).

    Points 3-5 (bands from the calibrated error distribution, prospective
    confirmation, a recorded change) cannot be met by a retrospective run;
    `passes_retrospective_criteria` is necessary, never sufficient."""
    usable_raw = [r for r in raw if r.get("pred_total") is not None]
    keep = {r["game_id"] for r in corrected}
    raw_same = [r for r in usable_raw if r["game_id"] in keep]
    s = block_stats(corrected)
    t = s["total"]
    raw_mae = error_stats(raw_same)["mae"] if raw_same else math.nan
    naive_mae = error_stats(raw_same, "naive_total")["mae"] if raw_same else math.nan
    lo, hi = t["bias_ci95"]
    bad_segments = []
    for seg, groups in segment_stats(corrected).items():
        for key, st in groups.items():
            if st.get("n", 0) >= GATE["segment_min_n"]:
                clo, chi = st["bias_ci95"]
                if abs(st["bias"]) > GATE["segment_max_abs_bias"] and (clo > 0 or chi < 0):
                    bad_segments.append({"segment": seg, "group": key, "n": st["n"], "bias": st["bias"]})
    checks = {
        "n": t["n"] >= GATE["min_n"],
        "total_bias": abs(t["bias"]) <= GATE["max_abs_total_bias"] and lo <= 0 <= hi,
        "home_bias": abs(s["home_points"]["bias"]) <= GATE["max_abs_side_bias"],
        "away_bias": abs(s["away_points"]["bias"]) <= GATE["max_abs_side_bias"],
        "mae_vs_raw": t["mae"] <= (1 - GATE["min_mae_improvement"]) * raw_mae,
        "mae_vs_naive": t["mae"] <= (1 - GATE["min_mae_improvement"]) * naive_mae,
        "segments": not bad_segments,
    }
    return {
        "n": t["n"],
        "checks": checks,
        "passes_retrospective_criteria": all(checks.values()),
        "mae": t["mae"],
        "raw_mae_same_games": raw_mae,
        "naive_mae_same_games": naive_mae,
        "bias": t["bias"],
        "bias_ci95": t["bias_ci95"],
        "failing_segments": bad_segments,
    }
