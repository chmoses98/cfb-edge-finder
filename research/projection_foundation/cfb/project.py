"""Forward projections for the current CFB week -> sift.projections.v1 (research_only; never betting authority).

    python -m cfb.project [--asof 2026-10-17T06:00:00Z] [--out results/projections_latest.json] [--no-players]

Uses the frozen choices recorded by run_walkforward in results/summary.json ("frozen_model"): ridge alphas,
the validated team-points model and its stack weights, the joint distribution kind, and the validated
player models. Everything else is recomputed walk-forward from the cached data, so the projection for a game
uses only games completed before it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pf_core import VERSION as PF_VERSION  # noqa: E402
from pf_core import gate  # noqa: E402

from cfb import build, data, gate_cfb, paths, players  # noqa: E402
from cfb import evaluate as ev  # noqa: E402
from cfb import team_eval as te  # noqa: E402

TEAM_KS = (14, 17, 21, 24, 28, 31, 35, 38, 42)
TOTAL_KS = (38, 42, 45, 48, 51, 55, 58, 62, 65)
MARGIN_KS = (-14, -10, -7, -3, 1, 4, 8, 11, 15)


def _q(dist, i, q):
    return float(dist.quantile(q)[i])


def _sha():
    import subprocess
    try:
        return subprocess.check_output(["git", "-C", str(paths.REPO_ROOT), "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _stack(preds, weights):
    names = list(weights)
    P = np.column_stack([preds[n].to_numpy(float) for n in names])
    w = np.array([weights[n] for n in names])
    return pd.Series(np.where(np.isfinite(P).all(1), P @ w, np.nan), index=preds[names[0]].index)


def team_block(X, Gu, frozen, now, asof, log):
    best = frozen["team_points"]["best"]
    if best == "G_game_ridge":
        from cfb import game_model as gm
        F = gm.game_features(X)
        al = frozen["g_alphas"]
        mu = gm.team_means(F, gm.walkforward(F, "margin", al["margin"]), gm.walkforward(F, "total", al["total"]), len(X))
        mu.index = X.index
    else:
        preds = te.ladder(X, "pts", log=log)
        if best == "F_stack":
            preds["F_stack"] = _stack(preds, frozen["team_points"]["stack_weights"])
        mu = preds[best]
    s = pd.Series(mu.to_numpy(), index=X["_rowid"].to_numpy())
    Gf = te.game_frame(X, {best: s})
    J = te.fit_joint(Gf, best, frozen.get("joint_kind", "empirical"))
    g = Gf[Gf["game_id"].isin(set(Gu["game_id"]))].reset_index(drop=True)
    g = g[g[f"{best}__h"].notna() & g[f"{best}__a"].notna()].reset_index(drop=True)
    D = J.distributions(g[f"{best}__h"].to_numpy(), g[f"{best}__a"].to_numpy())
    Xi = X.set_index(["game_id", "team_id"])
    cur = X[X["season"] == 2026].groupby("team_id")["pts"].apply(lambda s: int(s.notna().sum()))
    hist = X[X["season"] == 2026].sort_values("kickoff").groupby("team_id")["pts"].apply(lambda s: [float(v) for v in s.dropna()])
    names = X.drop_duplicates("team_id").set_index("team_id")["team"].to_dict()
    games = []
    for i, r in g.iterrows():
        entry = {"game_id": r["game_id"], "start_utc": pd.Timestamp(r["kickoff"]).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "home": names.get(r["home_id"], r["home_id"]), "away": names.get(r["away_id"], r["away_id"]),
                 "home_id": r["home_id"], "away_id": r["away_id"], "teams": [], "game": []}
        for side, oth, key in (("home_id", "away_id", "home"), ("away_id", "home_id", "away")):
            t, o = r[side], r[oth]
            row = Xi.loc[(r["game_id"], t)]
            dist = D[key]
            mean = float(dist.mean[i])
            th = {str(k): float(dist.prob_ge(np.full(len(dist), k))[i]) for k in TEAM_KS}
            gres = gate_cfb.team_gate(
                team_id=t, team=names.get(t, t), game_id=r["game_id"], start_utc=entry["start_utc"], mean=mean,
                dist_row_lo=0, dist_row_probs=dist.P[i], thresholds=th, sd=float(np.sqrt(dist.var[i])),
                week=int(r["week"]), cur_games_team=int(cur.get(t, 0)), cur_games_opp=int(cur.get(o, 0)),
                opp_fcs=row["opp_div"] != "fbs", team_fcs=row["team_div"] != "fbs", opp_def_rating=row.get("D_pts_def"),
                league_mu=row.get("D_pts_mu"), stat_history=hist.get(t, []), data_asof_utc=asof, now_utc=now,
                game_total_mean=float(D["total"].mean[i]), team_sum_mean=float(D["home"].mean[i] + D["away"].mean[i]))
            entry["teams"].append({"team": names.get(t, t), "team_id": t, "family": "team_points", "mean": round(mean, 2),
                                   "median": _q(dist, i, 0.5), "p10": _q(dist, i, 0.1), "p90": _q(dist, i, 0.9),
                                   "thresholds": {k: round(v, 4) for k, v in th.items()},
                                   "opponent_adjustment": {"opp_def_rating_pts": None if pd.isna(row.get("D_pts_def")) else round(float(row["D_pts_def"]), 2),
                                                           "sample_games": int(cur.get(o, 0))},
                                   "gate": {"status": gres["status"], "findings": [
                                       {k: f[k] for k in ("check", "severity", "what", "category", "action")} for f in gres["findings"]]}})
        for fam, dist, ks in (("game_total", D["total"], TOTAL_KS), ("margin", D["margin"], MARGIN_KS)):
            entry["game"].append({"family": fam, "orientation": "home minus away" if fam == "margin" else "home + away",
                                  "mean": round(float(dist.mean[i]), 2), "median": _q(dist, i, 0.5),
                                  "p10": _q(dist, i, 0.1), "p90": _q(dist, i, 0.9),
                                  "thresholds": {str(k): round(float(dist.prob_ge(np.full(len(dist), k))[i]), 4) for k in ks}})
        st = max((t_["gate"]["status"] for t_ in entry["teams"]), key=lambda s: {"PASS": 0, "REVIEW": 1, "BLOCK": 2}[s])
        entry["gate_status"] = st
        games.append(entry)
    return games


def player_block(X, T_all, Gu, frozen, now, asof, log):
    P = data.load_players()
    U = players.build_panel(P, T_all)
    U = players.team_shares(U, T_all)
    U = U[U["team_div"] == "fbs"]
    Df = players.player_features(U)
    Df = players.attach_team(Df, X)
    up = Df["game_id"].isin(set(Gu["game_id"]))
    avail = {}
    try:
        av = json.load(open(paths.FOOTBALL_2026 / "availability.json"))
        avail = {k: v for k, v in av.items()}
    except Exception:  # noqa: BLE001
        pass
    names_t = X.drop_duplicates("team_id").set_index("team_id")["team"].to_dict()
    opp_of = X[X["game_id"].isin(set(Gu["game_id"]))].set_index(["game_id", "team_id"])["opp_id"]
    out = []
    for fam, (role, opp, stat, *_r) in players.FAMILIES.items():
        fz = frozen.get("players", {}).get(fam)
        if not fz:
            continue
        preds = players.ladder(Df, fam, log=log)
        best = fz["best"]
        if best == "F_stack":
            preds["F_stack"] = _stack(preds, fz["stack_weights"])
        mu = preds[best]
        count = stat in ("pass_att", "carries", "receptions")
        lo, K = (0, 81) if count else (-30, 631)
        cand = Df[role]
        dmask = cand & Df["season"].between(2016, 2019) & mu.notna() & ~Df["post"]
        if role == "passer":
            dmask &= Df["has_pass"]
        prm = ev.fit_dist(fz["dist_kind"], Df.loc[dmask, stat], mu[dmask], lo, K)
        sel = up & cand & mu.notna()
        if not sel.any():
            continue
        dist = ev.make(prm, mu[sel].to_numpy())
        oppcol = {"passer": "pass_att", "rusher": "carries", "receiver": "receptions"}[role]
        histd = Df[(Df["season"] == 2026) & ~up].sort_values("kickoff").groupby("player_id")
        sh = histd[stat].apply(list).to_dict()
        oh = histd[oppcol].apply(list).to_dict()
        gfam = players.GATE_FAMILY[fam]
        ks = {"pass_yds": (150, 200, 250, 300), "pass_att": (25, 30, 35, 40), "carries": (10, 15, 20),
              "rush_yds": (40, 60, 80, 100), "receptions": (2, 4, 6, 8), "rec_yds": (40, 60, 80, 100)}[fam]
        for j, (idx, r) in enumerate(Df[sel].iterrows()):
            th = {str(k): round(float(dist.prob_ge(np.full(len(dist), k))[j]), 4) for k in ks}
            inj = None
            a = avail.get(str(r["team_id"]), {})
            if role == "passer" and a.get("qb_name") and a.get("qb_status") and a["qb_name"] == r["name"]:
                inj = a["qb_status"]
            gi = gate.GateInput(sport="CFB", family=gfam, entity_id=str(r["player_id"]), entity_name=r["name"],
                                team=names_t.get(r["team_id"]), game_id=str(r["game_id"]),
                                game_start_utc=pd.Timestamp(r["kickoff"]).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                mean=float(dist.mean[j]), data_asof_utc=asof, injury_status=inj,
                                expected_opportunity=None, opportunity_history=oh.get(r["player_id"], [])[-8:],
                                stat_history=sh.get(r["player_id"], [])[-8:], support_lo=lo, probs=list(dist.P[j]),
                                thresholds=th, sd=float(np.sqrt(dist.var[j])),
                                model_n_obs=len(sh.get(r["player_id"], [])))
            gr = gate.evaluate(gi, now_utc=now)
            out.append({"player_id": str(r["player_id"]), "name": r["name"], "team": names_t.get(r["team_id"]),
                        "opponent": names_t.get(opp_of.get((r["game_id"], r["team_id"]))), "game_id": str(r["game_id"]),
                        "family": gfam, "mean": round(float(dist.mean[j]), 2), "median": float(dist.median[j]),
                        "p10": float(dist.quantile(0.1)[j]), "p90": float(dist.quantile(0.9)[j]), "thresholds": th,
                        "opportunity": None,
                        "opponent_adjustment": {"factor": None if pd.isna(r.get(f"oppf_{players.OPP_EFF[role]}")) else round(float(r[f"oppf_{players.OPP_EFF[role]}"]), 3),
                                                "sample_games": None},
                        "adjustments": [f"model {best}; role '{role}' from the team's previous games this season",
                                        "receiving uses completions x reception share (targets unavailable)" if role == "receiver" else
                                        "team volume from schedule-adjusted ridge ratings"],
                        "readiness": fz.get("readiness", "SHADOW VALIDATION"),
                        "gate": {"status": gr["status"], "findings": [{k: f[k] for k in ("check", "severity", "what", "category", "action")}
                                                                      for f in gr["findings"]]},
                        "data_asof_utc": asof})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None)
    ap.add_argument("--out", default=str(paths.RESULTS / "projections_latest.json"))
    ap.add_argument("--no-players", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    log = lambda *x: print(f"[{time.time() - t0:6.0f}s]", *x, flush=True)  # noqa: E731
    summ = json.load(open(paths.RESULTS / "summary.json"))
    frozen = summ["frozen_model"]
    now_ts = pd.Timestamp(a.asof) if a.asof else pd.Timestamp.now(tz="UTC")
    now_ts = now_ts.tz_localize("UTC") if now_ts.tzinfo is None else now_ts.tz_convert("UTC")
    now = now_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    G, T = data.load()
    T = build.add_derived(T)
    done = T[(T["kickoff"] < now_ts) & T["pts"].notna()]
    asof = (done["kickoff"].max() + pd.Timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    T = T[T["kickoff"] < now_ts]                  # never use a game at/after the projection time
    Gu, Tu = data.upcoming(now_ts, T)
    out = {"schema": "sift.projections.v1", "sport": "CFB", "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "data_asof_utc": asof,
           "model": {"id": "pf-cfb", "version": f"pf-{PF_VERSION}+{_sha()}", "authority": "research_only"},
           "families": {f["family"]: {"readiness": f["readiness"], "model": f.get("best_model"), "reason": f["readiness_reason"]}
                        for f in summ["families"]},
           "games": [], "players": [],
           "notes": ["Research-only projections. They never change betting authority.",
                     "Team points, game total and margin come from ONE joint distribution (coherent by construction).",
                     "A BLOCK projection is written so the app can explain why it is withheld; do not display its numbers."]}
    if Gu.empty:
        log("no upcoming games in the current week window")
    else:
        T_all = pd.concat([T, Tu], ignore_index=True)
        X = build.features(T_all, frozen["alphas"], log)
        X = X.merge(pd.concat([G, Gu])[["game_id", "home_id", "close_spread", "close_total"]].drop_duplicates("game_id"),
                    on="game_id", how="left")
        X["_rowid"] = X.index
        out["games"] = team_block(X, Gu, frozen, now, asof, log)
        if not a.no_players:
            try:
                out["players"] = player_block(X, T_all, Gu, frozen, now, asof, log)
            except Exception as e:  # noqa: BLE001
                log("player projections failed:", repr(e))
                out["notes"].append(f"player projections unavailable this run: {e!r}")
    st = [t["gate"]["status"] for g in out["games"] for t in g["teams"]] + [p["gate"]["status"] for p in out["players"]]
    out["gate_summary"] = {s: st.count(s) for s in ("PASS", "REVIEW", "BLOCK")}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w"), separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else str(o))
    log(f"wrote {a.out}: {len(out['games'])} games, {len(out['players'])} player projections, gate {out['gate_summary']}")


if __name__ == "__main__":
    main()
