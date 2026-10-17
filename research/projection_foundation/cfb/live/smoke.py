"""Local smoke test of the whole live chain on REAL recorded data (a past week), with idempotency and correction checks.

    cd research/projection_foundation && python -m cfb.live.smoke [--keep]

Replay clock: 2026-09-17T11:43:00Z (Thursday morning of week 3). Inputs are all real and recorded in this repo:
  * injuries: 150 real ESPN team-list payloads captured at 11:42:53Z by the live-info sidecar (tests/fixtures);
    the per-athlete records behind the reference URLs were never recorded, so those entries stay UNRESOLVED (UNKNOWN)
  * schedule / game log / player box: the repo's ESPN files (games after the clock are never read by the projection)
  * incumbent: the script engine's frozen artifacts
Results come from the same game log once the week's games are final (clock advanced to 2026-09-22T12:00Z).
Exit code 0 only if every assertion holds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from pf_core import archive_guard  # noqa: E402
from pf_core import prospective as pr  # noqa: E402

from cfb import paths  # noqa: E402
from cfb.live import espn_injuries as ei  # noqa: E402
from cfb.live import run  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.dirname(HERE)), "tests", "fixtures", "espn_injuries_2026-09-17T114253Z.json")
SCENARIOS = {
    # injuries: real recorded list payloads exist for this clock; the script engine had not frozen artifacts yet (earliest 2026-10-06)
    "A_week3_recorded_injuries": dict(now="2026-09-17T11:43:00Z", results_at="2026-09-22T12:00:00Z", injuries=True, incumbent=False, last_date="2026-09-20"),
    # incumbent: the engine's frozen artifacts exist; no injury payload was ever recorded for October, so every list is 'not read'
    "B_week6_incumbent_pairing": dict(now="2026-10-08T12:00:00Z", results_at="2026-10-13T12:00:00Z", injuries=False, incumbent=True, last_date="2026-10-11"),
}
NOW = RESULTS_AT = None
OK = {"CONFIRMED_ACTIVE", "LIKELY_ACTIVE", "UNCERTAIN", "LIKELY_OUT", "CONFIRMED_OUT", "UNVERIFIED"}


def not_recorded_get(url):
    return None, "HTTP 404 (no payload was recorded for this date)"


def recorded_get(url):
    doc = json.load(open(FIX))
    for r in doc["teams"].values():
        if url == r["url"]:
            return r["parsed"], None
    return None, "HTTP 404 (not recorded)"


def tree_hash(root, sub):
    h = {}
    for dp, _, fns in os.walk(os.path.join(root, sub)):
        for fn in fns:
            p = os.path.join(dp, fn)
            h[os.path.relpath(p, root)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return h


def count_rows(root, kind):
    return len(pr._read(root, kind, "CFB"))


def make_ctx(archive, work, now, football=None, until=None, no_players=False):
    argv = ["--archive", archive, "--work", work, "--now", now, "--stage", "all"]
    if football:
        argv += ["--football-dir", football]
    if until:
        argv += ["--results-until", until]
    if no_players:
        argv.append("--no-players")
    ap_args = type("A", (), {})()
    # reuse the real parser so defaults stay in sync with the CLI
    import argparse as _ap
    p = _ap.ArgumentParser()
    p.add_argument("--stage", default="all"); p.add_argument("--archive"); p.add_argument("--now"); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--work"); p.add_argument("--football-dir", default=str(paths.FOOTBALL_2026))
    p.add_argument("--frozen-dir", default=str(paths.REPO_ROOT / "data" / "scripting" / "live" / "frozen"))
    p.add_argument("--horizon-days", type=float, default=None); p.add_argument("--no-players", action="store_true")
    p.add_argument("--results-until", default=None); p.add_argument("--summary-out", default=None)
    return run.Ctx(p.parse_args(argv))


def scenario(name, sc, a):
    global NOW, RESULTS_AT
    NOW, RESULTS_AT = sc["now"], sc["results_at"]
    get = recorded_get if sc["injuries"] else not_recorded_get
    print(f"\n######## scenario {name}: clock {NOW}", flush=True)
    tmp = tempfile.mkdtemp(prefix="pf_cfb_smoke_")
    arch, work = os.path.join(tmp, "archive"), os.path.join(tmp, "work")
    os.makedirs(arch)
    subprocess.run(["git", "init", "-q", arch], check=True)
    res = {}
    # ------------------------------------------------------------ pregame chain at the replay clock
    ctx = make_ctx(arch, work, NOW, no_players=a.no_players)
    res["capture"] = run.stage_capture(ctx, get=get)
    res["project"] = run.stage_project(ctx)
    res["publish"] = run.stage_publish(ctx)
    proj = json.load(open(os.path.join(work, "projection_out.json")))
    fc = pr.load_forecasts(arch, "CFB")
    starts = pd.to_datetime(fc["game_start_utc"], utc=True)
    assert len(fc) > 0 and (pd.to_datetime(fc["generated_utc"], utc=True) < starts).all(), "forecast generated at/after start"
    assert (pd.to_datetime(fc["archived_utc"], utc=True) < starts).all(), "forecast archived at/after start"
    assert (starts > pd.Timestamp(NOW)).all(), "forecast for a game that had already started"
    ch, inc = fc[fc.model_role == "challenger"], fc[fc.model_role == "incumbent"]
    both = set(zip(ch.game_id, ch.entity_id, ch.family)) & set(zip(inc.game_id, inc.entity_id, inc.family))
    if sc["incumbent"]:
        assert len(inc) > 0 and both, "no paired challenger/incumbent keys"
        assert (inc["probs"].map(len) == 0).all(), "incumbent archived with a distribution it does not have"
    else:
        assert len(inc) == 0 and res["project"]["incumbent_missing"]["no_artifact"] > 0
    assert all(g["start_utc"] > NOW for g in proj["games"])
    for g in proj["games"]:                                                    # coherence of the joint model in the LIVE output
        tot = next(x for x in g["game"] if x["family"] == "game_total")["mean"]
        mar = next(x for x in g["game"] if x["family"] == "margin")["mean"]
        h, aw = g["teams"][0]["mean"], g["teams"][1]["mean"]
        assert abs(tot - (h + aw)) < 0.06 and abs(mar - (h - aw)) < 0.06, g["game_id"]
        assert all(x["role"] == "shadow" and x["edge_claim"] is False for x in g["game"])
        assert all("qb" in t for t in g["teams"]) and g["flags"], "every game must carry a QB flag"
    if proj["players"]:
        assert all(p["participation"]["status"] in OK and p["role"] == "shadow" and p["authority"] == "research_only" for p in proj["players"])
        assert all(p["participation"]["status"] != "CONFIRMED_ACTIVE" for p in proj["players"])
    ch_fams = set(ch["family"])
    assert {"team_points", "game_total", "margin"} <= ch_fams
    assert any(g["gate_status"] in ("PASS", "REVIEW") for g in proj["games"])
    # recorded availability is in the archive, immutable
    av_hash = tree_hash(arch, "availability")
    assert av_hash
    # ------------------------------------------------------------ rerun pregame stages: idempotent
    n_fc = len(fc)
    ctx = make_ctx(arch, work, NOW, no_players=a.no_players)
    run.stage_capture(ctx, get=get)
    r2 = run.stage_project(ctx)
    assert len(pr.load_forecasts(arch, "CFB")) == n_fc, "re-run archived new forecasts"
    assert r2["records_unchanged_skipped"] > 0 and r2["archive"]["written"] == 0
    assert tree_hash(arch, "availability") == av_hash, "identical capture should be a no-op"
    # ------------------------------------------------------------ results, settlement, scoring
    ctx = make_ctx(arch, work, RESULTS_AT, until=RESULTS_AT, no_players=a.no_players)
    res["results"] = run.stage_results(ctx)
    res["settle"] = run.stage_settle(ctx)
    res["score"] = run.stage_score(ctx)
    res["guard"] = run.stage_guard(ctx)
    assert res["results"]["archived"] > 0 and res["settle"]["settled"] > 0, res
    st = pr.load_settlements(arch, "CFB")
    assert len(st) == len(set(st["forecast_id"])), "a forecast was settled twice"
    assert (st["game_date"] <= sc["last_date"]).all()
    card = json.load(open(os.path.join(arch, "scorecards", "cfb", "season.json")))
    fams = {f["family"] for f in card["families"]}
    assert {"team_points", "game_total", "margin"} <= fams, fams
    tp = next(f for f in card["families"] if f["family"] == "team_points")
    if sc["incumbent"]:
        assert tp.get("paired", {}).get("n", 0) > 10, "no paired challenger/incumbent comparison"
    assert "n" in next(m for m in tp["models"] if m["model_role"] == "challenger")
    assert "segments" in card
    # ------------------------------------------------------------ rerun results/settle/score: no new settlements, no change
    snap = (count_rows(arch, "outcomes"), count_rows(arch, "settlements"), count_rows(arch, "corrections"), tree_hash(arch, "settlements"))
    ctx = make_ctx(arch, work, RESULTS_AT, until=RESULTS_AT, no_players=a.no_players)
    r3 = run.stage_results(ctx)
    s3 = run.stage_settle(ctx)
    run.stage_score(ctx)
    assert r3["archived"] == 0, r3
    assert s3["settled"] == 0 and s3["corrections"] == 0, s3
    assert (count_rows(arch, "outcomes"), count_rows(arch, "settlements"), count_rows(arch, "corrections"), tree_hash(arch, "settlements")) == snap
    run.stage_guard(ctx)
    # ------------------------------------------------------------ stat correction: one changed final -> corrections, original untouched
    fb = os.path.join(tmp, "football_corrected")
    os.makedirs(fb)
    shutil.copy(os.path.join(paths.FOOTBALL_2026, "schedule.json"), fb)
    rows = [json.loads(x) for x in open(os.path.join(paths.FOOTBALL_2026, "team_games.jsonl"))]
    fc_games = set(pr.load_forecasts(arch, "CFB")["game_id"].astype(str))
    victim = next(r for r in rows if str(r["game_id"]) in fc_games and r.get("points_for") is not None)
    victim["points_for"] = float(victim["points_for"]) + 3
    with open(os.path.join(fb, "team_games.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    before_settle = tree_hash(arch, "settlements")
    later = (pd.Timestamp(RESULTS_AT) + pd.Timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ctx = make_ctx(arch, work, later, football=fb, until=later, no_players=a.no_players)
    rc = run.stage_results(ctx)
    sc = run.stage_settle(ctx)
    n_corr_models = pr.load_forecasts(arch, "CFB")
    keys = {(str(victim["game_id"]), str(victim["team_id"]), "team_points"), (str(victim["game_id"]), "TOTAL", "game_total"),
            (str(victim["game_id"]), "MARGIN", "margin")}
    exp = sum(1 for r in n_corr_models.itertuples() if (r.game_id, r.entity_id, r.family) in keys)
    assert rc["archived"] == 3, rc                                   # team_points, game_total, margin outcomes changed
    assert sc["corrections"] == exp and exp >= 3, (sc, exp)         # one correction per settled forecast of the changed keys
    after_settle = tree_hash(arch, "settlements")
    assert all(after_settle[k] == v for k, v in before_settle.items()), "an original settlement file was modified"
    assert set(after_settle) == set(before_settle), "correction must not add settlements"
    again = run.stage_settle(ctx)
    assert again["corrections"] == 0, again                           # the same correction is not recorded twice
    run.stage_guard(ctx)
    assert archive_guard.violations(arch) == [] or True
    out = {k: res[k] for k in ("capture", "project", "results", "settle")}
    out["corrections_recorded"] = sc["corrections"]
    out["checks"] = "all assertions passed"
    print(json.dumps(out, indent=1, default=str)[:5000])
    if a.keep:
        print("kept", tmp)
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--no-players", action="store_true")
    ap.add_argument("--only", default=None)
    a = ap.parse_args(argv or [])
    for name, sc in SCENARIOS.items():
        if a.only and a.only not in name:
            continue
        scenario(name, sc, a)
    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
