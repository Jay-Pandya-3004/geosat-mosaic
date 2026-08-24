"""SRF-weighted TOA-reflectance convolution math for reflective bands
(VIS/SWIR) -- implements a solar-spectrum + reflectance-target ensemble
approach, the reflective-band counterpart to srf_convolution.py's
thermal/Planck-based WV machinery.

Physical approach: TOA reflectance for a channel is

    R_channel = pi * integral(SRF(l) * E_solar(l) * rho_target(l) dl)
                / integral(SRF(l) * E_solar(l) dl)

i.e. the SRF- and solar-spectrum-weighted mean of the target's own
reflectance spectrum -- NOT a radiative-transfer atmosphere simulation like
WV's. v1 was deliberately decided to have no atmospheric transmittance term, so
there's no libRadtran/uvspec call in this module at all for the surface
targets; only the cloud targets (handled by a separate driver step calling
uvspec in solar/reflective mode) need an RT run. This module only holds the
solar-spectrum-weighted reflectance math and the USGS splib07a parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

_BAD_BAND_SENTINEL = -1.23e34  # splib07a's own marker for a deleted/bad channel, per its own documentation


@dataclass(frozen=True)
class ReflectanceSpectrum:
    """A surface/cloud target's reflectance spectrum.

    Attributes:
        wavelength_um: Ascending wavelength grid, in micrometers.
        reflectance: Reflectance values (0-1) at each wavelength.
    """

    wavelength_um: np.ndarray  # ascending
    reflectance: np.ndarray  # 0-1


@dataclass(frozen=True)
class SolarSpectrum:
    """The solar irradiance spectrum used to weight reflective-band convolution.

    Attributes:
        wavelength_nm: Ascending wavelength grid, in nanometers.
        irradiance_w_m2_nm: Spectral irradiance at each wavelength, in
            W m^-2 nm^-1.
    """

    wavelength_nm: np.ndarray  # ascending
    irradiance_w_m2_nm: np.ndarray


def parse_tsis1_hsrs(path: Path) -> SolarSpectrum:
    """Parse the TSIS-1 HSRS v2 solar spectrum netCDF file.

    Parses the TSIS-1 HSRS v2 netCDF -- `Vacuum Wavelength` (nm)
    and `SSI` (W m^-2 nm^-1), confirmed against the real downloaded file
    (25281 points, 202-2730nm).

    Args:
        path: Path to the TSIS-1 HSRS v2 netCDF file.

    Returns:
        The parsed :class:`SolarSpectrum`, sorted ascending by wavelength.
    """
    import netCDF4

    ds = netCDF4.Dataset(path)
    wavelength_nm = np.asarray(ds.variables["Vacuum Wavelength"][:])
    irradiance = np.asarray(ds.variables["SSI"][:])
    ds.close()
    order = np.argsort(wavelength_nm)
    return SolarSpectrum(wavelength_nm[order], irradiance[order])


def _splib07a_wavelength_file_for(spectrum_path: Path) -> Path:
    """Locate the sibling wavelength-grid file matching a splib07a spectrum file.

    splib07a spectra are named `..._<SPECTROMETER>a_AREF.txt` (e.g.
    `..._BECKa_AREF.txt`, `..._ASDFRa_AREF.txt`) and share one wavelength
    grid file per spectrometer. Looked up as a sibling in the same
    directory as `spectrum_path` -- this project's own copy of the USGS
    splib07a target library is flattened out of the original archive's
    per-chapter subfolders, unlike the raw USGS download.

    Args:
        spectrum_path: Path to a splib07a spectrum ASCII file.

    Returns:
        Path to the matching wavelength-grid file in the same directory.

    Raises:
        ValueError: If `spectrum_path`'s filename doesn't contain a
            recognized spectrometer suffix.
    """
    name = spectrum_path.stem
    spectrometer_map = {
        "BECKa": "splib07a_Wavelengths_BECK_Beckman_0.2-3.0_microns.txt",
        "ASDFRa": "splib07a_Wavelengths_ASD_0.35-2.5_microns_2151_ch.txt",
        "ASDNGa": "splib07a_Wavelengths_ASD_0.35-2.5_microns_2151_ch.txt",
        "AVIRISb": "splib07a_Wavelengths_AVIRIS_1996_0.37-2.5_microns.txt",
    }
    for suffix, wl_file in spectrometer_map.items():
        if suffix in name:
            return spectrum_path.parent / wl_file
    raise ValueError(f"Unrecognized spectrometer suffix in {spectrum_path.name}; add it to spectrometer_map")


def parse_splib07a_spectrum(path: Path) -> ReflectanceSpectrum:
    """Parse one USGS splib07a surface-reflectance target spectrum file.

    Parses one USGS splib07a ASCII spectrum file (one of this project's surface
    reflectance targets) plus its matching wavelength-grid file. Bad/
    deleted channels (sentinel -1.23e34, splib07a's own documented marker)
    are dropped, not treated as real near-zero reflectance.

    Args:
        path: Path to the splib07a ASCII reflectance spectrum file.

    Returns:
        The parsed :class:`ReflectanceSpectrum`, sorted ascending by
        wavelength, with bad/deleted-channel points removed.
    """
    wl_path = _splib07a_wavelength_file_for(path)
    wavelength_um = np.array([float(x) for x in wl_path.read_text().splitlines()[1:]])
    reflectance = np.array([float(x) for x in path.read_text().splitlines()[1:]])
    valid = reflectance > (_BAD_BAND_SENTINEL / 2)  # anything not the sentinel; real reflectance is 0-1
    wavelength_um, reflectance = wavelength_um[valid], reflectance[valid]
    order = np.argsort(wavelength_um)
    return ReflectanceSpectrum(wavelength_um[order], reflectance[order])


def toa_reflectance(srf_wavenumber_cm: np.ndarray, srf_response: np.ndarray, solar: SolarSpectrum, target: ReflectanceSpectrum) -> float:
    """Compute the SRF- and solar-spectrum-weighted TOA reflectance for a target.

    R = integral(SRF * E_solar * rho) / integral(SRF * E_solar) -- the
    solar-spectrum-and-target-weighted mean reflectance a channel with this
    SRF would report for this target, per this module's docstring. All
    three inputs (SRF, solar spectrum, target reflectance) are resampled
    onto the SRF's own wavelength grid (converted from its native
    wavenumber), since the SRF is the curve whose real shape matters most
    here and the other two are broader/smoother by comparison.

    Args:
        srf_wavenumber_cm: Channel SRF's wavenumber grid, in cm^-1.
        srf_response: Channel SRF's relative response values, same
            length as `srf_wavenumber_cm`.
        solar: Reference solar spectrum.
        target: Target surface/cloud reflectance spectrum.

    Returns:
        The SRF- and solar-weighted mean reflectance (dimensionless,
        typically 0-1) this channel would report for `target`.
    """
    srf_wavelength_nm = 1.0e7 / srf_wavenumber_cm
    order = np.argsort(srf_wavelength_nm)
    wl_nm, response = srf_wavelength_nm[order], srf_response[order]

    solar_on_srf_grid = np.interp(wl_nm, solar.wavelength_nm, solar.irradiance_w_m2_nm)
    target_on_srf_grid = np.interp(wl_nm, target.wavelength_um * 1000.0, target.reflectance)

    numerator = np.trapezoid(response * solar_on_srf_grid * target_on_srf_grid, wl_nm)
    denominator = np.trapezoid(response * solar_on_srf_grid, wl_nm)
    return float(numerator / denominator)
