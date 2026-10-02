"""Make the test-support modules importable by name.

`tests/` is a package (it has an `__init__.py`), so pytest puts the REPO
ROOT on `sys.path`, not `tests/` -- which is why the older test modules
each carry their own `sys.path.insert(0, "tests")` before
`from research_factories import ...`. That form depends on the working
directory pytest happens to be invoked from.

Doing it here, once, with an absolute path, makes `research_factories`
and `corpus_helpers` importable from every test regardless of cwd. The
per-file inserts are left in place; they are now redundant rather than
load-bearing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))



@pytest.fixture(autouse=True)
def _isolate_from_the_actions_runner(monkeypatch):
    """Tests run inside GitHub Actions in CI, where GITHUB_ACTIONS and
    GITHUB_STEP_SUMMARY are set for real. Code under test that reports its
    operational verdict would otherwise append fixture verdicts (e.g. a
    DEGRADED slate built from a fake catalog) to the CI job's own summary,
    and behave differently in CI than locally. A test that exercises that
    reporting sets these itself with monkeypatch."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("CATALOG_BUILD_OUTCOME", raising=False)
