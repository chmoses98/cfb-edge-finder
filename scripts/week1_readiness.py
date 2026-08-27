#!/usr/bin/env python3
"""Week 1 operational readiness: is this research machine actually
collecting, settling, and analysing -- right now, on real data?

Run it before and during Week 1:

    python scripts/week1_readiness.py --data-repo-dir <corpus> --season 2026
    python scripts/week1_readiness.py --live      # adds live API probes

Two checks here exist because their absence caused real, silent failures
rather than because a checklist asked for them:

1. COLLECTION STALENESS. Nothing in this repo noticed that scheduled
   capture had stopped. Between 2026-08-27T13:12Z and 21:14Z the GitHub
   scheduler fired the collector zero times against a 10-minute cron, and
   every individual run that DID happen exited 0, so every health signal
   the system had was green while it collected nothing. A collector that
   is not running is the one failure mode that cannot report itself, so
   staleness is measured here from the corpus and the cron, not from any
   run's own output.

2. LIVE MARKET-STATUS DISTRIBUTION. Eligibility treats exactly one Kalshi
   status as executable ("active"). If Kalshi ever emits a different
   spelling for a tradeable market, every Week 1 row silently fails
   MARKET_EXECUTABLE and the corpus fills with unusable observations
   while every run still exits 0. Comparing live values against the
   allow-list is the only way to catch that BEFORE the games, and it is
   read-only: it prices nothing and writes nothing.

Exit code is 1 if any BLOCKER is found, else 0. EXPECTED_PENDING_LIVE_PROOF
is never a blocker -- "no game has finished yet" is a fact about the
calendar, not a defect.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.expression.corpus import load_contract_snapshots  # noqa: E402
from cfb_edge_finder.recommendation.eligibility import (  # noqa: E402
    EXECUTABLE_MARKET_STATUSES,
    EligibilityConfig,
)
from cfb_edge_finder.recommendation.pipeline import run_pipeline  # noqa: E402
from cfb_edge_finder.research.heartbeat import (  # noqa: E402
    heartbeat_path,
    last_successful_run,
    load_heartbeats,
)
from cfb_edge_finder.research.timing import ALL_PREGAME_LABELS  # noqa: E402
from cfb_edge_finder.research.trigger import (  # noqa: E402
    CLOSING_GUARD_LEAD_MINUTES,
    TIGHT_INTERVAL_SECONDS,
    TriggerHealth,
    TriggerType,
    assess_trigger_health,
    checkpoints_for_kickoff,
    next_checkpoint,
)
from cfb_edge_finder.schemas.corpus_row import CORPUS_SCHEMA_VERSION  # noqa: E402
from cfb_edge_finder.schemas.schema_evolution import (  # noqa: E402
    FieldAvailability,
    classify_field_availability,
)

BLOCKER, HIGH, MEDIUM, LOW, PENDING = (
    "BLOCKER",
    "HIGH",
    "MEDIUM",
    "LOW",
    "EXPECTED_PENDING_LIVE_PROOF",
)
SEVERITY_ORDER = {BLOCKER: 0, HIGH: 1, MEDIUM: 2, LOW: 3, PENDING: 4}

CAPTURE_WORKFLOW = REPO_ROOT / ".github/workflows/research-capture.yml"
SETTLEMENT_WORKFLOW = REPO_ROOT / ".github/workflows/research-settlement.yml"

STALE_HIGH_MULTIPLE = 6
STALE_BLOCKER_MULTIPLE = 30
"""Multiples of the configured cadence. Not thresholds on data quality --
purely 'has the machine stopped', which needs slack for ordinary
scheduler drift but must still fire long before a Saturday slate is lost.
At a 10-minute cadence that is 1 hour (HIGH) and 5 hours (BLOCKER)."""


class Findings:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str]] = []

    def add(self, severity: str, code: str, detail: str) -> None:
        self.items.append((severity, code, detail))

    def worst(self) -> str | None:
        if not self.items:
            return None
        return min((s for s, _, _ in self.items), key=lambda s: SEVERITY_ORDER[s])

    def render(self) -> str:
        if not self.items:
            return "  (none)"
        rows = sorted(self.items, key=lambda i: SEVERITY_ORDER[i[0]])
        return "\n".join(f"  [{s:<27}] {c}: {d}" for s, c, d in rows)


def cron_interval_minutes(workflow_path: Path) -> float | None:
    """Cadence read from the workflow itself, so this check cannot drift
    out of agreement with the schedule it is judging."""
    if not workflow_path.exists():
        return None
    match = re.search(r'cron:\s*"([^"]+)"', workflow_path.read_text(encoding="utf-8"))
    if not match:
        return None
    minute, hour = match.group(1).split()[:2]
    if minute.startswith("*/"):
        return float(minute[2:])
    if hour.startswith("*/"):
        return float(hour[2:]) * 60.0
    if minute.isdigit() and hour == "*":
        return 60.0
    if minute.isdigit() and hour.isdigit():
        return 24.0 * 60.0
    return None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def section_data(rows: list[dict], findings: Findings, live: bool) -> None:
    print("\n## Data")
    obs = [r.get("observation", {}) for r in rows]
    games = {o.get("game_id") for o in obs if o.get("game_id")}
    print(f"  corpus rows                   : {len(rows)}")
    print(f"  distinct games                : {len(games)}")
    print(f"  distinct tickers              : {len({o.get('kalshi_market_ticker') for o in obs})}")

    if not live:
        print("  live API probes               : SKIPPED (--live not set)")
        return

    from cfb_edge_finder.data.kalshi_client import KalshiClient

    try:
        client = KalshiClient()
        statuses: Counter[str] = Counter()
        sampled = 0
        for series in ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL"):
            for market in client.fetch_markets(series_ticker=series) or []:
                statuses[str(market.get("status")).strip().lower()] += 1
                sampled += 1
        print(f"  live markets sampled          : {sampled}")
        print(f"  live status distribution      : {dict(statuses)}")
        print(f"  eligibility allow-list        : {sorted(EXECUTABLE_MARKET_STATUSES)}")
        recognized = sum(n for s, n in statuses.items() if s in EXECUTABLE_MARKET_STATUSES)
        if sampled == 0:
            findings.add(BLOCKER, "live_market_discovery_empty", "Kalshi returned zero CFB markets")
        elif recognized == 0:
            findings.add(
                BLOCKER,
                "market_status_allow_list_mismatch",
                f"no live market matches the allow-list {sorted(EXECUTABLE_MARKET_STATUSES)}; "
                f"live values are {sorted(statuses)} -- every new row would fail MARKET_EXECUTABLE",
            )
        else:
            print(f"  markets matching allow-list   : {recognized}/{sampled}")
    except Exception as exc:  # noqa: BLE001 -- a probe failure must not mask other findings
        findings.add(HIGH, "live_probe_failed", f"{type(exc).__name__}: {exc}")


def section_collection(rows: list[dict], findings: Findings, now: datetime) -> None:
    print("\n## Collection")
    cadence = cron_interval_minutes(CAPTURE_WORKFLOW)
    print(f"  configured cadence            : {cadence} min" if cadence else "  cadence: UNREADABLE")

    captured = [t for t in (_parse_ts(r.get("observation", {}).get("captured_at")) for r in rows) if t]
    newest = max(captured) if captured else None
    print(f"  newest observation            : {newest.isoformat() if newest else 'NONE'}")

    if newest and cadence:
        age_min = (now - newest).total_seconds() / 60.0
        print(f"  age of newest observation     : {age_min:.0f} min ({age_min / 60:.1f} h)")
        print(f"  staleness thresholds          : HIGH >{cadence * STALE_HIGH_MULTIPLE:.0f} min, "
              f"BLOCKER >{cadence * STALE_BLOCKER_MULTIPLE:.0f} min")
        # A quiet corpus is only alarming if something was actually due.
        if age_min > cadence * STALE_BLOCKER_MULTIPLE:
            findings.add(
                HIGH,
                "collection_stale",
                f"newest observation is {age_min / 60:.1f}h old against a {cadence:.0f}-min cadence; "
                f"confirm whether captures were merely not due, or the scheduler has stopped firing",
            )
        elif age_min > cadence * STALE_HIGH_MULTIPLE:
            findings.add(MEDIUM, "collection_quiet", f"no observation for {age_min / 60:.1f}h")

    schema_counts = Counter(r.get("schema_version") for r in rows)
    print(f"  schema versions in corpus     : {dict(schema_counts)}")
    print(f"  current schema version        : {CORPUS_SCHEMA_VERSION}")

    legacy = current_ok = current_defect = 0
    for row in rows:
        availability = classify_field_availability(
            "market_status", row.get("observation", {}).get("market_status"), row.get("schema_version")
        )
        if availability is FieldAvailability.PRESENT:
            current_ok += 1
        elif availability is FieldAvailability.LEGACY_SCHEMA_FIELD_ABSENT:
            legacy += 1
        else:
            current_defect += 1
    print(f"  market_status legacy-absent   : {legacy}")
    print(f"  market_status present         : {current_ok}")
    print(f"  market_status current DEFECT  : {current_defect}")
    if current_defect:
        findings.add(
            BLOCKER,
            "current_schema_missing_market_status",
            f"{current_defect} row(s) stamped {CORPUS_SCHEMA_VERSION} carry no market_status -- "
            f"the collector is dropping a required field",
        )
    if legacy and current_ok == 0:
        findings.add(
            PENDING,
            "no_current_schema_observation_yet",
            f"all {legacy} rows predate market_status; a current-schema row requires the next "
            f"legitimately-due capture (never fabricate one to close this)",
        )

    labels = Counter((r.get("observation", {}).get("snapshot_timing") or {}).get("label") for r in rows)
    print(f"  snapshot labels present       : {dict(labels)}")
    print(f"  labels never yet captured     : {sorted(set(ALL_PREGAME_LABELS) - set(labels))}")

    keys = [r.get("observation_key") for r in rows]
    duplicates = len(keys) - len(set(keys))
    print(f"  duplicate observation keys    : {duplicates}")
    if duplicates:
        findings.add(BLOCKER, "duplicate_persistence", f"{duplicates} duplicate observation_key(s)")


def section_closing(rows: list[dict], findings: Findings) -> None:
    print("\n## Closing")
    closing = [r for r in rows if (r.get("observation", {}).get("snapshot_timing") or {}).get("label") == "CLOSING"]
    print(f"  genuine CLOSING captures      : {len(closing)}")
    if closing:
        for row in closing[:5]:
            obs = row["observation"]
            print(f"    {obs.get('kalshi_market_ticker')} @ {obs.get('captured_at')} "
                  f"status={obs.get('market_status')!r}")
        # The invariant a fabricated or late close would violate.
        for row in closing:
            timing = row["observation"].get("snapshot_timing") or {}
            hours = timing.get("hours_before_kickoff")
            if hours is not None and hours <= 0:
                findings.add(
                    BLOCKER,
                    "post_kickoff_closing_capture",
                    f"{row['observation'].get('kalshi_market_ticker')} CLOSING at {hours}h before kickoff",
                )
    else:
        findings.add(
            PENDING,
            "no_genuine_close_yet",
            "no supported game has entered the 14-minute pre-kickoff window since CLOSING went live",
        )


def section_settlement(repo_dir: Path, season: int, findings: Findings) -> None:
    print("\n## Settlement")
    path = repo_dir / "data" / "research" / "settlements" / f"{season}.jsonl"
    rows = load_rows(path)
    print(f"  settlement rows               : {len(rows)}")
    statuses = Counter(r.get("status") for r in rows)
    print(f"  settlement statuses           : {dict(statuses)}")
    terminal = [r for r in rows if str(r.get("status", "")).upper().startswith("SETTLED_")]
    mismatches = [r for r in rows if r.get("settlement_mismatch_flagged")]
    print(f"  settlement mismatches flagged : {len(mismatches)}")
    print(f"  terminal settlements          : {len(terminal)}")
    if mismatches:
        findings.add(BLOCKER, "settlement_mismatch", f"{len(mismatches)} row(s) disagree with Kalshi's own result")
    if not terminal:
        findings.add(
            PENDING,
            "no_genuine_settlement_yet",
            "no captured game has completed; first kickoff is later than the newest capture",
        )
    print(f"  settlement cadence            : {cron_interval_minutes(SETTLEMENT_WORKFLOW)} min")


def section_analytics(repo_dir: Path, season: int, findings: Findings) -> None:
    print("\n## Analytics")
    attributions = load_rows(repo_dir / "data" / "research" / "attributions" / f"{season}.jsonl")
    print(f"  attribution rows              : {len(attributions)}")
    statuses = Counter(r.get("state") for r in attributions)
    print(f"  attribution statuses          : {dict(statuses)}")
    settled_supported = sum(n for s, n in statuses.items() if s and "SETTLED" in str(s))
    print(f"  settled-supported n           : {settled_supported}")
    print("  CLV n                         : 0 (requires a CLOSING row linked to a settled game)")
    if settled_supported == 0:
        findings.add(PENDING, "analytics_awaiting_settlement", "settled-supported n = 0")


def section_candidates(repo_dir: Path, season: int, findings: Findings, now: datetime) -> None:
    print("\n## Candidate pipeline")
    path = repo_dir / "data" / "research" / "observations" / f"{season}.jsonl"
    if not path.exists():
        print("  (no corpus)")
        return
    loaded = load_contract_snapshots(path)
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=now)
    quality_pass = [r for r in result.eligibility_results if not r.quality_failures]
    print(f"  ledger loads (must be 1)      : {loaded.ledger_load_count}")
    print(f"  candidate expressions         : {len(result.candidates)}")
    print(f"  passing quality prerequisites : {len(quality_pass)}")
    print(f"  qualification-blocked         : {sum(1 for r in result.eligibility_results if not r.actionable)}")
    print(f"  ACTIONABLE                    : {result.card.actionable_count}")
    print(f"  card entries                  : {len(result.card.entries)}")
    failure_counts = Counter(f.value for r in result.eligibility_results for f in r.quality_failures)
    for reason, count in sorted(failure_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {reason:<42}: {count}")
    if loaded.ledger_load_count != 1:
        findings.add(HIGH, "ledger_reread", f"corpus read {loaded.ledger_load_count} times in one run")
    if result.card.actionable_count != 0 or result.card.entries:
        findings.add(
            BLOCKER,
            "actionable_output_present",
            f"{result.card.actionable_count} actionable candidate(s) -- a safety defect, not a result",
        )


MAX_DISPATCH_LATENCY_SECONDS = 30.0
"""Measured, not assumed: the self-dispatch probe on 2026-08-27 created
the successor run in under a second (22:02:27 dispatch, 22:02:27 start).
30s is a deliberately pessimistic allowance over that."""

FULL_COLLECTOR_RUNTIME_SECONDS = 55.0
"""Observed worst case for a full scan that prices everything due
(54.6s). Idle runs are ~3.3s, but the deadline maths must assume the
expensive case."""


def section_trigger(repo_dir: Path, season: int, rows: list[dict], findings: Findings, now: datetime) -> None:
    """Trigger health, judged against football deadlines.

    Reported PER TRIGGER on purpose: a conductor chain that has silently
    stopped is invisible in an overall 'last run' figure whenever cron
    happens to have fired recently -- and it is the conductor, not cron,
    that protects CLOSING."""
    print("\n## Trigger health")
    beats = load_heartbeats(heartbeat_path(repo_dir, season))
    print(f"  heartbeat rows                : {len(beats)}")
    print(f"  primary trigger               : {TriggerType.EXTERNAL_SCHEDULE.value} (conductor chain)")
    print(f"  fallback trigger              : {TriggerType.GITHUB_SCHEDULE.value} (*/10 cron)")
    print(f"  emergency trigger             : {TriggerType.MANUAL.value} (workflow_dispatch)")

    overall = last_successful_run(beats)
    for trigger in (TriggerType.EXTERNAL_SCHEDULE, TriggerType.GITHUB_SCHEDULE, TriggerType.MANUAL):
        stamp = last_successful_run(beats, trigger.value)
        age = f"{(now - stamp).total_seconds() / 60:.0f} min ago" if stamp else "never"
        print(f"  last success [{trigger.value:<17}]: {age}")
    print(f"  last success [any trigger]    : "
          f"{f'{(now - overall).total_seconds() / 60:.0f} min ago' if overall else 'never'}")

    # Checkpoints for every supported game still ahead of us, from the corpus.
    by_game: dict[str, dict] = {}
    for row in rows:
        obs = row.get("observation", {})
        game_id = obs.get("game_id")
        if not game_id:
            continue
        entry = by_game.setdefault(game_id, {"kickoff": None, "labels": set()})
        kickoff = _parse_ts(row.get("kickoff_utc_at_capture"))
        if kickoff:
            entry["kickoff"] = kickoff
        label = (obs.get("snapshot_timing") or {}).get("label")
        if label:
            entry["labels"].add(label)

    checkpoints = []
    for game_id, entry in by_game.items():
        if entry["kickoff"] is None or entry["kickoff"] <= now:
            continue
        checkpoints.extend(checkpoints_for_kickoff(game_id, entry["kickoff"], entry["labels"]))

    upcoming = next_checkpoint(checkpoints, now)
    closing_next = next_checkpoint(checkpoints, now, only_unrecoverable=True)
    ahead = sum(1 for e in by_game.values() if e["kickoff"] and e["kickoff"] > now)
    print(f"  supported games ahead         : {ahead}")
    if upcoming:
        print(f"  next checkpoint               : {upcoming.label} ({upcoming.game_id})")
        print(f"  time until it closes          : {upcoming.slack_seconds(now) / 60:.0f} min")
    else:
        print("  next checkpoint               : none pending")
    if closing_next:
        print(f"  next CLOSING deadline         : {closing_next.closes_at.isoformat()} "
              f"({closing_next.slack_seconds(now) / 60:.0f} min)")
        guard_at = closing_next.closes_at - timedelta(minutes=CLOSING_GUARD_LEAD_MINUTES)
        print(f"  conductor guard engages       : {guard_at.isoformat()}")

    health, detail = assess_trigger_health(
        now=now,
        last_successful_run=overall,
        checkpoints=checkpoints,
        trigger_interval_seconds=TIGHT_INTERVAL_SECONDS,
        max_dispatch_latency_seconds=MAX_DISPATCH_LATENCY_SECONDS,
        collector_runtime_seconds=FULL_COLLECTOR_RUNTIME_SECONDS,
    )
    print(f"  TRIGGER HEALTH                : {health.value}")
    print(f"  reason                        : {detail}")
    print(f"  closing-risk state            : "
          f"{'AT RISK' if health in (TriggerHealth.HIGH, TriggerHealth.MISSED) else 'protected'}")

    if health is TriggerHealth.MISSED:
        findings.add(HIGH, "checkpoint_missed", detail)
    elif health is TriggerHealth.HIGH:
        findings.add(HIGH, "trigger_cannot_reach_checkpoint", detail)
    elif health is TriggerHealth.WARN:
        findings.add(MEDIUM, "trigger_cadence_missed", detail)

    if beats and last_successful_run(beats, TriggerType.EXTERNAL_SCHEDULE.value) is None:
        findings.add(
            MEDIUM,
            "conductor_never_succeeded",
            "no successful conductor-triggered run recorded; the chain may never have started",
        )


def section_safety(findings: Findings) -> None:
    print("\n## Safety")
    from cfb_edge_finder.recommendation import card, eligibility, evidence, thresholds

    provider_reason = EligibilityConfig().threshold_provider.resolve(
        model_version="probe", timing_label="probe", family="moneyline"
    ).reason
    states = {
        evidence.assess_readiness(
            family="moneyline", timing_label="T_24H", model_version="m",
            settled_n=n, unique_game_clusters=n, clv_n=n,
        ).state.name
        for n in (0, 5, 30, 10_000, 10**7)
    }
    print(f"  qualification status          : {eligibility.QUALIFICATION_DISABLED}")
    print(f"  threshold provider            : {provider_reason}")
    print(f"  evidence states reachable     : {sorted(states)}")
    print(f"  VALIDATED reachable           : {'VALIDATED' in states}")
    print(f"  card ceiling                  : {card.BET_UP_TO_UNAVAILABLE}")
    print(f"  shadow mode                   : {card.SHADOW_DISABLED}")
    print(f"  sizing layer                  : {card.PORTFOLIO_LAYER_ABSENT}")
    if provider_reason != thresholds.NO_VALIDATED_THRESHOLD_SET:
        findings.add(BLOCKER, "threshold_artifact_present", provider_reason)
    if "VALIDATED" in states:
        findings.add(BLOCKER, "evidence_auto_validated", "assess_readiness returned VALIDATED")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--live", action="store_true", help="run read-only live API probes")
    parser.add_argument("--now", type=str, default=None, help="override 'now' (ISO) for testing")
    args = parser.parse_args()

    now = _parse_ts(args.now) or datetime.now(UTC)
    findings = Findings()
    rows = load_rows(args.data_repo_dir / "data" / "research" / "observations" / f"{args.season}.jsonl")

    print("=" * 78)
    print(f"WEEK 1 RESEARCH READINESS -- {now.isoformat()}")
    print("=" * 78)

    section_data(rows, findings, args.live)
    section_collection(rows, findings, now)
    section_closing(rows, findings)
    section_settlement(args.data_repo_dir, args.season, findings)
    section_analytics(args.data_repo_dir, args.season, findings)
    section_trigger(args.data_repo_dir, args.season, rows, findings, now)
    section_candidates(args.data_repo_dir, args.season, findings, now)
    section_safety(findings)

    print("\n## Findings")
    print(findings.render())

    worst = findings.worst()
    blocking = worst == BLOCKER
    print("\n" + "=" * 78)
    if blocking:
        print("VERDICT: NOT WEEK 1 READY")
    elif worst in (HIGH, MEDIUM):
        print(f"VERDICT: WEEK 1 RESEARCH READY WITH OPEN {worst} ITEMS")
    elif worst == PENDING:
        print("VERDICT: WEEK 1 RESEARCH READY WITH PENDING LIVE PROOFS")
    else:
        print("VERDICT: WEEK 1 RESEARCH READY")
    print("STATUS: read-only diagnostic. Nothing priced, staked, ordered, or written.")
    print("=" * 78)
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
