"""
XISF master calibration frame parser, backed by the `xisf` package.

XISF (Extensible Image Serialization Format) is PixInsight's native file
format.  Users may have pre-built master calibration frames (master dark,
master bias, master flat) stored as .xisf files from a previous PixInsight
session.  This module reads those files and returns standard FitsFrame objects
so they can participate in calibration matching alongside individual .fit files.

Note: XISF files are intentionally NOT cached in the scan cache.  There are
typically only a handful of master files and they are fast to parse; caching
them would add schema complexity for negligible benefit.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from xisf import XISF

from .fits_parser import FitsFrame, imaging_night, normalise_camera

# Maps IMAGETYP header values (lower-cased) found in PixInsight XISF files to
# our internal frame_type strings.  Both "Master Dark" (PixInsight's label for
# a stacked master) and plain "dark" (bare IMAGETYP from ASIAIR-derived XISF)
# are included because different PixInsight workflows write different values.
_MASTER_TYPE_MAP: dict[str, str] = {
    'master dark':       'Dark',
    'master bias':       'Bias',
    'master zero':       'Bias',
    'master flat':       'Flat',
    'master flat field': 'Flat',
    'dark frame':        'Dark',
    'dark':              'Dark',
    'bias frame':        'Bias',
    'bias':              'Bias',
    'zero':              'Bias',
    'flat field':        'Flat',
    'flat':              'Flat',
}


def parse_xisf_frame(path: Path) -> Optional[FitsFrame]:
    """Parse an XISF master calibration file into a FitsFrame.

    Returns None if the file is not a recognisable Dark / Bias / Flat master.
    """
    try:
        xf = XISF(str(path))
        meta = xf.get_images_metadata()[0]
        kw = {
            k: v[0]['value']
            for k, v in meta.get('FITSKeywords', {}).items()
            if v
        }
    except Exception:
        return None

    # XISF FITSKeyword string values are sometimes stored with surrounding
    # single quotes (e.g. "'Master Dark'") — strip both whitespace and quotes.
    imagetyp = str(kw.get('IMAGETYP', '')).lower().strip().strip("'")
    frame_type = _MASTER_TYPE_MAP.get(imagetyp)
    if frame_type is None:
        return None

    raw_cam = str(kw.get('INSTRUME', '')).strip().strip("'")
    camera = normalise_camera(raw_cam) if raw_cam else 'UnknownCam'

    exp_raw = kw.get('EXPOSURE') or kw.get('EXPTIME') or 0
    try:
        exposure = float(exp_raw)
    except (ValueError, TypeError):
        exposure = 0.0

    temp_raw = kw.get('SET-TEMP')
    try:
        set_temp: Optional[int] = int(round(float(temp_raw))) if temp_raw is not None else None
    except (ValueError, TypeError):
        set_temp = None

    gain_raw = kw.get('GAIN')
    try:
        gain: Optional[int] = int(float(gain_raw)) if gain_raw is not None else None
    except (ValueError, TypeError):
        gain = None

    bin_raw = kw.get('XBINNING')
    try:
        binning = int(float(bin_raw)) if bin_raw is not None else 1
    except (ValueError, TypeError):
        binning = 1

    date_obs = str(kw.get('DATE-OBS', '')).strip().strip("'").rstrip('Z')
    ts: Optional[datetime] = None
    for fmt in ('%Y-%m-%dT%H:%M:%S.%f', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            ts = datetime.strptime(date_obs, fmt)
            break
        except ValueError:
            continue
    if ts is None:
        # No parseable DATE-OBS — fall back to the file's modification time.
        # This is a last resort; it means the staleness warning may be slightly
        # off, but it avoids crashing on malformed headers.
        ts = datetime.fromtimestamp(path.stat().st_mtime)

    return FitsFrame(
        path=path,
        frame_type=frame_type,
        target=str(kw.get('OBJECT', '')).strip().strip("'") or None,
        exposure=exposure,
        binning=binning,
        gain=gain,
        set_temp=set_temp,
        camera=camera,
        timestamp=ts,
        night_label=imaging_night(ts),
        calendar_date=ts.strftime('%Y-%m-%d'),
        rotator=None,  # XISF masters don't carry rotator position
        # fn_timestamp intentionally omitted (defaults to None) — XISF headers
        # store UTC; _apply_utc_offset_correction() in scan_fits() will patch night_label
    )
