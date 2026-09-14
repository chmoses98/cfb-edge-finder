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

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
