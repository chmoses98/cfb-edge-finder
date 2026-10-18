"""Prospective (live) validation: archive pregame forecasts, settle them against final results, score them, detect drift.

Storage layout (all write-once; lives on each repo's `projection-data` branch):
  forecasts/<sport>/<game_date>/<HHMMSS>Z_<hash>.jsonl.gz     sift.forecast_archive.v1 records
  outcomes/<sport>/<game_date>/<HHMMSS>Z_<hash>.jsonl.gz      sift.outcome.v1 records (final results as observed)
  settlements/<sport>/<game_date>/<HHMMSS>Z_<hash>.jsonl.gz   one row per settled forecast_id (never rewritten)
  corrections/<sport>/<game_date>/...                         later stat corrections (appended, never overwrite)
  scorecards/<sport>/latest.json + dated copies               sift.prospective_scorecard.v1

Integrity rules implemented here (and tested):
  * a forecast is archived only if it was generated AND archived before the game started ("late" ones are rejected);
  * the forecast scored for a (game, entity, family, model) is its LAST pregame version;
  * a forecast_id is settled at most once; a later different actual becomes a correction record, never an overwrite;
  * paired comparisons use only (game, entity, family) keys where every compared model has a settled forecast;
  * DNP / void outcomes are recorded and excluded from conditional scoring (props void on DNP).
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .dists import DistBatch
from .metrics import cluster_bootstrap_diff

FORECAST_SCHEMA = "sift.forecast_archive.v1"
OUTCOME_SCHEMA = "sift.outcome.v1"
SETTLEMENT_SCHEMA = "sift.settlement.v1"
SCORECARD_SCHEMA = "sift.prospective_scorecard.v1"

FORECAST_FIELDS = ("sport", "family", "model_id", "model_role", "model_version", "game_id", "game_start_utc",
                   "entity_id", "entity_name", "team", "generated_utc", "data_asof_utc", "mean", "lo", "probs",
                   "gate_status", "participation", "readiness")


def _ts(x) -> datetime | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    try:
        d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def forecast_id(r: dict) -> str:
    key = "|".join(str(r.get(k, "")) for k in ("sport", "family", "model_id", "game_id", "entity_id", "generated_utc"))
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _write_once(rows: list[dict], root: str, kind: str, sport: str, day: str, now: datetime) -> str | None:
    if not rows:
        return None
    payload = "\n".join(json.dumps(r, sort_keys=True, default=float) for r in rows).encode()
    h = hashlib.sha256(payload).hexdigest()[:12]
    d = os.path.join(root, kind, sport.lower(), day)
    os.makedirs(d, exist_ok=True)
    for fn in os.listdir(d):
        if fn.endswith(f"_{h}.jsonl.gz"):
            return os.path.join(d, fn)
    path = os.path.join(d, f"{now.strftime('%H%M%S')}Z_{h}.jsonl.gz")
    with gzip.open(path, "xb") as f:
        f.write(payload)
    return path


def _read(root: str, kind: str, sport: str, days: Iterable[str] | None = None) -> list[dict]:
    base = os.path.join(root, kind, sport.lower())
    if not os.path.isdir(base):
        return []
    out = []
    for day in (sorted(days) if days else sorted(os.listdir(base))):
        d = os.path.join(base, day)
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".jsonl.gz"):
                    with gzip.open(os.path.join(d, fn), "rt") as f:
                        out += [json.loads(x) for x in f if x.strip()]
    return out


# --------------------------------------------------------------------------- 1. archive
def archive_forecasts(records: Iterable[dict], root: str, now_utc: str | None = None) -> dict[str, Any]:
    """Archive pregame forecasts. Each record needs FORECAST_FIELDS (probs on the integer lattice starting at lo).
    Rejected: generated at/after start, archived at/after start, missing fields, incoherent pmf. Duplicates are skipped."""
    now = _ts(now_utc) or datetime.now(timezone.utc)
    seen_by_day: dict[tuple, set] = {}
    by_day: dict[tuple, list] = {}
    stats = {"written": 0, "late_rejected": 0, "invalid": 0, "duplicates": 0, "paths": []}
    for r in records:
        r = dict(r)
        start, gen = _ts(r.get("game_start_utc")), _ts(r.get("generated_utc"))
        missing = [k for k in ("sport", "family", "model_id", "game_id", "entity_id", "game_start_utc", "generated_utc", "mean")
                   if r.get(k) in (None, "")]
        probs = np.asarray(r.get("probs") or [], float)
        if missing or start is None or gen is None or (len(probs) and (not np.isfinite(probs).all() or
                                                                         abs(probs.sum() - 1) > 1e-4 or (probs < 0).any())):
            stats["invalid"] += 1
            continue
        if gen >= start or now >= start:
            stats["late_rejected"] += 1
            continue
        r["schema"] = FORECAST_SCHEMA
        r["archived_utc"] = _iso(now)
        r["forecast_id"] = forecast_id(r)
        r["probs"] = [round(float(p), 7) for p in probs]
        sp, day = r["sport"], start.strftime("%Y-%m-%d")
        key = (sp, day)
        if key not in seen_by_day:
            seen_by_day[key] = {x["forecast_id"] for x in _read(root, "forecasts", sp, [day])}
        if r["forecast_id"] in seen_by_day[key]:
            stats["duplicates"] += 1
            continue
        seen_by_day[key].add(r["forecast_id"])
        by_day.setdefault(key, []).append(r)
    for (sp, day), rows in by_day.items():
        p = _write_once(rows, root, "forecasts", sp, day, now)
        stats["written"] += len(rows)
        stats["paths"].append(p)
    return stats


def load_forecasts(root: str, sport: str, days: Iterable[str] | None = None) -> pd.DataFrame:
    return pd.DataFrame(_read(root, "forecasts", sport, days))


def latest_pregame(f: pd.DataFrame) -> pd.DataFrame:
    """The forecast that counts: last version generated and archived before the start, per (game, entity, family, model)."""
    if f.empty:
        return f
    g = f.copy()
    g["_gen"] = pd.to_datetime(g["generated_utc"], utc=True)
    g["_arc"] = pd.to_datetime(g["archived_utc"], utc=True)
    g["_start"] = pd.to_datetime(g["game_start_utc"], utc=True)
    g = g[(g["_gen"] < g["_start"]) & (g["_arc"] < g["_start"])]
    g = g.sort_values("_gen").groupby(["sport", "game_id", "entity_id", "family", "model_id"], as_index=False).tail(1)
    return g.drop(columns=["_gen", "_arc", "_start"]).reset_index(drop=True)


# --------------------------------------------------------------------------- 2. outcomes + settlement
def archive_outcomes(rows: Iterable[dict], root: str, sport: str, now_utc: str | None = None) -> dict[str, Any]:
    """Record final results as observed (game_id, entity_id, family, actual, participated, final, source, game_date).
    An outcome whose latest archived observation already has the same actual and participation is not written again,
    so re-running results adds nothing; a changed value is appended (and settle() turns it into a correction)."""
    now = _ts(now_utc) or datetime.now(timezone.utc)
    rows = [dict(r) for r in rows]
    days = sorted({str(r.get("game_date")) for r in rows if r.get("game_date") not in (None, "")})
    prev: dict[tuple, tuple] = {}
    for r in sorted(_read(root, "outcomes", sport, days), key=lambda x: x.get("observed_utc", "")):
        prev[(str(r["game_id"]), str(r["entity_id"]), r["family"])] = (_norm(r.get("actual")), _norm(r.get("participated")))
    by_day: dict[str, list] = {}
    n_bad = n_same = 0
    for r in rows:
        if not r.get("final") or r.get("game_id") in (None, "") or r.get("entity_id") in (None, "") or not r.get("family"):
            n_bad += 1
            continue
        key = (str(r["game_id"]), str(r["entity_id"]), r["family"])
        if prev.get(key) == (_norm(r.get("actual")), _norm(r.get("participated"))):
            n_same += 1
            continue
        r["schema"] = OUTCOME_SCHEMA
        r["observed_utc"] = _iso(now)
        by_day.setdefault(str(r["game_date"]), []).append(r)
    paths = [_write_once(v, root, "outcomes", sport, d, now) for d, v in by_day.items()]
    return {"written": sum(len(v) for v in by_day.values()), "unchanged": n_same, "rejected_not_final": n_bad,
            "paths": paths}


def _norm(v):
    """Comparable form of an outcome value (NaN/None -> None, numbers -> float, bools kept)."""
    if isinstance(v, np.generic):
        v = v.item()
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


def settle(root: str, sport: str, now_utc: str | None = None, days: Iterable[str] | None = None) -> dict[str, Any]:
    """Settle every unsettled latest-pregame forecast that has a final outcome. Idempotent: a forecast_id already settled
    is never re-settled; if its outcome has since changed, a correction record is appended instead."""
    now = _ts(now_utc) or datetime.now(timezone.utc)
    f = latest_pregame(load_forecasts(root, sport, days))
    o = pd.DataFrame(_read(root, "outcomes", sport, days))
    done = pd.DataFrame(_read(root, "settlements", sport))
    done_ids = set(done["forecast_id"]) if not done.empty else set()
    prev_actual = dict(zip(done["forecast_id"], done["actual"])) if not done.empty else {}
    stats = {"settled": 0, "void": 0, "pending": 0, "already_settled": 0, "corrections": 0}
    if f.empty:
        return stats
    if o.empty:
        stats["pending"] = len(f)
        return stats
    # latest observation of each outcome
    o = o.sort_values("observed_utc").groupby(["game_id", "entity_id", "family"], as_index=False).tail(1)
    j = f.merge(o[["game_id", "entity_id", "family", "actual", "participated", "game_date", "source"]],
                on=["game_id", "entity_id", "family"], how="left")
    new, corr = {}, {}
    for _, r in j.iterrows():
        if pd.isna(r.get("game_date")):
            stats["pending"] += 1
            continue
        part = r.get("participated")
        void = part is not None and not (isinstance(part, float) and math.isnan(part)) and not bool(part)
        rec = {"schema": SETTLEMENT_SCHEMA, "forecast_id": r["forecast_id"], "sport": r["sport"], "family": r["family"],
               "model_id": r["model_id"], "model_role": r.get("model_role"), "game_id": r["game_id"],
               "entity_id": r["entity_id"], "game_date": str(r["game_date"]), "game_start_utc": r["game_start_utc"],
               "generated_utc": r["generated_utc"], "mean": r["mean"], "lo": r.get("lo"), "probs": r.get("probs"),
               "gate_status": r.get("gate_status"), "participation": r.get("participation"),
               "actual": None if void else float(r["actual"]), "void": bool(void), "outcome_source": r.get("source"),
               "settled_utc": _iso(now)}
        if r["forecast_id"] in done_ids:
            stats["already_settled"] += 1
            pa = prev_actual.get(r["forecast_id"])
            if rec["actual"] is not None and pa is not None and abs(float(pa) - rec["actual"]) > 1e-9:
                corr.setdefault(rec["game_date"], []).append({**rec, "previous_actual": pa, "schema": "sift.correction.v1"})
            continue
        new.setdefault(rec["game_date"], []).append(rec)
        stats["void" if void else "settled"] += 1
    for d, rows in new.items():
        _write_once(rows, root, "settlements", sport, d, now)
    known_corr = {(c["forecast_id"], c["actual"]) for c in _read(root, "corrections", sport)}
    for d, rows in corr.items():
        rows = [c for c in rows if (c["forecast_id"], c["actual"]) not in known_corr]
        stats["corrections"] += len(rows)
        _write_once(rows, root, "corrections", sport, d, now)
    return stats


def load_settlements(root: str, sport: str) -> pd.DataFrame:
    return pd.DataFrame(_read(root, "settlements", sport))


# --------------------------------------------------------------------------- 3. scoring
def _dist(rows: pd.DataFrame) -> DistBatch:
    los = [int(x) if x is not None and not (isinstance(x, float) and math.isnan(x)) else 0 for x in rows["lo"]]
    probs = [list(p) if isinstance(p, (list, tuple, np.ndarray)) else [] for p in rows["probs"]]
    lo = min(los) if los else 0
    hi = max((l + len(p) for l, p in zip(los, probs)), default=1)
    P = np.zeros((len(rows), max(hi - lo, 1)))
    for i, (l, p) in enumerate(zip(los, probs)):
        if p:
            P[i, l - lo:l - lo + len(p)] = p
        else:
            P[i, :] = 1.0 / P.shape[1]
    return DistBatch(lo, P)


def _metrics(g: pd.DataFrame) -> dict[str, Any]:
    y = g["actual"].to_numpy(float)
    mu = g["mean"].to_numpy(float)
    e = mu - y
    out = {"n": int(len(g)), "n_games": int(g["game_id"].nunique()), "mae": float(np.mean(np.abs(e))),
           "rmse": float(np.sqrt(np.mean(e ** 2))), "bias": float(np.mean(e)), "mean_actual": float(np.mean(y))}
    if len(g) > 1:
        cl = g["game_id"].to_numpy()
        out["bias_ci95"] = cluster_bootstrap_diff(mu, y, cl, n_boot=500)["ci95"]
    has = g["probs"].map(lambda p: isinstance(p, (list, tuple, np.ndarray)) and len(p) > 0)
    if has.all() and len(g):
        d = _dist(g)
        out["crps"] = float(np.mean(d.crps(y)))
        pit = d.pit(y, np.random.default_rng(0))
        for lvl in (0.5, 0.8, 0.9):
            a = (1 - lvl) / 2
            out[f"cover_{int(lvl * 100)}"] = float(np.mean((pit >= a) & (pit <= 1 - a)))
        out["pit_var_ratio"] = float(np.var(pit) / (1 / 12)) if len(pit) > 1 else None
    return out


def score(settled: pd.DataFrame, challenger: str = "challenger", incumbent: str = "incumbent",
          since_utc: str | None = None) -> list[dict[str, Any]]:
    """Per family: metrics for every model, plus a paired challenger-vs-incumbent comparison on identical keys."""
    if settled.empty:
        return []
    s = settled[~settled["void"].astype(bool)].copy()
    if since_utc:
        s = s[pd.to_datetime(s["game_start_utc"], utc=True) >= pd.to_datetime(since_utc, utc=True)]
    out = []
    for fam, g in s.groupby("family"):
        fam_row: dict[str, Any] = {"family": fam, "models": []}
        for (mid, role), gm in g.groupby(["model_id", "model_role"], dropna=False):
            fam_row["models"].append({"model_id": mid, "model_role": role, **_metrics(gm)})
        roles = g.groupby("model_role")
        if challenger in roles.groups and incumbent in roles.groups:
            a = roles.get_group(challenger).set_index(["game_id", "entity_id"])
            b = roles.get_group(incumbent).set_index(["game_id", "entity_id"])
            keys = a.index.intersection(b.index)
            if len(keys):
                a, b = a.loc[keys].reset_index(), b.loc[keys].reset_index()
                y = a["actual"].to_numpy(float)
                paired = {"n": int(len(keys)), "n_games": int(a["game_id"].nunique()),
                          "mae_diff": cluster_bootstrap_diff(np.abs(a["mean"] - y), np.abs(b["mean"] - y), a["game_id"], n_boot=500)}
                if a["probs"].map(bool).all() and b["probs"].map(bool).all():
                    paired["crps_diff"] = cluster_bootstrap_diff(_dist(a).crps(y), _dist(b).crps(y), a["game_id"], n_boot=500)
                fam_row["paired"] = paired
        out.append(fam_row)
    return out


# --------------------------------------------------------------------------- 4. drift
def drift_alerts(scores: list[dict], reference: dict[str, dict], role: str = "challenger",
                 min_n: int = 100) -> list[dict[str, Any]]:
    """Compare live metrics with the frozen historical expectation (holdout) and with the incumbent.

    reference: family -> {"mae", "crps", "bias", "cover_80", "mean_actual"} from summary.json holdout.
    Status per family: INSUFFICIENT (n < min_n), OK, WATCH, ALERT. Every alert says what moved and by how much."""
    alerts = []
    for fr in scores:
        fam = fr["family"]
        m = next((x for x in fr["models"] if x.get("model_role") == role), None)
        if m is None:
            continue
        ref = reference.get(fam) or {}
        if m["n"] < min_n:
            alerts.append({"family": fam, "status": "INSUFFICIENT", "what": f"{m['n']} settled forecasts (< {min_n})"})
            continue
        found = []
        lo, hi = (m.get("bias_ci95") or [None, None])
        scale = max(abs(m.get("mean_actual") or 0) * 0.05, 1e-9)
        if lo is not None and (lo > 0 or hi < 0) and abs(m["bias"]) > scale:
            found.append(("ALERT", f"bias {m['bias']:+.3f} (95% CI {lo:+.3f}..{hi:+.3f}) exceeds 5% of the mean"))
        c80 = m.get("cover_80")
        if c80 is not None:
            if m["n"] >= 200 and not (0.72 <= c80 <= 0.88):
                found.append(("ALERT", f"80% range caught {c80:.0%} of outcomes (expected ~80%)"))
            elif not (0.65 <= c80 <= 0.95):
                found.append(("WATCH", f"80% range caught {c80:.0%} of outcomes"))
        if ref.get("crps") and m.get("crps"):
            ratio = m["crps"] / ref["crps"]
            if ratio > 1.15 and m["n"] >= 300:
                found.append(("ALERT", f"CRPS {m['crps']:.3f} is {ratio:.0%} of the historical holdout ({ref['crps']:.3f})"))
            elif ratio > 1.10:
                found.append(("WATCH", f"CRPS {ratio:.0%} of historical holdout"))
        elif ref.get("mae") and m["mae"] / ref["mae"] > 1.15 and m["n"] >= 300:
            found.append(("ALERT", f"MAE {m['mae']:.3f} vs historical {ref['mae']:.3f}"))
        p = fr.get("paired", {}).get("crps_diff") or fr.get("paired", {}).get("mae_diff")
        if p and p.get("n_obs", 0) >= min_n and p["ci95"][0] > 0:
            found.append(("ALERT", f"challenger worse than incumbent on identical forecasts: {p['mean_diff']:+.4f} "
                                   f"[{p['ci95'][0]:+.4f}, {p['ci95'][1]:+.4f}]"))
        status = "ALERT" if any(s == "ALERT" for s, _ in found) else "WATCH" if found else "OK"
        alerts.append({"family": fam, "status": status, "what": "; ".join(w for _, w in found) or "within expectations"})
    return alerts


# --------------------------------------------------------------------------- 5. scorecard + readable summary
def scorecard(sport: str, root: str, reference: dict[str, dict], now_utc: str | None = None,
              window_days: int | None = None, capture_stats: dict | None = None) -> dict[str, Any]:
    now = _ts(now_utc) or datetime.now(timezone.utc)
    s = load_settlements(root, sport)
    since = _iso(now - pd.Timedelta(days=window_days)) if window_days else None
    scores = score(s, since_utc=since) if not s.empty else []
    alerts = drift_alerts(scores, reference)
    f = load_forecasts(root, sport)
    return {"schema": SCORECARD_SCHEMA, "sport": sport, "generated_utc": _iso(now),
            "window": {"days": window_days, "since_utc": since},
            "capture": {"forecasts_archived": int(len(f)), "games": int(f["game_id"].nunique()) if len(f) else 0,
                        **(capture_stats or {})},
            "settlement": {"settled": int((~s["void"].astype(bool)).sum()) if len(s) else 0,
                           "void": int(s["void"].astype(bool).sum()) if len(s) else 0,
                           "corrections": len(_read(root, "corrections", sport))},
            "families": scores, "alerts": alerts,
            "authority": "research_only"}


def render_markdown(card: dict[str, Any], title: str | None = None) -> str:
    """A short, readable daily/weekly research summary (GitHub job summary and the archive)."""
    w = card.get("window", {}).get("days")
    lines = [f"## {title or card['sport'] + ' projection scorecard'}" + (f" — last {w} days" if w else " — season to date"),
             "", f"Generated {card['generated_utc']}. Research only; nothing here is a betting signal.", "",
             f"Archived pregame forecasts: {card['capture']['forecasts_archived']} across {card['capture']['games']} games. "
             f"Settled: {card['settlement']['settled']} (void/DNP {card['settlement']['void']}, "
             f"stat corrections {card['settlement']['corrections']}).", ""]
    if not card["families"]:
        lines.append("_No settled forecasts yet: accuracy will appear once archived games finish._")
        return "\n".join(lines) + "\n"
    lines += ["| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |",
              "|---|---|---|---|---|---|---|---|"]
    for fr in card["families"]:
        p = fr.get("paired", {}).get("crps_diff")
        ptxt = f"{p['mean_diff']:+.4f} [{p['ci95'][0]:+.4f}, {p['ci95'][1]:+.4f}] (n={p['n_obs']})" if p else "—"
        for m in fr["models"]:
            c = m.get("crps")
            cov = m.get("cover_80")
            lines.append(f"| {fr['family']} | {m['model_id']} ({m['model_role']}) | {m['n']} ({m['n_games']}) | "
                         f"{m['mae']:.3f} | {m['bias']:+.3f} | {'—' if c is None else f'{c:.3f}'} | "
                         f"{'—' if cov is None else f'{cov:.0%}'} | {ptxt if m['model_role'] == 'challenger' else ''} |")
    lines += ["", "**Drift checks**", ""]
    for a in card["alerts"]:
        lines.append(f"- {a['family']}: **{a['status']}** — {a['what']}")
    return "\n".join(lines) + "\n"


def write_scorecard(card: dict[str, Any], root: str, tag: str = "latest") -> str:
    d = os.path.join(root, "scorecards", card["sport"].lower())
    os.makedirs(d, exist_ok=True)
    stamp = card["generated_utc"].replace(":", "").replace("-", "")
    for name in (f"{tag}.json", f"{tag}_{stamp[:8]}.json"):  # one dated copy per UTC day (the day's last run wins)
        with open(os.path.join(d, name), "w") as f:
            json.dump(card, f, indent=1, sort_keys=True, default=float)
    with open(os.path.join(d, f"{tag}.md"), "w") as f:
        f.write(render_markdown(card))
    return os.path.join(d, f"{tag}.json")


def reference_from_summary(summary: dict) -> dict[str, dict]:
    """Frozen historical expectations per family from a sport_summary.v1 holdout block."""
    ref = {}
    for f in summary.get("families", []):
        h = f.get("holdout") or {}
        if h:
            ref[f["family"]] = {k: h.get(k) for k in ("mae", "crps", "bias", "cover_80")}
    return ref
