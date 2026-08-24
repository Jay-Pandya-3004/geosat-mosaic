#!/usr/bin/env python
"""Phase 2: build one resolution-tier NetCDF mosaic (multiple bands
sharing one grid) for one target timestamp, across all 6 satellite slots.

Generalizes run_phase0.py (which only ever handled the single IR-window
band on a 4km INSAT-anchored grid) to Phase 2's locked design: two
resolution tiers instead of one grid, one file per tier holding several
bands as separate band-suffixed variables (see mosaic_io.build_tier_dataset
and satellites/band_registry.py), correct per-band calibration
(brightness_temperature for MIR/WV/TIR1/TIR2, reflectance for VIS,
explicit radiance for SWIR -- see band_registry.py's docstring for why
SWIR can't just use satpy's default), and VIS/SWIR day/night masking
(processing/illumination.py) since satpy does not mask night as NaN on
its own.

Per-slot acquisition/reprojection/resolution logic is otherwise the same
shape as run_phase0.py's build_candidate -- see that script for the
original single-band version this was generalized from.

Usage:
    python scripts/run_phase2_tier.py A "2026-07-30T20:06:00"
    python scripts/run_phase2_tier.py B "2026-07-30T20:06:00" --blend-margin-km 1.0
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC_DIR))

# See run_phase0.py's identical line for why this must be set before satpy
# is imported anywhere -- makes satpy prefer this project's patched INSAT
# reader over its own stock copy.
os.environ.setdefault("SATPY_CONFIG_PATH", str(_SRC_DIR / "global_mosaic"))

import netCDF4  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402
from satpy import Scene  # noqa: E402

from global_mosaic.acquisition import goes, himawari, insat, meteosat  # noqa: E402
from global_mosaic.config import ResolvedSlot, resolve_all_phase0_slots, resolve_phase0_slot  # noqa: E402
from global_mosaic.processing.blend import feather_seams  # noqa: E402
from global_mosaic.processing.illumination import solar_zenith_angle_deg  # noqa: E402
from global_mosaic.processing.illumination import mask_night  # noqa: E402
from global_mosaic.processing.midtime import get_scan_times  # noqa: E402
from global_mosaic.processing.mosaic_io import (  # noqa: E402
    BandMosaicInputs,
    SatelliteMeta,
    build_tier_dataset,
    finalize_tiled_band_writer,
    merge_tiled_band_files,
    open_tiled_band_writer,
    write_band_tile,
    write_tier_netcdf,
)
from global_mosaic.processing.preview import (  # noqa: E402
    BAND_PREVIEW_SPECS,
    compute_display_time,
    new_accumulator,
    render_band_preview,
)
from global_mosaic.processing.reproject import (  # noqa: E402
    TIER_A_RESOLUTION_KM,
    TIER_B_RESOLUTION_KM,
    _CACHE_DIR,
    _cached_lonlats,
    clear_in_process_caches,
    common_grid,
    downsample_if_finer_than_target,
    reproject_to_cropped_grid,
)
from global_mosaic.processing.resolution import (  # noqa: E402
    effective_resolution_km,
    geometry_from_orbital_parameters,
)
from global_mosaic.processing.select import (  # noqa: E402
    SATELLITE_ID,
    Candidate,
    SelectionAccumulator,
)
from global_mosaic.processing.tiling import DEFAULT_FIXED_OVERHEAD_GB, TileRowRange, compute_row_tiles  # noqa: E402
from global_mosaic.satellites.band_registry import get_band_spec, get_calibration  # noqa: E402
from global_mosaic.satellites.reader_registry import get_reader_spec  # noqa: E402
from global_mosaic.satellites.srf_correction import get_srf_correction  # noqa: E402
from global_mosaic.satellites.sat_metadata import Satellites_Objects  # noqa: E402

import ctypes  # noqa: E402
from ctypes import wintypes  # noqa: E402


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    """ctypes mirror of the Win32 ``PROCESS_MEMORY_COUNTERS`` struct, used
    via ``GetProcessMemoryInfo`` to self-measure this process's
    working-set memory (see ``_memory_snapshot_gb``).

    Attributes:
        cb: Size of this structure in bytes, required by the Win32 API
            contract (must be set to ``ctypes.sizeof(...)`` before the call).
        PageFaultCount: Number of page faults incurred by the process.
        PeakWorkingSetSize: Historical high-water-mark working-set size, in
            bytes -- a peak, not the current usage.
        WorkingSetSize: Current working-set size, in bytes.
        QuotaPeakPagedPoolUsage: Peak paged-pool usage quota, in bytes.
        QuotaPagedPoolUsage: Current paged-pool usage quota, in bytes.
        QuotaPeakNonPagedPoolUsage: Peak non-paged-pool usage quota, in bytes.
        QuotaNonPagedPoolUsage: Current non-paged-pool usage quota, in bytes.
        PagefileUsage: Current page-file (commit charge) usage, in bytes.
        PeakPagefileUsage: Peak page-file (commit charge) usage, in bytes.
    """

    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


# Explicit argtypes/restype are required -- without them ctypes silently
# truncates the 64-bit HANDLE, and GetProcessMemoryInfo fails or returns
# garbage (see project notes). External `Get-Process` polling is
# unreliable for tracking these processes in this environment (always
# reads 0 despite the process genuinely running) -- this in-process
# self-measurement is the only technique that actually works.
ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
    wintypes.DWORD,
]
ctypes.windll.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def _memory_snapshot_gb() -> tuple[float, float]:
    """Self-measures this process's Windows working-set memory.

    `PeakWorkingSetSize` is a historical high-water mark, not current usage
    -- this distinction matters because a per-tile
    diagnostic that only checked "current" missed a transient spike that
    only showed up as a jump in the peak.

    Returns:
        tuple[float, float]: ``(current_working_set_gb, peak_working_set_gb)``
        for this process. Both are ``nan`` if the underlying
        ``GetProcessMemoryInfo`` call fails.
    """
    counters = _PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
    if not ok:
        return (float("nan"), float("nan"))
    return (counters.WorkingSetSize / 1024**3, counters.PeakWorkingSetSize / 1024**3)


def _format_elapsed(seconds: float) -> str:
    """Formats an elapsed duration for ``--profile-time`` output.

    Plain `Ns` / `Mmin Ss` formatting -- wall-clock ``time.perf_counter()``
    deltas, not CPU time (this project's bottlenecks are a mix of
    network I/O, disk cache reads, and compute, so wall clock is what
    actually matters for a run).

    Args:
        seconds: Elapsed wall-clock duration in seconds.

    Returns:
        str: ``"12.3s"`` style for durations under a minute, otherwise
        ``"Mmin Ss"`` style (e.g. ``"2min 5.1s"``).
    """
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}min {rest:.1f}s"

DEST_DIR = Path(
    os.environ.get("GLOBAL_MOSAIC_DEST_DIR", str(Path(__file__).resolve().parents[1] / "data" / "raw"))
)
# Override via GLOBAL_MOSAIC_DEST_DIR to isolate raw downloads per run (e.g.
# concurrent multi-timestamp runs that each want to know exactly which raw
# files are theirs to delete afterward) -- an env var, not a CLI flag,
# specifically so it propagates automatically to the per-band/per-satellite
# subprocesses this script spawns (they inherit the parent's environment)
# without threading a new flag through 3 levels of subprocess.run calls.
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "mosaics"

SEARCH_WINDOW_MINUTES = 5  # +-5 minutes

METEOSAT_DATASET_BY_SLOT = {
    "meteosat_prime": "SEVIRI",
    "meteosat_iodc": "SEVIRI_IODC",
}

TIERS: dict[str, tuple[float, list[str]]] = {
    "A": (TIER_A_RESOLUTION_KM, ["MIR", "WV", "TIR1", "TIR2"]),
    "B": (TIER_B_RESOLUTION_KM, ["VIS", "SWIR"]),
}

PER_SATELLITE_ISOLATION_PIXEL_THRESHOLD = 300_000_000
"""Grids at or below this pixel count run their whole 6-satellite loop
in-process (Tier A, 148M px, stable); grids above it spawn one
subprocess per satellite instead (Tier B, ~593M px), same as main()'s
per-band isolation but one level deeper. Tier B runs still hit
MemoryError satellite-by-satellite even after footprint cropping and
streaming fold-and-discard selection landed (those fixes address
selection-time memory, not the per-satellite acquisition/reprojection
work -- full-resolution source files, satpy Scene/dask state -- that
accumulates within one band-subprocess's 6-satellite loop). A pixel-count
threshold rather than a hardcoded tier name so a future third tier is
judged by its own grid size."""

VALUE_UNITS_BY_BAND = {
    "MIR": "K",
    "WV": "K",
    "TIR1": "K",
    "TIR2": "K",
    "VIS": "%",
    # SWIR is deliberately left off this fixed table -- unlike the other
    # five bands, its calibration (radiance) is NOT unit-consistent across
    # satellites (each ground segment expresses radiance in its own
    # preferred scale, e.g. INSAT's mW.cm-2.sr-1.micron-1 vs ABI's W.m-2.
    # sr-1.um-1 -- a genuine difference, not just a units *label*
    # difference the way K/% are). build_candidate reads the
    # `data.attrs["units"]` off the calibrated array per satellite
    # instead, so this gap shows up as differing per-satellite units in
    # the written file rather than being silently papered over with one
    # hardcoded string -- cross-satellite SWIR unit reconciliation is
    # future work, not solved here.
}


def fetch_slot(slot_name: str, satellite_key: str, target: dt.datetime, band: str):
    """Acquires the raw source file(s) for one slot/satellite/band/target.

    Same dispatch as run_phase0.py's fetch_slot, generalized with a `band`
    parameter for GOES/Himawari (each ABI/AHI channel is a separate product
    file, see acquisition/goes.py and himawari.py's band params added for
    Phase 2). Meteosat/INSAT bundle every channel into one file already, so
    `band` doesn't change what they fetch.

    Args:
        slot_name: Logical slot name (e.g. ``"goes_east"``, ``"meteosat_prime"``),
            used only for log messages here.
        satellite_key: Satellite identifier (e.g. ``"goes-19"``,
            ``"himawari-09"``, ``"meteosat-10"``, ``"insat-3ds"``) that
            selects which acquisition dispatch branch runs.
        target: Target timestamp to search around.
        band: Logical band name (e.g. ``"MIR"``, ``"VIS"``). Only affects
            GOES/Himawari, which fetch one product file per channel.

    Returns:
        tuple | None: ``(path_or_paths, source_filename)`` on success, where
        ``path_or_paths`` is a single path (GOES/Meteosat/INSAT) or a list of
        paths (Himawari's per-segment files), and ``source_filename`` is a
        display name (``"+"``-joined for Himawari). ``None`` if no file was
        found within the search window, or if Meteosat has no dataset name
        registered for this slot.

    Raises:
        ValueError: If `satellite_key` doesn't match any known acquisition
            dispatch prefix (``goes-``, ``himawari-``, ``meteosat-``,
            ``insat-``).
    """
    if satellite_key.startswith("goes-"):
        satpy_band = get_band_spec(satellite_key, band).satpy_band_name
        path = goes.download(satellite_key, target, str(DEST_DIR), band=satpy_band)
        return (path, Path(path).name) if path else None

    if satellite_key.startswith("himawari-"):
        satpy_band = get_band_spec(satellite_key, band).satpy_band_name
        paths = himawari.download(satellite_key, target, str(DEST_DIR), band=satpy_band)
        return (paths, "+".join(Path(p).name for p in paths)) if paths else None

    if satellite_key.startswith("meteosat-"):
        dataset_name = METEOSAT_DATASET_BY_SLOT.get(slot_name)
        if dataset_name is None:
            return None
        # meteosat.download() has no retry/catch of its own (unlike
        # insat.download() below) -- a EUMETSAT auth/network failure (e.g.
        # the 403 Unauthorised seen repeatedly) would
        # otherwise propagate uncaught and crash this whole run instead of
        # just leaving this one satellite's slot empty, same as any other
        # "no file found" case.
        try:
            paths = meteosat.download(dataset_name, target, str(DEST_DIR), tolerance_minutes=SEARCH_WINDOW_MINUTES)
        except Exception as exc:
            print(f"  [{slot_name}] {satellite_key}: acquisition failed ({exc}), skipping")
            return None
        return (paths[0], Path(paths[0]).name) if paths else None

    if satellite_key.startswith("insat-"):
        try:
            path = insat.download(satellite_key, target, str(DEST_DIR))
        except NotImplementedError as exc:
            print(f"  [{slot_name}] {satellite_key}: {exc}")
            return None
        return (path, Path(path).name) if path else None

    raise ValueError(f"No acquisition dispatch for satellite key: {satellite_key}")


def _channel_wavelength_and_resolution(satellite_key: str, imager: dict, channel_no: int) -> tuple[float, float]:
    """Looks up a channel's nominal resolution and center wavelength.

    Identical to run_phase0.py's helper of the same name -- see there for
    the two schema-difference fixes this encodes (Meteosat's
    Channels-as-string aliasing for meteosat-10/11, and its nested
    Spatial_Resolution dict vs the other families' flat field).

    Args:
        satellite_key: Satellite identifier, used to fetch the meteosat-09
            fallback imager when `imager["Channels"]` is a string alias.
        imager: The satellite's instrument metadata dict (one entry from
            ``Satellites_Objects[satellite_key]["Instruments"]``).
        channel_no: Channel index into the imager's `Channels` collection.

    Returns:
        tuple[float, float]: ``(nominal_resolution_km, band_wavelength_um)``.
    """
    channels = imager["Channels"]
    if isinstance(channels, str):
        fallback_imager = next(iter(Satellites_Objects["meteosat-09"]["Instruments"].values()))
        channels = fallback_imager["Channels"]

    channel = channels[channel_no]
    band_wavelength_um = channel["Central_Wavelength_Um"]

    if "Spatial_Resolution_Km" in channel:
        nominal_resolution_km = channel["Spatial_Resolution_Km"]
    else:
        nominal_resolution_km = channel["Spatial_Resolution"]["Dataset_Resolution_Km"]

    return nominal_resolution_km, band_wavelength_um


@dataclass
class Phase1Result:
    """Everything about one satellite's contribution to one band that
    does NOT depend on which tile (or whether tiling is even happening)
    -- fetch, scan-time/sync-window check, Scene load/calibration,
    source-side downsampling, and geometry lookup. Kept alive across an
    entire band's tile loop and reused for every tile (see
    build_band_mosaic_tiled) rather than rebuilt per tile: recomputing
    it per tile would mean redundant network/disk I/O and Scene
    decode/calibrate work for no benefit (none of this depends on the
    tile), and worse, would let a transient fetch failure on only SOME
    tiles make one satellite silently present in part of the mosaic and
    absent elsewhere for reasons unrelated to coverage or sync.

    Attributes:
        data: Downsampled, pre-reprojection calibrated array for this
            satellite/band. `.attrs["area"]` / `.attrs["orbital_parameters"]`
            are intact (needed by the tile-specific reprojection step).
        geometry: Satellite viewing geometry derived from the data's orbital
            parameters (see `geometry_from_orbital_parameters`).
        max_viewing_angle_deg: This satellite's maximum usable viewing angle,
            used to crop the reprojected footprint per tile.
        nominal_resolution_km: Native/nominal spatial resolution of this
            band for this satellite, independent of the loaded data itself.
        meta: Packed `SatelliteMeta` describing this satellite's
            contribution (scan times, calibration/SRF-correction info, etc.)
            for eventual NetCDF metadata.
        value_units: Units string for the calibrated values (e.g. ``"K"``,
            ``"%"``, or a satellite-specific radiance unit for SWIR).
    """

    data: xr.DataArray  # downsampled, pre-reprojection; .attrs["area"]/.attrs["orbital_parameters"] intact
    geometry: object
    max_viewing_angle_deg: float
    nominal_resolution_km: float
    meta: SatelliteMeta
    value_units: str


def build_satellite_phase1(
    slot_name: str, satellite_key: str, target: dt.datetime, band: str, target_resolution_km: float
) -> Phase1Result | None:
    """Builds the target-area-independent half of one satellite's candidate.

    The target-area-independent half of what build_candidate used to do in
    one shot -- see Phase1Result's own docstring for why this is split out
    and computed once per satellite per band, not once per tile.

    Args:
        slot_name: Logical slot name (e.g. ``"goes_east"``), used for log
            messages.
        satellite_key: Satellite identifier (e.g. ``"goes-19"``).
        target: Target timestamp to acquire/sync against.
        band: Logical band name (e.g. ``"MIR"``, ``"VIS"``).
        target_resolution_km: The tier's target grid resolution, used to
            decide whether the loaded data needs source-side downsampling
            (see `downsample_if_finer_than_target`).

    Returns:
        Phase1Result | None: The built result, or `None` on any of the same
        skip conditions build_candidate always had (no file in the search
        window, no reader registered, scan mid-time outside the sync
        window).
    """
    acquired = fetch_slot(slot_name, satellite_key, target, band)
    if acquired is None:
        print(f"  [{slot_name}/{band}] {satellite_key}: no file found within the search window, skipping")
        return None
    paths, source_filename = acquired

    spec = get_reader_spec(satellite_key)
    if spec.reader is None:
        print(f"  [{slot_name}/{band}] {satellite_key}: no reader registered ({spec.note}), skipping")
        return None

    scan_times = get_scan_times(satellite_key, paths)
    gap_seconds = abs((scan_times.mid - target).total_seconds())
    if gap_seconds > SEARCH_WINDOW_MINUTES * 60:
        print(
            f"  [{slot_name}/{band}] {satellite_key}: real scan mid-time {scan_times.mid} is "
            f"{gap_seconds / 60:.1f} min from target -- outside the +-{SEARCH_WINDOW_MINUTES} "
            f"min search window (confidence: {scan_times.confidence}), skipping"
        )
        return None

    band_spec = get_band_spec(satellite_key, band)
    calibration = get_calibration(band)
    scn = Scene(filenames=paths if isinstance(paths, list) else [paths], reader=spec.reader)
    scn.load([band_spec.satpy_band_name], calibration=calibration)
    data = scn[band_spec.satpy_band_name]

    # SRF/calibration correction (see satellites/srf_correction.py) --
    # applied here, immediately after satpy's own calibration and before
    # this candidate is reprojected/selected/blended: select.py picks
    # winners by resolution alone (unaffected either way), but blend.py
    # mixes winner+runner-up values directly, so correcting each satellite
    # BEFORE that mix is the only way the blend is meaningful across two
    # differently-calibrated sources. get_srf_correction defaults to
    # identity for any (satellite_key, band) not yet computed, so this is a
    # no-op everywhere until coefficients land. Attrs (satpy's own
    # area/geolocation metadata, needed downstream by
    # reproject_to_cropped_grid) don't survive plain arithmetic on an
    # xr.DataArray -- restored explicitly rather than relying on
    # xr.set_options(keep_attrs=...) global state.
    correction = get_srf_correction(satellite_key, band)
    if correction.method != "identity":
        corrected = data * correction.gain + correction.offset
        corrected.attrs = data.attrs
        data = corrected

    # nominal_resolution_km only depends on satellite_key/band, not on the
    # loaded data. VIS is the case this matters for: GOES/Himawari's
    # native 0.5km on Tier B's 1km grid is a measured 21696x21696
    # (~470M pixel) source array -- see reproject.py's
    # downsample_if_finer_than_target docstring for the full rationale.
    imager = next(iter(Satellites_Objects[satellite_key]["Instruments"].values()))
    nominal_resolution_km, band_wavelength_um = _channel_wavelength_and_resolution(
        satellite_key, imager, band_spec.channel_no
    )
    temporal_resolution_minutes = imager["Full_Disk_Temporal_Resolution_Minutes"]
    data = downsample_if_finer_than_target(data, nominal_resolution_km, target_resolution_km)

    geometry = geometry_from_orbital_parameters(data.attrs["orbital_parameters"])
    max_viewing_angle_deg = Satellites_Objects[satellite_key]["Max_Viewing_Angle_Deg"]

    meta = SatelliteMeta(
        satellite_key=satellite_key,
        satellite_id=SATELLITE_ID[satellite_key],
        longitude_deg=geometry.sub_satellite_lon_deg,
        scan_start=scan_times.start,
        scan_end=scan_times.end,
        scan_mid=scan_times.mid,
        nominal_spatial_resolution_km=nominal_resolution_km,
        band_center_wavelength_um=band_wavelength_um,
        temporal_resolution_minutes=temporal_resolution_minutes,
        source_filename=source_filename,
        srf_correction_gain=correction.gain,
        srf_correction_offset=correction.offset,
        srf_correction_method=correction.method,
    )
    value_units = VALUE_UNITS_BY_BAND.get(band) or data.attrs.get("units", "unknown")
    print(
        f"  [{slot_name}/{band}] {satellite_key}: Phase 1 OK, real mid-time {scan_times.mid} "
        f"(gap {gap_seconds:.0f}s, confidence {scan_times.confidence}, units {value_units})"
    )
    return Phase1Result(
        data=data,
        geometry=geometry,
        max_viewing_angle_deg=max_viewing_angle_deg,
        nominal_resolution_km=nominal_resolution_km,
        meta=meta,
        value_units=value_units,
    )


def build_candidate_for_tile(phase1: Phase1Result, target_area, band: str, sza_grid=None) -> Candidate:
    """Builds the tile-specific half of one satellite's candidate.

    Reproject onto whichever target_area is passed in (the full grid for
    the non-tiled path, or one tile's own sliced sub-area for the tiled path
    -- reproject_to_cropped_grid has no full-grid assumption either way),
    then night-mask + compute actual per-pixel resolution.

    Unlike build_satellite_phase1, this has no skip condition of its own --
    a failure here (e.g. a resource spike) is meant to propagate to the
    caller's try/except, matching build_candidate's original error-handling
    shape (one bad slot/band shouldn't kill the whole run, but that's the
    caller's job to catch, not this function's to swallow).

    Args:
        phase1: The satellite/band's target-area-independent Phase1Result
            (see build_satellite_phase1).
        target_area: The pyresample area to reproject onto -- the full grid,
            or one tile's sliced sub-area.
        band: Logical band name; VIS/SWIR get night-masked when `sza_grid`
            is given.
        sza_grid: Solar zenith angle grid aligned to the same rows as
            `target_area`, used to night-mask VIS/SWIR. `None` skips
            night-masking (e.g. non-reflective bands).

    Returns:
        Candidate: The reprojected, (optionally) night-masked, float32
        candidate with its per-pixel resolution array attached.
    """
    reprojected = reproject_to_cropped_grid(
        phase1.data, target_area, phase1.geometry.sub_satellite_lon_deg, phase1.max_viewing_angle_deg
    )
    crop_columns = reprojected.attrs["crop_columns"]

    if band in ("VIS", "SWIR") and sza_grid is not None:
        # mask_night wants a plain ndarray, not an xr.DataArray -- index
        # the underlying values, not the DataArray itself, to avoid any
        # ambiguity between xarray's label-based and positional indexing.
        reprojected = mask_night(reprojected, sza_grid.values[:, crop_columns])

    resolution_km = effective_resolution_km(
        reprojected.coords["lat"].values,
        reprojected.coords["lon"].values,
        phase1.geometry,
        phase1.nominal_resolution_km,
    )
    # float32, not resolution_km's native float64 (inherited from
    # pyresample's lat/lon coordinate arrays -- satpy's own calibrated
    # value data is already float32, see satpy Scene.load's dtype).
    # select_winners stacks one full-grid array per candidate
    # (data_stack + res_stack), and on Tier A's 2km grid (148M pixels)
    # with all 6 slots filled, that's ~14GB in float64 vs ~7GB in
    # float32 -- the difference between a MemoryError and a successful
    # run on this machine. Kelvin/km values don't need double precision
    # (float32 keeps ~7 significant digits, vastly more than this
    # data's actual accuracy). Only scoped to this Phase 2 driver, not
    # resolution.py/select.py themselves -- Phase 0's run_phase0.py is
    # untouched.
    resolution_da = xr.DataArray(
        resolution_km.astype(np.float32), coords=reprojected.coords, dims=reprojected.dims
    )
    reprojected = reprojected.astype(np.float32)

    return Candidate(
        phase1.meta.satellite_key,
        reprojected,
        resolution_da,
        phase1.meta.scan_mid,
        phase1.meta.source_filename,
        columns=crop_columns,
    )


def build_candidate(slot_name, satellite_key, target, target_area, band: str, sza_grid=None, target_resolution_km: float = 2.0):
    """Public contract unchanged from before the Phase1/tile split above
    -- a thin wrapper so Phase 0 and any other existing caller keeps
    working exactly as before. New callers that process more than one
    tile per satellite (build_band_mosaic_tiled) should call
    build_satellite_phase1 once and build_candidate_for_tile per tile
    directly instead of this wrapper, to actually get the point of the
    split (not recomputing Phase 1 for every tile)."""
    phase1 = build_satellite_phase1(slot_name, satellite_key, target, band, target_resolution_km)
    if phase1 is None:
        return None
    candidate = build_candidate_for_tile(phase1, target_area, band, sza_grid)
    return candidate, phase1.meta, phase1.value_units


def _write_candidate_npz(candidate: Candidate, meta: SatelliteMeta, value_units: str, out_path: Path) -> None:
    """Packs one satellite's built Candidate + SatelliteMeta into a .npz
    for the parent band-process to read back (see build_band_mosaic's
    isolate_satellites path). Deliberately UNCOMPRESSED (np.savez, not
    savez_compressed): "only the cropped footprint, not the full grid" is
    still hundreds of millions of pixels on Tier B (~45% of a 593M-pixel
    grid per satellite, 2 float32 arrays), and measurement showed
    zlib-compressing that -- ~1GB per candidate -- was itself the
    dominant per-satellite cost once isolation moved this handoff onto
    disk, worse than the compute it was meant to protect. Plenty of room
    on D:\\global_mosaic_cache's drive (376GB free) to trade disk space
    for the write/read speed this handoff is on the critical path for.

    Args:
        candidate: The built candidate (already reprojected/cropped/
            night-masked) to pack.
        meta: The candidate's satellite metadata.
        value_units: Units string for the candidate's values.
        out_path: Destination ``.npz`` path.

    Returns:
        None. Writes `out_path` as a side effect.
    """
    np.savez(
        out_path,
        value=candidate.data.values,
        resolution_km=candidate.resolution_km.values,
        columns=candidate.columns,
        satellite_key=candidate.satellite_key,
        mid_time=candidate.mid_time.isoformat(),
        source_filename=candidate.source_filename,
        satellite_id=meta.satellite_id,
        longitude_deg=meta.longitude_deg,
        scan_start=meta.scan_start.isoformat(),
        scan_end=meta.scan_end.isoformat(),
        scan_mid=meta.scan_mid.isoformat(),
        nominal_spatial_resolution_km=meta.nominal_spatial_resolution_km,
        band_center_wavelength_um=meta.band_center_wavelength_um,
        temporal_resolution_minutes=meta.temporal_resolution_minutes,
        srf_correction_gain=meta.srf_correction_gain,
        srf_correction_offset=meta.srf_correction_offset,
        srf_correction_method=meta.srf_correction_method,
        value_units=value_units,
    )


def _read_candidate_npz(path: Path) -> tuple[Candidate, SatelliteMeta, str]:
    """Inverse of _write_candidate_npz. The reconstructed Candidate's
    data/resolution_km are coordinate-less DataArrays -- fine because
    SelectionAccumulator.update() (the only consumer downstream of this)
    only ever reads .values/.columns/.satellite_key, never coords.

    Args:
        path: Path to a ``.npz`` file written by :func:`_write_candidate_npz`.

    Returns:
        Tuple of ``(candidate, meta, value_units)``, reconstructed from
        the packed file.
    """
    with np.load(path, allow_pickle=False) as npz:
        satellite_key = str(npz["satellite_key"])
        dims = ("y", "x")
        candidate = Candidate(
            satellite_key=satellite_key,
            data=xr.DataArray(npz["value"], dims=dims),
            resolution_km=xr.DataArray(npz["resolution_km"], dims=dims),
            mid_time=dt.datetime.fromisoformat(str(npz["mid_time"])),
            source_filename=str(npz["source_filename"]),
            columns=npz["columns"],
        )
        meta = SatelliteMeta(
            satellite_key=satellite_key,
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
        value_units = str(npz["value_units"])
    return candidate, meta, value_units


def run_single_satellite_candidate(
    tier: str, band: str, slot_name: str, satellite_key: str, target: dt.datetime, out_path: Path
) -> bool:
    """Builds ONE satellite's candidate for one band and writes it to a
    small .npz (see _write_candidate_npz) instead of returning it
    in-process. This is what build_band_mosaic invokes as a subprocess per
    satellite once a tier's grid crosses ``PER_SATELLITE_ISOLATION_PIXEL_THRESHOLD``
    -- same "a subprocess exiting is a hard OS-level
    memory-reclaim guarantee" reasoning main()'s per-band isolation
    already relies on, one level deeper.

    Args:
        tier: Tier name, ``"A"`` or ``"B"`` (selects the grid resolution).
        band: Logical band name, e.g. ``"MIR"``.
        slot_name: Logical slot name, e.g. ``"goes_east"``.
        satellite_key: Satellite key resolved for this slot.
        target: Target timestamp to acquire/sync against.
        out_path: Destination ``.npz`` path for the built candidate.

    Returns:
        True if a candidate was built and written to `out_path`; False
        if this satellite had no usable data, mirroring build_candidate's
        own None-on-miss contract.
    """
    resolution_km, _ = TIERS[tier]
    target_area = common_grid(resolution_km=resolution_km)
    # _build_sza_grid disk-caches this (see its own docstring) -- the
    # first satellite to reach this line for a given (grid, target_time)
    # pays the trig cost once, every subsequent satellite (including
    # ones running concurrently, see MAX_CONCURRENT_SATELLITE_SUBPROCESSES)
    # just loads it back.
    sza_grid = _build_sza_grid(target_area, target) if band in ("VIS", "SWIR") else None

    try:
        result = build_candidate(
            slot_name, satellite_key, target, target_area, band, sza_grid, target_resolution_km=resolution_km
        )
    except NotImplementedError as exc:
        print(f"  [{slot_name}/{band}] {satellite_key}: SKIPPED: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001 -- mirrors build_band_mosaic's own per-slot catch, now one process removed from it
        print(f"  [{slot_name}/{band}] {satellite_key}: FAILED: {exc!r}")
        return False
    if result is None:
        return False

    candidate, meta, value_units = result
    _write_candidate_npz(candidate, meta, value_units, out_path)
    print(f"  [{slot_name}/{band}] {satellite_key}: wrote candidate ({out_path.stat().st_size / 1024:.1f} KB)")
    return True


MAX_CONCURRENT_SATELLITE_SUBPROCESSES = 1
"""Bounds how many --single-satellite subprocesses run at once when
isolate_satellites is True (see build_band_mosaic). Kept at 1 (fully
sequential) based on measurement, not the ~6.4GB figure this constant
originally shipped with: an initial attempt at 2 concurrent subprocesses
hit a MemoryError, and a follow-up per-satellite peak-memory measurement
(ctypes GetProcessMemoryInfo from inside each candidate build, since
external Get-Process polling is unreliable for tracking these
subprocesses in this environment) found EVERY satellite -- goes-18,
himawari-09, meteosat-10, insat-3ds alike -- peaks at essentially the
same ~26.9GB working set on Tier B's grid, regardless of native source
resolution. On a 32GB machine, one candidate alone already uses ~84% of
total RAM; there is no pairing (heavy+light or otherwise) that makes 2
concurrent safe, since there turned out to be no "light" satellite at
this grid scale -- the dominant cost is shared full-grid machinery
(lonlats, SZA, KD-tree application), not satellite-specific source
size. Revisit only after either (a) RAM headroom increases, or (b) a
future pass reduces this ~26.9GB per-candidate footprint itself (worth
its own investigation -- it's higher than the sum of the individually-
cached artifacts tracked, so something in the per-candidate path is
using more memory than expected; not chased further).

Distinct from the `nprocs` pyresample-internal-parallelism regression
already documented in reproject.py: that was fine-grained multiprocessing
INSIDE one KD-tree search, re-importing the entire calling process per
worker. This constant governs coarse-grained concurrency across already-
independent subprocesses instead -- the mechanism that made it unsafe
here was per-candidate memory footprint, not import-multiplication."""


def _run_satellite_subprocess(
    tier: str, target: dt.datetime, band: str, slot_name: str, satellite_key: str, tmp_path: Path
) -> bool:
    """Runs one --single-satellite subprocess to completion and returns
    whether it produced a usable candidate file. Called from a thread
    pool (see build_band_mosaic's isolate_satellites path) -- safe to run
    concurrently since it touches no shared mutable state of its own;
    subprocess.run already releases the GIL while the child process
    runs, so this is OS-level parallelism, not GIL-bound.

    Args:
        tier: Tier name, ``"A"`` or ``"B"``.
        target: Target timestamp to pass through to the subprocess.
        band: Logical band name, e.g. ``"MIR"``.
        slot_name: Logical slot name, e.g. ``"goes_east"``.
        satellite_key: Satellite key for this slot.
        tmp_path: Output ``.npz`` path the subprocess is asked to write.

    Returns:
        True if the subprocess exited 0 and `tmp_path` now exists; False
        otherwise.
    """
    proc = subprocess.run(
        [
            sys.executable,
            __file__,
            tier,
            target.isoformat(),
            "--single-band",
            band,
            "--single-satellite",
            "--slot-name",
            slot_name,
            "--satellite-key",
            satellite_key,
            "--out",
            str(tmp_path),
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    return proc.returncode == 0 and tmp_path.exists()


def build_band_mosaic(
    band: str,
    target: dt.datetime,
    target_area,
    blend_margin_km: float,
    sza_grid=None,
    target_resolution_km: float = 2.0,
    tier: str | None = None,
    isolate_satellites: bool = False,
):
    """Runs the full acquire -> reproject -> select -> (optionally blend)
    pipeline for one band across all 6 slots. Returns a BandMosaicInputs
    ready for build_tier_dataset, or None if nothing usable was found for
    this band at all.

    `isolate_satellites` (see PER_SATELLITE_ISOLATION_PIXEL_THRESHOLD)
    runs each satellite's candidate-building in its own subprocess
    (run_single_satellite_candidate) instead of calling build_candidate
    directly here, reading back just the small packed result. `tier` is
    only needed to re-invoke this script as that subprocess -- required
    whenever isolate_satellites is True.

    Args:
        band: Logical band name, e.g. ``"MIR"``.
        target: Target timestamp to build this band for.
        target_area: The common target grid's ``AreaDefinition``.
        blend_margin_km: Seam-feathering margin in km; 0.0 disables
            blending.
        sza_grid: Solar zenith angle grid for night-masking VIS/SWIR, or
            None for bands that don't need it.
        target_resolution_km: The tier's target grid resolution, in km.
        tier: Tier name (``"A"``/``"B"``), required when
            `isolate_satellites` is True (needed to re-invoke this script).
        isolate_satellites: Whether to build each satellite's candidate
            in its own subprocess instead of in-process.

    Returns:
        A :class:`~global_mosaic.processing.mosaic_io.BandMosaicInputs`
        ready for build_tier_dataset, or None if nothing usable was found
        for this band at all.
    """
    # Fold each candidate into a running accumulator immediately after
    # it's built, instead of collecting a list of all 6 before selecting
    # -- SelectionAccumulator's whole point (see its docstring) is that a
    # caller who still holds every candidate simultaneously gets none of
    # its memory benefit even though the reduction itself never stacks
    # them. Without this, Tier B's 1km grid still hit a MemoryError at
    # the final selection step even with footprint-cropped candidates,
    # because all 6 satellites' cropped arrays were alive at once by
    # the time selection started. `sync_threshold_seconds` isn't
    # exposed by this driver (always best-effort/None, like the rest of
    # Phase 2), so there's no need for compute_sync_exclusion's
    # metadata-first pass here -- every successfully-built candidate is
    # folded in directly.
    resolved = resolve_all_phase0_slots(target)
    accumulator = SelectionAccumulator(grid_shape=(target_area.height, target_area.width))
    metas: list[SatelliteMeta] = []
    value_units = "unknown"
    n_folded = 0

    def _fold(candidate: Candidate, meta: SatelliteMeta, units: str, slot_name: str) -> bool:
        """Fold one satellite's candidate into the running accumulator, tolerating a fold failure.

        Args:
            candidate: The satellite's built candidate to fold in.
            meta: The candidate's satellite metadata, appended to
                `metas` on success.
            units: The candidate's value units, recorded as this band's
                `value_units` on success.
            slot_name: Logical slot name, used only for the failure log
                message.

        Returns:
            True if the candidate was folded successfully; False if
            `accumulator.update` raised (logged, not re-raised, so one
            bad satellite doesn't lose the others already folded).
        """
        nonlocal value_units, n_folded
        try:
            accumulator.update(candidate)
        except Exception as exc:  # noqa: BLE001 -- a resource failure folding one satellite shouldn't lose the others already folded
            print(f"  [{slot_name}/{band}] FAILED during fold: {exc!r}, skipping this satellite")
            return False
        del candidate  # explicit: let this satellite's raw arrays go before the next one is built
        metas.append(meta)
        value_units = units
        n_folded += 1
        return True

    if isolate_satellites:
        assert tier is not None, "isolate_satellites requires tier (needed to re-invoke this script)"
        # Pre-warms the SZA disk cache SEQUENTIALLY, in this process,
        # before any concurrent satellite subprocess can reach
        # _build_sza_grid itself. Failure mode: on a COLD cache,
        # pyorbital's cos_zen() needs several 4.4GB float64
        # intermediates at Tier B's grid size -- two satellite
        # subprocesses racing to compute the SAME (grid, target_time)
        # entry simultaneously (both seeing a cache miss) hit a
        # MemoryError, caught by a targeted smoke test. run_single_band
        # (the actual caller) already computes sza_grid before calling
        # this function, which happens to warm
        # this same cache entry first -- but build_band_mosaic shouldn't
        # depend on a caller's incidental ordering for correctness, so it
        # guarantees its own warm cache here. Free (cache hit, just a disk
        # read) whenever the caller already did this.
        if band in ("VIS", "SWIR"):
            _build_sza_grid(target_area, target)
        slots = [(slot_name, resolution) for slot_name, resolution in resolved.items() if resolution is not None]
        # Lands on _CACHE_DIR (GLOBAL_MOSAIC_CACHE_DIR, defaults to
        # D:\global_mosaic_cache), NOT OUT_DIR -- OUT_DIR is on the
        # project's G: drive, which has as little as ~17GB free and is the
        # exact drive that already filled to 100% once from
        # other multi-GB per-satellite artifacts (see reproject.py's
        # _CACHE_DIR docstring). Uncompressed Tier B candidates are
        # multi-GB each; this handoff needs room, not the project
        # drive.
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_paths = {
            slot_name: _CACHE_DIR
            / f"_tmp_sat_{tier.lower()}_{band.lower()}_{slot_name}_{target.strftime('%Y%m%dT%H%M%S')}.npz"
            for slot_name, _ in slots
        }

        # Launched through a bounded thread pool (MAX_CONCURRENT_SATELLITE_
        # SUBPROCESSES) rather than a plain loop -- each satellite's
        # subprocess is fully independent (own process, own footprint-
        # cropped candidate, own output file), so the machinery here would
        # let them run concurrently for free if that constant were raised.
        # It's currently pinned to 1 (fully sequential) because
        # measurement found every satellite's candidate build alone peaks
        # at ~26.9GB on this 32GB machine -- see that constant's own
        # docstring for the MemoryError this reproduced at 2 and the
        # measurement that explains why.
        ok_by_slot: dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_SATELLITE_SUBPROCESSES) as pool:
            futures = {
                pool.submit(
                    _run_satellite_subprocess, tier, target, band, slot_name, resolution.satellite_key, tmp_paths[slot_name]
                ): slot_name
                for slot_name, resolution in slots
            }
            for future in as_completed(futures):
                ok_by_slot[futures[future]] = future.result()

        # Folded in resolved.items()'s original order (not completion
        # order) purely for reproducible logs -- SelectionAccumulator.
        # update() is a running top-2-by-resolution reduction, provably
        # order-independent (see select.py's own docstring), so fold
        # order has no effect on the actual result.
        for slot_name, resolution in slots:
            tmp_path = tmp_paths[slot_name]
            if not ok_by_slot.get(slot_name, False):
                print(f"  [{slot_name}/{band}] {resolution.satellite_key}: subprocess produced nothing, skipping")
                continue
            candidate, meta, units = _read_candidate_npz(tmp_path)
            tmp_path.unlink()
            _fold(candidate, meta, units, slot_name)
    else:
        for slot_name, resolution in resolved.items():
            if resolution is None:
                continue
            try:
                result = build_candidate(
                    slot_name, resolution.satellite_key, target, target_area, band, sza_grid, target_resolution_km
                )
            except NotImplementedError as exc:
                print(f"  [{slot_name}/{band}] SKIPPED: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 -- one bad slot/band shouldn't kill the whole tier run
                print(f"  [{slot_name}/{band}] FAILED: {exc!r}")
                continue
            if result is None:
                continue
            candidate, meta, units = result
            _fold(candidate, meta, units, slot_name)

    if n_folded == 0:
        print(f"[{band}] No usable data for any slot -- skipping this band entirely.")
        return None

    # Finalizing/blending still operates on the whole full-grid state at
    # once, so a resource failure here isn't caught by the per-candidate
    # try/except above. Same "one bad thing shouldn't kill the whole run"
    # principle applied one level up: losing one band to a resource spike
    # shouldn't discard the other bands that already succeeded.
    try:
        lons, lats = _cached_lonlats(target_area)
        result = accumulator.finalize(
            excluded_by_sync=[],
            blend_margin_km=blend_margin_km,
            full_coords={"lat": (("y", "x"), lats), "lon": (("y", "x"), lons)},
        )

        value = result.value
        if blend_margin_km > 0:
            n_tied = int(result.tie_mask.values.sum())
            value = feather_seams(result)
            print(f"[{band}] Feathered {n_tied} seam pixels (blend margin {blend_margin_km} km)")
    except Exception as exc:  # noqa: BLE001
        print(f"[{band}] FAILED during selection/blending: {exc!r} -- skipping this band entirely.")
        return None

    return BandMosaicInputs(
        band_name=band,
        value=value,
        satellite_id=result.satellite_id,
        resolution_km=result.resolution_km,
        metas=metas,
        value_long_name=f"{band} ({get_calibration(band)})",
        value_units=value_units,
        tie_mask=result.tie_mask,
        runner_up_satellite_id=result.runner_up_satellite_id,
        runner_up_resolution_km=result.runner_up_resolution_km,
    )


def _build_sza_grid(target_area, target: dt.datetime) -> xr.DataArray:
    """Solar zenith angle depends only on grid shape + target time, not on
    which satellite is asking (illumination.py's own docstring: "the same
    solar_zenith_angle_deg array applies to every candidate satellite in
    a run" -- callers are expected to compute it once per run and reuse
    it). Per-satellite subprocess isolation broke that "once per run"
    contract by construction: each satellite subprocess is a fresh
    process with no memory of any other satellite's work, so a naive
    caller-computes-it-fresh-each-time design (this function's original
    form) ends up recomputing an IDENTICAL array once per satellite.
    Profiling measured that recompute at 52.5s -- ~25%
    of one Tier B satellite's total wall time -- for something that only
    needs to happen once per (grid, target_time) combination.

    Disk-cached like reproject.py's _cached_lonlats, same atomic
    tmp-then-rename pattern, but keyed additionally by target time
    (unlike lonlats, which only depends on grid shape) since a different
    run needs a different SZA grid, AND by `target_area.crop_offset` --
    same fix as _cached_lonlats' own (see that function's docstring):
    under tile-major processing, many tiles of the same tier share the
    same (width, height) but cover different rows, and without
    crop_offset in the key, the second same-shaped tile would silently
    reuse the first tile's SZA values instead of computing its own. The
    tmp filename includes this process's PID -- unlike _cached_lonlats'
    shared tmp name, this cache entry can genuinely be raced: multiple
    tiles (or, under the now-superseded per-satellite isolation,
    multiple satellite subprocesses for the same band) can legitimately
    want the SAME cache key on a cold cache simultaneously, so a shared
    tmp name here (safe for lonlats/neighbour_info, which are never
    raced this way) would let two writers interleave into the same file.
    """
    lons, lats = _cached_lonlats(target_area)
    cache_key = (
        f"sza_{target_area.width}x{target_area.height}_{target.strftime('%Y%m%dT%H%M%S')}"
        f"_{target_area.crop_offset[0]}_{target_area.crop_offset[1]}"
    )
    disk_path = _CACHE_DIR / f"{cache_key}.npz"
    if disk_path.exists():
        with np.load(disk_path) as cached:
            sza = cached["sza"]
    else:
        sza = solar_zenith_angle_deg(lats, lons, target)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = _CACHE_DIR / f"{cache_key}.{os.getpid()}.tmp.npz"
        np.savez(tmp_path, sza=sza)
        tmp_path.replace(disk_path)
    return xr.DataArray(
        sza,
        dims=("y", "x"),
        coords={"lat": (("y", "x"), lats), "lon": (("y", "x"), lons)},
    )


def build_band_mosaic_tiled(
    band: str,
    target: dt.datetime,
    target_area,
    blend_margin_km: float,
    target_resolution_km: float,
    out_path: Path,
    memory_budget_gb: float = 4.0,
    profile_memory: bool = False,
    profile_time: bool = False,
    gallery_dir: Path | None = None,
) -> bool:
    """`gallery_dir`, if given, is THIS band's own already-resolved output
    directory (e.g. .../gallery/tiera/mir/) for a rendered PNG thumbnail
    -- built from each tile's `value` array as it's produced in the loop
    below (see processing/preview.py's module docstring for why this
    accumulates in-memory rather than re-reading the finished .nc file).
    Silently skipped for any band not in preview.BAND_PREVIEW_SPECS (bands
    without an established fixed color-scale range).

    Tile-major replacement for build_band_mosaic's isolate_satellites
    path. Per-satellite peak memory on Tier B's grid was measured at
    ~26.9GB regardless of which satellite, dominated by full-grid-
    scale allocations -- SelectionAccumulator's 7 arrays (12.16GB, via
    tracemalloc), the cached lonlats grid (~4.42GB), the
    SZA grid (~2.21GB) -- that are NOT satellite-specific and don't
    shrink no matter how per-satellite work is optimized. Per-satellite
    subprocess isolation alone could never fit the 16GB RHEL production
    target because of this. Row-tiling the target grid so the
    accumulator/lonlats/SZA/candidates are all tile-sized instead fixes
    the actual bottleneck, and runs entirely in-process -- tiling itself
    is the memory-safety mechanism now, no subprocess spawning here at
    all (unlike build_band_mosaic's isolate_satellites path).

    Writes directly to `out_path` as it goes (see mosaic_io's
    open_tiled_band_writer/write_band_tile/finalize_tiled_band_writer)
    instead of returning an in-memory BandMosaicInputs for a caller to
    assemble and write afterward -- the whole point of tiling is never
    holding more than one tile's data at once, and handing back a
    full-grid result for later writing would undo that right at the end.
    Returns True if it wrote something, False if no satellite had usable
    data for this band at all (same contract as run_single_band).

    Phase 1 (see build_satellite_phase1) is recomputed FRESH for every
    (tile, satellite) pair here, NOT cached across tiles -- a correction
    to this function's first version, which cached all 6 satellites'
    Phase1Result simultaneously across the whole tile loop to avoid
    redundant fetch/decode work. That first version was measured
    (ctypes GetProcessMemoryInfo self-measurement) at ~26GB peak on
    Tier B's VIS band -- barely better than the ~26.9GB per-satellite
    peak that motivated replacing subprocess isolation with tiling in
    the first place. Just building and holding all 6 Phase1Results (no
    tile loop at all) already measured 13.67GB by itself, well beyond
    what 6 satellites' individually-small (~0.4-0.5GB each) downsampled
    arrays account for on their own -- pointing at satpy/dask state not
    being fully released between Scene loads within one process, a cost
    independent of target-grid size that caching Phase 1 across tiles
    was hiding behind, not avoiding. Paying the redundant fetch/decode
    cost (fetch_slot itself is a cheap cache-hit check once files are
    downloaded; the repeated cost is Scene load/calibrate/downsample
    happening once per tile instead of once per satellite) is the trade
    this function now makes for genuinely bounded memory, matching what
    independent review recommended before this function's first version
    deviated from it.

    Args:
        band: Logical band name, e.g. ``"MIR"``.
        target: Target timestamp to build this band for.
        target_area: The common target grid's ``AreaDefinition``.
        blend_margin_km: Seam-feathering margin in km; 0.0 disables
            blending.
        target_resolution_km: The tier's target grid resolution, in km.
        out_path: Destination path for this band's own tier-shaped
            output file.
        memory_budget_gb: Target peak memory per tile, used to size the
            row-tiling (see processing/tiling.py's compute_row_tiles).
        profile_memory: If True, print per-process working-set
            memory after every (tile, satellite) candidate and tile.
        profile_time: If True, print wall-clock elapsed time at the same
            points as `profile_memory`.
        gallery_dir: This band's own already-resolved gallery output
            directory (e.g. ``.../gallery/tiera/mir/``), or None to skip
            rendering a preview thumbnail.

    Returns:
        True if it wrote something to `out_path`, False if no satellite
        had usable data for this band at all (same contract as
        run_single_band).
    """
    band_start = time.perf_counter()
    resolved = resolve_all_phase0_slots(target)
    slots = [(slot_name, resolution) for slot_name, resolution in resolved.items() if resolution is not None]
    if not slots:
        print(f"[{band}] No resolved slots for this target -- skipping this band entirely.")
        return False

    include_sza = band in ("VIS", "SWIR")
    tiles = compute_row_tiles(
        target_area.width, target_area.height, memory_budget_gb=memory_budget_gb, include_sza=include_sza
    )
    print(f"[{band}] tile-major: {len(tiles)} tiles covering {target_area.height} rows (~{tiles[0].height} rows/tile)")

    # value_units is fixed per band except SWIR (its calibration isn't
    # unit-consistent across satellites -- see VALUE_UNITS_BY_BAND's own
    # docstring), which needs one candidate's actual
    # data.attrs["units"] to know. This is the ONE Phase 1 call in this
    # function not immediately followed by Phase 2 -- built, read, and
    # discarded purely to learn value_units before the writer can be
    # opened (its "value" variable's units attr is fixed at creation),
    # not held onto afterward.
    value_units = VALUE_UNITS_BY_BAND.get(band)
    if value_units is None:
        for slot_name, resolution in slots:
            try:
                probe = build_satellite_phase1(slot_name, resolution.satellite_key, target, band, target_resolution_km)
            except (NotImplementedError, Exception):  # noqa: BLE001 -- just probing for units, errors surface again in the tile loop below
                continue
            if probe is not None:
                value_units = probe.value_units
                del probe
                break
        if value_units is None:
            print(f"[{band}] No usable data for any slot -- skipping this band entirely.")
            return False

    lons_full, lats_full = _cached_lonlats(target_area)
    lat_1d = lats_full[:, 0]
    lon_1d = lons_full[0, :]
    value_long_name = f"{band} ({get_calibration(band)})"

    # Lands on _CACHE_DIR (GLOBAL_MOSAIC_CACHE_DIR, defaults to
    # D:\global_mosaic_cache), NOT next to out_path -- out_path is under
    # OUT_DIR on the project's G: drive, which has as little as ~10GB
    # free and is the exact drive that already filled to 100% once. The
    # stage file holds unpacked float32 value/
    # pixel_resolution_km/[solar_zenith_angle_deg] at FULL grid shape
    # (only ever one tile of it is in memory at a time, but the file on
    # disk is pre-allocated full-size) -- multi-GB for Tier B, needs
    # room, not the project drive.
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stage_path = _CACHE_DIR / f"_tmp_stage_{band.lower()}_{target.strftime('%Y%m%dT%H%M%S')}.nc"
    writer = open_tiled_band_writer(
        out_path,
        stage_path,
        tiles,
        (target_area.height, target_area.width),
        lat_1d,
        lon_1d,
        band,
        value_long_name,
        value_units,
        target,
        include_sza,
        variable_suffix=f"_{band.lower()}",
    )

    preview_accumulator = (
        new_accumulator(target_area.height, target_area.width)
        if gallery_dir is not None and band in BAND_PREVIEW_SPECS
        else None
    )

    metas_by_slot: dict[str, SatelliteMeta] = {}
    for tile in tiles:
        tile_start = time.perf_counter()
        tile_area = target_area[tile.y0 : tile.y1, :]
        sza_tile = _build_sza_grid(tile_area, target) if include_sza else None
        accumulator = SelectionAccumulator(grid_shape=(tile.height, target_area.width))
        n_folded_this_tile = 0
        for slot_name, resolution in slots:
            sat_start = time.perf_counter()
            try:
                phase1 = build_satellite_phase1(slot_name, resolution.satellite_key, target, band, target_resolution_km)
            except NotImplementedError as exc:
                print(f"  [{slot_name}/{band}] tile {tile.y0}:{tile.y1} SKIPPED: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 -- one bad slot/tile shouldn't kill the whole run
                print(f"  [{slot_name}/{band}] tile {tile.y0}:{tile.y1} FAILED (phase 1): {exc!r}")
                continue
            if phase1 is None:
                continue
            try:
                candidate = build_candidate_for_tile(phase1, tile_area, band, sza_tile)
                accumulator.update(candidate)
                del candidate  # explicit: let this satellite's tile-scoped arrays go before the next one
                n_folded_this_tile += 1
                metas_by_slot[slot_name] = phase1.meta
            except Exception as exc:  # noqa: BLE001 -- a resource failure on one satellite/tile shouldn't lose the others
                print(f"  [{slot_name}/{band}] tile {tile.y0}:{tile.y1} FAILED (phase 2): {exc!r}")
            finally:
                del phase1  # explicit: release this satellite's downsampled source array before the next one
                if profile_memory:
                    cur_gb, peak_gb = _memory_snapshot_gb()
                    print(f"    [memory] after {slot_name}: current {cur_gb:.2f}GB, peak-so-far {peak_gb:.2f}GB")
                if profile_time:
                    print(
                        f"    [time] {slot_name}: {_format_elapsed(time.perf_counter() - sat_start)}, "
                        f"tile so far {_format_elapsed(time.perf_counter() - tile_start)}, "
                        f"band so far {_format_elapsed(time.perf_counter() - band_start)}"
                    )

        result = accumulator.finalize(excluded_by_sync=[], blend_margin_km=blend_margin_km)
        value = result.value
        if blend_margin_km > 0 and bool(result.tie_mask.values.any()):
            value = feather_seams(result)

        write_band_tile(
            writer,
            tile,
            value.values,
            result.satellite_id.values,
            result.resolution_km.values,
            result.tie_mask.values,
            result.runner_up_satellite_id.values if result.runner_up_satellite_id is not None else None,
            result.runner_up_resolution_km.values if result.runner_up_resolution_km is not None else None,
            sza_tile.values if sza_tile is not None else None,
        )
        if preview_accumulator is not None:
            preview_accumulator.add_tile(tile.y0, tile.y1, value.values)
        print(f"[{band}] tile {tile.y0}:{tile.y1} written ({n_folded_this_tile}/{len(slots)} satellites)")
        if profile_memory:
            cur_gb, peak_gb = _memory_snapshot_gb()
            print(f"[{band}] tile {tile.y0}:{tile.y1} memory: current {cur_gb:.2f}GB, peak-so-far {peak_gb:.2f}GB")
        if profile_time:
            print(
                f"[{band}] tile {tile.y0}:{tile.y1} time: {_format_elapsed(time.perf_counter() - tile_start)}, "
                f"band so far {_format_elapsed(time.perf_counter() - band_start)}"
            )

        # Root cause of a ~26GB peak (barely better than the
        # per-satellite isolation this whole redesign replaces):
        # reproject.py's in-process lonlats/neighbour-info caches became
        # tile-aware (crop_offset in their keys, a correctness fix for
        # cross-tile collisions -- see that fix's own notes) but nothing
        # ever evicted them, so they silently accumulated one entry per
        # tile (lonlats) or per (tile, satellite) pair (neighbour-info,
        # up to 48 for this band) for the whole run. There is no
        # in-process reuse to lose by clearing here -- each (tile,
        # satellite) combination is visited exactly once per run by
        # construction -- and the disk layer underneath (cross-RUN reuse
        # for GOES/INSAT neighbour-info and all lonlats) is untouched by
        # this.
        clear_in_process_caches()

    if not metas_by_slot:
        # Every satellite failed on every tile -- close out the writer's
        # already-created (all-fill-valued, per Fix 2's "always write
        # every tile" invariant) files rather than leaving them open/
        # half-finalized, then report the same "nothing usable" contract
        # the non-tiled path has always had.
        writer.final_ds.close()
        writer.stage_ds.close()
        writer.stage_path.unlink(missing_ok=True)
        print(f"[{band}] No usable data for any slot across any tile -- skipping this band entirely.")
        return False

    finalize_tiled_band_writer(writer, list(metas_by_slot.values()))
    print(f"[{band}] wrote {out_path} ({out_path.stat().st_size / 1024:.1f} KB) via {len(tiles)} tiles")

    if preview_accumulator is not None:
        preview_accumulator.satellite_keys = {m.satellite_key for m in metas_by_slot.values()}
        preview_accumulator.scan_mids = [m.scan_mid for m in metas_by_slot.values()]
        # Filename carries BOTH the requested target (first -- what
        # THREDDS's dateFormatMark and retain_rolling_window.py's
        # retention logic key off, keeping it on the fixed request
        # cadence) and the actual mid-scan time (second, purely
        # informational -- lets you tell the actual time at a glance
        # without opening the image). Deliberately NOT applied to the
        # merged tier .nc file's own name -- that file bundles multiple
        # bands, which can genuinely have different median scan times,
        # so there's no single correct "actual time" for it the way
        # there is for one band's own accumulator.
        actual_mid = compute_display_time(preview_accumulator, target)
        preview_path = (
            gallery_dir
            / f"{band.lower()}_{target.strftime('%Y%m%dT%H%M%S')}_{actual_mid.strftime('%Y%m%dT%H%M%S')}.png"
        )
        # A re-render of this same target (e.g. a partial-recheck patch
        # picking up a satellite that was missing the first time) can land
        # on a different actual_mid than the earlier render, which changes
        # this filename -- without this, the earlier file would be orphaned
        # rather than replaced, since nothing else in the pipeline is keyed
        # on actual_mid to find and remove it.
        target_prefix = f"{band.lower()}_{target.strftime('%Y%m%dT%H%M%S')}_"
        for stale_path in gallery_dir.glob(f"{target_prefix}*.png"):
            if stale_path != preview_path:
                stale_path.unlink(missing_ok=True)
        render_band_preview(preview_accumulator, band, target, preview_path)
        del preview_accumulator  # explicit: release the full-grid float32 accumulator before this band's subprocess exits
        print(f"[{band}] wrote gallery preview {preview_path}")

    if profile_time:
        print(f"[{band}] total time: {_format_elapsed(time.perf_counter() - band_start)}")
    return True


def run_single_band(
    tier: str,
    band: str,
    target: dt.datetime,
    blend_margin_km: float,
    out_path: Path,
    isolate_satellites: bool | None = None,
    use_tiling: bool = True,
    memory_budget_gb: float = 4.0,
    profile_memory: bool = False,
    profile_time: bool = False,
    gallery_dir: Path | None = None,
) -> bool:
    """Builds and writes ONE band as its own small tier-shaped file.
    Returns True if it wrote something, False if this band had no usable
    data. Used both directly (--single-band) and as the subprocess target
    from main()'s per-band isolation -- see main()'s docstring note for
    why bands run in separate processes rather than a loop in one.

    `gallery_dir`, if given, is the gallery ROOT (e.g. .../gallery) --
    this function resolves it down to THIS tier+band's own subdirectory
    (.../gallery/tier<a|b>/<band>/) before handing it to
    build_band_mosaic_tiled, matching the on-disk layout the user already
    set up (gallery/tiera/{mir,tir1,tir2,wv}/).

    `use_tiling=True` (default): dispatches to build_band_mosaic_tiled,
    the current recommended path -- see that function's own docstring
    for why (per-satellite subprocess isolation alone can't fit the
    16GB RHEL production target; tiling can). `use_tiling=False` keeps
    the OLD full-grid/isolate_satellites path reachable for fallback
    and A/B comparison during validation, not deleted outright given
    this project's history of shipping memory changes that didn't hold
    up under closer testing -- see --no-tiling.

    `isolate_satellites=None` (only meaningful when use_tiling=False)
    auto-decides per this tier's grid size against
    PER_SATELLITE_ISOLATION_PIXEL_THRESHOLD; pass True/False to override
    (see --no-satellite-isolation).

    Args:
        tier: Tier name, ``"A"`` or ``"B"``.
        band: Logical band name, e.g. ``"MIR"``.
        target: Target timestamp to build this band for.
        blend_margin_km: Seam-feathering margin in km; 0.0 disables
            blending.
        out_path: Destination path for this band's own tier-shaped
            output file.
        isolate_satellites: Only meaningful when `use_tiling` is False;
            None auto-decides from this tier's grid size.
        use_tiling: Whether to use the tile-major path
            (build_band_mosaic_tiled) or the old full-grid path
            (build_band_mosaic).
        memory_budget_gb: Target peak memory per tile (tiling path only).
        profile_memory: If True, print per-process working-set
            memory at various points during the build.
        profile_time: If True, print wall-clock elapsed time at the same
            points as `profile_memory`.
        gallery_dir: Gallery ROOT directory, or None to skip rendering a
            preview thumbnail; resolved down to this tier+band's own
            subdirectory before use.

    Returns:
        True if it wrote something, False if this band had no usable data.
    """
    resolution_km, _ = TIERS[tier]
    target_area = common_grid(resolution_km=resolution_km)
    band_gallery_dir = gallery_dir / f"tier{tier.lower()}" / band.lower() if gallery_dir is not None else None

    if use_tiling:
        print(f"\n=== Band {band} === (tile-major, budget {memory_budget_gb}GB)")
        return build_band_mosaic_tiled(
            band,
            target,
            target_area,
            blend_margin_km,
            resolution_km,
            out_path,
            memory_budget_gb=memory_budget_gb,
            profile_memory=profile_memory,
            profile_time=profile_time,
            gallery_dir=band_gallery_dir,
        )

    if isolate_satellites is None:
        isolate_satellites = (target_area.height * target_area.width) > PER_SATELLITE_ISOLATION_PIXEL_THRESHOLD
    sza_grid = _build_sza_grid(target_area, target) if band in ("VIS", "SWIR") else None

    print(f"\n=== Band {band} ===" + (" (per-satellite isolation)" if isolate_satellites else ""))
    inputs = build_band_mosaic(
        band,
        target,
        target_area,
        blend_margin_km,
        sza_grid,
        target_resolution_km=resolution_km,
        tier=tier,
        isolate_satellites=isolate_satellites,
    )
    if inputs is None:
        return False

    ds = build_tier_dataset(f"Tier {tier}", [inputs], target, solar_zenith_angle_deg=sza_grid)
    write_tier_netcdf(str(out_path), ds)
    print(f"[{band}] wrote {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")
    return True


def _merge_single_band_files(paths: list[Path], tier: str, target: dt.datetime, out_path: Path) -> None:
    """Combines already-written single-band tier files (see
    run_single_band) into one final multi-band tier file. Each input
    already has this tier's band-suffixed variable names and identical
    lat/lon/crs (same target_area/grid every time), so this is a
    straightforward merge -- no recomputation, just reading back the
    small packed files (far lighter than the raw acquisition/reprojection
    pipeline that produced them).

    Uses merge_tiled_band_files (raw netCDF4, tile-major copy) rather
    than xr.open_dataset + xr.merge + write_tier_netcdf's ds.to_netcdf()
    -- that xarray path forced every packed field to be CF-decoded to
    float64 for the WHOLE grid then re-encoded, which crashed with
    numpy.ArrayMemoryError under concurrent-run memory pressure even
    though this step's own per-band inputs were each written well
    within budget (a 2026-08-06/07 incident this replaces the fix for).

    Args:
        paths: Already-written single-band tier files (from
            run_single_band) to merge.
        tier: Tier name, ``"A"`` or ``"B"``, used in the merged file's title.
        target: The originally requested mosaic timestamp, recorded as
            the merged file's `requested_target_time` attribute.
        out_path: Destination path for the merged multi-band tier file.

    Returns:
        None. Writes the merged file at `out_path` as a side effect.
    """
    # Both build_tier_dataset (line ~328) and finalize_tiled_band_writer
    # (line ~594) set each single-band file's own title as
    # "Global geostationary mosaic -- {band_name}" -- no parentheses --
    # but this used to assume a "Tier X (BAND)"-style parenthesized title
    # and crashed with IndexError the first time a multi-band tiled
    # merge was actually exercised end-to-end (VIS+SWIR both wrote fine,
    # merging them is what failed). rsplit on the "-- " separator instead
    # of assuming parens. Read via raw netCDF4 (a single string attr,
    # not grid data) rather than xarray -- no reason to import the
    # heavier machinery just for this.
    band_names = []
    for p in paths:
        with netCDF4.Dataset(str(p), "r") as d:
            band_names.append(d.title.rsplit("-- ", 1)[-1])

    top_level_attrs = {
        "title": f"Global geostationary mosaic -- Tier {tier} ({', '.join(band_names)})",
        "history": f"Generated {dt.datetime.now(dt.timezone.utc).isoformat()} by global_mosaic.processing.mosaic_io "
        "(merged from per-band subprocess runs, see run_phase2_tier.py main())",
        "requested_target_time": target.isoformat(),
        "references": "See global_mosaic.satellites.sat_metadata for per-satellite technical specifications.",
        # Which INSAT generation the "insat" slot resolved to for this
        # target (nearest-real-scan-time wins between 3DR/3DS, see
        # config.py::resolve_phase0_slot) -- applies to every band in
        # this file (one bundled INSAT file covers all 6 channels), but
        # matters most for WV, the only band whose native resolution
        # actually differs between generations (3D/3DR 8km vs 3DS 4km).
        # "none" means neither had usable data for this target and the
        # insat slot was dropped from this run entirely.
        "insat_generation": (resolve_phase0_slot("insat", target) or ResolvedSlot("insat", "none", 0.0))
        .satellite_key.removeprefix("insat-")
        .upper(),
    }
    merge_tiled_band_files(paths, out_path, top_level_attrs)


def _cleanup_raw_files_for_tier_run(bands: list[str], out_path: Path, dest_dir: Path) -> None:
    """Deletes every raw source file that contributed to this tier run's
    just-written merged file -- ground truth, read back from each band's
    own satellite_source_filename_<band> (see mosaic_io.py's
    _append_satellite_metadata_table), not guessed from which satellites
    the run happened to request. A single filename for GOES/Meteosat/
    INSAT; a "+"-joined list of all 10 segment filenames for Himawari
    (matches schedule_pipeline.py's own _raw_files_for_nc, duplicated
    here rather than imported -- see this file's existing
    METEOSAT_DATASET_BY_SLOT comment for why small pieces like this stay
    duplicated between these two scripts).

    Called from main() for Tier B specifically. Unlike Tier A,
    schedule_pipeline.py's own docstring explicitly excludes Tier B from
    the automated scheduler (too memory/time-expensive for unattended
    recurring runs), so there's no state-file "missing satellite, retry
    later" tracking for it at all -- a Tier B run is always a one-shot
    manual invocation, either it wrote a file or it didn't, so cleaning
    up immediately after a successful write (no 7-day wait, no
    partial-timestamp deferral) is unconditionally correct here, unlike
    Tier A's own deferred/conditional _cleanup_raw_files_for_target.
    This class of cleanup exists to prevent a 2026-08-08 incident: this
    exact shared DEST_DIR had NO cleanup at all before that date, grew
    unbounded, and filled the whole G: drive, corrupting the
    scheduler's own state file mid-write -- Tier B shares this same
    DEST_DIR, so it's exposed to the identical failure mode the moment
    it's actually run, even though it was Tier A's continuous runs that
    triggered the incident.

    A raw file already missing is skipped quietly, not an error.

    Args:
        bands: Band names contributing to `out_path`, used to look up
            each band's ``satellite_source_filename_<band>`` variable.
        out_path: The just-written merged tier file to read ground-truth
            source filenames from.
        dest_dir: Raw-file directory the source filenames are relative to.

    Returns:
        None. Deletes matched raw files as a side effect and prints a
        summary count.
    """
    files: set[Path] = set()
    with netCDF4.Dataset(str(out_path), "r") as ds:
        for band in bands:
            var_name = f"satellite_source_filename_{band.lower()}"
            if var_name not in ds.variables:
                continue
            for raw in ds.variables[var_name][:]:
                for name in str(raw).split("+"):
                    if name:
                        files.add(dest_dir / name)
    deleted = 0
    for raw_path in files:
        if raw_path.exists():
            raw_path.unlink()
            deleted += 1
    if files:
        print(f"Cleaned up {deleted}/{len(files)} raw source file(s) from {dest_dir}")


def main(
    tier: str,
    target: dt.datetime,
    blend_margin_km: float,
    isolate_bands: bool = True,
    isolate_satellites: bool | None = None,
    use_tiling: bool = True,
    memory_budget_gb: float = 4.0,
    profile_memory: bool = False,
    profile_time: bool = False,
    final_out_dir: Path | None = None,
    gallery_dir: Path | None = None,
) -> None:
    """Runs each band of `tier` in its own subprocess (default), then
    merges the results.

    Why subprocesses, not a plain loop: reproduced twice against
    satellite data -- a fresh, unstarted process handles a
    single band on Tier A's 2km/148M-pixel grid fine (even with all 6
    satellites contributing), but running bands back-to-back *within one
    Python process* reliably fails with a MemoryError starting on
    the 2nd-4th band, even after fixing two contributing bugs
    (resolution_km's needless float64, and reproject.py recomputing full
    lat/lon grids from scratch on every call instead of caching). That
    points to memory not being fully reclaimed between bands within one
    process (dask/satpy caching, allocator fragmentation, or both) rather
    than any single array being too large on its own. A subprocess exiting
    is a hard OS-level guarantee of full reclamation that chasing further
    individual allocations inside select.py can't match without much more
    investigation.

    `isolate_bands=False` is an experiment, not the new default: once
    footprint cropping + streaming fold-and-discard selection landed
    (same driver), peak memory dropped enough that
    isolation might no longer be load-bearing -- and profiling separately
    found satpy's own import cost is only ~1.3s, so the overhead
    isolation avoids is much smaller than assumed when it was first
    built. Worth measuring properly rather than assuming either way,
    but keep isolation as the default until that's confirmed stable
    across more than one measurement.

    `use_tiling=True` (default): each band uses build_band_mosaic_tiled
    (see run_single_band's own docstring) -- `isolate_satellites` is then
    irrelevant (ignored) since tiling itself is the memory-safety
    mechanism, not subprocess isolation. `use_tiling=False` keeps the
    OLD full-grid path reachable, in which case `isolate_satellites` is
    passed straight through to each band's run_single_band call (in-
    process or via the per-band subprocess's --no-satellite-isolation
    flag) -- None (default) lets each band auto-decide from its own
    tier's grid size (see PER_SATELLITE_ISOLATION_PIXEL_THRESHOLD);
    pass False to force it off everywhere for measurement.

    `final_out_dir`, if given, only redirects the FINAL merged tier file
    (e.g. to a network-mounted THREDDS share) -- per-band intermediate
    `.nc` files still land on local OUT_DIR as always, so pointing this
    at a network drive doesn't add network I/O to the scratch-file path,
    only to the one file meant to be served.

    `gallery_dir`, if given, is the gallery ROOT under which each band's
    rendered PNG thumbnail lands (see run_single_band's docstring for the
    exact tier<a|b>/<band>/ subdirectory layout) -- threaded through to
    each per-band subprocess the same way --profile-memory/--profile-time
    already are, since rendering happens inside build_band_mosaic_tiled,
    per-band, not at this top-level merge step.

    Args:
        tier: Tier name, ``"A"`` or ``"B"``.
        target: Target timestamp to build this tier for.
        blend_margin_km: Seam-feathering margin in km; 0.0 disables
            blending.
        isolate_bands: If True (default), run each band in its own
            subprocess; if False, run them in a loop in this process
            (experimental, see docstring above).
        isolate_satellites: Only meaningful when `use_tiling` is False;
            None auto-decides per band from that tier's grid size.
        use_tiling: Whether each band uses the tile-major path
            (build_band_mosaic_tiled) or the old full-grid path.
        memory_budget_gb: Target peak memory per tile (tiling path only).
        profile_memory: If True, print per-process working-set
            memory during each band's build.
        profile_time: If True, print wall-clock elapsed time during each
            band's build and for the whole run.
        final_out_dir: If given, redirects only the final merged tier
            file here (e.g. a network-mounted THREDDS share);
            per-band intermediates still use local ``OUT_DIR``.
        gallery_dir: Gallery ROOT directory, or None to skip rendering
            preview thumbnails.

    Returns:
        None. Writes the merged tier file and prints progress to stdout.

    Raises:
        SystemExit: If no band produced usable data for this target time.
    """
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if final_out_dir is not None:
        final_out_dir.mkdir(parents=True, exist_ok=True)
    run_start = time.perf_counter()

    _, bands = TIERS[tier]
    tmp_paths = [OUT_DIR / f"_tmp_tier{tier.lower()}_{band.lower()}_{target.strftime('%Y%m%dT%H%M%S')}.nc" for band in bands]

    written_paths = []
    for band, tmp_path in zip(bands, tmp_paths):
        if isolate_bands:
            cmd = [
                sys.executable,
                __file__,
                tier,
                target.isoformat(),
                "--blend-margin-km",
                str(blend_margin_km),
                "--single-band",
                band,
                "--out",
                str(tmp_path),
            ]
            if not use_tiling:
                cmd.append("--no-tiling")
                if isolate_satellites is False:
                    cmd.append("--no-satellite-isolation")
            else:
                cmd += ["--memory-budget-gb", str(memory_budget_gb)]
            if profile_memory:
                cmd.append("--profile-memory")
            if profile_time:
                cmd.append("--profile-time")
            if gallery_dir is not None:
                cmd += ["--gallery-dir", str(gallery_dir)]
            result = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]))
            ok = result.returncode == 0 and tmp_path.exists()
        else:
            ok = run_single_band(
                tier,
                band,
                target,
                blend_margin_km,
                tmp_path,
                isolate_satellites=isolate_satellites,
                use_tiling=use_tiling,
                memory_budget_gb=memory_budget_gb,
                profile_memory=profile_memory,
                profile_time=profile_time,
                gallery_dir=gallery_dir,
            )
        if ok:
            written_paths.append(tmp_path)
        else:
            print(f"[{band}] failed or produced nothing, skipping this band.")

    if not written_paths:
        print("\nNo usable data for any band in this tier -- nothing to write.")
        sys.exit(1)

    out_path = (final_out_dir or OUT_DIR) / f"tier{tier.lower()}_{target.strftime('%Y%m%dT%H%M%S')}.nc"
    try:
        _merge_single_band_files(written_paths, tier, target, out_path)
    finally:
        # Always clean up the per-band local tmp files, even if the merge
        # itself raised. Before 2026-08-09 this loop ran only after a
        # successful merge, so any crash/kill of this process between
        # generating the per-band files and finishing the merge (several
        # incidents before the INSAT/Meteosat download-race and hang
        # fixes) left them behind permanently -- one such incident left
        # ~2.3GB across 20 files sitting in OUT_DIR days later. These
        # are always safe to delete unconditionally -- they're one-shot
        # intermediates regenerated fresh on every run, never treated as
        # a cache to skip recomputation on retry.
        for p in written_paths:
            p.unlink(missing_ok=True)
    print(
        f"\nWrote {out_path} ({out_path.stat().st_size / 1024:.1f} KB), "
        f"bands: {[p.stem.split('_')[-2].upper() for p in written_paths]}"
    )
    if tier == "B":
        _cleanup_raw_files_for_tier_run(bands, out_path, DEST_DIR)
    if profile_time:
        print(f"Total run time ({tier}): {_format_elapsed(time.perf_counter() - run_start)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tier", choices=["A", "B"], help="A: MIR/WV/TIR1/TIR2 @ 2km. B: VIS/SWIR @ 1km.")
    parser.add_argument("target", help="ISO 8601 target timestamp, e.g. 2026-07-30T20:06:00")
    parser.add_argument(
        "--blend-margin-km",
        type=float,
        default=0.0,
        help="feather seams (processing/blend.py) wherever the winner and "
        "runner-up's actual resolution are within this many km of each "
        "other. Default 0.0: no blending, hard winner-take-all cut.",
    )
    parser.add_argument(
        "--single-band",
        default=None,
        help="internal: process only this one band and write it to --out, "
        "instead of running the whole tier. This is what main() invokes as "
        "a subprocess per band (see main()'s docstring for why) -- not "
        "normally passed by hand.",
    )
    parser.add_argument(
        "--single-satellite",
        action="store_true",
        help="internal: within --single-band's band, process only ONE "
        "satellite's candidate and write it to --out as a small .npz, "
        "instead of the whole band. Requires --slot-name and "
        "--satellite-key. This is what build_band_mosaic invokes as a "
        "subprocess per satellite once a tier's grid crosses "
        "PER_SATELLITE_ISOLATION_PIXEL_THRESHOLD -- not normally passed "
        "by hand.",
    )
    parser.add_argument("--slot-name", default=None, help="slot name for --single-satellite mode, e.g. goes_east")
    parser.add_argument("--satellite-key", default=None, help="satellite key for --single-satellite mode, e.g. goes-19")
    parser.add_argument("--out", type=Path, default=None, help="output path, required with --single-band/--single-satellite")
    parser.add_argument(
        "--no-isolation",
        action="store_true",
        help="EXPERIMENTAL: process all bands in this one process instead of "
        "spawning a subprocess per band. See main()'s docstring -- isolation "
        "was needed before footprint cropping + streaming selection landed; "
        "this flag is for measuring whether it's still needed, not a "
        "recommended default yet.",
    )
    parser.add_argument(
        "--no-satellite-isolation",
        action="store_true",
        help="Only meaningful with --no-tiling: force all of a band's "
        "satellites to run in one process instead of spawning a subprocess "
        "per satellite, overriding the automatic "
        "PER_SATELLITE_ISOLATION_PIXEL_THRESHOLD decision for this run.",
    )
    parser.add_argument(
        "--no-tiling",
        action="store_true",
        help="Use the OLD full-grid path (build_band_mosaic, optionally with "
        "--no-satellite-isolation) instead of the current default tile-major "
        "path (build_band_mosaic_tiled). Kept as a fallback for A/B "
        "comparison during validation -- per-satellite subprocess isolation "
        "alone cannot fit the 16GB RHEL production target (real per-"
        "satellite peak measured at ~26.9GB on Tier B), tiling can.",
    )
    parser.add_argument(
        "--memory-budget-gb",
        type=float,
        default=4.0,
        help="Only meaningful with tiling (the default): target peak memory "
        "per tile, used to compute how many row-tiles a band's grid is "
        "split into (see processing/tiling.py::compute_row_tiles). Default "
        "4.0GB leaves headroom on the 16GB RHEL production target.",
    )
    parser.add_argument(
        "--profile-memory",
        action="store_true",
        help="Print real per-process working-set memory (current + "
        "historical peak, via ctypes GetProcessMemoryInfo -- see project "
        "notes on why external Get-Process polling doesn't work here) "
        "after every (tile, satellite) candidate and after every tile. "
        "Threaded through to each per-band subprocess automatically.",
    )
    parser.add_argument(
        "--profile-time",
        action="store_true",
        help="Print real wall-clock elapsed time (time.perf_counter, not "
        "CPU time) after every (tile, satellite) candidate, after every "
        "tile, after every band, and for the whole run. Threaded through "
        "to each per-band subprocess automatically, same as "
        "--profile-memory.",
    )
    parser.add_argument(
        "--final-out-dir",
        type=Path,
        default=None,
        help="Redirect ONLY the final merged tier .nc file here (e.g. a "
        "network-mounted THREDDS share) -- per-band intermediates still "
        "use local OUT_DIR. Ignored with --single-band/--single-satellite "
        "(use --out for those).",
    )
    parser.add_argument(
        "--gallery-dir",
        type=Path,
        default=None,
        help="Gallery ROOT directory -- if given, each band gets a "
        "rendered PNG thumbnail written to <gallery-dir>/tier<a|b>/<band>/ "
        "(fixed-range reverse-grayscale, graticules, coastlines, country "
        "labels, legend -- see processing/preview.py). Built from each "
        "band's in-memory tile data as it's produced, not by re-reading "
        "the finished .nc file. Silently skipped for any band without an "
        "established color-scale range (see preview.BAND_PREVIEW_SPECS).",
    )
    args = parser.parse_args()
    target = dt.datetime.fromisoformat(args.target)
    isolate_satellites = False if args.no_satellite_isolation else None
    use_tiling = not args.no_tiling

    if args.single_satellite:
        if not (args.single_band and args.slot_name and args.satellite_key and args.out):
            parser.error("--single-satellite requires --single-band, --slot-name, --satellite-key, and --out")
        DEST_DIR.mkdir(parents=True, exist_ok=True)
        wrote = run_single_satellite_candidate(
            args.tier, args.single_band, args.slot_name, args.satellite_key, target, args.out
        )
        sys.exit(0 if wrote else 1)

    if args.single_band:
        if args.out is None:
            parser.error("--single-band requires --out")
        DEST_DIR.mkdir(parents=True, exist_ok=True)
        wrote = run_single_band(
            args.tier,
            args.single_band,
            target,
            args.blend_margin_km,
            args.out,
            isolate_satellites=isolate_satellites,
            use_tiling=use_tiling,
            memory_budget_gb=args.memory_budget_gb,
            profile_memory=args.profile_memory,
            profile_time=args.profile_time,
            gallery_dir=args.gallery_dir,
        )
        sys.exit(0 if wrote else 1)

    main(
        args.tier,
        target,
        args.blend_margin_km,
        isolate_bands=not args.no_isolation,
        isolate_satellites=isolate_satellites,
        use_tiling=use_tiling,
        memory_budget_gb=args.memory_budget_gb,
        profile_memory=args.profile_memory,
        profile_time=args.profile_time,
        final_out_dir=args.final_out_dir,
        gallery_dir=args.gallery_dir,
    )
