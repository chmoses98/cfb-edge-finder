"""Unit tests only -- mocked HTTP, never a live call. See
cfb_edge_finder/data/espn_client.py's module docstring.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from cfb_edge_finder.data.espn_client import RETRY_ATTEMPTS, SCOREBOARD_HOSTS, ESPNClient


def test_fetch_scoreboard_sends_expected_params():
    client = ESPNClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"events": []}
    mock_response.raise_for_status.return_value = None
    with patch("cfb_edge_finder.data.espn_client.requests.get", return_value=mock_response) as mock_get:
        result = client.fetch_scoreboard("20260829")

    assert result == {"events": []}
    _args, kwargs = mock_get.call_args
    assert kwargs["params"]["dates"] == "20260829"
    assert kwargs["params"]["groups"] == 80


def test_no_auth_header_sent_unauthenticated_by_design():
    client = ESPNClient()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"events": []}
    mock_response.raise_for_status.return_value = None
    with patch("cfb_edge_finder.data.espn_client.requests.get", return_value=mock_response) as mock_get:
        client.fetch_scoreboard("20260829")

    _args, kwargs = mock_get.call_args
    assert "headers" not in kwargs or "Authorization" not in kwargs.get("headers", {})


# --- bounded retry (mirrors CFBDClient's policy) -------------------------


class _RetryResp:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"events": []}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def _install_retry(monkeypatch, responses):
    from cfb_edge_finder.data import espn_client as ec

    calls = []

    def fake_get(url, params=None, timeout=None, headers=None):
        calls.append(url)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(ec.requests, "get", fake_get)
    monkeypatch.setattr(ec.time, "sleep", lambda _s: None)
    return calls


def test_transient_5xx_is_retried_then_succeeds(monkeypatch):
    calls = _install_retry(monkeypatch, [_RetryResp(503), _RetryResp(200, {"events": [{"id": "1"}]})])
    assert ESPNClient().fetch_scoreboard("20260829") == {"events": [{"id": "1"}]}
    assert len(calls) == 2


def test_persistent_5xx_raises_after_bounded_attempts_per_host(monkeypatch):
    """Bounded retry is now PER HOST: a genuinely dead ESPN retries its
    schedule on each host in turn and then raises, so the caller still
    fails closed. Total work stays bounded at attempts x hosts."""
    calls = _install_retry(monkeypatch, [_RetryResp(502)])
    with pytest.raises(requests.HTTPError):
        ESPNClient().fetch_scoreboard("20260829")
    assert len(calls) == RETRY_ATTEMPTS * len(SCOREBOARD_HOSTS)


def test_non_retryable_status_moves_to_the_next_host_without_retrying(monkeypatch):
    """A 403/404 is a POLICY answer, not a transient one -- retrying the
    same blocked host wastes the settlement window. This is exactly the
    2026-09-03 condition: site.api.espn.com returns 403 to CI on every
    request, so the client must spend one call there and move on."""
    calls = _install_retry(monkeypatch, [_RetryResp(404)])
    with pytest.raises(requests.HTTPError):
        ESPNClient().fetch_scoreboard("20260829")
    assert len(calls) == len(SCOREBOARD_HOSTS), "one call per host, no retries on a policy answer"
