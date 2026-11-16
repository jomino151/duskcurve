"""Desktop theme model: which mode the desktop should be in, and what each mode contains.

Applying the settings is the job of duskcurve.backend.desktop_theme.
"""

from duskcurve.theme.policy import DARK, LIGHT, desired_mode
from duskcurve.theme.variant import ThemeVariant

__all__ = ["DARK", "LIGHT", "ThemeVariant", "desired_mode"]
