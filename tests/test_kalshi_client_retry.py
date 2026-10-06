"""Bounded-retry behaviour of the Kalshi client (live 429 regression)."""
from __future__ import annotations

import pytest
import requests

from cfb_edge_finder.data import kalshi_client as kc


class _Resp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def _install(monkeypatch, responses):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(kc.requests, "get", fake_get)
    monkeypatch.setattr(kc.time, "sleep", lambda _s: None)
    return calls


def test_429_is_retried_then_succeeds(monkeypatch):
    calls = _install(monkeypatch, [_Resp(429), _Resp(429), _Resp(200, {"markets": [{"ticker": "A"}]})])
    client = kc.KalshiClient()
    assert client.fetch_markets(series_ticker="X") == [{"ticker": "A"}]
    assert len(calls) == 3, "429 was not retried"


def test_429_that_never_clears_finally_raises(monkeypatch):
    calls = _install(monkeypatch, [_Resp(429)])
    client = kc.KalshiClient()
    with pytest.raises(requests.HTTPError):
        client.fetch_markets(series_ticker="X")
    assert len(calls) == kc.RETRY_ATTEMPTS, "retry count is not bounded as documented"


def test_5xx_is_retried(monkeypatch):
    calls = _install(monkeypatch, [_Resp(503), _Resp(200, {"markets": []})])
    kc.KalshiClient().fetch_markets(series_ticker="X")
    assert len(calls) == 2


def test_400_is_not_retried(monkeypatch):
    """A real client error must fail fast -- retrying a 400 just turns one
    clear failure into several slow ones."""
    calls = _install(monkeypatch, [_Resp(400)])
    with pytest.raises(requests.HTTPError):
        kc.KalshiClient().fetch_markets(series_ticker="X")
    assert len(calls) == 1


def test_retry_after_header_is_honoured_and_capped():
    assert kc.KalshiClient._retry_delay_seconds(_Resp(429, headers={"Retry-After": "2"}), 0) == 2.0
    capped = kc.KalshiClient._retry_delay_seconds(_Resp(429, headers={"Retry-After": "99999"}), 0)
    assert capped == kc.RETRY_MAX_DELAY_SECONDS
    garbage = kc.KalshiClient._retry_delay_seconds(_Resp(429, headers={"Retry-After": "soon"}), 0)
    assert garbage == kc.RETRY_BASE_DELAY_SECONDS


def test_backoff_is_exponential_and_bounded():
    delays = [kc.KalshiClient._retry_delay_seconds(_Resp(429), i) for i in range(5)]
    assert delays == sorted(delays), "backoff is not monotonically increasing"
    assert max(delays) <= kc.RETRY_MAX_DELAY_SECONDS


# ============================================================ transport errors
# Regression: the six 2026-09-05/06 scheduled-collector crashes.
#
# `requests.ConnectionError` is a SIBLING of `requests.HTTPError`, not a
# subclass, so a reset socket bypassed both the retry loop below and the
# collector's own per-series guard, and killed the process before it could
# classify its run or write its operational state.


def _install_raising(monkeypatch, errors):
    """Each call raises errors[i]; a None entry returns a 200 instead."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        idx = len(calls)
        calls.append(url)
        err = errors[min(idx, len(errors) - 1)]
        if err is not None:
            raise err
        return _Resp(200, {"markets": [{"ticker": "A", "status": "active"}]})

    monkeypatch.setattr(kc.requests, "get", fake_get)
    monkeypatch.setattr(kc.time, "sleep", lambda _s: None)
    return calls


def _reset():
    return requests.ConnectionError("('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))")


def test_connection_reset_is_retried_then_succeeds(monkeypatch):
    calls = _install_raising(monkeypatch, [_reset(), _reset(), None])
    assert kc.KalshiClient().fetch_markets(series_ticker="X") == [{"ticker": "A", "status": "active"}]
    assert len(calls) == 3, "a reset socket was not retried"


def test_connection_reset_that_never_clears_raises_bounded(monkeypatch):
    """Exhausting the retries must still RAISE: the caller has to be able
    to tell a failed series from an empty one."""
    calls = _install_raising(monkeypatch, [_reset()])
    with pytest.raises(requests.ConnectionError):
        kc.KalshiClient().fetch_markets(series_ticker="X")
    assert len(calls) == kc.RETRY_ATTEMPTS


def test_timeouts_are_retried_too(monkeypatch):
    calls = _install_raising(monkeypatch, [requests.Timeout("timed out"), None])
    kc.KalshiClient().fetch_markets(series_ticker="X")
    assert len(calls) == 2


def test_transport_failure_backoff_needs_no_response():
    """No response exists for a transport error, so there is no
    `Retry-After` to consult -- the delay must still be computed."""
    delays = [kc.KalshiClient._retry_delay_seconds(None, i) for i in range(5)]
    assert delays == sorted(delays)
    assert max(delays) <= kc.RETRY_MAX_DELAY_SECONDS


def test_collector_counts_a_reset_series_instead_of_dying(monkeypatch):
    """The end-to-end shape of the crash: the collector's per-series guard
    must convert an exhausted transport failure into a COUNTED api
    failure, never a process kill."""
    import scripts.capture_kalshi_cfb_snapshot as snap

    _install_raising(monkeypatch, [_reset()])
    markets, failed = snap._fetch_active_markets_with_status(kc.KalshiClient(), "KXNCAAFGAME")
    assert markets == []
    assert failed is True, "a failed series must stay distinguishable from an empty one"
