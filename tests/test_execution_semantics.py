"""Contract semantics: which team, which rung, which direction.

These are the tests that stand between a 3,700-contract scan and a
silently inverted ladder. Getting a spread's team backwards produces a
perfectly well-formed, fully reconciled, completely wrong answer -- the
counting invariants cannot see it, so it is checked directly.
"""

from __future__ import annotations

from cfb_edge_finder.execution.semantics import (
    ContractKind,
    TeamSlot,
    build_game_teams,
    derive_semantics,
)
from tests.execution_fakes import GAME_KEY, market, standard_markets


def teams(title: str = "LSU at Ole Miss"):
    return build_game_teams(title, standard_markets())


def semantics_for(ticker: str, title: str = "LSU at Ole Miss"):
    rows = standard_markets()
    game_teams = build_game_teams(title, rows)
    contract = next(m for m in rows if m["market_ticker"] == ticker)
    return derive_semantics(contract, game_teams)


# ------------------------------------------------------------ home/away


def test_milestone_at_phrasing_states_home_and_away():
    game_teams = teams()
    assert game_teams.away_name == "LSU"
    assert game_teams.home_name == "Ole Miss"
    assert game_teams.home_away_confidence == "stated"


def test_vs_phrasing_is_labelled_a_convention_not_a_fact():
    game_teams = teams("LSU vs Ole Miss")
    assert game_teams.away_name == "LSU"
    assert game_teams.home_name == "Ole Miss"
    assert game_teams.home_away_confidence == "convention"
    assert game_teams.home_away_source == "event_title_order"


def test_team_codes_and_uuids_are_learned_from_the_contracts():
    game_teams = teams()
    assert game_teams.code_to_slot["LSU"] == TeamSlot.AWAY.value
    assert game_teams.code_to_slot["MISS"] == TeamSlot.HOME.value


# ------------------------------------------------------- alternate rungs


def test_alternate_spread_rungs_preserve_team_line_and_direction():
    for line in (2.5, 6.5, 9.5):
        away = semantics_for(f"KXNCAAFSPREAD-{GAME_KEY}-LSU{int(line + 1)}")
        assert away.kind == ContractKind.SPREAD.value
        assert away.team == TeamSlot.AWAY.value
        assert away.threshold == line
        assert away.comparator == "greater"
        assert away.requires == "period_distribution"

        home = semantics_for(f"KXNCAAFSPREAD-{GAME_KEY}-MISS{int(line + 1)}")
        assert home.team == TeamSlot.HOME.value
        assert home.threshold == line


def test_alternate_spread_rungs_are_distinct_not_collapsed():
    lines = {
        semantics_for(f"KXNCAAFSPREAD-{GAME_KEY}-LSU{int(line + 1)}").threshold
        for line in (2.5, 6.5, 9.5)
    }
    assert lines == {2.5, 6.5, 9.5}


def test_alternate_total_rungs_preserve_semantics():
    for line in (44.5, 51.5):
        total = semantics_for(f"KXNCAAFTOTAL-{GAME_KEY}-{int(line + 1)}")
        assert total.kind == ContractKind.TOTAL.value
        assert total.team == TeamSlot.NONE.value
        assert total.threshold == line
        assert total.period == "full_game"


def test_team_total_semantics_keep_the_team_and_the_rung():
    away = semantics_for(f"KXNCAAFTEAMTOTAL-{GAME_KEY}-LSU21")
    assert away.kind == ContractKind.TEAM_TOTAL.value
    assert away.team == TeamSlot.AWAY.value
    assert away.threshold == 20.5

    home = semantics_for(f"KXNCAAFTEAMTOTAL-{GAME_KEY}-MISS28")
    assert home.team == TeamSlot.HOME.value
    assert home.threshold == 27.5


def test_a_team_total_is_never_read_as_a_game_total():
    team_total = semantics_for(f"KXNCAAFTEAMTOTAL-{GAME_KEY}-LSU21")
    game_total = semantics_for(f"KXNCAAFTOTAL-{GAME_KEY}-45")
    assert team_total.kind != game_total.kind
    assert team_total.team != game_total.team


# ------------------------------------------------------- explicit-only


def test_a_tie_prices_only_from_an_explicit_probability():
    tie = semantics_for(f"KXNCAAF1H-{GAME_KEY}-TIE")
    assert tie.kind == ContractKind.TIE.value
    assert tie.requires == "explicit_probability"
    assert "point mass" in tie.rationale


def test_first_td_and_double_result_price_only_explicitly():
    for ticker in (f"KXNCAAFFIRSTTDTEAM-{GAME_KEY}-LSU", f"KXNCAAF1HFT-{GAME_KEY}-LSULSU"):
        contract = semantics_for(ticker)
        assert contract.requires == "explicit_probability"
        assert contract.yes_meaning


def test_moneyline_prices_from_the_period_margin_distribution():
    moneyline = semantics_for(f"KXNCAAFGAME-{GAME_KEY}-MISS")
    assert moneyline.kind == ContractKind.MONEYLINE.value
    assert moneyline.team == TeamSlot.HOME.value
    assert moneyline.requires == "period_distribution"


# -------------------------------------------------------- yes/no meaning


def test_no_meaning_is_the_negation_and_never_a_copy_of_yes():
    """Kalshi publishes the same text for `yes_sub_title` and
    `no_sub_title`. Taking that at face value would tell a reader that
    buying NO means the same thing as buying YES."""
    for contract in (
        semantics_for(f"KXNCAAFSPREAD-{GAME_KEY}-LSU10"),
        semantics_for(f"KXNCAAFTOTAL-{GAME_KEY}-45"),
        semantics_for(f"KXNCAAFTEAMTOTAL-{GAME_KEY}-LSU21"),
    ):
        assert contract.no_meaning
        assert contract.no_meaning != contract.yes_meaning


def test_an_unresolvable_team_is_reported_not_guessed():
    rows = standard_markets()
    game_teams = build_game_teams("LSU at Ole Miss", rows)
    stranger = market(
        f"KXNCAAFSPREAD-{GAME_KEY}-ZZZ7",
        family="game_spread",
        title="Somebody Else wins by over 6.5 points",
        floor_strike=6.5,
    )
    contract = derive_semantics(stranger, game_teams)
    assert contract.team == TeamSlot.UNRESOLVED.value
    assert contract.status == "unresolved_team"
