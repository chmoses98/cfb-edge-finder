"""Health reporting for one settlement run (mission section 19).

Separate from research/health.py, which grades a CAPTURE run: the two have
different failure modes and blending them would make each harder to read.
A capture run fails on discovery collapse; a settlement run fails on a
settlement that disagrees with Kalshi.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cfb_edge_finder.research.health import Diagnostic, Severity


@dataclass
class SettlementHealthReport:
    observations_scanned: int = 0
    unsettled_eligible: int = 0
    games_checked: int = 0
    games_newly_final: int = 0
    attributions_written: int = 0
    duplicate_attempts: int = 0

    settled_yes: int = 0
    settled_no: int = 0
    game_not_final: int = 0
    market_not_final: int = 0
    result_unavailable: int = 0
    semantics_unresolved: int = 0
    mapping_unresolved: int = 0
    unsupported_population: int = 0
    settlement_mismatches: int = 0

    closing_captured: int = 0
    closing_missing: int = 0

    api_failures: int = 0
    persistence_failures: int = 0
    wall_clock_seconds: float = 0.0

    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)


SEMANTICS_UNRESOLVED_WARN_RATIO = 0.05
"""Above 5% of settled-eligible observations failing on semantics is a
warning: the contract metadata captured at observation time should be
sufficient by construction, so a meaningful rate means the capture path is
storing something incomplete."""


def evaluate_settlement_health(
    report: SettlementHealthReport, *, expected_final_games: int | None = None
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    # A settlement that disagrees with Kalshi is the one failure this
    # whole cross-check exists to catch. Always HIGH, never averaged away.
    if report.settlement_mismatches > 0:
        diagnostics.append(
            Diagnostic(
                Severity.HIGH,
                "settlement_mismatch",
                f"{report.settlement_mismatches} contract(s) where our derived settlement disagrees with "
                f"Kalshi's official result -- every research conclusion from those contracts is suspect",
            )
        )

    if report.api_failures > 0:
        diagnostics.append(
            Diagnostic(
                Severity.HIGH,
                "settlement_api_failures",
                f"{report.api_failures} data-source call(s) failed during settlement",
            )
        )
    if report.persistence_failures > 0:
        diagnostics.append(
            Diagnostic(
                Severity.HIGH,
                "settlement_persistence_failures",
                f"{report.persistence_failures} settlement persistence failure(s)",
            )
        )

    # "Games should have completed but none did" is a real signal; "no
    # games completed and none were expected to" is normal midweek and
    # must not fail the run.
    if expected_final_games is not None and expected_final_games > 0 and report.games_newly_final == 0:
        diagnostics.append(
            Diagnostic(
                Severity.WARNING,
                "no_games_final_when_expected",
                f"{expected_final_games} game(s) were expected to be final but none resolved -- "
                f"possible stale or failing result source",
            )
        )

    settled_total = report.settled_yes + report.settled_no
    if settled_total > 0:
        semantics_rate = report.semantics_unresolved / (settled_total + report.semantics_unresolved)
        if semantics_rate >= SEMANTICS_UNRESOLVED_WARN_RATIO:
            diagnostics.append(
                Diagnostic(
                    Severity.WARNING,
                    "semantics_unresolved_rate_elevated",
                    f"{semantics_rate:.0%} of settleable observations could not be resolved from their own "
                    f"captured contract semantics",
                )
            )

    if report.mapping_unresolved > 0:
        diagnostics.append(
            Diagnostic(
                Severity.INFO,
                "mapping_unresolved",
                f"{report.mapping_unresolved} observation(s) never mapped to a game and can never be settled",
            )
        )

    # Missing closes never block settlement (mission section 20) but must
    # stay visible, because CLV analytics later depends on them.
    if report.closing_missing > 0:
        diagnostics.append(
            Diagnostic(
                Severity.INFO,
                "closing_missing_for_settled",
                f"{report.closing_missing} settled observation(s) have no CLOSING snapshot linked",
            )
        )

    return diagnostics


def should_fail_settlement_run(diagnostics: list[Diagnostic]) -> bool:
    return any(d.severity == Severity.HIGH for d in diagnostics)
