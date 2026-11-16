#!/usr/bin/env bash
# Duskcurve uninstaller. Reverses install.sh in the opposite order.
# Every step tolerates its target already being gone: a partial install is
# the main reason to run this.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$REPO_DIR/scripts/lib.sh"

PURGE=0

usage() {
    cat <<'EOF'
Usage: ./uninstall.sh [--purge]

  --purge     Also delete your configuration file. Without it the config is
              kept, so reinstalling picks up where you left off.
  -h, --help  Show this message.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --purge)   PURGE=1 ;;
        -h|--help) usage; exit 0 ;;
        *)         usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

# --- 1. stop the service -----------------------------------------------------
# First, so the daemon cannot overwrite the neutral ramps set in step 2.
if [ -f "$DUSKCURVE_UNIT_DIR/$UNIT_NAME" ]; then
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    rm -f "$DUSKCURVE_UNIT_DIR/$UNIT_NAME"
    systemctl --user daemon-reload 2>/dev/null || true
    log "Stopped and removed the daemon service"
fi

# --- 2. reset the ramps ------------------------------------------------------
# Before step 6 deletes the venv -- this is the only reason it is this early.
# Best-effort: displays may have changed since install.
if [ -x "$DUSKCURVE_VENV/bin/duskcurve-apply" ]; then
    if "$DUSKCURVE_VENV/bin/duskcurve-apply" --temp "$NEUTRAL_KELVIN" >/dev/null 2>&1; then
        log "Reset the displays to ${NEUTRAL_KELVIN}K"
    else
        warn "could not reset the gamma ramps; log out and back in to clear them"
    fi
fi

# --- 3. restore Night Light --------------------------------------------------
if previous="$(state_get night_light_was_enabled)"; then
    if gsettings set "$NIGHT_LIGHT_SCHEMA" "$NIGHT_LIGHT_KEY" "$previous"; then
        log "Restored Night Light to $previous"
    else
        warn "could not restore Night Light to $previous -- set it yourself in" \
             "Settings > Displays."
    fi
else
    warn "no record of your pre-install Night Light setting, so it has been left" \
         "as it is. Set it yourself in Settings > Displays if Duskcurve turned it off."
fi

# --- 4. extension ------------------------------------------------------------
ext_target="$DUSKCURVE_EXT_DIR/$EXT_UUID"
if [ -e "$ext_target" ] || [ -L "$ext_target" ]; then
    gnome-extensions disable "$EXT_UUID" 2>/dev/null || true
    rm -rf "$ext_target"
    log "Removed the GNOME Shell extension"
fi

# --- 5. desktop file, icon, console scripts ----------------------------------
rm -f "$DUSKCURVE_APP_DIR/$DESKTOP_FILE" "$DUSKCURVE_ICON_DIR/$ICON_NAME"
have gtk-update-icon-cache && gtk-update-icon-cache -qtf "$DUSKCURVE_ICON_THEME_DIR" || true
have update-desktop-database && update-desktop-database -q "$DUSKCURVE_APP_DIR" || true
for script in "${CONSOLE_SCRIPTS[@]}"; do
    # Only ever remove our own symlinks. Anything else is not ours.
    target="$DUSKCURVE_BIN_DIR/$script"
    if [ -L "$target" ]; then rm -f "$target"; fi
done

# --- 6. venv and state -------------------------------------------------------
rm -rf "$DUSKCURVE_HOME"

# --- 7. config ---------------------------------------------------------------
if [ "$PURGE" = "1" ]; then
    rm -f "$DUSKCURVE_CONFIG_FILE"
    rmdir "$(dirname "$DUSKCURVE_CONFIG_FILE")" 2>/dev/null || true
    log "Removed your configuration"
fi

cat <<EOF

Duskcurve has been removed.

  * LOG OUT and back in to unload the extension from the running shell.
EOF
if [ "$PURGE" != "1" ] && [ -f "$DUSKCURVE_CONFIG_FILE" ]; then
    cat <<EOF
  * Your configuration was kept at:
      $DUSKCURVE_CONFIG_FILE
    Re-run ./uninstall.sh --purge to remove it too.
EOF
fi
