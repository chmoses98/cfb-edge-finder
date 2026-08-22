"""Human-readable ingestion summary (mission spec section 10). Never hides
failures -- every count here has a corresponding list of the actual
items behind it, so "0 conflicts" is verifiably zero, not merely unreported.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cfb_edge_finder.schemas.observation import ConflictRecord


@dataclass
class IngestionSummary:
    season: int
    source_games_fetched: int = 0
    fbs_games_retained: int = 0
    non_fbs_filtered: int = 0
    canonical_teams_referenced: set[str] = field(default_factory=set)
    unresolved_team_aliases: list[str] = field(default_factory=list)
    neutral_site_games: int = 0
    postseason_games: int = 0
    duplicate_source_matches: int = 0
    conflicts: list[ConflictRecord] = field(default_factory=list)
    validation_failures: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"Ingestion summary -- season {self.season}",
            f"  source games fetched:        {self.source_games_fetched}",
            f"  FBS games retained:          {self.fbs_games_retained}",
            f"  non-FBS/unsupported filtered:{self.non_fbs_filtered:>4}",
            f"  canonical teams matched:     {len(self.canonical_teams_referenced)}",
            f"  unresolved team aliases:     {len(self.unresolved_team_aliases)}",
            f"  neutral-site games:          {self.neutral_site_games}",
            f"  postseason games:            {self.postseason_games}",
            f"  duplicate-source matches:    {self.duplicate_source_matches}",
            f"  conflicts:                   {len(self.conflicts)}",
            f"  validation failures:         {len(self.validation_failures)}",
        ]
        if self.unresolved_team_aliases:
            lines.append("  -- unresolved aliases --")
            lines.extend(f"     {alias}" for alias in self.unresolved_team_aliases)
        if self.conflicts:
            lines.append("  -- conflicts --")
            for c in self.conflicts:
                for fc in c.conflicts:
                    lines.append(f"     {c.game_id}: {fc.field} disagrees: {fc.values_by_source}")
        if self.validation_failures:
            lines.append("  -- validation failures --")
            lines.extend(f"     {failure}" for failure in self.validation_failures)
        return "\n".join(lines)
