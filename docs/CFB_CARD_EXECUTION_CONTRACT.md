# CFB card contract — October 17 process repair

## Mandatory sequence
1. Freeze the user-requested kickoff window, enumerate all physical games, and reconcile against the board.
2. Inventory all contracts by game, family, period, side, settlement rule and liquidity. Distinguish unavailable from unchecked.
3. Audit opponent strength, opponent-adjusted efficiency, injuries, weather and availability of each input; no rankings without credible schedules.
4. Write an independent game thesis, three distinct scripts and explicit counter-case **before** selecting contracts. Do not fabricate calibrated script probabilities.
5. Compare candidate expressions including 1Q, 1H, team totals, spreads, alternate lines and props where present; record rejections.
6. Price only from independently calibrated and backtested distributions. Never treat a Kalshi ask as a model fair probability.
7. Require <=15-minute executable quote, sufficient size, fee-aware edge and exact YES/NO settlement semantics.
8. Size at 0, 1, 2, 3, 4 or 5 units, with 1U=$75 and correlated game/thesis exposure limits; do not round arbitrary subunit stakes into conviction.
9. Publish one ranked table (bet, maximum price, stake, game script, counter-case) with all qualified plays, plus passes, ladder and parlay analysis.
10. Version any revision, naming the precise new evidence and stake delta.

## Blocking behavior
The board precheck in `scripts/check_cfb_execution_gate.py` is an **initial safety guard**, not a complete implementation of steps 1–10. It blocks research-only authority, missing games, started games, unpriced games, unhealthy inputs and stale quotes. It must not be bypassed for a betting card. Other steps require additional machine-readable evidence and tests before a production execution-card publisher may be enabled.

## October 17 noon/12:30 ET retrospective dry run
The published board at 13:33Z has `bet_authority=RESEARCH_ONLY`, `markets_priced=0`, `NO_MODEL_PRICES`, and `market_captured_at=08:00:01Z`. This fails authority, calibrated price and quote freshness gates. **Verdict: BLOCKED.** The old chat card is an unverified research shortlist, not a validated betting card. Do not reuse its old 43c/56c ladder prices or bet-up-to thresholds as executable values.
