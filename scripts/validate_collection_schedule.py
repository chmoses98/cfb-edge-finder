#!/usr/bin/env python3
"""Mission section 19: validate the checkpoint timing logic against the
REAL upcoming CFB slate, without capturing anything.

Strictly read-only and side-effect free. It fetches the live schedule,
reads the existing corpus, and for representative games at different
distances from kickoff prints which labels are due now, which are not yet
due, which are already captured, and when the next one becomes due.

*** IT ALSO DOES NOT SPEND QUOTA IT KNOWS IS GONE ***
The CFBD free tier is 1,000 metered calls/month shared across CFB and
CBB, and this diagnostic runs on EVERY manual/conductor dispatch of the
capture workflow -- ahead of deadline-critical captures. When the durable
access state (data/research/cfbd_access/state.json, written by the
collector, which owns probing and recovery) already says the quota is
exhausted and the next recovery-probe window has not arrived, a live
schedule fetch here is doomed: it can only produce 429s and retries.
So the gate is consulted FIRST and the fetch is skipped, exactly as
scripts/collection_conductor.py already does for its own planning read.
This costs nothing when access is healthy -- the gate is a local file
read, never a network call -- and it is a diagnostic decision only:
nothing about capture eligibility, fail-closed schedule handling, or the
ESPN fallback changes.

It deliberately does NOT write rows: fabricating a snapshot that is not
legitimately due would corrupt exactly the research primitive this
milestone exists to protect. Exact boundary behaviour is covered by
deterministic unit tests (tests/test_research_timing.py,
tests/test_prospective_collection.py); this script is the reality check
that those windows line up with a real slate.

    python scripts/validate_collection_schedule.py --schedule-season 2026
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import capture_kalshi_cfb_snapshot as milestone_d  # noqa: E402

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.research import (  # noqa: E402
    cfbd_access,
    persistence,
    shards,
    timing,
)

CADENCE_MINUTES = 10.0


def _next_due_at(kickoff: datetime, now: datetime, captured: set[str]) -> tuple[str, datetime] | None:
    """The next checkpoint that will become due, found by walking the
    cadence forward. Simulation, not prediction: it asks the SAME
    `resolve_due_labels` the collector uses, so it can never disagree
    with it."""
    probe = now
    horizon = kickoff + timedelta(minutes=1)
    while probe <= horizon:
        probe += timedelta(minutes=CADENCE_MINUTES)
        due = timing.resolve_due_labels(
            kickoff_utc=kickoff, now=probe, already_captured_labels=captured, game_started=False
        )
        if due:
            return due[0], probe
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schedule-season", type=int, default=2026)
    parser.add_argument("--data-repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--max-games", type=int, default=12)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    settings = Settings.from_env()

    now = datetime.now(UTC)

    # *** QUOTA GATE, BEFORE ANY METERED REQUEST ***
    # Read-only and local: the durable state the collector already
    # maintains. If it says metered CFBD access is gated off and the next
    # recovery probe is not yet due, every request below would be a 429,
    # so do not make them. The diagnostic still runs -- against the
    # durable corpus, reporting the gate -- rather than pretending the
    # schedule is unknown for some other reason.
    access_state = cfbd_access.load_state(args.data_repo_dir)
    quota_gated = cfbd_access.gate_says_exhausted(access_state, now=now)

    games = []
    gate_note = ""
    if quota_gated:
        gate_note = (
            f"CFBD GATED ({access_state.get('access_state')}): live schedule fetch skipped; "
            f"next probe {access_state.get('cfbd_next_probe_at')}, "
            f"quota resets {access_state.get('cfbd_quota_resets_at')}"
        )
        print(gate_note)
        print("(the collector owns probing and recovery; this read-only check never spends quota)")
    else:
        if not settings.cfbd_api_key:
            print("ERROR: CFBD_API_KEY not set.", file=sys.stderr)
            return 2
        client = CFBDClient(api_key=settings.cfbd_api_key)
        try:
            games, _classification = milestone_d._fetch_candidate_games(args.schedule_season, client, now)  # noqa: SLF001
        except CFBDAuthError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

    base_dir = args.data_repo_dir / "data" / "research"
    obs_sources = persistence.corpus_sources(base_dir, persistence.OBSERVATIONS_SUBDIR, args.schedule_season)
    index = persistence.load_observation_index(obs_sources)

    # The scanner's index is keyed by market TICKER (that is what its
    # scheduling lookup needs). This report is per GAME, so fold the
    # corpus once more into game_id -> captured labels. A ticker string
    # does not contain the game_id, so this has to come from each row's
    # own game_id field rather than from string matching.
    captured_by_game: dict[str, set[str]] = {}
    malformed = 0
    for _path, line in shards.iter_raw_lines(obs_sources):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        observation = obj.get("observation") or {}
        game_id = observation.get("game_id")
        label = (observation.get("snapshot_timing") or {}).get("label")
        if isinstance(game_id, str) and isinstance(label, str):
            captured_by_game.setdefault(game_id, set()).add(label)

    upcoming = [
        g for g in games if g.status == "scheduled" and g.kickoff_utc is not None and g.kickoff_utc > now
    ]
    upcoming.sort(key=lambda g: g.kickoff_utc)

    print(f"now (UTC)            : {now.isoformat()}")
    print(f"schedule season      : {args.schedule_season}")
    print(f"cfbd access          : {access_state.get('access_state') or 'no durable state recorded'}"
          + ("  [live fetch skipped]" if quota_gated else ""))
    print(f"scheduled games      : {len(games)}")
    print(f"upcoming (future KO) : {len(upcoming)}")
    print(f"corpus rows          : {index.row_count}  (loads={index.load_count}, malformed={index.malformed_rows})")
    print(f"games with snapshots : {len(captured_by_game)}  (malformed rows skipped: {malformed})")
    print(f"assumed cadence      : every {CADENCE_MINUTES:.0f} min")
    print()

    # Representative spread: nearest kickoffs first, then a few farther
    # out, so the report covers several distances rather than one cluster.
    sample = upcoming[: args.max_games // 2] + upcoming[len(upcoming) // 2 :][: args.max_games // 2]
    results = []
    header = f"{'hours_to_KO':>11}  {'game':<44} {'captured':<26} {'due_now':<22} next_due"
    print(header)
    print("-" * len(header))
    for game in sample:
        kickoff = game.kickoff_utc
        hours_out = (kickoff - now).total_seconds() / 3600.0
        # Labels already captured for ANY contract on this game -- the
        # game-level view of collection coverage.
        captured: set[str] = set(captured_by_game.get(game.game_id, set()))
        due_now = timing.resolve_due_labels(
            kickoff_utc=kickoff, now=now, already_captured_labels=captured, game_started=False
        )
        nxt = _next_due_at(kickoff, now, captured | set(due_now))
        not_yet = [
            label
            for label in timing.ALL_PREGAME_LABELS
            if label not in captured and label not in due_now
        ]
        row = {
            "game_id": game.game_id,
            "kickoff_utc": kickoff.isoformat(),
            "hours_to_kickoff": round(hours_out, 2),
            "already_captured": sorted(captured),
            "due_now": due_now,
            "not_yet_due": not_yet,
            "next_due_label": nxt[0] if nxt else None,
            "next_due_at": nxt[1].isoformat() if nxt else None,
        }
        results.append(row)
        print(
            f"{hours_out:>11.2f}  {game.game_id[:44]:<44} {','.join(sorted(captured))[:26]:<26} "
            f"{','.join(due_now)[:22]:<22} {(nxt[0] + ' @ ' + nxt[1].strftime('%m-%d %H:%M')) if nxt else '-'}"
        )

    print()
    print("Checkpoint windows in force:")
    for w in timing.NUMERIC_TIMING_WINDOWS:
        print(
            f"  {w.label:<8} {w.lower_bound_hours * 60:>7.0f} - {w.upper_bound_hours * 60:>7.0f} min "
            f"({(w.upper_bound_hours - w.lower_bound_hours) * 60:.0f} min wide)"
        )
    print(f"  {'CLOSING':<8} {0:>7.0f} - {timing.CLOSING_WINDOW_MINUTES:>7.0f} min "
          f"({timing.CLOSING_WINDOW_MINUTES:.0f} min wide, strictly pre-kickoff, never backfilled)")
    print(f"  {'EARLY_OPEN':<8} due once, on first pregame sighting")

    if args.json is not None:
        args.json.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nWrote {args.json}")

    if quota_gated:
        print(
            "\nNOTE: the live slate could not be consulted because CFBD quota is exhausted per the "
            "durable access state, so per-game rows above cover only what the corpus already knows. "
            "This is a diagnostic limitation, never a capture decision."
        )
    print("\nSTATUS: READ-ONLY schedule validation. Nothing was captured, priced, or written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
