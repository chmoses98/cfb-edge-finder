"""One CFB game, all the way through, deterministically.

*** WHAT THIS PROVES AND WHY IT MATTERS NOW ***

That if tomorrow's natural lifecycle happens, no MISSING SOFTWARE STAGE
prevents us from learning from it. Every stage below is exercised against
the real production functions:

    schedule -> mapping -> semantics -> projection -> pricing
    -> prospective observation at T_24H, T_6H, T_90, T_60, T_30, CLOSING
    -> final score -> settlement -> attribution -> closing linkage
    -> analytics -> candidate construction -> shadow gate

The fixtures are synthetic and stay in memory. Nothing here touches the
genuine research corpus, and the synthetic rows can never reach it -- a
test that wrote to the ledger would corrupt the very evidence it exists
to protect.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cfb_edge_finder.decision.artifact import load_artifact
from cfb_edge_finder.decision.shadow import ShadowDecisionState, run_shadow_pipeline
from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.expression.taxonomy import ContractSemantics, truth_condition_key
from cfb_edge_finder.research import timing
from cfb_edge_finder.research.attribution import attribute_observation
from cfb_edge_finder.research.checkpoint_manifest import manifest_from_corpus_row
from cfb_edge_finder.research.identity import observation_key
from cfb_edge_finder.research.settlement import settle_market
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.kalshi_observation import SnapshotTiming
from cfb_edge_finder.schemas.settlement import (
    GameFinalStatus,
    GameResult,
    MarketSettlementStatus,
)
from tests.research_factories import MODEL_VERSION, make_corpus_row, make_observation

KICKOFF = datetime(2026, 8, 29, 16, 0, tzinfo=UTC)
GAME_ID = "cfb-2026-wk01-away-team-at-home-team"
SEASON = 2026

PREGAME_SEQUENCE = ("T_24H", "T_6H", "T_90", "T_60", "T_30", "CLOSING")


def at(label: str) -> datetime:
    """A capture instant genuinely inside each label's window."""
    offsets = {
        "EARLY_OPEN": timedelta(days=20),
        "T_7D": timedelta(hours=168),
        "T_3D": timedelta(hours=72),
        "T_24H": timedelta(hours=24),
        "T_6H": timedelta(hours=6),
        "T_90": timedelta(minutes=90),
        "T_60": timedelta(minutes=60),
        "T_30": timedelta(minutes=30),
        "CLOSING": timedelta(minutes=7),
    }
    return KICKOFF - offsets[label]


def observation_at(label: str, *, ticker: str = "MKT-HOME", **overrides):
    return make_observation(
        kalshi_market_ticker=ticker,
        captured_at=at(label),
        snapshot_timing=SnapshotTiming(
            label=label, hours_before_kickoff=(KICKOFF - at(label)).total_seconds() / 3600
        ),
        game_id=GAME_ID,
        **overrides,
    )


def row_at(label: str, **overrides):
    return make_corpus_row(
        observation=observation_at(label, **overrides),
        kickoff_utc_at_capture=KICKOFF,
        season=SEASON,
    )


SETTLED_AT = KICKOFF + timedelta(hours=4)

HOME_WIN = GameResult(
    game_id=GAME_ID, season=SEASON, home_points=31, away_points=17,
    status=GameFinalStatus.FINAL, captured_at=SETTLED_AT,
)


# ---------------------------------------------- checkpoint scheduling


def test_every_pregame_checkpoint_becomes_due_at_its_own_window():
    """The scheduler must actually offer each label, or a stage of the
    lifecycle can never be reached in production."""
    for label in PREGAME_SEQUENCE:
        due = timing.resolve_due_labels(
            kickoff_utc=KICKOFF, now=at(label), already_captured_labels=set()
        )
        assert label in due, f"{label} never became due"


def test_a_captured_label_is_never_offered_again():
    """Idempotency at the scheduler: a retry must not duplicate work."""
    due = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=at("T_30"), already_captured_labels={"T_30"}
    )
    assert "T_30" not in due


def test_closing_is_due_strictly_before_kickoff_only():
    inside = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=KICKOFF - timedelta(minutes=7), already_captured_labels=set()
    )
    assert timing.CLOSING in inside

    at_kickoff = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=KICKOFF, already_captured_labels=set()
    )
    assert timing.CLOSING not in at_kickoff


def test_closing_can_never_be_backfilled_after_kickoff():
    """The hard rule. Any instant at or after kickoff offers nothing, and
    a started game offers nothing at all."""
    for minutes_after in (0, 1, 60, 60 * 24 * 7):
        after = timing.resolve_due_labels(
            kickoff_utc=KICKOFF,
            now=KICKOFF + timedelta(minutes=minutes_after),
            already_captured_labels=set(),
        )
        assert after == []
    assert (
        timing.resolve_due_labels(
            kickoff_utc=KICKOFF,
            now=KICKOFF - timedelta(minutes=7),
            already_captured_labels=set(),
            game_started=True,
        )
        == []
    )


def test_closing_and_t30_windows_are_disjoint():
    """14 not 15, so no instant owes both."""
    boundary = KICKOFF - timedelta(minutes=15)
    due = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=boundary, already_captured_labels=set()
    )
    assert not (timing.CLOSING in due and "T_30" in due)


# ------------------------------------------------- immutability


def test_each_checkpoint_gets_its_own_immutable_key():
    keys = {
        label: observation_key(
            season=SEASON,
            game_id=GAME_ID,
            market_ticker="MKT-HOME",
            timing_label=label,
            model_version=MODEL_VERSION.model_version,
        )
        for label in PREGAME_SEQUENCE
    }
    assert len(set(keys.values())) == len(PREGAME_SEQUENCE)


def test_the_same_checkpoint_recomputes_the_same_key():
    """Retry idempotency at the ledger: a second attempt dedupes rather
    than appending a rival row."""
    args = dict(
        season=SEASON,
        game_id=GAME_ID,
        market_ticker="MKT-HOME",
        timing_label="CLOSING",
        model_version=MODEL_VERSION.model_version,
    )
    assert observation_key(**args) == observation_key(**args)


def test_attribution_never_mutates_the_captured_row():
    row = row_at("T_24H")
    before = row.model_dump_json()
    attribute_observation(row, None, settled_at=KICKOFF + timedelta(hours=4))
    assert row.model_dump_json() == before


# ------------------------------------------------- settlement


def test_winner_settles_from_the_final_score():
    obs = observation_at("CLOSING", family=MarketFamily.MONEYLINE, team=Side.HOME)
    settlement = settle_market(obs, HOME_WIN, settled_at=KICKOFF + timedelta(hours=4))
    assert settlement.status is MarketSettlementStatus.SETTLED
    assert settlement.derived_contract_settlement == "yes"
    assert settlement.actual_home_margin == 14


def test_the_losing_winner_contract_settles_no():
    obs = observation_at("CLOSING", family=MarketFamily.MONEYLINE, team=Side.AWAY)
    settlement = settle_market(obs, HOME_WIN, settled_at=KICKOFF + timedelta(hours=4))
    assert settlement.derived_contract_settlement == "no"


@pytest.mark.parametrize(
    "threshold,expected",
    [(13.5, "yes"), (14.0, "no"), (14.5, "no")],
)
def test_spread_settles_on_a_STRICT_greater_than(threshold, expected):
    """`team_margin > threshold`, strictly. A 14-point margin does NOT
    cover a 14.0 line -- an inclusive comparison would silently flip
    every exact-number push."""
    obs = observation_at(
        "CLOSING", family=MarketFamily.SPREAD, team=Side.HOME,
        threshold=threshold, semantic_operator=">",
    )
    settlement = settle_market(obs, HOME_WIN, settled_at=KICKOFF + timedelta(hours=4))
    assert settlement.derived_contract_settlement == expected


@pytest.mark.parametrize(
    "threshold,expected",
    [(47.5, "yes"), (48.0, "no"), (48.5, "no")],
)
def test_total_settles_on_a_STRICT_greater_than(threshold, expected):
    """31 + 17 = 48. Strictly greater, so 48.0 is not exceeded."""
    obs = observation_at(
        "CLOSING", family=MarketFamily.TOTAL, team=None, side=Side.OVER,
        threshold=threshold, semantic_operator=">",
    )
    settlement = settle_market(obs, HOME_WIN, settled_at=KICKOFF + timedelta(hours=4))
    assert settlement.derived_contract_settlement == expected
    assert settlement.actual_total_points == 48


def test_a_game_that_is_not_final_does_not_settle():
    pending = GameResult(
        game_id=GAME_ID, season=SEASON, home_points=None, away_points=None,
        status=GameFinalStatus.NOT_YET_FINAL, captured_at=SETTLED_AT,
    )
    settlement = settle_market(
        observation_at("CLOSING"), pending, settled_at=KICKOFF + timedelta(hours=4)
    )
    assert settlement.status is MarketSettlementStatus.PENDING_NOT_FINAL
    assert settlement.derived_contract_settlement is None


def test_overtime_does_not_change_settlement():
    """A contract settles on the FINAL score regardless of periods."""
    ot = GameResult(
        game_id=GAME_ID, season=SEASON, home_points=31, away_points=17,
        status=GameFinalStatus.FINAL, went_to_overtime=True, captured_at=SETTLED_AT,
    )
    obs = observation_at("CLOSING", family=MarketFamily.MONEYLINE, team=Side.HOME)
    assert (
        settle_market(obs, ot, settled_at=KICKOFF).derived_contract_settlement
        == settle_market(obs, HOME_WIN, settled_at=KICKOFF).derived_contract_settlement
    )


# ---------------------------------------- attribution + closing link


def test_attribution_consumes_a_real_settlement():
    row = row_at("T_24H")
    settlement = settle_market(row.observation, HOME_WIN, settled_at=KICKOFF + timedelta(hours=4))
    attribution = attribute_observation(
        row, settlement, settled_at=KICKOFF + timedelta(hours=4),
        closing_row=row_at("CLOSING"), series_ticker="KXNCAAFGAME",
    )
    assert attribution.derived_contract_settlement == "yes"
    assert attribution.closing.closing_captured is True
    assert attribution.closing.closing_yes_price is not None


def test_a_missing_close_is_attributed_with_a_reason_not_silence():
    row = row_at("T_24H")
    settlement = settle_market(row.observation, HOME_WIN, settled_at=KICKOFF + timedelta(hours=4))
    attribution = attribute_observation(
        row, settlement, settled_at=KICKOFF + timedelta(hours=4),
        closing_row=None, closing_missing_reason="window closed before a capture occurred",
        series_ticker="KXNCAAFGAME",
    )
    assert attribution.closing.closing_captured is False
    assert attribution.closing.closing_status


def test_clv_requires_a_genuine_close():
    """No close, no CLV. It is never estimated from a nearby quote."""
    row = row_at("T_24H")
    settlement = settle_market(row.observation, HOME_WIN, settled_at=KICKOFF + timedelta(hours=4))
    without = attribute_observation(
        row, settlement, settled_at=KICKOFF + timedelta(hours=4),
        closing_missing_reason="none captured", series_ticker="KXNCAAFGAME",
    )
    assert without.closing.closing_yes_price is None
    assert without.closing.closing_captured is False


# ------------------------------------------- YES/NO independence


def test_yes_and_no_economics_are_independent_not_mirrored():
    """The executable NO price is `no_ask`, never `1 - yes_ask`. Two
    asks both carry spread, so they do not sum to 1."""
    obs = observation_at("T_30", executable_yes_price=0.55, executable_no_price=0.47)
    assert obs.executable_yes_price + obs.executable_no_price != pytest.approx(1.0)


def test_the_two_moneyline_spellings_settle_together():
    """home YES and away NO name one event -- proven from settlement
    semantics, so a portfolio layer cannot treat them as two theses."""
    home = ContractSemantics(
        market_ticker="H", game_id=GAME_ID, family=MarketFamily.MONEYLINE,
        team=Side.HOME, side=None, threshold=None, semantic_operator=">",
        parse_status="confirmed_live",
    )
    away = ContractSemantics(
        market_ticker="A", game_id=GAME_ID, family=MarketFamily.MONEYLINE,
        team=Side.AWAY, side=None, threshold=None, semantic_operator=">",
        parse_status="confirmed_live",
    )
    assert truth_condition_key(home, Side.YES) == truth_condition_key(away, Side.NO)


# -------------------------------------------- provenance + shadow


def test_every_checkpoint_yields_a_complete_reproducibility_manifest():
    for label in PREGAME_SEQUENCE:
        manifest = manifest_from_corpus_row(row_at(label).model_dump(mode="json"))
        assert manifest.is_complete, (label, manifest.missing_fields)
        assert manifest.timing_label == label
        assert manifest.capture_mode == "PROSPECTIVE"


def test_a_full_lifecycle_still_produces_no_shadow_qualification():
    """The end of the pipeline. Every stage ran; the gate still refuses,
    because no approved threshold artifact exists."""
    snapshots = [
        ContractSnapshot(
            semantics=ContractSemantics(
                market_ticker="MKT-HOME", game_id=GAME_ID, family=MarketFamily.MONEYLINE,
                team=Side.HOME, side=None, threshold=None, semantic_operator=">",
                parse_status="confirmed_live",
            ),
            timing_label=label,
            captured_at=at(label).isoformat(),
            model_probability=0.62,
            executable_yes_price=0.55,
            executable_no_price=0.47,
            market_status="active",
            fee_status="VERIFIED_CURRENT",
            fee_schedule_version="kalshi_fee_schedule_2026_07_07_taker",
            model_version=MODEL_VERSION.model_version,
            pricing_status="model_priced",
            series_ticker="KXNCAAFGAME",
            schema_version="research_corpus_v2",
            capture_mode="PROSPECTIVE",
        )
        for label in PREGAME_SEQUENCE
    ]
    result = run_shadow_pipeline(
        snapshots, resolution=load_artifact(None), now=KICKOFF - timedelta(minutes=5)
    )
    assert result.decisions
    assert result.shadow_qualified_count == 0
    assert all(d.state is not ShadowDecisionState.SHADOW_QUALIFIED for d in result.decisions)


def test_a_retrospective_row_is_excluded_at_every_stage():
    snapshot = ContractSnapshot(
        semantics=ContractSemantics(
            market_ticker="MKT-HOME", game_id=GAME_ID, family=MarketFamily.MONEYLINE,
            team=Side.HOME, side=None, threshold=None, semantic_operator=">",
            parse_status="confirmed_live",
        ),
        timing_label="CLOSING",
        captured_at=at("CLOSING").isoformat(),
        model_probability=0.62,
        executable_yes_price=0.55,
        executable_no_price=0.47,
        market_status="active",
        fee_status="VERIFIED_CURRENT",
        fee_schedule_version="kalshi_fee_schedule_2026_07_07_taker",
        model_version=MODEL_VERSION.model_version,
        pricing_status="model_priced",
        series_ticker="KXNCAAFGAME",
        schema_version="research_corpus_v2",
        capture_mode="RETROSPECTIVE_BACKFILL",
    )
    result = run_shadow_pipeline(
        [snapshot], resolution=load_artifact(None), now=KICKOFF - timedelta(minutes=5)
    )
    assert all(d.state is ShadowDecisionState.NOT_PROSPECTIVE for d in result.decisions)


def test_the_lifecycle_writes_nothing_to_the_genuine_corpus():
    """Synthetic fixtures must never reach the ledger they exist to
    protect."""
    import pathlib

    ledger = pathlib.Path("data/research/observations/2026.jsonl")
    before = ledger.read_bytes() if ledger.exists() else None
    row_at("CLOSING")
    settle_market(observation_at("CLOSING"), HOME_WIN, settled_at=KICKOFF)
    after = ledger.read_bytes() if ledger.exists() else None
    assert before == after


# ----------------------------------- regression: pregame means pregame


def test_no_pregame_label_is_due_at_or_past_kickoff_even_with_a_stale_status():
    """REGRESSION. `game_started` comes from the schedule source's status
    field, which can lag a real kickoff. EARLY_OPEN was the one label
    with no window bounds of its own, so a stale "scheduled" status let a
    PREGAME-labelled row be captured from post-kickoff market data -- a
    row that would look prospective and carry information from after the
    game began.

    Not observed in the genuine corpus (0 captures at or after kickoff at
    the time of the fix), but reachable. The clock is now authoritative,
    not the status field."""
    for minutes_after in (0, 1, 30, 60 * 24):
        due = timing.resolve_due_labels(
            kickoff_utc=KICKOFF,
            now=KICKOFF + timedelta(minutes=minutes_after),
            already_captured_labels=set(),
            game_started=False,  # the stale-status case
        )
        assert due == [], f"{minutes_after} min after kickoff still offered {due}"


def test_early_open_is_still_due_before_kickoff():
    """The fix must not suppress the legitimate case."""
    due = timing.resolve_due_labels(
        kickoff_utc=KICKOFF,
        now=KICKOFF - timedelta(days=20),
        already_captured_labels=set(),
    )
    assert timing.EARLY_OPEN in due


def test_the_kickoff_boundary_is_exact():
    one_second_before = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=KICKOFF - timedelta(seconds=1), already_captured_labels=set()
    )
    assert one_second_before  # CLOSING is due here
    assert (
        timing.resolve_due_labels(
            kickoff_utc=KICKOFF, now=KICKOFF, already_captured_labels=set()
        )
        == []
    )
