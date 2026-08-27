"""Mission section 7: one expensive projection per game, many contracts
priced cheaply from it -- and that property must SURVIVE the performance
work, not merely have held before it.

`GameProjectionCache` already existed for this reason; what was missing
was anything that actually checked it. These tests assert the counts
directly, so a future change that reintroduced per-contract projection
(or per-game ratings refitting) fails here instead of showing up as a
mysterious live slowdown weeks later.
"""

from __future__ import annotations

import collections
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

MODEL_VERSION = ModelVersion(model_version="reuse-test-1.0", pricing_engine_version="0.1.0")


def _scan(repo_dir: Path, monkeypatch, *, n_games: int, contracts_per_ladder: int = 5):
    games, classification = make_games(n_games)
    install_fake_market_feed(monkeypatch, make_markets(games, contracts_per_ladder=contracts_per_ladder))
    cache = GameProjectionCache(make_history_lines(games))
    telemetry = ScanTelemetry()
    report = health.CaptureHealthReport()
    scanner._apply_scan(
        repo_dir,
        season=SEASON,
        games=games,
        classification_by_game_id=classification,
        fcs_school_names=frozenset(),
        cache=cache,
        kalshi_client=None,
        model_version=MODEL_VERSION,
        training_cutoff_fn=lambda r: "cutoff",
        n_simulations=300,
        seed=0,
        now=NOW,
        schedule_source_timestamp=NOW,
        run_id="reuse",
        report=report,
        telemetry=telemetry,
    )
    path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    rows = [
        __import__("json").loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return cache, telemetry, report, rows


def test_one_projection_per_game_not_per_contract(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    n_games = 6
    cache, telemetry, report, rows = _scan(repo_dir, monkeypatch, n_games=n_games)

    priced = [r for r in rows if r["observation"]["pricing_status"] == "model_priced"]
    assert len(priced) > 0

    distinct_games = {r["observation"]["game_id"] for r in priced}
    assert cache.projection_builds <= len(distinct_games), (
        f"{cache.projection_builds} projections built for {len(distinct_games)} distinct games "
        "-- projection is no longer once-per-game"
    )
    assert len(priced) > cache.projection_builds, (
        "no contract reuse at all: as many projections as priced contracts"
    )
    assert telemetry.priced_contract_count == len(priced)


def test_ratings_are_fitted_once_per_as_of_not_once_per_game(tmp_path, monkeypatch):
    """The expensive shared upstream work (ridge fit + residual pool)
    depends only on `as_of`. Every game on one week's slate shares it."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cache, _, _, rows = _scan(repo_dir, monkeypatch, n_games=8)
    distinct_games = {r["observation"]["game_id"] for r in rows if r["observation"]["game_id"]}
    assert cache.ratings_fits == 1, (
        f"ratings refit {cache.ratings_fits} times for {len(distinct_games)} games sharing one as_of"
    )


def test_contracts_per_projection_scales_with_ladder_depth(tmp_path, monkeypatch):
    """A deeper ladder must cost more PRICING but not more MODEL work --
    this is the actual "price-only changes do not rerun the football
    model" property, measured rather than asserted."""
    shallow_dir = tmp_path / "shallow"
    shallow_dir.mkdir()
    deep_dir = tmp_path / "deep"
    deep_dir.mkdir()

    shallow_cache, _, _, shallow_rows = _scan(shallow_dir, monkeypatch, n_games=4, contracts_per_ladder=2)
    deep_cache, _, _, deep_rows = _scan(deep_dir, monkeypatch, n_games=4, contracts_per_ladder=10)

    assert len(deep_rows) > len(shallow_rows), "deeper ladder priced no extra contracts"
    assert deep_cache.projection_builds == shallow_cache.projection_builds, (
        f"deepening the ladder changed projection count "
        f"({shallow_cache.projection_builds} -> {deep_cache.projection_builds})"
    )
    assert deep_cache.ratings_fits == shallow_cache.ratings_fits


def test_contracts_per_projection_distribution_is_reported(tmp_path, monkeypatch):
    """Mission section 7 asks for the distribution, not just the totals --
    so compute it here and keep it assertable."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cache, telemetry, _, rows = _scan(repo_dir, monkeypatch, n_games=6)
    per_game = collections.Counter(
        r["observation"]["game_id"] for r in rows if r["observation"]["pricing_status"] == "model_priced"
    )
    assert per_game, "no priced contracts to distribute"
    counts = sorted(per_game.values())
    assert min(counts) > 1, f"some game priced only one contract from its projection: {counts}"
    # Every projection built must have been used by at least one contract.
    assert cache.projection_builds <= len(per_game)
    assert telemetry.game_projection_count == 0  # set by main(), not _apply_scan
