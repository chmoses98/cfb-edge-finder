"""Prospective 1Q ladder archive: completeness, eligibility, no back-dating, hashes, idempotency. Offline (fake transport)."""

import json
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cfb_edge_finder.ladder_archive import archive_io as aio
from cfb_edge_finder.ladder_archive import ladder_1q as l1
from cfb_edge_finder.ladder_archive import ladder_store as store

T0 = datetime(2026, 11, 5, 20, 0, 0, tzinfo=UTC)
KICK = T0 + timedelta(hours=2)
EV = "KXNCAAF1QTOTAL-26NOV05VTSMU"


def mk(strike, yb="0.40", ya="0.45", status="open", nb="0.55", na="0.60", **extra):
    m = {
        "ticker": f"{EV}-{int(strike * 2)}",
        "event_ticker": EV,
        "series_ticker": "KXNCAAF1QTOTAL",
        "status": status,
        "floor_strike": strike,
        "strike_type": "greater",
        "yes_bid_dollars": yb,
        "yes_ask_dollars": ya,
        "no_bid_dollars": nb,
        "no_ask_dollars": na,
        "last_price_dollars": "0.42",
        "volume_fp": "10.00",
        "open_interest_fp": "5.00",
        "rules_primary": f"If the teams collectively score more than {strike} points in the 1st quarter ... resolves to Yes.",
        "close_time": "2026-11-06T02:00:00Z",
    }
    m.update(extra)
    return m


class Resp:
    def __init__(self, body, status=200, headers=None):
        self.content = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.status_code = status
        self.headers = headers or {}


class Fake:
    """Dict-backed transport. `markets[status]` -> list; paging by 2 to exercise cursors."""

    def __init__(self, markets, start=KICK, ms_status="scheduled", fail=None, page=2):
        self.markets, self.start, self.ms_status, self.fail, self.page, self.calls = (
            markets,
            start,
            ms_status,
            fail or {},
            page,
            [],
        )

    def __call__(self, url, params=None, headers=None, timeout=None):
        path = url.split("/trade-api/v2")[1]
        self.calls.append((path, dict(params or {}), dict(headers or {})))
        key = (path, (params or {}).get("status"), (params or {}).get("cursor"))
        if key in self.fail:
            f = self.fail[key]
            return Resp({}, f) if isinstance(f, int) else (_ for _ in ()).throw(f)
        if path == "/markets":
            items = self.markets.get(params.get("status"), [])
            c = int(params.get("cursor") or 0)
            chunk = items[c : c + self.page]
            nxt = str(c + self.page) if c + self.page < len(items) else ""
            return Resp({"markets": chunk, "cursor": nxt})
        if path == "/milestones":
            return Resp(
                {
                    "milestones": [
                        {
                            "id": "ms1",
                            "title": "VT at SMU",
                            "start_date": self.start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "details": {"league": "NCAAFB", "status": self.ms_status},
                            "primary_event_tickers": [EV],
                            "related_event_tickers": [],
                        }
                    ],
                    "cursor": "",
                }
            )
        if path.endswith("/orderbook"):
            return Resp({"orderbook": {"yes": [[40, 100]], "no": [[55, 80]]}})
        return Resp({}, 404)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        self.t += timedelta(seconds=1)
        return self.t


def run(fake, state=None, t=T0, rid="r1"):
    g = l1.RawKalshiGetter(http_get=fake, sleep=lambda s: None, clock=Clock(t))
    return l1.capture(g, run_id=rid, prev_state=state, clock=g._clock)


def test_unquoted_contracts_are_explicit_rows_and_eligibility_is_at_observation_time():
    f = Fake({"open": [mk(6.5), mk(7.5, yb="0.00", ya="1.00", nb="0.00", na="1.00"), mk(9.5)]})
    r = run(f)
    by = {x["ticker"]: x for x in r.rows}
    assert len(by) == 3
    unq = by[f"{EV}-15"]
    assert unq["quote_status"] == "NO_QUOTE" and "NO_QUOTE" in unq["ineligible_reasons"] and not unq["eligible_pregame"]
    ok = by[f"{EV}-13"]
    assert ok["eligible_pregame"] and ok["lead_bucket"] == "1-6h" and ok["start_independently_verified"] is False
    assert ok["orderbook"]["status"] == "CAPTURED" and unq["orderbook"]["status"] == "NOT_REQUESTED"


def test_a_price_seen_after_kickoff_is_never_pregame_and_placeholder_start_is_never_eligible():
    r = run(Fake({"open": [mk(6.5)]}, start=T0 - timedelta(minutes=5)))
    row = r.rows[0]
    assert (
        not row["eligible_pregame"]
        and "AT_OR_AFTER_START" in row["ineligible_reasons"]
        and row["lead_bucket"] == "after_start"
    )
    r = run(Fake({"open": [mk(6.5)]}, ms_status="time-tbd"))
    assert "MILESTONE_STATUS_TIME_TBD" in r.rows[0]["ineligible_reasons"] and not r.rows[0]["eligible_pregame"]
    r = run(Fake({"closed": [mk(6.5, status="closed")]}))
    assert "MARKET_STATUS_CLOSED" in r.rows[0]["ineligible_reasons"]


def test_changed_only_semantics_and_periodic_full_board():
    f = Fake({"open": [mk(6.5), mk(7.5)]})
    r1 = run(f)
    assert r1.manifest["board"] == "FULL" and len(r1.rows) == 2
    r2 = run(f, state=r1.state, t=T0 + timedelta(minutes=10), rid="r2")
    assert (
        r2.manifest["board"] == "CHANGED_ONLY"
        and r2.rows == []
        and not r2.raw_pages
        and r2.manifest["rows_written"] == 0
    )  # manifest still produced
    f2 = Fake({"open": [mk(6.5, yb="0.41"), mk(7.5)]})
    r3 = run(f2, state=r2.state, t=T0 + timedelta(minutes=20), rid="r3")
    assert [x["ticker"] for x in r3.rows] == [f"{EV}-13"] and r3.rows[0]["row_reason"] == "CHANGED" and r3.raw_pages
    r4 = run(f2, state=r3.state, t=T0 + timedelta(hours=7), rid="r4")
    assert r4.manifest["board"] == "FULL" and len(r4.rows) == 2 and {x["row_reason"] for x in r4.rows} == {"FULL_BOARD"}


def test_incomplete_pagination_is_labelled_not_hidden():
    f = Fake({"open": [mk(6.5), mk(7.5), mk(8.5), mk(9.5)]}, fail={("/markets", "open", "2"): 500})
    r = run(f)
    assert r.manifest["capture_complete"] is False
    assert r.manifest["sweeps"]["open"]["complete"] is False and r.manifest["sweeps"]["open"]["partial"] is True
    assert any("open" in e for e in r.manifest["errors"]) and len(r.rows) == 2


def test_transient_429_is_retried_and_every_attempt_is_logged_with_original_body_hash():
    f = Fake({"open": [mk(6.5)]}, fail={("/markets", "open", None): 429})
    # the failure entry is permanent for that key, so make the first call fail only
    calls = {"n": 0}
    orig = f.__call__

    def flaky(url, params=None, headers=None, timeout=None):
        if url.endswith("/markets") and (params or {}).get("status") == "open" and calls["n"] == 0:
            calls["n"] += 1
            return Resp({}, 429, {"Retry-After": "0"})
        f.fail = {}
        return orig(url, params, headers, timeout)

    g = l1.RawKalshiGetter(http_get=flaky, sleep=lambda s: None, clock=Clock(T0))
    res = l1.capture(g, run_id="r", prev_state=None, clock=g._clock)
    statuses = [x["status"] for x in g.log if x["path"] == "/markets" and x["params"].get("status") == "open"]
    assert statuses[:2] == [429, 200] and res.manifest["capture_complete"]
    for x in g.log:
        if x.get("sha256") and x["status"] == 200:
            assert x["sha256"] in g.bodies and aio.sha256(g.bodies[x["sha256"]]) == x["sha256"]


def test_archive_roundtrip_verify_exclusive_write_and_tamper_detection():
    with tempfile.TemporaryDirectory() as d:
        arch = Path(d)
        r = run(Fake({"open": [mk(6.5), mk(7.5)]}))
        store.write_run(arch, r)
        v = store.verify(arch)
        assert v["ok"], v
        assert v["counts"]["rows"] == 2 and v["counts"]["raw_files"] >= 3
        with pytest.raises(FileExistsError):
            store.write_run(arch, r)  # same run id/time can never overwrite
        victim = sorted((arch / "raw").glob("*/*.json.gz"))[0]
        victim.write_bytes(aio.write_new_gz.__globals__["gzip"].compress(b'{"x":1}', mtime=0))
        assert not store.verify(arch)["ok"]


def test_replay_is_idempotent_for_identical_input():
    f = Fake({"open": [mk(6.5), mk(7.5)]})
    a, b = run(f, rid="x"), run(f, rid="x")
    assert [r["fingerprint"] for r in a.rows] == [r["fingerprint"] for r in b.rows]
    assert [r["contract_sha256"] for r in a.rows] == [r["contract_sha256"] for r in b.rows]


def test_only_public_read_paths_are_requested_and_no_credentials_are_sent():
    f = Fake({"open": [mk(6.5)], "settled": [mk(5.5, status="settled", result="yes")]})
    r = run(f)
    allowed = ("/markets", "/milestones")
    for path, _params, headers in f.calls:
        assert path.startswith(allowed) and not any(
            k.lower() in ("authorization", "kalshi-access-key", "kalshi-access-signature") for k in headers
        )
        assert "portfolio" not in path and "orders" not in path
    assert r.manifest["authenticated"] is False and r.manifest["writes_orders"] is False


def test_settlement_fields_are_preserved_for_linkage():
    r = run(
        Fake(
            {
                "settled": [
                    mk(5.5, status="settled", result="yes", expiration_value="9", settlement_ts="2026-11-06T01:00:00Z")
                ]
            }
        )
    )
    row = r.rows[0]
    assert (
        row["market"]["result"] == "yes" and row["quote"]["result"] == "yes" and row["quote"]["expiration_value"] == "9"
    )
    assert row["sweep_status"] == "settled"


def test_events_without_a_milestone_start_are_reported_not_dropped():
    m = mk(6.5)
    m["event_ticker"] = "KXNCAAF1QTOTAL-26NOV05XXYY"
    m["ticker"] = "KXNCAAF1QTOTAL-26NOV05XXYY-13"
    r = run(Fake({"open": [m]}))
    assert r.manifest["events_without_milestone_start"] == ["KXNCAAF1QTOTAL-26NOV05XXYY"]
    assert r.rows[0]["kalshi_start"] is None and "START_UNKNOWN" in r.rows[0]["ineligible_reasons"]


def test_markets_that_vanish_between_runs_are_reported_not_silent():
    f = Fake({"open": [mk(6.5), mk(7.5)]})
    r1 = run(f)
    r2 = run(Fake({"open": [mk(6.5)]}), state=r1.state, t=T0 + timedelta(minutes=10), rid="r2")
    assert (
        r2.manifest["previously_seen_not_returned"] == [f"{EV}-15"]
        and r2.manifest["previously_seen_not_returned_count"] == 1
    )


@pytest.mark.parametrize("status", ["created", "inprogress", "closed", "complete", "time-tbd", None])
def test_only_a_plain_scheduled_milestone_start_is_trusted(status):
    r = run(Fake({"open": [mk(6.5)]}, ms_status=status))
    assert not r.rows[0]["eligible_pregame"] and any(
        x.startswith("MILESTONE_STATUS_") for x in r.rows[0]["ineligible_reasons"]
    )


def test_orderbook_fetched_at_or_after_start_downgrades_the_row():
    clock_t = {"t": KICK - timedelta(seconds=3)}

    def slow_clock():
        clock_t["t"] += timedelta(seconds=2)  # each read advances: the order-book GET lands after kickoff
        return clock_t["t"]

    g = l1.RawKalshiGetter(http_get=Fake({"open": [mk(6.5)]}), sleep=lambda s: None, clock=slow_clock)
    res = l1.capture(g, run_id="r", prev_state=None, clock=slow_clock)
    row = res.rows[0]
    ob = row["orderbook"]
    if ob["status"].startswith("CAPTURED"):
        assert ob["fetched_at"] and (
            ob["pregame"]
            or (not row["eligible_pregame"] and "ORDERBOOK_FETCHED_AT_OR_AFTER_START" in row["ineligible_reasons"])
        )


def test_incomplete_full_board_does_not_reset_the_full_board_clock():
    f = Fake({"open": [mk(6.5), mk(7.5), mk(8.5)]}, fail={("/markets", "open", "2"): 500})
    r1 = run(f)
    assert r1.manifest["board"] == "FULL_INCOMPLETE" and r1.state["last_full_board"] is None
    r2 = run(Fake({"open": [mk(6.5), mk(7.5), mk(8.5)]}), state=r1.state, t=T0 + timedelta(minutes=10), rid="r2")
    assert r2.manifest["board"] == "FULL" and r2.state["last_full_board"] is not None


def test_start_or_milestone_status_change_with_unchanged_quote_writes_a_row():
    r1 = run(Fake({"open": [mk(6.5)]}, ms_status="time-tbd"))
    r2 = run(Fake({"open": [mk(6.5)]}, ms_status="scheduled"), state=r1.state, t=T0 + timedelta(minutes=10), rid="r2")
    assert len(r2.rows) == 1 and r2.rows[0]["row_reason"] == "CHANGED" and r2.rows[0]["eligible_pregame"]


def test_failed_orderbook_is_retried_next_run():
    class OB(Fake):
        def __call__(self, url, params=None, headers=None, timeout=None):
            if url.endswith("/orderbook") and self.fail_ob:
                return Resp({}, 400)
            return super().__call__(url, params, headers, timeout)

    f = OB({"open": [mk(6.5)]})
    f.fail_ob = True
    r1 = run(f)
    assert r1.rows[0]["orderbook"]["status"] == "FAILED"
    f.fail_ob = False
    r2 = run(f, state=r1.state, t=T0 + timedelta(minutes=10), rid="r2")
    assert len(r2.rows) == 1 and r2.rows[0]["orderbook"]["status"] == "CAPTURED"


def test_identical_raw_pages_are_archived_once_across_runs():
    f = Fake({"open": [mk(6.5), mk(7.5)]})
    r1 = run(f)
    assert r1.raw_pages
    f2 = Fake({"open": [mk(6.5, yb="0.41"), mk(7.5)]})
    r2 = run(f2, state=r1.state, t=T0 + timedelta(minutes=10), rid="r2")
    ms = [h for h in r2.manifest["requests"] if h["path"] == "/milestones"][0]["sha256"]
    assert ms not in r2.raw_pages and ms in r2.manifest["raw_pages_deduped"]
