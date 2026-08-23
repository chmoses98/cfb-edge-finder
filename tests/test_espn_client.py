"""Unit tests only -- mocked HTTP, never a live call. See
cfb_edge_finder/data/espn_client.py's module docstring.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from cfb_edge_finder.data.espn_client import ESPNClient


def test_fetch_scoreboard_sends_expected_params():
    client = ESPNClient()
    mock_response = MagicMock()
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
    mock_response.json.return_value = {"events": []}
    mock_response.raise_for_status.return_value = None
    with patch("cfb_edge_finder.data.espn_client.requests.get", return_value=mock_response) as mock_get:
        client.fetch_scoreboard("20260829")

    _args, kwargs = mock_get.call_args
    assert "headers" not in kwargs or "Authorization" not in kwargs.get("headers", {})
