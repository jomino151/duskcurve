"""Theme behaviour of the tick engine: one tick is enough to reach the correct mode."""

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from duskcurve.backend import FakeMutterTransport, MutterBackend
from duskcurve.daemon.config import ThemeConfig, default_config
from duskcurve.daemon.orchestrator import Orchestrator
from duskcurve.theme import DARK, LIGHT, ThemeVariant

BERLIN = ZoneInfo("Europe/Berlin")
# 2026-08-16 in Berlin: sunrise ~05:50, sunset ~20:30 (as computed by
# duskcurve.schedule.solar_events for this lat/lon/date).
DAY = (2026, 8, 16)

LIGHT_V = ThemeVariant("prefer-light", "Yaru", "Yaru")
DARK_V = ThemeVariant("prefer-dark", "Yaru-dark", "Yaru-dark")


def _dt(hour: int, minute: int) -> datetime:
    return datetime(DAY[0], DAY[1], DAY[2], hour, minute, tzinfo=BERLIN)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = dt

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class RecordingApplier:
    def __init__(self) -> None:
        self.applied: list[ThemeVariant] = []

    def apply(self, variant: ThemeVariant) -> list[str]:
        self.applied.append(variant)
        return []


def _cfg(**theme_kwargs):
    theme = ThemeConfig(enabled=True, light=LIGHT_V, dark=DARK_V, **theme_kwargs)
    return replace(default_config(), theme=theme, transition_seconds=0.0)


def _new(cfg, clock):
    applier = RecordingApplier()
    orch = Orchestrator(MutterBackend(FakeMutterTransport()), cfg, clock, theme_applier=applier)
    return orch, applier


def test_first_tick_applies_unconditionally():
    orch, applier = _new(_cfg(), FakeClock(_dt(13, 0)))
    orch.tick()
    assert applier.applied == [LIGHT_V]


def test_crossing_sunset_applies_dark_exactly_once():
    # Sunset is ~20:30; the probe times used throughout this file straddle it with margin.
    clock = FakeClock(_dt(20, 25))
    orch, applier = _new(_cfg(), clock)
    orch.tick()
    assert applier.applied == [LIGHT_V]
    clock.set(_dt(20, 34))
    orch.tick()
    assert applier.applied == [LIGHT_V, DARK_V]
    for _ in range(5):
        clock.advance(2)
        orch.tick()
    assert applier.applied == [LIGHT_V, DARK_V], "edge-triggered: no repeat writes"


def test_a_suspend_across_sunset_is_corrected_by_a_single_tick():
    # A switch scheduled inside a suspend window must still happen: the clock
    # jumps two hours and one tick corrects the theme.
    clock = FakeClock(_dt(20, 25))
    orch, applier = _new(_cfg(), clock)
    orch.tick()
    assert applier.applied == [LIGHT_V]
    clock.set(_dt(22, 34))
    orch.tick()
    assert applier.applied == [LIGHT_V, DARK_V]


def test_a_daemon_started_after_dark_applies_dark_on_its_first_tick():
    # A fresh orchestrator is correct immediately, not only from the next sunrise.
    orch, applier = _new(_cfg(), FakeClock(_dt(23, 30)))
    orch.tick()
    assert applier.applied == [DARK_V]


def test_disabled_theme_applies_nothing_ever():
    cfg = replace(default_config(), theme=ThemeConfig(enabled=False))
    clock = FakeClock(_dt(20, 34))
    orch, applier = _new(cfg, clock)
    orch.tick()
    clock.set(_dt(22, 0))
    orch.tick()
    assert applier.applied == []


def test_no_applier_is_a_no_op():
    orch = Orchestrator(MutterBackend(FakeMutterTransport()), _cfg(), FakeClock(_dt(13, 0)))
    orch.tick()  # must not raise


def test_paused_freezes_the_theme_then_corrects_on_unpause():
    clock = FakeClock(_dt(20, 25))
    orch, applier = _new(_cfg(), clock)
    orch.tick()
    assert applier.applied == [LIGHT_V]
    orch.set_paused(True)
    clock.set(_dt(22, 0))
    orch.tick()
    assert applier.applied == [LIGHT_V], "pause freezes the theme; there is no neutral theme"
    orch.set_paused(False)
    orch.tick()
    assert applier.applied == [LIGHT_V, DARK_V]


def test_manual_theme_wins_until_it_expires():
    clock = FakeClock(_dt(13, 0))
    orch, applier = _new(_cfg(), clock)
    orch.tick()
    assert applier.applied == [LIGHT_V]
    orch.set_theme(DARK, 60)
    orch.tick()
    assert applier.applied == [LIGHT_V, DARK_V]
    clock.set(_dt(13, 30))
    orch.tick()
    assert applier.applied == [LIGHT_V, DARK_V], "still inside the hour"
    clock.set(_dt(14, 30))
    orch.tick()
    assert applier.applied == [LIGHT_V, DARK_V, LIGHT_V], "expired, back to the schedule"


def test_manual_theme_with_no_duration_never_expires():
    clock = FakeClock(_dt(13, 0))
    orch, applier = _new(_cfg(), clock)
    orch.tick()
    orch.set_theme(DARK, 0)  # 0 = until cleared
    orch.tick()
    clock.set(_dt(18, 0))
    orch.tick()
    assert applier.applied == [LIGHT_V, DARK_V]
    orch.clear_theme()
    orch.tick()
    assert applier.applied == [LIGHT_V, DARK_V, LIGHT_V]


def test_set_theme_rejects_an_unknown_mode():
    orch, _applier = _new(_cfg(), FakeClock(_dt(13, 0)))
    try:
        orch.set_theme("sepia", 0)
    except ValueError:
        return
    raise AssertionError("expected ValueError for an unknown mode")


def test_editing_the_theme_config_reapplies_the_current_mode():
    # A theme edit takes effect at once, not at the next sunrise.
    clock = FakeClock(_dt(22, 0))
    cfg = _cfg()
    orch, applier = _new(cfg, clock)
    orch.tick()
    assert applier.applied == [DARK_V]
    new_dark = ThemeVariant("prefer-dark", "Yaru-blue-dark", "Yaru-dark")
    orch.set_config(replace(cfg, theme=replace(cfg.theme, dark=new_dark)))
    orch.tick()
    assert applier.applied == [DARK_V, new_dark]


def test_status_reports_the_theme():
    clock = FakeClock(_dt(22, 0))
    orch, _applier = _new(_cfg(), clock)
    orch.tick()
    theme = orch.status()["theme"]
    assert theme["enabled"] is True
    assert theme["mode"] == DARK
    assert theme["manual"] is None and theme["manual_expires_in_s"] is None
    orch.set_theme(LIGHT, 30)
    theme = orch.status()["theme"]
    assert theme["manual"] == LIGHT
    assert 1700 <= theme["manual_expires_in_s"] <= 1800


def test_status_evicts_an_expired_manual_theme_override_while_paused():
    # _tick_theme returns at the pause guard before eviction runs, so status()
    # must evict expired overrides itself.
    clock = FakeClock(_dt(13, 0))
    orch, _applier = _new(_cfg(), clock)
    orch.tick()
    orch.set_theme(DARK, 30)
    orch.set_paused(True)
    clock.advance(31 * 60)
    theme = orch.status()["theme"]
    assert theme["manual"] is None
    assert theme["manual_expires_in_s"] is None


def test_custom_anchors_are_honoured():
    cfg = _cfg(light_from="07:30", dark_from="19:00")
    clock = FakeClock(_dt(7, 0))
    orch, applier = _new(cfg, clock)
    orch.tick()
    assert applier.applied == [DARK_V]
    clock.set(_dt(8, 0))
    orch.tick()
    assert applier.applied == [DARK_V, LIGHT_V]
