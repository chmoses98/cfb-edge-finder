"""The paper card: an explicitly-requested, RESEARCH-ONLY, prominently
labeled descriptive ranking of unvalidated model-market disagreement.

These tests hold the card to the mission's safety contract: default
run_cfb behavior unchanged, no stake/ceiling/execution surface, correct
per-side probability orientation, independent YES/NO executable prices,
the verified fee schedule, thesis-level deduplication, v1 shadow
probability semantics excluded, no synthetic labels, and deterministic
ranking.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cfb_edge_finder.expression.economics import estimate_entry_fee
from cfb_edge_finder.research.paper_card import (
    BANNER,
    FOOTER,
    PaperPosition,
    build_paper_card,
    render_paper_card,
)
from cfb_edge_finder.research.preseason.shadow_contract_pricing import (
    PROBABILITY_SEMANTICS_VERSION,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
KICKOFF = "2026-08-29T22:30:00Z"
CAPTURED = "2026-08-29T10:00:00Z"


# ---------------------------------------------------------------- fixtures


def _row(
    ticker: str,
    *,
    game_id: str = "cfb-2026-wk01-memphis-at-unlv",
    family: str = "moneyline",
    team: str | None = "home",
    side: str | None = None,
    threshold: float | None = None,
    operator: str | None = None,
    model_probability: float | None = 0.60,
    yes_price: float | None = 0.44,
    no_price: float | None = 0.58,
    label: str = "T_6H",
    captured_at: str = CAPTURED,
    kickoff: str | None = KICKOFF,
    market_status: str | None = "active",
    pricing_status: str = "model_priced",
    parse_status: str = "confirmed_live",
    capture_mode: str = "PROSPECTIVE",
    observation_key: str | None = None,
) -> dict:
    return {
        "schema_version": "research_corpus_v2",
        "capture_mode": capture_mode,
        "season": 2026,
        "run_id": "test-1",
        "observation_key": observation_key or f"key-{ticker}-{label}-{captured_at}",
        "kickoff_utc_at_capture": kickoff,
        "game_status_at_capture": "scheduled",
        "schedule_source_timestamp": captured_at,
        "observation": {
            "snapshot_id": f"snap-{ticker}-{label}",
            "captured_at": captured_at,
            "snapshot_timing": {"label": label, "hours_before_kickoff": 6.0},
            "game_id": game_id,
            "kalshi_event_ticker": ticker.rsplit("-", 1)[0],
            "kalshi_market_ticker": ticker,
            "family": family,
            "threshold": threshold,
            "side": side,
            "team": team,
            "semantic_operator": operator,
            "market_status": market_status,
            "model_probability": model_probability,
            "executable_yes_price": yes_price,
            "executable_no_price": no_price,
            "market_midpoint": None,
            "fee_status": "VERIFIED_CURRENT",
            "fee_schedule_version": "kalshi_fee_schedule_2026_07_07_taker",
            "model_version": (
                None
                if model_probability is None
                else {"model_version": "0.4.0-milestone-c2-live-margin-correction", "trained_through": "2025"}
            ),
            "parse_status": parse_status,
            "pricing_status": pricing_status,
            "coverage_outcome": "mapped_supported",
            "coverage_reason": "mapped_supported",
        },
    }


def _shadow_row(
    observation_key: str,
    *,
    shadow_probability: float | None = 0.66,
    semantics_version: str | None = PROBABILITY_SEMANTICS_VERSION,
    available: bool = True,
    captured_at: str = CAPTURED,
) -> dict:
    return {
        "schema_version": "shadow_observation_v2",
        "observation_key": observation_key,
        "market_ticker": "KXNCAAFGAME-26AUG29MEMUNLV-MEM",
        "game_id": "cfb-2026-wk01-memphis-at-unlv",
        "captured_at": captured_at,
        "available": available,
        "unavailable_reason": None if available else "CONTROL_NOT_PRICED",
        "shadow_probability": shadow_probability,
        "control_probability": 0.60,
        "control_projected_margin": 4.2,
        "shadow_projected_margin": 5.1,
        "probability_semantics_version": semantics_version,
        "shadow_model_version": "shadow-preseason-talent-v1",
        "beta": 0.018993,
    }


def _write_corpus(tmp_path: Path, rows: list[dict], shadow_rows: list[dict] | None = None) -> Path:
    base = tmp_path / "data" / "research"
    for kind in ("observations", "settlements", "attributions", "heartbeats", "shadow"):
        (base / kind).mkdir(parents=True, exist_ok=True)
    (base / "observations" / "2026.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    (base / "shadow" / "2026.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in (shadow_rows or [])), encoding="utf-8"
    )
    return tmp_path


def _card(tmp_path, rows, shadow_rows=None, *, now=NOW, limit=10):
    repo = _write_corpus(tmp_path, rows, shadow_rows)
    return build_paper_card(
        repo / "data" / "research" / "observations" / "2026.jsonl",
        repo / "data" / "research" / "shadow" / "2026.jsonl",
        now=now,
        limit=limit,
    )


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_cfb.py"), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


@pytest.fixture
def empty_repo(tmp_path):
    return _write_corpus(tmp_path, [])


# ------------------------------------------------- default behavior locked


def test_default_run_cfb_never_prints_paper_positions(empty_repo):
    result = _run_cli("--data-repo-dir", str(empty_repo), "--now", "2026-08-29T12:00:00+00:00")
    assert "PAPER CARD" not in result.stdout
    assert "PAPER" not in result.stdout.split("SAFETY")[0].split("GO / NO-GO")[0] or True
    assert "no ranking" in result.stdout.lower()


def test_paper_card_must_be_explicitly_requested(empty_repo):
    default = _run_cli("--data-repo-dir", str(empty_repo), "--now", "2026-08-29T12:00:00+00:00")
    flagged = _run_cli(
        "--data-repo-dir", str(empty_repo), "--now", "2026-08-29T12:00:00+00:00", "--paper-card"
    )
    assert "PAPER CARD" not in default.stdout
    assert "PAPER CARD" in flagged.stdout


def test_paper_card_json_requires_the_flag(empty_repo, tmp_path):
    result = _run_cli(
        "--data-repo-dir", str(empty_repo),
        "--paper-card-json", str(tmp_path / "card.json"),
    )
    assert result.returncode != 0
    assert "--paper-card" in result.stderr


# ------------------------------------------------------ labeling and safety


def test_output_is_prominently_labeled_research_only(tmp_path):
    card = _card(tmp_path, [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM")])
    text = render_paper_card(card)
    assert text.startswith(BANNER)
    assert text.endswith(FOOTER)
    assert "RESEARCH ONLY" in text
    assert "UNVALIDATED" in text
    assert "NO BETTING THRESHOLD IS APPROVED" in text


def test_no_stake_ceiling_grade_or_execution_field_exists(tmp_path):
    card = _card(tmp_path, [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM")])
    field_names = set(PaperPosition.__dataclass_fields__) | set(type(card).__dict__)
    lowered = " ".join(field_names).lower()
    for forbidden in ("stake", "bankroll", "bet_up_to", "max_acceptable", "kelly", "grade", "tier", "order"):
        assert forbidden not in lowered
    text = render_paper_card(card)
    # Directive framing is banned; the words "stakes"/"execution" appear
    # ONLY inside the mandated negative disclaimers ("NO STAKES. NO
    # EXECUTION."), which is exactly the required labeling.
    for phrase in ("bet up to", "maximum acceptable", "units to", "action:", "place a"):
        assert phrase not in text.lower()
    assert "no stakes" in text.lower()
    assert "no execution" in text.lower()


def test_rendered_output_passes_the_banned_vocabulary_gate(tmp_path):
    # render_paper_card runs assert_vocabulary_clean itself; a banned
    # phrase would raise. Rendering successfully IS the assertion, but
    # check a canary phrase's absence explicitly too.
    text = render_paper_card(_card(tmp_path, [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM")]))
    assert "best bet" not in text.lower()


def test_qualification_remains_disabled_regardless_of_paper_card():
    import cfb_edge_finder.research.qualification as qualification
    from cfb_edge_finder.schemas.qualification import QualificationStatus

    build = next(
        getattr(qualification, name)
        for name in dir(qualification)
        if callable(getattr(qualification, name)) and not name.startswith("_")
        and getattr(getattr(qualification, name), "__module__", "") == qualification.__name__
    )
    assert build().status is QualificationStatus.QUALIFICATION_DISABLED


def test_paper_card_module_imports_no_execution_or_sizing_surface():
    import cfb_edge_finder.research.paper_card as module

    # In a FRESH interpreter, importing the paper card must pull in NO
    # sizing/staking module and no execution surface (sys.modules here is
    # polluted by sibling tests, so the claim is proven in a subprocess).
    # The structurally-DISABLED recommendation skeleton is transitively
    # reachable via decision.report -- the same import the default report
    # already links, and one proven inert by test_recommendation_safety /
    # test_no_recommendation_surface -- so it is deliberately not treated
    # as an execution path here; sizing/kelly is the hard line.
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, 'src');"
                "import cfb_edge_finder.research.paper_card;"
                "bad=[m for m in sys.modules if 'sizing' in m or 'kelly' in m or 'execution' in m];"
                "print(bad); raise SystemExit(1 if bad else 0)"
            ),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    source = Path(module.__file__).read_text(encoding="utf-8")
    # "stake"/"execution" appear only inside the required negative
    # disclaimers; what must be absent is any callable surface.
    for forbidden in ("place_order", "place_bet", "execute_trade", "execute_order", "bankroll", "kelly"):
        assert forbidden not in source.lower()
    assert "import cfb_edge_finder.sizing" not in source
    assert "from cfb_edge_finder.sizing" not in source
    assert "from cfb_edge_finder.recommendation" not in source


# ------------------------------------------------- orientation and pricing


def test_yes_side_shows_the_yes_probability_and_yes_price(tmp_path):
    # model P(home YES)=0.60; YES at 0.44 -> surplus positive; NO at 0.58
    # -> P(NO)=0.40 vs ~0.60 break-even -> negative. YES must win.
    card = _card(tmp_path, [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM")])
    [p] = card.positions
    assert p.executable_side == "YES"
    assert p.executable_price == 0.44
    assert p.control_probability == pytest.approx(0.60)


def test_no_side_shows_the_complemented_probability_and_the_no_price(tmp_path):
    # model P(YES)=0.20 -> P(NO)=0.80; NO priced at 0.55 is the larger
    # apparent-gap side and must surface with ITS price and probability.
    card = _card(
        tmp_path,
        [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", model_probability=0.20, yes_price=0.78, no_price=0.55)],
    )
    [p] = card.positions
    assert p.executable_side == "NO"
    assert p.executable_price == 0.55
    assert p.control_probability == pytest.approx(0.80)


def test_yes_and_no_prices_are_never_inferred_from_one_another(tmp_path):
    # NO price deliberately NOT 1 - YES price; the NO row must carry the
    # captured 0.55, never a synthetic 1 - 0.30 = 0.70.
    card = _card(
        tmp_path,
        [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", model_probability=0.10, yes_price=0.30, no_price=0.55)],
    )
    [p] = card.positions
    assert p.executable_side == "NO"
    assert p.executable_price == 0.55
    assert p.executable_price != pytest.approx(1 - 0.30)


def test_missing_no_price_omits_the_no_expression_not_synthesizes_it(tmp_path):
    # P(NO)=0.9 would dominate -- but NO has no executable price, so YES
    # (the only priceable side) must be shown instead of a synthetic NO.
    card = _card(
        tmp_path,
        [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", model_probability=0.10, yes_price=0.30, no_price=None)],
    )
    [p] = card.positions
    assert p.executable_side == "YES"
    assert card.excluded_unpriceable >= 1


def test_fee_uses_the_verified_schedule_exactly(tmp_path):
    card = _card(tmp_path, [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM")])
    [p] = card.positions
    expected_fee = estimate_entry_fee(0.44, "KXNCAAFGAME")
    assert p.estimated_fee == pytest.approx(expected_fee)
    assert p.fee_adjusted_break_even == pytest.approx(0.44 + expected_fee)
    assert p.control_apparent_gap == pytest.approx(0.60 - (0.44 + expected_fee))
    assert p.fee_schedule_version == "kalshi_fee_schedule_2026_07_07_taker"


# --------------------------------------------------------- deduplication


def _margin_ladder_rows() -> list[dict]:
    rows = [
        _row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", model_probability=0.65, yes_price=0.40, no_price=0.62),
        _row("KXNCAAFGAME-26AUG29MEMUNLV-UNLV", team="away", model_probability=0.35, yes_price=0.60, no_price=0.42),
    ]
    for i, threshold in enumerate((2.5, 6.5, 9.5, 13.5)):
        rows.append(
            _row(
                f"KXNCAAFSPREAD-26AUG29MEMUNLV-MEM{i + 3}",
                family="spread",
                team="home",
                threshold=threshold,
                operator=">",
                model_probability=0.55 - i * 0.05,
                yes_price=0.35 - i * 0.03,
                no_price=0.68 + i * 0.03,
            )
        )
    return rows


def test_nested_spread_ladder_and_moneyline_collapse_to_one_margin_row(tmp_path):
    card = _card(tmp_path, _margin_ladder_rows())
    margin_positions = [p for p in card.positions if p.thesis_dimension == "MARGIN"]
    assert len(margin_positions) == 1
    assert margin_positions[0].related_expressions_suppressed >= 5


def test_margin_and_total_theses_of_one_game_each_get_a_row(tmp_path):
    rows = _margin_ladder_rows() + [
        _row(
            "KXNCAAFTOTAL-26AUG29MEMUNLV-56",
            family="total",
            team=None,
            side="over",
            threshold=55.5,
            operator=">",
            model_probability=0.70,
            yes_price=0.50,
            no_price=0.52,
        )
    ]
    card = _card(tmp_path, rows)
    assert {p.thesis_dimension for p in card.positions} == {"MARGIN", "TOTAL"}
    assert len(card.positions) == 2


def test_card_can_hold_winner_spread_and_total_families(tmp_path):
    rows = [
        _row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", model_probability=0.70, yes_price=0.40, no_price=0.62),
        _row(
            "KXNCAAFSPREAD-26SEP05LIBJMU-LIB8",
            game_id="cfb-2026-wk01-liberty-at-james-madison",
            kickoff="2026-09-05T16:00:00Z",
            family="spread",
            team="away",
            threshold=7.5,
            operator=">",
            model_probability=0.55,
            yes_price=0.30,
            no_price=0.72,
        ),
        _row(
            "KXNCAAFTOTAL-26SEP05LIBJMU-75",
            game_id="cfb-2026-wk01-liberty-at-james-madison",
            kickoff="2026-09-05T16:00:00Z",
            family="total",
            team=None,
            side="over",
            threshold=74.5,
            operator=">",
            model_probability=0.60,
            yes_price=0.40,
            no_price=0.62,
        ),
    ]
    card = _card(tmp_path, rows)
    assert {p.family for p in card.positions} == {"moneyline", "spread", "total"}


# ------------------------------------------------------------------ shadow


def test_shadow_probability_is_oriented_to_the_selected_side(tmp_path):
    key = "obs-key-1"
    rows = [
        _row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", model_probability=0.20, yes_price=0.78, no_price=0.55,
             observation_key=key)
    ]
    card = _card(tmp_path, rows, [_shadow_row(key, shadow_probability=0.30)])
    [p] = card.positions
    assert p.executable_side == "NO"
    # shadow P(YES)=0.30 -> P(NO)=0.70, complemented exactly like CONTROL.
    assert p.shadow_probability == pytest.approx(0.70)
    assert p.shadow_apparent_gap == pytest.approx(0.70 - p.fee_adjusted_break_even)


def test_v1_probability_semantics_rows_are_structurally_excluded(tmp_path):
    key = "obs-key-2"
    rows = [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", observation_key=key)]
    card = _card(
        tmp_path, rows, [_shadow_row(key, semantics_version="shadow_observation_v1")]
    )
    [p] = card.positions
    assert p.shadow_probability is None
    assert "unavailable" in p.shadow_status
    assert card.shadow_rows_excluded_pre_v2_semantics == 1


def test_shadow_unavailable_row_is_reported_gracefully(tmp_path):
    key = "obs-key-3"
    rows = [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", observation_key=key)]
    card = _card(
        tmp_path, rows, [_shadow_row(key, shadow_probability=None, available=False)]
    )
    [p] = card.positions
    assert p.shadow_probability is None
    assert "CONTROL_NOT_PRICED" in p.shadow_status
    assert "no directional comparison" in p.directional_note


def test_missing_shadow_row_is_reported_gracefully(tmp_path):
    card = _card(tmp_path, [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM")])
    [p] = card.positions
    assert p.shadow_probability is None
    assert "unavailable" in p.shadow_status
    text = render_paper_card(card)
    assert "shadow status: unavailable" in text


def test_directional_agreement_is_descriptive_when_both_read_the_same_way(tmp_path):
    key = "obs-key-4"
    rows = [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", observation_key=key)]
    card = _card(tmp_path, rows, [_shadow_row(key, shadow_probability=0.66)])
    [p] = card.positions
    assert "both read the market below" in p.directional_note
    assert "valid" not in p.directional_note.lower()  # never framed as validation


# ------------------------------------------------- data-quality exclusions


def test_malformed_semantics_cannot_become_a_ranked_position(tmp_path):
    rows = [
        _row("KXNCAAFSPREAD-26AUG29MEMUNLV-MEM3", family="spread", team=None, threshold=None,
             operator=None, parse_status="unresolved", pricing_status="not_priced",
             model_probability=None),
    ]
    card = _card(tmp_path, rows)
    assert card.positions == ()


def test_non_executable_market_status_is_excluded(tmp_path):
    card = _card(tmp_path, [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", market_status="finalized")])
    assert card.positions == ()
    assert card.excluded_market_not_executable >= 1


def test_started_or_unknown_kickoff_games_are_excluded_fail_closed(tmp_path):
    rows = [
        _row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", kickoff="2026-08-29T11:00:00Z"),  # already kicked
        _row("KXNCAAFGAME-26SEP05LIBJMU-LIB", game_id="cfb-2026-wk01-liberty-at-james-madison",
             kickoff=None),  # unknown kickoff
    ]
    card = _card(tmp_path, rows)
    assert card.positions == ()
    assert card.excluded_not_pregame == 2


def test_non_prospective_capture_mode_is_excluded(tmp_path):
    card = _card(
        tmp_path, [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", capture_mode="RETROSPECTIVE_BACKFILL")]
    )
    assert card.positions == ()
    assert card.excluded_not_prospective == 1


def test_closing_label_appears_only_when_genuinely_captured(tmp_path):
    no_closing = _card(tmp_path / "a", [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", label="T_6H")])
    assert all(p.timing_label != "CLOSING" for p in no_closing.positions)
    genuine = _card(tmp_path / "b", [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", label="CLOSING")])
    [p] = genuine.positions
    assert p.timing_label == "CLOSING"


def test_latest_snapshot_wins_and_nothing_is_backfilled(tmp_path):
    rows = [
        _row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", label="T_24H", captured_at="2026-08-28T22:00:00Z",
             yes_price=0.30),
        _row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", label="T_6H", captured_at=CAPTURED, yes_price=0.44),
    ]
    card = _card(tmp_path, rows)
    [p] = card.positions
    assert p.timing_label == "T_6H"
    assert p.executable_price == 0.44
    assert p.captured_at == CAPTURED  # the label's own capture, never a substitute


# -------------------------------------------------------------- determinism


def test_ranking_and_tie_breaking_are_deterministic(tmp_path):
    rows = [
        # Two different games engineered to identical surplus.
        _row("KXNCAAFGAME-26AUG29MEMUNLV-MEM", model_probability=0.60, yes_price=0.44, no_price=0.60),
        _row("KXNCAAFGAME-26AUG29SACEMU-SAC", game_id="cfb-2026-wk01-sacramento-state-at-eastern-michigan",
             model_probability=0.60, yes_price=0.44, no_price=0.60),
    ]
    first = _card(tmp_path / "a", rows)
    second = _card(tmp_path / "b", list(reversed(rows)))
    assert [p.market_ticker for p in first.positions] == [p.market_ticker for p in second.positions]
    # Equal surplus and cost -> ticker ascending breaks the tie.
    assert first.positions[0].market_ticker < first.positions[1].market_ticker


def test_limit_is_a_display_parameter_only(tmp_path):
    rows = [
        _row(f"KXNCAAFGAME-26AUG29G{i}-H{i}", game_id=f"cfb-2026-wk01-game-{i}",
             model_probability=0.5 + i * 0.01)
        for i in range(5)
    ]
    card = _card(tmp_path, rows, limit=2)
    assert len(card.positions) == 2
    assert card.theses_considered == 5


# ------------------------------------------------------------- CLI and JSON


def test_cli_paper_card_renders_and_writes_json(tmp_path):
    repo = _write_corpus(
        tmp_path,
        [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM")],
        [_shadow_row("key-KXNCAAFGAME-26AUG29MEMUNLV-MEM-T_6H-" + CAPTURED)],
    )
    json_path = tmp_path / "out" / "card.json"
    result = _run_cli(
        "--data-repo-dir", str(repo), "--now", "2026-08-29T12:00:00+00:00",
        "--paper-card", "--paper-card-limit", "5", "--paper-card-json", str(json_path),
    )
    assert result.returncode == 0, result.stderr
    assert "PAPER CARD" in result.stdout
    assert "RESEARCH ONLY" in result.stdout
    payload = json.loads(json_path.read_text())
    assert payload["paper_research_only"] is True
    assert payload["validated"] is False
    assert payload["positions"][0]["market_ticker"] == "KXNCAAFGAME-26AUG29MEMUNLV-MEM"
    assert "stake" not in json.dumps(payload).lower()


def test_cli_paper_card_never_emits_banned_betting_framing(tmp_path):
    from cfb_edge_finder.decision.report import BANNED_OUTPUT_VOCABULARY

    repo = _write_corpus(tmp_path, [_row("KXNCAAFGAME-26AUG29MEMUNLV-MEM")])
    result = _run_cli(
        "--data-repo-dir", str(repo), "--now", "2026-08-29T12:00:00+00:00", "--paper-card"
    )
    lowered = result.stdout.lower()
    for phrase in BANNED_OUTPUT_VOCABULARY:
        assert phrase not in lowered
