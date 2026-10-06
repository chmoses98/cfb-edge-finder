"""The factual layer: provenance, freshness, gating, and the line it must not cross.

*** TWO THINGS THIS FILE GUARDS ***
1. The repository fetches FACTS and forms no opinion about them. A power
   rating, a projected score or an opponent-adjusted coefficient appearing
   here would reintroduce the retired model through the side door.
2. Missing is a value. "We looked and there are no injuries" and "we could not
   look" produce the same handicap and only one of them is a fact.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta

import pytest

from cfb_edge_finder.execution import context as context_module
from cfb_edge_finder.execution import context_sources
from cfb_edge_finder.execution.context import (
    CONTEXT_DOMAINS,
    MATERIAL_DOMAINS,
    MIN_GAMES_FOR_TEAM_TOTAL,
    ContextField,
    GameContext,
    confidence_ceiling,
    context_fingerprint,
    data_quality_for,
    freshness_of,
    gates_for,
    material_fingerprint,
    refresh_quality,
    rest_days,
)
from cfb_edge_finder.execution.context_sources import (
    availability_field,
    completed_games_for,
    identity_field,
    records_field,
    scoring_field,
)
from cfb_edge_finder.execution.evaluator import EvaluationStatus, evaluate_game
from cfb_edge_finder.execution.handicap import parse_handicap
from cfb_edge_finder.execution.quality import ConfidenceCeiling, DataQuality, FieldQuality
from cfb_edge_finder.execution.slate import build_slate, load_context_store
from tests.execution_fakes import GAME_KEY, catalog_dir
from tests.test_execution_disposition import config

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


def field(domain, values, *, quality=FieldQuality.FRESH.value, observed_at=None):
    return ContextField(
        domain=domain,
        values=values,
        source="test",
        observed_at=observed_at or NOW.isoformat(),
        quality=quality,
    )


def full_context(game_key=GAME_KEY, **overrides):
    fields = {domain: field(domain, {"x": 1}) for domain in CONTEXT_DOMAINS}
    fields["scoring"] = field(
        "scoring", {"home_games_observed": 5, "away_games_observed": 5}
    )
    fields.update(overrides)
    return GameContext(game_key=game_key, fields=fields, collected_at=NOW.isoformat())


# ------------------------------------------ the line that must not be crossed


def test_the_context_modules_produce_no_rating_projection_or_fair_value():
    """A structural scan, in the same spirit as the analysis artifact's own."""
    for module in (context_module, context_sources):
        source = inspect.getsource(module).lower()
        for banned in (
            "power_rating",
            "projected_score",
            "fair_probability",
            "win_probability",
            "expected_margin",
            "elo",
        ):
            # The word may appear in prose explaining the ban; it may not
            # appear as a key this code emits.
            assert f'"{banned}"' not in source, f"{module.__name__} emits {banned}"


def test_scoring_states_it_is_not_opponent_adjusted_in_the_data_itself():
    """A reader who assumes the number was schedule-adjusted would be reading
    an adjustment this repository has never computed, so the disclaimer is a
    FIELD rather than only a docstring."""
    history = [{"points_for": 28.0, "points_against": 17.0, "date": "2026-09-12T00:00:00+00:00"}] * 4
    one = scoring_field(history, history, observed_at=NOW.isoformat())
    assert one.values["opponent_adjusted"] is False
    assert "NOT adjusted" in one.values["note"]


# ------------------------------------------------------- missing is a value


def test_an_unreadable_injury_endpoint_is_never_read_as_no_injuries():
    """*** THE SUBSTITUTION THIS REPOSITORY MUST NEVER MAKE ***"""
    unreadable = availability_field(None, None, observed_at=NOW.isoformat())
    empty = availability_field({"items": []}, {"items": []}, observed_at=NOW.isoformat())

    assert unreadable.quality == FieldQuality.MISSING.value
    assert "NOT 'no injuries'" in (unreadable.detail or "")
    assert unreadable.values == {}

    assert empty.quality == FieldQuality.FRESH.value
    assert empty.values["home_listed"] == 0
    assert empty.values["away_listed"] == 0


def test_one_side_readable_and_one_not_is_partial_and_names_the_null():
    one = availability_field({"items": []}, None, observed_at=NOW.isoformat())
    assert one.quality == FieldQuality.PARTIAL.value
    assert one.values["home_listed"] == 0
    assert one.values["away_listed"] is None, (
        "a team whose list could not be read is null, never zero"
    )


def test_an_absent_context_store_produces_a_full_object_of_missing_domains():
    """A caller that has to test for None will eventually forget, and the
    forgotten branch treats absent facts as adequate ones."""
    empty = GameContext.empty("G", reason="nothing ran", as_of=NOW)
    assert set(empty.fields) == set(CONTEXT_DOMAINS)
    assert empty.missing_domains == tuple(sorted(CONTEXT_DOMAINS))
    assert all(f.quality == FieldQuality.MISSING.value for f in empty.fields.values())


def test_rest_days_is_none_rather_than_the_most_convincing_wrong_answer():
    assert rest_days(NOW, None) is None
    assert rest_days(None, NOW) is None
    assert rest_days(NOW, NOW - timedelta(days=7)) == 7


# --------------------------------------------------------------- freshness


def test_freshness_is_measured_against_the_slate_clock_not_the_collection_clock():
    """A context file that recorded `fresh` when it was written and was read
    two days later would report two-day-old weather as current."""
    stale_weather = full_context(
        environment=field(
            "environment", {"temperature_f": 70}, observed_at=(NOW - timedelta(hours=20)).isoformat()
        )
    )
    aged = refresh_quality(stale_weather, as_of=NOW)
    assert aged.fields["environment"].quality == FieldQuality.STALE.value
    assert "past the" in aged.fields["environment"].detail

    # The same observation is fresh against a clock 20 hours earlier.
    fresh = refresh_quality(stale_weather, as_of=NOW - timedelta(hours=19))
    assert fresh.fields["environment"].quality == FieldQuality.FRESH.value


def test_an_observation_with_no_timestamp_is_missing_not_fresh():
    quality, age = freshness_of("records", None, as_of=NOW)
    assert quality == FieldQuality.MISSING.value
    assert age is None


def test_a_future_forecast_hour_is_fresh_rather_than_negatively_aged():
    quality, age = freshness_of(
        "environment", (NOW + timedelta(hours=3)).isoformat(), as_of=NOW
    )
    assert quality == FieldQuality.FRESH.value
    assert age == 0.0


# ------------------------------------------------------- confidence ceiling


def test_a_missing_load_bearing_domain_caps_confidence_at_insufficient():
    context = full_context(records=ContextField.missing("records", "not fetched"))
    ceiling, reasons = confidence_ceiling(context)
    assert ceiling == ConfidenceCeiling.INSUFFICIENT.value
    assert any("load-bearing" in r for r in reasons)


def test_both_hazard_domains_unknown_caps_confidence_at_low():
    context = full_context(
        availability=ContextField.missing("availability", "not fetched"),
        environment=ContextField.missing("environment", "not fetched"),
    )
    ceiling, reasons = confidence_ceiling(context)
    assert ceiling == ConfidenceCeiling.LOW.value
    assert any("neither availability nor environment" in r for r in reasons)


def test_full_fresh_coverage_permits_high():
    assert confidence_ceiling(full_context())[0] == ConfidenceCeiling.HIGH.value


def test_a_handicapper_may_lower_the_confidence_and_may_not_raise_it():
    measured = DataQuality(confidence_ceiling=ConfidenceCeiling.LOW.value)
    claimed_high = DataQuality(confidence_ceiling=ConfidenceCeiling.HIGH.value)
    claimed_low = DataQuality(confidence_ceiling=ConfidenceCeiling.INSUFFICIENT.value)
    assert claimed_high.merged_with_measurement(measured).confidence_ceiling == "low"
    assert (
        claimed_low.merged_with_measurement(measured).confidence_ceiling == "insufficient"
    )


def test_a_handicap_may_not_remove_a_gate_the_measurement_raised():
    """Otherwise the fail-closed behaviour is one forgotten key away from off."""
    from cfb_edge_finder.execution.quality import DataQualityGate

    measured = DataQuality(
        gates=(DataQualityGate("team_total", "thin evidence", "min_games_observed=1"),)
    )
    silent = DataQuality()
    merged = silent.merged_with_measurement(measured)
    assert merged.gated_families == {"team_total"}


# ---------------------------------------------------------- family gating


def test_thin_scoring_evidence_closes_team_totals_and_names_the_measurement():
    context = full_context(
        scoring=field("scoring", {"home_games_observed": 6, "away_games_observed": 1})
    )
    gates = gates_for(context)
    assert {g.family for g in gates} >= {"team_total"}
    assert all("min_games_observed=1" in g.measured for g in gates)


def test_the_gate_is_a_count_of_games_not_a_division_label():
    """Deliberately NOT an FCS rule. An FCS team with a full season of
    observations passes; an FBS team in week one does not."""
    plenty = full_context(
        scoring=field(
            "scoring",
            {
                "home_games_observed": MIN_GAMES_FOR_TEAM_TOTAL,
                "away_games_observed": MIN_GAMES_FOR_TEAM_TOTAL,
            },
        )
    )
    assert gates_for(plenty) == ()
    thin = full_context(
        scoring=field(
            "scoring",
            {
                "home_games_observed": MIN_GAMES_FOR_TEAM_TOTAL - 1,
                "away_games_observed": 10,
            },
        )
    )
    assert gates_for(thin)


def test_a_gated_family_becomes_a_counted_terminal_bucket_never_a_guess(tmp_path):
    """The invariant must survive the gate: a closed family is unpriceable and
    accounted for, not dropped."""
    game = build_slate(catalog_dir(tmp_path), config(), slate_date=None).packets[0]
    handicap = parse_handicap(
        {
            "schema_version": "cfb_handicap_payload/2.0.0",
            "game_key": GAME_KEY,
            "teams": {"home": "Ole Miss", "away": "LSU"},
            "period_distributions": {
                "full_game": {
                    "home_mean": 27.0,
                    "away_mean": 24.0,
                    "home_sd": 10.5,
                    "away_sd": 10.0,
                    "correlation": 0.1,
                    "uncertainty": {"margin_points": 3.0},
                }
            },
        }
    )
    evaluation = evaluate_game(game, handicap, min_net_edge=0.02)
    gated = [
        r
        for r in evaluation.rows
        if r["status"] == EvaluationStatus.UNPRICEABLE_INSUFFICIENT_DATA.value
    ]
    assert gated, "the fixture has no collected context, so team totals must be gated"
    assert evaluation.unaccounted == 0
    for row in gated:
        assert row["data_quality_gate"]["measured"]
        assert "fair_probability" not in row, "a gated contract must never carry a price"


# ------------------------------------------------------------ materiality


def test_the_material_fingerprint_ignores_an_efficiency_refresh():
    """An invalidation that fires on everything is one nobody reads."""
    before = full_context()
    after = full_context(efficiency=field("efficiency", {"ppa": 0.31}))
    assert material_fingerprint(before) == material_fingerprint(after)
    assert context_fingerprint(before) != context_fingerprint(after)


def test_the_material_fingerprint_moves_when_availability_moves():
    before = full_context()
    after = full_context(
        availability=field("availability", {"home_injuries": [{"athlete": "QB1"}]})
    )
    assert material_fingerprint(before) != material_fingerprint(after)


def test_the_material_domains_are_the_ones_a_handicapper_reasons_from():
    assert set(MATERIAL_DOMAINS) == {"availability", "environment", "identity"}


# ----------------------------------------------------------- the store


def test_an_absent_context_directory_is_an_empty_store_not_an_error(tmp_path):
    assert load_context_store(tmp_path / "nothing-here") == {}
    assert load_context_store(None) == {}


def test_an_unreadable_context_file_costs_that_game_its_context_not_the_run(tmp_path):
    store_dir = tmp_path / "context"
    store_dir.mkdir()
    (store_dir / "GOOD.json").write_text(
        json.dumps(full_context("GOOD").as_dict()), encoding="utf-8"
    )
    (store_dir / "BROKEN.json").write_text("{not json", encoding="utf-8")
    store = load_context_store(store_dir)
    assert set(store) == {"GOOD"}


def test_a_slate_built_with_context_carries_it_and_the_hashes(tmp_path):
    store_dir = tmp_path / "context"
    store_dir.mkdir()
    (store_dir / f"{GAME_KEY}.json").write_text(
        json.dumps(full_context().as_dict()), encoding="utf-8"
    )
    build = build_slate(
        catalog_dir(tmp_path), config(), slate_date=None, context_dir=store_dir
    )
    context = build.packets[0]["factual_context"]
    assert context["context_hash"] and context["material_context_hash"]
    assert context["data_quality"]["confidence_ceiling"] in {
        c.value for c in ConfidenceCeiling
    }
    assert context["coverage"]["records"] in {q.value for q in FieldQuality}


def test_a_slate_built_without_context_still_builds_and_says_so(tmp_path):
    build = build_slate(catalog_dir(tmp_path), config(), slate_date=None, context_dir=None)
    context = build.packets[0]["factual_context"]
    assert context["data_quality"]["confidence_ceiling"] == "insufficient"
    assert set(context["missing_domains"]) == set(CONTEXT_DOMAINS)
    coverage = build.slate["factual_context_coverage"]
    assert coverage["games_with_no_context"] == coverage["games"]


# --------------------------------------------------- the ESPN parsers


def test_completed_games_ignores_a_game_that_has_not_finished():
    events = [
        {
            "date": "2026-09-12T20:00:00Z",
            "status": {"type": {"completed": True}},
            "competitions": [
                {
                    "competitors": [
                        {"team": {"displayName": "LSU"}, "score": "31", "homeAway": "away"},
                        {"team": {"displayName": "Ole Miss"}, "score": "24", "homeAway": "home"},
                    ]
                }
            ],
        },
        {
            "date": "2026-09-19T20:00:00Z",
            "status": {"type": {"completed": False}},
            "competitions": [
                {
                    "competitors": [
                        {"team": {"displayName": "LSU"}, "homeAway": "away"},
                        {"team": {"displayName": "Auburn"}, "homeAway": "home"},
                    ]
                }
            ],
        },
    ]
    rows = completed_games_for({"LSU"}, events)
    assert len(rows) == 1
    assert rows[0]["points_for"] == 31.0
    assert rows[0]["points_against"] == 24.0


def test_a_score_is_read_from_either_espn_shape():
    events = [
        {
            "date": "2026-09-12T20:00:00Z",
            "status": {"type": {"completed": True}},
            "competitions": [
                {
                    "competitors": [
                        {"team": {"displayName": "LSU"}, "score": {"value": 31.0}, "homeAway": "away"},
                        {"team": {"displayName": "Ole Miss"}, "score": {"value": 24.0}, "homeAway": "home"},
                    ]
                }
            ],
        }
    ]
    assert completed_games_for({"LSU"}, events)[0]["points_for"] == 31.0


def test_an_indoor_venue_has_an_environment_rather_than_a_missing_one():
    """Charging the research cost of weather that cannot matter would tell a
    handicapper to go and look something up for no reason."""
    event = {
        "id": "1",
        "date": "2026-09-19T20:00:00Z",
        "competitions": [{"venue": {"fullName": "A Dome", "indoor": True}, "competitors": []}],
    }
    from cfb_edge_finder.execution.context_sources import environment_field

    one = environment_field(event, observed_at=NOW.isoformat())
    assert one.quality == FieldQuality.FRESH.value
    assert one.values["indoor"] is True


def test_records_absent_on_both_sides_is_missing():
    event = {"competitions": [{"competitors": [{"homeAway": "home"}, {"homeAway": "away"}]}]}
    assert records_field(event, observed_at=NOW.isoformat()).quality == FieldQuality.MISSING.value


def test_identity_reports_partial_when_the_venue_is_absent():
    event = {
        "id": "7",
        "date": "2026-09-19T20:00:00Z",
        "competitions": [{"competitors": [{"team": {"displayName": "LSU"}, "homeAway": "away"}]}],
    }
    one = identity_field(event, observed_at=NOW.isoformat())
    assert one.quality == FieldQuality.PARTIAL.value


@pytest.mark.parametrize("domain", CONTEXT_DOMAINS)
def test_every_named_domain_is_representable(domain):
    """A domain nobody has named is a gap nobody can measure, so the list is
    exhaustive by construction and every entry must round-trip."""
    context = full_context()
    assert domain in context.fields
    assert domain in data_quality_for(context).coverage
