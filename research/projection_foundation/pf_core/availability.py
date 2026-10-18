"""Point-in-time availability observations (`sift.availability.v1`).

One observation = one statement, by one source, captured at one moment, about one player's (or team's) participation or
role for one event: an injury designation, a confirmed or projected lineup slot, a starting pitcher or goalie, a depth-chart
rank, a minutes restriction, a line assignment.

Rules enforced here:
  * every observation carries source, capture timestamp, event identity and the event start; freshness is derived;
  * snapshots are write-once files named by capture time + content hash (never overwritten, never edited);
  * as-of resolution only ever uses observations captured BEFORE the cut-off (and before the event start);
  * certainty is explicit: CONFIRMED (official: posted lineup, confirmed starter, official inactive list) vs REPORTED
    (official injury report designation) vs PROJECTED (third-party or team-projected: projected lines, probable pitcher,
    beat-writer depth chart) vs UNKNOWN;
  * a player with no observation is UNVERIFIED, never "active".
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

SCHEMA = "sift.availability.v1"
KINDS = ("injury", "lineup", "starter", "depth", "rotation", "line", "roster", "restriction")
CERTAINTY = ("CONFIRMED", "REPORTED", "PROJECTED", "UNKNOWN")
# normalised statuses; sources map their vocabulary onto these
STATUSES = ("ACTIVE", "STARTING", "PROBABLE", "QUESTIONABLE", "DOUBTFUL", "OUT", "INACTIVE", "BENCH", "NOT_STARTING",
            "LIMITED", "UNKNOWN")

# participation classes published with every projection
CONFIRMED_ACTIVE, LIKELY_ACTIVE, UNCERTAIN, LIKELY_OUT, CONFIRMED_OUT, UNVERIFIED = (
    "CONFIRMED_ACTIVE", "LIKELY_ACTIVE", "UNCERTAIN", "LIKELY_OUT", "CONFIRMED_OUT", "UNVERIFIED")
PARTICIPATION = (CONFIRMED_ACTIVE, LIKELY_ACTIVE, UNCERTAIN, LIKELY_OUT, CONFIRMED_OUT, UNVERIFIED)


def _ts(x: str | None) -> datetime | None:
    if not x:
        return None
    try:
        d = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Observation:
    sport: str
    event_id: str
    event_start_utc: str
    entity_id: str
    entity_name: str
    team: str
    kind: str                      # one of KINDS
    status: str                    # one of STATUSES
    certainty: str                 # one of CERTAINTY
    source: str                    # e.g. "nba_official_injury_report", "espn_injuries", "dailyfaceoff", "mlb_statsapi"
    captured_utc: str              # when WE fetched it
    source_updated_utc: str | None = None   # when the source says it was last updated (if it says)
    source_url: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)   # slot, line, rank, minutes cap, reason text...
    schema: str = SCHEMA

    def validate(self) -> list[str]:
        e = []
        for k in ("sport", "event_id", "entity_id", "team", "source"):
            if not getattr(self, k):
                e.append(f"missing {k}")
        if self.kind not in KINDS:
            e.append(f"kind {self.kind!r} not in {KINDS}")
        if self.status not in STATUSES:
            e.append(f"status {self.status!r} not in {STATUSES}")
        if self.certainty not in CERTAINTY:
            e.append(f"certainty {self.certainty!r} not in {CERTAINTY}")
        cap, start = _ts(self.captured_utc), _ts(self.event_start_utc)
        if cap is None:
            e.append("captured_utc not parseable")
        if start is None:
            e.append("event_start_utc not parseable")
        su = _ts(self.source_updated_utc) if self.source_updated_utc else None
        if self.source_updated_utc and su is None:
            e.append("source_updated_utc not parseable")
        if su and cap and su > cap:
            e.append("source_updated_utc after captured_utc")
        return e


def observation_from_dict(d: dict) -> Observation:
    keys = Observation.__dataclass_fields__.keys()
    return Observation(**{k: d[k] for k in keys if k in d})


# --------------------------------------------------------------------------- immutable snapshots
def write_snapshot(observations: Iterable[Observation], root: str, sport: str, captured_utc: str) -> dict[str, Any]:
    """Write one capture as a write-once gzip JSONL file: <root>/availability/<sport>/<date>/<HHMMSS>Z_<hash>.jsonl.gz.

    Invalid observations are rejected (counted, not written). Writing the same content twice is a no-op."""
    obs = list(observations)
    good, bad = [], []
    for o in obs:
        (bad if o.validate() else good).append(o)
    rows = [asdict(o) for o in good]
    payload = "\n".join(json.dumps(r, sort_keys=True) for r in rows).encode()
    h = hashlib.sha256(payload).hexdigest()[:12]
    t = _ts(captured_utc) or datetime.now(timezone.utc)
    d = os.path.join(root, "availability", sport.lower(), t.strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    existing = [f for f in os.listdir(d) if f.endswith(f"_{h}.jsonl.gz")]
    if existing or not rows:
        return {"path": os.path.join(d, existing[0]) if existing else None, "written": 0, "rejected": len(bad),
                "duplicate": bool(existing), "errors": [o.validate() for o in bad][:5]}
    path = os.path.join(d, f"{t.strftime('%H%M%S')}Z_{h}.jsonl.gz")
    with gzip.open(path, "xb") as f:   # 'x' = fail if it exists: never overwrite
        f.write(payload)
    return {"path": path, "written": len(rows), "rejected": len(bad), "duplicate": False,
            "errors": [o.validate() for o in bad][:5]}


def load_snapshots(root: str, sport: str, dates: Iterable[str] | None = None) -> list[dict]:
    base = os.path.join(root, "availability", sport.lower())
    if not os.path.isdir(base):
        return []
    days = sorted(dates) if dates else sorted(os.listdir(base))
    out = []
    for day in days:
        d = os.path.join(base, day)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".jsonl.gz"):
                with gzip.open(os.path.join(d, fn), "rt") as f:
                    out += [json.loads(line) for line in f if line.strip()]
    return out


# --------------------------------------------------------------------------- as-of resolution
def asof(rows: list[dict], cutoff_utc: str, event_id: str | None = None) -> dict[tuple, dict]:
    """Latest observation per (event, entity, kind) captured strictly before min(cutoff, event start)."""
    cut = _ts(cutoff_utc)
    best: dict[tuple, dict] = {}
    for r in rows:
        if event_id and r.get("event_id") != event_id:
            continue
        cap, start = _ts(r.get("captured_utc")), _ts(r.get("event_start_utc"))
        if cap is None or cut is None or cap >= cut or (start is not None and cap >= start):
            continue
        k = (r["event_id"], r["entity_id"], r["kind"])
        if k not in best or _ts(best[k]["captured_utc"]) < cap:
            best[k] = r
    return best


@dataclass
class Participation:
    status: str                    # one of PARTICIPATION
    certainty: str                 # strongest certainty behind the call
    source: str | None
    captured_utc: str | None
    age_minutes: float | None      # capture age at the cut-off
    reasons: list[str]

    def gate_injury_status(self) -> str:
        return {CONFIRMED_OUT: "OUT", LIKELY_OUT: "DOUBTFUL", UNCERTAIN: "QUESTIONABLE",
                LIKELY_ACTIVE: "PROBABLE", CONFIRMED_ACTIVE: "ACTIVE", UNVERIFIED: "UNKNOWN"}[self.status]


def resolve_participation(latest: dict[tuple, dict], event_id: str, entity_id: str, cutoff_utc: str,
                          stale_minutes: float = 24 * 60, requires_lineup: bool = False) -> Participation:
    """Combine the latest observations of every kind for one entity into one participation call.

    Precedence: an official OUT/INACTIVE wins; then a CONFIRMED lineup/starter; then the injury designation; then
    PROJECTED lineup/starter/line; nothing -> UNVERIFIED. Observations older than `stale_minutes` at the cut-off are
    demoted one level of certainty. `requires_lineup` (MLB hitters, NHL goalies, NBA starters) means only a CONFIRMED
    lineup/starter observation can produce CONFIRMED_ACTIVE.
    """
    cut = _ts(cutoff_utc)
    obs = [r for (ev, ent, _k), r in latest.items() if ev == event_id and ent == entity_id]
    if not obs:
        return Participation(UNVERIFIED, "UNKNOWN", None, None, None, ["no availability observation before the cut-off"])
    reasons = []

    def age(r):
        return (cut - _ts(r["captured_utc"])).total_seconds() / 60 if cut else None

    def fresh(r):
        a = age(r)
        return a is not None and a <= stale_minutes

    newest = max(obs, key=lambda r: r["captured_utc"])
    # 1 official exclusions
    for r in obs:
        if r["status"] in ("OUT", "INACTIVE") and r["certainty"] in ("CONFIRMED", "REPORTED"):
            st = CONFIRMED_OUT if fresh(r) else LIKELY_OUT
            return Participation(st, r["certainty"], r["source"], r["captured_utc"], age(r),
                                 [f"{r['source']}: {r['status']} ({r['kind']})" + ("" if fresh(r) else ", stale")])
    # 2 confirmed participation
    for r in obs:
        if r["kind"] in ("lineup", "starter", "roster", "rotation") and r["certainty"] == "CONFIRMED" and \
                r["status"] in ("ACTIVE", "STARTING", "BENCH", "LIMITED"):
            if fresh(r):
                return Participation(CONFIRMED_ACTIVE, "CONFIRMED", r["source"], r["captured_utc"], age(r),
                                     [f"{r['source']}: confirmed {r['status'].lower()} ({r['kind']})"])
            reasons.append(f"{r['source']}: confirmed {r['status'].lower()} but stale")
        if r["kind"] in ("lineup", "starter") and r["certainty"] == "CONFIRMED" and r["status"] == "NOT_STARTING":
            reasons.append(f"{r['source']}: confirmed not starting")
    # 3 injury designation
    inj = [r for r in obs if r["kind"] == "injury"]
    if inj:
        r = max(inj, key=lambda x: x["captured_utc"])
        m = {"DOUBTFUL": LIKELY_OUT, "QUESTIONABLE": UNCERTAIN, "LIMITED": UNCERTAIN, "PROBABLE": LIKELY_ACTIVE,
             "ACTIVE": LIKELY_ACTIVE}
        if r["status"] in m:
            st = m[r["status"]]
            if requires_lineup and st == LIKELY_ACTIVE:
                reasons.append("lineup not yet confirmed")
            return Participation(st, r["certainty"], r["source"], r["captured_utc"], age(r),
                                 reasons + [f"{r['source']}: {r['status'].lower()}"])
    # 4 projected participation
    proj = [r for r in obs if r["kind"] in ("lineup", "starter", "line", "depth", "rotation") and
            r["status"] in ("ACTIVE", "STARTING", "PROBABLE", "BENCH")]
    if proj:
        r = max(proj, key=lambda x: x["captured_utc"])
        st = LIKELY_ACTIVE if fresh(r) else UNCERTAIN
        return Participation(st, r["certainty"], r["source"], r["captured_utc"], age(r),
                             reasons + [f"{r['source']}: projected {r['status'].lower()} ({r['kind']})"])
    ns = [r for r in obs if r["status"] == "NOT_STARTING"]
    if ns:
        r = max(ns, key=lambda x: x["captured_utc"])
        return Participation(UNCERTAIN, r["certainty"], r["source"], r["captured_utc"], age(r),
                             reasons or [f"{r['source']}: not starting ({r['certainty'].lower()})"])
    return Participation(UNVERIFIED, newest["certainty"], newest["source"], newest["captured_utc"], age(newest),
                         reasons + ["observations do not establish participation"])


def coverage(rows: list[dict], expected: dict[str, list[str]], cutoff_by_event: dict[str, str],
             now_utc: str | None = None) -> dict[str, Any]:
    """Feed coverage and freshness: for each event, the share of expected entities with any observation before its
    cut-off, and the age of the newest capture. `expected` = event_id -> entity ids we intend to project."""
    now = _ts(now_utc) or datetime.now(timezone.utc)
    per = {}
    for ev, ents in expected.items():
        lat = asof(rows, cutoff_by_event[ev], ev)
        seen = {k[1] for k in lat}
        newest = max((r["captured_utc"] for r in lat.values()), default=None)
        per[ev] = {"expected": len(ents), "observed": len(set(ents) & seen),
                   "coverage": round(len(set(ents) & seen) / len(ents), 4) if ents else None,
                   "newest_capture_utc": newest,
                   "newest_age_minutes": round((now - _ts(newest)).total_seconds() / 60, 1) if newest else None,
                   "sources": sorted({r["source"] for r in lat.values()})}
    covs = [v["coverage"] for v in per.values() if v["coverage"] is not None]
    return {"events": per, "mean_coverage": round(sum(covs) / len(covs), 4) if covs else None, "n_events": len(per)}
