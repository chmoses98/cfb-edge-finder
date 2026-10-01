"""The decision record contract, and how one is built from a `candidates` run.

*** WHAT A RECORD IS ***
One document per `candidates` invocation: the decision-time evidence of one
batch (or shard) of one slate, exactly as the run produced it. It is built from
the artifacts the run just wrote -- the candidate artifact, its reduction ledger
and the handicap payloads the evaluation used -- and from nothing regenerated
later. A record built afterwards from a fresh evaluation would be a
reconstruction wearing the timestamp of a decision, and this module refuses to
build one without the run's own artifacts.

*** WHAT IS NEVER INVENTED ***
A field the handicapper or the artifact did not emit is `null`. There is no
recommended stake in a candidate artifact today (`stake_placeholder` is null by
design), no bankroll, no unit; the record says so with nulls, not with a
plausible number. `confidence` is the handicap's own word, capped by the data
quality the packet stated; `tier` is the artifact's robustness label.

*** IDENTITY ***
`record_id` is a content hash over the fields that define the decision -- the
slate, the batch, the packet hashes, the handicap fingerprints, the selected
tickers and their observed prices, and the artifact's `generated_at`. Two runs
that decided the same thing at the same moment are one record; a re-run after a
price move or a re-handicap is a new record, which is the truth.

`candidates[].recommendation_id` is the artifact's own deterministic id, kept
verbatim so a later join from the postmortem is to the same identifier the
artifact carried when it was read.

*** THE CARD REVIEW, AS IT STOOD AT DECISION TIME ***
A `cfb_candidate_artifact/1.1.0` artifact carries a `card_review` block: the
final-review contract, and which candidate was the CORE expression of its view,
which alternatives existed beside it, and whether each was a nested tail
extension or had an independent cash path. The record keeps that block
verbatim and copies the per-candidate and per-alternative fields onto the rows
the postmortem joins on, so a later question -- "the executed rung was an
alternative; would it have needed incremental justification?" -- is answered
from what the run said, not from a reconstruction. Every exposure figure in the
block is null: the record never invents a stake.

These fields are OPTIONAL within `cfb_decision_record/1.0.0`. They are purely
additive, no 1.0.0 field changes meaning, every reader uses `.get`, and a record
built from a 1.0.0 artifact simply has `card_review: null` -- so the schema is
not bumped, and records already in the store remain valid as written.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "cfb_decision_record/1.0.0"
SUPPORTED_SCHEMA_VERSIONS = ("cfb_decision_record/1.0.0",)

PRODUCER = "cfb_edge_finder.execution candidates"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _game_key_of_ticker(ticker: str | None) -> str | None:
    parts = str(ticker or "").split("-")
    return parts[1] if len(parts) >= 3 else None


def _quote_timestamp(packet: dict[str, Any] | None, ticker: str) -> str | None:
    """The venue's own quote time for this contract, from the packet.

    The packet carries every eligible contract with the `updated_time` Kalshi
    published for it; that is the decision-time price's own clock. Absent when
    the packet does not carry the contract, never substituted."""
    if not isinstance(packet, dict):
        return None
    for contract in packet.get("contracts") or []:
        if isinstance(contract, dict) and contract.get("ticker") == ticker:
            return contract.get("quote_timestamp") or contract.get("updated_time")
    return None


def build_decision_record(
    *,
    artifact: dict[str, Any],
    reduction_ledger: dict[str, Any] | None,
    handicaps: dict[str, dict[str, Any]],
    packets: dict[str, dict[str, Any]],
    slate: dict[str, Any],
    batch_entry: dict[str, Any] | None,
    source_files: dict[str, str],
    created_at: str | None = None,
) -> dict[str, Any]:
    """The record of one `candidates` run, from that run's own artifacts.

    `artifact` is the candidate artifact the run wrote; `reduction_ledger` its
    reduction ledger (the removals, each with the survivor it lost to);
    `handicaps` the handicap payloads (as dicts) the evaluation used, by game
    key; `packets` the shard packets by game key; `slate` the execution slate
    document (for the slate date, the catalog capture time and the timezone);
    `batch_entry` the batch manifest entry (for the kickoff window).
    """
    if not isinstance(artifact, dict) or not artifact.get("candidates") and "candidates" not in artifact:
        raise ValueError("a decision record needs the run's own candidate artifact")

    created = created_at or datetime.now(UTC).isoformat()
    games_block = artifact.get("games") or {}
    captured_at = ((slate or {}).get("source") or {}).get("captured_at")

    games: dict[str, dict[str, Any]] = {}
    for game_key in sorted(set(games_block) | set(handicaps)):
        block = games_block.get(game_key) or {}
        handicap = handicaps.get(game_key) or {}
        packet = packets.get(game_key) or {}
        metadata = packet.get("game_metadata") or {}
        games[game_key] = {
            "game_key": game_key,
            "game": block.get("game") or packet.get("title") or game_key,
            "kickoff": block.get("kickoff") or packet.get("kickoff"),
            "teams": dict(handicap.get("teams") or metadata.get("teams") or {}),
            "packet_hash": handicap.get("packet_hash") or packet.get("packet_hash"),
            "context_hash": handicap.get("context_hash"),
            # The handicapper's words, verbatim, or the block's statement that
            # none were supplied. Never paraphrased here.
            "thesis": handicap.get("thesis") or block.get("thesis"),
            "opposing_case": handicap.get("opposing_case") or block.get("strongest_opposing_case"),
            "assumptions": handicap.get("assumptions") or None,
            "confidence": handicap.get("confidence") or block.get("handicap_confidence"),
            "effective_confidence": (
                handicap.get("effective_confidence") or block.get("handicap_confidence_effective")
            ),
            "confidence_capped_by_data_quality": block.get("handicap_confidence_capped_by_data"),
            "data_quality_ceiling": ((block.get("factual_data_quality") or {}).get("confidence_ceiling")),
            "handicap_schema_version": (
                handicap.get("schema_version_supplied") or block.get("handicap_schema_version")
            ),
            "handicapped_at": handicap.get("handicapped_at"),
            "uncertainty_stated": block.get("uncertainty_stated"),
            "market_disagreement": block.get("market_disagreement"),
            # The whole payload, so the distributions that priced every rung are
            # recoverable, not just the prose.
            "handicap_payload": dict(handicap) if handicap else None,
        }

    candidates: list[dict[str, Any]] = []
    for entry in artifact.get("candidates") or []:
        ticker = str(entry.get("market") or "")
        game_key = str(entry.get("game_key") or _game_key_of_ticker(ticker) or "")
        game = games.get(game_key) or {}
        candidates.append(
            {
                "recommendation_id": entry.get("recommendation_id"),
                "game_key": game_key,
                "game": entry.get("game") or game.get("game"),
                "kickoff": entry.get("kickoff") or game.get("kickoff"),
                "market_ticker": ticker,
                "market_family": entry.get("market_family"),
                "period": entry.get("period"),
                "market_description": entry.get("side_means"),
                "side": str(entry.get("side") or "").upper(),
                "observed_price": _number(entry.get("kalshi_executable_price")),
                "observed_at": _quote_timestamp(packets.get(game_key), ticker) or captured_at,
                "fee": _number(entry.get("fee")),
                "implied_probability": _number(entry.get("implied_probability")),
                "fair_probability": _number(entry.get("fair_probability")),
                "fair_probability_range": entry.get("fair_probability_range"),
                "edge": _number(entry.get("fee_adjusted_edge")),
                "edge_range": entry.get("fee_adjusted_edge_range"),
                "bet_up_to": _number(entry.get("bet_up_to_price")),
                "confidence": game.get("effective_confidence"),
                "tier": entry.get("robustness"),
                "tier_reason": entry.get("robustness_reason"),
                # Nothing in the live workflow sizes a bet. Null is the truth.
                "recommended_stake": _number(entry.get("stake_placeholder")),
                "correlation_group": entry.get("correlation_group"),
                "market_disagreement_level": entry.get("market_disagreement_level"),
                "related_alternatives": [
                    {
                        "market_ticker": alt.get("ticker") or alt.get("market"),
                        "side": str(alt.get("side") or alt.get("best_side") or "").upper() or None,
                        "edge": _number(
                            alt.get("fee_adjusted_edge") if "fee_adjusted_edge" in alt else alt.get("net_edge")
                        ),
                        "observed_price": _number(alt.get("kalshi_executable_price") or alt.get("executable_entry")),
                        "reason": alt.get("reason"),
                        **_incremental_fields(alt),
                    }
                    for alt in (entry.get("related_alternatives") or [])
                    if isinstance(alt, dict)
                ],
                "related_alternatives_total": entry.get("related_alternatives_total"),
                # Its place on a card, as the artifact stated it (null for a
                # 1.0.0 artifact, which did not state it).
                "card_role": entry.get("card_role"),
                "thesis_group": entry.get("thesis_group"),
                "thesis_peers": entry.get("thesis_peers"),
                "wins_when": entry.get("wins_when"),
                "selected": True,
            }
        )

    evaluated_not_selected: list[dict[str, Any]] = []
    # The reduction ledger's `entries` are the removals; `removed` is their count.
    for removal in (reduction_ledger or {}).get("entries") or []:
        if not isinstance(removal, dict):
            continue
        ticker = str(removal.get("ticker") or removal.get("market") or "")
        evaluated_not_selected.append(
            {
                "market_ticker": ticker,
                "game_key": removal.get("game_key") or _game_key_of_ticker(ticker),
                "side": str(removal.get("side") or removal.get("best_side") or "").upper() or None,
                "edge": _number(removal.get("net_edge") if "net_edge" in removal else removal.get("fee_adjusted_edge")),
                "observed_price": _number(removal.get("executable_entry") or removal.get("kalshi_executable_price")),
                "removal_reason": removal.get("reason"),
                "lost_to": removal.get("lost_to"),
                **_incremental_fields(removal),
                "selected": False,
            }
        )

    identity = {
        "slate_date": (slate or {}).get("slate_date"),
        "batch": artifact.get("batch"),
        "shard": artifact.get("shard"),
        "generated_at": artifact.get("generated_at"),
        "packet_hashes": {k: v.get("packet_hash") for k, v in sorted(games.items())},
        "selected": [
            (c["market_ticker"], c["side"], c["observed_price"], c["bet_up_to"]) for c in candidates
        ],
    }

    record = {
        "schema_version": SCHEMA_VERSION,
        "record_id": _canonical_hash(identity),
        "created_at": created,
        "producer": PRODUCER,
        "slate_date": (slate or {}).get("slate_date"),
        "timezone": (slate or {}).get("timezone"),
        "batch": artifact.get("batch"),
        "shard": artifact.get("shard"),
        "kickoff_window": (batch_entry or {}).get("kickoff_window"),
        "artifact_generated_at": artifact.get("generated_at"),
        "catalog_captured_at": captured_at,
        "min_net_edge": artifact.get("min_net_edge"),
        "reconciliation": dict(artifact.get("reconciliation") or {}),
        "reduction": {
            key: (artifact.get("reduction") or {}).get(key)
            for key in ("candidate_rows_before_reduction", "surviving_candidates", "removed",
                        "removed_by_reason")
        },
        # Never emitted by the live workflow today. Kept as explicit nulls so a
        # future handicapper that states them has somewhere to put them, and a
        # reader of an old record sees that they were not stated.
        "bankroll_context": None,
        # The final-review contract the operator was handed, verbatim, or null
        # when the artifact predates it. Its exposure figures are all null.
        "candidate_schema_version": artifact.get("schema_version"),
        "card_review": artifact.get("card_review"),
        "games": games,
        "candidates": candidates,
        "evaluated_not_selected": evaluated_not_selected,
        "source": dict(source_files),
    }
    problems = validate_decision_record(record)
    if problems:
        raise ValueError("refusing to build an invalid decision record: " + "; ".join(problems))
    return record


def _incremental_fields(entry: dict[str, Any]) -> dict[str, Any]:
    """The card-review fields of an alternative or a removal, flattened.

    Read from either shape: an inline alternative carries the full `cash_path`;
    a reduction-ledger entry carries the relation and tail flags flat."""
    cash_path = entry.get("cash_path") if isinstance(entry.get("cash_path"), dict) else {}
    return {
        "card_role": entry.get("card_role"),
        "requires_incremental_justification": entry.get("requires_incremental_justification"),
        "cash_path_relation": cash_path.get("relation") or entry.get("cash_path_relation"),
        "tail_extension": (
            cash_path.get("tail_extension") if "tail_extension" in cash_path else entry.get("tail_extension")
        ),
        "extension_points": (
            cash_path.get("extension_points") if "extension_points" in cash_path else entry.get("extension_points")
        ),
    }


def _filled_exposure_figures(block: Any, path: str = "card_review") -> list[str]:
    """Every exposure placeholder in a card review that is NOT null.

    The repository sizes nothing, so at decision time every one of them is
    null. A record carrying a number there would be a stake nobody chose."""
    found: list[str] = []
    if isinstance(block, dict):
        for key, value in block.items():
            if key == "exposure_after_sizing" and isinstance(value, dict):
                found.extend(
                    f"{path}.{key}.{name}"
                    for name, figure in value.items()
                    if name != "supplied_by" and figure is not None
                )
            else:
                found.extend(_filled_exposure_figures(value, f"{path}.{key}"))
    elif isinstance(block, list):
        for index, value in enumerate(block):
            found.extend(_filled_exposure_figures(value, f"{path}[{index}]"))
    return found


def validate_decision_record(record: dict[str, Any]) -> list[str]:
    """Every reason this record may not be written or read. Empty means it may."""
    problems: list[str] = []
    if not isinstance(record, dict):
        return ["record is not an object"]
    version = record.get("schema_version")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        problems.append(
            f"schema_version {version!r} is not one of {list(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    for name in ("record_id", "created_at", "producer"):
        if not isinstance(record.get(name), str) or not record[name].strip():
            problems.append(f"{name} is required and must be a non-empty string")
    if not isinstance(record.get("games"), dict):
        problems.append("games must be an object keyed by game_key")
    if not isinstance(record.get("candidates"), list):
        problems.append("candidates must be a list")
    else:
        for index, candidate in enumerate(record["candidates"]):
            if not isinstance(candidate, dict):
                problems.append(f"candidates[{index}] is not an object")
                continue
            if not candidate.get("market_ticker"):
                problems.append(f"candidates[{index}] has no market_ticker")
            if candidate.get("side") not in ("YES", "NO"):
                problems.append(f"candidates[{index}] side must be YES or NO")
            if candidate.get("selected") is not True:
                problems.append(f"candidates[{index}] is not marked selected")
    if not isinstance(record.get("evaluated_not_selected"), list):
        problems.append("evaluated_not_selected must be a list")
    if "bankroll_context" not in record:
        problems.append("bankroll_context must be present (null when not emitted)")
    # OPTIONAL in 1.0.0 (absent on records written before it existed), but
    # when present it must be the contract the artifact handed over: operator
    # sizing authority, and no exposure figure filled in.
    review = record.get("card_review")
    if review is not None:
        if not isinstance(review, dict):
            problems.append("card_review must be an object or null")
        else:
            if review.get("sizing_authority") != "operator":
                problems.append("card_review.sizing_authority must be 'operator'")
            if review.get("repository_sizes_positions") is not False:
                problems.append("card_review.repository_sizes_positions must be false")
            for where in _filled_exposure_figures(review):
                problems.append(f"{where} is filled; a decision record never invents an exposure figure")
    return problems
