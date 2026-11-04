"""Coverage, identity join, settlement linkage and market-only baseline from the ARCHIVED ladder + raw ESPN summaries.

Strict by construction: a ticker joins an ESPN game only when exactly one schedule event matches both team names and the date window;
the ESPN start corroborates (or contradicts) Kalshi's milestone start; settlement is checked against ESPN's official period-1 line scores
(independent of Kalshi's `result`). Anything not proven stays in an explicit bucket with its own count. No price is ever called pregame
unless the row itself was observed pregame (see ladder_1q.eligibility). Baselines use the ask actually quoted (cost to buy YES), never a midpoint
fill; no fees are modelled here and nothing implies an executable edge."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from .archive_io import read_gz, read_jsonl
from .ladder_1q import _price, lead_bucket, parse_ts

RULE_RX = re.compile(r"of the (?P<a>.+?) vs (?P<b>.+?) college football game originally scheduled for (?P<d>[A-Z][a-z]{2} \d{1,2}, \d{4})")
ORIENT_RX = re.compile(r"more than (?P<x>[\d.]+) points in the 1st quarter.*resolves to Yes", re.I | re.S)
START_TOLERANCE = timedelta(minutes=45)


def load_rows(archive) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for f in sorted((archive / "rows").glob("*/*.jsonl")) if (archive / "rows").exists() else []:
        rows.extend(read_jsonl(f))
    return sorted(rows, key=lambda r: (r["ticker"], r["observed_at"]))


def load_manifests(archive) -> list[dict[str, Any]]:
    import json
    return [json.loads(p.read_text()) for p in sorted((archive / "runs").glob("*/*.json"))] if (archive / "runs").exists() else []


def espn_period1(espn_archive) -> dict[str, dict[str, Any]]:
    """event_id -> {teams:{id:{score,p1}}, completed, sha256, fetched_at}, from the newest complete raw summary per event."""
    import json
    out: dict[str, dict[str, Any]] = {}
    if not espn_archive or not (espn_archive / "index").exists():
        return out
    for f in sorted((espn_archive / "index").glob("*/*.jsonl")):
        for r in read_jsonl(f):
            if not r.get("fetch_ok") or not (r.get("final_state") or {}).get("completed"):
                continue
            payload = json.loads(read_gz(espn_archive / r["raw_file"]))
            comp = ((payload.get("header") or {}).get("competitions") or [{}])[0]
            teams = {}
            for c in comp.get("competitors") or []:
                ls = [x.get("displayValue") if isinstance(x, dict) else x for x in c.get("linescores") or []]
                try:
                    p1 = int(ls[0]) if ls else None
                except (TypeError, ValueError):
                    p1 = None
                teams[str((c.get("team") or {}).get("id") or c.get("id"))] = {"p1": p1, "n_periods": len(ls), "score": c.get("score"), "home_away": c.get("homeAway"), "names": [(c.get("team") or {}).get("displayName"), (c.get("team") or {}).get("location")]}
            out[r["event_id"]] = {"teams": teams, "kickoff": comp.get("date"), "sha256": r["sha256"], "fetched_at": r["fetched_at"], "raw_file": r["raw_file"]}
    return out


def match_event(rules: str, schedule_events: list[dict[str, Any]], name_variants) -> tuple[str | None, str]:
    m = RULE_RX.search(rules or "")
    if not m:
        return None, "RULES_TEXT_UNPARSEABLE"
    try:
        day = datetime.strptime(m.group("d"), "%b %d, %Y")
    except ValueError:
        return None, "RULES_DATE_UNPARSEABLE"
    va, vb = name_variants(m.group("a")), name_variants(m.group("b"))
    hits = []
    for ev in schedule_events:
        kick = parse_ts(ev.get("date"))
        if kick is None or abs((kick.replace(tzinfo=None) - day).total_seconds()) > 36 * 3600:
            continue
        sets = [set().union(*[name_variants(n) for n in c.get("names", [])]) for c in ev.get("competitors", [])]
        if len(sets) == 2 and ((va & sets[0] and vb & sets[1]) or (va & sets[1] and vb & sets[0])):
            hits.append(ev["id"])
    if len(hits) == 1:
        return hits[0], "MATCHED"
    return None, "NO_MATCH" if not hits else "AMBIGUOUS_MATCH"


def audit(rows: list[dict[str, Any]], manifests: list[dict[str, Any]], schedule_events: list[dict[str, Any]], espn: dict[str, dict[str, Any]], name_variants) -> dict[str, Any]:
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(r)
    sched = {e["id"]: e for e in schedule_events}
    cohort = Counter()
    join_status = Counter()
    buckets_rows, buckets_tickers = Counter(), defaultdict(set)
    games: dict[str, dict[str, Any]] = {}
    tickers_out = []
    brier = []
    for t, rs in by_ticker.items():
        last = rs[-1]
        raw = last["market"]
        floor = raw.get("floor_strike")
        orient = ORIENT_RX.search(raw.get("rules_primary") or "")
        orientation_ok = bool(orient and floor is not None and abs(float(orient.group("x")) - float(floor)) < 1e-9 and str(raw.get("strike_type")) == "greater")
        eid, why = match_event(raw.get("rules_primary"), schedule_events, name_variants)
        join_status[why] += 1
        for r in rs:
            buckets_rows[r["lead_bucket"]] += 1
            if r["eligible_pregame"]:
                buckets_tickers[r["lead_bucket"]].add(t)
        elig = [r for r in rs if r["eligible_pregame"] and r["quote_status"] == "QUOTED"]
        start_ok = None
        if eid and last.get("kalshi_start") and eid in espn:
            ek, kk = parse_ts(espn[eid]["kickoff"]), parse_ts(last["kalshi_start"])
            start_ok = bool(ek and kk and abs(ek - kk) <= START_TOLERANCE)
        p1 = None
        if eid and eid in espn:
            ts = espn[eid]["teams"]
            vals = [v["p1"] for v in ts.values()]
            if len(vals) == 2 and all(v is not None for v in vals):
                p1 = sum(vals)
        result = str(raw.get("result") or "").lower() or None
        derived = None if (p1 is None or floor is None or not orientation_ok) else ("yes" if p1 > float(floor) else "no")
        settle_consistent = None if (derived is None or result not in ("yes", "no")) else (derived == result)
        strict = bool(elig and eid and orientation_ok and start_ok and p1 is not None and result in ("yes", "no") and settle_consistent)
        cohort["tickers_observed"] += 1
        cohort["with_eligible_pregame_quote"] += bool(elig)
        cohort["joined_to_espn_game"] += bool(eid)
        cohort["start_corroborated_by_espn"] += bool(start_ok)
        cohort["orientation_verified"] += orientation_ok
        cohort["official_q1_score_available"] += p1 is not None
        cohort["kalshi_settled"] += result in ("yes", "no")
        cohort["settlement_consistent_with_espn_q1"] += bool(settle_consistent)
        cohort["settlement_inconsistent_with_espn_q1"] += settle_consistent is False
        cohort["strict_cohort"] += strict
        if eid:
            g = games.setdefault(eid, {"tickers": 0, "strict": 0})
            g["tickers"] += 1
            g["strict"] += strict
        if strict:
            q = max(elig, key=lambda r: r["observed_at"])            # the last eligible pregame quote actually observed
            ya = _price(q["market"], "yes_ask")
            yb = _price(q["market"], "yes_bid")
            y = 1.0 if result == "yes" else 0.0
            if ya is not None and 0 < ya < 1:
                brier.append({"ask": ya, "bid": yb, "y": y, "lead_minutes": q["lead_minutes"], "game": eid})
        tickers_out.append({"ticker": t, "n_rows": len(rs), "n_eligible_pregame_vintages": len(elig), "first_seen": rs[0]["observed_at"], "last_seen": rs[-1]["observed_at"], "join": why})
    n = len(brier)
    base = None
    if n:
        base = {"n_tickers": n, "n_games": len({b["game"] for b in brier}), "yes_ask_brier": round(sum((b["ask"] - b["y"]) ** 2 for b in brier) / n, 5),
                "yes_ask_mean_minus_outcome": round(sum(b["ask"] - b["y"] for b in brier) / n, 5),
                "note": "descriptive only: ask-implied probability of the LAST eligible pregame quote vs settled outcome; no fees, no fills, rungs within a game are dependent"}
    return {"schema": "cfb_1q_ladder_audit/1.0", "runs": len(manifests), "runs_capture_complete": sum(bool(m["capture_complete"]) for m in manifests), "runs_incomplete": [m["run_id"] for m in manifests if not m["capture_complete"]][:20],
            "first_run": manifests[0]["observed_at"] if manifests else None, "last_run": manifests[-1]["observed_at"] if manifests else None,
            "rows": len(rows), "cohort": dict(cohort), "join_status": dict(join_status), "rows_by_lead_bucket": dict(buckets_rows),
            "eligible_tickers_by_lead_bucket": {k: len(v) for k, v in buckets_tickers.items()}, "games_joined": len(games), "games_with_strict_cohort": sum(1 for g in games.values() if g["strict"]),
            "quote_status": dict(Counter(r["quote_status"] for r in rows)), "events_without_milestone_start": sorted({e for m in manifests for e in m.get("events_without_milestone_start", [])})[:50],
            "market_only_baseline": base, "baseline_status": "NO_STRICT_COHORT_YET" if not n else "DESCRIPTIVE_ONLY", "tickers": tickers_out[:500]}
