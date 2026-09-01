"""Regression coverage for the 2026-09-01 live mapping_failure_rate_high
false alarm (forensic audit: GH Actions run 33556291244).

The live shape: a Week-1 Kalshi universe where ~84% of the "failing"
markets were FBS-vs-known-FCS contract ladders, most of the rest were
Division II/III fixtures or verified Kalshi name variants of non-FBS
programs, and a small genuine-failure residue remained (a
schedule-source discrepancy). These tests drive the REAL `_apply_scan`
path over that shape and prove:

- deliberately-declined populations are accounted as
  `markets_unsupported_population`, never as `mapping_failures`;
- the genuine-failure residue still counts, per market, under its event;
- canonical observations for mapped, supported markets are byte-identical
  whether or not the non-FBS identity set is supplied;
- withholding the non-FBS set reproduces the legacy (pre-fix) counting,
  pinning exactly what the fix changed and nothing else.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tests"))
sys.path.insert(0, str(_ROOT / "scripts"))

import research_scan_and_capture as scanner  # noqa: E402
from scan_harness import (  # noqa: E402
    NOW,
    SEASON,
    install_fake_market_feed,
    make_games,
    make_history_lines,
    make_markets,
)

from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache  # noqa: E402
from cfb_edge_finder.research import health, persistence  # noqa: E402
from cfb_edge_finder.research.scan_telemetry import ScanTelemetry  # noqa: E402
from cfb_edge_finder.schemas.provenance import ModelVersion  # noqa: E402
from cfb_edge_finder.teams.fcs_identity import (  # noqa: E402
    build_fcs_school_name_set,
    build_non_fbs_school_name_set,
)

MODEL_VERSION = ModelVersion(model_version="forensics-test-1.0", pricing_engine_version="0.1.0")

# CFBD-shaped /teams rows the identity sets are built from -- the same
# construction production uses (football_state.to_scan_inputs).
CFBD_TEAMS = [
    {"school": "Weber State", "classification": "fcs"},
    {"school": "Grambling", "classification": "fcs"},
    {"school": "Edward Waters", "classification": "ii"},
]
FCS_SCHOOL_NAMES = build_fcs_school_name_set(CFBD_TEAMS)
NON_FBS_SCHOOL_NAMES = build_non_fbs_school_name_set(CFBD_TEAMS)


def _rules(away_name: str, home_name: str) -> str:
    return (
        f"If a team wins the {away_name} vs {home_name} college football game "
        f"originally scheduled for Sep 2, 2026, then the market resolves to Yes."
    )


def _ladder(event_ticker: str, away_name: str, home_name: str, n_contracts: int) -> list[dict]:
    rules = _rules(away_name, home_name)
    return [
        {
            "ticker": f"{event_ticker}-T{3.5 + rung}",
            "event_ticker": event_ticker,
            "title": f"{home_name} wins by over {3.5 + rung} points",
            "floor_strike": 3.5 + rung,
            "rules_primary": rules,
            "status": "active",
            "yes_bid_dollars": "0.40",
            "yes_ask_dollars": "0.55",
            "no_bid_dollars": "0.45",
            "no_ask_dollars": "0.60",
        }
        for rung in range(n_contracts)
    ]


def _week1_like_universe(games) -> dict[str, list[dict]]:
    """The live pattern in miniature: mapped FBS-vs-FBS events (from the
    harness) plus an FBS-vs-known-FCS ladder, a verified-variant non-FBS
    ladder, and one genuinely unidentifiable event."""
    markets = make_markets(games)
    fbs_home = games[0].home_team_name
    fbs_home_2 = games[1].home_team_name
    # FBS-vs-known-FCS: the dominant live bucket (e.g. "Montana St. vs
    # Nevada"), 10-contract ladder to exercise per-market fan-out.
    markets["KXNCAAFSPREAD"] += _ladder("KXNCAAFSPREAD-26SEP02WSTFBS", "Weber St.", fbs_home, 10)
    # Verified Kalshi variant of a non-FBS program ("Grambling St." for
    # CFBD "Grambling") hosting at an FBS team.
    markets["KXNCAAFSPREAD"] += _ladder("KXNCAAFSPREAD-26SEP02GRAMFBS", "Grambling St.", fbs_home_2, 5)
    # Genuinely unidentifiable pair (neither side in any identity source)
    # -- must remain a loud, per-market mapping failure.
    markets["KXNCAAFGAME"] += _ladder(
        "KXNCAAFGAME-26SEP02WEBKC", "Webber International Warriors", "Kentucky Christian Knights", 3
    )
    return markets


def _run_scan(repo_dir: Path, monkeypatch, games, classification, markets, *, non_fbs):
    install_fake_market_feed(monkeypatch, markets)
    report = health.CaptureHealthReport()
    report.games_scanned = len(games)  # set by main() in production, not by _apply_scan
    scanner._apply_scan(  # noqa: SLF001
        repo_dir,
        season=SEASON,
        games=games,
        classification_by_game_id=classification,
        fcs_school_names=FCS_SCHOOL_NAMES,
        non_fbs_school_names=non_fbs,
        cache=GameProjectionCache(make_history_lines(games)),
        kalshi_client=None,
        model_version=MODEL_VERSION,
        training_cutoff_fn=lambda r: f"strictly before season={r.as_of_season} week={r.as_of_week}",
        n_simulations=200,
        seed=0,
        now=NOW,
        schedule_source_timestamp=NOW,
        run_id="forensics-run",
        report=report,
        telemetry=ScanTelemetry(),
    )
    return report


def _observation_keys(repo_dir: Path) -> set[str]:
    path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    if not path.exists():
        return set()
    return {
        json.loads(line)["observation_key"]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def test_week1_like_universe_accounts_unsupported_populations_without_false_alarm(tmp_path, monkeypatch):
    games, classification = make_games(2)
    markets = _week1_like_universe(games)
    report = _run_scan(tmp_path, monkeypatch, games, classification, markets, non_fbs=NON_FBS_SCHOOL_NAMES)

    # 2 harness games x 3 series events each, plus the 3 synthetic events.
    assert report.events_scanned == 9
    # Only the genuinely unidentifiable event fails -- per market.
    assert report.events_mapping_failed == 1
    assert report.mapping_failures == 3
    # The declined populations are accounted explicitly, never as failures.
    assert report.markets_unsupported_population == 15

    diagnostics = health.evaluate_collapse(report, baseline_supported_markets=None)
    assert not any(d.code == "mapping_failure_rate_high" for d in diagnostics)
    assert not health.should_fail_run(diagnostics)


def test_without_non_fbs_set_the_legacy_counting_reproduces_the_live_alarm_shape(tmp_path, monkeypatch):
    # Withholding the non-FBS identity set must reproduce the PRE-FIX
    # accounting exactly: the FCS and variant ladders land back in
    # mapping_failures. This pins the fix's entire effect to the new
    # classification -- nothing else about the counting changed.
    games, classification = make_games(2)
    markets = _week1_like_universe(games)
    report = _run_scan(tmp_path, monkeypatch, games, classification, markets, non_fbs=frozenset())

    assert report.events_mapping_failed == 3
    assert report.mapping_failures == 18
    assert report.markets_unsupported_population == 0


def test_canonical_observations_for_supported_markets_identical_with_and_without_fix(tmp_path, monkeypatch):
    # Requirement: due checkpoints on unaffected (mapped, supported)
    # markets must produce the same canonical observations as before.
    games, classification = make_games(2)
    markets = _week1_like_universe(games)
    with_fix = tmp_path / "with_fix"
    without_fix = tmp_path / "without_fix"
    with_fix.mkdir()
    without_fix.mkdir()
    _run_scan(with_fix, monkeypatch, games, classification, markets, non_fbs=NON_FBS_SCHOOL_NAMES)
    _run_scan(without_fix, monkeypatch, games, classification, markets, non_fbs=frozenset())
    keys_with = _observation_keys(with_fix)
    keys_without = _observation_keys(without_fix)
    assert keys_with == keys_without
    assert keys_with, "fixture produced no due captures -- assertions above would be vacuous"
