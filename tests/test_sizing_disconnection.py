"""Structural proof that the sizing library is wired to nothing.

`SIZING_IS_DISCONNECTED` is a string. These tests are what make it true.
The check is done by PARSING imports, not by grepping text and not by
observing that nothing has imported it at runtime: a module that is never
executed in a test still ships, and a grep for the package name matches
its own docstring.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "cfb_edge_finder"
SIZING_PACKAGE = "cfb_edge_finder.sizing"

GUARDED_PACKAGES = (
    # The wager ledger records bets the owner already placed. It is guarded
    # for exactly that reason: it is the one package that legitimately knows
    # stakes and outcomes, so it is the most natural place for someone to
    # reach for sizing math ("we have the stakes right here, just size the
    # next one"). Recording a stake and choosing one must stay different acts.
    "accounting",
    # The market catalog IS the live path after the 2026 discovery pivot:
    # it is what a human reads to decide what to bet. That makes it the
    # single most attractive place to "just add a suggested stake next to
    # the price", which is exactly the line that must not be crossed --
    # the catalog reports what the market offers and nothing about what to
    # do about it.
    "catalog",
    # The live execution orchestration: it reads the catalog, applies a
    # handicap supplied from outside, and reconciles coverage. It reports
    # a fee-adjusted edge, which makes it the obvious place to "just add a
    # suggested stake" -- so it is guarded like every other package that
    # could end up on the live path. `stake_placeholder` in its report is
    # a blank the operator fills in, and nothing here may compute one.
    "execution",
    "decision",
    "recommendation",
    "research",
    "expression",
    "modeling",
    "analytics",
    "projections",
    "kalshi",
    "betting",
    "ingestion",
    "ratings",
    "teams",
    "schemas",
    "data",
)
"""Every package that could plausibly end up on a live or shadow path.
Listed explicitly rather than 'everything except sizing' so that adding a
new package is a deliberate decision about which side of the line it
falls on."""


def imported_modules(path: Path) -> set[str]:
    """Module names imported by one file, from the AST.

    String literals are excluded by construction -- this module's own
    docstring names the sizing package, and a text search would flag it."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            # `from cfb_edge_finder import sizing` binds the sizing package just
            # as `import cfb_edge_finder.sizing` does, but the package name
            # lives on the ALIAS, not on node.module. Recording only the module
            # let that phrasing -- the most natural one to write -- through
            # undetected, so each imported name is recorded too.
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def guarded_source_files() -> list[Path]:
    files: list[Path] = []
    for package in GUARDED_PACKAGES:
        root = SRC / package
        if root.exists():
            files.extend(sorted(root.rglob("*.py")))
    return files


def test_the_guard_list_covers_every_package_except_sizing():
    """If a new package appears, this fails until someone decides whether
    it belongs on the guarded side. Silence would let a new decision
    module import sizing unnoticed."""
    packages = {p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    assert packages - {"sizing"} <= set(GUARDED_PACKAGES)


def test_no_guarded_module_imports_the_sizing_package():
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in guarded_source_files()
        if any(name == SIZING_PACKAGE or name.startswith(SIZING_PACKAGE + ".") for name in imported_modules(path))
    ]
    assert offenders == [], f"sizing is reachable from the decision path via: {offenders}"


def test_no_script_imports_the_sizing_package():
    """The CLI entry points are the other way a stake could reach a
    human."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted((REPO_ROOT / "scripts").glob("*.py"))
        if any(name == SIZING_PACKAGE or name.startswith(SIZING_PACKAGE + ".") for name in imported_modules(path))
    ]
    assert offenders == []


def test_importing_the_decision_package_does_not_pull_in_sizing():
    """Runtime confirmation in a clean interpreter: importing everything
    on the decision path must leave `sys.modules` free of sizing, which
    also catches a lazy import hidden inside a function body."""
    code = (
        "import sys;"
        f"sys.path.insert(0, {repr(str(SRC.parent))});"
        "import cfb_edge_finder.decision.artifact,"
        " cfb_edge_finder.decision.shadow,"
        " cfb_edge_finder.decision.report,"
        " cfb_edge_finder.decision.portfolio,"
        " cfb_edge_finder.decision.ops_health,"
        " cfb_edge_finder.decision.expression_selection,"
        " cfb_edge_finder.recommendation.pipeline;"
        "print(any(m.startswith('cfb_edge_finder.sizing') for m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False", result.stdout


def test_running_the_shadow_pipeline_does_not_import_sizing():
    """The strongest form: EXECUTE the shadow path end to end and check
    afterwards. A lazy import inside a rarely-taken branch would survive
    every other test here."""
    code = (
        "import sys;"
        f"sys.path.insert(0, {repr(str(SRC.parent))});"
        "from datetime import UTC, datetime;"
        "from cfb_edge_finder.decision.artifact import load_artifact;"
        "from cfb_edge_finder.decision.shadow import run_shadow_pipeline;"
        "run_shadow_pipeline([], resolution=load_artifact(None), now=datetime.now(UTC));"
        "print(any(m.startswith('cfb_edge_finder.sizing') for m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False", result.stdout


def test_sizing_itself_is_importable_so_the_absence_is_a_choice():
    """The library must work. A disconnection that was really a broken
    import would pass every test above for the wrong reason."""
    from cfb_edge_finder.sizing import SIZING_IS_DISCONNECTED
    from cfb_edge_finder.sizing.kelly_math import taker_fee_cents

    assert SIZING_IS_DISCONNECTED
    assert taker_fee_cents(contract_count=1, price_cents=50) == 2


@pytest.mark.parametrize("entry_point", ["build_research_decision_report.py", "week1_ops_health.py"])
def test_new_cli_entry_points_declare_no_sizing_import(entry_point):
    assert SIZING_PACKAGE not in imported_modules(REPO_ROOT / "scripts" / entry_point)


def _ops_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "week1_ops_health", REPO_ROOT / "scripts" / "week1_ops_health.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_ops_health_lock_probe_agrees_with_this_test_module():
    """Two implementations of one property is a drift risk; this pins
    them together."""
    ops = _ops_module()
    assert ops.sizing_import_offenders() == []
    assert ops.sizing_is_disconnected() is True


def test_both_probes_guard_exactly_the_same_packages():
    """Agreeing on a clean repo is not agreement.

    Both probes returned "no offenders" while one of them was not scanning the
    accounting package at all -- so the test above passed on a probe that would
    have reported the lock healthy with a real sizing import sitting in an
    unscanned package. Equality of the two lists is the property that actually
    prevents that; identical answers on a repo with nothing to find is not.
    """
    assert set(_ops_module().GUARDED_PACKAGES) == set(GUARDED_PACKAGES)


def test_the_lock_probe_is_not_fooled_by_a_docstring_mentioning_sizing():
    """`recommendation/card.py` names the sizing package while explaining
    that it does NOT import it. A text-search probe reported the lock as
    broken because of that sentence; an AST probe must not."""
    card = SRC / "recommendation" / "card.py"
    assert SIZING_PACKAGE in card.read_text(encoding="utf-8")
    assert SIZING_PACKAGE not in imported_modules(card)
    assert _ops_module().sizing_is_disconnected() is True


@pytest.mark.parametrize(
    "statement",
    [
        "import cfb_edge_finder.sizing",
        "import cfb_edge_finder.sizing.kelly_math",
        "from cfb_edge_finder.sizing import kelly_math",
        "from cfb_edge_finder.sizing.kelly_math import size_position",
        "from cfb_edge_finder import sizing",
        "from cfb_edge_finder import sizing as s",
    ],
)
def test_every_way_of_importing_sizing_is_detected(statement, tmp_path):
    """One undetected phrasing is a lock that does not lock.

    `from cfb_edge_finder import sizing` used to pass both probes, because the
    package name is on the alias rather than on the module. Each shape is now
    asserted by name so the gap cannot quietly reopen.
    """
    path = tmp_path / "candidate.py"
    path.write_text(statement, encoding="utf-8")
    names = imported_modules(path)
    assert any(
        name == SIZING_PACKAGE or name.startswith(SIZING_PACKAGE + ".") for name in names
    ), f"{statement!r} would reach sizing undetected; saw {sorted(names)}"


def test_the_detector_still_ignores_a_docstring_that_names_sizing(tmp_path):
    """The other half. Widening the detector must not make it text-search-ish:
    the sentence in `recommendation/card.py` explaining that sizing is NOT
    imported must still not register as an import."""
    path = tmp_path / "candidate.py"
    path.write_text('"""This module does not import cfb_edge_finder.sizing."""\n', encoding="utf-8")
    assert imported_modules(path) == set()


def test_the_lock_probe_detects_a_real_import(tmp_path, monkeypatch):
    """A probe that cannot fail proves nothing. This plants a genuine
    import in a throwaway tree and checks it is caught."""
    ops = _ops_module()
    fake_root = tmp_path / "src" / "cfb_edge_finder" / "decision"
    fake_root.mkdir(parents=True)
    (fake_root / "leak.py").write_text(
        "from cfb_edge_finder.sizing.kelly_math import size_position\n", encoding="utf-8"
    )
    monkeypatch.setattr(ops, "REPO_ROOT", tmp_path)
    assert ops.sizing_import_offenders() != []
    assert ops.sizing_is_disconnected() is False
