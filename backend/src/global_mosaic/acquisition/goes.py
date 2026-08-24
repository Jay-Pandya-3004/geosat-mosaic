"""GOES acquisition -- two eras, two access patterns.

- goes-16..19 (ABI): public AWS S3 buckets, listed live per request (cheap
  enough at 10-min cadence -- one prefix listing per candidate hour).
- goes-08..15 (legacy imager, GridSat NCEI reprocessed archive): looked up
  from a precomputed index (backend/data/cache/goes_sat_timestaps.json,
  built by scripts/build_goes_legacy_index.py) rather than walking NCEI's
  HTTP directory listing live, which is too slow to do per-request.

Satellite keys match global_mosaic.satellites.sat_metadata (e.g. "goes-16",
"goes-08"), not the old bare "goes16"/"goes08" style.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from global_mosaic.acquisition._aws_common import get_s3fs, list_prefix

_SCAN_START_RE = re.compile(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d_")
_SCAN_END_RE = re.compile(r"_e(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})\d_")

ABI_BUCKETS = {
    "goes-16": "noaa-goes16",
    "goes-17": "noaa-goes17",
    "goes-18": "noaa-goes18",
    "goes-19": "noaa-goes19",
}

LEGACY_KEY_BY_SATELLITE = {
    "goes-08": "goes08", "goes-09": "goes09", "goes-10": "goes10", "goes-11": "goes11",
    "goes-12": "goes12", "goes-13": "goes13", "goes-14": "goes14", "goes-15": "goes15",
}

LEGACY_INDEX_PATH = Path(__file__).resolve().parents[4] / "data" / "cache" / "goes_sat_timestaps.json"


def _parse_timestamp(pattern: re.Pattern, key: str) -> dt.datetime | None:
    """Parse a GOES filename timestamp field into a datetime.

    Args:
        pattern: Compiled regex with 5 capture groups (year, day-of-year,
            hour, minute, second), e.g. ``_SCAN_START_RE`` or
            ``_SCAN_END_RE``.
        key: Filename or S3 key to search.

    Returns:
        The parsed UTC datetime, or None if the pattern does not match.
    """
    m = pattern.search(key)
    if not m:
        return None
    year, day_of_year, hour, minute, second = (int(g) for g in m.groups())
    return dt.datetime(year, 1, 1) + dt.timedelta(
        days=day_of_year - 1, hours=hour, minutes=minute, seconds=second
    )


def _parse_scan_mid(key: str) -> dt.datetime | None:
    """Compute a GOES ABI file's true scan mid-time from its filename.

    GOES filenames' s/e fields are true scan start/end (confirmed
    against a live AWS README), so the real per-file mid-time is exactly
    (s+e)/2 -- no need to open
    the file for this one satellite family, unlike Himawari/Meteosat/
    INSAT (see processing/midtime.py).

    Args:
        key: Filename or S3 key containing the ``_sYYYYDDDHHMMSSS_`` and
            ``_eYYYYDDDHHMMSSS_`` scan start/end fields.

    Returns:
        The scan mid-time as a UTC datetime, or None if either the start
        or end field could not be parsed.

    Note:
        BUG FIX (2026-07-31): this used to compare against scan START only,
        which can pick the wrong neighboring file -- found while re-aligning
        a Meteosat-synced target time: for one real target, the file whose
        scan START was nearest had a true mid-time 521s away, while a
        different file's scan START was further but its true mid-time was
        only 89s away. Comparing by mid-time instead picks the file that's
        actually best-synced, consistent with what build_candidate's
        downstream real mid-time check (processing/midtime.py) will judge it
        against anyway.
    """
    start = _parse_timestamp(_SCAN_START_RE, key)
    end = _parse_timestamp(_SCAN_END_RE, key)
    if start is None or end is None:
        return None
    return start + (end - start) / 2


def find_nearest_abi_file(
    satellite_key: str, target: dt.datetime, tolerance_minutes: int = 15, band: str = "C13"
) -> str | None:
    """Find the ABI full-disk file for `band` nearest to `target`.

    `band` defaults to C13 (Phase 0's IR-window channel); Phase 2 passes
    the other 5 shared-set bands (C02/C05/C07/C09/C15, see
    satellites/band_registry.py) -- each ABI channel is its own separate
    product file (unlike Meteosat/INSAT, which bundle every channel into
    one file per scan), so acquisition genuinely needs a band parameter,
    not just the reader/calibration layer above it.

    Searches the target hour plus the adjacent hours before/after to
    cover files whose scan mid-time crosses an hour boundary.

    Args:
        satellite_key: GOES ABI satellite key, e.g. ``"goes-16"``. Must
            be a key of ``ABI_BUCKETS``.
        target: Desired scan mid-time (UTC).
        tolerance_minutes: Maximum allowed distance between `target` and
            the nearest candidate's true scan mid-time, in minutes.
        band: ABI channel identifier, e.g. ``"C13"``.

    Returns:
        The s3fs-style path (``"bucket/key"``) of the nearest matching
        ABI full-disk file, or None if no candidate exists within
        `tolerance_minutes`.
    """
    bucket = ABI_BUCKETS[satellite_key]
    fs = get_s3fs(anonymous=True)

    candidates: list[str] = []
    for hour_offset in (0, -1, 1):
        probe = target + dt.timedelta(hours=hour_offset)
        prefix = f"ABI-L1b-RadF/{probe.year}/{probe.timetuple().tm_yday:03d}/{probe.hour:02d}/"
        candidates.extend(list_prefix(fs, bucket, prefix))

    band_matches = [k for k in candidates if f"{band}_" in k]
    if not band_matches:
        return None

    def time_delta(key: str) -> float:
        mid_time = _parse_scan_mid(key)
        return abs((mid_time - target).total_seconds()) if mid_time else float("inf")

    best = min(band_matches, key=time_delta)
    if time_delta(best) > tolerance_minutes * 60:
        return None
    return best


def download_abi(satellite_key: str, target: dt.datetime, dest_dir: str, band: str = "C13") -> str | None:
    """Download the nearest ABI full-disk file for `band`, skipping cached files.

    Args:
        satellite_key: GOES ABI satellite key, e.g. ``"goes-16"``.
        target: Desired scan mid-time (UTC).
        dest_dir: Local directory to download the file into.
        band: ABI channel identifier, e.g. ``"C13"``.

    Returns:
        The local path to the downloaded (or already-cached) file, or
        None if no matching file was found within tolerance.
    """
    fs = get_s3fs(anonymous=True)
    key = find_nearest_abi_file(satellite_key, target, band=band)
    if key is None:
        return None
    local_path = f"{dest_dir}/{key.split('/')[-1]}"
    # Skip re-fetching a file already on disk -- confirmed
    # this was unconditionally re-downloading every time (real GOES full-
    # disk files are 20-400+ MB depending on band), unlike meteosat.py/
    # insat.py's download(), which already skip existing files. Doesn't
    # help a genuine first-ever run, but every re-run/retry of the same
    # timestamp (very common -- this project reruns the same target time
    # while iterating) was paying full network cost for nothing.
    if not Path(local_path).exists():
        fs.get(key, local_path)
    return local_path


def _load_legacy_index() -> dict:
    """Load the precomputed GridSat-GOES legacy timestamp index from disk.

    Returns:
        The parsed JSON index (``{legacy_key: {iso_timestamp: entry}}``)
        from ``LEGACY_INDEX_PATH``.
    """
    with open(LEGACY_INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)


def find_nearest_legacy_entry(
    satellite_key: str, target: dt.datetime, tolerance_minutes: int = 45
) -> dict | None:
    """Find the nearest GridSat-GOES archive entry (goes-08..15) to `target`.

    Args:
        satellite_key: Legacy GOES satellite key, e.g. ``"goes-08"``. Must
            be a key of ``LEGACY_KEY_BY_SATELLITE``.
        target: Desired scan time (UTC).
        tolerance_minutes: Maximum allowed distance between `target` and
            the nearest indexed timestamp, in minutes.

    Returns:
        The index entry dict (containing at least ``file_name`` and
        ``download_url``) for the nearest match, or None if the
        satellite has no indexed timestamps or none fall within
        `tolerance_minutes`.
    """
    index = _load_legacy_index()
    legacy_key = LEGACY_KEY_BY_SATELLITE[satellite_key]
    timestamps = index.get(legacy_key, {})
    if not timestamps:
        return None

    def offset(iso: str) -> float:
        return abs((dt.datetime.fromisoformat(iso) - target).total_seconds())

    best_iso = min(timestamps, key=offset)
    if offset(best_iso) > tolerance_minutes * 60:
        return None
    return timestamps[best_iso]


def download_legacy(satellite_key: str, target: dt.datetime, dest_dir: str) -> str | None:
    """Download the nearest GridSat-GOES legacy archive file to `target`.

    Args:
        satellite_key: Legacy GOES satellite key, e.g. ``"goes-08"``.
        target: Desired scan time (UTC).
        dest_dir: Local directory to download the file into.

    Returns:
        The local path to the downloaded file, or None if no indexed
        entry was found within tolerance.

    Raises:
        requests.HTTPError: If the download request returns an error
            status.
    """
    import requests

    entry = find_nearest_legacy_entry(satellite_key, target)
    if entry is None:
        return None
    local_path = f"{dest_dir}/{entry['file_name']}"
    response = requests.get(entry["download_url"], timeout=60)
    response.raise_for_status()
    Path(local_path).write_bytes(response.content)
    return local_path


def download(satellite_key: str, target: dt.datetime, dest_dir: str, band: str = "C13") -> str | None:
    """Download the nearest GOES file to `target`, dispatching by satellite era.

    Dispatches to the ABI or legacy path based on satellite_key. `band`
    only matters for the ABI path -- GridSat's reprocessed legacy archive
    bundles multiple channels into one file per timestamp already, same
    as Meteosat/INSAT, so there's nothing band-specific to select there.

    Args:
        satellite_key: GOES satellite key (ABI: ``"goes-16"``..``"goes-19"``,
            legacy: ``"goes-08"``..``"goes-15"``).
        target: Desired scan time (UTC).
        dest_dir: Local directory to download the file into.
        band: ABI channel identifier; ignored for legacy satellites.

    Returns:
        The local path to the downloaded file, or None if no matching
        file was found.

    Raises:
        ValueError: If `satellite_key` is not a recognized GOES satellite.
    """
    if satellite_key in ABI_BUCKETS:
        return download_abi(satellite_key, target, dest_dir, band=band)
    if satellite_key in LEGACY_KEY_BY_SATELLITE:
        return download_legacy(satellite_key, target, dest_dir)
    raise ValueError(f"Unknown GOES satellite key: {satellite_key}")
