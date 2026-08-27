"""Assembling the research analytics report (mission sections 22, 24).

Produces a structured result the CLI renders as JSON, CSV and Markdown.
Contains no "best bets", no rankings, and no thresholds -- see
analytics/slices.py for why every non-core cell is labelled EXPLORATORY.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from cfb_edge_finder.analytics.calibration_report import (
    MARKET_PRICE_CAVEAT,
    ModelMarketComparison,
    compare_model_to_market,
)
from cfb_edge_finder.analytics.dataset import AnalysisDataset, AnalysisRow
from cfb_edge_finder.analytics.metrics import ANALYTICS_CODE_VERSION
from cfb_edge_finder.analytics.slices import (
    ABSOLUTE_GAP_BUCKETS,
    CORE,
    EXPLORATORY,
    FAMILY_READINESS,
    PRICE_BUCKETS,
    SIGNED_GAP_BUCKETS,
    TIMING_ORDER,
    SliceSummary,
    absolute_gap_bucket,
    price_bucket,
    signed_gap_bucket,
    slice_by,
    summarize_slice,
)

INSUFFICIENT_DATA_MESSAGE = "INSUFFICIENT NATURAL SETTLEMENT DATA YET"


@dataclass
class FamilyReport:
    family: str
    readiness: str
    n: int
    overall: SliceSummary
    comparison: ModelMarketComparison | None
    signed_gap_buckets: list[SliceSummary] = field(default_factory=list)
    absolute_gap_buckets: list[SliceSummary] = field(default_factory=list)
    price_buckets: list[SliceSummary] = field(default_factory=list)
    timing: list[SliceSummary] = field(default_factory=list)


@dataclass
class AnalyticsReport:
    generated_at: str
    analytics_code_version: str
    sufficient_data: bool
    message: str
    corpus: dict[str, Any]
    health: dict[str, int]
    filters: dict[str, Any]
    families: list[FamilyReport] = field(default_factory=list)
    overall_timing: list[SliceSummary] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _side_rows(rows: list[AnalysisRow], side: str) -> list[AnalysisRow]:
    """Rows that actually carry an executable price on this side. A row
    with no NO quote cannot contribute to NO-side analysis and must not
    be counted in its denominator."""
    return [r for r in rows if (r.entry_yes_price if side == "yes" else r.entry_no_price) is not None]


def build_family_report(family: str, rows: list[AnalysisRow], *, side: str = "yes") -> FamilyReport:
    usable = _side_rows(rows, side)
    readiness = FAMILY_READINESS.get(family, "unknown readiness")
    caveat = (f"Model readiness for {family}: {readiness}",)

    comparison = None
    if usable:
        is_yes = side == "yes"
        model_probs = [r.model_probability if is_yes else (1.0 - r.model_probability) for r in usable]
        market_probs = [
            (r.entry_yes_price if is_yes else r.entry_no_price) for r in usable
        ]
        outcomes = [r.event_true if is_yes else (not r.event_true) for r in usable]
        comparison = compare_model_to_market(
            model_probabilities=model_probs,
            market_probabilities=[p for p in market_probs if p is not None],
            outcomes=outcomes,
            extra_caveats=caveat,
        )

    return FamilyReport(
        family=family,
        readiness=readiness,
        n=len(usable),
        # Family-level totals are CORE (preregistered); every sub-slice is
        # EXPLORATORY because that is where the multiplicity lives.
        overall=summarize_slice(
            family, "family", usable, side=side, analysis_status=CORE, extra_caveats=caveat
        ),
        comparison=comparison,
        signed_gap_buckets=slice_by(
            usable,
            lambda r: signed_gap_bucket(
                r.gaps.yes_probability_gap if side == "yes" else (r.gaps.no_probability_gap or 0.0)
            )
            if (r.gaps.yes_probability_gap if side == "yes" else r.gaps.no_probability_gap) is not None
            else None,
            "signed_gap_bucket",
            [b[0] for b in SIGNED_GAP_BUCKETS],
            side=side,
        ),
        absolute_gap_buckets=slice_by(
            usable,
            lambda r: absolute_gap_bucket(
                (r.gaps.yes_probability_gap if side == "yes" else r.gaps.no_probability_gap) or 0.0
            )
            if (r.gaps.yes_probability_gap if side == "yes" else r.gaps.no_probability_gap) is not None
            else None,
            "absolute_gap_bucket",
            [b[0] for b in ABSOLUTE_GAP_BUCKETS],
            side=side,
        ),
        price_buckets=slice_by(
            usable,
            lambda r: price_bucket(r.entry_yes_price if side == "yes" else r.entry_no_price)
            if (r.entry_yes_price if side == "yes" else r.entry_no_price) is not None
            else None,
            "price_bucket",
            [b[0] for b in PRICE_BUCKETS],
            side=side,
        ),
        timing=slice_by(usable, lambda r: r.timing_label, "timing", list(TIMING_ORDER), side=side),
    )


def build_report(
    dataset: AnalysisDataset, *, filters: dict[str, Any] | None = None, side: str = "yes"
) -> AnalyticsReport:
    rows = dataset.rows
    closing_counts = dict(dataset.closing_status_counts)
    corpus = {
        "total_prospective_observations": dataset.total_observations,
        "supported_observations": dataset.supported_observations,
        "settled_supported_observations": dataset.settled_supported_n,
        "unique_games": len(dataset.games),
        "closing_available": dataset.closing_available_n,
        "closing_missing": dataset.settled_supported_n - dataset.closing_available_n,
        "closing_status_breakdown": closing_counts,
        "model_versions": sorted(dataset.model_versions),
        "diagnostic_unsupported_rows": len(dataset.diagnostic_rows),
        "attributions_seen": dataset.attributions_seen,
        "ledger_load_count": dataset.ledger_load_count,
        "load_seconds": round(dataset.load_seconds, 4),
        "analysis_side": side,
    }

    warnings: list[str] = []
    if dataset.health.has_fatal:
        warnings.append(
            "FATAL data-integrity condition detected -- see health block. Analytics results are NOT reliable."
        )
    if dataset.health.rejected_non_prospective:
        warnings.append(
            f"{dataset.health.rejected_non_prospective} non-prospective row(s) were excluded from the primary "
            f"dataset (mission section 21: retrospective fixtures never enter headline metrics)."
        )
    if dataset.diagnostic_rows:
        warnings.append(
            f"{len(dataset.diagnostic_rows)} unsupported-population row(s) partitioned out of headline metrics."
        )

    if not rows:
        warnings.append(
            "No settled, supported, prospective observations are available yet. Every metric below is empty by "
            "construction -- this is the honest state of the corpus, not a computation failure."
        )
        return AnalyticsReport(
            generated_at=datetime.now(UTC).isoformat(),
            analytics_code_version=ANALYTICS_CODE_VERSION,
            sufficient_data=False,
            message=INSUFFICIENT_DATA_MESSAGE,
            corpus=corpus,
            health=dataset.health.as_dict(),
            filters=filters or {},
            warnings=warnings,
        )

    missing_close = corpus["closing_missing"]
    if missing_close:
        warnings.append(
            f"{missing_close} settled observation(s) have no genuine CLOSING link. They are EXCLUDED from CLV "
            f"aggregates (never counted as zero); CLV sample sizes are reported separately as `clv_n`."
        )
    warnings.append(MARKET_PRICE_CAVEAT)
    warnings.append(
        "All non-family slices are EXPLORATORY and uncorrected for multiple comparisons. No threshold, cutoff, "
        "tier, or selection rule is derived anywhere in this report."
    )

    families = sorted({r.family for r in rows})
    return AnalyticsReport(
        generated_at=datetime.now(UTC).isoformat(),
        analytics_code_version=ANALYTICS_CODE_VERSION,
        sufficient_data=True,
        message=f"{len(rows)} settled supported prospective observation(s) across {len(dataset.games)} game(s)",
        corpus=corpus,
        health=dataset.health.as_dict(),
        filters=filters or {},
        families=[build_family_report(f, [r for r in rows if r.family == f], side=side) for f in families],
        overall_timing=slice_by(
            _side_rows(rows, side), lambda r: r.timing_label, "timing", list(TIMING_ORDER),
            side=side, analysis_status=EXPLORATORY,
        ),
        warnings=warnings,
    )


def report_to_dict(report: AnalyticsReport) -> dict[str, Any]:
    def _convert(value):
        if hasattr(value, "__dataclass_fields__"):
            return {k: _convert(v) for k, v in asdict(value).items()}
        if isinstance(value, dict):
            return {k: _convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_convert(v) for v in value]
        return value

    return _convert(report)


SLICE_CSV_COLUMNS = (
    "dimension", "label", "analysis_status", "n", "n_games", "confidence_label",
    "mean_model_probability", "mean_entry_price", "mean_signed_gap", "observed_event_rate",
    "calibration_error", "clv_n", "mean_clv", "median_clv", "favorable_clv_rate",
    "gross_unit_pnl", "fee_adjusted_unit_pnl", "fee_adjusted_roi", "pnl_n",
)


def slices_to_csv_rows(report: AnalyticsReport) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def _emit(family: str, summary: SliceSummary) -> None:
        record = {c: getattr(summary, c, None) for c in SLICE_CSV_COLUMNS}
        record["family"] = family
        rows.append(record)

    for fam in report.families:
        _emit(fam.family, fam.overall)
        for group in (fam.signed_gap_buckets, fam.absolute_gap_buckets, fam.price_buckets, fam.timing):
            for summary in group:
                _emit(fam.family, summary)
    for summary in report.overall_timing:
        _emit("ALL", summary)
    return rows


def render_markdown(report: AnalyticsReport) -> str:
    lines: list[str] = ["# Research Analytics Report", ""]
    lines.append(f"*Generated {report.generated_at} — analytics `{report.analytics_code_version}`*")
    lines.append("")
    lines.append("**Research-only.** Descriptive measurement of the settled prospective corpus. ")
    lines.append("No bet recommendation, qualification tier, staking, or threshold appears anywhere below.")
    lines.append("")

    lines.append("## Corpus health")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    for key, value in report.corpus.items():
        if isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in value.items()) or "—"
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value) or "—"
        lines.append(f"| {key} | {value} |")
    lines.append("")

    fatal = {k: v for k, v in report.health.items() if v}
    lines.append("## Data integrity")
    lines.append("")
    lines.append("No issues detected." if not fatal else "| Condition | Count |\n|---|---|")
    for key, value in fatal.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")

    if not report.sufficient_data:
        lines.append(f"## {report.message}")
        lines.append("")
        lines.append(
            "The corpus contains no settled, supported, prospective observations yet, so no calibration, "
            "CLV, or ROI metric can be computed. This is the honest state of the data — nothing was "
            "fabricated to fill the gap. Metrics will populate naturally as captured games complete and "
            "the scheduled settlement workflow attributes them."
        )
        lines.append("")
    else:
        for fam in report.families:
            lines.append(f"## {fam.family} (n={fam.n})")
            lines.append("")
            lines.append(f"**Model readiness:** {fam.readiness}")
            lines.append("")
            if fam.comparison is not None:
                c = fam.comparison
                lines.append("| Scorer | Brier | Log loss | ECE |")
                lines.append("|---|---|---|---|")
                for name, rep in (("model", c.model), ("market (executable)", c.market)):
                    lines.append(
                        f"| {name} | {_fmt(rep.brier)} | {_fmt(rep.log_loss)} | "
                        f"{_fmt(rep.expected_calibration_error)} |"
                    )
                lines.append("")
                lines.append(
                    f"Brier difference (model − market): {_fmt(c.brier_difference)} "
                    "(negative = model scored better)"
                )
                lines.append("")
            lines.append(_slice_table("Signed gap buckets", fam.signed_gap_buckets))
            lines.append(_slice_table("Timing checkpoints", fam.timing))
            lines.append(_slice_table("Price buckets", fam.price_buckets))

        lines.append(_slice_table("All families — timing", report.overall_timing))

    if report.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    return "\n".join(lines)


def _fmt(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _slice_table(title: str, summaries: list[SliceSummary]) -> str:
    header = "| Slice | n | games | conf | mean gap | event rate | clv n | mean CLV | fee-adj ROI |"
    lines = [f"### {title}", "", header, "|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s.label} | {s.n} | {s.n_games} | {s.confidence_label} | {_fmt(s.mean_signed_gap)} | "
            f"{_fmt(s.observed_event_rate)} | {s.clv_n} | {_fmt(s.mean_clv)} | {_fmt(s.fee_adjusted_roi)} |"
        )
    lines.append("")
    return "\n".join(lines)
