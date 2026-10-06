"""Pagination must never let a failure masquerade as an empty result.

Every test here exists because the opposite behaviour already shipped once
in this repository: a 429 partway through a series sweep was read as
"0 markets and continuing", and the run reported 2,966 markets instead of
~4,578 while looking perfectly healthy.
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.catalog.pagination import PageSweep, SweepStats, paginate


class FakeApi:
    """A dict-backed Kalshi stand-in. `pages` is a list of response bodies
    returned in order; an entry that is an Exception is raised instead."""

    def __init__(self, pages: list[object]) -> None:
        self.pages = pages
        self.calls: list[dict[str, object]] = []

    def __call__(self, path: str, params: dict[str, object]) -> dict:
        self.calls.append({"path": path, **params})
        index = len(self.calls) - 1
        if index >= len(self.pages):
            raise AssertionError(f"unexpected extra request #{index} to {path}")
        page = self.pages[index]
        if isinstance(page, Exception):
            raise page
        assert isinstance(page, dict)
        return page


def test_single_page_is_complete():
    api = FakeApi([{"markets": [{"ticker": "A"}, {"ticker": "B"}]}])
    sweep = paginate(api, "/markets", {"event_ticker": "E"}, "markets")
    assert sweep.complete is True
    assert [m["ticker"] for m in sweep.items] == ["A", "B"]
    assert sweep.pages_fetched == 1
    assert sweep.failure_reason is None


def test_cursor_chain_is_followed_to_exhaustion():
    api = FakeApi(
        [
            {"markets": [{"ticker": "A"}], "cursor": "c1"},
            {"markets": [{"ticker": "B"}], "cursor": "c2"},
            {"markets": [{"ticker": "C"}]},
        ]
    )
    sweep = paginate(api, "/markets", {}, "markets")
    assert sweep.complete is True
    assert [m["ticker"] for m in sweep.items] == ["A", "B", "C"]
    assert sweep.pages_fetched == 3
    # The cursor must actually be echoed back, or we re-read page 1 forever.
    assert api.calls[1]["cursor"] == "c1"
    assert api.calls[2]["cursor"] == "c2"


def test_limit_is_always_sent():
    """Kalshi rejects a request with no `limit` on /milestones outright
    (HTTP 400 "Query argument limit is required, but not found"), and the
    first revision of this pivot's own live probe requested limit=1000 --
    above the accepted maximum -- which failed every sweep instantly and
    reported an empty market universe."""
    api = FakeApi([{"milestones": []}])
    paginate(api, "/milestones", {"type": "football_game"}, "milestones", page_limit=200)
    assert api.calls[0]["limit"] == 200


def test_empty_result_is_complete_and_distinguishable():
    api = FakeApi([{"markets": []}])
    sweep = paginate(api, "/markets", {}, "markets")
    assert sweep.items == []
    assert sweep.complete is True
    assert sweep.partial is False


def test_request_failure_on_first_page_is_incomplete_not_empty():
    api = FakeApi([RuntimeError("HTTP 429 Too Many Requests")])
    sweep = paginate(api, "/markets", {}, "markets")
    assert sweep.items == []
    assert sweep.complete is False  # THE distinction this module exists for
    assert sweep.partial is False
    assert "429" in sweep.failure_reason


def test_partial_pagination_failure_keeps_data_and_flags_incomplete():
    """The dangerous case: a non-empty but truncated sweep."""
    api = FakeApi(
        [
            {"markets": [{"ticker": "A"}], "cursor": "c1"},
            RuntimeError("HTTP 500 Internal Server Error"),
        ]
    )
    sweep = paginate(api, "/markets", {}, "markets")
    assert [m["ticker"] for m in sweep.items] == ["A"]
    assert sweep.complete is False
    assert sweep.partial is True
    assert "page 1" in sweep.failure_reason


def test_repeated_cursor_is_detected_as_a_loop():
    api = FakeApi([{"markets": [{"ticker": "A"}], "cursor": "same"}] * 3)
    sweep = paginate(api, "/markets", {}, "markets", max_pages=10)
    assert sweep.complete is False
    assert "repeated" in sweep.failure_reason
    # Caught on the second sighting rather than burning all 10 pages.
    assert sweep.pages_fetched == 2


def test_page_cap_reached_is_incomplete():
    api = FakeApi([{"markets": [{"ticker": f"T{i}"}], "cursor": f"c{i}"} for i in range(5)])
    sweep = paginate(api, "/markets", {}, "markets", max_pages=5)
    assert len(sweep.items) == 5
    assert sweep.complete is False
    assert "cap of 5 pages" in sweep.failure_reason


def test_empty_page_mid_chain_does_not_end_the_sweep():
    """Cursor presence, not page content, is the end-of-chain signal --
    stopping at an empty-but-cursored page would drop everything after."""
    api = FakeApi(
        [
            {"markets": [{"ticker": "A"}], "cursor": "c1"},
            {"markets": [], "cursor": "c2"},
            {"markets": [{"ticker": "B"}]},
        ]
    )
    sweep = paginate(api, "/markets", {}, "markets")
    assert sweep.complete is True
    assert [m["ticker"] for m in sweep.items] == ["A", "B"]


def test_non_dict_response_is_incomplete():
    class BadApi:
        def __call__(self, path: str, params: dict[str, object]):
            return ["not", "a", "dict"]

    sweep = paginate(BadApi(), "/markets", {}, "markets")
    assert sweep.complete is False
    assert "not a JSON object" in sweep.failure_reason


def test_non_dict_items_are_skipped_without_crashing():
    api = FakeApi([{"markets": [{"ticker": "A"}, "garbage", None, {"ticker": "B"}]}])
    sweep = paginate(api, "/markets", {}, "markets")
    assert [m["ticker"] for m in sweep.items] == ["A", "B"]
    assert sweep.complete is True


def test_page_sweep_cannot_lie_about_itself():
    with pytest.raises(ValueError, match="cannot carry a failure_reason"):
        PageSweep(items=[], complete=True, pages_fetched=1, failure_reason="boom")
    with pytest.raises(ValueError, match="must explain itself"):
        PageSweep(items=[], complete=False, pages_fetched=1)


def test_sweep_stats_accumulate_failures():
    stats = SweepStats()
    stats.record_sweep("/markets a", PageSweep(items=[], complete=True, pages_fetched=2))
    stats.record_sweep(
        "/markets b", PageSweep(items=[], complete=False, pages_fetched=1, failure_reason="429")
    )
    assert stats.requests_made == 3
    assert stats.pagination_failures == 1
    assert stats.failed_paths == ["/markets b: 429"]
