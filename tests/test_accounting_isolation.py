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
import pkgutil
import re
import types
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


#: Modules that READ a recommendation in order to ATTRIBUTE a realised result
#: to it, and write nothing anywhere.
#:
#: *** WHY THEY ARE EXEMPT FROM THE NAME SCAN, AND WHAT REPLACES IT ***
#: The scan below is a NAME scan, and a name cannot tell "produces a
#: recommendation" from "reads one somebody else produced". A postmortem that
#: cuts realised profit and loss by robustness tier has to say the word
#: `recommendation` and the word `edge` to do its job, and refusing it those
#: words would mean the only way to get the cut is to spell it obscurely --
#: which is worse in every way than an exemption with a stronger test behind
#: it.
#:
#: So these modules are held to a STRICTER invariant instead, asserted in
#: `test_the_attribution_modules_cannot_reach_the_ledger_write_path`: nothing
#: that writes the ledger may import them, they may not import a write
#: function, and they may not define one. A recommendation therefore has no
#: path into a canonical row at all -- which is the property the name scan was
#: a proxy for.
ATTRIBUTION_MODULES = frozenset(
    {
        "cfb_edge_finder.accounting.recommendation_link",
        "cfb_edge_finder.accounting.postmortem",
    }
)

#: The functions that actually write a canonical row.
LEDGER_WRITE_FUNCTIONS = ("append_wagers", "append_settlements")

#: Every accounting module that can write, or that a writer runs.
LEDGER_WRITING_MODULES = (
    "cfb_edge_finder.accounting.store",
    "cfb_edge_finder.accounting.wager",
    "cfb_edge_finder.accounting.settlement",
    "cfb_edge_finder.accounting.import_routed_wagers",
    "cfb_edge_finder.accounting.import_settlements",
)


def test_accounting_exposes_no_sizing_or_recommendation_surface():
    violations = []
    package = cfb_edge_finder.accounting
    modules = [package]
    for _finder, name, _is_pkg in pkgutil.iter_modules(package.__path__, prefix=f"{package.__name__}."):
        if name in ATTRIBUTION_MODULES:
            continue
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
            value = getattr(module, name, None)
            # IMPORTING A SUBMODULE BINDS ITS NAME ON THE PACKAGE.
            #
            # Once any test has imported `accounting.recommendation_link`,
            # Python sets `recommendation_link` as an attribute of
            # `accounting` -- so this scan saw it in a full run and not when
            # the file ran alone, which is the worst kind of failure to debug.
            # An attribution MODULE reached through the package is the same
            # module the exemption above already covers.
            if isinstance(value, types.ModuleType) and value.__name__ in ATTRIBUTION_MODULES:
                continue
            # ...and so is a name re-exported out of one.
            if getattr(value, "__module__", None) in ATTRIBUTION_MODULES:
                continue
            hit = _forbidden_token(name)
            if hit is not None:
                violations.append(f"{module.__name__}.{name} (matched {hit!r})")
    assert violations == [], f"accounting grew a decision surface: {violations}"


def test_the_attribution_modules_cannot_reach_the_ledger_write_path():
    """*** STRICTER THAN THE NAME SCAN THEY ARE EXEMPT FROM ***

    A recommendation must have NO PATH into a canonical row. Three edges, all
    of which have to be empty for that to hold:

      1. nothing that writes the ledger may import an attribution module --
         so a writer cannot consult a recommendation while building a row;
      2. an attribution module may not import a write function -- so it
         cannot write one itself;
      3. an attribution module may not DEFINE one either, which is the shape
         somebody reaches for when the import looks wrong.
    """
    offenders = []

    for module_name in LEDGER_WRITING_MODULES:
        path = SRC / (module_name.split(".", 1)[1].replace(".", "/") + ".py")
        assert path.exists(), f"{module_name} is gone; this test no longer guards it"
        for imported in _imported_modules(path):
            if imported in ATTRIBUTION_MODULES:
                offenders.append(
                    f"{module_name} imports the attribution module {imported}"
                )
        source = path.read_text(encoding="utf-8")
        for attribution in ATTRIBUTION_MODULES:
            leaf = attribution.rsplit(".", 1)[-1]
            if f"import {leaf}" in source or f"from .{leaf}" in source:
                offenders.append(f"{module_name} names {leaf}")

    for module_name in sorted(ATTRIBUTION_MODULES):
        path = SRC / (module_name.split(".", 1)[1].replace(".", "/") + ".py")
        assert path.exists(), f"{module_name} is gone; this test no longer guards it"
        source = path.read_text(encoding="utf-8")
        for function in LEDGER_WRITE_FUNCTIONS:
            if function in source:
                offenders.append(f"{module_name} names the write function {function}")
        for verb in ("def write_", "def append_", "def save_", "def persist_"):
            if verb in source:
                offenders.append(f"{module_name} defines a writer ({verb.strip()})")

    assert offenders == [], (
        "a recommendation has a path into a canonical wager row; the whole point of the "
        f"separation is that it does not: {offenders}"
    )


def test_the_attribution_modules_really_do_exist_and_are_scanned():
    """Positive control. An exemption for a module that has been renamed or
    deleted is an exemption that silently covers nothing, and the scan above
    would pass while guarding an empty set."""
    for module_name in sorted(ATTRIBUTION_MODULES):
        module = importlib.import_module(module_name)
        assert module is not None
        assert any(
            _forbidden_token(name) for name in dir(module) if not name.startswith("_")
        ), (
            f"{module_name} carries no name the scan would have caught, so its exemption "
            "is doing nothing and should be removed"
        )


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
