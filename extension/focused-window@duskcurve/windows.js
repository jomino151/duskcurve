import Meta from 'gi://Meta';

/**
 * Tracks every Meta.Window so the extension can publish all visible fullscreen
 * windows, not just the focused one. Signal ids are nulled before disconnect()
 * and each disconnect is guarded separately: a window Mutter has already
 * disposed throws, and that must cost one disconnect only.
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

    // Connects the per-window signals; a no-op if the window is already tracked.
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
            // Drop the entry when Mutter starts unmanaging the window, before disposal.
            // Does not disconnect(); see _untrack().
            entry.unmanagedId = win.connect('unmanaged', () => this._untrack(win));
        } catch (e) {
            console.warn(`focused-window@duskcurve: WindowTracker failed to connect to a window: ${e}`);
        }

        this._windows.set(win, entry);
    }

    // Drops the window without disconnect(): by 'unmanaged' Mutter is already
    // tearing it down and disconnect() can throw.
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

    // Fullscreen windows that are visible now; the caller maps them to app ids and
    // connectors.
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

        // One try/catch per disconnect: a disposed window throws on the first, and the
        // rest must still be disconnected.
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
