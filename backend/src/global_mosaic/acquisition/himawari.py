"""Himawari-8/9 AHI acquisition via public AWS Open Data S3 buckets.

Bucket layout (public, anonymous):
  s3://{bucket}/AHI-L1b-FLDK/{year}/{month:02d}/{day:02d}/{HHMM}/
    HS_H0{8|9}_{YYYYMMDD}_{HHMM}_{band}_FLDK_R{res}_S{seg:02d}10.DAT.bz2

Full disk is split into 10 segments per band; satpy's ahi_hsd reader needs
all 10 present in one directory to assemble the full disk.

NOTE: bucket/path structure is based on published AWS Open Data / JMA
documentation, not verified against a live listing from this environment.
Sanity-check the first real fetch against the actual bucket before relying
on it in the pipeline.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from global_mosaic.acquisition._aws_common import get_s3fs, list_prefix

_CADENCE_MINUTES = 10

BUCKETS = {
    "himawari-08": "noaa-himawari8",
    "himawari-09": "noaa-himawari9",
}

BAND = "B13"  # IR window, 10.4 micron

_REAL_MIDTIME_OFFSET_MINUTES = 5.0
"""Himawari's REAL scan mid-time (per the file's own header, see
processing/midtime.py) is NOT its nominal slot label -- a Full Disk scan
takes ~10 minutes to sweep the whole disk, so the real midpoint lands
consistently ~5 minutes AFTER the nominal HHMM label, not AT it. As of
2026-08-09, downloaded files show nominal slot 0440's mid-time at
04:45:00.66 (matches +5min exactly) and nominal slot 0450's at 04:55:00.65
(also matches +5min exactly).

What this constant fixes: find_segment_keys's candidate sort previously
ranked slots by distance from `target` to the NOMINAL label, which is a
different thing from distance to the REAL mid-time that actually
determines whether SEARCH_WINDOW_MINUTES accepts this satellite in
schedule_pipeline.py/run_phase2_tier.py's per-band fetch. For a target
that lands in the back half of a 10-min nominal bucket, the
nominally-nearest slot's REAL mid-time can be ~10 min away even though
its nominal label looked close -- while the slot 10 minutes EARLIER
(nominally farther) has a REAL mid-time much closer to target. For target
2026-08-09T04:47:44, nominal 0450 (nominal gap 2.3 min) has REAL mid-time
gap 7.3 min (rejected by the fetch's own ±5 min tolerance); nominal 0440
(nominal gap 7.7 min) has REAL mid-time gap only 2.7 min (would be
accepted). Without this adjustment, every fixed-slot target whose true
nearest-by-real-time neighbor is the earlier nominal slot gets the wrong
one picked, chronically failing the fetch's tolerance check regardless of
how many times it's retried -- this produced a growing backlog (32
consecutive partial timestamps, all missing Himawari, over ~12 hours)
before the fix.

This is a separate issue from the 2026-08-08 "floored-slot tie-break
bias" fix directly above (see that comment) -- that fix correctly stopped
preferring earlier candidates on nominal-label ties, but this OFFSET
issue is about a genuine, non-tie, systematic bias in what
"nominal-nearest" even means for Himawari specifically. The old buggy
stable-sort tie-break bias (toward earlier slots) happened to often
accidentally compensate for this offset -- which is why this regression
wasn't visible until that unrelated bug was fixed."""


def _round_to_slot(target: dt.datetime) -> dt.datetime:
    """Floor a datetime down to the nearest Himawari 10-minute nominal scan slot.

    Args:
        target: Datetime to floor.

    Returns:
        `target` with minute floored to a multiple of ``_CADENCE_MINUTES``
        and seconds/microseconds zeroed.
    """
    floored_minute = (target.minute // _CADENCE_MINUTES) * _CADENCE_MINUTES
    return target.replace(minute=floored_minute, second=0, microsecond=0)


def find_segment_keys(
    satellite_key: str, target: dt.datetime, tolerance_minutes: int = 20, band: str = BAND
) -> list[str]:
    """Find the 10 full-disk segment keys for `band` at the scan slot nearest `target`.

    `band` defaults to B13 (Phase 0's IR-window channel); Phase 2 passes
    the other 5 shared-set bands (B03/B05/B07/B09/B15, see
    satellites/band_registry.py) -- each AHI channel is a separate set of
    10 segment files.

    Args:
        satellite_key: Himawari satellite key, e.g. ``"himawari-09"``. If
            not one of ``BUCKETS`` (e.g. an older Himawari-slot occupant
            like MTSAT that `coverage.py`'s date/longitude resolution
            doesn't know this project has no reader for), returns ``[]``
            rather than raising, matching the "nothing found" contract
            every other failure mode here already returns.
        target: Desired scan mid-time (UTC).
        tolerance_minutes: Maximum offset from the floored nominal slot
            (in minutes) to search outward in both directions, in
            ``_CADENCE_MINUTES`` steps.
        band: AHI channel identifier, e.g. ``"B13"``.

    Returns:
        Sorted list of the 10 s3fs-style segment keys for the nearest
        complete scan slot (by real, offset-corrected mid-time distance
        to `target`), or ``[]`` if no slot within tolerance has a
        complete set of 10 segments.

    Note:
        Sorting *offsets from the floored slot* by abs(offset) puts -10
        before +10 on every tie (Python's sort is stable, and -10 comes
        first in the input range) -- but the floored slot already threw
        away where `target` actually falls within that 10-min bucket, so
        "-10 before +10" has nothing to do with which slot is closer to
        `target`. On 2026-08-08, target 20:47:44 with the exact 20:40 slot
        missing (a gap in Himawari's own record that hour) returned the
        20:30 slot (17.7 min away) without ever checking 20:50 (only 2.3
        min away). Sorting candidate SLOT TIMES directly by distance to
        `target` (not by offset-from-floor) fixes this: ties only happen
        when target sits exactly on a bucket boundary, where either
        neighbor is equally valid anyway.
    """
    if satellite_key not in BUCKETS:
        return []
    fs = get_s3fs(anonymous=True)
    bucket = BUCKETS[satellite_key]

    # Sorting *offsets from the floored slot* by abs(offset) puts -10
    # before +10 on every tie (Python's sort is stable, and -10 comes
    # first in the input range) -- but the floored slot already threw
    # away where `target` actually falls within that 10-min bucket, so
    # "-10 before +10" has nothing to do with which slot is closer to
    # `target`. On 2026-08-08, target 20:47:44 with the exact 20:40 slot
    # missing (a gap in Himawari's own record that hour) returned the
    # 20:30 slot (17.7 min away) without ever checking 20:50 (only 2.3
    # min away). Sorting candidate SLOT TIMES directly by distance to
    # `target` (not by offset-from-floor) fixes this: ties only happen
    # when target sits exactly on a bucket boundary, where either
    # neighbor is equally valid anyway.
    rounded = _round_to_slot(target)
    real_midtime_offset = dt.timedelta(minutes=_REAL_MIDTIME_OFFSET_MINUTES)
    candidates = sorted(
        (rounded + dt.timedelta(minutes=offset) for offset in range(-tolerance_minutes, tolerance_minutes + 1, _CADENCE_MINUTES)),
        key=lambda probe: abs((probe + real_midtime_offset - target).total_seconds()),
    )
    for probe in candidates:
        hhmm = f"{probe.hour:02d}{probe.minute:02d}"
        prefix = f"AHI-L1b-FLDK/{probe.year}/{probe.month:02d}/{probe.day:02d}/{hhmm}/"
        keys = [k for k in list_prefix(fs, bucket, prefix) if f"_{band}_" in k]
        if len(keys) == 10:
            return sorted(keys)
    return []


def download(satellite_key: str, target: dt.datetime, dest_dir: str, band: str = BAND) -> list[str]:
    """Download all 10 full-disk segments for the nearest scan slot to `target`.

    Args:
        satellite_key: Himawari satellite key, e.g. ``"himawari-09"``.
        target: Desired scan mid-time (UTC).
        dest_dir: Local directory to download segments into.
        band: AHI channel identifier, e.g. ``"B13"``.

    Returns:
        Local paths of the downloaded (or already-cached) segment files,
        in the order returned by :func:`find_segment_keys`. Empty list
        if no complete matching slot was found.
    """
    fs = get_s3fs(anonymous=True)
    keys = find_segment_keys(satellite_key, target, band=band)
    local_paths = []
    for key in keys:
        local_path = f"{dest_dir}/{key.split('/')[-1]}"
        # Skip re-fetching a segment already on disk -- see goes.py's
        # download_abi for the same fix and why: every re-run of the same
        # timestamp was re-downloading all 10 segments unconditionally,
        # unlike meteosat.py/insat.py.
        if not Path(local_path).exists():
            fs.get(key, local_path)
        local_paths.append(local_path)
    return local_paths
