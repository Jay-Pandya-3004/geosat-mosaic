"""Unit tests for reproject.py's common_grid() and the Phase 2 tier
resolution constants. Synthetic -- AreaDefinition construction only, no
network/real files needed."""

from __future__ import annotations

import numpy as np
import pytest

import global_mosaic.processing.reproject as reproject_module
from global_mosaic.processing.reproject import (
    TIER_A_RESOLUTION_KM,
    TIER_B_RESOLUTION_KM,
    common_grid,
    visible_columns_for_satellite,
)


def test_tier_resolutions_match_locked_design_decision():
    """2026-07-31 planning session locked these in -- see reproject.py's
    docstrings for the full storage-size/detail-preservation reasoning."""
    assert TIER_A_RESOLUTION_KM == 2.0
    assert TIER_B_RESOLUTION_KM == 1.0


def test_finer_resolution_grid_has_more_pixels():
    phase0_grid = common_grid(4.0)
    tier_a_grid = common_grid(TIER_A_RESOLUTION_KM)
    tier_b_grid = common_grid(TIER_B_RESOLUTION_KM)

    # Halving resolution_km doubles both width and height (same degree
    # extent, half the degree step) -- so tier A (2km) should be ~2x
    # phase0 (4km) in each dimension, and tier B (1km) ~4x. Independent
    # per-dimension rounding means this isn't pixel-exact, so check within
    # a small relative tolerance instead of strict equality.
    def approx_ratio(a: int, b: int, expected_ratio: float, tol: float = 0.01) -> bool:
        return abs((a / b) - expected_ratio) <= tol

    assert approx_ratio(tier_a_grid.width, phase0_grid.width, 2.0)
    assert approx_ratio(tier_a_grid.height, phase0_grid.height, 2.0)
    assert approx_ratio(tier_b_grid.width, phase0_grid.width, 4.0)
    assert approx_ratio(tier_b_grid.height, phase0_grid.height, 4.0)


def test_grid_spans_full_geo_visible_latitude_range():
    grid = common_grid(TIER_A_RESOLUTION_KM)
    extent = grid.area_extent  # (lon_min, lat_min, lon_max, lat_max)
    assert extent[1] == -66.5
    assert extent[3] == 66.5
    assert extent[0] == -180.0
    assert extent[2] == 180.0


def test_cached_lonlats_writes_and_reuses_disk_cache(tmp_path, monkeypatch):
    """The whole point of the disk layer: a SECOND, independent
    AreaDefinition object of the same shape (simulating a fresh
    subprocess that can't share the first one's in-process id() cache)
    must load from disk instead of calling get_lonlats() again."""
    monkeypatch.setattr(reproject_module, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(reproject_module, "_lonlats_cache", {})

    grid_1 = common_grid(resolution_km=60.0)  # coarse -- keep the test fast
    lons_1, lats_1 = reproject_module._cached_lonlats(grid_1)

    cache_file = tmp_path / f"lonlats_{grid_1.width}x{grid_1.height}_{grid_1.crop_offset[0]}_{grid_1.crop_offset[1]}.npz"
    assert cache_file.exists()

    call_count = 0
    real_get_lonlats = grid_1.get_lonlats

    def counting_get_lonlats(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_get_lonlats(*args, **kwargs)

    grid_2 = common_grid(resolution_km=60.0)  # a different object, same shape
    monkeypatch.setattr(grid_2, "get_lonlats", counting_get_lonlats)
    lons_2, lats_2 = reproject_module._cached_lonlats(grid_2)

    assert call_count == 0, "get_lonlats() should not be called -- must load from disk instead"
    assert np.array_equal(lons_1, lons_2)
    assert np.array_equal(lats_1, lats_2)


def test_cached_lonlats_disk_values_are_float32(tmp_path, monkeypatch):
    monkeypatch.setattr(reproject_module, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(reproject_module, "_lonlats_cache", {})
    grid = common_grid(resolution_km=60.0)
    lons, lats = reproject_module._cached_lonlats(grid)
    assert lons.dtype == np.float32
    assert lats.dtype == np.float32


def test_visible_columns_no_wraparound_for_meteosat_prime():
    """Meteosat Prime (0 deg) is nowhere near the antimeridian -- its
    +-81.3deg visible range [-81.3, 81.3] should be a single contiguous,
    ascending column range with no wraparound."""
    grid = common_grid(resolution_km=60.0)  # coarse, fast
    cols = visible_columns_for_satellite(grid, sub_satellite_lon_deg=0.0, max_viewing_angle_deg=81.3)
    assert list(cols) == list(range(cols[0], cols[-1] + 1))  # strictly ascending, no wrap
    # ~162.6 deg wide out of 360 -- roughly 45% of the grid's columns.
    assert 0.40 < len(cols) / grid.width < 0.50


def test_visible_columns_wrap_for_goes_west():
    """GOES-West's real Nominal_Longitude_Deg (-137.0) does wrap past
    -180 at +-81.3deg (-137-81.3 = -218.3) -- confirmed by direct
    calculation, not a hypothetical edge case. The returned
    column array must contain BOTH high indices (near width-1, the
    wrapped-around low-longitude end) and low indices (near 0)."""
    grid = common_grid(resolution_km=60.0)
    cols = visible_columns_for_satellite(grid, sub_satellite_lon_deg=-137.0, max_viewing_angle_deg=81.3)
    assert cols.max() > grid.width - 5  # reaches almost to the last column
    assert cols.min() < 5  # and also covers the first few columns
    assert 0.40 < len(cols) / grid.width < 0.50


def test_visible_columns_wrap_for_himawari():
    """Himawari's real Nominal_Longitude_Deg (140.7) wraps the other way
    (140.7+81.3 = 222.0, past +180)."""
    grid = common_grid(resolution_km=60.0)
    cols = visible_columns_for_satellite(grid, sub_satellite_lon_deg=140.7, max_viewing_angle_deg=81.3)
    assert cols.max() > grid.width - 5
    assert cols.min() < 5
    assert 0.40 < len(cols) / grid.width < 0.50
