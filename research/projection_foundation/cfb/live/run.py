"""Live shadow operation entry point (run from research/projection_foundation).

    python -m cfb.live.run --stage all --archive /path/to/projection-data [--now 2026-09-17T12:00:00Z] [--dry-run]

Stages (each idempotent; `all` runs them in order): capture, project, publish, results, settle, score, guard.
`--archive` is a checkout of the `projection-data` branch. `--dry-run` copies the archive to a temp dir and runs every stage
against the copy: live fetches, real projections, gate, archive writes, settle and score all happen, nothing outside the
temp copy is touched. `--now` fixes the clock (tests, replay); stage clocks then advance one second per stage so a capture
made "at now" is visible to the projection that follows it.

Authority: everything produced here is research_only and role=shadow.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from pf_core import archive_guard  # noqa: E402
from pf_core import availability as av  # noqa: E402
from pf_core import prospective as pr  # noqa: E402

from cfb import data, paths  # noqa: E402
from cfb.live import availability as la  # noqa: E402
from cfb.live import espn_injuries as ei  # noqa: E402
from cfb.live import incumbent, records, results  # noqa: E402

STAGES = ["capture", "project", "publish", "results", "settle", "score", "guard"]
SPORT = "CFB"
SEG_MIN_N = 30


def utc(ts) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


class Ctx:
    def __init__(self, a):
        self.a = a
        self.fixed_now = pd.Timestamp(a.now).tz_localize("UTC") if a.now and pd.Timestamp(a.now).tzinfo is None else (
            pd.Timestamp(a.now).tz_convert("UTC") if a.now else None)
        self.t0 = time.time()
        self.tmp = None
        self.root = os.path.abspath(a.archive)
        if a.dry_run:
            self.tmp = tempfile.mkdtemp(prefix="pf_cfb_dry_")
            dst = os.path.join(self.tmp, "archive")
            if os.path.isdir(os.path.join(self.root, ".git")):
                shutil.copytree(self.root, dst, symlinks=True)
            else:
                os.makedirs(dst, exist_ok=True)
                subprocess.run(["git", "init", "-q", dst], check=True)
                if os.path.isdir(self.root):
                    shutil.copytree(self.root, dst, dirs_exist_ok=True)
            self.root = dst
        else:
            os.makedirs(self.root, exist_ok=True)
            if not os.path.isdir(os.path.join(self.root, ".git")):
                subprocess.run(["git", "init", "-q", self.root], check=True)
        self.work = os.path.abspath(a.work)
        os.makedirs(self.work, exist_ok=True)
        self.summary = {"sport": SPORT, "dry_run": bool(a.dry_run), "authority": "research_only", "stages": {}}
        self.football = os.path.abspath(a.football_dir)
        self.frozen = os.path.abspath(a.frozen_dir)

    def now(self, stage: str) -> pd.Timestamp:
        if self.fixed_now is not None:
            return self.fixed_now + pd.Timedelta(seconds=STAGES.index(stage))
        return pd.Timestamp.now(tz="UTC").floor("s")

    def log(self, *x):
        print(f"[{time.time() - self.t0:6.0f}s]", *x, flush=True)


# --------------------------------------------------------------------------- helpers
def _load_sched(ctx) -> dict:
    return json.load(open(os.path.join(ctx.football, "schedule.json")))


def _horizon(now: pd.Timestamp, days: float | None) -> pd.Timestamp:
    return now + pd.Timedelta(days=days) if days else data.week_end(now)


def _fbs_event(e: dict, fbs_ids) -> bool:
    if fbs_ids is None:
        return True
    return any(str(e[s].get("conference_id")) in fbs_ids for s in ("home", "away"))


def _fbs_conference_ids():
    try:
        src = str(paths.REPO_ROOT / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from cfb_edge_finder.scripting.espn import FBS_CONFERENCE_IDS  # noqa: E402
        return {str(x) for x in FBS_CONFERENCE_IDS}
    except Exception:  # noqa: BLE001
        return None


def _id_lookup() -> dict:
    """(team_id, player name) -> ESPN athlete id from the player box (so a projected QB shares the id of his projections)."""
    try:
        P = data.load_players()
        P = P[P["season"] == paths.CURRENT]
        P = P.sort_values("game_id")
        return {(str(r.team_id), r.name): str(r.player_id) for r in P.drop_duplicates(["team_id", "name"], keep="last").itertuples()}
    except Exception:  # noqa: BLE001
        return {}


def _gamelog(ctx) -> list[dict]:
    return results.read_gamelog(os.path.join(ctx.football, "team_games.jsonl"))


def _write_json_if_changed(path: str, obj: dict, ignore=("generated_utc",)) -> bool:
    """Rewritable views: skip the write when nothing but timestamps moved (keeps the archive commit meaningful)."""
    strip = lambda d: {k: v for k, v in d.items() if k not in ignore} if isinstance(d, dict) else d  # noqa: E731
    if os.path.exists(path):
        try:
            if strip(json.load(open(path))) == strip(json.loads(json.dumps(obj, default=_default))):
                return False
        except Exception:  # noqa: BLE001
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"), default=_default)
    return True


def _default(o):
    if hasattr(o, "item"):
        return o.item()
    return str(o)


# --------------------------------------------------------------------------- 1. capture
def stage_capture(ctx, get=ei.http_get_json) -> dict:
    now = ctx.now("capture")
    cap = utc(now)
    sched = _load_sched(ctx)
    events = la.upcoming_events(sched, now.to_pydatetime(), _horizon(now, ctx.a.horizon_days).to_pydatetime(),
                                replay=ctx.fixed_now is not None)
    fbs = _fbs_conference_ids()
    events = [e for e in events if _fbs_event(e, fbs)]
    team_ids = sorted({str(e[s]["team_id"]) for e in events for s in ("home", "away")})
    ctx.log(f"capture: {len(events)} events, {len(team_ids)} teams")
    inj = ei.fetch_teams(team_ids, get) if team_ids else {}
    qb = la.qb_state(_gamelog(ctx), now.to_pydatetime())
    obs = la.build_observations(captured_utc=cap, events=events, injuries=inj, qb=qb, id_lookup=_id_lookup(), season=paths.CURRENT)
    # reuse the production data job's own availability.json capture (preserved immutably, once per new observed_at)
    repo_obs = []
    ap = os.path.join(ctx.football, "availability.json")
    if os.path.exists(ap):
        prior = [r["captured_utc"] for r in av.load_snapshots(ctx.root, SPORT) if r.get("source") == la.REPO_SOURCE]
        repo_obs = la.repo_job_observations(json.load(open(ap)), events, max(prior) if prior else None, paths.CURRENT)
        obs += [o for o in repo_obs if o.captured_utc < cap]
    probes = _probes(inj, team_ids, get)
    snap = av.write_snapshot(obs, ctx.root, SPORT, cap) if obs else {"written": 0, "rejected": 0, "duplicate": False, "path": None}
    stats = la.capture_stats([], events, {}, cap, inj)
    out = {"captured_utc": cap, "events": len(events), "teams": len(team_ids), "observations": len(obs),
           "snapshot": {k: snap.get(k) for k in ("written", "rejected", "duplicate")}, "snapshot_path": snap.get("path"),
           "team_feed": stats["team_feed"], "repo_job_observations": len(repo_obs), "probes": probes,
           "observations_by_kind": pd.Series([f"{o.source}:{o.kind}:{o.certainty}" for o in obs]).value_counts().to_dict() if obs else {}}
    if snap.get("errors"):
        out["snapshot_errors"] = snap["errors"]
    if team_ids and stats["team_feed"]["lists_read"] == 0:
        out["WARNING"] = "no injury list could be read for any team (source down or blocked); nothing is treated as 'no injuries'"
    ctx.log("capture:", json.dumps({k: v for k, v in out.items() if k != "snapshot_path"}, default=str)[:2500])
    return out


def _probes(inj: dict, team_ids: list, get) -> dict:
    """Diagnostics only (never archived): (a) when this run saw no injury record, resolve the REAL reference URLs recorded by the
    sidecar (tests/fixtures) so the record parser is checked against a live payload; (b) does ESPN serve a CFB depth chart?"""
    out: dict = {}
    seen = next((v.sample_keys for v in inj.values() if v.sample_keys), None)
    if seen:
        out["record_keys_this_run"] = seen
    else:
        try:
            fx = json.load(open(os.path.join(str(paths.PF_ROOT), "tests", "fixtures", "espn_injuries_2026-09-17T114253Z.json")))
            refs = [u for r in fx["teams"].values() for u in r["parsed"]["items"]][:3]
            res = []
            for u in refs:
                body, err = get(u)
                if isinstance(body, dict):
                    rec = ei.parse_record(body, u)
                    res.append({"ref_tail": u.split("/athletes/")[-1][:40], "keys": sorted(body.keys())[:25], "status_raw": rec.status_raw,
                                "status": rec.status, "has_athlete_ref": isinstance(body.get("athlete"), dict), "date": rec.date})
                else:
                    res.append({"ref_tail": u.split("/athletes/")[-1][:40], "error": err})
            out["recorded_refs_resolved_now"] = res
        except Exception as e:  # noqa: BLE001
            out["recorded_refs_error"] = repr(e)
    if team_ids:
        url = f"{ei.CORE}/seasons/{paths.CURRENT}/teams/{team_ids[0]}/depthcharts"
        body, err = get(url)
        out["depthchart_probe"] = {"url_tail": url.split("/leagues/")[-1], "ok": isinstance(body, dict), "error": err,
                                   "keys": sorted(body.keys())[:10] if isinstance(body, dict) else None,
                                   "count": body.get("count") if isinstance(body, dict) else None}
    return out


# --------------------------------------------------------------------------- 2. project
class LiveAvailability:
    def __init__(self, ctx, now: pd.Timestamp):
        self.now = utc(now)
        self.rows = av.load_snapshots(ctx.root, SPORT)
        self.latest = av.asof(self.rows, self.now)
        self.qb = la.qb_state(_gamelog(ctx), now.to_pydatetime())
        self.ids = _id_lookup()
        self.qb_by_team: dict[str, dict] = {}
        self.expected: dict[str, list[str]] = {}
        self.statuses: dict[str, int] = {}

    def qb_info(self, Gu):
        out = {}
        for r in Gu.itertuples():
            for t in (r.home_id, r.away_id):
                q = la.qb_assessment(str(t), str(r.game_id), self.qb.get(str(t)), self.latest, self.now, self.ids)
                out[str(t)] = q
                if q.get("player_id"):
                    self.expected.setdefault(str(r.game_id), []).append(str(q["player_id"]))
        return out

    def resolve(self, game_id, player_id, name, team_id, role):
        p = av.resolve_participation(self.latest, game_id, player_id, self.now)
        self.statuses[p.status] = self.statuses.get(p.status, 0) + 1
        self.expected.setdefault(game_id, []).append(player_id)
        return p


def stage_project(ctx, replay: bool | None = None) -> dict:
    from cfb import project
    now = ctx.now("project")
    nows = utc(now)
    live = LiveAvailability(ctx, now)
    sink: list[dict] = []
    replay = ctx.fixed_now is not None if replay is None else replay
    out = project.generate(now, players=not ctx.a.no_players, sink=sink, avail=live, replay=replay, log=ctx.log,
                           horizon_end=_horizon(now, ctx.a.horizon_days), wall_clock_stamp=ctx.fixed_now is None)
    if replay:
        out["generated_utc"] = nows
    json.dump(out, open(os.path.join(ctx.work, "projection_out.json"), "w"), separators=(",", ":"), default=_default)
    # incumbent: the script engine's own scoring baseline for the same games, unchanged
    inc = incumbent.load(ctx.frozen)
    inc_recs, inc_miss = incumbent.forecasts(inc, [{"game_id": g["game_id"], "start_utc": g["start_utc"], "home": g["home"],
                                                    "away": g["away"], "home_id": g["home_id"], "away_id": g["away_id"]}
                                                   for g in out["games"]], nows, out["model"]["version"])
    allrecs = sink + inc_recs
    # never archive for a game that already started (archive_guard-style belt and braces; pf_core rejects it as well)
    allrecs = [r for r in allrecs if pd.Timestamp(r["game_start_utc"]) > now]
    prior = pr.load_forecasts(ctx.root, SPORT)
    fresh, unchanged = records.drop_unchanged(allrecs, prior)
    st = pr.archive_forecasts(fresh, ctx.root, now_utc=nows)
    by_role = pd.Series([r["model_role"] for r in fresh]).value_counts().to_dict() if fresh else {}
    cut = {g["game_id"]: nows for g in out["games"]}
    cap = la.capture_stats(live.rows, [{"event_id": g, "start_utc": s} for g, s in
                                       ((g["game_id"], g["start_utc"]) for g in out["games"])],
                           {k: sorted(set(v)) for k, v in live.expected.items()}, nows)
    gates = out["gate_summary"]
    qbflags = pd.Series([f for g in out["games"] for f in (g.get("flags") or ["OK"])]).value_counts().to_dict() if out["games"] else {}
    summary = {"now": nows, "games": len(out["games"]), "players": len(out["players"]), "gate": gates,
               "records_built": len(allrecs), "records_unchanged_skipped": unchanged, "records_by_role_written": by_role,
               "archive": {k: v for k, v in st.items() if k != "paths"}, "incumbent_missing": inc_miss,
               "participation_counts_players": live.statuses, "qb_flags_games": qbflags,
               "availability_coverage": cap["coverage"], "availability_newest_age_minutes": cap["newest_age_minutes_max"],
               "late_rejected": st["late_rejected"]}
    ctx.summary["capture_stats"] = {**ctx.summary.get("capture_stats", {}), "projection": {k: summary[k] for k in
                                    ("availability_coverage", "availability_newest_age_minutes", "participation_counts_players")}}
    ctx.log("project:", json.dumps(summary, default=str)[:900])
    return summary


# --------------------------------------------------------------------------- 3. publish
def stage_publish(ctx) -> dict:
    now = ctx.now("publish")
    nows = utc(now)
    fp = os.path.join(ctx.work, "projection_out.json")
    if not os.path.exists(fp):
        raise SystemExit("publish: no projection_out.json (run the project stage first)")
    out = json.load(open(fp))
    wrote = []
    stamp = now.strftime("%Y-%m-%dT%H%MZ")
    if _write_json_if_changed(os.path.join(ctx.root, "projections", "cfb", "latest.json"), out):
        _write_json_if_changed(os.path.join(ctx.root, "projections", "cfb", f"{stamp}.json"), out)
        wrote.append("projections/cfb/latest.json")
    summ = json.load(open(paths.RESULTS / "summary.json"))
    if _write_json_if_changed(os.path.join(ctx.root, "summary", "cfb", "latest.json"), summ):
        wrote.append("summary/cfb/latest.json")
    # shadow defensive ratings next to the script engine's ranks
    try:
        from cfb import defense_shadow
        doc = defense_shadow.build(nows, log=ctx.log)
        if _write_json_if_changed(os.path.join(ctx.root, "ratings", "cfb", "defense_latest.json"), doc):
            _write_json_if_changed(os.path.join(ctx.root, "ratings", "cfb", f"defense_{stamp}.json"), doc)
            wrote.append("ratings/cfb/defense_latest.json")
        contract = os.path.join(str(paths.PF_ROOT), "DEFENSE_RATINGS.md")
        if os.path.exists(contract):
            dst = os.path.join(ctx.root, "ratings", "cfb", "README.md")
            if not os.path.exists(dst) or open(dst).read() != open(contract).read():
                shutil.copyfile(contract, dst)
                wrote.append("ratings/cfb/README.md")
        league = doc["league"]
    except Exception as e:  # noqa: BLE001
        ctx.log("defence shadow publication failed:", repr(e))
        raise
    res = {"written": wrote, "defense_league": league}
    ctx.log("publish:", json.dumps(res, default=str)[:500])
    return res


# --------------------------------------------------------------------------- 4. results
def stage_results(ctx) -> dict:
    now = ctx.now("results")
    nows = utc(now)
    rows = _gamelog(ctx)
    if ctx.fixed_now is not None:                      # replay: only what had finished by the (virtual) result time
        horizon = pd.Timestamp(ctx.a.results_until) if ctx.a.results_until else now
        horizon = horizon.tz_localize("UTC") if horizon.tzinfo is None else horizon
        rows = [r for r in rows if pd.Timestamp(r["kickoff_utc"]) + pd.Timedelta(hours=5) < horizon]
    sched = _load_sched(ctx)
    fc = pr.load_forecasts(ctx.root, SPORT)
    if fc.empty:
        return {"outcomes": 0, "note": "no archived forecasts yet"}
    wanted = set(fc["game_id"].astype(str))
    outs, st = results.team_outcomes(rows, sched, wanted)
    outs = results.attach_qb_segments(outs, results.qb_segments(rows))
    pstats = {}
    if not ctx.a.no_players:
        try:
            P = data.load_players()
            fd = {g: d for g, d in results.final_game_dates(rows).items() if g in wanted}
            pouts, pstats = results.player_outcomes(fc, P, fd)
            outs += pouts
        except Exception as e:  # noqa: BLE001
            pstats = {"error": repr(e)}
    # idempotent: only outcomes that are new or whose value changed (a changed value becomes a correction at settle)
    old = pd.DataFrame(pr._read(ctx.root, "outcomes", SPORT))
    last = {}
    if not old.empty:
        o2 = old.sort_values("observed_utc").groupby(["game_id", "entity_id", "family"], as_index=False).tail(1)
        last = {(r.game_id, r.entity_id, r.family): (float(r.actual), bool(r.participated)) for r in o2.itertuples()}
    fresh = [o for o in outs if last.get((o["game_id"], o["entity_id"], o["family"])) != (float(o["actual"]), bool(o["participated"]))]
    w = pr.archive_outcomes(fresh, ctx.root, SPORT, now_utc=nows)
    res = {"final_games_with_forecasts": st["games_final"], "outcome_rows_seen": len(outs), "outcome_rows_new_or_changed": len(fresh),
           "archived": w["written"], "rejected_not_final": w["rejected_not_final"], "team_stats": st, "player_stats": pstats}
    ctx.log("results:", json.dumps(res, default=str)[:600])
    return res


# --------------------------------------------------------------------------- 5. settle
def stage_settle(ctx) -> dict:
    now = ctx.now("settle")
    res = pr.settle(ctx.root, SPORT, now_utc=utc(now))
    ctx.log("settle:", res)
    return res


# --------------------------------------------------------------------------- 6. score
def qb_segment_table(root: str, since_utc: str | None = None) -> dict:
    """Challenger team-points error split by the post-hoc 'starting QB changed' definition of the study, plus the
    pregame QB flags carried on the forecast. Paired with the incumbent on identical keys."""
    s = pr.load_settlements(root, SPORT)
    if s.empty:
        return {"n": 0}
    s = s[(s["family"] == "team_points") & ~s["void"].astype(bool)]
    if since_utc:
        s = s[pd.to_datetime(s["game_start_utc"], utc=True) >= pd.Timestamp(since_utc)]
    o = pd.DataFrame(pr._read(root, "outcomes", SPORT))
    f = pr.load_forecasts(root, SPORT)
    if s.empty or o.empty:
        return {"n": 0}
    o = o[o["family"] == "team_points"].sort_values("observed_utc").groupby(["game_id", "entity_id"], as_index=False).tail(1)
    seg_cols = [c for c in ("qb_changed", "leading_passer", "previous_leading_passer") if c in o.columns]
    s = s.merge(o[["game_id", "entity_id"] + seg_cols], on=["game_id", "entity_id"], how="left")
    fcols = [c for c in ("forecast_id", "qb_flag", "week", "fcs_game") if c in f.columns]
    s = s.merge(f[fcols], on="forecast_id", how="left") if "forecast_id" in f.columns else s
    out = {}
    from pf_core.metrics import cluster_bootstrap_diff
    for role in ("challenger", "incumbent"):
        g = s[s["model_role"] == role]
        for name, col in (("qb_changed", "qb_changed"), ("qb_flag", "qb_flag"), ("fcs_game", "fcs_game")):
            if col not in g.columns:
                continue
            for key, gg in g.groupby(col, dropna=True):
                if len(gg) < 1:
                    continue
                e = gg["mean"].astype(float) - gg["actual"].astype(float)
                row = {"n": int(len(gg)), "mae": round(float(e.abs().mean()), 3), "bias": round(float(e.mean()), 3)}
                if len(gg) >= SEG_MIN_N:
                    row["bias_ci95"] = [round(x, 3) for x in cluster_bootstrap_diff(gg["mean"].astype(float), gg["actual"].astype(float),
                                                                                    gg["game_id"], n_boot=500)["ci95"]]
                else:
                    row["note"] = f"n < {SEG_MIN_N}: no interval"
                out.setdefault(role, {}).setdefault(name, {})[str(key)] = row
    out["definition"] = {"qb_changed": "post-hoc: the team's leading passer differs from its previous game (the study's definition; "
                                       "study expectation: team over-projected by 2-3 points when True)",
                         "qb_flag": "pregame flag carried on the forecast (every game is at least QB_NOT_CONFIRMED)",
                         "reference": "development change=True bias +2.12, validation +2.97 (market +1.26/+1.81)"}
    return out


def stage_score(ctx) -> dict:
    now = ctx.now("score")
    nows = utc(now)
    summ = json.load(open(paths.RESULTS / "summary.json"))
    ref = pr.reference_from_summary(summ)
    cap = ctx.summary.get("capture_stats", {})
    out = {}
    md = []
    for tag, days in (("daily", 7), ("weekly", 28), ("season", None)):
        card = pr.scorecard(SPORT, ctx.root, ref, now_utc=nows, window_days=days, capture_stats=cap)
        since = card["window"].get("since_utc")
        card["segments"] = qb_segment_table(ctx.root, since)
        card["authority"] = "research_only"
        card["notes"] = ["game_total and margin are SHADOW VALIDATION families; nothing here is an edge claim.",
                         "incumbent = script-engine scoring baseline (mean only): paired comparison uses MAE."]
        p = os.path.join(ctx.root, "scorecards", "cfb", f"{tag}.json")
        prev = json.load(open(p)) if os.path.exists(p) else None
        strip = lambda c: {k: v for k, v in (c or {}).items() if k not in ("generated_utc", "window")}  # noqa: E731
        if prev is not None and strip(prev) == strip(json.loads(json.dumps(card, default=_default))):
            out[tag] = "unchanged"
            text = pr.render_markdown(card, title=f"CFB projection scorecard ({tag})")
        else:
            pr.write_scorecard(card, ctx.root, tag=tag)
            d = os.path.join(ctx.root, "scorecards", "cfb")
            for fn in os.listdir(d):                      # drop the per-run dated copies: the tag file is the rewritable view
                if fn.startswith(f"{tag}_") and fn.endswith(".json"):
                    os.remove(os.path.join(d, fn))
            out[tag] = {"settled": card["settlement"]["settled"], "void": card["settlement"]["void"],
                        "families": [f["family"] for f in card["families"]], "alerts": {a["family"]: a["status"] for a in card["alerts"]}}
            text = pr.render_markdown(card, title=f"CFB projection scorecard ({tag})")
        seg = card.get("segments") or {}
        if seg.get("challenger"):
            text += "\n**Segments (challenger team points)**\n\n```json\n" + json.dumps(seg["challenger"], indent=1) + "\n```\n"
        md.append(text)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    body = "\n".join(md)
    if step:
        with open(step, "a") as f:
            f.write(body + "\n")
    with open(os.path.join(ctx.work, "scorecards.md"), "w") as f:
        f.write(body)
    ctx.log("score:", json.dumps(out, default=str)[:500])
    return out


# --------------------------------------------------------------------------- 7. guard
def stage_guard(ctx) -> dict:
    bad = archive_guard.violations(ctx.root)
    if bad:
        ctx.log("archive_guard FAILED:", bad[:5])
        raise SystemExit(1)
    ctx.log("guard: OK (additions only)")
    return {"violations": 0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all")
    ap.add_argument("--archive", required=True)
    ap.add_argument("--now", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--work", default=str(paths.CACHE / "live"))
    ap.add_argument("--football-dir", default=str(paths.FOOTBALL_2026))
    ap.add_argument("--frozen-dir", default=str(paths.REPO_ROOT / "data" / "scripting" / "live" / "frozen"))
    ap.add_argument("--horizon-days", type=float, default=None, help="default: end of the current CFB week (next Tuesday 08:00 UTC)")
    ap.add_argument("--no-players", action="store_true")
    ap.add_argument("--results-until", default=None, help="replay only: treat games finished before this time as final")
    ap.add_argument("--summary-out", default=None)
    a = ap.parse_args(argv)
    ctx = Ctx(a)
    todo = STAGES if a.stage == "all" else a.stage.split(",")
    fns = {"capture": stage_capture, "project": stage_project, "publish": stage_publish, "results": stage_results,
           "settle": stage_settle, "score": stage_score, "guard": stage_guard}
    rc = 0
    for st in STAGES:
        if st not in todo:
            continue
        ctx.log(f"=== stage {st}")
        try:
            ctx.summary["stages"][st] = fns[st](ctx)
        except SystemExit as e:
            ctx.summary["stages"][st] = {"FAILED": str(e.code)}
            rc = int(e.code or 1)
            break
        except Exception as e:  # noqa: BLE001
            ctx.summary["stages"][st] = {"FAILED": repr(e)}
            ctx.log(f"stage {st} FAILED: {e!r}")
            rc = 1
            break
    out = a.summary_out or os.path.join(ctx.work, "run_summary.json")
    json.dump(ctx.summary, open(out, "w"), indent=1, default=_default)
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a") as f:
            f.write("### CFB live run summary\n\n```json\n" + json.dumps(ctx.summary, indent=1, default=_default)[:6000] + "\n```\n")
    if ctx.tmp and not a.dry_run:
        shutil.rmtree(ctx.tmp, ignore_errors=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
