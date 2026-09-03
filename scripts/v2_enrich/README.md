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

## Weather (added after the runner fetch landed)

- `build_weather_features.py`: builds `wx_*` features per product from
  `weather/weather_{archive,hforecast,prevrun}.parquet` and runs the
  rolling-origin ablation. Only meaningful once the history is complete.
- `weather_residual_test.py`: the residual test actually reported in
  §11 of the report, because the time-guarded fetch delivered only part
  of 2025. Output: `docs/v2/enrichment/enr_weather_residual.json`.
- Fetched files + manifests live on the `research-data-v2enrich` orphan
  branch under `data/research_cache/v2_enrich/weather/`.
