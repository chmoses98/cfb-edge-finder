#!/usr/bin/env python3
"""Reproduce frozen research V2, then freeze it as a production artifact.

*** ZERO METERED CFBD CALLS ***
Every input comes from durable research-data:
  data/research/v2/dataset_d025.parquet        the frozen V2 dataset
  data/research/v2/dataset_d025.parquet.meta.json
  data/research/v2/preds/ens_margin_d025_eq.parquet   persisted research
  data/research/v2/preds/struct_pre_ridge.parquet     out-of-sample
  data/research/v2/preds/eff_pre_ridge_d025.parquet   predictions
and the frozen spec docs/v2/V2_SPEC.json. Nothing here opens a socket.

*** REPRODUCE BEFORE YOU FREEZE (mission section 8) ***
The fit protocol below is a faithful port of
`research/v2/tournament.run_candidate` -- same standardisation, same
training-median fill, same alpha grid selected on the inner validation
season, same refit on train+val, same equal-weight ensemble. Before any
artifact is written, the port re-runs the research folds and compares its
own predictions against the PERSISTED research predictions. If they do
not agree to tolerance the script exits non-zero and writes nothing: a
model that cannot reproduce its research is not shadowed.

*** WHY THE ARTIFACT CARRIES A PREDICTION TABLE ***
The 2026 slate's features are already frozen in the dataset (preseason
tables plus prior-season opponent-adjusted state -- no 2026 result
exists in it, and the dataset was built 2026-09-02T06:24Z, before the
first Week 1 kickoff). Storing the resulting per-game predictions
alongside the model parameters makes the slate structurally immune to
mid-slate refitting: production reads a lookup, so there is no code path
that could re-derive a different number after Thursday's games land.
The parameters are stored too, so the table can be independently
re-derived and audited.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.modeling.v2.research_features import (  # noqa: E402
    FEATURE_SETS,
    matchup_frame,
)

TRAIN_FROM = 2015
ALPHA_GRID = (1.0, 10.0, 100.0, 1000.0)
PRODUCTION_SEASON = 2026
RESEARCH_FOLDS = (2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025)


# ------------------------------------------------------- research port


class Ridge:
    """Byte-faithful port of `research/v2/tournament._Ridge`.

    Standardise, ridge-penalise the standardised coefficients, keep the
    weighted target mean as the intercept. Reproduced rather than
    replaced with sklearn: sklearn's Ridge differs in how it handles the
    intercept and the penalty scale, and the whole point of this script
    is bit-comparability with the research numbers."""

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

    def as_dict(self, cols: list[str]) -> dict:
        return {
            "alpha": float(self.alpha),
            "features": list(cols),
            "mean": [float(v) for v in self.mean_],
            "std": [float(v) for v in self.std_],
            "coef": [float(v) for v in self.coef_],
            "y_mean": float(self.y_mean_),
        }


def prep(X: pd.DataFrame, cols: list[str], fill: pd.Series | None = None):
    """Port of `tournament._prep`: training-median fill, then 0.0 for any
    feature whose training median is itself undefined."""
    M = X[cols].astype(float)
    if fill is None:
        fill = M.median()
    return M.fillna(fill).fillna(0.0), fill


def fill_dict(fill: pd.Series, cols: list[str]) -> dict:
    """Training-median fill vector as plain JSON.

    Defensive against duplicate feature names: `matchup_frame` builds a
    wide superset and a feature set may name the same column twice, in
    which case `fill[c]` is a Series rather than a scalar. Take the first
    value -- they are by construction identical -- rather than letting a
    truthiness error abort an otherwise-valid build."""
    out: dict = {}
    for c in cols:
        v = fill[c]
        if isinstance(v, pd.Series):
            v = v.iloc[0]
        out[c] = None if pd.isna(v) else float(v)
    return out


def fit_fold(
    df: pd.DataFrame, X: pd.DataFrame, cols: list[str], target: str, test_mask: np.ndarray, test_season: int
) -> tuple[np.ndarray, float, dict, Ridge, pd.Series]:
    """One rolling-origin fit, exactly as `run_candidate` does it.

    Returns (test predictions, validation residual sd, chosen params,
    the FINAL refit model, the training fill vector)."""
    completed = df.completed.values
    fbs = df.both_fbs.values
    tr_mask = completed & (df.season.values < test_season) & (df.season.values >= TRAIN_FROM) & fbs
    val_season = int(max(df.season.values[tr_mask]))
    va_mask = tr_mask & (df.season.values == val_season)
    tr_only = tr_mask & ~va_mask

    Mtr, fill = prep(X[tr_only], cols)
    Mva, _ = prep(X[va_mask], cols, fill)
    Mte, _ = prep(X[test_mask], cols, fill)
    Xtr, Xva, Xte = Mtr.values, Mva.values, Mte.values
    ytr, yva = df[target].values[tr_only], df[target].values[va_mask]

    best, best_mae = None, np.inf
    for a in ALPHA_GRID:
        m = Ridge(a).fit(Xtr, ytr)
        mae = float(np.mean(np.abs(m.predict(Xva) - yva)))
        if mae < best_mae:
            best, best_mae = a, mae
    val_model = Ridge(best).fit(Xtr, ytr)
    resid_sd = float(np.std(yva - val_model.predict(Xva)))
    final = Ridge(best).fit(np.vstack([Xtr, Xva]), np.concatenate([ytr, yva]))
    return (
        final.predict(Xte),
        resid_sd,
        {"alpha": best, "val_mae": round(best_mae, 4), "val_season": val_season},
        final,
        fill,
    )


# ------------------------------------------------------ uncertainty port


def fit_scale_model(resid: np.ndarray, F: pd.DataFrame, cols: list[str]) -> dict:
    """Port of `research/v2/uncertainty.fit_scale_model` -- Gaussian
    log-likelihood fit of log-sd as a linear function of `cols`. Normal
    tails only: the research spec records that Student-t was NOT selected
    for margin, so the production path does not carry a t branch it would
    never take."""
    Z = np.column_stack([np.ones(len(F))] + [F[c].values.astype(float) for c in cols])
    r = np.asarray(resid, float)

    def nll(b):
        s = Z @ b
        return float(np.sum(s + 0.5 * (r**2) * np.exp(-2 * s)))

    b0 = np.zeros(Z.shape[1])
    b0[0] = np.log(np.std(r))
    res = optimize.minimize(nll, b0, method="L-BFGS-B")
    return {"cols": list(cols), "coef": [float(v) for v in res.x]}


def scale_sd(model: dict, F: pd.DataFrame) -> np.ndarray:
    Z = np.column_stack([np.ones(len(F))] + [F[c].values.astype(float) for c in model["cols"]])
    return np.exp(Z @ np.asarray(model["coef"], float))


# --------------------------------------------------------------- inputs


def materialise(repo_dir: Path, branch: str, out_dir: Path) -> dict:
    """Copy the durable research inputs out of origin/{branch} without
    checking it out. Read-only; zero network beyond git."""
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {
        "dataset": "data/research/v2/dataset_d025.parquet",
        "dataset_meta": "data/research/v2/dataset_d025.parquet.meta.json",
        "preds_ens": "data/research/v2/preds/ens_margin_d025_eq.parquet",
        "preds_struct": "data/research/v2/preds/struct_pre_ridge.parquet",
        "preds_eff": "data/research/v2/preds/eff_pre_ridge_d025.parquet",
    }
    subprocess.run(
        ["git", "fetch", "origin", branch, "--depth=1"], cwd=repo_dir, capture_output=True, timeout=600, check=False
    )
    paths = {}
    for key, rel in wanted.items():
        dest = out_dir / Path(rel).name
        if not dest.exists():
            show = subprocess.run(
                ["git", "show", f"origin/{branch}:{rel}"], cwd=repo_dir, capture_output=True, timeout=600
            )
            if show.returncode != 0:
                raise SystemExit(f"missing durable input {rel} on origin/{branch}")
            dest.write_bytes(show.stdout)
        paths[key] = dest
    return paths


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--data-branch", default="research-data")
    ap.add_argument("--work-dir", type=Path, required=True)
    ap.add_argument("--spec", type=Path, required=True, help="frozen docs/v2/V2_SPEC.json")
    ap.add_argument("--out", type=Path, required=True, help="artifact JSON to write")
    ap.add_argument("--margin-tolerance", type=float, default=1e-9)
    ap.add_argument("--reproduce-only", action="store_true")
    args = ap.parse_args()

    spec = json.loads(args.spec.read_text())
    spec_sha = hashlib.sha256(args.spec.read_bytes()).hexdigest()
    feature_sets = spec["feature_sets"]
    paths = materialise(args.repo_dir, args.data_branch, args.work_dir)

    df = pd.read_parquet(paths["dataset"])
    dataset_meta = json.loads(paths["dataset_meta"].read_text())
    dataset_sha = hashlib.sha256(paths["dataset"].read_bytes()).hexdigest()
    # The frozen dataset carries RAW per-game state columns; the model's
    # feature matrix is derived from them by the vendored, verbatim copy
    # of the research feature builder -- so production features are
    # produced by the same code that produced the research numbers.
    X = matchup_frame(df)
    for name in ("struct+pre", "eff+pre", "tot_eff"):
        assert list(feature_sets[name]) == list(FEATURE_SETS[name]), (
            f"frozen spec and ported feature builder disagree on {name!r} -- refusing to build"
        )

    report: dict = {
        "built_at": datetime.now(UTC).isoformat(),
        "spec_id": spec["spec_id"],
        "spec_sha256": spec_sha,
        "spec_declared_sha256": spec.get("spec_sha256"),
        "dataset_sha256": dataset_sha,
        "dataset_built_at": dataset_meta.get("built_at"),
        "dataset_cache_fetched_at": dataset_meta.get("cache_fetched_at"),
        "dataset_state_config": dataset_meta.get("state_config"),
        "cfbd_calls_made": 0,
    }

    # ---------------------------------------------------- reproduction
    persisted = pd.read_parquet(paths["preds_ens"])
    # Only the _d025 member is a like-for-like comparator; see the note
    # in the fold loop for why the other persisted file is not one.
    members = {"eff+pre": paths["preds_eff"]}
    completed = df.completed.values
    fbs = df.both_fbs.values

    repro_rows = []
    ens_ours: list[pd.DataFrame] = []
    oos_total: list[pd.DataFrame] = []
    for season in RESEARCH_FOLDS:
        te = completed & (df.season.values == season) & fbs
        if te.sum() == 0:
            continue
        member_preds = {}
        for set_name, cols in (("struct+pre", feature_sets["struct+pre"]), ("eff+pre", feature_sets["eff+pre"])):
            pred, sd, chosen, _m, _f = fit_fold(df, X, list(cols), "margin", te, season)
            member_preds[set_name] = pred
            row = {
                "component": set_name,
                "season": season,
                "alpha": chosen["alpha"],
                "val_sd": sd,
            }
            # *** WHICH PERSISTED FILE IS THE RIGHT COMPARATOR ***
            # The frozen spec's registry names are struct_pre_ridge_d025,
            # eff_pre_ridge_d025 and ens_margin_d025_eq -- all built on the
            # decay-0.25 dataset. research-data persists
            # `eff_pre_ridge_d025.parquet` (matching) but only
            # `struct_pre_ridge.parquet` WITHOUT the _d025 suffix, i.e. the
            # same candidate fit on a DIFFERENT dataset. Comparing against
            # it would be comparing against a different model, so the
            # struct member is verified ALGEBRAICALLY instead, below,
            # against the ensemble that the spec actually froze.
            comparator = members.get(set_name)
            if comparator is not None:
                persisted_member = pd.read_parquet(comparator)
                pm = persisted_member[persisted_member.season == season].set_index("game_id")
                ours = pd.Series(pred, index=df.game_id.values[te])
                joined_m = pm.join(ours.rename("ours"), how="inner")
                diff = np.abs(joined_m.pred_margin.values - joined_m.ours.values)
                row.update(
                    {
                        "n": int(len(joined_m)),
                        "max_abs_diff": float(np.max(diff)) if len(diff) else None,
                        "mean_abs_diff": float(np.mean(diff)) if len(diff) else None,
                    }
                )
            else:
                row["comparator"] = "not persisted on research-data; verified algebraically via the ensemble"
            repro_rows.append(row)
        ens = 0.5 * member_preds["struct+pre"] + 0.5 * member_preds["eff+pre"]
        ens_ours.append(pd.DataFrame({"game_id": df.game_id.values[te], "season": season, "ours": ens}))

        tot_pred, tot_sd, tot_chosen, _m, _f = fit_fold(df, X, list(feature_sets["tot_eff"]), "total", te, season)
        oos_total.append(
            pd.DataFrame(
                {
                    "game_id": df.game_id.values[te],
                    "season": season,
                    "pred_total_raw": tot_pred,
                    "actual_total": df.total.values[te],
                    "val_sd": tot_sd,
                    "alpha": tot_chosen["alpha"],
                }
            )
        )

    ens_ours_df = pd.concat(ens_ours, ignore_index=True)
    joined = persisted.merge(ens_ours_df, on=["game_id", "season"], how="inner")
    ens_diff = np.abs(joined.pred_margin.values - joined.ours.values)
    report["reproduction"] = {
        "per_component": repro_rows,
        "ensemble": {
            "n_games_compared": int(len(joined)),
            "n_persisted": int(len(persisted)),
            "max_abs_diff": float(np.max(ens_diff)),
            "mean_abs_diff": float(np.mean(ens_diff)),
            "tolerance": args.margin_tolerance,
        },
    }
    member_max = max(
        (r["max_abs_diff"] for r in repro_rows if r.get("max_abs_diff") is not None), default=0.0
    )

    # *** ALGEBRAIC CHECK ON THE UNPERSISTED MEMBER ***
    # The frozen ensemble is 0.5*struct + 0.5*eff. Our eff reproduces the
    # persisted eff exactly and our ensemble reproduces the persisted
    # ensemble exactly, so the struct member IMPLIED by the persisted
    # files (2*ens - eff) must equal ours -- and this asserts it directly
    # rather than leaving it as an inference.
    ours_struct = []
    for season in RESEARCH_FOLDS:
        te = completed & (df.season.values == season) & fbs
        if te.sum() == 0:
            continue
        pred, _sd, _ch, _m, _f = fit_fold(df, X, list(feature_sets["struct+pre"]), "margin", te, season)
        ours_struct.append(pd.DataFrame({"game_id": df.game_id.values[te], "season": season, "struct": pred}))
    ours_struct_df = pd.concat(ours_struct, ignore_index=True)
    eff_persisted = pd.read_parquet(paths["preds_eff"])[["game_id", "season", "pred_margin"]].rename(
        columns={"pred_margin": "eff"}
    )
    implied = persisted[["game_id", "season", "pred_margin"]].merge(eff_persisted, on=["game_id", "season"])
    implied["implied_struct"] = 2.0 * implied.pred_margin.values - implied.eff.values
    implied = implied.merge(ours_struct_df, on=["game_id", "season"], how="inner")
    struct_diff = np.abs(implied.implied_struct.values - implied.struct.values)
    report["reproduction"]["struct_member_algebraic"] = {
        "n_games_compared": int(len(implied)),
        "max_abs_diff": float(np.max(struct_diff)),
        "mean_abs_diff": float(np.mean(struct_diff)),
        "note": "implied_struct = 2*persisted_ensemble - persisted_eff",
    }

    ok = (
        float(np.max(ens_diff)) <= args.margin_tolerance
        and member_max <= args.margin_tolerance
        and float(np.max(struct_diff)) <= args.margin_tolerance
    )
    report["reproduction"]["passed"] = bool(ok)
    if len(joined) != len(persisted):
        report["reproduction"]["passed"] = False
        report["reproduction"]["coverage_error"] = (
            f"compared {len(joined)} of {len(persisted)} persisted rows"
        )
        ok = False

    print(json.dumps(report["reproduction"], indent=1))
    if not ok:
        print("\nREPRODUCTION FAILED -- refusing to write an artifact.", file=sys.stderr)
        return 1
    if args.reproduce_only:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.with_suffix(".reproduction.json").write_text(json.dumps(report, indent=2) + "\n")
        print("\nreproduction-only mode: no artifact written")
        return 0

    # ------------------------------------------- production fit (2026)
    te_2026 = (df.season.values == PRODUCTION_SEASON) & fbs
    if te_2026.sum() == 0:
        print("no 2026 FBS-vs-FBS games in the frozen dataset", file=sys.stderr)
        return 1

    margin_members = {}
    margin_preds = {}
    for set_name in ("struct+pre", "eff+pre"):
        cols = list(feature_sets[set_name])
        pred, sd, chosen, model, fill = fit_fold(df, X, cols, "margin", te_2026, PRODUCTION_SEASON)
        margin_preds[set_name] = pred
        margin_members[set_name] = {
            **model.as_dict(cols),
            "fill": fill_dict(fill, cols),
            "val_resid_sd": sd,
            "chosen": chosen,
        }
    pred_margin = 0.5 * margin_preds["struct+pre"] + 0.5 * margin_preds["eff+pre"]

    tot_cols = list(feature_sets["tot_eff"])
    pred_total_raw, tot_sd, tot_chosen, tot_model, tot_fill = fit_fold(
        df, X, tot_cols, "total", te_2026, PRODUCTION_SEASON
    )

    # Chronological affine recalibration, fit on OUT-OF-SAMPLE research
    # totals from every prior fold -- never on the season being predicted.
    oos_tot = pd.concat(oos_total, ignore_index=True).dropna(subset=["pred_total_raw", "actual_total"])
    b, a = np.polyfit(oos_tot.pred_total_raw.values, oos_tot.actual_total.values, 1)
    pred_total = a + b * pred_total_raw
    report["total_recalibration"] = {
        "form": "y = a + b * pred",
        "a": float(a),
        "b": float(b),
        "fit_rows": int(len(oos_tot)),
        "fit_seasons": sorted(int(s) for s in oos_tot.season.unique()),
    }

    # Uncertainty: fit on prior OUT-OF-SAMPLE residuals only.
    ens_oos = joined[["game_id", "season", "ours"]].rename(columns={"ours": "pred_margin"})
    facts = df[["game_id", "season", "week", "margin", "total", "both_fbs"]].copy()
    facts["game_id"] = facts.game_id.astype(ens_oos.game_id.dtype)
    m_oos = ens_oos.merge(facts, on=["game_id", "season"], how="inner").dropna(subset=["margin"])
    t_oos = oos_tot.merge(facts.drop(columns=["total"]), on=["game_id", "season"], how="inner")
    t_oos["pred_total"] = a + b * t_oos.pred_total_raw

    m_oos = m_oos.merge(
        t_oos[["game_id", "season", "pred_total"]], on=["game_id", "season"], how="left"
    )
    m_oos["early_w"] = (m_oos.week.values <= 3).astype(float)
    m_oos["fcs_involved"] = 0.0  # OOS rows are FBS-vs-FBS by construction
    m_oos["abs_pred_margin"] = np.abs(m_oos.pred_margin.values)
    m_oos = m_oos.dropna(subset=["pred_total"])
    margin_scale = fit_scale_model(
        (m_oos.margin.values - m_oos.pred_margin.values),
        m_oos,
        ["abs_pred_margin", "early_w", "fcs_involved", "pred_total"],
    )

    t_oos["early_w"] = (t_oos.week.values <= 3).astype(float)
    t_oos = t_oos.merge(ens_oos, on=["game_id", "season"], how="left").dropna(subset=["pred_margin"])
    t_oos["abs_pred_margin"] = np.abs(t_oos.pred_margin.values)
    total_scale = fit_scale_model(
        (t_oos.actual_total.values - t_oos.pred_total.values),
        t_oos,
        ["pred_total", "early_w", "abs_pred_margin"],
    )

    # Per-game frozen predictions for the prospective slate.
    slate = pd.DataFrame(
        {
            "game_id": df.game_id.values[te_2026].astype(str),
            "season": df.season.values[te_2026].astype(int),
            "week": df.week.values[te_2026].astype(int),
            "home_team": df.home.values[te_2026].astype(str),
            "away_team": df.away.values[te_2026].astype(str),
            "kickoff_utc": pd.to_datetime(df.kickoff.values[te_2026], utc=True).astype(str),
            "pred_margin": pred_margin,
            "pred_total": pred_total,
        }
    )
    slate["abs_pred_margin"] = np.abs(slate.pred_margin.values)
    slate["early_w"] = (slate.week.values <= 3).astype(float)
    slate["fcs_involved"] = 0.0
    slate["sd_margin"] = scale_sd(margin_scale, slate)
    slate["sd_total"] = scale_sd(total_scale, slate)
    slate["p_home"] = 1 - stats.norm.cdf(-slate.pred_margin.values / slate.sd_margin.values)

    games = [
        {
            "game_id": r.game_id,
            "season": int(r.season),
            "week": int(r.week),
            "home_team": r.home_team,
            "away_team": r.away_team,
            "kickoff_utc": r.kickoff_utc,
            "pred_margin": float(r.pred_margin),
            "pred_total": float(r.pred_total),
            "sd_margin": float(r.sd_margin),
            "sd_total": float(r.sd_total),
            "p_home": float(r.p_home),
        }
        for r in slate.itertuples(index=False)
    ]

    payload = {
        "schema_version": "v2_shadow_artifact_v1",
        "model_version": spec.get("production_model_version", "0.6.0-v2-shadow"),
        "spec_id": spec["spec_id"],
        "spec_sha256": spec_sha,
        "training_cutoff": spec["training_data_cutoff"],
        "training_seasons": [TRAIN_FROM, PRODUCTION_SEASON - 1],
        "prediction_season": PRODUCTION_SEASON,
        "dataset": {
            "sha256": dataset_sha,
            "built_at": dataset_meta.get("built_at"),
            "cache_fetched_at": dataset_meta.get("cache_fetched_at"),
            "state_config": dataset_meta.get("state_config"),
            "feature_hash": dataset_meta.get("feature_hash"),
        },
        "feature_sets": {k: list(feature_sets[k]) for k in ("struct+pre", "eff+pre", "tot_eff")},
        "margin_members": margin_members,
        "margin_weights": {"struct+pre": 0.5, "eff+pre": 0.5},
        "total_model": {
            **tot_model.as_dict(tot_cols),
            "fill": fill_dict(tot_fill, tot_cols),
            "val_resid_sd": tot_sd,
            "chosen": tot_chosen,
        },
        "total_recalibration": report["total_recalibration"],
        "uncertainty": {
            "margin_scale": margin_scale,
            "total_scale": total_scale,
            "tails": "normal",
            "continuity": "integer thresholds only; half-point strikes used as-is",
        },
        "reproduction": report["reproduction"],
        "games": games,
        "n_games": len(games),
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["artifact_sha256"] = hashlib.sha256(body).hexdigest()
    payload["built_at"] = report["built_at"]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "artifact": str(args.out),
                "artifact_sha256": payload["artifact_sha256"],
                "model_version": payload["model_version"],
                "n_games": len(games),
                "training_cutoff": payload["training_cutoff"],
                "margin_alpha": {k: v["chosen"]["alpha"] for k, v in margin_members.items()},
                "total_alpha": tot_chosen["alpha"],
                "total_recalibration": report["total_recalibration"],
                "pred_margin_range": [float(slate.pred_margin.min()), float(slate.pred_margin.max())],
                "pred_total_range": [float(slate.pred_total.min()), float(slate.pred_total.max())],
                "sd_margin_range": [float(slate.sd_margin.min()), float(slate.sd_margin.max())],
                "cfbd_calls_made": 0,
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
