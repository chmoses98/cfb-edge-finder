import pytest

from cfb_edge_finder.ids import (
    assert_unique_game_ids,
    canonical_game_id,
    slugify_team,
    validate_week_label,
)


def test_slugify_team_basic():
    assert slugify_team("Ohio State") == "ohio-state"


def test_slugify_team_strips_accents_and_punctuation():
    assert slugify_team("Texas A&M") == "texas-a-m"


def test_slugify_team_rejects_empty():
    with pytest.raises(ValueError):
        slugify_team("   ")


@pytest.mark.parametrize(
    "label",
    ["wk01", "wk15", "bowl-rose", "cfp-national-championship", "conf-champ-sec"],
)
def test_validate_week_label_accepts_known_forms(label):
    assert validate_week_label(label) == label


@pytest.mark.parametrize("label", ["week1", "wk1", "wk16-bad casing", "BOWL-ROSE", ""])
def test_validate_week_label_rejects_unknown_forms(label):
    with pytest.raises(ValueError):
        validate_week_label(label)


def test_canonical_game_id_is_deterministic():
    a = canonical_game_id(2026, "wk01", "baylor", "auburn")
    b = canonical_game_id(2026, "wk01", "baylor", "auburn")
    assert a == b
    assert a == "cfb-2026-wk01-baylor-at-auburn"


def test_canonical_game_id_excludes_kickoff_time_by_construction():
    # Same season/week/matchup always yields the same id regardless of any
    # kickoff-time information -- there is no kickoff parameter to vary.
    ids = {canonical_game_id(2026, "wk01", "baylor", "auburn") for _ in range(5)}
    assert len(ids) == 1


def test_canonical_game_id_rejects_same_team_both_sides():
    with pytest.raises(ValueError):
        canonical_game_id(2026, "wk01", "auburn", "auburn")


def test_canonical_game_id_no_duplicates_across_distinct_matchups():
    ids = [
        canonical_game_id(2026, "wk01", "baylor", "auburn"),
        canonical_game_id(2026, "wk01", "auburn", "baylor"),  # reversed home/away
        canonical_game_id(2026, "wk02", "baylor", "auburn"),  # different week
        canonical_game_id(2025, "wk01", "baylor", "auburn"),  # different season
        canonical_game_id(2026, "bowl-rose", "baylor", "auburn"),  # different week label
    ]
    assert len(ids) == len(set(ids))


def test_canonical_game_id_week0_and_high_week_numbers_are_valid():
    # Week 0 exists in real CFB schedules (season-openers a week early).
    assert canonical_game_id(2026, "wk00", "hawaii", "boise-state") == "cfb-2026-wk00-hawaii-at-boise-state"
    # A 12-team CFP first-round weekend can share one generic week label
    # across 4 games -- uniqueness still comes from the team-slug pair.
    a = canonical_game_id(2026, "cfp-first-round", "smu", "clemson")
    b = canonical_game_id(2026, "cfp-first-round", "indiana", "notre-dame")
    assert a != b


def test_canonical_game_id_neutral_site_is_invariant_to_vendor_home_away_disagreement():
    # This is the scenario the mission flagged explicitly: two vendors can
    # disagree about which team is "home" for a neutral-site game (e.g. a
    # Dublin/Ireland game). The ID must be identical either way.
    vendor_a = canonical_game_id(
        2026, "wk01", away_team_slug="florida-state", home_team_slug="georgia-tech", neutral_site=True
    )
    vendor_b = canonical_game_id(
        2026, "wk01", away_team_slug="georgia-tech", home_team_slug="florida-state", neutral_site=True
    )
    assert vendor_a == vendor_b
    assert vendor_a == "cfb-2026-wk01-florida-state-vs-georgia-tech"


def test_canonical_game_id_site_based_game_still_uses_away_at_home_order():
    # Non-neutral games keep the readable, order-sensitive format -- real
    # home-field advantage means the order is meaningful and vendors don't
    # actually disagree about it for a true home game.
    assert canonical_game_id(2026, "wk01", "baylor", "auburn", neutral_site=False) == "cfb-2026-wk01-baylor-at-auburn"


def test_canonical_game_id_neutral_and_site_based_ids_do_not_collide():
    site_based = canonical_game_id(2026, "wk01", "baylor", "auburn", neutral_site=False)
    neutral = canonical_game_id(2026, "wk01", "baylor", "auburn", neutral_site=True)
    assert site_based != neutral


def test_assert_unique_game_ids_passes_on_unique_input():
    assert_unique_game_ids(["cfb-2026-wk01-a-at-b", "cfb-2026-wk01-c-at-d"])


def test_assert_unique_game_ids_fails_loud_on_duplicates():
    with pytest.raises(ValueError, match="duplicate"):
        assert_unique_game_ids(["cfb-2026-wk01-a-at-b", "cfb-2026-wk01-a-at-b"])
