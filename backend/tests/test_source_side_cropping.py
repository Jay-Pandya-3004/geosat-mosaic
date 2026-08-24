"""Tests for crop_source_to_target_lonlat and its wiring into
reproject_to_cropped_grid -- the 2026-08-03 fix for the tile-major
redesign's remaining memory bottleneck: get_neighbour_info's KD-tree is
built over the SOURCE array, so tiling only the TARGET side
(visible_columns_for_satellite, row-tiles) never shrank KD-tree
construction cost with tile count. This proves the source is cropped
smaller for a narrow tile, that cropping falls back safely when there's
no overlap at all, and that the crop doesn't change the reprojected
values versus the uncropped reference -- same "optimization, not a
different algorithm" guarantee test_reproject_cropped.py already
established for the target-side crop.

Also regression-guards a bug this design went through before landing: a
first attempt used AreaDefinition.get_area_slices (pyresample's own
boundary-polygon reduce-before-resample helper, the same one satpy's
Scene.resample() applies by default) and it silently undershot the
needed source row range by a measured ~10 rows for exactly this
narrow-full-width-tile shape -- caught only by the exact-equivalence test
below actually failing, not by inspection. crop_source_to_target_lonlat
uses the exact algebraic inverse-projection of the target points instead,
specifically to avoid that failure mode."""

from __future__ import annotations

import numpy as np
import xarray as xr
from pyresample.geometry import AreaDefinition

import global_mosaic.processing.reproject as reproject_module
from global_mosaic.processing.reproject import (
    common_grid,
    crop_source_to_target_lonlat,
    reproject_to_common_grid,
    reproject_to_cropped_grid,
)

SUB_SATELLITE_LON_DEG = -75.2  # GOES-East's real nominal longitude
MAX_VIEWING_ANGLE_DEG = 81.3  # every satellite in this project's sat_metadata.py
RADIUS_OF_INFLUENCE_M = 20_000  # matches reproject_to_cropped_grid's own default


def _synthetic_source(size=400):
    area = AreaDefinition(
        "src",
        "src",
        "src",
        {"proj": "geos", "h": 35786023, "lon_0": SUB_SATELLITE_LON_DEG, "a": 6378137, "b": 6356752.3},
        size,
        size,
        (-5434894.9, -5434894.9, 5434894.9, 5434894.9),
    )
    rng = np.random.default_rng(0)
    values = rng.uniform(200.0, 300.0, size=(size, size)).astype(np.float32)
    data = xr.DataArray(values, dims=("y", "x"))
    data.attrs["area"] = area
    return data


def _narrow_equatorial_tile(full_target):
    y0 = full_target.height // 2
    return full_target[y0 : y0 + 10, :], y0, y0 + 10


def test_crop_source_to_target_lonlat_shrinks_for_a_narrow_tile():
    data = _synthetic_source()
    full_target = common_grid(resolution_km=30.0)
    tile, _, _ = _narrow_equatorial_tile(full_target)
    lons, lats = tile.get_lonlats()

    cropped = crop_source_to_target_lonlat(data, lons, lats, RADIUS_OF_INFLUENCE_M)

    # cropped.attrs["area"] is a SwathDefinition (see crop_source_to_
    # target_lonlat's docstring for why -- AreaDefinition.__getitem__ was
    # found to introduce a real precision bug for some real
    # satellites), so use .shape rather than AreaDefinition's .width/.height.
    orig_pixels = data.attrs["area"].width * data.attrs["area"].height
    cropped_shape = cropped.attrs["area"].shape
    cropped_pixels = cropped_shape[0] * cropped_shape[1]
    assert cropped_pixels < orig_pixels * 0.7, (
        f"a 10-row equatorial tile out of a {full_target.height}-row grid should crop away "
        f"most of the source's own latitude range, got {cropped_pixels}/{orig_pixels} pixels"
    )
    assert cropped.values.size == cropped_pixels


def test_crop_source_to_target_lonlat_chunking_matches_single_shot(monkeypatch):
    """Real, measured cost (granular ctypes profiling of a
    real INSAT VIS/Tier-B candidate): calling get_array_indices_from_lonlat
    on the full ~35M-point column-cropped target grid in one shot cost a
    real +1.38GB transient -- chunked processing (_CROP_CHUNK_SIZE) fixes
    that, but chunking must be EXACT, not an approximation. This forces a
    tiny chunk size (well below the test tile's own point count, so the
    loop genuinely runs multiple iterations, including a partial final
    chunk) and proves the accumulated min/max bounding box -- and
    therefore the actual cropped region -- is byte-for-byte identical to
    the default (effectively single-shot at this small scale) chunk size."""
    data = _synthetic_source()
    full_target = common_grid(resolution_km=30.0)
    tile, _, _ = _narrow_equatorial_tile(full_target)
    lons, lats = tile.get_lonlats()

    single_shot = crop_source_to_target_lonlat(data, lons, lats, RADIUS_OF_INFLUENCE_M)

    monkeypatch.setattr(reproject_module, "_CROP_CHUNK_SIZE", 50)
    chunked = crop_source_to_target_lonlat(data, lons, lats, RADIUS_OF_INFLUENCE_M)

    assert chunked.attrs["area"].shape == single_shot.attrs["area"].shape
    assert np.array_equal(chunked.attrs["area"].lons, single_shot.attrs["area"].lons)
    assert np.array_equal(chunked.attrs["area"].lats, single_shot.attrs["area"].lats)
    assert np.array_equal(chunked.values, single_shot.values)


def test_crop_source_to_target_lonlat_falls_back_when_no_overlap():
    """Target points far outside this satellite's visible disk -- none of
    them land inside the source's own domain, so the fallback must return
    the original data unchanged rather than crop to an empty/bogus region.
    This is a pure compute/memory optimization, never a correctness
    requirement (see crop_source_to_target_lonlat's own docstring):
    reproject_to_cropped_grid's resample_nearest call already correctly
    produces all-NaN output for genuinely out-of-view data with or without
    this crop."""
    data = _synthetic_source()
    far_lons = np.full((10, 10), 150.5)  # far side of the globe from lon_0=-75.2
    far_lats = np.linspace(-1.0, 1.0, 100).reshape(10, 10)

    result = crop_source_to_target_lonlat(data, far_lons, far_lats, RADIUS_OF_INFLUENCE_M)
    assert result is data


def test_cropped_reprojection_onto_a_tile_matches_full_reprojection_in_its_rows_and_columns():
    """The actual correctness guarantee: reprojecting a satellite onto one
    narrow TILE (not just the full grid, see test_reproject_cropped.py's
    target-side-only crop test) with source-side cropping now applied must
    land on exactly the same values as reprojecting onto the full grid and
    slicing out that tile's own rows/columns afterward -- proves the
    source crop changes nothing about the actual selection math, matching
    every other tiling change in this project's own equivalence-testing
    pattern (e.g. test_tile_pipeline_equivalence.py). This is the test
    that actually caught the get_area_slices undershoot bug (see module
    docstring) -- it failed for real before the fix, not just in theory."""
    data = _synthetic_source()
    full_target = common_grid(resolution_km=30.0)
    tile, y0, y1 = _narrow_equatorial_tile(full_target)

    full = reproject_to_common_grid(data, full_target)
    tiled_cropped = reproject_to_cropped_grid(data, tile, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)

    cols = tiled_cropped.attrs["crop_columns"]
    expected = full.values[y0:y1, :][:, cols]

    assert tiled_cropped.values.shape == expected.shape
    assert np.array_equal(np.isnan(tiled_cropped.values), np.isnan(expected))
    finite = ~np.isnan(expected)
    assert finite.sum() > 0, "test setup should produce overlapping finite pixels"
    assert np.allclose(tiled_cropped.values[finite], expected[finite])

    assert np.array_equal(tiled_cropped.coords["lat"].values, full.coords["lat"].values[y0:y1, :][:, cols])
    assert np.array_equal(tiled_cropped.coords["lon"].values, full.coords["lon"].values[y0:y1, :][:, cols])


def test_cropped_reprojection_matches_full_reprojection_for_a_tile_near_the_edge_of_the_visible_disk():
    """A second, harder tile position than the equatorial one above --
    near the satellite's own high-latitude visibility limit, where
    perspective distortion is strongest and an approximate source crop
    would be most likely to clip real needed pixels."""
    data = _synthetic_source()
    full_target = common_grid(resolution_km=30.0)
    # A narrow band near the top of the grid (+66.5deg) -- within this
    # satellite's real visibility (~81.3deg equatorial half-width, still
    # visible up to the grid's own +-66.5deg cutoff) but far from the
    # equator, where geos viewing-angle distortion is largest.
    tile = full_target[20:30, :]
    y0, y1 = 20, 30

    full = reproject_to_common_grid(data, full_target)
    tiled_cropped = reproject_to_cropped_grid(data, tile, SUB_SATELLITE_LON_DEG, MAX_VIEWING_ANGLE_DEG)

    cols = tiled_cropped.attrs["crop_columns"]
    expected = full.values[y0:y1, :][:, cols]

    assert tiled_cropped.values.shape == expected.shape
    assert np.array_equal(np.isnan(tiled_cropped.values), np.isnan(expected))
    finite = ~np.isnan(expected)
    assert finite.sum() > 0, "test setup should produce overlapping finite pixels near the visibility edge"
    assert np.allclose(tiled_cropped.values[finite], expected[finite])


def _synthetic_reversed_extent_source(size=400, lon_0=45.5):
    """A synthetic source area with a REVERSED area_extent (llx > urx,
    lly > ury), matching a real characteristic confirmed for
    Meteosat SEVIRI's native area (its south-to-north scan orientation
    gives it negative pixel_size_x/pixel_size_y, e.g. -3000.4 both, vs.
    the positive values every OTHER synthetic source in this file uses).
    Exists specifically to regression-guard a bug found via an
    old-vs-new Tier A pipeline output comparison: cropping via
    `AreaDefinition.__getitem__` (`source_area[y_slice, x_slice]`, this
    function's first implementation) silently introduced a small (~1km
    longitude / ~90m latitude, against Meteosat-09 data) precision drift
    for exactly this negative-pixel_size case -- invisible in every OTHER
    synthetic test in this file, which all use a normal (positive
    pixel_size) extent and therefore would never have caught it."""
    area = AreaDefinition(
        "src",
        "src",
        "src",
        {"proj": "geos", "h": 35786023, "lon_0": lon_0, "a": 6378137, "b": 6356752.3, "sweep": "y"},
        size,
        size,
        (5434894.9, 5434894.9, -5434894.9, -5434894.9),  # reversed, unlike every other test area here
    )
    assert area.pixel_size_x < 0 and area.pixel_size_y < 0, "test setup must actually exercise the negative-pixel_size case"
    rng = np.random.default_rng(0)
    values = rng.uniform(200.0, 300.0, size=(size, size)).astype(np.float32)
    data = xr.DataArray(values, dims=("y", "x"))
    data.attrs["area"] = area
    return data


def test_cropped_reprojection_matches_full_reprojection_for_a_reversed_extent_source():
    """Regression test for the bug found via a Tier A old-vs-new output
    comparison (2026-08-03): a source area with negative pixel_size
    (see _synthetic_reversed_extent_source) must still crop exactly,
    matching this file's other exact-equivalence tests. This test WOULD
    have failed before the get_lonlats(data_slice=...)-based fix (the
    prior AreaDefinition.__getitem__-based crop passed every other test in
    this file, all of which use a normal-orientation source area, and only
    a production Meteosat SEVIRI run exposed the bug)."""
    data = _synthetic_reversed_extent_source()
    full_target = common_grid(resolution_km=30.0)
    tile, y0, y1 = _narrow_equatorial_tile(full_target)

    full = reproject_to_common_grid(data, full_target)
    tiled_cropped = reproject_to_cropped_grid(data, tile, 45.5, MAX_VIEWING_ANGLE_DEG)

    cols = tiled_cropped.attrs["crop_columns"]
    expected = full.values[y0:y1, :][:, cols]

    assert tiled_cropped.values.shape == expected.shape
    assert np.array_equal(np.isnan(tiled_cropped.values), np.isnan(expected))
    finite = ~np.isnan(expected)
    assert finite.sum() > 0, "test setup should produce overlapping finite pixels"
    assert np.array_equal(tiled_cropped.values[finite], expected[finite]), (
        "reversed-extent (negative pixel_size) source area must resample identically to the uncropped reference"
    )


def test_crop_snaps_to_source_edge_when_close_to_it():
    """Regression test for a second bug found via the same Tier A
    old-vs-new comparison (2026-08-04, after the reversed-extent fix
    above): `get_array_indices_from_lonlat` becomes numerically imprecise
    for target points close to a satellite's own horizon (the geos
    forward/inverse projection's Jacobian is near-singular there).
    Against Meteosat-10 data: for target points at the project's own
    -66.5deg grid edge (near this satellite's horizon from its 0deg E
    position), the true nearest native pixel was 11 rows outside the
    margin_px-padded crop boundary -- silently dropping 8890 pixels of
    coverage in a Tier A run, and producing physically implausible values
    (a 151.7K TIR1 pixel, colder than any real Earth thermal-window
    brightness temperature) at nearby pixels that DID get a (wrong)
    match.

    Reproducing that exact numerical instability synthetically is hard --
    it's an intrinsic property of the geos+CRS inverse transform very
    close to the true horizon, not something a small hand-built geos area
    reliably exhibits. This test instead verifies the actual fix
    mechanically: whenever the computed crop boundary already lands close
    to the source array's own true edge (within `_EDGE_SNAP_FRACTION`),
    it must snap all the way out to that edge rather than stopping at a
    small margin-padded boundary that a near-horizon precision error could
    still fall outside of."""
    data = _synthetic_source(size=200)
    source_area = data.attrs["area"]
    # A target point whose OWN native index (row=5 of 200, i.e. 2.5%) is
    # well within _EDGE_SNAP_FRACTION=0.1 (10%) of the source's true top
    # edge (row 0).
    near_edge_lon, near_edge_lat = source_area.get_lonlat(5, 100)
    target_lons = np.array([[near_edge_lon]])
    target_lats = np.array([[near_edge_lat]])

    cropped = crop_source_to_target_lonlat(data, target_lons, target_lats, RADIUS_OF_INFLUENCE_M)

    _, _, _, _, y0, y1, x0, x1 = cropped.attrs["_source_cache_identity"]
    assert y0 == 0, (
        f"crop boundary (row {5}, margin-padded to ~row 2) is within 10% of the source's true top edge "
        f"(row 0) -- must snap all the way to it, got y0={y0}"
    )
