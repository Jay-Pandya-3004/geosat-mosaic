"""Per-band (VIS/SWIR/MIR/WV/TIR1/TIR2) satpy load recipe + calibration
mode, for all four satellite families. Generalizes run_phase0.py's
Phase-0-only `IR_WINDOW_CHANNEL_NO` (which only covered the one IR-window
channel) to the full Phase 2 shared 6-channel set.

`channel_no` is the integer key into a satellite's own
sat_metadata.py `Instruments["Imager"]["Channels"]` dict (used by
_channel_wavelength_and_resolution-style lookups for nominal resolution/
wavelength) -- checked directly against the actual entries, not
assumed: GOES-19 and Himawari-9's tables literally number channels 1-16
matching their band numbers (C02/B03 etc.), and several entries are
already annotated "(this project's Phase-2 <band>)" confirming the
mapping. Meteosat's SEVIRI numbering is NOT the same sequence (its own
1-12 channel table has VIS/SWIR/MIR/WV at different positions) -- read
directly from meteosat-09's own Channels dict, not inferred from GOES's
numbering.

`satpy_band_name` is the string passed to `Scene.load([...])` -- satpy's
own per-reader dataset name, independent of the channel_no above.

`calibration` is checked against actual GOES-18/
Himawari-9/INSAT-3DS files (see processing/illumination.py's module
docstring for the raw measurements):

- MIR/WV/TIR1/TIR2: brightness_temperature is already satpy's default
  for all three readers tested, but request it explicitly anyway so a
  reader that ever lacks it fails loudly instead of silently falling
  back to radiance.
- VIS: reflectance.
- SWIR: radiance -- NOT the default for GOES/Himawari (which default to
  reflectance, confirmed against real C05/B05 files), but it's the only
  calibration INSAT's own reader supports for SWIR at all (no
  reflectance/BT option in insat3d_img_l1b_h5.yaml) -- must be
  requested explicitly for all four satellites so the same band isn't
  silently stored as percent-reflectance for two satellites and
  physical radiance for the third.
"""

from __future__ import annotations

from dataclasses import dataclass

BANDS = ("VIS", "SWIR", "MIR", "WV", "TIR1", "TIR2")

BAND_CALIBRATION: dict[str, str] = {
    "VIS": "reflectance",
    "SWIR": "radiance",
    "MIR": "brightness_temperature",
    "WV": "brightness_temperature",
    "TIR1": "brightness_temperature",
    "TIR2": "brightness_temperature",
}


@dataclass(frozen=True)
class BandSpec:
    """A satellite family's load recipe for one logical band.

    Attributes:
        channel_no: Integer key into the satellite's own
            ``sat_metadata.py`` ``Instruments["Imager"]["Channels"]``
            dict, used for nominal resolution/wavelength lookups.
        satpy_band_name: The dataset name string passed to
            ``Scene.load([...])`` for this reader.
    """

    channel_no: int
    satpy_band_name: str


# Family key = satellite_key.split("-")[0], matching run_phase0.py's
# existing convention (IR_WINDOW_CHANNEL_NO / fetch_slot's dispatch).
FAMILY_BAND_REGISTRY: dict[str, dict[str, BandSpec]] = {
    "goes": {
        "VIS": BandSpec(2, "C02"),
        "SWIR": BandSpec(5, "C05"),
        "MIR": BandSpec(7, "C07"),
        "WV": BandSpec(9, "C09"),
        "TIR1": BandSpec(13, "C13"),
        "TIR2": BandSpec(15, "C15"),
    },
    "himawari": {
        "VIS": BandSpec(3, "B03"),
        "SWIR": BandSpec(5, "B05"),
        "MIR": BandSpec(7, "B07"),
        "WV": BandSpec(9, "B09"),
        "TIR1": BandSpec(13, "B13"),
        "TIR2": BandSpec(15, "B15"),
    },
    "meteosat": {
        # SEVIRI's own channel numbering (verified against meteosat-09's
        # real Channels dict) -- NOT the same sequence as GOES/Himawari.
        "VIS": BandSpec(1, "VIS006"),
        "SWIR": BandSpec(3, "IR_016"),
        "MIR": BandSpec(4, "IR_039"),
        "WV": BandSpec(5, "WV_062"),
        "TIR1": BandSpec(9, "IR_108"),
        "TIR2": BandSpec(10, "IR_120"),
    },
    "insat": {
        "VIS": BandSpec(1, "VIS"),
        "SWIR": BandSpec(2, "SWIR"),
        "MIR": BandSpec(3, "MIR"),
        "WV": BandSpec(4, "WV"),
        "TIR1": BandSpec(5, "TIR1"),
        "TIR2": BandSpec(6, "TIR2"),
    },
}


def get_band_spec(satellite_key: str, band: str) -> BandSpec:
    """Look up the satpy load recipe for a satellite's band.

    Args:
        satellite_key: Satellite key, e.g. ``"goes-18"``; the family is
            derived as the part before the first ``"-"``.
        band: Logical band name, one of ``BANDS``.

    Returns:
        The matching :class:`BandSpec`.

    Raises:
        KeyError: If the satellite's family or the band is not
            registered in ``FAMILY_BAND_REGISTRY``.
    """
    family = satellite_key.split("-")[0]
    return FAMILY_BAND_REGISTRY[family][band]


def get_calibration(band: str) -> str:
    """Look up the satpy calibration mode to request for a logical band.

    Args:
        band: Logical band name, one of ``BANDS``.

    Returns:
        The calibration mode string (e.g. ``"reflectance"``,
        ``"radiance"``, ``"brightness_temperature"``).

    Raises:
        KeyError: If `band` is not in ``BAND_CALIBRATION``.
    """
    return BAND_CALIBRATION[band]
