"""Row-tile sizing for tile-major mosaic processing.

Real measurement found one satellite's Tier B candidate
build peaks at ~26.9GB regardless of which satellite -- the target RHEL
production box has only 16GB total RAM, so the pipeline cannot run there
at all in its per-satellite-isolation form. Directly measured
(tracemalloc) contributors that are NOT satellite-specific and don't
shrink no matter how per-satellite work is optimized: SelectionAccumulator's
7 full-grid arrays (12.16GB at Tier B scale), the cached lon/lat grid
(~4.42GB), and the solar-zenith-angle grid (~2.21GB). Tile-major
processing -- row-tiling the target grid so the accumulator, lonlats, and
SZA are all tile-sized rather than full-grid-sized -- is the fix; this
module computes how many rows per tile a given memory budget allows.

This module is deliberately pyresample-free: it operates on plain
width/height ints, not AreaDefinition objects, so it's trivially testable
in isolation. The caller is responsible for turning a TileRowRange into
an actual sliced AreaDefinition via `full_area[y0:y1, :]` -- see that
call's own note in reproject.py about why tile areas must always be
produced this way (populates `crop_offset`, which the tile-aware caches
in reproject.py/run_phase2_tier.py depend on for correctness, not just
performance).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from global_mosaic.processing.select import SelectionAccumulator

_LONLATS_BYTES_PER_ROW_PER_PIXEL = 8  # 2 float32 arrays (lon, lat)
_SZA_BYTES_PER_ROW_PER_PIXEL = 4  # 1 float32 array

DEFAULT_FIXED_OVERHEAD_GB = 1.5
"""Budget reserved for costs that do NOT shrink with tile size: a
satellite's downsampled source array before reprojection (e.g. ~496MB for
Tier B VIS, confirmed real-file-size order of magnitude) plus
general Python/xarray/dask/satpy overhead. Not tied to a specific
measurement (that per-satellite floor hasn't been measured in isolation
yet) -- treat this as a starting estimate to validate empirically (this
project's established practice: measure, don't assume) via
the ctypes GetProcessMemoryInfo self-measurement technique proven earlier
today, not as a number to trust blindly."""


@dataclass(frozen=True)
class TileRowRange:
    """A row range [y0, y1) into a target grid's height dimension --
    half-open like Python slicing, so `y1 - y0` is the tile's own height
    and array[y0:y1, :] / nc_var[y0:y1, :] both work directly."""

    y0: int
    y1: int

    @property
    def height(self) -> int:
        """This tile's row count (``y1 - y0``)."""
        return self.y1 - self.y0


def compute_row_tiles(
    width: int,
    height: int,
    memory_budget_gb: float = 4.0,
    include_sza: bool = False,
    fixed_overhead_gb: float = DEFAULT_FIXED_OVERHEAD_GB,
) -> list[TileRowRange]:
    """Split a target grid's rows into memory-budget-sized tiles.

    Splits [0, height) into contiguous, non-overlapping tiles sized so
    that one tile's accumulator + lonlats + (optionally) SZA arrays fit
    within `memory_budget_gb` minus `fixed_overhead_gb`.

    Deterministic for a given (width, height, budget, include_sza) --
    required so the same tier's tile boundaries are identical run to run,
    which is what lets the tile-aware disk caches in reproject.py (GOES/
    INSAT neighbour-info) and _build_sza_grid actually get reused across
    separate future runs rather than only within one run.

    `include_sza=True` for Tier B (VIS/SWIR, shares one SZA grid per
    tier) -- Tier A has no solar-zenith field at all, so leave this False
    there; a smaller per-row cost means fewer, larger tiles, and Tier A's
    grid is already 1/4 Tier B's pixel count, so it commonly degenerates
    to a single tile (no tiling actually needed) here.

    Always returns at least one tile (even if the whole grid already fits
    the budget) and the last tile absorbs any remainder rather than being
    a separate short tile plus a whole extra tile.

    Args:
        width: Target grid width, in pixels.
        height: Target grid height, in pixels.
        memory_budget_gb: Total memory budget for one tile's arrays plus
            `fixed_overhead_gb`, in GB.
        include_sza: Whether to reserve per-row budget for a
            solar-zenith-angle array too (Tier B only).
        fixed_overhead_gb: Memory reserved for costs that don't shrink
            with tile size (per-satellite source arrays, general
            Python/xarray/dask/satpy overhead).

    Returns:
        List of contiguous, non-overlapping :class:`TileRowRange`
        covering ``[0, height)``, at least one tile, with the last tile
        absorbing any remainder rows.

    Raises:
        ValueError: If `width`/`height` are not positive, or
            `memory_budget_gb` does not exceed `fixed_overhead_gb`.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"width/height must be positive, got {width}x{height}")
    if memory_budget_gb <= fixed_overhead_gb:
        raise ValueError(
            f"memory_budget_gb ({memory_budget_gb}) must exceed fixed_overhead_gb "
            f"({fixed_overhead_gb}) -- nothing left for the actual per-tile arrays"
        )

    per_row_bytes = SelectionAccumulator.bytes_per_row(width) + width * _LONLATS_BYTES_PER_ROW_PER_PIXEL
    if include_sza:
        per_row_bytes += width * _SZA_BYTES_PER_ROW_PER_PIXEL

    row_budget_bytes = (memory_budget_gb - fixed_overhead_gb) * (1024**3)
    rows_per_tile = max(1, math.floor(row_budget_bytes / per_row_bytes))

    n_tiles = math.ceil(height / rows_per_tile)
    tiles = []
    for i in range(n_tiles):
        y0 = i * rows_per_tile
        y1 = min(y0 + rows_per_tile, height)
        tiles.append(TileRowRange(y0, y1))
    return tiles
