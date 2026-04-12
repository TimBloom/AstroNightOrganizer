"""Tests for calibration matching logic."""

from pathlib import Path

import pytest

from astronight.calibration import (
    _group_sessions,
    build_bias_index,
    build_dark_index,
    build_flat_index,
    find_closest_calib,
    resolve_calibration,
)
from astronight.fits_parser import parse_frame


def _make(fname: str):
    return parse_frame(Path(fname))


DARK_300     = _make('Dark_300.0s_Bin1_2600MC_gain100_20241001-140000_-10.0C_0001.fit')
DARK_300_OLD = _make('Dark_300.0s_Bin1_2600MC_gain100_20240301-140000_-10.0C_0001.fit')
# Two darks one calendar day apart — same imaging session straddling midnight
DARK_300_NEXT_DAY = _make('Dark_300.0s_Bin1_2600MC_gain100_20241002-020000_-10.0C_0001.fit')
BIAS = _make('Bias_1.0ms_Bin1_2600MC_gain100_20241001-141500_-10.0C_0001.fit')
FLAT = _make('Flat_2.0s_Bin1_2600MC_gain100_20240923-190000_0.0C_0001.fit')


class TestBuildDarkIndex:
    def test_key_format(self):
        idx = build_dark_index([DARK_300])
        assert '2600MC|300.0s|-10' in idx

    def test_calendar_date_key(self):
        idx = build_dark_index([DARK_300])
        inner = idx['2600MC|300.0s|-10']
        assert '2024-10-01' in inner


class TestGroupSessions:
    def test_single_date_is_one_session(self):
        index = {'2024-10-01': ['a', 'b']}
        sessions = _group_sessions(index)
        assert len(sessions) == 1

    def test_consecutive_dates_merged_into_one_session(self):
        index = {'2024-10-01': ['a'], '2024-10-02': ['b']}
        sessions = _group_sessions(index)
        assert len(sessions) == 1
        _, dates, frames = sessions[0]
        assert len(frames) == 2

    def test_non_consecutive_dates_are_separate_sessions(self):
        index = {'2024-10-01': ['a'], '2024-10-10': ['b']}
        sessions = _group_sessions(index)
        assert len(sessions) == 2

    def test_three_consecutive_dates_one_session(self):
        index = {'2024-10-01': ['a'], '2024-10-02': ['b'], '2024-10-03': ['c']}
        sessions = _group_sessions(index)
        assert len(sessions) == 1
        _, _, frames = sessions[0]
        assert len(frames) == 3

    def test_gap_of_two_days_splits_sessions(self):
        index = {'2024-10-01': ['a'], '2024-10-03': ['b']}
        sessions = _group_sessions(index)
        assert len(sessions) == 2


class TestFindClosest:
    def test_closest_date_label_and_delta(self):
        idx = build_dark_index([DARK_300, DARK_300_OLD])
        inner = idx['2600MC|300.0s|-10']
        match = find_closest_calib(inner, '2024-09-15')
        # Oct 1 is 16 days away; Mar 1 is ~198 days away — closest session is Oct 1
        assert match.date_label == '2024-10-01'
        assert match.days_delta == 16

    def test_returns_only_closest_session_not_all(self):
        # Oct 1 and Mar 1 are distinct sessions — only the closest (Oct 1) is returned
        idx = build_dark_index([DARK_300, DARK_300_OLD])
        inner = idx['2600MC|300.0s|-10']
        match = find_closest_calib(inner, '2024-09-15')
        assert len(match.frames) == 1  # only DARK_300, not DARK_300_OLD

    def test_midnight_spanning_session_returns_both_days(self):
        # Oct 1 and Oct 2 are one session straddling midnight — both frames returned
        idx = build_dark_index([DARK_300, DARK_300_NEXT_DAY])
        inner = idx['2600MC|300.0s|-10']
        match = find_closest_calib(inner, '2024-09-15')
        assert len(match.frames) == 2

    def test_empty_index_returns_none(self):
        assert find_closest_calib({}, '2024-09-15') is None

    def test_prefer_master_wins_over_newer_individuals(self):
        # Master is older (Mar 1) but prefer_master=True — master should still win
        from astronight.fits_parser import FitsFrame
        from datetime import datetime
        master = FitsFrame(
            path=Path('MasterDark_300s.xisf'),
            frame_type='Dark', target=None, exposure=300.0, binning=1,
            gain=100, set_temp=-10, camera='2600MC',
            timestamp=datetime(2024, 3, 1, 14, 0, 0),
            night_label='2024-03-01', calendar_date='2024-03-01', rotator=None,
        )
        # Build a mixed index: XISF master on Mar 1, individual .fit on Oct 1
        inner = {
            '2024-03-01': [master],
            '2024-10-01': [DARK_300],   # newer individual
        }
        match = find_closest_calib(inner, '2024-09-15', prefer_master=True)
        assert match.is_master
        assert match.frames == [master]

    def test_prefer_master_false_picks_newer_individual(self):
        # Without prefer_master, the newer individual wins over the older master
        from astronight.fits_parser import FitsFrame
        from datetime import datetime
        master = FitsFrame(
            path=Path('MasterDark_300s.xisf'),
            frame_type='Dark', target=None, exposure=300.0, binning=1,
            gain=100, set_temp=-10, camera='2600MC',
            timestamp=datetime(2024, 3, 1, 14, 0, 0),
            night_label='2024-03-01', calendar_date='2024-03-01', rotator=None,
        )
        inner = {
            '2024-03-01': [master],
            '2024-10-01': [DARK_300],
        }
        match = find_closest_calib(inner, '2024-09-15', prefer_master=False)
        assert not match.is_master
        assert match.frames == [DARK_300]


class TestResolveCalibration:
    def test_warns_when_no_darks(self):
        result = resolve_calibration(
            night_label='2024-09-15',
            camera='2600MC',
            exposure=300.0,
            set_temp=-10,
            dark_index={},
            bias_index={},
            flat_index={},
        )
        assert any('No darks' in w for w in result.warnings)

    def test_warns_when_old_darks(self):
        dark_idx = build_dark_index([DARK_300_OLD])  # 2024-03-01, ~198 days from night
        bias_idx = build_bias_index([BIAS])
        flat_idx = build_flat_index([FLAT])
        result = resolve_calibration(
            night_label='2024-09-15',
            camera='2600MC',
            exposure=300.0,
            set_temp=-10,
            dark_index=dark_idx,
            bias_index=bias_idx,
            flat_index=flat_idx,
        )
        assert any('183' in w or 'days' in w for w in result.warnings)
