"""Freezing the football artifact: canonical bytes, a hash, and why it changed. PURE.

The FROZEN CONTENT is everything football: identity, inputs fingerprints,
matchup profile, findings, scripts, confidence. Its SHA-256 over canonical
JSON (sorted keys, compact separators, UTF-8) is the `artifact_hash`.

The ENVELOPE adds what is not content: when the artifact was first
generated, and the regeneration log. An unchanged football picture keeps
its hash AND its original `generated_at`: re-running the builder on the same
inputs is a no-op, not a new publication.

When the hash changes, the reason is derived by comparing the two contents'
input fingerprints -- new games in the league log, a changed availability
listing, a changed identity resolution, a methodology change -- and recorded
in `regeneration`. Market data is never among the possible reasons because
it is never among the inputs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

FROZEN_KEYS = (
    "schema_version",
    "methodology_version",
    "adjustment_version",
    "event_id",
    "game_key",
    "season",
    "teams",
    "kickoff_utc",
    "neutral_site",
    "market_blind",
    "football_data_cutoff",
    "inputs",
    "data_confidence",
    "confidence",
    "matchup_profile",
    "matchup_findings",
    "game_scripts",
    "sift_read",
)


def canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )


def content_hash(content: dict[str, Any]) -> str:
    missing = [k for k in FROZEN_KEYS if k not in content]
    if missing:
        raise ValueError(f"frozen content is missing {missing}")
    extra = sorted(set(content) - set(FROZEN_KEYS))
    if extra:
        raise ValueError(f"frozen content carries keys outside the frozen set: {extra}")
    return hashlib.sha256(canonical_bytes(content)).hexdigest()


def regeneration_reasons(previous: dict[str, Any] | None, content: dict[str, Any]) -> list[str]:
    if previous is None:
        return ["FIRST_PUBLICATION"]
    before, after = previous.get("inputs") or {}, content.get("inputs") or {}
    reasons = []
    if previous.get("methodology_version") != content.get("methodology_version"):
        reasons.append("METHODOLOGY_CHANGED")
    if before.get("rows_fingerprint") != after.get("rows_fingerprint"):
        delta = (after.get("games_in_window") or 0) - (before.get("games_in_window") or 0)
        reasons.append(f"LEAGUE_GAMELOG_CHANGED ({delta:+d} games league-wide)")
    if before.get("team_games") != after.get("team_games"):
        reasons.append("TEAM_GAMELOG_CHANGED")
    if before.get("availability_fingerprint") != after.get("availability_fingerprint"):
        reasons.append("AVAILABILITY_CHANGED")
    if before.get("identity_fingerprint") != after.get("identity_fingerprint"):
        reasons.append("IDENTITY_CHANGED")
    if before.get("freshness_status") != after.get("freshness_status"):
        reasons.append("FRESHNESS_CHANGED")
    if previous.get("kickoff_utc") != content.get("kickoff_utc"):
        reasons.append("KICKOFF_CHANGED")
    return reasons or ["CONTENT_CHANGED_WITHOUT_INPUT_CHANGE"]


def freeze(
    content: dict[str, Any],
    *,
    generated_at: str,
    previous_envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap content in its envelope, reusing the previous envelope when unchanged."""
    digest = content_hash(content)
    from_the_future = previous_envelope is not None and str(previous_envelope.get("generated_at") or "") > generated_at
    if previous_envelope is not None and previous_envelope.get("artifact_hash") == digest and not from_the_future:
        return previous_envelope
    previous_content = (previous_envelope or {}).get("content")
    history = list((previous_envelope or {}).get("regeneration", {}).get("history") or [])
    reasons = regeneration_reasons(previous_content, content)
    if from_the_future:
        # A previous artifact stamped later than now cannot be the one this
        # publication froze; it is replaced, and the clock problem is named.
        reasons = ["PREVIOUS_ARTIFACT_STAMPED_AFTER_NOW"] + [r for r in reasons if not r.startswith("CONTENT_CHANGED")]
    if previous_envelope is not None:
        history.append(
            {
                "replaced_hash": previous_envelope.get("artifact_hash"),
                "replaced_generated_at": previous_envelope.get("generated_at"),
                "reasons": reasons,
                "at": generated_at,
            }
        )
    return {
        "artifact_hash": digest,
        "generated_at": generated_at,
        "regeneration": {"reasons": reasons, "history": history[-20:]},
        "content": content,
    }


def verify(envelope: dict[str, Any]) -> bool:
    """True when the envelope's content still hashes to its stated hash."""
    try:
        return content_hash(envelope["content"]) == envelope["artifact_hash"]
    except (KeyError, ValueError, TypeError):
        return False
