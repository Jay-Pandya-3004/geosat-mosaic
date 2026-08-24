"""Unit tests for satellites/band_registry.py. All synthetic -- checks
internal consistency and the real satpy band names (independently
cross-checked against the actual installed reader YAMLs:
abi_l1b.yaml, ahi_hsd.yaml, seviri_l1b_native.yaml,
readers/insat3d_img_l1b_h5.yaml)."""

from __future__ import annotations

from global_mosaic.satellites.band_registry import (
    BAND_CALIBRATION,
    BANDS,
    FAMILY_BAND_REGISTRY,
    get_band_spec,
    get_calibration,
)

FAMILIES = ("goes", "himawari", "meteosat", "insat")


def test_every_family_has_every_band():
    for family in FAMILIES:
        for band in BANDS:
            assert band in FAMILY_BAND_REGISTRY[family], f"{family} missing {band}"


def test_every_band_has_a_calibration():
    for band in BANDS:
        assert band in BAND_CALIBRATION


def test_thermal_bands_use_brightness_temperature():
    for band in ("MIR", "WV", "TIR1", "TIR2"):
        assert get_calibration(band) == "brightness_temperature"


def test_vis_uses_reflectance():
    assert get_calibration("VIS") == "reflectance"


def test_swir_uses_radiance_not_reflectance():
    """SWIR must NOT default to reflectance -- confirmed that
    GOES/Himawari default to reflectance while INSAT's reader only
    supports radiance for SWIR; radiance is the one calibration all four
    satellites actually share."""
    assert get_calibration("SWIR") == "radiance"


def test_get_band_spec_dispatches_by_family_prefix():
    spec = get_band_spec("goes-19", "TIR1")
    assert spec.satpy_band_name == "C13"
    assert spec.channel_no == 13

    spec = get_band_spec("himawari-09", "WV")
    assert spec.satpy_band_name == "B09"

    spec = get_band_spec("meteosat-10", "SWIR")
    assert spec.satpy_band_name == "IR_016"
    assert spec.channel_no == 3  # SEVIRI's own numbering, not GOES/Himawari's

    spec = get_band_spec("insat-3ds", "WV")
    assert spec.satpy_band_name == "WV"
    assert spec.channel_no == 4
