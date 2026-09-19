"""The ChatGPT analysis artifact.

The workflow these tests defend is:

    repo -> <window>.analysis.json -> ChatGPT -> every good bet

ChatGPT never writes back. So the artifact is the ONLY thing standing
between the live Kalshi universe and the answer, and the properties below
are the ones that make it trustworthy: it contains every eligible
contract, it contains their prices, it contains no model opinion, and it
imposes no cap on what comes back.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from cfb_edge_finder.execution.analysis import (
    AnalysisCoverageError,
    analysis_document,
)
from cfb_edge_finder.execution.shards import build_shards, shard_analysis_document, write_shards
from cfb_edge_finder.execution.slate import build_slate
from tests.analysis_helpers import analysis_rows, analysis_tickers
from tests.execution_fakes import GAME_KEY, catalog_dir, market, standard_markets
from tests.test_execution_disposition import config

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "cfb_edge_finder"


def slate_and_shard(tmp_path, markets=None, **catalog_kwargs):
    directory = catalog_dir(tmp_path, markets, **catalog_kwargs)
    build = build_slate(directory, config(), slate_date=None)
    shards = build_shards(build.slate)
    return build.slate, shards[0]


def document(tmp_path, markets=None, **catalog_kwargs):
    slate, shard = slate_and_shard(tmp_path, markets, **catalog_kwargs)
    return shard_analysis_document(slate, shard)


# ------------------------------------------------- complete coverage


def test_every_eligible_contract_appears_in_the_analysis_artifact(tmp_path):
    slate, shard = slate_and_shard(tmp_path)
    artifact = shard_analysis_document(slate, shard)

    expected = {c["ticker"] for p in shard.packets for c in p["contracts"]}
    assert analysis_tickers(artifact) == expected
    assert artifact["reconciliation"]["eligible_contracts"] == len(expected)
    assert artifact["reconciliation"]["contracts_in_analysis_artifact"] == len(expected)
    assert artifact["reconciliation"]["unaccounted_contracts"] == 0


def test_the_artifact_refuses_to_build_if_a_contract_is_missing(tmp_path):
    """The invariant is enforced at build time, not asserted in a
    docstring: an artifact short of the eligible universe is invalid and
    must never reach a reader."""
    slate, shard = slate_and_shard(tmp_path)
    truncated = json.loads(json.dumps(shard.packets[0]))
    truncated["contracts"] = truncated["contracts"][:-1]
    # counts still claim the full population -- exactly the drift the
    # check exists to catch
    with pytest.raises(AnalysisCoverageError) as excinfo:
        analysis_document(slate, "early", "early", [truncated])
    assert "unaccounted" in str(excinfo.value)


def test_the_ticker_reconstruction_rule_the_artifact_states_actually_works(tmp_path):
    """`ticker_prefix + t` is an instruction to the reader. If it does not
    round-trip, every explicit ticker a reader quotes back is wrong."""
    artifact = document(tmp_path)
    for game in artifact["games"]:
        for block in game["markets"].values():
            if "ticker_prefix" in block:
                index = block["columns"].index("t")
                for row in block["rows"]:
                    assert (block["ticker_prefix"] + str(row[index])).startswith("KX")
            else:
                assert "ticker" in block["columns"]


def test_counts_agree_between_the_game_block_and_its_market_blocks(tmp_path):
    artifact = document(tmp_path)
    for game in artifact["games"]:
        rows = sum(len(b["rows"]) for b in game["markets"].values())
        stated = sum(b["contracts"] for b in game["markets"].values())
        assert rows == stated == game["eligible_contracts"]


# ------------------------------------------------------------ prices


def test_every_executable_contract_carries_its_prices(tmp_path):
    artifact = document(tmp_path)
    rows = analysis_rows(artifact)
    assert rows
    for row in rows:
        # at least one side must be buyable -- that is what made it eligible
        assert row["yes_entry"] is not None or row["no_entry"] is not None
        for side in ("yes", "no"):
            if row[f"{side}_entry"] is not None:
                assert 0.0 < row[f"{side}_entry"] < 1.0
                assert row[f"{side}_fee"] is not None
                assert row[f"{side}_breakeven"] is not None
                assert row[f"{side}_breakeven"] >= row[f"{side}_entry"]


def test_both_bids_and_asks_are_published(tmp_path):
    """The NO ask is quoted independently and is NOT 1 - yes_ask. Both
    sides' books are published so a reader never has to derive the one
    that would put them on the wrong side of the spread."""
    artifact = document(tmp_path)
    for row in analysis_rows(artifact):
        for column in ("yes_bid", "yes_ask", "no_bid", "no_ask"):
            assert column in row


def test_the_price_conventions_are_stated_once_not_per_contract(tmp_path):
    artifact = document(tmp_path)
    assert artifact["price_conventions"]["expected_value"]
    assert "no_entry_is_not_one_minus_yes_entry" in artifact["price_conventions"]
    encoded = json.dumps(artifact)
    assert encoded.count("you pay yes_entry") == 1


def test_a_family_with_one_quote_age_states_it_once(tmp_path):
    """Compaction that is lossless: the value is still available for every
    contract, it is simply not repeated 800 times."""
    artifact = document(tmp_path)
    blocks = [b for g in artifact["games"] for b in g["markets"].values()]
    assert any("quote_age_s" in b for b in blocks)
    for row in analysis_rows(artifact):
        assert "quote_age_s" in row


# -------------------------------------------- no model, no pre-filter


FORBIDDEN_FIELDS = (
    "fair_probability",
    "fair_value",
    "model_probability",
    "win_probability",
    "projected_margin",
    "projected_total",
    "projected_score",
    "expected_score",
    "power_rating",
    "edge",
    "net_edge",
    "recommendation",
    "recommended",
    "rank",
    "score",
    "confidence_grade",
)


def _keys(payload, found=None):
    found = found if found is not None else set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key))
            _keys(value, found)
    elif isinstance(payload, list):
        for item in payload:
            _keys(item, found)
    return found


def test_no_repo_model_field_leaks_into_the_analysis_artifact(tmp_path):
    artifact = document(tmp_path)
    keys = _keys(artifact)
    offenders = sorted(k for k in keys if k.lower() in FORBIDDEN_FIELDS)
    assert offenders == [], f"model/recommendation fields in the ChatGPT artifact: {offenders}"
    assert artifact["model_projections"] == "absent"


def test_the_artifact_building_path_cannot_reach_the_projection_model():
    """Structural, not a promise. The modules that build what ChatGPT
    reads must not be able to import a model even by accident.

    `evaluator.py` is deliberately NOT in this list: it is the optional
    repo-side arithmetic check, it is not on the live path, and it does
    use the covariance algebra in `projections/` on distributions a human
    supplied."""
    artifact_path_modules = (
        "analysis.py",
        "slate.py",
        "semantics.py",
        "disposition.py",
        "shards.py",
        "windows.py",
    )
    forbidden = (
        "cfb_edge_finder.modeling",
        "cfb_edge_finder.projections",
        "cfb_edge_finder.recommendation",
        "cfb_edge_finder.decision",
        "cfb_edge_finder.sizing",
        "cfb_edge_finder.ratings",
    )
    for name in artifact_path_modules:
        tree = ast.parse((SRC / "execution" / name).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for module in imported:
            for banned in forbidden:
                assert not module.startswith(banned), f"{name} imports {module}"


def test_unusual_families_are_present_not_filtered(tmp_path):
    """A family nobody has modelled must reach ChatGPT with its prices,
    because ChatGPT is the thing that decides whether it is worth a bet."""
    markets = standard_markets()
    markets.append(
        market(
            f"KXNCAAFBRANDNEW-{GAME_KEY}-Y",
            family="a_family_invented_tomorrow",
            title="Something nobody has modelled",
            yes_ask=0.4,
            no_ask=0.62,
        )
    )
    artifact = document(tmp_path, markets)
    families = {f for g in artifact["games"] for f in g["markets"]}
    assert "a_family_invented_tomorrow" in families
    row = next(r for r in analysis_rows(artifact) if "BRANDNEW" in r["ticker"])
    assert row["yes_entry"] == 0.4
    assert row["no_entry"] == 0.62
    assert row["yes_means"]


def test_the_uncommon_families_in_the_fixture_all_survive(tmp_path):
    artifact = document(tmp_path)
    families = {f for g in artifact["games"] for f in g["markets"]}
    for family in ("first_half_moneyline", "touchdown_scorer", "unknown"):
        assert family in families, family


def test_nothing_is_excluded_for_being_unattractive(tmp_path):
    """Every contract in the catalog reaches the artifact unless it hit an
    objective mechanical exclusion, and the artifact publishes which."""
    slate, shard = slate_and_shard(tmp_path)
    artifact = shard_analysis_document(slate, shard)
    assert artifact["reconciliation"]["mechanical_exclusions"] == 0
    assert artifact["reconciliation"]["contracts_discovered"] == len(standard_markets())
    assert artifact["reconciliation"]["contracts_in_analysis_artifact"] == len(standard_markets())


def test_mechanical_exclusions_stay_counted_and_named(tmp_path):
    markets = standard_markets()
    markets.append(
        market(
            f"KXNCAAFTOTAL-{GAME_KEY}-99",
            family="game_total",
            title="Over 98.5 points scored",
            floor_strike=98.5,
            status="closed",
        )
    )
    artifact = document(tmp_path, markets)
    reconciliation = artifact["reconciliation"]
    assert reconciliation["mechanical_exclusions"] == 1
    assert reconciliation["exclusions_by_status"] == {"market_closed": 1}
    assert (
        reconciliation["contracts_discovered"]
        == reconciliation["eligible_contracts"] + reconciliation["mechanical_exclusions"]
    )


# ------------------------------------------------------- two stages


def test_game_context_precedes_markets_in_key_order(tmp_path):
    """A JSON object's order is what a reader meets first. Handicap from
    the facts, then look at the prices."""
    slate, shard = slate_and_shard(tmp_path)
    write_shards(slate, [shard], tmp_path / "out")
    raw = (tmp_path / "out" / "shards" / f"{shard.name}.analysis.json").read_text()
    for game in json.loads(raw)["games"]:
        keys = list(game.keys())
        assert keys.index("game_context") < keys.index("markets")
    assert raw.index('"how_to_use"') < raw.index('"games"')


def test_game_context_carries_the_facts_the_repo_actually_has(tmp_path):
    artifact = document(tmp_path)
    context = artifact["games"][0]["game_context"]
    for key in (
        "home_team",
        "away_team",
        "home_away_basis",
        "kickoff_utc",
        "kickoff_local",
        "division",
        "conference_game",
        "season_week",
        "exchange_tier",
    ):
        assert key in context, key
    assert context["home_team"] == "Ole Miss"
    assert context["away_team"] == "LSU"


def test_the_artifact_says_which_facts_it_does_not_carry(tmp_path):
    """Silently thin context invites a reader to assume the artifact
    already accounted for injuries and weather. Naming the gaps is what
    stops that."""
    artifact = document(tmp_path)
    missing = artifact["factual_data_not_in_artifact"]
    for expected in ("injuries and availability", "weather", "team records"):
        assert expected in missing


def test_conference_is_three_state_not_two(tmp_path):
    """Kalshi says False for a non-conference game. Absent is a different
    statement and must not be collapsed into it."""
    artifact = document(tmp_path)
    context = artifact["games"][0]["game_context"]
    assert context["conference_game"] is True
    assert context["conference"] == "SEC"


# ---------------------------------------------------------- no cap


def test_the_artifact_asks_for_every_qualifying_bet_and_caps_nothing(tmp_path):
    artifact = document(tmp_path)
    instructions = " ".join(artifact["how_to_use"] + artifact["do_not"]).lower()
    assert "there is no cap" in instructions
    assert "return all 100" in instructions
    assert "do not cap" in instructions
    for banned in ("top 6", "top six", "best 6", "shortlist"):
        assert banned not in instructions, banned


def test_the_artifact_never_asks_for_a_write_back(tmp_path):
    """ChatGPT cannot be assumed to write into this repository, so the
    artifact must never ask it to. The instruction prose is scanned
    separately because the one place these words legitimately appear is
    the sentence forbidding the behaviour."""
    artifact = document(tmp_path)
    prose = " ".join(artifact["how_to_use"] + artifact["do_not"]).lower()
    assert "do not attempt to save, commit or return a file" in prose
    for banned in ("commit the", "push the", "open a pull request", "write the handicap"):
        assert banned not in prose, banned

    body = json.dumps({k: v for k, v in artifact.items() if k not in ("how_to_use", "do_not")})
    for banned in ("commit", "pull request", "write back", "handicap payload", "github"):
        assert banned not in body.lower(), banned


# ------------------------------------------- the documented workflow

DOCS = REPO / "docs" / "LIVE_EXECUTION.md"
README = REPO / "README.md"
WORKFLOW = REPO / ".github" / "workflows" / "cfb-execution-slate.yml"


def test_the_documentation_never_claims_chatgpt_writes_to_github():
    """ChatGPT cannot be assumed to write into this repository. Any
    documented step that needs it to is a workflow the user cannot
    actually run."""
    for path in (DOCS, README):
        text = _flat(path).lower()
        # Phrased as ASSERTIONS of a write-back. "ChatGPT writes nothing
        # back" is the sentence this test exists to protect, so a bare
        # "chatgpt writes" would flag the promise as the violation.
        for banned in (
            "chatgpt commits",
            "chatgpt writes a",
            "chatgpt writes the",
            "chatgpt pushes",
            "chatgpt updates the repo",
            "handicaps_early.json",
            "written back into github",
            "write the handicap file",
            "have chatgpt commit",
        ):
            assert banned not in text, f"{path.name}: {banned}"


def _flat(path: Path) -> str:
    """Markdown hard-wraps at 72 columns, so a sentence this repository
    cares about is routinely split across two lines. Comparing on
    normalised whitespace tests the prose, not the wrapping."""
    return " ".join(path.read_text(encoding="utf-8").split())


def test_the_documented_live_workflow_needs_no_write_back():
    text = _flat(DOCS)
    assert "repo -> <window>.analysis.json -> ChatGPT -> every good bet" in text
    assert "**ChatGPT writes nothing back**" in text
    assert "not required to get bets" in text


def test_the_documented_workflow_imposes_no_bet_cap():
    """`--top` may exist for reading a long list on a terminal. It must
    not appear as a step anyone is told to run."""
    docs = _flat(DOCS)
    readme = _flat(README).lower()
    assert "There is no cap and no target number" in docs
    assert "no bet cap" in readme
    assert "**There is no bet cap.**" in docs
    assert "display truncation for debugging" in docs

    # the live-workflow section must not hand anyone a --top command
    workflow_section = docs[docs.index("## The live workflow") : docs.index("### The optional")]
    assert "--top" not in workflow_section


def test_the_documented_upload_prompt_matches_the_one_the_cli_prints():
    from cfb_edge_finder.execution.cli import CHATGPT_PROMPT

    docs = _flat(DOCS)
    for fragment in (
        "Run CFB. Bankroll $1,400.",
        "return every bet",
        "Do not use repo projections.",
    ):
        assert fragment in CHATGPT_PROMPT, fragment
        assert fragment in docs, fragment


def test_the_action_uploads_the_analysis_artifacts():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "*.analysis.json" in text
    assert "brief" not in text.lower()
    assert "contracts_in_analysis_artifact" in text
