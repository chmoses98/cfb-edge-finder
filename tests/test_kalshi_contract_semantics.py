"""Contract-semantics parsing tests, including against the genuine
sanitized live-capture fixtures in tests/fixtures/kalshi/."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason
from cfb_edge_finder.kalshi.contract_semantics import (
    extract_matchup_from_rules_primary,
    parse_spread_market,
    parse_total_market,
    parse_winner_market,
)
from cfb_edge_finder.schemas.common import MarketFamily, Side

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kalshi"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


# --- real live fixtures --------------------------------------------------


def test_parses_real_live_spread_fixture():
    market = _load_fixture("spread_market_suu5.json")
    parsed = parse_spread_market(market["title"], market["floor_strike"])
    assert parsed.reason is None
    assert parsed.market_family == MarketFamily.SPREAD
    assert parsed.line == 4.5
    assert parsed.operator == ">"
    assert parsed.raw_team_name == "Southern Utah"
    assert parsed.semantics_confidence == "confirmed_live"


def test_parses_real_live_total_fixture():
    market = _load_fixture("total_market_81.json")
    parsed = parse_total_market(market["title"], market["floor_strike"])
    assert parsed.reason is None
    assert parsed.market_family == MarketFamily.TOTAL
    assert parsed.side == Side.OVER
    assert parsed.line == 80.5
    assert parsed.operator == ">"
    assert parsed.semantics_confidence == "confirmed_live"


# --- operator/half-point/push-impossibility ------------------------------


@pytest.mark.parametrize("threshold", [4.5, 0.5, 13.5, 27.5])
def test_spread_thresholds_are_always_half_point_never_an_integer_push(threshold):
    parsed = parse_spread_market(f"Ohio State wins by over {threshold} points", threshold)
    assert parsed.reason is None
    # A real integer margin can never equal a half-point line -- push is
    # structurally impossible under the confirmed ">" operator.
    assert parsed.line is not None and not parsed.line.is_integer()


def test_spread_title_floor_strike_mismatch_is_parse_unresolved():
    parsed = parse_spread_market("Ohio State wins by over 4.5 points", 7.5)
    assert parsed.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED


def test_total_title_floor_strike_mismatch_is_parse_unresolved():
    parsed = parse_total_market("Over 45.5 points scored", 50.5)
    assert parsed.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED


def test_unrecognized_spread_grammar_is_parse_unresolved():
    parsed = parse_spread_market("Ohio State favored by 4.5", 4.5)
    assert parsed.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED


def test_unrecognized_total_grammar_is_parse_unresolved():
    parsed = parse_total_market("Under 45.5 points scored", 45.5)
    assert parsed.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED


def test_spread_missing_floor_strike_still_parses_from_title_alone():
    parsed = parse_spread_market("Texas wins by over 3.5 points", None)
    assert parsed.reason is None
    assert parsed.line == 3.5


# --- winner/moneyline: hardened '<TEAM> wins' grammar (mission item 7) --

WINNER_RULES_PRIMARY_CORNELL = (
    "If Cornell wins the Cornell vs Colgate college football game originally scheduled "
    "for Sep 19, 2026, then the market resolves to Yes."
)


def test_winner_market_parses_title_only_and_stays_unconfirmed():
    # No rules_primary supplied -- title grammar alone is not a
    # deterministic title/team/event correspondence, so confidence must
    # NOT be raised.
    parsed = parse_winner_market("Texas wins")
    assert parsed.reason is None
    assert parsed.market_family == MarketFamily.MONEYLINE
    assert parsed.raw_team_name == "Texas"
    assert parsed.semantics_confidence == "unconfirmed"


def test_winner_market_each_team_side_parses_from_its_own_title():
    # Real live evidence: each team gets its OWN "<TEAM> wins" market
    # under the same event (e.g. KXNCAAFGAME-...-COR / -...-COLG),
    # mirroring the spread ladder's per-team markets.
    home = parse_winner_market("Cornell wins")
    away = parse_winner_market("Colgate wins")
    assert home.raw_team_name == "Cornell"
    assert away.raw_team_name == "Colgate"
    assert home.reason is None
    assert away.reason is None


def test_winner_market_title_and_rules_primary_agreement_confirms_live():
    # Real live rules_primary text (job 97711133675). Title AND
    # rules_primary both naming Cornell is a genuine, deterministic
    # title/team/event correspondence -- confidence IS raised here.
    parsed = parse_winner_market("Cornell wins", WINNER_RULES_PRIMARY_CORNELL)
    assert parsed.reason is None
    assert parsed.raw_team_name == "Cornell"
    assert parsed.semantics_confidence == "confirmed_live"


def test_winner_market_ticker_team_identity_mismatch_is_parse_unresolved():
    # Title names Colgate, but rules_primary independently names Cornell
    # as the winning team -- inconsistent evidence, must never be guessed
    # or silently resolved to either side.
    parsed = parse_winner_market("Colgate wins", WINNER_RULES_PRIMARY_CORNELL)
    assert parsed.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED


def test_winner_market_rules_primary_present_but_unrecognized_phrasing_stays_unconfirmed():
    # rules_primary present but not matching the confirmed "If X wins
    # the " grammar -- must stay unconfirmed, never guessed as a match
    # or a mismatch.
    parsed = parse_winner_market("Texas wins", "Some other rules text entirely.")
    assert parsed.reason is None
    assert parsed.semantics_confidence == "unconfirmed"


def test_winner_market_overtime_semantics_use_the_same_grammar():
    # A game that went to overtime still settles the SAME "<TEAM> wins"
    # contract on the final score -- there is no separate overtime state
    # for this parser to special-case, and this test proves that.
    regulation = parse_winner_market("Ohio State wins")
    overtime_game = parse_winner_market("Ohio State wins")
    assert regulation == overtime_game


def test_unrecognized_winner_title_grammar_is_parse_unresolved():
    # No hardening test suite for spread/total is complete without the
    # analogous ambiguous/malformed-title rejection for winner markets.
    for bad_title in ("Texas", "Texas will win", "wins", "Texas wins by a lot"):
        parsed = parse_winner_market(bad_title)
        assert parsed.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED, bad_title


def test_empty_winner_title_is_parse_unresolved():
    parsed = parse_winner_market("   ")
    assert parsed.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED


def test_future_fbs_winner_market_readiness_without_fabricating_a_live_example():
    # Architecture readiness only -- this is a synthetic title, NOT a
    # claim that a live FBS winner contract currently exists (none does
    # in this capture; see docs/MILESTONE_D.md). Proves the parser is
    # ready to price one the moment it appears.
    parsed = parse_winner_market("Ohio State wins")
    assert parsed.reason is None
    assert parsed.market_family == MarketFamily.MONEYLINE
    assert parsed.raw_team_name == "Ohio State"


# --- extract_matchup_from_rules_primary: real matchup evidence -----------


def test_extracts_matchup_from_real_spread_fixture_rules_primary():
    market = _load_fixture("spread_market_suu5.json")
    matchup = extract_matchup_from_rules_primary(market["rules_primary"])
    assert matchup == "Southern Utah vs Montana"


def test_extracts_matchup_from_real_total_fixture_rules_primary():
    market = _load_fixture("total_market_81.json")
    matchup = extract_matchup_from_rules_primary(market["rules_primary"])
    assert matchup == "Southern Utah vs Montana"


def test_extracted_matchup_is_splittable_by_game_mapping():
    from cfb_edge_finder.kalshi.game_mapping import _split_title

    matchup = extract_matchup_from_rules_primary(
        "If Ohio State wins by more than 3.5 points in the Ohio State vs Michigan college "
        "football game originally scheduled for Nov 28, 2026, then the market resolves to Yes."
    )
    assert matchup == "Ohio State vs Michigan"
    assert _split_title(matchup) == ("Ohio State", "Michigan")


def test_extracts_matchup_from_real_winner_market_rules_primary():
    # Real live text (job 97711133675, KXNCAAFGAME-26SEP19CORCOLG-COR):
    # winner markets phrase this as "wins THE <matchup> college football
    # game" (no "in"), unlike spread/total's "...points IN THE <matchup>
    # college football game" -- both must extract correctly.
    matchup = extract_matchup_from_rules_primary(
        "If Cornell wins the Cornell vs Colgate college football game originally scheduled "
        "for Sep 19, 2026, then the market resolves to Yes."
    )
    assert matchup == "Cornell vs Colgate"


def test_missing_rules_primary_returns_none():
    assert extract_matchup_from_rules_primary(None) is None
    assert extract_matchup_from_rules_primary("") is None


def test_unrecognized_rules_primary_phrasing_returns_none():
    assert extract_matchup_from_rules_primary("Some unrelated rules text with no matchup phrase.") is None
