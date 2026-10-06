"""The explorer export carries the script engine under event_research.extensions.

Against the committed catalog, with script-engine payloads built in the test
by `scripts/script_engine.py build` from the snapshotted 2026 game log
(data/scripting/validation/inputs): every event whose game has a payload
publishes it, every document still validates and fits its byte budget, the
capability manifest says what it is, and no scoring market is published
with script authority.
"""

from __future__ import annotations

import gzip
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import app_export  # noqa: E402
import research_export as rx  # noqa: E402
import script_engine  # noqa: E402
from edge_finder_contract import research as R  # noqa: E402

DATA_ROOT = REPO / "data" / "live"
INPUTS = REPO / "data" / "scripting" / "validation" / "inputs"
GAMES = ("26OCT10UGAALA", "26OCT10LSUUK", "26OCT10TEXOKLA")


@pytest.fixture(scope="module")
def sift_dir(tmp_path_factory):
    catalog = DATA_ROOT / "cfb_market_catalog.json"
    if not catalog.exists():
        pytest.skip("no committed catalog")
    keys = {g["game_key"] for g in json.loads(catalog.read_text())["games"]}
    if not set(GAMES) <= keys:
        pytest.skip("the committed catalog no longer lists the test games")
    root = tmp_path_factory.mktemp("football")
    football = root / "football"
    football.mkdir()
    for src, name in (
        ("espn_2026_team_games.jsonl.gz", "team_games.jsonl"),
        ("espn_2026_schedule.json.gz", "schedule.json"),
    ):
        with gzip.open(INPUTS / src, "rb") as fh, open(football / name, "wb") as out:
            shutil.copyfileobj(fh, out)
    captured = json.loads(catalog.read_text())["capture"]["captured_at"]
    args = ["build", "--football-dir", str(football), "--catalog-dir", str(DATA_ROOT), "--out-dir", str(root / "live")]
    args += ["--as-of", captured] + [a for g in GAMES for a in ("--game", g)]
    assert script_engine.main(args) == 0
    sift = root / "live" / "sift"
    assert {p.name[: -len(".json.gz")] for p in sift.glob("*.json.gz")} >= set(GAMES)
    return sift


@pytest.fixture(scope="module")
def published(tmp_path_factory, sift_dir):
    out = tmp_path_factory.mktemp("engine") / "app" / "latest"
    captured = json.loads((DATA_ROOT / "cfb_market_catalog.json").read_text())["capture"]["captured_at"]
    assert app_export.main(["--out", str(out), "--data-root", str(DATA_ROOT), "--now", captured]) == 0
    rx.export_explorer(out, data_root=DATA_ROOT, research_root=None, max_history_commits=2, script_engine_dir=sift_dir)
    return out


def _events(out: Path):
    for path in sorted((out / "explorer" / "events").glob("*.json")):
        yield json.loads(path.read_text(encoding="utf-8")), path


def test_events_carry_the_script_engine_and_stay_in_budget(published, sift_dir):
    payloads = rx.load_script_engine(sift_dir)
    carried = 0
    for doc, path in _events(published):
        assert path.stat().st_size <= rx.BUDGETS["event_research"], path.name
        key = doc["event"]["source_ids"]["kalshi_game_key"]
        engine = (doc.get("extensions") or {}).get("script_engine")
        if key in payloads:
            assert engine is not None, key
            carried += 1
            gen = engine.get("script_generation")
            if gen:
                assert gen["market_blind"] is True
                assert gen["artifact_hash"] == payloads[key]["script_generation"]["artifact_hash"]
                assert any("CFB Script Engine" in n for n in doc["context"]["notes"])
        else:
            assert engine is None
    assert carried > 0


def test_the_published_tree_still_verifies(published):
    assert R.verify_explorer(published) == []


def test_capabilities_name_the_engine_as_research(published):
    caps = {c["capability"]: c for c in json.loads((published / "explorer" / "capabilities.json").read_text())["items"]}
    for name in ("opponent_adjustment", "matchup_metrics"):
        assert caps[name]["status"] == "RESEARCH"
        assert "Script Engine" in caps[name]["summary"]
        assert any("not a pricing model" in lim for lim in caps[name]["limitations"])
    assert caps["raw_projections"]["status"] == "UNAVAILABLE"


def test_no_published_payload_claims_a_price_edge(published):
    for doc, _ in _events(published):
        engine = (doc.get("extensions") or {}).get("script_engine")
        if not engine:
            continue
        blob = json.dumps(engine).lower()
        for banned in ("+ev", "bet_up_to", 'fair_probability": 0', "high_probability_expression"):
            assert banned not in blob
        for script in engine.get("game_scripts") or []:
            assert script["probability"] is None


def test_no_scoring_market_is_published_with_script_authority(published):
    seen = {"total:": 0, "team_scoring:": 0}
    for doc, _ in _events(published):
        engine = (doc.get("extensions") or {}).get("script_engine")
        if not engine or not engine.get("script_generation"):
            continue
        assert engine["band_policy"]["total_points"] == "UNCALIBRATED_DESCRIPTIVE"
        survivors = set(engine["script_survivors"])
        for e in engine["script_market_map"]["expressions"]:
            prefix = next((p for p in seen if e["thesis"].startswith(p)), None)
            if prefix is None:
                continue
            seen[prefix] += 1
            assert e["authority"] == "RESEARCH_UNCALIBRATED"
            assert set(e["labels"]) <= {"SCORING_BAND_UNCALIBRATED", "LOW_DATA_CONFIDENCE"}
            assert set(e["compat"]) <= {"R", "N"}
            assert f"{e['ticker']}:{e['side']}" not in survivors
        for t in engine["theses"]:
            assert not t["thesis"].startswith(("total:", "team_scoring:"))
    assert all(seen.values()), seen
