"""Mission section 4's explicitly named test cases: Miami FL vs Miami OH,
USC vs South Carolina, abbreviated/accented/directional names,
neutral-site, FBS-vs-FCS (via classify_mapped_market), rescheduled games,
plus ambiguous-team and ambiguous-game rejection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cfb_edge_finder.ids import canonical_game_id
from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason
from cfb_edge_finder.kalshi.game_mapping import (
    CORE_V1_MARKET_FAMILIES,
    KalshiGameEvidence,
    classify_mapped_market,
    map_kalshi_event_to_game,
)
from cfb_edge_finder.schemas.common import MarketFamily, SeasonType
from cfb_edge_finder.schemas.game import GameRecord

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def make_game(**overrides) -> GameRecord:
    defaults = dict(
        game_id=canonical_game_id(2026, "wk01", "texas", "ohio-state"),
        season=2026,
        week_label="wk01",
        season_type=SeasonType.REGULAR,
        home_team_id="ohio-state",
        away_team_id="texas",
        home_team_name="Ohio State",
        away_team_name="Texas",
        neutral_site=False,
        kickoff_utc=NOW,
        venue="Ohio Stadium",
        source_game_ids={"cfbd": "12345"},
        primary_source="cfbd",
        discovered_at=NOW,
        last_updated_at=NOW,
    )
    defaults.update(overrides)
    return GameRecord(**defaults)


def make_evidence(title: str, reference_timestamp=NOW, market_ticker="TICKER-1", event_ticker="EVT-1"):
    return KalshiGameEvidence(
        market_ticker=market_ticker,
        event_ticker=event_ticker,
        title=title,
        subtitle=None,
        reference_timestamp=reference_timestamp,
    )


# --- basic happy path ------------------------------------------------


def test_unique_team_pair_match_resolves():
    games = [make_game()]
    result = map_kalshi_event_to_game(make_evidence("Texas at Ohio State"), games)
    assert result.reason is None
    assert result.game_id == games[0].game_id
    assert result.home_team_id == "ohio-state"
    assert result.away_team_id == "texas"


# --- Miami FL vs Miami OH ---------------------------------------------


def test_miami_fl_and_miami_oh_are_disambiguated_by_full_name():
    miami_fl_game = make_game(
        game_id=canonical_game_id(2026, "wk01", "florida-state", "miami-fl"),
        home_team_id="miami-fl",
        away_team_id="florida-state",
        home_team_name="Miami (FL)",
        away_team_name="Florida State",
    )
    miami_oh_game = make_game(
        game_id=canonical_game_id(2026, "wk01", "buffalo", "miami-oh"),
        home_team_id="miami-oh",
        away_team_id="buffalo",
        home_team_name="Miami (OH)",
        away_team_name="Buffalo",
    )
    games = [miami_fl_game, miami_oh_game]

    result_fl = map_kalshi_event_to_game(make_evidence("Florida State at Miami (FL)"), games)
    assert result_fl.reason is None
    assert result_fl.game_id == miami_fl_game.game_id

    result_oh = map_kalshi_event_to_game(make_evidence("Buffalo at Miami (OH)"), games)
    assert result_oh.reason is None
    assert result_oh.game_id == miami_oh_game.game_id


def test_bare_miami_is_ambiguous_team_mapping():
    games = [
        make_game(
            game_id=canonical_game_id(2026, "wk01", "florida-state", "miami-fl"),
            home_team_id="miami-fl",
            away_team_id="florida-state",
        )
    ]
    result = map_kalshi_event_to_game(make_evidence("Florida State at Miami"), games)
    assert result.reason == KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING
    assert result.game_id is None


# --- USC vs South Carolina --------------------------------------------


def test_usc_and_south_carolina_never_confused():
    game = make_game(
        game_id=canonical_game_id(2026, "wk01", "south-carolina", "usc"),
        home_team_id="usc",
        away_team_id="south-carolina",
        home_team_name="USC",
        away_team_name="South Carolina",
    )
    result = map_kalshi_event_to_game(make_evidence("South Carolina at USC"), [game])
    assert result.reason is None
    assert result.home_team_id == "usc"
    assert result.away_team_id == "south-carolina"


# --- abbreviated / accented / directional names ------------------------


def test_abbreviated_and_full_name_both_resolve_the_same_game():
    game = make_game(
        game_id=canonical_game_id(2026, "wk01", "wake-forest", "nc-state"),
        home_team_id="nc-state",
        away_team_id="wake-forest",
        home_team_name="NC State",
        away_team_name="Wake Forest",
    )
    abbreviated = map_kalshi_event_to_game(make_evidence("Wake Forest at NC State"), [game])
    full = map_kalshi_event_to_game(make_evidence("Wake Forest at North Carolina State"), [game])
    assert abbreviated.reason is None
    assert full.reason is None
    assert abbreviated.game_id == full.game_id == game.game_id


def test_accented_name_resolves():
    game = make_game(
        game_id=canonical_game_id(2026, "wk01", "san-jose-state", "boise-state"),
        home_team_id="boise-state",
        away_team_id="san-jose-state",
        home_team_name="Boise State",
        away_team_name="San José State",
    )
    result = map_kalshi_event_to_game(make_evidence("San José State at Boise State"), [game])
    assert result.reason is None
    assert result.game_id == game.game_id


# --- neutral site --------------------------------------------------------


def test_neutral_site_game_still_maps_by_team_pair():
    game = make_game(
        game_id=canonical_game_id(2026, "wk01", "texas", "ohio-state", neutral_site=True),
        neutral_site=True,
        venue="Neutral Stadium",
    )
    result = map_kalshi_event_to_game(make_evidence("Texas vs Ohio State"), [game])
    assert result.reason is None
    assert result.game_id == game.game_id


# --- rescheduled games ----------------------------------------------------


def test_unique_match_ignores_a_large_date_gap_rescheduled_game():
    # The Kalshi market's own reference_timestamp reflects the ORIGINAL
    # schedule; the candidate GameRecord's kickoff_utc reflects the game
    # actually being moved two weeks later. Team-pair identity is the
    # strong signal -- a UNIQUE match is accepted regardless of date gap.
    game = make_game(kickoff_utc=NOW + timedelta(days=14))
    original_reference = NOW
    evidence = make_evidence("Texas at Ohio State", reference_timestamp=original_reference)
    result = map_kalshi_event_to_game(evidence, [game])
    assert result.reason is None
    assert result.game_id == game.game_id


# --- ambiguous game mapping (same pair twice) -----------------------------


def test_same_team_pair_twice_within_window_is_ambiguous_game_mapping():
    game_a = make_game(
        game_id=canonical_game_id(2026, "wk01", "texas", "ohio-state"),
        week_label="wk01",
        kickoff_utc=NOW,
    )
    game_b = make_game(
        game_id=canonical_game_id(2026, "conf-champ-big-ten", "texas", "ohio-state"),
        week_label="conf-champ-big-ten",
        season_type=SeasonType.CONFERENCE_CHAMPIONSHIP,
        kickoff_utc=NOW + timedelta(hours=2),
    )
    result = map_kalshi_event_to_game(make_evidence("Texas at Ohio State", reference_timestamp=NOW), [game_a, game_b])
    assert result.reason == KalshiCfbCoverageReason.AMBIGUOUS_GAME_MAPPING
    assert result.game_id is None


def test_date_window_disambiguates_a_real_rematch():
    game_a = make_game(
        game_id=canonical_game_id(2026, "wk01", "texas", "ohio-state"),
        week_label="wk01",
        kickoff_utc=NOW,
    )
    game_b = make_game(
        game_id=canonical_game_id(2026, "conf-champ-big-ten", "texas", "ohio-state"),
        week_label="conf-champ-big-ten",
        season_type=SeasonType.CONFERENCE_CHAMPIONSHIP,
        kickoff_utc=NOW + timedelta(days=100),
    )
    result = map_kalshi_event_to_game(make_evidence("Texas at Ohio State", reference_timestamp=NOW), [game_a, game_b])
    assert result.reason is None
    assert result.game_id == game_a.game_id


# --- no candidate at all ----------------------------------------------------


def test_no_matching_candidate_is_parse_unresolved():
    result = map_kalshi_event_to_game(make_evidence("Texas at Ohio State"), [])
    assert result.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED
    assert result.game_id is None


def test_unparseable_title_is_parse_unresolved():
    result = map_kalshi_event_to_game(make_evidence("Some Non-Matchup Title"), [make_game()])
    assert result.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED


# --- classify_mapped_market: the only place MAPPED_SUPPORTED can appear --


def test_classify_mapped_market_supported_fbs_vs_fbs_core_v1():
    game = make_game()
    mapping = map_kalshi_event_to_game(make_evidence("Texas at Ohio State"), [game])
    reason = classify_mapped_market(
        mapping, market_family=MarketFamily.SPREAD, home_classification="fbs", away_classification="fbs"
    )
    assert reason == KalshiCfbCoverageReason.MAPPED_SUPPORTED


def test_classify_mapped_market_fbs_vs_fcs_is_unsupported_population():
    game = make_game()
    mapping = map_kalshi_event_to_game(make_evidence("Texas at Ohio State"), [game])
    reason = classify_mapped_market(
        mapping, market_family=MarketFamily.TOTAL, home_classification="fbs", away_classification="fcs"
    )
    assert reason == KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_POPULATION


def test_classify_mapped_market_non_core_v1_family_is_unsupported_family():
    game = make_game()
    mapping = map_kalshi_event_to_game(make_evidence("Texas at Ohio State"), [game])
    reason = classify_mapped_market(
        mapping, market_family=MarketFamily.TEAM_TOTAL, home_classification="fbs", away_classification="fbs"
    )
    assert reason == KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_FAMILY
    assert MarketFamily.TEAM_TOTAL not in CORE_V1_MARKET_FAMILIES


def test_classify_mapped_market_passes_through_a_failed_mapping():
    failed_mapping = map_kalshi_event_to_game(make_evidence("Some Non-Matchup Title"), [make_game()])
    reason = classify_mapped_market(
        failed_mapping, market_family=MarketFamily.SPREAD, home_classification="fbs", away_classification="fbs"
    )
    assert reason == failed_mapping.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED


def test_classify_mapped_market_none_family_is_parse_unresolved():
    game = make_game()
    mapping = map_kalshi_event_to_game(make_evidence("Texas at Ohio State"), [game])
    reason = classify_mapped_market(
        mapping, market_family=None, home_classification="fbs", away_classification="fbs"
    )
    assert reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED
