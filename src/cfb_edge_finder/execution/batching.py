"""Batching by REASONING LOAD, not by bytes.

*** THE MEASUREMENT THAT MOTIVATED THIS ***
The 2026-09-19 slate, rebuilt from the retained catalog: 115 games, 14,222
eligible contracts, four kickoff-window shards of 134-591 KB. Byte sharding
had already done its job -- every window fitted in one file. And the early
window still asked one reader to handicap 31 games and inspect 3,710 contract
rows before it could name a single bet.

Bytes were never the constraint. A 448 KB file is not slow to open; 31 games
are slow to handicap, and 3,710 rows of alternate ladders are slow to read
through when 29 of every 30 of them are a mathematical consequence of a
distribution the reader has not written down yet.

So the primary unit of work is a HANDICAP BATCH: five to eight games' worth of
football reasoning, sized by a deterministic cost model, carrying FACTS and NO
CONTRACT ROWS. The contracts are priced by the evaluator once the batch's
handicaps come back, which is work a computer does in milliseconds and a
reader does in hours.

*** THE BYTE BUDGET DOES NOT GO AWAY ***
It stops being the primary strategy and stays as a safeguard. A batch that
somehow exceeds the encoding budget still splits, because a file nobody can
upload is no more useful for being correctly sized in games.

*** A GAME IS STILL NEVER SPLIT ***
Same rule, same reason: a handicap cannot be applied to markets that arrived in
a different conversation. A single game whose cost exceeds the whole batch
budget gets a batch of its own, and the manifest says so rather than the budget
winning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from cfb_edge_finder.execution import (
    CONTEXT_SCHEMA_VERSION,
    HANDICAP_BATCH_SCHEMA_VERSION,
)
from cfb_edge_finder.execution.context import CONTEXT_DOMAINS
from cfb_edge_finder.execution.quality import FieldQuality

#: Target reasoning cost per batch, in "average games".
#:
#: A cost rather than a game COUNT, because games are not interchangeable. The
#: 2026-09-19 slate is bimodal and measurably so: 79 of its 115 games carry two
#: scoring periods, four market families and ~28 contracts, while 36 carry seven
#: periods, nineteen families and ~300. The second kind is genuinely about three
#: times the football reasoning of the first, and a fixed "six games" would hand
#: a reader either six easy games or six hard ones and call them the same batch.
#:
#: At this budget the retained slate batches into runs of 3 (all-heavy) to 8
#: (all-light) games, which is the 5-8 design target hit by measuring the games
#: rather than by counting to six.
DEFAULT_BATCH_COST_BUDGET = 6.5

#: Hard ceiling on games per batch, whatever the cost model says.
#:
#: The cost model measures what this repository can see -- periods, families,
#: explicit tickers, factual gaps, breadth. It cannot see that a human reading
#: nine games in one sitting is worse at the ninth than at the first. This is
#: the safeguard for the part of the load that is not in the data.
MAX_GAMES_PER_BATCH = 8

#: A single game may not exceed this share of a batch on its own. Past it, the
#: game gets its own batch: a batch of one enormous game plus one cheap one is
#: worse than two honest batches, because the reader's attention is already
#: spent. Nothing on the retained slate reaches it -- it is a guard against a
#: game shape nobody has seen yet, not a routine branch.
MAX_SINGLE_GAME_COST = 4.0

#: Encoding safeguard for a context artifact. An order of magnitude below the
#: analysis artifact's budget because this file carries no contract rows.
DEFAULT_MAX_CONTEXT_BYTES = 250_000


@dataclass(frozen=True)
class GameCost:
    """The reasoning cost of one game, and every term that produced it.

    Every term is published, not just the total. A batch boundary an operator
    cannot explain is a batch boundary they will override.
    """

    game_key: str
    base: float
    periods: float
    families: float
    explicit: float
    data_gaps: float
    breadth: float

    @property
    def total(self) -> float:
        return round(
            self.base + self.periods + self.families + self.explicit + self.data_gaps + self.breadth,
            4,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_key": self.game_key,
            "total": self.total,
            "terms": {
                "base_handicap": self.base,
                "extra_periods": self.periods,
                "unusual_families": self.families,
                "explicit_probability_tickers": self.explicit,
                "missing_factual_context": self.data_gaps,
                "market_breadth": self.breadth,
            },
        }


def game_cost(packet: dict[str, Any]) -> GameCost:
    """How much football reasoning this one game asks for.

    *** EVERY TERM IS A THING THE HANDICAPPER ACTUALLY DOES ***
      base                one game, handicapped once. The irreducible unit, and
                          deliberately less than 1.0 so that "cost 6.5" means
                          "about six or seven games" rather than "six".
      extra periods       a first-half and a first-quarter distribution are two
                          more football opinions, not two more lookups.
      unusual families    a family with no closed-form pricing route needs a
                          judgement per family. MEASURED from the contracts'
                          own `requires`, never guessed from the family's name:
                          a family Kalshi ships tomorrow is costed correctly on
                          the first slate it appears in.
      explicit tickers    ...and then a number per ticker, which is the only
                          per-contract cost in the model. Capped, because
                          thirty of them is a slog and three hundred is a
                          different decision (declare the family unpriceable).
      missing context     a factual domain the repository could not fill is
                          research the reader has to do themselves. This is the
                          term that makes the context layer pay for itself: an
                          enriched slate batches into visibly larger batches
                          than an unenriched one, because it is less work.
      market breadth      a mild size term so a 304-contract game is costed
                          above a 28-contract one. Capped hard: the whole point
                          of the redesign is that ladder rungs are NOT the
                          expensive part.

    *** THE WEIGHTS ARE CALIBRATED, NOT GUESSED, AND THEY ARE NOT A MODEL ***
    They were set against the measured shape of the retained 2026-09-19 slate
    so that its cheapest game costs about 0.7 and its most expensive about 2.3.
    They say nothing about any game's betting merit and are not an input to any
    price.
    """
    contracts = list(packet.get("contracts") or [])
    periods: set[str] = set()
    families: set[str] = set()
    explicit_families: set[str] = set()
    explicit_tickers = 0
    for record in contracts:
        semantics = record.get("semantics") or {}
        family = str(record.get("family") or "unknown")
        families.add(family)
        if semantics.get("requires") == "period_distribution":
            periods.add(str(semantics.get("period")))
        else:
            explicit_families.add(family)
            explicit_tickers += 1

    context = packet.get("factual_context") or {}
    coverage = context.get("coverage") or {}
    if coverage:
        gaps = sum(
            1
            for quality in coverage.values()
            if str(quality) in (FieldQuality.MISSING.value, FieldQuality.LOW_COVERAGE.value)
        )
    else:
        # No context block at all means every domain is a gap. Costing it as
        # zero would make an unenriched slate look cheap to handicap, which is
        # exactly backwards -- it is the expensive case.
        gaps = len(CONTEXT_DOMAINS)

    return GameCost(
        game_key=str(packet.get("game_key")),
        base=0.6,
        periods=round(0.06 * max(0, len(periods) - 1), 4),
        families=round(min(0.35, 0.06 * len(explicit_families)), 4),
        explicit=round(min(0.25, 0.006 * explicit_tickers), 4),
        data_gaps=round(min(0.45, 0.04 * gaps), 4),
        breadth=round(min(0.4, len(contracts) / 760.0), 4),
    )


@dataclass(frozen=True)
class HandicapBatch:
    """One unit of handicapping work."""

    name: str
    window: str
    index: int
    packets: list[dict[str, Any]]
    costs: list[GameCost] = field(default_factory=list)

    @property
    def game_keys(self) -> list[str]:
        return [str(p["game_key"]) for p in self.packets]

    @property
    def cost(self) -> float:
        return round(sum(c.total for c in self.costs), 4)

    @property
    def eligible(self) -> int:
        return sum(int(p["counts"]["eligible"]) for p in self.packets)

    @property
    def kickoffs(self) -> list[str]:
        return sorted(str(p["kickoff"]) for p in self.packets if p.get("kickoff"))


def split_into_batches(
    window: str,
    packets: list[dict[str, Any]],
    *,
    budget: float = DEFAULT_BATCH_COST_BUDGET,
    max_bytes: int = DEFAULT_MAX_CONTEXT_BYTES,
) -> list[HandicapBatch]:
    """Greedy, kickoff-ordered packing on cost, with a byte safeguard.

    Kickoff order is preserved so a batch is a contiguous run of the window:
    `early_b1` is the first games to kick, which is also the order an operator
    wants them in when the first kickoff is forty minutes away.
    """
    if not packets:
        return []
    ordered = sorted(
        packets, key=lambda p: (str(p.get("kickoff") or "9999"), str(p["game_key"]))
    )
    costs = {str(p["game_key"]): game_cost(p) for p in ordered}

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_cost = 0.0
    current_bytes = 0

    for packet in ordered:
        cost = costs[str(packet["game_key"])].total
        size = len(
            json.dumps(context_block(packet), sort_keys=False, default=str).encode("utf-8")
        )
        over_cost = current and (current_cost + cost > budget)
        over_bytes = current and (current_bytes + size > max_bytes)
        over_count = len(current) >= MAX_GAMES_PER_BATCH
        # A game that is expensive ON ITS OWN closes the current batch and then
        # takes one alone. Without this a 3.5-cost game would be packed with a
        # 3.0-cost one and hand the reader seven games' worth of work labelled
        # as two.
        alone = current and cost >= MAX_SINGLE_GAME_COST
        if over_cost or over_bytes or over_count or alone:
            groups.append(current)
            current, current_cost, current_bytes = [], 0.0, 0
        current.append(packet)
        current_cost += cost
        current_bytes += size
        if cost >= MAX_SINGLE_GAME_COST and len(current) == 1:
            groups.append(current)
            current, current_cost, current_bytes = [], 0.0, 0
    if current:
        groups.append(current)

    return [
        HandicapBatch(
            name=f"{window}_b{index + 1}",
            window=window,
            index=index + 1,
            packets=group,
            costs=[costs[str(p["game_key"])] for p in group],
        )
        for index, group in enumerate(groups)
    ]


def build_batches(
    slate: dict[str, Any],
    *,
    budget: float = DEFAULT_BATCH_COST_BUDGET,
    max_bytes: int = DEFAULT_MAX_CONTEXT_BYTES,
    window_order: tuple[str, ...] = (),
) -> list[HandicapBatch]:
    by_window: dict[str, list[dict[str, Any]]] = {}
    for packet in slate.get("games") or []:
        by_window.setdefault(str(packet.get("kickoff_window") or "unscheduled"), []).append(packet)

    batches: list[HandicapBatch] = []
    for window in window_order:
        batches.extend(
            split_into_batches(window, by_window.get(window, []), budget=budget, max_bytes=max_bytes)
        )
    for window in sorted(set(by_window) - set(window_order)):
        batches.extend(
            split_into_batches(window, by_window[window], budget=budget, max_bytes=max_bytes)
        )
    return batches


# ------------------------------------------------------ the artifact


def _ladder_summary(packet: dict[str, Any]) -> dict[str, Any]:
    """What markets exist, in one line each, with NO rows.

    This is the whole latency saving stated as data. A family that was 29 rows
    of prices becomes `{"contracts": 29, "line_range": [-24.5, 24.5]}`, because
    the handicapper does not need the rungs to write a distribution -- and the
    evaluator, which does need them, reads them from the shard file on disk.
    """
    out: dict[str, Any] = {}
    for family, block in (packet.get("market_families") or {}).items():
        lines = block.get("lines") or []
        out[family] = {
            "contracts": block.get("contracts"),
            "periods": block.get("periods"),
            "kinds": block.get("kinds"),
            "priced_from": (
                "period_distribution"
                if "period_distribution" in (block.get("requires") or [])
                else "explicit_probability"
            ),
            "line_range": [min(lines), max(lines)] if lines else None,
        }
    return out


def _readable_context(context: dict[str, Any]) -> dict[str, Any]:
    """The factual block, minus the repetition a reader does not need.

    A domain with no values is reported ONCE, by name, in `missing_domains` --
    not as twelve identical empty objects carrying the same "not_attempted".
    The information is the same and the file is a third of the size, which is
    the point of the artifact.
    """
    domains = {
        domain: block
        for domain, block in (context.get("domains") or {}).items()
        if (block or {}).get("values")
    }
    quality = dict(context.get("data_quality") or {})
    # `coverage` is already on the parent; repeating it inside data_quality is
    # two copies of one fact that a future edit can make disagree.
    quality.pop("coverage", None)
    return {
        "context_hash": context.get("context_hash"),
        "collected_at": context.get("collected_at"),
        "coverage": context.get("coverage"),
        "missing_domains": context.get("missing_domains"),
        "data_quality": quality,
        "domains": domains,
        "note": context.get("note"),
    }


def context_block(packet: dict[str, Any]) -> dict[str, Any]:
    """One game's entry in a handicap-batch artifact.

    Key order is deliberate and NOT alphabetised: identity, then the facts,
    then what the facts do not cover, then -- last -- a summary of which
    markets exist. A reader meets the game before the market, which is the
    whole two-stage discipline, enforced by the file's shape rather than by
    an instruction at the top of it.
    """
    metadata = packet.get("game_metadata") or {}
    teams = metadata.get("teams") or {}
    context = packet.get("factual_context") or {}
    return {
        "game_key": packet.get("game_key"),
        "matchup": packet.get("title"),
        "kickoff_utc": packet.get("kickoff"),
        "teams": {
            "home": teams.get("home"),
            "away": teams.get("away"),
            "home_away_confidence": teams.get("home_away_confidence"),
        },
        "classification": {
            "conference": metadata.get("conference"),
            "division": metadata.get("division"),
            "tier": metadata.get("tier"),
            "season_year": metadata.get("season_year"),
            "season_week": metadata.get("season_week"),
        },
        "factual_context": _readable_context(context),
        "markets_available": _ladder_summary(packet),
        "eligible_contracts": (packet.get("counts") or {}).get("eligible"),
        "packet_hash": packet.get("packet_hash"),
    }


HOW_TO_USE_BATCH = [
    "STAGE A -- handicap each game in this file ONCE, from its factual context. Do not price "
    "individual contracts; there are none here to price.",
    "STAGE B -- return one handicap payload per game. One score distribution per period prices "
    "every rung of every ladder in that period deterministically, in the repository.",
    "EVERY distribution needs an `uncertainty` block. Without one NO CONTRACT IN THE GAME CAN BE "
    "CALLED ROBUST -- the repository will price it and will refuse to label the result robust.",
    "`markets_available` is a SUMMARY so you know which periods and families matter. The "
    "contracts, their prices and their fees are on disk and are priced by code, not by you.",
    "A family you cannot defensibly price goes in `declared_unpriceable_families`. A ticker you "
    "can price only by judgement goes in `explicit_probabilities`, with a range beside it.",
    "`data_quality` arrives pre-filled from what was actually fetched. Lower it freely; you "
    "cannot raise the ceiling, because the ceiling is a statement about what was fetched.",
]

DO_NOT_BATCH = [
    "Do not guess a probability to fill a form. An absent probability is a counted, visible "
    "outcome; a plausible one is a bet.",
    "Do not treat a missing factual domain as a benign one. `availability: missing` means the "
    "injury report could not be read, NOT that nobody is hurt.",
    "Do not return a distribution with no uncertainty and expect a robust recommendation.",
    "Do not skip a game because it looks uninteresting. An unhandicapped game's contracts are "
    "counted as unpriceable, which is honest; a skipped one is not.",
]


def batch_document(
    slate: dict[str, Any],
    batch: HandicapBatch,
) -> dict[str, Any]:
    """The compact factual artifact for one handicap batch.

    Carries no prices, no fees, no contract rows, and no fair value. It is the
    input to a football opinion; the market is the evaluator's problem.
    """
    return {
        "schema_version": HANDICAP_BATCH_SCHEMA_VERSION,
        "context_schema_version": CONTEXT_SCHEMA_VERSION,
        "batch": batch.name,
        "kickoff_window": batch.window,
        "slate_date": slate.get("slate_date"),
        "as_of": slate.get("as_of"),
        "how_to_use": HOW_TO_USE_BATCH,
        "do_not": DO_NOT_BATCH,
        "model_projections": "absent",
        "contract_rows_in_this_file": 0,
        "reasoning_load": {
            "games": len(batch.packets),
            "cost": batch.cost,
            "budget": DEFAULT_BATCH_COST_BUDGET,
            "per_game": [c.as_dict() for c in batch.costs],
            "note": (
                "Cost is a deterministic function of the game's periods, market families, "
                "explicit-probability tickers, missing factual domains and market breadth. It is a "
                "measure of HANDICAPPING WORK, not of market quality, and it says nothing about "
                "whether a game is worth betting."
            ),
        },
        "eligible_contracts_priced_from_this_batch": batch.eligible,
        "games": [context_block(packet) for packet in batch.packets],
    }
