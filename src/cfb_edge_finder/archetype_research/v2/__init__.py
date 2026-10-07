"""Script Engine V2 CANDIDATE: a research-only reinterpretation of the production findings. RESEARCH ONLY.

Protocol: docs/ARCHETYPE_V2_HOLDOUT_PROTOCOL.md (pre-registered before the sealed holdout was scored).

*** WHAT V2 IS ***
A different way of READING the findings the unmodified production engine
already produces. V2 computes no new metric, refits no threshold and never
calls a production builder differently: it takes the frozen pregame record
of `archetype_research.replay` and states independent claims along separate
dimensions (direction + strength, closeness, scoring environment, pace,
defensive suppression, disruption) instead of choosing mutually exclusive
narrative scripts.

*** WHAT V2 IS NOT ***
Not production. Nothing here is imported by `cfb_edge_finder.scripting`;
nothing changes the methodology version, archetypes, thresholds, bands,
SIFT, market mapping or the prospective ledger. Its empirical margin
distributions are HISTORICAL EMPIRICAL DISTRIBUTIONS, never probabilities.

*** SEASON ROLES (fixed) ***
DEVELOPMENT 2021-2025 is the only data any V2 quantity is fitted on.
SEALED_HOLDOUT 2014-2020 is evaluated once, after the protocol and the
frozen development parameters are committed. PROSPECTIVE 2026 is never an
input to V2.
"""

V2_VERSION = "cfb-archetype-v2-candidate/1.0.0"
PROTOCOL = "docs/ARCHETYPE_V2_HOLDOUT_PROTOCOL.md"
FROZEN_PARAMETERS = "data/scripting/validation/archetype_v2_frozen_parameters.json"
DEVELOPMENT_SEASONS = (2021, 2022, 2023, 2024, 2025)
HOLDOUT_SEASONS = (2014, 2015, 2016, 2017, 2018, 2019, 2020)
PROSPECTIVE_SEASONS = (2026,)
EXPLOSIVE_UPSET_STATUS = "UNTESTED_HISTORICALLY_DATA_UNAVAILABLE"
SEED = 20261008
RESAMPLES = 2000
