#!/usr/bin/env python3
"""Read-only live probe: can a FREE result source settle completed games
while CFBD is quota-exhausted and site.api.espn.com answers 403 to CI?

Runs the REAL settlement parsers (`research/result_provider.parse_espn_event`
and `espn_game_result`) against each candidate host, so what it reports is
what settlement would actually do -- not a hand-written approximation of
it. Writes nothing, settles nothing, makes ZERO CFBD calls.

Runs on a GitHub-hosted runner because the dev container's egress proxy
403s every ESPN host, and because the whole point is that host
reachability DIFFERS on CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.research.result_provider import (  # noqa: E402
    espn_game_result,
    parse_espn_event,
)
from cfb_edge_finder.schemas.settlement import GameFinalStatus  # noqa: E402

UA = {"User-Agent": "cfb-edge-finder settlement-source probe (read-only)"}

CANDIDATES = {
    # The host the settlement fallback currently uses.
    "site.api.espn.com": (
        "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
        {"groups": 80, "limit": 400},
        ("events",),
    ),
    # Verified reachable from CI on 2026-09-03 by the schedule fallback.
    "site.web.api.espn.com": (
        "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
        {"groups": 80, "limit": 400},
        ("events",),
    ),
    "cdn.espn.com": (
        "https://cdn.espn.com/core/college-football/scoreboard",
        {"xhr": 1, "groups": 80, "limit": 400},
        ("content", "sbData", "events"),
    ),
    # Structured NCAA surface, no auth. Different shape entirely; probed
    # so the report can say whether a genuinely independent second
    # provider exists, not just a second ESPN host.
    "ncaa (data.ncaa.com)": (
        "https://data.ncaa.com/casablanca/scoreboard/football/fbs/{y}/{m}/{d}/scoreboard.json",
        None,
        ("games",),
    ),
}


def _dig(payload, path: tuple[str, ...]):
    node = payload
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, list) else None


def probe(name: str, url: str, params: dict | None, path: tuple[str, ...], day: datetime) -> dict:
    if "{y}" in url:
        url = url.format(y=day.strftime("%Y"), m=day.strftime("%m"), d=day.strftime("%d"))
        params = None
    else:
        params = dict(params or {}, dates=day.strftime("%Y%m%d"))
    try:
        response = requests.get(url, params=params, headers=UA, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"host": name, "http": 0, "error": f"{type(exc).__name__}: {exc}"}
    if response.status_code != 200:
        return {"host": name, "http": response.status_code, "error": response.text[:160]}
    try:
        payload = response.json()
    except ValueError as exc:
        return {"host": name, "http": 200, "error": f"unparsable JSON: {exc}"}
    events = _dig(payload, path)
    if events is None:
        return {"host": name, "http": 200, "error": f"no list at {'.'.join(path)}", "top_keys": sorted(payload)[:12]}
    return {"host": name, "http": 200, "n_events": len(events), "events": events}


def settlement_check(events: list, day: datetime) -> dict:
    """Run the REAL settlement parsers over the events. Reports what
    would actually settle, fail closed, or be judged not-yet-final."""
    now = datetime.now(UTC)
    summary = {"parsed": 0, "identity_unresolved": 0, "final_settleable": 0, "not_yet_final": 0, "fail_closed": 0}
    samples: list[dict] = []
    fail_reasons: list[str] = []
    for raw in events:
        if not isinstance(raw, dict):
            continue
        facts = parse_espn_event(raw)
        summary["parsed"] += 1
        if facts.resolution_error is not None:
            summary["identity_unresolved"] += 1
            continue
        result, reason = espn_game_result(
            facts, game_id="probe", season=day.year, now=now, fallback_reason="probe"
        )
        if reason is not None:
            summary["fail_closed"] += 1
            fail_reasons.append(reason[:140])
            continue
        assert result is not None
        if result.status is GameFinalStatus.FINAL:
            summary["final_settleable"] += 1
            if len(samples) < 3:
                samples.append(
                    {
                        "espn_event_id": result.source_game_id,
                        "name": facts.name,
                        "home_points": result.home_points,
                        "away_points": result.away_points,
                        "went_to_overtime": result.went_to_overtime,
                        "status_evidence": result.status_evidence,
                    }
                )
        else:
            summary["not_yet_final"] += 1
    return {**summary, "settleable_samples": samples, "fail_closed_reasons": fail_reasons[:5]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days-back", type=int, default=5, help="How many days back to look for a completed slate")
    args = ap.parse_args()

    now = datetime.now(UTC)
    report: dict = {"probed_at": now.isoformat(), "cfbd_calls_made": 0, "hosts": {}}

    # Find a day that actually has completed games, using the first
    # reachable host -- never hard-coded scores, never a hard-coded date.
    for offset in range(1, args.days_back + 1):
        day = now - timedelta(days=offset)
        per_host: dict = {}
        any_final = False
        for name, (url, params, path) in CANDIDATES.items():
            got = probe(name, url, params, path, day)
            events = got.pop("events", None)
            if events is not None and name != "ncaa (data.ncaa.com)":
                got["settlement"] = settlement_check(events, day)
                any_final = any_final or got["settlement"]["final_settleable"] > 0
            elif events is not None:
                # NCAA's shape is entirely different; report its keys so
                # the decision to use or reject it is evidence-based.
                got["sample_keys"] = sorted(events[0])[:14] if events else []
                got["sample"] = json.dumps(events[0])[:400] if events else None
            per_host[name] = got
        report["hosts"][day.strftime("%Y-%m-%d")] = per_host
        if any_final:
            report["chosen_day"] = day.strftime("%Y-%m-%d")
            break

    print(json.dumps(report, indent=1, default=str))
    print("\n==== COMPACT VERDICT ====")
    verdict = {}
    for day, hosts in report["hosts"].items():
        for name, got in hosts.items():
            s = got.get("settlement") or {}
            verdict[f"{day} {name}"] = {
                "http": got.get("http"),
                "n_events": got.get("n_events"),
                "final_settleable": s.get("final_settleable"),
                "fail_closed": s.get("fail_closed"),
                "identity_unresolved": s.get("identity_unresolved"),
                "error": (got.get("error") or "")[:80] or None,
            }
    print(json.dumps(verdict, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
