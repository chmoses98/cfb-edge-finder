"""Wave 2A (CFB): the frozen Wave-2 streams replayed on genuinely captured 2026 Kalshi spread ladders. PURE.

Every rule is the frozen Wave-2 function, called unchanged (`wave2.checkpoint`, `implied_margin`, `natural_rung`,
`contract_for`, `entry_row`, `settlement_row`, `closing_context`, `stream_summary`). This module only adapts
historical captures into the attempt/quote shape those functions read, keeps the outcome-blind MEMBERSHIP stage
apart from the REVEAL stage, and adds descriptive robustness tables.

Two capture sources (protocol §3):

* RESEARCH_CORPUS -- research-data observation rows. They carry both executable asks, no bids. The frozen center
  needs the YES mid, so the YES bid is the book identity 1 - NO ask (and the NO bid 1 - YES ask), verified on the
  catalog before use and labelled on every quote. Entry prices are always the captured ask of the bought side.
* CATALOG_SNAPSHOT -- main's market catalog history, stamped with the build's `captured_at`; inside the window only
  if [captured_at - elapsed, captured_at + elapsed] is wholly inside it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

import numpy as np

from cfb_edge_finder.control_market.quotes import parse_utc, whole_cent
from cfb_edge_finder.control_prospective import WINDOW_CLOSE_MIN, WINDOW_OPEN_MIN
from cfb_edge_finder.control_prospective.capture import (
    ATTEMPT_NO_MARKET,
    ATTEMPT_OK,
    in_primary_window,
    iso,
    minutes_to,
)
from cfb_edge_finder.signal_discovery import wave2 as W

VERSION = "cfb-signal-discovery-wave2a/1.0.0"
SCHEMA = "cfb_signal_lab_wave2a_replay/1.0.0"
FREEZE_UTC = "2026-10-08T22:26:33Z"  # the Wave-2 pre-registration commit c441abb5
EVIDENCE_HISTORY = "RETROSPECTIVE_DISCOVERY_CORPUS"
EVIDENCE_2026 = "RETROSPECTIVE_2026_REPLAY"
EVIDENCE_PROSPECTIVE = "PROSPECTIVE_WAVE2"
PRE_FREEZE = "PRE_FREEZE"
POST_FREEZE_PRE_ACTIVATION = "POST_FREEZE_PRE_ACTIVATION"

SOURCE_RESEARCH = "RESEARCH_CORPUS"
SOURCE_CATALOG = "CATALOG_SNAPSHOT"
BID_IDENTITY = "BOOK_IDENTITY_FROM_OPPOSITE_ASK"
BID_CAPTURED = "CAPTURED"
ATTEMPT_PARTIAL = "PARTIAL_LADDER_UNPARSED"

# replay exclusion codes (protocol §5); NOT_ELIGIBLE is a rule outcome, not an exclusion
BEFORE_CAPTURE_HISTORY = "BEFORE_CAPTURE_HISTORY"
NO_MARKET = "NO_MARKET"
NO_PRIMARY_WINDOW_CAPTURE = "NO_PRIMARY_WINDOW_CAPTURE"
NO_EXECUTABLE_QUOTE = "NO_EXECUTABLE_QUOTE"
ORIENTATION_UNRESOLVED = "ORIENTATION_UNRESOLVED"
IDENTITY_FAILURE = "IDENTITY_FAILURE"
FEATURE_HISTORY_UNAVAILABLE = "FEATURE_HISTORY_UNAVAILABLE"
FEE_UNAVAILABLE = "FEE_UNAVAILABLE"
SETTLEMENT_UNAVAILABLE = "SETTLEMENT_UNAVAILABLE"
RULE_NOT_RECONSTRUCTABLE = "RULE_NOT_RECONSTRUCTABLE"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
EXCLUSIONS = (
    BEFORE_CAPTURE_HISTORY,
    NO_MARKET,
    NO_PRIMARY_WINDOW_CAPTURE,
    NO_EXECUTABLE_QUOTE,
    ORIENTATION_UNRESOLVED,
    IDENTITY_FAILURE,
    FEATURE_HISTORY_UNAVAILABLE,
    FEE_UNAVAILABLE,
    SETTLEMENT_UNAVAILABLE,
    RULE_NOT_RECONSTRUCTABLE,
)

#: The fields of a game record that membership may read. Scores are never among them.
OUTCOME_FIELDS = frozenset({"points_for", "points_against", "score", "control_margin", "settlement", "winner"})


class ReplayIntegrityError(RuntimeError):
    """A replay input would break the protocol (post-kickoff quote, outcome in membership, prospective path)."""


def freeze_phase(kickoff: str) -> str:
    return PRE_FREEZE if parse_utc(kickoff) < parse_utc(FREEZE_UTC) else POST_FREEZE_PRE_ACTIVATION


def in_replay_population(kickoff: str) -> bool:
    """2026 replay games kicked off strictly before the Wave-2 activation (the prospective population start)."""
    return parse_utc(kickoff) < parse_utc(W.ACTIVATION_UTC)


def assert_not_prospective_store(path: str) -> None:
    """Replay output may never land in the Wave-2 prospective store."""
    text = str(path).replace("\\", "/")
    if "/wave2/" in f"/{text}" or text.startswith("wave2/"):
        raise ReplayIntegrityError(f"replay output may not be written under the prospective Wave-2 store: {text}")


# --------------------------------------------------------------------------- book identity


def book_identity_holds(markets: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Count two-sided catalog markets where yes_bid == 1 - no_ask and no_bid == 1 - yes_ask (to the cent)."""
    out = Counter()
    for m in markets:
        yb, ya, nb, na = m.get("yes_bid"), m.get("yes_ask"), m.get("no_bid"), m.get("no_ask")
        if None in (yb, ya, nb, na):
            out["incomplete"] += 1
            continue
        ok = abs(yb - (1 - na)) < 1e-9 and abs(nb - (1 - ya)) < 1e-9
        out["holds" if ok else "violated"] += 1
    return dict(out)


# --------------------------------------------------------------------------- capture adapters


def _attempt(
    *, attempt_id: str, at: str, kickoff: str, game_key: str, source: str, outcome: str, in_window: bool, n: int
) -> dict[str, Any]:
    if parse_utc(at) >= parse_utc(kickoff):
        raise ReplayIntegrityError(f"{game_key}: capture at {at} is not before kickoff {kickoff}")
    return {
        "attempt_id": attempt_id,
        "attempted_at": at,
        "game_key": game_key,
        "kickoff_utc": kickoff,
        "minutes_before": round(minutes_to(kickoff, at), 2),
        "in_primary_window": in_window,
        "outcome": outcome,
        "markets": n,
        "source": source,
    }


def research_attempts(
    rows: Iterable[dict[str, Any]], *, game_key: str, kickoff: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Research-corpus spread rows of one Kalshi event -> one attempt per run (all rows share `captured_at`).

    `rows` are the research `observation` objects plus `run_id`. Captures at/after kickoff are dropped."""
    event = f"{W.SPREAD_SERIES}-{game_key}"
    by_run: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("kalshi_event_ticker") == event:
            by_run[str(r["run_id"])].append(r)
    attempts, quotes = [], []
    for run_id in sorted(by_run):
        group = by_run[run_id]
        stamps = {r["captured_at"] for r in group}
        if len(stamps) != 1:
            raise ReplayIntegrityError(f"{event} run {run_id}: rows carry {len(stamps)} capture times")
        at = stamps.pop()
        if parse_utc(at) >= parse_utc(kickoff):
            continue
        aid = f"RC:{run_id}:{event}"
        partial = any(r.get("threshold") is None for r in group)
        attempts.append(
            _attempt(
                attempt_id=aid,
                at=at,
                kickoff=kickoff,
                game_key=game_key,
                source=SOURCE_RESEARCH,
                outcome=ATTEMPT_PARTIAL if partial else ATTEMPT_OK,
                in_window=in_primary_window(kickoff, at),
                n=len(group),
            )
        )
        if partial:
            continue
        for r in sorted(group, key=lambda r: r["kalshi_market_ticker"]):
            ya, na = r.get("executable_yes_price"), r.get("executable_no_price")
            floor = float(r["threshold"])
            quotes.append(
                {
                    "attempt_id": aid,
                    "game_key": game_key,
                    "captured_at": at,
                    "market_ticker": r["kalshi_market_ticker"],
                    "floor_strike": floor,
                    "team_code": W.spread_code(r["kalshi_market_ticker"], floor),
                    "status": r.get("market_status"),
                    "yes_ask": ya,
                    "no_ask": na,
                    "yes_bid": None if na is None else round(1 - na, 4),
                    "no_bid": None if ya is None else round(1 - ya, 4),
                    "bid_provenance": BID_IDENTITY,
                    "source": SOURCE_RESEARCH,
                }
            )
    return attempts, quotes


def catalog_raw(market: dict[str, Any]) -> dict[str, Any]:
    """A catalog market record restated for the shared normaliser (the catalog's bare keys are DOLLARS)."""
    raw = {k: v for k, v in market.items() if k not in ("yes_bid", "yes_ask", "no_bid", "no_ask")}
    raw["ticker"] = market.get("market_ticker")
    for k in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
        raw[f"{k}_dollars"] = None if market.get(k) is None else f"{float(market[k]):.4f}"
    return raw


def catalog_window(kickoff: str, captured_at: str, elapsed_seconds: float) -> bool:
    """Conservative: the whole possible fetch interval must lie inside PRIMARY_60_180."""
    lo = parse_utc(captured_at) - timedelta(seconds=elapsed_seconds)
    hi = parse_utc(captured_at) + timedelta(seconds=elapsed_seconds)
    return in_primary_window(kickoff, iso(lo)) and in_primary_window(kickoff, iso(hi))


def catalog_attempt(
    markets: list[dict[str, Any]] | None,
    *,
    game_key: str,
    kickoff: str,
    captured_at: str,
    elapsed_seconds: float,
    commit: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """One catalog snapshot of one game -> (attempt, rungs). None when the snapshot could reach kickoff."""
    if parse_utc(captured_at) + timedelta(seconds=elapsed_seconds) >= parse_utc(kickoff):
        return None, []
    rows = W.spread_markets_for([catalog_raw(m) for m in (markets or [])], game_key)
    aid = f"CAT:{commit[:12]}:{game_key}"
    attempt = _attempt(
        attempt_id=aid,
        at=captured_at,
        kickoff=kickoff,
        game_key=game_key,
        source=SOURCE_CATALOG,
        outcome=ATTEMPT_OK if rows else ATTEMPT_NO_MARKET,
        in_window=catalog_window(kickoff, captured_at, elapsed_seconds),
        n=len(rows),
    )
    quotes = [
        {
            **r,
            "attempt_id": aid,
            "game_key": game_key,
            "captured_at": captured_at,
            "bid_provenance": BID_CAPTURED,
            "source": SOURCE_CATALOG,
        }
        for r in rows
    ]
    return attempt, quotes


# --------------------------------------------------------------------------- membership (outcome-blind)


def strip_outcomes(record: dict[str, Any]) -> dict[str, Any]:
    """A copy without any outcome field (membership must be computable without them)."""
    return {k: v for k, v in record.items() if k not in OUTCOME_FIELDS}


def kalshi_keys_for(orientation: dict[str, dict[str, Any]], game_id: str) -> list[str]:
    """Kalshi game keys whose winner markets the frozen matcher attributes to this ESPN event."""
    return sorted({t.split("-")[1] for t, o in orientation.items() if o.get("espn_event_id") == game_id})


def replay_exclusion(stream: str, status: str, reason: str | None, *, kickoff: str, first_capture: str) -> str | None:
    """Frozen status/reason -> the protocol's replay code (None for an eligible row)."""
    if status == W.ELIGIBLE:
        return None
    if status == W.EXCLUDED_PROTOCOL and reason == "IMPLIED_MARGIN_NOT_BELOW_3":
        return NOT_ELIGIBLE
    if status == W.EXCLUDED_PROTOCOL and reason == "ABS_Z_BELOW_1":
        return NOT_ELIGIBLE
    if status == W.EXCLUDED_PROTOCOL and str(reason).startswith("FEATURE_"):
        return FEATURE_HISTORY_UNAVAILABLE
    if status == W.IDENTITY_FAILURE:
        return IDENTITY_FAILURE
    if status == W.MARKET_NOT_OFFERED:
        return NO_MARKET
    if status == W.ORIENTATION_FAILURE:
        return ORIENTATION_UNRESOLVED
    if status == W.ENTRY_UNAVAILABLE:
        return NO_EXECUTABLE_QUOTE
    if status == W.FEE_UNAVAILABLE:
        return FEE_UNAVAILABLE
    if status == W.SYSTEM_FAILURE and reason == "NO_SPREAD_ATTEMPT_REACHED_KALSHI_IN_WINDOW":
        if parse_utc(kickoff) - timedelta(minutes=WINDOW_CLOSE_MIN) < parse_utc(first_capture):
            return BEFORE_CAPTURE_HISTORY
        return NO_PRIMARY_WINDOW_CAPTURE
    if status == NO_MARKET:
        return NO_MARKET
    return RULE_NOT_RECONSTRUCTABLE


def replay_entry(
    obs: dict[str, Any],
    *,
    attempts: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    codes: dict[str, Any],
    first_capture: str,
    code_sha: str | None,
) -> dict[str, Any]:
    """The frozen PRIMARY_60_180 entry for one PENDING observation, evaluated as if the window had just closed."""
    kickoff = obs["kickoff_utc"]
    for a in attempts:
        if parse_utc(a["attempted_at"]) >= parse_utc(kickoff):
            raise ReplayIntegrityError(f"{obs['game_key']}: post-kickoff attempt reached the checkpoint")
    if not attempts and codes["status"] == "NO_GAME_WINNER_ORIENTATION":
        cp = {"status": NO_MARKET, "reason": "NO_KALSHI_EVENT_MATCHED"}
    else:
        cp = W.checkpoint(
            kickoff=kickoff,
            now=kickoff,
            attempts=attempts,
            quotes=quotes,
            codes_status=codes,
            team_id=obs["side_team"]["team_id"],
        )
    if cp["status"] == W.PENDING:  # impossible at now == kickoff; kept as a guard
        raise ReplayIntegrityError(f"{obs['game_key']}: checkpoint still pending at kickoff")
    if cp["status"] == NO_MARKET:
        entry = {
            **{k: obs[k] for k in ("sport", "signal_id", "version", "game_key", "game_id", "kickoff_utc", "teams")},
            **{k: obs.get(k) for k in ("side", "side_team")},
            "record": W.ENTRY,
            "row_id": f"{obs['signal_id']}:{W.ENTRY}:{obs['game_key']}",
            "observation_id": obs["row_id"],
            "checkpoint": "PRIMARY_60_180",
            "status": W.MARKET_NOT_OFFERED,
            "reason": cp["reason"],
            "candidates_sha256": W.CANDIDATES_SHA256,
            "code_sha": code_sha,
        }
    else:
        entry = W.entry_row(obs=obs, cp=cp, now=kickoff, code_sha=code_sha)
    if cp["status"] == W.ELIGIBLE:
        used = next(a for a in attempts if a["attempt_id"] == cp["attempt_id"])
        entry["capture_source"] = used["source"]
        entry["bid_provenance"] = sorted(
            {q.get("bid_provenance") for q in quotes if q.get("attempt_id") == cp["attempt_id"]}
        )
        entry["ladder_rungs"] = sum(1 for q in quotes if q.get("attempt_id") == cp["attempt_id"])
    entry["window_attempts_by_source"] = dict(
        Counter(a["source"] for a in attempts if a.get("in_primary_window") and a["outcome"] == ATTEMPT_OK)
    )
    entry["replay_exclusion"] = replay_exclusion(
        obs["signal_id"], entry["status"], entry.get("reason"), kickoff=kickoff, first_capture=first_capture
    )
    entry["evidence_label"] = EVIDENCE_2026
    entry["freeze_phase"] = freeze_phase(kickoff)
    return entry


def observation_row(obs: dict[str, Any], *, week: int | None, first_capture: str) -> dict[str, Any]:
    """A frozen observation with its replay labels."""
    return {
        **obs,
        "week": week,
        "evidence_label": EVIDENCE_2026,
        "freeze_phase": freeze_phase(obs["kickoff_utc"]),
        "replay_exclusion": (
            None
            if obs["status"] == W.PENDING
            else replay_exclusion(
                obs["signal_id"],
                obs["status"],
                obs.get("reason"),
                kickoff=obs["kickoff_utc"],
                first_capture=first_capture,
            )
        ),
    }


# --------------------------------------------------------------------------- reveal


def reveal(
    entry: dict[str, Any], outcome: dict[str, Any], closing: dict[str, Any] | None, *, code_sha: str | None
) -> dict[str, Any]:
    """Settlement of one ELIGIBLE entry (frozen `settlement_row`), or SETTLEMENT_UNAVAILABLE."""
    if outcome.get("status") != "SETTLED":
        return {
            **{k: entry.get(k) for k in ("signal_id", "game_key", "game_id", "kickoff_utc", "side", "side_team")},
            "record": W.SETTLEMENT,
            "status": W.SETTLEMENT_PENDING,
            "reason": outcome.get("reason"),
            "replay_exclusion": SETTLEMENT_UNAVAILABLE,
            "evidence_label": EVIDENCE_2026,
        }
    row = W.settlement_row(entry=entry, outcome=outcome, closing=closing, now=entry["kickoff_utc"], code_sha=code_sha)
    row["evidence_label"] = EVIDENCE_2026
    row["freeze_phase"] = entry.get("freeze_phase")
    row["capture_source"] = entry.get("capture_source")
    row["contract"] = entry.get("contract")
    row["entry_captured_at"] = entry.get("captured_at")
    row["entry_minutes_before"] = entry.get("minutes_before")
    return row


# --------------------------------------------------------------------------- descriptive robustness


def _mean(xs: list[float]) -> float | None:
    return float(np.mean(xs)) if xs else None


def _ats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    w = sum(1 for r in rows if r["residual"] > 0)
    lo = sum(1 for r in rows if r["residual"] < 0)
    return {"wins": w, "losses": lo, "pushes": len(rows) - w - lo, "rate": w / (w + lo) if w + lo else None}


def leave_one_out(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Mean residual with each value of `key` removed in turn (min / max / which)."""
    groups = sorted({r[key] for r in rows if r.get(key) is not None}, key=str)
    if len(groups) < 2:
        return None
    res = {}
    for g in groups:
        rest = [r["residual"] for r in rows if r.get(key) != g]
        res[str(g)] = _mean(rest)
    lo = min(res, key=lambda k: res[k])
    hi = max(res, key=lambda k: res[k])
    return {"groups": len(groups), "min": res[lo], "min_without": lo, "max": res[hi], "max_without": hi}


def top_k_removed(rows: list[dict[str, Any]], key: str, k: int = 5) -> dict[str, Any] | None:
    """Mean residual after removing the k groups with the largest total residual (the best contributors)."""
    tot: dict[str, float] = defaultdict(float)
    for r in rows:
        tot[str(r.get(key))] += r["residual"]
    if len(tot) <= k:
        return None
    top = sorted(tot, key=lambda g: (-tot[g], g))[:k]
    rest = [r for r in rows if str(r.get(key)) not in top]
    return {"removed": top, "n": len(rest), "mean_residual": _mean([r["residual"] for r in rest]), **_ats(rest)}


def breakdown(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    out = {}
    for g in sorted({str(r.get(key)) for r in rows}):
        sub = [r for r in rows if str(r.get(key)) == g]
        pnl = [r["economics"]["fee_adjusted_pnl"] for r in sub if (r.get("economics") or {}).get("available")]
        out[g] = {
            "n": len(sub),
            "mean_residual": _mean([r["residual"] for r in sub]),
            **_ats(sub),
            "fee_adjusted_pnl": round(sum(pnl), 4) if pnl else None,
        }
    return out


def robustness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Week / team / conference concentration of a settled stream (descriptive; never a new rule)."""
    if not rows:
        return {"n": 0, "display": W.NO_SETTLED_SAMPLE}
    return {
        "n": len(rows),
        "by_week": breakdown(rows, "week"),
        "by_side": breakdown(rows, "side"),
        "by_favourite": breakdown(rows, "market_role"),
        "by_capture_source": breakdown(rows, "capture_source"),
        "by_freeze_phase": breakdown(rows, "freeze_phase"),
        "leave_one_week_out": leave_one_out(rows, "week"),
        "leave_one_team_out": leave_one_out(rows, "side_team_id"),
        "leave_one_conference_out": leave_one_out(rows, "side_conference"),
        "top5_teams_removed": top_k_removed(rows, "side_team_id"),
        "top5_conferences_removed": top_k_removed(rows, "side_conference"),
        "max_team_share": max(Counter(r.get("side_team_id") for r in rows).values()) / len(rows),
    }


def market_role(implied_margin: float) -> str:
    """Side favoured (implied margin > 0), underdog (< 0) or pick at the entry center."""
    return "FAVOURITE" if implied_margin > 0 else "UNDERDOG" if implied_margin < 0 else "PICK"


def clv_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    moves = [r["clv"]["closing_move_toward_side"] for r in rows if r["clv"].get("closing_move_toward_side") is not None]
    contract = [r["clv"]["contract_clv"] for r in rows if r["clv"].get("contract_clv") is not None]
    return {
        "with_close": len(moves),
        "missing_close": len(rows) - len(moves),
        "mean_center_move_toward_side": _mean(moves),
        "share_moved_toward_side": (sum(1 for m in moves if m > 0) / len(moves)) if moves else None,
        "contract_clv_n": len(contract),
        "mean_contract_clv": _mean(contract),
    }


def classify(summary: dict[str, Any]) -> str:
    """Protocol §7 interpretation rule (2026 replay only)."""
    n = summary.get("n", 0)
    if n < 15:
        return "INSUFFICIENT_REPLAY_DATA"
    mean = summary["residual"]["mean"]
    ci = summary["residual"]["ci95_bootstrap"]
    rate = summary["ats_like"]["rate"]
    if mean > 0 and ci is not None and ci[0] > 0 and rate is not None and rate >= 0.524:
        return "STRONGLY_SUPPORTIVE"
    if mean > 0 and rate is not None and rate >= 0.5:
        return "SUPPORTIVE"
    if mean <= 0 and rate is not None and rate < 0.5:
        return "UNSUPPORTIVE"
    return "MIXED"


def first_capture_time(attempts: Iterable[dict[str, Any]]) -> str | None:
    times = [a["attempted_at"] for a in attempts]
    return min(times) if times else None


def window_bounds_minutes() -> tuple[float, float]:
    return float(WINDOW_CLOSE_MIN), float(WINDOW_OPEN_MIN)


def assert_fee_known(contract: dict[str, Any]) -> None:
    if contract.get("status") == W.ELIGIBLE and (
        contract.get("fee") is None or whole_cent(contract.get("ask")) is None
    ):
        raise ReplayIntegrityError("an ELIGIBLE contract must carry an executable ask and a known fee")
