import atlas_entities


def test_split_alternatives_and_footnotes_without_duplicate_creatures():
    labels, reason = atlas_entities.split_entities("Agumon*, Guilmon; Agumon or Gabumon[1]")
    assert labels == ["Agumon", "Guilmon", "Gabumon"] and not reason


def test_editorial_entity_name_preserves_commas_and_forms():
    assert atlas_entities.split_entities("Knight, the Brave", known_labels=["Knight, the Brave"])[0] == ["Knight, the Brave"]
    assert atlas_entities.split_entities("Dragon (Red, Blue), Knight")[0] == ["Dragon (Red, Blue)", "Knight"]
    assert atlas_entities.split_entities('"Knight, the Brave", Dragon')[0] == ["Knight, the Brave", "Dragon"]


def test_fusion_is_not_mistaken_for_two_independent_origins():
    labels, reason = atlas_entities.split_entities("Knight + Dragon")
    assert not labels and reason


def test_exact_link_prevents_splitting_a_multiword_entity():
    assert atlas_entities.split_entities("Fire and Ice", cell={"links": [{"text": "Fire and Ice"}]}) == (["Fire and Ice"], "")
