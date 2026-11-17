import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {WindowWatcherService} from './dbus.js';
import {MonitorMap} from './monitors.js';
import {WindowTracker} from './windows.js';

export default class FocusedWindowExtension extends Extension {
    enable() {
        // Initialise every tracked id to a disconnect-safe value first, so disable() is
        // well-defined even if enable() fails partway.
        this._focusWindow = null;
        this._displaySignalId = null;
        this._monitorManagerSignalId = null;
        this._fullscreenId = null;
        this._monitorChangedId = null;
        this._unmanagedId = null;
        this._service = null;
        this._monitors = null;
        this._monitorManager = null;
        this._tracker = null;

        try {
            this._service = new WindowWatcherService();
            this._monitors = new MonitorMap();

            this._service.export();

            // Created after the service and monitor map, which its callback needs; _publish()
            // also guards on the service in case a callback lands mid-teardown.
            this._tracker = new WindowTracker(() => this._publish());

            this._displaySignalId = global.display.connect('notify::focus-window', () => {
                this._onFocusChanged();
            });

            // Cached so disable() disconnects from the same object that was connected.
            this._monitorManager = global.backend.get_monitor_manager();
            this._monitorManagerSignalId = this._monitorManager.connect(
                'monitors-changed', () => {
                    this._monitors?.refresh(() => this._publish());
                });

            // refresh() is asynchronous: publishing now would see an empty map and emit an
            // empty monitor_connector, so the first state is published on completion.
            this._monitors.refresh(() => this._onFocusChanged());
        } catch (e) {
            console.warn(`focused-window@duskcurve: enable() failed, tearing down: ${e}`);
            this.disable();
            throw e;
        }
    }

    _onFocusChanged() {
        // A refresh() completion can land after disable(); bail out rather than
        // reconnecting handlers nothing will clean up.
        if (!this._service)
            return;

        const win = global.display.focus_window;

        // Null each id before disconnect(): this._focusWindow may already be disposed by
        // Mutter, and disconnecting a disposed Meta.Window throws. A stale id would then
        // wedge every later focus change.
        try {
            if (this._fullscreenId !== null) {
                const id = this._fullscreenId;
                this._fullscreenId = null;
                this._focusWindow?.disconnect(id);
            }
            if (this._monitorChangedId !== null) {
                const id = this._monitorChangedId;
                this._monitorChangedId = null;
                this._focusWindow?.disconnect(id);
            }
            if (this._unmanagedId !== null) {
                const id = this._unmanagedId;
                this._unmanagedId = null;
                this._focusWindow?.disconnect(id);
            }
        } catch (e) {
            console.warn(`focused-window@duskcurve: _onFocusChanged() teardown error: ${e}`);
        }

        this._focusWindow = win;

        if (win !== null) {
            this._fullscreenId = win.connect('notify::fullscreen', () => this._publish());
            this._monitorChangedId = win.connect('notify::main-monitor', () => this._publish());
            // Drop the reference once Mutter starts unmanaging the window, before it is
            // disposed, so a later disconnect() never touches a disposed wrapper.
            this._unmanagedId = win.connect('unmanaged', () => {
                this._focusWindow = null;
            });
        }

        this._publish();
    }

    _publish() {
        // Callbacks (refresh, tracker) can land after disable() has torn everything down.
        if (!this._service)
            return;

        const win = global.display.focus_window;
        const entries = [];

        if (win !== null) {
            const appId = win.get_gtk_application_id() ??
                win.get_sandboxed_app_id() ??
                win.get_wm_class() ??
                '';
            const fullscreen = win.is_fullscreen();
            const connector = this._monitors.connectorFor(win.get_monitor());
            entries.push([appId, fullscreen, true, connector]);
        }

        // Focused window first, then every other visible fullscreen window as unfocused.
        for (const other of this._tracker?.visibleFullscreenWindows() ?? []) {
            if (other === win)
                continue;

            try {
                const appId = other.get_gtk_application_id() ??
                    other.get_sandboxed_app_id() ??
                    other.get_wm_class() ??
                    '';
                const connector = this._monitors.connectorFor(other.get_monitor());
                entries.push([appId, true, false, connector]);
            } catch (e) {
                console.warn(`focused-window@duskcurve: _publish() failed to read a window: ${e}`);
            }
        }

        this._service.updateWindows(entries);
    }

    disable() {
        // Disconnects can throw during compositor restart or shutdown; unexporting the
        // service must happen regardless, or the bus name leaks and the next enable()
        // exports a duplicate.
        try {
            if (this._fullscreenId !== null) {
                this._focusWindow?.disconnect(this._fullscreenId);
                this._fullscreenId = null;
            }
            if (this._monitorChangedId !== null) {
                this._focusWindow?.disconnect(this._monitorChangedId);
                this._monitorChangedId = null;
            }
            if (this._unmanagedId !== null) {
                this._focusWindow?.disconnect(this._unmanagedId);
                this._unmanagedId = null;
            }
            this._focusWindow = null;

            if (this._displaySignalId !== null) {
                global.display.disconnect(this._displaySignalId);
                this._displaySignalId = null;
            }

            if (this._monitorManagerSignalId !== null) {
                this._monitorManager?.disconnect(this._monitorManagerSignalId);
                this._monitorManagerSignalId = null;
            }
        } catch (e) {
            console.warn(`focused-window@duskcurve: disable() teardown error: ${e}`);
        } finally {
            // Release the bus name first so a throw from tracker.destroy() cannot leave it owned.
            this._service?.unexport();
            this._service = null;
            this._monitors = null;
            this._monitorManager = null;
            this._tracker?.destroy();
            this._tracker = null;
        }
    }
}
