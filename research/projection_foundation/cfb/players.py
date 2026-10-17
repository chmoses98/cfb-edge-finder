"""Player-game ladder (ESPN player box via sportsdataverse, 2014-2026).

Families: pass_att, pass_yds (team's primary passer), carries, rush_yds (rotation backs/QBs),
receptions, rec_yds (receivers). TARGETS ARE NOT AVAILABLE in the box score, so the receiving
opportunity is completions (team) x reception share.

Population (decided pre-game, from the team's previous 4 games of the SAME season):
  passing   the team's leading passer in its previous game (>= 10 attempts); evaluated conditional on
            recording >= 1 attempt (a QB prop voids if he does not play); non-appearance reported.
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
