"""Every append-only script-ledger stream is stored as bounded segments (2026-10-28).

From 2026-10-17 every push of the `script-ledger` branch was refused: `realized.jsonl` (one settled record per
V1 publication row) grew past GitHub's 100 MiB per-file limit inside a run, so each run's prospective rows were
lost with the push. These tests pin the segmented storage for all four streams, the readers that consume them,
the file-size guard and the workflow wiring that keeps an unpushed commit instead of dropping it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cfb_edge_finder.scripting import ledger as L

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import check_ledger_file_sizes as size_guard  # noqa: E402
import script_engine  # noqa: E402


def _rows(n: int, pad: int = 100) -> list[dict]:
    return [{"game_key": f"G{i}", "artifact_hash": f"h{i}", "kind": "PUBLICATION", "pad": "x" * pad} for i in range(n)]


# ------------------------------------------------------------------ the generic stream


def test_a_stream_rolls_over_reads_back_in_order_and_never_touches_an_earlier_segment(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "SEGMENT_MAX_BYTES", 300)
    first = tmp_path / "realized.jsonl"
    rows = _rows(25)
    written = []
    for r in rows:
        written.append(L.append_stream_line(first, r))
        if len(written) == 3:
            frozen = first.read_bytes()
    segs = L.stream_segments(first)
    assert segs[0] == first and len(segs) > 10  # past part-0009: numeric order, not just lexical luck
    assert [p.name for p in segs[1:]] == [f"realized.part-{i:04d}.jsonl" for i in range(2, len(segs) + 1)]
    assert all(p.stat().st_size <= 300 for p in segs)
    assert L.read_stream(first) == rows  # nothing dropped, nothing duplicated, order kept
    assert first.read_bytes() == frozen  # a closed segment is never appended to again
    assert sum(len(p.read_text().splitlines()) for p in segs) == len(rows)


def test_segments_of_different_streams_never_mix(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "SEGMENT_MAX_BYTES", 200)
    for r in _rows(6):
        L.append_stream_line(tmp_path / "publications.jsonl", r)
        L.append_stream_line(tmp_path / "publications_v2.jsonl", {**r, "v": 2})
    assert all(not p.name.startswith("publications_v2") for p in L.stream_segments(tmp_path / "publications.jsonl"))
    assert [r["v"] for r in L.read_stream(tmp_path / "publications_v2.jsonl")] == [2] * 6
    assert len(L.read_stream(tmp_path / "publications.jsonl")) == 6


def test_a_missing_stream_reads_empty_and_one_row_too_big_for_any_segment_is_refused(tmp_path, monkeypatch):
    assert L.read_stream(tmp_path / "realized_v2.jsonl") == []
    monkeypatch.setattr(L, "SEGMENT_MAX_BYTES", 50)
    with pytest.raises(ValueError):
        L.append_stream_line(tmp_path / "realized.jsonl", {"pad": "x" * 100})
    assert not (tmp_path / "realized.jsonl").exists()


def test_a_historical_first_file_above_the_cap_is_kept_byte_for_byte(tmp_path, monkeypatch):
    """The 2026 realized.jsonl was 72.9 MiB when segmentation reached it: it stays, new rows go to part-0002."""
    monkeypatch.setattr(L, "SEGMENT_MAX_BYTES", 1_000)
    first = tmp_path / "realized.jsonl"
    first.write_text(json.dumps({"artifact_hash": "old", "kind": "PUBLICATION"}) + "\n" + " " * 2_000 + "\n")
    before = first.read_bytes()
    L.append_stream_line(first, {"artifact_hash": "new", "kind": "PUBLICATION"})
    assert first.read_bytes() == before
    assert (tmp_path / "realized.part-0002.jsonl").exists()
    assert [r["artifact_hash"] for r in L.read_stream(first)] == ["old", "new"]


def test_v2_publications_are_segmented_and_dedupe_across_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "SEGMENT_MAX_BYTES", 400)
    from cfb_edge_finder.scripting import LEDGER_V2_SCHEMA_VERSION

    rows = [
        {"schema_version": LEDGER_V2_SCHEMA_VERSION, "game_key": f"G{i}", "claims_artifact_hash": f"c{i}",
         "kind": "FINAL_PREGAME", "pad": "y" * 150}
        for i in range(8)
    ]
    for r in rows:
        assert L.append_v2(tmp_path, 2026, r) is True
    assert len(L.stream_segments(tmp_path / "2026" / L.PUBLICATIONS_V2)) > 1
    assert L.append_v2(tmp_path, 2026, rows[0]) is False  # already in segment 1
    assert L.read_rows_v2(tmp_path, 2026) == rows


# ------------------------------------------------------------------ settle / report through the segments


def _fake_football(game_ids):
    rows = []
    for g in game_ids:
        rows.append(SimpleNamespace(game_id=g, team_id=f"{g}H", site="home"))
        rows.append(SimpleNamespace(game_id=g, team_id=f"{g}A", site="away"))
    return rows, None, None, None


def _publication(i: int) -> dict:
    return {
        "game_key": f"K{i}", "event_id": f"E{i}", "artifact_hash": f"a{i}", "kind": "PUBLICATION",
        "teams": {"home": {"team_id": f"E{i}H"}, "away": {"team_id": f"E{i}A"}},
        "expressions": [{"id": f"x{j}", "labels": [], "compat": "S", "entry": 0.5, "cost_per_contract": 0.5,
                         "wins_when": None} for j in range(3)],
    }


def test_settle_writes_realized_records_into_segments_without_drops_or_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "SEGMENT_MAX_BYTES", 900)
    season_dir = tmp_path / "2026"
    for i in range(12):
        L.append(tmp_path, 2026, _publication(i))
    # the pre-segmentation realized file already settled the first four publications
    season_dir.joinpath("realized.jsonl").write_text(
        "".join(json.dumps({"artifact_hash": f"a{i}", "kind": "PUBLICATION", "game_key": f"K{i}"}) + "\n"
                for i in range(4))
    )
    before = season_dir.joinpath("realized.jsonl").read_bytes()
    monkeypatch.setattr(script_engine, "load_football", lambda _d: _fake_football([f"E{i}" for i in range(12)]))
    monkeypatch.setattr(script_engine, "load_artifact", lambda *a: None)
    monkeypatch.setattr(
        script_engine, "realized_record",
        lambda home, away, row, plays: {"game_key": row["game_key"], "artifact_hash": row["artifact_hash"],
                                        "features": {"pad": "z" * 200}},
    )
    args = SimpleNamespace(football_dir=tmp_path, ledger_dir=tmp_path, season=2026)
    assert script_engine.cmd_settle(args) == 0
    assert season_dir.joinpath("realized.jsonl").read_bytes().startswith(before)  # append-only: old bytes untouched
    realized = L.read_stream(season_dir / "realized.jsonl")
    assert [r["artifact_hash"] for r in realized] == [f"a{i}" for i in range(12)]
    assert len(L.stream_segments(season_dir / "realized.jsonl")) > 2
    # a second run settles nothing new
    assert script_engine.cmd_settle(args) == 0
    assert len(L.read_stream(season_dir / "realized.jsonl")) == 12


def test_settle_v2_and_both_reports_read_every_segment(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "SEGMENT_MAX_BYTES", 300)
    season_dir = tmp_path / "2026"
    season_dir.mkdir()
    import cfb_edge_finder.scripting.realized_v2 as RV2

    settled = [
        {"game_key": f"K{i}", "claims_artifact_hash": f"c{i}", "kind": "FINAL_PREGAME", "pad": "q" * 120}
        for i in range(6)
    ]
    calls = []

    def fake_settle_rows(rows, done, football, load_claims, load_v1):
        calls.append(set(done))
        return [r for r in settled if RV2.key_of(r) not in done]

    monkeypatch.setattr(RV2, "settle_rows", fake_settle_rows)
    monkeypatch.setattr(script_engine, "load_football", lambda _d: ([], None, None, None))
    args = SimpleNamespace(football_dir=tmp_path, ledger_dir=tmp_path, season=2026)
    assert script_engine.cmd_settle_v2(args) == 0
    assert len(L.stream_segments(season_dir / "realized_v2.jsonl")) > 1
    assert script_engine.cmd_settle_v2(args) == 0  # the second run sees all six as done, across segments
    assert len(calls[1]) == 6 and L.read_stream(season_dir / "realized_v2.jsonl") == settled

    seen = {}
    monkeypatch.setattr(RV2, "build_report_v2", lambda pubs, realized: seen.setdefault("v2", realized) and {
        "final_pregame_games": 0, "settled_games": len(realized)})
    monkeypatch.setattr(RV2, "render_markdown_v2", lambda report: "")
    assert script_engine.cmd_report_v2(SimpleNamespace(ledger_dir=tmp_path, season=2026, out=tmp_path / "r")) == 0
    assert seen["v2"] == settled

    for r in [{"artifact_hash": f"a{i}", "kind": "PUBLICATION", "pad": "w" * 120} for i in range(6)]:
        L.append_stream_line(season_dir / "realized.jsonl", r)
    import cfb_edge_finder.scripting.report as REP

    monkeypatch.setattr(REP, "build_report", lambda pubs, realized: seen.setdefault("v1", realized) and {
        "publications": {"games": 0}, "settled": {"games": len(realized)}})
    monkeypatch.setattr(REP, "render_markdown", lambda report: "")
    assert script_engine.cmd_report(SimpleNamespace(ledger_dir=tmp_path, season=2026, out=tmp_path / "r")) == 0
    assert len(seen["v1"]) == 6 and len(L.stream_segments(season_dir / "realized.jsonl")) > 1


def test_the_research_conductor_reads_the_whole_v2_stream(tmp_path, monkeypatch):
    import cfb_research_conductor as C

    assert C.read_stream is L.read_stream
    src = (REPO / "scripts" / "cfb_research_conductor.py").read_text()
    assert 'read_stream(ledger_dir / season_dir / "publications_v2.jsonl")' in src
    lab = (REPO / "scripts" / "signal_lab_wave2_dry_run.py").read_text()
    assert "read_stream(args.ledger)" in lab


# ------------------------------------------------------------------ size guard + workflow


def test_the_size_guard_fails_below_the_host_limit_and_passes_segmented_trees(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "publications.jsonl").write_bytes(b"x" * 1000)
    assert size_guard.main([str(ok)]) == 0
    big = tmp_path / "big"
    big.mkdir()
    with (big / "realized.jsonl").open("wb") as fh:  # sparse: the size the host sees, without the disk
        fh.truncate(size_guard.GUARD_BYTES)
    assert size_guard.main([str(big)]) == 1
    assert size_guard.GUARD_BYTES < size_guard.HOST_LIMIT_BYTES
    assert L.SEGMENT_MAX_BYTES < size_guard.WARN_BYTES


def test_script_engine_workflow_guards_sizes_and_keeps_an_unpushed_commit():
    wf = (REPO / ".github" / "workflows" / "script-engine.yml").read_text()
    guard = wf.index("scripts/check_ledger_file_sizes.py")
    push = wf.index("git push origin script-ledger")
    assert guard < push
    assert "id: ledger_push" in wf
    assert "script-ledger-unpushed-${{ github.run_id }}" in wf and "retention-days: 90" in wf
    assert "git bundle create" in wf


def test_the_conductor_workflow_fetches_every_v2_segment():
    wf = (REPO / ".github" / "workflows" / "cfb-research-conductor.yml").read_text()
    assert r"/publications_v2(\.part-[0-9]+)?\.jsonl$" in wf
    assert 'git show "FETCH_HEAD:data/scripting/ledger/$SEASON/publications_v2.jsonl"' not in wf
