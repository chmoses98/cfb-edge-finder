"""Research analytics: descriptive measurement over the settled
prospective corpus.

*** WHAT THIS LAYER IS NOT ***
Nothing here recommends, qualifies, sizes, or selects. It reports what
the captured data says, sliced several ways, with sample sizes and
uncertainty attached. Choosing a threshold from these numbers is a
separate, later, deliberate decision -- and deliberately not something
this code can do, which is why no function in this package returns a
"best" anything. See tests/test_analytics_safety.py, which fails if a
selection-shaped surface ever appears.
"""
