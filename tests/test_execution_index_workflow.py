"""The scheduled workflow must build, verify and commit the execution index
in the right order.

``kalshi-market-catalog.yml`` is the only thing that publishes the small
files a GitHub-connected session reads. If the verify step moved after the
commit, or the "market surface unchanged" branch stopped resyncing the
index from the committed catalog, a broken or missing index would ship (or
never get repaired) without any test noticing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "kalshi-market-catalog.yml"
BUILDER = REPO_ROOT / "scripts" / "build_kalshi_cfb_catalog.py"

VERIFY_CMD = "scripts/build_cfb_execution_index.py --out-dir data/live --verify-only"
REBUILD_CMD = "scripts/build_cfb_execution_index.py --out-dir data/live"


def _parse_steps(text: str) -> list[dict]:
    """The catalog job's steps as ``{"name", "run"}`` dicts.

    Stdlib only: CI installs the package's dependencies and PyYAML is not
    one of them. Steps are the ``- `` items at the steps list's indent; a
    ``run: |`` block is every following line indented deeper than ``run:``."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "steps:")
    item_indent = len(lines[start + 1]) - len(lines[start + 1].lstrip())
    steps: list[dict] = []
    i = start + 1
    while i < len(lines):
        line = lines[i]
        indent = len(line) - len(line.lstrip())
        if line.strip() and indent < item_indent:
            break
        stripped = line.strip()
        if indent == item_indent and stripped.startswith("- "):
            steps.append({})
            stripped = stripped[2:]
            indent += 2
        if steps and stripped.startswith("name:"):
            steps[-1]["name"] = stripped[5:].strip()
        if steps and stripped.startswith("run:"):
            value = stripped[4:].strip()
            if value in ("|", ">"):
                body: list[str] = []
                i += 1
                while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                    body.append(lines[i].strip())
                    i += 1
                steps[-1]["run"] = "\n".join(body)
                continue
            steps[-1]["run"] = value
        i += 1
    return steps


@pytest.fixture(scope="module")
def steps() -> list[dict]:
    return _parse_steps(WORKFLOW.read_text(encoding="utf-8"))


def test_step_parser_sees_every_step(steps):
    # guards the parser itself: the job has more than a handful of steps and
    # the builder's multi-line run block is captured whole
    assert len(steps) >= 7
    assert any("--print-summary" in _run(s) for s in steps)


def _index_of(steps: list[dict], predicate) -> int:
    matches = [i for i, s in enumerate(steps) if predicate(s)]
    assert matches, "no matching step"
    assert len(matches) == 1, f"expected exactly one matching step, found {len(matches)}"
    return matches[0]


def _run(step: dict) -> str:
    return step.get("run") or ""


@pytest.fixture(scope="module")
def commit_step(steps) -> dict:
    return steps[_index_of(steps, lambda s: "git commit" in _run(s))]


def test_verify_only_step_exists_and_runs_the_script(steps):
    i = _index_of(steps, lambda s: VERIFY_CMD in _run(s))
    assert _run(steps[i]).strip().startswith("python3 ")


def test_verify_step_runs_after_the_catalog_build_and_before_the_commit(steps):
    build = _index_of(steps, lambda s: "build_kalshi_cfb_catalog.py" in _run(s))
    verify = _index_of(steps, lambda s: VERIFY_CMD in _run(s))
    commit = _index_of(steps, lambda s: "git commit" in _run(s))
    assert build < verify < commit


def test_only_one_step_commits_and_it_stages_the_whole_live_tree(steps, commit_step):
    commit_text = _run(commit_step)
    assert "git add -A data/live" in commit_text
    # every staging command stages the whole tree, so execution/** and the
    # index are recorded along with a game file that left the slate
    for line in commit_text.splitlines():
        if line.strip().startswith("git add"):
            assert line.strip() == "git add -A data/live", line


def test_unchanged_fingerprint_branch_resyncs_the_execution_index(commit_step):
    text = _run(commit_step)
    branch_start = text.index('if [ -n "$OLD_FP" ] && [ "$NEW_FP" = "$OLD_FP" ]; then')
    branch_end = text.index("\nfi\n", text.index("exit 1", branch_start))
    branch = text[branch_start:branch_end]

    checkout = branch.index("git checkout -- data/live")
    rebuild = branch.index(REBUILD_CMD, checkout)
    add = branch.index("git add -A data/live", rebuild)
    diff = branch.index("git diff --cached --quiet", add)
    commit = branch.index("git commit", diff)
    assert checkout < rebuild < add < diff < commit

    # the rebuild must be the BUILD form, not the read-only verification
    rebuild_line = branch[rebuild : branch.index("\n", rebuild)]
    assert "--verify-only" not in rebuild_line

    # it commits only when the cached diff reports a difference
    gate = branch[diff - len("if ") : diff + len("git diff --cached --quiet; then")]
    assert gate == "if git diff --cached --quiet; then"
    after_gate = branch[diff : commit]
    assert "exit 0" in after_gate, "an in-sync tree must exit without committing"

    # the resync restores the committed tree exactly, including untracked leftovers
    assert "git clean -fdq data/live" in branch[checkout:rebuild]
    # the unstaged timestamp-only catalog is discarded before restoring
    assert "git reset -q" in branch[:checkout]


def test_changed_surface_path_commits_via_the_same_diff_gate(commit_step):
    text = _run(commit_step)
    first_gate = text.index("git diff --cached --quiet")
    assert text.index("git add -A data/live") < first_gate
    assert "catalog byte-identical; nothing to commit" in text[first_gate : first_gate + 200]
    # the regular commit message path follows the unchanged branch
    resync_commit = text.index('git commit -m "kalshi cfb execution index: resync')
    assert text.index('git commit -m "kalshi cfb catalog:') > resync_commit


def test_commit_step_still_pushes_with_retries(commit_step):
    text = _run(commit_step)
    assert text.count("git push origin") == 2
    assert text.count("git pull --rebase --autostash") == 2


def test_catalog_builder_calls_write_execution_artifacts():
    source = BUILDER.read_text(encoding="utf-8")
    assert "from cfb_edge_finder.catalog.execution import" in source
    assert "write_execution_artifacts(" in source
    assert "ExecutionBuildError" in source
    # the call sits inside the builder's main flow, not just an unused import
    assert source.count("write_execution_artifacts") >= 2


def test_workflow_does_not_gain_secrets_and_keeps_its_own_concurrency_group():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.search(r"^concurrency:\n  group: kalshi-market-catalog$", text, re.MULTILINE)
    assert "secrets." not in text
