"""
Calibration frame matching logic.

Darks  → matched by Camera + Exposure + SetTemp, then closest calendar date.
Biases → matched by Camera only (bias is temperature-independent — it is the
         ADC zero-point offset and does not vary with sensor temperature),
         then closest calendar date.
Flats  → matched by Camera + NightLabel (same imaging night as the lights).

A warning is emitted when the best calibration match is more than 183 days
(~6 months) from the light frame night.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .fits_parser import FitsFrame, format_exposure

CALIB_WARNING_DAYS = 183
DARK_TEMP_TOLERANCE = 5   # °C — darks within this many degrees are considered usable


# ---------------------------------------------------------------------------
# Calibration index structures
# ---------------------------------------------------------------------------

def build_dark_index(frames: list[FitsFrame]) -> dict:
    """
    Returns:
        {
          "Camera|Exposure|SetTemp": {
              "yyyy-MM-dd": [FitsFrame, ...]
          }
        }
    """
    index: dict = {}
    for f in frames:
        if f.frame_type != 'Dark':
            continue
        exp_str = format_exposure(f.exposure)
        temp_str = str(f.set_temp) if f.set_temp is not None else 'NoTemp'
        key = f"{f.camera}|{exp_str}|{temp_str}"
        index.setdefault(key, {}).setdefault(f.calendar_date, []).append(f)
    return index


def build_bias_index(frames: list[FitsFrame]) -> dict:
    """
    Returns:
        {
          "Camera": {
              "yyyy-MM-dd": [FitsFrame, ...]
          }
        }

    Bias frames are keyed by camera only — temperature is intentionally excluded
    because bias is the ADC zero-point offset and does not vary with temperature.
    """
    index: dict = {}
    for f in frames:
        if f.frame_type != 'Bias':
            continue
        index.setdefault(f.camera, {}).setdefault(f.calendar_date, []).append(f)
    return index


def build_flat_index(frames: list[FitsFrame]) -> dict:
    """
    Returns:
        {
          "Camera|NightLabel": [FitsFrame, ...]
        }
    """
    index: dict = {}
    for f in frames:
        if f.frame_type != 'Flat':
            continue
        key = f"{f.camera}|{f.night_label}"
        index.setdefault(key, []).append(f)
    return index


# ---------------------------------------------------------------------------
# Closest-date lookup
# ---------------------------------------------------------------------------

@dataclass
class CalibMatch:
    date_label: str          # closest date, used for staleness warning
    frames: list[FitsFrame]  # ALL frames from ALL matching dates
    days_delta: int          # distance from closest date, used for staleness warning
    is_master: bool = False  # True when the matched frames are XISF masters


def _group_sessions(date_index: dict) -> list[tuple[str, list[str], list]]:
    """Group consecutive calendar dates in *date_index* into imaging sessions.

    Two dates are considered part of the same session when they are exactly
    1 calendar day apart.  This handles the common case of calibration frames
    captured across midnight — e.g. darks taken at 23:00 on Apr 1 and again
    at 01:00 on Apr 2 should be treated as one session of 50 frames, not two
    sessions of 25.  The gap threshold is intentionally tight (1 day) so that
    darks from separate imaging runs months apart are never merged.

    Args:
        date_index: mapping of 'yyyy-mm-dd' → list of FitsFrame objects.

    Returns:
        List of (earliest_date_label, [all_date_labels], [all_frames]) tuples,
        one per session, sorted by earliest date.
    """
    sorted_dates = sorted(date_index, key=lambda d: datetime.strptime(d, '%Y-%m-%d'))
    sessions: list[list[str]] = []
    for d in sorted_dates:
        if sessions:
            prev = datetime.strptime(sessions[-1][-1], '%Y-%m-%d')
            curr = datetime.strptime(d, '%Y-%m-%d')
            if (curr - prev).days <= 1:
                sessions[-1].append(d)
                continue
        sessions.append([d])

    result = []
    for session_dates in sessions:
        frames = [f for d in session_dates for f in date_index[d]]
        result.append((session_dates[0], session_dates, frames))
    return result


def _closest_in(date_index: dict, target_dt: datetime) -> Optional[CalibMatch]:
    """Return all frames from the single imaging session closest to *target_dt*.

    First groups the date index into sessions via `_group_sessions()`, then
    picks the session whose nearest date is closest to *target_dt*.  All frames
    from that session are returned together — this is what ensures 50 darks
    spread across an Apr 1 / Apr 2 midnight boundary all come back as one set.

    Only the closest session is returned (not all sessions) to avoid mixing
    calibration frames from different equipment setups that may be months apart.

    The *date_label* on the returned CalibMatch is the single date within the
    winning session that is nearest to *target_dt*; it is used only for the
    staleness warning, not for frame selection.

    Args:
        date_index: mapping of 'yyyy-mm-dd' → list of FitsFrame objects.
        target_dt:  the imaging night being matched against.

    Returns:
        CalibMatch for the closest session, or None if date_index is empty.
    """
    if not date_index:
        return None

    sessions = _group_sessions(date_index)

    # Find the session whose nearest date is closest to target_dt
    def session_delta(session):
        _, dates, _ = session
        return min(
            abs((datetime.strptime(d, '%Y-%m-%d') - target_dt).days)
            for d in dates
        )

    best_session = min(sessions, key=session_delta)
    _, session_dates, frames = best_session

    best_label = min(
        session_dates,
        key=lambda d: abs((datetime.strptime(d, '%Y-%m-%d') - target_dt).days),
    )
    delta = abs((datetime.strptime(best_label, '%Y-%m-%d') - target_dt).days)
    is_master = any(f.path.suffix.lower() == '.xisf' for f in frames)
    return CalibMatch(date_label=best_label, frames=frames,
                      days_delta=int(delta), is_master=is_master)


def find_closest_calib(
    date_index: dict,
    target_night: str,
    prefer_master: bool = False,
) -> Optional[CalibMatch]:
    """Given a {date: [frames]} index, return all matching frames with staleness
    info derived from the closest date.

    When *prefer_master* is True (darks and biases), an XISF master is always
    chosen over individual .fit files when one exists — regardless of date.
    When False (flats), the master is only preferred when it is at least as
    recent as the best individual set.
    """
    if not date_index:
        return None
    target_dt = datetime.strptime(target_night, '%Y-%m-%d')

    master_dates = {
        d: [f for f in fs if f.path.suffix.lower() == '.xisf']
        for d, fs in date_index.items()
        if any(f.path.suffix.lower() == '.xisf' for f in fs)
    }
    indiv_dates = {
        d: fs
        for d, fs in date_index.items()
        if not any(f.path.suffix.lower() == '.xisf' for f in fs)
    }

    master_match = _closest_in(master_dates, target_dt)
    indiv_match  = _closest_in(indiv_dates,  target_dt)

    if master_match and indiv_match:
        if prefer_master:
            return master_match
        # Flats: prefer master only when it is at least as recent
        return master_match if master_match.days_delta <= indiv_match.days_delta else indiv_match
    return master_match or indiv_match


# ---------------------------------------------------------------------------
# Per-night calibration resolution
# ---------------------------------------------------------------------------

@dataclass
class NightCalib:
    darks: list[FitsFrame] = field(default_factory=list)
    biases: list[FitsFrame] = field(default_factory=list)
    flats: list[FitsFrame] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def resolve_calibration(
    night_label: str,
    camera: str,
    exposure: float,
    set_temp: Optional[int],
    dark_index: dict,
    bias_index: dict,
    flat_index: dict,
) -> NightCalib:
    """Find the best calibration frames for one light-frame group."""
    from .fits_parser import format_exposure  # avoid circular at module level

    result = NightCalib()
    exp_str = format_exposure(exposure)
    temp_str = str(set_temp) if set_temp is not None else 'NoTemp'

    # --- Darks (fuzzy temperature match within ±DARK_TEMP_TOLERANCE °C) ---
    # Merge date indices from all dark buckets whose camera+exposure match and
    # whose temperature is within tolerance of the light group's set_temp.
    dark_candidates: dict[str, list[FitsFrame]] = {}
    for key, date_index in dark_index.items():
        k_camera, k_exp, k_temp = key.split('|', 2)
        if k_camera != camera or k_exp != exp_str:
            continue
        if set_temp is None or k_temp == 'NoTemp':
            # Accept only exact NoTemp↔NoTemp; skip mixed cases
            if temp_str != k_temp:
                continue
        else:
            try:
                if abs(int(k_temp) - set_temp) > DARK_TEMP_TOLERANCE:
                    continue
            except ValueError:
                continue
        for date, frames in date_index.items():
            dark_candidates.setdefault(date, []).extend(frames)

    if dark_candidates:
        match = find_closest_calib(dark_candidates, night_label, prefer_master=True)
        if match:
            result.darks = match.frames
            if match.days_delta > CALIB_WARNING_DAYS:
                kind = "Master dark" if match.is_master else "Dark frames"
                result.warnings.append(
                    f"{kind} are {match.days_delta} days from {night_label} "
                    f"(>{CALIB_WARNING_DAYS}d threshold) — consider new darks"
                )
    else:
        result.warnings.append(
            f"No darks found for {camera} {exp_str} @ {temp_str}°C "
            f"(±{DARK_TEMP_TOLERANCE}°C)"
        )

    # --- Biases (camera-only match — temperature-independent) ---
    if camera in bias_index:
        match = find_closest_calib(bias_index[camera], night_label, prefer_master=True)
        if match:
            result.biases = match.frames
            if match.days_delta > CALIB_WARNING_DAYS:
                kind = "Master bias" if match.is_master else "Bias frames"
                result.warnings.append(
                    f"{kind} are {match.days_delta} days from {night_label} "
                    f"(>{CALIB_WARNING_DAYS}d threshold) — consider new biases"
                )
    else:
        result.warnings.append(
            f"No biases found for {camera}"
        )

    # --- Flats ---
    flat_key = f"{camera}|{night_label}"
    if flat_key in flat_index:
        result.flats = flat_index[flat_key]
    else:
        result.warnings.append(
            f"No flats found for {camera} on night {night_label}"
        )

    return result
