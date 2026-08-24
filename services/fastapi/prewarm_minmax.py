#!/usr/bin/env python3
"""Pre-warms geosat-mosaic-api's /minmax cache for newly landed mosaic
files, so a live request from the frontend is a cache hit almost
always instead of occasionally racing a multi-second cold compute
(addressing the concurrency bug's "new request" scenario, fixed
2026-08-15). Mirrors services/xcube/prewarm_cache.py's shape -- same idea,
different service.

Run periodically (systemd timer, prewarm-minmax-cache.timer). Idempotent:
tracks which files it has already warmed in SEEN_FILE, only processes new
ones each run. Also prunes the live service's cache for files that have
left the rolling retention window, via /_internal/prune-cache -- runs
every invocation, not just when new files exist, since files can leave
the window without a new one landing in the same cycle.

Deliberately goes through the actual HTTP endpoint, not a direct netCDF4
read -- this must populate the exact same in-memory cache
(_minmax_cache) live requests check, not a separate cache of its own.
"""

import concurrent.futures
import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("/srv/mosaic-data/tiera")
API_BASE = "http://localhost:8001"
# Shared secret for /_internal/prune-cache -- see that route's own
# docstring in main.py for why this replaced an IP-based check.
INTERNAL_SECRET = Path("/opt/geosat-mosaic-api/.internal-secret").read_text().strip()
SEEN_FILE = Path("/opt/geosat-mosaic-api/.prewarm_minmax_seen")
LOG_FILE = Path("/opt/geosat-mosaic-api/prewarm_minmax.log")

BANDS = ["mir", "wv", "tir1", "tir2"]

# Gentle on purpose -- these are the same 4 process-pool workers live
# requests use (main.py's _process_pool), and each minmax computation is
# a ~2-5s full-array read, not a cheap tile render. 2 concurrent
# keeps a full cold backfill from starving live traffic -- explicitly
# 1-2 at a time instead of hammering all 4 workers, per the fix for the
# concurrency bug.
_WARM_CONCURRENCY = 2

_FILENAME_RE = re.compile(r"tiera_(\d{8})T(\d{6})\.nc$")


def log(msg: str) -> None:
    """Writes a timestamped line to both stdout and `LOG_FILE`.

    Args:
        msg: Message text to log (no trailing newline needed).
    """
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_seen() -> set[str]:
    """Loads the previously-recorded seen-files state from `SEEN_FILE`.

    Returns:
        Set of filenames already warmed in a previous run. Empty set if
        `SEEN_FILE` doesn't exist yet.
    """
    if not SEEN_FILE.exists():
        return set()
    return set(SEEN_FILE.read_text().splitlines())


def save_seen(seen: set[str]) -> None:
    """Persists the seen-files state to `SEEN_FILE`, one filename per
    line, sorted.

    Args:
        seen: Set of filenames to write out.
    """
    SEEN_FILE.write_text("\n".join(sorted(seen)) + "\n")


def iso_for_filename(filename: str) -> str | None:
    """Parses a `tiera_<YYYYMMDD>T<HHMMSS>.nc` filename into the ISO
    timestamp string `/minmax` expects.

    Args:
        filename: A mosaic filename, e.g. `"tiera_20260811T042508.nc"`.

    Returns:
        ISO 8601 timestamp string (e.g.
        `"2026-08-11T04:25:08.000Z"`), or None if `filename` doesn't
        match the expected pattern.
    """
    match = _FILENAME_RE.match(filename)
    if not match:
        return None
    dt = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def warm_one(args: tuple[str, str]) -> tuple[str, str, bool]:
    """Fetches one `/minmax` URL for a single (filename, band) pair, to
    populate the live service's in-memory cache.

    Args:
        args: `(filename, band)` tuple -- packed into a single argument
            since this is submitted via `ThreadPoolExecutor.submit`/
            tracked by a `{future: job}` dict keyed on the tuple.

    Returns:
        `(filename, band, success)` tuple, where `success` is True only
        on an HTTP 200 response; any exception (timeout, connection
        error, unparseable filename) is treated as a failure rather than
        propagated.
    """
    filename, band = args
    iso = iso_for_filename(filename)
    if iso is None:
        return filename, band, False
    url = f"{API_BASE}/minmax?band={band}&time={iso}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return filename, band, resp.status == 200
    except urllib.error.HTTPError:
        # A 4xx/5xx from the API itself -- log-worthy but not fatal
        # to the run, same "skip, don't mark seen, retry next time"
        # philosophy as the xcube prewarmer's dataset-not-visible-yet case.
        return filename, band, False
    except Exception:
        return filename, band, False


def prune_stale_entries(on_disk: set[str]) -> None:
    """Calls geosat-mosaic-api's `/_internal/prune-cache` to drop cache
    entries for files no longer on disk (left the rolling retention
    window).

    Args:
        on_disk: Current set of on-disk mosaic filenames -- passed as-is
            as the request's `valid_filenames`.
    """
    body = json.dumps({"valid_filenames": sorted(on_disk)}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/_internal/prune-cache",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Secret": INTERNAL_SECRET,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.load(resp)
        if result.get("removed", 0) > 0:
            log(f"PRUNED {result['removed']} stale cache entries")
    except Exception as exc:
        log(f"PRUNE FAILED: {exc}")


def main() -> None:
    """Entry point: one pre-warm cycle.

    Diffs the on-disk mosaic files against the previously-recorded
    `seen` state to find new files, warms `/minmax` for each new
    (filename, band) pair (throttled to `_WARM_CONCURRENCY`, saving
    `seen` incrementally as each file's bands finish), then drops
    `seen` entries for files that have left the rolling retention
    window and prunes the live service's cache to match, every run.
    """
    seen = load_seen()
    on_disk = {p.name for p in DATA_DIR.glob("*.nc")}
    new_files = sorted(on_disk - seen)

    if new_files:
        jobs = [(filename, band) for filename in new_files for band in BANDS]
        completed: dict[str, int] = {f: 0 for f in new_files}
        succeeded: dict[str, int] = {f: 0 for f in new_files}

        # submit()+as_completed(), not map() -- map()'s iterator yields
        # results in job-submission order, so with a mix of fast/slow
        # bands one slow band anywhere early in the list would hold back
        # every later result from being visible even after it finished.
        # as_completed() surfaces each result the moment it lands, which
        # matters here specifically so WARMED lines (and the seen-set
        # save) happen incrementally per file, not only after the entire
        # ~213-file backfill finishes -- both for progress
        # visibility (an apparently
        # "stalled" first run turned out to be working fine, just
        # invisible) and so a killed/interrupted run doesn't lose
        # already-completed work.
        with concurrent.futures.ThreadPoolExecutor(max_workers=_WARM_CONCURRENCY) as pool:
            futures = {pool.submit(warm_one, job): job for job in jobs}
            for future in concurrent.futures.as_completed(futures):
                filename, band, ok = future.result()
                completed[filename] += 1
                if ok:
                    succeeded[filename] += 1
                if completed[filename] == len(BANDS):
                    log(f"WARMED {filename}: {succeeded[filename]}/{len(BANDS)} bands cached")
                    seen.add(filename)
                    save_seen(seen)

    # Every run, not just when new files exist -- a file can leave the
    # rolling window without a new one landing in the same 10-minute cycle.
    stale_seen = seen - on_disk
    if stale_seen:
        seen -= stale_seen
        save_seen(seen)
    prune_stale_entries(on_disk)


if __name__ == "__main__":
    main()
