"""Football Signal Discovery Lab, Wave 2 (CFB): prospective confirmation. RESEARCH ONLY. PURE.

Two frozen streams (docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE2_PROTOCOL.md,
data/scripting/validation/signal_discovery_wave2/candidates.json):

* CFB-PROS-001 RUSHING_EDGE_SPREAD -- the exact Wave-1 CFB-DSC-001 follow rule (|z| >= 1 on the opponent-adjusted
  rush-defense quality difference), judged against the Kalshi spread-ladder `market_implied_margin` at
  PRIMARY_60_180.
* CFB-PROS-002 STRONG_CONTROL_MARKET_SKEPTICISM -- production V2 STRONG CONTROL whose CONTROL-side
  `market_implied_margin` is below +3.

Everything here is a pure function of captured rows. Row building is append-only: an OBSERVATION is frozen before
the window closes, an ENTRY once the window has closed, a SETTLEMENT once the final score exists, and none of them is
ever rewritten. Nothing here publishes a probability, a fair price, a stake or a recommendation; no verdict other than
the frozen research states can be produced, and EDGE_CONFIRMED is never one of them.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from cfb_edge_finder.control_market.quotes import parse_utc, whole_cent
from cfb_edge_finder.control_prospective import WINDOW_CLOSE_MIN
from cfb_edge_finder.control_prospective.capture import (
    ATTEMPT_FAILED,
    ATTEMPT_NO_MARKET,
    ATTEMPT_OK,
    minutes_to,
    normalize_market,
)
from cfb_edge_finder.kalshi.fee_schedule import (
    KALSHI_FEE_SCHEDULE_2026_07_07_TAKER,
    calculate_fee_cents,
    get_taker_multiplier,
)

CANDIDATES_PATH = Path("data/scripting/validation/signal_discovery_wave2/candidates.json")
#: Pinned in the pre-registration commit (c441abb5). Never edit the candidates file.
CANDIDATES_SHA256 = "bf19c972bd230ff505c50ccd3a631dbc027d41b4900ad76c97248a4cfc6e1ce5"
VERSION = "cfb-signal-discovery-wave2/1.0.0"
SPORT = "CFB"
SPREAD_SERIES = "KXNCAAFSPREAD"
ACTIVATION_UTC = "2026-10-10T00:00:00Z"

PROS_001 = "CFB-PROS-001"
PROS_002 = "CFB-PROS-002"
STREAMS = (PROS_001, PROS_002)

# CFB-PROS-001: exact Wave-1 CFB-DSC-001 standardisation and follow rule (evaluation_report.json)
DSC001_FEATURE = "defdiff.rush_success_rate"
DSC001_MEAN = 0.13804024626610417
DSC001_SD = 1.4218539928759784
Z_THRESHOLD = 1.0
# CFB-PROS-002
SKEPTIC_BELOW_POINTS = 3.0
STRONG_TIERS = ("HOME_CONTROL_STRONG", "AWAY_CONTROL_STRONG")

# row statuses (frozen list)
PENDING = "PENDING"
ELIGIBLE = "ELIGIBLE"
ENTRY_UNAVAILABLE = "ENTRY_UNAVAILABLE"
MARKET_NOT_OFFERED = "MARKET_NOT_OFFERED"
ORIENTATION_FAILURE = "ORIENTATION_FAILURE"
IDENTITY_FAILURE = "IDENTITY_FAILURE"
FEE_UNAVAILABLE = "FEE_UNAVAILABLE"
SETTLEMENT_PENDING = "SETTLEMENT_PENDING"
SETTLED = "SETTLED"
EXCLUDED_PROTOCOL = "EXCLUDED_PROTOCOL"
SYSTEM_FAILURE = "SYSTEM_FAILURE"
ROW_STATUSES = (
    PENDING,
    ELIGIBLE,
    ENTRY_UNAVAILABLE,
    MARKET_NOT_OFFERED,
    ORIENTATION_FAILURE,
    IDENTITY_FAILURE,
    FEE_UNAVAILABLE,
    SETTLEMENT_PENDING,
    SETTLED,
    EXCLUDED_PROTOCOL,
    SYSTEM_FAILURE,
)

# verdict states (frozen list); EDGE_CONFIRMED is deliberately absent
PROSPECTIVE_TRACKING = "PROSPECTIVE_TRACKING"
EARLY_READ = "EARLY_READ"
INTERIM = "INTERIM"
PRIMARY_REVIEW = "PRIMARY_REVIEW"
REJECTED = "REJECTED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
VERDICT_STATES = (PROSPECTIVE_TRACKING, EARLY_READ, INTERIM, PRIMARY_REVIEW, REJECTED, REVIEW_REQUIRED)
READS: dict[str, dict[int, str]] = {
    PROS_001: {50: EARLY_READ, 100: INTERIM, 200: INTERIM, 400: PRIMARY_REVIEW},
    PROS_002: {50: INTERIM, 100: PRIMARY_REVIEW},
}
PROS_002_FALSIFY_BELOW = 0.52
NO_SETTLED_SAMPLE = "NO SETTLED SAMPLE"

OBSERVATION = "OBSERVATION"
ENTRY = "ENTRY"
SETTLEMENT = "SETTLEMENT"
LEDGER_SCHEMA = "cfb_signal_lab_wave2_ledger/1.0.0"
STATUS_SCHEMA = "cfb_signal_lab_wave2_status/1.0.0"
N_BOOT = 4000
SEED = 20261010


class CandidatesMismatch(RuntimeError):
    """The Wave-2 candidate file is not the pre-registered one."""


def canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def load_candidates(root: Path) -> dict[str, Any]:
    """The registered candidate file, or CandidatesMismatch. Constants here must restate it exactly."""
    doc = json.loads((root / CANDIDATES_PATH).read_text(encoding="utf-8"))
    if doc.get("status") != "PRE-REGISTERED":
        raise CandidatesMismatch("candidates are not marked PRE-REGISTERED")
    digest = sha256(doc)
    if digest != CANDIDATES_SHA256:
        raise CandidatesMismatch(f"candidates hash {digest} != registered {CANDIDATES_SHA256}")
    s1 = doc["streams"][PROS_001]["standardisation"]
    if (s1["mean"], s1["sd"]) != (DSC001_MEAN, DSC001_SD):
        raise CandidatesMismatch("PROS-001 standardisation differs from the registration")
    if doc["streams"][PROS_002]["frozen_threshold_points"] != SKEPTIC_BELOW_POINTS:
        raise CandidatesMismatch("PROS-002 threshold differs from the registration")
    if doc["population"]["kickoff_on_or_after_utc"] != ACTIVATION_UTC:
        raise CandidatesMismatch("activation differs from the registration")
    for sid, reads in READS.items():
        if {int(k): v.split(" ")[0] for k, v in doc["streams"][sid]["reads"].items()} != reads:
            raise CandidatesMismatch(f"{sid} reads differ from the registration")
    if tuple(doc["row_statuses"]) != ROW_STATUSES or tuple(doc["verdict_states"]) != VERDICT_STATES:
        raise CandidatesMismatch("status vocabularies differ from the registration")
    return doc


# --------------------------------------------------------------------------- spread ladder capture


def spread_code(ticker: str | None, floor_strike: float | None) -> str | None:
    """`KXNCAAFSPREAD-26OCT09FSULOU-LOU4` with floor 3.5 -> `LOU` (suffix minus the integer floor+0.5)."""
    if not ticker or floor_strike is None:
        return None
    suffix = ticker.rsplit("-", 1)[-1].upper()
    n = floor_strike + 0.5
    if abs(n - round(n)) > 1e-9:
        return None
    digits = str(int(round(n)))
    if not suffix.endswith(digits) or len(suffix) == len(digits):
        return None
    return suffix[: -len(digits)]


def normalize_spread(raw: dict[str, Any]) -> dict[str, Any]:
    """A Kalshi KXNCAAFSPREAD market -> capture fields (prices in dollars, side-specific asks kept separately)."""
    m = normalize_market(raw)
    try:
        floor = float(raw["floor_strike"]) if raw.get("floor_strike") is not None else None
    except (TypeError, ValueError):
        floor = None
    m["floor_strike"] = floor
    m["strike_type"] = raw.get("strike_type")
    m["series"] = SPREAD_SERIES
    m["team_code"] = spread_code(m["market_ticker"], floor)
    return m


def spread_markets_for(raw_markets: Iterable[dict[str, Any]], game_key: str) -> list[dict[str, Any]]:
    """The game's spread rungs from one event read (any other ticker is dropped)."""
    prefix = f"{SPREAD_SERIES}-{game_key}-"
    out = [
        normalize_spread(m)
        for m in raw_markets
        if str(m.get("ticker") or m.get("market_ticker") or "").startswith(prefix)
    ]
    return sorted(out, key=lambda m: m["market_ticker"])


# --------------------------------------------------------------------------- orientation and the market center


def team_codes(orientation: dict[str, dict[str, Any]], event_id: str) -> dict[str, Any]:
    """Winner-market code -> ESPN team id for the game's event, from the frozen matcher's verdicts."""
    mine = {t: o for t, o in orientation.items() if o.get("espn_event_id") == event_id}
    if not mine:
        return {"status": "NO_GAME_WINNER_ORIENTATION", "codes": {}}
    if any(o["status"] == "ORIENTATION_CONFLICT" for o in mine.values()):
        return {"status": "ORIENTATION_CONFLICT", "codes": {}}
    codes = {t.rsplit("-", 1)[-1].upper(): o["team_id"] for t, o in mine.items() if o["status"] == "RESOLVED"}
    if len(codes) != 2 or len(set(codes.values())) != 2:
        return {"status": "ORIENTATION_UNRESOLVED", "codes": codes}
    return {"status": "RESOLVED", "codes": codes}


def _valid_mid(row: dict[str, Any]) -> float | None:
    status = str(row.get("status") or "").lower()
    if status not in ("active", "open"):
        return None
    yb, ya = row.get("yes_bid"), row.get("yes_ask")
    if yb is None or ya is None or not (0 < yb <= ya < 1):
        return None
    return (ya + yb) / 2


def ladder_points(rows: list[dict[str, Any]], team_id: str, codes: dict[str, str]) -> list[dict[str, Any]]:
    """Side-perspective ladder points (Wave-1 arithmetic). Each point remembers the rung it came from."""
    pts = []
    for r in rows:
        code = r.get("team_code")
        if code not in codes or r.get("floor_strike") is None:
            continue
        mid = _valid_mid(r)
        if mid is None:
            continue
        own = codes[code] == team_id
        s = float(r["floor_strike"])
        pts.append(
            {
                "s": s if own else -s,
                "p": mid if own else 1 - mid,
                "ticker": r["market_ticker"],
                "own_rung": own,
                "floor_strike": s,
                "yes_bid": r.get("yes_bid"),
                "yes_ask": r.get("yes_ask"),
                "no_bid": r.get("no_bid"),
                "no_ask": r.get("no_ask"),
            }
        )
    pts.sort(key=lambda x: (x["s"], x["p"]))
    return pts


def implied_margin(points: list[dict[str, Any]]) -> tuple[float | None, list[str] | None]:
    """Wave-1: interpolated strike where P(margin > s) crosses 0.5 at the FIRST pair p1 >= 0.5 >= p2, p1 != p2."""
    for a, b in zip(points, points[1:], strict=False):
        if a["p"] >= 0.5 >= b["p"] and a["p"] != b["p"]:
            value = a["s"] + (a["p"] - 0.5) / (a["p"] - b["p"]) * (b["s"] - a["s"])
            return value, [a["ticker"], b["ticker"]]
    return None, None


def natural_rung(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Smallest |p - 0.5|; ties -> smaller |s|, then ticker. Never optimised."""
    if not points:
        return None
    return min(points, key=lambda x: (abs(x["p"] - 0.5), abs(x["s"]), x["ticker"]))


def spread_fee(price: float | None) -> float | None:
    cents = whole_cent(price)
    if cents is None:
        return None
    multiplier, _ = get_taker_multiplier(SPREAD_SERIES)
    return calculate_fee_cents(cents, 1, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER, multiplier) / 100.0


def contract_for(point: dict[str, Any] | None) -> dict[str, Any]:
    """The natural rung as a research contract: own rung -> YES at yes_ask, opponent rung -> NO at no_ask."""
    if point is None:
        return {"status": ENTRY_UNAVAILABLE, "reason": "NO_VALID_RUNG"}
    side = "yes" if point["own_rung"] else "no"
    ask = point["yes_ask"] if side == "yes" else point["no_ask"]  # never 1 - the other side
    base = {
        "ticker": point["ticker"],
        "contract_side": side,
        "floor_strike": point["floor_strike"],
        "side_line": point["s"],
        "side_probability_mid": round(point["p"], 6),
        "ask": ask,
    }
    if whole_cent(ask) is None:
        return {**base, "status": ENTRY_UNAVAILABLE, "reason": "QUOTE_NOT_EXECUTABLE", "fee": None}
    fee = spread_fee(ask)
    if fee is None:
        return {**base, "status": FEE_UNAVAILABLE, "reason": "FEE_UNAVAILABLE", "fee": None}
    return {**base, "status": ELIGIBLE, "reason": None, "fee": fee}


def contract_pays(contract: dict[str, Any], side_margin: float) -> bool:
    """YES on '<side> wins by over s' pays iff margin > s; NO on '<opp> wins by over s' pays iff opp margin <= s."""
    s = contract["floor_strike"]
    if contract["contract_side"] == "yes":
        return side_margin > s
    return -side_margin <= s


# --------------------------------------------------------------------------- checkpoint


def checkpoint(
    *,
    kickoff: str,
    now: str,
    attempts: list[dict[str, Any]],
    quotes: list[dict[str, Any]],
    codes_status: dict[str, Any],
    team_id: str,
) -> dict[str, Any]:
    """PRIMARY_60_180: the LAST in-window attempt whose oriented ladder yields a market_implied_margin.

    Returns {"status", "reason", ...}; status ELIGIBLE carries the snapshot, PENDING while the window is open."""
    window = [a for a in attempts if a.get("in_primary_window")]
    reached = [a for a in window if a["outcome"] in (ATTEMPT_OK, ATTEMPT_NO_MARKET)]
    health = {
        "attempts_in_window": len(window),
        "attempts_failed": sum(1 for a in window if a["outcome"] == ATTEMPT_FAILED),
        "attempts_no_market": sum(1 for a in window if a["outcome"] == ATTEMPT_NO_MARKET),
        "orientation": codes_status["status"],
    }
    if codes_status["status"] == "RESOLVED":
        for a in sorted(window, key=lambda a: a["attempted_at"], reverse=True):
            if a["outcome"] != ATTEMPT_OK:
                continue
            rows = [q for q in quotes if q.get("attempt_id") == a["attempt_id"]]
            pts = ladder_points(rows, team_id, codes_status["codes"])
            value, bracket = implied_margin(pts)
            if value is None:
                continue
            return {
                "status": ELIGIBLE,
                "reason": None,
                "captured_at": a["attempted_at"],
                "minutes_before": a["minutes_before"],
                "attempt_id": a["attempt_id"],
                "market_implied_margin": round(value, 4),
                "bracket": bracket,
                "rungs_used": [{"s": p["s"], "p": round(p["p"], 6), "ticker": p["ticker"]} for p in pts],
                "points": pts,
                **health,
            }
    if parse_utc(now) <= parse_utc(kickoff) and minutes_to(kickoff, now) >= WINDOW_CLOSE_MIN:
        return {"status": PENDING, "reason": "WINDOW_OPEN_OR_NOT_YET_OPEN", **health}
    if not reached:
        return {"status": SYSTEM_FAILURE, "reason": "NO_SPREAD_ATTEMPT_REACHED_KALSHI_IN_WINDOW", **health}
    if all(a["outcome"] == ATTEMPT_NO_MARKET for a in reached):
        return {"status": MARKET_NOT_OFFERED, "reason": "NO_SPREAD_LADDER_IN_WINDOW", **health}
    if codes_status["status"] != "RESOLVED":
        return {"status": ORIENTATION_FAILURE, "reason": codes_status["status"], **health}
    return {"status": ENTRY_UNAVAILABLE, "reason": "NO_LADDER_CROSSING_IN_WINDOW", **health}


def closing_context(
    *, attempts: list[dict[str, Any]], quotes: list[dict[str, Any]], codes: dict[str, str], team_id: str, kickoff: str
) -> dict[str, Any] | None:
    """The last capture with 0 < minutes_before < 60 yielding an implied margin (descriptive only)."""
    late = [
        a
        for a in attempts
        if a["outcome"] == ATTEMPT_OK
        and 0 < a["minutes_before"] < WINDOW_CLOSE_MIN
        and parse_utc(a["attempted_at"]) < parse_utc(kickoff)
    ]
    for a in sorted(late, key=lambda a: a["attempted_at"], reverse=True):
        rows = [q for q in quotes if q.get("attempt_id") == a["attempt_id"]]
        pts = ladder_points(rows, team_id, codes)
        value, _ = implied_margin(pts)
        if value is not None:
            return {"captured_at": a["attempted_at"], "market_implied_margin": round(value, 4), "points": pts}
    return None


# --------------------------------------------------------------------------- rows


def _ids(*parts: str) -> str:
    return ":".join(parts)


def observation_001(
    *, game: dict[str, Any], feature: dict[str, Any] | None, reason: str | None, now: str, code_sha: str | None
) -> dict[str, Any]:
    """PROS-001 frozen pregame row (one per population game)."""
    base = _common(PROS_001, OBSERVATION, game, now, code_sha)
    if feature is None:
        return {
            **base,
            "status": IDENTITY_FAILURE if reason == "NO_ESPN_IDENTITY" else SYSTEM_FAILURE,
            "reason": reason,
        }
    x = feature.get(DSC001_FEATURE)
    elig = feature.get("eligibility")
    snap = {
        "feature": DSC001_FEATURE,
        "value": x,
        "home_def_q.rush_success_rate": feature.get("home_def_q.rush_success_rate"),
        "away_def_q.rush_success_rate": feature.get("away_def_q.rush_success_rate"),
        "net.rushing": feature.get("net.rushing"),
        "football_data_cutoff": feature.get("football_data_cutoff"),
        "prior_games_home": feature.get("prior_games_home"),
        "prior_games_away": feature.get("prior_games_away"),
        "history_rows": feature.get("history_rows"),
        "freshness": feature.get("freshness"),
        "feature_eligibility": elig,
    }
    snap["feature_row_sha256"] = sha256({k: v for k, v in feature.items() if k not in ("freshness",)})
    if elig is not None or x is None:
        return {**base, "status": EXCLUDED_PROTOCOL, "reason": f"FEATURE_{elig or 'UNDEFINED'}", "signal": snap}
    z = (x - DSC001_MEAN) / DSC001_SD
    snap["z"] = round(z, 6)
    if abs(z) < Z_THRESHOLD:
        return {**base, "status": EXCLUDED_PROTOCOL, "reason": "ABS_Z_BELOW_1", "signal": snap}
    side = "home" if z > 0 else "away"
    return {**base, "status": PENDING, "reason": None, "signal": snap, "side": side, "side_team": game["teams"][side]}


def observation_002(
    *, game: dict[str, Any], ledger_row: dict[str, Any], now: str, code_sha: str | None
) -> dict[str, Any]:
    """PROS-002 frozen pregame row: STRONG CONTROL copied verbatim from the first FINAL_PREGAME row seen."""
    base = _common(PROS_002, OBSERVATION, game, now, code_sha)
    c = (ledger_row.get("claims") or {}).get("control") or {}
    control = {
        "tier": c.get("tier"),
        "side": c.get("side"),
        "strength": c.get("strength"),
        "final_pregame_recorded_at": ledger_row.get("recorded_at"),
        "claims_artifact_hash": ledger_row.get("claims_artifact_hash"),
        "methodology_version": ledger_row.get("methodology_version"),
    }
    side = c.get("side")
    if side not in ("home", "away"):
        return {**base, "status": EXCLUDED_PROTOCOL, "reason": "CONTROL_SIDE_UNKNOWN", "signal": control}
    return {
        **base,
        "status": PENDING,
        "reason": None,
        "signal": control,
        "side": side,
        "side_team": game["teams"][side],
    }


def missing_observation(
    stream: str, game: dict[str, Any], now: str, code_sha: str | None, reason: str
) -> dict[str, Any]:
    return {**_common(stream, OBSERVATION, game, now, code_sha), "status": SYSTEM_FAILURE, "reason": reason}


def entry_row(*, obs: dict[str, Any], cp: dict[str, Any], now: str, code_sha: str | None) -> dict[str, Any]:
    """The frozen checkpoint for one observed stream row (written once the window has closed)."""
    base = {
        **{
            k: obs[k]
            for k in (
                "sport",
                "signal_id",
                "version",
                "game_key",
                "game_id",
                "kickoff_utc",
                "teams",
                "side",
                "side_team",
            )
        },
        "record": ENTRY,
        "schema": LEDGER_SCHEMA,
        "candidates_sha256": CANDIDATES_SHA256,
        "code_sha": code_sha,
        "generated_at": now,
        "observation_id": obs["row_id"],
        "row_id": _ids(obs["signal_id"], ENTRY, obs["game_key"]),
        "checkpoint": "PRIMARY_60_180",
        "health": {
            k: cp.get(k) for k in ("attempts_in_window", "attempts_failed", "attempts_no_market", "orientation")
        },
    }
    if cp["status"] != ELIGIBLE:
        return {**base, "status": cp["status"], "reason": cp["reason"]}
    point = natural_rung(cp["points"])
    contract = contract_for(point)
    entry = {
        "captured_at": cp["captured_at"],
        "minutes_before": cp["minutes_before"],
        "market_implied_margin": cp["market_implied_margin"],
        "bracket": cp["bracket"],
        "rungs_used": cp["rungs_used"],
        "contract": contract,
        "ticker": contract.get("ticker"),
        "rung": contract.get("floor_strike"),
        "contract_side": contract.get("contract_side"),
        "ask": contract.get("ask"),
        "fee": contract.get("fee"),
    }
    if obs["signal_id"] == PROS_002 and not cp["market_implied_margin"] < SKEPTIC_BELOW_POINTS:
        return {**base, **entry, "status": EXCLUDED_PROTOCOL, "reason": "IMPLIED_MARGIN_NOT_BELOW_3"}
    return {**base, **entry, "status": ELIGIBLE, "reason": None}


def settlement_row(
    *, entry: dict[str, Any], outcome: dict[str, Any], closing: dict[str, Any] | None, now: str, code_sha: str | None
) -> dict[str, Any]:
    """Actual margin, residual, ATS-like result and the natural-rung contract result (side perspective)."""
    margin = float(outcome["control_margin"])  # outcome_for(...) of the SIDE team: side points - opponent points
    implied = entry["market_implied_margin"]
    residual = margin - implied
    contract = entry["contract"]
    econ: dict[str, Any]
    if contract.get("status") != ELIGIBLE:
        econ = {"available": False, "reason": contract.get("reason") or contract.get("status")}
    else:
        pays = contract_pays(contract, margin)
        outlay = contract["ask"] + contract["fee"]
        econ = {
            "available": True,
            "contract_side": contract["contract_side"],
            "ticker": contract["ticker"],
            "entry_price": contract["ask"],
            "fee": contract["fee"],
            "outlay": round(outlay, 6),
            "pays": pays,
            "fee_adjusted_pnl": round((1.0 if pays else 0.0) - outlay, 6),
        }
    clv: dict[str, Any] = {"closing_implied_margin": None, "closing_move_toward_side": None, "contract_clv": None}
    if closing is not None:
        clv["closing_implied_margin"] = closing["market_implied_margin"]
        clv["closing_captured_at"] = closing["captured_at"]
        clv["closing_move_toward_side"] = round(closing["market_implied_margin"] - implied, 4)
        if contract.get("ticker"):
            same = [p for p in closing["points"] if p["ticker"] == contract["ticker"]]
            if same:
                close_ask = same[0]["yes_ask"] if contract["contract_side"] == "yes" else same[0]["no_ask"]
                if whole_cent(close_ask) is not None and contract.get("ask") is not None:
                    clv["contract_clv"] = round(close_ask - contract["ask"], 4)
    return {
        **{
            k: entry[k]
            for k in (
                "sport",
                "signal_id",
                "version",
                "game_key",
                "game_id",
                "kickoff_utc",
                "teams",
                "side",
                "side_team",
            )
        },
        "record": SETTLEMENT,
        "schema": LEDGER_SCHEMA,
        "candidates_sha256": CANDIDATES_SHA256,
        "code_sha": code_sha,
        "generated_at": now,
        "entry_id": entry["row_id"],
        "row_id": _ids(entry["signal_id"], SETTLEMENT, entry["game_key"]),
        "status": SETTLED,
        "reason": None,
        "market_implied_margin": implied,
        "side_points": outcome["control_points"],
        "opponent_points": outcome["opponent_points"],
        "actual_margin": margin,
        "residual": round(residual, 4),
        "outright_win": margin > 0,
        "ats_like": "WIN" if residual > 0 else "LOSS" if residual < 0 else "PUSH",
        "economics": econ,
        "clv": clv,
        "settlement_source": outcome.get("source"),
    }


def _common(stream: str, record: str, game: dict[str, Any], now: str, code_sha: str | None) -> dict[str, Any]:
    return {
        "schema": LEDGER_SCHEMA,
        "sport": SPORT,
        "signal_id": stream,
        "version": VERSION,
        "record": record,
        "row_id": _ids(stream, record, game["game_key"]),
        "game_key": game["game_key"],
        "game_id": game.get("game_id"),
        "kickoff_utc": game["kickoff_utc"],
        "teams": game.get("teams"),
        "candidates_sha256": CANDIDATES_SHA256,
        "code_sha": code_sha,
        "generated_at": now,
    }


# --------------------------------------------------------------------------- statistics and verdicts


def _boot_mean_ci(values: list[float]) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = np.random.default_rng(SEED)
    arr = np.asarray(values, float)
    idx = rng.integers(0, len(arr), size=(N_BOOT, len(arr)))
    means = arr[idx].mean(axis=1)
    return [float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))]


def _boot_ratio_ci(num: list[float], den: list[float]) -> list[float] | None:
    if len(num) < 2:
        return None
    rng = np.random.default_rng(SEED)
    a, b = np.asarray(num, float), np.asarray(den, float)
    idx = rng.integers(0, len(a), size=(N_BOOT, len(a)))
    r = a[idx].sum(axis=1) / b[idx].sum(axis=1)
    return [float(np.quantile(r, 0.025)), float(np.quantile(r, 0.975))]


def wilson(k: int, n: int) -> list[float] | None:
    if n == 0:
        return None
    z = 1.959963984540054
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [c - h, c + h]


def stream_summary(stream: str, settled: list[dict[str, Any]]) -> dict[str, Any]:
    """Cumulative read of one stream. n 0 is NO SETTLED SAMPLE, never a 0 rate."""
    rows = [r for r in settled if r["signal_id"] == stream and r["status"] == SETTLED]
    n = len(rows)
    if n == 0:
        return {"n": 0, "display": NO_SETTLED_SAMPLE, "verdict": PROSPECTIVE_TRACKING}
    res = [r["residual"] for r in rows]
    decided = [r for r in rows if r["ats_like"] != "PUSH"]
    wins = sum(1 for r in decided if r["ats_like"] == "WIN")
    priced = [r for r in rows if r["economics"].get("available")]
    pnl = [r["economics"]["fee_adjusted_pnl"] for r in priced]
    outlay = [r["economics"]["outlay"] for r in priced]
    out = {
        "n": n,
        "residual": {
            "mean": float(np.mean(res)),
            "median": float(np.median(res)),
            "sd": float(np.std(res, ddof=1)) if n > 1 else None,
            "ci95_bootstrap": _boot_mean_ci(res),
            "quantiles": {q: float(np.quantile(res, q / 100)) for q in (10, 25, 75, 90)},
            "positive_rate": sum(1 for x in res if x > 0) / n,
            "positive_rate_ci95": wilson(sum(1 for x in res if x > 0), n),
        },
        "ats_like": {
            "wins": wins,
            "losses": len(decided) - wins,
            "pushes": n - len(decided),
            "rate": wins / len(decided) if decided else None,
            "rate_ci95": wilson(wins, len(decided)),
        },
        "outright_win_rate": sum(1 for r in rows if r["outright_win"]) / n,
        "economics": (
            {
                "n": len(priced),
                "hits": sum(1 for r in priced if r["economics"]["pays"]),
                "fee_adjusted_pnl": round(sum(pnl), 6),
                "roi_on_outlay": sum(pnl) / sum(outlay),
                "roi_ci95_bootstrap": _boot_ratio_ci(pnl, outlay),
                "mean_price": float(np.mean([r["economics"]["entry_price"] for r in priced])),
                "unavailable": n - len(priced),
            }
            if priced
            else {"n": 0, "display": NO_SETTLED_SAMPLE, "unavailable": n}
        ),
        "home_away": dict(Counter(r["side"] for r in rows)),
    }
    out["verdict"] = verdict(stream, out)
    return out


def verdict(stream: str, summary: dict[str, Any]) -> str:
    n = summary["n"]
    reads = READS[stream]
    state = PROSPECTIVE_TRACKING
    for k in sorted(reads):
        if n >= k:
            state = reads[k]
    if state != PRIMARY_REVIEW:
        return state
    if stream == PROS_001:
        return REJECTED if summary["residual"]["mean"] <= 0 else REVIEW_REQUIRED
    rate = summary["ats_like"]["rate"]
    return REJECTED if rate is not None and rate < PROS_002_FALSIFY_BELOW else REVIEW_REQUIRED


def next_read(stream: str, n: int) -> dict[str, Any] | None:
    for k in sorted(READS[stream]):
        if n < k:
            return {"at_n": k, "state": READS[stream][k], "remaining": k - n}
    return None
