"""Publish-stage hook for the flag-gated OAE challenger block: flag off = byte-identical artifact; flag on = additive."""
import argparse
import hashlib
import json
import os
import shutil
import sys

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
FLAG = "CFB_OAE_FORECASTS"
DOC = os.path.join(HERE, "..", "results", "projections_latest.json")


def _ctx(tmp_path, now):
    from cfb.live import run
    a = argparse.Namespace(stage="publish", archive=str(tmp_path / "pd"), now=now, dry_run=False, work=str(tmp_path / "w"),
                           football_dir=os.path.join(REPO, "data", "football", "2026"),
                           frozen_dir=os.path.join(REPO, "data", "scripting", "live", "frozen"), horizon_days=None,
                           no_players=True, results_until=None, summary_out=None)
    ctx = run.Ctx(a)
    shutil.copyfile(DOC, os.path.join(ctx.work, "projection_out.json"))
    return ctx


def _publish(tmp_path, monkeypatch, flag):
    from cfb import defense_shadow
    from cfb.live import run
    monkeypatch.setattr(defense_shadow, "build", lambda *a, **k: {"league": {}})
    if flag is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, flag)
    doc = json.load(open(DOC))
    now = (pd.Timestamp(min(g["start_utc"] for g in doc["games"])) - pd.Timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ctx = _ctx(tmp_path, now)
    res = run.stage_publish(ctx)
    return open(os.path.join(ctx.root, "projections", "cfb", "latest.json"), "rb").read(), res


def _main_bytes():
    """Exactly what the publish stage on main writes: the projection document serialised unchanged."""
    from cfb.live import run
    return json.dumps(json.load(open(DOC)), separators=(",", ":"), default=run._default).encode()


@pytest.mark.parametrize("flag", [None, "", "0", "true"])
def test_flag_off_artifact_is_byte_identical_to_main(tmp_path, monkeypatch, flag):
    got, res = _publish(tmp_path, monkeypatch, flag)
    assert hashlib.sha256(got).hexdigest() == hashlib.sha256(_main_bytes()).hexdigest()
    assert "oae_challenger" not in res


def test_flag_on_is_additive_and_rollback_restores_main(tmp_path, monkeypatch):
    on, res = _publish(tmp_path / "on", monkeypatch, "1")
    doc_on, base = json.loads(on), json.load(open(DOC))
    assert set(doc_on) - set(base) == {"oae_challenger"}
    assert {k: doc_on[k] for k in base} == base                       # no existing field changed
    blk = doc_on["oae_challenger"]
    assert blk["status"] == "not_activated" and blk["activated"] is False and blk["model_version"].startswith("cfb-oae-")
    assert "error" not in res["oae_challenger"] and blk["games"], res
    assert {g["game_id"] for g in blk["games"]} <= {g["game_id"] for g in base["games"]}
    off, _ = _publish(tmp_path / "off", monkeypatch, None)            # rollback = unset the flag
    assert off == _main_bytes()


def _assert_unavailable(got: bytes, reason: str):
    """Fallback contract: every production field byte-for-byte as on main, plus only an explicit unavailable stub."""
    doc, base = json.loads(got), json.load(open(DOC))
    assert set(doc) - set(base) == {"oae_challenger"} and {k: doc[k] for k in base} == base
    blk = doc["oae_challenger"]
    assert blk["available"] is False and reason in blk["unavailable_reason"] and blk["games"] == []
    assert blk["activated"] is False and blk["bet_authority"] is False and blk["operating_forecast"] is False
    doc.pop("oae_challenger")
    assert json.dumps(doc, separators=(",", ":"), default=None).encode() == _main_bytes()


def _boom(exc):
    def f(*a, **k):
        raise exc
    return f


@pytest.mark.parametrize("target,attr,exc,reason", [
    ("block", "build_block", RuntimeError("boom"), "boom"),                                   # any exception
    ("run", "_load_sched", FileNotFoundError("schedule.json"), "schedule.json"),              # missing input
    ("run", "_gamelog_raw", ValueError("bad game log"), "bad game log"),                      # invalid input
    ("run", "_fbs_conference_ids", None, "FBS conference ids unavailable"),
    ("frozen", "load", None, "sha256"),                                                       # frozen-hash mismatch
])
def test_failures_publish_production_unchanged_with_explicit_unavailable_block(tmp_path, monkeypatch, target, attr,
                                                                               exc, reason):
    from cfb.live import run

    from cfb_edge_finder.oae import block, frozen
    mod = {"block": block, "run": run, "frozen": frozen}[target]
    if attr == "_fbs_conference_ids":
        monkeypatch.setattr(run, attr, lambda: None)
    elif target == "frozen":
        monkeypatch.setattr(frozen, "load", _boom(frozen.FrozenAssetError("asset x sha256 mismatch")))
    else:
        monkeypatch.setattr(mod, attr, _boom(exc))
    got, res = _publish(tmp_path, monkeypatch, "1")
    assert reason in res["oae_challenger"]["error"]
    _assert_unavailable(got, reason)


def test_season_not_covered_is_explicit_and_leaves_production_unchanged(tmp_path, monkeypatch):
    from cfb.live import run
    real = run._load_sched
    monkeypatch.setattr(run, "_load_sched", lambda ctx: dict(real(ctx), season=2027))
    got, _ = _publish(tmp_path, monkeypatch, "1")
    _assert_unavailable(got, "season 2027 not covered")


def test_non_finite_block_is_never_published(tmp_path, monkeypatch):
    from cfb_edge_finder.oae import block
    real = block.build_block
    monkeypatch.setattr(block, "build_block", lambda *a, **k: dict(real(*a, **k), poison=float("nan")))
    got, _ = _publish(tmp_path, monkeypatch, "1")
    _assert_unavailable(got, "Out of range float")


def test_flag_cannot_flip_authority_and_block_is_labelled_non_operating(tmp_path, monkeypatch):
    on, _ = _publish(tmp_path, monkeypatch, "1")
    doc, base = json.loads(on), json.load(open(DOC))
    assert doc["model"] == base["model"] and doc["model"]["authority"] == "research_only"
    assert doc["notes"] == base["notes"] and doc["gate_summary"] == base["gate_summary"]
    blk = doc["oae_challenger"]
    assert blk["available"] is True and blk["authority"] == "research_only" and blk["role"] == "shadow"
    assert blk["status"] == "not_activated" and blk["activated"] is False
    assert blk["bet_authority"] is False and blk["operating_forecast"] is False
    assert set(doc) - set(base) == {"oae_challenger"}                 # nothing else added at top level


def test_unset_after_set_leaves_no_residue(tmp_path, monkeypatch):
    """Same archive root: publish with the flag ON, then OFF; the next latest.json is main's bytes again."""
    archive = tmp_path / "shared"
    on, _ = _publish(archive, monkeypatch, "1")
    assert b"oae_challenger" in on
    off, res = _publish(archive, monkeypatch, None)
    assert off == _main_bytes() and "oae_challenger" not in res
    work = json.load(open(os.path.join(str(archive / "w"), "projection_out.json")))
    assert "oae_challenger" not in work                               # nothing written back into the work copy
