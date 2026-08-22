import pytest

from cfb_edge_finder.teams import REGISTRY, get_team
from cfb_edge_finder.teams.registry import (
    ALIASES,
    AMBIGUOUS_ALIASES,
    AmbiguousTeamAliasError,
    UnknownTeamAliasError,
    resolve_team_alias,
)


def test_registry_has_no_duplicate_team_ids():
    ids = [team.team_id for team in REGISTRY]
    assert len(ids) == len(set(ids))


def test_registry_matches_cfbd_reported_fbs_count():
    # 138, verified via web search against multiple independent, dated
    # sources during the Milestone B validation follow-up (Deseret News,
    # CBS Sports, ESPN, Wikipedia) -- see registry.py's provenance
    # warning for what "verified" does and doesn't mean here (not a live
    # CFBD fetch). Was 134 before the four FCS-to-FBS transitional
    # additions below were reconciled in.
    assert len(REGISTRY) == 138


@pytest.mark.parametrize(
    ("team_id", "conference", "season_start"),
    [
        ("delaware", "Conference USA", 2025),
        ("missouri-state", "Conference USA", 2025),
        ("north-dakota-state", "Mountain West", 2026),
        ("sacramento-state", "MAC", 2026),
    ],
)
def test_fcs_to_fbs_transitional_additions_present(team_id, conference, season_start):
    team = get_team(team_id)
    assert team is not None
    assert team.conference == conference
    assert team.season_start == season_start


@pytest.mark.parametrize(
    ("raw_name", "expected_team_id"),
    [
        ("Miami (FL)", "miami-fl"),
        ("Miami Hurricanes", "miami-fl"),
        ("Miami (OH)", "miami-oh"),
        ("Miami RedHawks", "miami-oh"),
        ("USC", "usc"),
        ("Southern California", "usc"),
        ("South Carolina", "south-carolina"),
        ("UTSA", "utsa"),
        ("UT San Antonio", "utsa"),
        ("UCF", "ucf"),
        ("Central Florida", "ucf"),
        ("Ole Miss", "ole-miss"),
        ("Mississippi", "ole-miss"),
        ("Louisiana", "louisiana"),
        ("Louisiana-Lafayette", "louisiana"),
        ("UL Lafayette", "louisiana"),
        ("UConn", "uconn"),
        ("Connecticut", "uconn"),
        ("Hawaii", "hawaii"),
        ("Hawai'i", "hawaii"),
        ("Western Kentucky", "western-kentucky"),
        ("WKU", "western-kentucky"),
    ],
)
def test_known_alias_cases_resolve_correctly(raw_name, expected_team_id):
    assert resolve_team_alias(raw_name) == expected_team_id
    assert get_team(expected_team_id) is not None


def test_usc_and_south_carolina_never_conflate():
    # The exact case the mission calls out: USC (Southern California) and
    # South Carolina must resolve to two DIFFERENT teams, never accidentally
    # merged by any fuzzy-matching shortcut (there is none in this module).
    assert resolve_team_alias("USC") != resolve_team_alias("South Carolina")


def test_bare_miami_is_ambiguous_and_fails_loud():
    with pytest.raises(AmbiguousTeamAliasError) as exc_info:
        resolve_team_alias("Miami")
    assert set(exc_info.value.candidates) == {"miami-fl", "miami-oh"}


def test_unknown_team_name_fails_loud_not_fuzzy_matched():
    with pytest.raises(UnknownTeamAliasError):
        resolve_team_alias("Ohio Stat")  # a plausible typo of "Ohio State" -- must NOT silently resolve


def test_every_registry_display_name_is_directly_resolvable():
    for team in REGISTRY:
        assert resolve_team_alias(team.display_name) == team.team_id


def test_no_alias_overlaps_a_registry_display_name_with_a_different_meaning():
    # Every ALIASES key must not silently shadow a distinct team's own
    # display_name with a different resolution.
    display_names = {team.display_name: team.team_id for team in REGISTRY}
    for alias, team_id in ALIASES.items():
        if alias in display_names:
            assert display_names[alias] == team_id


def test_ambiguous_aliases_are_not_also_registered_as_unambiguous():
    assert set(ALIASES) & set(AMBIGUOUS_ALIASES) == set()


def test_canonical_team_id_is_stable_across_repeated_resolution():
    ids = {resolve_team_alias("Ohio State") for _ in range(10)}
    assert len(ids) == 1
