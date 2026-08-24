"""Unit tests for satellites/srf_convolution.py. Parser tests run against
the real downloaded SRF filter-response files (not synthetic
fixtures) -- if those files ever move/get re-fetched in a different
format, these tests are meant to catch it."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from global_mosaic.satellites.srf_convolution import (
    SRFCurve,
    convolve_srf_with_spectrum,
    effective_brightness_temperature,
    inverse_planck,
    parse_srf_goes_cwg,
    parse_srf_nwpsaf,
    planck_radiance,
)

_SRF_DATA_DIR = Path(__file__).resolve().parents[2] / "docs" / "research" / "srf_data"
_INSAT_3DS_WV = _SRF_DATA_DIR / "insat-3ds" / "rtcoef_insat3_6_imager_srf_ch04.txt"
_GOES_19_WV = _SRF_DATA_DIR / "goes-19_fm4" / "GOES-R_ABI_FM4_SRF_CWG_ch9.txt"


def test_planck_inverse_planck_round_trip():
    wavenumber, temperature = 1450.0, 260.0
    radiance = planck_radiance(np.array([wavenumber]), temperature)[0]
    assert inverse_planck(radiance, wavenumber) == pytest.approx(temperature, abs=1e-6)


def test_planck_radiance_increases_with_temperature():
    wavenumber = np.array([1450.0])
    assert planck_radiance(wavenumber, 280.0)[0] > planck_radiance(wavenumber, 220.0)[0]


@pytest.mark.skipif(not _INSAT_3DS_WV.exists(), reason="SRF filter-response files not present in this checkout")
def test_parse_srf_nwpsaf_real_insat_wv_file():
    srf = parse_srf_nwpsaf(_INSAT_3DS_WV)
    assert len(srf.wavenumber_cm) == 163
    # Real range confirmed by inspection: 1371.7-1543.2 cm^-1.
    assert srf.wavenumber_cm.min() == pytest.approx(1371.7421, abs=0.01)
    assert srf.wavenumber_cm.max() == pytest.approx(1543.2099, abs=0.01)
    assert np.all(np.diff(srf.wavenumber_cm) > 0)  # sorted ascending
    assert np.all(srf.response >= 0)


@pytest.mark.skipif(not _GOES_19_WV.exists(), reason="SRF filter-response files not present in this checkout")
def test_parse_srf_goes_cwg_real_wv_file():
    srf = parse_srf_goes_cwg(_GOES_19_WV)
    assert len(srf.wavenumber_cm) == 2105
    # Real range confirmed by inspection: 1342.6-1553.0 cm^-1.
    assert srf.wavenumber_cm.min() == pytest.approx(1342.6, abs=0.1)
    assert srf.wavenumber_cm.max() == pytest.approx(1553.0, abs=0.1)
    assert np.all(np.diff(srf.wavenumber_cm) > 0)


@pytest.mark.skipif(
    not (_SRF_DATA_DIR / "goes-19_abi_nwpsaf").exists(),
    reason="SRF filter-response files not present in this checkout",
)
@pytest.mark.parametrize(
    "satellite_key,fm_dir,fm_prefix,channel",
    [
        ("goes-19", "goes-19_fm4", "GOES-R_ABI_FM4_SRF_CWG", ch)
        for ch in (2, 5, 7, 9, 13, 15)
    ]
    + [
        ("goes-18", "goes-18_fm3", "GOES-R_ABI_FM3_SRF_CWG", ch)
        for ch in (2, 5, 7, 9, 13, 15)
    ],
)
def test_goes_cwg_and_nwpsaf_srf_sources_are_identical(satellite_key, fm_dir, fm_prefix, channel):
    """GOES's two SRF sources (NOAA CWG release
    files under goes-XX_fmN/ vs NWP SAF's own RTTOV-coefficient-format
    repackaging under goes-XX_abi_nwpsaf/) were previously un-reconciled -- resolved
    2026-08-05 by diffing every band used by this project (VIS/SWIR/MIR/
    WV/TIR1/TIR2) for both goes-18 and goes-19: bit-for-bit identical
    wavenumber grids and response values in every case, 0.0 max diff.
    Not a coincidence -- the NWP SAF file's own header line literally
    reads "GOES 6.95um ABI FM4 SRF CWG processed ...", confirming it's a
    reformatting of the same underlying CWG measurement, not an
    independent one. This test locks that finding in as a regression
    guard: if either source is ever re-fetched independently and the two
    diverge, this should fail rather than silently going unnoticed."""
    sat_num = satellite_key.split("-")[1]
    cwg = parse_srf_goes_cwg(_SRF_DATA_DIR / fm_dir / f"{fm_prefix}_ch{channel}.txt")
    nwp = parse_srf_nwpsaf(_SRF_DATA_DIR / f"{satellite_key}_abi_nwpsaf" / f"rtcoef_goes_{sat_num}_abi_srf_ch{channel:02d}.txt")
    assert np.array_equal(cwg.wavenumber_cm, nwp.wavenumber_cm)
    assert np.array_equal(cwg.response, nwp.response)


def test_convolve_flat_spectrum_returns_the_constant():
    srf = SRFCurve(wavenumber_cm=np.linspace(1300, 1600, 50), response=np.ones(50))
    spectrum_wn = np.linspace(1200, 1700, 500)
    spectrum_radiance = np.full_like(spectrum_wn, 42.0)
    assert convolve_srf_with_spectrum(srf, spectrum_wn, spectrum_radiance) == pytest.approx(42.0)


def test_convolve_raises_on_non_overlapping_ranges():
    srf = SRFCurve(wavenumber_cm=np.linspace(1300, 1600, 50), response=np.ones(50))
    spectrum_wn = np.linspace(2000, 2500, 100)
    spectrum_radiance = np.ones_like(spectrum_wn)
    with pytest.raises(ValueError, match="does not overlap"):
        convolve_srf_with_spectrum(srf, spectrum_wn, spectrum_radiance)


def test_effective_brightness_temperature_recovers_blackbody_temperature():
    """A TOA spectrum that's an exact blackbody at T should convolve back
    to very close to T for any SRF shape -- confirms the whole convolve ->
    invert-Planck chain is self-consistent, not just each half in
    isolation. Not exactly T (integrating a nonlinear function's average
    isn't the same as evaluating it at the average), but close for a
    ~50 cm^-1-wide band."""
    true_temperature = 260.0
    spectrum_wn = np.linspace(1300, 1600, 500)
    spectrum_radiance = planck_radiance(spectrum_wn, true_temperature)

    srf = SRFCurve(wavenumber_cm=np.linspace(1420, 1470, 30), response=np.ones(30))
    recovered = effective_brightness_temperature(srf, spectrum_wn, spectrum_radiance)
    assert recovered == pytest.approx(true_temperature, abs=0.5)
