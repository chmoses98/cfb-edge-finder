"""Opponent adjustment: recovery of known truth, shrinkage, and leakage safety."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import numpy as np
import pytest
from script_engine_fakes import SEASON_START, UPCOMING_KICKOFF, synthetic_season, true_effects

from cfb_edge_finder.scripting.adjust import (
    Q_NOT_ADJUSTED,
    Q_THIN,
    fit_league,
    fit_metric,
    rank_in_universe,
)
from cfb_edge_finder.scripting.gamelog import FINAL_AFTER, before, data_cutoff, iso_utc, parse_utc

CUTOFF = data_cutoff(iso_utc(UPCOMING_KICKOFF))


@pytest.fixture(scope="module")
def season():
    return synthetic_season()


def test_adjustment_recovers_the_true_offensive_and_defensive_effects(season):
    fit = fit_metric(season, "yards_per_play", CUTOFF)
    truth = true_effects()
    fbs = list(fit.fbs_teams)
    off = np.corrcoef([fit.get(t, "offense").adjusted for t in fbs], [truth[t]["off"] for t in fbs])[0, 1]
    dfn = np.corrcoef([fit.get(t, "defense").adjusted for t in fbs], [truth[t]["def"] for t in fbs])[0, 1]
    assert off > 0.85 and dfn > 0.8


def test_adjustment_beats_raw_averages_when_schedules_differ(season):
    """Raw yards per play ignores who you played; the adjustment should track truth better."""
    fit = fit_metric(season, "yards_per_play", CUTOFF)
    truth = true_effects()
    fbs = list(fit.fbs_teams)
    adj = np.corrcoef([fit.get(t, "offense").adjusted for t in fbs], [truth[t]["off"] for t in fbs])[0, 1]
    raw = np.corrcoef([fit.get(t, "offense").raw for t in fbs], [truth[t]["off"] for t in fbs])[0, 1]
    assert adj >= raw - 0.02


def test_later_games_cannot_move_an_earlier_estimate(season):
    """The leakage test: a fit at cutoff C on the WHOLE season equals a fit on rows truncated at C."""
    early_cutoff = data_cutoff(iso_utc(SEASON_START + timedelta(weeks=4, hours=4)))
    truncated = [r for r in season if r.kickoff() + FINAL_AFTER < parse_utc(early_cutoff)]
    assert len(truncated) < len(season)
    full = fit_league(season, early_cutoff)
    cut = fit_league(truncated, early_cutoff)
    assert full.rows_fingerprint == cut.rows_fingerprint
    for metric_id, fit in full.fits.items():
        assert fit.estimates == cut.fits[metric_id].estimates, metric_id


def test_appending_future_games_never_changes_the_past(season):
    early_cutoff = data_cutoff(iso_utc(SEASON_START + timedelta(weeks=3, hours=4)))
    first = fit_metric(season[: len(season) // 2], "success_rate", early_cutoff)
    second = fit_metric(season, "success_rate", early_cutoff)
    assert first.estimates == second.estimates


def test_a_game_in_progress_at_the_cutoff_is_excluded(season):
    last = max(r.kickoff() for r in season)
    just_after = iso_utc(last + timedelta(hours=2))
    assert all(r.kickoff() != last for r in before(season, just_after))
    after_final = iso_utc(last + FINAL_AFTER + timedelta(minutes=1))
    assert any(r.kickoff() == last for r in before(season, after_final))


def test_the_data_cutoff_is_four_am_eastern_on_the_game_date():
    assert data_cutoff("2026-10-10T23:30:00Z") == "2026-10-10T08:00:00Z"  # 7:30 pm EDT game
    assert data_cutoff("2026-10-11T02:30:00Z") == "2026-10-10T08:00:00Z"  # 10:30 pm EDT, same date
    assert data_cutoff("2026-12-05T17:00:00Z") == "2026-12-05T09:00:00Z"  # 4 am EST after DST ends


def test_early_season_estimates_are_shrunk_and_flagged(season):
    week_two = data_cutoff(iso_utc(SEASON_START + timedelta(weeks=2, hours=4)))
    fit = fit_metric(season, "yards_per_play", week_two)
    est = fit.get("F00", "offense")
    assert est.games == 2
    assert est.quality == Q_THIN
    assert est.prior_weight > 0.6
    late = fit_metric(season, "yards_per_play", CUTOFF).get("F00", "offense")
    assert late.prior_weight < est.prior_weight
    assert late.se < est.se
    # Shrinkage: a two-game team sits closer to the league baseline than its raw rate.
    assert abs(est.adjusted - fit.mu) < abs(est.raw - fit.mu)


def test_a_descriptive_metric_is_never_presented_as_adjusted(season):
    fit = fit_metric(season, "pass_rate", CUTOFF)
    assert not fit.adjusted
    for est in fit.estimates.values():
        assert est.adjusted is None and est.quality == Q_NOT_ADJUSTED and est.raw is not None


def test_unavailable_inputs_stay_unavailable_never_raw(season):
    no_pbp = [replace(r, pbp=None, pbp_source=None) for r in season]
    fit = fit_metric(no_pbp, "success_rate", CUTOFF)
    assert fit.estimates == {} or all(e.adjusted is None for e in fit.estimates.values())


def test_ranks_are_within_the_fbs_universe_only(season):
    fit = fit_metric(season, "yards_per_play", CUTOFF)
    rank, universe = rank_in_universe(fit, "F00", "offense", higher_is_better=True)
    assert universe == 24 and 1 <= rank <= 24
    assert rank_in_universe(fit, "C00", "offense", True)[0] is None


def test_fits_are_deterministic(season):
    a = fit_league(season, CUTOFF)
    b = fit_league(list(reversed(season)), CUTOFF)
    for metric_id in a.fits:
        assert a.fits[metric_id].estimates == b.fits[metric_id].estimates


def test_schedule_graph_stability_is_reported(season):
    week_one = data_cutoff(iso_utc(SEASON_START + timedelta(weeks=1, hours=4)))
    early = fit_league(season, week_one)
    assert not early.stable
    assert early.fbs_components > 1
    assert fit_league(season, CUTOFF).stable
