"""End-to-end tests for the two new read-only CLI entry points.

They run as subprocesses against a temporary data directory, which is
also how they prove the scripts write nothing to the real corpus.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
OPS = REPO_ROOT / "scripts" / "week1_ops_health.py"
REPORT = REPO_ROOT / "scripts" / "build_research_decision_report.py"

OBSERVATION = {
    "observation_key": "k1",
    "schema_version": "research_corpus_v2",
    "capture_mode": "PROSPECTIVE",
    "season": 2026,
    "observation": {
        "kalshi_market_ticker": "KXNCAAFGAME-26SEP05AAABBB-AAA",
        "game_id": "g1",
        "family": "moneyline",
        "team": "home",
        "threshold": None,
        "semantic_operator": ">",
        "parse_status": "confirmed_live",
        "snapshot_timing": {"label": "T_24H", "hours_before_kickoff": 24.0},
        "captured_at": "2026-09-04T18:00:00+00:00",
        "model_probability": 0.62,
        "executable_yes_price": 0.50,
        "executable_no_price": 0.52,
        "market_status": "active",
        "fee_status": "VERIFIED_CURRENT",
        "fee_schedule_version": "kalshi_fee_schedule_2026_07_07_taker",
        "model_version": {"model_version": "m1"},
        "pricing_status": "model_priced",
    },
}


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    observations = tmp_path / "data" / "research" / "observations"
    settlements = tmp_path / "data" / "research" / "settlements"
    heartbeats = tmp_path / "data" / "research" / "heartbeats"
    for directory in (observations, settlements, heartbeats):
        directory.mkdir(parents=True)
    (observations / "2026.jsonl").write_text(json.dumps(OBSERVATION) + "\n", encoding="utf-8")
    (settlements / "2026.jsonl").write_text("", encoding="utf-8")
    (heartbeats / "2026.jsonl").write_text("", encoding="utf-8")
    return tmp_path


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, cwd=REPO_ROOT
    )


# ------------------------------------------------------ ops health


def test_ops_health_runs_and_reports_a_state(data_dir):
    result = run(OPS, "--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00")
    assert "OVERALL:" in result.stdout
    assert "read-only" in result.stdout


def test_ops_health_blocks_when_nothing_has_ever_run(data_dir):
    """No heartbeats at all: collection has never run, and the exit code
    must make that impossible to miss in a scheduled job. Note this is
    genuine failure, distinct from an intentionally wide quiet-period
    interval, which is HEALTHY -- see tests/test_collection_protection.py."""
    result = run(OPS, "--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00")
    assert result.returncode == 1
    assert "OVERALL: BLOCKED" in result.stdout


def test_ops_health_reports_zero_settled_games_for_pending_settlements(data_dir):
    """A settlement row exists for every market looked at, including
    games that have not kicked off. Only `status == settled` counts."""
    settlements = data_dir / "data" / "research" / "settlements" / "2026.jsonl"
    settlements.write_text(
        "\n".join(
            json.dumps({"game_id": f"g{i}", "status": "pending_not_final"}) for i in range(50)
        )
        + "\n",
        encoding="utf-8",
    )
    result = run(OPS, "--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00")
    assert "games with status=settled: 0" in result.stdout
    assert "EMPIRICAL THRESHOLD RESEARCH BLOCKED ON NATURAL SAMPLE SIZE" in result.stdout


def test_ops_health_counts_genuinely_settled_games(data_dir):
    settlements = data_dir / "data" / "research" / "settlements" / "2026.jsonl"
    settlements.write_text(
        json.dumps({"game_id": "g1", "status": "settled"})
        + "\n"
        + json.dumps({"game_id": "g2", "status": "pending_not_final"})
        + "\n",
        encoding="utf-8",
    )
    result = run(OPS, "--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00")
    assert "games with status=settled: 1" in result.stdout


def test_ops_health_writes_a_machine_readable_payload(data_dir, tmp_path):
    out = tmp_path / "ops.json"
    run(OPS, "--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00", "--json-out", str(out))
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["overall_state"] in {"HEALTHY", "WARN", "BLOCKED", "PENDING_NATURAL_DATA"}
    assert {c["check_id"] for c in payload["checks"]} >= {
        "collection_protection",
        "corpus_integrity",
        "closing_coverage",
        "safety_locks",
        "natural_data",
    }


def test_ops_health_confirms_the_safety_locks_hold(data_dir):
    result = run(OPS, "--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00")
    assert "Qualification disabled, no approved artifact" in result.stdout
    assert "sizing disconnected" in result.stdout


def test_ops_health_writes_nothing_to_the_data_directory(data_dir):
    before = {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file()}
    run(OPS, "--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00")
    after = {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file()}
    assert before == after


# ------------------------------------------------- decision report


def test_decision_report_runs_and_reports_zero_qualified(data_dir):
    result = run(REPORT, "--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00")
    assert result.returncode == 0
    assert "RESEARCH DECISION REPORT" in result.stdout
    assert "SHADOW_QUALIFIED       : 0" in result.stdout


def test_decision_report_contains_no_betting_card_framing(data_dir):
    from cfb_edge_finder.decision.report import BANNED_OUTPUT_VOCABULARY

    result = run(REPORT, "--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00")
    lowered = result.stdout.lower()
    for phrase in BANNED_OUTPUT_VOCABULARY:
        assert phrase not in lowered


def test_decision_report_is_byte_identical_across_runs(data_dir):
    args = ("--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00")
    assert run(REPORT, *args).stdout == run(REPORT, *args).stdout


def test_decision_report_refuses_an_unapproved_artifact(data_dir, tmp_path):
    """Pointing the CLI at a draft artifact must not activate it."""
    from tests.test_decision_artifact import artifact_dict

    artifact = tmp_path / "draft.json"
    artifact.write_text(json.dumps(artifact_dict(approval_state="DRAFT_RESEARCH")), encoding="utf-8")
    result = run(
        REPORT,
        "--data-repo-dir",
        str(data_dir),
        "--threshold-artifact",
        str(artifact),
        "--now",
        "2026-09-06T09:00:00+00:00",
    )
    assert "THRESHOLD_ARTIFACT_NOT_APPROVED" in result.stdout
    assert "SHADOW_QUALIFIED       : 0" in result.stdout


def test_decision_report_writes_nothing_to_the_data_directory(data_dir):
    before = {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file()}
    run(REPORT, "--data-repo-dir", str(data_dir), "--now", "2026-09-06T09:00:00+00:00")
    after = {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file()}
    assert before == after


def test_decision_report_payload_matches_the_printed_counts(data_dir, tmp_path):
    out = tmp_path / "report.json"
    result = run(
        REPORT,
        "--data-repo-dir",
        str(data_dir),
        "--now",
        "2026-09-06T09:00:00+00:00",
        "--json-out",
        str(out),
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["shadow_qualified_count"] == 0
    assert payload["threshold_artifact_status"] == "NO_VALIDATED_THRESHOLD_SET"
    assert f"candidates_considered  : {payload['candidates_considered']}" in result.stdout
