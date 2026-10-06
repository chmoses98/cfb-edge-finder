"""Discovery: from Kalshi's exchange to one complete menu per physical game.

===========================================================================
THE FLOW, AND THE LIVE EVIDENCE BEHIND EVERY STEP
===========================================================================
  1. GET /milestones?type=football_game  (paginated)
        -> filter details.league == "NCAAFB", live status
        -> ONE ROW PER PHYSICAL GAME, carrying its own event list
     The mission's documented filter (competition="College Football")
     returns zero rows because milestones have no `competition` field at
     all; `type` + `details.league` is the working equivalent, and it
     costs ~11 pages for the whole exchange.

  2. For each game: union(primary_event_tickers, related_event_tickers)
        -> every Kalshi event attached to that game

  3. For each event: GET /markets?event_ticker=... with COMPLETE cursor
     pagination -> every contract. The event's own inline market list is
     not trusted: the /events list endpoint with with_nested_markets
     returned only ONE market for events that genuinely had 22.

  4. SECOND, INDEPENDENT PATH: discover CFB series dynamically from
     GET /series?category=Sports, sweep their open events, and attach any
     game-level event the milestone path did not name. Its purpose is to
     make a miss VISIBLE rather than to be the spine.

  5. Classify every contract (label, never filter). Unknown is retained.

===========================================================================
WHY TWO PATHS
===========================================================================
A single discovery path fails silently. If a series is missed -- a ticker
we do not recognize, a family Kalshi launched this morning -- its markets
are simply absent from the artifact and nothing in the output says so.
Two paths that must agree turn that invisible gap into a reported number
(`reconciliation.events_only_in_series_sweep`).

This is not theoretical. The previous architecture drove discovery from a
hardcoded list of eight "known" CFB series. A live sweep during this pivot
found 145 CFB series, 98 of them with open events -- quarter spreads,
quarter totals, second-half lines, both-teams-to-score, a dozen team-stat
families, first-touchdown, overtime. An allowlist would have published a
confident, complete-looking menu missing most of what Kalshi actually
offers.

===========================================================================
WHY A FAILURE IS NEVER A ZERO
===========================================================================
Every fetch returns a `PageSweep` carrying `complete`. A failed event fetch
records the event ticker in `failed_event_tickers` and marks the game
INCOMPLETE; it never reduces to "0 markets". The exact incident that
motivates this is in KalshiClient._get's own docstring: a 429 partway
through a series was treated as "0 markets and continuing", and the run
reported 2,966 markets instead of ~4,578 while looking perfectly healthy.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from cfb_edge_finder.catalog.classification import (
    LADDER_FAMILIES,
    MarketFamilyLabel,
    classify_market,
)
from cfb_edge_finder.catalog.contract import CatalogContract, build_contract
from cfb_edge_finder.catalog.fees import resolve_effective_fee
from cfb_edge_finder.catalog.identity import (
    MILESTONE_TYPE_FOOTBALL_GAME,
    MilestoneGame,
    is_cfb_event,
    is_upcoming_cfb_milestone,
    milestone_from_payload,
    parse_event_ticker,
)
from cfb_edge_finder.catalog.pagination import PageSweep, SweepStats, paginate

# Kalshi's QUERY vocabulary is not its RESPONSE vocabulary. Querying
# status=open returns markets whose status reads "active"; status=settled
# returns "finalized"; and status=active is rejected outright with HTTP
# 400. Conflating the two is how a sweep asks for something the API
# refuses and reads the refusal as an empty market set.
QUERY_STATUS_OPEN = "open"
TRADEABLE_STATUS_VALUES = frozenset({"active", "open"})
UNOPENED_STATUS_VALUES = frozenset({"unopened", "initialized"})
PAUSED_STATUS_VALUES = frozenset({"paused"})
CLOSED_STATUS_VALUES = frozenset({"closed", "finalized", "settled", "determined"})

PREFETCH_CLOSE_BUFFER_DAYS = 7.0
"""How far back the bulk market prefetch reaches, via /markets'
`min_close_ts`. Markets for an upcoming game always close in the future, so
this cannot hide anything in the published horizon; it exists only to keep
the sweep from re-reading a whole season of settled markets on every run
(KXNCAAFSPREAD alone carries 9,360 markets across 47 pages unfiltered).
A week of slack covers a game that has just finished but whose markets have
not finalized yet."""


@dataclass
class GameCompleteness:
    """Per-game diagnostics. A consumer must be able to tell a thin menu
    from a broken capture without leaving the artifact."""

    related_event_tickers_reported: int = 0
    events_fetched: int = 0
    failed_event_tickers: list[str] = field(default_factory=list)
    pagination_failed_event_tickers: list[str] = field(default_factory=list)
    markets_discovered: int = 0
    markets_open: int = 0
    markets_unopened: int = 0
    markets_paused: int = 0
    markets_closed: int = 0
    markets_other_status: int = 0
    markets_classified: int = 0
    markets_unknown: int = 0
    api_failures: int = 0
    events_added_by_series_sweep: list[str] = field(default_factory=list)
    """Game-level events the milestone did NOT name but the independent
    series sweep found. Non-empty means the milestone's own event list was
    not the whole story for this game -- captured anyway, and reported."""

    @property
    def native_game_markets_complete(self) -> bool:
        """True only if every event we were told about was fetched in full.

        Deliberately NOT a statement about combo/multivariate markets --
        those are a separate, honestly-separate field (see
        CatalogGame.multivariate_market_coverage)."""
        return (
            not self.failed_event_tickers
            and not self.pagination_failed_event_tickers
            and self.api_failures == 0
            and self.events_fetched >= self.related_event_tickers_reported
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "related_event_tickers_reported": self.related_event_tickers_reported,
            "events_fetched": self.events_fetched,
            "failed_event_tickers": sorted(self.failed_event_tickers),
            "pagination_failed_event_tickers": sorted(self.pagination_failed_event_tickers),
            "events_added_by_series_sweep": sorted(self.events_added_by_series_sweep),
            "markets_discovered": self.markets_discovered,
            "markets_open": self.markets_open,
            "markets_unopened": self.markets_unopened,
            "markets_paused": self.markets_paused,
            "markets_closed": self.markets_closed,
            "markets_other_status": self.markets_other_status,
            "markets_classified": self.markets_classified,
            "markets_unknown": self.markets_unknown,
            "api_failures": self.api_failures,
            "native_game_markets_complete": self.native_game_markets_complete,
        }


@dataclass
class CatalogGame:
    """One physical game and its complete discovered market inventory."""

    game_key: str
    milestone: MilestoneGame | None
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    contracts: list[CatalogContract] = field(default_factory=list)
    completeness: GameCompleteness = field(default_factory=GameCompleteness)
    multivariate_eligible_event_tickers: list[str] = field(default_factory=list)
    """Events of THIS game that a Kalshi multivariate collection lists as
    combo-eligible legs. See MULTIVARIATE_COVERAGE_NOTE for why this is
    the strongest honest claim available."""

    @property
    def title(self) -> str | None:
        """Kalshi's own label for the matchup, taken from the game-level
        event that reads most cleanly ("Syracuse vs Pittsburgh" rather
        than "Syracuse vs Pittsburgh: 1st Half Spread")."""
        if self.milestone and self.milestone.title:
            return self.milestone.title
        for event in self.events.values():
            metadata = event.get("product_metadata") or {}
            if str(metadata.get("competition_scope") or "").strip().lower() == "game":
                return event.get("title")
        for event in self.events.values():
            title = event.get("title")
            if title:
                return str(title).split(":")[0].strip()
        return None

    @property
    def kickoff(self) -> datetime | None:
        if self.milestone and self.milestone.start_date:
            return self.milestone.start_date
        occurrences = [c.occurrence_datetime for c in self.contracts if c.occurrence_datetime]
        return min(occurrences) if occurrences else None

    @property
    def family_distribution(self) -> dict[str, int]:
        counts = Counter(str(c.classification.family) for c in self.contracts)
        return dict(sorted(counts.items()))


MULTIVARIATE_COVERAGE_NOTE = (
    "Kalshi's CFB combo/parlay markets are DYNAMICALLY INSTANTIATED, not pre-listed per game. Live "
    "evidence: exactly three multivariate collections reference NCAAF events "
    "(KXMVESPORTSMULTIGAMEEXTENDED-R, KXMVECROSSCATEGORY-R, KXMVECROSSCATEGORY-SHARD1-R), each listing "
    "the same 1,018 NCAAF event tickers among 2,603 cross-sport legs, with size_min=2 and "
    "functional_description 'The resulting market will only resolve to YES if every associated market "
    "resolves to YES'. An instantiated combo appears under a synthetic hash-suffixed event "
    "(KXMVESPORTSMULTIGAMEEXTENDED-S20264E1F9411A8E) whose legs are named in custom_strike rather than "
    "under any single game's event. There is therefore NO endpoint that enumerates the combos available "
    "for one physical game, and this catalog does not claim to cover them. What it does report per game "
    "is which of that game's events Kalshi lists as combo-eligible legs."
)


@dataclass
class CatalogRun:
    """One complete capture."""

    captured_at: datetime
    games: dict[str, CatalogGame] = field(default_factory=dict)
    season_level_events: dict[str, dict[str, Any]] = field(default_factory=dict)
    """CFB inventory with no physical game (conference champions, awards,
    weekly polls, win totals). Classified and retained, kept out of the
    per-game menus so a game's market list stays a game's market list."""
    milestones_considered: int = 0
    milestones_selected: int = 0
    milestone_sweep_complete: bool = True
    series_discovered: int = 0
    series_with_open_events: int = 0
    series_sweep_complete: bool = True
    events_only_in_series_sweep: list[str] = field(default_factory=list)
    events_only_in_milestones: list[str] = field(default_factory=list)
    stats: SweepStats = field(default_factory=SweepStats)
    errors: list[str] = field(default_factory=list)
    prefetched_events: int = 0
    prefetched_markets: int = 0
    events_served_from_prefetch: int = 0
    events_fetched_individually: int = 0
    """How many events came from the bulk index vs. a direct per-event
    fetch. Published so a cheap run and an expensive one are
    distinguishable, and so a sudden jump in individual fetches -- which
    means the bulk sweeps are failing or missing series -- is visible."""

    @property
    def total_contracts(self) -> int:
        return sum(len(g.contracts) for g in self.games.values())

    @property
    def complete(self) -> bool:
        return (
            self.milestone_sweep_complete
            and self.series_sweep_complete
            and not self.errors
            and all(g.completeness.native_game_markets_complete for g in self.games.values())
        )


class MarketDiscovery:
    """Drives the capture. Network access is confined to `getter`, which
    is `KalshiClient.get_json` in production and a dict-backed fake in
    tests -- every completeness edge case below is therefore testable with
    no network at all."""

    def __init__(self, getter: Any, page_limit: int = 200) -> None:
        self._get = getter
        self._page_limit = page_limit

    # --- primitives -----------------------------------------------------
    def _sweep(self, path: str, params: dict[str, object], list_key: str, stats: SweepStats) -> PageSweep:
        sweep = paginate(self._get, path, params, list_key, page_limit=self._page_limit)
        stats.record_sweep(f"{path} {params}", sweep)
        return sweep

    def fetch_football_game_milestones(self, stats: SweepStats) -> PageSweep:
        return self._sweep("/milestones", {"type": MILESTONE_TYPE_FOOTBALL_GAME}, "milestones", stats)

    def fetch_event_markets(self, event_ticker: str, stats: SweepStats) -> PageSweep:
        """Every market for one event, fully paginated.

        The event's inline market list is deliberately not used: a live
        probe found /events?with_nested_markets returning 1 market for
        events that actually had 22."""
        return self._sweep("/markets", {"event_ticker": event_ticker}, "markets", stats)

    def fetch_event(self, event_ticker: str, stats: SweepStats) -> dict[str, Any] | None:
        try:
            body = self._get(path=f"/events/{event_ticker}", params={})
        except Exception as exc:  # noqa: BLE001
            stats.request_failures += 1
            stats.failed_paths.append(f"/events/{event_ticker}: {exc}")
            return None
        stats.requests_made += 1
        if not isinstance(body, dict):
            return None
        event = body.get("event")
        return event if isinstance(event, dict) else body

    def fetch_sports_series(self, stats: SweepStats) -> PageSweep:
        return self._sweep("/series", {"category": "Sports"}, "series", stats)

    def fetch_series_events(self, series_ticker: str, stats: SweepStats) -> PageSweep:
        return self._sweep(
            "/events", {"series_ticker": series_ticker, "status": QUERY_STATUS_OPEN}, "events", stats
        )

    def fetch_multivariate_collections(self, stats: SweepStats) -> PageSweep:
        return self._sweep("/multivariate_event_collections", {}, "multivariate_contracts", stats)

    def fetch_series_markets(self, series_ticker: str, min_close_ts: int | None, stats: SweepStats) -> PageSweep:
        """Every market of one SERIES in one paginated sweep.

        *** WHY THIS EXISTS: A MEASURED 25x REQUEST REDUCTION ***
        Fetching markets per event is the obviously-correct primitive, and
        it is what `fetch_event_markets` does -- but the live slate has
        ~239 games x ~29 events each, so a per-event sweep costs ~14,000
        requests. A real audit run against the live exchange was still
        going after 17 minutes, against a 30-minute refresh cadence and a
        30-minute job timeout. A capture that cannot finish inside its own
        cadence is not a capture.

        One sweep per series (~98 series, a few pages each) covers the same
        markets in roughly 400 requests. `min_close_ts` bounds it further
        by excluding markets that closed long ago; markets for an upcoming
        game always close in the future, so nothing in the published
        horizon is filtered out by it.

        This does NOT weaken the completeness guarantee. The sweep's
        `complete` flag is tracked per series, and the caller falls back to
        a per-event fetch whenever the series sweep did not complete, the
        series was never swept, or the bucket for an event comes back
        empty -- so an empty bucket is always confirmed directly rather
        than published as "no markets"."""
        return self._sweep(
            "/markets", {"series_ticker": series_ticker, "min_close_ts": min_close_ts}, "markets", stats
        )

    def prefetch_markets_by_series(
        self, series_tickers: list[str], min_close_ts: int | None, stats: SweepStats
    ) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
        """Bulk-load markets for many series, bucketed by event ticker.

        Returns (markets_by_event, fully_swept_series). Only a series in
        `fully_swept_series` may be trusted for a negative answer."""
        markets_by_event: dict[str, list[dict[str, Any]]] = {}
        fully_swept: set[str] = set()
        for series_ticker in sorted(series_tickers):
            sweep = self.fetch_series_markets(series_ticker, min_close_ts, stats)
            if sweep.complete:
                fully_swept.add(series_ticker)
            for market in sweep.items:
                event_ticker = market.get("event_ticker")
                if isinstance(event_ticker, str) and event_ticker:
                    markets_by_event.setdefault(event_ticker, []).append(market)
        return markets_by_event, fully_swept

    # --- orchestration --------------------------------------------------
    def run(
        self,
        as_of: datetime | None = None,
        horizon_days: float | None = 10.0,
        include_series_reconciliation: bool = True,
        include_multivariate: bool = True,
    ) -> CatalogRun:
        now = as_of or datetime.now(UTC)
        run = CatalogRun(captured_at=now)

        # ---- path 1: milestones as the physical-game spine -------------
        milestone_sweep = self.fetch_football_game_milestones(run.stats)
        run.milestone_sweep_complete = milestone_sweep.complete
        if not milestone_sweep.complete:
            run.errors.append(f"milestone sweep incomplete: {milestone_sweep.failure_reason}")
        run.milestones_considered = len(milestone_sweep.items)

        selected: list[MilestoneGame] = []
        for payload in milestone_sweep.items:
            milestone = milestone_from_payload(payload)
            if is_upcoming_cfb_milestone(milestone, now, horizon_days):
                selected.append(milestone)
        run.milestones_selected = len(selected)

        series_fees = self._series_fee_index(run)
        cfb_series = sorted(
            ticker
            for ticker, series in series_fees.items()
            if self._series_looks_college_football(ticker, series)
        )

        # ---- bulk prefetch: markets and events, in series-sized sweeps -
        # Per-event fetching is ~14,000 requests on a live slate and could
        # not finish inside the refresh cadence (see
        # fetch_series_markets' docstring). These two index builds cover
        # the same ground in ~400, and every negative answer they give is
        # confirmed by a direct per-event fetch before it is published.
        min_close_ts = int((now - timedelta(days=PREFETCH_CLOSE_BUFFER_DAYS)).timestamp())
        market_index, fully_swept_series = self.prefetch_markets_by_series(cfb_series, min_close_ts, run.stats)
        event_index, series_events = self._build_event_index(cfb_series, run)
        run.prefetched_events = len(event_index)
        run.prefetched_markets = sum(len(v) for v in market_index.values())

        for milestone in selected:
            game_key = milestone.game_key
            if game_key is None:
                # A live CFB milestone whose event tickers we cannot parse
                # into a game key is a real gap, not something to discard.
                run.errors.append(
                    f"milestone {milestone.milestone_id} ({milestone.title!r}) has no parseable game key; "
                    f"event_tickers={list(milestone.event_tickers)[:5]}"
                )
                continue
            game = run.games.setdefault(game_key, CatalogGame(game_key=game_key, milestone=milestone))
            if game.milestone is None:
                game.milestone = milestone
            self._capture_game_events(
                game,
                list(milestone.event_tickers),
                run,
                series_fees,
                market_index=market_index,
                event_index=event_index,
                fully_swept_series=fully_swept_series,
            )

        # ---- path 2: independent series sweep, for visibility ----------
        if include_series_reconciliation:
            self._reconcile_with_series_sweep(
                run, series_fees, series_events, market_index, event_index, fully_swept_series
            )

        # ---- combo eligibility, honestly scoped ------------------------
        if include_multivariate:
            self._attach_multivariate_eligibility(run)

        self._finalize_alternate_lines(run)
        return run

    def _series_fee_index(self, run: CatalogRun) -> dict[str, dict[str, Any]]:
        """Fee metadata lives on the SERIES (`fee_type`, `fee_multiplier`),
        not on the market, so it is fetched once and joined in. A failure
        here degrades fee fields to None rather than failing the capture --
        a missing fee is a missing field, not a missing market."""
        sweep = self.fetch_sports_series(run.stats)
        run.series_discovered = len(sweep.items)
        run.series_sweep_complete = sweep.complete
        if not sweep.complete:
            run.errors.append(f"series sweep incomplete: {sweep.failure_reason}")
        index: dict[str, dict[str, Any]] = {}
        for series in sweep.items:
            ticker = str(series.get("ticker") or "")
            if ticker:
                index[ticker] = series
        return index

    def _build_event_index(
        self, cfb_series: list[str], run: CatalogRun
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """One /events sweep per CFB series, indexed by event ticker.

        The list endpoint returns FULL event objects (title, sub_title,
        product_metadata, settlement_sources), so this removes the
        per-event GET /events/{ticker} call as well -- and it is the same
        sweep the reconciliation path needs, so it is paid for once and
        used twice. `with_nested_markets` is deliberately NOT requested:
        it returned 1 market for events that genuinely had 22."""
        event_index: dict[str, dict[str, Any]] = {}
        series_events: dict[str, dict[str, Any]] = {}
        for series_ticker in cfb_series:
            sweep = self.fetch_series_events(series_ticker, run.stats)
            if not sweep.complete:
                run.series_sweep_complete = False
                run.errors.append(f"event sweep incomplete for {series_ticker}: {sweep.failure_reason}")
            if sweep.items:
                run.series_with_open_events += 1
            for event in sweep.items:
                event_ticker = str(event.get("event_ticker") or event.get("ticker") or "")
                if not event_ticker:
                    continue
                event_index[event_ticker] = event
                if is_cfb_event(event):
                    series_events[event_ticker] = event
        return event_index, series_events

    def _capture_game_events(
        self,
        game: CatalogGame,
        event_tickers: list[str],
        run: CatalogRun,
        series_fees: dict[str, dict[str, Any]],
        from_series_sweep: bool = False,
        market_index: dict[str, list[dict[str, Any]]] | None = None,
        event_index: dict[str, dict[str, Any]] | None = None,
        fully_swept_series: set[str] | None = None,
    ) -> None:
        completeness = game.completeness
        if not from_series_sweep:
            completeness.related_event_tickers_reported = max(
                completeness.related_event_tickers_reported, len(event_tickers)
            )
        market_index = market_index if market_index is not None else {}
        event_index = event_index if event_index is not None else {}
        fully_swept_series = fully_swept_series if fully_swept_series is not None else set()

        for event_ticker in event_tickers:
            if event_ticker in game.events:
                continue
            parts = parse_event_ticker(event_ticker)
            if parts is None:
                # Not a game-level event (a season-level ticker listed on a
                # milestone). Keep it in the season bucket, not the menu.
                run.season_level_events.setdefault(event_ticker, {"event_ticker": event_ticker})
                continue

            event = event_index.get(event_ticker) or self.fetch_event(event_ticker, run.stats)
            if event is None:
                completeness.failed_event_tickers.append(event_ticker)
                completeness.api_failures += 1
                continue
            game.events[event_ticker] = event
            completeness.events_fetched += 1
            if from_series_sweep:
                completeness.events_added_by_series_sweep.append(event_ticker)

            series_ticker = str(event.get("series_ticker") or parts.series_ticker)

            # Trust the bulk index ONLY for a non-empty bucket from a
            # series that swept completely. An empty bucket, or a series
            # whose sweep failed or was never run, falls through to a
            # direct per-event fetch -- so "no markets" is always a
            # confirmed answer rather than an absence in an index.
            prefetched = market_index.get(event_ticker)
            if prefetched and series_ticker in fully_swept_series:
                markets = prefetched
                run.events_served_from_prefetch += 1
            else:
                sweep = self.fetch_event_markets(event_ticker, run.stats)
                markets = sweep.items
                run.events_fetched_individually += 1
                if not sweep.complete:
                    completeness.pagination_failed_event_tickers.append(event_ticker)
                    completeness.api_failures += 1

            # Fee precedence, as Kalshi documents it: an event's
            # fee_type_override / fee_multiplier_override wins over the
            # parent series' fee_type / fee_multiplier. The event object is
            # already in hand, so honouring this costs no extra request.
            # A series we never resolved yields NO fee rather than a
            # defaulted one -- see catalog/fees.py on failing closed.
            series = series_fees.get(series_ticker)
            effective_fee = resolve_effective_fee(
                event, series, series_lookup_succeeded=series is not None
            )
            settlement_sources = event.get("settlement_sources") or []

            for market in markets:
                contract = build_contract(
                    market,
                    game_key=game.game_key,
                    series_ticker=series_ticker,
                    settlement_sources=settlement_sources,
                    effective_fee=effective_fee,
                    captured_at=run.captured_at,
                    classification=classify_market(
                        series_ticker, market.get("title"), market.get("rules_primary")
                    ),
                )
                game.contracts.append(contract)

        self._recount(game)

    @staticmethod
    def _recount(game: CatalogGame) -> None:
        """Recompute status/classification tallies from the contracts held.
        Derived rather than incremented so a re-entrant capture (an event
        added later by the series sweep) can never double-count."""
        c = game.completeness
        seen: dict[str, CatalogContract] = {}
        for contract in game.contracts:
            seen[contract.market_ticker] = contract
        if len(seen) != len(game.contracts):
            game.contracts = list(seen.values())

        c.markets_discovered = len(game.contracts)
        c.markets_open = 0
        c.markets_unopened = 0
        c.markets_paused = 0
        c.markets_closed = 0
        c.markets_other_status = 0
        c.markets_classified = 0
        c.markets_unknown = 0
        for contract in game.contracts:
            status = (contract.status or "").lower()
            if status in TRADEABLE_STATUS_VALUES:
                c.markets_open += 1
            elif status in UNOPENED_STATUS_VALUES:
                c.markets_unopened += 1
            elif status in PAUSED_STATUS_VALUES:
                c.markets_paused += 1
            elif status in CLOSED_STATUS_VALUES:
                c.markets_closed += 1
            else:
                c.markets_other_status += 1
            if contract.classification.family == MarketFamilyLabel.UNKNOWN:
                c.markets_unknown += 1
            else:
                c.markets_classified += 1

    def _reconcile_with_series_sweep(
        self,
        run: CatalogRun,
        series_fees: dict[str, dict[str, Any]],
        series_events: dict[str, dict[str, Any]],
        market_index: dict[str, list[dict[str, Any]]],
        event_index: dict[str, dict[str, Any]],
        fully_swept_series: set[str],
    ) -> None:
        """Attach anything the milestone path did not name.

        `series_events` comes from `_build_event_index`, which swept every
        CFB series Kalshi currently lists -- selection read at runtime, so
        a family launched today is swept today. It is not an allowlist: it
        decides only where to LOOK, and every event found is kept
        regardless of which series produced it.

        This path exists to make a milestone's omission VISIBLE. A single
        discovery path fails silently; a gap here becomes
        `discovery.events_only_in_series_sweep` in the artifact."""
        milestone_events = {ticker for game in run.games.values() for ticker in game.events}

        for event_ticker, event in sorted(series_events.items()):
            if event_ticker in milestone_events:
                continue
            parts = parse_event_ticker(event_ticker)
            if parts is None:
                run.season_level_events[event_ticker] = event
                continue
            run.events_only_in_series_sweep.append(event_ticker)
            game = run.games.setdefault(parts.game_key, CatalogGame(game_key=parts.game_key, milestone=None))
            self._capture_game_events(
                game,
                [event_ticker],
                run,
                series_fees,
                from_series_sweep=True,
                market_index=market_index,
                event_index=event_index,
                fully_swept_series=fully_swept_series,
            )

        run.events_only_in_milestones = sorted(milestone_events - set(series_events))

    @staticmethod
    def _series_looks_college_football(ticker: str, series: dict[str, Any]) -> bool:
        """Generous on purpose. A false positive costs one wasted event
        request whose results are then discarded by `is_cfb_event`; a false
        negative costs an entire silently-missing market family."""
        upper_ticker = ticker.upper()
        title = str(series.get("title") or "").upper()
        tags = [str(t).upper() for t in (series.get("tags") or [])]
        if upper_ticker.startswith(("KXNCAAF", "KXCFB", "KXCFP")):
            return True
        if "COLLEGE FOOTBALL" in title:
            return True
        return "FOOTBALL" in tags and "NCAA" in upper_ticker

    def _attach_multivariate_eligibility(self, run: CatalogRun) -> None:
        """Record which of each game's events are combo-eligible legs.

        This is the strongest claim the API supports -- see
        MULTIVARIATE_COVERAGE_NOTE. A failure here is non-fatal and leaves
        the coverage field empty rather than failing the capture, because
        combo eligibility is supplementary to the native game menu."""
        sweep = self.fetch_multivariate_collections(run.stats)
        if not sweep.complete:
            run.errors.append(f"multivariate sweep incomplete: {sweep.failure_reason}")

        eligible: set[str] = set()
        for collection in sweep.items:
            for ticker in collection.get("associated_event_tickers") or []:
                if isinstance(ticker, str):
                    eligible.add(ticker)

        for game in run.games.values():
            game.multivariate_eligible_event_tickers = sorted(t for t in game.events if t in eligible)

    @staticmethod
    def _finalize_alternate_lines(run: CatalogRun) -> None:
        """Mark ladder rungs.

        A market is an alternate line only relative to its siblings: within
        one event and family, more than one distinct strike means a ladder,
        and every rung is flagged. A single market cannot know this about
        itself, which is why it is decided here and not in the classifier.
        Moneylines are excluded -- two moneyline contracts are two sides of
        one market, not two rungs of a ladder."""
        for game in run.games.values():
            groups: dict[tuple[str | None, str], set[float]] = {}
            for contract in game.contracts:
                if contract.classification.family not in LADDER_FAMILIES:
                    continue
                if contract.semantics.floor_strike is None:
                    continue
                key = (contract.event_ticker, str(contract.classification.family))
                groups.setdefault(key, set()).add(contract.semantics.floor_strike)

            ladder_keys = {key for key, strikes in groups.items() if len(strikes) > 1}
            if not ladder_keys:
                continue
            game.contracts = [
                replace(contract, classification=replace(contract.classification, is_alternate_line=True))
                if (contract.event_ticker, str(contract.classification.family)) in ladder_keys
                else contract
                for contract in game.contracts
            ]
