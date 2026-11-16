"""Sandbox for driving install.sh / uninstall.sh without touching the machine.

Every external command the scripts call is stubbed by a script in stubs/ that
appends its argv to $STUB_LOG and exits 0. The stub directory goes first on
PATH, so a script that reaches for the real systemctl/gsettings/sudo/apt gets
the fake instead -- if a test ever hangs or mutates the developer's session,
that is a missing stub, not a flaky test.
"""

import os
import pathlib
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
STUBS = pathlib.Path(__file__).parent / "stubs"


class Sandbox:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.data = root / ".local" / "share"
        self.config = root / ".config"
        self.bin = root / ".local" / "bin"
        self.stub_bin = root / "stub-bin"
        self.stub_log_path = root / "stub.log"
        self.gsettings_store = root / "gsettings"
        for d in (self.data, self.config, self.bin, self.stub_bin, self.gsettings_store):
            d.mkdir(parents=True, exist_ok=True)
        for stub in STUBS.iterdir():
            target = self.stub_bin / stub.name
            shutil.copy(stub, target)
            target.chmod(0o755)
        self.stub_log_path.touch()
        self.env = {
            "HOME": str(root),
            "XDG_DATA_HOME": str(self.data),
            "XDG_CONFIG_HOME": str(self.config),
            # Stubs first, then the real PATH so bash/coreutils still resolve.
            "PATH": f"{self.stub_bin}:{os.environ['PATH']}",
            "STUB_LOG": str(self.stub_log_path),
            "GSETTINGS_STORE": str(self.gsettings_store),
            "DUSKCURVE_PYTHON": str(self.stub_bin / "python3"),
            # Stub python3 probe outcomes; tests flip these.
            "STUB_GI_OK": "1",
            "STUB_CAIRO_OK": "1",
            # Exit status for `python3 -m pip show duskcurve`: 1 = not installed,
            # which is the clean-machine default. Tests that exercise the
            # shadowing-old-install warning set this to "0".
            "STUB_PIP_SHOW_OK": "1",
            "TERM": "dumb",
        }

    def run(self, script: str, *args: str, expect_ok: bool = True, stdin: str = ""):
        proc = subprocess.run(
            ["bash", str(REPO / script), *args],
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
            cwd=REPO,
            timeout=60,
        )
        if expect_ok:
            assert proc.returncode == 0, f"{script} failed:\n{proc.stdout}\n{proc.stderr}"
        return proc

    def stub_log(self) -> list[str]:
        return [ln for ln in self.stub_log_path.read_text().splitlines() if ln.strip()]

    def called(self, name: str) -> list[str]:
        return [ln for ln in self.stub_log() if ln.split()[0] == name]

    def gsettings_value(self, schema: str, key: str) -> str | None:
        path = self.gsettings_store / f"{schema}.{key}"
        return path.read_text().strip() if path.is_file() else None

    def set_gsettings(self, schema: str, key: str, value: str) -> None:
        (self.gsettings_store / f"{schema}.{key}").write_text(value + "\n")


@pytest.fixture
def sandbox(tmp_path):
    return Sandbox(tmp_path)
