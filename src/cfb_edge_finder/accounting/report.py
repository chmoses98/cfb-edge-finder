"""What the wager ledger says about the owner's money. NOT about the model.

    CFB MODEL STATUS = RESEARCH ONLY / DISABLED
    CFB ACCOUNTING   = ENABLED

Those two lines are why this module is careful in a way a summariser normally
would not need to be.

WHY A RECORD OF WINS AND LOSSES IS NOT A MODEL RESULT
-----------------------------------------------------
This ledger holds wagers the owner placed. Not one of them was recommended by
this repository -- that is the defining property of the kind, enforced by
`FORBIDDEN_PROVENANCE_FIELDS`, and it means a won-lost record computed here is
a fact about a bankroll and not evidence about a model.

The distinction is easy to state and easy to lose, because the number looks
exactly like the number a backtest produces. So the summary CARRIES its own
disclaimer as a field rather than leaving it to a caller's docstring, and
:func:`render` prints it. A reader who sees only the output still sees it.

WHAT IS REFUSED
---------------
Nothing here ranks, sizes, prices or recommends. It counts what happened and
adds up money that already moved. A field this ledger cannot state honestly is
reported as UNESTABLISHED with the count of wagers in that state -- never as a
zero, because zero is a settled result that happens to pay nothing, which is a
different claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Printed with every summary. Stated as DATA, not as a comment, because the
#: won-lost record below is indistinguishable at a glance from a backtest's.
NOT_MODEL_EVIDENCE = (
    "ACCOUNTING ONLY. Every wager here was placed by the owner and recommended "
    "by nothing in this repository. This record is evidence about a bankroll "
    "and is not evidence about the CFB model, which remains research-only."
)

SETTLED = "SETTLED"


@dataclass
class WagerSummary:
    """Counts and money. No opinion of any kind."""

    season: int
    wagers: int = 0
    contracts: float = 0.0
    staked: float = 0.0
    fees_paid: float = 0.0

    #: Settlement is a LATER observation than the wager, so most of these are
    #: legitimately unknown for a while. Unknown is counted, never defaulted.
    settled: int = 0
    unsettled: int = 0
    won: int = 0
    lost: int = 0
    other_result: int = 0

    #: Stated only where the ledger states it. A wager whose settlement is
    #: known but whose P&L is not contributes to `profit_loss_unestablished`
    #: rather than to a total that would then be quietly incomplete.
    realized_profit_loss: float = 0.0
    profit_loss_established: int = 0
    profit_loss_unestablished: int = 0

    markets: dict = field(default_factory=dict)

    @property
    def not_model_evidence(self) -> str:
        return NOT_MODEL_EVIDENCE

    @property
    def profit_loss_is_complete(self) -> bool:
        """True only when every wager's P&L is established.

        A total assembled from some of the wagers is not the season's P&L, and
        printing it beside the wager count invites reading it as one.
        """
        return self.wagers > 0 and self.profit_loss_unestablished == 0


def summarize(rows: list, season: int) -> WagerSummary:
    """One season's ledger, added up. Rows are raw dicts, as the store stores."""
    summary = WagerSummary(season=season)

    for row in rows:
        summary.wagers += 1
        summary.contracts += _number(row.get("contracts"))
        summary.staked += _number(row.get("stake"))
        summary.fees_paid += _number(row.get("fees_paid"))

        key = (row.get("market_ticker"), row.get("side"))
        summary.markets[key] = summary.markets.get(key, 0) + 1

        if row.get("settlement_status") == SETTLED:
            summary.settled += 1
            result = row.get("result")
            if result == "WON":
                summary.won += 1
            elif result == "LOST":
                summary.lost += 1
            else:
                summary.other_result += 1
        else:
            summary.unsettled += 1

        profit_loss = row.get("net_profit_loss")
        if isinstance(profit_loss, (int, float)) and not isinstance(profit_loss, bool):
            summary.realized_profit_loss += float(profit_loss)
            summary.profit_loss_established += 1
        else:
            summary.profit_loss_unestablished += 1

    return summary


def render(summary: WagerSummary) -> str:
    """The summary as text. The disclaimer is the first thing printed."""
    lines = [
        NOT_MODEL_EVIDENCE,
        "",
        f"CFB wager ledger -- season {summary.season}",
        f"  wagers recorded: {summary.wagers}",
        f"  contracts:       {summary.contracts:g}",
        f"  staked:          {summary.staked:.2f}",
        f"  fees paid:       {summary.fees_paid:.2f}",
        f"  distinct market/side pairs: {len(summary.markets)}",
        "",
        "  settlement:",
        f"    settled:   {summary.settled}",
        f"    unsettled: {summary.unsettled}",
    ]
    if summary.settled:
        lines += [
            f"    won:  {summary.won}",
            f"    lost: {summary.lost}",
            f"    other result: {summary.other_result}",
        ]
    lines += ["", "  realized profit and loss:"]
    if summary.profit_loss_is_complete:
        lines.append(f"    {summary.realized_profit_loss:+.2f} (every wager established)")
    else:
        # The partial total is deliberately NOT printed as the season's P&L.
        # A number that covers some of the wagers, printed beside the wager
        # count, will be read as covering all of them.
        lines += [
            f"    UNESTABLISHED for {summary.profit_loss_unestablished} of "
            f"{summary.wagers} wagers",
            "    no season total is stated: a total assembled from some of the "
            "wagers is not the season's profit and loss",
        ]
    return "\n".join(lines)


def _number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)
