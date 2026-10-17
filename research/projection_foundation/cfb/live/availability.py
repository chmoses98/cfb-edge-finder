"""Point-in-time availability for CFB: ESPN injury listings + the projected starting quarterback.

Observations (pf_core.availability.Observation, written write-once by `write_snapshot`):

  espn_core_injuries    kind=injury   certainty=REPORTED   one per player ESPN lists for the team (status mapped to
                                                           OUT / DOUBTFUL / QUESTIONABLE / PROBABLE)
  espn_core_injuries    kind=roster   certainty=UNKNOWN    one sentinel per (event, team) saying the list WAS read
                        entity "team:<id>"                 (or could not be) and how many records it carried. It
                                                           does not assert anyone is healthy: an empty list is "no
                                                           injuries listed by this source", never "all active".
  pf_cfb_prior_game_passer  kind=starter  certainty=PROJECTED  the leading passer of the team's most recent completed
                                                           game (data/football/<season>/team_games.jsonl). This is OUR
                                                           inference, labelled as such; CFB has no official lineup or
                                                           depth chart feed, so no starter is ever CONFIRMED.

A player without any observation is UNVERIFIED (gate REVIEW, conditional on playing), never active. Empirical note
(recorded sidecar payloads, 2026-09-02..17, 228 teams): ESPN listed an injury for only 5 teams in 16 days, so an
absent listing carries almost no information; it is recorded anyway because it is free, immutable and the only
keyless source. Resolving those 5 recorded references on a GitHub runner (2026-10-17) returned records dated 2022 with status
"Active": stale entries are kept as UNKNOWN observations (see STALE_DAYS) and never establish participation. ESPN serves no CFB
depth chart (…/seasons/2026/teams/<id>/depthcharts answers HTTP 400), so no starter is ever CONFIRMED.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Iterable

from pf_core import availability as av

from . import espn_injuries as ei

SPORT = "CFB"
QB_SOURCE = "pf_cfb_prior_game_passer"
GAMELOG = "data/football/{season}/team_games.jsonl"
FLAG_ORDER = ["QB_OUT_REPORTED", "QB_QUESTIONABLE", "QB_RECENT_CHANGE", "QB_UNKNOWN", "QB_NOT_CONFIRMED"]


def iso(ts) -> str:
    return av.iso(ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00")))


def slug(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def upcoming_events(schedule: dict, now: datetime, horizon_end: datetime, replay: bool = False) -> list[dict]:
    """Not-yet-started, not-completed events with an FBS-conference participant starting after `now` (strict)."""
    out = []
    for e in schedule.get("events", []):
        if not replay and (e.get("completed") or e.get("state") == "post"):
            continue            # replay: the schedule file is newer than `now`, so completion flags are not evidence
        try:
            ko = datetime.fromisoformat(str(e["date"]).replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            continue
        ko = ko if ko.tzinfo else ko.replace(tzinfo=timezone.utc)
        if not (now < ko <= horizon_end):
            continue
        comp = {c["home_away"]: c for c in e.get("competitors", []) if c.get("home_away")}
        if "home" not in comp or "away" not in comp:
            continue
        out.append({"event_id": str(e["id"]), "start_utc": iso(ko), "neutral": bool(e.get("neutral_site")),
                    "home": comp["home"], "away": comp["away"]})
    return sorted(out, key=lambda x: (x["start_utc"], x["event_id"]))


def team_name(c: dict) -> str:
    return c.get("location") or (c.get("names") or [c.get("team_id")])[0]


# --------------------------------------------------------------------------- QB state from the ESPN game log
def qb_state(gamelog_rows: Iterable[dict], now: datetime, max_history: int = 4) -> dict[str, dict]:
    """Per team: the leading passer of its most recent completed game before `now`, the one before, and whether it changed."""
    by_team: dict[str, list[dict]] = {}
    for r in gamelog_rows:
        if r.get("points_for") is None or not r.get("primary_passer"):
            continue
        try:
            ko = datetime.fromisoformat(str(r["kickoff_utc"]).replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            continue
        if ko >= now:
            continue
        by_team.setdefault(str(r["team_id"]), []).append({"ko": ko, "game_id": str(r["game_id"]), "name": r["primary_passer"],
                                                        "att": r.get("primary_passer_att"), "team": r.get("team")})
    out = {}
    for t, rows in by_team.items():
        rows.sort(key=lambda x: x["ko"])
        last, prev = rows[-1], (rows[-2] if len(rows) > 1 else None)
        out[t] = {"team_id": t, "team": last["team"], "name": last["name"], "last_game_id": last["game_id"],
                  "last_game_utc": iso(last["ko"]), "prev_name": prev["name"] if prev else None,
                  "changed_last_game": bool(prev and slug(prev["name"]) != slug(last["name"])),
                  "games": len(rows), "history": [x["name"] for x in rows[-max_history:]]}
    return out


# --------------------------------------------------------------------------- observations
STALE_DAYS = 14


def build_observations(*, captured_utc: str, events: list[dict], injuries: dict[str, ei.TeamInjuries], qb: dict[str, dict],
                       id_lookup: dict[tuple[str, str], str] | None = None, season: int = 2026,
                       stale_days: int = STALE_DAYS) -> list[av.Observation]:
    id_lookup = id_lookup or {}
    cap = datetime.fromisoformat(captured_utc.replace("Z", "+00:00"))
    obs: list[av.Observation] = []
    for ev in events:
        for side in ("home", "away"):
            c = ev[side]
            tid, tname = str(c["team_id"]), team_name(c)
            common = dict(sport=SPORT, event_id=ev["event_id"], event_start_utc=ev["start_utc"], team=tname, captured_utc=captured_utc)
            inj = injuries.get(tid)
            obs.append(av.Observation(**common, entity_id=f"team:{tid}", entity_name=tname, kind="roster", status="UNKNOWN",
                                      certainty="UNKNOWN", source=ei.SOURCE, source_url=ei.team_url(tid),
                                      detail={"injury_list_read": bool(inj and inj.ok), "listed": inj.listed if inj else None,
                                              "unresolved": inj.unresolved if inj else None, "error": None if inj is None else inj.error,
                                              "meaning": "list read; entries are injury designations only, absence is not confirmation"}))
            for r in (inj.records if inj and inj.ok else []):
                if not r.athlete_id:
                    continue
                su, d = None, None
                if r.date:
                    try:
                        d = datetime.fromisoformat(str(r.date).replace("Z", "+00:00"))
                        d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
                        su = iso(d) if d <= cap else None
                    except Exception:  # noqa: BLE001
                        su, d = None, None
                # ESPN's CFB list keeps old records: the live feed returned 2022-dated entries with status "Active" in 2026. A record
                # whose own date is missing or older than `stale_days` before the capture establishes nothing about this game.
                age_days = (cap - d).total_seconds() / 86400 if su else None
                stale = age_days is None or age_days > stale_days
                known = r.resolved and r.status != "UNKNOWN" and not stale   # an unreadable/stale record proves only that ESPN lists SOMETHING
                obs.append(av.Observation(**common, entity_id=str(r.athlete_id), entity_name=r.athlete_name or str(r.athlete_id),
                                          kind="injury", status=r.status if known else "UNKNOWN", certainty="REPORTED" if known else "UNKNOWN", source=ei.SOURCE,
                                          source_url=r.ref or ei.team_url(tid), source_updated_utc=su,
                                          detail={"status_raw": r.status_raw, "position": r.position, "injury_id": r.injury_id,
                                                  "detail": r.detail, "record_resolved": r.resolved, "record_date": r.date,
                                                  "stale": bool(stale), "age_days": None if age_days is None else round(age_days, 1)}))
            q = qb.get(tid)
            if q:
                pid = id_lookup.get((tid, q["name"])) or f"name:{tid}:{slug(q['name'])}"
                obs.append(av.Observation(**common, entity_id=str(pid), entity_name=q["name"], kind="starter", status="STARTING",
                                          certainty="PROJECTED", source=QB_SOURCE, source_url=GAMELOG.format(season=season),
                                          source_updated_utc=q["last_game_utc"],
                                          detail={"basis": "leading passer in the team's most recent completed game",
                                                  "last_game_id": q["last_game_id"], "previous_passer": q["prev_name"],
                                                  "changed_last_game": q["changed_last_game"], "games_of_evidence": q["games"],
                                                  "official": False}))
    return obs


REPO_SOURCE = "repo_availability_json"


def repo_job_observations(avail: dict, events: list[dict], archived_max_captured: str | None, season: int = 2026) -> list[av.Observation]:
    """Reuse the production data job's own capture (scripts/collect_football_gamelog.py -> data/football/<season>/availability.json).

    That file is overwritten on main every ~2 hours, so its history lives only in git; ingesting each NEW `observed_at` into the
    immutable archive preserves it. Its parser reads ESPN's `items` as inline records, but ESPN returns reference URLs, so its
    entries carry no athlete/status (qb_status is None for all teams as of 2026-10-17); we record exactly what it says, never more.
    A QB status, if the job ever resolves one, is stored as a REPORTED injury observation."""
    out: list[av.Observation] = []
    for ev in events:
        for side in ("home", "away"):
            c = ev[side]
            tid, tname = str(c["team_id"]), team_name(c)
            a = avail.get(tid)
            if not a or a.get("status") != "OBSERVED" or not a.get("observed_at"):
                continue
            if archived_max_captured and a["observed_at"] <= archived_max_captured:
                continue                                        # already preserved by an earlier run
            if a["observed_at"] >= ev["start_utc"]:
                continue
            common = dict(sport=SPORT, event_id=ev["event_id"], event_start_utc=ev["start_utc"], team=tname, captured_utc=a["observed_at"],
                          source=REPO_SOURCE, source_url=f"data/football/{season}/availability.json")
            items = a.get("items") or []
            out.append(av.Observation(**common, entity_id=f"team:{tid}", entity_name=tname, kind="roster", status="UNKNOWN", certainty="UNKNOWN",
                                      detail={"items": len(items), "items_with_athlete": sum(1 for i in items if i.get("athlete")),
                                              "qb_uncertain": a.get("qb_uncertain"), "producer": "scripts/collect_football_gamelog.py"}))
            if a.get("qb_name") and a.get("qb_status"):
                out.append(av.Observation(**common, entity_id=f"name:{tid}:{slug(a['qb_name'])}", entity_name=a["qb_name"], kind="injury",
                                          status=ei.normalise_status(a["qb_status"]), certainty="REPORTED",
                                          detail={"status_raw": a["qb_status"], "position": "QB"}))
    return out


def qb_entity_id(team_id: str, q: dict | None, id_lookup: dict | None = None) -> str | None:
    if not q:
        return None
    return str((id_lookup or {}).get((str(team_id), q["name"])) or f"name:{team_id}:{slug(q['name'])}")


# --------------------------------------------------------------------------- QB flag
def qb_assessment(team_id: str, event_id: str, q: dict | None, latest: dict, now_utc: str, id_lookup: dict | None = None) -> dict:
    """The projected starting QB for one team in one game: participation as of `now_utc` and the flag SIFT surfaces.

    flag precedence: QB_OUT_REPORTED > QB_QUESTIONABLE > QB_RECENT_CHANGE > QB_UNKNOWN > QB_NOT_CONFIRMED. No starter is
    ever CONFIRMED in this feed, so every game carries at least QB_NOT_CONFIRMED."""
    if not q:
        return {"name": None, "player_id": None, "status": av.UNVERIFIED, "certainty": "UNKNOWN", "source": None,
                "flag": "QB_UNKNOWN", "confirmed": False, "changed_last_game": None, "gate": "REVIEW",
                "reasons": ["no completed game this season identifies a starting quarterback"]}
    pid = qb_entity_id(team_id, q, id_lookup)
    p = av.resolve_participation(latest, event_id, pid, now_utc)
    # an injury listing on the QB is stored under his athlete id; fall back to the projected-starter entity above
    flag, gate = "QB_NOT_CONFIRMED", "PASS"
    if p.status == av.CONFIRMED_OUT:
        flag, gate = "QB_OUT_REPORTED", "REVIEW"
    elif p.status in (av.LIKELY_OUT, av.UNCERTAIN):
        flag, gate = "QB_QUESTIONABLE", "REVIEW"
    elif q["changed_last_game"]:
        flag, gate = "QB_RECENT_CHANGE", "REVIEW"
    elif p.status == av.UNVERIFIED:
        flag, gate = "QB_UNKNOWN", "REVIEW"
    if p.status == av.CONFIRMED_ACTIVE:
        flag, gate = ("QB_RECENT_CHANGE", "REVIEW") if q["changed_last_game"] else ("OK", "PASS")
    reasons = list(p.reasons)
    if q["changed_last_game"]:
        reasons.append(f"leading passer changed last game ({q['prev_name']} -> {q['name']}); historically the team is over-projected by 2-3 points after a change")
    return {"name": q["name"], "player_id": pid, "status": p.status, "certainty": p.certainty, "source": p.source,
            "captured_utc": p.captured_utc, "flag": flag, "confirmed": p.status == av.CONFIRMED_ACTIVE,
            "changed_last_game": q["changed_last_game"], "gate": gate, "reasons": reasons}


def worst_flag(flags: Iterable[str]) -> str:
    fs = [f for f in flags if f in FLAG_ORDER]
    return min(fs, key=FLAG_ORDER.index) if fs else "OK"


def capture_stats(rows: list[dict], events: list[dict], expected: dict[str, list[str]], now_utc: str,
                  injuries: dict[str, ei.TeamInjuries] | None = None) -> dict:
    """pf_core coverage (player-level, vs the entities we intend to project) + feed health (team-level)."""
    cutoff = {e["event_id"]: e["start_utc"] for e in events}
    cov = av.coverage(rows, {k: v for k, v in expected.items() if k in cutoff}, {k: cutoff[k] for k in expected if k in cutoff}, now_utc)
    n_teams = len(injuries or {})
    ok = sum(1 for v in (injuries or {}).values() if v.ok)
    listed = sum(v.listed for v in (injuries or {}).values() if v.ok)
    return {"coverage": cov["mean_coverage"], "n_events": cov["n_events"],
            "newest_age_minutes_max": max([v["newest_age_minutes"] for v in cov["events"].values() if v["newest_age_minutes"] is not None], default=None),
            "team_feed": {"teams_requested": n_teams, "lists_read": ok, "lists_failed": n_teams - ok, "injury_records_listed": listed,
                          "teams_with_any_listing": sum(1 for v in (injuries or {}).values() if v.ok and v.listed),
                          "records_unresolved": sum(v.unresolved for v in (injuries or {}).values()),
                          "sample_record_keys": next((v.sample_keys for v in (injuries or {}).values() if v.sample_keys), [])}}
