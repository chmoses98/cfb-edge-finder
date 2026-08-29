"""PAPER CARD: a read-only, research-only, explicitly-requested ranked
view of current model-vs-Kalshi disagreement.

*** WHAT THIS IS ***
A THIN VIEW over machinery that already exists. It loads the captured
prospective corpus through `expression.corpus.load_contract_snapshots`,
groups it through `expression.grouping.build_universe`, and reads each
executable side's fee-aware economics from
`expression.economics.ExpressionEconomics` -- the module whose
`research_probability_surplus` (model probability for the exact
executable side, minus the fee-adjusted break-even probability from the
VERIFIED current Kalshi fee schedule) is the one and only ranking
quantity here. Nothing in this module prices, parses, maps, fits, or
fees anything itself.

*** WHAT THIS IS NOT ***
Not a recommendation, not a qualification, not a stake, not a size, not
an execution instruction, and not a validated expected value. No
empirical threshold exists or is consulted; the limit is a display
parameter only. `PaperPosition`/`PaperCard` are deliberately distinct
types from anything in `cfb_edge_finder.recommendation` and carry no
field a sizing or execution layer could consume. The rendered output is
run through `decision.report.assert_vocabulary_clean`, the same banned-
framing gate the standard report uses.

*** THE DETERMINISTIC "CURRENT OBSERVATION" POLICY ***
One snapshot per ticker: the LATEST captured pregame observation, which
is exactly `load_contract_snapshots`' documented default. No synthetic
backfill, no substitution of later prices onto earlier labels -- the
timing label shown is the label the capture genuinely carried, so
CLOSING appears only when a genuine CLOSING row exists. A ticker whose
latest row cannot be proven pregame (unknown kickoff, kickoff at/behind
`now`, non-PROSPECTIVE capture mode) is excluded, fail-closed.

*** EXPRESSION DEDUPLICATION (two documented levels) ***
1. Within one EQUIVALENCE GROUP (identical settlement event, e.g. home
   moneyline YES vs away moneyline NO): the representative is the
   CHEAPEST ALL-IN eligible expression -- the repository's existing
   dominance rule (`expression.economics.find_dominated_expressions` /
   `decision.expression_selection.select_expression`'s ordering), with
   the same deterministic tie-breaks (cost, price, ticker, YES before
   NO).
2. Across events within one THESIS -- a (game, dimension) pair, where
   MARGIN deliberately spans moneyline AND every spread rung (see
   `expression.taxonomy.MarketDimension.MARGIN`) and TOTAL spans the
   over ladder: exactly ONE position is surfaced, the event
   representative with the LARGEST research_probability_surplus
   (tie-break: all-in cost ascending, then ticker, then YES before NO).
   This is what keeps six nested rungs of one spread ladder from
   flooding the card; the count of suppressed sibling expressions is
   reported on the position instead.

*** SHADOW ORIENTATION AND ELIGIBILITY ***
The talent-shadow probability shown for a position comes ONLY from a
linked shadow row (joined on the corpus row's own `observation_key`)
whose `probability_semantics_version` equals
`shadow_contract_pricing.PROBABILITY_SEMANTICS_VERSION`
("shadow_probability_contract_oriented_v2") -- the semantics under which
`shadow_probability` was priced through the identical pricer, parsed
contract and resolved side as the canonical `model_probability`. Older
v1-semantics rows are margin-eligible only and are structurally excluded
here. For a NO expression, both the CONTROL and SHADOW probabilities are
complemented -- exact for a binary contract that settles the same event
-- at this call site, mirroring `build_expression_economics`' documented
"complement at the call site" rule. Shadow agreement is a description,
never a validation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from cfb_edge_finder.decision.expression_selection import EXECUTABLE_MARKET_STATUSES
from cfb_edge_finder.decision.report import assert_vocabulary_clean
from cfb_edge_finder.expression.corpus import load_contract_snapshots
from cfb_edge_finder.expression.economics import ExpressionEconomics
from cfb_edge_finder.expression.grouping import ContractSnapshot, build_universe
from cfb_edge_finder.research.preseason.shadow_contract_pricing import (
    PROBABILITY_SEMANTICS_VERSION,
)
from cfb_edge_finder.schemas.common import MarketFamily, Side

PROSPECTIVE_CAPTURE_MODE = "PROSPECTIVE"
"""schemas.corpus_row.CaptureMode's prospective literal -- the ONLY
capture mode admitted to the card; anything else (including an unstamped
None) is excluded, fail-closed."""

DEFAULT_LIMIT = 10
"""Display parameter only: how many ranked rows to print. It carries no
scientific meaning and gates nothing."""

SELECTION_POLICY = (
    "latest captured pregame observation per ticker; one representative "
    "expression per settlement event (cheapest all-in, existing dominance "
    "rule); one position per (game, dimension) thesis (largest fee-aware "
    "apparent disagreement); ranked by research_probability_surplus "
    "descending; deterministic tie-breaks (all-in cost, ticker, YES<NO)"
)

BANNER = (
    "====================================================================\n"
    "CFB PAPER CARD -- RESEARCH ONLY -- UNVALIDATED MODEL-MARKET DISAGREEMENT\n"
    "NO BETTING THRESHOLD IS APPROVED. NO STAKES. NO EXECUTION.\n"
    "These are PAPER positions: descriptive research rows, not wagers.\n"
    "===================================================================="
)

FOOTER = (
    "STATUS: PAPER / RESEARCH ONLY.\n"
    "These rankings are descriptive model-market disagreements, not validated\n"
    "betting recommendations. Apparent gaps are UNVALIDATED; no empirical\n"
    "threshold exists, qualification is disabled, and nothing here can be\n"
    "sized, priced for entry, or executed."
)


@dataclass(frozen=True)
class PaperPosition:
    """ONE ranked research row. Deliberately NOT a RecommendationCard,
    carries no stake/size/ceiling field, and never will."""

    rank: int
    game_id: str
    matchup: str
    kickoff_utc: str
    market_ticker: str
    description: str
    family: str
    executable_side: str
    timing_label: str
    captured_at: str
    quote_age_minutes: float | None
    executable_price: float
    estimated_fee: float
    fee_adjusted_break_even: float
    control_probability: float
    control_apparent_gap: float
    shadow_probability: float | None
    shadow_apparent_gap: float | None
    shadow_status: str
    directional_note: str
    control_projected_margin: float | None
    shadow_projected_margin: float | None
    model_version: str | None
    fee_schedule_version: str | None
    thesis_dimension: str
    related_expressions_suppressed: int
    related_events_in_thesis: int
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "game_id": self.game_id,
            "matchup": self.matchup,
            "kickoff_utc": self.kickoff_utc,
            "market_ticker": self.market_ticker,
            "description": self.description,
            "family": self.family,
            "executable_side": self.executable_side,
            "timing_label": self.timing_label,
            "captured_at": self.captured_at,
            "quote_age_minutes": self.quote_age_minutes,
            "executable_price": self.executable_price,
            "estimated_fee": self.estimated_fee,
            "fee_adjusted_break_even": self.fee_adjusted_break_even,
            "control_probability": self.control_probability,
            "control_apparent_gap": self.control_apparent_gap,
            "shadow_probability": self.shadow_probability,
            "shadow_apparent_gap": self.shadow_apparent_gap,
            "shadow_status": self.shadow_status,
            "directional_note": self.directional_note,
            "control_projected_margin": self.control_projected_margin,
            "shadow_projected_margin": self.shadow_projected_margin,
            "model_version": self.model_version,
            "fee_schedule_version": self.fee_schedule_version,
            "thesis_dimension": self.thesis_dimension,
            "related_expressions_suppressed": self.related_expressions_suppressed,
            "related_events_in_thesis": self.related_events_in_thesis,
            "notes": list(self.notes),
            "paper_research_only": True,
            "validated": False,
        }


@dataclass
class PaperCard:
    """The whole research view, with its exclusion accounting."""

    generated_at: str
    selection_policy: str = SELECTION_POLICY
    positions: tuple[PaperPosition, ...] = ()
    limit: int = DEFAULT_LIMIT
    tickers_considered: int = 0
    theses_considered: int = 0
    excluded_not_pregame: int = 0
    excluded_not_prospective: int = 0
    excluded_market_not_executable: int = 0
    excluded_unpriceable: int = 0
    shadow_rows_probability_eligible: int = 0
    shadow_rows_excluded_pre_v2_semantics: int = 0

    def to_payload(self) -> dict:
        return {
            "paper_research_only": True,
            "validated": False,
            "generated_at": self.generated_at,
            "selection_policy": self.selection_policy,
            "limit_display_parameter_only": self.limit,
            "positions": [p.to_dict() for p in self.positions],
            "tickers_considered": self.tickers_considered,
            "theses_considered": self.theses_considered,
            "excluded_not_pregame": self.excluded_not_pregame,
            "excluded_not_prospective": self.excluded_not_prospective,
            "excluded_market_not_executable": self.excluded_market_not_executable,
            "excluded_unpriceable": self.excluded_unpriceable,
            "shadow_rows_probability_eligible": self.shadow_rows_probability_eligible,
            "shadow_rows_excluded_pre_v2_semantics": self.shadow_rows_excluded_pre_v2_semantics,
        }


@dataclass
class _ThesisCandidate:
    economics: ExpressionEconomics
    snapshot: ContractSnapshot
    events_in_thesis: int = 0
    expressions_in_thesis: int = 0


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _matchup_of(game_id: str) -> str:
    """Readable matchup from the canonical game_id slug -- display only,
    never used for any semantic decision."""
    parts = game_id.split("-")
    if len(parts) > 3 and parts[0] == "cfb" and parts[2].startswith("wk"):
        return " ".join(parts[3:])
    return game_id


def _ticker_team_label(market_ticker: str) -> str:
    """The team abbreviation embedded in the ticker suffix (display only;
    semantics always come from the captured parsed fields)."""
    suffix = market_ticker.rsplit("-", 1)[-1]
    return suffix.rstrip("0123456789") or suffix


def _describe(snapshot: ContractSnapshot, side: Side) -> str:
    semantics = snapshot.semantics
    if semantics.family is MarketFamily.MONEYLINE:
        team = _ticker_team_label(semantics.market_ticker)
        return f"{team} moneyline -- {side.value.upper()}"
    if semantics.family is MarketFamily.SPREAD:
        team = _ticker_team_label(semantics.market_ticker)
        return f"{team} wins by over {semantics.threshold} -- {side.value.upper()}"
    if semantics.family is MarketFamily.TOTAL:
        if side is Side.YES:
            return f"Over {semantics.threshold} total points -- YES"
        return f"Over {semantics.threshold} total points -- NO (total <= {semantics.threshold})"
    return f"{semantics.market_ticker} -- {side.value.upper()}"


def _load_shadow_probability_map(shadow_path: Path) -> tuple[dict[str, dict], int, int]:
    """observation_key -> the probability-semantics-safe shadow row.

    ONLY rows stamped with the contract-oriented v2 probability semantics
    are admitted to the probability map; anything else (v1 rows, unstamped
    rows) is counted as excluded, never silently complemented or reused.
    Duplicate keys keep the latest `captured_at` deterministically.
    """
    eligible: dict[str, dict] = {}
    excluded_pre_v2 = 0
    if not shadow_path.exists():
        return eligible, 0, 0
    with shadow_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = row.get("observation_key")
            if not isinstance(key, str):
                continue
            if row.get("probability_semantics_version") != PROBABILITY_SEMANTICS_VERSION:
                excluded_pre_v2 += 1
                continue
            existing = eligible.get(key)
            if existing is None or str(row.get("captured_at") or "") > str(existing.get("captured_at") or ""):
                eligible[key] = row
    return eligible, len(eligible), excluded_pre_v2


def _oriented(probability: float | None, side: Side) -> float | None:
    """Complement a YES-oriented probability for a NO expression. Exact
    for a binary contract: both sides settle the SAME event."""
    if probability is None:
        return None
    return probability if side is Side.YES else 1.0 - probability


def build_paper_card(
    observations_path: Path,
    shadow_path: Path,
    *,
    now: datetime,
    limit: int = DEFAULT_LIMIT,
) -> PaperCard:
    """Assemble the card. Read-only: nothing is written, re-priced, or
    backfilled; every number is carried from what was genuinely captured."""
    card = PaperCard(generated_at=now.isoformat(), limit=limit)

    load = load_contract_snapshots(observations_path)
    card.tickers_considered = load.tickers_seen

    pregame: list[ContractSnapshot] = []
    for snapshot in load.snapshots:
        if snapshot.capture_mode != PROSPECTIVE_CAPTURE_MODE:
            card.excluded_not_prospective += 1
            continue
        kickoff = _parse_iso(snapshot.kickoff_utc_at_capture)
        if kickoff is None or kickoff <= now:
            card.excluded_not_pregame += 1
            continue
        pregame.append(snapshot)

    universe = build_universe(pregame)
    by_ticker = {s.semantics.market_ticker: s for s in pregame}

    shadow_map, shadow_eligible, shadow_excluded = _load_shadow_probability_map(shadow_path)
    card.shadow_rows_probability_eligible = shadow_eligible
    card.shadow_rows_excluded_pre_v2_semantics = shadow_excluded

    thesis_winners: list[_ThesisCandidate] = []
    for game in universe.games.values():
        for dimension_group in game.dimensions.values():
            event_representatives: list[ExpressionEconomics] = []
            expressions_in_thesis = 0
            for group in dimension_group.equivalence_groups.values():
                eligible: list[ExpressionEconomics] = []
                for expression in group.expressions:
                    snapshot = by_ticker.get(expression.market_ticker)
                    if snapshot is None:
                        continue
                    if (snapshot.market_status or "").strip().lower() not in EXECUTABLE_MARKET_STATUSES:
                        card.excluded_market_not_executable += 1
                        continue
                    if not expression.priceable or expression.research_probability_surplus is None:
                        card.excluded_unpriceable += 1
                        continue
                    eligible.append(expression)
                expressions_in_thesis += len(eligible)
                if not eligible:
                    continue
                # Existing dominance rule: cheapest all-in represents an
                # identical-settlement event; documented tie-breaks.
                representative = min(
                    eligible,
                    key=lambda e: (
                        e.all_in_cost,
                        e.executable_price,
                        e.market_ticker,
                        0 if e.executable_side is Side.YES else 1,
                    ),
                )
                event_representatives.append(representative)

            if not event_representatives:
                continue
            card.theses_considered += 1
            best = sorted(
                event_representatives,
                key=lambda e: (
                    -e.research_probability_surplus,
                    e.all_in_cost,
                    e.market_ticker,
                    0 if e.executable_side is Side.YES else 1,
                ),
            )[0]
            thesis_winners.append(
                _ThesisCandidate(
                    economics=best,
                    snapshot=by_ticker[best.market_ticker],
                    events_in_thesis=len(event_representatives),
                    expressions_in_thesis=expressions_in_thesis,
                )
            )

    thesis_winners.sort(
        key=lambda c: (
            -c.economics.research_probability_surplus,
            c.economics.all_in_cost,
            c.economics.market_ticker,
            0 if c.economics.executable_side is Side.YES else 1,
        )
    )

    positions: list[PaperPosition] = []
    for rank, candidate in enumerate(thesis_winners[:limit], start=1):
        economics = candidate.economics
        snapshot = candidate.snapshot
        side = economics.executable_side

        captured = _parse_iso(snapshot.captured_at)
        age_minutes = None if captured is None else round((now - captured).total_seconds() / 60.0, 1)

        shadow_row = shadow_map.get(snapshot.observation_key or "")
        shadow_probability = None
        shadow_gap = None
        control_margin = None
        shadow_margin = None
        if shadow_row is None:
            shadow_status = "unavailable: no probability-semantics-safe shadow row linked to this capture"
        elif not shadow_row.get("available") or shadow_row.get("shadow_probability") is None:
            shadow_status = (
                f"unavailable: {shadow_row.get('unavailable_reason') or 'shadow arm did not price this contract'}"
            )
            control_margin = shadow_row.get("control_projected_margin")
            shadow_margin = shadow_row.get("shadow_projected_margin")
        else:
            shadow_probability = _oriented(shadow_row.get("shadow_probability"), side)
            shadow_gap = (
                None
                if shadow_probability is None
                else shadow_probability - economics.fee_adjusted_break_even_probability
            )
            shadow_status = "available (research-only sidecar; agreement is not validation)"
            control_margin = shadow_row.get("control_projected_margin")
            shadow_margin = shadow_row.get("shadow_projected_margin")

        control_gap = economics.research_probability_surplus
        if shadow_gap is None:
            directional = "shadow unavailable -- no directional comparison"
        elif control_gap > 0 and shadow_gap > 0:
            directional = f"CONTROL and SHADOW both read the market below their {side.value.upper()} probability"
        elif control_gap < 0 and shadow_gap < 0:
            directional = f"CONTROL and SHADOW both read the market above their {side.value.upper()} probability"
        else:
            directional = "CONTROL and SHADOW point in different directions on this contract"

        notes: list[str] = []
        if age_minutes is not None and age_minutes > 24 * 60:
            notes.append(f"quote is {age_minutes / 60:.0f}h old (latest genuine capture for this ticker)")
        if candidate.expressions_in_thesis > 1:
            notes.append(
                f"{candidate.expressions_in_thesis - 1} related expression(s) across "
                f"{candidate.events_in_thesis} event(s) in this game's "
                f"{snapshot.semantics.dimension.value} thesis were represented by this row"
            )

        positions.append(
            PaperPosition(
                rank=rank,
                game_id=snapshot.semantics.game_id,
                matchup=_matchup_of(snapshot.semantics.game_id),
                kickoff_utc=snapshot.kickoff_utc_at_capture or "",
                market_ticker=economics.market_ticker,
                description=_describe(snapshot, side),
                family=snapshot.semantics.family.value if snapshot.semantics.family else "unknown",
                executable_side=side.value.upper(),
                timing_label=snapshot.timing_label,
                captured_at=snapshot.captured_at,
                quote_age_minutes=age_minutes,
                executable_price=economics.executable_price,
                estimated_fee=economics.estimated_fee,
                fee_adjusted_break_even=economics.fee_adjusted_break_even_probability,
                control_probability=economics.model_probability_for_this_side,
                control_apparent_gap=control_gap,
                shadow_probability=shadow_probability,
                shadow_apparent_gap=shadow_gap,
                shadow_status=shadow_status,
                directional_note=directional,
                control_projected_margin=control_margin,
                shadow_projected_margin=shadow_margin,
                model_version=snapshot.model_version,
                fee_schedule_version=economics.fee_schedule_version,
                thesis_dimension=snapshot.semantics.dimension.value,
                related_expressions_suppressed=max(candidate.expressions_in_thesis - 1, 0),
                related_events_in_thesis=candidate.events_in_thesis,
                notes=tuple(notes),
            )
        )

    card.positions = tuple(positions)
    return card


def _fmt_points(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:+.1f}"


def _fmt_prob(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def render_paper_card(card: PaperCard) -> str:
    lines: list[str] = [BANNER, ""]
    lines.append(f"generated at    : {card.generated_at}")
    lines.append(f"selection policy: {card.selection_policy}")
    lines.append(
        f"considered      : {card.tickers_considered} ticker(s), {card.theses_considered} distinct "
        f"game/dimension thesis group(s); showing up to {card.limit} (display parameter only)"
    )
    lines.append(
        f"excluded        : {card.excluded_not_pregame} not provably pregame, "
        f"{card.excluded_not_prospective} not prospective, "
        f"{card.excluded_market_not_executable} market not executable, "
        f"{card.excluded_unpriceable} without a priceable fee-aware quote"
    )
    lines.append("")

    if not card.positions:
        lines.append("No paper positions: nothing currently satisfies the pregame,")
        lines.append("prospective, executable, fee-verified, model-priced criteria.")
    else:
        lines.append(
            f"  {'PAPER':<5} {'MARKET':<42} {'KALSHI':>7} {'FEE':>5} {'BRKEVN':>7} "
            f"{'CONTROL':>8} {'SHADOW':>7} {'GAP(pts)':>9}"
        )
        for p in card.positions:
            lines.append(
                f"  {p.rank:<5} {p.description[:42]:<42} "
                f"{p.executable_price * 100:>6.0f}c {p.estimated_fee * 100:>4.0f}c "
                f"{_fmt_prob(p.fee_adjusted_break_even):>7} "
                f"{_fmt_prob(p.control_probability):>8} {_fmt_prob(p.shadow_probability):>7} "
                f"{_fmt_points(p.control_apparent_gap):>9}"
            )
        lines.append("")
        for p in card.positions:
            lines.append(f"  [{p.rank}] {p.matchup}  ({p.game_id})")
            lines.append(f"      ticker {p.market_ticker}  family {p.family}  side {p.executable_side}")
            lines.append(
                f"      kickoff {p.kickoff_utc or 'unknown'}  snapshot {p.timing_label} "
                f"@ {p.captured_at}"
                + (f"  (age {p.quote_age_minutes:.0f} min)" if p.quote_age_minutes is not None else "")
            )
            lines.append(
                f"      executable {p.executable_price * 100:.0f}c + fee {p.estimated_fee * 100:.1f}c "
                f"-> fee-adjusted break-even {_fmt_prob(p.fee_adjusted_break_even)}"
            )
            lines.append(
                f"      CONTROL {_fmt_prob(p.control_probability)} "
                f"(apparent gap {_fmt_points(p.control_apparent_gap)} pts, UNVALIDATED)"
            )
            if p.shadow_probability is not None:
                lines.append(
                    f"      SHADOW  {_fmt_prob(p.shadow_probability)} "
                    f"(apparent gap {_fmt_points(p.shadow_apparent_gap)} pts, research-only)"
                )
            lines.append(f"      shadow status: {p.shadow_status}")
            lines.append(f"      direction    : {p.directional_note}")
            if p.control_projected_margin is not None or p.shadow_projected_margin is not None:
                control_m = "-" if p.control_projected_margin is None else f"{p.control_projected_margin:+.2f}"
                shadow_m = "-" if p.shadow_projected_margin is None else f"{p.shadow_projected_margin:+.2f}"
                lines.append(f"      projected home margin: CONTROL {control_m} / SHADOW {shadow_m}")
            lines.append(f"      model version: {p.model_version or 'unknown'}  fee schedule: "
                         f"{p.fee_schedule_version or 'unknown'}")
            for note in p.notes:
                lines.append(f"      note: {note}")
            lines.append("")

    lines.append(FOOTER)
    text = "\n".join(lines)
    # The same banned-framing gate the standard report enforces.
    assert_vocabulary_clean(text)
    return text
