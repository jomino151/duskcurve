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
| `action_kelvin` | integer | required when `action = "fixed"`, else must be absent | The fixed colour temperature in Kelvin to apply. |
| `scope` | string | `"window_monitor"` | Which display(s) the rule's action applies to: `"all"` (every display), `"window_monitor"` (only the display the focused window is on), or a specific connector name (e.g. `"DP-2"`). |

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
