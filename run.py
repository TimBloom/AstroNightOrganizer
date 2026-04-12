#!/usr/bin/env python3
"""
AstroNightOrganizer launcher.

Double-click this file, or run:  python run.py

On first run it installs all required dependencies automatically,
then opens the AstroNightOrganizer GUI in your web browser.
"""

import sys
import os
import subprocess

# ---------------------------------------------------------------------------
# Python version check — must happen before any other imports
# ---------------------------------------------------------------------------

MIN_PYTHON = (3, 13)

if sys.version_info < MIN_PYTHON:
    print()
    print("=" * 60)
    print("  AstroNightOrganizer requires Python 3.13 or newer.")
    print(f"  You are running Python {sys.version.split()[0]}.")
    print()
    print("  Download the latest Python from:")
    print("  https://www.python.org/downloads/")
    print("=" * 60)
    print()
    if sys.platform == "win32":
        os.system("pause")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Dependency installation
# ---------------------------------------------------------------------------

def _find_installer():
    """Return a pip install command that works in this environment.

    Tries (in order):
      1. uv pip  — works inside uv-managed virtualenvs (no pip required)
      2. pip     — standard Python installs
    """
    # Try uv first
    uv = subprocess.run(["uv", "--version"], capture_output=True)
    if uv.returncode == 0:
        return ["uv", "pip", "install"]

    # Fall back to python -m pip
    pip_check = subprocess.run(
        [sys.executable, "-m", "pip", "--version"], capture_output=True
    )
    if pip_check.returncode == 0:
        return [sys.executable, "-m", "pip", "install"]

    return None


def _is_installed(package: str) -> bool:
    """Return True if *package* is importable in the current environment."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {package}"],
        capture_output=True,
    )
    return result.returncode == 0


def ensure_dependencies():
    """Install the package and all dependencies if not already present."""

    # Quick check: if nicegui and astronight are both importable, we're good
    if _is_installed("nicegui") and _is_installed("astronight"):
        return

    print()
    print("=" * 60)
    print("  AstroNightOrganizer — First-time setup")
    print("  Installing dependencies (this only happens once)...")
    print("=" * 60)
    print()

    installer = _find_installer()
    if installer is None:
        print("ERROR: Neither 'uv' nor 'pip' could be found.")
        print("Please install pip or uv, then run:")
        print(f"  pip install -e \"{os.path.dirname(os.path.abspath(__file__))}\"")
        print()
        if sys.platform == "win32":
            os.system("pause")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    cmd = installer + ["-e", script_dir]
    result = subprocess.run(cmd, text=True)

    if result.returncode != 0:
        print()
        print("ERROR: Dependency installation failed.")
        print("Please try running this manually:")
        print(f"  pip install -e \"{script_dir}\"")
        print()
        if sys.platform == "win32":
            os.system("pause")
        sys.exit(1)

    print()
    print("  Setup complete!")
    print()


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ensure_dependencies()

    # Now safe to import the app (deps are guaranteed to be present)
    try:
        from astronight.gui import run_gui
    except ImportError as e:
        print(f"\nFailed to import AstroNightOrganizer: {e}")
        print("Try running:  pip install -e .")
        if sys.platform == "win32":
            os.system("pause")
        sys.exit(1)

    print("Starting AstroNightOrganizer — opening in your browser...")
    print("Press Ctrl+C to quit.")
    print()

    run_gui()
