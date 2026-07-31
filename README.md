# flux-max
GNOME/Wayland-native, f.lux-equivalent blue-light filter. GPL-3.0-or-later.

## App / fullscreen rules

The daemon can react to the focused application or a fullscreen window per display,
via a `[[rules]]` list in `config.toml`. Each table in the list is one rule.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `match_app_glob` | string or absent | none (unset) | Glob pattern (`fnmatch` syntax) matched against the focused window's app id, e.g. `"org.gimp.*"`. |
| `match_fullscreen` | bool | `false` | If `true`, the rule only matches while the focused window is fullscreen. |
| `action` | string | required | One of `"disable"` (turn the filter off — neutral colour), `"force_day"` (fixed at the configured daytime `max_kelvin`), or `"fixed"` (fixed at `action_kelvin`). |
| `action_kelvin` | positive integer | required when `action = "fixed"`, else must be absent | The fixed colour temperature in Kelvin to apply. Must be a positive integer (`> 0`, not a bool) — `0`, a negative value, or `true`/`false` all fail config validation. |
| `scope` | non-empty string | `"window_monitor"` | Which display(s) the rule's action applies to: `"all"` (every display), `"window_monitor"` (only the display the focused window is on), or a specific connector name (e.g. `"DP-2"`). An empty string fails config validation. |

At least one of `match_app_glob` or `match_fullscreen = true` must be set — a rule with
neither would match every window forever.

Rules are evaluated **first-match-wins**: the list is scanned in order, and the first
rule whose `match_app_glob` and `match_fullscreen` constraints both hold for the
currently focused window is applied. Later rules are not considered, even if they would
also match.

Rules sit in the middle of the daemon's precedence chain for what temperature a display
shows:

```
paused > display disabled > manual temp > rule > curve + per-display override
```

A paused daemon or a disabled display always wins outright; a manual temporary
override (set via the D-Bus API) beats any rule; a matching rule beats the normal
schedule curve; and with no override, no manual temp, and no matching rule, the display
just follows its usual curve (plus any per-display override configured under
`[displays.<connector>]`).

### How fast a change takes effect

A single "eased over N seconds" transition feels right for the sun crawling across the
sky and wrong for everything else: nobody wants to wait 20 seconds for a manual slider
drag to land, and pausing or flipping a display off should be immediate, not a fade.
So the pacing that applies to a given temperature change depends on what caused it, not
just that it changed:

- **Schedule** — the curve drifting as time passes, or an override recomputing. This is
  the slow, natural one; it eases over `transition_seconds` (default 20 s). A manual
  temp that expires on its own (its duration ran out) also drifts back at this pace,
  because nobody asked for that change right now — it's the schedule reclaiming control.
- **Manual** — you set or clear a manual temperature (slider, quick-set, or the "Resume
  schedule" button). Deliberate, but not urgent; eases over `manual_transition_seconds`
  (default 2 s). This is a time constant, not a duration — each tick closes
  `min(1, dt/seconds)` of the remaining gap, so at the default 2 s tick interval a
  2.0 s constant settles the whole change in about one tick.
- **Toggle** — pausing/unpausing the daemon, or enabling/disabling a display. Both are
  a binary on/off decision with no useful in-between rate, so these are hardcoded
  **instant** (not configurable).
- **Rule** — a window rule engaging or releasing (e.g. going fullscreen). A reaction to
  something you just did, not the circadian drift the schedule's easing is tuned for, so
  **rules apply instantly by default**, configurable via `rule_transition_seconds`.

```toml
[transition]
seconds = 20.0         # schedule: curve drift / override recompute / manual-temp expiry
manual_seconds = 2.0   # manual: a deliberate temp set or clear
rule_seconds = 0.0     # rule: a rule engaging or releasing -- 0 = instant (default)
# pause/unpause and display enable/disable are always instant, not configurable
```

Each category's easing rate is independent of the others — changing one doesn't affect
how the rest transition.

Two copyable examples:

```toml
# Any fullscreen window disables the filter on the display it is on
[[rules]]
match_fullscreen = true
action = "disable"
scope = "window_monitor"

# Colour-critical work: full daylight on every display while GIMP is focused
[[rules]]
match_app_glob = "org.gimp.*"
action = "force_day"
scope = "all"
```

## Install

```bash
./install.sh
```

This is idempotent — re-running it is the supported way to upgrade. It:

- creates a `--system-site-packages` virtualenv at `~/.local/share/fluxmax/venv` and installs
  flux-max into it;
- symlinks `fluxmax-daemon`, `fluxmax-gui` and `fluxmax-apply` into `~/.local/bin`;
- installs the icon, desktop entry, the `focused-window@fluxmax` GNOME Shell extension, and the
  `fluxmax-daemon` systemd user unit;
- records your current Night Light setting (so `./uninstall.sh` can restore it) and enables and
  starts the daemon.

Flags:

- `--dev` — editable install (`pip install -e`) and a symlinked extension, for working on
  flux-max itself. The default copies both instead, so the install survives the repo being
  moved or deleted.
- `--no-start` — install everything but don't enable or start the daemon.
- `-h`, `--help` — show usage.

First run, after `./install.sh` finishes:

1. **Log out and back in.** On Wayland, GNOME Shell cannot be restarted in place
   (`Alt+F2 r` does not work), so the extension will not load until you do. Until then,
   window rules are inert — the daemon still runs the schedule normally on every display.
2. Set your location and wake/bedtime, either by launching `fluxmax-gui` or by editing
   `~/.config/fluxmax/config.toml` directly.

To remove flux-max:

```bash
./uninstall.sh
```

This stops and removes the daemon unit, resets every display back to 6500 K, restores Night
Light to the value it had before `./install.sh` ran (or, if that value was never successfully
recorded, leaves Night Light alone and tells you so), and removes the extension, desktop entry,
icon, `~/.local/bin` symlinks and venv. Your `~/.config/fluxmax/config.toml` is kept, so a
later reinstall picks up where you left off — pass `--purge` to delete it too.

The curve editor also needs the `python3-gi-cairo` system package (the PyGObject-to-pycairo
bridge), which `./install.sh` offers to install for you via `sudo apt install
python3-gi-cairo` if it's missing. See the GUI section below for what happens if you decline.

## GNOME Shell extension

Rules need to know which application is focused and whether it's fullscreen. On
GNOME/Wayland, that information isn't available to a background daemon directly — it
has to come from inside the shell. The `focused-window@fluxmax` extension in this repo
publishes that state as a small D-Bus service, `org.fluxmax.WindowWatcher`, which the
daemon's `watcher.py` consumes.

Without the extension installed and enabled, `[[rules]]` are simply inert: the watcher
degrades gracefully (no window state is ever reported), no rule ever matches, and the
daemon keeps running its normal brightness/temperature schedule on every display exactly
as if no rules were configured.

`./install.sh` installs and enables the extension for you, but it cannot load into a running
GNOME Shell session: on Wayland, GNOME Shell cannot be restarted in place, so `Alt+F2 r` does
not work here. You must **log out and back in** before rules take effect.

## GUI

Launch with `fluxmax-gui` (requires the daemon to be running; if the daemon is not reachable, the app shows a status page instead of working offline, since the daemon owns the configuration and the GUI never edits the config file directly).

The curve editor needs the `python3-gi-cairo` system package (the PyGObject-to-pycairo
bridge) to draw. It's a separate package from `python3-cairo`/pycairo itself, so it's easy
to have one without the other. `./install.sh` offers to install it for you; if it's missing,
the rest of the GUI still works normally — the curve editor just shows a status page naming
this fix instead of the curve.

The window has three tabs:

- **Schedule**: Mode selection (built-in modes or a custom editable curve), wake and bedtime times (HH:MM), a manual temperature slider with colour-temperature presets (Ember, Candle, Dim Incandescent, Incandescent, Halogen, Fluorescent, Sunlight, Daylight), a **Duration** for the override (Until cleared, 15 min, 30 min, 1 hour, 2 hours), and a "Resume schedule" button to clear it. The curve editor occupies the centre of the screen and shows the day's colour-temperature curve with markers for the configured wake/bedtime and solar events (sunrise/sunset).

- **Displays**: Per-display controls for each connected monitor. Each display shows its live colour temperature, an enable/disable toggle, an override kind (none, `warm_cap`, `fixed`, or a mired `offset`), a value field, and a **Manual duration** for that display's own override. A manual slider and resume button are available below. An accent badge appears when a window rule is currently controlling that display, showing which rule and what action it is applying.

Whenever a manual override is running, the group description says so, and — if it was given a duration — counts down: *"Manual override active — 25 min left."* An override set to "Until cleared" says that instead, so a forgotten override never looks like a dead schedule. The duration is chosen per set: the dropdown is a control rather than a readout, so it always starts at "Until cleared" and does not reflect what is currently running. The countdown is the thing that tells you that. Changing the dropdown while an override is already running re-arms that override at the new duration immediately, so you can add or remove a timer without touching the slider.

- **Rules**: The ordered list of app and fullscreen rules (first-match-wins: rules are evaluated top to bottom, and the first match wins). Use "Add rule" to create a new rule, or click the edit icon on an existing rule to change it. Up/down arrows reorder rules; delete removes them with an undo toast. Rules are highlighted if they are currently active on any display, showing which monitors they control.

### Curve editing

- **Dragging a keypoint vertically**: Change its temperature on release. If the mode is built-in, dragging a keypoint copies the curve to an editable custom mode and switches the mode automatically.

- **Dragging an event-anchored keypoint horizontally**: Adjust its offset. The config file will still name the event anchor (`sunrise`, `sunset`, `wake`, `bedtime`) rather than a fixed clock time, so the keypoint keeps tracking that event as the seasons change.

- **Double-click**: Insert a new keypoint at the clicked time.

- **Right-click**: Open a popover to change the keypoint's time anchor (fixed or event-based) or delete it. Deletion is refused at the last keypoint.

### Configuration file warning

Saving from the GUI rewrites `~/.config/fluxmax/config.toml` through the daemon's own serializer, so any hand-written comments, key order, or layout is not preserved. The app shows a one-time banner on the first save of the session confirming this. Users who hand-edit the config file should know this before they lose a comment. The GUI also monitors the file for external changes — if you edit the TOML by hand while the app is open, the daemon picks up the change and the GUI follows it within a couple of seconds.

### Menu

A menu button (☰) in the header opens **Preferences**, where you can set the location (latitude, longitude, and IANA timezone), temperature range (minimum and maximum Kelvin), and timing parameters:

- **Transition (s)**: Easing time for the gradual circadian drift (default 20 s).
- **Manual transition (s)**: Easing time for a deliberate temperature change (slider, quick-set), as distinct from the schedule's gradual drift — quick, but not snap-instant like a pause or display toggle (default 2 s).
- **Rule transition (s)**: Easing time for rule engages and releases, separate from the regular transition. Rules react to deliberate user actions like going fullscreen, not the slow circadian drift, so this defaults to 0 (instant).
- **Tick interval (s)**: How often the daemon re-evaluates the schedule (default 2 s).

Pausing and enabling/disabling a display are not in this list: they are hardcoded instant, since a binary on/off decision has no useful in-between rate to configure.
