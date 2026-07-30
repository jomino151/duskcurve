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
    # 3.0s), not the schedule's transition_seconds -- see the pacing tests
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
# transition_seconds=20 (schedule) and manual_transition_seconds=3 (manual,
# the default) are always kept far apart in these tests so a wrong pacing
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
