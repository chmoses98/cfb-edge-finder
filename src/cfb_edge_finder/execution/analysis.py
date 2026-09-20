"""The ChatGPT analysis artifact: one file per kickoff window, complete.

*** THE WORKFLOW THIS FILE EXISTS FOR ***

    repo  ->  <window>.analysis.json  ->  ChatGPT  ->  every good bet

That is the whole round trip. ChatGPT does NOT write anything back: no
handicap file, no commit, no repo state. It reads one artifact and
answers. Anything that would require a write-back has no place in the
live path.

*** WHAT IT CONTAINS ***
Every mechanically eligible Kalshi contract for every game in the shard,
WITH its prices, its fees, its executable entries and what YES and NO
mean -- grouped by market family, behind that game's factual context so a
reader can handicap first and look at the market second.

*** WHAT IT DOES NOT CONTAIN ***
No model fair value, no model win probability, no projected score, no
projected margin, no rating, no ranking, no edge, no recommendation and
no shortlist. The projection model is not trusted for live betting
decisions and is not on this path; `tests/test_execution_analysis.py`
asserts that mechanically rather than trusting this paragraph.

*** WHY A COLUMN TABLE ***
A Saturday window is ~5,000 contracts. Repeating ~14 key names on every
one of them triples the file for no information. Columns are named once,
rows are values, and every eligible contract still has its own row: this
compresses the ENCODING, never the population. The reconciliation at the
top of the artifact counts the rows that were actually written, so a
compaction bug becomes a failed build rather than a quiet omission.
"""

from __future__ import annotations

from typing import Any

from cfb_edge_finder.execution.semantics import TeamSlot

ANALYSIS_SCHEMA_VERSION = "cfb_execution_analysis/1.0.0"


class AnalysisCoverageError(RuntimeError):
    """The artifact does not contain every eligible contract. It is
    invalid and must not be published."""


# Families are emitted in this order so a reader meets the core markets
# first and the exotica last. A family not listed here is NOT dropped --
# it sorts to the end and is published under its own name, which is the
# whole point of a discovery layer that never hard-codes an allowlist.
FAMILY_ORDER = (
    "game_moneyline",
    "game_spread",
    "game_total",
    "team_total",
    "winning_margin",
    "first_half_moneyline",
    "first_half_spread",
    "first_half_total",
    "first_half_team_total",
    "second_half_moneyline",
    "second_half_spread",
    "second_half_total",
    "second_half_team_total",
    "quarter_moneyline",
    "quarter_spread",
    "quarter_total",
    "quarter_team_total",
    "first_score",
    "touchdown_scorer",
    "both_teams_to_score",
    "overtime",
    "team_stat_prop",
    "game_stat_prop",
    "player_prop",
    "other",
    "unknown",
)

YES_MEANS_TEMPLATES = {
    "moneyline": "<team> wins the <period> outright",
    "tie": "the <period> ends level",
    "spread": "<team> wins the <period> by MORE than <line> points",
    "total": "both teams COMBINED score MORE than <line> points in the <period>",
    "team_total": "<team> alone scores MORE than <line> points in the <period>",
    "margin_band": "<team> wins the <period> by at least <line> and at most <cap>",
    "binary_event": "see each row's yes_means -- these do not share one shape",
}

LADDER_COLUMNS = (
    "t",
    "team",
    "line",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "yes_entry",
    "yes_fee",
    "yes_breakeven",
    "no_entry",
    "no_fee",
    "no_breakeven",
    "quote_age_s",
)

EVENT_COLUMNS = (
    "ticker",
    "yes_means",
    "yes_bid",
    "yes_ask",
    "no_bid",
    "no_ask",
    "yes_entry",
    "yes_fee",
    "yes_breakeven",
    "no_entry",
    "no_fee",
    "no_breakeven",
    "quote_age_s",
)

PRICE_CONVENTIONS = {
    "units": "probability/dollars per $1 contract; 0.62 means 62 cents and implies 62%",
    "buying_yes": "you pay yes_entry (the quoted YES ask) plus yes_fee, and receive $1 if YES settles",
    "buying_no": "you pay no_entry (the quoted NO ask) plus no_fee, and receive $1 if NO settles",
    "no_entry_is_not_one_minus_yes_entry": (
        "the NO ask is quoted independently. 1 - yes_ask is the NO BID -- the price you could SELL "
        "NO at -- and using it as the cost of buying NO understates it by the full spread. The two "
        "disagreed on all 15,444 contracts in the live capture this artifact is built from."
    ),
    "breakeven": (
        "yes_breakeven = yes_entry + yes_fee. Your fair probability for YES must EXCEED it before a "
        "cent of expected value exists. Same for no_breakeven against your fair probability for NO. "
        "Precomputed so a 5,000-row scan does not depend on 5,000 additions."
    ),
    "expected_value": "EV per contract = fair_probability - entry - fee",
    "bid_vs_ask": (
        "bids are included so you can see the spread and judge whether a fill is realistic; you "
        "cannot buy at the bid"
    ),
}


def _round(value: Any, places: int) -> Any:
    return round(float(value), places) if isinstance(value, (int, float)) else None


def _side(record: dict[str, Any], side: str) -> dict[str, Any]:
    block = (record.get("executable") or {}).get(side) or {}
    return {
        "entry": _round(block.get("entry"), 4),
        "fee": _round(block.get("fee"), 6),
        "breakeven": _round(block.get("breakeven_probability"), 4),
    }


def _ladder_row(record: dict[str, Any], prefix_length: int) -> list[Any]:
    semantics = record.get("semantics") or {}
    quote = record.get("quote") or {}
    yes = _side(record, "yes")
    no = _side(record, "no")
    team = semantics.get("team")
    return [
        str(record["ticker"])[prefix_length:],
        # "none" is the slot's name for "not team-scoped", not a team. As
        # a null it lets the column be dropped entirely on a family where
        # no row has a team.
        None if team == TeamSlot.NONE.value else team,
        semantics.get("line"),
        quote.get("yes_bid"),
        quote.get("yes_ask"),
        quote.get("no_bid"),
        quote.get("no_ask"),
        yes["entry"],
        yes["fee"],
        yes["breakeven"],
        no["entry"],
        no["fee"],
        no["breakeven"],
        quote.get("quote_age_seconds"),
    ]


def _event_row(record: dict[str, Any]) -> list[Any]:
    semantics = record.get("semantics") or {}
    quote = record.get("quote") or {}
    yes = _side(record, "yes")
    no = _side(record, "no")
    return [
        str(record["ticker"]),
        semantics.get("yes_means"),
        quote.get("yes_bid"),
        quote.get("yes_ask"),
        quote.get("no_bid"),
        quote.get("no_ask"),
        yes["entry"],
        yes["fee"],
        yes["breakeven"],
        no["entry"],
        no["fee"],
        no["breakeven"],
        quote.get("quote_age_seconds"),
    ]


def _common_prefix(values: list[str]) -> str:
    if not values:
        return ""
    prefix = values[0]
    for value in values[1:]:
        while not value.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def _drop_empty_column(
    columns: list[str], rows: list[list[Any]], name: str
) -> tuple[list[str], list[list[Any]]]:
    if name not in columns:
        return columns, rows
    index = columns.index(name)
    if any(row[index] is not None for row in rows):
        return columns, rows
    return (
        [c for c in columns if c != name],
        [row[:index] + row[index + 1 :] for row in rows],
    )


def _shares_one_shape(records: list[dict[str, Any]]) -> bool:
    """True when every contract in the family means the same thing with a
    different team/line -- a ladder. False for families whose rows each
    say something different (props, doubles, anything unfamiliar), which
    therefore carry their own `yes_means` verbatim."""
    kinds = {str((r.get("semantics") or {}).get("kind")) for r in records}
    return len(kinds) == 1 and kinds.pop() in {
        "moneyline",
        "spread",
        "total",
        "team_total",
        "margin_band",
    }


def family_block(records: list[dict[str, Any]]) -> dict[str, Any]:
    first = records[0]
    semantics = first.get("semantics") or {}
    block: dict[str, Any] = {
        "period": semantics.get("period"),
        "kind": semantics.get("kind"),
        "contracts": len(records),
    }

    if _shares_one_shape(records):
        tickers = [str(r["ticker"]) for r in records]
        prefix = _common_prefix(tickers)
        # yes_means stays HERE, beside its rows, rather than in the
        # glossary at the top of the file. It is the one line that decides
        # whether a row is read correctly, and a reader 4,000 rows deep
        # should not have to hold it in mind. Everything constant --
        # "NO is the negation", "the full ticker is prefix + t" -- IS
        # hoisted, because repeating it 800 times says nothing new.
        block["yes_means"] = YES_MEANS_TEMPLATES.get(str(semantics.get("kind")), "")
        block["ticker_prefix"] = prefix
        block["columns"] = list(LADDER_COLUMNS)
        rows = [_ladder_row(r, len(prefix)) for r in records]
        # A game total has no team and a moneyline has no line. Carrying
        # a column of nulls on 4,800 rows says nothing; dropping it when
        # EVERY row is null loses nothing either.
        for column in ("team", "line"):
            block["columns"], rows = _drop_empty_column(block["columns"], rows, column)
    else:
        block["yes_means"] = "stated per row (this family's contracts do not share one shape)"
        block["columns"] = list(EVENT_COLUMNS)
        rows = [_event_row(r) for r in records]

    # A whole event's contracts usually carry one quote timestamp. When
    # they do, state it once and drop the column; when they do not, the
    # per-row value stays. Lossless either way.
    age_index = block["columns"].index("quote_age_s")
    ages = {row[age_index] for row in rows}
    if len(ages) == 1:
        block["quote_age_s"] = ages.pop()
        block["columns"] = [c for c in block["columns"] if c != "quote_age_s"]
        rows = [row[:age_index] + row[age_index + 1 :] for row in rows]
    block["rows"] = rows
    return block


FACTUAL_DATA_NOT_IN_ARTIFACT = (
    "team records",
    "recent results / form",
    "offensive and defensive statistics",
    "opponent-adjusted ratings",
    "injuries and availability",
    "depth charts",
    "weather",
    "rest and travel",
    "coaching and situational context",
)
"""Named rather than silently absent.

The live path is credential-free and Kalshi-only by construction (see
docs/KALSHI_MARKET_CATALOG.md): it holds no CFBD key, makes no ESPN call
and carries no statistical archive, so none of the above is available to
put here. Saying which facts are missing is what lets a reader supply
them from its own knowledge instead of assuming the artifact already
accounted for them."""


def game_block(packet: dict[str, Any], tz_name: str) -> dict[str, Any]:
    metadata = packet.get("game_metadata") or {}
    teams = metadata.get("teams") or {}
    conference = metadata.get("conference")
    if conference is None:
        # Absent is not the same statement as "non-conference". Kalshi
        # says False explicitly when it is a non-conference game.
        conference_game: bool | None = None
        conference_name = None
    elif conference is False or conference is True:
        conference_game = bool(conference)
        conference_name = None
    else:
        conference_game = True
        conference_name = str(conference)

    records = list(packet.get("contracts") or [])
    by_family: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_family.setdefault(str(record.get("family") or "unknown"), []).append(record)

    def order(name: str) -> tuple[int, str]:
        return (FAMILY_ORDER.index(name), "") if name in FAMILY_ORDER else (len(FAMILY_ORDER), name)

    markets = {
        family: family_block(by_family[family]) for family in sorted(by_family, key=order)
    }

    # `market_scopes_offered` used to sit here; it restated the keys of
    # `markets` in Kalshi's own wording, ~600 bytes per game for nothing
    # a reader could not see one field below.
    return {
        "game_key": packet.get("game_key"),
        "matchup": packet.get("title"),
        "game_context": {
            "home_team": teams.get("home"),
            "away_team": teams.get("away"),
            "home_away_basis": teams.get("home_away_source"),
            "home_away_confidence": teams.get("home_away_confidence"),
            "kickoff_utc": packet.get("kickoff"),
            "kickoff_local": _local_kickoff(packet.get("kickoff"), tz_name),
            "kickoff_window": packet.get("kickoff_window"),
            "division": metadata.get("division"),
            "conference_game": conference_game,
            "conference": conference_name,
            "season_year": metadata.get("season_year"),
            "season_week": metadata.get("season_week"),
            "season_type": metadata.get("season_type"),
            "exchange_tier": metadata.get("tier"),
            "catalog_complete_for_this_game": metadata.get("native_game_markets_complete"),
        },
        "eligible_contracts": len(records),
        "markets": markets,
    }


def _local_kickoff(kickoff: Any, tz_name: str) -> str | None:
    if not kickoff:
        return None
    from datetime import datetime

    try:
        moment = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
    except ValueError:
        return None
    try:
        from zoneinfo import ZoneInfo

        return moment.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d %H:%M %Z")
    except Exception:  # noqa: BLE001 - tzdata unavailable
        return moment.strftime("%Y-%m-%d %H:%M UTC")


HOW_TO_USE = [
    "STAGE A -- for each game, read ONLY `game_context` and handicap it independently from your own "
    "knowledge: who wins, by how much, how many points, and how confident you are. Do this before "
    "looking at that game's `markets`.",
    "STAGE B -- then read `markets` and compare your own fair probabilities against every listed "
    "contract's breakeven. Both sides of every contract are buyable; check YES and NO.",
    "Return EVERY wager you believe has positive expected value and sufficient confidence. There is "
    "no cap and no target number. If that is 0 bets, return 0. If it is 100, return all 100.",
    "Every mechanically eligible contract in this window is in this file. Nothing was filtered for "
    "attractiveness, liquidity, popularity or expected edge -- only objective mechanical exclusions "
    "were applied, and they are counted in `reconciliation`.",
    "This file contains NO model projection, fair value, win probability, projected score, rating or "
    "recommendation. There is nothing here to defer to. The handicap is entirely yours.",
    "Nothing is written back anywhere. Answer in the conversation; do not attempt to save, commit or "
    "return a file.",
    "For each bet you return, state: game, ticker, side (YES/NO), what that side means, the "
    "executable entry price, its breakeven, your fair probability, the fee-adjusted edge, your "
    "confidence, and the strongest case against the bet.",
]

DO_NOT = [
    "do not treat the quoted price as a reference answer while forming the handicap",
    "do not stop at moneyline/spread/total -- alternate rungs, team totals, halves, quarters and "
    "props are all present and all evaluable",
    "do not cap, rank-and-truncate, or return only the 'best' handful",
    "do not bet a contract whose meaning you are unsure of -- say so instead",
]


def analysis_document(
    slate: dict[str, Any],
    shard_name: str,
    window: str,
    packets: list[dict[str, Any]],
    *,
    tz_name: str = "America/New_York",
    discovered: int | None = None,
    excluded: int | None = None,
    exclusions_by_status: dict[str, int] | None = None,
    window_part: int = 1,
    window_parts: int = 1,
) -> dict[str, Any]:
    """One kickoff window, complete, ready to upload.

    Raises `AnalysisCoverageError` if the rows written do not equal the
    eligible contracts in the shard. The artifact is invalid in that case
    and must not be published."""
    games = [game_block(packet, tz_name) for packet in packets]

    eligible = sum(int(p["counts"]["eligible"]) for p in packets)
    in_artifact = sum(
        len(block["rows"]) for game in games for block in game["markets"].values()
    )
    if in_artifact != eligible:
        raise AnalysisCoverageError(
            f"{shard_name}: {in_artifact} contracts written but {eligible} are eligible "
            f"({eligible - in_artifact} unaccounted). The artifact is invalid."
        )

    discovered_total = (
        discovered
        if discovered is not None
        else sum(int(p["counts"]["discovered"]) for p in packets)
    )
    excluded_total = (
        excluded if excluded is not None else sum(int(p["counts"]["excluded"]) for p in packets)
    )

    source = slate.get("source") or {}
    config = slate.get("config") or {}
    quote_ages = [
        row[-1]
        for game in games
        for block in game["markets"].values()
        for row in block["rows"]
        if isinstance(row[-1], (int, float))
    ]

    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "artifact": "cfb_chatgpt_analysis_slate",
        "shard": shard_name,
        "kickoff_window": window,
        "window_part": window_part,
        "window_parts": window_parts,
        "part_note": (
            f"part {window_part} of {window_parts} for the {window} window. Each part is a "
            f"self-contained set of whole games -- no game's contracts are split across parts -- "
            f"so it can be handicapped on its own."
        ),
        "slate_date": slate.get("slate_date"),
        "as_of": slate.get("as_of"),
        "how_to_use": list(HOW_TO_USE),
        "do_not": list(DO_NOT),
        "model_projections": "absent",
        "model_projections_note": (
            "This repository's projection model was retired from the live path in September 2026 "
            "(docs/MODEL_RETIREMENT_2026.md) and is deliberately not trusted for betting decisions. "
            "No fair value, win probability, projected score, margin, total, rating or edge appears "
            "anywhere in this file."
        ),
        "reconciliation": {
            "games_discovered": discovered_games(slate),
            "games_included": len(games),
            "contracts_discovered": discovered_total,
            "mechanical_exclusions": excluded_total,
            "eligible_contracts": eligible,
            "contracts_in_analysis_artifact": in_artifact,
            "unaccounted_contracts": eligible - in_artifact,
            "exclusions_by_status": dict(sorted((exclusions_by_status or {}).items())),
            "invariant": "eligible_contracts == contracts_in_analysis_artifact, unaccounted == 0",
        },
        "freshness": {
            "captured_at": source.get("captured_at"),
            "capture_age_seconds_at_build": source.get("capture_age_seconds"),
            "max_capture_age_minutes": config.get("max_capture_age_minutes"),
            "freshest_quote_age_seconds": min(quote_ages) if quote_ages else None,
            "oldest_quote_age_seconds": max(quote_ages) if quote_ages else None,
            "quote_age_note": (
                "quote_age_s is time since Kalshi last CHANGED that contract's record. A pregame "
                "contract nobody has traded for days carries a large value and a perfectly live "
                "book; what actually goes stale is the capture, which is one timestamp for the "
                "whole file and is bounded by max_capture_age_minutes."
            ),
        },
        "fee_model": slate.get("fee_disclosure"),
        "price_conventions": dict(PRICE_CONVENTIONS),
        "reading_this_file": READING_THIS_FILE,
        "factual_data_not_in_artifact": list(FACTUAL_DATA_NOT_IN_ARTIFACT),
        "games": games,
    }


READING_THIS_FILE = {
    "games[].game_context": "the facts. Handicap from these before reading markets.",
    "games[].markets": "every eligible contract for that game, grouped by family.",
    "markets[family].yes_means": "what buying YES claims, for every row in that family.",
    "markets[family].no_means": "always the exact negation of that family's yes_means.",
    "markets[family].ticker_prefix": (
        "present on ladder families: a row's full ticker is ticker_prefix + its `t` value. "
        "Families without a prefix carry the full ticker in the row."
    ),
    "markets[family].columns": "names the positions in every row of that family.",
    "markets[family].quote_age_s": (
        "present when every contract in the family shares one quote age; otherwise quote_age_s is a "
        "column in the rows."
    ),
    "exchange_tier": (
        "Kalshi's own listing tier (A is a marquee matchup, D a minor one). An exchange attention "
        "signal, not a rating of either team."
    ),
    "factual_data_not_in_artifact": (
        "the facts this file does NOT carry, listed at the top level. Supply them yourself; do not "
        "assume the artifact accounted for them."
    ),
}


def discovered_games(slate: dict[str, Any]) -> int:
    reconciliation = slate.get("reconciliation") or {}
    return int(reconciliation.get("games_in_scope") or 0)
