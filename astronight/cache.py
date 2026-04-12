"""
SQLite-backed scan cache for FITS file metadata.

Cache key: (absolute_path, mtime, file_size)
On a cache hit all parsed FitsFrame fields are returned without opening the
FITS file, making repeated scans dramatically faster.

Cache location: %USERPROFILE%/.astronight/scan_cache.db  (Windows)
               ~/.astronight/scan_cache.db               (Unix)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .fits_parser import FitsFrame

# Bump this when FitsFrame fields change — forces a full re-parse on next run.
_SCHEMA_VERSION = 3


def default_cache_path() -> Path:
    return Path.home() / '.astronight' / 'scan_cache.db'


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS frames (
    path          TEXT    NOT NULL,
    mtime         REAL    NOT NULL,
    size          INTEGER NOT NULL,
    frame_type    TEXT    NOT NULL,
    target        TEXT,
    exposure      REAL    NOT NULL,
    binning       INTEGER NOT NULL,
    gain          INTEGER,
    set_temp      INTEGER,
    camera        TEXT    NOT NULL,
    timestamp      TEXT    NOT NULL,
    night_label    TEXT    NOT NULL,
    calendar_date  TEXT    NOT NULL,
    rotator        INTEGER,
    fn_timestamp   TEXT,
    PRIMARY KEY (path, mtime, size)
);
"""


# ---------------------------------------------------------------------------
# ScanCache class
# ---------------------------------------------------------------------------

class ScanCache:
    """SQLite-backed cache for parsed FITS frame metadata.

    Keyed by (absolute_path, mtime, size) so that unchanged files are never
    re-opened.  Must be created inside the thread that uses it — SQLite
    connections are not thread-safe across threads.

    Usage::

        with ScanCache() as cache:
            frames, errors = scan_fits(src, cache=cache)

    Or manually::

        cache = ScanCache()
        try:
            ...
        finally:
            cache.close()
    """

    def __init__(self, db_path: Optional[Path] = None):
        """Open (or create) the cache database at *db_path*.

        Args:
            db_path: Path to the SQLite file.  Defaults to
                     ``~/.astronight/scan_cache.db``.

        Two SQLite PRAGMAs are set for performance:
          - ``journal_mode=WAL``: Write-Ahead Logging allows concurrent readers
            while a write is in progress — important when the GUI triggers
            multiple scans.
          - ``synchronous=NORMAL``: Reduces fsync calls; safe for a cache
            (data loss on power failure is acceptable — the next scan will
            rebuild it).
        """
        self._path = db_path or default_cache_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._init_schema()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _init_schema(self):
        """Ensure the database schema matches _SCHEMA_VERSION.

        If no schema_version table exists (new database) or the stored version
        differs from _SCHEMA_VERSION, the frames table is dropped and recreated.
        This is intentionally destructive — the cache is just an optimisation
        and can always be rebuilt from the source FITS files.

        To force a rebuild after adding fields to FitsFrame, increment
        _SCHEMA_VERSION at the top of this module.
        """
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        has_version_table = cur.fetchone()[0] > 0

        if has_version_table:
            row = self._conn.execute('SELECT version FROM schema_version').fetchone()
            if row and row[0] == _SCHEMA_VERSION:
                return  # Schema is current
            # Version mismatch — wipe and rebuild
            self._conn.execute('DROP TABLE IF EXISTS frames')
            self._conn.execute('DROP TABLE IF EXISTS schema_version')

        for stmt in _DDL.strip().split(';'):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.execute('INSERT INTO schema_version VALUES (?)', (_SCHEMA_VERSION,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, path: Path, mtime: float, size: int) -> Optional[FitsFrame]:
        """Return a cached FitsFrame, or None on a cache miss."""
        row = self._conn.execute(
            'SELECT * FROM frames WHERE path=? AND mtime=? AND size=?',
            (str(path), mtime, size),
        ).fetchone()
        if row is None:
            return None
        return _row_to_frame(row, path)

    def put(self, frame: FitsFrame, mtime: float, size: int) -> None:
        """Insert or replace a cache entry."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO frames
              (path, mtime, size, frame_type, target, exposure, binning,
               gain, set_temp, camera, timestamp, night_label, calendar_date,
               rotator, fn_timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(frame.path), mtime, size,
                frame.frame_type, frame.target, frame.exposure, frame.binning,
                frame.gain, frame.set_temp, frame.camera,
                frame.timestamp.isoformat(), frame.night_label,
                frame.calendar_date, frame.rotator,
                frame.fn_timestamp.isoformat() if frame.fn_timestamp else None,
            ),
        )
        self._conn.commit()

    def put_batch(self, entries: list[tuple[FitsFrame, float, int]]) -> None:
        """Insert multiple entries in a single transaction (faster for bulk loads)."""
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO frames
              (path, mtime, size, frame_type, target, exposure, binning,
               gain, set_temp, camera, timestamp, night_label, calendar_date,
               rotator, fn_timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    str(f.path), mtime, size,
                    f.frame_type, f.target, f.exposure, f.binning,
                    f.gain, f.set_temp, f.camera,
                    f.timestamp.isoformat(), f.night_label,
                    f.calendar_date, f.rotator,
                    f.fn_timestamp.isoformat() if f.fn_timestamp else None,
                )
                for f, mtime, size in entries
            ],
        )
        self._conn.commit()

    def stats(self) -> dict:
        row = self._conn.execute('SELECT COUNT(*) FROM frames').fetchone()
        return {'entries': row[0], 'db_path': str(self._path)}

    def clean(self, known_paths: Optional[set[str]] = None) -> int:
        """Remove entries for files that no longer exist on disk.

        If *known_paths* is provided, removes entries not in that set
        (faster than hitting the filesystem for each row).
        Otherwise checks each path individually.
        Returns number of rows deleted.
        """
        if known_paths is not None:
            cur = self._conn.execute('SELECT DISTINCT path FROM frames')
            stale = [r[0] for r in cur if r[0] not in known_paths]
        else:
            cur = self._conn.execute('SELECT DISTINCT path FROM frames')
            stale = [r[0] for r in cur if not Path(r[0]).exists()]

        if stale:
            self._conn.executemany(
                'DELETE FROM frames WHERE path=?', [(p,) for p in stale]
            )
            self._conn.commit()
        return len(stale)


# ---------------------------------------------------------------------------
# Row → FitsFrame
# ---------------------------------------------------------------------------

def _row_to_frame(row: sqlite3.Row, path: Path) -> FitsFrame:
    """Reconstruct a FitsFrame from a SQLite row."""
    raw_fn_ts = row['fn_timestamp']
    return FitsFrame(
        path=path,
        frame_type=row['frame_type'],
        target=row['target'],
        exposure=row['exposure'],
        binning=row['binning'],
        gain=row['gain'],
        set_temp=row['set_temp'],
        camera=row['camera'],
        timestamp=datetime.fromisoformat(row['timestamp']),
        night_label=row['night_label'],
        calendar_date=row['calendar_date'],
        rotator=row['rotator'],
        fn_timestamp=datetime.fromisoformat(raw_fn_ts) if raw_fn_ts else None,
    )
