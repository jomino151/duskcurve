"""install.sh: preflight, cairo probe, venv, console scripts."""

NIGHT_LIGHT = "org.gnome.settings-daemon.plugins.color night-light-enabled"


def test_creates_the_venv_with_system_site_packages(sandbox):
    """--system-site-packages is the whole reason this venv works: `gi` is a
    system package and is not meaningfully pip-installable."""
    sandbox.run("install.sh", "--no-start")
    venv_calls = [c for c in sandbox.called("python3") if "-m venv" in c]
    assert len(venv_calls) == 1
    assert "--system-site-packages" in venv_calls[0]
    assert str(sandbox.data / "duskcurve" / "venv") in venv_calls[0]


def test_installs_the_package_non_editable_by_default(sandbox):
    sandbox.run("install.sh", "--no-start")
    pip = sandbox.called("pip")
    assert any("install" in c for c in pip)
    assert not any(" -e" in c for c in pip)


def test_dev_flag_installs_editable(sandbox):
    sandbox.run("install.sh", "--dev", "--no-start")
    assert any("-e" in c for c in sandbox.called("pip"))


def test_records_the_install_mode(sandbox):
    sandbox.run("install.sh", "--no-start")
    assert "install_mode=copy" in (sandbox.data / "duskcurve" / "install-state").read_text()
    sandbox.run("install.sh", "--dev", "--no-start")
    assert "install_mode=dev" in (sandbox.data / "duskcurve" / "install-state").read_text()


def test_symlinks_all_three_console_scripts(sandbox):
    sandbox.run("install.sh", "--no-start")
    venv_bin = sandbox.data / "duskcurve" / "venv" / "bin"
    for name in ("duskcurve-daemon", "duskcurve-gui", "duskcurve-apply"):
        link = sandbox.bin / name
        assert link.is_symlink()
        assert link.resolve() == (venv_bin / name).resolve()


def test_rerunning_replaces_the_symlinks_without_error(sandbox):
    """Re-running the installer is the supported upgrade path."""
    sandbox.run("install.sh", "--no-start")
    sandbox.run("install.sh", "--no-start")
    assert (sandbox.bin / "duskcurve-gui").is_symlink()


def test_refuses_to_clobber_a_real_file_in_local_bin(sandbox):
    """Someone else's duskcurve-gui is not ours to delete."""
    sandbox.bin.mkdir(parents=True, exist_ok=True)
    (sandbox.bin / "duskcurve-gui").write_text("#!/bin/sh\necho not ours\n")
    proc = sandbox.run("install.sh", "--no-start", expect_ok=False)
    assert proc.returncode != 0
    assert (sandbox.bin / "duskcurve-gui").read_text() == "#!/bin/sh\necho not ours\n"


def test_a_refused_clobber_leaves_no_partial_symlink_set(sandbox):
    """The collision scan runs before any linking, so an aborted install creates nothing.

    duskcurve-daemon sorts before duskcurve-gui, so a link-as-you-go loop would
    have created it first.
    """
    sandbox.bin.mkdir(parents=True, exist_ok=True)
    (sandbox.bin / "duskcurve-gui").write_text("#!/bin/sh\necho not ours\n")
    proc = sandbox.run("install.sh", "--no-start", expect_ok=False)
    assert proc.returncode != 0
    assert not (sandbox.bin / "duskcurve-daemon").exists()
    assert not (sandbox.bin / "duskcurve-apply").exists()


def test_diagnostic_die_names_the_remedy_for_an_old_pip_install(sandbox):
    """A collision with an old `pip install --user` copy names the remedy."""
    sandbox.bin.mkdir(parents=True, exist_ok=True)
    (sandbox.bin / "duskcurve-gui").write_text("#!/bin/sh\necho not ours\n")
    sandbox.env["STUB_PIP_SHOW_OK"] = "0"
    proc = sandbox.run("install.sh", "--no-start", expect_ok=False)
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "pip uninstall" in combined
    assert "left by an older" in combined


def test_refused_clobber_creates_no_venv_and_wastes_no_pip_install(sandbox):
    """The console-script pre-scan runs before the venv is created, so a refusal costs nothing."""
    sandbox.bin.mkdir(parents=True, exist_ok=True)
    (sandbox.bin / "duskcurve-gui").write_text("#!/bin/sh\necho not ours\n")
    proc = sandbox.run("install.sh", "--no-start", expect_ok=False)
    assert proc.returncode != 0
    assert not (sandbox.data / "duskcurve" / "venv").exists()
    assert not sandbox.called("pip")


def test_fails_fast_when_the_system_python_has_no_gi(sandbox):
    """Without gi nothing works, and the failure would otherwise surface much
    later as an import error inside the daemon under systemd."""
    sandbox.env["STUB_GI_OK"] = "0"
    proc = sandbox.run("install.sh", "--no-start", expect_ok=False)
    assert proc.returncode != 0
    assert "gi" in (proc.stdout + proc.stderr)
    assert not (sandbox.data / "duskcurve" / "venv").exists()


def test_offers_apt_when_the_cairo_bridge_is_missing_and_installs_on_yes(sandbox):
    sandbox.env["STUB_CAIRO_OK"] = "0"
    sandbox.run("install.sh", "--no-start", stdin="y\n")
    sudo = sandbox.called("sudo")
    assert any("apt" in c and "python3-gi-cairo" in c for c in sudo)


def test_declining_apt_is_not_fatal(sandbox):
    """The curve editor degrades to an actionable status page without it; the
    rest of the app is fine, so refusing must not abort the install."""
    sandbox.env["STUB_CAIRO_OK"] = "0"
    proc = sandbox.run("install.sh", "--no-start", stdin="n\n")
    assert proc.returncode == 0
    assert not sandbox.called("sudo")
    assert "python3-gi-cairo" in proc.stdout
    assert "Fix later with" in proc.stderr
    assert (sandbox.data / "duskcurve" / "venv").exists()


def test_does_not_touch_apt_when_the_cairo_bridge_is_present(sandbox):
    sandbox.run("install.sh", "--no-start")
    assert not sandbox.called("sudo")
    assert not sandbox.called("apt")


def test_warns_about_a_shadowing_pip_user_install(sandbox):
    """Warn about an old `pip install --user` copy that shadows this install."""
    sandbox.env["STUB_PIP_SHOW_OK"] = "0"
    proc = sandbox.run("install.sh", "--no-start")
    assert "break-system-packages" in (proc.stdout + proc.stderr)


def test_help_exits_zero_without_installing_anything(sandbox):
    proc = sandbox.run("install.sh", "--help")
    assert proc.returncode == 0
    assert not (sandbox.data / "duskcurve").exists()


def test_rejects_an_unknown_flag(sandbox):
    proc = sandbox.run("install.sh", "--wat", expect_ok=False)
    assert proc.returncode != 0


def test_records_night_light_before_starting_anything(sandbox):
    """The daemon turns Night Light off as its first act, so a value recorded
    after `systemctl enable --now` would record Duskcurve's own handiwork."""
    sandbox.set_gsettings(*NIGHT_LIGHT.split(), "true")
    sandbox.run("install.sh")
    state = (sandbox.data / "duskcurve" / "install-state").read_text()
    assert "night_light_was_enabled=true" in state

    log = sandbox.stub_log()
    first_get = next(i for i, c in enumerate(log) if c.startswith("gsettings get"))
    first_start = next(i for i, c in enumerate(log) if "enable" in c and c.startswith("systemctl"))
    assert first_get < first_start


def test_records_night_light_false_when_it_was_off(sandbox):
    sandbox.set_gsettings(*NIGHT_LIGHT.split(), "false")
    sandbox.run("install.sh", "--no-start")
    assert (
        "night_light_was_enabled=false"
        in (sandbox.data / "duskcurve" / "install-state").read_text()
    )


def test_reinstall_does_not_overwrite_the_recorded_night_light_value(sandbox):
    """By the second run the daemon has already set it to false. Re-recording
    would destroy the only copy of the user's original setting."""
    sandbox.set_gsettings(*NIGHT_LIGHT.split(), "true")
    sandbox.run("install.sh", "--no-start")
    sandbox.set_gsettings(*NIGHT_LIGHT.split(), "false")  # as the daemon would leave it
    sandbox.run("install.sh", "--no-start")
    assert (
        "night_light_was_enabled=true" in (sandbox.data / "duskcurve" / "install-state").read_text()
    )


def test_a_failed_night_light_read_records_nothing(sandbox):
    """A failed read is not recorded as 'off'; the uninstaller would turn Night Light off."""
    sandbox.env["STUB_GSETTINGS_GET_FAIL"] = "1"
    sandbox.run("install.sh", "--no-start")
    state = (sandbox.data / "duskcurve" / "install-state").read_text()
    assert "night_light_was_enabled" not in state

    sandbox.env["STUB_GSETTINGS_GET_FAIL"] = "0"
    sandbox.run("uninstall.sh")
    assert not any(c.startswith("gsettings set") for c in sandbox.stub_log())


def test_installs_the_icon_and_desktop_file(sandbox):
    sandbox.run("install.sh", "--no-start")
    icon = sandbox.data / "icons" / "hicolor" / "scalable" / "apps" / "org.duskcurve.gui.svg"
    desktop = sandbox.data / "applications" / "org.duskcurve.gui.desktop"
    assert icon.is_file()
    assert desktop.is_file()
    assert "Exec=duskcurve-gui" in desktop.read_text()


def test_copies_the_extension_by_default(sandbox):
    sandbox.run("install.sh", "--no-start")
    ext = sandbox.data / "gnome-shell" / "extensions" / "focused-window@duskcurve"
    assert ext.is_dir()
    assert not ext.is_symlink()
    assert (ext / "metadata.json").is_file()


def test_dev_symlinks_the_extension(sandbox):
    sandbox.run("install.sh", "--dev", "--no-start")
    ext = sandbox.data / "gnome-shell" / "extensions" / "focused-window@duskcurve"
    assert ext.is_symlink()


def test_reinstall_switching_from_copy_to_dev_replaces_the_extension(sandbox):
    """Leaving a copied directory in place would silently win over the symlink
    the user just asked for."""
    sandbox.run("install.sh", "--no-start")
    sandbox.run("install.sh", "--dev", "--no-start")
    ext = sandbox.data / "gnome-shell" / "extensions" / "focused-window@duskcurve"
    assert ext.is_symlink()


def test_enables_the_extension(sandbox):
    sandbox.run("install.sh", "--no-start")
    assert any("enable focused-window@duskcurve" in c for c in sandbox.called("gnome-extensions"))


def test_installs_reloads_enables_and_starts_the_unit(sandbox):
    sandbox.run("install.sh")
    assert (sandbox.config / "systemd" / "user" / "duskcurve-daemon.service").is_file()
    calls = sandbox.called("systemctl")
    assert any("daemon-reload" in c for c in calls)
    assert any("enable --now duskcurve-daemon.service" in c for c in calls)
    reload_at = next(i for i, c in enumerate(calls) if "daemon-reload" in c)
    enable_at = next(i for i, c in enumerate(calls) if "enable" in c)
    assert reload_at < enable_at


def test_no_start_installs_the_unit_but_does_not_enable_it(sandbox):
    sandbox.run("install.sh", "--no-start")
    assert (sandbox.config / "systemd" / "user" / "duskcurve-daemon.service").is_file()
    assert not any("enable" in c for c in sandbox.called("systemctl"))


def test_closing_message_prints_even_when_systemctl_fails(sandbox):
    """The log-out message still prints when the daemon cannot start (no session bus)."""
    sandbox.env["STUB_SYSTEMCTL_FAIL"] = "1"
    proc = sandbox.run("install.sh")
    assert proc.returncode == 0
    assert "log out" in proc.stdout.lower()


def test_closing_message_names_the_logout_the_config_and_the_rules_caveat(sandbox):
    """Each of these is a documented way a correct first install looks broken."""
    out = sandbox.run("install.sh").stdout
    assert "log out" in out.lower()
    assert "config.toml" in out
    assert "rules" in out.lower()


def test_a_full_install_is_idempotent(sandbox):
    first = sandbox.run("install.sh")
    second = sandbox.run("install.sh")
    assert first.returncode == 0 and second.returncode == 0
    ext = sandbox.data / "gnome-shell" / "extensions" / "focused-window@duskcurve"
    assert (ext / "metadata.json").is_file()
    assert (sandbox.config / "systemd" / "user" / "duskcurve-daemon.service").is_file()


def test_same_mode_reinstall_does_not_tear_down_the_extension_directory(sandbox):
    """A re-run must not recreate the extension directory under a running GNOME Shell.

    That would move the extension to the disabled list; a stable inode shows the
    directory was left in place.
    """
    ext = sandbox.data / "gnome-shell" / "extensions" / "focused-window@duskcurve"
    sandbox.run("install.sh", "--no-start")
    before = ext.stat().st_ino
    sandbox.run("install.sh", "--no-start")
    assert ext.is_dir()
    assert not ext.is_symlink()
    assert ext.stat().st_ino == before
    assert (ext / "metadata.json").is_file()


def test_switching_from_dev_back_to_copy_replaces_the_symlink(sandbox):
    """The other direction of the mode switch: a leftover symlink must not
    survive as the answer to a copying install."""
    sandbox.run("install.sh", "--dev", "--no-start")
    ext = sandbox.data / "gnome-shell" / "extensions" / "focused-window@duskcurve"
    assert ext.is_symlink()
    sandbox.run("install.sh", "--no-start")
    assert ext.is_dir()
    assert not ext.is_symlink()
    assert (ext / "metadata.json").is_file()


def test_reinstall_restarts_a_daemon_that_was_already_running(sandbox):
    """`enable --now` never restarts a running unit, so an upgrade needs an explicit restart."""
    sandbox.run("install.sh")
    sandbox.stub_log_path.write_text("")
    sandbox.run("install.sh")
    assert any("restart duskcurve-daemon.service" in c for c in sandbox.called("systemctl"))


def test_fresh_install_does_not_restart_the_daemon_it_just_started(sandbox):
    """`enable --now` already started it; restarting immediately would reset
    the ramps for nothing on every first install."""
    sandbox.env["STUB_SYSTEMCTL_INACTIVE"] = "1"
    sandbox.run("install.sh")
    assert not any("restart duskcurve-daemon.service" in c for c in sandbox.called("systemctl"))
