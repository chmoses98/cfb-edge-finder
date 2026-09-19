"""Kickoff-window sharding.

*** THE ONE RULE THAT CANNOT BEND ***
A GAME IS NEVER SPLIT. Every contract belonging to one physical game lives
in exactly one shard, because the whole workflow rests on "handicap this
game, then price every one of its contracts" -- and a handicap cannot be
applied to markets that arrived in a different conversation.

Size limits are therefore satisfied by adding numbered subshards
(`early_1`, `early_2`), never by moving part of a game. A single game that
is larger than the byte budget on its own gets a shard of its own and the
manifest says so, rather than the budget winning.

*** TWO FILES PER SHARD, SAME POPULATION ***
`<shard>.json`        full economics -- what the evaluator reads.
`<shard>.brief.json`  the same contracts, ~6x smaller -- what a handicapper
                      reads.
Both carry every eligible contract for their games. The brief is a
projection of the shard, never a selection from it, and the tests compare
ticker sets to keep it that way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cfb_edge_finder.execution.slate import canonical_hash, compact_packet
from cfb_edge_finder.execution.windows import WINDOW_ORDER

DEFAULT_MAX_SHARD_BYTES = 6_000_000
DEFAULT_MAX_SHARD_CONTRACTS = 5_000


def _encode(payload: dict[str, Any], *, compact: bool = True) -> str:
    """Compact for the bulk artifacts.

    `indent=1` puts every one of ~14,000 contracts' ~30 keys on its own
    line, which roughly doubles the file for the benefit of a reader who
    is a JSON parser. The manifest and the reports stay indented because
    a human does read those."""
    if compact:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n"
    return json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n"


@dataclass(frozen=True)
class Shard:
    name: str
    window: str
    packets: list[dict[str, Any]]

    @property
    def game_keys(self) -> list[str]:
        return [str(p["game_key"]) for p in self.packets]

    @property
    def eligible(self) -> int:
        return sum(int(p["counts"]["eligible"]) for p in self.packets)

    @property
    def discovered(self) -> int:
        return sum(int(p["counts"]["discovered"]) for p in self.packets)

    @property
    def excluded(self) -> int:
        return sum(int(p["counts"]["excluded"]) for p in self.packets)

    @property
    def kickoffs(self) -> list[str]:
        return sorted(str(p["kickoff"]) for p in self.packets if p.get("kickoff"))


def _packet_bytes(packet: dict[str, Any]) -> int:
    return len(_encode(packet).encode("utf-8"))


def split_window(
    window: str,
    packets: list[dict[str, Any]],
    max_bytes: int,
    max_contracts: int,
) -> list[Shard]:
    """Greedy, kickoff-ordered packing. Order is preserved so a subshard
    is always a contiguous run of kickoffs -- `early_1` is never a random
    scatter of the early window."""
    if not packets:
        return []
    ordered = sorted(packets, key=lambda p: (str(p.get("kickoff") or "9999"), str(p["game_key"])))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 0
    current_contracts = 0
    for packet in ordered:
        size = _packet_bytes(packet)
        contracts = int(packet["counts"]["eligible"])
        would_overflow = current and (
            current_bytes + size > max_bytes or current_contracts + contracts > max_contracts
        )
        if would_overflow:
            groups.append(current)
            current, current_bytes, current_contracts = [], 0, 0
        current.append(packet)
        current_bytes += size
        current_contracts += contracts
    if current:
        groups.append(current)

    if len(groups) == 1:
        return [Shard(name=window, window=window, packets=groups[0])]
    return [
        Shard(name=f"{window}_{i + 1}", window=window, packets=group)
        for i, group in enumerate(groups)
    ]


def build_shards(
    slate: dict[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_SHARD_BYTES,
    max_contracts: int = DEFAULT_MAX_SHARD_CONTRACTS,
) -> list[Shard]:
    by_window: dict[str, list[dict[str, Any]]] = {}
    for packet in slate.get("games") or []:
        by_window.setdefault(str(packet.get("kickoff_window") or "unscheduled"), []).append(packet)

    shards: list[Shard] = []
    for window in WINDOW_ORDER:
        shards.extend(split_window(window, by_window.get(window, []), max_bytes, max_contracts))
    for window in sorted(set(by_window) - set(WINDOW_ORDER)):
        shards.extend(split_window(window, by_window[window], max_bytes, max_contracts))
    return shards


def shard_document(slate: dict[str, Any], shard: Shard) -> dict[str, Any]:
    return {
        "schema_version": slate["schema_version"],
        "shard": shard.name,
        "kickoff_window": shard.window,
        "slate_date": slate.get("slate_date"),
        "as_of": slate.get("as_of"),
        "source": slate.get("source"),
        "fee_disclosure": slate.get("fee_disclosure"),
        "config": slate.get("config"),
        "counts": {
            "games": len(shard.packets),
            "contracts_discovered": shard.discovered,
            "contracts_eligible": shard.eligible,
            "contracts_excluded": shard.excluded,
        },
        "games": shard.packets,
    }


def brief_document(slate: dict[str, Any], shard: Shard) -> dict[str, Any]:
    return {
        "schema_version": slate["schema_version"],
        "artifact": "handicap_brief",
        "shard": shard.name,
        "kickoff_window": shard.window,
        "slate_date": slate.get("slate_date"),
        "as_of": slate.get("as_of"),
        "instructions": HANDICAP_BRIEF_INSTRUCTIONS,
        "counts": {
            "games": len(shard.packets),
            "contracts_eligible": shard.eligible,
        },
        "games": [compact_packet(p) for p in shard.packets],
    }


HANDICAP_BRIEF_INSTRUCTIONS = [
    "Handicap each game in `games` independently, from the factual context and your own knowledge.",
    "Do NOT reason from the quoted prices toward a fair value; they are listed so you can see the "
    "rung inventory, not as a reference answer.",
    "For each game return one handicap payload (schema: cfb_handicap_payload/1.0.0). Supply a score "
    "distribution for EVERY period that appears in that game's `families[*].period`.",
    "`period_distributions` keys are periods; each value is "
    "{home_mean, away_mean, home_sd, away_sd, correlation}.",
    "`teams.home` and `teams.away` MUST echo the packet's names exactly -- the evaluator rejects the "
    "payload otherwise, which is what stops a home/away flip from silently inverting every spread.",
    "Contracts whose `requires` is `explicit_probability` (ties, first-TD, stat props, 1H/FT doubles, "
    "any unfamiliar family) price ONLY from `explicit_probabilities`, keyed by the contract's `t`.",
    "If you cannot defensibly supply a number, leave it out. A missing probability becomes an "
    "explicit UNPRICEABLE in the ledger, which is a correct outcome. A guessed one is not.",
    "Every eligible contract for every game in this shard is listed here. Nothing was pre-filtered "
    "for attractiveness, and no shortlist may be produced before all of them are evaluated.",
]


def write_shards(
    slate: dict[str, Any],
    shards: list[Shard],
    out_dir: Path,
) -> dict[str, Any]:
    shard_dir = out_dir / "shards"
    shard_dir.mkdir(parents=True, exist_ok=True)

    existing = {p.name for p in shard_dir.glob("*.json")}
    written: set[str] = set()
    entries: list[dict[str, Any]] = []

    for shard in shards:
        document = shard_document(slate, shard)
        encoded = _encode(document)
        path = shard_dir / f"{shard.name}.json"
        path.write_text(encoded, encoding="utf-8")
        written.add(path.name)

        brief = brief_document(slate, shard)
        brief_encoded = _encode(brief)
        brief_path = shard_dir / f"{shard.name}.brief.json"
        brief_path.write_text(brief_encoded, encoding="utf-8")
        written.add(brief_path.name)

        kickoffs = shard.kickoffs
        entries.append(
            {
                "shard": shard.name,
                "kickoff_window": shard.window,
                "file": f"shards/{shard.name}.json",
                "brief_file": f"shards/{shard.name}.brief.json",
                "games": shard.game_keys,
                "game_count": len(shard.packets),
                "contracts_discovered": shard.discovered,
                "contracts_eligible": shard.eligible,
                "contracts_excluded": shard.excluded,
                "bytes": len(encoded.encode("utf-8")),
                "brief_bytes": len(brief_encoded.encode("utf-8")),
                "earliest_kickoff": kickoffs[0] if kickoffs else None,
                "latest_kickoff": kickoffs[-1] if kickoffs else None,
                "artifact_hash": canonical_hash(document),
                "brief_artifact_hash": canonical_hash(brief),
            }
        )

    for stale in sorted(existing - written):
        (shard_dir / stale).unlink()

    reconciliation = slate.get("reconciliation") or {}
    totals = {
        "shards": len(entries),
        "games": sum(e["game_count"] for e in entries),
        "contracts_discovered": sum(e["contracts_discovered"] for e in entries),
        "contracts_eligible": sum(e["contracts_eligible"] for e in entries),
        "contracts_excluded": sum(e["contracts_excluded"] for e in entries),
    }
    manifest = {
        "schema_version": slate["schema_version"],
        "artifact": "shard_manifest",
        "generated_at": slate.get("generated_at"),
        "slate_date": slate.get("slate_date"),
        "as_of": slate.get("as_of"),
        "source": slate.get("source"),
        "shards": entries,
        "totals": totals,
        "slate_totals": {
            "games": reconciliation.get("games_published"),
            "contracts_discovered_published_games": sum(
                int(p["counts"]["discovered"]) for p in slate.get("games") or []
            ),
            "contracts_eligible": reconciliation.get("contracts_eligible"),
        },
        "reconciles": (
            totals["games"] == reconciliation.get("games_published")
            and totals["contracts_eligible"] == reconciliation.get("contracts_eligible")
        ),
        "invariant": (
            "no game appears in more than one shard; shard totals equal the published slate totals"
        ),
    }

    seen: set[str] = set()
    duplicated: set[str] = set()
    for entry in entries:
        for key in entry["games"]:
            if key in seen:
                duplicated.add(key)
            seen.add(key)
    manifest["games_in_multiple_shards"] = sorted(duplicated)
    manifest["reconciles"] = bool(
        manifest["reconciles"]
        and not duplicated
        and totals["contracts_discovered"] == manifest["slate_totals"]["contracts_discovered_published_games"]
    )

    (out_dir / "shard_manifest.json").write_text(
        _encode(manifest, compact=False), encoding="utf-8"
    )
    return manifest
