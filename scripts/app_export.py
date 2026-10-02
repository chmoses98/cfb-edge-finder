#!/usr/bin/env python3
"""Export the Edge Finder app bundle (``edge_finder.app.v1``) for college football.

    python scripts/app_export.py --out app/latest [--data-root data/live] [--accounting-dir <checkout>]
                                 [--now <iso-utc>] [--commit-sha X] [--workflow-run-id Y]

WHAT THIS IS. A pure adapter from this repository's CURRENT data to the shared app contract
vendored under ``contract/edge_finder_contract``. It reads two things and invents nothing:

  * the committed Kalshi market catalog (``data/live/cfb_market_catalog.json`` plus one
    ``data/live/games/<game_key>.json`` per physical game) -> events, participants, markets;
  * a read-only checkout of the ``accounting-data`` branch (``wagers/<season>.jsonl``,
    ``settlements/<season>.jsonl``, ``settlement_amendments/<season>.jsonl``) -> wagers,
    settlements, performance.

THE CFB MODEL IS RETIRED (docs/MODEL_RETIREMENT_2026.md). ``model_prices.json``, ``theses.json``
and ``recommendations.json`` are therefore EMPTY BY DESIGN, health is built with
``model_required=False`` and ``bet_authority=RESEARCH_ONLY``, and nothing here reads the private
decision store or any candidate artifact. A wager in the ledger was placed by the owner and
recommended by nothing in this repository; the export says so in ``performance.notes``.

No credential of any kind is read. Every timestamp emitted is timezone-aware UTC (the contract
refuses naive input). On any failure the previous payload is left untouched, only ``health.json``
is rewritten with ``export_failed=True``, and the process exits 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "contract"))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from edge_finder_contract import (  # noqa: E402
    board,
    build,
    freshness,
    health,
    ids,
    linkage,
    performance,
    publish,
    timeutil,
)

from cfb_edge_finder.accounting import economics, store  # noqa: E402
from cfb_edge_finder.catalog.classification import classify_market  # noqa: E402
from cfb_edge_finder.catalog.identity import parse_event_ticker  # noqa: E402
from cfb_edge_finder.execution.semantics import build_game_teams  # noqa: E402
from cfb_edge_finder.execution.slate import load_catalog  # noqa: E402

SPORT = "CFB"
SOURCE_REPO = "chmoses98/cfb-edge-finder"
SOURCE_BRANCH = "main"
BET_AUTHORITY = "RESEARCH_ONLY"
MARKET_SOURCE = "kalshi_public_rest_v2"
LEDGER_SOURCE = "cfb_accounting_ledger"
EXPORT_SCOPE = "kalshi_market_catalog+accounting_ledger"

#: The catalog workflow runs every 30 minutes inside the Thu-Sun slate window and every 6 hours
#: otherwise (.github/workflows/kalshi-market-catalog.yml), and it commits ONLY when the market
#: surface changed. A capture is therefore FRESH for 45 minutes and STALE after 6 hours; between
#: the two it is AGING. The export itself is re-run every 30 minutes, so it carries the same rule.
MARKET_THRESHOLDS = freshness.Thresholds(45 * 60, 6 * 60 * 60)
EXPORT_THRESHOLDS = freshness.Thresholds(45 * 60, 6 * 60 * 60)
THRESHOLDS = {"market_data": MARKET_THRESHOLDS, "export": EXPORT_THRESHOLDS}
EXPORT_CADENCE_SECONDS = 30 * 60

NOT_MODEL_EVIDENCE = (
    "ACCOUNTING ONLY. Every wager here was placed by the owner and recommended by nothing in "
    "this repository. This record is evidence about a bankroll and is not evidence about the CFB "
    "model, which is retired."
)

_MARKET_STATUS = {"active": "OPEN", "open": "OPEN", "closed": "CLOSED", "finalized": "SETTLED",
                  "settled": "SETTLED", "determined": "CLOSED", "unopened": "UNOPENED", "initialized": "UNOPENED"}
_DONE_MARKET_STATUSES = {"closed", "finalized", "settled", "determined"}
_RESULTS = {"WON", "LOST", "PUSH", "VOID", "SCALAR"}
_FINAL_MILESTONE = {"final", "complete", "completed", "closed"}
_LIVE_MILESTONE = {"inprogress", "in_progress", "live"}


class ExportFailure(RuntimeError):
    """The bundle could not be built from the inputs."""


# ------------------------------------------------------------------ catalog -> events + markets


def _slot_codes(teams: Any) -> dict[str, str]:
    """slot -> the Kalshi team code Kalshi itself uses in that game's tickers (first by sort)."""
    out: dict[str, str] = {}
    for code, slot in sorted(teams.code_to_slot.items()):
        out.setdefault(slot, code)
    return out


def _slot_uuids(teams: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for uuid, slot in sorted(teams.uuid_to_slot.items()):
        out.setdefault(slot, uuid)
    return out


def _participant(slot: str, name: str, codes: dict[str, str], uuids: dict[str, str]) -> dict:
    code = codes.get(slot)
    uuid = uuids.get(slot)
    source_ids = {"kalshi_football_team": uuid} if uuid else {}
    if code:
        return build.participant(sport=SPORT, participant_type="TEAM", source="kalshi_team_code", source_id=code,
                                 display_name=name, short_name=code, source_ids=source_ids,
                                 metadata={"slot": slot})
    # No contract named this team with a ticker code (a game whose only markets are totals, say):
    # the display name Kalshi prints is the strongest id left, labelled as such.
    return build.participant(sport=SPORT, participant_type="TEAM", source="kalshi_team_name", source_id=name,
                             display_name=name, short_name=None, source_ids=source_ids, metadata={"slot": slot})


def _event_status(game: dict, markets: list[dict], now: str) -> str:
    milestone_status = str((game.get("identity") or {}).get("milestone_status") or "").lower()
    if milestone_status in _LIVE_MILESTONE:
        return "LIVE"
    if milestone_status in _FINAL_MILESTONE:
        return "FINAL"
    statuses = [str(m.get("status") or "").lower() for m in markets]
    if statuses and all(s in _DONE_MARKET_STATUSES for s in statuses):
        return "FINAL"
    kickoff = game.get("kickoff")
    if kickoff and timeutil.parse_ts(kickoff) <= timeutil.parse_ts(now):
        return "LIVE"
    return "SCHEDULED"


def _event_identity(game: dict) -> tuple[str, str, str, str]:
    """(source namespace, source id, start_time_source, start_time_confidence)."""
    identity = game.get("identity") or {}
    milestone_id = identity.get("milestone_id")
    if milestone_id:
        return "kalshi_milestone_id", str(milestone_id), "kalshi_milestone", "SCHEDULED"
    # No milestone named this game: identity comes from the event ticker's game key and the
    # kickoff was taken from the contracts' occurrence time rather than a schedule.
    return "kalshi_game_key", str(game["game_key"]), str(identity.get("source") or "kalshi_event_ticker"), "ESTIMATED"


def _build_event(game: dict, markets: list[dict], teams: Any, captured_at: str, now: str) -> tuple[dict, dict, dict]:
    codes, uuids = _slot_codes(teams), _slot_uuids(teams)
    participants: list[dict] = []
    home_id = away_id = None
    slot_to_participant: dict[str, str] = {}
    if teams.known:
        home = _participant("home", teams.home_name, codes, uuids)
        away = _participant("away", teams.away_name, codes, uuids)
        participants = [away, home]
        home_id, away_id = home["participant_id"], away["participant_id"]
        slot_to_participant = {"home": home_id, "away": away_id}
    identity = game.get("identity") or {}
    source, source_id, start_source, start_confidence = _event_identity(game)
    source_ids = {
        "kalshi_game_key": game.get("game_key"),
        "kalshi_milestone_id": identity.get("milestone_id"),
        "kalshi_main_event_ticker": identity.get("main_game_event_ticker"),
    }
    extensions = {
        "title": game.get("title"),
        "game_key": game.get("game_key"),
        "conference": identity.get("conference"),
        "division": identity.get("division"),
        "season_type": identity.get("season_type"),
        "season_week": identity.get("season_week"),
        "tier": identity.get("tier"),
        "milestone_status": identity.get("milestone_status"),
        "identity_source": identity.get("source"),
        "home_away_source": teams.home_away_source,
        "home_away_confidence": teams.home_away_confidence,
        "market_count": game.get("market_count"),
        "family_distribution": dict(game.get("family_distribution") or {}),
    }
    event = build.event(
        sport=SPORT, source=source, source_id=source_id, start_time_utc=game["kickoff"],
        participants=participants, home_participant=home_id, away_participant=away_id,
        league=identity.get("league"), season=identity.get("season_year"), competition=identity.get("division"),
        status=_event_status(game, markets, now), start_time_source=start_source,
        start_time_confidence=start_confidence, source_ids=source_ids,
        schedule_updated_at=captured_at, last_updated_at=captured_at, extensions=extensions,
    )
    teams_info = {"home_name": teams.home_name, "away_name": teams.away_name,
                  "home_away_source": teams.home_away_source, "home_away_confidence": teams.home_away_confidence}
    return event, slot_to_participant, teams_info


def _market_participant(market: dict, slots: dict, teams_uuid_to_slot: dict, teams_code_to_slot: dict) -> str | None:
    custom = market.get("custom_strike")
    if isinstance(custom, dict) and custom.get("football_team"):
        slot = teams_uuid_to_slot.get(str(custom["football_team"]))
        if slot:
            return slots.get(slot)
    ticker, event_ticker = str(market.get("market_ticker") or ""), str(market.get("event_ticker") or "")
    suffix = ticker[len(event_ticker) + 1:] if event_ticker and ticker.startswith(event_ticker + "-") else ""
    code = suffix.rstrip("0123456789")
    if code:
        slot = teams_code_to_slot.get(code.upper())
        if slot:
            return slots.get(slot)
    return None


def _market_side(family: str, participant_id: str | None, slots: dict) -> str | None:
    if participant_id:
        if participant_id == slots.get("home"):
            return "HOME"
        if participant_id == slots.get("away"):
            return "AWAY"
        return "PARTICIPANT"
    if "total" in family:
        return "OVER"
    return None


def _build_market(market: dict, event: dict, slots: dict, teams: Any, captured_at: str, markets_file: str) -> dict:
    family = build.normalise_family(market.get("family"))
    participant_id = _market_participant(market, slots, teams.uuid_to_slot, teams.code_to_slot)
    floor = market.get("floor_strike")
    line = floor if "spread" in family else None
    threshold = floor if line is None else None
    fee = market.get("fee") if isinstance(market.get("fee"), dict) else {}
    mechanics = market.get("mechanics") if isinstance(market.get("mechanics"), dict) else {}
    status = _MARKET_STATUS.get(str(market.get("status") or "").lower(), "UNKNOWN")
    # A finalized contract's 0/1 book is the exchange's post-settlement sentinel, not a quote
    # (the catalog flags it `is_sentinel_full_width_book`). Emitting it would make the contract
    # compute a 0.5 "market probability" out of nothing, so the quote fields go out null.
    sentinel = bool(mechanics.get("is_sentinel_full_width_book"))
    quote = (lambda key: None) if sentinel else market.get
    return build.market(
        sport=SPORT, kalshi_ticker=market["market_ticker"], market_family=family,
        yes_description=str(market.get("yes_sub_title") or market.get("title") or market["market_ticker"]),
        source=MARKET_SOURCE, event_id=event["event_id"],
        kalshi_event_ticker=market.get("event_ticker"), kalshi_series_ticker=market.get("series_ticker"),
        market_type=market.get("market_type"), period=market.get("period"), participant_id=participant_id,
        side=_market_side(family, participant_id, slots), line=line, threshold=threshold,
        no_description=market.get("no_sub_title"),
        yes_bid=quote("yes_bid"), yes_ask=quote("yes_ask"), no_bid=quote("no_bid"), no_ask=quote("no_ask"),
        last_price=market.get("last_price"), volume=market.get("volume"),
        open_interest=market.get("open_interest"), market_status=status,
        close_time_utc=market.get("close_time"), captured_at=captured_at,
        raw_market_reference=f"{markets_file}#{market['market_ticker']}",
        extensions={"book_state": mechanics.get("book_state"),
                    "fee_at_yes_ask": fee.get("model_trade_fee_at_yes_ask"),
                    "strike_type": market.get("strike_type"),
                    "sentinel_full_width_book": sentinel},
    )


def build_catalog_documents(data_root: Path, now: str) -> dict[str, Any]:
    index, details = load_catalog(Path(data_root))
    capture = index.get("capture") or {}
    captured_at = timeutil.to_iso(capture.get("captured_at"))
    fingerprint = capture.get("content_fingerprint")
    if not fingerprint:
        raise ExportFailure("catalog capture has no content_fingerprint")
    events: list[dict] = []
    markets: list[dict] = []
    contexts: dict[str, dict] = {}
    game_key_to_event: dict[str, str] = {}
    warnings: list[str] = []
    seen_tickers: set[str] = set()
    games = sorted((g for g in index.get("games") or [] if g.get("game_key")), key=lambda g: str(g["game_key"]))
    for game in games:
        game_key = str(game["game_key"])
        detail = details.get(game_key)
        if detail is None:
            warnings.append(f"game {game_key}: markets file {game.get('markets_file')} missing; "
                            "event exported without markets")
            raw_markets: list[dict] = []
        else:
            raw_markets = [m for m in detail.get("markets") or [] if m.get("market_ticker")]
        raw_markets.sort(key=lambda m: str(m["market_ticker"]))
        teams = build_game_teams(game.get("title"), raw_markets)
        event, slots, teams_info = _build_event(game, raw_markets, teams, captured_at, now)
        events.append(event)
        game_key_to_event[game_key] = event["event_id"]
        for raw in raw_markets:
            ticker = str(raw["market_ticker"]).upper()
            if ticker in seen_tickers:
                warnings.append(f"duplicate market ticker {ticker} skipped")
                continue
            seen_tickers.add(ticker)
            markets.append(_build_market(raw, event, slots, teams, captured_at, str(game.get("markets_file") or "")))
        contexts[event["event_id"]] = {
            "teams": teams_info,
            "identity": dict(game.get("identity") or {}),
            "completeness": game.get("completeness"),
            "family_distribution": dict(game.get("family_distribution") or {}),
            "catalog_events": [e.get("event_ticker") for e in (game.get("events") or []) if isinstance(e, dict)],
        }
    completeness = index.get("completeness") or {}
    if completeness and completeness.get("capture_complete") is False:
        warnings.append(f"catalog capture incomplete: {completeness.get('games_incomplete_count')} game(s) incomplete")
    unknown = sum(1 for m in markets if m["market_family"] == "unknown")
    if unknown:
        warnings.append(f"{unknown} market(s) carry family 'unknown' (kept, not dropped)")
    return {"events": events, "markets": markets, "contexts": contexts, "captured_at": captured_at,
            "fingerprint": fingerprint, "game_key_to_event": game_key_to_event, "warnings": warnings}


# ------------------------------------------------------------------ ledger -> wagers + settlements


def _seasons(accounting_dir: Path) -> list[int]:
    found: set[int] = set()
    for sub in (store.WAGERS_SUBDIR, store.SETTLEMENTS_SUBDIR):
        for path in (accounting_dir / sub).glob("*.jsonl"):
            if path.stem.isdigit():
                found.add(int(path.stem))
    return sorted(found)


def load_ledger(accounting_dir: Path | None) -> dict[str, Any]:
    """Raw rows per season, settlements in their CANONICAL (amended) view."""
    out = {"wagers": [], "settlements": [], "seasons": [], "present": False, "digest": None}
    if accounting_dir is None:
        return out
    accounting_dir = Path(accounting_dir)
    if not accounting_dir.is_dir():
        return out
    out["present"] = True
    digest = hashlib.sha256()
    for season in _seasons(accounting_dir):
        for path in (store.ledger_path(accounting_dir, season), store.settlement_ledger_path(accounting_dir, season),
                     store.amendments_ledger_path(accounting_dir, season)):
            digest.update(f"{path.name}:{path.stat().st_size if path.exists() else -1}:".encode())
            digest.update(path.read_bytes() if path.exists() else b"")
        wagers = store.read_rows(store.ledger_path(accounting_dir, season))
        settlements = store.read_rows(store.settlement_ledger_path(accounting_dir, season))
        amendments = store.read_rows(store.amendments_ledger_path(accounting_dir, season))
        out["wagers"].extend(wagers)
        out["settlements"].extend(economics.apply_amendments(settlements, amendments))
        out["seasons"].append(season)
    out["digest"] = digest.hexdigest()
    return out


def _game_key_of(ticker: str) -> str | None:
    parts = parse_event_ticker(ticker.rsplit("-", 1)[0]) if ticker.count("-") >= 2 else None
    return parts.game_key if parts else None


def _result(value: Any) -> str:
    text = str(value or "").upper()
    return text if text in _RESULTS else "UNKNOWN"


def _stub(ticker: str, event_id: str | None, settled: bool) -> dict:
    series = ticker.split("-", 1)[0]
    classification = classify_market(series, None, None)
    family = getattr(classification, "family", "unknown")
    return build.market_stub(sport=SPORT, kalshi_ticker=ticker, market_family=str(family), event_id=event_id,
                             source=LEDGER_SOURCE, market_status="SETTLED" if settled else "UNKNOWN")


def build_ledger_documents(ledger: dict, markets: list[dict], game_key_to_event: dict[str, str]) -> dict[str, Any]:
    by_market = {m["market_id"]: m for m in markets}
    settlement_by_key = {s.get("source_bet_key"): s for s in ledger["settlements"] if s.get("source_bet_key")}
    wagers: list[dict] = []
    settlements: list[dict] = []
    stubs: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()
    rows = sorted(ledger["wagers"], key=lambda r: (str(r.get("executed_at") or ""),
                                                   str(r.get("source_bet_key") or r.get("wager_id") or "")))
    for row in rows:
        ticker = str(row.get("market_ticker") or "").strip().upper()
        if not ticker:
            warnings.append(f"wager {row.get('wager_id')} has no market ticker; skipped")
            continue
        key = row.get("source_bet_key") or None
        native_id = row.get("wager_id") or None
        if key is None and native_id is None:
            warnings.append(f"wager on {ticker} has neither source_bet_key nor wager_id; skipped")
            continue
        game_key = _game_key_of(ticker)
        event_id = game_key_to_event.get(game_key) if game_key else None
        settlement_row = settlement_by_key.get(key) if key else None
        if settlement_row is None and row.get("settlement_status") == "SETTLED":
            settlement_row = row  # a wager carrying its own settlement fields (never from the router)
        selection = str(row.get("side") or "").upper()
        if selection not in ("YES", "NO"):
            warnings.append(f"wager {native_id or key} has side {row.get('side')!r}; skipped")
            continue
        wager = build.wager(
            sport=SPORT, kalshi_ticker=ticker, selection=selection, contracts=row.get("contracts") or 0,
            stake=row.get("stake") or 0, average_price=row.get("execution_price"), placed_at=row["executed_at"],
            source="KALSHI_ROUTER" if key else "OTHER", destination_repo=SOURCE_REPO, source_bet_key=key,
            native_id=native_id, event_id=event_id, side=None, fees=row.get("fees_paid"),
            settlement_status="PENDING",
            source_ids={"cfb_wager_id": native_id, "import_batch_id": row.get("import_batch_id"),
                        "kalshi_game_key": game_key},
            extensions={"entry_method": row.get("entry_method"), "game_date": row.get("game_date"),
                        "season": row.get("season"), "week": row.get("week"), "venue": row.get("venue"),
                        "fees_are_estimated": row.get("fees_are_estimated"),
                        "schema_version": row.get("schema_version")},
        )
        if wager["wager_id"] in seen:
            warnings.append(f"duplicate wager identity for {native_id or key}; later row skipped")
            continue
        seen.add(wager["wager_id"])
        settled = settlement_row is not None
        if wager["market_id"] not in by_market:
            stub = _stub(ticker, event_id, settled)
            by_market[stub["market_id"]] = stub
            stubs.append(stub)
        if settled:
            result = _result(settlement_row.get("result"))
            winning_side = None
            if result == "WON":
                winning_side = selection
            elif result == "LOST":
                winning_side = "NO" if selection == "YES" else "YES"
            refusals = list(settlement_row.get("refusals") or [])
            if result == "UNKNOWN" and "result_absent" not in refusals:
                refusals.append("result_absent")
            settlement = build.settlement(
                wager_id=wager["wager_id"], market_id=wager["market_id"], result=result,
                settled_at=settlement_row.get("settled_at") or row["executed_at"], source="kalshi_router",
                verification_status="EXCHANGE_CONFIRMED", winning_side=winning_side,
                gross_payout=settlement_row.get("gross_return"), fees=None,
                net_pnl=settlement_row.get("net_profit_loss"), refusals=refusals,
                source_ids={"cfb_settlement_id": settlement_row.get("settlement_id"), "source_bet_key": key,
                            "canonical_amendment_id": settlement_row.get("canonical_amendment_id")},
                extensions={"economics_version": settlement_row.get("economics_version"),
                            "as_filed_net_profit_loss": settlement_row.get("as_filed_net_profit_loss"),
                            "as_filed_gross_return": settlement_row.get("as_filed_gross_return"),
                            "ledger_settlement_status": settlement_row.get("settlement_status")},
            )
            settlements.append(settlement)
            wager["settlement_id"] = settlement["settlement_id"]
            wager["settlement_status"] = "SETTLED"
            wager["payout"] = settlement["gross_payout"]
            wager["profit_loss"] = settlement["net_pnl"]
        wagers.append(linkage.apply_links(wager, [], [], markets))
    off_board = sum(1 for w in wagers if w.get("event_id") is None)
    if off_board:
        warnings.append(f"{off_board} wager(s) reference games no longer on the catalog board (market stubs emitted)")
    stamps = [w["placed_at"] for w in wagers]
    settled_stamps = [s["settled_at"] for s in settlements]
    return {"wagers": wagers, "settlements": settlements, "stubs": stubs, "warnings": warnings,
            "router_as_of": max(stamps) if stamps else None,
            "settlement_as_of": max(settled_stamps) if settled_stamps else None}


# ------------------------------------------------------------------ the bundle


def build_bundle(*, data_root: Path, accounting_dir: Path | None, now: str, commit_sha: str | None,
                 workflow_run_id: str | None) -> dict[str, Any]:
    catalog = build_catalog_documents(data_root, now)
    ledger = load_ledger(accounting_dir)
    ledger_docs = build_ledger_documents(ledger, catalog["markets"], catalog["game_key_to_event"])
    warnings = list(catalog["warnings"]) + list(ledger_docs["warnings"])
    if not ledger["present"]:
        warnings.append("accounting-data checkout not supplied (--accounting-dir); wagers and settlements are empty")
    events, markets = catalog["events"], catalog["markets"] + ledger_docs["stubs"]
    wagers, settlements = ledger_docs["wagers"], ledger_docs["settlements"]
    captured_at = catalog["captured_at"]

    run = build.run(
        sport=SPORT, repo=SOURCE_REPO, completed_at=now, scope=EXPORT_SCOPE, status="SUCCESS",
        native_run_id=catalog["fingerprint"], commit_sha=commit_sha, workflow_run_id=workflow_run_id,
        model_version=None, events_requested=len(events), events_processed=len(events),
        markets_discovered=len(catalog["markets"]), markets_priced=0, recommendations_created=0,
        data_sources=[f"{MARKET_SOURCE} via data/live/cfb_market_catalog.json",
                      "accounting-data branch (wagers, settlements, settlement_amendments)"],
        input_freshness={"kalshi": captured_at, "schedule": captured_at,
                         "router": ledger_docs["router_as_of"], "settlement": ledger_docs["settlement_as_of"]},
        warnings=warnings, errors=[],
        source_ids={"catalog_content_fingerprint": catalog["fingerprint"],
                    "ledger_seasons": ",".join(str(s) for s in ledger["seasons"]) or None},
    )
    run_id = run["run_id"]
    health_doc = health.build_health(
        sport=SPORT, run_id=run_id, bet_authority=BET_AUTHORITY, last_market_capture=captured_at,
        last_model_generated=None, last_successful_run=now, payload_run_id=run_id, payload_available=True,
        export_failed=False, commit_sha=commit_sha, next_scheduled_run=timeutil.plus(now, EXPORT_CADENCE_SECONDS),
        router_as_of=ledger_docs["router_as_of"], settlement_as_of=ledger_docs["settlement_as_of"],
        model_required=False, router_applicable=ledger["present"], settlement_applicable=ledger["present"],
        thresholds=THRESHOLDS, warnings=warnings, errors=[], now=now, generated_at=now,
    )
    # What a committed export is worth re-committing for: the inputs (catalog fingerprint + ledger
    # bytes) and the time-derived judgements a reader cannot recompute from the payload alone.
    state_material = "|".join([
        str(catalog["fingerprint"]), str(ledger["digest"]), health_doc["overall_status"],
        health_doc["freshness_status"], health_doc["market_data_status"], str(commit_sha),
        ",".join(f"{e['event_id']}={e['status']}" for e in events),
    ])
    state_key = hashlib.sha256(state_material.encode("utf-8")).hexdigest()
    run["source_ids"]["ledger_digest"] = ledger["digest"]
    run["source_ids"]["export_state_key"] = state_key
    collections = {
        "events": build.collection("events", SPORT, run_id, now, events),
        "markets": build.collection("markets", SPORT, run_id, now, markets),
        "model_prices": build.collection("model_prices", SPORT, run_id, now, []),
        "recommendations": build.collection("recommendations", SPORT, run_id, now, []),
        "theses": build.collection("theses", SPORT, run_id, now, []),
        "wagers": build.collection("wagers", SPORT, run_id, now, wagers),
        "settlements": build.collection("settlements", SPORT, run_id, now, settlements),
        "runs": build.collection("runs", SPORT, run_id, now, [run]),
    }
    board_doc = board.build_board(sport=SPORT, run_id=run_id, generated_at=now, events=events, markets=markets,
                                  model_prices=[], recommendations=[], wagers=wagers, health=health_doc,
                                  thresholds=THRESHOLDS, now=now)
    freshness_by_event = {row["event_id"]: row["data_freshness"] for row in board_doc["items"]}
    perf = performance.build_performance(
        sport=SPORT, run_id=run_id, generated_at=now, wagers=wagers, settlements=settlements, markets=markets,
        recommendations=[], bankroll_history=None, bankroll_basis=None, clv_values=None,
        notes=[NOT_MODEL_EVIDENCE,
               "net_pnl is the ledger's canonical figure (settlement_amendments applied); a settlement the "
               "ledger refuses to price carries refusals and a null net_pnl, never an estimate."],
    )
    documents: dict[str, dict] = {**collections, "board": board_doc, "performance": perf}
    markets_by_event: dict[str, list[dict]] = {}
    for m in markets:
        if m.get("event_id"):
            markets_by_event.setdefault(m["event_id"], []).append(m)
    for event in events:
        eid = event["event_id"]
        documents[f"event_detail/{eid}"] = board.build_event_detail(
            sport=SPORT, run_id=run_id, generated_at=now, event=event, markets=markets_by_event.get(eid, []),
            model_prices=[], recommendations=[], theses=[], wagers=wagers, settlements=settlements,
            context=catalog["contexts"].get(eid, {}), price_history=[],
            data_freshness=freshness_by_event.get(eid, "UNKNOWN"),
        )
    manifest_freshness = {
        "kalshi": {"as_of": captured_at,
                   "status": freshness.status_for(captured_at, now=now, thresholds=MARKET_THRESHOLDS)},
        "router": {"as_of": ledger_docs["router_as_of"],
                   "status": freshness.status_for(ledger_docs["router_as_of"], component="router", now=now)},
        "settlement": {"as_of": ledger_docs["settlement_as_of"],
                       "status": freshness.status_for(ledger_docs["settlement_as_of"], component="settlement",
                                                      now=now)},
    }
    return {"run_id": run_id, "documents": documents, "health": health_doc, "warnings": warnings,
            "state_key": state_key, "freshness": manifest_freshness,
            "status": "SUCCESS" if ledger["present"] else "PARTIAL",
            "counts": {"events": len(events), "markets": len(markets), "market_stubs": len(ledger_docs["stubs"]),
                       "wagers": len(wagers), "settlements": len(settlements)}}


def _failure_health(root: Path, now: str, commit_sha: str | None, error: str) -> dict:
    manifest = publish.read_manifest(root)
    previous_capture = None
    previous_run = None
    payload_run_id = None
    if manifest:
        payload_run_id = manifest.get("run_id")
        previous_run = manifest.get("generated_at")
        previous_capture = ((manifest.get("freshness") or {}).get("kalshi") or {}).get("as_of")
    return health.build_health(
        sport=SPORT, run_id=payload_run_id or ids.run_id(SPORT, SOURCE_REPO, None, generated_at=now),
        bet_authority=BET_AUTHORITY, last_market_capture=previous_capture, last_model_generated=None,
        last_successful_run=previous_run, payload_run_id=payload_run_id, payload_available=manifest is not None,
        export_failed=True, commit_sha=commit_sha, model_required=False, thresholds=THRESHOLDS,
        warnings=[], errors=[error], now=now, generated_at=now,
    )


def published_state_key(root: Path) -> str | None:
    """The export_state_key of the run already published at ``root``, or None."""
    path = Path(root) / "runs.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8")).get("items") or []
    except (OSError, ValueError):
        return None
    return str((items[0].get("source_ids") or {}).get("export_state_key") or "") or None if items else None


def export(*, out: Path, data_root: Path, accounting_dir: Path | None, now: str | None = None,
           commit_sha: str | None = None, workflow_run_id: str | None = None,
           skip_unchanged: bool = False) -> dict:
    """Build and publish. Returns the manifest (``{"skipped": True}`` when ``skip_unchanged`` found the
    published tree already carries this state). Raises on failure AFTER writing health.json."""
    out = Path(out)
    now_iso = timeutil.to_iso(now or timeutil.now_utc())
    try:
        bundle = build_bundle(data_root=Path(data_root), accounting_dir=accounting_dir, now=now_iso,
                              commit_sha=commit_sha, workflow_run_id=workflow_run_id)
        if skip_unchanged and published_state_key(out) == bundle["state_key"] and not publish.verify_published(out):
            return {"skipped": True, "state_key": bundle["state_key"], "_counts": bundle["counts"]}
        manifest = publish.publish(
            root=out, sport=SPORT, run_id=bundle["run_id"], generated_at=now_iso, documents=bundle["documents"],
            source_repo=SOURCE_REPO, source_branch=SOURCE_BRANCH, commit_sha=commit_sha, model_version=None,
            status=bundle["status"], freshness=bundle["freshness"], warnings=bundle["warnings"],
            health=bundle["health"],
        )
    except Exception as exc:  # noqa: BLE001 - any failure must leave health behind and the payload alone
        message = f"{type(exc).__name__}: {exc}"
        publish.write_health_only(out, _failure_health(out, now_iso, commit_sha, message))
        raise ExportFailure(message) from exc
    manifest["_counts"] = bundle["counts"]
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="the app root to publish into (app/latest)")
    parser.add_argument("--data-root", default=os.path.join(REPO_ROOT, "data", "live"),
                        help="directory holding cfb_market_catalog.json and games/ (default: data/live)")
    parser.add_argument("--accounting-dir", default=None,
                        help="a read-only checkout of the accounting-data branch; absent => no wagers")
    parser.add_argument("--now", default=None, help="ISO-8601 UTC timestamp to export 'as of' (tests, determinism)")
    parser.add_argument("--commit-sha", default=None)
    parser.add_argument("--workflow-run-id", default=None)
    parser.add_argument("--skip-unchanged", action="store_true",
                        help="publish nothing when the tree at --out already carries this state (quiet workflow)")
    args = parser.parse_args(argv)
    try:
        manifest = export(out=Path(args.out), data_root=Path(args.data_root),
                          accounting_dir=Path(args.accounting_dir) if args.accounting_dir else None,
                          now=args.now, commit_sha=args.commit_sha, workflow_run_id=args.workflow_run_id,
                          skip_unchanged=args.skip_unchanged)
    except ExportFailure as exc:
        print(f"app export FAILED; health.json written, payload untouched: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    counts = manifest.pop("_counts")
    if manifest.get("skipped"):
        print(json.dumps({"skipped": True, "reason": "published tree already carries this state", **counts},
                         sort_keys=True))
        return 0
    print(json.dumps({"run_id": manifest["run_id"], "generated_at": manifest["generated_at"],
                      "status": manifest["status"], **counts, "warnings": len(manifest["warnings"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
