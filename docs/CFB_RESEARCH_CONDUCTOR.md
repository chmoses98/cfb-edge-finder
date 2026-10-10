# CFB research conductor

`.github/workflows/cfb-research-conductor.yml` → `scripts/cfb_research_conductor.py` → `cfb_edge_finder.control_prospective`.

The conductor is the prospective research clock for CFB. It is keyless: it uses only Kalshi's public `GET /markets`.
It writes only its own branch, `research-signals` (`research-signals-dev` from any other ref).

It is research only. It never places an order, sizes anything, or recommends anything.

## Why it exists

GitHub's cron is not a dependable clock here:

* The `*/30` catalog cron really fires every 2–6 h.
* A Script Engine 18:41 slot once ran at 23:17, after a 23:00 kickoff.

The 2026 CONTROL market study paid for that unreliability:

* **Missing prices.** 32 of 89 CONTROL games had no capture in the 60–180 minute window.
* **Missing `FINAL_PREGAME` rows.** No V2 `FINAL_PREGAME` row existed for any completed game.

The conductor fixes both. Each run cycles every 5 minutes for about 5.5 hours, then dispatches its successor
(`workflow_dispatch` from `GITHUB_TOKEN`). The `*/20` cron only revives a broken chain, and the concurrency group
keeps exactly one loop alive.

## One cycle

1. **Capture.** The conductor reads every open `KXNCAAFGAME` market once.
   * For every game whose **165 / 120 / 90 / 70**-minute slot is due inside **PRIMARY_60_180**, it appends an
     attempt row (`capture/<season>/attempts.jsonl`) and the side quotes (`capture/<season>/quotes.jsonl`).
   * A slot missed because the loop was down is caught up on the next cycle while the window is still open.
   * A game absent from the open list is confirmed against its own event before it is called absent.
   * A failed read is logged as `REQUEST_FAILED` for every due game, never as "no market".
2. **FINAL_PREGAME watchdog.**
   * When a game inside its final ~170 minutes has a V2 claim but no V2 `FINAL_PREGAME` ledger row, the conductor
     dispatches the CFB Script Engine.
   * Finished population games that are missing from the game log trigger a dispatch too, so settlement data arrives.
   * Dispatches are debounced for 40 minutes and never sent while a Script Engine run is queued or running. They
     are logged in `conductor/<season>/dispatches.jsonl`.
3. **Settle.** It writes the H1/H2 prospective settlement rows (`prospective/<season>/settlements.jsonl`),
   append-only and idempotent. See `docs/CONTROL_PROSPECTIVE_2026_PROTOCOL.md`.
4. **Publish.** It writes:
   * `signals/cfb_research_signals.json` (`cfb_research_signals/1.0.0`), which SIFT renders;
   * `reports/<season>/prospective_report.json`.

**Research side-steps.** Two research-only steps run between capture and the watchdog. Each runs inside its own
try/except, so a failure in either never stops the four steps above. SIFT reads neither.

* **Wave 2** (`wave2/`): `docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE2_PROTOCOL.md`.
* **Wave 2D Track B** (`run_defense/`): `docs/research/CFB_RUN_DEFENSE_PROSPECTIVE_PROTOCOL.md`.
  * Streams: CFB-MODEL-PROS-001, CFB-PROS-003, CFB-MECH-PROS-001.
  * P0 is rebuilt from a read-only copy of research-data's football state and preseason cache, which the workflow
    fetches once per job (`--p0-dir`). Without it, P0 is recorded UNAVAILABLE.

## Capture health (per game, per side)

| Status | Meaning | Whose problem |
|---|---|---|
| `CAPTURE_OK` | at least one valid executable YES ask in the window | — |
| `CAPTURE_PENDING` | the window has not closed yet | — |
| `MARKET_NOT_OFFERED` | Kalshi answered, and no game-winner market existed | market |
| `QUOTE_NOT_EXECUTABLE` | the market existed with no whole-cent ask in [1, 99] and status active/open | market |
| `PRICE_1_00` | the side's ask was $1.00 (no offer below a dollar) | market |
| `ORIENTATION_FAILURE` | markets existed, but the frozen matcher could not say which team each pays on | ours |
| `CAPTURE_SYSTEM_FAILURE` | the window closed with no successful attempt | ours |

Health statuses are never blurred: "the market did not offer it" is concluded only from an attempt that reached
Kalshi. The slate summary is `capture_health` in the signals contract.

## The research-signals contract (`cfb_research_signals/1.0.0`)

Every research number in the contract is read or computed, never typed in:

| Part | What it carries | Source |
|---|---|---|
| `study` | provenance | frozen #105 report and rows; hashes checked on load |
| `signals.moderate_control` | `VALUE_WATCH`, its wording, the initial basis (6–0, +32.5%, n = 6), prospective progress, the gate | report, prospective rows |
| `signals.strong_control` | `NO_EDGE`: a validated football signal; market approximately efficient so far | report |
| `signals.market_disagreement` | the 85¢ rule, the exploratory basis (STRONG under 85¢: 8–9, n = 17) and H2 progress | frozen rows, prospective rows |
| `capture_health` | the slate counts | capture rows |
| `games[]` | per game: event id, card line, quick read, 2–4 edges, compact claims, signal, compact historical range, the CONTROL side's current executable price (live read, or the catalog when the read failed, labelled), disagreement, capture status | V2 claims, frozen calibration in the claims, quotes |

Two statuses can be emitted by code: `VALUE_WATCH` and `REVIEW_REQUIRED`. Anything else comes only from a reviewed
`data/scripting/validation/control_prospective_2026/decisions.json`.

## Running it locally

```bash
python scripts/cfb_research_conductor.py --main-dir . --ledger-dir <ledger>/data/scripting/ledger \
    --store-dir /tmp/store --offline --now 2026-10-08T06:00:00Z
```

`--offline` skips the Kalshi read, so prices fall back to the committed catalog and are labelled `KALSHI_CATALOG`.
