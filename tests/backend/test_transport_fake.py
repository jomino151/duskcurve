import pytest

from duskcurve.backend.transport import FakeMutterTransport, StaleSerialError


def test_get_resources_shape():
    t = FakeMutterTransport()
    serial, crtcs, outputs, modes, mw, mh = t.call("GetResources", ())
    assert serial == t.serial
    assert [o[4] for o in outputs] == ["DP-2", "HDMI-1"]        # names
    assert [o[2] for o in outputs] == [0, 1]                    # current_crtc indexes
    assert [c[0] for c in crtcs] == [0, 1]                      # crtc ids


def test_get_gamma_returns_ramp_of_configured_size():
    t = FakeMutterTransport(ramp_size=256)
    r, g, b = t.call("GetCrtcGamma", (t.serial, 0))
    assert len(r) == len(g) == len(b) == 256


def test_set_gamma_records_and_updates():
    t = FakeMutterTransport(ramp_size=4)
    t.call("SetCrtcGamma", (t.serial, 0, [1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]))
    assert t.set_calls[-1] == (t.serial, 0, [1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12])
    assert t.call("GetCrtcGamma", (t.serial, 0)) == ([1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12])


def test_force_stale_once_raises_then_recovers():
    t = FakeMutterTransport(ramp_size=4)
    t.force_stale_once()
    with pytest.raises(StaleSerialError):
        t.call("SetCrtcGamma", (t.serial, 0, [0] * 4, [0] * 4, [0] * 4))
    # second call succeeds
    t.call("SetCrtcGamma", (t.serial, 0, [0] * 4, [0] * 4, [0] * 4))


def test_wrong_serial_raises_stale():
    t = FakeMutterTransport(ramp_size=4)
    with pytest.raises(StaleSerialError):
        t.call("SetCrtcGamma", (t.serial + 99, 0, [0] * 4, [0] * 4, [0] * 4))


def test_monitors_changed_callback_and_serial_bump():
    t = FakeMutterTransport()
    fired = []
    t.subscribe_monitors_changed(lambda: fired.append(t.serial))
    t.emit_monitors_changed(new_serial=42)
    assert fired == [42]


def test_disable_output_removes_it_from_resources():
    t = FakeMutterTransport()
    t.disable_output("HDMI-1")
    _, _, outputs, _, _, _ = t.call("GetResources", ())
    hdmi = next(o for o in outputs if o[4] == "HDMI-1")
    assert hdmi[2] == -1        # current_crtc index -1 => not driven
