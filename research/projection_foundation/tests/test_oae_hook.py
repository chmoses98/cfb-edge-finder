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


def test_block_failure_never_blocks_the_production_publish(tmp_path, monkeypatch):
    from cfb_edge_finder.oae import block
    monkeypatch.setattr(block, "build_block", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    got, res = _publish(tmp_path, monkeypatch, "1")
    assert got == _main_bytes() and "boom" in res["oae_challenger"]["error"]
