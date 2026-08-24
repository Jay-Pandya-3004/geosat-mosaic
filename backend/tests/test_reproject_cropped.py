"""Correctness test for reproject_to_cropped_grid: its output, placed
back at its own crop_columns, must exactly match the corresponding slice
of the full-grid reproject_to_common_grid -- cropping is a compute/memory
optimization, not a different algorithm, so this proves it changes
nothing about the actual values. Uses a synthetic geostationary source
area (real geos projection math via pyresample, not mocked) so the
resampling itself is real, just small and fast."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from pyresample.geometry import AreaDefinition

from global_mosaic.processing.reproject import (
    common_grid,
    downsample_if_finer_than_target,
    reproject_to_common_grid,
    reproject_to_cropped_grid,
    visible_columns_for_satellite,
)

SUB_SATELLITE_LON_DEG = -75.2  # GOES-East's real nominal longitude
MAX_VIEWING_ANGLE_DEG = 81.3  # every satellite in this project's sat_metadata.py


def _synthetic_source():
    area = AreaDefinition(
        "src",
        "src",
        "src",
        {"proj": "geos", "h": 35786023, "lon_0": SUB_SATELLITE_LON_DEG, "a": 6378137, "b": 6356752.3},
        200,
        200,
        (-5434894.9, -5434894.9, 5434894.9, 5434894.9),
    )
    rng = np.random.default_rng(0)
    values = rng.uniform(200.0, 300.0, size=(200, 200)).astype(np.float32)
    data = xr.DataArray(values, dims=("y", "x"))
    data.attrs["area"] = area
    return data


@pytest.mark.parametrize("resolution_km", [30.0])
def test_cropped_reprojection_matches_full_reprojection_in_its_own_columns(resolution_km):
    data = _synthetic_source()
    target_area = common_grid(resolution_km=resolution_km)

    full = reproject_to_common_grid(data, target_area)
    cropped = reproject_to_cropped_grid(data, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)

    cols = cropped.attrs["crop_columns"]
    expected_cols = visible_columns_for_satellite(target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)
    assert np.array_equal(cols, expected_cols)

    full_slice = full.values[:, cols]
    assert cropped.values.shape == full_slice.shape
    # NaN-safe equality: both real values and NaN-vs-NaN positions must match.
    both_nan = np.isnan(cropped.values) & np.isnan(full_slice)
    assert np.array_equal(np.isnan(cropped.values), np.isnan(full_slice))
    finite = ~both_nan
    assert np.allclose(cropped.values[finite], full_slice[finite])

    assert np.array_equal(cropped.coords["lat"].values, full.coords["lat"].values[:, cols])
    assert np.array_equal(cropped.coords["lon"].values, full.coords["lon"].values[:, cols])


def test_cropped_reprojection_is_smaller_than_full_grid():
    data = _synthetic_source()
    target_area = common_grid(resolution_km=30.0)
    cropped = reproject_to_cropped_grid(data, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)
    assert cropped.values.size < target_area.width * target_area.height


def test_downsample_leaves_data_unchanged_when_native_is_already_coarser():
    """No point block-averaging a satellite whose native resolution is
    ALREADY coarser than (or equal to) the target -- e.g. INSAT's 4km
    TIR1 onto Tier A's 2km grid needs no downsampling."""
    data = _synthetic_source()
    result = downsample_if_finer_than_target(data, native_resolution_km=4.0, target_resolution_km=2.0)
    assert result is data


def test_downsample_reduces_size_and_preserves_area_extent():
    data = _synthetic_source()
    original_extent = data.attrs["area"].area_extent
    # native 0.5km vs target 1km -> factor 2, matching the real GOES
    # VIS-onto-Tier-B case this was built for.
    result = downsample_if_finer_than_target(data, native_resolution_km=0.5, target_resolution_km=1.0)

    assert result.sizes["x"] == 100  # 200 / 2
    assert result.sizes["y"] == 100
    assert result.attrs["area"].width == 100
    assert result.attrs["area"].height == 100
    assert result.attrs["area"].area_extent == original_extent


def test_downsampled_reprojection_closely_matches_full_resolution():
    """The actual thing that matters: reprojecting the downsampled data
    should land almost exactly where reprojecting the full-resolution
    data would -- proves aggregate()'s geometry and coarsen()'s data
    reduction agree with each other, not just that each looks right in
    isolation. Uses a spatially SMOOTH synthetic field (a gradient), not
    i.i.d. random noise: neighboring pixels in real satellite imagery are
    physically correlated (cloud/clear-sky continuity), so that's the
    realistic case this optimization is built for -- pure random noise
    per pixel has no spatial correlation at all, so 2x2 block-averaging
    vs. single-pixel sampling would legitimately diverge sharply for
    that (confirmed: an earlier version of this test using
    _synthetic_source()'s random data failed this exact assertion, which
    was the test being unrealistic, not the implementation being wrong --
    the real GOES VIS file check this was modeled on showed ~0.5 mean
    abs diff on a 0-100 scale, not the near-random-magnitude gap seen
    with i.i.d. noise)."""
    area = AreaDefinition(
        "src",
        "src",
        "src",
        {"proj": "geos", "h": 35786023, "lon_0": SUB_SATELLITE_LON_DEG, "a": 6378137, "b": 6356752.3},
        200,
        200,
        (-5434894.9, -5434894.9, 5434894.9, 5434894.9),
    )
    row = np.linspace(200.0, 300.0, 200, dtype=np.float32)
    smooth_values = np.tile(row, (200, 1)) + np.tile(row.reshape(-1, 1), (1, 200))
    data = xr.DataArray(smooth_values.astype(np.float32), dims=("y", "x"))
    data.attrs["area"] = area

    target_area = common_grid(resolution_km=30.0)

    full = reproject_to_cropped_grid(data, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)
    downsampled = downsample_if_finer_than_target(data, native_resolution_km=0.5, target_resolution_km=1.0)
    cropped_from_downsampled = reproject_to_cropped_grid(
        downsampled, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG
    )

    both_finite = np.isfinite(full.values) & np.isfinite(cropped_from_downsampled.values)
    assert both_finite.sum() > 0, "test setup should produce overlapping finite pixels"
    assert np.allclose(full.values[both_finite], cropped_from_downsampled.values[both_finite], atol=5.0)
