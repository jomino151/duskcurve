import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const IFACE_XML = `
<node>
  <interface name="org.fluxmax.WindowWatcher">
    <signal name="FocusChanged">
      <arg type="s" name="app_id"/>
      <arg type="b" name="fullscreen"/>
      <arg type="s" name="monitor_connector"/>
    </signal>
    <method name="GetState">
      <arg type="s" name="app_id" direction="out"/>
      <arg type="b" name="fullscreen" direction="out"/>
      <arg type="s" name="monitor_connector" direction="out"/>
    </method>
  </interface>
</node>`;

export const BUS_NAME = 'org.fluxmax.WindowWatcher';
export const OBJECT_PATH = '/org/fluxmax/WindowWatcher';

/**
 * Owns the org.fluxmax.WindowWatcher bus name and exports the
 * WindowWatcher D-Bus object. Tracks the last-published state so that
 * update() can dedup and avoid emitting redundant FocusChanged signals.
 */
export class WindowWatcherService {
    constructor() {
        this._state = {appId: '', fullscreen: false, connector: ''};
        this._impl = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
        this._ownerId = null;
        this._exported = false;
    }

    export() {
        this._impl.export(Gio.DBus.session, OBJECT_PATH);
        this._exported = true;
        this._ownerId = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            null,
            null,
            null);
    }

    unexport() {
        if (this._ownerId) {
            Gio.bus_unown_name(this._ownerId);
            this._ownerId = null;
        }
        // this._impl.unexport() asserts (GLib-GIO-CRITICAL) if called when
        // not currently exported, so guard it explicitly rather than
        // relying on it being silently idempotent.
        if (this._exported) {
            this._impl.unexport();
            this._exported = false;
        }
    }

    // D-Bus method: GetState() -> (s app_id, b fullscreen, s monitor_connector)
    GetState() {
        return [this._state.appId, this._state.fullscreen, this._state.connector];
    }

    // Updates the tracked state and emits FocusChanged, but only when at
    // least one of the three fields actually changed (dedup).
    update(appId, fullscreen, connector) {
        if (this._state.appId === appId &&
            this._state.fullscreen === fullscreen &&
            this._state.connector === connector)
            return;

        this._state = {appId, fullscreen, connector};
        this._impl.emit_signal('FocusChanged',
            new GLib.Variant('(sbs)', [appId, fullscreen, connector]));
    }
}
