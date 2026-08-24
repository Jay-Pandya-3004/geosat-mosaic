"""Unit tests for satellites/srf_reflectance.py -- the VIS/SWIR
counterpart to test_srf_convolution.py's thermal/WV tests. Parser tests
run against the real downloaded files (TSIS-1 HSRS, USGS
splib07a targets), not synthetic fixtures."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from global_mosaic.satellites.srf_convolution import parse_srf_goes_cwg
from global_mosaic.satellites.srf_reflectance import (
    ReflectanceSpectrum,
    parse_splib07a_spectrum,
    parse_tsis1_hsrs,
    toa_reflectance,
)

_VIS_SWIR_REF_DIR = Path(__file__).resolve().parents[2] / "docs" / "research" / "srf_data" / "vis_swir_reference"
_TSIS1_FILE = _VIS_SWIR_REF_DIR / "tsis1_hsrs_1nm.nc"
_H2O_ICE_FILE = _VIS_SWIR_REF_DIR / "splib07a_targets" / "splib07a_H2O-Ice_GDS136_77K_BECKa_AREF.txt"
_GOES19_SWIR_SRF = Path(__file__).resolve().parents[2] / "docs" / "research" / "srf_data" / "goes-19_fm4" / "GOES-R_ABI_FM4_SRF_CWG_ch5.txt"


@pytest.mark.skipif(not _TSIS1_FILE.exists(), reason="TSIS-1 HSRS file not present in this checkout")
def test_parse_tsis1_hsrs_real_file():
    solar = parse_tsis1_hsrs(_TSIS1_FILE)
    assert len(solar.wavelength_nm) == 25281
    assert solar.wavelength_nm.min() == pytest.approx(202.0, abs=1.0)
    assert solar.wavelength_nm.max() == pytest.approx(2730.0, abs=1.0)
    assert np.all(np.diff(solar.wavelength_nm) > 0)
    assert np.all(solar.irradiance_w_m2_nm > 0)


@pytest.mark.skipif(not _H2O_ICE_FILE.exists(), reason="splib07a target files not present in this checkout")
def test_parse_splib07a_spectrum_drops_bad_bands():
    spectrum = parse_splib07a_spectrum(_H2O_ICE_FILE)
    assert len(spectrum.wavelength_um) > 0
    assert len(spectrum.wavelength_um) == len(spectrum.reflectance)
    # Bad-band sentinel (-1.23e34) must never survive into the parsed output.
    assert np.all(spectrum.reflectance > -1.0)
    assert np.all(np.diff(spectrum.wavelength_um) > 0)
    # Ice's real, physically expected shape: high-ish VIS reflectance,
    # sharp drop by ~1.5um (the same absorption feature this project's
    # whole SWIR/snow-cloud-phase design keeps citing).
    vis_idx = np.argmin(np.abs(spectrum.wavelength_um - 0.6))
    swir_idx = np.argmin(np.abs(spectrum.wavelength_um - 1.6))
    assert spectrum.reflectance[swir_idx] < spectrum.reflectance[vis_idx]


def test_toa_reflectance_flat_srf_flat_target_recovers_the_reflectance():
    """If the target is a perfectly flat (gray) reflector at some constant
    rho, the SRF-weighted TOA reflectance must equal exactly rho,
    regardless of the solar spectrum's shape -- confirms the convolution
    doesn't introduce a spurious solar-spectrum-shape bias for a target
    with no real spectral structure of its own."""
    srf_wn = np.linspace(1e7 / 700, 1e7 / 500, 50)  # ~500-700nm
    srf_response = np.ones_like(srf_wn)

    from global_mosaic.satellites.srf_reflectance import SolarSpectrum

    solar_wl = np.linspace(400, 800, 200)
    solar_irr = 1.0 + 0.3 * np.sin(solar_wl / 50.0)  # deliberately non-flat solar spectrum
    solar = SolarSpectrum(solar_wl, solar_irr)

    flat_target = ReflectanceSpectrum(
        wavelength_um=np.linspace(0.3, 1.0, 100), reflectance=np.full(100, 0.42)
    )
    result = toa_reflectance(srf_wn, srf_response, solar, flat_target)
    assert result == pytest.approx(0.42, abs=1e-6)


@pytest.mark.skipif(
    not (_TSIS1_FILE.exists() and _H2O_ICE_FILE.exists() and _GOES19_SWIR_SRF.exists()),
    reason="real reference files not present in this checkout",
)
def test_toa_reflectance_real_goes19_swir_against_ice():
    """End-to-end sanity check with real data: GOES-19's real SWIR SRF
    convolved against ice's real reflectance spectrum and the real TSIS-1
    solar spectrum should land in a physically plausible 0-1 reflectance
    range, not blow up or go negative."""
    srf = parse_srf_goes_cwg(_GOES19_SWIR_SRF)
    solar = parse_tsis1_hsrs(_TSIS1_FILE)
    ice = parse_splib07a_spectrum(_H2O_ICE_FILE)
    result = toa_reflectance(srf.wavenumber_cm, srf.response, solar, ice)
    assert 0.0 <= result <= 1.5  # TOA reflectance can exceed 1.0 for specular/bright targets in some conventions
