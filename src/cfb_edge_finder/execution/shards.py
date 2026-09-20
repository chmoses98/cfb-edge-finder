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
`<shard>.analysis.json`  THE DELIVERABLE: every eligible contract with its
                         prices, fees and semantics, behind each game's
                         factual context. This is the file that goes to
                         ChatGPT, and nothing is written back.
`<shard>.json`           the same contracts with the full per-contract
                         record retained, as the audit trail and the input
                         to the optional repo-side evaluator.

Both carry every eligible contract for their games. The analysis artifact
is a projection of the shard, never a selection from it: it counts its own
rows and refuses to build if they do not equal the eligible contracts, and
the tests compare ticker sets on top of that.

*** THE SIZE BUDGET IS THE ANALYSIS ARTIFACT'S ***
Packing is measured on the analysis bytes, because that is the file a
reader actually has to ingest. The audit file can be five times larger
without costing anyone anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cfb_edge_finder.execution.analysis import analysis_document, game_block
from cfb_edge_finder.execution.slate import canonical_hash
from cfb_edge_finder.execution.windows import DEFAULT_TIMEZONE, WINDOW_ORDER

DEFAULT_MAX_ANALYSIS_BYTES = 250_000
"""Byte budget for one analysis artifact.

*** SIZED FOR REASONING DEPTH, NOT FOR THE LARGEST FILE THAT FITS ***
A whole kickoff window is ~40 games and ~5,000 contracts in ~600 KB. That
is complete, and it is too much to handicap 40 games INDEPENDENTLY and
well in one pass -- the constraint that bites is attention, not the
context window. At ~250 KB a part is roughly 10-20 games, which a reader
can actually work through game by game.

It is a budget on the ENCODING only. It can never remove a contract: a
game is the indivisible unit, and a single game bigger than the budget
gets a part of its own rather than being split or trimmed. More parts is
the only thing a smaller budget ever buys."""

DEFAULT_MAX_SHARD_CONTRACTS = 5_000


def _encode(payload: dict[str, Any], *, compact: bool = True, sort_keys: bool = True) -> str:
    """Compact for the bulk artifacts.

    `indent=1` puts every one of ~14,000 contracts' ~30 keys on its own
    line, which roughly doubles the file for the benefit of a reader who
    is a JSON parser. The manifest and the reports stay indented because
    a human does read those.

    `sort_keys=False` is used for the analysis artifact alone: its key
    order is deliberate (how_to_use, then reconciliation, then per game
    `game_context` BEFORE `markets`), and alphabetising it would put the
    prices in front of the facts on every game."""
    if compact:
        return json.dumps(
            payload, sort_keys=sort_keys, separators=(",", ":"), default=str
        ) + "\n"
    return json.dumps(payload, indent=1, sort_keys=sort_keys, default=str) + "\n"


def _analysis_bytes(packet: dict[str, Any], tz_name: str = DEFAULT_TIMEZONE) -> int:
    """How much of the analysis artifact this one game will occupy."""
    return len(_encode(game_block(packet, tz_name), sort_keys=False).encode("utf-8"))


@dataclass(frozen=True)
class Shard:
    name: str
    window: str
    packets: list[dict[str, Any]]
    window_part: int = 1
    """1-based position of this part within its kickoff window."""
    window_parts: int = 1
    """How many parts that window was split into."""

    @property
    def analysis_filename(self) -> str:
        """`early.analysis.01.json`.

        ALWAYS numbered, even for a window that produced a single part.
        A uniform name means the upload order is readable straight off the
        filesystem and there is no special case to get wrong at 7am."""
        return f"{self.window}.analysis.{self.window_part:02d}.json"

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
    def exclusions_by_status(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for packet in self.packets:
            for status, count in (packet.get("exclusions") or {}).items():
                totals[status] = totals.get(status, 0) + int(count)
        return totals

    @property
    def kickoffs(self) -> list[str]:
        return sorted(str(p["kickoff"]) for p in self.packets if p.get("kickoff"))




def split_window(
    window: str,
    packets: list[dict[str, Any]],
    max_bytes: int,
    max_contracts: int,
) -> list[Shard]:
    """Greedy, kickoff-ordered packing. Order is preserved so a part is
    always a contiguous run of kickoffs -- `early` part 02 is never a
    random scatter of the early window.

    A packet is added to the current group BEFORE the next overflow check,
    so a single game larger than the whole budget lands in a part of its
    own intact. The budget never wins against the game-is-indivisible
    rule."""
    if not packets:
        return []
    ordered = sorted(packets, key=lambda p: (str(p.get("kickoff") or "9999"), str(p["game_key"])))
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_bytes = 0
    current_contracts = 0
    for packet in ordered:
        size = _analysis_bytes(packet)
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

    return [
        Shard(
            name=window if len(groups) == 1 else f"{window}_{i + 1}",
            window=window,
            packets=group,
            window_part=i + 1,
            window_parts=len(groups),
        )
        for i, group in enumerate(groups)
    ]


def build_shards(
    slate: dict[str, Any],
    *,
    max_bytes: int = DEFAULT_MAX_ANALYSIS_BYTES,
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


def shard_analysis_document(
    slate: dict[str, Any], shard: Shard, tz_name: str = DEFAULT_TIMEZONE
) -> dict[str, Any]:
    """The file that goes to ChatGPT. Raises rather than publish an
    artifact that does not contain every eligible contract."""
    return analysis_document(
        slate,
        shard.name,
        shard.window,
        shard.packets,
        tz_name=tz_name,
        discovered=shard.discovered,
        excluded=shard.excluded,
        exclusions_by_status=shard.exclusions_by_status,
        window_part=shard.window_part,
        window_parts=shard.window_parts,
    )


def write_shards(
    slate: dict[str, Any],
    shards: list[Shard],
    out_dir: Path,
    tz_name: str = DEFAULT_TIMEZONE,
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

        analysis = shard_analysis_document(slate, shard, tz_name)
        analysis_encoded = _encode(analysis, sort_keys=False)
        analysis_path = shard_dir / shard.analysis_filename
        analysis_path.write_text(analysis_encoded, encoding="utf-8")
        written.add(analysis_path.name)

        kickoffs = shard.kickoffs
        entries.append(
            {
                "shard": shard.name,
                "kickoff_window": shard.window,
                "file": f"shards/{shard.name}.json",
                "analysis_file": f"shards/{shard.analysis_filename}",
                "window_part": shard.window_part,
                "window_parts": shard.window_parts,
                "games": shard.game_keys,
                "game_count": len(shard.packets),
                "contracts_discovered": shard.discovered,
                "contracts_eligible": shard.eligible,
                "contracts_excluded": shard.excluded,
                "bytes": len(encoded.encode("utf-8")),
                "analysis_bytes": len(analysis_encoded.encode("utf-8")),
                "contracts_in_analysis_artifact": analysis["reconciliation"][
                    "contracts_in_analysis_artifact"
                ],
                "unaccounted_contracts": analysis["reconciliation"]["unaccounted_contracts"],
                "freshest_quote_age_seconds": analysis["freshness"]["freshest_quote_age_seconds"],
                "oldest_quote_age_seconds": analysis["freshness"]["oldest_quote_age_seconds"],
                "earliest_kickoff": kickoffs[0] if kickoffs else None,
                "latest_kickoff": kickoffs[-1] if kickoffs else None,
                "artifact_hash": canonical_hash(document),
                "analysis_artifact_hash": canonical_hash(analysis),
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
        "contracts_in_analysis_artifacts": sum(
            e["contracts_in_analysis_artifact"] for e in entries
        ),
        "unaccounted_contracts": sum(e["unaccounted_contracts"] for e in entries),
    }
    manifest = {
        "schema_version": slate["schema_version"],
        "artifact": "shard_manifest",
        "upload_order": [e["analysis_file"] for e in entries],
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
            "no game appears in more than one shard; shard totals equal the published slate "
            "totals; every eligible contract appears in an analysis artifact"
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
        and totals["contracts_discovered"]
        == manifest["slate_totals"]["contracts_discovered_published_games"]
        # The deliverable's own invariant: every eligible contract is in a
        # file a reader will actually open.
        and totals["contracts_in_analysis_artifacts"] == totals["contracts_eligible"]
        and totals["unaccounted_contracts"] == 0
    )

    (out_dir / "shard_manifest.json").write_text(
        _encode(manifest, compact=False), encoding="utf-8"
    )
    return manifest
