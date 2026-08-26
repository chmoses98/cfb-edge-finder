"""Mission sections 19-20: health checks and coverage-collapse detection."""

from __future__ import annotations

from cfb_edge_finder.research.health import CaptureHealthReport, Severity, evaluate_collapse, should_fail_run


def test_zero_markets_scanned_is_high_severity():
    report = CaptureHealthReport(games_scanned=10, markets_scanned=0)
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    assert any(d.code == "zero_markets_scanned" and d.severity == Severity.HIGH for d in diagnostics)


def test_zero_games_scanned_is_high_severity():
    report = CaptureHealthReport(games_scanned=0, markets_scanned=100)
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    assert any(d.code == "zero_games_scanned" for d in diagnostics)


def test_healthy_run_produces_no_diagnostics():
    report = CaptureHealthReport(
        games_scanned=50, markets_scanned=1000, supported_markets=800, captures_due=10,
        captures_written=10, captures_skipped_already_present=0, mapping_failures=5,
    )
    diagnostics = evaluate_collapse(report, baseline_supported_markets=850)
    assert diagnostics == []


def test_supported_market_drop_below_50_percent_is_warning_not_high():
    report = CaptureHealthReport(games_scanned=50, markets_scanned=1000, supported_markets=300)
    diagnostics = evaluate_collapse(report, baseline_supported_markets=1000)
    codes = {d.code: d.severity for d in diagnostics}
    assert codes.get("supported_market_drop") == Severity.WARNING


def test_supported_market_collapse_below_15_percent_is_high():
    report = CaptureHealthReport(games_scanned=50, markets_scanned=1000, supported_markets=100)
    diagnostics = evaluate_collapse(report, baseline_supported_markets=1000)
    codes = {d.code: d.severity for d in diagnostics}
    assert codes.get("supported_market_collapse") == Severity.HIGH


def test_a_light_week_is_not_automatically_flagged_as_error():
    # A genuinely smaller slate (bye weeks) with the SAME per-market
    # support ratio should not, by itself, trip supported-market collapse
    # (the ratio is computed against the SAME metric, not raw game count).
    report = CaptureHealthReport(games_scanned=20, markets_scanned=400, supported_markets=340)
    diagnostics = evaluate_collapse(report, baseline_supported_markets=350)
    assert not any(d.code.startswith("supported_market") for d in diagnostics)


def test_mapping_failure_rate_thresholds():
    warn_report = CaptureHealthReport(games_scanned=10, markets_scanned=100, mapping_failures=20)
    warn_diags = evaluate_collapse(warn_report, baseline_supported_markets=None)
    assert any(d.code == "mapping_failure_rate_elevated" for d in warn_diags)

    high_report = CaptureHealthReport(games_scanned=10, markets_scanned=100, mapping_failures=50)
    high_diags = evaluate_collapse(high_report, baseline_supported_markets=None)
    assert any(d.code == "mapping_failure_rate_high" and d.severity == Severity.HIGH for d in high_diags)


def test_persistence_write_count_mismatch_is_high():
    report = CaptureHealthReport(
        games_scanned=10, markets_scanned=100, captures_due=10, captures_skipped_already_present=0, captures_written=5
    )
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    assert any(d.code == "persistence_write_count_mismatch" and d.severity == Severity.HIGH for d in diagnostics)


def test_already_present_skips_do_not_count_against_write_mismatch():
    report = CaptureHealthReport(
        games_scanned=10, markets_scanned=100, captures_due=10, captures_skipped_already_present=10, captures_written=0
    )
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    assert not any(d.code == "persistence_write_count_mismatch" for d in diagnostics)


def test_persistence_failures_are_high():
    report = CaptureHealthReport(games_scanned=10, markets_scanned=100, persistence_failures=1)
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    assert any(d.code == "persistence_failures" and d.severity == Severity.HIGH for d in diagnostics)


def test_stale_schedule_failures_are_warning_only():
    report = CaptureHealthReport(games_scanned=10, markets_scanned=100, stale_schedule_failures=3)
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    codes = {d.code: d.severity for d in diagnostics}
    assert codes.get("stale_schedule_failures") == Severity.WARNING


def test_should_fail_run_true_only_with_high_severity():
    warning_report = CaptureHealthReport(games_scanned=10, markets_scanned=100, stale_schedule_failures=1)
    warning_only = evaluate_collapse(warning_report, None)
    assert should_fail_run(warning_only) is False

    high = evaluate_collapse(CaptureHealthReport(games_scanned=0, markets_scanned=0), None)
    assert should_fail_run(high) is True


def test_report_is_mutable_accumulator():
    report = CaptureHealthReport()
    report.games_scanned += 5
    report.markets_scanned += 100
    assert report.games_scanned == 5
    assert report.markets_scanned == 100
