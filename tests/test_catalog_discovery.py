"""Discovery end-to-end, against a Kalshi stand-in.

The cases here are the ones the mission names explicitly: many events for
one physical game, a game with one or two families, a game with many
families, an unknown new series, an unknown family retained, an empty
event, a failed event fetch, a partial pagination failure, duplicated
markets, status changes, a newly-created event and market appearing after
an initial capture, no silent loss on API error, deterministic output, and
physical-game grouping.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cfb_edge_finder.catalog.artifacts import (
    build_catalog,
    build_flat_index,
    catalog_content_fingerprint,
)
from cfb_edge_finder.catalog.classification import MarketFamilyLabel
from cfb_edge_finder.catalog.discovery import MarketDiscovery
from tests.catalog_fakes import (
    FakeKalshi,
    make_event,
    make_market,
    make_milestone,
    make_series,
)

NOW = datetime(2026, 9, 17, 22, 0, tzinfo=UTC)

CFB_SERIES = [
    make_series("KXNCAAFGAME", "College Football Game"),
    make_series("KXNCAAFSPREAD", "College Football Spread"),
    make_series("KXNCAAFTOTAL", "College Football Total Points"),
    make_series("KXNCAAF1HSPREAD", "College Football 1st Half Spread"),
    make_series("KXNCAAFTEAMSACK", "College Football Team Total Sacks"),
    make_series("KXNFLGAME", "NFL Game", tags=("Football",)),  # must be ignored
]


def _rich_game_fake(**overrides) -> FakeKalshi:
    """One physical game, five events across five series -- the
    'many market families' case."""
    game = "26SEP19UGAARK"
    event_tickers = tuple(
        f"{s}-{game}" for s in ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAF1HSPREAD", "KXNCAAFTEAMSACK")
    )
    events = {t: make_event(t, competition_scope=t.split("-")[0]) for t in event_tickers}
    events[f"KXNCAAFGAME-{game}"] = make_event(f"KXNCAAFGAME-{game}", competition_scope="Game")
    markets = {
        f"KXNCAAFGAME-{game}": [
            make_market(f"KXNCAAFGAME-{game}-UGA", title="Georgia wins", floor_strike=None),
            make_market(f"KXNCAAFGAME-{game}-ARK", title="Arkansas wins", floor_strike=None),
        ],
        f"KXNCAAFSPREAD-{game}": [
            make_market(
                f"KXNCAAFSPREAD-{game}-UGA{i}",
                title=f"Georgia wins by over {i}.5 points",
                floor_strike=i + 0.5,
            )
            for i in range(1, 6)
        ],
        f"KXNCAAFTOTAL-{game}": [
            make_market(f"KXNCAAFTOTAL-{game}-{i}", title=f"over {i}.5 total points", floor_strike=i + 0.5)
            for i in (44, 46, 48)
        ],
        f"KXNCAAF1HSPREAD-{game}": [
            make_market(
                f"KXNCAAF1HSPREAD-{game}-ARK{i}",
                title=f"Arkansas wins 1H by over {i}.5 points",
                floor_strike=i + 0.5,
            )
            for i in (3, 4)
        ],
        f"KXNCAAFTEAMSACK-{game}": [
            make_market(f"KXNCAAFTEAMSACK-{game}-UGA2", title="Georgia records over 2.5 sacks", floor_strike=2.5)
        ],
    }
    kwargs = {
        "milestones": [make_milestone(game, event_tickers)],
        "events": events,
        "markets_by_event": markets,
        "series": CFB_SERIES,
        "events_by_series": {},
        "multivariate": [],
    }
    kwargs.update(overrides)
    return FakeKalshi(**kwargs)


def test_one_physical_game_gathers_every_series_event():
    api = _rich_game_fake()
    run = MarketDiscovery(api).run(as_of=NOW)
    assert list(run.games) == ["26SEP19UGAARK"]
    game = run.games["26SEP19UGAARK"]
    assert len(game.events) == 5
    assert len(game.contracts) == 13
    assert game.completeness.native_game_markets_complete is True
    # Grouped under ONE physical game, not five separate ones.
    assert {c.game_key for c in game.contracts} == {"26SEP19UGAARK"}


def test_many_families_are_all_classified_and_none_dropped():
    api = _rich_game_fake()
    run = MarketDiscovery(api).run(as_of=NOW)
    families = run.games["26SEP19UGAARK"].family_distribution
    assert families == {
        "first_half_spread": 2,
        "game_moneyline": 2,
        "game_spread": 5,
        "game_total": 3,
        "team_stat_prop": 1,
    }
    assert run.games["26SEP19UGAARK"].completeness.markets_unknown == 0


def test_game_with_only_one_family():
    game = "26SEP20THINONE"
    api = FakeKalshi(
        milestones=[make_milestone(game, (f"KXNCAAFGAME-{game}",))],
        events={f"KXNCAAFGAME-{game}": make_event(f"KXNCAAFGAME-{game}")},
        markets_by_event={
            f"KXNCAAFGAME-{game}": [
                make_market(f"KXNCAAFGAME-{game}-AAA", floor_strike=None),
                make_market(f"KXNCAAFGAME-{game}-BBB", floor_strike=None),
            ]
        },
        series=CFB_SERIES,
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    assert run.games[game].family_distribution == {"game_moneyline": 2}
    assert run.games[game].completeness.native_game_markets_complete is True


def test_unknown_new_series_is_discovered_and_retained_as_unknown():
    """Kalshi launches KXNCAAFTELEPORT tomorrow. It must appear in the
    catalog, attached to the right game, labelled unknown -- NOT dropped."""
    game = "26SEP19UGAARK"
    novel = f"KXNCAAFTELEPORT-{game}"
    api = FakeKalshi(
        milestones=[make_milestone(game, (f"KXNCAAFGAME-{game}", novel))],
        events={
            f"KXNCAAFGAME-{game}": make_event(f"KXNCAAFGAME-{game}"),
            novel: make_event(novel, competition_scope="Teleportation"),
        },
        markets_by_event={
            f"KXNCAAFGAME-{game}": [make_market(f"KXNCAAFGAME-{game}-UGA", floor_strike=None)],
            novel: [
                make_market(f"{novel}-X", title="Something Kalshi has never listed before", floor_strike=None)
            ],
        },
        series=CFB_SERIES,
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    contracts = {c.market_ticker: c for c in run.games[game].contracts}
    assert f"{novel}-X" in contracts, "a novel series' market was DROPPED"
    assert contracts[f"{novel}-X"].classification.family == MarketFamilyLabel.UNKNOWN
    assert run.games[game].completeness.markets_unknown == 1
    # Unknown does not make the game incomplete -- it is captured, just unlabelled.
    assert run.games[game].completeness.native_game_markets_complete is True


def test_empty_event_is_complete_with_zero_markets():
    """An event that genuinely has no markets is COMPLETE at zero -- and
    must be distinguishable from the failure case below."""
    game = "26SEP19UGAARK"
    api = FakeKalshi(
        milestones=[make_milestone(game, (f"KXNCAAFGAME-{game}",))],
        events={f"KXNCAAFGAME-{game}": make_event(f"KXNCAAFGAME-{game}")},
        markets_by_event={f"KXNCAAFGAME-{game}": []},
        series=CFB_SERIES,
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    completeness = run.games[game].completeness
    assert completeness.markets_discovered == 0
    assert completeness.native_game_markets_complete is True
    assert completeness.failed_event_tickers == []


def test_failed_event_fetch_is_incomplete_never_zero_markets():
    """THE central guarantee. A failed fetch must mark the game INCOMPLETE,
    not publish a confident empty menu."""
    game = "26SEP19UGAARK"
    good = f"KXNCAAFGAME-{game}"
    bad = f"KXNCAAFSPREAD-{game}"
    api = FakeKalshi(
        milestones=[make_milestone(game, (good, bad))],
        events={good: make_event(good), bad: make_event(bad)},
        markets_by_event={good: [make_market(f"{good}-UGA", floor_strike=None)], bad: []},
        series=CFB_SERIES,
        fail_paths={bad: ConnectionResetError(104, "Connection reset by peer")},
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    completeness = run.games[game].completeness
    assert bad in completeness.failed_event_tickers
    assert completeness.api_failures >= 1
    assert completeness.native_game_markets_complete is False
    assert run.complete is False
    # The event that DID work is still captured -- a failure elsewhere
    # never discards good data.
    assert completeness.markets_discovered == 1


def test_partial_pagination_failure_marks_the_game_incomplete():
    game = "26SEP19UGAARK"
    event = f"KXNCAAFSPREAD-{game}"

    class HalfBrokenApi(FakeKalshi):
        def __call__(self, path, params=None):
            params = dict(params or {})
            # Fail only on the SECOND page of this event's markets.
            if path == "/markets" and params.get("cursor"):
                raise RuntimeError("HTTP 429 Too Many Requests")
            return super().__call__(path, params)

    api = HalfBrokenApi(
        milestones=[make_milestone(game, (event,))],
        events={event: make_event(event)},
        markets_by_event={
            event: [make_market(f"{event}-UGA{i}", floor_strike=i + 0.5) for i in range(10)]
        },
        series=CFB_SERIES,
        page_size=4,
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    completeness = run.games[game].completeness
    assert event in completeness.pagination_failed_event_tickers
    assert completeness.native_game_markets_complete is False
    # Page one's markets are retained, not thrown away.
    assert completeness.markets_discovered == 4


def test_cursor_pagination_collects_every_market_across_pages():
    game = "26SEP19UGAARK"
    event = f"KXNCAAFSPREAD-{game}"
    api = FakeKalshi(
        milestones=[make_milestone(game, (event,))],
        events={event: make_event(event)},
        markets_by_event={
            event: [make_market(f"{event}-UGA{i}", floor_strike=i + 0.5) for i in range(25)]
        },
        series=CFB_SERIES,
        page_size=4,
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    assert run.games[game].completeness.markets_discovered == 25
    assert run.games[game].completeness.native_game_markets_complete is True


def test_duplicated_markets_are_deduplicated_by_ticker():
    game = "26SEP19UGAARK"
    event = f"KXNCAAFGAME-{game}"
    duplicate = make_market(f"{event}-UGA", floor_strike=None)
    api = FakeKalshi(
        milestones=[make_milestone(game, (event,))],
        events={event: make_event(event)},
        markets_by_event={event: [duplicate, dict(duplicate), dict(duplicate)]},
        series=CFB_SERIES,
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    assert run.games[game].completeness.markets_discovered == 1


def test_market_status_changes_are_reflected_and_counted_separately():
    """Kalshi's RESPONSE vocabulary ('active', 'finalized') differs from
    its QUERY vocabulary ('open', 'settled'). A sweep that compared
    against 'open' once counted every tradeable market as closed."""
    game = "26SEP19UGAARK"
    event = f"KXNCAAFGAME-{game}"
    api = FakeKalshi(
        milestones=[make_milestone(game, (event,))],
        events={event: make_event(event)},
        markets_by_event={
            event: [
                make_market(f"{event}-A", status="active", floor_strike=None),
                make_market(f"{event}-B", status="finalized", floor_strike=None),
                make_market(f"{event}-C", status="closed", floor_strike=None),
                make_market(f"{event}-D", status="unopened", floor_strike=None),
                make_market(f"{event}-E", status="paused", floor_strike=None),
                make_market(f"{event}-F", status="something_new", floor_strike=None),
            ]
        },
        series=CFB_SERIES,
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    c = run.games[game].completeness
    assert c.markets_open == 1
    assert c.markets_closed == 2  # finalized + closed
    assert c.markets_unopened == 1
    assert c.markets_paused == 1
    assert c.markets_other_status == 1  # an unrecognized status is COUNTED, not dropped
    assert c.markets_discovered == 6


def test_newly_created_event_and_market_appear_on_a_later_capture():
    game = "26SEP19UGAARK"
    first = f"KXNCAAFGAME-{game}"
    later = f"KXNCAAFTOTAL-{game}"

    api = FakeKalshi(
        milestones=[make_milestone(game, (first,))],
        events={first: make_event(first)},
        markets_by_event={first: [make_market(f"{first}-UGA", floor_strike=None)]},
        series=CFB_SERIES,
    )
    first_run = MarketDiscovery(api).run(as_of=NOW)
    assert first_run.games[game].completeness.markets_discovered == 1
    assert len(first_run.games[game].events) == 1

    # Kalshi lists a new event AND a new market on the original event.
    api.milestones = [make_milestone(game, (first, later))]
    api.events[later] = make_event(later)
    api.markets_by_event[later] = [make_market(f"{later}-48", floor_strike=48.5)]
    api.markets_by_event[first].append(make_market(f"{first}-ARK", floor_strike=None))

    second_run = MarketDiscovery(api).run(as_of=NOW)
    assert len(second_run.games[game].events) == 2
    assert second_run.games[game].completeness.markets_discovered == 3
    assert second_run.games[game].completeness.native_game_markets_complete is True


def test_series_sweep_surfaces_an_event_the_milestone_never_named():
    """The second discovery path's whole purpose: make a milestone's
    omission visible instead of silently missing those markets."""
    game = "26SEP19UGAARK"
    named = f"KXNCAAFGAME-{game}"
    orphan = f"KXNCAAFTEAMSACK-{game}"
    api = FakeKalshi(
        milestones=[make_milestone(game, (named,))],
        events={named: make_event(named), orphan: make_event(orphan, competition_scope="Team Sacks")},
        markets_by_event={
            named: [make_market(f"{named}-UGA", floor_strike=None)],
            orphan: [make_market(f"{orphan}-UGA2", floor_strike=2.5)],
        },
        series=CFB_SERIES,
        events_by_series={"KXNCAAFTEAMSACK": [make_event(orphan, competition_scope="Team Sacks")]},
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    assert orphan in run.events_only_in_series_sweep
    assert orphan in run.games[game].events
    assert run.games[game].completeness.markets_discovered == 2
    assert orphan in run.games[game].completeness.events_added_by_series_sweep


def test_non_cfb_events_from_the_series_sweep_are_ignored():
    game = "26SEP19UGAARK"
    named = f"KXNCAAFGAME-{game}"
    nfl_event = "KXNFLGAME-26SEP17DETBUF"
    api = FakeKalshi(
        milestones=[make_milestone(game, (named,))],
        events={named: make_event(named)},
        markets_by_event={named: [make_market(f"{named}-UGA", floor_strike=None)]},
        series=CFB_SERIES,
        events_by_series={"KXNFLGAME": [make_event(nfl_event, competition="NFL")]},
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    assert list(run.games) == [game]
    assert nfl_event not in run.events_only_in_series_sweep


def test_season_level_events_are_kept_out_of_game_menus():
    """A conference-champion event is real inventory but has no physical
    game. It belongs in the season bucket, not in a game's market list."""
    game = "26SEP19UGAARK"
    named = f"KXNCAAFGAME-{game}"
    futures = "KXNCAAFSEC-26"
    api = FakeKalshi(
        milestones=[make_milestone(game, (named, futures))],
        events={named: make_event(named), futures: make_event(futures, competition_scope="Champion")},
        markets_by_event={named: [make_market(f"{named}-UGA", floor_strike=None)]},
        series=CFB_SERIES,
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    assert futures in run.season_level_events
    assert futures not in run.games[game].events
    assert run.games[game].completeness.markets_discovered == 1


def test_multiple_physical_games_are_grouped_independently():
    games = ["26SEP19UGAARK", "26SEP19FLAAUB", "26SEP20MSUND"]
    milestones = []
    events = {}
    markets = {}
    for g in games:
        for series in ("KXNCAAFGAME", "KXNCAAFSPREAD"):
            ticker = f"{series}-{g}"
            events[ticker] = make_event(ticker)
            markets[ticker] = [make_market(f"{ticker}-A", floor_strike=None)]
        milestones.append(make_milestone(g, (f"KXNCAAFGAME-{g}", f"KXNCAAFSPREAD-{g}"), milestone_id=f"ms-{g}"))
    api = FakeKalshi(
        milestones=milestones, events=events, markets_by_event=markets, series=CFB_SERIES
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    assert set(run.games) == set(games)
    for g in games:
        assert run.games[g].completeness.markets_discovered == 2


def test_finished_and_far_future_games_are_excluded_by_status_and_horizon():
    api = FakeKalshi(
        milestones=[
            make_milestone("26SEP19AAABBB", ("KXNCAAFGAME-26SEP19AAABBB",), status="complete"),
            make_milestone("26SEP19CCCDDD", ("KXNCAAFGAME-26SEP19CCCDDD",), status="closed"),
            make_milestone(
                "26DEC31EEEFFF", ("KXNCAAFGAME-26DEC31EEEFFF",), status="scheduled",
                start_date="2026-12-31T19:00:00Z",
            ),
            make_milestone("26SEP19GGGHHH", ("KXNCAAFGAME-26SEP19GGGHHH",), status="scheduled"),
        ],
        events={
            f"KXNCAAFGAME-{g}": make_event(f"KXNCAAFGAME-{g}")
            for g in ("26SEP19AAABBB", "26SEP19CCCDDD", "26DEC31EEEFFF", "26SEP19GGGHHH")
        },
        markets_by_event={
            f"KXNCAAFGAME-{g}": [make_market(f"KXNCAAFGAME-{g}-A", floor_strike=None)]
            for g in ("26SEP19AAABBB", "26SEP19CCCDDD", "26DEC31EEEFFF", "26SEP19GGGHHH")
        },
        series=CFB_SERIES,
    )
    run = MarketDiscovery(api).run(as_of=NOW, horizon_days=10.0)
    assert set(run.games) == {"26SEP19GGGHHH"}


def test_non_cfb_milestones_are_ignored():
    api = FakeKalshi(
        milestones=[
            make_milestone("26SEP17DETBUF", ("KXNFLGAME-26SEP17DETBUF",), league="NFL"),
            make_milestone("26SEP19UGAARK", ("KXNCAAFGAME-26SEP19UGAARK",), league="NCAAFB"),
        ],
        events={"KXNCAAFGAME-26SEP19UGAARK": make_event("KXNCAAFGAME-26SEP19UGAARK")},
        markets_by_event={
            "KXNCAAFGAME-26SEP19UGAARK": [make_market("KXNCAAFGAME-26SEP19UGAARK-A", floor_strike=None)]
        },
        series=CFB_SERIES,
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    assert list(run.games) == ["26SEP19UGAARK"]


def test_milestone_sweep_failure_is_reported_not_swallowed():
    api = FakeKalshi(series=CFB_SERIES, fail_paths={"/milestones": RuntimeError("HTTP 503")})
    run = MarketDiscovery(api).run(as_of=NOW)
    assert run.milestone_sweep_complete is False
    assert run.complete is False
    assert any("milestone sweep incomplete" in e for e in run.errors)


def test_alternate_lines_are_flagged_on_ladders_but_not_on_moneylines():
    api = _rich_game_fake()
    run = MarketDiscovery(api).run(as_of=NOW)
    contracts = {c.market_ticker: c for c in run.games["26SEP19UGAARK"].contracts}
    spread = contracts["KXNCAAFSPREAD-26SEP19UGAARK-UGA1"]
    moneyline = contracts["KXNCAAFGAME-26SEP19UGAARK-UGA"]
    single_rung = contracts["KXNCAAFTEAMSACK-26SEP19UGAARK-UGA2"]
    assert spread.classification.is_alternate_line is True
    assert moneyline.classification.is_alternate_line is False
    assert single_rung.classification.is_alternate_line is False


def test_output_is_deterministic_across_identical_captures():
    catalog_a = build_catalog(MarketDiscovery(_rich_game_fake()).run(as_of=NOW))
    catalog_b = build_catalog(MarketDiscovery(_rich_game_fake()).run(as_of=NOW))
    assert catalog_a == catalog_b
    assert catalog_content_fingerprint(catalog_a) == catalog_content_fingerprint(catalog_b)


def test_fingerprint_ignores_capture_time_but_notices_a_price_change():
    """Change detection must skip a no-op run (or the scheduled job
    rewrites the file every time) while still recording a real price
    move."""
    run_one = MarketDiscovery(_rich_game_fake()).run(as_of=NOW)
    later = datetime(2026, 9, 17, 23, 30, tzinfo=UTC)
    run_two = MarketDiscovery(_rich_game_fake()).run(as_of=later)
    assert catalog_content_fingerprint(build_catalog(run_one, include_markets=True)) == catalog_content_fingerprint(
        build_catalog(run_two, include_markets=True)
    )

    moved = _rich_game_fake()
    moved.markets_by_event["KXNCAAFGAME-26SEP19UGAARK"][0]["yes_ask_dollars"] = "0.5500"
    run_three = MarketDiscovery(moved).run(as_of=NOW)
    assert catalog_content_fingerprint(build_catalog(run_three, include_markets=True)) != catalog_content_fingerprint(
        build_catalog(run_one, include_markets=True)
    )


def test_flat_index_covers_every_catalog_market():
    run = MarketDiscovery(_rich_game_fake()).run(as_of=NOW)
    catalog = build_catalog(run, include_markets=True)
    flat = build_flat_index(run)
    catalog_tickers = {m["market_ticker"] for g in catalog["games"] for m in g["markets"]}
    flat_tickers = {m["market_ticker"] for m in flat["markets"]}
    assert catalog_tickers == flat_tickers
    assert flat["market_count"] == catalog["totals"]["markets"]


def test_raw_payload_is_preserved_when_requested():
    """Nothing captured may become unreachable."""
    run = MarketDiscovery(_rich_game_fake()).run(as_of=NOW)
    flat = build_flat_index(run, include_raw=True)
    row = next(m for m in flat["markets"] if m["market_ticker"] == "KXNCAAFGAME-26SEP19UGAARK-UGA")
    assert row["raw"]["yes_ask_dollars"] == "0.1200"
    assert row["raw"]["price_level_structure"] == "linear_cent"


# =========================================================================
# BULK PREFETCH
#
# Per-event market fetching is the obviously-correct primitive but costs
# ~14,000 requests on a live slate -- a real audit run was still going
# after 17 minutes against a 30-minute cadence. Markets are therefore
# bulk-loaded one series at a time and bucketed by event.
#
# The risk this introduces is a NEGATIVE answer from an index: "the bucket
# for this event is empty" must never be published as "this event has no
# markets" when the truth is "the bulk sweep failed, or never ran for that
# series". These tests pin that.
# =========================================================================


def test_prefetch_serves_events_without_per_event_requests():
    api = _rich_game_fake()
    run = MarketDiscovery(api).run(as_of=NOW)
    assert run.games["26SEP19UGAARK"].completeness.markets_discovered == 13
    assert run.events_served_from_prefetch == 5
    assert run.events_fetched_individually == 0
    # The expensive per-event query must not appear at all.
    per_event = [p for path, p in api.calls if path == "/markets" and p.get("event_ticker")]
    assert per_event == [], f"per-event market fetches were still issued: {per_event}"


def test_prefetch_bounds_its_sweep_with_min_close_ts():
    """Without a close-time bound the sweep re-reads a whole season of
    settled markets every run (KXNCAAFSPREAD alone is 9,360 markets)."""
    api = _rich_game_fake()
    MarketDiscovery(api).run(as_of=NOW)
    series_sweeps = [p for path, p in api.calls if path == "/markets" and p.get("series_ticker")]
    assert series_sweeps, "no bulk series sweep was issued"
    assert all("min_close_ts" in p for p in series_sweeps)
    assert all(int(p["min_close_ts"]) < NOW.timestamp() for p in series_sweeps)


def test_an_empty_bucket_is_confirmed_by_a_direct_fetch_not_assumed():
    """THE risk of an index: absence must be confirmed. Here the bulk
    sweep returns nothing at all, so every event must fall back to a
    direct per-event fetch and the menu must come out complete."""
    api = _rich_game_fake(serve_series_markets=False)
    run = MarketDiscovery(api).run(as_of=NOW)
    game = run.games["26SEP19UGAARK"]
    assert game.completeness.markets_discovered == 13, "markets were lost to an empty index"
    assert game.completeness.native_game_markets_complete is True
    assert run.events_served_from_prefetch == 0
    assert run.events_fetched_individually == 5


def test_a_failed_series_sweep_falls_back_rather_than_publishing_a_partial_bucket():
    """A series sweep that dies mid-chain leaves a NON-EMPTY but truncated
    bucket. Trusting it would publish a partial menu as complete, so a
    series that did not sweep cleanly is never trusted."""
    game = "26SEP19UGAARK"
    event = f"KXNCAAFSPREAD-{game}"

    class TruncatedSeriesSweep(FakeKalshi):
        def __call__(self, path, params=None):
            params = dict(params or {})
            # Bulk series sweep dies on its second page; per-event is fine.
            if path == "/markets" and params.get("series_ticker") and params.get("cursor"):
                raise RuntimeError("HTTP 429 Too Many Requests")
            return super().__call__(path, params)

    api = TruncatedSeriesSweep(
        milestones=[make_milestone(game, (event,))],
        events={event: make_event(event)},
        markets_by_event={
            event: [make_market(f"{event}-UGA{i}", floor_strike=i + 0.5) for i in range(10)]
        },
        series=CFB_SERIES,
        events_by_series={"KXNCAAFSPREAD": [make_event(event)]},
        page_size=4,
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    completeness = run.games[game].completeness
    # All 10 recovered via the per-event fallback, not the truncated 4.
    assert completeness.markets_discovered == 10
    assert completeness.native_game_markets_complete is True
    assert run.events_fetched_individually == 1


def test_a_series_never_swept_still_gets_its_markets():
    """A milestone can name an event whose series our CFB heuristic did
    not select. The milestone path must not depend on that heuristic."""
    game = "26SEP19UGAARK"
    unlisted = f"KXWEIRDSERIES-{game}"
    api = FakeKalshi(
        milestones=[make_milestone(game, (unlisted,))],
        events={unlisted: make_event(unlisted)},
        markets_by_event={unlisted: [make_market(f"{unlisted}-A", floor_strike=None)]},
        series=CFB_SERIES,  # KXWEIRDSERIES is NOT among them
    )
    run = MarketDiscovery(api).run(as_of=NOW)
    assert run.games[game].completeness.markets_discovered == 1
    assert run.events_fetched_individually == 1


def test_prefetch_and_per_event_paths_produce_identical_catalogs():
    """The optimization must not change the answer -- only the cost."""
    fast = build_catalog(MarketDiscovery(_rich_game_fake()).run(as_of=NOW))
    slow = build_catalog(MarketDiscovery(_rich_game_fake(serve_series_markets=False)).run(as_of=NOW))
    for catalog in (fast, slow):
        catalog["discovery"].pop("requests_made", None)
    assert fast["games"] == slow["games"]
    assert fast["totals"] == slow["totals"]


def test_the_bulk_path_is_dramatically_cheaper():
    """The reason this exists at all."""
    cheap = MarketDiscovery(_rich_game_fake()).run(as_of=NOW)
    expensive = MarketDiscovery(_rich_game_fake(serve_series_markets=False)).run(as_of=NOW)
    assert cheap.stats.requests_made < expensive.stats.requests_made
