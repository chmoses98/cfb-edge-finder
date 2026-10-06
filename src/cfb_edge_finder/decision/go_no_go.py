"""Week 1 GO / NO-GO: is the system safe to COLLECT and RESEARCH today?

*** WHAT THIS IS NOT ***

Not a profitability certification, and deliberately not influenced by
model edge in either direction. A large model-market disagreement is
neither a reason to go nor a reason to stop -- it is the research
subject. Making edge a GO condition would quietly turn an integrity check
into a trading signal.

*** THE THREE VERDICTS ***

    GO_RESEARCH                 Collect and research today.
    GO_RESEARCH_WITH_WARNINGS   Proceed; known limitations are listed.
    NO_GO                       Something makes today's data untrustworthy
                                or a safety lock has failed.

*** WHY 'NO NATURAL SETTLEMENT YET' IS A WARNING, NOT A NO-GO ***

Because collection is exactly what fixes it. Blocking collection until
settled data exists would guarantee settled data never arrives. The
absence of evidence is a limitation on what we may CONCLUDE, not a reason
to stop gathering.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class GoNoGoVerdict(StrEnum):
    GO_RESEARCH = "GO_RESEARCH"
    GO_RESEARCH_WITH_WARNINGS = "GO_RESEARCH_WITH_WARNINGS"
    NO_GO = "NO_GO"


class BlockerCode(StrEnum):
    """Conditions that make today's collection untrustworthy. Each one
    means data gathered now would be wrong, unusable, or unsafe -- not
    merely unprofitable."""

    CFBD_UNAVAILABLE = "CFBD_UNAVAILABLE"
    KALSHI_UNIVERSE_COLLAPSED = "KALSHI_UNIVERSE_COLLAPSED"
    CURRENT_SCHEMA_DEFECT = "CURRENT_SCHEMA_DEFECT"
    DUPLICATE_OR_MALFORMED_CORPUS = "DUPLICATE_OR_MALFORMED_CORPUS"
    TRAINING_LEAKAGE = "TRAINING_LEAKAGE"
    FBS_MAPPING_DEFECT = "FBS_MAPPING_DEFECT"
    INVALID_PROBABILITIES = "INVALID_PROBABILITIES"
    FEE_PROVENANCE_UNUSABLE = "FEE_PROVENANCE_UNUSABLE"
    CLOSING_TRIGGER_INSUFFICIENT = "CLOSING_TRIGGER_INSUFFICIENT"
    SAFETY_LOCK_BROKEN = "SAFETY_LOCK_BROKEN"
    EXECUTION_SURFACE_PRESENT = "EXECUTION_SURFACE_PRESENT"


class WarningCode(StrEnum):
    """Known limitations. Real, worth stating, and not reasons to stop."""

    NO_NATURAL_SETTLEMENT_YET = "NO_NATURAL_SETTLEMENT_YET"
    NO_CLV_YET = "NO_CLV_YET"
    UNSUPPORTED_FCS_POPULATION = "UNSUPPORTED_FCS_POPULATION"
    CONTEXTUAL_SOURCE_MISSING = "CONTEXTUAL_SOURCE_MISSING"
    QUIET_PERIOD_CADENCE = "QUIET_PERIOD_CADENCE"
    LEGACY_SCHEMA_ROWS_PRESENT = "LEGACY_SCHEMA_ROWS_PRESENT"
    ZERO_CURRENT_SEASON_INFORMATION = "ZERO_CURRENT_SEASON_INFORMATION"


@dataclass(frozen=True)
class GoNoGoItem:
    code: str
    detail: str


@dataclass
class GoNoGoReport:
    blockers: list[GoNoGoItem] = field(default_factory=list)
    warnings: list[GoNoGoItem] = field(default_factory=list)

    @property
    def verdict(self) -> GoNoGoVerdict:
        if self.blockers:
            return GoNoGoVerdict.NO_GO
        if self.warnings:
            return GoNoGoVerdict.GO_RESEARCH_WITH_WARNINGS
        return GoNoGoVerdict.GO_RESEARCH

    def block(self, code: BlockerCode, detail: str) -> None:
        self.blockers.append(GoNoGoItem(code.value, detail))

    def warn(self, code: WarningCode, detail: str) -> None:
        self.warnings.append(GoNoGoItem(code.value, detail))

    def to_payload(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "blockers": [{"code": b.code, "detail": b.detail} for b in self.blockers],
            "warnings": [{"code": w.code, "detail": w.detail} for w in self.warnings],
        }

    def render(self) -> str:
        lines = [f"VERDICT: {self.verdict.value}"]
        if self.blockers:
            lines.append("  BLOCKERS:")
            lines += [f"    [{b.code}] {b.detail}" for b in self.blockers]
        if self.warnings:
            lines.append("  WARNINGS (known limitations, not reasons to stop):")
            lines += [f"    [{w.code}] {w.detail}" for w in self.warnings]
        if not self.blockers and not self.warnings:
            lines.append("  No blockers, no warnings.")
        lines.append("  This is a data-integrity and safety verdict. It is NOT a")
        lines.append("  profitability certification and model edge is not an input.")
        return "\n".join(lines)


def evaluate_go_no_go(
    *,
    duplicate_rows: int,
    malformed_rows: int,
    non_prospective_rows: int,
    current_schema_missing_market_status: int,
    invalid_probability_count: int,
    fee_provenance_failures: int,
    safety_locks_ok: bool,
    execution_surface_found: bool,
    closing_trigger_at_risk: bool,
    kalshi_markets_discovered: int | None,
    cfbd_reachable: bool | None,
    settled_games: int,
    clv_observations: int,
    unsupported_population_rows: int,
    legacy_schema_rows: int,
    zero_carryover_games: int,
    contextual_sources_missing: int,
    quiet_period_active: bool,
    minimum_expected_markets: int = 100,
) -> GoNoGoReport:
    """Deterministic verdict from already-gathered facts.

    Every input is a count or a boolean the caller measured; nothing is
    fetched here, so the same facts always produce the same verdict."""
    report = GoNoGoReport()

    if cfbd_reachable is False:
        report.block(BlockerCode.CFBD_UNAVAILABLE, "the schedule source did not answer")
    if kalshi_markets_discovered is not None and kalshi_markets_discovered < minimum_expected_markets:
        report.block(
            BlockerCode.KALSHI_UNIVERSE_COLLAPSED,
            f"only {kalshi_markets_discovered} markets discovered, below the {minimum_expected_markets} "
            f"floor -- a collapse this large means discovery is broken, not that the slate is small",
        )
    if duplicate_rows or malformed_rows or non_prospective_rows:
        report.block(
            BlockerCode.DUPLICATE_OR_MALFORMED_CORPUS,
            f"{duplicate_rows} duplicate, {malformed_rows} malformed, "
            f"{non_prospective_rows} non-prospective row(s)",
        )
    if current_schema_missing_market_status:
        report.block(
            BlockerCode.CURRENT_SCHEMA_DEFECT,
            f"{current_schema_missing_market_status} current-schema row(s) missing market_status",
        )
    if invalid_probability_count:
        report.block(
            BlockerCode.INVALID_PROBABILITIES,
            f"{invalid_probability_count} contract(s) with non-finite or out-of-range probability",
        )
    if fee_provenance_failures:
        report.block(
            BlockerCode.FEE_PROVENANCE_UNUSABLE,
            f"{fee_provenance_failures} priced, tradeable contract(s) without a verified fee schedule",
        )
    if not safety_locks_ok:
        report.block(BlockerCode.SAFETY_LOCK_BROKEN, "a safety lock is not holding")
    if execution_surface_found:
        report.block(
            BlockerCode.EXECUTION_SURFACE_PRESENT, "an order-placement surface appeared in the codebase"
        )
    if closing_trigger_at_risk:
        report.block(
            BlockerCode.CLOSING_TRIGGER_INSUFFICIENT,
            "a CLOSING window is imminent and the observed trigger interval cannot cover it",
        )

    if settled_games <= 0:
        report.warn(
            WarningCode.NO_NATURAL_SETTLEMENT_YET,
            "0 settled games. Collection is what fixes this, so it is not a reason to stop.",
        )
    if clv_observations <= 0:
        report.warn(WarningCode.NO_CLV_YET, "no genuine CLOSING observation has been captured yet")
    if unsupported_population_rows:
        report.warn(
            WarningCode.UNSUPPORTED_FCS_POPULATION,
            f"{unsupported_population_rows} row(s) outside the supported FBS-vs-FBS population "
            f"remain correctly unpriced",
        )
    if legacy_schema_rows:
        report.warn(
            WarningCode.LEGACY_SCHEMA_ROWS_PRESENT,
            f"{legacy_schema_rows} row(s) predate the current schema; their absent fields are "
            f"legacy gaps, not defects",
        )
    if zero_carryover_games:
        report.warn(
            WarningCode.ZERO_CURRENT_SEASON_INFORMATION,
            f"{zero_carryover_games} game(s) projected with zero weight on the current season -- "
            f"the point estimate carries no 2026 information",
        )
    if contextual_sources_missing:
        report.warn(
            WarningCode.CONTEXTUAL_SOURCE_MISSING,
            f"{contextual_sources_missing} contextual field(s) have no dependable source",
        )
    if quiet_period_active:
        report.warn(
            WarningCode.QUIET_PERIOD_CADENCE,
            "external scheduler is in its intentional low-cadence quiet period; no critical "
            "checkpoint is near",
        )
    return report
