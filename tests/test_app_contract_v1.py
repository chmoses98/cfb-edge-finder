"""The Edge Finder app export (docs/APP_EXPORT.md) against the vendored contract.

Fixtures are built from the shapes of the REAL committed records (one catalog game trimmed to a
handful of markets, ledger rows in the ``cfb_accounted_wager.v1`` / ``cfb_wager_settlement.v1`` /
``cfb_settlement_amendment.v1`` dialects), with made-up money. The real-data smoke test at the end
runs the exporter against the committed catalog when it is present, and against a checkout of the
``accounting-data`` branch when ``CFB_ACCOUNTING_DIR`` names one.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path

import pytest
from edge_finder_contract import publish, sync

from cfb_edge_finder.accounting import economics, report

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_DATA_ROOT = REPO_ROOT / "data" / "live"
NOW = "2026-10-03T12:00:00Z"
CAPTURED_AT = "2026-10-03T11:40:00.123456Z"
FINGERPRINT = "f" * 64
GAME_KEY = "26OCT03WKUNMSU"
EVENT_TICKER = f"KXNCAAFGAME-{GAME_KEY}"
UUID_HOME = "7a77c54f-511d-4bcd-a4d5-7e84ea5eda89"
UUID_AWAY = "2eafddaf-140c-4c46-9a18-3a8a9d671964"
ON_BOARD_TICKER = f"KXNCAAFTOTAL-{GAME_KEY}-36"
OFF_BOARD_TICKER = "KXNCAAFSPREAD-26SEP12WKUUGA-UGA40"
KEY_ON = "kalshi:v1:" + "a" * 64
KEY_OFF = "kalshi:v1:" + "b" * 64
SECRET_SHAPES = ("PRIVATE KEY", "ghp_", "github_pat_", "Bearer ", "AIRTABLE")


def _load_exporter():
    spec = importlib.util.spec_from_file_location("app_export", REPO_ROOT / "scripts" / "app_export.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app_export = _load_exporter()


# ------------------------------------------------------------------ fixtures from real record shapes


def _market(ticker: str, series: str, family: str, period: str, *, title: str, yes_sub: str, no_sub: str,
            status: str = "active", strike_type: str = "greater", floor: float | None = None,
            team_uuid: str | None = None, custom: dict | None = None, yes_bid=0.46, yes_ask=0.48,
            no_bid=0.52, no_ask=0.54, book_state: str = "two_sided", sentinel: bool = False) -> dict:
    return {
        "market_ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "series_ticker": series,
        "family": family, "period": period, "market_type": "binary", "strike_type": strike_type,
        "floor_strike": floor, "cap_strike": None,
        "custom_strike": custom if custom is not None else ({"football_team": team_uuid} if team_uuid else None),
        "title": title, "yes_sub_title": yes_sub, "no_sub_title": no_sub, "status": status,
        "yes_bid": yes_bid, "yes_ask": yes_ask, "no_bid": no_bid, "no_ask": no_ask,
        "last_price": 0.47, "volume": 120.5, "open_interest": 80.0,
        "close_time": "2026-10-04T23:00:00Z", "occurrence_datetime": "2026-10-03T19:00:00Z",
        "updated_time": "2026-10-03T11:21:00.510475Z",
        "mechanics": {"book_state": book_state, "is_sentinel_full_width_book": sentinel,
                      "two_sided_quote": book_state == "two_sided"},
        "fee": {"model": "quadratic", "model_trade_fee_at_yes_ask": 0.017472, "model_trade_fee_at_no_ask": 0.0174},
        "classification_confidence": "structural", "rules_primary": "",
    }


def fixture_markets() -> list[dict]:
    return [
        _market(f"KXNCAAF1H-{GAME_KEY}-NMSU", "KXNCAAF1H", "first_half_moneyline", "first_half",
                title="New Mexico St. wins the 1st half", yes_sub="New Mexico St.",
                no_sub="New Mexico St. wins 1st Half",
                strike_type="structured", team_uuid=UUID_HOME),
        _market(f"KXNCAAFSPREAD-{GAME_KEY}-WKU10", "KXNCAAFSPREAD", "game_spread", "full_game",
                title="Western Kentucky wins by over 9.5 points", yes_sub="Western Kentucky wins by over 9.5 points",
                no_sub="Western Kentucky wins by over 9.5 points", floor=9.5, team_uuid=UUID_AWAY),
        _market(ON_BOARD_TICKER, "KXNCAAFTOTAL", "game_total", "full_game",
                title="Full Game: Over 35.5 points scored", yes_sub="Over 35.5 points", no_sub="Over 35.5 points",
                floor=35.5),
        _market(f"KXNCAAF1HFT-{GAME_KEY}-NMSUNMSU", "KXNCAAF1HFT", "unknown", "unknown",
                title="New Mexico St. wins 1st Half / New Mexico St. wins game",
                yes_sub="NMSU wins 1H / NMSU wins game", no_sub="NMSU wins 1H / NMSU wins game",
                strike_type="custom", custom={"1st Half Result": "NMSU wins 1st Half"}),
        _market(f"KXNCAAF1Q-{GAME_KEY}-WKU", "KXNCAAF1Q", "quarter_moneyline", "first_quarter",
                title="Western Kentucky wins the 1st quarter", yes_sub="Western Kentucky", no_sub="Western Kentucky",
                status="finalized", strike_type="structured", team_uuid=UUID_AWAY,
                yes_bid=0.0, yes_ask=1.0, no_bid=0.0, no_ask=1.0, book_state="empty_book", sentinel=True),
    ]


def fixture_game(kickoff: str = "2026-10-03T19:00:00Z", milestone: bool = True) -> dict:
    identity = {
        "conference": "CONFERENCE-USA", "division": "FBS", "league": "NCAAFB",
        "main_game_event_ticker": EVENT_TICKER, "milestone_id": "027cd803-662b-46d2-a7b6-c2a47614c1f3",
        "milestone_status": "scheduled", "season_type": "REG", "season_week": 6, "season_year": 2026,
        "source": "kalshi_milestone", "tier": "C",
    } if milestone else {
        "conference": None, "division": None, "league": None, "main_game_event_ticker": None, "milestone_id": None,
        "milestone_status": None, "season_type": None, "season_week": None, "season_year": None,
        "source": "kalshi_event_ticker", "tier": None,
    }
    return {
        "game_key": GAME_KEY, "title": "Western Kentucky at New Mexico St.", "kickoff": kickoff,
        "market_count": 5, "markets_file": f"games/{GAME_KEY}.json", "identity": identity,
        "completeness": {"api_failures": 0, "events_fetched": 5, "failed_event_tickers": [], "markets_discovered": 5},
        "family_distribution": {"first_half_moneyline": 1, "game_spread": 1, "game_total": 1, "unknown": 1,
                                "quarter_moneyline": 1},
        "events": [{"event_ticker": EVENT_TICKER, "series_ticker": "KXNCAAFGAME", "title": "WKU vs NMSU"}],
    }


def write_data_root(root: Path, *, kickoff: str = "2026-10-03T19:00:00Z", milestone: bool = True,
                    captured_at: str = CAPTURED_AT) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "games").mkdir(exist_ok=True)
    game = fixture_game(kickoff, milestone)
    index = {
        "artifact_layout": "slate index; each game's full contract inventory is in its own file",
        "capture": {"authenticated": False, "captured_at": captured_at, "contains_model_projections": False,
                    "content_fingerprint": FINGERPRINT, "source": "kalshi_public_rest_v2"},
        "completeness": {"capture_complete": True, "games_incomplete": [], "games_incomplete_count": 0},
        "games": [game],
    }
    (root / "cfb_market_catalog.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    (root / "games" / f"{GAME_KEY}.json").write_text(
        json.dumps({"game_key": GAME_KEY, "title": game["title"], "markets": fixture_markets()}), encoding="utf-8")
    return root


def ledger_rows() -> tuple[list[dict], list[dict], list[dict]]:
    def wager(key: str, ticker: str, side: str, executed_at: str, wid: str) -> dict:
        return {"contracts": 10.0, "entry_method": "IMPORTED_RECEIPT", "event_refs": {}, "executed_at": executed_at,
                "execution_price": 0.44, "fees_are_estimated": False, "fees_paid": 0.1727,
                "game_date": executed_at[:10],
                "gross_return": None, "import_batch_id": "test-batch", "market_ticker": ticker, "net_profit_loss": None,
                "notes": "", "result": None, "schema_version": "cfb_accounted_wager.v1", "season": 2026,
                "settlement_status": None, "side": side, "source_bet_key": key, "stake": 4.5727, "venue": "kalshi",
                "wager_id": wid, "week": None}
    wagers = [
        wager(KEY_OFF, OFF_BOARD_TICKER, "YES", "2026-09-12T20:52:29Z", "routed-0000000000000000000000off"),
        wager(KEY_ON, ON_BOARD_TICKER, "NO", "2026-10-03T11:50:00Z", "routed-00000000000000000000000on"),
    ]
    settlements = [{
        "gross_return": 10.0, "market_ticker": OFF_BOARD_TICKER, "net_profit_loss": 5.2546, "refusals": [],
        "result": "WON", "schema_version": "cfb_wager_settlement.v1", "settled_at": "2026-09-13T02:28:48.44776Z",
        "settlement_id": "stl-0000000000000000000000off", "settlement_status": "SETTLED", "side": "YES",
        "source_bet_key": KEY_OFF, "venue": "kalshi",
    }]
    amendments = [{
        "amended_at": "2026-09-28T12:46:46.456150+00:00", "amendment_id": "amd-00000000000000000000000a",
        "amends_settlement_id": "stl-0000000000000000000000off",
        "derivation": "net_profit_loss = gross_return - stake",
        "economics_version": "router-settlement-economics.v2",
        "evidence": {"entry_fees": 0.1727, "implied_fee_cost": 0.1727},
        "gross_return": 10.0, "market_ticker": OFF_BOARD_TICKER, "net_profit_loss": 5.4273,
        "original_gross_return": 10.0, "original_net_profit_loss": 5.2546, "original_refusals": [],
        "provenance": "test", "result": "WON", "schema_version": "cfb_settlement_amendment.v1", "side": "YES",
        "source_bet_key": KEY_OFF, "supersedes_economics_version": "router-settlement-economics.v1",
    }]
    return wagers, settlements, amendments


def write_accounting_dir(root: Path) -> Path:
    wagers, settlements, amendments = ledger_rows()
    for sub, rows in (("wagers", wagers), ("settlements", settlements), ("settlement_amendments", amendments)):
        (root / sub).mkdir(parents=True, exist_ok=True)
        (root / sub / "2026.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return root


def run_export(out: Path, data_root: Path, accounting: Path | None, now: str = NOW, **extra: str) -> int:
    argv = ["--out", str(out), "--data-root", str(data_root), "--now", now, "--commit-sha", "abc123"]
    if accounting is not None:
        argv += ["--accounting-dir", str(accounting)]
    for flag, value in extra.items():
        argv += [f"--{flag.replace('_', '-')}", value]
    return app_export.main(argv)


def _read(root: Path, name: str) -> dict:
    return json.loads((root / f"{name}.json").read_text(encoding="utf-8"))


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.json"))}


@pytest.fixture
def synthetic(tmp_path: Path) -> dict:
    data_root = write_data_root(tmp_path / "live")
    accounting = write_accounting_dir(tmp_path / "acct")
    out = tmp_path / "app" / "latest"
    assert run_export(out, data_root, accounting) == 0
    return {"out": out, "data_root": data_root, "accounting": accounting}


# ------------------------------------------------------------------ 1. the vendored contract


def test_vendored_contract_is_intact():
    assert sync.check() == []


# ------------------------------------------------------------------ 2. end to end on the synthetic root


def test_export_publishes_a_consistent_bundle(synthetic):
    out = synthetic["out"]
    assert publish.verify_published(out) == []
    manifest = _read(out, "manifest")
    assert manifest["status"] == "SUCCESS"
    assert manifest["counts"] == {"board": 1, "events": 1, "markets": 6, "model_prices": 0, "recommendations": 0,
                                  "runs": 1, "settlements": 1, "theses": 0, "wagers": 2}
    assert set(manifest["files"]) >= {"events", "markets", "model_prices", "recommendations", "theses", "wagers",
                                      "settlements", "runs", "board", "performance"}
    assert any(name.startswith("event_detail/") for name in manifest["files"])

    events = _read(out, "events")["items"]
    event = events[0]
    assert event["source_ids"]["kalshi_milestone_id"] == "027cd803-662b-46d2-a7b6-c2a47614c1f3"
    assert event["source_ids"]["kalshi_game_key"] == GAME_KEY
    assert event["status"] == "SCHEDULED" and event["start_time_confidence"] == "SCHEDULED"
    names = {p["participant_id"]: p for p in event["participants"]}
    assert names[event["home_participant"]]["display_name"] == "New Mexico St."
    assert names[event["away_participant"]]["display_name"] == "Western Kentucky"
    assert names[event["home_participant"]]["source_ids"] == {"kalshi_team_code": "NMSU",
                                                              "kalshi_football_team": UUID_HOME}

    markets = {m["kalshi_ticker"]: m for m in _read(out, "markets")["items"]}
    assert markets[f"KXNCAAF1H-{GAME_KEY}-NMSU"]["side"] == "HOME"
    assert markets[f"KXNCAAF1H-{GAME_KEY}-NMSU"]["participant_id"] == event["home_participant"]
    assert markets[f"KXNCAAFSPREAD-{GAME_KEY}-WKU10"]["side"] == "AWAY"
    assert markets[f"KXNCAAFSPREAD-{GAME_KEY}-WKU10"]["line"] == 9.5
    assert markets[ON_BOARD_TICKER]["threshold"] == 35.5 and markets[ON_BOARD_TICKER]["side"] == "OVER"
    assert markets[ON_BOARD_TICKER]["market_probability"] == 0.47
    assert markets[f"KXNCAAF1HFT-{GAME_KEY}-NMSUNMSU"]["market_family"] == "unknown"
    assert markets[f"KXNCAAF1HFT-{GAME_KEY}-NMSUNMSU"]["market_status"] == "OPEN"
    sentinel = markets[f"KXNCAAF1Q-{GAME_KEY}-WKU"]
    assert sentinel["market_status"] == "SETTLED" and sentinel["yes_bid"] is None
    assert sentinel["market_probability"] is None
    assert sentinel["extensions"]["sentinel_full_width_book"] is True
    assert set(markets[ON_BOARD_TICKER]["extensions"]) == {"book_state", "fee_at_yes_ask", "strike_type",
                                                           "sentinel_full_width_book"}
    stub = markets[OFF_BOARD_TICKER]
    assert stub["source"] == "cfb_accounting_ledger" and stub["yes_ask"] is None and stub["event_id"] is None
    assert stub["market_family"] == "game_spread" and stub["market_status"] == "SETTLED"

    assert _read(out, "model_prices")["items"] == []
    assert _read(out, "recommendations")["items"] == []
    assert _read(out, "theses")["items"] == []

    wagers = {w["kalshi_ticker"]: w for w in _read(out, "wagers")["items"]}
    on, off = wagers[ON_BOARD_TICKER], wagers[OFF_BOARD_TICKER]
    assert on["event_id"] == event["event_id"] and on["settlement_status"] == "PENDING" and on["settlement_id"] is None
    assert on["source"] == "KALSHI_ROUTER" and on["source_bet_key"] == KEY_ON and on["selection"] == "NO"
    assert on["recommendation_id"] is None and on["model_price_id"] is None
    assert on["linkage"]["market_captured_at"] == "2026-10-03T11:40:00Z"
    assert off["settlement_status"] == "SETTLED" and off["profit_loss"] == 5.4273 and off["payout"] == 10.0
    settlement = _read(out, "settlements")["items"][0]
    assert settlement["settlement_id"] == off["settlement_id"] and settlement["wager_id"] == off["wager_id"]
    assert settlement["result"] == "WON" and settlement["winning_side"] == "YES"
    assert settlement["net_pnl"] == 5.4273 and settlement["extensions"]["as_filed_net_profit_loss"] == 5.2546
    assert settlement["verification_status"] == "EXCHANGE_CONFIRMED"

    run = _read(out, "runs")["items"][0]
    assert run["source_ids"]["native_run_id"] == FINGERPRINT and run["commit_sha"] == "abc123"
    assert run["markets_priced"] == 0 and run["recommendations_created"] == 0 and run["model_version"] is None

    health = _read(out, "health")
    assert health["overall_status"] == "RESEARCH_ONLY"
    assert health["bet_authority"] == "RESEARCH_ONLY" and health["model_status"] == "NOT_APPLICABLE"
    assert health["market_data_status"] == "OK" and health["last_market_capture"] == "2026-10-03T11:40:00Z"
    assert health["thresholds"]["market_data"] == {"fresh_after_seconds": 2700, "stale_after_seconds": 21600}

    board = _read(out, "board")
    assert board["bet_authority"] == "RESEARCH_ONLY" and board["items"][0]["wagers_count"] == 1
    assert "NO_MODEL_PRICES" in board["items"][0]["health_flags"]
    detail = _read(out, board["items"][0]["detail_path"][:-5])
    assert len(detail["markets"]) == 5 and len(detail["wagers"]) == 1 and detail["context"]["teams"]["home_name"]
    performance = _read(out, "performance")
    assert performance["notes"][0].startswith("ACCOUNTING ONLY")
    assert performance["recommended_vs_wagered"] == {"recommendations": 0, "wagers_linked_to_recommendation": 0,
                                                     "wagers_unlinked": 2}


def test_absent_ledger_exports_empty_wagers_and_warns(tmp_path):
    data_root = write_data_root(tmp_path / "live")
    out = tmp_path / "app"
    assert run_export(out, data_root, tmp_path / "nowhere") == 0
    assert publish.verify_published(out) == []
    assert _read(out, "wagers")["items"] == [] and _read(out, "settlements")["items"] == []
    assert _read(out, "manifest")["status"] == "PARTIAL"
    assert any("accounting-data" in w for w in _read(out, "health")["warnings"])
    assert _read(out, "health")["router_status"] == "NOT_APPLICABLE"


def test_event_without_milestone_and_past_kickoff(tmp_path):
    data_root = write_data_root(tmp_path / "live", kickoff="2026-10-03T10:00:00Z", milestone=False)
    out = tmp_path / "app"
    assert run_export(out, data_root, None) == 0
    event = _read(out, "events")["items"][0]
    assert event["source_ids"]["kalshi_game_key"] == GAME_KEY and event["source_ids"]["kalshi_milestone_id"] is None
    assert event["start_time_confidence"] == "ESTIMATED" and event["status"] == "LIVE"


# ------------------------------------------------------------------ 3. determinism


def test_two_runs_are_byte_identical(synthetic, tmp_path):
    other = tmp_path / "app2"
    assert run_export(other, synthetic["data_root"], synthetic["accounting"]) == 0
    first, second = _read(synthetic["out"], "manifest"), _read(other, "manifest")
    assert first["run_id"] == second["run_id"]
    assert {n: f["sha256"] for n, f in first["files"].items()} == {n: f["sha256"] for n, f in second["files"].items()}
    assert _tree_bytes(synthetic["out"]) == _tree_bytes(other)
    for name, entry in first["files"].items():
        text = (synthetic["out"] / entry["path"]).read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == entry["sha256"], name


def test_skip_unchanged_publishes_nothing_on_a_quiet_rerun(synthetic):
    before = _tree_bytes(synthetic["out"])
    rc = app_export.main(["--out", str(synthetic["out"]), "--data-root", str(synthetic["data_root"]),
                          "--accounting-dir", str(synthetic["accounting"]), "--now", "2026-10-03T12:10:00Z",
                          "--commit-sha", "abc123", "--skip-unchanged"])
    assert rc == 0
    assert _tree_bytes(synthetic["out"]) == before
    # a freshness flip IS a change worth publishing
    rc = app_export.main(["--out", str(synthetic["out"]), "--data-root", str(synthetic["data_root"]),
                          "--accounting-dir", str(synthetic["accounting"]), "--now", "2026-10-04T12:10:00Z",
                          "--commit-sha", "abc123", "--skip-unchanged"])
    assert rc == 0 and _tree_bytes(synthetic["out"]) != before
    assert _read(synthetic["out"], "health")["overall_status"] == "STALE"


# ------------------------------------------------------------------ 4. failure safety


def test_a_broken_input_leaves_the_payload_alone_and_marks_health(synthetic):
    out = synthetic["out"]
    before = _tree_bytes(out)
    index = synthetic["data_root"] / "cfb_market_catalog.json"
    index.write_text("{not json", encoding="utf-8")
    assert run_export(out, synthetic["data_root"], synthetic["accounting"], now="2026-10-03T12:30:00Z") == 1
    after = _tree_bytes(out)
    assert {k: v for k, v in after.items() if k != "health.json"} == \
        {k: v for k, v in before.items() if k != "health.json"}
    health = _read(out, "health")
    assert health["overall_status"] == "DEGRADED"
    assert health["components"]["export"]["status"] == "DEGRADED"
    assert health["payload_run_id"] == _read(out, "manifest")["run_id"]
    assert health["last_market_capture"] == "2026-10-03T11:40:00Z"
    assert health["errors"] and "JSONDecodeError" in health["errors"][0]
    assert publish.verify_published(out) == []


def test_a_failure_with_no_previous_payload_is_unavailable(tmp_path):
    out = tmp_path / "app"
    assert run_export(out, tmp_path / "missing-live", None) == 1
    assert sorted(p.name for p in out.iterdir()) == ["health.json"]
    health = _read(out, "health")
    assert health["overall_status"] == "UNAVAILABLE" and health["payload_run_id"] is None


# ------------------------------------------------------------------ 5. stale data


def test_far_future_now_reads_stale(synthetic):
    out = synthetic["out"]
    assert run_export(out, synthetic["data_root"], synthetic["accounting"], now="2026-11-03T12:00:00Z") == 0
    health = _read(out, "health")
    assert health["overall_status"] == "STALE" and health["market_data_status"] == "STALE"
    assert _read(out, "board")["items"][0]["data_freshness"] == "STALE"
    assert "STALE_DATA" in _read(out, "board")["items"][0]["health_flags"]


# ------------------------------------------------------------------ 6. naive timestamps


_NAIVE_TS = re.compile(r'"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?"')


def test_a_naive_kickoff_is_refused(tmp_path):
    data_root = write_data_root(tmp_path / "live", kickoff="2026-10-03T19:00:00")
    out = tmp_path / "app"
    assert run_export(out, data_root, None) == 1
    health = _read(out, "health")
    assert health["overall_status"] == "UNAVAILABLE" and "NaiveTimestampError" in health["errors"][0]
    assert not list((out).glob("events.json"))


def test_a_naive_capture_time_is_refused(tmp_path):
    data_root = write_data_root(tmp_path / "live", captured_at="2026-10-03 11:40:00")
    assert run_export(tmp_path / "app", data_root, None) == 1


def test_no_naive_timestamp_is_ever_emitted(synthetic):
    for path in synthetic["out"].rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert not _NAIVE_TS.search(text), f"{path.name} carries a timestamp without a zone"


# ------------------------------------------------------------------ 8. no secret-shaped strings


def _assert_no_secret_shapes(root: Path) -> None:
    for path in root.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for shape in SECRET_SHAPES:
            assert shape not in text, f"{path.relative_to(root)} contains {shape!r}"


def test_no_secret_shaped_strings_in_the_synthetic_export(synthetic):
    _assert_no_secret_shapes(synthetic["out"])


# ------------------------------------------------------------------ 9. the ledger's own sums


def _ledger_summary(accounting: Path):
    def rows(sub: str) -> list[dict]:
        path = accounting / sub / "2026.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    canonical = economics.apply_amendments(rows("settlements"), rows("settlement_amendments"))
    return report.summarize(rows("wagers"), 2026, canonical)


def _assert_pnl_matches_ledger(out: Path, accounting: Path) -> None:
    summary = _ledger_summary(accounting)
    totals = _read(out, "performance")["totals"]
    wagers = _read(out, "wagers")["items"]
    settlements = {s["settlement_id"]: s for s in _read(out, "settlements")["items"]}
    assert totals["wagers"] == summary.wagers
    assert totals["settled"] == summary.settled and totals["pending"] == summary.unsettled
    assert totals["won"] == summary.won and totals["lost"] == summary.lost
    assert round(totals["stake"], 2) == round(summary.staked, 2)
    assert totals["settled_with_economics"] == summary.profit_loss_established
    assert round(totals["net_pnl"] or 0.0, 4) == round(summary.realized_profit_loss, 4)
    for w in wagers:
        if w["settlement_status"] == "SETTLED":
            assert w["settlement_id"] in settlements
            assert settlements[w["settlement_id"]]["market_id"] == w["market_id"]
            assert settlements[w["settlement_id"]]["net_pnl"] == w["profit_loss"]
    assert round(sum(w["fees"] for w in wagers), 4) == round(summary.fees_paid, 4)


def test_performance_totals_equal_the_ledger_report(synthetic):
    _assert_pnl_matches_ledger(synthetic["out"], synthetic["accounting"])


# ------------------------------------------------------------------ 7. the real committed data


@pytest.mark.skipif(not (REAL_DATA_ROOT / "cfb_market_catalog.json").exists(),
                    reason="no committed catalog at data/live (nothing real to export)")
def test_real_committed_catalog_exports_cleanly(tmp_path):
    """The production proof. The ledger lives on the accounting-data branch, which is not checked
    out here; set CFB_ACCOUNTING_DIR to a `git archive` of it to cover wagers too."""
    accounting = os.environ.get("CFB_ACCOUNTING_DIR")
    accounting_dir = Path(accounting) if accounting and Path(accounting).is_dir() else None
    out = tmp_path / "app" / "latest"
    index = json.loads((REAL_DATA_ROOT / "cfb_market_catalog.json").read_text(encoding="utf-8"))
    assert run_export(out, REAL_DATA_ROOT, accounting_dir, now=index["capture"]["captured_at"]) == 0
    assert publish.verify_published(out) == []
    manifest = _read(out, "manifest")
    assert manifest["counts"]["events"] == len(index["games"])
    assert manifest["counts"]["markets"] >= sum(g.get("market_count") or 0 for g in index["games"])
    assert manifest["counts"]["model_prices"] == 0 and manifest["counts"]["recommendations"] == 0
    health = _read(out, "health")
    assert health["overall_status"] == "RESEARCH_ONLY"
    assert health["bet_authority"] == "RESEARCH_ONLY" and health["model_status"] == "NOT_APPLICABLE"
    assert _read(out, "runs")["items"][0]["source_ids"]["native_run_id"] == index["capture"]["content_fingerprint"]
    _assert_no_secret_shapes(out)
    for path in out.rglob("*.json"):
        assert not _NAIVE_TS.search(path.read_text(encoding="utf-8")), path.name
    if accounting_dir is not None:
        _assert_pnl_matches_ledger(out, accounting_dir)
        assert manifest["counts"]["wagers"] > 0
    else:
        assert manifest["status"] == "PARTIAL"
