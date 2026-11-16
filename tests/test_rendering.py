"""SVG loading preserves ordinary artwork and rejects XML entities."""

import pytest
from defusedxml.common import EntitiesForbidden

from crycut.rendering import load_design, tag


def test_load_design_preserves_artwork_and_standard_escapes(tmp_path):
    source = tmp_path / "design.svg"
    source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'width="96mm" height="170mm" viewBox="0 0 96 170">'
        '<g fill="#ff0000"><path d="M 1 1 L 2 2"/>'
        "<text>A &amp; B &lt; C &#33;</text></g>"
        '<g id="cut"><path d="M 0 0 L 3 3"/></g></svg>'
    )

    artwork = load_design(source)

    assert artwork.tag == tag("svg")
    assert artwork.find(tag("g")).get("fill") == "#ff0000"
    assert artwork.find(".//" + tag("path")).get("d") == "M 1 1 L 2 2"
    assert artwork.find(".//" + tag("text")).text == "A & B < C !"
    assert not any(element.get("id") == "cut" for element in artwork.iter())


@pytest.mark.parametrize("external", [False, True], ids=["internal", "external"])
def test_load_design_rejects_entity_declarations(tmp_path, external):
    marker = tmp_path / "marker.txt"
    marker.write_text("harmless marker")
    entity = f'SYSTEM "{marker.as_uri()}"' if external else '"harmless marker"'
    source = tmp_path / "design.svg"
    source.write_text(
        f"<!DOCTYPE svg [<!ENTITY sample {entity}>]>"
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'width="96mm" height="170mm" viewBox="0 0 96 170">'
        "<text>&sample;</text></svg>"
    )

    with pytest.raises(EntitiesForbidden):
        load_design(source)
