"""`cfb_research_signals/1.0.0`: the CFB research-state contract SIFT renders. PURE.

Every research fact here is READ from a frozen artifact or computed from prospective rows -- never typed in:

* the retrospective basis (MODERATE n = 6, 6-0, +32.5%; STRONG -8.5%; the below-85-cent exploratory cut) comes
  from the frozen CONTROL market study report and per-game rows, whose hashes are checked on load;
* prospective progress comes from `tracking.summarize` over the append-only settlement rows;
* each game's read comes from its V2 claims (`claims_v2`) and its historical range from the frozen calibration
  carried in those claims;
* prices are the side's own captured executable ask, with an honest status when there is none.

The wording rules are part of the contract: no probability, no fair price, no stake, no recommendation, and a
historical count is never turned into a chance.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cfb_edge_finder.control_market import PROTOCOL_SHA256 as STUDY_PROTOCOL_SHA256
from cfb_edge_finder.control_market.quotes import contract_for, orient_markets, parse_utc
from cfb_edge_finder.control_prospective import (
    CAPTURE_OK,
    CAPTURE_PENDING,
    CAPTURE_SYSTEM_FAILURE,
    DISAGREEMENT_BELOW_CENTS,
    H1_ID,
    H1_WORDING,
    H2_ID,
    H2_WORDING,
    INSUFFICIENT_DATA,
    MARKET_NOT_OFFERED,
    MODERATE_TIERS,
    NO_EDGE,
    ORIENTATION_FAILURE,
    PRICE_1_00,
    PROSPECTIVE_VERSION,
    PROTOCOL_PATH,
    PROTOCOL_SHA256,
    QUOTE_NOT_EXECUTABLE,
    REVIEW_CHECKPOINTS,
    SIGNALS_SCHEMA,
    STRONG_TIERS,
    VALUE_WATCH,
)
from cfb_edge_finder.control_prospective.capture import current_price, side_status, to_quote

STUDY_DIR = Path("data/scripting/validation/control_market_2026")
STUDY_REPORT = STUDY_DIR / "control_market_report.json"
STUDY_ROWS = STUDY_DIR / "control_market_rows.jsonl.gz"
STUDY_PR = "chmoses98/cfb-edge-finder#105"
STUDY_RESULTS_COMMIT = "b7df12c36fb0f94e0ccb9a26ac861763a7b418e8"
STUDY_ROWS_SHA256 = "3956ea6a50c04cd6234f172ace6b6f95a28a01d85969ce7c8ffd76f16cdf8208"
STUDY_MANIFEST_SHA256 = "bc74f8bf79ac2a56568ce966386b12b45e401498606893649f03033b376f4ab7"

NameKeys = Callable[[str], set[str]]

# ------------------------------------------------------------------ wording (the contract's own text)

VALUE_WATCH_TEXT = {
    "label": "Value Watch",
    "short": "Promising early market evidence",
    "meaning": "Historically validated football signal with promising early market evidence. Prospective "
    "confirmation in progress.",
    "status_line": "Prospective confirmation in progress",
}
STRONG_TEXT = {
    "label": "Strong Control",
    "short": "Validated football signal",
    "market_summary": "Market approximately efficient so far",
    "priced_high": "Market already prices this side aggressively",
    "explanation": "Strong CONTROL is a validated football signal. In the first 2026 priced research sample the "
    "market already priced it about right: it is a football read, and the price already reflects it.",
}
DISAGREEMENT_TEXT = {
    "label": "Market Disagreement",
    "short": "Market unusually skeptical of Strong CONTROL",
    "explanation": "The football model shows Strong CONTROL, but the market is unusually skeptical. Early 2026 "
    "research found these disagreements performed poorly for CONTROL. This is being tracked prospectively and is "
    "not a betting rule.",
}
NO_CLAIM_TEXT = {
    "label": "No clear SIFT read",
    "short": "No supported matchup claim cleared the evidence requirements.",
    "explanation": "This does not mean the game is unusually unpredictable. It means the current evidence taxonomy "
    "did not justify a stronger claim.",
}


class StudyMismatch(RuntimeError):
    """A frozen study artifact no longer matches its registered hash."""


# --------------------------------------------------------------------------- the frozen study basis


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_study(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """(report, rows, report sha256). Raises StudyMismatch if the rows are not the frozen rows."""
    report = json.loads((root / STUDY_REPORT).read_text(encoding="utf-8"))
    with gzip.open(root / STUDY_ROWS, "rb") as fh:
        payload = fh.read()
    if hashlib.sha256(payload).hexdigest() != STUDY_ROWS_SHA256 or report.get("rows_sha256") != STUDY_ROWS_SHA256:
        raise StudyMismatch("control market rows do not match the frozen rows hash")
    if report.get("manifest_sha256") != STUDY_MANIFEST_SHA256 or report.get("protocol_sha256") != STUDY_PROTOCOL_SHA256:
        raise StudyMismatch("control market report does not cite the frozen manifest/protocol")
    rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]
    return report, rows, _sha_file(root / STUDY_REPORT)


def _r(x: float | None, k: int = 4) -> float | None:
    return None if x is None else round(float(x), k)


def _group_basis(report: dict[str, Any], group: str) -> dict[str, Any]:
    g = report["results"]["POOLED"]["groups"][group]
    e, f = g["economics"], g["football"]
    return {
        "retrospective_priced_n": e.get("n", 0),
        "wins": e.get("wins"),
        "losses": e.get("losses"),
        "fee_adjusted_roi": _r(e.get("roi_on_outlay")),
        "roi_ci95": (e.get("roi_on_outlay_bootstrap") or {}).get("ci95"),
        "mean_entry": _r(e.get("entry_price_mean")),
        "break_even": _r(e.get("break_even_win_rate")),
        "football_n": f.get("n"),
        "football_wins": f.get("wins"),
    }


def _tier_verdicts(report: dict[str, Any], tiers: tuple[str, ...]) -> dict[str, str]:
    groups = report["results"]["POOLED"]["groups"]
    return {t: (groups[t].get("verdict") or {}).get("verdict", INSUFFICIENT_DATA) for t in tiers}


def disagreement_basis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """EXPLORATORY: frozen STRONG rows whose primary entry ask was below the disagreement threshold."""
    priced = [
        r
        for r in rows
        if r["control_strength"] == "STRONG"
        and (r["primary"]["economics"] or {}).get("available")
        and round(r["primary"]["entry"]["price"] * 100) < DISAGREEMENT_BELOW_CENTS
    ]
    n = len(priced)
    wins = sum(1 for r in priced if r["settlement"]["control_won"])
    pnl = sum(r["primary"]["economics"]["fee_adjusted_pnl"] for r in priced)
    outlay = sum(r["primary"]["economics"]["outlay"] for r in priced)
    return {
        "retrospective_priced_n": n,
        "wins": wins,
        "losses": n - wins,
        "fee_adjusted_roi": _r(pnl / outlay) if outlay else None,
        "break_even": _r(outlay / n) if n else None,
        "kind": "EXPLORATORY (post-reveal cut of the frozen rows; not a validated rule)",
    }


# --------------------------------------------------------------------------- per-game read


def _team(claims_teams: dict[str, Any], side: str) -> str:
    return (claims_teams.get(side) or {}).get("name") or side


def compact_claims(claims_v2: dict[str, Any]) -> dict[str, Any]:
    c, teams = claims_v2["claims"], claims_v2.get("teams") or {}
    control = c.get("control")
    scoring = c.get("scoring_environment")
    return {
        "control": None
        if not control
        else {
            "side": control["side"],
            "strength": control["strength"],
            "tier": control["tier"],
            "team": _team(teams, control["side"]),
        },
        "closeness": bool(c.get("closeness")),
        "pace": (c.get("pace") or {}).get("level"),
        "scoring": None
        if not scoring
        else {"level": scoring["level"], "incremental": bool(scoring.get("incremental_over_baseline"))},
        "defensive_suppression": bool(c.get("defensive_suppression")),
        "disruption": [
            {
                "side": d["side"],
                "team": _team(teams, d["side"]),
                "aligned_with_control": bool(d.get("aligned_with_control")),
            }
            for d in c.get("disruption") or []
        ],
        "explosive_upset_v1": bool(
            (c.get("explosive_upset") or {}).get("script_ids") or (c.get("explosive_upset") or {}).get("scripts")
        ),
    }


def card_line(cc: dict[str, Any], status: str) -> str:
    """One compact read: 'Georgia controls · slower pace · defensive suppression'."""
    if status == "NO_SUPPORTED_CLAIM":
        return NO_CLAIM_TEXT["label"]
    parts: list[str] = []
    control = cc["control"]
    if control:
        parts.append(
            f"{control['team']} controls" if control["strength"] == "STRONG" else f"{control['team']} control edge"
        )
    elif cc["closeness"]:
        parts.append("Close-game profile")
    else:
        parts.append("No side edge")
    if cc["pace"] == "HIGH":
        parts.append("faster pace")
    elif cc["pace"] == "LOW":
        parts.append("slower pace")
    if cc["scoring"]:
        parts.append("elevated scoring" if cc["scoring"]["level"] == "ELEVATED" else "lower-scoring environment")
    if cc["defensive_suppression"]:
        parts.append("defensive suppression")
    for d in cc["disruption"]:
        parts.append("disruption aligned" if d["aligned_with_control"] else f"{d['team']} disruption edge")
    return " · ".join(parts[:4])


def quick_read(cc: dict[str, Any], status: str) -> str:
    """The game page's one plain sentence."""
    if status == "NO_SUPPORTED_CLAIM":
        return NO_CLAIM_TEXT["short"]
    control = cc["control"]
    if control:
        first = (
            f"{control['team']} controls this matchup."
            if control["strength"] == "STRONG"
            else (f"{control['team']} holds a control edge in this matchup.")
        )
    elif cc["closeness"]:
        first = "This profiles as a close game."
    else:
        first = "Neither side holds a sustained edge."
    pace = {"HIGH": "a faster game", "LOW": "a slower game"}.get(cc["pace"] or "")
    scoring = cc["scoring"]["level"] if cc["scoring"] else None
    tails = []
    if scoring == "ELEVATED":
        tails.append("elevated scoring")
    elif scoring == "SUPPRESSED":
        tails.append("a lower-scoring environment")
    if cc["defensive_suppression"]:
        tails.append("defensive resistance")
    if pace and tails:
        second = f" Expect {pace} with {' and '.join(tails)}."
    elif pace:
        second = f" Expect {pace}."
    elif tails:
        second = f" Expect {' and '.join(tails)}."
    else:
        second = ""
    return first + second


def edges(cc: dict[str, Any]) -> list[str]:
    """2-4 short matchup bullets, strongest claim first."""
    out: list[str] = []
    if cc["control"]:
        out.append(f"{cc['control']['team']} owns the sustained-efficiency matchup")
    if cc["closeness"]:
        out.append("Evidence supports a relatively close game")
    if cc["defensive_suppression"]:
        out.append("Both defenses suppress opponent efficiency")
    if cc["pace"] == "HIGH":
        out.append("More plays than an average FBS game")
    elif cc["pace"] == "LOW":
        out.append("Fewer plays than an average FBS game")
    if cc["scoring"]:
        out.append(
            "Scoring above what the baseline expects"
            if cc["scoring"]["level"] == "ELEVATED"
            else "Lower-scoring environment (already in the baseline)"
        )
    for d in cc["disruption"]:
        out.append(
            f"{d['team']} owns the disruption edge" + (", aligned with control" if d["aligned_with_control"] else "")
        )
    return out[:4]


def signal_of(cc: dict[str, Any]) -> str | None:
    control = cc["control"]
    if not control:
        return None
    return (
        "VALUE_WATCH"
        if control["tier"] in MODERATE_TIERS
        else "STRONG_CONTROL"
        if control["tier"] in STRONG_TIERS
        else None
    )


def historical(claims_v2: dict[str, Any]) -> dict[str, Any] | None:
    control = claims_v2["claims"].get("control") or {}
    rng = control.get("historical_range")
    if not rng:
        return None
    return {
        "label": "Historical empirical range",
        "variable": "CONTROL-side final margin of past games with the same claim",
        "median": rng["median"],
        "central_50": rng["central_50"],
        "central_80": rng["central_80"],
        "wins": rng["win_rate"]["hits"],
        "n": rng["win_rate"]["n"],
        "development_seasons": rng.get("development_seasons"),
        "validation_n": (rng.get("validation") or {}).get("n"),
        "validation_seasons": (rng.get("validation") or {}).get("seasons"),
        "calibration_sha256": rng.get("calibration_sha256"),
        "not": rng.get("not"),
    }


def disagreement(signal: str | None, price: dict[str, Any]) -> bool | None:
    """STRONG CONTROL whose current executable CONTROL-side ask is below the threshold; None when unknowable."""
    if signal != "STRONG_CONTROL":
        return False
    if price.get("status") != "EXECUTABLE" or price.get("yes_ask") is None:
        return None
    return round(price["yes_ask"] * 100) < DISAGREEMENT_BELOW_CENTS


# --------------------------------------------------------------------------- assembling one game


def participant_for(
    event: dict[str, Any] | None, code: str | None, name: str | None, keys: NameKeys
) -> dict[str, Any] | None:
    if not event:
        return None
    parts = event.get("participants") or []
    if code:
        hit = [
            p
            for p in parts
            if str((p.get("source_ids") or {}).get("kalshi_team_code") or p.get("short_name")).upper() == code
        ]
        if len(hit) == 1:
            return hit[0]
    if name:
        nk = keys(name)
        hit = [p for p in parts if nk & keys(p.get("display_name") or "")]
        if len(hit) == 1:
            return hit[0]
    return None


def game_entry(
    *,
    game: dict[str, Any],
    payload: dict[str, Any] | None,
    frozen: dict[str, Any] | None,
    event: dict[str, Any] | None,
    latest_quotes: list[dict[str, Any]],
    capture_quotes: list[dict[str, Any]],
    capture_attempts: list[dict[str, Any]],
    schedule: list[dict[str, Any]],
    keys: NameKeys,
    now: str,
) -> dict[str, Any]:
    """One game's research card. `latest_quotes` are the side quotes of the freshest read (capture rows)."""
    claims_v2 = (payload or {}).get("claims_v2")
    content = (frozen or {}).get("content") or {}
    kickoff = content.get("kickoff_utc") or game.get("kickoff")
    espn_id = str(content["event_id"]) if content.get("event_id") else None
    base: dict[str, Any] = {
        "event_id": (event or {}).get("event_id"),
        "game_key": game["game_key"],
        "espn_event_id": espn_id,
        "kickoff_utc": kickoff,
        "season_week": (game.get("identity") or {}).get("season_week"),
        "title": game.get("title"),
    }
    if not claims_v2:
        return {
            **base,
            "status": "NOT_BUILT",
            "card_line": None,
            "read": None,
            "edges": [],
            "claims": None,
            "signal": None,
            "historical": None,
            "market": None,
            "capture": None,
            "data_quality": None,
            "claims_artifact_hash": None,
        }
    status = claims_v2["status"]
    cc = compact_claims(claims_v2)
    signal = signal_of(cc)
    teams = claims_v2.get("teams") or {}
    control = cc["control"]
    target_side = control["side"] if control else "home"
    target_team_id = str((teams.get(target_side) or {}).get("team_id") or "")
    # orient the freshest quotes with the frozen matcher, then read the target side's own market
    oriented = orient_markets([to_quote(q) for q in latest_quotes], schedule, keys) if latest_quotes and espn_id else {}
    contract = contract_for(target_team_id, espn_id, oriented) if oriented and espn_id else None
    ticker = contract["primary"][0] if contract and contract["primary"] else None
    latest = next((q for q in latest_quotes if q["market_ticker"] == ticker), None) if ticker else None
    if latest is not None:
        price = current_price(latest)
    elif latest_quotes and contract and contract["status"] != "RESOLVED":
        price = {"status": ORIENTATION_FAILURE, "yes_ask": None, "captured_at": latest_quotes[0].get("captured_at")}
    elif not latest_quotes and game.get("_market_read_ok"):
        price = {"status": MARKET_NOT_OFFERED, "yes_ask": None, "captured_at": None}
    else:
        price = current_price(None)
    price["source"] = (latest or {}).get("source")
    code = ticker.rsplit("-", 1)[-1].upper() if ticker else None
    participant = participant_for(event, code, (teams.get(target_side) or {}).get("name"), keys)
    game_capture_quotes = [q for q in capture_quotes if q.get("game_key") == game["game_key"]]
    cap_oriented = (
        orient_markets([to_quote(q) for q in game_capture_quotes], schedule, keys)
        if game_capture_quotes and espn_id
        else {}
    )
    cap_contract = contract_for(target_team_id, espn_id, cap_oriented) if cap_oriented and espn_id else None
    cap_ticker = cap_contract["primary"][0] if cap_contract and cap_contract["primary"] else None
    capture = (
        side_status(
            kickoff=kickoff,
            now=now,
            market_ticker=cap_ticker,
            orientation_status=cap_contract["status"] if cap_contract else None,
            quotes=game_capture_quotes,
            attempts=[a for a in capture_attempts if a.get("game_key") == game["game_key"]],
        )
        if kickoff
        else None
    )
    if capture is not None:
        capture["side"] = target_side
    return {
        **base,
        "status": status,
        "data_quality": (claims_v2.get("data_quality") or {}).get("level"),
        "claims_artifact_hash": claims_v2.get("claims_artifact_hash"),
        "teams": {
            s: {"name": (teams.get(s) or {}).get("name"), "team_id": (teams.get(s) or {}).get("team_id")}
            for s in ("home", "away")
        },
        "headline": (claims_v2.get("story") or {}).get("headline"),
        "card_line": card_line(cc, status),
        "read": quick_read(cc, status),
        "edges": edges(cc) if status != "NO_SUPPORTED_CLAIM" else [],
        "claims": cc,
        "signal": signal,
        "historical": historical(claims_v2),
        "market": {
            "side": target_side,
            "team": (teams.get(target_side) or {}).get("name"),
            "team_code": code or ((participant or {}).get("short_name")),
            "participant_id": (participant or {}).get("participant_id"),
            "is_control_side": control is not None,
            "price": price,
            "disagreement": disagreement(signal, price),
        },
        "capture": capture,
    }


# --------------------------------------------------------------------------- capture health (slate)


def capture_health(games: list[dict[str, Any]], now: str, horizon_hours: float = 36.0) -> dict[str, Any]:
    """The current slate: games kicking off in [now - 6 h, now + horizon]."""
    lo, hi = parse_utc(now).timestamp() - 6 * 3600, parse_utc(now).timestamp() + horizon_hours * 3600
    slate = [g for g in games if g.get("kickoff_utc") and lo <= parse_utc(g["kickoff_utc"]).timestamp() <= hi]
    counts: Counter[str] = Counter()
    for g in slate:
        counts[(g.get("capture") or {}).get("status") or "NOT_TRACKED"] += 1
    markets_found = sum(
        1
        for g in slate
        if (g.get("market") or {}).get("price", {}).get("status") not in (None, MARKET_NOT_OFFERED, "PRICE_UNAVAILABLE")
    )
    closed = sum(
        counts[s]
        for s in (
            CAPTURE_OK,
            MARKET_NOT_OFFERED,
            QUOTE_NOT_EXECUTABLE,
            PRICE_1_00,
            ORIENTATION_FAILURE,
            CAPTURE_SYSTEM_FAILURE,
        )
    )
    eligible = counts[CAPTURE_OK] + counts[ORIENTATION_FAILURE] + counts[CAPTURE_SYSTEM_FAILURE]
    return {
        "window": {"from": slate and min(g["kickoff_utc"] for g in slate) or None, "horizon_hours": horizon_hours},
        "expected_games": len(slate),
        "game_winner_markets_found": markets_found,
        "valid_primary_captures": counts[CAPTURE_OK],
        "pending": counts[CAPTURE_PENDING],
        "market_absent": counts[MARKET_NOT_OFFERED],
        "unexecutable": counts[QUOTE_NOT_EXECUTABLE],
        "price_1_00": counts[PRICE_1_00],
        "orientation_failures": counts[ORIENTATION_FAILURE],
        "system_failures": counts[CAPTURE_SYSTEM_FAILURE],
        "not_tracked": counts["NOT_TRACKED"],
        "windows_closed": closed,
        "primary_window_coverage": None if not eligible else round(counts[CAPTURE_OK] / eligible, 4),
        "statuses": dict(sorted(counts.items())),
    }


# --------------------------------------------------------------------------- the contract


def _prospective_block(summary: dict[str, Any] | None, pop: dict[str, Any] | None, key: str) -> dict[str, Any]:
    h = (summary or {}).get(key) or {}
    n_games = 0
    if pop:
        by = pop.get("by_tier") or {}
        tiers = MODERATE_TIERS if key == "H1" else STRONG_TIERS
        n_games = sum(by.get(t, 0) for t in tiers)
    settled_n = h.get("n", 0)
    return {
        "n": n_games,
        "priced_n": settled_n,
        "settled_n": settled_n,
        "wins": h.get("wins", 0) if settled_n else None,
        "losses": h.get("losses", 0) if settled_n else None,
        "fee_adjusted_roi": h.get("roi") if settled_n else None,
        "roi_ci95": h.get("roi_ci95") if settled_n else None,
        "checkpoint": h.get("checkpoint"),
        "review_threshold": 50,
        "statement": "No prospective settlements yet."
        if not settled_n
        else f"{h.get('wins')}-{h.get('losses')} in {settled_n} priced prospective games",
    }


def build_signals(
    *,
    root: Path,
    now: str,
    games: list[dict[str, Any]],
    summary: dict[str, Any] | None,
    population_counts: dict[str, Any] | None,
    sources: dict[str, Any],
) -> dict[str, Any]:
    report, rows, report_sha = load_study(root)
    mod = _group_basis(report, "ALL_MODERATE")
    strong = _group_basis(report, "ALL_STRONG")
    mod_verdicts = _tier_verdicts(report, MODERATE_TIERS)
    strong_verdicts = _tier_verdicts(report, STRONG_TIERS)
    gate = (summary or {}).get("moderate_status") or {"status": VALUE_WATCH, "source": "AUTOMATIC_GATE"}
    mod_status = gate["status"]
    mod_basis = {**mod, "market_verdict": INSUFFICIENT_DATA, "tier_verdicts": mod_verdicts}
    explanation = (
        "Moderate CONTROL is a historically validated football signal. In the first 2026 priced research sample, "
        f"Moderate CONTROL teams went {mod['wins']}–{mod['losses']} with positive fee-adjusted returns, but the "
        "sample is too small to establish a proven betting edge. SIFT is tracking it prospectively."
    )
    game_page = (
        "Moderate CONTROL is a historically validated football signal. The initial 2026 priced sample went "
        f"{mod['wins']}–{mod['losses']}, but the market sample is too small to establish a proven edge. Prospective "
        "confirmation is in progress."
    )
    doc = {
        "schema": SIGNALS_SCHEMA,
        "generated_at": now,
        "sport": "CFB",
        "methodology_version": "cfb-script-engine/2.0.0",
        "prospective_version": PROSPECTIVE_VERSION,
        "research_only": True,
        "never": ["probability", "fair price", "bet-up-to price", "stake", "recommendation"],
        "sources": sources,
        "study": {
            "id": "CONTROL_2026_MARKET_PRICING",
            "pr": STUDY_PR,
            "protocol_sha256": report["protocol_sha256"],
            "manifest_sha256": report["manifest_sha256"],
            "rows_sha256": report["rows_sha256"],
            "report_sha256": report_sha,
            "results_commit": STUDY_RESULTS_COMMIT,
            "overall_verdict": report["results"]["POOLED"]["overall_verdict"],
            "report_path": str(STUDY_REPORT),
            "kind": "retrospective football replay with prospectively captured prices",
        },
        "protocol": {
            "path": str(PROTOCOL_PATH),
            "sha256": PROTOCOL_SHA256,
            "hypotheses": {"H1": {"id": H1_ID, "wording": H1_WORDING}, "H2": {"id": H2_ID, "wording": H2_WORDING}},
            "review_checkpoints": {str(k): v for k, v in REVIEW_CHECKPOINTS.items()},
            "primary_window": {"name": "PRIMARY_60_180", "open_minutes_before": 180, "close_minutes_before": 60},
        },
        "signals": {
            "moderate_control": {
                "status": mod_status,
                "applies_to": list(MODERATE_TIERS),
                **VALUE_WATCH_TEXT,
                "explanation": explanation,
                "game_page_explanation": game_page,
                "disclaimer": "Promising early market evidence; prospective confirmation in progress.",
                "small_sample": f"Initial priced n = {mod['retrospective_priced_n']}",
                "research_basis": mod_basis,
                "prospective": _prospective_block(summary, population_counts, "H1"),
                "hypothesis": H1_ID,
                "gate": gate,
            },
            "strong_control": {
                "status": NO_EDGE,
                "applies_to": list(STRONG_TIERS),
                **STRONG_TEXT,
                "research_basis": {
                    **strong,
                    "market_verdict": strong_verdicts.get("HOME_CONTROL_STRONG"),
                    "tier_verdicts": strong_verdicts,
                },
                "prospective": None,  # filled below from the all-STRONG prospective economics
            },
            "market_disagreement": {
                "status": INSUFFICIENT_DATA,
                **DISAGREEMENT_TEXT,
                "rule": {
                    "applies_to": "STRONG_CONTROL",
                    "price": "current executable YES ask of the CONTROL team's own game-winner market",
                    "below_cents": DISAGREEMENT_BELOW_CENTS,
                    "never": "NO is never 1 - YES; no price means no flag",
                },
                "research_basis": disagreement_basis(rows),
                "prospective": {
                    **_prospective_block(summary, None, "H2"),
                    "realized_minus_break_even": ((summary or {}).get("H2") or {}).get("realized_minus_break_even"),
                },
                "hypothesis": H2_ID,
            },
            "no_claim": NO_CLAIM_TEXT,
        },
        "population": population_counts,
        "capture_health": capture_health(games, now),
        "games": games,
    }
    # strong prospective uses the all-strong economics, not H2
    strong_all = (summary or {}).get("strong") or {}
    doc["signals"]["strong_control"]["prospective"] = {
        "n": sum(((population_counts or {}).get("by_tier") or {}).get(t, 0) for t in STRONG_TIERS),
        "priced_n": strong_all.get("n", 0),
        "settled_n": strong_all.get("n", 0),
        "fee_adjusted_roi": strong_all.get("roi") if strong_all.get("n") else None,
        "statement": "No prospective settlements yet."
        if not strong_all.get("n")
        else f"{strong_all.get('wins')}-{strong_all.get('losses')} in {strong_all.get('n')} priced prospective games",
    }
    return doc


def content_key(doc: dict[str, Any]) -> str:
    """A fingerprint that ignores the clock, so an unchanged state is not republished."""
    stripped = {k: v for k, v in doc.items() if k not in ("generated_at", "sources")}
    return hashlib.sha256(json.dumps(stripped, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
