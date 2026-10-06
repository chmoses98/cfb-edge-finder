"""No automatic path may relight a HIBERNATED research workflow.

*** THE INCIDENT THIS ENCODES ***

The 2026-09-17 market-discovery pivot hibernated research-capture.yml and
research-collection-conductor.yml by commenting out their cron. The
existing guards checked exactly that and nothing else -- and on 2026-09-25
an independent cron-job.org job was still dispatching Research Capture
every five minutes, every run failing on CFBD_QUOTA_EXHAUSTED /
DEADLINE_AT_RISK, one failure email per run.

"No active cron" is one trigger. Hibernation means NO unattended caller:
not cron, not push, not workflow_run chaining, not repository_dispatch, not
a reusable-workflow call, not another workflow or script dispatching it,
and -- because the external scheduler lives outside the repository where
CI cannot see it -- a run-time gate that refuses such a dispatch anyway.

The registry of what is hibernated is src/cfb_edge_finder/research/
hibernation.py. Reactivating a workflow is a deliberate change there, and
these tests are meant to fail until that change is made on purpose.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cfb_edge_finder.research import hibernation
from cfb_edge_finder.research.hibernation import (
    ACTIVE,
    HIBERNATED,
    WORKFLOW_LIFECYCLE,
    decide,
    hibernated_workflows,
    is_hibernated,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

MANUAL_ONLY = frozenset({"workflow_dispatch"})

GATED = ("research-capture.yml", "research-collection-conductor.yml")
"""Hibernated workflows that an UNATTENDED DISPATCH can reach -- the
external scheduler and the conductor chain -- and so must carry the
run-time gate, not only the static trigger guard."""

DISPATCHER_ALLOWLIST = {"scripts/collection_conductor.py"}
"""Code that may call GitHub's workflow-dispatch API. It is itself only
runnable from a hibernated workflow (asserted below)."""

RESEARCH_ENTRYPOINTS = ("research_scan_and_capture.py", "collection_conductor.py")


# --- a tiny `on:` parser -------------------------------------------------
#
# CI installs only `.[dev]` (pytest, ruff), so there is no YAML library.
# The trigger block of a workflow is regular enough to read by indentation,
# and the parser below has its own tests, so the guard is proven to bite
# rather than assumed to.


def _is_code(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def triggers(text: str) -> set[str]:
    """Every event key under the workflow's top-level `on:`, ignoring
    commented-out lines -- a commented `schedule:` is exactly what a
    hibernated workflow is supposed to look like."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"""^(?:on|"on"|'on'|true)\s*:\s*(.*?)\s*(?:#.*)?$""", line)
        if not match:
            continue
        inline = match.group(1)
        if inline:
            return {t.strip().strip("'\"") for t in inline.strip("[]").split(",") if t.strip()}
        found: set[str] = set()
        indent: int | None = None
        for body in lines[index + 1:]:
            if not _is_code(body):
                continue
            width = len(body) - len(body.lstrip())
            if width == 0:
                break
            if indent is None:
                indent = width
            if width == indent:
                key = re.match(r"^\s*([A-Za-z_]+)\s*:", body)
                if key:
                    found.add(key.group(1))
        return found
    return set()


def automatic_triggers(text: str) -> set[str]:
    return triggers(text) - MANUAL_ONLY


def _code_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if _is_code(line))


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _display_name(text: str) -> str:
    match = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
    assert match, "workflow has no name:"
    return match.group(1).strip().strip("'\"")


def _all_workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.y*ml"))


# --- the parser bites ----------------------------------------------------


@pytest.mark.parametrize(
    "snippet, expected",
    [
        ('on:\n  schedule:\n    - cron: "*/5 * * * *"\n  workflow_dispatch:\n', {"schedule"}),
        ("on:\n  push:\n    branches: [main]\n  workflow_dispatch:\n", {"push"}),
        ("on:\n  workflow_run:\n    workflows: [CI]\n    types: [completed]\n", {"workflow_run"}),
        ("on:\n  repository_dispatch:\n    types: [relight]\n", {"repository_dispatch"}),
        ("on:\n  workflow_call:\n  workflow_dispatch:\n", {"workflow_call"}),
        ("on: [push, workflow_dispatch]\n", {"push"}),
        ("on: schedule\n", {"schedule"}),
        ('"on":\n  pull_request:\n', {"pull_request"}),
    ],
)
def test_the_parser_catches_every_automatic_trigger_shape(snippet, expected):
    assert automatic_triggers("name: x\n" + snippet + "jobs:\n  a:\n") == expected


def test_the_parser_ignores_a_commented_out_schedule():
    text = (
        "name: x\non:\n#   schedule:\n#     - cron: \"*/10 * * * *\"\n"
        "  # a comment at the trigger indent\n  workflow_dispatch:\n    inputs:\n"
        "      schedule_season:\n        default: \"2026\"\njobs:\n  a:\n"
    )
    assert triggers(text) == {"workflow_dispatch"}


# --- the registry --------------------------------------------------------


def test_the_hibernation_registry_names_real_workflows():
    for name in WORKFLOW_LIFECYCLE:
        assert (WORKFLOWS / name).is_file(), f"hibernation registry names a missing workflow: {name}"
    assert set(WORKFLOW_LIFECYCLE.values()) <= {ACTIVE, HIBERNATED}


def test_the_retired_collector_and_conductor_are_hibernated():
    """Reactivation is an explicit operator decision. If this fails, the
    change that made it fail is the reactivation -- confirm it was meant,
    that the CFBD quota/credential and football-state checks in
    docs/EXTERNAL_SCHEDULER.md were done, and update this test with it."""
    for name in GATED:
        assert is_hibernated(name), f"{name} is no longer HIBERNATED -- was that deliberate?"


def test_the_live_catalog_is_not_hibernated():
    """The live path must never be caught by the gate or the guard."""
    for name in ("kalshi-market-catalog.yml", "cfb-execution-slate.yml", "ci.yml"):
        assert not is_hibernated(name)
        assert name not in WORKFLOW_LIFECYCLE


# --- static trigger guard ------------------------------------------------


@pytest.mark.parametrize("name", hibernated_workflows())
def test_a_hibernated_workflow_has_no_automatic_trigger(name):
    extra = automatic_triggers(_workflow(name))
    assert not extra, (
        f"{name} is HIBERNATED but declares automatic trigger(s) {sorted(extra)}. "
        f"Only workflow_dispatch is allowed; to reactivate, set it ACTIVE in "
        f"src/cfb_edge_finder/research/hibernation.py deliberately."
    )


@pytest.mark.parametrize("name", hibernated_workflows())
def test_a_hibernated_workflow_is_still_manually_runnable(name):
    """Hibernated, not deleted: a human can run it today."""
    assert "workflow_dispatch" in triggers(_workflow(name))


@pytest.mark.parametrize("name", hibernated_workflows())
def test_a_hibernated_schedule_stays_recorded_for_revival(name):
    text = _workflow(name)
    assert re.search(r"^#\s*schedule:", text, re.MULTILINE), f"{name} lost its commented schedule"
    assert re.search(r'^#\s*-\s*cron:\s*"', text, re.MULTILINE), f"{name} lost its commented cron"


@pytest.mark.parametrize("target", hibernated_workflows())
def test_no_other_workflow_can_dispatch_or_chain_a_hibernated_one(target):
    """Covers `uses: ./.github/workflows/<file>` (reusable call),
    `gh workflow run <file|name>`, the REST dispatch URL, and a
    `workflow_run` trigger naming it. Comments are excluded -- the live
    catalog's header legitimately talks about the retired collector."""
    display = _display_name(_workflow(target))
    offenders = []
    for path in _all_workflows():
        if path.name == target:
            continue
        code = _code_lines(path.read_text(encoding="utf-8"))
        if target in code or display in code:
            offenders.append(path.name)
    assert not offenders, f"{offenders} reference HIBERNATED {target} ({display!r}) outside comments"


def test_only_the_allowlisted_code_calls_the_dispatch_api():
    offenders = []
    for root in ("src", "scripts"):
        for path in sorted((REPO_ROOT / root).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"/dispatches\b|createWorkflowDispatch|repository_dispatch", text):
                relative = path.relative_to(REPO_ROOT).as_posix()
                if relative not in DISPATCHER_ALLOWLIST and relative != "src/cfb_edge_finder/research/trigger.py":
                    offenders.append(relative)
    assert not offenders, f"unexpected workflow-dispatch callers: {offenders}"


def test_research_entrypoints_never_run_unattended():
    """If an automatically-triggered workflow ran the collector or the
    conductor script, it would relight the research system without
    touching either hibernated workflow at all.

    Manual-only workflows are allowed to: the capture-resilience and
    settlement-source probes run the scanner, but only when a human
    dispatches them, which is exactly the recoverability this keeps."""
    offenders = []
    for path in _all_workflows():
        text = path.read_text(encoding="utf-8")
        if is_hibernated(path.name) or not automatic_triggers(text):
            continue
        code = _code_lines(text)
        for entry in RESEARCH_ENTRYPOINTS:
            if entry in code:
                offenders.append(f"{path.name} runs {entry}")
    assert not offenders, offenders


# --- run-time gate -------------------------------------------------------


def _jobs(text: str) -> dict[str, str]:
    """Job id -> its body text, by indentation under `jobs:`."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("jobs:"))
    jobs: dict[str, list[str]] = {}
    current = None
    for line in lines[start + 1:]:
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            current = match.group(1)
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return {k: "\n".join(v) for k, v in jobs.items()}


@pytest.mark.parametrize("name", GATED)
def test_gated_workflows_run_the_gate_first_and_do_nothing_without_it(name):
    jobs = _jobs(_workflow(name))
    assert "hibernation-gate" in jobs, f"{name} has no hibernation-gate job"
    gate = jobs["hibernation-gate"]
    assert "python3 scripts/hibernation_gate.py" in gate
    assert f"GATE_WORKFLOW: {name}" in gate, "the gate must judge THIS workflow"
    assert "pip install" not in gate, "the gate must be cheap: nothing installed, no CFBD call"
    assert "secrets." not in gate, "the gate needs no secret and must not be handed one"
    for job_id, body in jobs.items():
        if job_id == "hibernation-gate":
            continue
        assert "needs: hibernation-gate" in body, f"{name}:{job_id} does not wait for the gate"
        assert "needs.hibernation-gate.outputs.proceed == 'true'" in body, (
            f"{name}:{job_id} runs whatever the gate decided"
        )


def test_the_collector_gate_forwards_the_declared_trigger_source():
    gate = _jobs(_workflow("research-capture.yml"))["hibernation-gate"]
    assert "GATE_TRIGGER_SOURCE: ${{ github.event.inputs.trigger_source }}" in gate
    assert "GATE_ACTOR: ${{ github.actor }}" in gate
    assert "GATE_EVENT_NAME: ${{ github.event_name }}" in gate


def test_a_refused_collector_run_never_takes_the_writer_lock():
    """The live catalog holds research-data-write too. A refused dispatch
    must not queue in that group, or displace a pending catalog run."""
    text = _workflow("research-capture.yml")
    top_level = re.search(r"^concurrency:", text, re.MULTILINE)
    assert top_level is None, "workflow-level concurrency puts the gate inside the writers' group"
    writer = _jobs(text)["scan-and-capture"]
    assert "group: research-data-write" in writer
    assert "cancel-in-progress: false" in writer
    assert "group: research-data-write" not in _jobs(text)["hibernation-gate"]


# --- gate decisions ------------------------------------------------------


CAPTURE = "research-capture.yml"
CONDUCTOR = "research-collection-conductor.yml"


@pytest.mark.parametrize(
    "kwargs",
    [
        # The 2026-09-25 storm, exactly as cron-job.org sent it.
        dict(workflow_file=CAPTURE, event_name="workflow_dispatch", actor="chmoses98",
             trigger_source="EXTERNAL_SCHEDULE"),
        dict(workflow_file=CAPTURE, event_name="workflow_dispatch", actor="chmoses98",
             trigger_source=" external_schedule "),
        # A conductor-dispatched collector run.
        dict(workflow_file=CAPTURE, event_name="workflow_dispatch", actor="github-actions[bot]"),
        # Cron uncommented without flipping the registry.
        dict(workflow_file=CAPTURE, event_name="schedule", actor="chmoses98"),
        dict(workflow_file=CAPTURE, event_name="repository_dispatch", actor="chmoses98"),
        dict(workflow_file=CAPTURE, event_name="workflow_run", actor="chmoses98"),
        dict(workflow_file=CAPTURE, event_name=None, actor=None),
        # A successor conductor.
        dict(workflow_file=CONDUCTOR, event_name="workflow_dispatch", actor="github-actions[bot]"),
        # A human starting a LIVE conductor = starting unattended collection.
        dict(workflow_file=CONDUCTOR, event_name="workflow_dispatch", actor="chmoses98", dry_run=False),
        dict(workflow_file=CONDUCTOR, event_name="schedule", actor="chmoses98", dry_run=True),
        # dry_run cannot launder a bot/successor run.
        dict(workflow_file=CONDUCTOR, event_name="workflow_dispatch", actor="github-actions[bot]",
             dry_run=True),
    ],
)
def test_unattended_runs_of_a_hibernated_workflow_are_refused(kwargs):
    decision = decide(**kwargs)
    assert decision.proceed is False
    assert HIBERNATED in decision.reason


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(workflow_file=CAPTURE, event_name="workflow_dispatch", actor="chmoses98"),
        dict(workflow_file=CAPTURE, event_name="workflow_dispatch", actor="chmoses98", trigger_source=""),
        dict(workflow_file=CAPTURE, event_name="workflow_dispatch", actor="chmoses98",
             trigger_source="MANUAL"),
        dict(workflow_file=CONDUCTOR, event_name="workflow_dispatch", actor="chmoses98", dry_run=True),
    ],
)
def test_a_human_can_still_run_a_hibernated_workflow(kwargs):
    assert decide(**kwargs).proceed is True


def test_an_active_workflow_is_never_gated(monkeypatch):
    monkeypatch.setitem(hibernation.WORKFLOW_LIFECYCLE, CAPTURE, ACTIVE)
    for source in ("EXTERNAL_SCHEDULE", None):
        assert decide(workflow_file=CAPTURE, event_name="workflow_dispatch", actor="x",
                      trigger_source=source).proceed
    assert decide(workflow_file=CAPTURE, event_name="schedule", actor="x").proceed
    assert decide(workflow_file="kalshi-market-catalog.yml", event_name="schedule", actor="x").proceed


def _run_gate(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess, str, str]:
    output = tmp_path / "out"
    summary = tmp_path / "summary"
    full = {**os.environ, "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary), **env}
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "hibernation_gate.py")],
        env=full, capture_output=True, text=True, timeout=60,
    )
    return result, output.read_text(encoding="utf-8"), summary.read_text(encoding="utf-8")


def test_gate_script_refuses_the_external_scheduler_without_failing_the_run(tmp_path):
    """Exit 0 on refusal is deliberate -- a red run here would recreate the
    five-minute failure-email storm. It is not silent: a ::warning:: and a
    job-summary line mark every refused dispatch."""
    result, output, summary = _run_gate(
        tmp_path, GATE_WORKFLOW=CAPTURE, GATE_EVENT_NAME="workflow_dispatch",
        GATE_ACTOR="chmoses98", GATE_TRIGGER_SOURCE="EXTERNAL_SCHEDULE",
    )
    assert result.returncode == 0, result.stderr
    assert output.strip() == "proceed=false"
    assert "::warning" in result.stdout
    assert "REFUSED" in summary


def test_gate_script_lets_a_human_through(tmp_path):
    result, output, _ = _run_gate(
        tmp_path, GATE_WORKFLOW=CAPTURE, GATE_EVENT_NAME="workflow_dispatch",
        GATE_ACTOR="chmoses98", GATE_TRIGGER_SOURCE="",
    )
    assert result.returncode == 0, result.stderr
    assert output.strip() == "proceed=true"
    assert "::warning" not in result.stdout


def test_gate_script_honours_the_conductor_dry_run(tmp_path):
    base = dict(GATE_WORKFLOW=CONDUCTOR, GATE_EVENT_NAME="workflow_dispatch", GATE_ACTOR="chmoses98")
    _, live, _ = _run_gate(tmp_path, **base, GATE_DRY_RUN="false")
    assert live.strip() == "proceed=false"
    (tmp_path / "out").unlink()
    (tmp_path / "summary").unlink()
    _, dry, _ = _run_gate(tmp_path, **base, GATE_DRY_RUN="true")
    assert dry.strip() == "proceed=true"


# --- monitoring: hibernated is not an outage -----------------------------


def _readiness():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import week1_readiness  # type: ignore[import-not-found]

    return week1_readiness


def test_readiness_does_not_report_a_hibernated_collector_as_an_outage(tmp_path):
    readiness = _readiness()
    findings = readiness.Findings()
    readiness.section_trigger(tmp_path, 2026, [], findings, datetime.now(UTC), hibernated=True)
    severities = {s for s, _, _ in findings.items}
    assert not severities & {readiness.BLOCKER, readiness.HIGH, readiness.MEDIUM}, findings.render()
    assert any(code == "collector_hibernated" for _, code, _ in findings.items)


def test_readiness_still_alarms_on_the_same_evidence_when_active(tmp_path):
    """Same empty heartbeat ledger, collector ACTIVE: the original HIGH
    alarm must fire unchanged -- hibernation withholds severity, it does
    not weaken the check."""
    readiness = _readiness()
    findings = readiness.Findings()
    readiness.section_trigger(tmp_path, 2026, [], findings, datetime.now(UTC), hibernated=False)
    assert readiness.HIGH in {s for s, _, _ in findings.items}, findings.render()


def test_ops_health_does_not_block_on_a_hibernated_collector():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import week1_ops_health  # type: ignore[import-not-found]

    from cfb_edge_finder.decision.ops_health import OpsState

    now = datetime.now(UTC)
    off = week1_ops_health.protection_check([], now, hibernated=True)
    on = week1_ops_health.protection_check([], now, hibernated=False)
    assert off.state is OpsState.HEALTHY and "HIBERNATED" in off.detail
    assert on.state is not OpsState.HEALTHY, "the active-collector alarm must be unchanged"


# --- the live path is independent ---------------------------------------


def test_the_live_catalog_is_scheduled_keyless_and_independent_of_research():
    text = _workflow("kalshi-market-catalog.yml")
    code = _code_lines(text)
    assert "schedule" in automatic_triggers(text), "the live catalog lost its schedule"
    assert "secrets." not in code, "the live catalog must consume no secret"
    assert "hibernation_gate" not in code
    for entry in RESEARCH_ENTRYPOINTS + GATED:
        assert entry not in code


# --- documentation -------------------------------------------------------


def test_the_external_scheduler_doc_says_it_is_hibernated():
    doc = (REPO_ROOT / "docs" / "EXTERNAL_SCHEDULER.md").read_text(encoding="utf-8")
    head = doc[: doc.index("## Historical record")]
    assert "HIBERNATED" in head
    assert "must NOT be enabled" in head
    assert "explicit operator decision" in head
    assert "CFBD" in head and "football-state freshness" in head.lower()
