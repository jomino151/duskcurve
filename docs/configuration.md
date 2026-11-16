# Configuration

The config lives at `~/.config/duskcurve/config.toml`. The daemon reloads it when the file changes on disk.

## Sections

| Key | Default | Meaning |
| --- | --- | --- |
| `location.latitude`, `location.longitude` | `52.52`, `13.40` | Used for sunrise and sunset. |
| `location.timezone` | `"Europe/Berlin"` | IANA timezone name. |
| `schedule.wake`, `schedule.bedtime` | `"10:00"`, `"23:00"` | `HH:MM` anchors for the curve. |
| `schedule.mode` | `"reduce_eyestrain"` | `recommended`, `reduce_eyestrain`, `working_late`, `classic` or `custom`. |
| `[[schedule.keypoints]]` | none | `anchor` (`sunrise`, `sunset`, `wake`, `bedtime` or `HH:MM`), `offset_min`, `kelvin`, `easing` (`linear` or `smoothstep`). Required when `mode = "custom"`. |
| `range.min_kelvin`, `range.max_kelvin` | `1200`, `6500` | Temperature bounds. `max_kelvin` is also the `force_day` temperature. |
| `tick.interval_seconds` | `2.0` | How often the daemon re-evaluates. |
| `displays.<connector>.enabled` | `true` | `false` keeps that display neutral. |
| `displays.<connector>.override_kind` | `"none"` | `warm_cap` (never warmer than the value), `fixed` (the value in K), `offset` (mired delta, positive is warmer). |
| `displays.<connector>.override_value` | `0.0` | Must be > 0 for `warm_cap` and `fixed`. |

## Window rules

Each `[[rules]]` table is one rule. Rules need the shell extension and do nothing without it.

| Key | Default | Meaning |
| --- | --- | --- |
| `match_app_glob` | unset | `fnmatch` glob against the window's app id, e.g. `"org.gimp.*"`. |
| `match_fullscreen` | `false` | Match only fullscreen windows. |
| `match_unfocused` | `false` | Also match a visible fullscreen window that is not focused. Requires `match_fullscreen = true` and extension version 2. |
| `action` | required | `disable` (neutral), `force_day` (`range.max_kelvin`) or `fixed` (`action_kelvin`). |
| `action_kelvin` | none | Positive integer. Required for `fixed`, not allowed otherwise. |
| `scope` | `"window_monitor"` | `all`, `window_monitor` (the window's display) or a connector name such as `"DP-2"`. |

A rule must set `match_app_glob`, `match_fullscreen = true`, or both.

The first matching rule for the focused window wins. Rules with `match_unfocused = true` then claim only the displays the focused window's rule left free, in rule order.

```toml
# A fullscreen video keeps its display neutral, focused or not
[[rules]]
match_fullscreen = true
match_unfocused = true
action = "disable"

# Full daylight on every display while GIMP is focused
[[rules]]
match_app_glob = "org.gimp.*"
action = "force_day"
scope = "all"
```

With GNOME's "workspaces on primary display only" setting, an unfocused rule on a secondary display may release when you switch workspaces.

## Precedence

```
paused > display disabled > manual temperature > rule > curve + per-display override
```

## Transition pacing

How fast a change eases in depends on its cause.

| Cause | Key | Default |
| --- | --- | --- |
| Curve drift, override recompute, manual temperature expiring | `transition.seconds` | `20.0` |
| Manual temperature set or cleared | `transition.manual_seconds` | `2.0` |
| Rule engaging or releasing | `transition.rule_seconds` | `0.0` (instant) |
| Pause, resume, display enable or disable | none | instant |

Each value is a time constant: every tick closes `min(1, tick / seconds)` of the remaining gap.

## Theme switching

Off by default. When enabled, the daemon switches GNOME between a light and a dark variant at two anchors and corrects a missed switch on the first tick after suspend or lock.

| Key | Default | Meaning |
| --- | --- | --- |
| `theme.enabled` | `false` | Turns theme switching on. |
| `theme.light_from`, `theme.dark_from` | `"sunrise"`, `"sunset"` | Event anchor or `HH:MM`. Must not resolve to the same instant. |
| `theme.light_offset_min`, `theme.dark_offset_min` | `0` | Minutes to shift the anchor; negative is earlier. |
| `theme.<variant>.color_scheme` | `"prefer-light"` / `"prefer-dark"` | `default`, `prefer-light` or `prefer-dark`. |
| `theme.<variant>.gtk_theme`, `.icon_theme` | `"Yaru"` / `"Yaru-dark"` | Theme names. |
| `theme.<variant>.shell_theme` | `""` | Needs the [User Themes](https://extensions.gnome.org/extension/19/user-themes/) extension; skipped with a warning otherwise. |
| `theme.<variant>.cursor_theme` | `""` | Cursor theme name. |
| `theme.<variant>.wallpaper` | `""` | Absolute path. |

`<variant>` is `light` or `dark`. An empty string leaves that setting untouched.

Theme precedence is `paused (freeze) > manual theme > theme anchors`. Rules never change the theme. A theme you pick by hand in GNOME Settings stays until the next anchor.

Uninstalling does not restore your previous theme.

`./install.sh` warns if `auto-darkmode-switcher` is installed, since both write the same keys. Disable it before enabling `[theme]`:

```bash
systemctl --user disable --now auto-darkmode-switcher.service auto-darkmode-switcher.timer
```

## GUI

`duskcurve-gui` needs the running daemon and shows a status page without it.

| Tab | Contents |
| --- | --- |
| Schedule | Mode, wake and bedtime, curve editor, manual temperature with presets and a duration. |
| Displays | Per-display live temperature, enable switch, override, manual temperature, and the rule currently in control. |
| Rules | Ordered rule list with add, edit, reorder and delete. Active rules are highlighted. |
| Theme | Light/Dark/Auto override, anchors, and the settings for each variant. |

The menu opens Preferences: location, temperature range, transition times and tick interval.

| Curve editor action | Effect |
| --- | --- |
| Drag a keypoint vertically | Changes its temperature. On a built-in mode this first copies the curve to `custom`. |
| Drag an event-anchored keypoint horizontally | Changes its offset; the keypoint stays anchored to the event. |
| Double-click | Inserts a keypoint. |
| Right-click | Changes the keypoint's anchor or deletes it. The last keypoint cannot be deleted. |

Without `python3-gi-cairo` the curve editor shows an install hint instead of the curve.

Saving from the GUI rewrites `config.toml` in canonical form, so hand-written comments and key order are lost.
