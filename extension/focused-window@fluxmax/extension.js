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

        this._service = new WindowWatcherService();
        this._monitors = new MonitorMap();

        this._service.export();
        this._monitors.refresh();

        this._displaySignalId = global.display.connect('notify::focus-window', () => {
            this._onFocusChanged();
        });

        this._monitorManagerSignalId = global.backend.get_monitor_manager().connect(
            'monitors-changed', () => {
                this._monitors.refresh();
                this._publish();
            });

        this._onFocusChanged();
    }

    _onFocusChanged() {
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

        this._service?.unexport();
        this._service = null;
        this._monitors = null;
    }
}
