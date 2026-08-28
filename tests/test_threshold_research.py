"""Discovery and holdout validation.

The load-bearing tests here are the ones that plant a deliberately
absurd, guaranteed-profitable synthetic sample and prove it still cannot
approve anything. A safety property that has only been tested against
unprofitable data has not been tested.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cfb_edge_finder.research.holdout import (
    FrozenCandidateRule,
    ValidationVerdict,
    validate_candidate,
)
from cfb_edge_finder.research.protocol import (
    CLUSTER_UNIT,
    CONFIRMATORY_QUESTIONS,
    PROTOCOL_VERSION,
    document_hash,
    manifest,
)
from cfb_edge_finder.research.threshold_discovery import (
    BLOCKED_ON_SAMPLE,
    DRAFT_RESEARCH_FINDING,
    DiscoveryRefusal,
    SettledResearchObservation,
    discover_threshold_candidates,
)


def obs(
    *,
    game: str,
    ticker: str = "T",
    family: str = "moneyline",
    timing: str = "T_24H",
    model_version: str = "m1",
    price: float = 0.50,
    fee: float = 0.02,
    probability: float = 0.60,
    settled_yes: bool = True,
    capture_mode: str = "PROSPECTIVE",
    closing_price: float | None = None,
) -> SettledResearchObservation:
    return SettledResearchObservation(
        game_id=game,
        market_ticker=f"{game}-{ticker}",
        family=family,
        timing_label=timing,
        model_version=model_version,
        side="yes",
        executable_price=price,
        fee_adjusted_break_even=price + fee,
        model_probability=probability,
        settled_yes=settled_yes,
        capture_mode=capture_mode,
        closing_price=closing_price,
    )


def absurdly_profitable(n_games: int = 60) -> list[SettledResearchObservation]:
    """Every contract bought at 2c and settling YES: a ~50x return that
    could never occur. If anything can self-approve, this is what would
    do it."""
    return [
        obs(game=f"g{i}", price=0.01, fee=0.01, probability=0.99, settled_yes=True)
        for i in range(n_games)
    ]


# ------------------------------------------------- the protocol


def test_the_protocol_was_preregistered_with_zero_settled_games():
    m = manifest()
    assert m.settled_games_at_preregistration == 0
    assert m.version == PROTOCOL_VERSION


def test_the_protocol_document_hash_is_stable_and_real():
    first = document_hash()
    assert first != "DOCUMENT_MISSING"
    assert first == document_hash()
    assert len(first) == 64


def test_the_hash_changes_if_the_document_changes(tmp_path):
    """A protocol edited after results exist must produce a different
    hash, or the manifest proves nothing."""
    doc = tmp_path / "p.md"
    doc.write_text("original", encoding="utf-8")
    before = document_hash(doc)
    doc.write_text("original, with a threshold quietly relaxed", encoding="utf-8")
    assert document_hash(doc) != before


def test_a_missing_document_is_reported_not_raised():
    assert document_hash(__import__("pathlib").Path("nope.md")) == "DOCUMENT_MISSING"


def test_only_three_questions_are_confirmatory():
    """Everything else is descriptive. A protocol where everything is
    confirmatory is a protocol with no multiple-comparisons discipline."""
    assert len(CONFIRMATORY_QUESTIONS) == 3


def test_the_cluster_unit_is_the_game():
    assert CLUSTER_UNIT == "game_id"


# --------------------------------------------- discovery refusals


def test_no_settled_observations_is_blocked_on_sample():
    report = discover_threshold_candidates([], minimum_settled_games=10)
    assert report.status == BLOCKED_ON_SAMPLE
    assert DiscoveryRefusal.NO_SETTLED_OBSERVATIONS.value in report.refusals


def test_an_undeclared_minimum_refuses_every_slice():
    """A human must state the sample they will accept. Inferring it from
    the data it will then be applied to is circular."""
    report = discover_threshold_candidates(absurdly_profitable(), minimum_settled_games=None)
    assert report.findings == []
    assert report.status == BLOCKED_ON_SAMPLE
    assert all(r == DiscoveryRefusal.NO_DECLARED_MINIMUM.value for r in report.refusals.values())


def test_below_the_declared_minimum_refuses():
    report = discover_threshold_candidates(absurdly_profitable(5), minimum_settled_games=50)
    assert report.findings == []
    assert any("BELOW_DECLARED_MINIMUM_GAMES" in r for r in report.refusals.values())


def test_a_single_game_cluster_yields_no_interval_and_no_finding():
    rows = [obs(game="g1", ticker=str(i)) for i in range(40)]
    report = discover_threshold_candidates(rows, minimum_settled_games=1)
    assert report.findings == []
    assert DiscoveryRefusal.SINGLE_GAME_CLUSTER.value in report.refusals.values()


def test_retrospective_observations_are_excluded():
    rows = absurdly_profitable(60) + [
        obs(game="backfilled", capture_mode="RETROSPECTIVE_BACKFILL")
    ]
    report = discover_threshold_candidates(rows, minimum_settled_games=10)
    assert DiscoveryRefusal.NOT_PROSPECTIVE.value in report.refusals
    assert "backfilled" not in report.discovery_game_ids


# ------------------------------------ approval is not a capability


def test_an_absurdly_profitable_sample_produces_only_a_draft_finding():
    """THE LOAD-BEARING TEST. A ~50x return over 60 independent games
    still yields DRAFT_RESEARCH_FINDING and nothing more."""
    report = discover_threshold_candidates(absurdly_profitable(60), minimum_settled_games=10)
    assert report.findings
    assert report.status == DRAFT_RESEARCH_FINDING
    for finding in report.findings:
        assert finding.status == DRAFT_RESEARCH_FINDING
        assert finding.mean_research_unit_pl > 0.9  # genuinely spectacular
        assert finding.interval_excludes_zero      # and statistically clean
    # ...and yet:
    payload = json.dumps(report.to_payload())
    for forbidden in ("APPROVED", "VALIDATED", "SHADOW_QUALIFIED", "approval_state"):
        assert forbidden not in payload


def test_the_discovery_module_cannot_construct_a_threshold_artifact():
    """Not a policy -- an absent capability. The module does not import
    the artifact machinery at all."""
    import ast
    import pathlib

    tree = ast.parse(
        pathlib.Path("src/cfb_edge_finder/research/threshold_discovery.py").read_text()
    )
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not any("decision.artifact" in m for m in imported)
    assert not any("thresholds" in m for m in imported)


def test_no_approved_state_string_exists_in_the_discovery_module():
    import pathlib

    src = pathlib.Path("src/cfb_edge_finder/research/threshold_discovery.py").read_text()
    assert "APPROVED_FOR_LIVE" not in src
    assert "APPROVED_FOR_SHADOW" not in src


def test_findings_are_ordered_by_identifier_never_by_result():
    """An order that encodes profitability is a ranking whatever it is
    called."""
    rows = []
    for i in range(30):
        rows.append(obs(game=f"g{i}", family="total", timing="T_30", settled_yes=True))
        rows.append(obs(game=f"g{i}", family="moneyline", timing="T_24H", settled_yes=False))
    report = discover_threshold_candidates(rows, minimum_settled_games=10)
    keys = [(f.slice_key.family, f.slice_key.timing_label) for f in report.findings]
    assert keys == sorted(keys)


def test_every_finding_reports_how_many_slices_were_examined():
    rows = []
    for i in range(30):
        for family in ("moneyline", "spread", "total"):
            rows.append(obs(game=f"g{i}", family=family, ticker=family))
    report = discover_threshold_candidates(rows, minimum_settled_games=10)
    assert report.slices_examined == 3
    assert all(f.slices_examined == 3 for f in report.findings)


def test_sample_size_is_reported_three_ways():
    rows = [obs(game=f"g{i}", ticker=str(j)) for i in range(20) for j in range(3)]
    report = discover_threshold_candidates(rows, minimum_settled_games=10)
    f = report.findings[0]
    assert f.observations == 60
    assert f.distinct_contracts == 60
    assert f.distinct_games == 20


def test_clustering_uses_games_not_contracts():
    """Twenty contracts on one game are one football outcome. A
    contract-level interval would be far too narrow."""
    many_per_game = [obs(game=f"g{i}", ticker=str(j)) for i in range(12) for j in range(20)]
    one_per_game = [obs(game=f"g{i}", ticker="only") for i in range(12)]
    a = discover_threshold_candidates(many_per_game, minimum_settled_games=5).findings[0]
    b = discover_threshold_candidates(one_per_game, minimum_settled_games=5).findings[0]
    assert a.distinct_games == b.distinct_games == 12
    assert a.cluster_ci_low == pytest.approx(b.cluster_ci_low)


# ------------------------------------------- holdout validation


def rule(**overrides) -> FrozenCandidateRule:
    base = dict(
        rule_id="r1",
        families=("moneyline",),
        timing_labels=("T_24H",),
        model_versions=("m1",),
        minimum_signed_gap=0.05,
        price_min=0.05,
        price_max=0.95,
        discovery_corpus_identifier="research-data@abc",
        discovery_cutoff="2026-09-30",
        discovery_game_ids=tuple(f"d{i}" for i in range(20)),
    )
    base.update(overrides)
    return FrozenCandidateRule(**base)


def validation_rows(n: int = 30, prefix: str = "v") -> list[SettledResearchObservation]:
    return [
        obs(game=f"{prefix}{i}", price=0.50, fee=0.02, probability=0.60, settled_yes=True)
        for i in range(n)
    ]


def test_a_leaked_discovery_game_refuses_the_run():
    """THE ONE RULE. No override parameter exists."""
    r = rule()
    rows = validation_rows(20) + [obs(game="d3")]
    report = validate_candidate(
        r, frozen_hash=r.content_hash(), validation_observations=rows, minimum_validation_games=5
    )
    assert report.verdict is ValidationVerdict.REFUSED_DISCOVERY_LEAKAGE
    assert "d3" in report.leaked_game_ids


def test_a_mutated_rule_refuses_the_run():
    """A threshold nudged after seeing validation data changes the hash."""
    frozen = rule().content_hash()
    widened = rule(minimum_signed_gap=0.01)
    report = validate_candidate(
        widened,
        frozen_hash=frozen,
        validation_observations=validation_rows(),
        minimum_validation_games=5,
    )
    assert report.verdict is ValidationVerdict.REFUSED_RULE_MUTATED


def test_the_discovery_game_set_is_part_of_the_rule_identity():
    """The same numbers discovered on different games is a different
    claim."""
    assert rule().content_hash() != rule(discovery_game_ids=("x",)).content_hash()


def test_retrospective_validation_data_is_refused():
    r = rule()
    rows = validation_rows(20) + [obs(game="v99", capture_mode="RETROSPECTIVE_BACKFILL")]
    report = validate_candidate(
        r, frozen_hash=r.content_hash(), validation_observations=rows, minimum_validation_games=5
    )
    assert report.verdict is ValidationVerdict.REFUSED_NOT_PROSPECTIVE


def test_out_of_scope_validation_data_is_refused():
    r = rule(families=("spread",))
    report = validate_candidate(
        r,
        frozen_hash=r.content_hash(),
        validation_observations=validation_rows(),
        minimum_validation_games=5,
    )
    assert report.verdict is ValidationVerdict.REFUSED_INCOMPATIBLE_SCOPE


def test_insufficient_validation_sample_is_reported():
    r = rule()
    report = validate_candidate(
        r,
        frozen_hash=r.content_hash(),
        validation_observations=validation_rows(3),
        minimum_validation_games=25,
    )
    assert report.verdict is ValidationVerdict.INSUFFICIENT_VALIDATION_SAMPLE


def test_a_negative_result_is_reported_as_such():
    r = rule()
    losing = [
        obs(game=f"v{i}", price=0.50, fee=0.02, probability=0.60, settled_yes=False)
        for i in range(30)
    ]
    report = validate_candidate(
        r, frozen_hash=r.content_hash(), validation_observations=losing, minimum_validation_games=5
    )
    assert report.verdict is ValidationVerdict.NOT_CORROBORATED
    assert report.mean_research_unit_pl < 0


def test_the_strongest_verdict_still_defers_to_a_human():
    r = rule()
    report = validate_candidate(
        r,
        frozen_hash=r.content_hash(),
        validation_observations=validation_rows(30),
        minimum_validation_games=5,
    )
    assert report.verdict is ValidationVerdict.CORROBORATED_PENDING_HUMAN_REVIEW
    assert "NOT approved" in report.detail
    assert report.approves_anything is False


def test_even_a_perfect_validation_approves_nothing():
    """The companion to the discovery test: a flawless holdout result
    still produces no approval state anywhere in the payload."""
    r = rule()
    # Inside the rule's declared price domain (0.05-0.95) so the run
    # actually reaches a verdict; still a ~9x return over 80 games.
    perfect = [
        obs(game=f"v{i}", price=0.10, fee=0.01, probability=0.99, settled_yes=True)
        for i in range(80)
    ]
    report = validate_candidate(
        r, frozen_hash=r.content_hash(), validation_observations=perfect, minimum_validation_games=5
    )
    assert report.verdict is ValidationVerdict.CORROBORATED_PENDING_HUMAN_REVIEW
    payload = json.dumps(report.to_payload())
    for forbidden in ("APPROVED_FOR_LIVE", "APPROVED_FOR_SHADOW", "SHADOW_QUALIFIED"):
        assert forbidden not in payload
    assert report.to_payload()["approves_anything"] is False


def test_the_holdout_module_cannot_construct_an_artifact():
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/cfb_edge_finder/research/holdout.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any("decision.artifact" in m for m in imported)


def test_refusals_are_checked_most_fundamental_first():
    """A leaked game set must never be masked by a sample-size
    complaint."""
    r = rule()
    rows = [obs(game="d0")]  # leaked AND far below any minimum
    report = validate_candidate(
        r, frozen_hash=r.content_hash(), validation_observations=rows, minimum_validation_games=999
    )
    assert report.verdict is ValidationVerdict.REFUSED_DISCOVERY_LEAKAGE


def test_the_report_records_both_corpora_for_audit():
    r = rule()
    report = validate_candidate(
        r,
        frozen_hash=r.content_hash(),
        validation_observations=validation_rows(30),
        minimum_validation_games=5,
        validation_start=datetime(2026, 10, 1, tzinfo=UTC),
        validation_end=datetime(2026, 11, 1, tzinfo=UTC),
    )
    payload = report.to_payload()
    assert payload["discovery"]["corpus_identifier"] == "research-data@abc"
    assert payload["discovery"]["game_count"] == 20
    assert payload["validation"]["start"].startswith("2026-10-01")
    assert payload["protocol_version"] == PROTOCOL_VERSION
    assert len(payload["protocol_document_sha256"]) == 64


def test_hash_is_order_independent_over_declared_sets():
    """Reordering a tuple must not look like a different rule."""
    a = rule(families=("moneyline", "spread"))
    b = rule(families=("spread", "moneyline"))
    assert a.content_hash() == b.content_hash()
