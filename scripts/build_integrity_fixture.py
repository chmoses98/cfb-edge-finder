"""Freeze a compact historical extract of research packets for the integrity tests' minimum-sample assertions.

Research-only. Reads committed packet files and keeps only the fields the integrity tests assert on, so the sample
size no longer depends on how many games happen to be on the current slate. Re-run only to deliberately re-freeze.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests/fixtures/research_packet_integrity_frozen.json"


def build() -> dict:
    games = []
    for d in sorted((ROOT / "data/research_packets/games").iterdir()):
        try:
            packet = json.loads((d / "packet.json").read_text())
            markets = json.loads((d / "markets.json").read_text())
            profiles = json.loads((d / "team_profiles.json").read_text())["teams"]
            sched = json.loads((d / "schedules.json").read_text())["teams"]
            teams = {s: packet["teams"][s]["name"] for s in ("home", "away")}
            ppg = {s: profiles[s]["metrics"]["overall_scoring"]["points_per_game"]["offense"] for s in ("home", "away")}
            faced = {s: sched[s]["strength_of_schedule"]["avg_opponent_defense_rank_points"] for s in ("home", "away")}
            team_ids = {s: profiles[s]["team_id"] for s in ("home", "away")}
            swapped_markets = markets["side_teams"]["orientation_swapped_vs_kalshi_key"]
        except (KeyError, TypeError, OSError):
            continue  # football history absent / incomplete packet: excluded, as the live tests exclude it
        games.append(
            {
                "game_key": d.name,
                "teams": teams,
                "team_ids": team_ids,
                "orientation_swapped_packet": bool(packet["teams"]["identity"].get("orientation_swapped")),
                "orientation_swapped_markets": swapped_markets,
                "moneyline_contracts": [
                    {"side": c.get("side"), "yes_means": c["yes_means"]}
                    for c in markets["contracts"]
                    if c["family"] == "game_moneyline"
                ],
                "ppg_offense": {
                    s: ({k: ppg[s][k] for k in ("games", "raw", "adjusted")} if ppg[s] else None) for s in ppg
                },
                "avg_opponent_defense_rank_points": faced,
            }
        )
    return {"frozen_from": "data/research_packets/games (committed historical packets)", "games": games}


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=None, separators=(",", ":"), sort_keys=True) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}")
