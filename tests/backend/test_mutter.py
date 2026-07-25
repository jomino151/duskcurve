import pytest

from fluxmax.backend.mutter import Display, MutterBackend
from fluxmax.backend.transport import FakeMutterTransport


def test_list_displays_maps_connectors_to_crtc_ids():
    be = MutterBackend(FakeMutterTransport(ramp_size=4096))
    displays = {d.connector: d for d in be.list_displays()}
    assert displays["DP-2"] == Display(connector="DP-2", crtc_id=0, ramp_size=4096)
    assert displays["HDMI-1"] == Display(connector="HDMI-1", crtc_id=1, ramp_size=4096)


def test_list_displays_excludes_undriven_outputs():
    t = FakeMutterTransport()
    t.disable_output("HDMI-1")
    be = MutterBackend(t)
    names = [d.connector for d in be.list_displays()]
    assert names == ["DP-2"]


def test_unknown_connector_get_gamma_raises():
    from fluxmax.backend.transport import MutterError
    be = MutterBackend(FakeMutterTransport())
    with pytest.raises(MutterError):
        be.get_gamma("NOPE-9")
