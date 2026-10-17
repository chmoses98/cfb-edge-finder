"""pf_core 1.2.0 live-operation behaviour: availability as-of, forecast archive, settlement, scoring, drift."""
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pf_core import archive_guard, availability as av, dists, gate, prospective as pr  # noqa: E402

START = "2026-10-20T23:30:00Z"


def obs(**kw):
    base = dict(sport="NBA", event_id="g1", event_start_utc=START, entity_id="p1", entity_name="X", team="BOS",
                kind="injury", status="QUESTIONABLE", certainty="REPORTED", source="nba_official_injury_report",
                captured_utc="2026-10-20T18:00:00Z")
    base.update(kw)
    return av.Observation(**base)


def test_availability_validation_and_write_once(tmp_path):
    bad = obs(kind="vibes")
    assert bad.validate()
    r1 = av.write_snapshot([obs(), bad], str(tmp_path), "NBA", "2026-10-20T18:00:00Z")
    assert r1["written"] == 1 and r1["rejected"] == 1
    r2 = av.write_snapshot([obs()], str(tmp_path), "NBA", "2026-10-20T18:05:00Z")
    assert r2["duplicate"] and r2["written"] == 0
    assert len(av.load_snapshots(str(tmp_path), "NBA")) == 1


def test_asof_ignores_later_and_postgame_observations():
    rows = [obs(status="QUESTIONABLE", captured_utc="2026-10-20T18:00:00Z").__dict__,
            obs(status="OUT", certainty="CONFIRMED", captured_utc="2026-10-20T23:00:00Z").__dict__,
            obs(status="ACTIVE", kind="lineup", certainty="CONFIRMED", captured_utc="2026-10-21T01:00:00Z").__dict__]
    lat = av.asof(rows, "2026-10-20T22:00:00Z")
    p = av.resolve_participation(lat, "g1", "p1", "2026-10-20T22:00:00Z")
    assert p.status == av.UNCERTAIN
    lat2 = av.asof(rows, "2026-10-21T05:00:00Z")   # cut-off after start: post-start capture still excluded
    p2 = av.resolve_participation(lat2, "g1", "p1", "2026-10-21T05:00:00Z")
    assert p2.status == av.CONFIRMED_OUT


def test_unverified_is_never_active_and_lineup_confirmation():
    assert av.resolve_participation({}, "g1", "p1", START).status == av.UNVERIFIED
    lat = av.asof([obs(kind="lineup", status="STARTING", certainty="CONFIRMED", source="mlb_statsapi_lineup").__dict__], START)
    assert av.resolve_participation(lat, "g1", "p1", START).status == av.CONFIRMED_ACTIVE
    lat = av.asof([obs(kind="lineup", status="STARTING", certainty="PROJECTED", source="dailyfaceoff").__dict__], START)
    assert av.resolve_participation(lat, "g1", "p1", START).status == av.LIKELY_ACTIVE
    stale = av.asof([obs(kind="lineup", status="STARTING", certainty="PROJECTED", captured_utc="2026-10-17T00:00:00Z").__dict__], START)
    assert av.resolve_participation(stale, "g1", "p1", START).status == av.UNCERTAIN


def test_gate_participation():
    gi = gate.GateInput("NBA", "points", "1", "X", "BOS", "g", START, 20.0, data_asof_utc="2026-10-20T20:00:00Z",
                        injury_status="ACTIVE", participation="UNVERIFIED")
    assert gate.evaluate(gi, now_utc="2026-10-20T21:00:00Z")["status"] == gate.REVIEW
    gi.participation = "CONFIRMED_OUT"
    assert gate.evaluate(gi, now_utc="2026-10-20T21:00:00Z")["status"] == gate.BLOCK


def fc(model_role="challenger", mean=4.0, gen="2026-10-20T20:00:00Z", entity="p1", game="g1", model_id=None, mu=None):
    d = dists.poisson(np.array([mu or mean]), K=30)
    return {"sport": "NBA", "family": "rebounds", "model_id": model_id or f"pf-{model_role}", "model_role": model_role,
            "model_version": "1", "game_id": game, "game_start_utc": START, "entity_id": entity, "entity_name": "X",
            "team": "BOS", "generated_utc": gen, "data_asof_utc": gen, "mean": float(d.mean[0]), "lo": 0,
            "probs": d.P[0].tolist(), "gate_status": "PASS", "participation": "CONFIRMED_ACTIVE", "readiness": "READY"}


def test_archive_rejects_late_and_duplicates(tmp_path):
    root = str(tmp_path)
    s = pr.archive_forecasts([fc(), fc(gen="2026-10-21T00:00:00Z")], root, now_utc="2026-10-20T21:00:00Z")
    assert s["written"] == 1 and s["late_rejected"] == 1
    s2 = pr.archive_forecasts([fc()], root, now_utc="2026-10-20T21:30:00Z")
    assert s2["duplicates"] == 1 and s2["written"] == 0
    s3 = pr.archive_forecasts([fc(gen="2026-10-20T22:00:00Z")], root, now_utc="2026-10-21T00:00:00Z")
    assert s3["late_rejected"] == 1   # archived after the start: rejected even if generated before


def test_latest_pregame_and_settle_once_with_corrections(tmp_path):
    root = str(tmp_path)
    pr.archive_forecasts([fc(mean=4.0), fc(mean=6.0, mu=6.0, gen="2026-10-20T22:00:00Z")], root, now_utc="2026-10-20T22:10:00Z")
    lp = pr.latest_pregame(pr.load_forecasts(root, "NBA"))
    assert len(lp) == 1 and abs(lp["mean"].iloc[0] - 6.0) < 1e-6
    out = [{"game_id": "g1", "entity_id": "p1", "family": "rebounds", "actual": 7, "participated": True, "final": True,
            "source": "box", "game_date": "2026-10-20"}]
    pr.archive_outcomes(out, root, "NBA", now_utc="2026-10-21T04:00:00Z")
    s1 = pr.settle(root, "NBA", now_utc="2026-10-21T05:00:00Z")
    assert s1["settled"] == 1
    s2 = pr.settle(root, "NBA", now_utc="2026-10-21T06:00:00Z")
    assert s2["settled"] == 0 and s2["already_settled"] == 1 and s2["corrections"] == 0
    pr.archive_outcomes([{**out[0], "actual": 8}], root, "NBA", now_utc="2026-10-22T04:00:00Z")
    s3 = pr.settle(root, "NBA", now_utc="2026-10-22T05:00:00Z")
    assert s3["corrections"] == 1
    st = pr.load_settlements(root, "NBA")
    assert len(st) == 1 and st["actual"].iloc[0] == 7.0   # original never overwritten
    assert pr.settle(root, "NBA", now_utc="2026-10-22T06:00:00Z")["corrections"] == 0


def test_void_dnp_and_paired_scoring(tmp_path):
    root = str(tmp_path)
    rng = np.random.default_rng(0)
    recs, outs = [], []
    for g in range(40):
        for p in range(5):
            mu = rng.uniform(2, 9)
            ent, game = f"p{p}", f"g{g}"
            recs.append(fc("challenger", mu=mu, entity=ent, game=game))
            recs.append(fc("incumbent", mu=mu * 0.7, entity=ent, game=game))
            outs.append({"game_id": game, "entity_id": ent, "family": "rebounds", "actual": int(rng.poisson(mu)),
                         "participated": not (g == 0 and p == 0), "final": True, "source": "box", "game_date": "2026-10-20"})
    recs.append(fc("challenger", mu=3, entity="lonely", game="g0"))   # no incumbent twin -> not in paired set
    outs.append({"game_id": "g0", "entity_id": "lonely", "family": "rebounds", "actual": 3, "participated": True,
                 "final": True, "source": "box", "game_date": "2026-10-20"})
    pr.archive_forecasts(recs, root, now_utc="2026-10-20T21:00:00Z")
    pr.archive_outcomes(outs, root, "NBA", now_utc="2026-10-21T04:00:00Z")
    st = pr.settle(root, "NBA", now_utc="2026-10-21T05:00:00Z")
    assert st["void"] == 2   # DNP voids both models' forecasts
    sc = pr.score(pr.load_settlements(root, "NBA"))
    fam = sc[0]
    assert fam["paired"]["n"] == 199          # identical keys only, DNP excluded, unpaired excluded
    assert fam["paired"]["crps_diff"]["ci95"][1] < 0   # the unbiased challenger beats the biased incumbent
    card = pr.scorecard("NBA", root, {"rebounds": {"crps": 1.0, "mae": 1.5}}, now_utc="2026-10-21T06:00:00Z")
    a = {x["family"]: x for x in card["alerts"]}
    assert a["rebounds"]["status"] in ("OK", "WATCH", "ALERT")
    md = pr.render_markdown(card)
    assert "rebounds" in md and "betting signal" in md


def test_drift_alerts_on_bias_and_coverage():
    scores = [{"family": "pts", "models": [{"model_role": "challenger", "n": 500, "mean_actual": 10.0, "bias": 1.2,
                                              "bias_ci95": [0.9, 1.5], "cover_80": 0.6, "mae": 4, "crps": 3.0}]}]
    a = pr.drift_alerts(scores, {"pts": {"crps": 2.0}})[0]
    assert a["status"] == "ALERT" and "bias" in a["what"] and "80% range" in a["what"] and "CRPS" in a["what"]
    small = [{"family": "pts", "models": [{"model_role": "challenger", "n": 20, "mean_actual": 10, "bias": 5, "mae": 5}]}]
    assert pr.drift_alerts(small, {})[0]["status"] == "INSUFFICIENT"


def test_archive_guard(tmp_path):
    d = str(tmp_path)
    subprocess.run(["git", "init", "-q", d], check=True)
    os.makedirs(os.path.join(d, "forecasts"))
    open(os.path.join(d, "forecasts", "a.jsonl.gz"), "w").write("x")
    subprocess.run(["git", "-C", d, "add", "."], check=True)
    subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], check=True)
    assert archive_guard.violations(d) == []
    open(os.path.join(d, "forecasts", "b.jsonl.gz"), "w").write("y")
    assert archive_guard.violations(d) == []
    open(os.path.join(d, "forecasts", "a.jsonl.gz"), "w").write("changed")
    assert archive_guard.violations(d)
