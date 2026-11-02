"""Owner rules (2026-11-02): 5U per-bet ceiling, never against Auburn, no havoc input.

Every rule is restriction-only and visible. These tests pin the matrix of what is and is not
'against Auburn', the mapping edge cases ('Auburn' vs 'AUB'), the clamp, the integration into the
RUN CFB review (a blocked candidate stays in the artifact), and the havoc ban.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.decision.owner_rules import (
    MAX_BET_DOLLARS,
    OWNER_EXCLUSION_AUBURN,
    OWNER_EXCLUSION_AUBURN_UNDETERMINED,
    PER_BET_CAP_APPLIED,
    assert_no_havoc,
    auburn_exclusion,
    clamp_stake,
)
from cfb_edge_finder.execution.pit_review import ReviewContext, annotate_candidate_artifact, review_candidate
from cfb_edge_finder.scripting.matchup import DIMENSION_CONTEXT_METRICS, DIMENSION_EDGE_METRICS

HOME = "26NOV07ARKAUB"  # Arkansas at Auburn: Auburn is HOME
AWAY = "26OCT17AUBUGA"  # Auburn at Georgia: Auburn is AWAY
SRC = Path(__file__).resolve().parents[1] / "src" / "cfb_edge_finder"


def t(series: str, game: str, token: str) -> str:
    return f"{series}-{game}-{token}"


# ---- the Auburn matrix ---------------------------------------------------------------------------------
@pytest.mark.parametrize("game,aub,opp", [(HOME, "AUB", "ARK"), (AWAY, "AUB", "UGA")])
@pytest.mark.parametrize("series", ["KXNCAAFGAME", "KXNCAAF1H"])
def test_moneyline_direction(game, aub, opp, series):
    cases = {
        (aub, "YES"): False,  # with Auburn
        (aub, "NO"): True,  # Auburn does not win
        (opp, "YES"): True,  # opponent wins
        (opp, "NO"): False,  # opponent does not win == Auburn wins (no tie)
    }
    for (token, side), blocked in cases.items():
        v = auburn_exclusion(t(series, game, token), side)
        assert v.blocked is blocked, (series, token, side, v)
        assert v.reason == (OWNER_EXCLUSION_AUBURN if blocked else None)


@pytest.mark.parametrize("game,aub,opp", [(HOME, "AUB", "ARK"), (AWAY, "AUB", "UGA")])
@pytest.mark.parametrize("series", ["KXNCAAFSPREAD", "KXNCAAF1HSPREAD", "KXNCAAF1QSPREAD"])
def test_spread_direction_including_alternates(game, aub, opp, series):
    for rung in ("3", "7", "13"):
        assert auburn_exclusion(t(series, game, aub + rung), "YES").blocked is False  # Auburn covers
        assert auburn_exclusion(t(series, game, aub + rung), "NO").blocked is True  # Auburn fails to cover
        assert auburn_exclusion(t(series, game, opp + rung), "YES").blocked is True  # opponent covers
        assert auburn_exclusion(t(series, game, opp + rung), "NO").blocked is False  # opponent fails to cover


@pytest.mark.parametrize("game", [HOME, AWAY])
@pytest.mark.parametrize("series", ["KXNCAAFTOTAL", "KXNCAAF1HTOTAL", "KXNCAAF1QTOTAL"])
def test_game_and_period_totals_are_unaffected(game, series):
    for side in ("YES", "NO"):
        assert auburn_exclusion(t(series, game, "55"), side).blocked is False


def test_team_total_blocks_only_the_under_on_auburns_own_score():
    assert auburn_exclusion(t("KXNCAAFTEAMTOTAL", HOME, "AUB24"), "NO").blocked is True
    assert auburn_exclusion(t("KXNCAAFTEAMTOTAL", HOME, "AUB24"), "YES").blocked is False
    assert auburn_exclusion(t("KXNCAAFTEAMTOTAL", HOME, "ARK24"), "NO").blocked is False
    assert auburn_exclusion(t("KXNCAAFTEAMTOTAL", HOME, "ARK24"), "YES").blocked is False


# ---- mapping edge cases --------------------------------------------------------------------------------
@pytest.mark.parametrize("side", ["no", "No", " NO "])
def test_side_is_case_and_space_insensitive(side):
    assert auburn_exclusion(t("KXNCAAFGAME", HOME, "AUB"), side).blocked is True


def test_other_game_is_unaffected():
    assert auburn_exclusion(t("KXNCAAFGAME", "26NOV03OHIOAKR", "AKR"), "NO").blocked is False


def test_a_team_token_that_merely_contains_aub_is_not_auburn():
    # Exact token match, never substring: a hypothetical 'HAUB' must not trigger the exclusion.
    v = auburn_exclusion(t("KXNCAAFGAME", "26NOV07HAUBXYZ", "HAUB"), "NO")
    assert v.blocked is False


def test_title_alone_makes_an_undecodable_auburn_contract_fail_closed():
    v = auburn_exclusion("KXNCAAFGAME-GARBAGE", "YES", game_title="Arkansas at Auburn")
    assert v.blocked is True and v.reason == OWNER_EXCLUSION_AUBURN_UNDETERMINED


def test_title_cannot_loosen_a_decodable_verdict():
    # Title says Auburn; the ticker is decodable and NOT against Auburn -> still allowed.
    assert auburn_exclusion(t("KXNCAAFGAME", HOME, "AUB"), "YES", game_title="Arkansas at Auburn").blocked is False
    assert auburn_exclusion(t("KXNCAAFGAME", HOME, "AUB"), "NO", game_title="Arkansas at Auburn").blocked is True


@pytest.mark.parametrize("title", ["Auburn Montgomery at Troy", "Troy at Auburn Montgomery"])
def test_auburn_montgomery_is_not_auburn(title):
    assert auburn_exclusion("KXNCAAFGAME-GARBAGE", "YES", game_title=title).blocked is False


def test_title_with_auburn_but_unattributable_team_token_fails_closed():
    v = auburn_exclusion(t("KXNCAAFGAME", "26NOV07XYZQRS", "XYZ"), "NO", game_title="Auburn at Elsewhere")
    assert v.blocked is True and v.reason == OWNER_EXCLUSION_AUBURN_UNDETERMINED


def test_combos_are_untouched_without_an_auburn_title():
    assert auburn_exclusion("KXMVECROSSCATEGORY-S2026ABC-XYZ", "NO").blocked is False


# ---- the per-bet clamp ---------------------------------------------------------------------------------
def test_clamp_is_a_hard_5u_ceiling_with_a_disclosed_reason():
    assert MAX_BET_DOLLARS == 375.0
    over = clamp_stake(450.0)
    assert over.clamped and over.effective_dollars == 375.0 and over.reason == PER_BET_CAP_APPLIED
    at = clamp_stake(375.0)
    assert not at.clamped and at.effective_dollars == 375.0 and at.reason is None
    assert clamp_stake(None).effective_dollars is None  # a missing proposal is never assumed
    small = clamp_stake(100.0, unit_dollars=50.0)  # a different unit moves the ceiling: 5 x $50 = $250
    assert small.max_dollars == 250.0 and small.effective_dollars == 100.0 and small.clamped is False
    with pytest.raises(ValueError):
        clamp_stake(-1.0)


# ---- integration into the RUN CFB review (restriction-only) ---------------------------------------------
def _ctx(**kw):
    now = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
    base = dict(
        decision_time=now,
        quote_captured_at=now - timedelta(minutes=5),
        packets={HOME: {"title": "Arkansas at Auburn", "kickoff": "2026-11-07T17:45:00Z"}},
        handicaps={},
    )
    base.update(kw)
    return ReviewContext(**base)


def _cand(token: str, side: str, **kw):
    c = {
        "game_key": HOME,
        "market": t("KXNCAAFSPREAD", HOME, token),
        "side": side,
        "kickoff": "2026-11-07T17:45:00Z",
        "kalshi_executable_price": 0.45,
        "fair_probability": 0.6,
        "fair_probability_range": [0.55, 0.65],
    }
    c.update(kw)
    return c


def test_review_marks_an_against_auburn_candidate_not_actionable_with_the_named_reason():
    against = review_candidate(_cand("AUB3", "NO"), _ctx())
    assert against["pit_review"]["actionable"] is False
    assert OWNER_EXCLUSION_AUBURN in against["pit_review"]["blocking_guards"]
    assert against["owner_rules"]["auburn"]["blocked"] is True
    # with Auburn: the owner rule does not block (other guards may, but never the owner rule)
    with_auburn = review_candidate(_cand("AUB3", "YES"), _ctx())
    assert OWNER_EXCLUSION_AUBURN not in with_auburn["pit_review"]["blocking_guards"]
    assert with_auburn["owner_rules"]["auburn"]["blocked"] is False


def test_annotating_an_artifact_never_removes_or_reorders_candidates():
    art = {"candidates": [_cand("AUB3", "NO"), _cand("ARK7", "YES"), _cand("ARK7", "NO")]}
    before = [c["market"] + c["side"] for c in art["candidates"]]
    annotate_candidate_artifact(art, _ctx())
    assert [c["market"] + c["side"] for c in art["candidates"]] == before
    blocked = [c["pit_review"]["actionable"] for c in art["candidates"]]
    assert blocked[0] is False and blocked[1] is False  # AUB3 NO, ARK7 YES are against Auburn
    assert OWNER_EXCLUSION_AUBURN in art["pit_review"]["blocked_by"]


def test_review_reports_the_clamp_on_a_proposed_stake():
    cand = _cand("AUB3", "YES", recommendation_id="rec-1")
    out = review_candidate(cand, _ctx(proposed_stakes={"rec-1": 500.0}))
    sizing = out["owner_rules"]["sizing"]
    assert sizing["clamped"] is True and sizing["effective_dollars"] == 375.0
    assert sizing["reason"] == PER_BET_CAP_APPLIED
    none = review_candidate(cand, _ctx())["owner_rules"]["sizing"]
    assert none["proposed_dollars"] is None and none["clamped"] is False


# ---- havoc is never an input ----------------------------------------------------------------------------
def test_assert_no_havoc_detects_any_spelling():
    assert_no_havoc(["success_rate", "sack_rate_allowed"])
    for bad in ("havoc_allowed", "HAVOC", "b_havoc_def", "winner_Havoc_edge"):
        with pytest.raises(ValueError):
            assert_no_havoc(["ok", bad])


def test_live_script_engine_edge_and_context_tables_contain_no_havoc():
    names = [m for group in DIMENSION_EDGE_METRICS.values() for m in group]
    names += [m for group in DIMENSION_CONTEXT_METRICS.values() for m in group]
    assert_no_havoc(names, where="scripting.matchup tables")
    assert DIMENSION_EDGE_METRICS["disruption"] == ("sack_rate_allowed",)


#: Havoc appears here ONLY as documented exclusions or in frozen/vendored research that no live path imports.
_HAVOC_ALLOWED = {
    "decision/owner_rules.py": "the rule itself",
    "projections/discrete_scoring.py": "docstring: 'havoc metrics: excluded by owner instruction'",
    "scripting/metrics.py": "computed research proxy kept as-filed; excluded from every edge (matchup.py)",
    "scripting/matchup.py": "comment documenting the exclusion",
    "signal_discovery/screen.py": "comment/none",
    "modeling/v2/research_features.py": "VENDORED + hash-pinned shadow-model feature builder (tests/test_v2_shadow.py)",
    "archetype_research/analysis.py": "frozen historical archetype study, not a live path",
}


def test_havoc_appears_only_in_the_documented_places():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if re.search("havoc", path.read_text(encoding="utf-8"), re.IGNORECASE) and rel not in _HAVOC_ALLOWED:
            offenders.append(rel)
    assert not offenders, f"havoc referenced outside the documented exclusions: {offenders}"


@pytest.mark.parametrize("package", ["execution", "decision", "expression", "projections", "sizing"])
def test_no_live_package_imports_the_havoc_bearing_research_modules(package):
    bad = []
    for path in (SRC / package).rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+cfb_edge_finder\.(modeling\.v2|archetype_research)", text, re.MULTILINE):
            bad.append(path.name)
    assert not bad, f"{package} imports havoc-bearing research code: {bad}"
