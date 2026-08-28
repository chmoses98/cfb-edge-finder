"""Load the historical research cache into evaluable structures.

*** THE CACHE IS THE ONLY INPUT ***

Nothing here calls CFBD. The research must be reproducible without
repeatedly hitting a rate-limited API, and an experiment that could
silently re-fetch would produce results nobody can reproduce later.

*** SEASON ALIGNMENT IS THE WHOLE GAME ***

`/player/returning` and `/talent` are indexed by the season they APPLY
to, while the information they carry is the PRIOR season's. Every
feature built here is therefore stamped `derived_from_season = S - 1`,
and `features.PreseasonFeature.validate_for()` raises if that is ever
violated. An off-by-one here is invisible in the output and would
quietly hand the model the season it is predicting.

*** FBS-vs-FBS IS THE EVALUATION POPULATION ***

Matching the control's own scope and the preregistered protocol. FCS
games are loaded (the ratings fit uses them) but never scored, because
they are not priced for research and must not drive model selection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cfb_edge_finder.research.preseason.features import (
    FeatureTable,
    PreseasonFeature,
    coaching_change_features,
    returning_production_features,
    talent_features,
)

RETURNING_SPLITS = (
    "percentPPA",
    "percentPassingPPA",
    "percentRushingPPA",
    "percentReceivingPPA",
    "usage",
    "passingUsage",
)
"""The splits Candidate A tests. `percentPassingPPA` is what the control
already uses as its QB proxy; the rest are the incremental question."""


@dataclass(frozen=True)
class HistoricalGame:
    """One completed game, from the cache."""

    game_id: str
    season: int
    week: int
    home_team: str
    away_team: str
    home_points: int
    away_points: int
    neutral_site: bool
    home_classification: str | None
    away_classification: str | None

    @property
    def both_fbs(self) -> bool:
        return self.home_classification == "fbs" and self.away_classification == "fbs"

    @property
    def home_margin(self) -> int:
        return self.home_points - self.away_points

    @property
    def total_points(self) -> int:
        return self.home_points + self.away_points

    @property
    def home_won(self) -> bool:
        """A zero margin resolves to AWAY, matching research/settlement.py."""
        return self.home_margin > 0


@dataclass
class SeasonCache:
    season: int
    games: list[HistoricalGame] = field(default_factory=list)
    returning_rows: list[dict] = field(default_factory=list)
    talent_rows: list[dict] = field(default_factory=list)
    coach_rows: list[dict] = field(default_factory=list)

    @property
    def fbs_games(self) -> list[HistoricalGame]:
        return [g for g in self.games if g.both_fbs]

    def week_1_games(self) -> list[HistoricalGame]:
        return [g for g in self.fbs_games if g.week <= 1]


class CacheUnavailable(RuntimeError):
    """The research cache is missing or unusable."""


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) else None


def load_season(path: Path) -> SeasonCache:
    """Load one cached season. Rows without both scores are dropped: an
    incomplete game has no outcome to evaluate against."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    season = int(payload["season"])
    games: list[HistoricalGame] = []
    for row in payload.get("games", []):
        home_points = _int_or_none(row.get("homePoints"))
        away_points = _int_or_none(row.get("awayPoints"))
        week = _int_or_none(row.get("week"))
        home, away = row.get("homeTeam"), row.get("awayTeam")
        if home_points is None or away_points is None or week is None or not home or not away:
            continue
        games.append(
            HistoricalGame(
                game_id=str(row.get("id")),
                season=season,
                week=week,
                home_team=str(home),
                away_team=str(away),
                home_points=home_points,
                away_points=away_points,
                neutral_site=bool(row.get("neutralSite")),
                home_classification=row.get("homeClassification"),
                away_classification=row.get("awayClassification"),
            )
        )
    return SeasonCache(
        season=season,
        games=games,
        returning_rows=payload.get("returning_production", []),
        talent_rows=payload.get("talent", []),
        coach_rows=payload.get("coaches", []),
    )


def load_cache(cache_dir: Path) -> dict[int, SeasonCache]:
    """Load every cached season, keyed by season."""
    if not cache_dir.exists():
        raise CacheUnavailable(f"no research cache at {cache_dir}")
    seasons: dict[int, SeasonCache] = {}
    for path in sorted(cache_dir.glob("*.json")):
        if path.name == "manifest.json":
            continue
        cache = load_season(path)
        seasons[cache.season] = cache
    if not seasons:
        raise CacheUnavailable(f"research cache {cache_dir} contains no season files")
    return seasons


def build_feature_tables(seasons: dict[int, SeasonCache]) -> dict[int, FeatureTable]:
    """Build one leakage-guarded feature table per season.

    Coaching needs season S-1's roster of coaches, so a season whose
    predecessor is absent from the cache yields no coaching feature
    rather than a fabricated one -- `coaching_change_features` returns
    None for a team with no prior record, and a wholly missing prior
    season simply produces no comparison."""
    coaches_by_season = {
        s: {str(r.get("school")): str(r.get("coach")) for r in cache.coach_rows if r.get("school")}
        for s, cache in seasons.items()
    }

    tables: dict[int, FeatureTable] = {}
    for season, cache in sorted(seasons.items()):
        features: list[PreseasonFeature] = []
        features.extend(
            returning_production_features(
                cache.returning_rows, applies_to_season=season, splits=RETURNING_SPLITS
            )
        )
        features.extend(talent_features(cache.talent_rows, applies_to_season=season))
        features.extend(
            coaching_change_features(coaches_by_season, applies_to_season=season)
        )
        tables[season] = FeatureTable.build(features, applies_to_season=season)
    return tables


@dataclass(frozen=True)
class CorpusSummary:
    seasons: tuple[int, ...]
    total_games: int
    fbs_games: int
    week_1_fbs_games: int
    returning_rows: int
    talent_rows: int
    coach_rows: int

    def to_dict(self) -> dict:
        return {
            "seasons": list(self.seasons),
            "total_games": self.total_games,
            "fbs_vs_fbs_games": self.fbs_games,
            "week_1_fbs_vs_fbs_games": self.week_1_fbs_games,
            "returning_rows": self.returning_rows,
            "talent_rows": self.talent_rows,
            "coach_rows": self.coach_rows,
        }


def summarize(seasons: dict[int, SeasonCache]) -> CorpusSummary:
    return CorpusSummary(
        seasons=tuple(sorted(seasons)),
        total_games=sum(len(c.games) for c in seasons.values()),
        fbs_games=sum(len(c.fbs_games) for c in seasons.values()),
        week_1_fbs_games=sum(len(c.week_1_games()) for c in seasons.values()),
        returning_rows=sum(len(c.returning_rows) for c in seasons.values()),
        talent_rows=sum(len(c.talent_rows) for c in seasons.values()),
        coach_rows=sum(len(c.coach_rows) for c in seasons.values()),
    )
