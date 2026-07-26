from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fluxmax.backend import FakeMutterTransport, MutterBackend
from fluxmax.color import build_ramp
from fluxmax.daemon.config import DisplayConfig, default_config
from fluxmax.daemon.orchestrator import NEUTRAL_KELVIN, Orchestrator

BERLIN = ZoneInfo("Europe/Berlin")
DAY = (2026, 7, 25)


def _dt(hour: int, minute: int) -> datetime:
    return datetime(DAY[0], DAY[1], DAY[2], hour, minute, tzinfo=BERLIN)


class FakeClock:
    """A mutable tz-aware clock: advance it manually between ticks."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = dt

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def _new(cfg, clock, ramp_size=4096):
    t = FakeMutterTransport(ramp_size=ramp_size)
    be = MutterBackend(t)
    return be, t, Orchestrator(be, cfg, clock)


def test_day_sweep_noon_and_deep_night():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.tick()
    expected = build_ramp(4096, 6500)
    assert be.get_gamma("DP-2") == expected
    assert be.get_gamma("HDMI-1") == expected

    clock.set(_dt(3, 0))
    orch.tick()
    expected_night = build_ramp(4096, 2700)
    assert be.get_gamma("DP-2") == expected_night
    assert be.get_gamma("HDMI-1") == expected_night


def test_warm_cap_honored_at_deep_night():
    cfg = replace(
        default_config(),
        transition_seconds=0.0,
        displays={"DP-2": DisplayConfig(override_kind="warm_cap", override_value=3400)},
    )
    clock = FakeClock(_dt(3, 0))
    be, _t, orch = _new(cfg, clock)
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 3400)
    assert be.get_gamma("HDMI-1") == build_ramp(4096, 2700)


def test_fixed_override_and_disabled_display():
    cfg = replace(
        default_config(),
        transition_seconds=0.0,
        displays={
            "DP-2": DisplayConfig(override_kind="fixed", override_value=5000),
            "HDMI-1": DisplayConfig(enabled=False),
        },
    )
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 5000)
    assert be.get_gamma("HDMI-1") == build_ramp(4096, NEUTRAL_KELVIN)


def test_manual_temp_applies_per_connector_and_expires():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_manual_temp("DP-2", 2000, 10)
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 2000)
    assert be.get_gamma("HDMI-1") == build_ramp(4096, 6500)  # untouched, curve value

    clock.advance(11 * 60)  # past the 10-minute expiry
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 6500)  # back to the curve


def test_manual_temp_empty_connector_applies_to_all():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_manual_temp("", 2500, 10)
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 2500)
    assert be.get_gamma("HDMI-1") == build_ramp(4096, 2500)


def test_manual_temp_connector_specific_beats_all():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_manual_temp("", 2500, 10)
    orch.set_manual_temp("DP-2", 2000, 10)
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 2000)
    assert be.get_gamma("HDMI-1") == build_ramp(4096, 2500)


def test_clear_manual_temp_reverts_to_curve():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_manual_temp("DP-2", 2000, 10)
    orch.clear_manual_temp("DP-2")
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 6500)


def test_pause_and_unpause():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(3, 0))  # deep night: curve would want 2700
    be, _t, orch = _new(cfg, clock)

    orch.set_paused(True)
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, NEUTRAL_KELVIN)
    assert be.get_gamma("HDMI-1") == build_ramp(4096, NEUTRAL_KELVIN)

    orch.set_paused(False)
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 2700)


def test_easing_lands_strictly_between_old_and_target():
    cfg = replace(default_config(), transition_seconds=60.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.tick()  # first application for each connector snaps to 6500
    orch.set_manual_temp("DP-2", 2000, 10)
    clock.advance(5)  # small dt relative to the 60s transition
    orch.tick()

    status = orch.status()
    dp2 = next(d for d in status["per_display"] if d["connector"] == "DP-2")
    assert 2000 < dp2["current_k"] < 6500


def test_monitors_changed_re_apply_drops_missing_connector():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, t, orch = _new(cfg, clock)

    orch.tick()
    t.disable_output("HDMI-1")
    t.emit_monitors_changed()
    orch.on_monitors_changed()
    orch.tick()

    status = orch.status()
    assert [d["connector"] for d in status["per_display"]] == ["DP-2"]
    assert be.get_gamma("DP-2") == build_ramp(4096, 6500)


def test_status_shape():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.tick()
    status = orch.status()

    assert status["mode"] == "reduce_eyestrain"
    assert status["paused"] is False
    assert status["next_transition"] is None
    connectors = {d["connector"] for d in status["per_display"]}
    assert connectors == {"DP-2", "HDMI-1"}
    for d in status["per_display"]:
        assert set(d.keys()) == {"connector", "current_k", "enabled", "override"}
        assert isinstance(d["current_k"], int)
        assert isinstance(d["enabled"], bool)
        assert isinstance(d["override"], str)


def test_status_before_any_tick_reports_neutral():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    status = orch.status()
    for d in status["per_display"]:
        assert d["current_k"] == round(NEUTRAL_KELVIN)
