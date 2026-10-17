"""Which Kalshi event tickers are physical games, and which are season-level inventory.

The regression: `KXNCAAFCFPEXPAND-02DEC26T24` ("Will the CFP approve a 24-team playoff before Dec 2, 2026?")
matched the game grammar (`02DEC26` read as YY=02 MON=DEC DD=26, tail `T24` read as a team code). It entered the
catalog as a game with no teams, reached the board as a participant-less card, and failed the Script Engine's identity
step (`IDENTITY_FAIL: 02DEC26T24`) on every run. It is real inventory, so it must stay visible as a season-level event,
and it must never be a game.
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.catalog.artifacts import build_catalog
from cfb_edge_finder.catalog.discovery import MarketDiscovery
from cfb_edge_finder.catalog.identity import classify_event_ticker, parse_event_ticker
from tests.catalog_fakes import FakeKalshi, make_event, make_market, make_milestone, make_series
from tests.test_catalog_discovery import NOW

CFP_EXPAND = "KXNCAAFCFPEXPAND-02DEC26T24"


def test_the_cfp_expansion_question_is_not_a_game():
    parts, reason = classify_event_ticker(CFP_EXPAND)
    assert parts is None
    assert reason == "tail_is_not_two_team_codes"
    assert parse_event_ticker(CFP_EXPAND) is None


@pytest.mark.parametrize(
    "ticker",
    [
        "KXNCAAFB12-26",  # Big 12 championship winner (the BYU futures market lives here)
        "KXNCAAFB12QUAL-26",
        "KXNCAAFSEC-26",
        "KXNCAAFAWARD-26APPOY",
        "KXNCAAFCFPPOLL-26NOV17R1",
        "KXNCAAFSTADIUM-29APR01ALOHA",
        "KXNCAAFBIGTENWINS-26W10",
    ],
)
def test_season_level_inventory_is_never_a_game(ticker):
    assert parse_event_ticker(ticker) is None


@pytest.mark.parametrize(
    ("ticker", "game_key"),
    [
        ("KXNCAAFGAME-26OCT30ASUBYU", "26OCT30ASUBYU"),
        ("KXNCAAFSPREAD-26OCT17GTVT", "26OCT17GTVT"),  # two-letter codes
        ("KXNCAAFTOTAL-26OCT17LTULM", "26OCT17LTULM"),
        ("KXNCAAF1HSPREAD-26OCT17MASSBUFF", "26OCT17MASSBUFF"),
        ("KXNCAAFGAME-26SEP19UGAARK", "26SEP19UGAARK"),
    ],
)
def test_real_games_still_parse(ticker, game_key):
    parts = parse_event_ticker(ticker)
    assert parts is not None
    assert parts.game_key == game_key


def test_the_cfp_expansion_event_is_kept_as_season_level_inventory_not_a_game():
    game = "26SEP19UGAARK"
    named = f"KXNCAAFGAME-{game}"
    expand = make_event(
        CFP_EXPAND,
        title="Will the CFP approve a 24-team playoff before Dec 2, 2026?",
        sub_title="Approval must be confirmed by a contract Source Agency by Dec 2, 2026",
        competition_scope=None,
    )
    series = [*[make_series("KXNCAAFGAME", "College Football Game")], make_series("KXNCAAFCFPEXPAND", "CFP expansion")]
    api = FakeKalshi(
        milestones=[make_milestone(game, (named,))],
        events={named: make_event(named), CFP_EXPAND: expand},
        markets_by_event={
            named: [make_market(f"{named}-UGA", floor_strike=None)],
            CFP_EXPAND: [make_market(f"{CFP_EXPAND}-T24", title="24-team playoff approved")],
        },
        series=series,
        events_by_series={"KXNCAAFCFPEXPAND": [expand]},
    )
    run = MarketDiscovery(api).run(as_of=NOW)

    assert list(run.games) == [game]
    assert "02DEC26T24" not in run.games
    assert CFP_EXPAND not in run.events_only_in_series_sweep
    assert CFP_EXPAND in run.season_level_events  # kept, not discarded

    catalog = build_catalog(run)
    assert [g["game_key"] for g in catalog["games"]] == [game]
    kept = {e["event_ticker"]: e for e in catalog["season_level_events"]}[CFP_EXPAND]
    assert kept["title"] == "Will the CFP approve a 24-team playoff before Dec 2, 2026?"
    assert kept["scope"] == "season_level"
    assert kept["scope_reason"] == "tail_is_not_two_team_codes"
    assert catalog["totals"]["season_level_events"] == 1


def test_big_12_champion_futures_are_season_level_with_a_named_reason():
    """BYU's Big 12 championship position is a futures market (KXNCAAFB12-26): classified, never a game."""
    futures = "KXNCAAFB12-26"
    event = make_event(futures, title="College Football Big 12 Championship Winner", competition_scope="Champion")
    api = FakeKalshi(
        milestones=[],
        events={futures: event},
        markets_by_event={futures: [make_market(f"{futures}-BYU", title="BYU wins the Big 12")]},
        series=[make_series("KXNCAAFB12", "Big 12 Champion")],
        events_by_series={"KXNCAAFB12": [event]},
    )
    catalog = build_catalog(MarketDiscovery(api).run(as_of=NOW))
    assert catalog["games"] == []
    only = catalog["season_level_events"]
    assert [e["event_ticker"] for e in only] == [futures]
    assert only[0]["scope"] == "season_level"
    assert only[0]["scope_reason"] == "no_game_key"
