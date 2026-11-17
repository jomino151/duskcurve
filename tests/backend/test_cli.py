import re
import shlex
from pathlib import Path

from duskcurve.backend.cli import main
from duskcurve.backend.mutter import MutterBackend
from duskcurve.backend.transport import FakeMutterTransport
from duskcurve.color import build_ramp


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


def test_temp_without_connector_applies_to_every_display(capsys):
    be = _backend()
    assert main(["--temp", "6500"], backend=be) == 0
    crtcs = {call[1] for call in be._t.set_calls}
    assert crtcs == {0, 1}


def test_connector_without_temp_is_an_error(capsys):
    assert main(["--connector", "DP-2"], backend=_backend()) == 2


def test_uninstaller_reset_command_is_accepted():
    root = Path(__file__).resolve().parents[2]
    lib = (root / "scripts" / "lib.sh").read_text()
    kelvin = re.search(r"^NEUTRAL_KELVIN=(\d+)", lib, re.M).group(1)
    line = next(
        ln
        for ln in (root / "uninstall.sh").read_text().splitlines()
        if 'bin/duskcurve-apply"' in ln and "--temp" in ln
    )
    argv = shlex.split(line.split('bin/duskcurve-apply"', 1)[1].split(";")[0])
    argv = [a.replace("$NEUTRAL_KELVIN", kelvin) for a in argv]
    argv = [a for a in argv if not a.startswith((">", "2>"))]
    assert main(argv, backend=_backend()) == 0
