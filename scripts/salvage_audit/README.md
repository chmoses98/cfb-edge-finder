# Salvage audit scripts

The analysis behind `docs/CFB_FINAL_SALVAGE_VERDICT.md`. Research-only:
nothing here is imported by the collector, the scanner, or any production
path, and nothing writes to a research ledger.

## Inputs

Three checkouts, pointed at by environment variables (defaults are the
paths used when the verdict was produced):

| variable | branch | what is read |
|---|---|---|
| `SALVAGE_RDATA` | `research-data` | `data/research/v2/` (walk-forward V2 predictions, dataset, control predictions), `data/research_cache/v2/<season>/lines_*.json.gz` (CFBD per-book lines), the 2026 ledgers |
| `SALVAGE_RDATA_V2ENRICH` | `research-data-v2enrich` | the partial 2025 Open-Meteo weather sample |
| `SALVAGE_V2SRC` | `claude/cfb-model-v2-research-krupoc` `src/` | `research.v2.features.matchup_frame` and `research.v2.uncertainty` |

Python needs `pandas`, `pyarrow`, `numpy`, `scipy` (research-only; none is
a project dependency).

## Order

```
python3 build_master.py        # master.parquet + lines.parquet (6,266 games; per-book open/close/moneyline)
python3 a1_core.py             # spreads / totals / moneylines vs the close
python3 a2_open_move_ml.py     # opener, line movement, CLV, stacking, real-price moneylines
python3 a3_subgroups.py        # the 196-slice regime sweep with the Bonferroni bar
python3 a6_candidates.py       # the two candidate slices under scrutiny
python3 a7_sep_totals.py       # September totals forensics
python3 a5_residual_avoid.py   # market-residual model, avoid filter, calibration aid
python3 a4_prospective.py      # the 2026 ledgers: coverage, settlement, CLV, V2 retro-score
```

All scripts run from this directory (they write their parquet
intermediates next to themselves) and are deterministic given the seed in
`common.py`.

## What they must never become

A threshold, a bet list, or a card. The verdict they support is C; the
one registered hypothesis (§8 of the verdict) is a market anomaly with a
prespecified direction and kill rule, evaluated only on games captured
after the verdict's commit.
