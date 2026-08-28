"""Unit tests only -- mocked HTTP, never a live call. See
cfb_edge_finder/data/cfbd_client.py's module docstring: this module's
request/response shape assumptions are not independently live-verified,
and these tests do not claim otherwise.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient


def test_missing_api_key_raises_auth_error_without_attempting_a_request():
    client = CFBDClient(api_key=None)
    with patch("cfb_edge_finder.data.cfbd_client.requests.get") as mock_get:
        with pytest.raises(CFBDAuthError):
            client.fetch_games(season=2026)
        mock_get.assert_not_called()


def test_fetch_games_sends_bearer_token_and_expected_params():
    client = CFBDClient(api_key="test-key-123")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"id": 1}]
    mock_response.raise_for_status.return_value = None
    with patch("cfb_edge_finder.data.cfbd_client.requests.get", return_value=mock_response) as mock_get:
        result = client.fetch_games(season=2026, season_type="regular", week=1)

    assert result == [{"id": 1}]
    mock_get.assert_called_once()
    _args, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer test-key-123"
    assert kwargs["params"]["year"] == 2026
    assert kwargs["params"]["seasonType"] == "regular"
    assert kwargs["params"]["week"] == 1


def test_fetch_games_omits_none_params():
    client = CFBDClient(api_key="test-key-123")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []
    mock_response.raise_for_status.return_value = None
    with patch("cfb_edge_finder.data.cfbd_client.requests.get", return_value=mock_response) as mock_get:
        client.fetch_games(season=2026)

    _args, kwargs = mock_get.call_args
    assert "week" not in kwargs["params"]
    assert "seasonType" not in kwargs["params"]


def test_http_error_propagates():
    client = CFBDClient(api_key="test-key-123")
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = RuntimeError("simulated 500")
    with patch("cfb_edge_finder.data.cfbd_client.requests.get", return_value=mock_response):
        with pytest.raises(RuntimeError, match="simulated 500"):
            client.fetch_games(season=2026)


# --- bounded retry (live 502 regression, run 33211233986) ----------------


class _RetryResp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else []
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def _install_retry(monkeypatch, responses):
    from cfb_edge_finder.data import cfbd_client as cc

    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(cc.requests, "get", fake_get)
    monkeypatch.setattr(cc.time, "sleep", lambda _s: None)
    return calls


def test_transient_502_is_retried_then_succeeds(monkeypatch):
    """A one-off CFBD 502 killed an entire collection run before this
    retry existed -- the exact status observed live must recover."""
    calls = _install_retry(monkeypatch, [_RetryResp(502), _RetryResp(200, [{"id": 1}])])
    client = CFBDClient(api_key="k")
    assert client.fetch_games(season=2026) == [{"id": 1}]
    assert len(calls) == 2


def test_persistent_5xx_raises_after_bounded_attempts(monkeypatch):
    from cfb_edge_finder.data import cfbd_client as cc

    calls = _install_retry(monkeypatch, [_RetryResp(502)])
    client = CFBDClient(api_key="k")
    with pytest.raises(requests.HTTPError):
        client.fetch_games(season=2026)
    assert len(calls) == cc.RETRY_ATTEMPTS


def test_non_429_client_error_is_never_retried(monkeypatch):
    calls = _install_retry(monkeypatch, [_RetryResp(404)])
    client = CFBDClient(api_key="k")
    with pytest.raises(requests.HTTPError):
        client.fetch_games(season=2026)
    assert len(calls) == 1


def test_retry_after_header_is_honoured_and_capped(monkeypatch):
    from cfb_edge_finder.data import cfbd_client as cc

    resp = _RetryResp(429, headers={"Retry-After": "9999"})
    assert cc.CFBDClient._retry_delay_seconds(resp, 0) == cc.RETRY_MAX_DELAY_SECONDS
    resp2 = _RetryResp(429, headers={"Retry-After": "2"})
    assert cc.CFBDClient._retry_delay_seconds(resp2, 0) == 2.0
