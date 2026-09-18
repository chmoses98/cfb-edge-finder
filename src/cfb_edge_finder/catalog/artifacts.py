"""Serialize a capture into the published artifacts.

THE SHAPE, AND WHY IT IS SPLIT
  data/live/cfb_market_catalog.json   -- the SLATE INDEX. One entry per
      physical game: identity, its Kalshi events, how many markets of each
      family it has, its completeness diagnostics, and a pointer to its
      detail file. A few hundred KB, so it can be read end-to-end.
  data/live/games/<game_key>.json     -- ONE GAME'S COMPLETE INVENTORY.
      Every contract with full semantics, settlement rules, executable
      prices, quoted sizes and mechanics.
  data/live/cfb_markets_flat.json     -- optional; one row per contract
      across the whole slate, for search and joins.

*** WHY NOT ONE FILE ***
A live capture is 239 games and 15,312 contracts. Each contract carries
its own settlement rules, and that is not padding -- it is the thing a
handicapper has to read to know what the contract means. One file holding
all of them measured **52 MB**, and stayed above 31 MB even with compact
separators and every dispensable field stripped. The mission's two
requirements for the primary artifact -- "compact enough that ChatGPT can
ingest/read it efficiently" and "retaining all betting-relevant contract
information" -- cannot both hold in a single 15,000-contract document.

Splitting resolves it without dropping anything, because it matches how
the artifact is actually read: nobody asks "what can I bet across 239
games", they ask "what can I bet on THIS game". Read the index (~300 KB),
then one game file (typically 10-600 KB).

It also fixes the storage cost. A 52 MB blob rewritten whenever any price
moves -- which, during a slate, is constantly -- is exactly the storage
explosion this mission warns against. Per-game files mean a commit touches
only the games that actually changed.

*** WHY DETERMINISM IS ENFORCED, NOT HOPED FOR ***
These files are committed by a scheduled job. If key order or list order
drifted between runs, every run would produce a diff, every diff would be
noise, and change detection (the thing that keeps this cheap) would be
useless. So: every mapping is written with sorted keys, every list has an
explicit sort, and the only fields that legitimately change when nothing
has changed (`captured_at` and anything derived from it) are confined to
a `capture` block that change detection deliberately ignores. See
`catalog_content_fingerprint`.

*** WHAT IS NOT IN HERE ***
No projection, no fair value, no expected score, no model edge. The
`mechanics` block on each contract is arithmetic on the quote -- see
mechanics.py for the line and why it sits where it does.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cfb_edge_finder.catalog.classification import MarketFamilyLabel
from cfb_edge_finder.catalog.contract import contract_to_dict
from cfb_edge_finder.catalog.discovery import (
    MULTIVARIATE_COVERAGE_NOTE,
    CatalogGame,
    CatalogRun,
)

CATALOG_SCHEMA_VERSION = "cfb_market_catalog/1.0.0"
FLAT_SCHEMA_VERSION = "cfb_markets_flat/1.0.0"


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat().replace("+00:00", "Z") if moment is not None else None


def _event_summary(event_ticker: str, event: dict[str, Any]) -> dict[str, Any]:
    metadata = event.get("product_metadata") or {}
    return {
        "event_ticker": event_ticker,
        "series_ticker": event.get("series_ticker"),
        "title": event.get("title"),
        "sub_title": event.get("sub_title"),
        "competition": metadata.get("competition"),
        "competition_scope": metadata.get("competition_scope"),
        "mutually_exclusive": event.get("mutually_exclusive"),
        "settlement_sources": event.get("settlement_sources") or [],
    }


def game_detail_filename(game_key: str) -> str:
    """Per-game detail file name. Kept to the game key so the index's
    pointer is derivable and a stale file is recognizable."""
    return f"{game_key}.json"


def game_to_dict(game: CatalogGame, include_raw: bool = False, include_markets: bool = True) -> dict[str, Any]:
    """One game. `include_markets=False` yields the index entry: identity,
    events, counts and diagnostics, but not the contracts themselves."""
    milestone = game.milestone
    contracts = sorted(game.contracts, key=lambda c: c.market_ticker)

    payload: dict[str, Any] = {
        "game_key": game.game_key,
        "title": game.title,
        "kickoff": _iso(game.kickoff),
        "identity": {
            # Kalshi's own game identity -- no external schedule provider
            # and no CFBD key involved anywhere in this block.
            "source": "kalshi_milestone" if milestone else "kalshi_event_ticker",
            "milestone_id": milestone.milestone_id if milestone else None,
            "milestone_status": milestone.status if milestone else None,
            "league": milestone.league if milestone else None,
            "division": milestone.division if milestone else None,
            "conference": milestone.conference if milestone else None,
            "tier": milestone.tier if milestone else None,
            "season_year": milestone.season_year if milestone else None,
            "season_type": milestone.season_type if milestone else None,
            "season_week": milestone.season_week if milestone else None,
            "main_game_event_ticker": milestone.main_game_event_ticker if milestone else None,
        },
        "events": [_event_summary(t, e) for t, e in sorted(game.events.items())],
        "market_count": len(contracts),
        "family_distribution": game.family_distribution,
        "completeness": {
            **game.completeness.to_dict(),
            # The two coverage axes are reported separately and never
            # merged: we can be complete on the native game menu while
            # being structurally unable to enumerate combos.
            "native_game_markets_complete": game.completeness.native_game_markets_complete,
            "multivariate_market_coverage": {
                "claim": "eligible_legs_only",
                "combo_markets_enumerable_per_game": False,
                "combo_eligible_event_tickers": game.multivariate_eligible_event_tickers,
                "combo_eligible_event_count": len(game.multivariate_eligible_event_tickers),
                "note": MULTIVARIATE_COVERAGE_NOTE,
            },
        },
    }
    if include_markets:
        payload["markets"] = [contract_to_dict(c, include_raw=include_raw) for c in contracts]
    else:
        payload["markets_file"] = f"games/{game_detail_filename(game.game_key)}"
    return payload


def build_catalog(run: CatalogRun, include_raw: bool = False, include_markets: bool = False) -> dict[str, Any]:
    """The primary artifact: the slate index.

    `include_markets=True` inlines every contract, producing the single
    monolithic document. That is what the audit and the tests use, and it
    is NOT what gets published -- a live capture of it measured 52 MB. See
    this module's docstring."""
    games = sorted(run.games.values(), key=lambda g: (g.kickoff is None, g.kickoff, g.game_key))

    family_totals: Counter[str] = Counter()
    status_totals: Counter[str] = Counter()
    for game in games:
        for contract in game.contracts:
            family_totals[str(contract.classification.family)] += 1
            status_totals[str(contract.status or "unknown")] += 1

    incomplete = [g.game_key for g in games if not g.completeness.native_game_markets_complete]

    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "artifact_layout": (
            "slate index; each game's full contract inventory is in its own file named by "
            "games[].markets_file"
        )
        if not include_markets
        else "monolithic; every contract inlined under games[].markets",
        "capture": {
            "captured_at": _iso(run.captured_at),
            "source": "kalshi_public_rest_v2",
            "authenticated": False,
            "contains_model_projections": False,
            "notes": (
                "Market inventory only. No model projection, fair probability, expected score or "
                "model-generated edge appears anywhere in this artifact; the per-contract `mechanics` "
                "block is arithmetic on the quoted price alone."
            ),
        },
        "discovery": {
            "spine": "kalshi_football_game_milestones",
            "milestone_filter": {"type": "football_game", "details.league": "NCAAFB"},
            "reconciliation_path": "dynamic_series_event_sweep",
            "milestones_considered": run.milestones_considered,
            "milestones_selected": run.milestones_selected,
            "milestone_sweep_complete": run.milestone_sweep_complete,
            "series_discovered": run.series_discovered,
            "series_with_open_events": run.series_with_open_events,
            "series_sweep_complete": run.series_sweep_complete,
            "events_only_in_series_sweep": sorted(run.events_only_in_series_sweep),
            "events_only_in_series_sweep_count": len(run.events_only_in_series_sweep),
            "requests_made": run.stats.requests_made,
            # A cheap run and an expensive one must be distinguishable. A
            # jump in events_fetched_individually means the bulk series
            # sweeps are failing or missing series, which is a cost and
            # reliability signal even when the menu comes out complete.
            "events_served_from_prefetch": run.events_served_from_prefetch,
            "events_fetched_individually": run.events_fetched_individually,
            "prefetched_events": run.prefetched_events,
            "prefetched_markets": run.prefetched_markets,
            "request_failures": run.stats.request_failures,
            "pagination_failures": run.stats.pagination_failures,
            "failed_paths": sorted(run.stats.failed_paths),
            "errors": sorted(run.errors),
        },
        "totals": {
            "physical_games": len(games),
            "events": sum(len(g.events) for g in games),
            "markets": sum(len(g.contracts) for g in games),
            "season_level_events": len(run.season_level_events),
            "family_distribution": dict(sorted(family_totals.items())),
            "status_distribution": dict(sorted(status_totals.items())),
            "unknown_family_markets": family_totals.get(str(MarketFamilyLabel.UNKNOWN), 0),
        },
        "completeness": {
            "capture_complete": run.complete,
            "games_incomplete": sorted(incomplete),
            "games_incomplete_count": len(incomplete),
            "native_game_markets_complete": run.complete,
            "multivariate_market_coverage": {
                "claim": "eligible_legs_only",
                "combo_markets_enumerable_per_game": False,
                "note": MULTIVARIATE_COVERAGE_NOTE,
            },
        },
        "games": [
            game_to_dict(g, include_raw=include_raw, include_markets=include_markets) for g in games
        ],
        "season_level_events": [
            _event_summary(t, e) for t, e in sorted(run.season_level_events.items())
        ],
    }


def build_flat_index(run: CatalogRun, include_raw: bool = False) -> dict[str, Any]:
    """One row per contract, sorted by ticker, for search and joins."""
    rows: list[dict[str, Any]] = []
    for game in run.games.values():
        for contract in game.contracts:
            row = contract_to_dict(contract, include_raw=include_raw)
            row["game_key"] = game.game_key
            row["game_title"] = game.title
            row["kickoff"] = _iso(game.kickoff)
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("market_ticker")))
    return {
        "schema_version": FLAT_SCHEMA_VERSION,
        "capture": {"captured_at": _iso(run.captured_at), "contains_model_projections": False},
        "market_count": len(rows),
        "markets": rows,
    }


# Fields whose value changes on every run even when the market surface is
# identical. Change detection must ignore them or every run rewrites the
# file and the "efficient polling" requirement is defeated.
_VOLATILE_KEYS = frozenset(
    {
        "captured_at",
        "capture",
        "mechanics",
        "requests_made",
        "request_failures",
        "pagination_failures",
        "updated_time",
        "quote_age_seconds",
        "seconds_until_close",
        "seconds_until_occurrence",
    }
)


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in sorted(value.items()) if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def catalog_content_fingerprint(catalog: dict[str, Any]) -> str:
    """Stable hash of the MARKET CONTENT, ignoring capture timestamps and
    time-derived mechanics.

    Two captures of an unchanged market surface produce the same
    fingerprint, so the scheduled job can skip the commit entirely. Prices
    and sizes ARE included -- a price move is a real change worth
    recording; only clock-driven fields are excluded."""
    stripped = _strip_volatile(catalog)
    encoded = json.dumps(stripped, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _encode(payload: dict[str, Any]) -> str:
    """The one encoder every artifact goes through.

    `indent=1` costs ~10% on a document this shape and buys nothing: these
    files are read by machines, and a human inspecting one has `jq`. Sorted
    keys and a trailing newline keep the output byte-stable, which is what
    makes "rewrite only if changed" work at all."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n"


def _write_text(path: Path, encoded: str) -> int:
    """Atomic write. The temp-file-then-replace dance matters because these
    files are read by other processes (and by a human mid-slate) while a
    scheduled job may be rewriting them: a reader must never see a
    half-written catalog."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(encoded, encoding="utf-8")
    temp.replace(path)
    return len(encoded)


def write_json(path: Path, payload: dict[str, Any]) -> int:
    """Write one artifact deterministically and atomically."""
    return _write_text(path, _encode(payload))

@dataclass(frozen=True)
class WrittenArtifacts:
    """What a publish actually produced, so the caller can report it."""

    catalog_path: Path
    catalog_bytes: int
    game_files_written: int
    game_files_unchanged: int
    game_files_pruned: int
    game_bytes_total: int
    flat_path: Path | None = None
    flat_bytes: int = 0

    @property
    def total_bytes(self) -> int:
        return self.catalog_bytes + self.game_bytes_total + self.flat_bytes


def write_catalog_artifacts(
    run: CatalogRun,
    out_dir: Path,
    include_raw: bool = False,
    write_flat: bool = False,
) -> WrittenArtifacts:
    """Publish the slate index and one detail file per game.

    A game file is rewritten only when its bytes actually change, so a
    refresh over an unchanged game touches nothing -- which is what keeps
    a 30-minute cadence from rewriting tens of megabytes every tick.

    Detail files for games no longer on the slate are PRUNED, otherwise
    `games/` grows without bound and a stale file silently advertises a
    finished game's menu as current."""
    out_dir.mkdir(parents=True, exist_ok=True)
    games_dir = out_dir / "games"
    games_dir.mkdir(parents=True, exist_ok=True)

    written = unchanged = pruned = 0
    game_bytes = 0
    expected: set[str] = set()

    for game in run.games.values():
        filename = game_detail_filename(game.game_key)
        expected.add(filename)
        path = games_dir / filename
        payload = game_to_dict(game, include_raw=include_raw, include_markets=True)
        encoded = _encode(payload)
        game_bytes += len(encoded)
        if path.is_file() and path.read_text(encoding="utf-8") == encoded:
            unchanged += 1
            continue
        _write_text(path, encoded)
        written += 1

    for stale in sorted(games_dir.glob("*.json")):
        if stale.name not in expected:
            stale.unlink()
            pruned += 1

    catalog = build_catalog(run, include_raw=False, include_markets=False)
    catalog["capture"]["content_fingerprint"] = catalog_content_fingerprint(catalog)
    catalog_path = out_dir / "cfb_market_catalog.json"
    catalog_bytes = write_json(catalog_path, catalog)

    flat_path = None
    flat_bytes = 0
    if write_flat:
        flat_path = out_dir / "cfb_markets_flat.json"
        flat_bytes = write_json(flat_path, build_flat_index(run, include_raw=include_raw))

    return WrittenArtifacts(
        catalog_path=catalog_path,
        catalog_bytes=catalog_bytes,
        game_files_written=written,
        game_files_unchanged=unchanged,
        game_files_pruned=pruned,
        game_bytes_total=game_bytes,
        flat_path=flat_path,
        flat_bytes=flat_bytes,
    )
