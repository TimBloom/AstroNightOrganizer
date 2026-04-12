# AstroNightOrganizer — Developer Notes for Claude

This file captures the full project context so new sessions can pick up immediately.

## What This Project Is

A Python tool that sorts ASIAIR astrophotography FITS files into the folder
hierarchy PixInsight's WBPP (Weighted Batch Pre-Processing) module expects.

```
Destination/
└── Target/
    └── Camera/
        └── Exposure/
            ├── NIGHT_1/
            │   ├── Lights/
            │   ├── Flats/
            │   ├── Darks/
            │   ├── Biases/
            │   └── _INFO/
            └── NIGHT_2/
```

## Project Layout

```
AstroNightOrganizer/
├── AstroNightOrganizer.bat   # Windows double-click launcher (uses uv)
├── AstroNightOrganizer.sh    # macOS/Linux double-click launcher (uses uv); needs chmod +x once
├── run.py                    # Cross-platform launcher — auto-installs deps, opens GUI
├── pyproject.toml            # Requires Python 3.13+
├── astronight/
│   ├── fits_parser.py        # FITS header reading + filename fallback parsing
│   ├── xisf_parser.py        # XISF master calibration frame reader (uses xisf package)
│   ├── cache.py              # SQLite scan cache (~/.astronight/scan_cache.db)
│   ├── catalog.py            # OpenNGC name resolution (common name lookup)
│   ├── file_ops.py           # scan_fits(), copy_frame(), night index helpers
│   ├── calibration.py        # Dark/bias/flat matching logic
│   ├── sorter.py             # build_groups() + copy_groups()
│   ├── cli.py                # Click CLI (astronight sort / gui / catalog / cache)
│   └── gui.py                # NiceGUI browser UI
└── tests/
    ├── test_fits_parser.py   # Filename parsing, night label, header extraction
    ├── test_calibration.py   # Calibration index building and matching
    ├── test_sorter.py        # build_groups() grouping, splitting, sort order
    ├── test_file_ops.py      # copy_frame(), night index, scan_fits()
    └── test_cache.py         # ScanCache get/put/batch/clean/schema migration
```

133 tests, all passing. Run with: `.venv/Scripts/python.exe -m pytest -v`

## Dependencies

```toml
astropy>=7.2.0    # FITS file reading
click>=8.3.1      # CLI
nicegui>=3.9.0    # Browser UI
rich>=14.3.3      # CLI progress/tables
xisf>=0.9.6       # XISF master calibration file reading
```

Dev: `pytest>=9.0.2`

## Key Design Decisions

### FITS Parsing (fits_parser.py)

- **Header-first**: FITS header is authoritative for all technical fields
  (frame type, exposure, gain, temperature, camera, timestamp)
- **Filename fallback**: used when a header field is absent — most commonly
  `OBJECT`/target in Autorun light frames
- **`FitsFrame.fn_timestamp`**: the local timestamp parsed from the filename
  (e.g. `20260321-073509` → `datetime(2026, 3, 21, 7, 35, 9)`). `None` for
  XISF masters and any file whose name didn't match a known pattern. Used by
  `scan_fits()` for UTC offset detection and night label correction.

#### UTC vs Local Time

**Critical**: `DATE-OBS` in ASIAIR FITS headers is stored in **UTC**. Filenames
embed **local time**. Verified across 412 flat files — offset is always exactly
+5h (CDT) or +6h (CST), zero exceptions.

Night label calculation uses the **filename timestamp** (`fn_timestamp`) when
available, falling back to the header UTC timestamp. This ensures dawn flats
taken at e.g. 7:35 AM local (12:35 PM UTC) are assigned to night `2026-03-20`
(the imaging night) rather than `2026-03-21` (the UTC calendar date).

For frames with no filename timestamp (XISF masters, unrecognised filenames),
`scan_fits()` auto-detects the session's UTC offset and patches `night_label`
via `_apply_utc_offset_correction()` — see File Scanning section below.

#### Supported Filename Formats

**Light frames** (3 generations):
```
Light_<Target>_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<Temp>C_<seq>.fit   # Full (recent)
Light_<Target>_<Exp>_Bin<N>_gain<G>_<TS>_<Temp>C_<seq>.fit             # Mid
Light_<Target>_<Exp>_Bin<N>_<TS>_<seq>.fit                              # Old
```

**Calibration frames** (Dark/Bias shared, Flat has extra variants):
```
<Type>_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<Temp>C_<seq>.fit            # Full
<Type>_<Exp>_Bin<N>_<TS>_<seq>.fit                                      # Minimal
Flat_<deg>deg_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<Temp>C_<seq>.fit     # Angle-first (2026+)
Flat_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<deg>deg_<seq>.fit             # Angle-late, no temp (2026+)
Flat_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<deg>deg_<Temp>C_<seq>.fit     # Angle-late + temp (2026+, confirmed in wild)
```

The angle-late+temp format (`Flat_33.3ms_Bin1_2600MC_gain100_20260321-073509_285deg_12.1C_0016.fit`)
is the currently-observed 2026 ASIAIR production format. Note there is no `SET-TEMP` header
in these files; `CCD-TEMP` holds the actual sensor temperature.

**Skip list** (`_SKIP_RE`): `Stacked*`, `Preview*`, `MasterFlat_*`, `MasterDark_*`,
`MasterBias_*`, `Plan_*` — these are skipped without error even if valid FITS.

**Temperature** in filename is a float *after* the timestamp (e.g. `-9.9C`).
**Gain** uses `gain` prefix. **Camera** comes before gain.

### Object Name Resolution (catalog.py)

- OpenNGC CSV stored at `~/.astronight/openngc.csv` (alongside scan cache)
- `download_openngc()` fetches from `mattiaverga/OpenNGC` on GitHub (CC-BY-SA-4.0)
- `load_catalog(force=False)` parses CSV into a normalised dict, cached in memory
- `resolve_name(raw)` normalises input and returns: common name → NGC/IC display
  name → raw unchanged
- **Normalisation**: strip/collapse whitespace, upper-case, remove leading zeros
  from numeric suffix, collapse `M 101` → `M101`, `NGC 5457` → `NGC5457`
- Graceful degradation: if CSV absent, every name passes through unchanged
- `build_groups()` in sorter.py calls `resolve_name()` on each light frame's
  target before building the `LightGroupKey`; `LightGroup.original_targets`
  stores any raw names that differed from the resolved canonical name
- GUI shows original catalog IDs as a small gray subtitle under the resolved
  target name, and has an "Update catalog" button in the header

### XISF Master Frames (xisf_parser.py)

- Uses the `xisf` PyPI package
- Reads FITSKeyword elements from the XISF XML header
- Recognises IMAGETYP values: "Master Dark", "Master Bias", "Master Flat",
  plus plain "dark", "bias", "flat" etc.
- Returns a standard `FitsFrame` with `fn_timestamp=None` (no filename local time)
- UTC offset correction applied by `scan_fits()` post-parse

### Calibration Matching (calibration.py)

- **Darks**: Camera + Exposure + SetTemp (±5°C tolerance via `DARK_TEMP_TOLERANCE`)
  then closest imaging session. Temperature tolerance means darks at -20°C setpoint
  match lights at -15°C (within 5°C), etc.
- **Biases**: Camera only — temperature intentionally excluded because bias is
  ADC zero-point offset and is temperature-independent
- **Flats**: Camera + NightLabel (same imaging night as the lights)
- **Session grouping**: `_group_sessions()` in `calibration.py` groups consecutive
  calendar dates (within 1 day of each other) into a single imaging session before
  matching. This handles darks captured across midnight (e.g. Apr 1 + Apr 2 = one
  session of 50 frames). Only the closest session is returned — not all sessions —
  to avoid mixing darks from different setups months apart.
- **XISF master preference**: `find_closest_calib()` accepts a `prefer_master`
  flag. When `True` (darks and biases), the XISF master always wins over
  individual `.fit` files when one exists — regardless of date. When `False`
  (flats), the master wins only when at least as recent as the best individual
  set. Rationale: a stacked master dark/bias is always higher quality than the
  raw frames it was built from; flat masters are night-specific so recency
  matters.
- Warning threshold: 183 days (6 months) for darks and biases
- "No darks found" warning includes tolerance: `@ -10°C (±5°C)`

### Night Grouping (fits_parser.py)

- Noon-to-noon: frames before 12:00 **local time** belong to the previous
  calendar day's night
- Lights and flats use `night_label` (imaging night, local time)
- Darks and biases use `calendar_date` (actual calendar date from UTC header,
  since they're typically taken during the day and the distinction doesn't matter)

### Scan Cache (cache.py)

- SQLite at `~/.astronight/scan_cache.db`
- Key: `(path, mtime, size)` — unchanged files are never re-opened
- **Schema version 3** — bumping `_SCHEMA_VERSION` wipes and rebuilds automatically
  on next open (no manual intervention needed)
- WAL mode + NORMAL synchronous for performance
- XISF files are NOT cached (there are usually only a handful)
- Stores `fn_timestamp` so UTC offset correction works correctly on cache hits
- The `ScanCache` must be created inside the executor thread, not on the main
  thread, to avoid SQLite threading errors

### File Scanning (file_ops.py)

- `scan_fits()` scans `*.fit` + `*.fits` recursively, then `*.xisf`
- Progress callback is throttled in the GUI (every 250ms) to avoid flooding
  the WebSocket with 21,000+ UI updates
- **`_apply_utc_offset_correction(frames)`**: runs after all files are parsed.
  Collects `(header_utc - fn_timestamp)` samples from all frames that have a
  filename timestamp, takes the mode (rounded to the nearest hour), then patches
  `night_label` and `calendar_date` on any frame with `fn_timestamp=None` (XISF
  masters, unrecognised filenames). Handles any timezone and DST automatically.

### Sorter (sorter.py)

- Two phases: `build_groups()` (pure in-memory) then `copy_groups()` (file I/O)
- Groups sorted by `(target, night_label, exposure_float)` — exposure sort is
  **numeric** (not lexicographic string sort, which would put 300s before 60s)
- `copy_frame()` uses MD5 to detect duplicates: same hash → skip, different → `_dup1`
- Night index in `_INFO/night-index.csv` tracks NIGHT_N across multiple runs

### GUI (gui.py)

- NiceGUI browser app, auto-opens on `http://127.0.0.1:8765`
- **Folder picker**: tkinter `filedialog.askdirectory()` server-side, run in
  executor thread to avoid blocking event loop
- **Scan runs in executor**: `ScanCache` created inside the thread function
- **Checkbox events**: NiceGUI uses `e.args` not `e.value` for `update:model-value`
- **Field persistence**: `app.storage.general` with `storage_secret='astronight-local'`
  saves source, destination, extra calib folders between sessions
- **Groups UI**: tree-view grouped by target; multi-night targets have an
  expand/collapse chevron. Parent checkbox selects/deselects all children.
  Children update parent: all checked → parent checked, none → parent unchecked.
- **Filters**: Target text input, Camera dropdown, Year dropdown, Sort dropdown
  — all live-filter the groups table without rescanning. Dropdowns populated
  from scan results via `.options = [...]` + `.update()`
- **Sort options**: "Target A→Z" (default), "Most recent" (latest night first),
  "Most lights" (highest total frame count first)
- **Extra calib scan gate**: extra calib folders are only scanned when their
  checkbox is checked AND a path is set. Checkbox state is checked at scan time,
  not just at UI time.
- **Progress bars**: `show_value=False` + `instant-feedback` prop; percentage
  shown in status label instead
- **Warnings**: collapsible `ui.expansion` pane below the groups table

## Common Gotchas

- `select.options = [...]` requires a follow-up `.update()` call in NiceGUI
  for the browser to receive the new option list
- The scan callback fires for every file including cache hits — throttle it
  or the WebSocket gets overwhelmed on large archives (21k+ files)
- Bias temperature matching was intentionally removed — biases at -20°C setpoint
  are valid for lights at -10°C. Only darks need temperature matching.
- `DATE-OBS` is always UTC in ASIAIR files; filenames are always local time.
  Never use the header timestamp alone for night label calculation.
- `next_night_number()` accepts a `dest_root` parameter but never uses it
  (dead parameter — left as-is to avoid API churn)
- Schema version bump forces a full re-parse on next run — this is intentional
  and automatic; users don't need to run `cache clean` manually

## Changes (for reference)

- **Extra calib scanned when disabled**: GUI checked input text but not checkbox
  state — fixed to require both `calib_enabled[ft].value` and a path
- **Lexicographic exposure sort**: `300.0s` sorted before `60.0s` as a string —
  fixed to sort by raw float value in `build_groups()`
- **Dark matching returned only one date's frames**: `_closest_in` returned only
  the single nearest calendar date bucket — fixed via `_group_sessions()` which
  merges consecutive dates into sessions before picking the closest one
- **ScanCache leak in CLI**: connection not closed if `scan_fits()` raised —
  wrapped in `try/finally`
- **Dead `dry_run` param on `copy_frame()`**: parameter was never passed `True`
  by callers; removed
- **Missing flat filename patterns**: 2026 ASIAIR now writes angle and/or temp
  after the timestamp rather than before — three variants now handled
- **UTC night label for dawn flats**: ASIAIR `DATE-OBS` is UTC; a 7:35 AM local
  flat has `DATE-OBS` at 12:35 UTC (hour ≥ 12), which previously assigned it to
  the wrong imaging night — fixed via filename-first night label + UTC offset
  correction for XISF/unrecognised files
- **Master dark/bias preference unconditional**: previously the XISF master was
  only preferred when at least as recent as the best individual set — changed so
  the master always wins for darks and biases when one exists. Flats still use
  the recency-based preference since flat calibration is night-specific.
- **Object name resolution**: added `catalog.py` using OpenNGC (CC-BY-SA-4.0). Targets
  like `M 101`, `m101`, `NGC 5457` are resolved to `Pinwheel Galaxy` before
  grouping, so frames with different catalog designations for the same object
  are merged into one group. Original names stored on `LightGroup.original_targets`
  and shown as a subtitle in the GUI. "Update catalog" button in the header
  downloads/refreshes `~/.astronight/openngc.csv`.
- **Windows launcher**: added `AstroNightOrganizer.bat` — double-click to start; uses
  `uv run python run.py` to bypass Windows `python` → Microsoft Store stub issue.
- **macOS/Linux launcher**: added `AstroNightOrganizer.sh` — equivalent shell script;
  requires `chmod +x` once before first use.

## Potential Next Steps

- Add logging of which calibration was chosen (master vs individual) in dry-run output
- Test a real full copy (not dry run) on the actual archive
- Add `--since`/`--before` date filter flags to CLI
- Decide on calibration copy strategy: copy darks/biases per-night (current) vs
  pointing WBPP at the archive directly or a shared calibration library folder
