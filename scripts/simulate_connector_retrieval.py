#!/usr/bin/env python3
"""Retrieve a RUN CFB slate the way ChatGPT's GitHub connector does.

Uses ONLY the GitHub Contents API with its default JSON response
(``GET /repos/{owner}/{repo}/contents/{path}?ref=...``) and decodes the
base64 ``content`` field. No raw downloads, no code search, no local
files. That is the access pattern that returned an empty string for the
1.5 MB catalog and the 1.27 MB marquee game files, so a slate this script
can retrieve is one the connector's file-access path can retrieve.

    python scripts/simulate_connector_retrieval.py --date 2026-10-17 --window afternoon
    python scripts/simulate_connector_retrieval.py --date 2026-10-17 --game 26OCT17AUBUGA --game 26OCT16ASUTTU
    python scripts/simulate_connector_retrieval.py --ref claude/some-branch --date 2026-10-17

Set GITHUB_TOKEN to avoid the 60-request/hour unauthenticated limit.
Exit 0 when every requested game's complete inventory was retrieved and
verified; 1 otherwise. This is a diagnostic, not part of the publish path.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from typing import Any

API = "https://api.github.com"


class Retrieval:
    def __init__(self, owner: str, repo: str, ref: str) -> None:
        self.owner, self.repo, self.ref = owner, repo, ref
        self.calls = 0
        self.bytes = 0
        self.max_file = ("", 0)

    def _get(self, url: str) -> dict[str, Any]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "cfb-edge-finder-retrieval-check"}
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        last: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
                    self.calls += 1
                    return json.loads(resp.read())
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last = exc
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"GET {url} failed after retries: {last}")

    def file(self, path: str) -> dict[str, Any]:
        """One file through the Contents API, exactly as the connector sees it."""
        quoted, ref = urllib.parse.quote(path), urllib.parse.quote(self.ref)
        url = f"{API}/repos/{self.owner}/{self.repo}/contents/{quoted}?ref={ref}"
        meta = self._get(url)
        size = meta.get("size", 0)
        if meta.get("encoding") != "base64" or not meta.get("content"):
            raise RuntimeError(
                f"{path}: Contents API returned encoding={meta.get('encoding')!r} with "
                f"{len(meta.get('content') or '')} content chars for a {size}-byte file "
                "(the >1 MB failure mode)"
            )
        text = base64.b64decode(meta["content"]).decode("utf-8")
        self.bytes += len(text)
        if len(text) > self.max_file[1]:
            self.max_file = (path, len(text))
        return json.loads(text)

    def latest_successful_main_run(self) -> dict[str, Any] | None:
        url = (
            f"{API}/repos/{self.owner}/{self.repo}/actions/workflows/kalshi-market-catalog.yml/runs"
            "?branch=main&status=success&per_page=1"
        )
        runs = self._get(url).get("workflow_runs") or []
        return runs[0] if runs else None


def _price_line(q: dict[str, Any]) -> str:
    return (
        f"{q['market_ticker']:<44} {str(q['title'])[:44]:<44} "
        f"YES {q['yes_bid']}/{q['yes_ask']} x{q['yes_ask_size']}  "
        f"NO ask {q['no_ask']} x{q['yes_bid_size']}  [{q['book_state']}]"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", default="chmoses98")
    parser.add_argument("--repo", default="cfb-edge-finder")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--date", help="US/Eastern slate date YYYY-MM-DD (default: today in ET per the index)")
    parser.add_argument("--window", choices=["early", "afternoon", "evening", "all"], default="all")
    parser.add_argument("--game", action="append", default=[], help="game_key; repeatable (overrides --window)")
    parser.add_argument("--details", action="store_true", help="also fetch every event detail shard")
    parser.add_argument("--show-prices", type=int, default=3, help="sample price lines per core family")
    args = parser.parse_args(argv)

    r = Retrieval(args.owner, args.repo, args.ref)
    failures: list[str] = []

    index = r.file("data/live/cfb_execution_index.json")
    status = r.file("data/live/cfb_catalog_status.json")
    cap = index["capture"]
    print(f"index: schema {index['schema_version']}  captured_at {cap['captured_at']}  "
          f"fingerprint {str(cap['content_fingerprint'])[:12]}  "
          f"{cap['physical_games']} games / {cap['markets']} contracts")
    if status.get("content_fingerprint") != cap["content_fingerprint"]:
        failures.append("status file fingerprint != index fingerprint (index out of sync with catalog)")
        print("  !! status fingerprint does NOT match the index")
    else:
        print("  status file fingerprint matches the index")

    run = None
    try:
        run = r.latest_successful_main_run()
    except RuntimeError as exc:
        print(f"  freshness: run history unreachable ({exc}); report prices as CAPTURED, freshness UNVERIFIED")
    if run:
        finished = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
        age = (datetime.now(UTC) - finished).total_seconds() / 60
        print(f"  latest successful main run: #{run['run_number']} finished {run['updated_at']} ({age:.0f} min ago)")

    slate_date = args.date
    if not slate_date:
        from zoneinfo import ZoneInfo

        slate_date = datetime.now(ZoneInfo(index["slate_timezone"])).strftime("%Y-%m-%d")
    slate_ptr = next((s for s in index["slates"] if s["slate_date"] == slate_date), None)
    if slate_ptr is None:
        print(f"no slate {slate_date} in the index; available: {[s['slate_date'] for s in index['slates']]}")
        return 1
    try:
        slate_games = r.file(slate_ptr["path"])["games"]
    except RuntimeError as exc:
        # Fallback: the index itself carries every game row.
        print(f"  slate file failed ({exc}); falling back to the index's own game rows")
        slate_games = [g for g in index["games"] if g["slate_date"] == slate_date]

    if args.game:
        wanted = [g for g in slate_games if g["game_key"] in set(args.game)]
        missing = set(args.game) - {g["game_key"] for g in wanted}
        if missing:
            failures.append(f"games not on slate {slate_date}: {sorted(missing)}")
    elif args.window != "all":
        wanted = [g for g in slate_games if g["window"] == args.window]
    else:
        wanted = slate_games
    print(f"slate {slate_date}: {len(slate_games)} games; selected {len(wanted)} "
          f"({args.window if not args.game else 'by game key'})\n")

    for game in wanted:
        key = game["game_key"]
        quotes: list[dict[str, Any]] = []
        for path in game["quote_files"]:
            try:
                doc = r.file(path)
            except RuntimeError as exc:
                failures.append(f"{key}: {exc}")
                continue
            if doc["game_fingerprint"] != game["game_fingerprint"]:
                failures.append(f"{key}: {path} fingerprint differs from the slate row (re-read the index)")
            quotes.extend(doc["quotes"])
        families = Counter(q["family"] for q in quotes)
        complete = len(quotes) == game["market_count"]
        status_word = "USABLE" if game["usable"] else "UNAVAILABLE (incomplete discovery)"
        print(f"{key:20} {game['title']:<34} {game['kickoff_et']}  [{game['window']}]  {status_word}")
        print(f"  retrieved {len(quotes)}/{game['market_count']} contracts in {len(game['quote_files'])} file(s) "
              f"across {len(families)} families -> {'COMPLETE' if complete else 'MISSING CONTRACTS'}")
        if not complete:
            failures.append(f"{key}: {len(quotes)} of {game['market_count']} contracts retrieved")
        two_sided = sum(1 for q in quotes if q["book_state"] == "two_sided")
        print(f"  books: {two_sided} two-sided, {len(quotes) - two_sided} other; "
              f"buy-YES executable {sum(q['buy_yes_executable'] for q in quotes)}, "
              f"buy-NO executable {sum(q['buy_no_executable'] for q in quotes)}")
        print("  families: " + ", ".join(f"{f}={n}" for f, n in sorted(families.items())))
        for family in ("game_moneyline", "game_spread", "game_total"):
            for q in [q for q in quotes if q["family"] == family][: args.show_prices]:
                print("    " + _price_line(q))
        if args.details:
            manifest = r.file(game["manifest"])
            detail_count = 0
            for detail in manifest["detail_files"]:
                try:
                    doc = r.file(detail["path"])
                except RuntimeError as exc:
                    failures.append(f"{key}: {exc}")
                    continue
                if doc["game_fingerprint"] != game["game_fingerprint"]:
                    failures.append(f"{key}: {detail['path']} fingerprint mismatch")
                detail_count += len(doc["markets"])
                missing_rules = [m["market_ticker"] for m in doc["markets"] if not m.get("rules_primary")]
                if missing_rules:
                    failures.append(f"{key}: {len(missing_rules)} records without rules_primary")
            ok = detail_count == game["market_count"]
            print(f"  detail shards: {len(manifest['detail_files'])} files, {detail_count} full records "
                  f"(rules, fee block, mechanics) -> {'COMPLETE' if ok else 'MISSING'}")
            if not ok:
                failures.append(f"{key}: detail shards hold {detail_count} of {game['market_count']}")
        print()

    print(f"{r.calls} API calls, {r.bytes:,} bytes decoded, largest file {r.max_file[0]} ({r.max_file[1]:,} bytes)")
    if failures:
        print(f"\nRETRIEVAL FAILED ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("RETRIEVAL OK: every selected game's complete contract inventory came back through the Contents API")
    return 0


if __name__ == "__main__":
    sys.exit(main())
