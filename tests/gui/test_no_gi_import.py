import subprocess
import sys


def test_importing_duskcurve_gui_does_not_pull_in_gi():
    code = (
        "import sys; import duskcurve.gui; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'duskcurve.gui pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_importing_gui_pure_modules_does_not_pull_in_gi():
    code = (
        "import sys; "
        "import duskcurve.gui.protocol, duskcurve.gui.state, "
        "duskcurve.gui.editing, duskcurve.gui.geometry, duskcurve.gui.duration, "
        "duskcurve.gui.rules_logic; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'a pure duskcurve.gui module pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
