"""Mission section 15: two scheduled research runs must never race on the
append-only corpus.

The protection is two-layered and BOTH layers already existed before this
performance work (see .github/workflows/research-capture.yml and
research/git_durable_store.py). What did not exist was anything stopping a
FOURTH writer workflow being added later without the concurrency group --
which is exactly the kind of omission that only shows up as a corrupted
corpus months later. These tests read the workflow YAML directly and fail
if a workflow that can write to the durable store is missing the guard.

Deliberately parsed with a narrow line scan rather than a YAML dependency:
this repo's runtime deps are pydantic/numpy only, and adding PyYAML just
to lint three files would be a worse trade than a 20-line reader.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"

EXPECTED_GROUP = "research-data-write"


def _durable_store_writers() -> list[Path]:
    """Every workflow that grants `contents: write` AND runs a script that
    pushes to the durable-store branch."""
    writers = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        grants_write = re.search(r"^\s+contents:\s*write\s*$", text, re.MULTILINE) is not None
        if grants_write:
            writers.append(path)
    return writers


def test_there_is_at_least_one_durable_store_writer():
    """Guard against this whole module passing vacuously if the workflow
    layout is ever restructured."""
    assert _durable_store_writers(), "found no contents:write workflows -- detection logic is stale"


@pytest.mark.parametrize("workflow", _durable_store_writers(), ids=lambda p: p.name)
def test_every_writing_workflow_serializes_on_one_concurrency_group(workflow: Path):
    """All writers must share ONE group name. Per-workflow groups would
    serialize each workflow against itself but still let a capture run and
    a settlement run interleave on the same branch."""
    text = workflow.read_text(encoding="utf-8")
    match = re.search(r"^concurrency:\s*\n\s+group:\s*(\S+)", text, re.MULTILINE)
    assert match is not None, f"{workflow.name} can write the durable store but declares no concurrency group"
    assert match.group(1) == EXPECTED_GROUP, (
        f"{workflow.name} uses concurrency group {match.group(1)!r}; "
        f"every durable-store writer must share {EXPECTED_GROUP!r} or they can still race each other"
    )


@pytest.mark.parametrize("workflow", _durable_store_writers(), ids=lambda p: p.name)
def test_writing_workflows_never_cancel_an_in_flight_write(workflow: Path):
    """`cancel-in-progress: true` would kill a run midway through its
    commit/push retry loop. Queuing is the only safe setting for a writer."""
    text = workflow.read_text(encoding="utf-8")
    match = re.search(r"^concurrency:\s*\n(?:\s+\S.*\n)*?\s+cancel-in-progress:\s*(\S+)", text, re.MULTILINE)
    assert match is not None, f"{workflow.name} does not state cancel-in-progress explicitly"
    assert match.group(1) == "false", (
        f"{workflow.name} sets cancel-in-progress: {match.group(1)} -- "
        "an interrupted writer can leave the durable-store push half-done"
    )


def test_capture_workflow_is_the_scanner_and_is_scheduled():
    """Pins the assumption the two tests above rest on: the scanner really
    is one of the guarded scheduled writers."""
    capture = WORKFLOWS / "research-capture.yml"
    text = capture.read_text(encoding="utf-8")
    assert "research_scan_and_capture.py" in text
    assert re.search(r"^\s+-\s*cron:", text, re.MULTILINE), "capture workflow is no longer scheduled"
    assert capture in _durable_store_writers()
