"""The preregistered research protocol, as a verifiable object.

*** WHY A HASH ***

`docs/PROSPECTIVE_RESEARCH_PROTOCOL.md` is a promise about how results
will be analysed, made before the results exist. A promise nobody can
check is worth nothing, so every research report records the protocol
VERSION and a content HASH of the document it followed. A reader can then
recompute the hash and see whether the analysis followed the text that
was fixed in advance, or a text edited afterwards to fit.

*** WHAT THE HASH IS OVER ***

The bytes of the markdown file itself. Not a summary, not a subset --
editing a single threshold or population rule changes the hash, which is
the entire point. Whitespace matters too, deliberately: any edit at all
should be visible rather than quietly tolerated.

*** WHAT THIS MODULE CANNOT DO ***

It cannot approve anything, and it holds no thresholds. It records which
rules an analysis claimed to follow. Whether those rules were actually
obeyed is a matter for review, not for a hash.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

PROTOCOL_VERSION = "prospective_research_protocol_v1"

PROTOCOL_DOCUMENT = Path("docs/PROSPECTIVE_RESEARCH_PROTOCOL.md")

PREREGISTERED_AT = "2026-08-28"
"""The date the protocol was fixed, before the 2026-08-29 slate and
before ANY settled prospective observation existed. The commit
introducing the document is the real evidence; this constant is a
convenience for report headers."""

SETTLED_GAMES_AT_PREREGISTRATION = 0
"""Recorded so a later reader can confirm the protocol could not have
been fitted to results: there were none."""

CONFIRMATORY_QUESTIONS = (
    "Q1_signed_disagreement_predicts_outcomes",
    "Q2_signed_disagreement_predicts_movement_to_closing",
    "Q3_larger_disagreement_improves_fee_adjusted_research_pl",
)
"""Only these three. Everything else in the protocol is descriptive and
generates hypotheses for future weeks -- see the protocol's section 5."""

DESCRIPTIVE_QUESTIONS = (
    "Q4_by_family",
    "Q5_by_timing_label",
    "Q6_by_executable_price_band",
    "Q7_model_above_vs_below_market",
    "Q8_model_vs_market_calibration",
    "Q9_clv_corroboration",
    "Q10_replication_across_weeks",
    "Q1a_week1_context_ablation",
)

MANDATORY_PARTITIONS = ("model_version", "timing_label", "market_family")
"""Never pooled across. Two model versions are two populations, and an
EARLY_OPEN look is a different information state from a T_30 look."""

CLUSTER_UNIT = "game_id"
"""Every contract on one game shares one football outcome, so the unit of
independent evidence is the game. Intervals that treat contracts as
independent understate uncertainty by roughly the square root of the
ladder depth."""

REQUIRED_SAMPLE_REPORTING = ("observations", "distinct_contracts", "distinct_games")
"""All three, always together. A result quoted only in observations is
not a result."""


@dataclass(frozen=True)
class ProtocolManifest:
    """What an analysis claims to have followed."""

    version: str
    document_sha256: str
    preregistered_at: str
    settled_games_at_preregistration: int
    confirmatory_questions: tuple[str, ...]
    cluster_unit: str
    mandatory_partitions: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.version,
            "protocol_document_sha256": self.document_sha256,
            "preregistered_at": self.preregistered_at,
            "settled_games_at_preregistration": self.settled_games_at_preregistration,
            "confirmatory_questions": list(self.confirmatory_questions),
            "cluster_unit": self.cluster_unit,
            "mandatory_partitions": list(self.mandatory_partitions),
        }


def document_hash(path: Path | None = None) -> str:
    """SHA-256 of the protocol document, or `DOCUMENT_MISSING`.

    A missing document is reported rather than raising: a report that
    cannot find the protocol must still be able to say so honestly,
    instead of failing in a way that tempts someone to skip the manifest
    entirely."""
    target = path or PROTOCOL_DOCUMENT
    if not target.exists():
        return "DOCUMENT_MISSING"
    return hashlib.sha256(target.read_bytes()).hexdigest()


def manifest(path: Path | None = None) -> ProtocolManifest:
    return ProtocolManifest(
        version=PROTOCOL_VERSION,
        document_sha256=document_hash(path),
        preregistered_at=PREREGISTERED_AT,
        settled_games_at_preregistration=SETTLED_GAMES_AT_PREREGISTRATION,
        confirmatory_questions=CONFIRMATORY_QUESTIONS,
        cluster_unit=CLUSTER_UNIT,
        mandatory_partitions=MANDATORY_PARTITIONS,
    )
