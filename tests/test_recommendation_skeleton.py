"""Mission sections 3-24, 30: the recommendation skeleton's structure and
its disabled gates.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.expression.taxonomy import ContractSemantics
from cfb_edge_finder.recommendation.card import (
    BET_UP_TO_UNAVAILABLE,
    PORTFOLIO_LAYER_ABSENT,
    SHADOW_DISABLED,
    MaximumAcceptablePrice,
    PortfolioBoundary,
)
from cfb_edge_finder.recommendation.dedup import build_deduplication_view
from cfb_edge_finder.recommendation.eligibility import (
    QUALIFICATION_DISABLED,
    QUOTE_AGE_UNCONFIGURED,
    EligibilityConfig,
    FamilyResearchStatus,
    QualityPrerequisite,
    evaluate_eligibility,
    evaluate_quality_prerequisites,
    family_research_status,
)
from cfb_edge_finder.recommendation.evidence import EvidenceState, assess_readiness
from cfb_edge_finder.recommendation.odds import price_to_american_odds
from cfb_edge_finder.recommendation.pipeline import run_pipeline
from cfb_edge_finder.recommendation.risk import (
    RISK_LIMITS_DISABLED,
    ConcentrationLimits,
    build_exposure_keys,
    evaluate_concentration,
)
from cfb_edge_finder.recommendation.scoring import SCORING_DISABLED, ScoreComponents, build_score
from cfb_edge_finder.recommendation.thresholds import (
    NO_VALIDATED_THRESHOLD_SET,
    ApprovalState,
    NullThresholdProvider,
    StaticThresholdProvider,
    ThresholdArtifact,
    ThresholdIncompatibility,
    ThresholdProvenance,
)
from cfb_edge_finder.schemas.common import MarketFamily, Side

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
GAME = "cfb-2026-wk01-a-at-b"


def _snapshot(
    ticker="KXNCAAFGAME-EV-H", family=MarketFamily.MONEYLINE, team=Side.HOME, side=None,
    threshold=None, op=None, yes=0.55, no=0.50, model_p=0.60, status="active",
    fee_status="VERIFIED_CURRENT", captured=None, pricing="model_priced", label="T_24H", game=GAME,
):
    return ContractSnapshot(
        semantics=ContractSemantics(ticker, game, family, team, side, threshold, op, "confirmed_live"),
        timing_label=label,
        captured_at=(captured or NOW).isoformat(),
        model_probability=model_p,
        executable_yes_price=yes,
        executable_no_price=no,
        market_status=status,
        fee_status=fee_status,
        fee_schedule_version="kalshi_fee_schedule_2026_07_07_taker",
        model_version="0.4.0-milestone-c2-live-margin-correction",
        pricing_status=pricing,
        series_ticker=ticker.split("-", 1)[0],
    )


def _spread(threshold, team=Side.HOME, ticker=None, model_p=0.6):
    return _snapshot(
        ticker=ticker or f"KXNCAAFSPREAD-EV-{team.value}{threshold}",
        family=MarketFamily.SPREAD, team=team, threshold=threshold, op=">", model_p=model_p,
    )


def _total(threshold, model_p=0.6):
    return _snapshot(
        ticker=f"KXNCAAFTOTAL-EV-{threshold}", family=MarketFamily.TOTAL, team=None,
        side=Side.OVER, threshold=threshold, op=">", model_p=model_p,
    )


# --- Candidate construction (section 3) ----------------------------------


def test_two_candidates_per_contract_with_full_linkage():
    result = run_pipeline([_snapshot()], now=NOW)
    assert len(result.candidates) == 2
    sides = {c.executable_side for c in result.candidates}
    assert sides == {Side.YES, Side.NO}
    for c in result.candidates:
        for field in ("game_id", "market_ticker", "market_family", "timing_label", "executable_side",
                      "executable_price", "estimated_fee", "fee_adjusted_break_even_probability",
                      "model_probability", "research_probability_surplus", "equivalence_group_id",
                      "dimension_group_id", "game_group_id", "model_version", "captured_at"):
            assert getattr(c, field) is not None, f"candidate missing {field}"


def test_no_candidate_field_is_named_like_a_bet():
    from cfb_edge_finder.recommendation.candidate import ResearchCandidate

    for name in ResearchCandidate.__dataclass_fields__:
        assert not any(t in name.lower() for t in ("bet", "stake", "wager", "recommend"))


def test_no_side_model_probability_is_complemented():
    result = run_pipeline([_snapshot(model_p=0.6)], now=NOW)
    yes = next(c for c in result.candidates if c.executable_side is Side.YES)
    no = next(c for c in result.candidates if c.executable_side is Side.NO)
    assert yes.model_probability == pytest.approx(0.6)
    assert no.model_probability == pytest.approx(0.4)


def test_unpriceable_side_still_produces_a_candidate():
    """A missing NO quote is a real fact about the market; dropping the
    candidate would make the universe look tidier than it is."""
    result = run_pipeline([_snapshot(no=None)], now=NOW)
    no = next(c for c in result.candidates if c.executable_side is Side.NO)
    assert no.priceable is False and no.executable_price is None


# --- Threshold provider (sections 5, 19-23) ------------------------------


def test_default_provider_never_supplies_thresholds():
    resolution = NullThresholdProvider().resolve(model_version="m", timing_label="T_30", family="moneyline")
    assert resolution.available is False
    assert resolution.reason == NO_VALIDATED_THRESHOLD_SET
    assert resolution.artifact is None


def _artifact(**over):
    provenance = ThresholdProvenance(
        source_corpus_identifier="corpus-1", prospective_only=True, settled_game_count=200,
        created_at=NOW, analytics_code_version="analytics_v1", model_version="m1",
        approval_state=over.pop("approval_state", ApprovalState.APPROVED_FOR_LIVE),
    )
    if "provenance" in over:
        provenance = over.pop("provenance")
    base = dict(
        artifact_version="thr-1", provenance=provenance,
        applicable_model_versions=frozenset({"m1"}),
        applicable_timing_labels=frozenset({"T_30"}),
        applicable_families=frozenset({"moneyline"}),
    )
    base.update(over)
    return ThresholdArtifact(**base)


def test_model_version_mismatch_is_refused():
    provider = StaticThresholdProvider(_artifact())
    r = provider.resolve(model_version="m2", timing_label="T_30", family="moneyline")
    assert r.available is False
    assert ThresholdIncompatibility.MODEL_VERSION_MISMATCH in r.failures


def test_timing_mismatch_is_refused():
    r = StaticThresholdProvider(_artifact()).resolve(
        model_version="m1", timing_label="EARLY_OPEN", family="moneyline"
    )
    assert r.available is False
    assert ThresholdIncompatibility.TIMING_LABEL_MISMATCH in r.failures


def test_family_mismatch_is_refused():
    """A winner threshold must not silently apply to spread or total."""
    for family in ("spread", "total"):
        r = StaticThresholdProvider(_artifact()).resolve(
            model_version="m1", timing_label="T_30", family=family
        )
        assert r.available is False
        assert ThresholdIncompatibility.FAMILY_MISMATCH in r.failures


def test_unknown_axis_is_a_mismatch_not_a_wildcard():
    r = StaticThresholdProvider(_artifact()).resolve(model_version=None, timing_label=None, family=None)
    assert r.available is False
    assert {
        ThresholdIncompatibility.MODEL_VERSION_MISMATCH,
        ThresholdIncompatibility.TIMING_LABEL_MISMATCH,
        ThresholdIncompatibility.FAMILY_MISMATCH,
    } <= set(r.failures)


@pytest.mark.parametrize(
    "state", [ApprovalState.DRAFT_RESEARCH, ApprovalState.REVIEWED, ApprovalState.APPROVED_FOR_SHADOW]
)
def test_unapproved_artifact_is_refused(state):
    r = StaticThresholdProvider(_artifact(approval_state=state)).resolve(
        model_version="m1", timing_label="T_30", family="moneyline"
    )
    assert r.available is False
    assert ThresholdIncompatibility.NOT_APPROVED_FOR_LIVE in r.failures


def test_retrospective_or_evidence_free_artifact_is_refused():
    retro = ThresholdProvenance(
        source_corpus_identifier="c", prospective_only=False, settled_game_count=200, created_at=NOW,
        analytics_code_version="a", model_version="m1", approval_state=ApprovalState.APPROVED_FOR_LIVE,
    )
    r = StaticThresholdProvider(_artifact(provenance=retro)).resolve(
        model_version="m1", timing_label="T_30", family="moneyline"
    )
    assert ThresholdIncompatibility.NOT_PROSPECTIVE_ONLY in r.failures

    empty = ThresholdProvenance(
        source_corpus_identifier="c", prospective_only=True, settled_game_count=0, created_at=NOW,
        analytics_code_version="a", model_version="m1", approval_state=ApprovalState.APPROVED_FOR_LIVE,
    )
    r2 = StaticThresholdProvider(_artifact(provenance=empty)).resolve(
        model_version="m1", timing_label="T_30", family="moneyline"
    )
    assert ThresholdIncompatibility.INSUFFICIENT_DECLARED_EVIDENCE in r2.failures


def test_artifact_has_no_named_numeric_threshold_fields():
    """A named field would invite a default, and a default is the magic
    number this design forbids."""
    for name in ThresholdArtifact.__dataclass_fields__:
        assert not any(t in name.lower() for t in ("min_", "max_", "cutoff", "threshold_value"))


# --- Quality prerequisites (sections 17, 18) -----------------------------


def _candidate(**kw):
    return run_pipeline([_snapshot(**kw)], now=NOW).candidates[0]


def test_clean_record_passes_quality_when_freshness_is_configured():
    config = EligibilityConfig(max_quote_age_seconds=3600)
    assert evaluate_quality_prerequisites(_candidate(), config, now=NOW) == []


def test_unconfigured_quote_age_cannot_certify_freshness():
    """'No policy' must never read as 'any age is fine' -- that is how a
    stale price reaches an actionable path unnoticed."""
    config = EligibilityConfig()
    assert config.max_quote_age_seconds is None
    assert config.quote_age_policy == QUOTE_AGE_UNCONFIGURED
    assert QualityPrerequisite.QUOTE_FRESH in evaluate_quality_prerequisites(_candidate(), config, now=NOW)


def test_stale_quote_fails_when_a_policy_exists():
    config = EligibilityConfig(max_quote_age_seconds=60)
    stale = _candidate(captured=NOW - timedelta(hours=5))
    assert QualityPrerequisite.QUOTE_FRESH in evaluate_quality_prerequisites(stale, config, now=NOW)


@pytest.mark.parametrize("status", [None, "suspended", "closed", "finalized", "weird"])
def test_non_executable_market_status_fails(status):
    config = EligibilityConfig(max_quote_age_seconds=3600)
    c = _candidate(status=status)
    assert QualityPrerequisite.MARKET_EXECUTABLE in evaluate_quality_prerequisites(c, config, now=NOW)


def test_unverified_fee_schedule_fails():
    config = EligibilityConfig(max_quote_age_seconds=3600)
    c = _candidate(fee_status="unverified")
    assert QualityPrerequisite.FEE_SCHEDULE_VERIFIED in evaluate_quality_prerequisites(c, config, now=NOW)


def test_unresolved_semantics_fails():
    config = EligibilityConfig(max_quote_age_seconds=3600)
    snapshot = _snapshot(family=MarketFamily.SPREAD, team=Side.HOME, threshold=None, op=">")
    candidate = run_pipeline([snapshot], now=NOW).candidates[0]
    assert QualityPrerequisite.SEMANTICS_RESOLVED in evaluate_quality_prerequisites(candidate, config, now=NOW)


def test_unsupported_population_fails():
    config = EligibilityConfig(max_quote_age_seconds=3600)
    c = _candidate(pricing="unsupported_population")
    assert QualityPrerequisite.SUPPORTED_POPULATION in evaluate_quality_prerequisites(c, config, now=NOW)


def test_missing_model_probability_fails():
    config = EligibilityConfig(max_quote_age_seconds=3600)
    c = _candidate(model_p=None)
    assert QualityPrerequisite.MODEL_PROBABILITY_PRESENT in evaluate_quality_prerequisites(c, config, now=NOW)


def test_passing_every_quality_gate_still_is_not_actionable():
    """The load-bearing separation: knowing what a contract costs is not
    evidence that it is worth anything."""
    config = EligibilityConfig(max_quote_age_seconds=3600)
    candidate = _candidate()
    assert evaluate_quality_prerequisites(candidate, config, now=NOW) == []
    result = evaluate_eligibility(candidate, config, now=NOW)
    assert result.actionable is False
    assert result.status == QUALIFICATION_DISABLED
    assert result.threshold_reason == NO_VALIDATED_THRESHOLD_SET


# --- Evidence readiness (section 11) -------------------------------------


def test_zero_settled_is_no_settled_data():
    r = assess_readiness(
        family="moneyline", timing_label=None, model_version=None,
        settled_n=0, unique_game_clusters=0, clv_n=0,
    )
    assert r.state is EvidenceState.NO_SETTLED_DATA and r.actionable is False


def test_readiness_never_reaches_validated_from_data_alone():
    """No volume of rows promotes a slice: 'enough rows' is a different
    question from 'the rows say something real'."""
    r = assess_readiness(
        family="moneyline", timing_label="T_30", model_version="m1",
        settled_n=100_000, unique_game_clusters=5_000, clv_n=90_000,
    )
    assert r.state is EvidenceState.VALIDATION_PENDING
    assert r.state is not EvidenceState.VALIDATED
    assert r.actionable is False


def test_many_rows_from_few_games_is_low_sample():
    r = assess_readiness(
        family="spread", timing_label=None, model_version=None,
        settled_n=600, unique_game_clusters=2, clv_n=100,
    )
    assert r.state is EvidenceState.LOW_SAMPLE


def test_non_prospective_evidence_cannot_support_promotion():
    r = assess_readiness(
        family="moneyline", timing_label=None, model_version=None,
        settled_n=5000, unique_game_clusters=500, clv_n=4000, prospective_only=False,
    )
    assert r.state is EvidenceState.NO_SETTLED_DATA


# --- Family status (section 10) ------------------------------------------


def test_family_statuses_do_not_promote_totals():
    assert family_research_status("moneyline") is FamilyResearchStatus.SUPPORTED_RESEARCH_FAMILY
    assert family_research_status("spread") is FamilyResearchStatus.SUPPORTED_RESEARCH_FAMILY
    assert family_research_status("total") is FamilyResearchStatus.RESEARCH_PRIMITIVE_LOWER_CONFIDENCE
    assert family_research_status("exotic") is FamilyResearchStatus.UNSUPPORTED


# --- Dedup and nesting (sections 7, 8) -----------------------------------


def test_moneyline_pair_forms_one_equivalence_cluster_per_event():
    home = _snapshot(ticker="KXNCAAFGAME-EV-H", team=Side.HOME, yes=0.70, no=0.33)
    away = _snapshot(ticker="KXNCAAFGAME-EV-A", team=Side.AWAY, yes=0.33, no=0.70)
    view = build_deduplication_view(run_pipeline([home, away], now=NOW).candidates)
    multi = view.multi_expression_clusters
    assert len(multi) == 2, "the two winner events should each have two expressions"
    for cluster in multi:
        assert cluster.canonical_expression_candidate is not None


def test_dominated_expression_is_the_dearer_identical_payout():
    home = _snapshot(ticker="KXNCAAFGAME-EV-H", team=Side.HOME, yes=0.70, no=0.33)
    away = _snapshot(ticker="KXNCAAFGAME-EV-A", team=Side.AWAY, yes=0.37, no=0.91)
    view = build_deduplication_view(run_pipeline([home, away], now=NOW).candidates)
    assert view.dominated_count > 0
    for cluster in view.multi_expression_clusters:
        canonical = cluster.canonical_expression_candidate
        for dominated in cluster.dominated_expressions:
            assert (
                dominated.fee_adjusted_break_even_probability
                > canonical.fee_adjusted_break_even_probability
            )


def test_unpriceable_expression_is_never_called_dominated():
    home = _snapshot(ticker="KXNCAAFGAME-EV-H", team=Side.HOME, yes=0.70, no=None)
    away = _snapshot(ticker="KXNCAAFGAME-EV-A", team=Side.AWAY, yes=0.33, no=None)
    view = build_deduplication_view(run_pipeline([home, away], now=NOW).candidates)
    for cluster in view.equivalence_clusters.values():
        for dominated in cluster.dominated_expressions:
            assert dominated.priceable


def test_nested_spread_rungs_are_not_collapsed_into_one_event():
    """Team A ML, -3.5 and -7.5 are three DIFFERENT payout conditions."""
    snapshots = [_snapshot(ticker="KXNCAAFGAME-EV-H"), _spread(3.5), _spread(7.5)]
    view = build_deduplication_view(run_pipeline(snapshots, now=NOW).candidates)
    margin = next(g for g in view.nested_groups.values() if "MARGIN" in g.dimension_group_id)
    assert margin.is_nested_not_equivalent
    assert len(margin.distinct_events) >= 3, "nested rungs were collapsed into one event"


def test_nested_total_rungs_are_not_collapsed():
    view = build_deduplication_view(run_pipeline([_total(45.5), _total(52.5)], now=NOW).candidates)
    total = next(g for g in view.nested_groups.values() if "TOTAL" in g.dimension_group_id)
    assert total.is_nested_not_equivalent


def test_same_game_different_dimension_stays_separate():
    view = build_deduplication_view(run_pipeline([_spread(3.5), _total(45.5)], now=NOW).candidates)
    dimensions = {g.dimension_group_id for g in view.nested_groups.values()}
    assert len(dimensions) == 2


def test_unresolved_candidates_are_never_grouped():
    bad = _snapshot(family=MarketFamily.SPREAD, team=Side.HOME, threshold=None, op=">")
    view = build_deduplication_view(run_pipeline([bad], now=NOW).candidates)
    assert len(view.unresolved_candidates) == 2
    assert view.equivalence_clusters == {}


def test_canonical_expression_is_not_named_like_a_recommendation():
    from cfb_edge_finder.recommendation.dedup import EquivalenceCluster

    names = [n for n in dir(EquivalenceCluster) if not n.startswith("_")]
    assert not any(t in n.lower() for n in names for t in ("best", "recommend", "select", "pick"))


# --- Risk (sections 6, 9, 28) --------------------------------------------


def test_no_on_a_team_is_exposure_to_the_opponent():
    home = _snapshot(team=Side.HOME)
    candidates = run_pipeline([home], now=NOW).candidates
    yes_keys = build_exposure_keys(next(c for c in candidates if c.executable_side is Side.YES))
    no_keys = build_exposure_keys(next(c for c in candidates if c.executable_side is Side.NO))
    assert yes_keys.team_direction_exposure_id != no_keys.team_direction_exposure_id
    assert "away" in no_keys.team_direction_exposure_id


def test_risk_limits_are_disabled_and_enforce_nothing():
    result = run_pipeline([_snapshot(), _spread(3.5), _total(45.5)], now=NOW)
    assessment = evaluate_concentration(result.candidates, ConcentrationLimits())
    assert assessment.enforced is False
    assert assessment.status == RISK_LIMITS_DISABLED
    assert assessment.tally.max_per_game > 0, "exposure should still be counted"


def test_limits_marked_enabled_still_do_not_enforce():
    result = run_pipeline([_snapshot()], now=NOW)
    assessment = evaluate_concentration(
        result.candidates, ConcentrationLimits(enabled=True, max_expressions_per_game=1)
    )
    assert assessment.enforced is False


def test_risk_module_computes_no_money():
    from cfb_edge_finder.recommendation import risk

    for name in dir(risk):
        assert not any(t in name.lower() for t in ("dollar", "amount", "capital", "bankroll", "kelly"))


# --- Scoring (section 12) -------------------------------------------------


def test_score_is_never_composited():
    score = build_score(ScoreComponents(model_minus_break_even=0.07, prospective_clv_metric=0.02))
    assert score.composite is None
    assert score.status == SCORING_DISABLED
    assert score.components.model_minus_break_even == pytest.approx(0.07)


# --- American odds (section 15) ------------------------------------------


@pytest.mark.parametrize(
    "price,expected",
    [(0.50, -100), (0.60, -150), (0.40, 150), (0.25, 300), (0.80, -400), (0.99, -9900), (0.01, 9900)],
)
def test_american_odds_conversion(price, expected):
    odds = price_to_american_odds(price)
    assert odds.valid and odds.value == expected


@pytest.mark.parametrize("bad", [None, 0.0, 1.0, -0.1, 1.5])
def test_invalid_prices_have_no_american_representation(bad):
    odds = price_to_american_odds(bad)
    assert odds.valid is False and odds.value is None and odds.formatted == "-"


def test_odds_formatting_carries_no_opinion():
    from cfb_edge_finder.recommendation import odds as odds_module

    for name in dir(odds_module):
        assert not any(t in name.lower() for t in ("recommend", "qualify", "bet", "stake", "value"))


# --- Card and downstream boundary (sections 13, 16, 24, 29) --------------


def test_card_is_always_empty_with_zero_actionable():
    result = run_pipeline([_snapshot(), _spread(3.5), _total(45.5)], now=NOW)
    card = result.card
    assert card.actionable_count == 0
    assert card.entries == ()
    assert card.status == QUALIFICATION_DISABLED
    assert card.diagnostics.candidates_considered == 6


def test_bet_up_to_is_unavailable_and_carries_no_number():
    ceiling = MaximumAcceptablePrice()
    assert ceiling.available is False
    assert ceiling.status == BET_UP_TO_UNAVAILABLE
    assert ceiling.value is None


def test_shadow_mode_is_disabled():
    assert run_pipeline([_snapshot()], now=NOW).card.shadow_status == SHADOW_DISABLED


def test_portfolio_layer_is_absent():
    boundary = PortfolioBoundary()
    assert boundary.downstream_status == PORTFOLIO_LAYER_ABSENT


def test_card_reports_actionable_truthfully_rather_than_hardcoding_zero():
    """If a future change ever made a candidate actionable, the card must
    say so loudly rather than silently reporting zero."""
    import inspect

    from cfb_edge_finder.recommendation import card as card_module

    source = inspect.getsource(card_module.build_research_card)
    assert "len(actionable)" in source
    assert "actionable_count=0" not in source
