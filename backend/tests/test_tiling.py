"""Unit tests for tiling.py's compute_row_tiles -- the row-range
computation tile-major processing depends on for correctness (gapless,
non-overlapping, exact coverage) and for cache reuse across separate runs
(determinism)."""

from __future__ import annotations

import pytest

from global_mosaic.processing.tiling import DEFAULT_FIXED_OVERHEAD_GB, TileRowRange, compute_row_tiles


def _assert_valid_tiling(tiles: list[TileRowRange], height: int) -> None:
    assert tiles[0].y0 == 0, "must start at row 0"
    assert tiles[-1].y1 == height, "must end at exactly the grid height"
    for t in tiles:
        assert t.y1 > t.y0, "every tile must have positive height"
    for a, b in zip(tiles, tiles[1:]):
        assert a.y1 == b.y0, f"gap or overlap between {a} and {b}"


def test_tier_b_scale_tiling_is_valid_and_matches_measured_budget():
    tiles = compute_row_tiles(width=40075, height=14806, memory_budget_gb=4.0, include_sza=True)
    _assert_valid_tiling(tiles, 14806)
    # Real measurement: accumulator alone is 12.16GB at full
    # height -- a 4GB budget must produce more than one tile.
    assert len(tiles) > 1


def test_tier_a_scale_tiling_is_valid():
    tiles = compute_row_tiles(width=20038, height=7403, memory_budget_gb=4.0, include_sza=False)
    _assert_valid_tiling(tiles, 7403)


def test_small_grid_degenerates_to_one_tile():
    """A grid whose entire accumulator+lonlats(+sza) already fits the
    budget shouldn't be artificially split."""
    tiles = compute_row_tiles(width=100, height=100, memory_budget_gb=4.0, include_sza=True)
    assert tiles == [TileRowRange(0, 100)]


def test_including_sza_never_produces_fewer_tiles_than_excluding_it():
    """SZA adds real per-row cost (Tier B only) -- for the same grid, the
    include_sza=True budget can only be equal or tighter, never looser."""
    kwargs = dict(width=40075, height=14806, memory_budget_gb=4.0)
    with_sza = compute_row_tiles(include_sza=True, **kwargs)
    without_sza = compute_row_tiles(include_sza=False, **kwargs)
    assert len(with_sza) >= len(without_sza)


def test_deterministic_same_input_same_output():
    """Required for cross-run disk-cache reuse (GOES/INSAT neighbour-info,
    lonlats, SZA) -- a tier's tile boundaries must be identical run to
    run, not just internally consistent within one run."""
    a = compute_row_tiles(width=40075, height=14806, memory_budget_gb=4.0, include_sza=True)
    b = compute_row_tiles(width=40075, height=14806, memory_budget_gb=4.0, include_sza=True)
    assert a == b


def test_non_divisible_height_remainder_handled_by_last_tile():
    tiles = compute_row_tiles(width=1000, height=1005, memory_budget_gb=4.0, fixed_overhead_gb=3.999999)
    _assert_valid_tiling(tiles, 1005)
    # rows_per_tile forced to 1 by an almost-fully-consumed budget --
    # every tile except possibly none should be size 1, remainder exact.
    assert sum(t.height for t in tiles) == 1005


def test_budget_at_or_below_fixed_overhead_raises():
    with pytest.raises(ValueError):
        compute_row_tiles(width=1000, height=1000, memory_budget_gb=1.0, fixed_overhead_gb=1.5)
    with pytest.raises(ValueError):
        compute_row_tiles(width=1000, height=1000, memory_budget_gb=1.5, fixed_overhead_gb=1.5)


def test_nonpositive_dimensions_raise():
    with pytest.raises(ValueError):
        compute_row_tiles(width=0, height=100, memory_budget_gb=4.0)
    with pytest.raises(ValueError):
        compute_row_tiles(width=100, height=-5, memory_budget_gb=4.0)


def test_default_fixed_overhead_is_exported_and_used_by_default():
    explicit = compute_row_tiles(
        width=40075, height=14806, memory_budget_gb=4.0, include_sza=True, fixed_overhead_gb=DEFAULT_FIXED_OVERHEAD_GB
    )
    implicit = compute_row_tiles(width=40075, height=14806, memory_budget_gb=4.0, include_sza=True)
    assert explicit == implicit
