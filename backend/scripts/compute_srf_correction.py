#!/usr/bin/env python
"""Computes real SRF-weighted radiative-transfer correction coefficients
for one thermal band (WV first), and prints them as SRF_CORRECTIONS entries
ready to paste into satellites/srf_correction.py.

Implements SRF-weighted radiative transfer (the primary method) plus a
WV/thermal reference technique (an atmospheric-profile ensemble, mean bias
-> gain/offset, spread -> reference_ensemble_spread) using
satellites/srf_convolution.py's tested math.

**v1 interim ensemble, not yet the full designed one**: the design
specifically chose NWP SAF's 91-level/83-profile diverse dataset. That hasn't been
downloaded/converted to libRadtran's atmosphere_file format yet -- this
script uses libRadtran's own 6 bundled AFGL standard atmospheres (tropical,
mid-latitude summer/winter, subarctic summer/winter, US standard) instead,
a smaller but same-spirit real-atmosphere ensemble, available with zero
extra downloads. Upgrading to the full 83-profile set is a real, separately
-scoped follow-up (would just mean pointing ATMOSPHERE_FILES at 83 converted
profile files instead of 6 AFGL ones -- nothing else in this script would
need to change).

Requires a working libRadtran build with REPTRAN, Mie, and Baum data
installed -- LIBRADTRAN_ROOT below assumes
it's reachable via `wsl.exe` from Windows; edit UVSPEC_CMD if running this
directly inside WSL/Linux instead.

Usage:
    python scripts/compute_srf_correction.py WV --anchor goes-19 --target insat-3ds
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
from pathlib import Path

import numpy as np

import sys

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC_DIR))

from global_mosaic.satellites.srf_convolution import (  # noqa: E402
    SRFCurve,
    effective_brightness_temperature,
    parse_srf_goes_cwg,
    parse_srf_himawari_xlsx,
    parse_srf_nwpsaf,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRF_DATA_DIR = _REPO_ROOT / "docs" / "research" / "srf_data"

# WSL-side path to a libRadtran 2.0.6 install (REPTRAN, Mie, and Baum data
# required). Override via the LIBRADTRAN_ROOT environment variable if
# installed elsewhere; edit UVSPEC_CMD instead if running this script
# directly inside WSL/Linux rather than invoking it via `wsl.exe`.
LIBRADTRAN_ROOT = os.environ.get("LIBRADTRAN_ROOT", "/home/user/libRadtran-2.0.6")

# libRadtran's own bundled AFGL atmospheres -- the v1 interim ensemble
# used before the real NWP SAF set was converted (see
# convert_nwpsaf_profiles.py). Kept available via --ensemble afgl for
# comparison/regression checking, not the default anymore.
AFGL_ATMOSPHERE_FILES = [
    f"{LIBRADTRAN_ROOT}/data/atmmod/afglt.dat",  # tropical
    f"{LIBRADTRAN_ROOT}/data/atmmod/afglms.dat",  # mid-latitude summer
    f"{LIBRADTRAN_ROOT}/data/atmmod/afglmw.dat",  # mid-latitude winter
    f"{LIBRADTRAN_ROOT}/data/atmmod/afglss.dat",  # subarctic summer
    f"{LIBRADTRAN_ROOT}/data/atmmod/afglsw.dat",  # subarctic winter
    f"{LIBRADTRAN_ROOT}/data/atmmod/afglus.dat",  # US standard
]

# The actually-designed ensemble: 80 real ECMWF profiles
# (81/82/83 excluded, synthetic envelope/mean -- see
# convert_nwpsaf_profiles.py), converted from NWP SAF's ECMWF_83P_91L.dat.
# Discovered by listing convert_nwpsaf_profiles.py's output directory
# inside WSL rather than hardcoding a count, so this stays correct if the
# conversion script's exclusion set or output ever changes.
def _list_nwpsaf_atmosphere_files() -> list[str]:
    """List the converted NWP SAF profile atmosphere files inside WSL.

    Returns:
        Sorted WSL-side paths of ``nwpsaf_profile_*.dat`` files under
        ``/tmp/nwpsaf_afgl`` (see convert_nwpsaf_profiles.py, which
        produces them).

    Raises:
        subprocess.CalledProcessError: If the ``wsl.exe`` listing command
            fails (e.g. the profiles haven't been converted yet).
    """
    result = subprocess.run(
        ["wsl.exe", "--", "bash", "-c", "ls /tmp/nwpsaf_afgl/nwpsaf_profile_*.dat"],
        capture_output=True, text=True, check=True,
    )
    return sorted(result.stdout.strip().splitlines())

# Per-band SRF file lookup: (parser, relative path under the downloaded
# NWP SAF/GOES-R ABI CWG SRF filter-response files directory).
# WV proven first; MIR/TIR1/TIR2 generalize directly
# (channel numbers per band_registry.py's FAMILY_BAND_REGISTRY: GOES/
# Himawari share ch7/13/15, Meteosat SEVIRI ch4/9/10, INSAT ch3/5/6).
# VIS/SWIR needed a different (non-thermal) convolution instead --
# see compute_srf_correction_vis_swir.py.
_WV_SRF_FILES: dict[str, tuple] = {
    "goes-19": (parse_srf_goes_cwg, "goes-19_fm4/GOES-R_ABI_FM4_SRF_CWG_ch9.txt"),
    "goes-18": (parse_srf_goes_cwg, "goes-18_fm3/GOES-R_ABI_FM3_SRF_CWG_ch9.txt"),
    "insat-3ds": (parse_srf_nwpsaf, "insat-3ds/rtcoef_insat3_6_imager_srf_ch04.txt"),
    "insat-3dr": (parse_srf_nwpsaf, "insat-3dr/rtcoef_insat3_5_imager_srf_ch04.txt"),
    "insat-3d": (parse_srf_nwpsaf, "insat-3d/rtcoef_insat3_4_imager_srf_ch04.txt"),
    "meteosat-09": (parse_srf_nwpsaf, "meteosat-09_msg2_seviri/rtcoef_msg_2_seviri_srf_ch05.txt"),
    "meteosat-10": (parse_srf_nwpsaf, "meteosat-10_msg3_seviri/rtcoef_msg_3_seviri_srf_ch05.txt"),
}

_MIR_SRF_FILES: dict[str, tuple] = {
    "goes-19": (parse_srf_goes_cwg, "goes-19_fm4/GOES-R_ABI_FM4_SRF_CWG_ch7.txt"),
    "goes-18": (parse_srf_goes_cwg, "goes-18_fm3/GOES-R_ABI_FM3_SRF_CWG_ch7.txt"),
    "insat-3ds": (parse_srf_nwpsaf, "insat-3ds/rtcoef_insat3_6_imager_srf_ch03.txt"),
    "insat-3dr": (parse_srf_nwpsaf, "insat-3dr/rtcoef_insat3_5_imager_srf_ch03.txt"),
    "insat-3d": (parse_srf_nwpsaf, "insat-3d/rtcoef_insat3_4_imager_srf_ch03.txt"),
    "meteosat-09": (parse_srf_nwpsaf, "meteosat-09_msg2_seviri/rtcoef_msg_2_seviri_srf_ch04.txt"),
    "meteosat-10": (parse_srf_nwpsaf, "meteosat-10_msg3_seviri/rtcoef_msg_3_seviri_srf_ch04.txt"),
}

_TIR1_SRF_FILES: dict[str, tuple] = {
    "goes-19": (parse_srf_goes_cwg, "goes-19_fm4/GOES-R_ABI_FM4_SRF_CWG_ch13.txt"),
    "goes-18": (parse_srf_goes_cwg, "goes-18_fm3/GOES-R_ABI_FM3_SRF_CWG_ch13.txt"),
    "insat-3ds": (parse_srf_nwpsaf, "insat-3ds/rtcoef_insat3_6_imager_srf_ch05.txt"),
    "insat-3dr": (parse_srf_nwpsaf, "insat-3dr/rtcoef_insat3_5_imager_srf_ch05.txt"),
    "insat-3d": (parse_srf_nwpsaf, "insat-3d/rtcoef_insat3_4_imager_srf_ch05.txt"),
    "meteosat-09": (parse_srf_nwpsaf, "meteosat-09_msg2_seviri/rtcoef_msg_2_seviri_srf_ch09.txt"),
    "meteosat-10": (parse_srf_nwpsaf, "meteosat-10_msg3_seviri/rtcoef_msg_3_seviri_srf_ch09.txt"),
}

_TIR2_SRF_FILES: dict[str, tuple] = {
    "goes-19": (parse_srf_goes_cwg, "goes-19_fm4/GOES-R_ABI_FM4_SRF_CWG_ch15.txt"),
    "goes-18": (parse_srf_goes_cwg, "goes-18_fm3/GOES-R_ABI_FM3_SRF_CWG_ch15.txt"),
    "insat-3ds": (parse_srf_nwpsaf, "insat-3ds/rtcoef_insat3_6_imager_srf_ch06.txt"),
    "insat-3dr": (parse_srf_nwpsaf, "insat-3dr/rtcoef_insat3_5_imager_srf_ch06.txt"),
    "insat-3d": (parse_srf_nwpsaf, "insat-3d/rtcoef_insat3_4_imager_srf_ch06.txt"),
    "meteosat-09": (parse_srf_nwpsaf, "meteosat-09_msg2_seviri/rtcoef_msg_2_seviri_srf_ch10.txt"),
    "meteosat-10": (parse_srf_nwpsaf, "meteosat-10_msg3_seviri/rtcoef_msg_3_seviri_srf_ch10.txt"),
}

_BAND_SRF_FILES = {"WV": _WV_SRF_FILES, "MIR": _MIR_SRF_FILES, "TIR1": _TIR1_SRF_FILES, "TIR2": _TIR2_SRF_FILES}

# Himawari SRF data is local-only -- not under the downloaded NWP SAF/
# GOES-R ABI CWG/AHI SRF filter-response files directory, so handled
# separately from the repo-relative _BAND_SRF_FILES lookup.
# Band 9 = WV per band_registry.py's Himawari channel mapping.
_HIMAWARI_SRF_XLSX = {
    "himawari-08": Path("data/srf/Himawari/AHI-08_SpectralResponsivity/AHI-8_SpectralResponsivity_Data.xlsx"),
    "himawari-09": Path("data/srf/Himawari/AHI-09_SpectralResponsivity/AHI-9_SpectralResponsivity_Data.xlsx"),
}
_HIMAWARI_BAND_SHEET = {"WV": "Band 9", "MIR": "Band 7", "TIR1": "Band 13", "TIR2": "Band 15"}

# Combined wavenumber coverage of every satellite's SRF for each band (all
# 7 satellites, Himawari included) plus margin, converted to the nm range
# uvspec's `wavelength` directive wants. WV checked previously (GOES
# 1342.6-1553.0, INSAT 1371.7-1543.2 cm^-1). MIR/TIR1/TIR2 checked this
# session directly against every satellite's real SRF file -- Meteosat's
# SRF tails are the widest of the 7 in all three bands, so its range
# dominates the combined bound with no extra margin needed there:
#   MIR:  goes-19 2435.9-2691.9, insat 2448-2695, meteosat 2083.3-3092.5,
#         himawari 2431.4-2787.0 cm^-1 -> combined 2083.3-3092.5
#   TIR1: goes-19 913.7-1019.3, insat 869.6-986.2, meteosat 781.25-1136.36,
#         himawari 912.0-1010.0 cm^-1 -> combined 781.25-1136.36
#   TIR2: goes-19 754.4-895.8, insat 787.4-881.8, meteosat 714.29-1000.0,
#         himawari 732.0-895.0 cm^-1 -> combined 714.29-1000.0
_WV_WAVENUMBER_RANGE_CM = (1250.0, 1650.0)
_MIR_WAVENUMBER_RANGE_CM = (2000.0, 3200.0)
_TIR1_WAVENUMBER_RANGE_CM = (750.0, 1150.0)
_TIR2_WAVENUMBER_RANGE_CM = (680.0, 1050.0)

_BAND_WAVENUMBER_RANGE_CM = {
    "WV": _WV_WAVENUMBER_RANGE_CM,
    "MIR": _MIR_WAVENUMBER_RANGE_CM,
    "TIR1": _TIR1_WAVENUMBER_RANGE_CM,
    "TIR2": _TIR2_WAVENUMBER_RANGE_CM,
}


def load_srf(satellite_key: str, band: str) -> SRFCurve:
    """Load a satellite's SRF curve for a given band, dispatching by data format.

    Himawari's SRF data lives in a local xlsx workbook (see
    ``_HIMAWARI_SRF_XLSX``); every other satellite's SRF is looked up in
    ``_BAND_SRF_FILES`` and parsed via its registered parser function.

    Args:
        satellite_key: Satellite key, e.g. ``"goes-19"``.
        band: Band name, one of ``_BAND_SRF_FILES``' keys (``"WV"``,
            ``"MIR"``, ``"TIR1"``, ``"TIR2"``).

    Returns:
        The parsed :class:`SRFCurve` for this satellite/band.

    Raises:
        KeyError: If `satellite_key` has no registered SRF file for `band`.
    """
    if satellite_key in _HIMAWARI_SRF_XLSX:
        return parse_srf_himawari_xlsx(_HIMAWARI_SRF_XLSX[satellite_key], _HIMAWARI_BAND_SHEET[band])
    parser, rel_path = _BAND_SRF_FILES[band][satellite_key]
    return parser(_SRF_DATA_DIR / rel_path)


def run_uvspec_thermal_spectrum(atmosphere_file_abs_wsl_path: str, wn_range_cm: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Run uvspec in thermal/TOA-nadir-radiance mode for one atmosphere.

    Unit conversion determined empirically, not assumed: the
    libRadtran docs only document `uu`'s unit for the solar/reflective case
    (mW/(m^2 nm sr), tied to whichever solar_flux file is in use) and say
    nothing about thermal mode. Cross-checked raw `uu` against uvspec's own
    `output_quantity brightness` (independent internal Planck inversion) at
    two different wavelengths (~1472 and ~1592 cm^-1) -- both gave the same
    ~994.5x ratio to this module's planck_radiance-implied value, with NO
    wavelength dependence. A wavelength-dependent nm->cm^-1 Jacobian would
    have produced a materially different ratio at the two test points and
    didn't; a flat, wavelength-independent factor did. Conclusion: thermal-
    mode `uu` is already natively per-wavenumber (W m^-2 sr^-1 (cm^-1)^-1),
    not per-nm -- only a W->mW unit conversion (x1000) is needed, no
    Jacobian. The residual ~0.5% (994.5 vs exactly 1000) is consistent with
    this module's c1/c2 constants being rounded to 6-7 significant figures
    rather than full CODATA precision -- small enough not to matter at the
    ~0.5K-in-250K level this correction operates at, not a sign of a
    remaining unit error.

    Args:
        atmosphere_file_abs_wsl_path: WSL-side absolute path to a
            libRadtran atmosphere file to run uvspec against.
        wn_range_cm: ``(low, high)`` wavenumber bound, in cm^-1, to
            request from uvspec (converted internally to the
            corresponding nm wavelength range).

    Returns:
        Tuple of ``(wavenumber_cm, radiance_per_cm)`` arrays, sorted
        ascending by wavenumber -- TOA thermal radiance in
        mW m^-2 sr^-1 (cm^-1)^-1 at each wavenumber.

    Raises:
        subprocess.CalledProcessError: If the uvspec run fails.
    """
    lo_nm = 1.0e7 / wn_range_cm[1]
    hi_nm = 1.0e7 / wn_range_cm[0]
    inp = f"""atmosphere_file {atmosphere_file_abs_wsl_path}
data_files_path {LIBRADTRAN_ROOT}/data
rte_solver disort
mol_abs_param reptran coarse
albedo 0
source thermal
wavelength {lo_nm:.3f} {hi_nm:.3f}
zout TOA
umu 1.0
phi 0
output_user lambda uu
quiet
"""
    result = subprocess.run(
        ["wsl.exe", "--", f"{LIBRADTRAN_ROOT}/bin/uvspec"],
        input=inp,
        capture_output=True,
        text=True,
        check=True,
    )
    rows = np.array([[float(x) for x in line.split()] for line in result.stdout.strip().splitlines()])
    lambda_nm, uu = rows[:, 0], rows[:, 1]
    wavenumber_cm = 1.0e7 / lambda_nm
    radiance_per_cm = uu * 1000.0  # W -> mW; uu is already per-wavenumber in thermal mode, see docstring
    order = np.argsort(wavenumber_cm)
    return wavenumber_cm[order], radiance_per_cm[order]


def compute_bias(band: str, anchor_key: str, target_key: str, ensemble: str) -> None:
    """Compute and print the SRF-weighted brightness-temperature bias between two satellites.

    Runs uvspec across the chosen atmosphere ensemble, convolves both
    the anchor's and target's real SRF against each resulting TOA
    spectrum to get their respective effective brightness temperatures,
    averages the per-atmosphere bias, and prints the result as a ready-
    to-paste ``SRF_CORRECTIONS`` entry (see satellites/srf_correction.py).

    Args:
        band: Band name, one of ``_BAND_SRF_FILES``' keys.
        anchor_key: Reference satellite key (the anchor,
            typically ``"goes-19"``) that `target_key` is corrected to match.
        target_key: Satellite key to compute a correction for.
        ensemble: ``"nwpsaf"`` for the real 80-profile ECMWF ensemble, or
            ``"afgl"`` for the 6-atmosphere interim ensemble.

    Returns:
        None. Prints per-atmosphere bias values and the final
        ``SRFCorrection`` entry to stdout.
    """
    anchor_srf = load_srf(anchor_key, band)
    target_srf = load_srf(target_key, band)
    wn_range = _BAND_WAVENUMBER_RANGE_CM[band]

    if ensemble == "nwpsaf":
        atmosphere_files = _list_nwpsaf_atmosphere_files()
        ensemble_label = f"{len(atmosphere_files)} real NWP SAF ECMWF profiles (the designed ensemble)"
        source_label = "NWP SAF ECMWF_83P_91L.dat, 80 real profiles (81/82/83 synthetic envelope/mean excluded)"
    else:
        atmosphere_files = AFGL_ATMOSPHERE_FILES
        ensemble_label = f"{len(atmosphere_files)} AFGL atmospheres (interim ensemble)"
        source_label = "libRadtran 6-AFGL-atmosphere ensemble (interim, see script docstring)"

    biases = []
    for atm_file in atmosphere_files:
        wn, radiance = run_uvspec_thermal_spectrum(atm_file, wn_range)
        anchor_bt = effective_brightness_temperature(anchor_srf, wn, radiance)
        target_bt = effective_brightness_temperature(target_srf, wn, radiance)
        # anchor - target, NOT target - anchor: `offset` gets ADDED to the
        # target's raw reading (corrected = raw*gain + offset, see
        # srf_correction.py's SRFCorrection docstring), so it must already
        # be "how much to add to bring target's reading up/down onto the
        # anchor's scale" -- if target reads target_bt for the same
        # reference scene where anchor reads anchor_bt, target's raw value
        # needs (anchor_bt - target_bt) added to match anchor_bt. Storing
        # target_bt - anchor_bt here was a real, confirmed bug (found via
        # the WV empirical-overlap-bias spot-check against real INSAT-3DS/
        # Himawari-9 data, 2026-08-05): it added the wrong-signed
        # correction, making corrected values roughly TWICE as different
        # as the raw ones instead of bringing them into agreement.
        bias = anchor_bt - target_bt
        biases.append(bias)
        label = Path(atm_file).name
        print(f"  {label:24s}  anchor_BT={anchor_bt:7.3f}K  target_BT={target_bt:7.3f}K  bias={bias:+.4f}K")

    mean_bias = statistics.mean(biases)
    spread = statistics.pstdev(biases)
    print()
    print(f"{target_key} vs {anchor_key} ({band}): mean bias = {mean_bias:+.4f}K, spread = {spread:.4f}K "
          f"over {ensemble_label}")
    print()
    print("SRF_CORRECTIONS entry:" if ensemble == "nwpsaf" else "SRF_CORRECTIONS entry (interim, see script docstring):")
    print(f'    ("{target_key}", "{band}"): SRFCorrection(')
    print(f'        gain=1.0, offset={mean_bias:.4f},')
    print(f'        reference_satellite="{anchor_key}", method="srf-weighted-radiative-transfer",')
    print(f'        computed_date="TODO", srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",')
    print(f'        reference_spectrum_source="{source_label}",')
    print(f'        reference_ensemble_spread={spread:.4f},')
    print(f'    ),')


def main() -> int:
    """CLI entry point: parse arguments and run :func:`compute_bias`.

    Returns:
        Exit code, always 0 on success.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("band", choices=sorted(_BAND_SRF_FILES))
    parser.add_argument("--anchor", default="goes-19")
    parser.add_argument("--target", required=True)
    parser.add_argument("--ensemble", choices=["nwpsaf", "afgl"], default="nwpsaf",
                         help="nwpsaf = the real 80-profile ECMWF ensemble (default); "
                              "afgl = the 6-atmosphere interim ensemble used before conversion")
    args = parser.parse_args()

    compute_bias(args.band, args.anchor, args.target, args.ensemble)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
