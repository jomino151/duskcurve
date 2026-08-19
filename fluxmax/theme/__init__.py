"""flux-max desktop theme: which mode the desktop should be in, and what that mode looks like.

PURE package (stdlib + fluxmax.schedule.curve). The code that actually writes
the settings lives in fluxmax.backend.desktop_theme.
"""

from fluxmax.theme.policy import DARK, LIGHT, desired_mode
from fluxmax.theme.variant import ThemeVariant

__all__ = ["DARK", "LIGHT", "ThemeVariant", "desired_mode"]
