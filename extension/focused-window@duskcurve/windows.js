import Meta from 'gi://Meta';

/**
 * Tracks every Meta.Window Mutter knows about so the extension can publish
 * not just the focused window but every *visible fullscreen* window
 * alongside it (see visibleFullscreenWindows()). Owns all of the per-window
 * signal bookkeeping this requires, so extension.js does not grow a second
 * copy of its already-careful connect/disconnect dance for the focus
 * window.
 *
 * Teardown discipline mirrors extension.js exactly: null a stored signal id
 * *before* calling disconnect() on it, wrap each teardown group in
 * try/catch, and never let one failed disconnect skip the rest. A
 * Meta.Window GObject wrapper can already be disposed by Mutter (Mutter
 * calls g_object_run_dispose() on it at the end of unmanaging), and
 * disconnect() on a disposed GObject throws in GJS.
 */
export class WindowTracker {
    constructor(onChanged) {
        this._onChanged = onChanged;

        // Meta.Window -> {fullscreenId, minimizedId, monitorChangedId,
        //                 workspaceChangedId, unmanagedId}
        this._windows = new Map();

        this._displaySignalId = null;
        this._workspaceManagerSignalId = null;

        try {
            for (const win of global.display.list_all_windows())
                this._track(win);
        } catch (e) {
            console.warn(`focused-window@duskcurve: WindowTracker seed failed: ${e}`);
        }

        try {
            this._displaySignalId = global.display.connect(
                'window-created', (_display, win) => {
                    try {
                        this._track(win);
                        this._onChanged?.();
                    } catch (e) {
                        console.warn(
                            `focused-window@duskcurve: WindowTracker window-created handler error: ${e}`);
                    }
                });
        } catch (e) {
            console.warn(`focused-window@duskcurve: WindowTracker failed to connect window-created: ${e}`);
        }

        try {
            this._workspaceManagerSignalId = global.workspace_manager.connect(
                'active-workspace-changed', () => this._changed());
        } catch (e) {
            console.warn(
                `focused-window@duskcurve: WindowTracker failed to connect active-workspace-changed: ${e}`);
        }
    }

    // Connects the per-window signals this tracker cares about. A no-op if
    // the window is already tracked (window-created should never fire
    // twice for the same window, but this makes _track() safe to call
    // defensively regardless).
    _track(win) {
        if (this._windows.has(win))
            return;

        const entry = {
            fullscreenId: null,
            minimizedId: null,
            monitorChangedId: null,
            workspaceChangedId: null,
            unmanagedId: null,
        };

        try {
            entry.fullscreenId = win.connect('notify::fullscreen', () => this._changed());
            entry.minimizedId = win.connect('notify::minimized', () => this._changed());
            entry.monitorChangedId = win.connect('notify::main-monitor', () => this._changed());
            entry.workspaceChangedId = win.connect('workspace-changed', () => this._changed());
            // Clear this window's tracking entry as soon as Mutter starts
            // unmanaging it, i.e. before g_object_run_dispose() runs on
            // it, so nothing later calls disconnect() on an
            // already-disposed GObject wrapper. Deliberately does NOT call
            // disconnect() itself -- see _untrack().
            entry.unmanagedId = win.connect('unmanaged', () => this._untrack(win));
        } catch (e) {
            console.warn(`focused-window@duskcurve: WindowTracker failed to connect to a window: ${e}`);
        }

        this._windows.set(win, entry);
    }

    // unmanaged handler: drop the window from the tracked map WITHOUT
    // calling disconnect() on it. By the time 'unmanaged' fires, Mutter is
    // already tearing the window down; disconnect() on it can throw. Clear
    // the entry's stored ids first (so nothing could ever mistake them for
    // live subscriptions to disconnect), then drop the entry itself.
    _untrack(win) {
        const entry = this._windows.get(win);
        if (entry) {
            entry.fullscreenId = null;
            entry.minimizedId = null;
            entry.monitorChangedId = null;
            entry.workspaceChangedId = null;
            entry.unmanagedId = null;
        }
        this._windows.delete(win);
        this._changed();
    }

    _changed() {
        try {
            this._onChanged?.();
        } catch (e) {
            console.warn(`focused-window@duskcurve: WindowTracker onChanged handler error: ${e}`);
        }
    }

    // Returns the Meta.Window objects that are fullscreen and visible right
    // now, per the shared predicate documented in the design doc. The
    // caller (extension.js) maps each one to an app id and monitor
    // connector; this class only decides *which* windows qualify.
    visibleFullscreenWindows() {
        const result = [];

        for (const win of this._windows.keys()) {
            try {
                if (win.is_fullscreen() &&
                    !win.minimized &&
                    !win.is_override_redirect() &&
                    win.get_window_type() === Meta.WindowType.NORMAL &&
                    win.showing_on_its_workspace() &&
                    (win.is_on_all_workspaces() ||
                     win.get_workspace() === global.workspace_manager.get_active_workspace()))
                    result.push(win);
            } catch (e) {
                console.warn(`focused-window@duskcurve: WindowTracker predicate error: ${e}`);
            }
        }

        return result;
    }

    destroy() {
        try {
            if (this._displaySignalId !== null) {
                const id = this._displaySignalId;
                this._displaySignalId = null;
                global.display.disconnect(id);
            }
        } catch (e) {
            console.warn(`focused-window@duskcurve: WindowTracker destroy() teardown error: ${e}`);
        }

        try {
            if (this._workspaceManagerSignalId !== null) {
                const id = this._workspaceManagerSignalId;
                this._workspaceManagerSignalId = null;
                global.workspace_manager.disconnect(id);
            }
        } catch (e) {
            console.warn(`focused-window@duskcurve: WindowTracker destroy() teardown error: ${e}`);
        }

        // One try/catch PER DISCONNECT, not per window. A Meta.Window wrapper
        // that Mutter has already disposed throws on the first disconnect(),
        // and a single try around all five would then skip the other four --
        // leaving live handlers on a window this tracker no longer owns. The
        // whole point of the rule is that a throw must cost exactly one
        // disconnect, never the rest of the teardown.
        const ID_FIELDS = [
            'fullscreenId', 'minimizedId', 'monitorChangedId',
            'workspaceChangedId', 'unmanagedId',
        ];
        for (const [win, entry] of this._windows) {
            for (const field of ID_FIELDS) {
                try {
                    if (entry[field] !== null) {
                        const id = entry[field];
                        entry[field] = null;
                        win.disconnect(id);
                    }
                } catch (e) {
                    console.warn(
                        `focused-window@duskcurve: WindowTracker destroy() ` +
                        `failed to disconnect ${field}: ${e}`);
                }
            }
        }

        this._windows.clear();
        this._onChanged = null;
    }
}
