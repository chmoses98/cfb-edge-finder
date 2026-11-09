# RUN CFB market-first audit

Read-only, research-only. It reconciles **every listed Kalshi contract and both YES/NO sides** of a slate against the
analyst's recorded evaluations, and refuses to call an unfinished evaluation complete. It changes no prediction model,
trading authority or order execution, and it produces no probability, bet, stake, ranking by attractiveness or approval.

```
python scripts/audit_cfb_market_evaluation.py --slate-date 2026-11-07 --window early \
    --expect-games 42 --expect-contracts 3992 \
    [--evaluations analyst_evaluations.json] [--ledger-dir OUT] [--json OUT.json] [--md OUT.md] [--require-complete]
python scripts/audit_cfb_market_evaluation.py --slate-date x --verify-report OUT.json   # re-check a saved report
```

Exit codes: `0` report written; `1` structural defect (a contract or side missing, duplicated or miscounted, or a
report that contradicts its own arithmetic); `2` `--require-complete` and the verdict is not `COMPLETE`.

## What is counted

Inventory = every game of the slate (optionally one window) and every contract in every `markets*.json` part named by
the slate row. Each contract yields **two side rows** (YES at `yes.ask`, NO at `no.ask`), so sides = 2 × contracts. A
game whose files hold a different count than the slate row, a missing part file, a duplicate ticker, a contract lacking a
side, or a mismatch with `--expect-games/--expect-contracts` is a **STRUCTURAL** defect and the verdict is `INCOMPLETE`.

| status | meaning |
|---|---|
| `UNPRICEABLE` | no ask, or no resting size at the ask (an ask of 1.00 with zero size is nobody offering). Reconciled, never "evaluated". |
| `NOT_EVALUATED` | priceable, no analyst evaluation on record. The default. Never silently a pass. |
| `INSUFFICIENT_EVIDENCE` | analyst disposition naming the missing evidence. A gap, not a pass. |
| `EVALUATED_PASS` | analyst evaluated and passes, with a specific reason (probability range optional, "where justified"). |
| `EVALUATED_CANDIDATE` | analyst range whose **lower bound** clears the fee-inclusive break-even (`all_in`) at the quoted ask. |

Verdict: `INCOMPLETE` (any `NOT_EVALUATED` side or structural defect) → `COMPLETE_WITH_EVIDENCE_GAPS` (everything has a
disposition but some side is `INSUFFICIENT_EVIDENCE` or a pass without a probability) → `COMPLETE` (every priceable side
evaluated with a stated range). The rendered report's first line states the verdict, and `verify_report` rejects a report
that claims completion while sides are unevaluated or evidence is missing.

Per side the ledger records ask, size, 1U contracts and order fee, `all_in` (= break-even), book state, quote update
time, the packet's reference class (`n`, frequency for that side; a **HISTORICAL_ESTIMATE**, never a status or a
probability), evidence gaps (`NO_REFERENCE_CLASS`, `REFERENCE_CLASS_THIN`, `FOOTBALL_HISTORY_ABSENT`, `QB_NOT_CONFIRMED`,
`NO_STRUCTURED_INJURY_SOURCE`, `NO_FOOTBALL_MODEL_INPUT_FOR_FAMILY`), analysis status, probability range, **Bet Up To**
(computed by `research_packet/pricing.py` from the lower bound, never taken from the input) and reason.

## Analyst evaluations file (`cfb_market_evaluations/1.0.0`)

```json
{"schema_version": "cfb_market_evaluations/1.0.0", "evaluations": [
  {"game_key": "26NOV07BRWNYALE", "ticker": "KXNCAAFTOTAL-26NOV07BRWNYALE-45", "side": "NO",
   "status": "EVALUATED_CANDIDATE", "reason": "specific football reason",
   "probability": {"low": 0.58, "point": 0.63, "high": 0.68}, "thesis": "slow game", "reason_code": "OPTIONAL_CODE"},
  {"game_key": "...", "ticker": "...", "side": "YES", "status": "INSUFFICIENT_EVIDENCE",
   "reason": "QB unconfirmed; cannot bound the total", "missing_evidence": ["confirmed starting QB"]}
]}
```

Rejected entries (the side stays `NOT_EVALUATED`, and the audit names the defect): duplicate or orphan entries, a missing or
short reason, an invalid range, a candidate whose lower bound does not clear break-even (record it as a pass), a
probability on `INSUFFICIENT_EVIDENCE`, an entry for an unpriceable side. A stated `bet_up_to` that differs from the
computed one is overridden and flagged. Ranges are the analyst's opinion: `calibrated` is always `false`, there is no
reliability table, and nothing here is a validated positive-EV wager.

## Report contents

Total contracts and sides; evaluated / insufficient / not-evaluated / unpriceable counts; coverage and priceable-side
disposition by family; unpriceable reasons; evidence gaps; serious candidates (listed by kickoff then ticker, **not
ranked**); rejection reasons; alternate expressions (analyst thesis tags, plus a mechanical flag for any game whose
candidate has unevaluated same-thesis-group siblings); and historical **after-fee ROI** (settled accounted wagers,
game-clustered CI) and **CLV vs the closing mid proxy** by family. Executable (ask-side) CLV is not in the evidence join
and is reported `UNAVAILABLE`. History is context: no family is excluded, favoured or diversified into.

## Nov 7 early slate

`docs/reports/CFB_MARKET_AUDIT_2026-11-07_early.{md,json}`: 42 games, 3,992 contracts, 7,984 sides, all reconciled. 123
sides are unpriceable. **No analyst evaluations have been recorded, so 7,861 priceable sides are `NOT_EVALUATED` and the verdict
is `INCOMPLETE`.** Per-game ledgers are regenerated with `--ledger-dir`, not committed.
