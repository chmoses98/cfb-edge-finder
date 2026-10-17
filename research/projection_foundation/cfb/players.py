"""Player-game ladder (ESPN player box via sportsdataverse, 2014-2026).

Families: pass_att, pass_yds (team's primary passer), carries, rush_yds (rotation backs/QBs),
receptions, rec_yds (receivers). TARGETS ARE NOT AVAILABLE in the box score, so the receiving
opportunity is completions (team) x reception share.

Population (decided pre-game, from the team's previous 4 games of the SAME season):
  passing   the team's leading passer in its previous game (>= 10 attempts); evaluated conditional on
            recording >= 1 attempt (a QB prop voids if he does not play); non-appearance reported. Games without a
            passing line are NOT zeros in the player's history (they are unobserved).
  rushing   >= 5 carries in at least one of the previous 3 team games
  receiving >= 1 reception in at least 2 of the previous 4 team games
  For rushing/receiving a candidate absent from the box is scored as 0: ESPN lists only players with a
  stat, so did-not-play and zero-catch games cannot be told apart (documented limitation).
Week 1 has no in-season candidates and is not projected (needs a depth chart: INSUFFICIENT DATA).

Ladder: A season-to-date mean shrunk; B EWMA shrunk; C team opportunity (raw form) x player share x
player efficiency; D = team opportunity from schedule-adjusted ridge ratings x share x efficiency x
opponent efficiency factor (ridge on yards-per-attempt / per-carry / per-completion allowed);
E LightGBM on the same PIT features; F non-negative stack (development fit).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pf_core import models as pm
from pf_core import pit

HL = 4
FAMILIES = {
    # family: (role, opportunity col, stat col, team opportunity col, team efficiency target)
    "pass_att": ("passer", None, "pass_att", "pass_att", None),
    "pass_yds": ("passer", "pass_att", "pass_yds", "pass_att", "ypa"),
    "carries": ("rusher", None, "carries", "rush_att", None),
    "rush_yds": ("rusher", "carries", "rush_yds", "rush_att", "ypc"),
    "receptions": ("receiver", None, "receptions", "pass_cmp", None),
    "rec_yds": ("receiver", "receptions", "rec_yds", "pass_cmp", "ypc_rec"),
}


def build_panel(P: pd.DataFrame, T: pd.DataFrame) -> pd.DataFrame:
    """Union of (appearances) and (pre-game candidates), one row per (game, team, player)."""
    tg = T[["game_id", "team_id", "season", "week", "kickoff", "block", "post", "opp_id", "team_div", "opp_div",
            "site", "fbs_game"]].copy()
    tg = tg.sort_values(["team_id", "kickoff"]).reset_index(drop=True)
    tg["tgi"] = tg.groupby(["team_id", "season"]).cumcount()
    A = P.merge(tg[["game_id", "team_id", "tgi", "season"]], on=["game_id", "team_id", "season"], how="inner")
    A["lead_passer"] = False
    # leading passer per team-game
    pa = A[A["pass_att"] > 0].sort_values(["game_id", "team_id", "pass_att"], ascending=[True, True, False])
    lead = pa.drop_duplicates(["game_id", "team_id"])
    lead = lead[lead["pass_att"] >= 10][["game_id", "team_id", "player_id"]].assign(lead_passer=True)
    A = A.drop(columns="lead_passer").merge(lead, on=["game_id", "team_id", "player_id"], how="left")
    A["lead_passer"] = A["lead_passer"].fillna(False).astype(bool)
    A["c5"] = A["carries"].fillna(0) >= 5
    A["r1"] = A["receptions"].fillna(0) >= 1
    # candidates for team-game j from appearances in j-k..j-1 (same team, same season)
    cands = []
    for role, flag, lag, need in (("passer", "lead_passer", 1, 1), ("rusher", "c5", 3, 1), ("receiver", "r1", 4, 2)):
        src = A[A[flag]][["team_id", "season", "player_id", "tgi"]]
        rows = []
        for k in range(1, lag + 1):
            rows.append(src.assign(tgi=src["tgi"] + k))
        c = pd.concat(rows).groupby(["team_id", "season", "player_id", "tgi"]).size().rename("hits").reset_index()
        c = c[c["hits"] >= need].assign(role=role)
        cands.append(c[["team_id", "season", "player_id", "tgi", "role"]])
    C = pd.concat(cands, ignore_index=True)
    C = C.merge(tg[["team_id", "season", "tgi", "game_id"]], on=["team_id", "season", "tgi"], how="inner")
    roles = C.pivot_table(index=["game_id", "team_id", "player_id"], columns="role", values="tgi",
                          aggfunc="size", fill_value=0).reset_index()
    for r in ("passer", "rusher", "receiver"):
        if r not in roles:
            roles[r] = 0
        roles[r] = roles[r] > 0
    U = A.merge(roles, on=["game_id", "team_id", "player_id"], how="outer")
    U = U.drop(columns=["season"]).merge(tg.drop(columns=["tgi"]), on=["game_id", "team_id"], how="left")
    for r in ("passer", "rusher", "receiver"):
        U[r] = U[r].fillna(False).astype(bool)
    U["appeared"] = U["name"].notna()
    for c in ("pass_att", "pass_yds", "pass_cmp", "carries", "rush_yds", "receptions", "rec_yds"):
        U[c] = U[c].fillna(0.0)
    U["has_pass"] = U["has_pass"].fillna(False).astype(bool)
    # passing is only OBSERVED when the player has a passing line: a QB prop voids if he does not play, so a missing
    # passing line is 'not observed' (NaN), never a zero (rushing/receiving absences stay 0 - see module docstring)
    for c in ("pass_att", "pass_yds", "pass_cmp"):
        U.loc[~U["has_pass"], c] = np.nan
    U["name"] = U.groupby("player_id")["name"].transform(lambda s: s.ffill().bfill())
    return U


def team_shares(U: pd.DataFrame, T: pd.DataFrame) -> pd.DataFrame:
    team = T[["game_id", "team_id", "pass_att", "pass_cmp", "rush_att", "pass_yds", "rush_yds"]].rename(
        columns={c: "team_" + c for c in ("pass_att", "pass_cmp", "rush_att", "pass_yds", "rush_yds")})
    U = U.merge(team, on=["game_id", "team_id"], how="left")
    U["sh_pass"] = U["pass_att"] / U["team_pass_att"].replace(0, np.nan)
    U["sh_rush"] = U["carries"] / U["team_rush_att"].replace(0, np.nan)
    U["sh_rec"] = U["receptions"] / U["team_pass_cmp"].replace(0, np.nan)
    return U


def player_features(U: pd.DataFrame) -> pd.DataFrame:
    cols = ["pass_att", "pass_yds", "carries", "rush_yds", "receptions", "rec_yds", "sh_pass", "sh_rush", "sh_rec"]
    d = pit.sort_chrono(U, "player_id", "kickoff", "game_id")
    d = pit.lagged_features(d, "player_id", cols, halflives=(HL,), season_col="season", prefix="p_")
    return d


# ----------------------------------------------------------------------------- ladder
GATE_FAMILY = {"pass_att": "passing_attempts", "pass_yds": "passing_yards", "carries": "carries",
               "rush_yds": "rushing_yards", "receptions": "receptions", "rec_yds": "receiving_yards"}
SHARE = {"passer": "sh_pass", "rusher": "sh_rush", "receiver": "sh_rec"}
COUNT = {"passer": "pass_att", "rusher": "carries", "receiver": "receptions"}
TEAM_OPP_D = {"passer": "D_pass_att_pred", "rusher": "D_rush_att_pred", "receiver": "D_pass_cmp_pred"}
TEAM_OPP_B = {"passer": "Bt_pass_att", "rusher": "Bt_rush_att", "receiver": "Bt_pass_cmp"}
OPP_EFF = {"passer": "ypa", "rusher": "ypc", "receiver": "ypc_rec"}


def _ren(d: pd.DataFrame, col: str) -> pd.DataFrame:
    m = {c: c[2:] for c in d.columns if c.startswith("p_" + col + "_")}
    return d[list(m)].rename(columns=m)


def attach_team(U: pd.DataFrame, X: pd.DataFrame) -> pd.DataFrame:
    """Team opportunity (raw B and ridge D) and opponent efficiency factors, keyed by (game, team)."""
    from . import team_models as tm
    k = {c: tm.estimate_k(X, c) for c in ("pass_att", "rush_att", "pass_cmp")}
    Xt = X[["game_id", "team_id", "D_pass_att_pred", "D_rush_att_pred", "D_pass_cmp_pred",
            "D_ypa_def", "D_ypa_mu", "D_ypc_def", "D_ypc_mu", "D_ypc_rec_def", "D_ypc_rec_mu", "D_pts_pred"]].copy()
    for c in ("pass_att", "rush_att", "pass_cmp"):
        Xt[f"Bt_{c}"] = tm.model_B(X, c, k[c], k[c]).to_numpy() if f"o_{c}_ewm4" in X else np.nan
    for e in ("ypa", "ypc", "ypc_rec"):
        Xt[f"oppf_{e}"] = (Xt[f"D_{e}_mu"] + Xt[f"D_{e}_def"]) / Xt[f"D_{e}_mu"]
    return U.merge(Xt, on=["game_id", "team_id"], how="left")


def ladder(D: pd.DataFrame, fam: str, log=print) -> dict[str, pd.Series]:
    role, opp, stat, _, _ = FAMILIES[fam]
    cand = D[role]
    prior = pm.group_prior_pit(D.assign(_role=cand), ["_role"], "kickoff", stat)
    k = pit.estimate_shrinkage_k(D.loc[cand & D.season.between(2016, 2019), stat],
                                 D.loc[cand & D.season.between(2016, 2019), "player_id"] + D.loc[cand & D.season.between(2016, 2019), "season"].astype(str))
    out = {"A_season_avg": pm.est_A(_ren(D, stat), stat, prior, k),
           "B_ewma": pm.est_B(_ren(D, stat), stat, prior, k, HL)}
    sh = SHARE[role]
    sh_prior = pm.group_prior_pit(D.assign(_role=cand), ["_role"], "kickoff", sh)
    sh_hat = pm.est_B(_ren(D, sh), sh, sh_prior, 2.0, HL)
    cnt = COUNT[role]
    if opp is None:          # opportunity family itself: team volume x share
        out["C_team_x_share"] = D[TEAM_OPP_B[role]] * sh_hat
        out["D_ridge_team_x_share"] = D[TEAM_OPP_D[role]] * sh_hat
    else:
        # efficiency = yards per opportunity, shrunk toward the role's PIT rate (k in opportunity units)
        num = _ren(D, stat).rename(columns=lambda c: "num" + c[len(stat):])
        den = _ren(D, cnt).rename(columns=lambda c: "den" + c[len(cnt):])
        rate_prior = pm.group_prior_pit(D.assign(_role=cand, _r=D[stat] / D[cnt].replace(0, np.nan)), ["_role"], "kickoff", "_r")
        eff = pm.ratio_rate(pd.concat([num, den], axis=1), "num", "den", HL, rate_prior, {"passer": 60, "rusher": 40, "receiver": 15}[role])
        out["C_team_x_share_x_eff"] = D[TEAM_OPP_B[role]] * sh_hat * eff
        out["D_ridge_x_share_x_eff_x_opp"] = D[TEAM_OPP_D[role]] * sh_hat * eff * D[f"oppf_{OPP_EFF[role]}"].fillna(1.0)
    feats = [c for c in D.columns if c.startswith("p_") and (c.endswith("_ewm4") or c.endswith("_std") or c.endswith("_n")
                                                             or c.endswith("_prev"))]
    feats += [TEAM_OPP_D[role], TEAM_OPP_B[role], f"oppf_{OPP_EFF[role]}", "D_pts_pred", "site", "week"]
    train = cand & D["has_pass"] if role == "passer" else cand
    E = pm.lgbm_walkforward(D.assign(_blk=D["season"], _tr=train), feats, stat, "_blk", objective="regression",
                            min_train_blocks=1, train_mask_col="_tr",
                            params=dict(num_leaves=15, n_estimators=300, learning_rate=0.04, min_child_samples=200, n_jobs=2))
    out["E_lgbm"] = E
    log(f"  player ladder {fam}: k={k:.2f}")
    return out
