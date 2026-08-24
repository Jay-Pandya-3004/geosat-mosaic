"""Regression test found via the first end-to-end tiled multi-band Tier B
run (2026-08-03): `_merge_single_band_files` parsed each single-band
file's `title` attribute assuming a parenthesized "Tier X (BAND)" format,
but both `build_tier_dataset` and `finalize_tiled_band_writer` actually
write single-band titles as "Global geostationary mosaic -- {band_name}"
(no parentheses at all) -- `title.split("(", 1)[1]` raised IndexError the
first time two tiled band files (VIS and SWIR both wrote successfully)
were actually merged, a path no prior test had exercised (earlier tiling
validation was a single-band run, never a multi-band merge)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import xarray as xr

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import run_phase2_tier as tier_script  # noqa: E402


def _write_single_band_file(path, band_name):
    ds = xr.Dataset(
        {f"value_{band_name.lower()}": (("y", "x"), np.zeros((2, 2), dtype=np.float32))},
        coords={"lat": ("y", [1.0, 2.0]), "lon": ("x", [3.0, 4.0])},
        attrs={
            "Conventions": "CF-1.8",
            "title": f"Global geostationary mosaic -- {band_name}",
            "requested_target_time": "2026-01-01T00:11:00",
        },
    )
    ds.to_netcdf(path)


def test_merge_single_band_files_handles_the_real_unparenthesized_title_format(tmp_path):
    p1 = tmp_path / "vis.nc"
    p2 = tmp_path / "swir.nc"
    _write_single_band_file(p1, "VIS")
    _write_single_band_file(p2, "SWIR")
    out_path = tmp_path / "merged.nc"
    target = dt.datetime(2026, 1, 1, 0, 11, 0)

    tier_script._merge_single_band_files([p1, p2], "B", target, out_path)

    merged = xr.open_dataset(out_path)
    assert merged.attrs["title"] == "Global geostationary mosaic -- Tier B (VIS, SWIR)"
    assert "value_vis" in merged.variables
    assert "value_swir" in merged.variables
    merged.close()
