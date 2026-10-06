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
    STALENESS_TOLERANCE_MULTIPLE,
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
# THE FINGERPRINT FAILS CLOSED
#
# A supplied fingerprint is CORROBORATION. Three outcomes, three
# meanings, and the middle one is the whole point:
#
#   matches      the run re-observed the published content  -> FRESH
#   DISAGREES    the run and the artifact describe different
#                content                                   -> INCONSISTENT
#   not supplied no corroboration either way; freshness rests
#                on the run history alone, and says so      -> FRESH
#
# A mismatch is strictly WORSE evidence than no fingerprint: it is
# positive evidence that what we are about to read is not what the live
# run observed -- a partial commit, a run against another branch, a
# hand-edited artifact. Returning FRESH for it (which this module
# originally did) converts a detected fault into a green light.
# =========================================================================


def test_a_fingerprint_mismatch_is_never_fresh_however_recent_the_run():
    verdict = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(minutes=2),
        published_status={"captured_at": (FRIDAY - timedelta(minutes=2)).isoformat(),
                          "content_fingerprint": "published_abc"},
        last_run_fingerprint="run_reported_xyz",
        now=FRIDAY,
    )
    assert verdict.freshness is CatalogFreshness.INCONSISTENT
    assert verdict.is_fresh is False
    assert verdict.is_inconsistent is True
    assert verdict.fingerprint_matched is False
    assert verdict.fingerprint_corroboration == "mismatch"
    # The reason must name both sides -- a bare "inconsistent" is not
    # actionable, and the first question is always "which is which".
    assert "published_abc" in verdict.reason and "run_reported_xyz" in verdict.reason
    assert "MISMATCH" in verdict.explain()


def test_a_mismatch_outranks_a_perfectly_timely_run():
    """The run arrived 1 minute into a 30-minute cadence -- as punctual as
    it gets. Timeliness is not the question a mismatch raises."""
    verdict = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(minutes=1),
        published_status={"content_fingerprint": "A"},
        last_run_fingerprint="B",
        now=FRIDAY,
    )
    assert verdict.is_fresh is False
    assert verdict.minutes_since_last_success == pytest.approx(1.0, abs=0.01)


def test_a_mismatch_is_still_not_fresh_when_the_run_is_also_stale():
    """Neither fault masks the other, and neither can produce FRESH."""
    verdict = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(hours=9),
        published_status={"content_fingerprint": "A"},
        last_run_fingerprint="B",
        now=FRIDAY,
    )
    assert verdict.is_fresh is False
    assert verdict.freshness is CatalogFreshness.INCONSISTENT


def test_no_fingerprint_supplied_may_be_fresh_but_says_so_out_loud():
    """Absence of corroboration is not a fault -- but the verdict must not
    imply two signals agreed when only one was consulted."""
    verdict = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(minutes=10),
        published_status={"content_fingerprint": "published_abc"},
        last_run_fingerprint=None,
        now=FRIDAY,
    )
    assert verdict.freshness is CatalogFreshness.FRESH
    assert verdict.fingerprint_matched is None
    assert verdict.fingerprint_corroboration == "not_supplied"
    assert "NOT supplied" in verdict.reason
    assert "NOT corroborated" in verdict.explain()


def test_a_supplied_fingerprint_with_nothing_published_to_compare_is_not_a_match():
    """Distinct from both: we tried to corroborate and could not. That is
    reported as its own state rather than quietly counted as agreement."""
    verdict = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(minutes=10),
        published_status={"captured_at": FRIDAY.isoformat()},
        last_run_fingerprint="run_abc",
        now=FRIDAY,
    )
    assert verdict.freshness is CatalogFreshness.FRESH
    assert verdict.fingerprint_matched is None
    assert verdict.fingerprint_corroboration == "published_fingerprint_absent"
    assert "could not be corroborated" in verdict.reason


def test_the_stale_tolerance_is_unchanged_by_the_mismatch_check():
    """Guard against the fail-closed branch being placed where it swallows
    the ordinary run-age path."""
    assert STALENESS_TOLERANCE_MULTIPLE == 3.0
    inside = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(minutes=80),
        published_status={"content_fingerprint": "A"},
        last_run_fingerprint="A",
        now=FRIDAY,
    )
    outside = assess_freshness(
        last_successful_run_at=FRIDAY - timedelta(minutes=100),
        published_status={"content_fingerprint": "A"},
        last_run_fingerprint="A",
        now=FRIDAY,
    )
    assert inside.freshness is CatalogFreshness.FRESH
    assert outside.freshness is CatalogFreshness.STALE


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


def test_preflight_can_never_exit_zero_on_a_fingerprint_mismatch(tmp_path, capsys):
    """*** THE REGRESSION TEST FOR THE FAIL-CLOSED RULE ***
    A consuming session reads only the exit code before it decides
    whether to handicap. Exit 0 on a mismatch is the failure this test
    exists to make impossible: the run was recent, the game is complete,
    every other signal is green, and the ONE thing that disagrees is the
    corroboration. That must not be a green light.

    Exit 4 is its own code so a caller can tell "the collector may have
    died" (2) from "the run and the artifact disagree" (4). Both nonzero;
    neither ever 0."""
    live = _write_live(tmp_path, [_entry(complete=True)])  # publishes fingerprint "fp1"
    code = _preflight().main(
        [
            "--live-dir", str(live),
            "--last-successful-run-at", datetime.now(UTC).isoformat(),
            "--last-run-fingerprint", "fp_from_a_different_run",
            "--game", "26SEP19UGAARK",
        ]
    )
    out = capsys.readouterr().out
    assert code != 0, "a fingerprint mismatch exited 0 -- the consumer would handicap on it"
    assert code == 4
    assert "CATALOG INCONSISTENT" in out
    assert "MISMATCH" in out


def test_preflight_mismatch_beats_every_other_green_signal(tmp_path, capsys):
    """Same as above with NO game requested, so nothing but freshness can
    fail: the mismatch alone has to carry the refusal."""
    live = _write_live(tmp_path, [_entry(complete=True)])
    code = _preflight().main(
        [
            "--live-dir", str(live),
            "--last-successful-run-at", datetime.now(UTC).isoformat(),
            "--last-run-fingerprint", "definitely_not_fp1",
        ]
    )
    assert code == 4
    assert "CATALOG INCONSISTENT" in capsys.readouterr().out


def test_preflight_json_reports_the_corroboration_state_explicitly(tmp_path, capsys):
    """A machine consumer must be able to tell agreement from absence of
    evidence without string-matching a prose reason."""
    live = _write_live(tmp_path, [_entry(complete=True)])
    now = datetime.now(UTC).isoformat()

    _preflight().main(["--live-dir", str(live), "--last-successful-run-at", now,
                       "--last-run-fingerprint", "fp1", "--json"])
    matched = json.loads(capsys.readouterr().out)
    assert matched["fresh"] is True
    assert matched["fingerprint_corroboration"] == "matched"

    _preflight().main(["--live-dir", str(live), "--last-successful-run-at", now,
                       "--last-run-fingerprint", "nope", "--json"])
    mismatch = json.loads(capsys.readouterr().out)
    assert mismatch["fresh"] is False
    assert mismatch["fingerprint_corroboration"] == "mismatch"
    assert mismatch["freshness"] == "CATALOG INCONSISTENT"

    _preflight().main(["--live-dir", str(live), "--last-successful-run-at", now, "--json"])
    absent = json.loads(capsys.readouterr().out)
    assert absent["fresh"] is True
    assert absent["fingerprint_corroboration"] == "not_supplied"


# =========================================================================
# THE DOCUMENTED PROCEDURE MUST MATCH THE ENFORCED ONE
#
# The contract doc is what a ChatGPT session actually follows, so an error
# in it is a live defect, not a typo. Its GitHub API example is the single
# place a consumer learns HOW to obtain the liveness signal.
# =========================================================================

CONTRACT_DOC = Path(__file__).resolve().parents[1] / "docs" / "RUN_CFB_CONTRACT.md"


def test_the_documented_run_lookup_filters_to_main():
    """A feature-branch run must never certify the main catalog. Without
    `branch=main` the API returns the latest successful run on ANY branch
    -- including a feature branch of your own -- which says nothing about
    whether the production schedule on main is alive."""
    doc = CONTRACT_DOC.read_text(encoding="utf-8")
    example = doc[doc.index("actions/workflows/kalshi-market-catalog.yml/runs"):][:400]
    assert "branch=main" in example, "the run-lookup example does not filter to main"
    assert "status=success" in example
    assert "branch=main" in doc and "must never certify" in doc


def test_the_doc_states_every_preflight_exit_code_the_script_can_return():
    """A consumer that gates on the exit code needs all of them named,
    including the one added for a fingerprint mismatch."""
    doc = CONTRACT_DOC.read_text(encoding="utf-8")
    assert "**4**" in doc and "CATALOG INCONSISTENT" in doc
    assert "**2**" in doc and "**3**" in doc


def test_the_doc_does_not_describe_fee_keys_that_no_longer_exist():
    """`mechanics` stopped computing fees; a doc naming its old fee keys
    would send a consumer looking for a cost estimate that is not there."""
    doc = CONTRACT_DOC.read_text(encoding="utf-8")
    assert "estimated_fee_per_contract_at_mid" not in doc
    assert "model_trade_fee_at_yes_ask" in doc


def test_the_doc_tells_a_consumer_how_to_price_the_NO_side():
    """RUN CFB may conclude the correct wager is NO. The procedure must
    name the executable NO price, and must forbid the complement."""
    doc = CONTRACT_DOC.read_text(encoding="utf-8")
    assert "model_trade_fee_at_no_ask" in doc
    assert "basis_no_ask" in doc
    assert "1 − yes_ask" in doc or "1 - yes_ask" in doc, "the doc does not warn off the complement"
    assert "buying NO" in doc and "buying YES" in doc


def test_the_doc_forbids_reading_an_empty_book_as_a_50_percent_market():
    doc = CONTRACT_DOC.read_text(encoding="utf-8")
    assert "book_state" in doc
    assert "empty_book" in doc
    assert "100-cent spread is not evidence of a 50/50 market" in doc
    # And it must say the contract stays in the menu -- quote quality is
    # not a discovery-completeness failure.
    assert "does not remove a contract from the menu" in doc


def test_the_doc_states_that_only_named_fee_models_are_priced():
    doc = CONTRACT_DOC.read_text(encoding="utf-8")
    assert "quadratic_v2" in doc
    assert "prefix is not a shared formula" in doc
