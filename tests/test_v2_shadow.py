"""V2 shadow: it must reproduce research, price half-point strikes
correctly, and never be able to hurt the canonical 0.5.0 row.

The research tournament chose V2 and the enrichment mission recommended
PRODUCTIONIZE ORIGINAL V2. This file pins the three properties that make
shadowing it safe:

  REPRODUCTION  the vendored feature builder is byte-identical to the
                research one, and the artifact carries a PASSING
                reproduction record or it is refused outright;
  CORRECTNESS   half-point strikes are priced verbatim and only integer
                thresholds receive the continuity correction -- the bug
                the research audit found in the V1 path;
  ISOLATION     a missing, corrupt, mis-schema'd, mis-hashed or
                slate-contaminated artifact turns V2 OFF and changes
                nothing about the canonical capture.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "tests"))
sys.path.insert(0, str(_ROOT / "scripts"))

from cfb_edge_finder.modeling.v2.artifact import (  # noqa: E402
    ARTIFACT_SCHEMA_VERSION,
    V2ArtifactError,
    assert_no_outcomes_after,
    load_artifact,
)
from cfb_edge_finder.modeling.v2.pricing import (  # noqa: E402
    CONTINUITY,
    contract_probability,
    effective_threshold,
    home_win_probability,
    price_observation_v2,
    probability_less,
)
from cfb_edge_finder.research import v2_shadow  # noqa: E402
from cfb_edge_finder.schemas.kalshi_observation import MarketFamily, Side  # noqa: E402

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)

VENDORED_FEATURES_SHA256 = "8ace346ab1748e9c88519f8e0a1167114ce56b00d63155320c7a5114584bb6b7"
"""sha256 of research/v2/features.py at claude/cfb-model-v2-research-krupoc
@ c1eedc9, recorded when the file was vendored. See the header of
modeling/v2/research_features.py."""


# ---------------------------------------------------------------- fixtures


def _artifact_payload(**overrides):
    payload = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_version": "0.6.0-v2-shadow",
        "spec_id": "cfb-v2-candidate-2026-09-02",
        "spec_sha256": "a" * 64,
        "training_cutoff": "through the 2025 postseason; NO 2026 game result",
        "prediction_season": 2026,
        "dataset": {"built_at": "2026-09-02T06:24:25.308376Z", "cache_fetched_at": "2026-09-02T05:17:45Z"},
        "reproduction": {"passed": True, "ensemble": {"max_abs_diff": 0.0}},
        "games": [
            {
                "game_id": "401856766",
                "season": 2026,
                "week": 1,
                "home_team": "TCU",
                "away_team": "North Carolina",
                "pred_margin": 8.0,
                "pred_total": 51.0,
                "sd_margin": 16.0,
                "sd_total": 15.5,
                "p_home": 0.68,
            }
        ],
    }
    payload.update(overrides)
    return payload


def _write_artifact(tmp_path: Path, payload: dict) -> Path:
    body = {k: v for k, v in payload.items() if k not in ("artifact_sha256", "built_at")}
    payload["artifact_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload["built_at"] = "2026-09-03T21:42:20+00:00"
    path = tmp_path / "2026.artifact.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _loaded_prediction(tmp_path: Path):
    """A real, fully-verified artifact's prediction -- the orientation
    tests must run against something that passed every load check, not a
    hand-built object that skipped them."""
    return load_artifact(_write_artifact(tmp_path, _artifact_payload()), season=2026).for_game("401856766")


class _Obs:
    """The fields `price_observation_v2` reads -- exactly the ones
    `kalshi/ladder_pricing` stamps onto a canonical observation."""

    def __init__(self, family, threshold=None, side=None, team=None):
        self.family = family
        self.threshold = threshold
        self.side = side
        self.team = team


# ============================================================ reproduction
# Test matrix 10-17.


def test_vendored_feature_builder_is_unmodified():
    """The whole reproduction argument rests on production computing
    features with the SAME code as research. If someone edits the
    vendored copy, that argument silently stops holding -- so the file is
    hashed against the sha recorded when it was ported."""
    ported = _ROOT / "src" / "cfb_edge_finder" / "modeling" / "v2" / "research_features.py"
    body = ported.read_text()
    marker = "# ============================================================================\n"
    payload = body.split(marker)[-1]
    assert hashlib.sha256(payload.encode()).hexdigest() == VENDORED_FEATURES_SHA256, (
        "modeling/v2/research_features.py no longer matches the research file it was ported from"
    )


def test_artifact_without_a_passing_reproduction_is_refused(tmp_path):
    """Test matrix 24: a model that never proved it reproduces research
    is never shadowed, whatever else it carries."""
    path = _write_artifact(tmp_path, _artifact_payload(reproduction={"passed": False}))
    with pytest.raises(V2ArtifactError, match="reproduction"):
        load_artifact(path, season=2026)

    path = _write_artifact(tmp_path, _artifact_payload(reproduction={}))
    with pytest.raises(V2ArtifactError, match="reproduction"):
        load_artifact(path, season=2026)


# ================================================= half-point correctness
# Test matrix 16. The bug the research audit found in the V1 path.


@pytest.mark.parametrize("threshold", [-7.0, -3.0, 0.0, 3.0, 7.0, 51.0])
def test_integer_thresholds_receive_the_continuity_correction(threshold):
    assert effective_threshold(threshold) == pytest.approx(threshold + CONTINUITY)


@pytest.mark.parametrize("threshold", [-7.5, -3.5, 0.5, 3.5, 51.5])
def test_half_point_thresholds_are_used_verbatim(threshold):
    """A half-point strike cannot be landed on, so P(X > 3.5) is already
    exact. Correcting it would price a DIFFERENT contract than the one
    being traded -- a systematic half-point error on most markets."""
    assert effective_threshold(threshold) == pytest.approx(threshold)


def test_half_point_probabilities_are_exactly_complementary():
    """No push is possible on a half-point line, so P(>) + P(<) == 1
    exactly. This is the sharpest available check that no correction
    leaked in: any continuity shift would break the identity."""
    over = contract_probability(50.0, 15.0, 51.5)
    under = probability_less(50.0, 15.0, 51.5)
    assert over + under == pytest.approx(1.0, abs=1e-12)


def test_integer_probabilities_leave_exactly_the_push_gap():
    """On an integer line a push IS possible, so P(>) + P(<) < 1, and the
    gap is P(X == t). Asserting the gap is positive and matches the
    two-sided correction pins the integer branch just as tightly."""
    from cfb_edge_finder.modeling.v2.pricing import _norm_cdf

    point, sd, t = 50.0, 15.0, 51.0
    gap = 1.0 - (contract_probability(point, sd, t) + probability_less(point, sd, t))
    assert gap > 0
    expected = _norm_cdf((t + 0.5 - point) / sd) - _norm_cdf((t - 0.5 - point) / sd)
    assert gap == pytest.approx(expected, abs=1e-12)


def test_v2_never_shifts_a_half_point_line_the_way_v1_does():
    """Direct contrast with the canonical path, which adds 0.5
    unconditionally. On a half-point line the two must DISAGREE -- that
    disagreement is the fix."""
    from cfb_edge_finder.projections.distribution import CONTINUITY_CORRECTION

    point, sd, t = 8.0, 16.0, 3.5
    v1_style = contract_probability(point, sd, t + CONTINUITY_CORRECTION, continuity=0.0)
    v2 = contract_probability(point, sd, t)
    assert v2 != pytest.approx(v1_style)
    assert v2 > v1_style, "V1's extra half point makes the favourite look worse than the contract is"


# -------------------------------------------------- contract orientation
# Test matrix 16: spread, total, home/away orientation.


def test_spread_home_and_away_use_the_canonical_sign_convention(tmp_path):
    """`market_pricing.price_parsed_contract` maps a home-named spread to
    home_line = -line and prices P(margin > line); an away-named one to
    home_line = +line and prices P(margin < -line). V2 must read the
    contract the same way or the shadow is answering a different
    question."""
    pred = _loaded_prediction(tmp_path)

    home = _Obs(MarketFamily.SPREAD, threshold=3.5, side=None, team=Side.HOME)
    away = _Obs(MarketFamily.SPREAD, threshold=3.5, side=None, team=Side.AWAY)
    p_home, detail_home = price_observation_v2(home, pred)
    p_away, detail_away = price_observation_v2(away, pred)

    assert p_home == pytest.approx(contract_probability(8.0, 16.0, 3.5))
    assert p_away == pytest.approx(probability_less(8.0, 16.0, -3.5))
    assert "home" in detail_home and "away" in detail_away

    # These two are NOT complements, and asserting that they were is the
    # error this test caught. Both contracts read "team X wins by more
    # than 3.5", so together they exclude the band |margin| < 3.5 -- which
    # is exactly the outcome region where NEITHER pays. Pinning the gap
    # is what proves the sign convention was read correctly.
    from cfb_edge_finder.modeling.v2.pricing import _norm_cdf

    band = _norm_cdf((3.5 - 8.0) / 16.0) - _norm_cdf((-3.5 - 8.0) / 16.0)
    assert p_home + p_away == pytest.approx(1.0 - band, abs=1e-12)
    assert 0.0 < band < 1.0


def test_total_over_and_under_are_complementary_on_a_half_point(tmp_path):
    pred = _loaded_prediction(tmp_path)
    over = _Obs(MarketFamily.TOTAL, threshold=51.5, side=Side.OVER)
    under = _Obs(MarketFamily.TOTAL, threshold=51.5, side=Side.UNDER)
    p_over, _ = price_observation_v2(over, pred)
    p_under, _ = price_observation_v2(under, pred)
    assert p_over == pytest.approx(contract_probability(51.0, 15.5, 51.5))
    assert p_over + p_under == pytest.approx(1.0, abs=1e-12)


def test_moneyline_uses_the_margin_distribution(tmp_path):
    pred = _loaded_prediction(tmp_path)
    p, _ = price_observation_v2(_Obs(MarketFamily.MONEYLINE, team=Side.HOME), pred)
    assert p == pytest.approx(home_win_probability(8.0, 16.0))


def test_unpriceable_contracts_return_a_reason_not_a_number(tmp_path):
    """Fail closed, and say why: a silent None is indistinguishable from
    a contract V2 simply agreed about."""
    pred = _loaded_prediction(tmp_path)
    for obs, fragment in (
        (_Obs(MarketFamily.SPREAD, threshold=3.5, team=None), "no resolved team side"),
        (_Obs(MarketFamily.SPREAD, threshold=None, team=Side.HOME), "missing line"),
        (_Obs(MarketFamily.TOTAL, threshold=None, side=Side.OVER), "missing line/side"),
        (_Obs(MarketFamily.FIRST_HALF_TOTAL, threshold=24.5, side=Side.OVER), "outside the frozen V2 spec"),
    ):
        value, reason = price_observation_v2(obs, pred)
        assert value is None
        assert fragment in reason


# ==================================================== artifact validation
# Test matrix 23-26.


def test_valid_artifact_loads_and_stamps_its_own_version(tmp_path):
    path = _write_artifact(tmp_path, _artifact_payload())
    art = load_artifact(path, season=2026)
    assert art.model_version == "0.6.0-v2-shadow"
    assert art.summary_dict()["v2_model_version"] == "0.6.0-v2-shadow"
    assert art.for_game("401856766") is not None
    assert art.for_game("does-not-exist") is None


def test_tampered_artifact_is_refused(tmp_path):
    """Test matrix 24: a wrong hash means the numbers are not the ones
    that were verified, so nothing is priced from them."""
    path = _write_artifact(tmp_path, _artifact_payload())
    payload = json.loads(path.read_text())
    payload["games"][0]["pred_margin"] = 99.0
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    with pytest.raises(V2ArtifactError, match="sha256 mismatch"):
        load_artifact(path, season=2026)


def test_unknown_schema_is_refused_not_best_effort_parsed(tmp_path):
    path = _write_artifact(tmp_path, _artifact_payload(schema_version="v2_shadow_artifact_v99"))
    with pytest.raises(V2ArtifactError, match="schema"):
        load_artifact(path, season=2026)


def test_wrong_season_is_refused(tmp_path):
    path = _write_artifact(tmp_path, _artifact_payload())
    with pytest.raises(V2ArtifactError, match="season"):
        load_artifact(path, season=2025)


def test_missing_artifact_is_refused(tmp_path):
    with pytest.raises(V2ArtifactError, match="no V2 artifact"):
        load_artifact(tmp_path / "nope.json", season=2026)


def test_non_positive_uncertainty_is_refused(tmp_path):
    payload = _artifact_payload()
    payload["games"][0]["sd_margin"] = 0.0
    path = _write_artifact(tmp_path, payload)
    with pytest.raises(V2ArtifactError, match="non-positive uncertainty"):
        load_artifact(path, season=2026)


# ------------------------------------------------------- the freeze guard
# Test matrix 26: no Week 1 outcomes in the frozen artifact.


def test_artifact_built_before_the_slate_passes_the_freeze_guard(tmp_path):
    art = load_artifact(_write_artifact(tmp_path, _artifact_payload()), season=2026)
    assert_no_outcomes_after(art, NOW)  # dataset built 2026-09-02, slate starts later


def test_artifact_built_after_the_slate_cutoff_is_refused(tmp_path):
    """The load-bearing guard for a prospective test. An artifact whose
    evidence postdates the slate may have seen the outcomes it is being
    judged on, which is worse than having no comparison at all."""
    payload = _artifact_payload()
    payload["dataset"]["built_at"] = "2026-09-06T00:00:00+00:00"
    art = load_artifact(_write_artifact(tmp_path, payload), season=2026)
    with pytest.raises(V2ArtifactError, match="NEWER than the slate cutoff"):
        assert_no_outcomes_after(art, NOW)


def test_naive_timestamp_is_refused(tmp_path):
    payload = _artifact_payload()
    payload["dataset"]["built_at"] = "2026-09-02T06:24:25"
    art = load_artifact(_write_artifact(tmp_path, payload), season=2026)
    with pytest.raises(V2ArtifactError, match="timezone-aware"):
        assert_no_outcomes_after(art, NOW)


# ========================================================= shadow ledger
# Test matrix 19-21, 27.


def _row(key="obs-1", version="0.6.0-v2-shadow"):
    return v2_shadow.V2ShadowRow(
        schema_version=v2_shadow.V2_SHADOW_SCHEMA_VERSION,
        observation_key=key,
        season=2026,
        game_id="401856766",
        kalshi_market_ticker="KXNCAAFGAME-26TCU-TCU",
        timing_label="T_24H",
        captured_at=NOW.isoformat(),
        kickoff_utc=(NOW + timedelta(hours=24)).isoformat(),
        v2_model_version=version,
        v2_artifact_sha256="c" * 64,
        v2_spec_id="cfb-v2-candidate-2026-09-02",
        v2_training_cutoff="through 2025",
    )


def test_ledger_is_append_only_and_deduped(tmp_path):
    """Test matrix 20, 21, 27: a 5-minute loop re-running must not write
    a second copy of the same shadow row."""
    path = tmp_path / "2026.jsonl"
    assert v2_shadow.append_rows(path, [_row("a"), _row("b")]) == 2
    first = path.read_text()

    seen = v2_shadow.load_existing_keys(path)
    assert seen == {
        v2_shadow.dedup_key("a", "0.6.0-v2-shadow"),
        v2_shadow.dedup_key("b", "0.6.0-v2-shadow"),
    }

    # Re-running: both keys are already present, so nothing is appended
    # and the file is byte-identical.
    fresh = [
        r
        for r in (_row("a"), _row("b"))
        if v2_shadow.dedup_key(r.observation_key, r.v2_model_version) not in seen
    ]
    assert fresh == []
    assert v2_shadow.append_rows(path, fresh) == 0
    assert path.read_text() == first


def test_a_new_model_version_coexists_rather_than_overwriting(tmp_path):
    """Dedup is on (observation_key, model_version), so a future V2 can
    shadow the same canonical rows without destroying this one's
    evidence."""
    path = tmp_path / "2026.jsonl"
    v2_shadow.append_rows(path, [_row("a", "0.6.0-v2-shadow")])
    seen = v2_shadow.load_existing_keys(path)
    assert v2_shadow.dedup_key("a", "0.7.0-next") not in seen


def test_a_corrupt_tail_does_not_stop_todays_capture(tmp_path):
    path = tmp_path / "2026.jsonl"
    v2_shadow.append_rows(path, [_row("a")])
    with path.open("a") as handle:
        handle.write("{not json\n")
    assert v2_shadow.load_existing_keys(path) == {v2_shadow.dedup_key("a", "0.6.0-v2-shadow")}


def test_half_point_flag_is_recorded(tmp_path):
    assert v2_shadow.is_half_point(3.5) is True
    assert v2_shadow.is_half_point(3.0) is False
    assert v2_shadow.is_half_point(None) is None


def test_row_links_to_the_canonical_observation_key(tmp_path):
    """Test matrix 19: a shadow row must be joinable to the canonical row
    it shadows, and must never look like a standalone observation."""
    row = _row("cfb-2026-wk01-x-at-y|TICKER|T_24H|0.5.0")
    payload = json.loads(row.to_json())
    assert payload["observation_key"] == "cfb-2026-wk01-x-at-y|TICKER|T_24H|0.5.0"
    assert payload["schema_version"] == v2_shadow.V2_SHADOW_SCHEMA_VERSION
    assert "model_probability" not in payload, "must not mimic the canonical observation schema"




# =============================================== no scipy in the hot path
# A live dry run caught this: importing scipy.stats in the pricing module
# killed the collector at import time on a clean production install,
# BEFORE any canonical row could be captured.


def test_pricing_module_does_not_import_scipy():
    """scipy is not a runtime dependency (pyproject lists pydantic,
    requests, numpy). The V2 shadow must not smuggle one into the
    5-minute capture loop."""
    source = (_ROOT / "src" / "cfb_edge_finder" / "modeling" / "v2" / "pricing.py").read_text()
    assert "import scipy" not in source
    assert "from scipy" not in source

    for module in ("artifact.py", "pricing.py"):
        text = (_ROOT / "src" / "cfb_edge_finder" / "modeling" / "v2" / module).read_text()
        assert "from scipy" not in text, f"{module} must not import scipy"


def test_stdlib_normal_cdf_matches_published_reference_values():
    """The scipy replacement has to BE a replacement, not an approximation.

    Checked against published standard-Normal CDF values rather than
    against scipy, because scipy is not a dependency of this project at
    all -- not runtime, not dev. A test that silently skips wherever the
    library is absent would verify nothing in CI, which is the only place
    that matters here."""
    from cfb_edge_finder.modeling.v2.pricing import _norm_cdf

    # Phi(z) to 15 significant figures.
    reference = {
        -8.0: 6.22096057427178e-16,
        -5.0: 2.86651571879194e-07,
        -3.0: 1.34989803163009e-03,
        -1.959963984540054: 2.50000000000000e-02,
        -1.0: 1.58655253931457e-01,
        0.0: 5.00000000000000e-01,
        1.0: 8.41344746068543e-01,
        1.959963984540054: 9.75000000000000e-01,
        3.0: 9.98650101968370e-01,
        5.0: 9.99999713348428e-01,
    }
    for z, expected in reference.items():
        assert _norm_cdf(z) == pytest.approx(expected, rel=1e-12, abs=1e-18), f"Phi({z})"

    # Structural identities that any correct CDF must satisfy.
    for z in (0.3, 1.7, 4.2):
        assert _norm_cdf(z) + _norm_cdf(-z) == pytest.approx(1.0, abs=1e-15)


def test_stdlib_normal_cdf_agrees_with_scipy_when_scipy_is_present():
    """An extra cross-check where scipy happens to be installed (it is in
    the research environment). Skipped, never failed, when it is not --
    the reference-value test above is the one that always runs."""
    stats = pytest.importorskip("scipy.stats")

    from cfb_edge_finder.modeling.v2.pricing import _norm_cdf

    for z in (-8.0, -5.0, -3.0, -1.0, -0.25, 0.0, 0.25, 1.0, 3.0, 5.0, 8.0):
        assert _norm_cdf(z) == pytest.approx(float(stats.norm.cdf(z)), abs=1e-15, rel=1e-12)


def test_contract_probabilities_are_consistent_with_the_cdf():
    """End-to-end: the continuity branch plus the CDF, with no external
    reference implementation involved."""
    from cfb_edge_finder.modeling.v2.pricing import _norm_cdf

    for point, sd, t in ((8.0, 16.0, 3.5), (8.0, 16.0, -3.0), (51.0, 15.5, 51.5), (0.0, 14.0, 0.0)):
        cut = t + CONTINUITY if abs(t - round(t)) < 1e-9 else t
        expected = 1.0 - _norm_cdf((cut - point) / sd)
        assert contract_probability(point, sd, t) == pytest.approx(expected, abs=1e-14)
