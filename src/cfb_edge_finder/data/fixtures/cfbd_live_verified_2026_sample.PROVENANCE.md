# Provenance: `cfbd_live_verified_2026_sample.json`

**Genuineness:** These four records are copied verbatim (field-for-field)
from a real, authenticated CFBD API response. They are NOT synthetic and
NOT hand-constructed -- contrast with `cfbd_games_2026_sample.json`, which
is a synthetic fixture with fabricated IDs (`5001000xx`) used for
fixture-mode dry runs.

- **Source endpoint:** `GET https://api.collegefootballdata.com/games?year=2026`
- **Source season:** 2026 (regular season, week 1)
- **Capture timestamp (UTC):** 2026-08-23T01:20:52Z
- **Capture method:** `scripts/validate_cfbd_live.py`, run via
  `.github/workflows/validate-cfbd-live.yml` (`workflow_dispatch` only) on
  a GitHub-hosted Actions runner, using the `CFBD_API_KEY` repository
  secret. Workflow run:
  https://github.com/chmoses98/cfb-edge-finder/actions/runs/32610126557
- **Full raw record count that season:** 3610 games total (this file keeps
  only 4, hand-selected for schema/edge-case coverage -- see below). No
  large raw payload was committed; the workflow's own log output is also
  bounded to counts/field-names/small representative samples, never a bulk
  dump.
- **Fields intentionally removed:** none. Each record here is the CFBD
  response verbatim for that game ID (i.e. no field was stripped), aside
  from ordering keys for readability. Nothing resembling a credential,
  auth header, or email address was ever present in a CFBD game record to
  begin with.

## What each record is for

| id | why it's here |
|---|---|
| `401864494` | Ordinary FBS-vs-FBS game (USC vs San José State). The away team name `"San José State"` (accented) is the exact genuine string that exposed a real alias gap in the team registry -- it did not resolve until `"San José State": "san-jose-state"` was added to `ALIASES`. |
| `401866409` | Genuine FBS-vs-FCS matchup (Buffalo vs UAlbany, `awayClassification: "fcs"`). Confirms a real FCS opponent is retained by the schedule ingestion pipeline rather than silently dropped. |
| `401856766` | Genuine neutral-site FBS-vs-FBS game (TCU vs North Carolina, Aviva Stadium, Dublin -- `neutralSite: true`, `notes: "Aer Lingus College Football Classic"`). |
| `401907702` | Genuine Division II matchup (University of Mary vs Rocky Mountain, `homeClassification: "ii"`, `awayClassification: null`) that is NOT FBS-involved. Kept as a negative case: this record must be filtered out entirely by the schedule ingestion pipeline, never retained. |

## What is deliberately NOT in this file

A genuine structured College Football Playoff record (`playoff.competition`,
`playoff.round`, etc.) was also validated live, against a real historical
2024 CFP first-round game (source: `GET /games?year=2024&seasonType=postseason`,
same capture run as above). The genuine `playoff` object's keys and values
were confirmed:

```
competition = "cfp"
round = "first_round"
roundName = "First Round"
bowlName = "College Football Playoff First Round Presented by Allstate"
bracketSlot = "FR3"
homeSeed = 7
awaySeed = 10
```

and `derive_week_metadata(playoff=...)` was confirmed to correctly map this
genuine object to `week_label="cfp-first-round"`,
`cfp_round=CFPRound.FIRST_ROUND`. However, the validation script printed
only this `playoff` sub-object (by design, to avoid dumping a large raw
payload into CI logs) -- the surrounding game envelope (home/away team,
date, venue) for that specific historical game was never captured in a form
that could be copied into a fixture without fabricating fields. Rather than
inventing a plausible-looking home/away pairing around a genuine `playoff`
object, no full CFP game record is included here. `tests/test_week_labels.py`
already covers the structured-playoff-mapping logic directly against this
same genuine field shape (see `test_structured_playoff_round_mapping` and
neighboring tests), so the mapping itself is under test even though a full
end-to-end fixture game record for a CFP game is not.
