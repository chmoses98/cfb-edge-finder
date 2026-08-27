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
