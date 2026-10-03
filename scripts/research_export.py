#!/usr/bin/env python3
"""Export the CFB research-graph explorer (contract 1.1.0) beside the v1 app bundle.

    python scripts/research_export.py --out app/latest [--data-root data/live]
                                      [--include-cfbd --research-root <git archive of research-data>]
                                      [--min-interval-minutes N] [--check-due] [--now <iso-utc>]

Run it AFTER ``scripts/app_export.py``: it reads the published v1 bundle at ``--out`` (manifest,
events, markets, wagers), so every ``prt_`` / ``evt_`` / ``mkt_kalshi_`` id it emits is the id the
v1 export emitted, and ``run_id`` / ``generated_at`` are the v1 publication's own.

WHAT CFB CAN HONESTLY SHOW (docs/APP_EXPORT.md "Research explorer", audit 2026-10-03):

  * market inventory (VERIFIED, already in v1) and per-event MARKET HISTORY reconstructed from the
    catalog's git history on main (``git log`` / ``git ls-tree`` / ``git cat-file`` over
    ``data/live``): change-detected snapshots, NOT a closing series (PARTIAL);
  * the owner's wager ledger (VERIFIED, already in v1): each event research document lists the
    wagers placed on that game;
  * team profiles with SEASON-LEVEL aggregates from the frozen CFBD snapshot on the research-data
    branch (SP+, season advanced PPA / success rate / explosiveness / havoc, talent, recruiting,
    returning production; pregame Elo as a per-game series), 2014-2025 (+2026 preseason tables),
    labelled "CFBD snapshot <date>; no 2026 in-season stats" (PARTIAL), with rankings per season;
  * opponent-adjusted walk-forward team states read from ``data/research/v2/dataset.parquet``
    (RESEARCH: research-branch output, builder not on main).

Raw CFBD box-score, drive and play rows are NOT republished (CFBD's terms prohibit redistributing
raw API data; docs/DATA_SOURCES.md). Nothing here fits a model: rankings are arithmetic over stored
values (``edge_finder_contract.research``). No network access, no credential.

CFBD-derived values are published ONLY with ``--include-cfbd`` (the owner's opt-in): until the CFBD
redistribution licence is resolved the default publishes none, and every CFBD-backed capability is
UNAVAILABLE ("CFBD redistribution licence unresolved; owner opt-in required (--include-cfbd)"). With
the opt-in, the research-data files are still optional: when ``--research-root`` is absent or empty
(the fetch failed) the explorer publishes markets, market history, wagers and capabilities, and the
CFBD-backed capabilities read UNAVAILABLE ("research-data branch not available").

``--min-interval-minutes`` gates rebuilds with ``research.refresh_due``: the tree is rewritten only
when it is missing, the v1 events changed, or it is older than the interval.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "contract"))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from edge_finder_contract import build, ids, publish, timeutil  # noqa: E402
from edge_finder_contract import research as R  # noqa: E402

from cfb_edge_finder.teams.registry import (  # noqa: E402
    AmbiguousTeamAliasError,
    UnknownTeamAliasError,
    resolve_team_alias,
)

SPORT = "CFB"
EXPORT_VERSION = "cfb-research-export 1.0.0"
AUDIT_DATE = "2026-10-03"
SERIES_GAME_CAP = 40  # per-game series keep the last 40 games (CFB: ~3 seasons)
PROFILE_SEASONS = 2  # season observations carried inside a profile; every season is in the rankings
NO_RESEARCH_REASON = "research-data branch not available"
#: Default until the owner resolves the CFBD redistribution question: no CFBD-derived value is published.
LICENCE_OPT_IN_REASON = "CFBD redistribution licence unresolved; owner opt-in required (--include-cfbd)"
CFBD_CACHE = Path("data/research_cache/v2")
DATASET = Path("data/research/v2/dataset.parquet")
CFBD_TABLES = (
    "teams_fbs",
    "ratings_sp",
    "season_advanced",
    "talent",
    "recruiting_teams",
    "returning_production",
    "games",
)
PROP_FAMILIES = ("touchdown_scorer", "team_stat_prop", "game_stat_prop")
TEAM_PROP_FAMILIES = ("team_total", "first_half_team_total")
GAME_FAMILIES = ("game_spread", "game_total", "game_moneyline")
LICENCE_NOTE = (
    "CFBD redistribution licence unresolved: docs/DATA_SOURCES.md records that reselling or "
    "redistributing raw CFBD API data is prohibited, so only season-level aggregates and one per-game "
    "field (pregame Elo) are published; raw box-score, drive and play rows are not"
)
HISTORY_LABEL = "change-detected snapshots, not a closing series"
#: Per-document byte budgets (compact JSON) from the adapter spec; documents are trimmed to fit, never split.
BUDGETS = {"entity_profile": 150_000, "event_research": 150_000, "market_history": 400_000, "search_index": 300_000}
MODEL_RETIRED = (
    "No model projections: the CFB model is retired (docs/MODEL_RETIREMENT_2026.md); "
    "model_prices, theses and recommendations are empty by design."
)


class ExplorerExportFailure(RuntimeError):
    """The explorer could not be built from the inputs."""


def doc_bytes(doc: dict) -> int:
    return len(publish.dumps(doc, compact=True).encode("utf-8"))


# ------------------------------------------------------------------ inputs: the published v1 bundle


def load_v1(app_root: Path) -> dict[str, Any]:
    """The v1 publication the explorer describes (ids, run id and generated_at come from here)."""
    app_root = Path(app_root)
    manifest = publish.read_manifest(app_root)
    if manifest is None:
        raise ExplorerExportFailure(f"no v1 manifest.json under {app_root}; run scripts/app_export.py first")

    def items(name: str) -> list[dict]:
        path = app_root / f"{name}.json"
        return list(json.loads(path.read_text(encoding="utf-8"))["items"]) if path.exists() else []

    return {
        "manifest": manifest,
        "events": items("events"),
        "markets": items("markets"),
        "wagers": items("wagers"),
        "model_prices": items("model_prices"),
    }


# ------------------------------------------------------------------ inputs: the research-data branch files


def _read_gz(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else []


def _read_dataset(path: Path) -> tuple[list[dict] | None, str | None, dict]:
    meta_path = path.with_name(path.name + ".meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return None, "pyarrow is not installed, so data/research/v2/dataset.parquet was not read", meta
    required = [
        "game_id",
        "season",
        "week",
        "season_type",
        "kickoff",
        "home",
        "away",
        "home_id_cfbd",
        "away_id_cfbd",
        "completed",
        "neutral",
    ]
    schema_names = set(pq.read_schema(path).names)
    missing = [c for c in required if c not in schema_names]
    if missing:
        return None, f"dataset.parquet lacks columns {missing[:5]}", meta
    # A state metric whose columns are absent is simply not published (no value is ever invented).
    states = [f"{h}_{spec.side}_{spec.column}" for spec in RESEARCH_METRICS for h in ("h", "a")]
    rows = pq.read_table(path, columns=sorted(set(required + [c for c in states if c in schema_names]))).to_pylist()
    return rows, None, meta


def load_research(research_root: Path | None, *, absent_reason: str = NO_RESEARCH_REASON) -> dict[str, Any]:
    """CFBD snapshot tables (per season) and the v2 dataset rows, or the reason they are absent."""
    out: dict[str, Any] = {
        "present": False,
        "reason": absent_reason,
        "fetched_at": None,
        "cache_version": None,
        "source": None,
        "tables": {},
        "dataset": None,
        "dataset_reason": absent_reason,
        "dataset_meta": {},
    }
    if research_root is None:
        return out
    cache = Path(research_root) / CFBD_CACHE
    manifest_path = cache / "manifest.json"
    if not manifest_path.exists():
        return out
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tables: dict[int, dict[str, list[dict]]] = {}
    for season_dir in sorted(p for p in cache.iterdir() if p.is_dir() and p.name.isdigit()):
        season = int(season_dir.name)
        for name in CFBD_TABLES:
            path = season_dir / f"{name}.json.gz"
            if path.exists():
                tables.setdefault(season, {})[name] = _read_gz(path)
    if not any("teams_fbs" in t for t in tables.values()):
        return out
    out.update(
        present=True,
        reason=None,
        fetched_at=timeutil.to_iso(manifest["fetched_at"]),
        cache_version=manifest.get("cache_version"),
        source=manifest.get("source"),
        tables=tables,
    )
    dataset_path = Path(research_root) / DATASET
    if dataset_path.exists():
        rows, reason, meta = _read_dataset(dataset_path)
        out.update(dataset=rows, dataset_reason=reason, dataset_meta=meta)
    else:
        out["dataset_reason"] = "data/research/v2/dataset.parquet not in the research-data checkout"
    return out


# ------------------------------------------------------------------ inputs: catalog git history (market history)


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


class _BlobReader:
    """``git cat-file --batch`` over one long-lived process (thousands of blobs in seconds)."""

    def __init__(self, repo: str):
        self.proc = subprocess.Popen(
            ["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )

    def read(self, sha: str) -> bytes:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write((sha + "\n").encode())
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().split()
        if len(header) < 3 or header[1] == b"missing":
            raise KeyError(sha)
        data = self.proc.stdout.read(int(header[2]))
        self.proc.stdout.read(1)
        return data

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=30)


def _quotes(markets: list[dict]) -> dict[str, tuple]:
    """ticker -> (yes_bid, yes_ask, last_price, volume, open_interest); sentinel 0/1 books -> null quotes."""
    out = {}
    for m in markets:
        ticker = str(m.get("market_ticker") or "").upper()
        if not ticker:
            continue
        sentinel = bool((m.get("mechanics") or {}).get("is_sentinel_full_width_book"))
        out[ticker] = (
            None if sentinel else m.get("yes_bid"),
            None if sentinel else m.get("yes_ask"),
            m.get("last_price"),
            m.get("volume"),
            m.get("open_interest"),
        )
    return out


def load_catalog_history(data_root: Path, game_keys: set[str], max_commits: int | None = None) -> dict[str, Any]:
    """Every distinct quote state per ticker for the given games, from the catalog's git history plus the
    working-tree snapshot. Walks catalog commits newest -> oldest and stops at the first commit holding none
    of the games (older commits predate their listing)."""
    data_root = Path(data_root).resolve()
    snapshots: list[dict] = []  # {captured_at, source, games: {game_key: {ticker: quote}}}
    out: dict[str, Any] = {"snapshots": snapshots, "commits_read": 0, "git": False, "reason": None}
    blob_cache: dict[str, dict[str, tuple]] = {}
    try:
        top = _git(str(data_root), "rev-parse", "--show-toplevel").strip()
        rel = os.path.relpath(str(data_root), top).replace(os.sep, "/")
        commits = _git(top, "log", "--format=%H", "--", f"{rel}/cfb_market_catalog.json").split()
        if max_commits is not None:
            commits = commits[:max_commits]
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as exc:
        top, rel, commits = None, None, []
        out["reason"] = f"no git history for {data_root}: {type(exc).__name__}"
    if top and commits:
        out["git"] = True
        reader = _BlobReader(top)
        try:
            for sha in commits:
                listing = _git(top, "ls-tree", "-r", sha, "--", f"{rel}/games/", f"{rel}/cfb_market_catalog.json")
                index_blob, game_blobs = None, {}
                for line in listing.splitlines():
                    meta, path = line.split("\t", 1)
                    blob = meta.split()[2]
                    if path.endswith("/cfb_market_catalog.json"):
                        index_blob = blob
                    elif path.endswith(".json"):
                        key = path.rsplit("/", 1)[1][:-5]
                        if key in game_keys:
                            game_blobs[key] = blob
                if not game_blobs:
                    break
                if index_blob is None:
                    continue
                index = json.loads(reader.read(index_blob))
                captured_at = (index.get("capture") or {}).get("captured_at")
                if not captured_at:
                    continue
                games = {}
                for key, blob in game_blobs.items():
                    if blob not in blob_cache:
                        blob_cache[blob] = _quotes(json.loads(reader.read(blob)).get("markets") or [])
                    games[key] = blob_cache[blob]
                snapshots.append(
                    {"captured_at": timeutil.to_iso(captured_at), "source": f"catalog@{sha[:12]}", "games": games}
                )
                out["commits_read"] += 1
        finally:
            reader.close()
    # The working tree (the capture the v1 export just read) when it is not already the newest commit.
    index_path = data_root / "cfb_market_catalog.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        captured_at = timeutil.to_iso((index.get("capture") or {}).get("captured_at"))
        if not any(s["captured_at"] == captured_at for s in snapshots):
            games = {}
            for key in sorted(game_keys):
                path = data_root / "games" / f"{key}.json"
                if path.exists():
                    games[key] = _quotes(json.loads(path.read_text(encoding="utf-8")).get("markets") or [])
            snapshots.append({"captured_at": captured_at, "source": "catalog working tree", "games": games})
    snapshots.sort(key=lambda s: timeutil.parse_ts(s["captured_at"]))
    return out


# ------------------------------------------------------------------ metric definitions


@dataclass(frozen=True)
class SeasonMetric:
    slug: str
    name: str
    short: str
    table: str
    get: Callable[[dict], Any]
    higher_is_better: bool
    stat_type: str
    unit: str
    category: str
    description: str
    sample: Callable[[dict], Any] | None = None
    splits: dict[str, Callable[[dict], Any]] = field(default_factory=dict)


def _dig(*keys: str) -> Callable[[dict], Any]:
    def get(row: dict) -> Any:
        cur: Any = row
        for k in keys:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur if isinstance(cur, (int, float)) and not isinstance(cur, bool) else None

    return get


def _adv(side: str, stat: str, label: str, hib: bool, stat_type: str, unit: str, desc: str, short: str) -> SeasonMetric:
    return SeasonMetric(
        slug=f"{'off' if side == 'offense' else 'def'}_{label}",
        name=f"{'Offensive' if side == 'offense' else 'Defensive'} {desc}",
        short=short,
        table="season_advanced",
        get=_dig(side, stat),
        higher_is_better=hib,
        stat_type=stat_type,
        unit=unit,
        category="advanced",
        description=(
            f"CFBD season advanced stats, {side}.{stat}: the team's season-long {desc.lower()} "
            f"{'produced' if side == 'offense' else 'allowed'} per play (all plays, regular season and "
            "postseason as CFBD aggregates them). Republished as stored in the CFBD snapshot."
        ),
        sample=_dig(side, "plays"),
        splits={"rush": _dig(side, "rushingPlays", stat), "pass": _dig(side, "passingPlays", stat)}
        if stat in ("ppa", "successRate")
        else {},
    )


SEASON_METRICS: tuple[SeasonMetric, ...] = (
    SeasonMetric(
        "sp_plus_rating",
        "SP+ rating",
        "SP+",
        "ratings_sp",
        _dig("rating"),
        True,
        "RATING",
        "points",
        "ratings",
        "Bill Connelly's SP+ overall team rating as stored by CFBD (/ratings/sp): points better "
        "than an average FBS team on a neutral field.",
    ),
    SeasonMetric(
        "sp_plus_offense",
        "SP+ offense",
        "SP+ O",
        "ratings_sp",
        _dig("offense", "rating"),
        True,
        "RATING",
        "points",
        "ratings",
        "SP+ offensive rating as stored by CFBD (/ratings/sp offense.rating): adjusted points per game produced.",
    ),
    SeasonMetric(
        "sp_plus_defense",
        "SP+ defense",
        "SP+ D",
        "ratings_sp",
        _dig("defense", "rating"),
        False,
        "RATING",
        "points",
        "ratings",
        "SP+ defensive rating as stored by CFBD (/ratings/sp defense.rating): "
        "adjusted points per game allowed (lower is better).",
    ),
    _adv("offense", "ppa", "ppa", True, "RATE", "points per play", "PPA (predicted points added)", "O PPA"),
    _adv("offense", "successRate", "success_rate", True, "PERCENT", "share of plays", "success rate", "O SR"),
    _adv(
        "offense", "explosiveness", "explosiveness", True, "RATE", "PPA per successful play", "explosiveness", "O XPL"
    ),
    _adv("defense", "ppa", "ppa", False, "RATE", "points per play", "PPA (predicted points added)", "D PPA"),
    _adv("defense", "successRate", "success_rate", False, "PERCENT", "share of plays", "success rate", "D SR"),
    _adv(
        "defense", "explosiveness", "explosiveness", False, "RATE", "PPA per successful play", "explosiveness", "D XPL"
    ),
    SeasonMetric(
        "def_havoc_rate",
        "Defensive havoc rate",
        "D HAV",
        "season_advanced",
        _dig("defense", "havoc", "total"),
        True,
        "PERCENT",
        "share of plays",
        "advanced",
        "CFBD season advanced defense.havoc.total: share of "
        "opponent plays with a tackle for loss, forced fumble, interception or pass breakup.",
        sample=_dig("defense", "plays"),
    ),
    SeasonMetric(
        "off_havoc_allowed",
        "Offensive havoc allowed",
        "O HAV",
        "season_advanced",
        _dig("offense", "havoc", "total"),
        False,
        "PERCENT",
        "share of plays",
        "advanced",
        "CFBD season advanced offense.havoc.total: share of the team's own plays on which the defense "
        "created havoc (lower is better).",
        sample=_dig("offense", "plays"),
    ),
    SeasonMetric(
        "talent",
        "Team talent composite",
        "TAL",
        "talent",
        _dig("talent"),
        True,
        "INDEX",
        "composite points",
        "roster",
        "CFBD /talent: the 247Sports team talent composite for the season's roster, as stored.",
    ),
    SeasonMetric(
        "recruiting_points",
        "Recruiting class points",
        "REC",
        "recruiting_teams",
        _dig("points"),
        True,
        "INDEX",
        "composite points",
        "roster",
        "CFBD /recruiting/teams: 247Sports composite points of the season's signed recruiting class, as stored.",
    ),
    SeasonMetric(
        "returning_production",
        "Returning production (PPA share)",
        "RET",
        "returning_production",
        _dig("percentPPA"),
        True,
        "PERCENT",
        "share of prior-season PPA",
        "roster",
        "CFBD /player/returning percentPPA: the share of the prior season's total PPA produced by players "
        "who return this season (a team-level share, not player data).",
    ),
)


@dataclass(frozen=True)
class ResearchMetric:
    slug: str
    name: str
    short: str
    column: str
    side: str  # "o" offense state, "d" defense state
    higher_is_better: bool
    unit: str
    description: str
    series: bool


_STATE = (
    "Walk-forward opponent-adjusted team state from the v2 research dataset (research-data branch, "
    "data/research/v2/dataset.parquet): the ridge solution of m_off(T,g) = mu_m + off_m[T] - def_m[O] + "
    "hfa_m*home + e over strictly prior games (lam 6.0, season_decay 0.5, FCS pooled), read back as stored. "
)
RESEARCH_METRICS: tuple[ResearchMetric, ...] = (
    ResearchMetric(
        "adj_off_ppa",
        "Opponent-adjusted offensive PPA (research)",
        "adj O PPA",
        "o_ppa",
        "o",
        True,
        "points per play vs average",
        _STATE + "off_m for offensive PPA per play.",
        True,
    ),
    ResearchMetric(
        "adj_def_ppa",
        "Opponent-adjusted defensive PPA (research)",
        "adj D PPA",
        "o_ppa",
        "d",
        True,
        "points per play vs average",
        _STATE + "def_m for PPA per play; HIGHER means a BETTER defense "
        "(it subtracts more from what opponents produce).",
        True,
    ),
    ResearchMetric(
        "adj_off_success_rate",
        "Opponent-adjusted offensive success rate (research)",
        "adj O SR",
        "o_sr",
        "o",
        True,
        "share of plays vs average",
        _STATE + "off_m for offensive success rate.",
        False,
    ),
    ResearchMetric(
        "adj_def_success_rate",
        "Opponent-adjusted defensive success rate (research)",
        "adj D SR",
        "o_sr",
        "d",
        True,
        "share of plays vs average",
        _STATE + "def_m for success rate; higher = better defense.",
        False,
    ),
    ResearchMetric(
        "adj_margin",
        "Opponent-adjusted scoring margin (research)",
        "adj MAR",
        "margin",
        "o",
        True,
        "points vs average",
        _STATE + "off_m for the scoring-margin metric.",
        True,
    ),
)
ELO_SLUG = "pregame_elo"
MATCHUP_ORDER = (
    "sp_plus_rating",
    "off_ppa",
    "def_ppa",
    "off_success_rate",
    "def_success_rate",
    "off_explosiveness",
    "def_havoc_rate",
    "talent",
    "returning_production",
    "recruiting_points",
    "adj_off_ppa",
    "adj_def_ppa",
    "adj_margin",
)


def _state_value(spec: ResearchMetric, row_side: tuple[dict, str]) -> Any:
    """The stored state of ``spec`` for one side ("home"/"away") of a dataset row."""
    row, side = row_side
    return row.get(f"{side[0]}_{spec.side}_{spec.column}")


def _game_label(season: object, tag: str, week: object, where: str, opponent: object) -> str:
    """A per-game x label, e.g. ``2025 W07 @ Georgia`` (P = postseason, n = neutral site)."""
    return f"{season} {tag}{int(week or 0):02d} {where} {opponent}"


def season_window(season: int) -> dict:
    return R.window("SEASON", label=f"{season} season")


# ------------------------------------------------------------------ identity: Kalshi participants <-> CFBD teams


def _registry_slug(name: str) -> str | None:
    try:
        return resolve_team_alias(name)
    except (AmbiguousTeamAliasError, UnknownTeamAliasError):
        return None


def cfbd_teams(tables: dict[int, dict[str, list[dict]]]) -> dict[int, dict]:
    """CFBD team id -> its most recent teams_fbs record (with the seasons it was FBS)."""
    out: dict[int, dict] = {}
    for season in sorted(tables):
        for row in tables[season].get("teams_fbs", []):
            if row.get("id") is None:
                continue
            rec = dict(row)
            rec["fbs_seasons"] = sorted(set((out.get(row["id"]) or {}).get("fbs_seasons", [])) | {season})
            out[int(row["id"])] = rec
    return out


def map_participants(
    participants: dict[str, dict], teams: dict[int, dict], tables: dict[int, dict[str, list[dict]]]
) -> tuple[dict[str, dict], list[str]]:
    """Exact-only mapping. A Kalshi display name maps to a CFBD team when it equals the team's CFBD school
    name, equals one of its CFBD alternateNames, or resolves through the repo's alias registry
    (``teams.registry.resolve_team_alias``, exact match only) to the same canonical slug as one of the
    team's CFBD school names. Each rule must name exactly one CFBD team and no team may be claimed twice;
    anything else stays unmapped. The CFBD abbreviation is recorded as corroboration, never used alone."""
    school_ix: dict[str, set[int]] = {}
    alt_ix: dict[str, set[int]] = {}
    slug_ix: dict[str, set[int]] = {}
    for season in tables:
        for row in tables[season].get("teams_fbs", []):
            tid = int(row["id"])
            school_ix.setdefault(row["school"], set()).add(tid)
            for alt in row.get("alternateNames") or []:
                alt_ix.setdefault(alt, set()).add(tid)
            slug = _registry_slug(row["school"])
            if slug:
                slug_ix.setdefault(slug, set()).add(tid)
    proposed: dict[str, tuple[int, str]] = {}
    for pid, p in sorted(participants.items()):
        name = p["display_name"]
        for method, cands in (
            ("cfbd_school", school_ix.get(name)),
            ("cfbd_alternate_name", alt_ix.get(name)),
            ("registry_alias", slug_ix.get(_registry_slug(name) or "\0")),
        ):
            if cands:
                if len(cands) == 1:
                    proposed[pid] = (next(iter(cands)), method)
                break
    claims: dict[int, list[str]] = {}
    for pid, (tid, _) in proposed.items():
        claims.setdefault(tid, []).append(pid)
    mapping: dict[str, dict] = {}
    for pid, (tid, method) in sorted(proposed.items()):
        if len(claims[tid]) != 1:
            continue
        t = teams[tid]
        code = participants[pid].get("short_name")
        mapping[pid] = {
            "cfbd_team_id": tid,
            "school": t["school"],
            "method": method,
            "abbreviation": t.get("abbreviation"),
            "abbreviation_matches_kalshi_code": bool(code and code == t.get("abbreviation")),
        }
    unmapped = sorted(pid for pid in participants if pid not in mapping)
    return mapping, unmapped


def _season_name_index(rows: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        out[row["school"]] = int(row["id"])
    return out


# ------------------------------------------------------------------ the explorer build (pure)


@dataclass
class Ctx:
    sport: str
    run_id: str
    generated_at: str
    cfbd_as_of: str | None
    q_cfbd: dict | None = None
    q_research: dict | None = None
    q_elo: dict | None = None


def _cfbd_snapshot_label(fetched_at: str) -> str:
    return f"CFBD snapshot {timeutil.to_date(fetched_at)}; no 2026 in-season stats"


def _cfbd_limitations(fetched_at: str) -> list[str]:
    return [
        _cfbd_snapshot_label(fetched_at),
        "real vendor data, frozen snapshot, no 2026 in-season",
        "fetched once on the CFBD free tier; nothing refreshes it (research ingestion hibernated, quota exhausted)",
        "FBS teams only (CFBD teams_fbs); FCS teams have no CFBD identity here",
        LICENCE_NOTE,
    ]


def _participants(events: list[dict]) -> dict[str, dict]:
    """One participant per id. Kalshi titles occasionally truncate a school's name in one event
    ("University" for "University at Albany"), so the v1 export can carry two names for one id; the
    profile takes the most complete one (longest, then alphabetical) so the choice is deterministic."""
    out: dict[str, dict] = {}
    names: dict[str, set[str]] = {}
    for ev in sorted(events, key=lambda e: (e["start_time_utc"], e["event_id"])):
        for p in ev["participants"]:
            names.setdefault(p["participant_id"], set()).add(p["display_name"])
            if p["participant_id"] not in out:
                q = dict(p)
                q["metadata"] = {k: v for k, v in (p.get("metadata") or {}).items() if k != "slot"}
                out[p["participant_id"]] = q
    for pid, q in out.items():
        q["display_name"] = sorted(names[pid], key=lambda n: (-len(n), n))[0]
    return out


def _event_label(ev: dict, names: dict[str, str]) -> str:
    if ev.get("home_participant") and ev.get("away_participant"):
        return f"{names.get(ev['away_participant'], '?')} at {names.get(ev['home_participant'], '?')}"
    return " vs ".join(p["display_name"] for p in ev["participants"]) or str((ev.get("extensions") or {}).get("title"))


def _home_away(ev: dict, pid: str) -> str | None:
    if ev.get("home_participant") == pid:
        return "HOME"
    if ev.get("away_participant") == pid:
        return "AWAY"
    return None


@dataclass
class _Graph:
    """Shared state of one explorer build: identities, the CFBD tables and what has been published so far."""

    run_id: str
    now: str
    ctx: Ctx
    research: dict
    tables: dict
    teams: dict
    participants: dict
    names: dict
    cfbd_to_pid: dict
    season_obs: dict = field(default_factory=dict)  # pid -> observations
    split_obs: dict = field(default_factory=dict)
    ranking_refs: dict = field(default_factory=dict)
    series_refs: dict = field(default_factory=dict)
    latest_obs: dict = field(default_factory=dict)  # (pid, slug) -> newest observation
    published_metrics: dict = field(default_factory=dict)
    rankings: list = field(default_factory=list)
    series: list = field(default_factory=list)
    seasons_by_metric: dict = field(default_factory=dict)

    def entity_of(self, tid: int) -> tuple[str, str, str | None, str | None]:
        """(entity_id, display_name, short_name, profile path) of a CFBD team. A team on the board keeps its v1
        id; any other FBS team gets a cfbd-sourced id and no profile path."""
        pid = self.cfbd_to_pid.get(tid)
        if pid:
            p = self.participants[pid]
            return pid, p["display_name"], p.get("short_name"), R.team_path(pid)
        t = self.teams[tid]
        q = build.participant(
            sport=SPORT,
            participant_type="TEAM",
            source="cfbd_team_id",
            source_id=str(tid),
            display_name=t["school"],
            short_name=t.get("abbreviation"),
        )
        return q["participant_id"], t["school"], t.get("abbreviation"), None


def _add_season_metrics(g: _Graph) -> None:
    """Every CFBD season metric: one ranking per season over all FBS teams with a value, and the mapped
    teams' observations (with comparison context) and play-type splits."""
    run_id = g.run_id
    now = g.now
    ctx = g.ctx
    research = g.research
    tables = g.tables
    cfbd_to_pid = g.cfbd_to_pid
    entity_of = g.entity_of
    season_obs = g.season_obs
    split_obs = g.split_obs
    ranking_refs = g.ranking_refs
    latest_obs = g.latest_obs
    published_metrics = g.published_metrics
    rankings_published = g.rankings
    seasons_by_metric = g.seasons_by_metric
    fetched_at = research["fetched_at"]
    lims = _cfbd_limitations(fetched_at)
    ctx.q_cfbd = R.quality(
        status="PARTIAL",
        source=f"CFBD API snapshot ({research.get('source')}), research-data branch data/research_cache/v2",
        generated_at=now,
        production=False,
        data_as_of=fetched_at,
        source_version=research.get("cache_version"),
        methodology_version=EXPORT_VERSION,
        limitations=lims,
        coverage=f"seasons {min(tables)}-{max(tables)} (2026: preseason tables only)",
    )
    for spec in SEASON_METRICS:
        mid = build_metric_id(spec.slug)
        seasons = []
        for season in sorted(tables):
            season_tables = tables[season]
            if spec.table not in season_tables or "teams_fbs" not in season_tables:
                continue
            fbs = _season_name_index(season_tables["teams_fbs"])
            values = []
            stored: dict[int, dict] = {}
            for row in season_tables[spec.table]:
                tid = fbs.get(row.get("team"))
                if tid is None or tid in stored:
                    continue
                value = spec.get(row)
                if value is None:
                    continue
                stored[tid] = row
                eid, disp, short, path = entity_of(tid)
                sample = spec.sample(row) if spec.sample else None
                values.append(
                    {
                        "entity_id": eid,
                        "display_name": disp,
                        "short_name": short,
                        "value": value,
                        "sample_size": sample,
                        "path": path,
                    }
                )
            if len(values) < 2:
                continue
            seasons.append(season)
            win = season_window(season)
            rk = R.ranking(
                sport=SPORT,
                metric_id=mid,
                universe_label=f"FBS teams, {season} season (CFBD)",
                entity_type="TEAM",
                window=win,
                as_of=fetched_at,
                higher_is_better=spec.higher_is_better,
                values=values,
                run_id=run_id,
                generated_at=now,
                quality=ctx.q_cfbd,
                season=season,
                universe_filter="FBS teams in that season's CFBD teams_fbs with a stored value",
                links=[
                    R.link(
                        rel="METRIC",
                        target_kind="metric_registry",
                        label=spec.name,
                        target_id=mid,
                        path=R.app_path(R.METRICS_NAME),
                    )
                ],
            )
            rankings_published.append(rk)
            for tid, row in stored.items():
                pid = cfbd_to_pid.get(tid)
                if not pid:
                    continue
                ranking_refs.setdefault(pid, []).append(
                    {
                        "ranking_id": rk["ranking_id"],
                        "metric_id": mid,
                        "window_label": win["label"],
                        "split": None,
                        "path": R.ranking_path(rk["ranking_id"]),
                    }
                )
                obs = R.observation(
                    sport=SPORT,
                    metric_id=mid,
                    entity_id=pid,
                    entity_type="TEAM",
                    value=spec.get(row),
                    window=win,
                    as_of=fetched_at,
                    source="CFBD snapshot",
                    quality_status="PARTIAL",
                    unit=spec.unit,
                    season=season,
                    sample_size=spec.sample(row) if spec.sample else None,
                    context=R.context_from_ranking(rk, pid),
                )
                season_obs.setdefault(pid, []).append(obs)
                latest_obs[(pid, spec.slug)] = obs
                for split_value, getter in sorted(spec.splits.items()):
                    sv = getter(row)
                    if sv is not None:
                        split_obs.setdefault(pid, []).append(
                            R.observation(
                                sport=SPORT,
                                metric_id=mid,
                                entity_id=pid,
                                entity_type="TEAM",
                                value=sv,
                                window=win,
                                as_of=fetched_at,
                                source="CFBD snapshot",
                                quality_status="PARTIAL",
                                unit=spec.unit,
                                season=season,
                                split=R.split("play_type", split_value),
                            )
                        )
        if seasons:
            seasons_by_metric[spec.slug] = seasons
            published_metrics[spec.slug] = R.metric(
                sport=SPORT,
                slug=spec.slug,
                name=spec.name,
                short_name=spec.short,
                description=spec.description,
                entity_type="TEAM",
                category=spec.category,
                stat_type=spec.stat_type,
                source="CFBD snapshot",
                quality=ctx.q_cfbd,
                freshness="STALE",
                higher_is_better=spec.higher_is_better,
                unit=spec.unit,
                comparison_universe="FBS teams, per season (CFBD teams_fbs)",
                supports=R.supports(rank=True, percentile=True, windows=len(seasons) > 1, splits=bool(spec.splits)),
                windows=[f"{s} season" for s in seasons],
                splits=["play_type"] if spec.splits else [],
                source_version=research.get("cache_version"),
                methodology_version=EXPORT_VERSION,
                historical_start=f"{seasons[0]}-08-01",
                update_frequency="none (frozen snapshot)",
                known_limitations=lims,
            )


def _add_elo_series(g: _Graph) -> None:
    """Pregame Elo per completed game (never 2026 in-season), last SERIES_GAME_CAP games per mapped team."""
    run_id = g.run_id
    now = g.now
    ctx = g.ctx
    research = g.research
    tables = g.tables
    names = g.names
    cfbd_to_pid = g.cfbd_to_pid
    series_refs = g.series_refs
    published_metrics = g.published_metrics
    series_published = g.series
    fetched_at = research["fetched_at"]
    lims = _cfbd_limitations(fetched_at)
    # pregame Elo per game (completed games only, never 2026 in-season)
    elo_mid = build_metric_id(ELO_SLUG)
    ctx.q_elo = ctx.q_cfbd
    elo_rows: dict[int, list[dict]] = {}
    for season in sorted(tables):
        if season >= 2026:
            continue
        for g in tables[season].get("games", []):
            if not g.get("completed"):
                continue
            for side, other in (("home", "away"), ("away", "home")):
                tid, elo = g.get(f"{side}Id"), g.get(f"{side}PregameElo")
                if tid is None or elo is None or int(tid) not in cfbd_to_pid:
                    continue
                elo_rows.setdefault(int(tid), []).append({"g": g, "side": side, "other": other, "elo": elo})
    for tid, rows in sorted(elo_rows.items()):
        pid = cfbd_to_pid[tid]
        rows.sort(key=lambda r: (timeutil.parse_ts(r["g"]["startDate"]), r["g"]["id"]))
        pts = []
        for r in rows[-SERIES_GAME_CAP:]:
            g = r["g"]
            opp_tid = g.get(f"{r['other']}Id")
            where = "n" if g.get("neutralSite") else ("vs" if r["side"] == "home" else "@")
            tag = "W" if g.get("seasonType") == "regular" else "P"
            pts.append(
                R.point(
                    x=_game_label(g["season"], tag, g.get("week"), where, g.get(r["other"] + "Team")),
                    t=g["startDate"],
                    value=r["elo"],
                    quality_status="PARTIAL",
                    opponent_id=cfbd_to_pid.get(int(opp_tid)) if opp_tid is not None else None,
                    source="CFBD /games pregame Elo",
                )
            )
        ser = R.time_series(
            sport=SPORT,
            metric_id=elo_mid,
            entity_id=pid,
            entity_type="TEAM",
            x_axis="GAME",
            points=pts,
            as_of=fetched_at,
            run_id=run_id,
            generated_at=now,
            quality=ctx.q_cfbd,
            unit="Elo points",
            links=[
                R.link(rel="TEAM", target_kind="entity_profile", label=names[pid], target_id=pid, path=R.team_path(pid))
            ],
        )
        series_published.append(ser)
        series_refs.setdefault(pid, []).append(
            {
                "series_id": ser["series_id"],
                "metric_id": elo_mid,
                "x_axis": "GAME",
                "split": None,
                "path": R.series_path(ser["series_id"]),
            }
        )
    if elo_rows:
        published_metrics[ELO_SLUG] = R.metric(
            sport=SPORT,
            slug=ELO_SLUG,
            name="Pregame Elo",
            short_name="ELO",
            description="CFBD's pregame Elo rating for the team before each completed game (CFBD /games "
            "homePregameElo / awayPregameElo), as stored. Published only as a per-game series of the "
            f"last {SERIES_GAME_CAP} completed games through 2025.",
            entity_type="TEAM",
            category="ratings",
            stat_type="RATING",
            source="CFBD snapshot",
            quality=ctx.q_cfbd,
            freshness="STALE",
            higher_is_better=True,
            unit="Elo points",
            comparison_universe=None,
            supports=R.supports(time_series=True),
            windows=["GAME"],
            source_version=research.get("cache_version"),
            methodology_version=EXPORT_VERSION,
            historical_start=f"{min(tables)}-08-01",
            update_frequency="none (frozen snapshot)",
            known_limitations=lims + [f"series capped at the last {SERIES_GAME_CAP} completed games per team"],
        )


def _add_research_states(g: _Graph) -> None:
    """Opponent-adjusted walk-forward states from the v2 research dataset (RESEARCH)."""
    run_id = g.run_id
    now = g.now
    ctx = g.ctx
    research = g.research
    names = g.names
    cfbd_to_pid = g.cfbd_to_pid
    season_obs = g.season_obs
    series_refs = g.series_refs
    latest_obs = g.latest_obs
    published_metrics = g.published_metrics
    series_published = g.series
    fetched_at = research["fetched_at"]
    rows = research.get("dataset")
    if rows:
        meta = research.get("dataset_meta") or {}
        built_at = timeutil.to_iso(meta.get("built_at") or fetched_at)
        cfg = meta.get("state_config") or {}
        r_lims = [
            "research-branch output: walk-forward opponent-adjusted states built ad hoc on "
            "claude/cfb-model-v2-research-krupoc; the builder code (research/v2/state.py) is not on main",
            f"state config as stored: {json.dumps(cfg, sort_keys=True)}",
            "no 2026 in-season rows: the 2026 value is the pregame state for each team's first 2026 game "
            f"as built {timeutil.to_date(built_at)}",
            f"per-game series capped at the last {SERIES_GAME_CAP} completed games per team",
            LICENCE_NOTE,
        ]
        ctx.q_research = R.quality(
            status="RESEARCH",
            source="v2 research dataset (research-data branch, data/research/v2/dataset.parquet)",
            generated_at=now,
            production=False,
            data_as_of=built_at,
            source_version=meta.get("dataset_version"),
            methodology_version=meta.get("feature_hash"),
            limitations=r_lims,
            coverage="2015-2025 completed games; 2026 preseason state",
        )
        by_team: dict[int, list[tuple[dict, str]]] = {}
        for row in rows:
            for side in ("home", "away"):
                tid = row.get(f"{side}_id_cfbd")
                if tid is not None and int(tid) in cfbd_to_pid:
                    by_team.setdefault(int(tid), []).append((row, side))
        for spec in RESEARCH_METRICS:
            mid = build_metric_id(spec.slug)
            produced = False
            for tid, trows in sorted(by_team.items()):
                pid = cfbd_to_pid[tid]
                trows = sorted(trows, key=lambda rs: (timeutil.parse_ts(rs[0]["kickoff"]), str(rs[0]["game_id"])))
                col = partial(_state_value, spec)
                first_2026 = next((rs for rs in trows if int(rs[0]["season"]) == 2026 and col(rs) is not None), None)
                if first_2026 is not None:
                    obs = R.observation(
                        sport=SPORT,
                        metric_id=mid,
                        entity_id=pid,
                        entity_type="TEAM",
                        value=col(first_2026),
                        window=R.window("CUSTOM", label=f"pre-2026 state (built {timeutil.to_date(built_at)})"),
                        as_of=built_at,
                        source="v2 research dataset",
                        quality_status="RESEARCH",
                        unit=spec.unit,
                        season=2026,
                    )
                    season_obs.setdefault(pid, []).append(obs)
                    latest_obs[(pid, spec.slug)] = obs
                    produced = True
                if spec.series:
                    done = [rs for rs in trows if rs[0].get("completed") and col(rs) is not None][-SERIES_GAME_CAP:]
                    if done:
                        pts = []
                        for row, side in done:
                            other = "away" if side == "home" else "home"
                            opp_tid = row.get(f"{other}_id_cfbd")
                            where = "n" if row.get("neutral") else ("vs" if side == "home" else "@")
                            tag = "P" if str(row.get("season_type")) == "postseason" else "W"
                            pts.append(
                                R.point(
                                    x=_game_label(row["season"], tag, row.get("week"), where, row.get(other)),
                                    t=row["kickoff"],
                                    value=col((row, side)),
                                    quality_status="RESEARCH",
                                    opponent_id=cfbd_to_pid.get(int(opp_tid)) if opp_tid is not None else None,
                                    source="v2 research dataset (pregame state)",
                                )
                            )
                        ser = R.time_series(
                            sport=SPORT,
                            metric_id=mid,
                            entity_id=pid,
                            entity_type="TEAM",
                            x_axis="GAME",
                            points=pts,
                            as_of=built_at,
                            run_id=run_id,
                            generated_at=now,
                            quality=ctx.q_research,
                            unit=spec.unit,
                            links=[
                                R.link(
                                    rel="TEAM",
                                    target_kind="entity_profile",
                                    label=names[pid],
                                    target_id=pid,
                                    path=R.team_path(pid),
                                )
                            ],
                        )
                        series_published.append(ser)
                        series_refs.setdefault(pid, []).append(
                            {
                                "series_id": ser["series_id"],
                                "metric_id": mid,
                                "x_axis": "GAME",
                                "split": None,
                                "path": R.series_path(ser["series_id"]),
                            }
                        )
                        produced = True
            if produced:
                published_metrics[spec.slug] = R.metric(
                    sport=SPORT,
                    slug=spec.slug,
                    name=spec.name,
                    short_name=spec.short,
                    description=spec.description,
                    entity_type="TEAM",
                    category="opponent_adjusted",
                    stat_type="RATING",
                    source="v2 research dataset",
                    quality=ctx.q_research,
                    freshness="STALE",
                    higher_is_better=spec.higher_is_better,
                    unit=spec.unit,
                    comparison_universe=None,
                    supports=R.supports(time_series=spec.series, opponent_adjustment=True),
                    windows=["pre-2026 state"] + (["GAME"] if spec.series else []),
                    source_version=meta.get("dataset_version"),
                    methodology_version=meta.get("feature_hash"),
                    historical_start="2015-08-01",
                    update_frequency="none (frozen research artefact)",
                    known_limitations=r_lims,
                )


def build_explorer(
    *, v1: dict[str, Any], research: dict[str, Any], history: dict[str, Any], generated_at: str | None = None
) -> list[dict]:
    """Pure: the v1 bundle, the research-data tables and the catalog history in; contract documents out."""
    manifest = v1["manifest"]
    run_id = manifest["run_id"]
    now = timeutil.to_iso(generated_at or manifest["generated_at"])
    events = sorted(v1["events"], key=lambda e: (e["start_time_utc"], e["event_id"]))
    markets = sorted(v1["markets"], key=lambda m: m["kalshi_ticker"])
    wagers = v1["wagers"]
    participants = _participants(events)
    names = {pid: p["display_name"] for pid, p in participants.items()}
    ctx = Ctx(sport=SPORT, run_id=run_id, generated_at=now, cfbd_as_of=research.get("fetched_at"))
    captured = [m["captured_at"] for m in markets if m.get("captured_at")]
    market_as_of = max(captured, key=timeutil.parse_ts) if captured else now

    docs: list[dict] = []
    metric_docs: list[dict] = []

    # ---- CFBD tables -> per season values, rankings, Elo series
    tables = research.get("tables") or {}
    teams = cfbd_teams(tables) if research.get("present") else {}
    mapping, unmapped = map_participants(participants, teams, tables) if teams else ({}, sorted(participants))
    cfbd_to_pid = {m["cfbd_team_id"]: pid for pid, m in mapping.items()}

    g = _Graph(
        run_id=run_id,
        now=now,
        ctx=ctx,
        research=research,
        tables=tables,
        teams=teams,
        participants=participants,
        names=names,
        cfbd_to_pid=cfbd_to_pid,
    )
    if research.get("present"):
        _add_season_metrics(g)
        _add_elo_series(g)
        if research.get("dataset"):
            _add_research_states(g)
    season_obs, split_obs, ranking_refs, series_refs, latest_obs = (
        g.season_obs,
        g.split_obs,
        g.ranking_refs,
        g.series_refs,
        g.latest_obs,
    )
    published_metrics, rankings_published, series_published = g.published_metrics, g.rankings, g.series
    seasons_by_metric = g.seasons_by_metric

    # ---- market history (catalog git snapshots), one document per v1 event
    q_history = R.quality(
        status="PARTIAL",
        source="Kalshi market catalog git history on main (data/live)",
        generated_at=now,
        production=True,
        data_as_of=history["snapshots"][-1]["captured_at"] if history["snapshots"] else None,
        coverage=f"{len(history['snapshots'])} catalog snapshots",
        sample_size=len(history["snapshots"]),
        limitations=[
            HISTORY_LABEL,
            "a snapshot exists only when the catalog fingerprint changed (every 30 min in the Thu-Sun slate "
            "window, 6-hourly otherwise); a point is published only when the ticker's quote changed",
            "games are pruned after kickoff, so a ticker's last snapshot may precede kickoff by hours",
            "post-settlement 0/1 sentinel books are published as null quotes",
        ]
        + (
            [f"catalog git history unavailable for this run ({history['reason']}); working-tree snapshot only"]
            if not history.get("git")
            else []
        ),
    )
    market_ids_by_ticker = {m["kalshi_ticker"]: m["market_id"] for m in markets}
    history_docs: dict[str, dict] = {}
    for ev in events:
        key = (ev.get("source_ids") or {}).get("kalshi_game_key") or (ev.get("extensions") or {}).get("game_key")
        per_ticker: dict[str, list[dict]] = {}
        last: dict[str, tuple] = {}
        for snap in history["snapshots"]:
            quotes = snap["games"].get(key)
            if not quotes:
                continue
            for ticker, q in sorted(quotes.items()):
                # A new point only when the book or the last trade moved; volume / open interest ride along.
                if last.get(ticker) == q[:3]:
                    continue
                last[ticker] = q[:3]
                per_ticker.setdefault(ticker, []).append(
                    R.price_point(
                        captured_at=snap["captured_at"],
                        yes_bid=q[0],
                        yes_ask=q[1],
                        last_price=q[2],
                        volume=q[3],
                        open_interest=q[4],
                        source=snap["source"],
                    )
                )
        if not per_ticker:
            continue
        series = []
        for ticker, pts in sorted(per_ticker.items()):
            try:
                market_id = market_ids_by_ticker.get(ticker) or build_market_id(ticker)
            except ValueError:
                continue
            series.append({"market_id": market_id, "kalshi_ticker": ticker, "points": pts})
        as_of = max((p["captured_at"] for s in series for p in s["points"]), key=timeutil.parse_ts)
        mh_links = [
            R.link(
                rel="EVENT_RESEARCH",
                target_kind="event_research",
                label="event research",
                target_id=ev["event_id"],
                path=R.event_path(ev["event_id"]),
            )
        ]
        # Size the document before the (validating) constructor; trim each ticker to its most recent states
        # only when the full history would exceed the budget.
        quality = q_history
        draft = {"series": series, "quality": q_history, "links": mh_links, "pad": "x" * 600}
        keep = max(len(s_["points"]) for s_ in series)
        while doc_bytes(draft) > BUDGETS["market_history"] and keep > 1:
            keep = max(1, int(keep * 0.85))
            quality = dict(
                q_history,
                limitations=q_history["limitations"]
                + [
                    f"trimmed to each ticker's last {keep} quote states to stay under {BUDGETS['market_history']} bytes"
                ],
            )
            draft = dict(draft, quality=quality, series=[dict(s_, points=s_["points"][-keep:]) for s_ in series])
        history_docs[ev["event_id"]] = R.market_history(
            sport=SPORT,
            run_id=run_id,
            generated_at=now,
            event_id=ev["event_id"],
            as_of=as_of,
            series=draft["series"],
            quality=quality,
            links=mh_links,
        )

    # ---- metric registry
    registry = R.metric_registry(sport=SPORT, run_id=run_id, generated_at=now, metrics=list(published_metrics.values()))
    metric_names = {m["metric_id"]: m["name"] for m in registry["items"]}
    metric_docs.append(registry)

    # ---- profiles
    markets_by_event: dict[str, list[dict]] = {}
    for m in markets:
        if m.get("event_id"):
            markets_by_event.setdefault(m["event_id"], []).append(m)
    events_by_pid: dict[str, list[dict]] = {}
    for ev in events:
        for p in ev["participants"]:
            events_by_pid.setdefault(p["participant_id"], []).append(ev)
    season_label = next((str(ev.get("season")) for ev in events if ev.get("season")), None)
    unmapped_note = (
        f"{len(unmapped)} of {len(participants)} v1 participants have no exact CFBD identity mapping "
        "(FCS programs are not in CFBD teams_fbs) and publish without CFBD metrics"
    )
    profiles: dict[str, dict] = {}
    for pid, p in sorted(participants.items()):
        evs = events_by_pid.get(pid, [])
        games, opponents, links = [], {}, []
        for ev in evs:
            opp = next((x for x in ev["participants"] if x["participant_id"] != pid), None)
            games.append(
                R.game_ref(
                    event_id=ev["event_id"],
                    start_time_utc=ev["start_time_utc"],
                    status=ev["status"],
                    opponent_id=opp["participant_id"] if opp else None,
                    opponent_name=opp["display_name"] if opp else None,
                    home_away=_home_away(ev, pid),
                    competition=ev.get("competition"),
                    path=R.event_path(ev["event_id"]),
                )
            )
            links.append(
                R.link(
                    rel="EVENT",
                    target_kind="event_research",
                    label=_event_label(ev, names),
                    target_id=ev["event_id"],
                    path=R.event_path(ev["event_id"]),
                )
            )
            if opp:
                o = opponents.setdefault(
                    opp["participant_id"],
                    {
                        "participant_id": opp["participant_id"],
                        "display_name": opp["display_name"],
                        "event_ids": [],
                        "path": R.team_path(opp["participant_id"]),
                    },
                )
                o["event_ids"].append(ev["event_id"])
        for o in opponents.values():
            links.append(
                R.link(
                    rel="OPPONENT",
                    target_kind="entity_profile",
                    label=o["display_name"],
                    target_id=o["participant_id"],
                    path=o["path"],
                )
            )
        team_markets = [
            R.market_ref(m)
            for ev in evs
            for m in markets_by_event.get(ev["event_id"], [])
            if m.get("participant_id") == pid
        ]
        mp = mapping.get(pid)
        obs = season_obs.get(pid, [])
        keep_seasons: dict[str, set] = {}
        for o in obs:
            keep_seasons.setdefault(o["metric_id"], set()).add(o["season"])
        trimmed = []
        for o in sorted(obs, key=lambda o: (o["metric_id"], str(o["season"])), reverse=False):
            recent = sorted(keep_seasons[o["metric_id"]])[-PROFILE_SEASONS:]
            if o["season"] in recent:
                trimmed.append(o)
        trimmed.sort(
            key=lambda o: (
                MATCHUP_ORDER.index(o["metric_id"].split(".", 1)[1])
                if o["metric_id"].split(".", 1)[1] in MATCHUP_ORDER
                else 99,
                o["metric_id"],
                -(int(o["season"] or 0)),
            )
        )
        splits = {}
        sp = split_obs.get(pid, [])
        if sp:
            latest = max(int(o["season"]) for o in sp)
            splits["play_type"] = sorted(
                (o for o in sp if int(o["season"]) == latest), key=lambda o: (o["metric_id"], o["split"]["value"])
            )
        if mp:
            t = teams[mp["cfbd_team_id"]]
            loc = t.get("location") or {}
            ext = {
                "cfbd": {
                    "team_id": mp["cfbd_team_id"],
                    "school": mp["school"],
                    "mapping_method": mp["method"],
                    "abbreviation": mp["abbreviation"],
                    "abbreviation_matches_kalshi_code": mp["abbreviation_matches_kalshi_code"],
                    "conference": t.get("conference"),
                    "fbs_seasons": t.get("fbs_seasons"),
                    "home_stadium": {
                        k: loc.get(k)
                        for k in ("name", "city", "state", "timezone", "elevation", "capacity", "grass", "dome")
                    }
                    if loc
                    else None,
                    "snapshot": _cfbd_snapshot_label(research["fetched_at"]),
                }
            }
            q = R.quality(
                status="PARTIAL",
                source="Kalshi catalog + CFBD snapshot",
                generated_at=now,
                production=False,
                data_as_of=research["fetched_at"],
                methodology_version=EXPORT_VERSION,
                limitations=_cfbd_limitations(research["fetched_at"])
                + [
                    f"profile observations cover the latest {PROFILE_SEASONS} seasons per metric; every "
                    "season is in the linked rankings"
                ]
                + (["opponent-adjusted values are RESEARCH (research-branch output)"] if ctx.q_research else []),
            )
        else:
            reason = (
                research["reason"]
                if not research.get("present")
                else "no exact CFBD identity mapping (FCS program or unmatched name)"
            )
            ext = {"cfbd": None, "cfbd_unmapped_reason": reason}
            q = R.quality(
                status="PARTIAL",
                source="Kalshi market catalog",
                generated_at=now,
                production=True,
                data_as_of=market_as_of,
                methodology_version=EXPORT_VERSION,
                limitations=[f"{reason}: the profile carries Kalshi markets and games only"],
            )
        rk_refs = sorted(ranking_refs.get(pid, []), key=lambda r: (r["metric_id"], r["window_label"]))

        common = dict(
            sport=SPORT,
            run_id=run_id,
            generated_at=now,
            entity=p,
            entity_type="TEAM",
            season=season_label,
            league=next((ev.get("league") for ev in evs if ev.get("league")), None),
            metrics=trimmed,
            splits=splits,
            series=series_refs.get(pid, []),
            games=games,
            opponents=sorted(opponents.values(), key=lambda o: o["participant_id"]),
            markets=team_markets,
            links=links,
            extensions=ext,
        )
        prof = R.entity_profile(**common, rankings=rk_refs, quality=q)
        if doc_bytes(prof) > BUDGETS["entity_profile"]:
            shown = {(o["metric_id"], f"{o['season']} season") for o in trimmed}
            lims = q["limitations"] + [
                "ranking links limited to the seasons shown, to stay under the profile size budget"
            ]
            prof = R.entity_profile(
                **common,
                rankings=[r for r in rk_refs if (r["metric_id"], r["window_label"]) in shown],
                quality=dict(q, limitations=lims),
            )
        profiles[pid] = prof

    # ---- event research
    event_docs: dict[str, dict] = {}
    wagers_by_event: dict[str, list[str]] = {}
    for w in wagers:
        if w.get("event_id"):
            wagers_by_event.setdefault(w["event_id"], []).append(w["wager_id"])
    for ev in events:
        eid = ev["event_id"]
        parts = [
            {
                "participant_id": x["participant_id"],
                "display_name": x["display_name"],
                "home_away": _home_away(ev, x["participant_id"]),
                "path": R.team_path(x["participant_id"]),
            }
            for x in ev["participants"]
        ]
        matchup = []
        home, away = ev.get("home_participant"), ev.get("away_participant")
        if home and away:
            for slug in MATCHUP_ORDER:
                ho, ao = latest_obs.get((home, slug)), latest_obs.get((away, slug))
                if ho is None and ao is None:
                    continue
                mid = build_metric_id(slug)
                note = None
                if ho is None or ao is None:
                    missing = names[home] if ho is None else names[away]
                    note = f"no CFBD value for {missing}"
                elif ho.get("season") != ao.get("season"):
                    note = f"seasons differ: home {ho.get('season')}, away {ao.get('season')}"
                matchup.append({"metric_id": mid, "name": metric_names[mid], "home": ho, "away": ao, "note": note})
        notes = [MODEL_RETIRED]
        ha_source = (ev.get("extensions") or {}).get("home_away_source")
        if ha_source:
            notes.append(
                f"home/away from the Kalshi catalog ({ha_source}, "
                f"{(ev.get('extensions') or {}).get('home_away_confidence')}); neutral sites are not stated"
            )
        if research.get("present"):
            notes.append("Team metrics: " + _cfbd_snapshot_label(research["fetched_at"]) + ".")
        else:
            notes.append(f"Team metrics unavailable this run: {research['reason']}.")
        links = [
            R.link(
                rel="TEAM",
                target_kind="entity_profile",
                label=x["display_name"],
                target_id=x["participant_id"],
                path=x["path"],
            )
            for x in parts
        ]
        mh_path = None
        if eid in history_docs:
            mh_path = R.market_history_path(eid)
            links.append(
                R.link(
                    rel="MARKET_HISTORY",
                    target_kind="market_history",
                    label="price history",
                    target_id=eid,
                    path=mh_path,
                )
            )
        ev_lims = [MODEL_RETIRED]
        if research.get("present"):
            ev_lims.append(
                "matchup rows place the two teams' frozen CFBD season aggregates (latest stored season) "
                "and RESEARCH opponent-adjusted states side by side; FCS teams have none"
            )
        else:
            ev_lims.append(f"no matchup rows this run: {research['reason']}")
        q_ev = R.quality(
            status="PARTIAL",
            source="Kalshi market catalog + accounting ledger + CFBD snapshot",
            generated_at=now,
            production=True,
            data_as_of=market_as_of,
            limitations=ev_lims,
        )
        projections = [
            R.projection_ref(mp, research_only=True, authority="RESEARCH_ONLY", quality_status="RESEARCH")
            for mp in v1.get("model_prices", [])
            if mp.get("event_id") == eid
        ]
        refs = [R.market_ref(m) for m in markets_by_event.get(eid, [])]

        common = dict(
            sport=SPORT,
            run_id=run_id,
            generated_at=now,
            event=ev,
            quality=q_ev,
            participants=parts,
            projections=projections,
            markets=refs,
            market_history_path=mh_path,
            wagers=sorted(wagers_by_event.get(eid, [])),
            links=links,
        )
        doc = R.event_research(**common, matchup=matchup, context={"notes": notes})
        rows, size = list(matchup), doc_bytes(doc)
        if size > BUDGETS["event_research"]:
            # Markets are never dropped; matchup rows go from the end until the document fits.
            row_bytes = [doc_bytes(r) + 1 for r in matchup]
            while rows and size + 200 > BUDGETS["event_research"]:
                size -= row_bytes[len(rows) - 1]
                rows = rows[:-1]
            trim_note = (
                f"matchup trimmed to its first {len(rows)} rows (of {len(matchup)}) to keep "
                f"every market under the {BUDGETS['event_research']}-byte budget"
            )
            doc = R.event_research(**common, matchup=rows, context={"notes": notes + [trim_note]})
        event_docs[eid] = doc

    # ---- capability manifest
    caps = _capabilities(
        ctx=ctx,
        research=research,
        profiles=profiles,
        event_docs=event_docs,
        history_docs=history_docs,
        rankings=rankings_published,
        series=series_published,
        published_metrics=published_metrics,
        mapping=mapping,
        unmapped=unmapped,
        unmapped_note=unmapped_note,
        history=history,
        wagers=wagers,
        seasons_by_metric=seasons_by_metric,
    )
    split_dims = (
        [{"dimension": "play_type", "values": ["pass", "rush"], "status": "PARTIAL"}] if any(split_obs.values()) else []
    )
    windows = sorted({s for seasons in seasons_by_metric.values() for s in seasons})
    manifest_doc = R.capability_manifest(
        sport=SPORT,
        run_id=run_id,
        generated_at=now,
        capabilities=caps,
        audit_date=AUDIT_DATE,
        split_dimensions=split_dims,
        windows=[season_window(s) for s in windows],
        notes=[
            "CFB's live, maintained surface is market inventory + market history + accounting; everything "
            "team-level is a frozen CFBD corpus on the research-data branch that nothing refreshes.",
            MODEL_RETIRED,
            LICENCE_NOTE,
            unmapped_note,
        ],
    )

    # ---- search index
    entries = []
    for pid, prof in profiles.items():
        p = prof["entity"]
        mp = mapping.get(pid)
        aliases = [p.get("short_name")] + (
            [mp["school"]] + list(teams[mp["cfbd_team_id"]].get("alternateNames") or []) if mp else []
        )
        entries.append(
            R.search_entry(
                id=pid,
                kind="TEAM",
                label=p["display_name"],
                path=R.team_path(pid),
                sport=SPORT,
                secondary=(teams[mp["cfbd_team_id"]].get("conference") if mp else None),
                aliases=sorted({a for a in aliases if a and a != p["display_name"]}),
                league=prof.get("league"),
                season=season_label,
            )
        )
    for eid, doc in event_docs.items():
        ev = doc["event"]
        entries.append(
            R.search_entry(
                id=eid,
                kind="EVENT",
                label=_event_label(ev, names),
                path=R.event_path(eid),
                sport=SPORT,
                secondary=f"{timeutil.to_date(ev['start_time_utc'])} {ev.get('competition') or ''}".strip(),
                aliases=[(ev.get("extensions") or {}).get("game_key") or ""],
                league=ev.get("league"),
                season=ev.get("season"),
            )
        )
    for m in registry["items"]:
        entries.append(
            R.search_entry(
                id=m["metric_id"],
                kind="METRIC",
                label=m["name"],
                path=R.app_path(R.METRICS_NAME),
                sport=SPORT,
                secondary=m["category"],
                aliases=[m["short_name"]],
            )
        )
    for rk in rankings_published:
        entries.append(
            R.search_entry(
                id=rk["ranking_id"],
                kind="RANKING",
                label=f"{metric_names[rk['metric_id']]} {rk['universe']['season']}",
                path=R.ranking_path(rk["ranking_id"]),
                sport=SPORT,
                season=rk["universe"]["season"],
            )
        )
    search = R.search_index(sport=SPORT, run_id=run_id, generated_at=now, entries=entries)

    docs.extend(metric_docs)
    docs.extend(rankings_published)
    docs.extend(series_published)
    docs.extend(profiles[pid] for pid in sorted(profiles))
    docs.extend(event_docs[eid] for eid in sorted(event_docs))
    docs.extend(history_docs[eid] for eid in sorted(history_docs))
    docs.append(manifest_doc)
    docs.append(search)
    return docs


def build_metric_id(slug: str) -> str:
    return ids.metric_id(SPORT, slug)


def build_market_id(ticker: str) -> str:
    return ids.market_id(ticker)


# ------------------------------------------------------------------ the capability manifest


def _capabilities(
    *,
    ctx: Ctx,
    research: dict,
    profiles: dict,
    event_docs: dict,
    history_docs: dict,
    rankings: list,
    series: list,
    published_metrics: dict,
    mapping: dict,
    unmapped: list,
    unmapped_note: str,
    history: dict,
    wagers: list,
    seasons_by_metric: dict,
) -> list[dict]:
    C = R.capability
    U = "UNAVAILABLE"
    mid = build_metric_id
    caps: list[dict] = []
    have_cfbd = bool(research.get("present"))
    have_research = ctx.q_research is not None
    mapped_profile = next((R.team_path(pid) for pid in sorted(mapping) if pid in profiles), None)
    any_profile = next((R.team_path(pid) for pid in sorted(profiles)), None)
    cfbd_reason = [research["reason"]] if not have_cfbd else []
    cfbd_lims = _cfbd_limitations(research["fetched_at"]) if have_cfbd else []
    seasons = sorted({s for ss in seasons_by_metric.values() for s in ss})
    coverage = f"{seasons[0]}-{seasons[-1]} seasons, {len(mapping)} mapped FBS teams" if seasons else None
    since = f"{seasons[0]}-08-01" if seasons else None
    season_metric_ids = [mid(s.slug) for s in SEASON_METRICS if s.slug in published_metrics]

    # profiles / events
    if any_profile:
        caps.append(
            C(
                capability="team_profiles",
                status="PARTIAL",
                entity_types=["TEAM"],
                summary=f"{len(profiles)} team profiles (every v1 participant); {len(mapping)} carry CFBD "
                "season aggregates",
                evidence=[mapped_profile or any_profile],
                limitations=cfbd_lims + [unmapped_note]
                if have_cfbd
                else [f"{research['reason']}: profiles carry Kalshi markets and games only"],
                coverage=f"{len(profiles)} teams on the current board",
            )
        )
    caps.append(
        C(
            capability="player_profiles",
            status=U,
            summary="no player-level data exists in this repository",
            reasons=["no rosters, player stats, player game logs, usage or depth charts anywhere (audit §1.6)"],
        )
    )
    first_event = next(iter(sorted(event_docs)), None)
    if first_event:
        caps.append(
            C(
                capability="event_research",
                status="PARTIAL",
                entity_types=["EVENT"],
                summary=f"{len(event_docs)} event research documents (one per v1 event)",
                evidence=[R.event_path(first_event)],
                limitations=[
                    MODEL_RETIRED,
                    "matchup rows exist only where a team maps to CFBD (FBS) and use the frozen snapshot"
                    if have_cfbd
                    else f"no matchup rows: {research['reason']}",
                ],
                coverage=f"{len(event_docs)} events",
            )
        )
    # team metrics / advanced stats / rankings / comparisons
    if season_metric_ids and mapped_profile:
        caps.append(
            C(
                capability="team_metrics",
                status="PARTIAL",
                entity_types=["TEAM"],
                summary="season-level CFBD aggregates: SP+, season advanced, talent, recruiting, returning production",
                evidence=[mapped_profile],
                limitations=cfbd_lims,
                coverage=coverage,
                since=since,
                metrics=season_metric_ids,
                windows=[f"{s} season" for s in seasons],
            )
        )
        adv = [mid(s.slug) for s in SEASON_METRICS if s.table == "season_advanced" and s.slug in published_metrics]
        caps.append(
            C(
                capability="advanced_stats",
                status="PARTIAL",
                entity_types=["TEAM"],
                summary="CFBD season advanced PPA / success rate / explosiveness / havoc (offense and defense)",
                evidence=[mapped_profile],
                limitations=cfbd_lims,
                coverage=coverage,
                since=since,
                metrics=adv,
            )
        )
    else:
        for cap in ("team_metrics", "advanced_stats"):
            caps.append(
                C(
                    capability=cap,
                    status=U,
                    summary="CFBD season aggregates not available this run",
                    reasons=cfbd_reason or ["no CFBD season table with values"],
                )
            )
    if rankings:
        caps.append(
            C(
                capability="rankings",
                status="PARTIAL",
                entity_types=["TEAM"],
                summary=f"{len(rankings)} rankings: every CFBD season metric over all FBS teams with a value, "
                "per season",
                evidence=[R.ranking_path(rankings[0]["ranking_id"])],
                limitations=cfbd_lims
                + [
                    "rank and percentile are arithmetic over stored values (contract "
                    "competition ranking); FBS universe per season"
                ],
                coverage=coverage,
                since=since,
                metrics=season_metric_ids,
            )
        )
        caps.append(
            C(
                capability="comparisons",
                status="PARTIAL",
                entity_types=["TEAM", "MATCHUP"],
                summary="every observation carries rank / universe / league average / best / worst; event "
                "matchup rows compare home and away",
                evidence=[R.ranking_path(rankings[0]["ranking_id"]), mapped_profile],
                limitations=cfbd_lims,
                coverage=coverage,
                metrics=season_metric_ids,
            )
        )
    else:
        for cap in ("rankings", "comparisons"):
            caps.append(
                C(
                    capability=cap,
                    status=U,
                    summary="no comparison universe this run",
                    reasons=cfbd_reason or ["no CFBD season table with values"],
                )
            )
    if series:
        caps.append(
            C(
                capability="time_series",
                status="PARTIAL",
                entity_types=["TEAM"],
                summary="per-game pregame Elo (CFBD, PARTIAL) and opponent-adjusted states (RESEARCH)",
                evidence=[R.series_path(series[0]["series_id"])],
                limitations=cfbd_lims
                + [
                    f"per-game series capped at the last {SERIES_GAME_CAP} completed games "
                    "per team; no 2026 in-season points"
                ],
                coverage=coverage,
                metrics=sorted({s["metric_id"] for s in series}),
                windows=["GAME"],
            )
        )
    else:
        caps.append(
            C(
                capability="time_series",
                status=U,
                summary="no per-game series this run",
                reasons=cfbd_reason or ["no stored per-game values"],
            )
        )
    situational = [mid(s.slug) for s in SEASON_METRICS if s.splits and s.slug in published_metrics]
    if situational and mapped_profile:
        caps.append(
            C(
                capability="situational_splits",
                status="PARTIAL",
                entity_types=["TEAM"],
                summary="rush / pass splits of season PPA and success rate (latest season in profiles)",
                evidence=[mapped_profile],
                limitations=cfbd_lims
                + [
                    "only the play_type dimension is published; home/away, conference, down and "
                    "garbage-time splits stored in per-game CFBD rows are not republished"
                ],
                metrics=situational,
                splits=["play_type"],
                coverage=coverage,
            )
        )
    else:
        caps.append(
            C(
                capability="situational_splits",
                status=U,
                summary="no split published this run",
                reasons=cfbd_reason or ["no split values"],
            )
        )
    if have_research:
        research_ids = [mid(s.slug) for s in RESEARCH_METRICS if s.slug in published_metrics]
        caps.append(
            C(
                capability="opponent_adjustment",
                status="RESEARCH",
                entity_types=["TEAM"],
                summary="walk-forward opponent-adjusted offense/defense states from the v2 research dataset",
                evidence=[
                    next(
                        (R.series_path(s["series_id"]) for s in series if s["metric_id"] in research_ids),
                        mapped_profile or any_profile,
                    )
                ],
                limitations=ctx.q_research["limitations"],
                metrics=research_ids,
                coverage="2015-2025",
            )
        )
        caps.append(
            C(
                capability="matchup_metrics",
                status="RESEARCH",
                entity_types=["MATCHUP"],
                summary="event matchup rows place both teams' season aggregates and research states side by side",
                evidence=[R.event_path(first_event)] if first_event else [],
                limitations=[
                    "no matchup-specific metric is computed; rows are the two teams' own values",
                    "the audit rates stored matchup features (v2 diff_/sum_) RESEARCH; they are not republished",
                ],
            )
        )
    else:
        reason = cfbd_reason or [research.get("dataset_reason") or "v2 research dataset not read"]
        caps.append(
            C(capability="opponent_adjustment", status=U, summary="research states not read this run", reasons=reason)
        )
        caps.append(
            C(capability="matchup_metrics", status=U, summary="no research matchup values this run", reasons=reason)
        )
    # opponents / results / logs
    caps.append(
        C(
            capability="opponents",
            status="PARTIAL",
            entity_types=["TEAM"],
            summary="current-slate opponents from the Kalshi catalog in every profile; historical opponents only "
            "as labels on per-game series points",
            evidence=[any_profile] if any_profile else [],
            limitations=["historical results/scores are not republished (CFBD licence)", "2026 results are not stored"],
        )
        if any_profile
        else C(capability="opponents", status=U, summary="no profile", reasons=["no participants"])
    )
    for cap, why in (
        (
            "team_game_logs",
            "CFBD per-game box-score / advanced / drive rows exist for 2014-2025 (audit "
            "PARTIAL) but are not republished",
        ),
        (
            "historical_results",
            "CFBD game results 2014-2025 exist (audit PARTIAL) but are not "
            "republished; 2026 results stop mid-September",
        ),
    ):
        caps.append(
            C(
                capability=cap,
                status=U,
                summary="not published by this adapter",
                reasons=[why, LICENCE_NOTE] + cfbd_reason,
            )
        )
    for cap, why in (
        ("player_metrics", "no player stats anywhere (CFBD /roster deliberately not fetched)"),
        ("player_game_logs", "no player game logs anywhere"),
        ("usage", "returning-production usage is a team-level share, not player usage"),
        ("lineups", "starting_qb and depth_chart appear in zero source files"),
        ("play_by_play", "CFBD /plays was never fetched (too expensive); drive rows are not republished"),
        ("schedule_strength", "nothing computes strength of schedule; CFBD SP+ sos is null"),
        ("recent_form_windows", "no stored L3/L5 windows; recency lives only inside research states"),
        (
            "injuries",
            "ESPN injury capture (RESEARCH, hibernated 2026-09-17) holds unresolved $ref URLs "
            "for September games only; not published",
        ),
        (
            "weather",
            "Open-Meteo capture (RESEARCH) is hibernated since 2026-09-17 and covers no current event; not published",
        ),
        (
            "projection_distributions",
            "retired-model research artefacts (2021-2025, frozen slate) cover no current event; not published",
        ),
        ("raw_projections", "the CFB model is retired; model_prices.json is empty by design"),
        ("calibration", "model-era calibration artefacts are RESEARCH and cover the retired model only"),
        ("historical_accuracy", "model-era benchmark artefacts are RESEARCH (retired model); not published"),
        ("clv", "owner CLV is not computed; the catalog is not a near-kickoff capture"),
    ):
        caps.append(C(capability=cap, status=U, summary="not available", reasons=[why]))
    # venue
    if mapped_profile:
        caps.append(
            C(
                capability="venue_effects",
                status="PARTIAL",
                entity_types=["TEAM"],
                summary="CFBD home-stadium attributes (elevation, dome, grass, capacity, timezone) in mapped "
                "team profiles",
                evidence=[mapped_profile],
                limitations=[
                    "no venue-effect estimates exist",
                    "event venues and neutral sites are not stated by the Kalshi catalog",
                ]
                + cfbd_lims,
            )
        )
    else:
        caps.append(
            C(
                capability="venue_effects",
                status=U,
                summary="no venue attributes this run",
                reasons=cfbd_reason or ["no mapped team"],
            )
        )

    # markets
    def _event_with(families: tuple[str, ...]) -> str | None:
        for eid in sorted(event_docs):
            if any(m["market_family"] in families for m in event_docs[eid]["markets"]):
                return R.event_path(eid)
        return None

    n_markets = sum(len(d["markets"]) for d in event_docs.values())
    if first_event and n_markets:
        caps.append(
            C(
                capability="market_prices",
                status="VERIFIED",
                entity_types=["MARKET"],
                summary=f"{n_markets} current Kalshi markets with bid/ask/last/volume/open interest",
                evidence=[R.event_path(first_event)],
                coverage=f"{len(event_docs)} events",
            )
        )
    else:
        caps.append(C(capability="market_prices", status=U, summary="no markets", reasons=["empty catalog"]))
    for cap, fams, status, extra in (
        ("game_markets", GAME_FAMILIES, "VERIFIED", []),
        ("team_props", TEAM_PROP_FAMILIES, "VERIFIED", []),
        ("player_props", PROP_FAMILIES, "PARTIAL", ["inventory only: no player data exists to research these markets"]),
    ):
        path = _event_with(fams)
        if path:
            caps.append(
                C(
                    capability=cap,
                    status=status,
                    entity_types=["MARKET"],
                    summary=f"Kalshi {', '.join(fams)} markets on the current board",
                    evidence=[path],
                    limitations=extra,
                )
            )
        else:
            caps.append(
                C(
                    capability=cap,
                    status=U,
                    summary="none on the current board",
                    reasons=[f"no {', '.join(fams)} market in the catalog"],
                )
            )
    if history_docs:
        points = sum(len(s["points"]) for d in history_docs.values() for s in d["series"])
        first = history["snapshots"][0]["captured_at"] if history["snapshots"] else None
        caps.append(
            C(
                capability="market_price_history",
                status="PARTIAL",
                entity_types=["MARKET"],
                summary=f"{points} quote states over {len(history['snapshots'])} catalog snapshots ({HISTORY_LABEL})",
                evidence=[R.market_history_path(sorted(history_docs)[0])],
                limitations=next(iter(history_docs.values()))["quality"]["limitations"]
                + [
                    "the model-era research observation ledger (Aug 26-Sep 16 2026) covers none of the current "
                    "events and is not republished"
                ],
                coverage=f"{len(history_docs)} events",
                since=first,
            )
        )
    else:
        caps.append(
            C(capability="market_price_history", status=U, summary="no snapshot", reasons=["no catalog history"])
        )
    with_wagers = sorted(eid for eid, d in event_docs.items() if d["wagers"])
    on_board = sum(len(event_docs[e]["wagers"]) for e in with_wagers)
    if with_wagers:
        caps.append(
            C(
                capability="wager_history",
                status="VERIFIED",
                entity_types=["EVENT"],
                summary=f"{len(wagers)} ledger wagers in v1 wagers.json; {on_board} reference games on the "
                "current board and are listed in their event research",
                evidence=[R.event_path(e) for e in with_wagers[:5]],
                coverage=f"{len(wagers)} wagers",
                since=min((w["placed_at"] for w in wagers), key=timeutil.parse_ts),
            )
        )
    elif wagers:
        caps.append(
            C(
                capability="wager_history",
                status="UNKNOWN",
                summary=f"{len(wagers)} ledger wagers are in v1 wagers.json but none references a game on the "
                "current board, so no explorer document lists one",
                reasons=["no on-board wager to link"],
            )
        )
    else:
        caps.append(
            C(
                capability="wager_history",
                status=U,
                summary="no wagers in this publication",
                reasons=["accounting-data checkout not supplied to the v1 export, or the ledger is empty"],
            )
        )
    caps.append(
        C(
            capability="search",
            status="VERIFIED",
            entity_types=["TEAM", "EVENT"],
            summary="static search over teams, events, metrics and rankings",
            evidence=[R.app_path(R.SEARCH_NAME)],
        )
    )
    return caps


# ------------------------------------------------------------------ publication


def publication_meta(*, v1: dict, research: dict, history: dict, generated_at: str) -> dict:
    """Index-level quality, as_of (the newest data timestamp) and warnings."""
    stamps = [m["captured_at"] for m in v1["markets"] if m.get("captured_at")]
    stamps += [w["placed_at"] for w in v1["wagers"] if w.get("placed_at")]
    stamps += [s["captured_at"] for s in history["snapshots"]]
    if research.get("fetched_at"):
        stamps.append(research["fetched_at"])
    as_of = max(stamps, key=timeutil.parse_ts) if stamps else None
    warnings = []
    if not research.get("present"):
        warnings.append(f"{research['reason']}: CFBD-backed capabilities are UNAVAILABLE for this run")
    elif research.get("dataset") is None:
        warnings.append(f"opponent-adjusted research states not read: {research.get('dataset_reason')}")
    if not history.get("git"):
        warnings.append(f"catalog git history not read ({history.get('reason')}); market history = working tree only")
    lims = [HISTORY_LABEL + " (market history)", MODEL_RETIRED]
    lims += _cfbd_limitations(research["fetched_at"]) if research.get("present") else [research["reason"]]
    quality = R.quality(
        status="PARTIAL",
        source="Kalshi market catalog + accounting ledger + CFBD snapshot",
        generated_at=generated_at,
        production=True,
        data_as_of=as_of,
        methodology_version=EXPORT_VERSION,
        limitations=lims,
    )
    return {"as_of": as_of, "warnings": warnings, "quality": quality}


def explorer_due(app_root: Path, *, now: str | None, min_interval_seconds: float) -> tuple[bool, str]:
    """``research.refresh_due`` against the wall clock (or ``now``)."""
    return R.refresh_due(Path(app_root), now=now or timeutil.now_utc(), min_interval_seconds=min_interval_seconds)


def export_explorer(
    app_root: Path,
    *,
    data_root: Path,
    research_root: Path | None,
    now: str | None = None,
    include_cfbd: bool = False,
    min_interval_seconds: float = 0,
    max_history_commits: int | None = None,
) -> dict:
    """Build and publish ``<app_root>/explorer``. Returns the explorer index, or ``{"skipped": True,
    "reason": ...}`` when ``min_interval_seconds`` > 0 and ``research.refresh_due`` says the published tree
    is still current (then nothing is touched). Raises on failure; the previous explorer tree is left
    untouched (``research.publish_explorer`` is atomic).

    ``include_cfbd`` is the owner's opt-in to publish CFBD-derived values (off by default while the CFBD
    redistribution licence is unresolved); without it ``research_root`` is never read."""
    app_root = Path(app_root)
    if min_interval_seconds > 0:
        due, reason = explorer_due(app_root, now=now, min_interval_seconds=min_interval_seconds)
        if not due:
            return {"skipped": True, "reason": reason}
    v1 = load_v1(app_root)
    manifest = v1["manifest"]
    generated_at = timeutil.to_iso(now or manifest["generated_at"])
    if include_cfbd:
        research = load_research(Path(research_root) if research_root else None)
    else:
        research = load_research(None, absent_reason=LICENCE_OPT_IN_REASON)
    game_keys = {
        str((ev.get("source_ids") or {}).get("kalshi_game_key"))
        for ev in v1["events"]
        if (ev.get("source_ids") or {}).get("kalshi_game_key")
    }
    history = load_catalog_history(Path(data_root), game_keys, max_commits=max_history_commits)
    documents = build_explorer(v1=v1, research=research, history=history, generated_at=generated_at)
    meta = publication_meta(v1=v1, research=research, history=history, generated_at=generated_at)
    caps = next(d for d in documents if d["kind"] == "capability_manifest")
    return R.publish_explorer(
        app_root=app_root,
        sport=SPORT,
        run_id=manifest["run_id"],
        generated_at=generated_at,
        documents=documents,
        quality=meta["quality"],
        as_of=meta["as_of"],
        commit_sha=manifest.get("commit_sha"),
        base_manifest_run_id=manifest["run_id"],
        windows=caps["windows"],
        warnings=meta["warnings"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="the app root the v1 export published into (app/latest)")
    parser.add_argument(
        "--data-root",
        default=os.path.join(REPO_ROOT, "data", "live"),
        help="the committed catalog directory whose git history is the market history (data/live)",
    )
    parser.add_argument(
        "--research-root",
        default=None,
        help="a read-only git archive of the research-data branch; absent/empty => CFBD capabilities "
        "UNAVAILABLE for this run",
    )
    parser.add_argument(
        "--now", default=None, help="generated_at and refresh clock (defaults: the v1 manifest's generated_at / now)"
    )
    parser.add_argument(
        "--include-cfbd",
        action="store_true",
        help="owner opt-in: publish CFBD-derived values from --research-root (default off: licence unresolved)",
    )
    parser.add_argument(
        "--min-interval-minutes",
        type=float,
        default=0,
        help="rebuild only when the v1 events changed or the published explorer is older than this (0 = always)",
    )
    parser.add_argument(
        "--check-due",
        action="store_true",
        help="print due=true|false and reason=... (GITHUB_OUTPUT lines) for --min-interval-minutes and exit",
    )
    args = parser.parse_args(argv)
    if args.check_due:
        due, reason = explorer_due(Path(args.out), now=args.now, min_interval_seconds=args.min_interval_minutes * 60)
        print(f"due={'true' if due else 'false'}")
        print(f"reason={reason}")
        return 0
    try:
        index = export_explorer(
            Path(args.out),
            data_root=Path(args.data_root),
            research_root=Path(args.research_root) if args.research_root else None,
            now=args.now,
            include_cfbd=args.include_cfbd,
            min_interval_seconds=args.min_interval_minutes * 60,
        )
    except Exception as exc:  # noqa: BLE001 - report and exit 1; the previous explorer tree is untouched
        print(
            f"research explorer export FAILED; previous explorer left untouched: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        traceback.print_exc()
        return 1
    if index.get("skipped"):
        print(json.dumps({"skipped": True, "reason": index["reason"]}, sort_keys=True))
        return 0
    caps = json.loads((Path(args.out) / R.EXPLORER_DIR / R.CAPABILITIES_NAME).read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "run_id": index["run_id"],
                "generated_at": index["generated_at"],
                "as_of": index["as_of"],
                "counts": index["counts"],
                "warnings": index["warnings"],
                "bytes": R.tree_bytes(Path(args.out)),
                "capabilities": {c["capability"]: c["status"] for c in caps["items"]},
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
