"""CFBD quota observability + automatic recovery (research/cfbd_access.py).

Fixture payloads are VERBATIM live evidence from run 33349348575
(2026-08-31, during the real quota outage): GET /info answered HTTP 200
with remainingCalls=0 and resetAt=2026-09-01T00:00:00.000Z while every
metered endpoint returned 429 {"message":"Monthly call quota exceeded."}.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tests"))

from test_football_state_decoupling import (  # noqa: E402
    HISTORY_SEASONS,
    FakeCFBD,
    _history_rows,
    _schedule_row,
)

from cfb_edge_finder.research import cfbd_access, football_state  # noqa: E402
from cfb_edge_finder.research.cfbd_access import (  # noqa: E402
    CFBD_ACCESS_OK,
    CFBD_ACCESS_UNKNOWN,
    CFBD_QUOTA_EXHAUSTED,
    PROBE_ERROR_RETRY_HOURS,
    PROBE_MAX_INTERVAL_HOURS,
    assess,
    gate_says_exhausted,
    load_state,
    next_probe_time,
    parse_account_info,
    record_outcome,
    save_state,
    state_path,
    summary_lines,
)

NOW = datetime(2026, 8, 31, 2, 0, tzinfo=UTC)
SEASON = 2026
API_KEY = "sk-test-SECRET-NEVER-LOGGED"

LIVE_INFO_EXHAUSTED = {
    "patronLevel": 0,
    "tierName": "Free",
    "monthlyLimit": 1000,
    "remainingCalls": 0,
    "usedCalls": 1000,
    "resetAt": "2026-09-01T00:00:00.000Z",
    "sharedPool": True,
    "products": ["cfb", "cbb"],
    "features": {"adjustedMetrics": False},
}

LIVE_INFO_RECOVERED = {**LIVE_INFO_EXHAUSTED, "remainingCalls": 1000, "usedCalls": 0}


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=response)


class GateFakeCFBD(FakeCFBD):
    """FakeCFBD + the /info account surface, with separate failure knobs
    for the unmetered probe vs the metered endpoints."""

    def __init__(self, *, info=None, info_exc=None, metered_exc=None, **kwargs):
        super().__init__(**kwargs)
        self.info = info
        self.info_exc = info_exc
        self.metered_exc = metered_exc
        self.info_calls = 0

    def fetch_account_info(self):
        self.info_calls += 1
        if self.info_exc is not None:
            raise self.info_exc
        return self.info

    def _maybe_fail(self):
        self.calls += 1
        if self.metered_exc is not None:
            raise self.metered_exc
        if self.failing:
            raise requests.HTTPError("429 Client Error: Too Many Requests")


def _exhausted_state(next_probe_at: datetime) -> dict:
    return {
        "schema_version": "cfbd_access_v1",
        "access_state": CFBD_QUOTA_EXHAUSTED,
        "cfbd_next_probe_at": next_probe_at.isoformat(),
        "cfbd_quota_remaining": 0,
        "cfbd_quota_limit": 1000,
        "cfbd_quota_resets_at": "2026-09-01T00:00:00+00:00",
    }


# --------------------------------------------------- telemetry parsing


class TestQuotaTelemetryParsing:
    def test_live_exhausted_payload_parses_every_field(self):
        quota = parse_account_info(LIVE_INFO_EXHAUSTED, checked_at=NOW)
        assert quota.tier_name == "Free"
        assert quota.monthly_limit == 1000
        assert quota.remaining_calls == 0
        assert quota.used_calls == 1000
        assert quota.reset_at == datetime(2026, 9, 1, tzinfo=UTC)
        d = quota.as_state_dict()
        assert d["cfbd_quota_resets_at"] == "2026-09-01T00:00:00+00:00"
        assert d["cfbd_usage_source"].startswith("cfbd GET /info")

    def test_missing_optional_fields_stay_none_never_invented(self):
        quota = parse_account_info({"tierName": "Free"}, checked_at=NOW)
        assert quota.monthly_limit is None
        assert quota.remaining_calls is None
        assert quota.used_calls is None
        assert quota.reset_at is None

    @pytest.mark.parametrize(
        "raw",
        [
            {"remainingCalls": "800", "monthlyLimit": "1000", "resetAt": "not-a-date"},
            {"remainingCalls": True, "monthlyLimit": 3.5, "resetAt": 12345},
        ],
    )
    def test_wrong_typed_fields_stay_none(self, raw):
        quota = parse_account_info(raw, checked_at=NOW)
        assert quota.remaining_calls is None
        assert quota.monthly_limit is None
        assert quota.reset_at is None

    def test_malformed_info_response_yields_unavailable_not_guessed(self, tmp_path):
        # No prior exhaustion evidence + a broken /info: telemetry is
        # UNAVAILABLE (never guessed), but a broken observability endpoint
        # must not gate off otherwise-healthy operation -- fail-closed
        # applies to claiming RECOVERY, not to inventing exhaustion.
        client = GateFakeCFBD(info_exc=ValueError("unexpected /info response shape: list"))
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.quota is None
        assert assessment.access_state == CFBD_ACCESS_UNKNOWN
        assert assessment.recovery_detected is False
        assert "unusable /info payload" in assessment.probe_error


# ------------------------------------------------------- probe schedule


class TestNextProbeTime:
    def test_authoritative_reset_wins_when_sooner_than_cap(self):
        quota = parse_account_info(LIVE_INFO_EXHAUSTED, checked_at=NOW)
        # NOW is 22h before the reset -> the 6h cap applies first...
        assert next_probe_time(quota, now=NOW) == NOW + timedelta(hours=PROBE_MAX_INTERVAL_HOURS)
        # ...but within 6h of the reset, the probe lands just after it.
        late = datetime(2026, 8, 31, 22, 0, tzinfo=UTC)
        assert next_probe_time(quota, now=late) == datetime(2026, 9, 1, 0, 2, tzinfo=UTC)

    def test_no_reset_field_falls_back_to_capped_interval(self):
        quota = parse_account_info({"remainingCalls": 0}, checked_at=NOW)
        assert next_probe_time(quota, now=NOW) == NOW + timedelta(hours=PROBE_MAX_INTERVAL_HOURS)
        assert next_probe_time(None, now=NOW) == NOW + timedelta(hours=PROBE_MAX_INTERVAL_HOURS)

    def test_lagging_reset_probes_on_short_interval_not_six_hours(self):
        quota = parse_account_info(LIVE_INFO_EXHAUSTED, checked_at=NOW)
        after_reset = datetime(2026, 9, 1, 0, 5, tzinfo=UTC)
        assert next_probe_time(quota, now=after_reset) == after_reset + timedelta(hours=0.5)


# ------------------------------------------------------------ the gate


class TestGateDecisions:
    def test_healthy_state_never_probes(self, tmp_path):
        save_state(tmp_path, {"access_state": CFBD_ACCESS_OK})
        client = GateFakeCFBD(info=LIVE_INFO_RECOVERED)
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is True
        assert client.info_calls == 0 and client.calls == 0

    def test_exhausted_before_probe_window_makes_zero_network_calls(self, tmp_path):
        save_state(tmp_path, _exhausted_state(NOW + timedelta(hours=3)))
        client = GateFakeCFBD(info=LIVE_INFO_EXHAUSTED)
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is False
        assert assessment.access_state == CFBD_QUOTA_EXHAUSTED
        assert client.info_calls == 0 and client.calls == 0
        assert assessment.next_probe_at == NOW + timedelta(hours=3)

    def test_probe_still_exhausted_reschedules_and_stays_gated(self, tmp_path):
        save_state(tmp_path, _exhausted_state(NOW - timedelta(minutes=1)))
        client = GateFakeCFBD(info=LIVE_INFO_EXHAUSTED)
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is False
        assert assessment.access_state == CFBD_QUOTA_EXHAUSTED
        assert client.info_calls == 1
        assert assessment.next_probe_at is not None and assessment.next_probe_at > NOW

    def test_probe_showing_remaining_calls_recovers(self, tmp_path):
        save_state(tmp_path, _exhausted_state(NOW - timedelta(minutes=1)))
        client = GateFakeCFBD(info=LIVE_INFO_RECOVERED)
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is True
        assert assessment.access_state == CFBD_ACCESS_OK
        assert assessment.recovery_detected is True

    @pytest.mark.parametrize(
        "exc", [_http_error(503), requests.ConnectionError("boom"), requests.Timeout("slow")]
    )
    def test_probe_failure_is_never_recovery(self, tmp_path, exc):
        save_state(tmp_path, _exhausted_state(NOW - timedelta(minutes=1)))
        client = GateFakeCFBD(info_exc=exc)
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is False
        assert assessment.access_state == CFBD_QUOTA_EXHAUSTED  # stays exhausted, not "recovered"
        assert assessment.recovery_detected is False
        assert assessment.next_probe_at == NOW + timedelta(hours=PROBE_ERROR_RETRY_HOURS)

    def test_info_without_remaining_calls_is_not_recovery(self, tmp_path):
        save_state(tmp_path, _exhausted_state(NOW - timedelta(minutes=1)))
        client = GateFakeCFBD(info={"tierName": "Admin", "remainingCalls": None})
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is False
        assert "no usable remainingCalls" in assessment.probe_error

    def test_no_recorded_state_probes_before_any_metered_attempt(self, tmp_path):
        client = GateFakeCFBD(info=LIVE_INFO_EXHAUSTED)
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is False
        assert assessment.access_state == CFBD_QUOTA_EXHAUSTED
        assert client.info_calls == 1 and client.calls == 0

    def test_force_allow_is_an_operator_override(self, tmp_path):
        save_state(tmp_path, _exhausted_state(NOW + timedelta(hours=5)))
        client = GateFakeCFBD(info=LIVE_INFO_EXHAUSTED)
        assessment = assess(tmp_path, client, now=NOW, force_allow=True)
        assert assessment.allow_cfbd is True
        assert client.info_calls == 0


# ------------------------------------------------- outcome -> new state


class TestRecordOutcome:
    def test_live_429_flips_ok_to_exhausted_with_authoritative_reset(self, tmp_path):
        save_state(tmp_path, {"access_state": CFBD_ACCESS_OK})
        client = GateFakeCFBD(info=LIVE_INFO_EXHAUSTED)
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is True
        outcome = football_state.RefreshOutcome(
            state=None, source="unavailable", cfbd_requests=10,
            refresh_error="HTTPError: 429 Client Error", refresh_http_status=429,
        )
        record = record_outcome(assessment, outcome, client, now=NOW)
        assert record["access_state"] == CFBD_QUOTA_EXHAUSTED
        assert record["cfbd_quota_resets_at"] == "2026-09-01T00:00:00+00:00"  # from the follow-up free probe
        assert record["cfbd_next_probe_at"] is not None
        assert client.info_calls == 1  # telemetry probe, unmetered

    @pytest.mark.parametrize("status,error", [(502, "HTTPError: 502"), (None, "ConnectionError: boom")])
    def test_non_429_failures_do_not_mark_exhausted(self, tmp_path, status, error):
        save_state(tmp_path, {"access_state": CFBD_ACCESS_OK})
        client = GateFakeCFBD(info=LIVE_INFO_EXHAUSTED)
        assessment = assess(tmp_path, client, now=NOW)
        outcome = football_state.RefreshOutcome(
            state=None, source="unavailable", cfbd_requests=10,
            refresh_error=error, refresh_http_status=status,
        )
        record = record_outcome(assessment, outcome, client, now=NOW)
        assert record["access_state"] == CFBD_ACCESS_OK  # transient trouble is not quota exhaustion

    def test_successful_live_refresh_records_ok_and_clears_probe(self, tmp_path):
        save_state(tmp_path, _exhausted_state(NOW - timedelta(minutes=1)))
        client = GateFakeCFBD(info=LIVE_INFO_RECOVERED)
        assessment = assess(tmp_path, client, now=NOW)
        outcome = football_state.RefreshOutcome(state=object(), source="live_full_refresh", cfbd_requests=4)
        record = record_outcome(assessment, outcome, client, now=NOW)
        assert record["access_state"] == CFBD_ACCESS_OK
        assert record["cfbd_next_probe_at"] is None

    def test_gated_run_carries_prior_telemetry_forward(self, tmp_path):
        save_state(tmp_path, _exhausted_state(NOW + timedelta(hours=3)))
        client = GateFakeCFBD(info=LIVE_INFO_EXHAUSTED)
        assessment = assess(tmp_path, client, now=NOW)
        outcome = football_state.RefreshOutcome(state=None, source="unavailable", cfbd_requests=0)
        record = record_outcome(assessment, outcome, client, now=NOW)
        assert record["access_state"] == CFBD_QUOTA_EXHAUSTED
        assert record["cfbd_quota_resets_at"] == "2026-09-01T00:00:00+00:00"  # carried, not re-fetched
        assert client.info_calls == 0

    def test_api_key_never_appears_in_state_or_summary(self, tmp_path):
        client = GateFakeCFBD(info=LIVE_INFO_EXHAUSTED)
        client.api_key = API_KEY  # even if a client carried it as an attribute
        assessment = assess(tmp_path, client, now=NOW)
        outcome = football_state.RefreshOutcome(state=None, source="unavailable", cfbd_requests=0)
        record = record_outcome(assessment, outcome, client, now=NOW)
        save_state(tmp_path, record)
        persisted = state_path(tmp_path).read_text(encoding="utf-8")
        rendered = "\n".join(summary_lines(assessment, outcome))
        assert API_KEY not in persisted
        assert API_KEY not in rendered
        assert API_KEY not in json.dumps(record)


# ------------------------------------------- gate x slow-lane (resolve)


class TestGatedResolve:
    def test_gated_resolve_makes_zero_requests_and_fails_closed_when_no_artifact(self, tmp_path):
        client = FakeCFBD(failing=True)
        outcome = football_state.resolve_football_state(
            tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW, allow_cfbd=False
        )
        assert outcome.state is None
        assert outcome.cfbd_requests == 0
        assert client.calls == 0
        assert outcome.freshness == football_state.FOOTBALL_STATE_MISSING
        assert "gated" in outcome.refresh_error

    def test_gated_resolve_still_serves_fresh_cache(self, tmp_path):
        self._write_artifact(tmp_path, fetched_at=NOW - timedelta(hours=1))
        client = FakeCFBD(failing=True)
        outcome = football_state.resolve_football_state(
            tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW, allow_cfbd=False
        )
        assert outcome.state is not None
        assert outcome.cfbd_requests == 0 and client.calls == 0
        assert outcome.source == "cache"

    def test_gated_resolve_degrades_soft_stale_cache_like_a_failed_refresh(self, tmp_path):
        self._write_artifact(tmp_path, fetched_at=NOW - timedelta(hours=5))
        client = FakeCFBD(failing=True)
        outcome = football_state.resolve_football_state(
            tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW, allow_cfbd=False
        )
        assert outcome.state is not None  # inside the 6h hard bound
        assert outcome.source == "cache_cfbd_gated"
        assert outcome.cfbd_requests == 0 and client.calls == 0

    def test_gated_resolve_never_loosens_the_hard_bound(self, tmp_path):
        self._write_artifact(tmp_path, fetched_at=NOW - timedelta(hours=7))
        client = FakeCFBD(failing=True)
        outcome = football_state.resolve_football_state(
            tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW, allow_cfbd=False
        )
        assert outcome.state is None  # 7h > 6h hard bound: fail closed, gated or not
        assert client.calls == 0

    def test_freshness_policy_unchanged_when_not_gated(self, tmp_path):
        self._write_artifact(tmp_path, fetched_at=NOW - timedelta(hours=1))
        client = FakeCFBD()
        outcome = football_state.resolve_football_state(
            tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW
        )
        assert outcome.source == "cache" and outcome.cfbd_requests == 0

    @staticmethod
    def _write_artifact(tmp_repo: Path, *, fetched_at: datetime) -> None:
        hist_games, hist_adv = _history_rows()
        schedule = [_schedule_row(0, "Nebraska", "Ohio", NOW + timedelta(hours=30))]
        client = FakeCFBD(schedule=schedule, history={2025: {"games": hist_games, "advanced": hist_adv}})
        state = football_state.build_football_state(
            client, season=SEASON, history_seasons=HISTORY_SEASONS, now=fetched_at
        )
        football_state.save_football_state(tmp_repo, state)


# --------------------------------------------- recovery -> bootstrap


class TestRecoveryBootstrap:
    def _recovered_client(self) -> GateFakeCFBD:
        hist_games, hist_adv = _history_rows()
        schedule = [_schedule_row(0, "Nebraska", "Ohio", NOW + timedelta(hours=30))]
        return GateFakeCFBD(
            info=LIVE_INFO_RECOVERED,
            schedule=schedule,
            history={2025: {"games": hist_games, "advanced": hist_adv}},
        )

    def test_first_recovery_probe_triggers_exactly_one_full_bootstrap(self, tmp_path):
        save_state(tmp_path, _exhausted_state(NOW - timedelta(minutes=1)))
        client = self._recovered_client()

        # Run 1: probe recovers -> the SAME run performs the normal full
        # build (2 + 2*len(history) metered calls) and saves the artifact.
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.recovery_detected is True
        outcome = football_state.resolve_football_state(
            tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW,
            allow_cfbd=assessment.allow_cfbd,
        )
        assert outcome.source == "live_full_refresh"
        assert client.calls == 2 + 2 * len(HISTORY_SEASONS)
        loaded, verdict = football_state.load_football_state(tmp_path, SEASON)
        assert verdict == "LOADED" and loaded is not None
        save_state(tmp_path, record_outcome(assessment, outcome, client, now=NOW))

        # Run 2 (five minutes later): OK state, fresh artifact -> cache,
        # zero probes, zero metered calls. No repeated forced rebuilds.
        later = NOW + timedelta(minutes=5)
        calls_before, info_before = client.calls, client.info_calls
        assessment2 = assess(tmp_path, client, now=later)
        assert assessment2.allow_cfbd is True and client.info_calls == info_before
        outcome2 = football_state.resolve_football_state(
            tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=later,
            allow_cfbd=assessment2.allow_cfbd,
        )
        assert outcome2.source == "cache"
        assert outcome2.cfbd_requests == 0
        assert client.calls == calls_before

    def test_failed_bootstrap_after_successful_probe_stays_degraded(self, tmp_path):
        save_state(tmp_path, _exhausted_state(NOW - timedelta(minutes=1)))
        client = GateFakeCFBD(info=LIVE_INFO_RECOVERED, metered_exc=_http_error(429))
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is True
        outcome = football_state.resolve_football_state(
            tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=NOW,
            allow_cfbd=assessment.allow_cfbd,
        )
        assert outcome.state is None  # fail closed: nothing fabricated
        assert outcome.refresh_http_status == 429
        record = record_outcome(assessment, outcome, client, now=NOW)
        assert record["access_state"] == CFBD_QUOTA_EXHAUSTED  # back to gated, retried later

    def test_locked_out_day_costs_at_most_probes_never_rebuilds(self, tmp_path):
        """Request-budget guard: 288 five-minute runs across a locked-out
        day make ZERO metered calls and at most ~day/6h + 1 unmetered
        probes -- never a build attempt per poll."""
        client = GateFakeCFBD(info=LIVE_INFO_EXHAUSTED)
        clock = NOW
        for _ in range(288):
            assessment = assess(tmp_path, client, now=clock)
            assert assessment.allow_cfbd is False
            outcome = football_state.resolve_football_state(
                tmp_path, client, season=SEASON, history_seasons=HISTORY_SEASONS, now=clock,
                allow_cfbd=assessment.allow_cfbd,
            )
            assert outcome.state is None and outcome.cfbd_requests == 0
            save_state(tmp_path, record_outcome(assessment, outcome, client, now=clock))
            clock += timedelta(minutes=5)
        assert client.calls == 0  # zero metered attempts all day
        # 4 six-hourly probes reach the authoritative reset instant; the
        # fake then keeps reporting exhausted past its own stated reset
        # (a lagging server-side reset), which legitimately switches to
        # the eager 30-minute post-reset interval for the last ~2h. All
        # probes are unmetered /info -- vs ~1,150 metered-429s/day before.
        assert client.info_calls <= 9


# ------------------------------------------------ read-only gate views


class TestReadOnlyGateViews:
    def test_state_file_lives_under_the_durable_store_allowlist(self, tmp_path):
        save_state(tmp_path, {"access_state": CFBD_QUOTA_EXHAUSTED})
        rel = state_path(tmp_path).relative_to(tmp_path)
        assert str(rel).startswith("data/research/")
        assert load_state(tmp_path)["access_state"] == CFBD_QUOTA_EXHAUSTED

    def test_corrupt_state_file_reads_as_empty(self, tmp_path):
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        assert load_state(tmp_path) == {}

    def test_gate_says_exhausted_only_inside_the_probe_window(self):
        state = _exhausted_state(NOW + timedelta(hours=2))
        assert gate_says_exhausted(state, now=NOW) is True
        assert gate_says_exhausted(state, now=NOW + timedelta(hours=3)) is False  # probe due: let it try
        assert gate_says_exhausted({"access_state": CFBD_ACCESS_OK}, now=NOW) is False
        assert gate_says_exhausted({}, now=NOW) is False

    def test_summary_lines_expose_operator_answers(self):
        quota = parse_account_info(LIVE_INFO_EXHAUSTED, checked_at=NOW)
        assessment = cfbd_access.AccessAssessment(
            access_state=CFBD_QUOTA_EXHAUSTED, allow_cfbd=False, probe_ran=True,
            quota=quota, next_probe_at=NOW + timedelta(hours=6),
        )
        text = "\n".join(summary_lines(assessment))
        assert "CFBD_QUOTA_EXHAUSTED" in text
        assert "resets_at=2026-09-01T00:00:00+00:00" in text
        assert "next_probe_at=" in text

    def test_recovery_summary_announces_bootstrap(self):
        assessment = cfbd_access.AccessAssessment(
            access_state=CFBD_ACCESS_OK, allow_cfbd=True, probe_ran=True, recovery_detected=True,
        )
        outcome = football_state.RefreshOutcome(state=object(), source="live_full_refresh", cfbd_requests=4)
        text = "\n".join(summary_lines(assessment, outcome))
        assert "CFBD_RECOVERED" in text
        assert "FOOTBALL_STATE_READY" in text


# ------------------------------------------------------ unknown states


class TestUnknownState:
    def test_unknown_state_respects_probe_window_and_stays_gated(self, tmp_path):
        save_state(
            tmp_path,
            {"access_state": CFBD_ACCESS_UNKNOWN, "cfbd_next_probe_at": (NOW + timedelta(hours=1)).isoformat()},
        )
        client = GateFakeCFBD(info=LIVE_INFO_RECOVERED)
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is False and client.info_calls == 0

    def test_unknown_state_recovers_via_probe_when_window_passes(self, tmp_path):
        save_state(
            tmp_path,
            {"access_state": CFBD_ACCESS_UNKNOWN, "cfbd_next_probe_at": (NOW - timedelta(minutes=1)).isoformat()},
        )
        client = GateFakeCFBD(info=LIVE_INFO_RECOVERED)
        assessment = assess(tmp_path, client, now=NOW)
        assert assessment.allow_cfbd is True and assessment.recovery_detected is True
