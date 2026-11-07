"""Market-first audit (research_packet/market_audit.py + scripts/audit_cfb_market_evaluation.py).

The properties under test: every listed contract and BOTH sides are reconciled; merely listing a contract never
counts as evaluating it; missing / duplicated contracts are detected; an incomplete evaluation can never be reported as
complete; no probability, bet-up-to or approval is invented; and the audit changes nothing outside research.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from cfb_edge_finder.research_packet import market_audit as ma

REPO = Path(__file__).resolve().parents[1]
GAME = "26NOV07AAABBB"


def _side(ask, size, all_in, exec_=True):
    return {
        "ask": ask,
        "ask_size": size,
        "all_in": all_in,
        "exec": exec_,
        "contracts_1u": 100,
        "order_fee_1u": 1.0,
        "bid": 0.1,
    }


def _contract(i, family="game_total", ref=True, yes=None, no=None):
    c = {
        "ticker": f"KXNCAAFTOTAL-{GAME}-{40 + i}",
        "family": family,
        "kind": "total_over",
        "line": 40.5 + i,
        "book": "two_sided",
        "upd": "2026-11-07T00:00:00Z",
        "yes_means": f"Over {40.5 + i} points",
        "yes": yes or _side(0.40, 500.0, 0.41),
        "no": no or _side(0.62, 300.0, 0.63),
    }
    if ref:
        c["ref_freq"] = {"n": 120, "yes": 0.45}
    return c


@pytest.fixture
def root(tmp_path):
    def build(contracts, *, declared=None, parts=1, skip_part=False):
        gdir = tmp_path / "data/research_packets/games" / GAME
        gdir.mkdir(parents=True, exist_ok=True)
        files = []
        chunks = [contracts[i::parts] for i in range(parts)]
        for n, chunk in enumerate(chunks):
            name = "markets.json" if n == 0 else f"markets.part{n + 1:02d}of{parts:02d}.json"
            rel = f"data/research_packets/games/{GAME}/{name}"
            files.append(rel)
            if skip_part and n == parts - 1 and parts > 1:
                continue
            (tmp_path / rel).write_text(
                json.dumps(
                    {
                        "contracts": chunk,
                        "thesis_groups": {
                            "full_game_scoring": ["game_total", "team_total"],
                            "first_half": ["first_half_total"],
                        },
                        "families": {"game_total": {"coverage": "SUPPORTED"}},
                    }
                )
            )
        slate = {
            "games": [
                {
                    "game_key": GAME,
                    "window": "early",
                    "kickoff": "2026-11-07T17:00:00Z",
                    "contract_count": len(contracts) if declared is None else declared,
                    "football_history": "PRESENT",
                    "flag_codes": ["QB_NOT_CONFIRMED"],
                    "files": {"markets": files},
                }
            ]
        }
        sd = tmp_path / "data/research_packets/slates"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "2026-11-07.json").write_text(json.dumps(slate))
        return tmp_path

    return build


def run(root, evals=None, **kw):
    inv = ma.load_inventory(root, "2026-11-07", "early")
    sides = ma.build_sides(inv)
    entries = evals or []
    applied = ma.apply_evaluations(sides, entries)
    defects = ma.verify(inv, sides, applied, **kw)
    return inv, sides, defects, ma.classify(sides, defects)


def ev(c, side, status, **kw):
    base = {
        "game_key": GAME,
        "ticker": c["ticker"],
        "side": side,
        "status": status,
        "reason": "specific football reason for this side",
    }
    base.update(kw)
    return base


def test_every_contract_yields_two_sides_and_nothing_is_evaluated_by_listing(root):
    cs = [_contract(i) for i in range(5)]
    r = root(cs)
    inv, sides, defects, verdict = run(r, expect_games=1, expect_contracts=5)
    assert len(sides) == 10 and not defects
    assert {s["status"] for s in sides} == {ma.NOT_EVALUATED}
    assert verdict["verdict"] == ma.VERDICT_INCOMPLETE and verdict["evaluation_complete"] is False
    assert all(s["probability"] is None and s["bet_up_to"] is None for s in sides)  # nothing invented


def test_unpriceable_sides_are_reconciled_but_not_evaluated(root):
    cs = [
        _contract(0, no=_side(1.0, 0.0, None, exec_=False)),
        _contract(1, yes={"ask": None, "ask_size": 0, "exec": False}),
    ]
    inv, sides, defects, _ = run(root(cs))
    un = [s for s in sides if s["status"] == ma.UNPRICEABLE]
    assert len(un) == 2 and len(sides) == 4
    assert {s["reason_code"] for s in un} == {"NO_LIQUIDITY_AT_ASK", "NO_ASK"}


def test_missing_contract_or_part_file_is_a_structural_defect(root):
    cs = [_contract(i) for i in range(6)]
    _, _, defects, verdict = run(root(cs, declared=7))
    assert any(d.code == "CONTRACT_COUNT_MISMATCH" for d in defects)
    assert verdict["structural_defects"] >= 1 and verdict["verdict"] == ma.VERDICT_INCOMPLETE
    _, _, defects, _ = run(root(cs, parts=2, skip_part=True))
    codes = {d.code for d in defects}
    assert "MARKETS_FILE_MISSING" in codes and "CONTRACT_COUNT_MISMATCH" in codes


def test_expected_counts_and_duplicate_tickers_are_detected(root):
    cs = [_contract(i) for i in range(3)]
    _, _, defects, _ = run(root(cs), expect_games=2, expect_contracts=99)
    assert {"EXPECTED_GAMES_MISMATCH", "EXPECTED_CONTRACTS_MISMATCH"} <= {d.code for d in defects}
    dup = cs + [copy.deepcopy(cs[0])]
    _, _, defects, _ = run(root(dup))
    assert "DUPLICATE_TICKER" in {d.code for d in defects}


def test_valid_evaluations_apply_and_ceiling_is_computed_not_supplied(root):
    cs = [_contract(0), _contract(1)]
    evals = [
        ev(
            cs[0],
            "YES",
            ma.EVALUATED_CANDIDATE,
            probability={"low": 0.52, "point": 0.57, "high": 0.62},
            thesis="shootout",
        ),
        ev(cs[0], "NO", ma.EVALUATED_PASS, reason_code="PRICED_IN"),
        ev(
            cs[1],
            "YES",
            ma.INSUFFICIENT_EVIDENCE,
            missing_evidence=["confirmed QB"],
            reason="QB unknown; cannot bound the total",
        ),
    ]
    _, sides, defects, verdict = run(root(cs), evals)
    cand = next(s for s in sides if s["ticker"] == cs[0]["ticker"] and s["side"] == "YES")
    assert cand["status"] == ma.EVALUATED_CANDIDATE and cand["probability"]["calibrated"] is False
    assert cand["bet_up_to"] is not None and cand["bet_up_to"] >= 0.40  # fee-inclusive ceiling from the LOWER bound
    assert cand["margin_low_vs_all_in"] == pytest.approx(0.52 - 0.41)
    assert not [d for d in defects if d.severity == ma.INPUT]
    assert verdict["verdict"] == ma.VERDICT_INCOMPLETE  # 1 NOT_EVALUATED side per remaining contract side


def test_invalid_evaluations_are_rejected_and_leave_the_side_unevaluated(root):
    cs = [_contract(0)]
    bad = [
        ev(cs[0], "YES", ma.EVALUATED_CANDIDATE, probability={"low": 0.40, "high": 0.6}),  # low <= all-in 0.41
        ev(cs[0], "NO", ma.EVALUATED_CANDIDATE),  # no probability
    ]
    _, sides, defects, _ = run(root(cs), bad)
    assert {d.code for d in defects} == {"CANDIDATE_BELOW_BREAKEVEN", "CANDIDATE_WITHOUT_PROBABILITY"}
    assert {s["status"] for s in sides} == {ma.NOT_EVALUATED}
    cases = [
        ev(cs[0], "YES", ma.EVALUATED_PASS, reason="short"),
        ev(cs[0], "YES", ma.INSUFFICIENT_EVIDENCE, probability={"low": 0.4, "high": 0.5}, missing_evidence=["x"]),
        ev(cs[0], "YES", ma.INSUFFICIENT_EVIDENCE),
        ev(cs[0], "YES", ma.EVALUATED_PASS, probability={"low": 0.7, "high": 0.6}),
        ev(cs[0], "YES", "APPROVED"),
        {**ev(cs[0], "YES", ma.EVALUATED_PASS), "ticker": "NOPE"},
    ]
    codes = []
    for e in cases:
        _, s2, d2, _ = run(root(cs), [e])
        codes.append([d.code for d in d2 if d.severity == ma.INPUT])
        assert all(s["status"] == ma.NOT_EVALUATED for s in s2)
    assert codes == [
        ["MISSING_REASON"],
        ["PROBABILITY_ON_INSUFFICIENT_EVIDENCE"],
        ["MISSING_EVIDENCE_NOT_NAMED"],
        ["BAD_PROBABILITY"],
        ["BAD_STATUS"],
        ["ORPHAN_EVALUATION"],
    ]
    dup = [ev(cs[0], "YES", ma.EVALUATED_PASS), ev(cs[0], "YES", ma.EVALUATED_PASS)]
    _, s3, d3, _ = run(root(cs), dup)
    assert [d.code for d in d3] == ["DUPLICATE_EVALUATION"] * 2 and s3[0]["status"] == ma.NOT_EVALUATED


def test_verdict_complete_only_without_gaps(root):
    cs = [_contract(0)]
    full = [
        ev(cs[0], "YES", ma.EVALUATED_PASS, probability={"low": 0.30, "high": 0.40}),
        ev(cs[0], "NO", ma.EVALUATED_PASS, probability={"low": 0.55, "high": 0.62}),
    ]
    assert run(root(cs), full)[3]["verdict"] == ma.VERDICT_COMPLETE
    qualitative = [ev(cs[0], "YES", ma.EVALUATED_PASS), full[1]]
    assert run(root(cs), qualitative)[3]["verdict"] == ma.VERDICT_GAPS  # a pass without a probability is a gap
    gap = [full[0], ev(cs[0], "NO", ma.INSUFFICIENT_EVIDENCE, missing_evidence=["weather"])]
    v = run(root(cs), gap)[3]
    assert v["verdict"] == ma.VERDICT_GAPS and v["evaluation_complete"] is False


def test_report_cannot_claim_completion_it_does_not_have(root):
    cs = [_contract(0), _contract(1)]
    inv, sides, defects, _ = run(root(cs))
    rep = ma.build_report(inv, sides, defects)
    assert ma.verify_report(rep) == []
    md = ma.render_markdown(rep)
    assert md.splitlines()[2].startswith("**NOT A COMPLETE EVALUATION")
    assert "COMPLETE:" not in md
    forged = copy.deepcopy(rep)
    forged["verdict"].update(verdict=ma.VERDICT_COMPLETE, evaluation_complete=True)
    assert any("claims completion" in p for p in ma.verify_report(forged))
    dropped = copy.deepcopy(rep)
    dropped["totals"]["sides_recorded"] -= 1
    assert ma.verify_report(dropped)


def test_alternates_and_candidate_listing_are_not_ranked_or_forced(root):
    cs = [_contract(0), _contract(1), _contract(2, family="first_half_total")]
    evals = [
        ev(cs[1], "NO", ma.EVALUATED_CANDIDATE, probability={"low": 0.70, "high": 0.80}, thesis="grind"),
        ev(cs[0], "NO", ma.EVALUATED_PASS, probability={"low": 0.5, "high": 0.6}, thesis="grind"),
    ]
    inv, sides, defects, _ = run(root(cs), evals)
    rep = ma.build_report(inv, sides, defects)
    alt = rep["alternate_expressions"]
    assert (
        alt["thesis_tag_comparisons"][0]["thesis"] == "grind"
        and len(alt["thesis_tag_comparisons"][0]["expressions"]) == 2
    )
    assert alt["candidate_groups_with_unreviewed_alternates"][0]["not_evaluated_sides"] > 0
    assert rep["family_mix_of_candidates"] == {"game_total": 1}


def test_family_history_is_after_fee_context_only(tmp_path):
    items = [
        {
            "settlement_status": "SETTLED",
            "kalshi_ticker": f"KXNCAAFSPREAD-26OCT0{i}AAABBB-AAA3",
            "stake": 100.0,
            "profit_loss": -20.0 if i % 2 else 30.0,
            "fees": 2.0,
            "source_ids": {"kalshi_game_key": f"G{i}"},
        }
        for i in range(1, 9)
    ]
    items.append(
        {"settlement_status": "OPEN", "kalshi_ticker": "KXNCAAFSPREAD-X-1", "stake": 1, "profit_loss": 0, "fees": 0}
    )
    wp = tmp_path / "w.json"
    wp.write_text(json.dumps({"items": items}))
    h = ma.family_history(wp, None)
    row = h["roi_after_fees"]["game_spread"]
    assert row["wagers"] == 8 and row["roi_after_fees"] == pytest.approx((4 * 30 - 4 * 20) / 800)
    assert h["executable_clv"] == "UNAVAILABLE" and h["clv"] == "UNAVAILABLE"
    assert ma.family_history(None, None)["roi_after_fees"] == "UNAVAILABLE"


# --------------------------------------------------------------------------------- real packets and the CLI


def test_real_early_slate_reconciles_every_side_with_no_structural_defects():
    inv = ma.load_inventory(REPO, "2026-11-07", "early")
    sides = ma.build_sides(inv)
    n = sum(len(v) for v in inv.contracts.values())
    assert n > 0 and len(sides) == 2 * n
    assert not [d for d in inv.defects]
    assert ma.classify(sides, inv.defects)["verdict"] == ma.VERDICT_INCOMPLETE  # nobody has evaluated them


def test_committed_nov7_early_report_is_self_consistent_and_honest():
    rep = json.loads((REPO / "docs/reports/CFB_MARKET_AUDIT_2026-11-07_early.json").read_text())
    assert (rep["totals"]["games"], rep["totals"]["contracts_listed"], rep["totals"]["sides_expected"]) == (
        42,
        3992,
        7984,
    )
    assert rep["totals"]["sides_recorded"] == 7984 and rep["totals"]["reconciled"] is True
    assert ma.verify_report(rep) == []
    sc = rep["totals"]["status_counts"]
    assert sum(sc.values()) == 7984
    if sc[ma.NOT_EVALUATED]:
        assert rep["verdict"]["evaluation_complete"] is False


def _script():
    spec = importlib.util.spec_from_file_location("audit_cli", REPO / "scripts/audit_cfb_market_evaluation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_exit_codes_and_artifacts(root, tmp_path, capsys):
    cs = [_contract(i) for i in range(3)]
    r = root(cs)
    cli = _script()
    out = tmp_path / "o"
    out.mkdir()
    base = ["--root", str(r), "--slate-date", "2026-11-07", "--window", "early"]
    assert (
        cli.main([*base, "--json", str(out / "r.json"), "--md", str(out / "r.md"), "--ledger-dir", str(out / "l")]) == 0
    )
    assert cli.main([*base, "--require-complete"]) == 2  # incomplete is never a success when completeness is required
    assert cli.main([*base, "--expect-contracts", "4"]) == 1  # a missing contract fails regardless
    assert cli.main(["--slate-date", "x", "--verify-report", str(out / "r.json")]) == 0
    ledger = json.loads((out / "l" / f"{GAME}.sides.json").read_text())
    assert len(ledger["sides"]) == 6
    forged = json.loads((out / "r.json").read_text())
    forged["verdict"]["evaluation_complete"] = True
    (out / "f.json").write_text(json.dumps(forged))
    assert cli.main(["--slate-date", "x", "--verify-report", str(out / "f.json")]) == 1


def test_audit_touches_no_execution_or_model_module():
    import cfb_edge_finder.research_packet.market_audit as mod

    src = Path(mod.__file__).read_text()
    for forbidden in ("execution", "decision", "modeling", "kalshi_client", "place_order"):
        assert f"cfb_edge_finder.{forbidden}" not in src
