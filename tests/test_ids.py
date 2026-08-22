import pytest

from cfb_edge_finder.ids import canonical_game_id, slugify_team, validate_week_label


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
