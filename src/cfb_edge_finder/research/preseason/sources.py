"""Preseason feature sources, classified by what was KNOWABLE and when.

*** THE ONE QUESTION THIS MODULE ANSWERS ***

    For a game in season S, could this value have been known before that
    season's first snap -- using only data as it stood at the time, not
    as the API reports it today?

A feature that cannot answer that defensibly is UNUSABLE, and it is
recorded as unusable rather than quietly dropped. Publishing the rejects
is the point: an unlisted feature looks like an oversight, a listed one
is a decision with a reason.

*** THE FAILURE MODE THAT MATTERS MOST HERE ***

Not "did I filter by date" but "does this endpoint return a REVISED
value?". CFBD serves current state, not a historical snapshot. A roster
queried today reflects every subsequent transfer; a season-aggregate
statistic silently includes games after the prediction point. Neither is
fixed by filtering on season, because the leak is inside the value
itself. `RevisionRisk` below names that hazard separately from timing.

*** BUILT ON MILESTONE C'S AUDIT, NOT BESIDE IT ***

docs/MILESTONE_C.md section 1 already classified /games,
/stats/game/advanced, /stats/season, /player/returning, /talent,
/ratings/*, /lines, /roster and /coaches. Those verdicts are carried
forward here verbatim in `MILESTONE_C_VERDICT`, and this module extends
them to the families the preseason-prior mission adds. Re-deriving them
would create a second audit that could disagree with the first.

*** WHY SEVERAL ENTRIES SAY 'UNCONFIRMED' ***

CFBD's own documentation domains are blocked from this environment (a
constraint documented since Milestone B). Where the pre/post-week
semantics of an endpoint could not be confirmed from primary sources,
the honest classification is UNCONFIRMED -- not an optimistic guess. An
unconfirmed timing semantic is a leakage risk, and this research treats
it as disqualifying until someone can confirm it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class KnownBeforeSeason(StrEnum):
    """Whether the value existed before the season's first game."""

    YES = "YES"
    """Published in the offseason from prior-season data."""

    NO = "NO"
    """Generated during or after the season. Postgame by nature."""

    PARTIAL = "PARTIAL"
    """A preseason baseline exists but changes in-season (rosters)."""

    UNCONFIRMED = "UNCONFIRMED"
    """Could not be established from primary sources. Treated as unsafe."""


class RevisionRisk(StrEnum):
    """Whether today's API answer differs from the as-of-then answer.

    Separate from timing on purpose: a field can be conceptually
    preseason and still be served as a revised current value, in which
    case filtering by season does nothing."""

    IMMUTABLE = "IMMUTABLE"
    """Final scores, schedule metadata. Once true, always true."""

    SNAPSHOT_STABLE = "SNAPSHOT_STABLE"
    """Published once for a season and not restated afterwards."""

    RETROACTIVELY_REVISED = "RETROACTIVELY_REVISED"
    """Today's answer reflects later events. Cannot be reconstructed
    as-of without a historical snapshot nobody kept."""

    UNKNOWN = "UNKNOWN"


class Verdict(StrEnum):
    USABLE = "USABLE"
    """Leakage-safe as a preseason feature, subject to the as-of guard."""

    USABLE_EVALUATION_ONLY = "USABLE_EVALUATION_ONLY"
    """May benchmark the model; must never become a model input."""

    UNUSABLE_TIMING_UNCONFIRMED = "UNUSABLE_TIMING_UNCONFIRMED"
    UNUSABLE_POSTGAME = "UNUSABLE_POSTGAME"
    UNUSABLE_RETROACTIVE_REVISION = "UNUSABLE_RETROACTIVE_REVISION"
    UNAVAILABLE_NO_SOURCE = "UNAVAILABLE_NO_SOURCE"
    """No dependable historical source exists. Recorded, not fabricated."""


@dataclass(frozen=True)
class SourceAudit:
    """One candidate feature family's provenance verdict."""

    family: str
    endpoint: str
    known_before_season: KnownBeforeSeason
    revision_risk: RevisionRisk
    verdict: Verdict
    seasons_believed_available: str
    rationale: str

    @property
    def usable_as_model_feature(self) -> bool:
        return self.verdict is Verdict.USABLE

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "endpoint": self.endpoint,
            "known_before_season": self.known_before_season.value,
            "revision_risk": self.revision_risk.value,
            "verdict": self.verdict.value,
            "seasons_believed_available": self.seasons_believed_available,
            "usable_as_model_feature": self.usable_as_model_feature,
            "rationale": self.rationale,
        }


SOURCE_AUDIT: tuple[SourceAudit, ...] = (
    SourceAudit(
        family="returning_production_passing",
        endpoint="/player/returning",
        known_before_season=KnownBeforeSeason.YES,
        revision_risk=RevisionRisk.SNAPSHOT_STABLE,
        verdict=Verdict.USABLE,
        seasons_believed_available="all seasons in CFBD coverage",
        rationale=(
            "Published in the offseason from the PRIOR season's completed data. Already the "
            "control's QB-continuity proxy, so its timing is the one preseason semantic this "
            "repository has previously relied on."
        ),
    ),
    SourceAudit(
        family="returning_production_broader",
        endpoint="/player/returning",
        known_before_season=KnownBeforeSeason.YES,
        revision_risk=RevisionRisk.SNAPSHOT_STABLE,
        verdict=Verdict.USABLE,
        seasons_believed_available="all seasons in CFBD coverage",
        rationale=(
            "Total/rushing/receiving PPA and usage splits ride the same endpoint and the same "
            "publication timing as the passing split already in use. The control uses only the "
            "passing share, so the broader splits are the most defensible first candidate: same "
            "source, same timing, no new leakage surface."
        ),
    ),
    SourceAudit(
        family="talent_composite",
        endpoint="/talent",
        known_before_season=KnownBeforeSeason.YES,
        revision_risk=RevisionRisk.SNAPSHOT_STABLE,
        verdict=Verdict.USABLE,
        seasons_believed_available="all seasons in CFBD coverage",
        rationale=(
            "Recruiting-composite talent is settled by the offseason signing cycle. Milestone C "
            "audited it as available and leakage-safe and deliberately did NOT wire it in, to "
            "avoid piling features onto an unvalidated baseline. It is a legitimate candidate."
        ),
    ),
    SourceAudit(
        family="qb_identity",
        endpoint="/roster (+ depth chart)",
        known_before_season=KnownBeforeSeason.PARTIAL,
        revision_risk=RevisionRisk.RETROACTIVELY_REVISED,
        verdict=Verdict.UNUSABLE_RETROACTIVE_REVISION,
        seasons_believed_available="n/a",
        rationale=(
            "A roster queried today reflects every subsequent transfer, injury and depth-chart "
            "change; there is no as-of snapshot. Worse, no depth-chart source identifies the "
            "EXPECTED starter before Week 1. Reconstructing 'who was expected to start' from "
            "today's data would import the outcome into the feature. Continuity PROXIES are "
            "testable; QB identity is not."
        ),
    ),
    SourceAudit(
        family="transfer_portal",
        endpoint="none with historical snapshots",
        known_before_season=KnownBeforeSeason.PARTIAL,
        revision_risk=RevisionRisk.RETROACTIVELY_REVISED,
        verdict=Verdict.UNAVAILABLE_NO_SOURCE,
        seasons_believed_available="n/a",
        rationale=(
            "Portal activity is continuous and current rankings are restated as players move. "
            "Applying today's portal view to a 2021 preseason would be exactly the backward leak "
            "the mission forbids. Documented as unavailable rather than approximated."
        ),
    ),
    SourceAudit(
        family="coaching_change",
        endpoint="/coaches",
        known_before_season=KnownBeforeSeason.YES,
        revision_risk=RevisionRisk.SNAPSHOT_STABLE,
        verdict=Verdict.USABLE,
        seasons_believed_available="believed broad; NOT independently verified",
        rationale=(
            "Head-coach-by-season records are season-scoped and a hire is public before the "
            "season. Milestone C listed /coaches as not separately audited, so the endpoint's "
            "exact shape must be confirmed on first fetch -- the TIMING is defensible, the "
            "SCHEMA is not yet verified. Coordinator changes are a separate, weaker claim and "
            "are not included here."
        ),
    ),
    SourceAudit(
        family="preseason_ratings_sp_elo_srs",
        endpoint="/ratings/sp, /ratings/elo, /ratings/srs",
        known_before_season=KnownBeforeSeason.UNCONFIRMED,
        revision_risk=RevisionRisk.UNKNOWN,
        verdict=Verdict.UNUSABLE_TIMING_UNCONFIRMED,
        seasons_believed_available="varies",
        rationale=(
            "Milestone C found it ambiguous whether a given week's rating is pre- or post- that "
            "week's games, and could not resolve it because CFBD's documentation domains are "
            "blocked from this environment. That ambiguity is unchanged. An unconfirmed timing "
            "semantic on a team-strength rating is the highest-value leak available, so it stays "
            "disqualified until confirmed -- not adopted hopefully."
        ),
    ),
    SourceAudit(
        family="historical_betting_lines",
        endpoint="/lines",
        known_before_season=KnownBeforeSeason.NO,
        revision_risk=RevisionRisk.IMMUTABLE,
        verdict=Verdict.USABLE_EVALUATION_ONLY,
        seasons_believed_available="varies by provider",
        rationale=(
            "Closing lines finalise near kickoff, so they are not preseason information at all. "
            "Legitimate as a BENCHMARK for how hard each game was to forecast. Feeding a line "
            "into a model whose purpose is to disagree with that line would make the comparison "
            "circular, so it must never become an input."
        ),
    ),
    SourceAudit(
        family="weather",
        endpoint="NWS/NOAA or Visual Crossing",
        known_before_season=KnownBeforeSeason.NO,
        revision_risk=RevisionRisk.UNKNOWN,
        verdict=Verdict.UNUSABLE_POSTGAME,
        seasons_believed_available="historical reconstruction possible via Visual Crossing",
        rationale=(
            "Game-day weather is not preseason information and belongs to a different research "
            "question (totals error), which the mission itself says to keep separate. A "
            "historical PREGAME FORECAST -- what was predicted days before, which is what a "
            "pregame model could have used -- is not obtainable; only realised conditions are, "
            "and those are postgame. Excluded from preseason-prior research."
        ),
    ),
    SourceAudit(
        family="injuries_suspensions",
        endpoint="none",
        known_before_season=KnownBeforeSeason.PARTIAL,
        revision_risk=RevisionRisk.UNKNOWN,
        verdict=Verdict.UNAVAILABLE_NO_SOURCE,
        seasons_believed_available="n/a",
        rationale=(
            "College football has no mandatory injury report and no structured historical API. "
            "Constructing labels from archived reporting would be a hindsight exercise. "
            "Documented as a live-context blind spot, which is the honest outcome."
        ),
    ),
    SourceAudit(
        family="prior_season_final_scores",
        endpoint="/games",
        known_before_season=KnownBeforeSeason.YES,
        revision_risk=RevisionRisk.IMMUTABLE,
        verdict=Verdict.USABLE,
        seasons_believed_available="all",
        rationale=(
            "The control's entire Week 1 point estimate already rests on this. Included so the "
            "audit describes the control as well as the candidates."
        ),
    ),
)

MILESTONE_C_VERDICT = {
    "/games": "scores postgame; schedule metadata pregame -- USED",
    "/stats/game/advanced": "postgame; only `plays` used, filtered to strictly-prior games",
    "/stats/season": "HIGH leakage mid-season; NOT used",
    "/player/returning": "preseason-published; USED as QB-continuity proxy",
    "/talent": "preseason-safe; documented but NOT wired into V1",
    "/ratings/elo|sp|srs|fpi": "pre/post-week semantics UNCONFIRMED; NOT used",
    "/lines": "evaluation-only, never a model input",
    "/roster": "in-season churn; NOT used",
    "/coaches": "not separately audited; NOT used",
}
"""Milestone C section 1's verdicts, carried forward verbatim so this
audit extends the previous one instead of competing with it."""


def usable_families() -> tuple[str, ...]:
    return tuple(sorted(a.family for a in SOURCE_AUDIT if a.usable_as_model_feature))


def rejected_families() -> dict[str, str]:
    return {
        a.family: a.verdict.value for a in SOURCE_AUDIT if not a.usable_as_model_feature
    }


def audit_payload() -> dict:
    return {
        "milestone_c_carried_forward": MILESTONE_C_VERDICT,
        "sources": [a.to_dict() for a in SOURCE_AUDIT],
        "usable_as_model_features": list(usable_families()),
        "rejected": rejected_families(),
    }
