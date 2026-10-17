"""Schedule-adjusted offence/defence ratings for CFB, built on pf_core.pit.rolling_ridge_ratings.

CFB specifics layered on the shared routine (pf_core itself is untouched):

* Season-blocked walk-forward. For target season s the frame passed to rolling_ridge_ratings holds
  seasons s-H..s. Every earlier-season row is relabelled into ONE block (s*100+0) so the routine fits
  exactly once per current-season week (block s*100+w uses all prior seasons, decayed by
  prior_season_decay**(s - season), plus current-season weeks < w). Week 1 is therefore a pure
  preseason rating carried from earlier seasons.
* FCS handling. FBS schedules include ~1 FCS game per team and the cache only holds games with an
  FBS participant, so an individual FCS team has 1-3 games: a ridge prior pulls it to LEAGUE AVERAGE,
  i.e. it is treated like an average FBS team. fcs_mode="pool" maps every non-FBS team to a single
  'FCS' entity (well estimated, rated far below average); fcs_mode="individual" is the naive comparator.
* Preseason prior offsets (optional): `offset` = per-row prior expectation; the ridge then shrinks the
  current-season evidence toward the prior instead of toward league average (see priors.py).
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

from pf_core.pit import rolling_ridge_ratings

from . import paths


def _labels(T: pd.DataFrame, fcs_mode: str):
    if fcs_mode == "pool":
        off = np.where(T["team_div"] == "fbs", T["team_id"], "FCS")
        de = np.where(T["opp_div"] == "fbs", T["opp_id"], "FCS")
    else:
        off, de = T["team_id"].to_numpy(), T["opp_id"].to_numpy()
    return off, de


def season_ratings(T: pd.DataFrame, y_col: str, season: int, alpha: float = 5.0, decay: float = 0.5,
                   history: int = 3, fcs_mode: str = "pool", offset_col: str | None = None,
                   use_prior_seasons: bool = True, halflife_days: float | None = None) -> pd.DataFrame:
    """Ratings for every block of `season` (keys: block, team label)."""
    lo = season - (history if use_prior_seasons else 0)
    sub = T[(T["season"] >= lo) & (T["season"] <= season) & T[y_col].notna()].copy()
    if offset_col:
        sub = sub[sub[offset_col].notna()]
        sub["_y"] = sub[y_col] - sub[offset_col]
    else:
        sub["_y"] = sub[y_col]
    sub["_off"], sub["_def"] = _labels(sub, fcs_mode)
    sub["_blk"] = np.where(sub["season"] < season, season * 100, sub["block"])
    # one filler row in the block AFTER the last observed one, so the routine also emits the
    # "all data so far" rating used for future (not yet played) weeks. It is the last block, so it
    # never enters any fit. attach() maps every block to the latest rating block <= it.
    nxt = int(sub["_blk"].max()) + 1
    filler = pd.DataFrame({"_blk": [nxt], "_off": ["__none__"], "_def": ["__none__"], "_y": [0.0],
                           "site": [0], "season": [season], "kickoff": [pd.Timestamp("2100-01-01", tz="UTC")]})
    fit_rows = pd.concat([sub, filler], ignore_index=True)
    R = rolling_ridge_ratings(fit_rows, "_blk", "_off", "_def", "_y", home_col="site", alpha=alpha,
                              date_col="kickoff" if halflife_days else None, halflife_days=halflife_days,
                              season_col="season", prior_season_decay=decay, min_rows=20)
    if R.empty:
        return R
    R = R[R["_blk"] > season * 100].rename(columns={"_blk": "block"})
    R = R[R["team"] != "__none__"]
    return R


def walkforward(T: pd.DataFrame, y_col: str, seasons, **kw) -> pd.DataFrame:
    """Ratings for every block of every season in `seasons`, cached on disk by config."""
    key = json.dumps({"y": y_col, "seasons": list(seasons), **{k: v for k, v in kw.items()}}, sort_keys=True,
                     default=str)
    sig = str(len(T)) + str(T[y_col].sum())
    if kw.get("offset_col"):
        sig += str(np.nansum(T[kw["offset_col"]].to_numpy()))
    h = hashlib.sha256((key + sig).encode()).hexdigest()[:16]
    paths.BUILT.mkdir(parents=True, exist_ok=True)
    fp = paths.BUILT / f"ratings_{y_col}_{h}.parquet"
    if fp.exists():
        return pd.read_parquet(fp)
    out = [season_ratings(T, y_col, s, **kw) for s in seasons]
    R = pd.concat([r for r in out if len(r)], ignore_index=True)
    R.to_parquet(fp)
    return R


def attach(T: pd.DataFrame, R: pd.DataFrame, prefix: str, fcs_mode: str = "pool") -> pd.DataFrame:
    """Adds <prefix>_pred = intercept + off[team] + def[opp] + home*site, plus components and samples."""
    off, de = _labels(T, fcs_mode)
    # each row uses the latest rating block <= its own block in the same season (PIT-safe)
    rb = np.sort(R["block"].unique())
    pos = np.searchsorted(rb, T["block"].to_numpy(), side="right") - 1
    eff = np.where(pos >= 0, rb[np.clip(pos, 0, None)], -1)
    eff = np.where(eff // 100 == T["season"].to_numpy(), eff, -1)
    key = R.set_index(["block", "team"])
    o = key.reindex(pd.MultiIndex.from_arrays([eff, off]))
    d = key.reindex(pd.MultiIndex.from_arrays([eff, de]))
    out = T.copy()
    out[f"{prefix}_off"] = o["off_rating"].to_numpy()
    out[f"{prefix}_def"] = d["def_rating"].to_numpy()
    # unseen team (e.g. a new FBS program / an FCS team in individual mode): rating 0 = league average
    hit = o["intercept"].notna().to_numpy() | d["intercept"].notna().to_numpy()
    icpt = np.where(o["intercept"].notna(), o["intercept"], d["intercept"])
    hadv = np.where(o["home_adv"].notna(), o["home_adv"], d["home_adv"])
    out[f"{prefix}_mu"] = icpt
    out[f"{prefix}_pred"] = np.where(hit, icpt + np.nan_to_num(out[f"{prefix}_off"]) + np.nan_to_num(out[f"{prefix}_def"])
                                     + hadv * out["site"], np.nan)
    out[f"{prefix}_n_off"] = o["n_off"].to_numpy()
    out[f"{prefix}_n_def"] = d["n_def"].to_numpy()
    return out
