"""Recurring "tick" for automated Tier A production -- meant to be invoked
every ~20 minutes by an external scheduler (Windows Task Scheduler; there is
no internal sleep-loop here, matching this project's existing preference for
small on-demand scripts over long-lived daemons, e.g. retain_rolling_window.py).

Each tick:
  1. Adds every DUE fixed-slot candidate timestamp since the last one ever
     added (state["last_candidate_target"]), up to (now -
     --lookback-margin-minutes). Candidates land on a fixed 4-per-hour grid
     (see _FIXED_SLOT_OFFSETS: :17:44/:25:08/:47:44/:55:08 every hour) --
     NOT an arbitrary evenly-spaced step -- these are established
     INSAT-synchronized slot times:
     :17:44/:47:44 land on INSAT-3DS, :25:08/:55:08 on INSAT-3DR,
     giving a ~15 min combined INSAT cadence instead of 3DS/3DR
     colliding on the same ~30 min slot). Backfilling every due slot (not
     just adding a single "now minus margin" candidate) matters because a
     tick can be skipped outright rather than merely delayed -- see the
     Windows Task Scheduler entry's IgnoreNew multiple-instances
     policy, needed because a triggered Tier A run can itself take longer
     than one ~20 min tick interval. A single-candidate design would
     silently lose whichever slot a skipped tick would have added; this
     backfills it on the next tick that actually runs instead (capped at
     _MAX_BACKFILL_CANDIDATES for a long outage, so catching up doesn't
     itself take days).
  2. For every timestamp currently tracked as "pending" (this run's + any
     carried over from earlier ticks), cheaply checks (search only, no
     download -- see _check_satellite_available) which of its expected
     satellites (config.resolve_all_phase0_slots) have posted data yet.
  3. INSAT is NOT required for a timestamp to process -- MOSDAC history
     (an 8-day total outage Jul24-Aug3 2026, and even in
     normal operation INSAT can lag hours behind the other 5 satellites,
     which are typically ready within ~20-40 min) means waiting on it would
     block every mosaic for as long as INSAT is slow. Instead: a timestamp
     with every OTHER expected satellite present (or --max-wait-hours,
     default 24, elapsed for those) gets processed now, INSAT or not.

     After processing, the ACTUAL written file is read back
     (_actual_contributors) to see which expected satellites really made it
     in -- deliberately not just INSAT, and deliberately not assumed from
     entry["found"]. A live case (Himawari, 2026-08-07) showed a core
     satellite can end up missing too: entry["found"]'s search-only check
     (wider tolerance) said "found", but the per-band fetch's tighter
     real-time sync check (SEARCH_WINDOW_MINUTES) rejected that same
     satellite as too stale and left it out of the file. Any satellite
     missing from the written file -- INSAT or otherwise -- moves this
     timestamp to a SEPARATE tracking list, state["partial"] (see
     _run_partial_recheck), checked every tick for up to
     --catchup-wait-hours (default 168 = 7 days, matched to the ~8-day
     MOSDAC outage duration observed) for each missing satellite catching
     up. Any one showing up triggers a reprocess (run_phase2_tier.py again,
     same command, overwriting the already-published file), and `missing`
     is then re-derived from that fresh file's actual contributors rather
     than assumed. Past 7 days, remaining missing satellites are dropped
     from tracking and the version without them stays final.
  4. Applies the same 7-day/672-file rolling-window retention
     retain_rolling_window.py already implements (see _run_retention) --
     once per tick, across the tiera .nc dir and all 4 gallery band
     subdirectories independently. This replaces running that script by
     hand; it's no longer needed as a separate step.

Due `pending` timestamps (point 2) are processed up to
_MAX_CONCURRENT_PIPELINE_RUNS at a time, not strictly one-at-a-time -- see
that constant's own docstring for the real capacity numbers behind this
(measured ~28.4 min/timestamp Tier A cost vs. the ~15 min fixed-slot
arrival interval, a chronic deficit a sequential scheduler can never
recover from). State-file writes (`partial`/`pending`/raw-cache cleanup)
still only ever happen in main()'s own thread, never inside a worker --
only the expensive subprocess-spawning compute is parallelized.

The state file is now saved (_save_state, atomic) after EVERY individual
timestamp resolves -- each pending candidate's search-only update, each
pending timestamp's processing result, and each partial timestamp's
patch/reprocess result -- not just once at the very end of the tick as
before. This fixes a 2026-08-08 incident: an interrupted tick (killed
process, crash) previously lost ALL of that tick's progress, even work
that had already been fully written to disk. A ~6-hour tick that had
genuinely finished patching 12 of 17 partial timestamps (.nc files +
gallery thumbnails on disk, per _actual_contributors) still showed all 17
as unresolved in schedule_pipeline_state.json because it never reached
its one and only _save_state call. Saving after each item makes an
interrupted tick lose at most the one item in flight, not everything the
tick had already accomplished.

Coverage checking is cadence-aware: a satellite isn't treated as "missing"
until enough time has passed since the target for its own nominal repeat
cycle to plausibly have posted a file (see _due_for_check) -- checking, say,
INSAT (~30 min cadence) 3 minutes after target and finding nothing yet is
expected, not a real gap, and shouldn't cost a network round-trip to learn
that. Once a satellite is confirmed present for a given pending timestamp,
it's never re-checked for that timestamp again.

Tier A only (not Tier B) -- per this project's own capacity findings,
Tier B's memory/time cost makes it unsuitable for unattended recurring
runs; Tier A alone is the realistic automated target.

Usage (run manually once to see what it would do, then wire into Task
Scheduler at a 20-minute interval)::

    python schedule_pipeline.py --final-out-dir output/tiera --gallery-dir output/gallery
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import concurrent.futures
from typing import Callable

import netCDF4  # only used to read back real contributors from a written file, see _actual_contributors
import numpy as np  # only used to decode a patched band's real value grid for _regenerate_gallery_thumbnail

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from global_mosaic.acquisition import goes, himawari, insat, meteosat  # noqa: E402
from global_mosaic.config import resolve_all_phase0_slots  # noqa: E402
from global_mosaic.processing.mosaic_io import patch_tier_file_with_satellite, read_single_satellite_npz  # noqa: E402
from global_mosaic.processing.preview import (  # noqa: E402
    BAND_PREVIEW_SPECS,
    compute_display_time,
    new_accumulator,
    render_band_preview,
)
from global_mosaic.satellites.sat_metadata import Satellites_Objects  # noqa: E402

# retain_rolling_window.py lives alongside this script (both in backend/
# scripts/) -- Python puts the running script's own directory at
# sys.path[0] automatically, so this import needs no extra path setup.
# find_expired() has no argparse/CLI dependency (just re/datetime/pathlib),
# so it's called directly rather than subprocess-ing that script's CLI --
# same "no unnecessary process spawn for a cheap operation" approach
# _check_satellite_available's search-only calls already use.
from retain_rolling_window import find_expired  # noqa: E402

# Duplicated from run_phase2_tier.py rather than importing that module here
# (which would pull in satpy/netCDF4/xarray etc. just to reuse two small
# constants) -- keep in sync if either changes there. Same "duplicate a
# tightly-coupled small constant with a keep-in-sync comment" pattern
# config.py's own INSAT phase/offset tables already use for the same reason.
METEOSAT_DATASET_BY_SLOT = {
    "meteosat_prime": "SEVIRI",
    "meteosat_iodc": "SEVIRI_IODC",
}
SEARCH_WINDOW_MINUTES = 5

_DEFAULT_STATE_PATH = Path(__file__).resolve().parents[1] / "data" / "cache" / "schedule_pipeline_state.json"
_RUN_PHASE2_TIER = Path(__file__).resolve().parent / "run_phase2_tier.py"
_TIER_A_BANDS = ("mir", "wv", "tir1", "tir2")
_TIER_A_BANDS_UPPER = tuple(b.upper() for b in _TIER_A_BANDS)  # "MIR"/"WV"/"TIR1"/"TIR2", matches --single-band's own case convention

_MAX_CONCURRENT_PIPELINE_RUNS = 4
"""How many due `pending` timestamps get processed concurrently within one
tick, each via _run_pipeline (a blocking subprocess.run call) in its own
worker thread -- CPython releases the GIL while a thread is blocked on
subprocess.run, so this is genuine OS-level parallelism across separate
`run_phase2_tier.py` processes, not just interleaving on one core.

Real capacity problem this addresses, 2026-08-08: measured average Tier A
processing time (~28.4 min) exceeds the 4/hour fixed-slot arrival interval
(~15 min) by ~13.4 min/timestamp -- a chronic deficit (confirmed against
docs/project-notes "deployment_capacity_check"'s earlier estimate) that
means a sequential scheduler can never keep pace with live real-time, only
fall further behind.

Raised from 2 to 4, then back down to 3, 2026-08-09 (all same day): real
evidence that day (a backlog of 15-16 missed candidates projected to
accumulate during one already-running tick, itself caused by 2 concurrent
runs' effective ~15.5 min/timestamp throughput landing right at the ~15
min arrival interval with essentially no margin -- any real variance tips
it into unbounded backlog growth, which is what was actually happening)
confirmed 2 was no longer enough. This was ORIGINALLY dropped from 4 due
to real PermissionError crashes under concurrent partial-recheck fallback
reprocesses (see _MAX_CONCURRENT_PARTIAL_RECHECKS's own docstring) -- but
the actual root cause of those crashes is now identified and fixed, not
merely "investigated": concurrent per-band fetches for the same
(satellite, target) raced to open the same shared raw file (one INSAT
.h5/Meteosat .nat file covers every band) -- pinned down via the exact
WinError 32 sharing-violation signature and fixed with download
locks (insat.py/meteosat.py's _acquire_download_lock) plus a matching
merge-write lock (mosaic_io.py's _acquire_write_lock) for the final .nc
open. With that specific crash risk closed, 4 was tried -- but a live
memory check at that point (still 2 concurrent workers actually running)
found only ~4.4GB free out of 31.77GB total (Get-CimInstance
Win32_OperatingSystem), with the 2 real worker processes' own Private
Bytes up to ~8.5GB each. Doubling worker count risked exceeding physical
RAM into the ~22GB pagefile (Get-CimInstance Win32_PageFileUsage) --
survivable (not a hard crash) but likely to cause severe swapping-driven
slowdowns for exactly the large in-memory numpy/dask/xarray array work
this pipeline does, which would undermine the entire point of raising
concurrency. Pulled back to 3 as the cautious middle ground initially --
then deliberately raised back to 4 that same day as an explicit,
informed experiment (not a rediscovery of the earlier "fits comfortably"
assumption, which the live measurement above already contradicted once):
watch real free memory / pagefile usage on the first tick that actually
runs with 4, and back off again immediately if it drops critically low
or the system shows swap-driven slowdown rather than the intended
throughput gain. The "fits comfortably" claim below is the 2026-08-08
estimate that the 2026-08-09 measurement already contradicted once --
treat it as unverified until a real 4-concurrent measurement replaces
it.

Shared with _run_partial_recheck's own phase-2 work since 2026-08-09 --
formerly a separate _MAX_CONCURRENT_PARTIAL_RECHECKS pool (also 4, same
value, see that constant's own removed docstring for its history:
originally 4, dropped to 2 after a real PermissionError incident, raised
back to 4 once the actual root cause -- concurrent per-band fetches
racing on one shared raw file -- was fixed with real download locks, same
story as this constant's own 2026-08-09 section above). Merging them into
ONE shared executor fixed a real sequencing problem: due-timestamp
processing (main()'s own ThreadPoolExecutor `with` block) used to fully
finish and exit -- implicitly waiting for every submitted run via the
`with` block's own shutdown(wait=True) -- BEFORE _run_partial_recheck
even started, even though partial-recheck's lighter patch work could
safely have overlapped with a long due-timestamp phase instead of
queueing behind it. Now main() submits BOTH due-timestamp AND
partial-recheck phase-2 jobs to the SAME executor before consuming either
side's completions (see main()'s own Phase 2 comment) -- the two
`as_completed` loops that follow still run one after another in main()'s
single thread (same "one real writer" invariant _finish_partial_entry's
own docstring documents, no lock needed for that reason alone), but the
underlying WORK for both already started running concurrently in the
background the moment it was submitted, up to this ONE shared budget --
not two independent 4+4 pools, which would double the real memory
pressure this docstring's own 2026-08-09 section already found tight at
just 4. State-file bookkeeping (`partial`/`del pending`/raw-cache
cleanup) still stays entirely single-threaded in main()'s own thread,
only after each submitted run's future resolves -- this is what avoids
the real state-coordination race that would exist if two entire scheduler
ticks ran as independent top-level processes, each holding its own
in-memory state snapshot -- here there is still only ONE process that ever touches
schedule_pipeline_state.json; parallelism is scoped to the expensive
subprocess-spawning/patching compute only.

Verified real capacity for raising this above 1, 2026-08-08 (see
2026-08-09 correction above -- this specific figure was never
re-verified against real concurrent measurement until this same day,
when it was found to be optimistic): this deployment machine has 32GB
RAM / 16 logical processors; a single Tier A run's real peak memory
(tile-major, 4GB/tile budget, per-band subprocess isolation) was
estimated well under the ~16-18GB figures measured for Tier B's much
larger grid. Raising this further is NOT free for another reason either:
it does nothing about the one risk hardware can't fix -- external
satellite-API rate limiting (EUMETSAT/MOSDAC/GOES/Himawari all real,
observed sources of 403/503s). More concurrent runs means
more concurrent fetch traffic to the same external services, so tune
this value based on real observed retry/failure rates AND real free
memory, not just local CPU/core headroom."""

_MAX_CONCURRENT_PATCH_FETCHES = 4
"""Caps how many satellite x band FETCHES _patch_newly_available runs
concurrently for one target's patch attempt (see that function's own
docstring for the fetch-vs-write split this enables). A target needing
both Himawari and INSAT has up to 8 such fetches (2 satellites x 4
bands); left fully unbounded that's 8 concurrent subprocess spawns per
target, times however many partial-recheck target-level workers are
active at once (up to _MAX_CONCURRENT_PIPELINE_RUNS, since 2026-08-09
partial-recheck shares that one pool rather than having its own separate
limit -- see that constant's own docstring) = potentially many dozens at
once if left fully unbounded. 4 is a deliberately modest cap, not "as
many as fit" -- these are lightweight compared to a full 6-satellite
reprocess (one candidate, not six, per subprocess), but they still hit
the same real external rate limits this project has already observed
(EUMETSAT/MOSDAC/GOES/Himawari 403/503s) -- more concurrent
fetch traffic to the same external services during an already-large
partial-recheck backlog is exactly the wrong time to increase load on
them further."""

# Same shared raw-download-cache default AND same env var override as
# run_phase2_tier.py's own DEST_DIR (duplicated rather than imported --
# see this file's existing METEOSAT_DATASET_BY_SLOT comment for why: not
# worth pulling in satpy/netCDF4/xarray etc. just to reuse one path).
# Real incident this matters for, 2026-08-08: this directory grew
# unbounded (no automatic cleanup existed at all) until it filled the
# entire G: drive, which then corrupted the state file mid-write and took
# the scheduler down for ~3 hours -- see _run_raw_cache_cleanup.
_DEFAULT_DEST_DIR = Path(os.environ.get("GLOBAL_MOSAIC_DEST_DIR", str(Path(__file__).resolve().parents[1] / "data" / "raw")))

# Same default as retain_rolling_window.py's own --keep: 4 timestamps/hour
# (this scheduler's own _FIXED_SLOT_OFFSETS) x 24h x 7 days.
_DEFAULT_RETENTION_KEEP = 672
_GALLERY_BANDS = ("mir", "wv", "tir1", "tir2")  # matches run_phase2_tier.py's band_gallery_dir naming

# Floor on how long to wait after `target` before treating a satellite that
# hasn't posted yet as actually check-worthy, even for a fast-cadence
# satellite -- covers real post-scan processing/upload latency at the ground
# segment, which isn't part of the imaging cadence itself.
_CADENCE_GRACE_FLOOR_MINUTES = 15.0

# Real, established target slot times for this project -- 4 per hour, at
# these (minute, second) offsets, NOT an arbitrary evenly-spaced grid. Of
# an earlier set of 4 "optimal" candidate times, only :17:44/:47:44
# actually landed cleanly on real INSAT-3DS scans; :25:08/:55:08 were
# added once config.py's nearest-INSAT-generation-wins logic made
# INSAT-3DR usable too -- together giving a real ~15 min combined INSAT
# cadence across the two generations instead of one satellite's ~30 min.
_FIXED_SLOT_OFFSETS = [(17, 44), (25, 8), (47, 44), (55, 8)]  # (minute, second), sorted ascending within the hour

# Safety cap on how many candidates one tick will backfill in a row (see
# _add_missed_candidates). Without this, a long outage (task disabled,
# laptop off/asleep for days) would make the first tick after it try to
# create a candidate for EVERY missed fixed slot in that whole gap -- each
# of which eventually needs its own ~20-min real Tier A run, so a multi-day
# gap could take multi-day to work through. 20 candidates (5 hours' worth at
# 4/hour) is a reasonable amount to actually catch up on; anything older
# than that is dropped (logged, not silent) rather than queued.
_MAX_BACKFILL_CANDIDATES = 20


def _acquire_single_instance_lock(lock_path: Path):
    """Acquires an OS-level exclusive lock to prevent a second concurrent
    scheduler instance.

    Returns an open file handle holding an OS-level exclusive lock on
    `lock_path`, or None if another process already holds it. Real
    incident this exists to prevent, 2026-08-08: two full
    ``schedule_pipeline.py`` processes were found running concurrently against the
    same schedule_pipeline_state.json (root cause never confirmed),
    each with its own in-memory
    state snapshot, racing on `partial`/`pending` bookkeeping and even
    writing the same temp file from two processes at once. Whatever
    launches a second instance in the future, this makes that a fast,
    loud failure instead of a silent race.

    Deliberately an OS-level lock (msvcrt.locking on Windows, flock on
    POSIX) tied to the file handle/process lifetime, NOT a "does a lock
    file exist" flag -- a plain existence-flag lock would need manual
    staleness cleanup (what happens if the holder is killed instead of
    exiting cleanly, exactly what happened to the process that was
    killed in the incident above?). An OS-level lock is released automatically the instant the
    holding process's handle closes, including on a hard kill or crash,
    so there is no stale-lock state to ever clean up by hand.

    Args:
        lock_path: Path to the lock file to create/open and lock.

    Returns:
        An open, locked file handle on success, or None if the lock is
        already held by another process.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+b")
    try:
        if os.fstat(f.fileno()).st_size == 0:
            # msvcrt.locking needs a real byte at the offset it locks --
            # an empty file has nothing there yet on a brand-new lock path.
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
        return None
    return f


def _release_single_instance_lock(f) -> None:
    """Releases the OS-level lock acquired by _acquire_single_instance_lock
    and closes the file handle.

    Args:
        f: The open, locked file handle returned by
            _acquire_single_instance_lock.
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


def _load_state(path: Path) -> dict:
    """Loads the scheduler's persisted state dict from disk, self-healing
    from a missing or empty/corrupt state file, and migrating any
    old-schema `partial` entries in place.

    Args:
        path: Path to the state JSON file (e.g. --state-file).

    Returns:
        The state dict (at least containing a "pending" key). A missing
        file returns a fresh {"pending": {}}; an empty/corrupt file does
        the same after printing a warning (see the inline comment below
        for the real 2026-08-08 incident this handles).
    """
    if not path.exists():
        return {"pending": {}}
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        # This happened live on 2026-08-08: the G: drive filled up
        # completely (see the raw-cache cleanup added specifically to
        # prevent it recurring), and _save_state's write got interrupted
        # after truncating the file but before writing its content,
        # leaving a genuine 0-byte file on disk. Without this check, every
        # subsequent tick crashes at json.loads("") before doing ANY
        # work -- this silently took the scheduler down for ~3 hours
        # (every 20-min tick crashing immediately, no new candidates ever
        # added, no visible symptom beyond the empty file itself). Treating
        # it the same as "file doesn't exist" makes this self-healing --
        # losing bookkeeping (state["partial"]'s tracked missing-satellite
        # entries) for one tick is a far smaller cost than crashing
        # indefinitely. _save_state's own atomic-write fix (temp file +
        # os.replace) is the actual prevention; this is the defensive
        # backstop for if it ever happens anyway.
        print(f"  WARNING: state file {path} is empty/corrupt -- starting fresh (lost partial-tracking history, see this warning if it recurs)")
        return {"pending": {}}
    state = json.loads(raw)
    _migrate_partial_schema(state)
    return state


def _migrate_partial_schema(state: dict) -> None:
    """In-place upgrade of any state["partial"] entry still using an older
    `missing` schema, in two steps:

    1. The old INSAT-only schema (entry["insat_satellite_key"], a single
       str) to the generalized schema (entry["missing"], a dict of
       slot_name -> satellite_key -- see _run_partial_recheck). A real
       state file written by the pre-generalization code has exactly
       this old shape; without migrating it, _run_partial_recheck's
       entry["missing"] lookup raises KeyError on the very next tick
       that reaches it, crashing a run that could already be ~20 minutes
       into real work by that point.

    2. The flat per-satellite schema (entry["missing"][slot] == a plain
       satellite_key str) to the per-band schema added 2026-08-09
       (entry["missing"][slot] == {"satellite_key": str, "bands": [...]}
       -- see _compute_missing's own docstring for the real incident
       this fixes: a satellite present in SOME bands but missing from
       others, for a purely transient/operational reason, used to be
       silently accepted as "no longer missing" the moment ANY band
       succeeded). A flat-schema entry doesn't know which SPECIFIC bands
       were actually missing (that granularity didn't exist yet), so
       this conservatively assumes all 4 Tier A bands still need
       (re)checking -- safe, since re-fetching/re-patching an
       already-correct band is a harmless no-op (see
       ``patch_tier_file_with_satellite``'s own idempotency guard), just some one-time
       redundant work on the first tick that touches a migrated entry,
       not a correctness issue.

    Args:
        state: The full state dict (as returned by _load_state), mutated
            in place -- every entry under state["partial"] is upgraded to
            the current per-band `missing` schema.

    Returns:
        None. `state` is modified in place.
    """
    for entry in state.get("partial", {}).values():
        if "missing" not in entry and "insat_satellite_key" in entry:
            entry["missing"] = {"insat": entry.pop("insat_satellite_key")}

    for entry in state.get("partial", {}).values():
        missing = entry.get("missing", {})
        for slot, value in list(missing.items()):
            if isinstance(value, str):
                missing[slot] = {"satellite_key": value, "bands": list(_TIER_A_BANDS_UPPER)}


def _save_state(path: Path, state: dict) -> None:
    """Writes via a .tmp file + os.replace, not path.write_text() directly.
    Real failure hit live 2026-08-08: the G: drive filled up completely
    mid-write, and a direct write_text() (open in "w" mode truncates
    immediately, then writes) left a genuine 0-byte state file on disk --
    every following tick then crashed at _load_state's json.loads("")
    before doing any work, silently taking the scheduler down for ~3
    hours. os.replace is atomic on both POSIX and Windows (NTFS) -- the
    real file at `path` either has its complete old content or its
    complete new content, never a partial/truncated write, regardless of
    what interrupts the write (disk full, crash, power loss).

    Args:
        path: Destination path for the state file (e.g. --state-file).
        state: The full state dict to serialize (JSON, indented, sorted
            keys).

    Returns:
        None.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def _expected_satellites(target: dt.datetime) -> dict[str, str]:
    """Resolves which satellites are expected to cover `target`.

    slot_name -> satellite_key for every Phase 0 slot that resolves to an
    actual satellite at `target` (a slot with no operational satellite at
    all -- e.g. a date before any occupant existed -- isn't something to
    wait on, so it's left out rather than counted as permanently missing).

    Args:
        target: The candidate timestamp to resolve expected coverage for.

    Returns:
        Mapping of slot_name -> satellite_key for every slot with a real
        operational occupant at `target`.
    """
    resolved = resolve_all_phase0_slots(target)
    return {slot: r.satellite_key for slot, r in resolved.items() if r is not None}


def _cadence_minutes(satellite_key: str) -> float:
    """Looks up a satellite's nominal full-disk imaging cadence.

    Args:
        satellite_key: Key identifying the satellite (e.g. "himawari-9").

    Returns:
        The satellite's Full_Disk_Temporal_Resolution_Minutes, in minutes,
        from its first instrument entry in Satellites_Objects.
    """
    imager = next(iter(Satellites_Objects[satellite_key]["Instruments"].values()))
    return float(imager["Full_Disk_Temporal_Resolution_Minutes"])


def _due_for_check(satellite_key: str, target: dt.datetime, now: dt.datetime) -> bool:
    """Decides whether enough time has passed for a satellite to plausibly
    have posted data for `target` yet, so it's actually worth a real
    availability check (see _CADENCE_GRACE_FLOOR_MINUTES and
    _cadence_minutes).

    Args:
        satellite_key: Key identifying the satellite to check.
        target: The candidate timestamp being covered.
        now: The current tick's timestamp.

    Returns:
        True if `now` is at least the satellite's cadence (or the grace
        floor, whichever is larger) past `target`.
    """
    margin = max(_cadence_minutes(satellite_key), _CADENCE_GRACE_FLOOR_MINUTES)
    return now >= target + dt.timedelta(minutes=margin)


def _check_satellite_available(slot_name: str, satellite_key: str, target: dt.datetime) -> bool:
    """Cheap search-only availability check -- mirrors run_phase2_tier.py's
    fetch_slot dispatch, but calls each acquisition module's search-only
    finder (S3 listing / catalog search, no download) instead of its
    download(). Any exception (e.g. the real EUMETSAT 403 Unauthorised seen
    repeatedly) is caught here and treated as "not available
    yet" rather than crashing this whole tick -- same principle as
    fetch_slot's own now-fixed meteosat catch, just at the check stage
    instead of the actual-fetch stage.

    Args:
        slot_name: Phase 0 slot name (e.g. "meteosat_prime", "insat").
        satellite_key: Key identifying the satellite occupying that slot.
        target: The candidate timestamp to search for.

    Returns:
        True if a matching file/product was found for this satellite near
        `target`; False if none was found, the slot/dataset is
        unrecognized, or the search itself raised an exception.
    """
    try:
        if satellite_key.startswith("goes-"):
            if satellite_key in goes.ABI_BUCKETS:
                return goes.find_nearest_abi_file(satellite_key, target) is not None
            return goes.find_nearest_legacy_entry(satellite_key, target) is not None

        if satellite_key.startswith("himawari-"):
            return bool(himawari.find_segment_keys(satellite_key, target))

        if satellite_key.startswith("meteosat-"):
            dataset_name = METEOSAT_DATASET_BY_SLOT.get(slot_name)
            if dataset_name is None:
                return False
            product = meteosat.find_nearest_product(dataset_name, target, tolerance_minutes=SEARCH_WINDOW_MINUTES)
            return product is not None

        if satellite_key.startswith("insat-"):
            # insat._search_nearest is private but is exactly the search-
            # only half of insat.download() (no login/token needed -- MOSDAC's
            # search endpoint is public) -- reused directly rather than
            # duplicated, same codebase.
            return insat._search_nearest(satellite_key, target) is not None

        return False
    except Exception as exc:
        print(f"    [{slot_name}] {satellite_key}: availability check failed ({exc}) -- treating as not-yet-available")
        return False


def _update_pending_entry(entry: dict, target: dt.datetime, expected: dict[str, str], now: dt.datetime) -> dict:
    """Refreshes one pending timestamp's "found" satellite set by running a
    cheap availability check for each expected satellite that is both not
    already found and due for a check (see _due_for_check). A satellite
    already in `entry["found"]` is never re-checked again for this
    timestamp.

    Args:
        entry: The pending-timestamp state dict (has a "found" list; may
            not have one yet on first update).
        target: The candidate timestamp this entry tracks.
        expected: Mapping of slot_name -> satellite_key expected to cover
            `target` (see _expected_satellites).
        now: The current tick's timestamp, used for cadence gating.

    Returns:
        The same `entry` dict, mutated in place, with "found" updated to
        a sorted list of newly-confirmed satellite keys (plus any already
        present).
    """
    found = set(entry.get("found", []))
    for slot_name, satellite_key in expected.items():
        if satellite_key in found:
            continue
        if not _due_for_check(satellite_key, target, now):
            continue
        if _check_satellite_available(slot_name, satellite_key, target):
            found.add(satellite_key)
            print(f"    [{slot_name}] {satellite_key}: FOUND")
        else:
            print(f"    [{slot_name}] {satellite_key}: not yet available")
    entry["found"] = sorted(found)
    return entry


def _slot_instants_in_range(start_exclusive: dt.datetime, end_inclusive: dt.datetime) -> list[dt.datetime]:
    """Every _FIXED_SLOT_OFFSETS instant in (start_exclusive, end_inclusive],
    sorted ascending -- walks one hour at a time rather than computing this
    analytically since the 4 offsets aren't evenly spaced (7:24 and 22:36
    gaps, alternating), so there's no closed-form "next slot" arithmetic.

    Args:
        start_exclusive: Lower bound of the range (exclusive).
        end_inclusive: Upper bound of the range (inclusive).

    Returns:
        A sorted list of fixed-slot datetimes strictly after
        `start_exclusive` and up to and including `end_inclusive`. Empty
        if `start_exclusive >= end_inclusive`.
    """
    if start_exclusive >= end_inclusive:
        return []
    hour_cursor = start_exclusive.replace(minute=0, second=0, microsecond=0)
    instants = []
    while hour_cursor <= end_inclusive:
        for minute, second in _FIXED_SLOT_OFFSETS:
            instant = hour_cursor.replace(minute=minute, second=second)
            if start_exclusive < instant <= end_inclusive:
                instants.append(instant)
        hour_cursor += dt.timedelta(hours=1)
    return sorted(instants)


def _most_recent_slot_at_or_before(boundary: dt.datetime) -> dt.datetime:
    """The single latest _FIXED_SLOT_OFFSETS instant <= boundary -- used
    only for a cold start (no state["last_candidate_target"] yet), where
    there's no prior anchor to backfill from, so rather than walk
    indefinitely far into the past this just picks up the most recent slot
    and starts tracking forward from there.

    Args:
        boundary: Upper bound (inclusive) to search for a fixed slot at or
            before.

    Returns:
        The latest fixed-slot datetime at or before `boundary`.

    Raises:
        RuntimeError: If no fixed slot could be found within the current
            or previous hour -- should never happen since
            _FIXED_SLOT_OFFSETS is non-empty.
    """
    for hour_offset in (0, -1):
        hour_cursor = (boundary + dt.timedelta(hours=hour_offset)).replace(minute=0, second=0, microsecond=0)
        eligible = [hour_cursor.replace(minute=m, second=s) for m, s in _FIXED_SLOT_OFFSETS if hour_cursor.replace(minute=m, second=s) <= boundary]
        if eligible:
            return max(eligible)
    raise RuntimeError(f"no fixed slot found at or before {boundary} -- shouldn't happen, _FIXED_SLOT_OFFSETS is non-empty")


def _add_missed_candidates(state: dict, pending: dict, now: dt.datetime, lookback_margin_minutes: float) -> None:
    """Adds every fixed-slot candidate timestamp (_FIXED_SLOT_OFFSETS) due
    since the last one ever added, up to (now - lookback_margin_minutes) --
    not just a single newest candidate. A single-candidate design would
    silently LOSE a slot (not just delay it) whenever a tick doesn't run at
    all -- e.g. the IgnoreNew multiple-instances policy on the real Windows
    Task Scheduler entry means a tick gets skipped outright (not queued) if
    the previous one is still going, which happens whenever a triggered
    Tier A run takes longer than the ~20 min tick interval, a completely
    normal occurrence, not an edge case. "Add whichever slot is nearest to
    right now" has no memory of what should have happened while it wasn't
    running, so an in-between slot would be gone forever, not just late.

    Persisting the last candidate ever added (state["last_candidate_target"],
    kept even after that candidate is processed and removed from `pending`)
    fixes this: every tick backfills every missed slot up to the present,
    so a skipped tick just means the NEXT tick that runs adds more than one
    candidate, not that a slot silently vanishes."""
    newest_boundary = (now - dt.timedelta(minutes=lookback_margin_minutes)).replace(microsecond=0)

    last_iso = state.get("last_candidate_target")
    if last_iso is None:
        # First run ever (or state file predates this feature) -- nothing
        # to backfill from yet, just pick up the most recent slot.
        candidate = _most_recent_slot_at_or_before(newest_boundary)
        _add_one_candidate(state, pending, candidate, now)
        return

    last = dt.datetime.fromisoformat(last_iso)
    due = _slot_instants_in_range(last, newest_boundary)
    if len(due) > _MAX_BACKFILL_CANDIDATES:
        skipped = len(due) - _MAX_BACKFILL_CANDIDATES
        due = due[-_MAX_BACKFILL_CANDIDATES:]
        print(
            f"  WARNING: {skipped} missed candidate slot(s) older than the {_MAX_BACKFILL_CANDIDATES}-candidate "
            "backfill cap are being dropped (long gap since this last ran) -- backfilling only the most recent "
            f"{_MAX_BACKFILL_CANDIDATES}."
        )

    for candidate in due:
        _add_one_candidate(state, pending, candidate, now)


def _add_one_candidate(state: dict, pending: dict, candidate: dt.datetime, now: dt.datetime) -> None:
    """Registers one new fixed-slot candidate timestamp as pending and advances the backfill anchor.

    Args:
        state: The full state dict; `state["last_candidate_target"]` is
            updated to `candidate`'s ISO string.
        pending: The pending-timestamps dict, mutated in place with a new
            entry for `candidate`.
        candidate: The fixed-slot timestamp to add.
        now: The current tick's timestamp, recorded as the new entry's
            `first_seen`.

    Returns:
        None. Mutates `state` and `pending` in place.
    """
    key = candidate.isoformat()
    pending[key] = {"first_seen": now.isoformat(), "found": []}
    state["last_candidate_target"] = key
    print(f"  new candidate timestamp: {key}")


def _cleanup_stale_lock_files(final_out_dir: Path, dest_dir: Path, now: dt.datetime) -> None:
    """Deletes every .writelock (final_out_dir/.locks/) and .downloadlock
    (dest_dir/) file whose mtime is older than `now`, the current tick's
    own start time. Safe by construction, not just by convention: main()
    holds _acquire_single_instance_lock for the tick's ENTIRE blocking
    duration (including every subprocess.run call it makes), so only one
    schedule_pipeline.py process -- and everything it spawns -- can ever
    be running at a time. That means any lock file that already existed
    when THIS tick started is provably from a fully-finished previous
    run, never a currently-held lock, so there is no risk of deleting a
    file out from under an active holder. Purely a tidiness step (see
    _acquire_write_lock/_acquire_download_lock's own docstrings for why
    the file's mere existence was never required for correctness) --
    keeps .locks/ and dest_dir from accumulating one stray file per
    timestamp forever.

    Args:
        final_out_dir: Output directory whose ``.locks/*.writelock``
            files are checked.
        dest_dir: Raw-download directory whose ``*.downloadlock`` files
            are checked.
        now: The current tick's own start time; any lock file with an
            older mtime is considered from a finished previous run.

    Returns:
        None. Deletes stale lock files as a side effect and prints a
        summary count.
    """
    removed = 0
    locks_dir = final_out_dir / ".locks"
    dirs_and_patterns = [(locks_dir, "*.writelock"), (dest_dir, "*.downloadlock")]
    for directory, pattern in dirs_and_patterns:
        if not directory.is_dir():
            continue
        for f in directory.glob(pattern):
            try:
                mtime = dt.datetime.fromtimestamp(f.stat().st_mtime, dt.timezone.utc).replace(tzinfo=None)
                if mtime < now:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
    if removed:
        print(f"cleaned up {removed} stale lock file(s) from a previous run")


def _run_retention(final_out_dir: Path, gallery_dir: Path, keep: int, dest_dir: Path) -> None:
    """Same 7-day/672-file rolling window retain_rolling_window.py's own
    docstring describes -- run once for the main tiera_<ts>.nc dir, plus
    once per band's gallery subdirectory (gallery_dir/tiera/<band>/, see
    run_phase2_tier.py's band_gallery_dir), five directories total, each
    retained independently (a band that's been failing to render previews
    doesn't affect another band's retention count, and vice versa).
    A directory that doesn't exist yet (e.g. a band gallery folder before
    its first-ever successful render) is skipped quietly, not an error --
    unlike retain_rolling_window.py's own CLI, which treats a missing --dir
    as fatal since there a missing dir usually means a typo'd path, not "no
    output yet".

    Also cleans up any raw source files still associated with a retired
    tiera_<ts>.nc file (see _cleanup_raw_files_for_target) -- a defensive
    backstop, not the primary cleanup path. The primary path is main()/
    _run_partial_recheck, which clean up as soon as a timestamp is fully
    done needing them (fully resolved, or gave up after --catchup-wait-
    hours) -- long before this rolling-window retention would ever reach
    that file under normal operation. This backstop exists for whatever
    might slip through that primary path (e.g. a raw cleanup call that
    itself failed silently on a locked file).

    Args:
        final_out_dir: Directory holding the main ``tiera_<ts>.nc`` files.
        gallery_dir: Gallery ROOT directory (its ``tiera/<band>/``
            subdirectories are retained too).
        keep: Number of most-recent timestamps to keep per directory
            (same meaning as retain_rolling_window.py's ``--keep``).
        dest_dir: Raw-download directory, passed through to
            :func:`_cleanup_raw_files_for_target` for the backstop cleanup.

    Returns:
        None. Deletes expired files (and their associated raw source
        files) as a side effect and prints a summary per directory.
    """
    targets = [(final_out_dir, "tiera", ".nc")]
    for band in _GALLERY_BANDS:
        targets.append((gallery_dir / "tiera" / band, band, ".png"))

    for directory, prefix, suffix in targets:
        if not directory.is_dir():
            continue
        expired = find_expired(directory, prefix, keep, suffix)
        for path in expired:
            if directory == final_out_dir:
                try:
                    target = dt.datetime.strptime(path.stem.split("_")[1], "%Y%m%dT%H%M%S")
                    _cleanup_raw_files_for_target(target, final_out_dir, dest_dir)
                except (IndexError, ValueError):
                    pass  # unexpected filename shape -- skip raw cleanup for this one, still retire the mosaic file itself below
            path.unlink()
        if expired:
            print(f"  retention: {directory} -- retired {len(expired)} file(s), {keep} most recent kept")


def _final_path(final_out_dir: Path, target: dt.datetime) -> Path:
    """Builds the expected published tier-A file path for a target timestamp.

    Args:
        final_out_dir: Directory holding published ``tiera_<ts>.nc`` files.
        target: The mosaic target timestamp.

    Returns:
        The expected path, ``<final_out_dir>/tiera_<YYYYMMDDTHHMMSS>.nc``
        (whether or not it actually exists yet).
    """
    return final_out_dir / f"tiera_{target.strftime('%Y%m%dT%H%M%S')}.nc"


def _actual_contributors(nc_path: Path) -> set[str]:
    """Real satellite_key_<band> values actually written into the merged
    tier file (read via raw netCDF4 -- these are already plain Python str,
    a vlen-string netCDF4 type, no decoding needed). This is the only
    reliable source of truth for "what actually ended up in this mosaic" --
    deliberately NOT derived from the scheduler's own entry["found"]
    bookkeeping, which is a coarser, wider-tolerance search-only check (see
    _check_satellite_available). A real live case (Himawari, 2026-08-07)
    showed the two can disagree: the search-only check said "found", but
    the actual per-band fetch's tighter real-time sync tolerance
    (SEARCH_WINDOW_MINUTES) rejected that same satellite as too stale to
    use, so it never made it into the file. Unioned across all 4 Tier A
    bands since a satellite could in principle sync-check into some bands
    and not others.

    Args:
        nc_path: Path to the merged tier file to read from.

    Returns:
        Set of satellite keys that actually appear in at least one Tier A
        band's ``satellite_key_<band>`` variable.
    """
    contributors: set[str] = set()
    with netCDF4.Dataset(str(nc_path), "r") as ds:
        for band in _TIER_A_BANDS:
            var_name = f"satellite_key_{band}"
            if var_name not in ds.variables:
                continue
            contributors.update(str(v) for v in ds.variables[var_name][:])
    return contributors


def _missing_bands(nc_path: Path) -> list[str]:
    """Any of _TIER_A_BANDS whose satellite_key_<band> variable is absent
    from the file ENTIRELY -- a real gap _actual_contributors alone can
    never catch, since it just `continue`s past a missing band variable
    rather than flagging it (every satellite still reads as "present"
    via whichever OTHER bands do exist). Real live case found 2026-08-08:
    tiera_20260807T224744.nc had fully correct mir/wv data (all 6
    expected satellites) but was missing its tir1/tir2 satellite_key
    variables ENTIRELY -- roughly half the file's real content silently
    absent, yet _actual_contributors alone reported every satellite as
    "present" and the scheduler had marked this target fully complete.

    Args:
        nc_path: Path to the merged tier file to check.

    Returns:
        Lowercase band names (a subset of ``_TIER_A_BANDS``) whose
        ``satellite_key_<band>`` variable is entirely absent from the file.
    """
    with netCDF4.Dataset(str(nc_path), "r") as ds:
        return [band for band in _TIER_A_BANDS if f"satellite_key_{band}" not in ds.variables]


def _band_contributors(nc_path: Path) -> dict[str, set[str]]:
    """Real satellite_key_<band> contributors PER BAND (uppercase band
    names, e.g. "MIR") -- the precise sibling of _actual_contributors'
    union-across-bands version, added 2026-08-09 after a real live case
    showed a satellite can genuinely succeed in SOME bands and fail in
    others for a purely transient/operational reason: a real MemoryError
    crashed exactly 2 of 4 single-satellite-band fetch subprocesses for
    the same satellite+target (see _patch_newly_available's own
    docstring), while the other 2 succeeded and got patched in fine. The
    old union-based check (_actual_contributors, "present in ANY band")
    would have silently accepted this as "satellite present", never
    detecting or retrying the 2 bands it actually never made it into.

    A band whose satellite_key_<band> variable is entirely absent from
    the file (see _missing_bands) reads here as an empty contributor set
    -- functionally identical, for this per-satellite-per-band purpose,
    to "no satellite made it into this band", so callers don't need a
    separate structural check to get correct per-band results.

    Args:
        nc_path: Path to the merged tier file to read from.

    Returns:
        Mapping of uppercase band name (e.g. ``"MIR"``) to the set of
        satellite keys actually present in that band -- an empty set for
        a band whose variable is entirely absent.
    """
    contributors: dict[str, set[str]] = {}
    with netCDF4.Dataset(str(nc_path), "r") as ds:
        for band in _TIER_A_BANDS:
            var_name = f"satellite_key_{band}"
            if var_name not in ds.variables:
                contributors[band.upper()] = set()
                continue
            contributors[band.upper()] = {str(v) for v in ds.variables[var_name][:]}
    return contributors


def _compute_missing(nc_path: Path, expected: dict[str, str]) -> dict[str, dict]:
    """The one real completeness check this project needs. Returns
    {slot_name: {"satellite_key": str, "bands": [band, ...]}} -- `bands`
    lists EXACTLY which of the 4 Tier A bands this satellite is still
    missing from (uppercase, e.g. "MIR"), not just whether it's missing
    at all. Added 2026-08-09 (replacing an earlier all-or-nothing
    version) after a real live case (see _band_contributors' own
    docstring) showed a satellite present in some bands but missing from
    others -- for a purely transient/operational reason, not a genuine
    per-band data difference -- used to be silently accepted as "no
    longer missing" the moment ANY band succeeded, permanently hiding
    the gap in whichever bands failed with no way to ever detect or
    retry them. Now every band a satellite is expected in but isn't
    actually present in gets tracked individually, so a subsequent patch
    attempt (_patch_newly_available) only re-fetches the SPECIFIC bands
    still needed -- not all 4 again (wasteful when most already
    succeeded), and not none (the old bug).

    Args:
        nc_path: Path to the merged tier file to check (may not exist
            yet, e.g. before the first-ever run for this target).
        expected: Mapping of slot_name -> satellite_key expected to cover
            this target (see :func:`_expected_satellites`).

    Returns:
        Mapping of ``slot_name -> {"satellite_key": str, "bands": [...]}``
        for every expected satellite missing from at least one band. If
        `nc_path` doesn't exist yet, every expected satellite is reported
        missing from all 4 Tier A bands.
    """
    if not nc_path.exists():
        return {slot: {"satellite_key": sat, "bands": list(_TIER_A_BANDS_UPPER)} for slot, sat in expected.items()}
    band_contributors = _band_contributors(nc_path)
    missing: dict[str, dict] = {}
    for slot, sat in expected.items():
        missing_bands = [band for band in _TIER_A_BANDS_UPPER if sat not in band_contributors.get(band, set())]
        if missing_bands:
            missing[slot] = {"satellite_key": sat, "bands": missing_bands}
    return missing


def _regenerate_gallery_thumbnail(nc_path: Path, gallery_dir: Path, band: str, target: dt.datetime) -> None:
    """Re-renders one band's gallery PNG from the real, just-patched .nc
    file's full grid + real contributor list. Needed because
    _patch_newly_available (unlike a full _run_pipeline reprocess) never
    goes through run_phase2_tier.py's own gallery_dir machinery at all --
    without this, a successfully patched-in satellite's pixels would
    silently never show up in the gallery thumbnail, only in the .nc file
    itself. Reads the real written array back rather than threading the
    in-memory arrays out of patch_tier_file_with_satellite -- matches
    this file's existing ground-truth-over-cheap-bookkeeping pattern (see
    _actual_contributors), and preview.py's own module docstring argument
    against a re-read (an unnecessary extra cost for the FULL pipeline,
    which already holds the tile data in memory) doesn't apply here --
    the patch path never builds a full-grid array in memory at all, so a
    real read is the only way to get one to render from.

    Silently skipped for any band not in BAND_PREVIEW_SPECS or missing
    from the file entirely -- same contract build_band_mosaic_tiled's own
    gallery_dir handling already has.

    Args:
        nc_path: Path to the just-patched merged tier file to read the
            real grid/contributors back from.
        gallery_dir: Gallery ROOT directory; the PNG is written under its
            ``tiera/<band>/`` subdirectory.
        band: Logical band name, e.g. ``"mir"`` or ``"MIR"``.
        target: The mosaic's requested target time, shown in the
            rendered title.

    Returns:
        None. Writes the rendered PNG as a side effect (and removes any
        stale prior PNG for this same target), or does nothing if `band`
        isn't renderable or its data isn't present in the file.
    """
    band_upper = band.upper()
    if band_upper not in BAND_PREVIEW_SPECS:
        return
    suffix = f"_{band.lower()}"
    with netCDF4.Dataset(str(nc_path), "r") as ds:
        value_name = f"value{suffix}"
        key_name = f"satellite_key{suffix}"
        mid_name = f"satellite_scan_mid{suffix}"
        if value_name not in ds.variables or key_name not in ds.variables or mid_name not in ds.variables:
            return
        value = ds.variables[value_name][:, :]
        if np.ma.isMaskedArray(value):
            value = value.filled(np.nan)
        satellite_keys = {str(v) for v in ds.variables[key_name][:]}
        mid_var = ds.variables[mid_name]
        scan_mids = list(
            netCDF4.num2date(mid_var[:], units=mid_var.units, calendar=mid_var.calendar, only_use_cftime_datetimes=False)
        )
        height, width = value.shape

    accumulator = new_accumulator(height, width)
    accumulator.add_tile(0, height, value)
    accumulator.satellite_keys = satellite_keys
    accumulator.scan_mids = scan_mids

    # Filename carries the real actual-mid-scan-time suffix (see preview.
    # py's render_band_preview docstring for why), which shifts every time
    # the real contributor set changes -- the stale pre-patch PNG(s) for
    # this same target won't get overwritten by that new filename, so any
    # old one(s) are removed explicitly first rather than left to
    # accumulate as dead duplicates a viewer could still stumble onto.
    band_gallery_dir = gallery_dir / "tiera" / band.lower()
    target_str = target.strftime("%Y%m%dT%H%M%S")
    for stale in band_gallery_dir.glob(f"{band.lower()}_{target_str}_*.png"):
        stale.unlink()

    actual_mid = compute_display_time(accumulator, target)
    preview_path = band_gallery_dir / f"{band.lower()}_{target_str}_{actual_mid.strftime('%Y%m%dT%H%M%S')}.png"
    render_band_preview(accumulator, band_upper, target, preview_path)
    print(f"    [{band_upper}] regenerated gallery thumbnail {preview_path}")


def _raw_files_for_nc(nc_path: Path, dest_dir: Path) -> set[Path]:
    """Every real raw source file (in dest_dir) that contributed to any
    band in this merged tier file -- ground truth, read from
    satellite_source_filename_<band> (see mosaic_io.py's
    _append_satellite_metadata_table), NOT re-derived by guessing which
    file a fresh search would pick today (today's "nearest" file for this
    target might not even be the same file that was actually used when
    this mosaic was originally built). A single filename for GOES/
    Meteosat/INSAT; a "+"-joined list of all 10 segment filenames for
    Himawari (see fetch_slot's himawari branch in run_phase2_tier.py,
    which builds source_filename this same way).

    Args:
        nc_path: Path to the merged tier file to read source filenames from.
        dest_dir: Raw-download directory the filenames are relative to.

    Returns:
        Set of raw file paths (under `dest_dir`) that contributed to
        `nc_path`.
    """
    files: set[Path] = set()
    with netCDF4.Dataset(str(nc_path), "r") as ds:
        for band in _TIER_A_BANDS:
            var_name = f"satellite_source_filename_{band}"
            if var_name not in ds.variables:
                continue
            for raw in ds.variables[var_name][:]:
                for name in str(raw).split("+"):
                    if name:
                        files.add(dest_dir / name)
    return files


def _cleanup_raw_files_for_target(target: dt.datetime, final_out_dir: Path, dest_dir: Path) -> None:
    """Deletes every raw source file that contributed to `target`'s
    published mosaic (see _raw_files_for_nc) -- called once a timestamp is
    fully done needing them: either fully resolved (nothing left in
    `missing`) or given up on (past --catchup-wait-hours). Real incident
    this exists to prevent recurring, 2026-08-08: this raw cache had NO
    cleanup at all before this, grew unbounded across hours of continuous
    runs, and filled the entire G: drive -- which then corrupted the
    scheduler's own state file mid-write and took it down for ~3 hours.
    A raw file already missing (deleted by an earlier pass, or by another
    nearby target's own cleanup -- see below) is skipped quietly, not an
    error.

    Real simplification, not a bug: this does NOT check whether some OTHER
    still-tracked target (pending or partial) also needs the same raw
    file. Two targets close together on the fixed-slot grid (e.g. GOES's
    ~10-15 min cadence vs. the 4-per-hour slot grid) can resolve to the
    exact same nearest raw file. Deleting it here doesn't break that other
    target -- every acquisition module's download() already re-fetches a
    file that's missing locally -- it just costs that target one redundant
    re-download later: a real but minor inefficiency, not a correctness
    issue, and not worth the complexity of full cross-target reference
    counting to avoid.

    Args:
        target: The mosaic timestamp whose raw source files should be
            cleaned up.
        final_out_dir: Directory holding the published ``tiera_<ts>.nc``
            file for `target`.
        dest_dir: Raw-download directory to delete matched files from.

    Returns:
        None. Deletes matched raw files as a side effect (a no-op if
        `target`'s published file doesn't exist).
    """
    nc_path = _final_path(final_out_dir, target)
    if not nc_path.exists():
        return
    for raw_path in _raw_files_for_nc(nc_path, dest_dir):
        if raw_path.exists():
            raw_path.unlink()


def _run_pipeline(target: dt.datetime, final_out_dir: Path, gallery_dir: Path, memory_budget_gb: float) -> bool:
    """Runs a full Tier A run_phase2_tier.py subprocess for one target timestamp.

    Args:
        target: The mosaic target timestamp to process.
        final_out_dir: Passed through as ``--final-out-dir``.
        gallery_dir: Passed through as ``--gallery-dir``.
        memory_budget_gb: Passed through as ``--memory-budget-gb``.

    Returns:
        True if the subprocess exited 0.
    """
    cmd = [
        sys.executable, str(_RUN_PHASE2_TIER), "A", target.isoformat(),
        "--blend-margin-km", "0.0",
        "--memory-budget-gb", str(memory_budget_gb),
        "--final-out-dir", str(final_out_dir),
        "--gallery-dir", str(gallery_dir),
        "--profile-time",
    ]
    result = subprocess.run(cmd)
    return result.returncode == 0


def _fetch_single_satellite_npz(
    target: dt.datetime, band: str, slot_name: str, satellite_key: str, npz_path: Path
) -> bool:
    """Runs run_phase2_tier.py --single-satellite as a subprocess (same
    isolation this project already uses for this exact per-satellite
    step, see that script's run_single_satellite_candidate) to build one
    satellite's reprojected candidate for one band, written to npz_path.
    Args:
        target: Target timestamp to build the candidate for.
        band: Logical band name, e.g. ``"MIR"``.
        slot_name: Logical slot name, e.g. ``"goes_east"``.
        satellite_key: Satellite key for this slot.
        npz_path: Output ``.npz`` path the subprocess is asked to write.

    Returns:
        False if that satellite genuinely has no usable data for this
        band -- mirrors run_single_satellite_candidate's own contract (a
        skip, not a failure -- the same thing a full reprocess would see
        for this satellite/band combination); True if the subprocess
        exited 0 and wrote `npz_path`.
    """
    cmd = [
        sys.executable, str(_RUN_PHASE2_TIER), "A", target.isoformat(),
        "--single-band", band,
        "--single-satellite",
        "--slot-name", slot_name,
        "--satellite-key", satellite_key,
        "--out", str(npz_path),
    ]
    result = subprocess.run(cmd)
    return result.returncode == 0 and npz_path.exists()


def _patch_newly_available(
    target: dt.datetime, final_out_dir: Path, gallery_dir: Path, missing: dict, newly_available: list[tuple[str, str]]
) -> tuple[bool, dict]:
    """Tries incrementally patching each newly-available satellite into
    the already-published file (mosaic_io.patch_tier_file_with_satellite),
    instead of a full ~20 min reprocess -- see that function's own
    docstring for why this is much cheaper (skips re-reprojecting every
    already-contributing satellite). Returns (all_succeeded,
    remaining_missing), remaining_missing using the same per-band
    {slot: {"satellite_key": str, "bands": [...]}} schema as `missing`
    (see _compute_missing's own docstring).

    Only fetches/patches the SPECIFIC bands `missing[slot]["bands"]`
    lists for each satellite -- added 2026-08-09, replacing an earlier
    version that unconditionally tried all 4 Tier A bands regardless of
    which ones actually still needed this satellite. That mattered for
    two real reasons: (1) it wasted a fetch+patch attempt on a band that
    already had this satellite, and (2) worse, a satellite was removed
    from `missing` ENTIRELY the moment ANY band succeeded, even if
    OTHER expected bands never got it -- a real live case (a MemoryError
    crashed 2 of 4 single-satellite-band fetch subprocesses for the same
    satellite+target while the other 2 succeeded) showed this silently
    and permanently hid a real per-band gap with no way to ever detect
    or retry it. Now `remaining[slot]["bands"]` only drops the bands that
    actually got patched this attempt -- a slot is only removed from
    `remaining` entirely once its `bands` list is fully empty.

    If ANY band's fetch+patch raises an unexpected exception, this
    abandons the WHOLE attempt (returns all_succeeded=False, `missing`
    unchanged) rather than leave the file in a partially-patched state
    for this satellite -- the caller then falls back to a full
    _run_pipeline reprocess, the proven path, rather than let a bug in
    this newer patch code strand a timestamp the full pipeline could
    still resolve.

    Every band actually touched by a successful patch call (won pixels or
    not -- either way the file's real satellite_key_<band> contributor
    list just changed) gets its gallery thumbnail regenerated from the
    real post-patch file before returning (``_regenerate_gallery_thumbnail``)
    -- unlike a full _run_pipeline reprocess, this path never
    goes through run_phase2_tier.py's own gallery_dir machinery at all,
    so without this the newly-patched-in satellite's pixels would never
    show up in the gallery, only in the .nc file itself.

    Split into two phases, added 2026-08-09 after real-world evidence
    (a large live partial-recheck backlog, up to 8 sequential fetches
    for a target needing both Himawari and INSAT) showed the original
    fully-sequential version was the dominant real cost here:

      Phase A -- fetch every satellite x band candidate CONCURRENTLY (up
      to _MAX_CONCURRENT_PATCH_FETCHES at a time). Safe to parallelize
      fully: each _fetch_single_satellite_npz call writes to its own
      unique npz_path and touches nothing else shared. (A real gap this
      exposed, also fixed 2026-08-09: for satellites like INSAT where
      one real raw record covers every band, concurrent per-band fetches
      for the SAME satellite+target used to race to download the exact
      same raw file -- see insat.py's own _acquire_download_lock for the
      fix; that race is orthogonal to and doesn't affect the reasoning
      here, since it's the RAW download that raced, not this function's
      own npz_path writes, which were never shared to begin with.)

      Phase B -- apply the actual patches to nc_path SEQUENTIALLY, one
      at a time. NOT safe to parallelize: patch_tier_file_with_satellite
      does a full-file copy-with-overlay of nc_path to the SAME
      `.patch.tmp` path every time it's called (NetCDF dimensions can't
      be resized in place -- see that function's own docstring), each
      call reading whatever is CURRENTLY on disk. Two concurrent calls
      for the same target's different bands would both read the same
      starting state, both write their own complete copy to the same
      tmp path, and whichever wins the os.replace race would silently
      overwrite the other's patch -- a real lost-update bug, the same
      class of collision this project already fixed once
      for merge_tiled_band_files's own write step. Phase B stays
      sequential specifically to avoid reintroducing that here.

    Args:
        target: The mosaic target timestamp being patched.
        final_out_dir: Directory holding the already-published
            ``tiera_<ts>.nc`` file for `target`.
        gallery_dir: Gallery ROOT directory, used to regenerate any
            band's thumbnail that was actually touched by a patch.
        missing: Current ``{slot_name: {"satellite_key": str, "bands":
            [...]}}`` tracking dict for this target (see
            :func:`_compute_missing`).
        newly_available: ``(slot_name, satellite_key)`` pairs that just
            became available and should be attempted.

    Returns:
        Tuple of ``(all_succeeded, remaining_missing)``. `all_succeeded`
        is False if the published file doesn't exist yet, or any band's
        fetch+patch raised an unexpected exception (in which case
        `remaining_missing` is `missing` unchanged, and the caller should
        fall back to a full reprocess). Otherwise `remaining_missing` is
        `missing` with successfully-patched bands removed from each
        entry's ``"bands"`` list (and the whole slot removed once its
        list is empty).
    """
    nc_path = _final_path(final_out_dir, target)
    if not nc_path.exists():
        return False, missing

    remaining = {slot: {"satellite_key": info["satellite_key"], "bands": list(info["bands"])} for slot, info in missing.items()}
    touched_bands: set[str] = set()
    with tempfile.TemporaryDirectory() as tmp_dir:
        fetch_jobs = [
            (slot_name, satellite_key, band)
            for slot_name, satellite_key in newly_available
            for band in missing[slot_name]["bands"]
        ]
        npz_paths = {
            (slot_name, satellite_key, band): Path(tmp_dir) / f"{satellite_key}_{band.lower()}.npz"
            for slot_name, satellite_key, band in fetch_jobs
        }

        # Phase A: concurrent fetch -- see this function's own docstring
        # for why this half is safe to parallelize.
        fetched_ok: dict[tuple[str, str, str], bool] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_PATCH_FETCHES) as executor:
            future_to_job = {
                executor.submit(
                    _fetch_single_satellite_npz, target, band, slot_name, satellite_key, npz_paths[(slot_name, satellite_key, band)]
                ): (slot_name, satellite_key, band)
                for slot_name, satellite_key, band in fetch_jobs
            }
            for future in concurrent.futures.as_completed(future_to_job):
                job = future_to_job[future]
                fetched_ok[job] = future.result()

        # Phase B: sequential patch-write -- see this function's own
        # docstring for why this half must NOT be parallelized.
        for slot_name, satellite_key in newly_available:
            patched_bands: list[str] = []
            for band in missing[slot_name]["bands"]:
                if not fetched_ok.get((slot_name, satellite_key, band)):
                    continue
                npz_path = npz_paths[(slot_name, satellite_key, band)]
                try:
                    value, resolution_km, columns, meta = read_single_satellite_npz(npz_path)
                    won = patch_tier_file_with_satellite(nc_path, band, value, resolution_km, columns, meta)
                    patched_bands.append(band)
                    touched_bands.add(band)
                    print(f"    [{band}] {satellite_key}: patched in ({'won pixels' if won else 'no pixels won, recorded anyway'})")
                except Exception as exc:
                    print(f"    [{band}] {satellite_key}: patch failed ({exc!r}) -- falling back to full reprocess")
                    return False, missing
            if patched_bands:
                remaining_bands = [b for b in remaining[slot_name]["bands"] if b not in patched_bands]
                if remaining_bands:
                    remaining[slot_name]["bands"] = remaining_bands
                else:
                    remaining.pop(slot_name, None)

    for band in touched_bands:
        _regenerate_gallery_thumbnail(nc_path, gallery_dir, band, target)
    return True, remaining


def _finish_partial_entry(
    partial: dict, target_iso: str, target: dt.datetime, entry: dict, missing: dict,
    now: dt.datetime, catchup_wait_hours: float, final_out_dir: Path, dest_dir: Path,
) -> None:
    """Shared tail bookkeeping for one state["partial"] entry -- same
    three-way age/missing outcome (fully resolved / give up after
    --catchup-wait-hours / keep waiting) regardless of whether this entry
    had nothing new this tick (resolved immediately in
    ``_run_partial_recheck``'s phase 1) or just finished a patch/reprocess attempt (phase
    2, via a worker thread's now-resolved future, consumed by
    _consume_partial_futures). Only ever called from main()'s own thread
    -- see _consume_partial_futures's own docstring for why that
    matters.

    Args:
        partial: The full ``state["partial"]`` dict; `target_iso`'s entry
            is deleted from it if fully resolved or given up on.
        target_iso: ISO string key of this entry in `partial`.
        target: The parsed datetime form of `target_iso`.
        entry: This target's partial-tracking entry dict.
        missing: The freshly re-derived missing-satellites dict to record.
        now: The current tick's timestamp, used for age calculation.
        catchup_wait_hours: Maximum age before giving up on remaining
            missing satellites.
        final_out_dir: Directory holding the published tier file, passed
            through to raw-file cleanup.
        dest_dir: Raw-download directory, passed through to raw-file
            cleanup.

    Returns:
        None. Mutates `entry`/`partial` in place and prints a status line.
    """
    entry["missing"] = missing
    _cleanup_raw_files_for_target(target, final_out_dir, dest_dir)

    first_seen = dt.datetime.fromisoformat(entry["first_seen"])
    age_hours = (now - first_seen).total_seconds() / 3600.0

    if not missing:
        print(f"{target_iso}: all previously-missing satellites now present -- removing from partial")
        del partial[target_iso]
    elif age_hours >= catchup_wait_hours:
        still = ", ".join(f"{info['satellite_key']} ({slot}, bands={info['bands']})" for slot, info in missing.items())
        print(
            f"{target_iso}: still missing {still} after {age_hours / 24:.1f} days -- "
            "giving up, the already-published version stays final."
        )
        del partial[target_iso]
    else:
        still = ", ".join(f"{info['satellite_key']} ({slot}, bands={info['bands']})" for slot, info in missing.items())
        print(f"{target_iso}: still missing {still} (age {age_hours / 24:.1f}d) -- keep waiting")


def _resolve_one(
    target: dt.datetime, missing: dict, newly_available: list[tuple[str, str]],
    final_out_dir: Path, gallery_dir: Path, memory_budget_gb: float,
) -> dict:
    """Runs entirely inside a worker thread -- reads/returns plain values
    only, never touches `partial` itself (see ``_MAX_CONCURRENT_PIPELINE_RUNS``'s
    own docstring, the 2026-08-09 "shared executor" section, for
    why). Standalone (not nested inside _run_partial_recheck) since that
    same change -- it's now submitted to main()'s own shared executor
    from _run_partial_recheck rather than one this function's own nested
    scope used to own.

    Args:
        target: The mosaic target timestamp being resolved.
        missing: Current missing-satellites tracking dict for this target.
        newly_available: ``(slot_name, satellite_key)`` pairs that just
            became available.
        final_out_dir: Directory holding the published tier file.
        gallery_dir: Gallery ROOT directory.
        memory_budget_gb: Passed through to a full reprocess, if needed.

    Returns:
        The re-derived missing-satellites dict after either a successful
        patch or a full reprocess (or `missing` unchanged if both failed).
    """
    patched_ok, remaining = _patch_newly_available(target, final_out_dir, gallery_dir, missing, newly_available)
    if patched_ok:
        return remaining

    target_iso = target.isoformat()
    print(f"  {target_iso}: patch attempt failed or was incomplete -- falling back to full reprocess")
    success = _run_pipeline(target, final_out_dir, gallery_dir, memory_budget_gb)
    if not success:
        print(f"  ERROR: reprocess failed for {target_iso}. Keeping remaining missing satellites tracked, will retry next tick.")
        return missing

    nc_path = _final_path(final_out_dir, target)
    if not nc_path.exists():
        return missing
    still_missing_bands = _missing_bands(nc_path)
    if still_missing_bands:
        # Should never happen -- a full reprocess is supposed to
        # (re)build every band from scratch. If it still didn't,
        # something is wrong in run_phase2_tier.py itself, not
        # something this retry loop can fix by trying again with the
        # same inputs -- surface it loudly rather than silently keep
        # retrying forever.
        print(
            f"  WARNING: {target_iso}: full reprocess completed but {still_missing_bands} are STILL "
            "missing entirely -- this should not happen after a full reprocess; investigate "
            "run_phase2_tier.py. Keeping all previously-tracked satellites as missing."
        )
        return missing
    # Re-derive per-band, same as _compute_missing, but scoped to only the
    # bands THIS entry was originally tracking as missing (not
    # necessarily all 4 -- see _compute_missing's own docstring for why
    # band-level granularity matters here too: a full reprocess can hit
    # the exact same kind of per-band transient failure a patch attempt
    # can).
    band_contributors = _band_contributors(nc_path)
    still_missing: dict[str, dict] = {}
    for slot, info in missing.items():
        satellite_key = info["satellite_key"]
        remaining_bands = [b for b in info["bands"] if satellite_key not in band_contributors.get(b, set())]
        if remaining_bands:
            still_missing[slot] = {"satellite_key": satellite_key, "bands": remaining_bands}
    return still_missing


def _run_partial_recheck(
    partial: dict, now: dt.datetime, catchup_wait_hours: float,
    final_out_dir: Path, gallery_dir: Path, memory_budget_gb: float, dest_dir: Path,
    executor: concurrent.futures.ThreadPoolExecutor, save_callback: Callable[[], None],
) -> dict[concurrent.futures.Future, str]:
    """Rechecks every state["partial"] entry -- a timestamp that already
    published with one or more expected satellites missing from the actual
    written file (any satellite, not just INSAT -- see main()'s docstring
    point 3; a real live case, Himawari on 2026-08-07, showed a core
    satellite can end up missing too, via a real-time sync-tolerance
    mismatch, not just INSAT's chronic lag). No cadence/due-for-check
    gating here (unlike _update_pending_entry) -- by construction every
    entry here is already well past its missing satellites' own cadence+
    grace windows, so it's always worth a real check, every tick.

    Any newly-available missing satellite is first tried as an
    incremental PATCH (_patch_newly_available -- see
    ``mosaic_io.patch_tier_file_with_satellite``), which skips re-reprojecting every already-
    contributing satellite and is typically 5-10x cheaper than a full
    reprocess. Only if patching fails for any reason does this fall back
    to the proven full-reprocess path (run_phase2_tier.py writes its
    final .nc with mode "w", see mosaic_io.py's open_tiled_band_writer/
    merge_tiled_band_files, so re-running the same target just overwrites
    the previously-published file in place). GOES/Himawari/Meteosat/
    INSAT's own download() functions already skip re-fetching a file
    already on disk, so the shared raw cache (this script doesn't isolate
    it per-run) means a full reprocess only actually re-downloads
    whichever satellite is newly available -- but the reprojection/
    selection/blend compute itself has no equivalent cache, so it's the
    same ~20 min cost as the first run, not free (the whole reason the
    patch path exists).

    After either path, `missing` is re-derived from the FRESH file's real
    contributors (_actual_contributors) after a full reprocess, or from
    _patch_newly_available's own return value after a successful patch --
    not just assumed from whichever satellite the availability check
    found, since the same search-vs-real-fetch tolerance mismatch that
    put a satellite in `missing` in the first place could recur on this
    attempt too.

    _cleanup_raw_files_for_target now runs unconditionally, every tick,
    for every entry here -- NOT only once `missing` is fully empty (real
    gap fixed 2026-08-08). It only ever deletes a satellite's raw file
    once that satellite reads back as a real contributor in the file, so
    this is safe regardless of what else is still missing; the point is
    that "already a contributor" no longer has to wait on "the WHOLE
    target is done" before its now-unneeded raw file goes away, which
    could otherwise mean days of an unused file sitting around for a
    target stuck waiting on one slow satellite.

    Like main()'s own pending-timestamp loop, this is split into a cheap
    sequential phase 1 (the search-only newly_available check per entry)
    and a phase 2 that submits the actual patch-or-reprocess work for
    whichever entries have something newly available. An entry with
    nothing newly available this tick has no expensive work to do at
    all, so it's resolved immediately in phase 1 via ``_finish_partial_entry``
    rather than being routed through the executor for nothing.

    Submits phase 2's work to the CALLER-owned `executor` -- shared with
    main()'s own due-timestamp processing since 2026-08-09, not a
    separate pool -- and returns the {future: target_iso} dict for the
    caller to consume via its own as_completed loop
    (_consume_partial_futures), rather than consuming them here itself.
    See _MAX_CONCURRENT_PIPELINE_RUNS's own docstring for the full
    reasoning: due-timestamp and partial-recheck work used to run fully
    sequentially within one tick (this function only even STARTED once
    main()'s own due-timestamp ThreadPoolExecutor `with` block had fully
    finished and exited), adding real wall-clock time even though
    partial-recheck's lighter patch work could safely have overlapped
    with a long due-timestamp phase instead of queueing behind it.

    `save_callback` persists state["partial"] (via the caller's own
    _save_state) immediately after each phase-1 immediate resolution --
    phase 2's as-completed ones are saved by _consume_partial_futures,
    the same reasoning as before this split: never once after the whole
    tick finishes. See main()'s own docstring for the real incident
    (2026-08-08) this prevents: hours of already-disk-verified patch work
    being invisible to the state file because the tick doing it never
    reached its own end.

    Args:
        partial: The full ``state["partial"]`` dict of tracked entries.
        now: The current tick's timestamp.
        catchup_wait_hours: Maximum age before giving up on a still-missing
            entry (passed through to :func:`_finish_partial_entry`).
        final_out_dir: Directory holding published tier files.
        gallery_dir: Gallery ROOT directory.
        memory_budget_gb: Passed through to a full reprocess, if needed.
        dest_dir: Raw-download directory, passed through to raw-file cleanup.
        executor: Caller-owned thread pool to submit phase-2 work to
            (shared with main()'s own due-timestamp processing).
        save_callback: Called after each phase-1 immediate resolution to
            persist state right away.

    Returns:
        Mapping of ``{future: target_iso}`` for every entry submitted to
        `executor`, for the caller to consume via
        :func:`_consume_partial_futures`.
    """
    futures: dict[concurrent.futures.Future, str] = {}
    for target_iso in sorted(partial):
        target = dt.datetime.fromisoformat(target_iso)
        entry = partial[target_iso]
        missing: dict = entry["missing"]

        newly_available = [
            (slot_name, info["satellite_key"])
            for slot_name, info in missing.items()
            if _check_satellite_available(slot_name, info["satellite_key"], target)
        ]

        if newly_available:
            names = ", ".join(f"{key} ({slot})" for slot, key in newly_available)
            print(f"{target_iso}: {names} now available -- will patch in")
            missing_snapshot = dict(missing)
            future = executor.submit(
                _resolve_one, target, missing_snapshot, newly_available, final_out_dir, gallery_dir, memory_budget_gb
            )
            futures[future] = target_iso
        else:
            _finish_partial_entry(partial, target_iso, target, entry, missing, now, catchup_wait_hours, final_out_dir, dest_dir)
            save_callback()

    if futures:
        print(f"patching/reprocessing {len(futures)} partial timestamp(s), sharing main()'s own due-timestamp executor:")
    return futures


def _consume_partial_futures(
    futures: dict[concurrent.futures.Future, str], partial: dict, now: dt.datetime, catchup_wait_hours: float,
    final_out_dir: Path, dest_dir: Path, save_callback: Callable[[], None],
) -> None:
    """Completion-handling half of _run_partial_recheck's old phase 2 --
    split out so main() can submit due-timestamp AND partial-recheck work
    to the SAME shared executor before waiting on either (see
    _MAX_CONCURRENT_PIPELINE_RUNS's own docstring), then consume each
    side's results separately. Still only ever called from main()'s own
    thread, same single-writer invariant _finish_partial_entry's own
    docstring already documents -- this and main()'s own due-timestamp
    as_completed loop run one after another in that one thread, never
    concurrently with each other, so no lock is needed between them; only
    the underlying WORK (submitted earlier, to the shared executor) ever
    actually overlaps in wall-clock time.

    Args:
        futures: ``{future: target_iso}`` mapping returned by
            :func:`_run_partial_recheck`.
        partial: The full ``state["partial"]`` dict, mutated as each
            future resolves.
        now: The current tick's timestamp.
        catchup_wait_hours: Maximum age before giving up, passed through
            to :func:`_finish_partial_entry`.
        final_out_dir: Directory holding published tier files.
        dest_dir: Raw-download directory, passed through to raw-file cleanup.
        save_callback: Called after each future resolves to persist state.

    Returns:
        None. Mutates `partial` in place as a side effect.
    """
    for future in concurrent.futures.as_completed(futures):
        target_iso = futures[future]
        target = dt.datetime.fromisoformat(target_iso)
        entry = partial[target_iso]
        missing = future.result()
        _finish_partial_entry(partial, target_iso, target, entry, missing, now, catchup_wait_hours, final_out_dir, dest_dir)
        save_callback()


def main() -> None:
    """CLI entry point: parse arguments, acquire the single-instance lock, and run one tick.

    See the module docstring for what one tick does. Holds
    ``_acquire_single_instance_lock`` for the whole tick (including every
    subprocess it spawns) so at most one scheduler instance ever runs at
    once (see that function's own docstring for the real 2026-08-08
    incident this prevents).

    Returns:
        None. Exits with status 1 immediately (no work done) if another
        instance already holds the lock.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--final-out-dir", type=Path, default=Path("output/tiera"), help="passed through to run_phase2_tier.py")
    parser.add_argument("--gallery-dir", type=Path, default=Path("output/gallery"), help="passed through to run_phase2_tier.py")
    parser.add_argument("--memory-budget-gb", type=float, default=4.0, help="passed through to run_phase2_tier.py")
    parser.add_argument(
        "--lookback-margin-minutes", type=float, default=20.0,
        help="a fixed slot only becomes a candidate once this many minutes have passed since it -- gives "
        "satellites a head start before the first check, so an immediately-complete run doesn't have to "
        "wait through several ticks for satellites that would have been ready anyway.",
    )
    parser.add_argument(
        "--max-wait-hours", type=float, default=24.0,
        help="force-process once this long has passed, even if a non-INSAT satellite is still missing "
        "(INSAT itself never blocks or counts toward this -- see --catchup-wait-hours instead).",
    )
    parser.add_argument(
        "--catchup-wait-hours", type=float, default=168.0,
        help="how long (default 168 = 7 days, matched to the real ~8-day MOSDAC outage observed "
        "2026-07-24..08-03) to keep rechecking ANY satellite missing from an already-published file "
        "(not just INSAT -- see main()'s docstring point 3) and reprocessing if it shows up. After "
        "this, the version with that satellite missing stays final.",
    )
    parser.add_argument(
        "--retention-keep", type=int, default=_DEFAULT_RETENTION_KEEP,
        help="rolling-window size passed to find_expired -- same meaning as retain_rolling_window.py's own "
        "--keep (default 672 = 4/hour x 24h x 7 days). Applied to the tiera .nc dir and each of the 4 "
        "gallery band subdirectories independently, every tick.",
    )
    parser.add_argument(
        "--dest-dir", type=Path, default=_DEFAULT_DEST_DIR,
        help="shared raw satellite-download cache (same meaning/default as run_phase2_tier.py's own DEST_DIR, "
        "including the GLOBAL_MOSAIC_DEST_DIR env var override) -- used only to clean up a timestamp's raw "
        "source files once it's fully done needing them (see _cleanup_raw_files_for_target). Real incident "
        "this exists to prevent recurring, 2026-08-08: this directory had no cleanup at all, grew unbounded, "
        "and filled the G: drive completely, which corrupted the state file and took the scheduler down "
        "for ~3 hours.",
    )
    parser.add_argument("--state-file", type=Path, default=_DEFAULT_STATE_PATH)
    args = parser.parse_args()

    # Single-instance lock, held for the whole tick -- see
    # _acquire_single_instance_lock's own docstring for the real 2026-08-08
    # incident this prevents. Keyed off --state-file (not a fixed path) so
    # a test/dev run using a scratch --state-file gets its own lock, never
    # colliding with a real production run's.
    lock_path = args.state_file.with_suffix(args.state_file.suffix + ".lock")
    lock_handle = _acquire_single_instance_lock(lock_path)
    if lock_handle is None:
        print(
            f"ERROR: another schedule_pipeline.py instance already holds the lock at {lock_path} -- "
            f"exiting immediately rather than risk two processes racing on {args.state_file}."
        )
        sys.exit(1)
    try:
        _run_tick(args)
    finally:
        _release_single_instance_lock(lock_handle)


def _run_tick(args: argparse.Namespace) -> None:
    """Runs the actual scheduler tick body, once the single-instance lock is already held.

    Implements the 4 steps described in the module docstring: backfills
    missed fixed-slot candidates, checks/processes due pending
    timestamps (up to `_MAX_CONCURRENT_PIPELINE_RUNS` at a time),
    rechecks/patches partial (previously-incomplete) timestamps, and
    runs rolling-window retention -- saving state after every individual
    timestamp resolves, not just once at the end (see module docstring
    for the real 2026-08-08 incident this design avoids).

    Args:
        args: Parsed CLI arguments from :func:`main`'s argument parser.

    Returns:
        None. Mutates and persists the on-disk state file as a side effect.
    """
    # Naive UTC, matching every other datetime in this project (target
    # timestamps throughout config.py/meteosat.py/etc. are all naive) --
    # dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) avoids the
    # deprecated utcnow() while staying naive, rather than introducing an
    # aware datetime that would fail comparisons against the naive ones
    # everywhere else.
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    _cleanup_stale_lock_files(args.final_out_dir, args.dest_dir, now)
    state = _load_state(args.state_file)
    pending: dict = state.setdefault("pending", {})
    partial: dict = state.setdefault("partial", {})

    def _save_now() -> None:
        """Persists the full state dict right now (atomic, see _save_state)
        -- called after EVERY individual timestamp resolves throughout this
        function, not just once at the end. Real incident this prevents,
        2026-08-08: see module docstring -- an interrupted tick previously
        lost all of its progress, even work already fully written to disk,
        because _save_state was only ever called once, at the very end."""
        _save_state(args.state_file, state)

    _add_missed_candidates(state, pending, now, args.lookback_margin_minutes)
    _save_now()

    # Phase 1: cheap check only (search-only availability, no download) --
    # sequential, fast, decides which timestamps are actually due. Kept
    # separate from phase 2 below so the expensive part (real pipeline
    # runs) is the only part that gets parallelized -- see
    # _MAX_CONCURRENT_PIPELINE_RUNS's own docstring for why.
    due: list[tuple[str, dt.datetime, dict]] = []
    for target_iso in sorted(pending):
        target = dt.datetime.fromisoformat(target_iso)
        entry = pending[target_iso]
        first_seen = dt.datetime.fromisoformat(entry["first_seen"])
        age_hours = (now - first_seen).total_seconds() / 3600.0
        expected = _expected_satellites(target)
        # INSAT is checked (contributes to `entry["found"]` if available) but
        # never required -- core_expected is everything else, see main()'s
        # docstring point 3.
        core_expected = {slot: sat for slot, sat in expected.items() if slot != "insat"}

        print(f"{target_iso} (age {age_hours:.1f}h):")
        entry = _update_pending_entry(entry, target, expected, now)
        pending[target_iso] = entry
        _save_now()

        core_complete = set(entry["found"]) >= set(core_expected.values())
        forced = age_hours >= args.max_wait_hours

        if not core_complete and not forced:
            print(f"  {len(entry['found'])}/{len(expected)} satellites present (core not yet complete) -- waiting")
            continue

        reason = "core coverage complete" if core_complete else f"forced after {age_hours:.1f}h"
        print(f"  due for processing ({reason})")
        due.append((target_iso, target, expected))

    # Phase 2: the expensive part -- up to _MAX_CONCURRENT_PIPELINE_RUNS
    # real pipeline runs at once (each its own run_phase2_tier.py
    # subprocess tree, via a worker thread blocked on subprocess.run --
    # genuine OS-level parallelism, the GIL is released for the duration).
    # Partial-recheck's own phase-2 work (patch/reprocess for `partial`
    # entries) shares this SAME executor as of 2026-08-09 -- see
    # _MAX_CONCURRENT_PIPELINE_RUNS's own docstring for the full
    # reasoning -- submitted below via _run_partial_recheck BEFORE either
    # side's completions are consumed, so the real underlying work for
    # both due-timestamp and partial-recheck processing actually overlaps
    # in wall-clock time instead of running fully sequentially.
    # State-file bookkeeping (`partial`/`del pending`/raw-cache cleanup)
    # is only ever done here, in this single thread, once a given run's
    # future has actually resolved -- never inside a worker thread --
    # so schedule_pipeline_state.json still only ever has one real writer
    # per tick, same as before this change.
    if due:
        print(f"processing {len(due)} due timestamp(s), up to {_MAX_CONCURRENT_PIPELINE_RUNS} at a time:")
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_PIPELINE_RUNS) as executor:
        due_futures = {
            executor.submit(_run_pipeline, target, args.final_out_dir, args.gallery_dir, args.memory_budget_gb): (
                target_iso,
                target,
                expected,
            )
            for target_iso, target, expected in due
        }
        partial_futures = _run_partial_recheck(
            partial, now, args.catchup_wait_hours, args.final_out_dir, args.gallery_dir, args.memory_budget_gb, args.dest_dir,
            executor, save_callback=_save_now,
        )

        for future in concurrent.futures.as_completed(due_futures):
            target_iso, target, expected = due_futures[future]
            success = future.result()
            if not success:
                print(
                    f"  ERROR: pipeline run failed for {target_iso}. Removing from pending anyway "
                    "(would otherwise retry forever) -- investigate manually."
                )
            del pending[target_iso]

            if success:
                # Ground truth for "what actually made it into the file" --
                # NOT entry["found"] (that's a coarser, wider-tolerance search-
                # only check that can disagree with the per-band fetch's
                # tighter real-time sync tolerance -- happened on 2026-08-07
                # with Himawari, a core satellite the search check said was
                # "found" but which the actual fetch rejected as too stale).
                # Covers ANY missing satellite this way, not just INSAT --
                # and (see _compute_missing/_missing_bands, added
                # 2026-08-08 after hitting this case in production) an
                # entire band missing structurally, which plain
                # per-satellite contributor checks alone can silently miss.
                nc_path = _final_path(args.final_out_dir, target)
                missing = _compute_missing(nc_path, expected)
                if missing:
                    partial[target_iso] = {"first_seen": now.isoformat(), "missing": missing}
                    names = ", ".join(f"{info['satellite_key']} ({slot}, bands={info['bands']})" for slot, info in missing.items())
                    print(
                        f"  {target_iso}: tracking in partial list -- missing {names}, will reprocess if any "
                        f"show up within {args.catchup_wait_hours / 24:.0f} days"
                    )
                    # The satellites that DID make it in already don't need
                    # their raw files kept around just because this target is
                    # partial -- the incremental patch path (_patch_newly_
                    # available) only ever re-fetches the STILL-MISSING
                    # satellite later, never re-touches these. Real gap this
                    # closes, 2026-08-08: without this, a target waiting on
                    # one chronically-slow satellite (INSAT) left every OTHER
                    # already-consumed satellite's raw file sitting unused
                    # for up to --catchup-wait-hours (7 days default) for no
                    # reason. Safe to call here: _cleanup_raw_files_for_target
                    # only ever deletes a satellite's raw file once it reads
                    # back as a real CURRENT contributor in the file -- a
                    # still-missing satellite was never downloaded, so there
                    # is nothing of its to accidentally delete.
                    _cleanup_raw_files_for_target(target, args.final_out_dir, args.dest_dir)
                else:
                    # Fully complete on the first pass -- nothing left to ever
                    # reprocess for, so its raw source files can go now rather
                    # than waiting on a partial-tracking cleanup that will
                    # never happen for this target (see
                    # _cleanup_raw_files_for_target).
                    print(f"  {target_iso}: complete, all expected satellites present")
                    _cleanup_raw_files_for_target(target, args.final_out_dir, args.dest_dir)

            _save_now()

        _consume_partial_futures(
            partial_futures, partial, now, args.catchup_wait_hours, args.final_out_dir, args.dest_dir,
            save_callback=_save_now,
        )

    _run_retention(args.final_out_dir, args.gallery_dir, args.retention_keep, args.dest_dir)
    _save_now()


if __name__ == "__main__":
    main()
