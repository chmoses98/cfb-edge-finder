"""Feature-set definitions for the V2 tournament.

Every feature is a deterministic function of the dataset's pregame
columns. Feature sets are named and hashed so an experiment can pin the
exact set it used.

Conventions (see state.py): predicted metric for home offense vs away
defense is off_h - def_a; for away offense vs home defense, off_a - def_h.
`diff_*` = home-side minus away-side expectation (drives MARGIN);
`sum_*`  = home-side plus away-side expectation (drives TOTAL).
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

EFF_METRICS = ["pts_for", "o_ppa", "o_sr", "o_expl", "o_pass_ppa", "o_rush_ppa", "o_pass_sr", "o_rush_sr", "o_sd_sr",
               "o_pd_sr", "o_line_yds", "o_ppa_ng", "o_sr_ng", "dr_pts_per_drive", "dr_ppo", "dr_to_rate",
               "dr_3out_rate", "dr_start_ytg", "b_third_rate", "b_pen_yds", "b_havoc_def", "b_sack_rate_def",
               "b_takeaways"]
PACE_METRICS = ["o_plays", "dr_sec_per_play"]
PRE_FIELDS = ["talent", "recruit_avg4", "recruit_0", "ret_percentPPA", "ret_percentPassingPPA", "ret_percentRushingPPA",
              "ret_percentReceivingPPA", "ret_usage", "coach_change", "coach_tenure", "prev_margin_strength",
              "prev2_margin_strength", "prev_pf", "prev_pa", "prev_win_pct", "sp_prev_rating", "sp_prev_off",
              "sp_prev_def", "poll_pre_ap", "poll_pre_coaches", "fbs_new"]


def _col(df: pd.DataFrame, c: str) -> pd.Series:
    return pd.to_numeric(df[c], errors="coerce") if c in df.columns else pd.Series(np.nan, index=df.index)


def matchup_frame(df: pd.DataFrame) -> pd.DataFrame:
    """All derived matchup columns (superset); feature sets select from it."""
    out = pd.DataFrame(index=df.index)
    hi = np.where(df["neutral"].astype(bool), 0.0, 1.0)
    out["home_ind"] = hi
    out["neutral"] = df["neutral"].astype(float)
    # structural margin strength
    out["str_margin_diff"] = _col(df, "h_o_margin") - _col(df, "a_o_margin")
    out["str_margin_hfa"] = _col(df, "hfa_margin") * hi
    for m in EFF_METRICS + PACE_METRICS:
        h_exp = _col(df, f"h_o_{m}") - _col(df, f"a_d_{m}")
        a_exp = _col(df, f"a_o_{m}") - _col(df, f"h_d_{m}")
        out[f"diff_{m}"] = h_exp - a_exp
        out[f"sum_{m}"] = h_exp + a_exp
        out[f"hfa_{m}"] = _col(df, f"hfa_{m}") * hi
        out[f"mu_{m}"] = _col(df, f"mu_{m}")
    # explicit offense-vs-defense pairings (home offense vs away defense etc.)
    for m in ["o_ppa", "o_pass_ppa", "o_rush_ppa", "o_sr", "o_expl"]:
        out[f"h_off_{m}"] = _col(df, f"h_o_{m}")
        out[f"a_def_{m}"] = _col(df, f"a_d_{m}")
        out[f"a_off_{m}"] = _col(df, f"a_o_{m}")
        out[f"h_def_{m}"] = _col(df, f"h_d_{m}")
    # season evidence
    out["h_games"] = _col(df, "h_games_season")
    out["a_games"] = _col(df, "a_games_season")
    out["min_games"] = np.minimum(out["h_games"], out["a_games"])
    out["h_weighted"] = _col(df, "h_games_weighted")
    out["a_weighted"] = _col(df, "a_games_weighted")
    # preseason
    for f in PRE_FIELDS:
        h = _col(df, f"h_pre_{f}")
        a = _col(df, f"a_pre_{f}")
        out[f"pre_{f}_diff"] = h - a
        out[f"pre_{f}_sum"] = h + a
        out[f"h_pre_{f}"] = h
        out[f"a_pre_{f}"] = a
    # early-season interactions: preseason differential weighted by lack of current-season evidence
    early = np.exp(-out["min_games"] / 4.0)
    out["early_w"] = early
    for f in ["talent", "recruit_avg4", "prev_margin_strength", "sp_prev_rating", "poll_pre_ap", "ret_percentPPA",
              "ret_percentPassingPPA", "coach_change"]:
        out[f"early_x_{f}_diff"] = out[f"pre_{f}_diff"] * early
    # situational
    out["week"] = _col(df, "week")
    out["postseason"] = df["postseason"].astype(float) if "postseason" in df.columns else 0.0
    out["conference_game"] = df["conference_game"].astype(float)
    home_rest = _col(df, "home_rest_days").clip(upper=21).fillna(7)
    away_rest = _col(df, "away_rest_days").clip(upper=21).fillna(7)
    out["rest_diff"] = home_rest - away_rest
    out["home_short_week"] = (_col(df, "home_rest_days") < 6).astype(float)
    out["away_short_week"] = (_col(df, "away_rest_days") < 6).astype(float)
    out["travel_diff_km"] = _col(df, "away_travel_km").fillna(0) - _col(df, "home_travel_km").fillna(0)
    out["away_travel_km"] = _col(df, "away_travel_km").fillna(0)
    out["venue_elev_m"] = _col(df, "venue_elev_m").fillna(0)
    out["venue_dome"] = _col(df, "venue_dome").fillna(0)
    out["kick_hour_utc"] = _col(df, "kick_hour_utc").fillna(0)
    out["fcs_involved"] = ((df["home_class"] != "fbs") | (df["away_class"] != "fbs")).astype(float)
    # external rating
    out["elo_diff"] = _col(df, "home_pregame_elo") - _col(df, "away_pregame_elo")
    out["elo_sum"] = _col(df, "home_pregame_elo") + _col(df, "away_pregame_elo")
    return out


FEATURE_SETS: dict[str, list[str]] = {}


def _register(name: str, cols: list[str]) -> None:
    FEATURE_SETS[name] = cols


_STRUCT = ["str_margin_diff", "str_margin_hfa", "neutral"]
_SCORE = ["diff_pts_for", "sum_pts_for", "hfa_pts_for", "mu_pts_for"]
_EFF_DIFF = [f"diff_{m}" for m in EFF_METRICS] + [f"hfa_{m}" for m in ["o_ppa", "o_sr", "pts_for"]]
_EFF_SUM = [f"sum_{m}" for m in EFF_METRICS] + [f"mu_{m}" for m in ["o_ppa", "pts_for", "o_plays"]]
_PACE = [f"diff_{m}" for m in PACE_METRICS] + [f"sum_{m}" for m in PACE_METRICS]
_PAIRS = [c for m in ["o_ppa", "o_pass_ppa", "o_rush_ppa", "o_sr", "o_expl"]
          for c in (f"h_off_{m}", f"a_def_{m}", f"a_off_{m}", f"h_def_{m}")]
_EVID = ["h_games", "a_games", "min_games", "h_weighted", "a_weighted", "early_w"]
_PRE_DIFF = [f"pre_{f}_diff" for f in PRE_FIELDS]
_PRE_SUM = [f"pre_{f}_sum" for f in PRE_FIELDS]
_EARLY = [c for c in ("early_x_talent_diff", "early_x_recruit_avg4_diff", "early_x_prev_margin_strength_diff",
                      "early_x_sp_prev_rating_diff", "early_x_poll_pre_ap_diff", "early_x_ret_percentPPA_diff",
                      "early_x_ret_percentPassingPPA_diff", "early_x_coach_change_diff")]
_SIT = ["week", "postseason", "conference_game", "rest_diff", "home_short_week", "away_short_week", "travel_diff_km",
        "away_travel_km", "venue_elev_m", "venue_dome", "kick_hour_utc"]
_ELO = ["elo_diff"]

_register("struct", _STRUCT)
_register("struct+pre", _STRUCT + _PRE_DIFF + _EVID + _EARLY)
_register("eff", _STRUCT + _EFF_DIFF + _EVID)
_register("eff+pre", _STRUCT + _EFF_DIFF + _EVID + _PRE_DIFF + _EARLY)
_register("eff+pre+sit", _STRUCT + _EFF_DIFF + _EVID + _PRE_DIFF + _EARLY + _SIT)
_register(
    "full", _STRUCT + _SCORE + _EFF_DIFF + _EFF_SUM + _PACE + _PAIRS + _EVID + _PRE_DIFF + _PRE_SUM + _EARLY + _SIT
)
_register("full+elo", FEATURE_SETS["full"] + _ELO)
_register("elo_only", _ELO + ["neutral"])
# total-oriented sets
_register("tot_struct", _SCORE + ["neutral"])
_register("tot_eff", _SCORE + _EFF_SUM + _PACE + _EVID + ["neutral"])
_register("tot_eff+pre", _SCORE + _EFF_SUM + _PACE + _EVID + _PRE_SUM + _PRE_DIFF + ["neutral", "early_w"])
_register("tot_full", FEATURE_SETS["full"])


def feature_hash(name: str) -> str:
    return hashlib.sha256(",".join(FEATURE_SETS[name]).encode()).hexdigest()[:12]
