#!/usr/bin/env python
"""Computes real SRF-weighted correction coefficients for VIS/SWIR
(reflective bands) -- the reflective-band counterpart to compute_srf_correction.py's
thermal/WV pipeline.

Two different computations feed the same 6-member reflectance-target
ensemble:

- **4 surface targets** (desert, snow/ice, vegetation, water): analytic
  SRF x solar-spectrum x target-reflectance convolution, no libRadtran
  call at all -- see srf_reflectance.toa_reflectance. Matches the
  decision that VIS/SWIR gets no atmospheric-transmittance term in v1.
- **2 cloud targets** (liquid Mie, ice Baum): a real uvspec solar-mode run
  per band with `no_absorption mol` (disables only molecular/gas
  absorption, per libRadtran's own documented semantics -- preserves the
  cloud's own scattering AND absorption, which is the actual physical
  signal this project's SWIR band exists to sense), consistent with the
  "no atmosphere" simplification applied to the cloud case too. The
  resulting raw radiance is converted to reflectance via the standard
  R = pi*L/(E_solar*cos(sza)) relation, then convolved against each
  satellite's SRF the same way as the analytic targets.

Usage::

    python scripts/compute_srf_correction_vis_swir.py VIS --anchor goes-19 --target insat-3ds
    python scripts/compute_srf_correction_vis_swir.py SWIR --anchor goes-19 --target insat-3ds
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
from pathlib import Path

import numpy as np

_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC_DIR))

# WSL-side path to a libRadtran 2.0.6 install. Override via the
# LIBRADTRAN_ROOT environment variable if installed elsewhere.
LIBRADTRAN_ROOT = os.environ.get("LIBRADTRAN_ROOT", "/home/user/libRadtran-2.0.6")

from global_mosaic.satellites.srf_convolution import (  # noqa: E402
    SRFCurve,
    parse_srf_goes_cwg,
    parse_srf_himawari_xlsx,
    parse_srf_nwpsaf,
)
from global_mosaic.satellites.srf_reflectance import (  # noqa: E402
    ReflectanceSpectrum,
    SolarSpectrum,
    parse_splib07a_spectrum,
    parse_tsis1_hsrs,
    toa_reflectance,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRF_DATA_DIR = _REPO_ROOT / "docs" / "research" / "srf_data"
_VIS_SWIR_REF_DIR = _SRF_DATA_DIR / "vis_swir_reference"
_SOLAR_SPECTRUM_FILE = _VIS_SWIR_REF_DIR / "tsis1_hsrs_1nm.nc"
_SPLIB_DIR = _VIS_SWIR_REF_DIR / "splib07a_targets"

_SRF_FILES: dict[str, dict[str, tuple]] = {
    "VIS": {
        "goes-19": (parse_srf_goes_cwg, "goes-19_fm4/GOES-R_ABI_FM4_SRF_CWG_ch2.txt"),
        "goes-18": (parse_srf_goes_cwg, "goes-18_fm3/GOES-R_ABI_FM3_SRF_CWG_ch2.txt"),
        "insat-3ds": (parse_srf_nwpsaf, "insat-3ds/rtcoef_insat3_6_imager_srf_ch01.txt"),
        "insat-3dr": (parse_srf_nwpsaf, "insat-3dr/rtcoef_insat3_5_imager_srf_ch01.txt"),
        "insat-3d": (parse_srf_nwpsaf, "insat-3d/rtcoef_insat3_4_imager_srf_ch01.txt"),
        "meteosat-09": (parse_srf_nwpsaf, "meteosat-09_msg2_seviri/rtcoef_msg_2_seviri_srf_ch01.txt"),
        "meteosat-10": (parse_srf_nwpsaf, "meteosat-10_msg3_seviri/rtcoef_msg_3_seviri_srf_ch01.txt"),
    },
    "SWIR": {
        "goes-19": (parse_srf_goes_cwg, "goes-19_fm4/GOES-R_ABI_FM4_SRF_CWG_ch5.txt"),
        "goes-18": (parse_srf_goes_cwg, "goes-18_fm3/GOES-R_ABI_FM3_SRF_CWG_ch5.txt"),
        "insat-3ds": (parse_srf_nwpsaf, "insat-3ds/rtcoef_insat3_6_imager_srf_ch02.txt"),
        "insat-3dr": (parse_srf_nwpsaf, "insat-3dr/rtcoef_insat3_5_imager_srf_ch02.txt"),
        "insat-3d": (parse_srf_nwpsaf, "insat-3d/rtcoef_insat3_4_imager_srf_ch02.txt"),
        "meteosat-09": (parse_srf_nwpsaf, "meteosat-09_msg2_seviri/rtcoef_msg_2_seviri_srf_ch03.txt"),
        "meteosat-10": (parse_srf_nwpsaf, "meteosat-10_msg3_seviri/rtcoef_msg_3_seviri_srf_ch03.txt"),
    },
}
_HIMAWARI_SRF_XLSX = {
    "himawari-08": Path("data/srf/Himawari/AHI-08_SpectralResponsivity/AHI-8_SpectralResponsivity_Data.xlsx"),
    "himawari-09": Path("data/srf/Himawari/AHI-09_SpectralResponsivity/AHI-9_SpectralResponsivity_Data.xlsx"),
}
_HIMAWARI_BAND_SHEET = {"VIS": "Band 3", "SWIR": "Band 5"}

# Combined SRF coverage across all satellites:
# VIS ~0.5-0.8um, SWIR ~1.45-1.75um.
_WAVENUMBER_RANGE_CM = {"VIS": (12000.0, 21000.0), "SWIR": (5600.0, 7000.0)}

_SURFACE_TARGETS = {
    "desert": _SPLIB_DIR / "splib07a_Sand_GrndIsle1_no_oil_ASDFRa_AREF.txt",
    "snow_ice": _SPLIB_DIR / "splib07a_H2O-Ice_GDS136_77K_BECKa_AREF.txt",
    "vegetation": _SPLIB_DIR / "splib07a_Aspen_Leaf-A_DW92-2_BECKa_AREF.txt",
    "water": _SPLIB_DIR / "splib07a_Seawater_Open_Ocean_SW2_lwch_BECKa_AREF.txt",
}
_SZA_DEG = 30.0  # representative sun angle for the cloud RT case


def load_srf(satellite_key: str, band: str) -> SRFCurve:
    """Load a satellite's SRF curve for a given band, dispatching by data format.

    Himawari's SRF data lives in a local xlsx workbook (see
    ``_HIMAWARI_SRF_XLSX``); every other satellite's SRF is looked up in
    ``_SRF_FILES`` and parsed via its registered parser function.

    Args:
        satellite_key: Satellite key, e.g. ``"goes-19"``.
        band: Band name, ``"VIS"`` or ``"SWIR"``.

    Returns:
        The parsed :class:`SRFCurve` for this satellite/band.

    Raises:
        KeyError: If `satellite_key` has no registered SRF file for `band`.
    """
    if satellite_key in _HIMAWARI_SRF_XLSX:
        return parse_srf_himawari_xlsx(_HIMAWARI_SRF_XLSX[satellite_key], _HIMAWARI_BAND_SHEET[band])
    parser, rel_path = _SRF_FILES[band][satellite_key]
    return parser(_SRF_DATA_DIR / rel_path)


def run_uvspec_cloud_reflectance_spectrum(cloud_kind: str, wn_range_cm: tuple[float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Run one solar-mode uvspec run for a cloud target and return its reflectance spectrum.

    Reflectance is already converted from raw radiance via
    R = pi*L/(E_solar*cos(sza)), using TSIS-1's own irradiance at each
    wavelength (not a separate lookup) so the conversion is internally
    consistent with the solar spectrum uvspec was actually run against.

    Args:
        cloud_kind: ``"liquid"`` (Mie) or ``"ice"`` (Baum) cloud target.
        wn_range_cm: ``(low, high)`` wavenumber bound, in cm^-1, to
            request from uvspec (converted internally to the
            corresponding nm wavelength range).

    Returns:
        Tuple of ``(wavenumber_cm, reflectance)`` arrays, sorted
        ascending by wavenumber.

    Raises:
        subprocess.CalledProcessError: If the uvspec run fails.
    """
    # Rounded to whole nm: uvspec's solar_flux file reader requires the
    # `wavelength` directive's bounds to land exactly on a grid point in
    # the file (`spline` alone does not fix a fractional-nm boundary miss),
    # and tsis1_hsrs.dat has an exact value at every integer nm (checked
    # near 1428nm).
    lo_nm = np.floor(1.0e7 / wn_range_cm[1])
    hi_nm = np.ceil(1.0e7 / wn_range_cm[0])
    if cloud_kind == "liquid":
        cloud_lines = (
            f"wc_file 1D {LIBRADTRAN_ROOT}/examples/WC.DAT\n"
            "wc_properties mie interpolate\n"
        )
    else:
        cloud_lines = (
            f"ic_file 1D {LIBRADTRAN_ROOT}/examples/IC.DAT\n"
            "ic_properties baum interpolate\n"
        )
    inp = f"""atmosphere_file {LIBRADTRAN_ROOT}/data/atmmod/afglus.dat
data_files_path {LIBRADTRAN_ROOT}/data
source solar {LIBRADTRAN_ROOT}/data/solar_flux/tsis1_hsrs.dat
mol_abs_param crs
no_absorption mol
albedo 0.03
sza {_SZA_DEG}
rte_solver disort
number_of_streams 6
wavelength {lo_nm:.3f} {hi_nm:.3f}
spline {lo_nm:.3f} {hi_nm:.3f} 2.0
{cloud_lines}zout TOA
umu 1.0
phi 0
output_user lambda uu
quiet
"""
    result = subprocess.run(
        ["wsl.exe", "--", f"{LIBRADTRAN_ROOT}/bin/uvspec"],
        input=inp, capture_output=True, text=True, check=True,
    )
    rows = np.array([[float(x) for x in line.split()] for line in result.stdout.strip().splitlines()])
    lambda_nm, uu = rows[:, 0], rows[:, 1]

    solar = parse_tsis1_hsrs(_SOLAR_SPECTRUM_FILE)
    e_solar_at_lambda = np.interp(lambda_nm, solar.wavelength_nm, solar.irradiance_w_m2_nm) * 1000.0  # W->mW, match uu's mW convention
    reflectance = np.pi * uu / (e_solar_at_lambda * np.cos(np.radians(_SZA_DEG)))

    wavenumber_cm = 1.0e7 / lambda_nm
    order = np.argsort(wavenumber_cm)
    return wavenumber_cm[order], reflectance[order]


def compute_target_reflectances(band: str, srf: SRFCurve) -> dict[str, float]:
    """Compute one satellite's SRF-weighted TOA reflectance for all 6 reference targets.

    4 analytic surface targets (see ``_SURFACE_TARGETS``) via
    :func:`~global_mosaic.satellites.srf_reflectance.toa_reflectance`,
    plus 2 cloud targets (liquid, ice) via a real uvspec run (see
    :func:`run_uvspec_cloud_reflectance_spectrum`) convolved against
    `srf` the same way.

    Args:
        band: Band name, ``"VIS"`` or ``"SWIR"`` (selects the cloud
            run's wavenumber range).
        srf: The satellite's SRF curve for `band`.

    Returns:
        Dict mapping target name (the 4 keys of ``_SURFACE_TARGETS`` plus
        ``"liquid_cloud"``/``"ice_cloud"``) to this satellite's
        SRF-weighted TOA reflectance for that target.
    """
    solar = parse_tsis1_hsrs(_SOLAR_SPECTRUM_FILE)
    results = {}
    for name, path in _SURFACE_TARGETS.items():
        target = parse_splib07a_spectrum(path)
        results[name] = toa_reflectance(srf.wavenumber_cm, srf.response, solar, target)

    wn_range = _WAVENUMBER_RANGE_CM[band]
    for cloud_kind in ("liquid", "ice"):
        wn, refl_spectrum = run_uvspec_cloud_reflectance_spectrum(cloud_kind, wn_range)
        cloud_srf = SRFCurve(srf.wavenumber_cm, srf.response)
        interpolated = np.interp(cloud_srf.wavenumber_cm, wn, refl_spectrum)
        numerator = np.trapezoid(cloud_srf.response * interpolated, cloud_srf.wavenumber_cm)
        denominator = np.trapezoid(cloud_srf.response, cloud_srf.wavenumber_cm)
        results[f"{cloud_kind}_cloud"] = float(numerator / denominator)
    return results


def compute_bias(band: str, anchor_key: str, target_key: str) -> None:
    """Compute and print the SRF-weighted reflectance bias between two satellites.

    Computes both satellites' reflectance across the shared 6-target
    ensemble (see :func:`compute_target_reflectances`), averages the
    per-target bias, and prints the result as a ready-to-paste
    ``SRF_CORRECTIONS`` entry (see satellites/srf_correction.py).

    Args:
        band: Band name, ``"VIS"`` or ``"SWIR"``.
        anchor_key: Reference satellite key (the anchor,
            typically ``"goes-19"``) that `target_key` is corrected to match.
        target_key: Satellite key to compute a correction for.

    Returns:
        None. Prints per-target bias values and the final
        ``SRFCorrection`` entry to stdout.
    """
    anchor_srf = load_srf(anchor_key, band)
    target_srf = load_srf(target_key, band)

    anchor_reflectances = compute_target_reflectances(band, anchor_srf)
    target_reflectances = compute_target_reflectances(band, target_srf)

    biases = []
    for name in anchor_reflectances:
        # anchor - target, NOT target - anchor -- see compute_srf_correction.py's
        # identical comment on its own bias line for the full derivation.
        # `offset` gets ADDED to the target's raw reading (corrected =
        # raw*gain + offset), so it must be "how much to add to bring
        # target's reading onto the anchor's scale," i.e. anchor - target.
        # This was a real, confirmed sign bug (found via the WV empirical-
        # overlap-bias spot-check, 2026-08-05) shared by both this script
        # and the thermal-band one.
        bias = anchor_reflectances[name] - target_reflectances[name]
        biases.append(bias)
        print(f"  {name:12s}  anchor_refl={anchor_reflectances[name]:8.5f}  "
              f"target_refl={target_reflectances[name]:8.5f}  bias={bias:+.5f}")

    mean_bias = statistics.mean(biases)
    spread = statistics.pstdev(biases)
    print()
    print(f"{target_key} vs {anchor_key} ({band}): mean bias = {mean_bias:+.5f}, spread = {spread:.5f} "
          f"over {len(biases)} reflectance targets")
    print()
    print("SRF_CORRECTIONS entry:")
    print(f'    ("{target_key}", "{band}"): SRFCorrection(')
    print(f'        gain=1.0, offset={mean_bias:.5f},')
    print(f'        reference_satellite="{anchor_key}", method="srf-weighted-radiative-transfer",')
    print(f'        computed_date="TODO", srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",')
    print(f'        reference_spectrum_source="TSIS-1 HSRS + 6-target reflectance ensemble",')
    print(f'        reference_ensemble_spread={spread:.5f},')
    print(f'    ),')


def main() -> int:
    """CLI entry point: parse arguments and run :func:`compute_bias`.

    Returns:
        Exit code, always 0 on success.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("band", choices=["VIS", "SWIR"])
    parser.add_argument("--anchor", default="goes-19")
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    compute_bias(args.band, args.anchor, args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
