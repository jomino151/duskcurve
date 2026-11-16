"""Apply a ThemeVariant to the GNOME desktop by writing GSettings keys.

gi is imported lazily inside GioSettingsWriter, so importing this module needs no display.

Two constraints shape it:
- It never shells out to `gnome-extensions` or checks the shell's extension state: the shell
  disables user extensions on the lock screen, exactly when a scheduled switch fires unattended.
  dconf writes land regardless of lock state.
- It checks that both the schema and the key exist before every write: Gio.Settings.new() on an
  uninstalled schema and set_string() on an unknown key call g_error(), which aborts the process
  with no Python exception to catch.
"""

import logging
import os
from pathlib import Path
from typing import Any, Protocol

from duskcurve.theme import ThemeVariant

log = logging.getLogger("duskcurve.theme")

INTERFACE_SCHEMA = "org.gnome.desktop.interface"
USER_THEME_SCHEMA = "org.gnome.shell.extensions.user-theme"
BACKGROUND_SCHEMA = "org.gnome.desktop.background"

# (variant field name, schema, key) for every element that is a single plain
# string write. The wallpaper is not here: it is one field driving two keys.
_SIMPLE_ELEMENTS = (
    ("color_scheme", INTERFACE_SCHEMA, "color-scheme"),
    ("gtk_theme", INTERFACE_SCHEMA, "gtk-theme"),
    ("icon_theme", INTERFACE_SCHEMA, "icon-theme"),
    ("cursor_theme", INTERFACE_SCHEMA, "cursor-theme"),
    ("shell_theme", USER_THEME_SCHEMA, "name"),
)


class SettingsWriter(Protocol):
    def has_schema(self, schema: str) -> bool: ...

    def set_string(self, schema: str, key: str, value: str) -> bool: ...


class FakeSettingsWriter:
    """In-memory SettingsWriter for tests.

    `constructed` records every schema set_string was called with, even if it
    failed, so a test can assert callers checked has_schema first.
    """

    def __init__(
        self,
        missing_schemas: "frozenset[str] | set[str]" = frozenset(),
        unwritable: "frozenset[tuple[str, str]] | set[tuple[str, str]]" = frozenset(),
        missing_keys: "frozenset[tuple[str, str]] | set[tuple[str, str]]" = frozenset(),
    ) -> None:
        self.written: dict[tuple[str, str], str] = {}
        self.constructed: set[str] = set()
        self._missing = set(missing_schemas)
        self._unwritable = set(unwritable)
        self._missing_keys = set(missing_keys)

    def has_schema(self, schema: str) -> bool:
        return schema not in self._missing

    def set_string(self, schema: str, key: str, value: str) -> bool:
        # Record every attempt, before the missing-schema check, so a caller that
        # skips its has_schema guard is observable.
        self.constructed.add(schema)
        if schema in self._missing:
            return False
        if (schema, key) in self._missing_keys:
            return False
        if (schema, key) in self._unwritable:
            return False
        self.written[(schema, key)] = value
        return True


class GioSettingsWriter:
    """The real writer. Settings objects are cached; gi is imported on first use."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[Any, Any]] = {}

    def _gio(self) -> Any:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        return Gio

    def has_schema(self, schema: str) -> bool:
        return self._lookup_schema(schema) is not None

    def _lookup_schema(self, schema: str) -> Any:
        gio = self._gio()
        return gio.SettingsSchemaSource.get_default().lookup(schema, True)

    def set_string(self, schema: str, key: str, value: str) -> bool:
        # Guarded here as well as by the caller: a missing schema or key would
        # call g_error() and abort the process.
        cached = self._cache.get(schema)
        if cached is None:
            schema_obj = self._lookup_schema(schema)
            if schema_obj is None:
                return False
            settings = self._gio().Settings.new(schema)
            cached = self._cache[schema] = (schema_obj, settings)
        schema_obj, settings = cached
        if not schema_obj.has_key(key):
            return False
        return bool(settings.set_string(key, value))


class ThemeApplier:
    def __init__(self, writer: SettingsWriter, file_exists=os.path.isfile) -> None:
        self._writer = writer
        self._file_exists = file_exists

    def apply(self, variant: ThemeVariant) -> list[str]:
        """Apply every non-empty element of `variant`.

        Each element is attempted independently: a failure is logged and named
        in the returned list, and the remaining elements are still applied.
        """
        skipped: list[str] = []
        for field_name, schema, key in _SIMPLE_ELEMENTS:
            value = getattr(variant, field_name)
            if value:
                self._write(field_name, schema, key, value, skipped)
        self._apply_wallpaper(variant.wallpaper, skipped)
        return skipped

    def _write(self, element: str, schema: str, key: str, value: str, skipped: list[str]) -> None:
        # The writer returns False for anticipated failures, but can still raise
        # (no compiled schemas, a broken gi install); that must not stop the
        # remaining elements.
        try:
            if not self._writer.has_schema(schema):
                log.warning("skipping %s: GSettings schema %s is not installed", element, schema)
                skipped.append(element)
                return
            if not self._writer.set_string(schema, key, value):
                log.warning("skipping %s: %s %s rejected the value %r", element, schema, key, value)
                skipped.append(element)
        except Exception:
            log.warning("skipping %s: writing %s %s raised", element, schema, key, exc_info=True)
            skipped.append(element)

    def _apply_wallpaper(self, path: str, skipped: list[str]) -> None:
        if not path:
            return
        if not os.path.isabs(path):
            log.warning("skipping wallpaper: %r is not an absolute path", path)
            skipped.append("wallpaper")
            return
        if not self._file_exists(path):
            log.warning("skipping wallpaper: %s does not exist", path)
            skipped.append("wallpaper")
            return
        try:
            if not self._writer.has_schema(BACKGROUND_SCHEMA):
                log.warning("skipping wallpaper: schema %s is not installed", BACKGROUND_SCHEMA)
                skipped.append("wallpaper")
                return
            uri = Path(path).as_uri()
            # Both keys: GNOME picks one by color-scheme, which apply() also sets.
            ok_light = self._writer.set_string(BACKGROUND_SCHEMA, "picture-uri", uri)
            ok_dark = self._writer.set_string(BACKGROUND_SCHEMA, "picture-uri-dark", uri)
            if not (ok_light and ok_dark):
                log.warning("wallpaper only partly applied: %s", uri)
                skipped.append("wallpaper")
        except Exception:
            log.warning("skipping wallpaper: writing %s raised", BACKGROUND_SCHEMA, exc_info=True)
            skipped.append("wallpaper")
