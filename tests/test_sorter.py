"""Tests for sorter.build_groups."""

from datetime import datetime
from pathlib import Path

from astronight.fits_parser import FitsFrame
from astronight.sorter import build_groups


# ---------------------------------------------------------------------------
# Frame factories
# ---------------------------------------------------------------------------

def _light(target, camera, exposure, night, seq=1, set_temp=-10):
    ts = datetime.strptime(night + 'T22:00:00', '%Y-%m-%dT%H:%M:%S')
    return FitsFrame(
        path=Path(f'Light_{target}_{exposure}s_{camera}_{seq:04d}.fit'),
        frame_type='Light',
        target=target,
        exposure=exposure,
        binning=1,
        gain=100,
        set_temp=set_temp,
        camera=camera,
        timestamp=ts,
        night_label=night,
        calendar_date=night,
        rotator=None,
    )


def _dark(camera, exposure, night, seq=1, set_temp=-10):
    ts = datetime.strptime(night + 'T14:00:00', '%Y-%m-%dT%H:%M:%S')
    return FitsFrame(
        path=Path(f'Dark_{exposure}s_{camera}_{seq:04d}.fit'),
        frame_type='Dark',
        target=None,
        exposure=exposure,
        binning=1,
        gain=100,
        set_temp=set_temp,
        camera=camera,
        timestamp=ts,
        night_label=night,
        calendar_date=night,
        rotator=None,
    )


def _flat(camera, night, seq=1):
    ts = datetime.strptime(night + 'T19:00:00', '%Y-%m-%dT%H:%M:%S')
    return FitsFrame(
        path=Path(f'Flat_2.0s_{camera}_{seq:04d}.fit'),
        frame_type='Flat',
        target=None,
        exposure=2.0,
        binning=1,
        gain=100,
        set_temp=None,
        camera=camera,
        timestamp=ts,
        night_label=night,
        calendar_date=night,
        rotator=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBuildGroupsBasic:
    def test_empty_input_returns_empty(self):
        assert build_groups([]) == []

    def test_only_calibration_returns_empty(self):
        frames = [_dark('2600MC', 300.0, '2024-12-01')]
        assert build_groups(frames) == []

    def test_single_light_creates_one_group(self):
        frames = [_light('M42', '2600MC', 300.0, '2024-12-01')]
        groups = build_groups(frames)
        assert len(groups) == 1
        assert groups[0].key.target == 'M42'
        assert groups[0].key.camera == '2600MC'

    def test_calibration_frames_not_in_groups_list(self):
        frames = [
            _light('M42', '2600MC', 300.0, '2024-12-01'),
            _dark('2600MC', 300.0, '2024-12-01'),
        ]
        groups = build_groups(frames)
        assert len(groups) == 1

    def test_lights_on_same_night_aggregated(self):
        frames = [
            _light('M42', '2600MC', 300.0, '2024-12-01', seq=1),
            _light('M42', '2600MC', 300.0, '2024-12-01', seq=2),
            _light('M42', '2600MC', 300.0, '2024-12-01', seq=3),
        ]
        groups = build_groups(frames)
        assert len(groups) == 1
        assert len(groups[0].lights) == 3


class TestBuildGroupsSplitting:
    def test_two_nights_same_target_gives_two_groups(self):
        frames = [
            _light('M42', '2600MC', 300.0, '2024-12-01'),
            _light('M42', '2600MC', 300.0, '2024-12-02'),
        ]
        groups = build_groups(frames)
        assert len(groups) == 2
        assert all(g.key.target == 'M42' for g in groups)

    def test_two_targets_gives_two_groups(self):
        frames = [
            _light('M42',     '2600MC', 300.0, '2024-12-01'),
            _light('NGC6960', '2600MC', 300.0, '2024-12-01'),
        ]
        groups = build_groups(frames)
        targets = {g.key.target for g in groups}
        assert targets == {'M42', 'NGC6960'}

    def test_two_exposures_same_night_gives_two_groups(self):
        frames = [
            _light('M42', '2600MC',  60.0, '2024-12-01'),
            _light('M42', '2600MC', 300.0, '2024-12-01'),
        ]
        groups = build_groups(frames)
        assert len(groups) == 2

    def test_two_cameras_same_night_gives_two_groups(self):
        frames = [
            _light('M42', '2600MC', 300.0, '2024-12-01'),
            _light('M42', '585MC',  300.0, '2024-12-01'),
        ]
        groups = build_groups(frames)
        assert len(groups) == 2
        cameras = {g.key.camera for g in groups}
        assert cameras == {'2600MC', '585MC'}


class TestBuildGroupsSortOrder:
    def test_sorted_by_target_then_night(self):
        frames = [
            _light('NGC6960', '2600MC', 300.0, '2024-12-02'),
            _light('M42',     '2600MC', 300.0, '2024-12-01'),
            _light('M42',     '2600MC', 300.0, '2024-12-02'),
        ]
        groups = build_groups(frames)
        assert [g.key.target for g in groups] == ['M42', 'M42', 'NGC6960']
        assert groups[0].key.night_label == '2024-12-01'
        assert groups[1].key.night_label == '2024-12-02'

    def test_sorted_by_exposure_within_same_night(self):
        # Exposures are sorted numerically (60s before 300s), not lexicographically
        frames = [
            _light('M42', '2600MC', 300.0, '2024-12-01'),
            _light('M42', '2600MC',  60.0, '2024-12-01'),
        ]
        groups = build_groups(frames)
        assert groups[0].key.exposure_str == '60.0s'
        assert groups[1].key.exposure_str == '300.0s'


class TestBuildGroupsCalibration:
    def test_dark_matched_to_group(self):
        frames = [
            _light('M42', '2600MC', 300.0, '2024-12-01', set_temp=-10),
            _dark('2600MC', 300.0, '2024-12-01', set_temp=-10),
        ]
        groups = build_groups(frames)
        assert len(groups[0].calib.darks) == 1

    def test_flat_matched_to_same_night(self):
        frames = [
            _light('M42', '2600MC', 300.0, '2024-12-01'),
            _flat('2600MC', '2024-12-01'),
        ]
        groups = build_groups(frames)
        assert len(groups[0].calib.flats) == 1

    def test_flat_different_night_not_matched(self):
        frames = [
            _light('M42', '2600MC', 300.0, '2024-12-01'),
            _flat('2600MC', '2024-11-01'),
        ]
        groups = build_groups(frames)
        assert len(groups[0].calib.flats) == 0

    def test_no_darks_triggers_warning(self):
        frames = [_light('M42', '2600MC', 300.0, '2024-12-01')]
        groups = build_groups(frames)
        assert any('dark' in w.lower() for w in groups[0].calib.warnings)

    def test_no_flats_triggers_warning(self):
        frames = [_light('M42', '2600MC', 300.0, '2024-12-01')]
        groups = build_groups(frames)
        assert any('flat' in w.lower() for w in groups[0].calib.warnings)

    def test_no_warnings_when_all_calib_present(self):
        frames = [
            _light('M42', '2600MC', 300.0, '2024-12-01', set_temp=-10),
            _dark('2600MC', 300.0, '2024-12-01', set_temp=-10),
            _flat('2600MC', '2024-12-01'),
        ]
        # Bias will still warn since none provided — only check dark/flat warnings are absent
        groups = build_groups(frames)
        dark_flat_warns = [
            w for w in groups[0].calib.warnings
            if 'dark' in w.lower() or 'flat' in w.lower()
        ]
        assert dark_flat_warns == []
