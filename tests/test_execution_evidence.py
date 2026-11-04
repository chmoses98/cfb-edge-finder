"""Point-in-time evidence provenance: source id, observed_at, as_of and one of four statuses.

Fixtures for the fresh-valid case AND every invalid case, run through the real entry point
(``prepare-live --evidence-dir`` -> ``evaluate`` -> ``candidates``). Nothing here invents an observation: a
game with no record is NEVER_COLLECTED and stays blocked.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import collect_live_context as collector  # noqa: E402

from cfb_edge_finder.execution.cli import main  # noqa: E402
from cfb_edge_finder.execution.context import ContextField, GameContext  # noqa: E402
from cfb_edge_finder.execution.context_sources import availability_field  # noqa: E402
from cfb_edge_finder.execution.evidence import (  # noqa: E402
    EvidenceStatus,
    classify_availability,
    classify_projection,
    classify_quote,
    load_availability_evidence,
    publish_availability_evidence,
    record_for,
    write_index,
)
from cfb_edge_finder.execution.handicap import parse_handicap  # noqa: E402
from tests.execution_fakes import CAPTURED_AT, GAME_KEY, NOW, catalog_dir  # noqa: E402
from tests.test_execution_candidates import handicap  # noqa: E402

T = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)
WINDOW = timedelta(hours=18)
H = timedelta(hours=1)


def avail(**kw):
    base = {"quality": "fresh", "source": "espn.core.injuries", "observed_at": (T - 2 * H).isoformat()}
    base.update(kw)
    return base


def status(block, decision=T):
    return classify_availability(block, decision_time=decision, max_age=WINDOW)


# ------------------------------------------------------------------ classification


def test_availability_four_states_and_future():
    ok = status(avail())
    assert ok.status is EvidenceStatus.OBSERVED and ok.source_id == "espn.core.injuries"
    assert ok.observed_at == T - 2 * H and ok.guard_time == T - 2 * H
    assert status(avail(observed_at=(T - 19 * H).isoformat())).status is EvidenceStatus.STALE
    assert status(avail(observed_at=(T + H).isoformat())).status is EvidenceStatus.FROM_FUTURE
    tried = status({"quality": "missing", "source": "espn.core.injuries", "attempted_at": (T - H).isoformat(),
                    "detail": "HTTP 503"})
    assert tried.status is EvidenceStatus.UNAVAILABLE and tried.guard_time is None
    assert tried.attempted_at == T - H and "503" in tried.detail
    never = status({"quality": "missing", "source": "not_attempted", "observed_at": None})
    assert never.status is EvidenceStatus.NEVER_COLLECTED and never.guard_time is None
    assert status(None).status is EvidenceStatus.NEVER_COLLECTED


def test_partial_or_undatable_observation_is_unavailable_and_exposes_no_time():
    partial = status(avail(quality="partial", detail="one team's list could not be read"))
    assert partial.status is EvidenceStatus.UNAVAILABLE and partial.guard_time is None
    assert status(avail(observed_at="not a time")).status is EvidenceStatus.UNAVAILABLE
    assert status(avail(observed_at="2026-11-07T13:00:00")).status is EvidenceStatus.UNAVAILABLE  # naive


def test_source_stated_as_of_is_carried_but_never_gates():
    item = status(avail(source_as_of=(T - 400 * H).isoformat()))
    assert item.status is EvidenceStatus.OBSERVED  # freshness is the READ time, not the source's own date
    assert item.as_of == T - 400 * H


def projection(at, source="external_handicap"):
    payload = handicap().as_dict()
    payload.update(packet_hash=None, handicapped_at=at, source=source)
    return parse_handicap(payload)


def proj(h, decision=T):
    return classify_projection(h, decision_time=decision, max_age=timedelta(hours=12))


def test_projection_states_never_default_to_now():
    assert proj(None).status is EvidenceStatus.NEVER_COLLECTED
    undated = proj(projection(None))
    assert undated.status is EvidenceStatus.UNAVAILABLE and undated.guard_time is None
    assert proj(projection("2026-11-07T10:00:00")).status is EvidenceStatus.UNAVAILABLE  # naive: not guessed
    assert proj(projection("garbage")).status is EvidenceStatus.UNAVAILABLE
    fresh = proj(projection((T - 3 * H).isoformat()))
    assert fresh.status is EvidenceStatus.OBSERVED and fresh.observed_at == T - 3 * H
    assert proj(projection((T - 13 * H).isoformat())).status is EvidenceStatus.STALE
    assert proj(projection((T + H).isoformat())).status is EvidenceStatus.FROM_FUTURE
    assert projection("2026-11-07T10:00:00+02:00").handicapped_at == "2026-11-07T08:00:00+00:00"


def test_quote_item():
    assert classify_quote(None, decision_time=T, max_age=H).status is EvidenceStatus.NEVER_COLLECTED
    assert classify_quote(T - 10 * H, decision_time=T, max_age=H).status is EvidenceStatus.STALE
    assert classify_quote(T - timedelta(minutes=5), decision_time=T, max_age=H).status is EvidenceStatus.OBSERVED


def test_recording_a_handicap_does_not_stamp_now_on_an_undated_projection(tmp_path):
    from cfb_edge_finder.execution.state import StateStore

    store = StateStore(tmp_path)
    packet = {"game_key": GAME_KEY, "packet_hash": "x", "contracts": [], "factual_context": {}}
    state = store.record_handicap(packet, projection(None))
    assert state.handicap_timestamp is None  # was: datetime.now()
    assert state.handicap_recorded_at is not None  # when it was RECORDED is a different fact


# ------------------------------------------------------------------ the collector


def test_availability_field_distinguishes_not_attempted_from_attempted_and_failed():
    skipped = availability_field(None, None, observed_at=T.isoformat(), attempted=False)
    assert skipped.quality == "missing" and skipped.attempted_at is None
    failed = availability_field(None, None, observed_at=T.isoformat(), errors={"home": "HTTP 403"})
    assert failed.quality == "missing" and failed.attempted_at == T.isoformat() and "403" in failed.detail
    empty = availability_field({"items": []}, {"items": []}, observed_at=T.isoformat())
    assert empty.quality == "fresh" and empty.observed_at == T.isoformat() and empty.values["home_listed"] == 0
    dated = availability_field(
        {"items": [{"athlete": {"displayName": "A"}, "date": "2026-11-06T12:00Z"}]}, {"items": []},
        observed_at=T.isoformat(),
    )
    assert dated.source_as_of == "2026-11-06T12:00:00+00:00"
    half = availability_field({"items": []}, None, observed_at=T.isoformat(), errors={"away": "HTTP 500"})
    assert half.quality == "partial" and "500" in half.detail


def test_collect_game_records_what_was_attempted():
    event = None
    ctx = collector.collect_game(
        {"game_key": GAME_KEY, "kickoff": T.isoformat(), "game_metadata": {"teams": {}}},
        event=event, history={}, injuries={"home": None, "away": None}, odds=None,
        cfbd={"advanced": None, "coaches": None}, as_of=T,
    )
    # no ESPN event matched: the collector ran and could not locate the source -> UNAVAILABLE, not NEVER
    assert ctx.fields["availability"].attempted_at == T.isoformat()
    assert ctx.fields["efficiency"].attempted_at is None  # nothing even tried CFBD


def test_context_roundtrip_keeps_attempt_and_source_time():
    f = ContextField("availability", source="s", observed_at=T.isoformat(), quality="fresh",
                     attempted_at=T.isoformat(), source_as_of="2026-11-06T00:00:00+00:00")
    ctx = GameContext(GAME_KEY, {"availability": f}, collected_at=T.isoformat())
    back = GameContext.from_dict(ctx.as_dict()).fields["availability"]
    assert (back.attempted_at, back.source_as_of) == (f.attempted_at, f.source_as_of)


# ------------------------------------------------------------------ publishing committed evidence


def _context_file(dir_: Path, key: str, field: ContextField) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{key}.json").write_text(json.dumps(
        {"game_key": key, "domains": {"availability": field.as_dict()}}))


def test_publish_never_invents_and_never_downgrades_a_real_observation(tmp_path):
    ctxd, evd = tmp_path / "ctx", tmp_path / "ev"
    keys = {"A", "B", "C", "GONE_NOT_IN_CATALOG"}
    good = availability_field({"items": []}, {"items": []}, observed_at=T.isoformat())
    _context_file(ctxd, "A", good)
    _context_file(ctxd, "B", availability_field(None, None, observed_at=T.isoformat(), errors={"home": "HTTP 503"}))
    # C: collector never touched availability at all
    _context_file(ctxd, "C", availability_field(None, None, observed_at=T.isoformat(), attempted=False))
    c1 = publish_availability_evidence(context_dir=ctxd, catalog_game_keys=keys - {"GONE_NOT_IN_CATALOG"},
                                       evidence_dir=evd, run_at=T.isoformat())
    assert c1["observed"] == 1
    assert {p.stem for p in evd.glob("*.json")} == {"A", "B"}  # C: no record -> NEVER_COLLECTED
    store = load_availability_evidence(evd)
    assert store["A"].observed_at == T.isoformat() and store["B"].observed_at is None
    assert store["B"].attempted_at == T.isoformat()
    # a later FAILED run keeps the earlier real observation and only notes the failed attempt
    later = T + 6 * H
    _context_file(ctxd, "A", availability_field(None, None, observed_at=later.isoformat(),
                                                errors={"home": "HTTP 500"}))
    c2 = publish_availability_evidence(context_dir=ctxd, catalog_game_keys={"A", "B", "C"},
                                       evidence_dir=evd, run_at=later.isoformat())
    assert c2["kept_older_observation"] == 1
    a = json.loads((evd / "A.json").read_text())
    assert a["availability"]["observed_at"] == T.isoformat()
    assert a["last_failed_attempt_at"] == later.isoformat()
    # a game leaving the catalog is pruned
    c3 = publish_availability_evidence(context_dir=ctxd, catalog_game_keys={"A"}, evidence_dir=evd,
                                       run_at=later.isoformat())
    assert c3["pruned"] == 1 and not (evd / "B.json").exists()
    doc = write_index(evd, {"A", "X"}, later.isoformat())
    assert doc["games_with_a_record"] == 1 and doc["games_without_a_record_never_collected"] == 1


def test_record_roundtrip_is_loadable(tmp_path):
    f = availability_field({"items": []}, {"items": []}, observed_at=T.isoformat())
    d = tmp_path / "ev"
    d.mkdir()
    (d / "G.json").write_text(json.dumps(record_for("G", None, f, T.isoformat())))
    assert load_availability_evidence(d)["G"].quality == "fresh"
    assert load_availability_evidence(tmp_path / "nope") == {}
    assert load_availability_evidence(None) == {}


# ------------------------------------------------------------------ end to end through the CLI


def _walk(tmp_path: Path, *, evidence: ContextField | None, handicapped_at: str | None, decision: datetime):
    catalog = catalog_dir(tmp_path)
    out = tmp_path / "exec"
    ev = tmp_path / "evidence"
    if evidence is not None:
        ev.mkdir()
        (ev / f"{GAME_KEY}.json").write_text(json.dumps(record_for(GAME_KEY, None, evidence, NOW.isoformat())))
    assert main(["prepare-live", "--catalog-dir", str(catalog), "--out-dir", str(out), "--evidence-dir", str(ev),
                 "--as-of", NOW.isoformat(), "--date", "all"]) == 0
    manifest = json.loads((out / "shard_manifest.json").read_text())
    shard = manifest["shards"][0]["shard"]
    pk = json.loads((out / "shards" / f"{shard}.json").read_text())["games"][0]
    payload = {
        "schema_version": "cfb_handicap_payload/2.0.0", "game_key": pk["game_key"],
        "packet_hash": pk["packet_hash"], "teams": {"home": "Ole Miss", "away": "LSU"}, "confidence": "test",
        "thesis": "t", "opposing_case": "o", "handicapped_at": handicapped_at,
        "period_distributions": {"full_game": {
            "home_mean": 30.0, "away_mean": 21.0, "home_sd": 10.5, "away_sd": 10.0, "correlation": 0.1,
            "uncertainty": {"margin_points": 3.0, "total_points": 5.0}}},
        "explicit_probabilities": {},
    }
    hc = tmp_path / "hc.json"
    hc.write_text(json.dumps([payload]))
    assert main(["evaluate", "--shard", shard, "--out-dir", str(out), "--handicaps", str(hc),
                 "--min-edge", "0.02"]) == 0
    assert main(["candidates", "--shard", shard, "--out-dir", str(out), "--min-edge", "0.02",
                 "--no-decision-record", "--as-of", decision.isoformat()]) == 0
    art = json.loads((out / "candidates" / f"{shard}.candidates.json").read_text())
    assert art["candidates"]
    return art, pk


DECISION = NOW + timedelta(minutes=5)


def fresh_field(observed=NOW - 1 * H):
    return availability_field({"items": []}, {"items": []}, observed_at=observed.isoformat())


def test_fresh_supported_candidates_are_actionable_with_full_provenance(tmp_path):
    art, pk = _walk(tmp_path, evidence=fresh_field(), handicapped_at=(NOW - 2 * H).isoformat(), decision=DECISION)
    assert pk["factual_context"]["domains"]["availability"]["observed_at"] == (NOW - H).isoformat()
    for cand in art["candidates"]:
        review = cand["pit_review"]
        assert review["actionable"] is True and review["blocking_guards"] == []
        ev = review["evidence"]
        assert ev["availability"]["status"] == "OBSERVED"
        assert ev["availability"]["source_id"] == "espn.core.injuries"
        assert ev["availability"]["observed_at"] == (NOW - H).isoformat()
        assert ev["projection"]["status"] == "OBSERVED"
        assert ev["projection"]["observed_at"] == (NOW - 2 * H).isoformat()
        assert ev["quote"]["status"] == "OBSERVED"
    assert art["pit_review"]["evidence_status"]["availability"] == {"OBSERVED": len(art["candidates"])}


@pytest.mark.parametrize(
    ("evidence", "handicapped_at", "decision", "guard", "etype", "expected"),
    [
        (None, "ok", DECISION, "MISSING_AVAILABILITY", "availability", "NEVER_COLLECTED"),
        (availability_field(None, None, observed_at=(NOW - H).isoformat(), errors={"home": "HTTP 503"}),
         "ok", DECISION, "MISSING_AVAILABILITY", "availability", "UNAVAILABLE"),
        (fresh_field(NOW - 20 * H), "ok", DECISION, "AVAILABILITY_STALE", "availability", "STALE"),
        (fresh_field(NOW + 2 * H), "ok", DECISION, "AVAILABILITY_FROM_FUTURE", "availability", "FROM_FUTURE"),
        (fresh_field(), None, DECISION, "MISSING_PROJECTION_AS_OF", "projection", "UNAVAILABLE"),
        (fresh_field(), (NOW - 30 * H).isoformat(), DECISION, "PROJECTION_STALE", "projection", "STALE"),
        (fresh_field(), (NOW + 3 * H).isoformat(), DECISION, "PROJECTION_FROM_FUTURE", "projection", "FROM_FUTURE"),
        (fresh_field(), "ok", NOW + timedelta(hours=3), "QUOTE_STALE", "quote", "STALE"),
    ],
)
def test_every_invalid_case_stays_blocked_with_its_own_reason(
    tmp_path, evidence, handicapped_at, decision, guard, etype, expected
):
    ts = (NOW - 2 * H).isoformat() if handicapped_at == "ok" else handicapped_at
    art, _pk = _walk(tmp_path, evidence=evidence, handicapped_at=ts, decision=decision)
    for cand in art["candidates"]:
        review = cand["pit_review"]
        assert review["actionable"] is False
        assert guard in review["blocking_guards"]
        assert review["evidence"][etype]["status"] == expected


def test_evidence_never_overrides_a_context_store_that_really_observed(tmp_path):
    from cfb_edge_finder.execution.slate import merge_availability_evidence

    own = GameContext.empty(GAME_KEY, reason="r", as_of=NOW)
    fields = dict(own.fields)
    fields["availability"] = fresh_field(NOW - 3 * H)
    ctx = GameContext(GAME_KEY, fields, NOW.isoformat())
    merged = merge_availability_evidence(ctx, fresh_field(NOW - 1 * H), game_key=GAME_KEY, as_of=NOW)
    assert merged is ctx
    assert merge_availability_evidence(None, None, game_key=GAME_KEY, as_of=NOW) is None
    filled = merge_availability_evidence(None, fresh_field(), game_key=GAME_KEY, as_of=NOW)
    assert filled.fields["availability"].observed_at == (NOW - H).isoformat()
    assert CAPTURED_AT < NOW


def test_injury_url_has_no_season_segment_which_espn_answers_404_for():
    url = collector.injury_url("2649")
    assert url.endswith("/college-football/teams/2649/injuries")
    assert "/seasons/" not in url
