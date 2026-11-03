"""Period (quarter) scoring backfill, with provenance, validation, duplicate detection and quarantine.

One record per game, built ONLY from the venue's own per-period linescore (ESPN summary header). Nothing here
estimates a period: a game with no usable linescore produces no record, and 1Q/1H pricing stays refused.

RECORD (``cfb_period_scores/1``): game id, season, kickoff, home/away team ids, per-team period points,
final scores, and the provenance -- source name, endpoint, event id, fetch time, sha256 of the payload and of the
header subtree the numbers were read from. A record carries its own validation verdict.

STORE: append-only JSONL, never rewritten.
  * ``period_scores.jsonl``            valid records, one per game (and per home/away/kickoff identity)
  * ``period_scores_quarantine.jsonl`` everything refused, with the reason and (for conflicts) what it conflicts with
Ingest outcomes: ``NEW`` / ``DUPLICATE_NOOP`` (same game, same numbers) / ``CONFLICT_QUARANTINED`` (same game,
different numbers: the existing record is kept, the newcomer is quarantined, a human decides) / ``INVALID_QUARANTINED``.
A second ingest of the same payloads writes nothing.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cfb_edge_finder.scripting.gamelog import TeamGame

SCHEMA = "cfb_period_scores/1"
SOURCE_NAME = "espn.summary.header.linescores"
ENDPOINT = "site.api.espn.com/apis/site/v2/sports/football/college-football/summary"

#: Plausibility bounds. Owner-adjustable; a value outside them is quarantined for a human, not silently dropped.
MAX_POINTS_PER_PERIOD = 50
MAX_TEAM_POINTS = 120
MIN_REGULATION_PERIODS = 4

NEW = "NEW"
DUPLICATE_NOOP = "DUPLICATE_NOOP"
CONFLICT_QUARANTINED = "CONFLICT_QUARANTINED"
INVALID_QUARANTINED = "INVALID_QUARANTINED"


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_of(obj: Any) -> str:
    return hashlib.sha256(_canon(obj)).hexdigest()


@dataclass(frozen=True)
class PeriodRecord:
    game_id: str
    season: int
    kickoff_utc: str
    home_team_id: str
    away_team_id: str
    home_periods: tuple[float, ...]
    away_periods: tuple[float, ...]
    home_final: int | None
    away_final: int | None
    source: dict[str, str]
    fetched_at: str
    payload_sha256: str
    header_sha256: str
    issues: tuple[str, ...] = field(default_factory=tuple)
    schema: str = SCHEMA

    @property
    def valid(self) -> bool:
        return not self.issues

    @property
    def identity(self) -> str:
        return f"{self.kickoff_utc[:10]}|{self.home_team_id}|{self.away_team_id}"

    def numbers(self) -> tuple[Any, ...]:
        return (self.home_periods, self.away_periods, self.home_final, self.away_final)

    def as_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        for k in ("home_periods", "away_periods", "issues"):
            d[k] = list(d[k])
        d["valid"] = self.valid
        return d


def _periods(raw: Any) -> tuple[tuple[float, ...] | None, str | None]:
    if not isinstance(raw, list) or not raw:
        return None, "MISSING_LINESCORES"
    out: list[float] = []
    for entry in raw:
        text = entry.get("displayValue") if isinstance(entry, dict) else entry
        try:
            n = float(text)
        except (TypeError, ValueError):
            return None, "NON_NUMERIC_PERIOD"
        if n < 0 or n != int(n):
            return None, "NEGATIVE_OR_NONINTEGER_PERIOD"
        out.append(n)
    return tuple(out), None


def validate(
    home: tuple[float, ...] | None,
    away: tuple[float, ...] | None,
    home_final: int | None,
    away_final: int | None,
    *,
    expected: tuple[str, str] | None = None,
    got: tuple[str, str] | None = None,
    first_issue: tuple[str | None, str | None] = (None, None),
) -> tuple[str, ...]:
    issues: list[str] = [i for i in first_issue if i]
    if home is None or away is None:
        return tuple(sorted(set(issues or ["MISSING_LINESCORES"])))
    if home_final is None or away_final is None:
        issues.append("MISSING_FINAL_SCORE")
    if len(home) < MIN_REGULATION_PERIODS or len(away) < MIN_REGULATION_PERIODS:
        issues.append("FEWER_THAN_4_PERIODS")
    if len(home) != len(away):
        issues.append("PERIOD_COUNT_DIFFERS_HOME_AWAY")
    if home_final is not None and int(sum(home)) != int(home_final):
        issues.append("HOME_PERIOD_SUM_MISMATCH")
    if away_final is not None and int(sum(away)) != int(away_final):
        issues.append("AWAY_PERIOD_SUM_MISMATCH")
    if (
        any(v > MAX_POINTS_PER_PERIOD for v in (*home, *away))
        or sum(home) > MAX_TEAM_POINTS
        or sum(away) > MAX_TEAM_POINTS
    ):
        issues.append("IMPLAUSIBLE_POINTS")
    if expected is not None and got is not None and expected != got:
        issues.append("TEAM_MAPPING_MISMATCH")
    return tuple(sorted(set(issues)))


def record_from_summary(
    summary: dict[str, Any],
    *,
    fetched_at: str,
    expected_teams: tuple[str, str] | None = None,
) -> PeriodRecord | None:
    """Read one ESPN summary. Returns None when it is not a completed two-team game (nothing to record).

    ``expected_teams`` = (home_team_id, away_team_id) from the game catalog / team_games rows; a mismatch is
    quarantined as TEAM_MAPPING_MISMATCH, never repaired by swapping.
    """
    header = summary.get("header") or {}
    comps = header.get("competitions") or []
    if not comps:
        return None
    comp = comps[0]
    status = (comp.get("status") or {}).get("type") or {}
    if status and not status.get("completed", True):
        return None
    competitors = [c for c in comp.get("competitors") or [] if isinstance(c, dict)]
    by_side = {c.get("homeAway"): c for c in competitors}
    if len(competitors) != 2 or set(by_side) != {"home", "away"}:
        return None

    def team_id(c: dict[str, Any]) -> str:
        return str((c.get("team") or {}).get("id") or c.get("id") or "")

    def final(c: dict[str, Any]) -> int | None:
        try:
            return int(float(c.get("score")))
        except (TypeError, ValueError):
            return None

    hp, herr = _periods(by_side["home"].get("linescores"))
    ap, aerr = _periods(by_side["away"].get("linescores"))
    hf, af = final(by_side["home"]), final(by_side["away"])
    got = (team_id(by_side["home"]), team_id(by_side["away"]))
    issues = validate(hp, ap, hf, af, expected=expected_teams, got=got, first_issue=(herr, aerr))
    kickoff = str(comp.get("date") or "")
    if len(kickoff) == 17:
        kickoff = kickoff[:-1] + ":00Z"
    event_id = str(header.get("id") or comp.get("id") or "")
    return PeriodRecord(
        game_id=event_id,
        season=int(((header.get("season") or {}).get("year")) or 0),
        kickoff_utc=kickoff,
        home_team_id=got[0],
        away_team_id=got[1],
        home_periods=hp or (),
        away_periods=ap or (),
        home_final=hf,
        away_final=af,
        source={"name": SOURCE_NAME, "endpoint": ENDPOINT, "event_id": event_id},
        fetched_at=fetched_at,
        payload_sha256=sha256_of(summary),
        header_sha256=sha256_of(header),
        issues=issues,
    )


class PeriodStore:
    """Append-only valid + quarantine files. Idempotent: re-ingesting the same records writes nothing."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.valid_path = self.dir / "period_scores.jsonl"
        self.quarantine_path = self.dir / "period_scores_quarantine.jsonl"
        self._by_game: dict[str, dict[str, Any]] = {}
        self._by_identity: dict[str, dict[str, Any]] = {}
        self._quarantined: set[tuple[str, str]] = set()
        self._load()

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _load(self) -> None:
        for rec in self._read(self.valid_path):
            self._by_game[rec["game_id"]] = rec
            self._by_identity[f"{rec['kickoff_utc'][:10]}|{rec['home_team_id']}|{rec['away_team_id']}"] = rec
        for rec in self._read(self.quarantine_path):
            self._quarantined.add((rec["game_id"], rec["payload_sha256"]))

    def _append(self, path: Path, rec: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")

    def _quarantine(self, record: PeriodRecord, reason: str, conflicts_with: dict[str, Any] | None) -> str:
        key = (record.game_id, record.payload_sha256)
        if key not in self._quarantined:
            d = record.as_dict()
            d["quarantine_reason"] = reason
            if conflicts_with is not None:
                d["conflicts_with"] = {k: conflicts_with[k] for k in ("game_id", "payload_sha256", "fetched_at")}
            self._append(self.quarantine_path, d)
            self._quarantined.add(key)
        return CONFLICT_QUARANTINED if conflicts_with is not None else INVALID_QUARANTINED

    def ingest(self, record: PeriodRecord) -> str:
        if not record.valid:
            return self._quarantine(record, ";".join(record.issues), None)
        for existing in (self._by_game.get(record.game_id), self._by_identity.get(record.identity)):
            if existing is None:
                continue
            same = (
                tuple(existing["home_periods"]) == record.home_periods
                and tuple(existing["away_periods"]) == record.away_periods
                and existing["home_final"] == record.home_final
                and existing["away_final"] == record.away_final
            )
            if same:
                return DUPLICATE_NOOP
            reason = (
                "SAME_GAME_DIFFERENT_NUMBERS"
                if existing["game_id"] == record.game_id
                else ("SAME_MATCHUP_DIFFERENT_GAME_ID_AND_NUMBERS")
            )
            return self._quarantine(record, reason, existing)
        d = record.as_dict()
        self._append(self.valid_path, d)
        self._by_game[record.game_id] = d
        self._by_identity[record.identity] = d
        return NEW

    def valid_records(self) -> list[dict[str, Any]]:
        return list(self._by_game.values())

    def get(self, game_id: str) -> dict[str, Any] | None:
        return self._by_game.get(game_id)


def attach_periods(rows: list[TeamGame], store: PeriodStore) -> tuple[list[TeamGame], dict[str, int]]:
    """Rows with ``period_points`` filled from the store where the store's home/away mapping MATCHES the row.

    A row that already has period_points is left alone. A mismatch leaves the row unchanged and is counted.
    """
    out: list[TeamGame] = []
    stats = {"attached": 0, "already_had": 0, "no_record": 0, "mapping_mismatch": 0}
    for r in rows:
        if r.period_points is not None:
            stats["already_had"] += 1
            out.append(r)
            continue
        rec = store.get(r.game_id)
        if rec is None:
            stats["no_record"] += 1
            out.append(r)
            continue
        if r.team_id == rec["home_team_id"] and r.opponent_id == rec["away_team_id"]:
            periods = tuple(rec["home_periods"])
        elif r.team_id == rec["away_team_id"] and r.opponent_id == rec["home_team_id"]:
            periods = tuple(rec["away_periods"])
        else:
            stats["mapping_mismatch"] += 1
            out.append(r)
            continue
        stats["attached"] += 1
        out.append(dataclasses.replace(r, period_points=periods))
    return out, stats
