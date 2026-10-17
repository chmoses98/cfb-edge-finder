"""Point-in-time (leakage-safe) feature construction, shrinkage and opponent ratings.

Rule: a feature attached to game g may only use rows whose game finished before g started.
Every helper here shifts by one game *within the entity* before aggregating, and every
rating helper fits on rows strictly earlier than the block it predicts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sort_chrono(df: pd.DataFrame, entity: str, date_col: str, tiebreak: str | None = None) -> pd.DataFrame:
    keys = [entity, date_col] + ([tiebreak] if tiebreak else [])
    return df.sort_values(keys, kind="mergesort").reset_index(drop=True)


def lagged_features(df: pd.DataFrame, entity: str, cols: list[str], halflives=(3, 8),
                    season_col: str | None = None, prefix: str = "") -> pd.DataFrame:
    """Adds, for each col: prior-game count, career mean, EWMA(s), season-to-date mean and
    previous-season mean. df MUST already be sorted chronologically within entity.

    NaN values in col are treated as 'not observed' (e.g. did-not-play) and skipped.
    """
    out = df
    for c in cols:
        x = out[c].astype(float)
        obs = x.notna().astype(float)
        xs = x.fillna(0.0)
        cum = xs.groupby(out[entity], sort=False).cumsum() - xs
        cnt = obs.groupby(out[entity], sort=False).cumsum() - obs
        out[f"{prefix}{c}_n"] = cnt
        out[f"{prefix}{c}_career"] = cum / cnt.replace(0, np.nan)
        for h in halflives:
            # EWMA over observed games only, then shift one game
            ew = x.groupby(out[entity], sort=False).transform(
                lambda s, h=h: s.ewm(halflife=h, ignore_na=True).mean().shift(1))
            out[f"{prefix}{c}_ewm{h}"] = ew
        if season_col:
            keys = [out[entity], out[season_col]]
            scum = xs.groupby(keys, sort=False).cumsum() - xs
            scnt = obs.groupby(keys, sort=False).cumsum() - obs
            out[f"{prefix}{c}_std_n"] = scnt
            out[f"{prefix}{c}_std"] = scum / scnt.replace(0, np.nan)
            season_mean = out.groupby([entity, season_col])[c].mean().rename("_m").reset_index()
            season_n = out.groupby([entity, season_col])[c].count().rename("_n").reset_index()
            sm = season_mean.merge(season_n, on=[entity, season_col])
            sm[season_col] = sm[season_col] + 1
            sm = sm.rename(columns={"_m": f"{prefix}{c}_prev", "_n": f"{prefix}{c}_prev_n"})
            out = out.merge(sm, on=[entity, season_col], how="left")
    return out


def eb_shrink(mean, n, prior, k):
    """Empirical-Bayes posterior mean (n*mean + k*prior)/(n + k); NaN mean -> prior."""
    mean = np.asarray(mean, float)
    n = np.nan_to_num(np.asarray(n, float))
    prior = np.asarray(prior, float)
    m = np.where(np.isfinite(mean), mean, prior)
    return (n * m + k * prior) / (n + k)


def estimate_shrinkage_k(values: pd.Series, groups: pd.Series, min_n: int = 5) -> float:
    """k = within-entity variance / between-entity variance of true means (method of moments).

    k is 'how many games of evidence equal the prior'. Large k = heavy pooling.
    """
    d = pd.DataFrame({"v": values.astype(float), "g": groups}).dropna()
    st = d.groupby("g")["v"].agg(["mean", "var", "count"])
    st = st[st["count"] >= min_n]
    if len(st) < 5:
        return 10.0
    within = float(np.average(st["var"], weights=st["count"]))
    between_obs = float(np.var(st["mean"], ddof=1))
    noise = float(np.mean(within / st["count"]))
    between_true = max(between_obs - noise, 1e-9)
    return float(np.clip(within / between_true, 0.5, 500.0))


def rolling_ridge_ratings(games: pd.DataFrame, block_col: str, off_col: str, def_col: str, y_col: str,
                          home_col: str | None = None, alpha: float = 5.0, date_col: str | None = None,
                          halflife_days: float | None = None, season_col: str | None = None,
                          prior_season_decay: float = 0.5, min_rows: int = 20) -> pd.DataFrame:
    """Schedule-adjusted offence/defence ratings refitted before every block.

    Model: y = mu + off[o] + def[d] (+ home*h), ridge-penalised toward 0 (= league average).
    Fitting on all prior games jointly is the strength-of-schedule adjustment: a defence that
    only faced weak offences is not credited for low raw allowed numbers, because those
    offences' low ratings explain them. Ridge alpha is the pooling strength (in games).

    Returns rows (block, team, off_rating, def_rating, n_off, n_def, intercept) where ratings
    are in y units relative to league average, computed only from games with block < block.
    """
    from sklearn.linear_model import Ridge

    teams = pd.Index(sorted(set(games[off_col]).union(set(games[def_col]))))
    T = len(teams)
    oi = teams.get_indexer(games[off_col])
    di = teams.get_indexer(games[def_col])
    blocks = sorted(games[block_col].unique())
    res = []
    for b in blocks:
        m = (games[block_col] < b).to_numpy()
        if m.sum() < min_rows:
            continue
        sub = games.loc[m]
        n = int(m.sum())
        X = np.zeros((n, 2 * T + (1 if home_col else 0)))
        X[np.arange(n), oi[m]] = 1.0
        X[np.arange(n), T + di[m]] = 1.0
        if home_col:
            X[:, -1] = sub[home_col].astype(float).to_numpy()
        w = np.ones(n)
        if season_col is not None:
            cur = games.loc[games[block_col] == b, season_col].iloc[0]
            w *= prior_season_decay ** np.clip(cur - sub[season_col].to_numpy(), 0, None)
        if halflife_days and date_col:
            cur_d = pd.to_datetime(games.loc[games[block_col] == b, date_col]).min()
            age = (cur_d - pd.to_datetime(sub[date_col])).dt.days.to_numpy()
            w *= 0.5 ** (age / halflife_days)
        r = Ridge(alpha=alpha, fit_intercept=True)
        r.fit(X, sub[y_col].astype(float).to_numpy(), sample_weight=w)
        n_off = np.bincount(oi[m], minlength=T)
        n_def = np.bincount(di[m], minlength=T)
        res.append(pd.DataFrame({
            block_col: b, "team": teams, "off_rating": r.coef_[:T], "def_rating": r.coef_[T:2 * T],
            "n_off": n_off, "n_def": n_def, "intercept": r.intercept_,
            "home_adv": r.coef_[-1] if home_col else 0.0,
        }))
    return pd.concat(res, ignore_index=True) if res else pd.DataFrame()


def rolling_opponent_allowed(team_games: pd.DataFrame, def_col: str, date_col: str, y_col: str,
                             k_prior: float, season_col: str | None = None, halflife: float | None = None) -> pd.DataFrame:
    """Raw (not schedule-adjusted) PIT opponent factor: shrunk prior-games mean allowed / PIT league mean.

    Provided as the simple comparator for rolling_ridge_ratings.
    """
    d = team_games.sort_values([def_col, date_col]).copy()
    grp = [d[def_col]] + ([d[season_col]] if season_col else [])
    x = d[y_col].astype(float)
    if halflife:
        prior_mean = x.groupby(grp).transform(lambda s: s.ewm(halflife=halflife).mean().shift(1))
    else:
        prior_mean = (x.groupby(grp).cumsum() - x) / (x.groupby(grp).cumcount().replace(0, np.nan))
    n = x.groupby(grp).cumcount()
    # PIT league mean: expanding mean over all games on earlier dates
    by_date = d.groupby(date_col)[y_col].agg(["sum", "count"]).sort_index()
    lm = (by_date["sum"].cumsum() - by_date["sum"]) / (by_date["count"].cumsum() - by_date["count"]).replace(0, np.nan)
    d["_league"] = d[date_col].map(lm)
    d["_league"] = d["_league"].fillna(d[y_col].mean() if len(d) else np.nan)
    shr = eb_shrink(prior_mean, n, d["_league"], k_prior)
    d["opp_allowed_shrunk"] = shr
    d["opp_factor_raw"] = shr / d["_league"]
    d["opp_n"] = n
    return d


def rolling_origin(df: pd.DataFrame, block_col: str, fit_predict, min_train_blocks: int = 1,
                   refit_every: int = 1, verbose: bool = False) -> pd.DataFrame:
    """Chronological walk-forward: train on blocks < b, predict block b.

    fit_predict(train_df, test_df, state) -> (pred_df_indexed_like_test, state). state lets the
    caller reuse a fitted model between refits (refit_every > 1) to bound compute.
    """
    blocks = sorted(df[block_col].unique())
    outs = []
    state = None
    for i, b in enumerate(blocks):
        if i < min_train_blocks:
            continue
        test = df[df[block_col] == b]
        refit = state is None or ((i - min_train_blocks) % refit_every == 0)
        train = df[df[block_col] < b] if refit else None
        pred, state = fit_predict(train, test, state if not refit else None)
        outs.append(pred)
        if verbose:
            print(f"block {b}: n={len(test)}")
    return pd.concat(outs) if outs else pd.DataFrame()


def assert_point_in_time(feature_ts: pd.Series, game_ts: pd.Series, name: str = "feature") -> None:
    ft = pd.to_datetime(feature_ts, utc=True)
    gt = pd.to_datetime(game_ts, utc=True)
    bad = (ft >= gt) & ft.notna()
    if bad.any():
        raise AssertionError(f"{name}: {int(bad.sum())} rows use information timestamped at/after game start")
