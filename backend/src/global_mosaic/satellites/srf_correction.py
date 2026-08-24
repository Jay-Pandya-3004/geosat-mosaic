"""Cross-satellite spectral-response-function (SRF) radiometric correction
coefficients, per band and satellite.

Mosaicking different satellites' "same" band together (see select.py/
blend.py) silently juxtaposes sensors whose actual spectral response
differs -- this module holds the linear (gain, offset) correction that maps
one satellite's raw calibrated value onto a common reference satellite's
scale for a given band. Coefficients are computed offline and checked in
here as static data, not recomputed per mosaic run.

`value_corrected = value_raw * gain + offset`, applied to each Candidate's
data *before* select_winners/select_winners_streaming ever sees it -- this
must happen pre-selection/pre-blend, not as a post-hoc fix to the final
mosaic value. This module has no opinion on where in the pipeline it's
called from; it only holds the coefficients.

Absent (satellite_key, band) entries default to identity via
get_srf_correction, not a KeyError -- a satellite/band pair with no
correction computed yet is always safe to run, which supports incremental
rollout: WV first, other bands land independently, no band blocks another.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SRFCorrection:
    """A linear cross-satellite SRF radiometric correction for one (satellite, band) pair.

    Applied as ``value_corrected = value_raw * gain + offset``. The
    default (all-default-field) instance is the identity correction,
    used for a (satellite_key, band) pair with no computed coefficients
    yet, or for the reference satellite's own entry.

    Attributes:
        gain: Multiplicative correction factor (currently always 1.0 for
            every computed entry in this module).
        offset: Additive correction, in the band's native physical units
            (reflectance fraction for VIS/SWIR, Kelvin for thermal bands).
    """

    gain: float = 1.0
    offset: float = 0.0
    reference_satellite: str = ""
    """Which satellite this was corrected TO match -- empty string for the
    identity default (nothing to match, no correction applied) or for the
    reference satellite's own entry (the anchor satellite, goes-19, every band)."""
    method: str = "identity"
    """"identity" (no correction, the default), "srf-weighted-radiative-
    transfer" (the primary method), or "empirical-overlap-bias"
    (validation-only, not expected as a stored method value for any
    real entry)."""
    computed_date: str = ""
    """ISO date the coefficient was derived, not when a mosaic ran."""
    srf_source_version: str = ""
    """e.g. "NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04"."""
    reference_spectrum_source: str = ""
    """e.g. "NWP SAF 91L/83-profile diverse dataset" or "TSIS-1 HSRS +
    reflectance-target ensemble"."""
    reference_ensemble_spread: float | None = None
    """Std-dev (same units as `offset`) of the per-ensemble-member bias --
    thermal bands' 83 atmospheric profiles or reflective bands' set of
    reflectance targets. Not applied at correction time; a disclosed
    diagnostic only. None for the identity default or any method that isn't
    ensemble-based."""


_WV_SRF_SOURCE = "NWP SAF ECMWF_83P_91L.dat, 80 real profiles (81/82/83 synthetic envelope/mean excluded)"
_WV_COMPUTED_DATE = "2026-08-05"

_VIS_SWIR_SRF_SOURCE = "TSIS-1 HSRS v2 + 6-target reflectance ensemble"
_VIS_SWIR_COMPUTED_DATE = "2026-08-05"

_MIR_TIR_SRF_SOURCE = _WV_SRF_SOURCE
_MIR_TIR_COMPUTED_DATE = "2026-08-05"

SRF_CORRECTIONS: dict[tuple[str, str], SRFCorrection] = {
    # (satellite_key, band) -> SRFCorrection. Populated band by band as each
    # gets its RT-convolution coefficients computed.
    #
    # WV, computed via scripts/compute_srf_correction.py against goes-19
    # (the anchor satellite for every band) using the 80-profile NWP SAF
    # ensemble -- not the 6-AFGL interim set. Known, documented,
    # non-blocking caveat (see this script's own docstrings for the
    # reasoning): a ~1.3% cumulative altitude-integration imprecision in
    # the profile converter, which largely cancels out in this differential
    # measurement. GOES-18/19 SRF dual-source reconciliation shows the two
    # sources are bit-for-bit identical, not independent data. An
    # empirical-overlap-bias spot-check against INSAT-3DS/Himawari-9 data
    # caught a sign bug in this script's bias formula on 2026-08-05: every
    # offset below had been stored negated from what `corrected = raw*gain +
    # offset` needs. Fixed, all offsets in this file corrected -- see the
    # spot-check script for the full writeup. The magnitudes were never in
    # question, only the sign.
    ("insat-3d", "WV"): SRFCorrection(
        gain=1.0, offset=1.6586,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_WV_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_WV_SRF_SOURCE, reference_ensemble_spread=0.4669,
    ),
    ("insat-3dr", "WV"): SRFCorrection(
        gain=1.0, offset=1.8395,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_WV_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_WV_SRF_SOURCE, reference_ensemble_spread=0.5239,
    ),
    ("insat-3ds", "WV"): SRFCorrection(
        gain=1.0, offset=3.3600,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_WV_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_WV_SRF_SOURCE, reference_ensemble_spread=0.9444,
    ),
    ("himawari-08", "WV"): SRFCorrection(
        gain=1.0, offset=0.1672,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_WV_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_WV_SRF_SOURCE, reference_ensemble_spread=0.0510,
    ),
    ("himawari-09", "WV"): SRFCorrection(
        gain=1.0, offset=-0.1253,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_WV_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_WV_SRF_SOURCE, reference_ensemble_spread=0.0375,
    ),
    ("meteosat-09", "WV"): SRFCorrection(
        gain=1.0, offset=7.6471,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_WV_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_WV_SRF_SOURCE, reference_ensemble_spread=2.3728,
    ),
    ("meteosat-10", "WV"): SRFCorrection(
        gain=1.0, offset=7.5323,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_WV_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_WV_SRF_SOURCE, reference_ensemble_spread=2.3284,
    ),
    # goes-19 itself is the WV anchor -- identity by definition, not
    # listed explicitly; get_srf_correction's default already returns
    # SRFCorrection() (gain=1.0, offset=0.0, method="identity") for it.

    # VIS/SWIR, computed via scripts/compute_srf_correction_vis_swir.py
    # against goes-19 (the anchor satellite for every band) using the
    # TSIS-1 HSRS solar spectrum + 6-target reflectance ensemble (4
    # analytic surface targets -- desert/PICS-Libya-4 stand-in, snow/ice,
    # vegetation, water -- plus 2 uvspec solar-mode RT runs for liquid/ice
    # cloud). Same non-blocking caveat as WV applies (no empirical-overlap-
    # bias spot-check performed for these two bands specifically;
    # GOES-18/19 dual-source reconciliation, see the WV comment block
    # above). This script shared WV's sign bug (fixed 2026-08-05, see the
    # WV comment block above) -- the offsets below are already the
    # corrected values. Additional VIS/SWIR-specific caveat: the "desert"
    # target is a generic USGS splib07a sand sample, not PICS Libya-4's own
    # characterized reflectance (which wasn't obtainable); "snow_ice" and
    # "water" are single samples, not the full USGS library's variety.
    ("insat-3d", "VIS"): SRFCorrection(
        gain=1.0, offset=-0.01214,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.02761,
    ),
    ("insat-3dr", "VIS"): SRFCorrection(
        gain=1.0, offset=-0.01139,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.02588,
    ),
    ("insat-3ds", "VIS"): SRFCorrection(
        gain=1.0, offset=-0.01026,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.02325,
    ),
    ("himawari-08", "VIS"): SRFCorrection(
        gain=1.0, offset=0.00001,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00001,
    ),
    ("himawari-09", "VIS"): SRFCorrection(
        gain=1.0, offset=0.00004,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00006,
    ),
    ("meteosat-09", "VIS"): SRFCorrection(
        gain=1.0, offset=0.00008,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00016,
    ),
    ("meteosat-10", "VIS"): SRFCorrection(
        gain=1.0, offset=0.00000,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00007,
    ),
    ("insat-3d", "SWIR"): SRFCorrection(
        gain=1.0, offset=0.00341,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00354,
    ),
    ("insat-3dr", "SWIR"): SRFCorrection(
        gain=1.0, offset=-0.00004,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00132,
    ),
    ("insat-3ds", "SWIR"): SRFCorrection(
        gain=1.0, offset=0.00266,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00269,
    ),
    ("himawari-08", "SWIR"): SRFCorrection(
        gain=1.0, offset=-0.00060,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00063,
    ),
    ("himawari-09", "SWIR"): SRFCorrection(
        gain=1.0, offset=0.00068,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00071,
    ),
    ("meteosat-09", "SWIR"): SRFCorrection(
        gain=1.0, offset=-0.00542,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00562,
    ),
    ("meteosat-10", "SWIR"): SRFCorrection(
        gain=1.0, offset=-0.00537,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00557,
    ),

    # MIR/TIR1/TIR2, computed via scripts/compute_srf_correction.py (now
    # generalized beyond WV, per that script's docstring) against goes-19
    # (the anchor satellite for every band) using the same 80-profile NWP
    # SAF ensemble as WV. Channel numbers per band_registry.py's
    # FAMILY_BAND_REGISTRY: GOES/Himawari share ch7/13/15 for MIR/TIR1/
    # TIR2, Meteosat SEVIRI ch4/9/10, INSAT ch3/5/6 -- checked against
    # each satellite's SRF file's wavenumber coverage before running, not
    # assumed from the channel numbers alone. Same non-blocking caveats as
    # WV apply (the ~1.3% altitude-integration imprecision, no empirical-
    # overlap-bias spot-check for these bands specifically; GOES-18/19
    # dual-source reconciliation, see the WV comment block above). Computed
    # via the same script as WV, so shared its sign bug (fixed 2026-08-05,
    # see the WV comment block above) -- the offsets below are already the
    # corrected values.
    ("insat-3d", "MIR"): SRFCorrection(
        gain=1.0, offset=0.0859,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.0740,
    ),
    ("insat-3dr", "MIR"): SRFCorrection(
        gain=1.0, offset=0.0423,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.0225,
    ),
    ("insat-3ds", "MIR"): SRFCorrection(
        gain=1.0, offset=0.0304,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.0159,
    ),
    ("himawari-08", "MIR"): SRFCorrection(
        gain=1.0, offset=0.0031,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.0793,
    ),
    ("himawari-09", "MIR"): SRFCorrection(
        gain=1.0, offset=-0.0008,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.3132,
    ),
    ("meteosat-09", "MIR"): SRFCorrection(
        gain=1.0, offset=0.8546,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=1.1465,
    ),
    ("meteosat-10", "MIR"): SRFCorrection(
        gain=1.0, offset=1.0036,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=1.2063,
    ),
    ("insat-3d", "TIR1"): SRFCorrection(
        gain=1.0, offset=-0.1858,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.3732,
    ),
    ("insat-3dr", "TIR1"): SRFCorrection(
        gain=1.0, offset=-0.1931,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.3538,
    ),
    ("insat-3ds", "TIR1"): SRFCorrection(
        gain=1.0, offset=-0.2059,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.3324,
    ),
    ("himawari-08", "TIR1"): SRFCorrection(
        gain=1.0, offset=-0.1685,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.0765,
    ),
    ("himawari-09", "TIR1"): SRFCorrection(
        gain=1.0, offset=-0.1635,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.0751,
    ),
    ("meteosat-09", "TIR1"): SRFCorrection(
        gain=1.0, offset=-0.1745,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.3296,
    ),
    ("meteosat-10", "TIR1"): SRFCorrection(
        gain=1.0, offset=-0.1744,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.3462,
    ),
    ("insat-3d", "TIR2"): SRFCorrection(
        gain=1.0, offset=-0.8197,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.5831,
    ),
    ("insat-3dr", "TIR2"): SRFCorrection(
        gain=1.0, offset=-0.7860,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.5597,
    ),
    ("insat-3ds", "TIR2"): SRFCorrection(
        gain=1.0, offset=-0.8101,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.5793,
    ),
    ("himawari-08", "TIR2"): SRFCorrection(
        gain=1.0, offset=0.3037,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.1986,
    ),
    ("himawari-09", "TIR2"): SRFCorrection(
        gain=1.0, offset=0.2660,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.1759,
    ),
    ("meteosat-09", "TIR2"): SRFCorrection(
        gain=1.0, offset=-0.7875,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.5636,
    ),
    ("meteosat-10", "TIR2"): SRFCorrection(
        gain=1.0, offset=-0.8728,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.6228,
    ),

    # goes-18, all 6 bands, computed via both driver scripts (WV/MIR/TIR1/
    # TIR2 via compute_srf_correction.py, VIS/SWIR via
    # compute_srf_correction_vis_swir.py) against goes-19. Closes a gap:
    # unlike the INSAT generations (which share one satellite_id since only
    # one is ever a live candidate at a time, see select.py's SATELLITE_ID
    # comment), goes-18 gets its own distinct satellite_id and is a live,
    # independently-selectable mosaic candidate just like himawari-08 or
    # meteosat-10 -- both of which already had corrections, while goes-18
    # had none until now. Computed with the already-fixed sign convention
    # (the 2026-08-05 sign-bug fix), so these offsets did not need the
    # negation the other 42 entries did. Every offset here is much smaller
    # than the corresponding cross-family entries above (e.g. WV: 0.07K vs
    # INSAT's 1.7-3.4K, Meteosat's 7.5-7.6K) -- expected, since GOES-18 and
    # GOES-19 are the same ABI instrument design (FM3 vs FM4), not a
    # different sensor family.
    ("goes-18", "WV"): SRFCorrection(
        gain=1.0, offset=0.0713,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_WV_SRF_SOURCE, reference_ensemble_spread=0.0229,
    ),
    ("goes-18", "MIR"): SRFCorrection(
        gain=1.0, offset=0.0215,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.0046,
    ),
    ("goes-18", "TIR1"): SRFCorrection(
        gain=1.0, offset=-0.0360,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.0143,
    ),
    ("goes-18", "TIR2"): SRFCorrection(
        gain=1.0, offset=0.0178,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_MIR_TIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_MIR_TIR_SRF_SOURCE, reference_ensemble_spread=0.0115,
    ),
    ("goes-18", "VIS"): SRFCorrection(
        gain=1.0, offset=-0.00002,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00004,
    ),
    ("goes-18", "SWIR"): SRFCorrection(
        gain=1.0, offset=0.00008,
        reference_satellite="goes-19", method="srf-weighted-radiative-transfer",
        computed_date=_VIS_SWIR_COMPUTED_DATE, srf_source_version="NWP SAF / GOES-R ABI CWG / AHI SRF filter-response files, retrieved 2026-08-04",
        reference_spectrum_source=_VIS_SWIR_SRF_SOURCE, reference_ensemble_spread=0.00009,
    ),
}


def get_srf_correction(satellite_key: str, band: str) -> SRFCorrection:
    """Look up the SRF correction for a satellite/band pair.

    Args:
        satellite_key: Satellite key, e.g. ``"insat-3ds"``.
        band: Logical band name, e.g. ``"WV"``.

    Returns:
        The stored :class:`SRFCorrection` for `(satellite_key, band)`,
        or the identity default (gain=1.0, offset=0.0, method="identity")
        if no correction has been computed for this pair yet.
    """
    return SRF_CORRECTIONS.get((satellite_key, band), SRFCorrection())
