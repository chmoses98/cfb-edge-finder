"""End-to-end CLI tests for scripts/build_cfb_baseline.py -- Milestone
C.2's closure/parity mission requirement 6 ("Verify CLI behavior") and
requirement 4's remaining CLI-observable properties (parity between
--margin-correction-method linear/none, provenance content, FBS-vs-FCS
status). Runs the ACTUAL script as a subprocess in fixture mode (no
network access needed, deterministic) rather than re-testing the
underlying logic already covered by
tests/test_modeling_score_model_margin_correction.py.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "build_cfb_baseline.py"

BASE_ARGS = [
    "--seasons",
    "2024",
    "2025",
    "--mode",
    "fixture",
    "--as-of-season",
    "2026",
    "--as-of-week",
    "1",
    "--home",
    "fixture-team-0",
    "--away",
    "fixture-team-1",
    "--n-simulations",
    "500",
    "--seed",
    "0",
]


def _run_cli(*extra_args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *BASE_ARGS, *extra_args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result.stdout


def _extract_projection_record(stdout: str) -> dict:
    idx = stdout.index("ProjectionRecord:")
    tail = stdout[idx + len("ProjectionRecord:") :]
    obj, _end = json.JSONDecoder().raw_decode(tail.strip())
    return obj


def _extract_field(stdout: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}: (.+)", stdout)
    assert match, f"could not find {label!r} line in CLI output"
    return match.group(1)


def test_live_projection_defaults_to_linear_margin_correction():
    out = _run_cli()
    assert "method=linear" in out
    assert "applied=True" in out


def test_disabling_correction_reproduces_pre_c2_behavior():
    corrected_out = _run_cli()
    none_out = _run_cli("--margin-correction-method", "none")

    corrected_total = _extract_field(corrected_out, "Expected total")
    none_total = _extract_field(none_out, "Expected total")
    # Same leading numeric value (the "(total_correction_method=none --
    # always unchanged)" suffix is identical text in both, so a plain
    # string-startswith comparison on the numeric prefix is exact).
    assert corrected_total.split(" ")[0] == none_total.split(" ")[0]

    corrected_win_prob = re.search(r"P\(home win\) = ([\d.]+)", corrected_out).group(1)
    none_win_prob = re.search(r"P\(home win\) = ([\d.]+)", none_out).group(1)
    assert corrected_win_prob == none_win_prob

    # Provenance for a --margin-correction-method none run must honestly
    # report "none", never the final selected model's "linear" default.
    none_record = _extract_projection_record(none_out)
    none_ratings_version = none_record["model_version"]["ratings_component_version"]
    assert "margin_correction_method=none" in none_ratings_version
    assert "margin_correction_artifact=None" in none_ratings_version

    corrected_record = _extract_projection_record(corrected_out)
    corrected_ratings_version = corrected_record["model_version"]["ratings_component_version"]
    assert "margin_correction_method=linear" in corrected_ratings_version


def test_corrected_and_uncorrected_expected_margins_genuinely_differ():
    # Sanity check that the "none" comparison above is actually meaningful
    # -- i.e. the correction has a real, nonzero effect for this matchup,
    # not a no-op that would make the parity test above trivially pass.
    corrected_out = _run_cli()
    none_out = _run_cli("--margin-correction-method", "none")
    corrected_margin = _extract_field(corrected_out, "Expected margin (corrected)").split(" ")[0]
    none_margin = _extract_field(none_out, "Expected margin (corrected)").split(" ")[0]
    assert corrected_margin != none_margin


def test_cli_prints_model_version_training_cutoff_and_research_only_status():
    out = _run_cli()
    assert "Model version: 0.4.0-milestone-c2-live-margin-correction" in out
    assert "Data/training cutoff for this projection's own ratings" in out
    assert "STATUS: RESEARCH-ONLY" in out
    for forbidden in ("stake", "bankroll", "kelly", "place_bet", "place_order", "real_money"):
        assert forbidden not in out.lower()


def test_provenance_records_correction_method_and_artifact_version():
    out = _run_cli()
    record = _extract_projection_record(out)
    ratings_version = record["model_version"]["ratings_component_version"]
    assert "margin_correction_method=linear" in ratings_version
    assert "margin_correction_artifact=c2-margin-linear-v1-2022-2025" in ratings_version
    assert record["model_version"]["model_version"] == "0.4.0-milestone-c2-live-margin-correction"


def test_fbs_vs_fcs_game_marked_unsupported_and_never_corrected():
    out = _run_cli("--away", "fixture-fcs-team", "--away-classification", "fcs")
    assert "UNSUPPORTED_FOR_PRICING" in out
    assert "skip_reason=not_fbs_vs_fbs" in out
    assert "applied=False" in out


def test_as_of_predating_training_cutoff_skips_correction_via_cli():
    out = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--seasons",
            "2024",
            "2025",
            "--mode",
            "fixture",
            "--as-of-season",
            "2025",
            "--as-of-week",
            "3",
            "--home",
            "fixture-team-0",
            "--away",
            "fixture-team-1",
            "--n-simulations",
            "500",
            "--seed",
            "0",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert out.returncode == 0
    assert "skip_reason=as_of_predates_training_cutoff" in out.stdout


def test_no_recommendation_or_staking_surface_in_new_closure_pass_scripts():
    forbidden = (
        "stake",
        "bankroll",
        "kelly",
        "place_order",
        "place_bet",
        "execute_trade",
        "execute_order",
        "real_money",
        "tier_a",
        "tier_b",
        "tier_c",
        "qualification_bar",
    )
    for path in (
        REPO_ROOT / "scripts" / "build_cfb_baseline.py",
        REPO_ROOT / "scripts" / "fit_margin_correction_artifact.py",
        REPO_ROOT / "src" / "cfb_edge_finder" / "modeling" / "margin_correction_artifact.py",
    ):
        text = path.read_text().lower()
        violations = [f for f in forbidden if f in text]
        assert violations == [], f"{path}: forbidden substrings found: {violations}"
