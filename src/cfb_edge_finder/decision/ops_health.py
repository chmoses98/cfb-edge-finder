"""Week 1 operational health, as four states a person can act on.

*** WHY A SEPARATE VOCABULARY FROM week1_readiness.py ***

`week1_readiness.py` answers "is the whole system built correctly?" and
grades findings BLOCKER/HIGH/MEDIUM/LOW. That is the right shape for an
audit and the wrong shape for a Saturday morning, when the only question
is "do I need to do something right now?". This module answers that one,
in four states, and deliberately does not restate the audit.

    BLOCKED               Something is broken. Week 1 data is being lost
                          or a safety lock has failed. Act now.
    WARN                  Degraded but still collecting. Look before the
                          next kickoff window.
    PENDING_NATURAL_DATA  The machine is fine; the RESEARCH cannot
                          proceed because real settled games do not exist
                          yet. Nothing to fix -- waiting is the work.
    HEALTHY               Collecting, intact, locked.

*** WHY PENDING_NATURAL_DATA IS ITS OWN STATE ***

Because the alternative is reporting HEALTHY while the research is
blocked, or WARN for a condition no action can clear. Neither is true.
Time is the only remedy, and saying so plainly is what stops the absence
of data from being quietly filled in with something else.

*** ORDERING ***

BLOCKED > WARN > PENDING_NATURAL_DATA > HEALTHY. A broken machine
outranks a waiting one; a waiting one outranks a healthy claim, so
"HEALTHY" is never printed over an empty settlement set.

Pure functions over already-loaded inputs -- no filesystem, no network,
no clock of its own -- so every state is reachable in a test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class OpsState(StrEnum):
    HEALTHY = "HEALTHY"
    WARN = "WARN"
    BLOCKED = "BLOCKED"
    PENDING_NATURAL_DATA = "PENDING_NATURAL_DATA"


_PRECEDENCE = {
    OpsState.BLOCKED: 0,
    OpsState.WARN: 1,
    OpsState.PENDING_NATURAL_DATA: 2,
    OpsState.HEALTHY: 3,
}


@dataclass(frozen=True)
class HealthCheck:
    check_id: str
    state: OpsState
    detail: str
    remedy: str = ""
    """What a person should DO. Empty when the answer is 'nothing' --
    which is the honest remedy for PENDING_NATURAL_DATA."""


@dataclass
class OpsHealthReport:
    generated_at: datetime
    checks: list[HealthCheck] = field(default_factory=list)

    @property
    def overall_state(self) -> OpsState:
        if not self.checks:
            # No checks ran. That is not health, it is ignorance, and
            # reporting HEALTHY here would be the worst possible default.
            return OpsState.BLOCKED
        return min((c.state for c in self.checks), key=lambda s: _PRECEDENCE[s])

    @property
    def blocked_checks(self) -> list[HealthCheck]:
        return [c for c in self.checks if c.state is OpsState.BLOCKED]

    def state_counts(self) -> dict[str, int]:
        counts = {state.value: 0 for state in OpsState}
        for check in self.checks:
            counts[check.state.value] += 1
        return counts

    def to_payload(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "overall_state": self.overall_state.value,
            "state_counts": self.state_counts(),
            "checks": [
                {
                    "check_id": c.check_id,
                    "state": c.state.value,
                    "detail": c.detail,
                    "remedy": c.remedy,
                }
                for c in sorted(self.checks, key=lambda c: c.check_id)
            ],
        }

    def render(self) -> str:
        lines = [
            "=" * 74,
            f"WEEK 1 OPS HEALTH -- {self.generated_at.isoformat()}",
            "=" * 74,
        ]
        for check in sorted(self.checks, key=lambda c: (_PRECEDENCE[c.state], c.check_id)):
            lines.append(f"[{check.state.value:<20}] {check.check_id}")
            lines.append(f"    {check.detail}")
            if check.remedy:
                lines.append(f"    remedy: {check.remedy}")
        lines += ["", f"OVERALL: {self.overall_state.value}", "=" * 74]
        return "\n".join(lines) + "\n"


def check_collection_freshness(
    *, minutes_since_last_run: float | None, cadence_minutes: float
) -> HealthCheck:
    """Has the collector run recently enough for the configured cadence?

    Multiples, not absolute minutes, so tightening the cadence tightens
    this automatically instead of leaving a stale constant behind."""
    if cadence_minutes <= 0:
        return HealthCheck(
            "collection_freshness",
            OpsState.BLOCKED,
            f"cadence_minutes={cadence_minutes} is not a usable cadence",
            "Fix the workflow schedule before Week 1.",
        )
    if minutes_since_last_run is None:
        return HealthCheck(
            "collection_freshness",
            OpsState.BLOCKED,
            "No successful collection run has ever been recorded.",
            "Dispatch the capture workflow once and confirm a heartbeat lands.",
        )
    if minutes_since_last_run > cadence_minutes * 30:
        return HealthCheck(
            "collection_freshness",
            OpsState.BLOCKED,
            f"Last successful run was {minutes_since_last_run:.0f} min ago "
            f"(>30x the {cadence_minutes:.0f} min cadence). Collection has stopped.",
            "Check the workflow run log and the external scheduler, then dispatch manually.",
        )
    if minutes_since_last_run > cadence_minutes * 6:
        return HealthCheck(
            "collection_freshness",
            OpsState.WARN,
            f"Last successful run was {minutes_since_last_run:.0f} min ago "
            f"(>6x the {cadence_minutes:.0f} min cadence).",
            "Watch the next two cycles; GitHub's scheduler drifts, but not usually this far.",
        )
    return HealthCheck(
        "collection_freshness",
        OpsState.HEALTHY,
        f"Last successful run {minutes_since_last_run:.0f} min ago "
        f"(cadence {cadence_minutes:.0f} min).",
    )


def check_external_scheduler(
    *, minutes_since_external_run: float | None, expected_interval_minutes: float
) -> HealthCheck:
    """Is the INDEPENDENT clock still firing?

    GitHub's own cron delivered ~1.7% of expected runs in this
    repository's measured window, which is why an external scheduler
    exists at all. If the external clock stops, the fallback is the one
    that was already proven unreliable -- so its silence is BLOCKED, not
    a warning."""
    if minutes_since_external_run is None:
        return HealthCheck(
            "external_scheduler",
            OpsState.BLOCKED,
            "No EXTERNAL_SCHEDULE-triggered run has ever been recorded.",
            "Verify the external scheduler job is enabled and its request returns HTTP 204.",
        )
    if minutes_since_external_run > expected_interval_minutes * 6:
        return HealthCheck(
            "external_scheduler",
            OpsState.BLOCKED,
            f"Last external trigger was {minutes_since_external_run:.0f} min ago "
            f"(expected every ~{expected_interval_minutes:.0f} min). The independent clock "
            f"has stopped; only GitHub's unreliable cron remains.",
            "Check the external scheduler's job history for non-204 responses.",
        )
    if minutes_since_external_run > expected_interval_minutes * 2:
        return HealthCheck(
            "external_scheduler",
            OpsState.WARN,
            f"Last external trigger was {minutes_since_external_run:.0f} min ago "
            f"(expected every ~{expected_interval_minutes:.0f} min).",
            "Confirm the external scheduler is not rate-limited.",
        )
    return HealthCheck(
        "external_scheduler",
        OpsState.HEALTHY,
        f"External trigger seen {minutes_since_external_run:.0f} min ago.",
    )


def check_corpus_integrity(
    *, duplicate_rows: int, malformed_rows: int, non_prospective_rows: int, total_rows: int
) -> HealthCheck:
    """Append-only integrity. Any breach here poisons every downstream
    claim, so none of these degrade gracefully."""
    problems = []
    if duplicate_rows:
        problems.append(f"{duplicate_rows} duplicate observation_key rows")
    if malformed_rows:
        problems.append(f"{malformed_rows} malformed rows")
    if non_prospective_rows:
        problems.append(f"{non_prospective_rows} rows not marked PROSPECTIVE")
    if problems:
        return HealthCheck(
            "corpus_integrity",
            OpsState.BLOCKED,
            "; ".join(problems),
            "Do NOT backfill or rewrite. Investigate the writer before the next capture.",
        )
    if total_rows == 0:
        return HealthCheck(
            "corpus_integrity",
            OpsState.BLOCKED,
            "Corpus is empty.",
            "Confirm the capture workflow is writing to the research-data branch.",
        )
    return HealthCheck(
        "corpus_integrity",
        OpsState.HEALTHY,
        f"{total_rows} rows, 0 duplicates, 0 malformed, all PROSPECTIVE.",
    )


def check_closing_coverage(*, closing_due: int, closing_captured: int) -> HealthCheck:
    """CLOSING is the one checkpoint that can never be recovered: its
    window is 0 < minutes_to_kickoff <= 14 and it is never backfilled.
    A miss is permanent, so a miss is BLOCKED even though nothing is
    'broken' any more by the time it is noticed."""
    if closing_due == 0:
        return HealthCheck(
            "closing_coverage",
            OpsState.HEALTHY,
            "No CLOSING checkpoints were due in this window.",
        )
    if closing_captured == 0:
        return HealthCheck(
            "closing_coverage",
            OpsState.BLOCKED,
            f"{closing_due} CLOSING checkpoints were due and none were captured. "
            f"These cannot be recovered -- CLOSING is never backfilled.",
            "Confirm the external scheduler fired during the 14-minute pre-kickoff window.",
        )
    if closing_captured < closing_due:
        return HealthCheck(
            "closing_coverage",
            OpsState.WARN,
            f"{closing_captured} of {closing_due} CLOSING checkpoints captured. "
            f"The missed ones are permanently lost.",
            "Check trigger timing against the affected kickoffs before the next slate.",
        )
    return HealthCheck(
        "closing_coverage",
        OpsState.HEALTHY,
        f"{closing_captured}/{closing_due} CLOSING checkpoints captured.",
    )


def check_safety_locks(
    *,
    qualification_disabled: bool,
    threshold_artifact_absent: bool,
    validated_state_unreachable: bool,
    sizing_disconnected: bool,
) -> HealthCheck:
    """Every lock, checked positively. A lock that has silently opened is
    the most dangerous possible failure here, so any False is BLOCKED --
    there is no degraded mode for this check."""
    failures = []
    if not qualification_disabled:
        failures.append("qualification is NOT disabled")
    if not threshold_artifact_absent:
        failures.append("an approved threshold artifact is present")
    if not validated_state_unreachable:
        failures.append("evidence readiness can reach VALIDATED")
    if not sizing_disconnected:
        failures.append("the sizing library is reachable from the decision pipeline")
    if failures:
        return HealthCheck(
            "safety_locks",
            OpsState.BLOCKED,
            "; ".join(failures),
            "Stop. Do not run the pipeline for any decision purpose until this is understood.",
        )
    return HealthCheck(
        "safety_locks",
        OpsState.HEALTHY,
        "Qualification disabled, no approved artifact, VALIDATED unreachable, sizing disconnected.",
    )


def check_natural_data(*, settled_games: int, minimum_for_research: int | None) -> HealthCheck:
    """Whether empirical threshold research can proceed at all.

    `minimum_for_research` is the caller's stated requirement, not a
    number invented here. Passing None means no minimum has been
    established yet -- in which case the presence of ANY settled games is
    still not enough to claim research can proceed, and the check says so
    rather than guessing a bar."""
    if settled_games <= 0:
        return HealthCheck(
            "natural_data",
            OpsState.PENDING_NATURAL_DATA,
            "0 settled prospective games. EMPIRICAL THRESHOLD RESEARCH BLOCKED ON "
            "NATURAL SAMPLE SIZE.",
        )
    if minimum_for_research is None:
        return HealthCheck(
            "natural_data",
            OpsState.PENDING_NATURAL_DATA,
            f"{settled_games} settled prospective games, but no validated minimum "
            f"sample size has been established, so sufficiency cannot be claimed.",
        )
    if settled_games < minimum_for_research:
        return HealthCheck(
            "natural_data",
            OpsState.PENDING_NATURAL_DATA,
            f"{settled_games} settled prospective games, below the stated minimum of "
            f"{minimum_for_research}. EMPIRICAL THRESHOLD RESEARCH BLOCKED ON NATURAL "
            f"SAMPLE SIZE.",
        )
    return HealthCheck(
        "natural_data",
        OpsState.HEALTHY,
        f"{settled_games} settled prospective games (>= stated minimum {minimum_for_research}). "
        f"Sufficient sample EXISTS; approval of any threshold remains a separate human decision.",
    )
