#!/usr/bin/env python3
"""Rebuild the frozen assets of src/cfb_edge_finder/oae/frozen/ and the parity fixture (offline, research inputs).

    CFB_WORK=/tmp/cfb_work python research/oae_integration/build_frozen_assets.py RESEARCH_CHECKOUT

RESEARCH_CHECKOUT = a checkout of `claude/sift-phase-15-sports-forecasting-rz966p` (7ebddfc6). CFB_WORK holds the
research inputs exactly as #307/#308 built them: team_games.parquet (challenger/data.py on the vault ESPN summaries +
that branch's 2026 game log), periods.parquet (phase15/periods_data.py), 2014-2026/ (vault CFBD cache v1-292f3aac),
feat_s2026.parquet (p15model.walk_forward, Params15(periods=True), season 2026). Requires pandas, pyarrow, scipy.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "src/cfb_edge_finder/oae/frozen"
FIX = REPO / "tests/fixtures/oae"
WORK = Path(os.environ.get("CFB_WORK", "/tmp/cfb_work"))
SEASON = 2026
P307_BRANCH, P307_SHA = "claude/sift-predictive-breakthrough-j1m6df", "8742f2a95f7d843b640314617785dbe55ce9e2f5"
P308_BRANCH, P308_SHA = "claude/sift-phase-15-sports-forecasting-rz966p", "7ebddfc6e3e07c6840288ff7e658df68b0301134"
KEYS = ("game_id", "season", "date", "kickoff_utc", "team_id", "opponent_id", "site", "team_division",
        "opponent_division", "points_for", "points_against", "box_plays", "box_total_yards", "pbp_success_plays",
        "pbp_scrim_plays", "pbp_drives", "pbp_drive_points", "pp_q1", "pp_q2", "pp_q3", "pp_q4")


def git_bytes(rev: str, path: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{path}"], check=True, capture_output=True).stdout


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def gz_jsonl(rows) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as f:
        for r in rows:
            f.write((json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n").encode())
    return buf.getvalue()


def compact(tg: pd.DataFrame) -> list[dict]:
    out = []
    for r in tg.to_dict("records"):
        d = {k: r[k] for k in KEYS}
        for k, v in d.items():
            if isinstance(v, float) and not np.isfinite(v):
                d[k] = None
            elif isinstance(v, (np.integer,)):
                d[k] = int(v)
        d["season"] = int(d["season"])
        out.append(d)
    return out


def main(research: Path) -> None:
    sys.path[:0] = [str(research), str(research / "phase15")]
    import p15model as X
    from challenger import model as M

    files = {}
    p307 = git_bytes(P307_SHA, "research/cfb_challenger/frozen_params.json")
    p15 = git_bytes(P308_SHA, "research/cfb_challenger/phase15/frozen_p15.json")
    nbq = git_bytes(P308_SHA, "research/cfb_challenger/phase15/frozen_p15_period_nb.parquet")
    assert sha(nbq) == json.loads(p15)["period_nb_sha256"]
    nb = pd.read_parquet(io.BytesIO(nbq))
    buf = io.BytesIO()
    np.savez_compressed(buf, **{f"{s}_mu": q.mu.to_numpy(float) for s, q in nb.groupby("seg")},
                        **{f"{s}_y": q.y.to_numpy(np.int16) for s, q in nb.groupby("seg")})
    tg = pd.read_parquet(WORK / "team_games.parquet")
    per = pd.read_parquet(WORK / "periods.parquet")
    tgp = X.add_period_points(tg, per)
    prior = compact(tgp[tgp.season.isin([SEASON - 2, SEASON - 1])])
    pre = M.preseason_table(WORK / "2014-2026")
    pre = pre[pre.season == SEASON]
    pre_doc = {"season": SEASON, "teams": {str(r.team): [float(r.talent_z), float(r.ret_z)] for r in pre.itertuples()}}
    blobs = {"frozen_params_p307.json": p307, "frozen_p15.json": p15, "period_nb.npz": buf.getvalue(),
             "prior_team_games.jsonl.gz": gz_jsonl(prior),
             "preseason.json": json.dumps(pre_doc, sort_keys=True, indent=0).encode()}
    OUT.mkdir(parents=True, exist_ok=True)
    for name, b in blobs.items():
        (OUT / name).write_bytes(b)
        files[name] = {"sha256": sha(b)}
    files["frozen_params_p307.json"].update(source=f"{P307_SHA}:research/cfb_challenger/frozen_params.json")
    files["frozen_p15.json"].update(source=f"{P308_SHA}:research/cfb_challenger/phase15/frozen_p15.json")
    files["period_nb.npz"].update(source=f"{P308_SHA}:research/cfb_challenger/phase15/frozen_p15_period_nb.parquet",
                                  source_sha256=sha(nbq), transform="parquet -> npz per segment, original row order")
    files["prior_team_games.jsonl.gz"].update(
        source="research team_games.parquet (vault espn_summary_postgame collected-20261116, repo parser) + "
               "periods.parquet (ESPN header linescores, valid only)", seasons=[SEASON - 2, SEASON - 1], rows=len(prior))
    files["preseason.json"].update(source="vault cfbd_api_cache v1-292f3aac talent + returning_production, "
                                          "z-scored within season (challenger.model.preseason_table)", teams=len(pre_doc["teams"]))
    prov = {"season": SEASON, "files": files,
            "sources": {"p307": {"branch": P307_BRANCH, "commit": P307_SHA, "pr": 307},
                        "p308": {"branch": P308_BRANCH, "commit": P308_SHA, "pr": 308}}}
    (OUT / "PROVENANCE.json").write_text(json.dumps(prov, indent=1, sort_keys=True) + "\n")
    parity_fixture(tg, tgp, M, X)


def parity_fixture(tg, tgp, M, X) -> None:
    """Research frozen forecasts for a sample of 2026 FBS-vs-FBS games, computed with the research code itself."""
    sys.path.insert(0, os.environ.get("P15EVAL_DIR", str(WORK / "p15eval")))
    import p15eval as PE
    from scipy.stats import norm
    fz = json.loads((OUT / "frozen_p15.json").read_text())
    games = M.games_table(tg)
    F = pd.read_parquet(WORK / f"feat_s{SEASON}.parquet")
    ev = games[(games.season == SEASON) & (games.home_div == "fbs") & (games.away_div == "fbs")].merge(F, on="game_id")
    ev = ev.sort_values(["date", "game_id"]).reset_index(drop=True)
    pick = sorted(set(np.linspace(0, len(ev) - 1, 14).astype(int)) | set(ev.index[ev.neutral][:2]))
    ev = ev.loc[pick].reset_index(drop=True)
    pred = M.predict(fz["fg"]["layer"], ev)
    d = ev.merge(pred, on="game_id")
    n = ((d.n_h + d.n_a) / 2).to_numpy(float)
    sm, st = X.sigma_of(fz["fg"]["sigma"]["margin"], n), X.sigma_of(fz["fg"]["sigma"]["total"], n)
    nb = pd.read_parquet(io.BytesIO(git_bytes(P308_SHA, "research/cfb_challenger/phase15/frozen_p15_period_nb.parquet")))
    out = []
    for i, r in d.iterrows():
        pm = PE.pmf_from_margin_total(r.ch_margin, sm[i], r.ch_total, st[i], fz["fg"]["sigma"]["rho_mt"], 200)
        rec = {"game_id": r.game_id, "date": r.date, "home": r.home, "away": r.away, "neutral": bool(r.neutral),
               "n_h": int(r.n_h), "n_a": int(r.n_a), "margin": r.ch_margin, "total": r.ch_total, "sd_m": float(sm[i]),
               "p_home_win": float(norm.sf((0.5 - r.ch_margin) / sm[i])),
               "fg_spread": {str(k): pm.p_margin_gt(k) for k in (-10.5, -3.5, 0.5, 3.5, 7.5, 14.5)}, "periods": {}}
        for seg in ("q1", "q2", "q3", "q4", "h1"):
            fp = fz["periods"][seg]
            mus = []
            for side in ("h", "a"):
                ff, fa = (r.ch_home, r.ch_away) if side == "h" else (r.ch_away, r.ch_home)
                Xd = X.period_design(d.loc[[i]], side, seg, np.array([ff]), np.array([fa]))
                mus.append(float(X.ridge_apply(fp["coef"], Xd)[0]))
            q = nb[nb.seg == seg]
            nbp = X.NeighbourPMF(q.mu.to_numpy(), q.y.to_numpy())
            J = PE.ScorePMF(X.joint_independent(nbp.pmf(mus[0])[:70], nbp.pmf(mus[1])[:70], fp["tie_scale"]))
            rec["periods"][seg] = {"mh": mus[0], "ma": mus[1], "p_home_win": J.p_margin_gt(0.5),
                                   "spread": {str(k): J.p_margin_gt(k) for k in (0.5, 3.5, 7.5)},
                                   "total": {str(k): J.p_total_gt(k) for k in (3.5, 7.5, 10.5, 14.5, 20.5, 27.5)}}
        out.append(rec)
    FIX.mkdir(parents=True, exist_ok=True)
    (FIX / "parity_expected.json").write_text(json.dumps(
        {"source": f"research code at {P308_SHA} (challenger.model, phase15.p15model, p15eval) on research inputs",
         "games": out}, indent=1, default=float) + "\n")
    cur = compact(tgp[tgp.season == SEASON])
    (FIX / "team_games_2026_research.jsonl.gz").write_bytes(gz_jsonl(cur))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
