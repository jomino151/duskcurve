# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.1.0] - 2026-11-17

### Changed

- Renamed from flux-max to Duskcurve (package, commands, D-Bus names, config path `~/.config/duskcurve`).
- The default location is Berlin.

### Fixed

- D-Bus calls now return an error instead of timing out on unexpected failures.

## [1.0.4] - 2026-10-13

### Changed

- Supports GNOME 50 and Ubuntu 26.04. The shell extension now loads on GNOME 50 only; use 1.0.3 on GNOME 46.

### Fixed

- `./install.sh` rebuilds the venv after a system Python upgrade.

## [1.0.3] - 2026-09-28

### Changed

- The daemon skips gamma updates when the ramp has not changed and re-sends it every 60 s, which cuts idle CPU use.
- Gamma ramps are encoded faster.

## [1.0.2] - 2026-08-19

### Added

- Light/dark desktop theme switching at solar or clock anchors, with a Theme tab and a manual override.
- `match_unfocused` rules act on visible fullscreen windows that are not focused. Needs extension version 2.

## [1.0.0] - 2026-07-31

### Added

- Daemon that sets per-display colour temperature through Mutter's `DisplayConfig` D-Bus API.
- Keypoint curve anchored to sunrise, sunset, wake and bedtime, with built-in modes.
- Per-display overrides, timed manual temperatures, and app and fullscreen rules through a GNOME Shell extension.
- GTK4/libadwaita GUI, and `install.sh` / `uninstall.sh`.

[Unreleased]: https://github.com/jomino151/duskcurve/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/jomino151/duskcurve/compare/v1.0.4...v1.1.0
[1.0.4]: https://github.com/jomino151/duskcurve/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/jomino151/duskcurve/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/jomino151/duskcurve/compare/v1.0.0...v1.0.2
[1.0.0]: https://github.com/jomino151/duskcurve/releases/tag/v1.0.0
