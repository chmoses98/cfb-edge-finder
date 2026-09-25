"""Which research workflows are HIBERNATED, and who may still start them.

*** WHY THIS EXISTS ***

The 2026-09-17 market-discovery pivot hibernated the research collector
and its conductor by commenting out their `schedule:` blocks. That stopped
GitHub's cron and nothing else. An independent cron-job.org job
(docs/EXTERNAL_SCHEDULER.md) kept POSTing `workflow_dispatch` to
research-capture.yml every five minutes with a fine-grained PAT, and on
2026-09-25 every one of those runs was failing on CFBD_QUOTA_EXHAUSTED /
FOOTBALL_STATE_STALE_HARD / DEADLINE_AT_RISK -- one failure email every
five minutes, for a model nothing reads.

A commented-out cron is a statement about ONE trigger. Hibernation is a
statement about the WORKFLOW: no unattended caller may run it. This module
is where that statement lives, so that

  * CI can refuse a change that gives a hibernated workflow any automatic
    trigger (tests/test_hibernated_workflow_triggers.py), and
  * the workflow itself can refuse an unattended dispatch at run time
    (scripts/hibernation_gate.py) -- the one path CI cannot see, because
    the caller lives outside the repository.

*** WHAT IS STILL ALLOWED ***

A human pressing "Run workflow". Hibernated is not deleted: every script,
test and datum is intact and a person can run any of this today.

*** HOW TO REACTIVATE ***

An explicit operator decision, in one reviewed change: set the workflow's
entry below to ACTIVE, uncomment its `schedule:` block if it should have
one, and update the regression tests that will fail naming it. Check CFBD
quota/credentials and football-state freshness FIRST -- see the checklist
in docs/EXTERNAL_SCHEDULER.md.

Stdlib only: the gate runs on a bare runner before any `pip install`.
"""

from __future__ import annotations

from dataclasses import dataclass

HIBERNATED = "HIBERNATED"
ACTIVE = "ACTIVE"

HIBERNATED_SINCE = "2026-09-17"
HIBERNATION_REASON = (
    "market-discovery pivot: the CFB projection model is retired from the live path; "
    "the live path is kalshi-market-catalog.yml (docs/MODEL_RETIREMENT_2026.md)"
)

WORKFLOW_LIFECYCLE: dict[str, str] = {
    "research-capture.yml": HIBERNATED,
    "research-collection-conductor.yml": HIBERNATED,
    "research-settlement.yml": HIBERNATED,
    "live-info-sidecar.yml": HIBERNATED,
}
"""Every research workflow whose schedule was retired by the pivot.

Only the first two have an unattended DISPATCH path (the external
scheduler, the conductor's self-handoff) and so carry the run-time gate;
the other two can only ever be started by cron, which CI already forbids
for anything listed here."""

UNATTENDED_TRIGGER_SOURCES = frozenset({"EXTERNAL_SCHEDULE"})
"""Self-declared provenance that means "no human pressed this"."""

BOT_ACTOR_PREFIX = "github-actions"
"""The conductor dispatches with the workflow's GITHUB_TOKEN, so its runs
(collector dispatches AND successor conductors) carry this actor."""


def lifecycle(workflow_file: str) -> str:
    """ACTIVE unless explicitly listed. Unlisted is ACTIVE on purpose: the
    list is of things deliberately switched OFF, so a new workflow is never
    silently gated by omission."""
    return WORKFLOW_LIFECYCLE.get(workflow_file, ACTIVE)


def is_hibernated(workflow_file: str) -> bool:
    return lifecycle(workflow_file) == HIBERNATED


def hibernated_workflows() -> list[str]:
    return sorted(name for name, state in WORKFLOW_LIFECYCLE.items() if state == HIBERNATED)


@dataclass(frozen=True)
class GateDecision:
    proceed: bool
    reason: str


def decide(
    *,
    workflow_file: str,
    event_name: str | None,
    actor: str | None,
    trigger_source: str | None = None,
    dry_run: bool = False,
) -> GateDecision:
    """May this run of `workflow_file` do any work?

    Deny-by-default for everything unattended while hibernated; a human
    `workflow_dispatch` always proceeds. The order matters only for the
    reason text -- every refusal branch is independent."""
    if not is_hibernated(workflow_file):
        return GateDecision(True, f"{workflow_file} is {ACTIVE}")

    source = (trigger_source or "").strip().upper()
    who = (actor or "").strip()

    if event_name != "workflow_dispatch":
        return GateDecision(
            False,
            f"{workflow_file} is {HIBERNATED}; refusing a {event_name or 'unknown'!s} event -- "
            "only a human workflow_dispatch may run it",
        )
    if source in UNATTENDED_TRIGGER_SOURCES:
        return GateDecision(
            False,
            f"{workflow_file} is {HIBERNATED}; refusing a dispatch declaring trigger_source={source}. "
            "An external scheduler is still calling this workflow -- disable it "
            "(docs/EXTERNAL_SCHEDULER.md)",
        )
    if who.startswith(BOT_ACTOR_PREFIX):
        return GateDecision(
            False,
            f"{workflow_file} is {HIBERNATED}; refusing a dispatch by {who} "
            "(conductor chain / workflow-to-workflow dispatch)",
        )
    if workflow_file == "research-collection-conductor.yml" and not dry_run:
        # A conductor exists ONLY to dispatch the collector unattended and
        # hand off to successors. A live manual run would sleep for hours
        # and dispatch collector runs the collector's own gate refuses, so
        # the only live-conductor outcome while hibernated is waste. The
        # plan-only dry run stays available.
        return GateDecision(
            False,
            f"{workflow_file} is {HIBERNATED}; a live conductor run would start unattended "
            "collection. Run with dry_run=true to see the plan, or reactivate deliberately",
        )
    return GateDecision(True, f"{workflow_file} is {HIBERNATED}; manual run by {who or 'a human'} allowed")
