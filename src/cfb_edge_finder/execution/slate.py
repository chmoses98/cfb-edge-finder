"""Build the compact execution slate from the live Kalshi catalog.

Input:  `data/live/` -- the catalog this repository already produces.
Output: `data/execution/latest/cfb_execution_slate.json` -- one packet per
        physical game, carrying EVERY mechanically eligible contract plus
        an explicit disposition for every contract that did not make it.

*** WHAT IS REMOVED, AND WHAT IS NOT ***
Removed: raw Kalshi payload bulk -- the verbatim rules text, the settlement
timers, the open interest, the multivariate boilerplate note repeated on
every game, the fee block's four paragraphs of prose. That is ~65 MB of
game files compacted to a few MB.

NOT removed: any contract. The packet carries every eligible ticker, and
every ineligible ticker appears in `excluded_contracts` with the reason it
was excluded. There is no third bucket and nothing is summarised away.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cfb_edge_finder.execution import EXECUTION_SCHEMA_VERSION
from cfb_edge_finder.execution.disposition import (
    Disposition,
    DispositionConfig,
    MechanicalStatus,
    classify,
    executable_entry,
    parse_timestamp,
)
from cfb_edge_finder.execution.semantics import (
    ContractSemantics,
    build_game_teams,
    derive_semantics,
)
from cfb_edge_finder.execution.windows import kickoff_window

FEE_DISCLOSURE = {
    "is_net_fee": False,
    "excludes": ["rounding_fee", "rebate", "fee_accumulator", "user_balance_precision"],
    "formula": "ceil_to_0.000001(fee_multiplier * 0.07 * contracts * P * (1 - P))",
    "note": (
        "The documented Kalshi TRADE fee only, computed by catalog/fees.py from each side's own "
        "quoted ask. Kalshi's net fee is trade fee + rounding fee - rebate, and the last two depend "
        "on the account's balance precision and the order's fill sequence -- neither is knowable "
        "without credentials, so neither is estimated. A contract whose fee model this repository "
        "does not implement is excluded as unsupported_fee_model rather than priced with a guess."
    ),
    "stated_once": (
        "published once per artifact rather than on all 14,000 contracts, which is the only "
        "compaction applied to the fee block"
    ),
}


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _round(value: float | None, places: int = 6) -> float | None:
    return None if value is None else round(value, places)


def _fee_at(contract: dict[str, Any], key: str) -> float | None:
    fee = contract.get("fee")
    if not isinstance(fee, dict):
        return None
    value = fee.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _entry_economics(entry: float | None, fee: float | None) -> dict[str, Any] | None:
    """What one contract actually costs and what has to be true to break
    even on it.

    `breakeven_probability` is the whole point: a YES at $0.62 with a
    $0.0166 fee needs a TRUE probability above 0.6366 before a cent of it
    is expected value. Publishing entry and fee separately invites the
    reader to forget the second one."""
    if entry is None:
        return None
    fee_value = fee or 0.0
    return {
        "entry": _round(entry),
        "fee": _round(fee_value),
        "cost_per_contract": _round(entry + fee_value),
        "max_profit_per_contract": _round(1.0 - entry - fee_value),
        "breakeven_probability": _round(entry + fee_value),
    }


def build_contract_record(
    contract: dict[str, Any],
    semantics: ContractSemantics,
    game_key: str,
    as_of: datetime,
) -> dict[str, Any]:
    ticker = str(contract.get("market_ticker"))
    yes_entry = executable_entry(contract.get("yes_ask"))
    no_entry = executable_entry(contract.get("no_ask"))
    fee_yes = _fee_at(contract, "model_trade_fee_at_yes_ask")
    fee_no = _fee_at(contract, "model_trade_fee_at_no_ask")
    quote_timestamp = parse_timestamp(contract.get("updated_time"))
    fee = contract.get("fee") if isinstance(contract.get("fee"), dict) else {}

    record: dict[str, Any] = {
        "ticker": ticker,
        "market_id": ticker,
        "game_id": game_key,
        "event_ticker": contract.get("event_ticker"),
        "series_ticker": contract.get("series_ticker"),
        "family": contract.get("family"),
        "is_alternate_line": bool(contract.get("is_alternate_line")),
        "semantics": semantics.as_dict(),
        "quote": {
            "yes_bid": contract.get("yes_bid"),
            "yes_ask": contract.get("yes_ask"),
            "no_bid": contract.get("no_bid"),
            "no_ask": contract.get("no_ask"),
            "quote_timestamp": contract.get("updated_time"),
            "quote_age_seconds": (
                round((as_of - quote_timestamp).total_seconds())
                if quote_timestamp is not None
                else None
            ),
        },
        "fee": {
            "model": fee.get("model"),
            "multiplier": fee.get("multiplier"),
            "support": fee.get("support"),
            "fee_at_executable_yes": _round(fee_yes),
            "fee_at_executable_no": _round(fee_no),
        },
        "executable": {
            "yes": _entry_economics(yes_entry, fee_yes),
            "no": _entry_economics(no_entry, fee_no),
        },
        "status": MechanicalStatus.ELIGIBLE.value,
    }
    return record


def _family_block(family: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    """One family's complete rung inventory, as a column table.

    *** WHY A TABLE AND NOT A LIST OF OBJECTS ***
    A Saturday shard is ~4,000 contracts. Repeating the key names on every
    one of them triples the artifact for no information, and the artifact's
    whole purpose is to fit in one handicapping conversation. Columns are
    named once; rows are values. Every eligible contract still has its own
    row -- this compresses the ENCODING, never the population.

    *** WHY LADDER RUNGS CARRY A SUFFIX AND EXPLICIT-PRICE CONTRACTS DO NOT ***
    A spread ladder's 29 tickers differ only after a shared prefix, so the
    prefix is stated once. Contracts that can ONLY be priced by naming
    their ticker in `explicit_probabilities` carry the FULL ticker
    instead: those are the ones a reader has to type back, and a
    reconstructed ticker is a typo waiting to become a silently dropped
    probability.
    """
    first = records[0]
    semantics = first["semantics"]
    requires = str(semantics.get("requires"))
    explicit = requires != "period_distribution"

    tickers = [str(r["ticker"]) for r in records]
    prefix = _common_prefix(tickers) if not explicit else ""

    block: dict[str, Any] = {
        "period": semantics.get("period"),
        "kind": semantics.get("kind"),
        "requires": requires,
        "contracts": len(records),
    }

    if explicit:
        block["columns"] = ["ticker", "yes_means"]
        block["rows"] = [[str(r["ticker"]), r["semantics"].get("yes_means")] for r in records]
        block["pricing"] = (
            "price these ONLY by naming the full ticker in explicit_probabilities; leave out any "
            "you cannot defensibly supply and they become explicitly UNPRICEABLE"
        )
        return block

    has_team = any(r["semantics"].get("team") in ("home", "away") for r in records)
    has_line = any(r["semantics"].get("line") is not None for r in records)
    columns = ["ticker_suffix"]
    if has_team:
        columns.append("team")
    if has_line:
        columns.append("line")

    rows: list[list[Any]] = []
    for record in records:
        semantics = record["semantics"]
        row: list[Any] = [str(record["ticker"])[len(prefix) :]]
        if has_team:
            row.append(semantics.get("team"))
        if has_line:
            row.append(semantics.get("line"))
        rows.append(row)

    block["ticker_prefix"] = prefix
    block["full_ticker_rule"] = "ticker_prefix + ticker_suffix"
    block["yes_means_template"] = YES_MEANS_TEMPLATES.get(
        str(semantics.get("kind")), "see yes_means_example"
    )
    block["yes_means_example"] = first["semantics"].get("yes_means")
    block["columns"] = columns
    block["rows"] = rows
    return block


YES_MEANS_TEMPLATES = {
    "moneyline": "<team> wins the <period>",
    "spread": "<team> wins the <period> by more than <line>",
    "total": "combined <period> points exceed <line>",
    "team_total": "<team> scores more than <line> points in the <period>",
    "margin_band": "<team> wins the <period> by at least <line> and at most <cap>",
}
"""What YES means for every row in a family, stated once. The first
contract's own wording travels beside it as `yes_means_example` -- a
template alone is easy to misread, and an example alone is easy to
over-generalise from (the first spread row names ONE team, and there are
two)."""


def _common_prefix(values: list[str]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        while not value.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


@dataclass(frozen=True)
class SlateBuild:
    slate: dict[str, Any]
    packets: list[dict[str, Any]]


def _event_context(game: dict[str, Any]) -> list[dict[str, Any]]:
    events = []
    for event in game.get("events") or []:
        events.append(
            {
                "event_ticker": event.get("event_ticker"),
                "competition_scope": event.get("competition_scope"),
                "title": event.get("title"),
            }
        )
    return events


def build_packet(
    index_entry: dict[str, Any],
    detail: dict[str, Any],
    config: DispositionConfig,
    seen_tickers: set[str],
    tz_name: str,
) -> dict[str, Any]:
    game_key = str(index_entry.get("game_key"))
    title = index_entry.get("title")
    kickoff_raw = index_entry.get("kickoff") or detail.get("kickoff")
    kickoff = parse_timestamp(kickoff_raw)
    identity = index_entry.get("identity") or {}
    completeness = detail.get("completeness") or {}
    markets = list(detail.get("markets") or [])

    teams = build_game_teams(title, markets)

    contracts: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    exclusion_counts: dict[str, int] = {}
    families: dict[str, dict[str, Any]] = {}

    for contract in markets:
        semantics = derive_semantics(contract, teams)
        disposition: Disposition = classify(
            contract,
            semantics_status=semantics.status,
            team_slot=semantics.team,
            kickoff=kickoff,
            config=config,
            seen_tickers=seen_tickers,
        )
        ticker = str(contract.get("market_ticker") or "")
        if ticker:
            seen_tickers.add(ticker)

        if not disposition.eligible:
            exclusion_counts[disposition.status] = exclusion_counts.get(disposition.status, 0) + 1
            excluded.append(
                {
                    "ticker": ticker or None,
                    "family": contract.get("family"),
                    "status": disposition.status,
                    "reason": disposition.reason,
                }
            )
            continue

        record = build_contract_record(contract, semantics, game_key, config.as_of)
        contracts.append(record)
        family = str(record.get("family") or "unknown")
        bucket = families.setdefault(
            family,
            {
                "contracts": 0,
                "periods": set(),
                "kinds": set(),
                "requires": set(),
                "lines": set(),
            },
        )
        bucket["contracts"] += 1
        bucket["periods"].add(semantics.period)
        bucket["kinds"].add(semantics.kind)
        bucket["requires"].add(semantics.requires)
        if semantics.threshold is not None:
            bucket["lines"].add(semantics.threshold)

    market_families = {
        family: {
            "contracts": bucket["contracts"],
            "periods": sorted(bucket["periods"]),
            "kinds": sorted(bucket["kinds"]),
            "requires": sorted(bucket["requires"]),
            "lines": sorted(bucket["lines"]),
        }
        for family, bucket in sorted(families.items())
    }

    packet: dict[str, Any] = {
        "game_key": game_key,
        "game_id": game_key,
        "title": title,
        "kickoff": kickoff_raw,
        "kickoff_window": kickoff_window(kickoff, tz_name),
        "game_metadata": {
            "teams": {
                "home": teams.home_name,
                "away": teams.away_name,
                "home_away_source": teams.home_away_source,
                "home_away_confidence": teams.home_away_confidence,
            },
            "conference": identity.get("conference"),
            "division": identity.get("division"),
            "season_year": identity.get("season_year"),
            "season_week": identity.get("season_week"),
            "season_type": identity.get("season_type"),
            "tier": identity.get("tier"),
            "milestone_status": identity.get("milestone_status"),
            "main_game_event_ticker": identity.get("main_game_event_ticker"),
            "native_game_markets_complete": completeness.get("native_game_markets_complete"),
        },
        "factual_context": {
            "kickoff_utc": kickoff_raw,
            "events": _event_context(detail),
            "catalog_completeness": {
                "markets_discovered": completeness.get("markets_discovered"),
                "api_failures": completeness.get("api_failures"),
                "failed_event_tickers": completeness.get("failed_event_tickers"),
            },
            "note": (
                "Facts only. This packet deliberately carries NO model projection, no power "
                "rating, no expected score and no fair value. The handicap is the reader's to "
                "produce; quoted prices are the market's opinion, not a reference answer."
            ),
        },
        "counts": {
            "discovered": len(markets),
            "eligible": len(contracts),
            "excluded": len(excluded),
        },
        "market_families": market_families,
        "contracts": contracts,
        "excluded_contracts": excluded,
        "exclusions": dict(sorted(exclusion_counts.items())),
    }

    packet["market_universe_hash"] = canonical_hash(
        sorted(
            (
                c["ticker"],
                (c["executable"].get("yes") or {}).get("entry"),
                (c["executable"].get("no") or {}).get("entry"),
            )
            for c in contracts
        )
    )
    packet["packet_hash"] = canonical_hash(
        {k: v for k, v in packet.items() if k != "packet_hash"}
    )
    return packet


def compact_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """The per-game handicap brief: the same contracts, an order of
    magnitude smaller.

    Quoted prices are deliberately NOT carried here. The brief's job is to
    let someone handicap the game and see exactly which rungs their
    distribution has to cover; a price column would only anchor the
    handicap to the market it is supposed to be independent of. Every
    price, fee and breakeven is in the full shard file, which is what the
    evaluator reads."""
    by_family: dict[str, list[dict[str, Any]]] = {}
    for record in packet["contracts"]:
        by_family.setdefault(str(record.get("family") or "unknown"), []).append(record)

    metadata = packet["game_metadata"]
    context = packet["factual_context"]
    scopes = sorted(
        {str(e.get("competition_scope")) for e in context.get("events") or [] if e.get("competition_scope")}
    )
    return {
        "game_key": packet["game_key"],
        "title": packet["title"],
        "kickoff": packet["kickoff"],
        "kickoff_window": packet["kickoff_window"],
        "packet_hash": packet["packet_hash"],
        "teams": metadata["teams"],
        "game_metadata": {
            k: v for k, v in metadata.items() if k != "teams" and v is not None
        },
        "market_scopes_offered": scopes,
        "eligible_contracts": packet["counts"]["eligible"],
        "families": {
            family: _family_block(family, records)
            for family, records in sorted(by_family.items())
        },
    }


def load_catalog(catalog_dir: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    index_path = catalog_dir / "cfb_market_catalog.json"
    if not index_path.exists():
        raise FileNotFoundError(
            f"no catalog index at {index_path}. Run scripts/build_kalshi_cfb_catalog.py first."
        )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    details: dict[str, dict[str, Any]] = {}
    for entry in index.get("games") or []:
        relative = entry.get("markets_file")
        if not relative:
            continue
        path = catalog_dir / relative
        if not path.exists():
            continue
        details[str(entry.get("game_key"))] = json.loads(path.read_text(encoding="utf-8"))
    return index, details


def build_slate(
    catalog_dir: Path,
    config: DispositionConfig,
    *,
    tz_name: str = "America/New_York",
    slate_date: str | None = None,
    include_started_games: bool = False,
) -> SlateBuild:
    index, details = load_catalog(catalog_dir)
    capture = index.get("capture") or {}

    seen_tickers: set[str] = set()
    packets: list[dict[str, Any]] = []
    games_skipped: list[dict[str, Any]] = []

    entries = sorted(
        index.get("games") or [],
        key=lambda g: (str(g.get("kickoff") or "9999"), str(g.get("game_key"))),
    )

    contracts_discovered = 0
    contracts_eligible = 0
    exclusion_totals: dict[str, int] = {}
    family_totals: dict[str, int] = {}

    for entry in entries:
        game_key = str(entry.get("game_key"))
        detail = details.get(game_key)
        if detail is None:
            games_skipped.append(
                {
                    "game_key": game_key,
                    "reason": "catalog index names a detail file that is missing on disk",
                }
            )
            continue

        kickoff = parse_timestamp(entry.get("kickoff") or detail.get("kickoff"))
        if slate_date is not None:
            window_date = _local_date(kickoff, tz_name)
            if window_date != slate_date:
                continue

        packet = build_packet(entry, detail, config, seen_tickers, tz_name)
        contracts_discovered += packet["counts"]["discovered"]
        contracts_eligible += packet["counts"]["eligible"]
        for status, count in packet["exclusions"].items():
            exclusion_totals[status] = exclusion_totals.get(status, 0) + count
        for family, bucket in packet["market_families"].items():
            family_totals[family] = family_totals.get(family, 0) + bucket["contracts"]

        if packet["counts"]["eligible"] == 0 and not include_started_games:
            # Still counted above, still published in `games_without_eligible_contracts`
            # -- the reconciliation is not allowed to lose it.
            games_skipped.append(
                {
                    "game_key": game_key,
                    "reason": "no mechanically eligible contracts",
                    "exclusions": packet["exclusions"],
                    "discovered": packet["counts"]["discovered"],
                }
            )
            continue
        packets.append(packet)

    excluded_total = sum(exclusion_totals.values())
    balanced = contracts_discovered == contracts_eligible + excluded_total

    season_level_events = index.get("season_level_events") or []

    slate = {
        "schema_version": EXECUTION_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "as_of": config.as_of.isoformat(),
        "slate_date": slate_date,
        "timezone": tz_name,
        "source": {
            "catalog_dir": str(catalog_dir),
            "captured_at": capture.get("captured_at"),
            "content_fingerprint": capture.get("content_fingerprint"),
            "capture_age_seconds": (
                round(config.capture_age_seconds) if config.capture_age_seconds is not None else None
            ),
            "capture_complete": (index.get("completeness") or {}).get("capture_complete"),
        },
        "fee_disclosure": FEE_DISCLOSURE,
        "config": {
            "max_capture_age_minutes": config.max_capture_age_minutes,
            "max_quote_age_minutes": config.max_quote_age_minutes,
            "require_pregame": config.require_pregame,
            "min_seconds_before_kickoff": config.min_seconds_before_kickoff,
        },
        "reconciliation": {
            "contracts_discovered": contracts_discovered,
            "contracts_eligible": contracts_eligible,
            "contracts_excluded": excluded_total,
            "exclusions_by_status": dict(sorted(exclusion_totals.items())),
            "balanced": balanced,
            "unaccounted_contracts": contracts_discovered - contracts_eligible - excluded_total,
            "games_in_scope": len(packets) + len(games_skipped),
            "games_published": len(packets),
            "games_without_eligible_contracts": len(games_skipped),
            "season_level_events_excluded": len(season_level_events),
            "season_level_exclusion_reason": (
                "season-level inventory (conference champion, awards, win totals) carries no "
                "physical game and is not part of the per-game execution universe"
            ),
        },
        "market_families_discovered": dict(sorted(family_totals.items())),
        "games_without_eligible_contracts": games_skipped,
        "games": packets,
    }
    return SlateBuild(slate=slate, packets=packets)


def _local_date(moment: datetime | None, tz_name: str) -> str | None:
    if moment is None:
        return None
    from zoneinfo import ZoneInfo

    try:
        zone = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 - tzdata absent
        return moment.date().isoformat()
    return moment.astimezone(zone).date().isoformat()
