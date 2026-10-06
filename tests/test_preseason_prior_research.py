"""Preseason-prior research harness: control freeze, leakage guards,
walk-forward discipline, and proof that none of it reaches production.

The load-bearing tests are the ones that try to LEAK and expect a raise.
A guard that has only been tested with well-formed inputs has not been
tested.
"""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys

import pytest

from cfb_edge_finder.modeling.leakage import AsOf
from cfb_edge_finder.research.preseason.ablation import (
    ABLATION_VERSION,
    WALK_FORWARD_SPLIT,
    CandidateVerdict,
    ConfirmationLedger,
    ConfirmationSpentError,
    EffectType,
    assert_control_unchanged,
    blocked_candidate,
    classify_effect,
)
from cfb_edge_finder.research.preseason.control import (
    CONTROL_BASELINE_SHA256,
    CONTROL_MODEL_VERSION,
    control_has_drifted,
    control_manifest,
)
from cfb_edge_finder.research.preseason.evaluation import (
    GamePrediction,
    margin_metrics,
    paired_comparison,
    total_metrics,
    winner_metrics,
)
from cfb_edge_finder.research.preseason.features import (
    FeatureFamily,
    FeatureTable,
    LeakageViolation,
    PreseasonFeature,
    coaching_change_features,
    returning_production_features,
    talent_features,
)
from cfb_edge_finder.research.preseason.sources import (
    SOURCE_AUDIT,
    Verdict,
    rejected_families,
    usable_families,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "cfb_edge_finder"
PRESEASON = "cfb_edge_finder.research.preseason"


def imports_of(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ------------------------------------------------ control freeze


def test_the_control_matches_its_frozen_hash():
    """THE GUARD. If a production parameter changes mid-research, every
    comparison becomes invalid and this test says so immediately."""
    assert not control_has_drifted(CONTROL_BASELINE_SHA256)
    assert control_manifest().content_hash() == CONTROL_BASELINE_SHA256


def test_the_control_records_the_week1_zero_carryover():
    """The single most important control behaviour for this research."""
    payload = control_manifest().payload
    assert payload["priors"]["week1_carryover_weight"] == 0.0
    assert payload["priors"]["carryover_weight_by_games_played"]["0"] == 0.0


def test_the_control_records_that_no_preseason_info_moves_the_point_estimate():
    payload = control_manifest().payload
    assert payload["preseason_information_used_in_point_estimate"] == []
    assert payload["preseason_information_used_in_uncertainty_only"] == ["qb_continuity_proxy"]
    assert payload["qb_continuity_proxy"]["affects_point_estimate"] is False


def test_control_drift_is_detected():
    assert control_has_drifted("0" * 64)


def test_the_control_module_only_reads_production():
    """It must not redefine a parameter -- a second source of truth could
    disagree with the model actually being run."""
    src = (SRC / "research" / "preseason" / "control.py").read_text()
    tree = ast.parse(src)
    assigned = {
        t.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    for production_param in (
        "DEFAULT_RIDGE_LAMBDA", "DEFAULT_SEASON_SHRINKAGE_K", "DEFAULT_RESIDUAL_SCALE",
        "EARLY_SEASON_UNCERTAINTY_SCALE", "FROZEN_MARGIN_CORRECTION_PARAMS",
    ):
        assert production_param not in assigned, production_param


def test_the_control_manifest_is_deterministic():
    assert control_manifest().content_hash() == control_manifest().content_hash()


# ----------------------------------------- research cannot touch production


@pytest.mark.parametrize("package", ["modeling", "projections", "ratings", "recommendation", "kalshi"])
def test_no_production_package_imports_the_preseason_research(package):
    """The research reads production; production must never read the
    research. Otherwise a candidate feature could reach live pricing."""
    root = SRC / package
    if not root.exists():
        pytest.skip(f"{package} absent")
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in sorted(root.rglob("*.py"))
        if any(m.startswith(PRESEASON) for m in imports_of(p))
    ]
    assert offenders == [], f"production imports research: {offenders}"


def test_the_research_never_assigns_a_production_parameter():
    root = SRC / "research" / "preseason"
    protected = {
        "model_probability", "projected_margin", "projected_total",
        "DEFAULT_RIDGE_LAMBDA", "DEFAULT_SEASON_SHRINKAGE_K", "hfa", "ridge_lambda",
    }
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            targets = node.targets if isinstance(node, ast.Assign) else []
            for t in targets:
                name = t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", None)
                assert name not in protected, f"{path.name} assigns {name}"


def test_running_the_research_cli_does_not_change_the_control():
    before = control_manifest().content_hash()
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "research_preseason_prior.py")],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    assert control_manifest().content_hash() == before


# ------------------------------------------------- leakage guards


def test_a_feature_derived_from_the_season_it_predicts_raises():
    """THE CORE LEAK. Off-by-one here would let a feature see the season
    it is forecasting and leave no trace in the output."""
    bad = PreseasonFeature(
        team_id="Team", family=FeatureFamily.TALENT, name="talent_composite", value=900.0,
        derived_from_season=2024, applies_to_season=2024, source_endpoint="/talent",
    )
    with pytest.raises(LeakageViolation, match="may never see the season it predicts"):
        bad.validate_for(AsOf(season=2024, week=1))


def test_a_feature_derived_from_a_later_season_raises():
    worse = PreseasonFeature(
        team_id="Team", family=FeatureFamily.TALENT, name="talent_composite", value=900.0,
        derived_from_season=2025, applies_to_season=2024, source_endpoint="/talent",
    )
    with pytest.raises(LeakageViolation):
        worse.validate_for(AsOf(season=2024, week=1))


def test_a_feature_used_for_the_wrong_season_raises():
    feature = PreseasonFeature(
        team_id="Team", family=FeatureFamily.TALENT, name="talent_composite", value=900.0,
        derived_from_season=2023, applies_to_season=2024, source_endpoint="/talent",
    )
    with pytest.raises(LeakageViolation, match="applies to season"):
        feature.validate_for(AsOf(season=2025, week=1))


def test_a_correctly_dated_feature_passes():
    feature = PreseasonFeature(
        team_id="Team", family=FeatureFamily.TALENT, name="talent_composite", value=900.0,
        derived_from_season=2023, applies_to_season=2024, source_endpoint="/talent",
    )
    feature.validate_for(AsOf(season=2024, week=1))  # must not raise


def test_returning_production_is_dated_to_the_prior_season():
    """CFBD indexes /player/returning by the season it APPLIES TO while
    the production described is the prior season's."""
    features = returning_production_features(
        [{"team": "A", "percent_passing_ppa": 0.8}],
        applies_to_season=2024, splits=("percent_passing_ppa",),
    )
    assert features[0].derived_from_season == 2023
    assert features[0].applies_to_season == 2024
    features[0].validate_for(AsOf(season=2024, week=1))


def test_talent_is_dated_to_the_prior_signing_cycle():
    features = talent_features([{"school": "A", "talent": 900.0}], applies_to_season=2024)
    assert features[0].derived_from_season == 2023


def test_coaching_change_reads_only_the_two_relevant_seasons():
    """Reading season S+1 would reveal whether the hire worked out."""
    coaches = {2023: {"A": "Old"}, 2024: {"A": "New"}, 2025: {"A": "Newer"}}
    features = coaching_change_features(coaches, applies_to_season=2024)
    assert len(features) == 1
    assert features[0].value is True
    assert features[0].derived_from_season == 2023


def test_a_returning_coach_is_not_flagged_as_a_change():
    coaches = {2023: {"A": "Same"}, 2024: {"A": "Same"}}
    assert coaching_change_features(coaches, applies_to_season=2024)[0].value is False


def test_an_unknown_prior_coach_yields_none_not_a_manufactured_change():
    """'No prior record' is not evidence of a new coach. Defaulting it to
    True would invent coaching changes out of missing data."""
    coaches = {2024: {"A": "Someone"}}
    assert coaching_change_features(coaches, applies_to_season=2024)[0].value is None


def test_missing_values_are_never_imputed():
    """Imputing a league average would assert that an unknown team is
    average -- a modelling claim, not data cleaning."""
    features = returning_production_features(
        [{"team": "A"}], applies_to_season=2024, splits=("percent_passing_ppa",)
    )
    assert features[0].value is None
    assert not features[0].is_present


def test_the_feature_table_rejects_a_season_mismatch():
    feature = PreseasonFeature(
        team_id="A", family=FeatureFamily.TALENT, name="t", value=1.0,
        derived_from_season=2022, applies_to_season=2023, source_endpoint="/talent",
    )
    with pytest.raises(LeakageViolation):
        FeatureTable.build([feature], applies_to_season=2024)


def test_the_feature_table_validates_on_every_lookup():
    feature = PreseasonFeature(
        team_id="A", family=FeatureFamily.TALENT, name="t", value=1.0,
        derived_from_season=2024, applies_to_season=2024, source_endpoint="/talent",
    )
    table = FeatureTable.build([feature], applies_to_season=2024)
    with pytest.raises(LeakageViolation):
        table.get("A", "t", target=AsOf(season=2024, week=1))


def test_feature_coverage_is_reported():
    """A candidate must not be evaluated on a mostly-missing feature
    without that being visible."""
    rows = [{"team": "A", "x": 1.0}, {"team": "B"}, {"team": "C", "x": 2.0}]
    table = FeatureTable.build(
        returning_production_features(rows, applies_to_season=2024, splits=("x",)),
        applies_to_season=2024,
    )
    assert table.coverage("returning_x") == (2, 3)


# --------------------------------------------- source audit


def test_qb_identity_is_rejected_as_retroactively_revised():
    """A roster queried today reflects every later transfer."""
    assert rejected_families()["qb_identity"] == Verdict.UNUSABLE_RETROACTIVE_REVISION.value


def test_transfer_portal_is_recorded_unavailable_not_approximated():
    assert rejected_families()["transfer_portal"] == Verdict.UNAVAILABLE_NO_SOURCE.value


def test_preseason_ratings_stay_disqualified_while_timing_is_unconfirmed():
    """The highest-value leak available, so it stays out until someone
    can confirm the pre/post-week semantics."""
    assert (
        rejected_families()["preseason_ratings_sp_elo_srs"]
        == Verdict.UNUSABLE_TIMING_UNCONFIRMED.value
    )


def test_betting_lines_are_evaluation_only_and_never_a_feature():
    audit = next(a for a in SOURCE_AUDIT if a.family == "historical_betting_lines")
    assert audit.verdict is Verdict.USABLE_EVALUATION_ONLY
    assert not audit.usable_as_model_feature


def test_weather_is_excluded_from_preseason_research():
    assert rejected_families()["weather"] == Verdict.UNUSABLE_POSTGAME.value


def test_injuries_are_recorded_as_a_blind_spot():
    assert rejected_families()["injuries_suspensions"] == Verdict.UNAVAILABLE_NO_SOURCE.value


def test_every_audited_source_states_a_rationale():
    for audit in SOURCE_AUDIT:
        assert len(audit.rationale) > 40, audit.family


def test_the_usable_set_is_exactly_what_is_expected():
    assert usable_families() == (
        "coaching_change",
        "prior_season_final_scores",
        "returning_production_broader",
        "returning_production_passing",
        "talent_composite",
    )


# ------------------------------------- walk-forward discipline


def test_the_split_is_chronological_and_holds_back_the_latest_season():
    assert max(WALK_FORWARD_SPLIT.development_seasons) < WALK_FORWARD_SPLIT.selection_season
    assert WALK_FORWARD_SPLIT.selection_season < WALK_FORWARD_SPLIT.confirmation_season


def test_2020_is_excluded_rather_than_pooled():
    assert 2020 in WALK_FORWARD_SPLIT.excluded_seasons
    assert 2020 not in WALK_FORWARD_SPLIT.development_seasons
    assert WALK_FORWARD_SPLIT.role_of(2020) == "EXCLUDED"


def test_confirmation_can_only_be_spent_once():
    """A candidate that failed confirmation is rejected; retuning it and
    re-running turns confirmation into development."""
    ledger = ConfirmationLedger()
    ledger.spend("candidate_a")
    with pytest.raises(ConfirmationSpentError, match="already been evaluated"):
        ledger.spend("candidate_a")


def test_different_candidates_may_each_spend_once():
    ledger = ConfirmationLedger()
    ledger.spend("a")
    ledger.spend("b")
    assert ledger.spent == {"a", "b"}


def test_a_blocked_candidate_is_not_a_rejection():
    """A rejection asserts a measurement. None was made."""
    result = blocked_candidate("x", "no data")
    assert result.verdict is CandidateVerdict.BLOCKED_NO_HISTORICAL_DATA
    assert result.effect_type is EffectType.UNDETERMINED_NO_DATA
    assert not result.promotes_to_shadow


def test_only_a_confirmed_acceptance_promotes_to_shadow():
    from cfb_edge_finder.research.preseason.ablation import CandidateResult

    for verdict in CandidateVerdict:
        result = CandidateResult("x", verdict, EffectType.POINT_ESTIMATE)
        assert result.promotes_to_shadow == (verdict is CandidateVerdict.ACCEPT_CONFIRMED)


def test_assert_control_unchanged_passes_on_the_frozen_control():
    assert_control_unchanged()


def test_results_carry_the_control_they_were_measured_against():
    result = blocked_candidate("x", "no data")
    assert result.control_model_version == CONTROL_MODEL_VERSION
    assert result.control_sha256 == CONTROL_BASELINE_SHA256
    assert result.ablation_version == ABLATION_VERSION


# ------------------------------------------------ effect typing


def test_effect_type_distinguishes_mean_from_uncertainty():
    improving = paired_comparison(
        metric="margin_mae", control_errors=[10.0] * 40, candidate_errors=[8.0] * 40
    )
    assert classify_effect(margin_comparison=improving, coverage_delta=0.0) is (
        EffectType.POINT_ESTIMATE
    )
    assert classify_effect(margin_comparison=None, coverage_delta=0.05) is EffectType.UNCERTAINTY
    assert classify_effect(margin_comparison=improving, coverage_delta=0.05) is EffectType.BOTH
    assert classify_effect(margin_comparison=None, coverage_delta=0.0) is EffectType.NEITHER


def test_small_coverage_wobble_is_not_read_as_an_uncertainty_effect():
    assert classify_effect(margin_comparison=None, coverage_delta=0.005) is EffectType.NEITHER


# --------------------------------------------------- metrics


def prediction(margin: float, actual: int, prob: float = 0.6, **kw) -> GamePrediction:
    base = dict(
        game_id="g", season=2024, week=1, home_win_probability=prob,
        projected_margin=margin, projected_total=50.0,
        actual_home_margin=actual, actual_total=48,
    )
    base.update(kw)
    return GamePrediction(**base)


def test_a_zero_margin_counts_as_an_away_win():
    """Matches research/settlement.py. Restating it differently would
    make evaluation disagree with the ledger."""
    assert prediction(0.0, 0).home_won is False
    assert prediction(0.0, 1).home_won is True


def test_perfect_confident_predictions_score_near_zero_loss():
    perfect = [prediction(10, 10, prob=1 - 1e-9) for _ in range(20)]
    metrics = winner_metrics(perfect)
    assert metrics.log_loss < 1e-6
    assert metrics.brier < 1e-6


def test_margin_bias_has_the_expected_sign():
    """Projecting +10 when the result is +3 over-favours the home team,
    which must read as a positive bias."""
    over = [prediction(10.0, 3) for _ in range(30)]
    assert margin_metrics(over).bias == pytest.approx(7.0)
    assert margin_metrics(over).mae == pytest.approx(7.0)


def test_favorite_tail_bias_uses_only_big_projected_favorites():
    mixed = [prediction(20.0, 5) for _ in range(10)] + [prediction(2.0, 2) for _ in range(10)]
    assert margin_metrics(mixed).favorite_tail_bias == pytest.approx(15.0)


def test_total_metrics_are_signed_consistently():
    over = [prediction(3, 3, projected_total=60.0, actual_total=48) for _ in range(10)]
    assert total_metrics(over).bias == pytest.approx(12.0)


def test_empty_inputs_produce_nan_not_a_crash():
    assert winner_metrics([]).n == 0
    assert margin_metrics([]).n == 0
    assert total_metrics([]).n == 0


def test_paired_comparison_requires_identical_games():
    """Silently zipping mismatched lists would compare different games."""
    with pytest.raises(ValueError, match="identical games"):
        paired_comparison(metric="m", control_errors=[1.0, 2.0], candidate_errors=[1.0])


def test_a_clear_improvement_is_detected_as_improving():
    better = paired_comparison(
        metric="margin_mae",
        control_errors=[10.0 + i * 0.01 for i in range(200)],
        candidate_errors=[8.0 + i * 0.01 for i in range(200)],
    )
    assert better.mean_paired_difference == pytest.approx(-2.0)
    assert better.improves and not better.degrades


def test_a_clear_degradation_is_detected():
    worse = paired_comparison(
        metric="margin_mae",
        control_errors=[8.0 + i * 0.01 for i in range(200)],
        candidate_errors=[10.0 + i * 0.01 for i in range(200)],
    )
    assert worse.degrades and not worse.improves


def test_noise_is_not_reported_as_improvement():
    """A point estimate leaning the right way is not evidence."""
    import random

    rng = random.Random(11)
    control = [rng.gauss(10, 3) for _ in range(120)]
    candidate = [c + rng.gauss(0, 3) for c in control]
    result = paired_comparison(
        metric="margin_mae", control_errors=control, candidate_errors=candidate
    )
    assert not result.improves


# ------------------------------------------------------- CLI


def test_the_cli_reports_blocked_without_historical_data(tmp_path):
    result = subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "scripts" / "research_preseason_prior.py"),
            "--historical-cache", str(tmp_path / "absent"),
            "--json-out", str(tmp_path / "out.json"),
        ],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "out.json").read_text())
    assert payload["historical_data_available"] is False
    assert payload["any_candidate_promoted"] is False
    assert all(
        c["verdict"] == CandidateVerdict.BLOCKED_NO_HISTORICAL_DATA.value
        for c in payload["candidates"]
    )
    assert "BLOCKED is not REJECTED" in result.stdout


def test_the_cli_records_the_control_it_ran_against(tmp_path):
    subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "scripts" / "research_preseason_prior.py"),
            "--json-out", str(tmp_path / "out.json"),
        ],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    payload = json.loads((tmp_path / "out.json").read_text())
    assert payload["control"]["content_sha256"] == CONTROL_BASELINE_SHA256
    assert payload["walk_forward_split"]["excluded"] == [2020]


def test_no_shadow_model_was_created():
    """Part 13: do not create a shadow model just to create one."""
    assert not (SRC / "modeling" / "shadow_score_model.py").exists()
    assert not (SRC / "research" / "preseason" / "shadow_model.py").exists()
