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
DURABLE_BRANCH = "research-data"


def _concurrency_group(text: str) -> str | None:
    """The declared group, whether concurrency sits at the top level (the
    collector) or inside the job (the sidecars)."""
    match = re.search(r"^\s*concurrency:\s*\n\s+group:\s*(\S+)", text, re.MULTILINE)
    return match.group(1) if match else None


def _cancel_in_progress(text: str) -> str | None:
    match = re.search(
        r"^\s*concurrency:\s*\n(?:\s+\S.*\n)*?\s+cancel-in-progress:\s*(\S+)", text, re.MULTILINE
    )
    return match.group(1) if match else None


def _own_orphan_branch(text: str) -> str | None:
    """The isolated branch this workflow pushes to, if it pushes to one
    that is NOT the durable store.

    Detection is deliberately asymmetric and conservative. The collector
    reaches `research-data` through `git_durable_store` in Python, not
    through a literal push line in the YAML, so "grants contents: write"
    remains the default classification -- an unrecognised writer is
    treated as a durable-store writer and must share the group. Only a
    workflow that demonstrably pushes `HEAD:<other-branch>` is carved out
    as an isolated sidecar.

    `contents: write` was the original proxy and it was right while every
    writer targeted one branch. It stopped being right once workflows
    began writing their own ISOLATED orphan branches (the live-info
    sidecar), which legitimately must NOT share the collector's group: a
    previous mission's weather job did exactly that and cancelled 12
    queued collector runs."""
    for match in re.finditer(r"HEAD:([A-Za-z0-9._/-]+)", text):
        branch = match.group(1)
        if branch != DURABLE_BRANCH:
            return branch
    return None


def _grants_write(text: str) -> bool:
    return re.search(r"^\s+contents:\s*write\s*$", text, re.MULTILINE) is not None


# Writers that commit ONLY to the ref they run on, ONLY under one path no
# other workflow stages, through a rebase-retry push, and never reach the
# research-data durable store. Such a writer cannot race anyone, so it gets
# its OWN group: GitHub keeps one pending run per group, and inside the
# shared group the Kalshi catalog's queued runs were replaced by App Export
# and Script Engine runs before they started (3 of 9 on 2026-10-17, all with
# zero jobs). Every property that makes the carve-out safe is pinned by the
# MAIN-ONLY WRITERS tests at the bottom of this module.
MAIN_ONLY_WRITERS = {"kalshi-market-catalog.yml": "data/live"}


def _durable_store_writers() -> list[Path]:
    """Every writing workflow that is NOT an isolated-orphan-branch
    sidecar and NOT a pinned main-only writer -- i.e. anything that can
    reach the durable store."""
    return [
        path
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if _grants_write(text := path.read_text(encoding="utf-8"))
        and _own_orphan_branch(text) is None
        and path.name not in MAIN_ONLY_WRITERS
    ]


def _other_branch_writers() -> list[Path]:
    """Workflows that write their OWN isolated orphan branch."""
    return [
        path
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if _grants_write(text := path.read_text(encoding="utf-8")) and _own_orphan_branch(text) is not None
    ]


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
    group = _concurrency_group(text)
    assert group is not None, f"{workflow.name} can write the durable store but declares no concurrency group"
    assert group == EXPECTED_GROUP, (
        f"{workflow.name} uses concurrency group {group!r}; "
        f"every durable-store writer must share {EXPECTED_GROUP!r} or they can still race each other"
    )


@pytest.mark.parametrize("workflow", _durable_store_writers(), ids=lambda p: p.name)
def test_writing_workflows_never_cancel_an_in_flight_write(workflow: Path):
    """`cancel-in-progress: true` would kill a run midway through its
    commit/push retry loop. Queuing is the only safe setting for a writer."""
    text = workflow.read_text(encoding="utf-8")
    value = _cancel_in_progress(text)
    assert value is not None, f"{workflow.name} does not state cancel-in-progress explicitly"
    assert value == "false", (
        f"{workflow.name} sets cancel-in-progress: {value} -- "
        "an interrupted writer can leave the durable-store push half-done"
    )


@pytest.mark.parametrize("workflow", _other_branch_writers(), ids=lambda p: p.name)
def test_isolated_sidecars_never_squat_on_the_collectors_group(workflow: Path):
    """A workflow that writes its OWN orphan branch must declare its own
    concurrency group.

    GitHub keeps a single pending run per group, so a long-running sidecar
    sharing `research-data-write` would cancel queued COLLECTOR runs while
    it held the group. That is not hypothetical: on 2026-09-02 a weather
    fetch did exactly that and cost 12 collector runs. Isolation is the
    fix, and this is the test that keeps it."""
    text = workflow.read_text(encoding="utf-8")
    group = _concurrency_group(text)
    assert group is not None, f"{workflow.name} writes a branch but declares no concurrency group"
    assert group != EXPECTED_GROUP, (
        f"{workflow.name} writes its own branch but squats on {EXPECTED_GROUP!r} -- "
        "it would cancel queued collector runs while it held the group"
    )
    assert _cancel_in_progress(text) == "false", f"{workflow.name} must queue, never cancel, its own writes"


def test_capture_workflow_is_still_a_guarded_writer_while_hibernated():
    """Pins the assumption the two tests above rest on: the scanner is
    still one of the guarded writers, so if its schedule is ever revived
    it inherits the single-writer lock rather than racing the catalog.

    The original form of this test additionally required an ACTIVE cron
    line. The 2026 market-discovery pivot hibernated the collector
    (schedule commented out, nothing deleted -- see
    docs/MODEL_RETIREMENT_2026.md), so what matters now is the guard, not
    the cadence: a hibernated writer that keeps its concurrency group is
    safe to wake up, and one that loses it is not."""
    capture = WORKFLOWS / "research-capture.yml"
    text = capture.read_text(encoding="utf-8")
    assert "research_scan_and_capture.py" in text
    assert capture in _durable_store_writers()
    assert _concurrency_group(text) == EXPECTED_GROUP
    assert re.search(r"cron:", text), "the hibernated cadence must stay recorded, not be deleted"


# =========================================================================
# THE CUTOVER CENSUS
#
# The concurrency group above SERIALIZES writers. It does not STOP one,
# which is the whole problem during a storage cutover: a capture that
# runs between the merge and the migration holds the group entirely
# legitimately and lands its shard anyway.
#
# docs/CUTOVER_SHARDING.md therefore names every workflow that must be
# disabled before a cutover. A census in prose goes stale the first time
# someone adds a workflow, and the one that got forgotten would be the
# one that broke the cutover -- so it is asserted here instead.
# =========================================================================

RUNBOOK = Path(__file__).resolve().parent.parent / "docs" / "CUTOVER_SHARDING.md"


def test_the_cutover_runbook_exists():
    assert RUNBOOK.is_file(), "the cutover runbook is what steps A-I are executed from"


@pytest.mark.parametrize("workflow", _durable_store_writers(), ids=lambda p: p.name)
def test_every_durable_store_writer_is_named_in_the_cutover_runbook(workflow: Path):
    """A writer missing from the freeze list is a writer that keeps
    running through the cutover."""
    name = re.search(r"^name:\s*(.+)$", workflow.read_text(encoding="utf-8"), re.MULTILINE)
    assert name, f"{workflow.name} has no name: line"
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert name.group(1).strip() in runbook, (
        f"{workflow.name} can write the durable store but is not named in "
        f"docs/CUTOVER_SHARDING.md -- it would keep running through a cutover"
    )


def test_the_conductor_is_in_the_freeze_list_even_though_it_writes_nothing():
    """It dispatches Research Capture runs, and sits in its OWN
    concurrency group rather than the writers' -- so leaving it enabled
    keeps triggering the very writer the freeze is meant to stop."""
    conductor = WORKFLOWS / "research-collection-conductor.yml"
    name = re.search(r"^name:\s*(.+)$", conductor.read_text(encoding="utf-8"), re.MULTILINE)
    assert name.group(1).strip() in RUNBOOK.read_text(encoding="utf-8")


def test_the_raw_git_pusher_checks_the_maintenance_window_itself():
    """`preseason-research-fetch.yml` pushes to research-data with raw
    git rather than through GitDurableStore, so the in-code freeze cannot
    reach it. It has to check the window in the workflow, and must fail
    on a non-zero exit -- `--status` returns 1 for OPEN and 2 for
    UNKNOWN, and both must refuse."""
    text = (WORKFLOWS / "preseason-research-fetch.yml").read_text(encoding="utf-8")
    push = text.index("git push origin HEAD:research-data")
    guard = text.find("research_maintenance_window.py --status")
    assert guard != -1, "the raw-git pusher does not consult the maintenance window"
    assert guard < push, "the window is checked after the push, which guards nothing"


def test_no_other_workflow_pushes_to_research_data_with_raw_git():
    """If a second raw-git pusher appears it needs the same explicit
    gate, because no library call sits between it and the branch."""
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        if f"git push origin HEAD:{DURABLE_BRANCH}" not in text:
            continue
        if "research_maintenance_window.py --status" not in text:
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} push research-data with raw git and never check the maintenance window"
    )


# =========================================================================
# MAIN-ONLY WRITERS
#
# The carve-out above is only safe while every one of these holds. If one
# stops holding, the workflow must go back into research-data-write.
# =========================================================================


def _executable(text: str) -> str:
    """Non-comment lines, with backslash continuations joined."""
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return re.sub(r"\\\n\s*", " ", "\n".join(lines))


def _staged_paths(text: str) -> list[str]:
    """Paths passed to `git add`. A bare `git add -A` / `git add .` stages
    everything and is reported as "."."""
    paths: list[str] = []
    for match in re.finditer(r"git add((?:\s+-\S+)*)([^\n;&|)]*)", _executable(text)):
        named = [part for part in match.group(2).split() if not part.startswith("-")]
        paths.extend(named or ["."])
    return paths


def _pushes_its_own_ref(text: str) -> bool:
    """Pushes the ref it runs on (main in production) rather than a branch
    of its own."""
    return bool(re.search(r"HEAD:(\$\{GITHUB_REF_NAME\}|\$\{\{ github\.ref_name \}\}|main\b)", text))


@pytest.mark.parametrize("name", sorted(MAIN_ONLY_WRITERS))
def test_main_only_writer_has_its_own_group_and_queues(name: str):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert _grants_write(text), f"{name} no longer writes; drop it from MAIN_ONLY_WRITERS"
    group = _concurrency_group(text)
    assert group is not None and group != EXPECTED_GROUP, f"{name} must hold its own group, not {group!r}"
    assert _cancel_in_progress(text) == "false"


@pytest.mark.parametrize("name", sorted(MAIN_ONLY_WRITERS))
def test_main_only_writer_stages_only_its_own_path(name: str):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    staged = _staged_paths(text)
    assert staged, f"{name} stages nothing -- the scan is stale"
    own = MAIN_ONLY_WRITERS[name]
    assert all(path == own or path.startswith(own + "/") for path in staged), (
        f"{name} stages {staged}, outside {own}"
    )


@pytest.mark.parametrize("name", sorted(MAIN_ONLY_WRITERS))
def test_main_only_writer_pushes_its_own_ref_with_rebase_retry(name: str):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    pushes = re.findall(r"git push[^\n]*", text)
    assert pushes, f"{name} has no push -- the scan is stale"
    for push in pushes:
        assert 'HEAD:${GITHUB_REF_NAME}' in push, f"{name} pushes somewhere other than its own ref: {push}"
    assert text.count("git pull --rebase") >= len(pushes), f"{name} has a push without a rebase before it"
    assert f"HEAD:{DURABLE_BRANCH}" not in text and "git_durable_store" not in text


def test_the_main_tree_scan_sees_the_other_main_writers():
    """Guards the overlap test below against passing vacuously."""
    main_writers = {
        path.name for path in WORKFLOWS.glob("*.yml") if _pushes_its_own_ref(path.read_text(encoding="utf-8"))
    }
    assert {"app-export.yml", "script-engine.yml", "kalshi-market-catalog.yml"} <= main_writers
    assert _staged_paths("git add -A && git commit") == ["."]
    assert _staged_paths("git add data/a data/b\n# git add data/live") == ["data/a", "data/b"]


@pytest.mark.parametrize("name", sorted(MAIN_ONLY_WRITERS))
def test_no_other_workflow_stages_a_main_only_writers_path(name: str):
    own = MAIN_ONLY_WRITERS[name]
    offenders = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name == name:
            continue
        text = path.read_text(encoding="utf-8")
        if not _pushes_its_own_ref(text):
            continue  # writes a branch of its own, never main's tree
        staged = _staged_paths(text)
        if any(p == "." or p == own or p.startswith(own + "/") or own.startswith(p.rstrip("/") + "/")
               for p in staged):
            offenders.append(path.name)
    assert not offenders, f"{offenders} stage {own}; {name} would race them outside a shared group"
