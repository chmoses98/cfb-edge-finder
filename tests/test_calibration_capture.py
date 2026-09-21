"""What a future calibration study will need, captured at recommendation time.

Whether a fair probability was well calibrated can only be answered later,
against outcomes, and only if the inputs it came from can be reconstructed
exactly. A recommendation that cannot be tied back to the packet, the quote
universe and the opinion it was formed from is an anecdote.

None of this validates anything. It is the record that makes validation
possible at all, later, if a real sample accumulates.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))

import test_execution_candidates as fixtures  # noqa: E402

from cfb_edge_finder.execution.report import (  # noqa: E402
    handicap_fingerprint,
    recommendation_id,
)


def _artifact(min_net_edge: float = 0.0, batch: str | None = "early_b1"):
    tmp = pathlib.Path(tempfile.mkdtemp())
    game = fixtures.packet(tmp)
    evaluation = fixtures.evaluate_game(game, fixtures.handicap(), min_net_edge=min_net_edge)
    return fixtures.build_candidate_artifact(
        "early", [evaluation], {fixtures.GAME_KEY: game},
        {fixtures.GAME_KEY: fixtures.handicap()},
        min_net_edge=min_net_edge, batch=batch,
    )


# --------------------------------------------------- every field Phase 10 needs


def test_a_candidate_carries_everything_a_calibration_study_groups_on():
    artifact = _artifact()
    assert artifact["candidates"]
    for candidate in artifact["candidates"]:
        for field in (
            "recommendation_id",
            "game_key",
            "market",
            "side",
            "period",
            "market_family",
            "kalshi_executable_price",
            "fee",
            "fee_adjusted_breakeven",
            "fair_probability",
            "fair_probability_range",
            "fee_adjusted_edge",
            "fee_adjusted_edge_range",
            "robustness",
            "sensitivity_bound",
            "bet_up_to_price",
        ):
            assert field in candidate, field


def test_the_game_block_carries_the_provenance_a_rerun_would_need():
    artifact = _artifact()
    provenance = artifact["games"][fixtures.GAME_KEY]["provenance"]
    for field in (
        "packet_hash",
        "market_universe_hash",
        "context_hash",
        "material_context_hash",
        "handicap_hash",
        "context_collected_at",
    ):
        assert provenance.get(field), field


def test_confidence_and_data_quality_are_recorded_per_game():
    block = _artifact()["games"][fixtures.GAME_KEY]
    assert "handicap_confidence" in block
    assert "handicap_confidence_effective" in block
    assert "handicap_confidence_capped_by_data" in block
    assert "confidence_ceiling" in block["factual_data_quality"]


def test_the_artifact_is_timestamped():
    assert _artifact()["generated_at"]


# ------------------------------------------------------ the id behaves as a key


def test_the_same_recommendation_regenerates_the_same_id():
    """A re-run of an unchanged batch must be recognisable as the same
    recommendation, not counted twice."""
    first = {c["market"]: c["recommendation_id"] for c in _artifact()["candidates"]}
    second = {c["market"]: c["recommendation_id"] for c in _artifact()["candidates"]}
    assert first == second and first


def test_a_different_opinion_is_a_different_recommendation():
    base = dict(batch="b", shard="s", game_key="G", ticker="T", side="YES", packet_hash="p")
    assert recommendation_id(**base, handicap_hash="h1") != recommendation_id(
        **base, handicap_hash="h2"
    )


def test_a_different_quote_universe_is_a_different_recommendation():
    base = dict(batch="b", shard="s", game_key="G", ticker="T", side="YES", handicap_hash="h")
    assert recommendation_id(**base, packet_hash="p1") != recommendation_id(
        **base, packet_hash="p2"
    )


def test_the_two_sides_of_one_contract_are_different_recommendations():
    base = dict(batch="b", shard="s", game_key="G", ticker="T", packet_hash="p", handicap_hash="h")
    assert recommendation_id(**base, side="YES") != recommendation_id(**base, side="NO")


# ------------------------------------------- the fingerprint digests the OPINION


def test_the_fingerprint_ignores_prose():
    """Rewording a thesis changes no price and must not look like a new view."""
    a = fixtures.handicap()
    b = fixtures.handicap()
    object.__setattr__(b, "thesis", "completely different words, identical numbers")
    assert handicap_fingerprint(a) == handicap_fingerprint(b)


def test_the_fingerprint_changes_when_the_numbers_change():
    a = fixtures.handicap()
    b = fixtures.handicap()
    dist = b.period_distributions[next(iter(b.period_distributions))]
    object.__setattr__(dist, "home_mean", dist.home_mean + 3.0)
    assert handicap_fingerprint(a) != handicap_fingerprint(b)


def test_no_handicap_has_no_fingerprint():
    assert handicap_fingerprint(None) is None


# ------------------------------------------------------- separation of concerns


def test_the_recommendation_record_says_nothing_about_execution():
    """Whether a bet was TAKEN lives in the accounting ledger and is linked
    afterwards. Merging it in would make the recommendation unfalsifiable.

    Asserted over the candidate RECORDS, not the artifact's prose: the
    conventions block says the words "stake" and "sizes" precisely in order to
    state that this repository does neither, and a scan that tripped on the
    disclaimer would be testing the explanation rather than the data."""
    for candidate in _artifact()["candidates"]:
        assert candidate.get("stake_placeholder") is None, "nothing here sizes a bet"
        for field in candidate:
            assert field not in (
                "stake",
                "contracts",
                "filled_at",
                "fill_price",
                "net_profit_loss",
                "gross_settlement_payout",
                "settled",
                "outcome",
            ), field
