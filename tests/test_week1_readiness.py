"""Week 1 readiness: schema evolution, legacy-vs-defect classification,
and the invariants that must hold when Week 1 data starts arriving.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.expression.corpus import load_contract_snapshots
from cfb_edge_finder.recommendation.eligibility import (
    EXECUTABLE_MARKET_STATUSES,
    EligibilityConfig,
    QualityPrerequisite,
    evaluate_quality_prerequisites,
)
from cfb_edge_finder.recommendation.pipeline import run_pipeline
from cfb_edge_finder.research.timing import CLOSING_WINDOW_MINUTES, is_closing_due
from cfb_edge_finder.schemas.corpus_row import CORPUS_SCHEMA_VERSION
from cfb_edge_finder.schemas.schema_evolution import (
    CORPUS_SCHEMA_V1,
    CORPUS_SCHEMA_V2,
    FieldAvailability,
    classify_field_availability,
    field_expected_in,
    schema_rank,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _row(*, schema_version, market_status, ticker="KXNCAAFGAME-EV1-HOME", captured_at=None):
    captured_at = captured_at or (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    return {
        "observation_key": ticker,
        "schema_version": schema_version,
        "observation": {
            "game_id": "g1", "kalshi_market_ticker": ticker, "family": "moneyline",
            "team": "home", "side": None, "threshold": None, "semantic_operator": None,
            "model_probability": 0.60, "executable_yes_price": 0.58, "executable_no_price": 0.44,
            "market_midpoint": 0.59, "pricing_status": "model_priced", "parse_status": "confirmed_live",
            "captured_at": captured_at, "market_status": market_status,
            "fee_status": "VERIFIED_CURRENT", "fee_schedule_version": "v1",
            "model_version": {"model_version": "m1"},
            "snapshot_timing": {"label": "T_24H", "hours_before_kickoff": 24.0},
        },
    }


def _write(tmp_path: Path, rows) -> Path:
    path = tmp_path / "obs.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


# --- schema evolution policy (section 4) ---------------------------------


def test_current_schema_version_is_v2():
    assert CORPUS_SCHEMA_VERSION == CORPUS_SCHEMA_V2


def test_market_status_expected_only_from_v2():
    assert not field_expected_in("market_status", CORPUS_SCHEMA_V1)
    assert field_expected_in("market_status", CORPUS_SCHEMA_V2)


def test_unknown_or_missing_version_ranks_oldest():
    """An unrecognised stamp must never be credited with fields it may
    not carry -- otherwise a typo'd version silently promotes legacy rows
    into 'current-schema defect' or, worse, into executability."""
    assert schema_rank(None) == -1
    assert schema_rank("research_corpus_v99") == -1
    assert not field_expected_in("market_status", "research_corpus_v99")


def test_unregistered_field_is_expected_everywhere():
    assert field_expected_in("model_probability", CORPUS_SCHEMA_V1)


@pytest.mark.parametrize(
    "version,value,expected",
    [
        (CORPUS_SCHEMA_V1, None, FieldAvailability.LEGACY_SCHEMA_FIELD_ABSENT),
        (CORPUS_SCHEMA_V2, None, FieldAvailability.CURRENT_SCHEMA_DEFECT),
        (CORPUS_SCHEMA_V2, "active", FieldAvailability.PRESENT),
        (CORPUS_SCHEMA_V1, "active", FieldAvailability.PRESENT),
    ],
)
def test_classification(version, value, expected):
    assert classify_field_availability("market_status", value, version) is expected


def test_collector_and_schema_module_agree():
    """The collector used to restate the version as a literal, so bumping
    the constant would have left written rows stamped with the old one."""
    source = (REPO_ROOT / "scripts" / "research_scan_and_capture.py").read_text(encoding="utf-8")
    assert '"research_corpus_v1"' not in source
    assert "CORPUS_SCHEMA_VERSION" in source


# --- legacy vs current defect in eligibility (section 5) -----------------


def test_legacy_row_reports_legacy_reason_not_broken_quote(tmp_path):
    loaded = load_contract_snapshots(_write(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V1, market_status=None)]))
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=NOW)
    failures = {f for r in result.eligibility_results for f in r.quality_failures}
    assert QualityPrerequisite.LEGACY_SCHEMA_MARKET_STATUS_UNAVAILABLE in failures
    assert QualityPrerequisite.MARKET_EXECUTABLE not in failures


def test_current_schema_row_missing_status_is_a_genuine_defect(tmp_path):
    loaded = load_contract_snapshots(_write(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V2, market_status=None)]))
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=NOW)
    failures = {f for r in result.eligibility_results for f in r.quality_failures}
    assert QualityPrerequisite.MARKET_EXECUTABLE in failures
    assert QualityPrerequisite.LEGACY_SCHEMA_MARKET_STATUS_UNAVAILABLE not in failures


def test_legacy_row_is_still_not_executable(tmp_path):
    """The reason got more precise; the gate did not get weaker."""
    loaded = load_contract_snapshots(_write(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V1, market_status=None)]))
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=NOW)
    assert all(r.quality_failures for r in result.eligibility_results)
    assert result.card.actionable_count == 0


def test_non_active_status_is_a_market_failure_not_a_legacy_one(tmp_path):
    loaded = load_contract_snapshots(
        _write(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V2, market_status="suspended")])
    )
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=NOW)
    failures = {f for r in result.eligibility_results for f in r.quality_failures}
    assert QualityPrerequisite.MARKET_EXECUTABLE in failures


def test_unfamiliar_status_falls_through_to_not_executable():
    """Allow-list, not deny-list: an unrecognised Kalshi status must not
    be optimistically priced just because it isn't a known-bad string."""
    assert "open" not in EXECUTABLE_MARKET_STATUSES
    assert "initialized" not in EXECUTABLE_MARKET_STATUSES
    assert EXECUTABLE_MARKET_STATUSES == frozenset({"active"})


# --- the Week 1 target state (section 6) ---------------------------------


def test_current_schema_active_row_passes_every_quality_gate(tmp_path):
    """The state Week 1 must reach: a fresh, active, current-schema,
    supported, priced row clears data quality entirely -- and is STILL
    blocked, by qualification rather than by a data problem."""
    loaded = load_contract_snapshots(_write(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V2, market_status="active")]))
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=86_400), now=NOW)
    clean = [r for r in result.eligibility_results if not r.quality_failures]
    assert clean, "no candidate cleared quality; the zero below would be uninformative"
    assert all(not r.actionable for r in clean)
    assert {r.status for r in clean} == {"QUALIFICATION_DISABLED"}
    assert result.card.actionable_count == 0
    assert result.card.entries == ()


def test_stale_quote_still_fails_even_on_current_schema(tmp_path):
    old = (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    loaded = load_contract_snapshots(
        _write(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V2, market_status="active", captured_at=old)])
    )
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(max_quote_age_seconds=3600), now=NOW)
    failures = {f for r in result.eligibility_results for f in r.quality_failures}
    assert QualityPrerequisite.QUOTE_FRESH in failures


def test_unconfigured_quote_age_never_certifies_freshness(tmp_path):
    loaded = load_contract_snapshots(_write(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V2, market_status="active")]))
    candidates = run_pipeline(loaded.snapshots, config=EligibilityConfig(), now=NOW).eligibility_results
    failures = {f for r in candidates for f in r.quality_failures}
    assert QualityPrerequisite.QUOTE_FRESH in failures


# --- CLOSING strictness (section 8) --------------------------------------


@pytest.mark.parametrize("minutes,due", [(14.0, True), (0.5, True), (0.0, False), (-1.0, False), (14.1, False)])
def test_closing_window_is_strictly_pre_kickoff(minutes, due):
    """0 and -1 are the load-bearing cases: a CLOSING row must never be
    written at or after kickoff, because unlike every numeric bucket it
    can never be legitimately recovered later."""
    assert is_closing_due(
        kickoff_utc=NOW + timedelta(minutes=minutes), now=NOW, already_captured_labels=set()
    ) is due


def test_closing_not_due_once_captured_or_game_started():
    kickoff = NOW + timedelta(minutes=5)
    assert not is_closing_due(kickoff_utc=kickoff, now=NOW, already_captured_labels={"CLOSING"})
    assert not is_closing_due(kickoff_utc=kickoff, now=NOW, already_captured_labels=set(), game_started=True)
    assert not is_closing_due(kickoff_utc=None, now=NOW, already_captured_labels=set())


def test_closing_window_is_14_minutes_and_disjoint_from_t30():
    assert CLOSING_WINDOW_MINUTES == 14.0


# --- readiness command (section 22) --------------------------------------


def _run_readiness(repo: Path, extra=()):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "week1_readiness.py"),
         "--data-repo-dir", str(repo), "--season", "2026", *extra],
        capture_output=True, text=True, timeout=600,
    )


def _corpus_repo(tmp_path: Path, rows) -> Path:
    directory = tmp_path / "repo" / "data" / "research" / "observations"
    directory.mkdir(parents=True)
    (directory / "2026.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path / "repo"


def test_readiness_flags_current_schema_defect_as_blocker(tmp_path):
    repo = _corpus_repo(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V2, market_status=None)])
    result = _run_readiness(repo)
    assert "current_schema_missing_market_status" in result.stdout
    assert "NOT WEEK 1 READY" in result.stdout
    assert result.returncode == 1


def test_readiness_treats_legacy_only_corpus_as_pending_not_blocking(tmp_path):
    repo = _corpus_repo(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V1, market_status=None)])
    result = _run_readiness(repo)
    assert "no_current_schema_observation_yet" in result.stdout
    assert "NOT WEEK 1 READY" not in result.stdout
    assert result.returncode == 0


def test_readiness_flags_duplicate_persistence(tmp_path):
    duplicate = _row(schema_version=CORPUS_SCHEMA_V2, market_status="active")
    repo = _corpus_repo(tmp_path, [duplicate, dict(duplicate)])
    result = _run_readiness(repo)
    assert "duplicate_persistence" in result.stdout
    assert result.returncode == 1


def test_readiness_reads_cadence_from_the_workflow_itself():
    from scripts.week1_readiness import cron_interval_minutes  # type: ignore[import-not-found]

    assert cron_interval_minutes(REPO_ROOT / ".github/workflows/research-capture.yml") == 10.0
    assert cron_interval_minutes(REPO_ROOT / ".github/workflows/research-settlement.yml") == 360.0


def test_readiness_never_reports_actionable_output(tmp_path):
    repo = _corpus_repo(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V2, market_status="active")])
    result = _run_readiness(repo)
    assert "ACTIONABLE                    : 0" in result.stdout
    assert "actionable_output_present" not in result.stdout


# --- provenance completeness (section 17) --------------------------------


def test_data_version_manifest_carries_fee_schedule_version():
    """Was None on every one of the 1,724 legacy rows, so the manifest
    could not say which fee schedule priced them."""
    source = (REPO_ROOT / "scripts" / "research_scan_and_capture.py").read_text(encoding="utf-8")
    assert "fee_schedule_version=None" not in source
    assert "KALSHI_FEE_SCHEDULE_2026_07_07_TAKER.version_label" in source


def test_quality_prerequisites_are_pure(tmp_path):
    """evaluate_quality_prerequisites must not mutate the candidate."""
    loaded = load_contract_snapshots(_write(tmp_path, [_row(schema_version=CORPUS_SCHEMA_V2, market_status="active")]))
    from cfb_edge_finder.recommendation.candidate import build_candidates

    candidates = build_candidates(loaded.snapshots[0], economics_by_side={}, projection_snapshot_id=None)
    before = [str(c) for c in candidates]
    for candidate in candidates:
        evaluate_quality_prerequisites(candidate, EligibilityConfig(max_quote_age_seconds=86_400), now=NOW)
    assert [str(c) for c in candidates] == before


# --- durable store: a failed fetch is not an absent branch (section 18/19/21)


def _git(args, cwd):
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin", "HOME": str(cwd),
    }
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, timeout=120)


@pytest.fixture
def remote_with_corpus(tmp_path):
    """A bare remote holding a real research-data branch with rows in it."""
    remote = tmp_path / "remote.git"
    _git(["init", "-q", "--bare", str(remote)], tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    _git(["init", "-q"], work)
    _git(["checkout", "-q", "-b", "main"], work)
    (work / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-qm", "main"], work)
    _git(["push", "-q", str(remote), "main"], work)
    _git(["checkout", "-q", "--orphan", "research-data"], work)
    _git(["rm", "-rq", "--cached", "."], work)
    (work / "code.py").unlink()
    obs = work / "data" / "research" / "observations"
    obs.mkdir(parents=True)
    (obs / "2026.jsonl").write_text("REAL-1\nREAL-2\n", encoding="utf-8")
    _git(["add", "-f", "data"], work)
    _git(["commit", "-qm", "corpus"], work)
    _git(["push", "-q", str(remote), "research-data"], work)
    return remote


def test_unreachable_remote_refuses_to_start_a_fresh_orphan(tmp_path, remote_with_corpus):
    """The regression: a transient fetch failure used to be read as
    'branch does not exist', so the run continued against an EMPTY corpus
    and reported every captured label as due again."""
    from cfb_edge_finder.research.git_durable_store import GitDurableStoreError, ensure_branch_checked_out

    consumer = tmp_path / "consumer"
    _git(["clone", "-q", str(remote_with_corpus), str(consumer)], tmp_path)
    _git(["remote", "set-url", "origin", str(tmp_path / "does-not-exist.git")], consumer)

    with pytest.raises(GitDurableStoreError) as excinfo:
        ensure_branch_checked_out(consumer, "research-data")
    assert "refusing" in str(excinfo.value).lower()


def test_existing_branch_that_cannot_be_fetched_is_an_error(tmp_path, remote_with_corpus):
    from cfb_edge_finder.research.git_durable_store import GitDurableStoreError, ensure_branch_checked_out

    consumer = tmp_path / "consumer2"
    _git(["clone", "-q", str(remote_with_corpus), str(consumer)], tmp_path)
    # Reachable remote, but the branch fetch is made to fail by asking for
    # a branch name that exists remotely under a refspec git cannot resolve
    # locally is fragile; instead point at an unreadable path, which is the
    # realistic transient case (network/auth), and assert we do not orphan.
    _git(["remote", "set-url", "origin", "/proc/self/mem/nope.git"], consumer)
    with pytest.raises(GitDurableStoreError):
        ensure_branch_checked_out(consumer, "research-data")


def test_genuinely_absent_branch_still_creates_the_orphan(tmp_path, remote_with_corpus):
    """The legitimate first-run path must keep working -- the fix must not
    turn a real cold start into a hard failure."""
    from cfb_edge_finder.research.git_durable_store import ensure_branch_checked_out

    consumer = tmp_path / "consumer3"
    _git(["clone", "-q", str(remote_with_corpus), str(consumer)], tmp_path)
    ensure_branch_checked_out(consumer, "branch-that-does-not-exist")
    # symbolic-ref, not rev-parse: --orphan leaves the branch UNBORN (no
    # commit yet), and rev-parse cannot resolve HEAD in that state.
    head = _git(["symbolic-ref", "--short", "HEAD"], consumer).stdout.strip()
    assert head == "branch-that-does-not-exist"


def test_orphan_push_cannot_destroy_an_existing_corpus(tmp_path, remote_with_corpus):
    """Belt-and-braces on the blast radius: even if an orphan run somehow
    reached the push, a non-forced push is rejected and the real rows
    survive. This is why the bug above was MEDIUM and not data loss."""
    consumer = tmp_path / "consumer4"
    _git(["clone", "-q", str(remote_with_corpus), str(consumer)], tmp_path)
    _git(["checkout", "-q", "--orphan", "research-data-local"], consumer)
    _git(["rm", "-rq", "--cached", "."], consumer)
    obs = consumer / "data" / "research" / "observations"
    obs.mkdir(parents=True, exist_ok=True)
    (obs / "2026.jsonl").write_text("FRESH-ONLY\n", encoding="utf-8")
    _git(["add", "-f", "data"], consumer)
    _git(["commit", "-qm", "orphan"], consumer)

    pushed = _git(["push", "origin", "HEAD:research-data"], consumer)
    assert pushed.returncode != 0, "a non-fast-forward push must be rejected"

    survivor = _git(["show", "research-data:data/research/observations/2026.jsonl"], tmp_path / "work")
    assert "REAL-1" in survivor.stdout and "REAL-2" in survivor.stdout


def test_dry_run_loads_the_real_corpus(tmp_path):
    """--no-push used to skip the corpus fetch entirely, so a rehearsal
    scanned an empty ledger and disagreed with the real run by 1,337
    captures. The call must no longer be conditional on no_push."""
    source = (REPO_ROOT / "scripts" / "research_scan_and_capture.py").read_text(encoding="utf-8")
    assert "if not args.no_push:\n        git_durable_store.ensure_branch_checked_out" not in source
    assert "git_durable_store.ensure_branch_checked_out(args.data_repo_dir, args.data_branch)" in source
