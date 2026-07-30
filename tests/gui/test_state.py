from fluxmax.daemon.config import (
    ConfigError,
    default_config,
    dump_config,
)
from fluxmax.gui.state import AppState

GOOD_STATUS = (
    '{"mode":"classic","paused":false,"per_display":'
    '[{"connector":"DP-2","current_k":3000}]}'
)


def _state(apply_fn=None):
    return AppState(apply_fn or (lambda _toml: None))


def test_starts_empty_and_disconnected():
    st = _state()
    assert st.config is None
    assert st.status is None
    assert st.connected is False
    assert st.last_error is None


def test_set_config_text_parses_and_notifies():
    st = _state()
    calls = []
    st.subscribe(lambda: calls.append(1))
    st.set_config_text(dump_config(default_config()))
    assert st.config.mode == default_config().mode
    assert len(calls) == 1


def test_set_status_text_parses_and_notifies():
    st = _state()
    calls = []
    st.subscribe(lambda: calls.append(1))
    st.set_status_text(GOOD_STATUS)
    assert st.status.displays[0].connector == "DP-2"
    assert len(calls) == 1


def test_unsubscribe_stops_notifications():
    st = _state()
    calls = []
    unsubscribe = st.subscribe(lambda: calls.append(1))
    st.set_status_text(GOOD_STATUS)
    unsubscribe()
    st.set_status_text(GOOD_STATUS)
    assert len(calls) == 1


def test_every_subscriber_is_notified():
    st = _state()
    a, b = [], []
    st.subscribe(lambda: a.append(1))
    st.subscribe(lambda: b.append(1))
    st.set_connected(True)
    assert a == [1] and b == [1]


def test_a_raising_subscriber_does_not_block_the_others():
    st = _state()
    seen = []

    def boom():
        raise RuntimeError("subscriber exploded")

    st.subscribe(boom)
    st.subscribe(lambda: seen.append(1))
    st.set_connected(True)          # must not propagate
    assert seen == [1]


def test_apply_config_sends_toml_and_stores_optimistically():
    sent = []
    st = _state(sent.append)
    st.set_config_text(dump_config(default_config()))
    new = default_config().__class__(**{**default_config().__dict__, "mode": "classic"})
    assert st.apply_config(new) is True
    assert st.config.mode == "classic"
    assert len(sent) == 1
    assert "classic" in sent[0]


def test_apply_config_notifies_before_the_call_completes():
    # The optimistic update must be visible to subscribers, so the UI does not
    # visibly lag the D-Bus round trip.
    order = []

    def apply_fn(_toml):
        order.append("sent")

    st = AppState(apply_fn)
    st.set_config_text(dump_config(default_config()))
    st.subscribe(lambda: order.append("notified"))
    cfg = default_config()
    st.apply_config(cfg)
    assert order.index("notified") < order.index("sent")


def test_apply_config_reverts_and_records_the_error_on_rejection():
    def apply_fn(_toml):
        raise ConfigError("bedtime must be an 'HH:MM' string")

    st = AppState(apply_fn)
    original = default_config()
    st.set_config_text(dump_config(original))
    broken = default_config().__class__(**{**original.__dict__, "mode": "classic"})
    assert st.apply_config(broken) is False
    assert st.config.mode == original.mode           # reverted
    assert "bedtime" in st.last_error


def test_apply_config_revert_notifies_subscribers():
    def apply_fn(_toml):
        raise ConfigError("nope")

    st = AppState(apply_fn)
    st.set_config_text(dump_config(default_config()))
    calls = []
    st.subscribe(lambda: calls.append(1))
    st.apply_config(default_config())
    assert len(calls) == 2           # optimistic set, then revert


def test_last_error_clears_on_a_successful_apply():
    failing = {"fail": True}

    def apply_fn(_toml):
        if failing["fail"]:
            raise ConfigError("nope")

    st = AppState(apply_fn)
    st.set_config_text(dump_config(default_config()))
    st.apply_config(default_config())
    assert st.last_error is not None
    failing["fail"] = False
    st.apply_config(default_config())
    assert st.last_error is None


def test_set_connected_false_clears_nothing_but_the_flag():
    st = _state()
    st.set_status_text(GOOD_STATUS)
    st.set_connected(True)
    st.set_connected(False)
    assert st.connected is False
    assert st.status is not None      # last known state stays for display


def test_malformed_inbound_status_is_ignored_and_records_an_error():
    st = _state()
    st.set_status_text(GOOD_STATUS)
    st.set_status_text("not json")
    assert st.status.displays[0].connector == "DP-2"   # last good state kept
    assert st.last_error is not None


def test_malformed_inbound_config_is_ignored_and_records_an_error():
    st = _state()
    st.set_config_text(dump_config(default_config()))
    st.set_config_text("this is not toml = = =")
    assert st.config is not None
    assert st.last_error is not None


def test_successful_set_status_text_does_not_clear_apply_error():
    def apply_fn(_toml):
        raise ConfigError("apply failed")

    st = AppState(apply_fn)
    st.set_config_text(dump_config(default_config()))
    st.apply_config(default_config())
    assert st.last_error is not None
    # A subsequent status update must NOT clear the error
    st.set_status_text(GOOD_STATUS)
    assert st.last_error is not None


def test_successful_set_config_text_does_not_clear_apply_error():
    def apply_fn(_toml):
        raise ConfigError("apply failed")

    st = AppState(apply_fn)
    original = default_config()
    st.set_config_text(dump_config(original))
    st.apply_config(original)
    assert st.last_error is not None
    # A subsequent config update must NOT clear the error
    st.set_config_text(dump_config(original))
    assert st.last_error is not None


def test_clear_error_clears_and_notifies():
    st = _state()
    st.set_config_text("invalid toml = = =")
    assert st.last_error is not None
    calls = []
    st.subscribe(lambda: calls.append(1))
    st.clear_error()
    assert st.last_error is None
    assert len(calls) == 1


def test_set_error_sets_the_message_and_notifies():
    st = _state()
    calls = []
    st.subscribe(lambda: calls.append(1))
    st.set_error("set_manual_temp failed: timed out")
    assert st.last_error == "set_manual_temp failed: timed out"
    assert len(calls) == 1


def test_set_error_is_cleared_by_a_subsequent_successful_apply_config():
    st = _state()
    st.set_config_text(dump_config(default_config()))
    st.set_error("set_manual_temp failed: timed out")
    assert st.last_error is not None
    assert st.apply_config(default_config()) is True
    assert st.last_error is None


def test_clear_error_after_set_error_clears_it():
    st = _state()
    st.set_error("set_manual_temp failed: timed out")
    assert st.last_error is not None
    calls = []
    st.subscribe(lambda: calls.append(1))
    st.clear_error()
    assert st.last_error is None
    assert len(calls) == 1
