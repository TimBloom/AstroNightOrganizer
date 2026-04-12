"""Tests for file_ops: copy_frame, night index, and scan_fits."""

from pathlib import Path

import pytest

from astronight.file_ops import (
    copy_frame,
    next_night_number,
    read_night_index,
    scan_fits,
    write_night_index,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fits(path: Path, frame_type='Light', target='M42', exposure=300.0,
               camera='ZWO ASI2600MC', date_obs='2024-12-01T22:00:00'):
    """Write a minimal but valid FITS file using astropy."""
    from astropy.io import fits
    hdr = fits.Header()
    hdr['IMAGETYP'] = frame_type
    hdr['OBJECT']   = target
    hdr['EXPOSURE'] = exposure
    hdr['INSTRUME'] = camera
    hdr['DATE-OBS'] = date_obs
    hdr['XBINNING'] = 1
    hdr['GAIN']     = 100
    hdr['SET-TEMP'] = -10.0
    fits.PrimaryHDU(header=hdr).writeto(str(path), overwrite=True)


# ---------------------------------------------------------------------------
# copy_frame
# ---------------------------------------------------------------------------

class TestCopyFrame:
    def test_copies_new_file(self, tmp_path):
        src = tmp_path / 'src' / 'test.fit'
        src.parent.mkdir()
        src.write_bytes(b'FITS content')
        dest_dir = tmp_path / 'dest'

        action, dest = copy_frame(src, dest_dir)

        assert action == 'copied'
        assert dest.exists()
        assert dest.read_bytes() == b'FITS content'

    def test_skips_identical_existing_file(self, tmp_path):
        data = b'identical FITS content'
        src = tmp_path / 'src' / 'test.fit'
        src.parent.mkdir()
        src.write_bytes(data)
        dest_dir = tmp_path / 'dest'
        dest_dir.mkdir()
        (dest_dir / 'test.fit').write_bytes(data)

        action, dest = copy_frame(src, dest_dir)

        assert action == 'skipped'

    def test_renames_when_different_file_exists(self, tmp_path):
        src = tmp_path / 'src' / 'test.fit'
        src.parent.mkdir()
        src.write_bytes(b'new content')
        dest_dir = tmp_path / 'dest'
        dest_dir.mkdir()
        (dest_dir / 'test.fit').write_bytes(b'different existing content')

        action, dest = copy_frame(src, dest_dir)

        assert action == 'renamed'
        assert dest.name == 'test_dup1.fit'
        assert dest.read_bytes() == b'new content'

    def test_increments_dup_number_when_dup1_exists(self, tmp_path):
        src = tmp_path / 'src' / 'test.fit'
        src.parent.mkdir()
        src.write_bytes(b'new content')
        dest_dir = tmp_path / 'dest'
        dest_dir.mkdir()
        (dest_dir / 'test.fit').write_bytes(b'original')
        (dest_dir / 'test_dup1.fit').write_bytes(b'dup1')

        action, dest = copy_frame(src, dest_dir)

        assert action == 'renamed'
        assert dest.name == 'test_dup2.fit'

    def test_creates_dest_dir_if_missing(self, tmp_path):
        src = tmp_path / 'test.fit'
        src.write_bytes(b'data')
        dest_dir = tmp_path / 'nested' / 'subdir'

        action, dest = copy_frame(src, dest_dir)

        assert action == 'copied'
        assert dest_dir.exists()

    def test_preserves_filename(self, tmp_path):
        src = tmp_path / 'Light_M42_300.0s_Bin1_0001.fit'
        src.write_bytes(b'data')
        dest_dir = tmp_path / 'dest'

        _, dest = copy_frame(src, dest_dir)

        assert dest.name == 'Light_M42_300.0s_Bin1_0001.fit'


# ---------------------------------------------------------------------------
# Night index
# ---------------------------------------------------------------------------

class TestNightIndex:
    def test_read_returns_empty_when_file_missing(self, tmp_path):
        assert read_night_index(tmp_path) == {}

    def test_write_then_read_round_trip(self, tmp_path):
        index = {
            'M42/2600MC/300.0s': 3,
            'NGC6960/2600MC/60.0s': 1,
        }
        write_night_index(tmp_path, index)
        loaded = read_night_index(tmp_path)
        assert loaded == index

    def test_write_creates_parent_dirs(self, tmp_path):
        write_night_index(tmp_path, {'A/B/C': 1})
        assert (tmp_path / '_INFO' / 'night-index.csv').exists()

    def test_write_overwrites_existing(self, tmp_path):
        write_night_index(tmp_path, {'A/B/C': 1})
        write_night_index(tmp_path, {'A/B/C': 5})
        assert read_night_index(tmp_path) == {'A/B/C': 5}


class TestNextNightNumber:
    def test_starts_at_one_for_new_subtree(self):
        index: dict = {}
        n = next_night_number(Path('.'), 'Target/Camera/300.0s', index)
        assert n == 1

    def test_increments_existing_entry(self):
        index = {'Target/Camera/300.0s': 2}
        n = next_night_number(Path('.'), 'Target/Camera/300.0s', index)
        assert n == 3

    def test_updates_index_in_place(self):
        index: dict = {}
        next_night_number(Path('.'), 'Target/Camera/300.0s', index)
        assert index['Target/Camera/300.0s'] == 1

    def test_different_subtrees_are_independent(self):
        index: dict = {}
        n1 = next_night_number(Path('.'), 'M42/2600MC/300.0s', index)
        n2 = next_night_number(Path('.'), 'NGC6960/2600MC/300.0s', index)
        assert n1 == 1
        assert n2 == 1

    def test_sequential_calls_increment(self):
        index: dict = {}
        subtree = 'M42/2600MC/300.0s'
        results = [next_night_number(Path('.'), subtree, index) for _ in range(4)]
        assert results == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# scan_fits
# ---------------------------------------------------------------------------

class TestScanFits:
    def test_empty_directory(self, tmp_path):
        frames, errors = scan_fits(tmp_path)
        assert frames == []
        assert errors == []

    def test_finds_dot_fit_file(self, tmp_path):
        _make_fits(tmp_path / 'test.fit')
        frames, errors = scan_fits(tmp_path)
        assert len(frames) == 1
        assert errors == []

    def test_finds_dot_fits_extension(self, tmp_path):
        _make_fits(tmp_path / 'test.fits')
        frames, errors = scan_fits(tmp_path)
        assert len(frames) == 1

    def test_reads_header_frame_type(self, tmp_path):
        _make_fits(tmp_path / 'light.fit', frame_type='Light')
        _make_fits(tmp_path / 'dark.fit',  frame_type='Dark')
        frames, _ = scan_fits(tmp_path)
        types = {f.frame_type for f in frames}
        assert types == {'Light', 'Dark'}

    def test_reads_header_target(self, tmp_path):
        _make_fits(tmp_path / 'test.fit', target='NGC6960')
        frames, _ = scan_fits(tmp_path)
        assert frames[0].target == 'NGC6960'

    def test_reads_header_exposure(self, tmp_path):
        _make_fits(tmp_path / 'test.fit', exposure=120.0)
        frames, _ = scan_fits(tmp_path)
        assert frames[0].exposure == 120.0

    def test_skips_stacked_by_filename(self, tmp_path):
        # Even with a valid FITS header, SKIP_RE matches the filename first in parse_frame
        _make_fits(tmp_path / 'Stacked_M42_300.0s_Bin1_2600MC_gain100_20241201-220000_-10.0C_0001.fit')
        frames, _ = scan_fits(tmp_path)
        assert frames == []

    def test_scans_subdirectories_recursively(self, tmp_path):
        subdir = tmp_path / 'ASIAIR' / 'Autorun' / 'M42'
        subdir.mkdir(parents=True)
        _make_fits(subdir / 'light.fit')
        frames, _ = scan_fits(tmp_path)
        assert len(frames) == 1

    def test_multiple_files(self, tmp_path):
        for i in range(5):
            _make_fits(tmp_path / f'light_{i}.fit')
        frames, _ = scan_fits(tmp_path)
        assert len(frames) == 5

    def test_progress_callback_receives_all_files(self, tmp_path):
        for i in range(3):
            _make_fits(tmp_path / f'light_{i}.fit')
        calls = []
        scan_fits(tmp_path, progress_callback=lambda d, t, n: calls.append((d, t)))
        assert len(calls) == 3
        assert calls[-1] == (3, 3)

    def test_cache_hit_avoids_reparse(self, tmp_path):
        from astronight.cache import ScanCache
        p = tmp_path / 'test.fit'
        _make_fits(p, target='CachedTarget')

        # First scan populates the cache
        with ScanCache(tmp_path / 'cache.db') as cache:
            frames1, _ = scan_fits(tmp_path, cache=cache)

        assert len(frames1) == 1
        assert frames1[0].target == 'CachedTarget'

        # Second scan with the same unmodified file should hit the cache
        with ScanCache(tmp_path / 'cache.db') as cache:
            frames2, errors2 = scan_fits(tmp_path, cache=cache)

        assert len(frames2) == 1
        assert frames2[0].target == 'CachedTarget'
        assert errors2 == []
