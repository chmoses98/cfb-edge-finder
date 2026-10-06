"""Market prices cannot affect the football artifact. Proven two ways.

STRUCTURALLY: no football-side module of `cfb_edge_finder.scripting` can
import -- directly or through another scripting module -- anything that
carries a price (the catalog, the Kalshi client, the execution packet
builder, the retired model's packages). The football packet has no field a
price could travel in, and the catalog-to-request adapter drops every
non-identity key.

BEHAVIOURALLY: the same football packet run against radically different
Kalshi prices yields byte-identical matchup and script artifacts, and an
identical script/market compatibility map; only the price columns differ.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest
from execution_fakes import CAPTURED_AT, catalog_dir, standard_markets
from script_engine_fakes import availability, identity, synthetic_season

from cfb_edge_finder.execution.disposition import DispositionConfig
from cfb_edge_finder.execution.slate import build_packet, load_catalog
from cfb_edge_finder.scripting import FOOTBALL_MODULES, MARKET_MODULES
from cfb_edge_finder.scripting.expressions import annotate
from cfb_edge_finder.scripting.football import FootballPacket, build_content
from cfb_edge_finder.scripting.freeze import canonical_bytes, freeze
from cfb_edge_finder.scripting.market_map import MappingOrderError, map_game
from cfb_edge_finder.scripting.packets import GameRequest, request_from_catalog_entry

SCRIPTING = Path(__file__).resolve().parents[1] / "src" / "cfb_edge_finder" / "scripting"
FORBIDDEN_PREFIXES = (
    "cfb_edge_finder.catalog",
    "cfb_edge_finder.kalshi",
    "cfb_edge_finder.execution",
    "cfb_edge_finder.accounting",
    "cfb_edge_finder.decisions",
    "cfb_edge_finder.decision",
    "cfb_edge_finder.recommendation",
    "cfb_edge_finder.modeling",
    "cfb_edge_finder.projections",
    "cfb_edge_finder.sizing",
    "cfb_edge_finder.research",
    "cfb_edge_finder.scripting.market_map",
    "cfb_edge_finder.scripting.expressions",
    "cfb_edge_finder.scripting.ledger",
    "cfb_edge_finder.scripting.publish",
)
PRICE_WORDS = ("price", "ask", "bid", "odds", "line", "spread", "implied", "market", "probability", "ticker")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
    return names


def _closure(module: str, seen: set[str]) -> set[str]:
    """Every module reachable from `module` through scripting-internal imports."""
    if module in seen:
        return set()
    seen.add(module)
    path = SCRIPTING / f"{module}.py"
    found = _imports(path)
    for name in list(found):
        if name.startswith("cfb_edge_finder.scripting."):
            child = name.split(".")[2]
            if (SCRIPTING / f"{child}.py").exists():
                found |= _closure(child, seen)
    return found


@pytest.mark.parametrize("module", FOOTBALL_MODULES)
def test_football_modules_cannot_reach_a_price(module):
    reachable = _closure(module, set())
    offenders = sorted(n for n in reachable if n.startswith(FORBIDDEN_PREFIXES))
    assert offenders == [], f"{module} reaches {offenders}"


def test_every_scripting_module_is_classified_football_or_market():
    """A new module must be placed on one side of the line deliberately."""
    on_disk = {p.stem for p in SCRIPTING.glob("*.py")} - {"__init__"}
    assert on_disk == set(FOOTBALL_MODULES) | set(MARKET_MODULES)
    assert not set(FOOTBALL_MODULES) & set(MARKET_MODULES)


def test_the_football_packet_has_no_field_a_price_could_travel_in():
    for cls in (FootballPacket, GameRequest):
        for f in dataclasses.fields(cls):
            assert not any(word in f.name.lower() for word in PRICE_WORDS), (cls.__name__, f.name)


def test_the_catalog_adapter_keeps_identity_only():
    entry = {
        "game_key": "26OCT24F01F00",
        "title": "Team F01 at Team F00",
        "kickoff": "2026-10-25T00:00:00Z",
        "yes_ask": 0.91,
        "markets": [{"yes_ask": 0.2}],
        "market_reference": {"spread": -21.5},
        "family_distribution": {"game_spread": 30},
    }
    request = request_from_catalog_entry(entry, {"home": "Team F00", "away": "Team F01"})
    blob = json.dumps(dataclasses.asdict(request))
    assert "0.91" not in blob and "21.5" not in blob and "spread" not in blob


def _priced(markets: list[dict], transform) -> list[dict]:
    out = []
    for m in markets:
        m = dict(m)
        for side in ("yes", "no"):
            ask = m.get(f"{side}_ask")
            if ask is not None:
                new = transform(m["market_ticker"], side, ask)
                m[f"{side}_ask"] = new
                m[f"{side}_bid"] = round(max(0.01, new - 0.01), 2)
        out.append(m)
    return out


PRICE_WORLDS = {
    "as_listed": lambda t, s, a: a,
    "home_huge_favourite": lambda t, s, a: 0.97 if ("LSU" in t) == (s == "yes") else 0.04,
    "away_huge_favourite": lambda t, s, a: 0.03 if ("LSU" in t) == (s == "yes") else 0.98,
    "everything_a_coin_flip": lambda t, s, a: 0.5,
    "inverted": lambda t, s, a: round(1.0 - a, 2),
}


@pytest.fixture(scope="module")
def football_packet():
    rows = tuple(synthetic_season())
    return FootballPacket(
        identity=identity(),
        game_key="26SEP19LSUMISS",
        rows=rows,
        availability=availability(),
        identity_check={"status": "PASS", "orientation_swapped": False},
        freshness={"status": "FRESH"},
    )


def _run(tmp_path: Path, football_packet: FootballPacket, world: str):
    markets = _priced(standard_markets(), PRICE_WORLDS[world])
    directory = catalog_dir(tmp_path / world, markets)
    index, details = load_catalog(directory)
    entry = index["games"][0]
    captured = CAPTURED_AT
    market_packet = build_packet(
        entry, details[entry["game_key"]], DispositionConfig(as_of=captured, captured_at=captured), set(), "UTC"
    )
    # The football artifact is built WITHOUT the market packet in scope.
    content = build_content(football_packet)
    envelope = freeze(content, generated_at="2026-10-23T12:00:00Z")
    market_map = map_game(
        envelope, market_packet["contracts"], orientation_swapped=False, mapped_at="2026-10-23T12:00:00Z"
    )
    annotate(envelope["content"], market_map)
    # Non-vacuous: the contracts must actually be eligible and mapped.
    assert len(market_packet["contracts"]) >= 15, market_packet["exclusions"]
    assert market_map["coverage"]["expressions_mapped"] >= 20
    return envelope, market_map


def test_radically_different_prices_leave_the_football_artifact_byte_identical(tmp_path, football_packet):
    results = {world: _run(tmp_path, football_packet, world) for world in PRICE_WORLDS}
    reference_env, reference_map = results["as_listed"]
    assert reference_env["content"]["game_scripts"]["scripts"], "the fixture must produce scripts to be a real test"
    ref_bytes = canonical_bytes(reference_env["content"])
    for world, (envelope, market_map) in results.items():
        assert canonical_bytes(envelope["content"]) == ref_bytes, world
        assert envelope["artifact_hash"] == reference_env["artifact_hash"], world
        # Compatibility is a function of terms and scripts, never of price.
        compat = {e["expression_id"]: [c["status"] for c in e["compatibility"]] for e in market_map["expressions"]}
        ref = {e["expression_id"]: [c["status"] for c in e["compatibility"]] for e in reference_map["expressions"]}
        assert compat == ref, world
        survival = {e["expression_id"]: e["script_survival"] for e in market_map["expressions"]}
        assert survival == {e["expression_id"]: e["script_survival"] for e in reference_map["expressions"]}, world


def test_the_market_map_refuses_an_altered_artifact(football_packet):
    envelope = freeze(build_content(football_packet), generated_at="2026-10-23T12:00:00Z")
    tampered = json.loads(json.dumps(envelope))
    tampered["content"]["game_scripts"]["scripts"][0]["role"] = "DANGER"
    with pytest.raises(MappingOrderError):
        map_game(tampered, [], orientation_swapped=False, mapped_at="2026-10-23T12:00:00Z")


def test_the_market_map_cannot_precede_the_freeze(football_packet):
    envelope = freeze(build_content(football_packet), generated_at="2026-10-23T12:00:00Z")
    with pytest.raises(MappingOrderError):
        map_game(envelope, [], orientation_swapped=False, mapped_at="2026-10-23T11:59:59Z")


def test_annotation_never_writes_into_the_frozen_content(tmp_path, football_packet):
    envelope, _ = _run(tmp_path, football_packet, "home_huge_favourite")
    rebuilt = freeze(build_content(football_packet), generated_at="2026-10-23T12:00:00Z")
    assert envelope["artifact_hash"] == rebuilt["artifact_hash"]
    assert canonical_bytes(envelope["content"]) == canonical_bytes(rebuilt["content"])
