import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

from fluxmax.backend.nightlight import night_light_enabled, set_night_light  # noqa: E402

_SCHEMA = "org.gnome.settings-daemon.plugins.color"


def _memory_settings():
    # In-memory backend: exercises real Gio.Settings without touching the user's config.
    return Gio.Settings.new_with_backend(_SCHEMA, Gio.memory_settings_backend_new())


def test_toggle_roundtrip():
    s = _memory_settings()
    set_night_light(False, s)
    assert night_light_enabled(s) is False
    set_night_light(True, s)
    assert night_light_enabled(s) is True
