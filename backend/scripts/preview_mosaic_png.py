#!/usr/bin/env python
"""Render a full-resolution, lossless 16-bit grayscale PNG preview of one or
more 2D variables from a Phase 2 tier mosaic (tierb_*.nc / tiera_*.nc).

Exists because HDFView's Image view can't display these grids at all: its
SWT bitmap path computes buffer size as width*height*4 in a 32-bit int,
which overflows for Tier B's 40075x14806 grid (593M pixels) and throws
NegativeArraySizeException. This script bypasses that entirely by writing
the array straight to PNG with Pillow -- no downsampling, full native
resolution, 16-bit depth to preserve the source int16 precision (only a
linear rescale to fill the 0-65535 range, not an 8-bit lossy stretch).

Usage:
    python scripts/preview_mosaic_png.py data/mosaics/tierb_20260101T001100.nc
    python scripts/preview_mosaic_png.py data/mosaics/tierb_20260101T001100.nc \
        --variables value_vis value_swir --output-dir data/mosaics/preview
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import netCDF4 as nc
import numpy as np
from PIL import Image

# Legitimate large scientific raster, not a decompression bomb.
Image.MAX_IMAGE_PIXELS = None

DEFAULT_VARIABLES = ["value_vis", "value_swir"]


def render_variable(ds: nc.Dataset, name: str, output_dir: Path, stem: str) -> Path:
    """Render one 2D NetCDF variable to a full-resolution, lossless 16-bit grayscale PNG.

    Reads the variable's raw (un-unpacked) int16 values, linearly
    rescales the finite/non-fill range to fill 0-65535, and writes a
    16-bit PNG via Pillow -- avoids HDFView's Image-view overflow (see
    module docstring) and avoids compounding a second rounding on top of
    the file's own scale_factor/add_offset packing.

    Args:
        ds: Open ``netCDF4.Dataset`` for the mosaic file.
        name: Name of the 2D variable to render.
        output_dir: Directory to write the PNG into; created if needed.
        stem: Filename stem (typically the source .nc file's stem) used
            to build the output filename.

    Returns:
        The path of the written PNG file.

    Raises:
        ValueError: If the variable is not 2D, or has no valid
            (non-fill) pixels.
        RuntimeError: If there isn't enough free disk space at
            `output_dir` for the uncompressed 16-bit output.
    """
    var = ds.variables[name]
    if var.ndim != 2:
        raise ValueError(f"{name!r} is not 2D (shape={var.shape}); skipping")

    # Raw stored int16, not the auto-unpacked physical (scale_factor/
    # add_offset applied) float -- keeps this a lossless rescale of the
    # exact bits on disk rather than compounding two roundings.
    var.set_auto_maskandscale(False)
    raw = var[:]
    fill_value = getattr(var, "_FillValue", None)

    height, width = raw.shape
    needed_bytes = height * width * 2  # uncompressed 16-bit estimate
    output_dir.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(output_dir).free
    if needed_bytes > free_bytes:
        raise RuntimeError(
            f"{name}: need ~{needed_bytes / 1e9:.2f}GB free at {output_dir}, "
            f"only {free_bytes / 1e9:.2f}GB available"
        )

    if fill_value is not None:
        valid_mask = raw != fill_value
    else:
        valid_mask = np.ones_like(raw, dtype=bool)

    if not valid_mask.any():
        raise ValueError(f"{name}: no valid (non-fill) pixels")

    vmin = int(raw[valid_mask].min())
    vmax = int(raw[valid_mask].max())
    if vmax == vmin:
        scaled = np.zeros_like(raw, dtype=np.uint16)
    else:
        # float32 intermediate keeps peak memory at ~2.4GB for a 593M-pixel
        # grid instead of ~4.8GB for float64 -- precision loss is negligible
        # next to the destination's 16-bit (65536-level) quantization.
        stretched = (raw.astype(np.float32) - vmin) * (65535.0 / (vmax - vmin))
        np.clip(stretched, 0, 65535, out=stretched)
        scaled = stretched.astype(np.uint16)
        del stretched
    scaled[~valid_mask] = 0

    out_path = output_dir / f"{stem}_{name}.png"
    Image.fromarray(scaled, mode="I;16").save(out_path)

    print(
        f"{name}: {width}x{height} px, raw range [{vmin}, {vmax}] -> "
        f"{out_path} ({out_path.stat().st_size / 1e6:.1f}MB)"
    )
    return out_path


def main() -> int:
    """CLI entry point: render the requested variables from one mosaic .nc file to PNGs.

    Returns:
        Process exit code (always 0; missing variables are skipped with
        a warning rather than failing the whole run).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nc_file", type=Path, help="Path to a tierb_*.nc / tiera_*.nc mosaic")
    parser.add_argument(
        "--variables",
        nargs="+",
        default=DEFAULT_VARIABLES,
        help=f"2D variable names to render (default: {DEFAULT_VARIABLES})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for output PNGs (default: <nc_file's dir>/preview)",
    )
    args = parser.parse_args()

    if not args.nc_file.is_file():
        parser.error(f"no such file: {args.nc_file}")
    output_dir = args.output_dir or (args.nc_file.parent / "preview")

    ds = nc.Dataset(args.nc_file)
    try:
        for name in args.variables:
            if name not in ds.variables:
                print(f"skipping {name!r}: not present in {args.nc_file.name}", file=sys.stderr)
                continue
            render_variable(ds, name, output_dir, args.nc_file.stem)
    finally:
        ds.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
