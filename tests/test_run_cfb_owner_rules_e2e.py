"""Owner rules through the REAL RUN CFB entry point (`python -m cfb_edge_finder.execution ...`), Auburn at home.

prepare-live -> evaluate -> candidates on a controlled Arkansas-at-Auburn catalog. The handicap makes Arkansas the
side the model likes, so the surviving candidates are exactly the ones the Auburn rule must block. The rule is
restriction-only: blocked candidates stay in the artifact, nothing is added, removed or reordered.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from cfb_edge_finder.decision.owner_rules import OWNER_EXCLUSION_AUBURN, auburn_exclusion
from cfb_edge_finder.execution.cli import main
from tests.execution_fakes import NOW, catalog_dir, standard_markets

GAME = "26SEP19ARKAUB"


def auburn_markets():
    """standard_markets with LSU->Auburn (AUB, home) and Ole Miss->Arkansas (ARK, away)."""
    text = json.dumps(standard_markets(GAME))
    # names first (titles), then ticker tokens: LSU -> AUB (Auburn, home), MISS -> ARK (Arkansas, away)
    for old, new in (
        ("Ole Miss", "Arkansas"),
        ("26SEP19LSUMISS", GAME),
        ("-LSU", "-AUB"),
        ("-MISS", "-ARK"),
        ("LSU", "Auburn"),
    ):
        text = text.replace(old, new)
    return json.loads(text)


def walk(tmp_path: Path, *, home_mean: float, away_mean: float, extra=(), handicapped_at: str | None = None):
    catalog = catalog_dir(tmp_path, auburn_markets(), title="Arkansas at Auburn", game_key=GAME)
    out = tmp_path / "exec"
    assert (
        main(
            [
                "prepare-live",
                "--catalog-dir",
                str(catalog),
                "--out-dir",
                str(out),
                "--as-of",
                NOW.isoformat(),
                "--date",
                "all",
            ]
        )
        == 0
    )
    manifest = json.loads((out / "shard_manifest.json").read_text())
    shard = manifest["shards"][0]["shard"]
    pk = json.loads((out / "shards" / f"{shard}.json").read_text())["games"][0]
    hc = tmp_path / "hc.json"
    hc.write_text(
        json.dumps(
            [
                {
                    "schema_version": "cfb_handicap_payload/2.0.0",
                    "game_key": pk["game_key"],
                    "packet_hash": pk["packet_hash"],
                    "teams": {"home": "Auburn", "away": "Arkansas"},
                    "confidence": "test",
                    "thesis": "t",
                    "opposing_case": "o",
                    "period_distributions": {
                        "full_game": {
                            "home_mean": home_mean,
                            "away_mean": away_mean,
                            "home_sd": 10.5,
                            "away_sd": 10.0,
                            "correlation": 0.1,
                            "uncertainty": {"margin_points": 3.0, "total_points": 5.0},
                        }
                    },
                    "explicit_probabilities": {},
                    **({"handicapped_at": handicapped_at} if handicapped_at else {}),
                }
            ]
        )
    )
    assert (
        main(["evaluate", "--shard", shard, "--out-dir", str(out), "--handicaps", str(hc), "--min-edge", "0.02"]) == 0
    )
    assert (
        main(
            [
                "candidates",
                "--shard",
                shard,
                "--out-dir",
                str(out),
                "--min-edge",
                "0.02",
                "--no-decision-record",
                "--as-of",
                (NOW + timedelta(minutes=5)).isoformat(),
                *extra,
            ]
        )
        == 0
    )
    return json.loads((out / "candidates" / f"{shard}.candidates.json").read_text())


def test_fixture_tickers_really_name_auburn():
    tickers = [m["market_ticker"] for m in auburn_markets()]
    assert any(t.endswith("-AUB") for t in tickers) and any(t.endswith("-ARK") for t in tickers)
    assert all(GAME in t for t in tickers) and not any("LSU" in t or "MISS" in t for t in tickers)


def test_every_against_auburn_candidate_is_blocked_by_name_and_none_is_dropped(tmp_path):
    art = walk(tmp_path, home_mean=21.0, away_mean=31.0)  # Arkansas heavily favoured at Auburn
    cands = art["candidates"]
    assert cands, "the handicap is far from the book, so there are candidates"
    against = [c for c in cands if auburn_exclusion(c["market"], c["side"], game_title="Arkansas at Auburn").blocked]
    assert against, "an Arkansas-favouring handicap must surface at least one position against Auburn"
    for c in cands:
        verdict = auburn_exclusion(c["market"], c["side"], game_title="Arkansas at Auburn")
        assert c["owner_rules"]["auburn"]["blocked"] == verdict.blocked
        if verdict.blocked:
            assert c["pit_review"]["actionable"] is False
            assert OWNER_EXCLUSION_AUBURN in c["pit_review"]["blocking_guards"]
    assert art["pit_review"]["blocked_by"].get(OWNER_EXCLUSION_AUBURN, 0) == len(against)
    assert art["pit_review"]["candidates"] == len(cands), "restriction-only: nothing removed"


def test_auburn_favoured_handicap_does_not_trip_the_owner_rule(tmp_path):
    art = walk(tmp_path, home_mean=33.0, away_mean=17.0)  # Auburn heavily favoured
    for c in art["candidates"]:
        if c["owner_rules"]["auburn"]["blocked"]:
            raise AssertionError(
                f"a pro-Auburn handicap produced an against-Auburn candidate: {c['market']} {c['side']}"
            )
    assert OWNER_EXCLUSION_AUBURN not in art["pit_review"]["blocked_by"]


def test_per_bet_ceiling_is_applied_to_a_proposed_stake_through_the_cli(tmp_path):
    first = walk(tmp_path / "a", home_mean=21.0, away_mean=31.0)
    target = first["candidates"][0]
    stakes = tmp_path / "stakes.json"
    stakes.write_text(json.dumps({target["market"]: 900.0}))
    art = walk(tmp_path / "b", home_mean=21.0, away_mean=31.0, extra=("--proposed-stakes", str(stakes)))
    sizing = next(c for c in art["candidates"] if c["market"] == target["market"])["owner_rules"]["sizing"]
    assert sizing["clamped"] is True and sizing["effective_dollars"] == 375.0
    assert sizing["reason"] == "PER_BET_CAP_5U_APPLIED"
