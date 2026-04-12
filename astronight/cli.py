"""
Command-line interface for AstroNightOrganizer.

Usage:
    astronight sort <source> <destination> [--dry-run] [--target NAME] [--log-file PATH]
    astronight cache stats
    astronight cache clean
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from .cache import ScanCache, default_cache_path
from .file_ops import scan_fits
from .sorter import LightGroup, build_groups, copy_groups

console = Console()


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_log_file(dest: Path) -> Path:
    log_dir = dest / '_IMPORT_LOGS'
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    log_path = log_dir / f'import-{stamp}.log'
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format='%(asctime)s  %(message)s',
        datefmt='%H:%M:%S',
    )
    return log_path


def log(msg: str) -> None:
    logging.info(msg)


# ---------------------------------------------------------------------------
# Group table display
# ---------------------------------------------------------------------------

def show_groups(groups: list[LightGroup]) -> None:
    table = Table(show_header=True, header_style='bold', box=None, padding=(0, 1))
    table.add_column('#',        style='dim',  width=4,  justify='right')
    table.add_column('Target',   style='cyan', min_width=14)
    table.add_column('Camera',   min_width=8)
    table.add_column('Exposure', min_width=8)
    table.add_column('Night',    min_width=12)
    table.add_column('Lights',   justify='right', min_width=6)
    table.add_column('Darks',    justify='right', min_width=6)
    table.add_column('Biases',   justify='right', min_width=6)
    table.add_column('Flats',    justify='right', min_width=6)
    table.add_column('',         min_width=2)   # warnings column

    for i, g in enumerate(groups, 1):
        warn = '[yellow]⚠[/yellow]' if g.calib.warnings else ''
        table.add_row(
            str(i),
            g.key.target,
            g.key.camera,
            g.key.exposure_str,
            g.key.night_label,
            str(len(g.lights)),
            str(len(g.calib.darks)),
            str(len(g.calib.biases)),
            str(len(g.calib.flats)),
            warn,
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Selection parser  "1,3-5,7" -> {1,3,4,5,7}
# ---------------------------------------------------------------------------

def parse_selection(text: str, max_n: int) -> set[int]:
    selected: set[int] = set()
    for part in text.split(','):
        part = part.strip()
        if '-' in part:
            lo, _, hi = part.partition('-')
            try:
                selected.update(range(int(lo), int(hi) + 1))
            except ValueError:
                pass
        else:
            try:
                selected.add(int(part))
            except ValueError:
                pass
    return {n for n in selected if 1 <= n <= max_n}


def prompt_selection(groups: list[LightGroup]) -> list[LightGroup]:
    """Display the group table and ask the user which groups to process."""
    console.print()
    show_groups(groups)
    console.print()

    # Show any warnings beneath the table
    all_warnings = [(i + 1, w) for i, g in enumerate(groups) for w in g.calib.warnings]
    if all_warnings:
        console.print('[yellow]Warnings:[/yellow]')
        for num, w in all_warnings:
            console.print(f'  [yellow]⚠[/yellow]  [dim]#{num}[/dim] {w}')
        console.print()

    while True:
        raw = console.input(
            "[bold]Select groups to sort[/bold] "
            "(e.g. [cyan]1,3-5[/cyan] or [cyan]all[/cyan]) "
            f"[dim]\\[all][/dim]: "
        ).strip()

        if raw == '' or raw.lower() == 'all':
            return groups

        selected = parse_selection(raw, len(groups))
        if selected:
            chosen = [groups[i - 1] for i in sorted(selected)]
            return chosen

        console.print('[red]Invalid selection — try again.[/red]')


# ---------------------------------------------------------------------------
# sort command
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name='astronightorganizer', prog_name='AstroNightOrganizer')
def main():
    """AstroNightOrganizer — organise FITS files for PixInsight WBPP."""


@main.command()
@click.argument('source', type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument('destination', type=click.Path(file_okay=False, path_type=Path))
@click.option('--dry-run', is_flag=True, help='Show groups and preview without copying files.')
@click.option('--target', default=None, metavar='NAME',
              help='Pre-filter lights to targets containing NAME (case-insensitive).')
@click.option('--no-cache', is_flag=True, help='Bypass the scan cache and re-read all files.')
@click.option('--yes', '-y', is_flag=True, help='Skip interactive selection and process all groups.')
@click.option('--log-file', type=click.Path(dir_okay=False, path_type=Path),
              help='Write log to this file (default: DESTINATION/_IMPORT_LOGS/import-<stamp>.log).')
@click.option('--extra-dark', type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=None, metavar='PATH',
              help='Extra folder to scan for dark frames.')
@click.option('--extra-bias', type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=None, metavar='PATH',
              help='Extra folder to scan for bias frames.')
@click.option('--extra-flat', type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=None, metavar='PATH',
              help='Extra folder to scan for flat frames.')
def sort(source: Path, destination: Path, dry_run: bool, target: str | None,
         no_cache: bool, yes: bool, log_file: Path | None,
         extra_dark: Path | None, extra_bias: Path | None, extra_flat: Path | None):
    """Scan SOURCE, review groups, then sort FITS files into DESTINATION."""
    destination.mkdir(parents=True, exist_ok=True)

    if log_file:
        logging.basicConfig(filename=log_file, level=logging.INFO,
                            format='%(asctime)s  %(message)s', datefmt='%H:%M:%S')
        lp = log_file
    else:
        lp = setup_log_file(destination)

    console.print(f"\n[bold]AstroNightOrganizer[/bold]  |  source: [cyan]{source}[/cyan]")
    console.print(f"  destination : [cyan]{destination}[/cyan]")
    console.print(f"  log file    : [dim]{lp}[/dim]")
    if extra_dark:
        console.print(f"  extra darks : [cyan]{extra_dark}[/cyan]")
    if extra_bias:
        console.print(f"  extra biases: [cyan]{extra_bias}[/cyan]")
    if extra_flat:
        console.print(f"  extra flats : [cyan]{extra_flat}[/cyan]")
    if target:
        console.print(f"  target filter: [cyan]{target}[/cyan]")
    if dry_run:
        console.print("  [yellow bold]DRY RUN — no files will be copied[/yellow bold]")
    if no_cache:
        console.print("  [dim]cache: disabled[/dim]")
    console.print()

    # -------------------------------------------------------------------------
    # Phase 1: scan
    # -------------------------------------------------------------------------
    cache = None if no_cache else ScanCache()
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
            transient=True,
        ) as progress:
            scan_task = progress.add_task("Scanning...", total=None)

            def scan_cb(done: int, total: int, name: str):
                progress.update(scan_task, total=total, completed=done,
                                description=f"Scanning  {done}/{total}")

            frames, scan_errors = scan_fits(source, progress_callback=scan_cb, cache=cache)

        if cache:
            stats = cache.stats()
            console.print(f"  [dim]cache: {stats['entries']} entries[/dim]")
    finally:
        if cache:
            cache.close()

    # Scan extra calibration folders, keeping only the expected frame type each
    for frame_type, extra_path in (
        ('Dark', extra_dark),
        ('Bias', extra_bias),
        ('Flat', extra_flat),
    ):
        if extra_path is None:
            continue
        extra_cache = None if no_cache else ScanCache()
        try:
            extra_frames, extra_errors = scan_fits(extra_path, cache=extra_cache)
        finally:
            if extra_cache:
                extra_cache.close()
        kept = [f for f in extra_frames if f.frame_type == frame_type]
        frames = frames + kept
        scan_errors = scan_errors + extra_errors
        console.print(
            f"  [dim]extra {frame_type.lower()}s: {len(kept)} frames from {extra_path}[/dim]"
        )

    if scan_errors:
        console.print(f"[yellow]  {len(scan_errors)} file(s) could not be parsed — see log.[/yellow]")
        for path, exc in scan_errors:
            log(f"PARSE ERROR {path}: {exc}")

    # Apply --target pre-filter (calibration frames always kept)
    if target:
        target_lower = target.lower()
        frames = [
            f for f in frames
            if f.frame_type != 'Light' or
               (f.target and target_lower in f.target.lower())
        ]

    n_lights = sum(1 for f in frames if f.frame_type == 'Light')
    n_darks  = sum(1 for f in frames if f.frame_type == 'Dark')
    n_biases = sum(1 for f in frames if f.frame_type == 'Bias')
    n_flats  = sum(1 for f in frames if f.frame_type == 'Flat')

    console.print(
        f"\nFound [bold]{len(frames)}[/bold] FITS files  "
        f"([cyan]{n_lights}[/cyan] lights, "
        f"[cyan]{n_darks}[/cyan] darks, "
        f"[cyan]{n_biases}[/cyan] biases, "
        f"[cyan]{n_flats}[/cyan] flats)"
    )

    if not frames or n_lights == 0:
        console.print("[red]No light frames found. Exiting.[/red]")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Phase 2: build groups
    # -------------------------------------------------------------------------
    groups = build_groups(frames)

    if not groups:
        console.print("[red]No sortable groups found. Exiting.[/red]")
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Phase 3: interactive selection (or --yes / --dry-run skips it)
    # -------------------------------------------------------------------------
    if dry_run:
        # Dry run: show the table, show warnings, but don't prompt or copy
        console.print()
        show_groups(groups)
        console.print()
        all_warnings = [(i + 1, w) for i, g in enumerate(groups) for w in g.calib.warnings]
        if all_warnings:
            console.print('[yellow]Warnings:[/yellow]')
            for num, w in all_warnings:
                console.print(f'  [yellow]⚠[/yellow]  [dim]#{num}[/dim] {w}')
            console.print()
        total_lights = sum(len(g.lights) for g in groups)
        total_calib  = sum(
            len(g.calib.darks) + len(g.calib.biases) + len(g.calib.flats)
            for g in groups
        )
        console.print(
            f"[dim]Dry run complete — {len(groups)} groups, "
            f"{total_lights} lights, {total_calib} calibration files would be copied.[/dim]\n"
        )
        return

    if yes:
        selected_groups = groups
        console.print()
        show_groups(groups)
    else:
        selected_groups = prompt_selection(groups)

    if not selected_groups:
        console.print("[yellow]No groups selected. Exiting.[/yellow]")
        sys.exit(0)

    console.print(f"\nProcessing [bold]{len(selected_groups)}[/bold] group(s)...\n")

    # -------------------------------------------------------------------------
    # Phase 4: copy
    # -------------------------------------------------------------------------
    def sort_log(msg: str):
        log(msg)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        copy_task = progress.add_task("Copying...", total=None)

        def copy_cb(done: int, total: int, name: str):
            progress.update(copy_task, total=total, completed=done,
                            description=f"Copying  {done}/{total}  {name[:40]}")

        result = copy_groups(
            groups=selected_groups,
            dest_root=destination,
            dry_run=False,
            progress_cb=copy_cb,
            log_cb=sort_log,
        )

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    console.print()
    table = Table(title="Sort Complete", show_header=False, box=None)
    table.add_column(style='dim')
    table.add_column(style='bold')
    table.add_row("Copied",  str(result.copied))
    table.add_row("Skipped", str(result.skipped))
    table.add_row("Renamed", str(result.renamed))
    table.add_row("Errors",  f"[red]{result.errored}[/red]" if result.errored else "0")
    console.print(table)

    if result.warnings:
        console.print()
        console.print(f"[yellow]Warnings ({len(result.warnings)}):[/yellow]")
        for w in result.warnings:
            console.print(f"  [yellow]•[/yellow] {w}")

    console.print(f"\n[dim]Log: {lp}[/dim]\n")


# ---------------------------------------------------------------------------
# gui command
# ---------------------------------------------------------------------------

@main.command()
@click.option('--port', default=8765, show_default=True, help='Port for the local web server.')
def gui(port: int):
    """Open the AstroNightOrganizer graphical interface in your browser."""
    from .gui import run_gui
    run_gui(port=port)


# ---------------------------------------------------------------------------
# catalog command group
# ---------------------------------------------------------------------------

@main.group()
def catalog():
    """Manage the object name catalog."""


@catalog.command('update')
def catalog_update():
    """Download or refresh the OpenNGC object name catalog."""
    from .catalog import OPENNGC_PATH, download_openngc, load_catalog
    console.print(f"Downloading OpenNGC catalog to [dim]{OPENNGC_PATH}[/dim]…")
    try:
        download_openngc()
        cat = load_catalog(force=True)
        console.print(f"[green]Catalog updated — {len(cat):,} entries.[/green]")
    except Exception as exc:
        console.print(f"[red]Catalog update failed: {exc}[/red]")
        raise SystemExit(1)


@catalog.command('status')
def catalog_status():
    """Show catalog location and entry count."""
    from .catalog import OPENNGC_PATH, load_catalog
    if not OPENNGC_PATH.exists():
        console.print(f"[yellow]No catalog found at {OPENNGC_PATH}[/yellow]")
        console.print("Run [bold]astronight catalog update[/bold] to download it.")
        return
    cat = load_catalog()
    console.print(f"Location : [dim]{OPENNGC_PATH}[/dim]")
    console.print(f"Entries  : [bold]{len(cat):,}[/bold]")
    import os
    mtime = OPENNGC_PATH.stat().st_mtime
    from datetime import datetime
    console.print(f"Updated  : [dim]{datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')}[/dim]")


# ---------------------------------------------------------------------------
# cache command group
# ---------------------------------------------------------------------------

@main.group()
def cache():
    """Manage the scan cache."""


@cache.command('stats')
def cache_stats():
    """Show cache size and location."""
    with ScanCache() as c:
        s = c.stats()
    console.print(f"Cache entries : [bold]{s['entries']}[/bold]")
    console.print(f"Location      : [dim]{s['db_path']}[/dim]")


@cache.command('clean')
def cache_clean():
    """Remove cache entries for files that no longer exist on disk."""
    console.print("Scanning cache for stale entries...")
    with ScanCache() as c:
        removed = c.clean()
    console.print(f"Removed [bold]{removed}[/bold] stale entries.")
