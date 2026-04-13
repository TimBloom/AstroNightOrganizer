"""
NiceGUI-based graphical interface for AstroNightOrganizer.

Launched via:  python run.py
           or: astronight gui

Architecture overview
---------------------
The UI is a single-page NiceGUI app served on localhost.  All state lives in
the module-level ``AppState`` singleton; NiceGUI widgets hold no app state
themselves.

Folder pickers use a tkinter ``filedialog`` dialog run server-side in an
executor thread.  This gives the user a native OS folder picker with a real
full path, rather than the browser's file-upload dialog which only exposes
relative paths.

Scanning runs in an executor thread to avoid blocking the NiceGUI event loop.
``ScanCache`` must be constructed inside that thread (SQLite connections are
not thread-safe across threads).

The groups table uses a factory-function pattern for event handlers::

    def make_handler(captured_value):
        def handler(e): ...  # uses captured_value
        return handler

This is necessary because Python closures capture variables by reference.
Without the factory, all handlers in a loop would share the same loop variable.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from contextlib import contextmanager
import subprocess

from nicegui import app, ui
from nicegui.events import ValueChangeEventArguments

from .cache import ScanCache
from .file_ops import scan_fits
from .sorter import LightGroup, build_groups, copy_groups


@contextmanager
def _caffeinate():
    """Prevent macOS idle sleep for the duration of the block.

    Spawns ``caffeinate -i`` on macOS, which holds a power assertion that
    blocks idle sleep until the process exits.  On other platforms this is
    a no-op so the same call site works everywhere.
    """
    if sys.platform != 'darwin':
        yield
        return
    proc = subprocess.Popen(['caffeinate', '-i'])
    try:
        yield
    finally:
        proc.terminate()
        proc.wait()


# ---------------------------------------------------------------------------
# State shared across the page
# ---------------------------------------------------------------------------

class AppState:
    def __init__(self):
        self.source: Optional[Path] = None
        self.destination: Optional[Path] = None
        self.target_filter: str = ''
        self.frames: list = []
        self.groups: list[LightGroup] = []
        self.selected: set[int] = set()
        self.log_path: Optional[Path] = None
        # Optional extra calibration folders, keyed by frame type
        self.extra_calib: dict[str, Optional[Path]] = {
            'Dark': None,
            'Bias': None,
            'Flat': None,
        }

state = AppState()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def setup_log(dest: Path) -> Path:
    """Configure the root logger to write to a timestamped file under *dest*.

    Creates ``dest/_IMPORT_LOGS/import-<stamp>.log`` and returns the path.
    Uses ``force=True`` so that reconfiguring the logger across multiple sort
    runs in the same session works correctly.
    """
    log_dir = dest / '_IMPORT_LOGS'
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    log_path = log_dir / f'import-{stamp}.log'
    logging.basicConfig(
        filename=str(log_path),
        level=logging.INFO,
        format='%(asctime)s  %(message)s',
        datefmt='%H:%M:%S',
        force=True,
    )
    return log_path


def folder_picker(label: str, input_ref: ui.input, placeholder: str):
    """A full-width path input with a Browse button that opens a native folder dialog."""
    ui.label(label).classes('text-sm font-medium text-gray-600 mb-1')
    with ui.row().classes('w-full items-center gap-2'):
        inp = ui.input(placeholder=placeholder).classes('flex-1 font-mono text-sm')

        async def browse():
            result = await ui.run_javascript('''
                return new Promise((resolve) => {
                    const input = document.createElement("input");
                    input.type = "file";
                    input.webkitdirectory = true;
                    input.onchange = (e) => {
                        const files = e.target.files;
                        if (files.length > 0) {
                            // Return the common path prefix
                            const path = files[0].webkitRelativePath.split("/")[0];
                            resolve(files[0].path || path);
                        } else {
                            resolve(null);
                        }
                    };
                    input.click();
                });
            ''')
            if result:
                inp.set_value(result)

        ui.button(icon='folder_open', on_click=browse).props(
            'flat dense'
        ).classes('text-indigo-500').tooltip('Browse for folder')

    # Copy widget reference back to caller's variable via closure
    input_ref._wrapped = inp
    return inp


# ---------------------------------------------------------------------------
# Main page
# ---------------------------------------------------------------------------

def create_gui():
    """Register the NiceGUI page routes.  Call once before ``ui.run()``."""
    @ui.page('/')
    def index():
        # group_rows: flat list of {cb, idx} dicts for All/None bulk selection.
        # all_parent_cbs: list of parent checkboxes for bulk select/deselect.
        group_rows: list[dict] = []
        all_parent_cbs: list = []

        # ------------------------------------------------------------------
        # Header
        # ------------------------------------------------------------------
        from astronight import __version__

        with ui.header().classes(
            'bg-indigo-700 text-white items-center gap-4 px-8 py-4'
        ):
            ui.label('🔭 AstroNightOrganizer').classes('text-2xl font-bold tracking-wide')
            with ui.column().classes('flex-1 gap-0'):
                ui.label('ASIAIR → PixInsight WBPP').classes('text-indigo-200 text-sm')
                ui.label(f'v{__version__}').classes('text-indigo-300 text-xs')

            async def update_catalog():
                from .catalog import download_openngc, load_catalog
                catalog_btn.disable()
                catalog_status.visible = True
                catalog_status.set_text('Downloading…')
                try:
                    await asyncio.get_event_loop().run_in_executor(None, download_openngc)
                    load_catalog(force=True)
                    catalog_status.set_text('Updated')
                    ui.notify('Star catalog updated.', type='positive')
                except Exception as exc:
                    catalog_status.set_text('Failed')
                    ui.notify(f'Catalog update failed: {exc}', type='negative')
                finally:
                    catalog_btn.enable()

            catalog_status = ui.label('').classes('text-indigo-200 text-xs')
            catalog_status.visible = False
            catalog_btn = ui.button('Update catalog', icon='auto_awesome').props(
                'flat dense'
            ).classes('text-indigo-100 text-xs').tooltip(
                'Re-download OpenNGC object name database'
            )
            catalog_btn.on('click', update_catalog)

        # ------------------------------------------------------------------
        # Main content
        # ------------------------------------------------------------------
        with ui.column().classes('w-full max-w-4xl mx-auto px-6 py-4 gap-3'):

            # ---- Step 1: Folders ----------------------------------------
            with ui.card().classes('w-full shadow-sm p-4'):
                with ui.row().classes('items-center gap-2 mb-3'):
                    ui.badge('1').props('color=indigo rounded')
                    ui.label('Choose folders').classes('text-base font-semibold')

                # Source + Destination side by side
                with ui.grid(columns=2).classes('w-full gap-x-4 gap-y-1 mb-2'):
                    with ui.column().classes('gap-0'):
                        ui.label('Source folder').classes('text-xs font-medium text-gray-500')
                        with ui.row().classes('w-full items-center gap-1'):
                            source_input = ui.input(
                                placeholder='e.g. D:\\ASIAIR\\Archive'
                            ).classes('flex-1 font-mono text-xs')
                            ui.button(icon='folder_open').props('flat dense').classes(
                                'text-indigo-500'
                            ).tooltip('Browse').on('click', lambda: _browse(source_input))

                    with ui.column().classes('gap-0'):
                        ui.label('Destination folder').classes('text-xs font-medium text-gray-500')
                        with ui.row().classes('w-full items-center gap-1'):
                            dest_input = ui.input(
                                placeholder='e.g. D:\\WBPPImport\\'
                            ).classes('flex-1 font-mono text-xs')
                            ui.button(icon='folder_open').props('flat dense').classes(
                                'text-indigo-500'
                            ).tooltip('Browse').on('click', lambda: _browse(dest_input))

                ui.separator().classes('my-2')

                with ui.column().classes('gap-1 w-full'):
                    with ui.row().classes('items-center gap-2'):
                        ui.label('Extra calibration folders').classes('text-xs font-medium text-gray-500')
                        ui.badge('optional').props('color=grey outline').classes('text-xs')

                    calib_inputs:   dict[str, ui.input]    = {}
                    calib_enabled:  dict[str, ui.checkbox] = {}
                    calib_browse:   dict[str, ui.button]   = {}
                    for frame_type, label, icon, color in [
                        ('Dark', 'Darks',  'dark_mode', 'text-gray-500'),
                        ('Bias', 'Biases', 'exposure',  'text-indigo-400'),
                        ('Flat', 'Flats',  'wb_sunny',  'text-yellow-500'),
                    ]:
                        with ui.row().classes('w-full items-center gap-1'):
                            ui.icon(icon).classes(f'{color} text-base w-5 shrink-0')
                            enabled = ui.checkbox(label).classes('text-xs shrink-0').props('dense')
                            inp = ui.input(
                                placeholder=f'{label} folder…'
                            ).classes('flex-1 font-mono text-xs')
                            inp.disable()
                            browse_btn = ui.button(icon='folder_open').props('flat dense').classes(
                                'text-indigo-400'
                            ).tooltip(f'Browse for {label.lower()} folder')
                            browse_btn.disable()

                            def make_toggle(i, b, ft):
                                """Return a checkbox handler that enables/disables the
                                paired input *i* and browse button *b*, clearing the
                                path and resetting state when unchecked."""
                                def toggle(e):
                                    if e.args:
                                        i.enable(); b.enable()
                                    else:
                                        i.disable(); b.disable()
                                        i.set_value('')
                                        state.extra_calib[ft] = None
                                return toggle

                            enabled.on('update:model-value', make_toggle(inp, browse_btn, frame_type))

                            def make_browse(i):
                                """Return an async click handler that opens the folder
                                picker for input widget *i*."""
                                async def do():
                                    await _browse(i)
                                return do

                            browse_btn.on('click', make_browse(inp))
                            calib_inputs[frame_type]  = inp
                            calib_enabled[frame_type] = enabled
                            calib_browse[frame_type]  = browse_btn

            # ---- Step 2: Scan -------------------------------------------
            with ui.card().classes('w-full shadow-sm p-4'):
                with ui.row().classes('items-center gap-3'):
                    ui.badge('2').props('color=indigo rounded')
                    ui.label('Scan').classes('text-base font-semibold')
                    scan_btn = ui.button('Scan', icon='search').props('dense').classes(
                        'bg-indigo-600 text-white'
                    )
                    scan_status = ui.label(
                        'Enter folders above, then scan.'
                    ).classes('text-xs text-gray-400')

                scan_progress = ui.linear_progress(value=0, show_value=False).props('instant-feedback').classes('w-full mt-2')
                scan_progress.visible = False

            # ---- Step 3: Groups -----------------------------------------
            groups_card = ui.card().classes('w-full shadow-sm p-4')
            groups_card.visible = False
            with groups_card:
                with ui.row().classes('items-center justify-between mb-2 flex-wrap gap-2'):
                    with ui.row().classes('items-center gap-2'):
                        ui.badge('3').props('color=indigo rounded')
                        ui.label('Select groups').classes('text-base font-semibold')
                    with ui.row().classes('items-center gap-2'):
                        target_input = ui.input(placeholder='Filter target…').props(
                            'dense outlined clearable'
                        ).classes('text-xs').style('min-width:160px')
                        camera_select = ui.select(
                            ['All cameras'], value='All cameras', label='Camera'
                        ).props('dense outlined').classes('text-xs').style('min-width:140px')
                        year_select = ui.select(
                            ['All years'], value='All years', label='Year'
                        ).props('dense outlined').classes('text-xs').style('min-width:100px')
                        sort_select = ui.select(
                            ['Target A→Z', 'Most recent', 'Most lights'],
                            value='Target A→Z',
                            label='Sort by',
                        ).props('dense outlined').classes('text-xs').style('min-width:130px')
                        sel_all_btn  = ui.button('All',  icon='done_all').props('flat dense outline')
                        sel_none_btn = ui.button('None', icon='remove_done').props('flat dense outline')

                with ui.element('div').classes('w-full overflow-y-auto rounded border border-gray-200').style('max-height: 600px'):
                    groups_table = ui.column().classes('w-full gap-0')

                # Warnings — collapsed scrollable pane, shown only when needed
                warnings_area = ui.column().classes('w-full gap-0')

            # ---- Step 4: Sort -------------------------------------------
            sort_card = ui.card().classes('w-full shadow-sm p-4')
            sort_card.visible = False
            with sort_card:
                with ui.row().classes('items-center gap-3'):
                    ui.badge('4').props('color=indigo rounded')
                    ui.label('Sort').classes('text-base font-semibold')
                    dry_run_btn = ui.button(
                        'Dry run', icon='visibility'
                    ).props('dense outline').classes('text-indigo-600 border-indigo-400')
                    sort_btn = ui.button(
                        'Sort selected', icon='drive_file_move'
                    ).props('dense').classes('bg-green-600 text-white')
                    sort_status = ui.label('').classes('text-xs text-gray-400')

                sort_progress = ui.linear_progress(value=0, show_value=False).props('instant-feedback').classes('w-full mt-2')
                sort_progress.visible = False

            # ---- Log ----------------------------------------------------
            log_card = ui.card().classes('w-full shadow-sm p-4')
            log_card.visible = False
            with log_card:
                ui.label('Log').classes('text-xs font-semibold text-gray-500 mb-1')
                log_area = ui.log(max_lines=300).classes('w-full h-44 font-mono text-xs')

            # ==============================================================
            # Browse helper — native OS folder dialog via tkinter
            # (runs server-side so we get the real full path, no upload)
            # ==============================================================

            async def _browse(target_field: ui.input):
                """Open a native OS folder picker and write the result into *target_field*.

                Spawns tkinter in a subprocess so it runs on that process's main
                thread — required on macOS, and works identically on Windows and Linux.
                """
                import sys
                import subprocess

                def _open_dialog():
                    result = subprocess.run(
                        [sys.executable, '-c',
                         'import tkinter as tk; from tkinter import filedialog; '
                         'root = tk.Tk(); root.withdraw(); root.wm_attributes("-topmost", True); '
                         'print(filedialog.askdirectory() or "")'],
                        capture_output=True, text=True,
                    )
                    return result.stdout.strip()

                folder = await asyncio.get_event_loop().run_in_executor(None, _open_dialog)
                if folder:
                    target_field.set_value(folder)

            # ==============================================================
            # Restore saved field values
            # ==============================================================

            _saved = app.storage.general.get('astronight_fields', {})
            if _saved.get('source'):
                source_input.set_value(_saved['source'])
            if _saved.get('destination'):
                dest_input.set_value(_saved['destination'])
            for _ft in ('Dark', 'Bias', 'Flat'):
                _ft_data = _saved.get('extra_calib', {}).get(_ft, {})
                if _ft_data.get('enabled'):
                    calib_enabled[_ft].set_value(True)
                    calib_inputs[_ft].enable()
                    calib_browse[_ft].enable()
                if _ft_data.get('path'):
                    calib_inputs[_ft].set_value(_ft_data['path'])

            # ==============================================================
            # Log helper
            # ==============================================================

            def append_log(msg: str):
                """Write *msg* to the on-screen log panel and the log file."""
                log_card.visible = True
                log_area.push(msg)
                logging.info(msg)

            # ==============================================================
            # Groups table builder
            # ==============================================================

            def refresh_groups():
                """Apply target/camera/year filters, then rebuild the table."""
                tf   = target_input.value.strip().lower() if target_input.value else ''
                cam  = camera_select.value
                year = year_select.value

                filtered = [
                    g for g in state.groups
                    if (not tf  or tf in g.key.target.lower())
                    and (cam  == 'All cameras' or g.key.camera == cam)
                    and (year == 'All years'   or g.key.night_label.startswith(year))
                ]
                rebuild_groups_ui(filtered)

            target_input.on('update:model-value', lambda _: refresh_groups())
            camera_select.on('update:model-value', lambda _: refresh_groups())
            year_select.on('update:model-value',   lambda _: refresh_groups())
            sort_select.on('update:model-value',   lambda _: refresh_groups())

            def rebuild_groups_ui(groups: list | None = None):
                """Rebuild the groups tree from scratch using *groups*.

                Clears the existing table and re-renders every row.  Also
                repopulates the Camera and Year filter dropdowns from the full
                unfiltered ``state.groups`` list so the dropdowns always show
                all available options regardless of the current filter.

                Each target gets one header row.  Multi-night targets get an
                expand/collapse chevron and a hidden children div containing
                one row per night.  The checkbox wiring uses factory functions
                (``make_parent_handler``, ``make_child_handler``) to capture
                loop variables by value rather than by reference.

                Note: NiceGUI ``select.options = [...]`` requires a follow-up
                ``.update()`` call for the browser to receive the new option list.
                """
                from collections import defaultdict

                if groups is None:
                    groups = state.groups

                group_rows.clear()
                all_parent_cbs.clear()
                groups_table.clear()
                warnings_area.clear()
                state.selected.clear()

                # Populate filter dropdowns from the full (unfiltered) group list
                cameras = sorted({g.key.camera for g in state.groups})
                years   = sorted({g.key.night_label[:4] for g in state.groups}, reverse=True)
                camera_select.options = ['All cameras'] + cameras
                camera_select.update()
                year_select.options = ['All years'] + years
                year_select.update()

                # Group by target (preserving sort order from build_groups)
                target_map: dict[str, list[tuple[int, LightGroup]]] = defaultdict(list)
                for idx, group in enumerate(state.groups):
                    if group not in groups:
                        continue
                    target_map[group.key.target].append((idx, group))

                with groups_table:
                    # Column header — sticky so it stays visible while scrolling
                    with ui.row().classes(
                        'w-full px-3 py-2 bg-gray-100 rounded-t text-xs font-semibold text-gray-500 gap-2 sticky top-0 z-10'
                    ):
                        ui.label('').classes('w-5 shrink-0')   # chevron
                        ui.label('').classes('w-6 shrink-0')   # checkbox
                        ui.label('Target / Night').classes('flex-1 min-w-0')
                        ui.label('Lights').classes('w-14 text-right')
                        ui.label('Darks').classes('w-14 text-right')
                        ui.label('Biases').classes('w-14 text-right')
                        ui.label('Flats').classes('w-14 text-right')
                        ui.label('').classes('w-5 shrink-0')

                    def _target_sort_key(item):
                        from datetime import date
                        tgt, ig = item
                        sv = sort_select.value
                        if sv == 'Most recent':
                            latest = max(g.key.night_label for _, g in ig)
                            return (-date.fromisoformat(latest).toordinal(), tgt)
                        if sv == 'Most lights':
                            return (-sum(len(g.lights) for _, g in ig), tgt)
                        return (tgt,)

                    for target, indexed_groups in sorted(target_map.items(), key=_target_sort_key):
                        idxs         = [i for i, _ in indexed_groups]
                        total_lights = sum(len(g.lights)       for _, g in indexed_groups)
                        total_darks  = sum(len(g.calib.darks)  for _, g in indexed_groups)
                        total_biases = sum(len(g.calib.biases) for _, g in indexed_groups)
                        total_flats  = sum(len(g.calib.flats)  for _, g in indexed_groups)
                        has_warn     = any(g.calib.warnings    for _, g in indexed_groups)
                        multi        = len(indexed_groups) > 1

                        with ui.column().classes('w-full border-b border-gray-100'):

                            # ---- Target header row ----
                            with ui.row().classes(
                                'w-full px-3 py-2 hover:bg-gray-50 items-center gap-2 cursor-default'
                            ):
                                if multi:
                                    expand_icon = ui.icon('chevron_right').classes(
                                        'text-gray-400 cursor-pointer w-5 shrink-0'
                                    )
                                else:
                                    ui.label('').classes('w-5 shrink-0')

                                parent_cb = ui.checkbox(value=False).classes('w-6 shrink-0')
                                all_parent_cbs.append(parent_cb)

                                suffix = f'  ({len(indexed_groups)} nights)' if multi else ''
                                # Collect all original names across all nights for this target
                                all_originals = sorted({
                                    n
                                    for _, g in indexed_groups
                                    for n in g.original_targets
                                })
                                with ui.column().classes('flex-1 min-w-0 gap-0'):
                                    ui.label(target + suffix).classes(
                                        'font-semibold text-sm truncate'
                                    )
                                    if all_originals:
                                        ui.label(', '.join(all_originals)).classes(
                                            'text-xs text-gray-400 truncate'
                                        )
                                ui.label(str(total_lights)).classes('w-14 text-right font-medium text-sm')
                                ui.label(str(total_darks)).classes('w-14 text-right text-xs text-gray-400')
                                ui.label(str(total_biases)).classes('w-14 text-right text-xs text-gray-400')
                                ui.label(str(total_flats)).classes('w-14 text-right text-xs text-gray-400')
                                ui.label('⚠' if has_warn else '').classes(
                                    'w-5 shrink-0 text-center text-yellow-500 text-xs'
                                )

                            # ---- Children (multi-night only) ----
                            if multi:
                                children_div = ui.column().classes('w-full')
                                children_div.visible = False
                                child_cbs: list[tuple[int, ui.checkbox]] = []

                                with children_div:
                                    for idx, group in indexed_groups:
                                        g_warn = bool(group.calib.warnings)
                                        row_bg = 'bg-yellow-50' if g_warn else 'bg-gray-50'
                                        with ui.row().classes(
                                            f'w-full px-3 py-1 {row_bg} items-center gap-2 '
                                            'border-t border-gray-100'
                                        ):
                                            ui.label('').classes('w-5 shrink-0')
                                            child_cb = ui.checkbox(value=False).classes(
                                                'w-6 shrink-0'
                                            )
                                            child_cbs.append((idx, child_cb))
                                            detail = (
                                                f'{group.key.night_label}'
                                                f'  ·  {group.key.camera}'
                                                f'  ·  {group.key.exposure_str}'
                                            )
                                            ui.label(detail).classes(
                                                'flex-1 min-w-0 text-xs text-gray-500 truncate'
                                            )
                                            ui.label(str(len(group.lights))).classes(
                                                'w-14 text-right text-xs'
                                            )
                                            ui.label(str(len(group.calib.darks))).classes(
                                                'w-14 text-right text-xs text-gray-400'
                                            )
                                            ui.label(str(len(group.calib.biases))).classes(
                                                'w-14 text-right text-xs text-gray-400'
                                            )
                                            ui.label(str(len(group.calib.flats))).classes(
                                                'w-14 text-right text-xs text-gray-400'
                                            )
                                            ui.label('⚠' if g_warn else '').classes(
                                                'w-5 shrink-0 text-center text-yellow-500 text-xs'
                                            )
                                            group_rows.append({'cb': child_cb, 'idx': idx})

                                # Expand/collapse — factory captures div and icon by value
                                def make_expander(div, icon_el):
                                    """Return a toggle callback that shows/hides *div* and
                                    swaps the chevron icon on *icon_el*."""
                                    open_box = [False]  # mutable cell so inner fn can write it
                                    def toggle():
                                        open_box[0] = not open_box[0]
                                        div.visible = open_box[0]
                                        icon_el.props(
                                            'name=' + ('expand_more' if open_box[0] else 'chevron_right')
                                        )
                                    return toggle
                                expand_icon.on('click', make_expander(children_div, expand_icon))

                                # Parent → children: checking/unchecking the parent row
                                # sets all child checkboxes and updates state.selected.
                                def make_parent_handler(c_pairs):
                                    """Return a handler that propagates parent checkbox state
                                    to all child checkboxes in *c_pairs*."""
                                    def handler(e):
                                        checked = bool(e.args)
                                        for i, cb in c_pairs:
                                            cb.set_value(checked)
                                            if checked:
                                                state.selected.add(i)
                                            else:
                                                state.selected.discard(i)
                                    return handler
                                parent_cb.on(
                                    'update:model-value', make_parent_handler(child_cbs)
                                )

                                # Child → parent sync: when a child changes, update
                                # state.selected and set the parent checkbox to reflect
                                # all-checked / none-checked / partial (left as-is).
                                def make_child_handler(p_cb, c_pairs, this_idx):
                                    """Return a handler for one child checkbox.

                                    Updates ``state.selected`` for *this_idx* and
                                    syncs the parent checkbox *p_cb*:
                                    - all children checked  → parent checked
                                    - no  children checked  → parent unchecked
                                    - mixed                 → parent unchanged
                                    """
                                    def handler(e):
                                        if e.args:
                                            state.selected.add(this_idx)
                                        else:
                                            state.selected.discard(this_idx)
                                        vals = [cb.value for _, cb in c_pairs]
                                        if all(vals):
                                            p_cb.set_value(True)
                                        elif not any(vals):
                                            p_cb.set_value(False)
                                        # partial: leave parent checkbox as-is
                                    return handler

                                for idx_val, child_cb in child_cbs:
                                    child_cb.on(
                                        'update:model-value',
                                        make_child_handler(parent_cb, child_cbs, idx_val),
                                    )

                            else:
                                # Single-night: parent checkbox IS the selector
                                only_idx = idxs[0]
                                group_rows.append({'cb': parent_cb, 'idx': only_idx})

                                def make_single_handler(i):
                                    def handler(e):
                                        if e.args:
                                            state.selected.add(i)
                                        else:
                                            state.selected.discard(i)
                                    return handler
                                parent_cb.on('update:model-value', make_single_handler(only_idx))

                # Warnings section — collapsible scrollable pane
                all_warnings = [
                    (group, w)
                    for group in state.groups
                    for w in group.calib.warnings
                ]
                if all_warnings:
                    with warnings_area:
                        ui.separator().classes('mt-3')
                        with ui.expansion(
                            f'⚠  {len(all_warnings)} warning(s)'
                        ).classes('w-full text-yellow-700 text-sm font-semibold').props('dense'):
                            with ui.element('div').classes(
                                'overflow-y-auto'
                            ).style('max-height: 180px'):
                                for group, w in all_warnings:
                                    with ui.row().classes(
                                        'items-start gap-2 text-xs text-yellow-700 py-1 '
                                        'border-b border-yellow-100'
                                    ):
                                        ui.label('⚠').classes('shrink-0')
                                        ui.label(
                                            f'{group.key.target} ({group.key.night_label}): {w}'
                                        )

                groups_card.visible = True
                sort_card.visible = True

            def _set_all(checked: bool):
                for row in group_rows:
                    row['cb'].set_value(checked)
                    if checked:
                        state.selected.add(row['idx'])
                    else:
                        state.selected.discard(row['idx'])
                for pcb in all_parent_cbs:
                    pcb.set_value(checked)

            sel_all_btn.on('click',  lambda: _set_all(True))
            sel_none_btn.on('click', lambda: _set_all(False))

            # ==============================================================
            # Scan
            # ==============================================================

            async def do_scan():
                src_text  = source_input.value.strip()
                dest_text = dest_input.value.strip()

                if not src_text:
                    ui.notify('Please enter a source folder.', type='warning')
                    return
                if not dest_text:
                    ui.notify('Please enter a destination folder.', type='warning')
                    return

                src = Path(src_text)
                if not src.exists():
                    ui.notify(f'Source folder not found:\n{src}', type='negative')
                    return

                # Warn if the object name catalog hasn't been downloaded yet
                from .catalog import OPENNGC_PATH, download_openngc, load_catalog
                if not OPENNGC_PATH.exists():
                    action: asyncio.Future[str] = asyncio.get_event_loop().create_future()

                    with ui.dialog().props('persistent') as no_catalog_dialog, ui.card().classes('max-w-md'):
                        ui.label('No object catalog').classes('text-lg font-semibold')
                        ui.label(
                            'The object name catalog has not been downloaded yet. '
                            'Without it, targets like M 42 and NGC 5457 will not resolve '
                            'to common names (e.g. Orion Nebula, Pinwheel Galaxy), and '
                            'frames with different designations for the same object may '
                            'not be merged into one group.'
                        ).classes('text-sm text-gray-600')
                        with ui.row().classes('gap-2 justify-end w-full'):
                            ui.button('Cancel', on_click=lambda: (
                                action.set_result('cancel'),
                                no_catalog_dialog.close(),
                            )).props('flat')
                            ui.button('Scan anyway', on_click=lambda: (
                                action.set_result('scan'),
                                no_catalog_dialog.close(),
                            )).props('flat')
                            ui.button('Download catalog', icon='download', on_click=lambda: (
                                action.set_result('download'),
                                no_catalog_dialog.close(),
                            )).props('color=primary')

                    no_catalog_dialog.open()
                    choice = await action
                    no_catalog_dialog.delete()

                    if choice == 'cancel':
                        return
                    if choice == 'download':
                        scan_status.set_text('Downloading catalog…')
                        try:
                            await asyncio.get_event_loop().run_in_executor(None, download_openngc)
                            load_catalog(force=True)
                            ui.notify('Catalog downloaded.', type='positive')
                        except Exception as exc:
                            ui.notify(f'Catalog download failed: {exc}', type='negative')
                            return

                state.source = src
                state.destination = Path(dest_text)

                # Persist field values for next session
                app.storage.general['astronight_fields'] = {
                    'source': src_text,
                    'destination': dest_text,
                    'extra_calib': {
                        ft: {
                            'enabled': calib_enabled[ft].value,
                            'path': calib_inputs[ft].value.strip(),
                        }
                        for ft in ('Dark', 'Bias', 'Flat')
                    },
                }

                # Collect extra calib folders
                for ft, inp in calib_inputs.items():
                    raw = inp.value.strip()
                    if raw and calib_enabled[ft].value:
                        p = Path(raw)
                        if p.exists():
                            state.extra_calib[ft] = p
                        else:
                            ui.notify(f'Extra {ft} folder not found: {raw}', type='warning')
                            state.extra_calib[ft] = None
                    else:
                        state.extra_calib[ft] = None

                scan_btn.disable()
                scan_progress.visible = True
                scan_progress.set_value(0)
                scan_status.set_text('Starting scan…')
                groups_card.visible = False
                sort_card.visible = False
                state.selected.clear()

                _last_ui_update = [0.0]
                _ui_alive = [True]

                def progress_cb(done: int, total: int, name: str):
                    """Update the scan progress bar and status label.

                    Throttled to at most 4 updates per second (250 ms gap).
                    Without throttling, a 21,000-file archive would push
                    21,000+ WebSocket messages and overwhelm the browser.
                    The final update (done == total) always fires regardless
                    of the throttle so the bar reaches 100%.

                    If the browser disconnects mid-scan (e.g. Safari refresh),
                    NiceGUI deletes the client and UI updates raise RuntimeError.
                    We catch that once, disable further UI updates, and let the
                    scan continue to completion so the cache is fully written.
                    """
                    if not _ui_alive[0]:
                        return
                    now = time.monotonic()
                    if done < total and now - _last_ui_update[0] < 0.25:
                        return
                    _last_ui_update[0] = now
                    ratio = done / total if total else 0
                    try:
                        scan_progress.set_value(ratio)
                        scan_status.set_text(f'Scanning… {int(ratio * 100)}%')
                    except RuntimeError:
                        _ui_alive[0] = False

                def _scan_with_cache():
                    """Run scan_fits inside the executor thread.

                    ScanCache must be constructed here (inside the thread) because
                    SQLite connections cannot be shared across threads.
                    """
                    with ScanCache() as cache:
                        return scan_fits(src, progress_callback=progress_cb, cache=cache)

                with _caffeinate():
                    frames, errors = await asyncio.get_event_loop().run_in_executor(
                        None, _scan_with_cache
                    )

                    # Scan extra calibration folders and merge, filtering to their type
                    for ft, extra_path in state.extra_calib.items():
                        if extra_path is None:
                            continue
                        scan_status.set_text(f'Scanning extra {ft}s folder…')
                        def _scan_extra(p=extra_path):
                            with ScanCache() as cache:
                                return scan_fits(p, cache=cache)
                        extra_frames, extra_errors = await asyncio.get_event_loop().run_in_executor(
                            None, _scan_extra
                        )
                        # Only keep frames of the expected type from this folder
                        kept = [f for f in extra_frames if f.frame_type == ft]
                        frames = frames + kept
                        errors = errors + extra_errors
                        append_log(
                            f'Extra {ft}s folder: found {len(kept)} {ft.lower()} frames in {extra_path}'
                        )

                state.frames = frames

                for path, exc in errors:
                    append_log(f'PARSE ERROR {path}: {exc}')

                n_lights = sum(1 for f in frames if f.frame_type == 'Light')
                n_darks  = sum(1 for f in frames if f.frame_type == 'Dark')
                n_biases = sum(1 for f in frames if f.frame_type == 'Bias')
                n_flats  = sum(1 for f in frames if f.frame_type == 'Flat')

                scan_progress.set_value(1)
                scan_btn.enable()

                if errors:
                    ui.notify(
                        f'{len(errors)} file(s) could not be parsed — check log.',
                        type='warning'
                    )

                if n_lights == 0:
                    scan_status.set_text('Scan complete — no light frames found.')
                    ui.notify('No light frames found.', type='warning')
                    return

                scan_status.set_text(
                    f'Found {len(frames):,} files — '
                    f'{n_lights:,} lights · {n_darks:,} darks · '
                    f'{n_biases:,} biases · {n_flats:,} flats'
                )

                state.groups = build_groups(frames)
                # Reset filters and rebuild
                camera_select.set_value('All cameras')
                year_select.set_value('All years')
                refresh_groups()
                ui.notify(f'Found {len(state.groups)} light groups.', type='positive')

                # Remember that this source was successfully scanned so we can
                # auto-rescan on reconnect (fast from cache)
                app.storage.general['last_scan_source'] = src_text

            scan_btn.on('click', do_scan)

            # Auto-rescan on page reconnect if the previous session completed a
            # scan for the same source folder.  The cache makes this fast.
            _last_scan_src = app.storage.general.get('last_scan_source', '')
            if _last_scan_src and _last_scan_src == source_input.value.strip():
                ui.notify('Restoring previous scan…', type='info')
                ui.timer(0.5, do_scan, once=True)

            # ==============================================================
            # Sort / Dry run
            # ==============================================================

            async def do_sort(dry_run: bool):
                if not state.groups:
                    ui.notify('Please scan first.', type='warning')
                    return

                chosen = [state.groups[i] for i in sorted(state.selected)]
                if not chosen:
                    ui.notify('No groups selected.', type='warning')
                    return

                dest = state.destination
                dest.mkdir(parents=True, exist_ok=True)
                state.log_path = setup_log(dest)

                sort_btn.disable()
                dry_run_btn.disable()
                sort_progress.visible = True
                sort_progress.set_value(0)

                if dry_run:
                    sort_status.set_text('Dry run — no files will be copied')
                    total_lights = sum(len(g.lights) for g in chosen)
                    total_calib  = sum(
                        len(g.calib.darks) + len(g.calib.biases) + len(g.calib.flats)
                        for g in chosen
                    )
                    for g in chosen:
                        append_log(
                            f'[DRY RUN] {g.key.target} / {g.key.camera} / '
                            f'{g.key.exposure_str} / {g.key.night_label}  — '
                            f'{len(g.lights)} lights, {len(g.calib.darks)} darks, '
                            f'{len(g.calib.biases)} biases, {len(g.calib.flats)} flats'
                        )
                        for w in g.calib.warnings:
                            append_log(f'  ⚠ {w}')
                    sort_progress.set_value(1)
                    sort_status.set_text(
                        f'Dry run complete — {len(chosen)} groups, '
                        f'{total_lights} lights + {total_calib} calibration files'
                    )
                    ui.notify('Dry run complete.', type='info')
                else:
                    def progress_cb(done: int, total: int, name: str):
                        sort_progress.set_value(done / total if total else 0)
                        pct = int(100 * done / total) if total else 0
                        sort_status.set_text(f'Copying… {pct}%')

                    with _caffeinate():
                        result = await asyncio.get_event_loop().run_in_executor(
                            None,
                            lambda: copy_groups(
                                groups=chosen,
                                dest_root=dest,
                                dry_run=False,
                                progress_cb=progress_cb,
                                log_cb=append_log,
                            )
                        )

                    sort_progress.set_value(1)
                    summary = (
                        f'Done — {result.copied:,} copied, {result.skipped:,} skipped, '
                        f'{result.renamed:,} renamed, {result.errored} errors'
                    )
                    sort_status.set_text(summary)
                    append_log(summary)
                    if state.log_path:
                        append_log(f'Log: {state.log_path}')

                    ntype = 'negative' if result.errored else 'positive'
                    ui.notify(summary, type=ntype)

                sort_btn.enable()
                dry_run_btn.enable()

            sort_btn.on('click',    lambda: asyncio.ensure_future(do_sort(dry_run=False)))
            dry_run_btn.on('click', lambda: asyncio.ensure_future(do_sort(dry_run=True)))


def run_gui(host: str = '127.0.0.1', port: int = 8765, reload: bool = False):
    create_gui()
    ui.run(
        host=host,
        port=port,
        title='AstroNightOrganizer',
        favicon='🔭',
        reload=reload,
        show=True,
        storage_secret='astronight-local',
        reconnect_timeout=30,
    )
