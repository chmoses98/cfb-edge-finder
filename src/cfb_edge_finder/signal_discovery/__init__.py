"""Football Signal Discovery Lab -- Wave 1 (CFB). RESEARCH ONLY.

Protocol: docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE1_PROTOCOL.md.

Three strictly separated layers, each in its own module:

  features.py      market-blind, outcome-blind pregame features. Built by the
                   UNMODIFIED production football builder on games that finished
                   before the production cutoff. Imports nothing that can read a
                   line, a price or the target game's own result.
  market_lines.py  historical sportsbook lines (CFBD cache). Never imported by
                   features.py. Score fields in the CFBD lines payload are dropped
                   on load.
  outcomes.py      the target game's own result, read only after the pregame
                   feature table has been frozen and hashed.

`evaluate.py` is the only place the three meet, and it refuses to run unless
the pre-registered hypothesis file still hashes to the value named in the
protocol.

Nothing in this package changes a production rule, threshold, claim, SIFT
output, recommendation, stake or market mapping.
"""

SIGNAL_DISCOVERY_VERSION = "cfb-signal-discovery-wave1/1.0.0"
FEATURE_SCHEMA_VERSION = "cfb_signal_features/1.0.0"
REGISTRY_SCHEMA_VERSION = "football_signal_registry/1.0.0"
