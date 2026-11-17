from duskcurve.color.presets import PRESETS, Preset


def test_has_standard_preset_set():
    assert PRESETS[0] == Preset(1200, "Ember")
    assert PRESETS[-1] == Preset(6500, "Daylight")


def test_presets_sorted_ascending():
    kelvins = [p.kelvin for p in PRESETS]
    assert kelvins == sorted(kelvins)


def test_expected_names_present():
    names = {p.name for p in PRESETS}
    assert {"Candle", "Incandescent", "Halogen", "Sunlight"} <= names
