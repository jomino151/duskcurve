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
        this._unmanagedId = null;
        this._service = null;
        this._monitors = null;
        this._monitorManager = null;

        try {
            this._service = new WindowWatcherService();
            this._monitors = new MonitorMap();

            this._service.export();

            this._displaySignalId = global.display.connect('notify::focus-window', () => {
                this._onFocusChanged();
            });

            // Cached once here rather than re-fetched in disable(): Mutter
            // creates the monitor manager once at backend init, so this is
            // the same object either way, but caching makes that correct by
            // construction instead of an assumption disable() has to repeat.
            this._monitorManager = global.backend.get_monitor_manager();
            this._monitorManagerSignalId = this._monitorManager.connect(
                'monitors-changed', () => {
                    this._monitors?.refresh(() => this._publish());
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

        // Null each id BEFORE calling disconnect(), and don't let a throw
        // from one disconnect() skip the others or skip reassigning
        // _focusWindow/_publish() below. this._focusWindow can be a
        // Meta.Window GObject wrapper that Mutter has already disposed
        // (g_object_run_dispose() at the end of meta_window_unmanage()); a
        // disconnect() call on a disposed GObject throws in GJS. If the id
        // were nulled only after a (thrown) disconnect() call, it would
        // stay non-null forever, this._focusWindow would never be
        // reassigned, and this._publish() below would never run again --
        // wedging the extension (and the daemon, stuck on the last
        // published state) until a manual re-enable.
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
            console.warn(`focused-window@fluxmax: _onFocusChanged() teardown error: ${e}`);
        }

        this._focusWindow = win;

        if (win !== null) {
            this._fullscreenId = win.connect('notify::fullscreen', () => this._publish());
            this._monitorChangedId = win.connect('monitor-changed', () => this._publish());
            // Clear _focusWindow as soon as Mutter starts unmanaging this
            // window, i.e. before g_object_run_dispose() runs on it, so a
            // later disconnect() above never touches an already-disposed
            // GObject wrapper.
            this._unmanagedId = win.connect('unmanaged', () => {
                this._focusWindow = null;
            });
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
        // this._monitorManager, which can plausibly throw during a
        // compositor-restart or shutdown transition. Unexporting the
        // service and nulling our references must happen regardless, or
        // the bus name is never released and the next enable() exports a
        // second, duplicate object.
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
            console.warn(`focused-window@fluxmax: disable() teardown error: ${e}`);
        } finally {
            this._service?.unexport();
            this._service = null;
            this._monitors = null;
            this._monitorManager = null;
        }
    }
}
