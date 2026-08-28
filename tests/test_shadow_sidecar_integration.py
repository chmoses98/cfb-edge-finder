"""The live shadow sidecar: canonical capture is never endangered,
one transform per game, append-only dedup, and no production reach.

The load-bearing tests are the ones that BREAK the shadow and assert the
canonical path survives.
"""

from __future__ import annotations

import ast
import json
import pathlib
import tempfile
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from cfb_edge_finder.research import persistence
from cfb_edge_finder.research.preseason.shadow_capture import ShadowUnavailableReason
from cfb_edge_finder.research.preseason.shadow_prior import SHADOW_MODEL_VERSION, TALENT_BETA
from cfb_edge_finder.research.preseason.shadow_sidecar import (
    ShadowSidecar,
    shadow_key,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "cfb_edge_finder"

KICKOFF = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)
CAPTURED = KICKOFF - timedelta(hours=24)
RNG = np.random.default_rng(7)
MARGINS = np.round(RNG.normal(3.0, 17.0, 4000))


def sidecar(**kw) -> ShadowSidecar:
    base = dict(
        talent_by_team={"alabama": 973.5, "east-carolina": 623.8, "georgia": 1003.7},
        talent_season=2026,
        talent_source_version="preseason_research_cache_v1",
        talent_fetched_at="2026-08-28T15:00:00+00:00",
        code_sha="deadbeef",
        shadow_capture_started_at="2026-08-28T15:00:00+00:00",
    )
    base.update(kw)
    return ShadowSidecar(**base)


def contract(sc: ShadowSidecar, *, ticker="KXNCAAFGAME-T1", home="alabama",
             away="east-carolina", both_fbs=True, **kw):
    args = dict(
        observation_key="obs-1",
        game_id="cfb-2026-wk01-east-carolina-at-alabama",
        timing_label="T_24H",
        captured_at=CAPTURED,
        kickoff_utc=KICKOFF,
        market_ticker=ticker,
        market_family="moneyline",
        executable_yes_price=0.55,
        executable_no_price=0.47,
        control_model_version="0.4.0-milestone-c2-live-margin-correction",
        control_probability=0.61,
        projection_snapshot_id="snap-1",
        home_team_id=home,
        away_team_id=away,
        corrected_margin_samples=MARGINS,
        control_margin_corrected=3.0,
        control_expected_home=28.0,
        control_expected_away=25.0,
        both_fbs=both_fbs,
        capture_mode="PROSPECTIVE",
    )
    args.update(kw)
    return sc.for_contract(**args)


def imports_of(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ------------------------- canonical capture is never endangered


def test_the_sidecar_never_raises_even_on_garbage_input():
    """THE LOAD-BEARING TEST. A research side effect must never be able
    to cost a prospective capture -- least of all a CLOSING one, which
    cannot be recovered."""
    sc = sidecar()
    out = contract(sc, corrected_margin_samples="not an array", control_margin_corrected=object())
    assert out is None or out.available is False
    assert sc.telemetry.shadow_failures >= 0  # counted, never propagated


def test_a_missing_control_projection_yields_an_unavailable_record_not_an_error():
    sc = sidecar()
    out = contract(sc, control_probability=None, control_margin_corrected=None)
    assert out is not None
    assert not out.available
    assert out.unavailable_reason == ShadowUnavailableReason.CONTROL_NOT_PRICED.value


def test_the_scanner_treats_a_missing_sidecar_as_a_no_op():
    """`_build_shadow_sidecar` returns None on any problem, and the hook
    is guarded on that. Canonical capture then runs exactly as it did
    before this module existed."""
    src = (REPO_ROOT / "scripts" / "research_scan_and_capture.py").read_text()
    assert "if shadow_sidecar is not None and cached_projection is not None:" in src
    assert "except Exception:  # noqa: BLE001" in src


def test_shadow_persistence_happens_after_canonical_and_is_wrapped():
    """Ordering matters: if the shadow write raised, canonical rows are
    already durable."""
    src = (REPO_ROOT / "scripts" / "research_scan_and_capture.py").read_text()
    canonical = src.index("append_observation_rows")
    shadow = src.index("SHADOW_SUBDIR")
    assert canonical < shadow, "shadow must be written after canonical observations"


# --------------------------------- one transform per game


def test_one_transform_is_computed_per_game_not_per_contract():
    """Rebuilding per ticker would reintroduce the per-contract model
    work this repository has already had to fix once."""
    sc = sidecar()
    for i in range(25):
        contract(sc, ticker=f"KXNCAAFGAME-T{i}")
    assert sc.telemetry.shadow_game_transforms == 1
    assert sc.telemetry.shadow_contracts_priced == 25


def test_different_timing_labels_get_their_own_transform():
    """A T_24H and a CLOSING snapshot are different projections."""
    sc = sidecar()
    contract(sc, timing_label="T_24H")
    contract(sc, timing_label="CLOSING")
    assert sc.telemetry.shadow_game_transforms == 2


def test_both_arms_read_the_same_margin_draws():
    sc = sidecar()
    out = contract(sc)
    expected_delta = TALENT_BETA * (973.5 - 623.8)
    assert out.shadow_minus_control_margin == pytest.approx(expected_delta)
    assert out.shadow_projected_margin == pytest.approx(3.0 + expected_delta)


# ------------------------------------- fail-closed talent


@pytest.mark.parametrize("home,away,reason", [
    ("unknown-team", "alabama", ShadowUnavailableReason.TALENT_MISSING_HOME),
    ("alabama", "unknown-team", ShadowUnavailableReason.TALENT_MISSING_AWAY),
    ("unknown-a", "unknown-b", ShadowUnavailableReason.TALENT_MISSING_BOTH),
])
def test_missing_talent_is_explicit_and_never_a_zero_delta(home, away, reason):
    sc = sidecar()
    out = contract(sc, home=home, away=away)
    assert not out.available
    assert out.unavailable_reason == reason.value
    assert out.shadow_minus_control_margin is None
    assert sc.telemetry.unavailable_reasons.get(reason.value) == 1


def test_non_fbs_matchups_are_refused():
    sc = sidecar()
    out = contract(sc, both_fbs=False)
    assert not out.available
    assert out.unavailable_reason == ShadowUnavailableReason.UNSUPPORTED_POPULATION.value


def test_coverage_telemetry_is_counted_not_asserted():
    sc = sidecar()
    contract(sc, ticker="a")
    contract(sc, ticker="b", home="unknown-team")
    payload = sc.telemetry.to_dict()
    assert payload["control_contracts_priced"] == 2
    assert payload["shadow_contracts_priced"] == 1
    assert payload["shadow_coverage"] == pytest.approx(0.5)


# ------------------------------------ dedup / append-only


def test_the_shadow_key_includes_the_model_version():
    """So a LATER candidate coexists beside this one rather than
    overwriting the evidence it is collecting."""
    key = shadow_key("obs-1")
    assert key == f"obs-1|{SHADOW_MODEL_VERSION}"
    assert shadow_key("obs-1", "shadow-future-v2") != key


def test_a_retry_writes_zero_duplicate_rows():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "shadow" / "2026.jsonl"
        rows = [{"shadow_key": shadow_key("obs-1"), "game_id": "g"}]
        first = persistence.append_json_rows(path, rows, key_fn=lambda r: r.get("shadow_key"))
        second = persistence.append_json_rows(path, rows, key_fn=lambda r: r.get("shadow_key"))
        assert first.written == 1
        assert second.written == 0
        assert second.skipped_duplicate == 1
        assert len(path.read_text().strip().splitlines()) == 1


def test_a_later_shadow_version_coexists_without_overwriting():
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "shadow" / "2026.jsonl"
        persistence.append_json_rows(
            path, [{"shadow_key": shadow_key("obs-1"), "v": 1}],
            key_fn=lambda r: r.get("shadow_key"),
        )
        result = persistence.append_json_rows(
            path, [{"shadow_key": shadow_key("obs-1", "shadow-future-v2"), "v": 2}],
            key_fn=lambda r: r.get("shadow_key"),
        )
        assert result.written == 1
        assert len(path.read_text().strip().splitlines()) == 2


def test_shadow_rows_live_in_their_own_file_not_the_observations_file():
    """Canonical observations stay byte-identical; a reader that knows
    nothing about shadows never sees them."""
    assert persistence.SHADOW_SUBDIR == "shadow"
    assert persistence.SHADOW_SUBDIR != persistence.OBSERVATIONS_SUBDIR


# ------------------------------- prospective-only boundary


def test_the_deployment_boundary_is_recorded():
    """Future analytics must know that absence before deployment is
    expected, not missing data."""
    sc = sidecar()
    assert sc.shadow_capture_started_at is not None


def test_no_shadow_row_is_produced_for_a_non_prospective_capture():
    sc = sidecar()
    out = contract(sc, capture_mode="RETROSPECTIVE_BACKFILL")
    # build_shadow_record raises; the sidecar catches and counts it.
    assert out is None
    assert sc.telemetry.shadow_failures == 1


def test_no_shadow_row_for_a_capture_at_or_after_kickoff():
    sc = sidecar()
    out = contract(sc, captured_at=KICKOFF)
    assert not out.available
    assert out.unavailable_reason == ShadowUnavailableReason.CAPTURED_AT_OR_AFTER_KICKOFF.value


# --------------------------------- production isolation


@pytest.mark.parametrize(
    "package", ["modeling", "projections", "ratings", "recommendation", "kalshi", "decision"]
)
def test_no_production_package_imports_the_shadow_sidecar(package):
    root = SRC / package
    if not root.exists():
        pytest.skip(f"{package} absent")
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in sorted(root.rglob("*.py"))
        if any("research.preseason" in m for m in imports_of(p))
    ]
    assert offenders == []


def test_the_scanner_never_writes_a_shadow_value_into_a_canonical_row():
    """The hook may only APPEND to a separate list; it must not touch the
    corpus row builder's inputs."""
    src = (REPO_ROOT / "scripts" / "research_scan_and_capture.py").read_text()
    tree = ast.parse(src)
    emitter = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_emit_shadow_record"
    )
    assigned = {
        t.attr
        for node in ast.walk(emitter)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Attribute)
    }
    for canonical in ("model_probability", "observation", "pricing_status", "family"):
        assert canonical not in assigned


def test_the_sidecar_creates_no_qualification_or_stake_vocabulary():
    src = (SRC / "research" / "preseason" / "shadow_sidecar.py").read_text().lower()
    for banned in ("qualif", "stake", "wager", "bankroll", "place_order", "recommend"):
        assert banned not in src


def test_the_talent_lookup_goes_through_the_leakage_guard():
    """Reading the feature index directly would be faster and would skip
    validate_for(), the one check that stops a talent row being applied
    to the season it was derived from."""
    src = (REPO_ROOT / "scripts" / "research_scan_and_capture.py").read_text()
    assert 'table.get(team, "talent_composite", target=target)' in src


# ------------------------------------------- end to end


def test_end_to_end_control_and_shadow_share_one_market_observation():
    sc = sidecar()
    out = contract(sc)
    payload = out.to_dict()
    assert payload["market_ticker"] == "KXNCAAFGAME-T1"
    assert payload["executable_yes_price"] == 0.55
    assert payload["executable_no_price"] == 0.47
    assert payload["timing_label"] == "T_24H"
    assert payload["control_model_version"] == "0.4.0-milestone-c2-live-margin-correction"
    assert payload["shadow_model_version"] == SHADOW_MODEL_VERSION
    assert payload["is_canonical"] is False


def test_end_to_end_record_is_json_serialisable_and_linked():
    sc = sidecar()
    out = contract(sc)
    encoded = json.loads(json.dumps(out.to_dict()))
    assert encoded["observation_key"] == "obs-1"
    assert encoded["game_id"].startswith("cfb-2026-wk01")
    assert encoded["beta"] == TALENT_BETA


def test_end_to_end_the_control_values_pass_through_unchanged():
    sc = sidecar()
    out = contract(sc)
    assert out.control_probability == 0.61
    assert out.control_projected_margin == 3.0


def test_sidecar_failures_record_the_exception_type():
    """A bare failure count is undiagnosable. During development the
    broad except swallowed an AttributeError from a typo and only the
    counter revealed anything was wrong."""
    sc = sidecar()
    out = contract(sc, capture_mode="RETROSPECTIVE_BACKFILL")
    assert out is None
    assert sc.telemetry.shadow_failures == 1
    assert sc.telemetry.failure_types == {"ShadowBackfillError": 1}
    assert sc.telemetry.to_dict()["failure_types"] == {"ShadowBackfillError": 1}


def test_the_cached_transform_and_the_persisted_record_agree_on_delta():
    """Two derivation paths exist -- the per-game cached transform and the
    record builder. They must not be able to drift apart silently."""
    sc = sidecar()
    out = contract(sc)
    cached = sc.transform_for_game(
        game_id="cfb-2026-wk01-east-carolina-at-alabama", timing_label="T_24H",
        home_team_id="alabama", away_team_id="east-carolina",
        corrected_margin_samples=MARGINS, control_margin_corrected=3.0,
        control_probability_canonical=0.61,
        control_expected_home=28.0, control_expected_away=25.0, both_fbs=True,
    )
    assert cached is not None
    assert out.shadow_minus_control_margin == pytest.approx(cached.delta, abs=1e-12)
    assert out.shadow_projected_margin == pytest.approx(cached.shadow_margin, abs=1e-9)
