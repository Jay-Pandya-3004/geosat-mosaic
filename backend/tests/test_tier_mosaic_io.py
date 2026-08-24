"""Unit tests for mosaic_io.py's Phase 2 multi-band tier support
(build_tier_dataset / write_tier_netcdf). All synthetic, writes to a
pytest tmp_path."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
import xarray as xr

from global_mosaic.processing.mosaic_io import (
    BandMosaicInputs,
    SatelliteMeta,
    build_tier_dataset,
    write_tier_netcdf,
)

TARGET = dt.datetime(2026, 7, 30, 12, 0, 0)


def _grid_da(values, lat, lon):
    coords = {"lat": (("y", "x"), np.array(lat, dtype=float)), "lon": (("y", "x"), np.array(lon, dtype=float))}
    return xr.DataArray(np.array(values, dtype=float), coords=coords, dims=["y", "x"])


def _lat_lon_grid(shape=(4, 4)):
    lat = np.tile(np.linspace(-1, 1, shape[0]).reshape(-1, 1), (1, shape[1]))
    lon = np.tile(np.linspace(-1, 1, shape[1]).reshape(1, -1), (shape[0], 1))
    return lat, lon


def _band_inputs(band_name, fill_value, lat, lon):
    value = _grid_da(np.full((4, 4), fill_value), lat, lon)
    satellite_id = _grid_da(np.ones((4, 4)), lat, lon)
    resolution_km = _grid_da(np.full((4, 4), 2.0), lat, lon)
    metas = [SatelliteMeta("goes-19", 1, -75.2, TARGET, TARGET, TARGET, 2.0, 10.33, 10, "fake_goes.nc")]
    return BandMosaicInputs(
        band_name=band_name,
        value=value,
        satellite_id=satellite_id,
        resolution_km=resolution_km,
        metas=metas,
        value_long_name=f"{band_name} test value",
        value_units="K",
    )


def test_bands_get_suffixed_variables_sharing_one_grid(tmp_path):
    lat, lon = _lat_lon_grid()
    tir1 = _band_inputs("TIR1", 250.0, lat, lon)
    wv = _band_inputs("WV", 230.0, lat, lon)

    ds = build_tier_dataset("Tier A", [tir1, wv], TARGET)

    assert "value_tir1" in ds
    assert "value_wv" in ds
    assert "satellite_id_tir1" in ds
    assert "satellite_id_wv" in ds
    assert "pixel_resolution_km_tir1" in ds
    assert "pixel_resolution_km_wv" in ds
    # lat/lon/crs shared, not duplicated per band
    assert "lat" in ds.coords and "lon" in ds.coords
    assert "crs" in ds.data_vars
    assert "value" not in ds  # unsuffixed Phase-0-style name must not leak through

    assert ds["value_tir1"].values[0, 0] == pytest.approx(250.0)
    assert ds["value_wv"].values[0, 0] == pytest.approx(230.0)

    path = tmp_path / "tier_a.nc"
    write_tier_netcdf(str(path), ds)
    reopened = xr.open_dataset(path)
    assert reopened["value_tir1"].values[0, 0] == pytest.approx(250.0, abs=0.01)
    assert reopened["value_wv"].values[0, 0] == pytest.approx(230.0, abs=0.01)


def test_per_band_satellite_metadata_tables_are_independent(tmp_path):
    """Different bands can have different contributing satellites (a
    satellite can have a gap in one channel and not another) -- the
    per-band satellite dimension/metadata table must stay independent,
    not merged/shared like lat/lon/crs."""
    lat, lon = _lat_lon_grid()
    tir1 = _band_inputs("TIR1", 250.0, lat, lon)
    wv = _band_inputs("WV", 230.0, lat, lon)
    wv.metas = wv.metas + [
        SatelliteMeta("himawari-09", 6, 140.7, TARGET, TARGET, TARGET, 2.0, 6.9, 10, "fake_himawari.dat")
    ]
    # give WV a second (winning) satellite so its satellite_id differs from TIR1's
    wv.satellite_id = _grid_da(np.full((4, 4), 6), lat, lon)

    ds = build_tier_dataset("Tier A", [tir1, wv], TARGET)

    assert list(ds["satellite_key_tir1"].values) == ["goes-19"]
    assert list(ds["satellite_key_wv"].values) == ["goes-19", "himawari-09"]
    assert (ds["satellite_id_tir1"].values == 1).all()
    assert (ds["satellite_id_wv"].values == 6).all()


def test_tier_b_solar_zenith_and_daylit_fields(tmp_path):
    lat, lon = _lat_lon_grid()
    vis = _band_inputs("VIS", 12.0, lat, lon)
    vis.value_units = "%"
    swir = _band_inputs("SWIR", 0.5, lat, lon)
    swir.value_units = "mW.cm-2.sr-1.micron-1"

    sza = _grid_da([[10.0, 170.0, 10.0, 170.0]] * 4, lat, lon)  # half day, half night columns

    ds = build_tier_dataset("Tier B", [vis, swir], TARGET, solar_zenith_angle_deg=sza)

    assert "solar_zenith_angle_deg" in ds
    assert "is_daylit" in ds
    assert (ds["is_daylit"].values[:, 0] == 1).all()  # SZA 10 -> day
    assert (ds["is_daylit"].values[:, 1] == 0).all()  # SZA 170 -> night

    path = tmp_path / "tier_b.nc"
    write_tier_netcdf(str(path), ds)
    reopened = xr.open_dataset(path)
    assert reopened["solar_zenith_angle_deg"].values[0, 0] == pytest.approx(10.0, abs=0.01)
    assert reopened["is_daylit"].values[0, 1] == 0


def test_tier_attrs_name_the_bands():
    lat, lon = _lat_lon_grid()
    tir1 = _band_inputs("TIR1", 250.0, lat, lon)
    wv = _band_inputs("WV", 230.0, lat, lon)
    ds = build_tier_dataset("Tier A", [tir1, wv], TARGET)
    assert "TIR1" in ds.attrs["title"]
    assert "WV" in ds.attrs["title"]
    assert ds.attrs["requested_target_time"] == TARGET.isoformat()
