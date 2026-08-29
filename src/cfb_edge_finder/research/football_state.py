"""Durable, versioned FOOTBALL-STATE artifact: the slow lane of the
two-lane collection architecture.

*** THE PROBLEM THIS SOLVES (live incident, 2026-08-29) ***
The deadline-critical Kalshi capture loop ran every 5 minutes and made
live CFBD requests on EVERY invocation (schedule + all-division teams
each run; four seasons of history + advanced stats whenever a checkpoint
was due). At that cadence the free CFBD quota is structurally
exhaustible, and when CFBD began returning HTTP 429 at 16:50Z the entire
capture loop died with it -- costing the SJSU@USC and NCST@UVA CLOSING
windows even though nothing about those games' football state had
changed in hours. The football inputs the model consumes are effectively
STATIC at five-minute frequency: CONTROL is trained on completed
2022-2025 games (which change at most weekly), and the schedule changes
on the order of hours-to-days, not minutes. Only Kalshi prices move
fast.

*** THE ARCHITECTURE ***
SLOW LANE  -- this module fetches CFBD once per freshness interval and
persists a durable, versioned, provenance-stamped artifact on the
research-data branch (under `data/research/`, so the existing
git_durable_store staging/commit/serialization machinery covers it
unchanged).
FAST LANE  -- the capture loop loads the artifact, verifies freshness,
and runs the ENTIRE existing pipeline (normalization -> mapping ->
projection -> pricing -> capture) from it with ZERO CFBD requests.

*** WHAT IS CACHED, AND WHY RAW ***
The artifact stores the RAW CFBD wire rows (schedule games verbatim;
history games verbatim; advanced stats compacted to the single nested
field `offense.plays` that `modeling.corpus.build_team_game_lines` is
proven to read; all-division teams compacted to the identity fields
`teams.fcs_identity.build_fcs_school_name_set` is proven to read).
Normalization/classification/model fitting all run at LOAD time through
the exact production functions (`normalize_cfbd_game`,
`home_classification`/`away_classification`, `build_fcs_school_name_set`,
`build_team_game_lines`), so a projection produced from the artifact is
bit-identical to one produced from a live fetch of the same rows --
caching final projections instead would have coupled the artifact to
model internals and made a legitimate schedule refresh unable to reach
the projection layer. CONTROL itself is untouched.

*** FRESHNESS / SAFETY POLICY ***
Schedule (the safety-critical component -- kickoff, status, home/away):
  - soft refresh at SCHEDULE_SOFT_REFRESH_HOURS: the fast loop ATTEMPTS
    a 1-call live refresh; a failure (429/5xx/timeout) is recorded and
    the cached schedule keeps serving...
  - ...only up to the HARD limit, which is deliberately imported from
    `scan_logic.MAX_SCHEDULE_STALENESS_HOURS` -- the SAME 6-hour bound
    `guard_capture_allowed` already enforces per captured row via
    `schedule_source_timestamp`. Rows written from this artifact carry
    the artifact's own `schedule_fetched_at` as their
    schedule_source_timestamp, so the existing per-row guard and this
    loader can never disagree about what "too stale to capture" means.
History/teams (model inputs; not kickoff-safety-relevant):
  - soft refresh daily; hard cap generous (completed-game history only
    grows, roughly weekly).
Beyond a hard limit, or with no artifact at all and CFBD down, the fast
loop FAILS CLOSED: no captures, explicit telemetry/heartbeat, and the
(no-network) missed-checkpoint reconciliation still runs.

Kickoff-change safety is layered, not single-source:
  1. the 6h schedule freshness bound above;
  2. `resolve_due_labels`' clock guard (now >= cached kickoff => nothing
     due -- a game DELAYED under a stale cache can only miss captures,
     never mislabel a post-kickoff quote as pregame);
  3. Kalshi's own `status == "active"` requirement (discovery drops a
     started game's markets);
  4. the fast loop's close-time sanity check (scan side): a mapped
     market whose Kalshi `close_time` disagrees with the cached kickoff
     by more than KICKOFF_SANITY_TOLERANCE_MINUTES marks the game
     KICKOFF-UNCERTAIN and captures nothing for it, fail-closed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cfb_edge_finder.ingestion.game_normalization import (
    GameNormalizationError,
    away_classification,
    home_classification,
    normalize_cfbd_game,
)
from cfb_edge_finder.modeling.corpus import TeamGameLine, build_team_game_lines
from cfb_edge_finder.research.scan_logic import MAX_SCHEDULE_STALENESS_HOURS
from cfb_edge_finder.schemas.game import GameRecord
from cfb_edge_finder.teams.fcs_identity import build_fcs_school_name_set

FOOTBALL_STATE_SCHEMA_VERSION = "football_state_v1"
FOOTBALL_STATE_SUBDIR = "football_state"
"""Lives under data/research/ so git_durable_store.DURABLE_STORE_PATHS
stages it with no change to the staging allowlist -- and so the artifact
is durable DATA on the research-data branch, never code."""

SCHEDULE_SOFT_REFRESH_HOURS = 4.0
"""Attempt a live schedule refresh past this age. Chosen strictly below
the hard bound so a single failed refresh window still leaves usable
margin before capture must stop."""

SCHEDULE_HARD_MAX_HOURS = MAX_SCHEDULE_STALENESS_HOURS
"""6.0 -- the SAME constant guard_capture_allowed enforces per row.
Deliberately an import, not a copy: the loader and the per-row guard
cannot drift apart."""

HISTORY_SOFT_REFRESH_HOURS = 24.0
HISTORY_HARD_MAX_HOURS = 14 * 24.0
"""History is completed-game evidence: it only grows, and only around
game days. A day-old fit is the SAME fit unless games finished since.
The hard cap only exists so a forgotten artifact cannot serve forever."""

KICKOFF_SANITY_TOLERANCE_MINUTES = 30.0
"""Fast-lane cross-check threshold: cached kickoff vs the mapped Kalshi
market's own close_time. Beyond this the game is KICKOFF-UNCERTAIN and
nothing is captured for it. Tolerant enough for ordinary clock skew and
Kalshi closing a few minutes around kickoff; tight enough that a genuine
reschedule/postponement (which moves close_time by hours) always trips
it."""

# Freshness verdict vocabulary (strings, mirroring the repo's
# state-string style in e.g. shadow sidecar states).
FOOTBALL_STATE_FRESH = "FOOTBALL_STATE_FRESH"
FOOTBALL_STATE_STALE_SOFT = "FOOTBALL_STATE_STALE_SOFT"
FOOTBALL_STATE_STALE_HARD = "FOOTBALL_STATE_STALE_HARD"
FOOTBALL_STATE_MISSING = "FOOTBALL_STATE_MISSING"
FOOTBALL_STATE_CORRUPT = "FOOTBALL_STATE_CORRUPT"

_ADVANCED_KEEP = ("gameId", "team")
_TEAM_KEEP = ("school", "classification")


def _compact_advanced(rows: list[dict]) -> list[dict]:
    """Keeps EXACTLY the wire shape modeling.corpus reads: top-level
    gameId/team plus the nested offense.plays. Compaction must never
    flatten offense.plays -- a flattened cache silently produced a
    degenerate all-zero ratings fit in an offline reproduction, which is
    the precise footgun this preserves the shape against."""
    kept = []
    for row in rows:
        offense = row.get("offense") or {}
        kept.append(
            {**{k: row.get(k) for k in _ADVANCED_KEEP}, "offense": {"plays": offense.get("plays")}}
        )
    return kept


def _compact_teams(rows: list[dict]) -> list[dict]:
    return [{k: row.get(k) for k in _TEAM_KEEP} for row in rows]


@dataclass(frozen=True)
class FootballState:
    """One durable snapshot of everything the capture loop needs from
    CFBD. Raw wire rows in, production normalizers at load time out."""

    season: int
    history_seasons: tuple[int, ...]
    schedule_fetched_at: datetime
    teams_fetched_at: datetime
    history_fetched_at: datetime
    schedule_games: list[dict]
    all_division_teams: list[dict]
    history: dict[str, dict]
    """season(str) -> {"games": [...], "advanced": [compacted]}"""
    source: str = "cfbd"
    schema_version: str = FOOTBALL_STATE_SCHEMA_VERSION

    def schedule_age_hours(self, now: datetime) -> float:
        return (now - self.schedule_fetched_at).total_seconds() / 3600.0

    def history_age_hours(self, now: datetime) -> float:
        return (now - self.history_fetched_at).total_seconds() / 3600.0

    def freshness(self, now: datetime) -> str:
        if self.schedule_age_hours(now) > SCHEDULE_HARD_MAX_HOURS:
            return FOOTBALL_STATE_STALE_HARD
        if self.history_age_hours(now) > HISTORY_HARD_MAX_HOURS:
            return FOOTBALL_STATE_STALE_HARD
        if (
            self.schedule_age_hours(now) > SCHEDULE_SOFT_REFRESH_HOURS
            or self.history_age_hours(now) > HISTORY_SOFT_REFRESH_HOURS
        ):
            return FOOTBALL_STATE_STALE_SOFT
        return FOOTBALL_STATE_FRESH

    # ---------------------------------------------------------- payload

    def payload_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "season": self.season,
            "source": self.source,
            "history_seasons": list(self.history_seasons),
            "schedule_games": self.schedule_games,
            "all_division_teams": self.all_division_teams,
            "history": self.history,
        }

    def manifest_dict(self, payload_sha256: str) -> dict:
        return {
            "schema_version": self.schema_version,
            "season": self.season,
            "source": self.source,
            "history_seasons": list(self.history_seasons),
            "schedule_fetched_at": self.schedule_fetched_at.isoformat(),
            "teams_fetched_at": self.teams_fetched_at.isoformat(),
            "history_fetched_at": self.history_fetched_at.isoformat(),
            "payload_sha256": payload_sha256,
        }

    # ------------------------------------------------------ scan inputs

    def to_scan_inputs(self, observed_at: datetime) -> ScanInputs:
        """Runs the EXACT production normalizers over the cached raw
        rows. Nothing here re-implements normalization, classification,
        FCS identity, or corpus building -- byte-equivalent inputs to a
        live fetch of the same rows, by construction."""
        games: list[GameRecord] = []
        classification: dict[str, tuple[str | None, str | None]] = {}
        for raw in self.schedule_games:
            try:
                game = normalize_cfbd_game(raw, observed_at=observed_at)
            except GameNormalizationError:
                continue
            games.append(game)
            classification[game.game_id] = (home_classification(raw), away_classification(raw))

        fcs_names = build_fcs_school_name_set(self.all_division_teams)

        def lines_loader() -> list[TeamGameLine]:
            lines: list[TeamGameLine] = []
            for season in self.history_seasons:
                bundle = self.history.get(str(season)) or {}
                season_lines, _skipped = build_team_game_lines(
                    bundle.get("games") or [],
                    bundle.get("advanced") or [],
                    captured_at=observed_at,
                )
                lines.extend(season_lines)
            return lines

        return ScanInputs(
            games=games,
            classification_by_game_id=classification,
            fcs_school_names=fcs_names,
            schedule_source_timestamp=self.schedule_fetched_at,
            lines_loader=lines_loader,
        )


@dataclass(frozen=True)
class ScanInputs:
    games: list[GameRecord]
    classification_by_game_id: dict[str, tuple[str | None, str | None]]
    fcs_school_names: frozenset[str]
    schedule_source_timestamp: datetime
    lines_loader: object  # zero-arg callable -> list[TeamGameLine]


@dataclass
class RefreshOutcome:
    """What one slow-lane attempt did, for telemetry/heartbeat."""

    state: FootballState | None
    source: str
    """'cache' | 'live_full_refresh' | 'live_schedule_refresh' |
    'cache_after_refresh_failure' | 'unavailable'"""
    cfbd_requests: int = 0
    refresh_error: str | None = None
    freshness: str = FOOTBALL_STATE_MISSING


# --------------------------------------------------------------- building


def build_football_state(cfbd_client, *, season: int, history_seasons: list[int], now: datetime) -> FootballState:
    """The ONLY full CFBD sweep in the architecture: 2 calls for
    schedule+teams plus 2 per history season."""
    schedule = cfbd_client.fetch_games(season=season, season_type=None)
    teams = _compact_teams(cfbd_client.fetch_all_division_teams(season=season))
    history: dict[str, dict] = {}
    for hist_season in history_seasons:
        history[str(hist_season)] = {
            "games": cfbd_client.fetch_games(season=hist_season, season_type=None, division="fbs"),
            "advanced": _compact_advanced(cfbd_client.fetch_advanced_team_game_stats(season=hist_season)),
        }
    return FootballState(
        season=season,
        history_seasons=tuple(history_seasons),
        schedule_fetched_at=now,
        teams_fetched_at=now,
        history_fetched_at=now,
        schedule_games=schedule,
        all_division_teams=teams,
        history=history,
    )


def refresh_schedule_only(state: FootballState, cfbd_client, *, now: datetime) -> FootballState:
    """1-call refresh of the safety-critical component; history/teams
    carried forward untouched with their own timestamps."""
    schedule = cfbd_client.fetch_games(season=state.season, season_type=None)
    return FootballState(
        season=state.season,
        history_seasons=state.history_seasons,
        schedule_fetched_at=now,
        teams_fetched_at=state.teams_fetched_at,
        history_fetched_at=state.history_fetched_at,
        schedule_games=schedule,
        all_division_teams=state.all_division_teams,
        history=state.history,
    )


# ------------------------------------------------------------ persistence


def _paths(repo_dir: Path, season: int) -> tuple[Path, Path]:
    base = repo_dir / "data" / "research" / FOOTBALL_STATE_SUBDIR
    return base / f"{season}.json", base / f"{season}.manifest.json"


def save_football_state(repo_dir: Path, state: FootballState) -> None:
    """Payload written only when its content actually changed (sha
    compare), so a schedule-only refresh whose rows are identical costs a
    ~300-byte manifest rewrite, not a multi-MB payload rewrite, on the
    durable branch."""
    payload_path, manifest_path = _paths(repo_dir, state.season)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_bytes = json.dumps(state.payload_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    sha = hashlib.sha256(payload_bytes).hexdigest()
    if not payload_path.exists() or hashlib.sha256(payload_path.read_bytes()).hexdigest() != sha:
        payload_path.write_bytes(payload_bytes)
    manifest_path.write_text(
        json.dumps(state.manifest_dict(sha), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def load_football_state(repo_dir: Path, season: int) -> tuple[FootballState | None, str]:
    """Returns (state, verdict-string). Corruption (sha mismatch,
    unparsable JSON, schema drift) is FOOTBALL_STATE_CORRUPT and yields
    None -- fail closed, never a best-effort partial state."""
    payload_path, manifest_path = _paths(repo_dir, season)
    if not payload_path.exists() or not manifest_path.exists():
        return None, FOOTBALL_STATE_MISSING
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload_bytes = payload_path.read_bytes()
        if hashlib.sha256(payload_bytes).hexdigest() != manifest.get("payload_sha256"):
            return None, FOOTBALL_STATE_CORRUPT
        payload = json.loads(payload_bytes)
        if payload.get("schema_version") != FOOTBALL_STATE_SCHEMA_VERSION:
            return None, FOOTBALL_STATE_CORRUPT
        state = FootballState(
            season=int(payload["season"]),
            history_seasons=tuple(int(s) for s in payload["history_seasons"]),
            schedule_fetched_at=datetime.fromisoformat(manifest["schedule_fetched_at"]),
            teams_fetched_at=datetime.fromisoformat(manifest["teams_fetched_at"]),
            history_fetched_at=datetime.fromisoformat(manifest["history_fetched_at"]),
            schedule_games=payload["schedule_games"],
            all_division_teams=payload["all_division_teams"],
            history=payload["history"],
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return None, FOOTBALL_STATE_CORRUPT
    if state.schedule_fetched_at.tzinfo is None:
        return None, FOOTBALL_STATE_CORRUPT
    return state, "LOADED"


def load_football_state_from_git(repo_dir: Path, branch: str, season: int) -> tuple[FootballState | None, str]:
    """Read the artifact from `origin/{branch}` WITHOUT checking the
    branch out -- for callers (the conductor) that plan from a main
    checkout. Read-only; provenance is the artifact's own manifest."""
    import tempfile

    try:
        subprocess.run(
            ["git", "fetch", "origin", branch, "--depth=1"],
            cwd=repo_dir, capture_output=True, text=True, timeout=120, check=True,
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_repo = Path(tmp)
            base = tmp_repo / "data" / "research" / FOOTBALL_STATE_SUBDIR
            base.mkdir(parents=True)
            for name in (f"{season}.json", f"{season}.manifest.json"):
                show = subprocess.run(
                    ["git", "show", f"origin/{branch}:data/research/{FOOTBALL_STATE_SUBDIR}/{name}"],
                    cwd=repo_dir, capture_output=True, timeout=120,
                )
                if show.returncode != 0:
                    return None, FOOTBALL_STATE_MISSING
                (base / name).write_bytes(show.stdout)
            return load_football_state(tmp_repo, season)
    except (subprocess.SubprocessError, OSError):
        return None, FOOTBALL_STATE_MISSING


# ------------------------------------------------------------ orchestration


def resolve_football_state(
    repo_dir: Path,
    cfbd_client,
    *,
    season: int,
    history_seasons: list[int],
    now: datetime,
    force_refresh: bool = False,
) -> RefreshOutcome:
    """The slow-lane decision procedure the fast loop calls once per run.

    FRESH cache            -> use it, ZERO CFBD requests.
    SOFT-stale (or forced) -> attempt the cheapest sufficient live
                              refresh; on failure fall back to the cache
                              while it remains inside the HARD bound.
    HARD-stale / missing   -> attempt a live build; on failure return
                              state=None (callers fail closed).
    A successful refresh is saved to disk here so the surrounding durable
    commit persists it."""
    cached, _verdict = load_football_state(repo_dir, season)
    if cached is not None and list(cached.history_seasons) != list(history_seasons):
        cached = None  # config changed: rebuild

    if cached is not None and not force_refresh and cached.freshness(now) == FOOTBALL_STATE_FRESH:
        return RefreshOutcome(state=cached, source="cache", cfbd_requests=0, freshness=FOOTBALL_STATE_FRESH)

    needs_full = (
        cached is None
        or force_refresh
        or cached.history_age_hours(now) > HISTORY_SOFT_REFRESH_HOURS
    )
    try:
        if needs_full:
            fresh = build_football_state(cfbd_client, season=season, history_seasons=history_seasons, now=now)
            requests_made = 2 + 2 * len(history_seasons)
            source = "live_full_refresh"
        else:
            fresh = refresh_schedule_only(cached, cfbd_client, now=now)
            requests_made = 1
            source = "live_schedule_refresh"
        save_football_state(repo_dir, fresh)
        return RefreshOutcome(
            state=fresh, source=source, cfbd_requests=requests_made, freshness=FOOTBALL_STATE_FRESH
        )
    except Exception as exc:  # noqa: BLE001 -- any live failure degrades identically
        error = f"{type(exc).__name__}: {exc}"
        if cached is not None and cached.freshness(now) != FOOTBALL_STATE_STALE_HARD:
            return RefreshOutcome(
                state=cached,
                source="cache_after_refresh_failure",
                cfbd_requests=1 if not needs_full else 2 + 2 * len(history_seasons),
                refresh_error=error,
                freshness=cached.freshness(now),
            )
        return RefreshOutcome(
            state=None,
            source="unavailable",
            cfbd_requests=1 if not needs_full else 2 + 2 * len(history_seasons),
            refresh_error=error,
            freshness=FOOTBALL_STATE_STALE_HARD if cached is not None else FOOTBALL_STATE_MISSING,
        )
