#!/usr/bin/env python3
"""Wave 2D Track A: CFB rush projection integration (RETROSPECTIVE_MODEL_INTEGRATION).

    python3 scripts/rush_projection_cfb.py build --cfbd-root <research-data cache v2> \
        --football-state <football_state/2026.json> --preseason-root <checkout holding data/research_cache/preseason>
    python3 scripts/rush_projection_cfb.py analyze

`build` reconstructs the CURRENT production projection (P0-LIVE: 4 prior seasons, no in-season rows; P0-INSEASON:
the same plus strictly-prior in-season rows) with production code only, attaches the frozen Wave-2C pregame rush
features and the final scores, and writes the study input. `analyze` reads only the committed study input and
writes the integration report (walk-forward coefficients, gates, decision). Protocol:
docs/research/CFB_RUSH_PROJECTION_INTEGRATION_PROTOCOL.md. Research only; production imports nothing from here.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
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
from cfb_edge_finder.modeling.margin_calibration import fit_linear_margin  # noqa: E402
from cfb_edge_finder.modeling.qb_continuity import QBContinuityState, uncertainty_multiplier  # noqa: E402
from cfb_edge_finder.modeling.ratings import fit_fbs_efficiency_ratings  # noqa: E402
from cfb_edge_finder.modeling.score_model import (  # noqa: E402
    DEFAULT_RESIDUAL_SCALE,
    EARLY_SEASON_UNCERTAINTY_SCALE,
    effective_team_rating,
    project_game,
)
from cfb_edge_finder.modeling.talent_prior import talent_margin_delta  # noqa: E402
from cfb_edge_finder.signal_discovery import rush_projection as R  # noqa: E402

OUT = ROOT / "data" / "scripting" / "validation" / "rush_projection"
WAVE2C = ROOT / "data" / "scripting" / "validation" / "signal_mechanisms"
SEASONS = tuple(range(2014, 2026))
CAPTURED_AT = datetime(2026, 9, 3, tzinfo=UTC)
_DUMMY_POOL = np.zeros((1, 2))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_gz_json(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_jsonl_gz(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        for r in rows:
            gz.write((json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))


# --------------------------------------------------------------------------- production P0, deterministic part


def season_lines(cfbd_root: Path, season: int) -> list[TeamGameLine]:
    d = cfbd_root / str(season)
    advanced = read_gz_json(d / "advanced_regular.json.gz")
    post = d / "advanced_postseason.json.gz"
    if post.exists():
        advanced = advanced + read_gz_json(post)
    lines, _ = build_team_game_lines(read_gz_json(d / "games.json.gz"), advanced, captured_at=CAPTURED_AT)
    return lines


def deterministic(ratings, home: TeamGameLine) -> dict[str, float]:
    """Production project_game's expected points (the simulation does not touch them) plus model components."""
    proj = project_game(
        home_id=home.team_id,
        away_id=home.opponent_id,
        home_classification=home.team_classification,
        away_classification=home.opponent_classification,
        is_neutral_site=home.is_neutral_site,
        ratings=ratings,
        prior_season_ratings=None,
        residual_pool=_DUMMY_POOL,
        home_percent_passing_ppa=None,
        away_percent_passing_ppa=None,
        n_simulations=1,
        seed=0,
    )
    out = {
        "eh": proj.expected_home_points,
        "ea": proj.expected_away_points,
        "wh": proj.home_carryover_weight,
        "wa": proj.away_carryover_weight,
        "plays": ratings.expected_plays_for(home.team_id, home.opponent_id)
        + ratings.expected_plays_for(home.opponent_id, home.team_id),
    }
    if home.team_classification == "fbs" and home.opponent_classification == "fbs":
        hb = effective_team_rating(home.team_id, ratings, None)
        ab = effective_team_rating(home.opponent_id, ratings, None)
        out["off_diff"] = hb.offense - ab.offense
        out["def_diff"] = hb.defense - ab.defense
    return out


def load_talent(preseason_root: Path, season: int) -> dict[str, float]:
    import research_scan_and_capture as rsc  # production loader, exactly as live

    talent, _state = rsc._load_talent_by_team(preseason_root, season)  # noqa: SLF001
    return talent


def build_historical(cfbd_root: Path, preseason_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lines = {s: season_lines(cfbd_root, s) for s in SEASONS}
    games: dict[int, dict[str, dict[str, TeamGameLine]]] = {}
    for s in SEASONS:
        by_game: dict[str, dict[str, TeamGameLine]] = {}
        for ln in lines[s]:
            by_game.setdefault(ln.source_game_id, {})["home" if ln.is_home else "away"] = ln
        games[s] = {g: p for g, p in by_game.items() if "home" in p and "away" in p}

    rows: list[dict[str, Any]] = []
    for s in SEASONS:
        window = [ln for t in range(max(2014, s - 4), s) for ln in lines[t]]
        live_ratings = fit_fbs_efficiency_ratings(window, AsOf(s, 0)) if window else None
        inseason_cache: dict[int, Any] = {}
        talent = load_talent(preseason_root, s) if s in R.TALENT_SEASONS else {}
        for gid, pair in sorted(games[s].items(), key=lambda kv: (kv[1]["home"].week, kv[0])):
            home = pair["home"]
            wk = home.week
            if wk not in inseason_cache:
                hist = window + [ln for ln in lines[s] if ln.as_of.is_strictly_before(AsOf(s, wk))]
                inseason_cache[wk] = fit_fbs_efficiency_ratings(hist, AsOf(s, wk)) if hist else None
            fbs = home.team_classification == "fbs" and home.opponent_classification == "fbs"
            row: dict[str, Any] = {
                "game_id": gid,
                "season": s,
                "week": wk,
                "postseason": home.is_postseason,
                "neutral_site": home.is_neutral_site,
                "fbs_vs_fbs": fbs,
                "home_id": home.team_id,
                "away_id": home.opponent_id,
                "home_points": home.team_points,
                "away_points": home.opponent_points,
                "talent_delta": (
                    talent_margin_delta(talent.get(home.team_id), talent.get(home.opponent_id)) if fbs else 0.0
                ),
                "talent_active": bool(talent),
            }
            for tag, ratings in (("live", live_ratings), ("in", inseason_cache[wk])):
                if ratings is None:
                    continue
                for k, v in deterministic(ratings, home).items():
                    row[f"{tag}_{k}"] = v
            rows.append(row)

    # C.2 linear correction per season: fit_linear_margin on P0-INSEASON raw FBS-vs-FBS margins of the four prior
    # seasons (from 2015), exactly how the frozen live artifact was produced; residual covariance for sigma likewise.
    by_season: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_season[r["season"]].append(r)
    corrections: dict[int, dict[str, Any]] = {}
    sigma_inputs: dict[int, dict[str, float]] = {}
    for s in SEASONS:
        prior = [r for t in range(max(2015, s - 4), s) for r in by_season[t] if r["fbs_vs_fbs"] and "in_eh" in r]
        params = fit_linear_margin(
            np.array([r["in_eh"] - r["in_ea"] for r in prior]),
            np.array([r["home_points"] - r["away_points"] for r in prior]),
        )
        corrections[s] = {"a": params.a, "b": params.b, "identity": params.is_identity_fallback, "n": len(prior)}
        pool = [r for t in range(max(2014, s - 4), s) for r in by_season[t] if r["fbs_vs_fbs"] and "in_eh" in r]
        if pool:
            rh = np.array([r["home_points"] - r["in_eh"] for r in pool])
            ra = np.array([r["away_points"] - r["in_ea"] for r in pool])
            sigma_inputs[s] = {
                "var_h": float(rh.var(ddof=1)),
                "var_a": float(ra.var(ddof=1)),
                "cov": float(np.cov(rh, ra, ddof=1)[0, 1]),
                "n": len(pool),
            }
    for r in rows:
        c = corrections[r["season"]]
        r["c2_a"], r["c2_b"], r["c2_identity"] = c["a"], c["b"], c["identity"]
        si = sigma_inputs.get(r["season"])
        if not si:
            continue
        for tag in ("live", "in"):
            if f"{tag}_wh" not in r:
                continue
            # production project_game scales: QB unknown x early-season x residual scale (FBS-vs-FBS: no FCS term)
            s_h, s_a = (
                uncertainty_multiplier(QBContinuityState.UNKNOWN)
                * (1 + EARLY_SEASON_UNCERTAINTY_SCALE * (1 - r[f"{tag}_{w}"]))
                * DEFAULT_RESIDUAL_SCALE
                for w in ("wh", "wa")
            )
            r[f"{tag}_sigma"] = R.margin_sigma(si["var_h"], si["var_a"], si["cov"], s_h, s_a)
    return rows, {"corrections": corrections, "sigma_inputs": sigma_inputs}


def attach_wave2c(rows: list[dict[str, Any]], wave2c_rows: list[dict[str, Any]]) -> dict[str, int]:
    """Join the frozen Wave-2C features (pregame only) and the descriptive close; verify orientation by score."""
    by_id = {str(w["game_id"]): w for w in wave2c_rows}
    stats = {"joined": 0, "orientation_mismatch": 0}
    for r in rows:
        w = by_id.get(r["game_id"])
        r["in_wave2c"] = w is not None
        if w is None:
            continue
        if (w.get("o.home_points"), w.get("o.away_points")) != (r["home_points"], r["away_points"]):
            stats["orientation_mismatch"] += 1
            raise R.IntegrationError(f"orientation/score mismatch for game {r['game_id']}")
        stats["joined"] += 1
        r.update(R.rush_features(w))
        r["descriptive_close_spread_home"] = w.get("m.spread_home")
        r["wave2c_week"] = w.get("week")
    return stats


# --------------------------------------------------------------------------- 2026: the literal live object


def build_2026(football_state: Path, preseason_root: Path, wave2c_2026: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fs = json.loads(football_state.read_text(encoding="utf-8"))
    lines: list[TeamGameLine] = []
    for s in fs["history_seasons"]:
        h = fs["history"][str(s)]
        part, _ = build_team_game_lines(h["games"], h["advanced"], captured_at=CAPTURED_AT)
        lines.extend(part)
    talent = load_talent(preseason_root, 2026)
    cache = GameProjectionCache(lines, talent_by_team=talent)
    schedule: dict[str, tuple[Any, str | None, str | None]] = {}
    for raw in fs["schedule_games"]:
        try:
            g = normalize_cfbd_game(raw, observed_at=CAPTURED_AT)
        except GameNormalizationError:
            continue
        schedule[str(raw.get("id"))] = (g, home_classification(raw), away_classification(raw))
    out = []
    for w in wave2c_2026:
        gid = str(w["game_id"])
        hit = schedule.get(gid)
        margin = w.get("o.home_margin")
        if hit is None or margin is None:
            continue
        g, hc, ac = hit
        if hc != "fbs" or ac != "fbs":
            continue
        req = GameProjectionRequest(
            game_id=g.game_id,
            home_id=g.home_team_id,
            away_id=g.away_team_id,
            home_classification=hc,
            away_classification=ac,
            is_neutral_site=g.neutral_site,
            as_of_season=2026,
            as_of_week=g.week_number or 0,
            n_simulations=6000,
            seed=0,
        )
        p = cache.get_or_build(req).projection
        dist = p.to_game_distribution()
        sd = float(np.sqrt(dist.home_sd**2 + dist.away_sd**2 - 2 * dist.correlation * dist.home_sd * dist.away_sd))
        row = {
            "game_id": gid,
            "season": 2026,
            "week": g.week_number,
            "neutral_site": g.neutral_site,
            "fbs_vs_fbs": True,
            "home_id": g.home_team_id,
            "away_id": g.away_team_id,
            "home_points": w.get("o.home_points"),
            "away_points": w.get("o.away_points"),
            "p0_live_margin": p.expected_margin,
            "p0_live_total": p.expected_total,
            "p0_live_raw_margin": p.raw_expected_margin,
            "c2_delta": p.margin_delta,
            "talent_delta": p.talent_margin_delta,
            "p0_live_sigma": sd,
            "kalshi_pros001_implied_margin_home": None,
        }
        row.update(R.rush_features(w))
        out.append(row)
    return out


def live_reproduction(football_state: Path, preseason_root: Path, observations: Path) -> dict[str, Any]:
    """Re-price live 0.5.0 moneyline observations with the rebuilt live object; every one must match exactly."""
    from cfb_edge_finder.projections.distribution import price_market
    from cfb_edge_finder.schemas.common import MarketFamily, Side

    fs = json.loads(football_state.read_text(encoding="utf-8"))
    lines: list[TeamGameLine] = []
    for s in fs["history_seasons"]:
        h = fs["history"][str(s)]
        lines.extend(build_team_game_lines(h["games"], h["advanced"], captured_at=CAPTURED_AT)[0])
    cache = GameProjectionCache(lines, talent_by_team=load_talent(preseason_root, 2026))
    games = {}
    for raw in fs["schedule_games"]:
        try:
            g = normalize_cfbd_game(raw, observed_at=CAPTURED_AT)
        except GameNormalizationError:
            continue
        games[g.game_id] = (g, home_classification(raw), away_classification(raw))
    checked, worst = [], 0.0
    for line in observations.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        o = json.loads(line)["observation"]
        mv = (o.get("model_version") or {}).get("model_version")
        if (
            o.get("family") != "moneyline"
            or o.get("pricing_status") != "model_priced"
            or mv != "0.5.0-early-season-talent-prior"
        ):
            continue
        g, hc, ac = games[o["game_id"]]
        req = GameProjectionRequest(
            game_id=g.game_id,
            home_id=g.home_team_id,
            away_id=g.away_team_id,
            home_classification=hc,
            away_classification=ac,
            is_neutral_site=g.neutral_site,
            as_of_season=g.season,
            as_of_week=g.week_number or 0,
            n_simulations=6000,
            seed=0,
        )
        dist = cache.get_or_build(req).projection.to_game_distribution()
        side = Side.HOME if o["team"] == "home" else Side.AWAY
        p = price_market(dist, MarketFamily.MONEYLINE, side)
        worst = max(worst, abs(p - o["model_probability"]))
        checked.append(o["game_id"])
    return {
        "observations_file_sha256": sha256(observations),
        "moneyline_rows_checked": len(checked),
        "distinct_games": len(set(checked)),
        "max_abs_probability_difference": worst,
        "exact_to_1e-12": worst <= 1e-12,
    }


def cmd_build(args: argparse.Namespace) -> int:
    wave2c_hist = read_jsonl_gz(WAVE2C / "games_2014_2025.jsonl.gz")
    wave2c_2026 = read_jsonl_gz(WAVE2C / "games_2026.jsonl.gz")
    rows, fitted = build_historical(args.cfbd_root, args.preseason_root)
    join = attach_wave2c(rows, wave2c_hist)
    rows_2026 = build_2026(args.football_state, args.preseason_root, wave2c_2026)
    OUT.mkdir(parents=True, exist_ok=True)
    write_jsonl_gz(OUT / "p0_games_2014_2025.jsonl.gz", rows)
    write_jsonl_gz(OUT / "p0_games_2026.jsonl.gz", rows_2026)
    digests = {}
    for s in SEASONS:
        for name in ("games.json.gz", "advanced_regular.json.gz", "advanced_postseason.json.gz"):
            p = args.cfbd_root / str(s) / name
            if p.exists():
                digests[f"{s}/{name}"] = sha256(p)
    manifest = {
        "version": R.VERSION,
        "label": R.LABEL,
        "protocol_commit": R.PROTOCOL_COMMIT,
        "protocol_sha256": R.PROTOCOL_SHA256,
        "starting_main": R.STARTING_MAIN,
        "cfbd_cache_sha256": digests,
        "football_state_sha256": sha256(args.football_state),
        "preseason_cache_sha256": {
            p.name: sha256(p) for p in sorted((args.preseason_root / "data/research_cache/preseason").glob("*.json"))
        },
        "wave2c_inputs_sha256": {
            "games_2014_2025.jsonl.gz": sha256(WAVE2C / "games_2014_2025.jsonl.gz"),
            "games_2026.jsonl.gz": sha256(WAVE2C / "games_2026.jsonl.gz"),
        },
        "c2_corrections_by_season": {str(k): v for k, v in fitted["corrections"].items()},
        "sigma_inputs_by_season": {str(k): v for k, v in fitted["sigma_inputs"].items()},
        "wave2c_join": join,
        "rows_2014_2025": len(rows),
        "rows_2026": len(rows_2026),
        "live_reproduction": (
            live_reproduction(args.football_state, args.preseason_root, args.live_observations)
            if args.live_observations
            else None
        ),
        "outputs_sha256": {
            "p0_games_2014_2025.jsonl.gz": sha256(OUT / "p0_games_2014_2025.jsonl.gz"),
            "p0_games_2026.jsonl.gz": sha256(OUT / "p0_games_2026.jsonl.gz"),
        },
    }
    (OUT / "build_manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "rows_2026": len(rows_2026), "join": join}, sort_keys=True))
    return 0


def cmd_analyze(_args: argparse.Namespace) -> int:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rush_projection_cfb_analyze", ROOT / "scripts" / "rush_projection_cfb_analyze.py"
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
    b.add_argument("--live-observations", type=Path, default=None, help="research-data observations jsonl (0.5.0)")
    b.set_defaults(func=cmd_build)
    a = sub.add_parser("analyze")
    a.set_defaults(func=cmd_analyze)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
