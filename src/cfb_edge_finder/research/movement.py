"""Per-contract snapshot sequences: the raw material CLV analysis will
later need, assembled but deliberately not interpreted (mission sections
10-11).

*** WHAT THIS DELIBERATELY DOES NOT DO ***
No CLV number, no "beat the close" verdict, no edge/ROI/qualification
judgement of any kind. This milestone's job is to make sure the SEQUENCE
is preserved and legible; deciding whether a gap was good is a later
milestone's work and is explicitly out of scope here. Every function
below is a pure read over rows already in the corpus.

*** WHY MARKET AND MODEL MOVEMENT ARE KEPT SEPARATE ***
Both the market price and the model probability move between checkpoints,
for different reasons. The market moves on order flow; the model moves as
injuries surface, current-season evidence accumulates, and game metadata
firms up. A single "gap over time" series silently blends the two, and
you can no longer tell a market that drifted toward a static model from a
model that drifted toward a static market -- opposite research
conclusions from the same numbers. So `ContractMovement` carries both
series side by side and never pre-combines them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cfb_edge_finder.research.timing import ALL_PREGAME_LABELS
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow


@dataclass(frozen=True)
class SnapshotPoint:
    """One checkpoint's state for one contract."""

    label: str
    captured_at: datetime
    executable_yes_price: float | None
    executable_no_price: float | None
    market_midpoint: float | None
    model_probability: float | None
    research_probability_gap: float | None
    market_status: str | None
    hours_before_kickoff: float | None
    model_version: str | None

    @property
    def is_executable(self) -> bool:
        return self.executable_yes_price is not None or self.executable_no_price is not None


@dataclass(frozen=True)
class ContractMovement:
    """Every checkpoint captured for ONE (game, market ticker), ordered by
    the canonical checkpoint sequence rather than by insertion order."""

    game_id: str
    market_ticker: str
    points: tuple[SnapshotPoint, ...]

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(p.label for p in self.points)

    @property
    def has_closing(self) -> bool:
        return "CLOSING" in self.labels

    def point_for(self, label: str) -> SnapshotPoint | None:
        return next((p for p in self.points if p.label == label), None)

    def market_price_series(self) -> tuple[tuple[str, float | None], ...]:
        """(label, executable YES price) in checkpoint order -- the market
        half of the movement story."""
        return tuple((p.label, p.executable_yes_price) for p in self.points)

    def model_probability_series(self) -> tuple[tuple[str, float | None], ...]:
        """(label, model probability) in checkpoint order -- the MODEL
        half. Deliberately a separate series from the market one; see this
        module's docstring for why blending them destroys the distinction
        the collection regime exists to preserve."""
        return tuple((p.label, p.model_probability) for p in self.points)

    def model_probability_changed(self) -> bool:
        """True when the model's own view moved across checkpoints -- the
        thing that makes "the T_24H probability equals the T_30
        probability" an assumption worth refuting rather than relying on."""
        seen = [p.model_probability for p in self.points if p.model_probability is not None]
        return len(set(seen)) > 1


_LABEL_ORDER = {label: i for i, label in enumerate(ALL_PREGAME_LABELS)}


def _sort_key(point: SnapshotPoint) -> tuple[int, datetime]:
    """Canonical checkpoint order first, capture time as the tiebreaker so
    an unrecognised label (a future checkpoint, or a legacy one) sorts
    last but still deterministically rather than raising."""
    return (_LABEL_ORDER.get(point.label, len(_LABEL_ORDER)), point.captured_at)


def _to_point(row: ResearchCorpusRow) -> SnapshotPoint:
    obs = row.observation
    return SnapshotPoint(
        label=obs.snapshot_timing.label,
        captured_at=obs.captured_at,
        executable_yes_price=obs.executable_yes_price,
        executable_no_price=obs.executable_no_price,
        market_midpoint=obs.market_midpoint,
        model_probability=obs.model_probability,
        research_probability_gap=obs.research_probability_gap,
        market_status=obs.market_status,
        hours_before_kickoff=obs.snapshot_timing.hours_before_kickoff,
        model_version=obs.model_version.model_version if obs.model_version else None,
    )


def build_contract_movements(rows: list[ResearchCorpusRow]) -> list[ContractMovement]:
    """Group corpus rows into one movement sequence per (game, ticker).

    Rows that never mapped to a game are grouped under 'unmapped' rather
    than dropped -- an unmapped contract's price history is still a real
    observation, and silently discarding it would understate collection
    coverage."""
    grouped: dict[tuple[str, str], list[SnapshotPoint]] = {}
    for row in rows:
        key = (row.observation.game_id or "unmapped", row.observation.kalshi_market_ticker)
        grouped.setdefault(key, []).append(_to_point(row))

    movements = []
    for (game_id, ticker), points in sorted(grouped.items()):
        movements.append(
            ContractMovement(
                game_id=game_id,
                market_ticker=ticker,
                points=tuple(sorted(points, key=_sort_key)),
            )
        )
    return movements


def coverage_by_label(movements: list[ContractMovement]) -> dict[str, int]:
    """How many contracts captured each checkpoint. The headline
    collection-completeness view: a label with far fewer contracts than
    its neighbours points at a cadence or window problem."""
    counts = dict.fromkeys(ALL_PREGAME_LABELS, 0)
    for movement in movements:
        for label in set(movement.labels):
            if label in counts:
                counts[label] += 1
    return counts
