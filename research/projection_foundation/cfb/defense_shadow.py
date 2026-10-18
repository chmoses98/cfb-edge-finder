"""Shadow publication of the Projection Foundation defensive ratings next to the script engine's CURRENT ranks.

    python -m cfb.defense_shadow [--asof ISO] [--out ratings/cfb/defense_latest.json]

Writes `ratings/cfb/defense_latest.json` (schema sift.defense_ratings.v1, documented in DEFENSE_RATINGS_CONTRACT.md).
Nothing in src/cfb_edge_finder/scripting is modified: the script engine's rank is recomputed by IMPORTING its own
`adjust.fit_league` on the same committed game log (read-only), so the side-by-side column is exactly the rank it
would publish for a game kicking off now.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from cfb import defense_ratings as dr  # noqa: E402
from cfb import paths  # noqa: E402

SCHEMA = "sift.defense_ratings.v1"
METRIC = "points_per_game"


def _sha() -> str:
    try:
        return subprocess.check_output(["git", "-C", str(paths.REPO_ROOT), "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def script_engine_defence(rows_path, cutoff_iso: str) -> dict[str, dict]:
    """The production engine's published points-allowed defence rank per FBS team, as of `cutoff_iso` (read-only use of
    cfb_edge_finder.scripting; PRODUCTION code is imported, never edited)."""
    src = str(paths.REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from cfb_edge_finder.scripting import adjust, gamelog  # noqa: E402

    rows = gamelog.read_jsonl(rows_path)
    lf = adjust.fit_league(rows, cutoff_iso, metric_ids=(METRIC,))
    mf = lf.fits[METRIC]
    out = {}
    for t in mf.fbs_teams:
        est = mf.get(t, adjust.DEFENSE)
        rank, universe = adjust.rank_in_universe(mf, t, adjust.DEFENSE, False)
        out[str(t)] = {"rank": rank, "universe": universe,
                       "rating": None if est is None or est.adjusted is None else round(float(est.adjusted), 3),
                       "raw_points_allowed": None if est is None or est.raw is None else round(float(est.raw), 3),
                       "se": None if est is None or est.se is None else round(float(est.se), 3),
                       "games": 0 if est is None else int(est.games), "stable": bool(lf.stable),
                       "fbs_components": int(lf.fbs_components), "median_fbs_games": float(lf.median_fbs_games),
                       "published_when_unstable": not lf.stable}
    return out


def _num(x, nd=3):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


def build_document(tab: pd.DataFrame, league: dict, se: dict, *, generated_utc: str, data_asof_utc: str, season: int,
                   config: dr.DefenseConfig, code_sha: str) -> dict:
    teams = []
    for r in tab.itertuples(index=False):
        s = se.get(str(r.team_id)) or {"rank": None, "universe": None, "rating": None, "games": 0, "stable": None}
        teams.append({
            "team_id": str(r.team_id), "team": getattr(r, "team", None) or str(r.team_id),
            "rating": _num(r.rating_c), "rating_unit": "points per game allowed against an average offence, relative to the "
                                                       "FBS-average defence (negative = better than average)",
            "se": _num(r.se_c),
            "rank": None if pd.isna(r.rank) else int(r.rank), "rank_point": int(r.rank_point),
            "rank_lo": int(r.rank_lo), "rank_hi": int(r.rank_hi), "rank_band": "80% posterior interval",
            "p_top10": _num(r.p_top10, 3), "p_top25": _num(r.p_top25, 3),
            "rank_status": r.rank_status, "rank_reasons": list(r.rank_reasons),
            "games_played": int(r.games_current), "effective_games": _num(r.eff_games, 2), "carry_share": _num(r.carry_share, 3),
            "connected_to_main": bool(r.in_main_component), "component_size": int(r.component_size),
            "fcs_pooled": bool(r.fcs_pooled), "fcs_games_played": int(r.fcs_games_current),
            "script_engine": {k: s.get(k) for k in ("rank", "universe", "rating", "raw_points_allowed", "se", "games", "stable",
                                                    "fbs_components", "published_when_unstable")},
        })
    return {"schema": SCHEMA, "role": "shadow", "authority": "research_only", "sport": "CFB", "season": season,
            "generated_utc": generated_utc, "data_asof_utc": data_asof_utc,
            "model": {"id": "pf-cfb-defense", "version": f"pf+{code_sha}",
                      "spec": "weighted ridge off/def ratings (the frozen team_points ridge): "
                              f"alpha {config.alpha}, prior-season decay {config.decay}, history {config.history} seasons, "
                              f"FCS pooled into one entity, conference offsets "
                              f"{'on (alpha %s)' % config.conf_alpha if config.conf_alpha is not None else 'off'}"},
            "league": league,
            "notes": ["Shadow output: the production script-engine ranks are unchanged; each team carries the engine's own "
                      "current rank in `script_engine` for comparison.",
                      "`rank` is null (rank_status SUPPRESSED) while the FBS schedule graph is disconnected; `rank_lo`/`rank_hi` "
                      "(80% band) are always published.",
                      "Validation: results/defense_ratings_validation.json (next-game points allowed, validation + holdout)."],
            "teams": teams}


def build(asof: str | None = None, conf: bool | None = None, log=print) -> dict:
    from cfb import data
    cfg = dr.DefenseConfig(**_frozen_cfg())
    G, T = data.load()
    now = pd.Timestamp(asof) if asof else pd.Timestamp.now(tz="UTC")
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    T = T[T["kickoff"] < now]
    season = int(T["season"].max())
    done = T[T["pts"].notna()]
    cutoff_block = season * 100 + 99                      # "all games so far" rating for games not yet played
    conf_of = None
    if cfg.conf_alpha is not None:
        conf_of = _conf_map(T, season)
    f = dr.fit(T, season, cutoff_block, cfg, conf_of=conf_of)
    names = T[T["team_div"] == "fbs"].drop_duplicates("team_id", keep="last").set_index("team_id")["team"].to_dict()
    tab = dr.rank_table(f, names={str(k): v for k, v in names.items()})
    cutoff_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    se = script_engine_defence(paths.FOOTBALL_2026 / "team_games.jsonl", cutoff_iso)
    asof_data = (done["kickoff"].max() + pd.Timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return build_document(tab, dr.league_summary(f), se, generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          data_asof_utc=asof_data, season=season, config=cfg, code_sha=_sha())


def _conf_map(T: pd.DataFrame, season: int) -> dict:
    d = T[(T["season"] == season) & (T["team_div"] == "fbs")].drop_duplicates("team_id", keep="last")
    return dict(zip(d["team_id"].astype(str), d["team_conf"].astype(str)))


def _frozen_cfg() -> dict:
    """Frozen ridge settings from results/summary.json, plus the adopted/rejected conference decision."""
    s = json.load(open(paths.RESULTS / "summary.json"))["frozen_model"]
    cfg = {"alpha": s["alphas"]["pts"], "decay": s["decay"], "history": s["history"]}
    p = paths.RESULTS / "defense_ratings_validation.json"
    if p.exists():
        v = json.load(open(p))
        pub = v.get("published_rating") or {}
        if pub.get("conference_offsets"):
            cfg["alpha"], cfg["conf_alpha"] = pub["alpha"], pub["conf_alpha"]
        if v.get("rank_band", {}).get("var_inflation"):
            cfg["var_inflation"] = v["rank_band"]["var_inflation"]
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None)
    ap.add_argument("--out", default=str(paths.RESULTS / "defense_latest.json"))
    a = ap.parse_args()
    doc = build(a.asof)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump(doc, open(a.out, "w"), separators=(",", ":"))
    lg = doc["league"]
    print(f"wrote {a.out}: {len(doc['teams'])} FBS defences, components={lg['fbs_schedule_components']}, "
          f"ranks_published={lg['ranks_published']}, season {doc['season']}")


if __name__ == "__main__":
    main()
