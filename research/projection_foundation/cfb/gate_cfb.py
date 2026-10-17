"""Projection Quality Gate for CFB team projections: pf_core.gate.evaluate + CFB-specific checks.

CFB-specific checks (added as findings, never weakening pf_core's):
  coherence      team means must sum to the game-total mean and difference to the margin mean
                 (exact under the joint model; BLOCK if they ever disagree by > 0.5 pt)
  early_opponent weeks 1-4: the opponent's defensive rating rests on < 2 current-season games
                 (prior-season carry only) -> REVIEW
  fcs            an FCS participant (pooled FCS rating, ~2x error variance) -> REVIEW
  opponent_extreme  opponent defence rating beyond 2.5 SD of the FBS distribution -> REVIEW
"""
from __future__ import annotations

import numpy as np

from pf_core import gate

PASS, REVIEW, BLOCK = gate.PASS, gate.REVIEW, gate.BLOCK
_ORDER = {PASS: 0, REVIEW: 1, BLOCK: 2}


def _finding(check, sev, what, category, action, **inputs):
    return {"check": check, "severity": sev, "what": what, "inputs": inputs, "category": category, "action": action}


def team_gate(*, team_id, team, game_id, start_utc, mean, dist_row_lo, dist_row_probs, thresholds, sd,
              week, cur_games_team, cur_games_opp, opp_fcs, team_fcs, opp_def_rating, league_mu, stat_history,
              data_asof_utc, now_utc, game_total_mean=None, team_sum_mean=None, family="team_points", opp_def_sd=None):
    # pf_core's opponent check flags |log factor| > 0.35 as extreme and any factor with < 3 opponent games; in CFB a
    # top defence legitimately moves an offence's expectation by 30-50% and week-1 opponents always have 0 games, so
    # the factor is NOT passed to pf_core (see PF_CORE_FINDINGS); CFB-specific opponent checks are below.
    gi = gate.GateInput(sport="CFB", family=family, entity_id=str(team_id), entity_name=team, team=team,
                        game_id=str(game_id), game_start_utc=start_utc, mean=float(mean), data_asof_utc=data_asof_utc,
                        opponent_sample_games=int(cur_games_opp), opponent_factor=None, support_lo=dist_row_lo,
                        probs=list(map(float, dist_row_probs)), thresholds=thresholds, sd=sd,
                        model_n_obs=int(cur_games_team), stat_history=stat_history)
    res = gate.evaluate(gi, now_utc=now_utc)
    f = list(res["findings"])
    if game_total_mean is not None and team_sum_mean is not None and abs(game_total_mean - team_sum_mean) > 0.5:
        f.append(_finding("coherence", BLOCK, f"team means sum to {team_sum_mean:.1f} but game total is {game_total_mean:.1f}",
                          "mathematical", "Derive totals and margins from one joint distribution."))
    if week <= 4 and cur_games_opp < 2 and not opp_fcs:
        f.append(_finding("early_opponent", REVIEW, f"Week {week}: opponent defence rating rests on {cur_games_opp} current-season games",
                          "uncertainty", "Rating is mostly last season's carried value; show the wide range.",
                          games=int(cur_games_opp)))
    if opp_fcs or team_fcs:
        f.append(_finding("fcs", REVIEW, "FCS participant: pooled FCS rating, historically ~2x the error variance",
                          "uncertainty", "Treat as low-confidence; the pooled FCS rating cannot tell FCS teams apart."))
    if opp_def_rating is not None and opp_def_sd and abs(opp_def_rating) > 2.5 * opp_def_sd:
        f.append(_finding("opponent_extreme", REVIEW, f"Opponent defence rating {opp_def_rating:+.1f} pts is beyond 2.5 SD of FBS",
                          "model_assumption", "Check the opponent's schedule adjustment and data.", rating=float(opp_def_rating)))
    status = PASS
    for x in f:
        if _ORDER[x["severity"]] > _ORDER[status]:
            status = x["severity"]
    return {"status": status, "findings": f}


def thresholds_from(dist, row: int, ks) -> dict:
    return {str(k): float(dist.prob_ge(np.full(len(dist), k))[row]) for k in ks}
