from fluxmax.backend.cli import main
from fluxmax.backend.mutter import MutterBackend
from fluxmax.backend.transport import FakeMutterTransport
from fluxmax.color import build_ramp


def _backend(ramp_size=4096):
    return MutterBackend(FakeMutterTransport(ramp_size=ramp_size))


def test_apply_temp_builds_and_sends_correct_ramp(capsys):
    be = _backend()
    rc = main(["--connector", "DP-2", "--temp", "3400"], backend=be)
    assert rc == 0
    # DP-2 is crtc id 0; payload equals build_ramp for that ramp size
    _s, crtc, sr, sg, sb = be._t.set_calls[-1]  # test reaches into the fake transport
    assert crtc == 0
    assert (sr, sg, sb) == build_ramp(4096, 3400)


def test_unknown_connector_returns_error(capsys):
    rc = main(["--connector", "NOPE-9", "--temp", "3400"], backend=_backend())
    assert rc == 1
    assert "unknown connector" in capsys.readouterr().err


def test_list_flag_prints_displays(capsys):
    rc = main(["--list"], backend=_backend())
    assert rc == 0
    out = capsys.readouterr().out
    assert "DP-2" in out and "HDMI-1" in out
