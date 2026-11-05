# CFB 1Q ladder + raw ESPN postgame archives (issue #264, research-only)

Two append-only archives on isolated orphan branches. Read-only, keyless, no model, no orders, no stakes.

## `cfb-1q-ladder-archive` (workflow `cfb-1q-ladder-capture.yml`)
Captures every Kalshi `KXNCAAF1QTOTAL` rung (all statuses: unopened/open/closed/settled-within-7d) plus Kalshi football milestones (start times).
- `runs/` manifest every run: sweep completeness per status, request log (path, status, bytes, **sha256 of the original response bytes**), counts, errors. A partial capture says so (`capture_complete=false`).
- `rows/` one row per market when its quote/status/contract fingerprint changed, plus a FULL board every 6 h; unquoted contracts are explicit `NO_QUOTE` rows. Each row: observed_at, exact contract (rules text, strike, close times, contract sha256), all bid/ask/size/volume fields, `eligible_pregame` + reasons, `lead_bucket`, orderbook depth for open markets starting within 3 h.
- `raw/` gzip of the original API pages whenever anything changed or on a FULL board.
- Eligibility is decided at observation time and **never back-dated**; Kalshi's start can be a placeholder (`time-tbd`), so `start_independently_verified` is false until the audit corroborates it with ESPN.
- Persists after kickoff (settlement fields captured on later runs).

## `cfb-espn-raw-archive` (workflow `cfb-espn-raw-archive.yml`)
Original ESPN `summary` payloads (drives, plays, boxscore kicking, period line scores) for completed FBS-involved games + an extraction index: drive list, first drive per team, field-goal attempts with **distance from the play text** (never inferred), box-score kicking totals reconciled against FG plays, final score reconciled against the play log, and explicit missingness codes. Records are POSTGAME: `pregame_evidence=false`, `available_from` = our fetch time.

## Audit
`scripts/audit_cfb_1q_ladders.py` verifies hashes, joins tickers to ESPN games by rules-text team names + date window (exactly one match), corroborates start time, checks orientation ("more than X ... in the 1st quarter ... resolves to Yes"), recomputes settlement from ESPN period-1 line scores and flags any contradiction with Kalshi's `result`, and builds the strict cohort (eligible pregame quote + verified identity/orientation/start + official score + consistent settlement). The baseline is descriptive: last eligible ask vs outcome; no fees, no fills, no edge claim.
