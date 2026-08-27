"""Mechanically proves GameDistribution/price_market() cannot be mistaken
for a validated betting engine (mission audit section 4): no recommendation,
staking, or real-money-eligibility surface exists anywhere in the packages
that could plausibly host one. This is deliberately a structural check, not
a docstring-reading exercise -- docstrings can drift, `dir()` cannot.

Note: `cfb_edge_finder.schemas` legitimately defines
`RecommendationReadiness` (WATCH/EARLY_VALUE/ACTIONABLE/PASS) as a
*vocabulary* for a future milestone (see schemas/common.py) -- that is a
closed enum with no behavior, not an implementation, so it is intentionally
excluded from this scan. What's checked here is the set of packages where
actual staking/execution *logic* would show up if someone added it.

`cfb_edge_finder.recommendation` (the disabled recommendation/risk
skeleton) is included deliberately: it is the package where a sizing or
execution surface would most plausibly appear, so it is held to the same
mechanical rule as the rest rather than being trusted because its
docstrings say it is disabled.
"""

from __future__ import annotations

import importlib
import pkgutil

import cfb_edge_finder.betting
import cfb_edge_finder.data
import cfb_edge_finder.expression
import cfb_edge_finder.ingestion
import cfb_edge_finder.kalshi
import cfb_edge_finder.modeling
import cfb_edge_finder.projections
import cfb_edge_finder.recommendation
import cfb_edge_finder.research
import cfb_edge_finder.teams

FORBIDDEN_SUBSTRINGS = (
    "stake",
    "bankroll",
    "kelly",
    "place_order",
    "place_bet",
    "execute_trade",
    "execute_order",
    "real_money",
    "tier_a",
    "tier_b",
    "tier_c",
    "qualification_bar",
)

# Mission spec Milestone B section 13: no projection/rating/pricing logic
# belongs in ingestion/teams/data yet either -- that's Milestone C+.
_FORBIDDEN_PROJECTION_SUBSTRINGS = (
    "epa",
    "sp_plus",
    "elo",
    "power_rating",
    "opponent_adjusted",
    "win_probability",
    "spread_probability",
    "score_distribution",
    "net_edge",
)

_SCANNED_PACKAGES = (
    cfb_edge_finder.betting,
    cfb_edge_finder.projections,
    cfb_edge_finder.research,
    cfb_edge_finder.modeling,
    cfb_edge_finder.kalshi,
    # The recommendation skeleton is the package most likely to grow a
    # staking or execution surface, so it is scanned by the same rule as
    # everything else rather than trusted to police itself.
    cfb_edge_finder.recommendation,
    cfb_edge_finder.expression,
)
_MILESTONE_B_PACKAGES = (cfb_edge_finder.ingestion, cfb_edge_finder.teams, cfb_edge_finder.data)


def _iter_public_names(package):
    yield package.__name__, [n for n in dir(package) if not n.startswith("_")]
    if hasattr(package, "__path__"):
        for _finder, name, _is_pkg in pkgutil.iter_modules(package.__path__, prefix=f"{package.__name__}."):
            module = importlib.import_module(name)
            yield name, [n for n in dir(module) if not n.startswith("_")]


def test_betting_package_has_no_public_surface_yet():
    # betting/__init__.py is documented as an intentional empty stub
    # (Milestones G-H). If this test ever fails, it means something was
    # added to betting/ -- which is fine, but it means this mission's
    # explicit "do not build a recommendation layer yet" boundary has been
    # crossed and that should be a conscious, reviewed decision, not a
    # silent one.
    public_names = [n for n in dir(cfb_edge_finder.betting) if not n.startswith("_")]
    assert public_names == [], f"betting package unexpectedly has public surface: {public_names}"


def test_no_staking_or_recommendation_execution_surface_in_scanned_packages():
    violations = []
    for package in _SCANNED_PACKAGES:
        for module_name, names in _iter_public_names(package):
            for name in names:
                lowered = name.lower()
                for forbidden in FORBIDDEN_SUBSTRINGS:
                    if forbidden in lowered:
                        violations.append(f"{module_name}.{name} (matched {forbidden!r})")
    assert violations == [], f"found staking/recommendation-execution surface: {violations}"


def test_no_projection_or_rating_logic_in_milestone_b_packages():
    # Milestone B (data/teams/ingestion) is schedule/team identity only --
    # this proves no EPA/SP+/ratings/probability-pricing logic leaked in.
    violations = []
    for package in _MILESTONE_B_PACKAGES:
        for module_name, names in _iter_public_names(package):
            for name in names:
                lowered = name.lower()
                for forbidden in _FORBIDDEN_PROJECTION_SUBSTRINGS:
                    if forbidden in lowered:
                        violations.append(f"{module_name}.{name} (matched {forbidden!r})")
    assert violations == [], f"found projection/rating surface in a Milestone B package: {violations}"
