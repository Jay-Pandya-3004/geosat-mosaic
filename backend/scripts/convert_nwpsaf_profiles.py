#!/usr/bin/env python
"""Converts EUMETSAT NWP SAF's ECMWF_83P_91L.dat diverse-profile dataset
(the designed reference atmosphere ensemble) into libRadtran's
`atmosphere_file` format, so
compute_srf_correction.py can use the real, full ensemble instead of the
6-atmosphere AFGL interim set.

**Only 80 of the 83 profiles are real individual atmospheric states.**
The dataset's own header says so explicitly: "Profile 81 is an envelope of
minimum values, Profile 82 is an envelope of maximum values, Profile 83 is
the mean profile" -- these three are synthetic summary statistics, not real
ECMWF analyses, and including them in a "genuinely diverse real atmospheres"
ensemble (the whole reason this dataset was picked over an assumed/synthetic
one) would be self-defeating. Excluded here, not silently included.

Column mapping, NWP SAF -> libRadtran afgl format:
    NWP SAF:    P(hPa)  T(K)  WV(ppmv)  CO2(ppmv)  O3(ppmv)  N2O(ppmv)  CO(ppmv)  CH4(ppmv)
    libRadtran: z(km)  p(mb)  T(K)  air(cm-3)  o3(cm-3)  o2(cm-3)  h2o(cm-3)  co2(cm-3)  no2(cm-3)

- air number density: ideal gas law, n = p/(k_B T), with p in dyn/cm^2
  (=hPa*1000) and k_B in cgs (erg/K), giving n directly in cm^-3.
- o3/h2o/co2: VMR[ppmv] * 1e-6 * n_air (N2O/CO/CH4 aren't in libRadtran's
  column set and are dropped -- radiatively minor compared to O3/H2O/CO2 in
  the 6-8um WV window this project cares about).
- o2: NWP SAF doesn't include it at all (O2's atmospheric fraction is
  essentially globally constant and not meteorologically variable) -- filled
  from the standard dry-air mole fraction (0.20946) * n_air, not a per-
  profile quantity.
- no2: NWP SAF doesn't include it either, and unlike O2 there's no standard
  constant fraction to fall back on -- set to 0. Deliberate, documented
  simplification: NO2 absorption is predominantly UV/visible, radiatively
  negligible in the thermal-IR WV band this conversion is for.
- z(km): NWP SAF's own file has no altitude column. Computed here via
  hydrostatic integration (hypsometric equation) top-down from each
  profile's own real T(p) structure, anchored at the top (thinnest-
  pressure) level to libRadtran's own afglus.dat standard atmosphere via
  log-pressure interpolation -- the top level's real altitude barely
  varies across real atmospheric states at that pressure (~90+ km,
  essentially invariant mesospheric pressure-height relationship), so
  anchoring there and integrating each profile's own T(p) downward gives a
  self-consistent altitude scale per profile without needing an
  independent absolute reference.

  **Known, measured limitation, not silently assumed accurate**: validated
  this integration method by feeding it afglus.dat's OWN (p, T) pairs and
  comparing against afglus.dat's OWN known z values -- it does not
  reproduce them exactly, accumulating a ~1.1 km (~1.3%) discrepancy by the
  surface over an ~85 km column (likely simplified dry-air/geometric-vs-
  geopotential-height assumptions here vs whatever more precise method
  generated afglus.dat originally). Not fixed further because the actual
  quantity this whole pipeline computes -- the BT bias BETWEEN two
  satellites -- is a differential measurement: both satellites are
  convolved against the exact same libRadtran-computed radiance spectrum
  for a given atmosphere, so a systematic z-reconstruction error affects
  both identically and largely cancels in the difference. It would only
  matter to the extent it changes the radiance spectrum's SHAPE
  differently at different wavenumbers within the WV band -- a real but
  second-order effect, not assumed away, just not chased further given
  where it can and can't bias this specific measurement.

This script is meant to run INSIDE WSL (it shells out to nothing itself;
see run_inside_wsl.py for the thin Windows-side wrapper that invokes it via
`wsl.exe python3`).

Usage (inside WSL):
    python3 convert_nwpsaf_profiles.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

_SYNTHETIC_PROFILE_NUMBERS = {81, 82, 83}  # min/max envelope, mean -- not real states, see module docstring
_K_BOLTZMANN_ERG_PER_K = 1.380649e-16  # cgs: erg/K
_O2_DRY_AIR_FRACTION = 0.20946


def _hydrostatic_altitudes_km(pressure_hpa: np.ndarray, temperature_k: np.ndarray, top_altitude_km: float) -> np.ndarray:
    """Integrate the hypsometric equation top-down to derive per-level altitude.

    z[i] = z[i-1] + (R*Tbar/g0) * ln(p[i-1]/p[i]), using the mean
    temperature of each layer and the dry-air gas constant/gravity.
    Index 0 is the highest altitude/lowest pressure, matching both NWP
    SAF's and libRadtran's own row order.

    Args:
        pressure_hpa: Pressure at each level, in hPa, ordered from top
            (index 0) to surface.
        temperature_k: Temperature at each level, in Kelvin, same order
            and length as `pressure_hpa`.
        top_altitude_km: Reference altitude, in km, for the top
            (index 0) level (see :func:`_top_altitude_from_afglus`).

    Returns:
        Altitude in km at each level, same order/length as
        `pressure_hpa`.
    """
    r_specific = 287.05  # J/(kg K), dry air
    g0 = 9.80665  # m/s^2
    altitudes_m = np.empty_like(pressure_hpa)
    altitudes_m[0] = top_altitude_km * 1000.0
    for i in range(1, len(pressure_hpa)):
        t_mean = 0.5 * (temperature_k[i - 1] + temperature_k[i])
        dz = (r_specific * t_mean / g0) * np.log(pressure_hpa[i - 1] / pressure_hpa[i])
        altitudes_m[i] = altitudes_m[i - 1] + dz
    return altitudes_m / 1000.0


def _top_altitude_from_afglus(top_pressure_hpa: float, afglus_path: Path) -> float:
    """Interpolate libRadtran's afglus.dat to get a reference altitude for a top pressure level.

    Interpolates libRadtran's own afglus.dat (z, p, ...) in log-pressure
    to get a real reference altitude for the NWP SAF profile's own top
    (thinnest) pressure level -- see module docstring for why anchoring
    here, not at the surface, keeps this self-consistent per profile.

    Args:
        top_pressure_hpa: The NWP SAF profile's own top (thinnest)
            pressure level, in hPa.
        afglus_path: Path to libRadtran's ``afglus.dat`` standard
            atmosphere file.

    Returns:
        The interpolated reference altitude, in km, at `top_pressure_hpa`.
    """
    rows = []
    for line in afglus_path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        rows.append([float(x) for x in line.split()])
    arr = np.array(rows)
    z_km, p_mb = arr[:, 0], arr[:, 1]
    order = np.argsort(p_mb)
    return float(np.interp(np.log(top_pressure_hpa), np.log(p_mb[order]), z_km[order]))


def parse_nwpsaf_profiles(path: Path) -> list[dict]:
    """Parse NWP SAF's ECMWF_83P_91L.dat into per-profile pressure/temperature/gas data.

    Also parses and applies each profile's own actual surface pressure
    (header line 2's 6th field): the 101-level grid is a FIXED pressure
    ladder shared by every profile regardless of its actual surface
    pressure -- profile 1's header states 977.38 hPa surface pressure, but
    its data rows run all the way to a fixed 1100 hPa regardless -- so
    every profile's lowest few levels are fictitious below-ground
    extrapolations unless truncated here. This is a meaningful correctness
    fix, not a cosmetic one -- feeding libRadtran phantom underground
    atmosphere would corrupt the hydrostatic integration and the
    lower-atmosphere radiative transfer.

    Args:
        path: Path to NWP SAF's ``ECMWF_83P_91L.dat`` file.

    Returns:
        List of per-profile dicts with keys ``"number"`` (int profile
        number), ``"data"`` (2D array of the profile's rows, truncated
        to levels at or below the profile's own surface pressure), and
        ``"surface_pressure_hpa"``.
    """
    lines = path.read_text().splitlines()
    profiles = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("Profile"):
            profile_number = int(line.split()[1])
            i += 1
            surface_pressure_hpa = float(lines[i].split()[5])
            i += 1
            data_rows = []
            while i < len(lines) and not lines[i].strip().startswith("Profile"):
                if lines[i].strip():
                    data_rows.append([float(x) for x in lines[i].split()])
                i += 1
            data = np.array(data_rows)
            data = data[data[:, 0] <= surface_pressure_hpa]
            profiles.append({"number": profile_number, "data": data, "surface_pressure_hpa": surface_pressure_hpa})
        else:
            i += 1
    return profiles


def convert_profile_to_afgl_lines(data: np.ndarray, afglus_path: Path) -> list[str]:
    """Convert one NWP SAF profile's raw rows into libRadtran atmosphere-file lines.

    Derives air/o3/o2/h2o/co2/no2 number densities and a per-level
    altitude column from the NWP SAF profile's pressure/temperature/gas
    volume-mixing-ratio columns -- see the module docstring's "Column
    mapping" section for the full derivation and the deliberate
    simplifications (O2 from a fixed dry-air fraction, NO2 zeroed,
    altitude via hydrostatic integration anchored at the top level).

    Args:
        data: One profile's raw data rows (from
            :func:`parse_nwpsaf_profiles`), columns
            ``[P(hPa), T(K), WV(ppmv), CO2(ppmv), O3(ppmv), ...]``.
        afglus_path: Path to libRadtran's ``afglus.dat`` standard
            atmosphere file, used to anchor the altitude scale.

    Returns:
        Lines of a libRadtran ``atmosphere_file``-format text file
        (header comment plus one row per level), ready to join with
        ``"\\n"`` and write to disk.
    """
    pressure_hpa, temperature_k = data[:, 0], data[:, 1]
    wv_ppmv, co2_ppmv, o3_ppmv = data[:, 2], data[:, 3], data[:, 4]

    n_air_cm3 = (pressure_hpa * 1000.0) / (_K_BOLTZMANN_ERG_PER_K * temperature_k)  # dyn/cm^2 / (erg/K * K) = cm^-3

    n_o3 = o3_ppmv * 1e-6 * n_air_cm3
    n_o2 = _O2_DRY_AIR_FRACTION * n_air_cm3
    n_h2o = wv_ppmv * 1e-6 * n_air_cm3
    n_co2 = co2_ppmv * 1e-6 * n_air_cm3
    n_no2 = np.zeros_like(n_air_cm3)

    top_altitude_km = _top_altitude_from_afglus(pressure_hpa[0], afglus_path)
    z_km = _hydrostatic_altitudes_km(pressure_hpa, temperature_k, top_altitude_km)

    lines = [
        "#      z(km)      p(mb)        T(K)    air(cm-3)    o3(cm-3)     o2(cm-3)    h2o(cm-3)    co2(cm-3)     no2(cm-3)"
    ]
    for i in range(len(pressure_hpa)):
        lines.append(
            f"{z_km[i]:12.4f}{pressure_hpa[i]:12.5f}{temperature_k[i]:12.3f}"
            f"{n_air_cm3[i]:13.6E}{n_o3[i]:13.6E}{n_o2[i]:13.6E}{n_h2o[i]:13.6E}{n_co2[i]:13.6E}{n_no2[i]:13.6E}"
        )
    return lines


def main() -> int:
    """CLI entry point: convert every real (non-synthetic) NWP SAF profile to libRadtran format.

    Reads NWP SAF's ECMWF_83P_91L.dat, skips the 3 synthetic summary
    profiles (see ``_SYNTHETIC_PROFILE_NUMBERS``), and writes one
    ``nwpsaf_profile_<NN>.dat`` file per real profile to ``/tmp/nwpsaf_afgl``.

    Returns:
        Exit code, always 0 on success.
    """
    nwpsaf_file = Path.home() / "nwpsaf_profiles" / "ECMWF_83P_91L.dat"
    afglus_path = Path.home() / "libRadtran-2.0.6" / "data" / "atmmod" / "afglus.dat"
    output_dir = Path("/tmp/nwpsaf_afgl")
    output_dir.mkdir(parents=True, exist_ok=True)

    profiles = parse_nwpsaf_profiles(nwpsaf_file)
    written = 0
    for p in profiles:
        if p["number"] in _SYNTHETIC_PROFILE_NUMBERS:
            continue
        lines = convert_profile_to_afgl_lines(p["data"], afglus_path)
        out_path = output_dir / f"nwpsaf_profile_{p['number']:02d}.dat"
        out_path.write_text("\n".join(lines) + "\n")
        written += 1
    print(f"wrote {written} profiles to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
