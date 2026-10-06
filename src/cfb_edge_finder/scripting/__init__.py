"""CFB Script Engine V1: football data -> matchup -> scripts -> market expressions.

*** THIS IS NOT A BETTING MODEL ***
It is a descriptive layer. It publishes no fair probability, no expected
value, no stake and no "+EV" claim, and it never prices a contract. The
retired projection model (`docs/MODEL_RETIREMENT_2026.md`) is not imported
here and nothing here revives its fair values.

*** THE ORDER IS THE PRODUCT ***

    football data (pre-kickoff only)
      -> opponent-adjusted team profiles        adjust.py, profile.py
      -> market-blind matchup vector            matchup.py
      -> deterministic findings                 findings.py
      -> 0-4 evidence-gated game scripts        scripts.py
      -> FROZEN football artifact + hash        freeze.py
    ------------------------------------------------ market data enters here
      -> script/market compatibility map        market_map.py
      -> script survival + expression labels    expressions.py

Everything above the line is a function of football data alone. The modules
above the line may not import the catalog, the Kalshi client, the execution
packet builder or anything else that carries a price, and
`tests/test_script_engine_market_blindness.py` proves it twice: structurally
(by parsing imports) and behaviourally (byte-identical artifacts against
radically different prices).
"""

METHODOLOGY_VERSION = "cfb-script-engine/1.0.0"
ADJUSTMENT_VERSION = "cfb-opponent-adjustment/1.0.0"
TEAM_GAME_SCHEMA_VERSION = "cfb_team_game/1.0.0"
MATCHUP_SCHEMA_VERSION = "cfb_matchup_profile/1.0.0"
SCRIPT_ARTIFACT_SCHEMA_VERSION = "cfb_script_artifact/1.0.0"
MARKET_MAP_SCHEMA_VERSION = "cfb_script_market_map/1.0.0"
LEDGER_SCHEMA_VERSION = "cfb_script_ledger/1.0.0"
REALIZED_SCHEMA_VERSION = "cfb_realized_script/1.0.0"

#: Modules that build the football artifact. None of them may import anything
#: that carries a market price; the market-blindness test enforces it.
FOOTBALL_MODULES = (
    "gamelog",
    "espn",
    "cfbd",
    "metrics",
    "adjust",
    "matchup",
    "findings",
    "scripts",
    "confidence",
    "freeze",
    "football",
    "packets",
    "realized",
    "report",
)

#: Modules that run strictly AFTER the freeze and may read contracts and prices.
MARKET_MODULES = (
    "market_map",
    "expressions",
    "ledger",
    "publish",
)
