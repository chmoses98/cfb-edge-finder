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


# --- 2026-09-01 forensic-audit closure: event-level context + accounting ---


def test_week1_like_unsupported_heavy_slate_is_not_a_false_high_alarm():
    # The exact live shape that tripped the 2026-09-01 false alarm, as it
    # accounts AFTER the NON_FBS_PARTICIPANT classification: a large
    # deliberately-declined population (FBS-vs-FCS ladders, D2/D3
    # fixtures) explicitly accounted as unsupported, with only a small
    # genuine-failure residue (e.g. a schedule-source discrepancy).
    report = CaptureHealthReport(
        games_scanned=3438,
        markets_scanned=3923,
        events_scanned=439,
        events_mapping_failed=23,
        markets_unsupported_population=2039,
        mapping_failures=232,
        captures_due=0,
        supported_markets=0,
    )
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    assert not any(d.code == "mapping_failure_rate_high" for d in diagnostics)
    assert not should_fail_run(diagnostics)


def test_captures_due_zero_with_zero_supported_markets_is_not_a_collapse():
    # supported_markets only increments when a due checkpoint prices a
    # market, so a run with nothing due legitimately reports 0. With no
    # baseline (the scheduled scanner passes None), that must never read
    # as a discovery/mapping collapse.
    report = CaptureHealthReport(
        games_scanned=3438, markets_scanned=3923, events_scanned=439,
        captures_due=0, supported_markets=0, mapping_failures=0,
    )
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    assert diagnostics == []
    assert not should_fail_run(diagnostics)


def test_genuine_large_scale_mapping_failure_still_fails_high_and_loud():
    # If mapping genuinely breaks (a Kalshi grammar change, a registry
    # regression), the failures land in mapping_failures regardless of
    # the new unsupported-population accounting -- the HIGH alarm and the
    # nonzero exit must survive the metric refinement unchanged.
    report = CaptureHealthReport(
        games_scanned=3438,
        markets_scanned=3923,
        events_scanned=439,
        events_mapping_failed=439,
        markets_unsupported_population=0,
        mapping_failures=3923,
    )
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    assert any(d.code == "mapping_failure_rate_high" and d.severity == Severity.HIGH for d in diagnostics)
    assert should_fail_run(diagnostics)


def test_mapping_diagnostics_carry_event_level_context():
    report = CaptureHealthReport(
        games_scanned=10, markets_scanned=100, events_scanned=20,
        events_mapping_failed=10, markets_unsupported_population=5, mapping_failures=50,
    )
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    high = next(d for d in diagnostics if d.code == "mapping_failure_rate_high")
    assert "10/20" in high.detail
    assert "markets_unsupported_population=5" in high.detail


def test_event_level_fields_default_to_zero_and_change_no_legacy_behavior():
    # Reports built without the new fields (every pre-existing caller)
    # must evaluate exactly as before.
    report = CaptureHealthReport(games_scanned=10, markets_scanned=100, mapping_failures=50)
    diagnostics = evaluate_collapse(report, baseline_supported_markets=None)
    high = next(d for d in diagnostics if d.code == "mapping_failure_rate_high")
    assert high.severity == Severity.HIGH
    assert "events:" not in high.detail
