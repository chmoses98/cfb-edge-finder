"""Unit tests only -- mocked HTTP, never a live call. See
cfb_edge_finder/data/cfbd_client.py's module docstring: this module's
request/response shape assumptions are not independently live-verified,
and these tests do not claim otherwise.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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
    mock_response.raise_for_status.side_effect = RuntimeError("simulated 500")
    with patch("cfb_edge_finder.data.cfbd_client.requests.get", return_value=mock_response):
        with pytest.raises(RuntimeError, match="simulated 500"):
            client.fetch_games(season=2026)
