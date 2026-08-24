#!/usr/bin/env python
"""One-off empirical-overlap-bias spot-check for the WV band (a
validation step -- QA on the RT-derived coefficients already
committed in satellites/srf_correction.py, not a second computation
path; RT stays primary). Never done for WV
specifically before this script; the real INSAT-3DS/Himawari-9 SWIR seam
this project diagnosed by hand earlier is the precedent this follows.

Loads real local raw (uncorrected) WV brightness temperature from
INSAT-3DS and Himawari-9 for the same real ~2026-01-01 00:00 UTC scan
(files already in data/raw/), reprojects both onto a small regional grid
straddling their real geographic seam (~111.35 deg E, midway between
INSAT-3DS's 82.0E and Himawari-9's 140.7E sub-satellite points -- same
general geography as the SWIR seam), and compares the real measured raw
bias in the overlap against the RT-predicted raw bias implied by the
already-committed WV SRFCorrection offsets for these two satellites.

Both satellites' offsets correct their raw value onto goes-19's scale
(`corrected = raw + offset`, gain=1.0 for every WV entry). If corrected
values should agree at a shared real scene::

    raw_insat + offset_insat ~= raw_himawari + offset_himawari
    => raw_insat - raw_himawari ~= offset_himawari - offset_insat

That right-hand side is the "RT-predicted raw bias" this script compares
the real measured left-hand side against.

Caveat, stated up front (not an afterthought): INSAT-3DS and Himawari-9
view this overlap region from different, non-trivial off-nadir angles
(~13-46 deg longitude offset from each, given the 95-128E/+-15deg window
below) -- real path-length/limb effects on a semi-transparent WV channel
are a genuine, physical source of residual disagreement independent of
SRF calibration, which is exactly why this is framed as a spot-check
(does the RT prediction land in the right ballpark, same sign, right
order of magnitude) rather than a precision validation.

Usage: python scripts/spot_check_wv_empirical_overlap_bias.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC_DIR))
os.environ.setdefault("SATPY_CONFIG_PATH", str(_SRC_DIR / "global_mosaic"))

import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402
from pyresample.geometry import AreaDefinition  # noqa: E402
from satpy import Scene  # noqa: E402

from global_mosaic.satellites.band_registry import get_band_spec, get_calibration  # noqa: E402
from global_mosaic.satellites.reader_registry import get_reader_spec  # noqa: E402
from global_mosaic.satellites.srf_correction import get_srf_correction  # noqa: E402
from global_mosaic.processing.reproject import reproject_to_common_grid  # noqa: E402

_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"

_INSAT_3DS_FILE = _DATA_DIR / "3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5"
_HIMAWARI_9_WV_SEGMENTS = sorted(_DATA_DIR.glob("HS_H09_20260101_0000_B09_FLDK_R20_S*.DAT.bz2"))

# Regional grid straddling the real INSAT-3DS(82.0E)/Himawari-9(140.7E)
# seam (midpoint ~111.35E), 0.1deg (~11km) spacing -- generous enough to
# average over real synoptic-scale WV structure without smearing away a
# genuine bias, narrow enough to stay well clear of either satellite's
# own limb (max ~46deg longitude offset here vs each satellite's real
# ~81.3deg Max_Viewing_Angle_Deg limit).
_LON_MIN, _LON_MAX = 95.0, 128.0
_LAT_MIN, _LAT_MAX = -15.0, 15.0
_STEP_DEG = 0.1


def load_raw_band(satellite_key: str, band: str, paths: list[Path]) -> xr.DataArray:
    """Load one satellite's raw (uncorrected) calibrated band via its registered satpy reader.

    Args:
        satellite_key: Satellite key, e.g. ``"insat-3ds"``.
        band: Logical band name, e.g. ``"WV"``.
        paths: Local file path(s) to load (a single file, or the segment
            files a reader like Himawari's needs together).

    Returns:
        The loaded band as an ``xarray.DataArray``, at this band's
        registered calibration (see band_registry.get_calibration).
    """
    spec = get_reader_spec(satellite_key)
    band_spec = get_band_spec(satellite_key, band)
    calibration = get_calibration(band)
    scn = Scene(filenames=[str(p) for p in paths], reader=spec.reader)
    scn.load([band_spec.satpy_band_name], calibration=calibration)
    return scn[band_spec.satpy_band_name]


def main() -> int:
    """CLI entry point: run the INSAT-3DS/Himawari-9 WV empirical-overlap-bias spot-check.

    Loads both satellites' real raw WV data, reprojects both onto a
    small shared regional grid, measures the real raw bias in their
    overlap, and compares it against the bias predicted by the
    already-committed RT-derived WV SRF corrections (see module
    docstring for the full method).

    Returns:
        Process exit code: 1 if the required real data files are
        missing or there's no valid overlap, else 0.
    """
    print(f"INSAT-3DS file: {_INSAT_3DS_FILE.name}")
    print(f"Himawari-9 WV segments: {len(_HIMAWARI_9_WV_SEGMENTS)} files")
    if not _INSAT_3DS_FILE.exists() or len(_HIMAWARI_9_WV_SEGMENTS) == 0:
        print("Required real data files not found under data/raw/ -- see script docstring.")
        return 1

    insat = load_raw_band("insat-3ds", "WV", [_INSAT_3DS_FILE])
    himawari = load_raw_band("himawari-09", "WV", _HIMAWARI_9_WV_SEGMENTS)

    width = round((_LON_MAX - _LON_MIN) / _STEP_DEG)
    height = round((_LAT_MAX - _LAT_MIN) / _STEP_DEG)
    region = AreaDefinition(
        area_id="wv_overlap_spot_check",
        description="INSAT-3DS/Himawari-9 real overlap region for WV empirical-overlap-bias spot-check",
        proj_id="longlat",
        projection={"proj": "longlat", "datum": "WGS84"},
        width=width, height=height,
        area_extent=(_LON_MIN, _LAT_MIN, _LON_MAX, _LAT_MAX),
    )

    print("Reprojecting INSAT-3DS onto the overlap region...")
    insat_r = reproject_to_common_grid(insat, region, radius_of_influence_m=20_000)
    print("Reprojecting Himawari-9 onto the overlap region...")
    himawari_r = reproject_to_common_grid(himawari, region, radius_of_influence_m=20_000)

    both_valid = np.isfinite(insat_r.values) & np.isfinite(himawari_r.values)
    n = int(both_valid.sum())
    print(f"Overlap pixels with valid raw data from both satellites: {n} / {both_valid.size}")
    if n == 0:
        print("No real overlap in this region -- widen the window or check file loading.")
        return 1

    diff = insat_r.values[both_valid] - himawari_r.values[both_valid]  # raw insat - raw himawari
    print(
        f"Real empirical raw bias (insat_raw - himawari_raw): "
        f"mean={diff.mean():+.3f}K median={np.median(diff):+.3f}K std={diff.std():.3f}K "
        f"n={n}"
    )

    insat_corr = get_srf_correction("insat-3ds", "WV")
    himawari_corr = get_srf_correction("himawari-09", "WV")
    predicted_bias = himawari_corr.offset - insat_corr.offset
    print(
        f"RT-predicted raw bias (insat_raw - himawari_raw) implied by "
        f"srf_correction.py's committed WV offsets: {predicted_bias:+.3f}K "
        f"(= himawari_offset {himawari_corr.offset:+.4f} - insat_offset {insat_corr.offset:+.4f})"
    )
    print(f"Spot-check residual (empirical mean - RT-predicted): {diff.mean() - predicted_bias:+.3f}K")
    print(f"Same sign: {'yes' if np.sign(diff.mean()) == np.sign(predicted_bias) else 'NO'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
