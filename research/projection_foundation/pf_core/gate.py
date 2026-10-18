"""Projection Quality Gate — every projection is checked before publication.

Status: PASS (no structural concern) / REVIEW (unusual assumption or material uncertainty) /
BLOCK (invalid inputs, mathematical inconsistency or verified structural defect).

Checks are role-aware: a projection is compared with *that player's own* recent opportunity
and production, not with one universal threshold, so a 9-target projection is normal for a WR1
and a red flag for a TE3.

The gate never substitutes a market price for the model; a BLOCKed projection is withheld.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np

PASS, REVIEW, BLOCK = "PASS", "REVIEW", "BLOCK"
_ORDER = {PASS: 0, REVIEW: 1, BLOCK: 2}

# Hard physical / historical ceilings per (sport, family): a mean above these is impossible
# in practice and indicates a units or mapping bug. Generous on purpose.
HARD_MAX_MEAN = {
    ("NFL", "passing_yards"): 500, ("NFL", "passing_attempts"): 65, ("NFL", "completions"): 45,
    ("NFL", "passing_tds"): 5.5, ("NFL", "targets"): 18, ("NFL", "receptions"): 14,
    ("NFL", "receiving_yards"): 200, ("NFL", "carries"): 35, ("NFL", "rushing_yards"): 200,
    ("NFL", "anytime_td"): 2.0, ("NFL", "team_points"): 50,
    ("CFB", "passing_yards"): 550, ("CFB", "rushing_yards"): 250, ("CFB", "receiving_yards"): 220,
    ("CFB", "team_points"): 70, ("CFB", "game_total"): 100,
    ("NBA", "minutes"): 46, ("NBA", "points"): 55, ("NBA", "rebounds"): 22, ("NBA", "assists"): 17,
    ("NBA", "threes"): 9, ("NBA", "team_points"): 145,
    ("MLB", "batters_faced"): 34, ("MLB", "pitcher_strikeouts"): 14, ("MLB", "outs"): 27,
    ("MLB", "hits"): 3.0, ("MLB", "total_bases"): 4.5, ("MLB", "home_runs"): 1.0,
    ("MLB", "plate_appearances"): 6.0, ("MLB", "team_runs"): 12, ("MLB", "runs"): 2.0, ("MLB", "rbis"): 2.5,
    ("MLB", "hits_allowed"): 13, ("MLB", "walks"): 8, ("MLB", "earned_runs"): 10, ("MLB", "runs_allowed"): 10,
    ("MLB", "game_total"): 20,
    ("NHL", "toi_minutes"): 32, ("NHL", "shots"): 7.5, ("NHL", "goals"): 1.2, ("NHL", "assists"): 1.6,
    ("NHL", "points"): 2.4, ("NHL", "saves"): 45, ("NHL", "team_goals"): 6.5,
}

# Opponent-adjustment reliability is sport-specific: CFB plays 12 games, so 2 games is already a sixth of a season and
# its legitimate opponent factors are wider (FBS vs FCS); NBA/NHL/MLB play enough that 5 games is the floor.
OPP_MIN_GAMES = {"NFL": 3, "CFB": 2, "NBA": 5, "MLB": 5, "NHL": 5}
OPP_EXTREME_LOG = {"CFB": 0.6}

# Max age (hours) of the newest input before the projection is considered stale.
MAX_STALENESS_H = {"NFL": 24 * 8, "CFB": 24 * 8, "NBA": 48, "MLB": 36, "NHL": 48}


@dataclass
class GateInput:
    sport: str
    family: str
    entity_id: str | None
    entity_name: str | None
    team: str | None
    game_id: str | None
    game_start_utc: str | None
    mean: float | None
    identity_confidence: float | None = 1.0      # 1.0 exact id join; <1 fuzzy name match
    team_verified: bool | None = True             # roster/lineup confirms the team (None = not checked; no finding)
    data_asof_utc: str | None = None              # newest input timestamp used
    injury_status: str | None = None              # ACTIVE/PROBABLE/QUESTIONABLE/DOUBTFUL/OUT/UNKNOWN
    expected_opportunity: float | None = None     # minutes, targets, TOI, PA, batters faced...
    opportunity_history: Sequence[float] | None = None   # same unit, most recent last
    stat_history: Sequence[float] | None = None
    opponent_sample_games: int | None = None
    opponent_factor: float | None = None          # multiplicative opponent adjustment applied
    support_lo: int | None = None
    probs: Sequence[float] | None = None          # pmf on support_lo..
    thresholds: dict[str, float] | None = None    # {"k": P(X>=k)}
    sd: float | None = None
    model_n_obs: int | None = None                # games of this entity's own evidence
    participation: str | None = None              # pf_core.availability participation class (None = not supplied)
    participation_reasons: Sequence[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Finding:
    check: str
    severity: str
    what: str
    inputs: dict[str, Any]
    category: str          # data_quality | model_assumption | uncertainty | mathematical
    action: str


def _parse(ts: str | None):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _robust_band(hist: Sequence[float]):
    h = np.asarray([x for x in hist if x is not None and np.isfinite(x)], float)
    if len(h) == 0:
        return None
    med = float(np.median(h))
    mad = float(np.median(np.abs(h - med))) * 1.4826
    return med, mad, float(np.quantile(h, 0.1)), float(np.quantile(h, 0.9)), float(h.max()), len(h)


def evaluate(gi: GateInput, now_utc: str | None = None) -> dict[str, Any]:
    f: list[Finding] = []
    add = lambda *a: f.append(Finding(*a))  # noqa: E731

    # 1 identity
    if not gi.entity_id:
        add("identity", BLOCK, "No stable player/team id", {"name": gi.entity_name}, "data_quality",
            "Resolve the id in the identity map; never publish a name-only join.")
    elif gi.identity_confidence is not None and gi.identity_confidence < 0.99:
        sev = BLOCK if gi.identity_confidence < 0.9 else REVIEW
        add("identity", sev, f"Identity matched with confidence {gi.identity_confidence:.2f}",
            {"confidence": gi.identity_confidence}, "data_quality", "Confirm the crosswalk entry manually.")
    # 2 team
    if gi.team_verified is False:
        add("team", BLOCK, "Player's team is not confirmed by the latest roster/lineup",
            {"team": gi.team}, "data_quality", "Refresh roster; check for trade/release.")
    # 3 freshness / time travel
    gs, asof = _parse(gi.game_start_utc), _parse(gi.data_asof_utc)
    if gs and asof:
        if asof >= gs:
            add("freshness", BLOCK, "Inputs are timestamped at or after game start (look-ahead)",
                {"asof": gi.data_asof_utc, "start": gi.game_start_utc}, "data_quality",
                "Rebuild the projection from pre-game inputs only.")
        else:
            age_h = ((_parse(now_utc) or datetime.now(timezone.utc)) - asof).total_seconds() / 3600
            lim = MAX_STALENESS_H.get(gi.sport, 72)
            if age_h > lim:
                add("freshness", REVIEW, f"Newest input is {age_h:.0f}h old (limit {lim}h)",
                    {"age_hours": round(age_h, 1)}, "data_quality", "Re-run ingestion before publishing.")
    elif gs and not asof:
        add("freshness", REVIEW, "Input timestamp unknown", {}, "data_quality", "Record data_asof on every projection.")
    # 4 injury
    st = (gi.injury_status or "").upper()
    if st in ("OUT", "INACTIVE", "IR", "SUSPENDED"):
        add("injury", BLOCK, f"Player listed {st}", {"status": st}, "data_quality",
            "Withhold; redistribute opportunity to teammates.")
    elif st in ("DOUBTFUL", "QUESTIONABLE", "GTD", "DAY-TO-DAY"):
        add("injury", REVIEW, f"Player listed {st}: participation uncertain", {"status": st}, "uncertainty",
            "Show as conditional on playing or widen with a participation hurdle.")
    elif st in ("", "UNKNOWN") and gi.sport in ("NFL", "NBA"):
        add("injury", REVIEW, "No point-in-time injury status available", {}, "uncertainty",
            "Attach injury report snapshot; until then label participation as assumed.")
    # 4b participation (point-in-time availability feed)
    if gi.participation == "CONFIRMED_OUT":
        add("participation", BLOCK, "Confirmed out / inactive before the game", {"reasons": list(gi.participation_reasons or [])},
            "data_quality", "Withhold; redistribute opportunity to teammates.")
    elif gi.participation == "LIKELY_OUT":
        add("participation", REVIEW, "Likely out (doubtful or stale exclusion)", {"reasons": list(gi.participation_reasons or [])},
            "uncertainty", "Publish only as conditional on playing, with the participation probability.")
    elif gi.participation in ("UNCERTAIN", "UNVERIFIED"):
        add("participation", REVIEW, "Participation not confirmed" if gi.participation == "UNCERTAIN" else
            "No availability observation: player is unverified, not assumed active",
            {"reasons": list(gi.participation_reasons or [])}, "uncertainty",
            "Label conditional on playing; refresh availability capture before start.")
    # 5/6 opportunity and role consistency
    if gi.expected_opportunity is not None:
        if not np.isfinite(gi.expected_opportunity) or gi.expected_opportunity <= 0:
            add("participation", BLOCK, "Expected opportunity is zero/invalid", {"opp": gi.expected_opportunity},
                "mathematical", "Do not publish a production projection without opportunity.")
        elif gi.opportunity_history is not None:
            band = _robust_band(gi.opportunity_history)
            if band is None or band[5] < 3:
                add("role", REVIEW, "Fewer than 3 prior games establish this role",
                    {"games": 0 if band is None else band[5]}, "uncertainty",
                    "Widen the distribution; rely on positional prior.")
            else:
                med, mad, p10, p90, mx, n = band
                x = gi.expected_opportunity
                if x > max(p90 * 1.25, med + 3 * max(mad, 0.1 * med + 0.5)):
                    add("role", REVIEW, f"Projected opportunity {x:.1f} far above recent role (median {med:.1f}, p90 {p90:.1f})",
                        {"projected": x, "median": med, "p90": p90, "n": n}, "model_assumption",
                        "Confirm the role change (injury to teammate, depth chart) or revert toward history.")
                elif x < min(p10 * 0.75, med - 3 * max(mad, 0.1 * med + 0.5)):
                    add("role", REVIEW, f"Projected opportunity {x:.1f} far below recent role (median {med:.1f}, p10 {p10:.1f})",
                        {"projected": x, "median": med, "p10": p10, "n": n}, "model_assumption",
                        "Confirm a reduced role or a stale/decayed input (cf. NFL defect D1).")
    # 8 opponent adjustment reliability
    if gi.opponent_factor is not None:
        if gi.opponent_sample_games is not None and gi.opponent_sample_games < OPP_MIN_GAMES.get(gi.sport, 3) and abs(math.log(max(gi.opponent_factor, 1e-6))) > 0.05:
            add("opponent", REVIEW, f"Opponent adjustment ×{gi.opponent_factor:.2f} rests on {gi.opponent_sample_games} games",
                {"factor": gi.opponent_factor, "games": gi.opponent_sample_games}, "uncertainty",
                "Shrink the opponent factor harder toward 1.0 early in the season.")
        if abs(math.log(max(gi.opponent_factor, 1e-6))) > OPP_EXTREME_LOG.get(gi.sport, 0.35):
            add("opponent", REVIEW, f"Opponent adjustment ×{gi.opponent_factor:.2f} is extreme",
                {"factor": gi.opponent_factor}, "model_assumption", "Check schedule adjustment and opponent data.")
    # 9 plausibility
    if gi.mean is None or not np.isfinite(gi.mean):
        add("plausibility", BLOCK, "Projection mean missing or not finite", {}, "mathematical", "Fix the model output.")
    else:
        if gi.mean < 0 and gi.family not in ("rushing_yards", "margin", "plus_minus"):
            add("plausibility", BLOCK, "Negative mean for a non-negative statistic", {"mean": gi.mean}, "mathematical",
                "Fix the link function / units.")
        hm = HARD_MAX_MEAN.get((gi.sport, gi.family))
        if hm is not None and gi.mean > hm:
            add("plausibility", BLOCK, f"Mean {gi.mean:.2f} exceeds the hard ceiling {hm}", {"mean": gi.mean, "ceiling": hm},
                "data_quality", "Likely units/mapping error; inspect inputs.")
        if gi.stat_history is not None:
            band = _robust_band(gi.stat_history)
            if band and band[5] >= 5 and gi.mean > max(band[4] * 1.15, band[3] * 1.6) and gi.mean > 1:
                add("plausibility", REVIEW, f"Mean {gi.mean:.1f} exceeds anything in {band[5]} prior games (max {band[4]:.1f})",
                    {"mean": gi.mean, "max": band[4], "p90": band[3]}, "model_assumption",
                    "Check for an upstream outlier feature or opponent factor stacking.")
    # 10 distribution coherence
    if gi.probs is not None:
        p = np.asarray(gi.probs, float)
        if (not np.isfinite(p).all()) or (p < -1e-9).any() or abs(p.sum() - 1) > 1e-4:
            add("distribution", BLOCK, "Probability distribution invalid (negative, NaN or not summing to 1)",
                {"sum": float(np.nansum(p))}, "mathematical", "Regenerate the distribution.")
        elif gi.mean is not None and gi.support_lo is not None:
            dm = float(np.dot(np.arange(gi.support_lo, gi.support_lo + len(p)), p))
            tol = max(0.05 * abs(gi.mean), 0.15)
            gap = abs(dm - gi.mean)
            if gap > tol:
                # small gaps arise legitimately when a distribution is floored (e.g. at 0); large ones are a defect
                sev = BLOCK if gap > max(0.15 * abs(gi.mean), 0.5) else REVIEW
                add("distribution", sev, f"Distribution mean {dm:.2f} disagrees with published mean {gi.mean:.2f}",
                    {"dist_mean": dm, "mean": gi.mean}, "mathematical", "Publish the mean of the published distribution.")
    if gi.thresholds:
        ks = sorted(gi.thresholds, key=lambda k: float(k))
        ps = [gi.thresholds[k] for k in ks]
        if any(not (0 <= x <= 1) for x in ps):
            add("distribution", BLOCK, "Threshold probability outside [0,1]", {"thresholds": gi.thresholds}, "mathematical",
                "Regenerate thresholds from the pmf.")
        elif any(ps[i + 1] > ps[i] + 1e-9 for i in range(len(ps) - 1)):
            add("distribution", BLOCK, "Alternate thresholds are not monotone (P(X>=k) must fall as k rises)",
                {"thresholds": gi.thresholds}, "mathematical", "Derive every rung from one distribution.")
    # 12 confidence
    if gi.model_n_obs is not None and gi.model_n_obs < 3:
        add("confidence", REVIEW, f"Only {gi.model_n_obs} games of the player's own evidence", {"n": gi.model_n_obs},
            "uncertainty", "Label as prior-driven; show the wide range.")
    if gi.sd is not None and gi.mean and gi.mean > 0 and gi.sd / gi.mean > 2.5 and gi.family not in ("anytime_td", "goals", "home_runs"):
        add("confidence", REVIEW, f"Very wide distribution (cv {gi.sd / gi.mean:.1f})", {"sd": gi.sd, "mean": gi.mean},
            "uncertainty", "Present the range, not the point.")

    status = PASS
    for x in f:
        if _ORDER[x.severity] > _ORDER[status]:
            status = x.severity
    return {"status": status, "findings": [x.__dict__ for x in f]}


def team_reconciliation(player_sum: float, team_mean: float, label: str, review_tol: float = 0.15,
                        block_tol: float = 0.35) -> dict[str, Any]:
    """Player projections summed over a team must reconcile with the team projection
    (e.g. receiving yards vs team passing yards; player points vs team points)."""
    if not team_mean or not np.isfinite(team_mean) or team_mean <= 0:
        return {"status": REVIEW, "findings": [Finding("team_consistency", REVIEW, f"No team projection for {label}", {},
                                                       "data_quality", "Project the team total first.").__dict__]}
    r = player_sum / team_mean
    if abs(r - 1) > block_tol:
        sev = BLOCK
    elif abs(r - 1) > review_tol:
        sev = REVIEW
    else:
        return {"status": PASS, "findings": [], "ratio": r}
    return {"status": sev, "ratio": r, "findings": [Finding(
        "team_consistency", sev, f"{label}: players sum to {player_sum:.1f} vs team {team_mean:.1f} (×{r:.2f})",
        {"player_sum": player_sum, "team_mean": team_mean}, "model_assumption",
        "Normalise player shares to the team projection or fix the missing/duplicate player.").__dict__]}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    from collections import Counter
    c = Counter(r["status"] for r in results)
    checks = Counter((x["check"], x["severity"]) for r in results for x in r["findings"])
    return {"n": len(results), "status_counts": dict(c),
            "check_counts": {f"{k[0]}:{k[1]}": v for k, v in checks.most_common()}}
