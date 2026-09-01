"""Milestone D hardening: teams.fcs_identity is a minimal, exact-match-only
identity check -- NOT an FCS registry, NOT fuzzy matching, NOT modeling."""

from __future__ import annotations

from cfb_edge_finder.teams.fcs_identity import (
    build_fcs_school_name_set,
    is_known_fcs_school,
    normalize_school_name,
)

RAW_CFBD_TEAMS = [
    {"school": "Cornell", "classification": "fcs"},
    {"school": "Colgate", "classification": "fcs"},
    {"school": "Ohio State", "classification": "fbs"},
    {"school": "Texas", "classification": "fbs"},
    {"school": "North Dakota State", "classification": "fcs"},
]


def test_build_fcs_school_name_set_keeps_only_fcs():
    names = build_fcs_school_name_set(RAW_CFBD_TEAMS)
    assert names == {"cornell", "colgate", "north dakota state", "north dakota st."}


def test_fbs_teams_are_never_in_the_fcs_set():
    names = build_fcs_school_name_set(RAW_CFBD_TEAMS)
    assert "ohio state" not in names
    assert "texas" not in names


def test_is_known_fcs_school_exact_match_case_and_whitespace_insensitive():
    names = build_fcs_school_name_set(RAW_CFBD_TEAMS)
    assert is_known_fcs_school("Cornell", names) is True
    assert is_known_fcs_school("  cornell  ", names) is True
    assert is_known_fcs_school("CORNELL", names) is True


def test_is_known_fcs_school_no_fuzzy_matching():
    names = build_fcs_school_name_set(RAW_CFBD_TEAMS)
    # "Corne" is a substring/near-match of "Cornell" but must NOT match --
    # exact match only, mirroring teams.registry's own philosophy.
    assert is_known_fcs_school("Corne", names) is False
    assert is_known_fcs_school("Cornell University", names) is False


def test_is_known_fcs_school_rejects_unknown_and_empty_names():
    names = build_fcs_school_name_set(RAW_CFBD_TEAMS)
    assert is_known_fcs_school("Some Made Up School", names) is False
    assert is_known_fcs_school("", names) is False
    assert is_known_fcs_school(None, names) is False


def test_missing_school_or_classification_field_is_skipped_not_crashed():
    ragged = [{"school": "Cornell", "classification": "fcs"}, {"classification": "fcs"}, {"school": None}]
    names = build_fcs_school_name_set(ragged)
    assert names == {"cornell"}


def test_normalize_school_name_collapses_whitespace():
    assert normalize_school_name("North   Dakota  State") == "north dakota state"


# --- "St."-abbreviation generation (Milestone D closure) -------------------
# Real live bug (GH Actions run 32886794099): Kalshi's live text
# abbreviates "<X> State" as "<X> St." for FCS programs exactly like it
# does for FBS ones, but CFBD's own /teams data reports the full "State"
# spelling -- e.g. "Weber St.", "Jackson St.", "Tennessee St." never
# matched, so genuine FCS-vs-FCS/FBS-vs-FCS markets involving these
# programs fell through to an unexplained ambiguity instead.


def test_fcs_state_school_gets_both_full_and_abbreviated_forms():
    teams = [{"school": "Weber State", "classification": "fcs"}]
    names = build_fcs_school_name_set(teams)
    assert names == {"weber state", "weber st."}


def test_is_known_fcs_school_matches_the_abbreviated_state_form():
    teams = [
        {"school": "Jackson State", "classification": "fcs"},
        {"school": "Tennessee State", "classification": "fcs"},
    ]
    names = build_fcs_school_name_set(teams)
    assert is_known_fcs_school("Jackson St.", names) is True
    assert is_known_fcs_school("Tennessee St.", names) is True
    assert is_known_fcs_school("jackson st.", names) is True  # case-insensitive


def test_non_state_fcs_school_gets_no_extra_abbreviated_form():
    teams = [{"school": "Cornell", "classification": "fcs"}]
    names = build_fcs_school_name_set(teams)
    assert names == {"cornell"}


# --- non-FBS identity set (2026-09-01 forensic audit closure) --------------
# Live evidence (GH Actions run 33556291244): ~84% of a 45% "mapping
# failure" HIGH alarm was FBS-vs-known-FCS fixtures, plus Division II/III
# fixtures and a handful of deterministic Kalshi name variants
# ("Grambling St." for CFBD "Grambling") -- all deliberately-declined
# populations, none a mapping defect. The non-FBS set exists so those
# events can be classified NON_FBS_PARTICIPANT instead of collapsing
# into AMBIGUOUS_TEAM_MAPPING.

from cfb_edge_finder.teams.fcs_identity import (  # noqa: E402
    KNOWN_NON_FBS_NAME_VARIANTS,
    build_non_fbs_school_name_set,
    is_known_non_fbs_school,
)


def test_non_fbs_set_keeps_every_non_fbs_classification_and_no_fbs():
    teams = [
        {"school": "Cornell", "classification": "fcs"},
        {"school": "Edward Waters", "classification": "ii"},
        {"school": "Adrian", "classification": "iii"},
        {"school": "Ohio State", "classification": "fbs"},
    ]
    names = build_non_fbs_school_name_set(teams)
    assert "cornell" in names
    assert "edward waters" in names
    assert "adrian" in names
    assert "ohio state" not in names


def test_non_fbs_set_generates_st_abbreviated_forms_for_every_division():
    teams = [
        {"school": "Jackson State", "classification": "fcs"},
        {"school": "Kentucky State", "classification": "ii"},
    ]
    names = build_non_fbs_school_name_set(teams)
    assert is_known_non_fbs_school("Jackson St.", names) is True
    assert is_known_non_fbs_school("Kentucky St.", names) is True


def test_verified_kalshi_variant_activates_only_when_canonical_name_present():
    # "Grambling St." is a real live Kalshi token whose CFBD school name
    # is "Grambling" (no "State"), so the generic St.-abbreviation rule
    # can never produce it -- only the verified variant table can.
    with_canonical = build_non_fbs_school_name_set([{"school": "Grambling", "classification": "fcs"}])
    assert is_known_non_fbs_school("Grambling St.", with_canonical) is True
    # Without the canonical CFBD row the variant must NOT activate: the
    # table asserts identity against current CFBD data, never on its own.
    without_canonical = build_non_fbs_school_name_set([{"school": "Cornell", "classification": "fcs"}])
    assert is_known_non_fbs_school("Grambling St.", without_canonical) is False


def test_every_variant_target_is_a_normalized_name_not_a_variant_key():
    # The table must map variant -> canonical CFBD spelling, normalized;
    # a variant pointing at another variant (or unnormalized text) would
    # silently never activate.
    for variant, canonical in KNOWN_NON_FBS_NAME_VARIANTS.items():
        assert variant == variant.strip().casefold()
        assert canonical == canonical.strip().casefold()
        assert canonical not in KNOWN_NON_FBS_NAME_VARIANTS


def test_is_known_non_fbs_school_no_fuzzy_matching():
    teams = [{"school": "Southern", "classification": "fcs"}]
    names = build_non_fbs_school_name_set(teams)
    # "Southern University" is a verified variant of CFBD "Southern"...
    assert is_known_non_fbs_school("Southern University", names) is True
    # ...but any other unlisted variation must never match.
    assert is_known_non_fbs_school("Southern Univ", names) is False
    assert is_known_non_fbs_school("The Southern University", names) is False
    assert is_known_non_fbs_school("", names) is False
    assert is_known_non_fbs_school(None, names) is False


def test_fbs_registry_names_never_classified_non_fbs():
    # A name that resolves through the FBS registry must not also sit in
    # the non-FBS set built from CFBD's own FBS rows -- e.g. Washington
    # State (FBS) may appear on Kalshi as "Washington St."; only a
    # non-FBS "<X> State" school may generate that abbreviated form.
    teams = [
        {"school": "Washington State", "classification": "fbs"},
        {"school": "Weber State", "classification": "fcs"},
    ]
    names = build_non_fbs_school_name_set(teams)
    assert "washington state" not in names
    assert "washington st." not in names
    assert "weber st." in names
