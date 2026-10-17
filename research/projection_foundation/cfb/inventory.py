"""Data-availability matrix with point-in-time classes.

pit_class:
  archived       captured before kickoff and stored with that status (closing lines, schedules, frozen artifacts)
  reconstructable  post-game facts that are only ever used LAGGED (box scores, play-by-play aggregates,
                 player box), or pre-season facts published before the season (talent, returning production)
  retrospective  computed after the fact with information unavailable pre-game (end-of-season SP+,
                 observed weather, CFBD's EPA model fitted on all seasons) - only safe as prior-season inputs
  missing        not obtainable from reachable sources
"""
from __future__ import annotations

import pandas as pd

from . import paths


def matrix(G: pd.DataFrame, T: pd.DataFrame, P: pd.DataFrame | None) -> list[dict]:
    def seasons(mask, frame):
        s = sorted(frame.loc[mask, "season"].unique())
        return f"{s[0]}-{s[-1]}" if s else "none"

    rows = [
        {"dataset": "game results / schedule (FBS-involved; FCS-vs-FCS excluded)", "seasons": seasons(G.index == G.index, G),
         "rows": int(len(G)), "pit_class": "archived",
         "notes": "CFBD 2014-2025 (research-data cache), ESPN production log 2026. Neutral site, conference game, week."},
        {"dataset": "team-game box scores (plays, pass/rush att & yards, completions, turnovers, possession)",
         "seasons": seasons(T["plays"].notna(), T), "rows": int(T["plays"].notna().sum()), "pit_class": "reconstructable",
         "notes": "plays = box rush att + pass att in both sources (sacks are rushes). 2026 ESPN means match 2024-25 CFBD "
                  "(plays 65.9 vs 66.6, rush yds 151.6 vs 154.4)."},
        {"dataset": "game-level EPA/play, success rate, explosiveness (all plays and garbage-time filtered)",
         "seasons": seasons(T["epa"].notna(), T), "rows": int(T["epa"].notna().sum()), "pit_class": "retrospective",
         "notes": "CFBD advanced box (its EPA model is fitted retrospectively); used only lagged. 2026: success rate from "
                  "ESPN drives, NO EPA -> EPA family has no live season."},
        {"dataset": "drives / possessions", "seasons": seasons(T["drives"].notna(), T), "rows": int(T["drives"].notna().sum()),
         "pit_class": "reconstructable", "notes": "CFBD advanced 'drives'; 2026 from ESPN drive summaries."},
        {"dataset": "raw play-by-play", "seasons": "2014-2026 (remote)", "rows": 0, "pit_class": "reconstructable",
         "notes": "sportsdataverse espn_cfb_pbp / cfbfastR_cfb_pbp releases are reachable but were NOT downloaded "
                  "(~100 MB/season); game-level CFBD aggregates already carry EPA/SR with and without garbage time."},
        {"dataset": "player box scores (passing, rushing, receiving)",
         "seasons": "none" if P is None else f"{P.season.min()}-{P.season.max()}", "rows": 0 if P is None else int(len(P)),
         "pit_class": "reconstructable",
         "notes": "sportsdataverse espn_cfb_player_box. NO targets, snaps, routes or positions; only players with a stat appear."},
        {"dataset": "FBS/FCS classification and conferences", "seasons": "2014-2026", "rows": int(len(G)), "pit_class": "archived",
         "notes": "per-game classification from the schedule source (2026: Sacramento State / North Dakota State listed FBS)."},
        {"dataset": "talent composite (247)", "seasons": "2015-2026", "rows": 0, "pit_class": "reconstructable",
         "notes": "2014 file empty. Published pre-season; may be revised in-season (low contamination risk)."},
        {"dataset": "recruiting class points", "seasons": "2014-2025", "rows": 0, "pit_class": "reconstructable",
         "notes": "2026 class not cached: 2026 uses the 2023-2025 classes (3-year average)."},
        {"dataset": "returning production (offence PPA/usage)", "seasons": "2014-2026", "rows": 0, "pit_class": "reconstructable",
         "notes": "Offence only (no defensive returning production in the source)."},
        {"dataset": "transfer portal", "seasons": "2025 only", "rows": 0, "pit_class": "missing",
         "notes": "portal.json exists for 2025 only -> not usable for a walk-forward test."},
        {"dataset": "coaches (new head coach flag)", "seasons": "2014-2026", "rows": 0, "pit_class": "reconstructable", "notes": ""},
        {"dataset": "QB changes", "seasons": "2014-2026", "rows": 0, "pit_class": "reconstructable",
         "notes": "derived from player box (leading passer of the previous game vs this game). Injury reports: 2026 "
                  "current snapshot only (data/football/2026/availability.json) -> no historical injury data."},
        {"dataset": "weather / venue", "seasons": "2014-2025", "rows": 0, "pit_class": "retrospective",
         "notes": "research-data-v2enrich weather_archive = OBSERVED weather (contaminated as a pre-game input); "
                  "weather_hforecast (historical forecasts) exists but was not evaluated here. Venue id in games."},
        {"dataset": "closing lines (spread / total)", "seasons": seasons(G["close_spread"].notna(), G),
         "rows": int(G["close_spread"].notna().sum()), "pit_class": "archived",
         "notes": "CFBD multi-provider lines 2014-2025 (median across providers; untimestamped, treated as closing), "
                  "ESPN core odds 2026. Benchmark only - never a model input."},
        {"dataset": "SP+ / season advanced ratings", "seasons": "2014-2025", "rows": 0, "pit_class": "retrospective",
         "notes": "end-of-season values; not used."},
        {"dataset": "archived pregame forecasts in repo", "seasons": "2026", "rows": 0, "pit_class": "archived",
         "notes": "script-engine frozen artifacts (data/scripting/live/frozen, frozen_v2: claims + historical margin ranges, "
                  "no probabilities). The retired engine's expected points 2014-2025 (base_repair source contract) and the "
                  "v2 research walk-forward predictions 2017-2025 are RETROSPECTIVE replays, not archived pre-game."},
    ]
    return rows
