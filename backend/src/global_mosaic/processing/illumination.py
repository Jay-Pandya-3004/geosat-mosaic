"""Day/night masking for solar-reflective bands (VIS, SWIR).

Unlike the thermal bands (MIR/WV/TIR1/TIR2), which emit their own signal
day and night, VIS/SWIR measure *reflected sunlight* -- at night there is
nothing to reflect, so the value is physically undefined, not just less
accurate.

Not just assumed: loading a GOES-18
ABI C05 (SWIR) file and binning pixels by solar zenith angle (SZA) shows
satpy's reflectance calibration returns 100% *finite* values in every SZA
bin, including full night -- it does NOT mask night as NaN itself, it
just returns near-zero instrument noise (mean reflectance decays smoothly
from ~12% at SZA 0-60 deg to ~0% by SZA 100+, with SZA 90-100 already
down to a mean of 0.09%). So the existing satellite_id "no coverage"
mechanism (mosaic_io.py, select.py) does NOT naturally handle this --
the satellite genuinely covers a night-side pixel, it just has nothing
meaningful to report. This module explicitly marks that as invalid before
select.py ever sees it, rather than letting select_winners pick a
"winning" pixel that's actually just sensor noise.

Day/night is a property of the ground point + time, not of which
satellite is looking -- the same solar_zenith_angle_deg array applies to
every candidate satellite in a run (unlike pixel_resolution_km, which is
genuinely per-satellite), so callers should compute it once per mosaic
run and reuse it across all VIS/SWIR candidates.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import xarray as xr
from pyorbital.astronomy import sun_zenith_angle

DEFAULT_NIGHT_THRESHOLD_DEG = 90.0
"""Sun-below-astronomical-horizon convention (standard day/night boundary
in EO products). Not an arbitrary empirical fit -- the real per-pixel
signal measured already collapses to near-zero well before
this (mean 0.09% reflectance at SZA 90-100 vs ~2% at SZA 80-90), so 90 deg
is a conservative, physically-motivated cut rather than one that needed
its own from-scratch derivation the way resolution.py's horizon question
did (see that module's "NOT TRIMMING THE HORIZON" note for the contrasting
case where an empirical margin was tried and measured harmful)."""


def solar_zenith_angle_deg(lat_deg: np.ndarray, lon_deg: np.ndarray, time: dt.datetime) -> np.ndarray:
    """Compute the solar zenith angle at each grid point.

    Args:
        lat_deg: Latitude grid, in degrees.
        lon_deg: Longitude grid, in degrees.
        time: UTC time to compute solar position for.

    Returns:
        Solar zenith angle in degrees at each (lat_deg, lon_deg) grid
        point. <90 deg: sun above horizon (day). >=90 deg: night.
    """
    return sun_zenith_angle(time, lon_deg, lat_deg)


def is_daylit(sza_deg: np.ndarray, threshold_deg: float = DEFAULT_NIGHT_THRESHOLD_DEG) -> np.ndarray:
    """Compute a boolean day mask from a solar zenith angle array.

    Args:
        sza_deg: Solar zenith angle array, in degrees (e.g. from
            :func:`solar_zenith_angle_deg`).
        threshold_deg: Sun-below-horizon cutoff, in degrees.

    Returns:
        Boolean array, True where ``sza_deg < threshold_deg`` (daylit).
    """
    return sza_deg < threshold_deg


def mask_night(
    data: xr.DataArray, sza_deg: np.ndarray, threshold_deg: float = DEFAULT_NIGHT_THRESHOLD_DEG
) -> xr.DataArray:
    """Mask night-side pixels of a VIS/SWIR data array to NaN.

    Returns a copy of `data` with NaN wherever the sun is below
    `threshold_deg` -- meant to be applied to a VIS/SWIR candidate's
    reprojected values *before* constructing select.Candidate, so
    select_winners' existing "value must be finite" gating (it already
    requires this for every band, to exclude out-of-view/sensor-gap
    pixels) naturally excludes night pixels too, with no new code needed
    in select.py itself.

    Args:
        data: Reprojected VIS/SWIR data array to mask.
        sza_deg: Solar zenith angle array, in degrees, same shape as
            `data` (e.g. from :func:`solar_zenith_angle_deg`).
        threshold_deg: Sun-below-horizon cutoff, in degrees.

    Returns:
        A new ``xarray.DataArray``, same coords/dims/attrs as `data`,
        with night-side pixels (``sza_deg >= threshold_deg``) set to NaN.
    """
    masked = np.where(sza_deg < threshold_deg, data.values, np.nan)
    return xr.DataArray(masked, coords=data.coords, dims=data.dims, attrs=data.attrs)
