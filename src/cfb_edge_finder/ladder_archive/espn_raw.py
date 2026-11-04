"""Raw POSTGAME ESPN summary archive with an extraction index (drives, kicking, field-goal distances).

The ORIGINAL response bytes are stored (gzip) and hashed; the index row is derived from them and records exactly what the payload did
or did not contain. A summary describes a game that already happened: `available_from` is OUR fetch time, so these records may only
inform LATER games whose prediction cut-off is after `available_from`. They are never pregame evidence for the game they describe.

Nothing is inferred: an FG whose distance is not in the play text keeps distance=None with a reason; duplicate play ids are flagged;
box-score kicking totals are reconciled against the FG plays; scores are reconciled against the play log."""
from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any, Callable

import requests

from .archive_io import canonical, sha256

SCHEMA = "cfb_espn_raw_index/1.0"
HOSTS = ("https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary", "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/summary")
MAX_BYTES = 6_000_000
FG_DIST = re.compile(r"field goal attempt from\s+(\d+)\s+yards?", re.I)
FG_DIST_ALT = re.compile(r"(\d+)\s+yd\s+field goal", re.I)          # scoring-play phrasing: "Nolan Hauser 29 Yd Field Goal"
FG_RESULT = (("blocked", "BLOCKED"), ("no good", "NO_GOOD"), ("good", "GOOD"), ("missed", "NO_GOOD"))


def now_iso(clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> str:
    return clock().strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_raw(event_id: str, http_get: Callable[..., Any] = requests.get, sleep: Callable[[float], None] = time.sleep, attempts: int = 3) -> dict[str, Any]:
    """Returns {ok, status, uri, body(bytes)|None, fetched_at, error}. Bounded retries on 429/5xx/transport errors; never raises."""
    last: dict[str, Any] = {"ok": False, "status": None, "uri": None, "body": None, "fetched_at": None, "error": "no attempt"}
    for host in HOSTS:
        uri = f"{host}?event={event_id}"
        for attempt in range(attempts):
            try:
                r = http_get(host, params={"event": event_id}, headers={"Accept": "application/json"}, timeout=25)
            except requests.RequestException as exc:
                last = {"ok": False, "status": None, "uri": uri, "body": None, "fetched_at": now_iso(), "error": f"{type(exc).__name__}: {exc}"[:200]}
                sleep(min(2 ** attempt, 8))
                continue
            fetched = now_iso()
            body = r.content
            if r.status_code == 200 and len(body) <= MAX_BYTES:
                return {"ok": True, "status": 200, "uri": uri, "body": body, "fetched_at": fetched, "error": None}
            last = {"ok": False, "status": r.status_code, "uri": uri, "body": None, "fetched_at": fetched, "error": "oversize" if r.status_code == 200 else f"HTTP {r.status_code}"}
            if r.status_code == 429 or r.status_code >= 500:
                sleep(min(2 ** attempt, 8))
                continue
            break
    return last


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def kicking_totals(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """team_id -> {fg_made, fg_att, long, xp, kickers:[{athlete_id, fg}]} from boxscore.players[].statistics[name=kicking]; absent => not present."""
    out: dict[str, dict[str, Any]] = {}
    for team_block in (summary.get("boxscore") or {}).get("players") or []:
        tid = str((team_block.get("team") or {}).get("id") or "")
        for cat in team_block.get("statistics") or []:
            if cat.get("name") != "kicking":
                continue
            labels, totals = cat.get("labels") or [], cat.get("totals") or []
            fg = str(totals[labels.index("FG")]) if "FG" in labels and len(totals) > labels.index("FG") else None
            made = att = None
            if fg and "/" in fg:
                a, b = fg.split("/", 1)
                made, att = _int(a), _int(b)
            out[tid] = {"fg_made": made, "fg_att": att, "long": _int(totals[labels.index("LONG")]) if "LONG" in labels and len(totals) > labels.index("LONG") else None,
                        "kickers": [{"athlete_id": str((a.get("athlete") or {}).get("id") or ""), "fg": (a.get("stats") or [None])[labels.index("FG")] if "FG" in labels and len(a.get("stats") or []) > labels.index("FG") else None}
                                    for a in cat.get("athletes") or []]}
    return out


def fg_attempts(summary: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Field-goal attempts from the drive play log. distance only from the play text; duplicates by play id are kept out and flagged."""
    out, flags, seen = [], [], set()
    for drive in (summary.get("drives") or {}).get("previous") or []:
        team = str((drive.get("team") or {}).get("id") or "")
        for play in drive.get("plays") or []:
            ttext = ((play.get("type") or {}).get("text") or "")
            text = play.get("text") or ""
            if "field goal" not in ttext.lower() and "field goal attempt" not in text.lower():
                continue
            pid = str(play.get("id") or "")
            if pid and pid in seen:
                flags.append(f"DUPLICATE_FG_PLAY_ID:{pid}")
                continue
            seen.add(pid)
            m = FG_DIST.search(text)
            alt = None if m else FG_DIST_ALT.search(text)
            low = (ttext + " " + text).lower()
            result = next((v for k, v in FG_RESULT if k in low), "UNKNOWN")
            out.append({"play_id": pid, "team_id": team, "period": _int((play.get("period") or {}).get("number")), "clock": (play.get("clock") or {}).get("displayValue"),
                        "distance_yards": int((m or alt).group(1)) if (m or alt) else None, "distance_source": "play_text" if m else ("play_text_scoring_phrase" if alt else None),
                        "distance_missing_reason": None if (m or alt) else "NO_DISTANCE_IN_PLAY_TEXT",
                        "result": result, "type_text": ttext[:40]})
    return out, flags


def drive_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, d in enumerate((summary.get("drives") or {}).get("previous") or []):
        s, e = d.get("start") or {}, d.get("end") or {}
        rows.append({"seq": i, "drive_id": str(d.get("id") or ""), "team_id": str((d.get("team") or {}).get("id") or ""), "result": d.get("result"), "is_score": d.get("isScore"),
                     "offensive_plays": d.get("offensivePlays"), "yards": d.get("yards"), "time_elapsed": (d.get("timeElapsed") or {}).get("displayValue"),
                     "start_period": _int((s.get("period") or {}).get("number")), "start_clock": (s.get("clock") or {}).get("displayValue"), "start_text": s.get("text"),
                     "end_period": _int((e.get("period") or {}).get("number")), "n_plays_logged": len(d.get("plays") or [])})
    return rows


def final_scores(summary: dict[str, Any]) -> dict[str, Any]:
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    out: dict[str, Any] = {"completed": ((comp.get("status") or {}).get("type") or {}).get("completed"), "teams": {}}
    for c in comp.get("competitors") or []:
        out["teams"][str((c.get("team") or {}).get("id") or c.get("id") or "")] = {"home_away": c.get("homeAway"), "score": _int(c.get("score")),
                                                                                    "linescores": [_int(x.get("displayValue")) if isinstance(x, dict) else _int(x) for x in c.get("linescores") or []]}
    return out


def last_play_scores(summary: dict[str, Any]) -> tuple[int | None, int | None]:
    last = None
    for d in (summary.get("drives") or {}).get("previous") or []:
        for p in d.get("plays") or []:
            if p.get("homeScore") is not None and p.get("awayScore") is not None:
                last = p
    return (_int(last.get("homeScore")), _int(last.get("awayScore"))) if last else (None, None)


def index_row(event_id: str, fetch: dict[str, Any], schedule_row: dict[str, Any] | None, run_id: str) -> dict[str, Any]:
    import json
    row: dict[str, Any] = {"schema": SCHEMA, "run_id": run_id, "event_id": str(event_id), "source_uri": fetch.get("uri"), "http_status": fetch.get("status"), "fetched_at": fetch.get("fetched_at"),
                           "available_from": fetch.get("fetched_at"), "pregame_evidence": False, "usage_rule": "postgame; may inform only games whose prediction cut-off is after available_from",
                           "kickoff_utc": (schedule_row or {}).get("date"), "schedule_state": (schedule_row or {}).get("state"), "missingness": []}
    if not fetch.get("ok"):
        row.update(fetch_ok=False, error=fetch.get("error"), sha256=None, bytes=None)
        row["missingness"].append("FETCH_FAILED")
        return row
    body = fetch["body"]
    row.update(fetch_ok=True, sha256=sha256(body), bytes=len(body))
    try:
        s = json.loads(body)
    except ValueError:
        row["missingness"].append("PAYLOAD_NOT_JSON")
        return row
    drives = drive_summary(s)
    fgs, flags = fg_attempts(s)
    kick = kicking_totals(s)
    fs = final_scores(s)
    hs, as_ = last_play_scores(s)
    miss = row["missingness"]
    if not drives:
        miss.append("NO_DRIVES")
    if not kick:
        miss.append("NO_KICKING_CATEGORY")
    if fgs and any(f["distance_yards"] is None for f in fgs):
        miss.append("FG_DISTANCE_MISSING_FOR_SOME_ATTEMPTS")
    if any(f.startswith("DUPLICATE") for f in flags):
        miss.append("DUPLICATE_FG_PLAYS")
    if fs.get("completed") is not True:
        miss.append("GAME_NOT_COMPLETED_IN_PAYLOAD")
    row.update(final_state=fs, n_drives=len(drives), drives=drives, first_drive_by_team={t: next((d for d in drives if d["team_id"] == t), None) for t in {d["team_id"] for d in drives}},
               n_plays_logged=sum(d["n_plays_logged"] for d in drives), fg_attempts=fgs, fg_flags=flags, kicking_box=kick)
    rec: dict[str, Any] = {}
    box_att = sum(v["fg_att"] for v in kick.values() if v.get("fg_att") is not None) if kick else None
    rec["fg_plays_vs_box_attempts"] = {"plays": len(fgs), "box": box_att, "match": (box_att == len(fgs)) if box_att is not None else None}
    scores = {t: v["score"] for t, v in fs["teams"].items()}
    home = next((t for t, v in fs["teams"].items() if v["home_away"] == "home"), None)
    away = next((t for t, v in fs["teams"].items() if v["home_away"] == "away"), None)
    rec["last_play_score_vs_final"] = {"play": [hs, as_], "final": [scores.get(home), scores.get(away)], "match": (hs == scores.get(home) and as_ == scores.get(away)) if hs is not None and home and away else None}
    rec["period_score_sums_vs_final"] = {t: {"linescore_sum": sum(x for x in v["linescores"] if x is not None) if v["linescores"] else None, "final": v["score"]} for t, v in fs["teams"].items()}
    row["reconciliation"] = rec
    if rec["fg_plays_vs_box_attempts"]["match"] is False:
        miss.append("FG_PLAYS_DISAGREE_WITH_BOX_ATTEMPTS")
    if rec["last_play_score_vs_final"]["match"] is False:
        miss.append("PLAY_LOG_SCORE_DISAGREES_WITH_FINAL")
    return row
