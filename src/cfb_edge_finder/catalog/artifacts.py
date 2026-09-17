"""Serialize a capture into the published artifacts.

TWO ARTIFACTS, TWO JOBS
  data/live/cfb_market_catalog.json  -- the primary product. Structured
      around PHYSICAL GAMES: one entry per game, holding that game's
      complete market inventory plus its completeness diagnostics. Built
      to be read end-to-end by an external handicapper or ChatGPT, so the
      raw Kalshi payload is omitted here (every betting-relevant field is
      still present as a named key).
  data/live/cfb_markets_flat.json    -- one row per contract, for search
      and joins. Carries the raw payload when asked, so nothing captured
      is ever unreachable.

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


def game_to_dict(game: CatalogGame, include_raw: bool = False) -> dict[str, Any]:
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
        "markets": [contract_to_dict(c, include_raw=include_raw) for c in contracts],
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
    return payload


def build_catalog(run: CatalogRun, include_raw: bool = False) -> dict[str, Any]:
    """The primary, game-structured artifact."""
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
        "games": [game_to_dict(g, include_raw=include_raw) for g in games],
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


def write_json(path: Path, payload: dict[str, Any]) -> int:
    """Write deterministically and atomically. Returns bytes written.

    The temp-file-then-replace dance matters because these files are read
    by other processes (and by a human mid-slate) while a scheduled job may
    be rewriting them: a reader must never see a half-written catalog."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True, indent=1, default=str) + "\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(encoded, encoding="utf-8")
    temp.replace(path)
    return len(encoded)
