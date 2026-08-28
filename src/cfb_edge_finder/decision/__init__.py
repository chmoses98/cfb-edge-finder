"""Shadow decision layer: the machinery a future empirically-validated
decision engine will need, with every empirical gate locked.

Nothing in this package can authorise a wager. It exists so that when
genuine prospective CLOSING and settlement data arrive, the remaining
work is the empirical question -- what threshold is justified, on what
evidence -- and not a scramble to build plumbing under time pressure.

The locks are structural, not configuration:

* no threshold artifact ships with this repository;
* an artifact must be explicitly approved by a human before it is even
  eligible for SHADOW use, and separately before live use;
* no sample count, statistical result, or elapsed time promotes an
  artifact;
* the shadow pipeline's terminal success state is unreachable without an
  approved artifact, and the zero it reports is counted, never hardcoded.
"""
