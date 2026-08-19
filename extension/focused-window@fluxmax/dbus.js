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
    <signal name="WindowsChanged">
      <arg type="a(sbbs)" name="windows"/>
    </signal>
    <method name="GetState">
      <arg type="s" name="app_id" direction="out"/>
      <arg type="b" name="fullscreen" direction="out"/>
      <arg type="s" name="monitor_connector" direction="out"/>
    </method>
    <method name="GetWindows">
      <arg type="a(sbbs)" name="windows" direction="out"/>
    </method>
  </interface>
</node>`;

export const BUS_NAME = 'org.fluxmax.WindowWatcher';
export const OBJECT_PATH = '/org/fluxmax/WindowWatcher';

// Deep-compares two window lists as produced by updateWindows(): arrays of
// [app_id, fullscreen, focused, monitor_connector] tuples, in order.
function windowsEqual(a, b) {
    if (a.length !== b.length)
        return false;

    for (let i = 0; i < a.length; i++) {
        const [aId, aFullscreen, aFocused, aConnector] = a[i];
        const [bId, bFullscreen, bFocused, bConnector] = b[i];
        if (aId !== bId || aFullscreen !== bFullscreen ||
            aFocused !== bFocused || aConnector !== bConnector)
            return false;
    }

    return true;
}

/**
 * Owns the org.fluxmax.WindowWatcher bus name and exports the
 * WindowWatcher D-Bus object. Tracks the last-published legacy triple and
 * the last-published window list separately, so that updateWindows() can
 * dedup each of FocusChanged and WindowsChanged independently and avoid
 * emitting either redundantly.
 */
export class WindowWatcherService {
    constructor() {
        this._state = {appId: '', fullscreen: false, connector: ''};
        this._windows = [];
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
    // Legacy, untouched: still reflects the focused window only.
    GetState() {
        return [this._state.appId, this._state.fullscreen, this._state.connector];
    }

    // D-Bus method: GetWindows() -> (a(sbbs) windows)
    GetWindows() {
        return this._windows;
    }

    // Updates the tracked window set and emits FocusChanged / WindowsChanged
    // as needed. `list` is an array of [app_id, fullscreen, focused,
    // monitor_connector] tuples; at most one entry has focused === true.
    updateWindows(list) {
        const focusedEntry = list.find(([, , focused]) => focused);
        const legacy = focusedEntry
            ? [focusedEntry[0], focusedEntry[1], focusedEntry[3]]
            : ['', false, ''];

        // Exactly the old update()'s dedup rule, applied to the legacy
        // triple derived above, so a daemon that has not been updated yet
        // sees identical FocusChanged behaviour.
        if (this._state.appId !== legacy[0] ||
            this._state.fullscreen !== legacy[1] ||
            this._state.connector !== legacy[2]) {
            this._state = {appId: legacy[0], fullscreen: legacy[1], connector: legacy[2]};
            this._impl.emit_signal('FocusChanged',
                new GLib.Variant('(sbs)', legacy));
        }

        if (!windowsEqual(this._windows, list)) {
            this._windows = list;
            this._impl.emit_signal('WindowsChanged',
                new GLib.Variant('(a(sbbs))', [list]));
        }
    }
}
