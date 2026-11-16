"""scripts/lib.sh in isolation: paths, state file, helpers."""

import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO / "scripts" / "lib.sh"


def source(sandbox, snippet: str, expect_ok: bool = True):
    """Run `snippet` with scripts/lib.sh sourced, inside the sandbox env."""
    proc = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{LIB}"; {snippet}'],
        env=sandbox.env,
        capture_output=True,
        text=True,
    )
    if expect_ok:
        assert proc.returncode == 0, f"failed: {proc.stderr}"
    return proc


def test_paths_derive_from_xdg_not_home(sandbox):
    """Hardcoding ~/.local/share would make every install test a no-op that
    silently wrote to the developer's real home directory."""
    out = source(sandbox, 'echo "$DUSKCURVE_VENV"; echo "$DUSKCURVE_UNIT_DIR"').stdout.split()
    assert out[0] == str(sandbox.data / "duskcurve" / "venv")
    assert out[1] == str(sandbox.config / "systemd" / "user")


def test_paths_fall_back_to_the_default_xdg_locations(sandbox):
    env = dict(sandbox.env)
    env.pop("XDG_DATA_HOME")
    env.pop("XDG_CONFIG_HOME")
    proc = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; echo "$DUSKCURVE_HOME"; echo "$DUSKCURVE_UNIT_DIR"'],
        env=env,
        capture_output=True,
        text=True,
    )
    home = env["HOME"]
    assert proc.stdout.split() == [f"{home}/.local/share/duskcurve", f"{home}/.config/systemd/user"]


def test_state_set_then_get_round_trips(sandbox):
    out = source(
        sandbox, 'state_set night_light_was_enabled true; state_get night_light_was_enabled'
    )
    assert out.stdout.strip() == "true"


def test_state_set_upserts_rather_than_appending(sandbox):
    """Re-running the installer must not leave two values for one key --
    state_get would then return whichever the parser happened to hit first."""
    source(sandbox, 'state_set install_mode copy; state_set install_mode dev')
    text = (sandbox.data / "duskcurve" / "install-state").read_text()
    assert len(re.findall(r"^install_mode=", text, re.M)) == 1
    assert source(sandbox, "state_get install_mode").stdout.strip() == "dev"


def test_state_get_returns_nonzero_for_a_missing_key(sandbox):
    proc = source(sandbox, "state_get nope", expect_ok=False)
    assert proc.returncode != 0
    assert proc.stdout.strip() == ""


def test_state_get_returns_nonzero_when_the_file_does_not_exist(sandbox):
    """uninstall.sh distinguishes "recorded false" from "never recorded", and
    must not treat a missing file as a value."""
    proc = source(sandbox, "state_get night_light_was_enabled", expect_ok=False)
    assert proc.returncode != 0


def test_state_set_creates_the_directory(sandbox):
    source(sandbox, "state_set install_mode copy")
    assert (sandbox.data / "duskcurve" / "install-state").is_file()


def test_icon_theme_dir_is_the_parent_of_icon_dir(sandbox):
    """install.sh and uninstall.sh both need the theme root (for
    gtk-update-icon-cache) as well as the deeper scalable/apps dir; both
    should derive from one place instead of being hand-built twice."""
    out = source(
        sandbox, 'echo "$DUSKCURVE_ICON_THEME_DIR"; echo "$DUSKCURVE_ICON_DIR"'
    ).stdout.split("\n")
    assert out[0] == str(sandbox.data / "icons" / "hicolor")
    assert out[1] == str(sandbox.data / "icons" / "hicolor" / "scalable" / "apps")


def test_have_detects_present_and_absent_commands(sandbox):
    assert source(sandbox, "have bash && echo yes").stdout.strip() == "yes"
    proc = source(sandbox, "have definitely-not-a-real-command", expect_ok=False)
    assert proc.returncode != 0
    assert proc.stdout.strip() == ""


def test_die_exits_nonzero_and_writes_to_stderr(sandbox):
    proc = source(sandbox, 'die "boom"; echo unreachable', expect_ok=False)
    assert proc.returncode == 1
    assert "boom" in proc.stderr
    assert "unreachable" not in proc.stdout


def test_night_light_schema_and_key_match_the_python_module(sandbox):
    """lib.sh duplicates these strings because bash cannot import the module.
    If nightlight.py ever moves to a different schema, this catches the drift
    instead of uninstall.sh silently restoring nothing."""
    py = (REPO / "duskcurve" / "backend" / "nightlight.py").read_text()
    schema = re.search(r'_SCHEMA = "([^"]+)"', py).group(1)
    key = re.search(r'_KEY = "([^"]+)"', py).group(1)
    out = source(sandbox, 'echo "$NIGHT_LIGHT_SCHEMA"; echo "$NIGHT_LIGHT_KEY"').stdout.split()
    assert out == [schema, key]
