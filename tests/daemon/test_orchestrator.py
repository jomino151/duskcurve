from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fluxmax.backend import FakeMutterTransport, MutterBackend
from fluxmax.color import build_ramp
from fluxmax.daemon.config import DisplayConfig, default_config
from fluxmax.daemon.orchestrator import NEUTRAL_KELVIN, Orchestrator
from fluxmax.daemon.rules import Rule, WindowState

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


def test_set_manual_temp_rejects_non_positive_kelvin_without_poisoning_ticks():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    with pytest.raises(ValueError):
        orch.set_manual_temp("DP-2", 0, 10)

    # Rejected call must not have stored anything: a normal tick still
    # produces the curve value, not a ZeroDivisionError from kelvin_to_mired.
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 6500)

    with pytest.raises(ValueError):
        orch.set_manual_temp("DP-2", -5, 10)
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 6500)

    # A valid manual temp set afterwards still works normally.
    orch.set_manual_temp("DP-2", 2000, 10)
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, 2000)


def test_disabled_display_beats_manual_temp():
    cfg = replace(
        default_config(),
        transition_seconds=0.0,
        displays={"DP-2": DisplayConfig(enabled=False)},
    )
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_manual_temp("DP-2", 2000, 10)
    orch.tick()
    assert be.get_gamma("DP-2") == build_ramp(4096, NEUTRAL_KELVIN)


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
        assert set(d.keys()) == {"connector", "current_k", "enabled", "override", "rule_action"}
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


def _fs_disable_cfg(**kw):
    return replace(
        default_config(),
        rules=(Rule(action="disable", match_fullscreen=True, scope="window_monitor"),),
        transition_seconds=0.0,
        **kw,
    )


def test_rule_disables_only_the_window_monitor():
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(_fs_disable_cfg(), clock)
    orch.tick()
    night_dp2 = orch.status()["per_display"][0]["current_k"]
    assert night_dp2 < NEUTRAL_KELVIN          # the schedule is warming at 23:00

    orch.set_window_state(WindowState("org.game.Game", True, "DP-2"))
    orch.tick()
    per = {d["connector"]: d for d in orch.status()["per_display"]}
    assert per["DP-2"]["current_k"] == round(NEUTRAL_KELVIN)
    assert per["HDMI-1"]["current_k"] == night_dp2


def test_rule_lifts_and_restores():
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(_fs_disable_cfg(), clock)
    orch.tick()
    warm = orch.status()["per_display"][0]["current_k"]

    orch.set_window_state(WindowState("org.game.Game", True, "DP-2"))
    orch.tick()
    assert orch.status()["per_display"][0]["current_k"] == round(NEUTRAL_KELVIN)

    orch.set_window_state(None)
    orch.tick()
    assert orch.status()["per_display"][0]["current_k"] == warm


def test_manual_temp_outranks_a_rule():
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(_fs_disable_cfg(), clock)
    orch.set_window_state(WindowState("org.game.Game", True, "DP-2"))
    orch.set_manual_temp("DP-2", 2500, 60)
    orch.tick()
    assert orch.status()["per_display"][0]["current_k"] == 2500


def test_paused_outranks_a_fixed_rule():
    cfg = replace(
        default_config(),
        rules=(Rule(action="fixed", action_kelvin=3000, match_app_glob="*", scope="all"),),
        transition_seconds=0.0,
    )
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(cfg, clock)
    orch.set_window_state(WindowState("org.example.App", False, "DP-2"))
    orch.set_paused(True)
    orch.tick()
    assert orch.status()["per_display"][0]["current_k"] == round(NEUTRAL_KELVIN)


def test_disabled_display_outranks_a_fixed_rule():
    cfg = replace(
        default_config(),
        displays={"DP-2": DisplayConfig(enabled=False)},
        rules=(Rule(action="fixed", action_kelvin=3000, match_app_glob="*", scope="all"),),
        transition_seconds=0.0,
    )
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(cfg, clock)
    orch.set_window_state(WindowState("org.example.App", False, "DP-2"))
    orch.tick()
    per = {d["connector"]: d for d in orch.status()["per_display"]}
    assert per["DP-2"]["current_k"] == round(NEUTRAL_KELVIN)
    assert per["HDMI-1"]["current_k"] == 3000


def test_rule_change_is_eased_not_snapped():
    cfg = replace(
        default_config(),
        rules=(Rule(action="disable", match_fullscreen=True, scope="window_monitor"),),
        transition_seconds=20.0,
    )
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(cfg, clock)
    orch.tick()
    warm = orch.status()["per_display"][0]["current_k"]

    orch.set_window_state(WindowState("org.game.Game", True, "DP-2"))
    clock.advance(2.0)
    orch.tick()
    mid = orch.status()["per_display"][0]["current_k"]
    assert warm < mid < round(NEUTRAL_KELVIN)


def test_force_day_uses_config_max_kelvin():
    cfg = replace(
        default_config(),
        max_kelvin=5800.0,
        rules=(Rule(action="force_day", match_app_glob="*", scope="all"),),
        transition_seconds=0.0,
    )
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(cfg, clock)
    orch.set_window_state(WindowState("org.example.App", False, "DP-2"))
    orch.tick()
    assert orch.status()["per_display"][0]["current_k"] == 5800


def test_status_reports_window_and_rule_action():
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(_fs_disable_cfg(), clock)
    assert orch.status()["window"] is None
    assert orch.status()["per_display"][0]["rule_action"] == {"kind": "none", "kelvin": None}

    orch.set_window_state(WindowState("org.game.Game", True, "DP-2"))
    st = orch.status()
    assert st["window"] == {
        "app_id": "org.game.Game",
        "fullscreen": True,
        "monitor_connector": "DP-2",
    }
    per = {d["connector"]: d for d in st["per_display"]}
    assert per["DP-2"]["rule_action"] == {"kind": "disable", "kelvin": None}
    assert per["HDMI-1"]["rule_action"] == {"kind": "none", "kelvin": None}


def test_m4_status_keys_are_unchanged():
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(default_config(), clock)
    st = orch.status()
    assert set(st) >= {"mode", "paused", "per_display", "next_transition"}
    assert set(st["per_display"][0]) >= {"connector", "current_k", "enabled", "override"}
