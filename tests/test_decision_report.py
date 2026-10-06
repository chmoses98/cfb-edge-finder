"""The Research Decision Report: framing, determinism, and the counted
zero.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cfb_edge_finder.decision.artifact import load_artifact
from cfb_edge_finder.decision.portfolio import build_portfolio_view
from cfb_edge_finder.decision.report import (
    BANNED_OUTPUT_VOCABULARY,
    REPORT_VERSION,
    STANDING_LOCKS,
    CorpusSummary,
    ReportVocabularyError,
    assert_vocabulary_clean,
    render_report,
    report_payload,
)
from cfb_edge_finder.decision.shadow import run_shadow_pipeline
from tests.test_decision_shadow import approved_resolution, snapshot

NOW = datetime(2026, 9, 6, 9, 0, tzinfo=UTC)

CORPUS = CorpusSummary(
    total_rows=1909,
    prospective_rows=1909,
    non_prospective_rows=0,
    settled_games=0,
    schema_versions={"research_corpus_v1": 1724, "research_corpus_v2": 185},
    corpus_identifier="research-data@abc123",
)


def build(snapshots=None, resolution=None):
    snaps = snapshots if snapshots is not None else [snapshot()]
    run = run_shadow_pipeline(snaps, resolution=resolution or load_artifact(None), now=NOW)
    return run, build_portfolio_view([s.semantics for s in snaps])


def text_of(**overrides) -> str:
    run, portfolio = build(**overrides)
    return render_report(
        run, portfolio=portfolio, evidence_state="NO_SETTLED_DATA", corpus=CORPUS, generated_at=NOW
    )


# ------------------------------------------------------ the framing


def test_the_report_contains_no_betting_card_vocabulary():
    lowered = text_of().lower()
    for phrase in BANNED_OUTPUT_VOCABULARY:
        assert phrase not in lowered


def test_the_vocabulary_guard_actually_catches_something():
    """A guard that cannot fail is not a guard."""
    with pytest.raises(ReportVocabularyError):
        assert_vocabulary_clean("Here are today's BEST BETS")
    with pytest.raises(ReportVocabularyError):
        assert_vocabulary_clean("recommended wager: 2 units")


def test_the_guard_is_case_insensitive():
    with pytest.raises(ReportVocabularyError):
        assert_vocabulary_clean("best bet")
    with pytest.raises(ReportVocabularyError):
        assert_vocabulary_clean("BeSt BeT")


def test_the_candidate_table_has_no_stake_or_size_column():
    """`BANKROLL_ACCESS: NONE` legitimately names bankroll in the locks
    block, so the check is scoped to the per-candidate table -- the only
    place a size column could actually appear."""
    trail = text_of().split("PER-CANDIDATE TRAIL")[1].lower()
    for header in ("stake", "units", "wager", "bankroll", "kelly", "size", "$"):
        assert header not in trail


def test_size_vocabulary_appears_only_inside_the_standing_locks():
    remainder = text_of()
    for lock in STANDING_LOCKS:
        remainder = remainder.replace(lock, "")
    remainder = remainder.lower()
    for word in ("bankroll", "staking", "stake", "kelly", "units"):
        assert word not in remainder


def test_rows_are_sorted_by_identifier_not_by_attractiveness():
    """An ordered list of opportunities is a recommendation whatever the
    header says. Sorting by ticker makes the order carry no signal."""
    snapshots = [
        snapshot(semantics=snapshot().semantics.__class__(
            market_ticker=t, game_id="g1", family=snapshot().semantics.family,
            team=snapshot().semantics.team, side=None, threshold=None,
            semantic_operator=">", parse_status="confirmed_live",
        ))
        for t in ("ZZZ", "AAA", "MMM")
    ]
    text = text_of(snapshots=snapshots)
    trail = text.split("PER-CANDIDATE TRAIL")[1]
    positions = [trail.index(t) for t in ("AAA", "MMM", "ZZZ")]
    assert positions == sorted(positions)


def test_the_standing_locks_are_all_declared():
    text = text_of()
    for lock in STANDING_LOCKS:
        assert lock in text


# ------------------------------------------------- the counted zero


def test_a_locked_repository_reports_zero_qualified():
    text = text_of()
    assert "SHADOW_QUALIFIED       : 0" in text
    assert "counted, not asserted" in text


def test_the_report_would_show_a_non_zero_count_if_a_lock_opened(tmp_path):
    """The zero must be a measurement. With an approved artifact in
    place the same renderer prints a non-zero number, which is what makes
    the zero meaningful everywhere else."""
    run, portfolio = build(resolution=approved_resolution(tmp_path))
    payload = report_payload(
        run, portfolio=portfolio, evidence_state="NO_SETTLED_DATA", corpus=CORPUS, generated_at=NOW
    )
    assert payload["shadow_qualified_count"] == run.shadow_qualified_count


# ------------------------------------------------------- provenance


def test_corpus_provenance_is_reported_verbatim():
    text = text_of()
    assert "research-data@abc123" in text
    assert "total_rows             : 1909" in text
    assert "non_prospective_rows   : 0" in text
    assert "research_corpus_v1" in text and "research_corpus_v2" in text


def test_the_absent_artifact_is_explained_not_merely_stated():
    text = text_of()
    assert "NO_VALIDATED_THRESHOLD_SET" in text
    assert "This is the designed state." in text


def test_exposure_grouping_reports_theses_not_contracts():
    text = text_of()
    assert "distinct_theses" in text
    assert "EXPOSURE_LIMITS_ABSENT_PENDING_EMPIRICAL_POLICY" in text
    assert "Many contracts on one game are one thesis." in text


# ------------------------------------------------------ mechanics


def test_the_report_is_deterministic():
    assert text_of() == text_of()


def test_the_payload_and_the_text_agree_on_every_count():
    run, portfolio = build()
    payload = report_payload(
        run, portfolio=portfolio, evidence_state="NO_SETTLED_DATA", corpus=CORPUS, generated_at=NOW
    )
    text = render_report(
        run, portfolio=portfolio, evidence_state="NO_SETTLED_DATA", corpus=CORPUS, generated_at=NOW
    )
    assert f"candidates_considered  : {payload['candidates_considered']}" in text
    assert f"SHADOW_QUALIFIED       : {payload['shadow_qualified_count']}" in text


def test_the_payload_is_json_serialisable_and_sorted():
    run, portfolio = build()
    payload = report_payload(
        run, portfolio=portfolio, evidence_state="NO_SETTLED_DATA", corpus=CORPUS, generated_at=NOW
    )
    encoded = json.dumps(payload, sort_keys=True)
    assert json.loads(encoded)["report_version"] == REPORT_VERSION
    assert payload["standing_locks"] == list(STANDING_LOCKS)


def test_an_empty_run_renders_without_pretending_to_have_data():
    run, _ = build(snapshots=[])
    text = render_report(
        run, portfolio=None, evidence_state="NO_CANDIDATES", corpus=CorpusSummary(), generated_at=NOW
    )
    assert "(no candidates evaluated in this run)" in text
    assert "(none)" in text
    assert "no portfolio view supplied" in text


def test_generated_at_is_reported_from_the_caller_not_the_wall_clock():
    assert NOW.isoformat() in text_of()
