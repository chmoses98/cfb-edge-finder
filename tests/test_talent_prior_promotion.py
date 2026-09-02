"""Safety tests for the 2026-09-02 promotion of the early-season talent
margin prior into the production control path.

These pin the properties the model-repair mission's Phase 14 requires:
CONTROL 0.4.0 stays reproducible, the new model is distinguishable, the
prior is leakage-safe and bounded, totals and settlement semantics are
untouched, and -- most important operationally -- a model-version change
can never retroactively re-capture historical checkpoints.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tests"))
sys.path.insert(0, str(_ROOT / "scripts"))

import capture_kalshi_cfb_snapshot as milestone_d  # noqa: E402
from scan_harness import make_games, make_history_lines  # noqa: E402

from cfb_edge_finder.kalshi.game_projection_cache import (  # noqa: E402
    GameProjectionCache,
    GameProjectionRequest,
)
from cfb_edge_finder.modeling.leakage import AsOf  # noqa: E402
from cfb_edge_finder.modeling.ratings import fit_fbs_efficiency_ratings  # noqa: E402
from cfb_edge_finder.modeling.score_model import (  # noqa: E402
    apply_margin_correction,
    project_game,
)
from cfb_edge_finder.modeling.talent_prior import (  # noqa: E402
    TALENT_BETA,
    talent_margin_delta,
)
from cfb_edge_finder.research.persistence import load_observation_index  # noqa: E402
from cfb_edge_finder.research.preseason.control import CONTROL_MODEL_VERSION  # noqa: E402
from cfb_edge_finder.research.preseason.shadow_prior import (  # noqa: E402
    TALENT_BETA as SHADOW_TALENT_BETA,
)

# --- 10. the frozen beta is exactly the researched one --------------------


def test_talent_beta_is_exactly_the_frozen_researched_value():
    assert TALENT_BETA == 0.018993


def test_production_beta_equals_the_research_beta_exactly():
    """Production may not import the research package (a one-way
    boundary this repo enforces with its own guard tests), so the
    constant is duplicated -- and that duplication is policed HERE.
    If these could drift apart unnoticed, every historical result that
    justified the promotion would silently stop applying."""
    assert TALENT_BETA == SHADOW_TALENT_BETA


# --- 2. the new model version is distinct, and resolved honestly ----------


def test_model_versions_are_distinct_and_control_is_never_overwritten():
    assert milestone_d.MODEL_VERSION == "0.4.0-milestone-c2-live-margin-correction"
    assert milestone_d.TALENT_PRIOR_MODEL_VERSION == "0.5.0-early-season-talent-prior"
    assert milestone_d.MODEL_VERSION != milestone_d.TALENT_PRIOR_MODEL_VERSION
    assert CONTROL_MODEL_VERSION == milestone_d.MODEL_VERSION


def test_model_version_describes_the_arithmetic_that_actually_ran():
    """A run with no talent genuinely IS the control and must say so."""
    assert milestone_d.resolve_model_version(True) == milestone_d.TALENT_PRIOR_MODEL_VERSION
    assert milestone_d.resolve_model_version(False) == milestone_d.MODEL_VERSION


# --- the delta itself -----------------------------------------------------


def test_delta_is_linear_in_the_differential_and_antisymmetric():
    assert talent_margin_delta(800.0, 500.0) == pytest.approx(TALENT_BETA * 300.0)
    assert talent_margin_delta(500.0, 800.0) == pytest.approx(-TALENT_BETA * 300.0)
    assert talent_margin_delta(700.0, 700.0) == 0.0


def test_missing_talent_is_a_no_op_never_an_imputed_average():
    assert talent_margin_delta(None, 500.0) == 0.0
    assert talent_margin_delta(800.0, None) == 0.0
    assert talent_margin_delta(None, None) == 0.0


# --- 1/5/6. control reproducibility, monotonicity, boundedness ------------


def _projection(talent=None):
    games, _classification = make_games(2)
    lines = make_history_lines(games)
    as_of = AsOf(season=2026, week=1)
    history = [ln for ln in lines if ln.as_of.is_strictly_before(as_of)]
    ratings = fit_fbs_efficiency_ratings(history, as_of)
    pool = np.column_stack([np.random.default_rng(3).normal(0, 13, 800),
                            np.random.default_rng(4).normal(0, 13, 800)])
    g = games[0]
    raw = project_game(
        home_id=g.home_team_id, away_id=g.away_team_id,
        home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None,
        residual_pool=pool, home_percent_passing_ppa=None, away_percent_passing_ppa=None,
        n_simulations=2000, seed=0,
    )
    return apply_margin_correction(
        raw, is_fbs_vs_fbs=True, method="none", correction_model=None,
        artifact_version=None, as_of=as_of, training_cutoff=None,
        **({"talent_margin_delta": talent} if talent is not None else {}),
    )


def test_control_is_byte_identical_when_no_talent_is_supplied():
    """CONTROL 0.4.0 remains reproducible THROUGH THE PATCHED CODE --
    the default of 0.0 is what makes that true by construction."""
    control = _projection()
    assert control.talent_margin_delta == 0.0
    assert control.total_margin_delta == control.margin_delta
    d = control.to_game_distribution()
    assert d.home_mean == pytest.approx(control.raw.to_game_distribution().home_mean)
    assert d.away_mean == pytest.approx(control.raw.to_game_distribution().away_mean)


def test_talent_delta_moves_margin_and_leaves_total_exactly_unchanged():
    control, cand = _projection(), _projection(talent=6.0)
    assert cand.expected_margin == pytest.approx(control.expected_margin + 6.0)
    assert cand.expected_total == pytest.approx(control.expected_total)
    dc, dd = control.to_game_distribution(), cand.to_game_distribution()
    assert dd.home_mean + dd.away_mean == pytest.approx(dc.home_mean + dc.away_mean)
    assert dd.home_sd == pytest.approx(dc.home_sd)
    assert dd.away_sd == pytest.approx(dc.away_sd)
    assert dd.correlation == pytest.approx(dc.correlation)


def test_threshold_probabilities_stay_monotonic_and_bounded():
    cand = _projection(talent=7.5)
    thresholds = [-40.0, -20.0, -7.5, 0.0, 7.5, 20.0, 40.0]
    probs = [cand.prob_margin_greater_than(t) for t in thresholds]
    assert all(0.0 <= p <= 1.0 for p in probs)
    assert all(np.isfinite(p) for p in probs)
    assert probs == sorted(probs, reverse=True)


def test_extreme_talent_gaps_stay_finite_and_bounded():
    for delta in (-500.0, -80.0, 80.0, 500.0):
        cand = _projection(talent=delta)
        for t in (-60.0, 0.0, 60.0):
            p = cand.prob_margin_greater_than(t)
            assert 0.0 <= p <= 1.0 and np.isfinite(p)


# --- the prior is FBS-vs-FBS only, matching its fit population ------------


def test_prior_is_not_applied_to_non_fbs_games():
    games, _ = make_games(2)
    g = games[0]
    lines = make_history_lines(games)
    talent = {g.home_team_id: 900.0, g.away_team_id: 400.0}
    cache = GameProjectionCache(lines, talent_by_team=talent)
    req = GameProjectionRequest(
        game_id=g.game_id, home_id=g.home_team_id, away_id=g.away_team_id,
        home_classification="fbs", away_classification="fcs",
        is_neutral_site=False, as_of_season=2026, as_of_week=1,
        n_simulations=800, seed=0,
    )
    assert cache.get_or_build(req).projection.talent_margin_delta == 0.0


def test_prior_is_applied_for_fbs_vs_fbs_and_matches_the_frozen_formula():
    games, _ = make_games(2)
    g = games[0]
    lines = make_history_lines(games)
    talent = {g.home_team_id: 900.0, g.away_team_id: 400.0}
    cache = GameProjectionCache(lines, talent_by_team=talent)
    req = GameProjectionRequest(
        game_id=g.game_id, home_id=g.home_team_id, away_id=g.away_team_id,
        home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, as_of_season=2026, as_of_week=1,
        n_simulations=800, seed=0,
    )
    got = cache.get_or_build(req).projection.talent_margin_delta
    assert got == pytest.approx(TALENT_BETA * 500.0)


def test_cache_without_talent_reproduces_the_control_exactly():
    games, _ = make_games(2)
    g = games[0]
    lines = make_history_lines(games)
    req = GameProjectionRequest(
        game_id=g.game_id, home_id=g.home_team_id, away_id=g.away_team_id,
        home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, as_of_season=2026, as_of_week=1,
        n_simulations=800, seed=0,
    )
    control = GameProjectionCache(lines).get_or_build(req).projection
    with_empty = GameProjectionCache(lines, talent_by_team={}).get_or_build(req).projection
    assert control.talent_margin_delta == 0.0
    assert with_empty.talent_margin_delta == 0.0
    assert control.expected_margin == pytest.approx(with_empty.expected_margin)


# --- 3/4. the prospective boundary: THE critical operational property -----


def test_model_version_change_cannot_recapture_historical_checkpoints(tmp_path):
    """`labels_by_ticker` is keyed by (ticker, label) and is deliberately
    NOT model-version aware.

    This is what stops a promotion from re-pricing the whole back
    catalogue under the new version: a label already captured under
    0.4.0 still reads as captured when the scanner runs as 0.5.0, so
    `resolve_due_labels` never re-offers it. If this ever became
    version-scoped, the first 0.5.0 run would emit a new row for every
    historical checkpoint and the corpus would silently gain thousands
    of retroactive observations."""
    path = tmp_path / "2026.jsonl"
    rows = [
        {"observation_key": "k1", "season": 2026,
         "observation": {"kalshi_market_ticker": "T-1", "game_id": "g1",
                         "snapshot_timing": {"label": "T_6H"},
                         "model_version": {"model_version": "0.4.0-milestone-c2-live-margin-correction"}}},
        {"observation_key": "k2", "season": 2026,
         "observation": {"kalshi_market_ticker": "T-1", "game_id": "g1",
                         "snapshot_timing": {"label": "CLOSING"},
                         "model_version": {"model_version": "0.4.0-milestone-c2-live-margin-correction"}}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    index = load_observation_index(path)
    assert index.captured_labels_for("T-1") == {"T_6H", "CLOSING"}


def test_old_control_rows_are_never_reinterpreted_as_candidate_output(tmp_path):
    """A 0.4.0 row stays a 0.4.0 row. Nothing in the promotion rewrites
    or re-labels an existing observation."""
    path = tmp_path / "2026.jsonl"
    original = {"observation_key": "k1", "season": 2026,
                "observation": {"kalshi_market_ticker": "T-1", "game_id": "g1",
                                "snapshot_timing": {"label": "T_6H"},
                                "model_version": {"model_version": milestone_d.MODEL_VERSION}}}
    raw = json.dumps(original) + "\n"
    path.write_text(raw, encoding="utf-8")
    load_observation_index(path)
    assert path.read_text(encoding="utf-8") == raw
    reread = json.loads(path.read_text(encoding="utf-8").strip())
    assert reread["observation"]["model_version"]["model_version"] == milestone_d.MODEL_VERSION
    assert reread["observation"]["model_version"]["model_version"] != milestone_d.TALENT_PRIOR_MODEL_VERSION


# --- 11/12/13/14. nothing else moved -------------------------------------


def _prior_source() -> str:
    return (_ROOT / "src" / "cfb_edge_finder" / "modeling" / "talent_prior.py").read_text()


def _prior_code_lines() -> list[str]:
    """Executable lines only. Asserting against the raw file would match
    the module's own prose about what it deliberately does NOT do, which
    would fail for exactly the wrong reason."""
    import ast

    tree = ast.parse(_prior_source())
    return [ast.unparse(node) for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Call, ast.Assign, ast.FunctionDef))]


def test_week_1_2026_outcomes_are_absent_from_the_prior():
    """The prior is a frozen constant times a preseason composite. It
    contains no fitted term at all, so no future outcome can enter it."""
    code = " ".join(_prior_code_lines())
    for forbidden in ("actual_", "final_score", "home_points", "away_points", "lstsq", "polyfit"):
        assert forbidden not in code, f"talent_prior.py must contain no fitting surface ({forbidden})"


def test_family_semantics_fee_and_settlement_modules_untouched_by_the_prior():
    """The prior may only reach the margin channel. If it ever imported a
    contract-semantics, fee, settlement or timing module, a pricing
    change could silently become a settlement or scheduling change."""
    import ast

    tree = ast.parse(_prior_source())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    for forbidden in ("fee_schedule", "settlement", "contract_semantics", "executable_price", "timing"):
        assert not any(forbidden in m for m in imported), f"talent_prior.py must not import {forbidden}"
    # It imports NOTHING beyond __future__. The production pricing path
    # must not grow a dependency -- least of all on the research package,
    # which this repo's one-way boundary forbids outright.
    assert [m for m in imported if m != "__future__"] == []
