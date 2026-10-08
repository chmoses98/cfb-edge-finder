"""Historical sportsbook lines from the CFBD cache. RESEARCH ONLY; market side of the wall.

Never imported by `features.py`. The CFBD `/lines` payload carries the final
scores of each game (`homeScore`, `awayScore`); those fields are DROPPED here
on load, so the market table cannot become a back door for the outcome.

*** WHAT A CFBD LINE IS ***
One row per provider per game with `spread` (HOME perspective: negative =
home favoured), `overUnder`, sometimes `spreadOpen` / `overUnderOpen`, and
from 2021 sometimes `homeMoneyline` / `awayMoneyline` (American odds). CFBD
records no timestamp; these are the providers' last (closing or near-closing)
numbers. They are labelled `CLOSING_UNTIMESTAMPED` and never described as an
executable price we could have obtained.

*** PRE-REGISTERED CONSENSUS RULE (fixed before any outcome join) ***
* spread / total: median of the non-null values across distinct providers
  (provider names normalised; "Draft Kings" == "DraftKings"). Half-point
  medians are kept as is.
* dispersion: max - min across providers; > 3.0 points flags LINE_DISPERSION
  (a sensitivity exclusion, never a primary one).
* opening spread: median of non-null `spreadOpen` (2021+ only).
* moneyline: each provider with BOTH sides -> no-vig home probability
  (multiplicative normalisation); consensus = median. Economic tests use ONE
  provider's actual American odds, first available in the fixed priority
  ML_PROVIDER_PRIORITY. A spread or total carries no price in CFBD, so ATS /
  total economics are computed at an ASSUMED standard -110 and labelled so.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from statistics import median
from typing import Any

ML_PROVIDER_PRIORITY = ("DraftKings", "ESPN Bet", "Bovada", "William Hill (New Jersey)", "Caesars")
DISPERSION_FLAG_POINTS = 3.0
ASSUMED_SPREAD_TOTAL_AMERICAN = -110
LINE_TIMING = "CLOSING_UNTIMESTAMPED"

#: Keys of a CFBD lines game record that may be read (scores deliberately absent).
GAME_KEYS = ("id", "season", "week", "seasonType", "startDate", "homeTeam", "awayTeam", "homeTeamId", "awayTeamId")
LINE_KEYS = ("provider", "spread", "spreadOpen", "overUnder", "overUnderOpen", "homeMoneyline", "awayMoneyline")


def _provider(name: str | None) -> str:
    text = (name or "").strip()
    return {"Draft Kings": "DraftKings"}.get(text, text)


def american_to_decimal(odds: float) -> float:
    return 1.0 + (odds / 100.0 if odds > 0 else 100.0 / -odds)


def american_to_implied(odds: float) -> float:
    return 1.0 / american_to_decimal(odds)


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def consensus(game: dict[str, Any]) -> dict[str, Any]:
    """One market row per game from the provider rows (scores never read)."""
    meta = {k: game.get(k) for k in GAME_KEYS}
    lines = [{k: ln.get(k) for k in LINE_KEYS} for ln in game.get("lines") or []]
    by_provider: dict[str, dict[str, Any]] = {}
    for ln in lines:
        p = _provider(ln["provider"])
        if p and p not in by_provider:
            by_provider[p] = ln
    spreads = {p: _num(ln["spread"]) for p, ln in by_provider.items() if _num(ln["spread"]) is not None}
    totals = {p: _num(ln["overUnder"]) for p, ln in by_provider.items() if _num(ln["overUnder"]) is not None}
    opens = [_num(ln["spreadOpen"]) for ln in by_provider.values() if _num(ln["spreadOpen"]) is not None]
    topens = [_num(ln["overUnderOpen"]) for ln in by_provider.values() if _num(ln["overUnderOpen"]) is not None]
    nv, ml_exec = [], None
    for ln in by_provider.values():
        h, a = _num(ln["homeMoneyline"]), _num(ln["awayMoneyline"])
        if h is None or a is None or h == 0 or a == 0:
            continue
        ih, ia = american_to_implied(h), american_to_implied(a)
        nv.append(ih / (ih + ia))
    for p in ML_PROVIDER_PRIORITY:
        ln = by_provider.get(p)
        if ln is None:
            continue
        h, a = _num(ln["homeMoneyline"]), _num(ln["awayMoneyline"])
        if h is not None and a is not None and h != 0 and a != 0:
            ml_exec = {"provider": p, "home_american": h, "away_american": a}
            break
    spread = median(spreads.values()) if spreads else None
    total = median(totals.values()) if totals else None
    return {
        "game_id": str(meta["id"]),
        "season": meta["season"],
        "line_timing": LINE_TIMING,
        "providers_spread": sorted(spreads),
        "spread_home": spread,
        "spread_dispersion": (max(spreads.values()) - min(spreads.values())) if len(spreads) > 1 else 0.0,
        "spread_open_home": median(opens) if opens else None,
        "total": total,
        "total_dispersion": (max(totals.values()) - min(totals.values())) if len(totals) > 1 else 0.0,
        "total_open": median(topens) if topens else None,
        "ml_home_novig": median(nv) if nv else None,
        "ml_providers": len(nv),
        "ml_exec": ml_exec,
        "line_dispersion_flag": bool(
            spreads and (max(spreads.values()) - min(spreads.values())) > DISPERSION_FLAG_POINTS
        ),
        "cfbd_home_team_id": str(meta["homeTeamId"]) if meta["homeTeamId"] is not None else None,
        "cfbd_away_team_id": str(meta["awayTeamId"]) if meta["awayTeamId"] is not None else None,
    }


def load_lines(directory: Path) -> dict[str, dict[str, Any]]:
    """game_id -> consensus market row, regular + postseason."""
    out: dict[str, dict[str, Any]] = {}
    for name in ("lines_regular.json.gz", "lines_postseason.json.gz"):
        path = Path(directory) / name
        if not path.exists():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for game in json.load(fh):
                row = consensus(game)
                out[row["game_id"]] = row
    return out
