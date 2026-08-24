"""Mechanically proves the architectural boundary between prediction-time
model code and evaluation-time post-hoc diagnostics (historical-integrity
audit, mission section 3): no module on the actual prediction path may
import anything from modeling/diagnostics.py, so no diagnostic or
outcome-derived field can ever reach a prediction feature, even
accidentally. This is a static (AST) check of the real import
statements, not a docstring-reading exercise -- docstrings can drift,
import statements cannot.

diagnostics.py is explicitly allowed to depend on backtest.py (it is a
pure, downstream CONSUMER of already-computed `backtest.GameOutcome`
records -- see diagnostics.py's own module docstring) -- but the
dependency must never run the other way.
"""

from __future__ import annotations

import ast
from pathlib import Path

MODELING_DIR = Path(__file__).resolve().parents[1] / "src" / "cfb_edge_finder" / "modeling"

# Every module on the genuine prediction path: building the training
# corpus, fitting ratings, blending priors, projecting a game, calibrating
# probabilities, and the walk-forward harness that drives them -- plus the
# leakage primitive they all depend on. diagnostics.py must never appear
# as a dependency of any of these.
_PREDICTION_PATH_MODULES = (
    "corpus.py",
    "ratings.py",
    "priors.py",
    "score_model.py",
    "naive_benchmark.py",
    "calibration.py",
    "leakage.py",
    "qb_continuity.py",
    "backtest.py",
)


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
    return names


def test_no_prediction_path_module_imports_diagnostics():
    violations = []
    for filename in _PREDICTION_PATH_MODULES:
        path = MODELING_DIR / filename
        assert path.exists(), f"expected prediction-path module missing: {path}"
        for name in _imported_module_names(path):
            if name.endswith(".diagnostics") or name == "diagnostics":
                violations.append(f"{filename} imports {name!r}")
    assert violations == [], (
        "prediction-path module(s) import modeling/diagnostics.py, breaking the "
        f"prediction/diagnostics architectural boundary: {violations}"
    )


def test_diagnostics_module_depends_on_backtest_but_never_the_reverse():
    diagnostics_imports = _imported_module_names(MODELING_DIR / "diagnostics.py")
    assert any(name.endswith(".backtest") for name in diagnostics_imports), (
        "diagnostics.py is expected to consume backtest.GameOutcome -- if this changes, "
        "update this test's assumption deliberately, don't just delete it"
    )
    backtest_imports = _imported_module_names(MODELING_DIR / "backtest.py")
    assert not any(name.endswith(".diagnostics") for name in backtest_imports)


def test_production_projection_script_does_not_import_diagnostics():
    # scripts/build_cfb_baseline.py is the actual single-game research
    # projection CLI (the closest thing to a "production" prediction
    # path in this codebase) -- scripts/backtest_cfb_baseline.py is
    # allowed to import diagnostics.py (it only PRINTS a report after a
    # backtest completes, per its own --diagnostics flag), but the
    # projection script must never depend on it.
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    path = scripts_dir / "build_cfb_baseline.py"
    assert path.exists()
    for name in _imported_module_names(path):
        assert not name.endswith(".diagnostics"), f"build_cfb_baseline.py unexpectedly imports {name!r}"
