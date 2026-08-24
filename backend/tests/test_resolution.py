"""Unit tests for processing/resolution.py's per-pixel actual-resolution
model. All synthetic -- no network/real files needed."""

from __future__ import annotations

import numpy as np
import pytest

import global_mosaic.processing.resolution as resolution_module
from global_mosaic.processing.resolution import (
    SatelliteGeometry,
    effective_resolution_km,
    geometry_from_orbital_parameters,
    geometry_from_sat_metadata,
    view_zenith_angle_deg,
)

GOES19_GEOMETRY = SatelliteGeometry(
    sub_satellite_lon_deg=-75.2,
    sub_satellite_lat_deg=0.0,
    earth_radius_km=6378.137,
    orbit_radius_km=42164.363,
)


def test_nadir_view_zenith_angle_is_zero():
    vza = view_zenith_angle_deg(np.array([0.0]), np.array([-75.2]), GOES19_GEOMETRY)
    assert vza[0] == pytest.approx(0.0, abs=1e-6)


def test_nadir_resolution_equals_nominal():
    res = effective_resolution_km(np.array([0.0]), np.array([-75.2]), GOES19_GEOMETRY, nadir_resolution_km=2.0)
    assert res[0] == pytest.approx(2.0, abs=1e-6)


def test_resolution_degrades_monotonically_away_from_nadir():
    """Real-world regression check: resolution.py was verified against
    real GOES-19 data to produce 2.0 -> 2.33 -> 20.05 km at
    0/25/75 deg longitude offset from nadir (0.0, 29.0, 83.4 deg VZA
    respectively) -- this test encodes that same monotonic-degradation
    shape without needing the real file."""
    lons = np.array([-75.2, -100.0, -150.0])  # 0, 24.8, 74.8 deg offset from nadir
    res = effective_resolution_km(np.zeros(3), lons, GOES19_GEOMETRY, nadir_resolution_km=2.0)
    assert res[0] < res[1] < res[2]
    assert res[0] == pytest.approx(2.0, abs=1e-6)


def test_beyond_horizon_is_nan():
    # Antipodal point -- never visible from any GEO satellite.
    res = effective_resolution_km(np.array([0.0]), np.array([104.8]), GOES19_GEOMETRY, nadir_resolution_km=2.0)
    vza = view_zenith_angle_deg(np.array([0.0]), np.array([104.8]), GOES19_GEOMETRY)
    assert np.isnan(res[0])
    assert np.isnan(vza[0])


def test_horizon_edge_is_continuous_not_trimmed():
    """Regression test for a fix that was tried and reverted:
    a satellite's usable horizon is NOT artificially trimmed by
    a fixed margin (that was measured to discard far more real valid
    data than it protected against -- see resolution.py's "NOT TRIMMING
    THE HORIZON" docstring note). A point exactly at the pure geometric
    horizon must still be considered visible."""
    horizon_deg = np.degrees(np.arccos(GOES19_GEOMETRY.earth_radius_km / GOES19_GEOMETRY.orbit_radius_km))
    # A point exactly on the sub-satellite parallel, `horizon_deg` east of nadir.
    lon = GOES19_GEOMETRY.sub_satellite_lon_deg + horizon_deg
    res = effective_resolution_km(np.array([0.0]), np.array([lon]), GOES19_GEOMETRY, nadir_resolution_km=2.0)
    assert np.isfinite(res[0])
    # Just past it should be invisible.
    res_past = effective_resolution_km(
        np.array([0.0]), np.array([lon + 0.5]), GOES19_GEOMETRY, nadir_resolution_km=2.0
    )
    assert np.isnan(res_past[0])


def test_effective_resolution_km_chunking_matches_single_shot(monkeypatch):
    """Real, measured cost (granular ctypes profiling of a
    real INSAT/Himawari VIS Tier-B candidate): the single vectorized call
    over a full ~1970x18000-point cropped target grid cost a real +1.6-
    1.85GB transient -- larger than crop_source_to_target_lonlat's own
    (already chunked) cost in reproject.py. Chunked processing
    (_RESOLUTION_CHUNK_SIZE) fixes that, but must be EXACT: this forces a
    tiny chunk size on a real 2D (not just 1D) grid -- including a
    non-visible (beyond-horizon) region and a mix of near-nadir/near-limb
    points -- and proves the result is bit-for-bit identical to the
    default (effectively single-shot at this size) chunk size, including
    NaN placement."""
    lats = np.linspace(-20.0, 20.0, 37)
    lons = np.linspace(-140.0, 140.0, 53)  # wide enough to include beyond-horizon points
    lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")

    single_shot = effective_resolution_km(lat_grid, lon_grid, GOES19_GEOMETRY, nadir_resolution_km=2.0)

    monkeypatch.setattr(resolution_module, "_RESOLUTION_CHUNK_SIZE", 7)
    chunked = effective_resolution_km(lat_grid, lon_grid, GOES19_GEOMETRY, nadir_resolution_km=2.0)

    assert chunked.shape == single_shot.shape
    assert np.array_equal(np.isnan(chunked), np.isnan(single_shot))
    finite = ~np.isnan(single_shot)
    assert finite.sum() > 0 and (~finite).sum() > 0, "test grid should include both visible and beyond-horizon points"
    assert np.array_equal(chunked[finite], single_shot[finite])


def test_geometry_from_orbital_parameters_prefers_actual_over_nominal():
    """A Himawari-9 file's orbital_parameters had
    satellite_actual_longitude=140.7576, distinct from
    satellite_nominal_longitude=140.7 -- geometry_from_orbital_parameters
    must prefer the actual (station-kept) value."""
    params = {
        "satellite_actual_longitude": 140.7576,
        "satellite_actual_latitude": 0.0,
        "satellite_actual_altitude": 35789430.0,
        "satellite_nominal_longitude": 140.7,
        "satellite_nominal_latitude": 0.0,
        "satellite_nominal_altitude": 35785863.0,
    }
    geo = geometry_from_orbital_parameters(params)
    assert geo.sub_satellite_lon_deg == pytest.approx(140.7576)


def test_geometry_from_orbital_parameters_falls_back_to_projection():
    """A GOES-19 ABI file's orbital_parameters had NO satellite_actual_*
    keys at all -- must fall back through satellite_nominal_* to
    projection_* without raising."""
    params = {
        "projection_longitude": -75.0,
        "projection_latitude": 0.0,
        "projection_altitude": 35786023.0,
        "satellite_nominal_latitude": 0.0,
        "satellite_nominal_longitude": -75.19999694824219,
        "satellite_nominal_altitude": 35786023.4375,
    }
    geo = geometry_from_orbital_parameters(params)
    assert geo.sub_satellite_lon_deg == pytest.approx(-75.19999694824219)


def test_geometry_from_orbital_parameters_missing_altitude_raises():
    with pytest.raises(ValueError):
        geometry_from_orbital_parameters({"projection_longitude": -75.0})


def test_geometry_from_sat_metadata_has_earth_geometry_fields():
    """Regression test: all 3 INSAT entries were previously missing
    Earth_Radius_Km/Orbit_Radius_Km entirely, so this raised KeyError."""
    for key in ("insat-3d", "insat-3dr", "insat-3ds", "goes-19", "himawari-09", "meteosat-09"):
        geo = geometry_from_sat_metadata(key)
        assert geo.earth_radius_km == pytest.approx(6378.137)
        assert geo.orbit_radius_km > geo.earth_radius_km
