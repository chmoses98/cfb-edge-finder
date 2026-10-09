#!/usr/bin/env python3
"""Wave 2C (CFB): signal-mechanism study. RESEARCH ONLY. EXPLANATORY ONLY.

docs/research/CFB_SIGNAL_MECHANISM_PROTOCOL.md (pre-registered at 202c9e91).

    python3 scripts/signal_mechanisms_cfb.py build --cfbd-root DIR --sidecar-odds FILE   # -> study inputs
    python3 scripts/signal_mechanisms_cfb.py analyze                                    # -> mechanism_report.json

`build` assembles one row per historical game (the exact Wave-1 join, plus post-game mechanism outcomes and visible
pregame proxies) and one row per completed 2026 game (the Wave-2 feature builder on the Wave-2A-pinned ESPN log), and
writes them under data/scripting/validation/signal_mechanisms/. `analyze` reads only those committed files. Nothing
is written to a prospective store, and no Wave-2 rule, pin or ledger is touched.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cfb_edge_finder.signal_discovery import mechanisms as M  # noqa: E402
from cfb_edge_finder.signal_discovery import wave2 as W  # noqa: E402

OUT = ROOT / "data" / "scripting" / "validation" / "signal_mechanisms"
WAVE1 = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave1"
WAVE2A = ROOT / "data" / "scripting" / "validation" / "signal_discovery_wave2a"
SEASONS = list(range(2014, 2026))
LOG_PIN = "8173fd5ea2b38a61ad429570fbb5cbcaa22cf7b7"  # the Wave-2A main pin
FOOTBALL_LOG = "data/football/2026/team_games.jsonl"
SCHEDULE = "data/football/2026/schedule.json"

#: pregame feature fields kept on each study row (everything else is dropped)
KEEP_FEATURES = (
    "game_id",
    "season",
    "week",
    "season_type",
    "kickoff_utc",
    "home",
    "away",
    "home_id",
    "away_id",
    "neutral_site",
    "conference_game",
    "fcs_involved",
    "home_division",
    "away_division",
    "prior_games_home",
    "prior_games_away",
    "history_games",
    "history_latest_kickoff",
    "adjustment_stable",
    "control_side",
    "control_strength",
    "closeness",
    "pace_claim",
    "scoring_env_claim",
    "defensive_suppression_claim",
    "disruption_sides",
    "possession_environment",
    "tempo_environment",
    "baseline.home_margin",
    "baseline.total_points",
    "baseline.uncertainty_points",
    "def_quality_sum",
    "off_quality_sum",
    "defdiff.rush_success_rate",
)
NET_DIMS = ("sustained_efficiency", "rushing", "passing", "scoring", "finishing", "disruption", "explosiveness")
Q_METRICS = (
    "rush_success_rate",
    "pass_success_rate",
    "success_rate",
    "points_per_game",
    "yards_per_play",
    "plays_per_game",
    "points_per_drive",
    "early_down_success_rate",
    "first_down_rate",
    "third_down_rate",
    "turnovers_per_game",
    "points_per_opportunity",
)


def _gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _default(o):
    try:
        import numpy as np

        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return None if np.isnan(o) else float(o)
        if isinstance(o, np.bool_):
            return bool(o)
    except ImportError:  # pragma: no cover
        pass
    return str(o)


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default)


def write_gz(path: Path, rows: list[dict]) -> str:
    M.assert_not_prospective(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(canonical(r) + "\n" for r in rows).encode()
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as fh:
        fh.write(payload)
    return hashlib.sha256(payload).hexdigest()


def read_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(x) for x in fh]


def git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:  # noqa: BLE001
        return None


def git_show(ref: str, path: str) -> bytes:
    return subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT)


def _runner():
    spec = importlib.util.spec_from_file_location("rsd_cfb", ROOT / "scripts" / "run_signal_discovery_cfb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def slim_features(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: r.get(k) for k in KEEP_FEATURES}
    for d in NET_DIMS:
        out[f"net.{d}"] = r.get(f"net.{d}")
        out[f"unc.{d}"] = r.get(f"unc.{d}")
    for s in ("home", "away"):
        for u in ("off", "def"):
            for m in Q_METRICS:
                out[f"{s}_{u}_q.{m}"] = r.get(f"{s}_{u}_q.{m}")
            out[f"{s}_{u}_se.success_rate"] = r.get(f"{s}_{u}_se.success_rate")
            out[f"{s}_{u}_games.success_rate"] = r.get(f"{s}_{u}_games.success_rate")
    return out


# --------------------------------------------------------------------------- historical


def totals_open(directory: Path) -> dict[str, float]:
    """Median opening total across providers (scores in the lines file are never read)."""
    out = {}
    for name in ("lines_regular.json.gz", "lines_postseason.json.gz"):
        p = directory / name
        if not p.exists():
            continue
        for g in _gz(p):
            vals = []
            for ln in g.get("lines") or []:
                v = ln.get("overUnderOpen")
                if isinstance(v, (int, float)):
                    vals.append(float(v))
            if vals:
                out[str(g["id"])] = float(median(vals))
    return out


def visible_proxies(directory: Path, season: int) -> dict[str, dict[str, Any]]:
    """Per game: pregame Elo (CFBD), AP rank entering the week, record / margin / points to date (prior games only)."""
    games = _gz(directory / "games.json.gz")
    ranks: dict[tuple[str, int], dict[str, int]] = {}
    rk = directory / "rankings.json.gz"
    if rk.exists():
        for wk in _gz(rk):
            for poll in wk.get("polls") or []:
                if poll.get("poll") == "AP Top 25":
                    ranks[(wk.get("seasonType"), int(wk.get("week") or 0))] = {
                        str(x["teamId"]): int(x["rank"]) for x in poll.get("ranks") or []
                    }
    reg_weeks = sorted(w for (st, w) in ranks if st == "regular")
    hist: dict[str, list[tuple[str, float, float]]] = defaultdict(list)  # team -> (kickoff, margin, points)
    done = sorted(
        (g for g in games if g.get("completed") and g.get("homePoints") is not None), key=lambda g: g["startDate"]
    )
    out = {}
    for g in sorted(games, key=lambda g: g.get("startDate") or ""):
        gid, ko = str(g["id"]), g.get("startDate") or ""
        st, wk = g.get("seasonType"), int(g.get("week") or 0)
        poll = (
            ranks.get((st, wk)) if st == "regular" else (ranks.get(("regular", reg_weeks[-1])) if reg_weeks else None)
        )
        row = {"elo_home": g.get("homePregameElo"), "elo_away": g.get("awayPregameElo")}
        for side in ("home", "away"):
            tid = str(g.get(f"{side}Id"))
            prior = [x for x in hist[tid] if x[0] < ko]
            row[f"ap_{side}"] = (poll or {}).get(tid)
            row[f"wins_{side}"] = sum(1 for x in prior if x[1] > 0)
            row[f"games_{side}"] = len(prior)
            row[f"margin_td_{side}"] = (sum(x[1] for x in prior) / len(prior)) if prior else None
            row[f"ppg_td_{side}"] = (sum(x[2] for x in prior) / len(prior)) if prior else None
            row[f"last_margin_{side}"] = prior[-1][1] if prior else None
        out[gid] = row
        if g.get("completed") and g.get("homePoints") is not None:
            hp, ap = float(g["homePoints"]), float(g["awayPoints"])
            hist[str(g["homeId"])].append((ko, hp - ap, hp))
            hist[str(g["awayId"])].append((ko, ap - hp, ap))
    del done
    return out


def mechanism_outcomes(directory: Path, team_rows) -> dict[str, dict[str, Any]]:
    """Post-game channels per game, home-minus-away unless noted. Read only after membership is fixed."""
    games = {str(g["id"]): g for g in _gz(directory / "games.json.gz")}
    drives = _gz(directory / "drives_regular.json.gz") + (
        _gz(directory / "drives_postseason.json.gz") if (directory / "drives_postseason.json.gz").exists() else []
    )
    by = defaultdict(list)
    for d in drives:
        if d.get("gameId") is not None:
            by[(str(d["gameId"]), "home" if d.get("isHomeOffense") else "away")].append(d)
    tg = defaultdict(dict)
    for r in team_rows:
        tg[r.game_id][r.team_id] = r
    out = {}
    for gid, g in games.items():
        if not g.get("completed"):
            continue
        row: dict[str, Any] = {}
        qm = M.quarter_margins(g.get("homeLineScores"), g.get("awayLineScores"))
        if qm:
            row.update({f"g.{k}": v for k, v in qm.items()})
        hl = g.get("homeLineScores") or []
        al = g.get("awayLineScores") or []
        if len(hl) >= 4 and len(al) >= 4 and None not in hl[:4] + al[:4]:
            row["g.home_q4_pts"], row["g.away_q4_pts"] = float(hl[3]), float(al[3])
            row["g.home_thru_q3"], row["g.away_thru_q3"] = float(sum(hl[:3])), float(sum(al[:3]))
            row["g.home_h1_pts"], row["g.away_h1_pts"] = float(sum(hl[:2])), float(sum(al[:2]))
        dv = {s: M.drive_outcomes(by.get((gid, s), [])) for s in ("home", "away")}
        if dv["home"].get("drives") and dv["away"].get("drives"):
            for s in ("home", "away"):
                d = dv[s]
                row[f"g.{s}_drives"] = d["drives"]
                row[f"g.{s}_ppd"] = d["points"] / d["drives"]
                row[f"g.{s}_empty_rate"] = d["empty"] / d["drives"]
                row[f"g.{s}_three_out_rate"] = d["three_out"] / d["drives"]
                row[f"g.{s}_rz_trips"] = d["rz_trips"]
                row[f"g.{s}_rz_td_rate"] = d["rz_td"] / d["rz_trips"] if d["rz_trips"] else None
                row[f"g.{s}_long_drives"] = d["long_drives"]
                row[f"g.{s}_turnover_drives"] = d["turnover_drives"]
            row["g.empty_rate_diff"] = row["g.home_empty_rate"] - row["g.away_empty_rate"]
            row["g.ppd_diff"] = row["g.home_ppd"] - row["g.away_ppd"]
            row["g.three_out_diff"] = row["g.home_three_out_rate"] - row["g.away_three_out_rate"]
            row["g.rz_trips_diff"] = row["g.home_rz_trips"] - row["g.away_rz_trips"]
            row["g.long_drives_diff"] = row["g.home_long_drives"] - row["g.away_long_drives"]
            row["g.drives_total"] = row["g.home_drives"] + row["g.away_drives"]
        sides = tg.get(gid) or {}
        h, a = sides.get(str(g.get("homeId"))), sides.get(str(g.get("awayId")))
        if h is not None and a is not None:
            for s, t in (("home", h), ("away", a)):
                b, p = t.box or {}, t.pbp or {}
                row[f"g.{s}_plays"] = b.get("plays")
                row[f"g.{s}_first_downs"] = b.get("first_downs")
                row[f"g.{s}_poss_sec"] = b.get("possession_seconds")
                row[f"g.{s}_turnovers"] = b.get("turnovers")
                row[f"g.{s}_third_rate"] = (b["third_conv"] / b["third_att"]) if b.get("third_att") else None
                row[f"g.{s}_ypp"] = (
                    (b["total_yards"] / b["plays"]) if b.get("plays") and b.get("total_yards") is not None else None
                )
                row[f"g.{s}_rush_att"] = b.get("rush_att")
                row[f"g.{s}_pass_att"] = b.get("pass_att")
                row[f"g.{s}_sr"] = (
                    (p["success_plays"] / p["scrim_plays"])
                    if p.get("scrim_plays") and p.get("success_plays") is not None
                    else None
                )
                row[f"g.{s}_rush_sr"] = (
                    (p["rush_success"] / p["rush_plays"])
                    if p.get("rush_plays") and p.get("rush_success") is not None
                    else None
                )
                row[f"g.{s}_pass_sr"] = (
                    (p["pass_success"] / p["pass_plays"])
                    if p.get("pass_plays") and p.get("pass_success") is not None
                    else None
                )
                row[f"g.{s}_scoring_opps"] = p.get("scoring_opps")
                row[f"g.{s}_ints"] = b.get("ints_thrown")
                row[f"g.{s}_fumbles"] = b.get("fumbles_lost")
            for k in (
                "plays",
                "first_downs",
                "poss_sec",
                "turnovers",
                "third_rate",
                "ypp",
                "sr",
                "rush_sr",
                "pass_sr",
                "scoring_opps",
                "rush_att",
                "pass_att",
                "ints",
                "fumbles",
            ):
                hv, av = row.get(f"g.home_{k}"), row.get(f"g.away_{k}")
                row[f"g.{k}_diff"] = (hv - av) if hv is not None and av is not None else None
            hp_, ap_ = row.get("g.home_plays"), row.get("g.away_plays")
            row["g.plays_total"] = (hp_ + ap_) if hp_ is not None and ap_ is not None else None
        # CFBD advanced 'explosiveness' is a PPA magnitude, not a count: labelled where used
        out[gid] = row
    return out


def build_historical(cfbd_root: Path) -> tuple[list[dict], dict]:
    from cfb_edge_finder.archetype_research.replay import load_cfbd_season

    run = _runner()
    cfbd = {str(s): str(cfbd_root / str(s)) for s in SEASONS}
    rows, excl = run._join(WAVE1 / "features", cfbd, SEASONS)
    # integrity anchor
    sel = []
    for r in rows:
        side = M.frozen_side(r)
        if side is None or r["m.spread_home"] is None:
            continue
        sel.append(M.sign(side) * r["home_ats_resid"])
    rec = M.ats_record(sel)
    if not (
        rec["n"] == M.ANCHOR["n"]
        and abs(rec["mean"] - M.ANCHOR["mean"]) < 1e-9
        and (rec["w"], rec["l"], rec["p"]) == (M.ANCHOR["w"], M.ANCHOR["l"], M.ANCHOR["p"])
    ):
        raise M.MechanismIntegrityError(f"Wave-1 / PROS-001 anchor not reproduced: {rec}")
    extra, vis, topen = {}, {}, {}
    for s in SEASONS:
        d = cfbd_root / str(s)
        season = load_cfbd_season(d)
        extra.update(mechanism_outcomes(d, season["rows"]))
        vis.update(visible_proxies(d, s))
        topen.update(totals_open(d))
    out = []
    for r in rows:
        row = slim_features(r)
        for k in ("m.spread_home", "m.spread_open_home", "m.total", "m.ml_home_novig", "m.dispersion_flag"):
            row[k] = r.get(k)
        row["m.total_open"] = topen.get(r["game_id"])
        for k in (
            "home_points",
            "away_points",
            "home_margin",
            "total_points",
            "overtime",
            "home_1h",
            "away_1h",
            "home_plays",
            "away_plays",
        ):
            row[f"o.{k}"] = r.get(f"o.{k}")
        row.update(extra.get(r["game_id"], {}))
        row.update({f"v.{k}": v for k, v in (vis.get(r["game_id"]) or {}).items()})
        out.append(row)
    return out, {"exclusions": dict(excl), "anchor": rec}


# --------------------------------------------------------------------------- 2026


def build_2026(sidecar_odds: Path | None) -> tuple[list[dict], dict]:
    from cfb_edge_finder.scripting.football import LeagueFitCache
    from cfb_edge_finder.scripting.gamelog import TeamGame
    from cfb_edge_finder.signal_discovery import wave2_cycle as C

    log = [
        TeamGame.from_dict(json.loads(x)) for x in git_show(LOG_PIN, FOOTBALL_LOG).decode().splitlines() if x.strip()
    ]
    events = json.loads(git_show(LOG_PIN, SCHEDULE))["events"]
    blind = json.loads(json.dumps(events))
    scores = {}
    for e in blind:
        for c in e.get("competitors") or []:
            scores[(str(e["id"]), str(c["team_id"]))] = c.pop("score", None)
    member = {
        r["game_id"]: r
        for r in read_gz(WAVE2A / "replay_membership_2026.jsonl.gz")
        if r["signal_id"] == W.PROS_001 and r["record"] == "OBSERVATION"
    }
    entries = {}
    for r in read_gz(WAVE2A / "replay_rows_2026.jsonl.gz"):
        if r["record"] == "ENTRY" and r.get("market_implied_margin") is not None:
            entries[(r["signal_id"], r["game_id"])] = r
    controls = json.loads((WAVE2A / "replay_report_2026.json").read_text()).get("control_check") or {}
    odds = latest_odds(sidecar_odds) if sidecar_odds else {}
    by_game = defaultdict(list)
    for t in log:
        by_game[t.game_id].append(t)
    cache = LeagueFitCache()
    rows, check = [], {"compared": 0, "mismatch": 0, "mismatch_ids": []}
    for e in sorted(blind, key=lambda e: (e.get("date") or "", str(e["id"]))):
        gid = str(e["id"])
        if not e.get("completed") or (e.get("date") or "") >= W.ACTIVATION_UTC.replace("Z", "") or gid not in by_game:
            continue
        ident = C.identity({"content": {"event_id": gid}}, blind)
        if ident is None:
            continue
        kickoff = e["date"] if e["date"].endswith("Z") else e["date"] + "Z"
        feat = C.feature_for(ident, kickoff, 2026, tuple(log), blind, cache)
        row = slim_features(feat)
        row["game_id"] = gid
        row["season"] = 2026
        row["kickoff_utc"] = kickoff
        m = member.get(gid)
        if m is not None:
            row["week"] = m.get("week")
            mv = (m.get("signal") or {}).get("value")
            fv = feat.get(W.DSC001_FEATURE)
            check["compared"] += 1
            if (mv is None) != (fv is None) or (mv is not None and abs(mv - fv) > 1e-9):
                check["mismatch"] += 1
                check["mismatch_ids"].append(gid)
        hid, aid = ident["teams"]["home"]["team_id"], ident["teams"]["away"]["team_id"]
        hs, as_ = scores.get((gid, hid)), scores.get((gid, aid))
        if hs not in (None, "") and as_ not in (None, ""):
            row["o.home_points"], row["o.away_points"] = float(hs), float(as_)
            row["o.home_margin"] = row["o.home_points"] - row["o.away_points"]
            row["o.total_points"] = row["o.home_points"] + row["o.away_points"]
        sides = {t.team_id: t for t in by_game[gid]}
        for s, tid in (("home", hid), ("away", aid)):
            t = sides.get(tid)
            if t is None:
                continue
            b, p = t.box or {}, t.pbp or {}
            row[f"g.{s}_plays"] = b.get("plays")
            row[f"g.{s}_rush_sr"] = (
                (p["rush_success"] / p["rush_plays"])
                if p.get("rush_plays") and p.get("rush_success") is not None
                else None
            )
            row[f"g.{s}_pass_sr"] = (
                (p["pass_success"] / p["pass_plays"])
                if p.get("pass_plays") and p.get("pass_success") is not None
                else None
            )
            row[f"g.{s}_sr"] = (
                (p["success_plays"] / p["scrim_plays"])
                if p.get("scrim_plays") and p.get("success_plays") is not None
                else None
            )
            row[f"g.{s}_ppd"] = (p["drive_points"] / p["drives"]) if p.get("drives") else None
        hp_, ap_ = row.get("g.home_plays"), row.get("g.away_plays")
        row["g.plays_total"] = (hp_ + ap_) if hp_ is not None and ap_ is not None else None
        o = odds.get(gid)
        if o:
            row["m.dk_spread_home"], row["m.dk_total"], row["m.dk_lead_hours"] = (
                o["spread"],
                o["total"],
                o["lead_hours"],
            )
        for sid, key in ((W.PROS_001, "k.pros001"), (W.PROS_002, "k.pros002")):
            en = entries.get((sid, gid))
            if en is not None:
                row[f"{key}_implied_margin"] = en["market_implied_margin"]
                row[f"{key}_minutes_before"] = en.get("minutes_before")
        rows.append(row)
    if check["mismatch"]:
        raise M.MechanismIntegrityError(f"2026 frozen feature differs from the Wave-2A membership: {check}")
    return rows, {"feature_check": check, "control_check_wave2a": controls, "odds_games": len(odds)}


def latest_odds(path: Path) -> dict[str, dict[str, Any]]:
    """Last DraftKings snapshot strictly before kickoff per game (home-perspective spread)."""
    best: dict[str, dict[str, Any]] = {}
    with open(path) as fh:
        for line in fh:
            d = json.loads(line)
            books = (d.get("parsed") or {}).get("books") or []
            if d.get("kind") != "espn_odds" or not books or not d.get("kickoff_utc"):
                continue
            if datetime.fromisoformat(d["fetched_at"]) >= datetime.fromisoformat(d["kickoff_utc"]):
                continue
            b = books[0]
            if b.get("spread") is None and b.get("over_under") is None:
                continue
            g = str(d["game_id"])
            if g not in best or d["fetched_at"] > best[g]["fetched_at"]:
                best[g] = {
                    "fetched_at": d["fetched_at"],
                    "spread": b.get("spread"),
                    "total": b.get("over_under"),
                    "lead_hours": d.get("lead_hours"),
                    "provider": b.get("provider"),
                }
    return best


def cfbd_2026_parity(cfbd_root: Path) -> dict[str, Any]:
    """Identity / score parity on the 2026 games present in both CFBD and the pinned ESPN schedule."""
    events = {str(e["id"]): e for e in json.loads(git_show(LOG_PIN, SCHEDULE))["events"]}
    out = {
        "cfbd_games": 0,
        "overlap_ids": 0,
        "completed_both": 0,
        "score_match": 0,
        "orientation_match": 0,
        "neutral_match": 0,
        "rows": [],
    }
    games = _gz(cfbd_root / "2026" / "games.json.gz")
    out["cfbd_games"] = len(games)
    for g in games:
        e = events.get(str(g["id"]))
        if e is None:
            continue
        out["overlap_ids"] += 1
        sides = {c["home_away"]: c for c in e.get("competitors") or []}
        orient = str(sides.get("home", {}).get("team_id")) == str(g.get("homeId")) and str(
            sides.get("away", {}).get("team_id")
        ) == str(g.get("awayId"))
        out["orientation_match"] += 1 if orient else 0
        out["neutral_match"] += 1 if bool(e.get("neutral_site")) == bool(g.get("neutralSite")) else 0
        if g.get("completed") and e.get("completed"):
            out["completed_both"] += 1
            ok = (
                orient
                and str(sides["home"].get("score")) == str(g.get("homePoints"))
                and str(sides["away"].get("score")) == str(g.get("awayPoints"))
            )
            out["score_match"] += 1 if ok else 0
            out["rows"].append(
                {
                    "game_id": str(g["id"]),
                    "cfbd": [g.get("homePoints"), g.get("awayPoints")],
                    "espn": [sides["home"].get("score"), sides["away"].get("score")],
                    "match": ok,
                }
            )
    return out


def cmd_build(a) -> int:
    root = Path(a.cfbd_root)
    hist, hmeta = build_historical(root)
    fits = M.fit_residualisers(hist)
    r26, meta26 = build_2026(Path(a.sidecar_odds) if a.sidecar_odds else None)
    parity = cfbd_2026_parity(root)
    OUT.mkdir(parents=True, exist_ok=True)
    hashes = {
        "games_2014_2025": write_gz(OUT / "games_2014_2025.jsonl.gz", hist),
        "games_2026": write_gz(OUT / "games_2026.jsonl.gz", r26),
    }
    manifest = {
        "schema": "cfb_signal_mechanisms_build/1.0.0",
        "version": M.VERSION,
        "code_sha": git_sha(),
        "protocol": {"commit": M.PROTOCOL_COMMIT, "sha256": M.PROTOCOL_SHA256},
        "cfbd_cache": "research-data@292f3aac data/research_cache/v2",
        "cfbd_digests": {
            s: hashlib.sha256((root / str(s) / "games.json.gz").read_bytes()).hexdigest() for s in SEASONS
        },
        "log_pin": LOG_PIN,
        "log_sha256": hashlib.sha256(git_show(LOG_PIN, FOOTBALL_LOG)).hexdigest(),
        "schedule_sha256": hashlib.sha256(git_show(LOG_PIN, SCHEDULE)).hexdigest(),
        "sidecar_odds_sha256": hashlib.sha256(Path(a.sidecar_odds).read_bytes()).hexdigest()
        if a.sidecar_odds
        else None,
        "historical": hmeta,
        "residualisers": fits,
        "rows_2026": meta26,
        "cfbd_espn_parity_2026": parity,
        "hashes": hashes,
        "n_hist": len(hist),
        "n_2026": len(r26),
    }
    (OUT / "build_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True, default=_default) + "\n")
    print(
        json.dumps(
            {k: manifest[k] for k in ("n_hist", "n_2026", "historical", "rows_2026")}, indent=1, default=_default
        )[:3000]
    )
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--cfbd-root", required=True)
    b.add_argument("--sidecar-odds", default=None)
    sub.add_parser("analyze")
    a = ap.parse_args(argv)
    if a.cmd == "analyze":
        spec = importlib.util.spec_from_file_location(
            "cfb_mech_analyze", ROOT / "scripts" / "signal_mechanisms_cfb_analyze.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.main()
    return cmd_build(a)


if __name__ == "__main__":
    raise SystemExit(main())
