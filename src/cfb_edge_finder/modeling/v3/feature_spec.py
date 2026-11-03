"""V3 feature spec: the V2 research feature sets WITHOUT any havoc input. New id, nothing pinned is edited.

The frozen v2 builder (``modeling/v2/research_features.py``) is imported, never modified. This spec:
  * drops every havoc-named column from the input frame BEFORE the matchup frame is built, and
  * drops every havoc-named column and feature from the outputs,
so a model fitted on a v3 set cannot see a havoc value even if the dataset still carries one.
``SPEC_ID`` is new; a v3 shadow model is a different model than v2 and needs owner approval to fit and promote.
"""

from __future__ import annotations

import hashlib

import pandas as pd

from cfb_edge_finder.modeling.forbidden_features import assert_feature_names_clean, forbidden_in
from cfb_edge_finder.modeling.v2 import research_features as v2

SPEC_ID = "cfb-features-v3-nohavoc"
BASE_SPEC_ID = "cfb-features-v2 (frozen)"

FEATURE_SETS: dict[str, list[str]] = {
    name: [c for c in cols if not forbidden_in([c])] for name, cols in v2.FEATURE_SETS.items()
}
for _name, _cols in FEATURE_SETS.items():
    assert_feature_names_clean(_cols, spec_id=f"{SPEC_ID}/{_name}")

REMOVED_FROM_V2: dict[str, list[str]] = {
    name: [c for c in cols if forbidden_in([c])] for name, cols in v2.FEATURE_SETS.items() if forbidden_in(cols)
}


def matchup_frame(df: pd.DataFrame) -> pd.DataFrame:
    clean_in = df.drop(columns=[c for c in df.columns if forbidden_in([c])])
    out = v2.matchup_frame(clean_in)
    out = out.drop(columns=[c for c in out.columns if forbidden_in([c])])
    assert_feature_names_clean(list(out.columns), spec_id=f"{SPEC_ID}/matchup_frame")
    return out


def feature_hash(name: str) -> str:
    return hashlib.sha256(f"{SPEC_ID}:{','.join(FEATURE_SETS[name])}".encode()).hexdigest()[:12]
