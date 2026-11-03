# Havoc is never a handicapping input

Owner rule 2026-11-02. This is enforced, not just stated:

* `modeling/forbidden_features.py` -- `assert_feature_names_clean` for every new spec, and `HAVOC_MENTION_REGISTER`,
  the complete list of files that may mention the word, each with its reason.
* `tests/test_forbidden_features.py` -- scans `src`, `scripts`, `config`; a new file that mentions havoc and is not
  registered fails CI. The pinned legacy file's sha256 is checked too.
* `modeling/v3/feature_spec.py` -- the havoc-free successor of the frozen V2 feature sets (`cfb-features-v3-nohavoc`).

## The frozen v2 shadow builder
`modeling/v2/research_features.py` still names `b_havoc_def`. It is hash-pinned (tests/test_v2_shadow.py) and was
**not edited**: changing it would silently change what recorded v2 shadow predictions mean. RUN CFB does not read it.

## Refit path (needs owner approval)
1. Build the research dataset with `scripts/research_export.py` (raw columns may still be exported).
2. Fit a shadow model on `modeling/v3/feature_spec.FEATURE_SETS` with a new model version id.
3. Compare against the v2 shadow on the same strictly separated held-out block; report intervals.
4. Promotion of either is a separate owner decision. Nothing here promotes anything.
