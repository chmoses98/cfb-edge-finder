"""research/result_provider: CFBD-primary, fail-closed ESPN fallback for
canonical game results.

Every ESPN fixture shape here is copied from GENUINE live responses
(GitHub Actions run 33330066488, scripts/validate_espn_results_live.py,
2026-08-30): string scores, three-fold status.type finality, homeAway
tags, team.location matching CFBD school naming (unicode included), and
US-local date bucketing (a 2026-08-30T02:00Z kickoff sat in the 20260829
bucket while 20260830 was empty).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests

from cfb_edge_finder.research import persistence
from cfb_edge_finder.research.football_state import FootballState, save_football_state
from cfb_edge_finder.research.result_provider import (
    CFBD_PRIMARY,
    ESPN_FALLBACK,
    ESPN_KICKOFF_TOLERANCE_HOURS,
    GameIdentity,
    ResultProviderUnavailable,
    espn_game_result,
    load_identity_map,
    match_espn_event,
    parse_espn_event,
    resolve_game_results,
    scoreboard_dates_for,
)
from cfb_edge_finder.research.settlement import settle_market
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.settlement import GameFinalStatus, MarketSettlementStatus
from tests.research_factories import make_corpus_row, make_observation

NOW = datetime(2026, 8, 30, 19, 0, tzinfo=UTC)
SEASON = 2026
GAME_ID = "cfb-2026-wk01-sacramento-state-at-eastern-michigan"
KICKOFF = datetime(2026, 8, 29, 22, 30, tzinfo=UTC)

_SPEC = importlib.util.spec_from_file_location(
    "research_settle_under_test", Path(__file__).resolve().parents[1] / "scripts" / "research_settle.py"
)
research_settle = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("research_settle_under_test", research_settle)
_SPEC.loader.exec_module(research_settle)


# ------------------------------------------------------------- fixtures


def _identity(**overrides) -> GameIdentity:
    defaults = dict(
        game_id=GAME_ID,
        season=SEASON,
        home_team_id="eastern-michigan",
        away_team_id="sacramento-state",
        kickoff_utc=KICKOFF,
    )
    defaults.update(overrides)
    return GameIdentity(**defaults)


def _espn_event(
    *,
    event_id="401866408",
    date="2026-08-29T22:30Z",
    home=("Eastern Michigan", "Eastern Michigan Eagles"),
    away=("Sacramento State", "Sacramento State Hornets"),
    home_score="28",
    away_score="17",
    home_winner=True,
    away_winner=False,
    status_name="STATUS_FINAL",
    status_state="post",
    completed=True,
    season_year=SEASON,
    linescore_periods=4,
    **status_extra,
) -> dict:
    """One scoreboard event in the exact live-verified shape (trimmed to
    the fields settlement reads)."""

    def _competitor(home_away, names, score, winner):
        location, display = names
        competitor = {
            "homeAway": home_away,
            "score": score,
            "winner": winner,
            "team": {"id": "2199", "location": location, "displayName": display, "name": display.split()[-1]},
        }
        if linescore_periods:
            competitor["linescores"] = [{"value": 7.0, "period": p + 1} for p in range(linescore_periods)]
        return competitor

    status_type = {
        "id": "3",
        "name": status_name,
        "state": status_state,
        "completed": completed,
        "detail": "Final",
        "shortDetail": "Final",
        **status_extra,
    }
    return {
        "id": event_id,
        "date": date,
        "name": f"{away[1]} at {home[1]}",
        "season": {"year": season_year, "type": 2, "slug": "regular-season"},
        "competitions": [
            {
                "id": event_id,
                "date": date,
                "neutralSite": False,
                "status": {"type": status_type},
                "competitors": [
                    _competitor("home", home, home_score, home_winner),
                    _competitor("away", away, away_score, away_winner),
                ],
            }
        ],
    }


def _raw_cfbd_game(
    *,
    game_id_num=87001,
    home="Eastern Michigan",
    away="Sacramento State",
    start="2026-08-29T22:30:00.000Z",
    **extra,
) -> dict:
    raw = {
        "id": game_id_num,
        "season": SEASON,
        "week": 1,
        "seasonType": "regular",
        "startDate": start,
        "neutralSite": False,
        "homeTeam": home,
        "awayTeam": away,
        "homeClassification": "fbs",
        "awayClassification": "fbs",
    }
    raw.update(extra)
    return raw


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"HTTP {status}", response=response)


class _FakeCFBD:
    def __init__(self, *, raises: Exception | None = None, raw_games: list[dict] | None = None):
        self.raises = raises
        self.raw_games = raw_games or []
        self.calls = 0

    def fetch_games(self, season, season_type=None, **_):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.raw_games


class _FakeESPN:
    def __init__(self, events_by_date: dict[str, list[dict]], *, raises: Exception | None = None):
        self.events_by_date = events_by_date
        self.raises = raises
        self.fetched_dates: list[str] = []

    def fetch_scoreboard(self, date_yyyymmdd, group_id=80):
        self.fetched_dates.append(date_yyyymmdd)
        if self.raises is not None:
            raise self.raises
        return {"events": self.events_by_date.get(date_yyyymmdd, [])}


@pytest.fixture
def repo_with_preseason_cache(tmp_path: Path) -> Path:
    cache_dir = tmp_path / "data" / "research_cache" / "preseason"
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{SEASON}.json").write_text(json.dumps({"season": SEASON, "games": [_raw_cfbd_game()]}))
    return tmp_path


def _espn_for_slate(events: list[dict]) -> _FakeESPN:
    """The live bucketing: kickoff-date bucket EMPTY, prior-day bucket
    holding the events -- exactly what run 33330066488 observed for the
    2026-08-30T02:00Z game, and the general worst case for every date."""
    return _FakeESPN({"20260829": events, "20260830": []})


# ------------------------------------------------- CFBD primary + routing


class TestPrimaryRouting:
    def test_cfbd_success_uses_primary_and_never_touches_espn(self, repo_with_preseason_cache):
        espn = _FakeESPN({})
        cfbd = _FakeCFBD(raw_games=[_raw_cfbd_game(homePoints=28, awayPoints=17, status="completed")])
        outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=cfbd, repo_dir=repo_with_preseason_cache,
            needed_game_ids={GAME_ID}, espn_client=espn,
        )
        assert outcome.provider == CFBD_PRIMARY
        assert outcome.fallback_reason is None
        assert espn.fetched_dates == []
        result = outcome.results_by_game_id[GAME_ID]
        assert result.source == "cfbd"
        assert (result.home_points, result.away_points, result.status) == (28, 17, GameFinalStatus.FINAL)

    def test_cfbd_not_final_is_a_valid_primary_answer_espn_never_consulted(self, repo_with_preseason_cache):
        # ESPN would say FINAL here -- but a reachable primary's "not yet
        # final" must stand. Fallback is for availability, not overrides.
        espn = _espn_for_slate([_espn_event()])
        cfbd = _FakeCFBD(raw_games=[_raw_cfbd_game(status="in_progress")])
        outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=cfbd, repo_dir=repo_with_preseason_cache,
            needed_game_ids={GAME_ID}, espn_client=espn,
        )
        assert outcome.provider == CFBD_PRIMARY
        assert espn.fetched_dates == []
        assert outcome.results_by_game_id[GAME_ID].status is GameFinalStatus.NOT_YET_FINAL

    @pytest.mark.parametrize("exc", [_http_error(429), _http_error(502), requests.ConnectionError("boom")])
    def test_recoverable_cfbd_failures_engage_fallback(self, repo_with_preseason_cache, exc):
        outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=exc), repo_dir=repo_with_preseason_cache,
            needed_game_ids={GAME_ID}, espn_client=_espn_for_slate([_espn_event()]),
        )
        assert outcome.provider == ESPN_FALLBACK
        assert "cfbd unavailable (recoverable)" in (outcome.fallback_reason or "")
        assert GAME_ID in outcome.results_by_game_id

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_non_recoverable_cfbd_errors_raise_instead_of_falling_back(self, repo_with_preseason_cache, status):
        with pytest.raises(requests.HTTPError):
            resolve_game_results(
                season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=_http_error(status)),
                repo_dir=repo_with_preseason_cache, needed_game_ids={GAME_ID},
                espn_client=_espn_for_slate([_espn_event()]),
            )

    def test_espn_fetch_failure_while_cfbd_down_aborts_everything(self, repo_with_preseason_cache):
        espn = _FakeESPN({}, raises=requests.ConnectionError("espn down too"))
        with pytest.raises(ResultProviderUnavailable, match="both sources unavailable"):
            resolve_game_results(
                season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=_http_error(429)),
                repo_dir=repo_with_preseason_cache, needed_game_ids={GAME_ID}, espn_client=espn,
            )


# ------------------------------------------------------ identity matching


class TestIdentityMatching:
    def test_exact_match_settles_with_full_provenance(self, repo_with_preseason_cache):
        outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=_http_error(429)),
            repo_dir=repo_with_preseason_cache, needed_game_ids={GAME_ID},
            espn_client=_espn_for_slate([_espn_event()]),
        )
        result = outcome.results_by_game_id[GAME_ID]
        assert result.source == ESPN_FALLBACK
        assert result.source_game_id == "401866408"
        assert (result.home_points, result.away_points) == (28, 17)
        assert result.status is GameFinalStatus.FINAL
        assert "STATUS_FINAL" in (result.status_evidence or "")
        assert "cfbd unavailable" in (result.fallback_reason or "")
        assert outcome.unresolved == {}

    def test_orientation_flip_fails_closed(self):
        flipped = parse_espn_event(
            _espn_event(
                home=("Sacramento State", "Sacramento State Hornets"),
                away=("Eastern Michigan", "Eastern Michigan Eagles"),
            )
        )
        event, reason = match_espn_event(_identity(), [flipped])
        assert event is None
        assert "orientation mismatch" in reason

    def test_zero_matching_events_fails_closed(self):
        other = parse_espn_event(_espn_event(home=("Stanford", "Stanford Cardinal"), away=("Hawai'i", "x")))
        event, reason = match_espn_event(_identity(), [other])
        assert event is None
        assert "no ESPN event matched" in reason

    def test_multiple_matching_events_fail_closed_as_ambiguous(self):
        a = parse_espn_event(_espn_event(event_id="1"))
        b = parse_espn_event(_espn_event(event_id="2"))
        event, reason = match_espn_event(_identity(), [a, b])
        assert event is None
        assert "ambiguous" in reason

    def test_unknown_team_name_is_never_fuzzy_matched(self):
        facts = parse_espn_event(
            _espn_event(home=("Eastern Michigain Typo University", "same"), away=("Sacramento State", "x"))
        )
        assert facts.resolution_error is not None
        event, reason = match_espn_event(_identity(), [facts])
        assert event is None and "no ESPN event matched" in reason

    def test_ambiguous_alias_fails_closed(self):
        facts = parse_espn_event(_espn_event(home=("Miami", "Miami"), away=("Sacramento State", "x")))
        assert "ambiguous" in (facts.resolution_error or "").lower() or "Miami" in (facts.resolution_error or "")
        assert facts.home_team_id is None

    def test_kickoff_out_of_tolerance_fails_closed(self):
        facts = parse_espn_event(_espn_event(date="2026-09-05T22:30Z"))
        event, reason = match_espn_event(_identity(), [facts])
        assert event is None
        assert "kickoff out of tolerance" in reason
        assert str(ESPN_KICKOFF_TOLERANCE_HOURS) in reason

    def test_prior_day_bucketing_still_matches_live_utc_rollover_case(self, repo_with_preseason_cache):
        # Memphis@UNLV kicked 2026-08-30T02:00Z and lived ONLY in the
        # 20260829 bucket (live-verified). Same shape here: kickoff-date
        # bucket empty, event in the prior-day bucket.
        cache = repo_with_preseason_cache / "data" / "research_cache" / "preseason" / f"{SEASON}.json"
        cache.write_text(
            json.dumps({"games": [_raw_cfbd_game(home="UNLV", away="Memphis", start="2026-08-30T02:00:00.000Z")]})
        )
        unlv_game_id = "cfb-2026-wk01-memphis-at-unlv"
        event = _espn_event(
            event_id="401862693", date="2026-08-30T02:00Z",
            home=("UNLV", "UNLV Rebels"), away=("Memphis", "Memphis Tigers"),
            home_score="21", away_score="27", home_winner=False, away_winner=True,
        )
        espn = _FakeESPN({"20260830": [], "20260829": [event]})
        outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=_http_error(429)),
            repo_dir=repo_with_preseason_cache, needed_game_ids={unlv_game_id}, espn_client=espn,
        )
        assert set(espn.fetched_dates) == {"20260830", "20260829"}
        result = outcome.results_by_game_id[unlv_game_id]
        assert (result.home_points, result.away_points) == (21, 27)

    def test_wrong_season_event_is_not_a_candidate(self):
        facts = parse_espn_event(_espn_event(season_year=2025))
        event, reason = match_espn_event(_identity(), [facts])
        assert event is None
        assert "no ESPN event matched" in reason

    def test_unicode_locations_resolve_through_existing_exact_aliases(self):
        # Verbatim live naming: "San José State" and "Hawai'i".
        facts = parse_espn_event(
            _espn_event(home=("USC", "USC Trojans"), away=("San José State", "San José State Spartans"))
        )
        assert (facts.home_team_id, facts.away_team_id) == ("usc", "san-jose-state")
        facts2 = parse_espn_event(
            _espn_event(home=("Stanford", "Stanford Cardinal"), away=("Hawai'i", "Hawai'i Rainbow Warriors"))
        )
        assert facts2.away_team_id == "hawaii"

    def test_malformed_competitors_make_event_non_candidate(self):
        raw = _espn_event()
        raw["competitions"][0]["competitors"] = raw["competitions"][0]["competitors"][:1]
        facts = parse_espn_event(raw)
        assert "exactly one home and one away" in facts.resolution_error


# ------------------------------------------------------------- finality


class TestFinality:
    def test_full_threefold_final_signal_is_required_and_sufficient(self):
        result, reason = espn_game_result(
            parse_espn_event(_espn_event()), game_id=GAME_ID, season=SEASON, now=NOW, fallback_reason="r"
        )
        assert reason is None
        assert result.status is GameFinalStatus.FINAL

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(completed=True, status_state="in"),  # completed but not post
            dict(completed=False),  # STATUS_FINAL+post but completed false
            dict(status_name="STATUS_FINAL", status_state="in", completed=False),
        ],
    )
    def test_contradictory_finality_evidence_fails_closed(self, kwargs):
        result, reason = espn_game_result(
            parse_espn_event(_espn_event(**kwargs)), game_id=GAME_ID, season=SEASON, now=NOW, fallback_reason="r"
        )
        assert result is None
        assert "contradictory ESPN finality evidence" in reason

    def test_in_progress_yields_not_yet_final_never_a_score(self):
        event = _espn_event(status_name="STATUS_IN_PROGRESS", status_state="in", completed=False, home_score="14")
        result, reason = espn_game_result(
            parse_espn_event(event), game_id=GAME_ID, season=SEASON, now=NOW, fallback_reason="r"
        )
        assert reason is None
        assert result.status is GameFinalStatus.NOT_YET_FINAL
        assert result.home_points is None and result.away_points is None
        assert "STATUS_IN_PROGRESS" in result.status_evidence

    def test_espn_postponed_never_voids_stays_pending_for_primary(self):
        event = _espn_event(status_name="STATUS_POSTPONED", status_state="pre", completed=False)
        result, reason = espn_game_result(
            parse_espn_event(event), game_id=GAME_ID, season=SEASON, now=NOW, fallback_reason="r"
        )
        assert reason is None
        assert result.status is GameFinalStatus.NOT_YET_FINAL  # a void is a primary-source decision
        settlement = settle_market(make_observation(game_id=GAME_ID), result, settled_at=NOW)
        assert settlement.status is MarketSettlementStatus.PENDING_NOT_FINAL


# ------------------------------------------------------- score validation


class TestScores:
    @pytest.mark.parametrize("bad", ["", "N/A", "42.5", "-3", None])
    def test_unparseable_final_score_fails_closed(self, bad):
        result, reason = espn_game_result(
            parse_espn_event(_espn_event(home_score=bad)), game_id=GAME_ID, season=SEASON, now=NOW, fallback_reason="r"
        )
        assert result is None
        assert "unparseable score" in reason

    def test_winner_flags_contradicting_scores_fail_closed(self):
        event = _espn_event(home_winner=False, away_winner=True)  # but home 28 > away 17
        result, reason = espn_game_result(
            parse_espn_event(event), game_id=GAME_ID, season=SEASON, now=NOW, fallback_reason="r"
        )
        assert result is None
        assert "contradict scores" in reason

    def test_tied_final_carries_through_and_moneyline_stays_unsettleable(self):
        event = _espn_event(home_score="17", away_score="17", home_winner=None, away_winner=None)
        result, reason = espn_game_result(
            parse_espn_event(event), game_id=GAME_ID, season=SEASON, now=NOW, fallback_reason="r"
        )
        assert reason is None
        settlement = settle_market(make_observation(game_id=GAME_ID), result, settled_at=NOW)
        assert settlement.status is MarketSettlementStatus.UNSETTLEABLE_MISSING_FIELDS

    def test_overtime_detection_from_linescore_periods(self):
        five, _ = espn_game_result(
            parse_espn_event(_espn_event(linescore_periods=5)), game_id=GAME_ID, season=SEASON, now=NOW,
            fallback_reason="r",
        )
        four, _ = espn_game_result(
            parse_espn_event(_espn_event(linescore_periods=4)), game_id=GAME_ID, season=SEASON, now=NOW,
            fallback_reason="r",
        )
        absent, _ = espn_game_result(
            parse_espn_event(_espn_event(linescore_periods=0)), game_id=GAME_ID, season=SEASON, now=NOW,
            fallback_reason="r",
        )
        assert (five.went_to_overtime, four.went_to_overtime, absent.went_to_overtime) == (True, False, None)


# ------------------------------------------------------- identity sources


class TestIdentitySources:
    def test_identity_prefers_football_state_artifact(self, tmp_path):
        save_football_state(
            tmp_path,
            FootballState(
                season=SEASON, history_seasons=(), schedule_fetched_at=NOW, teams_fetched_at=NOW,
                history_fetched_at=NOW, schedule_games=[_raw_cfbd_game()], all_division_teams=[], history={},
            ),
        )
        identity, source = load_identity_map(tmp_path, SEASON, NOW)
        assert GAME_ID in identity
        assert "football_state artifact" in source
        assert identity[GAME_ID].kickoff_utc == KICKOFF

    def test_identity_falls_back_to_preseason_cache(self, repo_with_preseason_cache):
        identity, source = load_identity_map(repo_with_preseason_cache, SEASON, NOW)
        assert source == "preseason schedule cache"
        ident = identity[GAME_ID]
        assert (ident.home_team_id, ident.away_team_id) == ("eastern-michigan", "sacramento-state")

    def test_no_identity_source_fails_every_game_closed(self, tmp_path):
        outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=_http_error(429)), repo_dir=tmp_path,
            needed_game_ids={GAME_ID}, espn_client=_espn_for_slate([_espn_event()]),
        )
        assert outcome.results_by_game_id == {}
        assert "identity unavailable" in outcome.unresolved[GAME_ID]

    def test_unknown_kickoff_fails_closed_before_any_espn_query(self, tmp_path):
        cache_dir = tmp_path / "data" / "research_cache" / "preseason"
        cache_dir.mkdir(parents=True)
        (cache_dir / f"{SEASON}.json").write_text(
            json.dumps({"games": [_raw_cfbd_game(startTimeTBD=True)]})
        )
        espn = _espn_for_slate([_espn_event()])
        outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=_http_error(429)), repo_dir=tmp_path,
            needed_game_ids={GAME_ID}, espn_client=espn,
        )
        assert "kickoff unknown" in outcome.unresolved[GAME_ID]
        assert espn.fetched_dates == []

    def test_scoreboard_dates_cover_us_local_bucketing(self):
        assert scoreboard_dates_for(datetime(2026, 8, 30, 2, 0, tzinfo=UTC)) == ["20260830", "20260829"]
        assert scoreboard_dates_for(datetime(2026, 8, 29, 19, 0, tzinfo=UTC)) == ["20260829", "20260828"]


# --------------------------------------------- end-to-end settle + rerun


def _write_observation_ledger(repo_dir: Path, rows) -> Path:
    obs_path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, SEASON)
    obs_path.parent.mkdir(parents=True, exist_ok=True)
    with obs_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(row.model_dump_json() + "\n")
    return obs_path


class TestEndToEndSettlement:
    def _seed(self, repo_dir: Path) -> Path:
        rows = [
            make_corpus_row(
                observation=make_observation(
                    game_id=GAME_ID, kalshi_market_ticker="KXNCAAFGAME-X-EMU",
                    family=MarketFamily.MONEYLINE, team=Side.HOME,
                )
            ),
            make_corpus_row(
                observation=make_observation(
                    game_id=GAME_ID, kalshi_market_ticker="KXNCAAFSPREAD-X-SAC5",
                    family=MarketFamily.SPREAD, team=Side.AWAY, threshold=5.5, side=None,
                )
            ),
            make_corpus_row(
                observation=make_observation(
                    game_id=GAME_ID, kalshi_market_ticker="KXNCAAFTOTAL-X-T44",
                    family=MarketFamily.TOTAL, team=None, side=Side.OVER, threshold=44.5,
                )
            ),
        ]
        return _write_observation_ledger(repo_dir, rows)

    def test_fallback_settles_then_reruns_are_noops_and_ledger_untouched(self, repo_with_preseason_cache):
        repo_dir = repo_with_preseason_cache
        obs_path = self._seed(repo_dir)
        obs_bytes_before = obs_path.read_bytes()

        needed = research_settle._settleable_game_ids(repo_dir, SEASON)
        assert needed == {GAME_ID}

        outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=_http_error(429)), repo_dir=repo_dir,
            needed_game_ids=needed, espn_client=_espn_for_slate([_espn_event()]),
        )
        first = research_settle._apply_settle(
            repo_dir, season=SEASON, results_by_game_id=outcome.results_by_game_id, now=NOW
        )
        assert first.written == 3 and first.skipped_duplicate == 0

        settle_path = persistence.canonical_path(repo_dir / "data" / "research", persistence.SETTLEMENTS_SUBDIR, SEASON)
        settlements = persistence.read_settlement_rows(settle_path)
        by_ticker = {s.kalshi_market_ticker: s for s in settlements}
        # EMU won 28-17: home moneyline YES; SAC +5.5 lost by 11: NO; total 45 > 44.5: YES.
        assert by_ticker["KXNCAAFGAME-X-EMU"].derived_contract_settlement is Side.YES
        assert by_ticker["KXNCAAFSPREAD-X-SAC5"].derived_contract_settlement is Side.NO
        assert by_ticker["KXNCAAFTOTAL-X-T44"].derived_contract_settlement is Side.YES
        assert all(s.status is MarketSettlementStatus.SETTLED for s in settlements)
        assert all(s.game_result.source == ESPN_FALLBACK for s in settlements)
        assert all(s.game_result.status_evidence for s in settlements)

        # Idempotence: same fallback run again -> zero new rows.
        second = research_settle._apply_settle(
            repo_dir, season=SEASON, results_by_game_id=outcome.results_by_game_id, now=NOW + timedelta(hours=1)
        )
        assert second.written == 0 and second.skipped_duplicate == 3

        # The prospective observations ledger is BYTE-IDENTICAL -- settlement
        # never edits, backfills, or annotates captured rows.
        assert obs_path.read_bytes() == obs_bytes_before

    def test_cfbd_recovery_rerun_re_derives_identical_facts_as_noop(self, repo_with_preseason_cache):
        repo_dir = repo_with_preseason_cache
        self._seed(repo_dir)
        needed = {GAME_ID}
        espn_outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=_http_error(429)), repo_dir=repo_dir,
            needed_game_ids=needed, espn_client=_espn_for_slate([_espn_event()]),
        )
        research_settle._apply_settle(
            repo_dir, season=SEASON, results_by_game_id=espn_outcome.results_by_game_id, now=NOW
        )

        # CFBD comes back with the same final score: the re-derived facts
        # (status + both settlement outcomes) are identical, so the dedup
        # fingerprint absorbs them -- provenance differences never fork
        # the ledger.
        cfbd = _FakeCFBD(raw_games=[_raw_cfbd_game(homePoints=28, awayPoints=17, status="completed")])
        cfbd_outcome = resolve_game_results(
            season=SEASON, now=NOW + timedelta(hours=6), cfbd_client=cfbd, repo_dir=repo_dir,
            needed_game_ids=needed, espn_client=_FakeESPN({}),
        )
        assert cfbd_outcome.provider == CFBD_PRIMARY
        result = research_settle._apply_settle(
            repo_dir, season=SEASON, results_by_game_id=cfbd_outcome.results_by_game_id, now=NOW + timedelta(hours=6)
        )
        assert result.written == 0 and result.skipped_duplicate == 3

    def test_unresolved_game_settles_nothing_and_keeps_reason(self, repo_with_preseason_cache):
        repo_dir = repo_with_preseason_cache
        self._seed(repo_dir)
        # ESPN slate does not contain the game at all.
        outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=_http_error(429)), repo_dir=repo_dir,
            needed_game_ids={GAME_ID},
            espn_client=_espn_for_slate([_espn_event(home=("Stanford", "Stanford Cardinal"), away=("Hawai'i", "H"))]),
        )
        assert GAME_ID not in outcome.results_by_game_id
        assert "no ESPN event matched" in outcome.unresolved[GAME_ID]
        result = research_settle._apply_settle(
            repo_dir, season=SEASON, results_by_game_id=outcome.results_by_game_id, now=NOW
        )
        assert result.written == 0

    def test_summary_dict_reports_the_live_validation_counts(self, repo_with_preseason_cache):
        outcome = resolve_game_results(
            season=SEASON, now=NOW, cfbd_client=_FakeCFBD(raises=_http_error(429)),
            repo_dir=repo_with_preseason_cache, needed_game_ids={GAME_ID},
            espn_client=_espn_for_slate([_espn_event()]),
        )
        summary = outcome.summary_dict()
        assert summary["result_provider"] == ESPN_FALLBACK
        assert summary["games_with_results"] == 1
        assert summary["games_final"] == 1
        assert summary["games_unresolved_fail_closed"] == 0
        assert summary["identity_source"] == "preseason schedule cache"
        assert summary["espn_dates_fetched"] == ["20260828", "20260829"]
