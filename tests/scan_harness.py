"""Shared fixture builder for driving the scheduled scanner end-to-end
in-process, with the live CFBD/Kalshi HTTP calls replaced at the one
documented seam (`_fetch_active_markets_safe`) and nothing else stubbed.

Everything downstream of market discovery -- evidence extraction, game
mapping, contract parsing, projection caching, ladder pricing, corpus-row
construction, canonical keying, append-only persistence -- is the REAL
code path `scripts/research_scan_and_capture.py` runs in production. That
is deliberate: this harness exists to prove the performance work changed
no research output, so anything it stubbed out would be a hole in that
proof.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from cfb_edge_finder.modeling.corpus import TeamGameLine  # noqa: E402
from cfb_edge_finder.schemas.game import GameRecord  # noqa: E402
from cfb_edge_finder.teams.registry import REGISTRY, Subdivision, resolve_team_alias  # noqa: E402

SEASON = 2026
NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
SERIES = ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL")


def _usable_fbs_teams() -> list[tuple[str, str]]:
    """REAL registry FBS teams whose display name resolves back to their
    own team_id. Synthetic names ("Team 0001") are useless here: game
    mapping resolves market prose through the real team registry, so a
    made-up name maps to nothing and the whole slate silently produces
    zero priced observations -- which would make every equivalence
    assertion pass vacuously."""
    teams: list[tuple[str, str]] = []
    for team in REGISTRY:
        if team.subdivision != Subdivision.FBS or not team.active:
            continue
        try:
            if resolve_team_alias(team.display_name) == team.team_id:
                teams.append((team.team_id, team.display_name))
        except Exception:  # noqa: BLE001 -- an unresolvable alias is simply not usable here
            continue
    return teams


FBS_TEAMS = _usable_fbs_teams()
MAX_GAMES = len(FBS_TEAMS) // 2


def make_games(n_games: int, *, kickoff_hours_ahead: float = 24.0) -> tuple[list[GameRecord], dict]:
    """`n_games` scheduled FBS-vs-FBS games, all kicking off inside a
    numeric timing window so captures are genuinely DUE (a slate where
    nothing is due would exercise none of the pricing path)."""
    if n_games > MAX_GAMES:
        raise ValueError(f"only {MAX_GAMES} disjoint FBS matchups available, asked for {n_games}")
    kickoff = NOW + timedelta(hours=kickoff_hours_ahead)
    games: list[GameRecord] = []
    classification: dict[str, tuple[str, str]] = {}
    for i in range(n_games):
        (home_id, home_name), (away_id, away_name) = FBS_TEAMS[2 * i], FBS_TEAMS[2 * i + 1]
        game_id = f"cfb-2026-wk01-{away_id}-at-{home_id}"
        games.append(
            GameRecord(
                game_id=game_id,
                season=SEASON,
                week_label="wk01",
                season_type="regular",
                home_team_id=home_id,
                away_team_id=away_id,
                home_team_name=home_name,
                away_team_name=away_name,
                neutral_site=False,
                kickoff_utc=kickoff,
                status="scheduled",
                week_number=1,
                discovered_at=NOW,
                last_updated_at=NOW,
            )
        )
        classification[game_id] = ("fbs", "fbs")
    return games, classification


def _rules(home_name: str, away_name: str) -> str:
    # The exact live-confirmed phrasing kalshi/contract_semantics.py's
    # _MATCHUP_IN_RULES_RE was built from -- see its docstring.
    return (
        f"If a team wins the {away_name} vs {home_name} college football game "
        f"originally scheduled for Sep 2, 2026, then the market resolves to Yes."
    )


def make_markets(games: list[GameRecord], *, contracts_per_ladder: int = 5) -> dict[str, list[dict]]:
    """Kalshi-shaped market dicts keyed by series ticker: one moneyline
    pair plus a spread and a total LADDER per game, so the
    one-projection-per-game / many-contracts-per-projection property is
    actually exercised rather than assumed."""
    by_series: dict[str, list[dict]] = {s: [] for s in SERIES}
    for game in games:
        slug = f"{game.away_team_id}{game.home_team_id}".replace("-", "").upper()[:20]
        event = f"KXNCAAFGAME-26SEP02{slug}"
        rules = _rules(game.home_team_name, game.away_team_name)
        prices = {
            "yes_bid_dollars": "0.40",
            "yes_ask_dollars": "0.55",
            "no_bid_dollars": "0.45",
            "no_ask_dollars": "0.60",
        }
        for side_name in (game.home_team_name, game.away_team_name):
            by_series["KXNCAAFGAME"].append(
                {
                    "ticker": f"{event}-{side_name.replace(' ', '').upper()}",
                    "event_ticker": event,
                    "title": f"{side_name} wins",
                    "rules_primary": rules,
                    "status": "active",
                    **prices,
                }
            )
        spread_event = event.replace("KXNCAAFGAME", "KXNCAAFSPREAD")
        total_event = event.replace("KXNCAAFGAME", "KXNCAAFTOTAL")
        by_series["KXNCAAFSPREAD"].append(
            {
                "ticker": f"{spread_event}-ANCHOR",
                "event_ticker": spread_event,
                "title": f"{game.home_team_name} wins by over 3 points",
                "floor_strike": 3.0,
                "rules_primary": rules,
                "status": "active",
                **prices,
            }
        )
        for rung in range(contracts_per_ladder):
            threshold = 3.5 + rung
            by_series["KXNCAAFSPREAD"].append(
                {
                    "ticker": f"{spread_event}-T{threshold}",
                    "event_ticker": spread_event,
                    "title": f"{game.home_team_name} wins by over {threshold} points",
                    "floor_strike": threshold,
                    "rules_primary": rules,
                    "status": "active",
                    **prices,
                }
            )
            total_threshold = 45.5 + rung
            by_series["KXNCAAFTOTAL"].append(
                {
                    "ticker": f"{total_event}-O{total_threshold}",
                    "event_ticker": total_event,
                    "title": f"Over {total_threshold} points scored",
                    "floor_strike": total_threshold,
                    "rules_primary": rules,
                    "status": "active",
                    **prices,
                }
            )
    return by_series


def _line(team, opp, pts, opp_pts, home, week):
    return TeamGameLine(
        source_game_id=f"{'-'.join(sorted([team, opp]))}-{week}",
        season=2025,
        week=week,
        is_postseason=False,
        team_id=team,
        opponent_id=opp,
        team_classification="fbs",
        opponent_classification="fbs",
        is_home=home,
        is_neutral_site=False,
        team_points=pts,
        opponent_points=opp_pts,
        team_plays=68,
        captured_at=NOW,
    )


def make_history_lines(games: list[GameRecord], *, weeks: int = 6) -> list[TeamGameLine]:
    """A leakage-safe synthetic prior-season corpus covering every team in
    `games`, so `GameProjectionCache` can genuinely fit ratings and build a
    residual pool (a stubbed projection would not exercise real pricing)."""
    rng = np.random.default_rng(17)
    teams = sorted({t for g in games for t in (g.home_team_id, g.away_team_id)})
    strength = {t: rng.normal(0, 0.05) for t in teams}
    lines: list[TeamGameLine] = []
    for week in range(1, weeks + 1):
        shuffled = teams[:]
        rng.shuffle(shuffled)
        for i in range(0, len(shuffled) - 1, 2):
            home, away = shuffled[i], shuffled[i + 1]
            home_pts = max(int(rng.normal(28 + strength[home] * 180 + 2, 9)), 0)
            away_pts = max(int(rng.normal(24 + strength[away] * 180, 9)), 0)
            lines.append(_line(home, away, home_pts, away_pts, True, week))
            lines.append(_line(away, home, away_pts, home_pts, False, week))
    return lines


def install_fake_market_feed(monkeypatch, markets_by_series: dict[str, list[dict]]) -> dict[str, int]:
    """Replaces ONLY the live Kalshi fetch, and counts calls so a test can
    assert market discovery happens once per series per run."""
    import capture_kalshi_cfb_snapshot as milestone_d

    calls: dict[str, int] = {}

    def _fake(_client, series_ticker: str) -> list[dict]:
        calls[series_ticker] = calls.get(series_ticker, 0) + 1
        # Mirrors the real `_fetch_active_markets_safe`, which filters to
        # status == "active" CLIENT-side (Kalshi rejects status= as a query
        # parameter -- see that function's docstring). Without this the
        # fixture would be unfaithful in exactly the direction that hides
        # bugs: suspended/closed markets would reach pricing in tests but
        # never in production.
        return [
            m
            for m in markets_by_series.get(series_ticker, [])
            if str(m.get("status", "")).lower() == "active"
        ]

    monkeypatch.setattr(milestone_d, "_fetch_active_markets_safe", _fake)
    return calls
