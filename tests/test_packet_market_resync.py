"""The research packets' market files never disagree with the execution index committed beside them.

Two workflows write main on their own schedules: the Kalshi catalog (data/live, which holds the execution index) and the
packet build (data/research_packets, whose markets*.json are derived from that index). Committed independently, main
held a catalog its packets disagreed with between the two runs -- 2026-11-18 21:00 the index listed 286 contracts for
26NOV21IOWAILL while its markets.json (built 20:34) held 140 -- and the strict packet check failed main and every PR
(cfb-edge-finder #309). The fix is ordering: every commit that changes either side re-derives the market files from the
index in its own tree (scripts/resync_packet_markets.sh), including after each rebase.

These tests pin (1) that the offline markets-only re-price reproduces a full build exactly, never adds a game and keeps
the football side; (2) the git choreography of a real race and a real conflict, in throwaway repositories; and (3) the
wiring in both workflows.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts/build_research_packets.py"
WORKFLOWS = ROOT / ".github/workflows"
RESYNC = ROOT / "scripts/resync_packet_markets.sh"
REAPPLY = ROOT / "scripts/reapply_owned_path.sh"


# ---------------------------------------------------------------------------------------------------------------------
# (1) The re-price itself, on the committed slate
# ---------------------------------------------------------------------------------------------------------------------


def _load(p: Path):
    return json.loads(p.read_text())


def _committed_games() -> list[str]:
    index = _load(ROOT / "data/research_packets/index.json")
    exec_keys = {g["game_key"] for g in _load(ROOT / "data/live/cfb_execution_index.json")["games"]}
    keys = [g["game_key"] for g in index["games"] if g["game_key"] in exec_keys]
    return [k for k in keys if (ROOT / f"data/research_packets/games/{k}/packet.json").exists()]


@pytest.fixture(scope="module")
def two_games() -> list[str]:
    games = _committed_games()
    if len(games) < 2:
        pytest.skip("the committed slate holds fewer than two indexed games")
    # The two largest menus: the most contracts to get wrong.
    exec_rows = {g["game_key"]: g for g in _load(ROOT / "data/live/cfb_execution_index.json")["games"]}
    return sorted(games, key=lambda k: -exec_rows[k]["market_count"])[:2]


def _scratch_packets(tmp: Path, games: list[str]) -> Path:
    """A packet tree holding the committed index, slates, reference tables, ESPN caches and the named games."""
    src = ROOT / "data/research_packets"
    out = tmp / "research_packets"
    out.mkdir()
    shutil.copy2(src / "index.json", out / "index.json")
    for sub in ("slates", "reference", "sources"):
        shutil.copytree(src / sub, out / sub)
    for g in games:
        shutil.copytree(src / "games" / g, out / "games" / g)
    return out


def _build(out: Path, games: list[str], *extra: str) -> None:
    args = [sys.executable, str(BUILDER), "--no-fetch", "--out-dir", str(out), *extra]
    for g in games:
        args += ["--game", g]
    subprocess.run(args, check=True, capture_output=True, text=True)


def _markets(gdir: Path) -> tuple[dict, list[dict]]:
    parts = [_load(p) for p in sorted(gdir.glob("markets*.json"))]
    head = {k: v for k, v in parts[0].items() if k not in ("contracts", "built_at")}
    return head, [c for p in parts for c in p.get("contracts", [])]


def _age_market_files(gdir: Path) -> None:
    """Make the game's market files look like they were built from an OLDER execution index: drop half the contracts
    (the catalog has since listed more rungs) and stamp a fingerprint the current index does not carry."""
    for p in sorted(gdir.glob("markets*.json")):
        body = _load(p)
        body["contracts"] = body.get("contracts", [])[: len(body.get("contracts", [])) // 2]
        if "price_provenance" in body:
            body["price_provenance"]["game_fingerprint"] = "stale-fingerprint"
        p.write_text(json.dumps(body))


def test_markets_only_reprice_reproduces_a_full_build_exactly(tmp_path, two_games):
    """The catalog commit re-prices packets offline. Its markets must equal what a full build would publish from the
    same execution index -- every contract, every field (sides, all-in costs, ref_freq) and the header (side_teams)."""
    out = _scratch_packets(tmp_path, two_games)
    _build(out, two_games)  # a full build: records the reference class behind ref_freq
    full = {g: _markets(out / "games" / g) for g in two_games}
    football = {g: (out / "games" / g / "packet.json").read_bytes() for g in two_games}
    for g in two_games:
        _age_market_files(out / "games" / g)
        assert _markets(out / "games" / g)[1] != full[g][1]

    _build(out, two_games, "--markets-only")

    exec_rows = {g["game_key"]: g for g in _load(ROOT / "data/live/cfb_execution_index.json")["games"]}
    for g in two_games:
        head, contracts = _markets(out / "games" / g)
        assert head == full[g][0], g
        assert {c["ticker"]: c for c in contracts} == {c["ticker"]: c for c in full[g][1]}, g
        assert len(contracts) == exec_rows[g]["market_count"]
        assert head["price_provenance"]["game_fingerprint"] == exec_rows[g]["game_fingerprint"]
        assert (out / "games" / g / "packet.json").read_bytes() == football[g], "a re-price must keep the football"


def test_markets_only_carries_frequencies_for_packets_built_before_the_reference_ids_were_recorded(tmp_path, two_games):
    """Legacy packets (no recorded reference class): existing contracts keep their ref_freq, nothing is invented."""
    g = two_games[0]
    out = _scratch_packets(tmp_path, [g])
    _build(out, [g])
    _, before = _markets(out / "games" / g)
    for p in (out / "games" / g).glob("markets*.json"):
        body = _load(p)
        body.pop("ref_freq_reference_game_ids", None)
        body.setdefault("price_provenance", {})["game_fingerprint"] = "stale-fingerprint"
        p.write_text(json.dumps(body))
    _build(out, [g], "--markets-only")
    head, after = _markets(out / "games" / g)
    assert "ref_freq_reference_game_ids" not in head
    prev = {c["ticker"]: c.get("ref_freq") for c in before}
    for c in after:
        assert c.get("ref_freq") == prev.get(c["ticker"]), c["ticker"]


def test_markets_only_never_adds_a_game_and_keeps_the_football_header(tmp_path, two_games):
    """Building football packets is the packet workflow's job (from a refreshed game log). The catalog's re-price
    touches only games already indexed and keeps the index's football-log and horizon fields."""
    out = _scratch_packets(tmp_path, two_games)
    before = _load(out / "index.json")
    indexed = {g["game_key"] for g in before["games"]}
    dirs_before = {p.name for p in (out / "games").iterdir()}
    subprocess.run(
        [sys.executable, str(BUILDER), "--no-fetch", "--markets-only", "--out-dir", str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    after = _load(out / "index.json")
    assert {p.name for p in (out / "games").iterdir()} == dirs_before
    exec_keys = {g["game_key"] for g in _load(ROOT / "data/live/cfb_execution_index.json")["games"]}
    assert {g["game_key"] for g in after["games"]} == indexed & exec_keys
    for k in ("horizon_hours", "football_log_fetched_at", "football_log_through", "football_games_in_log"):
        assert after[k] == before[k], k


# ---------------------------------------------------------------------------------------------------------------------
# (2) The git choreography, in throwaway repositories with stand-in builders
# ---------------------------------------------------------------------------------------------------------------------

# Stand-ins with the same contract as the real scripts: the "execution index" is data/live/index.txt (a contract
# count); each indexed game's derived market file data/research_packets/games/<g>/markets.txt must equal it, beside a
# football file the catalog never touches. The real resync/reapply scripts run unmodified.
STUB_BUILDER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "--markets-only" in sys.argv and "--no-fetch" in sys.argv
    for game in sorted((root / "data/research_packets/games").iterdir()):
        (game / "markets.txt").write_text((root / "data/live/index.txt").read_text())
    """
)
STUB_VERIFIER = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    index = (root / "data/live/index.txt").read_text()
    bad = [g.name for g in (root / "data/research_packets/games").iterdir() if (g / "markets.txt").read_text() != index]
    print("FAIL: markets disagree with the execution index: %s" % bad if bad else "verified")
    ok = not bad
    sys.exit(0 if ok else 1)
    """
)


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout


def _sh(cwd: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    return subprocess.run(["bash", script, *args], cwd=cwd, capture_output=True, text=True, env=env)


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture()
def origin_and_clones(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "scripts").mkdir()
    for name in ("resync_packet_markets.sh", "reapply_owned_path.sh"):
        shutil.copy2(ROOT / "scripts" / name, seed / "scripts" / name)
    _write(seed, "scripts/build_research_packets.py", STUB_BUILDER)
    _write(seed, "scripts/verify_research_packets.py", STUB_VERIFIER)
    _write(seed, "data/live/index.txt", "140\n")
    _write(seed, "data/research_packets/games/A/markets.txt", "140\n")
    _write(seed, "data/research_packets/games/A/football.txt", "football v1\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "seed")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    clones = {}
    for name in ("catalog", "packets"):
        clones[name] = tmp_path / name
        _git(tmp_path, "clone", "-q", str(origin), str(clones[name]))
    return origin, clones


def _push_loop(repo: Path, owned: str) -> None:
    """The workflows' loop: rebase, or re-apply our owned path on a conflict; then re-derive, verify, push."""
    pulled = subprocess.run(
        ["git", "pull", "-q", "--rebase", "--autostash", "origin", "main"], cwd=repo, capture_output=True, text=True
    )
    if pulled.returncode != 0:
        r = _sh(repo, "scripts/reapply_owned_path.sh", owned)
        assert r.returncode == 0, r.stdout + r.stderr
    r = _sh(repo, "scripts/resync_packet_markets.sh", "--amend")
    assert r.returncode == 0, r.stdout + r.stderr
    _git(repo, "push", "-q", "origin", "HEAD:main")


def _origin_tree(origin: Path, tmp: Path) -> Path:
    check = tmp / "check"
    if check.exists():
        shutil.rmtree(check)
    _git(tmp, "clone", "-q", str(origin), str(check))
    return check


def _assert_every_commit_consistent(tree: Path) -> None:
    for sha in _git(tree, "rev-list", "HEAD").split():
        idx = _git(tree, "show", f"{sha}:data/live/index.txt")
        games = _git(tree, "ls-tree", "--name-only", f"{sha}:data/research_packets/games").split()
        for g in games:
            mk = _git(tree, "show", f"{sha}:data/research_packets/games/{g}/markets.txt")
            assert idx == mk, f"commit {sha[:8]} holds a catalog its packets disagree with ({g})"


def _catalog_commit(repo: Path, count: str, msg: str) -> None:
    _write(repo, "data/live/index.txt", count)
    _git(repo, "add", "-A", "data/live")
    r = _sh(repo, "scripts/resync_packet_markets.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    _git(repo, "commit", "-qm", msg)


def test_packet_build_that_lands_after_a_catalog_commit_is_re_derived_before_push(origin_and_clones, tmp_path):
    """The reported failure: packets built at 20:34 from a 140-contract index, catalog commits 286 at 21:00. The packet
    run's commit adds a game the catalog commit did not touch, so the rebase is CLEAN -- and used to push a market file
    that disagrees with the index."""
    origin, c = origin_and_clones
    # the packet run builds a new game B from the index it checked out (140)
    _write(c["packets"], "data/research_packets/games/B/football.txt", "football B\n")
    _write(c["packets"], "data/research_packets/games/B/markets.txt", "140\n")
    _git(c["packets"], "add", "-A", "data/research_packets")
    _git(c["packets"], "commit", "-qm", "cfb research packets: refresh")
    # meanwhile the catalog lists more contracts and pushes, re-deriving game A in the same commit
    _catalog_commit(c["catalog"], "286\n", "kalshi cfb catalog: 286")
    _git(c["catalog"], "push", "-q", "origin", "HEAD:main")

    # Without the re-derivation this is what main would have held:
    naive = tmp_path / "naive"
    _git(tmp_path, "clone", "-q", str(c["packets"]), str(naive))
    _git(naive, "pull", "-q", "--rebase", str(origin), "main")
    assert (naive / "data/research_packets/games/B/markets.txt").read_text() == "140\n"

    _push_loop(c["packets"], "data/research_packets")

    tree = _origin_tree(origin, tmp_path)
    assert (tree / "data/live/index.txt").read_text() == "286\n"
    assert (tree / "data/research_packets/games/B/markets.txt").read_text() == "286\n"
    assert (tree / "data/research_packets/games/B/football.txt").read_text() == "football B\n"
    _assert_every_commit_consistent(tree)


def test_catalog_commit_that_conflicts_with_a_packet_build_keeps_both_sides_and_re_derives(
    origin_and_clones, tmp_path
):
    """Both runs rewrote the same market file. The catalog keeps upstream's packets (new football) and its own index,
    and re-derives the market file; nothing is lost and nothing disagrees."""
    origin, c = origin_and_clones
    _write(c["packets"], "data/research_packets/games/A/football.txt", "football v2\n")
    _git(c["packets"], "add", "-A", "data/research_packets")
    _git(c["packets"], "commit", "-qm", "cfb research packets: refresh")
    _catalog_commit(c["packets"], "141\n", "kalshi cfb catalog: 141")  # an earlier catalog run, already on main
    _git(c["packets"], "push", "-q", "origin", "HEAD:main")

    _catalog_commit(c["catalog"], "286\n", "kalshi cfb catalog: 286")
    _push_loop(c["catalog"], "data/live")

    tree = _origin_tree(origin, tmp_path)
    assert (tree / "data/live/index.txt").read_text() == "286\n"
    assert (tree / "data/research_packets/games/A/markets.txt").read_text() == "286\n"
    assert (tree / "data/research_packets/games/A/football.txt").read_text() == "football v2\n"
    assert _git(tree, "log", "-1", "--format=%s").strip() == "kalshi cfb catalog: 286"
    _assert_every_commit_consistent(tree)


def test_resync_refuses_to_stage_an_inconsistent_tree(origin_and_clones):
    """If re-deriving cannot make the tree consistent, the script fails and the caller does not push."""
    _, c = origin_and_clones
    repo = c["catalog"]
    _write(repo, "scripts/build_research_packets.py", "import sys\n")  # a broken re-price writes nothing
    _write(repo, "data/live/index.txt", "999\n")
    r = _sh(repo, "scripts/resync_packet_markets.sh")
    assert r.returncode != 0 and "FAIL" in r.stdout


def test_resync_amend_never_rewrites_an_upstream_commit(origin_and_clones, tmp_path):
    """After a rebase that left nothing of ours, a needed re-derivation becomes its own commit, never an amend of
    someone else's (which could not be pushed fast-forward anyway)."""
    origin, c = origin_and_clones
    repo = c["catalog"]
    # upstream drifts (a writer that predates this fix pushes an index change alone)
    other = c["packets"]
    _write(other, "data/live/index.txt", "150\n")
    _git(other, "commit", "-qam", "legacy catalog commit")
    _git(other, "push", "-q", "origin", "HEAD:main")
    _git(repo, "pull", "-q", "--rebase", "origin", "main")
    upstream = _git(repo, "rev-parse", "HEAD").strip()
    r = _sh(repo, "scripts/resync_packet_markets.sh", "--amend")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _git(repo, "rev-parse", "HEAD~1").strip() == upstream
    _git(repo, "push", "-q", "origin", "HEAD:main")
    tree = _origin_tree(origin, tmp_path)
    assert (tree / "data/research_packets/games/A/markets.txt").read_text() == "150\n"


# ---------------------------------------------------------------------------------------------------------------------
# (3) Wiring
# ---------------------------------------------------------------------------------------------------------------------

PACKET_MARKET_WRITERS = {
    "kalshi-market-catalog.yml": "data/live",
    "cfb-research-packets.yml": "data/research_packets",
}


@pytest.mark.parametrize("name", sorted(PACKET_MARKET_WRITERS))
def test_every_push_is_preceded_by_a_rebase_and_a_strict_re_derivation(name):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    loop = text[text.index("for attempt in 1 2 3 4"):]
    loop = loop[: loop.index("done")]
    pull = loop.index("git pull --rebase")
    reapply = loop.index(f"scripts/reapply_owned_path.sh {PACKET_MARKET_WRITERS[name]}")
    resync = loop.index("scripts/resync_packet_markets.sh --amend")
    push = loop.index('git push origin "HEAD:${GITHUB_REF_NAME}"')
    assert pull < reapply < resync < push
    assert len(re.findall(r"git push", text)) == 1, f"{name} has a push outside the re-deriving loop"


def test_the_catalog_re_derives_the_packet_markets_into_its_own_commit():
    text = (WORKFLOWS / "kalshi-market-catalog.yml").read_text(encoding="utf-8")
    step = text[text.index("- name: Commit the catalog only if the market surface changed"):]
    first_resync = step.index("scripts/resync_packet_markets.sh\n")
    assert first_resync < step.index('git commit -m "${MSG')
    # an unchanged catalog still heals packets that drifted earlier, rather than exiting first
    assert "exit 0" not in step[: first_resync]


def test_the_resync_script_stages_only_the_packets_and_verifies_strictly():
    text = RESYNC.read_text(encoding="utf-8")
    assert re.findall(r"git add[^\n]*", text) == ["git add -A data/research_packets"]
    assert "build_research_packets.py --markets-only --no-fetch" in text
    assert "verify_research_packets.py --strict" in text
    assert text.index("--strict") < text.index("git add")
    assert "set -euo pipefail" in text and "git push" not in text


def test_the_reapply_script_restores_only_the_named_path_and_never_pushes():
    text = REAPPLY.read_text(encoding="utf-8")
    assert "git push" not in text
    assert re.findall(r"git checkout[^\n]*", text) == ['git checkout "$ours" -- "$own"']
    assert 'git reset -q --hard FETCH_HEAD' in text
