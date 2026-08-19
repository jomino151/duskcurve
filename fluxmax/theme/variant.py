"""The set of desktop settings that make up one theme (light or dark).

PURE: stdlib dataclasses only. Lives here rather than in daemon/config.py or
backend/desktop_theme.py because both of those import it, and fluxmax.theme is
the one package below them both.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ThemeVariant:
    """One theme's settings. An empty string means "leave this setting alone"."""

    color_scheme: str = ""  # "" | "default" | "prefer-light" | "prefer-dark"
    gtk_theme: str = ""
    icon_theme: str = ""
    shell_theme: str = ""
    cursor_theme: str = ""
    wallpaper: str = ""  # absolute path; "" = leave alone
