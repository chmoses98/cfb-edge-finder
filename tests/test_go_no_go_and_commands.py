"""GO/NO-GO verdicts and the new operator commands."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cfb_edge_finder.decision.go_no_go import (
    BlockerCode,
    GoNoGoVerdict,
    WarningCode,
    evaluate_go_no_go,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

CLEAN = dict(
    duplicate_rows=0,
    malformed_rows=0,
    non_prospective_rows=0,
    current_schema_missing_market_status=0,
    invalid_probability_count=0,
    fee_provenance_failures=0,
    safety_locks_ok=True,
    execution_surface_found=False,
    closing_trigger_at_risk=False,
    kalshi_markets_discovered=3594,
    cfbd_reachable=True,
    settled_games=10,
    clv_observations=5,
    unsupported_population_rows=0,
    legacy_schema_rows=0,
    zero_carryover_games=0,
    contextual_sources_missing=0,
    quiet_period_active=False,
)


def evaluate(**overrides):
    return evaluate_go_no_go(**{**CLEAN, **overrides})


# ------------------------------------------------------- verdicts


def test_a_clean_system_is_go_research():
    assert evaluate().verdict is GoNoGoVerdict.GO_RESEARCH


@pytest.mark.parametrize(
    "override,code",
    [
        ({"cfbd_reachable": False}, BlockerCode.CFBD_UNAVAILABLE),
        ({"kalshi_markets_discovered": 3}, BlockerCode.KALSHI_UNIVERSE_COLLAPSED),
        ({"duplicate_rows": 1}, BlockerCode.DUPLICATE_OR_MALFORMED_CORPUS),
        ({"malformed_rows": 1}, BlockerCode.DUPLICATE_OR_MALFORMED_CORPUS),
        ({"non_prospective_rows": 1}, BlockerCode.DUPLICATE_OR_MALFORMED_CORPUS),
        ({"current_schema_missing_market_status": 1}, BlockerCode.CURRENT_SCHEMA_DEFECT),
        ({"invalid_probability_count": 1}, BlockerCode.INVALID_PROBABILITIES),
        ({"fee_provenance_failures": 1}, BlockerCode.FEE_PROVENANCE_UNUSABLE),
        ({"safety_locks_ok": False}, BlockerCode.SAFETY_LOCK_BROKEN),
        ({"execution_surface_found": True}, BlockerCode.EXECUTION_SURFACE_PRESENT),
        ({"closing_trigger_at_risk": True}, BlockerCode.CLOSING_TRIGGER_INSUFFICIENT),
    ],
)
def test_each_blocker_produces_no_go(override, code):
    report = evaluate(**override)
    assert report.verdict is GoNoGoVerdict.NO_GO
    assert code.value in {b.code for b in report.blockers}


@pytest.mark.parametrize(
    "override,code",
    [
        ({"settled_games": 0}, WarningCode.NO_NATURAL_SETTLEMENT_YET),
        ({"clv_observations": 0}, WarningCode.NO_CLV_YET),
        ({"unsupported_population_rows": 400}, WarningCode.UNSUPPORTED_FCS_POPULATION),
        ({"legacy_schema_rows": 1724}, WarningCode.LEGACY_SCHEMA_ROWS_PRESENT),
        ({"zero_carryover_games": 86}, WarningCode.ZERO_CURRENT_SEASON_INFORMATION),
        ({"contextual_sources_missing": 3}, WarningCode.CONTEXTUAL_SOURCE_MISSING),
        ({"quiet_period_active": True}, WarningCode.QUIET_PERIOD_CADENCE),
    ],
)
def test_each_warning_still_permits_research(override, code):
    report = evaluate(**override)
    assert report.verdict is GoNoGoVerdict.GO_RESEARCH_WITH_WARNINGS
    assert code.value in {w.code for w in report.warnings}


def test_no_settled_data_is_a_warning_not_a_blocker():
    """Blocking collection until settled data exists would guarantee it
    never arrives."""
    report = evaluate(settled_games=0, clv_observations=0)
    assert report.verdict is GoNoGoVerdict.GO_RESEARCH_WITH_WARNINGS
    assert report.blockers == []


def test_a_quiet_period_is_a_warning_not_a_blocker():
    """The owner's intentional low cadence must never read as failure."""
    report = evaluate(quiet_period_active=True)
    assert report.verdict is GoNoGoVerdict.GO_RESEARCH_WITH_WARNINGS


def test_an_imminent_closing_the_cadence_cannot_cover_IS_a_blocker():
    """The one trigger condition that does stop the day: an
    unrecoverable window we cannot reach."""
    assert evaluate(closing_trigger_at_risk=True).verdict is GoNoGoVerdict.NO_GO


def test_a_blocker_dominates_warnings():
    report = evaluate(safety_locks_ok=False, settled_games=0, quiet_period_active=True)
    assert report.verdict is GoNoGoVerdict.NO_GO
    assert report.warnings


def test_model_edge_is_not_an_input():
    """Making edge a GO condition would turn an integrity check into a
    trading signal.

    `invalid_probability_count` is deliberately allowed: it counts
    NaN/out-of-range probabilities, which is data integrity, not a view
    on whether the numbers are attractive."""
    import inspect

    parameters = set(inspect.signature(evaluate_go_no_go).parameters)
    assert "invalid_probability_count" in parameters  # integrity, not edge
    for banned in ("edge", "gap", "roi", "profit", "disagreement", "expected_value"):
        assert not any(banned in name for name in parameters), banned
    # No parameter carries a probability VALUE, only counts of broken ones.
    assert not any(
        name.startswith("model_probability") or name.endswith("_probability")
        for name in parameters
    )


def test_the_rendered_verdict_disclaims_profitability():
    text = evaluate().render()
    assert "NOT a" in text and "profitability certification" in text


def test_the_payload_is_json_serialisable():
    json.dumps(evaluate(settled_games=0).to_payload())


def test_verdicts_are_deterministic():
    assert evaluate(settled_games=0).to_payload() == evaluate(settled_games=0).to_payload()


# ------------------------------------------------------- commands


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    for name in ("observations", "settlements", "heartbeats", "attributions"):
        (tmp_path / "data" / "research" / name).mkdir(parents=True)
        (tmp_path / "data" / "research" / name / "2026.jsonl").write_text("", encoding="utf-8")
    return tmp_path


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / script), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


COMMANDS = ["run_cfb.py", "postgame_research_report.py", "trigger_budget_report.py"]

# postgame takes --date rather than --now; each command is invoked with
# the clock argument it actually accepts.
CLOCK_ARGS = {
    "run_cfb.py": ("--now", "2026-08-28T13:00:00+00:00"),
    "trigger_budget_report.py": ("--now", "2026-08-28T13:00:00+00:00"),
    "postgame_research_report.py": ("--date", "2026-08-29"),
}


@pytest.mark.parametrize("script", COMMANDS)
def test_each_command_runs_on_an_empty_corpus(script, data_dir):
    """Useful with zero settled games is a requirement, not a nicety."""
    result = run(script, "--data-repo-dir", str(data_dir), *CLOCK_ARGS[script])
    assert result.returncode in (0, 1), result.stderr
    assert result.stdout.strip()


@pytest.mark.parametrize("script", COMMANDS)
def test_no_command_emits_betting_card_framing(script, data_dir):
    from cfb_edge_finder.decision.report import BANNED_OUTPUT_VOCABULARY

    result = run(script, "--data-repo-dir", str(data_dir), *CLOCK_ARGS[script])
    lowered = result.stdout.lower()
    for phrase in BANNED_OUTPUT_VOCABULARY:
        assert phrase not in lowered


@pytest.mark.parametrize("script", COMMANDS)
def test_no_command_writes_to_the_data_directory(script, data_dir):
    before = {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file()}
    run(script, "--data-repo-dir", str(data_dir), *CLOCK_ARGS[script])
    after = {p: p.read_bytes() for p in data_dir.rglob("*") if p.is_file()}
    assert before == after


def test_run_cfb_reports_zero_shadow_qualified(data_dir):
    result = run("run_cfb.py", "--data-repo-dir", str(data_dir), "--now", "2026-08-28T13:00:00+00:00")
    assert "shadow qualified (counted)     : 0" in result.stdout


def test_run_cfb_declares_the_preregistered_protocol(data_dir):
    result = run("run_cfb.py", "--data-repo-dir", str(data_dir), "--now", "2026-08-28T13:00:00+00:00")
    assert "prospective_research_protocol_v1" in result.stdout


def test_postgame_handles_zero_settlements_gracefully(data_dir):
    result = run(
        "postgame_research_report.py", "--data-repo-dir", str(data_dir), "--date", "2026-08-29"
    )
    assert result.returncode == 0
    assert "No games have settled yet" in result.stdout
    assert "nothing may be inferred from nothing" in result.stdout


def test_trigger_report_never_claims_a_configured_external_cadence():
    """The module may DISCUSS cron-job.org's configuration in order to
    disclaim knowing it -- what it must not do is read, infer, or require
    credentials for it."""
    import ast

    path = REPO_ROOT / "scripts" / "trigger_budget_report.py"
    src = path.read_text()
    assert "MEASURED" in src

    tree = ast.parse(src)
    code_strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    # Outside docstrings, nothing references the external provider at all.
    for value in code_strings:
        if value in docstrings:
            continue
        assert "cron-job" not in value.lower()
    for banned in ("CRONJOB_API_KEY", "cron-job.org/api", "requests.get"):
        assert banned not in src


def test_trigger_report_documents_the_policy_as_temporary():
    src = (REPO_ROOT / "scripts" / "trigger_budget_report.py").read_text()
    assert "TEMPORARY" in src.upper()
    assert "not the final automated architecture" in src


def test_run_cfb_writes_a_machine_readable_payload(data_dir, tmp_path):
    out = tmp_path / "run.json"
    run(
        "run_cfb.py", "--data-repo-dir", str(data_dir),
        "--now", "2026-08-28T13:00:00+00:00", "--json-out", str(out),
    )
    payload = json.loads(out.read_text())
    assert payload["research_state"]["shadow_qualified_count"] == 0
    assert payload["safety"]["execution_present"] is False
    assert payload["go_no_go"]["verdict"] in {v.value for v in GoNoGoVerdict}
