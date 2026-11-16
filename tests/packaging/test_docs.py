"""The README must describe the installer, not the procedure it replaced."""

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
README = (REPO / "README.md").read_text()


def test_readme_documents_both_scripts():
    assert "./install.sh" in README
    assert "./uninstall.sh" in README


def test_readme_documents_every_flag():
    for flag in ("--dev", "--no-start", "--purge"):
        assert flag in README


def test_readme_no_longer_teaches_the_manual_procedure():
    """Two competing sets of instructions would invite the double install."""
    assert "--break-system-packages" not in README
    assert "ln -s" not in README


def test_readme_documents_the_no_record_night_light_case():
    """install.sh records nothing on a failed read, so the no-record case is reachable."""
    lowered = README.lower()
    assert "leaves night light alone" in lowered or "never successfully" in lowered


def test_readme_still_warns_about_the_wayland_logout():
    """The extension only loads after a new session, and Alt+F2 r does not work on Wayland."""
    lowered = README.lower()
    assert "log out" in lowered
    assert "wayland" in lowered
