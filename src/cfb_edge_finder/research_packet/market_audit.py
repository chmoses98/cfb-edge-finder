"""Market-first coverage audit for RUN CFB: did the analyst genuinely evaluate EVERY contract and BOTH sides? PURE.

*** READ-ONLY / RESEARCH-ONLY ***
This module reads the research packets and an optional file of ANALYST evaluations. It never produces a probability,
a bet, a stake, a ranking by attractiveness or an approval. It touches no model, no execution and no trading authority.

The point is accounting that cannot be faked by listing contracts:

* every priceable side of every listed contract is a row (``2 x contracts`` rows in total);
* a side is ``EVALUATED_*`` only if an analyst evaluation was recorded for it, with a reason and, for a candidate, a
  stated probability range whose LOWER bound clears the fee-inclusive break-even at the quoted ask;
* a side that cannot be bought (no ask, no size at the ask) is ``UNPRICEABLE`` -- reconciled, never "evaluated";
* every other side is ``NOT_EVALUATED``. That is the default. It is never silently promoted to a pass;
* ``INSUFFICIENT_EVIDENCE`` is an explicit analyst disposition that names the missing evidence. It is a gap, not a pass;
* the verdict is ``COMPLETE`` only when no side is ``NOT_EVALUATED``, no structural defect exists, and every side
  that was disposed carries a probability range. Anything less is ``COMPLETE_WITH_EVIDENCE_GAPS`` or ``INCOMPLETE``,
  and the report text says so in its first line.

The reference-class numbers shown beside a side (``ref_freq``) are HISTORICAL_ESTIMATE descriptive frequencies copied
from the packet. They are context for the analyst, NOT a probability, and they never set a status or a ceiling.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cfb_edge_finder.research_packet import pricing

AUDIT_SCHEMA = "cfb_market_audit/1.0.0"
EVALUATIONS_SCHEMA = "cfb_market_evaluations/1.0.0"

UNPRICEABLE = "UNPRICEABLE"
NOT_EVALUATED = "NOT_EVALUATED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
EVALUATED_PASS = "EVALUATED_PASS"
EVALUATED_CANDIDATE = "EVALUATED_CANDIDATE"

ANALYST_STATUSES = (EVALUATED_PASS, EVALUATED_CANDIDATE, INSUFFICIENT_EVIDENCE)
ALL_STATUSES = (UNPRICEABLE, NOT_EVALUATED, *ANALYST_STATUSES)

VERDICT_INCOMPLETE = "INCOMPLETE"
VERDICT_GAPS = "COMPLETE_WITH_EVIDENCE_GAPS"
VERDICT_COMPLETE = "COMPLETE"

THIN_REFERENCE_CLASS_N = 30

# Defect severities: STRUCTURAL defects mean the inventory itself is wrong (a contract or side is missing or
# duplicated); they can never be reported complete. INPUT defects are rejected analyst entries.
STRUCTURAL = "STRUCTURAL"
INPUT = "INPUT"


@dataclass
class Defect:
    code: str
    severity: str
    detail: str
    game_key: str | None = None
    ticker: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Inventory:
    slate_date: str
    window: str | None
    games: list[dict[str, Any]] = field(default_factory=list)  # slate rows
    contracts: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # game_key -> contracts
    thesis_groups: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    family_coverage: dict[str, dict[str, str]] = field(default_factory=dict)
    defects: list[Defect] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ inventory


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_inventory(root: Path, slate_date: str, window: str | None = None) -> Inventory:
    """Every game of the slate (optionally one window) and every contract of every markets part file.

    Loading is strict about what it can prove: a game whose markets files hold a different number of contracts than the
    slate row says, a missing part file, or a game with no markets file is a STRUCTURAL defect, not a smaller slate.
    """
    root = Path(root)
    slate = _load_json(root / "data" / "research_packets" / "slates" / f"{slate_date}.json")
    rows = [g for g in slate["games"] if window is None or g.get("window") == window]
    inv = Inventory(slate_date=slate_date, window=window, games=rows)
    for g in rows:
        key = g["game_key"]
        loaded: list[dict[str, Any]] = []
        for rel in g.get("files", {}).get("markets") or []:
            path = root / rel
            if not path.is_file():
                inv.defects.append(Defect("MARKETS_FILE_MISSING", STRUCTURAL, f"{rel} does not exist", key))
                continue
            doc = _load_json(path)
            loaded.extend(doc.get("contracts") or [])
            inv.thesis_groups.setdefault(key, doc.get("thesis_groups") or {})
            for fam, meta in (doc.get("families") or {}).items():
                inv.family_coverage.setdefault(key, {})[fam] = (meta or {}).get("coverage", "UNKNOWN")
        inv.contracts[key] = loaded
        if not g.get("files", {}).get("markets"):
            inv.defects.append(Defect("NO_MARKETS_FILES_LISTED", STRUCTURAL, "slate row lists no markets file", key))
        if len(loaded) != int(g.get("contract_count", -1)):
            inv.defects.append(
                Defect(
                    "CONTRACT_COUNT_MISMATCH",
                    STRUCTURAL,
                    f"slate row says {g.get('contract_count')} contracts, markets files hold {len(loaded)}",
                    key,
                )
            )
        tickers = [c.get("ticker") for c in loaded]
        for t, n in Counter(tickers).items():
            if n > 1:
                inv.defects.append(Defect("DUPLICATE_TICKER", STRUCTURAL, f"listed {n} times", key, t))
    return inv


# ------------------------------------------------------------------------------------------------ sides


def _unpriceable_reason(block: dict[str, Any] | None) -> str | None:
    if not isinstance(block, dict):
        return "SIDE_BLOCK_MISSING"
    ask = block.get("ask")
    if not block.get("exec") or not (float(block.get("ask_size") or 0) > 0):
        # includes an ask of 1.00 with no resting size: nobody is offering this side at any price
        return "NO_LIQUIDITY_AT_ASK" if ask is not None else "NO_ASK"
    if ask is None:
        return "NO_ASK"
    if not (0.0 < float(ask) < 1.0):
        return "ASK_OUTSIDE_0_1"
    if block.get("all_in") is None:
        return "NO_FEE_INCLUSIVE_PRICE"
    return None


def build_sides(inv: Inventory) -> list[dict[str, Any]]:
    """Two rows per listed contract. A contract missing a YES or NO block still yields both rows (as UNPRICEABLE)."""
    rows: list[dict[str, Any]] = []
    games = {g["game_key"]: g for g in inv.games}
    for key, contracts in inv.contracts.items():
        g = games[key]
        group_of = {fam: grp for grp, fams in (inv.thesis_groups.get(key) or {}).items() for fam in fams}
        flags = set(g.get("flag_codes") or [])
        for c in contracts:
            ref = c.get("ref_freq") if isinstance(c.get("ref_freq"), dict) else None
            for side in ("YES", "NO"):
                block = c.get(side.lower())
                bad = _unpriceable_reason(block)
                block = block if isinstance(block, dict) else {}
                gaps: list[str] = []
                if ref is None:
                    gaps.append("NO_REFERENCE_CLASS")
                elif int(ref.get("n") or 0) < THIN_REFERENCE_CLASS_N:
                    gaps.append("REFERENCE_CLASS_THIN")
                if g.get("football_history") != "PRESENT":
                    gaps.append("FOOTBALL_HISTORY_ABSENT")
                if "QB_NOT_CONFIRMED" in flags:
                    gaps.append("QB_NOT_CONFIRMED")
                if "NO_STRUCTURED_INJURY_SOURCE" in flags:
                    gaps.append("NO_STRUCTURED_INJURY_SOURCE")
                if c.get("family") in (
                    "touchdown_scorer",
                    "team_stat_prop",
                    "game_stat_prop",
                    "player_prop",
                    "unknown",
                    "overtime",
                ):
                    gaps.append("NO_FOOTBALL_MODEL_INPUT_FOR_FAMILY")
                ref_side = None
                if ref is not None and ref.get("yes") is not None:
                    ref_side = ref["yes"] if side == "YES" else round(1.0 - float(ref["yes"]), 4)
                rows.append(
                    {
                        "game_key": key,
                        "kickoff": g.get("kickoff"),
                        "ticker": c.get("ticker"),
                        "family": c.get("family"),
                        "thesis_group": group_of.get(c.get("family"), "other"),
                        "side": side,
                        "means": c.get("yes_means") if side == "YES" else f"NOT: {c.get('yes_means')}",
                        "line": c.get("line"),
                        "book": c.get("book"),
                        "ask": block.get("ask"),
                        "ask_size": block.get("ask_size"),
                        "contracts_1u": block.get("contracts_1u"),
                        "order_fee_1u": block.get("order_fee_1u"),
                        "all_in": block.get("all_in"),
                        "quote_updated": c.get("upd"),
                        "football_history": g.get("football_history"),
                        "reference_class": (
                            None
                            if ref is None
                            else {"n": ref.get("n"), "p_side": ref_side, "tier": "HISTORICAL_ESTIMATE"}
                        ),
                        "evidence_gaps": gaps,
                        "family_coverage": (inv.family_coverage.get(key) or {}).get(c.get("family"), "UNKNOWN"),
                        "status": UNPRICEABLE if bad else NOT_EVALUATED,
                        "reason_code": bad,
                        "reason": None,
                        "probability": None,
                        "margin_low_vs_all_in": None,
                        "bet_up_to": None,
                        "thesis": None,
                        "missing_evidence": None,
                    }
                )
    return rows


# ------------------------------------------------------------------------------------------------ evaluations


def load_evaluations(path: Path | None) -> tuple[list[dict[str, Any]], list[Defect]]:
    if path is None:
        return [], []
    doc = _load_json(Path(path))
    defects: list[Defect] = []
    if not isinstance(doc, dict) or doc.get("schema_version") != EVALUATIONS_SCHEMA:
        defects.append(
            Defect("EVALUATIONS_SCHEMA", INPUT, f"expected schema_version {EVALUATIONS_SCHEMA!r}; no entry was applied")
        )
        return [], defects
    return list(doc.get("evaluations") or []), defects


def _valid_probability(p: Any) -> str | None:
    if not isinstance(p, dict):
        return "probability must be an object with low/high (and optionally point)"
    low, high, point = p.get("low"), p.get("high"), p.get("point")
    for name, v in (("low", low), ("high", high)):
        if not isinstance(v, int | float) or isinstance(v, bool) or not (0.0 < v < 1.0):
            return f"probability.{name} must be a number strictly between 0 and 1"
    if low > high:
        return "probability.low exceeds probability.high"
    if point is not None:
        if not isinstance(point, int | float) or isinstance(point, bool) or not (low <= point <= high):
            return "probability.point must lie within [low, high]"
    return None


def apply_evaluations(sides: list[dict[str, Any]], entries: list[dict[str, Any]]) -> list[Defect]:
    """Attach analyst evaluations. A rejected entry changes nothing: its side stays NOT_EVALUATED."""
    defects: list[Defect] = []
    index = {(s["game_key"], s["ticker"], s["side"]): s for s in sides}
    seen = Counter((e.get("game_key"), e.get("ticker"), str(e.get("side", "")).upper()) for e in entries)
    for e in entries:
        key = (e.get("game_key"), e.get("ticker"), str(e.get("side", "")).upper())
        gk, tk = key[0], key[1]

        def reject(code: str, detail: str, gk=gk, tk=tk) -> None:
            defects.append(Defect(code, INPUT, detail, gk, tk))

        if seen[key] > 1:
            reject("DUPLICATE_EVALUATION", f"{key[2]} evaluated {seen[key]} times; all copies rejected")
            continue
        side = index.get(key)
        if side is None:
            reject("ORPHAN_EVALUATION", f"no listed {key[2]} side matches this game_key/ticker")
            continue
        if side["status"] == UNPRICEABLE:
            reject("EVALUATION_ON_UNPRICEABLE_SIDE", f"side cannot be bought ({side['reason_code']}); entry ignored")
            continue
        status = e.get("status")
        reason = e.get("reason")
        if status not in ANALYST_STATUSES:
            reject("BAD_STATUS", f"status must be one of {ANALYST_STATUSES}")
            continue
        if not isinstance(reason, str) or len(reason.strip()) < 8:
            reject("MISSING_REASON", "a specific pass/candidate/insufficient-evidence reason is required")
            continue
        prob = e.get("probability")
        if status == INSUFFICIENT_EVIDENCE:
            if prob is not None:
                reject(
                    "PROBABILITY_ON_INSUFFICIENT_EVIDENCE", "a side with insufficient evidence carries no probability"
                )
                continue
            missing = e.get("missing_evidence")
            if not isinstance(missing, list) or not missing or not all(isinstance(m, str) and m for m in missing):
                reject("MISSING_EVIDENCE_NOT_NAMED", "name the missing evidence in `missing_evidence`")
                continue
            side["missing_evidence"] = missing
        else:
            if status == EVALUATED_CANDIDATE and prob is None:
                reject("CANDIDATE_WITHOUT_PROBABILITY", "a candidate needs a stated probability range")
                continue
            if prob is not None:
                err = _valid_probability(prob)
                if err:
                    reject("BAD_PROBABILITY", err)
                    continue
                if status == EVALUATED_CANDIDATE and not (float(prob["low"]) > float(side["all_in"])):
                    reject(
                        "CANDIDATE_BELOW_BREAKEVEN",
                        f"lower bound {prob['low']} does not clear the fee-inclusive break-even {side['all_in']}; "
                        "record it as EVALUATED_PASS (you may state a conditional ceiling)",
                    )
                    continue
        side["status"] = status
        side["reason"] = reason.strip()
        side["reason_code"] = e.get("reason_code") or side["reason_code"]
        side["thesis"] = e.get("thesis")
        if prob is not None:
            low = float(prob["low"])
            side["probability"] = {
                "low": low,
                "point": prob.get("point"),
                "high": float(prob["high"]),
                "tier": "ANALYST_JUDGMENT",
                "calibrated": False,
            }
            side["margin_low_vs_all_in"] = round(low - float(side["all_in"]), 6)
            side["bet_up_to"] = pricing.bet_up_to_price(low, str(side["ticker"]).split("-")[0])
            claimed = e.get("bet_up_to")
            if claimed is not None and side["bet_up_to"] is not None and abs(float(claimed) - side["bet_up_to"]) > 1e-9:
                defects.append(
                    Defect(
                        "BET_UP_TO_OVERRIDDEN",
                        INPUT,
                        f"entry stated {claimed}; the fee-inclusive ceiling from the lower bound is {side['bet_up_to']}"
                        " (the computed value is recorded)",
                        gk,
                        tk,
                    )
                )
    return defects


# ------------------------------------------------------------------------------------------------ verification


def verify(
    inv: Inventory,
    sides: list[dict[str, Any]],
    input_defects: list[Defect],
    *,
    expect_games: int | None = None,
    expect_contracts: int | None = None,
) -> list[Defect]:
    """Everything that makes the evaluation unreportable as complete. Returns all defects (structural first)."""
    out: list[Defect] = list(inv.defects)
    n_contracts = sum(len(v) for v in inv.contracts.values())
    if expect_games is not None and len(inv.games) != expect_games:
        out.append(
            Defect("EXPECTED_GAMES_MISMATCH", STRUCTURAL, f"expected {expect_games} games, found {len(inv.games)}")
        )
    if expect_contracts is not None and n_contracts != expect_contracts:
        out.append(
            Defect(
                "EXPECTED_CONTRACTS_MISMATCH", STRUCTURAL, f"expected {expect_contracts} contracts, found {n_contracts}"
            )
        )
    if len(sides) != 2 * n_contracts:
        out.append(
            Defect(
                "SIDE_COUNT_MISMATCH", STRUCTURAL, f"{len(sides)} side rows for {n_contracts} contracts (need 2 each)"
            )
        )
    per_contract = Counter((s["game_key"], s["ticker"]) for s in sides)
    for (gk, tk), n in per_contract.items():
        if n != 2:
            out.append(Defect("CONTRACT_MISSING_A_SIDE", STRUCTURAL, f"{n} side rows (need YES and NO)", gk, tk))
    for s in sides:
        if s["status"] not in ALL_STATUSES:
            out.append(Defect("UNKNOWN_STATUS", STRUCTURAL, str(s["status"]), s["game_key"], s["ticker"]))
    out.extend(input_defects)
    return out


def classify(sides: list[dict[str, Any]], defects: list[Defect]) -> dict[str, Any]:
    """The only place a verdict is decided. ``evaluation_complete`` is True only for VERDICT_COMPLETE."""
    c = Counter(s["status"] for s in sides)
    structural = [d for d in defects if d.severity == STRUCTURAL]
    disposed = [s for s in sides if s["status"] in ANALYST_STATUSES]
    without_prob = [s for s in disposed if s["status"] != INSUFFICIENT_EVIDENCE and s["probability"] is None]
    if structural or c[NOT_EVALUATED] > 0:
        verdict = VERDICT_INCOMPLETE
    elif c[INSUFFICIENT_EVIDENCE] > 0 or without_prob:
        verdict = VERDICT_GAPS
    else:
        verdict = VERDICT_COMPLETE
    return {
        "verdict": verdict,
        "evaluation_complete": verdict == VERDICT_COMPLETE,
        "every_side_has_a_disposition": c[NOT_EVALUATED] == 0 and not structural,
        "structural_defects": len(structural),
        "rejected_analyst_entries": len([d for d in defects if d.severity == INPUT]),
        "evaluated_without_probability": len(without_prob),
    }


# ------------------------------------------------------------------------------------------------ history


FAMILY_BY_SERIES = {
    "KXNCAAFSPREAD": "game_spread",
    "KXNCAAFTOTAL": "game_total",
    "KXNCAAFGAME": "game_moneyline",
    "KXNCAAFTEAMTOTAL": "team_total",
    "KXNCAAF1HSPREAD": "first_half_spread",
    "KXNCAAF1HTOTAL": "first_half_total",
    "KXNCAAF1QSPREAD": "quarter_spread",
    "KXNCAAF1QTOTAL": "quarter_total",
}


def family_history(wagers_path: Path | None, evidence_join_path: Path | None) -> dict[str, Any]:
    """After-fee ROI (settled accounted wagers) and close-proxy CLV by market family, where the repository has them.

    Aggregate only. Cells were examined after the fact, so this is context, never a rule: it does not exclude or
    favour any family, and a family with no row has no settled history here (not "good" and not "bad").
    """
    out: dict[str, Any] = {
        "roi_after_fees": {},
        "clv": {},
        "caveats": [
            "ROI is realised after-fee P&L on settled accounted wagers; cells are small and were examined "
            "after the fact.",
            "No model probability was recorded with these wagers, so handicapping error cannot be separated "
            "from pricing.",
            "CLV below is vs the closing MID proxy from the evidence join. Executable (ask-side) CLV is NOT available "
            "in the repository's evidence join; it is reported as unavailable rather than approximated.",
            "Nothing here excludes, favours or forces any family.",
        ],
    }
    if wagers_path and Path(wagers_path).is_file():
        by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for w in _load_json(Path(wagers_path)).get("items", []):
            if w.get("settlement_status") != "SETTLED":
                continue
            ticker = w.get("kalshi_ticker") or ""
            fam = next(
                (
                    v
                    for k, v in sorted(FAMILY_BY_SERIES.items(), key=lambda kv: -len(kv[0]))
                    if ticker.startswith(k + "-")
                ),
                None,
            )
            if fam is None:
                fam = "combo_or_other"
            game = (w.get("source_ids") or {}).get("kalshi_game_key") or ticker
            by_family[fam].append(
                {"stake": float(w["stake"]), "pl": float(w["profit_loss"]), "fee": float(w["fees"]), "game": game}
            )
        for fam, rows in sorted(by_family.items()):
            stake = sum(r["stake"] for r in rows)
            out["roi_after_fees"][fam] = {
                "wagers": len(rows),
                "games": len({r["game"] for r in rows}),
                "outlay": round(stake, 2),
                "net_pl": round(sum(r["pl"] for r in rows), 2),
                "fees": round(sum(r["fee"] for r in rows), 2),
                "roi_after_fees": round(sum(r["pl"] for r in rows) / stake, 4) if stake else None,
                "ci95_game_clustered": _cluster_ci(rows),
            }
    else:
        out["roi_after_fees"] = "UNAVAILABLE"
    if evidence_join_path and Path(evidence_join_path).is_file():
        by = (_load_json(Path(evidence_join_path)).get("clv_vs_mid") or {}).get("by_family") or {}
        out["clv"] = {
            FAMILY_BY_SERIES.get(series, series): {"n": v.get("n"), "mean_vs_mid": v.get("mean"), "ci95": v.get("ci95")}
            for series, v in sorted(by.items())
        }
    else:
        out["clv"] = "UNAVAILABLE"
    out["executable_clv"] = "UNAVAILABLE"
    return out


def _cluster_ci(rows: list[dict[str, Any]], reps: int = 2000) -> list[float] | None:
    import random

    games: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        games[r["game"]].append(r)
    groups = list(games.values())
    if len(groups) < 5:
        return None  # too few independent games for an interval; shown as missing, not as a wide guess

    def roi(rs: list[dict[str, Any]]) -> float:
        s = sum(r["stake"] for r in rs)
        return sum(r["pl"] for r in rs) / s if s else 0.0

    rnd = random.Random(11)
    draws = sorted(roi([x for g in (rnd.choice(groups) for _ in groups) for x in g]) for _ in range(reps))
    return [round(draws[int(0.025 * reps)], 4), round(draws[int(0.975 * reps) - 1], 4)]


# ------------------------------------------------------------------------------------------------ report


def _count(sides: list[dict[str, Any]]) -> dict[str, int]:
    c = Counter(s["status"] for s in sides)
    return {k: c.get(k, 0) for k in ALL_STATUSES}


def build_report(
    inv: Inventory,
    sides: list[dict[str, Any]],
    defects: list[Defect],
    *,
    history: dict[str, Any] | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    n_contracts = sum(len(v) for v in inv.contracts.values())
    verdict = classify(sides, defects)
    counts = _count(sides)

    fam: dict[str, dict[str, Any]] = {}
    for fname in sorted({s["family"] for s in sides}, key=str):
        rows = [s for s in sides if s["family"] == fname]
        fc = _count(rows)
        fam[str(fname)] = {
            "contracts": len({(s["game_key"], s["ticker"]) for s in rows}),
            "sides": len(rows),
            **{k.lower(): v for k, v in fc.items()},
            "evaluated": fc[EVALUATED_PASS] + fc[EVALUATED_CANDIDATE],
            "with_probability": sum(1 for s in rows if s["probability"] is not None),
            "pct_priceable_sides_disposed": _pct(
                fc[EVALUATED_PASS] + fc[EVALUATED_CANDIDATE] + fc[INSUFFICIENT_EVIDENCE],
                len(rows) - fc[UNPRICEABLE],
            ),
            "packet_family_coverage": sorted({s["family_coverage"] for s in rows}),
        }

    candidates = [s for s in sides if s["status"] == EVALUATED_CANDIDATE]
    candidates.sort(key=lambda s: (str(s["kickoff"]), s["game_key"], s["ticker"], s["side"]))  # NOT by attractiveness

    rejected = Counter()
    for s in sides:
        if s["status"] == EVALUATED_PASS:
            rejected[s["reason_code"] or "UNCODED_ANALYST_PASS"] += 1
    unpriceable = Counter(s["reason_code"] for s in sides if s["status"] == UNPRICEABLE)
    gap_counts = Counter(g for s in sides if s["status"] != UNPRICEABLE for g in s["evidence_gaps"])
    missing_named = Counter(
        m for s in sides if s["status"] == INSUFFICIENT_EVIDENCE for m in (s["missing_evidence"] or [])
    )

    return {
        "schema_version": AUDIT_SCHEMA,
        "slate_date": inv.slate_date,
        "window": inv.window,
        "as_of": as_of,
        "read_only": True,
        "authority": "RESEARCH_ONLY: no probability, bet, stake, approval or order is produced here",
        "verdict": verdict,
        "totals": {
            "games": len(inv.games),
            "contracts_listed": n_contracts,
            "sides_expected": 2 * n_contracts,
            "sides_recorded": len(sides),
            "reconciled": len(sides) == 2 * n_contracts and verdict["structural_defects"] == 0,
            "status_counts": counts,
            "priceable_sides": len(sides) - counts[UNPRICEABLE],
            "evaluated_sides": counts[EVALUATED_PASS] + counts[EVALUATED_CANDIDATE],
            "insufficient_evidence_sides": counts[INSUFFICIENT_EVIDENCE],
            "not_evaluated_sides": counts[NOT_EVALUATED],
            "unpriceable_sides": counts[UNPRICEABLE],
            "sides_with_stated_probability": sum(1 for s in sides if s["probability"] is not None),
        },
        "coverage_by_family": fam,
        "unpriceable_by_reason": dict(sorted(unpriceable.items())),
        "evidence_gaps_across_priceable_sides": dict(sorted(gap_counts.items())),
        "serious_candidates": [_candidate_row(s) for s in candidates],
        "rejection_reasons": dict(sorted(rejected.items(), key=lambda kv: -kv[1])),
        "missing_evidence_named_by_analyst": dict(sorted(missing_named.items(), key=lambda kv: -kv[1])),
        "alternate_expressions": _alternates(sides),
        "history_by_family": history,
        "defects": [d.as_dict() for d in defects],
        "family_mix_of_candidates": dict(Counter(s["family"] for s in candidates)),
        "notes": [
            "NOT_EVALUATED means no analyst evaluation is on record; it is not a pass.",
            "reference_class numbers are descriptive historical frequencies, never a probability or a status.",
            "Nothing is ranked by attractiveness; candidates are listed by kickoff then ticker. No family is favoured "
            "or excluded and no diversification is forced.",
        ],
    }


def _pct(n: int, d: int) -> float | None:
    return None if d <= 0 else round(100.0 * n / d, 1)


def _candidate_row(s: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "game_key",
        "ticker",
        "family",
        "side",
        "means",
        "ask",
        "ask_size",
        "contracts_1u",
        "all_in",
        "order_fee_1u",
        "probability",
        "margin_low_vs_all_in",
        "bet_up_to",
        "reason",
        "thesis",
        "evidence_gaps",
        "quote_updated",
    )
    return {k: s[k] for k in keys}


def _alternates(sides: list[dict[str, Any]]) -> dict[str, Any]:
    """Same-game thesis comparison from analyst-supplied thesis tags, plus a mechanical unreviewed-alternates flag."""
    by_thesis: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for s in sides:
        if s["thesis"] and s["status"] in (EVALUATED_PASS, EVALUATED_CANDIDATE):
            by_thesis[(s["game_key"], s["thesis"])].append(s)
    compared = []
    for (gk, thesis), rows in sorted(by_thesis.items()):
        compared.append(
            {
                "game_key": gk,
                "thesis": thesis,
                "families": sorted({r["family"] for r in rows}),
                "expressions": [
                    {
                        "ticker": r["ticker"],
                        "family": r["family"],
                        "side": r["side"],
                        "status": r["status"],
                        "ask": r["ask"],
                        "all_in": r["all_in"],
                        "low": (r["probability"] or {}).get("low"),
                        "margin_low_vs_all_in": r["margin_low_vs_all_in"],
                        "bet_up_to": r["bet_up_to"],
                    }
                    for r in sorted(rows, key=lambda r: (r["family"], r["ticker"], r["side"]))
                ],
            }
        )
    # A game with a candidate whose thesis-group siblings still hold unevaluated priceable sides has not had its
    # alternate expressions compared.
    candidate_groups: dict[str, set[str]] = defaultdict(set)
    for s in sides:
        if s["status"] == EVALUATED_CANDIDATE:
            candidate_groups[s["game_key"]].add(s["thesis_group"])
    unreviewed = []
    for gk, groups in sorted(candidate_groups.items()):
        for grp in sorted(groups):
            left = [
                s for s in sides if s["game_key"] == gk and s["thesis_group"] == grp and s["status"] == NOT_EVALUATED
            ]
            if left:
                unreviewed.append(
                    {
                        "game_key": gk,
                        "thesis_group": grp,
                        "not_evaluated_sides": len(left),
                        "families": sorted({s["family"] for s in left}),
                    }
                )
    return {
        "thesis_tag_comparisons": compared,
        "candidate_groups_with_unreviewed_alternates": unreviewed,
        "note": "Thesis tags are the analyst's. Where none are supplied no cross-family comparison is claimed.",
    }


# ------------------------------------------------------------------------------------------------ report integrity


def verify_report(report: dict[str, Any]) -> list[str]:
    """Independent consistency check of a rendered/committed report: catches a hand-edited or stale 'complete' claim."""
    problems: list[str] = []
    t = report.get("totals", {})
    v = report.get("verdict", {})
    sc = t.get("status_counts", {})
    if sum(sc.values()) != t.get("sides_recorded"):
        problems.append("status counts do not sum to sides_recorded")
    if t.get("sides_recorded") != t.get("sides_expected"):
        problems.append("sides_recorded != sides_expected (2 x contracts)")
    if t.get("sides_expected") != 2 * t.get("contracts_listed", -1):
        problems.append("sides_expected != 2 x contracts_listed")
    fam_sides = sum(f["sides"] for f in report.get("coverage_by_family", {}).values())
    if fam_sides != t.get("sides_recorded"):
        problems.append("family coverage does not account for every side")
    claims = v.get("evaluation_complete") or v.get("verdict") in (VERDICT_COMPLETE, VERDICT_GAPS)
    if claims and (sc.get(NOT_EVALUATED, 0) > 0 or v.get("structural_defects", 0) > 0):
        problems.append("report claims completion while sides are NOT_EVALUATED or structural defects exist")
    if v.get("evaluation_complete") and v.get("verdict") != VERDICT_COMPLETE:
        problems.append("evaluation_complete is true but verdict is not COMPLETE")
    if v.get("evaluation_complete") and (sc.get(INSUFFICIENT_EVIDENCE, 0) or v.get("evaluated_without_probability")):
        problems.append("evaluation_complete is true while evidence gaps remain")
    return problems


def render_markdown(report: dict[str, Any]) -> str:
    t, v = report["totals"], report["verdict"]
    sc = t["status_counts"]
    lines: list[str] = []
    if v["verdict"] == VERDICT_COMPLETE:
        head = "COMPLETE: every side has a disposition and a stated probability range."
    elif v["verdict"] == VERDICT_GAPS:
        head = "COMPLETE WITH EVIDENCE GAPS: every side has a disposition, but some lack evidence or a probability."
    else:
        head = (
            f"NOT A COMPLETE EVALUATION. {t['not_evaluated_sides']:,} of {t['priceable_sides']:,} priceable sides have "
            "no analyst evaluation on record."
            if t["not_evaluated_sides"]
            else "NOT A COMPLETE EVALUATION. Structural defects were found (see Defects)."
        )
    lines += [
        f"# CFB market-first audit: {report['slate_date']}"
        + (f" ({report['window']} window)" if report["window"] else ""),
        "",
        f"**{head}**",
        "",
        "_Research-only. No probability, bet, stake, approval or order is produced by this audit._",
        "",
        "## Reconciliation",
        "",
        "| games | contracts listed | sides expected (2x) | sides recorded | reconciled |",
        "|---:|---:|---:|---:|:---:|",
        f"| {t['games']} | {t['contracts_listed']:,} | {t['sides_expected']:,} | {t['sides_recorded']:,} | "
        f"{'yes' if t['reconciled'] else 'NO'} |",
        "",
        "| status | sides |",
        "|---|---:|",
    ]
    for k in ALL_STATUSES:
        lines.append(f"| {k} | {sc[k]:,} |")
    lines += [
        "",
        f"Sides with an analyst-stated probability range: **{t['sides_with_stated_probability']:,}**. "
        f"Evaluated: **{t['evaluated_sides']:,}**; insufficient evidence: **{t['insufficient_evidence_sides']:,}**; "
        f"unpriceable: **{t['unpriceable_sides']:,}**; not evaluated: **{t['not_evaluated_sides']:,}**.",
        "",
        "## Coverage by family",
        "",
        "| family | contracts | sides | evaluated | insufficient | not evaluated | unpriceable | with probability "
        "| % priceable disposed |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, f in sorted(report["coverage_by_family"].items(), key=lambda kv: -kv[1]["sides"]):
        pct = "n/a" if f["pct_priceable_sides_disposed"] is None else f"{f['pct_priceable_sides_disposed']}%"
        lines.append(
            f"| {name} | {f['contracts']:,} | {f['sides']:,} | {f['evaluated']:,} | {f['insufficient_evidence']:,} | "
            f"{f['not_evaluated']:,} | {f['unpriceable']:,} | {f['with_probability']:,} | {pct} |"
        )
    lines += ["", "## Why sides are unpriceable", ""]
    for k, n in report["unpriceable_by_reason"].items():
        lines.append(f"* `{k}`: {n:,}")
    lines += ["", "## Evidence gaps across priceable sides (a side can carry several)", ""]
    for k, n in report["evidence_gaps_across_priceable_sides"].items():
        lines.append(f"* `{k}`: {n:,}")
    lines += ["", "## Serious candidates (analyst-stated; listed by kickoff, not ranked)", ""]
    if report["serious_candidates"]:
        lines += [
            "| game | ticker | side | ask | all-in | low / point / high | Bet Up To | reason |",
            "|---|---|---|---:|---:|---|---:|---|",
        ]
        for c in report["serious_candidates"]:
            p = c["probability"] or {}
            lines.append(
                f"| {c['game_key']} | `{c['ticker']}` | {c['side']} | {c['ask']} | {c['all_in']} | "
                f"{p.get('low')} / {p.get('point')} / {p.get('high')} | {c['bet_up_to']} | {c['reason']} |"
            )
    else:
        lines.append("None on record. (That is not a finding that none exist: see the status counts above.)")
    lines += ["", "## Why markets were rejected (analyst passes, by reason code)", ""]
    if report["rejection_reasons"]:
        for k, n in report["rejection_reasons"].items():
            lines.append(f"* `{k}`: {n:,}")
    else:
        lines.append("No analyst passes are on record.")
    if report["missing_evidence_named_by_analyst"]:
        lines += ["", "## Missing evidence named by the analyst", ""]
        for k, n in report["missing_evidence_named_by_analyst"].items():
            lines.append(f"* {k}: {n:,}")
    alt = report["alternate_expressions"]
    lines += ["", "## Alternate expressions of the same thesis", ""]
    if alt["thesis_tag_comparisons"]:
        for g in alt["thesis_tag_comparisons"]:
            lines.append(
                f"* **{g['game_key']} / {g['thesis']}**: {len(g['expressions'])} expressions across "
                f"{', '.join(g['families'])}"
            )
    else:
        lines.append("No thesis-tagged comparisons on record; none is claimed.")
    for u in alt["candidate_groups_with_unreviewed_alternates"]:
        lines.append(
            f"* UNREVIEWED ALTERNATES: {u['game_key']} / {u['thesis_group']} has {u['not_evaluated_sides']} "
            f"unevaluated sides ({', '.join(u['families'])})"
        )
    h = report.get("history_by_family")
    if isinstance(h, dict):
        lines += ["", "## Historical after-fee ROI and CLV by family (context only)", ""]
        roi = h.get("roi_after_fees")
        if isinstance(roi, dict):
            lines += [
                "| family | wagers | games | outlay $ | net P&L $ | ROI after fees | 95% CI (game-clustered) "
                "| CLV vs mid (n) |",
                "|---|---:|---:|---:|---:|---:|---|---|",
            ]
            clv = h.get("clv") if isinstance(h.get("clv"), dict) else {}
            for fam, r in roi.items():
                c = clv.get(fam)
                clv_s = f"{c['mean_vs_mid']} ({c['n']})" if c else "n/a"
                lines.append(
                    f"| {fam} | {r['wagers']} | {r['games']} | {r['outlay']:,} | {r['net_pl']:,} | "
                    f"{_fmt_pct(r['roi_after_fees'])} | {r['ci95_game_clustered'] or 'too few games'} | {clv_s} |"
                )
        else:
            lines.append("Settled-wager ROI is unavailable.")
        lines += [""] + [f"* {c}" for c in h.get("caveats", [])]
        lines.append(f"* Executable CLV: {h.get('executable_clv')}")
    lines += ["", "## Defects", ""]
    if report["defects"]:
        by = Counter((d["severity"], d["code"]) for d in report["defects"])
        for (sev, code), n in sorted(by.items()):
            lines.append(f"* {sev} `{code}`: {n}")
    else:
        lines.append("None.")
    lines += [""] + [f"_{n}_" for n in report["notes"]]
    return "\n".join(lines) + "\n"


def _fmt_pct(x: float | None) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{100 * x:.1f}%"


def write_ledger(sides: list[dict[str, Any]], out_dir: Path) -> list[Path]:
    """One compact JSON file per game (every side, one row each); the full ledger is regenerated, not committed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    by_game: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in sides:
        by_game[s["game_key"]].append(s)
    paths = []
    for key, rows in sorted(by_game.items()):
        p = out_dir / f"{key}.sides.json"
        p.write_text(json.dumps({"game_key": key, "sides": rows}, separators=(",", ":")) + "\n", encoding="utf-8")
        paths.append(p)
    return paths
