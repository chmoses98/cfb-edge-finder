"""Wave 2D Track B: run-defense prospective streams inside the CFB research conductor. RESEARCH ONLY.

Pre-registered in docs/research/CFB_RUN_DEFENSE_PROSPECTIVE_PROTOCOL.md (committed alone before this code):

* CFB-MODEL-PROS-001  RUSH_PROJECTION_INCREMENT        P0 (literal live projection) vs frozen P1 research candidate
* CFB-PROS-003        RUSH_DEFENSE_RESIDUAL            continuous run-defense residual vs P0, efficiency held
* CFB-MECH-PROS-001   RUSH_DEFENSE_FORCED_PASS_CHANNEL possession / opponent passes / opponent interceptions
* C2C-F3 shadow (UNCERTAINTY_SCALED_RUSH_SIGNAL) and the separate FCS research population (C2C-F4)

Called by `scripts/cfb_research_conductor.py` after Wave 2, inside a try/except. Store layout (research-signals):

    run_defense/<season>/ledger.jsonl    OBSERVATION / MISSED / SETTLEMENT rows, each written once (append-only)
    run_defense/<season>/cycles.jsonl    one line per cycle that did something or failed
    run_defense/reports/<season>/run_defense_status.json

Nothing here is a bet, a stake, a badge or a production change. Wave-2 captures are READ only (market centre).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

from cfb_edge_finder.control_market.quotes import orient_markets, parse_utc
from cfb_edge_finder.control_prospective.capture import SlateGame, minutes_to, to_quote
from cfb_edge_finder.scripting.football import LeagueFitCache
from cfb_edge_finder.signal_discovery import rush_projection as R
from cfb_edge_finder.signal_discovery import wave2 as W
from cfb_edge_finder.signal_discovery import wave2_cycle as W2

VERSION = "cfb_run_defense_prospective/1.0.0"
PROTOCOL_PATH = "docs/research/CFB_RUN_DEFENSE_PROSPECTIVE_PROTOCOL.md"
PROTOCOL_COMMIT = "c9ebeee2fab99844ee3e0f5a3385740fd74df47d"
PROTOCOL_SHA256 = "576eb022d6bbfea0022aa0f1153dc87eaeda251cf33233783bc09b49bf198239"
ACTIVATION_UTC = "2026-10-13T12:00:00Z"
FIRST_ELIGIBLE = {"game_id": "401871067", "kickoff_utc": "2026-10-13T23:00:00Z"}
OBSERVE_FROM_MIN = 360.0

MODEL_PROS_001 = "CFB-MODEL-PROS-001"
PROS_003 = "CFB-PROS-003"
MECH_PROS_001 = "CFB-MECH-PROS-001"
STREAMS = (MODEL_PROS_001, PROS_003, MECH_PROS_001)
STREAM_NAMES = {
    MODEL_PROS_001: "RUSH_PROJECTION_INCREMENT",
    PROS_003: "RUSH_DEFENSE_RESIDUAL",
    MECH_PROS_001: "RUSH_DEFENSE_FORCED_PASS_CHANNEL",
}

#: Track A's final P1 coefficient (P0-LIVE errors 2015-2025, n 7,196), frozen in the protocol. Research only.
P1_BETA = 3.592270771756417
EFF_SD = 2.069489312273229
F3_K = 3.0
SEED = 20261013
N_BOOT = 2000
REVIEWS = (50, 100, 200, 400)
PRIMARY_N = 400

OBSERVATION, MISSED, SETTLEMENT = "OBSERVATION", "MISSED", "SETTLEMENT"
FBS_VS_FBS, FCS_RESEARCH = "FBS_VS_FBS", "FCS_RESEARCH"
STATUS = ("PROSPECTIVE_TRACKING", "EARLY_READ", "INTERIM", "PRIMARY_REVIEW", "REVIEW_REQUIRED", "REJECTED")

P0Provider = Callable[[str], dict[str, Any]]


# --------------------------------------------------------------------------- small helpers


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


def _num(v: Any) -> float | None:
    try:
        return None if v is None or isinstance(v, bool) else float(v)
    except (TypeError, ValueError):
        return None


def division_of(team_id: str, team_rows: Iterable[Any]) -> str | None:
    """A team's division from the production game log (its own rows, else its opponents' view of it)."""
    own = [r for r in team_rows if r.team_id == team_id]
    if own:
        return own[-1].team_division
    seen = [r for r in team_rows if r.opponent_id == team_id]
    return seen[-1].opponent_division if seen else None


def espn_ident(event: dict[str, Any]) -> dict[str, Any] | None:
    """The `wave2_cycle.feature_for` identity of a schedule event (same structure as `wave2_cycle.identity`)."""
    sides = {c.get("home_away"): c for c in event.get("competitors") or []}
    if set(sides) != {"home", "away"}:
        return None
    return {
        "event": event,
        "game_id": str(event["id"]),
        "teams": {
            s: {
                "team_id": str(sides[s]["team_id"]),
                "name": sides[s].get("location") or (sides[s].get("names") or [None])[0],
            }
            for s in ("home", "away")
        },
        "neutral_site": bool(event.get("neutral_site")),
    }


def _kickoff(event: dict[str, Any]) -> str:
    return parse_utc(event["date"]).strftime("%Y-%m-%dT%H:%M:%SZ")


def frozen_features(feat: dict[str, Any]) -> dict[str, Any]:
    """Exact Wave-2C constructs from the Wave-1/Wave-2 production feature row (protocol section 2)."""
    x = R.rush_features(feat)
    eff = _num(feat.get("net.sustained_efficiency"))
    pg_h, pg_a = _num(feat.get("prior_games_home")), _num(feat.get("prior_games_away"))
    n = None if pg_h is None or pg_a is None else min(pg_h, pg_a)
    return {
        "x_rd": x["x_rd"],
        "x_rr": x["x_rr"],
        "x_ro": x["x_ro"],
        W.DSC001_FEATURE: _num(feat.get(W.DSC001_FEATURE)),
        "eff": eff,
        "eff_z": None if eff is None else eff / EFF_SD,
        "x_rd_unc_c2c_f3": None if x["x_rd"] is None or n is None else x["x_rd"] * n / (n + F3_K),
        "prior_games_home": pg_h,
        "prior_games_away": pg_a,
        "history_rows": feat.get("history_rows"),
        "eligibility": feat.get("eligibility"),
        "football_data_cutoff": feat.get("football_data_cutoff"),
        "freshness": (feat.get("freshness") or {}).get("status"),
    }


def p1_candidate(p0: dict[str, Any] | None, x_rd: float | None, *, fbs_vs_fbs: bool) -> dict[str, Any] | None:
    """Frozen research candidate: P0 + beta * x_rd, zero-sum score shift (total exactly unchanged)."""
    if p0 is None or p0.get("status") != "OK":
        return None
    d = R.delta([P1_BETA], [x_rd], fbs_vs_fbs=fbs_vs_fbs)
    return {
        "beta": P1_BETA,
        "delta": d,
        "applied": d != 0.0,
        "margin": p0["margin"] + d,
        "total": p0["total"],
        "sd": p0["sd"],
    }


# --------------------------------------------------------------------------- rows


def _common(record: str, game_id: str, now: str, code_sha: str | None, run_id: str | None) -> dict[str, Any]:
    return {
        "row_id": f"RD:{record}:{game_id}",
        "record": record,
        "game_id": game_id,
        "written_at": now,
        "schema": VERSION,
        "protocol_sha256": PROTOCOL_SHA256,
        "code_sha": code_sha,
        "run_id": run_id,
    }


def observation_row(
    *,
    ident: dict[str, Any],
    kickoff: str,
    population: str,
    divisions: dict[str, str | None],
    feature: dict[str, Any] | None,
    feature_error: str | None,
    p0: dict[str, Any] | None,
    game_key: str | None,
    now: str,
    code_sha: str | None,
    run_id: str | None,
) -> dict[str, Any]:
    fbs = population == FBS_VS_FBS
    x = frozen_features(feature) if feature is not None else None
    cand = p1_candidate(p0, None if x is None else x["x_rd"], fbs_vs_fbs=fbs)
    row = _common(OBSERVATION, ident["game_id"], now, code_sha, run_id)
    row.update(
        {
            "kickoff_utc": kickoff,
            "minutes_before": round(minutes_to(kickoff, now), 2),
            "game_key": game_key,
            "population": population,
            "divisions": divisions,
            "neutral_site": ident["neutral_site"],
            "teams": ident["teams"],
            "features": x,
            "feature_status": "OK" if x is not None else "UNAVAILABLE",
            "feature_error": feature_error,
            "p0": p0,
            "p0_status": (p0 or {}).get("status", "UNAVAILABLE"),
            "p1": cand,
        }
    )
    return row


def market_centre(
    *,
    store_dir: Path,
    season: int,
    game_key: str | None,
    game_id: str,
    home_team_id: str,
    kickoff: str,
    now: str,
    schedule: list[dict[str, Any]],
    winner_quotes: list[dict[str, Any]],
    keys: Callable[[str], set[str]],
) -> dict[str, Any]:
    """Kalshi PRIMARY_60_180 home-perspective centre, read-only from the Wave-2 captures. Missing stays missing."""
    if not game_key:
        return {"status": "NO_KALSHI_GAME", "market_implied_margin_home": None}
    base = store_dir / "wave2" / str(season)
    attempts = [a for a in _read_rows(base / "spread_attempts.jsonl") if a.get("game_key") == game_key]
    quotes = [q for q in _read_rows(base / "spread_quotes.jsonl") if q.get("game_key") == game_key]
    w_quotes = [q for q in winner_quotes if q.get("game_key") == game_key]
    orientation = orient_markets([to_quote(q) for q in w_quotes], schedule, keys) if w_quotes else {}
    codes = W.team_codes(orientation, game_id)
    cp = W.checkpoint(
        kickoff=kickoff, now=now, attempts=attempts, quotes=quotes, codes_status=codes, team_id=home_team_id
    )
    if cp["status"] != W.ELIGIBLE:
        return {"status": cp["status"], "reason": cp.get("reason"), "market_implied_margin_home": None}
    return {
        "status": "OK",
        "market_implied_margin_home": cp["market_implied_margin"],
        "captured_at": cp["captured_at"],
        "minutes_before": cp["minutes_before"],
    }


def _box(rows: list[Any], team_id: str) -> dict[str, Any] | None:
    own = [r for r in rows if r.team_id == team_id]
    if not own:
        return None
    r = own[-1]
    pbp = r.pbp or {}
    return {
        "points": _num(r.points_for),
        "possession_seconds": _num(r.box.get("possession_seconds")),
        "pass_att": _num(r.box.get("pass_att")),
        "ints_thrown": _num(r.box.get("ints_thrown")),
        "rush_att": _num(r.box.get("rush_att")),
        "turnovers": _num(r.box.get("turnovers")),
        "drives": _num(pbp.get("drives")),
        "scoring_opps": _num(pbp.get("scoring_opps")),
    }


def _diff(h: dict[str, Any], a: dict[str, Any], k: str) -> float | None:
    return None if h.get(k) is None or a.get(k) is None else h[k] - a[k]


def settlement_row(
    *,
    obs: dict[str, Any],
    team_rows: list[Any],
    market: dict[str, Any],
    now: str,
    code_sha: str | None,
    run_id: str | None,
) -> dict[str, Any] | None:
    rows = [r for r in team_rows if str(r.game_id) == obs["game_id"]]
    h = _box(rows, obs["teams"]["home"]["team_id"])
    a = _box(rows, obs["teams"]["away"]["team_id"])
    if h is None or a is None or h["points"] is None or a["points"] is None:
        return None
    row = _common(SETTLEMENT, obs["game_id"], now, code_sha, run_id)
    row.update(
        {
            "kickoff_utc": obs["kickoff_utc"],
            "population": obs["population"],
            "actual_margin_home": h["points"] - a["points"],
            "actual_total": h["points"] + a["points"],
            "box": {"home": h, "away": a},
            "mechanism": {
                "possession_diff_home": _diff(h, a, "possession_seconds"),
                "opponent_pass_att_diff": _diff(a, h, "pass_att"),
                "opponent_ints_diff": _diff(a, h, "ints_thrown"),
                "drives_diff": _diff(h, a, "drives"),
                "scoring_opps_diff": _diff(h, a, "scoring_opps"),
            },
            "market": market,
            "source": "production game log (ESPN final score)",
        }
    )
    return row


# --------------------------------------------------------------------------- the cycle


def run(
    *,
    store_dir: Path,
    season: int,
    now: str,
    schedule: list[dict[str, Any]],
    team_rows: list[Any],
    p0_provider: P0Provider | None,
    games: list[SlateGame] | None = None,
    winner_quotes: list[dict[str, Any]] | None = None,
    keys: Callable[[str], set[str]] | None = None,
    code_sha: str | None = None,
    run_id: str | None = None,
    feature_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One cycle. Idempotent: a second call at the same instant appends nothing."""
    feature_fn = feature_fn or W2.feature_for
    base = store_dir / "run_defense" / str(season)
    ledger_path = base / "ledger.jsonl"
    ledger = _read_rows(ledger_path)
    done = {r["row_id"] for r in ledger}
    rows_team = tuple(team_rows)
    activation = parse_utc(ACTIVATION_UTC)
    key_by_event = {g.espn_event_id: g.game_key for g in games or [] if g.espn_event_id}
    errors: list[dict[str, Any]] = []
    new_rows: list[dict[str, Any]] = []
    cache = LeagueFitCache()

    # 1. frozen pregame observations (0 < minutes-to-kickoff <= 360); MISSED once kickoff passes unobserved
    for event in schedule:
        if not event.get("date") or not event.get("id"):
            continue
        kickoff = _kickoff(event)
        if parse_utc(kickoff) < activation:
            continue  # no backfill: before activation a game is never written
        ident = espn_ident(event)
        if ident is None:
            continue
        gid = ident["game_id"]
        if f"RD:{OBSERVATION}:{gid}" in done or f"RD:{MISSED}:{gid}" in done:
            continue
        hd = division_of(ident["teams"]["home"]["team_id"], rows_team)
        ad = division_of(ident["teams"]["away"]["team_id"], rows_team)
        if hd != "fbs" and ad != "fbs":
            continue  # FCS-vs-FCS (or unknown) is out of every population
        population = FBS_VS_FBS if hd == ad == "fbs" else FCS_RESEARCH
        m = minutes_to(kickoff, now)
        if m <= 0:
            row = _common(MISSED, gid, now, code_sha, run_id)
            row.update({"kickoff_utc": kickoff, "population": population, "reason": "NOT_OBSERVED_BEFORE_KICKOFF"})
            new_rows.append(row)
            done.add(row["row_id"])
            continue
        if m > OBSERVE_FROM_MIN:
            continue
        feature, ferr = None, None
        try:
            feature = feature_fn(ident, kickoff, season, rows_team, schedule, cache)
        except Exception as exc:  # noqa: BLE001 -- recorded; the observation still freezes P0
            ferr = f"{type(exc).__name__}: {exc}"
        p0: dict[str, Any] | None
        if p0_provider is None:
            p0 = {"status": "UNAVAILABLE", "reason": "NO_P0_INPUTS"}
        else:
            try:
                p0 = p0_provider(gid)
            except Exception as exc:  # noqa: BLE001 -- fail closed
                p0 = {"status": "UNAVAILABLE", "reason": f"{type(exc).__name__}: {exc}"[:300]}
        if feature is None and ferr is not None and m > 60:
            errors.append({"game_id": gid, "stage": "features", "error": ferr})
            continue  # retried next cycle while comfortably before kickoff
        row = observation_row(
            ident=ident,
            kickoff=kickoff,
            population=population,
            divisions={"home": hd, "away": ad},
            feature=feature,
            feature_error=ferr,
            p0=p0,
            game_key=key_by_event.get(gid),
            now=now,
            code_sha=code_sha,
            run_id=run_id,
        )
        new_rows.append(row)
        done.add(row["row_id"])

    # 2. settlements (after kickoff, once the production log has both final scores)
    for obs in [r for r in ledger + new_rows if r["record"] == OBSERVATION]:
        rid = f"RD:{SETTLEMENT}:{obs['game_id']}"
        if rid in done or parse_utc(now) <= parse_utc(obs["kickoff_utc"]):
            continue
        try:
            market = market_centre(
                store_dir=store_dir,
                season=season,
                game_key=obs.get("game_key"),
                game_id=obs["game_id"],
                home_team_id=obs["teams"]["home"]["team_id"],
                kickoff=obs["kickoff_utc"],
                now=now,
                schedule=schedule,
                winner_quotes=winner_quotes or [],
                keys=keys or (lambda name: set()),
            )
        except Exception as exc:  # noqa: BLE001 -- descriptive only; never blocks settlement
            market = {
                "status": "MARKET_READ_FAILED",
                "error": f"{type(exc).__name__}: {exc}",
                "market_implied_margin_home": None,
            }
        row = settlement_row(
            obs=obs, team_rows=list(rows_team), market=market, now=now, code_sha=code_sha, run_id=run_id
        )
        if row is None:
            continue
        new_rows.append(row)
        done.add(rid)

    _append(ledger_path, new_rows)
    ledger += new_rows
    if new_rows or errors:
        _append(base / "cycles.jsonl", [{"at": now, "run_id": run_id, "rows": len(new_rows), "errors": errors}])

    # 3. cumulative status report
    report = status_report(ledger, now=now, season=season, errors=errors)
    _write_if_changed(
        store_dir / "run_defense" / "reports" / str(season) / "run_defense_status.json",
        json.dumps(report, indent=1, sort_keys=True, default=float) + "\n",
    )
    return {
        "ledger_rows": len(new_rows),
        "errors": errors,
        "by_record": dict(Counter(f"{r['record']}:{r.get('population')}" for r in new_rows)),
    }


# --------------------------------------------------------------------------- summaries and status


def _cluster_ci(values: list[float], clusters: list[str], *, seed: int = SEED) -> list[float] | None:
    if len(values) < 10:
        return None
    return R.boot_mean_ci(np.array(values), clusters, seed=seed, n_boot=N_BOOT)


def _boot_coef(x: np.ndarray, y: np.ndarray, clusters: list[str], col: int = 0) -> list[float] | None:
    if len(y) < 10:
        return None
    keys = sorted(set(clusters))
    idx = {k: np.array([i for i, c in enumerate(clusters) if c == k]) for k in keys}
    rng = np.random.default_rng(SEED)
    out = []
    for _ in range(N_BOOT):
        take = np.concatenate([idx[k] for k in rng.choice(keys, size=len(keys))])
        try:
            out.append(float(R.fit_beta(x[take], y[take])[col]))
        except Exception:  # noqa: BLE001 -- degenerate resample
            continue
    if not out:
        return None
    return [float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))]


def status_for(n: int, primary_failed: bool | None) -> str:
    if n < REVIEWS[0]:
        return "PROSPECTIVE_TRACKING"
    if n < REVIEWS[1]:
        return "EARLY_READ"
    if n < PRIMARY_N:
        return "INTERIM"
    return "REJECTED" if primary_failed else "REVIEW_REQUIRED"


def next_review(n: int) -> int | None:
    return next((k for k in REVIEWS if k > n), None)


def _pairs(ledger: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    settled = {r["game_id"]: r for r in ledger if r["record"] == SETTLEMENT}
    return [(o, settled.get(o["game_id"])) for o in ledger if o["record"] == OBSERVATION]


def _p_home(mu: float, sd: float) -> float:
    return min(1.0, max(0.0, 1.0 - NormalDist(mu, sd).cdf(0.5)))


def stream_summaries(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = [(o, s) for o, s in _pairs(ledger) if o["population"] == FBS_VS_FBS]
    out: dict[str, Any] = {}

    # CFB-MODEL-PROS-001
    elig = [
        (o, s)
        for o, s in pairs
        if o["p0_status"] == "OK" and o["p1"] is not None and (o["features"] or {}).get("x_rd") is not None
    ]
    done = [(o, s) for o, s in elig if s is not None]
    d = [
        abs(o["p0"]["margin"] - s["actual_margin_home"]) - abs(o["p1"]["margin"] - s["actual_margin_home"])
        for o, s in done
    ]
    dsq = [
        (o["p0"]["margin"] - s["actual_margin_home"]) ** 2 - (o["p1"]["margin"] - s["actual_margin_home"]) ** 2
        for o, s in done
    ]
    cl = [o["kickoff_utc"][:10] for o, _ in done]
    brier = [
        (_p_home(o["p1"]["margin"], o["p0"]["sd"]) - (1.0 if s["actual_margin_home"] > 0 else 0.0)) ** 2
        - (_p_home(o["p0"]["margin"], o["p0"]["sd"]) - (1.0 if s["actual_margin_home"] > 0 else 0.0)) ** 2
        for o, s in done
        if s["actual_margin_home"] != 0
    ]
    mean_d = float(np.mean(d)) if d else None
    out[MODEL_PROS_001] = {
        "name": STREAM_NAMES[MODEL_PROS_001],
        "eligible": len(elig),
        "observed": len(elig),
        "settled": len(done),
        "primary_metric": {
            "name": "mean_abs_error_difference_p0_minus_p1",
            "value": mean_d,
            "ci95": _cluster_ci(d, cl),
        },
        "secondary": {
            "mean_sq_error_difference": float(np.mean(dsq)) if dsq else None,
            "winner_brier_p1_minus_p0": float(np.mean(brier)) if brier else None,
            "p0_bias": float(np.mean([s["actual_margin_home"] - o["p0"]["margin"] for o, s in done])) if done else None,
            "p1_bias": float(np.mean([s["actual_margin_home"] - o["p1"]["margin"] for o, s in done])) if done else None,
            "favourite_tail_bias": _tail_bias(done),
        },
        "status": status_for(len(done), None if len(done) < PRIMARY_N else (mean_d is not None and mean_d <= 0)),
        "next_review": next_review(len(done)),
    }

    # CFB-PROS-003 (primary: P0 residual on x_rd with efficiency held; secondary: Kalshi centre residual)
    elig3 = [
        (o, s)
        for o, s in pairs
        if o["p0_status"] == "OK"
        and o["features"]
        and o["features"]["x_rd"] is not None
        and o["features"]["eff_z"] is not None
    ]
    done3 = [(o, s) for o, s in elig3 if s is not None]
    b1, ci = None, None
    if len(done3) >= 3:
        x = np.array([[o["features"]["x_rd"], o["features"]["eff_z"]] for o, _ in done3])
        y = np.array([s["actual_margin_home"] - o["p0"]["margin"] for o, s in done3])
        b1 = float(R.fit_beta(x, y)[0])
        ci = _boot_coef(x, y, [o["kickoff_utc"][:10] for o, _ in done3])
    mk = [(o, s) for o, s in done3 if (s.get("market") or {}).get("market_implied_margin_home") is not None]
    b1m = None
    if len(mk) >= 3:
        x = np.array([[o["features"]["x_rd"], o["features"]["eff_z"]] for o, _ in mk])
        y = np.array([s["actual_margin_home"] - s["market"]["market_implied_margin_home"] for _, s in mk])
        b1m = float(R.fit_beta(x, y)[0])
    out[PROS_003] = {
        "name": STREAM_NAMES[PROS_003],
        "eligible": len(elig3),
        "observed": len(elig3),
        "settled": len(done3),
        "primary_metric": {"name": "b1_points_per_sd_x_rd_given_efficiency_vs_p0", "value": b1, "ci95": ci},
        "secondary": {"market_b1_vs_kalshi_centre": b1m, "n_with_kalshi_centre": len(mk)},
        "status": status_for(len(done3), None if len(done3) < PRIMARY_N else (b1 is not None and b1 <= 0)),
        "next_review": next_review(len(done3)),
    }

    # CFB-MECH-PROS-001
    elig_m = [(o, s) for o, s in pairs if o["features"] and o["features"]["x_rd"] is not None]
    done_m = [(o, s) for o, s in elig_m if s is not None]
    slopes: dict[str, Any] = {}
    for k in (
        "possession_diff_home",
        "opponent_pass_att_diff",
        "opponent_ints_diff",
        "drives_diff",
        "scoring_opps_diff",
    ):
        rows = [(o, s) for o, s in done_m if s["mechanism"].get(k) is not None]
        if len(rows) >= 3:
            x = np.array([[o["features"]["x_rd"]] for o, _ in rows])
            y = np.array([s["mechanism"][k] for _, s in rows])
            slopes[k] = {"n": len(rows), "slope_per_sd": float(R.fit_beta(x, y)[0])}
        else:
            slopes[k] = {"n": len(rows), "slope_per_sd": None}
    core = [slopes[k]["slope_per_sd"] for k in ("possession_diff_home", "opponent_pass_att_diff", "opponent_ints_diff")]
    failed = None if len(done_m) < PRIMARY_N else all(v is not None and v <= 0 for v in core)
    out[MECH_PROS_001] = {
        "name": STREAM_NAMES[MECH_PROS_001],
        "eligible": len(elig_m),
        "observed": len(elig_m),
        "settled": len(done_m),
        "primary_metric": {"name": "slopes_on_x_rd", "value": slopes},
        "status": status_for(len(done_m), failed),
        "next_review": next_review(len(done_m)),
    }
    return out


def _tail_bias(done: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    out = {}
    for lo, hi in R.MARGIN_BINS:
        rows = [(o, s) for o, s in done if lo <= abs(o["p0"]["margin"]) < hi]
        key = f"{int(lo)}_{'inf' if hi == float('inf') else int(hi)}"
        if not rows:
            out[key] = {"n": 0}
            continue
        sg = [1.0 if o["p0"]["margin"] >= 0 else -1.0 for o, _ in rows]
        out[key] = {
            "n": len(rows),
            "p0": float(
                np.mean([g * (s["actual_margin_home"] - o["p0"]["margin"]) for g, (o, s) in zip(sg, rows, strict=True)])
            ),
            "p1": float(
                np.mean([g * (s["actual_margin_home"] - o["p1"]["margin"]) for g, (o, s) in zip(sg, rows, strict=True)])
            ),
        }
    return out


def status_report(
    ledger: list[dict[str, Any]], *, now: str, season: int, errors: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    obs = [r for r in ledger if r["record"] == OBSERVATION]
    fcs = [r for r in obs if r["population"] == FCS_RESEARCH]
    fcs_settled = {r["game_id"] for r in ledger if r["record"] == SETTLEMENT and r["population"] == FCS_RESEARCH}
    return {
        "schema": VERSION,
        "season": season,
        "protocol": {"path": PROTOCOL_PATH, "commit": PROTOCOL_COMMIT, "sha256": PROTOCOL_SHA256},
        "activation_utc": ACTIVATION_UTC,
        "first_eligible": FIRST_ELIGIBLE,
        "label": "PROSPECTIVE_RESEARCH_ONLY -- no bet, no stake, no badge, no production change",
        "streams": stream_summaries(ledger),
        "fcs_research_c2c_f4": {"observed": len(fcs), "settled": len(fcs_settled), "pooled_with_primary": False},
        "c2c_f3_shadow": {
            "name": "UNCERTAINTY_SCALED_RUSH_SIGNAL",
            "recorded": sum(1 for r in obs if (r.get("features") or {}).get("x_rd_unc_c2c_f3") is not None),
            "affects": "nothing (shadow only)",
        },
        "health": {
            "observations": len(obs),
            "missed": sum(1 for r in ledger if r["record"] == MISSED),
            "settlements": sum(1 for r in ledger if r["record"] == SETTLEMENT),
            "p0_unavailable": sum(1 for r in obs if r["p0_status"] != "OK"),
            "feature_unavailable": sum(1 for r in obs if r["feature_status"] != "OK"),
            "last_cycle_errors": errors or [],
        },
        "ledger_sha256": hashlib.sha256(
            "\n".join(json.dumps(r, sort_keys=True) for r in ledger).encode("utf-8")
        ).hexdigest(),
    }


# --------------------------------------------------------------------------- P0: the literal live object


class LiveP0:
    """Current production projection, built exactly as `research_scan_and_capture` builds it (read-only inputs).

    `lines` is the live football-state history; `talent_by_team` comes from the production loader;
    `schedule_games` is the football state's CFBD schedule (CFBD game id == ESPN event id)."""

    def __init__(
        self,
        *,
        lines_loader: Callable[[], list[Any]],
        talent_by_team: dict[str, float],
        schedule_games: list[dict[str, Any]],
        model_version: str,
        provenance: dict[str, Any],
    ) -> None:
        self._lines_loader = lines_loader
        self._talent = dict(talent_by_team)
        self._raw = {str(g.get("id")): g for g in schedule_games}
        self._cache = None
        self.model_version = model_version
        self.provenance = provenance

    def __call__(self, game_id: str) -> dict[str, Any]:
        from datetime import UTC, datetime

        from cfb_edge_finder.ingestion.game_normalization import (
            away_classification,
            home_classification,
            normalize_cfbd_game,
        )
        from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest

        raw = self._raw.get(str(game_id))
        if raw is None:
            return {"status": "UNAVAILABLE", "reason": "NOT_IN_LIVE_FOOTBALL_STATE_SCHEDULE"}
        g = normalize_cfbd_game(raw, observed_at=datetime.now(UTC))
        hc, ac = home_classification(raw), away_classification(raw)
        if "fbs" not in (hc, ac):
            return {"status": "UNAVAILABLE", "reason": "NO_FBS_TEAM"}
        if self._cache is None:
            self._cache = GameProjectionCache(lines_provider=self._lines_loader, talent_by_team=self._talent)
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
        p = self._cache.get_or_build(req).projection
        dist = p.to_game_distribution()
        sd = float(np.sqrt(dist.home_sd**2 + dist.away_sd**2 - 2 * dist.correlation * dist.home_sd * dist.away_sd))
        return {
            "status": "OK",
            "model_version": self.model_version,
            "cfbd_game_id": g.game_id,
            "home_id": g.home_team_id,
            "away_id": g.away_team_id,
            "classifications": [hc, ac],
            "margin": p.expected_margin,
            "total": p.expected_total,
            "raw_margin": p.raw_expected_margin,
            "c2_delta": p.margin_delta,
            "talent_delta": p.talent_margin_delta,
            "sd": sd,
            "provenance": self.provenance,
        }
