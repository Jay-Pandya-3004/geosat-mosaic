"""Unit tests for processing/mosaic_io.py: CF NetCDF packing precision and
the blend-accountability fields (is_blended/runner_up_satellite_id/
blend_weight_winner). All synthetic, writes to a pytest tmp_path."""

from __future__ import annotations

import datetime as dt
import tempfile
from pathlib import Path

import netCDF4
import numpy as np
import pytest
import xarray as xr

from global_mosaic.processing.blend import feather_seams
from global_mosaic.processing.mosaic_io import (
    SatelliteMeta,
    build_mosaic_dataset,
    compute_blend_accountability_fields,
    finalize_tiled_band_writer,
    merge_tiled_band_files,
    open_tiled_band_writer,
    write_band_tile,
    write_mosaic_netcdf,
)
from global_mosaic.processing.select import Candidate, SelectionConfig, select_winners
from global_mosaic.processing.tiling import TileRowRange

TARGET = dt.datetime(2026, 7, 30, 12, 0, 0)


def _grid_da(values, lat, lon):
    coords = {"lat": (("y", "x"), np.array(lat, dtype=float)), "lon": (("y", "x"), np.array(lon, dtype=float))}
    return xr.DataArray(np.array(values, dtype=float), coords=coords, dims=["y", "x"])


def _lat_lon_grid(shape=(4, 4)):
    lat = np.tile(np.linspace(-1, 1, shape[0]).reshape(-1, 1), (1, shape[1]))
    lon = np.tile(np.linspace(-1, 1, shape[1]).reshape(1, -1), (shape[0], 1))
    return lat, lon


def _make_metas():
    return [
        SatelliteMeta("goes-19", 1, -75.2, TARGET, TARGET, TARGET, 2.0, 10.33, 10, "fake_goes.nc"),
        SatelliteMeta("himawari-09", 6, 140.7, TARGET, TARGET, TARGET, 2.0, 10.4, 10, "fake_himawari.dat"),
    ]


def test_packing_round_trip_precision(tmp_path):
    """Regression test: packing was computed as add_offset=vmin (spanning
    packed values [0, 65534]), which overflows/wraps a signed int16
    ([-32768, 32767]) and silently corrupts the data (one run decoded
    187-323K as 118-255K). The fix centers add_offset at the value
    midpoint. Asserts round-trip error is tiny relative to the value's own
    dynamic range across realistic physical ranges, including ones that
    previously overflowed."""
    lat, lon = _lat_lon_grid()
    for vmin, vmax in [(187.0, 323.0), (-50.0, 50.0), (0.0, 1.0), (1000.0, 1000.1)]:
        rng = np.random.default_rng(0)
        raw = rng.uniform(vmin, vmax, size=(4, 4))
        value = _grid_da(raw, lat, lon)
        satellite_id = _grid_da(np.ones((4, 4)), lat, lon)
        resolution_km = _grid_da(np.full((4, 4), 2.0), lat, lon)
        metas = [SatelliteMeta("goes-19", 1, -75.2, TARGET, TARGET, TARGET, 2.0, 10.33, 10, "f.nc")]

        ds = build_mosaic_dataset(value, satellite_id, resolution_km, metas, TARGET, "test_band")
        path = tmp_path / f"round_trip_{vmin}_{vmax}.nc"
        write_mosaic_netcdf(str(path), ds)

        reopened = xr.open_dataset(path)
        max_err = float(np.abs(reopened["value"].values - raw).max())
        dynamic_range = vmax - vmin
        # Should be within a few ten-thousandths of the dynamic range --
        # nowhere near the ~50% corruption the overflow bug caused.
        assert max_err < dynamic_range * 1e-3 + 1e-6, f"range [{vmin},{vmax}]: max_err={max_err}"


def test_nan_preserved_through_packing(tmp_path):
    lat, lon = _lat_lon_grid()
    raw = np.array([[1.0, 2.0, np.nan, 4.0], [5.0, np.nan, 7.0, 8.0], [9.0] * 4, [10.0] * 4])
    value = _grid_da(raw, lat, lon)
    satellite_id = _grid_da(np.where(np.isnan(raw), 0, 1), lat, lon)
    resolution_km = _grid_da(np.where(np.isnan(raw), np.nan, 2.0), lat, lon)
    metas = [SatelliteMeta("goes-19", 1, -75.2, TARGET, TARGET, TARGET, 2.0, 10.33, 10, "f.nc")]

    ds = build_mosaic_dataset(value, satellite_id, resolution_km, metas, TARGET, "test_band")
    path = tmp_path / "nan_test.nc"
    write_mosaic_netcdf(str(path), ds)

    reopened = xr.open_dataset(path)
    assert np.array_equal(np.isnan(reopened["value"].values), np.isnan(raw))


def test_blend_accountability_fields_reconstruct_original_values(tmp_path):
    """The accountability fix: is_blended/runner_up_satellite_id/
    blend_weight_winner must be enough to reconstruct which satellites
    contributed to a blended pixel and in what proportion -- otherwise
    satellite_id alone silently misreports a mixed pixel as a pure
    single-satellite observation (the gap a user caught by asking whether
    blending loses traceability)."""
    lat, lon = _lat_lon_grid()
    a_val, b_val = 10.0, 20.0
    a_data = _grid_da(np.full((4, 4), a_val), lat, lon)
    a_res = _grid_da(np.full((4, 4), 4.0), lat, lon)
    b_data = _grid_da(np.full((4, 4), b_val), lat, lon)
    b_res = _grid_da(np.full((4, 4), 1.0), lat, lon)
    candidates = [Candidate("goes-19", a_data, a_res, TARGET), Candidate("himawari-09", b_data, b_res, TARGET)]

    result = select_winners(candidates, SelectionConfig(target_time=TARGET, blend_margin_km=3.5))
    blended_value = feather_seams(result)
    assert result.tie_mask.values.all()  # every pixel ties in this synthetic setup

    ds = build_mosaic_dataset(
        blended_value,
        result.satellite_id,
        result.resolution_km,
        _make_metas(),
        TARGET,
        "test_band",
        tie_mask=result.tie_mask,
        runner_up_satellite_id=result.runner_up_satellite_id,
        runner_up_resolution_km=result.runner_up_resolution_km,
    )
    path = tmp_path / "blend_accountability.nc"
    write_mosaic_netcdf(str(path), ds)
    reopened = xr.open_dataset(path)

    assert (reopened["is_blended"].values == 1).all()
    assert (reopened["satellite_id"].values == 6).all()  # B (finer res) is the primary winner
    assert (reopened["runner_up_satellite_id"].values == 1).all()  # A is the runner-up

    w = reopened["blend_weight_winner"].values
    reconstructed = w * b_val + (1 - w) * a_val
    assert np.allclose(reconstructed, blended_value.values, atol=1e-2)


def test_non_blended_pixels_report_full_weight_to_winner(tmp_path):
    """Where select.py never flagged a tie, blend_weight_winner must be
    exactly 1.0 (not e.g. left at some other default) -- otherwise a
    reader can't tell a pure observation from a blended one."""
    lat, lon = _lat_lon_grid()
    a_data = _grid_da(np.full((4, 4), 10.0), lat, lon)
    a_res = _grid_da(np.full((4, 4), 4.0), lat, lon)
    candidates = [Candidate("goes-19", a_data, a_res, TARGET)]

    result = select_winners(candidates, SelectionConfig(target_time=TARGET))
    ds = build_mosaic_dataset(
        result.value,
        result.satellite_id,
        result.resolution_km,
        [_make_metas()[0]],
        TARGET,
        "test_band",
        tie_mask=result.tie_mask,
        runner_up_satellite_id=result.runner_up_satellite_id,
        runner_up_resolution_km=result.runner_up_resolution_km,
    )
    path = tmp_path / "no_blend.nc"
    write_mosaic_netcdf(str(path), ds)
    reopened = xr.open_dataset(path)

    assert (reopened["is_blended"].values == 0).all()
    assert (reopened["runner_up_satellite_id"].values == 0).all()
    assert np.allclose(reopened["blend_weight_winner"].values, 1.0, atol=1e-4)


def test_runner_up_id_zeroed_when_second_candidate_exists_but_not_tied(tmp_path):
    """Regression test found against a saved mosaic file:
    select.SelectionResult.runner_up_satellite_id is
    populated for *any* pixel with >=2 valid candidates, whether or not
    they were close enough to actually blend (see that field's own
    docstring) -- build_mosaic_dataset must mask it down to 0 wherever
    is_blended is 0, matching its documented contract. A prior version
    forgot this mask, so a satellite with a clear (non-tied) win still
    reported a nonzero runner_up_satellite_id in the written file. The
    single-candidate test above can't catch this: with only one
    candidate, select.py never computes a runner-up at all, so this
    needs a genuine two-candidate, non-tied scenario."""
    lat, lon = _lat_lon_grid()
    a_data = _grid_da(np.full((4, 4), 10.0), lat, lon)
    a_res = _grid_da(np.full((4, 4), 1.0), lat, lon)  # much finer -> clear winner
    b_data = _grid_da(np.full((4, 4), 20.0), lat, lon)
    b_res = _grid_da(np.full((4, 4), 10.0), lat, lon)  # far coarser -> not a tie
    candidates = [Candidate("goes-19", a_data, a_res, TARGET), Candidate("himawari-09", b_data, b_res, TARGET)]

    result = select_winners(candidates, SelectionConfig(target_time=TARGET, blend_margin_km=0.5))
    assert not result.tie_mask.values.any(), "test setup should not produce a tie"
    assert result.runner_up_satellite_id is not None
    assert (result.runner_up_satellite_id.values != 0).all(), "select.py itself should still report a runner-up"

    ds = build_mosaic_dataset(
        result.value,
        result.satellite_id,
        result.resolution_km,
        _make_metas(),
        TARGET,
        "test_band",
        tie_mask=result.tie_mask,
        runner_up_satellite_id=result.runner_up_satellite_id,
        runner_up_resolution_km=result.runner_up_resolution_km,
    )
    path = tmp_path / "runner_up_not_tied.nc"
    write_mosaic_netcdf(str(path), ds)
    reopened = xr.open_dataset(path)

    assert (reopened["is_blended"].values == 0).all()
    assert (reopened["runner_up_satellite_id"].values == 0).all(), (
        "runner_up_satellite_id must be zeroed in the OUTPUT FILE when nothing was actually blended, "
        "even though select.py's raw SelectionResult still names a runner-up candidate"
    )
    assert np.allclose(reopened["blend_weight_winner"].values, 1.0, atol=1e-4)


def test_satellite_metadata_table_round_trips(tmp_path):
    lat, lon = _lat_lon_grid()
    value = _grid_da(np.full((4, 4), 250.0), lat, lon)
    satellite_id = _grid_da(np.ones((4, 4)), lat, lon)
    resolution_km = _grid_da(np.full((4, 4), 2.0), lat, lon)
    metas = _make_metas()

    ds = build_mosaic_dataset(value, satellite_id, resolution_km, metas, TARGET, "test_band")
    path = tmp_path / "metadata_test.nc"
    write_mosaic_netcdf(str(path), ds)
    reopened = xr.open_dataset(path)

    assert list(reopened["satellite_key"].values) == ["goes-19", "himawari-09"]
    assert list(reopened["satellite_longitude"].values) == pytest.approx([-75.2, 140.7])
    assert list(reopened["satellite_source_filename"].values) == ["fake_goes.nc", "fake_himawari.dat"]


def test_time_coordinate_is_median_of_scan_mid_odd_count(tmp_path):
    """Regression test for a real run: 5 satellites' scan_mid values
    sorted to [20:05:00.630275, 20:05:07.15, 20:05:08.4, 20:06:24.3325,
    20:06:27.86] -- median (the middle of 5) must be exactly
    20:05:08.4, matching goes-18's own mid-time in that real file, NOT
    the mean (which would land somewhere between the two clusters) and
    NOT the requested target_time (20:06:00, kept separately as the
    requested_target_time attribute)."""
    lat, lon = _lat_lon_grid()
    value = _grid_da(np.full((4, 4), 250.0), lat, lon)
    satellite_id = _grid_da(np.ones((4, 4)), lat, lon)
    resolution_km = _grid_da(np.full((4, 4), 2.0), lat, lon)

    mids = [
        dt.datetime(2026, 7, 30, 20, 5, 8, 400000),  # goes-18 -- the true median
        dt.datetime(2026, 7, 30, 20, 5, 7, 150000),  # goes-19
        dt.datetime(2026, 7, 30, 20, 6, 27, 860000),  # meteosat-10
        dt.datetime(2026, 7, 30, 20, 6, 24, 332500),  # meteosat-09
        dt.datetime(2026, 7, 30, 20, 5, 0, 630275),  # himawari-09
    ]
    metas = [
        SatelliteMeta(f"sat-{i}", i + 1, 0.0, m, m, m, 2.0, 10.3, 10, f"f{i}.nc") for i, m in enumerate(mids)
    ]
    target = dt.datetime(2026, 7, 30, 20, 6, 0)

    ds = build_mosaic_dataset(value, satellite_id, resolution_km, metas, target, "test_band")

    assert ds["time"].values == np.datetime64(dt.datetime(2026, 7, 30, 20, 5, 8, 400000))

    path = tmp_path / "median_time_odd.nc"
    write_mosaic_netcdf(str(path), ds)
    reopened = xr.open_dataset(path)
    assert reopened["time"].values == np.datetime64(dt.datetime(2026, 7, 30, 20, 5, 8, 400000))
    assert reopened.attrs["requested_target_time"] == target.isoformat()


def test_time_coordinate_is_median_of_scan_mid_even_count():
    """Even-length median must average the two middle values via
    timedelta arithmetic (datetime + datetime is not a valid operation,
    so plain statistics.median would raise TypeError here)."""
    lat, lon = _lat_lon_grid()
    value = _grid_da(np.full((4, 4), 250.0), lat, lon)
    satellite_id = _grid_da(np.ones((4, 4)), lat, lon)
    resolution_km = _grid_da(np.full((4, 4), 2.0), lat, lon)

    mids = [
        dt.datetime(2026, 7, 30, 20, 0, 0),
        dt.datetime(2026, 7, 30, 20, 10, 0),
    ]
    metas = [SatelliteMeta(f"sat-{i}", i + 1, 0.0, m, m, m, 2.0, 10.3, 10, f"f{i}.nc") for i, m in enumerate(mids)]

    ds = build_mosaic_dataset(value, satellite_id, resolution_km, metas, TARGET, "test_band")
    assert ds["time"].values == np.datetime64(dt.datetime(2026, 7, 30, 20, 5, 0))


# --- compute_blend_accountability_fields: extracted-helper unit tests ---
# (build_mosaic_dataset's own tests above already exercise this indirectly
# through the full pipeline; these call it directly to pin its own
# contract, independent of build_mosaic_dataset ever changing.)


def test_compute_blend_accountability_fields_no_blend_info():
    is_blended, blend_weight_winner, runner_up_id = compute_blend_accountability_fields(
        (2, 2), np.full((2, 2), 2.0), None, None, None
    )
    assert (is_blended == 0).all() and is_blended.dtype == np.int8
    assert np.allclose(blend_weight_winner, 1.0)
    assert (runner_up_id == 0).all() and runner_up_id.dtype == np.int8


def test_compute_blend_accountability_fields_full_tie_reconstructs_correctly():
    resolution_km = np.full((2, 2), 4.0)
    runner_up_resolution_km = np.full((2, 2), 1.0)
    tie_mask = np.ones((2, 2), dtype=bool)
    runner_up_satellite_id = np.full((2, 2), 6, dtype=np.int16)

    is_blended, blend_weight_winner, runner_up_id = compute_blend_accountability_fields(
        (2, 2), resolution_km, tie_mask, runner_up_satellite_id, runner_up_resolution_km
    )
    assert (is_blended == 1).all()
    assert (runner_up_id == 6).all()
    # winner_weight = 1/4, runner_weight = 1/1 -> winner's share = 0.25/1.25 = 0.2
    assert np.allclose(blend_weight_winner, 0.2)


def test_compute_blend_accountability_fields_masks_runner_up_when_not_blended():
    """Mirrors test_runner_up_id_zeroed_when_second_candidate_exists_but_
    not_tied above, at the extracted-helper level: a real runner-up id can
    be present even where is_blended is False (select.py populates it for
    any pixel with >=2 candidates, tied or not) -- must be masked to 0."""
    is_blended, _, runner_up_id = compute_blend_accountability_fields(
        (2, 2), np.full((2, 2), 1.0), np.zeros((2, 2), dtype=bool), np.full((2, 2), 6, dtype=np.int16), np.full((2, 2), 10.0)
    )
    assert (is_blended == 0).all()
    assert (runner_up_id == 0).all()


# --- Tiled writer: must match the full-grid path exactly ---


def _synthetic_tile_scenario(height=12, width=6, seed=1, include_sza=True):
    rng = np.random.default_rng(seed)
    lat_1d = np.linspace(10, -1, height).astype(np.float32)
    lon_1d = np.linspace(-5, 5, width).astype(np.float32)
    lat2d = np.tile(lat_1d.reshape(-1, 1), (1, width))
    lon2d = np.tile(lon_1d.reshape(1, -1), (height, 1))

    value = rng.uniform(0, 100, size=(height, width)).astype(np.float32)
    satellite_id = rng.integers(1, 3, size=(height, width)).astype(np.int16)
    resolution_km = rng.uniform(1, 5, size=(height, width)).astype(np.float32)
    tie_mask = rng.random((height, width)) > 0.5
    runner_up_satellite_id = rng.integers(1, 3, size=(height, width)).astype(np.int16)
    runner_up_resolution_km = rng.uniform(1, 5, size=(height, width)).astype(np.float32)
    sza = rng.uniform(0, 180, size=(height, width)).astype(np.float32) if include_sza else None
    metas = [
        SatelliteMeta("himawari-09", 6, 140.7, TARGET, TARGET, TARGET, 1.0, 0.64, 10, "h.nc"),
        SatelliteMeta("goes-19", 1, -75.2, TARGET, TARGET, TARGET, 1.0, 0.64, 10, "g.nc"),
    ]
    return dict(
        height=height, width=width, lat_1d=lat_1d, lon_1d=lon_1d,
        value=value, satellite_id=satellite_id, resolution_km=resolution_km,
        tie_mask=tie_mask, runner_up_satellite_id=runner_up_satellite_id,
        runner_up_resolution_km=runner_up_resolution_km, sza=sza, metas=metas,
    )


def _assert_all_fields_match(ref_ds, tiled_ds, include_sza):
    names = ["value", "satellite_id", "pixel_resolution_km", "is_blended", "runner_up_satellite_id", "blend_weight_winner"]
    if include_sza:
        names += ["solar_zenith_angle_deg", "is_daylit"]
    for name in names:
        a = ref_ds[name].values.astype(float)
        b = tiled_ds[name].values.astype(float)
        assert np.allclose(np.nan_to_num(a, nan=-999), np.nan_to_num(b, nan=-999), atol=1e-1), f"{name} mismatch"
    assert np.array_equal(ref_ds["lat"].values, tiled_ds["lat"].values)
    assert np.array_equal(ref_ds["lon"].values, tiled_ds["lon"].values)
    assert list(ref_ds["satellite_key"].values) == list(tiled_ds["satellite_key"].values)
    assert list(ref_ds["satellite_id"].attrs["flag_values"]) == list(tiled_ds["satellite_id"].attrs["flag_values"])
    assert ref_ds["time"].values == tiled_ds["time"].values


@pytest.mark.parametrize("tile_ranges", [
    [(0, 12)],  # single tile, degenerate case
    [(0, 6), (6, 12)],  # even split
    [(0, 4), (4, 9), (9, 12)],  # non-divisible, 3 uneven tiles
])
def test_tiled_writer_matches_full_grid_writer(tmp_path, tile_ranges):
    """The core correctness claim of the whole tiled-writer design: no
    matter how the same data is split into tiles, the written output must
    be identical to writing it as one full-grid call -- including the
    packed value/pixel_resolution_km/solar_zenith_angle_deg fields, whose
    scale_factor/add_offset can only be known after seeing every tile
    (see finalize_tiled_band_writer's two-pass design)."""
    s = _synthetic_tile_scenario()

    coords = {"lat": (("y", "x"), np.tile(s["lat_1d"].reshape(-1, 1), (1, s["width"]))),
              "lon": (("y", "x"), np.tile(s["lon_1d"].reshape(1, -1), (s["height"], 1)))}
    value_da = xr.DataArray(s["value"], coords=coords, dims=("y", "x"))
    sat_id_da = xr.DataArray(s["satellite_id"], coords=coords, dims=("y", "x"))
    res_da = xr.DataArray(s["resolution_km"], coords=coords, dims=("y", "x"))
    tie_da = xr.DataArray(s["tie_mask"], coords=coords, dims=("y", "x"))
    ru_id_da = xr.DataArray(s["runner_up_satellite_id"], coords=coords, dims=("y", "x"))
    ru_res_da = xr.DataArray(s["runner_up_resolution_km"], coords=coords, dims=("y", "x"))

    ref_ds_in_memory = build_mosaic_dataset(
        value_da, sat_id_da, res_da, s["metas"], TARGET, "VIS",
        value_long_name="VIS test", value_units="%",
        tie_mask=tie_da, runner_up_satellite_id=ru_id_da, runner_up_resolution_km=ru_res_da,
    )
    ref_ds_in_memory["solar_zenith_angle_deg"] = (("y", "x"), s["sza"])
    ref_ds_in_memory["is_daylit"] = (("y", "x"), (s["sza"] < 90).astype(np.int8))
    ref_path = tmp_path / "ref.nc"
    write_mosaic_netcdf(str(ref_path), ref_ds_in_memory)

    final_path = tmp_path / "tiled.nc"
    stage_path = tmp_path / "tiled_stage.nc"
    tiles = [TileRowRange(y0, y1) for y0, y1 in tile_ranges]
    writer = open_tiled_band_writer(
        final_path, stage_path, tiles, (s["height"], s["width"]), s["lat_1d"], s["lon_1d"],
        "VIS", "VIS test", "%", TARGET, include_sza=True,
    )
    for t in tiles:
        write_band_tile(
            writer, t,
            s["value"][t.y0:t.y1, :], s["satellite_id"][t.y0:t.y1, :], s["resolution_km"][t.y0:t.y1, :],
            s["tie_mask"][t.y0:t.y1, :], s["runner_up_satellite_id"][t.y0:t.y1, :], s["runner_up_resolution_km"][t.y0:t.y1, :],
            s["sza"][t.y0:t.y1, :],
        )
    finalize_tiled_band_writer(writer, s["metas"])

    ref = xr.open_dataset(ref_path)
    tiled = xr.open_dataset(final_path)
    _assert_all_fields_match(ref, tiled, include_sza=True)
    ref.close()
    tiled.close()
    assert not stage_path.exists(), "staging file must be deleted by finalize_tiled_band_writer"


def test_tiled_writer_unwritten_categorical_var_is_not_zero_by_default():
    """Guards against a fill-value bug: _FillValue=0 for
    satellite_id/is_blended/runner_up_satellite_id would make xarray's
    default read-side auto-masking silently convert every LEGITIMATE
    zero (an extremely common value for these fields) into NaN. The
    fill value must be genuinely out-of-band (-1), never a valid data
    value -- checked here by creating the writer but deliberately never
    calling write_band_tile, then reading the untouched variable raw
    (auto-mask off) and confirming it's -1, not 0."""
    from global_mosaic.processing.mosaic_io import _FILL_INT8

    assert _FILL_INT8 not in (0, 1), "0 and 1 are real satellite_id/is_blended values -- fill must be out-of-band"

    with tempfile.TemporaryDirectory() as d:
        final_path = Path(d) / "final.nc"
        stage_path = Path(d) / "stage.nc"
        writer = open_tiled_band_writer(
            final_path, stage_path, [TileRowRange(0, 4)], (4, 4),
            np.linspace(1, 4, 4).astype(np.float32), np.linspace(1, 4, 4).astype(np.float32),
            "TEST", "test", "unit", TARGET, include_sza=False,
        )
        writer.final_ds.close()
        writer.stage_ds.close()

        with netCDF4.Dataset(str(final_path), "r") as ds:
            var = ds.variables["satellite_id"]
            var.set_auto_maskandscale(False)
            raw = var[:]
            assert (raw == _FILL_INT8).all(), "never-written region should show the sentinel fill value"
            assert _FILL_INT8 == -1


# --- merge_tiled_band_files: the real fix for the 2026-08-06/07 concurrent-run merge-step OOM ---


def test_merge_tiled_band_files_round_trips_packed_values(tmp_path):
    """merge_tiled_band_files replaced the old xr.open_dataset + xr.merge
    + write_tier_netcdf(ds.to_netcdf()) path, which CF-decoded every
    packed field to full-grid float64 and re-encoded it, and crashed with
    a real numpy.ArrayMemoryError under concurrent-run memory pressure
    even though each per-band input file was already written well within
    its own tiling budget.
    The new path copies raw packed values + encoding attrs directly,
    never decoding to float at all -- this test is the correctness
    guardrail for that rewrite: every packed field must still round-trip
    exactly the same as before, and fields shared across bands (lat/lon/
    crs/solar_zenith_angle_deg/is_daylit) must appear exactly once in the
    merged output, not duplicated or silently dropped."""
    height, width = 2500, 6  # height > _MERGE_CHUNK_ROWS (2000) -- exercises the chunked copy loop across 2 chunks, not just a single whole-array copy
    lat_1d = np.linspace(10, -1, height).astype(np.float32)
    lon_1d = np.linspace(-5, 5, width).astype(np.float32)
    sza = np.random.default_rng(3).uniform(0, 180, size=(height, width)).astype(np.float32)
    metas = [SatelliteMeta("goes-19", 1, -75.2, TARGET, TARGET, TARGET, 2.0, 10.33, 10, "fake_goes.nc")]
    tiles = [TileRowRange(0, 1300), TileRowRange(1300, height)]
    no_blend = np.zeros((height, width), dtype=bool)
    no_runner_up_id = np.zeros((height, width), dtype=np.int16)
    no_runner_up_res = np.zeros((height, width), dtype=np.float32)

    per_band = {}
    for suffix, band_name, seed in [("_vis", "VIS", 11), ("_swir", "SWIR", 22)]:
        rng = np.random.default_rng(seed)
        value = rng.uniform(0, 100, size=(height, width)).astype(np.float32)
        value[0, 0] = np.nan  # a real coverage gap -- must round-trip as NaN through the fill value, not silently become 0 or garbage
        satellite_id = rng.integers(0, 3, size=(height, width)).astype(np.int16)
        resolution_km = rng.uniform(1, 5, size=(height, width)).astype(np.float32)

        final_path = tmp_path / f"tmp_tierb{suffix}.nc"
        stage_path = tmp_path / f"tmp_tierb{suffix}_stage.nc"
        writer = open_tiled_band_writer(
            final_path, stage_path, tiles, (height, width), lat_1d, lon_1d,
            band_name, f"{band_name} test", "%", TARGET, include_sza=True, variable_suffix=suffix,
        )
        for t in tiles:
            write_band_tile(
                writer, t,
                value[t.y0:t.y1, :], satellite_id[t.y0:t.y1, :], resolution_km[t.y0:t.y1, :],
                no_blend[t.y0:t.y1, :], no_runner_up_id[t.y0:t.y1, :], no_runner_up_res[t.y0:t.y1, :],
                sza[t.y0:t.y1, :],
            )
        finalize_tiled_band_writer(writer, metas)
        per_band[suffix] = dict(value=value, satellite_id=satellite_id, path=final_path, stage_path=stage_path)

    out_path = tmp_path / "merged.nc"
    merge_tiled_band_files(
        [per_band["_vis"]["path"], per_band["_swir"]["path"]],
        out_path,
        {"title": "Global geostationary mosaic -- Tier B (VIS, SWIR)", "requested_target_time": TARGET.isoformat()},
    )

    merged = xr.open_dataset(out_path)
    for suffix in ("_vis", "_swir"):
        expected_value = per_band[suffix]["value"]
        actual_value = merged[f"value{suffix}"].values
        assert np.allclose(
            np.nan_to_num(expected_value, nan=-999), np.nan_to_num(actual_value, nan=-999), atol=1e-1
        ), f"value{suffix} did not round-trip correctly through merge"
        assert np.isnan(actual_value[0, 0]), f"value{suffix}'s fill pixel must decode as NaN, not a wrapped garbage value"
        assert np.array_equal(merged[f"satellite_id{suffix}"].values, per_band[suffix]["satellite_id"])

    # Shared-across-bands fields: present exactly once (not duplicated, not dropped).
    assert np.allclose(merged["solar_zenith_angle_deg"].values, sza, atol=1e-2)
    assert np.allclose(merged["lat"].values, lat_1d)
    assert np.allclose(merged["lon"].values, lon_1d)
    assert merged.attrs["title"] == "Global geostationary mosaic -- Tier B (VIS, SWIR)"
    merged.close()

    for suffix in ("_vis", "_swir"):
        assert not per_band[suffix]["stage_path"].exists(), "staging file must still be deleted by finalize_tiled_band_writer"
