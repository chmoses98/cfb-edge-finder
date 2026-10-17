"""The RUN CFB execution index: small, predictable files a GitHub-connected
LLM session can actually retrieve.

*** WHY THIS EXISTS ***
GitHub's Contents API -- the file-access path ChatGPT's GitHub connector
uses -- returns a file's metadata and SHA with ``"encoding": "none"`` and an
EMPTY ``content`` string for any file over 1 MB. Measured on main
(2026-10-17): ``cfb_market_catalog.json`` is 1.50 MB and every marquee
game's detail file (``games/26OCT17AUBUGA.json`` and friends) is ~1.27 MB,
so exactly the games a session most wants came back empty. GitHub code
search does not index files over 384 KB either, so searching for them
cannot work. The data was fine; it was simply too large to be read through
that door.

This module derives, from the already-published catalog and game files,
a set of artifacts every one of which is far below that limit:

  data/live/cfb_execution_index.json               the entry point
  data/live/execution/slates/<YYYY-MM-DD>.json     one slate (US/Eastern date)
  data/live/execution/games/<game_key>/manifest.json
  data/live/execution/games/<game_key>/quotes.json  EVERY contract's executable
                                                    YES/NO quote, size and fee
  data/live/execution/games/<game_key>/events/<event_ticker>.json
                                                    the verbatim full contract
                                                    records (rules, fee block,
                                                    mechanics) for one event

Nothing is dropped. ``quotes.json`` lists every contract the game file
lists, and the per-event detail shards together hold every game-file
market record byte-for-byte as published. Any file that would exceed the
shard budget is split into numbered parts, and the manifest lists the
parts; no consumer ever has to guess a path.

*** WHY IT IS DERIVED FROM DISK, NOT FROM THE IN-MEMORY RUN ***
So it can be rebuilt from exactly what is committed. The scheduled
workflow skips its commit when the market surface is unchanged; if the
execution files ever drift from the committed catalog (first deploy, a
bug fix, a hand edit), the workflow rebuilds them from the committed
catalog and commits only that resync. A deterministic rebuild over an
in-sync tree is byte-identical, so this never churns.

*** WHAT IS NOT IN HERE ***
No projection, no fair value, no edge, no recommendation -- the same line
artifacts.py holds. A quote row is the published quote restated, plus
which side of the book a buy executes against.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from cfb_edge_finder.catalog.freshness import (
    OFF_PEAK_CADENCE_MINUTES,
    SLATE_WINDOW_CADENCE_MINUTES,
    STALENESS_TOLERANCE_MULTIPLE,
)

EXECUTION_SCHEMA_VERSION = "cfb_execution_index/1.0.0"
INDEX_FILENAME = "cfb_execution_index.json"
CATALOG_FILENAME = "cfb_market_catalog.json"
STATUS_FILENAME = "cfb_catalog_status.json"
EXECUTION_DIRNAME = "execution"

# GitHub's Contents API returns empty content above 1 MB. The hard limit is
# kept well under it; the shard target is far under it so a session's
# context window, not just the API, can hold a file comfortably.
GITHUB_CONTENTS_API_LIMIT_BYTES = 1_000_000
HARD_LIMIT_BYTES = 800_000
SHARD_TARGET_BYTES = 250_000

SLATE_TIMEZONE = "America/New_York"
_SLATE_TZ = ZoneInfo(SLATE_TIMEZONE)

# Kickoff windows, by US/Eastern kickoff time. Boundaries are inclusive at
# the start: 14:00 ET is afternoon, 18:00 ET is evening.
WINDOW_EARLY = "early"
WINDOW_AFTERNOON = "afternoon"
WINDOW_EVENING = "evening"
WINDOW_UNSCHEDULED = "unscheduled"
AFTERNOON_STARTS_HOUR_ET = 14
EVENING_STARTS_HOUR_ET = 18
UNSCHEDULED_SLATE = "unscheduled"

WINDOW_DEFINITIONS = {
    WINDOW_EARLY: "kickoff before 14:00 US/Eastern (noon window and earlier)",
    WINDOW_AFTERNOON: "kickoff 14:00-17:59 US/Eastern (the 3:30 window)",
    WINDOW_EVENING: "kickoff 18:00 US/Eastern or later (primetime and late night)",
    WINDOW_UNSCHEDULED: "no published kickoff time",
}

QUOTE_LEGEND = {
    "BUY_YES": "executes at yes_ask; size offered is yes_ask_size; fee per contract is buy_yes_fee.",
    "BUY_NO": "executes at no_ask (NEVER 1 - yes_ask, which is the NO bid). The NO ask is the YES bid "
    "book (no_ask == 1 - yes_bid), so the size available is yes_bid_size; fee per contract is buy_no_fee. "
    "Kalshi publishes no_ask_size / no_bid_size as null.",
    "prices": "dollars per $1 contract; a $1 binary's price is its implied probability.",
    "buy_yes_executable / buy_no_executable": "true only when that side has positive quoted size. "
    "A price with zero size (e.g. the $0.00/$1.00 empty book) is not an offer.",
    "buy_yes_fee / buy_no_fee": "Kalshi model TRADE fee per contract at that side's ask, rounded up to "
    "$0.000001. Not a net fee. null = unknown cost, never free (see fee_support when present).",
    "book_state": "two_sided | bid_only | ask_only | empty_book | no_quote. Read before any price.",
    "floor_strike": "points (not money). Kalshi encodes '3+' as floor_strike 2.5.",
    "title": "what YES means, in Kalshi's words. Settlement text (rules_primary, rules_secondary, "
    "settlement_sources), the full fee block and mechanics are in event_files[event_ticker].",
    "status": "active = tradeable; finalized/determined = settled or settling; initialized = not yet open.",
    "updated_time": "when Kalshi last changed this contract. Compute quote age against your own clock.",
}


# --------------------------------------------------------------------------
# encoding (same conventions as artifacts.py: sorted keys, compact, newline)
# --------------------------------------------------------------------------


def _encode(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n"


def _sha256(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# identity helpers
# --------------------------------------------------------------------------


def kickoff_slot(kickoff: Any) -> dict[str, Any]:
    """Slate date, window and Eastern-time kickoff for one game.

    The slate date is the US/Eastern calendar date of kickoff -- NOT the
    date inside the game key. Kalshi's key date is unreliable for this:
    Missouri at Ole Miss kicks off 2026-10-17 15:30 ET but is keyed
    ``26OCT16MIZZMISS``."""
    moment = _parse_utc(kickoff)
    if moment is None or moment.tzinfo is None:
        return {"kickoff_et": None, "slate_date": UNSCHEDULED_SLATE, "window": WINDOW_UNSCHEDULED}
    local = moment.astimezone(_SLATE_TZ)
    if local.hour < AFTERNOON_STARTS_HOUR_ET:
        window = WINDOW_EARLY
    elif local.hour < EVENING_STARTS_HOUR_ET:
        window = WINDOW_AFTERNOON
    else:
        window = WINDOW_EVENING
    return {
        "kickoff_et": local.strftime("%Y-%m-%d %H:%M %Z"),
        "slate_date": local.strftime("%Y-%m-%d"),
        "window": window,
    }


def split_teams(title: Any) -> dict[str, Any]:
    """Teams from Kalshi's milestone title.

    ``"Auburn at Georgia"`` states away/home. ``"Duke vs Virginia"`` does
    not, so home/away are left null rather than guessed."""
    text = title if isinstance(title, str) else ""
    if text.count(" at ") == 1:
        away, home = (part.strip() for part in text.split(" at "))
        return {"away_team": away, "home_team": home, "teams": [away, home], "home_away_basis": "title 'AWAY at HOME'"}
    if text.count(" vs ") == 1:
        first, second = (part.strip() for part in text.split(" vs "))
        return {
            "away_team": None,
            "home_team": None,
            "teams": [first, second],
            "home_away_basis": "not stated (title uses 'vs'); do not infer home field from order",
        }
    return {"away_team": None, "home_team": None, "teams": [], "home_away_basis": "title not parseable"}


def _safe_component(value: str) -> str:
    """Game keys and event tickers are [A-Z0-9-]; anything else is a bug
    upstream and must not become a path traversal here."""
    if not value or any(not (c.isalnum() or c in "-_.") for c in value) or value.startswith("."):
        raise ValueError(f"unsafe path component: {value!r}")
    return value


# --------------------------------------------------------------------------
# quote rows
# --------------------------------------------------------------------------


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def quote_row(market: dict[str, Any]) -> dict[str, Any]:
    """One contract's executable quote, restated so the side a buy executes
    against is explicit. Pure function of the published record; the full
    record (rules, fee block, mechanics) is in the event detail shard."""
    fee = market.get("fee") if isinstance(market.get("fee"), dict) else {}
    mechanics = market.get("mechanics") if isinstance(market.get("mechanics"), dict) else {}
    yes_ask_size = market.get("yes_ask_size")
    yes_bid_size = market.get("yes_bid_size")
    row = {
        "market_ticker": market.get("market_ticker"),
        "event_ticker": market.get("event_ticker"),
        "family": market.get("family"),
        "is_alternate_line": market.get("is_alternate_line"),
        "title": market.get("title"),
        "status": market.get("status"),
        "strike_type": market.get("strike_type"),
        "floor_strike": market.get("floor_strike"),
        "yes_bid": market.get("yes_bid"),
        "yes_ask": market.get("yes_ask"),
        "no_bid": market.get("no_bid"),
        "no_ask": market.get("no_ask"),
        "yes_bid_size": yes_bid_size,
        "yes_ask_size": yes_ask_size,
        "book_state": mechanics.get("book_state"),
        "buy_yes_executable": _positive(yes_ask_size) and market.get("yes_ask") is not None,
        "buy_no_executable": _positive(yes_bid_size) and market.get("no_ask") is not None,
        "buy_yes_fee": fee.get("model_trade_fee_at_yes_ask"),
        "buy_no_fee": fee.get("model_trade_fee_at_no_ask"),
        "updated_time": market.get("updated_time"),
    }
    # Rare fields are published only when they carry information, so 300
    # rows of nulls do not crowd a session's context.
    if market.get("cap_strike") is not None:
        row["cap_strike"] = market.get("cap_strike")
    if fee.get("support") != "supported":
        row["fee_support"] = fee.get("support")
        row["fee_unavailable_reason"] = fee.get("unavailable_reason")
    return row


# --------------------------------------------------------------------------
# sharding
# --------------------------------------------------------------------------


def _chunk_by_bytes(items: list[Any], budget: int, overhead: int) -> list[list[Any]]:
    """Split ``items`` into consecutive chunks whose encoded size stays under
    ``budget`` (plus a fixed per-file ``overhead``). Order is preserved, so
    the split is deterministic for a given input. A single item larger than
    the budget gets a chunk of its own rather than being dropped."""
    chunks: list[list[Any]] = []
    current: list[Any] = []
    size = overhead
    for item in items:
        item_bytes = len(_encode(item))
        if current and size + item_bytes > budget:
            chunks.append(current)
            current, size = [], overhead
        current.append(item)
        size += item_bytes
    if current or not chunks:
        chunks.append(current)
    return chunks


def _part_names(stem: str, count: int) -> list[str]:
    if count == 1:
        return [f"{stem}.json"]
    return [f"{stem}.part{i:02d}of{count:02d}.json" for i in range(1, count + 1)]


# --------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------


@dataclass
class ExecutionBuild:
    """Every file the execution layer should contain, keyed by path relative
    to the catalog's out_dir, plus counts for reporting."""

    files: dict[str, str] = field(default_factory=dict)
    games: int = 0
    markets: int = 0
    slates: list[str] = field(default_factory=list)


class ExecutionBuildError(RuntimeError):
    """The published catalog cannot be turned into a trustworthy execution
    layer (a game file is missing, unreadable or contradicts the index)."""


def _game_row(entry: dict[str, Any], prefix: str, quote_files: list[str], game_fp: str) -> dict[str, Any]:
    completeness = entry.get("completeness") if isinstance(entry.get("completeness"), dict) else {}
    complete = completeness.get("native_game_markets_complete") is True
    identity = entry.get("identity") if isinstance(entry.get("identity"), dict) else {}
    key = entry["game_key"]
    row: dict[str, Any] = {
        "game_key": key,
        "title": entry.get("title"),
        **split_teams(entry.get("title")),
        "kickoff": entry.get("kickoff"),
        **kickoff_slot(entry.get("kickoff")),
        "milestone_status": identity.get("milestone_status"),
        "division": identity.get("division"),
        "conference": identity.get("conference"),
        "tier": identity.get("tier"),
        "market_count": entry.get("market_count"),
        "native_game_markets_complete": complete,
        "usable": complete,
        "game_fingerprint": game_fp,
        "manifest": f"{prefix}/{EXECUTION_DIRNAME}/games/{key}/manifest.json",
        "quote_files": quote_files,
    }
    if not complete:
        row["incomplete_diagnostics"] = {
            name: completeness.get(name)
            for name in (
                "failed_event_tickers",
                "pagination_failed_event_tickers",
                "events_fetched",
                "related_event_tickers_reported",
                "api_failures",
            )
        }
    return row


def build_execution_files(out_dir: Path, prefix: str = "data/live") -> ExecutionBuild:
    """Derive every execution artifact from the catalog and game files
    already written under ``out_dir``. Writes nothing.

    ``prefix`` is the repository path of ``out_dir`` (``data/live`` in
    production). Every pointer published is a full repository path, which
    is what a GitHub file-retrieval call takes."""
    prefix = prefix.rstrip("/")
    catalog_path = out_dir / CATALOG_FILENAME
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExecutionBuildError(f"cannot read {catalog_path}: {exc}") from exc

    capture = catalog.get("capture") or {}
    completeness = catalog.get("completeness") or {}
    totals = catalog.get("totals") or {}
    build = ExecutionBuild()

    slate_rows: dict[str, list[dict[str, Any]]] = {}
    index_rows: list[dict[str, Any]] = []

    for entry in sorted(catalog.get("games") or [], key=lambda g: str(g.get("game_key"))):
        key = _safe_component(str(entry.get("game_key") or ""))
        markets_file = entry.get("markets_file") or f"games/{key}.json"
        game_path = out_dir / markets_file
        try:
            raw = game_path.read_bytes()
            game = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise ExecutionBuildError(f"game {key}: cannot read detail file {game_path}: {exc}") from exc
        markets = game.get("markets")
        if not isinstance(markets, list):
            raise ExecutionBuildError(f"game {key}: detail file has no markets[] list")
        if game.get("market_count") != len(markets) or entry.get("market_count") != len(markets):
            raise ExecutionBuildError(
                f"game {key}: market_count disagrees (index {entry.get('market_count')}, "
                f"file {game.get('market_count')}, markets[] {len(markets)})"
            )
        game_fp = _sha256(raw)
        game_dir = f"{EXECUTION_DIRNAME}/games/{key}"

        # --- per-event detail shards: the verbatim market records ---------
        events_meta = {e.get("event_ticker"): e for e in game.get("events") or [] if isinstance(e, dict)}
        by_event: dict[str, list[dict[str, Any]]] = {}
        for market in markets:
            by_event.setdefault(str(market.get("event_ticker")), []).append(market)
        detail_files: list[dict[str, Any]] = []
        event_file_for: dict[str, str] = {}
        for event_ticker in sorted(by_event):
            stem = _safe_component(event_ticker)
            records = sorted(by_event[event_ticker], key=lambda m: str(m.get("market_ticker")))
            chunks = _chunk_by_bytes(records, SHARD_TARGET_BYTES, overhead=4_000)
            names = _part_names(stem, len(chunks))
            event_file_for[event_ticker] = f"{prefix}/{game_dir}/events/{names[0]}"
            for part, (name, chunk) in enumerate(zip(names, chunks, strict=True), start=1):
                rel = f"{game_dir}/events/{name}"
                payload = {
                    "schema_version": EXECUTION_SCHEMA_VERSION,
                    "artifact": "event_detail_shard",
                    "game_key": key,
                    "game_fingerprint": game_fp,
                    "event_ticker": event_ticker,
                    "event": events_meta.get(event_ticker),
                    "part": part,
                    "parts": len(chunks),
                    "event_market_count": len(records),
                    "market_count": len(chunk),
                    "markets": chunk,
                }
                encoded = _encode(payload)
                build.files[rel] = encoded
                detail_files.append(
                    {
                        "path": f"{prefix}/{rel}",
                        "event_ticker": event_ticker,
                        "part": part,
                        "parts": len(chunks),
                        "market_count": len(chunk),
                        "families": sorted({str(m.get("family")) for m in chunk}),
                        "bytes": len(encoded.encode("utf-8")),
                    }
                )

        # --- quotes: every contract, one compact row ----------------------
        rows = [
            quote_row(m)
            for m in sorted(markets, key=lambda m: (str(m.get("event_ticker")), str(m.get("market_ticker"))))
        ]
        quote_chunks = _chunk_by_bytes(rows, SHARD_TARGET_BYTES, overhead=6_000)
        quote_names = _part_names("quotes", len(quote_chunks))
        quote_paths = [f"{prefix}/{game_dir}/{name}" for name in quote_names]
        row = _game_row(entry, prefix, quote_paths, game_fp)
        for part, (name, chunk) in enumerate(zip(quote_names, quote_chunks, strict=True), start=1):
            payload = {
                "schema_version": EXECUTION_SCHEMA_VERSION,
                "artifact": "game_quotes",
                "game_key": key,
                "title": entry.get("title"),
                "kickoff": entry.get("kickoff"),
                "kickoff_et": row["kickoff_et"],
                "game_fingerprint": game_fp,
                "native_game_markets_complete": row["native_game_markets_complete"],
                "game_market_count": len(markets),
                "part": part,
                "parts": len(quote_chunks),
                "market_count": len(chunk),
                "family_distribution": dict(sorted(Counter(str(r["family"]) for r in chunk).items())),
                "legend": QUOTE_LEGEND,
                "event_files": {t: event_file_for[t] for t in sorted({str(r["event_ticker"]) for r in chunk})},
                "quotes": chunk,
            }
            build.files[f"{game_dir}/{name}"] = _encode(payload)

        # --- manifest: everything about the game except the markets -------
        manifest = {
            "schema_version": EXECUTION_SCHEMA_VERSION,
            "artifact": "game_manifest",
            **row,
            "family_distribution": game.get("family_distribution"),
            "identity": game.get("identity"),
            "completeness": game.get("completeness"),
            "events": game.get("events"),
            "source_game_file": f"{prefix}/{markets_file}",
            "source_game_file_bytes": len(raw),
            "detail_files": detail_files,
        }
        build.files[f"{game_dir}/manifest.json"] = _encode(manifest)

        slate_rows.setdefault(row["slate_date"], []).append(row)
        index_rows.append(row)
        build.games += 1
        build.markets += len(markets)

    # --- slate files ------------------------------------------------------
    slate_summaries: list[dict[str, Any]] = []
    for slate_date in sorted(slate_rows):
        rows_for_date = sorted(slate_rows[slate_date], key=lambda r: (str(r["kickoff"]), r["game_key"]))
        windows = Counter(r["window"] for r in rows_for_date)
        rel = f"{EXECUTION_DIRNAME}/slates/{_safe_component(slate_date)}.json"
        build.files[rel] = _encode(
            {
                "schema_version": EXECUTION_SCHEMA_VERSION,
                "artifact": "slate",
                "slate_date": slate_date,
                "slate_timezone": SLATE_TIMEZONE,
                "window_definitions": WINDOW_DEFINITIONS,
                "game_count": len(rows_for_date),
                "window_counts": dict(sorted(windows.items())),
                "games": rows_for_date,
            }
        )
        slate_summaries.append(
            {
                "slate_date": slate_date,
                "path": f"{prefix}/{rel}",
                "game_count": len(rows_for_date),
                "window_counts": dict(sorted(windows.items())),
            }
        )
        build.slates.append(slate_date)

    # --- the index --------------------------------------------------------
    index = {
        "schema_version": EXECUTION_SCHEMA_VERSION,
        "artifact": "execution_index",
        "contains_model_projections": False,
        "capture": {
            "captured_at": capture.get("captured_at"),
            "captured_at_meaning": (
                "the last time the market content CHANGED, not the last time the collector observed it. "
                "The workflow does not commit an unchanged surface."
            ),
            "content_fingerprint": capture.get("content_fingerprint"),
            "catalog_schema_version": catalog.get("schema_version"),
            "capture_complete": completeness.get("capture_complete"),
            "games_incomplete": completeness.get("games_incomplete") or [],
            "physical_games": totals.get("physical_games"),
            "markets": totals.get("markets"),
        },
        "freshness": {
            "status_file": f"{prefix}/{STATUS_FILENAME}",
            "status_file_must_match": "status_file.content_fingerprint == capture.content_fingerprint",
            "verify_latest_successful_run": (
                "GET /repos/chmoses98/cfb-edge-finder/actions/workflows/kalshi-market-catalog.yml/runs"
                "?branch=main&status=success&per_page=1  -> workflow_runs[0].updated_at"
            ),
            "stale_after_minutes": {
                "slate_window_thu_1800_to_sun_0600_utc": SLATE_WINDOW_CADENCE_MINUTES * STALENESS_TOLERANCE_MULTIPLE,
                "off_peak": OFF_PEAK_CADENCE_MINUTES * STALENESS_TOLERANCE_MULTIPLE,
            },
            "quote_status": (
                "CAPTURED quotes, not verified live executable quotes. Confirm on Kalshi before any order."
            ),
        },
        "retrieval": {
            "procedure": "docs/RUN_CFB_CONTRACT.md",
            "every_file_under_bytes": HARD_LIMIT_BYTES,
            "github_contents_api_limit_bytes": GITHUB_CONTENTS_API_LIMIT_BYTES,
            "do_not_fetch": [
                f"{prefix}/{CATALOG_FILENAME} (over 1 MB: the GitHub Contents API returns empty content)",
                f"{prefix}/games/<game_key>.json (marquee games exceed 1 MB; use the shards below)",
                "app/latest/board.json (not the complete Kalshi inventory)",
            ],
            "slate_date_rule": "US/Eastern calendar date of kickoff; never the date inside game_key",
        },
        "slate_timezone": SLATE_TIMEZONE,
        "window_definitions": WINDOW_DEFINITIONS,
        "slates": slate_summaries,
        "games": [
            {
                k: r[k]
                for k in (
                    "game_key",
                    "title",
                    "kickoff",
                    "kickoff_et",
                    "slate_date",
                    "window",
                    "market_count",
                    "usable",
                    "game_fingerprint",
                    "quote_files",
                    "manifest",
                )
            }
            for r in sorted(index_rows, key=lambda r: (str(r["kickoff"]), r["game_key"]))
        ],
    }
    build.files["../" + INDEX_FILENAME] = _encode(index)
    return build


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionWriteResult:
    files_written: int
    files_unchanged: int
    files_pruned: int
    games: int
    markets: int
    slates: tuple[str, ...]
    largest_file: tuple[str, int]


def _resolve(out_dir: Path, rel: str) -> Path:
    if rel.startswith("../"):
        # only the index lives beside the execution directory
        return out_dir / rel[3:]
    return out_dir / rel


def write_execution_artifacts(out_dir: Path, prefix: str = "data/live") -> ExecutionWriteResult:
    """Build and publish the execution layer. Rewrites a file only when its
    bytes change, and prunes any execution file no longer produced (a game
    that left the slate must not keep advertising its menu)."""
    build = build_execution_files(out_dir, prefix=prefix)
    written = unchanged = 0
    largest = ("", 0)
    for rel, encoded in sorted(build.files.items()):
        size = len(encoded.encode("utf-8"))
        if size > HARD_LIMIT_BYTES:
            raise ExecutionBuildError(f"{rel} would be {size} bytes, over the {HARD_LIMIT_BYTES}-byte hard limit")
        if size > largest[1]:
            largest = (rel, size)
        path = _resolve(out_dir, rel)
        if path.is_file() and path.read_text(encoding="utf-8") == encoded:
            unchanged += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(encoded, encoding="utf-8")
        temp.replace(path)
        written += 1

    expected = {_resolve(out_dir, rel).resolve() for rel in build.files}
    pruned = 0
    exec_root = out_dir / EXECUTION_DIRNAME
    for stale in sorted(exec_root.rglob("*")):
        if stale.is_file() and stale.resolve() not in expected:
            stale.unlink()
            pruned += 1
    for directory in sorted((p for p in exec_root.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(directory.iterdir()):
            directory.rmdir()

    return ExecutionWriteResult(
        files_written=written,
        files_unchanged=unchanged,
        files_pruned=pruned,
        games=build.games,
        markets=build.markets,
        slates=tuple(build.slates),
        largest_file=largest,
    )


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------


def verify_execution_artifacts(out_dir: Path, prefix: str = "data/live") -> list[str]:
    """Every way the published execution layer could mislead a consumer.
    Returns a list of problems; empty means verified.

    Checks against what is ON DISK (not a rebuild), so it catches a partial
    write, a stale shard or a hand edit as well as a builder bug."""
    prefix = prefix.rstrip("/")
    problems: list[str] = []

    def load(repo_path: str) -> dict[str, Any] | None:
        if not repo_path.startswith(prefix + "/"):
            problems.append(f"pointer outside {prefix}: {repo_path}")
            return None
        path = out_dir / repo_path[len(prefix) + 1 :]
        if not path.is_file():
            problems.append(f"missing file: {repo_path}")
            return None
        size = path.stat().st_size
        if size > HARD_LIMIT_BYTES:
            problems.append(f"{repo_path} is {size} bytes, over the {HARD_LIMIT_BYTES}-byte limit")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{repo_path} is not JSON: {exc}")
            return None

    index = load(f"{prefix}/{INDEX_FILENAME}")
    if index is None:
        return problems
    try:
        catalog = json.loads((out_dir / CATALOG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return problems + [f"cannot read catalog: {exc}"]

    published_fp = (catalog.get("capture") or {}).get("content_fingerprint")
    if index["capture"].get("content_fingerprint") != published_fp:
        problems.append(
            f"index fingerprint {index['capture'].get('content_fingerprint')} != catalog fingerprint {published_fp}"
        )
    status_path = out_dir / STATUS_FILENAME
    if status_path.is_file():
        status_fp = json.loads(status_path.read_text(encoding="utf-8")).get("content_fingerprint")
        if status_fp != published_fp:
            problems.append(f"status fingerprint {status_fp} != catalog fingerprint {published_fp}")

    catalog_games = {g["game_key"]: g for g in catalog.get("games") or []}
    index_games = {g["game_key"]: g for g in index.get("games") or []}
    if set(catalog_games) != set(index_games):
        problems.append(
            f"index games differ from catalog: missing {sorted(set(catalog_games) - set(index_games))[:10]}, "
            f"extra {sorted(set(index_games) - set(catalog_games))[:10]}"
        )

    referenced: set[str] = {f"{prefix}/{INDEX_FILENAME}"}
    slate_keys: set[str] = set()
    for slate in index.get("slates") or []:
        referenced.add(slate["path"])
        doc = load(slate["path"])
        if doc is None:
            continue
        for row in doc.get("games") or []:
            slate_keys.add(row["game_key"])
            if row["slate_date"] != slate["slate_date"]:
                problems.append(
                    f"{row['game_key']} listed on slate {slate['slate_date']} but dated {row['slate_date']}"
                )
    if slate_keys != set(index_games):
        problems.append("slate files do not list exactly the indexed games")

    for key, row in sorted(index_games.items()):
        entry = catalog_games.get(key)
        if entry is None:
            continue
        game_path = out_dir / (entry.get("markets_file") or f"games/{key}.json")
        try:
            raw = game_path.read_bytes()
        except OSError:
            problems.append(f"{key}: source game file missing")
            continue
        game_fp = _sha256(raw)
        if row["game_fingerprint"] != game_fp:
            problems.append(f"{key}: index game_fingerprint does not match the published game file")
        game_tickers = Counter(m.get("market_ticker") for m in json.loads(raw).get("markets") or [])

        referenced.add(row["manifest"])
        manifest = load(row["manifest"])
        quote_tickers: Counter[Any] = Counter()
        for path in row["quote_files"]:
            referenced.add(path)
            doc = load(path)
            if doc is None:
                continue
            if doc.get("game_fingerprint") != game_fp:
                problems.append(f"{path}: game_fingerprint mismatch")
            quote_tickers.update(q.get("market_ticker") for q in doc.get("quotes") or [])
        if quote_tickers != game_tickers:
            problems.append(f"{key}: quote rows ({sum(quote_tickers.values())}) != game contracts "
                            f"({sum(game_tickers.values())})")

        if manifest is None:
            continue
        if manifest.get("game_fingerprint") != game_fp:
            problems.append(f"{key}: manifest game_fingerprint mismatch")
        detail_tickers: Counter[Any] = Counter()
        for detail in manifest.get("detail_files") or []:
            referenced.add(detail["path"])
            doc = load(detail["path"])
            if doc is None:
                continue
            if doc.get("game_fingerprint") != game_fp:
                problems.append(f"{detail['path']}: game_fingerprint mismatch")
            detail_tickers.update(m.get("market_ticker") for m in doc.get("markets") or [])
        if detail_tickers != game_tickers:
            problems.append(f"{key}: detail shards ({sum(detail_tickers.values())}) != game contracts "
                            f"({sum(game_tickers.values())})")

    exec_root = out_dir / EXECUTION_DIRNAME
    if exec_root.is_dir():
        for path in sorted(exec_root.rglob("*.json")):
            repo_path = f"{prefix}/{path.relative_to(out_dir).as_posix()}"
            if repo_path not in referenced:
                problems.append(f"orphan file not referenced by the index: {repo_path}")
    return problems
