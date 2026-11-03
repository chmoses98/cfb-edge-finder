"""Auburn exclusion, owner refinement 2026-11-03 -- exhaustive matrix, combos, and bypass paths.

Never recommend a position that profits from Auburn underperforming: Auburn ML NO / opponent ML YES, Auburn spread
NO / opponent spread YES (any rung, game/1H/1Q), Auburn's OWN team-total Under. Game/1Q/1H TOTAL Unders are
evaluated independently and are NOT blocked. Combos are blocked when a known leg is against Auburn, and blocked
fail-closed when legs are unknown but the text names Auburn. The rule is restriction-only and applies inside
expression selection and eligibility, not just at artifact review.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from cfb_edge_finder.decision.expression_selection import ExpressionRejection, build_option
from cfb_edge_finder.decision.owner_rules import (
    OWNER_EXCLUSION_AUBURN,
    OWNER_EXCLUSION_AUBURN_UNDETERMINED,
    auburn_exclusion,
    combo_auburn_exclusion,
    game_key_mentions_auburn,
)
from cfb_edge_finder.schemas.common import Side

GAMES = {"home": ("26NOV07ARKAUB", "ARK"), "away": ("26OCT17AUBUGA", "UGA")}


@pytest.mark.parametrize("where", ["home", "away"])
@pytest.mark.parametrize("period_series", ["KXNCAAFTOTAL", "KXNCAAF1QTOTAL", "KXNCAAF1HTOTAL"])
@pytest.mark.parametrize("side", ["YES", "NO", "yes", "no"])
def test_game_period_totals_are_never_blocked_either_side(where, period_series, side):
    game, _ = GAMES[where]
    v = auburn_exclusion(f"{period_series}-{game}-55", side)
    assert v.blocked is False and v.reason is None


@pytest.mark.parametrize("where", ["home", "away"])
@pytest.mark.parametrize("rung", ["17", "24", "31"])
def test_team_total_matrix(where, rung):
    game, opp = GAMES[where]
    series = "KXNCAAFTEAMTOTAL"
    assert auburn_exclusion(f"{series}-{game}-AUB{rung}", "NO").reason == OWNER_EXCLUSION_AUBURN  # Auburn Under
    assert auburn_exclusion(f"{series}-{game}-AUB{rung}", "YES").blocked is False  # Auburn Over
    # the opponent's own team total is not a bet on Auburn underperforming
    assert auburn_exclusion(f"{series}-{game}-{opp}{rung}", "NO").blocked is False
    assert auburn_exclusion(f"{series}-{game}-{opp}{rung}", "YES").blocked is False


@pytest.mark.parametrize("where", ["home", "away"])
@pytest.mark.parametrize("series", ["KXNCAAFGAME", "KXNCAAF1H"])
def test_moneyline_matrix_both_sides(where, series):
    game, opp = GAMES[where]
    assert auburn_exclusion(f"{series}-{game}-AUB", "NO").blocked
    assert auburn_exclusion(f"{series}-{game}-{opp}", "YES").blocked
    assert not auburn_exclusion(f"{series}-{game}-AUB", "YES").blocked
    assert not auburn_exclusion(f"{series}-{game}-{opp}", "NO").blocked


@pytest.mark.parametrize(
    "ticker",
    [
        "KXNCAAF1QMONEY-26OCT17AUBUGA-UGA",  # a series the ontology does not know, in an Auburn game
        "KXNCAAF1HTEAMTOTAL-26NOV07ARKAUB-AUB10",
        "KXNCAAFSOMETHING-26NOV07ARKAUB-ARK",
    ],
)
def test_unknown_series_in_an_auburn_game_fails_closed(ticker):
    v = auburn_exclusion(ticker, "NO")
    assert v.blocked and v.reason == OWNER_EXCLUSION_AUBURN_UNDETERMINED


@pytest.mark.parametrize("ticker", ["KXNCAAFSOMETHING-26NOV07ARKMIA-ARK", "KXNCAAFSOMETHING-26NOV07BALLTOL-BALL"])
def test_unknown_series_in_a_non_auburn_game_is_not_blocked(ticker):
    assert not auburn_exclusion(ticker, "NO").blocked


def test_game_key_matching_is_exact_at_the_ends_only():
    assert game_key_mentions_auburn("26OCT17AUBUGA") and game_key_mentions_auburn("26NOV07ARKAUB")
    assert not game_key_mentions_auburn("26OCT17UGAMIA")
    assert not game_key_mentions_auburn("26OCT17MIAUBC")  # AUB in the middle is not Auburn
    assert not game_key_mentions_auburn(None) and not game_key_mentions_auburn("garbage")


# ---- combos ----------------------------------------------------------------------------------------------
COMBO = "KXMVECROSSCATEGORY-S2026ABC-XYZ"


def test_combo_yes_with_a_leg_against_auburn_is_blocked():
    legs = [
        {"ticker": "KXNCAAFGAME-26OCT17AUBUGA-UGA", "side": "YES"},
        {"ticker": "KXNBAGAME-26OCT21MINMIA-MIN", "side": "YES"},
    ]
    v = combo_auburn_exclusion(COMBO, "YES", legs=legs)
    assert v.blocked and v.reason == OWNER_EXCLUSION_AUBURN


def test_combo_yes_with_only_pro_auburn_legs_is_allowed_but_no_is_not_assumed_safe():
    legs = [{"ticker": "KXNCAAFGAME-26OCT17AUBUGA-AUB", "side": "YES"}]
    assert combo_auburn_exclusion(COMBO, "YES", legs=legs).blocked is False
    no = combo_auburn_exclusion(COMBO, "NO", legs=legs)  # a NO pays if ANY leg fails, i.e. Auburn losing
    assert no.blocked and no.reason == OWNER_EXCLUSION_AUBURN_UNDETERMINED


def test_combo_legs_that_do_not_involve_auburn_pass():
    legs = [
        {"ticker": "KXNCAAFGAME-26OCT17UGAMIA-UGA", "side": "NO"},
        {"ticker": "KXNBAGAME-26OCT21MINMIA-MIN", "side": "YES"},
    ]
    assert combo_auburn_exclusion(COMBO, "YES", legs=legs).blocked is False
    assert combo_auburn_exclusion(COMBO, "NO", legs=legs).blocked is False


@pytest.mark.parametrize(
    "text", ["Auburn to win and Lakers over", "yes KXNCAAFGAME-26NOV07ARKAUB-ARK", "26OCT17AUBUGA"]
)
def test_combo_with_unknown_legs_but_auburn_text_fails_closed(text):
    v = combo_auburn_exclusion(COMBO, "YES", legs=None, evidence_text=text)
    assert v.blocked and v.reason == OWNER_EXCLUSION_AUBURN_UNDETERMINED


def test_combo_with_unknown_legs_and_no_auburn_evidence_is_not_attributable():
    v = combo_auburn_exclusion(COMBO, "YES", legs=None, evidence_text="Lakers over 110 and Celtics win")
    assert v.blocked is False


def test_a_non_combo_is_ignored_by_the_combo_rule():
    assert combo_auburn_exclusion("KXNCAAFGAME-26OCT17AUBUGA-UGA", "YES").blocked is False


# ---- bypass: expression selection ------------------------------------------------------------------------
def _snap(ticker, yes=0.40, no=0.62):
    return SimpleNamespace(
        executable_yes_price=yes,
        executable_no_price=no,
        market_status="active",
        fee_status="VERIFIED_CURRENT",
        captured_at="2026-11-03T12:00:00+00:00",
        semantics=SimpleNamespace(parse_status="confirmed_live", market_ticker=ticker),
    )


NOW = datetime(2026, 11, 3, 12, 1, tzinfo=UTC)


def test_expression_selection_rejects_a_position_against_auburn():
    snap = _snap("KXNCAAFGAME-26OCT17AUBUGA-UGA")
    against = build_option(snap, Side.YES, now=NOW, max_quote_age_seconds=300)
    safe = build_option(snap, Side.NO, now=NOW, max_quote_age_seconds=300)
    assert ExpressionRejection.OWNER_EXCLUSION_AUBURN in against.rejections and not against.selectable
    assert ExpressionRejection.OWNER_EXCLUSION_AUBURN not in safe.rejections and safe.selectable


def test_expression_selection_does_not_block_a_game_total_under_in_an_auburn_game():
    snap = _snap("KXNCAAFTOTAL-26OCT17AUBUGA-55")
    under = build_option(snap, Side.NO, now=NOW, max_quote_age_seconds=300)
    assert ExpressionRejection.OWNER_EXCLUSION_AUBURN not in under.rejections and under.selectable
