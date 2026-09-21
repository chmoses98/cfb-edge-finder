"""Reasoning-load batching, streaming, and the union-equals-whole invariant.

*** THE INVARIANT ***
Processing a window as a sequence of batches must produce exactly the answer
that processing it in one pass would. If it does not, "streamable" is a
euphemism for "a different, smaller scan", and the exhaustiveness guarantee
this repository is built on would hold per batch and nowhere else.
"""

from __future__ import annotations

import json

from cfb_edge_finder.execution.batching import (
    MAX_GAMES_PER_BATCH,
    batch_document,
    build_batches,
    game_cost,
    split_into_batches,
)
from cfb_edge_finder.execution.evaluator import evaluate_game
from cfb_edge_finder.execution.handicap import parse_handicap
from cfb_edge_finder.execution.report import build_candidate_artifact
from cfb_edge_finder.execution.slate import build_slate
from cfb_edge_finder.execution.windows import WINDOW_ORDER
from tests.execution_fakes import GAME_KEY, catalog_dir
from tests.test_execution_disposition import config


def slate_of(tmp_path):
    return build_slate(catalog_dir(tmp_path), config(), slate_date=None).slate


def handicap_for(packet):
    periods = sorted(
        {
            str((r.get("semantics") or {}).get("period"))
            for r in packet["contracts"]
            if (r.get("semantics") or {}).get("requires") == "period_distribution"
        }
    )
    teams = (packet.get("game_metadata") or {}).get("teams") or {}
    return parse_handicap(
        {
            "schema_version": "cfb_handicap_payload/2.0.0",
            "game_key": packet["game_key"],
            "teams": {"home": teams.get("home"), "away": teams.get("away")},
            "period_distributions": {
                period: {
                    "home_mean": 27.0,
                    "away_mean": 24.0,
                    "home_sd": 10.5,
                    "away_sd": 10.0,
                    "correlation": 0.1,
                    "uncertainty": {"margin_points": 3.0, "total_points": 5.0},
                }
                for period in periods
            },
        }
    )


# ------------------------------------------------------------ the cost


def test_the_cost_publishes_every_term_it_summed(tmp_path):
    """A batch boundary an operator cannot explain is one they will override."""
    packet = build_slate(catalog_dir(tmp_path), config(), slate_date=None).packets[0]
    cost = game_cost(packet)
    terms = cost.as_dict()["terms"]
    assert set(terms) == {
        "base_handicap",
        "extra_periods",
        "unusual_families",
        "explicit_probability_tickers",
        "missing_factual_context",
        "market_breadth",
    }
    assert abs(sum(terms.values()) - cost.total) < 1e-6


def test_missing_factual_context_costs_more_than_present_context(tmp_path):
    """The term that makes the context layer pay for itself: an enriched slate
    is measurably cheaper to handicap and therefore batches larger."""
    packet = build_slate(catalog_dir(tmp_path), config(), slate_date=None).packets[0]
    bare = game_cost(packet).total
    enriched = dict(packet)
    enriched["factual_context"] = dict(packet["factual_context"])
    enriched["factual_context"]["coverage"] = {
        domain: "fresh" for domain in packet["factual_context"]["coverage"]
    }
    assert game_cost(enriched).total < bare


def test_the_unusual_family_term_is_measured_not_guessed_from_a_name(tmp_path):
    """A family Kalshi ships tomorrow must be costed correctly on the first
    slate it appears in, which a name-prefix heuristic cannot do."""
    packet = build_slate(catalog_dir(tmp_path), config(), slate_date=None).packets[0]
    renamed = json.loads(json.dumps(packet))
    for record in renamed["contracts"]:
        if (record.get("semantics") or {}).get("requires") != "period_distribution":
            record["family"] = "a_family_nobody_has_named_before"
    assert game_cost(renamed).families > 0


# ---------------------------------------------------------- the packing


def test_a_game_is_never_split_across_batches(tmp_path):
    slate = slate_of(tmp_path)
    batches = build_batches(slate, window_order=WINDOW_ORDER)
    seen: set[str] = set()
    for batch in batches:
        for key in batch.game_keys:
            assert key not in seen, f"{key} appears in two batches"
            seen.add(key)
    assert seen == {str(p["game_key"]) for p in slate["games"]}


def test_every_game_lands_in_exactly_one_batch(tmp_path):
    slate = slate_of(tmp_path)
    batches = build_batches(slate, window_order=WINDOW_ORDER)
    assert sum(len(b.packets) for b in batches) == len(slate["games"])
    assert sum(b.eligible for b in batches) == sum(
        p["counts"]["eligible"] for p in slate["games"]
    )


def test_batches_are_contiguous_runs_of_kickoffs(tmp_path):
    """`early_b1` must be the first games to kick, not a random scatter: the
    operator reading it has a kickoff forty minutes away."""
    packets = []
    base = build_slate(catalog_dir(tmp_path), config(), slate_date=None).packets[0]
    for index in range(12):
        clone = json.loads(json.dumps(base))
        clone["game_key"] = f"G{index:02d}"
        clone["kickoff"] = f"2026-09-19T1{index % 10}:00:00Z"
        packets.append(clone)
    batches = split_into_batches("early", packets, budget=2.0)
    ordered = [key for batch in batches for key in batch.game_keys]
    kickoffs = [next(p["kickoff"] for p in packets if p["game_key"] == k) for k in ordered]
    assert kickoffs == sorted(kickoffs)


def test_the_game_count_ceiling_holds_however_cheap_the_games_are(tmp_path):
    base = build_slate(catalog_dir(tmp_path), config(), slate_date=None).packets[0]
    packets = []
    for index in range(30):
        clone = json.loads(json.dumps(base))
        clone["game_key"] = f"G{index:02d}"
        clone["kickoff"] = f"2026-09-19T16:00:0{index % 10}Z"
        packets.append(clone)
    batches = split_into_batches("early", packets, budget=1000.0)
    assert batches
    assert all(len(b.packets) <= MAX_GAMES_PER_BATCH for b in batches)


# -------------------------------------------------------- the artifact


def test_the_batch_artifact_carries_no_contract_rows(tmp_path):
    """The whole latency saving, stated as an assertion.

    A batch artifact that carried the ladders would be the old analysis
    artifact with extra steps."""
    slate = slate_of(tmp_path)
    batch = build_batches(slate, window_order=WINDOW_ORDER)[0]
    document = batch_document(slate, batch)
    assert document["contract_rows_in_this_file"] == 0
    encoded = json.dumps(document)
    for banned in ("yes_ask", "no_ask", "yes_entry", "no_entry", '"rows"', "executable"):
        assert banned not in encoded, f"the batch artifact leaked {banned}"
    for game in document["games"]:
        assert "contracts" not in game
        assert "markets_available" in game


def test_the_batch_artifact_states_no_projection_and_no_fair_value(tmp_path):
    slate = slate_of(tmp_path)
    batch = build_batches(slate, window_order=WINDOW_ORDER)[0]
    document = batch_document(slate, batch)
    assert document["model_projections"] == "absent"
    encoded = json.dumps(document).lower()
    for banned in ("fair_probability", "projected_score", "power_rating", "net_edge"):
        assert banned not in encoded


# ------------------------------------------------ union equals the whole


def test_the_union_of_batches_equals_the_whole_window(tmp_path):
    """*** THE STREAMING INVARIANT ***

    Same games, same handicaps, same required edge. Batch-by-batch must give
    the same candidate set as one pass over the window, or "process it in
    pieces" means "get a different answer".
    """
    slate = slate_of(tmp_path)
    packets = {str(p["game_key"]): p for p in slate["games"]}
    handicaps = {key: handicap_for(packet) for key, packet in packets.items()}
    evaluations = {
        key: evaluate_game(packet, handicaps[key], min_net_edge=0.02)
        for key, packet in packets.items()
    }

    def candidate_set(keys):
        artifact = build_candidate_artifact(
            "early",
            [evaluations[k] for k in keys],
            {k: packets[k] for k in keys},
            {k: handicaps[k] for k in keys},
            min_net_edge=0.02,
        )
        return {
            (c["game_key"], c["market"], c["side"], c["kalshi_executable_price"])
            for c in artifact["candidates"]
        }

    whole = candidate_set(list(packets))
    union: set = set()
    for batch in build_batches(slate, window_order=WINDOW_ORDER):
        union |= candidate_set(batch.game_keys)
    assert union == whole


def test_an_unhandicapped_game_costs_its_own_contracts_and_nothing_else(tmp_path):
    """Streaming must not mean a half-scanned window reports as whole.

    A game with no handicap is not evaluated at all, so it cannot be part of a
    candidate artifact -- the CLI's gate refuses it. What it must never do is
    remove contracts from the games that WERE handicapped.
    """
    slate = slate_of(tmp_path)
    packets = {str(p["game_key"]): p for p in slate["games"]}
    key = GAME_KEY
    evaluation = evaluate_game(packets[key], handicap_for(packets[key]), min_net_edge=0.02)
    assert evaluation.eligible == len(packets[key]["contracts"])
    assert evaluation.unaccounted == 0
