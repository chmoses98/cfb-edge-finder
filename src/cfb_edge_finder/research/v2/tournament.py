"""Rolling-origin model tournament.

For each test season Y in `folds`, every candidate is fit on completed
games with season < Y (optionally FBS-vs-FBS only) and predicts every
FBS-vs-FBS game of season Y. Hyperparameters that need selection are
chosen INSIDE the training window: fit on seasons < Y-1, validate on
Y-1, then refit on all seasons < Y with the selected value. The test
season is never touched during selection.

Candidates produce, per game: pred_margin, pred_total, p_home (winner
probability). Winner probability for regression candidates is derived
from the margin prediction and the inner-validation residual scale
(Normal); classifier candidates produce it directly.

Every run is appended to a JSON-lines registry with the candidate spec,
feature-set hash, dataset hash, per-season metrics, segment metrics and
runtime. Per-game predictions are persisted for downstream calibration,
ensembling and failure analysis.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from cfb_edge_finder.research.v2.features import FEATURE_SETS, feature_hash, matchup_frame
from cfb_edge_finder.research.v2.metrics import margin_metrics, total_metrics, winner_metrics

DEFAULT_FOLDS = (2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025)
"""2020 is excluded as a TEST season (COVID scheduling) but its games remain
training evidence for later folds. 2015-2016 are training-only (too little
prior history to be fair test seasons)."""


@dataclass
class Candidate:
    name: str
    model: str  # ridge | lgbm | logit | lgbm_clf | points_ridge | points_lgbm | elo | zero
    target: str = "margin"  # margin | total | points | winner
    feature_set: str = "struct"
    params: dict = field(default_factory=dict)
    train_fbs_only: bool = True
    train_from: int = 2015

    def spec_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


SEGMENTS = {
    "all": lambda d: np.ones(len(d), bool),
    "week_1": lambda d: d.week.values <= 1,
    "weeks_1_3": lambda d: d.week.values <= 3,
    "weeks_4_plus": lambda d: (d.week.values >= 4) & (~d.postseason.values),
    "postseason": lambda d: d.postseason.values,
    "neutral": lambda d: d.neutral.values,
    "conference": lambda d: d.conference_game.values,
    "nonconference": lambda d: ~d.conference_game.values,
}


def _prep(X: pd.DataFrame, cols: list[str], fill: pd.Series | None = None):
    M = X[cols].astype(float)
    if fill is None:
        fill = M.median()
    return M.fillna(fill).fillna(0.0), fill


class _Ridge:
    def __init__(self, alpha: float):
        self.alpha = alpha

    def fit(self, X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None):
        self.mean_ = X.mean(0)
        self.std_ = X.std(0) + 1e-9
        Z = (X - self.mean_) / self.std_
        w = np.ones(len(y)) if w is None else w
        Zw = Z * w[:, None]
        self.y_mean_ = float(np.average(y, weights=w))
        A = Z.T @ Zw + self.alpha * np.eye(Z.shape[1])
        self.coef_ = np.linalg.solve(A, Zw.T @ (y - self.y_mean_))
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        return ((X - self.mean_) / self.std_) @ self.coef_ + self.y_mean_


def _fit_predict_regression(model: str, params: dict, Xtr, ytr, Xva, yva, Xte, seed: int = 7,
                            wtr=None, wva=None):
    """Select on (Xva, yva) inside the training window, refit on train+val, predict test.
    Returns (pred_test, val_resid_sd, chosen). `wtr`/`wva` are optional sample weights."""
    wtr = np.ones(len(ytr)) if wtr is None else wtr
    wva = np.ones(len(yva)) if wva is None else wva
    wall = np.concatenate([wtr, wva])
    if model == "ridge":
        grid = params.get("alphas", (1.0, 10.0, 100.0, 1000.0))
        best, best_mae = None, np.inf
        for a in grid:
            m = _Ridge(a).fit(Xtr, ytr, wtr)
            mae = np.mean(np.abs(m.predict(Xva) - yva))
            if mae < best_mae:
                best, best_mae = a, mae
        mv = _Ridge(best).fit(Xtr, ytr, wtr)
        resid_sd = float(np.std(yva - mv.predict(Xva)))
        final = _Ridge(best).fit(np.vstack([Xtr, Xva]), np.concatenate([ytr, yva]), wall)
        return final.predict(Xte), resid_sd, {"alpha": best, "val_mae": round(best_mae, 4)}
    if model == "ridge_lgbm":
        # stacked: ridge on the features, LightGBM on the ridge residuals (fit on train, validated on val)
        base_pred, _sd, ch = _fit_predict_regression("ridge", params, Xtr, ytr, Xva, yva, Xte, seed, wtr, wva)
        rid = _Ridge(ch["alpha"]).fit(Xtr, ytr, wtr)
        rtr = ytr - rid.predict(Xtr)
        rva = yva - rid.predict(Xva)
        resid_pred, _sd2, ch2 = _fit_predict_regression("lgbm", params, Xtr, rtr, Xva, rva, Xte, seed, wtr, wva)
        # validation residual sd for the stack
        rid_val = _Ridge(ch["alpha"]).fit(Xtr, ytr, wtr)
        resid_sd = float(np.std(yva - rid_val.predict(Xva)))
        return base_pred + resid_pred, resid_sd, {"ridge": ch, "lgbm": ch2}
    if model == "lgbm":
        import lightgbm as lgb

        p = {"n_estimators": 2000, "learning_rate": 0.02, "num_leaves": 15, "min_child_samples": 40,
             "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8, "reg_lambda": 5.0,
             "objective": params.get("objective", "regression"), "verbosity": -1, "random_state": seed}
        p.update({k: v for k, v in params.items() if k not in ("objective",)})
        m = lgb.LGBMRegressor(**p)
        m.fit(Xtr, ytr, sample_weight=wtr, eval_set=[(Xva, yva)], eval_metric="l1",
              callbacks=[lgb.early_stopping(100, verbose=False)])
        best_iter = int(m.best_iteration_ or p["n_estimators"])
        resid_sd = float(np.std(yva - m.predict(Xva)))
        val_mae = float(np.mean(np.abs(yva - m.predict(Xva))))
        p2 = dict(p)
        p2["n_estimators"] = max(best_iter, 50)
        final = lgb.LGBMRegressor(**p2).fit(np.vstack([Xtr, Xva]), np.concatenate([ytr, yva]), sample_weight=wall)
        return final.predict(Xte), resid_sd, {"best_iter": best_iter, "val_mae": round(val_mae, 4)}
    raise ValueError(model)


def _fit_predict_classifier(model: str, params: dict, Xtr, ytr, Xva, yva, Xte, seed: int = 7):
    if model == "logit":
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        grid = params.get("C", (0.01, 0.1, 1.0))
        best, best_ll = None, np.inf
        sc = StandardScaler().fit(Xtr)
        for c in grid:
            m = LogisticRegression(C=c, max_iter=2000).fit(sc.transform(Xtr), ytr)
            pv = np.clip(m.predict_proba(sc.transform(Xva))[:, 1], 1e-6, 1 - 1e-6)
            ll = -np.mean(yva * np.log(pv) + (1 - yva) * np.log(1 - pv))
            if ll < best_ll:
                best, best_ll = c, ll
        Xall = np.vstack([Xtr, Xva])
        sc2 = StandardScaler().fit(Xall)
        final = LogisticRegression(C=best, max_iter=2000).fit(sc2.transform(Xall), np.concatenate([ytr, yva]))
        return final.predict_proba(sc2.transform(Xte))[:, 1], {"C": best, "val_ll": round(best_ll, 4)}
    if model == "lgbm_clf":
        import lightgbm as lgb

        p = {"n_estimators": 2000, "learning_rate": 0.02, "num_leaves": 15, "min_child_samples": 40,
             "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8, "reg_lambda": 5.0,
             "verbosity": -1, "random_state": seed}
        p.update(params)
        m = lgb.LGBMClassifier(**p)
        m.fit(Xtr, ytr, eval_set=[(Xva, yva)], eval_metric="binary_logloss",
              callbacks=[lgb.early_stopping(100, verbose=False)])
        best_iter = int(m.best_iteration_ or p["n_estimators"])
        p2 = dict(p)
        p2["n_estimators"] = max(best_iter, 50)
        final = lgb.LGBMClassifier(**p2).fit(np.vstack([Xtr, Xva]), np.concatenate([ytr, yva]))
        return final.predict_proba(Xte)[:, 1], {"best_iter": best_iter}
    raise ValueError(model)


def run_candidate(cand: Candidate, df: pd.DataFrame, X: pd.DataFrame, folds=DEFAULT_FOLDS,
                  verbose: bool = True) -> tuple[pd.DataFrame, dict]:
    """Returns (predictions, run_record)."""
    t0 = time.perf_counter()
    cols = FEATURE_SETS[cand.feature_set]
    preds = []
    chosen: dict[int, dict] = {}
    completed = df.completed.values
    fbs = df.both_fbs.values
    for y in folds:
        tr_mask = completed & (df.season.values < y) & (df.season.values >= cand.train_from)
        if cand.train_fbs_only:
            tr_mask &= fbs
        te_mask = completed & (df.season.values == y) & fbs
        if tr_mask.sum() < 200 or te_mask.sum() == 0:
            continue
        val_season = max(df.season.values[tr_mask])
        va_mask = tr_mask & (df.season.values == val_season)
        tr_only = tr_mask & ~va_mask
        Mtr, fill = _prep(X[tr_only], cols)
        Mva, _ = _prep(X[va_mask], cols, fill)
        Mte, _ = _prep(X[te_mask], cols, fill)
        Xtr, Xva, Xte = Mtr.values, Mva.values, Mte.values
        decay = float(cand.params.get("train_decay", 1.0))
        wtr = np.power(decay, (y - df.season.values[tr_only]).astype(float))
        wva = np.power(decay, (y - df.season.values[va_mask]).astype(float))
        fit_params = {k: v for k, v in cand.params.items() if k != "train_decay"}
        out = pd.DataFrame({"game_id": df.game_id.values[te_mask], "season": y, "week": df.week.values[te_mask]})
        if cand.model == "zero":
            out["pred_margin"] = float(np.mean(df.margin.values[tr_mask]))
            out["pred_total"] = float(np.mean(df.total.values[tr_mask]))
            out["p_home"] = float(np.mean(df.home_won.values[tr_mask]))
            out["margin_sd"] = float(np.std(df.margin.values[tr_mask]))
        elif cand.model == "market":
            out["pred_margin"] = df.mkt_spread_margin.values[te_mask]
            out["pred_total"] = df.mkt_total.values[te_mask]
            out["p_home"] = df.mkt_p_home.values[te_mask]
            out["margin_sd"] = np.nan
        elif cand.target == "points":
            base = cand.model.replace("points_", "")
            ph, sdh, ch = _fit_predict_regression(base, fit_params, Xtr, df.home_points.values[tr_only], Xva,
                                                  df.home_points.values[va_mask], Xte, wtr=wtr, wva=wva)
            pa, sda, ca = _fit_predict_regression(base, fit_params, Xtr, df.away_points.values[tr_only], Xva,
                                                  df.away_points.values[va_mask], Xte, wtr=wtr, wva=wva)
            out["pred_margin"] = ph - pa
            out["pred_total"] = ph + pa
            # validation margin residual sd for winner prob
            out["margin_sd"] = float(np.sqrt(sdh**2 + sda**2))
            out["p_home"] = 1 - norm.cdf(-out["pred_margin"] / out["margin_sd"])
            chosen[y] = {"home": ch, "away": ca}
        elif cand.target == "winner":
            p, ch = _fit_predict_classifier(cand.model, fit_params, Xtr, df.home_won.values[tr_only], Xva,
                                            df.home_won.values[va_mask], Xte)
            out["p_home"] = p
            out["pred_margin"] = np.nan
            out["pred_total"] = np.nan
            out["margin_sd"] = np.nan
            chosen[y] = ch
        else:
            ycol = cand.target
            pred, sd, ch = _fit_predict_regression(cand.model, fit_params, Xtr, df[ycol].values[tr_only], Xva,
                                                   df[ycol].values[va_mask], Xte, wtr=wtr, wva=wva)
            if ycol == "margin":
                out["pred_margin"] = pred
                out["pred_total"] = np.nan
                out["margin_sd"] = sd
                out["p_home"] = 1 - norm.cdf(-pred / sd)
            else:
                out["pred_total"] = pred
                out["pred_margin"] = np.nan
                out["margin_sd"] = np.nan
                out["p_home"] = np.nan
            chosen[y] = ch
        preds.append(out)
        if verbose:
            print(f"    {cand.name}: fold {y} done ({time.perf_counter() - t0:.0f}s) {chosen.get(y, '')}")
    P = pd.concat(preds, ignore_index=True) if preds else pd.DataFrame()
    record = {
        "candidate": asdict(cand), "spec_hash": cand.spec_hash(), "feature_hash": feature_hash(cand.feature_set),
        "n_features": len(cols), "folds": list(folds), "chosen": {str(k): v for k, v in chosen.items()},
        "runtime_s": round(time.perf_counter() - t0, 1),
    }
    return P, record


def evaluate(P: pd.DataFrame, df: pd.DataFrame) -> dict:
    """Per-season and segment metrics for a prediction frame joined to targets."""
    d = P.merge(df[["game_id", "margin", "total", "home_won", "neutral", "postseason", "conference_game"]],
                on="game_id", how="left")
    d["postseason"] = d["postseason"].astype(bool)
    d["neutral"] = d["neutral"].astype(bool)
    d["conference_game"] = d["conference_game"].astype(bool)
    res: dict = {"by_season": {}, "by_segment": {}}

    def block(x: pd.DataFrame) -> dict:
        o: dict = {"n": int(len(x))}
        if x.pred_margin.notna().any():
            m = x[x.pred_margin.notna()]
            o["margin"] = margin_metrics(m.pred_margin.values, m.margin.values).to_dict()
        if x.pred_total.notna().any():
            t = x[x.pred_total.notna()]
            o["total"] = total_metrics(t.pred_total.values, t.total.values).to_dict()
        if x.p_home.notna().any():
            w = x[x.p_home.notna()]
            o["winner"] = winner_metrics(w.p_home.values, w.home_won.values).to_dict()
        return o

    for s, x in d.groupby("season"):
        res["by_season"][int(s)] = block(x)
    for name, fn in SEGMENTS.items():
        res["by_segment"][name] = block(d[fn(d)])
    res["pooled"] = block(d)
    return res


def leaderboard_row(name: str, ev: dict) -> dict:
    row = {"candidate": name}
    pooled = ev["pooled"]
    for key, sub in (("margin", "mae"), ("margin", "rmse"), ("margin", "bias"), ("margin", "fav_tail_bias"),
                     ("total", "mae"), ("total", "bias"), ("winner", "log_loss"), ("winner", "brier")):
        row[f"{key}_{sub}"] = pooled.get(key, {}).get(sub)
    row["wk1_margin_mae"] = ev["by_segment"]["week_1"].get("margin", {}).get("mae")
    row["wk1_total_mae"] = ev["by_segment"]["week_1"].get("total", {}).get("mae")
    row["wk4p_margin_mae"] = ev["by_segment"]["weeks_4_plus"].get("margin", {}).get("mae")
    row["wk4p_total_mae"] = ev["by_segment"]["weeks_4_plus"].get("total", {}).get("mae")
    for s, b in ev["by_season"].items():
        row[f"m_mae_{s}"] = b.get("margin", {}).get("mae")
        row[f"t_mae_{s}"] = b.get("total", {}).get("mae")
    return row


def run_tournament(cands: list[Candidate], df: pd.DataFrame, out_dir: Path, folds=DEFAULT_FOLDS,
                   dataset_meta: dict | None = None, verbose: bool = True) -> pd.DataFrame:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "preds").mkdir(exist_ok=True)
    X = matchup_frame(df)
    rows = []
    with (out_dir / "registry.jsonl").open("a") as reg:
        for cand in cands:
            if verbose:
                print(f"== {cand.name} [{cand.model} / {cand.target} / {cand.feature_set}]")
            P, rec = run_candidate(cand, df, X, folds, verbose=verbose)
            if not len(P):
                continue
            ev = evaluate(P, df)
            rec["metrics"] = ev
            rec["dataset"] = dataset_meta or {}
            rec["ran_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            reg.write(json.dumps(rec, default=float) + "\n")
            P.to_parquet(out_dir / "preds" / f"{cand.name}.parquet", index=False)
            row = leaderboard_row(cand.name, ev)
            row["runtime_s"] = rec["runtime_s"]
            rows.append(row)
            if verbose:
                print("   ", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()
                             if k in ("margin_mae", "total_mae", "winner_log_loss", "wk1_margin_mae",
                                      "wk4p_margin_mae", "wk1_total_mae")})
    lb = pd.DataFrame(rows)
    return lb
