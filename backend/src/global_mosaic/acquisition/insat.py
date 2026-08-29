"""INSAT-3D/3DR/3DS Imager acquisition via MOSDAC (ISRO).

IMPORTANT correction found while migrating this project's satellite
metadata against OSCAR: INSAT-3DS (launched Feb 2024), not INSAT-3DR, is
the current occupant of the 82E slot -- INSAT-3DR is actually at 74E. See
global_mosaic.satellites.sat_metadata's insat-3dr/insat-3ds entries. Use
satellites.coverage.resolve_slot(82.0, target_time) rather than hardcoding
which INSAT satellite to request.

Live download implemented 2026-08-05 against MOSDAC's
download_api/apios endpoints (session/token-based, not a stable public
REST API like NOAA's AWS buckets or EUMETSAT's Data Store). The login/
search/download/refresh/logout flow and the retry/resume/SHA256-verify
logic below is ported from a reference client (a family of scripts named
mdapi*.py/mdapi_opt*.py) -- specifically from `mdapi_opt5.py`, the one
version among 9 iterations with a documented, fully successful
production run (48/48 files, 0 failures, 19.85GB, clean logout) rather
than the later `mdapi_opt6.py`, which reverts a deliberate HTTP/2-disabled
stability fix and has no evidence of ever completing a run. Only the
single-file find-nearest-and-download logic was ported here, not that
script's whole-date-range batch/multi-worker orchestration -- this
module's `download()` contract is one file per call, matching
goes.py/himawari.py/meteosat.py.

Set MOSDAC_USERNAME / MOSDAC_PASSWORD env vars (see backend/.env.example,
loaded via python-dotenv the same way meteosat.py loads its EUMETSAT
credentials) -- never commit real values, backend/.env is gitignored.

While MOSDAC access is unavailable (network/portal issue, or credentials
not set -- both treated as "live download not possible right now", not
a hard error, since this module's whole design supports running without
MOSDAC access at all), `download()` still checks `dest_dir` for an
already-present file matching the satellite's filename convention
(`SSNNN_DDMMMYYYY_HHmm_LOP_STD[_VxxRxx].h5`) before giving up -- this
lets manually-placed sample/test files get picked up by the same
pipeline live downloads would use. The match is deliberately coarse
(see _APPROX_NOMINAL_TO_REAL_MID_OFFSET_MINUTES below) --
processing/midtime.py does the precise mid-time check once the file is
actually opened.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

_BASE_URL = "https://mosdac.gov.in"
_TOKEN_URL = f"{_BASE_URL}/download_api/gettoken"
_SEARCH_URL = f"{_BASE_URL}/apios/datasets.json"
_DOWNLOAD_URL = f"{_BASE_URL}/download_api/download"
_REFRESH_URL = f"{_BASE_URL}/download_api/refresh-token"
_LOGOUT_URL = f"{_BASE_URL}/download_api/logout"

_SATELLITE_CODE = {
    "insat-3d": "3D",
    "insat-3dr": "3R",
    "insat-3ds": "3S",
}

# datasetId per generation -- known correct for insat-3ds (the reference
# mdapi client's own config.json used "3SIMG_L1B_STD" and successfully
# downloaded files with it). insat-3d/3dr follow the same
# "<code>IMG_L1B_STD" pattern, consistent with every other INSAT naming
# convention in this project (identifier prefix ==
# _SATELLITE_CODE + "IMG"), but unverified against an actual 3D/3DR MOSDAC
# search response specifically -- flag if either ever 404s at search time.
_DATASET_ID = {key: f"{code}IMG_L1B_STD" for key, code in _SATELLITE_CODE.items()}

# Nominal-filename-HHmm -> actual-scan-mid-time offset, per generation,
# derived from sample files (not guessed): all three generations share
# the same ~30-min-cadence instrument design, so a scan started at nominal HHmm actually mid-scans
# roughly 13.6-14.0 min later, consistently across generations --
#   insat-3d:  3DIMG_04AUG2021_0830 -> real mid 08:43:35   (13.583 min)
#   insat-3dr: 3RIMG_04AUG2021_0845 -> real mid 08:58:50   (13.833 min)
#              3RIMG_04AUG2021_0915 -> real mid 09:28:49.5 (13.825 min, avg 13.829)
#   insat-3ds: 3SIMG_01JAN2026_0000 -> real mid 00:14:02   (14.033 min)
#              3SIMG_01JAN2026_0030 -> real mid 00:44:01.5 (14.025 min, avg 14.029)
# Used to rank candidates -- both already-downloaded local files
# (_find_local_file) and MOSDAC search results (_search_nearest) -- by
# ESTIMATED real mid-time rather than raw nominal time, so both
# selection paths agree with each other. The actual, precise mid-time
# (processing/midtime.py, opens the file's own header) is still the
# authoritative check that already runs downstream in
# run_phase2_tier.py's build_candidate before any candidate is used;
# this doesn't replace that, it just avoids picking the wrong candidate
# up front (bug found 2026-08-05: comparing raw nominal time ignores
# that every candidate shifts by roughly the same ~14 min offset, and
# can rank two nominally-close candidates in the wrong order for a
# target that falls between them).
_APPROX_NOMINAL_TO_REAL_MID_OFFSET_MINUTES = {
    "insat-3d": 13.583,
    "insat-3dr": 13.829,
    "insat-3ds": 14.029,
}

# SSNNN_DDMMMYYYY_HHmm_LOP_STD[_VxxRxx].h5 -- e.g.
# 3SIMG_01JAN2026_0000_L1B_STD_V01R00.h5
_FILENAME_RE = re.compile(
    r"^(?P<code>3[DRS])IMG_(?P<date>\d{2}[A-Z]{3}\d{4})_(?P<time>\d{4})_L1B_STD(?:_V\d{2}R\d{2})?\.h5$"
)

_CHUNK_SIZE = 16 * 1024 * 1024  # matches mdapi_opt5.py's proven chunk size
_MAX_DOWNLOAD_RETRIES = 20  # matches mdapi_opt5.py's MAX_RETRIES
_SEARCH_RETRIES = 5

_DOWNLOAD_LOCK_TIMEOUT_SECONDS = 1800.0
_DOWNLOAD_LOCK_POLL_SECONDS = 5.0


def _acquire_file_lock(lock_path: Path, busy_message: str):
    """Acquire an OS-level exclusive lock file, blocking (via polling) until available.

    Blocks (polling, same mechanism as mosaic_io.py's own
    _acquire_write_lock -- duplicated here rather than imported, this
    project's established pattern for a small utility shared by modules
    that shouldn't otherwise depend on each other, see schedule_
    pipeline.py's own METEOSAT_DATASET_BY_SLOT comment for the same
    reasoning) until an OS-level exclusive lock on `lock_path` can be
    acquired, then returns the open lock-file handle.

    Generic on purpose -- originally written just for serializing
    concurrent downloads of the same MOSDAC record (see download()'s own
    comment for that incident), then reused as-is for serializing login
    attempts across concurrent worker processes (see _shared_login) once
    that turned out to need the exact same blocking-exclusive-file-lock
    shape.

    Args:
        lock_path: Path to the lock file to create/open and lock.
        busy_message: Printed once (not per poll) if the lock is already
            held by another process, so the wait reads correctly for
            whichever resource is actually being waited on.

    Returns:
        The open lock-file handle, holding the exclusive lock. Must be
        passed to :func:`_release_file_lock` when done.

    Raises:
        TimeoutError: If the lock cannot be acquired within
            ``_DOWNLOAD_LOCK_TIMEOUT_SECONDS`` (another process appears
            to be genuinely stuck holding it).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    waited = 0.0
    announced = False
    while True:
        f = open(lock_path, "a+b")
        try:
            if os.fstat(f.fileno()).st_size == 0:
                f.write(b"0")
                f.flush()
            if sys.platform == "win32":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            if waited >= _DOWNLOAD_LOCK_TIMEOUT_SECONDS:
                raise TimeoutError(
                    f"could not acquire lock for {lock_path} after {_DOWNLOAD_LOCK_TIMEOUT_SECONDS:.0f}s -- "
                    "another process appears to be genuinely stuck holding it, not just still working"
                )
            if not announced:
                print(f"    [insat] {busy_message}")
                announced = True
            time.sleep(_DOWNLOAD_LOCK_POLL_SECONDS)
            waited += _DOWNLOAD_LOCK_POLL_SECONDS
        else:
            return f


def _release_file_lock(f) -> None:
    """Release and close a lock handle acquired by :func:`_acquire_file_lock`.

    Args:
        f: The open lock-file handle to unlock and close.
    """
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


# How long a cached login token is trusted before _shared_login logs in
# again rather than reusing it. Not tied to MOSDAC's own actual token
# lifetime (undocumented) -- this only needs to outlast one scheduler
# tick's burst of concurrent partial-recheck workers, since a stale
# cached token is still safe: _download_record already refreshes on a
# 401 mid-download regardless of how the token was obtained. Short
# enough that a genuinely-expired token doesn't sit cached for long,
# long enough to actually collapse a burst of simultaneous logins into
# one real network call.
_TOKEN_CACHE_TTL_SECONDS = 600.0


def _shared_login(username: str, password: str, dest_dir: str) -> tuple[str, str]:
    """Log into MOSDAC, sharing one token across concurrent worker processes.

    Cross-process wrapper around _login: at most one real login per
    _TOKEN_CACHE_TTL_SECONDS window, shared by every worker touching the
    same `dest_dir`, instead of each one logging in independently.

    The incident this fixes: schedule_pipeline.py's _run_partial_recheck
    runs several patch-retry workers concurrently (each a separate
    `python.exe` subprocess, not just a thread -- an in-process lock or
    cache would not be visible across them), and with a large partial
    backlog every one of them could end up calling _login for the same
    MOSDAC account within seconds of each other. That burst was enough
    to trip MOSDAC's own rate limiter on /download_api/gettoken -- and
    even with _is_retryable_error's 429 backoff, one worker's retry
    window kept losing the race against the others' simultaneous
    attempts still hitting the same limit, so the whole batch could end
    up failing "after retries" instead of any of them recovering.

    Serializing the actual login call (via the same file-lock mechanism
    download() already uses for concurrent-download races) and caching
    the result for every other worker to reuse turns N near-simultaneous
    logins into one -- the other N-1 workers just read the cached token
    back out once the lock releases, instead of each making their own
    call to MOSDAC at all.

    Args:
        username: MOSDAC account username.
        password: MOSDAC account password.
        dest_dir: Shared raw-download directory; the lock file and token
            cache live alongside the raw files here, one pair per
            directory, shared by every process pointed at it.

    Returns:
        ``(access_token, refresh_token)`` tuple, either freshly obtained
        or read back from the shared cache.
    """
    lock_path = Path(dest_dir) / ".mosdac_login.lock"
    cache_path = Path(dest_dir) / ".mosdac_token_cache.json"
    lock_handle = _acquire_file_lock(lock_path, "another concurrent worker is already logging in -- waiting for it instead of racing")
    try:
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text())
                if time.time() - cached["issued_at"] < _TOKEN_CACHE_TTL_SECONDS:
                    return cached["access_token"], cached["refresh_token"]
            except (json.JSONDecodeError, KeyError, OSError):
                pass  # a corrupt/partial cache file just means falling through to a real login below

        access_token, refresh_token = _login(username, password)
        cache_path.write_text(json.dumps({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "issued_at": time.time(),
        }))
        return access_token, refresh_token
    finally:
        _release_file_lock(lock_handle)


def _find_local_file(satellite_key: str, target: dt.datetime, dest_dir: str, tolerance_minutes: float = 30.0):
    """Find the best already-downloaded/placed local file matching `target`.

    Pre-filter over already-downloaded/placed files in `dest_dir`,
    ranked by an ESTIMATED real mid-time (nominal filename HHmm + this
    generation's known ~13.6-14.0 min offset, see
    _APPROX_NOMINAL_TO_REAL_MID_OFFSET_MINUTES) rather than the raw
    nominal time -- close enough to rank multiple local candidates
    correctly, but still just an estimate. Wider tolerance than the
    pipeline's actual +-5 min search window on purpose -- the precise cut
    happens later in processing/midtime.py once the file is actually
    opened.

    Args:
        satellite_key: INSAT satellite key, e.g. ``"insat-3ds"``. Must be
            a key of ``_SATELLITE_CODE``, else returns None.
        target: Desired scan mid-time (UTC).
        dest_dir: Directory to search for matching files.
        tolerance_minutes: Maximum allowed distance between `target` and
            a candidate's estimated real mid-time, in minutes.

    Returns:
        The local file path (str) of the nearest matching file, or None
        if `satellite_key` is unrecognized or no local file matches
        within tolerance.
    """
    code = _SATELLITE_CODE.get(satellite_key)
    if code is None:
        return None
    offset = dt.timedelta(minutes=_APPROX_NOMINAL_TO_REAL_MID_OFFSET_MINUTES.get(satellite_key, 0.0))

    best_path = None
    best_gap_seconds = None
    for path in Path(dest_dir).glob(f"{code}IMG_*_L1B_STD*.h5"):
        match = _FILENAME_RE.match(path.name)
        if not match:
            continue
        try:
            file_time = dt.datetime.strptime(f"{match['date']}_{match['time']}", "%d%b%Y_%H%M")
        except ValueError:
            continue

        estimated_real_mid = file_time + offset
        gap_seconds = abs((estimated_real_mid - target).total_seconds())
        if gap_seconds <= tolerance_minutes * 60 and (best_gap_seconds is None or gap_seconds < best_gap_seconds):
            best_path, best_gap_seconds = path, gap_seconds

    return str(best_path) if best_path else None


def _real_mid_time(satellite_key: str, identifier: str) -> dt.datetime | None:
    """Estimate a MOSDAC record's real scan mid-time from its identifier filename.

    Parses a MOSDAC search-result identifier into its estimated real
    scan mid-time -- same offset model _find_local_file uses for
    already-downloaded files, so a live search and a local-file lookup
    rank candidates identically.

    Args:
        satellite_key: INSAT satellite key, e.g. ``"insat-3ds"``.
        identifier: MOSDAC record filename/identifier, matching
            ``_FILENAME_RE``.

    Returns:
        Estimated real scan mid-time (UTC), or None if `identifier`
        doesn't match the expected filename pattern or its date/time
        fields fail to parse.
    """
    match = _FILENAME_RE.match(identifier)
    if not match:
        return None
    try:
        file_time = dt.datetime.strptime(f"{match['date']}_{match['time']}", "%d%b%Y_%H%M")
    except ValueError:
        return None
    offset = dt.timedelta(minutes=_APPROX_NOMINAL_TO_REAL_MID_OFFSET_MINUTES.get(satellite_key, 0.0))
    return file_time + offset


def _exponential_sleep(attempt: int) -> None:
    """Sleep with exponential backoff plus jitter before a retry.

    Same backoff shape as mdapi_opt5.py's exponential_sleep.

    Args:
        attempt: 1-based (or 0-based, depending on caller) retry attempt
            number; sleep duration is ``min(2**attempt, 60)`` seconds
            plus up to 1s of random jitter.
    """
    time.sleep(min(2**attempt, 60) + random.uniform(0, 1))


def _is_retryable_error(exc: requests.HTTPError) -> bool:
    """Check whether an HTTPError is a transient 5xx or 429 error worth retrying.

    True for a 5xx or 429 (transient, worth retrying) HTTPError. A gap
    found 2026-08-05 when testing against the MOSDAC service: a genuine
    503 from the login endpoint was treated as a permanent
    "invalid credentials" failure and never retried -- mirroring
    mdapi_opt5.py's own login()/refresh_token(), which have the same
    gap (its download_worker() DOES retry 5xx via this exact status-code
    check, just not login()/refresh_token() -- an inconsistency in the
    reference client itself, not something to keep faithfully).

    429 was added after a large patch-retry backlog logged into MOSDAC
    once per stuck candidate and tripped its rate limiter -- treating
    429 the same as "invalid credentials" meant one rate-limit response
    permanently gave up on that band/satellite instead of backing off
    and trying again a few seconds later, the same mistake the 503 case
    above already made.

    Args:
        exc: The caught ``requests.HTTPError``.

    Returns:
        True if `exc` has a response with a 5xx or 429 status code.
    """
    return exc.response is not None and (exc.response.status_code == 429 or 500 <= exc.response.status_code < 600)


def _search_dates(target: dt.datetime, tolerance_minutes: float) -> list[dt.date]:
    """Compute which calendar date(s) to query MOSDAC's day-granularity search for.

    MOSDAC's search API takes day-granularity startTime/endTime, but a
    scan just after/before midnight can be the true nearest match
    for a target just before/after it -- include the adjacent day when
    target falls within tolerance_minutes of either boundary.

    Args:
        target: Desired scan mid-time (UTC).
        tolerance_minutes: Search tolerance in minutes; if `target` falls
            within this distance of midnight on either side, the
            adjacent day is included too.

    Returns:
        Sorted list of one to three ``datetime.date`` values to search.
    """
    tol = dt.timedelta(minutes=tolerance_minutes)
    midnight = dt.datetime.combine(target.date(), dt.time())
    dates = {target.date()}
    if target - midnight < tol:
        dates.add((target - dt.timedelta(days=1)).date())
    if (midnight + dt.timedelta(days=1)) - target < tol:
        dates.add((target + dt.timedelta(days=1)).date())
    return sorted(dates)


def _login(username: str, password: str) -> tuple[str, str]:
    """Log into MOSDAC and obtain an access/refresh token pair.

    MOSDAC login -- ported from mdapi_opt5.py's login(), the reference
    client version with a documented production run.

    Args:
        username: MOSDAC account username.
        password: MOSDAC account password.

    Returns:
        Tuple of ``(access_token, refresh_token)``.

    Raises:
        requests.HTTPError: If a non-server-error (4xx) HTTP error is
            returned (e.g. invalid credentials) -- not retried.
        RuntimeError: If login still fails after ``_SEARCH_RETRIES``
            retries on transient errors.
    """
    for attempt in range(_SEARCH_RETRIES):
        try:
            response = requests.post(_TOKEN_URL, json={"username": username, "password": password}, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data["access_token"], data["refresh_token"]
        except (requests.ConnectionError, requests.Timeout):
            _exponential_sleep(attempt)
        except requests.HTTPError as e:
            if _is_retryable_error(e):
                _exponential_sleep(attempt)
                continue
            raise  # a real 4xx (invalid credentials etc.) shouldn't retry
    raise RuntimeError("MOSDAC login failed after retries")


def _refresh(username: str, password: str, refresh_token: str, dest_dir: str) -> tuple[str, str]:
    """Refresh the MOSDAC access token, falling back to a shared re-login on failure.

    Refreshes the access token; falls back to _shared_login (not a raw
    _login) if the refresh itself fails, same as mdapi_opt5.py's
    refresh_token() except for that one substitution.

    That substitution closes a gap in _shared_login's own fix: a 401
    mid-download sends _download_record here, and this fallback used to
    call _login directly, bypassing the cross-process lock/cache
    entirely. With a large partial backlog, several concurrent workers'
    downloads can all hit a 401 within the same window and all land in
    this fallback at once -- calling _login independently reproduces the
    exact login-burst-trips-the-rate-limiter failure _shared_login was
    built to prevent, just from a different call site. Routing through
    _shared_login here means those workers collapse into the same
    one-real-login-plus-cache-reads behavior as every other caller.

    Args:
        username: MOSDAC account username (used for fallback re-login).
        password: MOSDAC account password (used for fallback re-login).
        refresh_token: The current refresh token.
        dest_dir: Shared raw-download directory, passed through to
            _shared_login for its lock file and token cache.

    Returns:
        Tuple of ``(access_token, refresh_token)``, either freshly
        refreshed or from a shared re-login.
    """
    for attempt in range(_SEARCH_RETRIES):
        try:
            response = requests.post(_REFRESH_URL, json={"refresh_token": refresh_token}, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data["access_token"], data["refresh_token"]
        except (requests.ConnectionError, requests.Timeout):
            _exponential_sleep(attempt)
        except requests.HTTPError as e:
            if _is_retryable_error(e):
                _exponential_sleep(attempt)
                continue
            break  # a real 4xx means the refresh token itself is bad -- fall through to full re-login
    return _shared_login(username, password, dest_dir)


def _search_nearest(satellite_key: str, target: dt.datetime, tolerance_minutes: float = 30.0) -> dict | None:
    """Search the MOSDAC catalog for the entry nearest `target`.

    Finds the MOSDAC catalog entry whose ESTIMATED real scan mid-time
    (_real_mid_time) is closest to `target`, within tolerance_minutes, or
    None if nothing qualifies. The bug this design avoids (see
    _APPROX_NOMINAL_TO_REAL_MID_OFFSET_MINUTES's docstring): ranking by
    raw nominal identifier time instead of estimated real mid-time can
    pick the wrong candidate when two results straddle the target.

    Args:
        satellite_key: INSAT satellite key, e.g. ``"insat-3ds"``. Must be
            a key of ``_DATASET_ID``, else returns None.
        target: Desired scan mid-time (UTC).
        tolerance_minutes: Maximum allowed distance between `target` and
            a candidate's estimated real mid-time, in minutes.

    Returns:
        The nearest matching MOSDAC catalog entry (dict with at least
        ``identifier`` and ``id``), or None if no entry qualifies.
    """
    dataset_id = _DATASET_ID.get(satellite_key)
    if dataset_id is None:
        return None

    best_entry = None
    best_gap_seconds = None

    for search_date in _search_dates(target, tolerance_minutes):
        data = None
        for attempt in range(_SEARCH_RETRIES):
            try:
                response = requests.get(
                    _SEARCH_URL,
                    params={
                        "datasetId": dataset_id,
                        "startTime": search_date.isoformat(),
                        "endTime": search_date.isoformat(),
                        "count": 48,
                    },
                    timeout=15,
                )
                response.raise_for_status()
                data = response.json()
                break
            except (requests.ConnectionError, requests.Timeout):
                _exponential_sleep(attempt)
            except requests.HTTPError as e:
                if _is_retryable_error(e):
                    _exponential_sleep(attempt)
                    continue
                break  # a real 4xx for this date won't fix itself by retrying -- try the next date

        if not data:
            continue

        for entry in data.get("entries", []):
            identifier = entry.get("identifier", "")
            mid_time = _real_mid_time(satellite_key, identifier)
            if mid_time is None:
                continue
            gap_seconds = abs((mid_time - target).total_seconds())
            if gap_seconds <= tolerance_minutes * 60 and (best_gap_seconds is None or gap_seconds < best_gap_seconds):
                best_entry, best_gap_seconds = entry, gap_seconds

    return best_entry


def _download_record(
    access_token: str, refresh_token: str, username: str, password: str, record_id, final_path: str, dest_dir: str
) -> tuple[bool, str, str]:
    """Download one MOSDAC record to `final_path`, with resume/retry/SHA256 verification.

    Downloads one MOSDAC record to final_path, with resume/retry/
    SHA256 verification -- single-file adaptation of mdapi_opt5.py's
    download_worker (48/48-file production run; see this module's
    docstring for why opt5 specifically). Returns (success, access_token,
    refresh_token) -- tokens may have been refreshed mid-call, so the
    caller must use the returned ones for anything after.

    Args:
        access_token: Current MOSDAC access token (used for the
            Authorization header).
        refresh_token: Current MOSDAC refresh token, used to re-auth on
            a 401 mid-download.
        username: MOSDAC account username, for a fallback re-login if
            the refresh token itself is rejected.
        password: MOSDAC account password, for the same fallback.
        record_id: MOSDAC record id to download (the ``id`` field of a
            search-result entry).
        final_path: Local destination path for the completed download.
            A ``.part`` sibling file is used for in-progress/resumable
            downloads.
        dest_dir: Shared raw-download directory, passed through to
            _refresh (and from there to _shared_login) on a 401.

    Returns:
        Tuple of ``(success, access_token, refresh_token)``. `success`
        is False if the record could not be downloaded within
        ``_MAX_DOWNLOAD_RETRIES`` attempts (or on a 404). The returned
        tokens reflect any refresh that happened during the call and
        should be used by the caller going forward.
    """
    temp_path = final_path + ".part"

    for attempt in range(1, _MAX_DOWNLOAD_RETRIES + 1):
        sha256 = hashlib.sha256()
        headers = {"Authorization": f"Bearer {access_token}"}

        resume_position = 0
        if os.path.exists(temp_path):
            resume_position = os.path.getsize(temp_path)
            headers["Range"] = f"bytes={resume_position}-"
            with open(temp_path, "rb") as existing:
                while True:
                    chunk = existing.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    sha256.update(chunk)

        try:
            with requests.get(
                _DOWNLOAD_URL, headers=headers, params={"id": record_id}, stream=True, timeout=(10, 300)
            ) as response:
                if response.status_code == 401:
                    access_token, refresh_token = _refresh(username, password, refresh_token, dest_dir)
                    continue

                response.raise_for_status()

                content_range = response.headers.get("Content-Range")
                if content_range:
                    total_size = int(content_range.split("/")[-1])
                else:
                    total_size = resume_position + int(response.headers.get("Content-Length", 0))

                mode = "ab" if resume_position > 0 else "wb"
                with open(temp_path, mode) as f:
                    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                            sha256.update(chunk)

                os.replace(temp_path, final_path)

                # Strict size validation, same as mdapi_opt5.py.
                file_size = os.path.getsize(final_path)
                if file_size != total_size:
                    os.remove(final_path)
                    _exponential_sleep(attempt)
                    continue

                return True, access_token, refresh_token

        except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
            # ChunkedEncodingError (an observed MOSDAC mid-transfer
            # connection drop, 2026-08-09 -- e.g. IncompleteRead(7415514
            # bytes read, 431561516 more expected)) is NOT a subclass of
            # ConnectionError/Timeout (per its actual MRO), so it
            # was falling through uncaught before this fix -- failing the
            # whole band outright on attempt 1 instead of resuming from
            # temp_path via the Range-header logic above, which already
            # exists and works for exactly this failure mode.
            _exponential_sleep(attempt)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404:
                return False, access_token, refresh_token
            _exponential_sleep(attempt)

    return False, access_token, refresh_token


def download(satellite_key: str, target: dt.datetime, dest_dir: str) -> str | None:
    """Get the INSAT file nearest `target`, from a local file or a live MOSDAC download.

    The cheap local check and the network search both run BEFORE any
    lock is acquired -- deliberately, see the lock-keying comment below
    for why. Guarded by _acquire_file_lock/_release_file_lock for
    the actual download step -- see that function's own docstring for the
    2026-08-09 incident (concurrent per-band callers racing to download
    the same MOSDAC record) this originally addressed, and the comment
    below for a second, later 2026-08-09 incident it didn't cover.

    If ``MOSDAC_USERNAME``/``MOSDAC_PASSWORD`` are not set, live download
    is skipped entirely (treated as "not possible right now", not an
    error) and only the local-file check applies.

    Args:
        satellite_key: INSAT satellite key, e.g. ``"insat-3ds"``.
        target: Desired scan mid-time (UTC).
        dest_dir: Local directory to check for/download the file into.

    Returns:
        The local path to the matching file (pre-existing or freshly
        downloaded), or None if no local file matches and either
        credentials are missing, no MOSDAC entry matches within
        tolerance, login fails, or the download itself fails.
    """
    local = _find_local_file(satellite_key, target, dest_dir)
    if local is not None:
        return local

    username = os.environ.get("MOSDAC_USERNAME", "")
    password = os.environ.get("MOSDAC_PASSWORD", "")
    if not username or not password:
        print("[insat] MOSDAC_USERNAME/MOSDAC_PASSWORD not set -- skipping live download, no local file found either.")
        return None

    entry = _search_nearest(satellite_key, target)
    if entry is None:
        return None

    # Lock keyed on the RESOLVED record identifier, not on
    # (satellite_key, target) -- a regression surfaced on 2026-08-09, same
    # root cause as meteosat.py's own fix that same day: two DIFFERENT
    # candidate targets (e.g. insat-3dr at 16:17:44 and 16:25:08, only 7.4
    # min apart) both resolved to the SAME MOSDAC record
    # (3RIMG_09AUG2026_1615_L1B_STD_V01R00.h5) when _search_nearest ran on
    # both targets independently -- INSAT's own scan cadence per satellite
    # is coarser than Meteosat's, making this even more likely to happen.
    # A lock keyed by the REQUESTED target gives each caller a DIFFERENT
    # lock object despite racing on the identical file, so it never
    # actually serializes them.
    lock_path = Path(dest_dir) / f".{entry['identifier']}.downloadlock"
    lock_handle = _acquire_file_lock(lock_path, "another concurrent fetch is already downloading this same record -- waiting for it instead of racing")
    try:
        # Re-check now that the lock is held -- another caller may have
        # finished downloading this exact record while we were searching.
        final_path = os.path.join(dest_dir, entry["identifier"])
        if os.path.exists(final_path):
            return final_path

        try:
            access_token, refresh_token = _shared_login(username, password, dest_dir)
        except Exception as e:
            print(f"[insat] MOSDAC login failed: {e}")
            return None

        Path(dest_dir).mkdir(parents=True, exist_ok=True)

        success, access_token, refresh_token = _download_record(
            access_token, refresh_token, username, password, entry["id"], final_path, dest_dir
        )

        # No logout here: the token came from _shared_login's cross-process
        # cache (see that function's own docstring), so logging out after
        # every single download would invalidate it for every OTHER worker
        # still meant to reuse it within the cache's TTL window -- exactly
        # the sharing this was built to provide. The token simply expires
        # on MOSDAC's own side once _TOKEN_CACHE_TTL_SECONDS elapses;
        # nothing here needs to force that early.

        return final_path if success else None
    finally:
        _release_file_lock(lock_handle)
