"""Durable per-game execution state.

*** WHY THIS EXISTS ***
A Saturday slate is 100+ games and a handicapping session is a long chain
of fallible interactions. If one response fails, the cost of starting over
is the whole slate. So progress is written per game, immediately, in a
file whose name is the game key -- and a restart resumes at the first game
that is not finished.

*** WHY A COMPLETED GAME IS NOT AUTOMATICALLY TRUSTED ***
A completion recorded against one version of the market is not evidence
about a different one. Every record carries the packet hash and the
eligible-ticker hash it was produced against, and a reload compares both:

  same packet hash                -> reusable exactly as recorded
  same tickers, different quotes  -> handicap still stands, EVALUATION IS
                                     INVALID (every price moved under it)
  different tickers               -> evaluation invalid AND the handicap is
                                     flagged for review, because the game
                                     now has markets nobody has seen
  MATERIAL FACTUAL CHANGE         -> evaluation invalid, the prior handicap
                                     is KEPT AS HISTORY, and the current one
                                     is `needs_review`: it may not produce a
                                     new recommendation until re-handicapped

The third case is the one that keeps the scan exhaustive: silently reusing a
completed state across a market change is exactly how a contract disappears
from a scan that still reports itself exhaustive.

The fourth is the one that keeps the HANDICAP honest. A handicap survives a
price move; it does not survive the starting quarterback changing, and until
this distinction existed the two were the same event -- a packet hash over
everything, invalidating pricing and preserving the football opinion whatever
had actually moved. `context.MATERIAL_DOMAINS` decides which is which, and it
is deliberately narrow: availability, environment and game identity. A tenth
of a point of yards-per-play is a refreshed fact, not a different game, and an
invalidation that fired on it would fire on everything and be ignored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from cfb_edge_finder.execution import STATE_SCHEMA_VERSION
from cfb_edge_finder.execution.evaluator import GameEvaluation
from cfb_edge_finder.execution.handicap import HandicapPayload
from cfb_edge_finder.execution.slate import canonical_hash


class ExecutionState(StrEnum):
    PENDING_HANDICAP = "pending_handicap"
    HANDICAP_COMPLETE = "handicap_complete"
    EVALUATION_PARTIAL = "evaluation_partial"
    EVALUATION_COMPLETE = "evaluation_complete"
    STALE_DUE_TO_INPUT_CHANGE = "stale_due_to_input_change"
    HANDICAP_NEEDS_REVIEW = "handicap_needs_review"
    """A material factual input moved under a handicap that is otherwise
    complete. The handicap is still on disk and still readable; it may not
    be used to produce a recommendation until somebody looks."""


TERMINAL_STATES = frozenset({ExecutionState.EVALUATION_COMPLETE.value})


def eligible_ticker_hash(packet: dict[str, Any]) -> str:
    return canonical_hash(sorted(str(c.get("ticker")) for c in packet.get("contracts") or []))


@dataclass
class GameState:
    game_key: str
    state: str = ExecutionState.PENDING_HANDICAP.value
    packet_hash: str | None = None
    market_universe_hash: str | None = None
    eligible_ticker_hash: str | None = None
    context_hash: str | None = None
    material_context_hash: str | None = None
    superseded_handicaps: list[dict[str, Any]] = field(default_factory=list)
    """Every handicap a material factual change retired, newest last.

    History, not a fallback. Nothing reads these to price anything -- they
    exist so a postmortem can say what was believed before the news, which
    is the difference between a handicap error and a data-timing error."""
    handicap_status: str = "absent"
    handicap_timestamp: str | None = None
    handicap_payload: dict[str, Any] | None = None
    eligible_contracts: int | None = None
    evaluated_contracts: int | None = None
    unpriceable_contracts: int | None = None
    unaccounted_contracts: int | None = None
    evaluation_completion_status: str | None = None
    selected_candidates: list[dict[str, Any]] = field(default_factory=list)
    completed_at: str | None = None
    invalidation_reason: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "game_key": self.game_key,
            "state": self.state,
            "packet_hash": self.packet_hash,
            "market_universe_hash": self.market_universe_hash,
            "eligible_ticker_hash": self.eligible_ticker_hash,
            "context_hash": self.context_hash,
            "material_context_hash": self.material_context_hash,
            "superseded_handicaps": self.superseded_handicaps[-5:],
            "handicap_status": self.handicap_status,
            "handicap_timestamp": self.handicap_timestamp,
            "handicap_payload": self.handicap_payload,
            "eligible_contracts": self.eligible_contracts,
            "evaluated_contracts": self.evaluated_contracts,
            "unpriceable_contracts": self.unpriceable_contracts,
            "unaccounted_contracts": self.unaccounted_contracts,
            "evaluation_completion_status": self.evaluation_completion_status,
            "selected_candidates": self.selected_candidates,
            "completed_at": self.completed_at,
            "invalidation_reason": self.invalidation_reason,
            "history": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> GameState:
        return cls(
            game_key=str(payload.get("game_key")),
            state=str(payload.get("state") or ExecutionState.PENDING_HANDICAP.value),
            packet_hash=payload.get("packet_hash"),
            market_universe_hash=payload.get("market_universe_hash"),
            eligible_ticker_hash=payload.get("eligible_ticker_hash"),
            context_hash=payload.get("context_hash"),
            material_context_hash=payload.get("material_context_hash"),
            superseded_handicaps=list(payload.get("superseded_handicaps") or []),
            handicap_status=str(payload.get("handicap_status") or "absent"),
            handicap_timestamp=payload.get("handicap_timestamp"),
            handicap_payload=payload.get("handicap_payload"),
            eligible_contracts=payload.get("eligible_contracts"),
            evaluated_contracts=payload.get("evaluated_contracts"),
            unpriceable_contracts=payload.get("unpriceable_contracts"),
            unaccounted_contracts=payload.get("unaccounted_contracts"),
            evaluation_completion_status=payload.get("evaluation_completion_status"),
            selected_candidates=list(payload.get("selected_candidates") or []),
            completed_at=payload.get("completed_at"),
            invalidation_reason=payload.get("invalidation_reason"),
            history=list(payload.get("history") or []),
        )

    @property
    def is_complete(self) -> bool:
        return self.state == ExecutionState.EVALUATION_COMPLETE.value

    @property
    def has_usable_handicap(self) -> bool:
        return self.handicap_status == "complete" and bool(self.handicap_payload)

    def note(self, event: str, detail: str) -> None:
        self.history.append(
            {"at": datetime.now(UTC).isoformat(), "event": event, "detail": detail}
        )


class StateStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, game_key: str) -> Path:
        return self.root / f"{game_key}.json"

    def load(self, game_key: str) -> GameState:
        path = self.path_for(game_key)
        if not path.exists():
            return GameState(game_key=game_key)
        return GameState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, state: GameState) -> None:
        self.path_for(state.game_key).write_text(
            json.dumps(state.as_dict(), indent=1, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )

    def all_states(self) -> list[GameState]:
        return [
            GameState.from_dict(json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(self.root.glob("*.json"))
            if p.name != "state_index.json"
        ]

    # ------------------------------------------------------ reconciliation

    def reconcile_with_packet(self, packet: dict[str, Any]) -> GameState:
        """Load this game's state and align it with the packet on disk.

        Returns a state that is safe to act on: either genuinely current, or
        explicitly marked stale with the reason AND with the right thing
        invalidated. Three different things can have moved and they have three
        different costs, which is the whole reason this is not one comparison.
        """
        game_key = str(packet.get("game_key"))
        state = self.load(game_key)
        packet_hash = packet.get("packet_hash")
        ticker_hash = eligible_ticker_hash(packet)
        context = packet.get("factual_context") or {}
        context_hash = context.get("context_hash")
        material_hash = context.get("material_context_hash")
        eligible = len(packet.get("contracts") or [])

        if state.packet_hash is None:
            state.packet_hash = packet_hash
            state.market_universe_hash = packet.get("market_universe_hash")
            state.eligible_ticker_hash = ticker_hash
            state.context_hash = context_hash
            state.material_context_hash = material_hash
            state.eligible_contracts = eligible
            return state

        material_changed = (
            state.material_context_hash is not None
            and material_hash is not None
            and state.material_context_hash != material_hash
        )

        if state.packet_hash == packet_hash and not material_changed:
            state.eligible_contracts = eligible
            return state

        tickers_changed = state.eligible_ticker_hash != ticker_hash
        if material_changed:
            reason = (
                "a MATERIAL factual input changed since this handicap was formed "
                "(availability, environment or game identity); the football opinion is about a "
                "different game now"
            )
        elif tickers_changed:
            reason = "the game's eligible contract set changed since this state was written"
        else:
            reason = "quotes moved since this state was written; every evaluated price is out of date"

        state.note("invalidated", reason)
        state.invalidation_reason = reason
        state.packet_hash = packet_hash
        state.market_universe_hash = packet.get("market_universe_hash")
        state.eligible_ticker_hash = ticker_hash
        state.context_hash = context_hash
        state.eligible_contracts = eligible
        state.evaluated_contracts = None
        state.unpriceable_contracts = None
        state.unaccounted_contracts = None
        state.evaluation_completion_status = None
        state.selected_candidates = []
        state.completed_at = None

        if state.has_usable_handicap:
            if material_changed:
                # THE PRIOR HANDICAP IS KEPT, NOT DELETED. A postmortem asking
                # "did we get the game wrong, or did the news arrive after we
                # answered?" cannot answer it from a handicap that was
                # overwritten. It is retired to history and the live slot is
                # marked unusable until somebody re-handicaps.
                state.superseded_handicaps.append(
                    {
                        "retired_at": datetime.now(UTC).isoformat(),
                        "reason": reason,
                        "material_context_hash": state.material_context_hash,
                        "handicap": state.handicap_payload,
                    }
                )
                state.handicap_status = "complete_needs_review"
                state.state = ExecutionState.HANDICAP_NEEDS_REVIEW.value
            else:
                if tickers_changed:
                    state.handicap_status = "complete_needs_review"
                state.state = ExecutionState.STALE_DUE_TO_INPUT_CHANGE.value
        else:
            state.state = ExecutionState.PENDING_HANDICAP.value
        state.material_context_hash = material_hash
        return state

    # ------------------------------------------------------------ updates

    def record_handicap(self, packet: dict[str, Any], handicap: HandicapPayload) -> GameState:
        state = self.reconcile_with_packet(packet)
        state.handicap_status = "complete"
        state.handicap_timestamp = handicap.handicapped_at or datetime.now(UTC).isoformat()
        state.handicap_payload = handicap.as_dict()
        state.state = ExecutionState.HANDICAP_COMPLETE.value
        state.invalidation_reason = None
        # A fresh handicap is a handicap of the game AS IT IS NOW, so it clears
        # the review flag the material change raised. The retired payload stays
        # in `superseded_handicaps` either way.
        state.material_context_hash = (packet.get("factual_context") or {}).get(
            "material_context_hash"
        )
        state.context_hash = (packet.get("factual_context") or {}).get("context_hash")
        state.note("handicap_recorded", f"packet_hash={state.packet_hash}")
        self.save(state)
        return state

    def record_evaluation(
        self,
        packet: dict[str, Any],
        evaluation: GameEvaluation,
        selected: list[dict[str, Any]] | None = None,
    ) -> GameState:
        state = self.reconcile_with_packet(packet)
        state.eligible_contracts = evaluation.eligible
        state.evaluated_contracts = evaluation.evaluated
        state.unpriceable_contracts = evaluation.unpriceable
        state.unaccounted_contracts = evaluation.unaccounted
        state.evaluation_completion_status = "COMPLETE" if evaluation.complete else "INCOMPLETE"
        state.selected_candidates = list(selected or [])
        if state.handicap_status == "complete_needs_review" and evaluation.complete:
            # The arithmetic closed and the handicap behind it is under review.
            # Both are true and only one of them is a finish: an evaluation
            # priced from a handicap nobody has re-confirmed must not be
            # reported as a completed game, or `status` would show a resumable
            # slate as done.
            state.state = ExecutionState.HANDICAP_NEEDS_REVIEW.value
            state.completed_at = None
        elif evaluation.complete:
            state.state = ExecutionState.EVALUATION_COMPLETE.value
            state.completed_at = datetime.now(UTC).isoformat()
        else:
            state.state = ExecutionState.EVALUATION_PARTIAL.value
            state.completed_at = None
        state.note(
            "evaluation_recorded",
            f"{evaluation.evaluated} priced / {evaluation.unpriceable} unpriceable / "
            f"{evaluation.unaccounted} unaccounted",
        )
        self.save(state)
        return state

    def write_index(self, packets: list[dict[str, Any]], out_path: Path) -> dict[str, Any]:
        rows = []
        for packet in packets:
            state = self.reconcile_with_packet(packet)
            rows.append(
                {
                    "game_key": state.game_key,
                    "state": state.state,
                    "handicap_status": state.handicap_status,
                    "eligible_contracts": state.eligible_contracts,
                    "evaluated_contracts": state.evaluated_contracts,
                    "unpriceable_contracts": state.unpriceable_contracts,
                    "unaccounted_contracts": state.unaccounted_contracts,
                    "evaluation_completion_status": state.evaluation_completion_status,
                    "packet_hash": state.packet_hash,
                    "context_hash": state.context_hash,
                    "material_context_hash": state.material_context_hash,
                    "invalidation_reason": state.invalidation_reason,
                }
            )
            self.save(state)
        index = {
            "schema_version": STATE_SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "games": rows,
            "counts": {
                "total": len(rows),
                "complete": sum(
                    1 for r in rows if r["state"] == ExecutionState.EVALUATION_COMPLETE.value
                ),
                "pending_handicap": sum(
                    1 for r in rows if r["state"] == ExecutionState.PENDING_HANDICAP.value
                ),
                "stale": sum(
                    1
                    for r in rows
                    if r["state"] == ExecutionState.STALE_DUE_TO_INPUT_CHANGE.value
                ),
                "handicap_needs_review": sum(
                    1
                    for r in rows
                    if r["state"] == ExecutionState.HANDICAP_NEEDS_REVIEW.value
                ),
            },
        }
        out_path.write_text(
            json.dumps(index, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )
        return index


def next_incomplete(packets: list[dict[str, Any]], store: StateStore) -> dict[str, Any] | None:
    """Resume point: the first packet, in shard order, that is not already
    finished against the packet currently on disk."""
    for packet in packets:
        state = store.reconcile_with_packet(packet)
        if not state.is_complete:
            return packet
    return None
