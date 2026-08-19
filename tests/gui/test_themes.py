from fluxmax.gui.themes import list_gtk_themes, list_shell_themes


def _tree(tmp_path, spec):
    for name, subdirs in spec.items():
        for sub in subdirs:
            (tmp_path / name / sub).mkdir(parents=True)
        if not subdirs:
            (tmp_path / name).mkdir(parents=True)
    return [str(tmp_path)]


def test_gtk_themes_are_listed_sorted(tmp_path):
    dirs = _tree(tmp_path, {"Yaru-dark": ["gtk-3.0"], "Yaru": ["gtk-3.0"], "Adwaita": []})
    assert list_gtk_themes(dirs) == ["Adwaita", "Yaru", "Yaru-dark"]


def test_shell_themes_need_a_gnome_shell_subdirectory(tmp_path):
    dirs = _tree(tmp_path, {"Yaru": ["gnome-shell"], "Adwaita": ["gtk-3.0"]})
    assert list_shell_themes(dirs) == ["Yaru"]


def test_duplicates_across_directories_appear_once(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "Yaru" / "gtk-3.0").mkdir(parents=True)
    (b / "Yaru" / "gtk-3.0").mkdir(parents=True)
    assert list_gtk_themes([str(a), str(b)]) == ["Yaru"]


def test_a_missing_directory_is_ignored(tmp_path):
    assert list_gtk_themes([str(tmp_path / "nope")]) == []
