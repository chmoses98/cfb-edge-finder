#!/usr/bin/env python3
"""Build the per-game RUN CFB research packets under ``data/research_packets/``.

    python scripts/build_research_packets.py                       # every game inside the horizon
    python scripts/build_research_packets.py --game 26NOV06UNMNEV --game 26NOV06TCUARIZ
    python scripts/build_research_packets.py --markets-only        # re-price markets.json only (catalog workflow)

Reads: the committed ESPN game log (``data/football/<season>``), the committed Kalshi execution index
(``data/live/execution``), the historical reference (``data/research_packets/reference``) and a handful of keyless ESPN
lookups (venue/weather/predictor per game, roster/coach per team; cached under ``sources/``). Writes ONLY under the
output directory. It has no credentials, reads no order or portfolio endpoint, and cannot place an order.

Idempotent: a file whose content (ignoring the build timestamp) is unchanged is not rewritten, so an unchanged slate
commits nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import script_engine as se  # noqa: E402  (identity + football-packet wiring shared with the Script Engine)

from cfb_edge_finder.research_packet import (  # noqa: E402
    assemble,
    espn_extras,
    pricing,  # noqa: E402
)
from cfb_edge_finder.research_packet import history as hist  # noqa: E402
from cfb_edge_finder.research_packet import markets as mk  # noqa: E402
from cfb_edge_finder.scripting.football import LeagueFitCache, build_content  # noqa: E402
from cfb_edge_finder.scripting.gamelog import iso_utc, parse_utc  # noqa: E402

INDEX_SCHEMA = "cfb_research_index/1.0.0"
MARKETS_PART_BYTES = 190_000
EXTRAS_MAX_AGE = timedelta(hours=3)
ROSTER_MAX_AGE = timedelta(hours=12)


def _dump(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def _strip_build(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _strip_build(v) for k, v in payload.items() if k != "built_at"}
    if isinstance(payload, list):
        return [_strip_build(v) for v in payload]
    return payload


def write_if_changed(path: Path, payload: dict[str, Any], built_at: str) -> bool:
    """Write ``payload`` unless the file already holds the same content apart from its ``built_at`` stamp."""
    if path.exists():
        try:
            old = json.loads(path.read_text())
        except ValueError:
            old = None
        if old is not None and _strip_build(old) == _strip_build(payload):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dump(_with_built_at(payload, built_at)))
    return True


def _with_built_at(payload: dict[str, Any], built_at: str) -> dict[str, Any]:
    out = dict(payload)
    out["built_at"] = built_at
    return out


def load_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text()) if path.exists() else default


def load_execution(
    catalog_dir: Path, row: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    """The game's quote parts, event shards (by event ticker) and manifest, as the catalog workflow wrote them."""
    quotes = [json.loads((ROOT / p).read_text()) for p in row["quote_files"]]
    manifest = json.loads((ROOT / row["manifest"]).read_text())
    shards: dict[str, dict[str, Any]] = {}
    for entry in manifest.get("detail_files") or []:
        part = json.loads((ROOT / entry["path"]).read_text())
        merged = shards.setdefault(entry["event_ticker"], {"markets": []})
        merged["markets"].extend(part.get("markets") or [])
    return quotes, shards, manifest


def split_markets(markets: dict[str, Any]) -> list[dict[str, Any]]:
    """One payload, or several when the contract list would exceed the shard budget. Part 1 carries every header."""
    whole = _dump(markets)
    if len(whole.encode()) <= MARKETS_PART_BYTES:
        return [markets]
    contracts = markets["contracts"]
    header_bytes = len(_dump({**markets, "contracts": []}).encode())
    small_header = len(
        _dump(
            {
                "schema_version": markets["schema_version"],
                "game_key": markets["game_key"],
                "price_provenance": markets["price_provenance"],
            }
        ).encode()
    )
    chunks: list[list[dict[str, Any]]] = [[]]
    used = header_bytes
    for c in contracts:
        size = len(_dump(c).encode())
        if chunks[-1] and used + size > MARKETS_PART_BYTES:
            chunks.append([])
            used = small_header
        chunks[-1].append(c)
        used += size
    parts = []
    for i, chunk in enumerate(chunks, start=1):
        if i == 1:
            body = {**markets, "contracts": chunk}
        else:
            body = {
                "schema_version": markets["schema_version"],
                "game_key": markets["game_key"],
                "contracts": chunk,
                "price_provenance": markets["price_provenance"],
            }
        body["part"], body["parts"] = i, len(chunks)
        body["contracts_in_part"] = len(chunk)
        parts.append(body)
    return parts


def resolve_identity(se_mod: Any, entry: dict[str, Any], schedule: list[dict[str, Any]]):
    kickoff = entry.get("kickoff")
    request, event, hk, ak = se_mod.resolve_request(entry["game_key"], entry.get("title"), kickoff, schedule)
    check = se_mod.identity_check(request, event, hk, ak)
    if check["status"] == "FAIL":
        coded, kalshi_home_code = se_mod.match_by_codes(request, schedule)
        if coded is not None:
            espn_home = next(c for c in coded["competitors"] if c["home_away"] == "home")
            swapped = str(espn_home.get("abbreviation") or "").upper() != kalshi_home_code
            event = coded
            check = {
                "status": "RESOLVED",
                "orientation_swapped": swapped,
                "espn_event_id": coded["id"],
                "method": "team_codes",
                "reason": "matched by the team codes in the Kalshi game key against ESPN abbreviations",
            }
    return request, event, check


def refresh_cache(
    cache: dict[str, Any], key: str, fetch, max_age: timedelta, now: datetime, enabled: bool
) -> dict[str, Any]:
    cur = cache.get(key)
    if cur and cur.get("status") == "OBSERVED":
        seen = cur.get("observed_at")
        if seen and now - parse_utc(seen) < max_age:
            return cur
    if not enabled:
        return cur or {"status": "UNAVAILABLE", "reason": "not fetched in this run"}
    fresh = fetch(key)
    if fresh.get("status") == "OBSERVED" or not cur:
        cache[key] = fresh
        return fresh
    return cur  # keep the last good observation (its own observed_at tells the reader how old it is)


def reference_tables(out_dir: Path, series_by_multiplier: dict[float, str], built_at: str) -> None:
    """Bet-up-to ceilings by lower-bound probability and fee multiplier, so the arithmetic can be done by lookup."""
    rows = {}
    for mult, series in sorted(series_by_multiplier.items()):
        rows[f"{mult:g}"] = {
            "example_series": series,
            "bet_up_to_cents_by_probability_lower_bound_pct": {
                str(p): (None if (c := pricing.bet_up_to_price(p / 100, series)) is None else round(c * 100))
                for p in range(1, 100)
            },
        }
    payload = {
        "schema_version": "cfb_bet_up_to_table/1.0.0",
        "unit_dollars": pricing.UNIT_DOLLARS,
        "meaning": (
            "the highest whole-cent price at which a lower-bound probability (the probability that the side you "
                "BUY wins) still "
            "covers the all-in cost of a one-unit order. Use YOUR lower bound. null = no price qualifies. required "
                "margin = 0"
        ),
        "tables_by_fee_multiplier": rows,
    }
    write_if_changed(out_dir / "reference/bet_up_to_table.json", payload, built_at)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--football-dir", type=Path, default=ROOT / "data/football/2026")
    ap.add_argument("--catalog-dir", type=Path, default=ROOT / "data/live")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "data/research_packets")
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--horizon-hours", type=float, default=168.0)
    ap.add_argument("--as-of", type=str, default=None)
    ap.add_argument("--game", action="append")
    ap.add_argument(
        "--markets-only", action="store_true", help="re-price markets.json from the current quotes; keep football files"
    )
    ap.add_argument("--no-fetch", action="store_true", help="never touch the network; use cached ESPN lookups only")
    args = ap.parse_args(argv)

    now = parse_utc(args.as_of) if args.as_of else datetime.now(UTC)
    built_at = iso_utc(now)
    out = args.out_dir
    index = json.loads((args.catalog_dir / "cfb_execution_index.json").read_text())
    capture = index.get("capture") or {}
    rows, schedule, availability, manifest = se.load_football(args.football_dir)
    ref_path = out / "reference/history_games.jsonl.gz"
    reference_games = hist.load_games(ref_path) if ref_path.exists() else []
    baseline_acc = hist.baseline_accuracy(reference_games)
    unconditional = hist.unconditional(reference_games)

    sources_dir = out / "sources"
    extras_cache = load_json(sources_dir / "event_extras.json", {})
    roster_cache = load_json(sources_dir / "rosters.json", {})
    fetch = not args.no_fetch and not args.markets_only

    horizon = now + timedelta(hours=args.horizon_hours)
    wanted = set(args.game or [])
    entries = [
        g
        for g in sorted(index["games"], key=lambda g: (g["kickoff"], g["game_key"]))
        if (not wanted or g["game_key"] in wanted)
        and (wanted or (now - timedelta(hours=1) <= parse_utc(g["kickoff"]) <= horizon))
    ]
    cache = LeagueFitCache()
    rows_index: list[dict[str, Any]] = []
    series_by_multiplier: dict[float, str] = {}
    written = unchanged = 0

    for entry in entries:
        key = entry["game_key"]
        gdir = out / "games" / key
        quotes, shards, gmanifest = load_execution(args.catalog_dir, entry)
        for shard in shards.values():
            for m in shard["markets"][:1]:
                if m.get("fee_multiplier") is not None and m.get("series_ticker"):
                    series_by_multiplier.setdefault(float(m["fee_multiplier"]), m["series_ticker"])
        usable = bool(entry.get("usable"))

        if args.markets_only and (gdir / "packet.json").exists():
            old = load_json(gdir / "packet.json")
            stored = (load_json(gdir / "markets.json", {}).get("price_provenance") or {}).get("game_fingerprint")
            if stored and stored == entry.get("game_fingerprint"):
                unchanged += 1
                rows_index.append(
                    _index_row(
                        entry, old, _markets_stub(gdir), gdir, [{}] * max(1, len(list(gdir.glob("markets*.json"))))
                    )
                )
                continue
            chosen_cached = None
            comp = load_json(gdir / "distributions.json", {}).get("reference_class") or {}
            markets = mk.build_markets(
                game_key=key, quote_files=quotes, event_files=shards, capture=capture, reference_games=[]
            )
            # markets-only keeps the football packet; frequencies are re-derived from the full builder, so carry them
            # over.
            prev = {c["ticker"]: c.get("ref_freq") for c in _old_contracts(gdir)}
            for c in markets["contracts"]:
                if prev.get(c["ticker"]):
                    c["ref_freq"] = prev[c["ticker"]]
            parts = split_markets(markets)
            _write_market_parts(gdir, parts, built_at)
            _ = (old, chosen_cached, comp)
            rows_index.append(_index_row(entry, old, markets, gdir, parts))
            continue

        request, event, check = resolve_identity(se, entry, schedule)
        content = league = None
        home = away = None
        history = []
        extras = None
        rosters: dict[str, dict[str, Any]] = {}
        avail_view: dict[str, Any] = {}
        if check["status"] != "FAIL" and event is not None:
            packet = se.build_football_packet(request, event, check, schedule, rows, availability, args.season)
            content = build_content(packet, cache)
            league = cache.get(packet.rows, content["football_data_cutoff"])
            from cfb_edge_finder.scripting.gamelog import before

            history = list(before(packet.rows, content["football_data_cutoff"]))
            ident = packet.identity
            home = {"team_id": ident.home_id, "name": ident.home_name}
            away = {"team_id": ident.away_id, "name": ident.away_name}
            extras = refresh_cache(
                extras_cache, ident.event_id, espn_extras.fetch_event_extras, EXTRAS_MAX_AGE, now, fetch
            )
            rosters = {
                "home": refresh_cache(
                    roster_cache, ident.home_id, espn_extras.fetch_roster, ROSTER_MAX_AGE, now, fetch
                ),
                "away": refresh_cache(
                    roster_cache, ident.away_id, espn_extras.fetch_roster, ROSTER_MAX_AGE, now, fetch
                ),
            }
            for side, team in (("home", home), ("away", away)):
                a = availability.get(team["team_id"]) or {}
                items = a.get("items")
                resolved = [i for i in (items or []) if i.get("athlete")]
                avail_view[side] = {
                    "source": "espn.core.injuries via data/football/<season>/availability.json",
                    "status": a.get("status", "NOT_OBSERVED"),
                    "observed_at": a.get("observed_at"),
                    "listed": None if items is None else len(items),
                    "named_entries": len(resolved),
                    "entries": resolved,
                    "quality_note": (
                        "listed rows with no athlete/position/status are unresolved ESPN references: they prove "
                            "something was listed, not who or why"
                        if items and not resolved
                        else None
                    ),
                    "qb_flagged_uncertain": a.get("qb_uncertain"),
                }

        reference_chosen: list[dict[str, Any]] = []
        # LEAKAGE GUARD: only games that finished before this packet's data cutoff may sit in the reference class, and
        # never
        # the game itself. (For a live build this removes nothing; it protects any as-of build.)
        cutoff_iso = (content or {}).get("football_data_cutoff")
        game_refs = hist.eligible_reference(reference_games, cutoff_iso, (event or {}).get("id"))
        if content is not None:
            sb = content["matchup_profile"]["scoring_baseline"]
            if sb.get("home_margin") is not None and sb.get("total_points") is not None:
                _, reference_chosen = hist.comparables(game_refs, sb["home_margin"], sb["total_points"])

        markets = mk.build_markets(
            game_key=key,
            quote_files=quotes,
            event_files=shards,
            capture=capture,
            reference_games=reference_chosen,
            orientation_swapped=bool(check.get("orientation_swapped")),
            team_names={"home": (home or {}).get("name"), "away": (away or {}).get("name")},
        )
        parts = split_markets(markets)
        market_paths = [
            f"data/research_packets/games/{key}/"
            + ("markets.json" if i == 1 else f"markets.part{i:02d}of{len(parts):02d}.json")
            for i in range(1, len(parts) + 1)
        ]
        inputs = {
            "football_log_rows_fingerprint": (content or {}).get("inputs", {}).get("rows_fingerprint"),
            "football_log_fetched_at": manifest.get("fetched_at"),
            "availability_fingerprint": (content or {}).get("inputs", {}).get("availability_fingerprint"),
            "quotes_game_fingerprint": entry.get("game_fingerprint"),
            "quotes_captured_at": capture.get("captured_at"),
            "history_games": len(game_refs),
            "history_through_kickoff_utc": max((g["kickoff_utc"] for g in game_refs), default=None),
            "football_data_cutoff": cutoff_iso,
            "methodology_version": (content or {}).get("methodology_version"),
        }
        files = assemble.build_packet_files(
            game=entry,
            content=content,
            league=league,
            history=history,
            identity=check,
            home=home,
            away=away,
            extras=extras,
            rosters=rosters,
            availability=avail_view,
            reference_games=game_refs,
            unconditional_summary=unconditional,
            baseline_acc=baseline_acc,
            markets=parts[0] if len(parts) == 1 else {**markets, "contract_count": markets["contract_count"]},
            markets_paths=market_paths,
            markets_usable=usable,
            inputs=inputs,
            schedule_events=schedule,
        )
        files.pop("markets.json")
        for name, payload in files.items():
            if write_if_changed(gdir / name, payload, built_at):
                written += 1
            else:
                unchanged += 1
        _write_market_parts(gdir, parts, built_at)
        rows_index.append(_index_row(entry, files["packet.json"], markets, gdir, parts))

    # Sources cache (sorted, compact) and reference tables.
    if not args.markets_only:
        sources_dir.mkdir(parents=True, exist_ok=True)
        (sources_dir / "event_extras.json").write_text(_dump(extras_cache))
        (sources_dir / "rosters.json").write_text(_dump(roster_cache))
    reference_tables(out, series_by_multiplier, built_at)
    _write_indexes(out, rows_index, index, manifest, args, built_at, wanted)
    print(f"research packets: {len(entries)} games, {written} files written, {unchanged} unchanged")
    return 0


def _markets_stub(gdir: Path) -> dict[str, Any]:
    head = load_json(gdir / "markets.json", {})
    return {"contract_count": head.get("contract_count"), "families": head.get("families") or {}}


def _old_contracts(gdir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(gdir.glob("markets*.json")):
        out.extend(load_json(p, {}).get("contracts") or [])
    return out


def _write_market_parts(gdir: Path, parts: list[dict[str, Any]], built_at: str) -> int:
    """Write the market parts (only the ones that changed) and drop stale ones. Returns the number written."""
    names = [
        "markets.json" if i == 1 else f"markets.part{i:02d}of{len(parts):02d}.json" for i in range(1, len(parts) + 1)
    ]
    for stale in gdir.glob("markets*.json"):
        if stale.name not in names:
            stale.unlink()
    return sum(1 for name, body in zip(names, parts, strict=True) if write_if_changed(gdir / name, body, built_at))


def _index_row(
    entry: dict[str, Any], packet: dict[str, Any], markets: dict[str, Any], gdir: Path, parts: list[dict[str, Any]]
) -> dict[str, Any]:
    dq = (packet or {}).get("data_quality") or {}
    key = entry["game_key"]
    return {
        "game_key": key,
        "title": entry.get("title"),
        "kickoff": entry.get("kickoff"),
        "kickoff_et": entry.get("kickoff_et"),
        "slate_date": entry.get("slate_date"),
        "window": entry.get("window"),
        "packet": f"data/research_packets/games/{key}/packet.json",
        "files": {
            "team_profiles": f"data/research_packets/games/{key}/team_profiles.json",
            "schedules": f"data/research_packets/games/{key}/schedules.json",
            "distributions": f"data/research_packets/games/{key}/distributions.json",
            "markets": [
                f"data/research_packets/games/{key}/"
                + ("markets.json" if i == 1 else f"markets.part{i:02d}of{len(parts):02d}.json")
                for i in range(1, len(parts) + 1)
            ],
            "quotes_live_source": (entry.get("quote_files") or [None])[0],
        },
        "contract_count": markets["contract_count"],
        "families": {f: v["contracts"] for f, v in markets["families"].items()},
        "usable_market_menu": entry.get("usable"),
        "football_data_confidence": dq.get("football_data_confidence"),
        "blocking_flags": dq.get("blocking_flags"),
        "flag_codes": sorted({f["code"] for f in dq.get("missing_data_flags") or []}),
        "football_history": "ABSENT"
        if any(f["code"] == "FOOTBALL_HISTORY_ABSENT" for f in dq.get("missing_data_flags") or [])
        else "PRESENT",
        "sections": dq.get("sections"),
        "quotes_game_fingerprint": entry.get("game_fingerprint"),
    }


def _write_indexes(
    out: Path,
    rows: list[dict[str, Any]],
    exec_index: dict[str, Any],
    manifest: dict[str, Any],
    args,
    built_at: str,
    wanted: set[str],
) -> None:
    by_slate: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_slate.setdefault(r["slate_date"], []).append(r)
    header = {
        "schema_version": INDEX_SCHEMA,
        "contains_model_probabilities": False,
        "quotes_captured_at": (exec_index.get("capture") or {}).get("captured_at"),
        "execution_index_fingerprint": (exec_index.get("capture") or {}).get("content_fingerprint"),
        "football_log_fetched_at": manifest.get("fetched_at"),
        "football_log_through": (manifest.get("window") or {}).get("through"),
        "football_games_in_log": manifest.get("games_in_log"),
    }
    slates = []
    for date, rs in sorted(by_slate.items()):
        path = out / "slates" / f"{date}.json"
        merged = {r["game_key"]: r for r in rs}
        if wanted:  # a partial build updates its own games and keeps every other game's row
            for old_row in (load_json(path, {}) or {}).get("games") or []:
                merged.setdefault(old_row["game_key"], old_row)
        rs = list(merged.values())
        by_slate[date] = rs
        payload = {**header, "slate_date": date, "games": sorted(rs, key=lambda r: (r["kickoff"], r["game_key"]))}
        write_if_changed(path, payload, built_at)
        slates.append(
            {
                "slate_date": date,
                "path": f"data/research_packets/slates/{date}.json",
                "games": len(rs),
                "windows": {w: sum(1 for r in rs if r["window"] == w) for w in ("early", "afternoon", "evening")},
            }
        )
    if wanted:  # a partial build never replaces the whole-slate index
        return
    write_if_changed(
        out / "index.json",
        {
            **header,
            "horizon_hours": args.horizon_hours,
            "slates": slates,
            "game_count": len(rows),
            "reading_order": [
                "RUN_CFB.md",
                "this index -> slates[].path -> games[] (kickoff, window, blocking_flags, file paths)",
                "per game: packet.json, then schedules.json / team_profiles.json / distributions.json, then "
                    "markets.json",
                "re-read data/live/execution/games/<game_key>/quotes.json when its game_fingerprint differs from "
                    "markets.json's",
            ],
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
                        "packet",
                        "contract_count",
                        "football_data_confidence",
                        "blocking_flags",
                        "flag_codes",
                        "football_history",
                    )
                }
                for r in rows
            ],
        },
        built_at,
    )


if __name__ == "__main__":
    raise SystemExit(main())
