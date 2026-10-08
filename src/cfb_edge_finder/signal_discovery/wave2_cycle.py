"""One Wave-2 cycle inside the CFB research conductor: spread-ladder capture, frozen observations, entries,
settlements and the cumulative status report. RESEARCH ONLY.

Called by `scripts/cfb_research_conductor.py` after its own CONTROL capture, inside a try/except, so nothing
here can stop the existing conductor. Every write is an append to a JSONL file (never a rewrite) except the
status report, which is regenerated from the ledger on every change.

Store layout (on the `research-signals` branch):

    wave2/<season>/spread_attempts.jsonl   one row per spread-ladder attempt (OK / NO_MARKET / REQUEST_FAILED)
    wave2/<season>/spread_quotes.jsonl     the rungs of every OK attempt (side-specific asks, never complements)
    wave2/<season>/ledger.jsonl            OBSERVATION / ENTRY / SETTLEMENT rows, each written once
    wave2/<season>/cycles.jsonl            one line per cycle that did something or failed (machine-readable health)
    wave2/reports/<season>/wave2_status.json
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from cfb_edge_finder.archetype_research.replay import Target
from cfb_edge_finder.control_market.football import outcome_for
from cfb_edge_finder.control_market.quotes import orient_markets, parse_utc
from cfb_edge_finder.control_prospective import WINDOW_CLOSE_MIN
from cfb_edge_finder.control_prospective.capture import SlateGame, capture, due_slots, minutes_to, to_quote
from cfb_edge_finder.control_prospective.tracking import population
from cfb_edge_finder.scripting.football import LeagueFitCache
from cfb_edge_finder.scripting.packets import freshness
from cfb_edge_finder.signal_discovery import wave2 as W
from cfb_edge_finder.signal_discovery.evaluate import _derive_set2
from cfb_edge_finder.signal_discovery.features import eligibility, pregame_features

#: Implementation timing (inside the protocol's "before the window closes"): PROS-001 features are frozen at the
#: first cycle within 200 minutes of kickoff -- as late as possible so the game log is as complete as it will be
#: before the window opens at 180 -- and caught up until the window closes at 60.
OBSERVE_FROM_MIN = 200.0

NameKeys = Callable[[str], set[str]]


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":"), default=float) + "\n")


def _write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def identity(frozen: dict[str, Any] | None, schedule: list[dict[str, Any]]) -> dict[str, Any] | None:
    """ESPN identity of a Kalshi game (frozen V2 artifact event id -> schedule event), or None."""
    content = (frozen or {}).get("content") or {}
    eid = str(content.get("event_id") or "")
    if not eid:
        return None
    event = next((e for e in schedule if str(e.get("id")) == eid), None)
    if event is None:
        return None
    sides = {c.get("home_away"): c for c in event.get("competitors") or []}
    if set(sides) != {"home", "away"}:
        return None
    return {
        "event": event,
        "game_id": eid,
        "teams": {
            s: {
                "team_id": str(sides[s]["team_id"]),
                "name": sides[s].get("location") or (sides[s].get("names") or [None])[0],
            }
            for s in ("home", "away")
        },
        "neutral_site": bool(event.get("neutral_site")),
    }


def feature_for(
    ident: dict[str, Any],
    kickoff: str,
    season: int,
    team_rows: tuple,
    schedule: list[dict[str, Any]],
    cache: LeagueFitCache,
) -> dict[str, Any]:
    """The exact Wave-1 feature row (production builder, 04:00 ET cutoff) plus the derived DSC-001 difference."""
    event = ident["event"]

    def division(team_id: str) -> str | None:
        rows = [r for r in team_rows if r.team_id == team_id]
        return rows[-1].team_division if rows else None

    home, away = ident["teams"]["home"], ident["teams"]["away"]
    target = Target(
        game_id=ident["game_id"],
        season=season,
        week=None,
        season_type=None,
        kickoff_utc=parse_utc(kickoff).strftime("%Y-%m-%dT%H:%M:%SZ"),
        home_id=home["team_id"],
        away_id=away["team_id"],
        home_name=home["name"],
        away_name=away["name"],
        neutral_site=ident["neutral_site"],
        conference_game=event.get("conference_game"),
        home_division=division(home["team_id"]),
        away_division=division(away["team_id"]),
    )
    row = pregame_features(team_rows, target, cache)
    row["eligibility"] = eligibility(row)
    if row["eligibility"] is None:
        _derive_set2(row)
    else:
        row.setdefault(W.DSC001_FEATURE, None)
    row["history_rows"] = sum(1 for r in team_rows if r.kickoff() < parse_utc(kickoff))
    row["football_data_cutoff"] = row.get("cutoff") or row.get("football_data_cutoff")
    try:
        row["freshness"] = freshness({**event, "date": kickoff}, schedule, list(team_rows))
    except Exception as exc:  # noqa: BLE001 -- descriptive only
        row["freshness"] = {"status": "UNKNOWN", "error": f"{type(exc).__name__}: {exc}"}
    return row


def run(
    *,
    store_dir: Path,
    root: Path,
    season: int,
    now: str,
    games: list[SlateGame],
    schedule: list[dict[str, Any]],
    team_rows: list,
    frozen: Callable[[str], dict[str, Any] | None],
    ledger_v2: list[dict[str, Any]],
    winner_quotes: list[dict[str, Any]],
    keys: NameKeys,
    fetch_spread: Callable[[str], list[dict[str, Any]]] | None,
    code_sha: str | None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """One cycle. Idempotent: a second call at the same instant appends nothing."""
    W.load_candidates(root)
    base = store_dir / "wave2" / str(season)
    attempts_path, quotes_path = base / "spread_attempts.jsonl", base / "spread_quotes.jsonl"
    ledger_path = base / "ledger.jsonl"
    attempts, quotes, ledger = _read_rows(attempts_path), _read_rows(quotes_path), _read_rows(ledger_path)
    done = {r["row_id"] for r in ledger}
    errors: list[dict[str, Any]] = []
    rows_team = tuple(team_rows)
    activation = parse_utc(W.ACTIVATION_UTC)
    pop_games = [g for g in games if parse_utc(g.kickoff_utc) >= activation]

    # 1. spread-ladder capture at the frozen slots (same resilient schedule as the CONTROL winner capture)
    attempted: dict[str, set[str]] = {}
    for a in attempts:
        attempted.setdefault(a["game_key"], set()).update(a.get("slots") or [])
    new_attempts, new_quotes = [], []
    for g in pop_games:
        slots = due_slots(g.kickoff_utc, now, attempted.get(g.game_key, set()))
        if not slots:
            continue
        markets, error = None, None
        if fetch_spread is None:
            error = "offline: no Kalshi read"
        else:
            try:
                markets = W.spread_markets_for(fetch_spread(g.game_key), g.game_key)
            except Exception as exc:  # noqa: BLE001 -- logged as REQUEST_FAILED, never as "no market"
                error = f"{type(exc).__name__}: {exc}"
        attempt, rows = capture(g, slots, now, markets, request_error=error)
        attempt["series"] = W.SPREAD_SERIES
        new_attempts.append(attempt)
        new_quotes.extend({**r, "series": W.SPREAD_SERIES} for r in rows)
    _append(attempts_path, new_attempts)
    _append(quotes_path, new_quotes)
    attempts += new_attempts
    quotes += new_quotes

    # 2. observations (frozen before the window closes)
    new_rows: list[dict[str, Any]] = []
    cache = LeagueFitCache()
    final_pregame = population(ledger_v2)
    for g in pop_games:
        m = minutes_to(g.kickoff_utc, now)
        if m <= 0:
            continue
        ident = identity(frozen(g.game_key), schedule)
        game = {
            "game_key": g.game_key,
            "kickoff_utc": g.kickoff_utc,
            "game_id": ident["game_id"] if ident else None,
            "teams": ident["teams"] if ident else None,
        }
        before_close = m >= WINDOW_CLOSE_MIN
        rid1 = f"{W.PROS_001}:{W.OBSERVATION}:{g.game_key}"
        if rid1 not in done:
            if before_close and m <= OBSERVE_FROM_MIN:
                if ident is None:
                    pass  # identity may still arrive; concluded at window close
                else:
                    try:
                        feat = feature_for(ident, g.kickoff_utc, season, rows_team, schedule, cache)
                        new_rows.append(
                            W.observation_001(game=game, feature=feat, reason=None, now=now, code_sha=code_sha)
                        )
                        done.add(rid1)
                    except Exception as exc:  # noqa: BLE001 -- retried next cycle; SYSTEM_FAILURE at window close
                        errors.append(
                            {
                                "game_key": g.game_key,
                                "stage": "PROS-001 features",
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
            elif not before_close:
                reason = "NO_ESPN_IDENTITY" if ident is None else "OBSERVATION_NOT_FROZEN_BEFORE_WINDOW_CLOSE"
                new_rows.append(W.observation_001(game=game, feature=None, reason=reason, now=now, code_sha=code_sha))
                done.add(rid1)
        rid2 = f"{W.PROS_002}:{W.OBSERVATION}:{g.game_key}"
        if rid2 not in done:
            fp = final_pregame.get(g.game_key)
            tier = (((fp or {}).get("claims") or {}).get("control") or {}).get("tier")
            if before_close and fp is not None and tier in W.STRONG_TIERS and ident is not None:
                new_rows.append(W.observation_002(game=game, ledger_row=fp, now=now, code_sha=code_sha))
                done.add(rid2)
            elif not before_close:
                claim = (((frozen(g.game_key) or {}).get("content") or {}).get("claims") or {}).get("control") or {}
                if claim.get("strength") == "STRONG":
                    reason = (
                        "NO_ESPN_IDENTITY"
                        if ident is None
                        else "STRONG_CONTROL_WITHOUT_FINAL_PREGAME_BEFORE_WINDOW_CLOSE"
                    )
                    row = W.missing_observation(W.PROS_002, game, now, code_sha, reason)
                    if ident is None:
                        row["status"] = W.IDENTITY_FAILURE
                    new_rows.append(row)
                    done.add(rid2)

    # 3. entries (the window has closed)
    ledger_all = ledger + new_rows
    for obs in [r for r in ledger_all if r["record"] == W.OBSERVATION and r["status"] == W.PENDING]:
        rid = f"{obs['signal_id']}:{W.ENTRY}:{obs['game_key']}"
        if rid in done or minutes_to(obs["kickoff_utc"], now) >= WINDOW_CLOSE_MIN:
            continue
        g_attempts = [a for a in attempts if a["game_key"] == obs["game_key"]]
        g_quotes = [q for q in quotes if q["game_key"] == obs["game_key"]]
        w_quotes = [q for q in winner_quotes if q.get("game_key") == obs["game_key"]]
        orientation = orient_markets([to_quote(q) for q in w_quotes], schedule, keys) if w_quotes else {}
        codes = W.team_codes(orientation, str(obs["game_id"]))
        cp = W.checkpoint(
            kickoff=obs["kickoff_utc"],
            now=now,
            attempts=g_attempts,
            quotes=g_quotes,
            codes_status=codes,
            team_id=obs["side_team"]["team_id"],
        )
        if cp["status"] == W.PENDING:
            continue
        new_rows.append(W.entry_row(obs=obs, cp=cp, now=now, code_sha=code_sha))
        done.add(rid)

    # 4. settlements (final score in the production game log)
    ledger_all = ledger + new_rows
    for ent in [r for r in ledger_all if r["record"] == W.ENTRY and r["status"] == W.ELIGIBLE]:
        rid = f"{ent['signal_id']}:{W.SETTLEMENT}:{ent['game_key']}"
        if rid in done or parse_utc(now) <= parse_utc(ent["kickoff_utc"]):
            continue
        outcome = outcome_for(rows_team, str(ent["game_id"]), ent["side_team"]["team_id"])
        if outcome.get("status") != "SETTLED":
            continue
        w_quotes = [q for q in winner_quotes if q.get("game_key") == ent["game_key"]]
        orientation = orient_markets([to_quote(q) for q in w_quotes], schedule, keys) if w_quotes else {}
        codes = W.team_codes(orientation, str(ent["game_id"]))
        closing = (
            W.closing_context(
                attempts=[a for a in attempts if a["game_key"] == ent["game_key"]],
                quotes=[q for q in quotes if q["game_key"] == ent["game_key"]],
                codes=codes["codes"],
                team_id=ent["side_team"]["team_id"],
                kickoff=ent["kickoff_utc"],
            )
            if codes["status"] == "RESOLVED"
            else None
        )
        new_rows.append(W.settlement_row(entry=ent, outcome=outcome, closing=closing, now=now, code_sha=code_sha))
        done.add(rid)

    _append(ledger_path, new_rows)
    ledger += new_rows
    if new_attempts or new_rows or errors:
        _append(
            base / "cycles.jsonl",
            [{"at": now, "run_id": run_id, "attempts": len(new_attempts), "rows": len(new_rows), "errors": errors}],
        )

    # 5. cumulative status report
    report = status_report(ledger, attempts, now=now, season=season, errors=errors)
    _write_if_changed(
        store_dir / "wave2" / "reports" / str(season) / "wave2_status.json",
        json.dumps(report, indent=1, sort_keys=True, default=float) + "\n",
    )
    return {
        "spread_attempts": len(new_attempts),
        "spread_quotes": len(new_quotes),
        "ledger_rows": len(new_rows),
        "errors": errors,
        "by_status": dict(Counter(f"{r['signal_id']}:{r['record']}:{r['status']}" for r in new_rows)),
    }


def status_report(
    ledger: list[dict[str, Any]], attempts: list[dict[str, Any]], *, now: str, season: int, errors: list[dict[str, Any]]
) -> dict[str, Any]:
    """Machine-readable cumulative state. Built from the ledger only; `generated_at` is the last ledger change."""
    streams = {}
    settled = [r for r in ledger if r["record"] == W.SETTLEMENT]
    for sid in W.STREAMS:
        obs = [r for r in ledger if r["signal_id"] == sid and r["record"] == W.OBSERVATION]
        ent = [r for r in ledger if r["signal_id"] == sid and r["record"] == W.ENTRY]
        st = [r for r in settled if r["signal_id"] == sid]
        entered_ids = {r["game_key"] for r in ent}
        settled_ids = {r["game_key"] for r in st}
        awaiting = [
            r["game_key"]
            for r in ent
            if r["status"] == W.ELIGIBLE
            and r["game_key"] not in settled_ids
            and parse_utc(now) > parse_utc(r["kickoff_utc"])
        ]
        summary = W.stream_summary(sid, settled)
        streams[sid] = {
            "observations": dict(Counter(r["status"] for r in obs)),
            "observation_reasons": dict(Counter(r["reason"] for r in obs if r.get("reason"))),
            "awaiting_entry": sum(1 for r in obs if r["status"] == W.PENDING and r["game_key"] not in entered_ids),
            "entries": dict(Counter(r["status"] for r in ent)),
            "entry_reasons": dict(Counter(r["reason"] for r in ent if r.get("reason"))),
            "settlement_pending": len(awaiting),
            "summary": summary,
            "next_read": W.next_read(sid, summary["n"]),
        }
    values = [
        r["signal"]["value"]
        for r in ledger
        if r["signal_id"] == W.PROS_001
        and r["record"] == W.OBSERVATION
        and (r.get("signal") or {}).get("value") is not None
    ]
    feature_distribution = (
        {
            "n": len(values),
            "mean": float(np.mean(values)),
            "sd": float(np.std(values, ddof=1)) if len(values) > 1 else None,
            "wave1_mean": W.DSC001_MEAN,
            "wave1_sd": W.DSC001_SD,
            "share_abs_z_ge_1": sum(1 for v in values if abs((v - W.DSC001_MEAN) / W.DSC001_SD) >= 1) / len(values),
        }
        if values
        else {"n": 0, "display": W.NO_SETTLED_SAMPLE}
    )
    window_attempts = [a for a in attempts if a.get("in_primary_window")]
    return {
        "schema": W.STATUS_SCHEMA,
        "version": W.VERSION,
        "candidates_sha256": W.CANDIDATES_SHA256,
        "season": season,
        "activation_utc": W.ACTIVATION_UTC,
        "kind": "PROSPECTIVE research state only: no recommendation, no sizing, not read by SIFT.",
        "last_ledger_row_at": max((r["generated_at"] for r in ledger), default=None),
        "streams": streams,
        "pros001_feature_distribution": feature_distribution,
        "capture_health": {
            "spread_attempts": len(attempts),
            "spread_attempts_in_window": len(window_attempts),
            "by_outcome": dict(Counter(a["outcome"] for a in attempts)),
            "in_window_by_outcome": dict(Counter(a["outcome"] for a in window_attempts)),
            "system_failures": sum(1 for r in ledger if r["status"] == W.SYSTEM_FAILURE),
            "last_cycle_errors": errors,
        },
        "never_emitted": ["EDGE_CONFIRMED"],
    }
