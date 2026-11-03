from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from cfb_edge_finder.modeling import forbidden_features as ff
from cfb_edge_finder.modeling.v2 import research_features as v2
from cfb_edge_finder.modeling.v3 import feature_spec as v3

ROOT = Path(__file__).resolve().parents[1]


def test_no_unregistered_file_mentions_havoc():
    assert ff.scan_repo(ROOT) == [], "a file mentions havoc and is not in HAVOC_MENTION_REGISTER"


def test_every_registered_file_exists_and_mentions_it():
    for rel in ff.HAVOC_MENTION_REGISTER:
        p = ROOT / rel
        assert p.exists(), rel
        assert ff.file_mentions_forbidden(p), f"{rel} is registered but no longer mentions havoc: remove it"


def test_legacy_pinned_file_is_byte_identical():
    for rel, digest in ff.LEGACY_PINNED_SHA256.items():
        assert ff.sha256_file(ROOT / rel) == digest, f"{rel} is hash-pinned and must not be edited"


def test_assert_feature_names_clean_raises():
    with pytest.raises(ValueError, match="havoc"):
        ff.assert_feature_names_clean(["diff_o_ppa", "Diff_B_HAVOC_def"], spec_id="x")
    ff.assert_feature_names_clean(["diff_o_ppa"], spec_id="x")


def test_v3_sets_are_clean_and_only_remove_havoc():
    assert any(ff.forbidden_in(c) for c in v2.FEATURE_SETS.values()), "v2 should still carry havoc (frozen)"
    for name, cols in v3.FEATURE_SETS.items():
        assert not ff.forbidden_in(cols), name
        assert cols == [c for c in v2.FEATURE_SETS[name] if not ff.forbidden_in([c])], name
    assert v3.SPEC_ID != "cfb-features-v2"
    assert "b_havoc_def" in "".join(sum(v3.REMOVED_FROM_V2.values(), []))


def test_v3_matchup_frame_ignores_havoc_columns_in_the_input():
    n = 3
    df = pd.DataFrame({
        "neutral": [False, True, False], "postseason": [0, 0, 0], "conference_game": [1, 0, 1],
        "home_class": ["fbs"] * n, "away_class": ["fbs"] * n,
        "h_o_b_havoc_def": [0.1, 0.2, 0.3], "a_d_b_havoc_def": [0.2, 0.1, 0.4],
        "h_o_pts_for": [30.0, 28.0, 35.0], "a_d_pts_for": [24.0, 25.0, 20.0],
        "a_o_pts_for": [21.0, 27.0, 31.0], "h_d_pts_for": [22.0, 26.0, 23.0],
    })
    out = v3.matchup_frame(df)
    assert not ff.forbidden_in(list(out.columns))
    assert "diff_pts_for" in out.columns
