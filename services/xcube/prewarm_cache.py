#!/usr/bin/env python3
"""Pre-warms nginx's xcube tile cache (see xcube-cache.conf) for newly
landed mosaic files, so the first real user request after ingestion is
already a cache hit instead of a CPU-bound render.

Run periodically (systemd timer, prewarm-tile-cache.timer). Idempotent:
tracks which files it has already warmed (and each file's last-seen
mtime) in SEEN_FILE, and any specific tile URLs still missing (not yet
confirmed cached, e.g. from a timeout under load) in PENDING_FILE -- see
load_pending()'s docstring for why the latter exists separately from
just "warm everything again."

Cache lifetime, reworked 2026-08-16 to cover the full 7-day retention
window (max_size=30g, inactive=7d in xcube-cache.conf; proxy_cache_valid
200 7d in xcube-proxy.conf) instead of the original 2g/60m -- at that
scale, relying on nginx's own inactive-timeout to eventually age out
stale entries isn't good enough on its own: a file that gets reprocessed
mid-window (patch_tier_file_with_satellite revising a still-`partial`
file in place, same filename, new content) would otherwise keep serving
its now-wrong cached tiles for up to 7 days. This script now explicitly
detects that (by tracking each file's mtime, not just its existence) and
a file leaving the rolling window entirely, and purges the matching
cache entries directly in both cases -- see purge_cache_for()'s own
docstring for how, since stock nginx (no cache_purge module/nginx-plus)
has no built-in way to invalidate specific cache keys.

Query params, reworked 2026-08-16 -- this used to warm bare
`/tiles/{ds}/{var}/{z}/{y}/{x}` URLs with no query string at all. That
was a genuine bug, not just an inefficiency: nginx's cache key is the
literal, unmodified request_uri, and frontend traffic *always*
sends `crs`/`format`/`cmap`/`vmin`/`vmax`/`norm` as part of the URL
(per actual non-localhost entries in
/var/log/nginx/access.log) -- so every tile this script ever warmed was
a cache key frontend traffic could never hit. A tile
users had requested 22 times was still a `MISS` when fetched with
realistic params, while the bare URL was a `HIT`.

Fixed with an evidence-based param scheme per variable type (not
guessed) -- see VALUE_CMAP and SATELLITE_ID_PARAMS below for exactly
what's confirmed vs. still unverified. `value_<band>` needs its
`vmin`/`vmax` fetched at warm-time (from geosat-mosaic-api's /minmax, the same
endpoint the frontend itself uses) since those come from each file's
computed data range, not a fixed constant.
"""

import concurrent.futures
import json
import re
import subprocess
import sys
import urllib.request
import ssl
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("/srv/mosaic-data/tiera")
PROXY_BASE = "https://localhost:8443"
METADATA_API_BASE = "http://localhost:8001"
STORE_ID = "mosaics"
SEEN_FILE = Path("/opt/xcube-deploy/.prewarm_seen")
PENDING_FILE = Path("/opt/xcube-deploy/.prewarm_pending")
LOG_FILE = Path("/opt/xcube-deploy/prewarm_cache.log")
CACHE_DIR = Path("/var/cache/nginx/xcube_tiles")

_FILENAME_RE = re.compile(r"tiera_(\d{8})T(\d{6})\.nc$")

# Query-string schemes observed per variable type, from actual
# (non-localhost) requests in nginx's access log as of 2026-08-16, not
# guessed. nginx's cache key is the literal request_uri, so both
# param VALUES and ORDER matter for a byte-identical cache hit -- these
# are built to match traffic exactly, `crs, format, cmap, vmin,
# vmax, norm` in that order.
#
# value_<band>: continuous range, vmin/vmax genuinely vary per file (its
# own computed data range) -- fetched per file below, not
# fixed. cmap=Greys is the only colormap seen in every sample
# checked so far; it's presumably the frontend's default, but this
# couldn't be confirmed from source (this Windows-side copy of
# geosat-mosaic is stale and doesn't even contain the xcube-tile code
# path RHEL actually runs). A user selecting a different palette (if
# that control still exists in the frontend) would still miss this
# cache -- known, accepted gap, not fixable without RHEL-side source
# access.
VALUE_CMAP = "Greys"

# satellite_id_<band>: categorical (which of the 6 possible contributing
# satellites), fixed 0-6 flag-value range regardless of file/timestamp --
# identical params across multiple requests spanning different files.
SATELLITE_ID_PARAMS = "crs=CRS84&format=png&cmap=tab10&vmin=0&vmax=6&norm=cat"

# pixel_resolution_km_<band>: traffic has never requested this
# variable (zero non-localhost hits in the access log) -- no known
# params to match, so this is left warming the old bare
# (parameter-less) URL. Harmless either way since nothing hits it
# yet, but this is a known, open gap -- fix once actual traffic (or
# RHEL-side source) reveals what params it actually needs, don't guess.
PIXEL_RESOLUTION_HAS_NO_KNOWN_PARAMS = True

# mtimes are only ever compared to a previously-recorded value from this
# same script (never to wall-clock "now"), so a small tolerance just
# absorbs filesystem mtime-precision noise -- not a freshness window.
MTIME_TOLERANCE_SECONDS = 1.0

# Two separate concurrency knobs, added 2026-08-16 after a full-catalog
# re-warm (213 files, needed once to apply the params fix retroactively
# to every already-"seen" file) turned out to be fully sequential end to
# end, making that backfill impractically slow. First attempt just wrapped
# each *entire file* (minmax fetches + all 60 tile fetches) in a
# FILE_CONCURRENCY=4 pool on top of the existing 6-worker per-file
# tile-fetch pool -- tested against 8 files and made things
# *worse*, not better: up to 24 concurrent requests landing on xcube at
# once, but xcube's own request handling is effectively single-threaded
# (one python3 process pegged at 100% CPU, not spread
# across cores, while `/datasets` and tile requests both stalled behind
# it) -- so extra client-side concurrency against xcube doesn't add
# throughput, it just makes requests queue longer than fetch()'s 15s
# timeout tolerates. Success rate collapsed (30/60,
# 10/60, then 0/60 on later files in the same batch) and wall-clock for 8
# files was *slower* (5m25s) than the earlier, fully-sequential single-file
# baseline (44s/file).
#
# Reworked into two stages with independent concurrency instead:
#   - MINMAX_CONCURRENCY: fetching each file's 4 band ranges hits
#     geosat-mosaic-api, a *different* service with its own
#     concurrency (ProcessPoolExecutor + coalescing cache, see its
#     main.py) -- safe to run many files' minmax stage in parallel.
#   - TILE_FETCH_CONCURRENCY: kept at exactly 6, the number already
#     evidence-proven safe against xcube's serial request handling (the
#     original single-file-at-a-time behavior consistently got 53-60/60
#     tiles cached). Every file's tile fetches now share ONE pool of this
#     size instead of each file getting its own -- so total concurrent
#     load against xcube never exceeds 6 no matter how many files are
#     mid-flight, while still overlapping file N+1's minmax stage with
#     file N's tiles still draining through that shared pool.
MINMAX_CONCURRENCY = 4
TILE_FETCH_CONCURRENCY = 6

# The 3 rendered-as-map-layer variable types x 4 bands this deployment
# actually serves to the frontend (via ncdump + prior testing -- the
# other per-band fields like is_blended_*/blend_weight_winner_*
# exist in the files but aren't fetched as tiles by either frontend app).
BANDS = ["mir", "wv", "tir1", "tir2"]
VAR_TYPES = ["value", "satellite_id", "pixel_resolution_km"]
VARIABLES = [f"{t}_{b}" for b in BANDS for t in VAR_TYPES]

# Low zoom levels only -- these are the cheap, coarse "full-globe" tiles a
# user sees immediately on load/zoom-out, not the full pyramid.
#
# Cut from [0, 1, 2] to [0, 1] on 2026-08-16, after root-causing xcube's
# long-uptime memory growth: growth is
# driven by the number of distinct (dataset, zoom-level) pairs ever
# requested, not tile count within a level (~23MB per
# *new zoom level* touched on a dataset, regardless of how many
# individual tiles or variables are fetched at that level, since they
# all share the same cached downsampled array). This prewarmer forcing
# z=0-2 on every newly-landed file was a measured contributor to
# that growth -- dropping z=2 (16 of the 21 tiles/variable this loop
# used to fetch, but only 1 of the 3 *distinct levels* driving the
# cost) cuts this job's own memory contribution by roughly a third.
# z=0/z=1 are kept since they're the "immediately visible on load" tiles
# the reasoning above is about; z=2 was the least essential to
# guarantee-warm. Revisit (push back to including z=2, or beyond) only
# if the root cause of the growth gets fixed, or if z=2
# cold-load latency turns out to bother users more than this
# memory tradeoff is worth.
ZOOM_LEVELS = [0, 1]

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def log(msg: str) -> None:
    """Writes a timestamped line to both stdout and `LOG_FILE`.

    Args:
        msg: Message text to log (no trailing newline needed).
    """
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_seen() -> dict[str, float]:
    """Loads the previously-recorded seen-files state from `SEEN_FILE`.

    Returns:
        Mapping of filename -> mtime, as of the last run that saw this
        file. Empty dict if `SEEN_FILE` doesn't exist yet.
    """
    if not SEEN_FILE.exists():
        return {}
    seen = {}
    for line in SEEN_FILE.read_text().splitlines():
        if not line.strip():
            continue
        filename, mtime_str = line.rsplit("\t", 1)
        seen[filename] = float(mtime_str)
    return seen


def save_seen(seen: dict[str, float]) -> None:
    """Persists the seen-files state to `SEEN_FILE`, one `filename<TAB>mtime`
    line per entry, sorted by filename.

    Args:
        seen: Mapping of filename -> mtime to write out.
    """
    lines = [f"{filename}\t{mtime}" for filename, mtime in sorted(seen.items())]
    SEEN_FILE.write_text("\n".join(lines) + ("\n" if lines else ""))


def load_pending() -> dict[str, list[str]]:
    """filename -> exact list of tile URLs still not confirmed cached, as
    of the last run that attempted them. Added 2026-08-17 after two
    full-catalog backfill passes each left a large minority of files
    (119, then 75 of 213) with a handful of missing tiles apiece --
    root cause is xcube's own memory-pressure-driven slowdown under
    sustained load (see TILE_FETCH_CONCURRENCY's comment), not anything
    fixable by re-tuning concurrency further. The old design only ever
    recorded an ok/total *count* per file, so any retry had to redo the
    *entire* file (60 tiles + a fresh /minmax round-trip) to fix even a
    single missing tile -- correct but wasteful, and it doesn't
    naturally converge: each retry pass re-creates the same
    memory-pressure conditions that caused misses in the first place,
    just on a shrinking file list. Tracking the *exact URLs* instead
    means a retry only re-attempts what's actually still missing, so
    the cost of convergence shrinks every run instead of staying
    proportional to a whole file's tile count. A file's entry is
    cleared entirely once every one of its tiles is confirmed cached --
    see save_pending().

    Returns:
        Mapping of filename -> list of tile URLs still not confirmed
        cached. Empty dict if `PENDING_FILE` doesn't exist or fails to
        parse.
    """
    if not PENDING_FILE.exists():
        return {}
    try:
        return json.loads(PENDING_FILE.read_text())
    except Exception:
        return {}


def save_pending(pending: dict[str, list[str]]) -> None:
    """Persists the pending-tiles state to `PENDING_FILE` as JSON, dropping
    any file entry whose URL list is now empty.

    Args:
        pending: Mapping of filename -> list of still-missing tile URLs.
    """
    # Drop empty entries -- a fully-converged file shouldn't be carried
    # forward forever, it should just disappear from this file.
    cleaned = {filename: urls for filename, urls in pending.items() if urls}
    PENDING_FILE.write_text(json.dumps(cleaned))


def js_number(x: float) -> str:
    """Matches JavaScript's default number-to-string formatting (what
    the frontend does interpolating vmin/vmax into a URL) -- whole
    numbers print without a trailing '.0' (JS: String(-2) -> "-2", not
    Python's f"{-2.0}" -> "-2.0"). A real vmin/vmax rounding to exactly
    a whole number is unlikely but not impossible; this exists so that
    edge case still produces a byte-identical URL instead of a silent
    cache-key mismatch.

    Args:
        x: The number to format.

    Returns:
        `x` formatted the way JavaScript's default `String()` conversion
        would.
    """
    return str(int(x)) if x == int(x) else str(x)


def iso_time_for_filename(filename: str) -> str | None:
    """Parses a `tiera_<YYYYMMDD>T<HHMMSS>.nc` filename into the ISO
    timestamp string geosat-mosaic-api's `/minmax` endpoint expects.

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


def fetch_minmax(band: str, iso_time: str) -> tuple[float, float] | None:
    """Computed min/max for this band+timestamp, from
    geosat-mosaic-api's /minmax -- the exact same endpoint (and cache)
    the frontend calls, so the value this returns is what a live
    session would actually get. Rounded to 1 decimal place to match
    Globe.tsx's own `Math.round(min * 10) / 10` -- matches
    access-log values (e.g. `vmax=400.1`, `vmax=410.9`), this
    rounding is what produces the exact query-string value actual traffic
    sends, not just an equivalent number.

    Args:
        band: Band name, e.g. `"mir"`.
        iso_time: ISO 8601 timestamp string for the target file, as
            produced by `iso_time_for_filename`.

    Returns:
        `(vmin, vmax)` tuple, rounded to 1 decimal place, or None if the
        `/minmax` request failed for any reason.
    """
    url = f"{METADATA_API_BASE}/minmax?band={band}&time={iso_time}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        vmin = round(data["min"] * 10) / 10
        vmax = round(data["max"] * 10) / 10
        return vmin, vmax
    except Exception:
        return None


def dataset_ids_known_to_xcube() -> set[str] | None:
    """Fetches the set of dataset ids xcube currently exposes, via its own
    `/datasets` endpoint (through the nginx proxy).

    Returns:
        Set of dataset ids (e.g. `"mosaics~tiera_20260811T042508.nc"`), or
        None if the request failed for any reason (see inline comment for
        why failures are swallowed here rather than propagated).
    """
    # 10s -> 150s 2026-08-16: /datasets enumerates the whole catalog, and
    # after the dataset_cache_patch.py LRU bound (maxsize=40) shipped the
    # same day, a full-catalog scan can no longer keep every dataset
    # resident at once -- this can now genuinely take
    # ~90s (thrashes the 40-slot cache internally across 213 datasets),
    # not a hang.
    #
    # Returns None (was: let the exception propagate) on any failure --
    # 2026-08-17: a freshly-restarted xcube (e.g. right
    # after the memory watchdog cycles it) can transiently answer
    # /datasets with an HTTP 500 while still initializing, which
    # crashed the *entire* run uncaught, landing the systemd service in
    # `failed` state until the next timer tick. That's a worse outcome
    # than just skipping this one cycle: new/changed/retry files simply
    # get picked up again next run (nothing here is lost, `seen`/`pending`
    # for them hasn't been touched yet at the point this is called).
    try:
        req = urllib.request.Request(f"{PROXY_BASE}/datasets")
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=150) as resp:
            data = json.load(resp)
        return {d["id"] for d in data["datasets"]}
    except Exception as exc:
        log(f"SKIP CYCLE: /datasets failed ({exc}) -- will retry next run")
        return None


def value_ranges_for(filename: str) -> dict[str, tuple[float, float] | None]:
    """Per-band vmin/vmax for one file (one /minmax call per band --
    the range is the same across every zoom level/tile/x/y for a given
    band+timestamp, no need to refetch it 5 times for z=0-1's 1+4 tiles).
    Split out from tile_urls_for so multiple files' minmax stage can run
    concurrently (MINMAX_CONCURRENCY) -- this only ever talks to
    geosat-mosaic-api, never xcube, so it's exempt from the tile-fetch
    stage's tighter concurrency limit (see that constant's comment).

    Args:
        filename: Mosaic filename, e.g. `"tiera_20260811T042508.nc"`.

    Returns:
        Mapping of band -> `(vmin, vmax)` tuple (or None on failure), one
        entry per band in `BANDS`. Empty dict if `filename` doesn't match
        the expected timestamp pattern.
    """
    iso_time = iso_time_for_filename(filename)
    if iso_time is None:
        return {}
    return {band: fetch_minmax(band, iso_time) for band in BANDS}


def tile_urls_for(dataset_id: str, value_ranges: dict[str, tuple[float, float] | None]):
    """Builds the full list of tile URLs to pre-warm for one dataset,
    across every band/variable-type/zoom-level/x/y combination, with
    query strings matching the params live frontend traffic sends
    (see VALUE_CMAP/SATELLITE_ID_PARAMS/PIXEL_RESOLUTION_HAS_NO_KNOWN_PARAMS
    above for the per-variable-type scheme).

    Args:
        dataset_id: xcube dataset id for this file, e.g.
            `"mosaics~tiera_20260811T042508.nc"`.
        value_ranges: Per-band `(vmin, vmax)` tuples (or None), as
            returned by `value_ranges_for`, used to build the `value_*`
            variables' query strings.

    Yields:
        Full tile URLs (proxy base + path + query string) to fetch.
    """
    for band in BANDS:
        for var_type in VAR_TYPES:
            var = f"{var_type}_{band}"

            if var_type == "value":
                band_range = value_ranges.get(band)
                if band_range is None:
                    # /minmax failed or filename didn't parse -- fall back
                    # to the old bare URL rather than skip this variable
                    # entirely; still won't match real traffic, but no
                    # worse than before this fix.
                    query = ""
                else:
                    vmin, vmax = band_range
                    query = (
                        f"?crs=CRS84&format=png&cmap={VALUE_CMAP}"
                        f"&vmin={js_number(vmin)}&vmax={js_number(vmax)}&norm=lin"
                    )
            elif var_type == "satellite_id":
                query = f"?{SATELLITE_ID_PARAMS}"
            else:
                # pixel_resolution_km_<band> -- see
                # PIXEL_RESOLUTION_HAS_NO_KNOWN_PARAMS above.
                query = ""

            for z in ZOOM_LEVELS:
                n = 2**z
                for y in range(n):
                    for x in range(n):
                        yield f"{PROXY_BASE}/tiles/{dataset_id}/{var}/{z}/{y}/{x}{query}"


def fetch(url: str) -> tuple[str, bool]:
    """Fetches one tile URL through the nginx proxy, to populate its cache
    entry.

    Args:
        url: Full tile URL to request.

    Returns:
        `(url, success)` tuple, where `success` is True only on an HTTP
        200 response; any exception (timeout, connection error, non-200)
        is treated as a failure rather than propagated.
    """
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=15) as resp:
            return url, resp.status == 200
    except Exception:
        return url, False


def purge_cache_for(filename: str) -> int:
    """Deletes every nginx cache file whose stored request references
    this filename. Stock nginx (the apt package, no cache_purge module /
    not nginx-plus) has no built-in way to invalidate a specific cache
    key -- but each cached response's file on disk stores its original
    request in plaintext (`KEY: <url>`), so a
    recursive grep for the filename reliably finds every tile/variable/
    zoom-level combination cached for it, regardless of which hashed
    subdirectory nginx filed it under. Needs sudo -- cache files are
    owned by www-data, mode not world-readable.

    Args:
        filename: Mosaic filename to purge cache entries for, e.g.
            `"tiera_20260811T042508.nc"`.

    Returns:
        Number of cache files actually removed. 0 if the grep found no
        matches, or if the grep itself failed.
    """
    try:
        result = subprocess.run(
            ["sudo", "grep", "-arl", filename, str(CACHE_DIR)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        log(f"PURGE FAILED for {filename}: grep error: {exc}")
        return 0

    matched = [line for line in result.stdout.splitlines() if line.strip()]
    removed = 0
    for path in matched:
        try:
            subprocess.run(["sudo", "rm", "-f", path], check=True, timeout=10)
            removed += 1
        except Exception as exc:
            log(f"PURGE FAILED to remove {path}: {exc}")
    return removed


def main() -> None:
    """Entry point: one pre-warm cycle.

    Diffs the on-disk mosaic files against the previously-recorded
    `seen`/`pending` state to determine new, removed, changed
    (reprocessed), and retry-needed files; purges stale nginx cache
    entries for removed/changed files; then fetches minmax ranges (stage
    1) and tile URLs (stage 2, shared `TILE_FETCH_CONCURRENCY`-wide pool)
    for whatever needs warming, saving `seen`/`pending` state
    incrementally as each file's tiles finish. See the module docstring
    and the individual constants' comments for the full design rationale.
    """
    seen = load_seen()
    pending = load_pending()
    on_disk = {p.name: p.stat().st_mtime for p in DATA_DIR.glob("*.nc")}

    new_files = sorted(set(on_disk) - set(seen))
    removed_files = sorted(set(seen) - set(on_disk))
    changed_files = sorted(
        f
        for f in (set(on_disk) & set(seen))
        if on_disk[f] > seen[f] + MTIME_TOLERANCE_SECONDS
    )
    # Files with specific tiles still known-missing from a previous run --
    # see load_pending()'s docstring. Excludes anything already getting a
    # full rebuild below (new/changed), since that produces a fresh URL
    # list anyway, and anything that's left the rolling window (removed_files
    # already purges + drops these below).
    full_rebuild_set = set(new_files) | set(changed_files)
    retry_files = sorted(
        f for f in pending if pending[f] and f in on_disk and f not in full_rebuild_set
    )

    if not (new_files or removed_files or changed_files or retry_files):
        return

    for filename in removed_files:
        removed_count = purge_cache_for(filename)
        log(f"PURGED (left rolling window) {filename}: {removed_count} cache entries")
        del seen[filename]
        pending.pop(filename, None)

    for filename in changed_files:
        removed_count = purge_cache_for(filename)
        log(f"PURGED (reprocessed, mtime changed) {filename}: {removed_count} cache entries")
        # Any previously-recorded missing-tile list is for the *old*
        # content (possibly even different vmin/vmax) -- moot now that
        # this file gets a full fresh URL list below.
        pending.pop(filename, None)

    full_rebuild = new_files + changed_files
    if full_rebuild or retry_files:
        # retry_files don't need /datasets at all -- their URLs are
        # already known-good from a previous run, no dataset-visibility
        # check required. Only call it (and only let its failure affect
        # anything) when there's actually a full_rebuild file that needs
        # it, so a transient /datasets problem never blocks retry
        # progress, and doesn't get looked up needlessly on a
        # retry-only cycle either.
        rebuild_warmable = []
        if full_rebuild:
            known_ids = dataset_ids_known_to_xcube()
            if known_ids is None:
                log(f"SKIP {len(full_rebuild)} new/changed file(s) this cycle: /datasets unavailable, will retry next run")
            else:
                for filename in full_rebuild:
                    dataset_id = f"{STORE_ID}~{filename}"
                    if dataset_id not in known_ids:
                        # xcube hasn't picked this file up yet (e.g. landed
                        # since its last dataset-store rescan) -- skip for
                        # now, retry next run rather than record its mtime
                        # as seen. For a reprocessed file this also
                        # correctly leaves its (already-purged) cache cold
                        # until xcube catches up, rather than serving stale
                        # data in the meantime.
                        log(f"SKIP {filename}: not yet visible to xcube as {dataset_id}")
                        continue
                    rebuild_warmable.append((filename, dataset_id))
        rebuild_names = {f for f, _ in rebuild_warmable}

        file_urls: dict[str, list[str]] = {}

        if rebuild_warmable:
            # Stage 1: every file's minmax fetch, concurrently -- see
            # MINMAX_CONCURRENCY's comment for why this is safe at full
            # concurrency (a different, scalable service than xcube).
            with concurrent.futures.ThreadPoolExecutor(max_workers=MINMAX_CONCURRENCY) as pool:
                ranges_by_file = dict(
                    zip(
                        (f for f, _ in rebuild_warmable),
                        pool.map(value_ranges_for, (f for f, _ in rebuild_warmable)),
                    )
                )
            for filename, dataset_id in rebuild_warmable:
                file_urls[filename] = list(tile_urls_for(dataset_id, ranges_by_file[filename]))

        # Retry files already have their exact missing URLs recorded from
        # last time -- no need to touch xcube's dataset-store or /minmax
        # again, just re-attempt exactly what failed last run.
        for filename in retry_files:
            file_urls[filename] = pending[filename]

        if file_urls:
            # Stage 2: every tile from every file, through ONE shared pool
            # capped at TILE_FETCH_CONCURRENCY -- see that constant's
            # comment for why this must stay at exactly the number already
            # proven safe against xcube, regardless of how many files are
            # in flight. `counts`/`failed_urls` are only ever mutated from
            # this single (main) thread as futures complete via
            # as_completed(), so no lock is needed. Logging/seen-pending
            # update/save happen as soon as a file's *last* attempted tile
            # finishes (not all at the end), matching prewarm_minmax.py's
            # crash-resilience pattern -- if this script gets killed
            # mid-run, already-finished files stay recorded instead of
            # re-attempting everything from scratch.
            counts = {filename: [0, 0, len(urls)] for filename, urls in file_urls.items()}
            failed_urls: dict[str, list[str]] = {filename: [] for filename in file_urls}
            with concurrent.futures.ThreadPoolExecutor(max_workers=TILE_FETCH_CONCURRENCY) as pool:
                futures = {
                    pool.submit(fetch, url): filename
                    for filename, urls in file_urls.items()
                    for url in urls
                }
                for future in concurrent.futures.as_completed(futures):
                    filename = futures[future]
                    url, success = future.result()
                    c = counts[filename]
                    c[1] += 1
                    if success:
                        c[0] += 1
                    else:
                        failed_urls[filename].append(url)
                    if c[1] == c[2]:
                        ok, _done, total = c
                        is_retry = filename not in rebuild_names
                        label = "RETRIED" if is_retry else "WARMED"
                        still_missing = len(failed_urls[filename])
                        suffix = f" ({still_missing} still missing)" if still_missing else ""
                        log(f"{label} {filename}: {ok}/{total} tiles cached{suffix}")
                        pending[filename] = failed_urls[filename]
                        if filename in rebuild_names:
                            # mtime recorded now regardless of tile
                            # completeness -- seen/pending are deliberately
                            # separate concerns, see load_pending()'s
                            # docstring.
                            seen[filename] = on_disk[filename]
                        save_seen(seen)
                        save_pending(pending)

    # Always saved once more here too (cheap, idempotent) -- covers
    # removed_files' deletions on a run where nothing ended up attempted
    # (e.g. every full_rebuild file was still SKIPped as not-yet-visible
    # to xcube), which the per-completion save above wouldn't otherwise
    # reach.
    save_seen(seen)
    save_pending(pending)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Backstop, added 2026-08-17 after an unguarded /datasets error
        # (see dataset_ids_known_to_xcube()) crashed a run uncaught,
        # landing the systemd oneshot in `failed` state until the next
        # timer tick. This is a periodic background job -- any single run
        # failing should degrade to "try again next cycle," not require a
        # human to notice `systemctl status` says failed. Logs the full
        # traceback (to LOG_FILE, same as everything else) rather than
        # swallowing it silently, and still exits non-zero so systemd's
        # own failure accounting isn't lied to.
        import traceback
        log("RUN FAILED (uncaught exception):\n" + traceback.format_exc())
        raise
