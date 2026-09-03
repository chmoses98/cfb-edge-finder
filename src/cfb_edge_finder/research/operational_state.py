"""SEMANTIC RUN OUTCOME -- what a red run is allowed to mean.

*** THE PROBLEM THIS SOLVES (live, 2026-09-03) ***
The collector's exit code answered one question: "did any HIGH-severity
diagnostic fire, or was there no football state?" With the CFBD quota at
zero and the durable schedule past its 6h bound, the answer was "no
football state" on EVERY run -- so an external scheduler firing every 5
minutes produced a red GitHub Actions run, and an email, 288 times a day,
all of them saying the same already-known thing. An alert that fires on a
condition the operator has already acknowledged and cannot act on is not
an alert; it trains the operator to ignore the channel, which is
precisely how a REAL deadline failure gets missed.

*** THE RULE ***
A non-zero exit means: THE OPERATOR NEEDS TO CARE NOW.

It does NOT mean "we are degraded". Degraded-but-safe is a status, not an
incident, and it is reported in the job summary and the durable heartbeat
where a human can look at it deliberately.

Three things still fail loudly, unconditionally:
  1. any HIGH-severity health diagnostic (mapping collapse, persistence
     failure, a due CLOSING that did not land, corrupt state, ...) --
     `research/health.should_fail_run` is called exactly as before and is
     never suppressed;
  2. a deadline at risk: a checkpoint is due, or a kickoff is inside the
     window where one can become due, and trustworthy schedule evidence
     for that game does not exist;
  3. the FIRST run that enters a degraded state, or any run whose
     degraded state MATERIALLY CHANGES (a fallback that was healthy is
     now not) -- so entering degradation is always noticed once, and the
     288 identical repeats afterwards are not.

*** WHAT SUPPRESSION IS AND IS NOT ***
Suppression is keyed on (operational_state, blocker) recorded durably. It
never suppresses a different failure, never suppresses a HIGH diagnostic,
never suppresses deadline risk, and expires on its own after
`REALERT_AFTER_HOURS` so a long-lived degraded state re-reports about
once a day rather than fading into silence forever. It is deliberately
NOT `continue-on-error` and deliberately NOT a broadened exception
handler: the run still computes the same verdicts, it just answers a
better question with its exit code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from cfb_edge_finder.research.health import Diagnostic, Severity

OPERATIONAL_STATE_SCHEMA_VERSION = "operational_state_v1"
STATE_RELPATH = ("data", "research", "operational_state", "state.json")
"""Under data/research/ so git_durable_store's staging allowlist covers
it unchanged, alongside cfbd_access/football_state/schedule_state."""

HEALTHY = "HEALTHY"
"""Everything the run needed was available and nothing high-severity
fired. Exit 0, no alert."""

DEGRADED_SAFE = "DEGRADED_SAFE"
"""A known blocker is in force (typically CFBD quota exhaustion) AND a
healthy fallback is carrying the safety-critical facts AND nothing due is
being lost. Exit 0 after the entry alert."""

DEGRADED_WAITING = "DEGRADED_WAITING"
"""Capture is blocked -- no usable schedule evidence -- but nothing is
due and no kickoff is inside the deadline window, so nothing is being
lost by waiting. Exit 0 after the entry alert. This is the state Section
H describes: 'both providers temporarily unavailable, no checkpoint due,
state explicitly recorded'."""

DEADLINE_AT_RISK = "DEADLINE_AT_RISK"
"""A checkpoint is due, or a kickoff is close enough that one can become
due, and the schedule certainty required to capture it safely is not
available. ALWAYS exit 1: this is unrecoverable data loss in progress."""

INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
"""A HIGH-severity health diagnostic fired. ALWAYS exit 1, never
suppressed, never rate-limited."""

DEADLINE_RISK_HORIZON_HOURS = 8.0
"""A kickoff within this many hours is 'in the deadline window'. T_6H's
capture window opens 8h before kickoff (target 6h, half-width 2h) and is
the earliest of the final-approach buckets, so 8h is exactly the point
past which a missing schedule fact can start costing checkpoints. Wider
would cry wolf; narrower would let T_6H die quietly."""

REALERT_AFTER_HOURS = 24.0
"""A degraded state that persists this long alerts again. Bounded
re-reporting (about one a day) is the difference between 'suppressed' and
'forgotten'; the failure mode this guards against is a collector that has
been quietly not capturing for a week."""

_TERMINAL_STATES = (DEADLINE_AT_RISK, INTEGRITY_FAILURE)
_DEGRADED_STATES = (DEGRADED_SAFE, DEGRADED_WAITING)


@dataclass(frozen=True)
class RunClassification:
    """One run's semantic outcome and the exit code it justifies."""

    operational_state: str
    blocker: str | None
    deadline_risk: bool
    should_fail_run: bool
    alerting: bool
    reason: str
    high_severity_codes: tuple[str, ...] = ()
    suppressed_because: str | None = None
    summary_fields: dict = field(default_factory=dict)

    @property
    def is_degraded(self) -> bool:
        return self.operational_state in _DEGRADED_STATES

    def summary_lines(self) -> list[str]:
        lines = [f"operational_state={self.operational_state}", f"exit={1 if self.should_fail_run else 0}"]
        if self.blocker:
            lines.append(f"blocker={self.blocker}")
        lines.append(f"deadline_risk={str(self.deadline_risk).lower()}")
        for key, value in sorted(self.summary_fields.items()):
            lines.append(f"{key}={value}")
        if self.suppressed_because:
            lines.append(f"alert_suppressed={self.suppressed_because}")
        lines.append(f"reason={self.reason}")
        return lines


# ------------------------------------------------------------- durable state


def state_path(repo_dir: Path) -> Path:
    return repo_dir.joinpath(*STATE_RELPATH)


def load_state(repo_dir: Path) -> dict:
    path = state_path(repo_dir)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_state(repo_dir: Path, state: dict) -> None:
    path = state_path(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# ---------------------------------------------------------------- deadline


def deadline_risk_games(
    *,
    kickoffs_by_game_id: dict[str, datetime | None],
    trusted_game_ids: set[str] | frozenset[str],
    now: datetime,
    horizon_hours: float = DEADLINE_RISK_HORIZON_HOURS,
) -> list[str]:
    """Games inside the deadline window that do NOT have trustworthy
    schedule evidence -- the exact set whose checkpoints are at risk.

    A game already in the trusted set is not at risk however close its
    kickoff is: trusted means fresh evidence exists and the ordinary
    guards will do their job. A game with an unknown kickoff is NOT
    counted -- `resolve_due_labels` can never schedule anything for it,
    so it cannot lose a checkpoint -- while a game whose kickoff has
    passed is likewise finished, not at risk."""
    at_risk: list[str] = []
    horizon = now + timedelta(hours=horizon_hours)
    for game_id, kickoff in kickoffs_by_game_id.items():
        if kickoff is None or game_id in trusted_game_ids:
            continue
        if now < kickoff <= horizon:
            at_risk.append(game_id)
    return sorted(at_risk)


# -------------------------------------------------------------- classifier


def classify_run(
    *,
    diagnostics: list[Diagnostic],
    fail_closed: bool,
    blocker: str | None,
    deadline_risk: bool,
    prior_state: dict,
    now: datetime,
    summary_fields: dict | None = None,
    realert_after_hours: float = REALERT_AFTER_HOURS,
) -> RunClassification:
    """The single decision point for 'should this run be red?'.

    Order matters and is deliberate: integrity beats deadline beats
    degradation. A HIGH diagnostic is never reachable by suppression, and
    deadline risk is evaluated before any suppression logic runs."""
    summary_fields = dict(summary_fields or {})
    high = tuple(d.code for d in diagnostics if d.severity == Severity.HIGH)

    if high:
        return RunClassification(
            operational_state=INTEGRITY_FAILURE,
            blocker=blocker,
            deadline_risk=deadline_risk,
            should_fail_run=True,
            alerting=True,
            reason=f"high-severity diagnostics: {', '.join(high)}",
            high_severity_codes=high,
            summary_fields=summary_fields,
        )

    if deadline_risk:
        return RunClassification(
            operational_state=DEADLINE_AT_RISK,
            blocker=blocker,
            deadline_risk=True,
            should_fail_run=True,
            alerting=True,
            reason=(
                "a checkpoint is due or a kickoff is inside the deadline window and trustworthy "
                "schedule evidence is unavailable"
            ),
            summary_fields=summary_fields,
        )

    if fail_closed:
        state = DEGRADED_WAITING
        reason = "capture blocked but nothing is due and no kickoff is inside the deadline window"
    elif blocker:
        state = DEGRADED_SAFE
        reason = f"operating on a fallback under a known blocker ({blocker}); nothing due is being lost"
    else:
        return RunClassification(
            operational_state=HEALTHY,
            blocker=None,
            deadline_risk=False,
            should_fail_run=False,
            alerting=False,
            reason="all sources available; no high-severity diagnostics",
            summary_fields=summary_fields,
        )

    alerting, suppressed = _should_alert(
        state=state, blocker=blocker, prior_state=prior_state, now=now, realert_after_hours=realert_after_hours
    )
    return RunClassification(
        operational_state=state,
        blocker=blocker,
        deadline_risk=False,
        should_fail_run=alerting,
        alerting=alerting,
        reason=reason,
        suppressed_because=suppressed,
        summary_fields=summary_fields,
    )


def _should_alert(
    *, state: str, blocker: str | None, prior_state: dict, now: datetime, realert_after_hours: float
) -> tuple[bool, str | None]:
    """Alert on ENTRY to a degraded state, on any material change of what
    is blocking, and once per `realert_after_hours` thereafter. Otherwise
    stay quiet and say why."""
    last_state = prior_state.get("last_alerted_state")
    last_blocker = prior_state.get("last_alerted_blocker")
    last_at = _parse_iso(prior_state.get("last_alerted_at"))

    if last_state != state or last_blocker != blocker:
        return True, None
    if last_at is None:
        return True, None
    hours = (now - last_at).total_seconds() / 3600.0
    if hours >= realert_after_hours:
        return True, None
    return False, (
        f"identical state already alerted {hours:.1f}h ago "
        f"(state={state} blocker={blocker}); re-alerts after {realert_after_hours:.0f}h"
    )


def record_state(classification: RunClassification, prior_state: dict, *, now: datetime) -> dict:
    """The durable operational state this run leaves behind."""
    prior_operational = prior_state.get("operational_state")
    state_since = prior_state.get("state_since")
    if prior_operational != classification.operational_state or not state_since:
        state_since = now.isoformat()

    record = {
        "schema_version": OPERATIONAL_STATE_SCHEMA_VERSION,
        "operational_state": classification.operational_state,
        "state_since": state_since,
        "current_blocker": classification.blocker,
        "due_deadline_risk": classification.deadline_risk,
        "last_updated_at": now.isoformat(),
        "last_reason": classification.reason,
        "last_alerted_state": prior_state.get("last_alerted_state"),
        "last_alerted_blocker": prior_state.get("last_alerted_blocker"),
        "last_alerted_at": prior_state.get("last_alerted_at"),
    }
    if classification.alerting:
        record["last_alerted_state"] = classification.operational_state
        record["last_alerted_blocker"] = classification.blocker
        record["last_alerted_at"] = now.isoformat()
    return record


def read_state_from_git(repo_dir: Path, branch: str) -> dict:
    """Read the durable operational state from origin/{branch} WITHOUT a
    checkout -- for read-only planners. Any failure returns {}."""
    import subprocess

    rel = "/".join(STATE_RELPATH)
    try:
        subprocess.run(
            ["git", "fetch", "origin", branch, "--depth=1"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        show = subprocess.run(
            ["git", "show", f"origin/{branch}:{rel}"], cwd=repo_dir, capture_output=True, text=True, timeout=120
        )
        if show.returncode != 0:
            return {}
        loaded = json.loads(show.stdout)
        return loaded if isinstance(loaded, dict) else {}
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------- operator output

_STATE_ICON = {
    HEALTHY: ":white_check_mark:",
    DEGRADED_SAFE: ":large_blue_circle:",
    DEGRADED_WAITING: ":hourglass:",
    DEADLINE_AT_RISK: ":rotating_light:",
    INTEGRITY_FAILURE: ":x:",
}


def job_summary_markdown(classification: RunClassification) -> list[str]:
    """Concise GitHub Actions job-summary block (mission section J): the
    run's condition should be obvious without opening the log."""
    icon = _STATE_ICON.get(classification.operational_state, ":grey_question:")
    lines = [f"## {icon} {classification.operational_state}", ""]
    lines.append(f"- `exit` = `{1 if classification.should_fail_run else 0}`")
    if classification.blocker:
        lines.append(f"- `blocker` = `{classification.blocker}`")
    lines.append(f"- `deadline_risk` = `{str(classification.deadline_risk).lower()}`")
    for key, value in sorted(classification.summary_fields.items()):
        lines.append(f"- `{key}` = `{value}`")
    lines.append(f"- {classification.reason}")
    if classification.suppressed_because:
        lines.append(f"- alert suppressed: {classification.suppressed_because}")
    if classification.operational_state in _TERMINAL_STATES:
        lines.append("")
        lines.append("**This run is red because it needs an operator now.**")
    return lines
