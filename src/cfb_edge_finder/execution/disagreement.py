"""How far the handicap is from the market, measured and never obeyed.

*** THE MARKET IS NOT THE ANSWER KEY ***
The whole point of the exercise is to find places the market is wrong, so a
check that penalised disagreement would penalise the only thing worth finding.
Nothing here invalidates a handicap, downgrades an edge, or removes a
candidate.

*** AND YET ***
A handicap that disagrees with the market on ONE rung of ONE ladder is a
trade. A handicap that disagrees with it on every rung of every ladder in the
game, in the same direction, is usually one of four things and only the last
is a bet:

  * a home/away flip (every spread inverted at once);
  * a period mix-up (a full-game distribution answering first-half markets);
  * a units error (a total stated for one half against full-game lines);
  * a genuine, very large disagreement.

The first three produce enormous, uniform, one-directional disagreement and
they are the three failure modes that survive every other check in this
repository -- a flipped game reconciles perfectly, prices every contract, and
balances every count. So the disagreement is MEASURED, per game, and published
next to the candidates. An operator who sees `extreme` and agrees with it bets
it; an operator who sees `extreme` on a game they thought was a coin flip has
just been handed the only warning the system can give.

*** THE MEASUREMENT ***
Over the game's priced contracts, the mean of |fair - implied| and the mean of
the SIGNED (fair - implied). The signed mean is the one that catches a flip:
honest disagreement on a ladder is signed-consistent within one direction and
roughly cancels across the two sides of a total, while an inverted game pushes
every contract the same way.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Mean absolute (fair - implied) over a game's priced contracts, in
#: probability units.
#:
#: NOT CALIBRATED AGAINST ANYTHING. These are boundaries on a diagnostic, not
#: a claim about how often a handicap this far from the market is right. They
#: are set where a mechanical error becomes more likely than a football
#: opinion: a mean absolute disagreement above 12 points of probability across
#: a whole game's ladders is roughly a two-touchdown difference of view on
#: every rung simultaneously, which a handicapper rarely holds and a flipped
#: home/away label produces every time.
ELEVATED_MEAN_ABS = 0.06
EXTREME_MEAN_ABS = 0.12

#: The signed test, which is the flip detector. A game whose contracts ALL
#: lean the same way by this much is reported extreme even if the absolute
#: mean is modest.
EXTREME_MEAN_SIGNED = 0.10

#: Below this many priced contracts a game's mean is noise. Reported as
#: `insufficient_sample` rather than as `normal`, because "we could not tell"
#: and "we checked and it was fine" are different answers.
MIN_CONTRACTS_FOR_A_VERDICT = 8


class Disagreement(StrEnum):
    NORMAL = "normal"
    ELEVATED = "elevated"
    EXTREME = "extreme"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


@dataclass(frozen=True)
class GameDisagreement:
    game_key: str
    contracts: int
    mean_absolute: float | None
    mean_signed: float | None
    level: str
    reason: str

    @property
    def needs_extra_review(self) -> bool:
        return self.level == Disagreement.EXTREME.value

    def as_dict(self) -> dict[str, Any]:
        return {
            "game_key": self.game_key,
            "priced_contracts": self.contracts,
            "mean_absolute_disagreement": (
                None if self.mean_absolute is None else round(self.mean_absolute, 6)
            ),
            "mean_signed_disagreement": (
                None if self.mean_signed is None else round(self.mean_signed, 6)
            ),
            "level": self.level,
            "reason": self.reason,
            "note": (
                "A diagnostic, not a veto. The market is not treated as ground truth anywhere in "
                "this repository, and an extreme reading is a call for a second look at the "
                "handicap's inputs -- most often home/away orientation or period -- not a reason "
                "to discard it."
            ),
        }


def measure(game_key: str, rows: list[dict[str, Any]]) -> GameDisagreement:
    """One game's disagreement, from its priced evaluation rows.

    Uses the YES axis for every contract so the signs are comparable. Reading
    each row on its own best side would make a game of one-sided disagreement
    look balanced, which is the exact failure this is meant to catch.
    """
    deltas: list[float] = []
    for row in rows:
        fair_yes = row.get("fair_probability_yes")
        yes_entry = row.get("yes_entry")
        if fair_yes is None or yes_entry is None:
            continue
        deltas.append(float(fair_yes) - float(yes_entry))

    if len(deltas) < MIN_CONTRACTS_FOR_A_VERDICT:
        return GameDisagreement(
            game_key=game_key,
            contracts=len(deltas),
            mean_absolute=None,
            mean_signed=None,
            level=Disagreement.INSUFFICIENT_SAMPLE.value,
            reason=(
                f"{len(deltas)} priced contract(s) with both a fair probability and a YES ask; "
                f"below the {MIN_CONTRACTS_FOR_A_VERDICT} needed for a meaningful mean"
            ),
        )

    mean_abs = sum(abs(d) for d in deltas) / len(deltas)
    mean_signed = sum(deltas) / len(deltas)

    if abs(mean_signed) >= EXTREME_MEAN_SIGNED:
        return GameDisagreement(
            game_key=game_key,
            contracts=len(deltas),
            mean_absolute=mean_abs,
            mean_signed=mean_signed,
            level=Disagreement.EXTREME.value,
            reason=(
                f"every priced contract leans the same way by {mean_signed:+.3f} on average. A "
                "uniform one-directional gap across a whole game is what a home/away flip, a "
                "period mix-up or a units error looks like -- check those three before betting it"
            ),
        )
    if mean_abs >= EXTREME_MEAN_ABS:
        return GameDisagreement(
            game_key=game_key,
            contracts=len(deltas),
            mean_absolute=mean_abs,
            mean_signed=mean_signed,
            level=Disagreement.EXTREME.value,
            reason=(
                f"mean absolute disagreement {mean_abs:.3f} across {len(deltas)} contracts. That "
                "is a very large standing difference of view; it may be right, and it deserves a "
                "second look at the handicap's inputs first"
            ),
        )
    if mean_abs >= ELEVATED_MEAN_ABS:
        return GameDisagreement(
            game_key=game_key,
            contracts=len(deltas),
            mean_absolute=mean_abs,
            mean_signed=mean_signed,
            level=Disagreement.ELEVATED.value,
            reason=f"mean absolute disagreement {mean_abs:.3f} across {len(deltas)} contracts",
        )
    return GameDisagreement(
        game_key=game_key,
        contracts=len(deltas),
        mean_absolute=mean_abs,
        mean_signed=mean_signed,
        level=Disagreement.NORMAL.value,
        reason=f"mean absolute disagreement {mean_abs:.3f} across {len(deltas)} contracts",
    )
