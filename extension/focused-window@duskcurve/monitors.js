import Gio from 'gi://Gio';

const DISPLAY_CONFIG_BUS_NAME = 'org.gnome.Mutter.DisplayConfig';
const DISPLAY_CONFIG_OBJECT_PATH = '/org/gnome/Mutter/DisplayConfig';
const DISPLAY_CONFIG_IFACE = 'org.gnome.Mutter.DisplayConfig';

/**
 * Maps Meta.Window monitor indices to connector names (e.g. "eDP-1").
 * Meta.Monitor is not introspectable from GJS, so the mapping is inverted by
 * asking org.gnome.Mutter.DisplayConfig for the connectors and testing each.
 */
export class MonitorMap {
    constructor() {
        this._byIndex = new Map();
    }

    // Refreshes the index -> connector map asynchronously; onDone runs on success
    // and on failure, so callers can publish with whatever map is in place. Never
    // throws: a failure here must not unwind into enable().
    refresh(onDone) {
        try {
            Gio.DBus.session.call(
                DISPLAY_CONFIG_BUS_NAME,
                DISPLAY_CONFIG_OBJECT_PATH,
                DISPLAY_CONFIG_IFACE,
                'GetCurrentState',
                null,
                null,
                Gio.DBusCallFlags.NONE,
                -1,
                null,
                (conn, res) => {
                    let result;
                    try {
                        result = Gio.DBus.session.call_finish(res);
                    } catch (e) {
                        console.warn(`focused-window@duskcurve: GetCurrentState failed: ${e}`);
                        onDone?.();
                        return;
                    }

                    try {
                        const monitors = result.deepUnpack()[1];
                        const monitorManager = global.backend.get_monitor_manager();
                        const byIndex = new Map();

                        for (const monitor of monitors) {
                            const connector = monitor[0][0];
                            const index = monitorManager.get_monitor_for_connector(connector);
                            if (index < 0)
                                continue;
                            // First connector seen for an index wins
                            // (mirrored outputs share an index).
                            if (!byIndex.has(index))
                                byIndex.set(index, connector);
                        }

                        this._byIndex = byIndex;
                    } catch (e) {
                        console.warn(`focused-window@duskcurve: failed to parse monitor state: ${e}`);
                    }

                    onDone?.();
                });
        } catch (e) {
            console.warn(`focused-window@duskcurve: GetCurrentState dispatch failed: ${e}`);
            onDone?.();
        }
    }

    connectorFor(index) {
        return this._byIndex.get(index) ?? '';
    }
}
