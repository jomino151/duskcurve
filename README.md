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

### How fast a rule takes effect

A rule change is a reaction to something you just did — going fullscreen — not the
gradual circadian drift the schedule's easing is tuned for, so **rules apply instantly
by default**. The two speeds are configured separately:

```toml
[transition]
seconds = 20.0        # schedule / curve changes: eased over 20s
rule_seconds = 0.0    # rule engages or releases: 0 = instant (default)
```

Set `rule_seconds` above zero to ease rule changes instead; the transition then runs at
that rate in both directions, engaging and releasing. `seconds` is unaffected either
way — a manual temperature change or ordinary curve drift still eases over `seconds`.

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

For now (M7 will automate this), install it manually with a symlink:

```bash
ln -s "$PWD/extension/focused-window@fluxmax" ~/.local/share/gnome-shell/extensions/focused-window@fluxmax
```

Then **log out and back in** — on Wayland, GNOME Shell cannot be restarted in place, so
`Alt+F2 r` does not work here — and finally enable the extension:

```bash
gnome-extensions enable focused-window@fluxmax
```

## GUI

Launch with `fluxmax-gui` (requires the daemon to be running; if the daemon is not reachable, the app shows a status page instead of working offline, since the daemon owns the configuration and the GUI never edits the config file directly).

The curve editor needs the `python3-gi-cairo` system package (the PyGObject-to-pycairo
bridge) to draw. It's a separate package from `python3-cairo`/pycairo itself, so it's easy
to have one without the other. If it's missing, the rest of the GUI still works normally —
the curve editor just shows a status page naming this fix instead of the curve:

```bash
sudo apt install python3-gi-cairo
```

The window has three tabs:

- **Schedule**: Mode selection (built-in modes or a custom editable curve), wake and bedtime times (HH:MM), a manual temperature slider with colour-temperature presets (Ember, Candle, Dim Incandescent, Incandescent, Halogen, Fluorescent, Sunlight, Daylight), and a "Resume schedule" button to clear the override. The curve editor occupies the centre of the screen and shows the day's colour-temperature curve with markers for the configured wake/bedtime and solar events (sunrise/sunset).

- **Displays**: Per-display controls for each connected monitor. Each display shows its live colour temperature, an enable/disable toggle, an override kind (none, `warm_cap`, `fixed`, or a mired `offset`), and a value field. A manual slider and resume button are available below. An accent badge appears when a window rule is currently controlling that display, showing which rule and what action it is applying.

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
- **Rule transition (s)**: Easing time for rule engages and releases, separate from the regular transition. Rules react to deliberate user actions like going fullscreen, not the slow circadian drift, so this defaults to 0 (instant).
- **Tick interval (s)**: How often the daemon re-evaluates the schedule (default 2 s).
