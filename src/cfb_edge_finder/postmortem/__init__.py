"""Read-only postmortem tooling: reconcile executed contracts and attribute losses.

This package READS ledger rows (as plain dicts) and the pricing/ontology helpers, and writes only NEW derived
artifacts (reports, an append-only reconciliation store). It lives outside ``accounting`` on purpose: accounting
must not import predictive packages, and this package must be reachable by NO ledger-writing module
(``tests/test_postmortem_2026_11_02.py::test_postmortem_cannot_reach_the_ledger_write_path`` enforces both edges).
"""
