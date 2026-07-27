import Gio from 'gi://Gio';

const DISPLAY_CONFIG_BUS_NAME = 'org.gnome.Mutter.DisplayConfig';
const DISPLAY_CONFIG_OBJECT_PATH = '/org/gnome/Mutter/DisplayConfig';
const DISPLAY_CONFIG_IFACE = 'org.gnome.Mutter.DisplayConfig';

/**
 * Maps Meta.Window monitor indices to their connector names (e.g. "eDP-1",
 * "DP-2"). Mutter only exposes the reverse direction (connector -> index)
 * via Meta's monitor manager, and Meta.Monitor/Meta.Output are not
 * introspectable from GJS, so this inverts the mapping by asking
 * org.gnome.Mutter.DisplayConfig for the full list of connectors and
 * testing each one against get_monitor_for_connector().
 */
export class MonitorMap {
    constructor() {
        this._byIndex = new Map();
    }

    refresh() {
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
                    console.warn(`focused-window@fluxmax: GetCurrentState failed: ${e}`);
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
                        // First connector seen for an index wins (mirrored
                        // outputs share an index).
                        if (!byIndex.has(index))
                            byIndex.set(index, connector);
                    }

                    this._byIndex = byIndex;
                } catch (e) {
                    console.warn(`focused-window@fluxmax: failed to parse monitor state: ${e}`);
                }
            });
    }

    connectorFor(index) {
        return this._byIndex.get(index) ?? '';
    }
}
