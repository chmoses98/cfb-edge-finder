"""Plain-English layer of the CFB report: experiment write-ups, readiness rules, priorities and the markdown.

Numbers are always read from the stage results (S); nothing here is hand-typed except interpretation."""
from __future__ import annotations

import numpy as np

FAMILY_META = {
    "team_points": {"opportunity": "plays", "population": "FBS-vs-FBS regular-season team-games (a team's offence vs the opponent defence)"},
    "plays": {"opportunity": None, "population": "FBS-vs-FBS regular-season team-games; plays = rush att + pass att (box)"},
    "team_passing_yards": {"opportunity": "pass attempts", "population": "FBS-vs-FBS regular-season team-games (net passing yards)"},
    "team_rushing_yards": {"opportunity": "rush attempts", "population": "FBS-vs-FBS regular-season team-games (NCAA rushing incl. sacks)"},
    "points_per_play": {"opportunity": "plays", "population": "FBS-vs-FBS regular-season team-games (efficiency, x100 lattice)"},
    "epa_per_play": {"opportunity": "plays", "population": "FBS-vs-FBS regular-season team-games, 2016-2025 only (no EPA in the 2026 source)"},
    "success_rate": {"opportunity": "plays", "population": "FBS-vs-FBS regular-season team-games (x100 lattice)"},
}
PLAYER_POP = {
    "passer": "team's leading passer of its previous game (>=10 att), evaluated when he records >=1 attempt",
    "rusher": ">=5 carries in one of the team's previous 3 games; absent from the box = 0",
    "receiver": ">=1 reception in 2 of the team's previous 4 games; absent from the box = 0",
}
INSUFFICIENT = {
    "targets": "ESPN/CFBD player box scores carry no targets in any season; receiving opportunity can only be measured as "
               "completions x reception share. Remedy: charting data (e.g. PFF college) or play-by-play target attribution from "
               "the sportsdataverse espn_cfb_pbp release (pass plays carry the intended receiver in ESPN text) - a parser job.",
    "player_week1": "Week-1 player projections need a depth chart / returning-player table (no in-season role exists yet). "
                    "Remedy: preseason rosters (sportsdataverse espn_cfb_rosters) joined to last season's usage.",
}

# explicit overrides; every other family gets auto_readiness() from its holdout rows
_PLAYER_WHY = ("Historical walk-forward accuracy and calibration pass (beats the season average on 2023-24 and 2025, 80% "
               "coverage ~0.80-0.84), but participation cannot be verified pre-game: there is no historical injury/depth-chart "
               "data, receivers/rushers absent from the box are scored as 0 (DNP and zero-catch games are indistinguishable), "
               "and no prospective record exists yet. Display as research projections conditional on playing; build the "
               "prospective record via projection-data.")
_LINE_WHY = ("Point and distribution accuracy pass on holdout and 2025 (beats baseline and the retired engine, calibrated "
             "coverage), and the model matches the archived v2 research models, but it stays behind the closing line and its "
             "probabilities AT the closing number are less informative than the line itself (Brier ~0.26 vs 0.25 for a coin "
             "flip). Show distributions; never present cover/over probabilities at market numbers as edges.")
READINESS: dict[str, tuple[str, str]] = {
    "margin": ("SHADOW VALIDATION", _LINE_WHY),
    "game_total": ("SHADOW VALIDATION", _LINE_WHY),
    **{f: ("SHADOW VALIDATION", _PLAYER_WHY) for f in ("passing_attempts", "passing_yards", "carries", "rushing_yards",
                                                        "receptions", "receiving_yards")},
    "epa_per_play": ("INSUFFICIENT DATA",
                     "Validated 2016-2025 on CFBD EPA, but the live 2026 source (ESPN game log) carries no EPA, so it cannot be "
                     "projected forward. Remedy: compute EPA from the sportsdataverse espn_cfb_pbp release (has an EPA column) "
                     "inside the weekly job, or restore a CFBD key."),
}
PRIORITIES: list[dict] = [
    {"rank": 1, "item": "Archive pre-game QB/injury availability every week and model QB changes",
     "impact": "Largest identifiable bias: when the starting passer changes the team is over-projected by 2-3 points (market only 1.3-1.8)",
     "evidence": "CFB qb_change investigation (dev/val)", "complexity": "low (availability.json already captured; needs weekly archive + a feature)",
     "data": "ESPN injuries snapshot (keyless), archived prospectively from 2026"},
    {"rank": 2, "item": "Weeks 1-4: roster-turnover-aware preseason prior (transfers, defensive returning production)",
     "impact": "Week-1 gap to the closing line is the biggest of the season (validation team-points MAE +1.6 vs market)",
     "evidence": "by-week table; talent/offensive returning-production prior did NOT help (CFB-EARLY-01)",
     "complexity": "medium", "data": "transfer portal history (only 2025 cached), defensive returning production (not in source)"},
    {"rank": 3, "item": "Replace script-engine defensive ranks with carried, schedule-adjusted ratings; suppress ranks until the schedule graph connects",
     "impact": "Week-2 rank correlation with true defensive quality 0.36 -> 0.62; FCS-flattered defences fixed",
     "evidence": "CFB-DEF-01", "complexity": "low-medium (production change - owner decision)", "data": "none new"},
    {"rank": 4, "item": "Rate FCS teams individually from FCS-vs-FCS results", "impact": "FCS games MAE ~11.6 vs 9.5 FBS-vs-FBS",
     "evidence": "fcs segments", "complexity": "medium", "data": "sportsdataverse cfb_schedules (all divisions)"},
    {"rank": 5, "item": "Live EPA/success features from ESPN play-by-play inside the weekly job", "impact": "epa_per_play family forward + richer G features",
     "evidence": "inventory", "complexity": "medium", "data": "sportsdataverse espn_cfb_pbp (~100 MB/season)"},
    {"rank": 6, "item": "Receiver targets from play-by-play text", "impact": "true receiving opportunity (targets) instead of completions",
     "evidence": "player families", "complexity": "medium-high", "data": "espn_cfb_pbp"},
    {"rank": 7, "item": "Build a prospective track record from the projection-data branch", "impact": "the only honest test of forward readiness",
     "evidence": "-", "complexity": "low (workflow exists)", "data": "own archive"},
    {"rank": 8, "item": "Era/rule-change handling (2023 clock rule cut plays)", "impact": "LightGBM validation bias +1.2 pts; plays drift",
     "evidence": "E_lgbm validation bias", "complexity": "low", "data": "none new"},
]
PF_CORE_FINDINGS: list[dict] = [
    {"where": "pit.rolling_ridge_ratings", "finding": "No group/hierarchical offsets: an entity with 1-3 rows is shrunk to the "
     "league average. In CFB that rates every FCS opponent like an average FBS team, flattering whoever played them "
     "(FCS-game MAE +1.4 to +2.0 points vs pooling).", "workaround": "Relabel all non-FBS teams to one 'FCS' entity before calling."},
    {"where": "pit.rolling_ridge_ratings", "finding": "Fits every block present over ALL earlier rows (no history window) with a "
     "dense one-hot design; cost grows with history (~0.3-0.5 s per refit here).", "workaround": "Season-blocked relabelling "
     "(prior seasons collapsed into one block) and per-season calls; ratings cached on disk."},
    {"where": "pit.rolling_ridge_ratings", "finding": "halflife_days decays prior SEASONS too (on top of prior_season_decay); "
     "with CFB this costs ~0.5 points of MAE (9.63 -> 10.10).", "workaround": "Not used; documented as a failed experiment."},
    {"where": "pit.rolling_ridge_ratings", "finding": "The home-advantage coefficient is ridge-penalised with the team effects "
     "(sklearn Ridge penalises every coefficient). Harmless at the small alphas chosen here, biased toward 0 at large alpha.",
     "workaround": "Small alpha (0.5-5)."},
    {"where": "gate.evaluate (opponent check)", "finding": "Thresholds are not sport-aware: |log factor| > 0.35 is 'extreme' and any "
     "factor with < 3 opponent games is flagged; in CFB this flagged 44% of all team projections (a top defence legitimately "
     "moves expectations 30-50%, and every week-1 opponent has 0 games).", "workaround": "Factor not passed to pf_core; CFB checks "
     "(opponent beyond 2.5 SD of FBS; weeks 1-4 opponent sample) in cfb/gate_cfb.py."},
    {"where": "gate.evaluate (distribution)", "finding": "The 5% mean/pmf tolerance BLOCKs a correct distribution when the "
     "published mean is the model mean but the pmf is floored at 0 (low projections).", "workaround": "Publish the pmf's own mean."},
    {"where": "metrics.cluster_bootstrap_diff", "finding": "Raises ValueError on empty input.", "workaround": "Guard before calling."},
    {"where": "pit.lagged_features", "finding": "Returns a frame with a fresh RangeIndex (season merge) and inserts columns one by "
     "one (pandas fragmentation warnings, slow per-entity lambdas).", "workaround": "Merge results back on keys, never on index."},
    {"where": "models.fit_dist_params('nb')", "finding": "Does not record the support size K, so make_dist sizes the lattice per batch.",
     "workaround": "Set params['K'] explicitly."},
]


def _f(x, nd=2):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:.{nd}f}"


def _ci(c):
    if not c:
        return "n/a"
    return f"{c['mean_diff']:+.3f} [{c['ci95'][0]:+.3f}, {c['ci95'][1]:+.3f}]"


# ----------------------------------------------------------------------------- readiness rules
def auto_readiness(rows, best, has_incumbent):
    h = {r["split"]: r for r in rows if r["model"] == best}
    if "holdout" not in h:
        return "INSUFFICIENT DATA", "no holdout evaluation"
    r = h["holdout"]
    vb = r.get("vs_baseline")
    vi = r.get("vs_incumbent")
    beats_base = bool(vb and vb["ci95"][1] < 0)
    inc_ok = (not has_incumbent) or bool(vi and vi["ci95"][1] < 0)
    calib = 0.75 <= (r.get("cover_80") or 0) <= 0.85 and 0.8 <= (r.get("pit_var_ratio") or 0) <= 1.25
    h25 = h.get("holdout_2025", {})
    vb25 = h25.get("vs_baseline")
    stable = bool(vb25 and vb25["ci95"][1] < 0) if vb25 else beats_base
    lv = h.get("live_2026")
    live_ok = True
    if lv and lv.get("mae") is not None and r.get("mae"):
        live_ok = lv["mae"] <= 1.15 * r["mae"] and 0.7 <= (lv.get("cover_80") or 0) <= 0.9
    if beats_base and inc_ok and calib and stable and not live_ok:
        return "SHADOW VALIDATION", (f"historical holdout passes (vs baseline {_ci(vb)}) but the 2026 live sample deviates "
                                     f"(MAE {_f(lv.get('mae'))} vs holdout {_f(r.get('mae'))}, 80% cover {_f(lv.get('cover_80'))})")
    if beats_base and inc_ok and calib and stable:
        return "READY", (f"holdout 2023-24 beats the A baseline ({_ci(vb)} CRPS) "
                         + (f"and the incumbent ({_ci(vi)})" if has_incumbent else "(no incumbent exists)")
                         + f"; 80% coverage {_f(r['cover_80'])}, PIT variance ratio {_f(r['pit_var_ratio'])}; 2025 confirms.")
    if beats_base and calib:
        return "SHADOW VALIDATION", (f"beats the baseline on holdout ({_ci(vb)}) and is calibrated, but "
                                     + ("does not beat the incumbent with confidence" if not inc_ok else "is not stable on 2025"))
    if beats_base:
        return "SHADOW VALIDATION", f"beats the baseline on holdout ({_ci(vb)}) but calibration is off (80% cover {_f(r.get('cover_80'))}, PIT var {_f(r.get('pit_var_ratio'))})"
    return "NEEDS IMPROVEMENT", f"does not beat the season-average baseline on holdout ({_ci(vb)})"


def experiments(S) -> list[dict]:
    from . import narrative_text as nt
    return nt.experiments(S)


def error_investigations(S):
    from . import narrative_text as nt
    return nt.error_investigations(S)


def opponent_adjustment(S):
    from . import narrative_text as nt
    return nt.opponent_adjustment(S)


def opportunity(S):
    from . import narrative_text as nt
    return nt.opportunity(S)


def calibration(S):
    from . import narrative_text as nt
    return nt.calibration(S)


def failed(S):
    from . import narrative_text as nt
    return nt.failed(S)


def defense_summary(S):
    from . import narrative_text as nt
    return nt.defense_summary(S)


def markdown(S, summary):
    from . import narrative_text as nt
    return nt.markdown(S, summary)
