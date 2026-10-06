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


#: Closeness findings carry the efficiency gap and its uncertainty. RESOLVED
#: measurements may authorise a close margin; UNRESOLVED ones may not.
RESOLVED = {"EVEN_MATCHUP": (0.2, 1.0), "NARROW_EFFICIENCY_GAP": (1.5, 1.0)}
UNRESOLVED = {"EVEN_MATCHUP": (0.2, 1.9), "NARROW_EFFICIENCY_GAP": (1.2, 1.5)}


def _f(code, strength="MODERATE", category="ENVIRONMENT", resolved=True):
    out = {"code": code, "strength": strength, "category": category}
    if code in RESOLVED:
        out["value"], out["uncertainty"] = (RESOLVED if resolved else UNRESOLVED)[code]
    return out


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


# --- the margin-authority contract ---------------------------------------------------------------------

_EDGE = _f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "MODERATE", "MATCHUP")


def _hangs(findings):
    from cfb_edge_finder.scripting.scripts import _Ctx, _hangs_around

    return _hangs_around(_Ctx(findings, _NAMES, _BASE), "home")


def test_an_efficiency_edge_and_low_possessions_alone_create_no_hangs_around():
    assert _hangs([_EDGE, _f("LOW_POSSESSION_ENVIRONMENT")]) is None


def test_a_trailing_side_disruption_counter_creates_hangs_around():
    cand = _hangs([_EDGE, _f("AWAY_DISRUPTION_ADVANTAGE", "MODERATE", "MATCHUP")])
    assert cand is not None and cand.archetype == "UNDERDOG_HANGS_AROUND"
    assert cand.required == ["HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "AWAY_DISRUPTION_ADVANTAGE"]
    assert cand.margin_evidence == ["HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "AWAY_DISRUPTION_ADVANTAGE"]
    assert cand.shape["bands"]["home_margin"] is not None


def test_low_possessions_beside_a_real_counter_only_supports_it():
    cand = _hangs([_EDGE, _f("LOW_POSSESSION_ENVIRONMENT"), _f("AWAY_DISRUPTION_ADVANTAGE", "MODERATE", "MATCHUP")])
    assert cand is not None
    assert "LOW_POSSESSION_ENVIRONMENT" not in cand.required
    assert "LOW_POSSESSION_ENVIRONMENT" in cand.supporting
    assert "LOW_POSSESSION_ENVIRONMENT" not in cand.margin_evidence
    assert "AWAY_DISRUPTION_ADVANTAGE" in cand.margin_evidence
    one_score = [st for st in cand.chain if "one score" in st["step"]]
    assert one_score and all("AWAY_DISRUPTION_ADVANTAGE" in st["findings"] for st in one_score)
    assert all(st["findings"] != ["LOW_POSSESSION_ENVIRONMENT"] for st in one_score)


def test_a_narrow_efficiency_gap_is_a_legitimate_hangs_around_counter():
    cand = _hangs([_EDGE, _f("NARROW_EFFICIENCY_GAP")])
    assert cand is not None and "NARROW_EFFICIENCY_GAP" in cand.margin_evidence


def test_the_validator_rejects_unauthorised_margin_bands():
    from cfb_edge_finder.scripting.scripts import ARCHETYPE_DEFINITION, margin_authority_violations

    def script(evidence, band=(-8, 8)):
        return {
            "archetype": "X",
            "outcome_shape": {
                "bands": {"home_margin": list(band) if band else None},
                "band_authority": {"home_margin": ARCHETYPE_DEFINITION} if band else {},
                "margin_authority_evidence": evidence,
            },
            "causal_chain": [{"step": "the margin stays within one score", "findings": evidence}],
        }

    assert margin_authority_violations(script(["EVEN_MATCHUP"]), {"EVEN_MATCHUP"}) == []
    assert margin_authority_violations(script([]))  # no evidence
    for forbidden in (
        "HIGH_SCORING_ENVIRONMENT",
        "LOW_SCORING_ENVIRONMENT",
        "HIGH_POSSESSION_ENVIRONMENT",
        "LOW_POSSESSION_ENVIRONMENT",
        "HOME_SCORING_ADVANTAGE",
        "AWAY_SCORING_ADVANTAGE",
    ):
        assert margin_authority_violations(script([forbidden])), forbidden
    assert margin_authority_violations(script(["EVEN_MATCHUP"]), set())  # not a finding of this game
    assert margin_authority_violations(script(["EVEN_MATCHUP"], band=None))  # evidence without a band


def test_the_market_map_refuses_a_margin_band_without_authority_evidence():
    from cfb_edge_finder.execution.card_review import WinSet
    from cfb_edge_finder.scripting.market_map import RESEARCH_UNCALIBRATED, SUPPORTED, classify_against
    from cfb_edge_finder.scripting.scripts import ARCHETYPE_DEFINITION

    ws = WinSet("full_game:home_margin", 1, float("inf"))
    base = {"bands": {"home_margin": [7, 24]}, "band_authority": {"home_margin": ARCHETYPE_DEFINITION}}
    assert classify_against(ws, {**base, "margin_authority_evidence": ["HOME_SUSTAINED_EFFICIENCY_ADVANTAGE"]}) == {
        "status": SUPPORTED,
        "coverage": 1.0,
    }
    for evidence in ([], ["HOME_SCORING_ADVANTAGE"], ["LOW_POSSESSION_ENVIRONMENT"], ["HIGH_SCORING_ENVIRONMENT"]):
        assert classify_against(ws, {**base, "margin_authority_evidence": evidence})["status"] == RESEARCH_UNCALIBRATED


# Every finding a margin builder reads, with the strengths that change behaviour.
_POOL = [
    ("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "STRONG"),
    ("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "MODERATE"),
    ("AWAY_SUSTAINED_EFFICIENCY_ADVANTAGE", "MODERATE"),
    ("HOME_SCORING_ADVANTAGE", "STRONG"),
    ("AWAY_SCORING_ADVANTAGE", "STRONG"),
    ("HIGH_SCORING_ENVIRONMENT", "STRONG"),
    ("LOW_SCORING_ENVIRONMENT", "STRONG"),
    ("HIGH_POSSESSION_ENVIRONMENT", "STRONG"),
    ("LOW_POSSESSION_ENVIRONMENT", "STRONG"),
    ("BOTH_OFFENSES_EFFICIENT", "MODERATE"),
    ("BOTH_DEFENSES_CONTROL", "MODERATE"),
    ("EVEN_MATCHUP", "MODERATE"),
    ("NARROW_EFFICIENCY_GAP", "MODERATE"),
    ("EVEN_MATCHUP", "UNRESOLVED"),
    ("NARROW_EFFICIENCY_GAP", "UNRESOLVED"),
    ("HOME_FINISHING_ADVANTAGE", "MODERATE"),
    ("AWAY_DISRUPTION_ADVANTAGE", "MODERATE"),
    ("HOME_DISRUPTION_ADVANTAGE", "MODERATE"),
    ("AWAY_PASS_EXPLOSIVE_ADVANTAGE", "MODERATE"),
    ("AWAY_DEFENSIVE_CONTROL", "MODERATE"),
    ("HOME_OFFENSE_TURNOVER_PRONE", "MODERATE"),
    ("AWAY_OFFENSE_TURNOVER_PRONE", "MODERATE"),
    ("HIGH_VARIANCE_MATCHUP", "MODERATE"),
]


def _combos(max_size=3):
    import itertools

    for size in range(1, max_size + 1):
        for combo in itertools.combinations(_POOL, size):
            codes = [c for c, _ in combo]
            if len(set(codes)) == len(codes):
                yield [
                    _f(c, "MODERATE", resolved=False) if strength == "UNRESOLVED" else _f(c, strength)
                    for c, strength in combo
                ]


def test_no_margin_band_anywhere_in_the_library_rests_on_scoring_or_pace():
    """The central invariant, swept over every builder and every combination of
    up to three findings: an active margin band always names evidence, the
    evidence is present for the game, and none of it is a scoring or pace
    finding. Scoring/pace-only inputs therefore never produce a margin band."""
    from cfb_edge_finder.scripting.scripts import (
        FORBIDDEN_MARGIN_AUTHORITY,
        band_authority,
        build_scripts,
        candidates,
        margin_authority_violations,
    )

    checked = 0
    for findings in _combos():
        present = {f["code"] for f in findings}
        for cand in candidates(findings, _NAMES, _BASE):
            band = cand.shape["bands"].get("home_margin")
            if band is None:
                continue
            checked += 1
            script = {
                "archetype": cand.archetype,
                "outcome_shape": {
                    **cand.shape,
                    "band_authority": band_authority(cand.shape["bands"]),
                    "margin_authority_evidence": cand.margin_evidence,
                },
                "causal_chain": cand.chain,
            }
            assert margin_authority_violations(script, present, findings) == [], (cand.archetype, sorted(present))
        if present <= FORBIDDEN_MARGIN_AUTHORITY:
            # Scoring and pace findings alone: scripts may exist, margins may not.
            out = build_scripts("E", findings, _NAMES, _BASE, "MEDIUM")
            assert all(s["outcome_shape"]["bands"]["home_margin"] is None for s in out["scripts"]), sorted(present)
        else:
            build_scripts("E", findings, _NAMES, _BASE, "MEDIUM")  # raises on any violation
    assert checked > 500


def test_every_published_script_in_the_synthetic_season_honours_the_contract(season):
    from cfb_edge_finder.scripting.scripts import margin_authority_violations

    for home, away in [(h, a) for h in ("F00", "F02", "F05") for a in ("F01", "F03", "F04", "F06", "F07")]:
        content = build_content(_packet(season, home=home, away=away))
        present = {f["code"] for f in content["matchup_findings"]}
        for script in content["game_scripts"]["scripts"]:
            assert margin_authority_violations(script, present, content["matchup_findings"]) == []
            shape = script["outcome_shape"]
            assert bool(shape["margin_authority_evidence"]) == (shape["bands"]["home_margin"] is not None)


# --- closeness must be resolved by the data, not inferred from a missing edge ---------------------------


@pytest.mark.parametrize("closeness", ["EVEN_MATCHUP", "NARROW_EFFICIENCY_GAP"])
def test_an_unresolved_closeness_finding_grants_no_margin(closeness):
    from cfb_edge_finder.scripting.scripts import _Ctx, _grind, _shootout, closeness_grants_margin

    unresolved = _f(closeness, resolved=False)
    assert not closeness_grants_margin(unresolved)
    assert closeness_grants_margin(_f(closeness))
    for builder, env in ((_shootout, "HIGH_SCORING_ENVIRONMENT"), (_grind, "LOW_SCORING_ENVIRONMENT")):
        cand = builder(_Ctx([_f(env), unresolved], _NAMES, _BASE))
        # The script and its environment stay; the finding stays published; the margin does not.
        assert cand is not None and cand.shape["bands"]["home_margin"] is None
        assert cand.margin_evidence == []
        assert all("one score" not in st["step"] for st in cand.chain)


def test_the_closeness_gate_follows_the_stated_thresholds():
    from cfb_edge_finder.scripting.scripts import closeness_grants_margin

    even = lambda v, u: closeness_grants_margin({"code": "EVEN_MATCHUP", "value": v, "uncertainty": u})  # noqa: E731
    narrow = lambda v, u: closeness_grants_margin(  # noqa: E731
        {"code": "NARROW_EFFICIENCY_GAP", "value": v, "uncertainty": u}
    )
    assert even(0.17, 1.41) and even(0.5, 1.5) and not even(0.6, 1.5) and not even(0.0, 2.01)
    assert narrow(1.5, 1.5) and not narrow(1.49, 1.5)
    assert not closeness_grants_margin({"code": "EVEN_MATCHUP", "value": 0.1, "uncertainty": None})


def test_a_tossup_needs_resolved_even_matchup_and_never_exists_hollow():
    from cfb_edge_finder.scripting.scripts import _Ctx, _tossup

    assert _tossup(_Ctx([_f("EVEN_MATCHUP", resolved=False)], _NAMES, _BASE)) is None
    cand = _tossup(_Ctx([_f("EVEN_MATCHUP")], _NAMES, _BASE))
    assert cand is not None and cand.margin_evidence == ["EVEN_MATCHUP"]


def test_an_unresolved_narrow_gap_cannot_qualify_hangs_around():
    assert _hangs([_EDGE, _f("NARROW_EFFICIENCY_GAP", resolved=False)]) is None
    cand = _hangs(
        [_EDGE, _f("NARROW_EFFICIENCY_GAP", resolved=False), _f("AWAY_DEFENSIVE_CONTROL", "MODERATE", "MATCHUP")]
    )
    assert cand is not None and "NARROW_EFFICIENCY_GAP" not in cand.margin_evidence


def test_the_validator_rejects_unresolved_closeness_evidence():
    from cfb_edge_finder.scripting.scripts import ARCHETYPE_DEFINITION, margin_authority_violations

    script = {
        "archetype": "COMPETITIVE_TOSSUP",
        "outcome_shape": {
            "bands": {"home_margin": [-8, 8]},
            "band_authority": {"home_margin": ARCHETYPE_DEFINITION},
            "margin_authority_evidence": ["EVEN_MATCHUP"],
        },
        "causal_chain": [{"step": "inside one score", "findings": ["EVEN_MATCHUP"]}],
    }
    assert margin_authority_violations(script, {"EVEN_MATCHUP"}, [_f("EVEN_MATCHUP")]) == []
    assert margin_authority_violations(script, {"EVEN_MATCHUP"}, [_f("EVEN_MATCHUP", resolved=False)])
