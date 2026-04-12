"""Tests for filename parsing and night-label logic."""

from datetime import datetime
from pathlib import Path

import pytest

from astronight.fits_parser import (
    format_exposure,
    imaging_night,
    normalise_camera,
    parse_frame,
)


# ---------------------------------------------------------------------------
# Camera normalisation
# ---------------------------------------------------------------------------

class TestNormaliseCamera:
    def test_strips_zwo_asi_prefix(self):
        assert normalise_camera('ZWO ASI2600MC Duo') == '2600MC'

    def test_strips_pro_suffix(self):
        assert normalise_camera('ZWO ASI183MC Pro') == '183MC'

    def test_plain_model(self):
        assert normalise_camera('ZWO ASI294MC') == '294MC'

    def test_already_short(self):
        assert normalise_camera('2600MC') == '2600MC'


# ---------------------------------------------------------------------------
# Exposure formatting
# ---------------------------------------------------------------------------

class TestFormatExposure:
    def test_sub_second(self):
        assert format_exposure(0.001) == '1.0ms'

    def test_integer_seconds(self):
        assert format_exposure(300.0) == '300.0s'

    def test_fractional_seconds(self):
        assert format_exposure(1.5) == '1.5s'


# ---------------------------------------------------------------------------
# Night label
# ---------------------------------------------------------------------------

class TestImagingNight:
    def test_before_noon_rolls_back(self):
        ts = datetime(2024, 6, 24, 3, 52, 18)
        assert imaging_night(ts) == '2024-06-23'

    def test_after_noon_stays(self):
        ts = datetime(2024, 6, 23, 22, 0, 0)
        assert imaging_night(ts) == '2024-06-23'

    def test_exactly_noon_stays(self):
        ts = datetime(2024, 6, 23, 12, 0, 0)
        assert imaging_night(ts) == '2024-06-23'


# ---------------------------------------------------------------------------
# Filename parsing — Lights
# ---------------------------------------------------------------------------

class TestParseLightOld:
    """Old format: Light_Target_Exp_BinN_TS_seq.fit"""
    FNAME = 'Light_NGC6960_300.0s_Bin1_20240624-035218_0001.fit'

    def setup_method(self):
        self.frame = parse_frame(Path(self.FNAME))

    def test_parsed(self):
        assert self.frame is not None

    def test_frame_type(self):
        assert self.frame.frame_type == 'Light'

    def test_target(self):
        assert self.frame.target == 'NGC6960'

    def test_exposure(self):
        assert self.frame.exposure == 300.0

    def test_night_label_rolls_back(self):
        # 03:52 < noon → night is the previous calendar day
        assert self.frame.night_label == '2024-06-23'


class TestParseLightMid:
    """Mid format: Light_Target_Exp_BinN_gainG_TS_TempC_seq.fit"""
    FNAME = 'Light_M8_300.0s_Bin1_gain252_20240701-224722_-9.9C_0001.fit'

    def setup_method(self):
        self.frame = parse_frame(Path(self.FNAME))

    def test_parsed(self):
        assert self.frame is not None

    def test_target(self):
        assert self.frame.target == 'M8'

    def test_gain(self):
        assert self.frame.gain == 252

    def test_set_temp(self):
        assert self.frame.set_temp == -10  # -9.9 rounded

    def test_night_label(self):
        assert self.frame.night_label == '2024-07-01'  # 22:47 → same day


class TestParseLightFull:
    """Full format: Light_Target_Exp_BinN_Camera_gainG_TS_TempC_seq.fit"""
    FNAME = 'Light_M42_10.0s_Bin1_2600MC_gain100_20241202-223035_-20.0C_0001.fit'

    def setup_method(self):
        self.frame = parse_frame(Path(self.FNAME))

    def test_parsed(self):
        assert self.frame is not None

    def test_camera_normalised(self):
        assert self.frame.camera == '2600MC'

    def test_gain(self):
        assert self.frame.gain == 100

    def test_set_temp(self):
        assert self.frame.set_temp == -20

    def test_night_label(self):
        assert self.frame.night_label == '2024-12-02'


# ---------------------------------------------------------------------------
# Filename parsing — Calibration frames
# ---------------------------------------------------------------------------

class TestParseDark:
    FNAME = 'Dark_300.0s_Bin1_2600MC_gain100_20241001-140000_-10.0C_0001.fit'

    def setup_method(self):
        self.frame = parse_frame(Path(self.FNAME))

    def test_frame_type(self):
        assert self.frame.frame_type == 'Dark'

    def test_no_target(self):
        assert self.frame.target is None

    def test_calendar_date(self):
        # Darks use calendar date, not night label
        assert self.frame.calendar_date == '2024-10-01'


class TestParseBias:
    """Bias files have very short exposures (e.g. 1.0ms)."""
    FNAME = 'Bias_1.0ms_Bin1_2600MC_gain100_20241128-055010_-20.0C_0001.fit'

    def setup_method(self):
        self.frame = parse_frame(Path(self.FNAME))

    def test_frame_type(self):
        assert self.frame.frame_type == 'Bias'

    def test_exposure_ms(self):
        assert abs(self.frame.exposure - 0.001) < 1e-9


class TestParseFlat:
    FNAME = 'Flat_2.0s_Bin1_2600MC_gain100_20240923-190000_0.0C_0001.fit'

    def setup_method(self):
        self.frame = parse_frame(Path(self.FNAME))

    def test_frame_type(self):
        assert self.frame.frame_type == 'Flat'

    def test_no_target(self):
        assert self.frame.target is None


class TestSkipNonLightCalib:
    def test_stacked_skipped(self):
        assert parse_frame(Path('Stacked_IC434_300.0s_-19.9C_2600MC_20250131-213207.fit')) is None

    def test_stacked_numbered_skipped(self):
        # e.g. Stacked142_NGC 6960_60.0s_... (2025+ format)
        assert parse_frame(Path('Stacked142_NGC 6960_60.0s_Bin1_2600MC_gain100_20250801-030000_-10.0C_0001.fit')) is None

    def test_preview_skipped(self):
        assert parse_frame(Path('Preview_M16_60.0s_Bin1_gain252_20240723-215720_20.1C.fit')) is None

    def test_master_flat_skipped(self):
        assert parse_frame(Path('MasterFlat_Stack20_10.0ms_Bin1_20240602-172548.fit')) is None


class TestParseFlatAngle:
    """2026+ rotator-angle flat (angle first): Flat_106deg_33.3ms_Bin1_2600MC_gain100_TS_TempC_seq"""
    FNAME = 'Flat_106deg_33.3ms_Bin1_2600MC_gain100_20260120-073101_-3.0C_0001.fit'

    def setup_method(self):
        self.frame = parse_frame(Path(self.FNAME))

    def test_parsed(self):
        assert self.frame is not None

    def test_frame_type(self):
        assert self.frame.frame_type == 'Flat'

    def test_exposure(self):
        assert abs(self.frame.exposure - 0.0333) < 0.001

    def test_camera(self):
        assert self.frame.camera == '2600MC'


class TestParseFlatAngleLate:
    """2026+ rotator-angle flat (angle after timestamp, no temp):
    Flat_33.3ms_Bin1_2600MC_gain100_TS_286deg_seq"""
    FNAME = 'Flat_33.3ms_Bin1_2600MC_gain100_20260320-061616_286deg_1316.fit'

    def setup_method(self):
        self.frame = parse_frame(Path(self.FNAME))

    def test_parsed(self):
        assert self.frame is not None

    def test_frame_type(self):
        assert self.frame.frame_type == 'Flat'

    def test_exposure(self):
        assert abs(self.frame.exposure - 0.0333) < 0.001

    def test_camera(self):
        assert self.frame.camera == '2600MC'

    def test_night_label(self):
        # 6:16 AM local → before noon → rolls back to 2026-03-19
        assert self.frame.night_label == '2026-03-19'

    def test_no_set_temp(self):
        assert self.frame.set_temp is None


class TestParseFlatAngleLateTemp:
    """2026+ rotator-angle flat (angle AND temp after timestamp):
    Flat_33.3ms_Bin1_2600MC_gain100_TS_285deg_12.1C_seq
    This is the format confirmed in real ASIAIR headers as of 2026-03."""
    FNAME = 'Flat_33.3ms_Bin1_2600MC_gain100_20260321-073509_285deg_12.1C_0016.fit'

    def setup_method(self):
        self.frame = parse_frame(Path(self.FNAME))

    def test_parsed(self):
        assert self.frame is not None

    def test_frame_type(self):
        assert self.frame.frame_type == 'Flat'

    def test_exposure(self):
        assert abs(self.frame.exposure - 0.0333) < 0.001

    def test_camera(self):
        assert self.frame.camera == '2600MC'

    def test_set_temp(self):
        assert self.frame.set_temp == 12  # 12.1 rounded

    def test_night_label(self):
        # 7:35 AM local → before noon → rolls back to 2026-03-20 (the imaging night)
        # DATE-OBS in the header is UTC (12:35 PM UTC) which would wrongly give 2026-03-21;
        # the filename timestamp (local) must win for night label calculation.
        assert self.frame.night_label == '2026-03-20'


class TestNightLabelUsesFilenameTimestamp:
    """Regression: DATE-OBS is UTC; filename timestamp is local.
    Dawn flats/darks at 7-8 AM local (12-13 UTC) must roll back to the
    previous calendar day's night, not stay on the UTC date."""

    def test_utc_header_noon_uses_filename_local_time(self):
        # Filename says 07:35 local → night 2026-03-20
        # Header would say 12:35 UTC → hour=12, not < 12 → night 2026-03-21 (wrong)
        from astropy.io import fits
        from io import BytesIO
        hdr = fits.Header()
        hdr['IMAGETYP'] = 'Flat'
        hdr['EXPOSURE'] = 0.0333
        hdr['INSTRUME'] = 'ZWO ASI2600MC Duo'
        hdr['DATE-OBS'] = '2026-03-21T12:35:08.317943'  # UTC noon → wrong night if used directly
        hdr['XBINNING'] = 1
        hdr['GAIN']     = 100
        fname = 'Flat_33.3ms_Bin1_2600MC_gain100_20260321-073509_285deg_12.1C_0016.fit'
        frame = parse_frame(Path(fname), header=dict(hdr))
        assert frame is not None
        assert frame.night_label == '2026-03-20'  # local time, not UTC


class TestParseSpecialTargets:
    """Comet and multi-word target names."""

    def test_comet_target(self):
        f = parse_frame(Path('Light_C-2025 R2_60.0s_Bin1_2600MC_gain100_20251021-200735_-20.3C_0001.fit'))
        assert f is not None
        assert f.target == 'C-2025 R2'

    def test_spaced_ngc(self):
        f = parse_frame(Path('Light_NGC 6960_60.0s_Bin1_2600MC_gain100_20251015-020000_-10.0C_0001.fit'))
        assert f is not None
        assert f.target == 'NGC 6960'

    def test_lbn_target(self):
        f = parse_frame(Path('Light_LBN 437_120.0s_Bin1_2600MC_gain100_20251010-223000_-10.0C_0001.fit'))
        assert f is not None
        assert f.target == 'LBN 437'
