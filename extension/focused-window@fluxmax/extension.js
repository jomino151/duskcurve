import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {WindowWatcherService} from './dbus.js';
import {MonitorMap} from './monitors.js';

export default class FocusedWindowExtension extends Extension {
    enable() {
        // Initialize every tracked id/reference to a known, disconnect-safe
        // state *before* anything that could throw, so that disable() is
        // always well-defined even if enable() fails partway through.
        this._focusWindow = null;
        this._displaySignalId = null;
        this._monitorManagerSignalId = null;
        this._fullscreenId = null;
        this._monitorChangedId = null;
        this._service = null;
        this._monitors = null;

        try {
            this._service = new WindowWatcherService();
            this._monitors = new MonitorMap();

            this._service.export();

            this._displaySignalId = global.display.connect('notify::focus-window', () => {
                this._onFocusChanged();
            });

            this._monitorManagerSignalId = global.backend.get_monitor_manager().connect(
                'monitors-changed', () => {
                    this._monitors.refresh(() => this._publish());
                });

            // refresh() is asynchronous; its completion lands on a later
            // main-loop iteration. Publishing synchronously here would
            // always see an empty _byIndex and therefore always publish
            // an empty monitor_connector for the very first state (and
            // the daemon's startup GetState() would read that stale
            // value). Instead, publish from the completion callback so
            // the first published state carries a real connector.
            this._monitors.refresh(() => this._onFocusChanged());
        } catch (e) {
            console.warn(`focused-window@fluxmax: enable() failed, tearing down: ${e}`);
            this.disable();
            throw e;
        }
    }

    _onFocusChanged() {
        // A monitors.refresh() completion callback can land after
        // disable() has already run (e.g. enable()/disable() in quick
        // succession, or a refresh in flight at shutdown). Bail out
        // rather than reconnecting handlers nothing will ever clean up.
        if (!this._service)
            return;

        const win = global.display.focus_window;

        if (this._fullscreenId !== null) {
            this._focusWindow?.disconnect(this._fullscreenId);
            this._fullscreenId = null;
        }
        if (this._monitorChangedId !== null) {
            this._focusWindow?.disconnect(this._monitorChangedId);
            this._monitorChangedId = null;
        }

        this._focusWindow = win;

        if (win !== null) {
            this._fullscreenId = win.connect('notify::fullscreen', () => this._publish());
            this._monitorChangedId = win.connect('monitor-changed', () => this._publish());
        }

        this._publish();
    }

    _publish() {
        // Same rationale as _onFocusChanged(): a refresh() callback (from
        // enable()'s initial refresh or the monitors-changed handler) can
        // land after disable() has torn everything down. Without this
        // guard, this._service.update(...) below would throw on a null
        // service.
        if (!this._service)
            return;

        const win = global.display.focus_window;

        if (win === null) {
            this._service.update('', false, '');
            return;
        }

        const appId = win.get_gtk_application_id() ??
            win.get_sandboxed_app_id() ??
            win.get_wm_class() ??
            '';
        const fullscreen = win.is_fullscreen();
        const connector = this._monitors.connectorFor(win.get_monitor());

        this._service.update(appId, fullscreen, connector);
    }

    disable() {
        // The disconnects below dereference global.display and
        // global.backend.get_monitor_manager(), which can plausibly throw
        // during a compositor-restart or shutdown transition. Unexporting
        // the service and nulling our references must happen regardless,
        // or the bus name is never released and the next enable() exports
        // a second, duplicate object.
        try {
            if (this._fullscreenId !== null) {
                this._focusWindow?.disconnect(this._fullscreenId);
                this._fullscreenId = null;
            }
            if (this._monitorChangedId !== null) {
                this._focusWindow?.disconnect(this._monitorChangedId);
                this._monitorChangedId = null;
            }
            this._focusWindow = null;

            if (this._displaySignalId !== null) {
                global.display.disconnect(this._displaySignalId);
                this._displaySignalId = null;
            }

            if (this._monitorManagerSignalId !== null) {
                global.backend.get_monitor_manager().disconnect(this._monitorManagerSignalId);
                this._monitorManagerSignalId = null;
            }
        } catch (e) {
            console.warn(`focused-window@fluxmax: disable() teardown error: ${e}`);
        } finally {
            this._service?.unexport();
            this._service = null;
            this._monitors = null;
        }
    }
}
