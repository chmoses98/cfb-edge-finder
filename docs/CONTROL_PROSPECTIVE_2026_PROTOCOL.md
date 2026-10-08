# CONTROL × Kalshi game-winner: prospective tracking protocol (2026)

**Status: PRE-REGISTERED.** The machine-readable protocol is
`data/scripting/validation/control_prospective_2026/protocol.json`. Its sha256 is pinned in
`cfb_edge_finder.control_prospective.PROTOCOL_SHA256`, and `verify_protocol` refuses any other file.

The protocol was committed before any game in its population kicked off. The first V2 `FINAL_PREGAME`-eligible
kickoff is 2026-10-08T23:00Z. No prospective quote, settlement or outcome was read to write it.

This is research only. Nothing derived from it is a probability, a fair price, a bet-up-to price, a stake or a
recommendation.

## Why it exists

The frozen 2026 CONTROL market study (`docs/CONTROL_2026_MARKET_RESULTS.md`, chmoses98/cfb-edge-finder#105) left
two loose ends:

* **It was retrospective on the football side.** No V2 `FINAL_PREGAME` row existed for a completed game.
* **It priced only 45 of 89 CONTROL games.** For 32 of the misses, our pipeline captured nothing in the window.

MODERATE CONTROL went 6–0 in that priced sample, with fee-adjusted ROI of about +32.5%. That is interesting, but
six games prove nothing. This protocol freezes, before any future outcome, exactly how that idea and one other are
re-tested.

## Population

The population is every game that has a V2 ledger `FINAL_PREGAME` row recorded strictly before kickoff, with
kickoff on or after 2026-10-08T12:00Z.

* The game's row is the **last** such row.
* Its CONTROL tier is copied verbatim and never recomputed.
* A game without `FINAL_PREGAME` is a counted pipeline miss (`NO_FINAL_PREGAME`). It is never imputed from a
  `PUBLICATION` row.

## Entry price

The entry rule is identical to the frozen market protocol: the **last valid executable YES ask** of the CONTROL
team's own `KXNCAAFGAME` market inside **PRIMARY_60_180**, i.e. `kickoff−180 min ≤ captured_at ≤ kickoff−60 min`.

* There is no price cap.
* NO is never `1 − YES`.
* Missing stays missing.

Quotes come only from the research conductor's own prospective capture. It makes resilient attempts at 165, 120,
90 and 70 minutes before kickoff. A missed moment is caught up while the window is still open.

## Hypotheses (exact wording)

* **H1:** "MODERATE CONTROL, home and away pooled, purchased at the frozen PRIMARY_60_180 executable game-winner ask
  at all available prices, has fee-adjusted ROI > 0."
* **H2:** "STRONG CONTROL priced below 85¢ underperforms its fee-adjusted break-even rate."

Rules for both:

* **H1 slices.** HOME and AWAY are descriptive slices only.
* **H2 threshold.** Fixed at 85¢ (an entry ask < 0.85).
* **H2 status.** It is tracked, and it is not a rule of any kind.

## Review checkpoints

| priced, settled n | read |
|---|---|
| 10 | EARLY READ ONLY |
| 20 | INTERIM RESEARCH READ |
| 30 | MEANINGFUL EARLY SAMPLE |
| 50 | PRIMARY REVIEW |

## Research status and the promotion gate

The allowed statuses are `VALUE_WATCH`, `EDGE_CONFIRMED`, `NO_EDGE`, `INSUFFICIENT_DATA` and `REVIEW_REQUIRED`.

* **MODERATE CONTROL** starts as `VALUE_WATCH`. Code may only ever move it to `REVIEW_REQUIRED`, which happens
  when either condition holds:
  * H1 n ≥ 30, ROI > 0, capture coverage is healthy, **and** the football relationship is still coherent; or
  * H1 n ≥ 50.
* **"edge criteria met"** is a flag on `REVIEW_REQUIRED`. It requires all of:
  * n ≥ 50;
  * ROI > 0;
  * bootstrap 95% lower bound > 0;
  * each of HOME and AWAY ≥ 10;
  * at least 3 weeks;
  * no protocol deviation.

  The flag is **never** `EDGE_CONFIRMED`.
* **`EDGE_CONFIRMED`, `NO_EDGE` and `INSUFFICIENT_DATA`** for MODERATE come only from a reviewed entry in
  `decisions.json`.
* **STRONG CONTROL** is `NO_EDGE`: a validated football signal, with the market approximately efficient so far.
* **MARKET DISAGREEMENT** is `INSUFFICIENT_DATA`: exploratory and tracked through H2.

**Capture coverage healthy:**

    CAPTURE_OK / (CAPTURE_OK + ORIENTATION_FAILURE + CAPTURE_SYSTEM_FAILURE) ≥ 0.90

Market-side absences are not our failure, so they are excluded from the denominator.

**Football coherent:** the Wilson 95% upper bound of the prospective MODERATE win rate is ≥ 0.6658.

## Forbidden

* Changing any window, slot, threshold, tier, checkpoint or gate after the first prospective outcome.
* Any probability, fair price, bet-up-to price, stake, bankroll, Kelly fraction, unit size or automatic order.
* Recommendation language.
* Promoting a status by code beyond `REVIEW_REQUIRED`.
