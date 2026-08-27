"""Research-only market-expression and correlation framework.

Organizes related Kalshi contracts into game-level structures and
describes how different market expressions relate to the same underlying
football thesis.

*** WHAT THIS IS NOT ***
Not a recommendation engine, not a qualification engine, not a staking
engine. Nothing here selects a contract, ranks contracts by
attractiveness, assigns tiers, or infers a profitability threshold. The
outputs are structural facts (which contracts express the same event) and
arithmetic facts (what each expression costs after fees). Turning either
into a decision is a separate, later, deliberate mission -- and
deliberately not something this code can do. See
tests/test_expression_safety.py.
"""
