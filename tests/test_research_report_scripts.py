"""The season and weekly report writers, EXECUTED against a real sharded
corpus.

*** THE REGRESSION THESE EXIST FOR ***
Converting readers to `persistence.corpus_sources(...)` changed the
return type from a single `Path` to a LIST of source files (a legacy
monolith, if one is still present, plus every UTC-date shard). Two report
scripts kept their pre-sharding guard on that value:

    obs_path = persistence.corpus_sources(...)      # a list
    rows = persistence.read_observation_rows(obs_path) if obs_path.exists() else []
                                                       ^^^^^^^^^^^^^^^^^^
    AttributeError: 'list' object has no attribute 'exists'

Nothing caught it, because no test ever CALLED `_apply_report`. Both
would have raised on every run. These tests invoke the real functions end
to end and assert the report they write is correct, so the defect cannot
come back in silence.

An empty source list needs no guard at all: it reads back as an empty
result, which is exactly what "no corpus yet" means.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from research_factories import make_corpus_row, make_observation

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import research_season_report  # noqa: E402
import research_weekly_report  # noqa: E402

from cfb_edge_finder.research import persistence, shards  # noqa: E402
from cfb_edge_finder.research.settlement import extract_game_result, settle_market  # noqa: E402
from cfb_edge_finder.schemas.common import MarketFamily, Side  # noqa: E402

SEASON = 2026
NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)
GAME_ID = "cfb-2026-wk02-akron-at-wake-forest"


def _seed_sharded_corpus(repo_dir: Path, *, days: tuple[int, ...] = (12, 13)) -> Path:
    """A corpus spanning MULTIPLE date shards, written through the real
    append path. More than one shard is the point: a single-file
    assumption can pass against one shard by accident."""
    base = repo_dir / "data" / "research"
    base.mkdir(parents=True, exist_ok=True)
    for day in days:
        when = datetime(2026, 9, day, 10, tzinfo=UTC)
        observation = make_observation(
            game_id=GAME_ID,
            kalshi_market_ticker=f"KXNCAAFGAME-{day}",
            captured_at=when,
            family=MarketFamily.MONEYLINE,
            team=Side.HOME,
        )
        persistence.append_observation_rows(base, SEASON, [make_corpus_row(observation=observation)])
        result = extract_game_result(
            {"status": "final", "homePoints": 31, "awayPoints": 24},
            game_id=GAME_ID, season=SEASON, captured_at=when,
        )
        persistence.append_settlement_rows(base, SEASON, [settle_market(observation, result, settled_at=when)])
    return base


def _assert_multi_shard(base: Path) -> None:
    for family in (shards.OBSERVATIONS_SUBDIR, shards.SETTLEMENTS_SUBDIR):
        found = shards.shard_paths(base, family, SEASON)
        assert len(found) > 1, f"{family} should span several shards, got {[p.name for p in found]}"
        assert not shards.legacy_monolith_path(base, family, SEASON).exists()


# --- the season report ----------------------------------------------------


def test_season_report_runs_against_a_sharded_corpus(tmp_path):
    """THE regression guard for BLOCKER 1: this raised AttributeError on
    `list.exists()` before the fix, on every single run."""
    base = _seed_sharded_corpus(tmp_path)
    _assert_multi_shard(base)

    result = research_season_report._apply_report(tmp_path, season=SEASON, now=NOW)

    assert result.written == 1
    report_path = base / "reports" / "season" / f"{SEASON}-v1.json"
    assert report_path.is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["season"] == SEASON
    assert payload["report_version"] == 1


def test_season_report_reads_rows_from_every_shard(tmp_path):
    """Not merely 'it did not raise': the report must actually contain
    the rows that live in the later shards."""
    base = _seed_sharded_corpus(tmp_path, days=(10, 11, 12, 13))
    assert len(shards.shard_paths(base, shards.OBSERVATIONS_SUBDIR, SEASON)) == 4

    research_season_report._apply_report(tmp_path, season=SEASON, now=NOW)

    payload = json.loads(
        (base / "reports" / "season" / f"{SEASON}-v1.json").read_text(encoding="utf-8")
    )
    total = json.dumps(payload)
    assert "wk02" in total, "the week derived from sharded rows is missing from the report"
    index = persistence.load_observation_index_for(base, SEASON)
    assert index.row_count == 4, "fixture did not produce one row per shard"


def test_season_report_on_an_empty_corpus_needs_no_exists_guard(tmp_path):
    """An absent corpus must read back as empty rather than needing a
    `.exists()` check that a source LIST cannot answer."""
    (tmp_path / "data" / "research").mkdir(parents=True)
    assert persistence.corpus_sources(
        tmp_path / "data" / "research", shards.OBSERVATIONS_SUBDIR, SEASON
    ) == []

    result = research_season_report._apply_report(tmp_path, season=SEASON, now=NOW)

    assert result.written == 1
    assert (tmp_path / "data" / "research" / "reports" / "season" / f"{SEASON}-v1.json").is_file()


def test_season_report_still_reads_a_legacy_monolith(tmp_path):
    """The transition guarantee holds in the report path too."""
    base = tmp_path / "data" / "research"
    legacy = shards.legacy_monolith_path(base, shards.OBSERVATIONS_SUBDIR, SEASON)
    legacy.parent.mkdir(parents=True)
    row = make_corpus_row(observation=make_observation(game_id=GAME_ID))
    legacy.write_text(json.dumps(row.model_dump(mode="json"), sort_keys=True) + "\n", encoding="utf-8")

    result = research_season_report._apply_report(tmp_path, season=SEASON, now=NOW)
    assert result.written == 1


def test_season_report_versions_rather_than_overwriting(tmp_path):
    """Unchanged by sharding, and cheap to keep honest."""
    _seed_sharded_corpus(tmp_path)
    research_season_report._apply_report(tmp_path, season=SEASON, now=NOW)
    research_season_report._apply_report(tmp_path, season=SEASON, now=NOW)
    reports = tmp_path / "data" / "research" / "reports" / "season"
    assert (reports / f"{SEASON}-v1.json").is_file()
    assert (reports / f"{SEASON}-v2.json").is_file()
    latest = json.loads((reports / f"{SEASON}-latest.json").read_text(encoding="utf-8"))
    assert latest["latest_version"] == 2


# --- the weekly report ----------------------------------------------------


def test_weekly_report_runs_against_a_sharded_corpus(tmp_path):
    """The SAME defect lived here -- `settle_path.exists()` on a list --
    and was found by the repository-wide audit, not by the original
    report."""
    base = _seed_sharded_corpus(tmp_path)
    _assert_multi_shard(base)

    result = research_weekly_report._apply_report(
        tmp_path, season=SEASON, week_label="wk02", now=NOW
    )

    assert result.written == 1
    report_path = base / "reports" / "weekly" / f"{SEASON}-wk02.json"
    assert report_path.is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["season"] == SEASON
    assert payload["week_label"] == "wk02"


def test_weekly_report_on_an_empty_corpus(tmp_path):
    (tmp_path / "data" / "research").mkdir(parents=True)
    result = research_weekly_report._apply_report(
        tmp_path, season=SEASON, week_label="wk02", now=NOW
    )
    assert result.written == 1


# --- the class of bug, guarded generally ---------------------------------


@pytest.mark.parametrize(
    "family",
    [shards.OBSERVATIONS_SUBDIR, shards.SETTLEMENTS_SUBDIR, shards.ATTRIBUTIONS_SUBDIR],
)
def test_corpus_sources_is_a_list_not_a_path(tmp_path, family):
    """The type contract the two defects violated. A list has no
    `.exists()`, so any caller that reaches for one is broken by
    construction -- state that here rather than leaving it implicit."""
    sources = persistence.corpus_sources(tmp_path, family, SEASON)
    assert isinstance(sources, list)
    assert not hasattr(sources, "exists")


def test_corpus_identifier_is_stable_and_not_a_repr_of_the_file_list(tmp_path):
    """Provenance fields name the corpus, not every shard in it. Using
    `str(corpus_sources(...))` would emit a Python repr of N Paths that
    churns whenever a new date shard appears."""
    base = _seed_sharded_corpus(tmp_path)
    identifier = persistence.corpus_identifier(base, shards.OBSERVATIONS_SUBDIR, SEASON)

    assert identifier == str(shards.shard_dir(base, shards.OBSERVATIONS_SUBDIR, SEASON))
    assert "PosixPath" not in identifier and "[" not in identifier

    # Adding another shard must not change the corpus's identity.
    persistence.append_observation_rows(
        base,
        SEASON,
        [
            make_corpus_row(
                observation=make_observation(
                    game_id=GAME_ID,
                    kalshi_market_ticker="KXNCAAFGAME-LATER",
                    captured_at=datetime(2026, 9, 20, 10, tzinfo=UTC),
                )
            )
        ],
    )
    assert persistence.corpus_identifier(base, shards.OBSERVATIONS_SUBDIR, SEASON) == identifier
