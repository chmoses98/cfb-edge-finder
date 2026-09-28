# Settlement economics — v1, v2, and amendments

```bash
python scripts/amend_settlement_economics.py --base-dir <accounting-data> --season 2026 --dry-run
python scripts/amend_settlement_economics.py --base-dir <accounting-data> --season 2026
python scripts/validate_accounting_ledger.py --base-dir <accounting-data> --against origin/accounting-data
```

## The two contracts

The router (kalshi-bet-router) attributes a Kalshi settlement to one order and
states a net figure under a named **economics contract**, carried on the
settlement row as `economics_version`:

| contract | net | status |
|---|---|---|
| `router-settlement-economics.v1` | `gross − stake − fee_cost` | every CFB row filed before 2026-09-28. **Wrong**: `stake` already contains the entry fee and Kalshi's `fee_cost` is that same fee, so it is charged twice |
| `router-settlement-economics.v2` | `gross − stake`, only once the exchange's `fee_cost` is shown to equal the entry fees of the owner's orders on that market | the contract for every CFB settlement from the switch on |

A row with **no** `economics_version` is a v1 row. Absence is read as v1 and
nothing else, because every row written before the field existed was computed
that way. A v1 row built today is still written without the key, so the rows
already on disk keep the exact shape they were filed with.

Measured on the whole 2026 CFB ledger (93 settlements): on all 89 rows with an
established net, the fee v1 subtracted equals the wager's `fees_paid` to the
cent, so every filed net is understated by exactly that fee ($95.94 in total;
$25.93 on the 2026-09-26 slate).

## An amendment corrects the economics and touches nothing

A settlement row is a claim about money the exchange moved, and this ledger
never rewrites one. A later contract's interpretation of the same exchange
facts is a separate, append-only **amendment** row in
`settlement_amendments/<season>.jsonl`:

| field | meaning |
|---|---|
| `amendment_id` | `amd-<sha256(source_bet_key \| economics_version)[:24]>` — deterministic; the same correction derived twice is one row |
| `amends_settlement_id`, `source_bet_key` | the settlement corrected, and its wager |
| `supersedes_economics_version` → `economics_version` | v1 → v2; an amendment moves a settlement forward along the contract list, never back |
| `gross_return`, `net_profit_loss` | the corrected figures |
| `original_gross_return`, `original_net_profit_loss`, `original_refusals` | the filed figures, kept beside the correction |
| `derivation`, `evidence`, `provenance`, `amended_at` | how, from what, by whom, when — **not** part of identity |

An amendment may correct the **economics only**. The importer refuses to build
one when the filed row and the correction disagree on market, side, result or
gross (a contradiction of exchange facts), when the version does not move
forward, or when the correction's own net is unestablished or carries a
refusal. Two amendments under one id that state different money are two
derivations disagreeing, and the store refuses the second for a person to
resolve.

### How the importer files one

`scripts/import_routed_settlements.py`, on a row whose wager is already settled:

| filed row | incoming row | outcome | receipt |
|---|---|---|---|
| any | identical on the identity fields | nothing written | `DUPLICATE_NOOP` |
| v1 | v2, admissible correction, not yet filed | amendment appended | `CORRECTED` (+ `amendment_id`) |
| v1 | v2, identical to a filed amendment | nothing written | `DUPLICATE_NOOP` |
| v1 | v2, differs from a filed amendment | refused | `REFUSED` |
| v1 | v2 with unestablished net | refused (not an admissible correction) | `REFUSED` |
| v2 | v1 | refused (does not supersede) | `REFUSED` |
| same version | different money | refused (contradiction) | `REFUSED` |
| none | v2 | v2 settlement row appended, with `economics_version` | `NEW` |

`CORRECTED` and `DUPLICATE_NOOP` are verdicts the router's auto-merge gate
already accepts without judgement; `REFUSED` is red and needs a person.

### The backfill

`scripts/amend_settlement_economics.py` derives the correction for the rows
already filed **without the exchange**: a v1 net is `gross − stake − fee_cost`,
so `implied_fee_cost = gross − stake − filed_net` is recoverable exactly from
the row and its wager. Where that equals the wager's `fees_paid` to within
half a hundredth of a cent, the v2 condition is proven by the filed row and the
correction is `gross − stake`. Where it does not — a shared-position row whose
net v1 refused, or a fee the fills do not explain — nothing is written and the
row is counted under `unresolved` with its reason.

It is idempotent (a second run writes nothing) and consistent with the router:
the router's own v2 settlement run derives the same `amendment_id` and the same
figures from live fee evidence and lands on the same row as `DUPLICATE_NOOP`.
Where the backfill could not establish a correction (the four shared-position
rows of 2026-09-19), the router's v2 run **can**, because it sees the exchange's
`fee_cost` against every order on the market; those become `CORRECTED` rows
with the original refusal preserved under `original_refusals`.

## Reporting: canonical and as filed

`scripts/report_accounted_wagers.py` and `scripts/cfb_postmortem.py` state
their money under the **canonical** view — each settlement at its most advanced
filed contract — and print the as-filed (v1) total beside it, with the count of
amended settlements and the difference attributable to the fee treatment. The
raw v1 values stay in `settlements/<season>.jsonl` untouched, and
`apply_amendments` returns new dicts carrying them under `as_filed_*`.

## The validator

`scripts/validate_accounting_ledger.py` checks every amendment row: schema,
unique and deterministic `amendment_id`, the amended settlement exists in the
same season and belongs to the same wager, market/side/result agree, the
`original_*` figures match the settlement as filed, the contract moves forward,
gross is unchanged. With `--against`, `settlement_amendments/` is inside the
append-only diff alongside `wagers/` and `settlements/`.
