#!/usr/bin/env python3
"""Week 1 operational health -- the Saturday-morning question.

One verdict in four states: HEALTHY, WARN, BLOCKED, PENDING_NATURAL_DATA.
Read-only: nothing is priced, staked, ordered, dispatched, or written to
the corpus.

This is deliberately narrower than `week1_readiness.py`. That script
audits whether the system is BUILT correctly; this one answers whether it
is RUNNING and whether anything needs doing right now. The scoring logic
lives in `decision/ops_health.py` so every state is reachable in a test
without a repository on disk.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.decision.collection_protection import (  # noqa: E402
    TriggerObservation,
    assess_collection_protection,
)
from cfb_edge_finder.decision.ops_health import (  # noqa: E402
    OpsHealthReport,
    OpsState,
    check_closing_coverage,
    check_collection_protection,
    check_corpus_integrity,
    check_natural_data,
    check_safety_locks,
)
from cfb_edge_finder.research.heartbeat import (  # noqa: E402
    heartbeat_path,
    last_successful_run,
    load_heartbeats,
)
from cfb_edge_finder.schemas.settlement import MarketSettlementStatus  # noqa: E402

CAPTURE_WORKFLOW = REPO_ROOT / ".github/workflows/research-capture.yml"

def cron_interval_minutes(workflow_path: Path) -> float | None:
    """Cadence read from the workflow file so this check cannot drift
    away from the schedule it is judging."""
    if not workflow_path.exists():
        return None
    text = workflow_path.read_text(encoding="utf-8")
    intervals: list[float] = []
    for match in re.finditer(r"cron:\s*[\"']([^\"']+)[\"']", text):
        minute_field = match.group(1).split()[0]
        step = re.fullmatch(r"\*/(\d+)", minute_field)
        if step:
            intervals.append(float(step.group(1)))
        elif minute_field.isdigit():
            intervals.append(60.0)
    return min(intervals) if intervals else None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _minutes_since(moment: datetime | None, now: datetime) -> float | None:
    if moment is None:
        return None
    return (now - moment).total_seconds() / 60.0


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"__malformed__": True})
    return rows


def corpus_counts(rows: list[dict]) -> tuple[int, int, int, int]:
    """(duplicates, malformed, non_prospective, total)."""
    malformed = sum(1 for r in rows if r.get("__malformed__"))
    real = [r for r in rows if not r.get("__malformed__")]
    keys = [r.get("observation_key") for r in real]
    duplicates = len(keys) - len(set(keys))
    non_prospective = sum(1 for r in real if r.get("capture_mode") != "PROSPECTIVE")
    return duplicates, malformed, non_prospective, len(real)


def closing_counts(heartbeats: list[dict]) -> tuple[int, int]:
    """CLOSING checkpoints due and captured, summed over heartbeats.

    Heartbeats are the only honest source here: the corpus can only show
    what WAS captured, so a corpus-only view can never see a miss."""
    due = sum(int(h.get("closing_labels_due") or 0) for h in heartbeats)
    captured = sum(int(h.get("closing_labels_captured") or 0) for h in heartbeats)
    return due, captured


def probe_safety_locks() -> dict[str, bool]:
    """Positive proof of each lock, obtained by exercising the real code
    rather than reading a constant."""
    from cfb_edge_finder.decision.artifact import NO_VALIDATED_THRESHOLD_SET, load_artifact
    from cfb_edge_finder.recommendation import eligibility, evidence, thresholds

    provider_reason = eligibility.EligibilityConfig().threshold_provider.resolve(
        model_version="probe", timing_label="probe", family="moneyline"
    ).reason
    states = {
        evidence.assess_readiness(
            family="moneyline",
            timing_label="T_24H",
            model_version="m",
            settled_n=n,
            unique_game_clusters=n,
            clv_n=n,
        ).state.name
        for n in (0, 5, 30, 10_000, 10**7)
    }
    return {
        "qualification_disabled": bool(eligibility.QUALIFICATION_DISABLED),
        "threshold_artifact_absent": (
            provider_reason == thresholds.NO_VALIDATED_THRESHOLD_SET
            and load_artifact(None).status == NO_VALIDATED_THRESHOLD_SET
        ),
        "validated_state_unreachable": "VALIDATED" not in states,
        "sizing_disconnected": sizing_is_disconnected(),
    }


SIZING_PACKAGE = "cfb_edge_finder.sizing"

GUARDED_PACKAGES = (
    "decision",
    "recommendation",
    "research",
    "expression",
    "modeling",
    "analytics",
    "projections",
    "kalshi",
    "betting",
    "ingestion",
    "ratings",
    "teams",
    "schemas",
    "data",
)


def sizing_import_offenders() -> list[str]:
    """Modules on a decision path that import the sizing package.

    Parsed from the AST, never grepped. A text search matches the
    package name inside a docstring -- `recommendation/card.py` names it
    while explaining that it is NOT imported -- and would report the lock
    as broken on the strength of a comment. It also reads the source
    rather than `sys.modules`, so a module that is never executed in this
    process is still checked."""
    offenders: list[str] = []
    for package in GUARDED_PACKAGES:
        root = REPO_ROOT / "src" / "cfb_edge_finder" / package
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                offenders.append(f"{path.relative_to(REPO_ROOT)} (unparseable)")
                continue
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module)
            if any(n == SIZING_PACKAGE or n.startswith(SIZING_PACKAGE + ".") for n in names):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    return offenders


def sizing_is_disconnected() -> bool:
    return not sizing_import_offenders()


def trigger_observations(heartbeats: list[dict]) -> list[TriggerObservation]:
    """Heartbeat rows as trigger observations. Rows without a usable
    timestamp are dropped rather than defaulted -- a fabricated time would
    corrupt the measured interval, which is the one number this whole
    assessment rests on."""
    out: list[TriggerObservation] = []
    for row in heartbeats:
        moment = _parse_ts(row.get("invoked_at"))
        if moment is None:
            continue
        out.append(
            TriggerObservation(
                invoked_at=moment,
                trigger_type=str(row.get("trigger_type") or "UNKNOWN"),
                succeeded=bool(row.get("succeeded")),
            )
        )
    return out


def next_critical_checkpoint(heartbeats: list[dict]) -> tuple[datetime | None, str | None]:
    """The next critical checkpoint, taken from the most recent heartbeat
    that recorded one.

    Read from telemetry the collector already writes rather than
    recomputed here: recomputing would need a live schedule fetch, and a
    health command that silently depends on the network reports the
    network's health as if it were the system's."""
    for row in reversed(heartbeats):
        at = _parse_ts(row.get("next_critical_checkpoint_at"))
        label = row.get("next_critical_checkpoint")
        if at is not None and label:
            return at, str(label)
    return None, None


def assess_protection(heartbeats: list[dict], now: datetime):
    at, label = next_critical_checkpoint(heartbeats)
    return assess_collection_protection(
        now=now,
        last_successful_run=last_successful_run(heartbeats),
        observations=trigger_observations(heartbeats),
        next_checkpoint_at=at,
        next_checkpoint_label=label,
        # Manual Research Capture dispatch is always available to the
        # owner; it is the documented emergency fallback.
        manual_fallback_available=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--now", type=str, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument(
        "--minimum-settled-games",
        type=int,
        default=None,
        help="The sample size YOU have established as sufficient. Omitted by "
        "default because no such minimum has been validated -- the check then "
        "refuses to claim sufficiency rather than inventing a bar.",
    )
    args = parser.parse_args()

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
    report = OpsHealthReport(generated_at=now)

    heartbeats = load_heartbeats(heartbeat_path(args.data_repo_dir, args.season))
    protection = assess_protection(heartbeats, now)
    report.checks.append(check_collection_protection(protection))

    rows = load_rows(
        args.data_repo_dir / "data" / "research" / "observations" / f"{args.season}.jsonl"
    )
    duplicates, malformed, non_prospective, total = corpus_counts(rows)
    report.checks.append(
        check_corpus_integrity(
            duplicate_rows=duplicates,
            malformed_rows=malformed,
            non_prospective_rows=non_prospective,
            total_rows=total,
        )
    )

    due, captured = closing_counts(heartbeats)
    report.checks.append(check_closing_coverage(closing_due=due, closing_captured=captured))
    report.checks.append(check_safety_locks(**probe_safety_locks()))

    settlements = load_rows(
        args.data_repo_dir / "data" / "research" / "settlements" / f"{args.season}.jsonl"
    )
    # ONLY status == "settled" counts. A settlement row exists for every
    # market the settler has looked at, including ones whose game has not
    # kicked off ("pending_not_final"). Counting rows, or counting their
    # distinct game_ids, would report a settled sample that does not
    # exist -- which is exactly the fabrication this mission forbids.
    settled_games = len(
        {
            r.get("game_id")
            for r in settlements
            if r.get("game_id") and r.get("status") == MarketSettlementStatus.SETTLED.value
        }
    )
    settlement_statuses = Counter(r.get("status") for r in settlements if not r.get("__malformed__"))
    report.checks.append(
        check_natural_data(
            settled_games=settled_games, minimum_for_research=args.minimum_settled_games
        )
    )

    print(report.render(), end="")
    print("STATUS: read-only. Nothing priced, staked, ordered, dispatched, or written.")

    label_counts = Counter(
        (r.get("observation", {}).get("snapshot_timing") or {}).get("label")
        for r in rows
        if not r.get("__malformed__")
    )
    print(f"snapshot labels captured: {dict(sorted(label_counts.items(), key=lambda i: str(i[0])))}")
    print(f"settlement row statuses : {dict(sorted(settlement_statuses.items(), key=lambda i: str(i[0])))}")
    github_cadence = cron_interval_minutes(CAPTURE_WORKFLOW)
    print(
        f"github fallback cron    : {github_cadence:.0f} min (secondary only -- measured at 1.7% "
        f"delivery; never the primary clock)"
        if github_cadence
        else "github fallback cron    : UNREADABLE"
    )
    print(
        "observed trigger interval: "
        + (f"{protection.observed_interval_minutes:.0f} min "
           f"(median of {protection.interval_sample_size} gap(s), MEASURED not configured)"
           if protection.observed_interval_minutes is not None
           else "not measurable (fewer than two recorded runs)")
    )
    if protection.tighten_by is not None:
        print(f"tighten cadence by      : {protection.tighten_by.isoformat()}")
    print(f"games with status=settled: {settled_games}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report.to_payload(), indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.json_out}")

    return 1 if report.overall_state is OpsState.BLOCKED else 0


if __name__ == "__main__":
    raise SystemExit(main())
