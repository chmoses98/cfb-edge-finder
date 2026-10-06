"""The Research Decision Report.

*** WHAT THIS IS ***

A record of what the system CONSIDERED and where every candidate stopped.
It is a research diagnostic, written to be read by a person who wants to
know whether the pipeline is behaving, not by someone deciding what to
back.

*** WHAT THIS IS NOT, IN ITS OWN VOCABULARY ***

There is no "Best Bets" section, no ranking, no stake column, and no
wager language anywhere in the rendered text. That is enforced by
`BANNED_OUTPUT_VOCABULARY` and a test that renders a report and greps it,
rather than by an intention in this docstring. Sorting is by identifier
for reproducibility, deliberately NOT by gap, edge, or attractiveness --
an ordered list of opportunities is a recommendation whatever the header
above it says, and a reader will treat row one as the pick.

*** THE ZERO IS A MEASUREMENT ***

The qualified count is taken from `ShadowRunResult.shadow_qualified_count`,
which counts. If a lock ever failed, this report would say so rather than
continuing to print a reassuring zero.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from cfb_edge_finder.decision.artifact import NO_VALIDATED_THRESHOLD_SET
from cfb_edge_finder.decision.portfolio import EXPOSURE_LIMITS_ABSENT, PortfolioView
from cfb_edge_finder.decision.shadow import ShadowRunResult

REPORT_VERSION = "research_decision_report_v1"

BANNED_OUTPUT_VOCABULARY = (
    "best bet",
    "best bets",
    "top pick",
    "top picks",
    "lock of the",
    "recommended play",
    "recommended wager",
    "place a bet",
    "place a wager",
    "bet size",
    "stake size",
    "units to bet",
    "how much to bet",
    "action:",
)
"""Phrases this report must never contain, checked case-insensitively by
`assert_vocabulary_clean` and by a test. The list is about FRAMING: the
same numbers presented as a betting card read as instructions, and a
report that reads like instructions will eventually be used as
instructions."""

STANDING_LOCKS = (
    "QUALIFICATION_FOR_REAL_MONEY: DISABLED",
    "EMPIRICAL_THRESHOLD_ARTIFACT: ABSENT_OR_UNAPPROVED",
    "AUTO_VALIDATION: IMPOSSIBLE",
    "STAKING_CONNECTION_TO_DECISION_PIPELINE: ABSENT",
    "EXECUTION: ABSENT",
    "KALSHI_CLIENT: READ_ONLY",
    "ORDER_PLACEMENT: NONE",
    "BANKROLL_ACCESS: NONE",
)


class ReportVocabularyError(AssertionError):
    """Raised when rendered text contains banned framing."""


def assert_vocabulary_clean(text: str) -> None:
    """Fail loudly rather than publish a report that reads like a card."""
    lowered = text.lower()
    found = sorted({phrase for phrase in BANNED_OUTPUT_VOCABULARY if phrase in lowered})
    if found:
        raise ReportVocabularyError(f"Research Decision Report contains banned framing: {found}")


@dataclass(frozen=True)
class CorpusSummary:
    """Provenance of the observations the run read. All counts, no
    interpretation."""

    total_rows: int = 0
    prospective_rows: int = 0
    non_prospective_rows: int = 0
    settled_games: int = 0
    schema_versions: dict[str, int] = field(default_factory=dict)
    corpus_identifier: str = "UNKNOWN"


def report_payload(
    run: ShadowRunResult,
    *,
    portfolio: PortfolioView | None = None,
    evidence_state: str,
    corpus: CorpusSummary,
    generated_at: datetime,
) -> dict:
    """Machine-readable form. Same numbers as the text, so the two cannot
    drift into disagreeing."""
    return {
        "report_version": REPORT_VERSION,
        "generated_at": generated_at.isoformat(),
        "corpus": {
            "identifier": corpus.corpus_identifier,
            "total_rows": corpus.total_rows,
            "prospective_rows": corpus.prospective_rows,
            "non_prospective_rows": corpus.non_prospective_rows,
            "settled_games": corpus.settled_games,
            "schema_versions": dict(sorted(corpus.schema_versions.items())),
        },
        "threshold_artifact_status": run.artifact_status,
        "evidence_state": evidence_state,
        "candidates_considered": len(run.decisions),
        "shadow_qualified_count": run.shadow_qualified_count,
        "data_quality_pass_count": run.data_quality_pass_count,
        "state_counts": dict(sorted(run.state_counts().items())),
        "rejection_counts": dict(sorted(run.rejection_counts().items())),
        "portfolio": (
            {
                "distinct_theses": portfolio.distinct_theses,
                "contracts_grouped": portfolio.contract_count,
                "equivalence_groups": len(portfolio.equivalence_groups),
                "unresolved_groups": portfolio.unresolved_group_count,
                "limits_status": portfolio.limits_status,
            }
            if portfolio is not None
            else {"limits_status": EXPOSURE_LIMITS_ABSENT}
        ),
        "standing_locks": list(STANDING_LOCKS),
    }


def _bar(title: str) -> list[str]:
    return ["", f"== {title} " + "=" * max(0, 68 - len(title)), ""]


def render_report(
    run: ShadowRunResult,
    *,
    portfolio: PortfolioView | None = None,
    evidence_state: str,
    corpus: CorpusSummary,
    generated_at: datetime,
) -> str:
    """Human-readable text. Deterministic: every list is sorted by name,
    never by any measure of attractiveness."""
    lines: list[str] = [
        "RESEARCH DECISION REPORT",
        f"version: {REPORT_VERSION}",
        f"generated_at: {generated_at.isoformat()}",
        "",
        "This is a diagnostic record of what the research pipeline considered",
        "and where each candidate stopped. It contains no ranking and nothing",
        "actionable. Rows are sorted by identifier so the same input always",
        "produces the same file.",
    ]

    lines += _bar("STANDING LOCKS")
    lines += [f"  {lock}" for lock in STANDING_LOCKS]

    lines += _bar("CORPUS PROVENANCE")
    lines += [
        f"  corpus_identifier      : {corpus.corpus_identifier}",
        f"  total_rows             : {corpus.total_rows}",
        f"  prospective_rows       : {corpus.prospective_rows}",
        f"  non_prospective_rows   : {corpus.non_prospective_rows}",
        f"  settled_games          : {corpus.settled_games}",
    ]
    for version, count in sorted(corpus.schema_versions.items()):
        lines.append(f"  schema {version:<16}: {count}")

    lines += _bar("GATE STATUS")
    lines += [
        f"  threshold_artifact     : {run.artifact_status}",
        f"  evidence_state         : {evidence_state}",
    ]
    if run.artifact_status == NO_VALIDATED_THRESHOLD_SET:
        lines.append("  -> No approved threshold artifact exists, so no candidate can")
        lines.append("     pass the empirical gate. This is the designed state.")

    lines += _bar("WHAT THE PIPELINE CONSIDERED")
    lines += [
        f"  candidates_considered  : {len(run.decisions)}",
        f"  passed_data_quality    : {run.data_quality_pass_count}",
        f"  SHADOW_QUALIFIED       : {run.shadow_qualified_count}   (counted, not asserted)",
    ]

    lines += _bar("WHERE CANDIDATES STOPPED")
    state_counts = run.state_counts()
    if not state_counts:
        lines.append("  (no candidates evaluated in this run)")
    for state, count in sorted(state_counts.items()):
        lines.append(f"  {state:<28} {count}")

    rejections = run.rejection_counts()
    if rejections:
        lines += _bar("REJECTION REASONS")
        for reason, count in sorted(rejections.items()):
            lines.append(f"  {count:>6}  {reason}")

    lines += _bar("EXPOSURE GROUPING")
    if portfolio is None:
        lines.append("  (no portfolio view supplied for this run)")
        lines.append(f"  limits_status          : {EXPOSURE_LIMITS_ABSENT}")
    else:
        lines += [
            f"  contracts_grouped      : {portfolio.contract_count}",
            f"  distinct_theses        : {portfolio.distinct_theses}",
            f"  equivalence_groups     : {len(portfolio.equivalence_groups)}",
            f"  unresolved_groups      : {portfolio.unresolved_group_count}",
            f"  limits_status          : {portfolio.limits_status}",
            "  Note: distinct_theses counts latent football quantities, not",
            "  contracts. Many contracts on one game are one thesis.",
        ]

    lines += _bar("PER-CANDIDATE TRAIL")
    if not run.decisions:
        lines.append("  (none)")
    else:
        lines.append(f"  {'market_ticker':<34} {'side':<4} {'timing':<12} state")
        for decision in sorted(run.decisions, key=lambda d: (d.market_ticker, d.side)):
            timing = decision.timing_label or "-"
            lines.append(
                f"  {decision.market_ticker:<34} {decision.side:<4} {timing:<12} {decision.state.value}"
            )

    lines += _bar("END")
    lines.append("No ranking, no sizing, and no instruction is produced by this report.")
    text = "\n".join(lines) + "\n"
    assert_vocabulary_clean(text)
    return text


def summarize_states(run: ShadowRunResult) -> Counter[str]:
    """Convenience for callers that want the state histogram directly."""
    return Counter(d.state.value for d in run.decisions)
