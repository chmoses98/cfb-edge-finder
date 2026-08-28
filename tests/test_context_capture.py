"""Research-only context capture and checkpoint manifests.

The load-bearing tests are the structural ones: contextual research
fields must be incapable of reaching the probability model, proven by
parsing imports rather than by intending it.
"""

from __future__ import annotations

import ast
import json
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from cfb_edge_finder.research.checkpoint_manifest import (
    REQUIRED_FIELDS,
    REQUIRED_WHEN_PRICED,
    CheckpointManifest,
    ManifestCompletenessReport,
    manifest_from_corpus_row,
)
from cfb_edge_finder.research.context_capture import (
    CONTEXT_FIELD_PLAN,
    MODEL_IMPACT,
    ContextAvailability,
    ContextCoverageReport,
    ContextSource,
    build_context_record,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "cfb_edge_finder"
CONTEXT_MODULE = "cfb_edge_finder.research.context_capture"

KICKOFF = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)
CAPTURED = KICKOFF - timedelta(hours=24)


def imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ------------------------------------- the structural invariant


@pytest.mark.parametrize("package", ["modeling", "projections", "ratings"])
def test_no_model_package_imports_context_capture(package):
    """THE INVARIANT. A contextual research field must not be able to
    influence a probability, and the proof is that the probability code
    cannot even see it."""
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in sorted((SRC / package).rglob("*.py"))
        if CONTEXT_MODULE in imported_modules(p)
    ]
    assert offenders == [], f"context capture reachable from {package}: {offenders}"


def test_context_capture_does_not_import_the_model_either():
    """Both directions. If context capture imported the score model it
    could pass values in through a call rather than an import."""
    imported = imported_modules(SRC / "research" / "context_capture.py")
    for forbidden in ("score_model", "distribution", "ratings", "qb_continuity"):
        assert not any(forbidden in m for m in imported), forbidden


def test_the_four_protected_model_outputs_are_named_and_untouched():
    """The mission names four outputs that must not change. None of them
    appears as an assignable name anywhere in context capture."""
    src = (SRC / "research" / "context_capture.py").read_text()
    tree = ast.parse(src)
    assigned = set()
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else []
        for t in targets:
            if isinstance(t, ast.Name):
                assigned.add(t.id)
            elif isinstance(t, ast.Attribute):
                assigned.add(t.attr)
    for protected in ("model_probability", "projected_margin", "projected_total", "distribution"):
        assert protected not in assigned


def test_context_records_declare_their_model_impact():
    record = build_context_record(game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF)
    assert record.model_impact == MODEL_IMPACT == "NONE_RESEARCH_ONLY"
    assert record.to_payload()["model_impact"] == "NONE_RESEARCH_ONLY"


def test_building_a_context_record_does_not_change_a_projection():
    """Behavioural companion to the import checks: run the real score
    model, build context, run it again, and compare."""
    from cfb_edge_finder.modeling.qb_continuity import classify_continuity, uncertainty_multiplier

    before = uncertainty_multiplier(classify_continuity(0.8))
    build_context_record(
        game_id="g1",
        captured_at=CAPTURED,
        kickoff_utc=KICKOFF,
        observed={"qb_continuity_proxy": 0.1, "neutral_site_flag": True},
    )
    assert uncertainty_multiplier(classify_continuity(0.8)) == before


# ------------------------------------------- honest gap recording


def test_unavailable_sources_are_recorded_as_gaps_not_guessed():
    """CFB has no mandatory injury report. Recording that is honest;
    scraping a beat writer would not be."""
    record = build_context_record(game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF)
    injury = record.field_named("material_injury_status")
    assert injury.availability is ContextAvailability.SOURCE_UNAVAILABLE
    assert injury.source is ContextSource.NONE_AVAILABLE
    assert injury.value is None


def test_qb_identity_is_declared_unavailable_not_proxied():
    """The continuity proxy must never be relabelled as QB identity."""
    record = build_context_record(game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF)
    assert record.field_named("expected_starting_qb").availability is (
        ContextAvailability.SOURCE_UNAVAILABLE
    )
    assert record.field_named("qb_new_starter_flag").availability is (
        ContextAvailability.SOURCE_UNAVAILABLE
    )


def test_the_continuity_proxy_stays_a_proxy_even_when_observed():
    record = build_context_record(
        game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF,
        observed={"qb_continuity_proxy": 0.82},
    )
    field = record.field_named("qb_continuity_proxy")
    assert field.availability is ContextAvailability.DERIVED_PROXY
    assert field.value == 0.82
    assert not field.is_usable_evidence  # a proxy is analysed as a proxy


def test_a_null_value_is_not_an_observation():
    record = build_context_record(
        game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF, observed={"venue": None}
    )
    assert record.field_named("venue").availability is ContextAvailability.NOT_YET_CAPTURED


def test_an_observed_value_carries_full_provenance():
    record = build_context_record(
        game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF, observed={"venue": "Kyle Field"}
    )
    venue = record.field_named("venue")
    assert venue.is_usable_evidence
    assert venue.observed_at == CAPTURED
    assert venue.source is ContextSource.CFBD_GAMES


def test_wired_but_uncaptured_is_distinct_from_unavailable():
    """The same distinction market_status draws between a legacy row and
    a current defect."""
    record = build_context_record(game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF)
    assert record.field_named("weather_snapshot").availability is (
        ContextAvailability.NOT_YET_CAPTURED
    )
    assert record.field_named("material_injury_status").availability is (
        ContextAvailability.SOURCE_UNAVAILABLE
    )


def test_every_planned_field_appears_in_every_record():
    record = build_context_record(game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF)
    assert {f.name for f in record.fields} == set(CONTEXT_FIELD_PLAN)


def test_records_are_deterministic():
    a = build_context_record(game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF)
    b = build_context_record(game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF)
    assert a == b


# ------------------------------------------------- prospectivity


def test_a_record_captured_before_kickoff_is_prospective():
    assert build_context_record(
        game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF
    ).is_prospective


def test_a_record_captured_after_kickoff_is_not_prospective():
    late = build_context_record(
        game_id="g1", captured_at=KICKOFF + timedelta(minutes=1), kickoff_utc=KICKOFF
    )
    assert not late.is_prospective


def test_a_backfilled_record_is_never_prospective():
    backfilled = build_context_record(
        game_id="g1", captured_at=CAPTURED, kickoff_utc=KICKOFF,
        capture_mode="RETROSPECTIVE_BACKFILL",
    )
    assert not backfilled.is_prospective


def test_coverage_reports_what_an_ablation_could_actually_use():
    report = ContextCoverageReport(
        [
            build_context_record(
                game_id=f"g{i}", captured_at=CAPTURED, kickoff_utc=KICKOFF,
                observed={"venue": "V", "neutral_site_flag": False},
            )
            for i in range(3)
        ]
    )
    assert report.usable_field_names == ["neutral_site_flag", "venue"]
    assert report.coverage()["material_injury_status"]["SOURCE_UNAVAILABLE"] == 3


# -------------------------------------------- checkpoint manifest


def corpus_row(**overrides) -> dict:
    base = {
        "observation_key": "k",
        "schema_version": "research_corpus_v2",
        "capture_mode": "PROSPECTIVE",
        "run_id": "33157562772-1",
        "kickoff_utc_at_capture": "2026-08-29T16:00:00+00:00",
        "observation": {
            "game_id": "g1",
            "captured_at": "2026-08-28T13:00:00+00:00",
            "snapshot_timing": {"label": "T_24H"},
            "kalshi_market_ticker": "T1",
            "model_version": {"model_version": "m1"},
            "pricing_status": "model_priced",
            "snapshot_id": "scan-1",
            "fee_schedule_version": "kalshi_fee_schedule_2026_07_07_taker",
            "market_status": "active",
        },
    }
    base.update(overrides)
    return base


def test_a_priced_row_yields_a_complete_manifest():
    m = manifest_from_corpus_row(corpus_row())
    assert m.is_complete
    assert m.missing_fields == ()


def test_an_unpriced_row_is_complete_without_a_model_version():
    """883 real rows are unpriced and correctly carry no model version.
    Demanding one universally reported them all as defective."""
    row = corpus_row()
    row["observation"]["pricing_status"] = "not_priced"
    row["observation"]["model_version"] = {}
    m = manifest_from_corpus_row(row)
    assert not m.is_priced
    assert m.is_complete


def test_a_priced_row_missing_its_model_version_is_incomplete():
    row = corpus_row()
    row["observation"]["model_version"] = {}
    m = manifest_from_corpus_row(row)
    assert m.is_priced
    assert "model_version" in m.missing_fields


def test_pricing_status_decides_pricedness_not_the_scan_snapshot_id():
    """Every captured row carries a scan snapshot id whether or not the
    football model ran; inferring from it reported all 1,998 rows as
    priced."""
    row = corpus_row()
    row["observation"]["pricing_status"] = "not_priced"
    m = manifest_from_corpus_row(row)
    assert m.projection_snapshot_id == "scan-1"
    assert not m.is_priced


@pytest.mark.parametrize("field_name", REQUIRED_FIELDS)
def test_every_universally_required_field_is_detected_when_absent(field_name):
    m = CheckpointManifest(
        game_id="g", captured_at="t", timing_label="T_24H",
        observation_schema_version="v2", trigger_source="run",
    )
    assert m.is_complete
    stripped = CheckpointManifest(
        **{**{k: getattr(m, k) for k in ("game_id", "captured_at", "timing_label",
                                          "observation_schema_version", "trigger_source")},
           field_name: ""}
    )
    assert field_name in stripped.missing_fields


def test_model_version_is_required_only_when_priced():
    assert REQUIRED_WHEN_PRICED == ("model_version",)


def test_manifest_hashes_are_stable_and_distinguish_content():
    a = manifest_from_corpus_row(corpus_row())
    b = manifest_from_corpus_row(corpus_row())
    assert a.content_hash() == b.content_hash()
    changed = corpus_row()
    changed["observation"]["executable_yes_price"] = 0.77
    assert manifest_from_corpus_row(changed).content_hash() != a.content_hash()


def test_a_manifest_stores_identifiers_not_a_second_copy_of_the_data():
    """Duplicating the corpus would create a copy that can silently
    disagree with the original."""
    payload = manifest_from_corpus_row(corpus_row()).to_payload()
    assert "model_probability" not in payload
    assert "residual_pool" not in payload
    assert payload["market_tickers"] == ["T1"]


def test_completeness_report_aggregates_missing_fields():
    good = manifest_from_corpus_row(corpus_row())
    bad_row = corpus_row()
    bad_row["schema_version"] = ""
    report = ManifestCompletenessReport([good, manifest_from_corpus_row(bad_row)])
    assert report.complete_count == 1
    assert report.incomplete_count == 1
    assert report.missing_field_counts() == {"observation_schema_version": 1}


def test_manifest_payload_is_json_serialisable():
    json.dumps(manifest_from_corpus_row(corpus_row()).to_payload())
