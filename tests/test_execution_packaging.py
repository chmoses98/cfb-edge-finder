"""Packaging: parts change how the universe is delivered, never what is
in it.

The shard budget was lowered from "the largest file that fits" to a size
someone can actually handicap game by game. That is a packaging decision,
and these tests are what make it *only* a packaging decision: the same
slate split nine ways must contain exactly the same games and exactly the
same contracts as the same slate split four ways.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from cfb_edge_finder.execution.cli import CHATGPT_PROMPT, main
from cfb_edge_finder.execution.shards import (
    DEFAULT_MAX_ANALYSIS_BYTES,
    build_shards,
    split_window,
    write_shards,
)
from cfb_edge_finder.execution.slate import build_slate
from tests.analysis_helpers import analysis_tickers
from tests.execution_fakes import GAME_KEY, NOW, catalog_dir, market, standard_markets
from tests.test_execution_disposition import config

# Enough games, spread across every window, that a small budget is forced
# to make several parts and a large one is not.
KICKOFFS = {
    GAME_KEY: "2026-09-19T16:00:00Z",
    "26SEP19AAABBB": "2026-09-19T16:30:00Z",
    "26SEP19CCCDDD": "2026-09-19T17:00:00Z",
    "26SEP19EEEFFF": "2026-09-19T20:00:00Z",
    "26SEP19GGGHHH": "2026-09-19T20:30:00Z",
    "26SEP19IIIJJJ": "2026-09-19T23:30:00Z",
    "26SEP20KKKLLL": "2026-09-20T03:00:00Z",
}


def slate_for(tmp_path: Path):
    extra = [
        (key, "LSU at Ole Miss", kickoff, standard_markets(key))
        for key, kickoff in KICKOFFS.items()
        if key != GAME_KEY
    ]
    directory = catalog_dir(
        tmp_path, kickoff=KICKOFFS[GAME_KEY], extra_games=extra
    )
    return build_slate(directory, config(), slate_date=None).slate


def written_artifacts(out: Path) -> list[dict]:
    return [
        json.loads(p.read_text())
        for p in sorted((out / "shards").glob("*.analysis.*.json"))
    ]


def package(tmp_path: Path, max_bytes: int, name: str):
    slate = slate_for(tmp_path)
    out = tmp_path / name
    manifest = write_shards(slate, build_shards(slate, max_bytes=max_bytes), out)
    return slate, out, manifest


# ------------------------------------------------- exactly once, always


def test_every_game_appears_exactly_once_across_the_analysis_shards(tmp_path):
    slate, out, _ = package(tmp_path, 1, "tiny")
    seen = Counter(
        game["game_key"] for document in written_artifacts(out) for game in document["games"]
    )
    expected = {p["game_key"] for p in slate["games"]}
    assert set(seen) == expected
    assert all(count == 1 for count in seen.values()), seen.most_common(3)


def test_every_eligible_contract_appears_exactly_once(tmp_path):
    slate, out, _ = package(tmp_path, 1, "tiny")
    tickers = Counter()
    for document in written_artifacts(out):
        tickers.update(analysis_tickers(document))
    expected = {c["ticker"] for p in slate["games"] for c in p["contracts"]}
    assert set(tickers) == expected
    assert all(count == 1 for count in tickers.values()), tickers.most_common(3)
    assert sum(tickers.values()) == slate["reconciliation"]["contracts_eligible"]


def test_no_game_is_split_across_parts(tmp_path):
    """A game's contracts all travel together or the handicap cannot be
    applied to them."""
    slate, out, _ = package(tmp_path, 1, "tiny")
    by_game: dict[str, set[str]] = {}
    for document in written_artifacts(out):
        for game in document["games"]:
            assert game["game_key"] not in by_game, "game appears in two parts"
            by_game[game["game_key"]] = set()

    for packet in slate["games"]:
        parts = [
            document
            for document in written_artifacts(out)
            if any(g["game_key"] == packet["game_key"] for g in document["games"])
        ]
        assert len(parts) == 1
        game = next(g for g in parts[0]["games"] if g["game_key"] == packet["game_key"])
        assert game["eligible_contracts"] == packet["counts"]["eligible"]


def test_no_contract_is_unaccounted_in_any_part(tmp_path):
    _, out, manifest = package(tmp_path, 1, "tiny")
    for document in written_artifacts(out):
        reconciliation = document["reconciliation"]
        assert reconciliation["unaccounted_contracts"] == 0
        assert (
            reconciliation["contracts_in_analysis_artifact"]
            == reconciliation["eligible_contracts"]
        )
    assert manifest["totals"]["unaccounted_contracts"] == 0
    assert manifest["reconciles"] is True


# ------------------------------- packaging only, never eligibility


def test_sharding_changes_packaging_and_nothing_else(tmp_path):
    """Nine parts and one part must deliver the identical universe. If a
    budget could change what is eligible, the budget would be a filter."""
    slate, small_out, small_manifest = package(tmp_path, 1, "small")
    _, large_out, large_manifest = package(tmp_path, 10_000_000, "large")

    small_docs = written_artifacts(small_out)
    large_docs = written_artifacts(large_out)
    assert len(small_docs) > len(large_docs)

    def universe(documents):
        tickers: set[str] = set()
        games: set[str] = set()
        for document in documents:
            tickers |= analysis_tickers(document)
            games |= {g["game_key"] for g in document["games"]}
        return games, tickers

    assert universe(small_docs) == universe(large_docs)
    assert (
        small_manifest["totals"]["contracts_in_analysis_artifacts"]
        == large_manifest["totals"]["contracts_in_analysis_artifacts"]
        == slate["reconciliation"]["contracts_eligible"]
    )
    assert (
        small_manifest["totals"]["contracts_excluded"]
        == large_manifest["totals"]["contracts_excluded"]
    )


def test_a_single_game_bigger_than_the_budget_stays_whole(tmp_path):
    """The budget never wins against the game-is-indivisible rule. A game
    whose own payload exceeds the target gets a part of its own, intact --
    it is never trimmed and its markets are never split."""
    fat = standard_markets()
    for index in range(60):
        fat.append(
            market(
                f"KXNCAAFTOTAL-{GAME_KEY}-{200 + index}",
                family="game_total",
                title=f"Over {199.5 + index} points scored",
                floor_strike=199.5 + index,
                is_alternate_line=True,
            )
        )
    directory = catalog_dir(tmp_path, fat, kickoff="2026-09-19T16:00:00Z")
    slate = build_slate(directory, config(), slate_date=None).slate
    packet = slate["games"][0]

    shards = split_window("early", slate["games"], max_bytes=1, max_contracts=1)
    assert len(shards) == 1
    assert shards[0].packets[0]["counts"]["eligible"] == len(fat) == packet["counts"]["eligible"]

    out = tmp_path / "out"
    write_shards(slate, shards, out)
    document = written_artifacts(out)[0]
    assert len(analysis_tickers(document)) == len(fat)
    assert document["reconciliation"]["unaccounted_contracts"] == 0


# ------------------------------------------------- naming and order


def test_parts_are_numbered_deterministically_and_in_kickoff_order(tmp_path):
    _, out, manifest = package(tmp_path, 1, "tiny")
    names = [Path(e["analysis_file"]).name for e in manifest["shards"]]
    assert all(name.count(".") == 3 and name.endswith(".json") for name in names), names

    for entry in manifest["shards"]:
        expected = f"{entry['kickoff_window']}.analysis.{entry['window_part']:02d}.json"
        assert Path(entry["analysis_file"]).name == expected

    windows = [e["kickoff_window"] for e in manifest["shards"]]
    assert windows == sorted(windows, key=["early", "afternoon", "evening", "late"].index)
    for window in set(windows):
        parts = [e["window_part"] for e in manifest["shards"] if e["kickoff_window"] == window]
        assert parts == list(range(1, len(parts) + 1))


def test_a_single_part_window_is_still_numbered(tmp_path):
    """No special case: `late.analysis.01.json` whether or not a second
    part exists."""
    _, out, manifest = package(tmp_path, 10_000_000, "large")
    late = next(e for e in manifest["shards"] if e["kickoff_window"] == "late")
    assert Path(late["analysis_file"]).name == "late.analysis.01.json"
    assert late["window_parts"] == 1


def test_the_manifest_upload_order_matches_the_files_on_disk(tmp_path):
    _, out, manifest = package(tmp_path, 1, "tiny")
    listed = manifest["upload_order"]
    assert listed == [e["analysis_file"] for e in manifest["shards"]]
    for relative in listed:
        assert (out / relative).is_file(), relative
    on_disk = {p.name for p in (out / "shards").glob("*.analysis.*.json")}
    assert {Path(r).name for r in listed} == on_disk


def test_each_part_states_where_it_sits_in_its_window(tmp_path):
    _, out, manifest = package(tmp_path, 1, "tiny")
    for entry in manifest["shards"]:
        document = json.loads((out / entry["analysis_file"]).read_text())
        assert document["window_part"] == entry["window_part"]
        assert document["window_parts"] == entry["window_parts"]
        assert "no game's contracts are split across parts" in document["part_note"]


# ---------------------------------------------------------- the CLI


def test_the_printed_upload_order_matches_the_artifacts_produced(tmp_path, capsys):
    catalog = catalog_dir(
        tmp_path,
        kickoff=KICKOFFS[GAME_KEY],
        extra_games=[
            (key, "LSU at Ole Miss", kickoff, standard_markets(key))
            for key, kickoff in KICKOFFS.items()
            if key != GAME_KEY
        ],
    )
    out = tmp_path / "exec"
    assert main([
        "prepare-live",
        "--catalog-dir", str(catalog),
        "--out-dir", str(out),
        "--as-of", NOW.isoformat(),
        "--date", "all",
        "--max-analysis-bytes", "1",
    ]) == 0
    printed = capsys.readouterr().out

    manifest = json.loads((out / "shard_manifest.json").read_text())
    expected = [f"{out}/{relative}" for relative in manifest["upload_order"]]

    section = printed[printed.index("UPLOAD THESE") :]
    positions = [section.index(path) for path in expected]
    assert positions == sorted(positions), "printed order does not match the manifest order"
    assert f"UPLOAD THESE {len(expected)} FILES" in printed
    assert CHATGPT_PROMPT in printed

    on_disk = {p.name for p in (out / "shards").glob("*.analysis.*.json")}
    assert {Path(p).name for p in expected} == on_disk


def test_the_default_budget_targets_reasoning_depth(tmp_path):
    assert DEFAULT_MAX_ANALYSIS_BYTES == 250_000
