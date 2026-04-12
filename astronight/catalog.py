"""
Object name catalog — resolves ASIAIR target names to canonical common names.

Uses the OpenNGC database (MIT licence, https://github.com/mattiaverga/OpenNGC).
The CSV is stored at ~/.astronight/openngc.csv and can be refreshed via
download_openngc() or the "Update catalog" button in the GUI.

Resolution priority:
  1. Common name from OpenNGC  (e.g. "Pinwheel Galaxy")
  2. Primary NGC/IC designation (e.g. "NGC 5457")
  3. Original name as-is        (e.g. "M101", "my_target")
"""

from __future__ import annotations

import csv
import re
import urllib.request
from pathlib import Path
from typing import Optional

OPENNGC_PATH = Path.home() / '.astronight' / 'openngc.csv'
OPENNGC_URL  = (
    'https://github.com/mattiaverga/OpenNGC/raw/master/database_files/NGC.csv'
)

# Module-level cache: normalised_alias -> canonical display name
_catalog: Optional[dict[str, str]] = None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_openngc(dest: Path = OPENNGC_PATH) -> None:
    """Download the OpenNGC CSV from GitHub and save it to *dest*."""
    _MAX_BYTES = 20 * 1024 * 1024  # 20 MB — OpenNGC is ~3 MB; this is a generous ceiling
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(OPENNGC_URL, timeout=30) as resp:
        data = resp.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        raise ValueError(f"Downloaded file exceeds {_MAX_BYTES // (1024*1024)} MB — aborting")
    dest.write_bytes(data)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

_NORM_RE = re.compile(r'\s+')

def _normalise(raw: str) -> str:
    """Return a normalised lookup key for *raw*.

    Rules:
    - Strip surrounding whitespace, collapse internal whitespace
    - Upper-case
    - Remove leading zeros from the numeric suffix of NGC/IC/M designations
      so "NGC0001" and "NGC1" both become "NGC1"
    """
    s = _NORM_RE.sub(' ', raw.strip()).upper()
    # Collapse "M 101" → "M101", "NGC 5457" → "NGC5457" etc.
    s = re.sub(r'^(NGC|IC|M)\s+', r'\1', s)
    # Strip leading zeros from numeric suffix: "NGC0001" → "NGC1"
    s = re.sub(r'^(NGC|IC|M)0*(\d+)', lambda m: m.group(1) + m.group(2), s)
    return s


def _ngc_display(raw_name: str) -> str:
    """Format a raw OpenNGC Name field (e.g. "NGC5457") as "NGC 5457"."""
    m = re.match(r'^(NGC|IC)(\d+)', raw_name.strip())
    if m:
        return f"{m.group(1)} {int(m.group(2))}"
    return raw_name.strip()


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

def _build_lookup(csv_path: Path) -> dict[str, str]:
    """Parse the OpenNGC CSV and return a normalised-alias → display-name map.

    For each row the canonical display name is:
      - First common name (if present), else primary NGC/IC designation.

    Aliases mapped to that canonical name:
      - NGC/IC designation (e.g. "NGC5457")
      - All Messier aliases  (e.g. "M101")
      - All common-name tokens (e.g. "PINWHEEL GALAXY")
    """
    lookup: dict[str, str] = {}

    with csv_path.open(newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=';')
        for row in reader:
            raw_name     = row.get('Name', '').strip()
            messier_raw  = row.get('M', '').strip()
            common_raw   = row.get('Common names', '').strip()

            if not raw_name:
                continue

            # Canonical display name
            if common_raw:
                # May have multiple names separated by ", " — take the first
                canonical = common_raw.split(',')[0].strip()
            else:
                canonical = _ngc_display(raw_name)

            # Register aliases
            aliases: list[str] = [_normalise(raw_name)]
            if messier_raw:
                aliases.append(_normalise(f'M{messier_raw}'))
            if common_raw:
                for cn in common_raw.split(','):
                    cn = cn.strip()
                    if cn:
                        aliases.append(_normalise(cn))

            for alias in aliases:
                # First writer wins — keeps the most-specific canonical name
                if alias and alias not in lookup:
                    lookup[alias] = canonical

    return lookup


def load_catalog(force: bool = False) -> dict[str, str]:
    """Return the loaded catalog lookup dict (cached after first call).

    If *force* is True, reload from disk even if already cached.
    If the CSV does not exist, returns an empty dict (graceful degradation).
    """
    global _catalog
    if _catalog is not None and not force:
        return _catalog

    if not OPENNGC_PATH.exists():
        _catalog = {}
        return _catalog

    try:
        _catalog = _build_lookup(OPENNGC_PATH)
    except Exception:
        _catalog = {}

    return _catalog


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_name(raw: str) -> str:
    """Resolve *raw* to a canonical display name.

    Returns the common name if found in the catalog, then the primary NGC/IC
    designation, then *raw* unchanged as a last resort.
    """
    catalog = load_catalog()
    if not catalog:
        return raw

    key = _normalise(raw)
    return catalog.get(key, raw)
