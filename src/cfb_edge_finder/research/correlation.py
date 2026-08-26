"""Milestone E, Part G, mission section 18: correlation awareness.

A spread/total LADDER produces many observations from ONE game (one row
per threshold rung); treating each rung as an independent sample would
massively overstate effective sample size for any aggregate statistic
(calibration, hit rate). This module provides the three cluster keys a
report needs to compute contract-level, game-level, and family-level
counts SEPARATELY, plus a conservative effective-sample-size estimate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass


def contract_level_key(market_ticker: str) -> str:
    return market_ticker


def game_level_cluster_key(game_id: str, family: str | None) -> str:
    """One cluster per (game, family) -- e.g. all of one game's spread
    rungs collapse to one game-level unit, but its total-points ladder is
    a SEPARATE cluster (spread and total outcomes are not perfectly
    correlated with each other)."""
    return f"{game_id}::{family or 'unknown'}"


def family_level_cluster_key(family: str | None) -> str:
    return family or "unknown"


@dataclass(frozen=True)
class ClusterCounts:
    contract_level_n: int
    game_level_n: int
    family_level_n: int
    effective_sample_size: float
    """A conservative design-effect-style shrinkage: for `k` contract-level
    rows collapsing into `g` game-level clusters with an assumed
    within-game correlation, this returns `g` when rows-per-cluster is
    uniform-ish -- i.e. treats the game-level count as the safe effective
    N, never the raw contract count. See docstring below for why this is
    deliberately conservative rather than a fitted intraclass-correlation
    estimate (no real settled data exists yet to fit one)."""


def cluster_counts(rows: Iterable[tuple[str, str, str | None]]) -> ClusterCounts:
    """`rows`: iterable of (market_ticker, game_id, family). Effective
    sample size is set equal to the GAME-LEVEL cluster count -- the most
    conservative of the three denominators, deliberately chosen over
    fitting an intraclass correlation coefficient from data this codebase
    does not have yet (no settled season exists). A future milestone with
    real settled history can replace this with a fitted design effect;
    until then, under-claiming precision is the safer failure mode than
    over-claiming it."""
    contract_tickers: set[str] = set()
    game_clusters: set[str] = set()
    family_clusters: set[str] = set()
    for market_ticker, game_id, family in rows:
        contract_tickers.add(contract_level_key(market_ticker))
        game_clusters.add(game_level_cluster_key(game_id, family))
        family_clusters.add(family_level_cluster_key(family))
    return ClusterCounts(
        contract_level_n=len(contract_tickers),
        game_level_n=len(game_clusters),
        family_level_n=len(family_clusters),
        effective_sample_size=float(len(game_clusters)),
    )


def group_by_game_cluster(rows: Iterable[tuple[str, str, str | None]]) -> dict[str, list[str]]:
    """Returns {game_level_cluster_key: [market_tickers]} -- the raw
    grouping a report can use to compute a per-game_family cluster
    average before aggregating across clusters (avoids letting a game
    with 40 alt-line rungs outweigh a game with 3 in a naive mean)."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for market_ticker, game_id, family in rows:
        grouped[game_level_cluster_key(game_id, family)].append(market_ticker)
    return dict(grouped)
