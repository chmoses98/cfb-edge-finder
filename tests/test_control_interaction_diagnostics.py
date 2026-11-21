"""CONTROL interaction diagnostics are descriptive, frozen-claim-only and sample-safe."""

from __future__ import annotations

from cfb_edge_finder.scripting.control_interactions import build_control_interaction_diagnostics
from cfb_edge_finder.scripting.realized_v2 import build_report_v2, render_markdown_v2


def row(
    game: str,
    *,
    side: str = "home",
    strength: str = "MODERATE",
    won: bool | None = True,
    margin: float = 7,
    pace: dict | None = None,
    scoring: dict | None = None,
    suppression: dict | None = None,
    disruption: list[dict] | None = None,
    time: str = "2026-10-01T15:00:00Z",
    kind: str = "FINAL_PREGAME",
) -> dict:
    return {
        "kind": kind,
        "game_key": game,
        "recorded_at": time,
        "control": {
            "side": side,
            "strength": strength,
            "tier": f"{side.upper()}_CONTROL_{strength}",
            "control_margin": margin,
            "won": won,
            "range": {"status": "UNAVAILABLE"},
        },
        "pace": pace,
        "scoring_environment": scoring,
        "defensive_suppression": suppression,
        "disruption": disruption or [],
        "closeness": None,
    }


def test_moderate_control_interactions_keep_home_away_and_missing_separate():
    home_win = row(
        "g1", pace={"level": "HIGH"}, scoring={"level": "ELEVATED"},
        suppression={"realized_label": True}, disruption=[{"side": "home"}], margin=12,
    )
    home_loss = row("g2", won=False, margin=-6, disruption=[{"side": "away"}])
    away_missing = row(
        "g3", side="away", won=None, margin=0, pace={"status": "UNAVAILABLE"},
        scoring={"status": "UNAVAILABLE"}, suppression={"status": "UNAVAILABLE"},
        disruption=[{"side": "home"}, {"side": "away"}],
    )
    report = build_control_interaction_diagnostics([home_win, home_loss, away_missing])
    assert report["total_control_games"] == 3
    assert report["status"] == "DESCRIPTIVE_ONLY_NO_VALIDATED_MARKET_EDGE"
    home = report["by_control_tier"]["HOME_CONTROL_MODERATE"]
    assert (home["n"], home["wins"], home["losses"], home["mean_control_margin"]) == (2, 1, 1, 3)
    away = report["by_control_tier"]["AWAY_CONTROL_MODERATE"]
    assert (away["n"], away["undecided"], away["win_rate"]) == (1, 1, None)
    assert report["moderators"]["pace"]["HIGH"]["HOME_CONTROL_MODERATE"]["wins"] == 1
    assert report["moderators"]["pace"]["NO_CLAIM"]["HOME_CONTROL_MODERATE"]["n"] == 1
    assert report["moderators"]["pace"]["UNAVAILABLE"]["AWAY_CONTROL_MODERATE"]["n"] == 1
    assert report["moderators"]["scoring_environment"]["ELEVATED"]["HOME_CONTROL_MODERATE"]["n"] == 1
    assert report["moderators"]["scoring_environment"]["UNAVAILABLE"]["AWAY_CONTROL_MODERATE"]["n"] == 1
    assert report["moderators"]["defensive_suppression"]["CLAIM_PRESENT"]["HOME_CONTROL_MODERATE"]["n"] == 1
    assert report["moderators"]["defensive_suppression"]["UNAVAILABLE"]["AWAY_CONTROL_MODERATE"]["n"] == 1
    assert report["moderators"]["disruption_alignment"]["ALIGNED_ONLY"]["HOME_CONTROL_MODERATE"]["n"] == 1
    assert report["moderators"]["disruption_alignment"]["OPPOSING_ONLY"]["HOME_CONTROL_MODERATE"]["n"] == 1
    assert report["moderators"]["disruption_alignment"]["BOTH"]["AWAY_CONTROL_MODERATE"]["n"] == 1


def test_last_frozen_pregame_row_only_and_no_publication_row():
    older = row("a", won=False, margin=-12, time="2026-09-30T14:00:00Z")
    newer = row("a", won=True, margin=8, time="2026-10-01T15:00:00Z")
    publication = row("b", kind="PUBLICATION")
    forward = build_control_interaction_diagnostics([newer, older, publication])
    reverse = build_control_interaction_diagnostics([publication, older, newer])
    assert forward == reverse
    assert forward["total_control_games"] == 1
    assert forward["by_control_tier"]["HOME_CONTROL_MODERATE"]["wins"] == 1


def test_small_sample_is_not_promoted_to_signal_even_at_thirty():
    items = [row(f"g{i}", won=(i % 2 == 0)) for i in range(30)]
    report = build_control_interaction_diagnostics(items)
    assert report["by_control_tier"]["HOME_CONTROL_MODERATE"]["sample"] == "DESCRIPTIVE"
    assert report["status"] == "DESCRIPTIVE_ONLY_NO_VALIDATED_MARKET_EDGE"
    assert all("NO ROI" not in str(v) for v in report["moderators"].values())


def test_v2_report_carries_interactions_without_changing_control_score():
    a = row("a", pace={"level": "LOW", "direction_held": False})
    b = row("b", won=False, margin=-7)
    output = build_report_v2([], [a, b])
    assert output["schema"] == "cfb_script_engine_v2_report/1.1.0"
    assert output["control"]["HOME_CONTROL_MODERATE"]["wins"] == 1
    assert output["control_interactions"]["total_control_games"] == 2
    md = render_markdown_v2(output)
    assert "CONTROL interactions (descriptive only)" in md
    assert "pace | LOW | HOME_CONTROL_MODERATE | 1 | 1" in md


def test_absent_control_and_non_final_rows_cannot_pad_denominator():
    no_control = row("other")
    no_control["control"] = None
    strong = row("strong", side="away", strength="STRONG", won=False)
    report = build_control_interaction_diagnostics([no_control, strong, row("old", kind="PUBLICATION")])
    assert report["total_control_games"] == 1
    assert set(report["by_control_tier"]) == {"AWAY_CONTROL_STRONG"}
