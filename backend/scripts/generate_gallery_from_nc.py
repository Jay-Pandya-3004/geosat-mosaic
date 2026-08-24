"""Regenerate gallery preview PNGs from already-written tier .nc files.

Complements run_phase2_tier.py's --gallery-dir (which renders DURING the
live pipeline run, from in-memory tile data, so nothing gets processed
twice -- see processing/preview.py's own module docstring). This script
is for the opposite case: backfilling thumbnails for files that already
exist (e.g. produced before this feature existed, or after a change to
the rendering code itself that you want reflected without re-running the
whole acquisition/reprojection pipeline). Reading an already-written,
small, packed .nc file back is cheap -- nothing here re-downloads or
re-reprojects anything.

Usage:
    python generate_gallery_from_nc.py output/tiera/tiera_20260807T091744.nc --gallery-dir output/gallery
    python generate_gallery_from_nc.py output/tiera/*.nc --gallery-dir output/gallery
    python generate_gallery_from_nc.py output/tiera/tiera_20260807T091744.nc --gallery-dir output/gallery --band TIR1
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from global_mosaic.processing.preview import (  # noqa: E402
    BAND_PREVIEW_SPECS,
    PreviewAccumulator,
    compute_display_time,
    render_band_preview,
)

_FILENAME_RE = re.compile(r"^tier(?P<tier>[ab])_(?P<ts>\d{8}T\d{6})\.nc$", re.IGNORECASE)


def _parse_filename(path: Path) -> tuple[str, dt.datetime]:
    """Extract (tier, target) from a tier .nc file's filename.

    (tier, target) from the real tiera_<ts>.nc/tierb_<ts>.nc naming
    convention. Falls back to reading the file's own
    requested_target_time attribute if the filename doesn't match (e.g.
    a renamed/copied file) -- tier still has to come from the filename
    or an explicit --tier, since it isn't stored as its own attribute
    anywhere in the file.

    Args:
        path: Path to a ``tiera_<ts>.nc``/``tierb_<ts>.nc`` file.

    Returns:
        Tuple of ``(tier, target)`` -- tier is ``"A"``/``"B"``
        (uppercased), target is the parsed target datetime.

    Raises:
        ValueError: If the filename doesn't match the expected
            ``tier<a|b>_<YYYYMMDDTHHMMSS>.nc`` pattern.
    """
    match = _FILENAME_RE.match(path.name)
    if match:
        return match["tier"].upper(), dt.datetime.strptime(match["ts"], "%Y%m%dT%H%M%S")
    raise ValueError(
        f"{path.name} doesn't match tier<a|b>_<YYYYMMDDTHHMMSS>.nc -- "
        "can't infer tier/target from the filename, and this script doesn't (yet) take them explicitly."
    )


def _bands_in_file(ds: xr.Dataset) -> list[str]:
    """Find which renderable bands a tier .nc file actually contains.

    Every band this file actually has a value_<band> variable for,
    restricted to bands preview.py knows how to render (BAND_PREVIEW_SPECS)
    -- e.g. skips SWIR only if a future version of BAND_PREVIEW_SPECS ever
    drops it, not currently.

    Args:
        ds: Open tier mosaic ``xarray.Dataset``.

    Returns:
        Band names (uppercased) found as ``value_<band>`` variables and
        present in ``BAND_PREVIEW_SPECS``.
    """
    found = []
    for name in ds.data_vars:
        if name.startswith("value_"):
            band = name.removeprefix("value_").upper()
            if band in BAND_PREVIEW_SPECS:
                found.append(band)
    return found


def generate_for_file(nc_path: Path, gallery_dir: Path, bands: list[str] | None = None) -> list[Path]:
    """Render gallery preview PNGs for one already-written tier .nc file.

    Renders one PNG per band found in `nc_path` (or just `bands` if
    given), returns the list of paths written.

    Args:
        nc_path: Path to a ``tiera_<ts>.nc``/``tierb_<ts>.nc`` file.
        gallery_dir: Gallery root directory (same meaning as
            run_phase2_tier.py's ``--gallery-dir``).
        bands: If given, only render these bands; otherwise render every
            band found in the file (see :func:`_bands_in_file`).

    Returns:
        Paths of the PNG files written, one per rendered band.
    """
    tier, target = _parse_filename(nc_path)
    written = []
    with xr.open_dataset(nc_path) as ds:
        target_bands = bands or _bands_in_file(ds)
        for band in target_bands:
            suffix = f"_{band.lower()}"
            value_var = f"value{suffix}"
            if value_var not in ds:
                print(f"  [{band}] {value_var} not in {nc_path.name} -- skipping")
                continue

            accumulator = PreviewAccumulator(grid=ds[value_var].values)
            key_var, mid_var = f"satellite_key{suffix}", f"satellite_scan_mid{suffix}"
            if key_var in ds:
                accumulator.satellite_keys = set(ds[key_var].values.tolist())
            if mid_var in ds:
                # stored as datetime64[ns] (see _append_satellite_metadata_table) --
                # .item() on a datetime64 scalar gives a plain Python datetime
                # directly at us resolution (at ns resolution it gives an int
                # instead, which is why this converts to 'us' first).
                accumulator.scan_mids = [t.astype("datetime64[us]").item() for t in ds[mid_var].values]

            # Same dual-timestamp filename as the live pipeline path
            # (run_phase2_tier.py) -- target first (fixed cadence, what
            # THREDDS/retention key off), real actual mid-scan time
            # second (informational only).
            actual_mid = compute_display_time(accumulator, target)
            out_path = (
                gallery_dir / f"tier{tier.lower()}" / band.lower()
                / f"{band.lower()}_{target.strftime('%Y%m%dT%H%M%S')}_{actual_mid.strftime('%Y%m%dT%H%M%S')}.png"
            )
            render_band_preview(accumulator, band, target, out_path)
            print(f"  [{band}] wrote {out_path}")
            written.append(out_path)
    return written


def main() -> None:
    """CLI entry point: regenerate gallery PNGs for one or more tier .nc files.

    Returns:
        None. Prints a per-file status and a final written-count summary.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("nc_files", nargs="+", type=Path, help="one or more tier .nc files (shell-glob-expanded already, or pass a pattern your shell expands)")
    parser.add_argument("--gallery-dir", type=Path, required=True, help="gallery ROOT (same meaning as run_phase2_tier.py's --gallery-dir)")
    parser.add_argument("--band", action="append", dest="bands", help="only this band (repeatable) -- default: every band found in each file")
    args = parser.parse_args()

    total = 0
    for nc_path in args.nc_files:
        if not nc_path.is_file():
            print(f"SKIPPING {nc_path} -- not a file", file=sys.stderr)
            continue
        print(f"=== {nc_path.name} ===")
        try:
            written = generate_for_file(nc_path, args.gallery_dir, args.bands)
        except ValueError as exc:
            print(f"  SKIPPING: {exc}", file=sys.stderr)
            continue
        total += len(written)

    print(f"\nWrote {total} preview(s) total.")


if __name__ == "__main__":
    main()
