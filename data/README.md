# data/

This directory is deliberately near-empty at this stage. See
`docs/STORAGE_STRATEGY.md` for the full reasoning.

Only compact, curated, git-appropriate files belong here (e.g. a small
canonical game-schedule snapshot, a model-version manifest). Raw Kalshi
captures, large historical datasets, play-by-play dumps, and repeated
price snapshots do NOT belong here -- `data/raw/` and `data/archive/` are
excluded via `.gitignore` specifically to prevent that by accident.

Nothing is generated here yet because Milestone B (data ingestion) has not
started.
