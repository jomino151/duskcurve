"""Read/write GNOME Night Light's enabled flag so flux-max can take over gamma."""
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

_SCHEMA = "org.gnome.settings-daemon.plugins.color"
_KEY = "night-light-enabled"


def _default_settings() -> "Gio.Settings":
    return Gio.Settings.new(_SCHEMA)


def night_light_enabled(settings: "Gio.Settings | None" = None) -> bool:
    return (settings or _default_settings()).get_boolean(_KEY)


def set_night_light(enabled: bool, settings: "Gio.Settings | None" = None) -> None:
    (settings or _default_settings()).set_boolean(_KEY, enabled)
