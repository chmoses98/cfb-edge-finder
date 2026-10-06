"""Historical preseason experiments: season alignment, candidate/control
separation, confirmation discipline, and no production impact.

Several tests here encode bugs found while running the real experiments.
Each one made the pipeline silently produce a plausible-looking wrong
answer rather than fail, which is exactly why they are pinned.
"""

from __future__ import annotations

import ast
import pathlib

import numpy as np
import pytest

from cfb_edge_finder.modeling.leakage import AsOf
from cfb_edge_finder.research.preseason.candidates import (
    CANDIDATES,
    CandidateSpec,
    DevelopmentOnlyError,
    apply_candidate,
    fit_beta,
)
from cfb_edge_finder.research.preseason.corpus import (
    CacheUnavailable,
    HistoricalGame,
    build_feature_tables,
    load_cache,
)
from cfb_edge_finder.research.preseason.evaluation import GamePrediction
from cfb_edge_finder.research.preseason.shadow_prior import (
    CONFIRMATION_RESULT,
    DEVELOPMENT_SEASONS,
    SHADOW_MODEL_VERSION,
    TALENT_BETA,
    shadow_margin,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "cfb_edge_finder"
PRESEASON = "cfb_edge_finder.research.preseason"


def imports_of(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def game(**kw) -> HistoricalGame:
    base = dict(
        game_id="g1", season=2024, week=1, home_team="alabama", away_team="georgia",
        home_points=28, away_points=21, neutral_site=False,
        home_classification="fbs", away_classification="fbs",
    )
    base.update(kw)
    return HistoricalGame(**base)


def prediction(**kw) -> GamePrediction:
    base = dict(
        game_id="g1", season=2022, week=1, home_win_probability=0.6,
        projected_margin=3.0, projected_total=50.0,
        actual_home_margin=10, actual_total=48,
    )
    base.update(kw)
    return GamePrediction(**base)


# -------------------------------- the two join bugs, pinned


def test_historical_games_carry_resolved_team_ids_not_display_names():
    """BUG 1. The ratings snapshot is keyed by resolved id. Passing a raw
    CFBD display name to project_game misses every lookup, hands every
    team the league-average rating, and yields a model that projects every
    game as a coin flip -- log loss 0.669 against 0.693 for a fair coin.
    It looks like a broken control rather than a broken join."""
    import json
    import tempfile

    from cfb_edge_finder.research.preseason.corpus import load_season

    payload = {
        "season": 2024,
        "games": [{
            "id": 1, "week": 1, "homeTeam": "Georgia Tech", "awayTeam": "Florida State",
            "homePoints": 24, "awayPoints": 21, "neutralSite": True,
            "homeClassification": "fbs", "awayClassification": "fbs",
        }],
        "returning_production": [], "talent": [], "coaches": [],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(json.dumps(payload))
        path = pathlib.Path(fh.name)
    try:
        loaded = load_season(path)
        g = loaded.games[0]
        assert g.home_team == "georgia-tech"
        assert g.away_team == "florida-state"
        assert g.raw_home_name == "Georgia Tech"
    finally:
        path.unlink()


def test_ambiguous_team_names_are_skipped_not_guessed():
    """A bare 'Miami' is ambiguous between miami-fl and miami-oh. The
    production resolver raises; the loader drops the game rather than
    picking one."""
    import json
    import tempfile

    from cfb_edge_finder.research.preseason.corpus import load_season

    payload = {
        "season": 2024,
        "games": [{
            "id": 1, "week": 1, "homeTeam": "Miami", "awayTeam": "Florida State",
            "homePoints": 24, "awayPoints": 21, "neutralSite": False,
            "homeClassification": "fbs", "awayClassification": "fbs",
        }],
        "returning_production": [], "talent": [], "coaches": [],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(json.dumps(payload))
        path = pathlib.Path(fh.name)
    try:
        assert load_season(path).games == []
    finally:
        path.unlink()


def test_feature_tables_are_keyed_by_resolved_ids():
    """BUG 2. Feature rows arrive keyed by CFBD display names while games
    carry slugs, so every feature lookup missed and every candidate
    reported 'insufficient coverage' -- a broken join that looks exactly
    like absent data."""
    from cfb_edge_finder.research.preseason.corpus import SeasonCache

    seasons = {
        2024: SeasonCache(
            season=2024,
            games=[],
            returning_rows=[{"team": "Alabama", "percentPPA": 0.5}],
            talent_rows=[{"team": "Alabama", "talent": 980.0}],
            coach_rows=[{"school": "Alabama", "coach": "Someone"}],
        )
    }
    table = build_feature_tables(seasons)[2024]
    target = AsOf(season=2024, week=1)
    assert table.get("alabama", "talent_composite", target=target) is not None
    assert table.get("Alabama", "talent_composite", target=target) is None


# ------------------------------------------ season alignment


def test_returning_and_talent_are_dated_to_the_prior_season():
    from cfb_edge_finder.research.preseason.corpus import SeasonCache

    seasons = {
        2024: SeasonCache(
            season=2024, games=[],
            returning_rows=[{"team": "Alabama", "percentPPA": 0.5}],
            talent_rows=[{"team": "Alabama", "talent": 980.0}],
            coach_rows=[],
        )
    }
    table = build_feature_tables(seasons)[2024]
    target = AsOf(season=2024, week=1)
    for name in ("returning_percentPPA", "talent_composite"):
        feature = table.get("alabama", name, target=target)
        assert feature.derived_from_season == 2023
        assert feature.applies_to_season == 2024


def test_a_missing_cache_raises_rather_than_returning_empty():
    with pytest.raises(CacheUnavailable):
        load_cache(pathlib.Path("/nonexistent/preseason/cache"))


# --------------------------------- confirmation discipline


def test_beta_cannot_be_fit_on_a_non_development_season():
    """A beta fit on the data it is evaluated on measures in-sample fit,
    which is the one thing this research is not interested in."""
    spec = CANDIDATES[0]
    rows = [(prediction(season=2025), 0.5) for _ in range(50)]
    with pytest.raises(DevelopmentOnlyError, match="non-development season"):
        fit_beta(spec, rows, development_seasons=(2021, 2022, 2023))


def test_beta_fits_on_development_seasons():
    spec = CANDIDATES[0]
    rows = [
        (prediction(season=2022, projected_margin=0.0, actual_home_margin=int(10 * d)), float(d))
        for d in [x / 10 for x in range(-20, 21)] * 2
    ]
    fitted = fit_beta(spec, rows, development_seasons=(2021, 2022, 2023))
    assert fitted is not None
    assert fitted.beta == pytest.approx(10.0, rel=1e-6)


def test_too_few_development_rows_yields_no_beta():
    spec = CANDIDATES[0]
    rows = [(prediction(season=2022), 0.5) for _ in range(10)]
    assert fit_beta(spec, rows, development_seasons=(2022,)) is None


def test_a_zero_variance_feature_yields_no_beta():
    """All-identical differentials carry no information and would divide
    by ~zero."""
    spec = CANDIDATES[0]
    rows = [(prediction(season=2022), 0.0) for _ in range(60)]
    assert fit_beta(spec, rows, development_seasons=(2022,)) is None


# ------------------------------------- candidate application


def test_a_missing_differential_leaves_the_control_untouched():
    """Imputing zero would assert the two teams are equal on the feature,
    which is a claim rather than an absence."""
    spec = CANDIDATES[0]
    rows = [(prediction(season=2022, projected_margin=0.0, actual_home_margin=int(10 * d)), float(d))
            for d in [x / 10 for x in range(-20, 21)] * 2]
    fitted = fit_beta(spec, rows, development_seasons=(2022,))
    base = prediction()
    out = apply_candidate(base, None, fitted, np.zeros(10))
    assert out is base


def test_the_candidate_shifts_margin_and_probability_together():
    """Moving the margin while leaving the win probability alone would
    produce an arm that contradicts itself."""
    spec = CANDIDATES[0]
    rows = [(prediction(season=2022, projected_margin=0.0, actual_home_margin=int(10 * d)), float(d))
            for d in [x / 10 for x in range(-20, 21)] * 2]
    fitted = fit_beta(spec, rows, development_seasons=(2022,))
    samples = np.array([-5.0, -1.0, 1.0, 5.0])
    base = prediction(projected_margin=0.0, home_win_probability=0.5)
    out = apply_candidate(base, 1.0, fitted, samples)
    assert out.projected_margin == pytest.approx(10.0)
    assert out.home_win_probability == 1.0  # every sample shifted above zero


def test_the_candidate_never_alters_the_realised_outcome():
    spec = CANDIDATES[0]
    rows = [(prediction(season=2022, projected_margin=0.0, actual_home_margin=int(10 * d)), float(d))
            for d in [x / 10 for x in range(-20, 21)] * 2]
    fitted = fit_beta(spec, rows, development_seasons=(2022,))
    base = prediction(actual_home_margin=17, actual_total=55)
    out = apply_candidate(base, 0.5, fitted, np.zeros(10))
    assert out.actual_home_margin == 17 and out.actual_total == 55


def test_candidates_are_tested_one_family_at_a_time():
    """Part 11: no kitchen-sink model. Each spec names exactly one
    feature."""
    assert len({c.name for c in CANDIDATES}) == len(CANDIDATES)
    for spec in CANDIDATES:
        assert isinstance(spec, CandidateSpec)
        assert isinstance(spec.feature_name, str)


# ------------------------------------------------ shadow model


def test_the_shadow_model_version_is_distinct_from_the_control():
    from cfb_edge_finder.research.preseason.control import CONTROL_MODEL_VERSION

    assert SHADOW_MODEL_VERSION != CONTROL_MODEL_VERSION
    assert "shadow" in SHADOW_MODEL_VERSION


def test_the_shadow_beta_is_frozen_at_its_development_value():
    assert TALENT_BETA == pytest.approx(0.018993)
    assert DEVELOPMENT_SEASONS == (2021, 2022, 2023)


def test_the_shadow_confirmation_result_is_recorded():
    """So a later reader sees what the candidate earned rather than
    taking it on trust."""
    wk1 = CONFIRMATION_RESULT["week_1"]
    assert wk1["n"] == 47
    assert wk1["paired_delta"] < 0
    assert wk1["ci"][1] < 0, "confirmation interval must exclude zero"
    # The prior correctly stops mattering once on-field evidence exists.
    later = CONFIRMATION_RESULT["weeks_4_plus"]
    assert later["ci"][0] < 0 < later["ci"][1]


def test_the_shadow_adjusts_margin_by_beta_times_differential():
    out = shadow_margin(control_margin=3.0, home_talent=900.0, away_talent=800.0)
    assert out.delta == pytest.approx(TALENT_BETA * 100.0)
    assert out.shadow_margin == pytest.approx(3.0 + TALENT_BETA * 100.0)
    assert out.applied


@pytest.mark.parametrize("home,away", [(None, 800.0), (900.0, None), (None, None)])
def test_missing_talent_leaves_the_control_margin_unchanged(home, away):
    out = shadow_margin(control_margin=7.5, home_talent=home, away_talent=away)
    assert not out.applied
    assert out.shadow_margin == 7.5
    assert out.delta == 0.0


def test_the_shadow_output_declares_it_is_not_production():
    out = shadow_margin(control_margin=1.0, home_talent=900.0, away_talent=880.0)
    assert out.to_dict()["is_production"] is False


# ----------------------------- production is untouched


@pytest.mark.parametrize(
    "package", ["modeling", "projections", "ratings", "recommendation", "kalshi", "decision"]
)
def test_no_production_package_imports_the_preseason_research(package):
    root = SRC / package
    if not root.exists():
        pytest.skip(f"{package} absent")
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in sorted(root.rglob("*.py"))
        if any(m.startswith(PRESEASON) for m in imports_of(p))
    ]
    assert offenders == [], f"production imports research: {offenders}"


def test_no_production_package_imports_the_shadow_model():
    """The shadow must not be able to reach live pricing."""
    shadow = f"{PRESEASON}.shadow_prior"
    offenders = []
    for package in ("modeling", "projections", "ratings", "recommendation", "kalshi", "decision"):
        root = SRC / package
        if root.exists():
            offenders += [
                str(p) for p in root.rglob("*.py") if shadow in imports_of(p)
            ]
    assert offenders == []


def test_the_research_never_assigns_a_production_parameter():
    protected = {
        "model_probability", "projected_margin", "projected_total",
        "DEFAULT_RIDGE_LAMBDA", "DEFAULT_SEASON_SHRINKAGE_K", "hfa",
    }
    for path in sorted((SRC / "research" / "preseason").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            targets = node.targets if isinstance(node, ast.Assign) else []
            for t in targets:
                name = t.attr if isinstance(t, ast.Attribute) else getattr(t, "id", None)
                assert name not in protected, f"{path.name} assigns {name}"


def test_the_experiment_module_uses_the_production_projection_entry_point():
    """A reimplementation would measure a model nobody runs."""
    imported = imports_of(SRC / "research" / "preseason" / "experiment.py")
    assert "cfb_edge_finder.modeling.score_model" in imported
    assert "cfb_edge_finder.modeling.ratings" in imported


def test_the_fetch_script_never_writes_the_api_key():
    src = (REPO_ROOT / "scripts" / "fetch_preseason_research_cache.py").read_text()
    assert "api_key_present" in src
    assert "\"api_key\":" not in src
    assert "cfbd_api_key" in src  # read, never emitted
    for leak in ("print(key", "print(api_key", "json.dumps(key"):
        assert leak not in src
