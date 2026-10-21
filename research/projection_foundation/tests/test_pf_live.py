"""pf_core 1.2.0 live-operation behaviour: availability as-of, forecast archive, settlement, scoring, drift."""
import json
import math
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


# ---------------------------------------------------------------- 1.2.1
def test_not_starting_only_keeps_the_observations_certainty():
    lat = av.asof([obs(kind="lineup", status="NOT_STARTING", certainty="PROJECTED", source="rotowire").__dict__], START)
    p = av.resolve_participation(lat, "g1", "p1", START)
    assert p.status == av.UNCERTAIN and p.certainty == "PROJECTED"     # a projection is not a confirmation
    lat = av.asof([obs(kind="lineup", status="NOT_STARTING", certainty="CONFIRMED", source="mlb_statsapi_lineup").__dict__], START)
    assert av.resolve_participation(lat, "g1", "p1", START).certainty == "CONFIRMED"


def test_unchanged_outcomes_are_not_rewritten(tmp_path):
    root = str(tmp_path)
    out = [{"game_id": "g1", "entity_id": "p1", "family": "rebounds", "actual": 7, "participated": True, "final": True,
            "source": "box", "game_date": "2026-10-20"}]
    assert pr.archive_outcomes(out, root, "NBA", now_utc="2026-10-21T04:00:00Z")["written"] == 1
    again = pr.archive_outcomes([{**out[0], "actual": np.int64(7), "participated": np.bool_(True)}], root, "NBA",
                                now_utc="2026-10-21T08:00:00Z")
    assert again["written"] == 0 and again["unchanged"] == 1
    assert pr.archive_outcomes([{**out[0], "actual": 8}], root, "NBA", now_utc="2026-10-22T04:00:00Z")["written"] == 1
    assert len(pr._read(root, "outcomes", "NBA")) == 2


def test_scorecard_evaluations_are_write_once_and_versioned(tmp_path):
    """The current view is rewritten; every distinct evaluation is kept write-once under a versioned name, and an existing
    evaluation file is never overwritten (a re-run with the same evidence writes nothing new)."""
    root = str(tmp_path)
    d = os.path.join(root, "scorecards", "nba")
    c1 = pr.scorecard("NBA", root, {}, now_utc="2026-10-21T06:00:00Z")
    pr.write_scorecard(c1, root, "daily")
    first = {f: open(os.path.join(d, f), "rb").read() for f in os.listdir(d) if f.startswith("daily_")}
    assert len(first) == 1 and next(iter(first)).startswith("daily_20261021_v" + pr.SCORING_VERSION + "_")
    pr.write_scorecard(pr.scorecard("NBA", root, {}, now_utc="2026-10-21T18:00:00Z"), root, "daily")   # same evidence
    assert sorted(f for f in os.listdir(d) if f.startswith("daily_")) == sorted(first)
    changed = {**c1, "generated_utc": "2026-10-21T20:00:00Z", "settlement": {"settled": 5, "void": 0, "corrections": 0}}
    pr.write_scorecard(changed, root, "daily")
    now = sorted(f for f in os.listdir(d) if f.startswith("daily_"))
    assert len(now) == 2 and all(open(os.path.join(d, f), "rb").read() == b for f, b in first.items())
    assert json.load(open(os.path.join(d, "daily.json")))["generated_utc"] == "2026-10-21T20:00:00Z"


def _settled(rows):
    base = dict(sport="NFL", model_id="m", lo=0, probs=[], void=False, game_start_utc="2026-10-18T17:00:00Z")
    return pd.DataFrame([{**base, **r} for r in rows])


def _binary_rows(n_games=60, per=6, p_ch=0.23, p_inc=0.15, rate=0.23, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        for e in range(per):
            y = float(rng.random() < rate)
            rows.append(dict(family="anytime_td", model_role="challenger", model_id="ch", game_id=f"g{g}", entity_id=f"p{e}",
                             actual=y, mean=p_ch, probs=[1 - p_ch, p_ch]))
            rows.append(dict(family="anytime_td", model_role="incumbent", model_id="inc", game_id=f"g{g}", entity_id=f"p{e}",
                             actual=y, mean=p_inc, probs=[]))
    return rows


def test_binary_family_is_scored_by_brier_not_mae():
    """A calibrated challenger vs an incumbent that under-forecasts 'yes': MAE prefers the low forecaster (improper);
    the Brier score does not. No coverage figures, no MAE-based comparison, verdict only when the CI excludes 0."""
    sc = pr.score(_settled(_binary_rows()))[0]
    assert sc["outcome_type"] == "binary"
    ch = next(m for m in sc["models"] if m["model_role"] == "challenger")
    inc = next(m for m in sc["models"] if m["model_role"] == "incumbent")
    assert ch["primary_metric"] == "brier" and "cover_80" not in ch and "cover_80" not in inc
    assert inc["mae"] < ch["mae"]                       # the improper score favours the under-forecaster ...
    assert ch["brier"] < inc["brier"]                   # ... the proper one does not
    assert ch["crps"] == ch["brier"] and inc["crps"] == inc["brier"]
    assert abs(ch["calibration"]["in_the_large"]) < 0.05 and inc["calibration"]["in_the_large"] < -0.05
    assert sum(b["n"] for b in ch["calibration"]["reliability"]) == ch["n"]
    p = sc["paired"]
    assert p["primary_metric"] == "brier" and "mae_diff" not in p and p["crps_diff"] == p["brier_diff"]
    assert p["n"] == 360 and p["n_games"] == 60 and p["log_loss_diff"]["mean_diff"] < 0
    alerts = pr.drift_alerts([sc], {"anytime_td": {"crps": 0.17}})
    assert "worse than incumbent" not in alerts[0]["what"]


def test_binary_verdict_is_not_forced_when_the_interval_includes_zero():
    few = pr.score(_settled(_binary_rows(n_games=3, per=20, p_ch=0.23, p_inc=0.60)))[0]["paired"]
    assert few["brier_diff"]["ci95"][1] < 0 and pr.paired_verdict(few) == "too few games"
    tie = pr.score(_settled(_binary_rows(n_games=24, per=4, p_ch=0.20, p_inc=0.19)))[0]["paired"]
    assert tie["brier_diff"]["ci95"][0] < 0 < tie["brier_diff"]["ci95"][1]
    assert pr.paired_verdict(tie) == "not distinguishable"
    worse = pr.score(_settled(_binary_rows(n_games=80, per=6, p_ch=0.60, p_inc=0.23)))[0]
    assert pr.paired_verdict(worse["paired"]) == "challenger worse"
    a = pr.drift_alerts([worse], {})[0]
    assert a["status"] == "ALERT" and "(BRIER)" in a["what"]


def test_count_families_keep_their_metrics():
    rng = np.random.default_rng(3)
    rows = []
    for g in range(30):
        for e in range(5):
            y = float(rng.poisson(5))
            pmf = list(np.exp(-5) * 5 ** np.arange(25) / np.array([math.factorial(k) for k in range(25)]))
            rows.append(dict(family="receptions", model_role="challenger", game_id=f"g{g}", entity_id=f"p{e}", actual=y,
                             mean=5.0, probs=pmf))
            rows.append(dict(family="receptions", model_role="incumbent", game_id=f"g{g}", entity_id=f"p{e}", actual=y,
                             mean=6.0, probs=[]))
    sc = pr.score(_settled(rows))[0]
    ch = next(m for m in sc["models"] if m["model_role"] == "challenger")
    assert sc["outcome_type"] == "continuous_or_count" and "cover_80" in ch and "brier" not in ch
    assert ch["primary_metric"] == "crps"
    assert sc["paired"]["primary_metric"] == "mae" and "mae_diff" in sc["paired"] and "brier_diff" not in sc["paired"]


def test_paired_uses_identical_non_void_keys_and_drops_ambiguous_ones():
    rows = _binary_rows(n_games=4, per=3)
    rows.append(dict(rows[1], mean=0.5))                                   # a second incumbent row for (g0, p0): ambiguous
    rows.append(dict(family="anytime_td", model_role="challenger", model_id="ch", game_id="g9", entity_id="p9",
                     actual=None, mean=0.3, probs=[0.7, 0.3], void=True))  # DNP: void, never paired
    rows.append(dict(family="anytime_td", model_role="challenger", model_id="ch", game_id="g8", entity_id="p8",
                     actual=1.0, mean=0.3, probs=[0.7, 0.3]))              # no incumbent twin
    p = pr.score(_settled(rows))[0]["paired"]
    assert p["n"] == 4 * 3 - 1 and p["ambiguous_keys"] == 1

def test_archive_guard_checks_committed_changes_since_base(tmp_path):
    d = str(tmp_path)
    git = lambda *a: subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True,
                                    capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "init", "-q", d], check=True)
    os.makedirs(os.path.join(d, "forecasts"))
    open(os.path.join(d, "forecasts", "a.jsonl.gz"), "w").write("x")
    git("add", "."); git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    open(os.path.join(d, "forecasts", "b.jsonl.gz"), "w").write("y")
    git("add", "."); git("commit", "-qm", "addition")
    assert archive_guard.violations_since(d, base) == []
    open(os.path.join(d, "forecasts", "a.jsonl.gz"), "w").write("edited")
    git("add", "."); git("commit", "-qm", "edit")
    assert archive_guard.violations(d) == []                  # nothing uncommitted ...
    assert archive_guard.violations_since(d, base)            # ... but the committed edit is caught
    assert archive_guard.main([d, base]) == 1 and archive_guard.main([d]) == 0


def test_archive_guard_protects_dated_scorecard_evaluations(tmp_path):
    d = str(tmp_path)
    git = lambda *a: subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True,  # noqa: E731
                                    capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "init", "-q", d], check=True)
    sc = os.path.join(d, "scorecards", "nfl")
    os.makedirs(sc)
    for fn in ("season.json", "season.md", "season_20261021.json", "season_20261021_v1.3.0_0123456789ab.json"):
        open(os.path.join(sc, fn), "w").write("x")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    open(os.path.join(sc, "season.json"), "w").write("current view")                    # rewritable view
    open(os.path.join(sc, "season.md"), "w").write("current view")
    open(os.path.join(sc, "season_20261022_v1.3.0_ba9876543210.json"), "w").write("new")  # a new evaluation
    assert archive_guard.violations(d) == []
    open(os.path.join(sc, "season_20261021_v1.3.0_0123456789ab.json"), "w").write("edited")
    os.remove(os.path.join(sc, "season_20261021.json"))
    bad = archive_guard.violations(d)
    assert len(bad) == 2 and all("season_20261021" in b for b in bad)
    git("add", "-A")
    git("commit", "-qm", "edit")
    assert len(archive_guard.violations_since(d, base)) == 2
    assert archive_guard.is_immutable("scorecards/nhl/daily_20261021.json")
    assert not archive_guard.is_immutable("scorecards/nhl/daily.json") and not archive_guard.is_immutable("projections/nhl/latest.json")
