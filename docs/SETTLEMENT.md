# Research Settlement & Outcome Attribution

Resolving every captured research observation to an official game result,
an exact contract settlement, hypothetical research-unit economics, and a
closing-price linkage — with explicit states for everything that cannot
be resolved.

Research infrastructure only. No recommendation, qualification, staking,
or order placement, and none may be added under cover of it.

## 1. Architecture

Two ledgers, deliberately separate, both append-only:

| Ledger | Grain | Path | What it answers |
|---|---|---|---|
| `MarketSettlement` | one per (game, ticker) | `data/research/settlements/{season}.jsonl` | How did this contract settle? |
| `ObservationAttribution` | **one per captured observation** | `data/research/attributions/{season}.jsonl` | What did that outcome mean for *this snapshot*? |

**Why both.** A market's outcome is a single truth no matter how many
times we looked at it, so `MarketSettlement` is correctly one row per
market. But that is not the research record. The same contract is
captured at `EARLY_OPEN`, `T_7D`, `T_3D`, `T_24H`, `T_6H`, `T_90`,
`T_60`, `T_30` and `CLOSING`, and each snapshot has its own entry price,
model probability and fee. Collapsing them onto one settlement row would
destroy exactly the timing dimension the collection regime was built to
capture — you could no longer ask *"did the T_24H price beat the
close?"*, because there would be one row per market.

Settlement is kept out of prediction, market discovery, prospective
capture, and recommendation. It consumes a captured observation, an
authoritative final result, and the contract semantics stored **at
observation time**, and produces a deterministic record.

**Nothing mutates a captured observation.** Ever.

## 2. Result source

CFBD `/games`, via `research/settlement.extract_game_result`, read
defensively across candidate field names.

Captured per game: `game_id`, final home/away points, completion status,
overtime indicator (currently `None` — CFBD's `/games` response does not
expose it; recorded as genuinely unknown rather than guessed), derived
margin and total, `captured_at`, and source provenance.

**A game is not final because kickoff passed.** `FINAL` requires either
an explicit final/completed status string, or `completed: true` *with*
both scores present. `completed: true` alone is not enough, and there is
a test pinning that.

## 3. Contract resolution

All three families settle from the semantics **persisted on the
observation** (`family`, `threshold`, `side`, `team`,
`semantic_operator`) — never re-parsed from the ticker at settlement
time. That protects against later parser changes, ticker-grammar changes,
and market-metadata changes.

| Family | Rule | Boundary |
|---|---|---|
| Winner | `contract_team == actual_winner` | Settles on the **final** score; overtime changes nothing |
| Spread | `team_margin > threshold` | **Strict `>`**, never `>=` |
| Total | `final_total > threshold` | **Strict `>`**, never `>=` |

Any `semantic_operator` other than `>` is refused as
`UNSETTLEABLE_UNKNOWN_OPERATOR` rather than guessed.

**Pushes are structurally impossible, not modelled away.** Every
threshold in the real captured corpus is a half point (2.5, 3.5, 4.5,
29.5, 32.5, 35.5, 67.5 …), so a tie against the line cannot occur with
integer scores. There is no sportsbook-style push branch, and a test
asserts the fixture still shows only half-point lines. Strictness is
nonetheless verified directly with a constructed integer threshold, so
`>` versus `>=` is proven rather than assumed unreachable.

## 4. Kalshi cross-check

Read-only. `research/kalshi_settlement_check` fetches market metadata and
compares Kalshi's own finalized result to ours.

- Only `finalized` / `settled` / `determined` count as finalized.
  `closed` does **not** — a closed market has stopped trading but may
  carry no settlement yet.
- A result on a non-finalized market is not trusted as official.
- `void` / `all_yes` / `all_no` are voids, not settlements. Collapsing
  them would silently turn a voided market into a losing contract.
- An unrecognised result string yields `None` with the reason preserved,
  never a coerced side.
- A fetch failure is `fetch_failed=True`, never "no settlement" — the same
  distinction market discovery had to learn when an HTTP 429 was silently
  read as an empty series.

**A mismatch is a defect, not a data point.** Our derivation and Kalshi's
are two independent routes to the same fact; disagreement means one is
wrong, most likely our stored semantics, and every conclusion drawn from
that contract is suspect. So a mismatch is never written as a normal
settled record: state becomes `SETTLEMENT_MISMATCH`, both values are
retained as evidence, **no economics are computed**, and the run exits
non-zero at HIGH severity.

**Absence is never disagreement.** `None` on either side is not evidence
of agreement or mismatch.

## 5. Settlement states

`SETTLED_YES`, `SETTLED_NO`, `GAME_NOT_FINAL`, `MARKET_NOT_FINAL`,
`GAME_CANCELLED`, `GAME_POSTPONED`, `RESULT_UNAVAILABLE`,
`SEMANTICS_UNRESOLVED`, `MAPPING_UNRESOLVED`, `SETTLEMENT_MISMATCH`,
`NOT_APPLICABLE_UNSUPPORTED_POPULATION`.

Every eligible observation resolves to exactly one. There is no silent
missing settlement.

**Only terminal states are persisted.** Writing a `GAME_NOT_FINAL` row
would permanently consume that observation's attribution key and prevent
the real settlement from ever being recorded. `GAME_NOT_FINAL`,
`MARKET_NOT_FINAL`, `RESULT_UNAVAILABLE` and `GAME_POSTPONED` are
transient and revisited on the next run. `SETTLEMENT_MISMATCH` is
deliberately **not** terminal — it is a defect to investigate, and must
be allowed to resolve.

## 6. Idempotence and duplicate protection

`attribution_key = observation_key + "|" + settlement_code_version`.

Re-running unchanged code produces the same key, so the append path
rejects it: **a re-run writes zero duplicates**, byte-for-byte. Including
the code version means a genuine settlement-logic revision appends a
**new** conclusion alongside the old one rather than silently overwriting
a previous research finding — the amendment mechanism, tested.

Rigor matches prospective capture: exact set membership, no probabilistic
structure, existing lines never rewritten or reordered.

## 7. Research-unit economics

**This is measurement, not betting.** A contract settles at exactly $1.00
or $0.00, so *"what would one contract bought at the price we actually
observed have been worth?"* has an exact answer. That number is the
primitive later calibration work needs. It is not a wager, not a
position, not a recommendation; nothing sizes, aggregates, or optimizes.

- **Unit: exactly 1 contract**, always. `RESEARCH_UNIT_CONTRACTS = 1`, and
  a test fails if `research_unit_economics` ever gains a sizing parameter.
- YES unit → $1.00 if the contract's condition held; NO unit → $1.00 if it
  did not.
- Fields: `entry_price`, `settlement_value`, `research_unit_pnl`,
  `estimated_fee`, `fee_adjusted_research_unit_pnl`,
  `return_on_entry_price`.
- Return on a zero entry price is `None` (undefined), never infinity.
- **Unresolved means `None`, not zero.** A zero P/L would read as "broke
  even" when the truth is "we do not know".

**YES and NO are independent quotes.** The NO-side fee is recomputed from
the fee schedule at the **NO price**, not borrowed from the YES side.
Kalshi's taker fee is proportional to `P(1-P)` and therefore symmetric,
which makes borrowing tempting — but `executable_no_price` is captured
independently off the book and is not generally `1 - yes_price`. The real
corpus proves it: one captured contract shows `yes=0.74` alongside
`no=0.93`, summing to 1.67. A test asserts the fixture still demonstrates
this.

## 8. Closing linkage

Each attribution links to its own market's genuine `CLOSING` row:
closing YES/NO price, midpoint, model probability, `captured_at`, and the
closing observation key.

`build_closing_link` **raises** if handed any other checkpoint — a T_30
can never be silently promoted to a close. When no close exists, the
record carries the explicit `CLOSING_MISSING_*` reason.

**A missing close never blocks settlement** (it is reported at INFO), but
it stays visible because CLV analytics later depends on it.

Post-kickoff prices cannot appear here at all: the collector never
captures a `CLOSING` row at or after kickoff.

## 9. CLV primitives, deliberately ungraded

Persisted: entry YES/NO price, closing YES/NO price, entry model
probability, closing model probability, and both timestamps.

**No CLV grading, no edge thresholds, no profitability conclusion, no
recommendation.** A test scans the record's own field names and fails if
anything matching `clv` / `edge` / `roi` / `beat` / `grade` appears. The
next milestone interprets these fields; this one only preserves them.

## 10. Workflow and incremental indexing

`.github/workflows/research-settlement.yml`, cron `0 */6 * * *`, two
steps: market outcomes then per-observation attribution.

**Why not the collector's 10-minute cadence.** Prospective capture is
racing a market — a closing line exists for fourteen minutes and is gone
forever. Settlement is racing nothing: a final score does not expire, and
an observation settled six hours after kickoff carries the same research
value as one settled six minutes after. Matching the collector would
multiply CFBD and Kalshi load 36× to make a permanent fact available
slightly sooner.

**Incremental by construction.** Both ledgers are loaded exactly **once**
per attempt — the corpus in one pass, the attribution ledger via
`load_attribution_index`. "Already attributed?" is then an O(1) set
lookup, so a run is `O(observations + attributions + work)`, not the
nested `O(observations × attributions)` scan a naive implementation
produces. This repo has already paid once for that mistake in the capture
path (see `docs/PERFORMANCE.md`) and is not repeating it. Game results and
market settlements are each derived once and reused across every
checkpoint of that market.

Indexes are built per **attempt**, not per process, so the git
fetch-reset-retry loop dedups against genuinely fresh content.

Concurrency: shares the `research-data-write` group with every other
corpus writer, `cancel-in-progress: false`.

## 11. Health checks

Reported per run: observations scanned, unsettled eligible, games checked,
games newly final, attributions written, duplicate attempts, each state
count, closing captured/missing, API failures, runtime.

- **HIGH (fails the run)**: settlement mismatch; API failures; persistence
  failures.
- **WARNING**: games expected final but none resolved; elevated
  `SEMANTICS_UNRESOLVED` rate (≥5%).
- **INFO**: unmapped observations; settled observations with no close.

Zero newly-final games is normal midweek and does not fail; zero when
games *were* expected to have finished warns.

## 12. Persistence policy

Append-only. The observation corpus is never overwritten. Settlement
corrections use the explicit amendment mechanism (a new
`settlement_code_version` producing a new `attribution_key`), never silent
mutation — so a superseded conclusion remains inspectable next to the one
that replaced it.

## 13. Validation

**Historical / deterministic.** `tests/fixtures/real_captured_observations.jsonl`
holds 16 **real** rows from the live corpus — real Kalshi tickers, real
thresholds, real parsed operators, real executable prices — covering all
three families. Settlement runs against those, with final scores varied
per test to drive each side of every boundary. Winner YES/NO, away
mirror, overtime, spread strict boundary, spread ladder monotonicity,
total strict boundary, unknown operator, missing threshold, all void/
non-final states, mismatch, and both economics sides are covered.

**Live.** Two consecutive real workflow runs against the live corpus,
both green:

| | Run 1 ([33103859696](https://github.com/chmoses98/cfb-edge-finder/actions/runs/33103859696)) | Run 2 ([33103950959](https://github.com/chmoses98/cfb-edge-finder/actions/runs/33103950959)) |
|---|---|---|
| Observations scanned | 1,724 | 1,724 |
| Unsettled eligible | 930 | 930 |
| Games checked / newly final | 86 / 0 | 86 / 0 |
| **Attributions written** | **794** | **0** |
| **Duplicate attempts** | **0** | **0** |
| `GAME_NOT_FINAL` (held, not persisted) | 930 | 930 |
| `NOT_APPLICABLE_UNSUPPORTED_POPULATION` | 794 | 0 |
| Settlement mismatches / API failures | 0 / 0 | 0 / 0 |
| Runtime | 2.71 s | 2.89 s |

Run 2 proves both idempotence and the incremental index at once: it wrote
nothing, and `unsupported_population` fell from 794 to **0** because those
rows were excluded from the pending set by the O(1) index lookup *before*
being re-derived — not merely rejected at the append. The 930 eligible
observations correctly stayed `GAME_NOT_FINAL` across both runs without
consuming their attribution keys.

Ledger after: 794 rows, 794 unique `attribution_key`s, 0 duplicates, 0
malformed. The observation corpus is unchanged at 1,724 rows.

**Live settlement of a completed captured game is PENDING.** The earliest
captured kickoff is 2026-08-29; no captured game has finished, so
`games_newly_final` is legitimately 0 and `settled_yes`/`settled_no` are
both 0. No live settlement was fabricated to fill that gap — the
scheduled 6-hourly workflow will produce it once Week 1 completes.

## 14. Known limitations

1. **Overtime indicator is always `None`.** CFBD's `/games` response does
   not expose it. Recorded as unknown rather than inferred. Contract
   settlement is unaffected — every family settles on the final score.
2. **Kalshi cross-check coverage is unproven live.** The code path is
   fully unit-tested, but no captured market has finalized yet, so the
   derived-vs-official invariant has not been exercised against a real
   Kalshi settlement.
3. **`MARKET_NOT_FINAL` gating depends on Kalshi reachability.** With
   `--skip-kalshi-crosscheck`, settlement proceeds on the official score
   alone and that state cannot arise.
4. **Void semantics are recorded, not reconciled.** A Kalshi void on a
   game CFBD reports as final is preserved as evidence; deciding which
   source wins is deliberately left to a human.
