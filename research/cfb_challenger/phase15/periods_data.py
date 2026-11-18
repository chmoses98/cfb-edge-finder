"""Derive per-game period scores (ESPN header linescores) from archived postgame summaries.

Output parquet (one row per game, ESPN home/away orientation): game_id, season, kickoff_utc, neutral, home, away,
home_abbr, away_abbr,
h_q1..h_q4, a_q1..a_q4, h_ot, a_ot (sum of all periods after the 4th), h_final, a_final, n_periods, valid.
`valid` = 4+ linescores per side and they sum to the final score. Outcomes only (used as targets and as lagged
team history strictly before a game date); nothing pregame is read from these files.

    python -I research/cfb_challenger/phase15/periods_data.py OUT.parquet DIR [DIR ...]
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd


def _num(x):
    try:
        return float(x.get("value", x.get("displayValue")))
    except (TypeError, ValueError, AttributeError):
        return None


def parse_summary(d: dict) -> dict | None:
    c = d["header"]["competitions"][0]
    if c.get("status", {}).get("type", {}).get("name") not in ("STATUS_FINAL", None):
        return None
    side = {t["homeAway"]: t for t in c["competitors"]}
    if set(side) != {"home", "away"}:
        return None
    out = {"game_id": str(d["header"].get("id") or c["id"]), "season": int(d["header"].get("season", {}).get("year", 0)),
           "kickoff_utc": c.get("date"), "neutral": bool(c.get("neutralSite")),
           "home": str(side["home"]["team"]["id"]), "away": str(side["away"]["team"]["id"]),
           "home_abbr": side["home"]["team"].get("abbreviation"), "away_abbr": side["away"]["team"].get("abbreviation")}
    ok = True
    n = []
    for s, t in (("h", side["home"]), ("a", side["away"])):
        ls = [_num(x) for x in (t.get("linescores") or [])]
        fin = _num({"value": t.get("score")})
        n.append(len(ls))
        if len(ls) < 4 or any(v is None for v in ls) or fin is None:
            ok = False
            ls = (ls + [None] * 4)[:4] if len(ls) < 4 else ls
        for q in range(4):
            out[f"{s}_q{q + 1}"] = ls[q]
        out[f"{s}_ot"] = sum(v for v in ls[4:] if v is not None) if len(ls) > 4 else 0.0
        out[f"{s}_final"] = fin
        if ok and abs(sum(ls) - fin) > 1e-9:
            ok = False
    out["n_periods"] = max(n)
    out["valid"] = ok and n[0] == n[1]
    return out


def _one(path: str):
    try:
        return parse_summary(json.load(open(path)))
    except Exception:  # noqa: BLE001
        return None


def build(dirs: list[Path], out: Path) -> pd.DataFrame:
    files = [str(p) for d in dirs for p in sorted(Path(d).glob("*.json"))]
    with ProcessPoolExecutor(4) as ex:
        rows = [r for r in ex.map(_one, files, chunksize=64) if r]
    df = pd.DataFrame(rows).drop_duplicates("game_id")
    df.to_parquet(out)
    return df


if __name__ == "__main__":
    df = build([Path(x) for x in sys.argv[2:]], Path(sys.argv[1]))
    print(len(df), "games;", int(df.valid.sum()), "valid;", df.groupby("season").size().to_dict())
