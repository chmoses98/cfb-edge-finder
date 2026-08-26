"""Mission sections 24-25: mechanically proves the future qualification
interface can never produce BET/PLAY/STAKE/tier/order output, and that no
execution/order-placement surface exists anywhere Milestone E touches.
Mirrors tests/test_no_recommendation_surface.py's structural-scan
approach, extended to the new research/schemas/qualification modules and
to the research/reporting package this milestone adds."""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import cfb_edge_finder.research
from cfb_edge_finder.schemas.qualification import QualificationRecord, QualificationStatus

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
    "bet_up_to",
)


def _iter_public_names(package):
    yield package.__name__, [n for n in dir(package) if not n.startswith("_")]
    if hasattr(package, "__path__"):
        for _finder, name, _is_pkg in pkgutil.iter_modules(package.__path__, prefix=f"{package.__name__}."):
            module = importlib.import_module(name)
            yield name, [n for n in dir(module) if not n.startswith("_")]


def test_qualification_status_is_a_closed_two_member_enum():
    assert set(QualificationStatus) == {QualificationStatus.RESEARCH_ONLY, QualificationStatus.QUALIFICATION_DISABLED}


def test_qualification_status_has_no_actionable_member():
    forbidden_names = {"BET", "PLAY", "ACTIONABLE", "WATCH", "EARLY_VALUE", "TIER_A", "TIER_B", "TIER_C"}
    assert forbidden_names.isdisjoint({m.name for m in QualificationStatus})


def test_default_qualification_record_is_disabled():
    from cfb_edge_finder.research.qualification import default_disabled_record

    record = default_disabled_record()
    assert record.status == QualificationStatus.QUALIFICATION_DISABLED


def test_qualification_record_rejects_actionable_language_in_free_text():
    with pytest.raises(ValueError, match="forbidden substring"):
        QualificationRecord(risk_group="bet_up_to_50")


def test_qualification_record_rejects_tier_language():
    with pytest.raises(ValueError):
        QualificationRecord(risk_group="tier_a")


def test_no_staking_or_execution_surface_in_research_package():
    violations = []
    for module_name, names in _iter_public_names(cfb_edge_finder.research):
        for name in names:
            lowered = name.lower()
            for forbidden in FORBIDDEN_SUBSTRINGS:
                if forbidden in lowered:
                    violations.append(f"{module_name}.{name} (matched {forbidden!r})")
    assert violations == [], f"found staking/recommendation-execution surface: {violations}"


def test_no_staking_or_execution_surface_in_schemas_package():
    import cfb_edge_finder.schemas

    violations = []
    for module_name, names in _iter_public_names(cfb_edge_finder.schemas):
        for name in names:
            lowered = name.lower()
            for forbidden in FORBIDDEN_SUBSTRINGS:
                if forbidden in lowered:
                    violations.append(f"{module_name}.{name} (matched {forbidden!r})")
    assert violations == [], f"found staking/recommendation-execution surface: {violations}"


def test_no_kalshi_order_placement_client_exists():
    import cfb_edge_finder.data.kalshi_client as kalshi_client_module

    public_names = [n.lower() for n in dir(kalshi_client_module) if not n.startswith("_")]
    order_related = ("place_order", "create_order", "submit_order", "cancel_order", "place_trade")
    violations = [n for n in public_names for o in order_related if o in n]
    assert violations == [], f"found order-placement surface in kalshi_client: {violations}"


def test_no_kalshi_trading_credentials_in_settings():
    from cfb_edge_finder.config import Settings

    field_names = {f.lower() for f in Settings.__dataclass_fields__}
    # kalshi_api_key_id / kalshi_private_key_path already exist for
    # READ-ONLY market access (see kalshi_client.py, unauthenticated per
    # its own design) -- this test guards against a future trading-secret
    # field (e.g. a private key used for signed order requests) being
    # added without a conscious, reviewed decision.
    forbidden_field_substrings = ("order_secret", "trading_secret", "execution_key")
    violations = [f for f in field_names for s in forbidden_field_substrings if s in f]
    assert violations == []
