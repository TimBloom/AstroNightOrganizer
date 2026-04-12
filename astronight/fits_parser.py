"""
FITS header reading and filename parsing for ASIAIR-generated files.

Strategy: FITS header is the authoritative source for all technical fields.
Filename parsing is a fallback, used only when a header field is absent
(most commonly OBJECT/target, which is missing from some Autorun light frames).

ASIAIR filename formats (observed across 2024–2026):
  Light full:   Light_<Target>_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<Temp>C_<seq>.fit
  Light mid:    Light_<Target>_<Exp>_Bin<N>_gain<G>_<TS>_<Temp>C_<seq>.fit
  Light old:    Light_<Target>_<Exp>_Bin<N>_<TS>_<seq>.fit
  Calib full:   <Type>_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<Temp>C_<seq>.fit
  Calib min:    <Type>_<Exp>_Bin<N>_<TS>_<seq>.fit
  Flat angle A: Flat_<deg>deg_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<Temp>C_<seq>.fit  (2026+)
  Flat angle B: Flat_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<deg>deg_<seq>.fit          (2026+, no temp)
  Flat angle C: Flat_<Exp>_Bin<N>_<Camera>_gain<G>_<TS>_<deg>deg_<Temp>C_<seq>.fit  (2026+, temp after angle)

Notes:
  - DATE-OBS in FITS headers is stored in UTC by ASIAIR firmware; filenames use local time.
    Night labels are derived from filename timestamps (local) when available, falling back to
    the header timestamp. This ensures dawn flats are assigned to the correct imaging night.

Notes:
  - Temperature in filename is a float AFTER the timestamp (e.g. -9.9C, -20.0C)
  - Gain in filename uses a 'gain' prefix (e.g. gain252)
  - Camera in filename comes BEFORE gain
  - 2026+: ROTATOR header field and optional <deg>deg field in flat filenames
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from astropy.io import fits
from astropy.io.fits.verify import VerifyWarning


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FitsFrame:
    path: Path
    frame_type: str           # Light | Flat | Dark | Bias
    target: Optional[str]     # None for calibration frames
    exposure: float           # seconds (from EXPOSURE header, authoritative)
    binning: int              # from XBINNING header
    gain: Optional[int]       # from GAIN header
    set_temp: Optional[int]   # integer setpoint °C from SET-TEMP header
    camera: str               # normalised short name, e.g. "2600MC"
    timestamp: datetime       # from DATE-OBS header (authoritative, UTC)
    night_label: str          # yyyy-MM-dd imaging night (noon-to-noon, local time)
    calendar_date: str        # yyyy-MM-dd of actual timestamp (darks/biases)
    rotator: Optional[int]    # ROTATOR header value in degrees (2026+), or None
    fn_timestamp: Optional[datetime] = field(default=None, repr=False)
    # Local timestamp parsed from the filename (e.g. 20260321-073509 → 07:35 local).
    # None for XISF masters and any file whose name didn't match a known pattern.
    # Used by scan_fits to detect the session's UTC offset and correct night_label
    # on frames that only have a UTC header timestamp.

    # Populated later during grouping / copy
    dest_path: Optional[Path] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Camera name normalisation
# ---------------------------------------------------------------------------

_STRIP_PREFIX = re.compile(r'^ZWO\s+ASI\s*', re.IGNORECASE)
_STRIP_SUFFIX = re.compile(r'\s+(Pro|Duo|Plus|Mini)$', re.IGNORECASE)


def normalise_camera(raw: str) -> str:
    """'ZWO ASI2600MC Duo' -> '2600MC'"""
    name = _STRIP_PREFIX.sub('', raw.strip())
    name = _STRIP_SUFFIX.sub('', name)
    return name.strip()


# ---------------------------------------------------------------------------
# Exposure formatting
# ---------------------------------------------------------------------------

def format_exposure(seconds: float) -> str:
    """0.001 -> '1.0ms', 300.0 -> '300.0s'"""
    if seconds < 1.0:
        return f"{seconds * 1000:.1f}ms"
    return f"{seconds:.1f}s"


# ---------------------------------------------------------------------------
# Night label (noon-to-noon)
# ---------------------------------------------------------------------------

def imaging_night(ts: datetime) -> str:
    """Return the yyyy-MM-dd label for the imaging night that contains *ts*.

    Nights run from local noon to the following noon, so anything before 12:00
    belongs to the previous calendar day's night.
    """
    if ts.hour < 12:
        return (ts - timedelta(days=1)).strftime('%Y-%m-%d')
    return ts.strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# FITS header reading
# ---------------------------------------------------------------------------

def read_fits_header(path: Path) -> dict:
    """Return a flat dict of FITS header keyword→value for the primary HDU.

    Suppresses astropy warnings for known ASIAIR quirks:
    - Non-ASCII characters in headers (replaced with '?')
    - Header blocks that are 4096 bytes instead of the standard 2880

    Raises AstropyUserWarning as an exception if the file appears truncated,
    so callers can route it to the error list rather than silently processing
    a potentially corrupt file.
    """
    from astropy.utils.exceptions import AstropyUserWarning
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', AstropyUserWarning)
        warnings.simplefilter('ignore', VerifyWarning)
        warnings.filterwarnings('ignore', message='non-ASCII characters')
        with fits.open(path, memmap=False, ignore_missing_simple=True) as hdul:
            hdr = hdul[0].header
            result = dict(hdr)

    for w in caught:
        if issubclass(w.category, AstropyUserWarning) and 'truncated' in str(w.message).lower():
            raise AstropyUserWarning(str(w.message))

    return result


# ---------------------------------------------------------------------------
# Header extraction helpers
# ---------------------------------------------------------------------------

_IMAGETYP_MAP = {
    'light': 'Light', 'flat': 'Flat', 'dark': 'Dark', 'bias': 'Bias',
    'flat field': 'Flat', 'dark frame': 'Dark', 'bias frame': 'Bias',
    'light frame': 'Light',
}


def _header_frame_type(header: dict) -> Optional[str]:
    raw = str(header.get('IMAGETYP', '')).strip().lower()
    return _IMAGETYP_MAP.get(raw)


def _header_exposure(header: dict) -> Optional[float]:
    for key in ('EXPOSURE', 'EXPTIME'):
        if key in header:
            try:
                return float(header[key])
            except (ValueError, TypeError):
                pass
    return None


def _header_binning(header: dict) -> Optional[int]:
    if 'XBINNING' in header:
        try:
            return int(header['XBINNING'])
        except (ValueError, TypeError):
            pass
    return None


def _header_gain(header: dict) -> Optional[int]:
    if 'GAIN' in header:
        try:
            return int(header['GAIN'])
        except (ValueError, TypeError):
            pass
    return None


def _header_set_temp(header: dict) -> Optional[int]:
    if 'SET-TEMP' in header:
        try:
            return int(round(float(header['SET-TEMP'])))
        except (ValueError, TypeError):
            pass
    return None


def _header_camera(header: dict) -> Optional[str]:
    raw = str(header.get('INSTRUME', '')).strip()
    return raw if raw else None


def _header_object(header: dict) -> Optional[str]:
    raw = str(header.get('OBJECT', '')).strip()
    return raw if raw else None


def _header_timestamp(header: dict) -> Optional[datetime]:
    raw = str(header.get('DATE-OBS', '')).strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M:%S.%f'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return None


def _header_rotator(header: dict) -> Optional[int]:
    if 'ROTATOR' in header:
        try:
            return int(header['ROTATOR'])
        except (ValueError, TypeError):
            pass
    return None


# ---------------------------------------------------------------------------
# Filename patterns (fallback when header fields are absent)
# ---------------------------------------------------------------------------

_TS   = r'(\d{8}-\d{6})'        # YYYYMMDD-HHMMSS
_EXP  = r'([\d.]+(?:ms|s))'     # e.g. 300.0s, 1.0ms
_BIN  = r'Bin(\d)'               # e.g. Bin1
_SEQ  = r'\d+'                   # sequence number (not captured)
_CAM  = r'([A-Za-z0-9]+MC\w*)'  # e.g. 2600MC, 585MC
_GAIN = r'gain(\d+)'             # e.g. gain100
_TEMP = r'(-?[\d.]+)C'          # e.g. -9.9C, -20.0C
_DEG  = r'[\d.]+deg'            # e.g. 106deg (rotator angle — 2026+)

_PAT_LIGHT_FULL = re.compile(
    rf'^Light_(.+?)_{_EXP}_{_BIN}_{_CAM}_{_GAIN}_{_TS}_{_TEMP}_{_SEQ}\.fits?$',
    re.IGNORECASE,
)
_PAT_LIGHT_MID = re.compile(
    rf'^Light_(.+?)_{_EXP}_{_BIN}_{_GAIN}_{_TS}_{_TEMP}_{_SEQ}\.fits?$',
    re.IGNORECASE,
)
_PAT_LIGHT_OLD = re.compile(
    rf'^Light_(.+?)_{_EXP}_{_BIN}_{_TS}_{_SEQ}\.fits?$',
    re.IGNORECASE,
)
_PAT_FLAT_ANGLE = re.compile(
    rf'^(Flat)_{_DEG}_{_EXP}_{_BIN}_{_CAM}_{_GAIN}_{_TS}_{_TEMP}_{_SEQ}\.fits?$',
    re.IGNORECASE,
)
# 2026+ format: angle comes AFTER the timestamp, no temperature field
# e.g. Flat_33.3ms_Bin1_2600MC_gain100_20260320-061616_286deg_0001.fit
_PAT_FLAT_ANGLE_LATE = re.compile(
    rf'^(Flat)_{_EXP}_{_BIN}_{_CAM}_{_GAIN}_{_TS}_{_DEG}_{_SEQ}\.fits?$',
    re.IGNORECASE,
)
# 2026+ format: angle AND temperature come AFTER the timestamp
# e.g. Flat_33.3ms_Bin1_2600MC_gain100_20260321-073509_285deg_12.1C_0016.fit
_PAT_FLAT_ANGLE_LATE_TEMP = re.compile(
    rf'^(Flat)_{_EXP}_{_BIN}_{_CAM}_{_GAIN}_{_TS}_{_DEG}_{_TEMP}_{_SEQ}\.fits?$',
    re.IGNORECASE,
)
_PAT_CALIB_FULL = re.compile(
    rf'^(Flat|Dark|Bias)_{_EXP}_{_BIN}_{_CAM}_{_GAIN}_{_TS}_{_TEMP}_{_SEQ}\.fits?$',
    re.IGNORECASE,
)
_PAT_CALIB_MIN = re.compile(
    rf'^(Flat|Dark|Bias)_{_EXP}_{_BIN}_{_TS}_{_SEQ}\.fits?$',
    re.IGNORECASE,
)

_SKIP_RE = re.compile(
    r'^(Stacked\d*_|Preview_|MasterFlat_|MasterDark_|MasterBias_|Plan_)',
    re.IGNORECASE,
)


def _parse_exp(raw: str) -> float:
    if raw.endswith('ms'):
        return float(raw[:-2]) / 1000.0
    return float(raw[:-1])


def _parse_ts(raw: str) -> datetime:
    return datetime.strptime(raw, '%Y%m%d-%H%M%S')


def _filename_fields(name: str):
    """Extract fields from filename. Returns a dict with any values found.

    All values may be None if the filename doesn't match any known pattern.
    """
    out = dict(frame_type=None, target=None, exposure=None,
               binning=None, gain=None, set_temp=None, camera=None, ts=None)

    m = _PAT_LIGHT_FULL.match(name)
    if m:
        target, exp_s, binning, camera, gain, ts_s, temp = m.groups()
        out.update(frame_type='Light', target=target, exposure=_parse_exp(exp_s),
                   binning=int(binning), gain=int(gain),
                   set_temp=int(round(float(temp))), camera=camera, ts=_parse_ts(ts_s))
        return out

    m = _PAT_LIGHT_MID.match(name)
    if m:
        target, exp_s, binning, gain, ts_s, temp = m.groups()
        out.update(frame_type='Light', target=target, exposure=_parse_exp(exp_s),
                   binning=int(binning), gain=int(gain),
                   set_temp=int(round(float(temp))), ts=_parse_ts(ts_s))
        return out

    m = _PAT_LIGHT_OLD.match(name)
    if m:
        target, exp_s, binning, ts_s = m.groups()
        out.update(frame_type='Light', target=target, exposure=_parse_exp(exp_s),
                   binning=int(binning), ts=_parse_ts(ts_s))
        return out

    m = _PAT_FLAT_ANGLE.match(name)
    if m:
        _, exp_s, binning, camera, gain, ts_s, temp = m.groups()
        out.update(frame_type='Flat', exposure=_parse_exp(exp_s),
                   binning=int(binning), gain=int(gain),
                   set_temp=int(round(float(temp))), camera=camera, ts=_parse_ts(ts_s))
        return out

    m = _PAT_FLAT_ANGLE_LATE.match(name)
    if m:
        _, exp_s, binning, camera, gain, ts_s = m.groups()
        out.update(frame_type='Flat', exposure=_parse_exp(exp_s),
                   binning=int(binning), gain=int(gain),
                   camera=camera, ts=_parse_ts(ts_s))
        return out

    m = _PAT_FLAT_ANGLE_LATE_TEMP.match(name)
    if m:
        _, exp_s, binning, camera, gain, ts_s, temp = m.groups()
        out.update(frame_type='Flat', exposure=_parse_exp(exp_s),
                   binning=int(binning), gain=int(gain),
                   set_temp=int(round(float(temp))), camera=camera, ts=_parse_ts(ts_s))
        return out

    m = _PAT_CALIB_FULL.match(name)
    if m:
        ftype, exp_s, binning, camera, gain, ts_s, temp = m.groups()
        out.update(frame_type=ftype.capitalize(), exposure=_parse_exp(exp_s),
                   binning=int(binning), gain=int(gain),
                   set_temp=int(round(float(temp))), camera=camera, ts=_parse_ts(ts_s))
        return out

    m = _PAT_CALIB_MIN.match(name)
    if m:
        ftype, exp_s, binning, ts_s = m.groups()
        out.update(frame_type=ftype.capitalize(), exposure=_parse_exp(exp_s),
                   binning=int(binning), ts=_parse_ts(ts_s))
        return out

    return out


# ---------------------------------------------------------------------------
# Public parser — header-first
# ---------------------------------------------------------------------------

def parse_frame(path: Path, header: Optional[dict] = None) -> Optional[FitsFrame]:
    """Parse a FITS file into a FitsFrame.

    FITS header is the authoritative source for all technical fields.
    Filename parsing fills in any values absent from the header (most
    commonly the target name in Autorun light frames).

    Returns None if the file should be skipped or cannot be identified.
    """
    name = path.name

    if _SKIP_RE.match(name):
        return None

    # --- Extract from filename (provides baseline, especially target name) ---
    fn = _filename_fields(name)

    # --- Extract from header (authoritative, overrides filename) ---
    h_frame_type = _header_frame_type(header) if header else None
    h_exposure   = _header_exposure(header)   if header else None
    h_binning    = _header_binning(header)    if header else None
    h_gain       = _header_gain(header)       if header else None
    h_set_temp   = _header_set_temp(header)   if header else None
    h_camera     = _header_camera(header)     if header else None
    h_object     = _header_object(header)     if header else None
    h_ts         = _header_timestamp(header)  if header else None
    h_rotator    = _header_rotator(header)    if header else None

    # Merge: header wins for technical fields; filename wins for target when header lacks OBJECT
    frame_type = h_frame_type or fn['frame_type']
    exposure   = h_exposure   if h_exposure  is not None else fn['exposure']
    binning    = h_binning    if h_binning   is not None else fn['binning']
    gain       = h_gain       if h_gain      is not None else fn['gain']
    set_temp   = h_set_temp   if h_set_temp  is not None else fn['set_temp']
    camera     = h_camera     or fn['camera']
    target     = h_object     or fn['target']
    ts         = h_ts         or fn['ts']

    # Can't build a frame without these three
    if frame_type is None or exposure is None or ts is None:
        return None

    cam_norm = normalise_camera(camera) if camera else 'UnknownCam'
    # DATE-OBS in ASIAIR FITS headers is stored in UTC, but filenames use local time.
    # Use the filename timestamp for the night label so dawn flats/darks are assigned
    # to the correct imaging night (the evening before), not the UTC calendar date.
    fn_ts = fn['ts']
    night_ts = fn_ts or ts
    night    = imaging_night(night_ts)
    cal_date = ts.strftime('%Y-%m-%d')

    return FitsFrame(
        path=path,
        frame_type=frame_type,
        target=target,
        exposure=exposure,
        binning=binning or 1,
        gain=gain,
        set_temp=set_temp,
        camera=cam_norm,
        timestamp=ts,
        night_label=night,
        calendar_date=cal_date,
        rotator=h_rotator,
        fn_timestamp=fn_ts,
    )
