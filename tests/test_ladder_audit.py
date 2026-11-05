"""End-to-end audit on the REAL captured ESPN summary 401856705 (VAN at UGA, Q1 = 7-7, total 14) with a fake-transport ladder."""

import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect_live_context as transport  # noqa: E402

from cfb_edge_finder.ladder_archive import espn_raw as er
from cfb_edge_finder.ladder_archive import espn_store as es
from cfb_edge_finder.ladder_archive import ladder_1q as l1
from cfb_edge_finder.ladder_archive import ladder_audit as la
from cfb_edge_finder.ladder_archive import ladder_store as store

sys.path.insert(0, str(Path(__file__).parent))
from test_ladder_1q_archive import Clock, Fake, Resp  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "espn"
KICK = datetime(2026, 10, 3, 16, 45, tzinfo=UTC)
EV = "KXNCAAF1QTOTAL-26OCT03VANUGA"
RULE = "If the teams collectively score more than {x} points in the 1st quarter of the Vanderbilt vs Georgia college football game originally scheduled for Oct 3, 2026, then the market resolves to Yes."


def mk(x, result=None, status="open", yb="0.40", ya="0.45"):
    m = {
        "ticker": f"{EV}-{int(x * 2)}",
        "event_ticker": EV,
        "series_ticker": "KXNCAAF1QTOTAL",
        "status": status,
        "floor_strike": x,
        "strike_type": "greater",
        "yes_bid_dollars": yb,
        "yes_ask_dollars": ya,
        "no_bid_dollars": "0.55",
        "no_ask_dollars": "0.60",
        "volume_fp": "1.00",
        "open_interest_fp": "1.00",
        "rules_primary": RULE.format(x=x),
    }
    if result:
        m["result"] = result
    return m


class F(Fake):
    def __call__(self, url, params=None, headers=None, timeout=None):
        if url.endswith("/milestones"):
            return Resp(
                {
                    "milestones": [
                        {
                            "id": "m",
                            "start_date": KICK.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "details": {"league": "NCAAFB", "status": "scheduled"},
                            "primary_event_tickers": [EV],
                            "related_event_tickers": [],
                        }
                    ],
                    "cursor": "",
                }
            )
        return super().__call__(url, params, headers, timeout)


def build(tmp, pre_markets, post_markets, espn_body=True):
    lad = Path(tmp) / "lad"
    espn = Path(tmp) / "espn"
    g = l1.RawKalshiGetter(
        http_get=F({"open": pre_markets}), sleep=lambda s: None, clock=Clock(KICK - timedelta(hours=2))
    )
    r1 = l1.capture(g, run_id="pre", prev_state=None, clock=g._clock)
    store.write_run(lad, r1)
    g = l1.RawKalshiGetter(
        http_get=F({"settled": post_markets}), sleep=lambda s: None, clock=Clock(KICK + timedelta(hours=5))
    )
    r2 = l1.capture(g, run_id="post", prev_state=r1.state, clock=g._clock)
    store.write_run(lad, r2)
    if espn_body:
        body = (FIX / "summary_401856705.json").read_bytes()
        row = er.index_row(
            "401856705",
            {"ok": True, "status": 200, "uri": "u", "body": body, "fetched_at": "2026-10-04T06:00:00Z", "error": None},
            {"date": "2026-10-03T16:45Z", "state": "post"},
            "e1",
        )
        es.write_run(
            espn, {"run_id": "e1", "finished_at": "2026-10-04T06:00:01Z", "started_at": "x"}, [row], {"401856705": body}
        )
    return lad, (espn if espn_body else None)


def sched():
    return [
        {
            "id": "401856705",
            "date": "2026-10-03T16:45Z",
            "completed": True,
            "state": "post",
            "competitors": [
                {
                    "names": ["Vanderbilt Commodores", "Vanderbilt", "VAN"],
                    "team_id": "238",
                    "home_away": "away",
                    "conference_id": "8",
                },
                {
                    "names": ["Georgia Bulldogs", "Georgia", "UGA"],
                    "team_id": "61",
                    "home_away": "home",
                    "conference_id": "8",
                },
            ],
        }
    ]


def run_audit(lad, espn):
    return la.audit(la.load_rows(lad), la.load_manifests(lad), sched(), la.espn_period1(espn), transport.name_variants)


def test_strict_cohort_requires_pregame_quote_identity_orientation_start_score_and_consistent_settlement():
    with tempfile.TemporaryDirectory() as d:
        pre = [mk(12.5), mk(13.5), mk(14.5)]
        post = [mk(12.5, "yes", "settled"), mk(13.5, "yes", "settled"), mk(14.5, "no", "settled")]
        lad, espn = build(d, pre, post)
        rep = run_audit(lad, espn)
        c = rep["cohort"]
        assert c["tickers_observed"] == 3 and c["strict_cohort"] == 3 and c["settlement_inconsistent_with_espn_q1"] == 0
        assert (
            rep["join_status"] == {"MATCHED": 3}
            and c["start_corroborated_by_espn"] == 3
            and rep["games_with_strict_cohort"] == 1
        )
        assert rep["rows_by_lead_bucket"]["1-6h"] == 3 and rep["rows_by_lead_bucket"]["after_start"] == 3
        b = rep["market_only_baseline"]
        assert b["n_tickers"] == 3 and b["n_games"] == 1 and "no fees" in b["note"]


def test_a_kalshi_result_that_contradicts_espn_period_score_is_flagged_and_excluded():
    with tempfile.TemporaryDirectory() as d:
        lad, espn = build(
            d, [mk(13.5)], [mk(13.5, "no", "settled")]
        )  # ESPN Q1 total is 14 > 13.5, so 'no' is inconsistent
        c = run_audit(lad, espn)["cohort"]
        assert c["settlement_inconsistent_with_espn_q1"] == 1 and c["strict_cohort"] == 0


def test_no_pregame_observation_means_no_strict_cohort_even_if_settled():
    with tempfile.TemporaryDirectory() as d:
        lad, espn = build(d, [], [mk(6.5, "yes", "settled")])
        rep = run_audit(lad, espn)
        assert (
            rep["cohort"]["with_eligible_pregame_quote"] == 0
            and rep["cohort"].get("strict_cohort", 0) == 0
            and rep["baseline_status"] == "NO_STRICT_COHORT_YET"
        )


def test_missing_espn_summary_leaves_score_unavailable_not_inferred():
    with tempfile.TemporaryDirectory() as d:
        lad, espn = build(d, [mk(6.5)], [mk(6.5, "yes", "settled")], espn_body=False)
        c = run_audit(lad, espn)["cohort"]
        assert (
            c.get("official_q1_score_available", 0) == 0 and c.get("strict_cohort", 0) == 0 and c["kalshi_settled"] == 1
        )


def test_ambiguous_or_unmatched_identity_is_not_joined():
    with tempfile.TemporaryDirectory() as d:
        lad, espn = build(d, [mk(6.5)], [mk(6.5, "yes", "settled")])
        two = sched() + [dict(sched()[0], id="999")]
        rep = la.audit(la.load_rows(lad), la.load_manifests(lad), two, la.espn_period1(espn), transport.name_variants)
        assert rep["join_status"] == {"AMBIGUOUS_MATCH": 1} and rep["cohort"].get("joined_to_espn_game", 0) == 0
        rep = la.audit(la.load_rows(lad), la.load_manifests(lad), [], {}, transport.name_variants)
        assert rep["join_status"] == {"NO_MATCH": 1}


def test_orientation_mismatch_blocks_the_cohort():
    with tempfile.TemporaryDirectory() as d:
        bad = mk(6.5)
        bad["rules_primary"] = bad["rules_primary"].replace("more than 6.5", "fewer than 6.5")
        lad, espn = build(d, [bad], [dict(bad, status="settled", result="yes")])
        c = run_audit(lad, espn)["cohort"]
        assert c.get("orientation_verified", 0) == 0 and c.get("strict_cohort", 0) == 0


def test_quote_observed_after_espn_kickoff_or_with_disagreeing_start_never_enters_the_cohort():
    """Kalshi thinks the game starts 30 minutes AFTER ESPN's real kickoff: a quote seen in that window is NOT pregame."""
    with tempfile.TemporaryDirectory() as d:
        lad = Path(d) / "lad"
        late_start = KICK + timedelta(minutes=30)

        class Late(F):
            def __call__(self, url, params=None, headers=None, timeout=None):
                if url.endswith("/milestones"):
                    return Resp(
                        {
                            "milestones": [
                                {
                                    "id": "m",
                                    "start_date": late_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "details": {"league": "NCAAFB", "status": "scheduled"},
                                    "primary_event_tickers": [EV],
                                    "related_event_tickers": [],
                                }
                            ],
                            "cursor": "",
                        }
                    )
                return Fake.__call__(self, url, params, headers, timeout)

        g = l1.RawKalshiGetter(
            http_get=Late({"open": [mk(13.5)]}), sleep=lambda s: None, clock=Clock(KICK + timedelta(minutes=10))
        )
        r = l1.capture(g, run_id="late", prev_state=None, clock=g._clock)
        assert r.rows[0]["eligible_pregame"]  # Kalshi's (wrong) start says it is still pregame ...
        store.write_run(lad, r)
        g = l1.RawKalshiGetter(
            http_get=F({"settled": [mk(13.5, "yes", "settled")]}),
            sleep=lambda s: None,
            clock=Clock(KICK + timedelta(hours=5)),
        )
        store.write_run(lad, l1.capture(g, run_id="post", prev_state=r.state, clock=g._clock))
        body = (FIX / "summary_401856705.json").read_bytes()
        espn = Path(d) / "espn"
        row = er.index_row(
            "401856705",
            {"ok": True, "status": 200, "uri": "u", "body": body, "fetched_at": "2026-10-04T06:00:00Z", "error": None},
            {"date": "2026-10-03T16:45Z", "state": "post"},
            "e1",
        )
        es.write_run(
            espn, {"run_id": "e1", "finished_at": "2026-10-04T06:00:01Z", "started_at": "x"}, [row], {"401856705": body}
        )
        c = run_audit(lad, espn)["cohort"]
        assert (
            c["with_eligible_pregame_quote"] == 1
            and c["quote_after_espn_kickoff_or_start_disagrees"] == 1
            and c.get("strict_cohort", 0) == 0
        )


def test_parenthetical_state_aliases_and_awaiting_espn_record_are_distinguished():
    names = lambda *n: {"names": list(n)}  # noqa: E731
    ev = [
        {
            "id": "1",
            "date": "2026-10-31T20:00Z",
            "competitors": [
                names("Miami Hurricanes", "Miami", "MIA"),
                names("North Carolina Tar Heels", "North Carolina", "UNC"),
            ],
        }
    ]
    rules = "If the teams collectively score more than 10.5 points in the 1st quarter of the Miami (FL) vs North Carolina college football game originally scheduled for Oct 31, 2026, then the market resolves to Yes."
    assert la.match_event(rules, ev, transport.name_variants) == ("1", "MATCHED")
    ev_oh = [
        {
            "id": "2",
            "date": "2026-10-31T20:00Z",
            "competitors": [
                names("Miami (OH) RedHawks", "Miami (OH)", "M-OH"),
                names("North Carolina Tar Heels", "North Carolina"),
            ],
        }
    ]
    assert (
        la.match_event(rules, ev_oh, transport.name_variants)[1] == "NO_MATCH"
    )  # Miami (FL) must never match Miami (OH)


def test_upcoming_games_without_an_espn_record_are_awaiting_not_disagreeing():
    with tempfile.TemporaryDirectory() as d:
        lad, _ = build(d, [mk(13.5)], [], espn_body=False)
        c = run_audit(lad, None)["cohort"]
        assert (
            c["eligible_quote_awaiting_espn_game_record"] == 1
            and c.get("quote_after_espn_kickoff_or_start_disagrees", 0) == 0
        )


def test_scores_the_archive_flagged_as_inconsistent_are_never_used_to_settle():
    with tempfile.TemporaryDirectory() as d:
        lad = Path(d) / "lad"
        espn = Path(d) / "espn"
        pre, post = [mk(13.5)], [mk(13.5, "yes", "settled")]
        lad, _ = build(d, pre, post, espn_body=False)
        body = json.loads((FIX / "summary_401856705.json").read_bytes())
        body["header"]["competitions"][0]["competitors"][0]["score"] = "99"  # linescores no longer sum to the final
        raw = json.dumps(body).encode()
        row = er.index_row(
            "401856705",
            {"ok": True, "status": 200, "uri": "u", "body": raw, "fetched_at": "2026-10-04T06:00:00Z", "error": None},
            {"date": "2026-10-03T16:45Z", "state": "post"},
            "e1",
        )
        assert "PERIOD_SCORES_DISAGREE_WITH_FINAL" in row["missingness"]
        es.write_run(
            espn, {"run_id": "e1", "finished_at": "2026-10-04T06:00:01Z", "started_at": "x"}, [row], {"401856705": raw}
        )
        c = run_audit(lad, espn)["cohort"]
        assert c.get("official_q1_score_available", 0) == 0 and c.get("strict_cohort", 0) == 0
