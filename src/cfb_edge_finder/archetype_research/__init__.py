"""Historical archetype validation: a walk-forward replay of the production Script Engine. RESEARCH ONLY.

Protocol: docs/ARCHETYPE_HISTORICAL_PROTOCOL.md (pre-registered).
Results:  docs/ARCHETYPE_HISTORICAL_VALIDATION.md.

*** WHAT THIS PACKAGE MAY DO ***
Replay `scripting.football.build_content` -- the unmodified production
builder -- on each historical game, using only the games that had finished
before that game's production data cutoff; freeze the pregame result; and
only then classify the realized game and score the frozen scripts.

*** WHAT IT MAY NOT DO ***
It changes no production rule, threshold, band, archetype or ledger row. It
imports nothing that carries a market price, publishes no probability, and
refuses to write under the live publication or ledger paths
(`replay.assert_research_output_path`). Its outputs are research artifacts
under `data/scripting/validation/`.
"""

RESEARCH_VERSION = "cfb-archetype-historical-research/1.0.0"
PROTOCOL = "docs/ARCHETYPE_HISTORICAL_PROTOCOL.md"
PROTOCOL_COMMIT = "621eff005"
BOOTSTRAP_SEED = 20261007
BOOTSTRAP_RESAMPLES = 2000
MIN_SAMPLE = 30
