"""Script Engine V2 (shadow) migration tests: docs/SCRIPT_ENGINE_V2_MIGRATION.md section 17.

CONTROL, CLOSENESS, PACE, DEFENSIVE_SUPPRESSION, DISRUPTION, the V1 retirements,
EXPLOSIVE_UPSET, scoring authority, confidence semantics, the calibration
artifact, the V2 ledger stream, the payload, and equivalence with the Wave 2
research definition on every committed historical record.
"""

from __future__ import annotations

import copy
import gzip
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from cfb_edge_finder.archetype_research.v2.claims import v2_claims
from cfb_edge_finder.execution.card_review import WinSet
from cfb_edge_finder.scripting import (
    CLAIMS_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    LEDGER_V2_SCHEMA_VERSION,
    METHODOLOGY_V2_VERSION,
    METHODOLOGY_VERSION,
)
from cfb_edge_finder.scripting import calibration as cal
from cfb_edge_finder.scripting.claims import (
    CLAIMS_KEYS,
    NO_CLAIM_STATEMENT,
    NO_SUPPORTED_CLAIM,
    RETIRED_V1,
    build_claims,
    derive_claims,
    freeze_claims,
    story,
    verify_claims,
)
from cfb_edge_finder.scripting.claims_market import (
    ALIGNED,
    DIRECT,
    NO_AUTHORITY,
    OPPOSED,
    POLICY,
    RESEARCH_CONTEXT,
    RESEARCH_UNCALIBRATED,
    classify_expression,
    map_claims,
)
from cfb_edge_finder.scripting.ledger import LedgerOrderError, read_rows, read_rows_v2, row_for_v2
from cfb_edge_finder.scripting.market_map import MappingOrderError
from cfb_edge_finder.scripting.publish import sift_payload
from cfb_edge_finder.scripting.scripts import build_scripts

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import script_engine  # noqa: E402

NAMES = {"home": "Home U", "away": "Away St"}
BASE = {"home_points": 31.0, "away_points": 27.0, "total_points": 58.0}
CALIBRATION = cal.load_calibration(REPO)
RESEARCH_PARAMETERS = json.loads((REPO / cal.SOURCE_PARAMETERS_PATH).read_text())
HOLDOUT_REPORT = json.loads((REPO / cal.SOURCE_HOLDOUT_REPORT_PATH).read_text())

#: closeness findings carry the efficiency gap and its uncertainty: (value, uncertainty)
RESOLVED = {"EVEN_MATCHUP": (0.2, 1.0), "NARROW_EFFICIENCY_GAP": (1.5, 1.0)}
UNRESOLVED = {"EVEN_MATCHUP": (0.2, 1.9), "NARROW_EFFICIENCY_GAP": (1.2, 1.5)}


def f(code: str, strength: str = "MODERATE", category: str = "MATCHUP", resolved: bool = True) -> dict:
    out = {"code": code, "strength": strength, "category": category, "value": 1.5, "uncertainty": 0.4}
    if code in RESOLVED:
        out["value"], out["uncertainty"] = (RESOLVED if resolved else UNRESOLVED)[code]
        out["category"] = "ENVIRONMENT"
    return out


def claims_of(*findings: dict, v1_scripts: list | None = None) -> dict:
    return derive_claims(list(findings), NAMES, CALIBRATION, v1_scripts)[0]


def _walk_keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{path}/{k}", k, v
            yield from _walk_keys(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, f"{path}[{i}]")


# ------------------------------------------------------------------------------------------------ CONTROL


@pytest.mark.parametrize("side", ["home", "away"])
@pytest.mark.parametrize("strength", ["MODERATE", "STRONG"])
def test_control_carries_side_strength_and_its_exact_frozen_range(side, strength):
    c = claims_of(f(f"{side.upper()}_SUSTAINED_EFFICIENCY_ADVANTAGE", strength))["control"]
    tier = f"{side.upper()}_CONTROL_{strength}"
    assert (c["family"], c["side"], c["strength"], c["tier"]) == ("CONTROL", side, strength, tier)
    rng = c["historical_range"]
    frozen = RESEARCH_PARAMETERS["control"][tier]
    assert rng["n"] == frozen["distribution"]["n"]
    assert rng["win_rate"] == frozen["distribution"]["win_rate"]
    assert rng["median"] == frozen["distribution"]["p50"]
    assert rng["central_50"] == frozen["intervals"]["central_50"]
    assert rng["central_80"] == frozen["intervals"]["central_80"]
    assert rng["development_seasons"] == [2021, 2022, 2023, 2024, 2025]
    assert rng["validation"]["seasons"] == [2014, 2015, 2016, 2017, 2018, 2019, 2020]
    assert rng["calibration_sha256"] == cal.CONTROL_CALIBRATION_SHA256
    assert "Historical empirical range" in rng["label"] and "Not a prediction interval" in rng["not"]


def test_the_promoted_values_are_the_wave2_values_exactly():
    expected = {
        "HOME_CONTROL_MODERATE": (404, 0.7995, 12.0, [3.0, 24.0], [-7.0, 34.0], 512, 0.5254, 0.7852),
        "HOME_CONTROL_STRONG": (628, 0.9013, 23.0, [8.0, 35.0], [1.0, 48.0], 824, 0.5291, 0.8216),
        "AWAY_CONTROL_MODERATE": (404, 0.6658, 7.0, [-4.0, 18.0], [-13.7, 30.0], 514, 0.5370, 0.7840),
        "AWAY_CONTROL_STRONG": (378, 0.8095, 14.5, [3.0, 27.0], [-5.3, 37.0], 552, 0.5236, 0.7645),
    }
    for tier, (n, win, med, c50, c80, vn, cov50, cov80) in expected.items():
        t, v = CALIBRATION["tiers"][tier], CALIBRATION["validation"]["tiers"][tier]
        assert (t["n"], t["win_rate"]["rate"], t["median"], t["central_50"], t["central_80"]) == (n, win, med, c50, c80)
        assert (v["n"], v["coverage_50"]["rate"], v["coverage_80"]["rate"], v["within_tolerance"]) == (
            vn,
            cov50,
            cov80,
            True,
        )


def test_the_calibration_artifact_hash_is_exact_and_promotion_is_reproducible():
    assert (
        RESEARCH_PARAMETERS["sha256"]
        == cal.SOURCE_PARAMETERS_SHA256
        == ("683d075d99efdfed16f7fb35a9fd58234808d1a354f8fb8e3352c64e972bf7aa")
    )
    assert cal.content_sha256(RESEARCH_PARAMETERS) == RESEARCH_PARAMETERS["sha256"]
    assert CALIBRATION["sha256"] == cal.CONTROL_CALIBRATION_SHA256
    assert cal.content_sha256(CALIBRATION) == CALIBRATION["sha256"]
    promoted = cal.promote(RESEARCH_PARAMETERS, HOLDOUT_REPORT)
    assert promoted == CALIBRATION
    # byte stability of the committed file
    committed = (REPO / cal.CALIBRATION_PATH).read_text(encoding="utf-8")
    assert committed == json.dumps(promoted, indent=1, sort_keys=True) + "\n"


def test_no_contract_level_threshold_rate_is_promoted():
    blob = json.dumps(CALIBRATION)
    assert "p_margin_ge" not in blob.replace("p_margin_ge_<k>", "") and 'p_margin_gt_0"' not in blob


def test_an_altered_calibration_is_refused():
    tampered = copy.deepcopy(CALIBRATION)
    tampered["tiers"]["HOME_CONTROL_STRONG"]["central_80"] = [7.0, 24.0]
    with pytest.raises(cal.CalibrationMismatch):
        cal.verify_calibration(tampered)
    tampered["sha256"] = cal.content_sha256(tampered)  # self-consistent, but not the pinned artifact
    with pytest.raises(cal.CalibrationMismatch):
        cal.verify_calibration(tampered)
    bad_source = copy.deepcopy(RESEARCH_PARAMETERS)
    bad_source["control"]["HOME_CONTROL_STRONG"]["intervals"]["central_50"] = [9.0, 35.0]
    with pytest.raises(cal.CalibrationMismatch):
        cal.promote(bad_source, HOLDOUT_REPORT)


def test_pulls_away_is_not_duplicated_strong_control_is_one_claim():
    findings = [f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "STRONG"), f("HOME_FINISHING_ADVANTAGE")]
    v1 = build_scripts("E", findings, NAMES, BASE, "HIGH")
    assert {s["archetype"] for s in v1["scripts"]} >= {"HOME_CONTROL", "FAVORITE_PULLS_AWAY"}  # V1 says it twice
    c = claims_of(*findings)
    assert c["control"]["tier"] == "HOME_CONTROL_STRONG"
    assert "HOME_FINISHING_ADVANTAGE" in c["control"]["context"]["same_side"]
    claims_blob = json.dumps(c)
    assert "PULLS" not in claims_blob and "17, 45" not in claims_blob


def test_control_is_conditional_on_the_finding_alone():
    plain = claims_of(f("AWAY_SUSTAINED_EFFICIENCY_ADVANTAGE"))["control"]
    contradicted = claims_of(
        f("AWAY_SUSTAINED_EFFICIENCY_ADVANTAGE"), f("HOME_DISRUPTION_ADVANTAGE"), f("HIGH_VARIANCE_MATCHUP")
    )["control"]
    assert plain["historical_range"] == contradicted["historical_range"]
    assert contradicted["context"]["opposing"] == ["HOME_DISRUPTION_ADVANTAGE"]


# ------------------------------------------------------------------------------------------------ CLOSENESS


def test_resolved_even_matchup_activates_closeness_without_a_winner():
    c = claims_of(f("EVEN_MATCHUP"))["closeness"]
    assert c["statement"] == "The evidence supports a relatively close game."
    assert c["claims_winner"] is False and "side" not in c and "historical_range" not in c
    assert not any(k in c for k in ("winner_lean", "bands", "home_margin", "margin_band"))


def test_narrow_gap_alone_does_not_activate_closeness():
    claims, abstentions = derive_claims([f("NARROW_EFFICIENCY_GAP")], NAMES, CALIBRATION)
    assert claims["closeness"] is None
    assert any("NARROW_EFFICIENCY_GAP" in a["reason"] for a in abstentions)


def test_unresolved_even_matchup_does_not_activate_closeness():
    claims, abstentions = derive_claims([f("EVEN_MATCHUP", resolved=False)], NAMES, CALIBRATION)
    assert claims["closeness"] is None
    assert any(a["family"] == "CLOSENESS" and "does not resolve" in a["reason"] for a in abstentions)


def test_no_control_does_not_mean_closeness():
    assert claims_of(f("LOW_POSSESSION_ENVIRONMENT", category="ENVIRONMENT"))["closeness"] is None
    assert claims_of()["closeness"] is None


# ------------------------------------------------------------------------------------------------ PACE


@pytest.mark.parametrize("code,level", [("HIGH_POSSESSION_ENVIRONMENT", "HIGH"), ("LOW_POSSESSION_ENVIRONMENT", "LOW")])
def test_pace_is_an_independent_claim(code, level):
    alone = claims_of(f(code, category="ENVIRONMENT"))
    assert alone["pace"]["level"] == level and alone["pace"]["family"] == "PACE"
    assert alone["scoring_environment"] is None and alone["control"] is None and alone["closeness"] is None
    beside = claims_of(f(code, category="ENVIRONMENT"), f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "STRONG"))
    assert beside["pace"]["level"] == level and beside["control"]["tier"] == "HOME_CONTROL_STRONG"
    assert "OVER" not in json.dumps(alone["pace"]).upper().replace("OVERALL", "")


# ------------------------------------------------------------------------------------------------ DEFENSIVE SUPPRESSION


def test_defensive_suppression_surfaces_where_v1_grind_swallowed_it():
    findings = [
        f("BOTH_DEFENSES_CONTROL", category="ENVIRONMENT"),
        f("LOW_SCORING_ENVIRONMENT", category="ENVIRONMENT"),
    ]
    v1 = {s["archetype"] for s in build_scripts("E", findings, NAMES, BASE, "HIGH")["scripts"]}
    assert "COMPETITIVE_GRIND" in v1 and "DEFENSIVE_SUPPRESSION" not in v1  # the V1 exclusivity
    c = claims_of(*findings)
    assert c["defensive_suppression"]["evidence"] == ["BOTH_DEFENSES_CONTROL"]
    assert c["defensive_suppression"]["numeric_band"] is None
    assert c["scoring_environment"]["level"] == "SUPPRESSED"


# ------------------------------------------------------------------------------------------------ DISRUPTION


def test_disruption_surfaces_without_volatility_and_has_no_winner_authority():
    findings = [f("AWAY_DISRUPTION_ADVANTAGE")]
    v1 = {s["archetype"] for s in build_scripts("E", findings, NAMES, BASE, "HIGH")["scripts"]}
    assert "TURNOVER_DISRUPTION" not in v1  # V1 needed volatility
    c = claims_of(*findings)
    (d,) = c["disruption"]
    assert (d["side"], d["winner_authority"], d["volatility_context"], d["aligned_with_control"]) == (
        "away",
        False,
        [],
        None,
    )
    assert c["control"] is None
    ml = {"expression_id": "ML:YES", "kind": "moneyline", "unmappable_reason": None}
    ml["wins_when"] = {**WinSet("full_game:home_margin", float("-inf"), -1).as_dict()}
    assert classify_expression(ml, c)["authority"] == NO_AUTHORITY
    assert POLICY["moneyline"]["DISRUPTION_EDGE"] == NO_AUTHORITY


def test_disruption_aligned_with_control_is_marked_but_still_not_a_winner():
    c = claims_of(f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE"), f("HOME_DISRUPTION_ADVANTAGE"))
    assert c["disruption"][0]["aligned_with_control"] is True and c["disruption"][0]["winner_authority"] is False


# ------------------------------------------------------------------------------------------------ RETIREMENTS

RETIRED_NAMES = (
    "FAVORITE_PULLS_AWAY",
    "UNDERDOG_HANGS_AROUND",
    "COMPETITIVE_TOSSUP",
    "COMPETITIVE_SHOOTOUT",
    "COMPETITIVE_GRIND",
    "PACE_DRIVEN_OVER",
    "TURNOVER_DISRUPTION",
)

V1_TRIGGERS = [
    [f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "STRONG"), f("HOME_RUSH_ADVANTAGE")],  # PULLS_AWAY
    [f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE"), f("AWAY_DEFENSIVE_CONTROL")],  # HANGS_AROUND
    [f("EVEN_MATCHUP")],  # TOSSUP
    [f("HIGH_SCORING_ENVIRONMENT", category="ENVIRONMENT"), f("EVEN_MATCHUP")],  # SHOOTOUT
    [f("LOW_SCORING_ENVIRONMENT", category="ENVIRONMENT")],  # GRIND
    [f("HIGH_POSSESSION_ENVIRONMENT", category="ENVIRONMENT")],  # PACE_DRIVEN_OVER
    [f("HOME_DISRUPTION_ADVANTAGE"), f("AWAY_OFFENSE_TURNOVER_PRONE", category="DEPENDENCE")],  # TURNOVER_DISRUPTION
]


@pytest.mark.parametrize("findings", V1_TRIGGERS)
def test_retired_v1_archetypes_are_never_v2_claims(findings):
    v1 = {s["archetype"] for s in build_scripts("E", findings, NAMES, BASE, "HIGH")["scripts"]}
    assert v1 & set(RETIRED_NAMES), "each trigger must produce a retired V1 script to be a real test"
    claims = claims_of(*findings)
    blob = json.dumps(claims)
    for name in RETIRED_NAMES:
        assert name not in blob
    assert set(claims) == {
        "control",
        "closeness",
        "pace",
        "scoring_environment",
        "defensive_suppression",
        "disruption",
        "explosive_upset",
    }


def test_hangs_around_and_tossup_findings_produce_only_v2_claims():
    hangs = claims_of(f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE"), f("AWAY_DEFENSIVE_CONTROL"))
    assert hangs["control"]["side"] == "home" and hangs["closeness"] is None  # no "underdog stays close" claim
    tossup = claims_of(f("EVEN_MATCHUP"))
    assert tossup["closeness"] is not None and tossup["control"] is None
    assert all(RETIRED_V1[name] for name in RETIRED_NAMES)


# ------------------------------------------------------------------------------------------------ EXPLOSIVE_UPSET


def test_explosive_upset_is_carried_verbatim_and_unchanged():
    findings = [f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE"), f("AWAY_PASS_EXPLOSIVE_ADVANTAGE")]
    v1 = build_scripts("E", findings, NAMES, BASE, "HIGH")["scripts"]
    upset = [s for s in v1 if s["archetype"] == "EXPLOSIVE_UPSET"]
    assert upset, "the fixture must produce the V1 EXPLOSIVE_UPSET script"
    # V1 behaviour itself is unchanged: its band and authority are the V1 definition.
    assert upset[0]["outcome_shape"]["bands"]["home_margin"] == [-14, -1]
    assert upset[0]["outcome_shape"]["band_authority"]["home_margin"] == "ARCHETYPE_DEFINITION"
    c = claims_of(*findings, v1_scripts=v1)["explosive_upset"]
    assert c["status"] == "UNTESTED_HISTORICALLY_DATA_UNAVAILABLE"
    assert c["scripts"] == upset
    assert claims_of(*findings, v1_scripts=[])["explosive_upset"]["scripts"] == []


# ------------------------------------------------------------------------------------------------ SCORING


ALL_FINDINGS = [
    f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "STRONG"),
    f("HIGH_SCORING_ENVIRONMENT", "STRONG", "ENVIRONMENT"),
    f("BOTH_OFFENSES_EFFICIENT", category="ENVIRONMENT"),
    f("HIGH_POSSESSION_ENVIRONMENT", category="ENVIRONMENT"),
    f("BOTH_DEFENSES_CONTROL", category="ENVIRONMENT"),
    f("HOME_DISRUPTION_ADVANTAGE"),
]


def test_no_numeric_scoring_band_exists_in_v2_claims():
    claims = claims_of(*ALL_FINDINGS)
    for path, key, value in _walk_keys(claims):
        assert key not in ("total_points", "home_points", "away_points", "bands"), path
        if key == "numeric_band":
            assert value is None, path
    assert claims["scoring_environment"]["incremental_over_baseline"] is True
    suppressed = claims_of(f("LOW_SCORING_ENVIRONMENT", category="ENVIRONMENT"))["scoring_environment"]
    assert suppressed["incremental_over_baseline"] is False and "already expects" in suppressed["statement"]


def _expr(kind: str, variable: str, low: float, high: float, eid: str = "X:YES") -> dict:
    return {
        "expression_id": eid,
        "kind": kind,
        "unmappable_reason": None,
        "wins_when": WinSet(variable, low, high).as_dict(),
    }


INF = float("inf")


def test_totals_and_team_totals_stay_research_uncalibrated_and_environments_never_touch_margins():
    env_only = claims_of(*ALL_FINDINGS[1:5])
    assert env_only["control"] is None
    for kind, var in (("total", "full_game:total_points"), ("team_total", "full_game:home_points")):
        row = classify_expression(_expr(kind, var, 60, INF), env_only)
        assert row["authority"] == RESEARCH_UNCALIBRATED and row["relations"] == {}
    for kind, low, high in (("moneyline", 1, INF), ("spread", 8, INF), ("spread", -INF, 7)):
        row = classify_expression(_expr(kind, "full_game:home_margin", low, high), env_only)
        assert row["authority"] == NO_AUTHORITY and row["relations"] == {}


def test_control_has_directional_moneyline_authority_and_only_context_on_spreads():
    claims = claims_of(f("AWAY_SUSTAINED_EFFICIENCY_ADVANTAGE", "STRONG"))
    away_ml = classify_expression(_expr("moneyline", "full_game:home_margin", -INF, -1), claims)
    home_ml = classify_expression(_expr("moneyline", "full_game:home_margin", 1, INF), claims)
    assert (away_ml["authority"], away_ml["relations"]) == (DIRECT, {"CONTROL": ALIGNED})
    assert (home_ml["authority"], home_ml["relations"]) == (DIRECT, {"CONTROL": OPPOSED})
    spread = classify_expression(_expr("spread", "full_game:home_margin", -INF, -15), claims)
    assert spread["authority"] == RESEARCH_CONTEXT and spread["relations"] == {}
    total = classify_expression(_expr("total", "full_game:total_points", 50, INF), claims)
    assert total["authority"] == RESEARCH_UNCALIBRATED


# ------------------------------------------------------------------------------------------------ CONFIDENCE


def test_confidence_is_data_quality_and_never_changes_a_claim(built):
    envelope = built["football"]["26OCT10UGAALA"]
    base = build_claims(envelope, CALIBRATION)
    for level in ("HIGH", "MEDIUM", "LOW"):
        tweaked = copy.deepcopy(envelope)
        tweaked["content"]["confidence"]["level"] = level
        other = build_claims(tweaked, CALIBRATION)
        assert other["claims"] == base["claims"] and other["story"] == base["story"]
        assert other["data_quality"]["level"] == level
    assert "never how likely" in base["data_quality"]["describes"]
    assert base["outcome_strength_source"] == "CONTROL.strength"


# ------------------------------------------------------------------------------------------------ STORY / NO CLAIM

BANNED = re.compile(r"%|\bchance\b|\bprobab|\bpredict|\bodds\b|\bover\b|\bunder\b|\+?EV\b|\blikely\b", re.I)


def test_story_examples_are_deterministic_and_cite_their_claims():
    a = claims_of(
        f("HOME_SUSTAINED_EFFICIENCY_ADVANTAGE", "STRONG"),
        f("HIGH_POSSESSION_ENVIRONMENT", category="ENVIRONMENT"),
        f("HIGH_SCORING_ENVIRONMENT", category="ENVIRONMENT"),
    )
    s = story(a, NAMES)
    assert s["headline"] == (
        "Home U holds a strong sustained-efficiency control edge, with a faster possession environment and an "
        "elevated-scoring environment."
    )
    assert s["clauses"][0]["claims"] == ["control", "pace", "scoring_environment"]
    b = claims_of(
        f("EVEN_MATCHUP"),
        f("BOTH_DEFENSES_CONTROL", category="ENVIRONMENT"),
        f("LOW_POSSESSION_ENVIRONMENT", category="ENVIRONMENT"),
    )
    assert story(b, NAMES)["headline"] == (
        "Close-game profile, with a slower possession environment and defensive suppression."
    )
    assert story(b, NAMES) == story(copy.deepcopy(b), NAMES)


def test_story_never_invents_a_winner_or_probability_language():
    for findings in [*V1_TRIGGERS, ALL_FINDINGS, [f("AWAY_DISRUPTION_ADVANTAGE")]]:
        claims = claims_of(*findings)
        s = story(claims, NAMES)
        assert not BANNED.search(s["headline"]), s["headline"]
        for clause in s["clauses"]:
            assert clause["claims"] or clause["text"] == NO_CLAIM_STATEMENT
        if claims["control"] is None:
            assert "control edge" not in s["headline"]
        statements = [v for _, k, v in _walk_keys(claims) if k == "statement"]
        for text in statements:
            assert not BANNED.search(text), text


def test_no_claim_semantics():
    claims, _ = derive_claims([f("HOME_THIN_SAMPLE", category="DATA")], NAMES, CALIBRATION)
    s = story(claims, NAMES)
    assert s["headline"] == "No supported matchup claim cleared the evidence requirements."
    assert "uncertain" not in s["headline"] and "random" not in s["headline"]


# ------------------------------------------------------------------------------------- equivalence with research


def _records():
    for path in sorted((REPO / "data/scripting/validation/archetype_rows").glob("*.jsonl.gz")) + sorted(
        (REPO / "data/scripting/validation/archetype_v2_holdout_rows").glob("*.jsonl.gz")
    ):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if "pregame" in row:
                    yield row


def test_production_claims_equal_the_wave2_research_definition_on_every_historical_record():
    n = 0
    for row in _records():
        pre = row["pregame"]
        research = row.get("v2") or v2_claims(pre)
        prod = claims_of(*pre["findings"])
        ctl = prod["control"]
        assert (ctl["tier"] if ctl else None) == ((research["control"] or {}).get("claim")), pre["game_id"]
        assert bool(prod["closeness"]) == research["closeness"]["CLOSENESS_EVEN"], pre["game_id"]
        assert (("PACE_" + prod["pace"]["level"]) if prod["pace"] else None) == (research["pace"] or {}).get("claim")
        sc, rs = prod["scoring_environment"], research["scoring_environment"] or {}
        assert ((sc["level"] + "_SCORING_ENVIRONMENT", sc["strengthened"]) if sc else None) == (
            (rs["claim"], rs["strengthened"]) if rs.get("claim") else None
        )
        assert bool(prod["defensive_suppression"]) == bool(research["defensive_suppression"])
        assert sorted((d["side"], d["strength"]) for d in prod["disruption"]) == sorted(
            (d["side"], d["strength"]) for d in research["disruption"]
        )
        n += 1
    assert n > 10_000  # 4,546 development + 5,823 validation records


# ------------------------------------------------------------------------------------------------ end-to-end build

FIXTURE = REPO / "tests" / "fixtures" / "script_engine_export"
INPUTS = REPO / "data" / "scripting" / "validation" / "inputs"
GAMES = ("26OCT10UGAALA", "26OCT10LSUUK", "26OCT10TEXOKLA")
AS_OF = "2026-10-07T23:19:34Z"
AFTER_KICKOFF = "2026-10-11T12:00:00Z"


def _inputs(root: Path) -> tuple[Path, Path]:
    catalog = root / "catalog"
    (catalog / "games").mkdir(parents=True)
    for src in [FIXTURE / "cfb_market_catalog.json.gz", *sorted((FIXTURE / "games").glob("*.json.gz"))]:
        with gzip.open(src, "rb") as fh, open(catalog / src.relative_to(FIXTURE).with_suffix(""), "wb") as out:
            shutil.copyfileobj(fh, out)
    football = root / "football"
    football.mkdir()
    for src, name in (
        ("espn_2026_team_games.jsonl.gz", "team_games.jsonl"),
        ("espn_2026_schedule.json.gz", "schedule.json"),
    ):
        with gzip.open(INPUTS / src, "rb") as fh, open(football / name, "wb") as out:
            shutil.copyfileobj(fh, out)
    return football, catalog


def _build(football: Path, catalog: Path, out: Path, ledger: Path, as_of: str) -> int:
    args = ["build", "--football-dir", str(football), "--catalog-dir", str(catalog), "--out-dir", str(out)]
    args += ["--ledger-dir", str(ledger), "--season", "2026", "--as-of", as_of]
    return script_engine.main(args + [a for g in GAMES for a in ("--game", g)])


def _gz(path: Path):
    with gzip.open(path, "rb") as fh:
        return json.loads(fh.read())


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("v2build")
    football, catalog = _inputs(root)
    assert _build(football, catalog, root / "live", root / "ledger", AS_OF) == 0
    return {
        "root": root,
        "football_dir": football,
        "catalog": catalog,
        "football": {g: _gz(root / "live" / "frozen" / f"{g}.json.gz") for g in GAMES},
        "claims": {g: _gz(root / "live" / "frozen_v2" / f"{g}.json.gz") for g in GAMES},
        "sift": {g: _gz(root / "live" / "sift" / f"{g}.json.gz") for g in GAMES},
    }


def test_the_build_freezes_v2_claims_from_the_frozen_v1_artifact(built):
    for g in GAMES:
        env, v1 = built["claims"][g], built["football"][g]
        assert verify_claims(env)
        content = env["content"]
        assert tuple(content) == tuple(sorted(CLAIMS_KEYS)) or set(content) == set(CLAIMS_KEYS)
        assert content["schema_version"] == CLAIMS_SCHEMA_VERSION
        assert content["methodology_version"] == METHODOLOGY_V2_VERSION
        assert content["activation"] == "SHADOW" and content["probability"] is None
        assert content["source"]["football_artifact_hash"] == v1["artifact_hash"]
        assert v1["content"]["methodology_version"] == METHODOLOGY_VERSION  # V1 does not masquerade as V2
        assert content["calibration"]["sha256"] == cal.CONTROL_CALIBRATION_SHA256
        assert content == build_claims(v1, CALIBRATION)


def test_v2_control_direction_matches_the_v1_control_scripts(built):
    checked = 0
    for g in GAMES:
        scripts = built["football"][g]["content"]["game_scripts"]["scripts"]
        control = built["claims"][g]["content"]["claims"]["control"]
        for s in scripts:
            if s["archetype"] in ("HOME_CONTROL", "AWAY_CONTROL", "FAVORITE_PULLS_AWAY"):
                assert control is not None and s["outcome_shape"]["winner_lean"].lower() == control["side"]
                checked += 1
    assert checked > 0


def test_the_payload_is_additive_and_carries_claims_v2(built):
    for g in GAMES:
        payload = built["sift"][g]
        assert payload["version"] == "cfb_script_engine_payload/1.2.0"
        v2 = payload["claims_v2"]
        assert v2["claims_artifact_hash"] == built["claims"][g]["artifact_hash"]
        assert v2["activation"] == "SHADOW" and "retired_v1" not in v2
        assert v2["market_authority"]["schema_version"] == "cfb_claims_market_authority/1.0.0"
        record = {
            "football": built["football"][g],
            "market_map": None,
            "prices_captured_at": None,
        }
        without = sift_payload(record)
        assert without["claims_v2"] is None
        assert set(payload) - {"claims_v2"} == set(without) - {"claims_v2"}
        assert len(json.dumps(v2, separators=(",", ":"))) < 6_000


def test_v1_ledger_rows_are_unchanged_and_v2_rows_carry_their_own_methodology(built, tmp_path, monkeypatch):
    ledger = built["root"] / "ledger"
    v1_rows, v2_rows = read_rows(ledger, 2026), read_rows_v2(ledger, 2026)
    assert v1_rows and v2_rows
    assert all(r["schema_version"] == LEDGER_SCHEMA_VERSION and "methodology_version" not in r for r in v1_rows)
    assert all(
        r["schema_version"] == LEDGER_V2_SCHEMA_VERSION
        and r["methodology_version"] == METHODOLOGY_V2_VERSION
        and r["activation"] == "SHADOW"
        for r in v2_rows
    )

    # The V1 stream is byte-identical to a build in which V2 is disabled entirely.
    def broken(*_a, **_k):
        raise cal.CalibrationMismatch("disabled for the test")

    monkeypatch.setattr(script_engine, "load_calibration", broken)
    assert _build(built["football_dir"], built["catalog"], tmp_path / "live", tmp_path / "ledger", AS_OF) == 0
    assert (tmp_path / "ledger" / "2026" / "publications.jsonl").read_bytes() == (
        ledger / "2026" / "publications.jsonl"
    ).read_bytes()
    assert not (tmp_path / "ledger" / "2026" / "publications_v2.jsonl").exists()
    for g in GAMES:
        assert _gz(tmp_path / "live" / "frozen" / f"{g}.json.gz") == built["football"][g]
        assert _gz(tmp_path / "live" / "sift" / f"{g}.json.gz")["claims_v2"] is None
    summary = json.loads((tmp_path / "live" / "index.json").read_text())
    assert summary["v2"]["status"] == "DISABLED"


def test_no_v2_backfill_after_kickoff(built, tmp_path):
    env = built["claims"]["26OCT10UGAALA"]
    with pytest.raises(LedgerOrderError):
        row_for_v2(
            game_key="26OCT10UGAALA",
            season=2026,
            claims_envelope=env,
            authority_map=None,
            market_map=None,
            checkpoint="FINAL_PREGAME",
            recorded_at=AFTER_KICKOFF,
            prices_captured_at=None,
        )
    # A fresh build after kickoff writes no V2 artifact and no V2 row.
    assert _build(built["football_dir"], built["catalog"], tmp_path / "live", tmp_path / "ledger", AFTER_KICKOFF) == 0
    assert not list((tmp_path / "live").glob("frozen_v2/*.json.gz"))
    assert read_rows_v2(tmp_path / "ledger", 2026) == []


def test_the_v2_market_map_refuses_tampered_or_mismatched_inputs(built):
    env = copy.deepcopy(built["claims"]["26OCT10UGAALA"])
    mm = {"artifact_hash": env["content"]["source"]["football_artifact_hash"], "expressions": []}
    assert map_claims(env, mm, mapped_at=AS_OF)["counts"][DIRECT] == 0
    with pytest.raises(MappingOrderError):
        map_claims(env, {"artifact_hash": "0" * 64, "expressions": []}, mapped_at=AS_OF)
    env["content"]["story"]["headline"] = "Edited after the freeze."
    with pytest.raises(MappingOrderError):
        map_claims(env, mm, mapped_at=AS_OF)


def test_an_unchanged_picture_keeps_its_claims_hash_and_first_timestamp(built):
    v1 = built["football"]["26OCT10UGAALA"]
    first = built["claims"]["26OCT10UGAALA"]
    again = freeze_claims(build_claims(v1, CALIBRATION), generated_at="2026-10-09T00:00:00Z", previous_envelope=first)
    assert again is first


def test_no_supported_claim_status_uses_the_v2_statement(built):
    v1 = copy.deepcopy(built["football"]["26OCT10UGAALA"])
    v1["content"]["matchup_findings"] = []
    v1["content"]["game_scripts"]["scripts"] = []
    content = build_claims(v1, CALIBRATION)
    assert content["status"] == NO_SUPPORTED_CLAIM and content["status_statement"] == NO_CLAIM_STATEMENT
