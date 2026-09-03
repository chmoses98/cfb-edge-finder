#!/usr/bin/env python3
"""LIVE, READ-ONLY validation of the ESPN fresh-schedule fallback.

Runs the REAL `research/schedule_state.refresh_schedule_state` against
the REAL durable football-state artifact and reports what it matched,
what it refused, and why. Nothing is written to the research-data branch:
the artifact is materialised into a temp directory with `git show`, and
the refreshed schedule state is written there and thrown away.

Makes ZERO CFBD requests of any kind -- not even the unmetered /info --
so it is safe to run while the quota is exhausted, which is exactly when
its answer matters. Exists because the dev container's egress proxy 403s
every ESPN host, so the only place this can be proven is a runner.

The shape this validates was live-verified on 2026-09-03 (runs
33789268655 and 33789404748): `site.api.espn.com` answers 403 to CI,
while `site.web.api.espn.com` and `cdn.espn.com` carry the identical
scoreboard payload.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.data.espn_schedule_client import EspnScheduleClient  # noqa: E402
from cfb_edge_finder.research import football_state, schedule_state  # noqa: E402
from cfb_edge_finder.teams.registry import REGISTRY, Subdivision  # noqa: E402


def _materialise_football_state(repo_dir: Path, branch: str, season: int, into: Path) -> str | None:
    """Copy the durable artifact out of origin/{branch} without checking
    it out -- the same read-only pattern `load_football_state_from_git`
    uses, kept explicit here so the failure mode is reportable."""
    base = into / "data" / "research" / football_state.FOOTBALL_STATE_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "fetch", "origin", branch, "--depth=1"], cwd=repo_dir, capture_output=True, timeout=180, check=False
    )
    for name in (f"{season}.json", f"{season}.manifest.json"):
        show = subprocess.run(
            ["git", "show", f"origin/{branch}:data/research/{football_state.FOOTBALL_STATE_SUBDIR}/{name}"],
            cwd=repo_dir,
            capture_output=True,
            timeout=180,
        )
        if show.returncode != 0:
            return f"missing {name} on origin/{branch}"
        (base / name).write_bytes(show.stdout)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--data-branch", default="research-data")
    parser.add_argument("--repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--max-rejections-shown", type=int, default=25)
    args = parser.parse_args()

    now = datetime.now(UTC)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        error = _materialise_football_state(args.repo_dir, args.data_branch, args.season, work)
        if error:
            print(json.dumps({"ok": False, "error": error}, indent=2))
            return 1

        state, verdict = football_state.load_football_state(work, args.season)
        if state is None:
            print(json.dumps({"ok": False, "error": f"football state unusable: {verdict}"}, indent=2))
            return 1

        games = state.to_scan_inputs(now).games
        schedule_age_h = state.schedule_age_hours(now)
        near, far = schedule_state.required_buckets(games, now=now)

        outcome = schedule_state.refresh_schedule_state(
            work,
            games,
            season=args.season,
            now=now,
            client=EspnScheduleClient(),
            force_all_buckets=True,
        )
        applied = schedule_state.apply_schedule_state(
            games,
            outcome.state,
            cfbd_schedule_fetched_at=state.schedule_fetched_at,
            now=now,
            max_fact_age_hours=football_state.SCHEDULE_HARD_MAX_HOURS,
        )

        trusted_within_bound = sum(
            1
            for stamp in applied.schedule_source_timestamps.values()
            if (now - stamp).total_seconds() / 3600.0 <= football_state.SCHEDULE_HARD_MAX_HOURS
        )
        horizon_8h = now + timedelta(hours=8)
        soon = [g for g in applied.games if g.kickoff_utc is not None and now < g.kickoff_utc <= horizon_8h]

        # FBS participation is what the collector can actually capture, so
        # it is the only coverage number that means anything: ESPN's
        # groups=80 scoreboard does not carry D-II/D-III games and is not
        # expected to, while the artifact's schedule carries every division.
        fbs = {t.team_id for t in REGISTRY if t.subdivision == Subdivision.FBS}
        in_window = [
            g
            for g in games
            if g.kickoff_utc is not None
            and (now - timedelta(hours=24)) <= g.kickoff_utc <= now + timedelta(hours=schedule_state.DEEP_HORIZON_HOURS)
        ]
        fbs_in_window = [g for g in in_window if g.home_team_id in fbs or g.away_team_id in fbs]
        fbs_matched = [g for g in fbs_in_window if g.game_id in applied.fresh_game_ids]
        fbs_unmatched = [g for g in fbs_in_window if g.game_id not in applied.fresh_game_ids]

        report = {
            "ok": outcome.verdict != schedule_state.SCHEDULE_STATE_UNAVAILABLE and len(fbs_matched) > 0,
            "checked_at": now.isoformat(),
            "cfbd_requests_made": 0,
            "football_state": {
                "schedule_fetched_at": state.schedule_fetched_at.isoformat(),
                "schedule_age_hours": round(schedule_age_h, 2),
                "past_6h_hard_bound": schedule_age_h > football_state.SCHEDULE_HARD_MAX_HOURS,
                "games_in_artifact": len(games),
            },
            "buckets": {"near": near, "far": far},
            "espn": outcome.summary_dict(),
            "espn_hosts_attempted": [
                {"host": f.host, "bucket": f.date_param, "http": f.http_status, "n_events": len(f.events),
                 "error": f.error}
                for f in outcome.fetches
            ],
            "coverage": {
                "games_in_maintained_window": len(in_window),
                "fbs_participant_games_in_window": len(fbs_in_window),
                "fbs_games_with_fresh_espn_facts": len(fbs_matched),
                "fbs_games_without_fresh_facts": len(fbs_unmatched),
                "fbs_coverage_pct": (
                    round(100.0 * len(fbs_matched) / len(fbs_in_window), 1) if fbs_in_window else None
                ),
                "games_within_6h_bound_after_fallback": trusted_within_bound,
                "games_kicking_off_within_8h": len(soon),
                "games_within_8h_lacking_fresh_facts": sum(
                    1 for g in soon if g.game_id not in applied.fresh_game_ids
                ),
            },
            "schedule_changes": [
                {
                    "game_id": c.game_id,
                    "previous_kickoff_utc": c.previous_kickoff_utc.isoformat() if c.previous_kickoff_utc else None,
                    "new_kickoff_utc": c.new_kickoff_utc.isoformat(),
                    "detected_at": c.detected_at.isoformat(),
                }
                for c in outcome.changes
            ],
            "rejection_reasons": _reason_histogram(outcome.rejections),
            "unmatched_fbs_sample": [
                {
                    "game_id": g.game_id,
                    "kickoff_utc": g.kickoff_utc.isoformat() if g.kickoff_utc else None,
                    "reason": outcome.rejections.get(g.game_id, "bucket not fetched / no fact"),
                }
                for g in fbs_unmatched[: args.max_rejections_shown]
            ],
        }
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1


def _reason_histogram(rejections: dict[str, str]) -> dict[str, int]:
    """Group refusals by their KIND, not their text. Every 'no ESPN event
    matched home=X away=Y' carries different team ids, so grouping on the
    raw string produced one bucket per game and buried the shape under
    four hundred lines of noise."""
    counts: dict[str, int] = {}
    for reason in rejections.values():
        key = " ".join(reason.split()[:4])[:60]
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
