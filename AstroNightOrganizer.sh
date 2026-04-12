#!/usr/bin/env bash
# AstroNightOrganizer launcher — double-click or run in terminal to start the app
# Uses uv to manage the Python environment automatically

if ! command -v uv &>/dev/null; then
    echo
    echo "============================================================"
    echo "  AstroNightOrganizer requires 'uv' to be installed."
    echo "  Install it from:  https://docs.astral.sh/uv/"
    echo "  Or via your terminal:"
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "============================================================"
    echo
    # Keep the terminal window open if launched by double-click
    read -r -p "Press Enter to close..."
    exit 1
fi

cd "$(dirname "$0")"
uv run python run.py
