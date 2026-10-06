"""The explorer export carries the script engine under event_research.extensions.

Against the committed catalog and the committed script-engine payloads: every
event whose game has a payload publishes it, every document still validates
and fits its byte budget, and the capability manifest says what it is.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import app_export  # noqa: E402
import research_export as rx  # noqa: E402
from edge_finder_contract import research as R  # noqa: E402

DATA_ROOT = REPO / "data" / "live"
SIFT_DIR = REPO / "data" / "scripting" / "live" / "sift"


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    if not (DATA_ROOT / "cfb_market_catalog.json").exists() or not any(SIFT_DIR.glob("*.json.gz")):
        pytest.skip("no committed catalog or script-engine payloads")
    out = tmp_path_factory.mktemp("engine") / "app" / "latest"
    captured = json.loads((DATA_ROOT / "cfb_market_catalog.json").read_text())["capture"]["captured_at"]
    assert app_export.main(["--out", str(out), "--data-root", str(DATA_ROOT), "--now", captured]) == 0
    rx.export_explorer(out, data_root=DATA_ROOT, research_root=None, max_history_commits=2, script_engine_dir=SIFT_DIR)
    return out


def _events(out: Path):
    for path in sorted((out / "explorer" / "events").glob("*.json")):
        yield json.loads(path.read_text(encoding="utf-8")), path


def test_events_carry_the_script_engine_and_stay_in_budget(published):
    payloads = rx.load_script_engine(SIFT_DIR)
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
