"""Tests for reproject.py's neighbor-search (KD-tree) caching --
_resample_nearest_cached / _neighbour_info_cache_key. Two things must
both be true: reuse actually happens (correctness of the optimization's
premise) and reuse produces IDENTICAL output to a fresh, uncached call
(correctness of the optimization itself), plus that genuinely different
geometry correctly misses rather than reusing a wrong cached result (the
safety argument _neighbour_info_cache_key's docstring makes)."""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from pyresample.geometry import AreaDefinition

import global_mosaic.processing.reproject as reproject_module
from global_mosaic.processing.reproject import (
    _is_time_invariant_geometry,
    common_grid,
    reproject_to_cropped_grid,
)

SUB_SATELLITE_LON_DEG = -75.2
MAX_VIEWING_ANGLE_DEG = 81.3

# GOES's real orbital_parameters (confirmed against a real
# ABI file): only ever nominal/projection fields, no actual_* key.
GOES_LIKE_ORBITAL_PARAMETERS = {
    "projection_longitude": -75.2,
    "projection_latitude": 0.0,
    "projection_altitude": 35786023.0,
    "satellite_nominal_longitude": -75.2,
    "satellite_nominal_latitude": 0.0,
    "satellite_nominal_altitude": 35786023.4375,
}

# Himawari's real orbital_parameters (confirmed against a
# real AHI file): a real satellite_actual_* position.
HIMAWARI_LIKE_ORBITAL_PARAMETERS = {
    "projection_longitude": 140.7,
    "projection_latitude": 0.0,
    "projection_altitude": 35785863.0,
    "satellite_actual_longitude": 140.7466,
    "satellite_actual_latitude": -0.02,
    "satellite_actual_altitude": 35782860.0,
}


def _source(lon_0=SUB_SATELLITE_LON_DEG, seed=0, orbital_parameters=None):
    area = AreaDefinition(
        "src",
        "src",
        "src",
        {"proj": "geos", "h": 35786023, "lon_0": lon_0, "a": 6378137, "b": 6356752.3},
        200,
        200,
        (-5434894.9, -5434894.9, 5434894.9, 5434894.9),
    )
    rng = np.random.default_rng(seed)
    values = rng.uniform(200.0, 300.0, size=(200, 200)).astype(np.float32)
    data = xr.DataArray(values, dims=("y", "x"))
    data.attrs["area"] = area
    data.attrs["orbital_parameters"] = orbital_parameters or dict(GOES_LIKE_ORBITAL_PARAMETERS)
    return data


def test_is_time_invariant_geometry_true_for_nominal_only():
    assert _is_time_invariant_geometry(GOES_LIKE_ORBITAL_PARAMETERS) is True


def test_is_time_invariant_geometry_false_when_actual_position_present():
    assert _is_time_invariant_geometry(HIMAWARI_LIKE_ORBITAL_PARAMETERS) is False


def test_time_invariant_satellite_persists_neighbour_info_to_disk(tmp_path):
    target_area = common_grid(resolution_km=30.0)
    data = _source(orbital_parameters=GOES_LIKE_ORBITAL_PARAMETERS)
    reproject_to_cropped_grid(data, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)
    assert len(list(tmp_path.glob("neighbour_info_*.npz"))) == 1


def test_time_varying_satellite_does_not_persist_neighbour_info_to_disk(tmp_path):
    target_area = common_grid(resolution_km=30.0)
    data = _source(orbital_parameters=HIMAWARI_LIKE_ORBITAL_PARAMETERS)
    reproject_to_cropped_grid(data, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)
    assert list(tmp_path.glob("neighbour_info_*.npz")) == []


def test_time_varying_satellite_still_reuses_in_process_within_one_run(monkeypatch, tmp_path):
    """No disk persistence doesn't mean no reuse at all -- within a
    single process (e.g. Tier A's 4 same-resolution bands sharing one
    Himawari scan, if ever run un-isolated), the in-process dict cache
    still avoids a second real KD-tree search."""
    target_area = common_grid(resolution_km=30.0)
    data_a = _source(orbital_parameters=HIMAWARI_LIKE_ORBITAL_PARAMETERS, seed=0)
    data_b = _source(orbital_parameters=HIMAWARI_LIKE_ORBITAL_PARAMETERS, seed=1)

    from pyresample.kd_tree import get_neighbour_info as real_get_neighbour_info

    call_count = 0

    def counting_get_neighbour_info(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_get_neighbour_info(*args, **kwargs)

    import pyresample.kd_tree as kd_tree_module

    monkeypatch.setattr(kd_tree_module, "get_neighbour_info", counting_get_neighbour_info)

    reproject_to_cropped_grid(data_a, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)
    reproject_to_cropped_grid(data_b, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)

    assert call_count == 1, "second call with identical geometry should reuse the in-process cache"


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Every test gets a fresh, empty cache (disk + in-process dict) so
    tests can't see each other's cached entries."""
    monkeypatch.setattr(reproject_module, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(reproject_module, "_neighbour_info_cache", {})
    yield


def test_second_call_with_same_geometry_hits_cache_and_matches_first(tmp_path):
    target_area = common_grid(resolution_km=30.0)
    data_a = _source(seed=0)
    data_b = _source(seed=1)  # different VALUES, same geometry/area

    first = reproject_to_cropped_grid(data_a, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)
    cache_files_after_first = list(tmp_path.glob("neighbour_info_*.npz"))
    assert len(cache_files_after_first) == 1, "first call should have written one neighbour-info cache file"

    second = reproject_to_cropped_grid(data_b, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)
    cache_files_after_second = list(tmp_path.glob("neighbour_info_*.npz"))
    assert len(cache_files_after_second) == 1, "second call with identical geometry must reuse, not write a new cache file"

    # Different VALUES resampled the same way -> different data, but at
    # the exact same set of target pixels (same crop_columns, same
    # finite/NaN mask) since the neighbor mapping itself was reused.
    assert np.array_equal(first.attrs["crop_columns"], second.attrs["crop_columns"])
    assert np.array_equal(np.isnan(first.values), np.isnan(second.values))


def test_disk_cached_neighbour_info_matches_freshly_computed_result(tmp_path, monkeypatch):
    """The actual correctness check: loading a neighbour-info cache file
    back from disk (simulating a fresh subprocess with a cold in-process
    cache but a warm disk cache, run_phase2_tier.py's real situation)
    must produce byte-identical resampled output to computing it fresh."""
    target_area = common_grid(resolution_km=30.0)
    data = _source(seed=0)

    fresh = reproject_to_cropped_grid(data, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)

    # Simulate a new process: clear the in-process cache but keep the disk cache.
    monkeypatch.setattr(reproject_module, "_neighbour_info_cache", {})
    from_disk_cache = reproject_to_cropped_grid(data, target_area, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)

    assert np.array_equal(fresh.values, from_disk_cache.values, equal_nan=True)
    assert np.array_equal(fresh.coords["lat"].values, from_disk_cache.coords["lat"].values)
    assert np.array_equal(fresh.coords["lon"].values, from_disk_cache.coords["lon"].values)


def test_different_satellite_position_does_not_reuse_wrong_cache(tmp_path):
    """Safety check for _neighbour_info_cache_key's whole argument: a
    genuinely different sub-satellite longitude (simulating a different
    scan cycle's real station-kept position, e.g. Himawari's confirmed
    real cycle-to-cycle drift) must NOT reuse the first geometry's cached
    neighbor mapping -- it should compute (and cache) its own."""
    target_area = common_grid(resolution_km=30.0)
    data_here = _source(lon_0=-75.2, seed=0)
    data_elsewhere = _source(lon_0=-75.5, seed=0)  # different real position

    reproject_to_cropped_grid(data_here, target_area, -75.2, MAX_VIEWING_ANGLE_DEG)
    reproject_to_cropped_grid(data_elsewhere, target_area, -75.5, MAX_VIEWING_ANGLE_DEG)

    cache_files = list(tmp_path.glob("neighbour_info_*.npz"))
    assert len(cache_files) == 2, "genuinely different geometry must get its own cache entry, not reuse the wrong one"
