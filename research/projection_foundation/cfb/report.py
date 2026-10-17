"""summary.json (sift.projection_foundation.sport_summary.v1), experiment records and RESULTS_CFB.md."""
from __future__ import annotations

import json
import subprocess
import time

import numpy as np

from pf_core import VERSION as PF_VERSION
from pf_core import registry

from . import paths
from . import narrative as nv

SPLIT_TEXT = {
    "development": "2015-2019 seasons (2015 = warm-up for E; ladder metrics 2016-2019). 2020 is COVID-anomalous and excluded",
    "validation": "2021-2022 seasons (model selection)",
    "holdout": "2023-2024 seasons (scored once for the frozen winner, baseline, incumbents, market)",
    "holdout_2025": "2025 season (second holdout, scored once)",
    "live_2026": "2026 weeks played to date (ESPN source; sanity only)",
}


def _sha():
    try:
        return subprocess.check_output(["git", "-C", str(paths.REPO_ROOT), "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _c(o):
    if isinstance(o, dict):
        return {str(k): _c(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_c(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else round(float(o), 4)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _model_entry(r):
    keys = ["model", "split", "n_obs", "n_clusters", "mae", "rmse", "bias", "crps", "log_score", "cover_50", "cover_80",
            "cover_90", "pit_var_ratio", "vs_baseline", "vs_incumbent", "verdict"]
    e = {k: r.get(k) for k in keys}
    for k in ("vs_market", "vs_v2_research", "line_brier", "line_ece"):
        if r.get(k) is not None:
            e[k] = r[k]
    return e


def _split_market(rows):
    keep = [r for r in rows if r["model"] != "market_close"]
    mkt = [r for r in rows if r["model"] == "market_close"]
    return keep, [{k: r.get(k) for k in ("split", "n_obs", "mae", "rmse", "bias", "crps", "cover_80", "pit_var_ratio")} for r in mkt]


def _game_verdicts(rows, best):
    for r in rows:
        m = r["model"]
        if m == best:
            r["verdict"] = "best"
        elif m == "A_season_avg":
            r["verdict"] = "baseline"
        elif m.startswith("incumbent") or m.startswith("v2_"):
            r["verdict"] = "incumbent"
        elif m == "market_close":
            r["verdict"] = "market_benchmark"
        else:
            vb = r.get("vs_best")
            r["verdict"] = "competitive" if (vb and not (vb["ci95"][1] < 0 or vb["ci95"][0] > 0)) else "rejected"
    return rows


def _holdout(rows, best):
    h = [r for r in rows if r["split"] == "holdout" and r["model"] == best]
    if not h:
        return {}
    r = h[0]
    out = {"model": best, "n_obs": r["n_obs"], "mae": r["mae"], "crps": r["crps"], "bias": r["bias"],
           "cover_80": r["cover_80"], "vs_baseline": r.get("vs_baseline") or {}, "vs_incumbent": r.get("vs_incumbent") or {}}
    if r.get("vs_market"):
        out["vs_market"] = r["vs_market"]
    h25 = [x for x in rows if x["split"] == "holdout_2025" and x["model"] == best]
    if h25:
        out["holdout_2025"] = {k: h25[0].get(k) for k in ("n_obs", "mae", "crps", "bias", "cover_80", "vs_baseline", "vs_incumbent", "vs_market")}
    lv = [x for x in rows if x["split"] == "live_2026" and x["model"] == best]
    if lv:
        out["live_2026"] = {k: lv[0].get(k) for k in ("n_obs", "mae", "crps", "bias", "cover_80", "vs_baseline", "vs_market")}
    return out


def _ready(fam, rows, best, has_inc):
    st, why = nv.READINESS.get(fam) or nv.auto_readiness(rows, best, has_inc)
    return {"readiness": st, "readiness_reason": why}


def families(S) -> list[dict]:
    fams = []
    meta = nv.FAMILY_META
    for fam, F in S.get("team_families", {}).items():
        m = meta.get(fam, {})
        keep, mkt = _split_market(F["rows"])
        fams.append({"family": fam, "unit": "team-game", "opportunity_family": m.get("opportunity"),
                     "population": m.get("population", "FBS-vs-FBS regular season team-games"),
                     "models": [_model_entry(r) for r in keep], "market_benchmark": mkt, "best_model": F["best"],
                     "holdout": _holdout(F["rows"], F["best"]),
                     "distribution": {"kind": F["dist_kind"], "dev_crps_by_kind": F["dist_kind_dev_crps"]},
                     "stack_weights": F.get("stack_weights"),
                     **_ready(fam, F["rows"], F["best"], fam == "team_points")})
    GF = S.get("game_families")
    if GF:
        for fam in ("game_total", "margin"):
            rows = _game_verdicts([dict(r) for r in GF["rows"] if r["family"] == fam], GF["best"])
            m = meta.get(fam, {})
            keep, mkt = _split_market(rows)
            fams.append({"family": fam, "unit": "game", "opportunity_family": "plays",
                         "population": "FBS-vs-FBS regular season games; derived from the joint team-score model",
                         "models": [_model_entry(r) for r in keep], "market_benchmark": mkt, "best_model": GF["best"],
                         "holdout": _holdout(rows, GF["best"]),
                         "distribution": {"kind": f"joint_{GF['kind']}", "dev_kinds": GF["kinds"]},
                         **_ready(fam, rows, GF["best"], True)})
    for fam, F in S.get("players", {}).items():
        from .players import GATE_FAMILY, FAMILIES
        role = FAMILIES[fam][0]
        fams.append({"family": GATE_FAMILY[fam], "unit": "player-game",
                     "opportunity_family": {"pass_yds": "passing_attempts", "rush_yds": "carries", "rec_yds": "receptions"}.get(fam),
                     "population": nv.PLAYER_POP[role], "models": [_model_entry(r) for r in F["rows"]],
                     "best_model": F["best"], "holdout": _holdout(F["rows"], F["best"]),
                     "distribution": {"kind": F["dist_kind"], "dev_crps_by_kind": F["dist_kind_dev_crps"]},
                     **_ready(GATE_FAMILY[fam], F["rows"], F["best"], False)})
    for fam, why in nv.INSUFFICIENT.items():
        fams.append({"family": fam, "unit": "player-game", "opportunity_family": None, "population": "-", "models": [],
                     "best_model": None, "holdout": {}, "readiness": "INSUFFICIENT DATA", "readiness_reason": why})
    return fams


def frozen_model(S):
    tp = S.get("team_families", {}).get("team_points", {})
    fz = {"alphas": S.get("alphas"), "decay": None, "history": None,
          "team_points": {"best": tp.get("best"), "stack_weights": tp.get("stack_weights")},
          "g_alphas": S.get("g_alphas"), "joint_kind": S.get("game_families", {}).get("kind"), "players": {}}
    from . import build
    fz["decay"], fz["history"] = build.DECAY, build.HISTORY
    fams = {f["family"]: f for f in families(S)}
    from .players import GATE_FAMILY
    for fam, F in S.get("players", {}).items():
        fz["players"][fam] = {"best": F["best"], "stack_weights": F.get("stack_weights"), "dist_kind": F["dist_kind"],
                              "readiness": fams.get(GATE_FAMILY[fam], {}).get("readiness", "SHADOW VALIDATION")}
    return fz


def write(S, log=print):
    paths.RESULTS.mkdir(parents=True, exist_ok=True)
    S["frozen_model"] = frozen_model(S)
    try:
        from . import data, inventory
        G, T = data.load()
        S["inventory"] = inventory.matrix(G, T, data.load_players())
    except Exception as e:  # noqa: BLE001
        log("inventory refresh failed:", repr(e))
    summary = {
        "schema": "sift.projection_foundation.sport_summary.v1", "sport": "CFB",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "code_sha": _sha(),
        "pf_core_version": PF_VERSION, "splits": SPLIT_TEXT, "inventory": S.get("inventory", []),
        "families": families(S), "experiments": nv.experiments(S), "error_investigations": nv.error_investigations(S),
        "opponent_adjustment": nv.opponent_adjustment(S), "opportunity": nv.opportunity(S), "calibration": nv.calibration(S),
        "failed_experiments": nv.failed(S), "gate": S.get("gate", {}), "priorities": nv.PRIORITIES,
        "defensive_rankings": nv.defense_summary(S), "pf_core_findings": nv.PF_CORE_FINDINGS,
        "frozen_model": S.get("frozen_model"),
    }
    summary = _c(summary)
    json.dump(summary, open(paths.RESULTS / "summary.json", "w"), indent=1)
    exp_dir = paths.RESULTS / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    for e in summary["experiments"]:
        rec = registry.ExperimentRecord(
            experiment_id=e["id"], sport="CFB", family=e.get("family", "team_points"), model=e.get("model", ""),
            model_version="pf-cfb-1.0.0", question=e["question"], hypothesis=e["hypothesis"], split=e.get("split", "development+validation"),
            train_period="walk-forward: every fit uses only games before the predicted block", eval_period=e.get("split", ""),
            features=e.get("features", []), hyperparameters=e.get("hyperparameters", {}),
            data_provenance=["research-data CFBD cache 2014-2025", "data/football/2026 ESPN log", "sportsdataverse-data releases"],
            n_obs=int(e.get("n_obs", 0)), n_independent=int(e.get("n_obs", 0)), n_clusters=int(e.get("n_clusters", 0)),
            point={"result": e["result"]}, verdict=e["decision"], limitations=e.get("limitations", []))
        registry.write_record(rec, str(exp_dir))
    md = nv.markdown(S, summary)
    open(paths.PF_ROOT / "RESULTS_CFB.md", "w").write(md)
    log("wrote summary.json, experiments, RESULTS_CFB.md")
    return summary
