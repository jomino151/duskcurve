"""uninstall.sh: reverses install.sh, and restores Night Light."""

NIGHT_LIGHT = "org.gnome.settings-daemon.plugins.color night-light-enabled"


def test_removes_every_artifact_the_installer_created(sandbox):
    sandbox.run("install.sh")
    sandbox.run("uninstall.sh")
    assert not (sandbox.data / "fluxmax").exists()
    assert not (sandbox.config / "systemd" / "user" / "fluxmax-daemon.service").exists()
    assert not (sandbox.data / "gnome-shell" / "extensions" / "focused-window@fluxmax").exists()
    assert not (sandbox.data / "applications" / "org.fluxmax.gui.desktop").exists()
    assert not (
        sandbox.data / "icons" / "hicolor" / "scalable" / "apps" / "org.fluxmax.gui.svg"
    ).exists()
    for name in ("fluxmax-daemon", "fluxmax-gui", "fluxmax-apply"):
        assert not (sandbox.bin / name).exists()


def test_restores_night_light_when_it_was_on(sandbox):
    sandbox.set_gsettings(*NIGHT_LIGHT.split(), "true")
    sandbox.run("install.sh")
    sandbox.set_gsettings(*NIGHT_LIGHT.split(), "false")   # as the daemon leaves it
    sandbox.run("uninstall.sh")
    assert sandbox.gsettings_value(*NIGHT_LIGHT.split()) == "true"


def test_restores_night_light_when_it_was_off(sandbox):
    """Both directions, deliberately: a test that only checks one passes
    against a script that hardcodes the answer."""
    sandbox.set_gsettings(*NIGHT_LIGHT.split(), "false")
    sandbox.run("install.sh")
    sandbox.set_gsettings(*NIGHT_LIGHT.split(), "true")
    sandbox.run("uninstall.sh")
    assert sandbox.gsettings_value(*NIGHT_LIGHT.split()) == "false"


def test_does_not_guess_night_light_without_a_state_file(sandbox):
    sandbox.run("install.sh")
    (sandbox.data / "fluxmax" / "install-state").unlink()
    sandbox.set_gsettings(*NIGHT_LIGHT.split(), "false")
    proc = sandbox.run("uninstall.sh")
    assert not any(c.startswith("gsettings set") for c in sandbox.stub_log())
    assert "night light" in (proc.stdout + proc.stderr).lower()
    assert sandbox.gsettings_value(*NIGHT_LIGHT.split()) == "false"


def test_stops_the_service_before_resetting_the_ramps(sandbox):
    """A running daemon would immediately overwrite the neutral ramps."""
    sandbox.run("install.sh")
    sandbox.stub_log_path.write_text("")          # only look at the uninstall
    sandbox.run("uninstall.sh")
    log = sandbox.stub_log()
    stop_at = next(i for i, c in enumerate(log) if c.startswith("systemctl") and "disable" in c)
    reset_at = next(i for i, c in enumerate(log) if c.startswith("fluxmax-apply"))
    assert stop_at < reset_at


def test_resets_the_ramps_to_neutral_while_the_venv_still_exists(sandbox):
    sandbox.run("install.sh")
    sandbox.stub_log_path.write_text("")
    sandbox.run("uninstall.sh")
    assert any("--temp 6500" in c for c in sandbox.called("fluxmax-apply"))


def test_disables_the_extension(sandbox):
    sandbox.run("install.sh")
    sandbox.stub_log_path.write_text("")
    sandbox.run("uninstall.sh")
    assert any("disable focused-window@fluxmax" in c for c in sandbox.called("gnome-extensions"))


def test_keeps_the_user_config_by_default(sandbox):
    sandbox.run("install.sh")
    cfg = sandbox.config / "fluxmax" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("mode = 'custom'\n")
    proc = sandbox.run("uninstall.sh")
    assert cfg.is_file()
    assert str(cfg) in proc.stdout


def test_purge_removes_the_user_config(sandbox):
    sandbox.run("install.sh")
    cfg = sandbox.config / "fluxmax" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("mode = 'custom'\n")
    sandbox.run("uninstall.sh", "--purge")
    assert not cfg.exists()


def test_uninstalling_a_never_installed_system_succeeds_and_changes_nothing(sandbox):
    """A partial or absent install is the main reason someone runs this."""
    proc = sandbox.run("uninstall.sh")
    assert proc.returncode == 0
    assert not any(c.startswith("gsettings set") for c in sandbox.stub_log())


def test_uninstall_is_idempotent(sandbox):
    sandbox.run("install.sh")
    sandbox.run("uninstall.sh")
    second = sandbox.run("uninstall.sh")
    assert second.returncode == 0


def test_leaves_a_foreign_file_in_local_bin_alone(sandbox):
    """We only ever created symlinks; anything else is not ours to delete."""
    sandbox.run("install.sh", "--no-start")
    (sandbox.bin / "fluxmax-daemon").unlink()
    (sandbox.bin / "fluxmax-daemon").write_text("#!/bin/sh\necho not ours\n")
    sandbox.run("uninstall.sh")
    assert (sandbox.bin / "fluxmax-daemon").is_file()


def test_closing_message_mentions_the_logout(sandbox):
    sandbox.run("install.sh")
    out = sandbox.run("uninstall.sh").stdout
    assert "log out" in out.lower()


def test_help_exits_zero_without_removing_anything(sandbox):
    sandbox.run("install.sh", "--no-start")
    proc = sandbox.run("uninstall.sh", "--help")
    assert proc.returncode == 0
    assert (sandbox.data / "fluxmax").exists()
