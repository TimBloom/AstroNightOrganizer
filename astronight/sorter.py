"""
Core sorting orchestration — groups frames and drives the copy loop.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .calibration import (
    NightCalib,
    build_bias_index,
    build_dark_index,
    build_flat_index,
    resolve_calibration,
)
from .catalog import resolve_name
from .file_ops import (
    copy_frame,
    init_night_folders,
    next_night_number,
    read_night_index,
    write_night_index,
)
from .fits_parser import FitsFrame, format_exposure


# ---------------------------------------------------------------------------
# Group key and resolved group
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LightGroupKey:
    """Unique identity of a group of light frames.

    Two light frames belong to the same group when all five fields match.
    ``target`` is the *resolved* canonical name (e.g. "Pinwheel Galaxy"),
    not the raw value from the FITS header.
    """
    target: str
    camera: str
    exposure_str: str   # formatted string, e.g. "300.0s" — used in folder names
    night_label: str    # imaging night in 'yyyy-mm-dd' (noon-to-noon, local time)
    set_temp: Optional[int]


@dataclass
class LightGroup:
    """A resolved group of light frames with their matched calibration frames.

    Produced by ``build_groups()`` and consumed by ``copy_groups()``.
    """
    key: LightGroupKey
    lights: list[FitsFrame]
    calib: NightCalib
    original_targets: list[str] = field(default_factory=list)
    """Raw target names from FITS headers/filenames that resolved to key.target.

    Only populated when catalog resolution changed the name — e.g. if frames
    were filed as 'M 101' and 'NGC 5457', both appear here while key.target
    is 'Pinwheel Galaxy'.  Used by the GUI to show original IDs as a subtitle.
    Empty when the raw name was already the canonical name.
    """


# ---------------------------------------------------------------------------
# Phase 1 — build groups (no I/O, no copying)
# ---------------------------------------------------------------------------

def build_groups(all_frames: list[FitsFrame]) -> list[LightGroup]:
    """Group light frames and resolve calibration for each group.

    No files are read or written — this is pure in-memory work.
    Returns groups sorted by target / night_label for stable display ordering.
    """
    lights = [f for f in all_frames if f.frame_type == 'Light']
    calibs = [f for f in all_frames if f.frame_type != 'Light']

    dark_index = build_dark_index(calibs)
    bias_index = build_bias_index(calibs)
    flat_index = build_flat_index(calibs)

    raw: dict[LightGroupKey, list[FitsFrame]] = defaultdict(list)
    # Track which raw names resolved to each key so we can show them in the UI
    raw_targets: dict[LightGroupKey, set[str]] = defaultdict(set)

    for lf in lights:
        raw_name = lf.target or 'Unknown'
        resolved = resolve_name(raw_name)
        key = LightGroupKey(
            target=resolved,
            camera=lf.camera,
            exposure_str=format_exposure(lf.exposure),
            night_label=lf.night_label,
            set_temp=lf.set_temp,
        )
        raw[key].append(lf)
        raw_targets[key].add(raw_name)

    groups = []
    for key, light_frames in raw.items():
        calib = resolve_calibration(
            night_label=key.night_label,
            camera=key.camera,
            exposure=light_frames[0].exposure,
            set_temp=key.set_temp,
            dark_index=dark_index,
            bias_index=bias_index,
            flat_index=flat_index,
        )
        # Original names that differ from the resolved canonical name
        originals = sorted(
            n for n in raw_targets[key] if n != key.target
        )
        groups.append(LightGroup(key=key, lights=light_frames, calib=calib,
                                 original_targets=originals))

    groups.sort(key=lambda g: (g.key.target, g.key.night_label, g.lights[0].exposure))
    return groups


# ---------------------------------------------------------------------------
# Phase 2 — copy selected groups
# ---------------------------------------------------------------------------

@dataclass
class SortResult:
    copied: int = 0
    skipped: int = 0
    renamed: int = 0
    errored: int = 0
    warnings: list[str] = field(default_factory=list)


def copy_groups(
    groups: list[LightGroup],
    dest_root: Path,
    dry_run: bool = False,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> SortResult:
    """Copy files for the given groups into *dest_root*.

    Output folder structure per group::

        dest_root/
        └── Target/
            └── Camera/
                └── Exposure/        ← subtree
                    └── NIGHT_N/
                        ├── Lights/
                        ├── Flats/
                        ├── Darks/
                        ├── Biases/
                        └── _INFO/

    The ``subtree`` (``Target/Camera/Exposure``) is the key used in the night
    index to track NIGHT_N numbers across multiple runs.  NIGHT_N increments
    each time a new night label is encountered for the same subtree.

    Args:
        groups:      Groups to copy, typically a user-selected subset of
                     what ``build_groups()`` returned.
        dest_root:   Root destination directory.  Created if it doesn't exist.
        dry_run:     If True, count files and log paths but copy nothing.
        progress_cb: Called as ``(done, total, filename)`` for each file.
        log_cb:      Called with human-readable log lines as they are produced.

    Returns:
        SortResult with counts of copied / skipped / renamed / errored files
        and any calibration warnings encountered.
    """

    def log(msg: str) -> None:
        if log_cb:
            log_cb(msg)

    result = SortResult()
    night_index = read_night_index(dest_root)
    night_path_cache: dict[str, Path] = {}

    # Count total files for progress
    total_files = sum(
        len(g.lights) + len(g.calib.darks) + len(g.calib.biases) + len(g.calib.flats)
        for g in groups
    )
    op_num = 0

    for group in groups:
        key = group.key
        subtree = f"{key.target}/{key.camera}/{key.exposure_str}"
        cache_key = f"{subtree}|{key.night_label}"

        if cache_key not in night_path_cache:
            n = next_night_number(dest_root, subtree, night_index)
            night_dir = dest_root / key.target / key.camera / key.exposure_str / f"NIGHT_{n}"
            night_path_cache[cache_key] = night_dir
            if not dry_run:
                init_night_folders(night_dir, ['Lights', 'Flats', 'Darks', 'Biases'])
            log(f"  -> {night_dir.relative_to(dest_root)}  [{key.night_label}]")

        night_dir = night_path_cache[cache_key]

        for w in group.calib.warnings:
            log(f"  [WARN] {w}")
            result.warnings.append(w)

        to_copy: list[tuple[FitsFrame, Path]] = [
            *((f, night_dir / 'Lights')  for f in group.lights),
            *((f, night_dir / 'Darks')   for f in group.calib.darks),
            *((f, night_dir / 'Biases')  for f in group.calib.biases),
            *((f, night_dir / 'Flats')   for f in group.calib.flats),
        ]

        if dry_run:
            result.copied += len(to_copy)
            continue

        for frame, dest_dir in to_copy:
            op_num += 1
            if progress_cb:
                progress_cb(op_num, total_files, frame.path.name)
            try:
                action, dest_path = copy_frame(frame.path, dest_dir)
                if action == 'copied':
                    result.copied += 1
                elif action == 'skipped':
                    result.skipped += 1
                elif action == 'renamed':
                    result.renamed += 1
                    log(f"  [DUP] {frame.path.name} -> {dest_path.name}")
            except Exception as exc:
                result.errored += 1
                log(f"  [ERROR] {frame.path.name}: {exc}")

    if not dry_run:
        write_night_index(dest_root, night_index)

    return result
