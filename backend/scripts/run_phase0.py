#!/usr/bin/env python
"""Phase 0 milestone: build one CF-compliant NetCDF mosaic of the IR-window
band for one target timestamp, across all 6 satellite slots.

For each slot, first resolves which satellite currently occupies it (see
global_mosaic.config.resolve_phase0_slot -- date+longitude based, so a
GOES-16->19 handover or the 2023 Meteosat-10/11 role swap doesn't need a
code change here). Then: acquire the nearest file within a +-5 minute
search window -> compute its scan mid-time (processing/midtime.py, opens
the actual file -- only GOES's mid-time is recoverable from the filename
alone) -> reproject onto the common grid (processing/reproject.py) ->
compute each satellite's per-pixel resolution (processing/resolution.py)
-> select a winner per pixel by sync-then-resolution (processing/select.py)
-> write one CF-compliant, compressed NetCDF (processing/mosaic_io.py).

Deliberately simple and unstructured for this first pass -- the plan is
to restructure once the pipeline shape has settled. Known gaps, not bugs
to fix here:

- INSAT (3D/3DR/3DS): acquisition.insat.download has a MOSDAC
  live-download implementation (built on mdapi_opt5.py's client logic,
  see that module's docstring for why) requiring MOSDAC_USERNAME/
  MOSDAC_PASSWORD env vars; it still checks `data/raw/` for an
  already-present file matching the satellite's filename convention
  FIRST, so manually-placed sample files continue to flow through the
  same pipeline without hitting the network at all. Reader-side wiring
  works against INSAT-3DS files -- see readers/insat3d_img_l1b_h5.yaml.
- Meteosat IODC's eumdac collection ID (SEVIRI_IODC) is unverified
  (acquisition/meteosat.py's own docstring flags this) -- if it 404s
  or returns nothing, that slot is skipped rather than crashing the run.

Usage::

    python scripts/run_phase0.py "2024-06-01T06:00:00"
    python scripts/run_phase0.py "2024-06-01T06:00:00" --resolution-km 20 --sync-threshold-seconds 300
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC_DIR))

# Must be set before satpy is imported anywhere (directly here, or lazily
# inside processing/midtime.py): tells satpy to also look in
# src/global_mosaic/readers/ for reader .yaml configs, so this project's
# patched insat3d_img_l1b_h5 reader is used instead of satpy's own stock
# copy (which crashes on real INSAT-3DS files -- see that yaml's header
# comment for why). Harmless for every other satellite: satpy just finds
# no override yaml there and falls through to its normal stock readers.
os.environ.setdefault("SATPY_CONFIG_PATH", str(_SRC_DIR / "global_mosaic"))

from satpy import Scene  # noqa: E402

from global_mosaic.acquisition import goes, himawari, insat, meteosat  # noqa: E402
from global_mosaic.config import resolve_all_phase0_slots  # noqa: E402
from global_mosaic.processing.midtime import get_scan_times  # noqa: E402
from global_mosaic.processing.mosaic_io import (  # noqa: E402
    SatelliteMeta,
    build_mosaic_dataset,
    write_mosaic_netcdf,
)
from global_mosaic.processing.reproject import common_grid, reproject_to_common_grid  # noqa: E402
from global_mosaic.processing.resolution import (  # noqa: E402
    effective_resolution_km,
    geometry_from_orbital_parameters,
)
from global_mosaic.processing.blend import feather_seams  # noqa: E402
from global_mosaic.processing.select import Candidate, SelectionConfig, select_winners  # noqa: E402
from global_mosaic.satellites.reader_registry import get_reader_spec  # noqa: E402
from global_mosaic.satellites.sat_metadata import Satellites_Objects  # noqa: E402

DEST_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "mosaics"

SEARCH_WINDOW_MINUTES = 5  # +-5 minutes

# meteosat_prime/meteosat_iodc route to an EUMETSAT *collection*, not a
# specific satellite id -- EUMETSAT serves whichever bird is live behind it.
METEOSAT_DATASET_BY_SLOT = {
    "meteosat_prime": "SEVIRI",
    "meteosat_iodc": "SEVIRI_IODC",
}

# IR-window channel number per satellite family, in each family's own
# sat_metadata.py Channels dict -- used only to pull the nominal
# spatial_resolution/band_wavelength for the metadata table (§SatelliteMeta),
# NOT to select which band satpy loads (that's reader_registry.py's
# ir_window_band, a different naming convention on purpose -- see that
# file's docstring).
IR_WINDOW_CHANNEL_NO = {
    "goes": 13,
    "himawari": 13,
    "meteosat": 9,
    "insat": 5,
}


def fetch_slot(slot_name: str, satellite_key: str, target: dt.datetime):
    """Download the nearest file for one slot's resolved satellite, dispatching by family.

    Args:
        slot_name: Logical Phase 0 slot name, used to pick the Meteosat
            dataset (SEVIRI vs SEVIRI_IODC) and for log messages.
        satellite_key: Satellite key resolved for this slot, e.g.
            ``"goes-16"``.
        target: Desired scan time (UTC).

    Returns:
        Tuple of ``(local_paths, source_filename)``, or None if
        unavailable. `local_paths` is a single path (GOES/Meteosat) or a
        list of segment paths (Himawari, which satpy needs together as
        one scene).

    Raises:
        ValueError: If `satellite_key` doesn't match any known
            acquisition family prefix.
    """
    if satellite_key.startswith("goes-"):
        path = goes.download(satellite_key, target, str(DEST_DIR))
        return (path, Path(path).name) if path else None

    if satellite_key.startswith("himawari-"):
        paths = himawari.download(satellite_key, target, str(DEST_DIR))
        return (paths, "+".join(Path(p).name for p in paths)) if paths else None

    if satellite_key.startswith("meteosat-"):
        dataset_name = METEOSAT_DATASET_BY_SLOT.get(slot_name)
        if dataset_name is None:
            return None
        # meteosat.download() has no retry/catch of its own (unlike
        # insat.download() below) -- a EUMETSAT auth/network failure would
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
    """Look up a satellite's IR-window channel's nominal resolution and wavelength.

    Returns (nominal_resolution_km, band_center_wavelength_um) for one
    satellite's IR-window channel, handling two schema differences
    across sat_metadata.py's satellite families:
      - meteosat-10/meteosat-11's Channels field is a descriptive STRING
        ("same band set as meteosat-09...") rather than a per-channel
        dict, to avoid duplicating meteosat-09's full 12-channel table --
        fall back to meteosat-09's dict for those two.
      - SEVIRI channels (meteosat-*) store resolution as a nested
        Spatial_Resolution dict (Instrument_IFOV_Km/Native_Sampling_Km/
        Dataset_Resolution_Km) rather than GOES/Himawari/INSAT's flat
        Spatial_Resolution_Km -- use Dataset_Resolution_Km, the actual
        resolution of the distributed product.

    Args:
        satellite_key: Satellite key, e.g. ``"meteosat-10"``.
        imager: This satellite's ``Instruments["Imager"]`` (or
            equivalent) metadata dict from sat_metadata.py.
        channel_no: IR-window channel number for this satellite's
            family (see ``IR_WINDOW_CHANNEL_NO``).

    Returns:
        Tuple of ``(nominal_resolution_km, band_center_wavelength_um)``.
    """
    channels = imager["Channels"]
    if isinstance(channels, str):
        # meteosat-10/-11 reference meteosat-09's table instead of repeating it.
        fallback_imager = next(iter(Satellites_Objects["meteosat-09"]["Instruments"].values()))
        channels = fallback_imager["Channels"]

    channel = channels[channel_no]
    band_wavelength_um = channel["Central_Wavelength_Um"]

    if "Spatial_Resolution_Km" in channel:
        nominal_resolution_km = channel["Spatial_Resolution_Km"]
    else:
        nominal_resolution_km = channel["Spatial_Resolution"]["Dataset_Resolution_Km"]

    return nominal_resolution_km, band_wavelength_um


def build_candidate(slot_name, satellite_key, target, target_area):
    """Acquire, verify sync, reproject, and compute resolution for one slot.

    Args:
        slot_name: Logical Phase 0 slot name.
        satellite_key: Satellite key resolved for this slot.
        target: Desired scan mid-time (UTC).
        target_area: The common target grid's ``AreaDefinition``.

    Returns:
        Tuple of ``(Candidate, SatelliteMeta)``, or None if this slot
        has nothing usable for this target time (no file found, no
        reader registered, or the scan mid-time falls outside the
        search window).
    """
    acquired = fetch_slot(slot_name, satellite_key, target)
    if acquired is None:
        print(f"  [{slot_name}] {satellite_key}: no file found within the search window, skipping")
        return None
    paths, source_filename = acquired

    spec = get_reader_spec(satellite_key)
    if spec.reader is None:
        print(f"  [{slot_name}] {satellite_key}: no reader registered ({spec.note}), skipping")
        return None
    # Note: spec.custom readers (currently just INSAT) need SATPY_CONFIG_PATH
    # pointed at src/global_mosaic (set at the top of this script) so satpy
    # finds this project's patched reader instead of its own stock copy --
    # see readers/insat3d_img_l1b_h5.yaml's header comment for why that
    # matters.

    scan_times = get_scan_times(satellite_key, paths)
    gap_seconds = abs((scan_times.mid - target).total_seconds())
    if gap_seconds > SEARCH_WINDOW_MINUTES * 60:
        print(
            f"  [{slot_name}] {satellite_key}: real scan mid-time {scan_times.mid} is "
            f"{gap_seconds / 60:.1f} min from target -- outside the +-{SEARCH_WINDOW_MINUTES} "
            f"min search window (confidence: {scan_times.confidence}), skipping"
        )
        return None

    scn = Scene(filenames=paths if isinstance(paths, list) else [paths], reader=spec.reader)
    scn.load([spec.ir_window_band])
    data = scn[spec.ir_window_band]

    reprojected = reproject_to_common_grid(data, target_area)
    geometry = geometry_from_orbital_parameters(data.attrs["orbital_parameters"])

    family = satellite_key.split("-")[0]
    channel_no = IR_WINDOW_CHANNEL_NO[family]
    imager = next(iter(Satellites_Objects[satellite_key]["Instruments"].values()))
    nominal_resolution_km, band_wavelength_um = _channel_wavelength_and_resolution(satellite_key, imager, channel_no)
    temporal_resolution_minutes = imager["Full_Disk_Temporal_Resolution_Minutes"]

    resolution_km = effective_resolution_km(
        reprojected.coords["lat"].values,
        reprojected.coords["lon"].values,
        geometry,
        nominal_resolution_km,
    )
    import xarray as xr  # local import: only needed for this one wrap

    resolution_da = xr.DataArray(resolution_km, coords=reprojected.coords, dims=reprojected.dims)

    candidate = Candidate(satellite_key, reprojected, resolution_da, scan_times.mid, source_filename)
    meta = SatelliteMeta(
        satellite_key=satellite_key,
        satellite_id=0,  # filled in by main() from select.SATELLITE_ID once all candidates are known
        longitude_deg=geometry.sub_satellite_lon_deg,
        scan_start=scan_times.start,
        scan_end=scan_times.end,
        scan_mid=scan_times.mid,
        nominal_spatial_resolution_km=nominal_resolution_km,
        band_center_wavelength_um=band_wavelength_um,
        temporal_resolution_minutes=temporal_resolution_minutes,
        source_filename=source_filename,
    )
    print(
        f"  [{slot_name}] {satellite_key}: OK, real mid-time {scan_times.mid} "
        f"(gap {gap_seconds:.0f}s, confidence {scan_times.confidence})"
    )
    return candidate, meta


def main(
    target: dt.datetime,
    resolution_km: float,
    sync_threshold_seconds: float | None,
    blend_margin_km: float = 0.0,
) -> None:
    """Run the full Phase 0 pipeline for one target timestamp and write the mosaic NetCDF.

    Resolves each of the 6 logical slots to a satellite, acquires and
    reprojects each usable candidate, selects a per-pixel winner
    (optionally feathering seams), and writes the resulting CF-compliant
    NetCDF mosaic to ``OUT_DIR``.

    Args:
        target: ISO 8601 target timestamp to build the mosaic for (UTC).
        resolution_km: Common grid resolution at the equator, in km.
        sync_threshold_seconds: Strict sync mode threshold in seconds,
            or None for best-effort mode (see
            ``SelectionConfig.sync_threshold_seconds``).
        blend_margin_km: Seam-feathering margin in km; 0.0 disables
            blending (hard winner-take-all cut).

    Returns:
        None. Writes the mosaic file and prints progress to stdout.

    Raises:
        SystemExit: If no slot produced usable data for this target time.
    """
    from global_mosaic.processing.select import SATELLITE_ID

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    target_area = common_grid(resolution_km=resolution_km)
    resolved = resolve_all_phase0_slots(target)

    candidates: list[Candidate] = []
    metas: list[SatelliteMeta] = []

    for slot_name, resolution in resolved.items():
        if resolution is None:
            print(f"[{slot_name}] SKIPPED: no satellite covers {target} within fallback radius")
            continue

        print(
            f"[{slot_name}] resolved to {resolution.satellite_key} "
            f"(longitude offset {resolution.longitude_offset_deg:.1f} deg)"
        )
        try:
            result = build_candidate(slot_name, resolution.satellite_key, target, target_area)
        except NotImplementedError as exc:
            print(f"  [{slot_name}] SKIPPED: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 -- surface and keep going, one bad slot shouldn't kill the run
            print(f"  [{slot_name}] FAILED: {exc!r}")
            continue

        if result is None:
            continue
        candidate, meta = result
        meta.satellite_id = SATELLITE_ID[candidate.satellite_key]
        candidates.append(candidate)
        metas.append(meta)

    if not candidates:
        print("\nNo usable data for any slot at this target time -- nothing to write.")
        sys.exit(1)

    print(f"\n{len(candidates)}/6 slots contributed data. Selecting winners...")
    selection_config = SelectionConfig(
        target_time=target, sync_threshold_seconds=sync_threshold_seconds, blend_margin_km=blend_margin_km
    )
    result = select_winners(candidates, selection_config)
    if result.excluded_by_sync:
        print(f"Excluded by sync threshold ({sync_threshold_seconds}s): {result.excluded_by_sync}")

    value = result.value
    if blend_margin_km > 0:
        n_tied = int(result.tie_mask.values.sum())
        value = feather_seams(result)
        print(f"Feathered {n_tied} seam pixels (blend margin {blend_margin_km} km)")

    ds = build_mosaic_dataset(
        value,
        result.satellite_id,
        result.resolution_km,
        metas,
        target,
        band_name="IR_window",
        value_long_name="IR-window radiance/brightness temperature (mixed native units across satellites -- "
        "not yet inter-calibrated, see README's channel-mapping caveat)",
        value_units="mixed",
        # Preserves accountability through blending -- see is_blended/
        # runner_up_satellite_id/blend_weight_winner in mosaic_io.py.
        # Passing these even when blend_margin_km is 0 is harmless: every
        # tie_mask entry is False, so is_blended is all-0 and
        # blend_weight_winner is all-1.0 (a pure single-satellite mosaic,
        # correctly self-describing as such).
        tie_mask=result.tie_mask,
        runner_up_satellite_id=result.runner_up_satellite_id,
        runner_up_resolution_km=result.runner_up_resolution_km,
    )

    out_path = OUT_DIR / f"mosaic_{target.strftime('%Y%m%dT%H%M%S')}.nc"
    write_mosaic_netcdf(str(out_path), ds)
    print(f"\nWrote {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="ISO 8601 target timestamp, e.g. 2024-06-01T06:00:00")
    parser.add_argument(
        "--resolution-km", type=float, default=4.0, help="common grid resolution in km at the equator (default 4.0)"
    )
    parser.add_argument(
        "--sync-threshold-seconds",
        type=float,
        default=None,
        help="strict sync mode: drop any satellite whose real mid-time gap "
        "exceeds this many seconds beyond the best-synced candidate. "
        "Default: best-effort mode, no satellite dropped on sync grounds alone.",
    )
    parser.add_argument(
        "--blend-margin-km",
        type=float,
        default=0.0,
        help="feather seams (processing/blend.py) wherever the winner and "
        "runner-up's actual resolution are within this many km of each "
        "other. Default 0.0: no blending, hard winner-take-all cut.",
    )
    args = parser.parse_args()
    main(
        dt.datetime.fromisoformat(args.target),
        args.resolution_km,
        args.sync_threshold_seconds,
        args.blend_margin_km,
    )
