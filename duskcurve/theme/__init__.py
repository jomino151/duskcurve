"""Duskcurve desktop theme: which mode the desktop should be in, and what that mode looks like.

PURE package (stdlib + duskcurve.schedule.curve). The code that actually writes
the settings lives in duskcurve.backend.desktop_theme.
"""

from duskcurve.theme.policy import DARK, LIGHT, desired_mode
from duskcurve.theme.variant import ThemeVariant

__all__ = ["DARK", "LIGHT", "ThemeVariant", "desired_mode"]
