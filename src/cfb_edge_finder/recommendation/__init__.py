"""Structural plumbing for a FUTURE evidence-based recommendation engine.

*** THIS PACKAGE CANNOT RECOMMEND ANYTHING, BY CONSTRUCTION ***
Every path through it terminates in QUALIFICATION_DISABLED. That is not a
configuration default someone can flip: qualification requires a
versioned, externally supplied, explicitly approved empirical threshold
artifact, and no such artifact exists or can be produced from inside this
repository. The default provider returns NO_VALIDATED_THRESHOLD_SET, and
there is no code path that manufactures one.

*** WHY THE THRESHOLDS ARE DELIBERATELY ABSENT ***
The corpus currently holds ZERO settled supported observations. Any cutoff
written here today -- "5% surplus", "positive CLV", anything -- would be a
number chosen by a person's intuition and then dressed in the authority of
code. That is the exact failure mode this whole research programme is
built to avoid. Thresholds must come later, from prospective evidence,
holdout-aware, reviewed and approved by a human.

*** THE STAGE BOUNDARY ***
    1. candidate formation      built here
    2. eligibility              built here (structurally; always disabled)
    3. expression de-duplication built here
    4. correlation/risk grouping built here (limits disabled)
    5. ranking/scoring          container only, scoring disabled
    6. card construction        skeleton only, always empty
    -------------------------------- hard boundary --------------------
    7. stake sizing             ABSENT
    8. execution                ABSENT

Stages 7 and 8 have no implementation, no interface that computes them,
and no import path into this package. The boundary between 6 and 7 is
explicit (see card.py's PortfolioBoundary) precisely so that qualification
and sizing remain visibly separate decisions.
"""
