# V2 data-enrichment research scripts

Research-only. These scripts ran against the frozen V2 artifacts
(`data/research/v2/dataset_d025.parquet`, `preds/ens_margin_d025_eq.parquet`,
`preds/market_close.parquet`, `preds/tot_eff_ridge_d025_affine.parquet` on
`research-data`) plus free sources fetched with zero CFBD calls
(sportsdataverse `cfbfastR_cfb_pbp` and `ncaa_mfb_*` release parquet;
Open-Meteo via `scripts/v2_fetch_weather.py`). Paths inside the scripts
point at the scratch layout they were run from (dataset/preds/cache
directories side by side); `enrich_eval.py` reuses
`cfb_edge_finder.research.v2` unchanged so every challenger runs on V2's
exact folds. Results: `docs/v2/enrichment/enr_<family>.json`.
