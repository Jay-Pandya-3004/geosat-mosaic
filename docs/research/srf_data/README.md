# SRF (spectral response function) reference data

This directory holds the publicly published filter-response and
reference-spectrum files the SRF-correction tooling
(`backend/scripts/compute_srf_correction.py`,
`backend/scripts/compute_srf_correction_vis_swir.py`,
`backend/src/global_mosaic/satellites/srf_convolution.py`, and their test
suites) convolves against. Only the specific channels this project's 6
bands (VIS/SWIR/MIR/WV/TIR1/TIR2) actually use are included below, not
every channel each source publishes — see "Channel-to-band mapping"
below for which `chNN` maps to which band.

Everything under this directory is optional for running the mosaic
pipeline itself. It's only needed if you want to re-derive or extend the
per-satellite SRF correction coefficients in
`backend/src/global_mosaic/satellites/srf_correction.py`. The tests that
touch these files (`test_srf_convolution.py`, `test_srf_reflectance.py`)
skip automatically if a given file is missing.

## Provenance and redistribution

| Source | Files | Publisher |
|---|---|---|
| GOES-R ABI CWG SRF | `goes-{18,19}_fm3\|fm4/GOES-R_ABI_FM*_SRF_CWG_ch*.txt` | NOAA / GOES-R Calibration Working Group — US government work, public domain |
| NWP SAF RTTOV coefficient SRF | `goes-*_abi_nwpsaf/`, `insat-*/`, `meteosat-*_seviri/rtcoef_*_srf_ch*.txt` | EUMETSAT NWP SAF — freely available for research/operational use from their public archive |
| TSIS-1 Hybrid Solar Reference Spectrum | `vis_swir_reference/tsis1_hsrs_1nm.nc` | LASP, University of Colorado Boulder — public data product |
| USGS Spectral Library (splib07a) | `vis_swir_reference/splib07a_targets/*.txt` | USGS — US government work, public domain |
| AHI Spectral Responsivity | `../../../backend/data/srf/Himawari/AHI-0{8,9}_SpectralResponsivity/*.xlsx` | JMA (Japan Meteorological Agency) — publicly published instrument characterization data |

These are redistributed here unmodified (aside from being trimmed to only
the channels this project uses) for reproducibility of the SRF-correction
derivation. If you're reusing this data outside this project, check each
publisher's own terms.

## Thermal bands (WV/MIR/TIR1/TIR2) — NWP SAF & GOES-R ABI CWG SRF files

EUMETSAT's NWP SAF publishes RTTOV-coefficient-format SRF files for most
operational meteorological imagers; NOAA's GOES-R Calibration Working
Group (CWG) publishes GOES ABI's SRF files directly. The following are
included in this directory:

```
srf_data/
  goes-19_fm4/GOES-R_ABI_FM4_SRF_CWG_ch{2,5,7,9,13,15}.txt
  goes-18_fm3/GOES-R_ABI_FM3_SRF_CWG_ch{2,5,7,9,13,15}.txt
  goes-19_abi_nwpsaf/rtcoef_goes_19_abi_srf_ch{02,05,07,09,13,15}.txt   # NWP SAF's own repackaging, used only to cross-check against the CWG files above
  goes-18_abi_nwpsaf/rtcoef_goes_18_abi_srf_ch{02,05,07,09,13,15}.txt
  insat-3d/rtcoef_insat3_4_imager_srf_ch{01..06}.txt
  insat-3dr/rtcoef_insat3_5_imager_srf_ch{01..06}.txt
  insat-3ds/rtcoef_insat3_6_imager_srf_ch{01..06}.txt
  meteosat-09_msg2_seviri/rtcoef_msg_2_seviri_srf_ch{01,03,04,05,09,10}.txt
  meteosat-10_msg3_seviri/rtcoef_msg_3_seviri_srf_ch{01,03,04,05,09,10}.txt
```

Channel-to-band mapping (which `chNN` is which band) is in
`backend/scripts/compute_srf_correction.py`'s `_WV_SRF_FILES`/
`_MIR_SRF_FILES`/`_TIR1_SRF_FILES`/`_TIR2_SRF_FILES` dicts and
`compute_srf_correction_vis_swir.py`'s `_SRF_FILES` dict — check those for
the exact channel numbers per satellite before downloading, since they
differ by instrument family.

Himawari (AHI) is handled separately: its SRF data ships as an
`SpectralResponsivity_Data.xlsx` workbook per satellite (JMA-published,
not available via a stable download URL), included at
`backend/data/srf/Himawari/AHI-08_SpectralResponsivity/AHI-8_SpectralResponsivity_Data.xlsx`
and `AHI-09_SpectralResponsivity/AHI-9_SpectralResponsivity_Data.xlsx`
(relative to the repo root, not this `docs/research/srf_data/`
directory — see `_HIMAWARI_SRF_XLSX` in
`compute_srf_correction_vis_swir.py`). `_HIMAWARI_BAND_SHEET` maps this
project's VIS/SWIR bands to the workbook's own "Band 3"/"Band 5" sheet
names.

## Reflective bands (VIS/SWIR) — solar spectrum + surface/cloud targets

```
srf_data/vis_swir_reference/
  tsis1_hsrs_1nm.nc                                    # TSIS-1 Hybrid Solar Reference Spectrum (LASP/University of Colorado), resampled to 1nm
  splib07a_targets/
    splib07a_Sand_GrndIsle1_no_oil_ASDFRa_AREF.txt      # USGS Spectral Library (splib07a) -- desert/sand target
    splib07a_H2O-Ice_GDS136_77K_BECKa_AREF.txt          # -- snow/ice target
    splib07a_Aspen_Leaf-A_DW92-2_BECKa_AREF.txt         # -- vegetation target
    splib07a_Seawater_Open_Ocean_SW2_lwch_BECKa_AREF.txt # -- water target
```

The TSIS-1 spectrum is LASP's TSIS-1 HSRS distribution; the four
`splib07a_*` files are from the USGS Spectral Library (splib07a) public
archive.

## Format notes

Parsers for each file format live in `srf_convolution.py`
(`parse_srf_nwpsaf`, `parse_srf_goes_cwg`, `parse_srf_himawari_xlsx`) and
`srf_reflectance.py` (`parse_tsis1_hsrs`, `parse_splib07a_spectrum`) —
check their docstrings if a downloaded file's exact format doesn't match
what's expected here.
