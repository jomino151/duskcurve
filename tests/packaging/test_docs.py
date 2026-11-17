"""The README documents the installer."""

import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
README_ONLY = (REPO / "README.md").read_text()
README = README_ONLY + "\n" + (REPO / "docs" / "configuration.md").read_text()


def test_readme_documents_both_scripts():
    assert "./install.sh" in README_ONLY
    assert "./uninstall.sh" in README_ONLY


def test_readme_documents_every_flag():
    for flag in ("--dev", "--no-start", "--purge"):
        assert flag in README_ONLY


def test_readme_has_no_manual_install_steps():
    """Competing install instructions lead to double installs."""
    assert "--break-system-packages" not in README_ONLY
    assert "ln -s" not in README_ONLY


def test_readme_documents_the_no_record_night_light_case():
    """install.sh records nothing on a failed read, so the no-record case is reachable."""
    lowered = README.lower()
    assert "leaves night light alone" in lowered or "never successfully" in lowered


def test_readme_warns_about_the_wayland_logout():
    """The extension only loads after a new session, and Alt+F2 r does not work on Wayland."""
    lowered = README.lower()
    assert "log out" in lowered
    assert "wayland" in lowered
