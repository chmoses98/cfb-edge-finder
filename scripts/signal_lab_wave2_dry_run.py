#!/usr/bin/env python3
"""Football Signal Discovery Lab Wave 2 (CFB): current-slate ELIGIBILITY dry run. RESEARCH ONLY.

    python scripts/signal_lab_wave2_dry_run.py --main-dir . --ledger <publications_v2.jsonl> \
        [--hours 48] [--ladder-source catalog|live] [--out dry_run.json]

For every slate game kicking off in the next `--hours`, it says what the frozen streams WOULD see right now:
CFB-PROS-001 feature, z and side; CFB-PROS-002 STRONG CONTROL from the V2 ledger (FINAL_PREGAME if one exists,
else the latest publication, labelled); and the CURRENT Kalshi spread ladder's market_implied_margin. The ladder
comes from the committed market catalog snapshot (`catalog`, labelled with its capture time) or from Kalshi's
public API (`live`).

It never reads an outcome, never writes the Wave-2 ledger and is not a checkpoint: the entry is only ever the
conductor's PRIMARY_60_180 capture.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import cfb_research_conductor as conductor  # noqa: E402

from cfb_edge_finder.control_market.quotes import orient_markets, parse_utc  # noqa: E402
from cfb_edge_finder.control_prospective.capture import normalize_market, to_quote  # noqa: E402
from cfb_edge_finder.scripting.football import LeagueFitCache  # noqa: E402
from cfb_edge_finder.signal_discovery import wave2 as W  # noqa: E402
from cfb_edge_finder.signal_discovery import wave2_cycle as C  # noqa: E402


def catalog_ladder(main_dir: Path, game: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    detail = json.loads((main_dir / "data" / "live" / game["markets_file"]).read_text())
    spread, winner = [], []
    for m in detail.get("markets") or []:
        # the catalog carries DOLLARS under the bare keys (the API's bare keys are legacy cents): restate them
        # under the explicit *_dollars spelling so the shared normaliser reads them as dollars
        raw = {k: v for k, v in m.items() if k not in ("yes_bid", "yes_ask", "no_bid", "no_ask")}
        raw["ticker"] = m.get("market_ticker")
        for k in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
            raw[f"{k}_dollars"] = None if m.get(k) is None else f"{float(m[k]):.4f}"
        if m.get("family") == "game_spread":
            spread.append(raw)
        elif m.get("family") == "game_moneyline":
            winner.append(raw)
    return spread, winner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--main-dir", type=Path, default=ROOT)
    ap.add_argument("--ledger", type=Path, default=None, help="script-ledger publications_v2.jsonl")
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--now", default=None)
    ap.add_argument("--ladder-source", choices=("catalog", "live"), default="catalog")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    W.load_candidates(ROOT)
    now = args.now or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    inputs = conductor.Inputs(args.main_dir, conductor.SEASON)
    ledger = [json.loads(x) for x in args.ledger.read_text().splitlines() if x.strip()] if args.ledger else []
    latest: dict[str, dict] = {}
    for r in ledger:
        if r.get("methodology_version") != "cfb-script-engine/2.0.0":
            continue
        if r["game_key"] not in latest or r["recorded_at"] > latest[r["game_key"]]["recorded_at"]:
            latest[r["game_key"]] = r
    hi = parse_utc(now) + timedelta(hours=args.hours)
    rows_team = tuple(inputs.team_rows)
    cache = LeagueFitCache()
    out = []
    for g in inputs.catalog.get("games") or []:
        frozen = inputs.frozen(g["game_key"])
        kickoff = ((frozen or {}).get("content") or {}).get("kickoff_utc") or g.get("kickoff")
        if not kickoff or not parse_utc(now) < parse_utc(kickoff) <= hi:
            continue
        rec: dict[str, Any] = {"game_key": g["game_key"], "kickoff_utc": kickoff, "title": g.get("title")}
        rec["in_population"] = parse_utc(kickoff) >= parse_utc(W.ACTIVATION_UTC)
        ident = C.identity(frozen, inputs.schedule)
        if ident is None:
            rec["status"] = W.IDENTITY_FAILURE
            out.append(rec)
            continue
        game = {"game_key": g["game_key"], "kickoff_utc": kickoff, "game_id": ident["game_id"], "teams": ident["teams"]}
        try:
            feat = C.feature_for(ident, kickoff, conductor.SEASON, rows_team, inputs.schedule, cache)
            obs = W.observation_001(game=game, feature=feat, reason=None, now=now, code_sha=None)
            rec["pros001"] = {
                "status": obs["status"],
                "reason": obs.get("reason"),
                "z": (obs.get("signal") or {}).get("z"),
                "side": obs.get("side"),
            }
        except Exception as exc:  # noqa: BLE001
            rec["pros001"] = {"status": W.SYSTEM_FAILURE, "reason": f"{type(exc).__name__}: {exc}"}
        lr = latest.get(g["game_key"])
        control = ((lr or {}).get("claims") or {}).get("control") or {}
        rec["pros002"] = {
            "control_tier": control.get("tier"),
            "source_kind": (lr or {}).get("kind"),
            "candidate": control.get("tier") in W.STRONG_TIERS,
        }
        try:
            if args.ladder_source == "live":
                spread = conductor.fetch_spread_ladder(conductor.KALSHI_BASE, g["game_key"])
                winner = conductor.fetch_event_markets(conductor.KALSHI_BASE, f"KXNCAAFGAME-{g['game_key']}")
                rec["ladder_source"] = {"kind": "KALSHI_LIVE_READ", "at": now}
            else:
                spread, winner = catalog_ladder(args.main_dir, g)
                rec["ladder_source"] = {
                    "kind": "KALSHI_CATALOG_SNAPSHOT",
                    "at": (inputs.catalog.get("capture") or {}).get("captured_at"),
                }
            rows = W.spread_markets_for(spread, g["game_key"])
            wq = [
                {
                    **normalize_market(m),
                    "captured_at": now,
                    "title": g.get("title"),
                    "kickoff_utc": kickoff,
                    "attempt_id": "dry",
                    "game_key": g["game_key"],
                }
                for m in winner
            ]
            orientation = orient_markets([to_quote(q) for q in wq], inputs.schedule, conductor.market_keys)
            codes = W.team_codes(orientation, ident["game_id"])
            rec["orientation"] = codes["status"]
            rec["spread_rungs"] = len(rows)
            margins = {}
            if codes["status"] == "RESOLVED":
                for side in ("home", "away"):
                    value, _ = W.implied_margin(W.ladder_points(rows, ident["teams"][side]["team_id"], codes["codes"]))
                    margins[side] = None if value is None else round(value, 2)
            rec["current_market_implied_margin"] = margins
            side2 = control.get("side")
            if rec["pros002"]["candidate"] and side2 in margins and margins[side2] is not None:
                rec["pros002"]["would_be_eligible_now"] = margins[side2] < W.SKEPTIC_BELOW_POINTS
        except Exception as exc:  # noqa: BLE001
            rec["ladder_error"] = f"{type(exc).__name__}: {exc}"
        out.append(rec)
    out.sort(key=lambda r: (r["kickoff_utc"], r["game_key"]))
    pop = [r for r in out if r.get("in_population")]
    summary = {
        "now": now,
        "kind": "ELIGIBILITY DRY RUN: no outcome read, no ledger written, not a checkpoint",
        "games": len(out),
        "population_games": len(pop),
        "pros001": dict(Counter(str((r.get("pros001") or {}).get("status", r.get("status"))) for r in pop)),
        "pros001_reasons": dict(Counter(str((r.get("pros001") or {}).get("reason")) for r in pop)),
        "pros002_strong_candidates": sum(1 for r in pop if (r.get("pros002") or {}).get("candidate")),
        "pros002_would_be_eligible_now": sum(1 for r in pop if (r.get("pros002") or {}).get("would_be_eligible_now")),
        "ladders_with_center": sum(
            1 for r in pop if any(v is not None for v in (r.get("current_market_implied_margin") or {}).values())
        ),
        "orientation": dict(Counter(str(r.get("orientation")) for r in pop)),
    }
    doc = {"summary": summary, "games": out}
    text = json.dumps(doc, indent=1, sort_keys=True, default=float)
    if args.out:
        args.out.write_text(text + "\n")
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
