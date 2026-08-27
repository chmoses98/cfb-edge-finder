"""Mission sections 25, 26, 27, 31: the safety invariants.

These are the tests that matter most in this milestone. Everything else
describes structure; these prove the structure cannot act.
"""

from __future__ import annotations

import ast
import inspect
import json
import resource
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cfb_edge_finder.expression.corpus import load_contract_snapshots
from cfb_edge_finder.recommendation import (
    candidate,
    card,
    dedup,
    eligibility,
    evidence,
    odds,
    pipeline,
    risk,
    scoring,
    thresholds,
)
from cfb_edge_finder.recommendation.eligibility import QUALIFICATION_DISABLED, EligibilityConfig
from cfb_edge_finder.recommendation.pipeline import run_pipeline

SKELETON_MODULES = (
    candidate, eligibility, thresholds, evidence, risk, dedup, scoring, card, odds, pipeline,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def _corpus_rows(n_games, rungs=8):
    rows = []
    for g in range(n_games):
        game = f"game{g:05d}"
        ev = f"EV{g:05d}"
        for team, price in (("home", 0.60), ("away", 0.44)):
            rows.append({
                "observation_key": f"KXNCAAFGAME-{ev}-{team}",
                "observation": {
                    "game_id": game, "kalshi_market_ticker": f"KXNCAAFGAME-{ev}-{team.upper()}",
                    "family": "moneyline", "team": team, "side": None, "threshold": None,
                    "semantic_operator": None, "model_probability": price,
                    "executable_yes_price": price, "executable_no_price": 1.0 - price + 0.10,
                    "market_midpoint": price, "pricing_status": "model_priced",
                    "parse_status": "confirmed_live", "captured_at": "2026-09-05T11:59:00Z",
                    "market_status": "active", "fee_status": "VERIFIED_CURRENT",
                    "fee_schedule_version": "v1", "model_version": {"model_version": "m1"},
                    "snapshot_timing": {"label": "T_24H", "hours_before_kickoff": 24.0},
                },
            })
        for team in ("home", "away"):
            for i in range(rungs):
                rows.append({
                    "observation_key": f"KXNCAAFSPREAD-{ev}-{team}{i}",
                    "observation": {
                        "game_id": game, "kalshi_market_ticker": f"KXNCAAFSPREAD-{ev}-{team.upper()}{i}",
                        "family": "spread", "team": team, "side": None, "threshold": 1.5 + 2 * i,
                        "semantic_operator": ">", "model_probability": max(0.05, 0.80 - 0.05 * i),
                        "executable_yes_price": max(0.05, 0.78 - 0.05 * i),
                        "executable_no_price": min(0.95, 0.30 + 0.05 * i),
                        "market_midpoint": 0.5, "pricing_status": "model_priced",
                        "parse_status": "confirmed_live", "captured_at": "2026-09-05T11:59:00Z",
                        "market_status": "active", "fee_status": "VERIFIED_CURRENT",
                        "fee_schedule_version": "v1", "model_version": {"model_version": "m1"},
                        "snapshot_timing": {"label": "T_24H", "hours_before_kickoff": 24.0},
                    },
                })
        for i in range(rungs):
            rows.append({
                "observation_key": f"KXNCAAFTOTAL-{ev}-{i}",
                "observation": {
                    "game_id": game, "kalshi_market_ticker": f"KXNCAAFTOTAL-{ev}-{i}",
                    "family": "total", "team": None, "side": "over", "threshold": 40.5 + 3 * i,
                    "semantic_operator": ">", "model_probability": max(0.05, 0.85 - 0.06 * i),
                    "executable_yes_price": max(0.05, 0.83 - 0.06 * i),
                    "executable_no_price": min(0.95, 0.25 + 0.06 * i),
                    "market_midpoint": 0.5, "pricing_status": "model_priced",
                    "parse_status": "confirmed_live", "captured_at": "2026-09-05T11:59:00Z",
                    "market_status": "active", "fee_status": "VERIFIED_CURRENT",
                    "fee_schedule_version": "v1", "model_version": {"model_version": "m1"},
                    "snapshot_timing": {"label": "T_24H", "hours_before_kickoff": 24.0},
                },
            })
    return rows


def _write(tmp_path: Path, rows) -> Path:
    path = tmp_path / "obs.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


# --- THE zero-actionable invariant (section 25) --------------------------


def test_end_to_end_produces_zero_actionable_output(tmp_path):
    """The load-bearing safety test: candidates form, groups resolve, and
    NOTHING becomes actionable. Deliberately uses a universe engineered to
    pass every data-quality gate -- fresh quotes, active markets, verified
    fees, resolved semantics -- so the zero comes from the threshold
    boundary rather than from incidental data problems."""
    loaded = load_contract_snapshots(_write(tmp_path, _corpus_rows(25)))
    config = EligibilityConfig(max_quote_age_seconds=86_400)
    result = run_pipeline(loaded.snapshots, config=config, now=NOW)

    # Structure genuinely formed.
    assert len(result.candidates) > 500
    assert len(result.dedup_view.equivalence_clusters) > 100
    assert result.dedup_view.dominated_count >= 0
    assert result.concentration.tally.max_per_game > 0

    # Every quality gate passed for at least some candidates...
    clean = [r for r in result.eligibility_results if not r.quality_failures]
    assert clean, "no candidate passed the data-quality gates; the zero below would be uninformative"

    # ...and still nothing is actionable.
    assert result.card.actionable_count == 0
    assert result.card.entries == ()
    assert all(r.actionable is False for r in result.eligibility_results)
    assert all(r.status == QUALIFICATION_DISABLED for r in result.eligibility_results)


def test_no_stake_or_execution_value_appears_anywhere(tmp_path):
    loaded = load_contract_snapshots(_write(tmp_path, _corpus_rows(3)))
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=NOW)
    blob = json.dumps(
        {
            "card_status": result.card.status,
            "actionable": result.card.actionable_count,
            "ceiling": result.card.maximum_acceptable_price.status,
            "shadow": result.card.shadow_status,
            "risk": result.concentration.status,
        }
    ).lower()
    for token in ("stake", "bankroll", "kelly", "allocate", "order", "wager"):
        assert token not in blob


def test_card_entries_type_is_an_empty_tuple():
    """A future implementation must widen this deliberately; an entry
    cannot appear because a condition drifted."""
    annotation = card.ResearchCard.__dataclass_fields__["entries"].type
    assert "tuple[()]" in str(annotation)


# --- No magic profitability thresholds (section 26) ----------------------

ALLOWED_FRACTIONAL_CONSTANTS: dict[str, set[float]] = {
    # The pivot between the two American-odds conventions. A property of
    # the notation, not a decision boundary -- odds.py contains no
    # eligibility logic at all (asserted separately below).
    "cfb_edge_finder.recommendation.odds": {0.5},
}


def _fractional_constants(module) -> set[float]:
    """Float literals strictly between 0 and 1 -- the range a probability
    or ROI cutoff would live in. 0.0 and 1.0 are structural bounds, not
    thresholds, and are excluded."""
    found: set[float] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            if 0.0 < node.value < 1.0:
                found.add(node.value)
    return found


@pytest.mark.parametrize("module", SKELETON_MODULES, ids=lambda m: m.__name__)
def test_no_hard_coded_profitability_threshold(module):
    """Qualification must come from a versioned empirical artifact, never
    a magic number in source. This scans for exactly the shape such a
    number would take: a bare fraction like 0.05, 0.08 or 0.10."""
    allowed = ALLOWED_FRACTIONAL_CONSTANTS.get(module.__name__, set())
    offending = _fractional_constants(module) - allowed
    assert not offending, (
        f"{module.__name__} contains unexplained fractional constant(s) {sorted(offending)}; a "
        f"profitability cutoff must live in a reviewed threshold artifact, not in source"
    )


def test_the_specific_cutoffs_the_mission_warns_about_are_absent():
    suspicious = {0.05, 0.08, 0.10, 0.02, 0.03, 0.07, 0.15}
    for module in SKELETON_MODULES:
        found = _fractional_constants(module) & suspicious
        assert not found, f"{module.__name__} hard-codes {sorted(found)}"


def test_odds_module_contains_no_eligibility_logic():
    """Justifies its allowlist entry: it cannot gate anything.

    Scans CODE identifiers, not raw source -- the module's own docstring
    says it "has no eligibility logic, reads no thresholds", and a text
    scan would flag the very sentence documenting the boundary."""
    identifiers = {i.lower() for i in _code_identifiers(odds)}
    for token in ("eligib", "qualif", "candidate", "threshold", "actionable"):
        hits = [i for i in identifiers if token in i]
        assert not hits, f"odds module references gating surface: {hits}"
    # It also imports nothing from the rest of the skeleton.
    for node in ast.walk(ast.parse(inspect.getsource(odds))):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "recommendation" not in node.module


def test_threshold_artifact_values_are_empty_by_default():
    provenance = thresholds.ThresholdProvenance(
        source_corpus_identifier="c", prospective_only=True, settled_game_count=1,
        created_at=NOW, analytics_code_version="a", model_version="m",
        approval_state=thresholds.ApprovalState.DRAFT_RESEARCH,
    )
    artifact = thresholds.ThresholdArtifact(
        artifact_version="v", provenance=provenance,
        applicable_model_versions=frozenset(), applicable_timing_labels=frozenset(),
        applicable_families=frozenset(),
    )
    assert artifact.values == {}


# --- No automatic optimization (section 27) ------------------------------


@pytest.mark.parametrize("module", SKELETON_MODULES, ids=lambda m: m.__name__)
def test_no_threshold_optimizer_exists(module):
    """Threshold research must be deliberate, holdout-aware and reviewed --
    never a function that maximizes a metric and returns a cutoff."""
    banned = (
        "optimize", "maximize", "find_best", "best_cutoff", "tune", "search_threshold",
        "auto_approve", "promote",
    )
    for name in dir(module):
        if name.startswith("_"):
            continue
        lowered = name.lower()
        for token in banned:
            assert token not in lowered, f"{module.__name__}.{name} looks like an optimizer"


def test_no_api_auto_approves_an_artifact():
    source = inspect.getsource(thresholds)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert "approve" not in node.name.lower(), f"{node.name} could auto-approve an artifact"


def test_no_code_path_constructs_an_approved_for_live_artifact():
    """The enum member exists so the contract is expressible, and
    `LIVE_APPROVAL_STATES` legitimately REFERENCES it -- that reference is
    the gate doing its job. What must not exist is any code that ASSIGNS
    it, i.e. mints an artifact already approved for live use.

    Scoped to construction (keyword assignment) rather than mention,
    because forbidding the mention would forbid the check itself."""
    for module in SKELETON_MODULES:
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if not isinstance(node, ast.keyword):
                continue
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "APPROVED_FOR_LIVE"
                and node.arg == "approval_state"
            ):
                raise AssertionError(
                    f"{module.__name__} constructs provenance already APPROVED_FOR_LIVE"
                )


def test_live_approval_gate_is_a_membership_check_not_a_default():
    """The only permitted use of APPROVED_FOR_LIVE: deciding whether a
    supplied artifact qualifies."""
    assert thresholds.ApprovalState.APPROVED_FOR_LIVE in thresholds.LIVE_APPROVAL_STATES
    assert thresholds.ApprovalState.DRAFT_RESEARCH in thresholds.NON_LIVE_APPROVAL_STATES
    assert thresholds.ApprovalState.APPROVED_FOR_SHADOW in thresholds.NON_LIVE_APPROVAL_STATES


# --- Package-level surface ------------------------------------------------


def _code_identifiers(module) -> set[str]:
    """AST-based, excluding string literals: these modules' docstrings
    describe the boundary in prose, and a text scan would flag the very
    sentences documenting it."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


@pytest.mark.parametrize("module", SKELETON_MODULES, ids=lambda m: m.__name__)
def test_no_sizing_or_execution_surface(module):
    forbidden = (
        "stake", "bankroll", "kelly", "allocate", "portfolio_optimize", "place_order",
        "submit_order", "execute_trade", "wager", "bet_size", "tier_a", "tier_b",
    )
    identifiers = {i.lower() for i in _code_identifiers(module)}
    for token in forbidden:
        hits = [i for i in identifiers if token in i]
        assert not hits, f"{module.__name__} exposes {hits}"


@pytest.mark.parametrize("module", SKELETON_MODULES, ids=lambda m: m.__name__)
def test_skeleton_imports_no_trading_layer(module):
    imported: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert "betting" not in name, f"{module.__name__} imports {name}"


# --- Scale (section 31) ---------------------------------------------------


@pytest.mark.parametrize("n_games,rungs,label", [(100, 12, "~5k"), (500, 12, "~25k")])
def test_pipeline_scales_without_quadratic_comparison(tmp_path, n_games, rungs, label):
    loaded = load_contract_snapshots(_write(tmp_path, _corpus_rows(n_games, rungs)))
    started = time.perf_counter()
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=NOW)
    elapsed = time.perf_counter() - started

    assert loaded.ledger_load_count == 1
    assert result.card.actionable_count == 0
    # Deliberately enormous: trips only on a genuine O(n^2) regression.
    assert elapsed < 120.0, f"{label} took {elapsed:.1f}s"


def test_no_quadratic_blowup(tmp_path):
    timings = {}
    for n_games in (50, 200):
        path = _write(tmp_path / f"g{n_games}", _corpus_rows(n_games, 12)) if False else None
        sub = tmp_path / f"g{n_games}"
        sub.mkdir()
        path = _write(sub, _corpus_rows(n_games, 12))
        loaded = load_contract_snapshots(path)
        started = time.perf_counter()
        run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=NOW)
        timings[n_games] = max(time.perf_counter() - started, 1e-3)
    ratio = timings[200] / timings[50]
    assert ratio < 12.0, f"4x the data cost {ratio:.1f}x the time -- looks superlinear"


def test_memory_stays_bounded(tmp_path):
    loaded = load_contract_snapshots(_write(tmp_path, _corpus_rows(500, 12)))
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=NOW)
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert len(result.candidates) > 20_000
    assert (after - before) / 1024 < 2000
