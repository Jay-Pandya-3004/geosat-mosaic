"""SRF-weighted radiative-transfer convolution math -- the actual
implementation of the "srf-weighted-radiative-transfer" method for
thermal/emissive bands (WV, and by extension MIR/TIR1/TIR2).

This module holds the pure, testable pieces (Planck conversion, SRF file
parsing, convolution). The driver that actually invokes libRadtran's
`uvspec` across an atmospheric-profile ensemble and writes results into
srf_correction.SRF_CORRECTIONS lives in
scripts/compute_srf_correction.py -- kept separate so this module has no
dependency on a libRadtran install being present to be imported/tested.

Physical approach: for a thermal band, what a satellite channel "measures"
is the top-of-atmosphere spectral radiance, weighted by that channel's real
SRF curve, then inverted through the Planck function to an equivalent
brightness temperature:

    L_channel = integral(SRF(v) * L_TOA(v) dv) / integral(SRF(v) dv)
    BT_channel = inverse_planck(L_channel, v_ref)

Two satellites observing the SAME real scene (same atmospheric profile)
disagree only because their SRF curves differ -- convolving the SAME
libRadtran-computed L_TOA(v) spectrum against each satellite's own real SRF
curve isolates exactly that spectral-shape effect, which is exactly what
this method is designed to capture.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Radiance constants in the "wavenumber, mW" convention standard across
# meteorological satellite calibration literature (matches the c1/c2 form
# used in each satellite's own onboard calibration, e.g. the quadratic
# BT<->radiance tables published for Himawari AHI -- this module implements
# the un-corrected baseline Planck inversion those build on top of, not
# each satellite's own further sensor-specific quadratic correction, which
# is a documented, deliberate v1 simplification, not an oversight).
_C1 = 1.191042e-5  # mW m^-2 sr^-1 (cm^-1)^-4
_C2 = 1.4387752  # cm K


def planck_radiance(wavenumber_cm: np.ndarray, temperature_k: float) -> np.ndarray:
    """Compute spectral radiance via Planck's law.

    Spectral radiance [mW m^-2 sr^-1 (cm^-1)^-1] via Planck's law, given
    wavenumber(s) in cm^-1 and a scalar temperature in Kelvin.

    Args:
        wavenumber_cm: Wavenumber(s), in cm^-1.
        temperature_k: Temperature, in Kelvin.

    Returns:
        Spectral radiance at each wavenumber, in mW m^-2 sr^-1 (cm^-1)^-1.
    """
    return _C1 * wavenumber_cm**3 / (np.exp(_C2 * wavenumber_cm / temperature_k) - 1.0)


def inverse_planck(radiance: float, wavenumber_cm: float) -> float:
    """Invert Planck's law to get brightness temperature from radiance.

    Brightness temperature [K] for a given radiance [mW m^-2 sr^-1
    (cm^-1)^-1] at a given wavenumber [cm^-1] -- the inverse of
    planck_radiance, evaluated at a single reference wavenumber (the
    channel's own SRF-weighted centroid, not integrated over the band;
    this is the standard single-wavenumber BT convention every satellite's
    own calibration uses, not a further approximation introduced here).

    Args:
        radiance: Spectral radiance, in mW m^-2 sr^-1 (cm^-1)^-1.
        wavenumber_cm: Reference wavenumber, in cm^-1.

    Returns:
        Brightness temperature, in Kelvin.
    """
    return _C2 * wavenumber_cm / np.log(1.0 + _C1 * wavenumber_cm**3 / radiance)


@dataclass(frozen=True)
class SRFCurve:
    """A channel's spectral response function curve.

    Attributes:
        wavenumber_cm: Ascending wavenumber grid, in cm^-1.
        response: Unitless relative response values, same length as
            `wavenumber_cm`.
    """

    wavenumber_cm: np.ndarray  # ascending, cm^-1
    response: np.ndarray  # unitless relative response, same length


def _srf_weighted_centroid_wavenumber(srf: SRFCurve) -> float:
    """Compute the SRF response-weighted mean (centroid) wavenumber.

    Response-weighted mean wavenumber -- used as the single reference
    wavenumber for this channel's inverse_planck call (see that function's
    docstring for why a single wavenumber, not a full-band integration, is
    the correct convention here).

    Args:
        srf: The channel's SRF curve.

    Returns:
        The response-weighted mean wavenumber, in cm^-1.
    """
    return float(np.trapezoid(srf.wavenumber_cm * srf.response, srf.wavenumber_cm) / np.trapezoid(srf.response, srf.wavenumber_cm))


def parse_srf_nwpsaf(path: Path) -> SRFCurve:
    """Parse an EUMETSAT NWP SAF RTTOV-coefficient SRF text file.

    Parses the EUMETSAT NWP SAF RTTOV-coefficient SRF text format (used
    for INSAT-3D/3DR/3DS and Meteosat MSG-2/3/4 in the downloaded NWP SAF
    SRF filter-response files): two header lines (name, "Number of data
    points:"), a point count, a
    column-header line, then `wavenumber_cm-1  filter_response` rows.
    Confirmed against a real file (insat-3ds ch02).

    Args:
        path: Path to the NWP SAF SRF text file.

    Returns:
        The parsed :class:`SRFCurve`, sorted ascending by wavenumber.
    """
    lines = path.read_text().splitlines()
    n_points = int(lines[2].strip())
    data_lines = lines[4 : 4 + n_points]
    rows = np.array([[float(x) for x in line.split()] for line in data_lines])
    wavenumber, response = rows[:, 0], rows[:, 1]
    order = np.argsort(wavenumber)
    return SRFCurve(wavenumber[order], response[order])


def parse_srf_goes_cwg(path: Path) -> SRFCurve:
    """Parse a NOAA GOES-R ABI CWG SRF text file.

    Parses NOAA's GOES-R ABI CWG SRF text format (used for GOES-18/19 in
    the downloaded GOES-R ABI CWG SRF filter-response files): `#`-prefixed
    header/comment lines, then
    `wavelength_um  wavenumber_cm-1  relative_srf` rows. Confirmed against
    a real file (goes-19 ch9).

    Args:
        path: Path to the GOES-R ABI CWG SRF text file.

    Returns:
        The parsed :class:`SRFCurve`, sorted ascending by wavenumber.
    """
    rows = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        rows.append([float(x) for x in stripped.split()])
    arr = np.array(rows)
    wavenumber, response = arr[:, 1], arr[:, 2]
    order = np.argsort(wavenumber)
    return SRFCurve(wavenumber[order], response[order])


def parse_srf_himawari_xlsx(path: Path, band_sheet: str) -> SRFCurve:
    """Parse an AHI-8/9 SpectralResponsivity_Data.xlsx SRF sheet.

    Parses the AHI-8/9 SpectralResponsivity_Data.xlsx format (local
    files, not re-downloadable via a live URL). One sheet per
    band (`band_sheet` e.g. "Band 9" for WV), 4 header rows (model/flight-
    model info, then a "Wavelength/Wavenumber/Relative Responsivity"
    column-header row, then a "[um]/[cm^-1]/[-]" units row), then
    `wavelength_um, wavenumber_cm-1, response` data rows. Confirmed against
    the real AHI-8 Band 9 sheet. Requires `openpyxl`
    (requirements.txt).

    Args:
        path: Path to the SpectralResponsivity_Data.xlsx workbook.
        band_sheet: Sheet name for the desired band, e.g. ``"Band 9"``.

    Returns:
        The parsed :class:`SRFCurve`, sorted ascending by wavenumber.
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[band_sheet]
    wavenumber, response = [], []
    for row in ws.iter_rows(min_row=5, values_only=True):
        if row[0] is None or row[1] is None:
            continue
        wavenumber.append(float(row[1]))
        response.append(float(row[2]))
    wb.close()
    wavenumber_arr = np.array(wavenumber)
    response_arr = np.array(response)
    order = np.argsort(wavenumber_arr)
    return SRFCurve(wavenumber_arr[order], response_arr[order])


def convolve_srf_with_spectrum(
    srf: SRFCurve, spectrum_wavenumber_cm: np.ndarray, spectrum_radiance: np.ndarray
) -> float:
    """Convolve a channel's SRF against a radiance spectrum to get effective radiance.

    SRF-weighted effective radiance: integral(SRF(v) * L(v) dv) /
    integral(SRF(v) dv), with the radiance spectrum linearly interpolated
    onto the SRF curve's own wavenumber grid (the SRF is the higher- or
    comparable-resolution curve we trust the shape of; the radiative-
    transfer spectrum is what gets resampled onto it, not the reverse).

    Args:
        srf: The channel's SRF curve.
        spectrum_wavenumber_cm: Wavenumber grid of the radiance spectrum
            (e.g. from a libRadtran run), in cm^-1.
        spectrum_radiance: Radiance values at `spectrum_wavenumber_cm`.

    Returns:
        The SRF-weighted effective radiance.

    Raises:
        ValueError: If the SRF's wavenumber range doesn't overlap
            `spectrum_wavenumber_cm`'s range at all -- a silent 0/0 or
            all-zero-weight result would be a wrong-looking-like-a-real-
            number failure, not something to let through quietly.
    """
    lo, hi = spectrum_wavenumber_cm.min(), spectrum_wavenumber_cm.max()
    if srf.wavenumber_cm.max() < lo or srf.wavenumber_cm.min() > hi:
        raise ValueError(
            f"SRF range [{srf.wavenumber_cm.min():.1f}, {srf.wavenumber_cm.max():.1f}] cm^-1 "
            f"does not overlap the supplied spectrum's range [{lo:.1f}, {hi:.1f}] cm^-1"
        )
    interpolated_radiance = np.interp(srf.wavenumber_cm, spectrum_wavenumber_cm, spectrum_radiance)
    numerator = np.trapezoid(srf.response * interpolated_radiance, srf.wavenumber_cm)
    denominator = np.trapezoid(srf.response, srf.wavenumber_cm)
    return float(numerator / denominator)


def effective_brightness_temperature(
    srf: SRFCurve, spectrum_wavenumber_cm: np.ndarray, spectrum_radiance: np.ndarray
) -> float:
    """Compute the brightness temperature a satellite channel would report for a TOA spectrum.

    The actual per-satellite quantity this module exists to compute:
    convolve a TOA radiance spectrum (from libRadtran, same spectrum for
    every satellite being compared -- see module docstring) against this
    satellite's real SRF curve, then invert through Planck at this
    channel's SRF-weighted centroid wavenumber.

    Args:
        srf: The channel's SRF curve.
        spectrum_wavenumber_cm: Wavenumber grid of the TOA radiance
            spectrum, in cm^-1.
        spectrum_radiance: TOA radiance values at
            `spectrum_wavenumber_cm`.

    Returns:
        The effective brightness temperature this channel would report
        for the given TOA spectrum, in Kelvin.
    """
    effective_radiance = convolve_srf_with_spectrum(srf, spectrum_wavenumber_cm, spectrum_radiance)
    centroid = _srf_weighted_centroid_wavenumber(srf)
    return inverse_planck(effective_radiance, centroid)
