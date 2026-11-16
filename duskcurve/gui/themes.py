"""Enumerate installed GTK and GNOME Shell themes.

PURE (stdlib only, no gi) so the scanning logic is testable against a tmp_path.
A GTK theme is any directory; a shell theme is one containing a `gnome-shell`
subdirectory, which is what the User Themes extension actually loads.
"""

import os

DEFAULT_THEME_DIRS = (
    "/usr/share/themes",
    os.path.expanduser("~/.local/share/themes"),
    os.path.expanduser("~/.themes"),
)


def _scan(dirs, predicate) -> list[str]:
    found: set[str] = set()
    for directory in dirs:
        try:
            entries = os.listdir(directory)
        except OSError:
            continue  # a theme dir that does not exist is normal, not an error
        for name in entries:
            path = os.path.join(directory, name)
            if os.path.isdir(path) and predicate(path):
                found.add(name)
    return sorted(found)


def list_gtk_themes(dirs=DEFAULT_THEME_DIRS) -> list[str]:
    return _scan(dirs, lambda _path: True)


def list_shell_themes(dirs=DEFAULT_THEME_DIRS) -> list[str]:
    return _scan(dirs, lambda path: os.path.isdir(os.path.join(path, "gnome-shell")))
