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
    """Two competing sets of instructions is how the double-install the
    installer warns about happens in the first place."""
    assert "--break-system-packages" not in README
    assert "ln -s" not in README


def test_readme_still_warns_about_the_wayland_logout():
    """Correct installs look broken without it -- the extension does not load
    until the session restarts, and Alt+F2 r does not work on Wayland."""
    lowered = README.lower()
    assert "log out" in lowered
    assert "wayland" in lowered
