#!/usr/bin/env bash
# Shared paths and helpers for install.sh and uninstall.sh.
# Sourced, never executed: sets no shell options and holds no install policy.

EXT_UUID="focused-window@duskcurve"
CONSOLE_SCRIPTS=(duskcurve-daemon duskcurve-gui duskcurve-apply)
UNIT_NAME="duskcurve-daemon.service"
DESKTOP_FILE="org.duskcurve.gui.desktop"
ICON_NAME="org.duskcurve.gui.svg"
NEUTRAL_KELVIN=6500

# Duplicated from duskcurve/backend/nightlight.py because bash cannot import it;
# tests/packaging/test_lib.py asserts they agree.
NIGHT_LIGHT_SCHEMA="org.gnome.settings-daemon.plugins.color"
NIGHT_LIGHT_KEY="night-light-enabled"

DUSKCURVE_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
DUSKCURVE_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
DUSKCURVE_HOME="$DUSKCURVE_DATA_HOME/duskcurve"
DUSKCURVE_VENV="$DUSKCURVE_HOME/venv"
DUSKCURVE_STATE_FILE="$DUSKCURVE_HOME/install-state"
DUSKCURVE_BIN_DIR="$HOME/.local/bin"
DUSKCURVE_UNIT_DIR="$DUSKCURVE_CONFIG_HOME/systemd/user"
DUSKCURVE_EXT_DIR="$DUSKCURVE_DATA_HOME/gnome-shell/extensions"
DUSKCURVE_APP_DIR="$DUSKCURVE_DATA_HOME/applications"
DUSKCURVE_ICON_THEME_DIR="$DUSKCURVE_DATA_HOME/icons/hicolor"
DUSKCURVE_ICON_DIR="$DUSKCURVE_ICON_THEME_DIR/scalable/apps"
DUSKCURVE_CONFIG_FILE="$DUSKCURVE_CONFIG_HOME/duskcurve/config.toml"

DUSKCURVE_PYTHON="${DUSKCURVE_PYTHON:-python3}"

log()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

state_set() {
    local key="$1" value="$2"
    mkdir -p "$(dirname "$DUSKCURVE_STATE_FILE")"
    if [ -f "$DUSKCURVE_STATE_FILE" ]; then
        # Upsert: drop any existing line for the key so a re-install leaves one value.
        local tmp
        tmp="$(mktemp)"
        grep -v "^${key}=" "$DUSKCURVE_STATE_FILE" > "$tmp" || true
        mv "$tmp" "$DUSKCURVE_STATE_FILE"
    else
        printf '# Duskcurve install state -- written by install.sh, read by uninstall.sh\n' \
            > "$DUSKCURVE_STATE_FILE"
    fi
    printf '%s=%s\n' "$key" "$value" >> "$DUSKCURVE_STATE_FILE"
}

state_get() {
    local key="$1" line
    [ -f "$DUSKCURVE_STATE_FILE" ] || return 1
    line="$(grep -m1 "^${key}=" "$DUSKCURVE_STATE_FILE")" || return 1
    printf '%s\n' "${line#*=}"
}
