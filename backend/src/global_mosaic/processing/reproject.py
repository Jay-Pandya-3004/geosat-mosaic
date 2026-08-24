"""Reprojects each satellite's native geostationary grid onto the common
global mosaic grid using pyresample.

Common grid: a plain equirectangular (plate carree) lon/lat grid spanning
the full ±66.5 deg latitude range all 6 GEO satellites can ever see (see
README's "Known limitation" -- no GEO satellite can see the poles, this
is a hard geometric limit, not a bug). This matches the precedent set by
NOAA's own GMGSI composite product ("~8 km horizontal, on a plain lon/lat
grid (no map projection)") rather
than a fancier equal-area projection -- simplest correct choice to prove
the pipeline in Phase 0.

Each satellite's *native* AreaDefinition is NOT built by hand from
sat_metadata.py's static Nominal_Longitude_Deg -- satpy's own readers
already compute it per-file from the real station-kept position and
scan geometry (exposed as `data.attrs["area"]` on any satpy-loaded
DataArray, e.g. via the custom INSAT reader's `get_area_def`, or
abi_l1b/ahi_hsd/seviri_l1b_native's built-in area calculation). This
module only needs to resample from that native area onto the common grid.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import xarray as xr
from dotenv import load_dotenv
from pyresample.geometry import AreaDefinition

load_dotenv()

_KM_PER_DEGREE_AT_EQUATOR = 111.32
_MAX_ABS_LATITUDE_DEG = 66.5
"""Quality cutoff, not the true geometric horizon (~81.3deg, see
sat_metadata.py's Max_Viewing_Angle_Deg) -- see README's "Known
limitation" for the original ~15-20deg-of-disk-unreliable reasoning.
Deliberately kept at 66.5 rather than extended to 72.7 to match NOAA's
GMGSI product: resolution.py's numbers show even the best-case point at
72.7deg (directly under a satellite's own meridian) already has effective
resolution ~15km for a 2km-nadir band, worse than GMGSI's own native 8km
grid everywhere else -- GMGSI includes that band for visual/coverage
completeness, not because the data is actually good there."""

_CACHE_DIR = Path(
    os.environ.get("GLOBAL_MOSAIC_CACHE_DIR", str(Path(__file__).resolve().parents[3] / "data" / "cache"))
)
"""Neighbor-info (task 11) and lonlats caches can reach multi-GB per
entry for Tier B's 1km grid (840MB-3.4GB per satellite), and GOES's
entries in particular are permanently reusable (its exposed geometry
never varies run to run, unlike Himawari/Meteosat's station-kept
position, which drifts and only reuses within one scan cycle). Override
via GLOBAL_MOSAIC_CACHE_DIR in backend/.env (see .env.example) to point
this at a disk with room -- defaults to backend/data/cache, which filled
a 50GB project drive during testing."""

TIER_A_RESOLUTION_KM = 2.0
"""Phase 2 MIR/WV/TIR1/TIR2 tier grid spacing -- pegged to GOES/Himawari's
2km nadir resolution for these bands (the finest actually available per
each satellite's channel table), NOT to Phase 0's 4.0km default below.
That default was anchored to INSAT because INSAT happens to define the
shared 6-channel set (fewest channels) -- but channel-set membership and
pixel resolution are unrelated properties, and pegging the grid to the
coarsest contributor silently discards already-available GOES/Himawari
detail across most of the globe wherever they're select.py's actual
per-pixel winner. 2km is only a 4x pixel-count increase over 4km, a
worthwhile tradeoff for keeping that detail."""

TIER_B_RESOLUTION_KM = 1.0
"""Phase 2 VIS/SWIR tier grid spacing -- a deliberate ceiling, NOT the
absolute finest available (GOES/Himawari's native VIS is 0.5km, ~64x the
pixel count of Phase 0's 4km grid -- measured and judged impractical for
a continuous archive). 1km is a considered middle
ground (4x Phase 0's pixel count, same order as Tier A), accepting a
small loss of GOES/Himawari's absolute finest VIS/SWIR detail."""


def common_grid(resolution_km: float = 4.0) -> AreaDefinition:
    """Build the global equirectangular target grid.

    Global equirectangular AreaDefinition at ~`resolution_km` degree
    spacing (computed at the equator; a plate carree grid is not
    physically uniform in km at other latitudes -- same simplification
    GMGSI itself uses, see module docstring), spanning the full
    ±66.5 deg GEO-visible latitude range and the full 360 deg longitude.

    Args:
        resolution_km: Target grid spacing at the equator, in km.

    Returns:
        The global equirectangular ``AreaDefinition``.
    """
    degree_step = resolution_km / _KM_PER_DEGREE_AT_EQUATOR
    width = round(360.0 / degree_step)
    height = round((2 * _MAX_ABS_LATITUDE_DEG) / degree_step)

    return AreaDefinition(
        area_id="global_mosaic_common_grid",
        description=f"Global equirectangular grid, ~{resolution_km} km at equator, "
        f"+/-{_MAX_ABS_LATITUDE_DEG} deg latitude (GEO-visible range only, see README)",
        proj_id="longlat",
        projection={"proj": "longlat", "datum": "WGS84"},
        width=width,
        height=height,
        area_extent=(-180.0, -_MAX_ABS_LATITUDE_DEG, 180.0, _MAX_ABS_LATITUDE_DEG),
    )


_lonlats_cache: dict[tuple[int, int, tuple[int, int]], tuple[np.ndarray, np.ndarray]] = {}


def clear_in_process_caches() -> None:
    """Clear the in-process lonlats and neighbour-info caches.

    Empties _lonlats_cache and _neighbour_info_cache (the disk layer
    underneath both is untouched -- this only affects same-process
    reuse). Exists for tile-major callers (see run_phase2_tier.py's
    build_band_mosaic_tiled): once cache keys became tile-aware
    (crop_offset included, see _cached_lonlats' own docstring for why),
    every distinct tile got its own cache entry that nothing ever
    evicted -- just 3 of Tier B's 8 tiles already left 1.77GB resident in
    _lonlats_cache alone, and with _neighbour_info_cache accumulating one
    entry per (tile, satellite) pair too (up to 48 for a 6-satellite/8-tile
    band, unconditionally -- it caches in-process regardless of whether
    an entry also persists to disk), this fully explained a ~26GB peak
    that per-satellite-isolation-replacement tiling was supposed to have
    fixed. There is no legitimate in-process reuse to lose by clearing
    per tile: under tile-major processing, each (tile, satellite)
    combination is visited exactly once per run by construction, so this
    cache was providing zero benefit within a run while accumulating
    full-grid-scale memory across many small entries. The disk layer
    (GOES/INSAT neighbour-info, all lonlats) still gives cross-run reuse,
    unaffected by this.

    Returns:
        None.
    """
    _lonlats_cache.clear()
    _neighbour_info_cache.clear()


def _cached_lonlats(area: AreaDefinition) -> tuple[np.ndarray, np.ndarray]:
    """Get an AreaDefinition's lon/lat arrays, cached in-process and on disk.

    AreaDefinition.get_lonlats() recomputes two fresh full-grid float64
    arrays (~1.13 GB each at Tier A's 2km resolution, ~0.9s each) on every
    call, with no caching of its own. reproject_to_common_grid is called
    once per candidate satellite per band (up to 24 times in one
    6-satellite/4-band Tier A run), and every caller passes the SAME
    `target_area` object (built once in main() and reused, not a fresh
    AreaDefinition per call) -- so caching eliminates ~54 GB of redundant
    large-array churn per run, which was directly responsible for
    MemoryErrors reproduced against actual satellite data at Tier A's 2km
    grid.

    Keyed by `(width, height, crop_offset)`, NOT `id(area)` -- an earlier
    version of this cache keyed on object identity, justified by "one
    grid object per run, not many short-lived AreaDefinitions." That
    assumption broke once per-satellite tile-based processing landed:
    tile-major processing calls `full_area[y0:y1, :]` once per tile,
    producing many DIFFERENT AreaDefinition objects that often share the
    same `(width, height)` (every tile has the full grid's width; most
    tiles but the remainder share the same height too) -- under the old
    id()-keyed cache this would have been harmless (each new object gets
    its own cache miss), but the risk was one level down in the equally
    width/height-keyed disk cache below: two tiles at different latitude
    bands but the same shape are indistinguishable by `(width, height)`
    alone, so the second tile would have silently reused the first tile's
    lon/lat values -- wrong data, no exception.
    `crop_offset` (row, col offset from whatever area this was sliced
    from -- (0, 0) for an unsliced area, always populated by
    AreaDefinition's own `__init__`) is exactly what pyresample's own
    `__getitem__` already computes to describe "which slice is this,"
    so reusing it here is cheaper than re-hashing area_extent and
    self-documenting.

    Args:
        area: The grid AreaDefinition (or a tile-cropped slice of one)
            to get lon/lat arrays for.

    Returns:
        Tuple of ``(lons, lats)`` float32 arrays, same shape as `area`.
    """
    key = (area.width, area.height, tuple(area.crop_offset))
    if key in _lonlats_cache:
        return _lonlats_cache[key]

    # Disk layer, on top of the in-process cache above: this project's
    # driver isolates work across subprocesses (see run_phase2_tier.py)
    # precisely to dodge memory buildup within one process -- but that
    # means a FRESH process, and therefore a cold in-process cache, on
    # every band/satellite (or, under tile-major processing, every
    # tile). get_lonlats() is a pure function of the grid's shape/extent,
    # so the result is identical across every run at a given resolution
    # -- write it once, every subsequent process (this run or a future
    # one) loads instead of recomputing. crop_offset included in the
    # filename for the same reason as the in-process key above.
    disk_path = _CACHE_DIR / f"lonlats_{area.width}x{area.height}_{area.crop_offset[0]}_{area.crop_offset[1]}.npz"
    if disk_path.exists():
        with np.load(disk_path) as cached:
            lons, lats = cached["lons"], cached["lats"]
    else:
        lons, lats = area.get_lonlats()
        # float32, not get_lonlats()'s native float64 -- halves this
        # cache's own footprint (Tier B's 1km grid: ~4.7 GB combined
        # instead of ~9.5 GB). numpy's dtype-preserving math (NEP 50 weak
        # promotion, this numpy version) means feeding float32 lat/lon
        # into resolution.py's whole view-zenith-angle/resolution chain
        # keeps every intermediate there float32 too, without needing to
        # touch resolution.py itself. Precision is still far more than
        # needed: float32 gives ~7 significant digits, i.e. sub-meter
        # precision in degrees at these latitudes, for pixels that are
        # themselves 1-2 km.
        lons, lats = lons.astype(np.float32), lats.astype(np.float32)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Write to a temp name then rename -- atomic on the same
        # filesystem, so a concurrent subprocess (another band running at
        # the same time, see run_phase2_tier.py) can never observe a
        # partially-written cache file. Temp name must still END in
        # exactly ".npz" -- np.savez silently APPENDS ".npz" to any
        # filename that doesn't already end with it, so a naive
        # ".npz.tmp" name actually got written as ".npz.tmp.npz" and the
        # later rename couldn't find it.
        tmp_path = disk_path.parent / f"{disk_path.stem}.tmp.npz"
        np.savez(tmp_path, lons=lons, lats=lats)
        tmp_path.replace(disk_path)

    _lonlats_cache[key] = (lons, lats)
    return _lonlats_cache[key]


def downsample_if_finer_than_target(
    data: xr.DataArray, native_resolution_km: float, target_resolution_km: float
) -> xr.DataArray:
    """Downsample `data` if its native resolution is finer than the target grid's.

    Block-averages `data` down by an integer factor if its native
    resolution is finer than the target tier grid's own resolution --
    e.g. GOES/Himawari's native VIS (0.5km) reprojected onto Tier B's
    1km grid. Nearest-neighbor resampling onto a coarser target can't
    express that extra native detail anyway (many source pixels map to
    the same target pixel regardless), so keeping the full native array
    around just to throw most of it away at the gather step is pure
    waste -- a GOES-18 VIS file is 21696x21696 (~470M pixels), bigger
    than Tier B's entire 593M-pixel target grid, for ONE satellite's ONE
    band.

    Uses pyresample's own AreaDefinition.aggregate() for the geometry
    (not hand-derived -- checked against a GOES VIS file that it
    preserves the exact area_extent, i.e. no registration shift) paired
    with xarray's coarsen(...).mean() for the data, the same combination
    satpy's own resamplers use internally. boundary="trim" (not "pad"):
    pyresample's aggregate() reduces width/height by floor division too,
    so the two must agree on how a non-evenly-divisible remainder is
    handled or the data and its area geometry would silently mismatch.

    Reprojecting the aggregated result vs. the full-resolution one for
    the same GOES-18 VIS file and target crop: extents matched exactly,
    finite-pixel counts differed by ~0.03%, mean absolute difference ~0.5
    (reflectance %) -- physically-expected smoothing from block-averaging,
    not a bug, and well within what a coarser target grid could express
    from the full-resolution data anyway.

    Args:
        data: satpy-loaded DataArray with its native ``.attrs["area"]``.
        native_resolution_km: This data's native resolution, in km.
        target_resolution_km: The target tier grid's resolution, in km.

    Returns:
        `data` unchanged if `native_resolution_km` is not finer than
        `target_resolution_km` (factor <= 1); otherwise a new
        block-averaged DataArray with its ``area`` attr updated to match.
    """
    factor = round(target_resolution_km / native_resolution_km)
    if factor <= 1:
        return data

    area = data.attrs["area"]
    aggregated_area = area.aggregate(x=factor, y=factor)
    aggregated_data = data.coarsen(x=factor, y=factor, boundary="trim").mean()
    aggregated_data.attrs = dict(data.attrs)
    aggregated_data.attrs["area"] = aggregated_area
    return aggregated_data


def reproject_to_common_grid(
    data: xr.DataArray, target_area: AreaDefinition, radius_of_influence_m: float = 20_000
) -> xr.DataArray:
    """Reproject a satellite's native data onto the full common target grid.

    Nearest-neighbor resamples `data` (a satpy-loaded DataArray with its
    native `.attrs["area"]`) onto `target_area`.

    Nearest-neighbor, not bilinear/interpolated, is Phase 0's deliberate
    choice -- simplest correct option with no interpolation artifacts to
    account for while proving reprojection + selection + blending works
    at all; revisit once the pipeline is verified end-to-end.

    `radius_of_influence_m` should be a few native pixels wide for
    whichever satellite/band is being resampled (e.g. ~20 km covers a
    IR-window ~2-4 km native pixel comfortably without pulling in data
    from many pixels away); tune per band if upsampling/downsampling
    ratios vary a lot across the 6 satellites' native resolutions.

    Args:
        data: satpy-loaded DataArray with its native ``.attrs["area"]``.
        target_area: The common target grid's ``AreaDefinition``.
        radius_of_influence_m: Nearest-neighbor search radius, in meters.

    Returns:
        A new DataArray on `target_area`'s grid, with ``lat``/``lon``
        coords attached and NaN where no source pixel was within
        `radius_of_influence_m`.

    Raises:
        ValueError: If `data.attrs` has no ``"area"`` key.
    """
    from pyresample.kd_tree import resample_nearest

    if "area" not in data.attrs:
        raise ValueError(
            "reproject_to_common_grid needs data.attrs['area'] (the "
            "satellite's native AreaDefinition) -- load `data` via satpy "
            "so its reader can compute this, don't pass a bare array."
        )
    source_area = data.attrs["area"]

    values = data.values
    resampled = resample_nearest(
        source_area,
        values,
        target_area,
        radius_of_influence=radius_of_influence_m,
        fill_value=np.nan,
    )

    lons, lats = _cached_lonlats(target_area)
    return xr.DataArray(
        resampled,
        dims=("y", "x"),
        coords={"lat": (("y", "x"), lats), "lon": (("y", "x"), lons)},
        attrs={k: v for k, v in data.attrs.items() if k != "area"},
    )


def visible_columns_for_satellite(target_area: AreaDefinition, sub_satellite_lon_deg: float, max_viewing_angle_deg: float) -> np.ndarray:
    """Compute the target-grid column indices one GEO satellite could possibly see.

    Column indices into `target_area`'s width covering one GEO
    satellite's conservative visible longitude range.

    A satellite at sub-satellite point (lon0, 0deg) can see up to a
    `max_viewing_angle_deg` central angle from that point in every
    direction (see resolution.py's `view_zenith_angle_deg` -- same
    horizon condition, gamma <= arccos(Re/rho), which is what
    sat_metadata.py's Max_Viewing_Angle_Deg -- ~81.3 deg for every
    satellite in this project -- already encodes). At the equator that
    central angle equals the longitude offset directly; moving toward the
    poles the visible longitude half-width narrows (~81.3 deg at the
    equator vs ~67.7 deg at this project's own +-66.5 deg grid limit).
    This function deliberately uses the wider EQUATORIAL half-width as a
    single conservative crop across the satellite's whole visible
    latitude band, rather than a tighter latitude-dependent wedge --
    simpler, and safe to over-include slightly rather than risk clipping
    data. There is no separate LATITUDE crop: every satellite's own
    north/south visibility limit (~81.3 deg) is already looser than this
    project's grid's own +-66.5 deg cutoff, so cropping columns alone
    already captures the full benefit.

    Handles antimeridian wraparound (not a hypothetical: GOES-West at
    -137deg and Himawari at 140.7deg both wrap past +-180deg at this
    +-81.3deg half-width) by returning column indices modulo the grid
    width, in order -- a caller gathering `array[:, cols]` gets a
    contiguous-looking crop even when it really wraps around the back of
    the grid.

    Args:
        target_area: The common target grid's ``AreaDefinition``.
        sub_satellite_lon_deg: The satellite's sub-satellite longitude,
            in degrees.
        max_viewing_angle_deg: The satellite's maximum viewing angle
            (equatorial half-width), in degrees.

    Returns:
        Column indices into `target_area`'s width, in order, wrapped
        modulo the grid width where the visible range crosses the
        antimeridian.
    """
    degree_step = 360.0 / target_area.width
    grid_lon_min = target_area.area_extent[0]

    lon_min_deg = sub_satellite_lon_deg - max_viewing_angle_deg
    lon_max_deg = sub_satellite_lon_deg + max_viewing_angle_deg

    col_start = int(np.floor((lon_min_deg - grid_lon_min) / degree_step))
    col_end = int(np.ceil((lon_max_deg - grid_lon_min) / degree_step))
    return np.mod(np.arange(col_start, col_end), target_area.width)


_NPROCS = 1
"""Reverted to 1 after profiling on 2026-08-01: nprocs>1 was tried at 8
(min(8, os.cpu_count())) and made things catastrophically worse on this
Windows machine, not better. MIR's reproject step went from 141.94s
(nprocs=1 baseline) to 2303.05s (nprocs=8, a 16x regression), and VIS's
larger arrays crashed outright with a worker-process `std::bad_alloc`.
Root cause: pyresample's multiprocessing uses Python's `multiprocessing`
module, which defaults to the `spawn` start method on Windows (unlike
`fork` on Linux) -- every spawned worker re-imports the ENTIRE calling
process from scratch (profiling output showed satpy being re-imported
1200+ times in one run, once per worker per get_neighbour_info call),
and each worker needs its own copy of the query data, multiplying memory
~nprocs-fold instead of parallelizing it. `nprocs` may be worth
revisiting on Linux/fork-based deployment, but not on Windows without a
much more careful (and probably not worthwhile, given the import-cost
multiplier) implementation."""

_neighbour_info_cache: dict[str, tuple] = {}


def _neighbour_info_cache_key(cache_key_extra: tuple, radius_of_influence_m: float) -> str:
    """Build the cache key for a neighbour-info lookup.

    Hash of everything that actually determines pyresample's neighbor-
    search result. Built entirely from `cache_key_extra` (the caller's
    job to include the source geometry's own identity plus the crop
    parameters) rather than introspecting a `source_area` object directly
    -- the source geometry passed to `_resample_nearest_cached` can now be
    either an AreaDefinition (crs/width/height/extent) or a SwathDefinition
    (no such attributes, see `crop_source_to_target_lonlat`'s own
    `_source_cache_identity` attr), so this function no longer assumes
    which.

    Not keyed on a separately-tracked satellite position, because
    reproject_to_cropped_grid's own module docstring already establishes
    that satpy builds each file's native AreaDefinition from its actual
    station-kept position, not a generic nominal one -- comparing GOES-18
    and Himawari-9 files shows all 4 Tier A band files for the same
    satellite/scan-cycle have bit-identical orbital_parameters (GOES's
    case: it never varies at all, satpy only ever exposes the fixed
    projection/nominal grid for ABI, not a live position; Himawari's
    case: satellite_actual_longitude/latitude/altitude are exactly
    identical across all 4 bands of one cycle, only the *unused*-by-this-
    project nadir_longitude/latitude jitter in the 4th-5th decimal). So
    caching by the source geometry's own identity (now passed in via
    cache_key_extra) correctly HITS for same-cycle same-satellite reuse
    (the case this exists for) and correctly MISSES for a genuinely
    different scan (Himawari's position drifts slightly cycle to cycle
    from station-keeping) -- never a false hit, because that identity
    itself would differ.

    Args:
        cache_key_extra: Tuple describing the source geometry's identity
            plus crop parameters (built by the caller).
        radius_of_influence_m: Nearest-neighbor search radius, in meters.

    Returns:
        A 24-character hex digest uniquely identifying this
        neighbour-search configuration.
    """
    import hashlib

    parts = (cache_key_extra, radius_of_influence_m)
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:24]


def _is_time_invariant_geometry(orbital_parameters: dict) -> bool:
    """Check whether a satellite's exposed geometry is permanently fixed (safe to disk-cache).

    True if this file's exposed orbital_parameters never varies run to
    run: GOES's abi_l1b reader only ever exposes the fixed
    projection/nominal grid (no satellite_actual_* key at all, any file,
    any time), so its neighbor-info is safe to persist to disk and reuse
    forever. Himawari/Meteosat, by contrast, DO expose a
    satellite_actual_longitude/latitude/altitude (identical across one
    scan cycle's bands, but expected to drift cycle to cycle from
    station-keeping) -- disk-persisting THEIR neighbor-info would almost
    never hit again after the run that wrote it, for a cost that reached
    multi-GB per satellite on Tier B's grid and caused a disk-space
    incident. Detected by the presence of any satellite_actual_* key --
    its absence is exactly what the permanently-reusable satellites
    have.

    Args:
        orbital_parameters: A loaded DataArray's
            ``.attrs["orbital_parameters"]`` dict from satpy.

    Returns:
        True if no key starts with ``"satellite_actual_"`` (geometry is
        permanently reusable, e.g. GOES/INSAT); False otherwise
        (station-kept position drifts run to run, e.g. Himawari/Meteosat).
    """
    return not any(k.startswith("satellite_actual_") for k in orbital_parameters)


def _resample_nearest_cached(
    source_geometry,
    values: np.ndarray,
    target_swath,
    radius_of_influence_m: float,
    cache_key_extra: tuple,
    persist_to_disk: bool = True,
) -> np.ndarray:
    """Nearest-neighbor resample with the KD-tree neighbour-search step cached.

    Same result as pyresample.kd_tree.resample_nearest, decomposed
    into its own two steps (reading resample_nearest's source: it's
    exactly get_neighbour_info() + get_sample_from_neighbour_info(),
    nothing more) so the expensive part -- the KD-tree neighbor search --
    can be cached and reused.

    `source_geometry` may be an AreaDefinition OR a SwathDefinition (see
    `crop_source_to_target_lonlat`, which now returns a SwathDefinition
    for the cropped source -- fixed 2026-08-03: AreaDefinition.__getitem__'s
    incremental area_extent recomputation introduces small but real
    floating-point precision drift, seen against Meteosat SEVIRI data,
    that occasionally flipped which of two near-tied candidate pixels got
    selected). The cache key is built entirely from `cache_key_extra` (see
    `_neighbour_info_cache_key`), not from introspecting this object's own
    attributes, specifically so it works for both geometry types.
    NOT parallelized via nprocs -- see _NPROCS's docstring for why that
    was tried and reverted (a measured regression on Windows).

    Profiling found reprojection is 28-41% of total pipeline time, and
    GOES/Himawari's 4 Tier A bands (MIR/WV/TIR1/TIR2) all share the same
    2km native resolution -- meaning the same satellite's neighbor-search
    result is exactly reusable across all 4 bands (see
    _neighbour_info_cache_key's docstring for why this is safe, not just
    fast). Always cached in-process (dict, cheap, covers same-run reuse
    regardless of satellite). `persist_to_disk=False` (see
    reproject_to_cropped_grid's use of _is_time_invariant_geometry) skips
    writing/reading the disk layer -- for satellites whose position
    drifts run to run (Himawari, Meteosat), a disk entry would almost
    never be hit again after the run that wrote it, for a
    multi-GB-per-satellite cost on Tier B's grid that isn't worth paying
    for a one-off. Satellites with time-invariant geometry (GOES, INSAT)
    still get the disk layer, since those entries genuinely are reusable
    forever.

    Args:
        source_geometry: The source's ``AreaDefinition`` or
            ``SwathDefinition``.
        values: Source data values to resample.
        target_swath: Target points to resample onto (a
            ``SwathDefinition``).
        radius_of_influence_m: Nearest-neighbor search radius, in meters.
        cache_key_extra: Tuple identifying this exact neighbour-search
            configuration (see :func:`_neighbour_info_cache_key`).
        persist_to_disk: Whether to also read/write the disk cache layer
            (only worthwhile for time-invariant geometries).

    Returns:
        The resampled array, shape `target_swath.shape`, with NaN where
        no source pixel was within `radius_of_influence_m`.
    """
    from pyresample.kd_tree import get_neighbour_info, get_sample_from_neighbour_info

    key = _neighbour_info_cache_key(cache_key_extra, radius_of_influence_m)
    if key in _neighbour_info_cache:
        valid_input_index, valid_output_index, index_array, distance_array = _neighbour_info_cache[key]
    else:
        disk_path = _CACHE_DIR / f"neighbour_info_{key}.npz"
        if persist_to_disk and disk_path.exists():
            with np.load(disk_path) as cached:
                valid_input_index = cached["valid_input_index"]
                valid_output_index = cached["valid_output_index"]
                index_array = cached["index_array"]
                distance_array = cached["distance_array"]
        else:
            valid_input_index, valid_output_index, index_array, distance_array = get_neighbour_info(
                source_geometry,
                target_swath,
                radius_of_influence_m,
                neighbours=1,
                nprocs=_NPROCS,
            )
            if persist_to_disk:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp_path = _CACHE_DIR / f"neighbour_info_{key}.tmp.npz"
                np.savez(
                    tmp_path,
                    valid_input_index=valid_input_index,
                    valid_output_index=valid_output_index,
                    index_array=index_array,
                    distance_array=distance_array,
                )
                tmp_path.replace(disk_path)
        _neighbour_info_cache[key] = (valid_input_index, valid_output_index, index_array, distance_array)

    return get_sample_from_neighbour_info(
        "nn",
        target_swath.shape,
        values,
        valid_input_index,
        valid_output_index,
        index_array,
        distance_array=distance_array,
        fill_value=np.nan,
    )


_CROP_CHUNK_SIZE = 2_000_000
"""How many target lon/lat points crop_source_to_target_lonlat inverse-
projects at once. Measured via granular ctypes profiling on one INSAT
VIS/Tier-B candidate: calling `get_array_indices_from_lonlat` on the
FULL ~35M-point column-cropped target grid in one shot cost a +1.38GB
transient by itself -- a new cost this function introduced, on top of
(not instead of) the KD-tree savings it was built for. Chunking this
loop is EXACT, not an approximation (same min/max accumulated across
chunks as computing it in one shot, just without ever holding all ~35M
points' intermediate float64/masked-array copies at once) -- unlike the
`get_area_slices` approach tried and reverted above, there is no
boundary-sampling error being traded in here."""

_EDGE_SNAP_FRACTION = 0.1
"""If crop_source_to_target_lonlat's computed crop boundary already lands
within this fraction of the source array's own true edge on a given side,
snap out to that true edge instead of trusting the margin-padded
boundary -- see that function's own docstring. Found 2026-08-04:
`get_array_indices_from_lonlat` becomes numerically imprecise for target
points close to a satellite's own horizon, by more than any reasonably-
sized fixed pixel margin can be expected to cover (against Meteosat-10
data, the true nearest pixel was 11 rows outside a 9-pixel-padded
boundary). 10% is a deliberately generous, cheap safety margin -- it only
costs extra rows/cols on whichever side is already close to the edge, and
is a no-op for the common case (a tile that doesn't approach any
satellite's own horizon at all)."""


def crop_source_to_target_lonlat(
    data: xr.DataArray, target_lons: np.ndarray, target_lats: np.ndarray, radius_of_influence_m: float
) -> xr.DataArray:
    """Crop a satellite's native source array down to what target points could possibly need.

    Crops `data`'s native source array (and its `.attrs["area"]`) down
    to just the native (row, col) bounding box that could possibly be the
    nearest-neighbor source for any of `target_lons`/`target_lats` (the
    EXACT lon/lat of the target pixels about to be resampled, already
    column-cropped by the caller -- not target_area's own boundary).

    Why this is needed: `visible_columns_for_satellite`/
    `reproject_to_cropped_grid` below only ever shrink the TARGET side of
    the resample. The KD-tree built by `get_neighbour_info` (inside
    `_resample_nearest_cached`) is built over the SOURCE array, so its
    cost -- the dominant, still-unfixed memory cost after tiling, a
    transient spike of ~3.4-8.2GB per satellite depending on native
    resolution -- scales with source size regardless of how small the
    target tile is. Cropping the source before the KD-tree is built is
    what actually lets tree-construction cost shrink with tile count, the
    way every other tiled quantity already does.

    Deliberately does NOT use `AreaDefinition.get_area_slices` (pyresample's
    own boundary-polygon-based reduce-before-resample helper, the same one
    satpy's `Scene.resample()` applies by default) -- tried first, but
    caught by this project's own equivalence test
    (test_source_side_cropping.py) undershooting the true needed source
    row range by ~10 rows for a synthetic full-width thin tile:
    `get_area_slices` traces `area_to_cover`'s boundary with only
    `max(shape)//100 + 1` vertices (pyresample's own
    `_get_area_boundary`), which is far too coarse for a target rectangle
    spanning the full 360deg longitude range at only a few degrees of
    latitude -- exactly tile-major processing's own shape. Silently
    dropping source pixels this way would have been precisely the kind of
    "correct-looking but wrong" bug this project has repeatedly caught
    only via equivalence tests, so this function instead does the EXACT,
    algebraic inverse of the actual resample: `get_array_indices_from_lonlat`
    on the actual target points (the same pyresample primitive
    `get_area_slices` itself calls internally for its cross-projection
    case, just applied directly to every target point instead of a coarse
    boundary sample), so there is no boundary-approximation error to
    begin with -- only a small, principled pixel margin for
    `radius_of_influence_m` (converted from meters via the source area's
    own `pixel_size_x`/`pixel_size_y`) plus a couple of pixels of
    rounding slack.

    Falls back to returning `data` unchanged if none of the target points
    land inside the source's own domain (no overlap) -- this is a pure
    compute/memory optimization, never a correctness requirement, since
    `reproject_to_cropped_grid`'s own resample_nearest call is correct
    either way.

    Processes `target_lons`/`target_lats` in `_CROP_CHUNK_SIZE`-sized
    chunks rather than one `get_array_indices_from_lonlat` call over the
    whole (possibly tens of millions of points) array -- see
    `_CROP_CHUNK_SIZE`'s own docstring for the cost this avoids.
    Accumulating a running min/max across chunks is exactly equivalent to
    computing it over the full array at once, not an approximation.

    Args:
        data: satpy-loaded DataArray with its native ``.attrs["area"]``.
        target_lons: Exact target longitudes about to be resampled.
        target_lats: Exact target latitudes about to be resampled, same
            shape as `target_lons`.
        radius_of_influence_m: Nearest-neighbor search radius, in
            meters, used to size the crop margin.

    Returns:
        `data` unchanged if none of the target points overlap the
        source's domain; otherwise a cropped copy of `data` with
        ``attrs["area"]`` replaced by a ``SwathDefinition`` for the
        cropped region and ``attrs["_source_cache_identity"]`` set to
        the original area's identity plus crop bounds.
    """
    from pyresample.geometry import SwathDefinition

    source_area = data.attrs["area"]
    flat_lons = target_lons.ravel()
    flat_lats = target_lats.ravel()

    col_min = col_max = row_min = row_max = None
    for start in range(0, flat_lons.size, _CROP_CHUNK_SIZE):
        end = start + _CROP_CHUNK_SIZE
        cols_chunk, rows_chunk = source_area.get_array_indices_from_lonlat(flat_lons[start:end], flat_lats[start:end])
        valid_chunk = ~(np.ma.getmaskarray(cols_chunk) | np.ma.getmaskarray(rows_chunk))
        if not np.any(valid_chunk):
            continue
        c = np.asarray(cols_chunk)[valid_chunk]
        r = np.asarray(rows_chunk)[valid_chunk]
        col_min = int(c.min()) if col_min is None else min(col_min, int(c.min()))
        col_max = int(c.max()) if col_max is None else max(col_max, int(c.max()))
        row_min = int(r.min()) if row_min is None else min(row_min, int(r.min()))
        row_max = int(r.max()) if row_max is None else max(row_max, int(r.max()))

    if col_min is None:
        return data

    margin_px = int(np.ceil(radius_of_influence_m / min(source_area.pixel_size_x, source_area.pixel_size_y))) + 2
    x0 = max(0, col_min - margin_px)
    x1 = min(source_area.width, col_max + margin_px + 1)
    y0 = max(0, row_min - margin_px)
    y1 = min(source_area.height, row_max + margin_px + 1)

    # Found 2026-08-04 via a Tier A old-vs-new output comparison:
    # `get_array_indices_from_lonlat` (used above to find
    # col_min/col_max/row_min/row_max) becomes numerically imprecise for
    # target points very close to a satellite's OWN horizon -- the geos
    # forward/inverse projection's Jacobian is near-singular there, so a
    # small input perturbation can produce a meaningfully-wrong native
    # index, even though the point is genuinely visible (not masked).
    # Against Meteosat-10 data: for target points at the project's own
    # -66.5deg grid edge (near this satellite's horizon from its 0deg E
    # position), the true nearest native pixel was 11 rows outside the
    # margin_px-padded box -- `radius_of_influence_m`-based margin alone
    # assumes the index computation itself is accurate to within a few
    # pixels, which this specific failure mode violates. Rather than
    # guess a larger fixed margin (risking the same failure mode at a
    # different, untested scale), snap the crop out to the source array's
    # OWN true edge on any side where the computed boundary already lands
    # within `_EDGE_SNAP_FRACTION` of that edge -- cheap (only costs
    # extra rows/cols specifically on the affected side), robust
    # regardless of how imprecise the near-horizon index computation gets
    # (the true answer can never be past the array's own boundary), and
    # inert for the common case where a tile doesn't approach any
    # satellite's horizon at all (crop size unaffected).
    if x0 < _EDGE_SNAP_FRACTION * source_area.width:
        x0 = 0
    if source_area.width - x1 < _EDGE_SNAP_FRACTION * source_area.width:
        x1 = source_area.width
    if y0 < _EDGE_SNAP_FRACTION * source_area.height:
        y0 = 0
    if source_area.height - y1 < _EDGE_SNAP_FRACTION * source_area.height:
        y1 = source_area.height

    x_slice = slice(x0, x1)
    y_slice = slice(y0, y1)
    cropped = data.isel(y=y_slice, x=x_slice)

    # Build the cropped source geometry via get_lonlats(data_slice=...)
    # directly on the ORIGINAL (unsliced) source_area, NOT via
    # AreaDefinition.__getitem__ (`source_area[y_slice, x_slice]`, the
    # first version of this function) -- fixed 2026-08-03, caught only by
    # an old-vs-new full-pipeline output comparison (a synthetic/unit-
    # test-scale check would not have exercised a satellite with this
    # specific geometry): __getitem__'s incremental area_extent
    # recomputation introduced a small but real (against Meteosat SEVIRI
    # data: ~0.0096deg longitude, ~0.0008deg latitude -- roughly
    # 1km/90m) floating-point precision drift relative to computing
    # lon/lat directly for the same native indices. Invisible almost
    # everywhere, but near near-tied nearest-neighbor decisions (common
    # wherever pixel footprints are heavily distorted, e.g. high-viewing-
    # angle regions) that drift was occasionally enough to flip which of
    # two candidate source pixels got selected -- exact resample_nearest
    # equivalence testing against the uncropped reference showed ~0.05%
    # of one tile's pixels differed, with individual value errors up to
    # ~23K. get_lonlats(data_slice=...) is bit-identical to computing
    # lon/lat for the WHOLE source array and then slicing, WITHOUT
    # materializing the whole array -- pyresample's own
    # `_get_proj_vectors` slices its 1D coordinate vectors before the
    # meshgrid+CRS-transform step, so this has the same memory benefit
    # the crop was built for.
    crop_lons, crop_lats = source_area.get_lonlats(data_slice=(y_slice, x_slice))
    cropped.attrs = dict(data.attrs)
    cropped.attrs["area"] = SwathDefinition(lons=crop_lons, lats=crop_lats)
    # A SwathDefinition has no crs/area_extent of its own to build a
    # stable neighbour-info cache key from the way an AreaDefinition does
    # -- stash the ORIGINAL source area's identity plus the crop bounds
    # so reproject_to_cropped_grid can build a correct, collision-free
    # key (see _neighbour_info_cache_key).
    cropped.attrs["_source_cache_identity"] = (
        str(source_area.crs),
        source_area.width,
        source_area.height,
        tuple(round(v, 3) for v in source_area.area_extent),
        y0,
        y1,
        x0,
        x1,
    )
    return cropped


def reproject_to_cropped_grid(
    data: xr.DataArray,
    target_area: AreaDefinition,
    sub_satellite_lon_deg: float,
    max_viewing_angle_deg: float,
    radius_of_influence_m: float = 20_000,
) -> xr.DataArray:
    """Reproject a satellite's native data onto only its own visible footprint.

    Like reproject_to_common_grid, but only resamples onto the
    satellite's own visible footprint (see visible_columns_for_satellite)
    instead of the full global grid -- avoids computing (and allocating
    memory for) guaranteed-NaN pixels outside a satellite's own disk. A
    GEO satellite's visible footprint is a bit under half the full grid's
    longitude width (~45% at the +-81.3deg equatorial half-width this
    project's satellites use), so this is a substantial reduction in both
    compute and memory for the reprojection + downstream resolution.py
    geometry steps, not just a constant-factor tweak.

    Returns a DataArray with `attrs["crop_columns"]` set to the target
    grid's column indices this candidate covers (see
    visible_columns_for_satellite's antimeridian-wraparound note) --
    downstream candidate-selection code needs this to know where on the
    full grid to place/compare this candidate's cropped data.

    Args:
        data: satpy-loaded DataArray with its native ``.attrs["area"]``.
        target_area: The common target grid's ``AreaDefinition``.
        sub_satellite_lon_deg: The satellite's sub-satellite longitude,
            in degrees.
        max_viewing_angle_deg: The satellite's maximum viewing angle,
            in degrees.
        radius_of_influence_m: Nearest-neighbor search radius, in meters.

    Returns:
        A new DataArray covering only this satellite's visible column
        range, with ``lat``/``lon`` coords and ``attrs["crop_columns"]``
        set to the covered target-grid column indices.

    Raises:
        ValueError: If `data.attrs` has no ``"area"`` key.
    """
    from pyresample.geometry import SwathDefinition

    if "area" not in data.attrs:
        raise ValueError(
            "reproject_to_cropped_grid needs data.attrs['area'] (the "
            "satellite's native AreaDefinition) -- load `data` via satpy "
            "so its reader can compute this, don't pass a bare array."
        )

    cols = visible_columns_for_satellite(target_area, sub_satellite_lon_deg, max_viewing_angle_deg)
    full_lons, full_lats = _cached_lonlats(target_area)
    cropped_lons = full_lons[:, cols]
    cropped_lats = full_lats[:, cols]
    cropped_target = SwathDefinition(lons=cropped_lons, lats=cropped_lats)

    # Crop the SOURCE array down to just the (row, col) box that could
    # possibly be the nearest-neighbor source for these exact target
    # points, computed AFTER the target-side column crop above (fewer,
    # more relevant points to check, see crop_source_to_target_lonlat's
    # docstring for why this uses the exact points rather than
    # target_area's own boundary) -- this is what actually shrinks the
    # KD-tree construction cost below, which the target-side column crop
    # above does not touch.
    data = crop_source_to_target_lonlat(data, cropped_lons, cropped_lats, radius_of_influence_m)
    source_area = data.attrs["area"]
    # crop_source_to_target_lonlat returns a SwathDefinition (no crs/
    # area_extent of its own) plus a `_source_cache_identity` attr built
    # from the ORIGINAL AreaDefinition + crop bounds -- unless it found no
    # overlap and returned `data` unchanged, in which case `source_area`
    # is still the original (uncropped) AreaDefinition and its own crs/
    # width/height/extent are used directly, matching this cache key's
    # pre-crop-SwathDefinition behavior.
    if "_source_cache_identity" in data.attrs:
        source_identity = data.attrs.pop("_source_cache_identity")
    else:
        source_identity = (
            str(source_area.crs),
            source_area.width,
            source_area.height,
            tuple(round(v, 3) for v in source_area.area_extent),
        )

    resampled = _resample_nearest_cached(
        source_area,
        data.values,
        cropped_target,
        radius_of_influence_m,
        # target_area.crop_offset included alongside width/height for the
        # same reason as _cached_lonlats' key (see that function's
        # docstring): under tile-major processing, many tiles share the
        # same (width, height) but cover different rows -- without
        # crop_offset, two different tiles' neighbour-info would
        # collide, silently reprojecting one tile's data using another
        # tile's KD-tree result.
        cache_key_extra=(
            source_identity,
            sub_satellite_lon_deg,
            max_viewing_angle_deg,
            target_area.width,
            target_area.height,
            target_area.crop_offset,
        ),
        persist_to_disk=_is_time_invariant_geometry(data.attrs.get("orbital_parameters", {})),
    )

    return xr.DataArray(
        resampled,
        dims=("y", "x"),
        coords={"lat": (("y", "x"), cropped_lats), "lon": (("y", "x"), cropped_lons)},
        attrs={**{k: v for k, v in data.attrs.items() if k != "area"}, "crop_columns": cols},
    )
