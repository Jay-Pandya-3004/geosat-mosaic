"""Integration check for reproject.downsample_if_finer_than_target
against a real GOES-18 VIS (C02) file -- the actual scenario this
optimization was built for (a real native array here is 21696x21696,
~470M pixels, bigger than Tier B's entire 593M-pixel target grid for
just ONE satellite's ONE band). Auto-skipped if the file isn't present
in data/raw/ -- see test_pipeline_integration.py for the same pattern.
"""

from __future__ import annotations

import glob
import os

import numpy as np
import pytest
from satpy import Scene

from global_mosaic.processing.reproject import (
    common_grid,
    downsample_if_finer_than_target,
    reproject_to_cropped_grid,
)

from .conftest import DATA_RAW

pytestmark = pytest.mark.integration

GOES18_LON_DEG = -137.0
MAX_VIEWING_ANGLE_DEG = 81.3


def _find_goes18_vis_file():
    matches = sorted(glob.glob(str(DATA_RAW / "OR_ABI-L1b-RadF-M6C02_G18_*.nc")))
    return matches[0] if matches else None


def test_downsampled_vis_reprojection_matches_full_resolution_on_real_data():
    path = _find_goes18_vis_file()
    if path is None:
        pytest.skip("no real GOES-18 C02 (VIS) file in data/raw/ -- run run_phase2_tier.py B once first")

    os.environ.setdefault("SATPY_CONFIG_PATH", str(DATA_RAW.parents[1] / "src" / "global_mosaic"))
    scn = Scene(filenames=[path], reader="abi_l1b")
    scn.load(["C02"], calibration="reflectance")
    data = scn["C02"]
    original_extent = data.attrs["area"].area_extent

    target_area = common_grid(resolution_km=1.0)  # Tier B's real resolution

    full = reproject_to_cropped_grid(data, target_area, GOES18_LON_DEG, MAX_VIEWING_ANGLE_DEG)
    downsampled = downsample_if_finer_than_target(data, native_resolution_km=0.5, target_resolution_km=1.0)

    # Geometry must stay exact -- no registration shift from downsampling.
    assert downsampled.attrs["area"].area_extent == original_extent
    assert downsampled.sizes["x"] == data.sizes["x"] // 2
    assert downsampled.sizes["y"] == data.sizes["y"] // 2

    cropped_from_downsampled = reproject_to_cropped_grid(
        downsampled, target_area, GOES18_LON_DEG, MAX_VIEWING_ANGLE_DEG
    )

    both_finite = np.isfinite(full.values) & np.isfinite(cropped_from_downsampled.values)
    assert both_finite.sum() > 1_000_000, "expected a large real overlap region for a full-disk file"

    diff = np.abs(full.values[both_finite] - cropped_from_downsampled.values[both_finite])
    # Real measured result: mean ~0.5, max ~56 on a 0-100
    # reflectance scale (block-averaging smooths sharp cloud edges) --
    # bound generously above that so this doesn't flake on ordinary
    # data variation, while still catching a real regression (e.g. a
    # registration-shift bug would blow this up by orders of magnitude).
    assert diff.mean() < 3.0
    assert diff.max() < 100.0

    finite_full = int(np.isfinite(full.values).sum())
    finite_downsampled = int(np.isfinite(cropped_from_downsampled.values).sum())
    assert abs(finite_full - finite_downsampled) / finite_full < 0.01
