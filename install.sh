#!/usr/bin/env bash
# Duskcurve installer. Rootless except for one prompted `sudo apt` step.
# Idempotent: re-running is the supported upgrade path.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$REPO_DIR/scripts/lib.sh"

INSTALL_MODE="copy"
START_SERVICE=1

usage() {
    cat <<'EOF'
Usage: ./install.sh [--dev] [--no-start]

  --dev       Editable install (pip -e) and symlink the extension, for
              working on Duskcurve itself. The default copies instead, so
              the install survives the repo being moved or deleted.
  --no-start  Install everything but do not enable or start the daemon.
  -h, --help  Show this message.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dev)      INSTALL_MODE="dev" ;;
        --no-start) START_SERVICE=0 ;;
        -h|--help)  usage; exit 0 ;;
        *)          usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

# --- 1. preflight ------------------------------------------------------------
log "Checking prerequisites"
"$DUSKCURVE_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || die "python 3.12 or newer is required"
"$DUSKCURVE_PYTHON" -c 'import gi' 2>/dev/null \
    || die "the system PyGObject (python3-gi) is missing: sudo apt install python3-gi"

case ":$PATH:" in
    *":$DUSKCURVE_BIN_DIR:"*) ;;
    *) warn "$DUSKCURVE_BIN_DIR is not on your PATH -- add it to ~/.profile, or the" \
            "duskcurve-gui command and the daemon unit will not resolve." ;;
esac

old_pip_install=0
if "$DUSKCURVE_PYTHON" -m pip show duskcurve >/dev/null 2>&1; then
    old_pip_install=1
    warn "an older 'pip install --user' copy of Duskcurve is installed and will shadow" \
         "this one. Remove it with: $DUSKCURVE_PYTHON -m pip uninstall duskcurve --break-system-packages"
fi

# Check console-script targets before creating the (slow) venv, so we refuse early.
mkdir -p "$DUSKCURVE_BIN_DIR"
for script in "${CONSOLE_SCRIPTS[@]}"; do
    target="$DUSKCURVE_BIN_DIR/$script"
    if [ -e "$target" ] && [ ! -L "$target" ]; then
        if [ "$old_pip_install" = "1" ]; then
            die "$target is a regular file left by an older 'pip install --user' copy of" \
                "Duskcurve. Remove it first:" \
                "  $DUSKCURVE_PYTHON -m pip uninstall duskcurve --break-system-packages" \
                "then re-run ./install.sh."
        fi
        die "$target exists and is not a symlink -- refusing to overwrite it." \
            "Move it aside and re-run ./install.sh."
    fi
done

# --- 2. the cairo bridge -----------------------------------------------------
# python3-gi-cairo is a separate package that python3-gi does not pull in; without
# it the curve editor degrades to a status page.
if ! "$DUSKCURVE_PYTHON" -c 'import gi; gi.require_foreign("cairo")' 2>/dev/null; then
    log "The curve editor needs the python3-gi-cairo system package, which is missing."
    printf '    Install it now with sudo? [y/N] '
    read -r reply || reply=""
    case "$reply" in
        [yY]*) sudo apt install -y python3-gi-cairo || warn "apt failed; continuing without it" ;;
        *)     warn "continuing without python3-gi-cairo -- the curve editor will show an" \
                    "install hint instead of the curve. Fix later with:" \
                    "sudo apt install python3-gi-cairo" ;;
    esac
fi

# --- 3. venv + package -------------------------------------------------------
# A distro upgrade swaps the system python under the venv (its bin/python3 is a
# symlink), leaving site-packages under the old lib/pythonX.Y -- rebuild it.
_py_ver="$("$DUSKCURVE_PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [ -d "$DUSKCURVE_VENV" ] && [ ! -d "$DUSKCURVE_VENV/lib/python$_py_ver" ]; then
    log "Rebuilding the virtualenv for python $_py_ver"
    rm -rf "$DUSKCURVE_VENV"
fi
if [ ! -x "$DUSKCURVE_VENV/bin/pip" ]; then
    "$DUSKCURVE_PYTHON" -c 'import ensurepip' 2>/dev/null \
        || die "python venv support is missing: sudo apt install python3-venv"
    log "Creating the virtualenv at $DUSKCURVE_VENV"
    mkdir -p "$DUSKCURVE_HOME"
    # --system-site-packages so the venv inherits the system gi and the cairo
    # bridge; neither is meaningfully pip-installable.
    "$DUSKCURVE_PYTHON" -m venv --system-site-packages "$DUSKCURVE_VENV"
fi

log "Installing Duskcurve ($INSTALL_MODE)"
if [ "$INSTALL_MODE" = "dev" ]; then
    "$DUSKCURVE_VENV/bin/pip" install --upgrade -e "$REPO_DIR"
else
    "$DUSKCURVE_VENV/bin/pip" install --upgrade "$REPO_DIR"
fi

# --- 4. console scripts ------------------------------------------------------
# Collisions were checked in preflight; only the linking remains.
mkdir -p "$DUSKCURVE_BIN_DIR"
for script in "${CONSOLE_SCRIPTS[@]}"; do
    ln -sfn "$DUSKCURVE_VENV/bin/$script" "$DUSKCURVE_BIN_DIR/$script"
done
log "Linked ${CONSOLE_SCRIPTS[*]} into $DUSKCURVE_BIN_DIR"

# Diagnostic only: nothing reads this back.
state_set install_mode "$INSTALL_MODE"

# --- 5. record Night Light ---------------------------------------------------
# Must run before the daemon starts: it turns Night Light off first thing, so a
# later read would record its own change. A re-install keeps the existing value.
if ! state_get night_light_was_enabled >/dev/null; then
    if current="$(gsettings get "$NIGHT_LIGHT_SCHEMA" "$NIGHT_LIGHT_KEY" 2>/dev/null)"; then
        state_set night_light_was_enabled "$current"
        log "Recorded Night Light = $current for uninstall"
    else
        warn "could not read your Night Light setting; the uninstaller will leave it" \
             "alone rather than guess."
    fi
fi

# --- 6. icon + desktop file --------------------------------------------------
mkdir -p "$DUSKCURVE_ICON_DIR" "$DUSKCURVE_APP_DIR"
install -m 0644 "$REPO_DIR/data/icons/$ICON_NAME" "$DUSKCURVE_ICON_DIR/$ICON_NAME"
install -m 0644 "$REPO_DIR/data/$DESKTOP_FILE" "$DUSKCURVE_APP_DIR/$DESKTOP_FILE"
# Both refreshers are optional: a stale cache costs an icon, not an install.
have gtk-update-icon-cache && gtk-update-icon-cache -qtf "$DUSKCURVE_ICON_THEME_DIR" || true
have update-desktop-database && update-desktop-database -q "$DUSKCURVE_APP_DIR" || true
log "Installed the icon and desktop entry"

# --- 7. extension ------------------------------------------------------------
mkdir -p "$DUSKCURVE_EXT_DIR"
ext_target="$DUSKCURVE_EXT_DIR/$EXT_UUID"
# Replace the target only when its kind is wrong (symlink vs copied directory).
# Removing a correct one would pull it from under a running Shell and disable the
# extension; checking the disk also self-heals a missing state file.
if [ "$INSTALL_MODE" = "dev" ]; then
    if [ -e "$ext_target" ] && [ ! -L "$ext_target" ]; then
        rm -rf "$ext_target"
    fi
    ln -sfn "$REPO_DIR/extension/$EXT_UUID" "$ext_target"
else
    if [ -L "$ext_target" ]; then
        rm -f "$ext_target"
    fi
    mkdir -p "$ext_target"
    # Copy the contents, not the directory: the directory itself must survive.
    cp -r "$REPO_DIR/extension/$EXT_UUID/." "$ext_target/"
fi
gnome-extensions enable "$EXT_UUID" || warn "could not enable the extension yet -- run" \
    "'gnome-extensions enable $EXT_UUID' after logging back in"
log "Installed the GNOME Shell extension"

# --- 8. systemd unit ---------------------------------------------------------
mkdir -p "$DUSKCURVE_UNIT_DIR"
install -m 0644 "$REPO_DIR/data/$UNIT_NAME" "$DUSKCURVE_UNIT_DIR/$UNIT_NAME"
systemctl --user daemon-reload || warn "systemctl daemon-reload failed; the unit may not" \
    "be picked up until you log out and back in."
# Captured before `enable --now`, which would make a stopped unit look active.
daemon_was_active=0
if systemctl --user is-active --quiet "$UNIT_NAME" 2>/dev/null; then
    daemon_was_active=1
fi
if [ "$START_SERVICE" = "1" ]; then
    if systemctl --user enable --now "$UNIT_NAME"; then
        # `enable --now` never restarts a running unit, so restart on upgrade, but only if
        # it was already up: a first install should not reset the ramps.
        if [ "$daemon_was_active" = "1" ]; then
            systemctl --user restart "$UNIT_NAME" \
                || warn "could not restart the daemon, so it is still running the previous" \
                        "version. Restart it with 'systemctl --user restart $UNIT_NAME'."
        fi
        log "Daemon enabled and started"
    else
        warn "the daemon did not start; check 'systemctl --user status $UNIT_NAME'"
    fi
else
    log "Unit installed but not started (--no-start)"
fi

# --- 9. next steps -----------------------------------------------------------
cat <<EOF

Duskcurve is installed.

  1. LOG OUT and back in. On Wayland, GNOME Shell cannot be restarted in
     place ('Alt+F2 r' does not work), so the extension will not load until
     you do. Until then window rules are inert by design -- the daemon still
     runs the schedule on every display.
  2. Set your location and wake/bedtime in:
       $DUSKCURVE_CONFIG_FILE
     or launch the GUI with: duskcurve-gui
  3. Check on the daemon any time with:
       systemctl --user status $UNIT_NAME

To remove everything again: ./uninstall.sh
EOF
