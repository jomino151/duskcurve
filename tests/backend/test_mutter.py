import pytest

from duskcurve.backend.mutter import Display, MutterBackend
from duskcurve.backend.transport import FakeMutterTransport
from duskcurve.color import build_ramp


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
    from duskcurve.backend.transport import MutterError
    be = MutterBackend(FakeMutterTransport())
    with pytest.raises(MutterError):
        be.get_gamma("NOPE-9")


def test_apply_ramp_sends_exact_payload_to_correct_crtc():
    t = FakeMutterTransport(ramp_size=4096)
    be = MutterBackend(t)
    r, g, b = build_ramp(4096, 3400)
    be.apply_ramp("HDMI-1", r, g, b)
    serial, crtc, sr, sg, sb = t.set_calls[-1]
    assert crtc == 1                      # HDMI-1 -> crtc id 1
    assert (sr, sg, sb) == (r, g, b)


def test_get_gamma_roundtrips_after_apply():
    t = FakeMutterTransport(ramp_size=8)
    be = MutterBackend(t)
    r, g, b = build_ramp(8, 2700)
    be.apply_ramp("DP-2", r, g, b)
    assert be.get_gamma("DP-2") == (r, g, b)


def test_reset_writes_identity_ramp():
    t = FakeMutterTransport(ramp_size=4)
    be = MutterBackend(t)
    be.reset("DP-2")
    _serial, crtc, sr, sg, sb = t.set_calls[-1]
    assert crtc == 0
    assert sr == sg == sb == [0, 21845, 43690, 65535]     # round(i/3*65535)


def test_apply_ramp_retries_once_on_stale_serial():
    t = FakeMutterTransport(ramp_size=4)
    be = MutterBackend(t)
    t.force_stale_once()
    r, g, b = build_ramp(4, 5000)
    be.apply_ramp("DP-2", r, g, b)         # first attempt stale -> refresh -> retry
    assert t.set_calls[-1][2:] == (r, g, b)


def test_monitors_changed_refreshes_serial_and_notifies():
    t = FakeMutterTransport()
    be = MutterBackend(t)
    seen = []
    be.subscribe_monitors_changed(lambda: seen.append("changed"))
    t.emit_monitors_changed(new_serial=99)
    assert seen == ["changed"]
    # applies now use the refreshed serial (99) -> no stale error
    size = be.list_displays()[0].ramp_size
    r, g, b = build_ramp(size, 6500)
    be.apply_ramp("DP-2", r, g, b)
    assert t.set_calls[-1][0] == 99


def test_monitors_changed_drops_disappeared_display():
    t = FakeMutterTransport()
    be = MutterBackend(t)
    t.disable_output("HDMI-1")
    t.emit_monitors_changed()
    assert [d.connector for d in be.list_displays()] == ["DP-2"]


def test_list_displays_resolves_crtc_id_by_index_not_position():
    # crtc ids permuted vs their array positions: index 0 -> id 5, index 1 -> id 9
    be = MutterBackend(FakeMutterTransport(ramp_size=8, crtc_ids=(5, 9)))
    displays = {d.connector: d for d in be.list_displays()}
    assert displays["DP-2"].crtc_id == 5    # output current_crtc index 0 -> crtcs[0][0] == 5
    assert displays["HDMI-1"].crtc_id == 9  # output current_crtc index 1 -> crtcs[1][0] == 9


def test_stale_retry_uses_refreshed_serial():
    t = FakeMutterTransport(ramp_size=4)
    be = MutterBackend(t)
    t.force_stale_once(bump_serial_to=7)   # first apply is stale AND config serial advances to 7
    r, g, b = build_ramp(4, 5000)
    be.apply_ramp("DP-2", r, g, b)
    assert t.set_calls[-1][0] == 7          # retry used the refreshed serial, not the stale one
