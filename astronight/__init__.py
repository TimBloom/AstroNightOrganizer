"""
AstroNightOrganizer — organise astrophotography FITS files into a
WBPP-compatible folder structure for PixInsight.

Public API surface (everything else is considered internal):
  - ``astronight.sorter.build_groups`` / ``copy_groups``
  - ``astronight.fits_parser.parse_frame``
  - ``astronight.file_ops.scan_fits``
  - ``astronight.calibration.resolve_calibration``
  - ``astronight.catalog.resolve_name`` / ``download_openngc``
  - ``astronight.cache.ScanCache``
  - ``astronight.__version__``
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__: str = version('astronightorganizer')
except PackageNotFoundError:
    __version__ = 'unknown'
