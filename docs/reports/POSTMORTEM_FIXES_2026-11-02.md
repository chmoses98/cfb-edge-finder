# CFB postmortem fixes — 2026-11-02 (issue #248)

Verified from `app/latest/{wagers,settlements}.json`: Oct 22–Nov 1 = 90 settled, stake $9,013.40, net **-$1,848.05**, fees $282.07,
GAME_SPREAD -$1,706.30, GAME_TEAM_TOTAL +$581.42. **Net is fee-inclusive** (`net = gross − stake`; stake already contains the
fee, `docs/SETTLEMENT_ECONOMICS.md`) — fees are not subtracted a second time. All 90 rows pass the stake/fee/net identities and the
per-order fee recompute (all 308 ledger rows reproduce Kalshi's per-order ceil-to-cent fee).

| deliverable | status | where |
|---|---|---|
| Per-contract reconciliation, MISSING flags, append-only store | done | `postmortem/contract_reconciliation.py` |
| Cross-category combos | reported separately as `COMBO_UNDECODED` (legs not recoverable from ticker/ledger) | `expression/ticker_ontology.py` |
| PIT guards (projection/injury/QB/quote age, kickoff, price provenance) | done, restriction-only, **not yet wired into RUN CFB** | `decision/pit_guards.py` |
| Fee-inclusive break-even (per order), calibrated probability + interval, bet-up-to | done; `None` when uncalibrated | `expression/fair_value.py` |
| Game/day exposure in units (flags >5U/game, >15U/day, no caps) | done; unit is a required parameter | `decision/exposure_aggregation.py` |
| Key-number-exact distributions + held-out comparison | done for game/team/total; 1Q/1H refused (no per-period data) | `projections/discrete_scoring.py`, `DISCRETE_SCORING_EVAL_2026-11-02.md` |
| Loss attribution | done; **all 50 losses INSUFFICIENT_EVIDENCE** (no pregame probability, no closing quote, no recommendation link in ledger) | `postmortem/loss_attribution.py`, `LOSS_ATTRIBUTION_DASHBOARD_2026-11-02.md` |

## Unresolved / needs owner
1. **Attribution gap**: 308/308 wagers have null `recommendation_id`, `model_run_id`, `model_price_id`, order id, fill ids. Needs the router/decision-store to deliver them; nothing is back-filled.
2. **Closing quotes** for Oct 22–Nov 1 are not in this repository (execution snapshots start 2026-10-31). Supply `--closing-quotes` to get CLV and the VARIANCE vs handicap/pricing split.
3. **Wiring**: guards, fair-value and exposure flags are libraries; enabling them in RUN CFB changes the live path and needs explicit authorization. The discrete model is not promoted.
4. **Unit size** for the 5U/15U flags must be confirmed (docs example is $70).
5. **EPA, special teams, injury effect sizes, per-period scoring** are absent from repo data; not modeled, not faked.
