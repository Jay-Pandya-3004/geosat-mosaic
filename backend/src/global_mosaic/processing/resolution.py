"""Per-pixel *actual* ground resolution, based on each candidate satellite's
real viewing geometry to that pixel -- not just its nominal nadir figure.

This project's original requirements ask for a mask of "the
pixel actual spatial resolution based on the distance from satellite nadir
and actual location of pixel." No primary source gathered during research
gives a closed-form IFOV(view-zenith-angle) formula for any of the four
satellite families -- every data guide reviewed defines resolution
"at nadir/SSP" only. This module is
therefore this project's own derivation, not a value taken from a vendor
document. The model used:

  1. Compute the view zenith angle (VZA) at the ground point: the angle,
     measured from the local vertical at that point, between straight up
     and the line of sight to the satellite. For a satellite at
     geocentric distance rho over sub-satellite point (lat0=0, lon0) and
     a ground point at (lat, lon) on a spherical Earth of radius Re::

         cos(gamma) = cos(lat) * cos(lon - lon0)          # central angle
         r_s = sqrt(rho**2 + Re**2 - 2*rho*Re*cos(gamma))  # slant range
         VZA = arcsin(rho * sin(gamma) / r_s)

     (Re, rho renamed earth_radius_km/orbit_radius_km below.)
  2. Scale the nadir ground sample distance by two effects that both grow
     toward the limb:

     - range magnification (r_s / H): the instrument's angular IFOV is
       fixed, so footprint size scales linearly with slant range.
     - oblique-view elongation (1 / cos(VZA)): the footprint projects
       onto the tilted surface at an angle, stretching it further in
       the viewing direction.

     ::

         effective_resolution = nadir_resolution * (r_s / H) / cos(VZA)

     This is a standard simplified two-factor GEO-imager footprint-growth
     model (documented here as this project's own choice, not a sourced
     fact) -- GOES's own INR validation paper confirms footprint growth
     and registration quality both degrade sharply beyond ~75 deg VZA,
     consistent with this formula's
     shape, but does not give numeric coefficients to fit against.

Prefer the satellite's *actual* per-file sub-satellite longitude/latitude/
height (satpy exposes this as a loaded DataArray's
`.attrs["orbital_parameters"]`, e.g. `satellite_actual_longitude`) over
sat_metadata.py's static `Nominal_Longitude_Deg`, since real geostationary
satellites station-keep within a small box around their nominal slot
rather than sitting exactly on it -- same principle reproject.py's
docstring already calls out for building each satellite's native area
definition.

NOT TRIMMING THE HORIZON (investigated and reverted, 2026-07-31): a
mismatch was found between this module's "visible" pixels and where two
files (a GOES-19 ABI C13 file, a Himawari-9 AHI B13 file) actually
had data -- mostly "resolution says visible, real value is NaN" (a few
thousand pixels per file), concentrated in the last ~3 deg before the
geometric horizon. The obvious-looking fix -- trim `visible` by a flat
~3 deg margin -- was tried and directly measured against the same two
files, and made things *worse*: it turned ~3,000-22,000 sporadic missing
pixels into ~12,000-31,000 *newly excluded pixels that actually had
good data*. That result means the near-limb data loss isn't a clean band
starting at a fixed angle -- it's patchy/sporadic dropout (per-pixel
quality masking, not a hard cutoff) scattered through that zone, mixed
with plenty of genuinely valid pixels right up to close to the true
geometric horizon (which itself is accurate to within ~0.24 deg in both
files). A flat angular trim is the wrong tool for
patchy dropout, so this module deliberately does NOT do it. The original
finding that "some pixels get excluded near the limb" is real but benign:
select.py's `select_winners` already requires both value and resolution
to be valid before considering a candidate, so those specific missing
pixels are already safely and correctly excluded per-pixel, with no
geometry change needed. Recorded here so this exact "fix" isn't
re-attempted without re-measuring it first.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from global_mosaic.satellites.sat_metadata import Satellites_Objects


@dataclass
class SatelliteGeometry:
    """Actual (preferred) or nominal (fallback) viewing geometry for one
    satellite, in the units resolution_km_at_nadir needs (km).

    Attributes:
        sub_satellite_lon_deg: Sub-satellite point longitude, in degrees.
        sub_satellite_lat_deg: Sub-satellite point latitude, in degrees.
        earth_radius_km: Earth radius used for this geometry, in km.
        orbit_radius_km: Geocentric distance to the satellite (Earth
            radius + altitude), in km.
    """

    sub_satellite_lon_deg: float
    sub_satellite_lat_deg: float
    earth_radius_km: float
    orbit_radius_km: float  # geocentric distance to satellite (Re + altitude)


def geometry_from_orbital_parameters(orbital_parameters: dict) -> SatelliteGeometry:
    """Build a SatelliteGeometry from satpy's per-file `orbital_parameters` attrs.

    Key names differ noticeably reader to reader (not documented
    uniformly by satpy), so this tries three tiers, most-accurate first:

    1. `satellite_actual_*` -- the true station-kept position for this
       observation (present for ahi_hsd; a real Himawari-9 file
       had actual longitude 140.7576, vs. its nominal 140.7 --
       a non-trivial difference this matters for).
    2. `satellite_nominal_*` -- the satellite's assigned slot position,
       not its instantaneous real one (present for abi_l1b, whose
       orbital_parameters had no `satellite_actual_*` keys at all in a
       real GOES-19 file tested).
    3. `projection_*` -- the longitude/altitude the file's own
       geostationary projection was actually built against (present on
       every reader tested; the safest final fallback since it's at
       least self-consistent with the data's own native area).

    Args:
        orbital_parameters: A loaded DataArray's
            ``.attrs["orbital_parameters"]`` dict from satpy.

    Returns:
        The resolved :class:`SatelliteGeometry`, preferring
        actual-position keys, then nominal, then projection-derived.

    Raises:
        ValueError: If none of the three key tiers provide a longitude
            or altitude value.
    """
    lon = orbital_parameters.get(
        "satellite_actual_longitude",
        orbital_parameters.get("satellite_nominal_longitude", orbital_parameters.get("projection_longitude")),
    )
    lat = orbital_parameters.get(
        "satellite_actual_latitude",
        orbital_parameters.get("satellite_nominal_latitude", orbital_parameters.get("projection_latitude", 0.0)),
    )
    altitude_m = orbital_parameters.get(
        "satellite_actual_altitude",
        orbital_parameters.get("satellite_nominal_altitude", orbital_parameters.get("projection_altitude")),
    )
    if lon is None or altitude_m is None:
        raise ValueError(
            f"orbital_parameters is missing longitude/altitude under any "
            f"known key name: {sorted(orbital_parameters)}"
        )
    earth_radius_km = 6378.137
    return SatelliteGeometry(
        sub_satellite_lon_deg=float(lon),
        sub_satellite_lat_deg=float(lat),
        earth_radius_km=earth_radius_km,
        orbit_radius_km=earth_radius_km + float(altitude_m) / 1000.0,
    )


def geometry_from_sat_metadata(satellite_key: str) -> SatelliteGeometry:
    """Build a fallback SatelliteGeometry from sat_metadata.py's static nominal-slot values.

    For when a file's actual orbital_parameters aren't available (e.g.
    quick estimates before a file is downloaded/opened).

    Args:
        satellite_key: Satellite key, e.g. ``"goes-16"``.

    Returns:
        The :class:`SatelliteGeometry` built from this satellite's
        static nominal metadata.

    Raises:
        KeyError: If `satellite_key` is not in ``Satellites_Objects``, or
            it lacks the required metadata fields.
    """
    sat = Satellites_Objects[satellite_key]
    return SatelliteGeometry(
        sub_satellite_lon_deg=sat["Nominal_Longitude_Deg"],
        sub_satellite_lat_deg=sat.get("Sub_Satellite_Point", {}).get("lat", 0.0),
        earth_radius_km=sat["Earth_Radius_Km"],
        orbit_radius_km=sat["Orbit_Radius_Km"],
    )


def view_zenith_angle_deg(
    lat_deg: np.ndarray, lon_deg: np.ndarray, geometry: SatelliteGeometry
) -> np.ndarray:
    """Compute the view zenith angle of a satellite as seen from each grid point.

    View zenith angle (degrees) of `geometry`'s satellite as seen from
    each (lat_deg, lon_deg) grid point. NaN where the point is beyond the
    satellite's horizon (not visible). See the module docstring's "NOT
    TRIMMING THE HORIZON" note for why this is the pure geometric horizon,
    not an empirically-shrunk one -- that was tried and measured worse.

    Args:
        lat_deg: Latitude grid, in degrees.
        lon_deg: Longitude grid, in degrees, same shape as `lat_deg`.
        geometry: The satellite's viewing geometry.

    Returns:
        View zenith angle in degrees at each grid point, same shape as
        `lat_deg`; NaN where the point is beyond the satellite's horizon.
    """
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    lat0 = np.radians(geometry.sub_satellite_lat_deg)
    lon0 = np.radians(geometry.sub_satellite_lon_deg)

    cos_gamma = np.sin(lat0) * np.sin(lat) + np.cos(lat0) * np.cos(lat) * np.cos(lon - lon0)
    cos_gamma = np.clip(cos_gamma, -1.0, 1.0)
    gamma = np.arccos(cos_gamma)

    re = geometry.earth_radius_km
    rho = geometry.orbit_radius_km
    r_s = np.sqrt(rho**2 + re**2 - 2 * rho * re * cos_gamma)

    sin_vza = np.clip(rho * np.sin(gamma) / r_s, -1.0, 1.0)
    vza = np.arcsin(sin_vza)

    # Points beyond the horizon: the satellite-Earth-point triangle above
    # only holds for a point actually on the near side. Horizon condition
    # for a spherical Earth: gamma <= arccos(re / rho).
    horizon_gamma = np.arccos(re / rho)
    visible = gamma <= horizon_gamma

    return np.degrees(np.where(visible, vza, np.nan))


_RESOLUTION_CHUNK_SIZE = 2_000_000
"""How many grid points effective_resolution_km processes at once.
Measured cost (granular ctypes profiling, one INSAT/
Himawari VIS Tier-B candidate, ~1970x18000-point cropped target grid):
this function's single vectorized call over the WHOLE array -- roughly
10 same-shape float intermediates (lat, lon, cos_gamma, gamma, r_s,
sin_vza, cos_vza, cos_vza_safe, stretch, resolution, visible) -- cost a
+1.6-1.85GB transient, actually LARGER than crop_source_to_target_
lonlat's own (chunked-away) cost in reproject.py. Chunking this loop is
EXACT, not an approximation: every grid point's result is an independent
elementwise function of only that point's own lat/lon (no cross-point
reduction), so processing in blocks and writing into one preallocated
output array produces bit-identical results to the single-shot version,
just without ~10 full-size intermediates ever coexisting at once."""


def effective_resolution_km(
    lat_deg: np.ndarray,
    lon_deg: np.ndarray,
    geometry: SatelliteGeometry,
    nadir_resolution_km: float,
) -> np.ndarray:
    """Compute actual (view-geometry-scaled) ground resolution at each grid point.

    Actual ground resolution (km) at each (lat_deg, lon_deg) grid point
    for a satellite with the given nadir resolution, per this module's
    docstring model. NaN where the point isn't visible from the satellite
    (pure geometric horizon -- see the module docstring's "NOT TRIMMING
    THE HORIZON" note).

    Processes points in `_RESOLUTION_CHUNK_SIZE`-sized chunks -- see that
    constant's own docstring for the memory cost this avoids; the
    chunking is exact, not an approximation (see docstring there).

    Args:
        lat_deg: Latitude grid, in degrees.
        lon_deg: Longitude grid, in degrees, same shape as `lat_deg`.
        geometry: The satellite's viewing geometry.
        nadir_resolution_km: The satellite's nominal resolution at
            nadir/SSP, in km.

    Returns:
        Actual ground resolution in km at each grid point, same shape as
        `lat_deg`; NaN where the point isn't visible from the satellite.
    """
    lat_deg = np.asarray(lat_deg)
    lon_deg = np.asarray(lon_deg)
    lat_flat = lat_deg.ravel()
    lon_flat = lon_deg.ravel()
    out = np.empty(lat_flat.shape, dtype=lat_flat.dtype)

    lat0 = np.radians(geometry.sub_satellite_lat_deg)
    lon0 = np.radians(geometry.sub_satellite_lon_deg)
    re = geometry.earth_radius_km
    rho = geometry.orbit_radius_km
    h = rho - re
    horizon_gamma = np.arccos(re / rho)

    for start in range(0, lat_flat.size, _RESOLUTION_CHUNK_SIZE):
        end = start + _RESOLUTION_CHUNK_SIZE
        lat = np.radians(lat_flat[start:end])
        lon = np.radians(lon_flat[start:end])

        cos_gamma = np.sin(lat0) * np.sin(lat) + np.cos(lat0) * np.cos(lat) * np.cos(lon - lon0)
        cos_gamma = np.clip(cos_gamma, -1.0, 1.0)
        gamma = np.arccos(cos_gamma)

        r_s = np.sqrt(rho**2 + re**2 - 2 * rho * re * cos_gamma)

        sin_vza = np.clip(rho * np.sin(gamma) / r_s, -1.0, 1.0)
        cos_vza = np.sqrt(1.0 - sin_vza**2)

        visible = gamma <= horizon_gamma

        # cos_vza -> 0 right at the horizon; clip to avoid a divide-by-zero
        # blow-up for the handful of grid points sitting exactly on the limb.
        cos_vza_safe = np.where(cos_vza < 1e-3, 1e-3, cos_vza)

        stretch = (r_s / h) / cos_vza_safe
        resolution = nadir_resolution_km * stretch
        out[start:end] = np.where(visible, resolution, np.nan)

    return out.reshape(lat_deg.shape)
