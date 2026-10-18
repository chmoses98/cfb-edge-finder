"""Schedule-adjusted defensive ratings with honest uncertainty (research-only; shadow output).

This is the Projection Foundation replacement candidate for the script engine's defensive ranks (RESULTS_CFB.md
section 7). The point estimate is the SAME model the frozen team-points family already uses (so nothing new is
tuned here): a weighted ridge

    y_r = mu + h * site_r + off[team_r] + def[opp_r]        (y = points scored by the team in row r)

fitted on the last `history` seasons, with every earlier season's rows weighted `decay ** seasons_ago` (prior seasons
are a down-weighted prior, not ignored) and every non-FBS team mapped to ONE pooled 'FCS' entity. On top of it this
module adds what a published ranking needs and the production ranks lacked:

* a posterior covariance of the ratings (`Var = s2 * (X'WX + alpha I)^-1`, the same construction production uses),
  from which a rank interval (10th-90th percentile of ranks over posterior draws) is derived;
* schedule-graph diagnostics: connected components of the current-season FBS-vs-FBS graph (the production engine's
  ranks were published while this graph had ~85 components after week 1), and a rank policy that WITHHOLDS the rank
  (the rating and its band stay visible) while the graph is disconnected and the median team has too few games;
* effective games and the share of a team's information that is carried from earlier seasons, so that early-season
  uncertainty is visible per team;
* optional conference offsets (hierarchical shrinkage toward the team's conference instead of the FBS mean) - OFF in
  the frozen configuration; only adopted if the development-period experiment (defense_validate.py) shows a gain.

Point-in-time: a fit for cutoff block `b` uses only rows with block < b of the target season and the seasons before it.
Pure functions of the input frame; nothing is read from disk here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

POOL = "FCS"
RANK_OK, RANK_PROVISIONAL, RANK_SUPPRESSED = "OK", "PROVISIONAL", "SUPPRESSED"


@dataclass(frozen=True)
class DefenseConfig:
    alpha: float = 0.5              # frozen team-points ridge alpha (summary.json frozen_model.alphas.pts)
    decay: float = 0.2              # weight of a game one season back (frozen)
    history: int = 3                # seasons carried (frozen)
    fcs_mode: str = "pool"          # "pool" (frozen) | "individual" (naive comparator, tests only)
    conf_alpha: float | None = None  # None = no conference offsets (frozen); else the penalty on conference effects
    n_draws: int = 2000
    seed: int = 20261017
    band: tuple[float, float] = (0.10, 0.90)   # rank interval = 80% posterior band
    var_inflation: float = 1.0      # multiplies the posterior variance (set from the development calibration check)
    # rank policy (same spirit as production's LeagueFit.stable): publish ranks only when the current-season FBS
    # schedule graph is connected, or the median FBS team already has `min_median_games` games
    min_median_games: int = 4
    min_team_games: int = 3


# --------------------------------------------------------------------------- helpers
def _labels(T: pd.DataFrame, fcs_mode: str):
    if fcs_mode == "pool":
        off = np.where(T["team_div"] == "fbs", T["team_id"], POOL)
        de = np.where(T["opp_div"] == "fbs", T["opp_id"], POOL)
    else:
        off, de = T["team_id"].to_numpy(), T["opp_id"].to_numpy()
    return off.astype(object), de.astype(object)


def schedule_components(games: pd.DataFrame, fbs_ids) -> dict:
    """Connected components of the FBS-vs-FBS graph built from `games` (one row per team-game; columns team_id,
    opp_id, team_div, opp_div). Isolated FBS teams (no game yet) are their own component, as in production's count."""
    fbs = sorted(set(map(str, fbs_ids)))
    parent = {t: t for t in fbs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    if len(games):
        g = games[(games["team_div"] == "fbs") & (games["opp_div"] == "fbs")]
        for a, b in zip(g["team_id"].astype(str), g["opp_id"].astype(str)):
            if a in parent and b in parent:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
    roots = {t: find(t) for t in fbs}
    sizes: dict[str, int] = {}
    for r in roots.values():
        sizes[r] = sizes.get(r, 0) + 1
    main = max(sizes, key=lambda r: (sizes[r], r)) if sizes else None
    comp_id = {r: i for i, r in enumerate(sorted(sizes, key=lambda r: (-sizes[r], r)))}
    return {"n_components": len(sizes), "largest": sizes.get(main, 0), "n_fbs": len(fbs),
            "largest_share": (sizes.get(main, 0) / len(fbs)) if fbs else 0.0,
            "component_of": {t: comp_id[roots[t]] for t in fbs},
            "component_size": {t: sizes[roots[t]] for t in fbs},
            "in_main": {t: roots[t] == main for t in fbs}}


def _weighted_ridge(X: np.ndarray, y: np.ndarray, w: np.ndarray, alpha: float):
    """sklearn.linear_model.Ridge(alpha, fit_intercept=True) with sample_weight, plus the posterior covariance."""
    sw = w.sum()
    xm = (w[:, None] * X).sum(0) / sw
    ym = (w * y).sum() / sw
    Xc, yc = X - xm, y - ym
    S = Xc.T @ (w[:, None] * Xc)
    A = S + alpha * np.eye(X.shape[1])
    Ainv = np.linalg.inv(A)
    beta = Ainv @ (Xc.T @ (w * yc))
    resid = yc - Xc @ beta
    p_eff = float(np.trace(Ainv @ S))
    s2 = float((w * resid ** 2).sum() / max(sw - p_eff, 1.0))
    return beta, ym - xm @ beta, s2 * Ainv, s2, p_eff


@dataclass
class DefenseFit:
    season: int
    cutoff_block: int
    config: DefenseConfig
    teams: list                       # entity labels, FBS teams (+ POOL)
    mu: float
    home_adv: float
    s2: float
    off: np.ndarray                   # total offence rating per entity
    de: np.ndarray                    # total defence rating per entity (points allowed vs average; LOWER = better)
    cov_de: np.ndarray                # posterior covariance of `de`
    info: pd.DataFrame                # per-entity games / weights / carry share / fcs exposure
    components: dict
    median_fbs_games: float

    def predict(self, rows: pd.DataFrame) -> np.ndarray:
        """Points for each team-game row: mu + off[team] + def[opp] + h*site. A team the fit has never seen
        contributes 0 (league average), as the frozen walk-forward attach() does."""
        idx = {t: i for i, t in enumerate(self.teams)}
        ol, dl = _labels(rows, self.config.fcs_mode)
        o = np.array([self.off[idx[t]] if t in idx else 0.0 for t in map(str, ol)])
        d = np.array([self.de[idx[t]] if t in idx else 0.0 for t in map(str, dl)])
        return self.mu + o + d + self.home_adv * rows["site"].to_numpy(float)

    def frame(self) -> pd.DataFrame:
        sd = np.sqrt(np.clip(np.diag(self.cov_de), 0, None))
        out = pd.DataFrame({"team_id": self.teams, "off_rating": self.off, "def_rating": self.de, "se_def": sd})
        return out.merge(self.info, on="team_id", how="left")


def fit(T: pd.DataFrame, season: int, cutoff_block: int, cfg: DefenseConfig = DefenseConfig(),
        conf_of: dict | None = None, light: bool = False) -> DefenseFit:
    """Fit as of `cutoff_block` (rows with block < cutoff_block of `season` and all rows of earlier seasons in the
    history window). T needs: season, block, team_id, opp_id, team_div, opp_div, site, pts."""
    lo = season - cfg.history
    sub = T[(T["season"] >= lo) & (T["season"] <= season) & T["pts"].notna()].copy()
    sub = sub[(sub["season"] < season) | (sub["block"] < cutoff_block)]
    off_l, def_l = _labels(sub, cfg.fcs_mode)
    # entity universe: every FBS team of the target season (even with no games yet) + whatever the rows name
    used = set(map(str, np.concatenate([off_l, def_l]))) if len(sub) else set()
    fbs_season = set(T[(T["season"] == season) & (T["team_div"] == "fbs")]["team_id"].astype(str))
    teams = sorted((fbs_season | used) - {POOL}) + ([POOL] if cfg.fcs_mode == "pool" else [])
    idx = {t: i for i, t in enumerate(teams)}
    n, nt = len(sub), len(teams)
    oi = np.array([idx[str(t)] for t in off_l]) if n else np.zeros(0, int)
    di = np.array([idx[str(t)] for t in def_l]) if n else np.zeros(0, int)
    w = cfg.decay ** np.clip(season - sub["season"].to_numpy(), 0, None).astype(float)
    cur = (sub["season"].to_numpy() == season)

    use_conf = cfg.conf_alpha is not None and conf_of is not None
    confs = sorted({c for c in conf_of.values() if c is not None}) if use_conf else []
    ci = {c: i for i, c in enumerate(confs)}
    nc = len(confs)
    cscale = np.sqrt(cfg.alpha / cfg.conf_alpha) if use_conf else 0.0
    p = 2 * nt + 1 + (2 * nc if use_conf else 0)
    X = np.zeros((n, p))
    X[np.arange(n), oi] = 1.0
    X[np.arange(n), nt + di] = 1.0
    X[:, 2 * nt] = sub["site"].to_numpy(float)
    if use_conf:
        for k, (arr, base) in enumerate(((oi, 2 * nt + 1), (di, 2 * nt + 1 + nc))):
            cc = np.array([ci.get(conf_of.get(teams[j]), -1) if teams[j] != POOL else -1 for j in arr])
            m = cc >= 0
            X[np.where(m)[0], base + cc[m]] = cscale
    y = sub["pts"].to_numpy(float)
    if n < 20:
        raise ValueError("too few rows to fit a defence rating")
    beta, icpt, cov, s2, p_eff = _weighted_ridge(X, y, w, cfg.alpha)
    cov = cov * cfg.var_inflation
    # total rating of each entity = team effect (+ its conference effect)
    M_off = np.zeros((nt, p))
    M_def = np.zeros((nt, p))
    M_off[np.arange(nt), np.arange(nt)] = 1.0
    M_def[np.arange(nt), nt + np.arange(nt)] = 1.0
    if use_conf:
        for j, t in enumerate(teams):
            c = conf_of.get(t) if t != POOL else None
            if c in ci:
                M_off[j, 2 * nt + 1 + ci[c]] = cscale
                M_def[j, 2 * nt + 1 + nc + ci[c]] = cscale
    de = M_def @ beta
    cov_de = M_def @ cov @ M_def.T
    if light:                                        # walk-forward experiments only need ratings
        info, comp, med = pd.DataFrame({"team_id": teams}), {}, 0.0
    else:
        info = _team_info(sub, season, teams, w, cur)
        comp = schedule_components(sub[sub["season"] == season], [t for t in teams if t != POOL])
        per = sub[sub["season"] == season].groupby("team_id").size()
        fb = [per.get(t, 0) for t in teams if t != POOL]
        med = float(sorted(fb)[len(fb) // 2]) if fb else 0.0
    return DefenseFit(season, cutoff_block, cfg, teams, float(icpt), float(beta[2 * nt]), s2, M_off @ beta, de,
                      cov_de, info, comp, med)


def _team_info(sub: pd.DataFrame, season: int, teams: list, w: np.ndarray, cur: np.ndarray) -> pd.DataFrame:
    """Per entity, as a DEFENDER: current-season games, weighted effective games, carry share, FCS exposure."""
    d = pd.DataFrame({"team_id": sub["opp_id"].astype(str).where(sub["opp_div"] == "fbs", POOL).to_numpy(),
                      "w": w, "cur": cur, "vs_fcs": (sub["team_div"] != "fbs").to_numpy()})
    g = d.groupby("team_id")
    cur_rows = d[d["cur"]].groupby("team_id")
    info = pd.DataFrame({"team_id": teams})
    info["games_current"] = info["team_id"].map(cur_rows.size()).fillna(0).astype(int)
    info["games_used"] = info["team_id"].map(g.size()).fillna(0).astype(int)
    info["eff_games"] = info["team_id"].map(g["w"].sum()).fillna(0.0)
    cw = info["team_id"].map(d[d["cur"]].groupby("team_id")["w"].sum()).fillna(0.0)
    info["carry_share"] = np.where(info["eff_games"] > 0, 1.0 - cw / info["eff_games"].where(info["eff_games"] > 0, 1), 1.0)
    info["fcs_games_current"] = info["team_id"].map(d[d["cur"] & d["vs_fcs"]].groupby("team_id").size()).fillna(0).astype(int)
    info["fcs_games_used"] = info["team_id"].map(d[d["vs_fcs"]].groupby("team_id").size()).fillna(0).astype(int)
    return info


# --------------------------------------------------------------------------- ranks and bands
def rating_draws(f: DefenseFit, keep: list[int], n: int | None = None) -> np.ndarray:
    """Posterior draws of the defence ratings of entities `keep` (indices into f.teams); rows = draws."""
    cfg = f.config
    n = n or cfg.n_draws
    mu = f.de[keep]
    cov = f.cov_de[np.ix_(keep, keep)]
    rng = np.random.default_rng(cfg.seed)
    try:
        L = np.linalg.cholesky(cov + 1e-9 * np.eye(len(keep)))
        return mu[None, :] + rng.standard_normal((n, len(keep))) @ L.T
    except np.linalg.LinAlgError:
        return mu[None, :] + rng.standard_normal((n, len(keep))) * np.sqrt(np.clip(np.diag(cov), 0, None))


def rank_band(f: DefenseFit, team_ids: list[str]) -> pd.DataFrame:
    """Point rank and 80% band of a SUBSET of entities (ranked among themselves) - used by the validation."""
    idx = {t: i for i, t in enumerate(f.teams)}
    keep = [idx[t] for t in team_ids if t in idx]
    ids = [f.teams[i] for i in keep]
    draws = rating_draws(f, keep)
    ranks = draws.argsort(1).argsort(1) + 1
    cfg = f.config
    return pd.DataFrame({"team_id": ids, "rank_point": pd.Series(f.de[keep]).rank(method="first").astype(int).to_numpy(),
                         "rank_lo": np.quantile(ranks, cfg.band[0], axis=0), "rank_hi": np.quantile(ranks, cfg.band[1], axis=0)})


def centered(f: DefenseFit, team_ids: list[str] | None = None) -> pd.DataFrame:
    """Ratings relative to the mean of the listed FBS entities (default: all FBS) with the SD of THAT contrast.

    The level of the whole FBS defence relative to the pooled FCS entity and the intercept is only weakly identified
    (conference offsets in particular are nearly collinear with them), which inflates every team's raw SE by a common
    shift that does not affect any ranking or any team-vs-team comparison. Publishing the centred rating removes it."""
    idx = {t: i for i, t in enumerate(f.teams)}
    ids = [t for t in (team_ids or f.teams) if t in idx and t != POOL]
    k = [idx[t] for t in ids]
    n = len(k)
    C = np.eye(n) - np.ones((n, n)) / n
    r = C @ f.de[k]
    cov = C @ f.cov_de[np.ix_(k, k)] @ C.T
    return pd.DataFrame({"team_id": ids, "rating_c": r, "se_c": np.sqrt(np.clip(np.diag(cov), 0, None))})


def rank_table(f: DefenseFit, names: dict | None = None) -> pd.DataFrame:
    """FBS defences ranked by rating (1 = fewest points allowed), with an 80% rank interval from posterior draws,
    the rank policy applied, and every flag the shadow file publishes."""
    cfg = f.config
    fr = f.frame()
    fr = fr[fr["team_id"] != POOL].reset_index(drop=True)
    keep = [i for i, t in enumerate(f.teams) if t != POOL]
    mu = f.de[keep]
    draws = rating_draws(f, keep)
    ranks = draws.argsort(1).argsort(1) + 1          # rank 1 = lowest points allowed
    fr = fr.merge(centered(f), on="team_id", how="left")
    fr["rank_point"] = pd.Series(mu).rank(method="first").astype(int).to_numpy()
    fr["rank_lo"] = np.quantile(ranks, cfg.band[0], axis=0).round().astype(int)
    fr["rank_hi"] = np.quantile(ranks, cfg.band[1], axis=0).round().astype(int)
    fr["p_top10"] = (ranks <= 10).mean(0)
    fr["p_top25"] = (ranks <= 25).mean(0)
    comp = f.components
    league_ok = comp["n_components"] == 1 or f.median_fbs_games >= cfg.min_median_games
    status, reason = [], []
    for t, g in zip(fr["team_id"], fr["games_current"]):
        r = []
        if not league_ok:
            r.append(f"FBS schedule graph is disconnected ({comp['n_components']} components) and the median team has "
                     f"{f.median_fbs_games:.0f} games (< {cfg.min_median_games})")
        elif not comp["in_main"].get(t, False):
            r.append("team is not connected to the main FBS schedule component")
        if r:
            status.append(RANK_SUPPRESSED)
        else:
            if comp["n_components"] > 1:
                r.append(f"schedule graph still has {comp['n_components']} components")
            if g < cfg.min_team_games:
                r.append(f"only {g} games this season (< {cfg.min_team_games})")
            status.append(RANK_PROVISIONAL if r else RANK_OK)
        reason.append(r)
    fr["rank_status"] = status
    fr["rank_reasons"] = reason
    fr["rank"] = np.where(fr["rank_status"] == RANK_SUPPRESSED, np.nan, fr["rank_point"])
    fr["component_id"] = fr["team_id"].map(comp["component_of"])
    fr["component_size"] = fr["team_id"].map(comp["component_size"])
    fr["in_main_component"] = fr["team_id"].map(comp["in_main"]).astype(bool)
    fr["fcs_pooled"] = (fr["fcs_games_used"] > 0) if cfg.fcs_mode == "pool" else False
    if names:
        fr["team"] = fr["team_id"].map(names)
    return fr.sort_values(["rank_point", "team_id"]).reset_index(drop=True)


def league_summary(f: DefenseFit) -> dict:
    c = f.components
    return {"fbs_schedule_components": int(c["n_components"]), "largest_component_share": round(float(c["largest_share"]), 4),
            "median_fbs_games": f.median_fbs_games, "n_fbs": int(c["n_fbs"]),
            "ranks_published": bool(c["n_components"] == 1 or f.median_fbs_games >= f.config.min_median_games),
            "rule": (f"ranks are withheld while the current-season FBS-vs-FBS schedule graph is disconnected AND the median "
                     f"FBS team has fewer than {f.config.min_median_games} games; teams outside the main component stay "
                     f"withheld; provisional while the graph is not yet one component or the team has fewer than "
                     f"{f.config.min_team_games} games")}
