"""Writes a single mosaic timestamp to a CF-compliant, compressed NetCDF
file, per this project's original storage requirements:

  - the selected mosaic value itself
  - a 2D satellite_id mask (integer per pixel, keying which satellite that
    pixel's value came from -- "accountability of where the value of each
    pixel came from")
  - a 2D pixel_resolution_km mask (the ACTUAL per-pixel resolution from
    processing/resolution.py, not each satellite's nominal figure)
  - per-satellite-id metadata stored in the same file as a small
    `satellite`-dimensioned variable set (Satellite Longitude, Scanning
    Start/End Time, Spatial/Band/Temporal Resolution, source File_Name --
    the exact field list originally required)

Compression/storage: float fields are packed to int16 with a
scale_factor/add_offset (CF packed-data convention) rather than stored as
float32/64, plus zlib compression on every variable, per the original
"think about data compression ... reduce the use of float over int ...
highest level of data compression ... without affecting easier use of
read operation" instructions. Compression level 4 is a reasonable
default -- higher levels buy diminishing space savings at increasing CPU
cost for a format meant to be re-opened often (not archived once and never
read again), which is the "without affecting easier use of read
operation" trade-off those instructions call out explicitly.

Measured on 2026-08-04 against Tier A/B output files, level 4 vs level 9
-- same packed int16 bytes either way, so this is a lossless
recompression test, no precision/accountability impact: level 9 saves
only ~7.5% combined file size (Tier A -5.9%, Tier B -8.9%) for roughly
DOUBLE the write time (Tier A 111.3s->224.8s, +102%; Tier B
229.4s->364.2s, +59%). Level 4 stays the default.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import netCDF4
import numpy as np
import xarray as xr

_FILL_INT16 = np.iinfo(np.int16).min  # -32768, reserved: never a valid packed value
_FILL_INT8 = -1
"""Explicit fill value for the tiled writer's categorical int8 fields
(satellite_id, is_blended, runner_up_satellite_id, is_daylit) -- see
TiledBandWriter's module note for why an unwritten-tile fill value is
needed at all. Deliberately -1, NOT 0: 0 is itself an extremely common
value for every one of these fields (0 = no coverage / not blended / no
runner-up), so using it as the fill value too would make xarray/netCDF4's
default read-side auto-masking (mask_and_scale=True) silently convert
every LEGITIMATE zero into NaN on read -- a round-trip test caught this:
is_blended/runner_up_satellite_id/satellite_id all came back corrupted
this way with _FillValue=0. -1 is out of every one of these fields'
0..127 value range, so it can never collide with genuine data -- and
given the tiled writer's driver always writes every tile in full (no
tile is ever skipped), this fill value should never actually appear in
an output file; it exists purely as a defensive signal if that invariant
is ever violated by a bug, not as a value any correct run relies on."""
_COMPRESSION_LEVEL = 4


def _median_datetime(times: list[dt.datetime]) -> dt.datetime:
    """Median of a list of datetimes.

    Plain `statistics.median` doesn't work here: for an even-length list
    it averages the two middle values via `(a + b) / 2`, but
    `datetime + datetime` isn't a valid operation (only
    `datetime + timedelta` is) -- so it would raise TypeError. Handles
    both parities explicitly instead.

    Args:
        times: Datetimes to take the median of (order doesn't matter,
            sorted internally).

    Returns:
        The median datetime -- the middle element for an odd-length
        list, or the midpoint between the two middle elements for an
        even-length list.
    """
    ordered = sorted(times)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return ordered[mid - 1] + (ordered[mid] - ordered[mid - 1]) / 2


@dataclass
class SatelliteMeta:
    """Everything this project's original requirements ask to
    be stored per satellite_id, so any pixel's value is traceable back to
    exactly which file/satellite produced it and under what conditions.

    Attributes:
        satellite_key: Short satellite identifier (e.g. "meteosat-10"),
            used as the value in the `satellite_key` NetCDF variable and
            in `flag_meanings`.
        satellite_id: Integer id used in the `satellite_id` pixel mask
            and the `satellite` dimension coordinate.
        longitude_deg: Satellite's orbital longitude.
        scan_start: Start time of the scan that produced this satellite's
            contributing data.
        scan_end: End time of that scan.
        scan_mid: Scan mid-time. Should come straight from
            processing/midtime.py's ScanTimes.mid (already computed
            there -- the same value select.Candidate.mid_time was built
            from), not recomputed here as (scan_start + scan_end)/2, to
            keep one single source of truth rather than two independent
            computations that could theoretically drift apart later.
        nominal_spatial_resolution_km: Satellite's nominal nadir spatial
            resolution (see pixel_resolution_km for the actual per-pixel
            value).
        band_center_wavelength_um: Band center wavelength used for this
            mosaic.
        temporal_resolution_minutes: Full-disk repeat cadence.
        source_filename: Name of the source file this satellite's data
            was read from.
        srf_correction_gain: Multiplicative SRF/calibration correction
            already applied to this satellite's value before selection/
            blending (see satellites/srf_correction.py) -- stored here
            purely for disclosure/accountability, matching this file's
            existing "record what happened to the data, don't just apply
            it silently" pattern (see is_blended/blend_weight_winner
            above). Default 1.0 means no correction was applied.
        srf_correction_offset: Additive SRF/calibration correction
            counterpart to `srf_correction_gain`. Default 0.0 means no
            correction was applied.
        srf_correction_method: How gain/offset were derived. Default
            "identity" means no correction was applied.
    """

    satellite_key: str
    satellite_id: int
    longitude_deg: float
    scan_start: dt.datetime
    scan_end: dt.datetime
    # Should come straight from processing/midtime.py's ScanTimes.mid
    # (already computed there -- the same value select.Candidate.mid_time
    # was built from), not recomputed here as (scan_start + scan_end)/2,
    # to keep one single source of truth rather than two independent
    # computations that could theoretically drift apart later.
    scan_mid: dt.datetime
    nominal_spatial_resolution_km: float
    band_center_wavelength_um: float
    temporal_resolution_minutes: float
    source_filename: str
    srf_correction_gain: float = 1.0
    srf_correction_offset: float = 0.0
    srf_correction_method: str = "identity"
    """SRF/calibration correction already applied to this satellite's value
    before selection/blending (see satellites/srf_correction.py) -- stored here purely for
    disclosure/accountability, matching this file's existing "record what
    happened to the data, don't just apply it silently" pattern (see
    is_blended/blend_weight_winner above). 1.0/0.0/"identity" (the
    dataclass defaults) mean no correction was applied."""


def _compute_scale_offset(data: xr.DataArray) -> tuple[float, float]:
    """Computes CF packed-data scale_factor/add_offset for `data`.

    CF packed-data convention: unpacked = add_offset + scale_factor * packed.
    Scales the array's actual finite range across the usable int16 range
    (reserving _FILL_INT16 for NaN/missing), rather than a
    one-size-fits-all fixed scale -- works for any band's physical units
    (radiance, brightness temperature, km, ...) without per-field tuning.

    Only computes the two numbers -- the actual int16 packing (and NaN ->
    _FillValue substitution) is done by xarray/netCDF4 itself at write
    time from these plus the `dtype`/`_FillValue` entries in `encoding`
    (see write_mosaic_netcdf). Packing it here *and* handing xarray a
    scale_factor/add_offset encoding would double-pack the data -- keep
    the values handed to the Dataset as plain floats with NaN for missing.

    Args:
        data: Full-grid array whose finite values' range determines the
            packing.

    Returns:
        Tuple of (scale_factor, add_offset). Falls back to (1.0, 0.0) if
        `data` has no finite values at all (e.g. zero overlap this run --
        arbitrary but harmless since there's nothing to pack anyway).
    """
    values = data.values
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 1.0, 0.0  # nothing valid this run (e.g. zero overlap) -- arbitrary but harmless

    # Packed values must land in [int16_min+1, int16_max] (-32767..32767,
    # reserving -32768 for the fill value) -- NOT [0, 65534], which
    # silently overflows/wraps a signed int16 and corrupts the data.
    # Centering add_offset at the midpoint spans that symmetric range
    # exactly: packed(vmin) = -32767, packed(vmax) = +32767. See
    # _scale_offset_from_range for the shared math.
    return _scale_offset_from_range(float(finite.min()), float(finite.max()))


def _packed_encoding() -> dict:
    """Base xarray `encoding` dict for a zlib-compressed, int16-packed variable."""
    return {"zlib": True, "complevel": _COMPRESSION_LEVEL, "dtype": "int16", "_FillValue": _FILL_INT16}


def _scale_offset_from_range(vmin: float, vmax: float) -> tuple[float, float]:
    """Computes CF packed-data scale_factor/add_offset from an explicit range.

    Pure version of _compute_scale_offset's math, taking the range
    directly instead of an xr.DataArray -- used by the tiled writer
    (see open_tiled_band_writer/finalize_tiled_band_writer), which tracks
    a running (vmin, vmax) across tiles as it goes rather than ever
    holding the full array to call .values.min()/.max() on.

    Args:
        vmin: Minimum finite value to be represented.
        vmax: Maximum finite value to be represented.

    Returns:
        Tuple of (scale_factor, add_offset) such that packing vmin/vmax
        lands exactly at -32767/+32767 (see callers for the reasoning on
        why the symmetric range is used).
    """
    usable_half_range = np.iinfo(np.int16).max  # 32767
    scale_factor = 1.0 if vmax == vmin else (vmax - vmin) / (2 * usable_half_range)
    add_offset = (vmin + vmax) / 2.0
    return scale_factor, add_offset


def _srf_correction_attrs(metas: list[SatelliteMeta]) -> dict:
    """Builds global/tier-level SRF-correction disclosure attrs.

    Global/tier-level disclosure attrs for whether SRF correction (see
    satellites/srf_correction.py) was actually applied to any contributing
    satellite in this file. Per-satellite method/date/
    source-version live on the satellite_srf_correction_* variables instead
    of duplicated here, since different satellites in the same file can
    legitimately be at different rollout stages.

    Args:
        metas: Per-satellite metadata for every satellite contributing to
            this file.

    Returns:
        Dict of dataset-level attrs (`srf_correction_enabled`,
        `srf_correction_schema_version`, `srf_correction_reference`) to
        merge into the Dataset's `attrs`.
    """
    return {
        "srf_correction_enabled": "true" if any(m.srf_correction_method != "identity" for m in metas) else "false",
        "srf_correction_schema_version": "1",
        "srf_correction_reference": "satellites/srf_correction.py",
    }


def compute_blend_accountability_fields(
    value_shape: tuple[int, ...],
    resolution_km: np.ndarray,
    tie_mask: np.ndarray | None,
    runner_up_satellite_id: np.ndarray | None,
    runner_up_resolution_km: np.ndarray | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Computes the is_blended/blend_weight_winner/runner_up_id fields.

    Extracted from build_mosaic_dataset's inline logic so it can run
    per-tile (see mosaic_io's tiled writer) as well as on a full grid
    (build_mosaic_dataset itself, unchanged below) -- same computation,
    same contracts, just callable at either granularity.

    Args:
        value_shape: Shape of the grid (or tile) being processed; every
            returned array has this shape.
        resolution_km: Winning satellite's per-pixel actual resolution,
            used (with `runner_up_resolution_km`) to compute the inverse-
            resolution blend weight.
        tie_mask: Boolean mask of pixels that were actually blended
            (feathered) rather than a hard cut, or None if this
            band/tile has no blending information at all (treated as
            "nothing blended").
        runner_up_satellite_id: Second-place satellite id per pixel
            (populated wherever a pixel had >=2 valid candidates,
            regardless of whether they were close enough to blend), or
            None.
        runner_up_resolution_km: Runner-up satellite's per-pixel actual
            resolution, or None.

    Returns:
        Tuple of (is_blended, blend_weight_winner, runner_up_id_arr),
        each shaped `value_shape`, dtypes int8/float64/int8 matching what
        build_mosaic_dataset has always written. `runner_up_id_arr` is
        masked down to 0 everywhere `is_blended` is false, per that
        field's documented contract.
    """
    is_blended = tie_mask if tie_mask is not None else np.zeros(value_shape, dtype=bool)

    blend_weight_winner = np.ones(value_shape, dtype=np.float64)
    if tie_mask is not None and runner_up_resolution_km is not None:
        winner_weight = 1.0 / resolution_km
        runner_weight = 1.0 / runner_up_resolution_km
        computed = winner_weight / (winner_weight + runner_weight)
        blend_weight_winner = np.where(is_blended, computed, 1.0)

    # select.SelectionResult.runner_up_satellite_id is populated for every
    # pixel with >=2 valid candidates, regardless of whether they were
    # actually close enough to blend (see that field's own docstring) --
    # mask it down to "only where a blend actually happened" here, to
    # match this variable's documented contract (0 elsewhere).
    if runner_up_satellite_id is not None:
        runner_up_id_arr = np.where(is_blended, runner_up_satellite_id, 0).astype(np.int8)
    else:
        runner_up_id_arr = np.zeros(value_shape, dtype=np.int8)

    return is_blended.astype(np.int8), blend_weight_winner, runner_up_id_arr


def build_mosaic_dataset(
    value: xr.DataArray,
    satellite_id: xr.DataArray,
    resolution_km: xr.DataArray,
    metas: list[SatelliteMeta],
    target_time: dt.datetime,
    band_name: str,
    value_long_name: str = "selected mosaic value",
    value_units: str = "unknown",
    tie_mask: xr.DataArray | None = None,
    runner_up_satellite_id: xr.DataArray | None = None,
    runner_up_resolution_km: xr.DataArray | None = None,
) -> xr.Dataset:
    """Assembles the CF-compliant in-memory Dataset (packing not applied
    yet here -- see write_mosaic_netcdf for the encoding that actually
    triggers int16 packing + compression on write).

    Pass `tie_mask`/`runner_up_satellite_id`/`runner_up_resolution_km`
    (all straight from select.SelectionResult) whenever `value` was run
    through processing/blend.py's feather_seams -- otherwise
    `satellite_id` alone would silently misreport a blended pixel as a
    pure single-satellite observation, breaking
    the original accountability
    requirement ("this storing can make it easy for accountability of
    where the value of each pixel came from"). Fixed 2026-07-31: blending
    was silently losing traceability of the true per-satellite
    observations until this.

    Args:
        value: Selected/blended mosaic value for this band, on the
            common grid.
        satellite_id: Primary (highest-weighted) source satellite id per
            pixel (0 = no coverage).
        resolution_km: Actual per-pixel ground resolution of the winning
            satellite (see processing/resolution.py).
        metas: Per-satellite metadata for every satellite contributing to
            this band, used both for the `satellite`-dimensioned
            metadata variables and for `satellite_id`'s flag_values/
            flag_meanings attrs.
        target_time: The originally requested mosaic timestamp, recorded
            as the `requested_target_time` attribute (distinct from the
            dataset's authoritative `time` coordinate, which is the
            actual median scan mid-time).
        band_name: Band name used in the dataset `title` attribute.
        value_long_name: `long_name` attribute for the `value` variable.
        value_units: `units` attribute for the `value` variable.
        tie_mask: Boolean mask of pixels actually blended, or None if
            `value` was never run through feather_seams.
        runner_up_satellite_id: Second-place satellite id per pixel, or
            None.
        runner_up_resolution_km: Runner-up satellite's per-pixel actual
            resolution, or None.

    Returns:
        An in-memory, CF-1.8-attributed `xr.Dataset` with the `value`,
        `satellite_id`, `is_blended`, `runner_up_satellite_id`,
        `blend_weight_winner`, `pixel_resolution_km`, per-satellite
        metadata variables, and `crs` grid-mapping variable -- ready to
        be handed to write_mosaic_netcdf for packed/compressed writing.
    """
    lat = value.coords["lat"]
    lon = value.coords["lon"]

    # See compute_blend_accountability_fields for the shared logic (also
    # used by the tiled writer, one tile at a time) -- this call is the
    # full-grid case. A prior version of this function forgot to mask
    # runner_up_satellite_id down to "only where a blend actually
    # happened," silently breaking that field's documented contract for
    # any pixel with a second candidate that wasn't tied -- caught by
    # tests/test_saved_mosaic_file.py against a real written file; still
    # covered by that mask inside the shared helper now.
    is_blended, blend_weight_winner, runner_up_id_arr = compute_blend_accountability_fields(
        value.shape,
        resolution_km.values,
        tie_mask.values if tie_mask is not None else None,
        runner_up_satellite_id.values if runner_up_satellite_id is not None else None,
        runner_up_resolution_km.values if runner_up_resolution_km is not None else None,
    )

    ds = xr.Dataset(
        data_vars={
            "value": (
                value.dims,
                value.values,
                {
                    "long_name": value_long_name,
                    "units": value_units,
                    "grid_mapping": "crs",
                    "coordinates": "lat lon",
                },
            ),
            "satellite_id": (
                satellite_id.dims,
                satellite_id.values.astype(np.int8),
                {
                    "long_name": "PRIMARY (highest-weighted) source satellite for this pixel "
                    "(0 = no coverage). Where is_blended is true, this pixel's value is a "
                    "weighted mix of this satellite and runner_up_satellite_id -- see "
                    "blend_weight_winner for the exact split, do not treat it as a pure "
                    "single-satellite observation.",
                    "flag_values": np.array([0] + [m.satellite_id for m in metas], dtype=np.int8),
                    "flag_meanings": "no_data " + " ".join(m.satellite_key.replace("-", "_") for m in metas),
                    "grid_mapping": "crs",
                    "coordinates": "lat lon",
                },
            ),
            "is_blended": (
                satellite_id.dims,
                is_blended,
                {
                    "long_name": "1 where processing/blend.py feathered this pixel between "
                    "satellite_id and runner_up_satellite_id instead of a hard cut, 0 where "
                    "value is a pure single-satellite observation",
                    "grid_mapping": "crs",
                    "coordinates": "lat lon",
                },
            ),
            "runner_up_satellite_id": (
                satellite_id.dims,
                runner_up_id_arr,
                {
                    "long_name": "second satellite contributing to this pixel's value where "
                    "is_blended=1 (0 elsewhere -- including pixels with only one candidate "
                    "at all, which can never be blended)",
                    "grid_mapping": "crs",
                    "coordinates": "lat lon",
                },
            ),
            "blend_weight_winner": (
                satellite_id.dims,
                blend_weight_winner,
                {
                    "long_name": "fraction of this pixel's value contributed by satellite_id "
                    "(vs. 1-this from runner_up_satellite_id); exactly 1.0 wherever "
                    "is_blended=0 -- lets the original per-satellite values be reconstructed "
                    "exactly: value = w*winner + (1-w)*runner_up",
                    "grid_mapping": "crs",
                    "coordinates": "lat lon",
                },
            ),
            "pixel_resolution_km": (
                resolution_km.dims,
                resolution_km.values,
                {
                    "long_name": "actual ground resolution at this pixel, accounting for "
                    "view-zenith-angle degradation (see processing/resolution.py)",
                    "units": "km",
                    "grid_mapping": "crs",
                    "coordinates": "lat lon",
                },
            ),
        },
        coords={
            # processing/reproject.py's common_grid() is a rectilinear
            # equirectangular grid: lat varies only by row (y), lon only by
            # column (x) -- checked against written files (np.allclose
            # across every row/column). Storing them as full 2D arrays
            # duplicates that same 1D pattern y (or x) times for no reason;
            # collapsing to genuine 1D CF coordinates loses no information
            # (a consumer gets the exact same values back via
            # lat_1d[:, None] / lon_1d[None, :] broadcasting) and cuts
            # measured storage -- lon alone was ~4.3-4.7% of a Tier B file
            # just from repeating the same 40075-value row 14806 times.
            "lat": (("y",), lat.values[:, 0], {"standard_name": "latitude", "units": "degrees_north"}),
            "lon": (("x",), lon.values[0, :], {"standard_name": "longitude", "units": "degrees_east"}),
            "satellite": ("satellite", [m.satellite_id for m in metas]),
            "time": (
                (),
                np.datetime64(_median_datetime([m.scan_mid for m in metas])),
                {
                    "standard_name": "time",
                    "long_name": "median scan mid-time across contributing satellites -- the "
                    "mosaic's authoritative timestamp (not the requested target_time attribute, "
                    "which is what was asked for; this is what the data actually reflects). "
                    "Chosen over the mean specifically for robustness to one oddly-timed "
                    "satellite (e.g. INSAT's ~30 min cadence vs. the ~10-15 min of the others) "
                    "skewing the average. A scalar (not a length-1 dimension) since Phase 0 is "
                    "one timestamp per file -- concatenating multiple files along this "
                    "coordinate later (Phase 3's continuous archive) promotes it to a real "
                    "dimension automatically (e.g. via xr.open_mfdataset(..., concat_dim='time')).",
                },
            ),
        },
        attrs={
            "Conventions": "CF-1.8",
            "title": f"Global geostationary mosaic -- {band_name}",
            "history": f"Generated {dt.datetime.now(dt.timezone.utc).isoformat()} by global_mosaic.processing.mosaic_io",
            "requested_target_time": target_time.isoformat(),
            "references": "See global_mosaic.satellites.sat_metadata for per-satellite technical specifications.",
            **_srf_correction_attrs(metas),
        },
    )

    # Per-satellite-id metadata table -- the exact field list
    # originally required, keyed by
    # the same integer id used in the satellite_id mask above.
    ds["satellite_longitude"] = ("satellite", [m.longitude_deg for m in metas], {"units": "degrees_east"})
    ds["satellite_scan_start"] = ("satellite", np.array([m.scan_start for m in metas], dtype="datetime64[ns]"))
    ds["satellite_scan_end"] = ("satellite", np.array([m.scan_end for m in metas], dtype="datetime64[ns]"))
    ds["satellite_scan_mid"] = (
        "satellite",
        np.array([m.scan_mid for m in metas], dtype="datetime64[ns]"),
        {
            "long_name": "scan mid-time -- (scan_start + scan_end) / 2, used by select.py to compare "
            "each satellite's real synchronization to the target time. Stored explicitly rather than "
            "requiring a consumer to recompute it from scan_start/scan_end.",
        },
    )
    ds["satellite_spatial_resolution_km"] = (
        "satellite",
        [m.nominal_spatial_resolution_km for m in metas],
        {"long_name": "nominal nadir spatial resolution (see pixel_resolution_km for the actual per-pixel value)"},
    )
    ds["satellite_band_wavelength_um"] = (
        "satellite",
        [m.band_center_wavelength_um for m in metas],
        {"units": "micrometer", "long_name": "band center wavelength used for this mosaic"},
    )
    ds["satellite_temporal_resolution_minutes"] = (
        "satellite",
        [m.temporal_resolution_minutes for m in metas],
        {"long_name": "full-disk repeat cadence"},
    )
    ds["satellite_source_filename"] = ("satellite", [m.source_filename for m in metas])
    ds["satellite_key"] = ("satellite", [m.satellite_key for m in metas])
    ds["satellite_srf_correction_gain"] = (
        "satellite",
        [m.srf_correction_gain for m in metas],
        {
            "long_name": "multiplicative SRF/calibration correction applied to this satellite's raw "
            "value before selection/blending for this band; 1.0 = no correction (identity). "
            "value_corrected = value_raw * gain + offset. See satellite_srf_correction_method and "
            "satellites/srf_correction.py.",
        },
    )
    ds["satellite_srf_correction_offset"] = (
        "satellite",
        [m.srf_correction_offset for m in metas],
        {
            "long_name": "additive SRF/calibration correction applied to this satellite's raw value "
            "before selection/blending for this band, in the same physical units as value; "
            "0.0 = no correction (identity).",
        },
    )
    ds["satellite_srf_correction_method"] = (
        "satellite",
        [m.srf_correction_method for m in metas],
        {
            "long_name": "how gain/offset were derived: 'identity' (no correction), "
            "'srf-weighted-radiative-transfer', or 'empirical-overlap-bias'. See "
            "satellites/srf_correction.py.",
        },
    )

    # CF grid_mapping variable for the plate carree common grid (see
    # processing/reproject.py's common_grid()).
    ds["crs"] = ((), 0, {"grid_mapping_name": "latitude_longitude", "longitude_of_prime_meridian": 0.0})

    return ds


def write_mosaic_netcdf(path: str, ds: xr.Dataset) -> None:
    """Writes `ds` to `path`, packing every float data variable to int16
    (scale_factor/add_offset) and zlib-compressing every variable, per
    this project's original storage requirements. satellite_id is already int8
    on the way in and gets compressed but not further packed (it's
    already the smallest lossless representation for <128 satellite ids).

    Args:
        path: Destination NetCDF file path.
        ds: Dataset built by build_mosaic_dataset (or with the same
            variable set/dtypes).

    Returns:
        None. Writes the file at `path` as a side effect.
    """
    encoding: dict[str, dict] = {}

    for name in ("value", "pixel_resolution_km"):
        scale_factor, add_offset = _compute_scale_offset(ds[name])
        encoding[name] = {
            **_packed_encoding(),
            "scale_factor": scale_factor,
            "add_offset": add_offset,
        }

    # blend_weight_winner is exactly bounded [0,1] -- a fixed scale (not
    # the dynamic min/max packer) covers the full range at ~3e-5
    # precision, plenty for a blend fraction.
    if "blend_weight_winner" in ds:
        encoding["blend_weight_winner"] = {
            **_packed_encoding(),
            "scale_factor": 1.0 / 32767,
            "add_offset": 0.5,
        }

    encoding["satellite_id"] = {"zlib": True, "complevel": _COMPRESSION_LEVEL}
    encoding["lat"] = {"zlib": True, "complevel": _COMPRESSION_LEVEL}
    encoding["lon"] = {"zlib": True, "complevel": _COMPRESSION_LEVEL}
    for name in ("is_blended", "runner_up_satellite_id"):
        if name in ds:
            encoding[name] = {"zlib": True, "complevel": _COMPRESSION_LEVEL}
    for name in (
        "satellite_longitude",
        "satellite_spatial_resolution_km",
        "satellite_band_wavelength_um",
        "satellite_temporal_resolution_minutes",
        "satellite_srf_correction_gain",
        "satellite_srf_correction_offset",
    ):
        encoding[name] = {"zlib": True, "complevel": _COMPRESSION_LEVEL}

    # "satellite" (the id coordinate) and satellite_temporal_resolution_minutes
    # are built from plain Python ints in build_mosaic_dataset, which
    # xarray/netCDF4 default to int64 ("long") on write -- DAP2/OPeNDAP
    # can't serve that type ("NcDDS Variable data type = long", seen
    # 2026-08-07). Same fix as _append_satellite_metadata_table's
    # tiled-writer counterpart: force int32, which loses nothing since
    # both are always tiny values.
    encoding["satellite"] = {"dtype": "int32"}
    encoding["satellite_temporal_resolution_minutes"]["dtype"] = "int32"
    encoding["crs"] = {"dtype": "int32"}

    ds.to_netcdf(path, engine="netcdf4", encoding=encoding)


_MERGE_CHUNK_ROWS = 2000
"""Row-chunk size for merge_tiled_band_files's variable-by-variable copy.
Not tied to compute_row_tiles/memory_budget_gb (that sizing model is
built around SelectionAccumulator's per-row cost, irrelevant here) -- the
data being copied is already-packed int8/int16 (~2 bytes/pixel/variable
at most), so even a generous chunk size keeps peak memory in the tens of
MB per variable, comfortably bounded without needing measurement to
justify a specific number the way tiling.py's budget does."""

_WRITE_LOCK_TIMEOUT_SECONDS = 2700.0
_WRITE_LOCK_POLL_SECONDS = 5.0


def _acquire_write_lock(out_path: Path):
    """Blocks (polling, NOT netCDF4/msvcrt's own built-in retry -- see
    below) until an OS-level exclusive lock on `out_path` can be acquired,
    then returns the open lock-file handle. This prevents a 2026-08-09
    incident: two processes (this project's own scheduler + a still-
    unexplained external process-duplication mechanism on the machine --
    every python.exe launch there was silently getting mirrored by a
    second, identical process) raced to open the SAME out_path for
    writing at the same time. netCDF4.Dataset(out_path, "w") has no
    protection against that at all -- the loser got a raw, unhandled
    PermissionError (a Windows sharing violation, over an SMB share),
    which crashed the whole run and threw away already-completed work
    rather than just waiting its turn.

    Deliberately a Python-level polling loop, not msvcrt.locking's own
    LK_LOCK blocking mode (or fcntl.flock without LOCK_NB) -- both of
    those only retry for a few seconds internally before raising, nowhere
    close to how long a writer here can legitimately hold this lock (a
    single target's merge write measured up to several minutes, and a
    from-scratch reprocess's total run time up to ~40 minutes).
    _WRITE_LOCK_TIMEOUT_SECONDS (45 min) is set comfortably above that
    worst case; a TimeoutError past that point means the other holder is
    genuinely stuck, not just still working, and should be investigated
    rather than silently waited on forever.

    Tied to the lock file's own OS-level handle lifetime, same mechanism
    as schedule_pipeline.py's _acquire_single_instance_lock -- released
    automatically the instant the holding process's handle closes, even
    on a hard kill, so a crashed holder can never leave this stuck.
    Unlike that function, this one is intentionally blocking rather than
    fail-fast: two writers reaching this specific call are, by
    construction, producing the exact same output for the exact same
    target (this function has exactly one caller site, per merged file),
    so the correct behavior for the second one is "wait, then proceed"
    -- not "refuse to run at all", which is what a fail-fast lock would
    mean for two genuinely independent scheduler ticks instead.

    Args:
        out_path: Path of the output file about to be written; the lock
            file itself lives alongside it under a `.locks/` subfolder.

    Returns:
        The open lock-file handle -- holds the OS-level exclusive lock
        until passed to `_release_write_lock`.

    Raises:
        TimeoutError: If the lock could not be acquired within
            `_WRITE_LOCK_TIMEOUT_SECONDS` (45 minutes), implying the
            current holder is genuinely stuck rather than still working.
    """
    # Kept in a .locks/ subfolder rather than alongside out_path itself
    # (2026-08-09) -- these files are never deleted (see this function's
    # own docstring on why: the OS-level lock, not the file's existence,
    # is what matters), so leaving them in the main output directory
    # accumulates one stray file per timestamp forever, cluttering a
    # directory a human might browse for .nc output. Purely
    # cosmetic -- doesn't change the locking mechanism itself.
    lock_path = out_path.parent / ".locks" / (out_path.name + ".writelock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    waited = 0.0
    announced = False
    while True:
        f = open(lock_path, "a+b")
        try:
            if os.fstat(f.fileno()).st_size == 0:
                f.write(b"0")
                f.flush()
            if sys.platform == "win32":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            if waited >= _WRITE_LOCK_TIMEOUT_SECONDS:
                raise TimeoutError(
                    f"could not acquire write lock for {out_path} after {_WRITE_LOCK_TIMEOUT_SECONDS:.0f}s -- "
                    "another process appears to be genuinely stuck holding it, not just still working"
                )
            if not announced:
                print(
                    f"  [merge_tiled_band_files] {out_path.name} is locked by another process (likely a "
                    "duplicate concurrent write) -- waiting for it to finish instead of crashing"
                )
                announced = True
            time.sleep(_WRITE_LOCK_POLL_SECONDS)
            waited += _WRITE_LOCK_POLL_SECONDS
        else:
            if announced:
                print(f"  [merge_tiled_band_files] acquired write lock for {out_path.name} after waiting {waited:.0f}s")
            return f


def _release_write_lock(f) -> None:
    """Releases and closes a lock-file handle acquired via `_acquire_write_lock`.

    Args:
        f: The open lock-file handle returned by `_acquire_write_lock`.

    Returns:
        None.
    """
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


def merge_tiled_band_files(
    paths: list[Path],
    out_path: Path,
    top_level_attrs: dict,
) -> None:
    """Combines already-written per-band tiled files (see
    open_tiled_band_writer/write_band_tile/finalize_tiled_band_writer)
    into one multi-band tier file -- WITHOUT decoding through xarray's CF
    auto mask/scale machinery the way _merge_single_band_files used to
    (xr.open_dataset + xr.merge + write_tier_netcdf's ds.to_netcdf()).

    This replaces a genuine problem: every per-band file's ``value_<band>``/
    ``pixel_resolution_km_<band>``/``blend_weight_winner_<band>``/
    ``satellite_id_<band>``/etc. fields are already packed int8/int16 on
    disk (see open_tiled_band_writer). Reading them via plain
    xr.open_dataset auto-decodes every packed field back to float64 for
    the WHOLE grid (CF mask+scale on read), and ds.to_netcdf() then
    re-encodes them back (CF mask+scale on write, via xarray's generic
    non-tiled encoder) -- both steps materialize full-grid-sized float64
    (or bool, for the intermediate notnull() mask) temporaries. This
    caused a crash on 2026-08-06/07: a numpy.core._exceptions.ArrayMemoryError
    ("Unable to allocate 4.42 GiB") under concurrent-multi-timestamp
    memory pressure, even though every per-band tile write that produced
    these files already stayed within its own tiling memory budget --
    this merge/write step was the ONE part of the whole pipeline that
    wasn't tiled/budget-aware.

    Since each band's own packed fields are independently already-ranged
    (nothing here needs reconciling against another band's value range --
    unlike, say, combining two satellites' candidates for the SAME
    variable), this merge is purely structural: copy each variable's raw
    on-disk values + scale_factor/add_offset/_FillValue attrs verbatim,
    tile-major (see _MERGE_CHUNK_ROWS), deduplicating the handful of
    fields shared verbatim across every band's file (lat/lon/crs/
    solar_zenith_angle_deg/is_daylit -- see open_tiled_band_writer's own
    docstring for why those are unsuffixed and redundantly present in
    every band's tmp file to begin with).

    The actual netCDF4.Dataset(out_path, "w") open below is guarded by
    _acquire_write_lock/_release_write_lock -- see that function's own
    docstring for the 2026-08-09 incident (two processes racing to open
    the same out_path, crashing with an unhandled PermissionError) this
    exists to prevent.

    Args:
        paths: Per-band tiled tmp files (see open_tiled_band_writer)
            to combine.
        out_path: Destination path for the merged multi-band tier file.
        top_level_attrs: Extra dataset-level attributes to set on the
            merged output (in addition to `Conventions`, which is always
            set to "CF-1.8" here).

    Returns:
        None. Writes the merged file at `out_path` as a side effect.
    """
    sources = [netCDF4.Dataset(str(p), "r") for p in paths]
    try:
        height = sources[0].dimensions["y"].size
        width = sources[0].dimensions["x"].size

        write_lock = _acquire_write_lock(out_path)
        try:
            out = netCDF4.Dataset(str(out_path), "w")
            try:
                out.createDimension("y", height)
                out.createDimension("x", width)

                copied: set[str] = set()
                for src in sources:
                    for name, src_var in src.variables.items():
                        if name in copied:
                            continue  # shared field (lat/lon/crs/solar_zenith_angle_deg/is_daylit) -- first copy wins, identical by construction
                        for dim in src_var.dimensions:
                            if dim not in out.dimensions:
                                out.createDimension(dim, src.dimensions[dim].size)

                        src_var.set_auto_maskandscale(False)
                        create_kwargs = {}
                        if "_FillValue" in src_var.ncattrs():
                            create_kwargs["fill_value"] = src_var.getncattr("_FillValue")
                        dst_var = out.createVariable(
                            name,
                            src_var.dtype,
                            src_var.dimensions,
                            zlib=True,
                            complevel=_COMPRESSION_LEVEL,
                            **create_kwargs,
                        )
                        dst_var.set_auto_maskandscale(False)
                        for attr in src_var.ncattrs():
                            if attr == "_FillValue":
                                continue  # already set via create_kwargs above
                            dst_var.setncattr(attr, src_var.getncattr(attr))

                        if src_var.dimensions == ("y", "x"):
                            for y0 in range(0, height, _MERGE_CHUNK_ROWS):
                                y1 = min(y0 + _MERGE_CHUNK_ROWS, height)
                                dst_var[y0:y1, :] = src_var[y0:y1, :]
                        else:
                            dst_var[...] = src_var[...]

                        copied.add(name)

                out.Conventions = "CF-1.8"
                for key, value in top_level_attrs.items():
                    setattr(out, key, value)
            finally:
                out.close()
        finally:
            _release_write_lock(write_lock)
    finally:
        for src in sources:
            src.close()


def _pack_finite(data: np.ndarray, scale_factor: float, add_offset: float, fill_value: int) -> np.ndarray:
    """Manual CF int16 packing with correct NaN -> fill_value handling.
    NOT the same as assigning a float array directly to a netCDF4
    variable that has scale_factor/add_offset set -- netCDF4-python's own
    write-time auto-packing does NOT correctly substitute the fill value
    for NaN inputs (a silently-wrong-not-crashing bug: NaN packed
    straight through the scale/offset arithmetic and got cast to int16,
    landing on an arbitrary wrapped value instead of the fill value).
    This function is used for every packed write in the tiled writer
    below; callers must also call `var.set_auto_maskandscale(False)`
    before assigning the result and only set scale_factor/add_offset as
    attrs (metadata, not applied on this write) either before or after --
    this combination round-trips correctly through a normal
    (auto-scale-on) read.

    Args:
        data: Plain float array (NaN for missing values) to pack.
        scale_factor: CF scale_factor to pack against.
        add_offset: CF add_offset to pack against.
        fill_value: Integer fill value substituted wherever `data` is
            non-finite.

    Returns:
        int16 array of the same shape as `data`, packed as
        `round((data - add_offset) / scale_factor)`, with `fill_value`
        substituted for non-finite entries.
    """
    packed = np.round((data - add_offset) / scale_factor)
    return np.where(np.isfinite(data), packed, fill_value).astype(np.int16)


_SAFE_TIME_UNITS = "milliseconds since 1970-01-01"
_SAFE_TIME_CALENDAR = "proleptic_gregorian"
"""Fixed, always-fine-enough-grained time encoding used when patch_tier_
file_with_satellite appends a satellite -- deliberately NOT the existing
variable's own units/calendar (see that function's docstring for the
bug this avoids: a file's initial write can pick a units granularity too
coarse for a LATER value, e.g. "days since <ref>" when only one satellite
existed at write time, silently truncating a 2-minute offset to zero when
cast to int64). Re-normalizing every datetime-dimensioned field to this
fixed convention on every patch sidesteps needing to reason about whether
the existing granularity happens to be fine enough -- it always is."""


def _is_cf_time_var(var: netCDF4.Variable) -> bool:
    """True if `var` looks like a CF-encoded time variable (has a `units` attr of the form "<x> since <ref>")."""
    return "units" in var.ncattrs() and " since " in var.getncattr("units")


def _new_satellite_metadata_row(var: netCDF4.Variable, meta: SatelliteMeta, band: str) -> object:
    """The single new value to append to one satellite<suffix>-dimensioned
    variable, given this variable's own on-disk representation. Datetime
    fields return a plain dt.datetime, NOT a pre-encoded number -- the
    caller (patch_tier_file_with_satellite) re-encodes every datetime
    field (old values and this new one together) under _SAFE_TIME_UNITS,
    since trusting the existing variable's own units/calendar here is
    exactly the bug this whole module now guards against (see
    _SAFE_TIME_UNITS's own docstring).

    Args:
        var: The existing `satellite<suffix>`-dimensioned NetCDF variable
            being appended to; its name determines which field of `meta`
            to pull the new value from.
        meta: The new satellite's metadata.
        band: Band name (used to strip the `_<band>` suffix off
            `var.name` for the scan_start/scan_end/scan_mid dispatch).

    Returns:
        The single new value to append -- a `dt.datetime` for datetime
        fields (left un-encoded; see caller), or the appropriate scalar
        (str/float/int) otherwise.
    """
    name = var.name
    if name.startswith("satellite_scan_"):
        return {"scan_start": meta.scan_start, "scan_end": meta.scan_end, "scan_mid": meta.scan_mid}[
            name.removeprefix("satellite_").removesuffix(f"_{band.lower()}")
        ]
    if name.startswith("satellite_longitude"):
        return meta.longitude_deg
    if name.startswith("satellite_temporal_resolution_minutes"):
        return int(meta.temporal_resolution_minutes)
    if name.startswith("satellite_key"):
        return meta.satellite_key
    if name.startswith("satellite_source_filename"):
        return meta.source_filename
    if name.startswith("satellite_srf_correction_gain"):
        return meta.srf_correction_gain
    if name.startswith("satellite_srf_correction_offset"):
        return meta.srf_correction_offset
    if name.startswith("satellite_srf_correction_method"):
        return meta.srf_correction_method
    if name.startswith("satellite_spatial_resolution_km") or name.startswith("satellite_nominal_spatial_resolution_km"):
        return meta.nominal_spatial_resolution_km
    if name.startswith("satellite_band_wavelength_um"):
        return meta.band_center_wavelength_um
    # The satellite<suffix> dimension coordinate itself (plain int32 ids,
    # see mosaic_io.py's own int32 dtype fix -- no name prefix match
    # above, it IS "satellite_<band>" exactly).
    return meta.satellite_id


def _widen_scale_offset_if_needed(
    full_existing_packed: np.ndarray, scale: float, offset: float, fill: int, new_finite: np.ndarray
) -> tuple[float, float, bool]:
    """Widens (scale, offset) only if new_finite's range doesn't already fit what they can represent.

    The bug this guards against, first seen 2026-08-08: silently packing a value
    outside an int16 scale/offset's representable range doesn't raise, it
    WRAPS to an incorrect, wrongly-ordered value (e.g. 1.5 packed against
    a scale/offset calibrated for [2.0, 6.0] produced 24577 -- a value
    that would have LOST a per-pixel comparison it should have WON,
    silently corrupting the selection decision, not just the stored
    number). `full_existing_packed` must be the variable's COMPLETE
    array, not just the columns being patched -- scale_factor/add_offset
    are single attributes shared by the WHOLE variable, so widening only
    has to consider a restricted slice's range, it could still be too
    narrow for values elsewhere in the file this call never looks at.

    Args:
        full_existing_packed: The variable's complete on-disk packed
            int16 array (not just the columns being patched).
        scale: Current CF scale_factor.
        offset: Current CF add_offset.
        fill: Integer fill value marking "no data" in `full_existing_packed`.
        new_finite: The new candidate's finite (non-NaN) values that must
            also be representable under the returned (scale, offset).

    Returns:
        Tuple of ``(scale, offset, needs_repack)``. If `new_finite`
        already fits within the existing (scale, offset), returns them
        unchanged with ``needs_repack=False``. Otherwise returns a new
        (scale, offset) wide enough to cover both the existing decoded
        range and `new_finite`, with ``needs_repack=True``.
    """
    if new_finite.size == 0:
        return scale, offset, False
    represented_min = -32767 * scale + offset
    represented_max = 32767 * scale + offset
    new_min, new_max = float(new_finite.min()), float(new_finite.max())
    if new_min >= represented_min and new_max <= represented_max:
        return scale, offset, False

    existing_valid = full_existing_packed != fill
    if existing_valid.any():
        existing_decoded = full_existing_packed[existing_valid].astype(np.float64) * scale + offset
        combined_min = min(float(existing_decoded.min()), new_min)
        combined_max = max(float(existing_decoded.max()), new_max)
    else:
        combined_min, combined_max = new_min, new_max
    new_scale, new_offset = _scale_offset_from_range(combined_min, combined_max)
    return new_scale, new_offset, True


_LOCAL_PATCH_TMP_DIR = Path(__file__).resolve().parents[3] / "data" / "mosaics"
"""Local scratch directory for patch_tier_file_with_satellite's local-
copy optimization below -- same directory run_phase2_tier.py's own
OUT_DIR already uses for its local band tmp files, reused here rather
than inventing a second convention."""


def patch_tier_file_with_satellite(
    nc_path: Path, band: str, value: np.ndarray, resolution_km: np.ndarray, columns: np.ndarray, meta: SatelliteMeta
) -> bool:
    """Incrementally folds ONE newly-available satellite's already-
    reprojected candidate into an already-published tier file, instead of
    a full run_phase2_tier.py reprocess -- built specifically for
    schedule_pipeline.py's partial-timestamp catch-up (see that script's
    docstring point 3). The motivation: a full reprocess re-downloads
    nothing for satellites already cached, but STILL re-reprojects and
    re-selects EVERY satellite from scratch (~20 min, dominated by CPU,
    not I/O) -- this instead reuses run_phase2_tier.py's own --single-
    satellite machinery to build just the NEW candidate, then does the
    same per-pixel best-actual-resolution-wins comparison select_winners
    already implements, but only against what's ALREADY stored in the
    file (not every candidate from scratch).

    Both the read (existing file) and write (new copy) sides are staged
    through _LOCAL_PATCH_TMP_DIR rather than operating directly against
    nc_path (which lives on a network share, \\\\...\\THREDDS\\... in this
    project's deployment) -- a 2026-08-09 finding: netCDF4/HDF5's
    own chunked, compressed access pattern means many small, latency-
    bound read/write operations over SMB, not one efficient transfer.
    Bulk-copying the whole file in one sequential shutil.copy2 (both
    directions) and doing all the netCDF4 work against local disk
    keeps the total bytes moved over the network about the same (the
    data only lives on the share either way) but replaces the slow part
    -- many small SMB round-trips -- with two fast bulk copies.

    `value`/`resolution_km` are the new candidate's already-reprojected
    arrays (see select.Candidate) restricted to `columns` -- the full-
    grid column indices this satellite's cropped footprint actually
    covers (select.Candidate.columns; everywhere else in the grid is
    provably unaffected by this satellite and left untouched). Comparison
    happens directly on PACKED int16 values, not decoded floats --
    correct because _compute_scale_offset always produces a strictly
    positive scale_factor (see its own docstring), so packed-value
    ordering exactly matches unpacked-value ordering. The common case
    needs no full-grid decode/re-encode; the exception is if the new
    satellite's values fall outside what the file's existing
    ``scale_factor``/``add_offset`` can represent (see _widen_scale_offset_if_needed),
    in which case the WHOLE variable (not just `columns`) gets decoded
    and repacked under a wider scale/offset -- a real, not theoretical,
    case as of 2026-08-08: silently packing an out-of-range value
    doesn't raise, it wraps to an incorrect value that can make a
    genuinely better satellite silently LOSE its comparison.

    Returns True if the new satellite actually won at least one pixel in
    this band (the file was rewritten either way, since the satellite
    still needs recording in the per-satellite metadata table -- see
    schedule_pipeline.py's _actual_contributors, which needs this entry
    to stop treating the satellite as still-missing).

    Writes via a .patch.tmp file + os.replace, same atomic-write
    reasoning as schedule_pipeline.py's _save_state fix: this rewrites
    the WHOLE file (a fixed-size NetCDF dimension, the satellite<suffix>
    coordinate, can't be resized in place to fit the one new row this
    needs -- easiest correct fix is a full copy-with-overlay, same
    technique merge_tiled_band_files already uses), so an interrupted
    write must never leave a half-written file in place of the real one.
    The final os.replace still targets nc_path's own network directory
    (via a network-side .patch.tmp, populated by one bulk copy from the
    local build) so the atomicity guarantee is unchanged -- only WHERE
    the slow read/write work happens moved, not the swap-in step itself.

    Args:
        nc_path: Path to the already-published tier file to patch, on
            its real (possibly network-share) location.
        band: Band name whose ``_<band>``-suffixed variables are being
            patched, e.g. ``"vis"``.
        value: New satellite's already-reprojected value array,
            restricted to `columns`.
        resolution_km: New satellite's actual per-pixel resolution,
            restricted to `columns`, same shape as `value`.
        columns: Full-grid column indices this satellite's cropped
            footprint covers (select.Candidate.columns).
        meta: New satellite's metadata row to append to the file's
            per-satellite metadata table.

    Returns:
        True if the new satellite won at least one pixel in this band
        (i.e. beat or filled in for the existing per-pixel resolution
        comparison at some column); False if it won none. The file is
        rewritten (and the satellite recorded in the metadata table)
        either way, unless the idempotency check finds it already
        recorded, in which case this returns False without touching the
        file at all.
    """
    suffix = f"_{band.lower()}"
    columns = np.asarray(columns)
    value = np.asarray(value, dtype=np.float64)
    resolution_km = np.asarray(resolution_km, dtype=np.float64)

    # Cheap idempotency check straight against the real (network) file --
    # the bug found on 2026-08-08: this function's only caller
    # (schedule_pipeline.py's _patch_newly_available) loops one satellite
    # across all 4 bands and abandons the WHOLE attempt (falls back to a
    # full reprocess) if any single band's patch raises -- but bands
    # already patched successfully before the failure are NOT rolled
    # back, since each band is its own independent file rewrite. A later
    # retry would then call this again for a satellite that's already
    # recorded in SOME bands, and without this check it would blindly
    # append a second, duplicate row rather than detecting it's already
    # there (a test run left satellite_key_tir1/tir2 with
    # meteosat-10/meteosat-09 listed twice each). Only reads one small
    # variable -- not worth a full local copy just for this.
    src = netCDF4.Dataset(str(nc_path), "r")
    try:
        key_var_name = f"satellite_key{suffix}"
        if key_var_name in src.variables and meta.satellite_key in src.variables[key_var_name][:]:
            print(f"    [{band}] {meta.satellite_key}: already recorded in this band, skipping (idempotent no-op)")
            return False
    finally:
        src.close()

    # Not idempotent -- doing the real patch. Bulk-copy the existing file
    # to local disk in one sequential transfer (see this function's own
    # docstring for why) before any netCDF4 reading/writing starts.
    _LOCAL_PATCH_TMP_DIR.mkdir(parents=True, exist_ok=True)
    local_src_path = _LOCAL_PATCH_TMP_DIR / f"{nc_path.stem}{suffix}.patch_src.tmp"
    local_out_path = _LOCAL_PATCH_TMP_DIR / f"{nc_path.stem}{suffix}.patch_out.tmp"
    shutil.copy2(nc_path, local_src_path)
    try:
        src = netCDF4.Dataset(str(local_src_path), "r")
        try:
            height = src.dimensions["y"].size
            sat_dim_name = f"satellite{suffix}"
            old_sat_count = src.dimensions[sat_dim_name].size

            res_var = src.variables[f"pixel_resolution_km{suffix}"]
            value_var = src.variables[f"value{suffix}"]
            res_var.set_auto_maskandscale(False)
            value_var.set_auto_maskandscale(False)
            res_fill = res_var.getncattr("_FillValue")
            value_fill = value_var.getncattr("_FillValue")

            # Full-array reads (not just `columns`) -- required for
            # _widen_scale_offset_if_needed's own correctness (see its
            # docstring: scale_factor/add_offset are whole-variable
            # attributes, a columns-restricted view could miss why a wider
            # range is already needed elsewhere in the file).
            res_full = res_var[:, :]
            value_full = value_var[:, :]
            res_scale, res_offset, res_repack = _widen_scale_offset_if_needed(
                res_full, res_var.scale_factor, res_var.add_offset, res_fill, resolution_km[np.isfinite(resolution_km)]
            )
            value_scale, value_offset, value_repack = _widen_scale_offset_if_needed(
                value_full, value_var.scale_factor, value_var.add_offset, value_fill, value[np.isfinite(value)]
            )
            if res_repack:
                res_full = _pack_finite(
                    np.where(res_full != res_fill, res_full.astype(np.float64) * res_var.scale_factor + res_var.add_offset, np.nan),
                    res_scale, res_offset, res_fill,
                )
            if value_repack:
                value_full = _pack_finite(
                    np.where(value_full != value_fill, value_full.astype(np.float64) * value_var.scale_factor + value_var.add_offset, np.nan),
                    value_scale, value_offset, value_fill,
                )

            existing_res_packed = res_full[:, columns]
            new_res_packed = _pack_finite(resolution_km, res_scale, res_offset, res_fill)
            existing_valid = existing_res_packed != res_fill
            new_valid = new_res_packed != res_fill
            wins = new_valid & (~existing_valid | (new_res_packed < existing_res_packed))
            won_any = bool(wins.any())

            res_full[:, columns] = np.where(wins, new_res_packed, existing_res_packed)
            new_value_packed = _pack_finite(value, value_scale, value_offset, value_fill)
            value_full[:, columns] = np.where(wins, new_value_packed, value_full[:, columns])
            satid_full = src.variables[f"satellite_id{suffix}"][:, :]
            satid_full[:, columns] = np.where(wins, np.int8(meta.satellite_id), satid_full[:, columns])

            out = netCDF4.Dataset(str(local_out_path), "w")
            try:
                for dim_name, dim in src.dimensions.items():
                    out.createDimension(dim_name, old_sat_count + 1 if dim_name == sat_dim_name else dim.size)

                for name, src_var in src.variables.items():
                    src_var.set_auto_maskandscale(False)
                    create_kwargs = {}
                    if "_FillValue" in src_var.ncattrs():
                        create_kwargs["fill_value"] = src_var.getncattr("_FillValue")
                    dst_var = out.createVariable(
                        name, src_var.dtype, src_var.dimensions, zlib=True, complevel=_COMPRESSION_LEVEL, **create_kwargs
                    )
                    dst_var.set_auto_maskandscale(False)
                    for attr in src_var.ncattrs():
                        if attr != "_FillValue":
                            dst_var.setncattr(attr, src_var.getncattr(attr))
                    # Widened scale/offset (if any) overrides whatever was
                    # just copied verbatim above -- must win, or the repacked
                    # values written below would be decoded wrong by anyone
                    # reading this file back.
                    if name == f"pixel_resolution_km{suffix}" and res_repack:
                        dst_var.scale_factor, dst_var.add_offset = res_scale, res_offset
                    if name == f"value{suffix}" and value_repack:
                        dst_var.scale_factor, dst_var.add_offset = value_scale, value_offset

                    if name == f"value{suffix}":
                        for y0 in range(0, height, _MERGE_CHUNK_ROWS):
                            y1 = min(y0 + _MERGE_CHUNK_ROWS, height)
                            dst_var[y0:y1, :] = value_full[y0:y1, :]
                    elif name == f"pixel_resolution_km{suffix}":
                        for y0 in range(0, height, _MERGE_CHUNK_ROWS):
                            y1 = min(y0 + _MERGE_CHUNK_ROWS, height)
                            dst_var[y0:y1, :] = res_full[y0:y1, :]
                    elif name == f"satellite_id{suffix}":
                        for y0 in range(0, height, _MERGE_CHUNK_ROWS):
                            y1 = min(y0 + _MERGE_CHUNK_ROWS, height)
                            dst_var[y0:y1, :] = satid_full[y0:y1, :]
                    elif sat_dim_name in src_var.dimensions:
                        if _is_cf_time_var(src_var):
                            # Re-normalize to _SAFE_TIME_UNITS rather than
                            # trust this variable's own (possibly too coarse
                            # for the new value -- see _SAFE_TIME_UNITS'S
                            # docstring) units/calendar. Decode ALL existing
                            # entries via their original units first
                            # (always safe/lossless regardless of
                            # granularity -- the precision problem is only on
                            # the ENCODE side), then re-encode everyone
                            # together under the fixed convention.
                            old_decoded = netCDF4.num2date(
                                src_var[:], units=src_var.units, calendar=src_var.calendar, only_use_cftime_datetimes=False
                            )
                            new_decoded = _new_satellite_metadata_row(src_var, meta, band)
                            dst_var.units = _SAFE_TIME_UNITS
                            dst_var.calendar = _SAFE_TIME_CALENDAR
                            dst_var[:] = netCDF4.date2num(
                                [*old_decoded, new_decoded], units=_SAFE_TIME_UNITS, calendar=_SAFE_TIME_CALENDAR
                            )
                        else:
                            # No trailing `...` on either assignment -- these
                            # are all 1-D (dims=(sat_dim_name,) only), and
                            # netCDF4-python's vlen-string variables
                            # (satellite_key_<band>, satellite_source_filename_
                            # <band>, satellite_srf_correction_method_<band>)
                            # raise "IndexError: Illegal index" specifically
                            # when an Ellipsis is combined with a scalar
                            # index -- plain `var[i] = value` (no comma)
                            # works for both string and numeric dtypes here,
                            # so used uniformly rather than branching by
                            # dtype.
                            dst_var[:old_sat_count] = src_var[:]
                            dst_var[old_sat_count] = _new_satellite_metadata_row(src_var, meta, band)
                    elif name == f"time{suffix}":
                        # Recompute the band's authoritative timestamp -- the
                        # median scan mid-time across ALL contributing
                        # satellites, now including this new one -- rather
                        # than leave it describing only the old, incomplete
                        # satellite set. satellite_scan_mid_<band> is read
                        # from `src` (its own original, still-valid units --
                        # decoding existing values is always safe regardless
                        # of granularity), but re-encoded under
                        # _SAFE_TIME_UNITS, same reasoning as the sat-dim
                        # branch above.
                        mid_var = src.variables[f"satellite_scan_mid{suffix}"]
                        old_mids = netCDF4.num2date(
                            mid_var[:], units=mid_var.units, calendar=mid_var.calendar, only_use_cftime_datetimes=False
                        )
                        new_median = _median_datetime([*old_mids, meta.scan_mid])
                        dst_var.units = _SAFE_TIME_UNITS
                        dst_var.calendar = _SAFE_TIME_CALENDAR
                        dst_var[...] = netCDF4.date2num(new_median, units=_SAFE_TIME_UNITS, calendar=_SAFE_TIME_CALENDAR)
                    elif src_var.dimensions == ("y", "x"):
                        for y0 in range(0, height, _MERGE_CHUNK_ROWS):
                            y1 = min(y0 + _MERGE_CHUNK_ROWS, height)
                            dst_var[y0:y1, :] = src_var[y0:y1, :]
                    else:
                        dst_var[...] = src_var[...]

                for attr in src.ncattrs():
                    setattr(out, attr, src.getncattr(attr))
            finally:
                out.close()
        finally:
            src.close()

        # Bulk-upload the finished local file back to the network share
        # in one sequential copy, then the same atomic os.replace as
        # before -- see this function's own docstring for why the read/
        # write work itself happens locally but the swap-in step doesn't
        # change.
        network_tmp_path = nc_path.with_suffix(".patch.tmp")
        shutil.copy2(local_out_path, network_tmp_path)
        os.replace(network_tmp_path, nc_path)
    finally:
        local_src_path.unlink(missing_ok=True)
        local_out_path.unlink(missing_ok=True)

    return won_any


def read_single_satellite_npz(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, SatelliteMeta]:
    """Reads back a run_phase2_tier.py --single-satellite output (see that
    script's own ``_write_candidate_npz`` -- same field names, this is its
    inverse), for patch_tier_file_with_satellite's caller
    (``schedule_pipeline.py``'s partial-timestamp catch-up). Deliberately
    reimplemented here rather than importing run_phase2_tier.py's own
    ``_read_candidate_npz`` -- that module pulls in satpy/xarray/GDAL just
    to get this one function, a startup cost paid on every scheduler
    tick even when no patch is needed that tick (same "duplicate a small,
    tightly-coupled piece rather than import a heavy module for it"
    reasoning this project already uses for METEOSAT_DATASET_BY_SLOT in
    ``schedule_pipeline.py``). Returns (value, resolution_km, columns, meta) instead of
    a select.Candidate -- this module has no reason to depend on
    select.py's types, and patch_tier_file_with_satellite only ever needs
    these four pieces anyway.

    Args:
        path: Path to the ``.npz`` file written by run_phase2_tier.py's
            ``_write_candidate_npz``.

    Returns:
        Tuple of ``(value, resolution_km, columns, meta)`` -- the new
        satellite's already-reprojected value/resolution arrays
        (restricted to `columns`), the full-grid column indices its
        footprint covers, and its :class:`SatelliteMeta`.
    """
    with np.load(path, allow_pickle=False) as npz:
        meta = SatelliteMeta(
            satellite_key=str(npz["satellite_key"]),
            satellite_id=int(npz["satellite_id"]),
            longitude_deg=float(npz["longitude_deg"]),
            scan_start=dt.datetime.fromisoformat(str(npz["scan_start"])),
            scan_end=dt.datetime.fromisoformat(str(npz["scan_end"])),
            scan_mid=dt.datetime.fromisoformat(str(npz["scan_mid"])),
            nominal_spatial_resolution_km=float(npz["nominal_spatial_resolution_km"]),
            band_center_wavelength_um=float(npz["band_center_wavelength_um"]),
            temporal_resolution_minutes=float(npz["temporal_resolution_minutes"]),
            source_filename=str(npz["source_filename"]),
            srf_correction_gain=float(npz["srf_correction_gain"]),
            srf_correction_offset=float(npz["srf_correction_offset"]),
            srf_correction_method=str(npz["srf_correction_method"]),
        )
        return npz["value"], npz["resolution_km"], npz["columns"], meta


@dataclass
class TiledBandWriter:
    """Handle bundling everything open_tiled_band_writer creates --
    passed to write_band_tile per tile and finalize_tiled_band_writer
    once at the end. Two on-disk files, not one, because a netCDF4
    variable's storage dtype is fixed at creation and this writer needs
    to see EVERY tile's data before it knows the true global min/max
    needed to choose value/pixel_resolution_km/solar_zenith_angle_deg's
    int16 packing range (`_compute_scale_offset`'s existing full-grid
    version can call `.values.min()/.max()` directly because it always
    has the whole array in memory -- tile-major processing deliberately
    never does). `stage_path` holds those three fields as plain
    unpacked float32, written once per tile during the main pass;
    `final_path` holds everything else directly (fixed-encoding fields
    don't need to wait) plus, at finalize(), the properly packed int16
    versions of the staged fields, computed from the by-then-known true
    range and read back from the stage file tile by tile -- never
    holding more than one tile of either file in memory at once.

    Attributes:
        final_ds: Open netCDF4 Dataset for the final output file
            (fixed-encoding fields written directly; packed dynamic
            fields created only at finalize()).
        final_path: Path of `final_ds`.
        stage_ds: Open netCDF4 Dataset for the temporary staging file
            holding unpacked float32 dynamic fields.
        stage_path: Path of `stage_ds`; deleted by
            :func:`finalize_tiled_band_writer`.
        tiles: The full list of tile row-ranges this writer expects
            (``list[tiling.TileRowRange]``), used by finalize() to
            iterate the staged data back out tile by tile.
        include_sza: Whether this writer also tracks
            ``solar_zenith_angle_deg``/``is_daylit``.
        value_long_name: `long_name` attribute for the packed `value`
            variable, set at finalize().
        value_units: `units` attribute for the packed `value` variable,
            set at finalize().
        suffix: Band-name variable suffix (e.g. ``"_vis"``), matching
            build_tier_dataset's per-band renaming convention.
        running_range: Per dynamic-field-name running ``[min, max]``
            accumulated across every :func:`write_band_tile` call, used
            by :func:`finalize_tiled_band_writer` to choose each field's
            int16 packing range.
    """

    final_ds: netCDF4.Dataset
    final_path: Path
    stage_ds: netCDF4.Dataset
    stage_path: Path
    tiles: list  # list[tiling.TileRowRange] -- not type-hinted directly to avoid a hard dependency from mosaic_io on tiling.py
    include_sza: bool
    value_long_name: str
    value_units: str
    suffix: str
    running_range: dict[str, list[float]] = field(default_factory=dict)


def open_tiled_band_writer(
    final_path: Path,
    stage_path: Path,
    tiles: list,
    grid_shape: tuple[int, int],
    lat_1d: np.ndarray,
    lon_1d: np.ndarray,
    band_name: str,
    value_long_name: str,
    value_units: str,
    target_time: dt.datetime,
    include_sza: bool,
    variable_suffix: str = "",
) -> TiledBandWriter:
    """Creates both files at full grid_shape, ready for write_band_tile
    calls in any tile order (though callers should still go through
    `tiles` in order for readable logs -- nothing here requires it).

    `variable_suffix` (e.g. "_vis") matches build_tier_dataset's own
    per-band renaming convention -- this writer produces one band's own
    tmp file (mirroring the existing isolate_bands per-band-then-merge
    pattern in run_phase2_tier.py, not a new architecture), so its
    grid-shaped dynamic/fixed fields need the same suffix
    `_merge_single_band_files` expects when combining multiple bands'
    files into the final tier file. `lat`/`lon`/`crs` stay unsuffixed
    (identical across every band in a tier, deduplicated by the merge)
    and so does `solar_zenith_angle_deg`/`is_daylit` (a property of
    ground point + time, not of which band -- same "shared, unsuffixed,
    redundantly present in each band's own tmp file, deduplicated at
    merge" contract build_tier_dataset already uses).

    `final_path`'s fixed-encoding fields (satellite_id, is_blended,
    runner_up_satellite_id, blend_weight_winner, is_daylit) get an
    explicit `_FillValue=_FILL_INT8`/`_FILL_INT16` at creation -- unlike
    build_mosaic_dataset's full-grid path (safe there because the whole
    array is always 100% computed before any write happens, so there's
    no such thing as an "unwritten" pixel), a tiled writer's variables
    start genuinely unwritten and stay that way for any tile that never
    gets a write_band_tile call -- without an explicit fill value,
    netCDF4/HDF5's implementation-default fill for an int8 variable is a
    nonzero, implementation-defined value, silently breaking the
    documented "0 = no coverage"/"0 = not blended" contract these fields
    have always had. Callers should still always call write_band_tile
    for every tile in `tiles` regardless (even an all-zero result) as
    defensive-in-depth, not rely on fill-value semantics alone.

    Args:
        final_path: Destination path for the final, fully-packed output
            file (created here, populated tile by tile via
            write_band_tile and finalize_tiled_band_writer).
        stage_path: Path for the temporary unpacked-float32 staging file
            (created here, deleted by finalize_tiled_band_writer).
        tiles: The full list of tile row-ranges
            (``list[tiling.TileRowRange]``) this writer will be given
            data for.
        grid_shape: ``(height, width)`` of the full output grid.
        lat_1d: 1D latitude coordinate values, length `height`.
        lon_1d: 1D longitude coordinate values, length `width`.
        band_name: Band name used in the dataset `title` attribute.
        value_long_name: `long_name` attribute for the packed `value`
            variable (applied at finalize()).
        value_units: `units` attribute for the packed `value` variable
            (applied at finalize()).
        target_time: The originally requested mosaic timestamp, recorded
            as the `requested_target_time` attribute.
        include_sza: Whether to also create the shared, unsuffixed
            ``is_daylit``/``solar_zenith_angle_deg`` fields.
        variable_suffix: Band-name variable suffix (e.g. ``"_vis"``),
            matching build_tier_dataset's per-band renaming convention.

    Returns:
        A :class:`TiledBandWriter` ready to be passed to
        :func:`write_band_tile` for each tile, then
        :func:`finalize_tiled_band_writer` once at the end.
    """
    height, width = grid_shape
    final_ds = netCDF4.Dataset(str(final_path), "w")
    final_ds.createDimension("y", height)
    final_ds.createDimension("x", width)

    lat_var = final_ds.createVariable("lat", "f4", ("y",), zlib=True, complevel=_COMPRESSION_LEVEL)
    lat_var.standard_name = "latitude"
    lat_var.units = "degrees_north"
    lat_var[:] = lat_1d

    lon_var = final_ds.createVariable("lon", "f4", ("x",), zlib=True, complevel=_COMPRESSION_LEVEL)
    lon_var.standard_name = "longitude"
    lon_var.units = "degrees_east"
    lon_var[:] = lon_1d

    # "i4" (int32), not "i8" -- a "long"/64-bit int here is DAP2/OPeNDAP-
    # incompatible (raises "NcDDS Variable data type = long", seen
    # 2026-08-07) and this dummy value is always 0, so int32 loses nothing.
    crs_var = final_ds.createVariable("crs", "i4", ())
    crs_var.grid_mapping_name = "latitude_longitude"
    crs_var.longitude_of_prime_meridian = 0.0
    crs_var[...] = 0

    def _make_int8_var(name: str, long_name: str):
        var = final_ds.createVariable(
            name, "i1", ("y", "x"), zlib=True, complevel=_COMPRESSION_LEVEL, fill_value=_FILL_INT8
        )
        var.long_name = long_name
        var.grid_mapping = "crs"
        var.coordinates = "lat lon"
        return var

    # flag_values/flag_meanings depend on `metas`, not known until
    # finalize() -- set there, not here.
    _make_int8_var(
        f"satellite_id{variable_suffix}",
        "PRIMARY (highest-weighted) source satellite for this pixel (0 = no coverage). Where "
        "is_blended is true, this pixel's value is a weighted mix of this satellite and "
        "runner_up_satellite_id -- see blend_weight_winner for the exact split, do not treat it "
        "as a pure single-satellite observation.",
    )
    _make_int8_var(
        f"is_blended{variable_suffix}",
        "1 where processing/blend.py feathered this pixel between satellite_id and "
        "runner_up_satellite_id instead of a hard cut, 0 where value is a pure single-satellite "
        "observation",
    )
    _make_int8_var(
        f"runner_up_satellite_id{variable_suffix}",
        "second satellite contributing to this pixel's value where is_blended=1 (0 elsewhere -- "
        "including pixels with only one candidate at all, which can never be blended)",
    )

    blend_weight_var = final_ds.createVariable(
        f"blend_weight_winner{variable_suffix}", "i2", ("y", "x"), zlib=True, complevel=_COMPRESSION_LEVEL, fill_value=_FILL_INT16
    )
    blend_weight_var.set_auto_maskandscale(False)  # see _pack_finite's docstring -- writes must bypass auto-packing
    blend_weight_var.long_name = (
        "fraction of this pixel's value contributed by satellite_id (vs. 1-this from "
        "runner_up_satellite_id); exactly 1.0 wherever is_blended=0 -- lets the original "
        "per-satellite values be reconstructed exactly: value = w*winner + (1-w)*runner_up"
    )
    blend_weight_var.grid_mapping = "crs"
    blend_weight_var.coordinates = "lat lon"
    blend_weight_var.scale_factor = 1.0 / 32767
    blend_weight_var.add_offset = 0.5

    if include_sza:
        # Deliberately UNSUFFIXED, unlike every other field above --
        # solar_zenith_angle_deg/is_daylit are a property of ground point
        # + time, not of which band, so every band's own tmp file gets a
        # redundant copy of the same values (matching build_tier_dataset's
        # existing contract) that xr.merge(..., compat="equals") later
        # deduplicates into one shared copy in the final tier file.
        _make_int8_var(
            "is_daylit",
            "1 where solar_zenith_angle_deg < 90 (day), 0 where night. Distinguishes "
            "satellite_id=0 caused by nighttime (VIS/SWIR have no reflected-sunlight signal to "
            "measure) from satellite_id=0 caused by a genuine coverage gap -- satellite_id alone "
            "cannot tell these apart.",
        )

    final_ds.Conventions = "CF-1.8"
    final_ds.title = f"Global geostationary mosaic -- {band_name}"
    final_ds.history = (
        f"Generated {dt.datetime.now(dt.timezone.utc).isoformat()} by "
        "global_mosaic.processing.mosaic_io (tiled writer)"
    )
    final_ds.requested_target_time = target_time.isoformat()
    final_ds.references = "See global_mosaic.satellites.sat_metadata for per-satellite technical specifications."

    # dynamic_names: (variable name in final_path/stage_ds, is-this-field-
    # suffixed). "value"/"pixel_resolution_km" are genuinely per-band;
    # "solar_zenith_angle_deg" is shared -- same unsuffixed reasoning as
    # is_daylit above.
    dynamic_names = [f"value{variable_suffix}", f"pixel_resolution_km{variable_suffix}"]
    if include_sza:
        dynamic_names.append("solar_zenith_angle_deg")

    stage_ds = netCDF4.Dataset(str(stage_path), "w")
    stage_ds.createDimension("y", height)
    stage_ds.createDimension("x", width)
    for name in dynamic_names:
        stage_var = stage_ds.createVariable(name, "f4", ("y", "x"), zlib=True, complevel=1, fill_value=np.nan)
        # complevel=1, not _COMPRESSION_LEVEL -- this file is deleted at
        # the end of finalize(), never read by anything but this same
        # writer's own repack pass, so it isn't worth paying extra CPU for
        # a smaller intermediate no one keeps.
        del stage_var  # attrs not needed on a temp file nothing else reads

    return TiledBandWriter(
        final_ds=final_ds,
        final_path=final_path,
        stage_ds=stage_ds,
        stage_path=stage_path,
        tiles=list(tiles),
        include_sza=include_sza,
        value_long_name=value_long_name,
        value_units=value_units,
        suffix=variable_suffix,
        running_range={name: [np.inf, -np.inf] for name in dynamic_names},
    )


def _update_running_range(bounds: list[float], data: np.ndarray) -> None:
    """Widens a running [min, max] tracker in place to also cover `data`'s finite values.

    Used by the tiled writer (see TiledBandWriter.running_range) to
    accumulate the true global min/max of a dynamic field across every
    tile's write_band_tile call, since no single tile ever holds the
    whole array to call .min()/.max() on directly.

    Args:
        bounds: Two-element [min, max] list, updated in place. Callers
            should initialize this to ``[np.inf, -np.inf]`` before the
            first call.
        data: Tile's worth of values (may contain NaN, which is ignored).

    Returns:
        None. `bounds` is mutated in place; left unchanged if `data` has
        no finite values.
    """
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return
    bounds[0] = min(bounds[0], float(finite.min()))
    bounds[1] = max(bounds[1], float(finite.max()))


def write_band_tile(
    writer: TiledBandWriter,
    tile,
    value: np.ndarray,
    satellite_id: np.ndarray,
    resolution_km: np.ndarray,
    tie_mask: np.ndarray | None,
    runner_up_satellite_id: np.ndarray | None,
    runner_up_resolution_km: np.ndarray | None,
    sza_tile: np.ndarray | None = None,
) -> None:
    """Writes one tile's already-finalized (post-selection, post-blend)
    result. `value` is the actual mosaic value for this tile (blended if
    blend_margin_km > 0, hard-cut winner otherwise -- same as
    build_mosaic_dataset's `value` param); the other arrays match
    select.SelectionResult's fields for this tile's rows only.

    Args:
        writer: The :class:`TiledBandWriter` returned by
            :func:`open_tiled_band_writer`.
        tile: The tile row-range being written (must expose `y0`/`y1`
            attributes, e.g. a ``tiling.TileRowRange``).
        value: This tile's mosaic value (post-selection, post-blend).
        satellite_id: This tile's primary source satellite id per pixel.
        resolution_km: This tile's winning satellite's actual per-pixel
            resolution.
        tie_mask: This tile's boolean mask of pixels actually blended,
            or None if no blending information applies.
        runner_up_satellite_id: This tile's second-place satellite id
            per pixel, or None.
        runner_up_resolution_km: This tile's runner-up resolution per
            pixel, or None.
        sza_tile: This tile's solar zenith angle in degrees, required if
            `writer.include_sza` is True.

    Returns:
        None. Writes into `writer`'s open datasets as a side effect.

    Raises:
        ValueError: If `writer.include_sza` is True but `sza_tile` is None.
    """
    is_blended, blend_weight_winner, runner_up_id_arr = compute_blend_accountability_fields(
        value.shape, resolution_km, tie_mask, runner_up_satellite_id, runner_up_resolution_km
    )
    y0, y1 = tile.y0, tile.y1

    s = writer.suffix
    writer.final_ds.variables[f"satellite_id{s}"][y0:y1, :] = satellite_id.astype(np.int8)
    writer.final_ds.variables[f"is_blended{s}"][y0:y1, :] = is_blended
    writer.final_ds.variables[f"runner_up_satellite_id{s}"][y0:y1, :] = runner_up_id_arr

    blend_weight_var = writer.final_ds.variables[f"blend_weight_winner{s}"]
    blend_weight_var[y0:y1, :] = _pack_finite(
        blend_weight_winner, blend_weight_var.scale_factor, blend_weight_var.add_offset, _FILL_INT16
    )

    writer.stage_ds.variables[f"value{s}"][y0:y1, :] = value.astype(np.float32)
    writer.stage_ds.variables[f"pixel_resolution_km{s}"][y0:y1, :] = resolution_km.astype(np.float32)
    _update_running_range(writer.running_range[f"value{s}"], value)
    _update_running_range(writer.running_range[f"pixel_resolution_km{s}"], resolution_km)

    if writer.include_sza:
        if sza_tile is None:
            raise ValueError("write_band_tile: include_sza=True at open time but sza_tile is None")
        is_daylit = (sza_tile < 90).astype(np.int8)
        writer.final_ds.variables["is_daylit"][y0:y1, :] = is_daylit
        writer.stage_ds.variables["solar_zenith_angle_deg"][y0:y1, :] = sza_tile.astype(np.float32)
        _update_running_range(writer.running_range["solar_zenith_angle_deg"], sza_tile)


_DYNAMIC_FIELD_ATTRS = {
    "pixel_resolution_km": {
        "long_name": "actual ground resolution at this pixel, accounting for view-zenith-angle "
        "degradation (see processing/resolution.py)",
        "units": "km",
    },
    "solar_zenith_angle_deg": {
        "long_name": "solar zenith angle -- <90 deg: sun above horizon (day), >=90 deg: night. "
        "Shared across every band in this tier (a property of ground point + time, not of which "
        "satellite observed it), unlike the per-band pixel_resolution_km field.",
        "units": "degrees",
    },
}


def finalize_tiled_band_writer(writer: TiledBandWriter, metas: list[SatelliteMeta]) -> None:
    """Pass 2: for value/pixel_resolution_km/[solar_zenith_angle_deg],
    computes the true int16 packing range from every tile's running
    (min, max) seen during write_band_tile, creates that packed variable
    in final_path, and re-reads the staged float32 data tile by tile
    (small, tile-sized reads -- never the full array) to write the
    packed result. Writes the small satellite-dimensioned metadata table
    via xarray's mode='a' append (appending a new-dimension xr.Dataset to
    a file netCDF4-python already created works correctly and doesn't
    disturb the existing variables) --
    reuses xarray's own well-tested datetime64 CF encoding rather than
    reimplementing it by hand for a handful of values. Closes and
    deletes the staging file.

    Args:
        writer: The :class:`TiledBandWriter` that every tile has already
            been written to via :func:`write_band_tile`.
        metas: Per-satellite metadata for every satellite contributing to
            this band, used for the per-satellite metadata table and
            `satellite_id`'s flag_values/flag_meanings attrs.

    Returns:
        None. Finishes writing and closes `writer.final_ds`, and closes
        and deletes `writer.stage_path`, as side effects.
    """
    for name, bounds in writer.running_range.items():
        # `name` is already the on-disk variable name (suffixed for
        # value/pixel_resolution_km, unsuffixed for solar_zenith_angle_deg
        # -- see open_tiled_band_writer's dynamic_names construction).
        # Strip the suffix only to look up the RIGHT attrs template below;
        # solar_zenith_angle_deg has no suffix to strip, so this is a
        # no-op for it.
        base_name = name[: len(name) - len(writer.suffix)] if writer.suffix and name.endswith(writer.suffix) else name

        vmin, vmax = bounds
        if not np.isfinite(vmin) or not np.isfinite(vmax):
            # nothing valid this run (e.g. zero overlap) -- arbitrary but
            # harmless, matches _compute_scale_offset's same fallback.
            scale_factor, add_offset = 1.0, 0.0
        else:
            scale_factor, add_offset = _scale_offset_from_range(vmin, vmax)

        attrs = (
            {"long_name": writer.value_long_name, "units": writer.value_units}
            if base_name == "value"
            else _DYNAMIC_FIELD_ATTRS[base_name]
        )

        var = writer.final_ds.createVariable(
            name, "i2", ("y", "x"), zlib=True, complevel=_COMPRESSION_LEVEL, fill_value=_FILL_INT16
        )
        var.set_auto_maskandscale(False)  # see _pack_finite's docstring
        var.long_name = attrs["long_name"]
        if "units" in attrs:
            var.units = attrs["units"]
        var.grid_mapping = "crs"
        var.coordinates = "lat lon"

        stage_var = writer.stage_ds.variables[name]
        for tile in writer.tiles:
            y0, y1 = tile.y0, tile.y1
            var[y0:y1, :] = _pack_finite(np.asarray(stage_var[y0:y1, :]), scale_factor, add_offset, _FILL_INT16)
        var.scale_factor = scale_factor
        var.add_offset = add_offset

    sat_id_var = writer.final_ds.variables[f"satellite_id{writer.suffix}"]
    sat_id_var.flag_values = np.array([0] + [m.satellite_id for m in metas], dtype=np.int8)
    sat_id_var.flag_meanings = "no_data " + " ".join(m.satellite_key.replace("-", "_") for m in metas)

    for attr_name, attr_value in _srf_correction_attrs(metas).items():
        setattr(writer.final_ds, attr_name, attr_value)

    writer.final_ds.close()
    writer.stage_ds.close()
    writer.stage_path.unlink()

    _append_satellite_metadata_table(writer.final_path, metas, writer.suffix)


def _append_satellite_metadata_table(final_path: Path, metas: list[SatelliteMeta], suffix: str) -> None:
    """Same field list/attrs as build_mosaic_dataset's tail section
    (satellite_longitude/scan_start/scan_end/scan_mid/spatial_resolution_km/
    band_wavelength_um/temporal_resolution_minutes/source_filename/
    satellite_key, plus the `satellite` and `time` coords) -- built as a
    small standalone xr.Dataset (independent of grid size, no reason to
    route it through the tiled writer at all) and appended to the
    already-tile-written final_path. `time` is the median scan mid-time
    across metas, same as the full-grid path -- see _median_datetime's
    own docstring for why median over mean.

    `suffix` matches build_tier_dataset's own rename_map, which renames
    EVERY coord except lat/lon (so "satellite"/"time" become
    "satellite_vis"/"time_vis", not left shared) -- this table is
    genuinely per-band (which satellites/scan-times contributed differs
    per band, a satellite can have a gap in one channel and not another),
    unlike solar_zenith_angle_deg/is_daylit above.

    Args:
        final_path: Path of the already-tile-written final output file
            (from :func:`open_tiled_band_writer`/:func:`finalize_tiled_band_writer`)
            to append this table to.
        metas: Per-satellite metadata for every satellite contributing to
            this band.
        suffix: Band-name variable suffix (e.g. ``"_vis"``) applied to
            every variable/coord in this table.

    Returns:
        None. Appends the metadata table to `final_path` (mode="a") as
        a side effect.
    """
    sat_dim = f"satellite{suffix}"
    ds = xr.Dataset(
        coords={
            sat_dim: (sat_dim, [m.satellite_id for m in metas]),
            f"time{suffix}": (
                (),
                np.datetime64(_median_datetime([m.scan_mid for m in metas])),
                {
                    "standard_name": "time",
                    "long_name": "median scan mid-time across contributing satellites -- the "
                    "mosaic's authoritative timestamp (not the requested target_time attribute, "
                    "which is what was asked for; this is what the data actually reflects). "
                    "Chosen over the mean specifically for robustness to one oddly-timed "
                    "satellite (e.g. INSAT's ~30 min cadence vs. the ~10-15 min of the others) "
                    "skewing the average.",
                },
            ),
        }
    )
    ds[f"satellite_longitude{suffix}"] = (sat_dim, [m.longitude_deg for m in metas], {"units": "degrees_east"})
    ds[f"satellite_scan_start{suffix}"] = (sat_dim, np.array([m.scan_start for m in metas], dtype="datetime64[ns]"))
    ds[f"satellite_scan_end{suffix}"] = (sat_dim, np.array([m.scan_end for m in metas], dtype="datetime64[ns]"))
    ds[f"satellite_scan_mid{suffix}"] = (
        sat_dim,
        np.array([m.scan_mid for m in metas], dtype="datetime64[ns]"),
        {
            "long_name": "scan mid-time -- (scan_start + scan_end) / 2, used by select.py to compare "
            "each satellite's real synchronization to the target time. Stored explicitly rather than "
            "requiring a consumer to recompute it from scan_start/scan_end.",
        },
    )
    ds[f"satellite_spatial_resolution_km{suffix}"] = (
        sat_dim,
        [m.nominal_spatial_resolution_km for m in metas],
        {"long_name": "nominal nadir spatial resolution (see pixel_resolution_km for the actual per-pixel value)"},
    )
    ds[f"satellite_band_wavelength_um{suffix}"] = (
        sat_dim,
        [m.band_center_wavelength_um for m in metas],
        {"units": "micrometer", "long_name": "band center wavelength used for this mosaic"},
    )
    ds[f"satellite_temporal_resolution_minutes{suffix}"] = (
        sat_dim,
        [m.temporal_resolution_minutes for m in metas],
        {"long_name": "full-disk repeat cadence"},
    )
    ds[f"satellite_source_filename{suffix}"] = (sat_dim, [m.source_filename for m in metas])
    ds[f"satellite_key{suffix}"] = (sat_dim, [m.satellite_key for m in metas])
    ds[f"satellite_srf_correction_gain{suffix}"] = (
        sat_dim,
        [m.srf_correction_gain for m in metas],
        {
            "long_name": "multiplicative SRF/calibration correction applied to this satellite's raw "
            "value before selection/blending for this band; 1.0 = no correction (identity). "
            "value_corrected = value_raw * gain + offset. See satellite_srf_correction_method and "
            "satellites/srf_correction.py.",
        },
    )
    ds[f"satellite_srf_correction_offset{suffix}"] = (
        sat_dim,
        [m.srf_correction_offset for m in metas],
        {
            "long_name": "additive SRF/calibration correction applied to this satellite's raw value "
            "before selection/blending for this band, in the same physical units as value; "
            "0.0 = no correction (identity).",
        },
    )
    ds[f"satellite_srf_correction_method{suffix}"] = (
        sat_dim,
        [m.srf_correction_method for m in metas],
        {
            "long_name": "how gain/offset were derived: 'identity' (no correction), "
            "'srf-weighted-radiative-transfer', or 'empirical-overlap-bias'. See "
            "satellites/srf_correction.py.",
        },
    )

    encoding = {
        f"{base}{suffix}": {"zlib": True, "complevel": _COMPRESSION_LEVEL}
        for base in (
            "satellite_longitude",
            "satellite_spatial_resolution_km",
            "satellite_band_wavelength_um",
            "satellite_temporal_resolution_minutes",
            "satellite_srf_correction_gain",
            "satellite_srf_correction_offset",
        )
    }
    # sat_dim (the satellite id coordinate) and temporal_resolution_minutes
    # are built from plain Python ints above, which xarray/netCDF4 default
    # to int64 ("long") on write -- DAP2/OPeNDAP can't serve that type
    # (raises "NcDDS Variable data type = long", seen 2026-08-07).
    # Both are always tiny (satellite_id/cadence-in-minutes never
    # approach int32's range, let alone int64's), so int32 loses nothing.
    encoding[sat_dim] = {"dtype": "int32"}
    encoding[f"satellite_temporal_resolution_minutes{suffix}"]["dtype"] = "int32"
    ds.to_netcdf(str(final_path), mode="a", encoding=encoding)


@dataclass
class BandMosaicInputs:
    """Everything build_mosaic_dataset needs for one band within a Phase 2
    resolution-tier file (see satellites/band_registry.py for the 6 bands
    and their tier grouping). `band_name` (e.g. "MIR") becomes this band's
    variable suffix (lowercased) in the merged tier Dataset -- value_mir,
    satellite_id_mir, pixel_resolution_km_mir, etc.

    Attributes:
        band_name: Band name, e.g. ``"MIR"``; lowercased to form this
            band's variable suffix in the merged tier Dataset.
        value: Selected/blended mosaic value for this band, on the
            common grid.
        satellite_id: Primary source satellite id per pixel for this band.
        resolution_km: Actual per-pixel resolution of the winning
            satellite for this band.
        metas: Per-satellite metadata for every satellite contributing to
            this band.
        value_long_name: `long_name` attribute for this band's `value`
            variable.
        value_units: `units` attribute for this band's `value` variable.
        tie_mask: Boolean mask of pixels actually blended, or None if
            this band's value was never run through feather_seams.
        runner_up_satellite_id: Second-place satellite id per pixel, or
            None.
        runner_up_resolution_km: Runner-up satellite's per-pixel actual
            resolution, or None.
    """

    band_name: str
    value: xr.DataArray
    satellite_id: xr.DataArray
    resolution_km: xr.DataArray
    metas: list[SatelliteMeta]
    value_long_name: str
    value_units: str
    tie_mask: xr.DataArray | None = None
    runner_up_satellite_id: xr.DataArray | None = None
    runner_up_resolution_km: xr.DataArray | None = None


def build_tier_dataset(
    tier_name: str,
    band_inputs: list[BandMosaicInputs],
    target_time: dt.datetime,
    solar_zenith_angle_deg: xr.DataArray | None = None,
) -> xr.Dataset:
    """Assembles a Phase 2 resolution-tier Dataset holding multiple bands
    that share one common grid (see reproject.py's TIER_A_RESOLUTION_KM /
    TIER_B_RESOLUTION_KM) as separate band-suffixed variables, rather than
    a stacked band dimension -- matches how xcube expects distinct
    queryable variables per cube (e.g. Sentinel-2 exposing B02/B03/B04
    separately), and lets each band keep its own independent satellite_id/
    accountability fields, since satellite selection genuinely differs per
    band (a satellite can have a gap in one channel and not another).

    Reuses build_mosaic_dataset unchanged for each band (so the well-tested
    packing/accountability/median-time logic isn't duplicated), then
    renames that band's variables/coords with its suffix and merges. `lat`/
    `lon`/`crs` are deliberately left unsuffixed and shared -- identical
    across every band in a tier by construction (same target grid), so
    xr.merge deduplicates them rather than storing N redundant copies.

    Pass `solar_zenith_angle_deg` for Tier B (VIS/SWIR) -- computed once
    per run on the common grid (see processing/illumination.py: day/night
    is a property of the ground point + time, not of which satellite is
    looking, so it's shared across every band in the tier rather than
    per-band like pixel_resolution_km). Stores both the continuous SZA
    value and a derived `is_daylit` convenience flag, so a consumer can
    tell "satellite_id=0 because it's night" apart from "genuinely no
    satellite ever covered this pixel" -- satellite_id alone can't
    distinguish those (the VIS day/night masking
    design is grounded in real GOES-18 C05 data:
    satpy does NOT mask night as NaN, it returns near-zero noise,
    so this distinction needs its own explicit field).

    Args:
        tier_name: Tier name used in the dataset `title` attribute, e.g.
            ``"Tier A"``.
        band_inputs: One :class:`BandMosaicInputs` per band to include in
            this tier, sharing a common grid.
        target_time: The originally requested mosaic timestamp, recorded
            as the `requested_target_time` attribute.
        solar_zenith_angle_deg: Solar zenith angle on the common grid,
            for Tier B (VIS/SWIR); omit for tiers with no day/night
            dependence.

    Returns:
        A merged `xr.Dataset` with each band's variables suffixed by its
        lowercased `band_name`, shared unsuffixed `lat`/`lon`/`crs`, and
        (if `solar_zenith_angle_deg` was given) shared unsuffixed
        `solar_zenith_angle_deg`/`is_daylit` fields -- ready to be handed
        to write_tier_netcdf for packed/compressed writing.
    """
    per_band_datasets = []
    band_names = []
    for b in band_inputs:
        ds = build_mosaic_dataset(
            b.value,
            b.satellite_id,
            b.resolution_km,
            b.metas,
            target_time,
            b.band_name,
            value_long_name=b.value_long_name,
            value_units=b.value_units,
            tie_mask=b.tie_mask,
            runner_up_satellite_id=b.runner_up_satellite_id,
            runner_up_resolution_km=b.runner_up_resolution_km,
        )
        suffix = b.band_name.lower()
        rename_map = {name: f"{name}_{suffix}" for name in ds.data_vars if name != "crs"}
        rename_map.update({name: f"{name}_{suffix}" for name in ds.coords if name not in ("lat", "lon")})
        ds = ds.rename(rename_map)
        ds.attrs = {}  # tier-level attrs are set once below, not per band
        per_band_datasets.append(ds)
        band_names.append(b.band_name)

    merged = xr.merge(per_band_datasets, join="exact", compat="equals")

    if solar_zenith_angle_deg is not None:
        from global_mosaic.processing.illumination import is_daylit as _is_daylit

        merged["solar_zenith_angle_deg"] = (
            solar_zenith_angle_deg.dims,
            solar_zenith_angle_deg.values,
            {
                "long_name": "solar zenith angle -- <90 deg: sun above horizon (day), "
                ">=90 deg: night. Shared across every band in this tier (a property of "
                "ground point + time, not of which satellite observed it), unlike the "
                "per-band pixel_resolution_km_<band> fields.",
                "units": "degrees",
                "grid_mapping": "crs",
                "coordinates": "lat lon",
            },
        )
        merged["is_daylit"] = (
            solar_zenith_angle_deg.dims,
            _is_daylit(solar_zenith_angle_deg.values).astype(np.int8),
            {
                "long_name": "1 where solar_zenith_angle_deg < 90 (day), 0 where night. "
                "Distinguishes satellite_id_<band>=0 caused by nighttime (VIS/SWIR have no "
                "reflected-sunlight signal to measure) from satellite_id_<band>=0 caused by "
                "a genuine coverage gap -- satellite_id alone cannot tell these apart.",
                "grid_mapping": "crs",
                "coordinates": "lat lon",
            },
        )

    all_metas = [m for b in band_inputs for m in b.metas]
    merged.attrs = {
        "Conventions": "CF-1.8",
        "title": f"Global geostationary mosaic -- {tier_name} ({', '.join(band_names)})",
        "history": f"Generated {dt.datetime.now(dt.timezone.utc).isoformat()} by global_mosaic.processing.mosaic_io",
        "requested_target_time": target_time.isoformat(),
        "references": "See global_mosaic.satellites.sat_metadata for per-satellite technical specifications.",
        **_srf_correction_attrs(all_metas),
    }
    return merged


def _matches(name: str, base: str) -> bool:
    """Checks whether `name` is `base` itself or a band-suffixed variant of it.

    Args:
        name: Variable name to test, e.g. ``"value_mir"``.
        base: Unsuffixed base name to match against, e.g. ``"value"``.

    Returns:
        True if `name` equals `base` exactly, or equals ``f"{base}_<suffix>"``.
    """
    return name == base or name.startswith(base + "_")


def write_tier_netcdf(path: str, ds: xr.Dataset) -> None:
    """Like write_mosaic_netcdf, generalized for build_tier_dataset's
    band-suffixed variable names (value_mir, pixel_resolution_km_wv, ...)
    via prefix matching instead of a fixed name list, since the set of
    variables depends on which bands went into this tier.

    Args:
        path: Destination NetCDF file path.
        ds: Dataset built by build_tier_dataset (or with the same
            band-suffixed variable naming convention).

    Returns:
        None. Writes the file at `path` as a side effect.
    """
    encoding: dict[str, dict] = {}

    for name in ds.data_vars:
        if name == "crs":
            continue
        if _matches(name, "value") or _matches(name, "pixel_resolution_km") or name == "solar_zenith_angle_deg":
            scale_factor, add_offset = _compute_scale_offset(ds[name])
            encoding[name] = {**_packed_encoding(), "scale_factor": scale_factor, "add_offset": add_offset}
        elif _matches(name, "blend_weight_winner"):
            encoding[name] = {**_packed_encoding(), "scale_factor": 1.0 / 32767, "add_offset": 0.5}
        else:
            encoding[name] = {"zlib": True, "complevel": _COMPRESSION_LEVEL}

    for name in ("lat", "lon"):
        encoding[name] = {"zlib": True, "complevel": _COMPRESSION_LEVEL}

    ds.to_netcdf(path, engine="netcdf4", encoding=encoding)
