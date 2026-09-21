"""How did we actually do. Computed from the LEDGER and from nothing else.

*** WHERE EVERY NUMBER COMES FROM ***
Money comes from `wagers/<season>.jsonl` and `settlements/<season>.jsonl`, and
from nowhere else. Those are rows the venue's own fill evidence produced, via
the destination's importer, and they are the only record of what happened.

Recommendations are joined in for ATTRIBUTION only: to cut the same realised
P&L by robustness tier, by confidence, by edge bucket, by whether the fill
respected the bet-up-to price. Not one figure changes because a recommendation
exists or does not. `tests/test_postmortem.py` runs the whole report twice --
with the recommendation ledger and with it empty -- and asserts every monetary
total is identical.

*** WHAT IS NEVER GUESSED ***
An unsettled wager is not a loss. A P&L the ledger cannot state is not zero. A
report covering some of the wagers is not the season's report, and this refuses
to print a headline total until every wager's economics are established --
`is_final` is the flag, and `render` prints PARTIAL in capitals when it is
false. A number that covers two thirds of the bets, printed beside the bet
count, will be read as covering all of them.

*** CAUSALITY IS NOT INFERRED FROM AN OUTCOME ***
A robust positive-EV bet that lost is not a handicap error. It is the expected
behaviour of a bet that was never certain, and a postmortem that called it one
would teach the operator to abandon exactly the process that was working. The
`issue_categories` block therefore assigns a category only where the EVIDENCE
is about the process rather than about the result:

  EXECUTION ERROR   filled above the price the analysis said to stop at.
                    Visible without knowing the outcome.
  SIZING ERROR      staked materially away from what was recommended.
                    Also visible without the outcome.
  DATA ERROR        the bet was placed on a game whose factual inputs were
                    already recorded as insufficient.
  HANDICAP ERROR    only where a whole TIER lost across enough bets to be
                    worth looking at -- and it is reported as a prompt to
                    look, never as a conclusion.
  NORMAL VARIANCE   everything else. The default, deliberately.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from cfb_edge_finder.accounting.recommendation_link import (
    MatchResult,
    MatchState,
)

SETTLED = "SETTLED"

NOT_MODEL_EVIDENCE = (
    "ACCOUNTING ONLY. Every wager here was placed by the owner. A recommendation "
    "carried alongside one is evidence that an analysis named the same market first, "
    "not that it caused the bet -- and no figure in this report is computed from a "
    "recommendation. This is evidence about a bankroll and is not evidence about any "
    "model, which remains research-only."
)

#: Fee-adjusted edge buckets, in probability units. Boundaries chosen to be
#: readable rather than calibrated -- this repository has never validated a bar
#: at which a CFB Kalshi edge is real, and these are a way of grouping bets,
#: not a claim about which group is better.
EDGE_BUCKETS = ((0.0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 1.01))

#: Below this many settled bets a tier's record is noise, and the report says
#: so rather than showing a P&L that reads like a finding.
MIN_SETTLED_FOR_A_TIER_READING = 8


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def edge_bucket(edge: float | None) -> str:
    if edge is None:
        return "unknown"
    if edge < 0:
        return "negative"
    for low, high in EDGE_BUCKETS:
        if low <= edge < high:
            return f"{low:.2f}-{high:.2f}"
    return "unknown"


def market_family_of(ticker: str | None) -> tuple[str, str]:
    """(family, period) from the Kalshi series ticker, or ("unknown", "unknown").

    Uses the catalog's own structural classifier, the same one the live path
    uses. A ticker it cannot read is `unknown` rather than a guess: this is a
    grouping for a report, and a wrong group is worse than an honest one.
    """
    if not ticker:
        return "unknown", "unknown"
    from cfb_edge_finder.catalog.classification import classify_from_series_ticker

    series = str(ticker).split("-", 1)[0]
    classification = classify_from_series_ticker(series)
    if classification is None:
        return "unknown", "unknown"
    return str(classification.family.value), str(classification.period.value)


def game_key_of(ticker: str | None) -> str:
    """The Kalshi game code embedded in a market ticker, or "unknown".

    `KXNCAAFSPREAD-26SEP19LSUMISS-LSU10` -> `26SEP19LSUMISS`. A grouping key
    for a report, read from the venue's own ticker, and `unknown` when the
    shape is not the one this reads."""
    parts = str(ticker or "").split("-")
    return parts[1] if len(parts) >= 3 else "unknown"


@dataclass
class Bucket:
    """One cut's realised economics. Money only where the ledger states it."""

    label: str
    wagers: int = 0
    settled: int = 0
    won: int = 0
    lost: int = 0
    other_result: int = 0
    staked: float = 0.0
    fees_paid: float = 0.0
    gross_return: float = 0.0
    net_profit_loss: float = 0.0
    profit_loss_established: int = 0
    profit_loss_unestablished: int = 0

    @property
    def is_complete(self) -> bool:
        return self.wagers > 0 and self.profit_loss_unestablished == 0

    @property
    def roi(self) -> float | None:
        """Realised P&L over the stake that produced it.

        None when the P&L is incomplete, because a return computed over every
        bet's stake and only some bets' outcomes is not a return on anything.
        """
        if not self.is_complete or self.staked <= 0:
            return None
        return round(self.net_profit_loss / self.staked, 6)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "wagers": self.wagers,
            "settled": self.settled,
            "won": self.won,
            "lost": self.lost,
            "other_result": self.other_result,
            "staked": round(self.staked, 4),
            "fees_paid": round(self.fees_paid, 4),
            "gross_return": round(self.gross_return, 4) if self.is_complete else None,
            "net_profit_loss": (
                round(self.net_profit_loss, 4) if self.is_complete else None
            ),
            "roi": self.roi,
            "profit_loss_established": self.profit_loss_established,
            "profit_loss_unestablished": self.profit_loss_unestablished,
            "complete": self.is_complete,
            "reading": (
                None
                if self.settled >= MIN_SETTLED_FOR_A_TIER_READING
                else f"{self.settled} settled bet(s): too few for this cut to mean anything"
            ),
        }


def _accumulate(bucket: Bucket, wager: dict[str, Any], settlement: dict[str, Any]) -> None:
    bucket.wagers += 1
    bucket.staked += _number(wager.get("stake")) or 0.0
    bucket.fees_paid += _number(wager.get("fees_paid")) or 0.0

    if settlement.get("settlement_status") == SETTLED:
        bucket.settled += 1
        result = settlement.get("result")
        if result == "WON":
            bucket.won += 1
        elif result == "LOST":
            bucket.lost += 1
        else:
            bucket.other_result += 1

    profit_loss = _number(settlement.get("net_profit_loss"))
    if profit_loss is None:
        bucket.profit_loss_unestablished += 1
        return
    bucket.profit_loss_established += 1
    bucket.net_profit_loss += profit_loss
    gross = _number(settlement.get("gross_return"))
    if gross is not None:
        bucket.gross_return += gross


@dataclass
class Postmortem:
    season: int
    overall: Bucket = field(default_factory=lambda: Bucket("overall"))
    by_game: dict[str, Bucket] = field(default_factory=dict)
    by_market_family: dict[str, Bucket] = field(default_factory=dict)
    by_period: dict[str, Bucket] = field(default_factory=dict)
    by_robustness: dict[str, Bucket] = field(default_factory=dict)
    by_confidence: dict[str, Bucket] = field(default_factory=dict)
    by_data_quality: dict[str, Bucket] = field(default_factory=dict)
    by_edge_bucket: dict[str, Bucket] = field(default_factory=dict)
    by_attribution: dict[str, Bucket] = field(default_factory=dict)
    match_counts: dict[str, int] = field(default_factory=dict)
    correlated_exposure: list[dict[str, Any]] = field(default_factory=list)
    price_vs_recommended: dict[str, Any] = field(default_factory=dict)
    issue_categories: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    unit: float | None = None

    @property
    def is_final(self) -> bool:
        return self.overall.is_complete

    @property
    def unit_result(self) -> float | None:
        """Realised P&L in units. None without an explicit unit.

        Never inferred from the average stake: an operator who varied their
        size would get a "unit" that never existed, and a unit figure derived
        from the bets it is meant to measure is circular."""
        if self.unit is None or self.unit <= 0 or not self.is_final:
            return None
        return round(self.overall.net_profit_loss / self.unit, 4)

    def as_dict(self) -> dict[str, Any]:
        def cuts(buckets: dict[str, Bucket]) -> list[dict[str, Any]]:
            return [buckets[key].as_dict() for key in sorted(buckets)]

        settled_games = {
            key: bucket for key, bucket in self.by_game.items() if bucket.is_complete
        }
        best = max(settled_games.values(), key=lambda b: b.net_profit_loss, default=None)
        worst = min(settled_games.values(), key=lambda b: b.net_profit_loss, default=None)

        return {
            "schema_version": "cfb_postmortem/1.0.0",
            "season": self.season,
            "not_model_evidence": NOT_MODEL_EVIDENCE,
            "is_final": self.is_final,
            "completeness_note": (
                "every wager's economics are established"
                if self.is_final
                else (
                    f"PARTIAL: {self.overall.profit_loss_unestablished} of "
                    f"{self.overall.wagers} wager(s) have no established profit and loss. "
                    "No headline total is stated, because a total assembled from some of the "
                    "wagers is not the season's."
                )
            ),
            "overall": self.overall.as_dict(),
            "unit": self.unit,
            "unit_result": self.unit_result,
            "denominators": {
                "roi": "realised net profit and loss divided by the stake that produced it",
                "unit_result": (
                    "realised net profit and loss divided by the unit the OPERATOR supplied. "
                    "Absent without one: a unit inferred from the average stake would be a "
                    "figure that never existed."
                ),
            },
            "by_game": cuts(self.by_game),
            "by_market_family": cuts(self.by_market_family),
            "by_period": cuts(self.by_period),
            "by_robustness_tier": cuts(self.by_robustness),
            "by_confidence_tier": cuts(self.by_confidence),
            "by_data_quality_tier": cuts(self.by_data_quality),
            "by_recommended_edge_bucket": cuts(self.by_edge_bucket),
            "by_attribution": cuts(self.by_attribution),
            "largest_winning_game": best.as_dict() if best else None,
            "largest_losing_game": worst.as_dict() if worst else None,
            "correlated_exposure": self.correlated_exposure,
            "recommendation_matching": self.match_counts,
            "price_vs_recommended": self.price_vs_recommended,
            "issue_categories": self.issue_categories,
        }


def build(
    wagers: list[dict[str, Any]],
    settlements: list[dict[str, Any]],
    season: int,
    *,
    matches: MatchResult | None = None,
    recommendations: list[Any] | None = None,
    unit: float | None = None,
) -> Postmortem:
    """The whole report.

    `matches` and `recommendations` add CUTS. They never add or change money:
    every figure is accumulated from the wager row and its settlement row, and
    a bet with no recommendation simply lands in the `unattributed` bucket
    with its own economics intact."""
    report = Postmortem(season=season, unit=unit)

    by_key = {
        s.get("source_bet_key"): s
        for s in settlements
        if isinstance(s, dict) and s.get("source_bet_key")
    }
    match_by_key = matches.by_key() if matches else {}
    recommendation_by_id = {
        r.recommendation_id: r for r in (recommendations or [])
    }
    if matches:
        report.match_counts = matches.counts

    def bucket(buckets: dict[str, Bucket], label: str) -> Bucket:
        if label not in buckets:
            buckets[label] = Bucket(label)
        return buckets[label]

    exposure: dict[str, list[str]] = defaultdict(list)
    price_deltas: list[float] = []
    above_ceiling = 0
    execution_issues: list[dict[str, Any]] = []
    sizing_issues: list[dict[str, Any]] = []
    data_issues: list[dict[str, Any]] = []

    for wager in wagers:
        key = wager.get("source_bet_key")
        # A wager carrying its own settlement fields is honoured as a fallback:
        # the router never sends one, the field exists on the record, and
        # silently ignoring a populated field is the same class of defect as
        # silently dropping one.
        settlement = by_key.get(key) or wager
        ticker = wager.get("market_ticker")
        family, period = market_family_of(ticker)
        game = game_key_of(ticker)

        _accumulate(report.overall, wager, settlement)
        _accumulate(bucket(report.by_game, game), wager, settlement)
        _accumulate(bucket(report.by_market_family, family), wager, settlement)
        _accumulate(bucket(report.by_period, period), wager, settlement)

        match = match_by_key.get(key)
        recommendation = (
            recommendation_by_id.get(match.recommendation_id) if match else None
        )

        _accumulate(
            bucket(report.by_attribution, match.state if match else "unmatched"),
            wager,
            settlement,
        )
        _accumulate(
            bucket(
                report.by_robustness,
                (recommendation.robustness if recommendation else None) or "not_recommended",
            ),
            wager,
            settlement,
        )
        _accumulate(
            bucket(
                report.by_confidence,
                (recommendation.confidence if recommendation else None) or "not_recommended",
            ),
            wager,
            settlement,
        )
        _accumulate(
            bucket(
                report.by_data_quality,
                (recommendation.data_quality_ceiling if recommendation else None)
                or "not_recommended",
            ),
            wager,
            settlement,
        )
        _accumulate(
            bucket(
                report.by_edge_bucket,
                edge_bucket(recommendation.fee_adjusted_edge) if recommendation else "not_recommended",
            ),
            wager,
            settlement,
        )

        group = (
            recommendation.correlation_group
            if recommendation and recommendation.correlation_group
            else f"game:{game}"
        )
        exposure[f"{game} / {group}"].append(str(ticker))

        if match and match.price_delta is not None:
            price_deltas.append(match.price_delta)
        if match and match.state == MatchState.EXECUTED_ABOVE_BET_UP_TO.value:
            above_ceiling += 1
            execution_issues.append(
                {
                    "ticker": ticker,
                    "category": "EXECUTION ERROR",
                    "evidence": match.reason,
                    "note": (
                        "visible WITHOUT the outcome. A bet filled past its own ceiling was a "
                        "worse bet than the one recommended, whether or not it won."
                    ),
                }
            )
        if match and match.state == MatchState.EXECUTED_DIFFERENT_SIZE.value:
            sizing_issues.append(
                {
                    "ticker": ticker,
                    "category": "SIZING ERROR",
                    "evidence": match.reason,
                    "note": "also visible without the outcome.",
                }
            )
        if recommendation and recommendation.data_quality_ceiling in (
            "insufficient",
            "low",
        ):
            data_issues.append(
                {
                    "ticker": ticker,
                    "category": "DATA/PROCESS ERROR",
                    "evidence": (
                        f"the factual inputs for this game were recorded as "
                        f"{recommendation.data_quality_ceiling} before the bet was placed"
                    ),
                    "note": (
                        "not a claim that the bet was wrong. A claim that the process knew it "
                        "was reasoning from thin evidence and proceeded anyway."
                    ),
                }
            )

    report.correlated_exposure = [
        {
            "group": group,
            "contracts": sorted(tickers),
            "contract_count": len(tickers),
            "note": (
                "contracts in one group win or lose together. Two different tickers are not "
                "diversification."
            ),
        }
        for group, tickers in sorted(exposure.items())
        if len(tickers) > 1
    ]

    report.price_vs_recommended = {
        "matched_bets": len(price_deltas),
        "mean_price_delta": (
            round(sum(price_deltas) / len(price_deltas), 6) if price_deltas else None
        ),
        "worst_price_delta": round(max(price_deltas), 6) if price_deltas else None,
        "executed_above_bet_up_to": above_ceiling,
        "note": (
            "positive means paid MORE than the recommendation quoted. Computed from the "
            "ledger's own execution price and the artifact's own quote; neither is adjusted "
            "to the other."
        ),
    }

    # HANDICAP ERROR is the one category an outcome can suggest, and it is
    # reported as a PROMPT rather than a verdict -- and only where a whole tier
    # lost across enough settled bets that variance is not the obvious reading.
    handicap_prompts = []
    for label, tier in sorted(report.by_robustness.items()):
        if label == "not_recommended" or not tier.is_complete:
            continue
        if tier.settled < MIN_SETTLED_FOR_A_TIER_READING:
            continue
        if tier.net_profit_loss < 0:
            handicap_prompts.append(
                {
                    "tier": label,
                    "category": "HANDICAP ERROR (PROMPT, NOT A VERDICT)",
                    "evidence": (
                        f"{tier.settled} settled bets in the {label} tier, "
                        f"{tier.net_profit_loss:+.2f} realised"
                    ),
                    "note": (
                        "a losing tier is a reason to re-read the handicaps in it. It is NOT a "
                        "finding: a robust positive-EV bet that loses is the expected behaviour "
                        "of a bet that was never certain, and calling that a handicap error "
                        "would teach the operator to abandon the process that was working."
                    ),
                }
            )

    report.issue_categories = {
        "EXECUTION_ERROR": execution_issues,
        "SIZING_ERROR": sizing_issues,
        "DATA_PROCESS_ERROR": data_issues,
        "HANDICAP_ERROR_PROMPTS": handicap_prompts,
        "NORMAL_VARIANCE": [
            {
                "note": (
                    "every settled bet not named above. The default, deliberately: an outcome "
                    "is not evidence about a decision, and a postmortem that assigned a cause "
                    "to every loss would be assigning causes to variance."
                ),
                "settled_bets": report.overall.settled,
                "categorised": len(execution_issues) + len(sizing_issues) + len(data_issues),
            }
        ],
    }
    return report


def render(report: Postmortem) -> str:
    document = report.as_dict()
    overall = document["overall"]
    lines = [
        NOT_MODEL_EVIDENCE,
        "",
        f"CFB POSTMORTEM -- season {report.season}",
        f"  {'FINAL' if report.is_final else 'PARTIAL'}: {document['completeness_note']}",
        "",
        f"  wagers:      {overall['wagers']}",
        f"  settled:     {overall['settled']}  (pending {overall['wagers'] - overall['settled']})",
        f"  won/lost:    {overall['won']} / {overall['lost']}"
        + (f"  other {overall['other_result']}" if overall["other_result"] else ""),
        f"  staked:      {overall['staked']:.2f}",
        f"  fees paid:   {overall['fees_paid']:.2f}",
    ]
    if report.is_final:
        lines += [
            f"  gross return:{overall['gross_return']:.2f}",
            f"  net P&L:     {overall['net_profit_loss']:+.2f}",
            f"  ROI:         {overall['roi']:+.4f}  (net P&L / stake)",
        ]
        if report.unit_result is not None:
            lines.append(f"  units:       {report.unit_result:+.2f}  (unit {report.unit})")
        else:
            lines.append("  units:       not stated -- no unit was supplied")
    else:
        lines.append("  net P&L:     NOT STATED while any wager's economics are unestablished")

    def section(title: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        lines.extend(["", f"  {title}:"])
        for row in rows:
            money = (
                f"{row['net_profit_loss']:+9.2f}" if row["net_profit_loss"] is not None
                else "  PARTIAL"
            )
            note = f"   ({row['reading']})" if row["reading"] else ""
            lines.append(
                f"    {row['label']:34} n={row['wagers']:3} settled={row['settled']:3} "
                f"{money}{note}"
            )

    section("by market family", document["by_market_family"])
    section("by period", document["by_period"])
    section("by robustness tier", document["by_robustness_tier"])
    section("by confidence tier", document["by_confidence_tier"])
    section("by factual data-quality tier", document["by_data_quality_tier"])
    section("by recommended edge bucket", document["by_recommended_edge_bucket"])
    section("by attribution", document["by_attribution"])

    if document["recommendation_matching"]:
        lines.extend(["", "  recommendation matching:"])
        for state, count in document["recommendation_matching"].items():
            lines.append(f"    {state:34} {count}")

    price = document["price_vs_recommended"]
    if price["matched_bets"]:
        lines.extend(
            [
                "",
                "  execution against the recommendation:",
                f"    matched bets:              {price['matched_bets']}",
                f"    mean price delta:          {price['mean_price_delta']:+.4f}",
                f"    worst price delta:         {price['worst_price_delta']:+.4f}",
                f"    filled above bet-up-to:    {price['executed_above_bet_up_to']}",
            ]
        )

    if document["correlated_exposure"]:
        lines.extend(["", "  correlated exposure (these move together):"])
        for group in document["correlated_exposure"]:
            lines.append(f"    {group['group']:40} {group['contract_count']} contracts")

    categorised = document["issue_categories"]
    lines.extend(["", "  issues, where the EVIDENCE supports naming one:"])
    for category in ("EXECUTION_ERROR", "SIZING_ERROR", "DATA_PROCESS_ERROR"):
        entries = categorised.get(category) or []
        lines.append(f"    {category:24} {len(entries)}")
        for entry in entries[:5]:
            lines.append(f"      - {entry['evidence']}")
    for prompt in categorised.get("HANDICAP_ERROR_PROMPTS") or []:
        lines.append(f"    PROMPT: {prompt['tier']} -- {prompt['evidence']}")
    lines.append(
        "    everything else is NORMAL VARIANCE. A losing bet is not evidence of a "
        "mistake, and this report does not pretend otherwise."
    )
    return "\n".join(lines)
