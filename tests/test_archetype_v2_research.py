"""Script Engine V2 candidate: isolation from production, sealed-holdout gate, frozen parameters, leakage."""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
from pathlib import Path

import pytest
from script_engine_fakes import synthetic_season

from cfb_edge_finder.archetype_research.replay import schedule_targets
from cfb_edge_finder.archetype_research.study import run_targets
from cfb_edge_finder.archetype_research.v2 import EXPLOSIVE_UPSET_STATUS, HOLDOUT_SEASONS
from cfb_edge_finder.archetype_research.v2 import criteria as K
from cfb_edge_finder.archetype_research.v2.claims import claims_hash, v2_claims
from cfb_edge_finder.archetype_research.v2.evaluate import criteria, evaluate
from cfb_edge_finder.archetype_research.v2.fit import (
    FrozenParametersMismatch,
    SealedSeasonError,
    attach_claims,
    fit_development,
    load_frozen,
    write_frozen,
)
from cfb_edge_finder.archetype_research.v2.holdout import (
    HoldoutGateError,
    check_gate,
    manifest_of,
    predict_season,
    reveal_and_score,
)
from cfb_edge_finder.scripting import findings as production_findings
from cfb_edge_finder.scripting import realized as production_realized
from cfb_edge_finder.scripting import scripts as production_scripts
from cfb_edge_finder.scripting.gamelog import FINAL_AFTER, parse_utc

REPO = Path(__file__).resolve().parents[1]
V2_DIR = REPO / "src" / "cfb_edge_finder" / "archetype_research" / "v2"
SCRIPTING_DIR = REPO / "src" / "cfb_edge_finder" / "scripting"


def _as_season(rows, year):
    return [dataclasses.replace(r, season=year, kickoff_utc=r.kickoff_utc.replace("2026-", f"{year}-")) for r in rows]


def _games(rows):
    out = {}
    for r in rows:
        g = out.setdefault(
            r.game_id,
            {
                "id": r.game_id,
                "season": r.season,
                "week": r.week,
                "seasonType": "regular",
                "startDate": r.kickoff_utc,
                "completed": True,
                "neutralSite": False,
                "conferenceGame": None,
            },
        )
        g["homeId" if r.site == "home" else "awayId"] = r.team_id
    return list(out.values())


def _season_data(rows):
    return {"rows": rows, "games": _games(rows), "digests": {"synthetic": "0"}}


@pytest.fixture(scope="module")
def dev_records():
    rows = _as_season(synthetic_season(seed=11), 2023)
    targets, _ = schedule_targets(_games(rows), rows)
    return attach_claims(run_targets(rows, targets))


@pytest.fixture(scope="module")
def holdout_rows():
    return _as_season(synthetic_season(seed=23), 2016)


@pytest.fixture(scope="module")
def frozen_files(tmp_path_factory, dev_records):
    root = tmp_path_factory.mktemp("v2")
    params = fit_development(dev_records)
    frozen_path = root / "frozen.json"
    write_frozen(params, frozen_path)
    protocol = root / "PROTOCOL.md"
    protocol.write_text(f"PRE-REGISTERED\nfrozen parameters sha256: {params['sha256']}\n")
    return protocol, frozen_path, params


@pytest.fixture(scope="module")
def predicted(holdout_rows):
    return predict_season(_season_data(holdout_rows))


def _production_rules():
    return copy.deepcopy(
        {
            "findings": {k: getattr(production_findings, k) for k in dir(production_findings) if k.isupper()},
            "scripts": {
                k: getattr(production_scripts, k)
                for k in dir(production_scripts)
                if k.isupper() and not callable(getattr(production_scripts, k))
            },
            "realized": production_realized.ONE_SCORE,
        }
    )


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


# ------------------------------------------------------------------ isolation


def test_v2_cannot_alter_production_rules(dev_records, frozen_files):
    before = _production_rules()
    _, _, params = frozen_files
    ev = evaluate(dev_records, params)
    criteria(ev)
    assert _production_rules() == before


def test_production_never_imports_the_v2_candidate():
    for path in SCRIPTING_DIR.glob("*.py"):
        assert not any("archetype_research" in name for name in _imports(path)), path.name


def test_v2_imports_production_football_primitives_but_nothing_market_side():
    forbidden = (
        "cfb_edge_finder.scripting.market_map",
        "cfb_edge_finder.scripting.expressions",
        "cfb_edge_finder.scripting.ledger",
        "cfb_edge_finder.scripting.publish",
        "cfb_edge_finder.catalog",
        "cfb_edge_finder.kalshi",
        "cfb_edge_finder.execution",
        "cfb_edge_finder.modeling",
        "cfb_edge_finder.projections",
        "cfb_edge_finder.recommendation",
        "cfb_edge_finder.decision",
        "cfb_edge_finder.sizing",
        "cfb_edge_finder.accounting",
    )
    seen_football = False
    for path in sorted(V2_DIR.rglob("*.py")):
        names = _imports(path)
        seen_football |= any(
            n.startswith("cfb_edge_finder.scripting") or "archetype_research.replay" in n for n in names
        )
        for name in names:
            assert not name.startswith(forbidden), f"{path.name} imports {name}"
    assert seen_football


def test_claims_read_only_the_pregame_record(dev_records):
    r = next(x for x in dev_records if x["outcome"] is not None)
    first = v2_claims(r["pregame"])
    doctored = copy.deepcopy(r)
    doctored["outcome"]["features"]["home_margin"] = 99
    assert v2_claims(doctored["pregame"]) == first
    assert claims_hash(first) == r["v2_hash"]
    assert all(c["probability"] is None for c in (v2_claims(x["pregame"]) for x in dev_records))


def test_claim_dimensions_are_independent(dev_records):
    multi = [
        x
        for x in dev_records
        if x["v2"]["control"]
        and (
            (x["v2"]["scoring_environment"] or {}).get("claim") or x["v2"]["pace"] or x["v2"]["closeness"]["CLOSENESS"]
        )
    ]
    assert multi, "a game must be able to carry a directional claim and another dimension at once"


def test_explosive_upset_stays_untested_never_fabricated(dev_records, frozen_files):
    assert all(x["v2"]["explosive_upset"] == {"status": EXPLOSIVE_UPSET_STATUS} for x in dev_records)
    ev = evaluate(dev_records, frozen_files[2])
    assert ev["explosive_upset"] == {"status": EXPLOSIVE_UPSET_STATUS}


# ------------------------------------------------------------------ frozen parameters


def test_development_fit_refuses_holdout_and_prospective_seasons(dev_records):
    for year in (2016, 2026):
        bad = copy.deepcopy(dev_records[:5])
        for r in bad:
            r["pregame"]["season"] = year
        with pytest.raises(SealedSeasonError):
            fit_development(dev_records + bad)


def test_frozen_parameters_cannot_be_altered(frozen_files, tmp_path):
    _, frozen_path, params = frozen_files
    assert load_frozen(frozen_path)["sha256"] == params["sha256"]
    tampered = json.loads(frozen_path.read_text())
    tier = K.CONTROL_TIERS[0]
    tampered["control"][tier]["intervals"]["central_50"] = [-100, 100]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(tampered))
    with pytest.raises(FrozenParametersMismatch):
        load_frozen(bad)


def test_holdout_scoring_uses_frozen_intervals_and_cannot_refit(dev_records, frozen_files):
    _, _, params = frozen_files
    fixed = copy.deepcopy(params)
    for tier in K.CONTROL_TIERS:
        fixed["control"][tier]["intervals"] = {"central_50": [1000, 1001], "central_80": [-1000, 1000]}
    ev = evaluate(dev_records, fixed)
    for tier in K.CONTROL_TIERS:
        cov = ev["control"]["tiers"][tier].get("interval_coverage")
        if cov:
            assert cov["central_50"]["rate"] == 0.0 and cov["central_80"]["rate"] == 1.0
    assert "cfb_edge_finder.archetype_research.v2.fit" not in _imports(V2_DIR / "evaluate.py")


# ------------------------------------------------------------------ sealed holdout gate


def test_predict_is_only_for_holdout_seasons(dev_records):
    rows = _as_season(synthetic_season(seed=11), 2023)
    with pytest.raises(HoldoutGateError):
        predict_season(_season_data(rows))
    assert 2016 in HOLDOUT_SEASONS


def test_holdout_outcomes_cannot_be_revealed_without_the_protocol_and_freeze(
    predicted, holdout_rows, frozen_files, tmp_path
):
    protocol, frozen_path, params = frozen_files
    preds = predicted["records"]
    manifest = manifest_of(preds)
    targets = {t.game_id: t for t in schedule_targets(_games(holdout_rows), holdout_rows)[0]}
    kwargs = {"protocol_path": protocol, "frozen_path": frozen_path}
    # no protocol
    with pytest.raises(HoldoutGateError):
        reveal_and_score(
            preds, manifest, holdout_rows, targets, protocol_path=tmp_path / "none.md", frozen_path=frozen_path
        )
    # protocol that does not name the frozen hash
    unnamed = tmp_path / "unnamed.md"
    unnamed.write_text("PRE-REGISTERED\n")
    with pytest.raises(HoldoutGateError):
        reveal_and_score(preds, manifest, holdout_rows, targets, protocol_path=unnamed, frozen_path=frozen_path)
    # predictions altered after the manifest was frozen
    altered = copy.deepcopy(preds)
    altered[-1]["v2"]["control"] = {"claim": "HOME_CONTROL_STRONG", "side": "home", "tier": "STRONG", "evidence": []}
    with pytest.raises(HoldoutGateError):
        reveal_and_score(altered, manifest, holdout_rows, targets, **kwargs)
    # the intact chain reveals
    revealed, frozen = reveal_and_score(preds, manifest, holdout_rows, targets, **kwargs)
    assert frozen["sha256"] == params["sha256"]
    assert any(r["outcome"] for r in revealed)
    assert all(r["outcome"] is None for r in preds), "stage 1 must never carry an outcome"


def test_predictions_never_see_the_target_or_later_games(predicted, holdout_rows):
    preds = {r["pregame"]["game_id"]: r for r in predicted["records"]}
    target = sorted(preds.values(), key=lambda r: r["pregame"]["kickoff_utc"])[len(preds) // 2]
    cutoff = parse_utc(target["pregame"]["football_data_cutoff"])
    doctored = [
        dataclasses.replace(r, points_for=(r.points_for or 0) + 45, points_against=0.0)
        if r.kickoff() + FINAL_AFTER >= cutoff
        else r
        for r in holdout_rows
    ]
    again = {r["pregame"]["game_id"]: r for r in predict_season(_season_data(doctored))["records"]}
    tid = target["pregame"]["game_id"]
    assert again[tid]["pregame_hash"] == target["pregame_hash"]
    assert again[tid]["v2_hash"] == target["v2_hash"]


def test_appending_future_games_cannot_alter_prior_predictions(predicted, holdout_rows):
    late = [
        dataclasses.replace(
            r,
            game_id=f"LATE{r.game_id}",
            kickoff_utc=parse_utc(r.kickoff_utc).replace(month=12).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        for r in holdout_rows
        if r.week == 1
    ]
    again = predict_season(_season_data(holdout_rows + late))
    before = manifest_of(predicted["records"])["games"]
    after = manifest_of(again["records"])["games"]
    assert late and all(after[g] == h for g, h in before.items())


def test_holdout_rerun_is_deterministic(predicted, holdout_rows):
    again = predict_season(_season_data(holdout_rows))
    assert manifest_of(again["records"])["sha256"] == manifest_of(predicted["records"])["sha256"]


def test_gate_requires_preregistered_marker(frozen_files, tmp_path):
    _, frozen_path, params = frozen_files
    p = tmp_path / "draft.md"
    p.write_text(f"DRAFT {params['sha256']}")
    with pytest.raises(HoldoutGateError):
        check_gate(p, frozen_path)


# ------------------------------------------------------------------ committed artifacts


def test_committed_protocol_names_the_committed_frozen_parameters():
    protocol = REPO / "docs" / "ARCHETYPE_V2_HOLDOUT_PROTOCOL.md"
    frozen = REPO / "data" / "scripting" / "validation" / "archetype_v2_frozen_parameters.json"
    if not protocol.exists() or not frozen.exists():
        pytest.skip("protocol not yet committed")
    params = load_frozen(frozen)
    text = protocol.read_text()
    assert "PRE-REGISTERED" in text and params["sha256"] in text
    assert params["development_seasons"] == [2021, 2022, 2023, 2024, 2025]


def test_holdout_report_was_produced_under_the_committed_freeze():
    report = REPO / "data" / "scripting" / "validation" / "archetype_v2_holdout_report.json"
    frozen = REPO / "data" / "scripting" / "validation" / "archetype_v2_frozen_parameters.json"
    if not report.exists():
        pytest.skip("holdout not yet scored")
    rep = json.loads(report.read_text())
    params = load_frozen(frozen)
    assert rep["frozen_parameters_sha256"] == params["sha256"]
    for tier, iv in rep["frozen_control_intervals"].items():
        assert iv == params["control"][tier]["intervals"]
    assert rep["holdout_seasons"] == list(HOLDOUT_SEASONS)
    assert rep["production_changed"] is False and rep["probabilities_published"] is False


def test_criteria_produce_a_pre_registered_verdict(dev_records, frozen_files):
    crit = criteria(evaluate(dev_records, frozen_files[2]))
    assert set(K.FAMILIES) <= set(crit["summary"]["families_passed"])
    assert crit["summary"]["verdict"] in (
        "V2 GENERALIZES STRONGLY ON SEALED HOLDOUT",
        "V2 PARTIALLY GENERALIZES",
        "V2 FAILS SEALED HOLDOUT",
    )
