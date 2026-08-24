"""Extracts each satellite's real scan start/end/mid time from an actual
downloaded file.

Built from per-satellite primary-source research and each satellite's
Scan_Timing field in sat_metadata.py: only GOES's mid-time is exactly
recoverable from its filename alone (the s/e fields ARE true scan
start/end). Himawari, Meteosat, and INSAT all have Mid_Time_Source ==
"file_header" -- their filenames only carry a nominal/representative
timestamp, not true start+end, so a real mid-time requires opening the
file itself.

This module always opens the file via its satpy reader (or the project's
custom INSAT reader) rather than re-implementing per-format binary header
parsing, since satpy's own file handlers already read the header
start_time/end_time for every reader this project uses:

- abi_l1b: Scene-level start_time/end_time ARE the true scan start/end
  -- checked against a real GOES-19 file, exactly matching the
  filename's s/e fields.
- ahi_hsd, seviri_l1b_native: Scene-level start_time/end_time are only
  the *nominal* timeline-slot boundary (e.g. :00/:10 for Himawari,
  :00/:15 for Meteosat) -- both readers additionally expose a
  per-dataset `attrs["time_parameters"]` dict with
  `observation_start_time`/`observation_end_time`, which are the true
  (sub-minute-precision) scan times. This module always prefers those
  when present.
- insat3d_img_l1b_h5 (custom, readers/insat3d_img_l1b_h5.py): reads the
  HDF5 root attributes Acquisition_Start_Time/Acquisition_End_Time via
  its start_time/end_time properties (Scene-level).

One correction this discovery makes to earlier research: it had inferred
(circumstantially, not confirmed) that SEVIRI's native-format filename
timestamp was most likely the nominal repeat-cycle *start*. Checking
against a real MSG3 file, the filename timestamp exactly equals
`observation_end_time` to the millisecond -- it's the scan END time, not
start.

Creating a satpy Scene and loading one dataset is still cheap (dask-backed,
lazy) -- this doesn't decode/read the full pixel array, just the header/
metadata needed to populate these time attributes.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from global_mosaic.satellites.reader_registry import get_reader_spec
from global_mosaic.satellites.sat_metadata import Satellites_Objects


@dataclass
class ScanTimes:
    """A satellite file's real scan start/end/mid time, from its own header/metadata.

    Attributes:
        satellite_key: Satellite key the scan times were extracted for.
        start: Real scan start time (UTC).
        end: Real scan end time (UTC).
        mid: Real scan mid-time (UTC), computed as ``start + (end - start) / 2``.
        confidence: This satellite's ``Scan_Timing.Mid_Time_Confidence``
            from sat_metadata.py (e.g. "exact", "approximate",
            "unverified") -- describes how reliable this general
            approach is for this satellite, not a per-call measurement.
    """

    satellite_key: str
    start: dt.datetime
    end: dt.datetime
    mid: dt.datetime
    confidence: str  # sat_metadata's Scan_Timing.Mid_Time_Confidence for this satellite


def _scan_timing_confidence(satellite_key: str) -> str:
    """Look up a satellite's documented mid-time confidence rating.

    Looks up the Mid_Time_Confidence sat_metadata.py recorded for this
    satellite's primary imaging instrument, so callers/QA can tell an
    "exact" mid-time (GOES) apart from an "approximate" or "unverified" one
    (Himawari/Meteosat/INSAT) even though all four are computed the same
    way here (real header start/end, not a filename formula).

    Args:
        satellite_key: Satellite key, e.g. ``"goes-16"``.

    Returns:
        The confidence string from the first instrument in
        ``sat_metadata.py`` that has a ``Scan_Timing`` block, or
        ``"unverified"`` if none do.
    """
    instruments = Satellites_Objects[satellite_key]["Instruments"]
    for instrument in instruments.values():
        scan_timing = instrument.get("Scan_Timing")
        if scan_timing:
            return scan_timing["Mid_Time_Confidence"]
    return "unverified"


def get_scan_times(satellite_key: str, filepaths: str | list[str]) -> ScanTimes:
    """Open a satellite file with its registered satpy reader and extract its real scan times.

    Opens `filepaths` with the satellite's registered satpy reader and
    returns its real start/end/mid scan time.

    `filepaths` is the single product file (GOES, Meteosat, INSAT) or the
    list of segment files satpy needs together to resolve one scene's time
    range (Himawari, which splits a Full Disk observation into 10
    per-band segment files -- see acquisition/himawari.py).

    Args:
        satellite_key: Satellite key, e.g. ``"goes-16"``.
        filepaths: Single file path, or list of segment file paths (for
            Himawari), to open together as one satpy Scene.

    Returns:
        The extracted :class:`ScanTimes`.

    Raises:
        ValueError: If no satpy reader is registered for `satellite_key`,
            satpy doesn't recognize the registered reader name, or the
            reader doesn't expose usable start_time/end_time for these
            files.
    """
    spec = get_reader_spec(satellite_key)
    if spec.reader is None:
        raise ValueError(
            f"No satpy reader registered for {satellite_key!r} in reader_registry.py "
            f"({spec.note or 'no note'}) -- cannot compute a real scan mid-time."
        )

    # Imported lazily: satpy is a heavy optional dependency and this module
    # should still be importable (e.g. for the Scan_Timing lookup helper
    # above) in environments that haven't installed it yet.
    from satpy import Scene

    paths = [filepaths] if isinstance(filepaths, str) else filepaths
    try:
        scn = Scene(filenames=paths, reader=spec.reader)
    except KeyError as exc:
        raise ValueError(
            f"satpy doesn't know reader {spec.reader!r} for {satellite_key!r}. "
            f"If this is one of this project's custom readers, check "
            f"readers/README.md -- it may need SATPY_CONFIG_PATH set or a "
            f"missing .yaml written before satpy can load it."
        ) from exc

    start, end = scn.start_time, scn.end_time

    # Some readers (ahi_hsd, seviri_l1b_native) only expose the
    # *nominal* timeline-slot boundary at the
    # Scene level -- the true scan start/end lives in a per-dataset
    # `time_parameters` dict instead. Load the satellite's registered
    # IR-window band (cheap, dask-backed) and prefer that if present.
    if spec.ir_window_band and spec.ir_window_band in scn.available_dataset_names():
        scn.load([spec.ir_window_band])
        time_params = scn[spec.ir_window_band].attrs.get("time_parameters")
        if time_params:
            start = time_params.get("observation_start_time", start)
            end = time_params.get("observation_end_time", end)

    if start is None or end is None:
        raise ValueError(
            f"{satellite_key}'s {spec.reader} reader did not expose "
            f"start_time/end_time for {paths!r} -- cannot compute a real scan "
            f"mid-time from this file."
        )

    mid = start + (end - start) / 2
    return ScanTimes(
        satellite_key=satellite_key,
        start=start,
        end=end,
        mid=mid,
        confidence=_scan_timing_confidence(satellite_key),
    )
