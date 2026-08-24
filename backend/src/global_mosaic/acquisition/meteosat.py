"""Meteosat (SEVIRI/MVIRI) acquisition via EUMETSAT Data Store, using the
official `eumdac` client.

Migrated from the original prototype's meteosat.py. That file had a live
EUMETSAT consumer key/secret hardcoded in plaintext -- moved to environment
variables here (EUMETSAT_CONSUMER_KEY / EUMETSAT_CONSUMER_SECRET, loaded
from backend/.env via python-dotenv). Never commit real values for those --
backend/.env is gitignored; only backend/.env.example (placeholders) is
tracked.

Collection IDs below are only verified for HRSEVIRI (Prime, 0E). The IODC
(41.5E / 9.5E depending on current role -- see satellites/sat_metadata.py's
meteosat-10/11 Longitude_History_Note) collection ID is an unverified guess
-- check the EUMETSAT Data Store catalog before relying on it.
"""

from __future__ import annotations

import datetime as dt
import os
import random
import sys
import time
from pathlib import Path

import eumdac
import eumdac.errors
from dotenv import load_dotenv

load_dotenv()

_MAX_DOWNLOAD_RETRIES = 8
"""A reproducible failure, first seen 2026-08-07: product.open() on an
otherwise-valid, already-searchable product intermittently raises
eumdac.errors.EumdacError ("... Unauthorised (403)") -- not a
credentials/entitlement problem (token acquisition and catalog search both
succeed every time; the SAME product identifier that failed once succeeded
on a later isolated retry). The pattern across several test downloads: the
first request in a back-to-back burst succeeds, requests
fired right after it in quick succession fail -- classic download-rate-
limiting, most likely mapped to a 403 by EUMETSAT's Data Store rather than
the more conventional 429. A short backoff before retrying is the
appropriate response either way, mirroring insat.py's own proven
_exponential_sleep retry pattern for this exact class of transient failure.

Raised from 5 to 8, 2026-08-08: a satellite that exhausts its retry
budget here doesn't just fail this fetch, it lands the whole timestamp in
schedule_pipeline.py's state["partial"] tracking (a satellite still
missing from an already-published mosaic, rechecked every tick for up to
--catchup-wait-hours). More budget per attempt trades a slightly longer
worst-case single-fetch stall for a better chance of resolving on this
same pass instead of needing that separate tracking + a later reprocess
at all."""


def _exponential_sleep(attempt: int) -> None:
    """Sleep with exponential backoff plus jitter before a retry.

    Same backoff shape as insat.py's own _exponential_sleep.

    Args:
        attempt: 1-based retry attempt number; sleep duration is
            ``min(2**attempt, 60)`` seconds plus up to 1s of random jitter.
    """
    time.sleep(min(2**attempt, 60) + random.uniform(0, 1))


_MIN_REQUEST_INTERVAL_SECONDS = 8.0
"""Proactive counterpart to _MAX_DOWNLOAD_RETRIES (see that constant's own
docstring for the full diagnosis): config.py's PHASE0_SLOTS puts
meteosat_prime and meteosat_iodc back-to-back, so within one tile's
satellite loop their product.open() calls fire in immediate succession --
exactly the "first request in a burst succeeds, the very next one gets
403'd" pattern _MAX_DOWNLOAD_RETRIES exists to recover from. Recovering
after the fact only works if EUMETSAT's rate-limit cooldown clears
within that retry budget's own backoff -- spacing requests out up front
instead avoids triggering the limit at all, so a first-run Meteosat fetch
is more likely to just succeed outright instead of needing partial-
timestamp tracking to catch it on a later retick.

Raised from 3.0 to 8.0, 2026-08-08: still an unverified guess (EUMETSAT
publishes no documented rate-limit threshold), widened on the same
reasoning as _MAX_DOWNLOAD_RETRIES's own increase -- favor a first-
attempt success over needing the retry budget (or partial-tracking) to
recover at all. Costs about 5s of extra wall-clock per meteosat_
iodc fetch immediately following meteosat_prime's (the only place this
throttle actually waits out its full interval in practice, since nothing
else in a single tile's satellite loop calls product.open() back-to-
back) -- negligible against a ~28 min Tier A run. Worth tightening or
loosening again if better data suggests a different threshold.

Made cross-process 2026-08-10 (see _throttle_request's own docstring for
the mechanism) -- a gap identified alongside the search-retry fix
above: this throttle's state used to be a plain module-level Python
variable, which only ever coordinated calls WITHIN one process. Since
2026-08-09's shared-executor change, schedule_pipeline.py runs due-
timestamp AND partial-recheck work concurrently, up to
_MAX_CONCURRENT_PIPELINE_RUNS (4) simultaneous run_phase2_tier.py
subprocesses -- each one is a fresh Python process with its OWN copy of
this module, so each independently believed it was "the first request,
no need to wait," while EUMETSAT saw the aggregate request rate across
all of them at once. Plausible contributor to the same 403s already
observed -- this in-memory throttle was never actually enforcing
_MIN_REQUEST_INTERVAL_SECONDS against the traffic pattern that most
needed it."""

_THROTTLE_LOCK_PATH = Path(__file__).resolve().parents[3] / "data" / "cache" / ".eumetsat_request_throttle.lock"
"""Fixed, deliberately NOT derived from dest_dir -- dest_dir is
per-run-overridable (GLOBAL_MOSAIC_DEST_DIR, see run_phase2_tier.py's own
comment on why: isolating raw downloads per concurrent run) and this
throttle needs to coordinate the EUMETSAT account-level rate limit
ACROSS every concurrent run regardless of which dest_dir it's using, so it
lives at one fixed path instead -- backend/data/cache, the same directory
schedule_pipeline_state.json already uses for exactly this kind of
project-wide shared state."""


def _throttle_request() -> None:
    """Block until at least _MIN_REQUEST_INTERVAL_SECONDS have passed since the last EUMETSAT request.

    Enforces _MIN_REQUEST_INTERVAL_SECONDS between successive EUMETSAT
    requests -- covers meteosat_prime -> meteosat_iodc's back-to-back
    fetch within one tile the same as before, but now ALSO across every
    concurrent run_phase2_tier.py subprocess sharing schedule_pipeline.py's
    executor pool (see _MIN_REQUEST_INTERVAL_SECONDS's own 2026-08-10
    update for why the previous per-process version didn't actually cover
    that).

    Cross-process via the same OS-level exclusive-lock primitive
    _acquire_download_lock already uses below (msvcrt.locking/fcntl.flock),
    applied to _THROTTLE_LOCK_PATH -- a fixed, shared file, not one keyed
    per download like the download lock is. The lock file's own mtime IS
    the shared "last request time" state (wall-clock time.time(), not
    time.monotonic() -- monotonic's reference point isn't a documented
    cross-process-comparable value, wall-clock via mtime avoids that
    entirely). Holding the lock for the whole check-wait-touch sequence
    makes it atomic across every process: whichever process gets the lock
    next always sees the TRUE last request time across all of them, not
    just its own history. Brief by construction (bounded by
    _MIN_REQUEST_INTERVAL_SECONDS, a few seconds at most), so unlike
    _acquire_download_lock this doesn't need a timeout/stuck-process
    detection -- both msvcrt and fcntl release a process's locks
    automatically on exit, crash or clean, so nothing here can wedge this
    lock permanently even if a process dies while holding it.

    Returns:
        None. Blocks the calling thread/process for as long as needed.
    """
    _THROTTLE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Checked BEFORE open()'s own create-if-missing side effect -- open(...,
    # "a+b") sets a freshly-created file's mtime to "now" as part of
    # creating it, which would otherwise make the very first-ever call look
    # like it already had a prior request an instant ago and wait out the
    # full interval for nothing. A small race exists here (two processes
    # could both see "doesn't exist yet" right at startup and both skip
    # their first wait) -- accepted as a boot-time-only transient, not an
    # ongoing steady-state risk, rather than engineering full protection
    # against a race that only ever matters for the first couple of calls
    # after this file doesn't exist yet.
    existed_before = _THROTTLE_LOCK_PATH.exists()
    f = open(_THROTTLE_LOCK_PATH, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # blocking -- wait for it, don't fail immediately
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # blocking

        if existed_before:
            last_request_time = os.path.getmtime(_THROTTLE_LOCK_PATH)
            remaining = _MIN_REQUEST_INTERVAL_SECONDS - (time.time() - last_request_time)
            if remaining > 0:
                time.sleep(remaining)
        os.utime(_THROTTLE_LOCK_PATH, None)  # mark "now" as the last request time for the next caller, any process
    finally:
        if sys.platform == "win32":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()

DATASETS = {
    "SEVIRI": "EO:EUM:DAT:MSG:HRSEVIRI",
    "SEVIRI_IODC": "EO:EUM:DAT:MSG:HRSEVIRI-IODC",  # UNVERIFIED, check before use
    "MVIRI": "EO:EUM:DAT:0882",
}

FILE_EXTENSIONS = {
    "SEVIRI": ".nat",
    "SEVIRI_IODC": ".nat",
    "MVIRI": ".nc",
}

TIME_WINDOWS = {
    "SEVIRI": dt.timedelta(minutes=15),
    "SEVIRI_IODC": dt.timedelta(minutes=15),
    "MVIRI": dt.timedelta(minutes=30),
}

_DOWNLOAD_LOCK_TIMEOUT_SECONDS = 1800.0
_DOWNLOAD_LOCK_POLL_SECONDS = 5.0


def _acquire_download_lock(lock_path: Path):
    """Acquire an OS-level exclusive lock file, blocking (via polling) until available.

    Blocks (polling, same mechanism as mosaic_io.py's own
    _acquire_write_lock and insat.py's own _acquire_download_lock --
    duplicated here rather than imported, this project's established
    pattern for a small utility shared by modules that shouldn't
    otherwise depend on each other) until an OS-level exclusive lock on
    `lock_path` can be acquired, then returns the open lock-file handle.

    Same bug class as insat.py's own lock was added for (2026-08-09), just
    surfaced here a tick later: schedule_pipeline.py's
    _patch_newly_available fetches all missing Tier A bands for one
    satellite CONCURRENTLY, but one EUMETSAT product (one
    dataset_name+target) covers every band in a single .nat/.nc file --
    concurrent per-band callers for the SAME (dataset_name, target) all
    resolve to the same `path`/`temp_path` in download() below and raced
    to open the identical .part temp file, producing a raw unhandled
    WinError 32 (Windows sharing violation) instead of one download
    followed by cheap local-file hits. Keyed on (dataset_name, target,
    dest_dir), matching insat.py's (satellite_key, target, dest_dir)
    keying for the same reason.

    Args:
        lock_path: Path to the lock file to create/open and lock.

    Returns:
        The open lock-file handle, holding the exclusive lock. Must be
        passed to :func:`_release_download_lock` when done.

    Raises:
        TimeoutError: If the lock cannot be acquired within
            ``_DOWNLOAD_LOCK_TIMEOUT_SECONDS`` (another process appears
            to be stuck holding it).
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
                    f"could not acquire download lock for {lock_path} after {_DOWNLOAD_LOCK_TIMEOUT_SECONDS:.0f}s -- "
                    "another process appears to be genuinely stuck holding it, not just still downloading"
                )
            if not announced:
                print("    [meteosat] another concurrent fetch is already downloading this same product -- waiting for it instead of racing")
                announced = True
            time.sleep(_DOWNLOAD_LOCK_POLL_SECONDS)
            waited += _DOWNLOAD_LOCK_POLL_SECONDS
        else:
            return f


def _release_download_lock(f) -> None:
    """Release and close a lock handle acquired by :func:`_acquire_download_lock`.

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


def _get_datastore() -> eumdac.DataStore:
    """Build an authenticated eumdac DataStore client from environment credentials.

    Returns:
        An ``eumdac.DataStore`` authenticated with an access token built
        from the ``EUMETSAT_CONSUMER_KEY``/``EUMETSAT_CONSUMER_SECRET``
        environment variables (loaded from ``backend/.env``).

    Raises:
        KeyError: If either environment variable is not set.
    """
    key = os.environ["EUMETSAT_CONSUMER_KEY"]
    secret = os.environ["EUMETSAT_CONSUMER_SECRET"]
    token = eumdac.AccessToken((key, secret))
    return eumdac.DataStore(token)


def find_nearest_product(dataset_name: str, target: dt.datetime, tolerance_minutes: float = 5.0):
    """Find the eumdac Product whose sensing mid-time is closest to `target`.

    Returns the eumdac Product whose sensing mid-time
    ((sensing_start + sensing_end) / 2) is closest to `target`, within
    `tolerance_minutes`, or None if nothing qualifies.

    Fixes a bug found during Phase 0 testing: the previous version of
    `download()` just grabbed whatever product(s) `collection.search()`
    returned for a single fixed [target, target + window) range and used
    the first one -- not necessarily the nearest to `target`, and prone to
    missing a closer product that fell just outside that one-sided window.
    For example, target 2026-07-30T17:24:43 against MSG3's HRSEVIRI
    collection had a product at mid-time 17:21:26.9 (3.3 min away, well
    within a 5-min tolerance) that the old window-based search never saw,
    while it picked up a worse one 11.7 min away instead.

    eumdac's `Product` objects expose `sensing_start`/`sensing_end`
    directly from the search results, so ranking by proximity needs no
    download, only the initial (cheap) collection search.

    Retry+throttle added here 2026-08-10, mirroring download()'s own
    _MAX_DOWNLOAD_RETRIES/_throttle_request pattern exactly:
    schedule_pipeline_tick.log showed the exact same class of error
    _MAX_DOWNLOAD_RETRIES's docstring already diagnoses --
    eumdac.errors.EumdacError "Unauthorised (403)", EUMETSAT's own
    download-rate-limiting mapped to 403 rather than 429 -- ALSO hits this
    search/get_collection step ("Could not get search options for
    EO:EUM:DAT:MSG:HRSEVIRI-IODC - Unauthorised (403)"), not just
    product.open() below. Before this fix, a single transient 403 here was
    immediately fatal -- no retry at all -- caught only by
    run_phase2_tier.py's bare try/except, which just logs "acquisition
    failed, skipping" and gives up, even though the identical failure mode
    on the download side already got 8 retries with backoff.

    Args:
        dataset_name: Key into ``DATASETS``, e.g. ``"SEVIRI"``.
        target: Desired sensing mid-time (UTC).
        tolerance_minutes: Maximum allowed distance between `target` and
            a candidate product's sensing mid-time, in minutes.

    Returns:
        The nearest matching ``eumdac`` ``Product``, or None if nothing
        in the search bracket falls within `tolerance_minutes`.

    Raises:
        eumdac.errors.EumdacError: If the collection search still fails
            after ``_MAX_DOWNLOAD_RETRIES`` retries with backoff.
    """
    datastore = _get_datastore()
    dataset_id = DATASETS[dataset_name]

    # Bracket wide enough to see the nearest product on either side of
    # `target`, even if it falls just outside +-tolerance_minutes: one
    # full repeat cycle beyond the tolerance covers that.
    bracket = TIME_WINDOWS[dataset_name] + dt.timedelta(minutes=tolerance_minutes)

    products: list | None = None
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_DOWNLOAD_RETRIES + 1):
        try:
            _throttle_request()
            collection = datastore.get_collection(dataset_id)
            products = list(collection.search(dtstart=target - bracket, dtend=target + bracket))
            last_exc = None
            break
        except eumdac.errors.EumdacError as exc:
            last_exc = exc
            if attempt < _MAX_DOWNLOAD_RETRIES:
                _exponential_sleep(attempt)
    if last_exc is not None:
        raise last_exc

    best = None
    best_gap_seconds = None
    for product in products:
        mid = product.sensing_start + (product.sensing_end - product.sensing_start) / 2
        gap_seconds = abs((mid - target).total_seconds())
        if gap_seconds <= tolerance_minutes * 60 and (best_gap_seconds is None or gap_seconds < best_gap_seconds):
            best, best_gap_seconds = product, gap_seconds

    return best


def download(dataset_name: str, target: dt.datetime, dest_dir: str, tolerance_minutes: float = 5.0) -> list[str]:
    """Download the single nearest product to `target` for `dataset_name`.

    Downloads the single nearest product to `target` (within
    `tolerance_minutes`) for `dataset_name`. Returns [] if nothing
    qualifies within tolerance.

    The search runs BEFORE any lock is acquired -- deliberately, see the
    lock-keying comment below for why. Guarded by _acquire_download_lock/
    _release_download_lock for the actual download step -- see that
    function's own docstring for the 2026-08-09 incident (concurrent
    per-band callers racing to download the same EUMETSAT product) this
    originally addressed, and the docstring comment below for a second,
    later 2026-08-09 incident it didn't cover.

    Args:
        dataset_name: Key into ``DATASETS``, e.g. ``"SEVIRI"``.
        target: Desired sensing mid-time (UTC).
        dest_dir: Local directory to download the product's file(s) into.
        tolerance_minutes: Maximum allowed distance between `target` and
            the nearest product's sensing mid-time, in minutes.

    Returns:
        Local paths of the downloaded (or already-cached) file(s) for the
        matched product, in the order of ``product.entries`` that match
        the dataset's file extension. Empty list if no product qualifies
        within tolerance or it has no matching-extension entries.

    Raises:
        eumdac.errors.EumdacError: If a download attempt still fails
            after ``_MAX_DOWNLOAD_RETRIES`` retries with backoff.
        OSError: If a local file-write attempt still fails after
            ``_MAX_DOWNLOAD_RETRIES`` retries.
        TimeoutError: If the per-product download lock cannot be acquired
            within ``_DOWNLOAD_LOCK_TIMEOUT_SECONDS``.
    """
    product = find_nearest_product(dataset_name, target, tolerance_minutes)
    if product is None:
        return []

    extension = FILE_EXTENSIONS[dataset_name]
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    relevant_entries = [e for e in product.entries if os.path.basename(e).endswith(extension)]
    if not relevant_entries:
        return []

    # Lock keyed on the RESOLVED filename(s), not on (dataset_name,
    # target) -- a regression surfaced on 2026-08-09 after raising
    # schedule_pipeline.py's concurrency from 2 to 4: two DIFFERENT
    # candidate targets only 7.4 min apart (e.g. 15:47:44 and 15:55:08)
    # both resolved to the SAME MSG3 scan
    # (MSG3-SEVI-MSG15-0100-NA-20260809155743...nat) when
    # find_nearest_product ran on both targets independently. A lock keyed
    # by the REQUESTED target gives each caller a DIFFERENT lock object
    # even though they're about to race on the identical file -- doesn't
    # serialize anything, defeating the whole point. Keying on the
    # filename(s) instead means any two callers that resolve to the same
    # product, however they got there, always contend for the same lock.
    lock_key = "_".join(sorted(os.path.basename(e) for e in relevant_entries))
    lock_path = Path(dest_dir) / f".{lock_key}.downloadlock"
    lock_handle = _acquire_download_lock(lock_path)
    try:
        downloaded = []

        for entry in relevant_entries:
            filename = os.path.basename(entry)
            path = os.path.join(dest_dir, filename)
            if os.path.exists(path):
                downloaded.append(path)
                continue

            # .part temp file + os.replace only on full success -- without
            # this, a mid-download failure (see _MAX_DOWNLOAD_RETRIES's
            # docstring) leaves an empty/truncated file at `path` itself,
            # which the "if os.path.exists(path): ... continue" skip-if-
            # already-downloaded check above would then wrongly treat as a
            # real prior success on any later retry/re-run of this same
            # timestamp, silently feeding a corrupt 0-byte file downstream
            # instead of actually re-fetching it. Same pattern insat.py's
            # own _download_record already uses for the same reason.
            temp_path = path + ".part"
            last_exc: Exception | None = None
            for attempt in range(1, _MAX_DOWNLOAD_RETRIES + 1):
                try:
                    _throttle_request()
                    with product.open(entry) as f, open(temp_path, "wb") as out:
                        out.write(f.read())
                    os.replace(temp_path, path)
                    downloaded.append(path)
                    last_exc = None
                    break
                except (eumdac.errors.EumdacError, OSError) as exc:
                    last_exc = exc
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    if attempt < _MAX_DOWNLOAD_RETRIES:
                        _exponential_sleep(attempt)
            if last_exc is not None:
                raise last_exc

        return downloaded
    finally:
        _release_download_lock(lock_handle)
