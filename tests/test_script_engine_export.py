"""The explorer export carries the script engine under event_research.extensions.

Against a COMMITTED three-game catalog fixture (tests/fixtures/script_engine_export: the
2026-10-07T23:19Z capture of UGA-ALA, LSU-UK and TEX-OKLA, reduced to those games), with
script-engine payloads built in the test by `scripts/script_engine.py build` from the snapshotted 2026
game log (data/scripting/validation/inputs): every event whose game has a payload publishes it, every
document still validates and fits its byte budget, the capability manifest says what it is, and no
scoring market carries script authority. Nothing here depends on what the live catalog lists today.

TEX-OKLA (290 markets) is the event that exceeded the budget on the live capture: it exercises the
deterministic over-budget fallback (`research_export.fit_script_engine`).
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

FIXTURE = REPO / "tests" / "fixtures" / "script_engine_export"
INPUTS = REPO / "data" / "scripting" / "validation" / "inputs"
GAMES = ("26OCT10UGAALA", "26OCT10LSUUK", "26OCT10TEXOKLA")


@pytest.fixture(scope="module")
def data_root(tmp_path_factory):
    """The committed catalog fixture, decompressed into a catalog directory."""
    root = tmp_path_factory.mktemp("catalog")
    (root / "games").mkdir()
    for src in [FIXTURE / "cfb_market_catalog.json.gz", *sorted((FIXTURE / "games").glob("*.json.gz"))]:
        rel = src.relative_to(FIXTURE).with_suffix("")
        with gzip.open(src, "rb") as fh, open(root / rel, "wb") as out:
            shutil.copyfileobj(fh, out)
    return root


@pytest.fixture(scope="module")
def captured(data_root):
    return json.loads((data_root / "cfb_market_catalog.json").read_text())["capture"]["captured_at"]


@pytest.fixture(scope="module")
def sift_dir(tmp_path_factory, data_root, captured):
    keys = {g["game_key"] for g in json.loads((data_root / "cfb_market_catalog.json").read_text())["games"]}
    assert set(GAMES) <= keys
    root = tmp_path_factory.mktemp("football")
    football = root / "football"
    football.mkdir()
    for src, name in (
        ("espn_2026_team_games.jsonl.gz", "team_games.jsonl"),
        ("espn_2026_schedule.json.gz", "schedule.json"),
    ):
        with gzip.open(INPUTS / src, "rb") as fh, open(football / name, "wb") as out:
            shutil.copyfileobj(fh, out)
    args = ["build", "--football-dir", str(football), "--catalog-dir", str(data_root), "--out-dir", str(root / "live")]
    args += ["--as-of", captured] + [a for g in GAMES for a in ("--game", g)]
    assert script_engine.main(args) == 0
    sift = root / "live" / "sift"
    assert {p.name[: -len(".json.gz")] for p in sift.glob("*.json.gz")} >= set(GAMES)
    return sift


@pytest.fixture(scope="module")
def published(tmp_path_factory, sift_dir, data_root, captured):
    out = tmp_path_factory.mktemp("engine") / "app" / "latest"
    assert app_export.main(["--out", str(out), "--data-root", str(data_root), "--now", captured]) == 0
    rx.export_explorer(out, data_root=data_root, research_root=None, max_history_commits=2, script_engine_dir=sift_dir)
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


def _check_scoring_expressions(engine: dict, seen: dict[str, int]) -> None:
    assert engine["band_policy"]["total_points"] == "UNCALIBRATED_DESCRIPTIVE"
    survivors = set(engine.get("script_survivors") or [])
    for e in (engine.get("script_market_map") or {}).get("expressions") or []:
        prefix = next((p for p in seen if e["thesis"].startswith(p)), None)
        if prefix is None:
            continue
        seen[prefix] += 1
        assert e["authority"] == "RESEARCH_UNCALIBRATED"
        assert set(e["labels"]) <= {"SCORING_BAND_UNCALIBRATED", "LOW_DATA_CONFIDENCE"}
        assert set(e["compat"]) <= {"R", "N"}
        assert f"{e['ticker']}:{e['side']}" not in survivors
    for t in engine.get("theses") or []:
        assert not t["thesis"].startswith(("total:", "team_scoring:"))


def test_no_scoring_market_carries_script_authority_in_the_built_payloads(sift_dir):
    """The fixture slate lists totals and team totals, so this check is never vacuous."""
    seen = {"total:": 0, "team_scoring:": 0}
    for payload in rx.load_script_engine(sift_dir).values():
        if payload.get("script_generation"):
            _check_scoring_expressions(payload, seen)
    assert all(seen.values()), seen


def test_no_scoring_market_is_published_with_script_authority(published):
    """Whatever survives the budget trim keeps the same research-only status."""
    for doc, _ in _events(published):
        engine = (doc.get("extensions") or {}).get("script_engine")
        if engine and engine.get("script_generation"):
            _check_scoring_expressions(engine, {"total:": 0, "team_scoring:": 0})


def test_the_over_budget_event_is_trimmed_with_provenance_not_published_oversized(published):
    trimmed = 0
    for doc, path in _events(published):
        assert path.stat().st_size <= rx.BUDGETS["event_research"], path.name
        engine = (doc.get("extensions") or {}).get("script_engine")
        if not engine or "payload_trim" not in engine:
            continue
        trimmed += 1
        trim = engine["payload_trim"]
        assert trim["steps"] == list(rx.ENGINE_TRIM_STEPS[: len(trim["steps"])])
        assert any("trimmed to fit the event budget" in n or "omitted" in n for n in doc["context"]["notes"])
        if not trim["omitted"]:
            # identity, status, scripts and their summaries, the SIFT read and findings survive every step
            for key in (
                "script_generation",
                "status",
                "game_scripts",
                "sift_read",
                "data_confidence",
                "matchup_findings",
            ):
                assert key in engine, key
            assert all(s["summary"] for s in engine["game_scripts"])
            assert set(engine["matchup_profile"]["teams"]) == {"home", "away"}
    assert trimmed >= 1


# ------------------------------------------------------------------ fit_script_engine at the boundary


@pytest.fixture(scope="module")
def boundary(sift_dir, published):
    """A real payload and the common event fields of its published event."""
    payloads = rx.load_script_engine(sift_dir)
    doc, _ = next(
        (d, p) for d, p in _events(published) if d["event"]["source_ids"]["kalshi_game_key"] == "26OCT10LSUUK"
    )
    common = {
        k: doc[k]
        for k in (
            "event",
            "quality",
            "participants",
            "projections",
            "markets",
            "market_history_path",
            "wagers",
            "links",
            "run_id",
            "generated_at",
        )
    }
    common["sport"] = doc["sport"]
    engine = payloads["26OCT10LSUUK"]
    full = rx.doc_bytes(
        rx.R.event_research(**{**common, "extensions": {"script_engine": engine}}, matchup=[], context={"notes": []})
    )
    return engine, common, full


def test_a_payload_exactly_at_the_budget_is_published_untouched(boundary):
    engine, common, full = boundary
    out, note = rx.fit_script_engine(engine, common, [], [], budget=full)
    assert out is engine and note is None


def test_one_byte_over_the_budget_applies_only_the_first_trim_step(boundary):
    engine, common, full = boundary
    out, note = rx.fit_script_engine(engine, common, [], [], budget=full - 1)
    assert out["payload_trim"]["steps"] == [rx.ENGINE_TRIM_STEPS[0]]
    assert note.endswith(rx.ENGINE_TRIM_STEPS[0])
    assert out["game_scripts"] == engine["game_scripts"] and out["script_generation"] == engine["script_generation"]
    assert "correlation" in json.dumps(engine["script_market_map"]["expressions"])  # input untouched


def test_trimming_is_deterministic_and_keeps_the_identity_and_scripts(boundary):
    engine, common, full = boundary
    tight = full // 3
    a, note_a = rx.fit_script_engine(engine, common, [], [], budget=tight)
    b, note_b = rx.fit_script_engine(engine, common, [], [], budget=tight)
    assert a == b and note_a == note_b
    if not a["payload_trim"]["omitted"]:
        assert a["script_generation"] == engine["script_generation"]
        assert [s["summary"] for s in a["game_scripts"]] == [s["summary"] for s in engine["game_scripts"]]
        assert a["sift_read"] == engine["sift_read"] and a["status"] == engine["status"]


def test_a_payload_that_cannot_fit_is_omitted_explicitly(boundary):
    engine, common, _ = boundary
    out, note = rx.fit_script_engine(engine, common, [], [], budget=1)
    assert out["status"] == rx.ENGINE_OMITTED_STATUS and "script_generation" not in out
    assert out["artifact_hash"] == engine["script_generation"]["artifact_hash"]
    assert out["payload_trim"]["omitted"] is True and "omitted" in note


def test_an_event_that_cannot_fit_raises_instead_of_publishing(monkeypatch, tmp_path, sift_dir, data_root, captured):
    out = tmp_path / "app" / "latest"
    assert app_export.main(["--out", str(out), "--data-root", str(data_root), "--now", captured]) == 0
    monkeypatch.setitem(rx.BUDGETS, "event_research", 5_000)
    with pytest.raises(rx.EventBudgetError):
        rx.export_explorer(
            out, data_root=data_root, research_root=None, max_history_commits=2, script_engine_dir=sift_dir
        )


def test_the_v2_shadow_claims_are_published_and_never_trimmed(boundary, published):
    engine, common, full = boundary
    assert engine["claims_v2"]["activation"] == "SHADOW"
    for budget in (full - 1, full // 2, full // 3):
        out, _ = rx.fit_script_engine(engine, common, [], [], budget=budget)
        if not out["payload_trim"]["omitted"]:
            assert out["claims_v2"] == engine["claims_v2"], budget
    carried = 0
    for doc, _ in _events(published):
        engine_doc = (doc.get("extensions") or {}).get("script_engine") or {}
        if engine_doc.get("script_generation"):
            assert engine_doc["claims_v2"]["claims_artifact_hash"]
            carried += 1
    assert carried == 3
