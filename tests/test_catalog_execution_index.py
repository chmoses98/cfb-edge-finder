"""The RUN CFB execution index (``catalog/execution.py``).

GitHub's Contents API returns empty content for files over 1 MB, which made
the published catalog and the marquee game files unreadable to a GitHub
connector. These tests pin the derived small-file layer: that it is built
deterministically from what is on disk, loses no contract, stays under its
size budgets, never carries model output, and that the verifier actually
catches each way the layer could mislead a consumer.

Fixtures are synthetic and minimal; the last test also checks the COMMITTED
production tree when it exists.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

from cfb_edge_finder.catalog import execution
from cfb_edge_finder.catalog.execution import (
    EXECUTION_DIRNAME,
    ExecutionBuildError,
    build_execution_files,
    kickoff_slot,
    quote_row,
    split_teams,
    verify_execution_artifacts,
    write_execution_artifacts,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFIX = "data/live"
CATALOG_FP = "c" * 64


# --------------------------------------------------------------------------
# synthetic fixtures
# --------------------------------------------------------------------------


def _encode(payload: Any) -> str:
    """Encoded exactly like production game files."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def make_contract(
    game_key: str,
    event_ticker: str,
    index: int,
    *,
    family: str = "game_total",
    yes_ask: float | None = 0.55,
    yes_bid: float | None = 0.50,
    no_ask: float | None = 0.50,
    no_bid: float | None = 0.45,
    yes_ask_size: float | None = 10.0,
    yes_bid_size: float | None = 7.0,
    padding: int = 0,
    **extra: Any,
) -> dict[str, Any]:
    market = {
        "market_ticker": f"{event_ticker}-{index:03d}",
        "event_ticker": event_ticker,
        "family": family,
        "is_alternate_line": index % 2 == 1,
        "title": f"{game_key} contract {index}",
        "status": "active",
        "strike_type": "greater",
        "floor_strike": index + 0.5,
        "cap_strike": None,
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
        "yes_bid_size": yes_bid_size,
        "yes_ask_size": yes_ask_size,
        "no_bid_size": None,
        "no_ask_size": None,
        "updated_time": "2026-10-17T18:00:00Z",
        "mechanics": {"book_state": "two_sided", "yes_mid": 0.525},
        "fee": {
            "support": "supported",
            "unavailable_reason": None,
            "model_trade_fee_at_yes_ask": 0.017325,
            "model_trade_fee_at_no_ask": 0.0175,
        },
        "rules_primary": "If the total is over the line then YES. " + ("x" * padding),
    }
    market.update(extra)
    return market


def make_game_spec(
    game_key: str,
    title: str | None,
    kickoff: str | None,
    *,
    events: dict[str, int] | None = None,
    complete: bool = True,
    padding: int = 0,
) -> dict[str, Any]:
    events = events or {f"KXNCAAFTOTAL-{game_key}": 3, f"KXNCAAFSPREAD-{game_key}": 2}
    markets: list[dict[str, Any]] = []
    for event_ticker, count in events.items():
        for i in range(count):
            markets.append(make_contract(game_key, event_ticker, i, padding=padding))
    completeness: dict[str, Any] = {
        "native_game_markets_complete": complete,
        "api_failures": 0 if complete else 2,
        "events_fetched": len(events),
        "related_event_tickers_reported": len(events) + (0 if complete else 1),
        "failed_event_tickers": [] if complete else [f"KXNCAAFGAME-{game_key}"],
        "pagination_failed_event_tickers": [],
    }
    return {
        "game_key": game_key,
        "title": title,
        "kickoff": kickoff,
        "identity": {
            "milestone_status": "scheduled",
            "division": "FBS",
            "conference": "SEC",
            "tier": "A",
        },
        "completeness": completeness,
        "events": [
            {"event_ticker": t, "series_ticker": t.split("-")[0], "title": f"{title} {t}"} for t in events
        ],
        "family_distribution": {"game_total": len(markets)},
        "markets": markets,
    }


def default_specs() -> list[dict[str, Any]]:
    return [
        # 12:00 EDT, early, slate 2026-10-17
        make_game_spec("26OCT17AUBUGA", "Auburn at Georgia", "2026-10-17T16:00:00Z"),
        # 22:30 EDT on 10-17 == 02:30 UTC on 10-18: must stay on the ET date
        make_game_spec("26OCT17WASHUSC", "Washington at USC", "2026-10-18T02:30:00Z"),
        # 20:00 EDT, 2026-10-18, incomplete
        make_game_spec("26OCT18DUKEUVA", "Duke vs Virginia", "2026-10-19T00:00:00Z", complete=False),
        # no kickoff: unscheduled
        make_game_spec("26OCT19TBDGAME", "Somebody at Somewhere", None),
    ]


def write_tree(out_dir: Path, specs: list[dict[str, Any]] | None = None, *, fingerprint: str = CATALOG_FP) -> dict:
    """Write catalog + game files + status file, the way production does."""
    specs = default_specs() if specs is None else specs
    (out_dir / "games").mkdir(parents=True, exist_ok=True)
    entries = []
    for spec in specs:
        game_payload = {
            "completeness": spec["completeness"],
            "events": spec["events"],
            "family_distribution": spec["family_distribution"],
            "game_key": spec["game_key"],
            "identity": spec["identity"],
            "kickoff": spec["kickoff"],
            "market_count": len(spec["markets"]),
            "markets": spec["markets"],
            "title": spec["title"],
        }
        (out_dir / "games" / f"{spec['game_key']}.json").write_text(_encode(game_payload), encoding="utf-8")
        entries.append(
            {
                "game_key": spec["game_key"],
                "title": spec["title"],
                "kickoff": spec["kickoff"],
                "market_count": len(spec["markets"]),
                "markets_file": f"games/{spec['game_key']}.json",
                "identity": spec["identity"],
                "events": spec["events"],
                "completeness": spec["completeness"],
                "family_distribution": spec["family_distribution"],
            }
        )
    incomplete = [s["game_key"] for s in specs if not s["completeness"]["native_game_markets_complete"]]
    catalog = {
        "schema_version": "cfb_market_catalog/test",
        "capture": {"captured_at": "2026-10-17T19:00:00Z", "content_fingerprint": fingerprint},
        "completeness": {"capture_complete": not incomplete, "games_incomplete": incomplete},
        "totals": {"physical_games": len(specs), "markets": sum(len(s["markets"]) for s in specs)},
        "games": entries,
    }
    (out_dir / "cfb_market_catalog.json").write_text(_encode(catalog), encoding="utf-8")
    (out_dir / "cfb_catalog_status.json").write_text(
        _encode({"content_fingerprint": fingerprint, "physical_games": len(specs)}), encoding="utf-8"
    )
    return catalog


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    write_tree(tmp_path)
    return tmp_path


@pytest.fixture
def built(tree: Path) -> Path:
    write_execution_artifacts(tree, PREFIX)
    assert verify_execution_artifacts(tree, PREFIX) == []
    return tree


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_index(out_dir: Path) -> dict[str, Any]:
    return read_json(out_dir / execution.INDEX_FILENAME)


def repo_to_local(out_dir: Path, repo_path: str) -> Path:
    assert repo_path.startswith(PREFIX + "/"), repo_path
    return out_dir / repo_path[len(PREFIX) + 1 :]


def game_row(out_dir: Path, key: str) -> dict[str, Any]:
    return next(g for g in load_index(out_dir)["games"] if g["game_key"] == key)


def exec_files(out_dir: Path) -> dict[str, bytes]:
    files = {
        p.relative_to(out_dir).as_posix(): p.read_bytes()
        for p in (out_dir / EXECUTION_DIRNAME).rglob("*")
        if p.is_file()
    }
    files[execution.INDEX_FILENAME] = (out_dir / execution.INDEX_FILENAME).read_bytes()
    return files


# --------------------------------------------------------------------------
# 1. kickoff_slot
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kickoff", "window", "slate", "kickoff_et"),
    [
        # EDT (UTC-4) in October
        ("2026-10-17T15:59:00Z", "early", "2026-10-17", "2026-10-17 11:59 EDT"),
        ("2026-10-17T17:59:00Z", "early", "2026-10-17", "2026-10-17 13:59 EDT"),
        ("2026-10-17T18:00:00Z", "afternoon", "2026-10-17", "2026-10-17 14:00 EDT"),
        ("2026-10-17T21:59:00Z", "afternoon", "2026-10-17", "2026-10-17 17:59 EDT"),
        ("2026-10-17T22:00:00Z", "evening", "2026-10-17", "2026-10-17 18:00 EDT"),
        ("2026-10-17T16:00:00+00:00", "early", "2026-10-17", "2026-10-17 12:00 EDT"),
        # 22:30 ET is already the next day in UTC; the slate stays on the ET date
        ("2026-10-18T02:30:00Z", "evening", "2026-10-17", "2026-10-17 22:30 EDT"),
        # EST (UTC-5) in November, after the Nov 1 2026 fall-back
        ("2026-11-14T18:59:00Z", "early", "2026-11-14", "2026-11-14 13:59 EST"),
        ("2026-11-14T19:00:00Z", "afternoon", "2026-11-14", "2026-11-14 14:00 EST"),
        ("2026-11-14T22:59:00Z", "afternoon", "2026-11-14", "2026-11-14 17:59 EST"),
        ("2026-11-14T23:00:00Z", "evening", "2026-11-14", "2026-11-14 18:00 EST"),
        ("2026-11-15T04:30:00Z", "evening", "2026-11-14", "2026-11-14 23:30 EST"),
        # midnight ET belongs to the new ET date and is "early"
        ("2026-11-15T05:00:00Z", "early", "2026-11-15", "2026-11-15 00:00 EST"),
    ],
)
def test_kickoff_slot_windows_and_dates(kickoff, window, slate, kickoff_et):
    slot = kickoff_slot(kickoff)
    assert slot == {"kickoff_et": kickoff_et, "slate_date": slate, "window": window}


@pytest.mark.parametrize("kickoff", [None, "", "not a date", "2026-13-45T99:00:00Z", 12345, {}, "2026-10-17T16:00:00"])
def test_kickoff_slot_unscheduled_for_missing_or_garbage(kickoff):
    assert kickoff_slot(kickoff) == {"kickoff_et": None, "slate_date": "unscheduled", "window": "unscheduled"}


# --------------------------------------------------------------------------
# 2. split_teams
# --------------------------------------------------------------------------


def test_split_teams_at_states_home_and_away():
    teams = split_teams("Auburn at Georgia")
    assert teams["away_team"] == "Auburn"
    assert teams["home_team"] == "Georgia"
    assert teams["teams"] == ["Auburn", "Georgia"]


def test_split_teams_vs_does_not_guess_home_field():
    teams = split_teams("Duke vs Virginia")
    assert teams["home_team"] is None
    assert teams["away_team"] is None
    assert teams["teams"] == ["Duke", "Virginia"]
    assert "do not infer" in teams["home_away_basis"]


@pytest.mark.parametrize("title", [None, "", "Just One Team", "A at B at C", "A vs B vs C", 42])
def test_split_teams_unparseable_has_no_teams(title):
    teams = split_teams(title)
    assert teams["teams"] == []
    assert teams["home_team"] is None
    assert teams["away_team"] is None


# --------------------------------------------------------------------------
# 3. quote_row
# --------------------------------------------------------------------------


def test_quote_row_buy_no_uses_no_ask_and_the_yes_bid_book():
    market = make_contract(
        "G", "E-G", 0, yes_ask=0.60, yes_bid=0.52, no_ask=0.48, no_bid=0.40, yes_ask_size=0.0, yes_bid_size=25.0
    )
    row = quote_row(market)
    assert row["no_ask"] == 0.48
    # the NO ask is NOT the complement of the YES ask (that is the NO bid)
    assert row["no_ask"] != pytest.approx(1 - row["yes_ask"])
    # the NO ask is backed by the YES bid book, so its size is yes_bid_size
    assert row["yes_bid_size"] == 25.0
    assert row["buy_no_executable"] is True
    # YES side has zero size: a price with no size is not an offer
    assert row["buy_yes_executable"] is False


def test_quote_row_buy_no_follows_yes_bid_size_not_yes_ask_size():
    base = {"yes_ask": 0.6, "no_ask": 0.4}
    only_ask_size = quote_row(make_contract("G", "E-G", 0, **base, yes_ask_size=9.0, yes_bid_size=0.0))
    assert only_ask_size["buy_yes_executable"] is True
    assert only_ask_size["buy_no_executable"] is False
    only_bid_size = quote_row(make_contract("G", "E-G", 0, **base, yes_ask_size=None, yes_bid_size=3.0))
    assert only_bid_size["buy_yes_executable"] is False
    assert only_bid_size["buy_no_executable"] is True


def test_quote_row_zero_size_empty_book_is_not_executable_even_with_prices():
    empty = make_contract(
        "G", "E-G", 0, yes_ask=1.0, yes_bid=0.0, no_ask=1.0, no_bid=0.0, yes_ask_size=0.0, yes_bid_size=0.0,
        mechanics={"book_state": "empty_book"},
    )
    row = quote_row(empty)
    assert row["yes_ask"] == 1.0 and row["no_ask"] == 1.0
    assert row["buy_yes_executable"] is False
    assert row["buy_no_executable"] is False
    assert row["book_state"] == "empty_book"


@pytest.mark.parametrize("size", [None, 0, 0.0, -1, True, "5"])
def test_quote_row_non_positive_or_non_numeric_size_is_not_executable(size):
    row = quote_row(make_contract("G", "E-G", 0, yes_ask_size=size, yes_bid_size=size))
    assert row["buy_yes_executable"] is False
    assert row["buy_no_executable"] is False


def test_quote_row_needs_a_price_as_well_as_size():
    row = quote_row(make_contract("G", "E-G", 0, yes_ask=None, no_ask=None, yes_ask_size=5, yes_bid_size=5))
    assert row["buy_yes_executable"] is False
    assert row["buy_no_executable"] is False


def test_quote_row_fees_come_from_each_sides_own_ask_fee():
    market = make_contract("G", "E-G", 0)
    market["fee"]["model_trade_fee_at_yes_ask"] = 0.011111
    market["fee"]["model_trade_fee_at_no_ask"] = 0.022222
    market["fee"]["model_trade_fee_at_yes_mid"] = 0.999
    row = quote_row(market)
    assert row["buy_yes_fee"] == 0.011111
    assert row["buy_no_fee"] == 0.022222
    assert 0.999 not in row.values()


def test_quote_row_unknown_fee_is_null_not_zero():
    market = make_contract("G", "E-G", 0)
    market["fee"] = {"support": "supported"}
    row = quote_row(market)
    assert row["buy_yes_fee"] is None
    assert row["buy_no_fee"] is None


def test_quote_row_cap_strike_only_when_non_null():
    assert "cap_strike" not in quote_row(make_contract("G", "E-G", 0, cap_strike=None))
    assert quote_row(make_contract("G", "E-G", 0, cap_strike=7.5))["cap_strike"] == 7.5
    assert quote_row(make_contract("G", "E-G", 0, cap_strike=0))["cap_strike"] == 0


def test_quote_row_fee_support_only_when_not_supported():
    supported = quote_row(make_contract("G", "E-G", 0))
    assert "fee_support" not in supported
    assert "fee_unavailable_reason" not in supported

    market = make_contract("G", "E-G", 0)
    market["fee"] = {"support": "unsupported", "unavailable_reason": "unknown fee schedule"}
    row = quote_row(market)
    assert row["fee_support"] == "unsupported"
    assert row["fee_unavailable_reason"] == "unknown fee schedule"
    assert row["buy_yes_fee"] is None

    no_fee_block = make_contract("G", "E-G", 0)
    del no_fee_block["fee"]
    assert quote_row(no_fee_block)["fee_support"] is None


def test_quote_row_is_a_pure_function_of_the_record():
    market = make_contract("G", "E-G", 0)
    before = copy.deepcopy(market)
    first = quote_row(market)
    assert quote_row(market) == first
    assert market == before


# --------------------------------------------------------------------------
# 4. build + verify
# --------------------------------------------------------------------------


def test_build_and_verify_clean_slate(tree):
    result = write_execution_artifacts(tree, PREFIX)
    assert result.games == 4
    assert result.markets == 20
    assert set(result.slates) == {"2026-10-17", "2026-10-18", "unscheduled"}
    assert result.files_written > 0
    assert result.files_unchanged == 0
    assert verify_execution_artifacts(tree, PREFIX) == []


def test_every_quote_and_detail_shard_ticker_matches_the_game_file(built):
    for row in load_index(built)["games"]:
        key = row["game_key"]
        game = read_json(built / "games" / f"{key}.json")
        game_tickers = sorted(m["market_ticker"] for m in game["markets"])

        quote_tickers = []
        for path in row["quote_files"]:
            quote_tickers += [q["market_ticker"] for q in read_json(repo_to_local(built, path))["quotes"]]
        assert sorted(quote_tickers) == game_tickers

        manifest = read_json(repo_to_local(built, row["manifest"]))
        detail_tickers = []
        for detail in manifest["detail_files"]:
            doc = read_json(repo_to_local(built, detail["path"]))
            detail_tickers += [m["market_ticker"] for m in doc["markets"]]
        assert sorted(detail_tickers) == game_tickers


def test_detail_shards_hold_the_published_market_records_verbatim(built):
    key = "26OCT17AUBUGA"
    game = read_json(built / "games" / f"{key}.json")
    originals = {m["market_ticker"]: m for m in game["markets"]}
    manifest = read_json(repo_to_local(built, game_row(built, key)["manifest"]))
    seen = {}
    for detail in manifest["detail_files"]:
        for market in read_json(repo_to_local(built, detail["path"]))["markets"]:
            seen[market["market_ticker"]] = market
    assert seen == originals


def test_incomplete_game_is_not_usable_and_explains_why(built):
    row = game_row(built, "26OCT18DUKEUVA")
    assert row["usable"] is False
    manifest = read_json(repo_to_local(built, row["manifest"]))
    assert manifest["native_game_markets_complete"] is False
    assert manifest["usable"] is False
    diagnostics = manifest["incomplete_diagnostics"]
    assert diagnostics["failed_event_tickers"] == ["KXNCAAFGAME-26OCT18DUKEUVA"]
    assert diagnostics["api_failures"] == 2

    slate = read_json(built / "execution" / "slates" / "2026-10-18.json")
    slate_row = next(g for g in slate["games"] if g["game_key"] == "26OCT18DUKEUVA")
    assert slate_row["usable"] is False
    assert "incomplete_diagnostics" in slate_row

    complete = game_row(built, "26OCT17AUBUGA")
    assert complete["usable"] is True
    assert "incomplete_diagnostics" not in read_json(repo_to_local(built, complete["manifest"]))
    quotes = read_json(repo_to_local(built, row["quote_files"][0]))
    assert quotes["native_game_markets_complete"] is False


def test_slate_files_list_the_right_games(built):
    slates = {s["slate_date"]: s for s in load_index(built)["slates"]}
    assert set(slates) == {"2026-10-17", "2026-10-18", "unscheduled"}

    def keys(date: str) -> list[str]:
        doc = read_json(repo_to_local(built, slates[date]["path"]))
        assert doc["slate_date"] == date
        assert doc["game_count"] == len(doc["games"])
        return [g["game_key"] for g in doc["games"]]

    # the 22:30 ET kickoff (02:30 UTC on the 18th) is on the 17th's slate
    assert sorted(keys("2026-10-17")) == ["26OCT17AUBUGA", "26OCT17WASHUSC"]
    assert keys("2026-10-18") == ["26OCT18DUKEUVA"]
    assert keys("unscheduled") == ["26OCT19TBDGAME"]
    assert slates["2026-10-17"]["game_count"] == 2
    assert slates["2026-10-17"]["window_counts"] == {"early": 1, "evening": 1}

    doc = read_json(repo_to_local(built, slates["2026-10-17"]["path"]))
    # ordered by kickoff within a slate
    assert [g["game_key"] for g in doc["games"]] == ["26OCT17AUBUGA", "26OCT17WASHUSC"]
    by_key = {g["game_key"]: g for g in doc["games"]}
    assert by_key["26OCT17AUBUGA"]["away_team"] == "Auburn"
    assert by_key["26OCT17AUBUGA"]["home_team"] == "Georgia"
    assert by_key["26OCT17WASHUSC"]["window"] == "evening"


def test_index_paths_are_full_repo_paths_starting_with_prefix(built):
    index = load_index(built)
    paths = [s["path"] for s in index["slates"]]
    for row in index["games"]:
        paths += [row["manifest"], *row["quote_files"]]
        manifest = read_json(repo_to_local(built, row["manifest"]))
        paths += [d["path"] for d in manifest["detail_files"]]
        paths.append(manifest["source_game_file"])
        quotes = read_json(repo_to_local(built, row["quote_files"][0]))
        paths += list(quotes["event_files"].values())
    paths.append(index["freshness"]["status_file"])
    assert paths
    for path in paths:
        assert path.startswith(PREFIX + "/"), path
        assert ".." not in path
        assert (built / path[len(PREFIX) + 1 :]).is_file(), path


def test_prefix_is_applied_to_every_pointer(tree):
    write_execution_artifacts(tree, "some/other/place/")
    row = game_row(tree, "26OCT17AUBUGA")
    assert row["manifest"].startswith("some/other/place/execution/games/26OCT17AUBUGA/")
    assert verify_execution_artifacts(tree, "some/other/place") == []
    # pointers do not resolve under a different prefix
    assert verify_execution_artifacts(tree, PREFIX) != []


def test_game_fingerprint_is_sha256_of_the_game_file_bytes(built):
    for row in load_index(built)["games"]:
        raw = (built / "games" / f"{row['game_key']}.json").read_bytes()
        assert row["game_fingerprint"] == hashlib.sha256(raw).hexdigest()
        manifest = read_json(repo_to_local(built, row["manifest"]))
        assert manifest["game_fingerprint"] == row["game_fingerprint"]
        assert manifest["source_game_file_bytes"] == len(raw)
        for path in row["quote_files"]:
            assert read_json(repo_to_local(built, path))["game_fingerprint"] == row["game_fingerprint"]


def test_index_capture_fingerprint_equals_the_catalogs(built):
    index = load_index(built)
    catalog = read_json(built / "cfb_market_catalog.json")
    assert index["capture"]["content_fingerprint"] == catalog["capture"]["content_fingerprint"] == CATALOG_FP
    assert index["capture"]["captured_at"] == catalog["capture"]["captured_at"]
    assert index["capture"]["physical_games"] == 4
    assert index["capture"]["markets"] == 20
    assert index["capture"]["capture_complete"] is False
    assert index["capture"]["games_incomplete"] == ["26OCT18DUKEUVA"]
    assert index["contains_model_projections"] is False


def test_index_lives_beside_execution_dir_and_nothing_else_is_written(built):
    assert (built / "cfb_execution_index.json").is_file()
    allowed_top = {
        "cfb_execution_index.json",
        "cfb_market_catalog.json",
        "cfb_catalog_status.json",
        "games",
        "execution",
    }
    assert {p.name for p in built.iterdir()} == allowed_top
    # the original game files are untouched (still exactly the production encoding)
    for path in (built / "games").glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert text == _encode(json.loads(text))


def test_build_execution_files_writes_nothing(tree):
    build = build_execution_files(tree, PREFIX)
    assert build.games == 4
    assert build.markets == 20
    assert "../cfb_execution_index.json" in build.files
    assert not (tree / "execution").exists()
    assert not (tree / "cfb_execution_index.json").exists()


# --------------------------------------------------------------------------
# 5. determinism
# --------------------------------------------------------------------------


def test_second_write_changes_nothing_and_is_byte_identical(tree):
    first = write_execution_artifacts(tree, PREFIX)
    before = exec_files(tree)
    mtimes = {p: (tree / p).stat().st_mtime_ns for p in before if p != execution.INDEX_FILENAME}

    second = write_execution_artifacts(tree, PREFIX)
    assert second.files_written == 0
    assert second.files_pruned == 0
    assert second.files_unchanged == first.files_written
    assert exec_files(tree) == before
    assert {p: (tree / p).stat().st_mtime_ns for p in mtimes} == mtimes
    assert not list(tree.rglob("*.tmp"))


def test_a_changed_price_rewrites_only_that_games_files(tree):
    write_execution_artifacts(tree, PREFIX)
    before = exec_files(tree)

    path = tree / "games" / "26OCT17AUBUGA.json"
    game = read_json(path)
    game["markets"][0]["yes_ask"] = 0.61
    path.write_text(_encode(game), encoding="utf-8")

    result = write_execution_artifacts(tree, PREFIX)
    after = exec_files(tree)
    changed = {p for p in after if after[p] != before.get(p)}
    assert result.files_written == len(changed)
    assert changed  # at minimum the game's quotes/manifest/shard, its slate, and the index
    for rel in changed:
        assert (
            "26OCT17AUBUGA" in rel
            or rel == execution.INDEX_FILENAME
            or rel == "execution/slates/2026-10-17.json"
        ), rel
    assert verify_execution_artifacts(tree, PREFIX) == []


def test_games_are_ordered_deterministically_regardless_of_catalog_order(tmp_path):
    specs = default_specs()
    a, b = tmp_path / "a", tmp_path / "b"
    write_tree(a, specs)
    write_tree(b, list(reversed(copy.deepcopy(specs))))
    write_execution_artifacts(a, PREFIX)
    write_execution_artifacts(b, PREFIX)
    assert exec_files(a) == exec_files(b)


# --------------------------------------------------------------------------
# 6. sharding
# --------------------------------------------------------------------------

SHARD_BUDGET = 8_000


def sharded_specs() -> list[dict[str, Any]]:
    big = make_game_spec(
        "26OCT17BIGGAME",
        "Big at Game",
        "2026-10-17T20:00:00Z",
        events={"KXNCAAFTOTAL-26OCT17BIGGAME": 40, "KXNCAAFSPREAD-26OCT17BIGGAME": 30, "KXNCAAFGAME-26OCT17BIGGAME": 2},
        padding=200,
    )
    small = make_game_spec("26OCT17SMALLGM", "Small at Game", "2026-10-17T17:00:00Z")
    return [big, small]


def test_large_game_splits_into_numbered_parts_without_losing_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "SHARD_TARGET_BYTES", SHARD_BUDGET)
    write_tree(tmp_path, sharded_specs())
    write_execution_artifacts(tmp_path, PREFIX)
    assert verify_execution_artifacts(tmp_path, PREFIX) == []

    row = game_row(tmp_path, "26OCT17BIGGAME")
    assert len(row["quote_files"]) > 1
    count = len(row["quote_files"])
    for i, path in enumerate(row["quote_files"], start=1):
        assert path.endswith(f"/quotes.part{i:02d}of{count:02d}.json")
        doc = read_json(repo_to_local(tmp_path, path))
        assert (doc["part"], doc["parts"]) == (i, count)
        assert doc["game_market_count"] == 72
        assert doc["market_count"] == len(doc["quotes"])

    manifest = read_json(repo_to_local(tmp_path, row["manifest"]))
    multi = [d for d in manifest["detail_files"] if d["parts"] > 1]
    assert multi, "the 40-market event must split into detail parts"
    for detail in multi:
        pattern = rf"/{detail['event_ticker']}\.part{detail['part']:02d}of{detail['parts']:02d}\.json$"
        assert re.search(pattern, detail["path"])


def test_unsplit_game_uses_plain_file_names(built):
    row = game_row(built, "26OCT17AUBUGA")
    assert [p.rsplit("/", 1)[1] for p in row["quote_files"]] == ["quotes.json"]
    manifest = read_json(repo_to_local(built, row["manifest"]))
    assert all(d["parts"] == 1 and d["path"].endswith(f"/{d['event_ticker']}.json") for d in manifest["detail_files"])


def test_sharding_loses_and_duplicates_no_market(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "SHARD_TARGET_BYTES", SHARD_BUDGET)
    specs = sharded_specs()
    write_tree(tmp_path, specs)
    write_execution_artifacts(tmp_path, PREFIX)

    for spec in specs:
        expected = sorted(m["market_ticker"] for m in spec["markets"])
        assert len(expected) == len(set(expected))
        row = game_row(tmp_path, spec["game_key"])

        quotes = [
            q["market_ticker"]
            for path in row["quote_files"]
            for q in read_json(repo_to_local(tmp_path, path))["quotes"]
        ]
        assert sorted(quotes) == expected

        manifest = read_json(repo_to_local(tmp_path, row["manifest"]))
        detail = [
            m["market_ticker"]
            for d in manifest["detail_files"]
            for m in read_json(repo_to_local(tmp_path, d["path"]))["markets"]
        ]
        assert sorted(detail) == expected

        # each detail part carries its own accurate count
        for d in manifest["detail_files"]:
            doc = read_json(repo_to_local(tmp_path, d["path"]))
            assert doc["market_count"] == d["market_count"] == len(doc["markets"])
        # every event's parts add up to its event_market_count
        per_event: dict[str, int] = {}
        for d in manifest["detail_files"]:
            per_event[d["event_ticker"]] = per_event.get(d["event_ticker"], 0) + d["market_count"]
        for d in manifest["detail_files"]:
            doc = read_json(repo_to_local(tmp_path, d["path"]))
            assert doc["event_market_count"] == per_event[d["event_ticker"]]


def test_every_execution_file_is_under_the_budget_except_a_single_oversized_record(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "SHARD_TARGET_BYTES", SHARD_BUDGET)
    write_tree(tmp_path, sharded_specs())
    write_execution_artifacts(tmp_path, PREFIX)

    shard_files = [p for p in (tmp_path / "execution" / "games").rglob("*.json") if p.name != "manifest.json"]
    assert shard_files
    for path in shard_files:
        doc = read_json(path)
        records = doc.get("quotes") or doc.get("markets")
        if len(records) > 1:
            assert path.stat().st_size <= SHARD_BUDGET, (path.name, path.stat().st_size)


def test_a_single_record_larger_than_the_budget_gets_its_own_part_not_dropped(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "SHARD_TARGET_BYTES", SHARD_BUDGET)
    spec = make_game_spec(
        "26OCT17HUGEREC", "Huge at Record", "2026-10-17T20:00:00Z",
        events={"KXNCAAFTOTAL-26OCT17HUGEREC": 3},
    )
    spec["markets"][1]["rules_primary"] = "y" * (SHARD_BUDGET * 2)
    write_tree(tmp_path, [spec])
    write_execution_artifacts(tmp_path, PREFIX)
    assert verify_execution_artifacts(tmp_path, PREFIX) == []
    manifest = read_json(repo_to_local(tmp_path, game_row(tmp_path, "26OCT17HUGEREC")["manifest"]))
    sizes = [(d["market_count"], d["bytes"]) for d in manifest["detail_files"]]
    assert sum(c for c, _ in sizes) == 3
    oversized = [(c, b) for c, b in sizes if b > SHARD_BUDGET]
    assert oversized and all(c == 1 for c, _ in oversized)


def test_sharded_rebuild_is_also_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "SHARD_TARGET_BYTES", SHARD_BUDGET)
    write_tree(tmp_path, sharded_specs())
    write_execution_artifacts(tmp_path, PREFIX)
    before = exec_files(tmp_path)
    again = write_execution_artifacts(tmp_path, PREFIX)
    assert again.files_written == 0 and again.files_pruned == 0
    assert exec_files(tmp_path) == before


def test_shrinking_a_game_prunes_its_stale_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(execution, "SHARD_TARGET_BYTES", SHARD_BUDGET)
    write_tree(tmp_path, sharded_specs())
    write_execution_artifacts(tmp_path, PREFIX)
    assert len(game_row(tmp_path, "26OCT17BIGGAME")["quote_files"]) > 1

    # same game, now tiny and back under the normal budget: the old
    # .partNNofMM files must not linger as orphans
    monkeypatch.undo()
    write_tree(tmp_path, [make_game_spec("26OCT17BIGGAME", "Big at Game", "2026-10-17T20:00:00Z")])
    result = write_execution_artifacts(tmp_path, PREFIX)
    assert result.files_pruned > 0
    assert not list((tmp_path / "execution").rglob("*.part*of*.json"))
    assert verify_execution_artifacts(tmp_path, PREFIX) == []


# --------------------------------------------------------------------------
# 7. pruning
# --------------------------------------------------------------------------


def test_removing_a_game_prunes_its_directory_and_empty_slate(tmp_path):
    specs = default_specs()
    write_tree(tmp_path, specs)
    write_execution_artifacts(tmp_path, PREFIX)
    gone = "26OCT18DUKEUVA"  # the only game on 2026-10-18
    assert (tmp_path / "execution" / "games" / gone).is_dir()
    assert (tmp_path / "execution" / "slates" / "2026-10-18.json").is_file()

    remaining = [s for s in specs if s["game_key"] != gone]
    write_tree(tmp_path, remaining)
    (tmp_path / "games" / f"{gone}.json").unlink()
    result = write_execution_artifacts(tmp_path, PREFIX)

    assert result.files_pruned > 0
    assert not (tmp_path / "execution" / "games" / gone).exists()
    assert not (tmp_path / "execution" / "slates" / "2026-10-18.json").exists()
    assert (tmp_path / "execution" / "slates" / "2026-10-17.json").is_file()
    index = load_index(tmp_path)
    assert gone not in {g["game_key"] for g in index["games"]}
    assert "2026-10-18" not in {s["slate_date"] for s in index["slates"]}
    assert verify_execution_artifacts(tmp_path, PREFIX) == []


def test_removing_one_of_two_games_on_a_date_keeps_the_slate_without_it(tmp_path):
    specs = default_specs()
    write_tree(tmp_path, specs)
    write_execution_artifacts(tmp_path, PREFIX)
    write_tree(tmp_path, [s for s in specs if s["game_key"] != "26OCT17WASHUSC"])
    write_execution_artifacts(tmp_path, PREFIX)
    slate = read_json(tmp_path / "execution" / "slates" / "2026-10-17.json")
    assert [g["game_key"] for g in slate["games"]] == ["26OCT17AUBUGA"]
    assert not (tmp_path / "execution" / "games" / "26OCT17WASHUSC").exists()


def test_pruning_removes_stray_files_under_execution_but_never_touches_games(tmp_path):
    write_tree(tmp_path)
    write_execution_artifacts(tmp_path, PREFIX)
    stray = tmp_path / "execution" / "notes" / "old.txt"
    stray.parent.mkdir(parents=True)
    stray.write_text("leftover", encoding="utf-8")
    games_before = {p.name: p.read_bytes() for p in (tmp_path / "games").iterdir()}

    result = write_execution_artifacts(tmp_path, PREFIX)
    assert result.files_pruned == 1
    assert not stray.exists()
    assert not stray.parent.exists()
    assert {p.name: p.read_bytes() for p in (tmp_path / "games").iterdir()} == games_before


# --------------------------------------------------------------------------
# 8. verification catches
# --------------------------------------------------------------------------


def _first_detail_path(out_dir: Path, key: str = "26OCT17AUBUGA") -> Path:
    manifest = read_json(repo_to_local(out_dir, game_row(out_dir, key)["manifest"]))
    return repo_to_local(out_dir, manifest["detail_files"][0]["path"])


def _rewrite(path: Path, mutate) -> None:
    doc = read_json(path)
    mutate(doc)
    path.write_text(_encode(doc), encoding="utf-8")


def test_verify_catches_a_deleted_detail_shard(built):
    shard = _first_detail_path(built)
    shard.unlink()
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("missing file" in p and shard.name in p for p in problems), problems
    assert any("detail shards" in p for p in problems), problems


def test_verify_catches_a_deleted_quotes_file(built):
    path = repo_to_local(built, game_row(built, "26OCT17AUBUGA")["quote_files"][0])
    path.unlink()
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("missing file" in p and "quotes.json" in p for p in problems), problems
    assert any("quote rows" in p for p in problems), problems


def test_verify_catches_a_deleted_manifest_slate_or_index(built):
    manifest = repo_to_local(built, game_row(built, "26OCT17AUBUGA")["manifest"])
    manifest.unlink()
    assert any("missing file" in p and "manifest.json" in p for p in verify_execution_artifacts(built, PREFIX))

    slate = built / "execution" / "slates" / "2026-10-17.json"
    slate.unlink()
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("missing file" in p and "2026-10-17.json" in p for p in problems)
    assert any("slate files do not list exactly the indexed games" in p for p in problems)

    (built / "cfb_execution_index.json").unlink()
    assert any("missing file" in p for p in verify_execution_artifacts(built, PREFIX))


def test_verify_catches_a_tampered_game_fingerprint_in_a_quotes_file(built):
    path = repo_to_local(built, game_row(built, "26OCT17AUBUGA")["quote_files"][0])
    _rewrite(path, lambda d: d.__setitem__("game_fingerprint", "0" * 64))
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("game_fingerprint mismatch" in p and "quotes.json" in p for p in problems), problems


def test_verify_catches_a_tampered_game_fingerprint_in_a_detail_shard_or_manifest(built):
    _rewrite(_first_detail_path(built), lambda d: d.__setitem__("game_fingerprint", "0" * 64))
    assert any("game_fingerprint mismatch" in p for p in verify_execution_artifacts(built, PREFIX))

    manifest = repo_to_local(built, game_row(built, "26OCT17WASHUSC")["manifest"])
    _rewrite(manifest, lambda d: d.__setitem__("game_fingerprint", "1" * 64))
    assert any("manifest game_fingerprint mismatch" in p for p in verify_execution_artifacts(built, PREFIX))


def test_verify_catches_the_game_file_changing_after_the_build(built):
    path = built / "games" / "26OCT17AUBUGA.json"
    game = read_json(path)
    game["markets"][0]["yes_ask"] = 0.99
    path.write_text(_encode(game), encoding="utf-8")
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("index game_fingerprint does not match the published game file" in p for p in problems), problems


def test_verify_catches_an_orphan_json_under_execution(built):
    orphan = built / "execution" / "games" / "26OCT17AUBUGA" / "events" / "STALE-EVENT.json"
    orphan.write_text("{}\n", encoding="utf-8")
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("orphan file" in p and "STALE-EVENT.json" in p for p in problems), problems


def test_verify_catches_a_stale_part_left_behind(built):
    shard = _first_detail_path(built)
    twin = shard.with_name(shard.stem + ".part02of02.json")
    twin.write_text(shard.read_text(encoding="utf-8"), encoding="utf-8")
    assert any("orphan file" in p for p in verify_execution_artifacts(built, PREFIX))


def test_verify_catches_index_fingerprint_not_matching_the_catalog(built):
    index_path = built / "cfb_execution_index.json"
    _rewrite(index_path, lambda d: d["capture"].__setitem__("content_fingerprint", "d" * 64))
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("index fingerprint" in p and "catalog fingerprint" in p for p in problems), problems


def test_verify_catches_a_status_file_fingerprint_mismatch(built):
    _rewrite(built / "cfb_catalog_status.json", lambda d: d.__setitem__("content_fingerprint", "e" * 64))
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("status fingerprint" in p for p in problems), problems


def test_verify_without_a_status_file_skips_only_that_check(built):
    (built / "cfb_catalog_status.json").unlink()
    assert verify_execution_artifacts(built, PREFIX) == []


def test_verify_catches_a_file_over_the_hard_limit(built, monkeypatch):
    assert verify_execution_artifacts(built, PREFIX) == []
    sizes = sorted(p.stat().st_size for p in (built / "execution").rglob("*.json"))
    limit = sizes[len(sizes) // 2]
    monkeypatch.setattr(execution, "HARD_LIMIT_BYTES", limit)
    problems = verify_execution_artifacts(built, PREFIX)
    over = [p for p in problems if "byte limit" in p]
    assert over, problems
    assert all(f"over the {limit}-byte limit" in p for p in over)


def test_write_refuses_a_file_over_the_hard_limit(tree, monkeypatch):
    monkeypatch.setattr(execution, "HARD_LIMIT_BYTES", 500)
    with pytest.raises(ExecutionBuildError, match="hard limit"):
        write_execution_artifacts(tree, PREFIX)


def test_verify_catches_a_quote_row_removed_from_quotes_json(built):
    path = repo_to_local(built, game_row(built, "26OCT17AUBUGA")["quote_files"][0])
    _rewrite(path, lambda d: d["quotes"].pop())
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("quote rows (4) != game contracts (5)" in p for p in problems), problems


def test_verify_catches_a_duplicated_quote_row(built):
    path = repo_to_local(built, game_row(built, "26OCT17AUBUGA")["quote_files"][0])
    _rewrite(path, lambda d: d["quotes"].append(copy.deepcopy(d["quotes"][0])))
    assert any("quote rows" in p for p in verify_execution_artifacts(built, PREFIX))


def test_verify_catches_a_market_removed_from_a_detail_shard(built):
    _rewrite(_first_detail_path(built), lambda d: d["markets"].pop())
    assert any("detail shards" in p for p in verify_execution_artifacts(built, PREFIX))


def test_verify_catches_a_game_missing_from_or_extra_in_the_index(built):
    index_path = built / "cfb_execution_index.json"
    _rewrite(index_path, lambda d: d["games"].pop())
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("index games differ from catalog" in p for p in problems), problems


def test_verify_catches_a_slate_row_on_the_wrong_date(built):
    path = built / "execution" / "slates" / "2026-10-17.json"
    _rewrite(path, lambda d: d["games"][0].__setitem__("slate_date", "2026-10-18"))
    problems = verify_execution_artifacts(built, PREFIX)
    assert any("listed on slate 2026-10-17 but dated 2026-10-18" in p for p in problems)


def test_verify_catches_a_pointer_outside_the_prefix(built):
    _rewrite(built / "cfb_execution_index.json", lambda d: d["slates"][0].__setitem__("path", "elsewhere/x.json"))
    assert any("pointer outside" in p for p in verify_execution_artifacts(built, PREFIX))


def test_verify_catches_non_json(built):
    _first_detail_path(built).write_text("{not json", encoding="utf-8")
    assert any("is not JSON" in p for p in verify_execution_artifacts(built, PREFIX))


def test_verify_reports_missing_index_and_missing_catalog(tmp_path, tree):
    assert any("missing file" in p for p in verify_execution_artifacts(tmp_path, PREFIX))
    write_execution_artifacts(tree, PREFIX)
    (tree / "cfb_market_catalog.json").unlink()
    assert any("cannot read catalog" in p for p in verify_execution_artifacts(tree, PREFIX))


# --------------------------------------------------------------------------
# 9. build errors
# --------------------------------------------------------------------------


def test_missing_game_file_is_a_build_error(tree):
    (tree / "games" / "26OCT17AUBUGA.json").unlink()
    with pytest.raises(ExecutionBuildError, match="26OCT17AUBUGA"):
        write_execution_artifacts(tree, PREFIX)
    assert not (tree / "cfb_execution_index.json").exists()


def test_missing_or_unparseable_catalog_is_a_build_error(tmp_path):
    with pytest.raises(ExecutionBuildError):
        write_execution_artifacts(tmp_path, PREFIX)
    (tmp_path / "cfb_market_catalog.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(ExecutionBuildError):
        write_execution_artifacts(tmp_path, PREFIX)


def test_unparseable_game_file_is_a_build_error(tree):
    (tree / "games" / "26OCT17AUBUGA.json").write_text("{truncated", encoding="utf-8")
    with pytest.raises(ExecutionBuildError, match="cannot read detail file"):
        write_execution_artifacts(tree, PREFIX)


def test_game_file_without_a_markets_list_is_a_build_error(tree):
    path = tree / "games" / "26OCT17AUBUGA.json"
    _rewrite(path, lambda d: d.__setitem__("markets", None))
    with pytest.raises(ExecutionBuildError, match="no markets"):
        write_execution_artifacts(tree, PREFIX)


def test_catalog_market_count_mismatch_is_a_build_error(tree):
    catalog_path = tree / "cfb_market_catalog.json"
    _rewrite(catalog_path, lambda d: d["games"][0].__setitem__("market_count", 999))
    with pytest.raises(ExecutionBuildError, match="market_count disagrees"):
        write_execution_artifacts(tree, PREFIX)


def test_game_file_market_count_mismatch_is_a_build_error(tree):
    path = tree / "games" / "26OCT17AUBUGA.json"
    _rewrite(path, lambda d: d.__setitem__("market_count", d["market_count"] + 1))
    with pytest.raises(ExecutionBuildError, match="market_count disagrees"):
        write_execution_artifacts(tree, PREFIX)


def test_markets_list_shorter_than_declared_count_is_a_build_error(tree):
    path = tree / "games" / "26OCT17AUBUGA.json"
    _rewrite(path, lambda d: d["markets"].pop())
    with pytest.raises(ExecutionBuildError, match="market_count disagrees"):
        write_execution_artifacts(tree, PREFIX)


@pytest.mark.parametrize("bad_key", ["../evil", "a/b", "..", ".hidden", "", "has space", "x\\y"])
def test_unsafe_game_key_is_rejected(tmp_path, bad_key):
    write_tree(tmp_path)
    catalog_path = tmp_path / "cfb_market_catalog.json"
    _rewrite(catalog_path, lambda d: d["games"][0].__setitem__("game_key", bad_key))
    with pytest.raises((ValueError, ExecutionBuildError)):
        write_execution_artifacts(tmp_path, PREFIX)
    assert not (tmp_path.parent / "evil").exists()


def test_unsafe_event_ticker_is_rejected(tmp_path):
    spec = make_game_spec("26OCT17AUBUGA", "Auburn at Georgia", "2026-10-17T16:00:00Z")
    for market in spec["markets"]:
        market["event_ticker"] = "../../escape"
    write_tree(tmp_path, [spec])
    with pytest.raises((ValueError, ExecutionBuildError)):
        write_execution_artifacts(tmp_path, PREFIX)
    assert not (tmp_path.parent / "escape.json").exists()


def test_failed_build_leaves_the_existing_execution_layer_intact(tree):
    write_execution_artifacts(tree, PREFIX)
    before = exec_files(tree)
    (tree / "games" / "26OCT17AUBUGA.json").unlink()
    with pytest.raises(ExecutionBuildError):
        write_execution_artifacts(tree, PREFIX)
    assert exec_files(tree) == before


# --------------------------------------------------------------------------
# 10. no model output
# --------------------------------------------------------------------------

FORBIDDEN_KEY_PARTS = ("projection", "fair", "edge", "expected_score", "kelly", "recommend")


def _all_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_keys(child)


def _forbidden(keys) -> list[str]:
    return sorted({k for k in keys if any(part in str(k).lower() for part in FORBIDDEN_KEY_PARTS)})


def test_quote_rows_carry_no_model_output_keys(built):
    checked = 0
    for row in load_index(built)["games"]:
        for path in row["quote_files"]:
            for quote in read_json(repo_to_local(built, path))["quotes"]:
                assert _forbidden(quote) == [], quote
                checked += 1
    assert checked == 20


def test_index_and_slate_game_rows_carry_no_model_output_keys(built):
    index = load_index(built)
    for row in index["games"]:
        assert _forbidden(_all_keys(row)) == []
    for slate in index["slates"]:
        assert _forbidden(_all_keys(slate)) == []
        doc = read_json(repo_to_local(built, slate["path"]))
        for row in doc["games"]:
            assert _forbidden(_all_keys(row)) == []
    assert index["contains_model_projections"] is False


def test_quote_row_keys_are_a_fixed_published_set():
    row = quote_row(make_contract("G", "E-G", 0))
    assert _forbidden(row) == []
    assert set(row) == {
        "market_ticker", "event_ticker", "family", "is_alternate_line", "title", "status", "strike_type",
        "floor_strike", "yes_bid", "yes_ask", "no_bid", "no_ask", "yes_bid_size", "yes_ask_size", "book_state",
        "buy_yes_executable", "buy_no_executable", "buy_yes_fee", "buy_no_fee", "updated_time",
    }  # fmt: skip


# --------------------------------------------------------------------------
# 11. the CLI
# --------------------------------------------------------------------------


def _load_cli():
    path = REPO_ROOT / "scripts" / "build_cfb_execution_index.py"
    spec = importlib.util.spec_from_file_location("build_cfb_execution_index_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_builds_and_verifies_a_good_tree(tree, capsys):
    cli = _load_cli()
    assert cli.main(["--out-dir", str(tree), "--prefix", PREFIX]) == 0
    out = capsys.readouterr().out
    assert "execution index: 4 games / 20 contracts across 3 slates" in out
    assert "VERIFIED" in out
    assert (tree / "cfb_execution_index.json").is_file()
    # a second run is a no-op that still verifies
    assert cli.main(["--out-dir", str(tree), "--prefix", PREFIX]) == 0
    assert "0 files written" in capsys.readouterr().out
    assert cli.main(["--out-dir", str(tree), "--prefix", PREFIX, "--verify-only"]) == 0


def test_cli_verify_only_writes_nothing(tree):
    cli = _load_cli()
    # nothing built yet: verification fails, and still writes nothing
    assert cli.main(["--out-dir", str(tree), "--prefix", PREFIX, "--verify-only"]) == 3
    assert not (tree / "execution").exists()
    assert not (tree / "cfb_execution_index.json").exists()


def test_cli_verify_only_returns_3_after_tampering(tree, capsys):
    cli = _load_cli()
    assert cli.main(["--out-dir", str(tree), "--prefix", PREFIX]) == 0
    shard = _first_detail_path(tree)
    shard.unlink()
    capsys.readouterr()
    assert cli.main(["--out-dir", str(tree), "--prefix", PREFIX, "--verify-only"]) == 3
    captured = capsys.readouterr()
    assert "missing file" in captured.out
    assert "FATAL" in captured.err
    # a full (non-verify-only) run heals it
    assert cli.main(["--out-dir", str(tree), "--prefix", PREFIX]) == 0
    assert shard.is_file()


def test_cli_returns_1_when_the_build_fails(tree, capsys):
    cli = _load_cli()
    (tree / "games" / "26OCT17AUBUGA.json").unlink()
    assert cli.main(["--out-dir", str(tree), "--prefix", PREFIX]) == 1
    assert "execution index build failed" in capsys.readouterr().err


def test_cli_prefix_defaults_to_out_dir_as_given(tree, monkeypatch):
    cli = _load_cli()
    monkeypatch.chdir(tree.parent)
    rel = tree.name
    assert cli.main(["--out-dir", f"./{rel}/"]) == 0
    row = game_row(tree, "26OCT17AUBUGA")
    assert row["manifest"].startswith(f"{rel}/execution/games/26OCT17AUBUGA/")


# --------------------------------------------------------------------------
# 12. the COMMITTED production data
# --------------------------------------------------------------------------

COMMITTED_INDEX = REPO_ROOT / "data" / "live" / "cfb_execution_index.json"


@pytest.mark.skipif(not COMMITTED_INDEX.is_file(), reason="no committed execution index in this checkout")
def test_committed_execution_layer_verifies_and_is_small():
    live = REPO_ROOT / "data" / "live"
    assert verify_execution_artifacts(live, "data/live") == []

    sizes = {p: p.stat().st_size for p in (live / "execution").rglob("*") if p.is_file()}
    sizes[COMMITTED_INDEX] = COMMITTED_INDEX.stat().st_size
    assert len(sizes) > 1
    too_big = {str(p.relative_to(REPO_ROOT)): s for p, s in sizes.items() if s >= 1_000_000}
    assert too_big == {}


@pytest.mark.skipif(not COMMITTED_INDEX.is_file(), reason="no committed execution index in this checkout")
def test_committed_index_matches_the_committed_catalog_and_status():
    live = REPO_ROOT / "data" / "live"
    index = read_json(COMMITTED_INDEX)
    catalog = read_json(live / "cfb_market_catalog.json")
    assert index["capture"]["content_fingerprint"] == catalog["capture"]["content_fingerprint"]
    status = live / "cfb_catalog_status.json"
    if status.is_file():
        assert read_json(status)["content_fingerprint"] == index["capture"]["content_fingerprint"]
    assert {g["game_key"] for g in index["games"]} == {g["game_key"] for g in catalog["games"]}
    assert index["contains_model_projections"] is False
