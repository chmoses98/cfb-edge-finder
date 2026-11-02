# CFB postmortem fixes — 2026-11-02 (issue #248, PR #249)

Verified from `app/latest/{wagers,settlements}.json`: Oct 22–Nov 1 = 90 settled, stake $9,013.40, net **-$1,848.05**, fees $282.07,
GAME_SPREAD -$1,706.30, GAME_TEAM_TOTAL +$581.42. **Net is fee-inclusive** (`net = gross − stake`; stake already contains the
fee, `docs/SETTLEMENT_ECONOMICS.md`) — fees are not subtracted a second time. All 90 rows pass the stake/fee/net identities and the
per-order fee recompute (all 308 ledger rows reproduce Kalshi's per-order ceil-to-cent fee).

Owner unit: **1U = $75** → review flags above **$375/game** and **$1,125/day** (flags, never caps).

| deliverable | status | where |
|---|---|---|
| Per-contract reconciliation, MISSING flags, append-only store | done | `postmortem/contract_reconciliation.py` |
| Cross-category combos | reported separately as `COMBO_UNDECODED` (legs not recoverable from ticker/ledger) | `expression/ticker_ontology.py` |
| PIT guards (projection/availability/quote age, kickoff, price provenance) | **wired into RUN CFB** (`candidates` / `report`), default on, restriction-only | `decision/pit_guards.py`, `execution/pit_review.py` |
| Fee-inclusive break-even (per order), stated-lower-bound bet-up-to, calibration status | **wired**; additive fields on every candidate; `null` + reason when no uncertainty region; `qualified_positive_ev=false` (no reliability table exists) | `expression/fair_value.py`, `execution/pit_review.py` |
| Game/day exposure in units (>5U/game, >15U/day, no caps) | **wired** into the candidate artifact; proposal `NOT_ASSESSED` unless stakes supplied | `decision/exposure_aggregation.py`, `execution/pit_review.py` |
| RUN CFB contract + schema | `cfb_candidate_artifact/1.2.0` (additive); step 10a added to `RUN_CFB_CONTRACT.md` | `docs/RUN_CFB_CONTRACT.md` |
| Key-number-exact distributions + held-out comparison | done for game/team/total; 1Q/1H refused (no per-period data) | `projections/discrete_scoring.py`, `DISCRETE_SCORING_EVAL_2026-11-02.md` |
| Key-number calibration | **NOT_FIXED** under the pre-declared rule (aggregate gap closed; accuracy gain not shown). Model stays **unpromoted** | `research/discrete_scoring_eval.py` |
| Evidence join (recommendation link, fill ids, entry context, closing-price proxy / CLV) | done; coverage below | `postmortem/evidence_join.py`, `evidence_join_2026-10-22_2026-11-01.json` |
| Loss attribution | done; now with near-close proxies: 48 `INSUFFICIENT_EVIDENCE`, 2 `VARIANCE` | `postmortem/loss_attribution.py`, `LOSS_ATTRIBUTION_DASHBOARD_2026-11-02.md` |

## Evidence coverage (Oct 22–Nov 1, 90 settled wagers)
| evidence | found | missing |
|---|---|---|
| recommendation link | 0 | 90 (`UNKNOWN_NO_DECISION_RECORDS`: the private decision store is not in this repository) |
| order id / fill ids | 0 | 90 (`MISSING_NOT_IN_ROUTED_LEDGER`: the router payload does not carry them) |
| entry-time book (last catalog snapshot at or before the order) | 82 | 8 (cross-category combos) |
| closing proxy within 60 min of kickoff | 51 | — |
| closing proxy older than 60 min (`STALE_CLOSE_PROXY`, up to ~7 h) | 31 (not used as a close) | — |
| closing proxy | — | 8 (combos are not in the catalog) |

CLV against the closing-proxy mid (probability points; positive = market moved toward the position; intervals assume
independence so they are too narrow): **all 82: -2.3 [-4.1, -0.5]**; near-close 51: -3.2 [-5.9, -0.4]. By family:
**team totals -5.3 [-9.9, -0.6] (n=28)**, game spreads -0.8 [-1.4, -0.2] (n=27), game moneyline -1.3 (n=11). Team totals were the one
profitable family (+$581) while paying worse than the market's close — consistent with variance, not with a demonstrated edge.
The "close" is the last 30-minute catalog capture before kickoff (the repository holds no true close); the capture cadence has
gaps of hours, which is why the lag is reported per wager.

## Unresolved / needs owner
1. **Recommendation, model-run and fill ids.** The routed-ledger contract refuses recommendation/model provenance on accounting
   rows by design, so they can only ever live in a separate derived record, and that record needs the private decision store
   (`CFB_DECISION_STORE`) as input — run `scripts/cfb_evidence_join.py --decision-records <store>`. Order and fill ids need the
   router payload and importers to carry them (cross-repo change, kalshi-bet-router + the destination importers). Nothing is back-filled.
2. **True closing prices.** `research-data` stops at 2026-09-16; no branch holds Oct 22–Nov 1 per-checkpoint closes. Until the
   capture cadence is dense near kickoff, closes stay proxies with stated lag.
3. **A calibrated pregame probability** (a reliability table from settled outcomes) is what separates BAD_HANDICAP from BAD_PRICING.
   None exists, so no loss can be assigned to either and `qualified_positive_ev` stays false.
4. **Discrete model**: key-number mass is calibrated in aggregate but accuracy is not demonstrated; single season of data; it is
   not wired into RUN CFB and not promoted.
5. **EPA, special teams, injury effect sizes, per-period scoring** are absent from repo data; not modeled, not faked. 1Q/1H pricing stays refused.
6. **CFB has no confirmed-QB source**; the guard reports `QB_STATUS_NOT_TRACKED` instead of blocking everything.
