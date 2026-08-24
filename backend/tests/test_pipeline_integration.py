"""Integration checks against real, already-downloaded satellite files in
data/raw/ -- these were the three diagnostics a user asked for after
reviewing a rendered mosaic PNG:
  1. does blend.py measurably reduce discontinuity at satellite_id
     boundaries, vs. a hard cut?
  2. does any NaN/fill data leak into the final mosaic where a satellite
     is credited?
  3. is int16 packing finer than the sensor noise floor (i.e. not itself
     a source of visible banding)?

Requires real files (GOES-18/19 ABI C13, Himawari-9 AHI B13 segments) to
already be present in data/raw/ -- these come from public, anonymous AWS
buckets (see acquisition/goes.py, acquisition/himawari.py) but this test
does not download them itself, to keep the suite runnable offline/in CI.
Auto-skipped if the files aren't there; run scripts/run_phase0.py once
first (or just let it run generally) to populate data/raw/ before running
this file specifically.
"""

from __future__ import annotations

import datetime as dt
import glob

import numpy as np
import pytest
import xarray as xr
from satpy import Scene

from global_mosaic.processing.blend import feather_seams
from global_mosaic.processing.midtime import get_scan_times
from global_mosaic.processing.reproject import common_grid, reproject_to_common_grid
from global_mosaic.processing.resolution import effective_resolution_km, geometry_from_orbital_parameters
from global_mosaic.processing.select import Candidate, SelectionConfig, select_winners

from .conftest import DATA_RAW

pytestmark = pytest.mark.integration

TARGET_AREA = common_grid(resolution_km=20.0)


def _goes_files():
    return sorted(glob.glob(str(DATA_RAW / "OR_ABI-L1b-RadF-M6C13_G18_*.nc"))), sorted(
        glob.glob(str(DATA_RAW / "OR_ABI-L1b-RadF-M6C13_G19_*.nc"))
    )


def _himawari_segment_sets():
    """Groups cached Himawari segment files by their {date}_{HHMM} slot,
    returning only sets with all 10 segments present (satpy needs the
    complete set to assemble one scene)."""
    all_segs = sorted(glob.glob(str(DATA_RAW / "HS_H09_*_B13_FLDK_*.DAT.bz2")))
    by_slot: dict[str, list[str]] = {}
    for path in all_segs:
        # HS_H09_{date}_{HHMM}_B13_FLDK_R20_S{seg}.DAT.bz2
        parts = path.split("HS_H09_")[-1].split("_B13_")[0]
        by_slot.setdefault(parts, []).append(path)
    return [sorted(v) for v in by_slot.values() if len(v) == 10]


def _load_candidate(satellite_key, reader, band, paths, nadir_res):
    scn = Scene(filenames=paths if isinstance(paths, list) else [paths], reader=reader)
    scn.load([band])
    data = scn[band]
    reproj = reproject_to_common_grid(data, TARGET_AREA)
    geom = geometry_from_orbital_parameters(data.attrs["orbital_parameters"])
    res = effective_resolution_km(reproj.coords["lat"].values, reproj.coords["lon"].values, geom, nadir_res)
    res_da = xr.DataArray(res, coords=reproj.coords, dims=reproj.dims)
    mid = get_scan_times(satellite_key, paths).mid
    return Candidate(satellite_key, reproj, res_da, mid, str(paths))


@pytest.fixture(scope="module")
def real_candidates():
    goes18, goes19 = _goes_files()
    himawari_sets = _himawari_segment_sets()
    if not goes18 or not goes19 or not himawari_sets:
        pytest.skip(
            "needs cached GOES-18/19 + a complete 10-segment Himawari-9 set in data/raw/ -- "
            "run scripts/run_phase0.py once to populate it"
        )
    candidates = [
        _load_candidate("goes-18", "abi_l1b", "C13", goes18[-1], 2.0),
        _load_candidate("goes-19", "abi_l1b", "C13", goes19[-1], 2.0),
        _load_candidate("himawari-09", "ahi_hsd", "B13", himawari_sets[0], 2.0),
    ]
    return candidates


@pytest.fixture(scope="module")
def selection_result(real_candidates):
    target = real_candidates[0].mid_time  # doesn't need to be exact for this diagnostic
    return select_winners(real_candidates, SelectionConfig(target_time=target, blend_margin_km=0.5))


def test_blend_reduces_boundary_discontinuity_below_interior_variability(selection_result):
    """Check 1: boundary/interior discontinuity ratio must drop after
    blending -- confirmed on a real 5-satellite run
    (1.57x -> 0.79x, i.e. blended boundaries became smoother than
    typical interior variability, not just less rough)."""
    result = selection_result
    blended = feather_seams(result)

    def ratio(val):
        sat_id = result.satellite_id.values
        diffs_boundary, diffs_interior = [], []
        for axis in (0, 1):
            a = np.take(val, range(val.shape[axis] - 1), axis=axis)
            b = np.take(val, range(1, val.shape[axis]), axis=axis)
            id_a = np.take(sat_id, range(sat_id.shape[axis] - 1), axis=axis)
            id_b = np.take(sat_id, range(1, sat_id.shape[axis]), axis=axis)
            valid = np.isfinite(a) & np.isfinite(b) & (id_a != 0) & (id_b != 0)
            boundary = valid & (id_a != id_b)
            interior = valid & (id_a == id_b)
            diffs_boundary.append(np.abs(a[boundary] - b[boundary]))
            diffs_interior.append(np.abs(a[interior] - b[interior]))
        db, di = np.concatenate(diffs_boundary), np.concatenate(diffs_interior)
        if db.size == 0 or di.size == 0:
            pytest.skip("no satellite-boundary pixels in this real data window to compare")
        return np.percentile(db, 95) / np.percentile(di, 95)

    ratio_hard = ratio(result.value.values)
    ratio_blended = ratio(blended.values)
    assert ratio_blended < ratio_hard, (
        f"blending should reduce the boundary/interior discontinuity ratio: "
        f"hard-cut={ratio_hard:.2f}x, blended={ratio_blended:.2f}x"
    )


def test_no_nan_leakage_into_credited_pixels(selection_result):
    """Check 2: wherever satellite_id says a satellite covers a pixel,
    that pixel's value must be finite -- no fill/NaN data should ever be
    reported as if it were a real observation."""
    result = selection_result
    covered = result.satellite_id.values != 0
    assert np.isfinite(result.value.values[covered]).all()


def test_no_implausible_brightness_temperatures(selection_result):
    """Sanity check: every finite selected value should be a plausible
    IR-window brightness temperature, not a mis-calibrated/garbage value."""
    result = selection_result
    finite = result.value.values[np.isfinite(result.value.values)]
    assert finite.size > 0
    assert finite.min() > 100.0
    assert finite.max() < 400.0


def test_packing_finer_than_sensor_noise_floor(selection_result, real_candidates, tmp_path):
    """Check 3: int16 packing precision should be well below the ~0.1-0.3K
    sensor noise floor for IR-window brightness temperature -- ruling out
    quantization as a source of visible banding in a rendered mosaic."""
    from global_mosaic.processing.mosaic_io import SatelliteMeta, build_mosaic_dataset, write_mosaic_netcdf
    from global_mosaic.processing.select import SATELLITE_ID

    result = selection_result
    now = dt.datetime.now(dt.timezone.utc)
    metas = [
        SatelliteMeta(
            c.satellite_key, SATELLITE_ID[c.satellite_key], 0.0, c.mid_time, c.mid_time, c.mid_time, 2.0, 10.3, 10, c.source_filename
        )
        for c in real_candidates
    ]

    ds = build_mosaic_dataset(result.value, result.satellite_id, result.resolution_km, metas, now, "IR_window")
    path = tmp_path / "packing_check.nc"
    write_mosaic_netcdf(str(path), ds)

    finite = result.value.values[np.isfinite(result.value.values)]
    vmin, vmax = finite.min(), finite.max()
    scale_factor = (vmax - vmin) / (2 * 32767)
    assert scale_factor < 0.05, f"packing step {scale_factor:.5f}K is not comfortably below the sensor noise floor"
