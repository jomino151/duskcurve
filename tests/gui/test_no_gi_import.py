import subprocess
import sys


def test_importing_fluxmax_gui_does_not_pull_in_gi():
    code = (
        "import sys; import fluxmax.gui; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'fluxmax.gui pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_importing_gui_pure_modules_does_not_pull_in_gi():
    code = (
        "import sys; "
        "import fluxmax.gui.protocol, fluxmax.gui.state, "
        "fluxmax.gui.editing, fluxmax.gui.geometry, fluxmax.gui.duration; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'a pure fluxmax.gui module pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
