"""Preseason feature construction, with fail-loud as-of enforcement.

*** THE GUARD IS THE POINT ***

Every feature carries the season it describes and the season it is used
to predict. `PreseasonFeature.validate_for()` raises when a feature would
be applied to a season it could not have preceded. It RAISES rather than
returning False or filtering silently: a leak that produces a slightly
optimistic number is far more dangerous than one that crashes, because
the crash gets fixed and the optimistic number gets published.

*** THE STRICT RULE ***

A preseason feature for season S must be derived from data whose latest
possible observation is season S-1 or earlier. `/player/returning` for
2024 describes what returns FROM 2023 -- so its `derived_from_season` is
2023 even though CFBD indexes it under 2024. Getting that off by one
would let a feature see the season it is predicting, and it is exactly
the kind of mistake that leaves no trace in the output.

*** NO FEATURE VALUES ARE INVENTED HERE ***

This module builds features FROM supplied source rows. It contains no
fallback constants and no imputation: a team with no returning-production
row yields `None`, not a league average. Imputing a mean would quietly
assert that an unknown team is average, which is a modelling claim, not a
data-cleaning step.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from cfb_edge_finder.modeling.leakage import AsOf


class LeakageViolation(AssertionError):
    """A feature was about to be used for a season it could not precede."""


class FeatureFamily(StrEnum):
    RETURNING_PRODUCTION = "RETURNING_PRODUCTION"
    TALENT = "TALENT"
    COACHING = "COACHING"
    PRIOR_SEASON_STRENGTH = "PRIOR_SEASON_STRENGTH"


@dataclass(frozen=True)
class PreseasonFeature:
    """One team's preseason value for one season.

    `derived_from_season` is the LATEST season whose events could have
    influenced this value. `applies_to_season` is the season being
    predicted. The guard requires derived < applies, strictly."""

    team_id: str
    family: FeatureFamily
    name: str
    value: float | bool | str | None
    derived_from_season: int
    applies_to_season: int
    source_endpoint: str

    def validate_for(self, target: AsOf) -> None:
        """Raise unless this feature legitimately precedes `target`."""
        if self.applies_to_season != target.season:
            raise LeakageViolation(
                f"{self.name} for {self.team_id} applies to season "
                f"{self.applies_to_season} but was used for {target.season}"
            )
        if self.derived_from_season >= self.applies_to_season:
            raise LeakageViolation(
                f"{self.name} for {self.team_id} is derived from season "
                f"{self.derived_from_season} and applied to {self.applies_to_season}: a "
                f"preseason feature may never see the season it predicts"
            )

    @property
    def is_present(self) -> bool:
        return self.value is not None


def returning_production_features(
    rows: list[dict], *, applies_to_season: int, splits: tuple[str, ...]
) -> list[PreseasonFeature]:
    """Build returning-production features from `/player/returning` rows.

    CFBD indexes this endpoint by the season it APPLIES TO, while the
    production it describes is the prior season's. `derived_from_season`
    is therefore `applies_to_season - 1`, which is what makes the guard
    meaningful rather than decorative.

    A missing split yields a feature with `value=None`. It is not imputed
    and not dropped: a caller must decide, visibly, what to do about a
    team it knows nothing about."""
    out: list[PreseasonFeature] = []
    for row in rows:
        team = row.get("team")
        if not team:
            continue
        for split in splits:
            raw = row.get(split)
            out.append(
                PreseasonFeature(
                    team_id=str(team),
                    family=FeatureFamily.RETURNING_PRODUCTION,
                    name=f"returning_{split}",
                    value=float(raw) if isinstance(raw, (int, float)) else None,
                    derived_from_season=applies_to_season - 1,
                    applies_to_season=applies_to_season,
                    source_endpoint="/player/returning",
                )
            )
    return out


def talent_features(rows: list[dict], *, applies_to_season: int) -> list[PreseasonFeature]:
    """Build talent-composite features from `/talent` rows.

    Talent for season S is the recruiting composite as it stands entering
    S, settled during the S-1 signing cycle -- hence derived_from S-1."""
    out: list[PreseasonFeature] = []
    for row in rows:
        team = row.get("school") or row.get("team")
        if not team:
            continue
        raw = row.get("talent")
        out.append(
            PreseasonFeature(
                team_id=str(team),
                family=FeatureFamily.TALENT,
                name="talent_composite",
                value=float(raw) if isinstance(raw, (int, float, str)) and str(raw) else None,
                derived_from_season=applies_to_season - 1,
                applies_to_season=applies_to_season,
                source_endpoint="/talent",
            )
        )
    return out


def coaching_change_features(
    coaches_by_season: dict[int, dict[str, str]], *, applies_to_season: int
) -> list[PreseasonFeature]:
    """Head-coach change entering `applies_to_season`.

    Compares the coach recorded for season S against season S-1. Only
    those two seasons are read, and never S+1 -- a later season's record
    would reveal whether the hire worked out.

    A team with no S-1 record yields None rather than True: 'we have no
    prior record' is not evidence of a new coach, and defaulting it to
    True would manufacture coaching changes out of missing data."""
    current = coaches_by_season.get(applies_to_season, {})
    prior = coaches_by_season.get(applies_to_season - 1, {})
    out: list[PreseasonFeature] = []
    for team, coach in sorted(current.items()):
        previous = prior.get(team)
        changed = None if previous is None else (coach != previous)
        out.append(
            PreseasonFeature(
                team_id=team,
                family=FeatureFamily.COACHING,
                name="head_coach_changed",
                value=changed,
                derived_from_season=applies_to_season - 1,
                applies_to_season=applies_to_season,
                source_endpoint="/coaches",
            )
        )
    return out


@dataclass
class FeatureTable:
    """Features indexed for O(1) lookup by (team, name).

    Built once per season rather than scanned per game: a season is
    ~130 teams and a slate is ~800 games, so a linear scan per lookup is
    the O(n^2) shape this repository has already been bitten by."""

    applies_to_season: int
    _by_key: dict[tuple[str, str], PreseasonFeature]

    @classmethod
    def build(cls, features: list[PreseasonFeature], *, applies_to_season: int) -> FeatureTable:
        index: dict[tuple[str, str], PreseasonFeature] = {}
        for feature in features:
            if feature.applies_to_season != applies_to_season:
                raise LeakageViolation(
                    f"feature {feature.name} for {feature.team_id} applies to "
                    f"{feature.applies_to_season}, not {applies_to_season}"
                )
            index[(feature.team_id, feature.name)] = feature
        return cls(applies_to_season=applies_to_season, _by_key=index)

    def get(self, team_id: str, name: str, *, target: AsOf) -> PreseasonFeature | None:
        feature = self._by_key.get((team_id, name))
        if feature is None:
            return None
        feature.validate_for(target)
        return feature

    def coverage(self, name: str) -> tuple[int, int]:
        """(teams with a present value, teams with any row) for one
        feature. Reported so a candidate cannot be evaluated on a feature
        that is mostly missing without that being visible."""
        rows = [f for (_, n), f in self._by_key.items() if n == name]
        return sum(1 for f in rows if f.is_present), len(rows)

    def __len__(self) -> int:
        return len(self._by_key)
