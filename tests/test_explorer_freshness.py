"""The research explorer follows the CFB Script Engine publication it embeds.

App Export rebuilds the explorer (``scripts/research_export.py``) when it is missing, the v1 events
changed, or it is older than 180 minutes -- and, at any age, when the content fingerprint of the
Script Engine payload set (``data/scripting/live/sift``) differs from the one recorded with the
published explorer (``explorer/sources.json``, bound to that tree's ``index.json``). A successful
Script Engine run on main starts App Export; ``force_explorer`` bypasses only the age throttle.
"""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest
from edge_finder_contract import research as R

from tests.test_app_contract_v1 import GAME_KEY, write_accounting_dir, write_data_root
from tests.test_research_export import _v1_export, rx

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "app-export.yml"
T0 = "2026-10-03T12:30:00Z"  # the explorer's generated_at
SOON = "2026-10-03T13:00:00Z"  # 30 minutes later: inside the 180-minute throttle
LATE = "2026-10-03T15:31:00Z"  # past it
INTERVAL = 180 * 60


def _payload(hash_: str = "a" * 64, *, mapped_at: str = "2026-10-03T12:00:00Z", archetype: str = "HOME_CONTROL"):
    return {
        "version": "cfb_script_engine_payload/1.1.0",
        "status": "SINGLE_SCRIPT",
        "script_generation": {
            "artifact_hash": hash_,
            "generated_at": "2026-10-03T11:00:00Z",
            "mapped_at": mapped_at,
            "prices_captured_at": mapped_at,
            "methodology_version": "cfb-script-engine/1.3.0",
            "market_blind": True,
        },
        "game_scripts": [{"role": "PRIMARY", "archetype": archetype, "probability": None}],
    }


def _write(directory: Path, payloads: dict[str, dict], *, order: list[str] | None = None, mtime: int = 0) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.json.gz"):
        old.unlink()
    for key in order or sorted(payloads):
        with gzip.GzipFile(directory / f"{key}.json.gz", "wb", mtime=mtime) as fh:
            fh.write(json.dumps(payloads[key]).encode("utf-8"))
    return directory


@pytest.fixture(scope="module")
def base(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("freshness")
    data_root = write_data_root(root / "live")
    accounting = write_accounting_dir(root / "acct")
    out = root / "app" / "latest"
    _v1_export(out, data_root, accounting, "2026-10-03T12:00:00Z")
    return {"out": out, "data_root": data_root}


@pytest.fixture
def site(base, tmp_path) -> dict:
    """A v1 publication with no explorer yet, and a Script Engine directory holding one payload."""
    out = tmp_path / "app" / "latest"
    shutil.copytree(base["out"], out)
    shutil.rmtree(out / "explorer", ignore_errors=True)
    engine = _write(tmp_path / "sift", {GAME_KEY: _payload()})
    return {"out": out, "engine": engine, "data_root": base["data_root"]}


def _build(site: dict, *, now: str = T0, min_interval: float = 0, engine: Path | None = None) -> dict:
    return rx.export_explorer(
        site["out"],
        data_root=site["data_root"],
        research_root=None,
        now=now,
        min_interval_seconds=min_interval,
        script_engine_dir=engine or site["engine"],
    )


def _due(site: dict, now: str, **kw) -> tuple[bool, str]:
    kw.setdefault("script_engine_dir", site["engine"])
    return rx.explorer_due(site["out"], now=now, min_interval_seconds=INTERVAL, **kw)


def _sources(site: dict) -> dict:
    return json.loads((site["out"] / "explorer" / rx.SOURCES_NAME).read_text(encoding="utf-8"))


def _embedded(site: dict) -> dict | None:
    index = R.read_index(site["out"])
    for rel, entry in index["files"].items():
        if entry["kind"] == "event_research":
            doc = json.loads((site["out"] / "explorer" / rel).read_text(encoding="utf-8"))
            return (doc.get("extensions") or {}).get("script_engine")
    return None


# 1
def test_nothing_changed_inside_the_interval_is_not_due(site):
    _build(site)
    due, reason = _due(site, SOON)
    assert not due and "unchanged" in reason


# 2, 3
def test_a_changed_script_publication_is_due_at_any_age_with_an_explicit_reason(site):
    _build(site)
    _write(site["engine"], {GAME_KEY: _payload("b" * 64, archetype="UNDERDOG_HANGS_AROUND")})
    due, reason = _due(site, SOON)
    assert due
    assert reason.startswith("script-engine publication changed")
    published = rx.recorded_script_engine_fingerprint(site["out"])
    current = rx.script_engine_fingerprint(rx.load_script_engine(site["engine"]))
    assert published[:12] in reason and current[:12] in reason


# 4, 5
def test_the_rebuild_records_the_new_fingerprint_and_the_next_run_is_quiet(site):
    _build(site)
    first = _sources(site)["script_engine"]["fingerprint"]
    _write(site["engine"], {GAME_KEY: _payload("b" * 64)})
    index = _build(site, now=SOON, min_interval=INTERVAL)  # inside the interval, but the publication changed
    assert not index.get("skipped")
    sources = _sources(site)
    current = rx.script_engine_fingerprint(rx.load_script_engine(site["engine"]))
    assert sources["script_engine"]["fingerprint"] == current != first
    assert sources["script_engine"]["payloads"] == 1
    assert sources["script_engine"]["methodology_versions"] == ["cfb-script-engine/1.3.0"]
    assert sources["explorer_index_sha256"] == rx._index_digest(site["out"])
    assert sources["explorer_generated_at"] == index["generated_at"]
    assert _embedded(site)["script_generation"]["artifact_hash"] == "b" * 64  # the explorer carries the new payload
    # an identical publication is not due again, and an App Export run leaves the tree alone
    tree = R.digest_tree(site["out"])
    due, reason = _due(site, SOON)
    assert not due and "unchanged" in reason
    assert _build(site, now=SOON, min_interval=INTERVAL)["skipped"] is True
    assert R.digest_tree(site["out"]) == tree


# 6
def test_the_fingerprint_ignores_file_order_gzip_bytes_and_run_clocks(tmp_path):
    payloads = {"26OCT10UGAALA": _payload("a" * 64), "26OCT10LSUUK": _payload("c" * 64), "26OCT09ISUBYU": _payload()}
    one = _write(tmp_path / "one", payloads, order=["26OCT10UGAALA", "26OCT09ISUBYU", "26OCT10LSUUK"], mtime=0)
    two = _write(tmp_path / "two", payloads, order=["26OCT10LSUUK", "26OCT10UGAALA", "26OCT09ISUBYU"], mtime=1_700_000)
    fp = rx.script_engine_fingerprint(rx.load_script_engine(one))
    assert fp == rx.script_engine_fingerprint(rx.load_script_engine(two))
    # dict insertion order (keys and nested fields) cannot move it
    reordered = {k: dict(reversed(list(payloads[k].items()))) for k in reversed(list(payloads))}
    assert rx.script_engine_fingerprint(reordered) == fp
    # the engine stamps every payload with its run clocks; a run that changed nothing else is the same publication
    reclocked = {k: _payload(p["script_generation"]["artifact_hash"], mapped_at="2026-10-04T09:41:00Z")
                 for k, p in payloads.items()}
    assert rx.script_engine_fingerprint(reclocked) == fp
    # ...but content is identity: a new artifact, a removed game or another methodology version moves it
    assert rx.script_engine_fingerprint({**payloads, "26OCT10UGAALA": _payload("d" * 64)}) != fp
    assert rx.script_engine_fingerprint({k: v for k, v in payloads.items() if k != "26OCT10LSUUK"}) != fp
    other = json.loads(json.dumps(payloads))
    other["26OCT09ISUBYU"]["script_generation"]["methodology_version"] = "cfb-script-engine/1.4.0"
    assert rx.script_engine_fingerprint(other) != fp
    # no publication is a state of its own
    assert rx.script_engine_fingerprint({}) == rx.script_engine_fingerprint(rx.load_script_engine(tmp_path / "none"))
    assert rx.script_engine_fingerprint({}) != fp


# 7
def _block(text: str, header: str) -> str:
    """The lines of ``header`` and everything indented under it (the workflow tests read YAML as text,
    like tests/test_trigger_reliability.py)."""
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if line.strip() == header.strip() and line.startswith(header[:1]))
    indent = len(lines[i]) - len(lines[i].lstrip())
    out = [lines[i]]
    for line in lines[i + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        out.append(line)
    return "\n".join(out)


def _step(text: str, name: str) -> str:
    return _block(text, f"      - name: {name}")


def test_only_a_successful_main_script_engine_run_starts_the_export_and_it_publishes_main():
    text = WORKFLOW.read_text(encoding="utf-8")
    on = _block(text, "on:")
    assert 'workflows: ["Kalshi CFB Market Catalog", "CFB Script Engine"]' in on  # the catalog trigger is kept
    assert "types: [completed]" in on
    export = _block(text, "  export:")
    cond = " ".join(_block(export, "    if: >-").split()[2:])
    assert cond == (
        "github.event_name != 'workflow_run' || github.event.workflow_run.name != 'CFB Script Engine' "
        "|| (github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.head_branch == 'main' "
        "&& github.event.workflow_run.head_repository.full_name == github.repository)"
    )
    # whatever started it, the job publishes main's tree
    assert export.index("    if: >-") < export.index("    steps:")
    first_step = export[export.index("    steps:") :]
    assert first_step.split("\n")[1:4] == ["      - uses: actions/checkout@v4", "        with:", "          ref: main"]
    # and the Script Engine pushes its prospective ledger from main only
    engine = (REPO_ROOT / ".github" / "workflows" / "script-engine.yml").read_text(encoding="utf-8")
    assert "if: github.ref_name == 'main'" in engine


def test_a_sources_record_from_another_tree_cannot_certify_this_explorer(site, tmp_path):
    """A record is trusted only for the exact index it was written with: one copied from another
    tree (another branch's export, an older run) reads as no record, and the explorer rebuilds."""
    _build(site)
    foreign = tmp_path / "foreign"
    shutil.copytree(site["out"], foreign)
    other = {"out": foreign, "engine": _write(tmp_path / "other_sift", {GAME_KEY: _payload("e" * 64)}),
             "data_root": site["data_root"]}
    _build(other)
    shutil.copy(foreign / "explorer" / rx.SOURCES_NAME, site["out"] / "explorer" / rx.SOURCES_NAME)
    assert rx.recorded_script_engine_fingerprint(site["out"]) is None
    _write(site["engine"], {GAME_KEY: _payload("e" * 64)})  # even when the foreign fingerprint matches the payloads
    due, reason = _due(site, SOON)
    assert due and reason == rx.NO_FINGERPRINT_REASON


# 8
def test_changed_v1_events_are_still_due(site):
    _build(site)
    events_path = site["out"] / "events.json"
    events = json.loads(events_path.read_text(encoding="utf-8"))
    events["items"].append(dict(events["items"][0], event_id="evt_new_game_on_the_board"))
    events_path.write_text(json.dumps(events), encoding="utf-8")
    due, reason = _due(site, SOON)
    assert due and reason.startswith("v1 events changed")


# 9
def test_the_age_throttle_still_refreshes_an_unchanged_explorer(site):
    _build(site)
    due, reason = _due(site, LATE)
    assert due and reason.startswith("explorer is") and "refresh every 10800 s" in reason


# 10
def test_an_explorer_published_before_fingerprints_rebuilds_once(site):
    _build(site)
    (site["out"] / "explorer" / rx.SOURCES_NAME).unlink()  # what main published before this change
    due, reason = _due(site, SOON)
    assert due and reason == rx.NO_FINGERPRINT_REASON
    assert not _build(site, now=SOON, min_interval=INTERVAL).get("skipped")
    assert (site["out"] / "explorer" / rx.SOURCES_NAME).exists()
    due, reason = _due(site, SOON)
    assert not due and "unchanged" in reason


def test_a_failed_publish_keeps_the_previous_record_valid(site, monkeypatch):
    _build(site)
    recorded = rx.recorded_script_engine_fingerprint(site["out"])
    _write(site["engine"], {GAME_KEY: _payload("f" * 64)})

    def boom(**_kw):
        raise R.ExplorerError(["injected"])

    monkeypatch.setattr(R, "publish_explorer", boom)
    with pytest.raises(R.ExplorerError):
        _build(site, now=SOON, min_interval=INTERVAL)
    assert rx.recorded_script_engine_fingerprint(site["out"]) == recorded  # the old tree, still described truly
    assert _due(site, SOON)[0]  # and the next run tries again


# 11
def test_force_rebuilds_inside_the_interval_and_the_default_keeps_the_throttle(site, capsys):
    _build(site)
    args = ["--out", str(site["out"]), "--data-root", str(site["data_root"]), "--min-interval-minutes", "180",
            "--script-engine-dir", str(site["engine"]), "--now", SOON]
    # default: nothing changed, inside the interval -> the due check says no, and the export skips
    assert rx.main(args + ["--check-due"]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "due=false"
    assert rx.main(args) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is True
    # forced: due, and rebuilt (validated and published like any other build)
    assert rx.main(args + ["--check-due", "--force"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "due=true" and "forced" in out[1]
    assert rx.main(args + ["--force"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert "skipped" not in summary and summary["generated_at"] == SOON
    assert summary["script_engine"]["fingerprint"] == rx.recorded_script_engine_fingerprint(site["out"])
    assert R.read_index(site["out"])["generated_at"] == SOON
    assert R.verify_explorer(site["out"]) == []


def test_the_workflow_forces_only_on_an_explicit_manual_request():
    text = WORKFLOW.read_text(encoding="utf-8")
    dispatch = _block(text, "  workflow_dispatch:")
    assert "      force_explorer:" in dispatch
    assert "        type: boolean" in dispatch and "        default: false" in dispatch
    export = _block(text, "  export:")
    assert (
        "      FORCE_EXPLORER: ${{ github.event_name == 'workflow_dispatch' && inputs.force_explorer == true }}"
        in export
    )
    for name in ("Is a research explorer rebuild due?", "Build the research explorer (app/latest/explorer)"):
        run = _step(text, name)
        assert 'if [ "$FORCE_EXPLORER" = "true" ]; then FORCE_ARGS=(--force); fi' in run
        assert '"${FORCE_ARGS[@]}"' in run and "--min-interval-minutes 180" in run
    # forcing changes nothing else: the v1 export, the failure gate and the commit step are untouched
    assert "--force" not in _step(text, "Build the app export")
    assert "FORCE" not in _step(text, "Commit app/latest only if changed")
    assert "steps.research_export.outcome == 'failure'" in _step(text, "Fail the job if the research explorer failed")
    assert text.count("--force") == 2
