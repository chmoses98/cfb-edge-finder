"""Sharding: a game is never split, and the parts sum to the whole."""

from __future__ import annotations

import json

from cfb_edge_finder.execution.shards import build_shards, split_window, write_shards
from cfb_edge_finder.execution.slate import build_slate
from tests.execution_fakes import GAME_KEY, catalog_dir, standard_markets
from tests.test_execution_disposition import config


def slate_with_games(tmp_path, kickoffs: dict[str, str]):
    extra = [
        (key, "LSU at Ole Miss", kickoff, standard_markets(key))
        for key, kickoff in kickoffs.items()
        if key != GAME_KEY
    ]
    directory = catalog_dir(
        tmp_path,
        kickoff=kickoffs.get(GAME_KEY, "2026-09-19T23:30:00Z"),
        extra_games=extra,
    )
    return build_slate(directory, config(), slate_date=None).slate


def test_shards_cover_every_window_a_kickoff_falls_in(tmp_path):
    slate = slate_with_games(
        tmp_path,
        {
            GAME_KEY: "2026-09-19T16:00:00Z",   # 12:00 ET -> early
            "26SEP19AAABBB": "2026-09-19T20:00:00Z",  # 16:00 ET -> afternoon
            "26SEP19CCCDDD": "2026-09-19T23:30:00Z",  # 19:30 ET -> evening
            "26SEP20EEEFFF": "2026-09-20T03:00:00Z",  # 23:00 ET -> late
        },
    )
    shards = build_shards(slate)
    assert [s.window for s in shards] == ["early", "afternoon", "evening", "late"]


def test_no_game_is_ever_split_across_shards(tmp_path):
    slate = slate_with_games(
        tmp_path,
        {
            GAME_KEY: "2026-09-19T16:00:00Z",
            "26SEP19AAABBB": "2026-09-19T16:30:00Z",
            "26SEP19CCCDDD": "2026-09-19T17:00:00Z",
        },
    )
    # A budget so small that every game overflows it: the packer must add
    # subshards, never move part of a game.
    shards = build_shards(slate, max_bytes=1, max_contracts=1)
    assert len(shards) == 3
    seen = set()
    for shard in shards:
        for packet in shard.packets:
            assert packet["game_key"] not in seen
            seen.add(packet["game_key"])
            tickers = {c["ticker"] for c in packet["contracts"]}
            assert len(tickers) == packet["counts"]["eligible"]
    assert len(seen) == 3


def test_a_single_game_larger_than_the_budget_gets_its_own_shard(tmp_path):
    slate = slate_with_games(tmp_path, {GAME_KEY: "2026-09-19T16:00:00Z"})
    shards = split_window("early", slate["games"], max_bytes=1, max_contracts=1)
    assert len(shards) == 1
    assert shards[0].packets[0]["counts"]["eligible"] == len(standard_markets())


def test_subshards_are_numbered_and_kickoff_ordered(tmp_path):
    slate = slate_with_games(
        tmp_path,
        {
            GAME_KEY: "2026-09-19T17:00:00Z",
            "26SEP19AAABBB": "2026-09-19T16:00:00Z",
            "26SEP19CCCDDD": "2026-09-19T16:30:00Z",
        },
    )
    shards = split_window("early", slate["games"], max_bytes=1, max_contracts=1)
    assert [s.name for s in shards] == ["early_1", "early_2", "early_3"]
    kickoffs = [s.packets[0]["kickoff"] for s in shards]
    assert kickoffs == sorted(kickoffs)


def test_shard_totals_reconcile_to_the_global_universe(tmp_path):
    slate = slate_with_games(
        tmp_path,
        {
            GAME_KEY: "2026-09-19T16:00:00Z",
            "26SEP19AAABBB": "2026-09-19T20:00:00Z",
            "26SEP19CCCDDD": "2026-09-19T23:30:00Z",
        },
    )
    shards = build_shards(slate, max_bytes=1, max_contracts=1)
    manifest = write_shards(slate, shards, tmp_path / "out")

    assert manifest["reconciles"] is True
    assert manifest["games_in_multiple_shards"] == []
    assert manifest["totals"]["games"] == slate["reconciliation"]["games_published"]
    assert (
        manifest["totals"]["contracts_eligible"]
        == slate["reconciliation"]["contracts_eligible"]
    )
    assert (
        manifest["totals"]["contracts_discovered"]
        == manifest["slate_totals"]["contracts_discovered_published_games"]
    )


def test_the_manifest_carries_what_an_operator_needs_per_shard(tmp_path):
    slate = slate_with_games(tmp_path, {GAME_KEY: "2026-09-19T16:00:00Z"})
    manifest = write_shards(slate, build_shards(slate), tmp_path / "out")
    entry = manifest["shards"][0]
    for key in (
        "games",
        "game_count",
        "contracts_eligible",
        "contracts_discovered",
        "contracts_excluded",
        "bytes",
        "earliest_kickoff",
        "latest_kickoff",
        "artifact_hash",
    ):
        assert key in entry, key
    assert entry["bytes"] > 0
    assert entry["artifact_hash"]


def test_the_brief_is_a_projection_of_the_shard_not_a_selection(tmp_path):
    """The handicap-facing file must contain exactly the same contracts as
    the evaluator-facing one. A brief that quietly held fewer would put a
    pre-filter back in the workflow by the back door."""
    slate = slate_with_games(tmp_path, {GAME_KEY: "2026-09-19T16:00:00Z"})
    out = tmp_path / "out"
    write_shards(slate, build_shards(slate), out)

    full = json.loads((out / "shards" / "early.json").read_text())
    brief = json.loads((out / "shards" / "early.brief.json").read_text())

    full_tickers = {
        c["ticker"] for game in full["games"] for c in game["contracts"]
    }
    brief_tickers = set()
    for game in brief["games"]:
        for block in game["families"].values():
            prefix = block.get("ticker_prefix", "")
            for row in block["rows"]:
                brief_tickers.add(prefix + row[0] if prefix else row[0])

    assert brief_tickers == full_tickers
    assert sum(g["eligible_contracts"] for g in brief["games"]) == len(full_tickers)


def test_the_brief_carries_no_prices(tmp_path):
    """Anchoring the handicap to the market it is meant to be independent
    of is the failure this guards."""
    slate = slate_with_games(tmp_path, {GAME_KEY: "2026-09-19T16:00:00Z"})
    out = tmp_path / "out"
    write_shards(slate, build_shards(slate), out)
    brief = (out / "shards" / "early.brief.json").read_text()
    for forbidden in ("yes_ask", "no_ask", "yes_entry", "breakeven", "implied_probability"):
        assert forbidden not in brief


def test_stale_shard_files_are_removed_when_the_slate_shrinks(tmp_path):
    slate = slate_with_games(
        tmp_path,
        {GAME_KEY: "2026-09-19T16:00:00Z", "26SEP19AAABBB": "2026-09-19T20:00:00Z"},
    )
    out = tmp_path / "out"
    write_shards(slate, build_shards(slate), out)
    assert (out / "shards" / "afternoon.json").exists()

    smaller = dict(slate)
    smaller["games"] = [g for g in slate["games"] if g["kickoff_window"] == "early"]
    write_shards(smaller, build_shards(smaller), out)
    assert not (out / "shards" / "afternoon.json").exists()
    assert (out / "shards" / "early.json").exists()
