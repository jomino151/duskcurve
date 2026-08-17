import pytest

from fluxmax.backend.desktop_theme import (
    BACKGROUND_SCHEMA,
    INTERFACE_SCHEMA,
    USER_THEME_SCHEMA,
    FakeSettingsWriter,
    ThemeApplier,
)
from fluxmax.theme import ThemeVariant

FULL = ThemeVariant(
    color_scheme="prefer-dark",
    gtk_theme="Yaru-blue-dark",
    icon_theme="Yaru-dark",
    shell_theme="Yaru-blue-dark",
    cursor_theme="Bibata-Modern-Classic",
    wallpaper="/home/u/night.jpg",
)


def _applier(writer, existing_files=("/home/u/night.jpg",)):
    return ThemeApplier(writer, file_exists=lambda p: p in existing_files)


def test_every_element_writes_its_exact_schema_and_key():
    w = FakeSettingsWriter()
    assert _applier(w).apply(FULL) == []
    assert w.written == {
        (INTERFACE_SCHEMA, "color-scheme"): "prefer-dark",
        (INTERFACE_SCHEMA, "gtk-theme"): "Yaru-blue-dark",
        (INTERFACE_SCHEMA, "icon-theme"): "Yaru-dark",
        (INTERFACE_SCHEMA, "cursor-theme"): "Bibata-Modern-Classic",
        (USER_THEME_SCHEMA, "name"): "Yaru-blue-dark",
        (BACKGROUND_SCHEMA, "picture-uri"): "file:///home/u/night.jpg",
        (BACKGROUND_SCHEMA, "picture-uri-dark"): "file:///home/u/night.jpg",
    }


def test_wallpaper_writes_both_uri_keys():
    # Both, always: GNOME picks between them using color-scheme, which we are also
    # setting, so writing only one makes the wallpaper lag the theme by one switch.
    w = FakeSettingsWriter()
    _applier(w).apply(ThemeVariant(wallpaper="/home/u/night.jpg"))
    assert w.written[(BACKGROUND_SCHEMA, "picture-uri")] == "file:///home/u/night.jpg"
    assert w.written[(BACKGROUND_SCHEMA, "picture-uri-dark")] == "file:///home/u/night.jpg"


def test_empty_string_writes_nothing_at_all():
    w = FakeSettingsWriter()
    assert _applier(w).apply(ThemeVariant()) == []
    assert w.written == {}


def test_missing_schema_skips_only_that_element_and_never_constructs_it():
    w = FakeSettingsWriter(missing_schemas={USER_THEME_SCHEMA})
    skipped = _applier(w).apply(FULL)
    assert skipped == ["shell_theme"]
    assert (USER_THEME_SCHEMA, "name") not in w.written
    # The crash guard: a missing schema must never reach set_string, because the
    # real writer would construct a Gio.Settings and abort the process.
    assert USER_THEME_SCHEMA not in w.constructed
    assert w.written[(INTERFACE_SCHEMA, "gtk-theme")] == "Yaru-blue-dark"


def test_missing_wallpaper_file_is_skipped_and_the_rest_still_applies():
    w = FakeSettingsWriter()
    skipped = _applier(w, existing_files=()).apply(FULL)
    assert skipped == ["wallpaper"]
    assert (BACKGROUND_SCHEMA, "picture-uri") not in w.written
    assert w.written[(INTERFACE_SCHEMA, "color-scheme")] == "prefer-dark"


def test_a_rejected_write_is_reported_as_skipped():
    # set_string returns False (out-of-range enum, unwritable key) without raising.
    w = FakeSettingsWriter(unwritable={(INTERFACE_SCHEMA, "color-scheme")})
    skipped = _applier(w).apply(FULL)
    assert skipped == ["color_scheme"]
    assert w.written[(INTERFACE_SCHEMA, "gtk-theme")] == "Yaru-blue-dark"


def test_relative_wallpaper_path_is_skipped_not_raised():
    w = FakeSettingsWriter()
    skipped = _applier(w, existing_files=("night.jpg",)).apply(ThemeVariant(wallpaper="night.jpg"))
    assert skipped == ["wallpaper"]
    assert w.written == {}


def test_missing_key_skips_only_that_element_and_never_writes_it():
    # A schema that exists but lacks the specific key -- e.g. a pre-GNOME-42
    # gsettings-desktop-schemas missing 'color-scheme'. Distinct from a missing
    # SCHEMA (above): here has_schema returns True, only the key write fails.
    w = FakeSettingsWriter(missing_keys={(INTERFACE_SCHEMA, "color-scheme")})
    skipped = _applier(w).apply(FULL)
    assert skipped == ["color_scheme"]
    assert (INTERFACE_SCHEMA, "color-scheme") not in w.written
    assert w.written[(INTERFACE_SCHEMA, "gtk-theme")] == "Yaru-blue-dark"


class _RaisingWriter:
    """A writer whose has_schema raises for one schema, to simulate a broken
    gi/dconf environment (e.g. Gio.SettingsSchemaSource.get_default() is None
    on a system with no compiled schemas at all -- AttributeError on .lookup).
    """

    def __init__(self, raising_schema: str) -> None:
        self._raising_schema = raising_schema
        self.written: dict[tuple[str, str], str] = {}

    def has_schema(self, schema: str) -> bool:
        if schema == self._raising_schema:
            raise AttributeError("'NoneType' object has no attribute 'lookup'")
        return True

    def set_string(self, schema: str, key: str, value: str) -> bool:
        self.written[(schema, key)] = value
        return True


def test_a_raising_writer_skips_only_that_element_and_never_propagates():
    w = _RaisingWriter(raising_schema=USER_THEME_SCHEMA)
    skipped = _applier(w).apply(FULL)
    assert skipped == ["shell_theme"]
    assert (USER_THEME_SCHEMA, "name") not in w.written
    # The other five elements still landed.
    assert w.written[(INTERFACE_SCHEMA, "color-scheme")] == "prefer-dark"
    assert w.written[(INTERFACE_SCHEMA, "gtk-theme")] == "Yaru-blue-dark"
    assert w.written[(INTERFACE_SCHEMA, "icon-theme")] == "Yaru-dark"
    assert w.written[(INTERFACE_SCHEMA, "cursor-theme")] == "Bibata-Modern-Classic"
    assert w.written[(BACKGROUND_SCHEMA, "picture-uri")] == "file:///home/u/night.jpg"
    assert w.written[(BACKGROUND_SCHEMA, "picture-uri-dark")] == "file:///home/u/night.jpg"


def test_a_raising_wallpaper_writer_skips_only_the_wallpaper():
    w = _RaisingWriter(raising_schema=BACKGROUND_SCHEMA)
    skipped = _applier(w).apply(FULL)
    assert skipped == ["wallpaper"]
    assert (BACKGROUND_SCHEMA, "picture-uri") not in w.written
    assert w.written[(INTERFACE_SCHEMA, "color-scheme")] == "prefer-dark"


def test_gio_writer_reports_a_bogus_schema_as_absent():
    gio = pytest.importorskip("gi.repository.Gio")
    assert gio is not None
    from fluxmax.backend.desktop_theme import GioSettingsWriter

    w = GioSettingsWriter()
    assert w.has_schema(INTERFACE_SCHEMA) is True
    assert w.has_schema("org.nonexistent.bogus") is False
    # And set_string on it must return False rather than constructing (and aborting).
    assert w.set_string("org.nonexistent.bogus", "whatever", "x") is False
