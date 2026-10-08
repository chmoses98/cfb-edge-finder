"""Prospective CONTROL tracking: population, settlement rows, H1/H2 summaries and the research-status gate. PURE.

*** FROZEN CLAIMS ARE NEVER REGENERATED ***
The population is the V2 ledger's FINAL_PREGAME rows exactly as written. A game's tier is copied from the LAST
FINAL_PREGAME row recorded before kickoff. Nothing here builds, re-derives or backfills a claim.

*** APPEND-ONLY, IDEMPOTENT ***
`settle` returns rows only for games that are not yet settled; a written row is never rewritten. Re-running
on the same inputs writes nothing. Rows are deterministic functions of the ledger, the captures, the frozen
orientation matcher and the game log.

*** WHAT IS NEVER PRODUCED ***
No probability, no fair price, no stake, no recommendation. Economics are one research contract per game,
exactly as in the frozen market study.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from cfb_edge_finder.control_market.economics import entry_fee, one_contract
from cfb_edge_finder.control_market.football import outcome_for
from cfb_edge_finder.control_market.quotes import contract_for, orient_markets, parse_utc
from cfb_edge_finder.control_market.report import _roi_stat, bootstrap, wilson
from cfb_edge_finder.control_prospective import (
    AUTOMATIC_STATUSES,
    CAPTURE_OK,
    CAPTURE_PENDING,
    CAPTURE_SYSTEM_FAILURE,
    DISAGREEMENT_BELOW_CENTS,
    H1_ID,
    H2_ID,
    MODERATE_TIERS,
    ORIENTATION_FAILURE,
    PROSPECTIVE_VERSION,
    PROTOCOL_SHA256,
    REVIEW_CHECKPOINTS,
    REVIEW_REQUIRED,
    SETTLEMENT_SCHEMA,
    STRONG_TIERS,
    VALUE_WATCH,
)
from cfb_edge_finder.control_prospective.capture import side_status, to_quote
from cfb_edge_finder.scripting.gamelog import TeamGame

METHODOLOGY = "cfb-script-engine/2.0.0"
KICKOFF_ON_OR_AFTER = "2026-10-08T12:00:00Z"
COVERAGE_HEALTHY = 0.90
FOOTBALL_COHERENT_FLOOR = 0.6658
GATE_REVIEW_N = 30
GATE_PRIMARY_N = 50
EDGE_MIN_PER_SIDE = 10
EDGE_MIN_WEEKS = 3

NameKeys = Callable[[str], set[str]]


# --------------------------------------------------------------------------- population


def population(ledger_rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """game_key -> the LAST V2 FINAL_PREGAME row recorded strictly before kickoff (kickoff >= registration)."""
    out: dict[str, dict[str, Any]] = {}
    for row in ledger_rows:
        if row.get("kind") != "FINAL_PREGAME" or row.get("methodology_version") != METHODOLOGY:
            continue
        kickoff = row.get("kickoff_utc")
        if not kickoff or parse_utc(kickoff) < parse_utc(KICKOFF_ON_OR_AFTER):
            continue
        if parse_utc(row["recorded_at"]) >= parse_utc(kickoff):
            continue  # the writer refuses these; a reader refuses them too
        current = out.get(row["game_key"])
        if current is None or parse_utc(row["recorded_at"]) > parse_utc(current["recorded_at"]):
            out[row["game_key"]] = row
    return out


def missing_final_pregame(ledger_rows: Iterable[dict[str, Any]], now: str) -> list[dict[str, Any]]:
    """Games the V2 ledger published but never froze as FINAL_PREGAME, once kickoff has passed (a pipeline miss)."""
    rows = list(ledger_rows)
    have = set(population(rows))
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("methodology_version") != METHODOLOGY:
            continue
        kickoff = row.get("kickoff_utc")
        if not kickoff or parse_utc(kickoff) < parse_utc(KICKOFF_ON_OR_AFTER) or parse_utc(kickoff) > parse_utc(now):
            continue
        if row["game_key"] not in have:
            seen[row["game_key"]] = {
                "game_key": row["game_key"],
                "kickoff_utc": kickoff,
                "event_id": row.get("event_id"),
            }
    return sorted(seen.values(), key=lambda r: (r["kickoff_utc"], r["game_key"]))


def control_of(row: dict[str, Any]) -> dict[str, Any] | None:
    """The CONTROL claim of a ledger row, copied: {tier, side, strength, team_id, team}."""
    c = (row.get("claims") or {}).get("control")
    if not c:
        return None
    team = (row.get("teams") or {}).get(c["side"]) or {}
    return {
        "tier": c["tier"],
        "side": c["side"],
        "strength": c["strength"],
        "team_id": str(team.get("team_id")),
        "team": team.get("name"),
    }


# --------------------------------------------------------------------------- per game


def evaluate_game(
    row: dict[str, Any],
    *,
    quotes: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    schedule: list[dict[str, Any]],
    keys: NameKeys,
    team_rows: list[TeamGame],
    now: str,
) -> dict[str, Any] | None:
    """A CONTROL population game's capture, entry, settlement and economics as of `now` (None without CONTROL)."""
    control = control_of(row)
    if control is None:
        return None
    kickoff = row["kickoff_utc"]
    game_quotes = [q for q in quotes if q.get("game_key") == row["game_key"]]
    game_attempts = [a for a in attempts if a.get("game_key") == row["game_key"]]
    orientation = orient_markets([to_quote(q) for q in game_quotes], schedule, keys) if game_quotes else {}
    contract = contract_for(control["team_id"], str(row["event_id"]), orientation) if orientation else None
    ticker = contract["primary"][0] if contract and contract["primary"] else None
    health = side_status(
        kickoff=kickoff,
        now=now,
        market_ticker=ticker,
        orientation_status=contract["status"] if contract else None,
        quotes=game_quotes,
        attempts=game_attempts,
    )
    price = (health.get("entry") or {}).get("price")
    outcome = (
        outcome_for(team_rows, str(row["event_id"]), control["team_id"])
        if parse_utc(now) > parse_utc(kickoff)
        else {"status": "NOT_STARTED"}
    )
    pays = outcome.get("control_won") if outcome.get("status") == "SETTLED" else None
    economics = one_contract(price, entry_fee(price), pays)
    hypotheses = []
    if control["tier"] in MODERATE_TIERS:
        hypotheses.append(H1_ID)
    if control["tier"] in STRONG_TIERS and price is not None and round(price * 100) < DISAGREEMENT_BELOW_CENTS:
        hypotheses.append(H2_ID)
    return {
        "schema": SETTLEMENT_SCHEMA,
        "prospective_version": PROSPECTIVE_VERSION,
        "protocol_sha256": PROTOCOL_SHA256,
        "game_key": row["game_key"],
        "event_id": str(row["event_id"]),
        "kickoff_utc": kickoff,
        "teams": row.get("teams"),
        "control": control,
        "final_pregame": {
            "recorded_at": row["recorded_at"],
            "claims_artifact_hash": row.get("claims_artifact_hash"),
            "data_quality": row.get("data_quality"),
        },
        "capture": health,
        "settlement": outcome,
        "economics": economics,
        "hypotheses": hypotheses,
    }


def settle(
    ledger_rows: Iterable[dict[str, Any]],
    already: Iterable[dict[str, Any]],
    *,
    quotes: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    schedule: list[dict[str, Any]],
    keys: NameKeys,
    team_rows: list[TeamGame],
    now: str,
) -> list[dict[str, Any]]:
    """New settlement rows: population CONTROL games whose result is final and that have no row yet."""
    done = {r["game_key"] for r in already}
    out = []
    for game_key, row in sorted(population(ledger_rows).items()):
        if game_key in done or parse_utc(now) <= parse_utc(row["kickoff_utc"]):
            continue
        ev = evaluate_game(
            row, quotes=quotes, attempts=attempts, schedule=schedule, keys=keys, team_rows=team_rows, now=now
        )
        if ev is None or ev["settlement"].get("status") != "SETTLED":
            continue
        out.append({**ev, "settled_at": now})
    return out


# --------------------------------------------------------------------------- summaries


def _econ(rows: list[dict[str, Any]]) -> dict[str, Any]:
    priced = [r for r in rows if r["economics"].get("available")]
    n = len(priced)
    if not n:
        return {
            "n": 0,
            "wins": 0,
            "losses": 0,
            "roi": None,
            "roi_ci95": None,
            "break_even": None,
            "win_rate": None,
            "mean_entry": None,
            "fee_adjusted_pnl": None,
        }
    pnl = [r["economics"]["fee_adjusted_pnl"] for r in priced]
    outlay = [r["economics"]["outlay"] for r in priced]
    wins = sum(1 for r in priced if r["economics"]["settlement_value"] == 1.0)
    roi = sum(pnl) / sum(outlay)
    boot = bootstrap({"pnl": pnl, "outlay": outlay}, _roi_stat, n)
    return {
        "n": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": round(wins / n, 4),
        "mean_entry": round(sum(r["economics"]["entry_price"] for r in priced) / n, 4),
        "break_even": round(sum(outlay) / n, 4),
        "fee_adjusted_pnl": round(sum(pnl), 4),
        "roi": round(roi, 4),
        "roi_ci95": boot.get("ci95"),
    }


def checkpoint(n: int) -> dict[str, Any]:
    reached = [k for k in sorted(REVIEW_CHECKPOINTS) if n >= k]
    upcoming = [k for k in sorted(REVIEW_CHECKPOINTS) if n < k]
    return {
        "reached": REVIEW_CHECKPOINTS[reached[-1]] if reached else None,
        "reached_at_n": reached[-1] if reached else None,
        "next_at_n": upcoming[0] if upcoming else None,
        "next": REVIEW_CHECKPOINTS[upcoming[0]] if upcoming else None,
    }


def football(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [r for r in rows if r["settlement"].get("status") == "SETTLED"]
    n = len(settled)
    wins = sum(1 for r in settled if r["settlement"]["control_won"])
    return {
        "n": n,
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else None,
        "wilson95": wilson(wins, n) if n else None,
    }


def coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(r["capture"]["status"] for r in rows)
    ours = counts[CAPTURE_OK] + counts[ORIENTATION_FAILURE] + counts[CAPTURE_SYSTEM_FAILURE]
    rate = counts[CAPTURE_OK] / ours if ours else None
    return {
        "statuses": dict(sorted(counts.items())),
        "pipeline_coverage": None if rate is None else round(rate, 4),
        "healthy": None if rate is None else rate >= COVERAGE_HEALTHY,
    }


def h1_summary(settled: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in settled if H1_ID in r["hypotheses"]]
    econ = _econ(rows)
    return {
        "id": H1_ID,
        **econ,
        "checkpoint": checkpoint(econ["n"]),
        "slices": {t: _econ([r for r in rows if r["control"]["tier"] == t]) for t in MODERATE_TIERS},
        "football": football(rows),
        "coverage": coverage(rows),
        "weeks": len({r["kickoff_utc"][:10] for r in rows if r["economics"].get("available")}),
    }


def h2_summary(settled: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in settled if H2_ID in r["hypotheses"]]
    econ = _econ(rows)
    diff = None if econ["n"] == 0 else round(econ["win_rate"] - econ["break_even"], 4)
    return {
        "id": H2_ID,
        **econ,
        "realized_minus_break_even": diff,
        "underperforming_so_far": None if diff is None else diff < 0,
        "checkpoint": checkpoint(econ["n"]),
    }


def strong_summary(settled: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in settled if r["control"]["tier"] in STRONG_TIERS]
    return {**_econ(rows), "football": football(rows)}


def moderate_status(h1: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """The frozen gate. Code emits VALUE_WATCH or REVIEW_REQUIRED only; a reviewed decision overrides."""
    reviewed = [d for d in decisions if d.get("signal") == "moderate_control" and d.get("status")]
    if reviewed:
        last = reviewed[-1]
        return {
            "status": last["status"],
            "source": "REVIEWED_DECISION",
            "decision": last,
            "review_reason": None,
            "edge_criteria_met": None,
        }
    n, roi = h1["n"], h1["roi"]
    cov_ok = h1["coverage"]["healthy"] is True
    fb = h1["football"]["wilson95"]
    coherent = fb is not None and fb[1] >= FOOTBALL_COHERENT_FLOOR
    early = n >= GATE_REVIEW_N and roi is not None and roi > 0 and cov_ok and coherent
    primary = n >= GATE_PRIMARY_N
    status = REVIEW_REQUIRED if (early or primary) else VALUE_WATCH
    assert status in AUTOMATIC_STATUSES
    ci = h1["roi_ci95"]
    edge = (
        primary
        and roi is not None
        and roi > 0
        and ci is not None
        and ci[0] > 0
        and all(h1["slices"][t]["n"] >= EDGE_MIN_PER_SIDE for t in MODERATE_TIERS)
        and h1["weeks"] >= EDGE_MIN_WEEKS
    )
    reason = None
    if primary:
        reason = "PRIMARY_REVIEW: prospective priced n reached 50"
    elif early:
        reason = "n >= 30 with ROI > 0, healthy capture coverage and a coherent football relationship"
    return {
        "status": status,
        "source": "AUTOMATIC_GATE",
        "decision": None,
        "review_reason": reason,
        "edge_criteria_met": bool(edge) if status == REVIEW_REQUIRED else None,
        "football_coherent": coherent if fb is not None else None,
        "capture_coverage_healthy": h1["coverage"]["healthy"],
    }


def summarize(settled: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    h1 = h1_summary(settled)
    return {
        "settled_rows": len(settled),
        "H1": h1,
        "H2": h2_summary(settled),
        "strong": strong_summary(settled),
        "moderate_status": moderate_status(h1, decisions),
    }


def population_counts(ledger_rows: list[dict[str, Any]], now: str) -> dict[str, Any]:
    pop = population(ledger_rows)
    tiers: Counter[str] = Counter()
    upcoming: Counter[str] = Counter()
    for row in pop.values():
        c = control_of(row)
        tier = c["tier"] if c else "NO_CONTROL"
        tiers[tier] += 1
        if parse_utc(row["kickoff_utc"]) > parse_utc(now):
            upcoming[tier] += 1
    by_strength: dict[str, int] = defaultdict(int)
    for t, k in tiers.items():
        by_strength["MODERATE" if t in MODERATE_TIERS else "STRONG" if t in STRONG_TIERS else t] += k
    return {
        "final_pregame_games": len(pop),
        "by_tier": dict(sorted(tiers.items())),
        "by_strength": dict(sorted(by_strength.items())),
        "awaiting_kickoff": dict(sorted(upcoming.items())),
        "missing_final_pregame": missing_final_pregame(ledger_rows, now),
    }


__all__ = [
    "CAPTURE_PENDING",
    "population",
    "missing_final_pregame",
    "evaluate_game",
    "settle",
    "summarize",
    "population_counts",
    "checkpoint",
]
