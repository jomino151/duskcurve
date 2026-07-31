#!/usr/bin/env bash
# Shared facts and mechanics for install.sh and uninstall.sh.
#
# Sourced, never executed: it deliberately sets no shell options of its own,
# and contains no install/uninstall policy -- only the paths both entry
# points must agree on, plus small helpers.

EXT_UUID="focused-window@fluxmax"
CONSOLE_SCRIPTS=(fluxmax-daemon fluxmax-gui fluxmax-apply)
UNIT_NAME="fluxmax-daemon.service"
DESKTOP_FILE="org.fluxmax.gui.desktop"
ICON_NAME="org.fluxmax.gui.svg"
NEUTRAL_KELVIN=6500

# Duplicated from fluxmax/backend/nightlight.py:7-8 because bash cannot import
# it. tests/packaging/test_lib.py asserts these two still agree with that file.
NIGHT_LIGHT_SCHEMA="org.gnome.settings-daemon.plugins.color"
NIGHT_LIGHT_KEY="night-light-enabled"

FLUXMAX_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
FLUXMAX_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
FLUXMAX_HOME="$FLUXMAX_DATA_HOME/fluxmax"
FLUXMAX_VENV="$FLUXMAX_HOME/venv"
FLUXMAX_STATE_FILE="$FLUXMAX_HOME/install-state"
FLUXMAX_BIN_DIR="$HOME/.local/bin"
FLUXMAX_UNIT_DIR="$FLUXMAX_CONFIG_HOME/systemd/user"
FLUXMAX_EXT_DIR="$FLUXMAX_DATA_HOME/gnome-shell/extensions"
FLUXMAX_APP_DIR="$FLUXMAX_DATA_HOME/applications"
FLUXMAX_ICON_DIR="$FLUXMAX_DATA_HOME/icons/hicolor/scalable/apps"
FLUXMAX_CONFIG_FILE="$FLUXMAX_CONFIG_HOME/fluxmax/config.toml"

FLUXMAX_PYTHON="${FLUXMAX_PYTHON:-python3}"

log()  { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

state_set() {
    local key="$1" value="$2"
    mkdir -p "$(dirname "$FLUXMAX_STATE_FILE")"
    if [ -f "$FLUXMAX_STATE_FILE" ]; then
        # Upsert: drop any existing line for this key before appending, so a
        # re-install cannot leave two values for one key.
        local tmp
        tmp="$(mktemp)"
        grep -v "^${key}=" "$FLUXMAX_STATE_FILE" > "$tmp" || true
        mv "$tmp" "$FLUXMAX_STATE_FILE"
    else
        printf '# flux-max install state -- written by install.sh, read by uninstall.sh\n' \
            > "$FLUXMAX_STATE_FILE"
    fi
    printf '%s=%s\n' "$key" "$value" >> "$FLUXMAX_STATE_FILE"
}

state_get() {
    local key="$1" line
    [ -f "$FLUXMAX_STATE_FILE" ] || return 1
    line="$(grep -m1 "^${key}=" "$FLUXMAX_STATE_FILE")" || return 1
    printf '%s\n' "${line#*=}"
}
