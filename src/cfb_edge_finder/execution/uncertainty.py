"""How wrong the handicap is allowed to be, stated by the handicapper.

*** THE PROBLEM THIS EXISTS TO STOP ***
A single projected score, propagated through closed-form algebra, produces a
fair probability for several hundred contracts in one game. Every one of those
numbers inherits the projection's error, and none of them show it. "LSU 27.5,
Ole Miss 24.0" turns into "fair 0.5814" on a ladder rung, and 0.5814 against a
0.55 ask reads as a four-point edge whether the margin estimate was worth
plus-or-minus half a point or plus-or-minus a touchdown.

So the handicap does not supply a point. It supplies a point AND A REGION, and
every fair value this repository publishes is accompanied by what happens to it
across that region.

*** THE REGION IS A BOX, AND THE BOX IS THE HANDICAPPER'S ***
Four independent axes, each one a thing a handicapper can actually reason about
and none of them inferred on their behalf:

    margin_points      how many points the expected MARGIN could be wrong by
    total_points       how many points the expected TOTAL could be wrong by
    sd_scale_low/high  how much wider or narrower the real spread of outcomes
                       could be than the stated standard deviations
    correlation_delta  how much the two teams' scores could co-move differently
                       than stated

Margin and total are perturbed separately and orthogonally because they are
separate football opinions: being wrong about who is better is not the same
mistake as being wrong about how many points get scored, and a system that
moved both together would understate one whole class of error. A margin shift
of `d` moves the home mean by +d/2 and the away mean by -d/2, which leaves the
total untouched; a total shift of `t` moves both by +t/2, which leaves the
margin untouched.

*** WHY CORNERS, AND WHERE THAT IS EXACT ***
The evaluator needs the WORST fair value in the region, not a sample of it. For
the families that carry almost every contract -- moneyline, spread, alternate
spread, total, alternate total, team total -- the fair probability is monotone
in each axis separately:

  * P(margin >= k) is monotone increasing in the margin mean, for any k;
  * P(total >= k) is monotone increasing in the total mean, for any k;
  * P(X >= k) is monotone in sigma on each side of k (increasing when k is
    above the mean, decreasing when it is below) -- monotone either way, so the
    extreme over an INTERVAL of sigma is always at one of its endpoints;
  * margin variance is monotone in the correlation, so the same argument holds.

A function monotone in each coordinate attains its extremes over a box at the
box's corners. Enumerating the 2^k corners therefore gives the EXACT minimum
and maximum over the region, not an approximation of it -- and that claim is
published per contract as `sensitivity_bound: exact_corner_extremum`.

It is not universally true. A winning-margin BAND is a difference of two tail
probabilities and is not monotone in the mean, and an explicit probability the
handicapper typed has no analytic structure at all. Those are labelled
`grid_extremum` and `stated_range` respectively, and the label is the honest
part: a bound the method cannot support is named rather than implied.

*** AN ABSENT REGION IS NOT A ZERO REGION ***
`HandicapUncertainty.absent()` is a distinct object, not a box of width zero.
A box of width zero would make every base-case edge survive its own
sensitivity analysis unchanged, and a point estimate would come back labelled
ROBUST -- which is precisely the failure this module was written to prevent.
The evaluator refuses to award robustness to a contract whose region is absent,
and `stated` is the flag it reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any

#: A margin or total perturbation past this is not a stated uncertainty, it is
#: a different handicap. Refused rather than clamped: silently shrinking a
#: number the handicapper typed would publish a region nobody chose.
MAX_POINT_DELTA = 28.0

#: Standard deviations may be scaled within this range. A scale below 0.25 or
#: above 4.0 describes a distribution so different from the stated one that
#: calling it the same handicap is a fiction.
MIN_SD_SCALE = 0.25
MAX_SD_SCALE = 4.0


class UncertaintyValidationError(ValueError):
    """The stated region cannot be used as written."""


@dataclass(frozen=True)
class HandicapUncertainty:
    """The region around one period's score distribution.

    `stated` is what separates "the handicapper said the margin is good to
    within three points" from "the handicapper said nothing". Both produce a
    region -- the second a degenerate one -- and only the first can support a
    robustness claim.
    """

    margin_points: float = 0.0
    total_points: float = 0.0
    sd_scale_low: float = 1.0
    sd_scale_high: float = 1.0
    correlation_delta: float = 0.0
    stated: bool = False

    @classmethod
    def absent(cls) -> HandicapUncertainty:
        return cls()

    @property
    def is_degenerate(self) -> bool:
        """True when the region is a single point, however it got that way.

        A handicapper who states `margin_points: 0` with no other axis has
        written down a point estimate in the uncertainty schema's clothing, and
        it must not buy a robustness claim that a bare point estimate cannot.
        """
        return (
            self.margin_points == 0.0
            and self.total_points == 0.0
            and self.sd_scale_low == 1.0
            and self.sd_scale_high == 1.0
            and self.correlation_delta == 0.0
        )

    @property
    def supports_robustness(self) -> bool:
        return self.stated and not self.is_degenerate

    def as_dict(self) -> dict[str, Any]:
        return {
            "stated": self.stated,
            "margin_points": self.margin_points,
            "total_points": self.total_points,
            "sd_scale_low": self.sd_scale_low,
            "sd_scale_high": self.sd_scale_high,
            "correlation_delta": self.correlation_delta,
        }

    @classmethod
    def parse(cls, period: str, payload: Any) -> HandicapUncertainty:
        if payload is None:
            return cls.absent()
        if not isinstance(payload, dict):
            raise UncertaintyValidationError(
                f"period {period!r}: uncertainty must be an object, got {type(payload).__name__}"
            )
        if not payload:
            return cls.absent()

        def number(key: str, default: float) -> float:
            value = payload.get(key)
            if value is None or value == "":
                return default
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise UncertaintyValidationError(
                    f"period {period!r}: uncertainty.{key} is not numeric"
                ) from exc

        margin = number("margin_points", 0.0)
        total = number("total_points", 0.0)
        sd_low = number("sd_scale_low", 1.0)
        sd_high = number("sd_scale_high", 1.0)
        correlation = number("correlation_delta", 0.0)

        for name, value in (("margin_points", margin), ("total_points", total)):
            if value < 0:
                raise UncertaintyValidationError(
                    f"period {period!r}: uncertainty.{name} is {value}; a half-width cannot be negative"
                )
            if value > MAX_POINT_DELTA:
                raise UncertaintyValidationError(
                    f"period {period!r}: uncertainty.{name} is {value}, past the {MAX_POINT_DELTA} "
                    "point ceiling. A band that wide is a different handicap, not an error bar"
                )
        for name, value in (("sd_scale_low", sd_low), ("sd_scale_high", sd_high)):
            if not MIN_SD_SCALE <= value <= MAX_SD_SCALE:
                raise UncertaintyValidationError(
                    f"period {period!r}: uncertainty.{name} is {value}, outside "
                    f"[{MIN_SD_SCALE}, {MAX_SD_SCALE}]"
                )
        if sd_low > sd_high:
            raise UncertaintyValidationError(
                f"period {period!r}: uncertainty.sd_scale_low ({sd_low}) is above sd_scale_high "
                f"({sd_high}); the two are the ends of one interval"
            )
        if correlation < 0:
            raise UncertaintyValidationError(
                f"period {period!r}: uncertainty.correlation_delta is {correlation}; "
                "a half-width cannot be negative"
            )
        if correlation > 0.9:
            raise UncertaintyValidationError(
                f"period {period!r}: uncertainty.correlation_delta is {correlation}, past 0.9"
            )

        return cls(
            margin_points=margin,
            total_points=total,
            sd_scale_low=sd_low,
            sd_scale_high=sd_high,
            correlation_delta=correlation,
            stated=True,
        )


@dataclass(frozen=True)
class Scenario:
    """One corner of the region, named so a report can say which corner bit.

    `label` is deterministic and readable: `margin-,total+,sd_high` names the
    corner where the margin was overestimated, the total underestimated and the
    game more volatile than stated.
    """

    label: str
    margin_shift: float
    total_shift: float
    sd_scale: float
    correlation_shift: float

    @property
    def is_base(self) -> bool:
        return (
            self.margin_shift == 0.0
            and self.total_shift == 0.0
            and self.sd_scale == 1.0
            and self.correlation_shift == 0.0
        )


BASE_SCENARIO = Scenario("base", 0.0, 0.0, 1.0, 0.0)


def scenarios(uncertainty: HandicapUncertainty) -> tuple[Scenario, ...]:
    """The base case, then every corner of the stated region.

    The base case is ALWAYS first and always present, because the base fair
    value is what a reader compares everything else against. An absent or
    degenerate region yields exactly one scenario, and the caller can tell the
    difference between "tested and survived" and "there was nothing to test"
    by the length of this tuple.
    """
    if not uncertainty.supports_robustness:
        return (BASE_SCENARIO,)

    margin_axis = (
        [(-uncertainty.margin_points, "margin-"), (uncertainty.margin_points, "margin+")]
        if uncertainty.margin_points
        else [(0.0, "")]
    )
    total_axis = (
        [(-uncertainty.total_points, "total-"), (uncertainty.total_points, "total+")]
        if uncertainty.total_points
        else [(0.0, "")]
    )
    sd_axis = (
        [(uncertainty.sd_scale_low, "sd_low"), (uncertainty.sd_scale_high, "sd_high")]
        if (uncertainty.sd_scale_low != 1.0 or uncertainty.sd_scale_high != 1.0)
        else [(1.0, "")]
    )
    rho_axis = (
        [(-uncertainty.correlation_delta, "rho-"), (uncertainty.correlation_delta, "rho+")]
        if uncertainty.correlation_delta
        else [(0.0, "")]
    )

    out: list[Scenario] = [BASE_SCENARIO]
    for (m, ml), (t, tl), (s, sl), (r, rl) in product(margin_axis, total_axis, sd_axis, rho_axis):
        label = ",".join(part for part in (ml, tl, sl, rl) if part)
        out.append(Scenario(label or "base", m, t, s, r))
    # A corner that coincides with the base case (every axis silent) would be
    # a duplicate; dedupe on the label so the scenario count is the number of
    # DISTINCT things tested rather than an artefact of the axis product.
    seen: set[str] = set()
    unique: list[Scenario] = []
    for scenario in out:
        if scenario.label in seen:
            continue
        seen.add(scenario.label)
        unique.append(scenario)
    return tuple(unique)
