"""The accounting package's OWN invariant, not a loophole in someone else's.

`test_no_recommendation_surface.py` scans the PREDICTIVE packages so the CFB
model cannot grow into a recommendation engine. `cfb_edge_finder.accounting` is
not in that scan, and it deliberately cannot be: it records real wagers, so it
contains the word `stake` legitimately, and adding it to `_SCANNED_PACKAGES`
would fail on the one field the package exists to hold.

That is an honest reason to be excluded from THAT test and no reason at all to
be unpoliced. An accounting ledger is exactly the shape of thing that becomes a
staking surface by accretion -- it already knows prices, sizes and outcomes, so
"just rank these" is a small diff away. So this file holds it to the equivalent
rule, stated for what it actually is:

  * the ledger may not size, rank, recommend or price anything. It records.
  * no predictive package may import it. A projection that can read the wager
    ledger is one refactor from being trained on it, which would make the
    owner's betting history into model authority -- the precise thing the CFB
    mission forbids.

Both directions are checked, because an empty dependency edge is only trustworthy
when nothing crosses it either way.
"""

from __future__ import annotations

import ast
import importlib
import re
import pkgutil
from pathlib import Path

import pytest

import cfb_edge_finder
import cfb_edge_finder.accounting

SRC = Path(cfb_edge_finder.__file__).parent

#: The packages that carry predictive authority. None of them may know the
#: wager ledger exists.
PREDICTIVE_PACKAGES = (
    "betting",
    "projections",
    "research",
    "modeling",
    "kalshi",
    "recommendation",
    "expression",
    "ingestion",
    "teams",
    "data",
)

#: Forbidden in accounting. NOT the same list the predictive packages get:
#: `stake` is the whole point here. What must never appear is the language of
#: DECIDING a bet rather than recording one.
#:
#: Matched as TOKENS, not substrings -- `ledger_path` contains the letters of
#: "edge" and means nothing of the kind. A substring rule that cries wolf on a
#: legitimate name is a rule someone eventually deletes, which is how the real
#: violation gets through later.
FORBIDDEN_IN_ACCOUNTING = (
    "kelly",
    "recommend",
    "projection",
    "fair_value",
    "fair_probability",
    "edge",
    "expected_value",
    "rank",
    "score_bet",
    "size_bet",
    "sizing",
    "qualification",
    "readiness",
    "place_order",
    "place_bet",
    "execute_trade",
    "execute_order",
)


def _tokens(name: str) -> list[str]:
    """`ledgerPath` and `ledger_path` both become ["ledger", "path"]."""
    return [t for t in re.split(r"_+|(?<=[a-z0-9])(?=[A-Z])", name) if t]


def _forbidden_token(name: str):
    """The forbidden entry this name matches, or None.

    An entry matches a TOKEN of the name, by prefix so that "recommend" still
    catches "recommendation" -- but never mid-word, so "ledger" is not "edge".
    Multi-word entries must match consecutive tokens.
    """
    tokens = [t.lower() for t in _tokens(name)]
    for forbidden in FORBIDDEN_IN_ACCOUNTING:
        wanted = forbidden.split("_")
        for start in range(len(tokens) - len(wanted) + 1):
            if all(
                tokens[start + offset].startswith(part)
                for offset, part in enumerate(wanted)
            ):
                return forbidden
    return None


def _module_files(package_name: str) -> list[Path]:
    directory = SRC / package_name
    if not directory.is_dir():
        return []
    return sorted(directory.rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    """Every module name this file imports, via AST.

    Parsed, not grepped: a grep for "accounting" matches the word in a comment,
    in a docstring, and in a variable called `accounting_enabled`, none of which
    is a dependency. Only a real import edge counts.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            # The module AND each imported name. `from cfb_edge_finder import
            # accounting` binds the accounting package just as surely as
            # `import cfb_edge_finder.accounting` does, but only the alias
            # carries the word -- recording the module alone would let the most
            # natural way to write the forbidden import pass unnoticed.
            prefix = "." * node.level + (node.module or "")
            found.add(prefix)
            for alias in node.names:
                found.add(f"{prefix}.{alias.name}" if prefix and not prefix.endswith(".") else f"{prefix}{alias.name}")
    return found


def test_no_predictive_package_imports_the_wager_ledger():
    offenders = []
    for package in PREDICTIVE_PACKAGES:
        for path in _module_files(package):
            for module in _imported_modules(path):
                if "accounting" in module:
                    offenders.append(f"{path.relative_to(SRC)} imports {module}")
    assert offenders == [], (
        "a predictive package can read the wager ledger; that makes the owner's "
        f"betting history into model input: {offenders}"
    )


def test_the_import_detector_can_actually_find_a_known_import():
    """Positive control.

    Without this, the test above passes just as happily when `_imported_modules`
    is broken and returns nothing. It must be shown to find a real edge that is
    known to exist before its silence means anything.
    """
    persistence = SRC / "research" / "persistence.py"
    assert persistence.exists(), "fixture moved; pick another known-importing module"
    found = _imported_modules(persistence)
    assert any("cfb_edge_finder.research" in m for m in found), (
        f"the detector failed to find a known import edge; it proves nothing: {sorted(found)}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "import cfb_edge_finder.accounting",
        "import cfb_edge_finder.accounting as acct",
        "import cfb_edge_finder.accounting.store",
        "from cfb_edge_finder import accounting",
        "from cfb_edge_finder.accounting import store",
        "from cfb_edge_finder.accounting.store import append_wagers",
        "from ..accounting import store",
        "from ..accounting.store import append_wagers",
        "def f():\n    from cfb_edge_finder import accounting",
    ],
)
def test_the_import_detector_catches_every_way_of_writing_the_forbidden_import(source, tmp_path):
    """Each shape, individually.

    The control above only shows the detector finds SOME edge. It found none of
    `from cfb_edge_finder import accounting` while still passing, because that
    statement carries the package name on the ALIAS and the detector read only
    the module. One phrasing slipping through is all it takes -- and it happens
    to be the phrasing a person writes first. So every shape is asserted by
    name, including the function-local import someone reaches for precisely
    when a module-level one looks wrong.
    """
    path = tmp_path / "candidate.py"
    path.write_text(source, encoding="utf-8")
    found = _imported_modules(path)
    assert any("accounting" in module for module in found), (
        f"{source!r} would import the ledger undetected; found only {sorted(found)}"
    )


def test_accounting_does_not_import_any_predictive_package():
    offenders = []
    for path in _module_files("accounting"):
        for module in _imported_modules(path):
            for package in PREDICTIVE_PACKAGES:
                if f"cfb_edge_finder.{package}" in module:
                    offenders.append(f"{path.relative_to(SRC)} imports {module}")
    assert offenders == [], (
        f"accounting reaches into predictive code; keep the edge empty both ways: {offenders}"
    )


def test_accounting_exposes_no_sizing_or_recommendation_surface():
    violations = []
    package = cfb_edge_finder.accounting
    modules = [package]
    for _finder, name, _is_pkg in pkgutil.iter_modules(package.__path__, prefix=f"{package.__name__}."):
        modules.append(importlib.import_module(name))
    for module in modules:
        for name in dir(module):
            if name.startswith("_"):
                continue
            # FORBIDDEN_PROVENANCE_FIELDS is the tuple of names this package
            # REFUSES; naming them in order to reject them is the opposite of
            # exposing them.
            if name == "FORBIDDEN_PROVENANCE_FIELDS":
                continue
            hit = _forbidden_token(name)
            if hit is not None:
                violations.append(f"{module.__name__}.{name} (matched {hit!r})")
    assert violations == [], f"accounting grew a decision surface: {violations}"


def test_the_surface_detector_can_actually_find_a_forbidden_name():
    """Positive control for the scan above, for the same reason as the other."""
    import types

    fake = types.ModuleType("fake")
    fake.recommend_stake = lambda: None  # noqa: E731
    fake.kelly_fraction = lambda: None  # noqa: E731
    fake.ledger_path = lambda: None  # noqa: E731
    hits = sorted(n for n in dir(fake) if not n.startswith("_") and _forbidden_token(n))
    assert hits == ["kelly_fraction", "recommend_stake"], (
        f"the surface detector is broken: {hits}"
    )
    # ...and the same call must NOT fire on the legitimate name that shares
    # letters with a forbidden one. A detector nobody trusts gets deleted.
    assert _forbidden_token("ledger_path") is None
