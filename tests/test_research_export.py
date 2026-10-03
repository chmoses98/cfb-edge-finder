"""The CFB research explorer (scripts/research_export.py, contract 1.1.0 research graph).

Inputs are real: the committed catalog at ``data/live`` (and its git history, as deep as the checkout
has it), a field-trimmed slice of the ``research-data`` branch committed under
``tests/fixtures/research_export/research_root`` (CFBD 2024-2026 season tables and 2025-2026 rows of
``data/research/v2/dataset.parquet``, every value copied verbatim), and, when ``CFB_ACCOUNTING_DIR``
names a ``git archive`` of ``accounting-data``, the real wager ledger. The synthetic v1 fixture from
``test_app_contract_v1`` covers the wager path deterministically and the research-fetch-failure path.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from edge_finder_contract import CAPABILITIES, packet
from edge_finder_contract import research as R

from tests.test_app_contract_v1 import app_export, write_accounting_dir, write_data_root

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DATA_ROOT = REPO_ROOT / "data" / "live"
RESEARCH_ROOT = REPO_ROOT / "tests" / "fixtures" / "research_export" / "research_root"
NOW = "2026-10-03T12:00:00Z"


def _load_module():
    spec = importlib.util.spec_from_file_location("research_export", REPO_ROOT / "scripts" / "research_export.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["research_export"] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


rx = _load_module()

# The audit's capability matrix (scratchpad audit_cfb.md §4 / §10) as published by this adapter. Where the
# adapter publishes less than the audit allows, the published status is UNAVAILABLE and the comment says why.
EXPECTED_WITH_RESEARCH = {
    "team_profiles": "PARTIAL",  # every v1 participant; CFBD metrics only for exactly-mapped FBS teams
    "player_profiles": "UNAVAILABLE",  # audit: no player-level data anywhere
    "event_research": "PARTIAL",  # markets + wagers + frozen-snapshot matchup; model retired
    "team_metrics": "PARTIAL",  # audit PARTIAL: CFBD season aggregates, frozen 2026-09-02
    "player_metrics": "UNAVAILABLE",  # audit UNAVAILABLE
    "team_game_logs": "UNAVAILABLE",  # audit PARTIAL, not republished: CFBD raw-row redistribution licence
    "player_game_logs": "UNAVAILABLE",  # audit UNAVAILABLE
    "historical_results": "UNAVAILABLE",  # audit PARTIAL, not republished: CFBD raw-row redistribution licence
    "opponents": "PARTIAL",  # audit PARTIAL: current-slate opponents + series labels
    "opponent_adjustment": "RESEARCH",  # audit RESEARCH: dataset.parquet states, builder not on main
    "schedule_strength": "UNAVAILABLE",  # audit: nothing computes it
    "recent_form_windows": "UNAVAILABLE",  # audit: not stored
    "usage": "UNAVAILABLE",
    "lineups": "UNAVAILABLE",
    "injuries": "UNAVAILABLE",  # audit RESEARCH at most (hibernated sidecar, no current event): not published
    "matchup_metrics": "RESEARCH",  # audit RESEARCH
    "projection_distributions": "UNAVAILABLE",  # audit RESEARCH (retired model, no current event): not published
    "raw_projections": "UNAVAILABLE",  # audit RESEARCH (retired model): model_prices empty by design
    "market_prices": "VERIFIED",  # audit VERIFIED
    "market_price_history": "PARTIAL",  # audit PARTIAL: change-detected catalog snapshots
    "advanced_stats": "PARTIAL",  # audit PARTIAL
    "situational_splits": "PARTIAL",  # audit PARTIAL: play_type only
    "player_props": "PARTIAL",  # audit PARTIAL (inventory only)
    "team_props": "VERIFIED",  # audit VERIFIED (inventory)
    "game_markets": "VERIFIED",  # audit VERIFIED (inventory)
    "play_by_play": "UNAVAILABLE",  # audit UNAVAILABLE
    "weather": "UNAVAILABLE",  # audit RESEARCH (hibernated, no current event): not published
    "venue_effects": "PARTIAL",  # audit PARTIAL: home-stadium attributes, no effect estimates
    "calibration": "UNAVAILABLE",  # audit RESEARCH (retired model): not published
    "historical_accuracy": "UNAVAILABLE",  # audit RESEARCH (retired model): not published
    "clv": "UNAVAILABLE",  # audit PARTIAL for the model era only; owner CLV not computed
    "wager_history": "VERIFIED",  # audit VERIFIED (when a ledger wager is on the board)
    "rankings": "PARTIAL",
    "time_series": "PARTIAL",
    "comparisons": "PARTIAL",
    "search": "VERIFIED",
}
CFBD_BACKED = (
    "team_metrics",
    "advanced_stats",
    "rankings",
    "comparisons",
    "time_series",
    "situational_splits",
    "opponent_adjustment",
    "matchup_metrics",
    "venue_effects",
)


def _caps(root: Path) -> dict[str, dict]:
    doc = json.loads((root / "explorer" / "capabilities.json").read_text(encoding="utf-8"))
    return {c["capability"]: c for c in doc["items"]}


def _read(root: Path, name: str) -> dict:
    return json.loads((root / name).read_text(encoding="utf-8"))


def _v1_export(out: Path, data_root: Path, accounting: Path | None, now: str) -> None:
    argv = ["--out", str(out), "--data-root", str(data_root), "--now", now, "--commit-sha", "abc123"]
    if accounting is not None:
        argv += ["--accounting-dir", str(accounting)]
    assert app_export.main(argv) == 0


@pytest.fixture(scope="module")
def real(tmp_path_factory) -> dict:
    if not (REAL_DATA_ROOT / "cfb_market_catalog.json").exists():
        pytest.skip("no committed catalog at data/live")
    acct = os.environ.get("CFB_ACCOUNTING_DIR")
    accounting = Path(acct) if acct and Path(acct).is_dir() else None
    out = tmp_path_factory.mktemp("real") / "app" / "latest"
    captured_at = _read(REAL_DATA_ROOT, "cfb_market_catalog.json")["capture"]["captured_at"]
    _v1_export(out, REAL_DATA_ROOT, accounting, captured_at)
    # the newest 12 catalog commits keep the test fast; the workflow reads every commit that lists a board game
    index = rx.export_explorer(out, data_root=REAL_DATA_ROOT, research_root=RESEARCH_ROOT, max_history_commits=12)
    return {"out": out, "index": index, "accounting": accounting}


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory) -> dict:
    base = tmp_path_factory.mktemp("synthetic")
    data_root = write_data_root(base / "live")
    accounting = write_accounting_dir(base / "acct")
    out = base / "app" / "latest"
    _v1_export(out, data_root, accounting, NOW)
    rx.export_explorer(out, data_root=data_root, research_root=RESEARCH_ROOT)
    return {"base": base, "out": out, "data_root": data_root, "accounting": accounting}


# ------------------------------------------------------------------ 1. real inputs publish a clean graph


def test_real_explorer_verifies_and_describes_the_v1_publication(real):
    out, index = real["out"], real["index"]
    assert R.verify_explorer(out) == []
    manifest = _read(out, "manifest.json")
    assert index["run_id"] == manifest["run_id"] == index["base_manifest_run_id"]
    assert index["generated_at"] == manifest["generated_at"]
    assert index["commit_sha"] == manifest["commit_sha"]
    # the v1 bundle is still intact beside the explorer
    from edge_finder_contract import publish

    assert publish.verify_published(out) == []


def test_real_documents_stay_inside_the_byte_budgets(real):
    root = real["out"] / "explorer"
    for sub, budget in (("teams", 150_000), ("events", 150_000), ("market_history", 400_000)):
        sizes = {p.name: p.stat().st_size for p in (root / sub).glob("*.json")}
        assert sizes and max(sizes.values()) <= budget, (sub, max(sizes.items(), key=lambda kv: kv[1]))
    assert (root / "search_index.json").stat().st_size <= 300_000


# ------------------------------------------------------------------ 2. determinism


def test_two_publishes_are_byte_identical(synthetic, tmp_path):
    other = tmp_path / "app" / "latest"
    shutil.copytree(synthetic["out"], other)
    shutil.rmtree(other / "explorer")
    rx.export_explorer(other, data_root=synthetic["data_root"], research_root=RESEARCH_ROOT)
    assert R.digest_tree(other) == R.digest_tree(synthetic["out"])
    before = R.digest_tree(synthetic["out"])
    rx.export_explorer(synthetic["out"], data_root=synthetic["data_root"], research_root=RESEARCH_ROOT)
    assert R.digest_tree(synthetic["out"]) == before


# ------------------------------------------------------------------ 3. coverage of the v1 publication


def test_every_v1_event_and_participant_is_covered(real):
    out = real["out"]
    _, docs = R.load_explorer(out)
    events = _read(out, "events.json")["items"]
    explorer_events = {d["event"]["event_id"]: d for d in docs.values() if d["kind"] == "event_research"}
    profiles = {d["entity"]["participant_id"]: d for d in docs.values() if d["kind"] == "entity_profile"}
    histories = {d["event_id"] for d in docs.values() if d["kind"] == "market_history"}
    assert set(explorer_events) == {e["event_id"] for e in events}
    assert histories == {e["event_id"] for e in events}
    v1_participants = {p["participant_id"]: p for e in events for p in e["participants"]}
    assert set(profiles) == set(v1_participants)
    for pid, p in v1_participants.items():
        assert profiles[pid]["entity"]["source_ids"] == p["source_ids"]
        assert profiles[pid]["entity"]["display_name"] == p["display_name"]
    markets = {m["market_id"]: m for m in _read(out, "markets.json")["items"]}
    for eid, doc in explorer_events.items():
        assert doc["event"] == next(e for e in events if e["event_id"] == eid)
        assert {m["market_id"] for m in doc["markets"]} == {
            m for m, row in markets.items() if row.get("event_id") == eid
        }
    mapped = [p for p in profiles.values() if p["extensions"].get("cfbd")]
    unmapped = [p for p in profiles.values() if not p["extensions"].get("cfbd")]
    assert mapped and all(p["metrics"] for p in mapped)
    assert all(not p["metrics"] and p["games"] for p in unmapped)
    caps = _caps(out)
    assert any(
        f"{len(unmapped)} of {len(profiles)} v1 participants" in lim for lim in caps["team_profiles"]["limitations"]
    )


def test_market_history_comes_from_catalog_snapshots(real):
    _, docs = R.load_explorer(real["out"])
    histories = [d for d in docs.values() if d["kind"] == "market_history"]
    for doc in histories:
        assert "change-detected snapshots, not a closing series" in doc["quality"]["limitations"]
        for s in doc["series"]:
            assert s["points"] and all(
                p["source"].startswith(("catalog@", "catalog working tree")) for p in s["points"]
            )
            states = [(p["yes_bid"], p["yes_ask"], p["last_price"]) for p in s["points"]]
            # a point is published only when the book or the last trade moved
            assert all(a != b for a, b in zip(states, states[1:], strict=False))


# ------------------------------------------------------------------ 4. capability statuses = the audit matrix


def test_capability_statuses_match_the_audit(real, synthetic):
    assert set(EXPECTED_WITH_RESEARCH) == set(CAPABILITIES)
    caps = _caps(synthetic["out"])
    # the synthetic board (one game, five markets) lists no prop market: the inventory decides those two
    synthetic_expected = dict(EXPECTED_WITH_RESEARCH, player_props="UNAVAILABLE", team_props="UNAVAILABLE")
    assert {k: v["status"] for k, v in caps.items()} == synthetic_expected
    wh = caps["wager_history"]
    assert wh["evidence"] and all(p.startswith("explorer/events/") for p in wh["evidence"])
    for path in wh["evidence"]:
        assert _read(synthetic["out"], path)["wagers"]
    real_caps = {k: v["status"] for k, v in _caps(real["out"]).items()}
    expected = dict(EXPECTED_WITH_RESEARCH)
    if real["accounting"] is None:
        expected["wager_history"] = "UNAVAILABLE"
    elif real_caps["wager_history"] == "UNKNOWN":  # a real ledger whose wagers have all left the board
        expected["wager_history"] = "UNKNOWN"
    assert real_caps == expected
    for item in _caps(real["out"]).values():
        if item["status"] in ("PARTIAL", "RESEARCH"):
            assert item["limitations"], item["capability"]
        if item["status"] == "UNAVAILABLE":
            assert item["reasons"], item["capability"]
    assert any("licence" in lim for lim in _caps(real["out"])["team_metrics"]["limitations"])


def test_research_fetch_failure_still_publishes_markets_and_capabilities(synthetic, tmp_path):
    for research_root in (None, tmp_path / "empty-research"):
        if research_root is not None:
            research_root.mkdir()
        out = tmp_path / f"app-{'none' if research_root is None else 'empty'}" / "latest"
        shutil.copytree(synthetic["out"], out)
        shutil.rmtree(out / "explorer")
        rx.export_explorer(out, data_root=synthetic["data_root"], research_root=research_root)
        assert R.verify_explorer(out) == []
        caps = _caps(out)
        for name in CFBD_BACKED:
            assert caps[name]["status"] == "UNAVAILABLE", name
            assert "research-data branch not available" in caps[name]["reasons"], name
        assert caps["market_prices"]["status"] == "VERIFIED"
        assert caps["market_price_history"]["status"] == "PARTIAL"
        assert caps["wager_history"]["status"] == "VERIFIED"
        assert caps["team_profiles"]["status"] == "PARTIAL"
        index = R.read_index(out)
        assert index["counts"]["teams"] == 2 and index["counts"]["rankings"] == 0 and index["counts"]["series"] == 0
        assert any("research-data branch not available" in w for w in index["warnings"])
        _, docs = R.load_explorer(out)
        for prof in (d for d in docs.values() if d["kind"] == "entity_profile"):
            assert prof["metrics"] == [] and prof["extensions"]["cfbd"] is None and prof["markets"]


def test_without_pyarrow_the_research_states_are_unavailable(synthetic, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", None)
    out = tmp_path / "app" / "latest"
    shutil.copytree(synthetic["out"], out)
    rx.export_explorer(out, data_root=synthetic["data_root"], research_root=RESEARCH_ROOT)
    assert R.verify_explorer(out) == []
    caps = _caps(out)
    assert caps["opponent_adjustment"]["status"] == "UNAVAILABLE"
    assert "pyarrow" in caps["opponent_adjustment"]["reasons"][0]
    assert caps["team_metrics"]["status"] == "PARTIAL"


# ------------------------------------------------------------------ 5. the handicap packet


def test_game_packet_for_the_first_event_is_complete(real):
    out = real["out"]
    first = sorted(_read(out, "events.json")["items"], key=lambda e: (e["start_time_utc"], e["event_id"]))[0]
    pk = packet.build(app_root=out, scope_kind="GAME", event_id=first["event_id"])  # validates
    assert pk["markets"] and all(m["event_id"] == first["event_id"] for m in pk["markets"])
    assert {e["entity_id"] for e in pk["evidence"]} == {p["participant_id"] for p in first["participants"]}
    assert pk["quality"]["missing"] == []
    assert pk["quality"]["capabilities"]["market_prices"] == "VERIFIED"


# ------------------------------------------------------------------ 6. secrets


def test_no_secret_shaped_strings(real, synthetic):
    assert R.no_secret_shaped_strings(real["out"]) == []
    assert R.no_secret_shaped_strings(synthetic["out"]) == []


# ------------------------------------------------------------------ 7. RESEARCH stays RESEARCH


def test_research_observations_keep_their_status_in_profiles_and_packets(synthetic):
    out = synthetic["out"]
    _, docs = R.load_explorer(out)
    registry = next(d for d in docs.values() if d["kind"] == "metric_registry")
    research_ids = {m["metric_id"] for m in registry["items"] if m["quality"]["status"] == "RESEARCH"}
    assert research_ids >= {"met_cfb.adj_off_ppa", "met_cfb.adj_def_ppa", "met_cfb.adj_margin"}
    seen = 0
    for prof in (d for d in docs.values() if d["kind"] == "entity_profile"):
        for o in prof["metrics"]:
            if o["metric_id"] in research_ids:
                assert o["quality_status"] == "RESEARCH"
                seen += 1
            else:
                assert o["quality_status"] == "PARTIAL"
    assert seen
    for s in (d for d in docs.values() if d["kind"] == "time_series" and d["metric_id"] in research_ids):
        assert s["quality"]["status"] == "RESEARCH" and {p["quality_status"] for p in s["points"]} == {"RESEARCH"}
    event = _read(out, "events.json")["items"][0]
    pk = packet.build(app_root=out, scope_kind="GAME", event_id=event["event_id"])
    research_obs = [o for e in pk["evidence"] for o in e["observations"] if o["metric_id"] in research_ids]
    assert research_obs and {o["quality_status"] for o in research_obs} == {"RESEARCH"}
    assert research_ids & set(pk["quality"]["research_only_items"])
    assert pk["quality"]["missing"] == []


# ------------------------------------------------------------------ 8. failure leaves the previous tree alone


def test_a_failing_publish_leaves_the_previous_tree_intact(synthetic, tmp_path):
    out = synthetic["out"]
    before = R.digest_tree(out)
    v1 = rx.load_v1(out)
    research = rx.load_research(RESEARCH_ROOT)
    history = rx.load_catalog_history(synthetic["data_root"], {"26OCT03WKUNMSU"})
    docs = [d for d in rx.build_explorer(v1=v1, research=research, history=history) if d["kind"] != "metric_registry"]
    with pytest.raises(R.ExplorerError):
        R.publish_explorer(
            app_root=out,
            sport="CFB",
            run_id=v1["manifest"]["run_id"],
            generated_at=v1["manifest"]["generated_at"],
            documents=docs,
            quality=R.quality(status="VERIFIED", source="t", generated_at=NOW, production=False),
        )
    assert R.digest_tree(out) == before
    broken = tmp_path / "broken"
    shutil.copytree(RESEARCH_ROOT, broken)
    (broken / "data" / "research_cache" / "v2" / "manifest.json").write_text("{not json", encoding="utf-8")
    assert rx.main(["--out", str(out), "--data-root", str(synthetic["data_root"]), "--research-root", str(broken)]) == 1
    assert R.digest_tree(out) == before
    assert R.verify_explorer(out) == []


# ------------------------------------------------------------------ identity, rankings, series


def test_identity_mapping_is_exact_only():
    teams = {
        1: {"id": 1, "school": "New Mexico State", "abbreviation": "NMSU", "alternateNames": ["NMSU"]},
        2: {"id": 2, "school": "Miami", "abbreviation": "MIA", "alternateNames": ["Miami (FL)", "Miami U"]},
        3: {"id": 3, "school": "Miami (OH)", "abbreviation": "M-OH", "alternateNames": ["Miami U"]},
    }
    tables = {2025: {"teams_fbs": list(teams.values())}}
    parts = {
        "prt_aaaa": {"display_name": "New Mexico St.", "short_name": "NMSU"},  # registry alias
        "prt_bbbb": {"display_name": "Miami (FL)", "short_name": "MIA"},  # CFBD alternate name
        "prt_cccc": {"display_name": "Miami U", "short_name": "MIA"},  # names two CFBD teams -> unmapped
        "prt_dddd": {"display_name": "Montana St.", "short_name": "MTST"},  # FCS -> unmapped
        "prt_eeee": {"display_name": "Miami (OH)", "short_name": "MOH"},
    }  # exact school
    mapping, unmapped = rx.map_participants(parts, rx.cfbd_teams(tables), tables)
    assert mapping["prt_aaaa"]["cfbd_team_id"] == 1 and mapping["prt_aaaa"]["method"] == "registry_alias"
    assert mapping["prt_aaaa"]["abbreviation_matches_kalshi_code"] is True
    assert mapping["prt_bbbb"]["cfbd_team_id"] == 2 and mapping["prt_bbbb"]["method"] == "cfbd_alternate_name"
    assert mapping["prt_eeee"]["cfbd_team_id"] == 3 and mapping["prt_eeee"]["method"] == "cfbd_school"
    assert unmapped == ["prt_cccc", "prt_dddd"]


def test_observation_context_is_read_off_the_published_ranking(real):
    _, docs = R.load_explorer(real["out"])
    rankings = {d["ranking_id"]: d for d in docs.values() if d["kind"] == "ranking"}
    registry = next(d for d in docs.values() if d["kind"] == "metric_registry")
    for m in registry["items"]:
        if m["supports"]["rank"]:
            assert any(r["metric_id"] == m["metric_id"] for r in rankings.values()), m["metric_id"]
    checked = 0
    for prof in (d for d in docs.values() if d["kind"] == "entity_profile"):
        for o in prof["metrics"]:
            if o.get("context"):
                rk = rankings[o["context"]["ranking_id"]]
                assert o["context"] == R.context_from_ranking(rk, prof["entity"]["participant_id"])
                assert rk["universe"]["size"] >= 100  # every FBS team with a value, not the board's teams
                checked += 1
    assert checked
    for s in (d for d in docs.values() if d["kind"] == "time_series"):
        assert 0 < len(s["points"]) <= rx.SERIES_GAME_CAP
        assert all(p["event_id"] is None and p["path"] is None for p in s["points"])  # CFBD games are not v1 events
        assert all(int(p["x"][:4]) <= 2025 for p in s["points"])  # season label: no 2026 in-season point


def test_workflow_runs_the_explorer_after_the_v1_export_without_blocking_it():
    text = (REPO_ROOT / ".github" / "workflows" / "app-export.yml").read_text(encoding="utf-8")
    v1 = text.index("scripts/app_export.py")
    explorer = text.index("scripts/research_export.py")
    assert v1 < explorer
    assert "id: research_export" in text and "continue-on-error: true" in text
    assert "steps.research_export.outcome == 'failure'" in text
    assert "git fetch --depth=1 origin research-data" in text and "git archive origin/research-data" in text
    assert '--research-root "$RUNNER_TEMP/research"' in text
