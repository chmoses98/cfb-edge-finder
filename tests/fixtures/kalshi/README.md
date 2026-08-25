# Kalshi CFB live-capture fixtures (Milestone D)

These JSON files are **genuine field values** captured from real, live
`GET https://api.elections.kalshi.com/trade-api/v2/markets/...` responses
during this milestone's manual GitHub Actions workflow runs
(`.github/workflows/validate-kalshi-cfb-live.yml`, scripts
`validate_kalshi_cfb_live.py` and `validate_kalshi_market_detail_live.py`).
The game is Southern Utah at Montana, originally scheduled 2026-08-29.

They are **not** a raw, unfiltered dump of the full API response (which
would include unrelated bookkeeping fields and is unnecessary for this
project's fixtures) -- each file keeps only the fields this codebase's
Kalshi modules (`contract_semantics.py`, `price_extraction.py`) actually
read, with real values, so tests exercise real evidence rather than a
synthetic guess at the schema. `captured_at_utc` on each file records
when this session observed the value; prices in particular are a live
quote and will not match a later live fetch.

- `spread_market_suu5.json` -- one rung of the Southern Utah @ Montana
  spread ladder (Southern Utah wins by over 4.5).
- `total_market_81.json` -- the game total market (over 80.5).

Southern Utah and Montana are both FCS programs -- this pair is real,
live evidence of a `MAPPED_UNSUPPORTED_POPULATION` case for the C.2
model (which only prices FBS-vs-FBS), not a fabricated example.
