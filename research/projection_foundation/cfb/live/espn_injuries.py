"""ESPN core API injury lists, resolved into one record per listed player.

    GET {CORE}/teams/{team_id}/injuries           -> {"count": n, "items": [<ref>, ...]}
    GET <ref>   (…/seasons/{yr}/athletes/{athlete_id}/injuries/{injury_id})   -> the injury record

Why this module exists: the repo's existing capture (scripts/collect_football_gamelog.availability_for) reads
`payload["items"]` as if the entries were injury records. Recorded payloads from the research-sidecar branch (9,510
real team fetches, 2026-09-02..17) show `items` is a list of REFERENCE URLs (strings): the old capture dropped every
non-dict entry, so `qb_status` was None for every team in data/football/2026/availability.json whatever the real
status. This module resolves the references. It never replaces that script (production data job is untouched).

Transport is injected (`get(url) -> (payload | None, error | None)`) so tests run on recorded payloads; the default
uses urllib with retries and no third-party dependency.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"
SOURCE = "espn_core_injuries"
USER_AGENT = "sift-projection-foundation/1.0 (research; keyless)"
Get = Callable[[str], "tuple[dict | list | None, str | None]"]
_ATHLETE_RE = re.compile(r"/athletes/(\d+)")
_INJURY_RE = re.compile(r"/injuries/(\d+)")


def team_url(team_id: str) -> str:
    return f"{CORE}/teams/{team_id}/injuries"


def http_get_json(url: str, timeout: float = 20.0, retries: int = 2) -> tuple[object | None, str | None]:
    """One GET, never raises: a failure is (None, reason) the caller records."""
    last = "no attempt"
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404):
                return None, last
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        time.sleep(min(2 ** attempt, 4))
    return None, last


@dataclass
class InjuryRecord:
    ref: str | None
    athlete_id: str | None
    injury_id: str | None
    status_raw: str | None          # e.g. "Out", "Questionable", "Day-To-Day"
    status: str                     # normalised to pf_core.availability.STATUSES
    athlete_name: str | None = None
    position: str | None = None
    date: str | None = None         # ISO date the source lists for the record, if any
    detail: str | None = None
    resolved: bool = True           # False when only the reference could be read


@dataclass
class TeamInjuries:
    team_id: str
    url: str
    ok: bool                        # the list endpoint answered with a readable payload
    error: str | None
    listed: int                     # how many references/records the list carried
    records: list[InjuryRecord] = field(default_factory=list)
    unresolved: int = 0
    sample_keys: list[str] = field(default_factory=list)   # keys of the first resolved record (logged for verification)


def normalise_status(raw: str | None) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return "UNKNOWN"
    if any(k in s for k in ("out", "suspen", "injured reserve", "season", "dismiss", "inactive")):
        return "OUT"
    if "doubt" in s:
        return "DOUBTFUL"
    if any(k in s for k in ("question", "day-to-day", "day to day", "game-time", "game time", "gtd")):
        return "QUESTIONABLE"
    if any(k in s for k in ("probable", "available", "active")):
        return "PROBABLE"
    return "UNKNOWN"


def _ref_of(x) -> str | None:
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return x.get("$ref") or x.get("href")
    return None


def parse_record(payload: dict, ref: str | None = None) -> InjuryRecord:
    """Tolerant parse of one injury record (field names follow ESPN's core schema; absent fields stay None)."""
    ath = payload.get("athlete") if isinstance(payload.get("athlete"), dict) else {}
    ath_ref = _ref_of(payload.get("athlete")) or ref or ""
    m = _ATHLETE_RE.search(ath_ref) or _ATHLETE_RE.search(ref or "")
    athlete_id = str(ath.get("id")) if ath.get("id") else (m.group(1) if m else None)
    t = payload.get("type") if isinstance(payload.get("type"), dict) else {}
    raw = payload.get("status") if isinstance(payload.get("status"), str) else None
    raw = raw or t.get("description") or t.get("name") or t.get("abbreviation")
    pos = ath.get("position") if isinstance(ath.get("position"), dict) else {}
    det = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    im = _INJURY_RE.search(ref or "")
    return InjuryRecord(ref=ref, athlete_id=athlete_id, injury_id=str(payload.get("id")) if payload.get("id") else (im.group(1) if im else None),
                        status_raw=raw, status=normalise_status(raw),
                        athlete_name=ath.get("displayName") or ath.get("fullName"), position=pos.get("abbreviation"),
                        date=payload.get("date"), detail=det.get("type") or payload.get("shortComment"))


def fetch_team(team_id: str, get: Get = http_get_json, resolve: bool = True) -> TeamInjuries:
    url = team_url(team_id)
    payload, err = get(url)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return TeamInjuries(team_id, url, False, err or "payload carried no items list", 0)
    out = TeamInjuries(team_id, url, True, None, len(payload["items"]))
    for it in payload["items"]:
        if isinstance(it, dict) and ("status" in it or "type" in it) and "$ref" not in it:
            rec = parse_record(it, None)                          # inline record
        else:
            ref = _ref_of(it)
            m = _ATHLETE_RE.search(ref or "")
            if not ref:
                out.unresolved += 1
                continue
            body, e2 = get(ref) if resolve else (None, "resolution skipped")
            if isinstance(body, dict):
                rec = parse_record(body, ref)
                if not out.sample_keys:
                    out.sample_keys = sorted(body.keys())[:20]
            else:
                # the reference itself still tells us an injury record exists for this athlete
                rec = InjuryRecord(ref, m.group(1) if m else None, (_INJURY_RE.search(ref) or [None, None])[1] if _INJURY_RE.search(ref) else None,
                                   None, "UNKNOWN", resolved=False)
                out.unresolved += 1
        out.records.append(rec)
    return out


def fetch_teams(team_ids, get: Get = http_get_json, workers: int = 8) -> dict[str, TeamInjuries]:
    ids = sorted({str(t) for t in team_ids})
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(zip(ids, ex.map(lambda t: fetch_team(t, get), ids)))
