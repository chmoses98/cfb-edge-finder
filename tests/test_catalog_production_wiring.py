"""The catalog's PRODUCTION wiring, not just its logic.

*** WHY THIS FILE EXISTS ***
Every other catalog test drives `MarketDiscovery` with a dict-backed fake,
which is the right way to test discovery logic -- and is precisely why a
real defect got all the way to a live runner undetected: the public
`KalshiClient.get_json` method the production entrypoint passes in was
missing from the client entirely. The fakes never touched it, the
schema/isolation tests only AST-parsed the entrypoint without importing
it, and the whole suite was green while `build_kalshi_cfb_catalog.py`
could not start.

These tests exercise the seam between the catalog and the real client,
with no network: the contract is that `KalshiClient` satisfies what
`MarketDiscovery` calls, and that the entrypoint can be imported and run
end-to-end against a stubbed transport.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cfb_edge_finder.catalog.discovery import MarketDiscovery
from cfb_edge_finder.catalog.pagination import paginate
from cfb_edge_finder.data.kalshi_client import KalshiClient
from tests.catalog_fakes import make_event, make_market, make_milestone, make_series


def test_kalshi_client_exposes_the_getter_the_catalog_requires():
    """`build_kalshi_cfb_catalog.py` does exactly this. If the method is
    missing or renamed, the production entrypoint cannot start -- which is
    what happened, and what this pins."""
    client = KalshiClient()
    assert hasattr(client, "get_json"), "KalshiClient.get_json is the catalog's only network seam"
    assert callable(client.get_json)


def test_get_json_signature_matches_how_pagination_calls_it():
    """`paginate` calls `getter(path=..., params=...)` BY KEYWORD. A
    positional-only signature would fail at runtime while looking correct
    to any test that calls it positionally."""
    signature = inspect.signature(KalshiClient().get_json)
    assert "path" in signature.parameters
    assert "params" in signature.parameters
    for name in ("path", "params"):
        assert signature.parameters[name].kind is not inspect.Parameter.POSITIONAL_ONLY


def test_paginate_drives_the_real_client_bound_method(monkeypatch):
    """`JsonGetter` is a structural protocol, so the only honest check is
    to actually page through the real client and see items come back."""
    transport = StubTransport({"/markets": {"markets": [{"ticker": "T1"}, {"ticker": "T2"}]}})
    monkeypatch.setattr("cfb_edge_finder.data.kalshi_client.requests.get", transport)
    sweep = paginate(KalshiClient().get_json, "/markets", {"event_ticker": "E"}, "markets")
    assert sweep.complete is True
    assert [m["ticker"] for m in sweep.items] == ["T1", "T2"]


def test_a_real_client_http_error_makes_the_sweep_incomplete_not_empty(monkeypatch):
    """Through the REAL retry loop: an unrecoverable HTTP error must reach
    pagination as an exception so the sweep reports incomplete. If the
    client swallowed it and returned {}, a failed request would publish as
    an empty market list -- the failure this whole mission targets."""
    transport = StubTransport({})  # every path 404s
    monkeypatch.setattr("cfb_edge_finder.data.kalshi_client.requests.get", transport)
    sweep = paginate(KalshiClient().get_json, "/markets", {"event_ticker": "MISSING"}, "markets")
    assert sweep.items == []
    assert sweep.complete is False
    assert sweep.failure_reason is not None


class StubTransport:
    """Stands in for `requests.get` so the REAL KalshiClient -- its retry
    loop, its parameter pruning, its JSON handling -- is exercised without
    a network. Only the socket is faked."""

    def __init__(self, routes: dict[str, dict]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.requests.append((url, dict(params or {})))
        path = url.split("/trade-api/v2", 1)[1]
        body = self.routes.get(path)
        if body is None:
            # Path-style lookup for /events/{ticker}
            body = self.routes.get(path.rsplit("/", 1)[0] + "/*")
        status = 200 if body is not None else 404
        payload = body if body is not None else {"error": {"code": "not_found"}}

        class Response:
            status_code = status
            headers: dict[str, str] = {}
            text = json.dumps(payload)

            @staticmethod
            def json():
                return payload

            @staticmethod
            def raise_for_status():
                if status >= 400:
                    import requests

                    raise requests.HTTPError(f"HTTP {status}")

        return Response()


GAME = "26SEP19UGAARK"
EVENT = f"KXNCAAFGAME-{GAME}"


@pytest.fixture
def stubbed_client(monkeypatch) -> tuple[KalshiClient, StubTransport]:
    transport = StubTransport(
        {
            "/milestones": {"milestones": [make_milestone(GAME, (EVENT,))]},
            "/series": {"series": [make_series("KXNCAAFGAME", "College Football Game")]},
            "/events": {"events": []},
            "/events/*": {"event": make_event(EVENT)},
            "/markets": {"markets": [make_market(f"{EVENT}-UGA", floor_strike=None)]},
            "/multivariate_event_collections": {"multivariate_contracts": []},
        }
    )
    monkeypatch.setattr("cfb_edge_finder.data.kalshi_client.requests.get", transport)
    return KalshiClient(), transport


def test_discovery_runs_end_to_end_through_the_real_client(stubbed_client):
    """The exact call the production entrypoint makes."""
    client, transport = stubbed_client
    run = MarketDiscovery(client.get_json).run(
        as_of=datetime(2026, 9, 17, 22, 0, tzinfo=UTC), horizon_days=10.0
    )
    assert list(run.games) == [GAME]
    assert run.games[GAME].completeness.markets_discovered == 1
    assert run.games[GAME].completeness.native_game_markets_complete is True

    # The mandatory `limit` really is sent by the real client's code path.
    paginated = [params for url, params in transport.requests if "limit" in params]
    assert paginated, "no request carried a limit -- /milestones rejects that with HTTP 400"
    assert all(int(p["limit"]) <= 200 for p in paginated), "a limit above the maximum is rejected outright"


def test_milestone_sweep_uses_the_working_filter(stubbed_client):
    """`type=football_game`, not `competition=College Football`. The latter
    returns HTTP 200 with zero rows because milestones have no
    `competition` field at all."""
    client, transport = stubbed_client
    MarketDiscovery(client.get_json).run(as_of=datetime(2026, 9, 17, 22, 0, tzinfo=UTC))
    milestone_calls = [params for url, params in transport.requests if url.endswith("/milestones")]
    assert milestone_calls, "the milestone spine was never queried"
    assert milestone_calls[0].get("type") == "football_game"
    assert "competition" not in milestone_calls[0]


def test_the_production_entrypoint_imports_and_parses_its_arguments():
    """Importing the entrypoint is what would have caught the missing
    client method. Its argument parsing is checked too, so a bad default
    cannot reach the scheduled workflow."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kalshi_cfb_catalog.py"
    spec = importlib.util.spec_from_file_location("build_kalshi_cfb_catalog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    args = module._parse_args(["--out-dir", "/tmp/x", "--horizon-days", "3"])
    assert args.out_dir == "/tmp/x"
    assert args.horizon_days == 3.0
    # -1 means "no horizon", the spelling the workflow passes through.
    assert module._parse_args(["--horizon-days", "-1"]).horizon_days == -1.0


def test_the_entrypoint_writes_all_three_artifacts(stubbed_client, tmp_path, monkeypatch):
    """A full production run, faked only at the socket."""
    import importlib.util

    client, _transport = stubbed_client
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kalshi_cfb_catalog.py"
    spec = importlib.util.spec_from_file_location("build_kalshi_cfb_catalog_run", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    exit_code = module.main(["--out-dir", str(tmp_path), "--print-summary"])
    assert exit_code == 0

    catalog = json.loads((tmp_path / "cfb_market_catalog.json").read_text())
    flat = json.loads((tmp_path / "cfb_markets_flat.json").read_text())
    status = json.loads((tmp_path / "cfb_catalog_status.json").read_text())

    assert catalog["totals"]["physical_games"] == 1
    assert catalog["totals"]["markets"] == 1
    assert flat["market_count"] == 1
    # The workflow's change detection reads exactly these keys.
    assert status["content_fingerprint"] == catalog["capture"]["content_fingerprint"]
    for key in ("physical_games", "markets", "capture_complete", "content_fingerprint"):
        assert key in status, f"the catalog workflow reads status[{key!r}]"


def test_the_entrypoint_refuses_to_publish_an_empty_catalog(tmp_path, monkeypatch):
    """Zero games is far more likely to mean a filter or API change than a
    genuinely empty college-football slate, so it must NOT overwrite a
    good catalog with an empty one."""
    import importlib.util

    transport = StubTransport(
        {
            "/milestones": {"milestones": []},
            "/series": {"series": []},
            "/events": {"events": []},
            "/markets": {"markets": []},
            "/multivariate_event_collections": {"multivariate_contracts": []},
        }
    )
    monkeypatch.setattr("cfb_edge_finder.data.kalshi_client.requests.get", transport)

    path = Path(__file__).resolve().parents[1] / "scripts" / "build_kalshi_cfb_catalog.py"
    spec = importlib.util.spec_from_file_location("build_kalshi_cfb_catalog_empty", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--out-dir", str(tmp_path)]) == 1
    assert not (tmp_path / "cfb_market_catalog.json").exists(), "an empty catalog must not be written"


def test_fetch_milestones_sends_type_not_competition(stubbed_client):
    """The diagnostic helper must not reintroduce the filter that returns
    nothing."""
    client, transport = stubbed_client
    client.fetch_milestones(milestone_type="football_game")
    params = [p for url, p in transport.requests if url.endswith("/milestones")][0]
    assert params.get("type") == "football_game"
    # None-valued params are pruned by the client, so `competition` must be absent.
    assert "competition" not in params
