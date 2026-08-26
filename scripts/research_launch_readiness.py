#!/usr/bin/env python3
"""Milestone E, Part N: machine-readable preseason launch readiness check.

Checks what CAN be verified statically/locally in this environment
(modules exist and import, the hard-disable/no-execution-surface tests
pass, workflows are defined, the rehearsal test suite is present and
green). Network-dependent items (a genuine live capture, a real durable
push) are checked as prerequisites-present rather than executed here --
see docs/MILESTONE_E.md "Launch readiness checklist" for which items
still require a live GitHub Actions run to close out, exactly like every
prior milestone in this repo (B/C/D) that could only fully validate its
live path via a workflow_dispatch run from an environment with network
egress.

    python scripts/research_launch_readiness.py
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

REQUIRED_MODULES = [
    "cfb_edge_finder.research.identity",
    "cfb_edge_finder.research.timing",
    "cfb_edge_finder.research.closing",
    "cfb_edge_finder.research.settlement",
    "cfb_edge_finder.research.clv",
    "cfb_edge_finder.research.gap_buckets",
    "cfb_edge_finder.research.correlation",
    "cfb_edge_finder.research.health",
    "cfb_edge_finder.research.persistence",
    "cfb_edge_finder.research.git_durable_store",
    "cfb_edge_finder.research.reporting",
    "cfb_edge_finder.research.qualification",
    "cfb_edge_finder.schemas.qualification",
]

REQUIRED_WORKFLOWS = [
    ".github/workflows/research-capture.yml",
    ".github/workflows/research-settlement.yml",
    ".github/workflows/research-weekly-report.yml",
]

REQUIRED_TEST_FILES_SUBSTRINGS = [
    "test_research_identity",
    "test_research_timing",
    "test_research_persistence",
    "test_research_git_sync",
    "test_research_closing",
    "test_research_settlement",
    "test_research_clv",
    "test_research_gap_buckets",
    "test_research_correlation",
    "test_research_health",
    "test_research_rehearsal",
    "test_research_failure_injection",
    "test_qualification_hard_disabled",
]


def _check(name: str, ok: bool, evidence: str) -> dict:
    return {"requirement": name, "status": "PASS" if ok else "FAIL", "evidence": evidence}


def main() -> int:
    results = []

    for module_name in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
            results.append(_check(f"module_importable:{module_name}", True, "imported without error"))
        except Exception as exc:  # noqa: BLE001
            results.append(_check(f"module_importable:{module_name}", False, str(exc)))

    for wf in REQUIRED_WORKFLOWS:
        path = REPO_ROOT / wf
        results.append(_check(f"workflow_defined:{wf}", path.exists(), f"exists={path.exists()}"))

    tests_dir = REPO_ROOT / "tests"
    test_files = {p.stem for p in tests_dir.glob("test_*.py")} if tests_dir.exists() else set()
    for substr in REQUIRED_TEST_FILES_SUBSTRINGS:
        found = any(substr in name for name in test_files)
        results.append(_check(f"test_suite_present:{substr}", found, f"found={found}"))

    pytest_proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-header"], cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    results.append(
        _check(
            "full_test_suite_green",
            pytest_proc.returncode == 0,
            pytest_proc.stdout.strip().splitlines()[-1] if pytest_proc.stdout.strip() else pytest_proc.stderr[-500:],
        )
    )

    ruff_proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    ruff_evidence = (ruff_proc.stdout + ruff_proc.stderr).strip()[-500:] or "clean"
    results.append(_check("ruff_clean", ruff_proc.returncode == 0, ruff_evidence))

    # Static items that are documented, not machine-checkable without live network access.
    doc_only = [
        (
            "durable_persistence_live_verified",
            "requires a live GH Actions run pushing to research-data -- see docs/MILESTONE_E.md",
        ),
        (
            "live_capture_rehearsal_run",
            "in-process rehearsal (fixtures) is PASS via tests; a live scan is a live-network follow-up",
        ),
        (
            "season_scale_benchmark",
            "see tests/test_research_scale_benchmark.py for the season-volume estimate",
        ),
        (
            "recommendation_logic_disabled",
            "see tests/test_qualification_hard_disabled.py and test_no_recommendation_surface.py",
        ),
        (
            "no_execution_surface",
            "see tests/test_no_recommendation_surface.py -- no Kalshi write/order client exists",
        ),
    ]
    for name, note in doc_only:
        results.append({"requirement": name, "status": "DOCUMENTED_NOT_MACHINE_CHECKED", "evidence": note})

    overall_pass = all(r["status"] == "PASS" for r in results if r["status"] in ("PASS", "FAIL"))
    print(json.dumps({"overall": "PASS" if overall_pass else "FAIL", "checks": results}, indent=2))
    return 0 if overall_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
