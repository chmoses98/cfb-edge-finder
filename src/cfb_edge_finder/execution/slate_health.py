"""Operational health of one `prepare-live` run.

    HEALTHY         a slate with eligible contracts was built
    DEGRADED        it was built, but optional factual context is missing
                    (collector crashed, every source unreachable, budget hit)
    NOT_APPLICABLE  there was nothing to build, AND the catalog proves it:
                    no game on the slate date, or every game on it has
                    already kicked off
    FAILED          an actionable problem: reconciliation broke, or games
                    exist and none of their contracts is eligible

*** WHY THIS EXISTS ***
`prepare-live` used to exit 4 for every zero-eligible slate. That is right
when games exist and nothing about them is bettable -- the commonest cause is
a stale catalog, which is the fail-closed case working -- but it is also what
a scheduled run on a date with no college football returned. The workflow is
scheduled every Friday and Saturday year-round, so every off-week and every
offseason weekend would have been a red run that nobody can act on.

*** A "NO GAMES" VERDICT MUST BE EVIDENCED, NEVER INFERRED FROM EMPTINESS ***
An empty slate is NOT_APPLICABLE only when the catalog it was cut from is
itself trustworthy evidence that the date is empty:

  * the capture is complete (the milestone sweep saw everything),
  * the capture is fresh (inside the slate's own max-capture-age bar), and
  * the catalog lists at least one game kicking off AFTER the slate date,
    so its horizon demonstrably covers the date in question.

Missing any of those, the empty slate stays FAILED (exit 4) -- an empty
catalog, a stale one or a partial one could be hiding an outage.

"Everything already kicked off" is NOT_APPLICABLE only when every excluded
contract was excluded as `game_started` and nothing else: a stale quote, a
mapping failure or a closed market mixed in keeps the run red.

Nothing here touches pricing, eligibility or the slate's contents. It reads
the reconciliation the builder already produced and names the outcome.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cfb_edge_finder.execution.disposition import MechanicalStatus, parse_timestamp

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
NOT_APPLICABLE = "NOT_APPLICABLE"
FAILED = "FAILED"

EXIT_OK = 0
EXIT_STRUCTURAL = 2
EXIT_ZERO_ELIGIBLE = 4

NO_GAMES_ON_SLATE_DATE = "NO_GAMES_ON_SLATE_DATE"
SLATE_ALREADY_UNDER_WAY = "SLATE_ALREADY_UNDER_WAY"
ZERO_ELIGIBLE_CONTRACTS = "ZERO_ELIGIBLE_CONTRACTS"
EMPTY_SLATE_UNPROVEN = "EMPTY_SLATE_UNPROVEN"
RECONCILIATION_FAILED = "RECONCILIATION_FAILED"
CONTEXT_UNAVAILABLE = "FACTUAL_CONTEXT_UNAVAILABLE"

CONTEXT_RUN_RECORD = "collection_run.json"
DEGRADING_CONTEXT_VERDICTS = frozenset({"sources_unreachable", "no_events_matched", "stopped_early"})
"""Collector verdicts that mean the enrichment did not happen for a reason
worth recording. `partial` (some FCS games unmatched) and
`no_games_in_horizon` are normal and stay HEALTHY."""


def _local_date(raw: Any, tz_name: str) -> str | None:
    moment = parse_timestamp(raw)
    if moment is None:
        return None
    from zoneinfo import ZoneInfo

    try:
        return moment.astimezone(ZoneInfo(tz_name)).date().isoformat()
    except Exception:  # noqa: BLE001 - tzdata absent
        return moment.date().isoformat()


def _empty_date_is_evidenced(slate: dict[str, Any], catalog_index: dict[str, Any]) -> list[str]:
    """Return the reasons the catalog does NOT prove the slate date empty
    (an empty list means it does)."""
    gaps: list[str] = []
    slate_date = slate.get("slate_date")
    source = slate.get("source") or {}
    max_age_minutes = (slate.get("config") or {}).get("max_capture_age_minutes")
    if not slate_date:
        gaps.append("no slate date: an all-dates slate with no games means an empty catalog")
    if source.get("capture_complete") is not True:
        gaps.append("catalog capture is not complete")
    age = source.get("capture_age_seconds")
    if age is None or max_age_minutes is None or age > float(max_age_minutes) * 60.0:
        gaps.append(f"catalog capture age {age}s is outside the {max_age_minutes}-minute freshness bar")
    games = catalog_index.get("games") or []
    if not games:
        gaps.append("catalog lists no games at all")
    elif slate_date:
        tz_name = str(slate.get("timezone") or "America/New_York")
        later = [
            g for g in games if (_local_date(g.get("kickoff"), tz_name) or "") > str(slate_date)
        ]
        if not later:
            gaps.append(f"catalog lists no game after {slate_date}, so it does not prove the date is covered")
    return gaps


def read_context_run(context_dir: Path | None) -> tuple[bool, dict[str, Any] | None]:
    """(expected, record). `expected` is False when no context dir was asked for."""
    if context_dir is None:
        return False, None
    try:
        record = json.loads((Path(context_dir) / CONTEXT_RUN_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True, None
    return True, record if isinstance(record, dict) else None


def classify_slate_health(
    slate: dict[str, Any],
    catalog_index: dict[str, Any],
    *,
    structural_failure: str | None = None,
    context_expected: bool = False,
    context_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reconciliation = slate.get("reconciliation") or {}
    eligible = int(reconciliation.get("contracts_eligible") or 0)
    in_scope = int(reconciliation.get("games_in_scope") or 0)
    exclusions = {k: v for k, v in (reconciliation.get("exclusions_by_status") or {}).items() if v}

    def result(state: str, reason: str | None, exit_code: int, detail: str) -> dict[str, Any]:
        return {"state": state, "reason": reason, "exit_code": exit_code, "detail": detail}

    if structural_failure:
        return result(FAILED, RECONCILIATION_FAILED, EXIT_STRUCTURAL, structural_failure)

    if eligible == 0:
        if in_scope == 0:
            gaps = _empty_date_is_evidenced(slate, catalog_index)
            if not gaps:
                return result(
                    NOT_APPLICABLE,
                    NO_GAMES_ON_SLATE_DATE,
                    EXIT_OK,
                    f"no game on {slate.get('slate_date')}; a complete, fresh catalog lists later games",
                )
            return result(
                FAILED,
                EMPTY_SLATE_UNPROVEN,
                EXIT_ZERO_ELIGIBLE,
                "empty slate the catalog cannot vouch for: " + "; ".join(gaps),
            )
        if exclusions and set(exclusions) == {MechanicalStatus.GAME_STARTED.value}:
            return result(
                NOT_APPLICABLE,
                SLATE_ALREADY_UNDER_WAY,
                EXIT_OK,
                f"all {in_scope} game(s) on the slate have already kicked off",
            )
        return result(
            FAILED,
            ZERO_ELIGIBLE_CONTRACTS,
            EXIT_ZERO_ELIGIBLE,
            "games exist but no contract is eligible: " + json.dumps(exclusions, sort_keys=True),
        )

    if context_expected:
        if context_run is None:
            return result(
                DEGRADED,
                CONTEXT_UNAVAILABLE,
                EXIT_OK,
                "the factual-context collector left no run record; slate built without enrichment",
            )
        verdict = str(context_run.get("verdict") or "")
        if verdict in DEGRADING_CONTEXT_VERDICTS:
            return result(
                DEGRADED,
                CONTEXT_UNAVAILABLE,
                EXIT_OK,
                f"factual-context collector verdict {verdict!r}; slate built with reduced enrichment",
            )
    return result(HEALTHY, None, EXIT_OK, f"{eligible} eligible contract(s) on the slate")


def emit_github_summary(health: dict[str, Any]) -> None:
    """One annotation and one step-summary line. Silent outside Actions."""
    state = health["state"]
    line = f"execution slate health: {state}" + (f" ({health['reason']})" if health.get("reason") else "")
    line += f" -- {health['detail']}"
    if os.environ.get("GITHUB_ACTIONS") == "true":
        if state == DEGRADED:
            print(f"::warning title=Execution slate DEGRADED::{health['detail']}")
        elif state == NOT_APPLICABLE:
            print(f"::notice title=Execution slate not applicable::{health['detail']}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"- **{line}**\n")
    print(line)
