"""Name variants: fix the spellings, never invent a school.

Measured, not imagined. On the first full live run against ESPN, 24 of 27
unmatched games failed on ONE side only, and the pattern was punctuation:

    Kalshi "Delaware St."         ESPN "Delaware State"
    Kalshi "Grambling St."        ESPN "Grambling"
    Kalshi "Southern University"  ESPN "Southern"
    Kalshi "University at Albany" ESPN "UAlbany" / "Albany"

Every rule here rewrites an AFFIX. The dangerous one is dropping the
qualifier entirely, because "Ohio State" is not Ohio and "Michigan State" is
not Michigan -- both pairs are real, distinct, FBS schools playing real games.
That rule is therefore gated on the repository's own team registry.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import collect_live_context as collector  # noqa: E402


def _keys(name: str) -> set[str]:
    return collector._kalshi_team_keys({"home": name, "away": "x"})["home"]


def _espn(*names: str) -> set[str]:
    return set().union(*(collector.name_variants(n) for n in names))


# ------------------------------------------------------------- affix rewrites


def test_trailing_st_reads_as_state():
    assert "delawarestate" in collector.name_variants("Delaware St.")


def test_trailing_state_reads_as_st():
    assert "delawarest" in collector.name_variants("Delaware State")


def test_a_trailing_university_is_decoration():
    assert "southern" in collector.name_variants("Southern University")


def test_university_at_x_yields_both_x_and_ux():
    v = collector.name_variants("University at Albany")
    assert "albany" in v and "ualbany" in v


# --------------------------------------------- the rule that must never guess


def test_a_leading_saint_is_never_rewritten_to_state():
    """St. John's is a school. "State John's" is not."""
    for saint in ("St. John's", "St. Francis", "St. Thomas"):
        for variant in collector.name_variants(saint, allow_stem=True):
            assert "state" not in variant, (saint, variant)


def test_dropping_the_qualifier_is_refused_when_the_stem_is_another_school():
    """The false-match generator this guard exists to stop."""
    for name in ("Ohio St.", "Michigan St.", "Washington St.", "Oregon St.",
                 "Florida St.", "Mississippi St.", "Arizona St.", "Kansas St."):
        assert collector._stem_is_safe(name) is False, name


def test_dropping_the_qualifier_is_allowed_when_the_stem_names_nothing_else():
    """ESPN really does call Grambling State just "Grambling"."""
    assert collector._stem_is_safe("Grambling St.") is True


def test_a_stem_the_registry_does_not_know_is_no_collision():
    """The permissive branch, and why it is safe: if the stem names nothing,
    it cannot name the WRONG thing."""
    assert collector._stem_is_safe("Notarealschoolanywhere St.") is True


def test_a_name_with_no_trailing_qualifier_has_no_stem_to_drop():
    assert collector._stem_is_safe("Princeton") is False
    assert collector._stem_is_safe("Ole Miss") is False


# ------------------------------------------- the measured cases, end to end


def test_the_names_that_failed_the_live_run_now_match():
    cases = {
        "Alcorn St.": ("Alcorn State Braves", "Alcorn State"),
        "Grambling St.": ("Grambling Tigers", "Grambling"),
        "NC St.": ("NC State Wolfpack", "NC State"),
        "Southern University": ("Southern Jaguars", "Southern"),
        "University at Albany": ("UAlbany Great Danes", "UAlbany"),
        "Delaware St.": ("Delaware State Hornets", "Delaware State"),
        "Northwestern St.": ("Northwestern State Demons", "Northwestern State"),
    }
    for kalshi, espn in cases.items():
        assert _keys(kalshi) & _espn(*espn), kalshi


def test_an_abbreviation_is_left_unmatched_rather_than_guessed():
    """"App State" is Appalachian State, and that is a fact about a school --
    not a punctuation rule. This function has no way to know it, so the game
    stays honestly unenriched instead of being paired on a hunch."""
    assert not (_keys("Appalachian St.") & _espn("App State Mountaineers", "App State"))


def test_the_wrong_ohio_still_cannot_pair():
    assert not (_keys("Ohio St.") & _espn("Ohio Bobcats", "Ohio"))


def test_the_wrong_michigan_still_cannot_pair():
    assert not (_keys("Michigan St.") & _espn("Michigan Wolverines", "Michigan"))


def test_the_wrong_delaware_still_cannot_pair():
    """Delaware Blue Hens and Delaware State Hornets are different schools,
    and the stem rule refuses this one even though the St.->State rule fixes
    the real match."""
    assert not (_keys("Delaware St.") & _espn("Delaware Blue Hens", "Delaware"))


def test_an_empty_or_missing_name_yields_nothing():
    assert collector.name_variants(None) == set()
    assert collector.name_variants("   ") == set()
