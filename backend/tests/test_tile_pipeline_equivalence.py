"""Proves the mathematically load-bearing claim behind tile-major
processing: folding candidates into a SEPARATE, ROW-SLICED
SelectionAccumulator per tile and assembling the tiles back together
produces IDENTICAL output to folding the same candidates into ONE
full-grid SelectionAccumulator. This is what actually justifies
run_phase2_tier.py's build_band_mosaic_tiled -- lat/lon (rectilinear,
tested elsewhere), effective_resolution_km and blend.feather_seams
(both confirmed purely per-pixel with no cross-pixel dependency when
this design was reviewed) mean SelectionAccumulator's own per-pixel
running top-2 reduction is the only place a tiling bug could silently
produce wrong-but-plausible output instead of a crash. No satpy/network
dependency -- synthetic candidates only, mirroring test_select_streaming_
equivalence.py's existing pattern for the (non-tiled) streaming design."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from global_mosaic.processing.select import Candidate, SelectionAccumulator
from global_mosaic.processing.tiling import TileRowRange, compute_row_tiles

TARGET = dt.datetime(2026, 7, 30, 12, 0, 0)


def _make_candidates(height: int, width: int, seed: int) -> list[Candidate]:
    """Three overlapping satellites at a moderate synthetic grid size,
    with real footprint cropping (columns) and some NaN/no-coverage
    regions -- close in shape to what build_candidate_for_tile actually
    produces, without needing real satpy/network data."""
    rng = np.random.default_rng(seed)
    candidates = []

    # Satellite A: full width, moderate resolution, one NaN band.
    a_data = rng.uniform(200.0, 300.0, size=(height, width)).astype(np.float32)
    a_res = rng.uniform(2.0, 4.0, size=(height, width)).astype(np.float32)
    nan_rows = slice(height // 3, height // 3 + max(1, height // 10))
    a_data[nan_rows, :] = np.nan
    a_res[nan_rows, :] = np.nan
    candidates.append(Candidate("goes-19", _da(a_data), _da(a_res), TARGET, columns=np.arange(width)))

    # Satellite B: cropped to the left half of the grid (real footprint-
    # cropping shape), finer resolution -> should win where it overlaps A.
    b_cols = np.arange(0, width // 2)
    b_data = rng.uniform(200.0, 300.0, size=(height, len(b_cols))).astype(np.float32)
    b_res = rng.uniform(0.5, 1.5, size=(height, len(b_cols))).astype(np.float32)
    candidates.append(Candidate("himawari-09", _da(b_data), _da(b_res), TARGET + dt.timedelta(minutes=2), columns=b_cols))

    # Satellite C: cropped to the right half, resolution close to A's ->
    # real chance of ties (exercises tie_mask/blend, not just hard cuts).
    c_cols = np.arange(width // 2, width)
    c_data = rng.uniform(200.0, 300.0, size=(height, len(c_cols))).astype(np.float32)
    c_res = a_res[:, c_cols] + rng.uniform(-0.3, 0.3, size=(height, len(c_cols))).astype(np.float32)
    candidates.append(Candidate("meteosat-10", _da(c_data), _da(c_res), TARGET - dt.timedelta(minutes=1), columns=c_cols))

    return candidates


def _da(values):
    import xarray as xr

    return xr.DataArray(values, dims=("y", "x"))


def _fold_full_grid(candidates: list[Candidate], height: int, width: int, blend_margin_km: float):
    accumulator = SelectionAccumulator(grid_shape=(height, width))
    for c in candidates:
        accumulator.update(c)
    return accumulator.finalize(excluded_by_sync=[], blend_margin_km=blend_margin_km)


def _fold_tiled(candidates: list[Candidate], tiles: list[TileRowRange], width: int, blend_margin_km: float):
    """Mirrors build_band_mosaic_tiled's real loop shape: for each tile,
    slice every candidate's rows (columns untouched -- footprint cropping
    is a width-only concept, orthogonal to row-tiling, confirmed when
    this design was reviewed), fold into a tile-sized accumulator, and
    place its finalized result into the right rows of full-size output
    arrays."""
    full_value = np.full((tiles[-1].y1, width), np.nan, dtype=np.float32)
    full_satellite_id = np.zeros((tiles[-1].y1, width), dtype=np.int16)
    full_resolution_km = np.full((tiles[-1].y1, width), np.nan, dtype=np.float32)
    full_tie_mask = np.zeros((tiles[-1].y1, width), dtype=bool)
    full_runner_up_id = np.zeros((tiles[-1].y1, width), dtype=np.int16)
    full_runner_up_res = np.full((tiles[-1].y1, width), np.nan, dtype=np.float32)

    for tile in tiles:
        tile_accumulator = SelectionAccumulator(grid_shape=(tile.height, width))
        for c in candidates:
            tile_data = c.data.values[tile.y0 : tile.y1, :]
            tile_res = c.resolution_km.values[tile.y0 : tile.y1, :]
            tile_candidate = Candidate(c.satellite_key, _da(tile_data), _da(tile_res), c.mid_time, columns=c.columns)
            tile_accumulator.update(tile_candidate)
        result = tile_accumulator.finalize(excluded_by_sync=[], blend_margin_km=blend_margin_km)

        full_value[tile.y0 : tile.y1, :] = result.value.values
        full_satellite_id[tile.y0 : tile.y1, :] = result.satellite_id.values
        full_resolution_km[tile.y0 : tile.y1, :] = result.resolution_km.values
        full_tie_mask[tile.y0 : tile.y1, :] = result.tie_mask.values
        if result.runner_up_satellite_id is not None:
            full_runner_up_id[tile.y0 : tile.y1, :] = result.runner_up_satellite_id.values
            full_runner_up_res[tile.y0 : tile.y1, :] = result.runner_up_resolution_km.values

    return full_value, full_satellite_id, full_resolution_km, full_tie_mask, full_runner_up_id, full_runner_up_res


@pytest.mark.parametrize(
    "tile_ranges",
    [
        [(0, 40)],  # single tile, degenerate case
        [(0, 20), (20, 40)],  # even split
        [(0, 7), (7, 19), (19, 31), (31, 40)],  # non-divisible, 4 uneven tiles
    ],
)
@pytest.mark.parametrize("blend_margin_km", [0.0, 5.0])
def test_tiled_folding_matches_full_grid_folding(tile_ranges, blend_margin_km):
    height, width = 40, 24
    candidates = _make_candidates(height, width, seed=0)
    tiles = [TileRowRange(y0, y1) for y0, y1 in tile_ranges]
    assert tiles[-1].y1 == height, "test setup: tiles must cover the full synthetic grid"

    full = _fold_full_grid(candidates, height, width, blend_margin_km)
    tiled_value, tiled_sat_id, tiled_res, tiled_tie, tiled_ru_id, tiled_ru_res = _fold_tiled(
        candidates, tiles, width, blend_margin_km
    )

    assert np.array_equal(full.value.values, tiled_value, equal_nan=True)
    assert np.array_equal(full.satellite_id.values, tiled_sat_id)
    assert np.array_equal(full.resolution_km.values, tiled_res, equal_nan=True)
    assert np.array_equal(full.tie_mask.values, tiled_tie)
    if full.runner_up_satellite_id is not None:
        assert np.array_equal(full.runner_up_satellite_id.values, tiled_ru_id)
        assert np.array_equal(full.runner_up_resolution_km.values, tiled_ru_res, equal_nan=True)


def test_tile_boundary_row_is_correct_not_just_the_aggregate():
    """A bug that only corrupts rows exactly at a tile seam (an off-by-
    one in tile ranges, or a row double-counted/dropped between tiles)
    could easily hide inside an aggregate array-equality check if it's
    a small fraction of total pixels -- check the exact seam rows
    explicitly."""
    height, width = 40, 24
    candidates = _make_candidates(height, width, seed=1)
    tile_ranges = [(0, 7), (7, 19), (19, 31), (31, 40)]
    tiles = [TileRowRange(y0, y1) for y0, y1 in tile_ranges]

    full = _fold_full_grid(candidates, height, width, blend_margin_km=5.0)
    tiled_value, tiled_sat_id, _, _, _, _ = _fold_tiled(candidates, tiles, width, blend_margin_km=5.0)

    seam_rows = [t.y1 - 1 for t in tiles[:-1]] + [t.y0 for t in tiles[1:]]  # last row of each tile + first row of the next
    for row in seam_rows:
        assert np.array_equal(full.value.values[row, :], tiled_value[row, :], equal_nan=True), f"row {row} mismatch"
        assert np.array_equal(full.satellite_id.values[row, :], tiled_sat_id[row, :]), f"row {row} satellite_id mismatch"


def test_real_tier_b_tile_ranges_are_used_correctly_end_to_end():
    """Sanity check that compute_row_tiles' own output (not a hand-picked
    tile_ranges list) plugs into the fold-and-assemble logic correctly,
    at a small-but-nontrivial synthetic grid."""
    height, width = 100, 60
    candidates = _make_candidates(height, width, seed=2)
    # Budget deliberately just barely above fixed_overhead_gb -- leaves
    # only ~107KB for row-scaling cost at this tiny synthetic width, well
    # under what 100 rows would need, to force a real multi-tile split
    # rather than the single-tile degenerate case a generous budget would
    # give at this grid's small size.
    tiles = compute_row_tiles(width, height, memory_budget_gb=1.0001, fixed_overhead_gb=1.0, include_sza=False)
    assert len(tiles) > 1, "test setup should force real tiling, not a degenerate single tile"

    full = _fold_full_grid(candidates, height, width, blend_margin_km=2.0)
    tiled_value, tiled_sat_id, tiled_res, tiled_tie, _, _ = _fold_tiled(candidates, tiles, width, blend_margin_km=2.0)

    assert np.array_equal(full.value.values, tiled_value, equal_nan=True)
    assert np.array_equal(full.satellite_id.values, tiled_sat_id)
    assert np.array_equal(full.resolution_km.values, tiled_res, equal_nan=True)
    assert np.array_equal(full.tie_mask.values, tiled_tie)
