import pytest

gi = pytest.importorskip("gi")
gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

from duskcurve.backend.gio_transport import _params  # noqa: E402
from duskcurve.color import build_ramp  # noqa: E402


def test_set_crtc_gamma_params_match_the_plain_variant_exactly():
    # The fast bytes-backed encoding must be byte-for-byte what the slow
    # element-by-element GLib.Variant constructor produces.
    r, g, b = build_ramp(4096, 3400)
    args = (7, 1, r, g, b)
    fast = _params("SetCrtcGamma", args)
    slow = GLib.Variant("(uuaqaqaq)", args)
    assert fast.get_type_string() == "(uuaqaqaq)"
    assert fast.equal(slow)
    assert fast.unpack() == (7, 1, r, g, b)


def test_set_crtc_gamma_keeps_full_16_bit_range():
    ramp = [0, 1, 32768, 65534, 65535]
    fast = _params("SetCrtcGamma", (1, 0, ramp, ramp, ramp))
    assert fast.unpack()[2] == ramp


def test_other_methods_are_unchanged():
    assert _params("GetResources", ()) is None
    assert _params("GetCrtcGamma", (3, 0)).equal(GLib.Variant("(uu)", (3, 0)))
