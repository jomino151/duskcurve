"""install.sh: preflight, cairo probe, venv, console scripts."""

NIGHT_LIGHT = "org.gnome.settings-daemon.plugins.color night-light-enabled"


def test_creates_the_venv_with_system_site_packages(sandbox):
    """--system-site-packages is the whole reason this venv works: `gi` is a
    system package and is not meaningfully pip-installable."""
    sandbox.run("install.sh", "--no-start")
    venv_calls = [c for c in sandbox.called("python3") if "-m venv" in c]
    assert len(venv_calls) == 1
    assert "--system-site-packages" in venv_calls[0]
    assert str(sandbox.data / "fluxmax" / "venv") in venv_calls[0]


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
    assert "install_mode=copy" in (sandbox.data / "fluxmax" / "install-state").read_text()
    sandbox.run("install.sh", "--dev", "--no-start")
    assert "install_mode=dev" in (sandbox.data / "fluxmax" / "install-state").read_text()


def test_symlinks_all_three_console_scripts(sandbox):
    sandbox.run("install.sh", "--no-start")
    venv_bin = sandbox.data / "fluxmax" / "venv" / "bin"
    for name in ("fluxmax-daemon", "fluxmax-gui", "fluxmax-apply"):
        link = sandbox.bin / name
        assert link.is_symlink()
        assert link.resolve() == (venv_bin / name).resolve()


def test_rerunning_replaces_the_symlinks_without_error(sandbox):
    """Re-running the installer is the supported upgrade path."""
    sandbox.run("install.sh", "--no-start")
    sandbox.run("install.sh", "--no-start")
    assert (sandbox.bin / "fluxmax-gui").is_symlink()


def test_refuses_to_clobber_a_real_file_in_local_bin(sandbox):
    """Someone else's fluxmax-gui is not ours to delete."""
    sandbox.bin.mkdir(parents=True, exist_ok=True)
    (sandbox.bin / "fluxmax-gui").write_text("#!/bin/sh\necho not ours\n")
    proc = sandbox.run("install.sh", "--no-start", expect_ok=False)
    assert proc.returncode != 0
    assert (sandbox.bin / "fluxmax-gui").read_text() == "#!/bin/sh\necho not ours\n"


def test_fails_fast_when_the_system_python_has_no_gi(sandbox):
    """Without gi nothing works, and the failure would otherwise surface much
    later as an import error inside the daemon under systemd."""
    sandbox.env["STUB_GI_OK"] = "0"
    proc = sandbox.run("install.sh", "--no-start", expect_ok=False)
    assert proc.returncode != 0
    assert "gi" in (proc.stdout + proc.stderr)
    assert not (sandbox.data / "fluxmax" / "venv").exists()


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
    assert (sandbox.data / "fluxmax" / "venv").exists()


def test_does_not_touch_apt_when_the_cairo_bridge_is_present(sandbox):
    sandbox.run("install.sh", "--no-start")
    assert not sandbox.called("sudo")
    assert not sandbox.called("apt")


def test_warns_about_a_shadowing_pip_user_install(sandbox):
    """Two flux-max installs on PATH is a debugging trap; this machine has an
    old `pip install --user --break-system-packages` copy from M5/M6."""
    sandbox.env["STUB_PIP_SHOW_OK"] = "0"
    proc = sandbox.run("install.sh", "--no-start")
    assert "break-system-packages" in (proc.stdout + proc.stderr)


def test_help_exits_zero_without_installing_anything(sandbox):
    proc = sandbox.run("install.sh", "--help")
    assert proc.returncode == 0
    assert not (sandbox.data / "fluxmax").exists()


def test_rejects_an_unknown_flag(sandbox):
    proc = sandbox.run("install.sh", "--wat", expect_ok=False)
    assert proc.returncode != 0
