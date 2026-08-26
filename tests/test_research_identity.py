"""Mission section 3: deterministic snapshot identity."""

from __future__ import annotations

from cfb_edge_finder.research.identity import observation_key, settlement_key


def _key(**overrides):
    base = dict(
        season=2026, game_id="cfb-2026-wk01-a-at-b", market_ticker="MKT-1", timing_label="T_60", model_version="1.0"
    )
    base.update(overrides)
    return observation_key(**base)


def test_same_inputs_produce_same_key():
    assert _key() == _key()


def test_different_model_version_produces_different_key():
    assert _key(model_version="1.0") != _key(model_version="1.1")


def test_different_timing_label_produces_different_key():
    assert _key(timing_label="T_60") != _key(timing_label="T_30")


def test_different_market_ticker_produces_different_key():
    assert _key(market_ticker="MKT-1") != _key(market_ticker="MKT-2")


def test_different_game_id_produces_different_key():
    assert _key(game_id="cfb-2026-wk01-a-at-b") != _key(game_id="cfb-2026-wk01-c-at-d")


def test_different_season_produces_different_key():
    assert _key(season=2026) != _key(season=2027)


def test_different_capture_window_version_produces_different_key():
    assert _key() != _key(capture_window_version="capture_window_v2")


def test_key_is_not_a_random_uuid_shaped_value():
    # sha256 hexdigest is deterministic and 64 hex chars -- not a UUID.
    key = _key()
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


def test_key_stable_across_repeated_calls_many_times():
    keys = {_key() for _ in range(50)}
    assert len(keys) == 1


def test_settlement_key_deterministic_and_distinct_from_observation_key():
    s1 = settlement_key(game_id="g1", market_ticker="MKT-1")
    s2 = settlement_key(game_id="g1", market_ticker="MKT-1")
    assert s1 == s2
    assert s1 != _key(game_id="g1", market_ticker="MKT-1")


def test_settlement_key_distinguishes_markets():
    assert settlement_key(game_id="g1", market_ticker="MKT-1") != settlement_key(game_id="g1", market_ticker="MKT-2")
