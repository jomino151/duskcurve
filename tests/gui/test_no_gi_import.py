import subprocess
import sys

import pytest


def test_importing_fluxmax_gui_does_not_pull_in_gi():
    code = (
        "import sys; import fluxmax.gui; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'fluxmax.gui pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.xfail(reason="state/editing/geometry land in Tasks 6.4-6.8", strict=False)
def test_importing_gui_pure_modules_does_not_pull_in_gi():
    code = (
        "import sys; "
        "import fluxmax.gui.protocol, fluxmax.gui.state, "
        "fluxmax.gui.editing, fluxmax.gui.geometry; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'a pure fluxmax.gui module pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
