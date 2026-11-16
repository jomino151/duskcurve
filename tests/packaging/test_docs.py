"""The user docs must describe the installer, not the procedure it replaced."""

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
README_ONLY = (REPO / "README.md").read_text()
README = README_ONLY + "\n" + (REPO / "docs" / "configuration.md").read_text()
DOC_FILES = [
    REPO / "README.md",
    REPO / "CHANGELOG.md",
    REPO / "CONTRIBUTING.md",
    *sorted((REPO / "docs").glob("*.md")),
]


def test_readme_documents_both_scripts():
    assert "./install.sh" in README_ONLY
    assert "./uninstall.sh" in README_ONLY


def test_readme_documents_every_flag():
    for flag in ("--dev", "--no-start", "--purge"):
        assert flag in README_ONLY


def test_readme_no_longer_teaches_the_manual_procedure():
    """Two competing sets of instructions would invite the double install."""
    assert "--break-system-packages" not in README_ONLY
    assert "ln -s" not in README_ONLY


def test_readme_documents_the_no_record_night_light_case():
    """install.sh records nothing on a failed read, so the no-record case is reachable."""
    lowered = README.lower()
    assert "leaves night light alone" in lowered or "never successfully" in lowered


def test_readme_still_warns_about_the_wayland_logout():
    """The extension only loads after a new session, and Alt+F2 r does not work on Wayland."""
    lowered = README.lower()
    assert "log out" in lowered
    assert "wayland" in lowered


def test_readme_is_short():
    assert len(README_ONLY.splitlines()) <= 150


def test_docs_carry_no_process_or_tooling_references():
    # Bracketed letters keep this file itself clear of the words it forbids.
    forbidden = re.compile(
        r"cl[a]ude|anthr[o]pic|superp[o]wers|subag[e]nt|mileston[e]|agent[i]c", re.I
    )
    for path in DOC_FILES:
        found = forbidden.search(path.read_text())
        assert found is None, f"{path.name} mentions {found.group()!r}"
