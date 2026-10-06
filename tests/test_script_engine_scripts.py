"""Findings and scripts: evidence-gated, deterministic, and free of market language."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from script_engine_fakes import availability, identity, synthetic_season

from cfb_edge_finder.scripting.findings import CATEGORY_DATA, derive_findings
from cfb_edge_finder.scripting.football import FootballPacket, build_content
from cfb_edge_finder.scripting.scripts import ROLES, build_scripts

MARKET_WORDS = (
    "favored",
    "favoured",
    "favorite ",
    "underdog ",
    "spread",
    "line ",
    "odds",
    "implied",
    "market",
    "price",
    "kalshi",
    "+ev",
    "probability",
)


def _packet(rows, *, home="F00", away="F01", avail=None, check=None, fresh=None) -> FootballPacket:
    return FootballPacket(
        identity=identity(home, away),
        game_key="KEY",
        rows=tuple(rows),
        availability=avail or availability(),
        identity_check=check or {"status": "PASS", "orientation_swapped": False},
        freshness=fresh or {"status": "FRESH"},
    )


@pytest.fixture(scope="module")
def season():
    return synthetic_season()


@pytest.fixture(scope="module")
def content(season):
    return build_content(_packet(season))


def test_a_strong_unit_level_edge_produces_a_control_script(content):
    scripts = content["game_scripts"]["scripts"]
    primary = scripts[0]
    assert primary["role"] == "PRIMARY" and primary["archetype"] == "HOME_CONTROL"
    assert primary["probability"] is None
    assert "HOME_SUSTAINED_EFFICIENCY_ADVANTAGE" in primary["required_findings"]
    assert 2 <= len(scripts) <= 4
    assert [s["rank"] for s in scripts] == list(range(1, len(scripts) + 1))
    assert all(s["role"] in ROLES for s in scripts)


def test_every_causal_step_cites_a_finding_that_exists(content):
    codes = {f["code"] for f in content["matchup_findings"]}
    for script in content["game_scripts"]["scripts"]:
        assert script["causal_chain"]
        for step in script["causal_chain"]:
            assert step["findings"], step
            assert set(step["findings"]) <= codes, step
        for key in ("required_findings", "supporting_findings", "contradicting_findings"):
            assert set(script[key]) <= codes


def test_every_finding_points_at_metrics_the_profile_publishes(content):
    teams = content["matchup_profile"]["teams"]
    for f in content["matchup_findings"]:
        if f["category"] == CATEGORY_DATA:
            continue
        assert f["metric_refs"], f["code"]
        for ref in f["metric_refs"]:
            side, unit, metric_id = ref.split(".", 2)
            assert f"{unit}.{metric_id}" in teams[side]["metrics"], ref


def test_generated_text_uses_no_market_language(content):
    text = json.dumps(
        {
            "findings": [f["statement"] for f in content["matchup_findings"]],
            "scripts": [
                [s["title"], s["summary"], [c["step"] for c in s["causal_chain"]]]
                for s in content["game_scripts"]["scripts"]
            ],
            "read": {k: v for k, v in content["sift_read"].items() if k != "generated_from"},
        }
    ).lower()
    for word in MARKET_WORDS:
        assert word not in text, word


def test_danger_breaks_the_primary_and_never_restates_it(content):
    scripts = {s["role"]: s for s in content["game_scripts"]["scripts"]}
    if "DANGER" in scripts:
        primary, danger = scripts["PRIMARY"], scripts["DANGER"]
        assert danger["archetype"] != primary["archetype"]
        lo = primary["outcome_shape"]["bands"]["home_margin"][0]
        d = danger["outcome_shape"]["bands"]["home_margin"]
        assert (d[0] + d[1]) / 2 < lo


def test_no_evidence_means_no_script():
    out = build_scripts("E", [], {"home": "A", "away": "B"}, {}, "LOW")
    assert out["status"] == "NO_SCRIPT_CLEARED_EVIDENCE" and out["scripts"] == []


def test_an_evenly_matched_game_gets_a_tossup_not_a_winner(season):
    # F03 vs F03-like: use two mid teams by swapping in a mirror of the same team.
    content = build_content(_packet(season, home="F05", away="F05"))
    archetypes = {s["archetype"] for s in content["game_scripts"]["scripts"]}
    assert "HOME_CONTROL" not in archetypes and "AWAY_CONTROL" not in archetypes


def test_noise_cannot_become_a_finding_early_in_the_season(season):
    early = [r for r in season if r.week <= 2]
    content = build_content(_packet(early))
    matchup = [f for f in content["matchup_findings"] if f["category"] == "MATCHUP"]
    for f in matchup:
        assert f["uncertainty"] is None or abs(f["value"]) >= f["uncertainty"]
    assert content["data_confidence"] == "LOW"
    assert "gate failed: sample_size" in content["confidence"]["reasons"]


def test_confidence_describes_evidence_not_the_pick(season):
    good = build_content(_packet(season))
    assert good["data_confidence"] == "HIGH"
    qb = build_content(_packet(season, avail=availability(qb_uncertain=True)))
    assert qb["data_confidence"] == "LOW"
    assert any(f["code"] == "HOME_QB_AVAILABILITY_UNCERTAIN" for f in qb["matchup_findings"])
    unobserved = build_content(_packet(season, avail=availability(status="NOT_OBSERVED")))
    assert unobserved["data_confidence"] == "MEDIUM"
    stale = build_content(_packet(season, fresh={"status": "STALE"}))
    assert stale["data_confidence"] == "LOW"
    failed = build_content(_packet(season, check={"status": "FAIL", "reason": "x"}))
    assert failed["data_confidence"] == "LOW"
    # The scripts themselves do not change with confidence: it describes evidence.
    strip = lambda c: [(s["archetype"], s["role"]) for s in c["game_scripts"]["scripts"]]  # noqa: E731
    assert strip(good) == strip(unobserved)


def test_a_quarterback_change_in_the_box_scores_is_surfaced(season):
    last_week = max(r.week for r in season)
    changed = [
        replace(r, primary_passer="Backup QB") if r.team_id == "F00" and r.week == last_week else r for r in season
    ]
    content = build_content(_packet(changed))
    assert any(f["code"] == "HOME_QB_CHANGE_RECENT" for f in content["matchup_findings"])
    assert content["data_confidence"] != "HIGH"


def test_findings_are_deterministic(season):
    a = build_content(_packet(season))
    b = build_content(_packet(list(reversed(season))))
    assert a["matchup_findings"] == b["matchup_findings"]
    assert a["game_scripts"] == b["game_scripts"]


def test_turnovers_alone_never_carry_a_script(content):
    for script in content["game_scripts"]["scripts"]:
        required = set(script["required_findings"])
        assert not required or not required <= {
            "HOME_OFFENSE_TURNOVER_PRONE",
            "AWAY_OFFENSE_TURNOVER_PRONE",
            "HOME_DEFENSE_TURNOVER_RELIANT",
            "AWAY_DEFENSE_TURNOVER_RELIANT",
        }


def test_derive_findings_needs_no_market_argument():
    import inspect

    params = set(inspect.signature(derive_findings).parameters)
    assert params == {"vector", "names", "availability"}


@pytest.mark.parametrize(
    "home,away", [(h, a) for h in ("F00", "F02", "F05") for a in ("F01", "F03", "F04", "F06", "F07") if h != a]
)
def test_every_step_in_every_matchup_cites_only_findings_that_exist(season, home, away):
    content = build_content(_packet(season, home=home, away=away))
    codes = {f["code"] for f in content["matchup_findings"]}
    for script in content["game_scripts"]["scripts"]:
        for step in script["causal_chain"]:
            assert step["findings"] and set(step["findings"]) <= codes, (script["archetype"], step)


def test_a_volume_only_grind_never_claims_the_defenses_out_rate_the_offenses():
    from cfb_edge_finder.scripting.scripts import _Ctx, _grind

    findings = [
        {"code": "LOW_POSSESSION_ENVIRONMENT", "strength": "MODERATE", "category": "ENVIRONMENT"},
        {"code": "HIGH_SCORING_ENVIRONMENT", "strength": "STRONG", "category": "ENVIRONMENT"},
    ]
    cand = _grind(_Ctx(findings, {"home": "A", "away": "B"}, {}))
    text = " ".join(s["step"] for s in cand.chain) + cand.summary
    assert "out-rates" not in text and "upper hand" not in text


def test_every_band_states_its_authority_and_only_margins_define_an_archetype(season):
    from cfb_edge_finder.scripting.scripts import ARCHETYPE_DEFINITION, UNCALIBRATED_DESCRIPTIVE

    seen = set()
    for home, away in [(h, a) for h in ("F00", "F02", "F05") for a in ("F01", "F03", "F04", "F06", "F07")]:
        out = build_content(_packet(season, home=home, away=away))["game_scripts"]
        assert out["band_policy"]["home_margin"] == ARCHETYPE_DEFINITION
        assert out["band_policy"]["total_points"] == UNCALIBRATED_DESCRIPTIVE
        for script in out["scripts"]:
            shape = script["outcome_shape"]
            assert set(shape["band_authority"]) == {k for k, v in shape["bands"].items() if v is not None}
            for band, authority in shape["band_authority"].items():
                seen.add((band, authority))
                expected = ARCHETYPE_DEFINITION if band == "home_margin" else UNCALIBRATED_DESCRIPTIVE
                assert authority == expected, (script["archetype"], band)
    assert ("home_margin", ARCHETYPE_DEFINITION) in seen
    assert {b for b, a in seen if a == UNCALIBRATED_DESCRIPTIVE} >= {"home_points", "away_points"}


# --- a scoring or pace environment is not a margin -----------------------------------------------------

_NAMES = {"home": "Home U", "away": "Away St"}
_BASE = {"home_points": 31.0, "away_points": 27.0, "total_points": 58.0}


def _f(code, strength="MODERATE", category="ENVIRONMENT"):
    return {"code": code, "strength": strength, "category": category}


@pytest.mark.parametrize(
    "archetype,environment",
    [
        ("COMPETITIVE_SHOOTOUT", "HIGH_SCORING_ENVIRONMENT"),
        ("COMPETITIVE_GRIND", "LOW_SCORING_ENVIRONMENT"),
        ("COMPETITIVE_GRIND", "LOW_POSSESSION_ENVIRONMENT"),
    ],
)
def test_an_environment_alone_cannot_create_an_active_one_score_band(archetype, environment):
    from cfb_edge_finder.execution.card_review import WinSet
    from cfb_edge_finder.scripting.market_map import NEUTRAL, classify_against
    from cfb_edge_finder.scripting.scripts import _Ctx, _grind, _shootout, band_authority

    builder = _shootout if archetype == "COMPETITIVE_SHOOTOUT" else _grind
    cand = builder(_Ctx([_f(environment)], _NAMES, _BASE))
    # The script and its scoring environment stay...
    assert cand is not None and cand.archetype == archetype
    assert cand.shape["total_environment"] in ("ELEVATED", "SUPPRESSED")
    assert cand.shape["bands"]["total_points"] is not None
    # ...but it states no margin, says nothing about one score, and has no margin authority.
    assert cand.shape["bands"]["home_margin"] is None
    assert cand.shape["margin_environment"] == "NOT_STATED"
    text = " ".join(st["step"] for st in cand.chain) + " " + cand.summary
    assert "one score" not in text and "close game" not in text and "separate" not in text
    shape = {**cand.shape, "band_authority": band_authority(cand.shape["bands"])}
    assert "home_margin" not in shape["band_authority"]
    # So it contributes nothing to a moneyline or a spread.
    for ws in (WinSet("full_game:home_margin", 1, float("inf")), WinSet("full_game:home_margin", -3, float("inf"))):
        assert classify_against(ws, shape)["status"] == NEUTRAL


@pytest.mark.parametrize(
    "archetype,environment,closeness",
    [
        ("COMPETITIVE_SHOOTOUT", "HIGH_SCORING_ENVIRONMENT", "EVEN_MATCHUP"),
        ("COMPETITIVE_SHOOTOUT", "HIGH_SCORING_ENVIRONMENT", "NARROW_EFFICIENCY_GAP"),
        ("COMPETITIVE_GRIND", "LOW_SCORING_ENVIRONMENT", "NARROW_EFFICIENCY_GAP"),
        ("COMPETITIVE_GRIND", "LOW_POSSESSION_ENVIRONMENT", "EVEN_MATCHUP"),
    ],
)
def test_independent_closeness_evidence_states_the_one_score_band_and_carries_the_claim(
    archetype, environment, closeness
):
    from cfb_edge_finder.scripting.scripts import (
        ARCHETYPE_DEFINITION,
        ONE_SCORE,
        _Ctx,
        _grind,
        _shootout,
        band_authority,
    )

    builder = _shootout if archetype == "COMPETITIVE_SHOOTOUT" else _grind
    cand = builder(_Ctx([_f(environment), _f(closeness)], _NAMES, _BASE))
    assert cand.shape["bands"]["home_margin"] == [-ONE_SCORE, ONE_SCORE]
    assert cand.shape["margin_environment"] == "ONE_SCORE"
    assert band_authority(cand.shape["bands"])["home_margin"] == ARCHETYPE_DEFINITION
    # Every step that claims a one-score margin cites the closeness finding -- and only it.
    margin_steps = [st for st in cand.chain if "one score" in st["step"]]
    assert margin_steps
    for st in margin_steps:
        assert st["findings"] == [closeness]
    # No step lets the environment carry the margin claim.
    for st in cand.chain:
        if environment in st["findings"]:
            assert "one score" not in st["step"] and "separate" not in st["step"]


def test_a_strong_efficiency_edge_and_a_scoring_advantage_alone_cannot_pull_away():
    from cfb_edge_finder.scripting.scripts import _Ctx, _pulls_away

    findings = [
        _f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "STRONG", "MATCHUP"),
        _f("HOME_SCORING_ADVANTAGE", "STRONG", "MATCHUP"),
    ]
    assert _pulls_away(_Ctx(findings, _NAMES, _BASE), "home") is None


@pytest.mark.parametrize("edge", ["FINISHING", "DISRUPTION", "EXPLOSIVE", "RUSH", "PASS"])
def test_an_independent_second_matchup_advantage_can_pull_away(edge):
    from cfb_edge_finder.scripting.scripts import _Ctx, _pulls_away

    findings = [
        _f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "STRONG", "MATCHUP"),
        _f("HOME_SCORING_ADVANTAGE", "STRONG", "MATCHUP"),
        _f(f"HOME_{edge}_ADVANTAGE", "MODERATE", "MATCHUP"),
    ]
    cand = _pulls_away(_Ctx(findings, _NAMES, _BASE), "home")
    assert cand is not None and cand.shape["bands"]["home_margin"] == [17, 45]
    # The scoring advantage may support the script; the qualifying step cites the independent edge.
    assert "HOME_SCORING_ADVANTAGE" in cand.supporting
    second_step = cand.chain[1]
    assert second_step["findings"] == [f"HOME_{edge}_ADVANTAGE"]
