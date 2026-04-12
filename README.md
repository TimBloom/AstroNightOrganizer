# AstroNightOrganizer

**Organise astrophotography FITS files into a WBPP-compatible folder structure for PixInsight.**

AstroNightOrganizer automatically organizes FITS dumps into the hierarchical layout that PixInsight's Weighted Batch Pre-Processing (WBPP) module expects, making it effortless to match calibration frames to light frames by camera, exposure, temperature, and observation date. It also resolves catalog names (M 101, NGC 5457) to common names (Pinwheel Galaxy) so targets captured under different designations are merged automatically.

---

## What It Does

When you dump files from ASIAIR, everything lands in a flat directory — hundreds of light frames, darks, biases, and flats all mixed together. WBPP in PixInsight needs a specific folder structure to automatically match calibration frames to the lights they belong to:

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
                └── ...
```

AstroNightOrganizer does this automatically by:

1. Reading FITS headers to extract frame type, exposure, gain, temperature, camera, and timestamp
2. Resolving target names to canonical common names using the OpenNGC catalog — so `M 101`, `M101`, and `NGC 5457` all become `Pinwheel Galaxy` and land in the same group
3. Grouping lights by target, camera, exposure, and imaging night (noon-to-noon)
4. Matching calibration frames intelligently — darks matched by temperature (±5°C tolerance) and date, biases by camera and date, flats by camera and night
5. Preferring PixInsight XISF master calibration frames: masters always win for darks and biases; for flats, masters win when at least as recent as the best individual set
6. Copying everything into the right place with duplicate detection
7. Caching parsed metadata for fast repeated runs

---

## Quick Start

### 1. Download

Click **Code → Download ZIP** on this page and extract it, or clone with git:

```bash
git clone https://github.com/TimBloom/AstroNightOrganizer.git
cd AstroNightOrganizer
```

### 2. Install `uv`

AstroNightOrganizer uses [`uv`](https://docs.astral.sh/uv/) to manage Python and dependencies automatically. Install it once:

| Platform | Command |
|---|---|
| Windows | `winget install astral-sh.uv` |
| macOS / Linux | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

> **Requires Python 3.13+.** `uv` will download and install the right Python version for you if it isn't already present.

### 3. Run

**Windows** — double-click **`AstroNightOrganizer.bat`**, or from a terminal:

```powershell
.\AstroNightOrganizer.bat
```

**macOS / Linux** — make the launcher executable once, then run it:

```bash
chmod +x AstroNightOrganizer.sh
./AstroNightOrganizer.sh
```

All Python dependencies are installed automatically on the first run. The app opens in your default browser at `http://127.0.0.1:8765`.

> **Linux users:** the GUI folder picker requires `tkinter`, which is not always bundled with Python. Install it with `sudo apt install python3-tk` (Debian/Ubuntu) or the equivalent for your distro.

> **Platform note:** AstroNightOrganizer has been tested on Windows 11. The macOS and Linux launchers are included and should work, but have not yet been tested on those platforms. Reports of success or issues are welcome.

---

## GUI

The graphical interface runs as a local web app and opens automatically in your default browser.

### Step 1 — Choose Folders

- **Source folder**: your ASIAIR archive root (scanned recursively)
- **Destination folder**: where sorted files will be copied
- **Extra calibration folders** *(optional)*: if your darks, biases, or flats live in separate folders (e.g. a dedicated calibration library drive), enable each type independently and point it at the right folder

All folder paths are remembered between sessions — you won't need to re-enter them next time.

### Step 2 — Scan

Click **Scan** to recursively read all `.fit`/`.fits` files. FITS headers are parsed first; filenames are used as fallback only. All frames are always scanned in full — use the Target/Camera/Year filters in Step 3 to narrow what you see after the scan. The scan cache means unchanged files are served from a local SQLite database rather than re-opened, so subsequent scans of the same archive are dramatically faster.

### Step 3 — Select Groups

After scanning, light frames are grouped by target, camera, exposure, and imaging night. Target names are automatically resolved to common names using the OpenNGC catalog — frames filed under `M 101`, `NGC 5457`, or `Pinwheel Galaxy` all appear under **Pinwheel Galaxy**. Original catalog IDs are shown as a subtitle when they differ from the resolved name. The results are displayed in a tree:

- **One row per target** — shows the total light/calibration frame counts across all nights
- Targets with multiple nights show a **▶ expand** chevron — click it to see individual nights
- **Check/uncheck a target** to select or deselect all nights under it
- **Check/uncheck individual nights** after expanding — the parent checkbox updates automatically
- Use **All** / **None** to select or clear everything at once
- Warning indicators (⚠) flag groups whose best calibration match is more than 6 months old

**Filters and sorting** — live-filter and re-sort the groups table without rescanning:

- **Target**: type any partial name (e.g. `NGC`, `M4`) — matches are applied instantly as you type
- **Camera**: dropdown populated from your scan results
- **Year**: dropdown populated from your scan results, newest first
- **Sort by**: order the results by Target A→Z (default), Most recent (latest night first), or Most lights (highest frame count first)

### Step 4 — Sort

- **Dry run**: logs what would be copied for each selected group without touching any files — always worth doing first
- **Sort selected**: copies files into the WBPP folder structure

Progress is shown as a percentage. A log panel appears below and a timestamped log file is written to `DESTINATION/_IMPORT_LOGS/`.

---

## CLI

For scripted or headless use, the full feature set is also available on the command line.

### Basic Sort

```powershell
astronight sort "D:\ASIAIR\Archive" "D:\WBPPImport"
```

Scans the source recursively, groups lights, matches calibration frames, prompts you to select which groups to process, then copies everything.

### Dry Run (Preview Without Copying)

```powershell
astronight sort "D:\ASIAIR\Archive" "D:\WBPPImport" --dry-run
```

Shows what would happen — groups found, warnings, file counts — without touching any files.

### Filter by Target

```powershell
astronight sort "D:\ASIAIR\Archive" "D:\WBPPImport" --target "M42"
```

Only processes light frames whose target name contains `M42` (case-insensitive). Calibration frames are always included.

### Skip Confirmation Prompt

```powershell
astronight sort "D:\ASIAIR\Archive" "D:\WBPPImport" --yes
```

Processes all groups without prompting for selection.

### Extra Calibration Folders

```powershell
astronight sort "D:\ASIAIR\Archive" "D:\WBPPImport" `
    --extra-dark  "D:\CalibLibrary\Darks" `
    --extra-bias  "D:\CalibLibrary\Biases" `
    --extra-flat  "D:\CalibLibrary\Flats"
```

Each option accepts a folder path and merges frames of the expected type into the scan before grouping. Equivalent to the extra calibration folder checkboxes in the GUI. Any combination of the three can be used.

### Skip the Cache

```powershell
astronight sort "D:\ASIAIR\Archive" "D:\WBPPImport" --no-cache
```

Forces a full re-parse of every FITS file. Use this if you suspect the cache is stale or for debugging.

### Custom Log File

```powershell
astronight sort "D:\ASIAIR\Archive" "D:\WBPPImport" --log-file "C:\Logs\sort.log"
```

By default a timestamped log is written to `DESTINATION/_IMPORT_LOGS/import-<stamp>.log`.

### Launch the GUI from CLI

```powershell
astronight gui
astronight gui --port 9000
```

### Catalog Management

```powershell
# Download or refresh the OpenNGC object name catalog
astronight catalog update

# Show catalog location, entry count, and last updated date
astronight catalog status
```

### Cache Management

```powershell
# Show how many entries are cached and where the database lives
astronight cache stats

# Remove entries for files that no longer exist on disk
astronight cache clean
```

---

## Installation (Manual)

If you prefer not to use `run.py`, you can install manually:

```powershell
# With uv (recommended)
uv pip install -e .

# Or with pip
pip install -e .
```

Then run the CLI with `astronight --help` or the GUI with `astronight gui`.

---

## How It Works

### 1. FITS Header Reading (Authoritative Source)

Every `.fit`/`.fits` file is opened and its primary HDU header is parsed. The following fields are read from the header and treated as authoritative:

| Header field | Used for |
|---|---|
| `IMAGETYP` | Frame type (Light / Flat / Dark / Bias) |
| `EXPOSURE` / `EXPTIME` | Exposure duration in seconds |
| `XBINNING` | Binning |
| `GAIN` | Gain value |
| `SET-TEMP` | Temperature setpoint (°C) for calibration matching |
| `INSTRUME` | Camera model, normalised to short name (e.g. `2600MC`) |
| `OBJECT` | Target name (present in Live lights; sometimes absent in Autorun) |
| `DATE-OBS` | Precise ISO timestamp (stored in **UTC** by ASIAIR firmware) |
| `ROTATOR` | Rotator angle in degrees (2026+ files) |

### 2. Filename Parsing and Timezone Handling

ASIAIR filenames embed the **local time** of the imaging session (e.g. `20260321-073509` = 7:35 AM local), while `DATE-OBS` in the header is always **UTC**. AstroNightOrganizer uses the filename timestamp specifically for night label calculation — so dawn flats taken at 7:35 AM local are correctly assigned to the previous evening's imaging night, not to the UTC calendar date.

If a required field is missing from the header (most commonly `OBJECT`/target in Autorun light frames), the filename is also used as a fallback for that field.

For files with no filename timestamp — XISF master calibration frames and any unrecognised filename patterns — AstroNightOrganizer auto-detects the session's UTC offset from the rest of the scan batch and applies it automatically. This handles any timezone and DST transitions without any configuration.

### 3. Noon-to-Noon Night Grouping

Light frames and flats are grouped by **imaging night**, defined as noon-to-noon **local time**. If you imaged from 22:00 on Jan 15 through 06:00 on Jan 16, all those frames belong to the `2025-01-15` imaging night.

Darks and biases use their plain calendar date for matching. This avoids confusion when calibration frames are taken during the day and straddle the noon boundary.

### 4. Calibration Matching

For each group of lights, the best available calibration frames are found:

- **Darks**: matched by camera + exposure + temperature setpoint (±5°C tolerance), then closest calendar date
- **Biases**: matched by camera only — temperature is intentionally excluded because bias is the ADC zero-point offset and does not vary with sensor temperature
- **Flats**: matched by camera + imaging night (same night as the lights)

If the best dark or bias match is more than **183 days** (~6 months) away, a warning is shown. Calibration frames are physically copied into each target's `NIGHT_N` folder so every folder is self-contained for WBPP.

### 4a. XISF Master Calibration Frames

If PixInsight master calibration files (`.xisf`) are present in the source or extra calibration folders, they are automatically detected and preferred:

- **Darks and biases**: the XISF master is **always preferred** over individual `.fit` files when a matching master exists. A stacked master is a higher quality input than the raw frames it was built from, regardless of which is more recent.
- **Flats**: the master is preferred only when it is at least as recent as the best individual set, since flat calibration is night-specific.

IMAGETYP values recognised in XISF headers: `Master Dark`, `dark`, `dark frame`, `Master Bias`, `bias`, `bias frame`, `Master Zero`, `zero`, `Master Flat`, `flat`, `flat field`, `Master Flat Field`.

### 5. Object Name Resolution

Before grouping, each light frame's target name is resolved against the [OpenNGC](https://github.com/mattiaverga/OpenNGC) catalog (CC-BY-SA-4.0). The resolution priority is:

1. **Common name** — e.g. `Pinwheel Galaxy`
2. **Primary NGC/IC designation** — e.g. `NGC 5457`
3. **Original name as-is** — used when no catalog match is found

Lookup is case- and space-insensitive: `M 101`, `m101`, `M101`, and `NGC5457` all resolve to the same entry. The `LightGroup` retains the original raw names so the GUI can show them as a subtitle.

The OpenNGC CSV is stored at `~/.astronight/openngc.csv`. Use the **Update catalog** button in the GUI header to download or refresh it. Until the catalog is downloaded the tool works fine — unresolved names are used as-is.

### 6. Duplicate Handling

Before copying, MD5 hashes are compared between source and destination:

- **Same hash** → file is skipped (already present)
- **Different hash** → file is copied with a `_dup1`, `_dup2`, etc. suffix

### 7. Night Index

AstroNightOrganizer maintains a `_INFO/night-index.csv` in the destination root to track which `NIGHT_N` numbers have been used per subtree. You can run the tool in multiple batches and night numbers will increment correctly.

### 8. Scan Cache

Parsed frame metadata is cached in an SQLite database:

- **Windows**: `%USERPROFILE%/.astronight/scan_cache.db`
- **macOS/Linux**: `~/.astronight/scan_cache.db`

The cache key is `(absolute_path, mtime, size)`. Unchanged files are served from cache without re-opening the FITS file. When the internal cache schema is updated (e.g. after a software upgrade), it is rebuilt automatically on the next scan — no manual action needed. Use `astronight cache clean` to remove stale entries for files that have been deleted from disk.

---

## Supported Filename Formats

### Light Frames

| Generation | Format | Example |
|---|---|---|
| Full (recent) | `Light_<Target>_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<Temp>C_<seq>.fit` | `Light_M42_300.0s_Bin1_2600MC_gain100_20250115-210530_-15.5C_0001.fit` |
| Mid | `Light_<Target>_<Exp>_Bin<N>_gain<G>_<TS>_<Temp>C_<seq>.fit` | `Light_M8_300.0s_Bin1_gain252_20240701-224722_-9.9C_0001.fit` |
| Old | `Light_<Target>_<Exp>_Bin<N>_<TS>_<seq>.fit` | `Light_NGC6960_300.0s_Bin1_20240624-035218_0001.fit` |

Multi-word targets (`LBN 437`, `NGC 6960`), comet designations (`C-2025 R2`), and special characters in target names are handled correctly.

### Calibration Frames

| Format | Example |
|---|---|
| `<Type>_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<Temp>C_<seq>.fit` | `Dark_300.0s_Bin1_2600MC_gain100_20241001-140000_-10.0C_0001.fit` |
| `<Type>_<Exp>_Bin<N>_<TS>_<seq>.fit` | `Dark_300.0s_Bin1_20241001-140000_0001.fit` |
| Flat — angle before exposure (2026+) | `Flat_106deg_33.3ms_Bin1_2600MC_gain100_20260120-073101_-3.0C_0001.fit` |
| Flat — angle after timestamp, no temp (2026+) | `Flat_33.3ms_Bin1_2600MC_gain100_20260320-061616_286deg_1316.fit` |
| Flat — angle and temp after timestamp (2026+) | `Flat_33.3ms_Bin1_2600MC_gain100_20260321-073509_285deg_12.1C_0016.fit` |

### Automatically Skipped

Files with these prefixes are skipped without error:

- `Stacked*` — stacked outputs
- `Preview*` — ASIAIR preview frames
- `MasterFlat_*`, `MasterDark_*`, `MasterBias_*` — pre-built master calibration frames
- `Plan_*` — ASIAIR imaging plans

---

## Output Structure Example

**Source:**
```
ASIAIR Data Archive/
├── Autorun/
│   ├── Light/M42/
│   │   ├── Light_M42_300.0s_Bin1_2600MC_gain100_20250115-210530_-15.5C_0001.fit
│   │   └── Light_M42_300.0s_Bin1_2600MC_gain100_20250115-210845_-15.5C_0002.fit
│   ├── Dark/
│   │   └── Dark_300.0s_Bin1_2600MC_gain100_20250114-020000_-15.0C_0001.fit
│   ├── Bias/
│   │   └── Bias_1.0ms_Bin1_2600MC_gain100_20250114-015900_-15.0C_0001.fit
│   └── Flat/
│       └── Flat_2.5s_Bin1_2600MC_gain100_20250115-183000_-15.0C_0001.fit
```

**After `astronight sort` (or GUI Sort):**
```
WBPPImport/
├── M42/
│   └── 2600MC/
│       └── 300.0s/
│           └── NIGHT_1/
│               ├── Lights/
│               │   ├── Light_M42_300.0s_..._0001.fit
│               │   └── Light_M42_300.0s_..._0002.fit
│               ├── Flats/
│               │   └── Flat_2.5s_..._0001.fit
│               ├── Darks/
│               │   └── Dark_300.0s_..._0001.fit
│               ├── Biases/
│               │   └── Bias_1.0ms_..._0001.fit
│               └── _INFO/
└── _IMPORT_LOGS/
    └── import-20250117-153045.log
```

Open `M42/2600MC/300.0s/NIGHT_1/` in PixInsight WBPP and it will automatically load lights and match all calibration frames by folder.

---

## Running Tests

```powershell
uv run pytest -v
```

Tests cover filename parsing (all generations and flat variants), FITS header extraction, UTC vs local time / night-label logic, calibration matching, duplicate handling, skip-list filtering, sort order, the scan cache (schema migration, get/put/batch/clean), copy_frame collision handling, and the night index.

---

## FAQ

**What if my target name is in the FITS header but not the filename?**  
The header is authoritative. `OBJECT` is used when present; the filename is only parsed as a fallback if the header lacks the field.

**Can I sort new frames into a destination folder I've already sorted into before?**  
Yes. The night index ensures `NIGHT_N` numbers increment correctly across runs, so new nights get the next available number rather than overwriting existing ones. Duplicate files are detected by MD5 and skipped automatically, so re-sorting the same source frames is safe.

**How do I know if my calibration frames are too old?**  
A warning (⚠) is shown in the GUI groups table and written to the log whenever the best-matching dark or bias set is more than 183 days from the light frame night.

**I image from different locations / timezones. Will night grouping still be correct?**  
Yes. AstroNightOrganizer auto-detects the UTC offset from your files' own timestamps each scan — no configuration needed. Whether you're imaging from home in CDT (+5h) or a dark site in a different timezone, the correct local night is inferred automatically.

**Can I sort files on a network drive (like a NAS)?**  
Yes. The scan cache is especially useful here — on subsequent runs, unchanged files on the NAS are served from the local SQLite cache without any network reads.

**What if I image multiple targets in one night?**  
Each target gets its own subtree. Calibration frames are copied into each target's `NIGHT_N` folder independently so every folder is self-contained for WBPP.

**What if there are no matching flats for a night?**  
A warning is logged and the `Flats/` folder is created but left empty. The sort continues normally.

**My target names don't resolve — I used custom names like "My_Nebula".**  
Unrecognised names pass through unchanged and are used as the group name. The catalog only covers objects in the NGC/IC/Messier catalogs.

**I have a master dark XISF from six months ago and fresh individual darks. Which will be used?**  
The master. For darks and biases, AstroNightOrganizer always prefers an XISF master over individual `.fit` frames when a matching master exists — a stacked master is higher quality than raw frames regardless of age. If you want the individual frames used, remove or move the master XISF from your calibration folders.

**My calibration library is in a separate folder — can AstroNightOrganizer find it?**  
Yes. In the GUI, expand the **Extra calibration folders** section and enable each type (Darks, Biases, Flats) independently, pointing each to its own folder. Those frames are merged with the source scan before grouping.

---

## Uninstalling

AstroNightOrganizer stores files in two places:

**1. The project folder** — contains the source code and the Python virtual environment created by `uv`. Delete the entire folder to remove the app:

```powershell
# Windows
Remove-Item -Recurse -Force "C:\path\to\AstroNightOrganizer"
```

```bash
# macOS / Linux
rm -rf /path/to/AstroNightOrganizer
```

**2. User data folder** — the scan cache and OpenNGC catalog are stored outside the project folder and must be removed separately if you want a clean uninstall:

```powershell
# Windows
Remove-Item -Recurse -Force "$env:USERPROFILE\.astronight"
```

```bash
# macOS / Linux
rm -rf ~/.astronight
```

That's everything — AstroNightOrganizer does not write to the registry, system directories, or anywhere else.

---

## License

[MIT License](LICENSE) — Copyright (c) 2026 Tim Bloom
