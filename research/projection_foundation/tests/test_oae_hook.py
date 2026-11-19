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
OPERATING = "CFB_OAE_OPERATING"
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


def _setenv(monkeypatch, name, value):
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)


def _publish(tmp_path, monkeypatch, flag, operating=None, now=None):
    """Both keys are set (or cleared) explicitly, so a test never inherits CFB_OAE_OPERATING from the job env."""
    from cfb import defense_shadow
    from cfb.live import run
    monkeypatch.setattr(defense_shadow, "build", lambda *a, **k: {"league": {}})
    _setenv(monkeypatch, FLAG, flag)
    _setenv(monkeypatch, OPERATING, operating)
    doc = json.load(open(DOC))
    if now is None:
        now = (pd.Timestamp(min(g["start_utc"] for g in doc["games"])) - pd.Timedelta(hours=4)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    ctx = _ctx(tmp_path, now)
    res = run.stage_publish(ctx)
    return open(os.path.join(ctx.root, "projections", "cfb", "latest.json"), "rb").read(), res


def _main_bytes():
    """Exactly what the publish stage on main writes: the projection document serialised unchanged."""
    from cfb.live import run
    return json.dumps(json.load(open(DOC)), separators=(",", ":"), default=run._default).encode()


@pytest.mark.parametrize("operating", [None, "1"])
@pytest.mark.parametrize("flag", [None, "", "0", "true"])
def test_flag_off_artifact_is_byte_identical_to_main(tmp_path, monkeypatch, flag, operating):
    """The promotion key alone publishes nothing: with CFB_OAE_FORECASTS off the artifact is main's bytes."""
    got, res = _publish(tmp_path, monkeypatch, flag, operating)
    assert hashlib.sha256(got).hexdigest() == hashlib.sha256(_main_bytes()).hexdigest()
    assert "oae_challenger" not in res


def test_flag_on_is_additive_and_rollback_restores_main(tmp_path, monkeypatch):
    on, res = _publish(tmp_path / "on", monkeypatch, "1", None)
    doc_on, base = json.loads(on), json.load(open(DOC))
    assert set(doc_on) - set(base) == {"oae_challenger"}
    assert {k: doc_on[k] for k in base} == base                       # no existing field changed
    blk = doc_on["oae_challenger"]
    assert blk["status"] == "not_activated" and blk["activated"] is False and blk["model_version"].startswith("cfb-oae-")
    assert "error" not in res["oae_challenger"] and blk["games"], res
    assert {g["game_id"] for g in blk["games"]} <= {g["game_id"] for g in base["games"]}
    off, _ = _publish(tmp_path / "off", monkeypatch, None)            # rollback = unset the flag
    assert off == _main_bytes()


def _assert_never_operating(blk: dict):
    """Fail-closed authority contract: identical to the non-operating labels, never operating, never bet authority."""
    assert blk["status"] == "not_activated" and blk["activated"] is False and blk["operating_forecast"] is False
    assert blk["role"] == "shadow" and blk["authority"] == "research_only" and blk["bet_authority"] is False
    assert "Not approved for use" in blk.get("note", "Not approved for use")


def _assert_unavailable(got: bytes, reason: str):
    """Fallback contract: every production field byte-for-byte as on main, plus only an explicit unavailable stub."""
    doc, base = json.loads(got), json.load(open(DOC))
    assert set(doc) - set(base) == {"oae_challenger"} and {k: doc[k] for k in base} == base
    blk = doc["oae_challenger"]
    assert blk["available"] is False and reason in blk["unavailable_reason"] and blk["games"] == []
    assert blk["activated"] is False and blk["bet_authority"] is False and blk["operating_forecast"] is False
    _assert_never_operating(blk)
    doc.pop("oae_challenger")
    assert json.dumps(doc, separators=(",", ":"), default=None).encode() == _main_bytes()


def _boom(exc):
    def f(*a, **k):
        raise exc
    return f


@pytest.mark.parametrize("operating", [None, "1"])   # "1" = promotion requested: must still fail closed
@pytest.mark.parametrize("target,attr,exc,reason", [
    ("block", "build_block", RuntimeError("boom"), "boom"),                                   # any exception
    ("run", "_load_sched", FileNotFoundError("schedule.json"), "schedule.json"),              # missing input
    ("run", "_gamelog_raw", ValueError("bad game log"), "bad game log"),                      # invalid input
    ("run", "_fbs_conference_ids", None, "FBS conference ids unavailable"),
    ("frozen", "load", None, "sha256"),                                                       # frozen-hash mismatch
])
def test_failures_publish_production_unchanged_with_explicit_unavailable_block(tmp_path, monkeypatch, target, attr,
                                                                               exc, reason, operating):
    from cfb.live import run

    from cfb_edge_finder.oae import block, frozen
    mod = {"block": block, "run": run, "frozen": frozen}[target]
    if attr == "_fbs_conference_ids":
        monkeypatch.setattr(run, attr, lambda: None)
    elif target == "frozen":
        monkeypatch.setattr(frozen, "load", _boom(frozen.FrozenAssetError("asset x sha256 mismatch")))
    else:
        monkeypatch.setattr(mod, attr, _boom(exc))
    got, res = _publish(tmp_path, monkeypatch, "1", operating)
    assert reason in res["oae_challenger"]["error"] and res["oae_challenger"]["activated"] is False
    _assert_unavailable(got, reason)


@pytest.mark.parametrize("operating", [None, "1"])
def test_season_not_covered_is_explicit_and_leaves_production_unchanged(tmp_path, monkeypatch, operating):
    from cfb.live import run
    real = run._load_sched
    monkeypatch.setattr(run, "_load_sched", lambda ctx: dict(real(ctx), season=2027))
    got, res = _publish(tmp_path, monkeypatch, "1", operating)
    _assert_unavailable(got, "season 2027 not covered")
    assert res["oae_challenger"]["activated"] is False


@pytest.mark.parametrize("operating", [None, "1"])
def test_non_finite_block_is_never_published(tmp_path, monkeypatch, operating):
    from cfb_edge_finder.oae import block
    real = block.build_block
    monkeypatch.setattr(block, "build_block", lambda *a, **k: dict(real(*a, **k), poison=float("nan")))
    got, _ = _publish(tmp_path, monkeypatch, "1", operating)
    _assert_unavailable(got, "Out of range float")


def test_promotion_with_both_keys_activates_a_valid_block_without_bet_authority(tmp_path, monkeypatch):
    """The full activation path (what #311 turns on): both keys = 1 and a valid block -> operating forecast."""
    on, res = _publish(tmp_path, monkeypatch, "1", "1")
    doc, base = json.loads(on), json.load(open(DOC))
    assert set(doc) - set(base) == {"oae_challenger"} and {k: doc[k] for k in base} == base
    blk = doc["oae_challenger"]
    assert blk["available"] is True and blk["unavailable_reason"] is None and blk["games"]
    assert blk["status"] == "activated" and blk["activated"] is True and blk["operating_forecast"] is True
    assert blk["role"] == "operating" and blk["bet_authority"] is False and "promotion_withheld" not in blk
    assert res["oae_challenger"]["activated"] is True and res["oae_challenger"]["games"] == len(blk["games"]) > 0
    shadow, _ = _publish(tmp_path / "shadow", monkeypatch, "1", None)       # same forecasts with the key off
    assert json.loads(shadow)["oae_challenger"]["games"] == blk["games"]
    off, _ = _publish(tmp_path / "off", monkeypatch, None, None)           # full rollback
    assert off == _main_bytes()


def test_promotion_is_withheld_when_no_game_can_be_forecast(tmp_path, monkeypatch):
    """Every game already started: the block is available but empty, so it is NOT declared operating."""
    doc = json.load(open(DOC))
    late = (pd.Timestamp(max(g["start_utc"] for g in doc["games"])) + pd.Timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    got, res = _publish(tmp_path, monkeypatch, "1", "1", now=late)
    blk = json.loads(got)["oae_challenger"]
    assert blk["games"] == [] and "no forecast games" in blk["promotion_withheld"]
    _assert_never_operating(blk)
    assert res["oae_challenger"]["activated"] is False


@pytest.mark.parametrize("corrupt,why", [
    (lambda b: b["games"][0]["fg"].__setitem__("p_home_win", 1.4), "probabilities outside [0, 1]"),
    (lambda b: b["games"][0]["fg"].__setitem__("p_away_win", 0.9), "p_home_win + p_away_win != 1"),
    (lambda b: b.__setitem__("model_version", "cfb-oae-9.9.9"), "model_version"),
    (lambda b: b["provenance"]["files"].__setitem__("frozen_p15.json", "0" * 64), "provenance hashes differ"),
    (lambda b: b.update(available=False, unavailable_reason="upstream said so"), "forecast unavailable"),
])
def test_invalid_block_from_the_builder_is_demoted_by_the_gate(tmp_path, monkeypatch, corrupt, why):
    """Even a builder that returns a block already labelled operating cannot get an invalid forecast activated."""
    from cfb_edge_finder import oae
    from cfb_edge_finder.oae import block

    real = block.build_block

    def bad(*a, **k):
        b = json.loads(json.dumps(real(*a, **k)))
        b.update(oae.OPERATING_AUTHORITY)                                 # forged activation
        corrupt(b)
        return b
    monkeypatch.setattr(block, "build_block", bad)
    got, res = _publish(tmp_path, monkeypatch, "1", "1")
    blk = json.loads(got)["oae_challenger"]
    _assert_never_operating(blk)
    assert any(why in w for w in blk["promotion_withheld"]), blk["promotion_withheld"]
    assert res["oae_challenger"]["activated"] is False


def test_frozen_hash_failure_after_load_withholds_promotion(tmp_path, monkeypatch):
    """`frozen.load` is cached per process; the gate re-hashes from disk, so a later mismatch still fails closed."""
    from cfb_edge_finder.oae import frozen
    frozen.load()                                                          # warm the cache with valid assets
    monkeypatch.setattr(frozen, "verify_all", _boom(frozen.FrozenAssetError("frozen asset x sha256 mismatch")))
    got, res = _publish(tmp_path, monkeypatch, "1", "1")
    blk = json.loads(got)["oae_challenger"]
    _assert_never_operating(blk)
    assert any("sha256 mismatch" in w for w in blk["promotion_withheld"])
    assert res["oae_challenger"]["activated"] is False


def test_flag_cannot_flip_authority_and_block_is_labelled_non_operating(tmp_path, monkeypatch):
    on, _ = _publish(tmp_path, monkeypatch, "1", None)
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
    on, _ = _publish(archive, monkeypatch, "1", "1")
    assert b"oae_challenger" in on
    off, res = _publish(archive, monkeypatch, None)
    assert off == _main_bytes() and "oae_challenger" not in res
    work = json.load(open(os.path.join(str(archive / "w"), "projection_out.json")))
    assert "oae_challenger" not in work                               # nothing written back into the work copy
