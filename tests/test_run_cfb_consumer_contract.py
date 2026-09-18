"""The RUN CFB consumer contract, enforced.

The catalog deliberately publishes an INCOMPLETE capture rather than
suppressing every game it did capture. That is only safe if a consumer
cannot accidentally treat the incomplete one as complete. These tests are
what make "cannot" true rather than "should not".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.catalog.consumer import (
    GameUsability,
    IncompleteGameError,
    assess_game,
    assess_slate,
    require_usable_game,
)
from cfb_edge_finder.catalog.freshness import (
    OFF_PEAK_CADENCE_MINUTES,
    SLATE_WINDOW_CADENCE_MINUTES,
    CatalogFreshness,
    assess_freshness,
    expected_cadence_minutes,
    in_slate_window,
)


def _entry(complete=True, **overrides):
    completeness = {
        "native_game_markets_complete": complete,
        "related_event_tickers_reported": 29,
        "events_fetched": 29,
        "failed_event_tickers": [],
        "pagination_failed_event_tickers": [],
        "api_failures": 0,
        "markets_discovered": 303,
    }
    completeness.update(overrides.pop("completeness", {}))
    entry = {
        "game_key": "26SEP19UGAARK",
        "title": "Georgia at Arkansas",
        "market_count": 303,
        "markets_file": "games/26SEP19UGAARK.json",
        "completeness": completeness,
    }
    entry.update(overrides)
    return entry


# =========================================================================
# THE RULE
# =========================================================================


def test_a_complete_game_may_be_handicapped():
    verdict = assess_game(_entry(complete=True))
    assert verdict.may_handicap is True
    assert verdict.usability is GameUsability.USABLE
    assert "USABLE" in verdict.explain()


def test_an_incomplete_game_may_not_be_handicapped():
    """THE load-bearing test."""
    verdict = assess_game(
        _entry(
            complete=False,
            completeness={
                "native_game_markets_complete": False,
                "failed_event_tickers": ["KXNCAAFSPREAD-26SEP19UGAARK"],
                "events_fetched": 28,
                "api_failures": 1,
            },
        )
    )
    assert verdict.may_handicap is False
    assert verdict.usability is GameUsability.UNAVAILABLE_INCOMPLETE_DISCOVERY


def test_the_refusal_names_the_failed_event_diagnostics():
    """Refusing is not enough -- a consumer must be told WHICH events
    failed, or it cannot tell a broken capture from a thin game."""
    verdict = assess_game(
        _entry(
            complete=False,
            completeness={
                "native_game_markets_complete": False,
                "failed_event_tickers": ["KXNCAAFSPREAD-26SEP19UGAARK"],
                "pagination_failed_event_tickers": ["KXNCAAFTOTAL-26SEP19UGAARK"],
                "events_fetched": 27,
                "related_event_tickers_reported": 29,
                "api_failures": 2,
            },
        )
    )
    explanation = verdict.explain()
    assert "UNAVAILABLE" in explanation
    assert "market discovery" in explanation and "incomplete" in explanation
    assert "KXNCAAFSPREAD-26SEP19UGAARK" in explanation
    assert "KXNCAAFTOTAL-26SEP19UGAARK" in explanation
    assert "27 of 29" in explanation
    assert "PARTIAL" in explanation, "the partial menu must be named as partial"


def test_an_incomplete_game_does_not_pretend_its_menu_is_complete():
    """It still reports how many contracts it HAS -- suppressing that
    would hide evidence -- but flags them as a partial menu."""
    verdict = assess_game(_entry(complete=False, market_count=41))
    assert verdict.market_count == 41
    assert "must not be used for bet selection" in verdict.explain()


def test_require_usable_game_is_a_hard_stop():
    """A guard nobody can forget to call, because it raises."""
    require_usable_game(_entry(complete=True))  # does not raise
    with pytest.raises(IncompleteGameError) as excinfo:
        require_usable_game(_entry(complete=False))
    assert "UNAVAILABLE" in str(excinfo.value)
    assert excinfo.value.verdict.usability is GameUsability.UNAVAILABLE_INCOMPLETE_DISCOVERY


def test_a_missing_completeness_flag_is_treated_as_not_proven_complete():
    """A missing flag must never read as permission -- the same 'a missing
    field means everything is fine' assumption the discovery layer
    refuses to make."""
    entry = _entry()
    del entry["completeness"]["native_game_markets_complete"]
    verdict = assess_game(entry)
    assert verdict.may_handicap is False
    assert "unproven" in verdict.reason


def test_a_game_absent_from_the_catalog_is_unavailable_not_empty():
    verdict = assess_game(None, game_key="26SEP19NOSUCH")
    assert verdict.may_handicap is False
    assert verdict.usability is GameUsability.UNAVAILABLE_NOT_IN_CATALOG
    assert "not present" in verdict.explain()


def test_unknown_family_markets_do_not_block_a_game():
    """An unclassified contract is a LABELLING gap, not a DISCOVERY gap.
    Blocking on it would punish the catalog for being honest."""
    verdict = assess_game(_entry(complete=True, completeness={"markets_unknown": 6}))
    assert verdict.may_handicap is True


def test_the_detail_document_gates_the_same_way_as_the_index_entry():
    """A consumer that opened only the per-game file must get the same
    answer as one reading the index."""
    detail = _entry(complete=False)
    detail.pop("markets_file")
    detail["markets"] = [{"market_ticker": "X"}]
    assert assess_game(detail).may_handicap is False


# =========================================================================
# SLATE-LEVEL BEHAVIOUR
# =========================================================================


def test_slate_incomplete_does_not_invalidate_individually_complete_games():
    """THE other load-bearing test. capture_complete is a slate-wide AND;
    gating on it would discard 238 good menus because one unrelated game
    got a 429."""
    catalog = {
        "capture": {"captured_at": "2026-09-18T00:21:10Z"},
        "completeness": {"capture_complete": False},
        "games": [
            _entry(complete=True, game_key="26SEP19GOODONE"),
            _entry(complete=True, game_key="26SEP19ALSOGOOD"),
            _entry(complete=False, game_key="26SEP19BROKEN"),
        ],
    }
    slate = assess_slate(catalog)
    assert slate.capture_complete is False
    assert slate.usable_game_keys == ["26SEP19GOODONE", "26SEP19ALSOGOOD"]
    assert [v.game_key for v in slate.unavailable] == ["26SEP19BROKEN"]
    assert slate.verdict_for("26SEP19GOODONE").may_handicap is True
    assert slate.verdict_for("26SEP19BROKEN").may_handicap is False


def test_slate_complete_means_every_game_passed():
    catalog = {
        "capture": {"captured_at": "2026-09-18T00:21:10Z"},
        "completeness": {"capture_complete": True},
        "games": [_entry(complete=True, game_key="26SEP19A"), _entry(complete=True, game_key="26SEP19B")],
    }
    slate = assess_slate(catalog)
    assert slate.capture_complete is True
    assert not slate.unavailable


def test_slate_summary_names_every_unavailable_game():
    catalog = {
        "capture": {"captured_at": "2026-09-18T00:21:10Z"},
        "completeness": {"capture_complete": False},
        "games": [_entry(complete=True, game_key="26SEP19OK"), _entry(complete=False, game_key="26SEP19BAD")],
    }
    summary = assess_slate(catalog).summary()
    assert "1 game(s) usable" in summary and "1 unavailable" in summary
    assert "26SEP19BAD" in summary


# =========================================================================
# FRESHNESS
# =========================================================================

# 2026-09-18 is a Friday -> inside the slate window.
FRIDAY = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
# 2026-09-15 is a Monday -> off-peak.
MONDAY = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def test_slate_window_matches_the_workflow_schedule():
    assert in_slate_window(datetime(2026, 9, 17, 18, 0, tzinfo=UTC)) is True  # Thu 18:00
    assert in_slate_window(datetime(2026, 9, 17, 17, 0, tzinfo=UTC)) is False  # Thu 17:00
    assert in_slate_window(FRIDAY) is True
    assert in_slate_window(datetime(2026, 9, 19, 23, 0, tzinfo=UTC)) is True  # Sat
    assert in_slate_window(datetime(2026, 9, 20, 5, 0, tzinfo=UTC)) is True  # Sun 05:00
    assert in_slate_window(datetime(2026, 9, 20, 7, 0, tzinfo=UTC)) is False  # Sun 07:00
    assert in_slate_window(MONDAY) is False
    assert expected_cadence_minutes(FRIDAY) == SLATE_WINDOW_CADENCE_MINUTES
    assert expected_cadence_minutes(MONDAY) == OFF_PEAK_CADENCE_MINUTES


def test_an_old_committed_timestamp_is_fresh_when_a_recent_run_reobserved_it():
    """THE freshness trap. The catalog does not commit when nothing
    changed, so old committed content that was just re-observed is FRESH."""
    verdict = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(minutes=12),
        published_status={
            "captured_at": (FRIDAY - timedelta(hours=6)).isoformat(),
            "content_fingerprint": "abc123",
        },
        last_run_fingerprint="abc123",
        now=FRIDAY,
    )
    assert verdict.freshness is CatalogFreshness.FRESH
    assert verdict.fingerprint_matched is True
    assert "re-observed" in verdict.reason
    assert "identical to what is published" in verdict.reason
    assert "confirmed current" in verdict.reason


def test_a_recent_timestamp_with_no_successful_run_is_stale():
    """The inverse trap: a dead collector's last gasp looks recent."""
    verdict = assess_freshness(
        last_successful_run_at=None,
        published_status={"captured_at": (FRIDAY - timedelta(minutes=5)).isoformat()},
        now=FRIDAY,
    )
    assert verdict.freshness is CatalogFreshness.STALE
    assert "no successful production run" in verdict.reason
    assert "does NOT substitute" in verdict.reason


def test_a_run_beyond_tolerance_is_stale_in_the_slate_window():
    verdict = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(minutes=95),
        published_status={"captured_at": FRIDAY.isoformat()},
        now=FRIDAY,
    )
    assert verdict.freshness is CatalogFreshness.STALE
    assert verdict.expected_cadence_minutes == 30


def test_the_same_gap_is_fresh_off_peak():
    """A 95-minute-old run is stale against a 30-minute cadence and
    perfectly healthy against a 6-hour one."""
    verdict = assess_freshness(
        last_successful_run_at=MONDAY - timedelta(minutes=95),
        published_status={"captured_at": MONDAY.isoformat()},
        now=MONDAY,
    )
    assert verdict.freshness is CatalogFreshness.FRESH
    assert verdict.expected_cadence_minutes == OFF_PEAK_CADENCE_MINUTES


def test_scheduler_lateness_is_tolerated_but_a_dead_collector_is_not():
    """GitHub's scheduler is not punctual; a 30-minute schedule firing at
    47 minutes is normal, not an outage."""
    late = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(minutes=47),
        published_status={"captured_at": FRIDAY.isoformat()},
        now=FRIDAY,
    )
    assert late.freshness is CatalogFreshness.FRESH
    dead = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(hours=9),
        published_status={"captured_at": FRIDAY.isoformat()},
        now=FRIDAY,
    )
    assert dead.freshness is CatalogFreshness.STALE


def test_freshness_explanation_states_the_timestamp_is_not_an_observation():
    verdict = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(minutes=10),
        published_status={
            "captured_at": (FRIDAY - timedelta(hours=6)).isoformat(),
            "content_fingerprint": "f",
        },
        last_run_fingerprint="f",
        now=FRIDAY,
    )
    assert "not an observation timestamp" in verdict.explain()


# =========================================================================
# THE PREFLIGHT CLI
# =========================================================================


def _preflight():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "run_cfb_preflight.py"
    spec = importlib.util.spec_from_file_location("run_cfb_preflight", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_live(tmp_path: Path, games, capture_complete=True, captured_at="2026-09-18T00:21:10Z"):
    live = tmp_path / "live"
    (live / "games").mkdir(parents=True)
    index = {
        "capture": {"captured_at": captured_at, "content_fingerprint": "fp1"},
        "completeness": {"capture_complete": capture_complete},
        "games": games,
    }
    (live / "cfb_market_catalog.json").write_text(json.dumps(index))
    (live / "cfb_catalog_status.json").write_text(
        json.dumps({"captured_at": captured_at, "content_fingerprint": "fp1"})
    )
    return live


def test_preflight_exits_nonzero_without_a_successful_run(tmp_path, capsys):
    live = _write_live(tmp_path, [_entry(complete=True)])
    assert _preflight().main(["--live-dir", str(live)]) == 2
    assert "CATALOG STALE" in capsys.readouterr().out


def test_preflight_refuses_an_incomplete_requested_game(tmp_path, capsys):
    live = _write_live(tmp_path, [_entry(complete=False, game_key="26SEP19BROKEN")], capture_complete=False)
    code = _preflight().main(
        [
            "--live-dir", str(live),
            "--last-successful-run-at", datetime.now(UTC).isoformat(),
            "--game", "26SEP19BROKEN",
        ]
    )
    assert code == 3, "a requested-but-unusable game must not exit 0"
    assert "UNAVAILABLE" in capsys.readouterr().out


def test_preflight_passes_a_fresh_catalog_and_a_complete_game(tmp_path, capsys):
    live = _write_live(tmp_path, [_entry(complete=True)])
    code = _preflight().main(
        [
            "--live-dir", str(live),
            "--last-successful-run-at", datetime.now(UTC).isoformat(),
            "--last-run-fingerprint", "fp1",
            "--game", "26SEP19UGAARK",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "CATALOG FRESH" in out
    assert "games/26SEP19UGAARK.json" in out


def test_preflight_json_mode_is_machine_readable(tmp_path, capsys):
    live = _write_live(tmp_path, [_entry(complete=True), _entry(complete=False, game_key="26SEP19BAD")],
                       capture_complete=False)
    _preflight().main(
        ["--live-dir", str(live), "--last-successful-run-at", datetime.now(UTC).isoformat(), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["fresh"] is True
    assert payload["capture_complete"] is False
    assert payload["usable_games"] == 1
    assert payload["unavailable_game_keys"] == ["26SEP19BAD"]
