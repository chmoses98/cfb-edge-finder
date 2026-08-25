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
    assert names == {"cornell", "colgate", "north dakota state"}


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
