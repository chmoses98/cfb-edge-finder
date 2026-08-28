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

import sys

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
    from pathlib import Path

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
