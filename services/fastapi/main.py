"""Minimal FastAPI service backing three GeoSat-MOSAIC features THREDDS
genuinely cannot serve to a browser on its own: the info button's
per-satellite metadata table, point-probe, and the Plotly timeseries.

Info button: satellite_key_<band>, satellite_longitude_<band>, and
satellite_scan_start/mid/end_<band> are all dimensioned by satellite_<band>
(6 slots per file), not by lat/lon -- they're not grid variables, so
THREDDS's NCSS grid endpoint refuses them outright ("is not contained in
the requested dataset"). OPeNDAP can't serve the scan-time
fields either way -- they're int64/datetime64, and DAP2 has no 64-bit
integer type (the same reason they're stripped from the WMS/OPeNDAP
aggregation in catalog.xml).

Point-probe / timeseries: NCSS's grid-as-point endpoint (the normal way to
pull a single pixel out of a THREDDS grid dataset) is outright unsafe
against this data -- it can silently return the wrong pixel
(the [0,0] grid corner, ignoring the requested lat/lon entirely) OR crash
the whole Tomcat process with a java.lang.OutOfMemoryError, on both
the aggregated dataset AND the raw single-file dataset. Never use it here.

Concurrency, 2026-08-15 (two rounds): every route here reads a `.nc` file
with netCDF4, which wraps netCDF-C/HDF5 -- not thread-safe for concurrent
access from different threads in one process. A `ThreadPoolExecutor`
version of /timeseries's per-file loop was tried first (this app's
earliest version) and turned out to be genuinely unsafe: intermittent
"NetCDF: HDF error", and on a larger batch, silently crashed the whole
process. The fix at the time was making that one loop sequential -- but
that only protected *within* one request, not *across* different routes
hit at the same moment (this app's normal pattern: every band/timestamp
change fires /minmax and /satellite-metadata together). RHEL-side
root-caused a production crash from exactly that gap -- fixed same day with a
process-wide `threading.Lock()` around every netCDF4 access, which worked
but serialized everything, including routes that had nothing to do with
each other, making concurrent load feel slow.

**Second round, same day**: replaced the lock with two levers
instead of trading correctness for throughput --

1. **In-memory caching** for /minmax and /satellite-metadata, keyed by
   (filename, band). Both are pure functions of those inputs and files
   are immutable once complete (same argument as the nginx tile cache on
   the xcube side), so this is unconditionally safe, not just a
   heuristic. Unbounded dict, no eviction -- the key space is
   naturally bounded by the rolling 7-day retention window (~213 files x
   4 bands at most), a negligible memory footprint even fully populated.
2. **A `ProcessPoolExecutor`** for the actual netCDF4 reads (cache misses,
   plus /point-probe and /timeseries which aren't cached -- lat/lon varies
   too much per request to be worth it). Each worker is a separate OS
   process with its own independent HDF5 library state, so this sidesteps
   the thread-safety hazard entirely rather than working around it --
   genuinely concurrent, genuinely safe, at the cost of
   inter-process dispatch overhead (small compared to the actual
   ~1-4s/file netCDF4 read cost). This is *also* why /timeseries's
   per-file loop is no longer forced sequential: the earlier crash was
   specifically about **threads** sharing one process's HDF5 state --
   process-pool dispatch doesn't have that problem, so
   `asyncio.gather`-ing the per-file lookups concurrently (throttled to
   `max_workers` at a time) is safe now in a way it wasn't with the old
   ThreadPoolExecutor attempt.

All three features read the raw tiera_<timestamp>.nc file(s) directly
with netCDF4 instead, sidestepping THREDDS for exactly the parts it
can't handle.

Dev-only for now: no auth. CORS origins were previously hardcoded to the
two Vite dev server ports (5173/5174), which worked when this service and
the frontend dev servers ran on the same box (RHEL). As of 2026-08-14 the
backend (this service, THREDDS, xcube) moved to a separate Windows/WSL2
box while the frontend dev servers stay on the RHEL box -- the two are now
on different machines, so the Origin header the browser sends is whatever
address/port the frontend is actually being served from on RHEL, which
this service has no fixed way to predict. Switched to a wildcard
(allow_origins=["*"]) to match xcube's existing permissive CORS behavior
rather than hardcode a guess at the RHEL box's real address -- safe here
specifically because allow_credentials is not set (defaults to False), so
FastAPI/Starlette permits combining a wildcard origin with unauthenticated
requests. Revisit if auth is ever added.
"""

import asyncio
import secrets
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

import netCDF4
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Shared secret for /_internal/prune-cache -- see that route's own
# docstring for why an IP-based check doesn't work here (uvicorn trusts
# X-Forwarded-For by default, trivially spoofable, and both nginx and
# the Cloudflare Tunnel connect to this app over loopback anyway, so
# genuinely-external and genuinely-local callers are indistinguishable
# at the TCP level). Read once at import time, not per-request.
_INTERNAL_SECRET_PATH = Path("/opt/geosat-mosaic-api/.internal-secret")
_INTERNAL_SECRET = _INTERNAL_SECRET_PATH.read_text().strip()

# Same directory THREDDS's own aggregation and the new raw-file
# datasetScan (catalog.xml) both scan -- this bypasses THREDDS entirely
# rather than reading through it, since THREDDS can't serve these fields.
DATA_DIR = Path("/srv/mosaic-data/tiera")

BANDS = {"mir", "wv", "tir1", "tir2"}

# See the module docstring's "Concurrency, 2026-08-15" section for the
# full story. 4 workers is a reasonable default for this box, not a
# measured-optimal number -- tune if usage ever saturates it.
_process_pool = ProcessPoolExecutor(max_workers=4)

# (filename, band) -> response dict. Safe unconditionally (files are
# immutable once complete) and unbounded on purpose -- see docstring.
# Pruned by /_internal/prune-cache (see prewarm_minmax.py) rather than a
# size/TTL policy, since "no longer needed" here means "file left the
# rolling retention window," not "got old."
_minmax_cache: dict[tuple[str, str], dict] = {}
_satellite_metadata_cache: dict[tuple[str, str], dict] = {}

# In-flight computations per (filename, band), so N concurrent requests
# for the same not-yet-cached key coalesce onto one computation
# instead of each independently submitting to the process pool. Without
# this, 3 concurrent duplicate requests for the same cold key each ran
# their own full ~2-5s computation and competed for the same 4 worker
# slots, pushing all 3 past 15-20s instead of one paying the cost and the
# other two getting it free once it lands -- missed in the initial
# 2026-08-15 cache+pool fix, added same day after RHEL-side
# re-verification turned it up.
_minmax_inflight: dict[tuple[str, str], asyncio.Future] = {}
_satellite_metadata_inflight: dict[tuple[str, str], asyncio.Future] = {}


async def _cached_call(
    cache: dict[tuple[str, str], dict],
    inflight: dict[tuple[str, str], asyncio.Future],
    key: tuple[str, str],
    worker: Callable[..., dict],
    *args,
) -> dict:
    """Cache + in-flight-coalescing wrapper around a process-pool worker
    call, keyed by (filename, band). Used by /minmax and
    /satellite-metadata -- both pure functions of those inputs. NOT used
    for /point-probe/timeseries, which vary by lat/lon per request and
    wouldn't get a meaningful cache hit rate.

    Args:
        cache: The (filename, band) -> result cache to check/populate.
        inflight: The (filename, band) -> in-progress Future map used to
            coalesce concurrent callers for the same key.
        key: The (filename, band) cache key for this call.
        worker: The process-pool worker function to invoke on a cache
            miss (`_read_minmax` or `_read_satellite_metadata`).
        *args: Positional arguments to forward to `worker`.

    Returns:
        The cached or freshly-computed result dict for `key`.

    Raises:
        Exception: Whatever `worker` itself raises, propagated to this
            call (and to any other caller awaiting the same in-flight
            Future) after the failed key is removed from `inflight`.
    """
    cached = cache.get(key)
    if cached is not None:
        return cached

    existing = inflight.get(key)
    if existing is not None:
        return await existing

    loop = asyncio.get_running_loop()
    future = loop.create_future()
    inflight[key] = future
    try:
        result = await loop.run_in_executor(_process_pool, worker, *args)
        if "error" not in result:
            cache[key] = result
        if not future.done():
            future.set_result(result)
        return result
    except Exception as exc:  # noqa: BLE001 -- propagate to this caller, but also unblock followers
        if not future.done():
            future.set_exception(exc)
        raise
    finally:
        inflight.pop(key, None)

app = FastAPI(title="GeoSat-MOSAIC metadata API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _filename_for(timestamp: str) -> str:
    """"2026-08-11T04:25:08.000Z" (straight from THREDDS's own aggregated
    time dimension, which is what the frontend always passes here) ->
    "tiera_20260811T042508.nc" -- matches catalog.xml's dateFormatMark
    exactly, since that's literally where the aggregation's time values
    come from in the first place.

    Args:
        timestamp: ISO 8601 timestamp string, e.g.
            `"2026-08-11T04:25:08.000Z"`.

    Returns:
        The corresponding mosaic filename, e.g.
        `"tiera_20260811T042508.nc"`.

    Raises:
        ValueError: If `timestamp` is not a valid ISO 8601 string.
    """
    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return f"tiera_{dt.strftime('%Y%m%dT%H%M%S')}.nc"


def _iso(var, index=Ellipsis) -> str:
    """Decodes one (or all) values of a netCDF4 time variable to an ISO
    8601 string using that variable's own `units`/`calendar` attributes.

    Args:
        var: A netCDF4 Variable holding numeric time value(s).
        index: Index (or slice) into `var` to decode. Defaults to
            `Ellipsis`, decoding the whole variable.

    Returns:
        ISO 8601 string with a trailing `"Z"`, e.g.
        `"2026-08-11T04:25:08.000000Z"`.
    """
    value = var[index]
    return netCDF4.num2date(value, units=var.units, calendar=var.calendar).isoformat() + "Z"


def _maybe_float(value) -> float | None:
    """netCDF4 returns the numpy.ma masked singleton for a value that
    equals that variable's _FillValue -- `satellite_band_wavelength_um_
    <band>` is one real example (per-slot, `_FillValue: nan`, genuinely
    unset for an unused slot in a file with fewer than 6 contributing
    satellites).

    Args:
        value: A scalar value read from a netCDF4 variable (possibly the
            numpy.ma masked singleton).

    Returns:
        `float(value)`, or None if `value` is masked.
    """
    return None if np.ma.is_masked(value) else float(value)


def _satellite_entry(ds, band: str, slot: int) -> dict:
    """One row of the per-satellite table (key/longitude/scan times) for
    slot `slot` (an index into the satellite_<band> dimension) -- shared by
    /satellite-metadata (every slot) and /point-probe (just the slot(s)
    resolved for one pixel).

    Args:
        ds: Open netCDF4 Dataset for the mosaic file.
        band: Band name, e.g. `"mir"`.
        slot: Index into the `satellite_<band>` dimension identifying
            which contributing satellite's row to build.

    Returns:
        Dict with `key`, `longitude`, `scan_start`, `scan_mid`,
        `scan_end`, `band_wavelength_um`, and `spatial_resolution_km`
        entries for this satellite slot.
    """
    return {
        "key": str(ds.variables[f"satellite_key_{band}"][slot]),
        "longitude": float(ds.variables[f"satellite_longitude_{band}"][slot]),
        "scan_start": _iso(ds.variables[f"satellite_scan_start_{band}"], slot),
        "scan_mid": _iso(ds.variables[f"satellite_scan_mid_{band}"], slot),
        "scan_end": _iso(ds.variables[f"satellite_scan_end_{band}"], slot),
        # Band center wavelength, per-satellite (not per-pixel -- a
        # satellite's sensor band has one fixed wavelength, unlike
        # pixel_resolution_km below which genuinely varies pixel to
        # pixel). See satellite_band_wavelength_um_<band>'s own long_name.
        "band_wavelength_um": _maybe_float(
            ds.variables[f"satellite_band_wavelength_um_{band}"][slot]
        ),
        # Nominal nadir spatial resolution, for the GeoSat-Gallery info
        # panel -- per-satellite, fixed by that satellite's sensor
        # design, distinct from pixel_resolution_km (actual per-pixel
        # value, degrades away from nadir). See
        # satellite_spatial_resolution_km_<band>'s own long_name.
        "spatial_resolution_km": _maybe_float(
            ds.variables[f"satellite_spatial_resolution_km_{band}"][slot]
        ),
    }


def _read_satellite_metadata(path: Path, band: str) -> dict:
    """Process-pool worker -- module-level so it's importable/picklable
    (fork start method on Linux doesn't strictly need this, but keeping it
    a plain top-level function avoids relying on that).

    Args:
        path: Path to the mosaic `.nc` file to read.
        band: Band name, e.g. `"mir"`.

    Returns:
        Dict with `filename`, `band`, `actual_mid_time`, and `satellites`
        (a list of per-slot entries from `_satellite_entry`) keys -- the
        response body for `/satellite-metadata`.
    """
    with netCDF4.Dataset(path) as ds:
        # The gallery thumbnail filename (see mosaicThumbnailUrl in the
        # frontend) is "<band>_<target>_<actual-mid>.png" -- "target" is
        # what's already in the app's own timestamp state (it's literally
        # where the aggregation's time dimension comes from -- the value
        # embedded in this same file's name), but "actual-mid" is this
        # band's own time_<band> scalar, which isn't available anywhere
        # else the frontend can reach (removed from THREDDS's OPeNDAP/WMS
        # aggregation for the same int64/DAP2 reason as the scan times
        # below), so it rides along in this same response.
        actual_mid_time = _iso(ds.variables[f"time_{band}"])
        slot_count = ds.dimensions[f"satellite_{band}"].size
        satellites = [_satellite_entry(ds, band, slot) for slot in range(slot_count)]

    return {
        "filename": path.name,
        "band": band,
        "actual_mid_time": actual_mid_time,
        "satellites": satellites,
    }


@app.get("/satellite-metadata")
async def satellite_metadata(
    band: str = Query(...),
    time: str = Query(...),
) -> dict:
    """Per-satellite metadata table for the info button --
    key/longitude/scan-time/wavelength/
    spatial-resolution for every satellite slot contributing to this
    band+timestamp's mosaic. THREDDS's NCSS grid endpoint can't serve
    these fields (see module docstring), so this reads the raw `.nc` file
    directly (cached/coalesced via `_cached_call`).

    Args:
        band: Band name; must be one of `BANDS`.
        time: ISO 8601 timestamp string identifying the mosaic file (as
            emitted by THREDDS's own aggregated time dimension).

    Returns:
        Dict with `filename`, `band`, `actual_mid_time`, and `satellites`
        keys, as built by `_read_satellite_metadata`.

    Raises:
        HTTPException: 400 if `band` is not in `BANDS` or `time` can't be
            parsed; 404 if no file exists for that timestamp.
    """
    if band not in BANDS:
        raise HTTPException(400, f"band must be one of {sorted(BANDS)}")

    try:
        filename = _filename_for(time)
    except ValueError:
        raise HTTPException(400, f"could not parse timestamp: {time!r}")

    path = DATA_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"no file for that timestamp ({filename})")

    return await _cached_call(
        _satellite_metadata_cache,
        _satellite_metadata_inflight,
        (filename, band),
        _read_satellite_metadata,
        path,
        band,
    )


def _probe_file(path: Path, band: str, lat: float, lon: float) -> dict:
    """Per-pixel value + contributing satellite detail for one file --
    the shared core of /point-probe (one file) and /timeseries (many
    files, dispatched concurrently via the process pool, see below).
    Nearest-neighbor lookup against the raw file's own 1-D lat/lon
    coordinate arrays -- lat/lon are only 7403/20038 elements, a plain
    argmin over the whole array is well under a millisecond, no need for
    anything cleverer. Deliberately bypasses THREDDS's own NCSS
    grid-as-point mechanism entirely -- see this module's docstring, it's
    unsafe against this dataset in two different ways: silently wrong
    pixel, or a Tomcat OOM crash.

    Process-pool worker (see module docstring's "Concurrency" section) --
    always runs inside a pool worker process now, never called directly
    from a route in the main process, so it doesn't need its own
    thread-safety guard.

    Args:
        path: Path to the mosaic `.nc` file to read.
        band: Band name, e.g. `"mir"`.
        lat: Target latitude in degrees.
        lon: Target longitude in degrees.

    Returns:
        Dict with `filename`, `lat`, `lon` (the actual matched pixel's
        coordinates), `value`, `pixel_resolution_km`, `primary`,
        `is_blended`, `runner_up`, `blend_weight_winner`, and
        `satellite_count` keys.
    """
    with netCDF4.Dataset(path) as ds:
        y = int(np.argmin(np.abs(ds.variables["lat"][:] - lat)))
        x = int(np.argmin(np.abs(ds.variables["lon"][:] - lon)))
        pixel_lat = float(ds.variables["lat"][y])
        pixel_lon = float(ds.variables["lon"][x])

        value = ds.variables[f"value_{band}"][y, x]
        # Actual per-pixel ground resolution, not the nominal per-satellite
        # nadir figure (satellite_spatial_resolution_km_<band>, also
        # available) -- this grid variable already accounts for
        # view-zenith-angle degradation away from nadir, per its own
        # long_name.
        pixel_resolution_km = _maybe_float(
            ds.variables[f"pixel_resolution_km_{band}"][y, x]
        )
        satellite_id = int(ds.variables[f"satellite_id_{band}"][y, x])
        is_blended = bool(ds.variables[f"is_blended_{band}"][y, x])

        # satellite_id_<band>'s flag value doesn't index directly into the
        # satellite_<band> dimension (slot order and flag-value order are
        # independently assigned -- e.g. flag_values
        # [0 2 1 4 6 3] against slots [goes-18 goes-19 meteosat-09
        # himawari-09 meteosat-10]) -- match against satellite_<band> (the
        # per-slot flag-value array) to find which slot it actually
        # is, rather than assuming/hardcoding an order.
        flag_per_slot = ds.variables[f"satellite_{band}"][:]

        def resolve(flag_value: int) -> dict | None:
            if flag_value == 0:  # "no_data", not a real contributing slot
                return None
            matches = np.flatnonzero(flag_per_slot == flag_value)
            if matches.size == 0:
                return None
            return _satellite_entry(ds, band, int(matches[0]))

        primary = resolve(satellite_id)
        runner_up = None
        blend_weight_winner = None
        if is_blended and primary is not None:
            runner_up_id = int(ds.variables[f"runner_up_satellite_id_{band}"][y, x])
            runner_up = resolve(runner_up_id)
            blend_weight_winner = float(
                ds.variables[f"blend_weight_winner_{band}"][y, x]
            )

        # Per-file "how many of the possible satellite slots actually had
        # data this cycle" -- a "Reprocessing
        # disclaimer" informational label (e.g. "5/6 satellites present").
        # Deliberately just a raw count here, not "N/6" -- the max isn't
        # hardcoded anywhere in this codebase (it's whatever
        # satellite_key_<band>'s length actually is, which varies
        # file-to-file, e.g. 5 vs 6), so the frontend derives "out of how
        # many" from the actual data it has (the max seen across a fetched
        # series) rather than either side hardcoding a satellite-count
        # constant that could go stale if a satellite is added/retired.
        satellite_count = ds.dimensions[f"satellite_{band}"].size

    return {
        "filename": path.name,
        "lat": pixel_lat,
        "lon": pixel_lon,
        "value": None if np.ma.is_masked(value) else float(value),
        "pixel_resolution_km": pixel_resolution_km,
        "primary": primary,
        "is_blended": is_blended,
        "runner_up": runner_up,
        "blend_weight_winner": blend_weight_winner,
        "satellite_count": satellite_count,
    }


def _read_minmax(path: Path, band: str) -> dict:
    """Process-pool worker -- see /minmax's docstring for why this is
    correct (not just a THREDDS-parity approximation).

    Reworked 2026-08-16 to work in packed int16 space instead of decoding
    to the default float64 -- necessary, not just a nice-to-have,
    after the /minmax pre-warm job's own backfill (2+ concurrent full-file
    reads) pushed the whole 15Gi WSL2 box into swap thrashing (same
    pathology as the earlier xcube OOM the same day). The default
    netCDF4 auto-mask-and-scale path decodes the full ~7403x20038 grid to
    float64 (~1.2GB per call) plus a parallel boolean mask array -- with
    4 process-pool workers, worst case that's 4-6GB just for /minmax
    alone, on a box already sharing its budget with xcube/tomcat.
    `set_auto_maskandscale(False)` reads the raw packed int16 array
    instead (~4x smaller, ~300MB), and the packing-guarantee this
    endpoint already depends on for correctness (mosaic_io.py's
    _scale_offset_from_range: packed(true_min) == -32767, packed(true_max)
    == +32767 by construction) means finding min/max in packed-integer
    space and decoding just the two resulting scalars is exactly as
    correct as decoding the whole array first -- just cheaper.

    Args:
        path: Path to the mosaic `.nc` file to read.
        band: Band name, e.g. `"mir"`.

    Returns:
        On success, dict with `band`, `min`, `max` keys (decoded to real
        physical units). On failure (no valid data), dict with a single
        `error` key describing the problem -- checked by the `/minmax`
        route, which converts it into an HTTPException.
    """
    with netCDF4.Dataset(path) as ds:
        var = ds.variables[f"value_{band}"]
        var.set_auto_maskandscale(False)
        raw = var[:]
        fill_value = var._FillValue
        scale_factor = var.scale_factor
        add_offset = var.add_offset

    valid = raw[raw != fill_value]
    if valid.size == 0:
        return {"error": f"no valid {band} data in {path.name}"}

    packed_min = int(valid.min())
    packed_max = int(valid.max())
    return {
        "band": band,
        "min": float(packed_min) * scale_factor + add_offset,
        "max": float(packed_max) * scale_factor + add_offset,
    }


@app.get("/minmax")
async def minmax(
    band: str = Query(...),
    time: str = Query(...),
) -> dict:
    """True min/max of value_<band> for a timestamp -- replaces THREDDS's
    WMS GetMetadata&item=minmax (backs the colorbar's "reset to auto
    range" button, lib/thredds.ts's fetchMinMax).

    THREDDS's version was both slow (~26s for this app's near-global
    bbox -- EDAL resamples to the request's own width/
    height before computing min/max, so it pays decompression cost
    over most of the grid) and, it turns out, subtly wrong: that ~26s
    number was also not the true data range, because EDAL's resample-then-
    minmax only sees the coarse output grid, not the full native array --
    it can (and does) miss extreme pixels that a
    coarse regular sample just doesn't happen to land on. This endpoint's
    answer is simpler AND provably correct instead of merely faster: read
    the full native array and take the true min/max directly. That's safe
    specifically because of how mosaic_io.py packs this variable
    (_scale_offset_from_range) -- scale_factor/add_offset are computed
    FROM the file's own true min/max so that packed(true_min) == -32767
    and packed(true_max) == +32767 exactly, by construction. So the
    packed-domain floor/ceiling isn't a fill value or a sentinel to filter
    out (an early version of this endpoint wrongly assumed exactly that,
    since -32767 initially looked identical to a THREDDS-reported "-1.921
    min" that a stale code comment elsewhere blamed on "an unmasked fill-
    value pixel" -- that comment was itself mistaken) -- decoding it is
    *guaranteed* to hand back the extreme value. No bbox/width/height
    params needed either: this file's own lat/lon coordinate arrays
    already span exactly this app's coverage extent (-66.49102..66.49102
    lat), so there's no wider/narrower region to clip to in the first
    place. Reading+scanning the full ~7403x20038 array is ~3.5s uncached
    (still a wait, but a small fraction of THREDDS's ~26s, and the number
    itself is correct rather than an artifact of sparse sampling).

    Args:
        band: Band name; must be one of `BANDS`.
        time: ISO 8601 timestamp string identifying the mosaic file.

    Returns:
        Dict with `band`, `min`, `max` keys.

    Raises:
        HTTPException: 400 if `band` is not in `BANDS` or `time` can't be
            parsed; 404 if no file exists for that timestamp, or if the
            file has no valid data for `band`.
    """
    if band not in BANDS:
        raise HTTPException(400, f"band must be one of {sorted(BANDS)}")

    try:
        filename = _filename_for(time)
    except ValueError:
        raise HTTPException(400, f"could not parse timestamp: {time!r}")

    path = DATA_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"no file for that timestamp ({filename})")

    result = await _cached_call(_minmax_cache, _minmax_inflight, (filename, band), _read_minmax, path, band)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.get("/point-probe")
async def point_probe(
    band: str = Query(...),
    time: str = Query(...),
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict:
    """Per-pixel value + contributing satellite detail for a map click.
    Not cached -- lat/lon varies too
    much request-to-request to be worth it -- but still dispatched via the
    process pool for concurrency safety (see module docstring).

    Args:
        band: Band name; must be one of `BANDS`.
        time: ISO 8601 timestamp string identifying the mosaic file.
        lat: Target latitude in degrees, -90 to 90.
        lon: Target longitude in degrees, -180 to 180.

    Returns:
        Dict with `band` plus the keys from `_probe_file` (`filename`,
        `lat`, `lon`, `value`, `pixel_resolution_km`, `primary`,
        `is_blended`, `runner_up`, `blend_weight_winner`,
        `satellite_count`).

    Raises:
        HTTPException: 400 if `band` is not in `BANDS` or `time` can't be
            parsed; 404 if no file exists for that timestamp.
    """
    if band not in BANDS:
        raise HTTPException(400, f"band must be one of {sorted(BANDS)}")

    try:
        filename = _filename_for(time)
    except ValueError:
        raise HTTPException(400, f"could not parse timestamp: {time!r}")

    path = DATA_DIR / filename
    if not path.exists():
        raise HTTPException(404, f"no file for that timestamp ({filename})")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(_process_pool, _probe_file, path, band, lat, lon)
    return {"band": band, **result}


class TimeseriesRequest(BaseModel):
    """Request body for `POST /timeseries`.

    Attributes:
        band: Band name; must be one of `BANDS`.
        lat: Target latitude in degrees.
        lon: Target longitude in degrees.
        times: ISO 8601 timestamp strings to probe at this point, one per
            series sample.
    """

    band: str
    lat: float
    lon: float
    times: list[str]


@app.post("/timeseries")
async def timeseries(request: TimeseriesRequest) -> dict:
    """Per-point value + accountability across many timestamps at a fixed
    lat/lon, for the Plotly timeseries chart -- same accountability info as
    the point-probe result, shown on hover.
    Reuses the exact same per-file lookup as /point-probe (_probe_file),
    just run across every requested timestamp instead of one.

    Deliberately does NOT implement a "might still change" mutability
    flag for the reprocessing disclaimer on the timeseries/
    analysis plot -- that would be driven by whether a timestamp is
    a key in schedule_pipeline_state.json's `partial` dict, but no such file
    or any writer for it exists anywhere in the backend pipeline.
    The informational satellite-count label
    (`satellite_count` in the response) is implemented -- only the
    mutability-flag half has no data source to drive it in this deployment.

    Args:
        request: Parsed `TimeseriesRequest` body (band, lat, lon, times).

    Returns:
        Dict with `band`, `lat`, `lon`, and `points` (a list, one entry
        per requested timestamp, each either the `_probe_file` result
        dict merged with `time`, or an `{"time": ..., "error": ...}`
        dict for a timestamp that failed to parse, has no matching file,
        or raised during the probe).

    Raises:
        HTTPException: 400 if `request.band` is not in `BANDS` or
            `request.times` is empty.
    """
    if request.band not in BANDS:
        raise HTTPException(400, f"band must be one of {sorted(BANDS)}")
    if not request.times:
        raise HTTPException(400, "times must not be empty")

    loop = asyncio.get_running_loop()

    async def probe_one(time: str) -> dict:
        """Resolves one requested timestamp to a probe result (or an
        inline error dict) without raising, so one bad timestamp in the
        batch doesn't fail the whole `/timeseries` request.

        Args:
            time: ISO 8601 timestamp string to probe.

        Returns:
            `{"time": time, **_probe_file(...)}` on success, or
            `{"time": time, "error": ...}` if the timestamp can't be
            parsed, has no matching file, or the probe itself raised.
        """
        try:
            filename = _filename_for(time)
        except ValueError:
            return {"time": time, "error": f"could not parse timestamp: {time!r}"}
        path = DATA_DIR / filename
        if not path.exists():
            return {"time": time, "error": f"no file for that timestamp ({filename})"}
        try:
            result = await loop.run_in_executor(
                _process_pool, _probe_file, path, request.band, request.lat, request.lon
            )
            return {"time": time, **result}
        except Exception as exc:  # noqa: BLE001 -- surface per-point, don't fail the whole batch
            return {"time": time, "error": str(exc)}

    # Concurrent now, throttled to _process_pool's max_workers -- was
    # forced sequential before because the old ThreadPoolExecutor attempt
    # shared one process's HDF5 state across threads (genuinely unsafe,
    # see module docstring). Process-pool dispatch doesn't have that
    # problem: each file read gets its own independent interpreter/HDF5
    # state, so gathering these concurrently is safe, not just faster.
    points = list(await asyncio.gather(*(probe_one(time) for time in request.times)))

    return {"band": request.band, "lat": request.lat, "lon": request.lon, "points": points}


class PruneCacheRequest(BaseModel):
    """Request body for `POST /_internal/prune-cache`.

    Attributes:
        valid_filenames: Current on-disk mosaic filenames -- cache entries
            for any filename not in this list are dropped.
    """

    valid_filenames: list[str]


@app.post("/_internal/prune-cache")
def prune_cache(body: PruneCacheRequest, http_request: Request) -> dict:
    """Called by prewarm_minmax.py after each pre-warm run to drop cache
    entries for files that have left the rolling retention window --
    without this, _minmax_cache/_satellite_metadata_cache only ever grow
    (harmless correctness-wise, since a pruned-but-still-on-disk
    entry is never actually requested again, but an unbounded leak over
    long uptimes otherwise). `valid_filenames` should be the prewarm
    job's own current DATA_DIR listing -- this endpoint doesn't touch the
    filesystem itself, just diffs the cache's keys against whatever list
    it's given, so it stays a pure in-memory operation regardless of disk
    state at call time.

    Shared-secret protected: this is an internal maintenance hook, not a
    public API surface, and unlike every other route here it has no
    meaningful input validation (any caller can hand it an arbitrary
    list). It was reachable over the public
    internet with no restriction at all as of 2026-08-20 -- with wildcard CORS and no
    auth on this app, anyone could POST an empty `valid_filenames` and
    wipe the whole cache on demand, forcing every request back to a
    2-5s recompute (see the two _*_cache module comments above).

    First fix attempt checked `request.client.host == "127.0.0.1"`
    instead of a secret -- proven bypassable right after being added:
    uvicorn trusts `X-Forwarded-For` by default, so a plain
    `curl -H "X-Forwarded-For: 127.0.0.1"` from anywhere satisfied it.
    Worse, it wouldn't have fixed anything even without that header,
    since both nginx and the Cloudflare Tunnel connect to this app over
    loopback -- genuinely-external and genuinely-local callers are
    indistinguishable at the TCP level here regardless of headers. A
    shared secret (compared with `secrets.compare_digest` to avoid a
    timing side-channel) is the only check in this topology that
    actually distinguishes "knows the secret" from "anyone on the
    internet," which is the property this endpoint needs.

    Args:
        body: Parsed `PruneCacheRequest` body, containing the current
            on-disk filename list.
        http_request: The raw Starlette `Request`, used to read the
            `X-Internal-Secret` header (not a path/query param, so not
            expressed as a `Query`/model field).

    Returns:
        Dict with a `removed` key: total number of cache entries dropped
        across `_minmax_cache` and `_satellite_metadata_cache`.

    Raises:
        HTTPException: 403 if the `X-Internal-Secret` header is missing
            or doesn't match `_INTERNAL_SECRET`.
    """
    provided = http_request.headers.get("X-Internal-Secret", "")
    if not secrets.compare_digest(provided, _INTERNAL_SECRET):
        raise HTTPException(403, "missing or invalid X-Internal-Secret header")
    valid = set(body.valid_filenames)
    removed = 0
    for cache in (_minmax_cache, _satellite_metadata_cache):
        stale_keys = [key for key in cache if key[0] not in valid]
        for key in stale_keys:
            del cache[key]
            removed += 1
    return {"removed": removed}
