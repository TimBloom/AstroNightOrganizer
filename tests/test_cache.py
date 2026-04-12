"""Tests for cache.ScanCache."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from astronight.cache import ScanCache, _SCHEMA_VERSION
from astronight.fits_parser import FitsFrame


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_frame(path='/some/path/test.fit', target='M42', frame_type='Light', rotator=None):
    return FitsFrame(
        path=Path(path),
        frame_type=frame_type,
        target=target,
        exposure=300.0,
        binning=1,
        gain=100,
        set_temp=-10,
        camera='2600MC',
        timestamp=datetime(2024, 12, 1, 22, 0, 0),
        night_label='2024-12-01',
        calendar_date='2024-12-01',
        rotator=rotator,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScanCacheGetPut:
    def test_miss_returns_none(self, tmp_path):
        with ScanCache(tmp_path / 'cache.db') as cache:
            result = cache.get(Path('/nonexistent.fit'), 12345.0, 1024)
        assert result is None

    def test_put_then_get_returns_frame(self, tmp_path):
        frame = _make_frame()
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(frame, 1000.0, 2048)
            result = cache.get(Path('/some/path/test.fit'), 1000.0, 2048)
        assert result is not None
        assert result.frame_type == 'Light'
        assert result.target == 'M42'
        assert result.exposure == 300.0
        assert result.night_label == '2024-12-01'
        assert result.camera == '2600MC'

    def test_wrong_mtime_is_cache_miss(self, tmp_path):
        frame = _make_frame()
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(frame, 1000.0, 2048)
            result = cache.get(frame.path, 9999.0, 2048)
        assert result is None

    def test_wrong_size_is_cache_miss(self, tmp_path):
        frame = _make_frame()
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(frame, 1000.0, 2048)
            result = cache.get(frame.path, 1000.0, 9999)
        assert result is None

    def test_put_replaces_existing_entry(self, tmp_path):
        frame1 = _make_frame(target='M42')
        frame2 = _make_frame(target='NGC6960')
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(frame1, 1000.0, 2048)
            cache.put(frame2, 1000.0, 2048)
            result = cache.get(frame2.path, 1000.0, 2048)
        assert result.target == 'NGC6960'

    def test_rotator_none_preserved(self, tmp_path):
        frame = _make_frame(rotator=None)
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(frame, 1.0, 100)
            result = cache.get(frame.path, 1.0, 100)
        assert result.rotator is None

    def test_rotator_value_preserved(self, tmp_path):
        frame = _make_frame(rotator=106)
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(frame, 1.0, 100)
            result = cache.get(frame.path, 1.0, 100)
        assert result.rotator == 106

    def test_target_none_preserved(self, tmp_path):
        frame = _make_frame(frame_type='Dark', target=None)
        frame = FitsFrame(
            path=Path('/dark.fit'), frame_type='Dark', target=None,
            exposure=300.0, binning=1, gain=100, set_temp=-10, camera='2600MC',
            timestamp=datetime(2024, 12, 1, 14, 0, 0),
            night_label='2024-12-01', calendar_date='2024-12-01', rotator=None,
        )
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(frame, 1.0, 100)
            result = cache.get(frame.path, 1.0, 100)
        assert result.target is None


class TestScanCacheBatch:
    def test_put_batch_stores_all_entries(self, tmp_path):
        frames = [_make_frame(f'/path/{i}.fit', target=f'Target{i}') for i in range(5)]
        entries = [(f, float(i), 512) for i, f in enumerate(frames)]
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put_batch(entries)
            for i, frame in enumerate(frames):
                result = cache.get(frame.path, float(i), 512)
                assert result is not None
                assert result.target == f'Target{i}'

    def test_put_batch_empty_list(self, tmp_path):
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put_batch([])
            assert cache.stats()['entries'] == 0


class TestScanCacheStats:
    def test_stats_entry_count(self, tmp_path):
        with ScanCache(tmp_path / 'cache.db') as cache:
            assert cache.stats()['entries'] == 0
            cache.put(_make_frame('/a.fit'), 1.0, 100)
            cache.put(_make_frame('/b.fit'), 2.0, 100)
            assert cache.stats()['entries'] == 2

    def test_stats_includes_db_path(self, tmp_path):
        db = tmp_path / 'cache.db'
        with ScanCache(db) as cache:
            assert str(db) in cache.stats()['db_path']


class TestScanCacheClean:
    def test_clean_removes_nonexistent_paths(self, tmp_path):
        frame = _make_frame('/definitely/does/not/exist.fit')
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(frame, 1.0, 100)
            removed = cache.clean()
        assert removed == 1

    def test_clean_keeps_existing_paths(self, tmp_path):
        real_file = tmp_path / 'real.fit'
        real_file.write_bytes(b'data')
        frame = _make_frame(str(real_file))
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(frame, 1.0, 100)
            removed = cache.clean()
        assert removed == 0

    def test_clean_with_known_paths_removes_excluded(self, tmp_path):
        f1 = _make_frame('/path/keep.fit')
        f2 = _make_frame('/path/drop.fit')
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(f1, 1.0, 100)
            cache.put(f2, 2.0, 100)
            # Use str(path) so the key format matches what the cache stores on all platforms
            removed = cache.clean(known_paths={str(f1.path)})
        assert removed == 1

    def test_clean_with_known_paths_keeps_included(self, tmp_path):
        frame = _make_frame('/path/keep.fit')
        with ScanCache(tmp_path / 'cache.db') as cache:
            cache.put(frame, 1.0, 100)
            cache.clean(known_paths={str(frame.path)})
            assert cache.stats()['entries'] == 1


class TestScanCacheSchema:
    def test_old_schema_version_triggers_rebuild(self, tmp_path):
        db = tmp_path / 'cache.db'
        conn = sqlite3.connect(db)
        conn.execute('CREATE TABLE schema_version (version INTEGER)')
        conn.execute('INSERT INTO schema_version VALUES (0)')
        conn.execute('''CREATE TABLE frames (
            path TEXT, mtime REAL, size INTEGER, frame_type TEXT, target TEXT,
            exposure REAL, binning INTEGER, gain INTEGER, set_temp INTEGER,
            camera TEXT, timestamp TEXT, night_label TEXT, calendar_date TEXT,
            rotator INTEGER, PRIMARY KEY (path, mtime, size))''')
        conn.commit()
        conn.close()

        with ScanCache(db) as cache:
            assert cache.stats()['entries'] == 0

    def test_current_schema_not_rebuilt(self, tmp_path):
        db = tmp_path / 'cache.db'
        frame = _make_frame()
        with ScanCache(db) as cache:
            cache.put(frame, 1.0, 100)
        # Re-opening should NOT wipe the data
        with ScanCache(db) as cache:
            assert cache.stats()['entries'] == 1

    def test_context_manager_closes_connection(self, tmp_path):
        db = tmp_path / 'cache.db'
        with ScanCache(db) as cache:
            cache.put(_make_frame(), 1.0, 100)
        # After close, the db file should exist and be readable
        conn = sqlite3.connect(db)
        count = conn.execute('SELECT COUNT(*) FROM frames').fetchone()[0]
        conn.close()
        assert count == 1
