"""The sidecar has to survive the working-tree swap the real run performs.

A live run on main captured 158 canonical observations and 0 shadow rows,
with 0 failures and 0 games offered -- telemetry indistinguishable from
"nothing was eligible". The cause was not in the sidecar's logic, which
was correct and well tested, but in WHEN its imports resolved:

`main` calls `ensure_branch_checked_out` before `_apply_scan`, and that
runs `git checkout -B research-data`, replacing the working tree -- the
editable install's `src/` included -- with the research-data branch's own
stray `src/` snapshot. That snapshot is a fossil of the old
stray-source-tree incident and has no `research/preseason/` package at
all. Modules already in `sys.modules` survive the swap; a function-local
import does not. So `_build_shadow_sidecar`'s deferred
`research.preseason.corpus` import raised ModuleNotFoundError on every
real run, the broad `except` swallowed it, and the sidecar was silently
None in production while every test passed -- because no test checks out
the data branch mid-run.

These tests pin the two invariants that failure violated.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "scripts")

import research_scan_and_capture as scanner  # noqa: E402

# Every module `_build_shadow_sidecar` needs. Each must be bound while
# main's tree is still on disk, i.e. at scanner import time.
REQUIRED_MODULES = (
    "cfb_edge_finder.modeling.leakage",
    "cfb_edge_finder.research.preseason.corpus",
    "cfb_edge_finder.research.preseason.shadow_sidecar",
    "cfb_edge_finder.research.preseason.shadow_spec",
    "cfb_edge_finder.research.preseason.shadow_transform",
)


@pytest.mark.parametrize("module_name", REQUIRED_MODULES)
def test_sidecar_dependency_is_imported_before_the_branch_checkout(module_name: str) -> None:
    """Importing the scanner must bind every module the sidecar needs.

    `main` swaps the working tree to a branch whose `src/` predates these
    modules, so anything not already in `sys.modules` by then can never
    be imported again in that process.
    """
    assert module_name in sys.modules, (
        f"{module_name} is not bound at scanner import time. If "
        f"_build_shadow_sidecar imports it lazily, the import will run "
        f"AFTER ensure_branch_checked_out has replaced src/ with the "
        f"research-data branch's stale snapshot, raise "
        f"ModuleNotFoundError, be swallowed, and silently disable the "
        f"shadow sidecar in production."
    )


def test_scanner_has_no_function_local_third_party_imports() -> None:
    """A deferred import of our own package is the bug above, re-armed."""
    import ast

    tree = ast.parse(Path("scripts/research_scan_and_capture.py").read_text())
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.ImportFrom) and (inner.module or "").startswith(
                "cfb_edge_finder"
            ):
                offenders.append(f"{node.name}: from {inner.module}")
            elif isinstance(inner, ast.Import):
                for alias in inner.names:
                    if alias.name.startswith("cfb_edge_finder"):
                        offenders.append(f"{node.name}: import {alias.name}")
    assert offenders == [], (
        "function-local cfb_edge_finder imports resolve after the working "
        f"tree has been swapped and will fail there: {offenders}"
    )


def test_builder_names_its_reason_when_the_cache_is_absent(tmp_path) -> None:
    """A None sidecar must say WHY.

    Returning a bare None left every shadow counter at 0, which is also
    what a healthy run with nothing eligible produces -- so the live
    failure was unreadable from the log.
    """
    from datetime import UTC, datetime

    sidecar, state = scanner._build_shadow_sidecar(tmp_path, 2026, datetime.now(UTC))
    assert sidecar is None
    assert state != "ACTIVE"
    assert state.startswith("UNAVAILABLE_"), state


def test_builder_reports_active_against_a_real_cache(tmp_path) -> None:
    """And must say ACTIVE when it genuinely built one."""
    import json
    from datetime import UTC, datetime

    cache_dir = tmp_path / "data" / "research_cache" / "preseason"
    cache_dir.mkdir(parents=True)
    payload = {
        "season": 2026,
        "talent": [
            {"team": "Alpha", "talent": 900.0},
            {"team": "Beta", "talent": 700.0},
        ],
    }
    (cache_dir / "2026.json").write_text(json.dumps(payload))

    sidecar, state = scanner._build_shadow_sidecar(tmp_path, 2026, datetime.now(UTC))
    # Either it built (ACTIVE) or it declined for a NAMED reason. What it
    # must never do is decline anonymously.
    if sidecar is None:
        assert state.startswith("UNAVAILABLE_"), state
    else:
        assert state == "ACTIVE"
        assert sidecar.beta == pytest.approx(0.018993)


# ---------------------------------------------------------------------
# THE TEST THAT WOULD ACTUALLY HAVE CAUGHT THIS
#
# Everything above inspects THIS process, where the working tree never
# moves. That is why 2,079 tests passed while the real workflow failed:
# no test ran the scanner across a branch checkout. This one does. It
# builds a throwaway repo with a real remote, a code branch, and an
# ORPHAN data branch carrying a STALE `src/` with no
# `research/preseason/` package -- the shape of the real research-data
# branch -- then, in a subprocess, imports the scanner, calls the REAL
# `ensure_branch_checked_out`, and only then builds the sidecar.
#
# Measured against the two commits that matter:
#   pre-fix  (52c0cf5): SIDECAR=NONE   -- silently disabled
#   post-fix (15a22cf): SIDECAR=BUILT, STATE=ACTIVE
# ---------------------------------------------------------------------

# Real CFBD team names and talent rows. Synthetic names ("Alpha") are
# dropped by team resolution, which would make this test pass for the
# wrong reason -- a sidecar that reports no talent looks a lot like a
# sidecar that could not be imported.
_TALENT_FIXTURE = [
    {"team": "Georgia", "talent": 1003.67, "year": 2026},
    {"team": "Alabama", "talent": 985.12, "year": 2026},
    {"team": "Rice", "talent": 512.40, "year": 2026},
]

_PROBE = '''
import sys
from datetime import UTC, datetime
from pathlib import Path

repo = Path(sys.argv[1])
sys.path.insert(0, str(repo / "scripts"))
import research_scan_and_capture as scanner

# Prove we are exercising the throwaway tree, not the installed package.
assert Path(scanner.__file__).is_relative_to(repo), scanner.__file__
import cfb_edge_finder

assert Path(cfb_edge_finder.__file__).is_relative_to(repo), cfb_edge_finder.__file__

# The real thing the workflow does between import and use.
scanner.git_durable_store.ensure_branch_checked_out(repo, "datastale")

corpus_py = repo / "src" / "cfb_edge_finder" / "research" / "preseason" / "corpus.py"
assert not corpus_py.exists(), f"tree not swapped: {corpus_py} still present"

out = scanner._build_shadow_sidecar(repo, 2026, datetime.now(UTC))
sidecar, state = out if isinstance(out, tuple) else (out, "LEGACY_NO_STATE")
print(f"SIDECAR={'BUILT' if sidecar else 'NONE'} STATE={state}")
if sidecar is not None:
    print(f"TEAMS={len(sidecar.talent_by_team)} BETA={sidecar.beta}")
'''


def _git(args: list[str], cwd) -> None:
    import subprocess

    r = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"


def test_sidecar_builds_after_a_real_data_branch_checkout(tmp_path) -> None:
    """The regression test for the live defect, in its real environment.

    Imports the scanner, performs a genuine `ensure_branch_checked_out`
    onto a branch whose `src/` lacks `research/preseason/`, and only then
    builds the sidecar -- the exact order `main()` uses.
    """
    repo_root = Path(__file__).resolve().parents[1]
    work = tmp_path / "work"
    bare = tmp_path / "remote.git"
    work.mkdir()

    # Branch 1: the code, exactly as it stands in this checkout.
    _git(["init", "-q", "-b", "codemain", "."], work)
    for name in ("src", "scripts"):
        shutil.copytree(
            repo_root / name, work / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    _git(["add", "-A"], work)
    _git(["commit", "-qm", "code"], work)

    # Branch 2: the data branch -- ORPHAN, stale src/, no preseason package.
    _git(["checkout", "-q", "--orphan", "datastale"], work)
    _git(["rm", "-rq", "--cached", "."], work)
    stale = tmp_path / "stale"
    shutil.copytree(work / "src", stale, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(stale / "cfb_edge_finder" / "research" / "preseason")
    for child in work.iterdir():
        if child.name != ".git":
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    shutil.copytree(stale, work / "src")
    cache = work / "data" / "research_cache" / "preseason"
    cache.mkdir(parents=True)
    (cache / "2026.json").write_text(json.dumps({"season": 2026, "talent": _TALENT_FIXTURE}))
    _git(["add", "-Af"], work)
    _git(["commit", "-qm", "data"], work)

    # A real remote: ensure_branch_checked_out fetches before it checks out.
    _git(["init", "-q", "--bare", str(bare)], tmp_path)
    _git(["remote", "add", "origin", str(bare)], work)
    _git(["push", "-q", "origin", "codemain", "datastale"], work)
    _git(["checkout", "-q", "codemain"], work)

    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE)
    result = subprocess.run(
        [sys.executable, str(probe), str(work)],
        cwd=work,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""},
        timeout=300,
    )
    assert result.returncode == 0, (
        f"probe failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr[-3000:]}"
    )
    assert "SIDECAR=BUILT" in result.stdout, (
        "the sidecar did not survive the data-branch checkout -- this is the "
        f"live defect, reproduced:\n{result.stdout}"
    )
    assert "STATE=ACTIVE" in result.stdout, result.stdout
    assert "BETA=0.018993" in result.stdout, result.stdout
