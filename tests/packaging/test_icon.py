"""The icon asset and its agreement with the desktop file."""

import pathlib
import re
import xml.etree.ElementTree as ET

REPO = pathlib.Path(__file__).resolve().parents[2]
ICON = REPO / "data" / "icons" / "org.fluxmax.gui.svg"
DESKTOP = REPO / "data" / "org.fluxmax.gui.desktop"


def test_icon_exists_and_is_parseable_svg():
    root = ET.parse(ICON).getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg"


def test_icon_has_a_viewbox_so_it_scales():
    """Without a viewBox an SVG does not scale to the many sizes the shell
    asks for -- which defeats the point of installing into scalable/."""
    root = ET.parse(ICON).getroot()
    assert root.get("viewBox")


def test_icon_is_square():
    root = ET.parse(ICON).getroot()
    _, _, w, h = (float(n) for n in root.get("viewBox").split())
    assert w == h


def test_icon_name_matches_the_desktop_files_icon_key():
    """A mismatch here is invisible until the app is installed on a real
    desktop, where it silently falls back to a generic tile."""
    icon_key = re.search(r"^Icon=(.+)$", DESKTOP.read_text(), re.M).group(1).strip()
    assert ICON.stem == icon_key


def test_icon_embeds_no_external_references():
    """A hicolor icon must be self-contained: no raster hrefs, no @import, no
    font the target machine may not have."""
    text = ICON.read_text()
    assert "xlink:href" not in text
    assert "@import" not in text
    assert "<image" not in text
    assert "<text" not in text
