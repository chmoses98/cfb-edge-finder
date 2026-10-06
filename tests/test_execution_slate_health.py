"""prepare-live's operational verdict (execution/slate_health.py).

The scheduled CFB Execution Slate workflow runs every Friday and Saturday
year-round. A date with no college football, or a run after every game on
the date has kicked off, must be green -- but only when the catalog PROVES
it. An empty slate the catalog cannot vouch for, and a slate whose games
exist but whose contracts are all ineligible, stay red (exit 4).

Every test pins `--as-of`; nothing depends on the real calendar or on the
committed live catalog.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.execution import slate_health
from cfb_edge_finder.execution.cli import main
from tests.execution_fakes import CAPTURED_AT, NOW, catalog_dir

# The fake catalog's single game kicks off 2026-09-19T23:30Z (19:30 Eastern).


def _prepare(catalog: Path, out: Path, *extra: str, as_of: datetime = NOW, context: Path | None = None) -> int:
    args = [
        "prepare-live",
        "--catalog-dir", str(catalog),
        "--out-dir", str(out),
        "--as-of", as_of.isoformat(),
        *extra,
    ]
    if context is not None:
        args += ["--context-dir", str(context)]
    return main(args)


def _health(out: Path) -> dict:
    return json.loads((out / "cfb_execution_slate.json").read_text())["health"]


def _context(tmp_path: Path, verdict: str) -> Path:
    directory = tmp_path / "context"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / slate_health.CONTEXT_RUN_RECORD).write_text(json.dumps({"verdict": verdict}))
    return directory


# ------------------------------------------------------- NOT_APPLICABLE


def test_a_date_with_no_games_is_green_when_the_catalog_proves_it(tmp_path):
    catalog = catalog_dir(tmp_path)
    out = tmp_path / "exec"
    assert _prepare(catalog, out, "--date", "2026-09-18") == 0
    health = _health(out)
    assert health["state"] == slate_health.NOT_APPLICABLE
    assert health["reason"] == slate_health.NO_GAMES_ON_SLATE_DATE
    assert health["exit_code"] == 0


def test_a_slate_whose_games_have_all_kicked_off_is_green(tmp_path):
    catalog = catalog_dir(tmp_path)
    out = tmp_path / "exec"
    after_kickoff = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)
    assert _prepare(catalog, out, "--date", "2026-09-19", as_of=after_kickoff) == 0
    health = _health(out)
    assert health["state"] == slate_health.NOT_APPLICABLE
    assert health["reason"] == slate_health.SLATE_ALREADY_UNDER_WAY


# ------------------------------------------- empty but UNPROVEN stays red


def test_an_empty_date_the_catalog_does_not_reach_past_stays_red(tmp_path, capsys):
    catalog = catalog_dir(tmp_path)
    out = tmp_path / "exec"
    # no game in the catalog kicks off after 2026-09-20: the horizon may not cover it
    assert _prepare(catalog, out, "--date", "2026-09-20") == 4
    health = _health(out)
    assert health["state"] == slate_health.FAILED
    assert health["reason"] == slate_health.EMPTY_SLATE_UNPROVEN
    assert "WARNING" in capsys.readouterr().err


def test_an_empty_date_cut_from_a_stale_catalog_stays_red(tmp_path):
    catalog = catalog_dir(tmp_path, captured_at=CAPTURED_AT - timedelta(hours=8))
    out = tmp_path / "exec"
    assert _prepare(catalog, out, "--date", "2026-09-18") == 4
    health = _health(out)
    assert health["reason"] == slate_health.EMPTY_SLATE_UNPROVEN
    assert "freshness" in health["detail"]


def test_an_empty_date_cut_from_an_incomplete_catalog_stays_red(tmp_path):
    catalog = catalog_dir(tmp_path)
    index_path = catalog / "cfb_market_catalog.json"
    index = json.loads(index_path.read_text())
    index["completeness"]["capture_complete"] = False
    index_path.write_text(json.dumps(index))
    out = tmp_path / "exec"
    assert _prepare(catalog, out, "--date", "2026-09-18") == 4
    assert "not complete" in _health(out)["detail"]


def test_games_with_no_eligible_contract_stay_red(tmp_path):
    catalog = catalog_dir(tmp_path, captured_at=CAPTURED_AT - timedelta(hours=8))
    out = tmp_path / "exec"
    assert _prepare(catalog, out, "--date", "2026-09-19") == 4
    health = _health(out)
    assert health["state"] == slate_health.FAILED
    assert health["reason"] == slate_health.ZERO_ELIGIBLE_CONTRACTS


def test_started_games_mixed_with_any_other_exclusion_stay_red():
    slate = {
        "slate_date": "2026-09-19",
        "reconciliation": {
            "contracts_eligible": 0,
            "games_in_scope": 2,
            "exclusions_by_status": {"game_started": 40, "stale_quote": 30},
        },
    }
    health = slate_health.classify_slate_health(slate, {"games": []})
    assert health["state"] == slate_health.FAILED
    assert health["exit_code"] == slate_health.EXIT_ZERO_ELIGIBLE


def test_a_structural_failure_is_red_whatever_else_is_true():
    slate = {"reconciliation": {"contracts_eligible": 10, "games_in_scope": 1}}
    health = slate_health.classify_slate_health(slate, {}, structural_failure="does not balance")
    assert health["state"] == slate_health.FAILED
    assert health["exit_code"] == slate_health.EXIT_STRUCTURAL


# ------------------------------------------------ HEALTHY and DEGRADED


def test_a_built_slate_with_context_is_healthy(tmp_path):
    out = tmp_path / "exec"
    code = _prepare(
        catalog_dir(tmp_path), out, "--date", "2026-09-19", context=_context(tmp_path, "complete")
    )
    assert code == 0
    assert _health(out)["state"] == slate_health.HEALTHY


@pytest.mark.parametrize("verdict", ["sources_unreachable", "stopped_early", "no_events_matched"])
def test_lost_factual_context_is_degraded_not_red(tmp_path, monkeypatch, capsys, verdict):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = tmp_path / "exec"
    code = _prepare(
        catalog_dir(tmp_path), out, "--date", "2026-09-19", context=_context(tmp_path, verdict)
    )
    assert code == 0
    health = _health(out)
    assert health["state"] == slate_health.DEGRADED
    assert health["reason"] == slate_health.CONTEXT_UNAVAILABLE
    assert "::warning title=Execution slate DEGRADED::" in capsys.readouterr().out
    assert "DEGRADED" in summary.read_text()


def test_a_collector_that_left_no_record_is_degraded(tmp_path):
    out = tmp_path / "exec"
    empty = tmp_path / "no-context-here"
    assert _prepare(catalog_dir(tmp_path), out, "--date", "2026-09-19", context=empty) == 0
    assert _health(out)["state"] == slate_health.DEGRADED


def test_partial_context_coverage_is_still_healthy(tmp_path):
    out = tmp_path / "exec"
    code = _prepare(
        catalog_dir(tmp_path), out, "--date", "2026-09-19", context=_context(tmp_path, "partial")
    )
    assert code == 0
    assert _health(out)["state"] == slate_health.HEALTHY


def test_not_applicable_writes_a_notice_and_a_summary_line(tmp_path, monkeypatch, capsys):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert _prepare(catalog_dir(tmp_path), tmp_path / "exec", "--date", "2026-09-18") == 0
    assert "::notice title=Execution slate not applicable::" in capsys.readouterr().out
    assert "NOT_APPLICABLE" in summary.read_text()
