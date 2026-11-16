from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from duskcurve.backend import FakeMutterTransport, MutterBackend
from duskcurve.color import build_ramp
from duskcurve.daemon.config import DisplayConfig, default_config
from duskcurve.daemon.orchestrator import NEUTRAL_KELVIN, Orchestrator
from duskcurve.daemon.rules import Rule, WindowState

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
    # manual_transition_seconds=0.0 too: this test is about rejection not
    # poisoning state, not about easing pace, and the clock never advances
    # between ticks here so a non-zero manual pace would leave factor at 0.
    cfg = replace(default_config(), transition_seconds=0.0, manual_transition_seconds=0.0)
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
    # A deliberate manual set eases at manual_transition_seconds (default
    # 2.0s), not the schedule's transition_seconds -- see the pacing tests
    # below. transition_seconds is set high here to prove that a slow
    # schedule pace does NOT leak into the manual change.
    cfg = replace(default_config(), transition_seconds=60.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.tick()  # first application for each connector snaps to 6500
    orch.set_manual_temp("DP-2", 2000, 10)
    clock.advance(1)  # small dt relative to the 3s manual transition
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


def test_manual_override_does_not_survive_a_monitor_being_unplugged():
    """A per-connector manual temp is pruned with the rest of the connector's
    state. Otherwise it lies dormant and silently re-applies whenever a
    monitor with the same connector name comes back -- possibly days later."""
    cfg = replace(default_config(), transition_seconds=0.0, manual_transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, t, orch = _new(cfg, clock)

    orch.set_manual_temp("HDMI-1", 2000, 0)
    orch.tick()
    assert be.get_gamma("HDMI-1") == build_ramp(4096, 2000)

    t.disable_output("HDMI-1")
    t.emit_monitors_changed()
    orch.on_monitors_changed()
    orch.tick()

    t.enable_output("HDMI-1")
    t.emit_monitors_changed()
    orch.on_monitors_changed()
    orch.tick()

    hdmi = next(d for d in orch.status()["per_display"] if d["connector"] == "HDMI-1")
    assert hdmi["manual_k"] is None
    assert hdmi["current_k"] != 2000


def test_global_manual_override_survives_a_monitor_being_unplugged():
    """The all-displays manual key is "" -- not a real connector, so pruning
    by connector must not evict it."""
    cfg = replace(default_config(), transition_seconds=0.0, manual_transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, t, orch = _new(cfg, clock)

    orch.set_manual_temp("", 2000, 0)
    orch.tick()

    t.disable_output("HDMI-1")
    t.emit_monitors_changed()
    orch.on_monitors_changed()
    orch.tick()

    dp2 = next(d for d in orch.status()["per_display"] if d["connector"] == "DP-2")
    assert dp2["manual_k"] == 2000
    assert be.get_gamma("DP-2") == build_ramp(4096, 2000)


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
        expected_keys = {
            "connector",
            "current_k",
            "enabled",
            "override",
            "rule_action",
            "manual_k",
            "manual_expires_in_s",
        }
        assert set(d.keys()) == expected_keys
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
    """With rule_transition_seconds configured, a rule change goes through the
    mired easing rather than snapping. (Rule changes are instant by DEFAULT --
    see test_rule_change_is_instant_by_default; this pins that the eased path
    still works when the user asks for it.)"""
    cfg = replace(
        default_config(),
        rules=(Rule(action="disable", match_fullscreen=True, scope="window_monitor"),),
        transition_seconds=20.0,
        rule_transition_seconds=20.0,
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


# ---------------------------------------------------------------------------
# Rule transition speed (separate from the schedule's transition_seconds)
# ---------------------------------------------------------------------------


def _rule_cfg(**kw):
    """Fullscreen->disable on the window's own monitor, slow schedule easing."""
    return replace(
        default_config(),
        rules=(Rule(action="disable", match_fullscreen=True, scope="window_monitor"),),
        transition_seconds=20.0,
        **kw,
    )


def test_rule_change_is_instant_by_default():
    """A rule going active jumps to its target in one tick even though the
    schedule's own transition_seconds is 20s."""
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(_rule_cfg(), clock)
    orch.tick()
    warm = orch.status()["per_display"][0]["current_k"]
    assert warm < NEUTRAL_KELVIN

    orch.set_window_state(WindowState("org.game.Game", True, "DP-2"))
    clock.advance(2.0)
    orch.tick()
    assert orch.status()["per_display"][0]["current_k"] == round(NEUTRAL_KELVIN)


def test_rule_release_is_instant_by_default():
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(_rule_cfg(), clock)
    orch.tick()
    warm = orch.status()["per_display"][0]["current_k"]

    orch.set_window_state(WindowState("org.game.Game", True, "DP-2"))
    clock.advance(2.0)
    orch.tick()
    orch.set_window_state(None)
    clock.advance(2.0)
    orch.tick()
    assert orch.status()["per_display"][0]["current_k"] == warm


def test_rule_transition_seconds_eases_when_configured():
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(_rule_cfg(rule_transition_seconds=10.0), clock)
    orch.tick()
    warm = orch.status()["per_display"][0]["current_k"]

    orch.set_window_state(WindowState("org.game.Game", True, "DP-2"))
    clock.advance(2.0)
    orch.tick()
    mid = orch.status()["per_display"][0]["current_k"]
    assert warm < mid < round(NEUTRAL_KELVIN)

    # and it keeps easing at the rule rate on subsequent ticks, not the
    # schedule's slower rate
    clock.advance(2.0)
    orch.tick()
    assert orch.status()["per_display"][0]["current_k"] > mid


def test_instant_rule_does_not_speed_up_non_rule_changes():
    """The instant-by-default rule path is scoped to actual rule changes. A
    schedule-paced target change -- here a per-display override edit, which is
    neither a rule action, a manual temp, nor a pause/enable toggle -- still
    eases at transition_seconds and must not snap.

    A manual temp is NOT used as the example here (as it once was): manual
    changes now ease at their own manual_transition_seconds (2s default,
    separate from this test's rules/schedule pacing), and at the default 2s
    tick interval that settles within a single tick -- so it can no longer
    demonstrate "still eases gradually". See
    test_manual_set_converges_at_manual_pace_not_schedule_or_instant for the
    manual-pace assertion instead.
    """
    cfg = replace(default_config(), transition_seconds=20.0, rules=())
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(cfg, clock)
    orch.tick()
    warm = orch.status()["per_display"][0]["current_k"]
    assert warm < NEUTRAL_KELVIN

    orch.set_config(
        replace(cfg, displays={"DP-2": DisplayConfig(override_kind="fixed", override_value=6500)})
    )
    clock.advance(2.0)
    orch.tick()
    mid = orch.status()["per_display"][0]["current_k"]
    # eased partway toward 6500, not snapped to it
    assert warm < mid < round(NEUTRAL_KELVIN)


def test_rule_transition_only_affects_the_scoped_connector():
    clock = FakeClock(_dt(23, 0))
    be, t, orch = _new(_rule_cfg(), clock)
    orch.tick()
    warm = orch.status()["per_display"][1]["current_k"]

    orch.set_window_state(WindowState("org.game.Game", True, "DP-2"))
    clock.advance(2.0)
    orch.tick()
    per = {d["connector"]: d for d in orch.status()["per_display"]}
    assert per["DP-2"]["current_k"] == round(NEUTRAL_KELVIN)
    assert per["HDMI-1"]["current_k"] == warm


# ---------------------------------------------------------------------------
# Indefinite manual temps and manual_k in status (Task M6.1)
# ---------------------------------------------------------------------------


def test_manual_temp_with_zero_duration_never_expires():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_manual_temp("DP-2", 2700, 0)
    clock.advance(48 * 3600)  # 48 hours in seconds
    orch.tick()
    status = orch.status()
    entry = next(d for d in status["per_display"] if d["connector"] == "DP-2")
    assert entry["manual_k"] == 2700
    assert entry["current_k"] == 2700


def test_manual_temp_with_negative_duration_never_expires():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_manual_temp("DP-2", 2700, -5)
    clock.advance(3600)  # 1 hour in seconds
    assert orch.status()["per_display"][0]["manual_k"] == 2700


def test_manual_temp_with_positive_duration_still_expires():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_manual_temp("DP-2", 2700, 30)
    clock.advance(29 * 60)  # 29 minutes in seconds
    assert orch.status()["per_display"][0]["manual_k"] == 2700
    clock.advance(2 * 60)  # 2 more minutes
    assert orch.status()["per_display"][0]["manual_k"] is None


def test_status_manual_k_is_null_when_no_manual_temp():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    entry = orch.status()["per_display"][0]
    assert "manual_k" in entry
    assert entry["manual_k"] is None


def test_status_manual_k_prefers_the_per_connector_entry_over_global():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_manual_temp("", 3000, 0)  # all displays
    orch.set_manual_temp("DP-2", 2200, 0)  # this one specifically
    by_connector = {d["connector"]: d["manual_k"] for d in orch.status()["per_display"]}
    assert by_connector["DP-2"] == 2200
    assert by_connector["HDMI-1"] == 3000


def test_clear_manual_temp_clears_manual_k():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_manual_temp("DP-2", 2700, 0)
    orch.clear_manual_temp("DP-2")
    assert orch.status()["per_display"][0]["manual_k"] is None


def test_status_keeps_every_pre_m6_key():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)

    entry = orch.status()["per_display"][0]
    for key in ("connector", "current_k", "enabled", "override", "rule_action"):
        assert key in entry


# ---------------------------------------------------------------------------
# Cause-based pacing (M6 hardware-pass fix): schedule / manual / toggle / rule
# ---------------------------------------------------------------------------
#
# transition_seconds=20 (schedule) and manual_transition_seconds=3 (manual;
# set explicitly here, above the 2.0 default) are kept far apart so a wrong pacing
# category produces an unmistakably wrong amount of movement, not just a
# slightly-off number.


def _k(orch, connector="DP-2") -> float:
    return next(d for d in orch.status()["per_display"] if d["connector"] == connector)[
        "current_k"
    ]


def test_pausing_reaches_neutral_in_one_tick_not_gradually():
    cfg = replace(default_config(), transition_seconds=20.0)
    clock = FakeClock(_dt(3, 0))  # deep night: curve wants 2700
    be, _t, orch = _new(cfg, clock)
    orch.tick()  # first-ever application snaps to the curve value
    assert _k(orch) == 2700

    orch.set_paused(True)
    clock.advance(1)  # tiny compared to the 20s schedule pace
    orch.tick()
    assert _k(orch) == round(NEUTRAL_KELVIN)


def test_unpausing_returns_to_the_curve_in_one_tick():
    cfg = replace(default_config(), transition_seconds=20.0)
    clock = FakeClock(_dt(3, 0))
    be, _t, orch = _new(cfg, clock)
    orch.tick()
    orch.set_paused(True)
    clock.advance(1)
    orch.tick()
    assert _k(orch) == round(NEUTRAL_KELVIN)

    orch.set_paused(False)
    clock.advance(1)  # tiny compared to the 20s schedule pace
    orch.tick()
    assert _k(orch) == 2700


def test_disabling_a_display_is_instant_sibling_keeps_schedule_pace():
    cfg = replace(default_config(), transition_seconds=20.0)
    clock = FakeClock(_dt(3, 0))  # both connectors start at 2700
    be, _t, orch = _new(cfg, clock)
    orch.tick()
    assert _k(orch, "DP-2") == 2700
    assert _k(orch, "HDMI-1") == 2700

    # Disable DP-2 and, in the same config push, give HDMI-1 a new fixed
    # target so its schedule-paced movement is observable.
    orch.set_config(
        replace(
            cfg,
            displays={
                "DP-2": DisplayConfig(enabled=False),
                "HDMI-1": DisplayConfig(override_kind="fixed", override_value=5000),
            },
        )
    )
    clock.advance(5)  # small compared to the 20s schedule pace
    orch.tick()

    assert _k(orch, "DP-2") == round(NEUTRAL_KELVIN)  # instant, one tick
    # HDMI-1 is unaffected by DP-2's toggle: it still eases at schedule pace,
    # so a 5s tick against a 20s transition has moved but not arrived.
    assert 2700 < _k(orch, "HDMI-1") < 5000


def test_reenabling_a_display_is_instant():
    cfg = replace(
        default_config(),
        transition_seconds=20.0,
        displays={"DP-2": DisplayConfig(enabled=False)},
    )
    clock = FakeClock(_dt(3, 0))
    be, _t, orch = _new(cfg, clock)
    orch.tick()
    assert _k(orch) == round(NEUTRAL_KELVIN)

    orch.set_config(replace(cfg, displays={"DP-2": DisplayConfig(enabled=True)}))
    clock.advance(1)  # tiny compared to the 20s schedule pace
    orch.tick()
    assert _k(orch) == 2700


def test_reenabling_mid_transition_rejoins_the_sibling_not_the_endpoint():
    """Disabling a display mid-transition and re-enabling it must put it back
    where its still-transitioning siblings are, not at the transition's
    endpoint. Otherwise the two displays sit at different temperatures for the
    rest of the transition."""
    cfg = replace(default_config(), transition_seconds=60.0)
    clock = FakeClock(_dt(13, 0))  # noon: both connectors settle at 6500
    be, _t, orch = _new(cfg, clock)
    orch.tick()
    assert _k(orch, "DP-2") == 6500
    assert _k(orch, "HDMI-1") == 6500

    # Start a slow schedule-paced transition on both displays at once.
    warm = replace(
        cfg,
        displays={
            c: DisplayConfig(override_kind="fixed", override_value=2700)
            for c in ("DP-2", "HDMI-1")
        },
    )
    orch.set_config(warm)
    for _ in range(3):
        clock.advance(2)
        orch.tick()
    assert _k(orch, "DP-2") == _k(orch, "HDMI-1")
    assert 2700 < _k(orch, "DP-2") < 6500  # genuinely mid-transition

    # Disable DP-2: instant to neutral, sibling keeps easing.
    off = replace(
        warm,
        displays={
            **warm.displays,
            "DP-2": DisplayConfig(
                enabled=False, override_kind="fixed", override_value=2700
            ),
        },
    )
    orch.set_config(off)
    clock.advance(2)
    orch.tick()
    assert _k(orch, "DP-2") == round(NEUTRAL_KELVIN)

    # Re-enable it: instant, but to wherever the sibling is right now.
    orch.set_config(warm)
    clock.advance(2)
    orch.tick()
    assert _k(orch, "DP-2") == _k(orch, "HDMI-1")

    # ...and they stay together for the rest of the transition.
    for _ in range(6):
        clock.advance(2)
        orch.tick()
        assert _k(orch, "DP-2") == _k(orch, "HDMI-1")

    clock.advance(60)  # well past the 60s time constant: both converge
    orch.tick()
    assert _k(orch, "DP-2") == 2700
    assert _k(orch, "HDMI-1") == 2700


def test_backwards_clock_step_mid_transition_holds_instead_of_diverging():
    """A wall-clock step backwards (NTP correction, resume from suspend) gives
    a negative dt. Easing by a negative factor moves *away* from the target and
    straight through mired's singularity at 0, producing negative Kelvin and a
    garbage ramp. A tick that goes backwards in time must simply hold."""
    cfg = replace(default_config(), transition_seconds=20.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)
    orch.tick()

    orch.set_config(
        replace(
            cfg,
            displays={
                c: DisplayConfig(override_kind="fixed", override_value=2700)
                for c in ("DP-2", "HDMI-1")
            },
        )
    )
    clock.advance(2)
    orch.tick()
    mid = _k(orch)
    assert 2700 < mid < 6500

    clock.advance(-30)  # the clock steps backwards mid-transition
    orch.tick()
    assert _k(orch) == mid  # held, not flung past the target

    clock.advance(2)  # and time moving forward again resumes the transition
    orch.tick()
    assert 2700 < _k(orch) < mid


def _expiry(orch, connector="DP-2"):
    return next(d for d in orch.status()["per_display"] if d["connector"] == connector)[
        "manual_expires_in_s"
    ]


def test_status_reports_no_expiry_when_no_manual_override_is_active():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    _be, _t, orch = _new(cfg, clock)
    orch.tick()
    assert _expiry(orch) is None


def test_status_reports_no_expiry_for_an_until_cleared_manual_override():
    """duration_min <= 0 means "until explicitly cleared": there is a manual
    override but no expiry, and the two states must stay distinguishable."""
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    _be, _t, orch = _new(cfg, clock)
    orch.tick()
    orch.set_manual_temp("DP-2", 2000, 0)
    orch.tick()

    st = next(d for d in orch.status()["per_display"] if d["connector"] == "DP-2")
    assert st["manual_k"] == 2000  # override IS active
    assert st["manual_expires_in_s"] is None  # it just never expires


def test_status_counts_down_a_timed_manual_override():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    _be, _t, orch = _new(cfg, clock)
    orch.tick()
    orch.set_manual_temp("DP-2", 2000, 30)
    assert _expiry(orch) == 1800

    clock.advance(600)
    orch.tick()
    assert _expiry(orch) == 1200


def test_status_drops_the_expiry_once_the_manual_override_has_expired():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    _be, _t, orch = _new(cfg, clock)
    orch.tick()
    orch.set_manual_temp("DP-2", 2000, 1)
    assert _expiry(orch) == 60

    clock.advance(61)
    orch.tick()
    st = next(d for d in orch.status()["per_display"] if d["connector"] == "DP-2")
    assert st["manual_k"] is None
    assert st["manual_expires_in_s"] is None


def test_a_global_manual_override_reports_its_expiry_on_every_display():
    """Connector "" applies to all displays, so each of them must report the
    same countdown rather than only the connector the call named."""
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    _be, _t, orch = _new(cfg, clock)
    orch.tick()
    orch.set_manual_temp("", 2000, 45)

    assert _expiry(orch, "DP-2") == 2700
    assert _expiry(orch, "HDMI-1") == 2700


def test_a_per_connector_expiry_wins_over_the_global_one():
    cfg = replace(default_config(), transition_seconds=0.0)
    clock = FakeClock(_dt(13, 0))
    _be, _t, orch = _new(cfg, clock)
    orch.tick()
    orch.set_manual_temp("", 2000, 60)
    orch.set_manual_temp("DP-2", 3000, 10)

    assert _expiry(orch, "DP-2") == 600
    assert _expiry(orch, "HDMI-1") == 3600


def test_manual_set_converges_at_manual_pace_not_schedule_or_instant():
    cfg = replace(default_config(), transition_seconds=20.0, manual_transition_seconds=3.0)
    clock = FakeClock(_dt(13, 0))  # noon: curve wants 6500
    be, _t, orch = _new(cfg, clock)
    orch.tick()
    assert _k(orch) == 6500

    orch.set_manual_temp("DP-2", 2000, 60)
    clock.advance(1)  # 1s against a 3s manual pace: moved, not arrived
    orch.tick()
    k = _k(orch)
    # at manual pace (3s) this lands around 3714K; at schedule pace (20s) it
    # would barely move off 6500 (~5843K); instant would already be at 2000K.
    assert 3300 < k < 4200


def test_clear_manual_temp_returns_to_curve_at_manual_pace():
    cfg = replace(default_config(), transition_seconds=20.0, manual_transition_seconds=3.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)
    orch.tick()
    orch.set_manual_temp("DP-2", 2000, 60)
    clock.advance(5)  # exceeds the 3s manual pace: fully converges
    orch.tick()
    assert _k(orch) == 2000

    orch.clear_manual_temp("DP-2")
    clock.advance(1)  # 1s against a 3s manual pace: moved, not arrived
    orch.tick()
    k = _k(orch)
    # at manual pace (3s) this lands around 2600K; at schedule pace (20s) it
    # would barely move off 2000K (~2072K).
    assert 2200 < k < 3200


def test_manual_temp_expiry_drifts_back_at_schedule_pace_not_manual_pace():
    """The subtle distinction: a deliberate clear_manual_temp eases at manual
    pace (see above), but a manual temp expiring on its own is not a
    deliberate action and must ease back at the slower schedule pace."""
    cfg = replace(default_config(), transition_seconds=20.0, manual_transition_seconds=3.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)
    orch.tick()

    orch.set_manual_temp("DP-2", 2000, 1)  # expires 60s from now
    clock.advance(5)  # exceeds the 3s manual pace: fully converges to 2000
    orch.tick()
    assert _k(orch) == 2000

    clock.advance(54)  # 59s elapsed since the manual set: not expired yet
    orch.tick()
    assert _k(orch) == 2000  # unchanged: target hasn't moved

    clock.advance(2)  # 61s elapsed: expired. dt for this tick is only 2s.
    orch.tick()
    k = _k(orch)
    # at schedule pace (20s) a 2s tick barely moves off 2000K (~2149K); at
    # manual pace (3s) it would already be around 3714K.
    assert 2000 < k < 2300


def test_pacing_persists_across_ticks_until_convergence_then_resets_to_schedule():
    cfg = replace(default_config(), transition_seconds=20.0, manual_transition_seconds=3.0)
    clock = FakeClock(_dt(13, 0))
    be, _t, orch = _new(cfg, clock)
    orch.tick()

    orch.set_manual_temp("DP-2", 2000, 1)  # expires 60s from now
    clock.advance(1)
    orch.tick()
    first = _k(orch)
    assert 2000 < first < 6500  # manual pace, first tick

    clock.advance(1)
    orch.tick()
    second = _k(orch)
    assert 2000 < second < first  # still easing at manual pace, second tick

    clock.advance(10)  # well past manual pace: fully converges (12s elapsed)
    orch.tick()
    assert _k(orch) == 2000

    clock.advance(46)  # 58s elapsed since the manual set: not expired yet
    orch.tick()
    assert _k(orch) == 2000  # unchanged: target hasn't moved

    # Pacing has reset to "schedule" (it reset back at the convergence tick
    # above). Now the manual temp expires on its own -- not a deliberate
    # action -- so the drift back to the curve eases at the slow schedule
    # rate, not the fast manual rate, even though "manual" was this
    # connector's most recent active category.
    clock.advance(3)  # 61s elapsed: expired. dt for this tick is only 3s.
    orch.tick()
    third = _k(orch)
    assert 2000 < third < 2600


# ---------------------------------------------------------------------------
# M9: unfocused fullscreen windows (set_window_state's unfocused=, status()["windows"])
# ---------------------------------------------------------------------------


def test_unfocused_fullscreen_window_drives_its_connector():
    cfg = replace(
        default_config(),
        rules=(
            Rule(
                action="disable",
                match_fullscreen=True,
                match_unfocused=True,
                scope="window_monitor",
            ),
        ),
        transition_seconds=0.0,
    )
    clock = FakeClock(_dt(23, 0))
    be, _t, orch = _new(cfg, clock)

    orch.set_window_state(None, [WindowState("mpv", True, "DP-2")])
    orch.tick()
    per = {d["connector"]: d for d in orch.status()["per_display"]}
    assert per["DP-2"]["current_k"] == round(NEUTRAL_KELVIN)
    assert per["HDMI-1"]["current_k"] != round(NEUTRAL_KELVIN)


def test_status_reports_the_unfocused_windows():
    clock = FakeClock(_dt(23, 0))
    be, _t, orch = _new(replace(default_config(), rules=()), clock)

    orch.set_window_state(
        WindowState("gimp", False, "HDMI-1"),
        [WindowState("mpv", True, "DP-2")],
    )
    status = orch.status()
    assert status["window"]["app_id"] == "gimp"
    assert status["windows"] == [
        {"app_id": "gimp", "fullscreen": False, "focused": True, "monitor_connector": "HDMI-1"},
        {"app_id": "mpv", "fullscreen": True, "focused": False, "monitor_connector": "DP-2"},
    ]


def test_status_windows_is_empty_when_there_is_no_window():
    clock = FakeClock(_dt(23, 0))
    be, _t, orch = _new(replace(default_config(), rules=()), clock)

    orch.set_window_state(None)
    assert orch.status()["windows"] == []
    assert orch.status()["window"] is None


def test_set_window_state_defaults_unfocused_to_empty():
    clock = FakeClock(_dt(23, 0))
    be, _t, orch = _new(replace(default_config(), rules=()), clock)

    orch.set_window_state(WindowState("gimp", False, "HDMI-1"))
    assert orch.status()["windows"] == [
        {"app_id": "gimp", "fullscreen": False, "focused": True, "monitor_connector": "HDMI-1"}
    ]


def test_status_rule_action_reflects_an_unfocused_window_without_ticking():
    # status() runs its OWN resolve_rules, separate from tick()'s. If it were
    # to lose the unfocused windows, every temperature would still be right
    # (tick computed those) while the GUI's per-display rule badge silently
    # reported "none" -- the Displays tab would show an untinted monitor with
    # no explanation of why.
    cfg = replace(
        default_config(),
        rules=(
            Rule(
                action="disable",
                match_fullscreen=True,
                match_unfocused=True,
                scope="window_monitor",
            ),
        ),
    )
    clock = FakeClock(_dt(23, 0))
    _be, _t, orch = _new(cfg, clock)

    orch.set_window_state(None, [WindowState("mpv", True, "DP-2")])
    per = {d["connector"]: d for d in orch.status()["per_display"]}
    assert per["DP-2"]["rule_action"] == {"kind": "disable", "kelvin": None}
    assert per["HDMI-1"]["rule_action"] == {"kind": "none", "kelvin": None}


def test_an_unfocused_rule_engages_at_rule_pace_not_schedule_pace():
    # The M5 lesson, re-checked for the new source: a rule reacting to a
    # deliberate user action must not inherit the schedule's slow drift. With
    # a 20 s schedule constant and the default 0 s rule constant, one tick
    # must land the display fully on its target -- the same guarantee a
    # focused-window rule already has.
    cfg = replace(
        default_config(),
        rules=(
            Rule(
                action="disable",
                match_fullscreen=True,
                match_unfocused=True,
                scope="window_monitor",
            ),
        ),
        transition_seconds=20.0,
    )
    assert cfg.rule_transition_seconds == 0.0
    clock = FakeClock(_dt(23, 0))
    _be, _t, orch = _new(cfg, clock)

    orch.tick()  # settle on the night curve first
    clock.advance(2)
    orch.set_window_state(None, [WindowState("mpv", True, "DP-2")])
    orch.tick()

    per = {d["connector"]: d for d in orch.status()["per_display"]}
    assert per["DP-2"]["current_k"] == round(NEUTRAL_KELVIN)


# --- Idle cost: redundant SetCrtcGamma pushes are skipped -------------------


def test_steady_state_ticks_do_not_repush_an_unchanged_ramp():
    # At noon the curve is flat: after the first push, a tick that would send
    # the exact same ramp must not call SetCrtcGamma at all. Every redundant
    # push costs gnome-shell a KMS gamma commit.
    clock = FakeClock(_dt(13, 0))
    _be, t, orch = _new(default_config(), clock)
    orch.tick()
    assert len(t.set_calls) == 2  # one per display
    for _ in range(10):
        clock.advance(2)
        orch.tick()
    assert len(t.set_calls) == 2


def test_unchanged_ramp_is_repushed_every_60_seconds():
    # Self-healing: something outside the daemon (resume, VT switch, Night
    # Light) can reset gamma, so an unchanged ramp is still re-sent once a
    # minute.
    clock = FakeClock(_dt(13, 0))
    _be, t, orch = _new(default_config(), clock)
    orch.tick()
    clock.advance(58)
    orch.tick()
    assert len(t.set_calls) == 2
    clock.advance(2)
    orch.tick()
    assert len(t.set_calls) == 4
    assert t.set_calls[2][2:] == t.set_calls[0][2:]


def test_a_clock_step_backwards_forces_a_repush():
    # A wall clock stepped back (NTP, resume) makes "time since last push"
    # negative; that must not suppress re-pushing for however long the step was.
    clock = FakeClock(_dt(13, 0))
    _be, t, orch = _new(default_config(), clock)
    orch.tick()
    clock.advance(-3600)
    orch.tick()
    assert len(t.set_calls) == 4


def test_a_changed_temperature_is_pushed_immediately():
    clock = FakeClock(_dt(13, 0))
    be, t, orch = _new(default_config(), clock)
    orch.tick()
    clock.advance(2)
    orch.set_manual_temp("DP-2", 2000, 0)
    orch.tick()
    assert len(t.set_calls) == 3  # only DP-2 changed
    assert be.get_gamma("DP-2") == build_ramp(4096, 2000)


def test_monitors_changed_forces_a_repush():
    # A hotplug/mode change can leave a CRTC with a reset gamma table, so the
    # next tick must re-send even if the temperature has not moved.
    clock = FakeClock(_dt(13, 0))
    _be, t, orch = _new(default_config(), clock)
    orch.tick()
    orch.on_monitors_changed()
    clock.advance(2)
    orch.tick()
    assert len(t.set_calls) == 4


def test_a_failed_push_is_retried_on_the_next_tick():
    # Only a push that succeeded counts as applied; otherwise a transient
    # D-Bus failure would leave the display wrong for up to a minute.
    clock = FakeClock(_dt(13, 0))
    be, t, orch = _new(default_config(), clock)
    real_apply = be.apply_ramp
    failures = {"left": 2}

    def flaky_apply(connector, r, g, b):
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("bus hiccup")
        real_apply(connector, r, g, b)

    be.apply_ramp = flaky_apply
    with pytest.raises(RuntimeError):
        orch.tick()
    with pytest.raises(RuntimeError):
        orch.tick()
    clock.advance(2)
    orch.tick()
    assert len(t.set_calls) == 2


def test_the_periodic_repush_reuses_the_ramp_instead_of_rebuilding(monkeypatch):
    import duskcurve.daemon.orchestrator as orch_mod

    builds = []
    real_build = orch_mod.build_ramp

    def counting_build(size, kelvin):
        builds.append(kelvin)
        return real_build(size, kelvin)

    monkeypatch.setattr(orch_mod, "build_ramp", counting_build)
    clock = FakeClock(_dt(13, 0))
    _be, t, orch = _new(default_config(), clock)
    for _ in range(40):  # 80 s: one periodic repush in there
        orch.tick()
        clock.advance(2)
    assert len(t.set_calls) == 4
    assert len(builds) == 2
