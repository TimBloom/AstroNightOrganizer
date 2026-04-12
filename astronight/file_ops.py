"""
File scanning, MD5 collision detection, and copy/move operations.
"""

from __future__ import annotations

import csv
import hashlib
import shutil
from pathlib import Path
from typing import Optional

from .fits_parser import FitsFrame, format_exposure, imaging_night, parse_frame, read_fits_header
from .xisf_parser import parse_xisf_frame


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_fits(
    root: Path,
    progress_callback=None,
    cache=None,
) -> tuple[list[FitsFrame], list[tuple[Path, Exception]]]:
    """Recursively scan *root* for .fit/.fits files and parse each one.

    If *cache* (a ScanCache instance) is provided, parsed results are stored
    and retrieved by (path, mtime, size) so unchanged files are not re-opened.

    *progress_callback(scanned, total, filename)* is called for every file.
    Returns (frames, errors).
    """
    paths = list(root.rglob('*.fit')) + list(root.rglob('*.fits'))
    total = len(paths)
    frames: list[FitsFrame] = []
    errors: list[tuple[Path, Exception]] = []
    cache_batch: list[tuple[FitsFrame, float, int]] = []

    for i, path in enumerate(paths, 1):
        if progress_callback:
            progress_callback(i, total, path.name)

        try:
            stat = path.stat()
            mtime = stat.st_mtime
            size  = stat.st_size
        except OSError as exc:
            errors.append((path, exc))
            continue

        # --- Cache lookup ---
        if cache is not None:
            cached = cache.get(path, mtime, size)
            if cached is not None:
                frames.append(cached)
                continue

        # --- Cache miss: parse the file ---
        try:
            header = read_fits_header(path)
        except Warning as exc:
            # Truncated or otherwise flagged by astropy — report and skip
            errors.append((path, exc))
            continue
        except Exception:
            header = None
        try:
            frame = parse_frame(path, header)
            if frame is not None:
                frames.append(frame)
                if cache is not None:
                    cache_batch.append((frame, mtime, size))
        except Exception as exc:
            errors.append((path, exc))

    # Flush new entries to cache in one transaction
    if cache is not None and cache_batch:
        cache.put_batch(cache_batch)

    # --- XISF master calibration files (not cached — typically very few) ---
    for xp in root.rglob('*.xisf'):
        try:
            frame = parse_xisf_frame(xp)
            if frame is not None:
                frames.append(frame)
        except Exception as exc:
            errors.append((xp, exc))

    # --- UTC offset correction ---
    # DATE-OBS in ASIAIR FITS/XISF headers is UTC. Filenames use local time.
    # Detect the local UTC offset from frames that have a filename timestamp and
    # apply it to correct night_label on frames that only have a UTC header timestamp
    # (XISF masters, unrecognised filename patterns). Handles any timezone and DST.
    _apply_utc_offset_correction(frames)

    return frames, errors


def _apply_utc_offset_correction(frames: list[FitsFrame]) -> None:
    """Detect the session's UTC→local offset and patch night_label on frames
    that only have a UTC header timestamp (fn_timestamp is None).

    Background: ASIAIR stores DATE-OBS in UTC but embeds local time in
    filenames.  Most frames can derive their correct local night label from
    the filename timestamp directly.  XISF master calibration frames and any
    file whose name didn't match a known pattern have fn_timestamp=None and
    so their night_label was initially computed from the UTC header time —
    which can be wrong by several hours, particularly for dawn frames.

    Strategy:
      1. For every frame that has a filename timestamp, compute
         offset = round(header_utc − fn_timestamp) to the nearest hour.
      2. Take the mode of those samples.  Using the mode rather than mean
         handles DST transitions within a single scan batch where some frames
         may have a +5h offset and others +6h.
      3. For frames with fn_timestamp=None, recompute night_label and
         calendar_date using local_ts = header_utc − modal_offset.

    If the batch contains no filename-derived frames (e.g. an XISF-only extra
    calibration folder), night_labels are left as UTC-derived — better than
    applying a random guess.

    This function mutates the FitsFrame objects in place.
    """
    from collections import Counter
    from datetime import timedelta

    # Collect integer-hour offset samples
    offset_counter: Counter = Counter()
    for f in frames:
        if f.fn_timestamp is None:
            continue
        delta_h = (f.timestamp - f.fn_timestamp).total_seconds() / 3600
        # Round to nearest whole hour — filename ts has 1-second precision vs
        # header ts which has sub-second precision, so difference is always ~0s
        offset_counter[round(delta_h)] += 1

    if not offset_counter:
        return  # No filename-derived frames; can't detect offset

    modal_offset = offset_counter.most_common(1)[0][0]  # e.g. 5 or 6

    # Patch frames whose night_label came from UTC header
    for f in frames:
        if f.fn_timestamp is not None:
            continue  # Already correct
        local_ts = f.timestamp - timedelta(hours=modal_offset)
        f.night_label    = imaging_night(local_ts)
        f.calendar_date  = local_ts.strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# Night index (NIGHT_N tracking)
# ---------------------------------------------------------------------------

NIGHT_INDEX_FILE = '_INFO/night-index.csv'


def read_night_index(dest_root: Path) -> dict[str, int]:
    """Load subtree→night_number mapping from CSV.

    Key format: 'Target/Camera/Exposure'  Value: last used NIGHT_N integer.
    """
    index_path = dest_root / NIGHT_INDEX_FILE
    result: dict[str, int] = {}
    if not index_path.exists():
        return result
    with index_path.open(newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            try:
                result[row['subtree']] = int(row['night_number'])
            except (KeyError, ValueError):
                pass
    return result


def write_night_index(dest_root: Path, index: dict[str, int]) -> None:
    index_path = dest_root / NIGHT_INDEX_FILE
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['subtree', 'night_number'])
        writer.writeheader()
        for subtree, number in sorted(index.items()):
            writer.writerow({'subtree': subtree, 'night_number': number})


def next_night_number(dest_root: Path, subtree: str, index: dict[str, int]) -> int:
    """Return the next NIGHT_N integer for *subtree*, updating *index* in place."""
    current = index.get(subtree, 0)
    next_n = current + 1
    index[subtree] = next_n
    return next_n


# ---------------------------------------------------------------------------
# MD5 helpers
# ---------------------------------------------------------------------------

def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Copy with collision handling
# ---------------------------------------------------------------------------

def copy_frame(src: Path, dest_dir: Path) -> tuple[str, Optional[Path]]:
    """Copy *src* into *dest_dir*, handling MD5 collisions.

    Returns:
        ('copied', dest_path)   — file was copied
        ('skipped', dest_path)  — identical file already exists
        ('renamed', dest_path)  — different file existed; copied with _dup suffix
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name

    if dest.exists():
        if md5(src) == md5(dest):
            return ('skipped', dest)
        # Find a free _dupN name
        stem = src.stem
        suffix = src.suffix
        for n in range(1, 10001):
            candidate = dest_dir / f"{stem}_dup{n}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
        else:
            raise RuntimeError(
                f"Could not find a free duplicate name for {src.name} in {dest_dir} "
                f"after 10,000 attempts"
            )
        shutil.copy2(src, dest)
        return ('renamed', dest)

    shutil.copy2(src, dest)
    return ('copied', dest)


# ---------------------------------------------------------------------------
# Night folder initialisation
# ---------------------------------------------------------------------------

def init_night_folders(night_dir: Path, frame_types: list[str]) -> None:
    """Create the standard sub-folder layout inside a NIGHT_N directory.

    Creates one sub-folder for each entry in *frame_types* plus an '_INFO'
    folder for metadata (e.g. night-index.csv).  Folders are created with
    parents=True so the full NIGHT_N path is initialised in one call.

    Args:
        night_dir:    Path to the NIGHT_N directory to initialise.
        frame_types:  Sub-folder names to create, typically
                      ['Lights', 'Flats', 'Darks', 'Biases'].
    """
    for sub in frame_types + ['_INFO']:
        (night_dir / sub).mkdir(parents=True, exist_ok=True)
