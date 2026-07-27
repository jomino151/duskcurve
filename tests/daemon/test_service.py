from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from fluxmax.backend import FakeMutterTransport, MutterBackend
from fluxmax.daemon.config import ConfigError, default_config, dump_config, load_config
from fluxmax.daemon.orchestrator import Orchestrator
from fluxmax.daemon.service import BUS_NAME, INTERFACE_XML, OBJECT_PATH, DaemonService

BERLIN = ZoneInfo("Europe/Berlin")


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


def _dt(hour: int, minute: int) -> datetime:
    return datetime(2026, 7, 25, hour, minute, tzinfo=BERLIN)


def _new_service(tmp_path, cfg=None, on_status_changed=None):
    cfg = cfg if cfg is not None else default_config()
    config_path = tmp_path / "config.toml"
    config_path.write_text(dump_config(cfg))

    transport = FakeMutterTransport(ramp_size=4096)
    backend = MutterBackend(transport)
    clock = FakeClock(_dt(13, 0))
    orchestrator = Orchestrator(backend, cfg, clock)

    calls = []
    callback = on_status_changed if on_status_changed is not None else calls.append

    service = DaemonService(
        orchestrator,
        str(config_path),
        on_status_changed=callback,
    )
    return service, orchestrator, config_path, calls, clock


def test_get_config_round_trips_current_config(tmp_path):
    cfg = default_config()
    service, _orch, _path, _calls, _clock = _new_service(tmp_path, cfg=cfg)

    toml_text = service.get_config()
    assert load_config(toml_text) == cfg


def test_set_config_round_trips_and_persists_and_emits(tmp_path):
    cfg = default_config()
    service, _orch, config_path, calls, _clock = _new_service(tmp_path, cfg=cfg)

    modified = replace(cfg, wake="06:30", bedtime="22:00")
    service.set_config(dump_config(modified))

    assert load_config(service.get_config()) == modified
    assert load_config(config_path.read_text()) == modified
    assert len(calls) == 1


def test_set_config_invalid_toml_does_not_corrupt_state(tmp_path):
    cfg = default_config()
    service, _orch, config_path, calls, _clock = _new_service(tmp_path, cfg=cfg)

    original_file_text = config_path.read_text()

    with pytest.raises(ConfigError):
        service.set_config("not valid toml [[[")

    assert load_config(service.get_config()) == cfg
    assert config_path.read_text() == original_file_text
    assert calls == []


def test_set_config_out_of_range_value_does_not_corrupt_state(tmp_path):
    cfg = default_config()
    service, _orch, config_path, calls, _clock = _new_service(tmp_path, cfg=cfg)

    original_file_text = config_path.read_text()

    # Out-of-range latitude: hand-craft invalid TOML text directly.
    bad_toml = (
        "[location]\n"
        "latitude = 999.0\n"
        "longitude = 13.40\n"
        'timezone = "Europe/Berlin"\n\n'
        "[schedule]\n"
        'wake = "10:00"\n'
        'bedtime = "23:00"\n'
        'mode = "reduce_eyestrain"\n'
    )

    with pytest.raises(ConfigError):
        service.set_config(bad_toml)

    assert load_config(service.get_config()) == cfg
    assert config_path.read_text() == original_file_text
    assert calls == []


def test_set_paused_delegates_and_reflected_in_status_and_emits(tmp_path):
    service, orch, _path, calls, _clock = _new_service(tmp_path)

    orch.tick()
    service.set_paused(True)

    status = __import__("json").loads(service.get_status())
    assert status["paused"] is True
    assert len(calls) == 1


def test_set_manual_temp_and_clear_delegate(tmp_path):
    service, orch, _path, calls, clock = _new_service(tmp_path)

    service.set_manual_temp("DP-2", 2000, 10)
    orch.tick()
    status = __import__("json").loads(service.get_status())
    dp2 = next(d for d in status["per_display"] if d["connector"] == "DP-2")
    assert dp2["current_k"] == 2000
    assert len(calls) == 1

    service.clear_manual_temp("DP-2")
    assert len(calls) == 2


def test_reload_config_re_reads_file_and_emits(tmp_path):
    cfg = default_config()
    service, _orch, config_path, calls, _clock = _new_service(tmp_path, cfg=cfg)

    modified = replace(cfg, wake="07:15")
    config_path.write_text(dump_config(modified))

    service.reload_config()

    assert load_config(service.get_config()) == modified
    assert len(calls) == 1


def test_get_status_returns_json_of_orchestrator_status(tmp_path):
    service, orch, _path, _calls, _clock = _new_service(tmp_path)
    orch.tick()

    status_json = service.get_status()
    parsed = __import__("json").loads(status_json)
    assert parsed == orch.status()


def test_module_constants():
    assert BUS_NAME == "org.fluxmax.Daemon"
    assert OBJECT_PATH == "/org/fluxmax/Daemon"


def test_interface_xml_parses_and_exposes_contract():
    gi = pytest.importorskip("gi")
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    node_info = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
    interfaces = {iface.name: iface for iface in node_info.interfaces}
    assert "org.fluxmax.Daemon" in interfaces
    iface = interfaces["org.fluxmax.Daemon"]

    method_names = {m.name for m in iface.methods}
    assert method_names == {
        "GetStatus",
        "GetConfig",
        "SetConfig",
        "SetPaused",
        "SetManualTemp",
        "ClearManualTemp",
        "ReloadConfig",
    }

    signal_names = {s.name for s in iface.signals}
    assert signal_names == {"StatusChanged"}
