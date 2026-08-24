"""Regression test for the most serious risk in tile-major processing:
before today's fix, _cached_lonlats and reproject_to_cropped_grid's
neighbour-info cache both keyed on (width, height) alone. Under row-
tiling, many tiles share the full grid's width, and most tiles (all but
a remainder) share the same height too -- two tiles at completely
different latitude bands were indistinguishable to those cache keys, so
the second tile would have silently reused the first tile's cached
lon/lat and reprojection result. No exception, just wrong output. This
test proves the fix (crop_offset included in both cache keys) actually
prevents that collision, using two REAL same-shaped tiles sliced from
the same grid (not hand-built) so crop_offset is populated exactly the
way the real tile-major driver will produce it."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from pyresample.geometry import AreaDefinition

import global_mosaic.processing.reproject as reproject_module
from global_mosaic.processing.reproject import _cached_lonlats, common_grid, reproject_to_cropped_grid

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

SUB_SATELLITE_LON_DEG = -75.2
MAX_VIEWING_ANGLE_DEG = 81.3


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(reproject_module, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(reproject_module, "_lonlats_cache", {})
    monkeypatch.setattr(reproject_module, "_neighbour_info_cache", {})
    yield


def _two_same_shaped_tiles():
    """Two row-tiles of the SAME full grid, same height by construction,
    different latitude bands -- exactly the collision-prone case."""
    full_area = common_grid(resolution_km=30.0)
    tile_height = full_area.height // 4  # arbitrary, just needs two non-overlapping same-sized slices
    tile_1 = full_area[0:tile_height, :]
    tile_2 = full_area[tile_height : 2 * tile_height, :]
    assert tile_1.width == tile_2.width and tile_1.height == tile_2.height
    assert tile_1.crop_offset != tile_2.crop_offset
    return tile_1, tile_2


def test_lonlats_cache_does_not_collide_across_same_shaped_tiles():
    tile_1, tile_2 = _two_same_shaped_tiles()

    lons_1, lats_1 = _cached_lonlats(tile_1)
    lons_2, lats_2 = _cached_lonlats(tile_2)

    # Different latitude bands -> genuinely different lat values, not the
    # same array reused (would fail if the collision bug were present).
    assert not np.array_equal(lats_1, lats_2)
    # Each tile's own lats must fall within ITS OWN true extent, not the
    # other tile's -- proves it's not just "different but still wrong."
    assert lats_1.max() <= tile_1.area_extent[3] and lats_1.min() >= tile_1.area_extent[1]
    assert lats_2.max() <= tile_2.area_extent[3] and lats_2.min() >= tile_2.area_extent[1]
    assert lats_1.max() > lats_2.max(), "tile_1 (rows 0:h) should be the northern band, tile_2 further south"


def test_lonlats_disk_cache_writes_separate_files_per_tile(tmp_path):
    tile_1, tile_2 = _two_same_shaped_tiles()
    _cached_lonlats(tile_1)
    _cached_lonlats(tile_2)
    cache_files = list(tmp_path.glob("lonlats_*.npz"))
    assert len(cache_files) == 2, f"expected 2 distinct disk cache entries, got {[f.name for f in cache_files]}"


def _synthetic_source(seed=0):
    area = AreaDefinition(
        "src", "src", "src",
        {"proj": "geos", "h": 35786023, "lon_0": SUB_SATELLITE_LON_DEG, "a": 6378137, "b": 6356752.3},
        200, 200,
        (-5434894.9, -5434894.9, 5434894.9, 5434894.9),
    )
    rng = np.random.default_rng(seed)
    values = rng.uniform(200.0, 300.0, size=(200, 200)).astype(np.float32)
    data = xr.DataArray(values, dims=("y", "x"))
    data.attrs["area"] = area
    # No satellite_actual_* key -> time-invariant -> disk-persisted, same
    # as the real GOES/INSAT case this collision would actually bite.
    data.attrs["orbital_parameters"] = {
        "projection_longitude": SUB_SATELLITE_LON_DEG,
        "projection_latitude": 0.0,
        "projection_altitude": 35786023.0,
    }
    return data


def test_neighbour_info_cache_does_not_collide_across_same_shaped_tiles(tmp_path):
    tile_1, tile_2 = _two_same_shaped_tiles()
    data = _synthetic_source()

    result_1 = reproject_to_cropped_grid(data, tile_1, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)
    result_2 = reproject_to_cropped_grid(data, tile_2, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)

    cache_files = list(tmp_path.glob("neighbour_info_*.npz"))
    assert len(cache_files) == 2, (
        f"tile_1 and tile_2 have the same (width, height) but different crop_offset -- "
        f"must get separate neighbour-info cache entries, got {[f.name for f in cache_files]}"
    )
    # The two tiles' reprojected lat coordinates must reflect their own
    # true latitude bands, not each other's -- the actual bug this guards
    # against is silently correct-looking-but-wrong output, not a crash.
    assert not np.array_equal(result_1.coords["lat"].values, result_2.coords["lat"].values)
    assert result_1.coords["lat"].values.max() > result_2.coords["lat"].values.max()


def test_sza_cache_does_not_collide_across_same_shaped_tiles(tmp_path, monkeypatch):
    """Same collision risk as lonlats/neighbour-info, guarded the same
    way (crop_offset in the cache key) -- see run_phase2_tier.py's
    _build_sza_grid."""
    import run_phase2_tier as tier_script

    monkeypatch.setattr(tier_script, "_CACHE_DIR", tmp_path)
    tile_1, tile_2 = _two_same_shaped_tiles()
    target = dt.datetime(2026, 1, 1, 0, 11, 0)

    sza_1 = tier_script._build_sza_grid(tile_1, target)
    sza_2 = tier_script._build_sza_grid(tile_2, target)

    cache_files = list(tmp_path.glob("sza_*.npz"))
    assert len(cache_files) == 2, (
        f"tile_1 and tile_2 have the same (width, height) but different crop_offset -- "
        f"must get separate SZA cache entries, got {[f.name for f in cache_files]}"
    )
    assert not np.array_equal(sza_1.values, sza_2.values)
