# Model retirement — 2026 market-discovery pivot

**Status: the college-football projection model is retired from the LIVE
path. Nothing has been deleted.**

The prior projection and data research did not produce a model trusted for
wagering decisions. Rather than recalibrate or repair it, this pivot removes
model-generated game probabilities from the live betting workflow entirely
and replaces the live path with a **market inventory**: for every upcoming
physical college-football game, the complete set of Kalshi markets, their
raw contract semantics, their executable prices, and honest diagnostics
about how complete that capture is.

The question the repository now answers is narrow and answerable:

> For this physical college-football game, what EXACTLY can I bet on
> Kalshi right now?

It no longer answers "and what is it worth" — that judgement moves outside
this repository, to a human or an external handicapper reading the catalog.

---

## The new live path

| Component | Purpose |
|---|---|
| `.github/workflows/kalshi-market-catalog.yml` | The only scheduled writer. Refreshes the catalog and commits on change. |
| `scripts/build_kalshi_cfb_catalog.py` | Entrypoint. No credentials of any kind. |
| `src/cfb_edge_finder/catalog/` | Discovery, classification, mechanics, artifacts. |
| `data/live/cfb_market_catalog.json` | **Primary product.** The slate index: one entry per physical game, pointing at its detail file. |
| `data/live/games/<game_key>.json` | One game's complete contract inventory. |
| `data/live/cfb_catalog_status.json` | Small status/fingerprint file for change detection. |
| `docs/RUN_CFB_CONTRACT.md` | What a consuming session must do, including the completeness gate and the freshness rule. |

**No secret is required.** Kalshi's market-data endpoints are public reads,
and physical-game identity comes from Kalshi's own `football_game`
milestones — so the live path needs no `CFBD_API_KEY` and no Kalshi
credential. `tests/test_catalog_schema_and_isolation.py` asserts that the
catalog package and its entrypoint reference neither.

**No model can reach it.** The same test file asserts the catalog package
imports nothing from `modeling`, `projections`, `recommendation`,
`decision`, `sizing` or `analytics`, and that no model-shaped key
(`fair_probability`, `expected_margin`, `model_edge`, `kelly`, …) appears
anywhere in the published artifact. `catalog` is also on the guarded side
of the sizing lock (`tests/test_sizing_disconnection.py`).

---

## What was hibernated, and what that costs

Four scheduled workflows fed or served the projection model. Their
`schedule:` blocks are **commented out, not deleted**; `workflow_dispatch`
still works, so any of them can be run by a human today, and re-enabling a
schedule is one uncomment.

| Workflow | Was | Why hibernated |
|---|---|---|
| `research-capture.yml` | `*/10 * * * *` | Feeds the model: CFBD history → ratings/priors → score model → prospective capture. ~4,300 runs/month. |
| `research-collection-conductor.yml` | `17 * * * *` + self-relighting chain | Exists only to dispatch `research-capture` more reliably than cron does. |
| `research-settlement.yml` | `0 */6 * * *` | Settles and attributes model-era research wagers. |
| `live-info-sidecar.yml` | `17 */6 * * *` | Research-only sidecar. |

### The one genuinely irreversible consequence

**Closing lines stop accruing while `research-capture` is hibernated, and
they cannot be recovered after kickoff.** Every other observation the
research corpus collects can in principle be rebuilt later from historical
data; a closing line cannot. `docs/QUIET_HIBERNATION.md` recorded that as
the single reason the collector was still running at all — to accumulate
clean prospective evidence for a possible end-of-season review.

This is a deliberate trade, and it is the owner's call rather than a
technical one:

- **If the end-of-season review still matters**, re-enable
  `research-capture.yml`'s schedule (uncomment the `schedule:` block). It
  is independent of the catalog and does not touch `data/live/`. Consider
  a cheaper cadence than `*/10` if only closing lines matter.
- **If it does not**, leave it hibernated. Nothing in the live path
  depends on it, and the corpus already collected stays exactly where it
  is.

Note that the catalog itself captures a far wider market surface than the
research collector ever did (every family Kalshi lists, not three), so
from the pivot forward it is also the better raw record — but it is a
change-detected snapshot of the current surface, **not** a checkpointed
pre-kickoff observation series, and it is not a substitute for a closing
line captured at a known offset from kickoff.

---

## If CLV research is reintroduced later

**The old collector stays hibernated. Do not re-enable it for this.** We
accept the loss of its future projection-specific closing observations.

That collector computed closing-line value against the retired projection
model's own notion of a fair line, which is why it needed the model, the
CFBD history fetch and a checkpoint schedule built around them. A CLV study
does not actually require any of that. The measurement that matters is
model-free:

    CLV  =  the price you actually paid for a contract
            vs
            that same contract's final pre-kickoff Kalshi executable price

Both sides are market observations. Neither needs a projection.

If that work is picked up, implement it against the **new catalog**:

- the wager side comes from the existing wager ledger
  (`src/cfb_edge_finder/accounting/`), which already records what was paid;
- the closing side comes from a catalog capture taken close to kickoff —
  the same `data/live/games/<game_key>.json` contracts, joined on
  `market_ticker`, using the executable price (`yes_ask`/`yes_bid`, not the
  mid) and netting Kalshi's fee from `mechanics`;
- `occurrence_datetime` and `close_time` on every contract say when
  "pre-kickoff" ends, so the capture window is defined by the data rather
  than by a separate schedule.

**Two things such a system would need that the catalog does not do today**,
stated plainly so nobody assumes otherwise:

1. The catalog is a change-detected snapshot of the CURRENT surface. It is
   not a checkpointed series captured at a known offset from kickoff. A CLV
   study needs a deliberate near-kickoff capture — a small addition to the
   catalog's cadence, not a revival of the old collector.
2. Once a game kicks off it leaves the published horizon and its detail
   file is pruned. A CLV study must persist the closing capture it cares
   about at the time, rather than expecting to read it back later.

It should NOT require the retired CFB projection model, and it must not
become a route to reintroduce one onto the live path.

**This mission does not implement any of it.**

---

## What is preserved, untouched

Nothing was deleted. Specifically retained:

- **All research code**: `src/cfb_edge_finder/research/`,
  `src/cfb_edge_finder/modeling/` (including `modeling/v2/`),
  `projections/`, `analytics/`, `recommendation/`, `decision/`,
  `expression/`, `sizing/`.
- **All research data** under `data/` and on the `research-data` durable
  branch.
- **All research documentation**: every `docs/MILESTONE_*.md`,
  `docs/MODEL_REPAIR_2026.md`, `docs/PRESEASON_PRIOR_RESEARCH.md`,
  `docs/EMPIRICAL_RESEARCH_GATE.md`, `docs/QUIET_HIBERNATION.md`, and the
  Kalshi market audit that preceded this pivot
  (`docs/KALSHI_CFB_MARKET_AUDIT.md`).
- **All tests** for the above — the full suite still runs and still passes.
- **Every manual workflow**, all still dispatchable:
  `backtest-cfb-baseline-live.yml`, `preseason-research-fetch.yml`,
  `research-weekly-report.yml`, `validate-cfbd-live.yml`,
  `validate-kalshi-cfb-live.yml`, `capture-resilience-probe.yml`,
  `settlement-source-probe.yml`.

`docs/KALSHI_CFB_MARKET_AUDIT.md` and
`src/cfb_edge_finder/kalshi/cfb_market_family_registry.py` deserve a
specific note: they remain accurate as a record of *what was researched in
2026*, and the registry's `CORE_V1`/`LATER_GAME_MODEL` priorities remain a
reasonable description of *what a model would have priced first*. They are
**no longer a description of Kalshi's market surface** — that registry knew
four college-football series where the live exchange carries 145. The
catalog does not consult it, and the classifier in
`src/cfb_edge_finder/catalog/classification.py` supersedes it for
labelling.

---

## How to revive any of it

```bash
# Re-enable a hibernated schedule: uncomment the `schedule:` block.
$EDITOR .github/workflows/research-capture.yml

# Or just run one now, without changing anything:
#   Actions -> "Research Capture (scheduled scanner)" -> Run workflow
```

Two guards will notice if a schedule comes back, by design rather than by
accident:

- `tests/test_trigger_reliability.py::test_the_live_catalog_is_the_only_scheduled_writer_now`
  fails, naming the workflow. That is the intended prompt to confirm the
  revival was deliberate — update the test in the same change.
- `scripts/week1_readiness.py::cron_interval_minutes` reports the cadence
  again. It now ignores commented-out `cron:` lines; before this pivot it
  matched them anywhere in the file, and so would have gone on reporting a
  live 10-minute cadence for a collector that no longer ran.

---

## Actions and storage, before and after

| | Before | After |
|---|---|---|
| Scheduled runs/week | ~1,050 (`*/10` collector + hourly conductor + 6-hourly settlement + 6-hourly sidecar) | ~150 (catalog only) |
| Secrets consumed on a schedule | `CFBD_API_KEY` | none |
| Commits on an unchanged surface | one per run (timestamps churn) | **zero** — content fingerprint excludes capture time and clock-derived mechanics |
| Failure emails | a red run per reset socket | only a genuinely unusable capture (zero games, or the builder crashing) |

The catalog polls the window that actually matters *more* often than a flat
hourly schedule would (every 30 minutes Thursday 18:00 → Sunday 06:00 UTC)
while spending roughly a seventh of the runs, because the rest of the week
drops to 6-hourly.
