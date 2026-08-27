"""Mission sections 4-14, 21, 26: settlement resolution and per-observation
attribution, exercised against GENUINE captured contract metadata.

`tests/fixtures/real_captured_observations.jsonl` holds 16 real rows
lifted from the live research corpus -- real Kalshi tickers, real
thresholds, real parsed operators, real executable prices. Settlement is
run against those, not against invented market metadata, because the
whole risk this milestone guards is that our STORED semantics disagree
with what the contract actually meant.

Final scores are supplied per test to drive each side of every boundary.
That is not fabrication: a boundary test's entire job is to hold the
contract fixed and vary the outcome.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cfb_edge_finder.research import attribution as attribution_mod
from cfb_edge_finder.research.attribution import (
    ATTRIBUTION_CODE_VERSION,
    attribute_observation,
    research_unit_economics,
)
from cfb_edge_finder.research.settlement import extract_game_result, flag_mismatch, settle_market
from cfb_edge_finder.schemas.attribution import AttributionState
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow
from cfb_edge_finder.schemas.settlement import GameFinalStatus, GameResult

FIXTURE = Path(__file__).parent / "fixtures" / "real_captured_observations.jsonl"
NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _real_rows() -> list[ResearchCorpusRow]:
    return [
        ResearchCorpusRow.model_validate(json.loads(line))
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


REAL_ROWS = _real_rows()


def _by_family(family: MarketFamily) -> list[ResearchCorpusRow]:
    return [r for r in REAL_ROWS if r.observation.family == family]


def _result(home: int, away: int, *, status=GameFinalStatus.FINAL, game_id="g", ot=None) -> GameResult:
    return GameResult(
        game_id=game_id, season=2026,
        home_points=home if status is GameFinalStatus.FINAL else None,
        away_points=away if status is GameFinalStatus.FINAL else None,
        status=status, went_to_overtime=ot, captured_at=NOW,
    )


# --- The fixture itself is genuine --------------------------------------


def test_fixture_carries_real_captured_contract_metadata():
    assert len(REAL_ROWS) == 16
    fams = {r.observation.family for r in REAL_ROWS}
    assert fams == {MarketFamily.MONEYLINE, MarketFamily.SPREAD, MarketFamily.TOTAL}
    for r in REAL_ROWS:
        assert r.observation.kalshi_market_ticker.startswith("KXNCAAF")
        assert r.observation.parse_status == "confirmed_live"
        assert r.observation.pricing_status == "model_priced"


def test_real_no_prices_are_not_one_minus_yes():
    """Mission section 11 warns against assuming NO = 1 - YES. The real
    corpus proves that warning is not hypothetical: these are independent
    executable quotes off the book and they do not complement."""
    violations = [
        (r.observation.kalshi_market_ticker, r.observation.executable_yes_price, r.observation.executable_no_price)
        for r in REAL_ROWS
        if r.observation.executable_yes_price is not None
        and r.observation.executable_no_price is not None
        and abs((r.observation.executable_yes_price + r.observation.executable_no_price) - 1.0) > 0.02
    ]
    assert violations, "fixture no longer demonstrates independent YES/NO quotes"


def test_all_real_thresholds_are_half_points():
    """Mission section 5: if live CFB lines are all half-point, push is
    structurally impossible and must NOT be modelled sportsbook-style."""
    thresholds = [r.observation.threshold for r in REAL_ROWS if r.observation.threshold is not None]
    assert thresholds
    assert all(abs(t - int(t)) == 0.5 for t in thresholds), thresholds


# --- Winner contracts (section 4) ----------------------------------------


def test_winner_yes_and_no_on_a_real_moneyline_contract():
    home_row = next(r for r in _by_family(MarketFamily.MONEYLINE) if r.observation.team is Side.HOME)
    s_home_wins = settle_market(home_row.observation, _result(31, 17), settled_at=NOW)
    assert s_home_wins.derived_contract_settlement is Side.YES
    assert s_home_wins.actual_winner is Side.HOME

    s_home_loses = settle_market(home_row.observation, _result(17, 31), settled_at=NOW)
    assert s_home_loses.derived_contract_settlement is Side.NO


def test_away_moneyline_is_the_mirror():
    away_row = next(r for r in _by_family(MarketFamily.MONEYLINE) if r.observation.team is Side.AWAY)
    assert settle_market(away_row.observation, _result(17, 31), settled_at=NOW).derived_contract_settlement is Side.YES
    assert settle_market(away_row.observation, _result(31, 17), settled_at=NOW).derived_contract_settlement is Side.NO


def test_overtime_settles_on_the_final_score_not_regulation():
    """A contract settles on the FINAL score regardless of periods."""
    home_row = next(r for r in _by_family(MarketFamily.MONEYLINE) if r.observation.team is Side.HOME)
    ot = settle_market(home_row.observation, _result(38, 35, ot=True), settled_at=NOW)
    assert ot.derived_contract_settlement is Side.YES
    assert ot.game_result.went_to_overtime is True


# --- Spread contracts (section 5) ----------------------------------------


def test_spread_uses_strict_greater_than_at_the_real_threshold():
    row = _by_family(MarketFamily.SPREAD)[0]
    obs = row.observation
    thr = obs.threshold
    assert obs.team is Side.HOME and obs.semantic_operator == ">"

    # margin just above -> YES; just below -> NO. Half-point threshold
    # means the exact-equality case cannot occur with integer scores.
    above = settle_market(obs, _result(int(thr) + 1 + 20, 20), settled_at=NOW)
    below = settle_market(obs, _result(int(thr) + 20, 20), settled_at=NOW)
    assert above.derived_contract_settlement is Side.YES
    assert below.derived_contract_settlement is Side.NO


def test_spread_threshold_is_never_treated_as_greater_or_equal():
    """Constructed integer threshold to prove strictness explicitly: at
    margin == threshold the contract must settle NO, not YES."""
    row = _by_family(MarketFamily.SPREAD)[0]
    obs = row.observation.model_copy(update={"threshold": 7.0})
    exactly = settle_market(obs, _result(27, 20), settled_at=NOW)  # margin exactly 7
    assert exactly.derived_contract_settlement is Side.NO, "spread used >= instead of strict >"
    assert settle_market(obs, _result(28, 20), settled_at=NOW).derived_contract_settlement is Side.YES


def test_spread_ladder_is_monotonic_on_the_real_rungs():
    """Real captured rungs on one game (2.5 / 3.5 / 4.5): a fixed margin
    must settle YES on every lower rung it clears and NO above it."""
    rungs = sorted(
        {(r.observation.threshold, r.observation.kalshi_market_ticker) for r in _by_family(MarketFamily.SPREAD)}
    )
    assert len(rungs) >= 3
    row = _by_family(MarketFamily.SPREAD)[0]
    margin = 4  # home wins by 4
    outcomes = []
    for thr, _t in rungs:
        obs = row.observation.model_copy(update={"threshold": thr})
        s = settle_market(obs, _result(20 + margin, 20), settled_at=NOW)
        outcomes.append((thr, s.derived_contract_settlement))
    yes_thresholds = [t for t, side in outcomes if side is Side.YES]
    no_thresholds = [t for t, side in outcomes if side is Side.NO]
    assert yes_thresholds and no_thresholds, outcomes
    assert max(yes_thresholds) < min(no_thresholds), f"ladder not monotonic: {outcomes}"


# --- Total contracts (section 6) -----------------------------------------


def test_total_uses_strict_greater_than_at_the_real_threshold():
    row = _by_family(MarketFamily.TOTAL)[0]
    obs = row.observation
    thr = obs.threshold
    assert obs.side is Side.OVER and obs.semantic_operator == ">"
    over = settle_market(obs, _result(int(thr // 2) + 1, int(thr // 2) + 1), settled_at=NOW)
    under = settle_market(obs, _result(3, 3), settled_at=NOW)
    assert over.derived_contract_settlement is Side.YES
    assert under.derived_contract_settlement is Side.NO


def test_total_threshold_is_never_treated_as_greater_or_equal():
    row = _by_family(MarketFamily.TOTAL)[0]
    obs = row.observation.model_copy(update={"threshold": 50.0})
    assert settle_market(obs, _result(25, 25), settled_at=NOW).derived_contract_settlement is Side.NO
    assert settle_market(obs, _result(26, 25), settled_at=NOW).derived_contract_settlement is Side.YES


# --- Semantics come from the observation, never reparsed (section 7) -----


def test_unknown_operator_is_refused_rather_than_guessed():
    row = _by_family(MarketFamily.SPREAD)[0]
    obs = row.observation.model_copy(update={"semantic_operator": ">="})
    s = settle_market(obs, _result(30, 20), settled_at=NOW)
    assert s.status.value.startswith("unsettleable")
    assert s.derived_contract_settlement is None


def test_missing_threshold_is_refused():
    row = _by_family(MarketFamily.SPREAD)[0]
    obs = row.observation.model_copy(update={"threshold": None})
    assert settle_market(obs, _result(30, 20), settled_at=NOW).derived_contract_settlement is None


# --- Non-final / void game states (sections 8, 15) -----------------------


@pytest.mark.parametrize(
    "status,expected_state",
    [
        (GameFinalStatus.NOT_YET_FINAL, AttributionState.GAME_NOT_FINAL),
        (GameFinalStatus.POSTPONED, AttributionState.GAME_POSTPONED),
        (GameFinalStatus.CANCELED, AttributionState.GAME_CANCELLED),
    ],
)
def test_non_final_games_never_settle(status, expected_state):
    row = _by_family(MarketFamily.MONEYLINE)[0]
    s = settle_market(row.observation, _result(0, 0, status=status), settled_at=NOW)
    a = attribute_observation(row, s, settled_at=NOW)
    assert a.state is expected_state
    assert a.event_true is None
    assert a.yes_economics is None and a.no_economics is None, "economics computed for an unresolved game"


def test_unresolved_pnl_is_none_not_zero():
    """A zero P/L would read as 'broke even'; the truth is 'unknown'."""
    row = _by_family(MarketFamily.MONEYLINE)[0]
    a = attribute_observation(row, None, settled_at=NOW)
    assert a.state is AttributionState.RESULT_UNAVAILABLE
    assert a.yes_economics is None and a.no_economics is None


def test_unmapped_observation_is_mapping_unresolved():
    row = _by_family(MarketFamily.MONEYLINE)[0]
    unmapped = row.model_copy(update={"observation": row.observation.model_copy(update={"game_id": None})})
    a = attribute_observation(unmapped, None, settled_at=NOW)
    assert a.state is AttributionState.MAPPING_UNRESOLVED


def test_unsupported_population_is_not_a_failure():
    row = _by_family(MarketFamily.MONEYLINE)[0]
    unsupported = row.model_copy(
        update={"observation": row.observation.model_copy(update={"pricing_status": "unsupported_population"})}
    )
    a = attribute_observation(unsupported, None, settled_at=NOW)
    assert a.state is AttributionState.NOT_APPLICABLE_UNSUPPORTED_POPULATION


def test_market_not_final_is_recorded_rather_than_settled_optimistically():
    row = _by_family(MarketFamily.MONEYLINE)[0]
    s = settle_market(row.observation, _result(31, 17), settled_at=NOW)
    a = attribute_observation(row, s, settled_at=NOW, require_market_final=True, market_is_final=False)
    assert a.state is AttributionState.MARKET_NOT_FINAL
    assert a.yes_economics is None


# --- Settlement mismatch (section 16) ------------------------------------


def test_mismatch_is_flagged_and_never_written_as_settled():
    row = _by_family(MarketFamily.MONEYLINE)[0]
    s = settle_market(row.observation, _result(31, 17), settled_at=NOW)
    derived = s.derived_contract_settlement
    opposite = Side.NO if derived is Side.YES else Side.YES
    mismatched = flag_mismatch(s, opposite)
    assert mismatched.settlement_mismatch_flagged is True

    a = attribute_observation(row, mismatched, settled_at=NOW)
    assert a.state is AttributionState.SETTLEMENT_MISMATCH
    assert a.yes_economics is None, "economics computed despite a settlement mismatch"
    assert a.derived_contract_settlement is derived
    assert a.official_kalshi_settlement is opposite, "diagnostic evidence not preserved"


def test_agreement_is_not_a_mismatch_and_absence_is_not_either():
    row = _by_family(MarketFamily.MONEYLINE)[0]
    s = settle_market(row.observation, _result(31, 17), settled_at=NOW)
    agreed = flag_mismatch(s, s.derived_contract_settlement)
    assert agreed.settlement_mismatch_flagged is False
    absent = flag_mismatch(s, None)
    assert absent.settlement_mismatch_flagged is False


# --- Research-unit economics (sections 10, 11) ---------------------------


def test_yes_unit_economics_on_a_win_and_a_loss():
    win = research_unit_economics(side=Side.YES, entry_price=0.40, event_true=True, series_ticker="KXNCAAFGAME")
    assert win.settlement_value == 1.0
    assert win.research_unit_pnl == pytest.approx(0.60)
    assert win.fee_adjusted_research_unit_pnl < win.research_unit_pnl
    assert win.return_on_entry_price == pytest.approx(1.5)

    loss = research_unit_economics(side=Side.YES, entry_price=0.40, event_true=False, series_ticker="KXNCAAFGAME")
    assert loss.settlement_value == 0.0
    assert loss.research_unit_pnl == pytest.approx(-0.40)


def test_no_unit_economics_is_the_inverse_event():
    no_win = research_unit_economics(side=Side.NO, entry_price=0.30, event_true=False, series_ticker="KXNCAAFGAME")
    assert no_win.settlement_value == 1.0
    assert no_win.research_unit_pnl == pytest.approx(0.70)

    no_loss = research_unit_economics(side=Side.NO, entry_price=0.30, event_true=True, series_ticker="KXNCAAFGAME")
    assert no_loss.settlement_value == 0.0


def test_no_side_fee_is_computed_at_the_no_price_not_the_yes_price():
    """The real corpus has yes=0.74 alongside no=0.93 on one contract --
    reusing the YES fee for the NO side would be wrong by construction."""
    yes = research_unit_economics(side=Side.YES, entry_price=0.74, event_true=True, series_ticker="KXNCAAFGAME")
    no = research_unit_economics(side=Side.NO, entry_price=0.93, event_true=False, series_ticker="KXNCAAFGAME")
    assert yes.estimated_fee != no.estimated_fee, "NO fee was borrowed from the YES side"


def test_zero_entry_price_return_is_undefined_not_infinite():
    e = research_unit_economics(side=Side.YES, entry_price=0.0, event_true=True, series_ticker="KXNCAAFGAME")
    assert e.return_on_entry_price is None
    assert e.estimated_fee is None


def test_missing_entry_price_yields_no_economics():
    assert research_unit_economics(
        side=Side.YES, entry_price=None, event_true=True, series_ticker="KXNCAAFGAME"
    ) is None


def test_research_unit_is_fixed_at_one_contract():
    assert attribution_mod.RESEARCH_UNIT_CONTRACTS == 1
    row = _by_family(MarketFamily.MONEYLINE)[0]
    s = settle_market(row.observation, _result(31, 17), settled_at=NOW)
    a = attribute_observation(row, s, settled_at=NOW)
    assert a.research_unit_size == 1


def test_settled_observation_gets_both_sides_when_both_prices_exist():
    row = next(
        r for r in _by_family(MarketFamily.MONEYLINE)
        if r.observation.executable_yes_price is not None and r.observation.executable_no_price is not None
    )
    s = settle_market(row.observation, _result(31, 17), settled_at=NOW)
    a = attribute_observation(row, s, settled_at=NOW)
    assert a.state in (AttributionState.SETTLED_YES, AttributionState.SETTLED_NO)
    assert a.yes_economics is not None and a.no_economics is not None
    # Exactly one side wins.
    assert {a.yes_economics.settlement_value, a.no_economics.settlement_value} == {0.0, 1.0}


# --- Provenance (section 24) ---------------------------------------------


def test_attribution_carries_full_provenance():
    row = _by_family(MarketFamily.SPREAD)[0]
    s = settle_market(row.observation, _result(31, 17), settled_at=NOW)
    a = attribute_observation(row, s, settled_at=NOW, result_fetched_at=NOW, run_id="run-1")
    assert a.observation_key == row.observation_key
    assert a.attribution_key.startswith(row.observation_key)
    for field in ("game_id", "kalshi_market_ticker", "family", "timing_label", "season",
                  "captured_at", "entry_yes_price", "entry_model_probability",
                  "fee_schedule_version", "model_version", "settlement_code_version", "settled_at"):
        assert getattr(a, field) is not None, f"provenance field {field} missing"
    assert a.result_source == "cfbd"
    assert a.run_id == "run-1"
    assert a.settlement_code_version == ATTRIBUTION_CODE_VERSION


def test_attribution_never_mutates_the_observation():
    row = _by_family(MarketFamily.SPREAD)[0]
    before = row.model_dump_json()
    s = settle_market(row.observation, _result(31, 17), settled_at=NOW)
    attribute_observation(row, s, settled_at=NOW)
    assert row.model_dump_json() == before


def test_extract_game_result_requires_authoritative_final_state():
    """Mission section 3: a game is not final merely because kickoff
    passed. Only an explicit status or completed+scores qualifies."""
    assert extract_game_result({}, game_id="g", season=2026, captured_at=NOW).status is GameFinalStatus.NOT_YET_FINAL
    assert extract_game_result(
        {"completed": True}, game_id="g", season=2026, captured_at=NOW
    ).status is GameFinalStatus.NOT_YET_FINAL, "completed without scores must not be final"
    assert extract_game_result(
        {"completed": True, "homePoints": 30, "awayPoints": 20}, game_id="g", season=2026, captured_at=NOW
    ).status is GameFinalStatus.FINAL
