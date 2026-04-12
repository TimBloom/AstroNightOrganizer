# Changelog

All notable changes to AstroNightOrganizer are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.3.1] — 2026-04-12

### Added
- **`AstroNightOrganizer.sh`** macOS/Linux launcher — equivalent to the Windows `.bat`
  file. Checks for `uv`, then runs `uv run python run.py` from the project directory.
  Make it executable once with `chmod +x AstroNightOrganizer.sh`, then double-click or
  run from a terminal.

---

## [0.3.0] — 2026-04-12

### Added
- **`--extra-dark`, `--extra-bias`, `--extra-flat` options** on `astronight sort` —
  equivalent to the extra calibration folder checkboxes in the GUI. Each accepts a
  folder path and merges frames of the expected type into the scan before grouping.
- **`astronight catalog update`** — downloads or refreshes the OpenNGC CSV from
  GitHub, equivalent to the "Update catalog" button in the GUI.
- **`astronight catalog status`** — shows catalog location, entry count, and last
  updated date.

---

## [0.2.0] — 2026-04-12

### Added
- **Object name resolution** via [OpenNGC](https://github.com/mattiaverga/OpenNGC) (MIT licence).
  Target names like `M 101`, `m101`, `NGC 5457` all resolve to `Pinwheel Galaxy` before
  grouping, so frames filed under different catalog designations for the same object are
  merged automatically. Original catalog IDs shown as a subtitle in the GUI groups table.
- **"Update catalog" button** in the GUI header — downloads/refreshes `~/.astronight/openngc.csv`
  from GitHub in the background.
- **Versioning**: `astronight.__version__` populated from package metadata; `--version` flag
  on the CLI; version shown in the GUI header.
- **`AstroNightOrganizer.bat`** Windows launcher — double-click to start, bypasses the Windows
  `python` → Microsoft Store stub issue by calling `uv run python run.py` directly.
- **`astronight/__init__.py`** exposing `__version__`.

### Changed
- **XISF master preference for darks and biases**: masters now always win over individual
  `.fit` files when a matching master exists, regardless of date. Previously the master
  only won when at least as recent as the best individual set. Rationale: a stacked master
  is always higher quality than the raw frames it was built from.
  Flats are unchanged — master preferred only when at least as recent as the best individual set.

---

## [0.1.0] — 2026-04-11

### Added
- **Core sorting pipeline**: scan ASIAIR FITS archive → group lights by target / camera /
  exposure / imaging night → match calibration frames → copy into WBPP folder structure.
- **FITS header parsing** (`fits_parser.py`): header-first with filename fallback. Supports
  all observed ASIAIR filename generations including 2026+ flat variants with rotator angle
  before or after the timestamp, with and without sensor temperature.
- **XISF master calibration frame support** (`xisf_parser.py`): reads PixInsight master
  darks, biases, and flats from `.xisf` files via the `xisf` package.
- **Scan cache** (`cache.py`): SQLite at `~/.astronight/scan_cache.db`, keyed by
  `(path, mtime, size)`. Schema auto-migrates on version bump — no manual intervention.
- **Calibration matching** (`calibration.py`):
  - Darks: camera + exposure + temperature setpoint (±5°C tolerance), closest imaging session
  - Biases: camera only (temperature-independent)
  - Flats: camera + imaging night
  - Session grouping: consecutive calendar dates (≤1 day apart) treated as one session,
    handles darks captured across midnight
  - Staleness warning when best match is >183 days from the light frame night
- **UTC vs local time handling**: `DATE-OBS` in ASIAIR headers is UTC; filenames embed local
  time. Night labels derived from filename timestamps when available; UTC offset auto-detected
  from the scan batch and applied to XISF/unrecognised files. Handles any timezone and DST.
- **NiceGUI browser GUI** (`gui.py`):
  - Folder pickers with native OS dialog (tkinter server-side)
  - Optional extra calibration folders per type (Darks / Biases / Flats), independently toggled
  - Groups tree view: one row per target, expandable for multi-night targets
  - Parent/child checkbox sync
  - Filters: target text, camera dropdown, year dropdown
  - Sort options: Target A→Z, Most recent, Most lights
  - Warnings collapsible pane
  - Dry run and Sort buttons with progress bar
  - Field persistence across sessions via `app.storage.general`
- **Click CLI** (`cli.py`): `sort`, `gui`, `cache stats`, `cache clean` commands with
  `--dry-run`, `--target`, `--no-cache`, `--yes`, `--log-file` options.
- **MD5 duplicate detection**: identical file → skip; different file → copy with `_dup1` suffix.
- **Night index** (`_INFO/night-index.csv`): tracks `NIGHT_N` numbers across multiple runs.
- **`run.py`** launcher: auto-installs dependencies on first run.
- **Test suite**: 133 tests covering filename parsing, FITS header extraction, UTC/local time,
  calibration matching, session grouping, duplicate handling, sort order, scan cache
  (schema migration, get/put/batch/clean), copy_frame collision handling, night index.

### Fixed
- Flat filename patterns for 2026+ ASIAIR firmware (angle and/or temp after timestamp).
- UTC night label for dawn flats: ASIAIR `DATE-OBS` is UTC; a 7:35 AM local flat had its
  night label calculated from the UTC hour (12:35), assigning it to the wrong imaging night.
- Dark matching returned only the single nearest calendar date's frames instead of all frames
  from the nearest imaging session, causing midnight-spanning dark sessions to return only half
  their frames.
- Lexicographic exposure sort: `300.0s` sorted before `60.0s` as a string — fixed to sort by
  float value.
- Extra calibration folders were scanned even when their checkbox was unchecked.
- `ScanCache` connection leaked in the CLI if `scan_fits()` raised an exception.
- Truncated FITS files emitted `AstropyUserWarning` to the console — now caught, reported in
  the errors list, and skipped cleanly.
- Bias temperature matching removed: biases at -20°C setpoint are valid for lights at -10°C.

---

[0.3.0]: https://github.com/TimBloom/Code/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/TimBloom/Code/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/TimBloom/Code/releases/tag/v0.1.0
