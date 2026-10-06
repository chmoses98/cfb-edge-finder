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
    _split_title,
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


# --- FCS-vs-FCS: distinct, understood unsupported population -------------

FCS_SCHOOL_NAMES = frozenset({"cornell", "colgate", "yale", "holy cross"})


def test_fcs_vs_fcs_is_classified_distinctly_not_as_ambiguous():
    # Real live evidence: KXNCAAFGAME-26SEP19CORCOLG (job 97711133675) --
    # Cornell vs Colgate, neither team in teams.registry (FBS-only) and
    # never a candidate GameRecord (FBS-scoped schedule fetch).
    result = map_kalshi_event_to_game(
        make_evidence("Cornell vs Colgate"), candidate_games=[], fcs_school_names=FCS_SCHOOL_NAMES
    )
    assert result.reason == KalshiCfbCoverageReason.FCS_VS_FCS
    assert result.game_id is None


def test_fcs_vs_fcs_still_works_with_real_fbs_candidate_games_present():
    # An unrelated FBS game in the candidate pool must not interfere.
    result = map_kalshi_event_to_game(
        make_evidence("Yale vs Holy Cross"), candidate_games=[make_game()], fcs_school_names=FCS_SCHOOL_NAMES
    )
    assert result.reason == KalshiCfbCoverageReason.FCS_VS_FCS


def test_without_fcs_school_names_supplied_behavior_is_unchanged():
    # Default (empty) fcs_school_names must reproduce the exact prior
    # behavior -- AMBIGUOUS_TEAM_MAPPING, never a silent regression.
    result = map_kalshi_event_to_game(make_evidence("Cornell vs Colgate"), [])
    assert result.reason == KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING


def test_one_known_fcs_side_and_one_unknown_side_is_not_fcs_vs_fcs():
    # Only ONE side matches the FCS set -- must stay AMBIGUOUS_TEAM_MAPPING,
    # never guessed as FCS-vs-FCS from partial evidence.
    result = map_kalshi_event_to_game(
        make_evidence("Cornell vs Some Unknown School"), candidate_games=[], fcs_school_names=FCS_SCHOOL_NAMES
    )
    assert result.reason == KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING


def test_one_known_fcs_side_and_one_real_fbs_side_is_not_fcs_vs_fcs():
    # A genuine FBS-vs-FCS market resolves the FBS side via the registry
    # (first_id/second_id both non-None) -- it never reaches the FCS-vs-FCS
    # branch at all, and is instead handled downstream by
    # classify_mapped_market's MAPPED_UNSUPPORTED_POPULATION.
    game = make_game()
    result = map_kalshi_event_to_game(
        make_evidence("Cornell at Ohio State"), [game], fcs_school_names=FCS_SCHOOL_NAMES
    )
    assert result.reason == KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING


def test_fcs_vs_fcs_reason_maps_to_unsupported_market_outcome():
    from cfb_edge_finder.kalshi.cfb_coverage_reason import to_coverage_outcome
    from cfb_edge_finder.schemas.common import CoverageOutcome

    assert to_coverage_outcome(KalshiCfbCoverageReason.FCS_VS_FCS) == CoverageOutcome.UNSUPPORTED_MARKET


def test_classify_mapped_market_passes_through_fcs_vs_fcs_unchanged():
    failed_mapping = map_kalshi_event_to_game(
        make_evidence("Cornell vs Colgate"), [], fcs_school_names=FCS_SCHOOL_NAMES
    )
    reason = classify_mapped_market(
        failed_mapping, market_family=MarketFamily.SPREAD, home_classification=None, away_classification=None
    )
    assert reason == KalshiCfbCoverageReason.FCS_VS_FCS


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


# --- _split_title separator priority (Milestone D closure) ----------------
# Real live bug (GH Actions run 32886794099): a matchup string whose
# SECOND team's own name contains " at " (e.g. CFBD's "University at
# Albany") was mis-split when " at " was checked before " vs "/" vs. ",
# because every production matchup string is always "<TEAM1> vs <TEAM2>"
# (extract_matchup_from_rules_primary's own regex requires a literal
# " vs "). " vs "/" vs. " are now checked first.


def test_split_title_prefers_vs_over_at_when_a_team_name_contains_at():
    # CFBD's real school name for UAlbany is "University at Albany" --
    # the SECOND team's own name contains " at ", which must not be
    # mistaken for the team-pair separator.
    assert _split_title("New Hampshire vs University at Albany") == ("New Hampshire", "University at Albany")


def test_split_title_still_splits_on_at_when_no_vs_present():
    # The " at " fallback must still work for evidence that never
    # contains " vs " at all (e.g. a raw "<TEAM1> at <TEAM2>" title, as
    # already exercised by make_evidence("Texas at Ohio State") above).
    assert _split_title("Texas at Ohio State") == ("Texas", "Ohio State")


def test_university_at_albany_matchup_is_fcs_vs_fcs_not_ambiguous():
    # The full, real-shaped scenario (both New Hampshire and University
    # at Albany are genuine FCS programs, per the real live event this
    # bug was found against): before the separator-priority fix, this
    # matchup mis-split into "New Hampshire vs University" / "Albany",
    # neither of which is a known FCS school name, so it landed as an
    # unexplained AMBIGUOUS_TEAM_MAPPING. With the fix, it splits into
    # the two real team names, both of which ARE known FCS schools --
    # correctly reclassified as the distinct, understood FCS_VS_FCS
    # outcome instead.
    fcs_names = frozenset({"new hampshire", "university at albany"})
    result = map_kalshi_event_to_game(
        make_evidence("New Hampshire vs University at Albany"), [], fcs_school_names=fcs_names
    )
    assert result.reason == KalshiCfbCoverageReason.FCS_VS_FCS


# --- NON_FBS_PARTICIPANT: the 2026-09-01 forensic-audit closure ----------
# Live evidence (GH Actions run 33556291244): 1,485 of 1,775 "mapping
# failure" markets were FBS-vs-known-FCS fixtures ("Montana St. vs
# Nevada"), and most of the rest involved Division II/III programs or
# verified Kalshi name variants -- all deliberately-declined populations
# that landed in AMBIGUOUS_TEAM_MAPPING because the FCS_VS_FCS carve-out
# requires BOTH sides to be FCS.

NON_FBS_SCHOOL_NAMES = frozenset(
    {"cornell", "colgate", "montana st.", "montana state", "edward waters", "grambling st.", "grambling"}
)


def test_fbs_vs_known_fcs_is_non_fbs_participant_not_a_mapping_failure():
    # "Montana St. vs Nevada": Nevada resolves in the FBS registry,
    # Montana St. is a known FCS program -- the fixture can never be a
    # supported FBS-vs-FBS population, so it must be classified, not
    # counted as a failure.
    result = map_kalshi_event_to_game(
        make_evidence("Montana St. vs Nevada"),
        candidate_games=[],
        fcs_school_names=frozenset({"montana st.", "montana state"}),
        non_fbs_school_names=NON_FBS_SCHOOL_NAMES,
    )
    assert result.reason == KalshiCfbCoverageReason.NON_FBS_PARTICIPANT
    assert result.game_id is None


def test_known_non_fbs_side_with_unknown_opponent_is_non_fbs_participant():
    # One side provably non-FBS is enough: whatever the unknown opponent
    # is, the fixture is not a supported population. (Live example:
    # "Edward Waters Tigers vs Jackson St." -- the D2 side's
    # mascot-suffixed name is unknown, the FCS side is known.)
    result = map_kalshi_event_to_game(
        make_evidence("Some Unknown Opponent vs Edward Waters"),
        candidate_games=[],
        fcs_school_names=frozenset(),
        non_fbs_school_names=NON_FBS_SCHOOL_NAMES,
    )
    assert result.reason == KalshiCfbCoverageReason.NON_FBS_PARTICIPANT


def test_both_sides_unknown_stays_ambiguous_team_mapping_fail_closed():
    # Neither side deterministically identified -> the event stays a
    # genuine mapping failure. No guessing, ever.
    result = map_kalshi_event_to_game(
        make_evidence("Webber International Warriors vs Kentucky Christian Knights"),
        candidate_games=[],
        fcs_school_names=FCS_SCHOOL_NAMES,
        non_fbs_school_names=NON_FBS_SCHOOL_NAMES,
    )
    assert result.reason == KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING


def test_ambiguous_fbs_name_never_reclassified_by_non_fbs_set():
    # Bare "Miami" is a genuine FBS-registry collision
    # (AmbiguousTeamAliasError). Even if a same-named school appeared in
    # the non-FBS set, ambiguity must win: only an UNKNOWN side is
    # eligible for the non-FBS identity check.
    result = map_kalshi_event_to_game(
        make_evidence("Florida State at Miami"),
        candidate_games=[],
        fcs_school_names=frozenset(),
        non_fbs_school_names=frozenset({"miami"}),
    )
    assert result.reason == KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING


def test_fcs_vs_fcs_still_takes_precedence_over_non_fbs_participant():
    # Both sides known FCS keeps the more specific, pre-existing reason.
    result = map_kalshi_event_to_game(
        make_evidence("Cornell vs Colgate"),
        candidate_games=[],
        fcs_school_names=FCS_SCHOOL_NAMES,
        non_fbs_school_names=NON_FBS_SCHOOL_NAMES,
    )
    assert result.reason == KalshiCfbCoverageReason.FCS_VS_FCS


def test_without_non_fbs_school_names_supplied_behavior_is_unchanged():
    # Default (empty) set must reproduce the exact prior behavior.
    result = map_kalshi_event_to_game(
        make_evidence("Montana St. vs Nevada"), candidate_games=[], fcs_school_names=frozenset()
    )
    assert result.reason == KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING


def test_unmappable_fbs_vs_fbs_event_remains_fail_closed():
    # Miami (FL) vs Stanford: both sides resolve cleanly in the FBS
    # registry but CFBD's schedule carries no such fixture -- must stay a
    # genuine, loud PARSE_UNRESOLVED (schedule-source discrepancy), never
    # be absorbed by the unsupported-population classification.
    result = map_kalshi_event_to_game(
        make_evidence("Miami (FL) vs Stanford"),
        candidate_games=[make_game()],
        fcs_school_names=FCS_SCHOOL_NAMES,
        non_fbs_school_names=NON_FBS_SCHOOL_NAMES,
    )
    assert result.reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED
    assert result.game_id is None
