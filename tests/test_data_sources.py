from cfb_edge_finder.data.sources import REGISTRY, DataSourceSpec


def test_registry_entries_are_well_formed():
    assert len(REGISTRY) > 0
    for spec in REGISTRY:
        assert isinstance(spec, DataSourceSpec)
        assert spec.name
        assert spec.category
        assert spec.notes, f"{spec.name} must document its verification caveats"


def test_registry_has_no_duplicate_names():
    names = [spec.name for spec in REGISTRY]
    assert len(names) == len(set(names))
