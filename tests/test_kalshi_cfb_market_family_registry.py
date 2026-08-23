import pytest
from pydantic import ValidationError

from cfb_edge_finder.kalshi.cfb_market_family_registry import (
    KALSHI_CFB_MARKET_FAMILIES,
    AlternateLineSupport,
    EvidenceConfidence,
    KalshiMarketFamilyRecord,
    MarketScope,
    MilestoneCPriority,
    validate_registry,
)


def test_registry_is_non_empty():
    assert len(KALSHI_CFB_MARKET_FAMILIES) > 0


def test_registry_has_no_duplicate_family_ids():
    ids = [r.family_id for r in KALSHI_CFB_MARKET_FAMILIES]
    assert len(ids) == len(set(ids))


def test_validate_registry_passes_on_the_real_registry():
    validate_registry()  # must not raise


def test_validate_registry_catches_duplicate_ids():
    dupe = KALSHI_CFB_MARKET_FAMILIES[0]
    with pytest.raises(ValueError, match="duplicate family_id"):
        validate_registry((dupe, dupe))


def test_every_priority_value_is_a_valid_enum_member():
    for record in KALSHI_CFB_MARKET_FAMILIES:
        assert record.milestone_c_priority in MilestoneCPriority


def test_no_futures_family_has_a_game_model_priority():
    for record in KALSHI_CFB_MARKET_FAMILIES:
        if record.scope == MarketScope.FUTURES:
            assert record.milestone_c_priority not in (
                MilestoneCPriority.CORE_V1,
                MilestoneCPriority.LATER_GAME_MODEL,
            ), f"{record.family_id} is a futures family but has a single-game-model priority"


def test_no_unverified_family_is_core_v1():
    for record in KALSHI_CFB_MARKET_FAMILIES:
        if record.historical_confidence == EvidenceConfidence.UNVERIFIED:
            assert record.milestone_c_priority != MilestoneCPriority.CORE_V1


def test_every_core_v1_family_has_a_required_probability_primitive():
    for record in KALSHI_CFB_MARKET_FAMILIES:
        if record.milestone_c_priority == MilestoneCPriority.CORE_V1:
            assert record.required_probability_primitive


def test_core_v1_families_are_the_expected_three():
    core_v1_ids = {
        r.family_id for r in KALSHI_CFB_MARKET_FAMILIES if r.milestone_c_priority == MilestoneCPriority.CORE_V1
    }
    assert core_v1_ids == {"game_winner", "point_spread", "game_total"}


def test_touchdown_prop_is_confirmed_but_not_a_build_target():
    # Legally self-certified for CFB, but reporting says it is not actually
    # offered for college players -- CONFIRMED confidence must not, by
    # itself, force a family onto the build list.
    record = next(r for r in KALSHI_CFB_MARKET_FAMILIES if r.family_id == "touchdown_prop")
    assert record.historical_confidence == EvidenceConfidence.CONFIRMED
    assert record.milestone_c_priority == MilestoneCPriority.UNSUPPORTED_UNVERIFIED


def test_futures_families_include_win_total_ladder_with_confirmed_ladder_evidence():
    record = next(r for r in KALSHI_CFB_MARKET_FAMILIES if r.family_id == "regular_season_win_total")
    assert record.alternate_line_support == AlternateLineSupport.LADDER_CONFIRMED
    assert record.scope == MarketScope.FUTURES


# --- Constructing a bad record directly must fail loud (not just the
# registry-level checks above) ---


def _minimal_kwargs(**overrides):
    base = dict(
        family_id="test_family",
        display_name="Test",
        scope=MarketScope.GAME_LEVEL,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary="test",
        contract_semantic_type="test",
        alternate_line_support=AlternateLineSupport.UNKNOWN,
        milestone_c_priority=MilestoneCPriority.CORE_V1,
        required_probability_primitive="P(x)",
    )
    base.update(overrides)
    return base


def test_core_v1_without_confirmed_confidence_raises():
    with pytest.raises(ValidationError, match="not CONFIRMED"):
        KalshiMarketFamilyRecord(**_minimal_kwargs(historical_confidence=EvidenceConfidence.PROBABLE))


def test_core_v1_without_a_probability_primitive_raises():
    with pytest.raises(ValidationError, match="no required_probability_primitive"):
        KalshiMarketFamilyRecord(**_minimal_kwargs(required_probability_primitive=None))


def test_futures_scope_with_core_v1_priority_raises():
    with pytest.raises(ValidationError, match="futures families must be"):
        KalshiMarketFamilyRecord(**_minimal_kwargs(scope=MarketScope.FUTURES))


def test_futures_scope_with_later_game_model_priority_raises():
    kwargs = _minimal_kwargs(scope=MarketScope.FUTURES, milestone_c_priority=MilestoneCPriority.LATER_GAME_MODEL)
    with pytest.raises(ValidationError, match="futures families must be"):
        KalshiMarketFamilyRecord(**kwargs)


def test_futures_scope_with_unsupported_unverified_priority_is_allowed():
    record = KalshiMarketFamilyRecord(
        **_minimal_kwargs(
            scope=MarketScope.FUTURES,
            milestone_c_priority=MilestoneCPriority.UNSUPPORTED_UNVERIFIED,
            required_probability_primitive=None,
        )
    )
    assert record.scope == MarketScope.FUTURES


def test_registry_module_has_no_recommendation_or_staking_surface():
    # Milestone B.5 is classification/audit only -- no edge, recommendation,
    # or staking computation belongs anywhere in this module.
    import cfb_edge_finder.kalshi.cfb_market_family_registry as registry_module

    forbidden = ("recommend", "edge", "stake", "kelly", "ev_", "place_order", "place_bet")
    public_names = [n for n in dir(registry_module) if not n.startswith("_")]
    violations = [n for n in public_names for f in forbidden if f in n.lower()]
    assert violations == [], f"unexpected recommendation/staking surface: {violations}"
