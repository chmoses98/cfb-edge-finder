# Research Analytics

Descriptive measurement over the settled prospective corpus: model-market
gaps, side-aware closing-line value, calibration, fee-adjusted
one-contract economics, and slices by family, gap, timing and price.

**Research-only.** Nothing here recommends, qualifies, sizes, or selects.
No function returns a "best" anything — by construction, and enforced by
test.

## 1. Architecture

```
observations/{season}.jsonl  ─┐
                              ├─► analytics.dataset ─► AnalysisRow[] ─► slices ─► report ─► JSON/CSV/MD
attributions/{season}.jsonl  ─┘
```

| Module | Responsibility |
|---|---|
| `analytics/metrics.py` | per-observation gaps and side-aware CLV |
| `analytics/calibration_report.py` | binned calibration, Brier, log loss, ECE, market comparison |
| `analytics/uncertainty.py` | game-cluster bootstrap, sample-confidence labels |
| `analytics/dataset.py` | ledger join, provenance enforcement, health checks |
| `analytics/slices.py` | gap / price / timing / family cells |
| `analytics/report.py` | assembly and rendering |
| `scripts/analyze_prospective_research.py` | CLI |

Both ledgers are **read-only** and read **exactly once** each. Nothing
recomputes a historical snapshot with today's model or today's prices —
every row carries the model probability and executable prices actually
captured at that checkpoint. A metric derived from a re-priced snapshot
would be a backtest wearing a prospective label.

Every row keeps its linkage: `observation_key`, `attribution_key`,
`game_id`, `market_ticker`, `family`, `timing_label`, `model_version`,
`captured_at`, `settled_at`.

## 2. Model-market gap

```
yes_probability_gap = model_probability       - executable_yes_price
no_probability_gap  = (1 - model_probability) - executable_no_price
```

Each side is computed against **its own** quote. `executable_no_price` is
captured independently off the order book and is **not** `1 − yes_price`:
the live corpus quotes `yes=0.74` alongside `no=0.93`, summing to 1.67.
Both gaps can be negative at once; both can in principle be positive.

Deliberately named *gap*, not *edge*: it is a disagreement measurement,
not a claim of value.

## 3. Closing-line value — side-aware

```
YES CLV = closing_yes_price - entry_yes_price
NO  CLV = closing_no_price  - entry_no_price
```

Both read as *"did the thing I bought get more expensive?"*, which is the
side-correct question. A naive "the price went up so the market moved our
way" is wrong half the time, because a move that helps YES hurts NO by
the same amount.

Also emitted: `logit_movement` (clamped so a 0/1 quote — a real, tradeable
price — is not dropped or infinite), and `favorable`, which is `None` for
an exactly-flat close because zero movement is neither favorable nor
unfavorable.

### Closing quality is never zero-filled

A missing close is `available=False` with the reason preserved
(`CLOSING_MISSING_MARKET_CLOSED`, `_API_FAILURE`, `_NO_EXECUTABLE_QUOTE`,
`_MAPPING_FAILURE`, `_NO_SCAN_IN_WINDOW`, `CLOSING_NOT_APPLICABLE`).

**Zero is a real CLV value** meaning "the price did not move". Conflating
it with "we do not know" would drag every aggregate toward zero by exactly
the number of markets we failed to capture. CLV sample size is therefore
reported separately as `clv_n`, always alongside the slice's total `n`.

## 4. Fee-adjusted research economics

One contract, always. `RESEARCH_UNIT_CONTRACTS = 1`; a test fails if a
sizing parameter ever appears. Fields per side: entry price, estimated
fee (recomputed at **that side's** price), settlement value ($1 or $0),
gross unit P/L, fee-adjusted unit P/L, return on entry price.

`fee_adjusted_roi` for a slice is total fee-adjusted P/L over total entry
cost. Undefined (`None`) at zero capital rather than infinite. An unknown
fee yields `None`, never a silent zero.

## 5. Calibration

Binned in deciles by default. Per bin: count, mean predicted, observed
rate, calibration error (`observed − predicted`; positive =
under-confident). Plus Brier, log loss, ECE and max calibration error.

- **Empty bins are retained** with `count=0` and null statistics. Dropping
  them would hide that a probability range was never predicted at all.
- **ECE is observation-weighted**, so empty bins contribute nothing rather
  than diluting the error toward zero.
- **Log loss is clipped** at 1e-15. A confident-and-wrong boundary
  prediction would otherwise be infinite and swamp the aggregate. Stated,
  not hidden.
- A prediction of exactly 1.0 lands in the final bin rather than being
  dropped.

## 6. Market comparison

Model and executable market price are scored on **identical rows**, so
the comparison is paired by construction. `brier_difference` is
`model − market`; negative means the model scored better (Brier is a
loss), and it is named as a plain difference so the sign has to be read.

> **Caveat, attached to every comparison.** Executable Kalshi prices are
> not fair probabilities. They embed the bid/ask spread, taker fees and
> order-book microstructure — YES and NO on one contract routinely sum to
> well over 1. They are compared as a *transactable market benchmark*,
> not as a calibrated forecast.

No significance claim is made from a Brier difference alone.

## 7. Slices

| Dimension | Buckets |
|---|---|
| Signed gap | `<0%`, 0–2, 2–4, 4–6, 6–8, 8–10, 10–15, 15%+ |
| Absolute gap | 0–2, 2–4, 4–6, 6–8, 8–10, 10–15, 15%+ |
| Price | 0–10¢ … 91–99¢ |
| Timing | EARLY_OPEN → CLOSING |
| Family | moneyline, spread, total |

**Signed and absolute are both reported.** The `<0%` bucket (model below
market) stays a distinct population rather than being folded in with a
same-magnitude positive gap — model-above-market and model-below-market
are different hypotheses.

Every label is returned even when empty. An absent bucket is information:
it says the model never disagreed by that much.

**Family readiness travels with the numbers.** Totals carry *"WEAKER —
research primitive only; the totals model underperformed the naive
benchmark in Milestone C.2 backtesting"*, so a totals figure cannot be
read without its caveat.

## 8. Uncertainty — cluster bootstrap

Two dependence structures make contract rows far from independent:

1. **Game clustering.** One game produces a moneyline pair plus full
   spread and total ladders — 30+ contracts driven by one final score.
2. **Checkpoint clustering.** One contract is captured at nine
   checkpoints, all sharing one outcome.

Treating those as ~270 independent observations would shrink an interval
by roughly `sqrt(270)` against the truth — which is how a research system
talks itself into a signal that is one game.

Method: **nonparametric bootstrap resampling whole games with
replacement.** Resampling games keeps every contract and every checkpoint
together, preserving both structures without modelling either. Chosen
over analytic cluster-robust variance because it needs no distributional
assumption and degrades gracefully at small game counts. `game_id` is the
cluster because it is the coarsest and therefore most conservative unit.

Below **5 distinct clusters** the interval is withheld with a reason; the
point estimate is still reported. Seeded, so a report is reproducible.

The mean is bootstrapped on precomputed cluster `(sum, count)` totals —
`sum(drawn sums) / sum(drawn counts)` is algebraically identical to
re-meaning the concatenated values, and turns each iteration from `O(n)`
into `O(n_clusters)`.

## 9. Sample-size labelling

`n < 20` → `LOW_SAMPLE`; `n < 50` → `CAUTION`; `< 5` game clusters →
`LOW_SAMPLE` regardless of row count, because 300 contracts from 2 games
is a small sample wearing a large sample's clothes.

**Data is never suppressed** — confidence is labelled, and these labels
are not betting thresholds.

## 10. Multiple comparisons

Family × gap × timing × price × direction is well over a thousand cells;
some will look excellent by chance.

- Family-level totals and calibration are `CORE` (preregistered).
- **Everything else is `EXPLORATORY`**, carrying an explicit
  uncorrected-multiplicity caveat.

No parameter search for a best-looking subgroup exists anywhere. Slices
are returned in **fixed order**, never sorted by performance — asserted
by test.

## 11. Provenance and exclusions

- **Prospective-only** (`capture_mode == "PROSPECTIVE"`) is enforced on
  every admitted row; rejections are counted, not silent. Retrospective
  fixtures can never reach a headline ROI or CLV number.
- **Unsupported populations** (anything outside moneyline/spread/total —
  FBS-vs-FCS and FCS-vs-FCS remain `UNSUPPORTED_FOR_PRICING`) are
  partitioned into `diagnostic_rows`, inspectable but never mixed in.
- `SETTLEMENT_MISMATCH` rows are excluded entirely: a contract whose
  outcome is in dispute must not contribute to any metric.

## 12. Health checks

Counted per run: duplicate observation keys, duplicate attribution keys,
settlement mismatches, impossible probabilities, missing provenance,
malformed close links, unsupported leakage, non-prospective rejections,
malformed rows.

**Fatal** (exits non-zero, and the report is marked unreliable): duplicate
keys, settlement mismatch, unsupported leakage, impossible probability.

An **empty settled sample is not an error**. The run exits 0 and reports
`INSUFFICIENT NATURAL SETTLEMENT DATA YET` — exiting non-zero for a
legitimate corpus state would train everyone to ignore the exit code.

## 13. CLI

```
python scripts/analyze_prospective_research.py --season 2026
python scripts/analyze_prospective_research.py --season 2026 --family spread --timing T_24H
python scripts/analyze_prospective_research.py --season 2026 --side no --min-sample 30
```

Filters: `--family`, `--timing`, `--model-version` (all repeatable),
`--side`, `--min-sample`, `--captured-from`, `--captured-to`. Changing
what is analyzed never requires a code edit. The default run produces the
canonical full report.

Artifacts (`data/research/analytics/<season>/`): `analytics_summary.json`,
`analytics_slices.csv`, `analytics_report.md` — each carrying
`generated_at`, source paths, season, analytics code version, counts,
model versions, and the filters applied. Written under a separate
`analytics/` path so a regenerated report can never disturb the research
record it describes.

## 14. Scale

Measured, synthetic, 200 game clusters:

| Rows | Dataset build | Report build | Total | Ledger loads | Peak RSS |
|---|---|---|---|---|---|
| 1,000 | 0.03 s | 1.07 s | 1.10 s | 2 | 30 MB |
| 10,000 | 0.36 s | 1.34 s | 1.70 s | 2 | 73 MB |
| 100,000 | 7.72 s | 9.55 s | 17.27 s | 2 | 493 MB |

Linear in corpus size; exactly two ledger reads regardless of size.

## 15. Limitations

1. **No settled sample yet.** As of this writing the corpus contains zero
   settled supported prospective observations — no captured game has
   completed. Every metric is empty by construction.
2. **Executable prices are not probabilities** (§6). The market benchmark
   is transactable, not calibrated.
3. **One model version** is represented so far; cross-version comparison
   is not yet possible.
4. **Totals are a research primitive**, not a validated pricing model.
5. **CLV depends on closing capture.** Markets with no genuine close are
   excluded from CLV aggregates, so `clv_n` can be materially below `n`.
6. **Exploratory slices are uncorrected.** A single striking cell in a
   thousand-cell grid is not a finding.
7. **No threshold selection exists, deliberately.** Choosing a cutoff
   from these numbers is a separate, later mission.
