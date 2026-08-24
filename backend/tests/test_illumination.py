"""Unit tests for processing/illumination.py. All synthetic -- no network
needed; pyorbital's solar-position math is a well-established dependency
already used transitively via satpy, not something this project derives
itself."""

from __future__ import annotations

import datetime as dt

import numpy as np
import xarray as xr

from global_mosaic.processing.illumination import (
    DEFAULT_NIGHT_THRESHOLD_DEG,
    is_daylit,
    mask_night,
    solar_zenith_angle_deg,
)

NOON_UTC = dt.datetime(2026, 3, 20, 12, 0, 0)  # near equinox, for a clean day/night split


def test_local_noon_has_lower_sza_than_local_midnight():
    """At a fixed instant, the point directly under the sun has a much
    lower SZA than the point on the opposite side of the globe."""
    lat = np.array([0.0, 0.0])
    lon = np.array([0.0, 180.0])  # roughly local noon vs local midnight at 12:00 UTC
    sza = solar_zenith_angle_deg(lat, lon, NOON_UTC)
    assert sza[0] < 20.0  # near-subsolar point: sun nearly overhead
    assert sza[1] > 160.0  # antisolar point: sun nearly straight down/below


def test_is_daylit_matches_threshold():
    sza = np.array([10.0, 89.9, 90.0, 91.0, 170.0])
    assert list(is_daylit(sza)) == [True, True, False, False, False]


def test_mask_night_nans_out_night_pixels_only():
    data = xr.DataArray(np.array([50.0, 50.0, 50.0]), dims=("x",))
    sza = np.array([10.0, 89.0, 120.0])
    masked = mask_night(data, sza)
    assert masked.values[0] == 50.0
    assert masked.values[1] == 50.0
    assert np.isnan(masked.values[2])


def test_mask_night_preserves_coords_and_attrs():
    data = xr.DataArray(
        np.array([1.0, 2.0]),
        dims=("x",),
        coords={"x": [0, 1]},
        attrs={"units": "%", "long_name": "reflectance"},
    )
    sza = np.array([170.0, 10.0])
    masked = mask_night(data, sza)
    assert masked.attrs == data.attrs
    assert list(masked.coords["x"].values) == [0, 1]
    assert np.isnan(masked.values[0])
    assert masked.values[1] == 2.0


def test_default_threshold_is_astronomical_horizon():
    assert DEFAULT_NIGHT_THRESHOLD_DEG == 90.0
