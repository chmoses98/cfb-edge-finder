"""Mission section 12-13: winner/spread/total settlement, special cases,
overtime (settles on final score regardless of periods)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

sys.path.insert(0, "tests")
from research_factories import make_observation  # noqa: E402

from cfb_edge_finder.research.settlement import extract_game_result, flag_mismatch, settle_market
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.settlement import GameFinalStatus, MarketSettlementStatus

NOW = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)


def test_extract_game_result_final_via_status_string():
    raw = {"id": 123, "status": "final", "homePoints": 31, "awayPoints": 24}
    result = extract_game_result(raw, game_id="g1", season=2026, captured_at=NOW)
    assert result.status == GameFinalStatus.FINAL
    assert result.home_points == 31
    assert result.away_points == 24


def test_extract_game_result_final_via_completed_bool():
    raw = {"completed": True, "home_points": 20, "away_points": 17}
    result = extract_game_result(raw, game_id="g1", season=2026, captured_at=NOW)
    assert result.status == GameFinalStatus.FINAL


def test_extract_game_result_postponed():
    raw = {"status": "postponed"}
    result = extract_game_result(raw, game_id="g1", season=2026, captured_at=NOW)
    assert result.status == GameFinalStatus.POSTPONED


def test_extract_game_result_canceled():
    raw = {"status": "cancelled"}
    result = extract_game_result(raw, game_id="g1", season=2026, captured_at=NOW)
    assert result.status == GameFinalStatus.CANCELED


def test_extract_game_result_not_yet_final_default():
    raw = {"status": "scheduled"}
    result = extract_game_result(raw, game_id="g1", season=2026, captured_at=NOW)
    assert result.status == GameFinalStatus.NOT_YET_FINAL


def _result(home=31, away=24, status=GameFinalStatus.FINAL):
    raw_status = "final" if status == GameFinalStatus.FINAL else status.value
    raw = {"status": raw_status, "homePoints": home, "awayPoints": away}
    return extract_game_result(raw, game_id="g1", season=2026, captured_at=NOW)


def test_moneyline_home_wins_settles_yes_for_home_contract():
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME, threshold=None, semantic_operator=None)
    settlement = settle_market(obs, _result(home=31, away=24), settled_at=NOW)
    assert settlement.status == MarketSettlementStatus.SETTLED
    assert settlement.actual_winner == Side.HOME
    assert settlement.derived_contract_settlement == Side.YES


def test_moneyline_home_wins_settles_no_for_away_contract():
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.AWAY, threshold=None, semantic_operator=None)
    settlement = settle_market(obs, _result(home=31, away=24), settled_at=NOW)
    assert settlement.derived_contract_settlement == Side.NO


def test_spread_home_covers_settles_yes():
    # Home wins by 7, threshold 4.5 -> home covers.
    obs = make_observation(family=MarketFamily.SPREAD, team=Side.HOME, threshold=4.5, semantic_operator=">")
    settlement = settle_market(obs, _result(home=31, away=24), settled_at=NOW)
    assert settlement.derived_contract_settlement == Side.YES


def test_spread_home_does_not_cover_settles_no():
    # Home wins by 3, threshold 4.5 -> does not cover.
    obs = make_observation(family=MarketFamily.SPREAD, team=Side.HOME, threshold=4.5, semantic_operator=">")
    settlement = settle_market(obs, _result(home=27, away=24), settled_at=NOW)
    assert settlement.derived_contract_settlement == Side.NO


def test_spread_away_team_margin_uses_negated_home_margin():
    # Away team covers +4.5 means away must lose by less than 4.5 or win outright.
    obs = make_observation(family=MarketFamily.SPREAD, team=Side.AWAY, threshold=4.5, semantic_operator=">")
    # Home wins by 3 -> away margin = -3, not > 4.5 -> NO
    settlement = settle_market(obs, _result(home=27, away=24), settled_at=NOW)
    assert settlement.derived_contract_settlement == Side.NO
    # Away wins outright by 10 -> away margin = 10 > 4.5 -> YES
    settlement2 = settle_market(obs, _result(home=14, away=24), settled_at=NOW)
    assert settlement2.derived_contract_settlement == Side.YES


def test_total_over_hits_settles_yes():
    obs = make_observation(family=MarketFamily.TOTAL, side=Side.OVER, threshold=50.5, team=None, semantic_operator=">")
    settlement = settle_market(obs, _result(home=31, away=24), settled_at=NOW)  # total 55
    assert settlement.derived_contract_settlement == Side.YES


def test_total_under_settles_no():
    obs = make_observation(family=MarketFamily.TOTAL, side=Side.OVER, threshold=60.5, team=None, semantic_operator=">")
    settlement = settle_market(obs, _result(home=31, away=24), settled_at=NOW)  # total 55
    assert settlement.derived_contract_settlement == Side.NO


def test_overtime_settles_on_final_score_only_no_special_case():
    # A game that went to OT settles identically to a regulation game with
    # the same final score -- there is no separate OT branch in settlement.
    obs_reg = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME, threshold=None, semantic_operator=None)
    settlement = settle_market(obs_reg, _result(home=38, away=35), settled_at=NOW)
    assert settlement.derived_contract_settlement == Side.YES


def test_postponed_game_voids_market():
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME)
    result = extract_game_result({"status": "postponed"}, game_id="g1", season=2026, captured_at=NOW)
    settlement = settle_market(obs, result, settled_at=NOW)
    assert settlement.status == MarketSettlementStatus.VOID_POSTPONED
    assert settlement.derived_contract_settlement is None


def test_canceled_game_voids_market():
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME)
    result = extract_game_result({"status": "canceled"}, game_id="g1", season=2026, captured_at=NOW)
    settlement = settle_market(obs, result, settled_at=NOW)
    assert settlement.status == MarketSettlementStatus.VOID_CANCELED


def test_not_yet_final_is_pending():
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME)
    result = extract_game_result({"status": "scheduled"}, game_id="g1", season=2026, captured_at=NOW)
    settlement = settle_market(obs, result, settled_at=NOW)
    assert settlement.status == MarketSettlementStatus.PENDING_NOT_FINAL


def test_unknown_operator_is_unsettleable_not_guessed():
    obs = make_observation(family=MarketFamily.SPREAD, team=Side.HOME, threshold=4.5, semantic_operator=">=")
    settlement = settle_market(obs, _result(home=31, away=24), settled_at=NOW)
    assert settlement.status == MarketSettlementStatus.UNSETTLEABLE_UNKNOWN_OPERATOR
    assert settlement.derived_contract_settlement is None


def test_missing_score_fields_is_unsettleable():
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME)
    result = extract_game_result({"status": "final"}, game_id="g1", season=2026, captured_at=NOW)
    settlement = settle_market(obs, result, settled_at=NOW)
    assert settlement.status == MarketSettlementStatus.UNSETTLEABLE_MISSING_FIELDS


def test_flag_mismatch_flags_disagreement():
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME)
    settlement = settle_market(obs, _result(home=31, away=24), settled_at=NOW)
    assert settlement.derived_contract_settlement == Side.YES
    flagged = flag_mismatch(settlement, official=Side.NO)
    assert flagged.settlement_mismatch_flagged is True
    assert flagged.official_kalshi_settlement == Side.NO
    assert flagged.derived_contract_settlement == Side.YES  # both preserved


def test_flag_mismatch_no_flag_on_agreement():
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME)
    settlement = settle_market(obs, _result(home=31, away=24), settled_at=NOW)
    flagged = flag_mismatch(settlement, official=Side.YES)
    assert flagged.settlement_mismatch_flagged is False


def test_flag_mismatch_no_flag_when_official_absent():
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME)
    settlement = settle_market(obs, _result(home=31, away=24), settled_at=NOW)
    flagged = flag_mismatch(settlement, official=None)
    assert flagged.settlement_mismatch_flagged is False
    assert flagged.official_kalshi_settlement is None
