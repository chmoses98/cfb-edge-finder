"""CFB research conductor: one cycle of primary-window capture, FINAL_PREGAME watchdog, prospective settlement
and research-signals publication. Keyless and read-only toward Kalshi (public GET /markets only).

    python scripts/cfb_research_conductor.py --main-dir <checkout of main data> \
        --ledger-dir <script-ledger data/scripting/ledger> --store-dir <research-signals worktree> \
        [--now <iso-utc>] [--dispatch] [--offline]

`.github/workflows/cfb-research-conductor.yml` runs a cycle every few minutes inside a long-lived job and
dispatches its own successor, because GitHub's cron is not a dependable clock for this repository (measured:
the */30 catalog cron fires every 2-6 h). The cycle itself is idempotent: running it twice at the same moment
writes nothing new.

What one cycle does, in order:

1. CAPTURE   -- reads every open KXNCAAFGAME market once, then records an attempt (and the quotes) for every
                game whose 165/120/90/70-minute slot is due inside PRIMARY_60_180, catching up a missed slot
                while the window is open. A failed read is logged as REQUEST_FAILED, never as "no market".
2. WATCHDOG  -- a game kicking off within 170 minutes that has a V2 claim but no FINAL_PREGAME ledger row
                makes the conductor dispatch the CFB Script Engine (debounced; never while one is running).
                Finished games missing from the game log do the same, so settlement data arrives.
3. SETTLE    -- prospective H1/H2 settlement rows for finished population games (append-only).
4. PUBLISH   -- `signals/cfb_research_signals.json` (cfb_research_signals/1.0.0) and the prospective report,
                rewritten only when their content changed.

Nothing here can place an order, size a position or recommend anything.
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import collect_live_context as names_util  # noqa: E402  (production name matching; no network)

from cfb_edge_finder.control_market.quotes import parse_utc  # noqa: E402
from cfb_edge_finder.control_prospective import (  # noqa: E402
    GAME_WINNER_SERIES,
    SIGNALS_SCHEMA,
    load_decisions,
    load_protocol,
    verify_protocol,
)
from cfb_edge_finder.control_prospective.capture import (  # noqa: E402
    SlateGame,
    capture,
    due_slots,
    markets_by_game,
    normalize_market,
)
from cfb_edge_finder.control_prospective.signals import build_signals, content_key, game_entry  # noqa: E402
from cfb_edge_finder.control_prospective.tracking import (  # noqa: E402
    population,
    population_counts,
    settle,
    summarize,
)
from cfb_edge_finder.scripting.gamelog import TeamGame, read_jsonl  # noqa: E402

SEASON = 2026
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
WATCHDOG_HORIZON_MIN = 170
WATCHDOG_MIN_LEAD_MIN = 12
DISPATCH_DEBOUNCE_MIN = 40
RESULTS_GRACE_HOURS = 4.5
SIGNALS_LOOKBACK_HOURS = 6
SIGNALS_HORIZON_DAYS = 10


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def market_keys(name: str) -> set[str]:
    """Production Kalshi-side team spellings (identical to the frozen market study's matcher input)."""
    if not name:
        return set()
    return names_util._kalshi_team_keys({"home": name, "away": None})["home"] | names_util.name_variants(_fold(name))


def with_name_keys(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for event in events:
        for c in event.get("competitors") or []:
            keys: set[str] = set()
            for name in c.get("names") or []:
                keys |= names_util.name_variants(name) | names_util.name_variants(_fold(name))
            c["name_keys"] = sorted(keys)
    return events


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_gz(path: Path) -> Any:
    try:
        with gzip.open(path, "rb") as fh:
            return json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError):
        return None


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


# --------------------------------------------------------------------------- inputs


class Inputs:
    """Everything one cycle reads from main (a sparse checkout refreshed by the workflow)."""

    def __init__(self, main_dir: Path, season: int) -> None:
        football = main_dir / "data" / "football" / str(season)
        self.catalog = read_json(main_dir / "data" / "live" / "cfb_market_catalog.json", {"games": [], "capture": {}})
        self.catalog_dir = main_dir / "data" / "live"
        self.schedule = with_name_keys(read_json(football / "schedule.json", {"events": []})["events"])
        self.team_rows = read_jsonl_safe(football)
        self.sift_dir = main_dir / "data" / "scripting" / "live" / "sift"
        self.frozen_v2_dir = main_dir / "data" / "scripting" / "live" / "frozen_v2"
        events = read_json(main_dir / "app" / "latest" / "events.json", {"items": []}).get("items") or []
        self.events = {
            (e.get("source_ids") or {}).get("kalshi_game_key") or (e.get("extensions") or {}).get("game_key"): e
            for e in events
        }

    def payload(self, game_key: str) -> dict[str, Any] | None:
        return read_gz(self.sift_dir / f"{game_key}.json.gz")

    def frozen(self, game_key: str) -> dict[str, Any] | None:
        return read_gz(self.frozen_v2_dir / f"{game_key}.json.gz")

    def catalog_markets(self, game: dict[str, Any]) -> list[dict[str, Any]]:
        detail = (
            read_json(self.catalog_dir / str(game.get("markets_file") or ""), {}) if game.get("markets_file") else {}
        )
        # catalog records already carry DOLLARS under the bare keys (unlike the API's legacy cents spelling)
        return [
            {
                "market_ticker": m.get("market_ticker"),
                "event_ticker": m.get("event_ticker") or str(m.get("market_ticker") or "").rsplit("-", 1)[0],
                "team_code": str(m.get("market_ticker") or "").rsplit("-", 1)[-1].upper(),
                "yes_sub_title": m.get("yes_sub_title"),
                "status": m.get("status"),
                "yes_bid": m.get("yes_bid"),
                "yes_ask": m.get("yes_ask"),
                "no_bid": m.get("no_bid"),
                "no_ask": m.get("no_ask"),
            }
            for m in detail.get("markets") or []
            if m.get("family") == "game_moneyline" and not (m.get("mechanics") or {}).get("is_sentinel_full_width_book")
        ]


def read_jsonl_safe(football: Path) -> list[TeamGame]:
    path = football / "team_games.jsonl"
    if not path.exists():
        return []
    return list(read_jsonl(path))


def slate_games(inputs: Inputs, now: str) -> list[tuple[dict[str, Any], SlateGame]]:
    lo = parse_utc(now) - timedelta(hours=SIGNALS_LOOKBACK_HOURS)
    hi = parse_utc(now) + timedelta(days=SIGNALS_HORIZON_DAYS)
    out = []
    for g in inputs.catalog.get("games") or []:
        frozen = inputs.frozen(g["game_key"])
        content = (frozen or {}).get("content") or {}
        kickoff = content.get("kickoff_utc") or g.get("kickoff")
        if not kickoff:
            continue
        if not lo <= parse_utc(kickoff) <= hi:
            continue
        out.append(
            (
                g,
                SlateGame(
                    game_key=g["game_key"],
                    kickoff_utc=str(kickoff).replace("+00:00", "Z"),
                    kickoff_source="espn_schedule" if content.get("kickoff_utc") else "kalshi_catalog",
                    title=g.get("title"),
                    espn_event_id=str(content["event_id"]) if content.get("event_id") else None,
                ),
            )
        )
    out.sort(key=lambda x: (x[1].kickoff_utc, x[1].game_key))
    return out


# --------------------------------------------------------------------------- Kalshi (public, read-only)


def fetch_series_markets(base: str) -> list[dict[str, Any]]:
    from cfb_edge_finder.data.kalshi_client import KalshiClient

    client = KalshiClient(base_url=base)
    out: list[dict[str, Any]] = []
    cursor = None
    for _ in range(10):
        page = client._get(
            "/markets", {"series_ticker": GAME_WINNER_SERIES, "status": "open", "limit": 1000, "cursor": cursor}
        )
        out.extend(page.get("markets") or [])
        cursor = page.get("cursor")
        if not cursor:
            break
    return out


def fetch_event_markets(base: str, event_ticker: str) -> list[dict[str, Any]]:
    from cfb_edge_finder.data.kalshi_client import KalshiClient

    page = KalshiClient(base_url=base)._get("/markets", {"event_ticker": event_ticker, "limit": 100})
    return page.get("markets") or []


# --------------------------------------------------------------------------- watchdog


def script_engine_busy() -> bool:
    try:
        out = subprocess.run(
            ["gh", "run", "list", "--workflow", "script-engine.yml", "--limit", "5", "--json", "status"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout
        return any(
            r.get("status") in ("queued", "in_progress", "waiting", "pending", "requested") for r in json.loads(out)
        )
    except Exception:  # noqa: BLE001 -- unknown state: assume busy, try again next cycle
        return True


def dispatch_script_engine() -> str | None:
    try:
        subprocess.run(
            ["gh", "workflow", "run", "script-engine.yml", "--ref", "main"],
            check=True,
            timeout=60,
            capture_output=True,
            text=True,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def watchdog_needs(
    games: list[tuple[dict[str, Any], SlateGame]], ledger_v2: list[dict[str, Any]], inputs: Inputs, now: str
) -> dict[str, list[str]]:
    final = {r["game_key"] for r in ledger_v2 if r.get("kind") == "FINAL_PREGAME"}
    need_final = []
    for g, sg in games:
        minutes = (parse_utc(sg.kickoff_utc) - parse_utc(now)).total_seconds() / 60
        if WATCHDOG_MIN_LEAD_MIN < minutes <= WATCHDOG_HORIZON_MIN and g["game_key"] not in final:
            if inputs.frozen(g["game_key"]) is not None:  # the engine builds V2 for this game
                need_final.append(g["game_key"])
    in_log = {r.game_id for r in inputs.team_rows}
    need_results = []
    for game_key, row in population(ledger_v2).items():
        age_h = (parse_utc(now) - parse_utc(row["kickoff_utc"])).total_seconds() / 3600
        if RESULTS_GRACE_HOURS <= age_h <= 72 and str(row["event_id"]) not in in_log:
            need_results.append(game_key)
    return {"final_pregame": sorted(need_final), "results": sorted(need_results)}


# --------------------------------------------------------------------------- one cycle


def run_cycle(
    *,
    main_dir: Path,
    ledger_dir: Path,
    store_dir: Path,
    now: str,
    season: int = SEASON,
    fetch: Any = None,
    fetch_event: Any = None,
    dispatch: Any = None,
    busy: Any = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    verify_protocol(load_protocol(ROOT))
    inputs = Inputs(main_dir, season)
    store = store_dir
    season_dir = str(season)
    attempts_path = store / "capture" / season_dir / "attempts.jsonl"
    quotes_path = store / "capture" / season_dir / "quotes.jsonl"
    settle_path = store / "prospective" / season_dir / "settlements.jsonl"
    dispatch_path = store / "conductor" / season_dir / "dispatches.jsonl"
    attempts = read_rows(attempts_path)
    quotes = read_rows(quotes_path)
    ledger_v2 = read_rows(ledger_dir / season_dir / "publications_v2.jsonl")
    games = slate_games(inputs, now)

    # 1. one Kalshi read for the whole series
    read_error = None
    live: dict[str, list[dict[str, Any]]] = {}
    try:
        live = markets_by_game(fetch() if fetch else [])
    except Exception as exc:  # noqa: BLE001 -- a failed read is logged per game as REQUEST_FAILED
        read_error = f"{type(exc).__name__}: {exc}"

    attempted: dict[str, set[str]] = {}
    for a in attempts:
        for s in a.get("slots") or []:
            attempted.setdefault(a["game_key"], set()).add(s)
    new_attempts: list[dict[str, Any]] = []
    new_quotes: list[dict[str, Any]] = []
    for _, sg in games:
        slots = due_slots(sg.kickoff_utc, now, attempted.get(sg.game_key, set()))
        if not slots:
            continue
        markets = None
        error = read_error
        if error is None:
            markets = live.get(sg.game_key)
            if not markets and fetch_event is not None:
                try:  # not in the open list: confirm against the event itself (closed/paused vs absent)
                    markets = [normalize_market(m) for m in fetch_event(sg.event_ticker)]
                except Exception as exc:  # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"
        attempt, rows = capture(sg, slots, now, markets, request_error=error)
        new_attempts.append(attempt)
        new_quotes.extend(rows)
    append_rows(attempts_path, new_attempts)
    append_rows(quotes_path, new_quotes)
    attempts += new_attempts
    quotes += new_quotes

    # 2. watchdog
    needs = watchdog_needs(games, ledger_v2, inputs, now)
    dispatched = None
    if dispatch is not None and (needs["final_pregame"] or needs["results"]):
        last = read_rows(dispatch_path)
        recent = [d for d in last if (parse_utc(now) - parse_utc(d["at"])).total_seconds() < DISPATCH_DEBOUNCE_MIN * 60]
        if not recent and not (busy() if busy else False):
            error = dispatch()
            dispatched = {"at": now, "reason": needs, "error": error, "run_id": run_id}
            append_rows(dispatch_path, [dispatched])

    # 3. settle
    settled = read_rows(settle_path)
    new_settled = settle(
        ledger_v2,
        settled,
        quotes=quotes,
        attempts=attempts,
        schedule=inputs.schedule,
        keys=market_keys,
        team_rows=inputs.team_rows,
        now=now,
    )
    append_rows(settle_path, new_settled)
    settled += new_settled
    summary = summarize(settled, load_decisions(ROOT))
    pop = population_counts(ledger_v2, now)

    # 4. publish
    catalog_at = (inputs.catalog.get("capture") or {}).get("captured_at")
    entries = []
    for g, sg in games:
        if read_error is None:
            latest = [
                {
                    **m,
                    "captured_at": now,
                    "source": "KALSHI_LIVE_READ",
                    "title": sg.title,
                    "kickoff_utc": sg.kickoff_utc,
                    "game_key": sg.game_key,
                }
                for m in live.get(sg.game_key) or []
            ]
            g = {**g, "_market_read_ok": True}
        else:
            latest = [
                {
                    **m,
                    "captured_at": catalog_at,
                    "source": "KALSHI_CATALOG",
                    "title": sg.title,
                    "kickoff_utc": sg.kickoff_utc,
                    "game_key": sg.game_key,
                }
                for m in inputs.catalog_markets(g)
            ]
        entries.append(
            game_entry(
                game={**g, "kickoff": sg.kickoff_utc},
                payload=inputs.payload(sg.game_key),
                frozen=inputs.frozen(sg.game_key),
                event=inputs.events.get(sg.game_key),
                latest_quotes=latest,
                capture_quotes=quotes,
                capture_attempts=attempts,
                schedule=inputs.schedule,
                keys=market_keys,
                now=now,
            )
        )
    sources = {
        "catalog_captured_at": catalog_at,
        "market_read": {"ok": read_error is None, "at": now, "error": read_error, "series": GAME_WINNER_SERIES},
        "conductor_run_id": run_id,
        "ledger_v2_rows": len(ledger_v2),
        "watchdog": {"needs": needs, "dispatched": dispatched},
    }
    doc = build_signals(root=ROOT, now=now, games=entries, summary=summary, population_counts=pop, sources=sources)
    signals_path = store / "signals" / "cfb_research_signals.json"
    previous = read_json(signals_path, None)
    changed = previous is None or previous.get("schema") != SIGNALS_SCHEMA or content_key(previous) != content_key(doc)
    # prices move every few minutes; republish on content change, or at least every 20 minutes for freshness
    stale = previous is None or (parse_utc(now) - parse_utc(previous.get("generated_at") or now)).total_seconds() > 1200
    if changed or stale:
        write_if_changed(signals_path, json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n")
    report = {"generated_at": now, "summary": summary, "population": pop, "capture_health": doc["capture_health"]}
    write_if_changed(
        store / "reports" / season_dir / "prospective_report.json",
        json.dumps({k: v for k, v in report.items() if k != "generated_at"}, indent=1, sort_keys=True) + "\n",
    )
    return {
        "now": now,
        "games": len(games),
        "attempts": len(new_attempts),
        "quotes": len(new_quotes),
        "read_error": read_error,
        "watchdog": needs,
        "dispatched": dispatched,
        "settled": len(new_settled),
        "published": bool(changed or stale),
        "capture_health": doc["capture_health"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--main-dir", type=Path, required=True)
    parser.add_argument("--ledger-dir", type=Path, required=True)
    parser.add_argument("--store-dir", type=Path, required=True)
    parser.add_argument("--season", type=int, default=SEASON)
    parser.add_argument("--now", default=None)
    parser.add_argument("--kalshi-base", default=KALSHI_BASE)
    parser.add_argument("--offline", action="store_true", help="no Kalshi read (prices fall back to the catalog)")
    parser.add_argument("--dispatch", action="store_true", help="let the watchdog dispatch the Script Engine")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    def offline() -> list[dict[str, Any]]:
        raise RuntimeError("offline: no Kalshi read")

    result = run_cycle(
        main_dir=args.main_dir,
        ledger_dir=args.ledger_dir,
        store_dir=args.store_dir,
        now=args.now or now_iso(),
        season=args.season,
        fetch=offline if args.offline else (lambda: fetch_series_markets(args.kalshi_base)),
        fetch_event=None if args.offline else (lambda t: fetch_event_markets(args.kalshi_base, t)),
        dispatch=dispatch_script_engine if args.dispatch else None,
        busy=script_engine_busy if args.dispatch else None,
        run_id=args.run_id,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
