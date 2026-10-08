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


# ------------------------------------------- a separator inside a school's own name


def _moneyline_pair(game_key: str, away: tuple[str, str], home: tuple[str, str]):
    """The two game-winner contracts Kalshi lists for a game: (code, YES label) per side, plus the tie."""
    rows = [
        market(
            f"KXNCAAFGAME-{game_key}-{code}", family="game_moneyline", title=f"Will {label} win?", yes_sub_title=label
        )
        for code, label in (away, home)
    ]
    rows.append(market(f"KXNCAAF1H-{game_key}-TIE", family="first_half_moneyline", title="Tie", yes_sub_title="Tie"))
    return rows


def test_university_at_albany_at_stony_brook_is_two_real_teams():
    # The live milestone (26OCT17ALBYSTON, data/live/games/26OCT17ALBYSTON.json). Partitioning on the FIRST " at "
    # published away "University" and home "Albany at Stony Brook"; the contracts name the real two.
    rows = _moneyline_pair("26OCT17ALBYSTON", ("ALBY", "University at Albany"), ("STON", "Stony Brook"))
    game_teams = build_game_teams("University at Albany at Stony Brook", rows)
    assert game_teams.away_name == "University at Albany"
    assert game_teams.home_name == "Stony Brook"
    assert game_teams.home_away_source == "milestone_title_at"
    assert game_teams.home_away_confidence == "stated"
    # Both sides now carry their exchange code (before: STON matched no side, so home had no code at all).
    assert game_teams.code_to_slot == {"ALBY": TeamSlot.AWAY.value, "STON": TeamSlot.HOME.value}
    assert "University" not in (game_teams.away_name, game_teams.home_name)


def test_a_school_with_at_in_its_name_can_be_home_too():
    rows = _moneyline_pair("26OCT24STONALBY", ("STON", "Stony Brook"), ("ALBY", "University at Albany"))
    game_teams = build_game_teams("Stony Brook at University at Albany", rows)
    assert (game_teams.away_name, game_teams.home_name) == ("Stony Brook", "University at Albany")
    assert game_teams.code_to_slot == {"STON": TeamSlot.AWAY.value, "ALBY": TeamSlot.HOME.value}


def test_an_at_inside_a_name_never_hijacks_a_vs_title():
    rows = _moneyline_pair("26SEP06UNHALBY", ("UNH", "New Hampshire"), ("ALBY", "University at Albany"))
    game_teams = build_game_teams("New Hampshire vs University at Albany", rows)
    assert (game_teams.away_name, game_teams.home_name) == ("New Hampshire", "University at Albany")
    assert game_teams.home_away_confidence == "convention"


def test_several_separators_and_no_contract_evidence_is_reported_not_guessed():
    game_teams = build_game_teams("University at Albany at Stony Brook", [])
    assert game_teams.away_name is None and game_teams.home_name is None
    assert game_teams.home_away_source == "ambiguous_title"
    assert not game_teams.known


def test_ordinary_names_keep_parsing_exactly_as_before():
    cases = {
        "Miami (OH) at Miami (FL)": ("Miami (OH)", "Miami (FL)"),
        "Appalachian St. at Louisiana": ("Appalachian St.", "Louisiana"),
        "Louisiana Tech at NC State": ("Louisiana Tech", "NC State"),
        "Penn at Penn State": ("Penn", "Penn State"),
        "UMass at LIU": ("UMass", "LIU"),
        "Southeast Missouri St. at Central Connecticut St.": ("Southeast Missouri St.", "Central Connecticut St."),
        "Northwestern St. at Missouri St.": ("Northwestern St.", "Missouri St."),
        "Sacramento St. at Bowling Green": ("Sacramento St.", "Bowling Green"),
    }
    for title, (away, home) in cases.items():
        without = build_game_teams(title, [])
        assert (without.away_name, without.home_name, without.home_away_confidence) == (away, home, "stated"), title
        codes = (away[:4].upper().replace(" ", ""), home[:4].upper().replace(" ", "") + "H")
        with_contracts = build_game_teams(title, _moneyline_pair("26OCT31TEST", (codes[0], away), (codes[1], home)))
        assert (with_contracts.away_name, with_contracts.home_name) == (away, home), title
