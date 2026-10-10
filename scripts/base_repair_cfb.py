#!/usr/bin/env python3
"""Wave 2E: CFB base-projection repair + run-defense reintegration (RETROSPECTIVE_MODEL_REPAIR).

    python3 scripts/base_repair_cfb.py build --cfbd-root <research-data cache v2> \
        --football-state <football_state/2026.json> --preseason-root <checkout holding data/research_cache/preseason>
    python3 scripts/base_repair_cfb.py analyze

`build` adds the two inputs Wave 2D did not have: the pre-registered source-contract replay (B1 with current-season
plays defined as box rush_att + pass_att, the ESPN definition) and the 2026 sanity rows (P0 = the literal live object;
B1 = the same object with current-season rows from the production ESPN log). The P0 / B1 historical components are
the committed Wave-2D study input. `analyze` reads only committed inputs. Protocol:
docs/research/CFB_BASE_MODEL_REPAIR_PROTOCOL.md. Research only.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cfb_edge_finder.ingestion.game_normalization import (  # noqa: E402
    GameNormalizationError,
    away_classification,
    home_classification,
    normalize_cfbd_game,
)
from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest  # noqa: E402
from cfb_edge_finder.modeling.corpus import TeamGameLine, build_team_game_lines  # noqa: E402
from cfb_edge_finder.modeling.leakage import AsOf  # noqa: E402
from cfb_edge_finder.modeling.ratings import fit_fbs_efficiency_ratings  # noqa: E402
from cfb_edge_finder.scripting.gamelog import read_jsonl  # noqa: E402

OUT = ROOT / "data" / "scripting" / "validation" / "base_repair"
SEASONS = tuple(range(2014, 2026))
CAPTURED_AT = datetime(2026, 9, 3, tzinfo=UTC)
CURRENT_SOURCE = "espn_production_log_bridge"


def _rp():
    spec = importlib.util.spec_from_file_location("rush_projection_cfb_b", ROOT / "scripts" / "rush_projection_cfb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- source-contract replay (protocol 6)


def box_attempt_lines(cfbd_root: Path, season: int) -> list[TeamGameLine]:
    """CFBD lines whose `team_plays` is box rush_att + pass_att (the ESPN `plays` definition)."""
    rp = _rp()
    d = cfbd_root / str(season)
    fake_adv = []
    for g in rp.read_gz_json(d / "games_teams.json.gz"):
        for t in g.get("teams") or []:
            st = {s["category"]: s["stat"] for s in t.get("stats") or []}
            try:
                plays = int(float(st["rushingAttempts"]) + float(str(st["completionAttempts"]).split("-")[1]))
            except (KeyError, ValueError, IndexError):
                continue
            fake_adv.append({"gameId": g["id"], "team": t.get("team"), "offense": {"plays": plays}})
    lines, _ = build_team_game_lines(rp.read_gz_json(d / "games.json.gz"), fake_adv, captured_at=CAPTURED_AT)
    return lines


def build_source_contract(cfbd_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """B1 raw margins with the CURRENT season's rows on the box-attempt definition (prior seasons advanced)."""
    rp = _rp()
    adv = {s: rp.season_lines(cfbd_root, s) for s in SEASONS}
    box = {s: box_attempt_lines(cfbd_root, s) for s in SEASONS}
    rows = []
    for s in SEASONS:
        window = [ln for t in range(max(2014, s - 4), s) for ln in adv[t]]
        pairs: dict[str, dict[str, TeamGameLine]] = {}
        for ln in adv[s]:
            pairs.setdefault(ln.source_game_id, {})["home" if ln.is_home else "away"] = ln
        cache: dict[int, Any] = {}
        for gid, pair in sorted(pairs.items(), key=lambda kv: (next(iter(kv[1].values())).week, kv[0])):
            if "home" not in pair or "away" not in pair:
                continue
            home = pair["home"]
            wk = home.week
            if wk not in cache:
                hist = window + [ln for ln in box[s] if ln.as_of.is_strictly_before(AsOf(s, wk))]
                cache[wk] = fit_fbs_efficiency_ratings(hist, AsOf(s, wk)) if hist else None
            if cache[wk] is None:
                continue
            det = rp.deterministic(cache[wk], home)
            rows.append({"game_id": gid, "season": s, "week": wk, "sc_eh": det["eh"], "sc_ea": det["ea"]})
    n_box = {s: sum(1 for ln in box[s] if ln.team_plays) for s in SEASONS}
    n_adv = {s: sum(1 for ln in adv[s] if ln.team_plays) for s in SEASONS}
    return rows, {"box_rows_with_plays": n_box, "advanced_rows_with_plays": n_adv}


# --------------------------------------------------------------------------- 2026: live object vs in-season object


def espn_current_season_lines(
    football_rows: list[Any], schedule_events: list[dict[str, Any]], cfbd_schedule: list[dict[str, Any]]
) -> tuple[list[TeamGameLine], dict[str, Any]]:
    """Production ESPN log -> TeamGameLine (protocol 6 bridge): CFBD id == ESPN id; slugs, week, classification and
    CFBD home designation from the football state's CFBD schedule row; points and box plays from the log."""
    role = {}
    for e in schedule_events:
        for c in e.get("competitors") or []:
            role[(str(e["id"]), str(c.get("team_id")))] = c.get("home_away")
    cfbd = {}
    for raw in cfbd_schedule:
        try:
            g = normalize_cfbd_game(raw, observed_at=CAPTURED_AT)
        except GameNormalizationError:
            continue
        cfbd[str(raw.get("id"))] = (g, home_classification(raw), away_classification(raw))
    by_game: dict[str, list[Any]] = defaultdict(list)
    for r in football_rows:
        by_game[str(r.game_id)].append(r)
    lines, stats = [], defaultdict(int)
    for gid, rows in by_game.items():
        hit = cfbd.get(gid)
        if hit is None:
            stats["no_cfbd_identity"] += 1
            continue
        g, hc, ac = hit
        if "fbs" not in (hc, ac):
            continue
        sides = {role.get((gid, str(r.team_id))): r for r in rows}
        if set(sides) != {"home", "away"}:
            stats["no_orientation"] += 1
            continue
        h, a = sides["home"], sides["away"]
        if None in (h.points_for, a.points_for):
            stats["no_final"] += 1
            continue
        for me, opp, mid, oid, mc, oc, is_home in (
            (h, a, g.home_team_id, g.away_team_id, hc, ac, True),
            (a, h, g.away_team_id, g.home_team_id, ac, hc, False),
        ):
            plays = me.box.get("plays")
            lines.append(
                TeamGameLine(
                    source_game_id=gid,
                    season=g.season,
                    week=g.week_number or 0,
                    is_postseason=False,
                    team_id=mid,
                    opponent_id=oid,
                    team_classification=mc,
                    opponent_classification=oc,
                    is_home=is_home,
                    is_neutral_site=g.neutral_site,
                    team_points=int(me.points_for),
                    opponent_points=int(opp.points_for),
                    team_plays=int(plays) if plays else None,
                    kickoff_utc=me.kickoff(),
                    source=CURRENT_SOURCE,
                    captured_at=CAPTURED_AT,
                )
            )
        stats["games"] += 1
    return lines, dict(stats)


def build_2026(football_state: Path, preseason_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rp = _rp()
    fs = json.loads(football_state.read_text(encoding="utf-8"))
    prior: list[TeamGameLine] = []
    for s in fs["history_seasons"]:
        h = fs["history"][str(s)]
        prior.extend(build_team_game_lines(h["games"], h["advanced"], captured_at=CAPTURED_AT)[0])
    sched = json.loads((ROOT / "data/football/2026/schedule.json").read_text(encoding="utf-8"))["events"]
    log = list(read_jsonl(ROOT / "data/football/2026/team_games.jsonl"))
    cur, stats = espn_current_season_lines(log, sched, fs["schedule_games"])
    talent = rp.load_talent(preseason_root, 2026)
    p0 = GameProjectionCache(prior, talent_by_team=talent)
    b1 = GameProjectionCache(prior + cur, talent_by_team=talent)
    pairs: dict[str, dict[str, TeamGameLine]] = defaultdict(dict)
    for ln in cur:
        pairs[ln.source_game_id]["home" if ln.is_home else "away"] = ln
    rows = []
    for gid, pair in sorted(pairs.items()):
        home = pair["home"]
        if home.team_classification != "fbs" or home.opponent_classification != "fbs":
            continue
        req = GameProjectionRequest(
            game_id=gid,
            home_id=home.team_id,
            away_id=home.opponent_id,
            home_classification="fbs",
            away_classification="fbs",
            is_neutral_site=home.is_neutral_site,
            as_of_season=2026,
            as_of_week=home.week,
            n_simulations=6000,
            seed=0,
        )
        out = {
            "game_id": gid,
            "week": home.week,
            "neutral_site": home.is_neutral_site,
            "home_id": home.team_id,
            "away_id": home.opponent_id,
            "home_points": home.team_points,
            "away_points": home.opponent_points,
        }
        for tag, cache in (("p0", p0), ("b1", b1)):
            c = cache.get_or_build(req)
            p = c.projection
            d = p.to_game_distribution()
            out[f"{tag}_raw"] = p.raw_expected_margin
            out[f"{tag}_c2"] = p.margin_delta
            out[f"{tag}_talent"] = p.talent_margin_delta
            out[f"{tag}_margin"] = p.expected_margin
            out[f"{tag}_total"] = p.expected_total
            out[f"{tag}_sigma"] = float(
                np.sqrt(d.home_sd**2 + d.away_sd**2 - 2 * d.correlation * d.home_sd * d.away_sd)
            )
            out[f"{tag}_training_rows"] = c.training_rows
        out["current_season_rows_before"] = sum(1 for ln in cur if ln.as_of.is_strictly_before(AsOf(2026, home.week)))
        rows.append(out)
    return rows, {"bridge": stats, "current_season_lines": len(cur), "games": len(rows)}


def cmd_build(args: argparse.Namespace) -> int:
    rp = _rp()
    OUT.mkdir(parents=True, exist_ok=True)
    sc_rows, sc_meta = build_source_contract(args.cfbd_root)
    rows26, meta26 = build_2026(args.football_state, args.preseason_root)
    rp.write_jsonl_gz(OUT / "source_contract_2014_2025.jsonl.gz", sc_rows)
    rp.write_jsonl_gz(OUT / "games_2026.jsonl.gz", rows26)
    manifest = {
        "protocol": "docs/research/CFB_BASE_MODEL_REPAIR_PROTOCOL.md",
        "protocol_sha256": sha256(ROOT / "docs/research/CFB_BASE_MODEL_REPAIR_PROTOCOL.md"),
        "wave2d_input_sha256": sha256(ROOT / "data/scripting/validation/rush_projection/p0_games_2014_2025.jsonl.gz"),
        "football_state_sha256": sha256(args.football_state),
        "espn_log_sha256": sha256(ROOT / "data/football/2026/team_games.jsonl"),
        "espn_schedule_sha256": sha256(ROOT / "data/football/2026/schedule.json"),
        "source_contract": sc_meta,
        "y2026": meta26,
        "outputs_sha256": {p.name: sha256(p) for p in sorted(OUT.glob("*.jsonl.gz"))},
    }
    (OUT / "build_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"sc_rows": len(sc_rows), "rows_2026": len(rows26), **meta26}, sort_keys=True))
    return 0


def cmd_analyze(_args: argparse.Namespace) -> int:
    spec = importlib.util.spec_from_file_location(
        "base_repair_cfb_analyze", ROOT / "scripts" / "base_repair_cfb_analyze.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod.main()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--cfbd-root", type=Path, required=True)
    b.add_argument("--football-state", type=Path, required=True)
    b.add_argument("--preseason-root", type=Path, required=True)
    b.set_defaults(func=cmd_build)
    a = sub.add_parser("analyze")
    a.set_defaults(func=cmd_analyze)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
