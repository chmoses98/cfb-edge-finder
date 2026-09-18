"""Cursor pagination whose result can never be mistaken for an empty one.

*** WHY THIS EXISTS RATHER THAN REUSING KalshiClient._paginate ***
`data/kalshi_client.py`'s `_paginate` returns a bare `list[dict]`. That is
fine when an exception is allowed to propagate, but it makes two genuinely
different outcomes look identical to any caller that guards the call:

  - "this event has no markets"        -> []
  - "page 3 of 7 hit MAX_PAGES"        -> [first 600 markets]

The second is a silent, partial answer. That is not a hypothetical: the
live collection run recorded in `KalshiClient._get`'s docstring reported
2,966 markets instead of ~4,578 and looked perfectly healthy, because a
429 partway through a series was treated as "0 markets and continuing".
This mission's completeness requirement is precisely that such a run must
be INCOMPLETE and must say so, so pagination here returns the items AND
the truth about how the sweep ended.

`PageSweep.complete` is therefore the load-bearing field, and it is False
whenever the sweep ended for any reason other than the server telling us
it was done.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

DEFAULT_PAGE_LIMIT = 200
"""Kalshi's list endpoints reject a limit above their own maximum, and a
rejected request is an error, not an empty page. The live probe behind
this pivot initially requested limit=1000 and every paginated call failed
instantly -- reporting zero milestones, zero events and zero markets while
non-paginated calls in the same run succeeded. 200 is the value the probe
then verified against /milestones, /events, /markets and /series alike."""

MAX_PAGES = 200
"""A hard stop so a malformed or cyclic cursor cannot loop forever. Set
high enough that hitting it means something is genuinely wrong (200 pages
x 200 = 40,000 items from one filtered query), and when it IS hit the
sweep is reported incomplete rather than quietly truncated."""


class JsonGetter(Protocol):
    """Whatever can perform one GET and return parsed JSON, raising on
    failure. `KalshiClient.get_json` satisfies this, and so does a test
    double -- which is the point: every pagination edge case below is
    unit-testable with no network."""

    def __call__(self, path: str, params: dict[str, object]) -> dict: ...


@dataclass(frozen=True)
class PageSweep:
    """The outcome of one paginated sweep."""

    items: list[dict]
    complete: bool
    """True only if the server signalled the end of the cursor chain.
    False for a request failure, a page-limit stop, or a repeated cursor."""
    pages_fetched: int
    failure_reason: str | None = None
    """Human-readable cause when `complete` is False. Carried into the
    catalog's diagnostics so a partial game menu explains itself."""
    partial: bool = False
    """True when the sweep failed AFTER collecting at least one page. These
    are the dangerous ones -- a non-empty but incomplete result is exactly
    what silently under-reports a market menu."""

    def __post_init__(self) -> None:
        if self.complete and self.failure_reason is not None:
            raise ValueError("a complete sweep cannot carry a failure_reason")
        if not self.complete and self.failure_reason is None:
            raise ValueError("an incomplete sweep must explain itself via failure_reason")


@dataclass
class SweepStats:
    """Request-level accounting for one capture run, so the artifact can
    state how much of it was actually observed vs. inferred."""

    requests_made: int = 0
    request_failures: int = 0
    pagination_failures: int = 0
    failed_paths: list[str] = field(default_factory=list)

    def record_sweep(self, path: str, sweep: PageSweep) -> None:
        self.requests_made += sweep.pages_fetched
        if not sweep.complete:
            self.pagination_failures += 1
            self.failed_paths.append(f"{path}: {sweep.failure_reason}")


def paginate(
    getter: JsonGetter,
    path: str,
    params: dict[str, object],
    list_key: str,
    page_limit: int = DEFAULT_PAGE_LIMIT,
    max_pages: int = MAX_PAGES,
) -> PageSweep:
    """Follow Kalshi's `cursor` chain to exhaustion, reporting honestly.

    Termination, in the order checked:
      1. the request raised            -> incomplete (partial if we had pages)
      2. the response had no cursor    -> COMPLETE, the only success case
      3. the cursor repeated           -> incomplete (a server-side loop;
                                          without this check `max_pages`
                                          would burn 200 requests first)
      4. `max_pages` reached           -> incomplete

    An empty page that still carries a cursor is followed rather than
    treated as the end: Kalshi may legitimately return a short or empty
    page mid-chain, and stopping there would drop everything after it.
    Cursor presence, not page content, is the end-of-chain signal.
    """
    items: list[dict] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()

    for page_index in range(max_pages):
        page_params = dict(params, limit=page_limit)
        if cursor:
            page_params["cursor"] = cursor
        try:
            body = getter(path=path, params=page_params)
        except Exception as exc:  # noqa: BLE001 -- any failure is the same answer here
            reason = f"request failed on page {page_index}: {type(exc).__name__}: {exc}"
            return PageSweep(
                items=items,
                complete=False,
                pages_fetched=page_index,
                failure_reason=reason,
                partial=bool(items),
            )

        if not isinstance(body, dict):
            return PageSweep(
                items=items,
                complete=False,
                pages_fetched=page_index,
                failure_reason=f"page {page_index} was {type(body).__name__}, not a JSON object",
                partial=bool(items),
            )

        page_items = body.get(list_key) or []
        if isinstance(page_items, list):
            items.extend(item for item in page_items if isinstance(item, dict))

        next_cursor = body.get("cursor") or None
        if not next_cursor:
            return PageSweep(items=items, complete=True, pages_fetched=page_index + 1)
        if next_cursor in seen_cursors:
            return PageSweep(
                items=items,
                complete=False,
                pages_fetched=page_index + 1,
                failure_reason=f"cursor {next_cursor!r} repeated -- server-side pagination loop",
                partial=bool(items),
            )
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    return PageSweep(
        items=items,
        complete=False,
        pages_fetched=max_pages,
        failure_reason=f"pagination cap of {max_pages} pages reached with a cursor still outstanding",
        partial=bool(items),
    )
