"""Classification is a label, never a filter.

Every series ticker asserted here was observed live on Kalshi during this
pivot (see docs/evidence/kalshi_milestone_probe.txt) -- these are real
families, not invented ones.
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.catalog.classification import (
    ClassificationConfidence,
    GamePeriod,
    MarketFamilyLabel,
    classify_market,
)


@pytest.mark.parametrize(
    ("series_ticker", "expected_family", "expected_period"),
    [
        # --- the three families the old CORE_V1 gate knew about ---------
        ("KXNCAAFGAME", MarketFamilyLabel.GAME_MONEYLINE, GamePeriod.FULL_GAME),
        ("KXNCAAFSPREAD", MarketFamilyLabel.GAME_SPREAD, GamePeriod.FULL_GAME),
        ("KXNCAAFTOTAL", MarketFamilyLabel.GAME_TOTAL, GamePeriod.FULL_GAME),
        # --- halves -----------------------------------------------------
        ("KXNCAAF1H", MarketFamilyLabel.FIRST_HALF_MONEYLINE, GamePeriod.FIRST_HALF),
        ("KXNCAAF1HSPREAD", MarketFamilyLabel.FIRST_HALF_SPREAD, GamePeriod.FIRST_HALF),
        ("KXNCAAF1HTOTAL", MarketFamilyLabel.FIRST_HALF_TOTAL, GamePeriod.FIRST_HALF),
        ("KXNCAAF1HTEAMTOTAL", MarketFamilyLabel.FIRST_HALF_TEAM_TOTAL, GamePeriod.FIRST_HALF),
        ("KXNCAAF2H", MarketFamilyLabel.SECOND_HALF_MONEYLINE, GamePeriod.SECOND_HALF),
        ("KXNCAAF2HSPREAD", MarketFamilyLabel.SECOND_HALF_SPREAD, GamePeriod.SECOND_HALF),
        ("KXNCAAF2HTOTAL", MarketFamilyLabel.SECOND_HALF_TOTAL, GamePeriod.SECOND_HALF),
        # --- quarters, all four -----------------------------------------
        ("KXNCAAF1Q", MarketFamilyLabel.QUARTER_MONEYLINE, GamePeriod.FIRST_QUARTER),
        ("KXNCAAF2Q", MarketFamilyLabel.QUARTER_MONEYLINE, GamePeriod.SECOND_QUARTER),
        ("KXNCAAF3Q", MarketFamilyLabel.QUARTER_MONEYLINE, GamePeriod.THIRD_QUARTER),
        ("KXNCAAF4Q", MarketFamilyLabel.QUARTER_MONEYLINE, GamePeriod.FOURTH_QUARTER),
        ("KXNCAAF1QSPREAD", MarketFamilyLabel.QUARTER_SPREAD, GamePeriod.FIRST_QUARTER),
        ("KXNCAAF3QTOTAL", MarketFamilyLabel.QUARTER_TOTAL, GamePeriod.THIRD_QUARTER),
        ("KXNCAAF4QSPREAD", MarketFamilyLabel.QUARTER_SPREAD, GamePeriod.FOURTH_QUARTER),
        # --- team totals and team-stat props ----------------------------
        ("KXNCAAFTEAMTOTAL", MarketFamilyLabel.TEAM_TOTAL, GamePeriod.FULL_GAME),
        ("KXNCAAFTEAMSACK", MarketFamilyLabel.TEAM_STAT_PROP, GamePeriod.FULL_GAME),
        ("KXNCAAFTEAMTO", MarketFamilyLabel.TEAM_STAT_PROP, GamePeriod.FULL_GAME),
        ("KXNCAAFTEAMFG", MarketFamilyLabel.TEAM_STAT_PROP, GamePeriod.FULL_GAME),
        ("KXNCAAFTEAMRECYDS", MarketFamilyLabel.TEAM_STAT_PROP, GamePeriod.FULL_GAME),
        # --- game-level oddities ----------------------------------------
        ("KXNCAAF1QBTTS", MarketFamilyLabel.BOTH_TEAMS_TO_SCORE, GamePeriod.FIRST_QUARTER),
        ("KXNCAAFFIRSTTDTEAM", MarketFamilyLabel.TOUCHDOWN_SCORER, GamePeriod.FULL_GAME),
        ("KXNCAAFFTTS", MarketFamilyLabel.FIRST_SCORE, GamePeriod.FULL_GAME),
        ("KXNCAAFOT", MarketFamilyLabel.OVERTIME, GamePeriod.OVERTIME),
        ("KXNCAAFTOTALFG", MarketFamilyLabel.GAME_STAT_PROP, GamePeriod.FULL_GAME),
        ("KXNCAAFTOTALTD", MarketFamilyLabel.GAME_STAT_PROP, GamePeriod.FULL_GAME),
        # --- season/futures, kept but separated from game menus ---------
        ("KXNCAAFWINS", MarketFamilyLabel.SEASON_FUTURES, GamePeriod.SEASON),
        ("KXNCAAFSEC", MarketFamilyLabel.SEASON_FUTURES, GamePeriod.SEASON),
        ("KXNCAAFPLAYOFF", MarketFamilyLabel.SEASON_FUTURES, GamePeriod.SEASON),
        ("KXNCAAFAWARD", MarketFamilyLabel.SEASON_FUTURES, GamePeriod.SEASON),
        ("KXNCAAFAPRANK", MarketFamilyLabel.SEASON_FUTURES, GamePeriod.SEASON),
        ("KXNCAAFCFPPOLL", MarketFamilyLabel.SEASON_FUTURES, GamePeriod.SEASON),
        ("KXNCAAFCONFMATCHUP", MarketFamilyLabel.SEASON_FUTURES, GamePeriod.SEASON),
        # --- combos -----------------------------------------------------
        ("KXNCAAFPREPACK4ML", MarketFamilyLabel.MULTIVARIATE_COMBO, GamePeriod.UNKNOWN),
        ("KXNCAAFPREPACKSGP", MarketFamilyLabel.MULTIVARIATE_COMBO, GamePeriod.UNKNOWN),
    ],
)
def test_real_live_series_tickers_classify_structurally(series_ticker, expected_family, expected_period):
    result = classify_market(series_ticker)
    assert result.family == expected_family, result.rationale
    assert result.period == expected_period, result.rationale
    assert result.confidence == ClassificationConfidence.STRUCTURAL


def test_unknown_series_is_retained_as_unknown_never_dropped():
    """THE load-bearing test. A family Kalshi invents tomorrow must arrive
    labelled UNKNOWN -- not raise, not return None, not be filtered out."""
    result = classify_market("KXNCAAFSOMETHINGENTIRELYNEW")
    assert result.family == MarketFamilyLabel.UNKNOWN
    assert result.confidence == ClassificationConfidence.UNRESOLVED
    assert "retained verbatim" in result.rationale


def test_classify_never_returns_none_for_any_input():
    for candidate in ("", "GARBAGE", "KXNFLGAME", "kxncaafspread", "X", "KX-----"):
        result = classify_market(candidate)
        assert isinstance(result.family, MarketFamilyLabel)


def test_classify_handles_missing_series_ticker():
    result = classify_market(None)
    assert result.family == MarketFamilyLabel.UNKNOWN


def test_a_brand_new_period_family_combination_generalizes():
    """KXNCAAF2HTEAMTOTAL does not exist on the exchange today. The parser
    is structural (period token + family suffix), so it must classify
    sensibly the day Kalshi launches it -- without a code change."""
    result = classify_market("KXNCAAF2HTEAMTOTAL")
    assert result.family != MarketFamilyLabel.UNKNOWN
    assert result.period == GamePeriod.SECOND_HALF
    assert result.confidence == ClassificationConfidence.STRUCTURAL


def test_text_fallback_when_ticker_is_unrecognized():
    """A non-CFB-prefixed ticker still classifies from Kalshi's own
    contract text rather than being discarded."""
    result = classify_market(
        "SOMENEWPREFIX",
        title="Will Georgia vs Arkansas have over 52.5 points?",
        rules_primary="If the total points scored is more than 52.5 ...",
    )
    assert result.family == MarketFamilyLabel.GAME_TOTAL
    assert result.confidence == ClassificationConfidence.TEXTUAL


def test_text_fallback_reads_the_period_too():
    result = classify_market(
        "SOMENEWPREFIX", title="Will the 1st half of Georgia vs Arkansas have over 24.5 points?"
    )
    assert result.family == MarketFamilyLabel.FIRST_HALF_TOTAL
    assert result.period == GamePeriod.FIRST_HALF


def test_team_total_is_not_swallowed_by_total():
    """Suffix matching is longest-first: TEAMTOTAL must not resolve to the
    shorter TOTAL suffix, or every team total becomes a game total and the
    two are silently merged."""
    assert classify_market("KXNCAAFTEAMTOTAL").family == MarketFamilyLabel.TEAM_TOTAL
    assert classify_market("KXNCAAFTOTAL").family == MarketFamilyLabel.GAME_TOTAL


def test_futures_are_not_misread_as_game_markets():
    """KXNCAAFCONFMATCHUP ends in nothing game-like, but a naive substring
    check for 'GAME' would have matched KXNCAAFBOWLGAME. Futures are
    checked before game families for exactly that reason."""
    assert classify_market("KXNCAAFBOWLGAME").family == MarketFamilyLabel.SEASON_FUTURES
    assert classify_market("KXNCAAFGAME").family == MarketFamilyLabel.GAME_MONEYLINE


def test_overtime_is_not_matched_as_a_substring():
    """"OT" sits inside "TOTALFG" at index 1. A substring test therefore
    published a real total-field-goals contract as an overtime market --
    caught here, fixed by matching overtime suffixes on equality."""
    assert classify_market("KXNCAAFTOTALFG").family == MarketFamilyLabel.GAME_STAT_PROP
    assert classify_market("KXNCAAFTOTALTD").family == MarketFamilyLabel.GAME_STAT_PROP
    assert classify_market("KXNCAAFOT").family == MarketFamilyLabel.OVERTIME
    assert classify_market("KXNCAAFMOSTOT").family == MarketFamilyLabel.OVERTIME


def test_conference_code_alone_is_futures_but_not_as_a_substring():
    """"CS" alone means the FCS-champion market, but as a SUBSTRING it
    appears inside KXNCAAFCSGAME -- the FCS *game* series. Treating it as
    a substring would bury every FCS game's market menu under season
    futures."""
    assert classify_market("KXNCAAFSEC").family == MarketFamilyLabel.SEASON_FUTURES
    assert classify_market("KXNCAAFB10").family == MarketFamilyLabel.SEASON_FUTURES
    assert classify_market("KXNCAAFCSGAME").family == MarketFamilyLabel.GAME_MONEYLINE


def test_spread_text_beats_total_text_when_both_appear():
    """"Arkansas wins 1H by over 4.5 points" (a real live title) contains
    both an over/under and a win-by. It is a spread, and checking total
    first published it as a total."""
    result = classify_market("NEWPREFIX", title="Arkansas wins 1H by over 4.5 points")
    assert result.family == MarketFamilyLabel.FIRST_HALF_SPREAD


def test_fcs_game_series_classifies_as_a_game():
    """KXNCAAFCSGAME ('College Football FCS Game') is a real series. An
    FCS game's menu matters as much as an SEC game's."""
    assert classify_market("KXNCAAFCSGAME").family == MarketFamilyLabel.GAME_MONEYLINE
