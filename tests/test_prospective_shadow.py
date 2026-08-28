"""Prospective CONTROL-vs-SHADOW capture: frozen specs, canonical
immutability, fail-closed coverage, and the end-to-end lifecycle.

The load-bearing tests are the ones that try to corrupt 2026 as
confirmation evidence -- backfilling a shadow, drifting a spec, imputing
a missing talent value -- and expect a refusal.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from cfb_edge_finder.research.preseason.shadow_analytics import (
    PRIMARY_HYPOTHESIS,
    PROHIBITED,
    SETTLED_2026_GAMES_AT_REGISTRATION,
    EvidenceState,
    SettledShadowPair,
    compare,
    hypothesis_hash,
    hypothesis_manifest,
)
from cfb_edge_finder.research.preseason.shadow_capture import (
    SHADOW_RECORD_SCHEMA_VERSION,
    ShadowBackfillError,
    ShadowCoverageReport,
    ShadowUnavailableReason,
    build_shadow_record,
)
from cfb_edge_finder.research.preseason.shadow_prior import SHADOW_MODEL_VERSION, TALENT_BETA
from cfb_edge_finder.research.preseason.shadow_spec import (
    BETA_FIT_PROVENANCE,
    CONTROL_SPEC_SHA256,
    SHADOW_SPEC_SHA256,
    SpecDriftError,
    assert_specs_frozen,
    control_spec,
    shadow_spec,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "cfb_edge_finder"

KICKOFF = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)
CAPTURED = KICKOFF - timedelta(hours=24)
SAMPLES = np.array([-14.0, -7.0, -1.0, 0.0, 1.0, 7.0, 14.0, 21.0])


def record(**kw):
    base = dict(
        observation_key="k1",
        game_id="cfb-2026-wk01-away-at-home",
        timing_label="T_24H",
        captured_at=CAPTURED,
        kickoff_utc=KICKOFF,
        market_ticker="KXNCAAFGAME-T1",
        market_family="moneyline",
        executable_yes_price=0.55,
        executable_no_price=0.47,
        control_model_version="0.4.0-milestone-c2-live-margin-correction",
        control_probability=0.60,
        control_projected_margin=3.0,
        control_margin_samples=SAMPLES,
        talent_home=900.0,
        talent_away=800.0,
        talent_source_version="preseason_research_cache_v1",
        both_fbs=True,
        capture_mode="PROSPECTIVE",
    )
    base.update(kw)
    return build_shadow_record(**base)


def imports_of(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ------------------------------------------------ frozen specs


def test_both_arms_match_their_frozen_hashes():
    """If either arm drifts, 2026 stops being untouched confirmation
    evidence, and this fails immediately rather than silently."""
    assert control_spec().content_hash() == CONTROL_SPEC_SHA256
    assert shadow_spec().content_hash() == SHADOW_SPEC_SHA256
    assert_specs_frozen(control_sha256=CONTROL_SPEC_SHA256, shadow_sha256=SHADOW_SPEC_SHA256)


def test_spec_drift_is_refused():
    with pytest.raises(SpecDriftError, match="CONTROL specification"):
        assert_specs_frozen(control_sha256="0" * 64, shadow_sha256=SHADOW_SPEC_SHA256)
    with pytest.raises(SpecDriftError, match="SHADOW specification"):
        assert_specs_frozen(control_sha256=CONTROL_SPEC_SHA256, shadow_sha256="0" * 64)


def test_the_shadow_spec_forbids_refitting_on_2026():
    assert shadow_spec().payload["may_be_refit_on_2026"] is False
    assert shadow_spec().payload["prospective_season"] == 2026


def test_the_beta_fit_provenance_records_the_simulation_sensitivity():
    """A 'frozen' constant that cannot be reproduced from the cache
    without also knowing the fit-time simulation count is not fully
    frozen. Recorded rather than discovered later."""
    assert BETA_FIT_PROVENANCE["fit_time_n_simulations"] == 2000
    assert BETA_FIT_PROVENANCE["beta_at_fit_time"] == TALENT_BETA
    assert BETA_FIT_PROVENANCE["beta_refit_at_8000_simulations"] != TALENT_BETA
    assert "sensitivity_note" in BETA_FIT_PROVENANCE


def test_the_control_arm_is_labelled_canonical_and_the_shadow_is_not():
    assert "CANONICAL" in control_spec().payload["role"]
    assert "RESEARCH ONLY" in shadow_spec().payload["role"]
    assert shadow_spec().model_version != control_spec().model_version


# --------------------------------------- prospective-only


def test_a_non_prospective_capture_is_refused_outright():
    """THE BACKFILL GUARD. A shadow value on a retrospective row is a
    backfilled number wearing a prospective label."""
    with pytest.raises(ShadowBackfillError, match="backfilled number"):
        record(capture_mode="RETROSPECTIVE_BACKFILL")


def test_a_capture_at_or_after_kickoff_yields_no_shadow_value():
    for offset in (timedelta(0), timedelta(minutes=1), timedelta(days=1)):
        out = record(captured_at=KICKOFF + offset)
        assert not out.available
        assert out.unavailable_reason == ShadowUnavailableReason.CAPTURED_AT_OR_AFTER_KICKOFF.value
        assert out.shadow_projected_margin is None


def test_a_pre_kickoff_prospective_capture_produces_a_value():
    out = record()
    assert out.available
    assert out.unavailable_reason is None


# ------------------------------- fail closed, never impute


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"talent_home": None}, ShadowUnavailableReason.TALENT_MISSING_HOME),
        ({"talent_away": None}, ShadowUnavailableReason.TALENT_MISSING_AWAY),
        ({"talent_home": None, "talent_away": None}, ShadowUnavailableReason.TALENT_MISSING_BOTH),
        ({"both_fbs": False}, ShadowUnavailableReason.UNSUPPORTED_POPULATION),
        ({"control_probability": None}, ShadowUnavailableReason.CONTROL_NOT_PRICED),
        ({"control_projected_margin": None}, ShadowUnavailableReason.CONTROL_NOT_PRICED),
    ],
)
def test_every_unavailable_case_is_explicit_and_never_a_silent_zero(kw, reason):
    """'We had no talent data' and 'talent said these teams are equal'
    are different claims. A silent zero delta collapses them."""
    out = record(**kw)
    assert not out.available
    assert out.unavailable_reason == reason.value
    assert out.shadow_minus_control_margin is None
    assert out.shadow_projected_margin is None


def test_an_unavailable_shadow_still_records_the_control_values():
    """Canonical capture must never be blocked by shadow failure."""
    out = record(talent_home=None)
    assert out.control_probability == 0.60
    assert out.control_projected_margin == 3.0


def test_unsupported_population_is_refused_even_with_talent_present():
    """The candidate was validated on FBS-vs-FBS only and must not be
    extrapolated beyond it."""
    out = record(both_fbs=False, talent_home=900.0, talent_away=500.0)
    assert not out.available


# --------------------------------------- the adjustment


def test_the_shadow_margin_is_control_plus_beta_times_differential():
    out = record(talent_home=900.0, talent_away=800.0)
    assert out.talent_differential == pytest.approx(100.0)
    assert out.shadow_minus_control_margin == pytest.approx(TALENT_BETA * 100.0)
    assert out.shadow_projected_margin == pytest.approx(3.0 + TALENT_BETA * 100.0)


def test_the_shadow_probability_moves_with_the_shifted_margin():
    """Moving the margin while leaving the probability alone would
    produce an arm that contradicts itself."""
    out = record(talent_home=2000.0, talent_away=0.0)  # huge positive shift
    assert out.shadow_probability is not None
    assert out.shadow_probability > out.control_probability
    assert out.shadow_minus_control_probability > 0


def test_a_zero_simulated_margin_counts_as_an_away_win():
    """Matches research/settlement.py: strictly greater than zero."""
    out = record(talent_home=800.0, talent_away=800.0, control_margin_samples=np.array([0.0, 0.0]))
    assert out.shadow_probability == 0.0


def test_the_shadow_is_deterministic():
    assert record().to_dict() == record().to_dict()


def test_a_shadow_record_declares_it_is_not_canonical():
    out = record()
    assert out.is_canonical is False
    assert out.to_dict()["is_canonical"] is False
    assert out.schema_version == SHADOW_RECORD_SCHEMA_VERSION


def test_the_record_links_to_the_canonical_observation():
    out = record()
    payload = out.to_dict()
    for field in (
        "observation_key", "game_id", "timing_label", "captured_at", "market_ticker",
        "market_family", "control_model_version", "shadow_model_version",
        "talent_source_version", "beta", "provenance",
    ):
        assert field in payload, field
    assert payload["shadow_model_version"] == SHADOW_MODEL_VERSION


def test_the_record_never_carries_a_canonical_probability_field():
    """`model_probability` is the canonical name. A shadow row must not
    define it, or a careless join could overwrite the control."""
    assert "model_probability" not in record().to_dict()


# ------------------------------------------------ coverage


def test_coverage_is_reported_separately_from_control():
    """A shadow that silently covered a third of games would otherwise
    look like a fair comparison on the full slate."""
    records = [record(), record(talent_home=None), record(both_fbs=False)]
    report = ShadowCoverageReport(records).to_dict()
    assert report["total"] == 3
    assert report["available"] == 1
    assert report["unavailable"] == 2
    assert report["coverage_rate"] == pytest.approx(1 / 3)
    assert set(report["unavailable_reasons"]) == {
        ShadowUnavailableReason.TALENT_MISSING_HOME.value,
        ShadowUnavailableReason.UNSUPPORTED_POPULATION.value,
    }


# ------------------------------------- hypothesis registration


def test_the_hypothesis_was_registered_with_zero_settled_2026_games():
    assert SETTLED_2026_GAMES_AT_REGISTRATION == 0
    assert "Weeks 1-3" in PRIMARY_HYPOTHESIS
    assert "reduce absolute margin error" in PRIMARY_HYPOTHESIS


def test_the_hypothesis_hash_is_stable():
    assert hypothesis_hash() == hypothesis_hash()
    assert len(hypothesis_hash()) == 64
    assert hypothesis_manifest()["hypothesis_sha256"] == hypothesis_hash()


def test_market_families_were_chosen_before_results():
    assert hypothesis_manifest()["population"]["families_chosen_before_results"] is True


def test_refitting_on_2026_is_explicitly_prohibited():
    joined = " ".join(PROHIBITED)
    assert "refitting beta on 2026" in joined
    assert "selecting games" in joined


# ------------------------------------------------ analytics


def test_at_zero_settled_games_nothing_is_measured():
    """Not a null result. Reporting 0.0 would invite a reader to treat
    absence of measurement as a measured null."""
    result = compare([])
    assert result.state is EvidenceState.INSUFFICIENT_NATURAL_EVIDENCE
    assert result.n_games == 0
    assert result.control_margin_mae is None
    assert result.paired_margin_delta is None
    assert "not a null result" in result.detail


def test_with_settled_games_both_arms_are_compared_pairwise():
    pairs = [
        SettledShadowPair(
            game_id=f"g{i}", week=1, timing_label="T_24H",
            control_probability=0.6, shadow_probability=0.65,
            control_margin=3.0, shadow_margin=6.0, actual_home_margin=7,
        )
        for i in range(30)
    ]
    result = compare(pairs)
    assert result.state is EvidenceState.MEASURED
    assert result.n_games == 30
    assert result.control_margin_mae == pytest.approx(4.0)
    assert result.shadow_margin_mae == pytest.approx(1.0)
    assert result.paired_margin_delta == pytest.approx(-3.0)


def test_a_zero_margin_is_an_away_win_in_the_comparison():
    pair = SettledShadowPair(
        game_id="g", week=1, timing_label="T_24H",
        control_probability=0.5, shadow_probability=0.5,
        control_margin=0.0, shadow_margin=0.0, actual_home_margin=0,
    )
    assert pair.home_won is False


# --------------------------- production is untouched


@pytest.mark.parametrize(
    "package", ["modeling", "projections", "ratings", "recommendation", "kalshi", "decision"]
)
def test_no_production_package_imports_the_shadow(package):
    root = SRC / package
    if not root.exists():
        pytest.skip(f"{package} absent")
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in sorted(root.rglob("*.py"))
        if any("research.preseason" in m for m in imports_of(p))
    ]
    assert offenders == [], f"production imports the research/shadow: {offenders}"


def test_the_shadow_modules_never_assign_a_canonical_field():
    protected = {"model_probability", "projected_margin", "projected_total"}
    for name in ("shadow_capture.py", "shadow_spec.py", "shadow_analytics.py", "shadow_prior.py"):
        path = SRC / "research" / "preseason" / name
        for node in ast.walk(ast.parse(path.read_text())):
            targets = node.targets if isinstance(node, ast.Assign) else []
            for t in targets:
                attr = t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", None)
                assert attr not in protected, f"{name} assigns {attr}"


def test_the_shadow_creates_no_qualification_or_stake_vocabulary():
    for name in ("shadow_capture.py", "shadow_spec.py", "shadow_analytics.py"):
        src = (SRC / "research" / "preseason" / name).read_text().lower()
        for banned in ("qualif", "stake", "wager", "bet_size", "bankroll", "place_order"):
            assert banned not in src, f"{name} mentions {banned}"


# ------------------------------------------- end to end


def test_end_to_end_control_and_shadow_share_identical_market_input():
    """Both arms must price the SAME contract at the SAME instant with
    the SAME executable price and fee context. A shadow that saw a
    different market would not be a model comparison."""
    out = record()
    payload = out.to_dict()
    assert payload["market_ticker"] == "KXNCAAFGAME-T1"
    assert payload["executable_yes_price"] == 0.55
    assert payload["executable_no_price"] == 0.47
    assert payload["timing_label"] == "T_24H"
    assert payload["captured_at"] == CAPTURED.isoformat()


def test_end_to_end_the_control_values_are_carried_through_unchanged():
    """The shadow record must not restate the control differently from
    what the canonical row holds."""
    out = record()
    assert out.control_probability == 0.60
    assert out.control_projected_margin == 3.0
    assert out.control_model_version == "0.4.0-milestone-c2-live-margin-correction"


def test_end_to_end_settlement_is_identical_for_both_arms():
    """One game, one outcome. The arms differ only in prediction."""
    pair = SettledShadowPair(
        game_id="g", week=1, timing_label="CLOSING",
        control_probability=0.60, shadow_probability=0.70,
        control_margin=3.0, shadow_margin=6.0, actual_home_margin=10,
    )
    assert pair.home_won is True
    result = compare([pair] * 2)
    assert result.n_games == 2
    # Same realised outcome drives both arms' errors.
    assert result.control_margin_mae == pytest.approx(7.0)
    assert result.shadow_margin_mae == pytest.approx(4.0)
