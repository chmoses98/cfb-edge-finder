"""Every discovered contract gets exactly one mechanical status, and the
fail-closed cases actually fail closed.

These are the tests that protect the reconciliation identity:

    contracts_discovered == contracts_eligible + explicit exclusions
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cfb_edge_finder.execution.disposition import (
    DispositionConfig,
    MechanicalStatus,
)
from cfb_edge_finder.execution.slate import build_slate
from tests.execution_fakes import (
    CAPTURED_AT,
    GAME_KEY,
    NOW,
    catalog_dir,
    fee_block,
    market,
    standard_markets,
)


def config(**overrides):
    base = {
        "as_of": NOW,
        "captured_at": CAPTURED_AT,
        "max_capture_age_minutes": 120.0,
    }
    base.update(overrides)
    return DispositionConfig(**base)


def build(tmp_path, markets=None, cfg=None, **catalog_kwargs):
    directory = catalog_dir(tmp_path, markets, **catalog_kwargs)
    return build_slate(directory, cfg or config(), slate_date=None)


# ---------------------------------------------------- the whole universe


def test_every_discovered_contract_receives_a_disposition(tmp_path):
    slate = build(tmp_path).slate
    reconciliation = slate["reconciliation"]
    assert reconciliation["contracts_discovered"] == len(standard_markets())
    assert (
        reconciliation["contracts_discovered"]
        == reconciliation["contracts_eligible"] + reconciliation["contracts_excluded"]
    )
    assert reconciliation["unaccounted_contracts"] == 0
    assert reconciliation["balanced"] is True


def test_every_excluded_contract_is_named_with_its_reason(tmp_path):
    markets = standard_markets()
    markets.append(
        market(
            f"KXNCAAFTOTAL-{GAME_KEY}-99",
            family="game_total",
            title="Over 98.5 points scored",
            floor_strike=98.5,
            status="closed",
        )
    )
    packet = build(tmp_path, markets).packets[0]
    excluded = packet["excluded_contracts"]
    assert len(excluded) == packet["counts"]["excluded"] == 1
    assert excluded[0]["status"] == MechanicalStatus.MARKET_CLOSED.value
    assert excluded[0]["reason"]
    assert excluded[0]["ticker"] == f"KXNCAAFTOTAL-{GAME_KEY}-99"


def test_no_eligible_contract_silently_disappears(tmp_path):
    """The eligible list, the per-family counts and the headline number
    are three independent renderings of one population."""
    packet = build(tmp_path).packets[0]
    assert len(packet["contracts"]) == packet["counts"]["eligible"]
    by_family = sum(b["contracts"] for b in packet["market_families"].values())
    assert by_family == packet["counts"]["eligible"]
    tickers = {c["ticker"] for c in packet["contracts"]}
    assert len(tickers) == len(packet["contracts"])


# --------------------------------------------------------- fail closed


def test_unknown_fee_models_fail_closed(tmp_path):
    markets = standard_markets()
    markets.append(
        market(
            f"KXNCAAFTOTAL-{GAME_KEY}-60",
            family="game_total",
            title="Over 59.5 points scored",
            floor_strike=59.5,
            fee=fee_block(support="unsupported_model", model="quadratic_v2"),
        )
    )
    packet = build(tmp_path, markets).packets[0]
    statuses = {e["ticker"]: e["status"] for e in packet["excluded_contracts"]}
    assert statuses[f"KXNCAAFTOTAL-{GAME_KEY}-60"] == MechanicalStatus.UNSUPPORTED_FEE_MODEL.value


def test_a_supported_model_with_no_computable_fee_still_fails_closed(tmp_path):
    """`support: supported` is not enough -- the amount has to exist."""
    markets = standard_markets()
    fee = fee_block()
    fee["model_trade_fee_at_yes_ask"] = None
    fee["model_trade_fee_at_no_ask"] = None
    markets.append(
        market(
            f"KXNCAAFTOTAL-{GAME_KEY}-61",
            family="game_total",
            title="Over 60.5 points scored",
            floor_strike=60.5,
            fee=fee,
        )
    )
    packet = build(tmp_path, markets).packets[0]
    statuses = {e["ticker"]: e["status"] for e in packet["excluded_contracts"]}
    assert statuses[f"KXNCAAFTOTAL-{GAME_KEY}-61"] == MechanicalStatus.UNSUPPORTED_FEE_MODEL.value


def test_started_games_fail_closed(tmp_path):
    """Kickoff has passed: every contract on the game is excluded, none
    silently."""
    build_result = build(tmp_path, kickoff="2026-09-19T11:00:00Z")
    slate = build_result.slate
    assert slate["reconciliation"]["contracts_eligible"] == 0
    assert (
        slate["reconciliation"]["exclusions_by_status"][MechanicalStatus.GAME_STARTED.value]
        == len(standard_markets())
    )
    assert slate["reconciliation"]["balanced"] is True


def test_a_game_inside_the_kickoff_buffer_fails_closed(tmp_path):
    cfg = config(min_seconds_before_kickoff=3600.0)
    slate = build(tmp_path, cfg=cfg, kickoff="2026-09-19T12:30:00Z").slate
    assert slate["reconciliation"]["contracts_eligible"] == 0
    assert MechanicalStatus.GAME_STARTED.value in slate["reconciliation"]["exclusions_by_status"]


def test_stale_captures_fail_closed(tmp_path):
    cfg = config(as_of=CAPTURED_AT + timedelta(hours=6))
    slate = build(tmp_path, cfg=cfg).slate
    assert slate["reconciliation"]["contracts_eligible"] == 0
    assert (
        slate["reconciliation"]["exclusions_by_status"][MechanicalStatus.STALE_QUOTE.value]
        == len(standard_markets())
    )


def test_a_missing_capture_timestamp_is_stale_not_fresh(tmp_path):
    cfg = config(captured_at=None)
    slate = build(tmp_path, cfg=cfg).slate
    assert slate["reconciliation"]["contracts_eligible"] == 0
    assert MechanicalStatus.STALE_QUOTE.value in slate["reconciliation"]["exclusions_by_status"]


def test_the_optional_per_contract_quote_age_gate_fails_closed(tmp_path):
    cfg = config(max_quote_age_minutes=5.0)
    slate = build(tmp_path, cfg=cfg).slate
    assert slate["reconciliation"]["contracts_eligible"] == 0
    assert MechanicalStatus.STALE_QUOTE.value in slate["reconciliation"]["exclusions_by_status"]


def test_a_contract_with_no_buyable_side_is_excluded(tmp_path):
    markets = standard_markets()
    markets.append(
        market(
            f"KXNCAAFTOTAL-{GAME_KEY}-70",
            family="game_total",
            title="Over 69.5 points scored",
            floor_strike=69.5,
            yes_ask=None,
            no_ask=None,
        )
    )
    packet = build(tmp_path, markets).packets[0]
    statuses = {e["ticker"]: e["status"] for e in packet["excluded_contracts"]}
    assert (
        statuses[f"KXNCAAFTOTAL-{GAME_KEY}-70"]
        == MechanicalStatus.MISSING_EXECUTABLE_PRICE.value
    )


def test_sentinel_prices_of_zero_and_one_are_not_executable(tmp_path):
    markets = standard_markets()
    markets.append(
        market(
            f"KXNCAAFTOTAL-{GAME_KEY}-71",
            family="game_total",
            title="Over 70.5 points scored",
            floor_strike=70.5,
            yes_ask=0.0,
            no_ask=1.0,
        )
    )
    packet = build(tmp_path, markets).packets[0]
    statuses = {e["ticker"]: e["status"] for e in packet["excluded_contracts"]}
    assert (
        statuses[f"KXNCAAFTOTAL-{GAME_KEY}-71"]
        == MechanicalStatus.MISSING_EXECUTABLE_PRICE.value
    )


def test_duplicate_tickers_are_dispositioned_not_double_counted(tmp_path):
    markets = standard_markets()
    markets.append(markets[0])
    slate = build(tmp_path, markets).slate
    reconciliation = slate["reconciliation"]
    assert reconciliation["exclusions_by_status"][MechanicalStatus.DUPLICATE.value] == 1
    assert reconciliation["balanced"] is True


def test_a_team_scoped_contract_with_no_resolvable_team_is_a_mapping_failure(tmp_path):
    markets = standard_markets()
    markets.append(
        market(
            f"KXNCAAFSPREAD-{GAME_KEY}-XYZ7",
            family="game_spread",
            title="Nobody In This Game wins by over 6.5 points",
            floor_strike=6.5,
        )
    )
    packet = build(tmp_path, markets).packets[0]
    statuses = {e["ticker"]: e["status"] for e in packet["excluded_contracts"]}
    assert statuses[f"KXNCAAFSPREAD-{GAME_KEY}-XYZ7"] == MechanicalStatus.MAPPING_FAILURE.value


def test_a_contract_with_no_stateable_meaning_is_excluded(tmp_path):
    markets = standard_markets()
    blank = market(
        f"KXNCAAFMYSTERY-{GAME_KEY}-1", family="unknown", period="unknown", title="x"
    )
    blank["title"] = None
    blank["yes_sub_title"] = None
    blank["rules_primary"] = None
    markets.append(blank)
    packet = build(tmp_path, markets).packets[0]
    statuses = {e["ticker"]: e["status"] for e in packet["excluded_contracts"]}
    assert (
        statuses[f"KXNCAAFMYSTERY-{GAME_KEY}-1"]
        == MechanicalStatus.UNSUPPORTED_MARKET_SEMANTICS.value
    )


# -------------------------------------- unknown families stay in the count


def test_an_unfamiliar_family_is_eligible_not_dropped(tmp_path):
    """A family Kalshi invents tomorrow must survive discovery. Its
    pricing problem belongs to the evaluator, not to the mechanical
    filter."""
    markets = standard_markets()
    markets.append(
        market(
            f"KXNCAAFBRANDNEW-{GAME_KEY}-Y",
            family="a_family_invented_tomorrow",
            title="Something nobody has modelled",
            yes_ask=0.4,
            no_ask=0.62,
        )
    )
    packet = build(tmp_path, markets).packets[0]
    tickers = {c["ticker"] for c in packet["contracts"]}
    assert f"KXNCAAFBRANDNEW-{GAME_KEY}-Y" in tickers
    record = next(c for c in packet["contracts"] if "BRANDNEW" in c["ticker"])
    assert record["semantics"]["requires"] == "explicit_probability"
    assert record["semantics"]["yes_means"]


def test_the_known_unknown_family_from_the_live_slate_stays_eligible(tmp_path):
    """KXNCAAF1HFT (1st-half/fulltime double) is classified `unknown` by
    the catalog and is genuinely unpriceable from marginal distributions.
    It is still eligible, still counted, still visible."""
    packet = build(tmp_path).packets[0]
    record = next(c for c in packet["contracts"] if "1HFT" in c["ticker"])
    assert record["status"] == MechanicalStatus.ELIGIBLE.value
    assert record["semantics"]["requires"] == "explicit_probability"


def test_the_exclusion_status_vocabulary_is_closed(tmp_path):
    slate = build(tmp_path, kickoff="2026-09-19T11:00:00Z").slate
    known = {s.value for s in MechanicalStatus}
    assert set(slate["reconciliation"]["exclusions_by_status"]) <= known


def test_as_of_is_honoured_rather_than_wall_clock(tmp_path):
    """The whole suite depends on this: a test that silently used
    `datetime.now()` would pass today and fail in a week."""
    cfg = config(as_of=datetime(2030, 1, 1, tzinfo=UTC), captured_at=datetime(2030, 1, 1, tzinfo=UTC))
    slate = build(tmp_path, cfg=cfg).slate
    assert slate["reconciliation"]["contracts_eligible"] == 0
